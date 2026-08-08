"""xs_bench.py — 코인끼리 비교하는 신호를 잰다 (읽기 전용)

    cd /path/to/auto
    python xs_bench.py              # 30종 × 1460일
    python xs_bench.py 30 4y
    python xs_bench.py --list

**아무 파일도 고치지 않습니다.**

왜 종류가 다른가
───────────────
signal_bench 에서 잰 13개는 전부 **한 코인 안에서 그 코인의 가격만**
봤다. "BTC가 20일 신고가냐", "RSI가 35 아래냐" 같은 절대 조건이다.
그래서 시장 전체가 오르면 롱 신호가 다 같이 좋아 보이고, 내리면 다
같이 나빠진다 — 국면에 통째로 휘둘린다. 실제로 그렇게 나왔다.

여기서는 **순위**를 본다. "지난 30일 30종 중 제일 많이 오른 3종이냐".

  시장 전체가 20% 오르든 20% 내리든 순위는 남는다. 국면이 공통으로
  더하는 몫이 순위에서는 상쇄된다. 정보의 종류가 다르다.

  주식·선물에서 횡단면 모멘텀은 오래 연구된 축이다. 그게 코인에서도
  되는지는 여기서 재 봐야 안다.

무엇이 같고 무엇이 다른가
───────────────────────
signal_bench 와 **완전히 같게** 맞췄다. 그래야 비교가 된다.

    손절 1.5×ATR14 · 목표 1R/2R/3R · 같은 수수료·슬리피지
    같은 코인·기간의 무작위 진입과 비교
    전반기·후반기 따로, 두 기간 모두 초과 CI 하한이 0을 넘어야 통과
    한 코인 동시 포지션 1개

다른 건 **누구를 고르느냐**뿐이다. 매주 한 번 전 종목을 줄 세워
위/아래 K개만 잡는다.

미래를 보지 않게
──────────────
  순위는 그날 종가까지의 값으로 매기고, 진입은 다음 봉 시가다
  (evaluate_trade 가 그렇게 한다). 그날 순위를 매길 때 그날 이후
  가격은 쓰지 않는다.

  순위 대상은 **그날 데이터가 있는 코인**뿐이다. 나중에 상장된 코인을
  과거 순위에 끼워 넣지 않는다.
"""

import math
import os
import sys
import time
import zlib

pd = None
bt = None

MAX_DAYS = 1460
SAMPLES_PER_COIN = 60      # 무작위 기준선, 방향당
REBALANCE_DAYS = 7         # 며칠마다 줄을 다시 세우나
TOP_K = 3                  # 위/아래 몇 개를 잡나
MIN_UNIVERSE = 10          # 이보다 적으면 순위에 뜻이 없다
MIN_PER_PERIOD = 15


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


def pick_coins(n):
    for mod_name, var in (("spotlight", "SCAN_COINS"), ("config", "SCAN_COINS"),
                          ("config", "PAPER_COINS"), ("config", "COINS")):
        try:
            v = getattr(__import__(mod_name), var, None)
            if isinstance(v, (list, tuple)) and v:
                return [f"{c}/USDT" if "/" not in c else c for c in v[:n]]
        except Exception:
            pass
    return [f"{c}/USDT" for c in ("BTC", "ETH", "SOL", "XRP", "BNB")][:n]


# ── 후보 ─────────────────────────────────────────────────────
#
# (이름, 줄 세우는 값, "top"|"bottom", 롱?)

CANDIDATES = [
    ("30일 모멘텀 상위 롱", "mom30", "top", True),
    ("30일 모멘텀 하위 숏", "mom30", "bottom", False),
    ("90일 모멘텀 상위 롱", "mom90", "top", True),
    ("90일 모멘텀 하위 숏", "mom90", "bottom", False),
    ("변동성조정 30일 상위 롱", "mom30v", "top", True),
    # 횡단면 역추세 — 제일 많이 빠진 걸 사면?
    ("30일 모멘텀 하위 롱", "mom30", "bottom", True),
]


def prepare(d):
    """줄 세우기에 쓸 값. 전부 그날까지의 종가만 본다."""
    d = d.copy()
    d["mom30"] = d["close"].pct_change(30)
    d["mom90"] = d["close"].pct_change(90)
    # 같은 상승률이라도 조용히 오른 쪽을 높게 본다
    d["mom30v"] = d["mom30"] / (d["atr"] / d["close"])
    return d


# ── 통계 (signal_bench 와 동일) ───────────────────────────────

def mean_r(ts):
    return sum(t["r"] for t in ts) / len(ts) if ts else 0.0


def se_r(ts):
    n = len(ts)
    if n < 2:
        return float("inf")
    m = mean_r(ts)
    sd = math.sqrt(sum((t["r"] - m) ** 2 for t in ts) / (n - 1))
    return sd / math.sqrt(n)


