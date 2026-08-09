"""patches/fix_ask_context.py 회귀 테스트.

이 패치는 봇의 **AI 시스템 프롬프트**를 고친다. 여기가 틀리면 봇이
매일 우리가 부정한 규칙으로 방향을 말한다. 두 달 걸려 알아낸 것을
봇이 반대로 떠드는 셈이다.

고정하는 것:
  · 문법이 깨지면 저장하지 않는다 (봇이 아예 안 뜬다)
  · 두 번 돌려도 두 번 넣지 않는다
  · 대상이 없으면 GONE 으로 남긴다 — 조용히 성공한 척하지 않는다
  · percentile_ctx 가 없어도 봇은 그대로 돈다
  · 새 프롬프트가 '방향 예측 금지'를 명시한다
"""

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout

try:
    from .helpers import load_patch
except ImportError:            # 봇 폴더에 그대로 복사된 경우
    from helpers import load_patch

fx = load_patch("fix_ask_context")


BOT = '''import json


def ask_claude(user_question, market_data):
    system_prompt = """너는 봇이야.

━━━ 💰 자금흐름 ━━━
• 펀비 +0.05%↑ → 롱 과열, 청산 위험
• 펀비 -0.05%↓ → 숏 과열, 스퀴즈 가능
• OI 급증 + 가격 옆걸음 → 곧 큰 움직임
• CVD↑ + 가격↑ = 진짜 상승 / CVD↓ + 가격↑ = 가짜 상승
• 롱숏 70%+ 한쪽 쏠림 → 역지표 (반대로 갈 가능성)"""
    return system_prompt


def ask_claude_conversational(user_message, chat_id, market_data=None):
    system_prompt = """너는 봇이야.

━━━ 💰 자금흐름 ━━━
펀비 +0.05%↑→롱 과열 / -0.05%↓→숏 과열
OI 급증+가격 옆걸음→큰 움직임 임박
CVD↑+가격↑=진짜 / CVD↓+가격↑=가짜
롱숏 70%+ 한쪽 쏠림→역지표

끝."""
    if market_data:
        macro = {}
        full_message = f"""{user_message}

[현재 시장 데이터]
{json.dumps(market_data, ensure_ascii=False, default=str)}

[거시경제 지수]
{json.dumps(macro, ensure_ascii=False, default=str)}"""
    else:
        full_message = user_message
    return full_message
'''


