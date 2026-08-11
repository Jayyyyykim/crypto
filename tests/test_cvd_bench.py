"""patches/cvd_bench.py 회귀 테스트.

CVD 는 봇 프롬프트에 '아직 안 쟀다'로 남아 있던 마지막 구멍이다.
이 도구가 틀리면 그 구멍이 **틀린 답으로 메워진다** — 안 잰 것보다
나쁘다. "쟀는데 아니더라"를 믿고 다음 3개월을 보내게 되기 때문이다.

고정하는 것:
  · 누적을 절대값으로 안 쓴다 (시작점을 옮겨도 값이 같아야 한다)
  · 미래를 안 본다 — 누적 창도 분위수 창도 그날까지
  · 테이커 자료가 없으면 어느 후보도 발동하지 않는다
  · 거래가 0 인 날에 0/0 을 만들지 않는다
  · 하루치 비(이미 잰 것)와 다른 것을 재고 있다
  · 잰 개수를 정직하게 센다 (60 + 10)
"""

import json
import os
import tempfile
import unittest

import pandas as pd

try:
    from .helpers import load_patch
except ImportError:            # 봇 폴더에 그대로 복사된 경우
    from helpers import load_patch

fb = load_patch("feature_bench")
fx = load_patch("cvd_bench")
fx.fb = fb
fx.pd = pd

DAY = 86_400_000
NOW = 1_786_000_000_000


def rows(coin="BTC", n=40, buy=100.0, sell=50.0, kind="taker", start=NOW):
    out = []
    for i in range(n):
        out.append({"kind": kind, "coin": coin, "ts": start + i * DAY,
                    "raw": {"time": start + i * DAY,
                            "buy": buy, "sell": sell}})
    return out


def write(recs):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w", encoding="utf-8") as fp:
        for r in recs:
            fp.write(json.dumps(r) + "\n")
    return path


class TestLoadTaker(unittest.TestCase):
    """buy·sell 을 갈라서 읽어야 누적을 만들 수 있다."""

    def tearDown(self):
        for p in getattr(self, "_paths", []):
            os.unlink(p)

    def path(self, recs):
        p = write(recs)
        self._paths = getattr(self, "_paths", []) + [p]
        return p

    def test_both_sides_are_kept(self):
        t, cols, bad = fx.load_taker(self.path(rows(n=3)))
        self.assertEqual(len(t["BTC"]), 3)
        self.assertEqual(list(t["BTC"].values())[0], (100.0, 50.0))
        self.assertEqual(bad, 0)

    def test_it_says_which_columns_it_used(self):
        t, cols, _ = fx.load_taker(self.path(rows(n=2)))
        self.assertIn("buy", cols)
        self.assertIn("sell", cols)

    def test_alias_names_work(self):
        r = [{"kind": "taker", "coin": "ETH", "ts": NOW,
              "raw": {"time": NOW, "taker_buy_volume_usd": 8,
                      "taker_sell_volume_usd": 2}}]
        t, cols, _ = fx.load_taker(self.path(r))
        self.assertEqual(t["ETH"][list(t["ETH"])[0]], (8.0, 2.0))

    def test_other_kinds_are_ignored(self):
        t, _, _ = fx.load_taker(self.path(rows(kind="funding", n=3)))
        self.assertEqual(t, {})

    def test_last_of_the_day_wins(self):
        """feature_bench 와 같은 규칙이어야 한다. 다르면 두 결과를
        나란히 놓을 수 없다."""
        r = [{"kind": "taker", "coin": "BTC", "ts": NOW,
              "raw": {"buy": 1, "sell": 1}},
             {"kind": "taker", "coin": "BTC", "ts": NOW + 3600_000,
              "raw": {"buy": 9, "sell": 1}}]
        t, _, _ = fx.load_taker(self.path(r))
        self.assertEqual(len(t["BTC"]), 1)
        self.assertEqual(list(t["BTC"].values())[0], (9.0, 1.0))

    def test_mixed_time_units_land_on_one_day(self):
        r = [{"kind": "taker", "coin": "BTC", "ts": NOW // 1000,
              "raw": {"buy": 1, "sell": 1}},
             {"kind": "taker", "coin": "BTC", "ts": NOW,
              "raw": {"buy": 2, "sell": 1}}]
        t, _, _ = fx.load_taker(self.path(r))
        self.assertEqual(len(t["BTC"]), 1)

    def test_unusable_rows_are_counted(self):
        r = rows(n=2) + [{"kind": "taker", "coin": "BTC", "ts": NOW,
                          "raw": {"buy": 1}}]
        t, _, bad = fx.load_taker(self.path(r))
        self.assertEqual(bad, 1)

    def test_negative_volume_is_refused(self):
        r = [{"kind": "taker", "coin": "BTC", "ts": NOW,
              "raw": {"buy": -5, "sell": 1}}]
        t, _, bad = fx.load_taker(self.path(r))
        self.assertEqual(t, {})
        self.assertEqual(bad, 1)

    def test_missing_file_is_empty(self):
        t, cols, bad = fx.load_taker("없는파일.jsonl")
        self.assertEqual((t, cols, bad), ({}, None, 0))


