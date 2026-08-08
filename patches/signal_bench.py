"""signal_bench.py — 새 진입 규칙을 무작위 진입과 겨루게 하는 시험대 (읽기 전용)

    cd /path/to/auto
    python signal_bench.py             # 20종 × 730일, 후보 전부
    python signal_bench.py 20 2y
    python signal_bench.py --list      # 후보 목록만

**아무 파일도 고치지 않습니다.**

왜 이게 필요한가
───────────────
지금까지 신호 하나를 고칠 때마다 패치를 만들고 봇에 넣고 돌려 보는
데 한 바퀴가 걸렸다. 그런데 diag_baseline 결과는 **여섯 개 전부 무작위
진입을 못 이겼다**는 것이었다. 고칠 대상이 아니라 다시 만들 대상이고,
새로 만들려면 **아이디어를 싸게 버리는 길**이 먼저 있어야 한다.

여기서는 진입 규칙만 함수 하나로 적으면 나머지는 자동으로 붙는다:

    · 손절 1.5×ATR14, 목표 1R/2R/3R  ← 모든 후보가 동일
    · 수수료·슬리피지 동일
    · 같은 코인·기간의 무작위 진입과 비교
    · 전반기·후반기 따로 (국면 의존 걸러내기)
    · 두 기간 모두 초과의 신뢰구간 하한이 0을 넘어야 통과

  **진입 시점만 다르고 나머지는 전부 같다.** 그래야 좋아진 게
  '언제 들어갔나' 덕인지 알 수 있다. 지금까지 이걸 안 해서
  손절 바꾼 효과와 신호 효과가 섞였다.

후보 추가하는 법
───────────────
아래 CANDIDATES 에 한 줄 추가하면 끝이다.

    ("이름", 롱이면 True, lambda d, i: 조건식)

  d 는 지표가 붙은 DataFrame, i 는 봉 인덱스다. i 이후를 보면
  미래를 보는 것이므로 **d.iloc[i] 와 그 이전만** 쓴다.

지금 들어 있는 후보의 근거
────────────────────────
diag_funnel·diag_baseline 이 알려준 것에서 골랐다.

  · 지금 봇은 전부 '역추세 되돌림'(최저점 근처에서 롱)이다.
    반대 계열인 **돌파 추종**을 한 번도 재본 적이 없다.
  · 중기숏은 무작위보다 **유의하게 나빴다**(초과 -0.280R,
    95% 구간 [-0.550, -0.010]). 뒤집으면 어떤지 확인한다.
  · 되돌림을 하려면 큰 추세 안에서 해야 한다는 통설을 그대로 건다
    (ma200 위에서만 RSI 되돌림 롱).
  · 변동성 수축 뒤 확장은 방향과 무관하게 자주 쓰이는 자리다.

  전부 '해봤더니 어떻더라'를 얻기 위한 것이지, 이게 답이라는
  주장이 아니다. 통과 못 하면 버리는 게 이 도구의 용도다.
"""

import math
import os
import sys
import time
import zlib

pd = None
bt = None

SAMPLES_PER_COIN = 60      # 무작위 기준선, 방향당


def _load():
    global pd, bt
    if bt is not None:
        return True
    for cand in (os.getcwd(), os.path.dirname(os.path.abspath(__file__))):
        if os.path.exists(os.path.join(cand, "backtest.py")) and cand not in sys.path:
            sys.path.insert(0, cand)
    try:
        import console
        console.enable_utf8()
    except Exception:
        pass
    try:
        import pandas
        import backtest
    except ImportError as e:
        print(f"backtest.py 를 못 불렀습니다: {e}")
        print(f"지금 폴더: {os.getcwd()}")
        return False
    pd, bt = pandas, backtest
    return True


# ── 후보 진입 규칙 ────────────────────────────────────────────
#
# (이름, 롱?, 조건)  — 조건은 d.iloc[i] 와 그 이전만 본다.

def _c(d, i, col):
    return d[col].iloc[i]


CANDIDATES = [
    # 돌파 추종 — 지금 봇에 없는 계열이다.
    ("돌파롱 20일신고", True,
     lambda d, i: _c(d, i, "close") > _c(d, i - 1, "hh20") and _c(d, i, "close") > _c(d, i, "ma200")),
    ("돌파숏 20일신저", False,
     lambda d, i: _c(d, i, "close") < _c(d, i - 1, "ll20") and _c(d, i, "close") < _c(d, i, "ma200")),

    # 추세 안에서의 되돌림 — 되돌림을 하려면 큰 추세를 등지라는 통설
    ("추세내 되돌림롱", True,
     lambda d, i: _c(d, i, "close") > _c(d, i, "ma200") and _c(d, i, "rsi") < 35),
    ("추세내 되돌림숏", False,
     lambda d, i: _c(d, i, "close") < _c(d, i, "ma200") and _c(d, i, "rsi") > 65),

    # 변동성 수축 뒤 확장
    ("변동성수축 후 상승", True,
     lambda d, i: _c(d, i - 1, "atr_pct") <= _c(d, i - 1, "atr_pct_q20")
                  and _c(d, i, "close") > _c(d, i, "ma20")),
    ("변동성수축 후 하락", False,
     lambda d, i: _c(d, i - 1, "atr_pct") <= _c(d, i - 1, "atr_pct_q20")
                  and _c(d, i, "close") < _c(d, i, "ma20")),

    # 중기숏이 무작위보다 유의하게 나빴다 — 뒤집으면?
    ("중기숏 뒤집기(롱)", True,
     lambda d, i: abs(_c(d, i, "close") - _c(d, i, "recent_high")) <= _c(d, i, "close") * 0.005
                  and _c(d, i, "rsi") >= 65),
]


