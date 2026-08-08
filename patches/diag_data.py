"""diag_data.py — 백테스트가 실제로 **몇 봉을 받아오는지** 잰다 (읽기 전용)

    cd /path/to/auto
    python diag_data.py            # 8종 × 730일
    python diag_data.py 20 2y

**아무 파일도 고치지 않습니다.**

왜
──
diag_funnel 에서 20종 × 730일인데 전체 봉이 3,729로 나왔다.
14,600이어야 한다. 코인당 186봉 — 2년이 아니라 8개월치다.

지표 워밍업으로 빠지는 건 코인당 99봉뿐이다(rsi14·ma50·swing50 →
앞 49봉 + 신호 루프가 50부터 시작). 나머지는 애초에 안 받아온 것이다.

의심 자리는 get_ohlcv_history 의 이 줄이다:

    if len(batch) < 1000:
        break

  거래소가 한 번에 1000봉을 안 주면 거기서 페이지 넘기기를 그만둔다.
  Bitget 은 오래된 구간을 history-candles 로 주는데 이쪽 상한은 더
  낮다. 그러면 첫 배치 한 번 받고 끝난다.

  다만 이건 추정이다. 여기서 실제로 몇 봉이 오는지, 첫 봉이 언제인지
  찍어 확인한다. 타임프레임별로 따로 본다 — 주봉이 부족하면
  천사/악마가 영원히 0건이 된다(50봉 미만이면 아예 버린다).

읽는 법
──────
    코인   TF   요청     실제     기간        첫 봉
    BTC    1d   830일    285봉    285일       2025-10-26     ← 545일 모자람
    BTC    4h   830일   1000봉    166일       2026-02-22
    BTC    1w   930일     52봉    364일       2025-08-11

  '실제 기간'이 '요청'보다 훨씬 짧으면 그만큼 못 받은 것이다.
  4h 는 봉 수는 많은데 기간이 짧을 수 있다 — 그것도 같은 문제다.
"""

import os
import sys
import time

pd = None
bt = None


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


# run_multi_backtest._one() 이 실제로 요청하는 값과 같게 맞춘다.
def requests_for(days):
    return [("1d", days + 100), ("4h", days + 100), ("1w", days + 200)]


def probe(sym, tf, want_days):
    t0 = time.time()
    try:
        df = bt.get_ohlcv_history(sym, tf, want_days)
    except Exception as e:
        return {"err": f"{type(e).__name__}: {e}", "sec": time.time() - t0}
    if df is None or len(df) == 0:
        return {"err": "빈 결과 (None 또는 0봉)", "sec": time.time() - t0}
    span = (df["timestamp"].iloc[-1] - df["timestamp"].iloc[0]).days
    return {
        "bars": len(df),
        "span": span,
        "first": str(df["timestamp"].iloc[0])[:10],
        "last": str(df["timestamp"].iloc[-1])[:10],
        "sec": time.time() - t0,
    }


def main(argv):
    if not _load():
        return 1
    rest = argv[1:]
    n = next((int(a) for a in rest if a.isdigit() and int(a) <= 50), 8)
    days = 730
    for a in rest:
        al = a.lower()
        if al.endswith("y") and al[:-1].isdigit():
            days = int(al[:-1]) * 365
        elif al.endswith("d") and al[:-1].isdigit():
            days = int(al[:-1])
    days = max(90, min(days, 730))

    coins = pick_coins(n)
    print("=" * 68)
    print("  과거 데이터 수집 실측 (아무것도 고치지 않습니다)")
    print("=" * 68)
    print(f"  대상 폴더: {os.getcwd()}")
    print(f"  {len(coins)}종 · 백테스트 {days}일 기준\n")
    print(f"  {'코인':<8}{'TF':<5}{'요청':>7}{'실제봉':>8}{'실제기간':>9}"
          f"{'첫 봉':>13}   비고")
    print("  " + "─" * 64)

    short = {}
    for sym in coins:
        coin = sym.replace("/USDT", "")
        for tf, want in requests_for(days):
            r = probe(sym, tf, want)
            if "err" in r:
                print(f"  {coin:<8}{tf:<5}{want:>6}일{'':>8}{'':>9}{'':>13}   ❌ {r['err']}")
                short.setdefault(tf, []).append(coin)
                continue
            gap = want - r["span"]
            flag = ""
            if r["span"] < want * 0.7:
                flag = f"⚠️ {gap}일 모자람"
                short.setdefault(tf, []).append(coin)
            print(f"  {coin:<8}{tf:<5}{want:>6}일{r['bars']:>7}봉"
                  f"{r['span']:>8}일{r['first']:>13}   {flag}")
        print()

    print("  " + "─" * 64)
    if short:
        print("\n  요청보다 크게 짧은 타임프레임")
        for tf, lst in sorted(short.items()):
            print(f"    {tf}: {len(lst)}/{len(coins)}종 — {', '.join(lst[:10])}"
                  + (" 외" if len(lst) > 10 else ""))
        print("""
  이러면 백테스트는 요청한 기간이 아니라 받아온 기간만 잰다.
  표본이 적은 게 전략 탓이 아니라 데이터 탓이라는 뜻이다.

  주봉이 50봉 미만이면 천사/악마는 아예 검사도 안 된다
  (backtest.py 가 50봉 미만 주봉을 버린다).

  고치려면:  python fix_history_fetch.py --apply""")
    else:
        print("\n  ✅ 모든 타임프레임이 요청한 기간을 채웠습니다.")
        print("  표본이 적은 원인은 데이터가 아니라 신호 조건 쪽입니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
