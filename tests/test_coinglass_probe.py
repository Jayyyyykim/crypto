"""patches/coinglass_probe.py 회귀 테스트.

이 도구의 출력으로 **돈을 낼지 말지**를 정한다. ✅ 를 잘못 찍으면
안 되는 등급에 결제하고, ❌ 를 잘못 찍으면 되는 걸 포기한다.

고정하는 것:
  · 성공/실패 판정이 HTTP 상태와 body 의 code 를 둘 다 본다
    (200 인데 code 가 에러인 응답이 흔하다)
  · 경로 후보를 돌려 되는 것을 찾고, 전부 실패하면 실패로 남긴다
  · '가장 오래된 날' 은 받은 데이터에서 실제로 계산한다
  · 이어받기가 이미 있는 시각을 다시 적지 않는다
  · **호출 하나하나** 사이에 간격을 둔다 (페이지 사이만이 아니라)
  · 429 는 실패가 아니라 '나중에' — 기다렸다 다시 묻는다
  · 200 인데 0봉이면 ✅ 가 아니다
  · 실패한 이유가 화면까지 올라온다 ('+0' 으로 삼키지 않는다)
"""

import io
import json
import os
import tempfile
import unittest
import urllib.error

try:
    from .helpers import load_patch
except ImportError:            # 봇 폴더에 그대로 복사된 경우
    from helpers import load_patch

fx = load_patch("coinglass_probe")


class FakeNet:
    """urlopen 을 대신한다. 경로별로 응답을 정해 준다."""

    def __init__(self, rules):
        self.rules = rules
        self.urls = []

    def __call__(self, req, timeout=20):
        url = req.full_url
        self.urls.append(url)
        for frag, resp in self.rules:
            if frag in url:
                if isinstance(resp, int):
                    raise urllib.error.HTTPError(
                        url, resp, "nope", {}, io.BytesIO(b'{"msg":"upgrade plan"}'))
                return self._ok(resp)
        raise urllib.error.HTTPError(url, 404, "no", {}, io.BytesIO(b'{"msg":"not found"}'))

    @staticmethod
    def _ok(body):
        class R:
            status = 200

            def read(self):
                return json.dumps(body).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        return R()


def install(rules):
    net = FakeNet(rules)
    fx.urllib.request.urlopen = net
    fx.time.sleep = lambda s: None
    fx._last_call[0] = 0.0        # 앞 테스트가 남긴 시각에 발목 잡히지 않게
    return net


DAY = 86_400_000
NOW = 1_786_000_000_000


def series(n=1000):
    return {"code": "0", "data": [{"time": NOW - i * DAY, "close": "1.0"} for i in range(n)]}


class TestCall(unittest.TestCase):

    def test_success(self):
        install([("x", {"code": "0", "data": [1, 2, 3]})])
        ok, data, status = fx.call("/x", "K", {})
        self.assertTrue(ok)
        self.assertEqual(data, [1, 2, 3])

    def test_http_error_is_failure(self):
        install([("x", 403)])
        ok, msg, status = fx.call("/x", "K", {})
        self.assertFalse(ok)
        self.assertEqual(status, 403)
        self.assertIn("upgrade", msg)

    def test_200_with_error_code_is_failure(self):
        """HTTP 200 인데 body 의 code 가 에러인 응답이 흔하다."""
        install([("x", {"code": "40001", "msg": "plan not allowed"})])
        ok, msg, status = fx.call("/x", "K", {})
        self.assertFalse(ok, "200 이라고 성공으로 봤다")
        self.assertIn("plan", msg)

    def test_data_not_a_list_is_failure(self):
        install([("x", {"code": "0", "data": {"oops": 1}})])
        ok, msg, _ = fx.call("/x", "K", {})
        self.assertFalse(ok)

    def test_key_goes_in_the_header(self):
        net = install([("x", {"code": "0", "data": []})])
        captured = {}
        orig = net.__call__

        def spy(req, timeout=20):
            captured["hdr"] = req.get_header("Cg-api-key")
            return orig(req, timeout)
        fx.urllib.request.urlopen = spy
        fx.call("/x", "MYKEY", {})
        self.assertEqual(captured["hdr"], "MYKEY")


