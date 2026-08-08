"""patches/fix_history_fetch.py 회귀 테스트.

예전 코드는 `if len(batch) < 1000: break` 때문에, 거래소가 한 번에
1000봉을 안 주면 첫 배치만 받고 끝났다. 730일을 요청해도 8개월치만
재면서 그걸 몇 주 동안 몰랐다.

고정하는 것:
  · 한 번에 조금씩 주는 거래소에서도 요청한 기간을 채운다
  · 같은 배치를 계속 주는 거래소에서 무한루프에 빠지지 않는다
  · 현재까지 오면 멈춘다 (쓸데없이 더 부르지 않는다)
  · 빈 결과·예외에도 죽지 않는다
  · 기간을 못 채우면 조용히 넘어가지 않고 경고한다
"""

import io
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta

try:
    from .helpers import load_patch
except ImportError:            # 봇 폴더에 그대로 복사된 경우
    from helpers import load_patch

fx = load_patch("fix_history_fetch")

DAY_MS = 86_400_000
TF_MS = {"4h": DAY_MS // 6, "1d": DAY_MS, "1w": DAY_MS * 7}


def make_module(patched):
    """get_ohlcv_history 만 있는 최소 모듈을 만든다."""
    body = f'''
from datetime import datetime, timedelta
import pandas as pd

_BITGET_TF_MAP = {{"3d": "3Dutc", "1M": "1Mutc"}}
exchange = None


def get_ohlcv_history(symbol, timeframe, days):
    tf_minutes = {{"4h": 240, "1d": 1440, "1w": 10080}}
    minutes_per_candle = tf_minutes.get(timeframe, 1440)
    needed_candles     = int(days * 1440 / minutes_per_candle) + 50

{{LOOP}}

    if not all_ohlcv:
        return None

    df = pd.DataFrame(all_ohlcv, columns=['timestamp','open','high','low','close','volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df = df.drop_duplicates('timestamp').sort_values('timestamp').reset_index(drop=True)

{{TAIL}}
'''
    src = body.replace("{LOOP}", fx.LOOP_OLD).replace("{TAIL}", fx.WARN_OLD)
    if patched:
        out, items = fx.patch_backtest(src)
        assert all(s == fx.TODO for _, _, s in items), items
        src = out
    ns = {}
    exec(compile(src, "stub", "exec"), ns)
    return ns


class Exchange:
    """한 번에 cap 봉만 주는 거래소."""

    def __init__(self, cap=200, now_ms=None, stuck=False, empty=False, boom=False,
                 listed_days_ago=None):
        self.cap, self.stuck, self.empty, self.boom = cap, stuck, empty, boom
        self.calls = 0
        self.now = now_ms or int(datetime.now().timestamp() * 1000)
        # 최근에 상장된 코인 — 이 시각 이전 봉은 존재하지 않는다
        self.listed = (self.now - listed_days_ago * DAY_MS) if listed_days_ago else None

    def fetch_ohlcv(self, symbol, tf, since=None, limit=1000):
        self.calls += 1
        if self.boom:
            raise RuntimeError("rate limit")
        if self.empty:
            return []
        step = TF_MS[tf]
        start = since - (since % step) + step
        if self.listed is not None:
            start = max(start, self.listed)
        if self.stuck:                       # 항상 같은 구간을 준다
            start = self.now - step * self.cap
        out, t = [], start
        while t < self.now and len(out) < min(self.cap, limit):
            out.append([t, 100.0, 101.0, 99.0, 100.0, 1.0])
            t += step
        return out


def span_days(df):
    return (df["timestamp"].iloc[-1] - df["timestamp"].iloc[0]).days


class TestPagination(unittest.TestCase):

    def fetch(self, patched, tf="1d", days=830, **kw):
        ns = make_module(patched)
        ex = Exchange(**kw)
        ns["exchange"] = ex
        buf = io.StringIO()
        with redirect_stdout(buf):
            df = ns["get_ohlcv_history"]("BTC/USDT", tf, days)
        return df, ex, buf.getvalue()

    def test_old_code_stops_after_one_batch(self):
        """고치기 전에는 첫 배치만 받고 끝났다 — 이게 원인이었다."""
        df, ex, _ = self.fetch(patched=False)
        self.assertEqual(ex.calls, 1)
        self.assertLess(span_days(df), 300, "예전 코드가 830일을 채웠을 리 없다")

    def test_patched_fills_requested_span(self):
        df, ex, _ = self.fetch(patched=True)
        self.assertGreater(ex.calls, 1, "페이지를 안 넘겼다")
        self.assertGreaterEqual(span_days(df), 830 * 0.9)

    def test_patched_fills_intraday_too(self):
        df, ex, _ = self.fetch(patched=True, tf="4h")
        self.assertGreaterEqual(span_days(df), 830 * 0.9)

    def test_no_infinite_loop_when_exchange_repeats(self):
        """같은 구간만 돌려주는 거래소에서도 멈춰야 한다."""
        df, ex, _ = self.fetch(patched=True, stuck=True)
        self.assertLessEqual(ex.calls, 3, f"진전이 없는데 {ex.calls}번 불렀다")

    def test_stops_at_present(self):
        """현재까지 왔으면 목표 봉 수와 무관하게 그만 부른다."""
        df, ex, _ = self.fetch(patched=True, cap=10_000)
        self.assertEqual(ex.calls, 1)

    def test_empty_result_is_none(self):
        df, ex, _ = self.fetch(patched=True, empty=True)
        self.assertIsNone(df)

    def test_exception_does_not_crash(self):
        df, ex, out = self.fetch(patched=True, boom=True)
        self.assertIsNone(df)
        self.assertIn("데이터 수집 오류", out)
        self.assertIn("BTC/USDT", out, "어느 심볼에서 났는지 나와야 한다")

    def test_short_data_warns(self):
        """기간을 못 채우면 조용히 넘어가면 안 된다."""
        ns = make_module(patched=True)
        now = int(datetime.now().timestamp() * 1000)
        ns["exchange"] = Exchange(now_ms=now, listed_days_ago=30)  # 최근 상장
        buf = io.StringIO()
        with redirect_stdout(buf):
            ns["get_ohlcv_history"]("NEW/USDT", "1d", 830)
        self.assertIn("⚠️", buf.getvalue())
        self.assertIn("NEW/USDT", buf.getvalue())

    def test_full_data_does_not_warn(self):
        _, _, out = self.fetch(patched=True)
        self.assertNotIn("⚠️", out)


class TestPatchStatus(unittest.TestCase):

    def test_idempotent(self):
        body = fx.LOOP_OLD + "\n\n" + fx.WARN_OLD
        once, _ = fx.patch_backtest(body)
        twice, items = fx.patch_backtest(once)
        self.assertEqual(once, twice)
        self.assertTrue(all(s == fx.DONE for _, _, s in items), items)

    def test_missing_anchor_is_reported(self):
        _, items = fx.patch_backtest("아무것도 없는 파일")
        self.assertTrue(all(s == fx.GONE for _, _, s in items), items)


if __name__ == "__main__":
    unittest.main()
