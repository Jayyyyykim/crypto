"""patches/tv_levels.py 회귀 테스트.

남의 지표를 역산한 것이다. 여기가 틀리면 **틀린 레벨을 원본이라
믿고** 재게 된다 — 안 재는 것보다 나쁘다. "쟀는데 아니더라"를
근거로 다음 3개월을 보내기 때문이다.

구조(중심·배수·손절 간격)는 CSV 900줄에서 소수점까지 맞았다.
그건 못 박는다. 폭은 근사고, 근사라는 사실도 못 박는다.

고정하는 것:
  · 중심은 **그날 시가**다 (전날 종가가 아니라 오늘 시가)
  · 배수는 [1, 2.402, 4.512], 손절은 폭×0.6 더 바깥
  · 지지·저항이 중심에 대칭이다
  · 미래를 안 본다 — 오늘 ATR 을 쓰지 않는다
  · 자료가 모자라면 None (틀린 값을 내지 않는다)
"""

import unittest

import pandas as pd

try:
    from .helpers import load_patch
except ImportError:            # 봇 폴더에 그대로 복사된 경우
    from helpers import load_patch

fx = load_patch("tv_levels")


def frame(n=60, base=100.0, rng=2.0):
    """일봉 흉내. 매일 같은 폭으로 움직인다."""
    return pd.DataFrame({
        "open":  [base] * n,
        "high":  [base + rng] * n,
        "low":   [base - rng] * n,
        "close": [base] * n,
    })


class TestShape(unittest.TestCase):
    """CSV 900줄에서 확인된 구조."""

    def test_the_multipliers_are_pinned(self):
        self.assertEqual(fx.TF_MULT, (1.0, 2.402, 4.512))
        self.assertEqual(fx.SL_MULT, 0.6)

    def test_center_is_todays_open(self):
        d = frame()
        d.loc[59, "open"] = 123.45
        lv = fx.daily_levels(d)
        self.assertAlmostEqual(lv["center"].iloc[-1], 123.45)

    def test_levels_are_symmetric_about_the_center(self):
        lv = fx.daily_levels(frame())
        c = lv["center"].iloc[-1]
        for i in (1, 2, 3):
            up = lv[f"res{i}"].iloc[-1] - c
            dn = c - lv[f"sup{i}"].iloc[-1]
            self.assertAlmostEqual(up, dn, places=9)

    def test_the_three_widths_keep_the_ratio(self):
        lv = fx.daily_levels(frame())
        c = lv["center"].iloc[-1]
        w = lv["res1"].iloc[-1] - c
        self.assertAlmostEqual((lv["res2"].iloc[-1] - c) / w, 2.402, places=6)
        self.assertAlmostEqual((lv["res3"].iloc[-1] - c) / w, 4.512, places=6)

    def test_the_stop_sits_outside_the_level(self):
        lv = fx.daily_levels(frame())
        w = lv["width"].iloc[-1]
        self.assertAlmostEqual(lv["res1_sl"].iloc[-1] - lv["res1"].iloc[-1],
                               w * 0.6, places=9)
        self.assertAlmostEqual(lv["sup1"].iloc[-1] - lv["sup1_sl"].iloc[-1],
                               w * 0.6, places=9)


class TestNoLookahead(unittest.TestCase):
    """원본은 오늘 봉을 쓰는 것으로 보인다. 우리는 안 쓴다."""

    def test_todays_bar_does_not_move_todays_levels(self):
        a = frame()
        b = frame()
        b.loc[59, "high"] = 999.0        # 오늘만 크게 흔든다
        b.loc[59, "low"] = 1.0
        la = fx.daily_levels(a)["res1"].iloc[-1]
        lb = fx.daily_levels(b)["res1"].iloc[-1]
        self.assertAlmostEqual(la, lb, places=9,
                               msg="오늘 고저가 오늘 레벨을 움직인다 — 미래를 본다")

    def test_yesterday_does_move_them(self):
        a = frame()
        b = frame()
        b.loc[58, "high"] = 130.0
        self.assertNotAlmostEqual(fx.daily_levels(a)["res1"].iloc[-1],
                                  fx.daily_levels(b)["res1"].iloc[-1])


