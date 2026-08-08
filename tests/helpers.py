"""테스트용 합성 OHLCV 생성기 + 패치 스크립트 로더."""

import importlib.util
import os
import unittest

import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_patch(name):
    """patches/<name>.py 를 모듈로 불러온다.

    이 tests/ 폴더는 봇 폴더에 그대로 복사돼 돌아가기도 한다. 거기서는
    패치 스크립트가 patches/ 가 아니라 봇 루트에 있거나 아예 없다.
    못 찾으면 그 파일의 테스트만 건너뛴다 — import 단계에서 죽으면
    나머지 테스트까지 통째로 안 돌아간다.
    """
    for cand in (os.path.join(_ROOT, "patches", name + ".py"),
                 os.path.join(_ROOT, name + ".py")):
        if os.path.exists(cand):
            spec = importlib.util.spec_from_file_location(name, cand)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    raise unittest.SkipTest(f"{name}.py 를 찾지 못해 건너뜁니다 (패치 스크립트 전용 테스트)")


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
