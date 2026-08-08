"""patches/diag_baseline.py 회귀 테스트.

이 도구가 "신호가 무작위 진입보다 나은가"를 판정한다. 여기가 헐거우면
방향 베팅을 우위로 착각하고 실돈이 들어간다.

고정하는 것:
  · 무작위 표본은 실행마다 같아야 한다 (다르면 결론이 흔들린다)
  · 롱/숏 손절·목표 방향이 뒤집히면 안 된다
  · 초과 판정은 신뢰구간 하한이 0을 넘을 때만 ✅
  · 기간을 반으로 나눌 때 경계가 중복되거나 빠지지 않는다
"""

import types
import unittest

try:
    from .helpers import load_patch
except ImportError:            # 봇 폴더에 그대로 복사된 경우
    from helpers import load_patch

fx = load_patch("diag_baseline")


def T(r):
    return {"r": r}


class TestStats(unittest.TestCase):

    def test_mean_and_se(self):
        ts = [T(1.0)] * 50 + [T(-1.0)] * 50
        self.assertAlmostEqual(fx.mean_r(ts), 0.0)
        self.assertAlmostEqual(fx.se_r(ts), fx.se_r(ts))   # 계산이 유한한지
        self.assertLess(fx.se_r(ts), 0.2)

    def test_empty_is_safe(self):
        self.assertEqual(fx.mean_r([]), 0.0)
        self.assertEqual(fx.se_r([]), float("inf"))

    def test_stable_seed_is_reproducible(self):
        """파이썬 hash()는 실행마다 달라진다 — crc32 로 고정했다."""
        self.assertEqual(fx.stable_seed("BTC/L"), fx.stable_seed("BTC/L"))
        self.assertNotEqual(fx.stable_seed("BTC/L"), fx.stable_seed("BTC/S"))
        self.assertEqual(fx.stable_seed("BTC/L"), 3218367118,
                         "씨앗 값이 바뀌면 예전 회차와 표본이 달라진다")

    def test_is_long(self):
        for t in ("MID_LONG", "FIB_LONG", "SMA_LONG", "ANGEL"):
            self.assertTrue(fx.is_long(t), t)
        for t in ("MID_SHORT", "FIB_SHORT", "SMA_SHORT", "DEMON"):
            self.assertFalse(fx.is_long(t), t)

    def test_split_is_a_partition(self):
        ts = [{"r": 0.0, "date": d} for d in
              ("2024-01-01", "2025-07-01", "2025-06-30", "2026-01-01")]
        a, b = fx.split(ts, "2025-07-01")
        self.assertEqual(len(a) + len(b), len(ts), "겹치거나 빠진 게 있다")
        self.assertTrue(all(t["date"] < "2025-07-01" for t in a))
        self.assertTrue(all(t["date"] >= "2025-07-01" for t in b))


class TestExcess(unittest.TestCase):

    def test_significant_excess_passes(self):
        act = [T(1.0)] * 200
        base = [T(0.0)] * 200
        line, d, ok = fx.excess_line("전체", act, base)
        self.assertTrue(ok)
        self.assertAlmostEqual(d, 1.0)
        self.assertIn("✅", line)

    def test_positive_but_noisy_does_not_pass(self):
        """초과가 양수여도 신뢰구간이 0을 포함하면 통과가 아니다."""
        act = [T(2.0), T(-2.0)] * 6 + [T(0.4)]
        base = [T(2.0), T(-2.0)] * 100
        line, d, ok = fx.excess_line("전체", act, base)
        self.assertGreater(d, 0)
        self.assertFalse(ok, "노이즈인데 통과시켰다")
        self.assertNotIn("✅", line)

    def test_negative_excess_is_marked(self):
        line, d, ok = fx.excess_line("전체", [T(-1.0)] * 100, [T(0.0)] * 100)
        self.assertFalse(ok)
        self.assertIn("❌", line)

    def test_thin_sample_is_reported(self):
        line, d, ok = fx.excess_line("전반기", [], [T(0.0)] * 10)
        self.assertFalse(ok)
        self.assertIn("표본 부족", line)


class TestRandomTrades(unittest.TestCase):

    def frame(self, n=200):
        import pandas as pd
        return pd.DataFrame({
            "timestamp": pd.date_range("2024-01-01", periods=n, freq="1D"),
            "open": [100.0] * n, "high": [101.0] * n,
            "low": [99.0] * n, "close": [100.0] * n,
            "atr": [2.0] * n,
        })

    def setUp(self):
        self.seen = []
        fx.bt = types.SimpleNamespace(
            ATR_STOP_MULT=1.5,
            evaluate_trade=lambda sig, df: (self.seen.append(sig) or {"r": 0.0}),
        )

    def test_long_geometry(self):
        out = fx.random_trades(self.frame(), "BTC", True, n=5)
        self.assertTrue(out)
        for t in out:
            self.assertAlmostEqual(t["sl"], 100.0 - 3.0)      # 1.5 × ATR 2.0
            self.assertAlmostEqual(t["tp1"], 100.0 + 3.0)
            self.assertAlmostEqual(t["tp2"], 100.0 + 6.0)
            self.assertLess(t["sl"], t["entry"], "롱 손절이 진입가 위에 있다")
            self.assertTrue(fx.is_long(t["type"]))

    def test_short_geometry(self):
        out = fx.random_trades(self.frame(), "BTC", False, n=5)
        for t in out:
            self.assertAlmostEqual(t["sl"], 100.0 + 3.0)
            self.assertAlmostEqual(t["tp1"], 100.0 - 3.0)
            self.assertGreater(t["sl"], t["entry"], "숏 손절이 진입가 아래에 있다")
            self.assertFalse(fx.is_long(t["type"]))

    def test_same_days_every_run(self):
        a = [t["idx"] for t in fx.random_trades(self.frame(), "BTC", True, n=20)]
        b = [t["idx"] for t in fx.random_trades(self.frame(), "BTC", True, n=20)]
        self.assertEqual(a, b, "무작위 표본이 실행마다 달라진다")

    def test_different_coins_get_different_days(self):
        a = [t["idx"] for t in fx.random_trades(self.frame(), "BTC", True, n=20)]
        b = [t["idx"] for t in fx.random_trades(self.frame(), "ETH", True, n=20)]
        self.assertNotEqual(a, b)

    def test_nan_atr_rows_are_skipped(self):
        df = self.frame()
        df.loc[:, "atr"] = float("nan")
        self.assertEqual(fx.random_trades(df, "BTC", True, n=10), [])

    def test_short_frame_is_safe(self):
        self.assertEqual(fx.random_trades(self.frame(n=20), "BTC", True, n=10), [])

    def test_never_samples_the_last_bar(self):
        """마지막 봉에서 진입하면 뒤를 볼 수 없다."""
        df = self.frame(n=200)
        out = fx.random_trades(df, "BTC", True, n=100)
        self.assertTrue(out)
        self.assertLess(max(t["idx"] for t in out), len(df) - 1)


if __name__ == "__main__":
    unittest.main()
