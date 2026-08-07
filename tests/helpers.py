"""테스트용 합성 OHLCV 생성기."""

import pandas as pd


def make_df(bars):
    """bars: [(open, high, low, close), ...] → 봇 get_ohlcv() 형식 DataFrame"""
    rows = []
    ts = pd.Timestamp("2026-01-01")
    for i, b in enumerate(bars):
        o, h, l, c = b
        rows.append({
            "timestamp": ts + pd.Timedelta(days=i),
            "open": o, "high": h, "low": l, "close": c, "volume": 1000.0,
        })
    return pd.DataFrame(rows)


def flat_bars(n, price=100.0, spread=1.0):
    """가격이 거의 안 움직이는 봉 n개 (워밍업 채우기용)."""
    return [(price, price + spread, price - spread, price) for _ in range(n)]


def walk(n, start=100.0, drift=0.0, amp=1.0):
    """결정적 지그재그 워크 — 난수 없이 재현 가능하게."""
    bars = []
    px = start
    for i in range(n):
        nxt = px * (1 + drift) + (amp if i % 2 == 0 else -amp)
        hi = max(px, nxt) + amp * 0.5
        lo = min(px, nxt) - amp * 0.5
        bars.append((px, hi, lo, nxt))
        px = nxt
    return bars
