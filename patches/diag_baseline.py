"""diag_baseline.py — 신호가 '아무 날이나 들어간 것'보다 나은가 (읽기 전용)

    cd /path/to/auto
    python diag_baseline.py            # 20종 × 730일
    python diag_baseline.py 20 2y

**아무 파일도 고치지 않습니다.**

왜
──
diag_regime 결과가 이랬다.

    SMA_SHORT   전반기(BTC +86%)  54건 -0.004R
                후반기(BTC -45%)  69건 +0.401R
    SMA_LONG    전반기(BTC +86%)  55건 +0.273R
                후반기(BTC -45%)  19건 -0.470R

  롱은 오를 때 벌고 숏은 내릴 때 번다. 정확히 거울상이다. 이건
  '신호가 좋다'가 아니라 **'그 방향으로 포지션을 들고 있었다'**일 수
  있다. 그렇다면 신호는 아무것도 안 한 것이고, 다음 국면에 그대로 잃는다.

  구분하는 법은 하나다. **같은 기간·같은 코인·같은 방향으로 아무 날이나
  들어갔을 때**와 비교하는 것이다.

무엇을 재나
──────────
신호가 난 날 말고, 무작위로 고른 날에 같은 구조의 거래를 넣는다:

    손절  1.5 × ATR14        (신호와 같은 폭)
    목표  1R / 2R / 3R       (신호와 같은 구조)
    체결  다음 봉 시가, 같은 수수료·슬리피지

  그 무작위 거래의 평균 R이 '기준선'이다. 그리고

      초과 = 신호의 기대값 − 기준선

  이 초과가 신호가 실제로 더한 값이다. 초과가 0이면 신호는
  달력을 던져서 정한 것과 다르지 않다.

  전반기·후반기 각각 따로 낸다. 국면이 원인이면 기준선도 같이
  움직이므로 초과만 0 근처에 남는다 — 그게 답이다.

주의
────
  무작위 표본은 코인 이름으로 고정된 씨앗을 쓴다. 몇 번을 돌려도
  같은 날이 뽑히므로 결과가 흔들리지 않는다.
"""

import math
import os
import sys
import time
import zlib

pd = None
bt = None

SAMPLES_PER_COIN = 60      # 방향당. 20종이면 방향당 1,200건쯤 된다.


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


def mean_r(ts):
    return sum(t["r"] for t in ts) / len(ts) if ts else 0.0


def se_r(ts):
    n = len(ts)
    if n < 2:
        return float("inf")
    m = mean_r(ts)
    sd = math.sqrt(sum((t["r"] - m) ** 2 for t in ts) / (n - 1))
    return sd / math.sqrt(n)


def stable_seed(text):
    """실행마다 같은 날이 뽑히게. 파이썬 hash()는 실행마다 달라진다."""
    return zlib.crc32(text.encode("utf-8"))


LONG_TYPES = ("MID_LONG", "FIB_LONG", "SMA_LONG", "ANGEL")


def is_long(type_name):
    return type_name in LONG_TYPES


def random_trades(df, coin, long_side, n=SAMPLES_PER_COIN):
    """무작위 날짜에 같은 구조로 넣은 거래들."""
    import random
    lo, hi = 50, len(df) - 2
    if hi <= lo:
        return []
    rng = random.Random(stable_seed(f"{coin}/{'L' if long_side else 'S'}"))
    idxs = sorted(rng.sample(range(lo, hi), min(n, hi - lo)))

    out = []
    for i in idxs:
        row = df.iloc[i]
        atr = row.get("atr", float("nan"))
        if atr != atr or atr <= 0:      # NaN 워밍업 구간
            continue
        price = float(row["close"])
        risk = float(bt.ATR_STOP_MULT) * float(atr)
        if risk <= 0:
            continue
        sign = 1 if long_side else -1
        sig = {
            "idx": i,
            "type": "MID_LONG" if long_side else "MID_SHORT",
            "entry": price,
            "sl": price - sign * risk,
            "tp1": price + sign * risk,
            "tp2": price + sign * risk * 2,
            "tp3": price + sign * risk * 3,
            "date": row["timestamp"].strftime("%Y-%m-%d"),
        }
        out.append({**sig, **bt.evaluate_trade(sig, df)})
    return out