class Frames(unittest.TestCase):

    N = 400

    def frame(self, n=None, closes=None):
        n = n or self.N
        return pd.DataFrame({
            "timestamp": pd.date_range("2024-01-01", periods=n, freq="1D"),
            "open": [100.0] * n,
            "close": closes if closes is not None else [100.0 + i for i in range(n)],
            "high": [1e6] * n, "low": [1.0] * n, "atr": [2.0] * n,
        })

    def taker(self, n=None, buy=100.0, sell=50.0, coin="BTC"):
        n = n or self.N
        days = pd.date_range("2024-01-01", periods=n, freq="1D")
        fb_ = buy if callable(buy) else (lambda i: buy)
        fs_ = sell if callable(sell) else (lambda i: sell)
        mk = lambda i: (fb_(i), fs_(i))
        return {coin: {d.strftime("%Y-%m-%d"): mk(i) for i, d in enumerate(days)}}


class TestAttach(Frames):

    def test_cvd_columns_are_added(self):
        d = fx.attach_cvd(self.frame(), {}, "BTC", self.taker())
        for c in ("cvd_s", "cvd_l", "cvd_s_hi", "cvd_l_lo",
                  "cvd_flip_up", "px_new_hi"):
            self.assertIn(c, d.columns)

    def test_cvd_is_a_share_not_a_running_total(self):
        """첫날부터 더하면 시작점을 옮길 때마다 값이 달라진다.
        그 값의 '상위'는 아무 뜻이 없다."""
        d = fx.attach_cvd(self.frame(), {}, "BTC", self.taker())
        v = d["cvd_l"].iloc[300]
        self.assertAlmostEqual(v, (100 - 50) / (100 + 50), places=6)
        self.assertLessEqual(abs(v), 1.0)

    def test_start_point_does_not_move_the_value(self):
        """자료를 앞에서 잘라도 같은 날의 CVD 는 같아야 한다."""
        full = self.taker()
        d1 = fx.attach_cvd(self.frame(), {}, "BTC", full)
        cut = {"BTC": {k: v for k, v in full["BTC"].items() if k >= "2024-03-01"}}
        d2 = fx.attach_cvd(self.frame(), {}, "BTC", cut)
        self.assertAlmostEqual(d1["cvd_l"].iloc[300], d2["cvd_l"].iloc[300],
                               places=9)

    def test_sell_heavy_is_negative(self):
        d = fx.attach_cvd(self.frame(), {}, "BTC", self.taker(buy=20.0, sell=80.0))
        self.assertLess(d["cvd_l"].iloc[300], 0)

    def test_zero_volume_day_does_not_become_nan_forever(self):
        d = fx.attach_cvd(self.frame(), {}, "BTC",
                          self.taker(buy=lambda i: 0.0 if i % 50 == 0 else 100.0,
                                     sell=lambda i: 0.0 if i % 50 == 0 else 50.0))
        self.assertFalse(pd.isna(d["cvd_l"].iloc[300]),
                         "거래 없는 날 하나가 누적을 통째로 지웠다")

    def test_it_does_not_read_the_future(self):
        base = self.frame()
        t1 = self.taker()
        t2 = {"BTC": dict(t1["BTC"])}
        for i, ts in enumerate(base["timestamp"]):
            if i > 250:
                t2["BTC"][ts.strftime("%Y-%m-%d")] = (9e9, 1.0)
        d1 = fx.attach_cvd(base, {}, "BTC", t1)
        d2 = fx.attach_cvd(base, {}, "BTC", t2)
        for col in ("cvd_s", "cvd_l", "cvd_l_hi", "cvd_l_lo"):
            self.assertAlmostEqual(d1[col].iloc[250], d2[col].iloc[250],
                                   places=9, msg=f"{col} 이 미래를 본다")

    def test_thresholds_move_with_time(self):
        d = fx.attach_cvd(self.frame(), {},  "BTC",
                          self.taker(buy=lambda i: 100.0 + i, sell=lambda i: 50.0))
        a, b = d["cvd_l_hi"].iloc[150], d["cvd_l_hi"].iloc[350]
        self.assertNotAlmostEqual(a, b, msg="문턱이 안 움직인다 — 상수다")

    def test_flip_is_a_one_day_event(self):
        """전환은 '지금 뒤집혔다'는 하루짜리 사건이다. 계속 켜져
        있으면 그건 전환이 아니라 상태다."""
        n = 300
        d = fx.attach_cvd(
            self.frame(n), {}, "BTC",
            self.taker(n, buy=lambda i: 20.0 if i < 150 else 80.0,
                       sell=lambda i: 80.0 if i < 150 else 20.0))
        ups = int(d["cvd_flip_up"].sum())
        self.assertEqual(ups, 1, f"전환이 {ups}번 잡혔다")

    def test_price_extremes_are_marked(self):
        closes = [100.0] * 100 + [200.0] + [100.0] * 99
        d = fx.attach_cvd(self.frame(200, closes), {}, "BTC", self.taker(200))
        self.assertTrue(d["px_new_hi"].iloc[100])
        self.assertFalse(d["px_new_hi"].iloc[150])

    def test_a_flat_price_is_never_a_new_high(self):
        """오늘을 창에 넣고 >= 로 보면 횡보장 전체가 '신고가'가 된다.
        그러면 엇갈림 후보는 아무것도 안 재는 것과 같아진다."""
        d = fx.attach_cvd(self.frame(200, [100.0] * 200), {}, "BTC",
                          self.taker(200))
        self.assertEqual(int(d["px_new_hi"].sum()), 0)
        self.assertEqual(int(d["px_new_lo"].sum()), 0)


