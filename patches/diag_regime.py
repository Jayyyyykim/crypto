"""diag_regime.py — 그 성적이 우위인가, 그냥 그 시기였나 (읽기 전용)

    cd /path/to/auto
    python diag_regime.py            # 20종 × 730일
    python diag_regime.py 20 2y

**아무 파일도 고치지 않습니다.**

왜
──
SMA_SHORT 가 123건에 +0.223R 로 나왔다. 유일한 🟢다. 그런데 이건
**숏 전략이고, 잰 구간이 하락장이었다면 우위가 아니라 방향 베팅**이다.
그러면 다음 상승장에 그대로 잃는다.

백테스트 한 줄로는 이 둘을 구분할 수 없다. 여기서 네 가지를 따로 본다.

① 기간 반 나누기
   전반기·후반기 둘 다 플러스여야 우위다. 한쪽만 플러스면 국면이다.
   같이 찍히는 BTC 등락률과 견줘 본다 — 숏이 BTC 빠진 구간에서만
   벌었다면 답은 나온 것이다.

② 집중도
   상위 1종목을 빼도 플러스가 남는가. 20종 중 몇 종에서 벌었는가.
   한 종목이 만든 숫자는 다음에 재현되지 않는다.

③ 신뢰구간
   거래당 R의 표준편차로 95% 구간을 낸다. 하한이 0 아래면 "플러스인지
   아닌지 아직 모른다"가 정확한 표현이다. 표본이 몇 건 더 필요한지도
   같이 낸다.

④ 선택 편향
   타입 6개를 재서 제일 좋은 걸 골랐다. 6번 뽑아 제일 좋은 게 우연히
   좋아 보일 확률을 감안해 문턱을 올린다(본페로니).

읽는 법
──────
  네 항목이 모두 통과해야 "우위 후보"다. 하나라도 걸리면 그 이유가
  같이 찍힌다. 통과했다고 미래 수익이 보장되는 것도 아니다 — 다만
  '아직 아무것도 모르는 상태'는 벗어난 것이다.
"""

import math
import os
import sys
import time

pd = None
bt = None


# 거래소가 주는 만큼 받는다. ⑪ 전에는 페이지를 안 넘겨서 730 이상이
# 의미가 없었지만, 지금은 요청한 만큼 온다. 국면을 더 담으려면 길게
# 잡아야 한다 — 상승장 하나·하락장 하나로는 표본이 늘 모자란다.
MAX_DAYS = 1460


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


# ── 통계 ─────────────────────────────────────────────────────

def mean_r(trades):
    return sum(t["r"] for t in trades) / len(trades) if trades else 0.0


def stdev_r(trades):
    n = len(trades)
    if n < 2:
        return 0.0
    m = mean_r(trades)
    return math.sqrt(sum((t["r"] - m) ** 2 for t in trades) / (n - 1))


def conf_interval(trades, z=1.96):
    """거래당 기대값의 95% 구간. (하한, 상한, 표준오차)"""
    n = len(trades)
    if n < 2:
        return (float("-inf"), float("inf"), float("inf"))
    se = stdev_r(trades) / math.sqrt(n)
    m = mean_r(trades)
    return (m - z * se, m + z * se, se)


def needed_n(trades, target=0.0, z=1.96):
    """지금 기대값이 유지된다고 칠 때, 0을 벗어나려면 몇 건이 필요한가."""
    m, sd = mean_r(trades), stdev_r(trades)
    if sd <= 0 or m <= target:
        return None
    return int(math.ceil((z * sd / (m - target)) ** 2))


def split_by_date(trades, mid):
    a = [t for t in trades if t.get("date", "") < mid]
    b = [t for t in trades if t.get("date", "") >= mid]
    return a, b


def by_coin(trades):
    out = {}
    for t in trades:
        out.setdefault(t["_coin"], []).append(t)
    return out


# ── 수집 ─────────────────────────────────────────────────────

