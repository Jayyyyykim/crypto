"""
start.py — 이거 하나만 실행하면 됩니다

    python start.py

처음 돌리면 알아서 준비(채점 기준 박기 → 소급 채점)를 하고, 그다음부터는
매일 할 일(사건 기록 → 밀린 채점 → 리포트)을 합니다. 지금이 어느 단계인지
스스로 판단하므로 순서를 외울 필요가 없습니다.

명령을 직접 고르고 싶으면:

    python start.py setup     준비만 (기준 박기 + 소급 채점)
    python start.py daily     매일 할 일만
    python start.py report    리포트만 보기
    python start.py check     점검만 (이 숫자 믿어도 되나)

봇의 시세 함수를 자동으로 찾습니다. 못 찾으면 아래 CONFIG만 고치십시오.
"""

import os
import re
import sys

# ═══════════════════════════════════════════════════════════════
#  CONFIG — 자동 탐지가 실패했을 때만 고치면 됩니다
# ═══════════════════════════════════════════════════════════════

# 봇에서 시세를 가져오는 함수가 있는 파일 이름 (.py 빼고)
#   예: bot.py 안에 있으면 "bot"
BOT_MODULE = None          # None이면 자동 탐지

# 그 함수 이름
#   반드시 get_ohlcv(종목, 타임프레임, limit=개수) 형태여야 합니다
OHLCV_FUNC = None          # None이면 자동 탐지

# 분석할 종목. None이면 봇에서 찾아보고, 그것도 없으면 아래 기본값을 씁니다
COINS = None

FALLBACK_COINS = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "ADA/USDT",
    "DOGE/USDT", "LINK/USDT", "AVAX/USDT", "DOT/USDT", "ATOM/USDT",
]

# 소급 채점에 쓸 과거 봉 수 (많을수록 표본이 커지고 오래 걸립니다)
BACKFILL_BARS = 400

# ═══════════════════════════════════════════════════════════════

import call_journal
import console
import healthcheck
import kimchi_band
import regime_gate
import setup_ledger
import six_bar_align

console.enable_utf8()

# 라이브러리 자체 로그는 끈다 — 이 스크립트가 같은 내용을 더 읽기 좋게
# 다시 찍기 때문에, 켜 두면 "신규 사건 7건"이 두 줄로 나온다.
# 조회 실패처럼 꼭 봐야 하는 것만 따로 살린다.
_lib_say = console.say


def _quiet(*parts, **kw):
    text = " ".join(str(p) for p in parts)
    if "실패" in text:
        _lib_say(*parts, **kw)


console.say = _quiet

# 자동 탐지에서 볼 파일과 함수 이름 후보
_MODULE_HINTS = ["bot", "main", "exchange", "market", "data", "api",
                 "trader", "paper_trader", "core", "utils"]
_FUNC_HINTS = ["get_ohlcv", "fetch_ohlcv", "get_candles", "fetch_candles",
               "get_klines", "ohlcv"]
_COIN_HINTS = ["COINS", "SYMBOLS", "UNIVERSE", "WATCHLIST", "TARGET_COINS"]


def line(ch="─", n=66):
    print(ch * n)


def head(title):
    print()
    line("═")
    print(f"  {title}")
    line("═")


def plain(html):
    """텔레그램용 HTML 태그를 지워 콘솔에서 읽히게."""
    return re.sub(r"<[^>]+>", "", html)


# ═══════════════════════════════════════════════════════════════
#  봇 연결
# ═══════════════════════════════════════════════════════════════

def find_ohlcv():
    """봇의 시세 함수를 찾는다. (함수, 설명) 또는 (None, 안내문)."""
    if BOT_MODULE and OHLCV_FUNC:
        try:
            mod = __import__(BOT_MODULE)
            return getattr(mod, OHLCV_FUNC), f"{BOT_MODULE}.{OHLCV_FUNC}()"
        except Exception as e:
            return None, f"CONFIG에 적은 {BOT_MODULE}.{OHLCV_FUNC}를 못 불렀습니다: {e}"

    here = os.path.dirname(os.path.abspath(__file__))
    names = [f[:-3] for f in sorted(os.listdir(here))
             if f.endswith(".py") and f[:-3] not in _SELF_MODULES]
    # 이름이 그럴듯한 것부터 본다
    names.sort(key=lambda n: (n not in _MODULE_HINTS, n))

    for name in names:
        try:
            mod = __import__(name)
        except Exception:
            continue          # 봇의 다른 파일이 안 열려도 무시하고 계속
        for fn in _FUNC_HINTS:
            f = getattr(mod, fn, None)
            if callable(f):
                return f, f"{name}.{fn}()"
    return None, None


