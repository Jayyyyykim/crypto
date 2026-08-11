"""cvd_bench.py — 누적 거래량 델타(CVD)가 진입 시점을 알려주나

    cd /path/to/jaybot
    python cvd_bench.py --peek      # CVD 가 제대로 만들어지나 (먼저 이것)
    python cvd_bench.py             # 시험대 실행
    python cvd_bench.py 30 4y       # 30종 · 4년

왜 이것만 남았나
───────────────
지금까지 60가지를 쟀고 하나도 통과하지 못했다. 가격 45, 포지션 15.
그런데 봇의 프롬프트에는 CVD 가 **"아직 안 쟀다"** 로 남아 있었다.
정직하게 적어 둔 것이지만, 정직한 구멍도 구멍이다. 3개월 뒤에
"CVD 는 다르지 않을까"가 다시 나온다.

    "안 쟀다" 를 "쟀고 아니었다" 로 바꾸는 것이 이 도구의 전부다.

새로 받을 자료가 없다
────────────────────
CVD 는 테이커 매수/매도 **거래대금**의 누적 차다. coinglass_hist.jsonl
에 이미 buy·sell 이 통째로 들어 있다. feature_bench 는 그걸 하루치
비(buy/(buy+sell))로만 썼다 — 그게 `테이커 매수 극단` 후보다.

    하루치 비는 이미 쟀다. 안 쟀던 건 **누적**이다.

무엇이 다른가 — 누적이라야 보이는 것
──────────────────────────────────
CVD 이야기의 핵심은 언제나 **엇갈림(divergence)** 이다.

    가격은 20일 신고가인데 CVD 는 안 따라 올라왔다
      → 오른 게 산 사람 때문이 아니다 → 곧 빠진다 (라고들 한다)

이건 하루치 값으로는 만들 수 없다. 20일을 누적해야 나온다.
그래서 이 시험대의 후보 열 개 중 넷이 엇갈림이다.

누적을 어떻게 재나 — 절대값을 안 쓴다
──────────────────────────────────
CVD 를 첫날부터 더하면 **시작점을 어디로 잡느냐에 따라 값이 통째로
달라진다.** 그 값의 '상위/하위'를 따지는 건 아무 뜻이 없다.

그래서 N일 누적 델타를 같은 N일 총거래대금으로 나눈다.

    cvd20 = Σ(매수−매도, 20일) ÷ Σ(매수+매도, 20일)

−1 에서 +1 사이고, 코인이 커도 작아도, 옛날이어도 지금이어도
같은 뜻이다. 시작점이 없으므로 시작점에 안 흔들린다.

틀은 그대로다
────────────
feature_bench.run() 을 **그대로 부른다.** 손절 1.5×ATR, 목표
1R/2R/3R, 다음 봉 시가 체결, 수수료 0.06% + 슬리피지 0.05%,
한 코인 동시 1개, 무작위 진입과 비교, 전후반 분리.

재는 자가 도구마다 다르면 결과를 나란히 놓을 수 없다. 여기서
바꾸는 건 후보와 값 붙이는 법 둘뿐이다.

몇 개째인가
──────────
이걸로 70개다 (가격 45 · 포지션 15 · CVD 10). 후보가 많을수록
그중 하나가 우연히 좋아 보일 확률도 높아진다. 통과한 것이
나오면 **70분의 1** 이라는 걸 같이 봐야 한다. 화면 끝에 찍는다.
"""

import os
import sys
import time

fb = None
pd = None

CACHE = "coinglass_hist.jsonl"
SHORT, LONG = 5, 20        # 누적 창 (일)
MIN_TOTAL = 1.0            # 총거래대금이 이보다 작은 날은 값이 없는 것


def _load():
    """feature_bench 를 통해 backtest·signal_bench 까지 끌어온다."""
    global fb, pd
    if fb is not None:
        return True
    for cand in (os.getcwd(), os.path.dirname(os.path.abspath(__file__))):
        if os.path.exists(os.path.join(cand, "feature_bench.py")) and cand not in sys.path:
            sys.path.insert(0, cand)
    try:
        import feature_bench
    except ImportError as e:
        print(f"  feature_bench.py 를 못 불렀습니다: {e}")
        print("  이 스크립트는 feature_bench.py 와 같은 폴더에 두십시오.")
        return False
    if not feature_bench._load():
        return False
    fb, pd = feature_bench, feature_bench.pd
    return True


