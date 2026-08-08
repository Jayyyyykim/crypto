"""funding_bench.py — 펀딩비는 과거를 받을 수 있다. 지금 잰다.

    cd /path/to/auto
    python funding_bench.py --fetch      # 펀딩 과거를 받아 캐시에 저장 (처음 한 번)
    python funding_bench.py              # 캐시로 시험대 실행
    python funding_bench.py --coverage   # 캐시에 몇 년치가 있나

앞서 한 말을 고칩니다
────────────────────
"비가격 정보는 거래소가 30일치만 줘서 백테스트가 불가능하다"고 했습니다.
**펀딩비는 아닙니다.**

    30일만 오는 것   미결제약정 이력, 롱숏 계정비, 테이커 매수/매도
                     (Binance /futures/data/* 계열)
    과거가 다 오는 것 **펀딩비** — 상장 시점까지 페이지를 넘겨 받을 수 있다
                     (ccxt fetch_funding_rate_history)

  펀딩은 8시간마다 한 번씩 몇 년치가 남아 있습니다. 그러니 이건
  3개월을 기다릴 필요가 없습니다. 오늘 재면 됩니다.

왜 펀딩이 다른 축인가
────────────────────
펀딩은 가격이 아니라 **포지션 쏠림의 값**입니다. 롱이 몰리면 롱이
숏에게 돈을 냅니다(양수). 즉 "지금 사람들이 어느 쪽에 얼마나 몰려
있는지"를 돈으로 표시한 숫자입니다.

지금까지 잰 45가지는 전부 가격에서 나온 값이었습니다. 이건 아닙니다.

무엇을 재나
──────────
signal_bench 와 **완전히 같은 틀**입니다. 손절 1.5×ATR, 목표 1R/2R/3R,
같은 수수료, 무작위 진입과 비교, 전후반 분리, 한 코인 동시 1개.
다른 건 진입 조건뿐입니다.

문턱은 고정값이 아니라 **그 코인의 지난 90일 분위수**로 잡습니다.
코인마다 펀딩 수준이 다르고 시기마다 다릅니다. "0.01% 넘으면"
같은 상수를 쓰면 그 상수를 고른 것 자체가 과최적화입니다.

미래 차단
────────
그날 종가까지 확정된 펀딩만 씁니다. 분위수 창도 그날까지입니다.
진입은 언제나 다음 봉 시가입니다.

캐시
────
    funding_hist.jsonl   (코인, 시각, 요율) — 한 번 받으면 다시 안 받습니다.
                         --fetch 를 다시 돌리면 없는 구간만 이어 받습니다.
"""

import json
import math
import os
import sys
import time
import zlib

pd = None
bt = None
sb = None

CACHE = "funding_hist.jsonl"
MAX_DAYS = 1460
SAMPLES_PER_COIN = 60
MIN_PER_PERIOD = 15
PCT_WINDOW = 90            # 분위수 창 (일)
HI_Q, LO_Q = 0.90, 0.10    # 상·하위 10%


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
        return False
    # signal_bench 도 제 몫의 backtest 를 잡아야 한다. 안 부르면
    # signal_bench.bt 가 None 인 채로 make_trade 가 터진다.
    if not signal_bench._load():
        return False
    pd, bt, sb = pandas, backtest, signal_bench
    return True


# ── 캐시 ─────────────────────────────────────────────────────

