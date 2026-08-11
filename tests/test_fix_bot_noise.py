"""patches/fix_bot_noise.py 회귀 테스트.

이 패치는 **로그를 조용하게 만드는 것**이 목적이다. 그래서 잘못 만들면
증상이 없다 — 걸러 낸 코인이 진짜 코인이었어도, 판정이 None 으로
떨어져도, 화면은 오히려 깨끗해 보인다. 그래서 여기서 못 잡으면
아무 데서도 못 잡는다.

고정하는 것:
  · ㉔ 없는 심볼만 거른다. 목록을 못 받으면 **거르지 않는다**
  · ㉔ **거르는 규칙과 부르는 규칙이 같다** (어긋나면 로그가 안 조용해진다)
  · ㉔ 같은 제외 목록을 매번 다시 찍지 않는다 (그것도 소음이다)
  · ㉕ NaN 이 섞이면 '혼조'가 아니라 None
  · ㉖ 모델 이름이 나머지 호출부와 같다
  · ㉖ API 가 무슨 말을 했는지 로그에 남는다
  · 문법이 깨지면 저장하지 않는다, 두 번 돌려도 두 번 넣지 않는다
"""

import ast
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

import pandas as pd

try:
    from .helpers import load_patch
except ImportError:            # 봇 폴더에 그대로 복사된 경우
    from helpers import load_patch

fx = load_patch("fix_bot_noise")


