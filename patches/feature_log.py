"""feature_log.py — 비가격 정보를 매일 한 줄씩 적어 둔다

    cd /path/to/auto
    python feature_log.py            # 오늘치 기록 (하루 한 번)
    python feature_log.py --report   # 얼마나 모였나
    python feature_log.py --check    # 한 종목만 찍어 동작 확인 (기록 안 함)

**읽기 전용이 아닙니다.** feature_log.jsonl 에 줄을 덧붙입니다.
기존 파일은 절대 고치지 않고 뒤에 붙이기만 합니다.

왜 오늘 켜야 하나
────────────────
일봉 가격으로 진입 시점을 고르는 축은 45번 재서 전부 닫혔다
(FINDINGS.md). 남은 축은 **가격이 아닌 정보**다 — 미결제약정, 펀딩,
롱숏비, 테이커 매수/매도. 봇에 수집 코드는 이미 다 있다.

문제는 **거래소가 30일치만 준다**는 것이다. 백테스트가 불가능하다.
과거를 살 방법이 없으니 지금부터 적는 수밖에 없다.

    오늘 켜면    3개월 뒤에 90일치를 시험대에 올릴 수 있다
    안 켜면      3개월 뒤에도 30일치뿐이다

하루에 30종 × 한 줄. 파일은 1년에 3MB 안 된다.

무엇을 적나
──────────
    oi              미결제약정 (계약 수)
    oi_chg_24h      24시간 변화율 — 신규 포지션이 들어왔나 빠졌나
    ls_ratio        전체 계정 롱/숏 비율
    top_ls_ratio    상위 트레이더 롱/숏 비율 (개미와 반대로 가는지)
    taker_ratio     테이커 매수/매도 — 시장가로 누가 밀어붙였나
    funding         펀딩비
    price           그날 시세 (나중에 봉과 맞춰보는 용)

**수익률은 적지 않는다.** 나중에 OHLCV 에서 계산한다. 지금 적으면
그 시점 이후를 알아야 하므로 미래를 보는 것이 된다.

한 소스가 실패해도 나머지는 기록한다. 실패한 소스 이름을 같이 남겨
나중에 "이 날은 왜 비었나"를 알 수 있게 한다.

거르는 것
────────
같은 날 같은 코인은 두 번 적지 않는다. 하루에 여러 번 돌려도 안전하다.

매일 돌리는 법 (윈도우)
─────────────────────
    작업 스케줄러 → 작업 만들기
      트리거: 매일 09:05
      동작:   프로그램  python
              인수      feature_log.py
              시작위치  C:\\Users\\user\\Desktop\\jaybot

  봇이 24시간 돌고 있다면 bot.py 스케줄러에 붙여도 된다.
  하루 한 번만 실제로 기록되므로 자주 불러도 문제없다.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone

LOG_PATH = "feature_log.jsonl"
SCHEMA = 1


def _now_utc():
    return datetime.now(timezone.utc)


def today_str():
    return _now_utc().strftime("%Y-%m-%d")


def pick_coins(n=None):
    for mod_name, var in (("spotlight", "SCAN_COINS"), ("config", "SCAN_COINS"),
                          ("config", "PAPER_COINS"), ("config", "COINS")):
        try:
            v = getattr(__import__(mod_name), var, None)
            if isinstance(v, (list, tuple)) and v:
                out = [c.replace("/USDT", "") for c in v]
                return out[:n] if n else out
        except Exception:
            pass
    return ["BTC", "ETH", "SOL", "XRP", "BNB"]


# ── 수집 ─────────────────────────────────────────────────────

def _oi_change(liq, symbol):
    """24시간 전 대비 미결제약정 변화율. 못 구하면 None."""
    hist = liq.get_oi_history(symbol, period="1h", limit=25)
    if not hist or len(hist) < 2:
        return None, None
    now, then = hist[-1]["oi"], hist[0]["oi"]
    if then <= 0:
        return now, None
    return now, round((now / then - 1) * 100, 2)


def collect(coin, liq, funding_map):
    """한 코인의 오늘치. (특징 dict, 실패한 소스 목록)"""
    symbol = f"{coin}/USDT"
    f, bad = {}, []

    try:
        oi, chg = _oi_change(liq, symbol)
        f["oi"], f["oi_chg_24h"] = oi, chg
        if oi is None:
            bad.append("oi")
    except Exception:
        f["oi"] = f["oi_chg_24h"] = None
        bad.append("oi")

    for key, fn, field in (
        ("ls_ratio", lambda: liq.get_long_short_ratio(symbol, period="1h", limit=1), "ratio"),
        ("top_ls_ratio", lambda: liq.get_top_trader_ratio(symbol, period="1h"), "ratio"),
        ("taker_ratio", lambda: liq.get_taker_buy_sell(symbol, period="1h", limit=1), "ratio"),
    ):
        try:
            r = fn()
            f[key] = r.get(field) if r else None
        except Exception:
            f[key] = None
        if f[key] is None:
            bad.append(key)

    f["funding"] = funding_map.get(coin)
    if f["funding"] is None:
        bad.append("funding")

    return f, bad


def price_of(coin):
    """그날 시세. 나중에 일봉과 맞춰보는 용도."""
    try:
        import backtest
        # 30일을 부른다. 5일처럼 짧게 부르면 backtest 의 '기간이 짧다'
        # 경고(⑪b)가 매번 뜬다 — 실제로는 문제가 아닌데 시끄럽다.
        df = backtest.get_ohlcv_history(f"{coin}/USDT", "1d", 30)
        if df is not None and len(df):
            return float(df["close"].iloc[-1])
    except Exception:
        pass
    return None


def funding_via_ccxt(coins):
    """거래소에서 직접 현재 펀딩비를 받는다.

    features.get_funding_rates() 는 코인 5개만 하드코딩돼 있고, 쓰는
    엔드포인트(Bitget mix v1)가 응답하지 않으면 통째로 빈 값이 온다.
    실제로 30종 전부 비어서 들어왔다. ccxt 로 받으면 종목 제한이 없다.
    """
    out = {}
    try:
        import backtest
        ex = backtest.exchange
    except Exception:
        return out
    for c in coins:
        for sym in (f"{c}/USDT:USDT", f"{c}/USDT"):
            try:
                r = ex.fetch_funding_rate(sym)
                v = r.get("fundingRate")
                if v is not None:
                    out[c] = round(float(v) * 100, 6)   # % 로 통일
                    break
            except Exception:
                continue
    return out


def funding_all():
    try:
        import features
        r = features.get_funding_rates()
        if isinstance(r, dict):
            # {"BTC": {"rate": ...}} 든 {"BTC": 0.01} 이든 받아낸다
            out = {}
            for k, v in r.items():
                coin = str(k).replace("/USDT", "").upper()
                if isinstance(v, dict):
                    for kk in ("rate", "funding", "fundingRate", "value"):
                        if kk in v:
                            out[coin] = float(v[kk])
                            break
                else:
                    try:
                        out[coin] = float(v)
                    except (TypeError, ValueError):
                        pass
            return out
    except Exception as e:
        print(f"  펀딩비를 못 받았습니다: {e}")
    return {}


# ── 저장 ─────────────────────────────────────────────────────

def already_logged(path=LOG_PATH):
    """이미 적힌 (날짜, 코인) 집합. 파일이 깨져 있어도 읽을 수 있는 줄만 쓴다."""
    seen = set()
    if not os.path.exists(path):
        return seen
    with open(path, encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                seen.add((row.get("date"), row.get("coin")))
            except json.JSONDecodeError:
                continue          # 깨진 줄은 건너뛰고 나머지를 살린다
    return seen


def append(rows, path=LOG_PATH):
    """덧붙이기만 한다. 기존 줄은 절대 고치지 않는다."""
    with open(path, "a", encoding="utf-8", newline="") as fp:
        for r in rows:
            fp.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
        fp.flush()
        os.fsync(fp.fileno())     # 전원이 나가도 적힌 것은 남는다


# ── 명령 ─────────────────────────────────────────────────────

def cmd_log(coins):
    try:
        import liquidation as liq
    except ImportError as e:
        print(f"liquidation.py 를 못 불렀습니다: {e}")
        print(f"지금 폴더: {os.getcwd()}")
        return 1

    date = today_str()
    seen = already_logged()
    todo = [c for c in coins if (date, c) not in seen]

    print(f"  {date} · 대상 {len(coins)}종 · 새로 적을 것 {len(todo)}종")
    if not todo:
        print("  오늘치는 이미 적혀 있습니다.")
        return 0

    fm = funding_all()
    missing_f = [c for c in todo if c not in fm]
    if missing_f:
        # features 쪽이 5종만 다루거나 통째로 비어 오는 일이 있다.
        fm.update(funding_via_ccxt(missing_f))
    rows, fails = [], {}
    for c in todo:
        f, bad = collect(c, liq, fm)
        f["price"] = price_of(c)
        if f["price"] is None:
            bad.append("price")
        rows.append({
            "schema": SCHEMA,
            "date": date,
            "coin": c,
            "captured_at": _now_utc().isoformat(timespec="seconds"),
            "f": f,
            "missing": sorted(set(bad)),
        })
        for b in bad:
            fails[b] = fails.get(b, 0) + 1
        time.sleep(0.25)          # 거래소를 몰아치지 않는다

    append(rows)
    print(f"  {len(rows)}줄 적었습니다 → {LOG_PATH}")
    if fails:
        print("  못 받은 항목: " + ", ".join(f"{k} {v}종" for k, v in sorted(fails.items())))
    return 0


def cmd_report():
    if not os.path.exists(LOG_PATH):
        print(f"  {LOG_PATH} 이 아직 없습니다. python feature_log.py 로 시작하십시오.")
        return 0
    days, coins, missing, bad_lines = {}, set(), {}, 0
    with open(LOG_PATH, encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                bad_lines += 1
                continue
            days.setdefault(r.get("date"), 0)
            days[r["date"]] += 1
            coins.add(r.get("coin"))
            for m in r.get("missing", []):
                missing[m] = missing.get(m, 0) + 1

    n = len(days)
    print("=" * 62)
    print("  비가격 정보 적립 현황")
    print("=" * 62)
    print(f"  기록된 날  {n}일   ({min(days)} ~ {max(days)})" if days else "  비어 있습니다")
    print(f"  코인       {len(coins)}종")
    print(f"  총 줄 수   {sum(days.values()):,}")
    if bad_lines:
        print(f"  ⚠️ 읽을 수 없는 줄 {bad_lines}개 (건너뜀)")
    if missing:
        print("  못 받은 항목 누적: " +
              ", ".join(f"{k} {v}" for k, v in sorted(missing.items(), key=lambda x: -x[1])))

    # 빠진 날 찾기
    if n >= 2:
        import datetime as _dt
        d0, d1 = min(days), max(days)
        cur = _dt.date.fromisoformat(d0)
        end = _dt.date.fromisoformat(d1)
        gaps = []
        while cur <= end:
            if cur.isoformat() not in days:
                gaps.append(cur.isoformat())
            cur += _dt.timedelta(days=1)
        if gaps:
            print(f"  ⚠️ 빠진 날 {len(gaps)}일: {', '.join(gaps[:8])}"
                  + (" 외" if len(gaps) > 8 else ""))

    need = 90
    print("=" * 62)
    if n >= need:
        print(f"  {need}일을 넘겼습니다. 시험대에 올릴 수 있습니다.")
    else:
        print(f"  시험대에 올리려면 {need}일쯤 필요합니다. {need - n}일 남았습니다.")
        print("  (30종 × 90일 ≈ 2,700줄. 진입 규칙 하나를 재기에 충분한 표본입니다)")
    return 0


def cmd_check(coin):
    try:
        import liquidation as liq
    except ImportError as e:
        print(f"liquidation.py 를 못 불렀습니다: {e}")
        return 1
    print(f"  {coin} 한 종목만 찍어 봅니다 (기록하지 않습니다)\n")
    fm = funding_all()
    if coin not in fm:
        fm.update(funding_via_ccxt([coin]))
    f, bad = collect(coin, liq, fm)
    f["price"] = price_of(coin)
    for k, v in f.items():
        print(f"    {k:<14} {v}")
    if bad:
        print(f"\n  못 받음: {', '.join(sorted(set(bad)))}")
        print("  일부가 비어도 나머지는 기록됩니다. 전부 비면 네트워크나")
        print("  바이낸스 접근을 확인하십시오(이 값들은 바이낸스에서 옵니다).")
    else:
        print("\n  전부 받았습니다.")
    return 0


def main(argv):
    for cand in (os.getcwd(), os.path.dirname(os.path.abspath(__file__))):
        if os.path.exists(os.path.join(cand, "liquidation.py")) and cand not in sys.path:
            sys.path.insert(0, cand)
    try:
        import console
        console.enable_utf8()
    except Exception:
        pass

    if "--report" in argv:
        return cmd_report()
    if "--check" in argv:
        rest = [a for a in argv[1:] if not a.startswith("-")]
        return cmd_check(rest[0].upper() if rest else "BTC")

    print("=" * 62)
    print("  비가격 정보 기록")
    print("=" * 62)
    print(f"  대상 폴더: {os.getcwd()}")
    return cmd_log(pick_coins())


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