def load_cache(path=CACHE):
    """{코인: {날짜: 그날 펀딩 평균}}"""
    out = {}
    if not os.path.exists(path):
        return out
    raw = {}
    with open(path, encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            raw.setdefault(r["coin"], {}).setdefault(r["date"], []).append(r["rate"])
    for coin, days in raw.items():
        # 하루에 3번(8시간마다) 들어온다. 그날 값들의 평균을 그날의 값으로 본다.
        out[coin] = {d: sum(v) / len(v) for d, v in days.items()}
    return out


def cache_bounds(path=CACHE):
    """{코인: (첫날, 마지막날, 줄 수)}"""
    b = {}
    if not os.path.exists(path):
        return b
    with open(path, encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            c, d = r["coin"], r["date"]
            lo, hi, n = b.get(c, (d, d, 0))
            b[c] = (min(lo, d), max(hi, d), n + 1)
    return b


def fetch_one(coin, since_ms, seen_ts):
    """ccxt 로 펀딩 이력을 페이지 넘겨 받는다. 이미 있는 시각은 건너뛴다."""
    symbol = f"{coin}/USDT:USDT"          # 무기한 선물 표기
    rows, cur, guard = [], since_ms, 0
    while guard < 200:
        guard += 1
        try:
            batch = bt.exchange.fetch_funding_rate_history(symbol, since=cur, limit=200)
        except Exception as e:
            if guard == 1:
                # 표기가 다를 수 있다. 한 번 더 다르게 시도한다.
                try:
                    batch = bt.exchange.fetch_funding_rate_history(
                        f"{coin}/USDT", since=cur, limit=200)
                except Exception as e2:
                    print(f"    {coin}: {type(e2).__name__}: {e2}")
                    return []
            else:
                print(f"    {coin}: {type(e).__name__}: {e}")
                break
        if not batch:
            break
        newest = 0
        for it in batch:
            ts = it.get("timestamp")
            rate = it.get("fundingRate")
            if ts is None or rate is None:
                continue
            newest = max(newest, ts)
            if ts in seen_ts:
                continue
            rows.append({
                "coin": coin,
                "ts": int(ts),
                "date": pd.Timestamp(ts, unit="ms", tz="UTC").strftime("%Y-%m-%d"),
                "rate": float(rate),
            })
        if newest <= cur:
            break
        cur = newest + 1
        if len(batch) < 100:
            time.sleep(0.2)
            continue
        time.sleep(0.2)
    return rows


def cmd_fetch(coins, days):
    have = {}
    if os.path.exists(CACHE):
        with open(CACHE, encoding="utf-8") as fp:
            for line in fp:
                try:
                    r = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                have.setdefault(r["coin"], set()).add(int(r["ts"]))

    since = int((pd.Timestamp.utcnow() - pd.Timedelta(days=days + 60)).timestamp() * 1000)
    total = 0
    print(f"  {len(coins)}종 · {days + 60}일 이전부터 이어 받습니다\n")
    with open(CACHE, "a", encoding="utf-8", newline="") as fp:
        for c in coins:
            rows = fetch_one(c, since, have.get(c, set()))
            for r in rows:
                fp.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
            fp.flush()
            total += len(rows)
            print(f"    {c:<8} +{len(rows)}건")
    print(f"\n  {total}건 저장 → {CACHE}")
    return 0


def cmd_coverage():
    b = cache_bounds()
    if not b:
        print(f"  {CACHE} 이 아직 없습니다. python funding_bench.py --fetch")
        return 0
    print("=" * 62)
    print("  펀딩 이력 캐시")
    print("=" * 62)
    short = []
    for c in sorted(b):
        lo, hi, n = b[c]
        span = (pd.Timestamp(hi) - pd.Timestamp(lo)).days
        flag = ""
        if span < 700:
            flag = "  ⚠️ 2년 미만"
            short.append(c)
        print(f"  {c:<8} {lo} ~ {hi}   {span:>5}일 · {n:>6}건{flag}")
    print("=" * 62)
    if short:
        print(f"  2년이 안 되는 종목 {len(short)}: {', '.join(short)}")
        print("  최근 상장이거나 거래소가 그만큼만 주는 것입니다.")
    return 0


# ── 진입 규칙 ────────────────────────────────────────────────
#
# (이름, 롱?, 조건)  — d 는 펀딩 칼럼이 붙은 DataFrame

CANDIDATES = [
    # 롱이 몰려 돈을 내고 있다 → 되돌림을 노려 숏
    ("펀딩 극단 양수 숏", False,
     lambda d, i: d["fund"].iloc[i] >= d["fund_hi"].iloc[i]),
    # 숏이 몰려 돈을 내고 있다 → 롱
    ("펀딩 극단 음수 롱", True,
     lambda d, i: d["fund"].iloc[i] <= d["fund_lo"].iloc[i]),
    # 반대쪽 — 쏠림을 추종하면?
    ("펀딩 극단 양수 롱", True,
     lambda d, i: d["fund"].iloc[i] >= d["fund_hi"].iloc[i]),
    ("펀딩 극단 음수 숏", False,
     lambda d, i: d["fund"].iloc[i] <= d["fund_lo"].iloc[i]),
    # 쏠림이 풀리는 순간 — 극단에 있다가 빠져나온 첫 봉
    ("펀딩 과열 해소 숏", False,
     lambda d, i: d["fund"].iloc[i - 1] >= d["fund_hi"].iloc[i - 1]
                  and d["fund"].iloc[i] < d["fund_hi"].iloc[i]),
    # 펀딩은 양수인데 가격은 20일 신저 — 롱이 물려 있다
    ("롱 물림 (펀딩+ · 가격 신저)", False,
     lambda d, i: d["fund"].iloc[i] > 0
                  and d["close"].iloc[i] <= d["ll20"].iloc[i - 1]),
]


def prepare(d, fmap):
    """펀딩과 그 코인의 분위수 문턱을 붙인다. 전부 그날까지만 본다."""
    d = d.copy()
    d["fund"] = [fmap.get(ts.strftime("%Y-%m-%d")) for ts in d["timestamp"]]
    d["fund"] = d["fund"].ffill(limit=2)          # 하루 빠진 건 직전 값으로
    # 문턱은 코인마다·시기마다 다르다. 상수를 고르면 그 상수가 과최적화다.
    d["fund_hi"] = d["fund"].rolling(PCT_WINDOW, min_periods=30).quantile(HI_Q)
    d["fund_lo"] = d["fund"].rolling(PCT_WINDOW, min_periods=30).quantile(LO_Q)
    d["ll20"] = d["low"].rolling(20).min()
    return d


# ── 통계 (signal_bench 와 동일) ───────────────────────────────

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


def run(coins, days, cache):
    hits = {n: [] for n, _, _ in CANDIDATES}
    base = {True: [], False: []}
    failed, nofund = [], []

    for sym in coins:
        coin = sym.replace("/USDT", "")
        fmap = cache.get(coin)
        if not fmap:
            nofund.append(coin)
            continue
        try:
            df = bt.get_ohlcv_history(sym, "1d", days + 200)
            if df is None or len(df) < 200:
                failed.append(coin)
                continue
            d = prepare(bt.compute_indicators(df), fmap)
        except Exception as e:
            print(f"  [건너뜀] {coin}: {e}")
            failed.append(coin)
            continue

        for side in (True, False):
            base[side].extend(sb.random_trades(d, coin, side, n=SAMPLES_PER_COIN))

        for name, long_side, cond in CANDIDATES:
            free = -1
            for i in range(150, len(d) - 1):
                if i <= free:
                    continue
                try:
                    ok = bool(cond(d, i))
                except Exception:
                    ok = False
                if not ok:
                    continue
                t = sb.make_trade(d, i, long_side)
                if t:
                    t["_coin"] = coin
                    hits[name].append(t)
                    free = i + sb.hold_bars(d, t)
    return hits, base, failed, nofund


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

    if "--coverage" in argv:
        return cmd_coverage()
    if "--fetch" in argv:
        print("=" * 62)
        print("  펀딩 이력 받기 (한 번만 받으면 다음부터는 이어받습니다)")
        print("=" * 62)
        return cmd_fetch([c.replace("/USDT", "") for c in coins], days)

    cache = load_cache()
    if not cache:
        print(f"  {CACHE} 이 없습니다. 먼저 받으십시오:")
        print("      python funding_bench.py --fetch")
        return 1

    print("=" * 80)
    print("  펀딩 시험대 — 포지션 쏠림이 진입 시점을 알려주나")
    print("=" * 80)
    print(f"  대상 폴더: {os.getcwd()}")
    print(f"  {len(coins)}종 × {days}일 · 후보 {len(CANDIDATES)}개")
    print(f"  문턱: 그 코인의 지난 {PCT_WINDOW}일 상·하위 {round((1-HI_Q)*100)}% 분위수")
    print("  손절 1.5×ATR · 목표 1R/2R/3R · 한 코인 동시 1개")
    print("  3~6분 걸립니다...")
    t0 = time.time()

    hits, base, failed, nofund = run(coins, days, cache)
    if nofund:
        print(f"\n  ⚠️ 펀딩 캐시가 없어 빠짐 {len(nofund)}: {', '.join(nofund[:10])}")
    if failed:
        print(f"  ⚠️ 시세를 못 받아 빠짐 {len(failed)}: {', '.join(failed[:10])}")

    dates = sorted(t["date"] for ts in hits.values() for t in ts)
    if not dates:
        print("\n  거래가 하나도 없습니다.")
        return 1
    mid = str(pd.Timestamp(dates[0]) + (pd.Timestamp(dates[-1]) - pd.Timestamp(dates[0])) / 2)[:10]
    print(f"\n  구간 {dates[0]} ~ {dates[-1]}   (반 나누는 지점 {mid})")
    print(f"  기준선  무작위 롱 {mean_r(base[True]):+.3f}R ({len(base[True])}건)"
          f" · 무작위 숏 {mean_r(base[False]):+.3f}R ({len(base[False])}건)")

    passed = []
    for name, long_side, _ in sorted(CANDIDATES, key=lambda c: -mean_r(hits[c[0]])):
        ts, b = hits[name], base[long_side]
        print(f"\n  [{name}]  {'롱' if long_side else '숏'}  {len(ts)}건 · {mean_r(ts):+.3f}R")
        if len(ts) < 30:
            print("    ⚠️ 30건 미만 — 판단 보류")
            continue
        marks = []
        for lbl, a, bb in (("전체", ts, b),
                           ("전반기", split(ts, mid)[0], split(b, mid)[0]),
                           ("후반기", split(ts, mid)[1], split(b, mid)[1])):
            dd, lo, hi = excess(a, bb)
            good = lo > 0 and len(a) >= MIN_PER_PERIOD
            if lbl != "전체":
                marks.append(good)
            tail = (f"   초과 {dd:+.3f}  [{lo:+.3f}, {hi:+.3f}] "
                    f"{'✅' if good else ('·' if dd > 0 else '❌')}"
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
  후보 {len(CANDIDATES)}개 중 고른 것입니다. 순서를 지키십시오.
    1. diag_regime 으로 선택 편향 보정 후에도 남는지
    2. 봇에 알림 전용으로 넣고 페이퍼 4주 · 30건
    3. 백테스트 기대값과 페이퍼 실측이 비슷하면 그때 실매매""")
    else:
        print("""  통과한 후보가 없습니다.

  펀딩(포지션 쏠림)도 진입 시점을 고르는 데 기여하지 않는다는 뜻입니다.
  남은 비가격 축(미결제약정·롱숏비·테이커)은 과거가 30일뿐이라
  feature_log.jsonl 이 쌓이기를 기다려야 합니다.""")
    print(f"\n  ({time.time()-t0:.0f}초 소요)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