class TestThrottle(unittest.TestCase):
    """BTC 만 받히고 나머지 29종이 전부 +0 이던 원인이 여기였다.

    예전엔 페이지 사이에서만 쉬어서, 코인이나 엔드포인트가 바뀔 때는
    연달아 때렸다. 429 가 나고 그 뒤가 조용히 전멸했다.
    """

    def naps(self):
        got = []
        fx.time.sleep = lambda s: got.append(s)
        return got

    def test_every_call_is_spaced_not_just_pages(self):
        install([("x", {"code": "0", "data": [1]})])
        got = self.naps()
        for _ in range(3):
            fx.call("/x", "K", {})
        self.assertGreaterEqual(len([s for s in got if s > 0]), 2,
                                "호출 사이에 안 쉬었다 — 429 로 전멸한다")

    def test_probe_of_many_endpoints_is_spaced(self):
        """서로 다른 경로를 연달아 찔러도 간격이 있어야 한다."""
        install([("a", {"code": "0", "data": [1]}),
                 ("b", {"code": "0", "data": [1]})])
        got = self.naps()
        fx.call("/a", "K", {})
        fx.call("/b", "K", {})
        self.assertTrue(any(s > 0 for s in got), "경로가 바뀌면 안 쉰다")

    def test_429_is_retried_not_treated_as_missing(self):
        calls = {"n": 0}

        def flaky(req, timeout=20):
            calls["n"] += 1
            if calls["n"] <= 2:
                raise urllib.error.HTTPError(req.full_url, 429, "rate", {},
                                             io.BytesIO(b'{"msg":"slow down"}'))
            return FakeNet._ok({"code": "0", "data": [1, 2]})
        fx.urllib.request.urlopen = flaky
        fx.time.sleep = lambda s: None
        fx._last_call[0] = 0.0
        ok, data, status = fx.call("/x", "K", {})
        self.assertTrue(ok, "429 를 '없는 항목'으로 처리했다")
        self.assertEqual(data, [1, 2])

    def test_429_that_never_clears_is_a_failure(self):
        install([("x", 429)])
        fx.time.sleep = lambda s: None
        ok, msg, status = fx.call("/x", "K", {})
        self.assertFalse(ok)
        self.assertEqual(status, 429)

    def test_200_server_error_is_retried(self):
        """TON 펀딩비가 '200 Server Error' 하나로 통째로 빠졌다.

        저쪽이 잠깐 넘어진 것과 그 코인에 데이터가 없는 것은 다르다.
        """
        seq = {"n": 0}

        def flaky(req, timeout=20):
            seq["n"] += 1
            if seq["n"] <= 2:
                return FakeNet._ok({"code": "50001", "msg": "Server Error"})
            return FakeNet._ok({"code": "0", "data": [1, 2, 3]})
        fx.urllib.request.urlopen = flaky
        fx.time.sleep = lambda s: None
        fx._last_call[0] = 0.0
        ok, data, status = fx.call("/x", "K", {})
        self.assertTrue(ok, "일시적 서버 오류를 '데이터 없음'으로 처리했다")
        self.assertEqual(data, [1, 2, 3])

    def test_permanent_error_is_not_retried(self):
        """'지원하지 않는 쌍'은 몇 번을 물어도 같다. 재시도는 낭비다."""
        net = install([("x", {"code": "1", "msg":
                              "The requested pair does not exist on the exchange."})])
        ok, msg, status = fx.call("/x", "K", {})
        self.assertFalse(ok)
        self.assertEqual(len(net.urls), 1, f"쓸데없이 {len(net.urls)}번 물었다")


