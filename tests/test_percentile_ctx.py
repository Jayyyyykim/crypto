"""patches/percentile_ctx.py 회귀 테스트.

이 도구는 화면에 "상위 3%" 를 띄운다. 사람이 그걸 보고 판단한다.
분위가 틀리면 **틀린 문맥으로 사람을 움직이게 된다.**

고정하는 것:
  · 값 뽑는 규칙이 feature_bench 와 같다 (같은 칼럼, 같은 순서)
  · 미결제약정은 수준이 아니라 하루 변화율로 잰다
  · 날짜가 붙어 있지 않으면 '하루 변화'로 세지 않는다
  · 자료가 짧으면 기준을 만들지 않는다 (틀린 분위보다 없는 게 낫다)
  · 단위가 10의 거듭제곱만큼 어긋나면 잡아낸다
  · 표시가 '매매 신호'로 읽히지 않는다
"""

import json
import os
import tempfile
import unittest

try:
    from .helpers import load_patch
except ImportError:            # 봇 폴더에 그대로 복사된 경우
    from helpers import load_patch

fx = load_patch("percentile_ctx")

NOW = 1_786_000_000_000
DAY = 86_400_000


class TestValueOf(unittest.TestCase):
    """feature_bench 와 **같은 값**을 뽑아야 한다.

    여기가 어긋나면 시험대에서 잰 것과 화면에 띄우는 것이 다른 값이
    된다. 같은 이름으로 다른 것을 보게 된다.
    """

    def test_funding_and_oi_use_close(self):
        self.assertEqual(fx.value_of("funding", {"close": 0.01}), 0.01)
        self.assertEqual(fx.value_of("oi", {"close": 1.5e9}), 1.5e9)

    def test_ls_ratio_prefers_the_ratio_column(self):
        v = fx.value_of("ls_ratio", {"global_account_long_percent": 58.94,
                                     "global_account_long_short_ratio": 1.4355,
                                     "global_account_short_percent": 41.06})
        self.assertAlmostEqual(v, 1.4355)

    def test_ls_ratio_falls_back_to_percents(self):
        v = fx.value_of("ls_ratio", {"global_account_long_percent": 60.0,
                                     "global_account_short_percent": 40.0})
        self.assertAlmostEqual(v, 1.5)

    def test_top_ls_prefers_the_ratio_column(self):
        v = fx.value_of("top_ls", {"top_account_long_percent": 70.09,
                                   "top_account_long_short_ratio": 2.3436,
                                   "top_account_short_percent": 29.91})
        self.assertAlmostEqual(v, 2.3436)

    def test_taker_is_a_buy_share(self):
        v = fx.value_of("taker", {"taker_buy_volume_usd": 75,
                                  "taker_sell_volume_usd": 25})
        self.assertAlmostEqual(v, 0.75)

    def test_junk_is_none(self):
        self.assertIsNone(fx.value_of("oi", None))
        self.assertIsNone(fx.value_of("oi", {"close": "abc"}))
        self.assertIsNone(fx.value_of("taker", {"taker_buy_volume_usd": 0,
                                                "taker_sell_volume_usd": 0}))


class TestDerive(unittest.TestCase):

    def test_pct_change_is_percent(self):
        s = [("2025-01-01", 100.0), ("2025-01-02", 110.0)]
        self.assertAlmostEqual(fx.derive(s, "pct_change")[0], 10.0)

    def test_a_gap_is_not_a_one_day_change(self):
        """사흘 건너뛴 변화를 '하루 변화'라 부르면 분포가 부푼다."""
        s = [("2025-01-01", 100.0), ("2025-01-04", 130.0),
             ("2025-01-05", 143.0)]
        got = fx.derive(s, "pct_change")
        self.assertEqual(len(got), 1, got)
        self.assertAlmostEqual(got[0], 10.0)

    def test_zero_divisor_is_skipped(self):
        s = [("2025-01-01", 0.0), ("2025-01-02", 5.0)]
        self.assertEqual(fx.derive(s, "pct_change"), [])

    def test_none_means_raw_values(self):
        s = [("2025-01-01", 1.0), ("2025-01-02", 2.0)]
        self.assertEqual(fx.derive(s, None), [1.0, 2.0])