def collect(coins, days):
    by_type, failed = {}, []
    btc_daily = None
    for sym in coins:
        coin = sym.replace("/USDT", "")
        try:
            df_d = bt.get_ohlcv_history(sym, "1d", days + 100)
            if df_d is None or len(df_d) < 60:
                failed.append(coin)
                continue
            df_h4 = bt.get_ohlcv_history(sym, "4h", days + 100)
            df_w = bt.get_ohlcv_history(sym, "1w", days + 200)
            df_d = bt.compute_indicators(df_d)
            df_h4 = bt.compute_indicators(df_h4) if (df_h4 is not None and len(df_h4) >= 50) else None
            df_w = bt.compute_indicators(df_w) if (df_w is not None and len(df_w) >= 50) else None
            if coin == "BTC":
                btc_daily = df_d
            for sig in bt.detect_signals_vectorized(df_d, df_w, df_h4):
                t = {**sig, **bt.evaluate_trade(sig, df_d), "_coin": coin}
                by_type.setdefault(t.get("type", "기타"), []).append(t)
        except Exception as e:
            print(f"  [건너뜀] {coin}: {e}")
            failed.append(coin)
    for tr in by_type.values():
        tr.sort(key=lambda t: t.get("date", ""))
    return by_type, btc_daily, failed


def btc_move(df, lo, hi):
    """구간 [lo, hi) 의 BTC 등락률 %."""
    if df is None:
        return None
    s = df[(df["timestamp"] >= lo) & (df["timestamp"] < hi)]
    if len(s) < 2:
        return None
    return (s["close"].iloc[-1] / s["close"].iloc[0] - 1) * 100


# ── 판정 ─────────────────────────────────────────────────────

def judge(name, trades, mid, btc_a, btc_b, n_types):
    overall = mean_r(trades)
    out = [f"\n  [{name}]  {len(trades)}건 · {overall:+.3f}R/거래"]
    reasons = []
    # 전체가 이미 마이너스면 '어느 기간이 좋았나'를 따지는 건 의미가 없다.
    # 숫자는 그대로 보여주되 판정 사유로는 세지 않는다.
    moot = overall <= 0

    # ① 기간 반 나누기
    a, b = split_by_date(trades, mid)
    ma, mb = mean_r(a), mean_r(b)
    fa = f"{btc_a:+.0f}%" if btc_a is not None else "?"
    fb = f"{btc_b:+.0f}%" if btc_b is not None else "?"
    out.append(f"    ① 전반기  {len(a):>4}건 · {ma:+.3f}R    (BTC {fa})")
    out.append(f"       후반기  {len(b):>4}건 · {mb:+.3f}R    (BTC {fb})")
    if moot:
        pass
    elif len(a) < 10 or len(b) < 10:
        reasons.append("한쪽 기간의 표본이 10건 미만이라 비교가 안 된다")
    elif ma <= 0 and mb <= 0:
        reasons.append("두 기간 모두 마이너스")
    elif ma <= 0 or mb <= 0:
        good, bad = ("후반기", "전반기") if ma <= 0 else ("전반기", "후반기")
        reasons.append(f"{good}만 플러스이고 {bad}는 마이너스 — 국면 의존 의심")

    # ② 집중도
    coins = by_coin(trades)
    tot = {c: sum(t["r"] for t in ts) for c, ts in coins.items()}
    plus = sum(1 for v in tot.values() if v > 0)
    if tot:
        best = max(tot, key=tot.get)
        rest = [t for t in trades if t["_coin"] != best]
        mr = mean_r(rest) if rest else 0.0
        out.append(f"    ② 플러스 종목 {plus}/{len(coins)}종 · "
                   f"최고 {best} {tot[best]:+.1f}R")
        out.append(f"       {best} 빼면  {len(rest):>4}건 · {mr:+.3f}R")
        if not moot and mr <= 0:
            reasons.append(f"{best} 하나를 빼면 마이너스 — 한 종목이 만든 숫자")
        if not moot and plus < len(coins) * 0.4:
            reasons.append(f"{len(coins)}종 중 {plus}종에서만 벌었다")

    # ③ 신뢰구간
    lo, hi, se = conf_interval(trades)
    out.append(f"    ③ 95% 구간  [{lo:+.3f}, {hi:+.3f}]R   (표준오차 {se:.3f})")
    if not moot and lo <= 0:
        need = needed_n(trades)
        extra = f" — 지금 성적이 유지되면 {need}건쯤 필요" if need else ""
        reasons.append(f"신뢰구간이 0을 포함한다{extra}")

    # ④ 선택 편향 (본페로니)
    z = 1.96 if n_types <= 1 else abs(_z_for(0.05 / n_types))
    lo2, _, _ = conf_interval(trades, z=z)
    out.append(f"    ④ 타입 {n_types}개 중 고른 것 보정 후 하한  {lo2:+.3f}R")
    if not moot and lo2 <= 0 and lo > 0:
        reasons.append(f"보정 전엔 통과하지만 {n_types}개 중 고른 걸 감안하면 못 미친다")

    if moot:
        out.append("    ❌ 전체 기대값이 마이너스 — 여기서 끝. 위 숫자는 참고용이다")
        return out
    if reasons:
        out.append("    ⚠️ 우위라고 하기 어렵다")
        for r in reasons:
            out.append(f"       · {r}")
    else:
        out.append("    ✅ 네 항목 모두 통과 — 우위 후보")
    return out