def find_coins():
    if COINS:
        return list(COINS), "CONFIG"

    here = os.path.dirname(os.path.abspath(__file__))
    for name in [f[:-3] for f in sorted(os.listdir(here))
                 if f.endswith(".py") and f[:-3] not in _SELF_MODULES]:
        try:
            mod = __import__(name)
        except Exception:
            continue
        for var in _COIN_HINTS:
            v = getattr(mod, var, None)
            if isinstance(v, (list, tuple)) and v and isinstance(v[0], str):
                return list(v), f"{name}.{var}"
    return list(FALLBACK_COINS), "기본값"


_SELF_MODULES = {
    "start", "selfcheck", "console", "jsonstore", "level_map",
    "expected_range", "regime_gate", "setup_ledger", "call_journal",
    "six_bar_align", "risk_calc", "grid_lines", "kimchi_band",
    "healthcheck", "demo_edge",
}


def explain_no_ohlcv():
    head("봇의 시세 함수를 못 찾았습니다")
    print("""
  이 스크립트는 봇이 이미 갖고 있는 '시세 가져오는 함수'를 빌려 씁니다.
  그 함수가 어디 있는지만 알려주면 됩니다.

  1) 봇 폴더에서 이런 함수를 찾으십시오 (이름은 다를 수 있습니다)

         def get_ohlcv(symbol, timeframe, limit=200):
             ...
             return df        # timestamp/open/high/low/close/volume

  2) start.py 위쪽 CONFIG를 고치십시오

         BOT_MODULE = "bot"          # 그 함수가 있는 파일 (.py 빼고)
         OHLCV_FUNC = "get_ohlcv"    # 함수 이름

  3) 다시 실행

         python start.py
""")


def probe_ohlcv(get_ohlcv, coins):
    """실제로 한 번 불러 본다. 인자 모양이 다르면 여기서 걸린다."""
    sym = coins[0]
    try:
        df = get_ohlcv(sym, "1d", limit=50)
    except TypeError as e:
        return None, (f"함수는 찾았는데 인자 모양이 다릅니다 ({e}).\n"
                      f"  get_ohlcv(종목, 타임프레임, limit=개수) 형태여야 합니다.")
    except Exception as e:
        return None, f"{sym} 시세를 못 받았습니다: {type(e).__name__}: {e}"

    if df is None or len(df) == 0:
        return None, f"{sym} 시세가 비어 있습니다."
    try:
        _ = df["close"], df["high"], df["low"]
    except Exception:
        return None, "결과에 close/high/low 열이 없습니다."
    return df, None


# ═══════════════════════════════════════════════════════════════
#  단계
# ═══════════════════════════════════════════════════════════════

def is_first_run():
    return not call_journal.criteria_history()


def do_setup(get_ohlcv, coins):
    head("준비 — 딱 한 번만 합니다")

    if is_first_run():
        print("\n[1/2] 채점 기준을 기록장에 박습니다")
        print("      결과를 본 뒤에 기준을 고칠 수 없게 먼저 적어 두는 절차입니다.")
        call_journal.register_criteria({
            "보합_밴드"    : setup_ledger.FLAT_BAND,
            "채점_시점"    : list(setup_ledger.DEFAULT_HORIZONS),
            "구간_룩백"    : setup_ledger.RANGE_LOOKBACK,
            "프리미엄_경계": setup_ledger.PREMIUM_AT,
            "디스카운트_경계": setup_ledger.DISCOUNT_AT,
            "발표_최소표본": 30,
            "주의": "이 기준은 결과가 나온 뒤에 바꾸지 않습니다.",
        })
        print("      → 완료. 이제 이 기준은 고치면 티가 납니다.")
    else:
        print("\n[1/2] 채점 기준 — 이미 박혀 있습니다 (건너뜀)")

    print(f"\n[2/2] 소급 채점 — 과거 {BACKFILL_BARS}봉으로 표본을 만듭니다")
    print(f"      {len(coins)}종목이라 몇 분 걸릴 수 있습니다. 기다리십시오.")
    setup_ledger.backfill_universe(coins, get_ohlcv, timeframe="1d",
                                   bars=BACKFILL_BARS)
    s = setup_ledger.summary()
    print(f"      → 사건 {s['total']:,}건 · {s['symbols']}종목 "
          f"({s['first_date']} ~ {s['last_date']})")


def do_daily(get_ohlcv, coins):
    head("매일 할 일")

    print("\n[1/3] 오늘 사건 기록")
    scan = None
    try:
        def analyze(symbol, tf):
            df = get_ohlcv(symbol, tf, limit=80)
            if df is None or len(df) < 40:
                return None
            closes = list(df["close"])
            series = six_bar_align.trend_series(closes, 10, 30)
            return {"trend_code": series[-1] or "side"}

        scan = regime_gate.scan_universe(coins, analyze)
        regime_gate.record_snapshot(scan)
        print(f"      환경: {scan['label']} — {scan['note']}")
    except Exception as e:
        print(f"      환경 판정 실패: {type(e).__name__}: {e}")

    n = setup_ledger.capture(coins, get_ohlcv, timeframe="1d",
                             regime=(scan or {}).get("label"))
    print(f"      → 신규 사건 {len(n)}건")

    print("\n[2/3] 밀린 채점 따라잡기")
    scored = setup_ledger.score_pending(get_ohlcv, timeframe="1d")
    print(f"      → {scored}건 채점")

    print("\n[3/3] 김프 기록 (선택)")
    print("      업비트/바이낸스 시세를 연결하면 '지금이 비싼지 싼지'가 나옵니다.")
    print("      kimchi_band.record(kimchi_band.premium(업비트원, 바이낸스달러, 환율))")

    return scan


