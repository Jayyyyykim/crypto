"""patches/signal_bench.py 회귀 테스트.

이 시험대의 판정으로 새 신호를 봇에 넣을지 정하게 된다. 헐거우면
지금까지 걸러낸 것과 똑같은 실수를 새 규칙으로 반복한다.

고정하는 것:
  · 표본이 적은 기간은 ✅ 를 받을 수 없다 (6건짜리가 통과하던 버그)
  · 후보 조건은 미래 봉을 보지 않는다
  · 모든 후보가 같은 거래 구조를 쓴다 — 진입 시점만 다르다
  · NaN 이 섞인 구간에서 조용히 True 가 되지 않는다
"""

import types
import unittest

try:
    from .helpers import load_patch
except ImportError:            # 봇 폴더에 그대로 복사된 경우
    from helpers import load_patch

fx = load_patch("signal_bench")


def T(r):
    return {"r": r}


class TestExcessGate(unittest.TestCase):

    def test_thin_period_cannot_pass(self):
        """구간이 좁게 나오는 건 확실해서가 아니라 표본이 적어서다."""
        act = [T(1.0)] * (fx.MIN_PER_PERIOD - 1)
        base = [T(0.0)] * 500
        d, lo, hi = fx.excess(act, base)
        self.assertEqual((d, lo, hi), (0.0, float("-inf"), float("inf")))
        self.assertFalse(lo > 0)

    def test_thin_baseline_cannot_pass(self):
        d, lo, hi = fx.excess([T(1.0)] * 500, [T(0.0)] * 3)
        self.assertFalse(lo > 0)

    def test_clear_excess_passes(self):
        d, lo, hi = fx.excess([T(1.0)] * 200, [T(0.0)] * 200)
        self.assertAlmostEqual(d, 1.0)
        self.assertGreater(lo, 0)

    def test_noise_does_not_pass(self):
        act = [T(2.0), T(-2.0)] * 30 + [T(0.3)]
        base = [T(2.0), T(-2.0)] * 300
        d, lo, hi = fx.excess(act, base)
        self.assertGreater(d, 0)
        self.assertLess(lo, 0, "노이즈인데 통과시켰다")


