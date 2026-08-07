import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import setup_ledger as sl
from helpers import make_df


class TestDetect(unittest.TestCase):
    def _flat_base(self, n=25, px=100.0):
        """구간 위치가 중간이 되도록 넓은 레인지를 미리 만들어 둔다."""
        highs = [px + 10] * n
        lows = [px - 10] * n
        closes = [px] * n
        return highs, lows, closes

    def test_sweep_up_when_wick_rejected(self):
        h, l, c = self._flat_base()
        h.append(h[-1] + 5)      # 전일 고가 돌파
        l.append(l[-1])
        c.append(c[-1])          # 종가는 전일 고가 아래로 회귀
        evs = sl.detect_at(h, l, c, len(c) - 1)
        types = [e["type"] for e in evs]
        self.assertIn("sweep_up", types)
        self.assertNotIn("break_prev_high", types)
        sweep = [e for e in evs if e["type"] == "sweep_up"][0]
        self.assertEqual(sweep["direction"], "down")

    def test_break_prev_high_when_close_holds(self):
        h, l, c = self._flat_base()
        h.append(h[-1] + 5)
        l.append(l[-1])
        c.append(h[-2] + 2)      # 종가가 전일 고가 위
        evs = sl.detect_at(h, l, c, len(c) - 1)
        types = [e["type"] for e in evs]
        self.assertIn("break_prev_high", types)
        self.assertNotIn("sweep_up", types)

    def test_sweep_down_and_lose_prev_low_are_exclusive(self):
        h, l, c = self._flat_base()
        h.append(h[-1])
        l.append(l[-1] - 5)
        c.append(c[-1])          # 전일 저가 위로 회귀 → sweep_down
        evs = sl.detect_at(h, l, c, len(c) - 1)
        types = [e["type"] for e in evs]
        self.assertIn("sweep_down", types)
        self.assertNotIn("lose_prev_low", types)

        h2, l2, c2 = self._flat_base()
        h2.append(h2[-1])
        l2.append(l2[-1] - 5)
        c2.append(l2[-2] - 2)    # 종가가 전일 저가 아래 → lose_prev_low
        types2 = [e["type"] for e in sl.detect_at(h2, l2, c2, len(c2) - 1)]
        self.assertIn("lose_prev_low", types2)
        self.assertNotIn("sweep_down", types2)

    def test_premium_and_discount_zones(self):
        # 레인지 80~120, 종가 118 → 위치 0.95 = 프리미엄
        h = [120] * 25 + [120]
        l = [80] * 25 + [80]
        c = [100] * 25 + [118]
        types = [e["type"] for e in sl.detect_at(h, l, c, len(c) - 1)]
        self.assertIn("premium_zone", types)

        c2 = [100] * 25 + [82]
        types2 = [e["type"] for e in sl.detect_at(h, l, c2, len(c2) - 1)]
        self.assertIn("discount_zone", types2)

    def test_zone_directions_are_mean_reverting(self):
        self.assertEqual(sl.EVENT_DIRECTION["premium_zone"], "down")
        self.assertEqual(sl.EVENT_DIRECTION["discount_zone"], "up")

    def test_first_bar_yields_nothing(self):
        self.assertEqual(sl.detect_at([1], [1], [1], 0), [])


class TestScoring(unittest.TestCase):
    def test_hit_when_moves_implied_direction(self):
        c = [100.0, 105.0]
        s = sl.score_event([106] * 2, [99] * 2, c, 0, "up", 1)
        self.assertEqual(s["result"], "hit")
        self.assertAlmostEqual(s["move_pct"], 0.05)

    def test_miss_when_moves_against(self):
        c = [100.0, 95.0]
        s = sl.score_event([101] * 2, [94] * 2, c, 0, "up", 1)
        self.assertEqual(s["result"], "miss")

    def test_flat_inside_band(self):
        c = [100.0, 100.5]     # 0.5% < 1% 밴드
        s = sl.score_event([101] * 2, [99] * 2, c, 0, "up", 1)
        self.assertEqual(s["result"], "flat")

    def test_short_direction_inverts(self):
        c = [100.0, 95.0]
        s = sl.score_event([101] * 2, [94] * 2, c, 0, "down", 1)
        self.assertEqual(s["result"], "hit")

    def test_mfe_mae_are_path_based(self):
        """종가는 제자리여도 도중에 크게 흔들렸으면 그게 기록돼야 한다."""
        c = [100.0, 100.0]
        s = sl.score_event([110.0, 110.0], [90.0, 90.0], c, 0, "up", 1,
                           atr_pct=0.05)
        self.assertAlmostEqual(s["fav_pct"], 0.10)
        self.assertAlmostEqual(s["adv_pct"], 0.10)
        self.assertAlmostEqual(s["mfe_atr"], 2.0)
        self.assertAlmostEqual(s["mae_atr"], 2.0)

    def test_none_when_future_missing(self):
        c = [100.0, 101.0]
        self.assertIsNone(sl.score_event([1] * 2, [1] * 2, c, 0, "up", 5))