class TestProbe(unittest.TestCase):

    def test_first_working_path_wins(self):
        install([("/api/futures/open-interest/history", 403),
                 ("/api/futures/openInterest/ohlc-history", series(10))])
        r = fx.probe_one("OI", "oi", [
            "/api/futures/open-interest/history",
            "/api/futures/openInterest/ohlc-history"], "K", "BTC")
        self.assertNotIn("error", r)
        self.assertEqual(r["path"], "/api/futures/openInterest/ohlc-history")

    def test_all_paths_failing_is_reported(self):
        install([("/api/futures", 403)])
        r = fx.probe_one("OI", "oi", ["/api/futures/a", "/api/futures/b"], "K", "BTC")
        self.assertIn("error", r)
        self.assertEqual(r["status"], 403)

    def test_oldest_is_computed_from_data(self):
        d = [{"time": NOW}, {"time": NOW - 30 * DAY}, {"time": NOW - 10 * DAY}]
        got = fx.oldest(d)
        self.assertTrue(got.startswith("2026-") or got.startswith("2025-"), got)
        self.assertEqual(got, fx.oldest(list(reversed(d))), "정렬에 좌우되면 안 된다")

    def test_oldest_handles_empty(self):
        self.assertIsNone(fx.oldest([]))
        self.assertIsNone(fx.oldest([{"close": "1"}]))

    def test_empty_success_is_not_a_pass(self):
        """200 인데 0봉을 ✅ 로 세면, 되는 줄 알고 결제한다."""
        install([("x", {"code": "0", "data": []})])
        r = fx.probe_one("합산 OI", "oi_agg", ["/api/futures/x"], "K", "BTC")
        self.assertNotIn("error", r)
        self.assertTrue(fx._looks_empty(r), "0봉인데 정상으로 봤다")

    def test_nonempty_params_beat_empty_params(self):
        """앞 후보가 0봉이라고 거기서 멈추면, 뒤에 있는 되는 조합을 못 본다.

        oi_agg 가 이랬다 — 전거래소 합산인데 exchange 를 같이 보내서
        빈 리스트가 왔다.
        """
        install([("exchange=Binance", {"code": "0", "data": []}),
                 ("/api/futures/agg", series(50))])
        r = fx.probe_one("합산 OI", "oi_agg", ["/api/futures/agg"], "K", "BTC")
        self.assertEqual(r["rows"], 50, "0봉짜리 첫 후보에서 멈췄다")
        self.assertNotIn("exchange", r["params"])

    def test_real_data_beats_empty_on_a_later_path(self):
        install([("/api/futures/a", {"code": "0", "data": []}),
                 ("/api/futures/b", series(20))])
        r = fx.probe_one("X", "x", ["/api/futures/a", "/api/futures/b"], "K", "BTC")
        self.assertEqual(r["path"], "/api/futures/b")

    def test_looks_empty_is_false_for_errors(self):
        self.assertFalse(fx._looks_empty({"error": "403", "status": 403}))


class TestFetch(unittest.TestCase):

    def test_pages_backwards_and_dedupes(self):
        install([("history", series(1000))])
        rows, why = fx.fetch_series("/api/futures/open-interest/history",
                                    {"symbol": "BTCUSDT", "interval": "1d", "limit": 1000},
                                    "K", "BTC", "oi", None)
        ts = [r["ts"] for r in rows]
        self.assertEqual(len(ts), len(set(ts)), "같은 시각을 두 번 담았다")
        self.assertGreaterEqual(len(rows), 1000)
        self.assertIsNone(why)

    def test_stops_when_no_new_rows(self):
        """항상 같은 구간을 주는 서버에서도 멈춰야 한다."""
        install([("history", {"code": "0", "data": [{"time": NOW, "close": "1"}]})])
        rows, why = fx.fetch_series("/api/futures/open-interest/history",
                                    {"symbol": "BTCUSDT", "interval": "1d"},
                                    "K", "BTC", "oi", None)
        self.assertEqual(len(rows), 1)
        self.assertIsNone(why)

    def test_error_mid_page_keeps_what_it_got(self):
        calls = {"n": 0}

        def flaky(req, timeout=20):
            calls["n"] += 1
            if calls["n"] > 1:
                raise urllib.error.HTTPError(req.full_url, 429, "rate", {},
                                             io.BytesIO(b'{"msg":"slow"}'))
            return FakeNet._ok(series(1000))
        fx.urllib.request.urlopen = flaky
        fx.time.sleep = lambda s: None
        fx._last_call[0] = 0.0
        rows, why = fx.fetch_series("/p", {"symbol": "BTCUSDT"}, "K", "BTC", "oi", None)
        self.assertEqual(len(rows), 1000, "중간에 끊겼다고 받은 것까지 버렸다")
        self.assertTrue(why, "왜 끊겼는지를 안 알려준다")

    def test_failure_reason_reaches_the_caller(self):
        """'+0' 만 찍으면 '없다'와 '거절당했다'가 구분이 안 된다.

        29종이 전부 +0 이던 화면이 정확히 그랬다.
        """
        install([("/p", 403)])
        rows, why = fx.fetch_series("/p", {"symbol": "BTCUSDT"}, "K", "BTC", "oi", None)
        self.assertEqual(rows, [])
        self.assertIn("403", str(why))

    def test_empty_data_is_not_an_error(self):
        install([("/p", {"code": "0", "data": []})])
        rows, why = fx.fetch_series("/p", {"symbol": "BTCUSDT"}, "K", "BTC", "oi", None)
        self.assertEqual(rows, [])
        self.assertIsNone(why, "정말 없는 것을 오류로 찍었다")

    def test_empty_page_midway_is_asked_again(self):
        """이력 한복판의 빈 페이지를 '끝'으로 믿으면 잘린 이력을 받는다.

        ETH 테이커가 1761에서 끊겼다가 재실행에서 173줄이 더 나온 게
        이 모양이다.
        """
        pages = [
            {"code": "0", "data": [{"time": NOW - i * DAY} for i in range(0, 10)]},
            {"code": "0", "data": []},                       # 딸꾹
            {"code": "0", "data": [{"time": NOW - i * DAY} for i in range(10, 20)]},
            {"code": "0", "data": []},
            {"code": "0", "data": []},
        ]
        seq = {"n": 0}

        def serve(req, timeout=20):
            i = min(seq["n"], len(pages) - 1)
            seq["n"] += 1
            return FakeNet._ok(pages[i])
        fx.urllib.request.urlopen = serve
        fx.time.sleep = lambda s: None
        fx._last_call[0] = 0.0
        rows, why = fx.fetch_series("/p", {"symbol": "BTCUSDT"}, "K", "BTC", "oi", None)
        self.assertEqual(len(rows), 20, "빈 페이지 하나에 속아 절반만 받았다")
        self.assertIsNone(why)

    def test_non_daily_series_is_capped_and_reported(self):
        """TON 청산이 39,999줄 왔다. 일봉이면 나올 수 없는 수다.

        끝없이 받으면 시간만 버리고, 조용히 받으면 일봉인 줄 알고
        백테스트에 넣는다.
        """
        hour = DAY // 24
        seq = {"n": 0}

        def serve(req, timeout=20):
            base = seq["n"] * 1000
            seq["n"] += 1
            return FakeNet._ok({"code": "0", "data": [
                {"time": NOW - (base + i) * hour} for i in range(1000)]})
        fx.urllib.request.urlopen = serve
        fx.time.sleep = lambda s: None
        fx._last_call[0] = 0.0
        rows, why = fx.fetch_series("/p", {"symbol": "TONUSDT"}, "K", "TON", "liq", None)
        self.assertLessEqual(len(rows), fx.MAX_ROWS + 1000)
        self.assertIn("일봉", str(why))