class TestBreakpoints(unittest.TestCase):

    def test_short_series_gets_no_table(self):
        """틀린 분위를 띄우느니 없는 게 낫다."""
        self.assertIsNone(fx.breakpoints(list(range(fx.MIN_DAYS - 1))))

    def test_long_series_spans_min_to_max(self):
        bp = fx.breakpoints([float(i) for i in range(1000)])
        self.assertEqual(len(bp), fx.STEPS)
        self.assertEqual(bp[0], 0.0)
        self.assertEqual(bp[-1], 999.0)

    def test_monotone(self):
        bp = fx.breakpoints([float(i % 500) for i in range(1000)])
        self.assertEqual(bp, sorted(bp))


class TestRank(unittest.TestCase):

    def setUp(self):
        self.bp = fx.breakpoints([float(i) for i in range(1000)])

    def test_top_value_is_top_percentile(self):
        self.assertGreaterEqual(fx.rank(self.bp, 1e9), 100)

    def test_bottom_value_is_zero(self):
        self.assertEqual(fx.rank(self.bp, -1e9), 0)

    def test_middle_is_about_fifty(self):
        self.assertAlmostEqual(fx.rank(self.bp, 500.0), 50, delta=2)

    def test_none_value_is_none(self):
        self.assertIsNone(fx.rank(self.bp, None))
        self.assertIsNone(fx.rank(None, 1.0))


class TestScaleHint(unittest.TestCase):
    """CoinGlass 펀딩 close 는 0.01, ccxt fundingRate 는 0.0001.

    그대로 비교하면 **매일 '하위 0%'** 가 뜬다. 조용히 틀리는 종류다.
    """

    def setUp(self):
        # 과거 분포가 0.01 언저리
        self.bp = fx.breakpoints([0.005 + 0.00001 * i for i in range(1000)])

    def test_same_scale_needs_no_fix(self):
        self.assertEqual(fx.scale_hint(self.bp, 0.012), 1.0)

    def test_hundredfold_smaller_is_caught(self):
        self.assertAlmostEqual(fx.scale_hint(self.bp, 0.00012), 100.0)

    def test_hundredfold_larger_is_caught(self):
        self.assertAlmostEqual(fx.scale_hint(self.bp, 1.2), 0.01)

    def test_after_fixing_the_percentile_is_sane(self):
        v = 0.00012                                  # ccxt 표기
        f = fx.scale_hint(self.bp, v)
        pct = fx.rank(self.bp, v * f)
        self.assertGreater(pct, 5)
        self.assertLess(pct, 95, "맞춰도 끝으로 몰린다")

    def test_zero_and_none_do_not_crash(self):
        self.assertEqual(fx.scale_hint(self.bp, 0), 1.0)
        self.assertEqual(fx.scale_hint(self.bp, None), 1.0)
        self.assertEqual(fx.scale_hint([], 1.0), 1.0)


class TestContext(unittest.TestCase):

    def table(self):
        bp = fx.breakpoints([float(i) for i in range(1000)])
        return {"BTC": {k: {"bp": bp, "n": 1000, "from": "2021-01-01",
                            "to": "2026-08-01"}
                        for k, *_ in fx.METRICS}}

    def test_reports_percentile_per_metric(self):
        ctx = fx.context("BTC", {"funding": 990.0}, self.table())
        self.assertGreater(ctx["funding"]["pct"], 95)

    def test_missing_value_is_dropped_not_guessed(self):
        ctx = fx.context("BTC", {"funding": None, "ls_ratio": 500.0}, self.table())
        self.assertNotIn("funding", ctx)
        self.assertIn("ls_ratio", ctx)

    def test_unknown_coin_is_empty(self):
        self.assertEqual(fx.context("없는코인", {"funding": 1.0}, self.table()), {})

    def test_extremes_counts_both_tails(self):
        ctx = fx.context("BTC", {"funding": 995.0, "ls_ratio": 2.0,
                                 "taker_ratio": 500.0}, self.table())
        got = set(fx.extremes(ctx))
        self.assertIn("funding", got)
        self.assertIn("ls_ratio", got)
        self.assertNotIn("taker_ratio", got)

    def test_scale_is_applied_when_given(self):
        ctx = fx.context("BTC", {"funding": 5.0}, self.table(), {"funding": 100.0})
        self.assertEqual(ctx["funding"]["value"], 500.0)
        self.assertEqual(ctx["funding"]["raw"], 5.0)

    def test_no_scale_means_one(self):
        ctx = fx.context("BTC", {"funding": 5.0}, self.table())
        self.assertEqual(ctx["funding"]["scale"], 1.0)
        self.assertEqual(ctx["funding"]["value"], 5.0)