class TestCandidates(unittest.TestCase):

    def frame(self, n=400):
        import pandas as pd
        # 계단식 상승 뒤 하락 — 돌파/되돌림 조건이 실제로 걸리게
        close = [100.0 + i * 0.5 for i in range(n // 2)]
        close += [close[-1] - i * 0.5 for i in range(n - n // 2)]
        return pd.DataFrame({
            "timestamp": pd.date_range("2024-01-01", periods=n, freq="1D"),
            "open": close, "close": close,
            "high": [c * 1.01 for c in close], "low": [c * 0.99 for c in close],
            "atr": [2.0] * n, "rsi": [50.0] * n, "ma20": close,
            "recent_high": [c * 1.02 for c in close],
        })

    def test_every_candidate_is_evaluable(self):
        """조건식이 예외 없이 계산돼야 한다 — 예외는 조용한 False 가 된다."""
        d = fx.prepare(self.frame())
        for name, side, cond in fx.CANDIDATES:
            for i in (210, 300, 380):
                try:
                    bool(cond(d, i))
                except Exception as e:
                    self.fail(f"{name} at i={i}: {type(e).__name__}: {e}")

    def test_candidates_do_not_read_the_future(self):
        """i 이후 봉을 바꿔도 i 시점 판정은 그대로여야 한다."""
        base = self.frame()
        d1 = fx.prepare(base)
        tampered = base.copy()
        i = 250
        for col in ("close", "high", "low", "open"):
            tampered.loc[i + 1:, col] = tampered.loc[i + 1:, col] * 5
        d2 = fx.prepare(tampered)
        for name, side, cond in fx.CANDIDATES:
            a, b = bool(cond(d1, i)), bool(cond(d2, i))
            self.assertEqual(a, b, f"{name} 이 미래 봉을 보고 있다")

    def test_prepare_columns_are_backward_looking(self):
        d = fx.prepare(self.frame())
        # atr_pct_q20 은 당일을 빼고 계산한다 (shift(1))
        self.assertTrue(d["atr_pct_q20"].isna().iloc[:60].all())
        self.assertFalse(d["atr_pct_q20"].isna().iloc[200:].any())


class TestTradeShape(unittest.TestCase):

    def setUp(self):
        fx.bt = types.SimpleNamespace(
            ATR_STOP_MULT=1.5,
            evaluate_trade=lambda sig, df: {"r": 0.0},
        )

    def frame(self, n=300, atr=2.0):
        import pandas as pd
        return pd.DataFrame({
            "timestamp": pd.date_range("2024-01-01", periods=n, freq="1D"),
            "open": [100.0] * n, "high": [101.0] * n,
            "low": [99.0] * n, "close": [100.0] * n, "atr": [atr] * n,
        })

    def test_all_candidates_share_one_structure(self):
        """진입 시점만 달라야 한다 — 손절·목표가 다르면 비교가 안 된다."""
        d = self.frame()
        lng = fx.make_trade(d, 250, True)
        sht = fx.make_trade(d, 250, False)
        self.assertAlmostEqual(lng["entry"] - lng["sl"], 3.0)
        self.assertAlmostEqual(sht["sl"] - sht["entry"], 3.0)
        self.assertAlmostEqual(lng["tp2"] - lng["entry"], 6.0)
        self.assertAlmostEqual(sht["entry"] - sht["tp2"], 6.0)

    def test_nan_atr_makes_no_trade(self):
        d = self.frame(atr=float("nan"))
        self.assertIsNone(fx.make_trade(d, 250, True))

    def test_random_sample_is_stable(self):
        d = self.frame()
        a = [t["idx"] for t in fx.random_trades(d, "BTC", True, n=20)]
        b = [t["idx"] for t in fx.random_trades(d, "BTC", True, n=20)]
        self.assertEqual(a, b)

    def test_random_sample_skips_warmup_and_last_bar(self):
        d = self.frame(n=400)
        out = fx.random_trades(d, "BTC", True, n=100)
        self.assertGreaterEqual(min(t["idx"] for t in out), 200)
        self.assertLess(max(t["idx"] for t in out), len(d) - 1)


class TestNoOverlap(unittest.TestCase):
    """겹치는 거래는 서로 독립이 아니다 — 신뢰구간이 실제보다 좁아진다."""

    def setUp(self):
        fx.bt = types.SimpleNamespace(
            ATR_STOP_MULT=1.5,
            evaluate_trade=lambda sig, df: {"r": 0.0},
        )

    def frame(self, n=400, drift=0.0):
        import pandas as pd
        close = [100.0 + i * drift for i in range(n)]
        return pd.DataFrame({
            "timestamp": pd.date_range("2024-01-01", periods=n, freq="1D"),
            "open": close, "close": close,
            "high": [c + 0.2 for c in close], "low": [c - 0.2 for c in close],
            "atr": [2.0] * n,
        })

    def test_hold_bars_hits_stop(self):
        """손절이 먼저 닿으면 그 봉에서 끝난다."""
        import pandas as pd
        d = self.frame(n=100)
        d.loc[10:, "low"] = 50.0                     # 진입 직후 급락
        sig = {"idx": 5, "type": "MID_LONG", "sl": 90.0, "tp1": 110.0, "tp2": 120.0}
        self.assertEqual(fx.hold_bars(d, sig), 5)    # idx 5 → 10번 봉이 5봉째

    def test_hold_bars_times_out(self):
        """아무것도 안 닿으면 max_bars 까지 들고 있다."""
        d = self.frame(n=400)
        sig = {"idx": 200, "type": "MID_LONG", "sl": 1.0, "tp1": 9e9, "tp2": 9e9}
        self.assertEqual(fx.hold_bars(d, sig, max_bars=60), 60)

    def test_hold_bars_short_is_mirrored(self):
        d = self.frame(n=100)
        d.loc[10:, "high"] = 200.0
        sig = {"idx": 5, "type": "MID_SHORT", "sl": 110.0, "tp1": 90.0, "tp2": 80.0}
        self.assertEqual(fx.hold_bars(d, sig), 5)

    def test_hold_bars_at_the_end_is_safe(self):
        d = self.frame(n=100)
        sig = {"idx": 99, "type": "MID_LONG", "sl": 1.0, "tp1": 9e9, "tp2": 9e9}
        self.assertEqual(fx.hold_bars(d, sig), 1)

    def test_random_baseline_respects_no_overlap(self):
        """기준선도 같은 규칙을 받아야 비교가 기울지 않는다."""
        d = self.frame(n=400)
        out = fx.random_trades(d, "BTC", True, n=200)
        idxs = [t["idx"] for t in out]
        self.assertEqual(idxs, sorted(idxs))
        gaps = [b - a for a, b in zip(idxs, idxs[1:])]
        self.assertTrue(all(g > 0 for g in gaps))
        if fx.NO_OVERLAP:
            self.assertLess(len(out), 200, "겹치는 표본이 안 걸러졌다")


if __name__ == "__main__":
    unittest.main()