class TestCandidates(Frames):

    def test_every_candidate_is_evaluable(self):
        d = fx.attach_cvd(self.frame(), {}, "BTC",
                          self.taker(buy=lambda i: 100.0 + (i % 37) * 4,
                                     sell=lambda i: 50.0 + (i % 23) * 3))
        for name, side, cond in fx.CANDIDATES:
            for i in (200, 300, 390):
                bool(cond(d, i))          # 예외가 나면 실패

    def test_no_taker_data_fires_nothing(self):
        """자료가 없는데 조건이 참이 되면 없는 신호를 세게 된다."""
        d = fx.attach_cvd(self.frame(), {}, "BTC", {})
        for name, side, cond in fx.CANDIDATES:
            for i in (200, 300, 390):
                self.assertFalse(bool(cond(d, i)),
                                 f"{name} 이 자료 없이 발동했다 (i={i})")

    def test_both_directions_are_tested(self):
        sides = {s for _, s, _ in fx.CANDIDATES}
        self.assertEqual(sides, {True, False})

    def test_divergence_candidates_exist(self):
        """엇갈림이 CVD 이야기의 핵심이다. 그걸 안 재면 잰 게 아니다."""
        names = [n for n, _, _ in fx.CANDIDATES]
        self.assertGreaterEqual(sum("엇갈림" in n for n in names), 2)

    def test_bearish_divergence_fires_where_it_should(self):
        # 가격은 계속 신고가, CVD 는 마지막에 평소보다 처진다
        d = fx.attach_cvd(self.frame(), {}, "BTC",
                          self.taker(buy=lambda i: 20.0 if i > 280 else 80.0,
                                     sell=lambda i: 80.0 if i > 280 else 20.0))
        cond = next(c for n, _, c in fx.CANDIDATES if n.startswith("약세 엇갈림"))
        self.assertTrue(bool(cond(d, 300)))

    def test_bearish_divergence_stays_quiet_when_cvd_agrees(self):
        d = fx.attach_cvd(self.frame(), {}, "BTC",
                          self.taker(buy=lambda i: 80.0 if i > 280 else 20.0,
                                     sell=lambda i: 20.0 if i > 280 else 80.0))
        cond = next(c for n, _, c in fx.CANDIDATES if n.startswith("약세 엇갈림"))
        self.assertFalse(bool(cond(d, 300)))

    def test_divergence_line_is_not_zero(self):
        """실측에서 30종 전부 델타 평균이 음수였다. 0 을 기준선으로
        쓰면 '음수' 는 1,252건, '양수' 는 24건 — 한쪽은 30건 미만이라
        판정 보류로 빠진다. 잰 게 아니라 못 잰 것이다."""
        n = 400
        # 항상 매도 우위지만, 뒤로 갈수록 덜하다
        d = fx.attach_cvd(
            self.frame(n), {}, "BTC",
            self.taker(n, buy=lambda i: 40.0 + i * 0.05, sell=60.0))
        self.assertLess(d["cvd_l"].iloc[350], 0, "여전히 매도 우위다")
        over = next(c for n_, _, c in fx.CANDIDATES if n_.startswith("강세 엇갈림"))
        # 값이 음수여도 '평소보다 높다' 는 잡혀야 한다
        self.assertTrue(fx._over(d, 350, "cvd_l"),
                        "0 을 기준으로 쓰고 있다 — 한쪽이 통째로 안 잡힌다")

    def test_both_divergence_sides_are_reachable(self):
        """한쪽만 잡히면 그 후보는 30건 미만으로 판정 보류가 된다."""
        n = 400
        d = fx.attach_cvd(
            self.frame(n), {}, "BTC",
            self.taker(n, buy=lambda i: 50.0 + 20.0 * ((i // 13) % 2),
                       sell=60.0))
        rng = range(200, n - 1)
        self.assertGreater(sum(fx._under(d, i, "cvd_l") for i in rng), 20)
        self.assertGreater(sum(fx._over(d, i, "cvd_l") for i in rng), 20)

    def test_it_is_not_measuring_the_daily_ratio_again(self):
        """하루치 비는 feature_bench 가 이미 쟀다. 같은 걸 또 재면
        '새로 쟀다'가 거짓말이 된다."""
        n = 400
        # 하루치 비는 매일 똑같은데 누적은 움직이는 자료
        d = fx.attach_cvd(
            self.frame(n), {}, "BTC",
            self.taker(n, buy=lambda i: 100.0 if i % 2 else 20.0,
                       sell=lambda i: 20.0 if i % 2 else 100.0))
        self.assertLess(abs(d["cvd_l"].iloc[300]), 0.2,
                        "번갈아 나오면 누적은 0 근처여야 한다")


class TestHonesty(unittest.TestCase):
    """몇 개를 쟀는지가 결과만큼 중요하다."""

    def test_prior_count_is_recorded(self):
        self.assertEqual(fx.TESTED_BEFORE, 60)

    def test_total_is_seventy(self):
        self.assertEqual(fx.TESTED_BEFORE + len(fx.CANDIDATES), 70)

    def test_candidate_names_are_unique(self):
        names = [n for n, _, _ in fx.CANDIDATES]
        self.assertEqual(len(names), len(set(names)))


class TestSharedHarness(unittest.TestCase):
    """재는 자가 도구마다 다르면 결과를 나란히 놓을 수 없다."""

    def test_feature_bench_run_accepts_our_candidates(self):
        import inspect
        sig = inspect.signature(fb.run)
        self.assertIn("candidates", sig.parameters)
        self.assertIn("attach_fn", sig.parameters)

    def test_defaults_are_unchanged_for_feature_bench(self):
        import inspect
        sig = inspect.signature(fb.run)
        self.assertIsNone(sig.parameters["candidates"].default)
        self.assertIsNone(sig.parameters["attach_fn"].default)

    def test_the_gate_is_the_same_one(self):
        self.assertIs(fx.report.__globals__["fb"].excess, fb.excess)
        self.assertIs(fx.report.__globals__["fb"].split, fb.split)


if __name__ == "__main__":
    unittest.main()