class TestDetectScales(unittest.TestCase):
    """단위는 **여러 코인을 한꺼번에** 보고 정해야 한다.

    코인 하나만 보고 정하면 '진짜 극단값'과 '단위가 다름'을 구분할
    수 없고, 하필 우리가 띄우려는 극단을 조용히 지워 버린다.
    """

    def table(self, coins):
        bp = fx.breakpoints([0.005 + 0.00001 * i for i in range(1000)])
        return {c: {"funding": {"bp": bp, "n": 1000,
                                "from": "2021-01-01", "to": "2026-08-01"}}
                for c in coins}

    def test_all_coins_off_by_100_is_a_unit_problem(self):
        coins = [f"C{i}" for i in range(10)]
        live = {c: {"funding": 0.00012} for c in coins}
        got = fx.detect_scales(live, self.table(coins))
        self.assertEqual(got["funding"]["how"], "mul")
        self.assertAlmostEqual(got["funding"]["k"], 100.0)

    def test_one_coin_at_an_extreme_is_not_rescaled(self):
        """이게 핵심이다. 한 종목만 극단이면 그건 오늘의 사건이다."""
        coins = [f"C{i}" for i in range(10)]
        live = {c: {"funding": 0.010} for c in coins}
        live["C0"]["funding"] = 0.00008          # 혼자만 아주 낮다
        got = fx.detect_scales(live, self.table(coins))
        self.assertNotIn("funding", got, got)

    def test_an_extreme_coin_keeps_its_extreme_percentile(self):
        coins = [f"C{i}" for i in range(10)]
        live = {c: {"funding": 0.010} for c in coins}
        live["C0"]["funding"] = 0.00008
        tab = self.table(coins)
        scales = fx.detect_scales(live, tab)
        ctx = fx.context("C0", live["C0"], tab, scales)
        self.assertEqual(ctx["funding"]["pct"], 0, "극단값이 지워졌다")
        self.assertIn("funding", fx.extremes(ctx))

    def test_too_few_coins_means_no_guess(self):
        coins = ["C0", "C1"]
        live = {c: {"funding": 0.00012} for c in coins}
        self.assertEqual(fx.detect_scales(live, self.table(coins)), {})


class TestRatioVersusShare(unittest.TestCase):
    """실제로 물린 것 — 10의 거듭제곱이 아니라 **비(比) vs 비율**.

    과거 테이커는 buy/(buy+sell) 인 비율(0~1)인데, 봇은 buy/sell 인
    비(比)를 준다. 매수≈매도면 비는 1.0, 비율은 0.5다. **두 배 차이라
    거듭제곱 검사는 그냥 통과시켰다** — 초록불을 잘못 켜 줬다.
    """

    def table(self, coins):
        # 매수비(0~1)가 0.49 언저리에 몰린 과거 분포
        bp = fx.breakpoints([0.46 + 0.00008 * i for i in range(1000)])
        return {c: {"taker_ratio": {"bp": bp, "n": 1000,
                                    "from": "2021-01-01", "to": "2026-08-01"}}
                for c in coins}

    def test_share_conversion_is_found(self):
        coins = [f"C{i}" for i in range(8)]
        live = {c: {"taker_ratio": 1.0 + 0.01 * i} for i, c in enumerate(coins)}
        got = fx.detect_scales(live, self.table(coins))
        self.assertIn("taker_ratio", got, "비 vs 비율을 못 잡았다")
        self.assertEqual(got["taker_ratio"]["how"], "share")

    def test_after_conversion_the_percentile_is_sane(self):
        coins = [f"C{i}" for i in range(8)]
        live = {c: {"taker_ratio": 1.0 + 0.01 * i} for i, c in enumerate(coins)}
        tab = self.table(coins)
        sc = fx.detect_scales(live, tab)
        ctx = fx.context("C0", live["C0"], tab, sc)
        self.assertAlmostEqual(ctx["taker_ratio"]["value"], 0.5, places=6)
        self.assertGreater(ctx["taker_ratio"]["pct"], 5)
        self.assertLess(ctx["taker_ratio"]["pct"], 95)

    def test_the_conversion_is_named_on_screen(self):
        coins = [f"C{i}" for i in range(8)]
        live = {c: {"taker_ratio": 1.0} for c in coins}
        tab = self.table(coins)
        sc = fx.detect_scales(live, tab)
        ctx = fx.context("C0", live["C0"], tab, sc)
        self.assertNotEqual(ctx["taker_ratio"]["form"], fx.IDENTITY["kind"],
                            "무엇을 바꿨는지 화면에 안 남는다")

    def test_a_value_already_in_range_is_left_alone(self):
        coins = [f"C{i}" for i in range(8)]
        live = {c: {"taker_ratio": 0.50} for c in coins}
        self.assertEqual(fx.detect_scales(live, self.table(coins)), {})


