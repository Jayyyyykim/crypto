"""patches/fix_level_source.py 회귀 테스트.

가장 중요한 건 **미래 차단**이다. 프랙탈 스윙은 좌우 width봉을 봐야
확정되므로, 인덱스 j인 스윙을 j+width 이전 봉에서 쓰면 백테스트가
미래를 보고 레벨을 그린다. 그렇게 나온 성적은 전부 거짓이다.

나머지:
  · 속도용 캐시가 결과를 바꾸지 않아야 한다 (캐시 없이 계산한 값과 동일)
  · 지지 < 현재가 < 저항 이 항상 성립해야 한다
  · level_map 을 못 불러도 죽지 않고 예전 방식으로 돌아가야 한다
  · 토글을 rolling20 으로 되돌리면 예전 동작 그대로여야 한다
"""

import unittest

try:
    from .helpers import load_patch
except ImportError:            # 봇 폴더에 그대로 복사된 경우
    from helpers import load_patch

fx = load_patch("fix_level_source")

STUB = f'''import pandas as pd

RSI_OVERSOLD     = 35
RSI_OVERBOUGHT   = 65


def round_px(v):
    return v


def _stop_below(price, level, row):
    return level * 0.99


def _stop_above(price, level, row):
    return level * 1.01


{fx.HELPER_ANCHOR}
{fx.PREP_OLD}
        row = df_daily.iloc[i]
        price, rsi = row['close'], row['rsi']
{fx.LEVEL_OLD}
        signals.append({{"idx": i, "sup": sup, "res": res}})
    return signals
'''


def build():
    out, items = fx.patch_backtest(STUB)
    assert all(s == fx.TODO for _, _, s in items), items
    ns = {}
    exec(compile(out, "stub", "exec"), ns)
    return ns


class TestLookahead(unittest.TestCase):
    """미래 차단 — 이게 깨지면 백테스트 결과가 전부 거짓이 된다."""

    def setUp(self):
        self.ns = build()
        import level_map
        self.w = level_map.DEFAULT_PIVOT_WIDTH

    def prep(self, pivots):
        import level_map
        return (level_map, sorted(pivots, key=lambda p: p[0]),
                sorted(p[0] for p in pivots), {})

    def test_pivot_invisible_until_confirmed(self):
        """인덱스 j 스윙은 j+width 봉이 마감돼야 보인다.

        확정되면 **1차 지지가 되는** 스윙을 쓴다. 멀리 있는 스윙으로는
        미래를 봤는지 값에 드러나지 않아 시험이 되지 않는다.
        """
        far = [(5, 80.0), (12, 80.4), (19, 80.2), (26, 80.1)]   # ≈80 클러스터
        near = (60, 99.0)                     # 확정되면 여기가 1차 지지
        prep = self.prep(far + [near])

        before, _ = self.ns["_lm_levels"](prep, 60 + self.w - 1, 100.0)
        after, _ = self.ns["_lm_levels"](prep, 60 + self.w, 100.0)

        self.assertLess(before, 90.0, "확정 전 스윙을 미리 썼다 — 미래를 봤다")
        self.assertAlmostEqual(after, 99.0, places=3)

    def test_too_few_pivots_returns_none(self):
        prep = self.prep([(5, 90.0), (10, 91.0), (15, 89.0)])
        self.assertEqual(self.ns["_lm_levels"](prep, 100, 100.0), (None, None))


class TestLevelSanity(unittest.TestCase):

    def setUp(self):
        self.ns = build()
        import level_map
        self.prep = (level_map,
                     [(5, 80.0), (15, 90.0), (25, 110.0), (35, 120.0), (45, 95.0)],
                     [5, 15, 25, 35, 45], {})

    def test_support_below_resistance_above(self):
        sup, res = self.ns["_lm_levels"](self.prep, 100, 100.0)
        self.assertLess(sup, 100.0)
        self.assertGreater(res, 100.0)

    def test_cache_does_not_change_result(self):
        """캐시는 속도용이다. 값이 달라지면 안 된다."""
        import level_map
        fresh = (level_map, self.prep[1], self.prep[2], {})
        for i in range(50, 120):
            for px in (85.0, 100.0, 115.0):
                cold = self.ns["_lm_levels"](
                    (level_map, self.prep[1], self.prep[2], {}), i, px)
                warm = self.ns["_lm_levels"](fresh, i, px)
                self.assertEqual(cold, warm, f"i={i} px={px}")

    def test_price_outside_all_levels(self):
        sup, res = self.ns["_lm_levels"](self.prep, 100, 1000.0)
        self.assertIsNotNone(sup)
        self.assertIsNone(res, "위에 레벨이 없으면 None 이어야 한다")
        sup, res = self.ns["_lm_levels"](self.prep, 100, 1.0)
        self.assertIsNone(sup)
        self.assertIsNotNone(res)

    def test_none_prep_is_safe(self):
        self.assertEqual(self.ns["_lm_levels"](None, 100, 100.0), (None, None))


class TestFallback(unittest.TestCase):

    def df(self):
        import pandas as pd
        n = 80
        return pd.DataFrame({
            "high": [100 + (i % 7) for i in range(n)],
            "low": [90 - (i % 5) for i in range(n)],
            "close": [95.0] * n,
            "rsi": [50.0] * n,
            "recent_low": [88.0] * n,
            "recent_high": [104.0] * n,
        })

    def test_rolling20_toggle_ignores_level_map(self):
        ns = build()
        ns["LEVEL_SOURCE"] = "rolling20"
        sigs = ns["detect_signals_vectorized"](self.df())
        self.assertTrue(sigs)
        self.assertTrue(all(s["sup"] == 88.0 and s["res"] == 104.0 for s in sigs))

    def test_levelmap_actually_changes_levels(self):
        ns = build()
        ns["LEVEL_SOURCE"] = "levelmap"
        sigs = ns["detect_signals_vectorized"](self.df())
        self.assertTrue(sigs)
        self.assertTrue(any(s["sup"] != 88.0 or s["res"] != 104.0 for s in sigs),
                        "levelmap 인데 20봉 극값과 똑같다")

    def test_missing_level_map_falls_back(self):
        """level_map 을 못 불러도 죽지 않고 예전 레벨로 간다."""
        import sys
        ns = build()
        ns["LEVEL_SOURCE"] = "levelmap"
        saved = sys.modules.pop("level_map", None)
        sys.modules["level_map"] = None      # import 시 ImportError 유발
        try:
            sigs = ns["detect_signals_vectorized"](self.df())
        finally:
            if saved is not None:
                sys.modules["level_map"] = saved
            else:
                sys.modules.pop("level_map", None)
        self.assertTrue(sigs)
        self.assertTrue(all(s["sup"] == 88.0 and s["res"] == 104.0 for s in sigs))


class TestPatchStatus(unittest.TestCase):

    def test_idempotent(self):
        once, _ = fx.patch_backtest(STUB)
        twice, items = fx.patch_backtest(once)
        self.assertEqual(once, twice)
        self.assertTrue(all(s == fx.DONE for _, _, s in items), items)

    def test_missing_anchor_is_reported(self):
        broken = STUB.replace(fx.LEVEL_OLD, "        sup = res = 0.0")
        _, items = fx.patch_backtest(broken)
        got = {n: s for n, _, s in items}
        self.assertEqual(got["⑩d"], fx.GONE)
        self.assertEqual(got["⑩b"], fx.TODO)


if __name__ == "__main__":
    unittest.main()
