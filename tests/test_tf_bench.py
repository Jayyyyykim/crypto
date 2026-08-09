"""patches/tf_bench.py 회귀 테스트.

이 도구는 "4시간봉에서는 달랐을 수도 있잖아"를 닫기 위한 것이다.
여기가 틀리면 그 물음이 안 닫히거나, 더 나쁘게는 **잘못 닫힌다.**

고정하는 것:
  · 비용(R)은 손절폭에 반비례한다 — 시간대가 짧아지면 커진다
  · 고정비는 왕복이다 (진입·청산 각각 수수료+슬리피지)
  · 시세를 못 받으면 표기를 바꿔 다시 시도하고, 그래도 없으면 None
  · 짧은 이력을 받아 놓고 다 받은 척하지 않는다
"""

import unittest

try:
    from .helpers import load_patch
except ImportError:            # 봇 폴더에 그대로 복사된 경우
    from helpers import load_patch

fx = load_patch("tf_bench")


class FakeBt:
    FEE_RATE = 0.0006
    SLIPPAGE = 0.0005
    ATR_STOP_MULT = 1.5

    def __init__(self, ok=None, bars=1000):
        self.ok = ok
        self.bars = bars
        self.tried = []

    def get_ohlcv_history(self, sym, tf, n):
        self.tried.append((sym, tf, n))
        if self.ok is not None and sym != self.ok:
            raise ValueError(f"bitget does not have market symbol {sym}")
        return list(range(min(n, self.bars)))


class TestCost(unittest.TestCase):
    """비용이 이 시험의 핵심이다. 여기가 틀리면 결론이 통째로 틀린다."""

    def setUp(self):
        self.old = fx.bt
        fx.bt = FakeBt()

    def tearDown(self):
        fx.bt = self.old

    def test_round_trip_is_two_sided(self):
        """진입에 한 번, 청산에 한 번. 한쪽만 세면 비용이 절반이 된다."""
        self.assertAlmostEqual(fx.round_trip_cost(), 2 * (0.0006 + 0.0005))

    def test_narrower_stop_costs_more(self):
        wide = fx.cost_in_r(0.05)      # 일봉 5%
        tight = fx.cost_in_r(0.017)    # 4시간봉 1.7%
        self.assertGreater(tight, wide)
        self.assertAlmostEqual(tight / wide, 0.05 / 0.017, places=6)

    def test_cost_matches_hand_calculation(self):
        # 고정비 0.22% ÷ 손절폭 5% = 0.044R
        self.assertAlmostEqual(fx.cost_in_r(0.05), 0.044, places=6)

    def test_halving_the_stop_doubles_the_cost(self):
        self.assertAlmostEqual(fx.cost_in_r(0.02) / fx.cost_in_r(0.04), 2.0, places=9)

    def test_nan_width_does_not_crash(self):
        self.assertNotEqual(fx.cost_in_r(float("nan")), fx.cost_in_r(float("nan")))
        self.assertNotEqual(fx.cost_in_r(0), fx.cost_in_r(0))

    def test_missing_constants_fall_back(self):
        class Bare:
            pass
        fx.bt = Bare()
        self.assertGreater(fx.round_trip_cost(), 0)


class TestStopWidth(unittest.TestCase):

    def setUp(self):
        self.old = fx.bt
        fx.bt = FakeBt()

    def tearDown(self):
        fx.bt = self.old

    def frame(self, atr, close, n=100):
        import pandas as pd
        return pd.DataFrame({"atr": [atr] * n, "close": [close] * n})

    def test_width_is_stop_over_price(self):
        # 1.5 × ATR 2.0 / 가격 100 = 3%
        self.assertAlmostEqual(fx.stop_width(self.frame(2.0, 100.0)), 0.03)

    def test_absurd_widths_are_dropped(self):
        """ATR 이 가격보다 큰 줄은 자료 오류다. 중앙값을 끌고 간다."""
        import pandas as pd
        d = pd.DataFrame({"atr": [2.0] * 50 + [500.0] * 3,
                          "close": [100.0] * 53})
        self.assertAlmostEqual(fx.stop_width(d), 0.03)

    def test_empty_is_nan(self):
        import pandas as pd
        d = pd.DataFrame({"atr": [], "close": []})
        self.assertNotEqual(fx.stop_width(d), fx.stop_width(d))


class TestFetch(unittest.TestCase):

    def setUp(self):
        self.old = fx.bt

    def tearDown(self):
        fx.bt = self.old

    def test_spot_first(self):
        fx.bt = FakeBt(ok="TON/USDT")
        self.assertIsNotNone(fx.fetch("TON/USDT", "TON", "4h", 1000))
        self.assertEqual(fx.bt.tried[0][0], "TON/USDT")

    def test_perp_fallback(self):
        fx.bt = FakeBt(ok="TON/USDT:USDT")
        self.assertIsNotNone(fx.fetch("TON/USDT", "TON", "4h", 1000))

    def test_timeframe_is_passed_through(self):
        """4시간봉을 달라고 했는데 일봉을 받으면 시험이 통째로 헛것이 된다."""
        fx.bt = FakeBt()
        fx.fetch("BTC/USDT", "BTC", "4h", 1000)
        self.assertTrue(all(t[1] == "4h" for t in fx.bt.tried), fx.bt.tried)

    def test_too_short_is_none(self):
        fx.bt = FakeBt(bars=50)
        self.assertIsNone(fx.fetch("BTC/USDT", "BTC", "4h", 1000))

    def test_all_forms_failing_is_none(self):
        fx.bt = FakeBt(ok="없는것")
        self.assertIsNone(fx.fetch("BTC/USDT", "BTC", "4h", 1000))


class TestBarMath(unittest.TestCase):
    """2년을 달라고 했는데 2개월을 받으면 결론이 틀어진다 (⑪ 과 같은 실수)."""

    def test_hours_per_bar(self):
        self.assertEqual(fx.TF_HOURS["4h"], 4.0)
        self.assertEqual(fx.TF_HOURS["1d"], 24.0)

    def test_two_years_of_4h_is_about_4400_bars(self):
        bars = int(730 * 24 / fx.TF_HOURS["4h"])
        self.assertAlmostEqual(bars, 4380, delta=10)

    def test_one_year_of_1h_is_about_8760_bars(self):
        bars = int(365 * 24 / fx.TF_HOURS["1h"])
        self.assertAlmostEqual(bars, 8760, delta=10)


class TestWidth(unittest.TestCase):

    def test_korean_counts_as_two(self):
        self.assertEqual(len(fx.sb_w("항목", 10)), 8)
        self.assertEqual(len(fx.sb_w("abc", 10)), 10)


if __name__ == "__main__":
    unittest.main()
