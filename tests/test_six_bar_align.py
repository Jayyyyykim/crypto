import os
import sys
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import six_bar_align as sba
from helpers import make_df


def rising(n, start=100.0, step=1.0):
    return [(start + i * step, start + i * step + 0.5,
             start + i * step - 0.5, start + i * step) for i in range(n)]


def falling(n, start=300.0, step=1.0):
    return [(start - i * step, start - i * step + 0.5,
             start - i * step - 0.5, start - i * step) for i in range(n)]


class TestTrendSeries(unittest.TestCase):
    def test_none_until_enough_bars(self):
        closes = [100.0 + i for i in range(10)]
        s = sba.trend_series(closes, 10, 30)
        self.assertTrue(all(v is None for v in s))

    def test_rising_series_reads_up(self):
        closes = [100.0 + i for i in range(60)]
        self.assertEqual(sba.trend_series(closes, 10, 30)[-1], "up")

    def test_falling_series_reads_down(self):
        closes = [300.0 - i for i in range(60)]
        self.assertEqual(sba.trend_series(closes, 10, 30)[-1], "down")

    def test_prefix_equivalence(self):
        """전체 배열의 i번째 == 앞에서 i+1개만 넣고 계산한 값.

        이 성질이 깨지면 O(n) 최적화가 결과를 바꾼 것이다.
        """
        closes = [100 + (i % 7) * 3 - (i % 11) for i in range(200)]
        full = sba.trend_series(closes, 10, 30)
        for i in (40, 80, 150, 199):
            self.assertEqual(full[i], sba.trend_series(closes[:i + 1], 10, 30)[-1])

    def test_empty(self):
        self.assertEqual(sba.trend_series([], 10, 30), [])


class TestAnalyseTf(unittest.TestCase):
    def test_none_on_short_history(self):
        self.assertIsNone(sba.analyse_tf(make_df(rising(10)), "1d"))

    def test_state_and_bars(self):
        r = sba.analyse_tf(make_df(rising(80)), "1d")
        self.assertEqual(r["state"], "up")
        self.assertEqual(r["bars"], 80)

    def test_flip_is_found_with_timestamp(self):
        """내렸다가 오르면 전환 지점과 시각이 나와야 한다."""
        bars = falling(60, start=300.0) + rising(60, start=240.0, step=2.0)
        r = sba.analyse_tf(make_df(bars), "1d")
        self.assertEqual(r["state"], "up")
        self.assertIsNotNone(r["flipped_at"])
        self.assertGreater(r["flipped_index"], 60)

    def test_monthly_uses_shorter_window(self):
        """월봉에 (10,30)을 쓰면 30봉으로는 판정이 안 된다 — 짧은 창이라 된다."""
        df = make_df(rising(20))
        self.assertIsNone(sba.analyse_tf(df, "1d"))       # slow=30 → 부족
        self.assertIsNotNone(sba.analyse_tf(df, "1M"))    # slow=10 → 가능


class TestBuildRow(unittest.TestCase):
    def _cells(self, states):
        return {tf: ({"state": s, "flipped_at": None, "flipped_index": None,
                      "bars": 100} if s else None)
                for tf, s in zip(sba.SIX_TFS, states)}

    def test_six_up_is_aligned(self):
        row = sba.build_row("A/USDT", self._cells(["up"] * 6))
        self.assertEqual(row["aligned"], "up")
        self.assertEqual(row["votes"], 6)
        self.assertEqual(row["counted"], 6)
        self.assertEqual(row["score"], 6)

    def test_four_of_six_is_aligned(self):
        row = sba.build_row("A/USDT",
                            self._cells(["up", "up", "up", "up", "down", "down"]))
        self.assertEqual(row["aligned"], "up")
        self.assertEqual(row["votes"], 4)

    def test_three_of_six_is_not_aligned(self):
        row = sba.build_row("A/USDT",
                            self._cells(["up", "up", "up", "down", "down", "down"]))
        self.assertIsNone(row["aligned"])

    def test_uncounted_bars_excluded(self):
        row = sba.build_row("A/USDT",
                            self._cells(["up", "up", "up", "up", None, None]))
        self.assertEqual(row["counted"], 4)
        self.assertEqual(row["aligned"], "up")

    def test_label_shows_ratio(self):
        row = sba.build_row("A/USDT",
                            self._cells(["down"] * 5 + ["up"]))
        self.assertIn("5/6봉 정렬", sba.label(row))
        self.assertIn("하락", sba.label(row))

    def test_label_when_split(self):
        row = sba.build_row("A/USDT",
                            self._cells(["up", "up", "up", "down", "down", "down"]))
        self.assertEqual(sba.label(row), "방향 갈림")


