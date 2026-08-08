"""patches/fix_ratelimit.py 회귀 테스트.

429 하나에 그 코인의 데이터가 통째로 잘리면서, 같은 명령을 두 번
돌린 결과가 20% 달랐다. 그 상태로는 어떤 A/B 도 성립하지 않는다.

고정하는 것:
  · 429 는 기다렸다 다시 묻는다 (그리고 결국 성공하면 데이터를 다 받는다)
  · 429 가 아닌 오류는 재시도하지 않는다 (헛되이 늘어지면 안 된다)
  · 재시도 상한을 넘기면 포기하되 죽지는 않는다
  · 백오프가 실제로 늘어난다 (같은 간격으로 두들기면 더 막힌다)
"""

import io
import unittest
from contextlib import redirect_stdout
from datetime import datetime

try:
    from .helpers import load_patch
except ImportError:            # 봇 폴더에 그대로 복사된 경우
    from helpers import load_patch

fx = load_patch("fix_ratelimit")
hist = load_patch("fix_history_fetch")

DAY_MS = 86_400_000
TF_MS = {"4h": DAY_MS // 6, "1d": DAY_MS, "1w": DAY_MS * 7}


def make_module():
    """⑪(페이지 넘기기) + ⑬(429 대응)이 모두 들어간 최소 모듈."""
    body = '''
from datetime import datetime, timedelta
import pandas as pd

_BITGET_TF_MAP = {"3d": "3Dutc", "1M": "1Mutc"}
exchange = None


def get_ohlcv_history(symbol, timeframe, days):
    tf_minutes = {"4h": 240, "1d": 1440, "1w": 10080}
    minutes_per_candle = tf_minutes.get(timeframe, 1440)
    needed_candles     = int(days * 1440 / minutes_per_candle) + 50

{LOOP}

    if not all_ohlcv:
        return None

    df = pd.DataFrame(all_ohlcv, columns=['timestamp','open','high','low','close','volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df = df.drop_duplicates('timestamp').sort_values('timestamp').reset_index(drop=True)

{TAIL}
'''
    src = body.replace("{LOOP}", hist.LOOP_OLD).replace("{TAIL}", hist.WARN_OLD)
    src, items = hist.patch_backtest(src)
    assert all(s == hist.TODO for _, _, s in items), items

    # ⑬은 fetch 블록과 상수만 손댄다. 상수는 별도 앵커라 여기 직접 붙인다.
    src = "RETRY_ON_429 = 4\nRETRY_BACKOFF_SEC = 1.5\n" + src
    assert fx.FETCH_OLD in src, "⑬이 잡을 fetch 블록이 없다"
    src = src.replace(fx.FETCH_OLD, fx.FETCH_NEW, 1)

    ns = {"_time": FakeTime()}
    exec(compile(src, "stub", "exec"), ns)
    return ns


class FakeTime:
    """진짜로 안 자고, 얼마나 잤는지만 기록한다."""

    def __init__(self):
        self.slept = []

    def sleep(self, sec):
        self.slept.append(sec)


class Exchange:
    def __init__(self, fail_times=0, err="bitget 429 Too Many Requests", cap=200):
        self.fail_times, self.err, self.cap = fail_times, err, cap
        self.calls = 0
        self.now = int(datetime.now().timestamp() * 1000)

    def fetch_ohlcv(self, symbol, tf, since=None, limit=1000):
        self.calls += 1
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError(self.err)
        step = TF_MS[tf]
        out, t = [], since - (since % step) + step
        while t < self.now and len(out) < min(self.cap, limit):
            out.append([t, 100.0, 101.0, 99.0, 100.0, 1.0])
            t += step
        return out


def run(ex, days=830, tf="1d"):
    ns = make_module()
    ns["exchange"] = ex
    clock = ns["_time"]
    buf = io.StringIO()
    with redirect_stdout(buf):
        df = ns["get_ohlcv_history"]("BTC/USDT", tf, days)
    return df, clock, buf.getvalue()


class TestRetry(unittest.TestCase):

    def test_429_is_retried_and_recovers(self):
        """두 번 막히고 세 번째에 성공하면 데이터를 온전히 받아야 한다."""
        ex = Exchange(fail_times=2)
        df, clock, out = run(ex)
        self.assertIsNotNone(df)
        span = (df["timestamp"].iloc[-1] - df["timestamp"].iloc[0]).days
        self.assertGreaterEqual(span, 830 * 0.9, "재시도 후에도 데이터가 잘렸다")
        self.assertEqual(len(clock.slept), 2)
        self.assertNotIn("데이터 수집 오류", out)

    def test_backoff_grows(self):
        """같은 간격으로 두들기면 더 막힌다."""
        ex = Exchange(fail_times=3)
        _, clock, _ = run(ex)
        self.assertEqual(clock.slept, [1.5, 3.0, 6.0])

    def test_non_429_is_not_retried(self):
        ex = Exchange(fail_times=1, err="ValueError: 잘못된 심볼")
        df, clock, out = run(ex)
        self.assertEqual(clock.slept, [], "429가 아닌데 기다렸다")
        self.assertEqual(ex.calls, 1)
        self.assertIn("데이터 수집 오류", out)

    def test_gives_up_after_limit_without_crashing(self):
        ex = Exchange(fail_times=999)
        df, clock, out = run(ex)
        self.assertIsNone(df)
        self.assertEqual(ex.calls, 4, "재시도 상한을 넘겼다")
        self.assertEqual(len(clock.slept), 3, "마지막 시도 뒤엔 안 기다려야 한다")
        self.assertIn("데이터 수집 오류", out)

    def test_no_failure_means_no_sleep(self):
        _, clock, out = run(Exchange())
        self.assertEqual(clock.slept, [])
        self.assertNotIn("데이터 수집 오류", out)


class TestPatchStatus(unittest.TestCase):

    def stub(self):
        return ("import ccxt\nimport pandas as pd\n\n"
                + fx.EX_OLD + "\n\n" + fx.FETCH_OLD + "\n\n" + fx.WORKERS_OLD + "\n")

    def test_all_four_apply(self):
        _, items = fx.patch_backtest(self.stub())
        self.assertEqual([s for _, _, s in items], [fx.TODO] * 4)

    def test_idempotent(self):
        once, _ = fx.patch_backtest(self.stub())
        twice, items = fx.patch_backtest(once)
        self.assertEqual(once, twice)
        self.assertTrue(all(s == fx.DONE for _, _, s in items), items)

    def test_missing_anchor_is_reported(self):
        _, items = fx.patch_backtest("아무것도 없는 파일")
        self.assertTrue(all(s == fx.GONE for _, _, s in items), items)


if __name__ == "__main__":
    unittest.main()