# ── 테이커 거래대금을 양쪽 다 읽는다 ─────────────────────────
#
# feature_bench.load_cache 는 taker 를 하루치 '비' 하나로 접어 버린다.
# 누적을 만들려면 buy·sell 을 갈라 놔야 한다. 그래서 따로 읽는다.

def load_taker(path=CACHE):
    """{coin: {날짜: (매수, 매도)}}, 쓴 칼럼 이름, 못 읽은 줄 수.

    하루에 여러 줄이면 **그날 마지막 것**을 쓴다 — feature_bench 와
    같은 규칙이다. 거래대금은 누적값이라 그날 끝값이 맞다.
    """
    import json
    out, cols, bad = {}, None, 0
    if not os.path.exists(path):
        return out, cols, bad
    tmp = {}                       # coin -> date -> (ms, buy, sell)
    with open(path, encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if r.get("kind") != "taker":
                continue
            raw = r.get("raw")
            if not isinstance(raw, dict):
                bad += 1
                continue
            ms = fb.to_ms(r.get("ts"))
            date = fb.day_of(ms) if ms is not None else None
            if date is None:
                bad += 1
                continue
            b, cb = fb._find(raw, ("buy",))
            s, cs = fb._find(raw, ("sell",))
            if b is None or s is None or b < 0 or s < 0:
                bad += 1
                continue
            if cols is None:
                cols = f"{cb} / {cs}"
            day = tmp.setdefault(r["coin"], {})
            if date not in day or ms > day[date][0]:
                day[date] = (ms, b, s)
    for coin, days in tmp.items():
        out[coin] = {d: (b, s) for d, (_, b, s) in days.items()}
    return out, cols, bad


# ── 값 붙이기 ────────────────────────────────────────────────

def attach_cvd(d, cache, coin, taker=None):
    """feature_bench 가 붙이는 것 + CVD 계열.

    미래는 안 쓴다. 누적도 분위수 창도 **그날까지만** 본다.
    """
    d = fb.attach(d, cache, coin)
    taker = taker if taker is not None else CVD_TAKER
    m = (taker or {}).get(coin) or {}

    dates = [ts.strftime("%Y-%m-%d") for ts in d["timestamp"]]
    nan = float("nan")
    base = d["close"].astype("float64")

    buy = base.copy()
    sell = base.copy()
    buy.iloc[:] = [float(m[x][0]) if x in m else nan for x in dates]
    sell.iloc[:] = [float(m[x][1]) if x in m else nan for x in dates]

    delta = buy - sell
    total = buy + sell
    # 거래가 0 인 날을 0/0 으로 두면 누적이 통째로 NaN 이 된다.
    total = total.where(total >= MIN_TOTAL)

    for n, tag in ((SHORT, "cvd_s"), (LONG, "cvd_l")):
        ds = delta.rolling(n, min_periods=max(3, n // 2)).sum()
        ts_ = total.rolling(n, min_periods=max(3, n // 2)).sum()
        d[tag] = ds / ts_

    # 방금 부호가 바뀌었나 — '지금 뒤집혔다'는 하루짜리 사건이다.
    prev = d["cvd_l"].shift(1)
    d["cvd_flip_up"] = (d["cvd_l"] > 0) & (prev <= 0)
    d["cvd_flip_dn"] = (d["cvd_l"] < 0) & (prev >= 0)

    # 가격 쪽 — 엇갈림을 보려면 같은 창의 가격 상태가 있어야 한다.
    #
    # **오늘을 뺀 지난 20일**과 견준다. 오늘을 창에 넣고 >= 로 보면
    # 값이 안 움직이는 구간에서 매일이 '신고가'가 된다 — 횡보장
    # 전체가 신호가 되고, 그러면 엇갈림 후보는 아무것도 안 재는
    # 것과 같아진다.
    hi = d["close"].shift(1).rolling(LONG, min_periods=LONG).max()
    lo = d["close"].shift(1).rolling(LONG, min_periods=LONG).min()
    d["px_new_hi"] = d["close"] > hi
    d["px_new_lo"] = d["close"] < lo

    for name in ("cvd_s", "cvd_l"):
        r = d[name].rolling(fb.PCT_WINDOW, min_periods=30)
        d[name + "_hi"] = r.quantile(fb.HI_Q)
        d[name + "_lo"] = r.quantile(fb.LO_Q)
    return d


CVD_TAKER = {}             # run() 전에 채운다


def _flag(d, i, c):
    if c not in d.columns:
        return False
    v = d[c].iloc[i]
    return bool(v) if v == v else False


def _sign(d, i, c):
    """값의 부호. 없으면 None."""
    v = fb._v(d, i, c)
    return None if v is None else (1 if v > 0 else (-1 if v < 0 else 0))


# ── 후보 ─────────────────────────────────────────────────────
#
# (이름, 롱?, 조건)

CANDIDATES = [
    # ① 누적 자체 — 산 쪽이 이기고 있나
    ("CVD 5일 누적 상위 → 롱(추종)", True,
     lambda d, i: fb._hi(d, i, "cvd_s")),
    ("CVD 5일 누적 상위 → 숏(역추종)", False,
     lambda d, i: fb._hi(d, i, "cvd_s")),
    ("CVD 5일 누적 하위 → 롱(역추종)", True,
     lambda d, i: fb._lo(d, i, "cvd_s")),
    ("CVD 20일 누적 상위 → 숏(역추종)", False,
     lambda d, i: fb._hi(d, i, "cvd_l")),
    ("CVD 20일 누적 하위 → 롱(역추종)", True,
     lambda d, i: fb._lo(d, i, "cvd_l")),

    # ② 엇갈림 — 누적이라야 만들 수 있는 것. CVD 이야기의 핵심.
    ("약세 엇갈림: 가격 20일 신고가 · CVD 는 음수 → 숏", False,
     lambda d, i: _flag(d, i, "px_new_hi") and _sign(d, i, "cvd_l") == -1),
    ("강세 엇갈림: 가격 20일 신저가 · CVD 는 양수 → 롱", True,
     lambda d, i: _flag(d, i, "px_new_lo") and _sign(d, i, "cvd_l") == 1),

    # ③ 확인 — 엇갈림의 반대. 가격과 CVD 가 같이 간다.
    ("확인: 가격 20일 신고가 · CVD 상위 → 롱", True,
     lambda d, i: _flag(d, i, "px_new_hi") and fb._hi(d, i, "cvd_l")),

    # ④ 전환 — 방금 뒤집힌 날
    ("CVD 20일 누적이 음→양 전환 → 롱", True,
     lambda d, i: _flag(d, i, "cvd_flip_up")),
    ("CVD 20일 누적이 양→음 전환 → 숏", False,
     lambda d, i: _flag(d, i, "cvd_flip_dn")),
]

TESTED_BEFORE = 60         # 가격 45 + 포지션 15


# ── 자료 점검 ────────────────────────────────────────────────

def cmd_peek(path=CACHE):
    """CVD 가 실제로 만들어지나. 시험대 돌리기 전에 이것부터."""
    print("=" * 78)
    print("  CVD 자료 점검")
    print("=" * 78)
    print(f"  대상 폴더: {os.getcwd()}")
    if not os.path.exists(path):
        print(f"\n  {path} 이 없습니다. 먼저 받으십시오:")
        print("      python coinglass_probe.py --fetch")
        return 1

    taker, cols, bad = load_taker(path)
    if not taker:
        print("\n  테이커 자료가 한 줄도 없습니다.")
        print("  coinglass_probe.py --coverage 로 무엇이 왔는지 보십시오.")
        return 1

    print(f"\n  쓰는 칼럼: {cols}")
    print(f"  종목 {len(taker)}종" + (f" · 못 읽은 줄 {bad:,}" if bad else ""))

    print(f"\n  {'종목':<8} {'일수':>6}  {'구간':<24} "
          f"{'하루 델타 평균':>14}")
    thin = []
    for coin in sorted(taker, key=lambda c: -len(taker[c]))[:40]:
        days = taker[coin]
        ds = sorted(days)
        tot = sum(b + s for b, s in days.values())
        dlt = sum(b - s for b, s in days.values())
        share = (dlt / tot) if tot else 0.0
        print(f"  {coin:<8} {len(days):>6}  {ds[0]} ~ {ds[-1]}  "
              f"{share:>+13.4f}")
        if len(days) < 250:
            thin.append(f"{coin}({len(days)}일)")

    if thin:
        print(f"\n  ⚠️ 250일 미만이라 시험대에서 빠질 수 있음: "
              f"{', '.join(thin[:10])}")

    # 눈금 검사 — 델타 평균이 전부 한쪽이면 자료가 이상한 것이다.
    shares = []
    for days in taker.values():
        tot = sum(b + s for b, s in days.values())
        if tot:
            shares.append(sum(b - s for b, s in days.values()) / tot)
    if shares:
        pos = sum(1 for s in shares if s > 0)
        print(f"\n  눈금 검사 — 델타 평균이 양수인 종목 {pos}/{len(shares)}")
        if pos in (0, len(shares)) and len(shares) >= 5:
            print("    ⚠️ 전부 한쪽입니다. 매수/매도 칼럼이 뒤바뀌었거나")
            print("       한쪽만 채워졌을 수 있습니다. 위 칼럼 이름을 보십시오.")
        else:
            print("    양쪽에 흩어져 있습니다. 정상입니다.")
    print("=" * 78)
    return 0


# ── 실행 ─────────────────────────────────────────────────────

def main(argv):
    if not _load():
        return 1
    if "--peek" in argv:
        return cmd_peek()

    rest = [a for a in argv[1:] if not a.startswith("-")]
    n = next((int(a) for a in rest if a.isdigit() and int(a) <= 50), 30)
    days = fb.MAX_DAYS
    for a in rest:
        al = a.lower()
        if al.endswith("y") and al[:-1].isdigit():
            days = int(al[:-1]) * 365
        elif al.endswith("d") and al[:-1].isdigit():
            days = int(al[:-1])
    days = max(180, min(days, fb.MAX_DAYS))
    coins = fb.sb._pick(n)

    cache, _cols, _skipped = fb.load_cache()
    taker, tcols, bad = load_taker()
    if not taker:
        print(f"  {CACHE} 에 테이커 자료가 없습니다. 먼저 받으십시오:")
        print("      python coinglass_probe.py --fetch")
        return 1

    global CVD_TAKER
    CVD_TAKER = taker

    print("=" * 80)
    print("  CVD 시험대 — 누적 거래량 델타가 진입 시점을 알려주나")
    print("=" * 80)
    print(f"  대상 폴더: {os.getcwd()}")
    print(f"  {len(coins)}종 × {days}일 · 후보 {len(CANDIDATES)}개")
    print(f"  테이커 자료 {len(taker)}종 · 칼럼 {tcols}"
          + (f" · 못 읽은 줄 {bad:,}" if bad else ""))
    print(f"  누적 창 {SHORT}일 · {LONG}일 — 같은 창의 총거래대금으로 나눈다")
    print("  손절 1.5×ATR · 목표 1R/2R/3R · 한 코인 동시 1개")
    print("\n  3~8분 걸립니다...")
    t0 = time.time()

    hits, base, failed, nodata = fb.run(
        coins, days, cache,
        candidates=CANDIDATES,
        attach_fn=lambda d, c, coin: attach_cvd(d, c, coin, taker))

    missing = [s.replace("/USDT", "") for s in coins
               if s.replace("/USDT", "") not in taker]
    if missing:
        print(f"\n  ⚠️ 테이커 자료가 없어 CVD 가 안 붙는 종목 "
              f"{len(missing)}: {', '.join(missing[:10])}")
    if failed:
        print(f"  ⚠️ 시세를 못 받아 빠짐 {len(failed)}: {', '.join(failed[:10])}")

    dates = sorted(t["date"] for ts in hits.values() for t in ts)
    if not dates:
        print("\n  거래가 하나도 없습니다. --peek 로 CVD 가 붙는지 보십시오.")
        return 1
    mid = str(pd.Timestamp(dates[0])
              + (pd.Timestamp(dates[-1]) - pd.Timestamp(dates[0])) / 2)[:10]
    print(f"\n  구간 {dates[0]} ~ {dates[-1]}   (반 나누는 지점 {mid})")
    print(f"  기준선  무작위 롱 {fb.mean_r(base[True]):+.3f}R"
          f" ({len(base[True])}건)"
          f" · 무작위 숏 {fb.mean_r(base[False]):+.3f}R ({len(base[False])}건)")

    passed = report(hits, base, mid)

    print("\n" + "=" * 80)
    total = TESTED_BEFORE + len(CANDIDATES)
    if passed:
        print(f"  통과: {', '.join(passed)}")
        print(f"""
  ⚠️ 이걸로 **{total}개째**입니다 (가격 45 · 포지션 15 · CVD
     {len(CANDIDATES)}). 후보가 {total}개면 그중 하나가 우연히 좋아
     보일 확률도 그만큼 높습니다. 통과했다고 바로 돈을 걸지
     마십시오. 순서를 지키십시오.

    1. diag_regime 으로 상승장/하락장 어느 한쪽 덕이 아닌지
    2. 봇에 **알림 전용**으로 넣고 페이퍼 4주 · 30건
    3. 백테스트 기대값과 페이퍼 실측이 비슷하면 그때 실매매""")
    else:
        print(f"""  통과한 후보가 없습니다. ({len(CANDIDATES)}개 시험)

  이걸로 **{total}개** 입니다 — 가격 45 · 포지션 15 · CVD
  {len(CANDIDATES)}. 전부 통과 못 했습니다.

  CVD 는 봇 프롬프트에 '아직 안 쟀다'로 남아 있던 마지막
  구멍이었습니다. 이제 **'쟀고 아니었다'** 입니다. 다음에
  누가 "CVD 엇갈림은 다르지 않을까"라고 하면 여기를 보면 됩니다.

  엇갈림 후보 넷이 전부 통과 못 한 것이 특히 값집니다 — 그게
  CVD 이야기의 핵심이었기 때문입니다.""")
    print(f"\n  ({time.time() - t0:.0f}초 소요)")
    return 0


def report(hits, base, mid):
    """feature_bench 와 같은 표. 판정도 같다."""
    passed = []
    for name, long_side, _ in sorted(CANDIDATES,
                                     key=lambda c: -fb.mean_r(hits[c[0]])):
        ts, b = hits[name], base[long_side]
        print(f"\n  [{name}]  {'롱' if long_side else '숏'}  "
              f"{len(ts)}건 · {fb.mean_r(ts):+.3f}R")
        if len(ts) < 30:
            print("    ⚠️ 30건 미만 — 판단 보류")
            continue
        marks = []
        for lbl, a, bb in (("전체", ts, b),
                           ("전반기", fb.split(ts, mid)[0], fb.split(b, mid)[0]),
                           ("후반기", fb.split(ts, mid)[1], fb.split(b, mid)[1])):
            dd, lo, hi = fb.excess(a, bb)
            good = lo > 0 and len(a) >= fb.MIN_PER_PERIOD
            if lbl != "전체":
                marks.append(good)
            tail = (f"   초과 {dd:+.3f}  [{lo:+.3f}, {hi:+.3f}] "
                    f"{'✅' if good else ('·' if dd > 0 else '❌')}"
                    if len(a) >= fb.MIN_PER_PERIOD
                    else f"   — {fb.MIN_PER_PERIOD}건 미만이라 판정 보류")
            print(f"    {lbl:<7} {fb.mean_r(a):+.3f}R ({len(a):>4}건)  "
                  f"vs 무작위 {fb.mean_r(bb):+.3f}R" + tail)
        if marks and all(marks):
            print("    ✅ 두 기간 모두 무작위보다 유의하게 낫다")
            passed.append(name)
        else:
            print("    ❌ 통과 못 함")
    return passed


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
