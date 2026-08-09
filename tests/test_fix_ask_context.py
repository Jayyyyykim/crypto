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
from concurrent.futures import ThreadPoolExecutor, as_completed


def quick_analysis(symbol):
    timeframes = {"1h": "🕐1시간"}
    results = {}
    for tf, name in timeframes.items():
        data = analyze_timeframe(symbol, tf)
        if data:
            # 핵심 필드만 추출 (토큰 절약)
            results[name] = {
                "trend"     : data.get("trend"),
                "rsi"       : data.get("rsi"),
                "price"     : data.get("price"),
                "support"   : data.get("support"),
                "resistance": data.get("resistance"),
                "signal"    : data.get("signal"),
            }
    return results


def build_market_brief(coins, timeout_per_coin=15):
    market_data = {}
    with ThreadPoolExecutor(max_workers=len(coins)) as ex:
        futures = {ex.submit(quick_analysis, sym): sym for sym in coins}
        for fut in as_completed(futures, timeout=timeout_per_coin * len(coins)):
            sym = futures[fut]
            c = sym.replace("/USDT", "")
            try:
                market_data[c] = fut.result(timeout=timeout_per_coin)
            except Exception as e:
                print(f"[build_market_brief] {c} 실패: {type(e).__name__}: {e}")
                market_data[c] = {"error": "분석 실패"}
    return market_data


def handle_message(text, reply_chat_id=None):
    tl = text.lower().strip()
    if tl.startswith("/알림"):
        return "알림"

    elif tl.startswith("/리스크"):
        parsed = parse_risk_command(text)
        if not parsed:
            return "사용법"
        return "계산기"

    elif tl in ["/리스크", "/risk", "/리스크현황"]:
        return get_risk_report()
    return None


def ask_claude(user_question, market_data):
    system_prompt = """너는 봇이야.

━━━ 📊 매매 철학 ━━━
• 구조가 먼저 (주봉 → 일봉 → 4H → 1H → 15분)
• 손익비 1:2 이상, 분할 진입, 몰빵 금지
• BTC가 방향, 알트는 그림자
• 비위남 시그널: 👼 천사 롱 / 😈 악마 숏 / 🟢 미드롱 / 🔴 미드숏
• 김태욱 피보나치: 0.382 / 0.618이 핵심 진입/지지

━━━ 💰 자금흐름 ━━━
• 펀비 +0.05%↑ → 롱 과열, 청산 위험
• 펀비 -0.05%↓ → 숏 과열, 스퀴즈 가능
• OI 급증 + 가격 옆걸음 → 곧 큰 움직임
• CVD↑ + 가격↑ = 진짜 상승 / CVD↓ + 가격↑ = 가짜 상승
• 롱숏 70%+ 한쪽 쏠림 → 역지표 (반대로 갈 가능성)"""
    return system_prompt


