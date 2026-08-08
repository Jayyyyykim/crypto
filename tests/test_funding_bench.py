"""patches/funding_bench.py 회귀 테스트.

펀딩은 가격이 아닌 정보 중 **유일하게 과거를 받을 수 있는 것**이다.
여기가 틀리면 유일한 기회를 잘못된 결론으로 날린다.

고정하는 것:
  · 하루에 세 번 오는 값을 그날 하나로 접는다 (평균)
  · 문턱은 그 코인의 분위수로 잡는다 — 상수를 고르면 그게 과최적화다
  · 분위수 창도 진입 조건도 그날까지만 본다
  · 캐시 한 줄이 깨져도 나머지를 읽는다
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

fx = load_patch("funding_bench")


class TestCache(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "funding_hist.jsonl")

    def tearDown(self):
        self.dir.cleanup()

    def write(self, rows, extra=""):
        with open(self.path, "w", encoding="utf-8") as fp:
            for r in rows:
                fp.write(json.dumps(r) + "\n")
            fp.write(extra)

    def row(self, coin, date, ts, rate):
        return {"coin": coin, "date": date, "ts": ts, "rate": rate}

    def test_three_a_day_becomes_one(self):
        """8시간마다 오는 값을 그날 하나로 접는다."""
        self.write([self.row("BTC", "2025-01-01", 1, 0.01),
                    self.row("BTC", "2025-01-01", 2, 0.02),
                    self.row("BTC", "2025-01-01", 3, 0.03)])
        got = fx.load_cache(self.path)
        self.assertAlmostEqual(got["BTC"]["2025-01-01"], 0.02)

    def test_coins_are_separate(self):
        self.write([self.row("BTC", "2025-01-01", 1, 0.01),
                    self.row("ETH", "2025-01-01", 1, 0.05)])
        got = fx.load_cache(self.path)
        self.assertAlmostEqual(got["BTC"]["2025-01-01"], 0.01)
        self.assertAlmostEqual(got["ETH"]["2025-01-01"], 0.05)

    def test_corrupt_line_is_skipped(self):
        self.write([self.row("BTC", "2025-01-01", 1, 0.01)], extra="{깨진 줄\n")
        self.assertIn("BTC", fx.load_cache(self.path))

    def test_missing_file_is_empty(self):
        self.assertEqual(fx.load_cache(self.path + ".nope"), {})

    def test_bounds_report_span(self):
        self.write([self.row("BTC", "2024-01-01", 1, 0.0),
                    self.row("BTC", "2025-01-01", 2, 0.0)])
        lo, hi, n = fx.cache_bounds(self.path)["BTC"]
        self.assertEqual((lo, hi, n), ("2024-01-01", "2025-01-01", 2))


class TestPrepare(unittest.TestCase):

    def frame(self, n=400):
        import pandas as pd
        return pd.DataFrame({
            "timestamp": pd.date_range("2024-01-01", periods=n, freq="1D"),
            "open": [100.0] * n, "close": [100.0] * n,
            "high": [101.0] * n, "low": [99.0] * n, "atr": [2.0] * n,
        })

    def fmap(self, n=400, ramp=True):
        import pandas as pd
        days = pd.date_range("2024-01-01", periods=n, freq="1D")
        return {d.strftime("%Y-%m-%d"): (i / n if ramp else 0.01)
                for i, d in enumerate(days)}

    def test_funding_column_is_joined_by_date(self):
        d = fx.prepare(self.frame(), self.fmap())
        self.assertAlmostEqual(d["fund"].iloc[100], 100 / 400)

    def test_thresholds_are_per_coin_quantiles_not_constants(self):
        """상수 문턱은 그 상수를 고른 것 자체가 과최적화다."""
        d = fx.prepare(self.frame(), self.fmap())
        hi_early, hi_late = d["fund_hi"].iloc[150], d["fund_hi"].iloc[350]
        self.assertNotAlmostEqual(hi_early, hi_late,
                                  msg="문턱이 시기에 따라 안 움직인다 — 상수다")
        self.assertGreater(hi_late, hi_early)

    def test_hi_is_above_lo(self):
        d = fx.prepare(self.frame(), self.fmap())
        rows = d.dropna(subset=["fund_hi", "fund_lo"])
        self.assertTrue((rows["fund_hi"] >= rows["fund_lo"]).all())

    def test_warmup_is_nan_not_zero(self):
        """창이 안 찼는데 0으로 채우면 초반이 전부 극단으로 잡힌다."""
        d = fx.prepare(self.frame(), self.fmap())
        self.assertTrue(d["fund_hi"].iloc[:29].isna().all())

    def test_does_not_read_the_future(self):
        base = self.frame()
        fm = self.fmap()
        d1 = fx.prepare(base, fm)
        fm2 = dict(fm)
        import pandas as pd
        for i, ts in enumerate(base["timestamp"]):
            if i > 250:
                fm2[ts.strftime("%Y-%m-%d")] = 99.0      # 이후를 통째로 조작
        d2 = fx.prepare(base, fm2)
        for col in ("fund", "fund_hi", "fund_lo"):
            self.assertAlmostEqual(d1[col].iloc[250], d2[col].iloc[250], places=9,
                                   msg=f"{col} 이 미래를 본다")

    def test_candidates_are_evaluable(self):
        d = fx.prepare(self.frame(), self.fmap())
        for name, side, cond in fx.CANDIDATES:
            for i in (200, 300, 390):
                try:
                    bool(cond(d, i))
                except Exception as e:
                    self.fail(f"{name} at i={i}: {type(e).__name__}: {e}")

    def test_both_directions_are_tested(self):
        """쏠림 되돌림만 재고 추종을 안 재면 결론이 반쪽이다."""
        longs = [n for n, s, _ in fx.CANDIDATES if s]
        shorts = [n for n, s, _ in fx.CANDIDATES if not s]
        self.assertTrue(longs and shorts)


class TestStats(unittest.TestCase):

    def test_thin_period_cannot_pass(self):
        d, lo, hi = fx.excess([{"r": 1.0}] * (fx.MIN_PER_PERIOD - 1),
                              [{"r": 0.0}] * 500)
        self.assertEqual(lo, float("-inf"))

    def test_clear_excess_passes(self):
        d, lo, hi = fx.excess([{"r": 1.0}] * 200, [{"r": 0.0}] * 200)
        self.assertGreater(lo, 0)


if __name__ == "__main__":
    unittest.main()
