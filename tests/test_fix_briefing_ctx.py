"""patches/fix_briefing_ctx.py 회귀 테스트.

봉마감 브리핑은 하루 여섯 번 저절로 온다. 여기에 붙는 줄이 틀리면
사람이 매일 틀린 문맥을 본다. /질문 보다 이쪽이 더 자주 읽힌다.

고정하는 것:
  · 문법이 깨지면 저장하지 않는다 (봇이 아예 안 뜬다)
  · 두 번 돌려도 두 번 넣지 않는다
  · percentile_ctx 가 없어도 브리핑은 그대로 나간다
  · 조용한 날에는 줄이 안 붙는다
  · 방향을 말하지 않는다
"""

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

try:
    from .helpers import load_patch
except ImportError:            # 봇 폴더에 그대로 복사된 경우
    from helpers import load_patch

fx = load_patch("fix_briefing_ctx")


BOT = '''def get_candle_briefing_multi(symbol, closed_tfs):
    coin = symbol.replace("/USDT", "")
    lines = ["머리줄"]

    # TF 엇갈림 (v4.4 애드온)
    trend_int = {}
    conflict = {"text": ""}
    if conflict["text"]:
        lines.append(conflict["text"])

    return {"coin": coin, "text": "\\n".join(lines),
            "chart": None, "sup1": None, "res1": None}
'''


class Base(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.old = os.getcwd()
        os.chdir(self.dir.name)
        self.write(BOT)

    def tearDown(self):
        os.chdir(self.old)
        for name in ("b1", "b2", "b3"):
            sys.modules.pop(name, None)
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
            rc = fx.main(["fix_briefing_ctx.py", *args])
        return rc, out.getvalue()

    def load(self, name="b1"):
        import importlib.util
        spec = importlib.util.spec_from_file_location(name, "bot.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod


class TestApply(Base):

    def test_preview_changes_nothing(self):
        before = self.read()
        rc, out = self.run_fx()
        self.assertEqual(self.read(), before)
        self.assertIn("적용 예정", out)

    def test_result_still_parses(self):
        import ast
        self.run_fx("--apply")
        ast.parse(self.read())

    def test_backup_is_made(self):
        self.run_fx("--apply")
        self.assertTrue(os.path.exists("bot.py.bak"))

    def test_running_twice_does_not_double(self):
        self.run_fx("--apply")
        once = self.read()
        rc, out = self.run_fx("--apply")
        self.assertEqual(self.read(), once)
        self.assertIn("이미 적용됨", out)

    def test_unknown_bot_is_refused(self):
        """봇 판이 다르면 조용히 성공한 척하지 않는다."""
        self.write("def get_candle_briefing_multi(a, b):\n    return None\n")
        rc, out = self.run_fx()
        self.assertEqual(rc, 1)
        self.assertIn("대상 없음", out)

    def test_verify(self):
        rc, out = self.run_fx("--verify")
        self.assertIn("□", out)
        self.run_fx("--apply")
        rc, out = self.run_fx("--verify")
        self.assertIn("✅", out)


class TestBehaviour(Base):
    """붙인 뒤 실제로 어떻게 도나."""

    def stub_pc(self, line, table=None):
        """percentile_ctx 를 흉내 낸다."""
        import types
        m = types.ModuleType("percentile_ctx")
        m.table_once = lambda: (table if table is not None else {"BTC": {"funding": {}}})
        m.live_cached = lambda coins: {"BTC": {"funding": 0.01}}
        m.one_line = lambda c, v, t, s, skip: line
        m.miscalibrated = lambda cal: set()
        sys.modules["percentile_ctx"] = m
        return m

    def tearDown(self):
        sys.modules.pop("percentile_ctx", None)
        super().tearDown()

    def test_line_is_added_when_something_is_extreme(self):
        self.stub_pc("BTC: 펀딩비(선물) 상위 · 롱숏 계정비(선물·전체계정) 하위")
        self.run_fx("--apply")
        r = self.load().get_candle_briefing_multi("BTC/USDT", ["4h"])
        self.assertIn("💠 펀딩비(선물) 상위", r["text"])
        self.assertNotIn("BTC: 펀딩비", r["text"], "코인 이름이 두 번 나온다")

    def test_quiet_day_adds_nothing(self):
        """극단인 게 없으면 한 줄도 안 붙는다. 매번 붙으면 아무도 안 읽는다."""
        self.stub_pc("")
        self.run_fx("--apply")
        r = self.load("b2").get_candle_briefing_multi("BTC/USDT", ["4h"])
        self.assertNotIn("💠", r["text"])

    def test_no_table_means_no_line(self):
        self.stub_pc("BTC: 뭔가 상위", table={})
        self.run_fx("--apply")
        r = self.load("b3").get_candle_briefing_multi("BTC/USDT", ["4h"])
        self.assertNotIn("💠", r["text"])

    def test_briefing_survives_a_broken_percentile_ctx(self):
        """분석 도구 하나 때문에 봉마감이 안 오면 그건 개선이 아니다."""
        import types
        m = types.ModuleType("percentile_ctx")

        def boom():
            raise RuntimeError("망가짐")
        m.table_once = boom
        sys.modules["percentile_ctx"] = m
        self.run_fx("--apply")
        r = self.load("b2").get_candle_briefing_multi("BTC/USDT", ["4h"])
        self.assertIsNotNone(r)
        self.assertIn("머리줄", r["text"])

    def test_briefing_survives_no_percentile_ctx_at_all(self):
        sys.modules.pop("percentile_ctx", None)
        self.run_fx("--apply")
        r = self.load("b3").get_candle_briefing_multi("BTC/USDT", ["4h"])
        self.assertIsNotNone(r)
        self.assertIn("머리줄", r["text"])


class TestWording(Base):
    """방향을 말하면 안 된다. 60가지를 재서 알 수 없다는 게 확인됐다."""

    def test_no_direction_words_in_the_patch(self):
        for word in ("매수", "매도", "진입하", "롱 유리", "숏 유리", "오를", "내릴"):
            self.assertNotIn(word, fx.NEW, f"패치가 방향을 말한다: {word}")

    def test_it_uses_the_shared_miscalibrated_set(self):
        """비교가 안 맞는 지표는 빠져야 한다 — 안 그러면 매 브리핑에
        가짜 경고가 하나씩 생긴다."""
        self.assertIn("miscalibrated", fx.NEW)

    def test_it_uses_the_cache(self):
        """4H·12H·1D 가 동시에 닫히면 같은 코인을 세 번 부른다."""
        self.assertIn("live_cached", fx.NEW)


if __name__ == "__main__":
    unittest.main()
