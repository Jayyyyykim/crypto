"""patches/feature_bench.py 회귀 테스트.

이 도구는 CoinGlass 로 산 자료가 쓸모 있는지를 판정한다. 여기가
틀리면 **엉뚱한 칼럼으로 잰 결과를 믿고** 다음 단계를 정한다.

고정하는 것:
  · 어느 칼럼을 썼는지 반드시 드러난다 (조용히 첫 숫자를 집지 않는다)
  · 시각 단위가 섞여 있어도 같은 날로 접힌다
  · 하루에 여러 줄이면 그날 **마지막** 값을 쓴다 (수준이지 합이 아니다)
  · 문턱이 상수가 아니라 그 코인의 분위수다
  · 분위수 창도 진입 조건도 그날까지만 본다
  · 자료가 없는 항목의 조건은 참이 되지 않는다
"""

import json
import os
import tempfile
import unittest

try:
    from .helpers import load_patch
except ImportError:            # 봇 폴더에 그대로 복사된 경우
    from helpers import load_patch

fx = load_patch("feature_bench")

DAY = 86_400_000
NOW = 1_786_000_000_000


class TestValueOf(unittest.TestCase):
    """어느 칼럼을 썼는지가 결과를 바꾼다. 조용히 집으면 안 된다."""

    def test_ohlc_uses_close(self):
        v, col = fx.value_of("oi", {"time": NOW, "open": 1, "high": 3,
                                    "low": 0.5, "close": 2})
        self.assertEqual(v, 2.0)
        self.assertEqual(col, "close")

    def test_ratio_field_wins_over_close(self):
        v, col = fx.value_of("ls_ratio", {"time": NOW, "longShortRatio": 1.5})
        self.assertEqual(v, 1.5)
        self.assertEqual(col, "longShortRatio")

    def test_ratio_can_be_built_from_two_columns(self):
        v, col = fx.value_of("ls_ratio", {"time": NOW,
                                          "longAccount": 60, "shortAccount": 40})
        self.assertAlmostEqual(v, 1.5)
        self.assertIn("/", col)

    def test_taker_becomes_a_buy_share(self):
        """매수·매도 절대량은 코인끼리 비교가 안 된다. 비율이어야 한다."""
        v, col = fx.value_of("taker", {"time": NOW, "buy": 75, "sell": 25})
        self.assertAlmostEqual(v, 0.75)

    def test_taker_alias_names(self):
        v, _ = fx.value_of("taker", {"time": NOW,
                                     "taker_buy_volume_usd": 30,
                                     "taker_sell_volume_usd": 10})
        self.assertAlmostEqual(v, 0.75)

    def test_liq_is_net_and_bounded(self):
        v, _ = fx.value_of("liq", {"time": NOW,
                                   "longLiquidationUsd": 90,
                                   "shortLiquidationUsd": 10})
        self.assertAlmostEqual(v, 0.8)

    def test_guessed_column_is_marked(self):
        """아는 이름이 없으면 첫 숫자를 집되, 물음표로 표시해야 한다."""
        v, col = fx.value_of("oi", {"time": NOW, "무슨값": 7})
        self.assertEqual(v, 7.0)
        self.assertTrue(col.endswith("?"), col)

    def test_time_is_never_the_value(self):
        for k in fx.TIME_KEYS:
            v, col = fx.value_of("oi", {k: NOW})
            self.assertIsNone(v, f"{k} 를 값으로 집었다")

    def test_junk_is_none(self):
        self.assertEqual(fx.value_of("oi", None), (None, None))
        self.assertEqual(fx.value_of("oi", {"time": NOW, "close": "abc"})[0], None)

    def test_zero_divisor_does_not_crash(self):
        v, _ = fx.value_of("taker", {"time": NOW, "buy": 0, "sell": 0})
        self.assertIsNone(v)


