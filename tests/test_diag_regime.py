"""patches/diag_regime.py 회귀 테스트.

이 도구의 출력으로 "실돈을 넣을지"를 정하게 된다. 판정이 헐거우면
검증 안 된 신호가 통과하고, 그게 지금 상황에서 제일 비싼 실수다.

고정하는 것:
  · 전체 기대값이 마이너스면 다른 항목을 따지지 않는다 (판정이 헷갈리면 안 됨)
  · 두 기간 모두 마이너스인데 '한 기간만 플러스'라고 하지 않는다
  · 한 종목이 만든 숫자를 잡아낸다
  · 신뢰구간·필요 표본수가 교과서 값과 맞는다
  · 타입을 여러 개 잰 만큼 문턱이 올라간다 (본페로니)
"""

import unittest

try:
    from .helpers import load_patch
except ImportError:            # 봇 폴더에 그대로 복사된 경우
    from helpers import load_patch

fx = load_patch("diag_regime")


def T(r, date="2025-01-01", coin="BTC"):
    return {"r": r, "date": date, "_coin": coin}


class TestStats(unittest.TestCase):

    def test_mean_and_stdev(self):
        tr = [T(1.0), T(-1.0), T(2.0), T(0.0)]
        self.assertAlmostEqual(fx.mean_r(tr), 0.5)
        self.assertAlmostEqual(fx.stdev_r(tr), 1.2909944, places=5)

    def test_empty_and_single(self):
        self.assertEqual(fx.mean_r([]), 0.0)
        self.assertEqual(fx.stdev_r([T(1.0)]), 0.0)
        lo, hi, se = fx.conf_interval([T(1.0)])
        self.assertEqual((lo, hi), (float("-inf"), float("inf")))

    def test_conf_interval_matches_formula(self):
        tr = [T(1.0)] * 50 + [T(-1.0)] * 50
        lo, hi, se = fx.conf_interval(tr)
        # 평균 0, 표준편차 ≈1 → SE ≈ 0.1005
        self.assertAlmostEqual(fx.mean_r(tr), 0.0)
        self.assertAlmostEqual(se, fx.stdev_r(tr) / 10.0)
        self.assertAlmostEqual(lo, -1.96 * se)
        self.assertAlmostEqual(hi, +1.96 * se)

    def test_needed_n(self):
        """n = (z·sd/m)^2 — 지금 성적이 유지될 때 0을 벗어날 표본 수."""
        tr = [T(1.2)] * 30 + [T(-1.0)] * 30      # 평균 +0.1
        need = fx.needed_n(tr)
        import math
        expect = math.ceil((1.96 * fx.stdev_r(tr) / fx.mean_r(tr)) ** 2)
        self.assertEqual(need, expect)
        self.assertGreater(need, len(tr), "표본이 더 필요한 상황이어야 한다")

    def test_needed_n_is_none_when_already_negative(self):
        self.assertIsNone(fx.needed_n([T(-1.0)] * 20))

    def test_bonferroni_raises_the_bar(self):
        """타입을 6개 재면 문턱이 1.96보다 높아야 한다."""
        z1 = fx._z_for(0.05)
        z6 = fx._z_for(0.05 / 6)
        self.assertAlmostEqual(z1, 1.96, places=2)
        self.assertGreater(z6, z1)
        self.assertAlmostEqual(z6, 2.638, places=2)

    def test_split_by_date(self):
        tr = [T(1.0, "2024-01-01"), T(1.0, "2025-06-30"), T(1.0, "2025-07-01")]
        a, b = fx.split_by_date(tr, "2025-07-01")
        self.assertEqual(len(a), 2)
        self.assertEqual(len(b), 1)


class TestJudge(unittest.TestCase):

    def verdict(self, trades, n_types=6, mid="2025-07-01"):
        return "\n".join(fx.judge("t", trades, mid, -20.0, +30.0, n_types))

    def test_negative_type_short_circuits(self):
        """전체가 마이너스면 다른 사유를 늘어놓지 않는다."""
        tr = ([T(-1.0, "2024-01-01", f"C{i}") for i in range(30)]
              + [T(-1.0, "2026-01-01", f"C{i}") for i in range(30)])
        v = self.verdict(tr)
        self.assertIn("전체 기대값이 마이너스", v)
        self.assertNotIn("국면 의존", v)
        self.assertNotIn("한 종목이 만든", v)
        self.assertNotIn("우위 후보", v)

    def test_regime_dependence_is_caught(self):
        """전반기만 벌고 후반기에 잃으면 잡아야 한다."""
        tr = ([T(3.0, "2024-01-01", f"C{i}") for i in range(20)]
              + [T(-0.5, "2026-01-01", f"C{i}") for i in range(20)])
        v = self.verdict(tr)
        self.assertIn("국면 의존", v)
        self.assertIn("전반기만 플러스", v)
        self.assertNotIn("우위 후보", v)

    def test_both_periods_negative_is_not_called_regime(self):
        """두 기간 다 마이너스인데 '한 기간만 플러스'라고 하면 안 된다."""
        tr = ([T(2.0, "2024-01-01", "A")] * 1
              + [T(-0.2, "2024-02-01", f"C{i}") for i in range(20)]
              + [T(-0.2, "2026-01-01", f"C{i}") for i in range(20)])
        v = self.verdict(tr)
        self.assertNotIn("만 플러스", v)

    def test_single_coin_concentration_is_caught(self):
        """한 종목이 다 만든 숫자."""
        tr = ([T(9.0, "2024-01-01", "WIN") for _ in range(12)]
              + [T(9.0, "2026-01-01", "WIN") for _ in range(12)]
              + [T(-0.3, "2024-06-01", f"C{i}") for i in range(12)]
              + [T(-0.3, "2026-06-01", f"C{i}") for i in range(12)])
        v = self.verdict(tr)
        self.assertIn("WIN 하나를 빼면 마이너스", v)
        self.assertNotIn("우위 후보", v)

    def test_clean_edge_passes(self):
        """넓게 퍼지고 두 기간 다 벌고 표본도 충분하면 통과해야 한다."""
        tr = []
        for period in ("2024-01-01", "2026-01-01"):
            for i in range(120):
                coin = f"C{i % 20}"
                tr.append(T(2.0 if i % 2 == 0 else -1.0, period, coin))
        v = self.verdict(tr)
        self.assertIn("우위 후보", v)

    def test_thin_period_is_flagged(self):
        tr = ([T(2.0, "2024-01-01", f"C{i}") for i in range(40)]
              + [T(2.0, "2026-01-01", f"C{i}") for i in range(5)])
        v = self.verdict(tr)
        self.assertIn("10건 미만", v)

    def test_more_types_tested_makes_it_harder(self):
        """같은 성적이라도 타입을 많이 재면 통과가 어려워야 한다."""
        # 코인 19개(홀수)에 승패를 번갈아 배분하면 종목마다 승패가 섞인다.
        # 20개로 하면 짝수 인덱스 종목만 이겨서 집중도에 먼저 걸린다.
        tr = []
        for period in ("2024-01-01", "2026-01-01"):
            for i in range(100):
                tr.append(T(1.35 if i % 2 == 0 else -1.0, period, f"C{i % 19}"))
        lenient = self.verdict(tr, n_types=1)
        strict = self.verdict(tr, n_types=20)
        self.assertIn("우위 후보", lenient)
        self.assertNotIn("우위 후보", strict)


if __name__ == "__main__":
    unittest.main()