BOT = '''class _Ex:
    fail = False
    calls = 0
    syms = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "ADA/USDT", "XRP/USDT:USDT"]

    def load_markets(self):
        _Ex.calls += 1
        if _Ex.fail:
            raise RuntimeError("거래소 응답 없음")
        return {s: {} for s in _Ex.syms}


exchange = _Ex()
PAPER_COINS = []
PAPER_MAX_COINS = 30
PAPER_MIN_USDT_VOLUME = 0
UNIVERSE = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "ADA/USDT", "SKHYNIX/USDT"]


def select_paper_universe(coins, max_coins=0, min_usdt_volume=0):
    return list(UNIVERSE)


def get_paper_coins():
    """최신 전 종목 스캔 결과에서 페이퍼 대상만 동적으로 선별."""
    return select_paper_universe(
        PAPER_COINS,
        max_coins=PAPER_MAX_COINS,
        min_usdt_volume=PAPER_MIN_USDT_VOLUME,
    )


def get_smma_cluster(close, smma_vals):
    vals    = list(smma_vals.values())
    current = round(close.iloc[-1], 2)

    sorted_desc = all(vals[i] >= vals[i+1] for i in range(len(vals)-1))
    sorted_asc  = all(vals[i] <= vals[i+1] for i in range(len(vals)-1))
    spread_pct  = round((max(vals) - min(vals)) / current * 100, 2)
    state = "정배열" if sorted_desc else ("역배열" if sorted_asc else "혼조")
    return {"spread_pct": spread_pct, "state": state}


RESPONSE = {"content": [{"text": "코멘트"}]}


class _R:
    def json(self):
        return RESPONSE


def get_claude_level_comment(prompt):
    try:
        body = {
            "model"     : "claude-sonnet-4-20250514",
            "max_tokens": 300,
            "messages"  : [{"role": "user", "content": prompt}],
        }
        SENT.append(body)
        r = _R()
        result = r.json()
        comment = result['content'][0]['text']
        return comment
    except Exception as e:
        print(f"Claude 레벨 코멘트 오류: {e}")
        return None


SENT = []
'''


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
            rc = fx.main(["fix_bot_noise.py", *args])
        return rc, out.getvalue()

    def load(self):
        """고친 bot.py 를 새 모듈로 불러온다."""
        import importlib.util
        Base._n += 1
        name = f"_botnoise{Base._n}"
        spec = importlib.util.spec_from_file_location(name, "bot.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        self.loaded.append(name)
        spec.loader.exec_module(mod)
        return mod


class TestApply(Base):

    def test_preview_changes_nothing(self):
        before = self.read()
        rc, out = self.run_fx()
        self.assertEqual(self.read(), before)
        self.assertIn("적용 예정", out)
        self.assertEqual(rc, 0)

    def test_every_fresh_site_is_found(self):
        """㉔u 는 이미 붙인 판을 갈아 끼우는 자리라, 새 봇에는
        해당 없음이 정상이다. 나머지는 전부 찾아야 한다."""
        rc, out = self.run_fx()
        body = [l for l in out.splitlines() if l.startswith("    ")]
        for tag in ("㉔ ", "㉕ ", "㉖a", "㉖b"):
            line = next(l for l in body if tag in l)
            self.assertIn("적용 예정", line, f"{tag} 자리를 못 찾았다")
        self.assertEqual(out.count("대상 없음"), 1)

    def test_the_loose_version_gets_upgraded(self):
        """느슨한 판을 이미 붙인 봇이 있다. 그 판은 표기가 다른
        심볼을 봐줘서 로그가 안 조용해진다 — 갈아 끼워야 한다."""
        self.run_fx("--apply")
        loose = self.read().replace(fx.TIGHT_NEW, fx.LOOSE_OLD)
        self.assertIn('(s + ":USDT") in have', loose)
        self.write(loose)
        rc, out = self.run_fx()
        line = next(l for l in out.splitlines() if "㉔u" in l)
        self.assertIn("적용 예정", line)
        self.run_fx("--apply")
        self.assertIn("실제로 부를 때 쓰는 규칙이 같아야", self.read())
        self.assertIn("market_symbol", self.read())

    def test_result_still_parses(self):
        self.run_fx("--apply")
        ast.parse(self.read())

    def test_backup_is_made(self):
        self.run_fx("--apply")
        self.assertTrue(os.path.exists("bot.py.bak"))
        with open("bot.py.bak", encoding="utf-8") as fp:
            self.assertEqual(fp.read(), BOT)

    def test_running_twice_does_not_double(self):
        self.run_fx("--apply")
        once = self.read()
        rc, out = self.run_fx("--apply")
        self.assertEqual(self.read(), once)
        self.assertIn("이미 적용됨", out)

    def test_unknown_bot_is_refused(self):
        """봇 판이 다르면 조용히 성공한 척하지 않는다."""
        self.write("def get_paper_coins():\n    return []\n")
        rc, out = self.run_fx()
        self.assertEqual(rc, 1)
        self.assertIn("대상 없음", out)
        self.assertIn("봇 판이 다릅니다", out)

    def test_verify(self):
        rc, out = self.run_fx("--verify")
        self.assertIn("□", out)
        self.run_fx("--apply")
        rc, out = self.run_fx("--verify")
        self.assertNotIn("□", out)
        self.assertEqual(out.count("✅"), len(fx.SITES))


class TestUniverse(Base):
    """㉔ 없는 심볼만 거른다."""

    def bot(self):
        self.run_fx("--apply")
        m = self.load()
        m.exchange.__class__.calls = 0
        m.exchange.__class__.fail = False
        return m

    def test_unfetchable_symbols_are_dropped(self):
        m = self.bot()
        with redirect_stdout(io.StringIO()):
            got = m.get_paper_coins()
        self.assertNotIn("SKHYNIX/USDT", got)
        self.assertIn("BTC/USDT", got)

    def test_it_does_not_filter_when_almost_everything_would_go(self):
        """거의 다 떨어지면 목록과 조회 방식이 안 맞는 것이다
        (예: 이 객체가 현물을 안 싣는다). 그때 거르면 페이퍼가 멈춘다."""
        m = self.bot()
        m.exchange.__class__.syms = ["BTC/USDT"]
        m._MARKETS["syms"] = set()
        out = io.StringIO()
        with redirect_stdout(out):
            got = m.get_paper_coins()
        self.assertEqual(len(got), 5, "전부 걸러서 페이퍼가 멈췄다")
        self.assertIn("거르지 않습니다", out.getvalue())

    def test_perp_only_symbol_is_dropped_but_named(self):
        """거래소가 'XRP/USDT:USDT' 로만 실으면 XRP 는 진짜 종목이지만
        **봇은 'XRP/USDT' 라는 이름으로 못 부른다.** 봐주면 매시간
        네 번 실패하는 게 그대로다 — 로그가 안 조용해진다."""
        m = self.bot()
        m.UNIVERSE = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT"]
        out = io.StringIO()
        with redirect_stdout(out):
            got = m.get_paper_coins()
        self.assertNotIn("XRP/USDT", got)
        self.assertIn("XRP(→XRP/USDT:USDT?)", out.getvalue())

    def test_it_says_what_it_dropped(self):
        m = self.bot()
        out = io.StringIO()
        with redirect_stdout(out):
            m.get_paper_coins()
        self.assertIn("제외 1종", out.getvalue())
        self.assertIn("SKHYNIX", out.getvalue())

    def test_a_tokenized_stock_that_is_listed_survives(self):
        """토큰화 주식도 코인 거래소에서 매매된다. 이름으로 판단하면
        멀쩡히 거래되는 종목을 버린다."""
        m = self.bot()
        m.exchange.__class__.syms = ["BTC/USDT", "SKHYNIX/USDT"]
        m._MARKETS["syms"] = set()
        m.UNIVERSE = ["BTC/USDT", "SKHYNIX/USDT"]
        m.exchange.__class__.calls = 0
        out = io.StringIO()
        with redirect_stdout(out):
            got = m.get_paper_coins()
        self.assertIn("SKHYNIX/USDT", got)
        self.assertNotIn("제외", out.getvalue())

    def test_a_notation_difference_is_pointed_out(self):
        """조용히 버리는 게 이 검사의 유일한 위험이다 — 화면에 남긴다."""
        m = self.bot()
        m.exchange.__class__.syms = ["BTC/USDT", "ETH/USDT", "SOL/USDT",
                                     "AAOIX/USDT"]
        m._MARKETS["syms"] = set()
        m.UNIVERSE = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "AAOI/USDT"]
        out = io.StringIO()
        with redirect_stdout(out):
            m.get_paper_coins()
        self.assertIn("AAOI(→AAOIX/USDT?)", out.getvalue())
        self.assertIn("표기만 다른 것일 수 있습니다", out.getvalue())

    def test_no_arrow_when_nothing_is_similar(self):
        m = self.bot()
        out = io.StringIO()
        with redirect_stdout(out):
            m.get_paper_coins()
        self.assertNotIn("→", out.getvalue())
        self.assertNotIn("표기만 다른", out.getvalue())

    def test_it_does_not_repeat_the_same_list(self):
        """매시간 같은 줄을 다시 찍으면 그것도 소음이다."""
        m = self.bot()
        with redirect_stdout(io.StringIO()):
            m.get_paper_coins()
        out = io.StringIO()
        with redirect_stdout(out):
            m.get_paper_coins()
        self.assertNotIn("거래소에 없어 제외", out.getvalue())

    def test_a_new_bad_symbol_is_reported(self):
        m = self.bot()
        with redirect_stdout(io.StringIO()):
            m.get_paper_coins()
        m.UNIVERSE = ["BTC/USDT", "ETH/USDT", "SOL/USDT",
                      "SKHYNIX/USDT", "SAMSUNG/USDT"]
        out = io.StringIO()
        with redirect_stdout(out):
            m.get_paper_coins()
        self.assertIn("SAMSUNG", out.getvalue())

    def test_market_list_is_cached(self):
        """한 시간에 한 번이면 된다. 매 스캔마다 부르면 그게 더 느리다."""
        m = self.bot()
        with redirect_stdout(io.StringIO()):
            m.get_paper_coins()
            m.get_paper_coins()
            m.get_paper_coins()
        self.assertEqual(m.exchange.__class__.calls, 1)

    def test_it_falls_through_when_the_list_cannot_be_fetched(self):
        """이 검사 때문에 페이퍼가 멈추면 그건 개선이 아니다."""
        m = self.bot()
        m.exchange.__class__.fail = True
        out = io.StringIO()
        with redirect_stdout(out):
            got = m.get_paper_coins()
        self.assertEqual(got, list(m.UNIVERSE))
        self.assertIn("그대로 갑니다", out.getvalue())

    def test_nothing_is_dropped_when_everything_is_listed(self):
        m = self.bot()
        m.UNIVERSE = ["BTC/USDT", "ETH/USDT"]
        out = io.StringIO()
        with redirect_stdout(out):
            got = m.get_paper_coins()
        self.assertEqual(got, ["BTC/USDT", "ETH/USDT"])
        self.assertNotIn("제외", out.getvalue())


