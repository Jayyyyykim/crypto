"""patches/exit_bench.py 회귀 테스트.

출구 규칙은 조용히 틀리기 쉽다. 추적손절이 안 따라가거나, 숏에서
부호가 뒤집히거나, 수수료가 빠지거나 해도 숫자는 그럴듯하게 나온다.
그러면 '출구를 바꿔도 안 되더라'는 결론 자체가 거짓이 된다.

고정하는 것:
  · 손절이면 -1R, 목표면 그 배수 — 수수료를 뺀 만큼 정확히
  · 추적손절이 실제로 따라 올라간다 (그리고 되돌림에서 이익을 지킨다)
  · 숏은 롱의 거울상
  · 같은 봉에서 손절·목표가 함께 닿으면 손절을 먼저 본다
"""

import types
import unittest

try:
    from .helpers import load_patch
except ImportError:            # 봇 폴더에 그대로 복사된 경우
    from helpers import load_patch

fx = load_patch("exit_bench")

FEE, SLIP = 0.0006, 0.0005


def cost_r(entry, exits, unit):
    return (entry * FEE + sum((p * FEE + p * SLIP) * q for p, q in exits)) / unit


def stub_bt():
    return types.SimpleNamespace(ATR_STOP_MULT=1.5, SLIPPAGE=SLIP,
                                 FEE_RATE=FEE, _cost_r=cost_r)


def frame(bars, atr=2.0):
    """bars: [(open, high, low, close), ...]  — 0번은 진입 봉 앞."""
    import pandas as pd
    return pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01", periods=len(bars), freq="1D"),
        "open": [b[0] for b in bars], "high": [b[1] for b in bars],
        "low": [b[2] for b in bars], "close": [b[3] for b in bars],
        "atr": [atr] * len(bars),
    })