class TestWidth(unittest.TestCase):

    def test_width_is_half_the_atr(self):
        d = frame(rng=2.0)               # 매일 고저폭 4.0, TR 4.0
        lv = fx.daily_levels(d)
        self.assertAlmostEqual(lv["width"].iloc[-1], 4.0 * fx.ATR_K, places=6)

    def test_the_approximation_is_declared(self):
        """근사를 근사라고 안 적으면 다음 사람이 원본으로 믿는다."""
        self.assertIn("근사", fx.__doc__)
        self.assertIn("되그리기", fx.__doc__)


class TestBotEntry(unittest.TestCase):
    """봇에서 부르는 입구."""

    def ohlcv(self, n=60, bad=None):
        def fn(symbol, timeframe, limit=200):
            if bad == "none":
                return None
            if bad == "short":
                return frame(3)
            return frame(n)
        return fn

    def test_it_returns_every_level(self):
        got = fx.levels_now(self.ohlcv(), "BTC/USDT")
        for k in ("center", "width", "res1", "res2", "res3",
                  "sup1", "sup2", "sup3", "res1_sl", "sup1_sl"):
            self.assertIn(k, got)
            self.assertIsInstance(got[k], float)

    def test_no_data_is_none_not_a_guess(self):
        self.assertIsNone(fx.levels_now(self.ohlcv(bad="none"), "BTC/USDT"))
        self.assertIsNone(fx.levels_now(self.ohlcv(bad="short"), "BTC/USDT"))

    def test_warmup_is_none_not_nan(self):
        """ATR 이 아직 안 익었으면 값을 내면 안 된다."""
        def fn(symbol, timeframe, limit=200):
            return frame(11)             # ATR9 + shift 에 겨우 모자람
        got = fx.levels_now(fn, "BTC/USDT")
        if got is not None:
            for v in got.values():
                self.assertEqual(v, v, "NaN 을 값인 척 돌려줬다")

    def test_it_asks_for_daily_bars(self):
        seen = []

        def fn(symbol, timeframe, limit=200):
            seen.append(timeframe)
            return frame()
        fx.levels_now(fn, "BTC/USDT")
        self.assertEqual(seen, ["1d"], "일봉 기준 계산인데 다른 봉을 부른다")


class TestCheckCommand(unittest.TestCase):

    def test_check_refuses_a_csv_without_indicator_columns(self):
        import io
        import os
        import tempfile
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "x.csv")
            frame(5).to_csv(p, index=False)
            out = io.StringIO()
            with redirect_stdout(out):
                rc = fx.check([p])
            self.assertEqual(rc, 1)
            self.assertIn("지표 칸이 없습니다", out.getvalue())

    def test_check_survives_a_missing_file(self):
        import io
        from contextlib import redirect_stdout
        out = io.StringIO()
        with redirect_stdout(out):
            rc = fx.check(["없는파일_12345.csv"])
        self.assertEqual(rc, 1)

    def test_an_unmatched_pattern_says_so(self):
        """윈도우 cmd 는 *.csv 를 안 풀어 준다. 그걸 그대로 열려고 하면
        [Errno 22] 라는 알 수 없는 소리가 난다 — 실제로 그랬다."""
        import io
        import os
        import tempfile
        from contextlib import redirect_stdout
        old = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp)
                out = io.StringIO()
                with redirect_stdout(out):
                    rc = fx.check(["*.csv"])
            finally:
                os.chdir(old)
        self.assertEqual(rc, 1)
        t = out.getvalue()
        self.assertIn("맞는 파일이 없습니다", t)
        self.assertIn("지금 폴더", t)
        self.assertNotIn("Errno", t)


if __name__ == "__main__":
    unittest.main()