class TestCalibration(unittest.TestCase):
    """스물 중 열둘이 '역대 최고'면 종목이 특이한 게 아니라 비교가 틀린 것.

    실제로 테이커가 그랬다. 과거는 CoinGlass 의 **하루치** 매수/매도인데
    봇이 주는 지금 값은 **한 시간치**다. 단위가 아니라 시간 창이 다르다.
    거듭제곱 검사도, 비↔비율 검사도 이건 못 잡는다.
    """

    def ctxs(self, pcts):
        return [{"taker_ratio": {"pct": p, "name": "테이커", "value": 0.5,
                                 "raw": 0.5, "form": "그대로", "scale": 1.0,
                                 "pct_": p, "n": 1000, "from": "", "to": ""}}
                for p in pcts]

    def test_healthy_metric_is_about_two_tenths(self):
        pcts = list(range(0, 100, 5))          # 고르게 퍼짐
        cal = fx.calibration(self.ctxs(pcts))
        self.assertLess(cal["taker_ratio"], fx.MISCAL)
        self.assertEqual(fx.miscalibrated(cal), set())

    def test_everything_extreme_is_flagged(self):
        pcts = [0, 100] * 10
        cal = fx.calibration(self.ctxs(pcts))
        self.assertEqual(cal["taker_ratio"], 1.0)
        self.assertIn("taker_ratio", fx.miscalibrated(cal))

    def test_too_few_coins_is_not_judged(self):
        self.assertEqual(fx.calibration(self.ctxs([0, 100, 0])), {})

    def test_broken_metric_is_excluded_from_the_headline(self):
        """깨진 지표 하나 때문에 '3개 동시 극단'이 매일 뜨면 안 된다."""
        ctx = {"taker_ratio": {"pct": 100}, "funding": {"pct": 50},
               "ls_ratio": {"pct": 3}}
        self.assertEqual(len(fx.extremes(ctx)), 2)
        self.assertEqual(len(fx.extremes(ctx, skip={"taker_ratio"})), 1)


class TestTakerProbe(unittest.TestCase):
    """--taker 는 추측하지 않고 실제로 뭐가 오는지 본다 (--peek 과 같은 수법).

    이 수법이 지금까지 두 번 사람을 살렸다 — 롱숏비 칼럼 이름과
    비 vs 비율. 화면에 안 띄우면 그대로 잘못 쟀을 것들이다.
    """

    class FakeLiq:
        def __init__(self, shapes):
            self.shapes = shapes
            self.asked = []

        def get_taker_buy_sell(self, symbol, period="1h", limit=1):
            self.asked.append((period, limit))
            if (period, limit) not in self.shapes:
                raise ValueError(f"period {period} not supported")
            return self.shapes[(period, limit)]

    def drive(self, shapes):
        import contextlib
        import io as _io
        import sys as _sys
        import types
        mod = types.ModuleType("liquidation")
        fake = self.FakeLiq(shapes)
        mod.get_taker_buy_sell = fake.get_taker_buy_sell
        old = _sys.modules.get("liquidation")
        _sys.modules["liquidation"] = mod
        out = _io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                fx.cmd_taker("BTC")
        finally:
            if old is None:
                _sys.modules.pop("liquidation", None)
            else:
                _sys.modules["liquidation"] = old
        return out.getvalue(), fake

    def test_it_tries_a_daily_period(self):
        """하루치를 바로 주면 그게 제일 깨끗하다 — 과거와 같은 창이다."""
        _out, fake = self.drive({("1h", 1): {"ratio": 0.89}})
        self.assertIn(("1d", 1), fake.asked)

    def test_it_sums_24_hourly_records(self):
        rows = [{"taker_buy_volume_usd": 60, "taker_sell_volume_usd": 40}] * 24
        out, _f = self.drive({("1h", 24): rows})
        self.assertIn("0.6000", out, out)

    def test_unsupported_period_is_reported_not_swallowed(self):
        out, _f = self.drive({("1h", 1): {"ratio": 0.89}})
        self.assertIn("❌", out)

    def test_missing_module_does_not_crash(self):
        import contextlib
        import io as _io
        import sys as _sys
        old = _sys.modules.pop("liquidation", None)
        out = _io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                rc = fx.cmd_taker("BTC")
        finally:
            if old is not None:
                _sys.modules["liquidation"] = old
        self.assertEqual(rc, 1)


