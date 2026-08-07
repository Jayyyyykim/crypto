import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import expected_range as er
from helpers import make_df, flat_bars


class TestQuantile(unittest.TestCase):
    def test_endpoints(self):
        v = [1.0, 2.0, 3.0, 4.0]
        self.assertEqual(er._quantile(v, 0), 1.0)
        self.assertEqual(er._quantile(v, 1), 4.0)

    def test_interpolates(self):
        # 0.5 분위 = 2와 3 사이 중간
        self.assertAlmostEqual(er._quantile([1.0, 2.0, 3.0, 4.0], 0.5), 2.5)

    def test_empty(self):
        self.assertEqual(er._quantile([], 0.5), 0.0)


class TestExcursions(unittest.TestCase):
    def test_gap_up_clamps_downside_to_zero(self):
        """봉 전체가 직전 종가 위에 있으면 아래 이탈폭은 음수가 아니라 0이다."""
        highs = [100, 120]
        lows = [99, 110]
        closes = [100, 115]
        ups, dns = er._excursions(highs, lows, closes, 100)
        self.assertAlmostEqual(ups[0], 0.20)   # (120-100)/100
        self.assertEqual(dns[0], 0.0)          # 아래로는 안 갔다


class TestExpectedRange(unittest.TestCase):
    def test_returns_none_on_short_history(self):
        df = make_df(flat_bars(10))
        self.assertIsNone(er.expected_range(df))

    def test_asymmetric_band(self):
        """아래 꼬리가 길면 하단이 상단보다 멀어야 한다 (대칭 ATR과의 차이)."""
        bars = []
        px = 100.0
        for i in range(100):
            # 위로는 0.5%, 아래로는 3% 꼬리
            bars.append((px, px * 1.005, px * 0.97, px))
        df = make_df(bars)
        r = er.expected_range(df, q=0.7)
        self.assertIsNotNone(r)
        up_room = r["upper"] - r["base"]
        dn_room = r["base"] - r["lower"]
        self.assertGreater(dn_room, up_room * 3)

    def test_position_and_zone(self):
        bars = [(100.0, 102.0, 98.0, 100.0) for _ in range(100)]
        df = make_df(bars)
        r = er.expected_range(df, live_price=100.0)
        self.assertAlmostEqual(r["position"], 0.5, places=1)
        self.assertEqual(r["zone"], "중간")

        r_top = er.expected_range(df, live_price=r["upper"])
        self.assertEqual(r_top["zone"], "상단")
        r_bot = er.expected_range(df, live_price=r["lower"])
        self.assertEqual(r_bot["zone"], "하단")

    def test_out_of_range_flagged(self):
        bars = [(100.0, 102.0, 98.0, 100.0) for _ in range(100)]
        df = make_df(bars)
        r = er.expected_range(df, live_price=500.0)
        self.assertTrue(r["out_of_range"])
        self.assertEqual(r["zone"], "상단이탈")
        self.assertEqual(r["position"], 1.0)   # 클립됨
        self.assertGreater(r["raw_position"], 1.0)   # 원본은 보존


class TestCoverage(unittest.TestCase):
    def test_coverage_near_nominal_on_stationary_series(self):
        """변동성이 일정한 계열이면 실측 커버리지가 명목치 근처여야 한다."""
        bars = []
        for i in range(300):
            amp = 1.0 if i % 3 else 2.0     # 결정적이지만 폭이 두 가지
            bars.append((100.0, 100 + amp, 100 - amp, 100.0))
        df = make_df(bars)
        cov = er.coverage(df, q=0.9)
        self.assertIsNotNone(cov)
        self.assertGreater(cov["actual"], 0.5)

    def test_flags_too_tight_band(self):
        """변동성이 뒤에서 폭발하면 밴드가 좁다고 표시돼야 한다."""
        bars = [(100.0, 100.5, 99.5, 100.0) for _ in range(120)]
        px = 100.0
        for i in range(80):
            bars.append((px, px * 1.15, px * 0.85, px))
        df = make_df(bars)
        cov = er.coverage(df, q=0.7)
        self.assertIsNotNone(cov)
        self.assertTrue(cov["band_too_tight"])


class TestTradeLevelCheck(unittest.TestCase):
    def setUp(self):
        bars = [(100.0, 103.0, 97.0, 100.0) for _ in range(100)]
        self.er = er.expected_range(make_df(bars), live_price=100.0)

    def test_warns_on_too_tight_stop(self):
        # 예상 하단이 3% 근처인데 손절을 0.5%로 두면 노이즈에 털린다
        w = er.check_trade_levels(self.er, entry=100.0, sl=99.5, tp1=105.0)
        self.assertTrue(any("손절" in x for x in w))

    def test_warns_on_far_target(self):
        w = er.check_trade_levels(self.er, entry=100.0, sl=95.0, tp1=130.0)
        self.assertTrue(any("TP1" in x for x in w))

    def test_no_warning_on_sane_levels(self):
        w = er.check_trade_levels(self.er, entry=100.0, sl=96.5, tp1=104.0)
        self.assertEqual(w, [])


class TestRank(unittest.TestCase):
    def test_orders(self):
        rows = [
            {"width_pct": 0.10, "raw_position": 0.9, "coin": "A"},
            {"width_pct": 0.30, "raw_position": 0.1, "coin": "B"},
        ]
        self.assertEqual(er.rank(rows, "wide")[0]["coin"], "B")
        self.assertEqual(er.rank(rows, "narrow")[0]["coin"], "A")
        self.assertEqual(er.rank(rows, "top")[0]["coin"], "A")
        self.assertEqual(er.rank(rows, "bottom")[0]["coin"], "B")


if __name__ == "__main__":
    unittest.main()