def excess(actual, baseline):
    if len(actual) < MIN_PER_PERIOD or len(baseline) < MIN_PER_PERIOD:
        return 0.0, float("-inf"), float("inf")
    d = mean_r(actual) - mean_r(baseline)
    se = math.sqrt(se_r(actual) ** 2 + se_r(baseline) ** 2)
    return d, d - 1.96 * se, d + 1.96 * se


def split(ts, mid):
    return ([t for t in ts if t["date"] < mid], [t for t in ts if t["date"] >= mid])


# ── 거래 (signal_bench 와 동일한 구조) ────────────────────────

_LONG_TYPES = ("MID_LONG", "FIB_LONG", "SMA_LONG", "ANGEL")


def make_trade(d, i, long_side, coin):
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
        "_coin": coin,
    }
    return {**sig, **bt.evaluate_trade(sig, d)}


def hold_bars(d, sig, max_bars=60):
    """evaluate_trade 와 같은 규칙. 중복 방지 게이트에만 쓴다."""
    i = sig["idx"]
    long_side = sig["type"] in _LONG_TYPES
    fut = d.iloc[i + 1: i + 1 + max_bars]
    if len(fut) == 0:
        return 1
    entry = float(fut["open"].iloc[0])
    stop, tp1, tp2 = float(sig["sl"]), float(sig["tp1"]), float(sig["tp2"])
    highs, lows = fut["high"].to_numpy(), fut["low"].to_numpy()
    tp1_hit = False
    for k in range(len(fut)):
        h, l = highs[k], lows[k]
        if long_side:
            if l <= stop:
                return k + 1
            if not tp1_hit and h >= tp1:
                tp1_hit, stop = True, entry
            if tp1_hit and h >= tp2:
                return k + 1
        else:
            if h >= stop:
                return k + 1
            if not tp1_hit and l <= tp1:
                tp1_hit, stop = True, entry
            if tp1_hit and l <= tp2:
                return k + 1
    return len(fut)


def random_trades(d, coin, long_side, n=SAMPLES_PER_COIN):
    import random
    lo, hi = 120, len(d) - 2
    if hi <= lo:
        return []
    rng = random.Random(zlib.crc32(f"{coin}/{'L' if long_side else 'S'}".encode()))
    out, free_at = [], -1
    for i in sorted(rng.sample(range(lo, hi), min(n, hi - lo))):
        if i <= free_at:
            continue
        t = make_trade(d, i, long_side, coin)
        if t:
            out.append(t)
            free_at = i + hold_bars(d, t)
    return out


# ── 실행 ─────────────────────────────────────────────────────

def run(coins, days):
    frames, failed = {}, []
    for sym in coins:
        coin = sym.replace("/USDT", "")
        try:
            df = bt.get_ohlcv_history(sym, "1d", days + 200)
            if df is None or len(df) < 180:
                failed.append(coin)
                continue
            frames[coin] = prepare(bt.compute_indicators(df))
        except Exception as e:
            print(f"  [건너뜀] {coin}: {e}")
            failed.append(coin)
    if len(frames) < MIN_UNIVERSE:
        return {}, {True: [], False: []}, failed, []

    base = {True: [], False: []}
    for coin, d in frames.items():
        for side in (True, False):
            base[side].extend(random_trades(d, coin, side))

    # 날짜 → {코인: 봉 인덱스}
    where = {}
    for coin, d in frames.items():
        for i, ts in enumerate(d["timestamp"]):
            if i < 120:                 # 워밍업(mom90 + 지표)
                continue
            where.setdefault(ts.strftime("%Y-%m-%d"), {})[coin] = i

    dates = sorted(where)
    rebal = dates[::REBALANCE_DAYS]

    hits = {name: [] for name, *_ in CANDIDATES}
    free = {name: {} for name, *_ in CANDIDATES}
    sizes = []

    for day in rebal:
        members = where[day]
        for name, key, side_pick, long_side in CANDIDATES:
            scored = []
            for coin, i in members.items():
                v = frames[coin][key].iloc[i]
                if v == v:              # NaN 제외
                    scored.append((float(v), coin, i))
            if len(scored) < MIN_UNIVERSE:
                continue
            if name == CANDIDATES[0][0]:
                sizes.append(len(scored))
            scored.sort(reverse=(side_pick == "top"))
            for _, coin, i in scored[:TOP_K]:
                if i <= free[name].get(coin, -1):
                    continue
                t = make_trade(frames[coin], i, long_side, coin)
                if t:
                    hits[name].append(t)
                    free[name][coin] = i + hold_bars(frames[coin], t)
    return hits, base, failed, sizes