class TestSymbolVariants(unittest.TestCase):
    """Binance 에 PEPEUSDT 는 없다. 1000PEPEUSDT 가 있다.

    이름 하나 때문에 PEPE 가 통째로 빠졌다.
    """

    def test_variants_include_the_1000_form(self):
        self.assertIn("1000PEPEUSDT", fx.symbol_variants("PEPE", "BTCUSDT"))

    def test_falls_back_when_pair_does_not_exist(self):
        install([("symbol=PEPEUSDT", {"code": "1", "msg":
                  "The requested pair does not exist on the exchange."}),
                 ("symbol=1000PEPEUSDT", series(50))])
        rows, why, used = fx.fetch_coin("/p", {"symbol": "BTCUSDT", "interval": "1d"},
                                        "K", "PEPE", "oi")
        self.assertEqual(used, "1000PEPEUSDT")
        self.assertEqual(len(rows), 50)
        self.assertIsNone(why)

    def test_plan_error_does_not_retry_every_name(self):
        """등급 문제면 이름을 바꿔 봐야 똑같이 막힌다. 시간만 3배로 든다."""
        net = install([("/p", 403)])
        fx.fetch_coin("/p", {"symbol": "BTCUSDT"}, "K", "PEPE", "oi")
        names = {u.split("symbol=")[1].split("&")[0] for u in net.urls if "symbol=" in u}
        self.assertEqual(names, {"PEPEUSDT"}, f"이름을 다 돌았다: {names}")

    def test_normal_coin_uses_the_plain_name(self):
        install([("history", series(20))])
        rows, why, used = fx.fetch_coin("/history", {"symbol": "BTCUSDT"},
                                        "K", "ETH", "oi")
        self.assertEqual(used, "ETHUSDT")


