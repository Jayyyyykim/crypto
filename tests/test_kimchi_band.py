import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kimchi_band as kb


class BandCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "k.json")

    def tearDown(self):
        self.dir.cleanup()

    def fill(self, values, end_days_ago=0):
        """오늘부터 거슬러 올라가며 값을 채운다 (마지막이 가장 최근)."""
        n = len(values)
        for i, v in enumerate(values):
            d = (datetime.now() - timedelta(days=n - 1 - i + end_days_ago)
                 ).strftime("%Y-%m-%d")
            kb.record(v, date=d, path=self.path)


class TestPremium(unittest.TestCase):
    def test_zero_when_fair(self):
        self.assertAlmostEqual(kb.premium(1_400_000, 1000, 1400), 0.0)

    def test_positive_and_negative(self):
        self.assertAlmostEqual(kb.premium(1_414_000, 1000, 1400), 1.0)
        self.assertAlmostEqual(kb.premium(1_386_000, 1000, 1400), -1.0)

    def test_none_on_bad_input(self):
        self.assertIsNone(kb.premium(1_400_000, 0, 1400))
        self.assertIsNone(kb.premium(1_400_000, 1000, 0))


class TestRecord(BandCase):
    def test_one_point_per_day(self):
        kb.record(1.0, date="2026-08-07", path=self.path)
        kb.record(2.0, date="2026-08-07", path=self.path)
        pts = kb.series(365, self.path)
        self.assertEqual(len([p for p in pts if p[0] == "2026-08-07"]), 1)

    def test_latest_write_wins(self):
        kb.record(1.0, date="2026-08-07", path=self.path)
        kb.record(2.0, date="2026-08-07", path=self.path)
        hist = kb.jsonstore.load(self.path, default={})
        self.assertEqual(hist["2026-08-07"]["value"], 2.0)

    def test_none_is_ignored(self):
        self.assertIsNone(kb.record(None, path=self.path))
        self.assertEqual(kb.series(365, self.path), [])

    def test_series_is_date_sorted(self):
        for d in ("2026-08-05", "2026-08-03", "2026-08-04"):
            kb.record(1.0, date=d, path=self.path)
        pts = kb.series(365, self.path)
        self.assertEqual([d for d, _ in pts], ["2026-08-03", "2026-08-04", "2026-08-05"])


class TestBand(BandCase):
    def test_none_below_min_samples(self):
        self.fill([0.1] * 5)
        self.assertIsNone(kb.band(path=self.path))

    def test_position_at_bottom_and_top(self):
        self.fill([float(i) for i in range(10)])       # 0.0 ~ 9.0
        low = kb.band(current=0.0, path=self.path)
        high = kb.band(current=9.0, path=self.path)
        self.assertAlmostEqual(low["position"], 0.0)
        self.assertAlmostEqual(high["position"], 1.0)
        self.assertEqual(low["zone"], "하단")
        self.assertEqual(high["zone"], "상단")

    def test_middle_zone(self):
        self.fill([float(i) for i in range(10)])
        b = kb.band(current=4.5, path=self.path)
        self.assertEqual(b["zone"], "가운데")
        self.assertAlmostEqual(b["position"], 0.5)

    def test_same_number_different_meaning(self):
        """같은 -0.25%가 어떤 달엔 상단, 어떤 달엔 하단이다 — 이 모듈의 존재 이유."""
        cheap_month = [-3.0, -2.5, -2.0, -1.8, -1.5, -1.2, -1.0, -0.8, -0.6, -0.5]
        rich_month = [2.0, 1.5, 1.2, 1.0, 0.8, 0.5, 0.2, 0.0, -0.2, -0.3]

        self.fill(cheap_month)
        b1 = kb.band(current=-0.25, path=self.path)

        self.dir.cleanup()
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "k.json")
        self.fill(rich_month)
        b2 = kb.band(current=-0.25, path=self.path)

        self.assertEqual(b1["zone"], "상단")
        self.assertEqual(b2["zone"], "하단")

    def test_out_of_range_flagged(self):
        self.fill([float(i) for i in range(10)])
        b = kb.band(current=99.0, path=self.path)
        self.assertTrue(b["out_of_range"])
        self.assertIn("벗어났습니다", kb.read_as(b))

    def test_percentile_counts_days_below(self):
        self.fill([float(i) for i in range(10)])       # 0..9
        b = kb.band(current=5.0, path=self.path)
        self.assertAlmostEqual(b["percentile"], 0.5)

    def test_flat_history_does_not_divide_by_zero(self):
        self.fill([1.0] * 12)
        b = kb.band(current=1.0, path=self.path)
        self.assertAlmostEqual(b["position"], 0.5)

    def test_defaults_to_last_recorded(self):
        self.fill([float(i) for i in range(10)])
        self.assertAlmostEqual(kb.band(path=self.path)["current"], 9.0)

    def test_window_excludes_old_points(self):
        self.fill([float(i) for i in range(10)], end_days_ago=90)   # 전부 창 밖
        self.assertIsNone(kb.band(window_days=30, path=self.path))


class TestReport(BandCase):
    def test_report_asks_for_more_data_when_short(self):
        self.fill([0.1] * 3)
        r = kb.get_report(path=self.path)
        self.assertIn("기록이", r)
        self.assertIn("record", r)

    def test_report_renders_with_data(self):
        self.fill([float(i) for i in range(15)])
        r = kb.get_report(current=7.0, path=self.path)
        self.assertIn("김프 위치", r)
        self.assertIn("최저", r)
        self.assertIn("차익이 되지 않습니다", r)

    def test_read_as_handles_none(self):
        self.assertIn("표본이 모자라", kb.read_as(None))


if __name__ == "__main__":
    unittest.main()
