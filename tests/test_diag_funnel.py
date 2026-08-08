"""patches/diag_funnel.py 의 깔때기 집계 회귀 테스트.

이 스크립트는 아무것도 고치지 않지만, **어느 조건을 고칠지**를 여기서
정하게 된다. 병목을 잘못 짚으면 엉뚱한 데를 고치고 또 한 바퀴 돈다.

고정하는 것:
  · 누적 통과 수는 앞 조건이 깨지면 뒤를 세지 않는다
  · 단독 통과 수는 순서와 무관하게 각자 센다
  · 0으로 떨어지는 줄이 무조건 병목이 되면 안 된다
    (2건 → 0건 이 1,104건 → 3건 을 제치면 안 된다)
"""

import unittest

try:
    from .helpers import load_patch
except ImportError:            # 봇 폴더에 그대로 복사된 경우
    from helpers import load_patch


def tally(*args, **kwargs):
    # diag_funnel 은 backtest 를 import 한다. 여기서는 Tally 만 필요하므로
    # 모듈 전체를 불러오지 못하면 건너뛴다.
    fx = load_patch("diag_funnel")
    return fx.Tally(*args, **kwargs)


class TestTally(unittest.TestCase):

    def make(self):
        return tally("t", ["① a", "② + b", "③ + c"])

    def test_cumulative_stops_at_first_failure(self):
        t = self.make()
        t.feed([True, True, True])
        t.feed([True, True, False])
        t.feed([True, False, True])
        t.feed([False, True, True])
        self.assertEqual(t.total, 4)
        self.assertEqual(t.steps, [3, 2, 1])

    def test_solo_counts_are_independent_of_order(self):
        t = self.make()
        t.feed([False, True, True])
        t.feed([False, True, False])
        self.assertEqual(t.steps, [0, 0, 0])
        self.assertEqual(t.solo, [0, 2, 1], "누적이 0이어도 단독은 세야 한다")

    def test_zero_row_does_not_win_bottleneck(self):
        """'2건 → 0건'이 '1000건 → 3건'을 제치면 안 된다."""
        t = self.make()
        for k in range(1000):
            a = k < 3                      # ①에서 1000 → 3
            b = a and k < 2                # ②에서 3 → 2
            t.feed([a, b, False])          # ③에서 2 → 0
        self.assertEqual(t.steps, [3, 2, 0])
        line = next(l for l in t.report() if "→ 병목" in l)
        self.assertIn("① a", line)

    def test_bottleneck_is_the_biggest_drop(self):
        t = self.make()
        for k in range(100):
            t.feed([k < 50, k < 5, k < 4])   # 100→50, 50→5, 5→4
        self.assertEqual(t.steps, [50, 5, 4])
        line = next(l for l in t.report() if "→ 병목" in l)
        self.assertIn("② + b", line)

    def test_contradiction_is_flagged(self):
        """서로 모순인 조건 — 독립 기대치보다 훨씬 적으면 표시해야 한다."""
        t = self.make()
        for k in range(1000):
            lo = k < 300                 # 앞쪽 300
            hi = k >= 700                # 뒤쪽 300 — lo 와 절대 겹치지 않는다
            t.feed([lo, hi, True])
        self.assertEqual(t.steps[-1], 0)
        joined = "\n".join(t.report())
        self.assertIn("모순", joined)

    def test_no_contradiction_flag_when_independent(self):
        t = self.make()
        for k in range(1000):
            t.feed([k % 2 == 0, k % 3 == 0, True])
        joined = "\n".join(t.report())
        self.assertNotIn("모순", joined)

    def test_report_survives_empty(self):
        t = self.make()
        self.assertTrue(t.report())      # 0봉이어도 죽지 않는다


if __name__ == "__main__":
    unittest.main()
