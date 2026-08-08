"""
run_backtest.py — 백테스트를 터미널에서 바로 돌립니다

`backtest.py`는 함수만 정의된 라이브러리라 `python backtest.py`를 실행하면
아무 일도 일어나지 않습니다(진입점이 없습니다). 원래는 텔레그램 명령
`/백테 btc 1y` 로 부르게 돼 있습니다. 이 스크립트는 같은 일을 터미널에서
하게 해 줍니다.

    cd /path/to/auto

    python run_backtest.py                 # 기본 10종 × 1년, 타입별 집계
    python run_backtest.py btc             # BTC 1년
    python run_backtest.py btc 6m          # BTC 6개월
    python run_backtest.py --all 20 2y     # 상위 20종 × 2년

왜 이걸 먼저 돌리나
─────────────────
페이퍼 승격 게이트는 "4주 + 30건"입니다. 백테스트는 과거 데이터로 **오늘
수백 건**을 만들어 줍니다. "이 전략에 우위가 있나"를 4주 기다리지 않고
지금 알 수 있습니다.

읽는 법
──────
신호 타입별 **기대값(R/거래)**만 보면 됩니다.

    🟢 +0.15R 이상  자동화 후보
    🟡 0 ~ +0.15R   보류 — 수수료·슬리피지 변동에 뒤집힐 수 있음
    🔴 0 이하       제외 권장 — 이 타입은 걸수록 잃습니다

승률이 높아도 기대값이 음수면 잃습니다. 반대도 마찬가지입니다.

⚠️ 과거 성적이 미래 수익을 보장하지 않습니다. 특히 이 백테스트는 지금
   상장된 코인만 보므로 생존 편향이 있고, 실제 성적은 이보다 나쁠
   가능성이 높습니다.
"""

import os
import re
import sys
import time

# 이 파일을 하위 폴더(예: patches/)에 두고 `python patches/run_backtest.py`
# 로 돌리면, 파이썬은 sys.path[0]에 **스크립트가 있는 폴더**를 넣는다.
# 그러면 backtest.py 를 못 찾는다. 지금 폴더도 후보에 넣어 준다.
for _cand in (os.getcwd(), os.path.dirname(os.path.abspath(__file__))):
    if os.path.exists(os.path.join(_cand, "backtest.py")) and _cand not in sys.path:
        sys.path.insert(0, _cand)

try:
    import console
    console.enable_utf8()
except Exception:
    pass

try:
    from backtest import parse_backtest_command, run_backtest, run_multi_backtest
except ImportError as e:
    print(f"backtest.py 를 못 불렀습니다: {e}")
    print(f"지금 폴더: {os.getcwd()}")
    print("봇 폴더(backtest.py 가 있는 곳)에서 실행하십시오.")
    raise SystemExit(1)

# 기본 대상 — config에 있으면 그걸 쓰고, 없으면 아래 목록
FALLBACK_COINS = ["BTC", "ETH", "SOL", "XRP", "BNB", "ADA", "DOGE", "AVAX",
                  "LINK", "DOT", "ATOM", "NEAR", "APT", "ARB", "OP"]


def plain(html):
    """텔레그램용 태그를 지워 콘솔에서 읽히게."""
    return re.sub(r"<[^>]+>", "", html or "")


def pick_coins(n):
    """텔레그램 /백테전체 와 **같은 목록**을 쓴다.

    bot.py 는 `from spotlight import SCAN_COINS` 후 `SCAN_COINS[:n]` 을 쓴다.
    예전엔 config 만 뒤져서 config.PAPER_COINS(5종)를 집었다 — 그래서
    `--all 20` 을 넣어도 5종만 돌았고, 20종 결과와 비교가 안 됐다.
    """
    for mod_name, var in (("spotlight", "SCAN_COINS"),      # ← bot.py 와 동일
                          ("config", "SCAN_COINS"),
                          ("config", "PAPER_COINS"),
                          ("config", "COINS")):
        try:
            mod = __import__(mod_name)
            v = getattr(mod, var, None)
            if isinstance(v, (list, tuple)) and v:
                picked = [f"{c}/USDT" if "/" not in c else c for c in v[:n]]
                return picked, f"{mod_name}.{var} ({len(v)}종 중)"
        except Exception:
            pass
    return [f"{c}/USDT" for c in FALLBACK_COINS[:n]], "기본 목록"


