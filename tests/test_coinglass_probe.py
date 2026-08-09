"""patches/coinglass_probe.py 회귀 테스트.

이 도구의 출력으로 **돈을 낼지 말지**를 정한다. ✅ 를 잘못 찍으면
안 되는 등급에 결제하고, ❌ 를 잘못 찍으면 되는 걸 포기한다.

고정하는 것:
  · 성공/실패 판정이 HTTP 상태와 body 의 code 를 둘 다 본다
    (200 인데 code 가 에러인 응답이 흔하다)
  · 경로 후보를 돌려 되는 것을 찾고, 전부 실패하면 실패로 남긴다
  · '가장 오래된 날' 은 받은 데이터에서 실제로 계산한다
  · 이어받기가 이미 있는 시각을 다시 적지 않는다
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


class TestFetch(unittest.TestCase):

    def test_pages_backwards_and_dedupes(self):
        install([("history", series(1000))])
        rows = fx.fetch_series("/api/futures/open-interest/history",
                               {"symbol": "BTCUSDT", "interval": "1d", "limit": 1000},
                               "K", "BTC", "oi", None)
        ts = [r["ts"] for r in rows]
        self.assertEqual(len(ts), len(set(ts)), "같은 시각을 두 번 담았다")
        self.assertGreaterEqual(len(rows), 1000)

    def test_stops_when_no_new_rows(self):
        """항상 같은 구간을 주는 서버에서도 멈춰야 한다."""
        install([("history", {"code": "0", "data": [{"time": NOW, "close": "1"}]})])
        rows = fx.fetch_series("/api/futures/open-interest/history",
                               {"symbol": "BTCUSDT", "interval": "1d"},
                               "K", "BTC", "oi", None)
        self.assertEqual(len(rows), 1)

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
        rows = fx.fetch_series("/p", {"symbol": "BTCUSDT"}, "K", "BTC", "oi", None)
        self.assertEqual(len(rows), 1000, "중간에 끊겼다고 받은 것까지 버렸다")


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


class TestWidth(unittest.TestCase):

    def test_korean_counts_as_two(self):
        self.assertEqual(len(fx.w("항목", 10)), 8)      # 4칸 + 공백 6
        self.assertEqual(len(fx.w("abc", 10)), 10)

    def test_right_align(self):
        self.assertTrue(fx.w("1", 5, right=True).endswith("1"))


if __name__ == "__main__":
    unittest.main()
