"""patches/feature_log.py 회귀 테스트.

이 로그는 **다시 만들 수 없다.** 오늘 안 적으면 오늘은 영영 없다.
그래서 지켜야 할 것이 보통 파일보다 엄하다:

  · 기존 줄을 절대 고치지 않는다 (덧붙이기만)
  · 한 줄이 깨져도 나머지를 읽을 수 있다
  · 같은 날 같은 코인을 두 번 적지 않는다
  · 한 소스가 실패해도 나머지는 적는다 (그리고 무엇이 실패했는지 남긴다)
  · 수익률을 적지 않는다 — 적으면 그 시점 이후를 알아야 하므로 미래를 본다
"""

import json
import os
import tempfile
import types
import unittest

try:
    from .helpers import load_patch
except ImportError:            # 봇 폴더에 그대로 복사된 경우
    from helpers import load_patch

fx = load_patch("feature_log")


class FakeLiq:
    def __init__(self, fail=()):
        self.fail = set(fail)

    def get_oi_history(self, symbol, period="1h", limit=25):
        if "oi" in self.fail:
            raise RuntimeError("boom")
        return [{"time": i, "oi": 1000.0 + i * 10} for i in range(limit)]

    def get_long_short_ratio(self, symbol, period="1h", limit=1):
        return None if "ls" in self.fail else {"ratio": 1.22}

    def get_top_trader_ratio(self, symbol, period="1h"):
        if "top" in self.fail:
            raise RuntimeError("boom")
        return {"ratio": 0.92}

    def get_taker_buy_sell(self, symbol, period="1h", limit=1):
        return None if "taker" in self.fail else {"ratio": 1.25}


class TestCollect(unittest.TestCase):

    def test_all_sources_ok(self):
        f, bad = fx.collect("BTC", FakeLiq(), {"BTC": 0.01})
        self.assertEqual(bad, [])
        self.assertAlmostEqual(f["oi"], 1240.0)
        self.assertAlmostEqual(f["oi_chg_24h"], 24.0)
        self.assertEqual(f["ls_ratio"], 1.22)
        self.assertEqual(f["top_ls_ratio"], 0.92)
        self.assertEqual(f["taker_ratio"], 1.25)
        self.assertEqual(f["funding"], 0.01)

    def test_one_failure_does_not_lose_the_rest(self):
        f, bad = fx.collect("BTC", FakeLiq(fail=("oi",)), {"BTC": 0.01})
        self.assertIn("oi", bad)
        self.assertIsNone(f["oi"])
        self.assertEqual(f["ls_ratio"], 1.22, "한 소스 실패에 나머지까지 날렸다")

    def test_every_missing_source_is_named(self):
        f, bad = fx.collect("BTC", FakeLiq(fail=("oi", "ls", "top", "taker")), {})
        self.assertEqual(set(bad), {"oi", "ls_ratio", "top_ls_ratio",
                                    "taker_ratio", "funding"})

    def test_no_forward_return_is_stored(self):
        """수익률을 적으면 그 시점 이후를 알아야 한다 — 미래를 보는 것이다."""
        f, _ = fx.collect("BTC", FakeLiq(), {"BTC": 0.01})
        for k in f:
            self.assertNotIn("ret", k, f"{k} — 미래 수익률로 보이는 항목")
            self.assertNotIn("future", k)
            self.assertNotIn("fwd", k)


class TestFunding(unittest.TestCase):

    def load(self, payload):
        import sys
        mod = types.ModuleType("features")
        mod.get_funding_rates = lambda: payload
        old = sys.modules.get("features")
        sys.modules["features"] = mod
        try:
            return fx.funding_all()
        finally:
            if old is not None:
                sys.modules["features"] = old
            else:
                sys.modules.pop("features", None)

    def test_dict_shape(self):
        self.assertEqual(self.load({"BTC": {"rate": 0.01}})["BTC"], 0.01)

    def test_scalar_shape(self):
        self.assertEqual(self.load({"ETH": 0.02})["ETH"], 0.02)

    def test_symbol_suffix_is_stripped(self):
        self.assertIn("SOL", self.load({"SOL/USDT": 0.03}))

    def test_garbage_is_skipped_not_crashed(self):
        got = self.load({"BTC": "이상한값", "ETH": 0.02})
        self.assertNotIn("BTC", got)
        self.assertEqual(got["ETH"], 0.02)


class TestStore(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "feature_log.jsonl")

    def tearDown(self):
        self.dir.cleanup()

    def row(self, date, coin):
        return {"schema": 1, "date": date, "coin": coin, "f": {"oi": 1.0},
                "missing": [], "captured_at": "2026-01-01T00:00:00+00:00"}

    def read(self):
        with open(self.path, encoding="utf-8") as fp:
            return fp.read()

    def test_append_never_rewrites(self):
        fx.append([self.row("2026-01-01", "BTC")], self.path)
        first = self.read()
        fx.append([self.row("2026-01-02", "BTC")], self.path)
        after = self.read()
        self.assertTrue(after.startswith(first), "기존 줄이 바뀌었다")
        self.assertEqual(len(after.strip().splitlines()), 2)

    def test_already_logged_finds_pairs(self):
        fx.append([self.row("2026-01-01", "BTC"), self.row("2026-01-01", "ETH")],
                  self.path)
        seen = fx.already_logged(self.path)
        self.assertIn(("2026-01-01", "BTC"), seen)
        self.assertNotIn(("2026-01-01", "SOL"), seen)

    def test_corrupt_line_does_not_hide_the_rest(self):
        """한 줄이 깨져도 나머지 날짜는 살아 있어야 한다."""
        fx.append([self.row("2026-01-01", "BTC")], self.path)
        with open(self.path, "a", encoding="utf-8") as fp:
            fp.write("{깨진 줄\n")
        fx.append([self.row("2026-01-02", "ETH")], self.path)
        seen = fx.already_logged(self.path)
        self.assertIn(("2026-01-01", "BTC"), seen)
        self.assertIn(("2026-01-02", "ETH"), seen)

    def test_missing_file_is_empty_not_error(self):
        self.assertEqual(fx.already_logged(self.path + ".nope"), set())

    def test_rows_are_stable_json(self):
        """키 순서가 흔들리면 diff 로 비교할 수 없다."""
        fx.append([self.row("2026-01-01", "BTC")], self.path)
        line = self.read().strip()
        keys = list(json.loads(line))
        self.assertEqual(keys, sorted(keys))


if __name__ == "__main__":
    unittest.main()
