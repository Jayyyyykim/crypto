"""tf_bench.py — 일봉이 아닌 시간대에서는 다른가

    cd /path/to/jaybot
    python tf_bench.py                # 4시간봉 · 30종 · 2년
    python tf_bench.py 4h 2y
    python tf_bench.py 1h 1y
    python tf_bench.py --cost         # 각 시간대의 비용만 계산 (30초)

왜 이걸 재나
───────────
지금까지 60개를 쟀는데 **전부 일봉 진입**이었다. 그런데 봇은 6개
시간대를 본다. "4시간봉에서는 달랐을 수도 있잖아"가 남아 있으면
3개월 뒤에 그 얘기가 다시 나온다. 재고 닫는다.

먼저 계산해 볼 것 — 비용
────────────────────────
거래 하나에 드는 고정비는 시간대와 무관하다. 수수료 0.06% ×2회 +
슬리피지 0.05% ×2회. 그런데 **손절폭은 시간대에 비례해 줄어든다.**

    비용(R) = 고정비(%) ÷ 손절폭(%)

손절폭이 절반이면 비용은 두 배다. 일봉 1.5×ATR14 가 보통 4~6%인데
4시간봉은 1.5~2%다. 즉 **거래 하나가 짊어지는 짐이 3배쯤 된다.**

  우리가 잰 것 중 제일 좋았던 초과가 +0.09R 이었다. 그 정도 우위는
  비용이 3배가 되면 그냥 먹힌다.

  이건 추론이다. --cost 로 실제 손절폭을 재서 확인하고, 그 다음
  시험대를 돌린다. 추론이 맞아도 **숫자로 확인한 것과 짐작한 것은
  다르다.**

무엇을 재나
──────────
`signal_bench.py` 의 후보 7개를 **그대로** 쓴다. 손절 1.5×ATR,
목표 1R/2R/3R, 다음 봉 시가 체결, 같은 수수료, 한 코인 동시 1개,
무작위 진입과 비교, 전후반 분리.

    다른 건 봉의 길이뿐이다.

지표 창은 봉 수 그대로다
───────────────────────
'20봉 신고가'는 4시간봉에서 3.3일 신고가가 된다. 200봉 이동평균은
33일선이 된다. 이게 맞다 — **그게 인트라데이 매매다.** 일봉과 같은
창(20일=120봉)을 쓰면 그건 '일봉 규칙을 4시간마다 확인하는 것'이지
다른 시간대의 매매가 아니다.

  그래서 이 시험은 "같은 규칙을 더 자주 본다"가 아니라 "더 빠른
  규칙"을 재는 것이다. 둘 다 궁금하지만, 사람들이 '4시간봉 매매'라고
  할 때 뜻하는 건 후자다.
"""

import math
import os
import sys
import time

pd = None
bt = None
sb = None

TF_HOURS = {"15m": 0.25, "1h": 1.0, "4h": 4.0, "1d": 24.0}
DEFAULT_TF = "4h"
MIN_BARS = 400
MIN_PER_PERIOD = 15


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
        import signal_bench
    except ImportError as e:
        print(f"  필요한 파일을 못 불렀습니다: {e}")
        print("  backtest.py · signal_bench.py 와 같은 폴더에 두십시오.")
        return False
    if not signal_bench._load():
        return False
    pd, bt, sb = pandas, backtest, signal_bench
    return True


def round_trip_cost():
    """거래 하나에 드는 고정비(비율). 진입·청산 각각 수수료+슬리피지."""
    fee = float(getattr(bt, "FEE_RATE", 0.0006))
    slip = float(getattr(bt, "SLIPPAGE", 0.0005))
    return 2.0 * (fee + slip)


def stop_width(d):
    """손절폭 / 가격 의 중앙값. 이게 비용을 R 로 바꾸는 분모다."""
    mult = float(getattr(bt, "ATR_STOP_MULT", 1.5))
    w = (d["atr"] * mult / d["close"]).dropna()
    w = w[(w > 0) & (w < 1)]
    return float(w.median()) if len(w) else float("nan")


def cost_in_r(width):
    return round_trip_cost() / width if width and width == width else float("nan")


