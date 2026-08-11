"""patches/fix_daily_record.py 회귀 테스트.

이 패치는 **앞으로 쌓을 기록**을 만든다. 여기가 틀리면 몇 주 뒤에야
안다 — 그때는 그 몇 주가 이미 비어 있고, 되돌릴 수 없다. 지나간
날의 사건은 다시 잡을 수 없기 때문이다.

그래서 조용한 실패를 특히 조심한다.

고정하는 것:
  · 봉마감 브리핑을 **절대 늦추지 않는다** (딴 실에서 돈다)
  · 기록이 터져도 브리핑은 그대로 나간다
  · 하루에 한 번만 돈다
  · 시세 함수 모양이 다르면 조용히 실패하지 않고 말한다
  · 채점 기준은 한 번만 박고, 두 번 박지 않는다
  · setup_ledger/call_journal 이 없어도 봇은 그대로 돈다
  · 문법이 깨지면 저장하지 않는다, 두 번 돌려도 두 번 넣지 않는다
"""

import ast
import io
import os
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout

try:
    from .helpers import load_patch
except ImportError:            # 봇 폴더에 그대로 복사된 경우
    from helpers import load_patch

fx = load_patch("fix_daily_record")


BOT = '''import time as _time

SLOW = {"secs": 0.0}
UNIVERSE = ["BTC/USDT", "ETH/USDT"]
OHLCV_KIND = {"mode": "ok"}


def get_paper_coins():
    return list(UNIVERSE)


def get_ohlcv(symbol, timeframe, limit=200):
    if OHLCV_KIND["mode"] == "badargs":
        raise TypeError("get_ohlcv() got an unexpected keyword argument 'limit'")
    if OHLCV_KIND["mode"] == "boom":
        raise RuntimeError("거래소 응답 없음")
    if OHLCV_KIND["mode"] == "empty":
        return []
    if SLOW["secs"]:
        _time.sleep(SLOW["secs"])
    return [1] * limit


def get_candle_briefing_multi(symbol, closed_tfs):
    coin = symbol.replace("/USDT", "")
    lines = ["머리줄"]

    # TF 엇갈림 (v4.4 애드온)
    trend_int = {}
    conflict = {"text": ""}
    if conflict["text"]:
        lines.append(conflict["text"])

    return {"coin": coin, "text": "\\n".join(lines)}
'''


def stub_modules(captured=1, scored=2, backfill=5, criteria=None, boom=None):
    """setup_ledger·call_journal 을 흉내 낸다."""
    import types
    sl = types.ModuleType("setup_ledger")
    sl.FLAT_BAND = 0.01
    sl.DEFAULT_HORIZONS = (1, 3, 7, 30)
    sl.RANGE_LOOKBACK = 60
    sl.calls = []

    def capture(coins, fn, timeframe="1d"):
        sl.calls.append(("capture", tuple(coins), timeframe))
        if boom == "capture":
            raise RuntimeError("원장 터짐")
        return [{}] * captured

    def score_pending(fn, timeframe="1d"):
        sl.calls.append(("score", timeframe))
        return [{}] * scored

    sl.capture = capture
    sl.score_pending = score_pending
    sl.summary = lambda: {"total": 100, "scored": 40, "backfill": backfill,
                          "live": 60, "symbols": 2,
                          "first_date": "2024-01-01", "last_date": "2026-01-01"}

    cj = types.ModuleType("call_journal")
    cj.registered = []
    cj.criteria_history = lambda: list(criteria or [])
    cj.register_criteria = lambda c: cj.registered.append(c)

    sys.modules["setup_ledger"] = sl
    sys.modules["call_journal"] = cj
    return sl, cj