class TestFetchCommand(unittest.TestCase):
    """--fetch 를 통째로 돌려 본다.

    이 테스트가 없어서 'fetch_series 가 튜플을 돌려주는데 cmd_fetch 는
    리스트로 받는' 크래시를 사용자가 먼저 만났다. 단위 테스트가 다
    통과해도, 명령을 실제로 안 돌리면 이런 게 남는다.
    """

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.old = os.getcwd()
        os.chdir(self.dir.name)
        self.out = io.StringIO()

    def tearDown(self):
        os.chdir(self.old)
        self.dir.cleanup()

    def lines(self):
        with open(fx.CACHE, encoding="utf-8") as fp:
            return sum(1 for _ in fp)

    def run_fetch(self, coins=("BTC", "ETH")):
        import contextlib
        with contextlib.redirect_stdout(self.out):
            rc = fx.cmd_fetch("K", list(coins))
        return rc, self.out.getvalue()

    def test_writes_rows_for_every_coin(self):
        install([("supported-coins", {"code": "0", "data": ["BTC", "ETH"]}),
                 ("history", series(30))])
        rc, out = self.run_fetch()
        self.assertEqual(rc, 0)
        with open(fx.CACHE, encoding="utf-8") as fp:
            rows = [json.loads(l) for l in fp]
        coins = {r["coin"] for r in rows}
        self.assertEqual(coins, {"BTC", "ETH"},
                         f"코인별로 안 받았다: {coins}\n{out}")

    def test_resume_does_not_rewrite(self):
        install([("supported-coins", {"code": "0", "data": ["BTC"]}),
                 ("history", series(30))])
        self.run_fetch(("BTC",))
        n1 = self.lines()
        self.out = io.StringIO()
        self.run_fetch(("BTC",))
        n2 = self.lines()
        self.assertEqual(n1, n2, "이어받기가 같은 줄을 또 적었다")

    def test_rejection_is_printed_not_swallowed(self):
        """받다가 403 이 나면 화면에 이유가 떠야 한다."""
        seen = {"n": 0}
        net = FakeNet([("supported-coins", {"code": "0", "data": ["BTC"]}),
                       ("history", series(30))])

        def gate(req, timeout=20):
            if "ETHUSDT" in req.full_url or "symbol=ETH" in req.full_url:
                seen["n"] += 1
                raise urllib.error.HTTPError(req.full_url, 403, "no", {},
                                             io.BytesIO(b'{"msg":"upgrade plan"}'))
            return net(req, timeout)
        fx.urllib.request.urlopen = gate
        fx.time.sleep = lambda s: None
        fx._last_call[0] = 0.0
        rc, out = self.run_fetch(("BTC", "ETH"))
        self.assertIn("403", out, f"거절당한 걸 조용히 +0 으로 삼켰다\n{out}")
        self.assertIn("⚠️", out)

    def test_bad_key_stops_early(self):
        install([("supported-coins", 401)])
        rc, out = self.run_fetch()
        self.assertEqual(rc, 1)