def fetch(sym, coin, tf, days):
    """시세를 받는다. 세 번째 인자는 **일 수**다 — 봉 수가 아니다.

    여기를 헷갈려서 4시간봉 2년(4,680봉)을 '4,680일 달라'로 보냈다.
    거래소가 못 주니 사다리 맨 아래 400일까지 떨어졌고, 실제로는
    코인당 143일치로 쟀다. 화면에는 '2년'이라고 찍혀 있었다.

    ⑪ 과 똑같은 실수다 — 요청한 만큼 왔다고 믿은 것.
    """
    forms, seen = [], set()
    for s in (sym, f"{coin}/USDT:USDT", f"{coin}USDT"):
        if s not in seen:
            seen.add(s)
            forms.append(s)
    best, best_n = None, 0
    for want in (days, days // 2, 400):
        for s in forms:
            try:
                df = bt.get_ohlcv_history(s, tf, want)
            except Exception:
                continue
            n = 0 if df is None else len(df)
            if n > best_n:
                best, best_n = df, n
            if n >= MIN_BARS:
                return df
    return best if best_n >= MIN_BARS else None


def cmd_cost(coins, tfs=("1d", "4h", "1h")):
    """시험대를 돌리기 전에 **비용부터** 본다.

    여기서 비용이 우위보다 크면 그 시간대는 볼 것도 없다.
    30초면 답이 나오는 것을 3분 돌려서 알아낼 이유가 없다.
    """
    print("=" * 74)
    print("  시간대별 비용 — 거래 하나가 짊어지는 짐")
    print("=" * 74)
    c = round_trip_cost()
    print(f"  고정비 {c * 100:.3f}% (수수료·슬리피지, 왕복) · 손절 "
          f"{getattr(bt, 'ATR_STOP_MULT', 1.5)}×ATR14\n")
    print("  " + sb_w("시간대", 10) + sb_w("손절폭 중앙값", 16)
          + sb_w("비용(R)", 10) + "종목")
    print("  " + "─" * 60)

    rows = []
    for tf in tfs:
        widths, got = [], 0
        for sym in coins[:8]:                    # 비용은 8종이면 충분하다
            coin = sym.replace("/USDT", "")
            df = fetch(sym, coin, tf, 1200)
            if df is None:
                continue
            try:
                w = stop_width(bt.compute_indicators(df))
            except Exception:
                continue
            if w == w:
                widths.append(w)
                got += 1
        if not widths:
            print("  " + sb_w(tf, 10) + "받지 못했습니다")
            continue
        med = sorted(widths)[len(widths) // 2]
        r = cost_in_r(med)
        rows.append((tf, med, r))
        print("  " + sb_w(tf, 10) + sb_w(f"{med * 100:.2f}%", 16)
              + sb_w(f"{r:.3f}R", 10) + f"{got}종")

    print("  " + "─" * 60)
    if len(rows) >= 2:
        base = dict((t, r) for t, _, r in rows)
        if "1d" in base:
            print("\n  일봉 대비")
            for tf, _, r in rows:
                if tf != "1d" and base["1d"]:
                    print(f"    {tf:<6} 비용이 {r / base['1d']:.1f}배")
        print("""
  읽는 법
    지금까지 잰 60개 중 가장 큰 초과가 +0.09R 이었습니다.
    비용이 그보다 크면, 그 시간대에서는 **우위가 있어도 남지 않습니다.**
    그래도 재고 싶으면:  python tf_bench.py 4h 2y""")
    return 0


def sb_w(text, width, right=False):
    import unicodedata
    n = sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in str(text))
    pad = " " * max(0, width - n)
    return (pad + str(text)) if right else (str(text) + pad)


def run(coins, tf, days):
    hits = {name: [] for name, _, _ in sb.CANDIDATES}
    base = {True: [], False: []}
    failed, widths, spans = [], [], []

    for sym in coins:
        coin = sym.replace("/USDT", "")
        try:
            df = fetch(sym, coin, tf, days)
            if df is None:
                failed.append(coin)
                continue
            d = sb.prepare(bt.compute_indicators(df))
        except Exception as e:
            print(f"  [건너뜀] {coin}: {type(e).__name__}: {e}")
            failed.append(coin)
            continue

        w = stop_width(d)
        if w == w:
            widths.append(w)
        spans.append((str(d["timestamp"].iloc[0])[:10], str(d["timestamp"].iloc[-1])[:10],
                      len(d)))

        for side in (True, False):
            base[side].extend(sb.random_trades(d, coin, side))

        for name, long_side, cond in sb.CANDIDATES:
            free_at = -1
            for i in range(200, len(d) - 1):
                if sb.NO_OVERLAP and i <= free_at:
                    continue
                try:
                    ok = bool(cond(d, i))
                except Exception:
                    ok = False
                if not ok:
                    continue
                t = sb.make_trade(d, i, long_side)
                if t:
                    hits[name].append(t)
                    if sb.NO_OVERLAP:
                        free_at = i + sb.hold_bars(d, t)
    return hits, base, failed, widths, spans


def main(argv):
    if not _load():
        return 1

    rest = [a for a in argv[1:] if not a.startswith("-")]
    tf = next((a for a in rest if a in TF_HOURS), DEFAULT_TF)
    n = next((int(a) for a in rest if a.isdigit() and int(a) <= 50), 30)
    coins = sb._pick(n)

    if "--cost" in argv:
        return cmd_cost(coins)

    days = 730
    for a in rest:
        al = a.lower()
        if al.endswith("y") and al[:-1].isdigit():
            days = int(al[:-1]) * 365
        elif al.endswith("d") and al[:-1].isdigit():
            days = int(al[:-1])
    want_bars = int(days * 24 / TF_HOURS[tf])

    print("=" * 80)
    print(f"  시간대 시험대 — {tf} 에서는 다른가")
    print("=" * 80)
    print(f"  대상 폴더: {os.getcwd()}")
    print(f"  {len(coins)}종 × {tf} · {days}일 요청 (≈{want_bars:,}봉)"
          f" · 후보 {len(sb.CANDIDATES)}개")
    print("  손절 1.5×ATR · 목표 1R/2R/3R · 한 코인 동시 1개")
    print("  지표 창은 봉 수 그대로입니다 — '20봉 신고'가 "
          f"{20 * TF_HOURS[tf] / 24:.1f}일 신고가 됩니다.")
    print("\n  받는 중입니다. 일봉보다 오래 걸립니다...")
    t0 = time.time()

    hits, base, failed, widths, spans = run(coins, tf, days)
    if failed:
        print(f"\n  ⚠️ 시세를 못 받아 빠짐 {len(failed)}: {', '.join(failed[:10])}")
    if not spans:
        print("\n  시세를 하나도 못 받았습니다.")
        return 1

    med_w = sorted(widths)[len(widths) // 2] if widths else float("nan")
    cr = cost_in_r(med_w)
    med_bars = sorted(s[2] for s in spans)[len(spans) // 2]
    # 요청한 만큼 왔는지 **화면에서 바로** 보이게 한다.
    # ⑪ 도, 이 파일의 첫 판도, 요청한 만큼 왔다고 믿어서 틀렸다.
    got_days = sorted(
        (pd.Timestamp(hi) - pd.Timestamp(lo)).days for lo, hi, _ in spans)
    med_days = got_days[len(got_days) // 2]
    print(f"\n  실제로 받은 것: 중앙값 {med_bars:,}봉 · {med_days}일"
          f"  (요청 {days}일 · {want_bars:,}봉)")
    print(f"    예) {coins[0]}  {spans[0][0]} ~ {spans[0][1]} · {spans[0][2]:,}봉")
    if med_days < days * 0.7:
        print(f"    ⚠️ 요청의 {med_days / days * 100:.0f}% 만 왔습니다."
              " 아래 결과는 그 기간에 대한 것입니다.")

    # 기간이 맞아도 **봉이 다 왔는지는 별개**다.
    #
    # 598일에 1,194봉이 오면 기간은 82% 채워졌지만 4시간봉으로는
    # 3분의 1이다. 중간이 숭숭 비어 있다는 뜻이고, 그런 자료로 잰
    # '20봉 신고가'는 20봉 신고가가 아니다. 기간만 보면 못 잡는다.
    want_per_day = 24.0 / TF_HOURS[tf]
    dense = med_bars / max(1, med_days * want_per_day)
    if dense < 0.7:
        print(f"    ⚠️ **봉이 성깁니다** — {med_days}일이면 "
              f"{int(med_days * want_per_day):,}봉이어야 하는데 {med_bars:,}봉"
              f" ({dense * 100:.0f}%).")
        print("       중간이 비어 있습니다. 아래 결과는 참고만 하십시오 —"
              " 이 자료로는 이 시간대를 확정할 수 없습니다.")
    print(f"  손절폭 중앙값 {med_w * 100:.2f}%  ·  고정비 "
          f"{round_trip_cost() * 100:.3f}%  →  **거래당 비용 {cr:.3f}R**")

    dates = sorted(t["date"] for ts in hits.values() for t in ts)
    if not dates:
        print("\n  거래가 하나도 없습니다.")
        return 1
    mid = str(pd.Timestamp(dates[0])
              + (pd.Timestamp(dates[-1]) - pd.Timestamp(dates[0])) / 2)[:10]
    print(f"\n  구간 {dates[0]} ~ {dates[-1]}   (반 나누는 지점 {mid})")
    print(f"  기준선  무작위 롱 {sb.mean_r(base[True]):+.3f}R ({len(base[True])}건)"
          f" · 무작위 숏 {sb.mean_r(base[False]):+.3f}R ({len(base[False])}건)")
    print(f"          무작위가 대략 −{cr:.3f}R 근처면 눈금이 맞는 것입니다.")

    passed = []
    for name, long_side, _ in sorted(sb.CANDIDATES, key=lambda c: -sb.mean_r(hits[c[0]])):
        ts, b = hits[name], base[long_side]
        print(f"\n  [{name}]  {'롱' if long_side else '숏'}  "
              f"{len(ts)}건 · {sb.mean_r(ts):+.3f}R")
        if len(ts) < 30:
            print("    ⚠️ 30건 미만 — 판단 보류")
            continue
        marks = []
        for lbl, a, bb in (("전체", ts, b),
                           ("전반기", sb.split(ts, mid)[0], sb.split(b, mid)[0]),
                           ("후반기", sb.split(ts, mid)[1], sb.split(b, mid)[1])):
            dd, lo, hi = sb.excess(a, bb)
            good = lo > 0 and len(a) >= MIN_PER_PERIOD
            if lbl != "전체":
                marks.append(good)
            tail = (f"   초과 {dd:+.3f}  [{lo:+.3f}, {hi:+.3f}] "
                    f"{'✅' if good else ('·' if dd > 0 else '❌')}"
                    if len(a) >= MIN_PER_PERIOD
                    else f"   — {MIN_PER_PERIOD}건 미만이라 판정 보류")
            print(f"    {lbl:<7} {sb.mean_r(a):+.3f}R ({len(a):>4}건)  "
                  f"vs 무작위 {sb.mean_r(bb):+.3f}R" + tail)
        if marks and all(marks):
            print("    ✅ 두 기간 모두 무작위보다 유의하게 낫다")
            passed.append(name)
        else:
            print("    ❌ 통과 못 함")

    print("\n" + "=" * 80)
    if passed:
        print(f"  통과: {', '.join(passed)}")
        print(f"""
  ⚠️ 통과했더라도 비용을 먼저 보십시오. 이 시간대의 거래당 비용은
     {cr:.3f}R 입니다. 초과가 그보다 작으면 실제로는 남지 않습니다.
     그리고 지금까지 잰 것이 60개를 넘었습니다 — diag_regime 과
     페이퍼 4주를 거치기 전에는 돈을 걸지 마십시오.""")
    else:
        print(f"""  통과한 후보가 없습니다. ({len(sb.CANDIDATES)}개 시험 · {tf})

  이 시간대의 거래당 비용은 {cr:.3f}R 입니다. 일봉에서 잰 가장 큰
  초과가 +0.09R 이었으니, 비용이 그보다 크면 우위가 있어도 남지
  않습니다. 숫자로 확인됐습니다.""")
    print(f"\n  ({time.time() - t0:.0f}초 소요)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