class TestNear(unittest.TestCase):
    """이름이 비슷한 것 찾기 — 표기 차이를 사람 눈에 보여 주는 부분."""

    HAVE = ["BTC/USDT", "AAOIX/USDT", "1000PEPE/USDT:USDT", "SOL/USDT"]

    def test_suffix_form(self):
        self.assertEqual(fx._near("AAOI", self.HAVE), ["AAOIX/USDT"])

    def test_prefix_form(self):
        self.assertEqual(fx._near("PEPE", self.HAVE), ["1000PEPE/USDT:USDT"])

    def test_exact_match_is_not_a_suggestion(self):
        self.assertEqual(fx._near("BTC", self.HAVE), [])

    def test_nothing_similar(self):
        self.assertEqual(fx._near("SKHYNIX", self.HAVE), [])


class TestSymbolLookup(unittest.TestCase):
    """--symbols 는 봇을 안 켜고 거래소에만 물어본다."""

    def setUp(self):
        self.real = fx.lookup
        self.seen = []
        fx.lookup = lambda names, ex_name="bybit": (self.seen.append(names) or 0)

    def tearDown(self):
        fx.lookup = self.real

    def run_fx(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = fx.main(["fix_bot_noise.py", *args])
        return rc, out.getvalue()

    def test_space_form(self):
        self.run_fx("--symbols", "SKHYNIX,AAOI")
        self.assertEqual(self.seen, [["SKHYNIX", "AAOI"]])

    def test_equals_form(self):
        self.run_fx("--symbols=SKHYNIX")
        self.assertEqual(self.seen, [["SKHYNIX"]])

    def test_it_does_not_touch_bot_py(self):
        """이건 조회다. bot.py 가 없어도 돌아야 한다."""
        rc, out = self.run_fx("--symbols", "BTC")
        self.assertEqual(rc, 0)


class TestSmma(Base):
    """㉕ NaN 이 섞이면 판정이 조용히 틀린다."""

    def bot(self):
        self.run_fx("--apply")
        return self.load()

    @staticmethod
    def close(px=100.0):
        return pd.Series([px])

    def test_clean_values_still_work(self):
        m = self.bot()
        r = m.get_smma_cluster(self.close(), {"a": 110.0, "b": 105.0,
                                              "c": 102.0, "d": 100.0})
        self.assertEqual(r["state"], "정배열")
        self.assertEqual(r["spread_pct"], 10.0)

    def test_nan_in_values_returns_none(self):
        m = self.bot()
        r = m.get_smma_cluster(self.close(), {"a": 110.0, "b": float("nan"),
                                              "c": 102.0, "d": 100.0})
        self.assertIsNone(r, "NaN 이 섞였는데 '혼조'로 답하면 조용히 틀린다")

    def test_nan_price_returns_none(self):
        m = self.bot()
        r = m.get_smma_cluster(self.close(float("nan")),
                               {"a": 110.0, "b": 105.0, "c": 102.0, "d": 100.0})
        self.assertIsNone(r)

    def test_zero_price_returns_none(self):
        """0 으로 나누면 spread_pct 가 inf 다."""
        m = self.bot()
        r = m.get_smma_cluster(self.close(0.0),
                               {"a": 110.0, "b": 105.0, "c": 102.0, "d": 100.0})
        self.assertIsNone(r)

    def test_too_few_values_returns_none(self):
        m = self.bot()
        r = m.get_smma_cluster(self.close(), {"a": 110.0, "b": 105.0})
        self.assertIsNone(r)


class TestLevelComment(Base):
    """㉖ 여기만 모델 이름이 옛것이었다."""

    def bot(self):
        self.run_fx("--apply")
        return self.load()

    def test_model_name_matches_the_other_callers(self):
        m = self.bot()
        m.get_claude_level_comment("안녕")
        self.assertEqual(m.SENT[-1]["model"], "claude-sonnet-4-5")

    def test_old_model_name_is_gone_from_the_file(self):
        self.run_fx("--apply")
        self.assertNotIn("claude-sonnet-4-20250514", self.read())

    def test_success_still_returns_the_text(self):
        m = self.bot()
        self.assertEqual(m.get_claude_level_comment("안녕"), "코멘트")

    def test_api_error_is_printed_in_full(self):
        """'content' KeyError 만 찍으면 저쪽이 무슨 말을 했는지 모른다."""
        m = self.bot()
        m.RESPONSE = {"error": {"type": "not_found_error",
                                "message": "model: claude-sonnet-4-20250514"}}
        out = io.StringIO()
        with redirect_stdout(out):
            r = m.get_claude_level_comment("안녕")
        self.assertIsNone(r)
        self.assertIn("not_found_error", out.getvalue())
        self.assertIn("model: claude-sonnet-4-20250514", out.getvalue())
        self.assertNotIn("'content'", out.getvalue())


if __name__ == "__main__":
    unittest.main()