def collect(coins, days):
    real, base, failed = {}, {True: [], False: []}, []
    btc = None
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
                btc = df_d
            for sig in bt.detect_signals_vectorized(df_d, df_w, df_h4):
                t = {**sig, **bt.evaluate_trade(sig, df_d), "_coin": coin}
                real.setdefault(t.get("type", "기타"), []).append(t)
            for side in (True, False):
                base[side].extend(random_trades(df_d, coin, side))
        except Exception as e:
            print(f"  [건너뜀] {coin}: {e}")
            failed.append(coin)
    return real, base, btc, failed


def split(ts, mid):
    return ([t for t in ts if t.get("date", "") < mid],
            [t for t in ts if t.get("date", "") >= mid])


def excess_line(label, actual, baseline):
    """(줄, 초과값, 통계적으로 0을 벗어나나)"""
    if not actual or not baseline:
        return f"    {label:<8} 표본 부족", 0.0, False
    ma, mb = mean_r(actual), mean_r(baseline)
    d = ma - mb
    se = math.sqrt(se_r(actual) ** 2 + se_r(baseline) ** 2)
    lo, hi = d - 1.96 * se, d + 1.96 * se
    ok = lo > 0
    mark = "✅" if ok else ("·" if d > 0 else "❌")
    return (f"    {label:<8} 신호 {ma:+.3f}R ({len(actual):>4}건)   "
            f"무작위 {mb:+.3f}R ({len(baseline):>5}건)   "
            f"초과 {d:+.3f}R  [{lo:+.3f}, {hi:+.3f}] {mark}"), d, ok


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
    print("=" * 78)
    print("  신호가 '아무 날이나 들어간 것'보다 나은가 (아무것도 고치지 않습니다)")
    print("=" * 78)
    print(f"  대상 폴더: {os.getcwd()}")
    print(f"  {len(coins)}종 × {days}일 · 무작위 표본 코인당 {SAMPLES_PER_COIN}건/방향")
    print("  2~5분 걸립니다...")
    t0 = time.time()

    real, base, btc, failed = collect(coins, days)
    if not real:
        print("\n  신호가 하나도 없습니다.")
        return 1
    if failed:
        print(f"\n  ⚠️ 빠진 종목 {len(failed)}: {', '.join(failed)}")

    dates = sorted(t.get("date", "") for tr in real.values() for t in tr)
    lo_d, hi_d = dates[0], dates[-1]
    mid = str(pd.Timestamp(lo_d) + (pd.Timestamp(hi_d) - pd.Timestamp(lo_d)) / 2)[:10]
    print(f"\n  구간 {lo_d} ~ {hi_d}   (반 나누는 지점 {mid})")

    verdicts = []
    for name, trades in sorted(real.items(), key=lambda kv: mean_r(kv[1]), reverse=True):
        if len(trades) < 10:
            continue
        side = is_long(name)
        label = bt._TYPE_LABEL.get(name, name) if hasattr(bt, "_TYPE_LABEL") else name
        print(f"\n  [{label}]  {'롱' if side else '숏'}")
        b = base[side]
        rows = [("전체", trades, b)]
        ra, rb = split(trades, mid)
        ba, bb = split(b, mid)
        rows += [("전반기", ra, ba), ("후반기", rb, bb)]
        oks, ds = [], []
        for lbl, act, bl in rows:
            line, d, ok = excess_line(lbl, act, bl)
            print(line)
            if lbl != "전체":
                oks.append(ok)
                ds.append(d)
        if all(oks) and ds and min(ds) > 0:
            print("    ✅ 두 기간 모두 무작위보다 유의하게 낫다")
            verdicts.append((label, True))
        elif ds and min(ds) <= 0:
            print("    ❌ 한 기간에서 무작위보다 못하다 — 신호가 더하는 게 없다")
            verdicts.append((label, False))
        else:
            print("    ⚠️ 무작위보다 나은지 아직 단정할 수 없다 (표본 부족)")
            verdicts.append((label, False))

    print("\n" + "=" * 78)
    won = [n for n, ok in verdicts if ok]
    if won:
        print(f"  무작위 진입을 이긴 타입: {', '.join(won)}")
        print("  이 타입만 다음 단계(봇 반영)로 가져갑니다.")
    else:
        print("""  무작위 진입을 이긴 타입이 없습니다.

  이 여섯 개는 '언제 들어갈지'를 정하는 데 기여하지 않습니다.
  성적 차이는 방향(롱이냐 숏이냐)과 그 기간의 시장이 만든 것입니다.
  손절·수수료를 고쳐도 이건 안 바뀝니다 — 고칠 대상이 아니라
  다시 만들 대상입니다.""")
    print(f"\n  ({time.time()-t0:.0f}초 소요)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
