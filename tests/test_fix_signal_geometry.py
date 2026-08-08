"""patches/fix_signal_geometry.py 회귀 테스트.

고정하는 것:
  · 손절이 '레벨 아래'와 '노이즈 밖'을 **둘 다** 만족해야 한다
  · 토글을 예전 값으로 되돌리면 예전 동작이 정확히 돌아와야 한다
  · 4H 필터 완화가 '역추세 배제'이지 '필터 제거'가 아니어야 한다
  · 앵커를 하나라도 못 찾으면 조용히 넘어가지 말고 ⚠️ 로 보고해야 한다
"""

import unittest

try:
    from .helpers import load_patch
except ImportError:            # 봇 폴더에 그대로 복사된 경우
    from helpers import load_patch

fx = load_patch("fix_signal_geometry")


STUB = f'''import pandas as pd

RSI_OVERSOLD     = 35
RSI_OVERBOUGHT   = 65


def round_px(v):
    return v


def compute_indicators(df):
    close, high, low = df['close'], df['high'], df['low']
{fx.ATR_ANCHOR}
    return df


{fx.HELPER_ANCHOR}
    signals = []
    for i in range(len(df_daily)):
        row = df_daily.iloc[i]
        price, rsi = row['close'], row['rsi']
        sup, res = row['recent_low'], row['recent_high']
        near_sup = near_res = True
        h4_up, h4_down = row['h4_up'], row['h4_down']

{fx.LONG_OLD}
            signals.append({{"type": "MID_LONG", "sl": sl}})

{fx.SHORT_OLD}
            signals.append({{"type": "MID_SHORT", "sl": sl}})
    return signals
'''


def build(**overrides):
    """패치를 적용한 스텁을 실행해 네임스페이스를 돌려준다."""
    out, items = fx.patch_backtest(STUB)
    # ⑦은 run_multi_backtest 를 손대는데 이 스텁엔 그 함수가 없다.
    assert all(s == fx.TODO for num, _, s in items if num != "⑦"), items
    ns = {}
    exec(compile(out, "stub", "exec"), ns)
    ns.update(overrides)
    return ns


class TestStopGeometry(unittest.TestCase):

    def setUp(self):
        self.ns = build()

    def row(self, atr):
        import pandas as pd
        return pd.Series({"atr": atr})

    def test_stop_clears_both_level_and_noise(self):
        """손절은 레벨 아래이면서 동시에 1.5×ATR 밖이어야 한다."""
        below = self.ns["_stop_below"]
        # ATR이 넓을 때 — 노이즈 쪽이 이긴다
        got = below(price=100.0, level=100.0, row=self.row(4.0))
        self.assertAlmostEqual(got, 100.0 - 1.5 * 4.0)
        self.assertLess(got, 100.0 * 0.99, "레벨 아래여야 한다")
        # ATR이 아주 좁을 때 — 레벨 쪽이 이긴다
        got = below(price=100.0, level=100.0, row=self.row(0.1))
        self.assertAlmostEqual(got, 99.0)

    def test_short_stop_is_mirror(self):
        above = self.ns["_stop_above"]
        self.assertAlmostEqual(above(100.0, 100.0, self.row(4.0)), 100.0 + 6.0)
        self.assertAlmostEqual(above(100.0, 100.0, self.row(0.1)), 101.0)

    def test_stop_is_outside_daily_noise(self):
        """예전 고정 1% 손절은 1.5×ATR의 11% 지점이었다 — 그 상태로 돌아가면 안 된다."""
        atr = 4.5           # 일간 변동성 4.5%짜리 코인
        old = 100.0 * 0.011
        new = 100.0 - self.ns["_stop_below"](100.0, 100.0, self.row(atr))
        self.assertLess(old / (1.5 * atr), 0.2, "예전 손절은 노이즈 안에 있었다")
        self.assertGreaterEqual(new / (1.5 * atr), 1.0, "새 손절은 노이즈 밖이어야 한다")

    def test_missing_atr_falls_back_to_level(self):
        """ATR이 아직 NaN인 초기 봉에서도 죽지 않고 예전 방식으로 간다."""
        import pandas as pd
        for bad in (float("nan"), 0.0, -1.0):
            self.assertAlmostEqual(
                self.ns["_stop_below"](100.0, 100.0, pd.Series({"atr": bad})), 99.0)
        self.assertAlmostEqual(
            self.ns["_stop_below"](100.0, 100.0, pd.Series({"close": 1.0})), 99.0)

    def test_toggle_restores_old_behaviour(self):
        ns = build(ATR_STOP_MULT=0.0)
        # 모듈 전역을 바꿔야 함수가 본다
        ns["_stop_below"].__globals__["ATR_STOP_MULT"] = 0.0
        self.assertAlmostEqual(ns["_stop_below"](100.0, 100.0, self.row(4.0)), 99.0)
        ns["_stop_below"].__globals__["ATR_STOP_MULT"] = 1.5