class TestFlipsAndOverlap(unittest.TestCase):
    def _row(self, aligned_states, flips):
        cells = {}
        for tf, s in zip(sba.SIX_TFS, aligned_states):
            cells[tf] = {"state": s, "flipped_at": flips.get(tf),
                         "flipped_index": 1 if flips.get(tf) else None,
                         "bars": 100} if s else None
        return sba.build_row("A/USDT", cells)

    def test_recent_flip_within_window(self):
        row = self._row(["up"] * 6, {"1d": "2026-08-05 00:00"})
        now = datetime(2026, 8, 7)
        self.assertEqual(len(sba.recent_flips(row, 7, now)), 1)

    def test_old_flip_excluded(self):
        row = self._row(["up"] * 6, {"1d": "2026-06-01 00:00"})
        now = datetime(2026, 8, 7)
        self.assertEqual(sba.recent_flips(row, 7, now), [])

    def test_flips_sorted_longest_bar_first(self):
        row = self._row(["up"] * 6,
                        {"4h": "2026-08-06 00:00", "1w": "2026-08-05 00:00"})
        flips = sba.recent_flips(row, 7, datetime(2026, 8, 7))
        self.assertEqual(flips[0]["tf"], "1w")

    def test_overlap_requires_alignment_and_flip(self):
        aligned_recent = self._row(["up"] * 6, {"1d": "2026-08-05 00:00"})
        aligned_old = self._row(["up"] * 6, {"1d": "2026-01-01 00:00"})
        split = self._row(["up", "up", "up", "down", "down", "down"],
                          {"1d": "2026-08-05 00:00"})
        now = datetime(2026, 8, 7)
        self.assertTrue(sba.is_overlap(aligned_recent, 7, now))
        self.assertFalse(sba.is_overlap(aligned_old, 7, now))
        self.assertFalse(sba.is_overlap(split, 7, now))

    def test_flip_in_opposite_direction_is_not_overlap(self):
        """정렬은 상승인데 하락으로 전환한 봉만 있으면 겹침이 아니다."""
        cells = {tf: {"state": "up", "flipped_at": None,
                      "flipped_index": None, "bars": 100} for tf in sba.SIX_TFS}
        cells["4h"] = {"state": "down", "flipped_at": "2026-08-06 00:00",
                       "flipped_index": 90, "bars": 100}
        row = sba.build_row("A/USDT", cells)
        self.assertEqual(row["aligned"], "up")
        self.assertFalse(sba.is_overlap(row, 7, datetime(2026, 8, 7)))


class TestUniverse(unittest.TestCase):
    def _fetch(self, mapping):
        def get_ohlcv(symbol, tf, limit=None):
            return make_df(mapping[symbol])
        return get_ohlcv

    def test_scan_and_summarize(self):
        fetch = self._fetch({"A/USDT": rising(200), "B/USDT": falling(200)})
        rows = sba.scan_universe(["A/USDT", "B/USDT"], fetch)
        self.assertEqual(len(rows), 2)
        s = sba.summarize(rows)
        self.assertEqual(s["watched"], 2)
        self.assertEqual(s["up_aligned"], 1)
        self.assertEqual(s["down_aligned"], 1)
        self.assertAlmostEqual(s["up_ratio"], 0.5)

    def test_scan_survives_broken_fetch(self):
        def bad(symbol, tf, limit=None):
            raise RuntimeError("boom")
        self.assertEqual(sba.scan_universe(["X/USDT"], bad), [])

    def test_direction_matrix_shape(self):
        fetch = self._fetch({"A/USDT": rising(200)})
        rows = sba.scan_universe(["A/USDT"], fetch)
        m = sba.direction_matrix(rows)
        self.assertEqual(len(m["header"]), 6)
        self.assertEqual(len(m["rows"][0]["cells"]), 6)
        self.assertTrue(all(c in ("▲", "▼", "–") for c in m["rows"][0]["cells"]))

    def test_reports_render(self):
        fetch = self._fetch({"A/USDT": rising(200), "B/USDT": falling(200)})
        rows = sba.scan_universe(["A/USDT", "B/USDT"], fetch)
        self.assertIn("여섯 봉 정렬", sba.get_report(rows))
        self.assertIn("봉 방향 그리드", sba.get_matrix_report(rows))

    def test_report_handles_empty(self):
        self.assertIn("없어요", sba.get_report([]))


if __name__ == "__main__":
    unittest.main()