def _z_for(p):
    """양측 p 에 해당하는 z. 근사식(Beasley-Springer-Moro 축약)."""
    # 필요한 건 p=0.0083(0.05/6) 근처 한 점이므로 이분탐색으로 충분하다.
    lo, hi = 0.0, 10.0
    for _ in range(80):
        mid = (lo + hi) / 2
        # 양측 꼬리 = 2*(1-Phi(mid))
        tail = math.erfc(mid / math.sqrt(2))
        if tail > p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def main(argv):
    if not _load():
        return 1
    rest = argv[1:]
    n = next((int(a) for a in rest if a.isdigit() and int(a) <= 50), 20)
    days = 730
    for a in rest:
        al = a.lower()
        if al.endswith("y") and al[:-1].isdigit():
            days = int(al[:-1]) * 365
        elif al.endswith("d") and al[:-1].isdigit():
            days = int(al[:-1])
    days = max(90, min(days, MAX_DAYS))

    coins = pick_coins(n)
    print("=" * 66)
    print("  우위인가 국면인가 (아무것도 고치지 않습니다)")
    print("=" * 66)
    print(f"  대상 폴더: {os.getcwd()}")
    print(f"  {len(coins)}종 × {days}일 — 1~3분 걸립니다...")
    t0 = time.time()

    by_type, btc, failed = collect(coins, days)
    if not by_type:
        print("\n  신호가 하나도 없습니다.")
        return 1

    all_dates = sorted(t.get("date", "") for tr in by_type.values() for t in tr)
    lo_d, hi_d = all_dates[0], all_dates[-1]
    mid = str(pd.Timestamp(lo_d) + (pd.Timestamp(hi_d) - pd.Timestamp(lo_d)) / 2)[:10]

    btc_a = btc_move(btc, pd.Timestamp(lo_d), pd.Timestamp(mid))
    btc_b = btc_move(btc, pd.Timestamp(mid), pd.Timestamp(hi_d) + pd.Timedelta(days=1))

    print(f"\n  구간 {lo_d} ~ {hi_d}   (반 나누는 지점 {mid})")
    if failed:
        print(f"  ⚠️ 빠진 종목 {len(failed)}: {', '.join(failed)}")

    n_types = len(by_type)
    order = sorted(by_type.items(), key=lambda kv: mean_r(kv[1]), reverse=True)
    for name, trades in order:
        label = bt._TYPE_LABEL.get(name, name) if hasattr(bt, "_TYPE_LABEL") else name
        if len(trades) < 10:
            print(f"\n  [{label}]  {len(trades)}건 — 표본이 너무 적어 건너뜁니다")
            continue
        print("\n".join(judge(label, trades, mid, btc_a, btc_b, n_types)))

    print(f"\n  ({time.time()-t0:.0f}초 소요)")
    print("""
  통과했더라도 이건 '아직 아무것도 모르는 상태를 벗어났다'는 뜻이지
  미래 수익이 보장된다는 뜻이 아닙니다. 지금 상장된 코인만 보므로
  생존 편향이 남아 있고, 실제 성적은 이보다 나쁠 가능성이 높습니다.""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
