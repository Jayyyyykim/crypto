"""patches/fix_cvd_honest.py 회귀 테스트.

두 가지를 붙인다.

  ㉘a·b  거래소에 보낼 이름을 한 규칙으로 정한다. 지금까지는
         거르는 쪽과 부르는 쪽이 서로 다른 규칙을 썼고, 그래서
         화면에는 '다 통과'로 뜨는데 로그는 안 조용해졌다.
  ㉘c~h  AI 프롬프트가 CVD 엇갈림을 사실로 가르치고 있었다.
         쟀고 안 됐다.

여기가 틀리면 증상이 조용하다. 표시 이름이 'SKHYNIX/USDT:USDT' 로
새면 화면 곳곳의 replace('/USDT','') 가 'SKHYNIX:USDT' 를 만들고,
프롬프트가 안 바뀌면 AI 는 매일 부정된 규칙으로 방향을 말한다.

고정하는 것:
  · 거래소에 보내는 이름만 바뀐다 — 표시 이름은 그대로
  · 목록을 못 받으면 하던 대로 (조회가 멈추면 개선이 아니다)
  · 못 부르는 이름은 거래소를 두드리지 않는다
  · 시세도 봉과 **같은 규칙**으로 부른다
  · 프롬프트에 '가짜 상승' 단정과 '아직 안 쟀다'가 남지 않는다
  · _MARKETS 가 없으면 붙이지 않고 순서를 알려준다
"""

import ast
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

fx = load_patch("fix_cvd_honest")


