"""exit_bench.py — 같은 진입에 출구만 바꿔서 잰다 (읽기 전용)

    cd /path/to/auto
    python exit_bench.py           # 30종 × 1460일
    python exit_bench.py 30 4y

**아무 파일도 고치지 않습니다.** signal_bench.py 가 같은 폴더에
있어야 합니다 (진입 규칙을 거기서 가져옵니다).

앞서 한 말을 고칩니다
────────────────────
"진입에 우위가 없으면 어떤 출구도 우위를 못 만든다"고 했습니다.
앞쪽 절반은 맞습니다 — 무작위 진입의 기대값은 출구를 어떻게 바꾸든
-수수료 근처입니다.

**뒤집으면 틀립니다.** 우위가 있는 진입도 출구가 나쁘면 0으로 보입니다.
그리고 지금까지 쓴 출구가 바로 그 종류입니다:

    1R에서 절반 익절 → 손절을 본전으로 → 2R에서 전량 청산

  추세추종은 **드물게 오는 큰 한 방**으로 먹고삽니다. 이익의 분포가
  오른쪽으로 길죠. 그런데 위 규칙은 2R에서 잘라버립니다. 10R짜리
  움직임이 와도 2R만 받습니다. 게다가 1R에서 손절을 본전으로 올리니
  정상적인 되돌림에 남은 절반까지 털립니다.

  지금까지 제일 나았던 두 개(돌파롱·돌파숏)가 정확히 추세추종입니다.
  **우위가 없어서 0으로 나온 건지, 출구가 잘라먹어서 0으로 보이는 건지
  아직 구분한 적이 없습니다.**

무엇을 재나
──────────
진입은 signal_bench 의 후보를 그대로 쓰고, 출구만 넷으로 갈라 잽니다.

    A 현행        1R 절반 익절 → 본전 이동 → 2R 청산
    B 추적 2ATR   목표 없음. 고점에서 2ATR 밀리면 청산
    C 추적 3ATR   같은 규칙, 더 느슨하게
    D 고정 3R     본전 이동 없음, 3R 목표

  **무작위 기준선도 같은 출구로 다시 잽니다.** 안 그러면 출구를 바꾼
  효과와 진입 효과가 또 섞입니다. 비교는 언제나 같은 출구끼리입니다.

  보유 상한은 넷 다 120봉으로 맞췄습니다. 추세추종에 60봉은 짧고,
  상한이 다르면 그것 자체가 차이가 됩니다.

읽는 법
──────
  출구를 바꿨는데 **초과**가 그대로 0 근처면, 그건 출구 탓이 아니라
  진입에 정말 아무것도 없다는 뜻입니다. 그때는 접는 게 맞습니다.

  어떤 출구에서 초과가 두 기간 모두 유의해지면, 그 조합이 후보입니다.
"""

import math
import os
import sys
import time
import zlib

pd = None
bt = None
sb = None

MAX_DAYS = 1460
MAX_BARS = 120             # 넷 다 동일. 추세추종에 60봉은 짧다.
SAMPLES_PER_COIN = 60
MIN_PER_PERIOD = 15

EXITS = ("A 현행", "B 추적2ATR", "C 추적3ATR", "D 고정3R")


def _load():
    global pd, bt, sb
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
        return False
    try:
        import signal_bench
    except ImportError as e:
        print(f"signal_bench.py 를 못 불렀습니다: {e}")
        print("같은 폴더에 signal_bench.py 가 있어야 합니다.")
        return False
    pd, bt, sb = pandas, backtest, signal_bench
    return True


# ── 출구 규칙 ────────────────────────────────────────────────