class Base(unittest.TestCase):

    _n = 0

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.old = os.getcwd()
        os.chdir(self.dir.name)
        self.write(BOT)
        self.loaded = []

    def tearDown(self):
        os.chdir(self.old)
        for name in self.loaded:
            sys.modules.pop(name, None)
        sys.modules.pop("setup_ledger", None)
        sys.modules.pop("call_journal", None)
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
            rc = fx.main(["fix_daily_record.py", *args])
        return rc, out.getvalue()

    def load(self):
        import importlib.util
        Base._n += 1
        name = f"_dayrec{Base._n}"
        spec = importlib.util.spec_from_file_location(name, "bot.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        self.loaded.append(name)
        spec.loader.exec_module(mod)
        return mod

    def settle(self, m, secs=3.0):
        """기록 실이 끝날 때까지 기다린다."""
        end = time.time() + secs
        while m._REC["busy"] and time.time() < end:
            time.sleep(0.01)
        return not m._REC["busy"]


class TestApply(Base):

    def test_preview_changes_nothing(self):
        before = self.read()
        rc, out = self.run_fx()
        self.assertEqual(self.read(), before)
        self.assertIn("적용 예정", out)

    def test_both_sites_are_found(self):
        rc, out = self.run_fx()
        self.assertNotIn("대상 없음", out)

    def test_result_still_parses(self):
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
        self.write("def other():\n    return 1\n")
        rc, out = self.run_fx()
        self.assertEqual(rc, 1)
        self.assertIn("봇 판이 다릅니다", out)

    def test_the_helper_lands_at_module_level(self):
        """이름만으로 찾으면 부르는 자리에 끼워 넣게 된다 — 그러면
        함수 안에 모듈 수준 코드가 들어간다."""
        self.write("def caller():\n    return get_candle_briefing_multi(1, 2)\n\n"
                   + BOT)
        self.run_fx("--apply")
        tree = ast.parse(self.read())
        names = [n.name for n in tree.body if isinstance(n, ast.FunctionDef)]
        self.assertIn("record_day", names, "record_day 가 맨 위에 없다")

    def test_verify(self):
        rc, out = self.run_fx("--verify")
        self.assertIn("□", out)
        self.run_fx("--apply")
        rc, out = self.run_fx("--verify")
        self.assertNotIn("□", out)

    def test_verify_warns_about_missing_modules(self):
        rc, out = self.run_fx("--verify")
        self.assertIn("setup_ledger.py", out)
        self.assertIn("봇은 그대로 돕니다", out)


class TestRecording(Base):
    """붙인 뒤 실제로 어떻게 도나."""

    def bot(self, **kw):
        self.sl, self.cj = stub_modules(**kw)
        self.run_fx("--apply")
        return self.load()

    def brief(self, m):
        out = io.StringIO()
        with redirect_stdout(out):
            r = m.get_candle_briefing_multi("BTC/USDT", ["1d"])
            self.settle(m)
        return r, out.getvalue()

    def test_it_captures_and_scores(self):
        m = self.bot()
        self.brief(m)
        kinds = [c[0] for c in self.sl.calls]
        self.assertIn("capture", kinds)
        self.assertIn("score", kinds)

    def test_it_uses_the_paper_universe(self):
        m = self.bot()
        self.brief(m)
        cap = next(c for c in self.sl.calls if c[0] == "capture")
        self.assertEqual(cap[1], ("BTC/USDT", "ETH/USDT"))

    def test_it_reports_what_it_did(self):
        m = self.bot()
        _, out = self.brief(m)
        self.assertIn("[기록장]", out)
        self.assertIn("오늘 사건 1건", out)
        self.assertIn("채점 2건", out)

    def test_it_runs_once_a_day(self):
        m = self.bot()
        for _ in range(4):
            self.brief(m)
        self.assertEqual(sum(1 for c in self.sl.calls if c[0] == "capture"), 1)

    def test_a_new_day_runs_again(self):
        m = self.bot()
        self.brief(m)
        m._REC["day"] = "2000-01-01"
        self.brief(m)
        self.assertEqual(sum(1 for c in self.sl.calls if c[0] == "capture"), 2)

    def test_criteria_are_pinned_once(self):
        m = self.bot()
        self.brief(m)
        self.assertEqual(len(self.cj.registered), 1)
        self.assertIn("보합_밴드", self.cj.registered[0])

    def test_criteria_are_not_pinned_twice(self):
        """두 번 박히면 '기준이 바뀌었다'로 읽힌다."""
        m = self.bot(criteria=[{"이미": "있음"}])
        self.brief(m)
        self.assertEqual(self.cj.registered, [])

    def test_it_says_when_backfill_is_missing(self):
        """소급 표본이 없으면 몇 주 뒤 숫자를 무엇과 견줄지가 없다."""
        m = self.bot(backfill=0)
        _, out = self.brief(m)
        self.assertIn("python start.py setup", out)

    def test_it_stays_quiet_when_backfill_exists(self):
        m = self.bot(backfill=500)
        _, out = self.brief(m)
        self.assertNotIn("start.py setup", out)


class TestNeverBlocksTheBriefing(Base):
    """브리핑이 기록을 기다리면 그건 개선이 아니다."""

    def bot(self, **kw):
        self.sl, self.cj = stub_modules(**kw)
        self.run_fx("--apply")
        return self.load()

    def test_briefing_returns_before_recording_finishes(self):
        m = self.bot()
        m.SLOW["secs"] = 0.6
        t0 = time.time()
        with redirect_stdout(io.StringIO()):
            r = m.get_candle_briefing_multi("BTC/USDT", ["1d"])
        took = time.time() - t0
        self.assertIsNotNone(r)
        self.assertLess(took, 0.3, f"브리핑이 {took:.2f}초 기다렸다")
        m.SLOW["secs"] = 0.0
        with redirect_stdout(io.StringIO()):
            self.settle(m)

    def test_briefing_survives_a_broken_ledger(self):
        m = self.bot(boom="capture")
        out = io.StringIO()
        with redirect_stdout(out):
            r = m.get_candle_briefing_multi("BTC/USDT", ["1d"])
            self.settle(m)
        self.assertIn("머리줄", r["text"])
        self.assertIn("오늘은 건너뜁니다", out.getvalue())

    def test_briefing_survives_without_the_modules(self):
        # sys.modules 에 None 을 넣으면 import 가 ImportError 로 막힌다.
        # 저장소에 진짜 파일이 있어도 '없는 봇 폴더'를 흉내 낼 수 있다.
        sys.modules["setup_ledger"] = None
        sys.modules["call_journal"] = None
        self.run_fx("--apply")
        m = self.load()
        out = io.StringIO()
        with redirect_stdout(out):
            r = m.get_candle_briefing_multi("BTC/USDT", ["1d"])
            self.settle(m)
        self.assertIn("머리줄", r["text"])
        self.assertFalse(m._REC["on"], "못 부를 걸 매일 다시 시도한다")

    def test_briefing_survives_a_bot_without_get_paper_coins(self):
        self.sl, self.cj = stub_modules()
        self.run_fx("--apply")
        src = self.read().replace("def get_paper_coins(", "def _gone_coins(")
        self.write(src)
        m = self.load()
        out = io.StringIO()
        with redirect_stdout(out):
            r = m.get_candle_briefing_multi("BTC/USDT", ["1d"])
            self.settle(m)
        self.assertIn("머리줄", r["text"])
        self.assertIn("[기록장]", out.getvalue())


class TestOhlcvShape(Base):
    """조용히 실패하면 원장이 빈 채로 며칠이 간다. 되돌릴 수 없다."""

    def bot(self, mode):
        self.sl, self.cj = stub_modules()
        self.run_fx("--apply")
        m = self.load()
        m.OHLCV_KIND["mode"] = mode
        return m

    def go(self, m):
        out = io.StringIO()
        with redirect_stdout(out):
            m.get_candle_briefing_multi("BTC/USDT", ["1d"])
            self.settle(m)
        return out.getvalue()

    def test_wrong_signature_is_named(self):
        m = self.bot("badargs")
        out = self.go(m)
        self.assertIn("인자 모양이 다릅니다", out)
        self.assertIn("limit=개수", out)
        self.assertFalse(m._REC["on"], "고칠 때까지 매일 다시 시도한다")

    def test_a_fetch_error_is_named(self):
        m = self.bot("boom")
        out = self.go(m)
        self.assertIn("시세를 못 받았습니다", out)

    def test_empty_data_is_named(self):
        m = self.bot("empty")
        out = self.go(m)
        self.assertIn("비어 있습니다", out)

    def test_nothing_is_captured_when_the_probe_fails(self):
        """모양이 틀린 채로 capture 를 부르면 서른 종목이 전부
        조용히 실패하고 '사건 0건'만 남는다."""
        m = self.bot("boom")
        self.go(m)
        self.assertEqual([c for c in self.sl.calls if c[0] == "capture"], [])


if __name__ == "__main__":
    unittest.main()