BOT = '''_MARKETS = {"at": 0.0, "syms": set(), "said": set()}

SENT = {"ohlcv": [], "ticker": []}
STOCKS = set()
FAIL = {"markets": False}


class _Ex:
    def load_markets(self):
        if FAIL["markets"]:
            raise RuntimeError("거래소 응답 없음")
        return {s: {} for s in
                ["BTC/USDT", "ETH/USDT", "SKHYNIX/USDT:USDT"]}

    def fetch_ohlcv(self, symbol, timeframe, limit=0):
        SENT["ohlcv"].append(symbol)
        return [[0, 1, 1, 1, 1, 1]] * 20

    def fetch_ticker(self, symbol):
        SENT["ticker"].append(symbol)
        return {"last": 100.0}


exchange = _Ex()
_SILENT_TF = ()


def is_stock_symbol(symbol):
    return symbol in STOCKS


def get_stock_ohlcv(symbol, timeframe, limit):
    return "주식봉"


def get_stock_price(symbol):
    return 42.0


def _drop_unclosed(df, timeframe):
    return df


class _PD:
    @staticmethod
    def DataFrame(rows, columns=None):
        return {"rows": rows}

    @staticmethod
    def to_datetime(x, unit=None):
        return x


pd = _PD()


def get_ohlcv(symbol, timeframe, limit=400):
    """OHLCV 수집 — 크립토(Bybit) / 주식·지수(yfinance) 자동 라우팅
    ※ v3.6부터 마감봉만 반환 (미완성 봉 자동 제거)"""
    if is_stock_symbol(symbol):
        return get_stock_ohlcv(symbol, timeframe, limit)
    for attempt in range(2):
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            if not ohlcv or len(ohlcv) < 10:
                return None
            df = pd.DataFrame(ohlcv, columns=['timestamp'])
            return _drop_unclosed(df, timeframe)
        except Exception as e:
            if timeframe not in _SILENT_TF and attempt == 1:
                print(f"데이터 수집 오류 ({symbol} {timeframe}): {e}")
    return None


def get_current_price(symbol):
    """현재가 조회 — 크립토/주식 자동 라우팅, 재시도 포함"""
    if is_stock_symbol(symbol):
        return get_stock_price(symbol)
    for attempt in range(2):
        try:
            ticker = exchange.fetch_ticker(symbol)
            price = ticker.get('last')
            if price and price > 0:
                return float(price)
        except Exception:
            pass
    return None


PROMPT_A = """
  - 가격↑ + CVD↓ = 가짜 상승 (반전 주의)

30종 · 4년 · 진입 규칙 60가지를 무작위 진입과 겨루게 했고
통과한 것이 하나도 없다. 아래는 그 결과다.

• CVD↑+가격↑=진짜 / CVD↓+가격↑=가짜 — 이건 아직 안 쟀다
"""

PROMPT_B = """
• 데이터 모순 적극 짚기 (가격↑ + CVD↓ = 가짜 상승 등)

   · CVD↑+가격↑=진짜 / CVD↓+가격↑=가짜 — 이건 아직 안 쟀다.
     쓰되 '측정 안 됨'을 밝혀라.

• **진입 시점 규칙은 45가지를 쟀고 전부 실패했다.**
"""
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
            rc = fx.main(["fix_cvd_honest.py", *args])
        return rc, out.getvalue()

    def load(self):
        import importlib.util
        Base._n += 1
        name = f"_cvdhonest{Base._n}"
        spec = importlib.util.spec_from_file_location(name, "bot.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        self.loaded.append(name)
        spec.loader.exec_module(mod)
        return mod

    def bot(self):
        self.run_fx("--apply")
        return self.load()


class TestApply(Base):

    def test_preview_changes_nothing(self):
        before = self.read()
        rc, out = self.run_fx()
        self.assertEqual(self.read(), before)
        self.assertIn("적용 예정", out)

    def test_every_site_is_found(self):
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

    def test_it_refuses_without_the_markets_cache(self):
        """market_symbol 은 ㉔ 이 만든 캐시를 쓴다. 없으면 붙여도
        첫 호출에서 NameError 다 — 순서를 알려주고 멈춘다."""
        self.write(BOT.replace('_MARKETS = {"at": 0.0, "syms": set(), "said": set()}',
                               "# 캐시 없음"))
        rc, out = self.run_fx("--apply")
        self.assertEqual(rc, 1)
        self.assertIn("fix_bot_noise.py --apply", out)
        self.assertFalse(os.path.exists("bot.py.bak"), "안 고친다고 해놓고 고쳤다")

    def test_unknown_bot_is_refused(self):
        self.write("def other():\n    return 1\n_MARKETS = {}\n")
        rc, out = self.run_fx()
        self.assertEqual(rc, 1)
        self.assertIn("봇 판이 다릅니다", out)

    def test_verify(self):
        rc, out = self.run_fx("--verify")
        self.assertIn("□", out)
        self.run_fx("--apply")
        rc, out = self.run_fx("--verify")
        self.assertNotIn("□", out)
        self.assertEqual(out.count("✅"), len(fx.SITES))


class TestMarketSymbol(Base):
    """거래소가 아는 이름으로 바꾼다. 표시 이름은 안 바꾼다."""

    def test_plain_symbol_is_unchanged(self):
        m = self.bot()
        self.assertEqual(m.market_symbol("BTC/USDT"), "BTC/USDT")

    def test_perp_only_symbol_is_resolved(self):
        m = self.bot()
        self.assertEqual(m.market_symbol("SKHYNIX/USDT"), "SKHYNIX/USDT:USDT")

    def test_truly_missing_is_none(self):
        m = self.bot()
        self.assertIsNone(m.market_symbol("없는것/USDT"))

    def test_it_falls_back_when_the_list_cannot_be_fetched(self):
        """이 함수 때문에 조회가 멈추면 그건 개선이 아니다."""
        m = self.bot()
        m.FAIL["markets"] = True
        self.assertEqual(m.market_symbol("아무거나/USDT"), "아무거나/USDT")

    def test_the_list_is_cached(self):
        m = self.bot()
        m.market_symbol("BTC/USDT")
        m.FAIL["markets"] = True          # 다시 부르면 터진다
        self.assertEqual(m.market_symbol("SKHYNIX/USDT"), "SKHYNIX/USDT:USDT")


class TestFetching(Base):

    def test_ohlcv_sends_the_resolved_name(self):
        m = self.bot()
        m.get_ohlcv("SKHYNIX/USDT", "1d", limit=20)
        self.assertEqual(m.SENT["ohlcv"], ["SKHYNIX/USDT:USDT"])

    def test_price_sends_the_resolved_name(self):
        """봉만 고치면 현재가가 없어서 체결 검사가 조용히 건너뛴다."""
        m = self.bot()
        m.get_current_price("SKHYNIX/USDT")
        self.assertEqual(m.SENT["ticker"], ["SKHYNIX/USDT:USDT"])

    def test_plain_symbols_are_untouched(self):
        m = self.bot()
        m.get_ohlcv("BTC/USDT", "1d", limit=20)
        m.get_current_price("BTC/USDT")
        self.assertEqual(m.SENT["ohlcv"], ["BTC/USDT"])
        self.assertEqual(m.SENT["ticker"], ["BTC/USDT"])

    def test_missing_symbol_never_touches_the_exchange(self):
        """없는 이름으로 두 번 두드리고 오류를 찍을 이유가 없다."""
        m = self.bot()
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertIsNone(m.get_ohlcv("없는것/USDT", "1d", limit=20))
            self.assertIsNone(m.get_current_price("없는것/USDT"))
        self.assertEqual(m.SENT["ohlcv"], [])
        self.assertEqual(m.SENT["ticker"], [])
        self.assertNotIn("데이터 수집 오류", out.getvalue())

    def test_stocks_still_route_to_yfinance(self):
        m = self.bot()
        m.STOCKS.add("AAPL")
        self.assertEqual(m.get_ohlcv("AAPL", "1d"), "주식봉")
        self.assertEqual(m.get_current_price("AAPL"), 42.0)
        self.assertEqual(m.SENT["ohlcv"], [])

    def test_it_never_returns_a_perp_name_to_the_caller(self):
        """'SKHYNIX/USDT:USDT' 가 표시 이름으로 새면 화면 곳곳의
        replace('/USDT','') 가 'SKHYNIX:USDT' 를 만든다."""
        src = self.read()
        self.run_fx("--apply")
        after = self.read()
        # 새로 들어간 코드에서 perp 이름이 밖으로 나가는 자리가 없어야
        for bad in ("symbol = real", "symbol = perp", "return perp\n    return sym"):
            self.assertNotIn(bad, after)
        self.assertIn("표시 이름은 그대로 두고", after)


class TestPrompt(Base):
    """AI 프롬프트가 부정된 규칙을 가르치면 매일 그대로 말한다."""

    def test_the_fake_rally_claim_is_gone(self):
        self.run_fx("--apply")
        s = self.read()
        self.assertNotIn("가격↑ + CVD↓ = 가짜 상승 (반전 주의)", s)
        self.assertNotIn("가짜 상승 등)", s)

    def test_not_measured_becomes_measured(self):
        self.run_fx("--apply")
        s = self.read()
        self.assertNotIn("이건 아직 안 쟀다", s)
        self.assertIn("쟀고 안 됐다", s)

    def test_the_numbers_are_there(self):
        """숫자 없이 '안 됐다'만 적으면 3개월 뒤에 다시 묻는다."""
        self.run_fx("--apply")
        s = self.read()
        self.assertIn("1,252건", s)
        self.assertIn("−0.186", s)

    def test_counts_are_updated(self):
        self.run_fx("--apply")
        s = self.read()
        self.assertIn("진입 규칙 70가지", s)
        self.assertIn("진입 시점 규칙은 70가지", s)
        self.assertNotIn("45가지를 쟀고", s)
        self.assertNotIn("60가지를 무작위", s)

    def test_the_patch_does_not_predict_direction(self):
        """60가지를 재서 방향은 알 수 없다는 게 확인됐다."""
        for _, _, _, new, _ in fx.SITES:
            for word in ("매수하", "매도하", "롱 유리", "숏 유리", "곧 빠진"):
                self.assertNotIn(word, new, f"패치가 방향을 말한다: {word}")


if __name__ == "__main__":
    unittest.main()