def simulate(d, i, long_side, exit_name):
    """진입 i, 출구 규칙 하나. 반환 R (수수료·슬리피지 반영) 또는 None.

    진입가·수수료 계산은 backtest.evaluate_trade 와 같은 규칙을 쓴다.
    """
    row = d.iloc[i]
    atr = row.get("atr", float("nan"))
    if atr != atr or atr <= 0:
        return None
    fut = d.iloc[i + 1: i + 1 + MAX_BARS]
    if len(fut) < 2:
        return None

    s = 1 if long_side else -1
    risk = float(bt.ATR_STOP_MULT) * float(atr)
    entry = float(fut["open"].iloc[0]) * (1 + bt.SLIPPAGE * s)
    stop = entry - s * risk
    unit = abs(entry - stop)
    if unit <= 0:
        return None

    tp1, tp2, tp3 = (entry + s * unit * k for k in (1, 2, 3))
    trail = {"B 추적2ATR": 2.0, "C 추적3ATR": 3.0}.get(exit_name)

    highs, lows = fut["high"].to_numpy(), fut["low"].to_numpy()
    closes = fut["close"].to_numpy()
    realized, tp1_hit = 0.0, False
    best = entry

    for k in range(len(fut)):
        h, l = float(highs[k]), float(lows[k])
        hit = l if long_side else h          # 우리에게 불리한 쪽
        fav = h if long_side else l          # 유리한 쪽

        # 같은 봉에서 손절·목표가 함께 닿으면 손절을 먼저 본다 (보수적)
        if (long_side and hit <= stop) or (not long_side and hit >= stop):
            if tp1_hit:
                exits = [(tp1, 0.5), (stop, 0.5)]
                gross = realized + 0.5 * s * (stop - entry) / unit
            else:
                exits = [(stop, 1.0)]
                gross = s * (stop - entry) / unit
            return round(gross - bt._cost_r(entry, exits, unit), 3)

        if trail is None:
            if exit_name == "A 현행":
                if not tp1_hit and s * (fav - tp1) >= 0:
                    realized, tp1_hit, stop = 0.5 * s * (tp1 - entry) / unit, True, entry
                if tp1_hit and s * (fav - tp2) >= 0:
                    exits = [(tp1, 0.5), (tp2, 0.5)]
                    gross = realized + 0.5 * s * (tp2 - entry) / unit
                    return round(gross - bt._cost_r(entry, exits, unit), 3)
            else:                                     # D 고정3R — 본전 이동 없음
                if s * (fav - tp3) >= 0:
                    gross = s * (tp3 - entry) / unit
                    return round(gross - bt._cost_r(entry, [(tp3, 1.0)], unit), 3)
        else:
            # 유리한 쪽 최고점을 갱신하고, 거기서 trail×ATR 밀린 자리로 손절을 끌어올린다.
            best = max(best, fav) if long_side else min(best, fav)
            new_stop = best - s * trail * float(atr)
            stop = max(stop, new_stop) if long_side else min(stop, new_stop)

            # 올린 손절을 **같은 봉 안에서** 다시 검사한다.
            #
            # 봉 안에서 고점과 저점 중 뭐가 먼저였는지는 알 수 없다. 이걸
            # 안 하면 '고점을 찍어 손절을 올려두고, 그 봉에서 되밀린 건
            # 다음 봉에서야 검사'하게 된다 — 추적손절에 한 봉짜리 미래를
            # 공짜로 주는 셈이라 성적이 부풀려진다.
            # 보수적으로: 같은 봉에서 둘 다 닿았으면 손절된 것으로 본다.
            if (long_side and hit <= stop) or (not long_side and hit >= stop):
                if tp1_hit:
                    exits = [(tp1, 0.5), (stop, 0.5)]
                    gross = realized + 0.5 * s * (stop - entry) / unit
                else:
                    exits = [(stop, 1.0)]
                    gross = s * (stop - entry) / unit
                return round(gross - bt._cost_r(entry, exits, unit), 3)

    last = float(closes[-1])
    if tp1_hit:
        exits = [(tp1, 0.5), (last, 0.5)]
        gross = realized + 0.5 * s * (last - entry) / unit
    else:
        exits = [(last, 1.0)]
        gross = s * (last - entry) / unit
    return round(gross - bt._cost_r(entry, exits, unit), 3)


def hold_bars(d, i, long_side, exit_name):
    """중복 방지용. 실제 청산 봉을 정확히 세지 않고 보수적으로 잡는다."""
    return MAX_BARS // 2


# ── 통계 ─────────────────────────────────────────────────────

def mean_r(ts):
    return sum(t["r"] for t in ts) / len(ts) if ts else 0.0


def se_r(ts):
    n = len(ts)
    if n < 2:
        return float("inf")
    m = mean_r(ts)
    return math.sqrt(sum((t["r"] - m) ** 2 for t in ts) / (n - 1)) / math.sqrt(n)


def excess(a, b):
    if len(a) < MIN_PER_PERIOD or len(b) < MIN_PER_PERIOD:
        return 0.0, float("-inf"), float("inf")
    d = mean_r(a) - mean_r(b)
    se = math.sqrt(se_r(a) ** 2 + se_r(b) ** 2)
    return d, d - 1.96 * se, d + 1.96 * se


def split(ts, mid):
    return ([t for t in ts if t["date"] < mid], [t for t in ts if t["date"] >= mid])


# ── 실행 ─────────────────────────────────────────────────────