class TestSimulate(unittest.TestCase):

    def setUp(self):
        fx.bt = stub_bt()

    def flat(self, n):
        return [(100.0, 100.0, 100.0, 100.0)] * n

    def test_stop_gives_minus_one_r_minus_cost(self):
        bars = self.flat(1) + [(100.0, 100.0, 100.0, 100.0)] + [(100.0, 100.0, 90.0, 92.0)]
        r = fx.simulate(frame(bars), 0, True, "A 현행")
        entry = 100.0 * (1 + SLIP)
        unit = 3.0                       # 1.5 × ATR 2.0
        self.assertAlmostEqual(r, round(-1.0 - cost_r(entry, [(entry - unit, 1.0)], unit), 3),
                               places=2)

    def test_fixed_3r_target(self):
        bars = self.flat(1) + [(100.0, 100.0, 100.0, 100.0)] + [(100.0, 130.0, 100.0, 128.0)]
        r = fx.simulate(frame(bars), 0, True, "D 고정3R")
        self.assertGreater(r, 2.8)
        self.assertLess(r, 3.0, "수수료가 안 빠졌다")

    def test_current_rule_caps_at_about_1_5r(self):
        """1R 절반 + 2R 절반 = 1.5R. 10R짜리 움직임이 와도 여기서 잘린다."""
        bars = self.flat(1) + [(100.0, 100.0, 100.0, 100.0)] + [(100.0, 200.0, 100.0, 199.0)]
        r = fx.simulate(frame(bars), 0, True, "A 현행")
        self.assertGreater(r, 1.3)
        self.assertLess(r, 1.5, "현행 규칙이 2R 위로 먹으면 안 된다")

    def test_trailing_stop_protects_a_gain(self):
        """크게 올랐다 되돌아오면 본전이 아니라 이익 근처에서 나와야 한다."""
        bars = (self.flat(1) + [(100.0, 100.0, 100.0, 100.0)]
                + [(100.0, 130.0, 100.0, 130.0)]        # +10R 지점까지 상승
                + [(130.0, 130.0, 100.0, 100.0)])       # 되돌림
        r = fx.simulate(frame(bars), 0, True, "B 추적2ATR")
        # 고점 130 에서 2×ATR(4) 밀린 126 에서 청산 → 약 (126-100)/3 ≈ 8.6R
        self.assertGreater(r, 7.0, "추적손절이 안 따라 올라갔다")

    def test_trailing_never_moves_backwards(self):
        """손절은 유리한 쪽으로만 움직인다."""
        bars = (self.flat(1) + [(100.0, 100.0, 100.0, 100.0)]
                + [(100.0, 120.0, 100.0, 120.0)]
                + [(120.0, 121.0, 118.0, 119.0)]        # 살짝 후퇴 — 손절은 그대로
                + [(119.0, 119.0, 100.0, 100.0)])
        r = fx.simulate(frame(bars), 0, True, "B 추적2ATR")
        self.assertGreater(r, 4.0)

    def test_short_is_mirrored(self):
        up = self.flat(1) + [(100.0, 100.0, 100.0, 100.0)] + [(100.0, 130.0, 100.0, 128.0)]
        dn = self.flat(1) + [(100.0, 100.0, 100.0, 100.0)] + [(100.0, 100.0, 70.0, 72.0)]
        rl = fx.simulate(frame(up), 0, True, "D 고정3R")
        rs = fx.simulate(frame(dn), 0, False, "D 고정3R")
        self.assertAlmostEqual(rl, rs, places=1)

    def test_stop_wins_ties_within_a_bar(self):
        """같은 봉에서 둘 다 닿으면 손절이 먼저다 (보수적)."""
        bars = self.flat(1) + [(100.0, 100.0, 100.0, 100.0)] + [(100.0, 200.0, 80.0, 150.0)]
        r = fx.simulate(frame(bars), 0, True, "D 고정3R")
        self.assertLess(r, 0, "동시 터치인데 이익으로 처리했다")

    def test_trailing_stop_is_checked_within_the_same_bar(self):
        """고점을 찍고 같은 봉에서 되밀리면 그 봉에서 손절돼야 한다.

        안 그러면 손절을 올려두고 되밀림은 다음 봉에서야 검사하게 된다 —
        추적손절에 한 봉짜리 미래를 공짜로 주는 셈이라 성적이 부풀려진다.
        """
        # 진입 100, ATR 2 → 초기 손절 97. 한 봉에서 고점 130, 저점 100.
        # 고점 130 → 손절 126 으로 상승. 같은 봉 저점 100 이 126 아래다.
        bars = (self.flat(1) + [(100.0, 100.0, 100.0, 100.0)]
                + [(100.0, 130.0, 100.0, 129.0)]
                + [(129.0, 300.0, 129.0, 299.0)])      # 다음 봉은 폭등
        r = fx.simulate(frame(bars), 0, True, "B 추적2ATR")
        self.assertLess(r, 10.0, "같은 봉 되밀림을 놓치고 다음 봉 폭등을 먹었다")
        self.assertGreater(r, 7.0, "126 근처에서 나왔어야 한다")

    def test_nan_atr_returns_none(self):
        bars = self.flat(3)
        self.assertIsNone(fx.simulate(frame(bars, atr=float("nan")), 0, True, "A 현행"))

    def test_too_short_future_returns_none(self):
        self.assertIsNone(fx.simulate(frame(self.flat(2)), 1, True, "A 현행"))

    def test_all_exit_names_are_handled(self):
        bars = self.flat(1) + [(100.0, 100.0, 100.0, 100.0)] + self.flat(10)
        for e in fx.EXITS:
            r = fx.simulate(frame(bars), 0, True, e)
            self.assertIsNotNone(r, e)
            self.assertLess(r, 0, f"{e}: 움직임이 없으면 수수료만큼 마이너스여야 한다")


class TestStats(unittest.TestCase):

    def test_thin_period_cannot_pass(self):
        a = [{"r": 1.0}] * (fx.MIN_PER_PERIOD - 1)
        d, lo, hi = fx.excess(a, [{"r": 0.0}] * 500)
        self.assertEqual(lo, float("-inf"))

    def test_clear_excess_passes(self):
        d, lo, hi = fx.excess([{"r": 1.0}] * 200, [{"r": 0.0}] * 200)
        self.assertGreater(lo, 0)

    def test_split_is_a_partition(self):
        ts = [{"r": 0.0, "date": x} for x in
              ("2023-01-01", "2024-09-06", "2024-09-05", "2026-01-01")]
        a, b = fx.split(ts, "2024-09-06")
        self.assertEqual(len(a) + len(b), len(ts))


if __name__ == "__main__":
    unittest.main()
