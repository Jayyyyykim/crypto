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
        self.assertIsNone(v, "거래가 아예 없으면 매수비는 정의되지 않는다")

    def test_no_liquidation_is_a_value_not_a_hole(self):
        """청산 0 은 '값이 없는 날'이 아니라 양쪽 다 안 터진 날이다.

        버리면 조용한 날이 통째로 사라지고 시끄러운 날만 남는다 —
        42,906줄이 그렇게 빠져 있었다.
        """
        v, _ = fx.value_of("liq", {"time": NOW,
                                   "long_liquidation_usd": "0",
                                   "short_liquidation_usd": "0"})
        self.assertEqual(v, 0.0)

    def test_string_numbers_are_accepted(self):
        """CoinGlass 는 숫자를 문자열로 보내기도 한다."""
        v, _ = fx.value_of("liq", {"time": NOW,
                                   "long_liquidation_usd": "90",
                                   "short_liquidation_usd": "10"})
        self.assertAlmostEqual(v, 0.8)


class TestRealPayloads(unittest.TestCase):
    """CoinGlass 가 **실제로** 보낸 레코드 (--peek 으로 확인한 것).

    처음에 내가 추측한 이름은 둘 다 틀렸다. ls_ratio·top_ls 가
    비율 대신 long_percent 를 집고 있었다 — 알파벳 순으로 그게
    먼저였기 때문이다. 눈으로 안 봤으면 그대로 쟀을 것이다.
    """

    REAL = {
        "funding": {"close": 0.01, "high": 0.02, "low": 0.0,
                    "open": 0.01, "time": NOW},
        "oi": {"close": 1546148042.0, "high": 1.6e9, "low": 1.5e9,
               "open": 1.55e9, "time": NOW},
        "oi_agg": {"close": 15704638406.0, "high": 1.6e10, "low": 1.5e10,
                   "open": 1.55e10, "time": NOW},
        "ls_ratio": {"global_account_long_percent": 58.94,
                     "global_account_long_short_ratio": 1.4355,
                     "global_account_short_percent": 41.06, "time": NOW},
        "top_ls": {"time": NOW, "top_account_long_percent": 70.09,
                   "top_account_long_short_ratio": 2.3436,
                   "top_account_short_percent": 29.91},
        "taker": {"taker_buy_volume_usd": 4735.0,
                  "taker_sell_volume_usd": 5265.0, "time": NOW},
        "liq": {"long_liquidation_usd": 1118.0,
                "short_liquidation_usd": 882.0, "time": NOW},
    }

    def test_nothing_is_guessed(self):
        """⚠️ 가 하나라도 남으면 그 항목은 엉뚱한 숫자로 재게 된다."""
        guessed = []
        for kind, raw in self.REAL.items():
            v, col = fx.value_of(kind, raw)
            self.assertIsNotNone(v, kind)
            if col.endswith("?"):
                guessed.append(f"{kind}:{col}")
        self.assertEqual(guessed, [], f"추측으로 집은 칼럼: {guessed}")

    def test_ls_ratio_is_the_ratio_not_the_percent(self):
        v, col = fx.value_of("ls_ratio", self.REAL["ls_ratio"])
        self.assertAlmostEqual(v, 1.4355)
        self.assertEqual(col, "global_account_long_short_ratio")

    def test_top_ls_is_the_ratio_not_the_percent(self):
        v, col = fx.value_of("top_ls", self.REAL["top_ls"])
        self.assertAlmostEqual(v, 2.3436)
        self.assertEqual(col, "top_account_long_short_ratio")

    def test_percent_columns_still_work_as_a_fallback(self):
        """비율 칼럼이 사라지면 퍼센트 둘로 만들어 쓴다."""
        raw = {k: v for k, v in self.REAL["ls_ratio"].items()
               if "long_short_ratio" not in k}
        v, col = fx.value_of("ls_ratio", raw)
        self.assertAlmostEqual(v, 58.94 / 41.06, places=6)
        self.assertFalse(col.endswith("?"), col)

    def test_taker_share_is_between_zero_and_one(self):
        v, _ = fx.value_of("taker", self.REAL["taker"])
        self.assertAlmostEqual(v, 0.4735, places=4)

    def test_liq_net_matches_what_peek_showed(self):
        v, _ = fx.value_of("liq", self.REAL["liq"])
        self.assertAlmostEqual(v, 0.118, places=3)

    def test_oi_is_a_level_so_the_bench_must_use_change(self):
        """1.5e9 같은 수준은 코인끼리 비교가 안 된다. 변화율로 써야 한다."""
        v, col = fx.value_of("oi", self.REAL["oi"])
        self.assertGreater(v, 1e9)
        self.assertEqual(col, "close")


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