class TestBaseline(unittest.TestCase):
    def test_requires_minimum_samples(self):
        self.assertIsNone(sl.baseline_rate({"3": {"up": 5, "down": 5}}, 3, "up"))

    def test_computes_rate_excluding_flat(self):
        b = {"3": {"up": 70, "down": 30, "flat": 500}}
        self.assertAlmostEqual(sl.baseline_rate(b, 3, "up"), 0.70)

    def test_missing_horizon(self):
        self.assertIsNone(sl.baseline_rate({}, 3, "up"))


class TestBackfillAndStats(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        self.path = self.tmp.name

    def tearDown(self):
        for p in (self.path, self.path + ".tmp"):
            if os.path.exists(p):
                os.remove(p)

    def _trending_df(self, n=250, drift=0.01):
        """꾸준히 오르는 계열 — 상승 기준선이 높게 나와야 한다."""
        bars = []
        px = 100.0
        for i in range(n):
            nxt = px * (1 + drift)
            bars.append((px, nxt * 1.01, px * 0.99, nxt))
            px = nxt
        return make_df(bars)

    def test_backfill_produces_events_and_baseline(self):
        evs, base = sl.backfill("A/USDT", self._trending_df(), horizons=(1, 3))
        self.assertTrue(evs)
        self.assertTrue(all(e["source"] == "backfill" for e in evs))
        self.assertTrue(base[3]["up"] > 0)

    def test_baseline_reflects_drift(self):
        """상승 드리프트 계열이면 무조건부 상승 확률이 압도적이어야 한다."""
        _, base = sl.backfill("A/USDT", self._trending_df(), horizons=(3,))
        decided = base[3]["up"] + base[3]["down"]
        self.assertGreater(base[3]["up"] / decided, 0.9)

    def test_edge_near_zero_when_setup_only_rides_drift(self):
        """
        이 모듈의 핵심 주장을 검증한다.

        꾸준히 오르는 계열에서는 상승 셋업의 적중률이 90%+로 나오지만,
        기준선도 똑같이 90%+다. 따라서 초과 적중률(edge)은 0 근처여야 한다
        — "적중률이 높다 ≠ 우위가 있다".
        """
        evs, base = sl.backfill("A/USDT", self._trending_df(), horizons=(3,))
        sl.merge(evs, base, path=self.path)
        st = sl.stats(horizon=3, path=self.path, min_samples=5)

        up_rows = [r for r in st["rows"] if r["direction"] == "up"
                   and r["edge"] is not None]
        self.assertTrue(up_rows, "상승 셋업이 하나도 집계되지 않았다")
        for r in up_rows:
            self.assertGreater(r["hit_rate"], 0.85)     # 적중률은 높고
            self.assertLess(abs(r["edge"]), 0.15)       # 우위는 거의 없다

    def test_merge_dedupes_by_id(self):
        evs, base = sl.backfill("A/USDT", self._trending_df(), horizons=(3,))
        sl.merge(evs, base, path=self.path)
        first = sl.summary(self.path)["total"]
        sl.merge(evs, base, path=self.path)
        self.assertEqual(sl.summary(self.path)["total"], first)

    def test_merge_accumulates_baseline(self):
        evs, base = sl.backfill("A/USDT", self._trending_df(), horizons=(3,))
        sl.merge(evs, base, path=self.path)
        b1 = sl._load(self.path)["baseline"]["3"]["up"]
        sl.merge(evs, base, path=self.path)
        b2 = sl._load(self.path)["baseline"]["3"]["up"]
        self.assertEqual(b2, b1 * 2)

    def test_stats_respects_min_samples(self):
        evs, base = sl.backfill("A/USDT", self._trending_df(), horizons=(3,))
        sl.merge(evs, base, path=self.path)
        self.assertEqual(sl.stats(3, self.path, min_samples=10_000)["rows"], [])

    def test_regime_filter(self):
        evs, base = sl.backfill("A/USDT", self._trending_df(), horizons=(3,),
                                regime_lookup=lambda d: "상승국면")
        sl.merge(evs, base, path=self.path)
        self.assertTrue(sl.stats(3, self.path, regime="상승국면", min_samples=5)["rows"])
        self.assertEqual(sl.stats(3, self.path, regime="관망", min_samples=5)["rows"], [])

    def test_short_history_returns_nothing(self):
        evs, base = sl.backfill("A/USDT", make_df([(100, 101, 99, 100)] * 10))
        self.assertEqual(evs, [])

    def test_reset_clears(self):
        evs, base = sl.backfill("A/USDT", self._trending_df(), horizons=(3,))
        sl.merge(evs, base, path=self.path)
        sl.reset(self.path)
        self.assertEqual(sl.summary(self.path)["total"], 0)

    def test_report_renders(self):
        evs, base = sl.backfill("A/USDT", self._trending_df(), horizons=(3,))
        sl.merge(evs, base, path=self.path)
        out = sl.get_report(3, self.path)
        self.assertIn("셋업 채점표", out)
        self.assertIn("기준선", out)

    def test_report_when_empty(self):
        self.assertIn("원장백필", sl.get_report(3, self.path))


class TestCaptureAndScore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        self.path = self.tmp.name

    def tearDown(self):
        for p in (self.path, self.path + ".tmp"):
            if os.path.exists(p):
                os.remove(p)

    def _df_with_final_breakout(self, n=40):
        bars = [(100.0, 101.0, 99.0, 100.0) for _ in range(n)]
        bars.append((100.0, 106.0, 99.5, 105.0))    # 전일 고가 돌파 마감
        return make_df(bars)

    def test_capture_records_unscored_live_event(self):
        df = self._df_with_final_breakout()
        evs = sl.capture(["A/USDT"], lambda s, tf, limit=None: df, path=self.path)
        self.assertTrue(evs)
        self.assertTrue(all(e["source"] == "live" for e in evs))
        self.assertTrue(all(e["scores"] == {} for e in evs))
        self.assertIn("break_prev_high", [e["type"] for e in evs])

    def test_capture_tags_regime(self):
        df = self._df_with_final_breakout()
        evs = sl.capture(["A/USDT"], lambda s, tf, limit=None: df,
                         regime="상승우위", path=self.path)
        self.assertTrue(all(e["regime"] == "상승우위" for e in evs))

    def test_score_pending_fills_scores_once_future_exists(self):
        df = self._df_with_final_breakout()
        sl.capture(["A/USDT"], lambda s, tf, limit=None: df, path=self.path)

        # 이후 봉이 생긴 상태 — 돌파 후 크게 올랐다
        extended = self._df_with_final_breakout()
        import pandas as pd
        more = pd.DataFrame([{
            "timestamp": extended["timestamp"].iloc[-1] + pd.Timedelta(days=k + 1),
            "open": 105.0, "high": 120.0, "low": 104.0, "close": 118.0,
            "volume": 1000.0,
        } for k in range(5)])
        full = pd.concat([extended, more], ignore_index=True)

        n = sl.score_pending(lambda s, tf, limit=None: full,
                             horizons=(1, 3), path=self.path)
        self.assertGreater(n, 0)
        evs = sl._load(self.path)["events"]
        brk = [e for e in evs if e["type"] == "break_prev_high"][0]
        self.assertEqual(brk["scores"]["1"]["result"], "hit")

    def test_score_pending_noop_when_nothing_pending(self):
        self.assertEqual(sl.score_pending(lambda s, tf, limit=None: None,
                                          path=self.path), 0)

    def test_capture_survives_fetch_error(self):
        def boom(s, tf, limit=None):
            raise RuntimeError("x")
        self.assertEqual(sl.capture(["A/USDT"], boom, path=self.path), [])


if __name__ == "__main__":
    unittest.main()