class TestCache(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "coinglass_hist.jsonl")

    def tearDown(self):
        self.dir.cleanup()

    def write(self, rows, extra=""):
        with open(self.path, "w", encoding="utf-8") as fp:
            for r in rows:
                fp.write(json.dumps(r) + "\n")
            fp.write(extra)

    def row(self, kind, coin, ts, raw):
        return {"kind": kind, "coin": coin, "ts": ts, "raw": raw}

    def test_last_of_the_day_wins(self):
        """미결제약정은 수준이다. 그날 끝값이 그날 종가와 짝이 맞는다."""
        self.write([self.row("oi", "BTC", NOW, {"close": 10}),
                    self.row("oi", "BTC", NOW + 3600_000, {"close": 20})])
        cache, cols, _ = fx.load_cache(self.path)
        day = list(cache["oi"]["BTC"])[0]
        self.assertEqual(cache["oi"]["BTC"][day], 20)

    def test_mixed_time_units_land_on_one_day(self):
        self.write([self.row("oi", "BTC", NOW, {"close": 10}),
                    self.row("oi", "BTC", NOW // 1000, {"close": 11})])
        cache, _, _ = fx.load_cache(self.path)
        self.assertEqual(len(cache["oi"]["BTC"]), 1, "같은 날이 둘로 갈렸다")

    def test_unusable_rows_are_counted_not_hidden(self):
        self.write([self.row("oi", "BTC", NOW, {"close": 10}),
                    self.row("oi", "BTC", 10 ** 20, {"close": 10}),
                    self.row("oi", "BTC", "안녕", {"close": 10})])
        cache, _, skipped = fx.load_cache(self.path)
        self.assertGreaterEqual(skipped.get("oi", 0), 1)

    def test_corrupt_line_is_skipped(self):
        self.write([self.row("oi", "BTC", NOW, {"close": 1})], extra="{깨진\n")
        cache, _, _ = fx.load_cache(self.path)
        self.assertIn("BTC", cache["oi"])

    def test_missing_file_is_empty(self):
        cache, cols, skipped = fx.load_cache(self.path + ".nope")
        self.assertEqual(cache, {})

    def test_kinds_and_coins_stay_separate(self):
        self.write([self.row("oi", "BTC", NOW, {"close": 1}),
                    self.row("oi", "ETH", NOW, {"close": 2}),
                    self.row("funding", "BTC", NOW, {"close": 3})])
        cache, _, _ = fx.load_cache(self.path)
        self.assertEqual(cache["oi"]["BTC"], cache["oi"]["BTC"])
        self.assertNotEqual(cache["oi"]["BTC"], cache["oi"]["ETH"])
        self.assertIn("funding", cache)


class TestAttach(unittest.TestCase):

    def frame(self, n=400):
        import pandas as pd
        return pd.DataFrame({
            "timestamp": pd.date_range("2024-01-01", periods=n, freq="1D"),
            "open": [100.0] * n, "close": [100.0 + i for i in range(n)],
            "high": [201.0] * n, "low": [99.0] * n, "atr": [2.0] * n,
        })

    def cache(self, n=400, kind="oi", ramp=True):
        import pandas as pd
        days = pd.date_range("2024-01-01", periods=n, freq="1D")
        return {kind: {"BTC": {d.strftime("%Y-%m-%d"): (i + 1 if ramp else 5.0)
                               for i, d in enumerate(days)}}}

    def test_values_are_joined_by_date(self):
        d = fx.attach(self.frame(), self.cache(), "BTC")
        self.assertAlmostEqual(d["oi"].iloc[100], 101)

    def test_oi_becomes_a_change_not_a_level(self):
        """수준은 코인끼리·시기끼리 비교가 안 된다."""
        d = fx.attach(self.frame(), self.cache(), "BTC")
        self.assertAlmostEqual(d["oi_chg"].iloc[100], 1 / 100, places=6)

    def test_thresholds_move_with_time(self):
        d = fx.attach(self.frame(), self.cache(), "BTC")
        a, b = d["oi_chg_hi"].iloc[150], d["oi_chg_hi"].iloc[350]
        self.assertNotAlmostEqual(a, b, msg="문턱이 안 움직인다 — 상수다")

    def test_warmup_is_nan_not_zero(self):
        d = fx.attach(self.frame(), self.cache(), "BTC")
        self.assertTrue(d["oi_chg_hi"].iloc[:29].isna().all())

    def test_does_not_read_the_future(self):
        base = self.frame()
        c1 = self.cache()
        c2 = {"oi": {"BTC": dict(c1["oi"]["BTC"])}}
        import pandas as pd
        for i, ts in enumerate(base["timestamp"]):
            if i > 250:
                c2["oi"]["BTC"][ts.strftime("%Y-%m-%d")] = 9e9
        d1 = fx.attach(base, c1, "BTC")
        d2 = fx.attach(base, c2, "BTC")
        for col in ("oi", "oi_chg", "oi_chg_hi", "oi_chg_lo"):
            self.assertAlmostEqual(d1[col].iloc[250], d2[col].iloc[250], places=6,
                                   msg=f"{col} 이 미래를 본다")

    def test_missing_kind_does_not_crash(self):
        d = fx.attach(self.frame(), self.cache(kind="oi"), "BTC")
        self.assertIn("lsr_hi", d.columns)

    def test_missing_kind_never_fires_a_signal(self):
        """자료가 없는데 조건이 참이 되면 없는 신호를 세게 된다."""
        d = fx.attach(self.frame(), self.cache(kind="oi"), "BTC")
        for name, side, cond in fx.CANDIDATES:
            if "개미" in name or "큰손" in name or "테이커" in name or "펀딩" in name:
                for i in (200, 300, 390):
                    self.assertFalse(bool(cond(d, i)),
                                     f"{name} 이 자료 없이 발동했다 (i={i})")

    def test_every_candidate_is_evaluable(self):
        full = {}
        for k in ("oi", "funding", "ls_ratio", "top_ls", "taker"):
            full.update(self.cache(kind=k))
        d = fx.attach(self.frame(), full, "BTC")
        for name, side, cond in fx.CANDIDATES:
            for i in (200, 300, 390):
                try:
                    bool(cond(d, i))
                except Exception as e:
                    self.fail(f"{name} at i={i}: {type(e).__name__}: {e}")

    def test_both_directions_are_tested(self):
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

    def test_split_is_by_date(self):
        a, b = fx.split([{"date": "2024-01-01"}, {"date": "2025-01-01"}], "2024-06-01")
        self.assertEqual(len(a), 1)
        self.assertEqual(len(b), 1)


if __name__ == "__main__":
    unittest.main()
