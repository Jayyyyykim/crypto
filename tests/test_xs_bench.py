"""patches/xs_bench.py 회귀 테스트.

횡단면은 **줄 세우는 방향이 뒤집혀도 결과가 그럴듯하게 나온다.**
'상위 롱'이 실제로는 하위를 잡고 있어도 숫자는 나오고, 그걸
알아챌 방법이 없다. 그래서 선택 방향을 시험으로 못박는다.

고정하는 것:
  · "top" 은 값이 큰 쪽, "bottom" 은 작은 쪽을 고른다
  · 그날 데이터가 있는 코인만 줄 세운다 (나중에 상장된 코인을
    과거 순위에 끼워 넣지 않는다)
  · 줄 세울 코인이 MIN_UNIVERSE 미만이면 아예 안 잡는다
  · 순위 값은 그날까지의 종가만 본다
  · 한 코인 동시 포지션 1개
"""

import types
import unittest

try:
    from .helpers import load_patch
except ImportError:            # 봇 폴더에 그대로 복사된 경우
    from helpers import load_patch

fx = load_patch("xs_bench")


def T(r):
    return {"r": r}


def frame(n=400, slope=0.0, start=100.0):
    import pandas as pd
    close = [start * (1 + slope) ** i for i in range(n)]
    return pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01", periods=n, freq="1D"),
        "open": close, "close": close,
        "high": [c * 1.005 for c in close], "low": [c * 0.995 for c in close],
        "atr": [c * 0.02 for c in close],
    })


class TestPrepare(unittest.TestCase):

    def test_momentum_columns(self):
        d = fx.prepare(frame(slope=0.01))
        self.assertAlmostEqual(d["mom30"].iloc[100], 1.01 ** 30 - 1, places=6)
        self.assertAlmostEqual(d["mom90"].iloc[200], 1.01 ** 90 - 1, places=6)

    def test_momentum_does_not_read_the_future(self):
        base = frame(slope=0.005)
        d1 = fx.prepare(base)
        tampered = base.copy()
        i = 250
        for col in ("close", "high", "low", "open"):
            tampered.loc[i + 1:, col] = tampered.loc[i + 1:, col] * 7
        d2 = fx.prepare(tampered)
        for col in ("mom30", "mom90", "mom30v"):
            self.assertAlmostEqual(d1[col].iloc[i], d2[col].iloc[i], places=9,
                                   msg=f"{col} 이 미래를 본다")

    def test_vol_adjusted_prefers_quiet_gains(self):
        """같은 상승률이면 변동성이 낮은 쪽이 높게 나와야 한다."""
        calm = fx.prepare(frame(slope=0.01))
        wild = frame(slope=0.01)
        wild["atr"] = wild["close"] * 0.08          # 4배 시끄럽게
        wild = fx.prepare(wild)
        self.assertGreater(calm["mom30v"].iloc[100], wild["mom30v"].iloc[100])


class TestExcessGate(unittest.TestCase):

    def test_thin_period_cannot_pass(self):
        d, lo, hi = fx.excess([T(1.0)] * (fx.MIN_PER_PERIOD - 1), [T(0.0)] * 500)
        self.assertEqual(lo, float("-inf"))

    def test_clear_excess_passes(self):
        d, lo, hi = fx.excess([T(1.0)] * 200, [T(0.0)] * 200)
        self.assertGreater(lo, 0)


class TestTradeShape(unittest.TestCase):

    def setUp(self):
        fx.bt = types.SimpleNamespace(
            ATR_STOP_MULT=1.5, evaluate_trade=lambda sig, df: {"r": 0.0})

    def test_geometry_matches_signal_bench(self):
        d = frame()
        lng = fx.make_trade(d, 250, True, "BTC")
        risk = d["atr"].iloc[250] * 1.5
        self.assertAlmostEqual(lng["entry"] - lng["sl"], risk)
        self.assertAlmostEqual(lng["tp2"] - lng["entry"], risk * 2)
        self.assertEqual(lng["_coin"], "BTC")

    def test_hold_bars_stops_on_the_stop(self):
        d = frame(n=100)
        d.loc[10:, "low"] = 1.0
        sig = {"idx": 5, "type": "MID_LONG", "sl": 50.0, "tp1": 9e9, "tp2": 9e9}
        self.assertEqual(fx.hold_bars(d, sig), 5)


class TestRanking(unittest.TestCase):
    """줄 세우는 방향이 뒤집히면 결론이 통째로 반대가 된다."""

    def setUp(self):
        import pandas as pd
        self.pd = pd
        fx.pd = pd
        # 코인마다 기울기를 다르게 — WIN 이 가장 많이 오르고 LOSE 가 가장 덜 오른다
        self.slopes = {}
        names = ["LOSE"] + [f"C{i}" for i in range(1, 14)] + ["WIN"]
        for k, nm in enumerate(names):
            self.slopes[nm] = 0.001 * k
        frames = {nm: frame(n=400, slope=s) for nm, s in self.slopes.items()}
        self.frames = frames
        fx.bt = types.SimpleNamespace(
            ATR_STOP_MULT=1.5,
            evaluate_trade=lambda sig, df: {"r": 0.0},
            compute_indicators=lambda df: df,
            get_ohlcv_history=lambda sym, tf, days: frames[sym.split("/")[0]].copy(),
        )
        fx.pick_coins = lambda n: [f"{c}/USDT" for c in list(frames)[:n]]

    def picks(self, name):
        hits, base, failed, sizes = fx.run(
            [f"{c}/USDT" for c in self.frames], 400)
        return {t["_coin"] for t in hits[name]}

    def test_top_picks_the_strongest(self):
        got = self.picks("30일 모멘텀 상위 롱")
        self.assertIn("WIN", got)
        self.assertNotIn("LOSE", got, "상위를 고른다면서 최약체를 잡았다")

    def test_bottom_picks_the_weakest(self):
        got = self.picks("30일 모멘텀 하위 숏")
        self.assertIn("LOSE", got)
        self.assertNotIn("WIN", got, "하위를 고른다면서 최강체를 잡았다")

    def test_only_top_k_are_picked(self):
        got = self.picks("30일 모멘텀 상위 롱")
        self.assertLessEqual(len(got), fx.TOP_K + 1,
                             f"{fx.TOP_K}종만 잡아야 하는데 {len(got)}종을 잡았다")

    def test_small_universe_is_skipped(self):
        """줄 세울 코인이 모자라면 순위에 뜻이 없다."""
        few = list(self.frames)[:fx.MIN_UNIVERSE - 1]
        hits, base, failed, sizes = fx.run([f"{c}/USDT" for c in few], 400)
        self.assertEqual(hits, {})

    def test_no_overlap_per_coin(self):
        hits, *_ = fx.run([f"{c}/USDT" for c in self.frames], 400)
        for name, ts in hits.items():
            per = {}
            for t in sorted(ts, key=lambda t: t["idx"]):
                prev = per.get(t["_coin"])
                if prev is not None:
                    self.assertGreater(t["idx"], prev, f"{name}/{t['_coin']} 겹침")
                per[t["_coin"]] = t["idx"]


if __name__ == "__main__":
    unittest.main()