def main(argv):
    if "--list" in argv:
        print(f"후보 목록 (매 {REBALANCE_DAYS}일 재선정 · 위/아래 {TOP_K}종)")
        for name, key, pick, long_side in CANDIDATES:
            print(f"  · {name}  [{key} {pick}, {'롱' if long_side else '숏'}]")
        return 0
    if not _load():
        return 1

    rest = [a for a in argv[1:] if not a.startswith("-")]
    n = next((int(a) for a in rest if a.isdigit() and int(a) <= 50), 30)
    days = MAX_DAYS
    for a in rest:
        al = a.lower()
        if al.endswith("y") and al[:-1].isdigit():
            days = int(al[:-1]) * 365
        elif al.endswith("d") and al[:-1].isdigit():
            days = int(al[:-1])
    days = max(180, min(days, MAX_DAYS))

    coins = pick_coins(n)
    print("=" * 80)
    print("  횡단면 시험대 — 코인끼리 줄 세워 고른다 (아무것도 고치지 않습니다)")
    print("=" * 80)
    print(f"  대상 폴더: {os.getcwd()}")
    print(f"  {len(coins)}종 × {days}일 · 후보 {len(CANDIDATES)}개")
    print(f"  {REBALANCE_DAYS}일마다 재선정 · 위/아래 {TOP_K}종 · 손절 1.5×ATR · 목표 1R/2R/3R")
    print("  3~6분 걸립니다...")
    t0 = time.time()

    hits, base, failed, sizes = run(coins, days)
    if not hits:
        print(f"\n  줄 세울 코인이 {MIN_UNIVERSE}종도 안 됩니다. 기간을 줄여 보십시오.")
        return 1
    if failed:
        print(f"\n  ⚠️ 빠진 종목 {len(failed)}: {', '.join(failed)}")
    if sizes:
        print(f"  줄 세운 종목 수: 평균 {sum(sizes)/len(sizes):.0f}종 "
              f"(최소 {min(sizes)} · 최대 {max(sizes)})")

    dates = sorted(t["date"] for ts in hits.values() for t in ts)
    if not dates:
        print("\n  거래가 하나도 없습니다.")
        return 1
    mid = str(pd.Timestamp(dates[0]) + (pd.Timestamp(dates[-1]) - pd.Timestamp(dates[0])) / 2)[:10]
    print(f"\n  구간 {dates[0]} ~ {dates[-1]}   (반 나누는 지점 {mid})")
    print(f"  기준선  무작위 롱 {mean_r(base[True]):+.3f}R ({len(base[True])}건)"
          f" · 무작위 숏 {mean_r(base[False]):+.3f}R ({len(base[False])}건)")

    passed = []
    for name, key, pick, long_side in sorted(CANDIDATES, key=lambda c: -mean_r(hits[c[0]])):
        ts = hits[name]
        b = base[long_side]
        print(f"\n  [{name}]  {len(ts)}건 · {mean_r(ts):+.3f}R")
        if len(ts) < 30:
            print("    ⚠️ 30건 미만 — 판단 보류")
            continue
        marks = []
        for lbl, a, bb in (("전체", ts, b),
                           ("전반기", split(ts, mid)[0], split(b, mid)[0]),
                           ("후반기", split(ts, mid)[1], split(b, mid)[1])):
            d, lo, hi = excess(a, bb)
            good = lo > 0 and len(a) >= MIN_PER_PERIOD
            if lbl != "전체":
                marks.append(good)
            tail = (f"   초과 {d:+.3f}  [{lo:+.3f}, {hi:+.3f}] "
                    f"{'✅' if good else ('·' if d > 0 else '❌')}"
                    if len(a) >= MIN_PER_PERIOD
                    else f"   — {MIN_PER_PERIOD}건 미만이라 판정 보류")
            print(f"    {lbl:<7} {mean_r(a):+.3f}R ({len(a):>4}건)  "
                  f"vs 무작위 {mean_r(bb):+.3f}R" + tail)
        if marks and all(marks):
            print("    ✅ 두 기간 모두 무작위보다 유의하게 낫다")
            passed.append(name)
        else:
            print("    ❌ 통과 못 함")

    print("\n" + "=" * 80)
    if passed:
        print(f"  통과: {', '.join(passed)}")
        print(f"""
  후보 {len(CANDIDATES)}개 중 고른 것입니다. 다음 순서를 지키십시오.
    1. diag_regime 으로 선택 편향 보정 후에도 남는지 확인
    2. 봇에 **알림 전용**으로 넣고 페이퍼 4주 · 30건
    3. 백테스트 기대값과 페이퍼 실측이 비슷하면 그때 실매매

  2번을 건너뛰면 안 됩니다. 백테스트는 종가 시장가로 재는데 봇은
  지정가로 들어갑니다. 그 차이는 페이퍼로만 알 수 있습니다.""")
    else:
        print("""  통과한 후보가 없습니다.

  순위 기반도 안 되면, 일봉 가격만으로 진입 시점을 고르는 길은
  여기서 접는 게 맞습니다. 봇을 사람이 보는 스캐너·알림 도구로
  정리하는 쪽이 남은 시간을 훨씬 잘 쓰는 길입니다.""")
    print(f"\n  ({time.time()-t0:.0f}초 소요)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
