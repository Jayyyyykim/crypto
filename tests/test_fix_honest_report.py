"""patches/fix_honest_report.py 회귀 테스트.

⑮는 **실돈 직전 단계**를 막는 장치다. 게이트가 엉뚱한 자리에 들어가면
차단이 조용히 무력화되고, 페이퍼 기록이 오염된 채로 몇 주가 간다.

고정하는 것:
  · 차단 목록에 든 타입은 기록되지 않는다
  · 다른 타입은 그대로 통과한다 (과잉 차단이 아니다)
  · 게이트가 유효성 검사 **뒤**에 온다 (그 앞이면 순서가 뒤바뀐다)
  · 리포트에서 오해를 부르던 문장이 사라진다
"""

import unittest

try:
    from .helpers import load_patch
except ImportError:            # 봇 폴더에 그대로 복사된 경우
    from helpers import load_patch

fx = load_patch("fix_honest_report")


PAPER_STUB = f'''
{fx.PT_CONST_OLD}


def capture_signals(sigs):
    """paper_trader 와 같은 들여쓰기 깊이(코인 루프 안의 신호 루프)."""
    kept = []
    for _symbol in ("BTC/USDT",):
        for sig in sigs:
            signal_type = sig.get('type')
            entry, sl, tp1, tp2 = sig.get('entry'), sig.get('sl'), sig.get('tp1'), sig.get('tp2')
{fx.PT_GATE_OLD}
            kept.append(signal_type)
    return kept
'''

BT_STUB = f'''
def format_multi_backtest(stats):
    lines = [
        "머리말",
{fx.BT_LINE_OLD}
        "",
    ]
    return lines
'''


def build():
    out, items = fx.patch_paper(PAPER_STUB)
    assert all(s == fx.TODO for _, _, s in items), items
    ns = {}
    exec(compile(out, "stub", "exec"), ns)
    return ns


def sig(t):
    return {"type": t, "entry": 1.0, "sl": 0.9, "tp1": 1.1, "tp2": 1.2}


class TestMute(unittest.TestCase):

    def setUp(self):
        self.ns = build()

    def test_muted_type_is_not_recorded(self):
        got = self.ns["capture_signals"]([sig("MID_SHORT")])
        self.assertEqual(got, [], "확정 손실 신호가 그대로 기록됐다")

    def test_other_types_still_pass(self):
        got = self.ns["capture_signals"](
            [sig("MID_LONG"), sig("MID_SHORT"), sig("LONG_LONG"), sig("LONG_SHORT")])
        self.assertEqual(got, ["MID_LONG", "LONG_LONG", "LONG_SHORT"],
                         "차단이 과했다 — 멀쩡한 타입까지 막았다")

    def test_muted_set_contains_the_measured_one(self):
        self.assertIn("MID_SHORT", self.ns["MUTED_TYPES"])

    def test_emptying_the_set_restores_everything(self):
        """지우는 게 아니라 끄는 것 — 되돌릴 수 있어야 한다."""
        self.ns["MUTED_TYPES"] = set()
        got = self.ns["capture_signals"]([sig("MID_SHORT"), sig("MID_LONG")])
        self.assertEqual(got, ["MID_SHORT", "MID_LONG"])

    def test_incomplete_signal_still_dies_first(self):
        """값이 빠진 신호는 차단 여부와 무관하게 먼저 걸러진다."""
        got = self.ns["capture_signals"]([{"type": "MID_LONG", "entry": None,
                                           "sl": 1, "tp1": 1, "tp2": 1}])
        self.assertEqual(got, [])

    def test_gate_comes_after_validation(self):
        """게이트가 유효성 검사 앞에 오면 순서가 뒤바뀐다."""
        body = fx.PT_GATE_NEW
        self.assertLess(body.index("None in (entry"), body.index("MUTED_TYPES"))


class TestReport(unittest.TestCase):

    def test_misleading_line_is_gone(self):
        out, items = fx.patch_backtest(BT_STUB)
        self.assertEqual(items[0][2], fx.TODO)
        self.assertNotIn("기대값 +인 타입만 화이트리스트", out)
        self.assertIn("무작위 진입을 이긴 것만 후보", out)

    def test_report_still_builds(self):
        out, _ = fx.patch_backtest(BT_STUB)
        ns = {}
        exec(compile(out, "stub", "exec"), ns)
        lines = ns["format_multi_backtest"](None)
        self.assertTrue(any("diag_baseline" in x for x in lines))


class TestPatchStatus(unittest.TestCase):

    def test_idempotent_paper(self):
        once, _ = fx.patch_paper(PAPER_STUB)
        twice, items = fx.patch_paper(once)
        self.assertEqual(once, twice)
        self.assertTrue(all(s == fx.DONE for _, _, s in items), items)

    def test_idempotent_backtest(self):
        once, _ = fx.patch_backtest(BT_STUB)
        twice, items = fx.patch_backtest(once)
        self.assertEqual(once, twice)
        self.assertEqual(items[0][2], fx.DONE)

    def test_missing_anchors_are_reported(self):
        _, items = fx.patch_paper("아무것도 없는 파일")
        self.assertTrue(all(s == fx.GONE for _, _, s in items), items)
        _, items = fx.patch_backtest("아무것도 없는 파일")
        self.assertEqual(items[0][2], fx.GONE)


if __name__ == "__main__":
    unittest.main()