def prepare(df):
    """후보들이 쓰는 지표를 얹는다. 전부 과거만 본다."""
    d = df.copy()
    d["ma200"] = d["close"].rolling(200, min_periods=60).mean()
    d["hh20"] = d["high"].rolling(20).max()
    d["ll20"] = d["low"].rolling(20).min()
    d["atr_pct"] = d["atr"] / d["close"]
    # 지난 120봉 기준 하위 20% 문턱. shift(1) 로 당일을 뺀다.
    d["atr_pct_q20"] = d["atr_pct"].shift(1).rolling(120, min_periods=60).quantile(0.20)
    return d


# ── 통계 (diag_baseline 과 동일) ──────────────────────────────

def mean_r(ts):
    return sum(t["r"] for t in ts) / len(ts) if ts else 0.0


def se_r(ts):
    n = len(ts)
    if n < 2:
        return float("inf")
    m = mean_r(ts)
    sd = math.sqrt(sum((t["r"] - m) ** 2 for t in ts) / (n - 1))
    return sd / math.sqrt(n)


MIN_PER_PERIOD = 15    # 기간별 최소 표본. 6건짜리 신뢰구간은 신뢰가 아니다.


def excess(actual, baseline):
    """(초과, 하한, 상한). 표본이 모자라면 (0, -inf, inf).

    표본 하한을 여기서 걸지 않으면 6건짜리 한쪽 기간이 ✅ 를 받는다 —
    구간이 좁게 나오는 건 표본이 적어서 분산 추정이 못 미더운 것이지
    확실해서가 아니다.
    """
    if len(actual) < MIN_PER_PERIOD or len(baseline) < MIN_PER_PERIOD:
        return 0.0, float("-inf"), float("inf")
    d = mean_r(actual) - mean_r(baseline)
    se = math.sqrt(se_r(actual) ** 2 + se_r(baseline) ** 2)
    return d, d - 1.96 * se, d + 1.96 * se


def split(ts, mid):
    return ([t for t in ts if t["date"] < mid], [t for t in ts if t["date"] >= mid])


# ── 거래 만들기 ──────────────────────────────────────────────

def make_trade(d, i, long_side):
    """모든 후보가 같은 구조를 쓴다 — 진입 시점만 다르다."""
    row = d.iloc[i]
    atr = row.get("atr", float("nan"))
    if atr != atr or atr <= 0:
        return None
    price = float(row["close"])
    risk = float(bt.ATR_STOP_MULT) * float(atr)
    if risk <= 0:
        return None
    s = 1 if long_side else -1
    sig = {
        "idx": i,
        "type": "MID_LONG" if long_side else "MID_SHORT",
        "entry": price,
        "sl": price - s * risk,
        "tp1": price + s * risk,
        "tp2": price + s * risk * 2,
        "tp3": price + s * risk * 3,
        "date": row["timestamp"].strftime("%Y-%m-%d"),
    }
    return {**sig, **bt.evaluate_trade(sig, d)}


def random_trades(d, coin, long_side, n=SAMPLES_PER_COIN):
    import random
    lo, hi = 200, len(d) - 2
    if hi <= lo:
        return []
    rng = random.Random(zlib.crc32(f"{coin}/{'L' if long_side else 'S'}".encode()))
    out = []
    for i in sorted(rng.sample(range(lo, hi), min(n, hi - lo))):
        t = make_trade(d, i, long_side)
        if t:
            out.append(t)
    return out


def run(coins, days, only=None):
    hits = {name: [] for name, _, _ in CANDIDATES}
    base = {True: [], False: []}
    failed = []

    for sym in coins:
        coin = sym.replace("/USDT", "")
        try:
            df = bt.get_ohlcv_history(sym, "1d", days + 300)
            if df is None or len(df) < 260:
                failed.append(coin)
                continue
            d = prepare(bt.compute_indicators(df))
        except Exception as e:
            print(f"  [건너뜀] {coin}: {e}")
            failed.append(coin)
            continue

        for side in (True, False):
            base[side].extend(random_trades(d, coin, side))

        for name, long_side, cond in CANDIDATES:
            if only and name not in only:
                continue
            for i in range(200, len(d) - 1):
                try:
                    ok = bool(cond(d, i))
                except Exception:
                    ok = False
                if not ok:
                    continue
                t = make_trade(d, i, long_side)
                if t:
                    hits[name].append(t)
    return hits, base, failed