def toggles():
    """이 리포트가 **어느 설정으로** 나온 건지 같이 찍는다.

    패치가 들어갔는지 아닌지를 결과만 보고 맞히려다 몇 번 헛돌았다.
    리포트가 스스로 이름표를 달게 한다.
    """
    import backtest as bt
    rows = [
        ("레벨 근접 폭", getattr(bt, "SUPPORT_ZONE_PCT", "?"), 0.5, "⑥"),
        ("표본 하한", getattr(bt, "MIN_TRADES_TO_TRUST", None), 30, "④"),
        ("손절 ATR 배수", getattr(bt, "ATR_STOP_MULT", None), 1.5, "⑧"),
        ("4H 정배열 요구", getattr(bt, "MID_H4_STRICT", None), False, "⑨"),
        ("레벨 소스", getattr(bt, "LEVEL_SOURCE", None), "levelmap", "⑩"),
    ]
    out = []
    for label, got, want, num in rows:
        mark = "✅" if got == want else ("❌" if got is None else "⚙️")
        shown = "미적용" if got is None else got
        out.append(f"    {mark} {num} {label:14s} {shown}")
    return out


def parse_period(tokens, default_days=365):
    for t in tokens:
        tl = t.lower()
        if tl.endswith("y") and tl[:-1].isdigit():
            return int(tl[:-1]) * 365
        if tl.endswith("m") and tl[:-1].isdigit():
            return int(tl[:-1]) * 30
        if tl.endswith("d") and tl[:-1].isdigit():
            return int(tl[:-1])
    return default_days


def main(argv):
    args = argv[1:]

    print("=" * 62)
    print("  백테스트")
    print("=" * 62)
    print(f"  대상 폴더: {os.getcwd()}")
    print("  설정")
    for line in toggles():
        print(line)

    # ── 전체(여러 코인) 모드 ──
    if not args or args[0] in ("--all", "-a", "all", "전체"):
        rest = [a for a in args if a not in ("--all", "-a", "all", "전체")]
        n = next((int(a) for a in rest if a.isdigit() and int(a) <= 50), 10)
        days = parse_period(rest, 365)
        coins, src = pick_coins(n)

        print(f"\n  대상 {len(coins)}종 ({src}) · {days}일")
        if len(coins) < n:
            print(f"  ⚠️ {n}종을 요청했는데 목록에 {len(coins)}종뿐입니다."
                  " 결과를 다른 회차와 비교할 때 주의하십시오.")
        print(f"  {', '.join(c.replace('/USDT','') for c in coins)}")
        print("\n  데이터를 받는 중입니다. 2~4분 걸립니다...\n")
        t0 = time.time()
        try:
            report = run_multi_backtest(coins, days=days)
        except Exception as e:
            print(f"  실패: {type(e).__name__}: {e}")
            print("\n  거래소 접속이 막혀 있으면 이 오류가 납니다.")
            print("  인터넷 연결과 방화벽을 확인하십시오.")
            return 1
        print(plain(report))
        print(f"\n  ({time.time()-t0:.0f}초 소요)")
        return 0

    # ── 단일 코인 모드 ──
    coin = args[0].upper().replace("/USDT", "")
    days = parse_period(args[1:], 365)
    symbol = f"{coin}/USDT"

    print(f"\n  {coin} · {days}일")
    print("\n  데이터를 받는 중입니다. 1~2분 걸립니다...\n")
    t0 = time.time()
    try:
        report = run_backtest(symbol, days)
    except Exception as e:
        print(f"  실패: {type(e).__name__}: {e}")
        return 1
    print(plain(report))
    print(f"\n  ({time.time()-t0:.0f}초 소요)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