class TestSkipDiagnostic(unittest.TestCase):
    """개수만 세면 '몇 개'는 알아도 '어디'와 '왜'를 모른다.

    BTC 자료가 통째로 빠진 채 22종으로 잰 것을, 못 읽음 개수만
    보고는 못 찾았다. 개수가 BTC 줄 수와 같다는 걸 사람이 눈으로
    맞춰 봐야 알았다.
    """

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "c.jsonl")

    def tearDown(self):
        self.dir.cleanup()

    def write(self, rows):
        with open(self.path, "w", encoding="utf-8") as fp:
            for r in rows:
                fp.write(json.dumps(r) + "\n")

    def test_skips_are_broken_down_by_coin(self):
        self.write([{"kind": "oi", "coin": "ETH", "ts": NOW, "raw": {"close": 1}}]
                   + [{"kind": "oi", "coin": "BTC", "ts": NOW - i * DAY,
                       "raw": {"close": None}} for i in range(7)])
        cache, _, skipped = fx.load_cache(self.path)
        self.assertEqual(skipped["oi"], 7)
        self.assertEqual(fx.SKIP_SAMPLE["oi"]["coins"], {"BTC": 7})

    def test_a_whole_sample_row_is_kept(self):
        self.write([{"kind": "oi", "coin": "BTC", "ts": NOW, "raw": {"close": None}}])
        fx.load_cache(self.path)
        self.assertEqual(fx.SKIP_SAMPLE["oi"]["one"]["coin"], "BTC")

    def test_sample_resets_between_loads(self):
        self.write([{"kind": "oi", "coin": "BTC", "ts": NOW, "raw": {"close": None}}])
        fx.load_cache(self.path)
        self.write([{"kind": "oi", "coin": "ETH", "ts": NOW, "raw": {"close": 1}}])
        fx.load_cache(self.path)
        self.assertNotIn("oi", fx.SKIP_SAMPLE, "지난 실행의 표본이 남았다")


class TestOhlcvFallback(unittest.TestCase):
    """CoinGlass 자료는 선물인데 시세를 현물에서 받고 있었다.

    최근 상장 8종(ARB·SUI·TIA·SEI·RENDER·TON·PEPE·WIF)이 통째로
    빠졌다. 하필 최근 상장만 빠지면 표본이 오래된 코인 쪽으로 기운다.
    """

    class Fake:
        def __init__(self, ok):
            self.ok = ok
            self.tried = []

        def get_ohlcv_history(self, sym, tf, n):
            self.tried.append(sym)
            if sym != self.ok:
                raise ValueError(f"bitget does not have market symbol {sym}")
            return list(range(300))

    def drive(self, ok):
        old = fx.bt
        fake = self.Fake(ok)
        fx.bt = fake
        try:
            return fx.ohlcv("TON/USDT", "TON", 1460), fake.tried
        finally:
            fx.bt = old

    def test_spot_is_tried_first(self):
        df, tried = self.drive("TON/USDT")
        self.assertIsNotNone(df)
        self.assertEqual(tried, ["TON/USDT"])

    def test_perp_form_is_the_fallback(self):
        df, tried = self.drive("TON/USDT:USDT")
        self.assertIsNotNone(df, f"선물 표기를 안 시도했다: {tried}")
        self.assertIn("TON/USDT:USDT", tried)

    def test_thousand_form_for_tiny_coins(self):
        df, tried = self.drive("1000PEPE/USDT")
        old = fx.bt
        try:
            fx.bt = self.Fake("1000PEPE/USDT")
            got = fx.ohlcv("PEPE/USDT", "PEPE", 1460)
        finally:
            fx.bt = old
        self.assertIsNotNone(got)

    def test_all_forms_failing_returns_none(self):
        df, tried = self.drive("없는것")
        self.assertIsNone(df)
        self.assertGreaterEqual(len(tried), 3)

    def test_short_history_is_not_accepted(self):
        class Short(self.Fake):
            def get_ohlcv_history(self, sym, tf, n):
                self.tried.append(sym)
                return list(range(10))
        old = fx.bt
        try:
            fx.bt = Short("x")
            self.assertIsNone(fx.ohlcv("A/USDT", "A", 1460))
        finally:
            fx.bt = old


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