def run(coins, days):
    hits = {(n, e): [] for n, _, _ in sb.CANDIDATES for e in EXITS}
    base = {(sd, e): [] for sd in (True, False) for e in EXITS}
    failed = []

    for sym in coins:
        coin = sym.replace("/USDT", "")
        try:
            df = bt.get_ohlcv_history(sym, "1d", days + 300)
            if df is None or len(df) < 260:
                failed.append(coin)
                continue
            d = sb.prepare(bt.compute_indicators(df))
        except Exception as e:
            print(f"  [건너뜀] {coin}: {e}")
            failed.append(coin)
            continue

        import random
        for side in (True, False):
            rng = random.Random(zlib.crc32(f"{coin}/{'L' if side else 'S'}".encode()))
            lo, hi = 200, len(d) - 2
            if hi <= lo:
                continue
            free = -1
            for i in sorted(rng.sample(range(lo, hi), min(SAMPLES_PER_COIN, hi - lo))):
                if i <= free:
                    continue
                date = d["timestamp"].iloc[i].strftime("%Y-%m-%d")
                got = False
                for e in EXITS:
                    r = simulate(d, i, side, e)
                    if r is not None:
                        base[(side, e)].append({"r": r, "date": date, "_coin": coin})
                        got = True
                if got:
                    free = i + hold_bars(d, i, side, EXITS[0])

        for name, long_side, cond in sb.CANDIDATES:
            free = -1
            for i in range(200, len(d) - 1):
                if i <= free:
                    continue
                try:
                    ok = bool(cond(d, i))
                except Exception:
                    ok = False
                if not ok:
                    continue
                date = d["timestamp"].iloc[i].strftime("%Y-%m-%d")
                got = False
                for e in EXITS:
                    r = simulate(d, i, long_side, e)
                    if r is not None:
                        hits[(name, e)].append({"r": r, "date": date, "_coin": coin})
                        got = True
                if got:
                    free = i + hold_bars(d, i, long_side, EXITS[0])
    return hits, base, failed


def main(argv):
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

    coins = sb._pick(n)
    print("=" * 84)
    print("  출구 시험대 — 같은 진입에 출구만 바꾼다 (아무것도 고치지 않습니다)")
    print("=" * 84)
    print(f"  대상 폴더: {os.getcwd()}")
    print(f"  {len(coins)}종 × {days}일 · 진입 {len(sb.CANDIDATES)}개 × 출구 {len(EXITS)}개")
    print(f"  보유 상한 {MAX_BARS}봉 (넷 다 동일) · 손절 1.5×ATR")
    print("  4~8분 걸립니다...")
    t0 = time.time()

    hits, base, failed = run(coins, days)
    if failed:
        print(f"\n  ⚠️ 빠진 종목 {len(failed)}: {', '.join(failed)}")

    dates = sorted(t["date"] for ts in hits.values() for t in ts)
    if not dates:
        print("\n  거래가 하나도 없습니다.")
        return 1
    mid = str(pd.Timestamp(dates[0]) + (pd.Timestamp(dates[-1]) - pd.Timestamp(dates[0])) / 2)[:10]
    print(f"\n  구간 {dates[0]} ~ {dates[-1]}   (반 나누는 지점 {mid})")
    print("\n  무작위 진입의 출구별 기대값 — 출구 자체가 얼마나 바꾸는지")
    for e in EXITS:
        print(f"    {e:<12} 롱 {mean_r(base[(True, e)]):+.3f}R"
              f"   숏 {mean_r(base[(False, e)]):+.3f}R")

    passed = []
    for name, long_side, _ in sb.CANDIDATES:
        rows = []
        for e in EXITS:
            ts, b = hits[(name, e)], base[(long_side, e)]
            if len(ts) < 30:
                rows.append((e, len(ts), mean_r(ts), None, None, False))
                continue
            marks, ds = [], []
            for a, bb in ((split(ts, mid)[0], split(b, mid)[0]),
                          (split(ts, mid)[1], split(b, mid)[1])):
                dd, lo, hi = excess(a, bb)
                marks.append(lo > 0 and len(a) >= MIN_PER_PERIOD)
                ds.append(dd)
            dt, lot, _ = excess(ts, b)
            ok = all(marks)
            rows.append((e, len(ts), mean_r(ts), dt, ds, ok))
            if ok:
                passed.append(f"{name} / {e}")

        print(f"\n  [{name}]  {'롱' if long_side else '숏'}")
        for e, cnt, m, dt, ds, ok in rows:
            if dt is None:
                print(f"    {e:<12} {cnt:>4}건  — 30건 미만, 판단 보류")
                continue
            print(f"    {e:<12} {cnt:>4}건 · {m:+.3f}R   초과 전체 {dt:+.3f}"
                  f"   전반 {ds[0]:+.3f} / 후반 {ds[1]:+.3f}   {'✅' if ok else ''}")

    print("\n" + "=" * 84)
    if passed:
        print("  두 기간 모두 유의한 조합:")
        for p in passed:
            print(f"    · {p}")
        print("""
  진입 × 출구를 함께 뒤졌으므로 조합 수만큼 우연히 좋아 보일 확률이
  올라갑니다. diag_regime 으로 보정한 뒤, 봇에는 알림 전용으로 넣고
  페이퍼 4주·30건을 거쳐야 합니다.""")
    else:
        print("""  출구를 넷으로 바꿔도 초과가 0 근처입니다.

  출구가 잘라먹어서 0으로 보인 게 아니라, 진입에 정말 아무것도
  없다는 뜻입니다. 일봉 가격으로 진입 시점을 고르는 길은 여기서
  접는 것이 맞습니다.""")
    print(f"\n  ({time.time()-t0:.0f}초 소요)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