class TestCoverage(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.old = os.getcwd()
        self.old_env = os.environ.pop("COINGLASS_API_KEY", None)
        os.chdir(self.dir.name)

    def tearDown(self):
        os.chdir(self.old)
        if self.old_env is not None:
            os.environ["COINGLASS_API_KEY"] = self.old_env
        self.dir.cleanup()

    def test_coverage_needs_no_key(self):
        """구독을 끊은 뒤에도 받은 걸 확인할 수 있어야 한다.

        키를 먼저 요구하면, 키가 죽은 다음엔 확인이 불가능해진다.
        """
        import contextlib
        with open(fx.CACHE, "w", encoding="utf-8") as fp:
            for i in range(3):
                fp.write(json.dumps({"coin": "BTC", "kind": "oi",
                                     "ts": NOW - i * DAY, "raw": {}}) + "\n")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = fx.main(["coinglass_probe.py", "--coverage"])
        self.assertEqual(rc, 0, out.getvalue())
        self.assertNotIn("API 키가 없습니다", out.getvalue())

    def cover(self, rows):
        import contextlib
        with open(fx.CACHE, "w", encoding="utf-8") as fp:
            for r in rows:
                fp.write(json.dumps(r) + "\n")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            fx.cmd_coverage()
        return out.getvalue()

    def solid(self, kind, coin, days):
        return [{"coin": coin, "kind": kind, "ts": NOW - i * DAY, "raw": {}}
                for i in range(days)]

    def test_a_hole_in_the_middle_is_found(self):
        """기간만 보면 2년인데 가운데가 뚫려 있으면 그 구간은 조용히 틀린다."""
        rows = self.solid("oi", "BTC", 800)
        kept = [r for r in rows if not (200 < (NOW - r["ts"]) // DAY < 400)]
        out = self.cover(kept)
        self.assertIn("중간이 빠진", out, out)
        self.assertIn("BTC", out)

    def test_complete_series_says_so(self):
        out = self.cover(self.solid("oi", "BTC", 800))
        self.assertIn("구멍 없음", out, out)

    def test_non_daily_is_flagged(self):
        """TON 청산이 39,999줄이었다. 하루에 여러 줄이면 일봉이 아니다."""
        hour = DAY // 24
        rows = [{"coin": "TON", "kind": "liq", "ts": NOW - i * hour, "raw": {}}
                for i in range(2000)]
        out = self.cover(rows)
        self.assertIn("일봉이 아닙니다", out, out)

    def test_coverage_without_cache_explains(self):
        import contextlib
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = fx.main(["coinglass_probe.py", "--coverage"])
        self.assertEqual(rc, 1)
        self.assertIn("--fetch", out.getvalue())


class TestKey(unittest.TestCase):

    def test_key_from_flag(self):
        self.assertEqual(fx.api_key(["p", "--key", " ABC "]), "ABC")

    def test_key_from_env(self):
        old = os.environ.get("COINGLASS_API_KEY")
        os.environ["COINGLASS_API_KEY"] = "ENVKEY"
        try:
            self.assertEqual(fx.api_key(["p"]), "ENVKEY")
        finally:
            if old is None:
                os.environ.pop("COINGLASS_API_KEY", None)
            else:
                os.environ["COINGLASS_API_KEY"] = old

    def test_no_key_is_empty(self):
        old = os.environ.pop("COINGLASS_API_KEY", None)
        try:
            self.assertEqual(fx.api_key(["p"]), "")
        finally:
            if old is not None:
                os.environ["COINGLASS_API_KEY"] = old


class TestKeyFile(unittest.TestCase):
    """환경변수는 창을 닫으면 사라진다. 파일 쪽이 실수가 적어 그걸 지원한다."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.old_cwd = os.getcwd()
        self.old_env = os.environ.pop("COINGLASS_API_KEY", None)
        os.chdir(self.dir.name)

    def tearDown(self):
        os.chdir(self.old_cwd)
        if self.old_env is not None:
            os.environ["COINGLASS_API_KEY"] = self.old_env
        self.dir.cleanup()

    def write(self, text, encoding="utf-8"):
        with open(fx.KEY_FILE, "w", encoding=encoding) as fp:
            fp.write(text)

    def test_plain_key(self):
        self.write("ABC123\n")
        self.assertEqual(fx.api_key(["p"]), "ABC123")

    def test_notepad_bom_is_stripped(self):
        """윈도우 메모장이 UTF-8 로 저장하면 앞에 BOM 이 붙는다."""
        self.write("ABC123\n", encoding="utf-8-sig")
        self.assertEqual(fx.api_key(["p"]), "ABC123")

    def test_quotes_and_spaces_are_stripped(self):
        self.write('   "ABC123"   \n')
        self.assertEqual(fx.api_key(["p"]), "ABC123")

    def test_comment_lines_are_skipped(self):
        self.write("# 내 키\nABC123\n")
        self.assertEqual(fx.api_key(["p"]), "ABC123")

    def test_flag_beats_file(self):
        self.write("FROMFILE\n")
        self.assertEqual(fx.api_key(["p", "--key", "FROMFLAG"]), "FROMFLAG")

    def test_env_beats_file(self):
        self.write("FROMFILE\n")
        os.environ["COINGLASS_API_KEY"] = "FROMENV"
        try:
            self.assertEqual(fx.api_key(["p"]), "FROMENV")
        finally:
            os.environ.pop("COINGLASS_API_KEY", None)

    def test_empty_file_is_no_key(self):
        self.write("\n\n# 주석만\n")
        self.assertEqual(fx.api_key(["p"]), "")


class TestWidth(unittest.TestCase):

    def test_korean_counts_as_two(self):
        self.assertEqual(len(fx.w("항목", 10)), 8)      # 4칸 + 공백 6
        self.assertEqual(len(fx.w("abc", 10)), 10)

    def test_right_align(self):
        self.assertTrue(fx.w("1", 5, right=True).endswith("1"))


if __name__ == "__main__":
    unittest.main()