def ask_claude_conversational(user_message, chat_id, market_data=None):
    system_prompt = """너는 봇이야.

━━━ 📈 트레이딩 배경 지식 ━━━
• 구조 우선 (주봉→일봉→4H)
• 손익비 1:2 이상, 분할 진입, 몰빵 X
• BTC가 방향, 알트는 그림자
• 비위남 시그널: 👼 천사 롱 / 😈 악마 숏 / 🟢 미드롱 / 🔴 미드숏

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

    def test_every_site_is_found(self):
        rc, out = self.run_fx()
        for tag in ("⑰a", "⑰b", "⑱a", "⑱b", "⑲a", "⑲b", "⑳", "㉑", "㉒"):
            self.assertIn(tag, out)
        self.assertEqual(out.count("적용 예정"), len(fx.SITES), out)


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
        self.assertEqual(out.count("✅"), len(fx.SITES))


class TestPriceRulesToo(Base):
    """자금흐름만 고쳤더니 AI 가 이번엔 가격 구조로 진입가를 제시했다.

        "$65,266~65,400 돌파 확인 후 재진입이 안전할 듯"

    그 규칙도 쟀다 — 돌파롱 20일신고 -0.211R. 45가지 전부 실패.
    특히 '비위남 4종'은 우리가 직접 잰 그 신호들이고, 미드숏은
    무작위보다 **유의하게** 나빴다.
    """

    def test_the_four_bot_signals_are_no_longer_taught_as_signals(self):
        self.run_fx("--apply")
        s = self.read()
        self.assertNotIn("• 비위남 시그널: 👼 천사 롱", s)
        self.assertIn("비위남 4종", s)

    def test_the_confirmed_loser_is_named(self):
        """미드숏이 무작위보다 나쁘다는 건 유일하게 확정된 결과다."""
        self.run_fx("--apply")
        self.assertIn("-0.280R", self.read())

    def test_entry_price_calls_are_forbidden(self):
        self.run_fx("--apply")
        s = self.read()
        self.assertIn("진입가·손절가를 단정적으로 제시하지 마라", s)
        self.assertIn("돌파 확인 후 진입", s)

    def test_risk_math_on_a_user_plan_is_still_allowed(self):
        """사용자가 계획을 말하면 손익비 계산은 해줘야 한다 — 그건 산수다.

        전부 막으면 봇이 쓸모없어진다. 예측과 산수를 갈라야 한다.
        """
        self.run_fx("--apply")
        s = self.read()
        self.assertIn("산수지 예측이 아니다", s)

    def test_risk_management_advice_is_kept(self):
        self.run_fx("--apply")
        self.assertIn("손익비 1:2 이상, 분할 진입, 몰빵 금지", self.read())


class TestRealBugs(Base):
    """프롬프트 말고 코드 쪽 — bot.py 를 읽다 찾은 것들."""

    def load(self, name):
        import importlib.util
        import sys
        spec = importlib.util.spec_from_file_location(name, "bot.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod

    def test_risk_command_was_dead_and_now_works(self):
        """/리스크 분기가 두 번 나오는데 앞의 startswith 가 다 먹었다.

        그래서 그냥 /리스크 를 치면 현황 대신 계산기 사용법이 나오고,
        /리스크현황 은 아예 죽어 있었다.
        """
        before = self.load("b_before")
        before.parse_risk_command = lambda t: None
        before.get_risk_report = lambda: "현황"
        self.assertEqual(before.handle_message("/리스크"), "사용법")

        self.run_fx("--apply")
        after = self.load("b_after")
        after.parse_risk_command = lambda t: None
        after.get_risk_report = lambda: "현황"
        self.assertEqual(after.handle_message("/리스크"), "현황")
        self.assertEqual(after.handle_message("/리스크현황"), "현황")

    def test_the_calculator_still_works(self):
        self.run_fx("--apply")
        mod = self.load("b_calc")
        mod.parse_risk_command = lambda t: (1, 2, 3, 4, 5, 6)
        self.assertEqual(mod.handle_message("/리스크 100000 83000 80000 10"), "계산기")

    def test_null_fields_are_gone(self):
        """analyze_timeframe 은 'price'·'signal' 칼럼을 주지 않는다.

        그대로 두면 /질문 이 AI 에게 매 타임프레임마다 null 을 보낸다.
        있는 척하는 빈칸은 없는 것보다 나쁘다.
        """
        before = self.load("q_before")
        before.analyze_timeframe = lambda s, tf: {"trend": "상승", "rsi": 58,
                                                  "current_price": 65192,
                                                  "support": 1, "resistance": 2}
        got = list(before.quick_analysis("BTC/USDT").values())[0]
        self.assertIsNone(got["price"], "원본이 이미 멀쩡하면 이 패치는 필요없다")

        self.run_fx("--apply")
        after = self.load("q_after")
        after.analyze_timeframe = before.analyze_timeframe
        got2 = list(after.quick_analysis("BTC/USDT").values())[0]
        self.assertEqual(got2["price"], 65192)
        self.assertNotIn("signal", got2)

    def test_question_survives_a_timeout(self):
        """as_completed 는 반복자 자체가 TimeoutError 를 던진다.

        안 잡으면 /질문 이 통째로 죽고, 사용자는 '분석 중...'만 받은 채
        답을 영영 못 받는다.
        """
        import threading
        self.run_fx("--apply")
        mod = self.load("t_after")
        # time.sleep 을 쓰지 않는다 — 다른 테스트 파일이 그걸 무력화해
        # 놓으면 이 테스트가 조용히 무의미해진다. Event.wait 은 안 건드린다.
        mod.quick_analysis = lambda sym: (threading.Event().wait(1.5), {"ok": 1})[1]
        got = mod.build_market_brief(["BTC/USDT", "ETH/USDT"], timeout_per_coin=0.2)
        self.assertEqual(set(got), {"BTC", "ETH"})
        for v in got.values():
            self.assertIn("error", v)


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