class TestH4Filter(unittest.TestCase):

    def fire(self, h4_up, h4_down, rsi, strict):
        import pandas as pd
        out, _ = fx.patch_backtest(STUB)
        ns = {}
        exec(compile(out, "stub", "exec"), ns)
        ns["MID_H4_STRICT"] = strict
        df = pd.DataFrame([{"close": 100.0, "rsi": rsi, "recent_low": 100.0,
                            "recent_high": 100.0, "atr": 2.0,
                            "h4_up": h4_up, "h4_down": h4_down}])
        sigs = ns["detect_signals_vectorized"](df, None, object())
        return {s["type"] for s in sigs}

    def test_relaxed_filter_allows_neutral_4h(self):
        """4H가 정배열도 역배열도 아닌 '중립'이면 통과해야 한다.

        예전 조건은 20일 최저점에서 4H 정배열을 요구해 사실상 모순이었다.
        """
        self.assertIn("MID_LONG", self.fire(False, False, rsi=30, strict=False))
        self.assertNotIn("MID_LONG", self.fire(False, False, rsi=30, strict=True))

    def test_relaxed_filter_still_blocks_counter_trend(self):
        """완화는 '필터 제거'가 아니다 — 역추세는 여전히 막아야 한다."""
        self.assertNotIn("MID_LONG", self.fire(False, True, rsi=30, strict=False))
        self.assertNotIn("MID_SHORT", self.fire(True, False, rsi=80, strict=False))

    def test_rsi_gate_still_applies(self):
        self.assertEqual(self.fire(False, False, rsi=50, strict=False), set())


class TestPatchStatus(unittest.TestCase):

    def states(self, items):
        return {n: s for n, _, s in items}

    def test_idempotent(self):
        once, _ = fx.patch_backtest(STUB)
        twice, items = fx.patch_backtest(once)
        self.assertEqual(once, twice)
        for num, state in self.states(items).items():
            if num != "⑦":
                self.assertEqual(state, fx.DONE, num)

    def test_missing_anchor_is_reported_not_silent(self):
        """앵커를 못 찾으면 ⚠️ 로 보고해야 한다 — 조용한 무시가 지난번 사고 원인."""
        broken = STUB.replace(fx.LONG_OLD, "        if False:\n            sl = 0.0")
        _, items = fx.patch_backtest(broken)
        self.assertEqual(self.states(items)["⑨a"], fx.GONE)
        # 나머지는 그래도 진행돼야 한다
        self.assertEqual(self.states(items)["⑨b"], fx.TODO)

    def test_seven_reports_partial_anchor_loss(self):
        """⑦은 조각이 7개다. 하나라도 못 찾으면 '적용됨'이라고 하면 안 된다."""
        _, items = fx.patch_backtest(STUB)   # STUB에는 run_multi_backtest가 없다
        self.assertEqual(self.states(items)["⑦"], fx.GONE)


if __name__ == "__main__":
    unittest.main()