class Base(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.old = os.getcwd()
        os.chdir(self.dir.name)
        self.write(BOT)

    def tearDown(self):
        os.chdir(self.old)
        self.dir.cleanup()

    def write(self, text):
        with open("bot.py", "w", encoding="utf-8") as fp:
            fp.write(text)

    def read(self):
        with open("bot.py", encoding="utf-8") as fp:
            return fp.read()

    def run_fx(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = fx.main(["fix_ask_context.py", *args])
        return rc, out.getvalue()


class TestPreview(Base):

    def test_preview_changes_nothing(self):
        before = self.read()
        rc, out = self.run_fx()
        self.assertEqual(self.read(), before, "미리보기가 파일을 건드렸다")
        self.assertIn("적용 예정", out)

    def test_all_four_sites_are_found(self):
        rc, out = self.run_fx()
        for tag in ("⑰a", "⑰b", "⑱a", "⑱b"):
            self.assertIn(tag, out)
        self.assertEqual(out.count("적용 예정"), 4, out)


class TestApply(Base):

    def test_apply_makes_a_backup(self):
        self.run_fx("--apply")
        self.assertTrue(os.path.exists("bot.py.bak"))

    def test_result_still_parses(self):
        """문법이 깨진 파일을 저장하면 봇이 아예 안 뜬다."""
        import ast
        self.run_fx("--apply")
        ast.parse(self.read())

    def test_disproven_rules_are_gone(self):
        self.run_fx("--apply")
        s = self.read()
        self.assertNotIn("펀비 +0.05%↑ → 롱 과열, 청산 위험", s)
        self.assertNotIn("롱숏 70%+ 한쪽 쏠림→역지표", s)

    def test_measured_result_replaces_them(self):
        self.run_fx("--apply")
        s = self.read()
        self.assertIn("무작위보다 나빴", s)
        self.assertIn("60", s)

    def test_direction_calls_are_forbidden(self):
        """'펀비가 높으니 떨어진다' 를 못 하게 막는 문장이 있어야 한다."""
        self.run_fx("--apply")
        s = self.read()
        self.assertIn("방향을 예측하지 마라", s)
        self.assertIn("방향 예측에 쓰지 말 것", s)

    def test_cvd_is_marked_unmeasured_not_deleted(self):
        """안 잰 것을 '틀렸다'고 하면 그것도 거짓말이다."""
        self.run_fx("--apply")
        s = self.read()
        self.assertIn("아직 안 쟀다", s)

    def test_percentile_block_is_wired_into_the_prompt(self):
        self.run_fx("--apply")
        s = self.read()
        self.assertIn("build_percentile_block(market_data)", s)
        self.assertIn("{_pctl_block}", s)

    def test_running_twice_does_not_double(self):
        self.run_fx("--apply")
        once = self.read()
        rc, out = self.run_fx("--apply")
        self.assertEqual(self.read(), once, "두 번째 실행이 또 넣었다")
        self.assertIn("이미 적용됨", out)


class TestVerify(Base):

    def test_verify_before_apply(self):
        rc, out = self.run_fx("--verify")
        self.assertIn("□", out)

    def test_verify_after_apply(self):
        self.run_fx("--apply")
        rc, out = self.run_fx("--verify")
        self.assertNotIn("□", out)
        self.assertEqual(out.count("✅"), 4)


class TestMissingTargets(Base):

    def test_unknown_bot_is_reported_not_faked(self):
        """대상이 없으면 '대상 없음'이라고 말해야 한다.

        조용히 성공한 척하면, 사용자는 고쳐진 줄 알고 봇을 재시작한다.
        """
        self.write("def ask_claude_conversational(a, b, c=None):\n    return a\n")
        rc, out = self.run_fx()
        self.assertIn("대상 없음", out)

    def test_no_bot_file_is_an_error(self):
        os.remove("bot.py")
        rc, out = self.run_fx()
        self.assertEqual(rc, 1)
        self.assertIn("bot.py", out)

    def test_broken_result_is_not_saved(self):
        """고친 결과가 문법 오류면 저장하지 않는다."""
        import ast
        self.write(BOT.replace("    return full_message", "    return full_message\n  bad"))
        rc, out = self.run_fx("--apply")
        try:
            ast.parse(self.read())
        except SyntaxError:
            pass          # 원본이 이미 깨져 있던 것 — 우리가 덮지 않았으면 된다
        self.assertFalse(os.path.exists("bot.py.bak"),
                         "깨진 결과를 저장하고 백업까지 남겼다")


class TestHelperIsSafe(Base):
    """percentile_ctx 가 없어도 봇은 그대로 돌아야 한다.

    분석 도구 하나 때문에 /질문 이 죽으면 그건 개선이 아니다.
    """

    def test_helper_returns_empty_without_the_module(self):
        import importlib.util
        import sys
        self.run_fx("--apply")
        spec = importlib.util.spec_from_file_location("botmod", "bot.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["botmod"] = mod
        try:
            spec.loader.exec_module(mod)
            self.assertEqual(mod.build_percentile_block({"BTC": {}}), "")
        finally:
            sys.modules.pop("botmod", None)

    def test_question_prompt_still_builds(self):
        import importlib.util
        import sys
        self.run_fx("--apply")
        spec = importlib.util.spec_from_file_location("botmod2", "bot.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["botmod2"] = mod
        try:
            spec.loader.exec_module(mod)
            msg = mod.ask_claude_conversational("BTC 어때?", 1, {"BTC": {"x": 1}})
            self.assertIn("BTC 어때?", msg)
            self.assertIn("현재 시장 데이터", msg)
        finally:
            sys.modules.pop("botmod2", None)


if __name__ == "__main__":
    unittest.main()
