"""patches/fix_price_precision.py 의 상태 판정 회귀 테스트.

예전 판은 파일당 한 줄만 찍었다. ①②③만 적용된 파일이 "이미 적용됨"으로
보였고, 리포트를 바꾸는 ④⑤⑥이 빠진 걸 아무도 눈치채지 못했다. 백테스트
결과가 패치 전과 바이트 단위로 같게 나온 원인이 그것이었다.

여기서 고정하는 것:
  · 절반만 적용된 파일은 나머지를 '적용 예정'으로 보고해야 한다
  · 두 번 적용해도 파일이 더 바뀌지 않아야 한다 (멱등)
  · ③의 앵커를 못 찾아도 ④⑤⑥은 계속 진행돼야 한다
"""

import unittest

try:
    from .helpers import load_patch
except ImportError:            # 봇 폴더에 그대로 복사된 경우
    from helpers import load_patch

fx = load_patch("fix_price_precision")


BACKTEST_STUB = f'''"""가짜 backtest.py"""
{fx.BT_ANCHOR}

{fx.BT_ZONE_OLD}


def calc_stats(trades):
    total = len(trades)
    total_r = sum(t["r"] for t in trades)
    avg_win = 1.0
    avg_loss = -1.0
    win_rate = 50.0
{fx.BT_EXP_OLD}
    for t in trades:
        entry = round(t["entry"], 2)
        sl  = round(t["sl"], 2)
    return {{"total": total, "expectancy": expectancy}}


def format_multi_backtest(stats):
    for st in stats:
{fx.BT_VERDICT_OLD}
        yield emoji, verdict
'''


def states(items):
    return {num: state for num, _, state in items}


class TestBacktestPatch(unittest.TestCase):

    def test_fresh_file_reports_all_pending(self):
        _, items = fx.patch_backtest(BACKTEST_STUB)
        self.assertEqual(states(items),
                         {"③": fx.TODO, "④": fx.TODO, "⑤": fx.TODO, "⑥": fx.TODO})

    def test_half_applied_file_reports_the_rest_pending(self):
        """①②③만 들어간 상태 — 실제로 사용자가 있던 자리."""
        once, _ = fx.patch_backtest(BACKTEST_STUB)
        # ③만 남기고 ④⑤⑥은 되돌린다
        half = once
        half = half.replace(fx.BT_GATE_CONST_NEW.split("\nSUPPORT")[0] + "\n\n", "")
        half = half.replace(fx.BT_EXP_NEW, fx.BT_EXP_OLD)
        half = half.replace(fx.BT_ZONE_NEW, fx.BT_ZONE_OLD)
        half = half.replace(fx.BT_VERDICT_NEW, fx.BT_VERDICT_OLD)

        _, items = fx.patch_backtest(half)
        got = states(items)
        self.assertEqual(got["③"], fx.DONE, "반올림은 이미 적용된 것으로 읽혀야 한다")
        for num in ("④", "⑤", "⑥"):
            self.assertEqual(got[num], fx.TODO,
                             f"{num}이 미적용인데 미적용으로 보고되지 않았다")

    def test_idempotent(self):
        once, _ = fx.patch_backtest(BACKTEST_STUB)
        twice, items = fx.patch_backtest(once)
        self.assertEqual(once, twice, "두 번째 적용에서 파일이 또 바뀌었다")
        self.assertTrue(all(s == fx.DONE for s in states(items).values()),
                        states(items))

    def test_missing_round_px_anchor_does_not_abort_report_fixes(self):
        """③의 앵커가 없어도 ④⑤⑥은 적용돼야 한다.

        예전 판은 여기서 곧장 return 해서 셋이 통째로 조용히 건너뛰어졌다.
        """
        no_anchor = BACKTEST_STUB.replace(fx.BT_ANCHOR, "SLIPPAGE = 0.001")
        out, items = fx.patch_backtest(no_anchor)
        got = states(items)
        self.assertEqual(got["③"], fx.GONE)
        for num in ("④", "⑤", "⑥"):
            self.assertEqual(got[num], fx.TODO)
        self.assertIn("MIN_TRADES_TO_TRUST = 30", out)
        self.assertIn("SUPPORT_ZONE_PCT = 0.5", out)

    def test_sample_gate_precedes_expectancy_colour(self):
        """표본이 모자라면 기대값 부호로 색을 매기지 않는다."""
        out, _ = fx.patch_backtest(BACKTEST_STUB)
        ns = {}
        exec(compile(out, "backtest_stub", "exec"), ns)
        got = list(ns["format_multi_backtest"]([{"total": 3, "expectancy": 0.31}]))
        self.assertEqual(got[0][0], "⚪")
        self.assertIn("표본 부족", got[0][1])

        got = list(ns["format_multi_backtest"]([{"total": 30, "expectancy": 0.31}]))
        self.assertEqual(got[0][0], "🟢")

    def test_expectancy_is_mean_r(self):
        out, _ = fx.patch_backtest(BACKTEST_STUB)
        ns = {}
        exec(compile(out, "backtest_stub", "exec"), ns)
        trades = [{"r": 2.0, "entry": 1, "sl": 1}] + [{"r": 0.0, "entry": 1, "sl": 1}] * 3
        self.assertAlmostEqual(ns["calc_stats"](trades)["expectancy"], 0.5)

    def test_zone_matches_bot_entry_zone(self):
        out, _ = fx.patch_backtest(BACKTEST_STUB)
        ns = {}
        exec(compile(out, "backtest_stub", "exec"), ns)
        # 봇의 ENTRY_ZONE = 0.005 → 퍼센트로 0.5
        self.assertEqual(ns["SUPPORT_ZONE_PCT"], 0.5)


BOT_STUB = "\n".join(old for old, _ in fx.BOT_ROUNDING) + "\n\n" + fx.BOT_PRICE_OLD + "\n"


class TestBotPatch(unittest.TestCase):

    def test_fresh_then_idempotent(self):
        once, items = fx.patch_bot(BOT_STUB)
        self.assertEqual(states(items), {"①": fx.TODO, "②": fx.TODO})
        twice, items = fx.patch_bot(once)
        self.assertEqual(once, twice)
        self.assertEqual(states(items), {"①": fx.DONE, "②": fx.DONE})

    def test_partial_rounding_is_not_reported_as_done(self):
        once, _ = fx.patch_bot(BOT_STUB)
        # 한 줄만 예전 형태로 되돌린다
        old, new = fx.BOT_ROUNDING[0]
        partial = once.replace(new, old, 1)
        _, items = fx.patch_bot(partial)
        self.assertEqual(states(items)["①"], fx.TODO)


if __name__ == "__main__":
    unittest.main()