def main(argv):
    if "--list" in argv:
        print("후보 목록")
        for name, long_side, _ in CANDIDATES:
            print(f"  · {name}  ({'롱' if long_side else '숏'})")
        return 0
    if not _load():
        return 1

    rest = [a for a in argv[1:] if not a.startswith("-")]
    n = next((int(a) for a in rest if a.isdigit() and int(a) <= 50), 20)
    days = 730
    for a in rest:
        al = a.lower()
        if al.endswith("y") and al[:-1].isdigit():
            days = int(al[:-1]) * 365
        elif al.endswith("d") and al[:-1].isdigit():
            days = int(al[:-1])
    days = max(90, min(days, 730))

    coins = _pick(n)
    print("=" * 80)
    print("  진입 규칙 시험대 — 무작위 진입을 이기는가 (아무것도 고치지 않습니다)")
    print("=" * 80)
    print(f"  대상 폴더: {os.getcwd()}")
    print(f"  {len(coins)}종 × {days}일 · 후보 {len(CANDIDATES)}개")
    print("  손절 1.5×ATR · 목표 1R/2R/3R · 진입 시점만 다름")
    print("  3~6분 걸립니다...")
    t0 = time.time()

    hits, base, failed = run(coins, days)
    if failed:
        print(f"\n  ⚠️ 빠진 종목 {len(failed)}: {', '.join(failed)}")

    dates = sorted(t["date"] for ts in hits.values() for t in ts)
    if not dates:
        print("\n  후보 중 신호가 하나도 없습니다.")
        return 1
    mid = str(pd.Timestamp(dates[0]) + (pd.Timestamp(dates[-1]) - pd.Timestamp(dates[0])) / 2)[:10]
    print(f"\n  구간 {dates[0]} ~ {dates[-1]}   (반 나누는 지점 {mid})")
    print(f"  기준선  무작위 롱 {mean_r(base[True]):+.3f}R ({len(base[True])}건)"
          f" · 무작위 숏 {mean_r(base[False]):+.3f}R ({len(base[False])}건)")

    n_c = len(CANDIDATES)
    passed = []
    rows = sorted(CANDIDATES, key=lambda c: -mean_r(hits[c[0]]))
    for name, long_side, _ in rows:
        ts = hits[name]
        b = base[long_side]
        print(f"\n  [{name}]  {'롱' if long_side else '숏'}  {len(ts)}건 · {mean_r(ts):+.3f}R")
        if len(ts) < 30:
            print("    ⚠️ 30건 미만 — 판단 보류")
            continue
        marks = []
        for lbl, (a, bb) in (("전체", (ts, b)),
                             ("전반기", (split(ts, mid)[0], split(b, mid)[0])),
                             ("후반기", (split(ts, mid)[1], split(b, mid)[1]))):
            d, lo, hi = excess(a, bb)
            good = lo > 0 and len(a) >= MIN_PER_PERIOD
            if lbl != "전체":
                marks.append(good)
            print(f"    {lbl:<7} {mean_r(a):+.3f}R ({len(a):>4}건)  vs 무작위 {mean_r(bb):+.3f}R"
                  + (f"   초과 {d:+.3f}  [{lo:+.3f}, {hi:+.3f}] "
                     f"{'✅' if good else ('·' if d > 0 else '❌')}"
                     if len(a) >= MIN_PER_PERIOD
                     else f"   — {MIN_PER_PERIOD}건 미만이라 판정 보류"))
        if marks and all(marks):
            print("    ✅ 두 기간 모두 무작위보다 유의하게 낫다")
            passed.append(name)
        else:
            print("    ❌ 통과 못 함")

    print("\n" + "=" * 80)
    if passed:
        print(f"  통과: {', '.join(passed)}")
        print(f"""
  주의 — 후보를 {n_c}개 재서 고른 것입니다. 통과한 규칙은 대상을
  30종·3년으로 넓혀 한 번 더 확인한 뒤에 봇에 넣으십시오.""")
    else:
        print("""  통과한 후보가 없습니다.

  나쁜 결과가 아닙니다. 아이디어 하나를 버리는 데 5분이 든다는 뜻이고,
  지금까지는 한 바퀴에 며칠이 걸렸습니다. CANDIDATES 에 규칙을 더
  적어 다시 돌리십시오.""")
    print(f"\n  ({time.time()-t0:.0f}초 소요)")
    return 0


def _pick(n):
    for mod_name, var in (("spotlight", "SCAN_COINS"), ("config", "SCAN_COINS"),
                          ("config", "PAPER_COINS"), ("config", "COINS")):
        try:
            v = getattr(__import__(mod_name), var, None)
            if isinstance(v, (list, tuple)) and v:
                return [f"{c}/USDT" if "/" not in c else c for c in v[:n]]
        except Exception:
            pass
    return [f"{c}/USDT" for c in ("BTC", "ETH", "SOL", "XRP", "BNB")][:n]


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
