"""patches/fix_stop_floor.py 회귀 테스트.

핵심 불변식: **손절을 좁히는 일은 절대 없다.**
바닥을 깔 뿐이라, 원래 손절이 이미 노이즈 밖이면 그대로 둬야 한다.
좁히면 그 신호는 지금 FIB_SHORT 가 겪는 -1.166R 로 간다.
"""

import unittest

try:
    from .helpers import load_patch
except ImportError:            # 봇 폴더에 그대로 복사된 경우
    from helpers import load_patch

fx = load_patch("fix_stop_floor")
geom = load_patch("fix_signal_geometry")


def build(atr_mult=1.5):
    """⑧의 헬퍼 + ⑭의 바닥 헬퍼가 들어간 최소 모듈."""
    src = f"ATR_STOP_MULT = {atr_mult}\n\n" + geom.STOP_HELPERS.strip("\n") + "\n"
    out, items = fx.patch_backtest(src)
    assert items[0][2] == fx.TODO, items
    ns = {}
    exec(compile(out, "stub", "exec"), ns)
    return ns


class TestFloor(unittest.TestCase):

    def setUp(self):
        self.ns = build()

    def row(self, atr):
        import pandas as pd
        return pd.Series({"atr": atr})

    def test_floor_widens_a_too_tight_stop(self):
        """FIB_SHORT 의 실제 모양 — 진입가 대비 0.5% 손절."""
        price, atr = 100.0, 3.0
        orig = price * 1.005                      # 0.5%
        got = max(orig, self.ns["_floor_above"](price, self.row(atr)))
        self.assertAlmostEqual(got, 100.0 + 4.5)
        self.assertGreater(got - price, orig - price, "넓어져야 한다")

    def test_floor_never_narrows(self):
        """원래 손절이 이미 노이즈 밖이면 손대지 않는다 (FIB_LONG 등)."""
        price, atr = 100.0, 1.0
        far_long = 80.0                            # 이미 20% 아래
        self.assertAlmostEqual(
            min(far_long, self.ns["_floor_below"](price, self.row(atr))), far_long)
        far_short = 130.0
        self.assertAlmostEqual(
            max(far_short, self.ns["_floor_above"](price, self.row(atr))), far_short)

    def test_missing_atr_keeps_original(self):
        """ATR을 못 구하면 min/max 에서 무시돼 원래 손절이 살아난다."""
        import pandas as pd
        for bad in (float("nan"), 0.0, -1.0):
            r = pd.Series({"atr": bad})
            self.assertEqual(self.ns["_floor_below"](100.0, r), float("inf"))
            self.assertEqual(self.ns["_floor_above"](100.0, r), float("-inf"))
            self.assertAlmostEqual(min(99.0, self.ns["_floor_below"](100.0, r)), 99.0)
            self.assertAlmostEqual(max(101.0, self.ns["_floor_above"](100.0, r)), 101.0)
        r = pd.Series({"close": 1.0})              # atr 칼럼 자체가 없음
        self.assertEqual(self.ns["_floor_below"](100.0, r), float("inf"))

    def test_toggle_off_disables_floor(self):
        ns = build(atr_mult=0.0)
        self.assertEqual(ns["_floor_below"](100.0, self.row(3.0)), float("inf"))
        self.assertEqual(ns["_floor_above"](100.0, self.row(3.0)), float("-inf"))

    def test_long_and_short_are_mirrors(self):
        r = self.row(2.0)
        self.assertAlmostEqual(100.0 - self.ns["_floor_below"](100.0, r),
                               self.ns["_floor_above"](100.0, r) - 100.0)


class TestSites(unittest.TestCase):
    """각 신호의 손절 줄이 min/max 로 감싸졌는지 — 방향이 바뀌면 재앙이다."""

    def test_longs_use_min_shorts_use_max(self):
        for num, label, old, new in fx.SITES:
            short = any(k in label for k in ("숏", "악마"))
            if short:
                self.assertIn("max(", new, f"{num} {label} 는 숏인데 max 가 아니다")
                self.assertIn("_floor_above", new, f"{num} {label}")
            else:
                self.assertIn("min(", new, f"{num} {label} 는 롱인데 min 이 아니다")
                self.assertIn("_floor_below", new, f"{num} {label}")

    def test_original_level_is_preserved_in_each_site(self):
        """원래 손절 식이 그대로 남아 있어야 한다 — 대체가 아니라 바닥이다."""
        for num, label, old, new in fx.SITES:
            core = old.split("round_px(", 1)[1].rsplit(")", 1)[0]
            self.assertIn(core, new, f"{num} {label} 에서 원래 손절 식이 사라졌다")


class TestPatchStatus(unittest.TestCase):

    def stub(self):
        return ("ATR_STOP_MULT = 1.5\n\n" + geom.STOP_HELPERS.strip("\n") + "\n\n"
                + "\n".join(old for _, _, old, _ in fx.SITES) + "\n")

    def test_idempotent(self):
        once, _ = fx.patch_backtest(self.stub())
        twice, items = fx.patch_backtest(once)
        self.assertEqual(once, twice)
        self.assertTrue(all(s == fx.DONE for _, _, s in items), items)

    def test_requires_signal_geometry_first(self):
        """⑧ 없이 적용하면 NameError 가 난다 — 그 전에 막아야 한다."""
        _, items = fx.patch_backtest("SUPPORT_ZONE_PCT = 0.5\n")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0][2], fx.GONE)
        self.assertIn("fix_signal_geometry", items[0][1])

    def test_partial_file_reports_missing_sites(self):
        src = self.stub().replace(fx.SITES[1][2], "        pass")
        _, items = fx.patch_backtest(src)
        got = {n: s for n, _, s in items}
        self.assertEqual(got["⑭b"], fx.GONE)
        self.assertEqual(got["⑭a"], fx.TODO)


if __name__ == "__main__":
    unittest.main()