def do_report(get_ohlcv, coins, scan=None):
    head("리포트")

    print(plain(setup_ledger.get_report(horizon=3)))

    try:
        rows = six_bar_align.scan_universe(coins, get_ohlcv)
        print()
        print(plain(six_bar_align.get_report(rows)))
    except Exception as e:
        print(f"\n여섯 봉 정렬 실패: {type(e).__name__}: {e}")

    if scan:
        print()
        print(plain(regime_gate.get_report(scan)))

    print()
    print(plain(call_journal.get_report(horizon=3)))


def do_check(get_ohlcv=None, coins=None):
    """점검을 한 번만 돌리고 결과를 돌려준다.

    화면에 찍는 것과 마지막 요약이 각자 healthcheck를 돌리면, 검사 범위가
    달라져 같은 화면에서 판정이 🚨와 ⚠️로 엇갈린다. 실제로 그랬다.
    한 번 돌린 결과를 둘이 같이 쓴다.
    """
    head("점검 — 이 숫자를 믿어도 되나")
    checks, real = healthcheck.run(3, symbols=coins, get_ohlcv_fn=get_ohlcv)
    level, msg = healthcheck.verdict(checks)

    for c in checks:
        # 기호를 healthcheck 리포트와 맞춘다 — 판정문이 "위 🚨부터"라고
        # 가리키는데 화면에 🚨가 없으면 어디를 보라는 건지 알 수 없다.
        mark = {"ok": "✅", "warn": "⚠️", "bad": "🚨"}[c["level"]]
        print(f"  {mark} {c['title']}")
        print(f"      {c['detail']}")
    if real:
        print("\n  기준선을 넘은 셋업")
        for r in real:
            print(f"    · {r['label']} — 적중 {r['hit_rate']*100:.1f}% · "
                  f"초과 {r['edge']*100:+.1f}%p (판정 {r['decided']}건)")
    line()
    print(f"  {msg}")
    return checks, real


def whats_next(checks, real):
    head("다음에 뭘 하면 되나")
    s = setup_ledger.summary()
    level, msg = healthcheck.verdict(checks)

    print()
    if s["total"] < 1000:
        print("  · 표본이 아직 적습니다. BACKFILL_BARS를 늘리거나 종목을 더 넣으십시오.")
    if not real:
        print("  · 지금은 기준선을 넘는 셋업이 없습니다. 이게 정상입니다 —")
        print("    적중률이 높아도 기준선과 같으면 그 셋업이 한 일은 없습니다.")
    else:
        print("  · 기준선을 넘은 셋업이 있습니다:")
        for r in real:
            print(f"      {r['label']} — 초과 {r['edge']*100:+.1f}%p "
                  f"(판정 {r['decided']}건)")
        print("    risk_calc.size_from_edge()에 넣으면 크기까지 나옵니다.")

    print(f"\n  · 매일 한 번 이 스크립트를 돌리십시오:  python start.py")
    print(f"  · 종합 판정: {msg}")


# ═══════════════════════════════════════════════════════════════

def main(argv):
    cmd = argv[1] if len(argv) > 1 else "auto"

    head("봇 확장 모듈")

    get_ohlcv, where = find_ohlcv()
    if get_ohlcv is None:
        explain_no_ohlcv()
        return 1

    coins, coin_src = find_coins()
    print(f"\n  시세 함수 : {where}")
    print(f"  종목      : {len(coins)}개 ({coin_src})")

    df, err = probe_ohlcv(get_ohlcv, coins)
    if err:
        print(f"\n  ⚠ {err}")
        explain_no_ohlcv()
        return 1
    print(f"  연결 확인 : {coins[0]} 일봉 {len(df)}개 정상 수신")

    if cmd == "check":
        do_check(get_ohlcv, coins)
        return 0
    if cmd == "report":
        do_report(get_ohlcv, coins)
        return 0
    if cmd == "setup":
        do_setup(get_ohlcv, coins)
        do_check(get_ohlcv, coins)
        return 0
    if cmd == "daily":
        scan = do_daily(get_ohlcv, coins)
        do_report(get_ohlcv, coins, scan)
        return 0

    # auto — 지금 어느 단계인지 스스로 판단
    if is_first_run() or setup_ledger.summary()["total"] == 0:
        do_setup(get_ohlcv, coins)
    scan = do_daily(get_ohlcv, coins)
    do_report(get_ohlcv, coins, scan)
    checks, real = do_check(get_ohlcv, coins)
    whats_next(checks, real)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