class TestOutside(unittest.TestCase):
    """'중앙값에서 멀다'가 아니라 '과거 범위에 아예 못 들어간다'가 신호다."""

    def setUp(self):
        self.bp = fx.breakpoints([float(i) for i in range(1000)])

    def test_inside_is_zero(self):
        self.assertEqual(fx.outside(self.bp, 500.0), 0.0)
        self.assertEqual(fx.outside(self.bp, 100.0), 0.0)

    def test_far_below_is_positive(self):
        self.assertGreater(fx.outside(self.bp, -5000.0), 0)

    def test_far_above_is_positive(self):
        self.assertGreater(fx.outside(self.bp, 50000.0), 0)


class TestBuild(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "c.jsonl")

    def tearDown(self):
        self.dir.cleanup()

    def write(self, rows):
        with open(self.path, "w", encoding="utf-8") as fp:
            for r in rows:
                fp.write(json.dumps(r) + "\n")

    def rows(self, kind, coin, n, mk):
        return [{"kind": kind, "coin": coin, "ts": NOW - i * DAY, "raw": mk(i)}
                for i in range(n)]

    def test_builds_a_table_from_enough_days(self):
        self.write(self.rows("ls_ratio", "BTC", 400,
                             lambda i: {"global_account_long_short_ratio": 1.0 + i / 400}))
        table, thin = fx.build(self.path)
        self.assertIn("ls_ratio", table["BTC"])
        self.assertGreaterEqual(table["BTC"]["ls_ratio"]["n"], 400)

    def test_thin_series_is_reported_not_silently_dropped(self):
        self.write(self.rows("ls_ratio", "BTC", 50,
                             lambda i: {"global_account_long_short_ratio": 1.0}))
        table, thin = fx.build(self.path)
        self.assertEqual(table, {})
        self.assertTrue(thin)

    def test_oi_becomes_a_change_not_a_level(self):
        self.write(self.rows("oi", "BTC", 500, lambda i: {"close": 1e9 + i * 1e6}))
        table, _ = fx.build(self.path)
        bp = table["BTC"]["oi_chg_24h"]["bp"]
        self.assertLess(abs(bp[len(bp) // 2]), 5.0, "수준을 그대로 담았다")

    def test_missing_file_is_empty(self):
        table, thin = fx.build(self.path + ".nope")
        self.assertEqual(table, {})


class TestWording(unittest.TestCase):
    """이 화면이 '사라'로 읽히면 안 된다.

    60개를 재서 이 값들로 진입 시점을 고를 수 없다는 것이 확인됐다.
    '평소와 다르다'와 '지금 사라'는 다른 말이다.
    """

    def test_no_zero_percent_wording(self):
        """'상위 0%' 는 읽는 사람을 멈칫하게 한다."""
        for p in (0, 100):
            self.assertNotIn("0%", fx.say(p), f"{p} → {fx.say(p)}")
        self.assertIn("최고", fx.say(100))
        self.assertIn("최저", fx.say(0))

    def test_say_never_gives_a_direction(self):
        for p in (0, 1, 5, 50, 95, 99, 100):
            s = fx.say(p)
            for word in ("매수", "매도", "사", "팔", "진입", "롱", "숏"):
                self.assertNotIn(word, s, f"{p} → {s}")

    def test_module_states_it_is_not_a_signal(self):
        self.assertIn("매매 신호가 아니다", fx.__doc__)


if __name__ == "__main__":
    unittest.main()
