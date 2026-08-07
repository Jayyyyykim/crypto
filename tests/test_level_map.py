import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import level_map as lm
from helpers import make_df


class TestPivots(unittest.TestCase):
    def test_finds_obvious_swing(self):
        # 인덱스 5에 뾰족한 고점
        highs = [10, 11, 12, 13, 14, 30, 14, 13, 12, 11, 10]
        lows = [h - 2 for h in highs]
        sw_h, sw_l = lm.find_pivots(highs, lows, width=3)
        self.assertIn(5, [i for i, _ in sw_h])

    def test_excludes_unconfirmed_edge(self):
        """마지막 width개 봉은 오른쪽이 안 채워져 스윙으로 확정 못 한다."""
        highs = [10, 11, 12, 13, 14, 15, 16, 17, 99]
        lows = [h - 1 for h in highs]
        sw_h, _ = lm.find_pivots(highs, lows, width=3)
        self.assertNotIn(8, [i for i, _ in sw_h])


class TestCluster(unittest.TestCase):
    def test_merges_nearby_prices(self):
        pivots = [(1, 100.0), (5, 100.2), (9, 100.4), (20, 150.0)]
        levels = lm.cluster_levels(pivots, total_bars=30, tol=0.006)
        self.assertEqual(len(levels), 2)
        big = [l for l in levels if l["price"] < 120][0]
        self.assertEqual(big["touches"], 3)

    def test_recency_boosts_strength(self):
        old = lm.cluster_levels([(1, 100.0), (2, 100.1)], total_bars=100)[0]
        new = lm.cluster_levels([(98, 100.0), (99, 100.1)], total_bars=100)[0]
        self.assertGreater(new["strength"], old["strength"])

    def test_empty(self):
        self.assertEqual(lm.cluster_levels([], 100), [])


class TestLevelMap(unittest.TestCase):
    def _range_df(self):
        """80~120 사이를 여러 번 오간 계열 — 양 끝이 반복 터치된 레벨이 된다."""
        bars = []
        for cycle in range(8):
            for px in (100, 110, 120, 110, 100, 90, 80, 90):
                bars.append((px, px + 1, px - 1, px))
        return make_df(bars)

    def test_none_on_short_history(self):
        df = make_df([(100, 101, 99, 100)] * 10)
        self.assertIsNone(lm.build_level_map(df))

    def test_supports_below_resistances_above(self):
        r = lm.build_level_map(self._range_df(), price=100.0)
        self.assertIsNotNone(r)
        for s in r["supports"]:
            self.assertLess(s["price"], 100.0)
        for x in r["resistances"]:
            self.assertGreater(x["price"], 100.0)

    def test_ranked_by_distance_not_strength(self):
        """1차는 가장 가까운 벽이다 — 가격은 가까운 것부터 부딪힌다."""
        r = lm.build_level_map(self._range_df(), price=100.0)
        sup = r["supports"]
        self.assertTrue(all(sup[i]["distance"] <= sup[i + 1]["distance"]
                            for i in range(len(sup) - 1)))
        res = r["resistances"]
        self.assertTrue(all(res[i]["distance"] <= res[i + 1]["distance"]
                            for i in range(len(res) - 1)))

    def test_depth_capped_at_three(self):
        r = lm.build_level_map(self._range_df(), price=100.0)
        self.assertLessEqual(len(r["supports"]), 3)
        self.assertLessEqual(len(r["resistances"]), 3)

    def test_nearest_picks_closer_side(self):
        r = lm.build_level_map(self._range_df(), price=118.0)
        self.assertIsNotNone(r["nearest"])
        # 118은 120 저항에 가깝다
        self.assertEqual(r["nearest"]["side"], "resistance")

    def test_touch_counts_accumulate(self):
        """여러 번 닿은 자리는 터치 횟수가 1보다 커야 한다."""
        r = lm.build_level_map(self._range_df(), price=100.0)
        all_levels = r["supports"] + r["resistances"]
        self.assertTrue(any(l["touches"] > 1 for l in all_levels))


class TestInvalidation(unittest.TestCase):
    def test_up_read_uses_last_swing_low(self):
        inv = lm.invalidation_price(
            swing_highs=[(5, 120.0)], swing_lows=[(8, 90.0)], price=100.0)
        self.assertEqual(inv["direction"], "up")
        self.assertEqual(inv["price"], 90.0)
        self.assertAlmostEqual(inv["distance"], 0.10)

    def test_down_read_uses_last_swing_high(self):
        inv = lm.invalidation_price(
            swing_highs=[(5, 120.0)], swing_lows=[(8, 90.0)], price=80.0)
        self.assertEqual(inv["direction"], "down")
        self.assertEqual(inv["price"], 120.0)

    def test_none_when_no_pivots(self):
        self.assertIsNone(lm.invalidation_price([], [], 100.0))


class TestRadar(unittest.TestCase):
    def test_sorts_by_distance_and_filters_far(self):
        bars = []
        for cycle in range(8):
            for px in (100, 110, 120, 110, 100, 90, 80, 90):
                bars.append((px, px + 1, px - 1, px))
        df = make_df(bars)

        def get_ohlcv(symbol, tf, limit=None):
            return df

        prices = {"A/USDT": 119.5, "B/USDT": 105.0}
        rows = lm.level_radar(
            ["A/USDT", "B/USDT"], get_ohlcv, timeframes=("1d",),
            get_price_fn=lambda s: prices[s], max_distance=0.05)

        self.assertTrue(rows)
        # 119.5는 120 레벨에 0.4% — 105보다 훨씬 가깝다
        self.assertEqual(rows[0]["coin"], "A")
        self.assertTrue(all(r["distance"] <= 0.05 for r in rows))

    def test_survives_broken_fetch(self):
        def bad(symbol, tf, limit=None):
            raise RuntimeError("boom")
        self.assertEqual(lm.level_radar(["X/USDT"], bad, timeframes=("1d",)), [])


if __name__ == "__main__":
    unittest.main()
