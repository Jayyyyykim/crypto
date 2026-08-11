"""fix_cvd_honest.py — ㉘ 심볼 해석을 한 규칙으로 · CVD 를 잰 결과로

    cd /path/to/jaybot
    python fix_cvd_honest.py           # 미리보기
    python fix_cvd_honest.py --apply   # 적용 (원본은 .bak)
    python fix_cvd_honest.py --verify  # 반영됐나

bot.py 를 고칩니다.

────────────────────────────────────────────────────────────

㉘a 심볼을 **부르는 규칙**을 하나로 — 이게 진짜 원인이었다

    데이터 수집 오류 (SKHYNIX/USDT 1d): bybit does not have market symbol

  bybit 상장 목록 3,171개를 직접 조회해 확인했습니다. 여섯 종목
  전부 실려 있습니다 — 다만 **무기한선물 표기로만** 실려 있습니다.

      SKHYNIX/USDT       없음
      SKHYNIX/USDT:USDT  있음      ← 스캔은 앞의 이름으로 넘긴다

  토큰화 주식이 코인 거래소에서 매매되면서 생긴 일입니다. 봇에
  `is_stock_symbol()` 이 있고 진짜 주식은 yfinance 로 돌리는데,
  이건 주식이 아니라 **바이빗 무기한선물**이라 그 갈래로 안 갑니다.

  ㉔에서 페이퍼 대상만 걸렀는데, 그건 반창고였습니다. `/질문`,
  차트, 레벨 조회는 여전히 실패합니다.

  → `market_symbol()` 하나를 두고, 거래소에 보낼 때만 그 이름으로
    바꿉니다. **화면에 쓰는 이름은 그대로 'SKHYNIX/USDT' 입니다** —
    'SKHYNIX/USDT:USDT' 를 코인 이름으로 쓰면 곳곳의
    `symbol.replace('/USDT','')` 가 'SKHYNIX:USDT' 를 만듭니다.

  ㉔의 거르는 함수도 같은 `market_symbol()` 을 씁니다.
  **거르는 규칙과 부르는 규칙이 어긋난 것이 이 버그의 정체**였습니다.
  한쪽만 봐주면 로그는 안 조용해지는데 화면에는 '다 통과'로 뜹니다.

㉘b~e CVD — 프롬프트가 아직 안 잰 것으로 알고 있다

  `cvd_bench.py` 로 CVD 10가지를 쟀습니다. 통과 0. 그런데 봇의
  AI 프롬프트는 두 군데서 이걸 **사실로 가르치고** 있습니다.

      - 가격↑ + CVD↓ = 가짜 상승 (반전 주의)
      • 데이터 모순 적극 짚기 (가격↑ + CVD↓ = 가짜 상승 등)

  그리고 다른 두 군데는 정직하게 "아직 안 쟀다"로 적혀 있습니다.
  이제 쟀으니 그것도 바꿔야 합니다.

  실측 (30종 · 2022-11 ~ 2026-08, 무작위 숏 기준선 −0.008R):

      약세 엇갈림(가격 신고가 · CVD 안 따라옴) → 숏
        1,252건 · −0.022R · 초과 −0.015   ❌ 무작위보다 못함
      강세 엇갈림 → 롱                     기준선 문제로 재측정 중
      CVD 20일 누적 양→음 전환 → 숏
        144건 · −0.194R · 초과 −0.186 [−0.369, −0.004]
        **무작위보다 유의하게 나쁨**

  ⑰~㉑ 에서 펀비·OI·롱숏을 이렇게 고쳤습니다. CVD 만 남아 있었습니다.
  프롬프트를 그대로 두면 AI 는 시킨 대로 매일 그 규칙으로 방향을
  말합니다. 지우는 게 아니라 **잰 결과로 바꿉니다** — 값은 쓰되
  방향 예측이 아니라 '지금이 평소와 얼마나 다른가'로.

  ※ `check_cvd_divergence_alert` 가 15분마다 도는 것은 건드리지
    않았습니다. 알림 자체를 끌지는 사람이 정할 일입니다.

되돌리려면
─────────
    bot.py.bak 을 덮어쓰면 됩니다.
"""

import ast
import os
import shutil
import sys

TODO, DONE, GONE = "적용 예정", "이미 적용됨", "대상 없음"
MARK = {TODO: "□", DONE: "✅", GONE: "⚠️"}


# ── ㉘a market_symbol() 을 get_ohlcv 앞에 둔다 ──
RESOLVE_OLD = '''def get_ohlcv(symbol, timeframe, limit=400):
    """OHLCV 수집 — 크립토(Bybit) / 주식·지수(yfinance) 자동 라우팅
    ※ v3.6부터 마감봉만 반환 (미완성 봉 자동 제거)"""
    if is_stock_symbol(symbol):
        return get_stock_ohlcv(symbol, timeframe, limit)
    for attempt in range(2):
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)'''

RESOLVE_NEW = '''def market_symbol(sym):
    """거래소가 실제로 아는 이름. 어떤 이름으로도 못 부르면 None.

    스캔은 'SKHYNIX/USDT' 로 넘기는데 바이빗에는
    'SKHYNIX/USDT:USDT'(무기한선물)로만 실려 있다. 토큰화 주식이
    코인 거래소에서 매매되면서 생긴 일이고, 여섯 종목이 매시간 네
    번씩 조회에 실패하며 로그를 덮고 있었다. 그 소음에 진짜 오류가
    묻혔다.

    **표시 이름은 그대로 두고 거래소에 보낼 때만 바꾼다.**
    'SKHYNIX/USDT:USDT' 를 코인 이름으로 쓰면 화면 곳곳의
    symbol.replace('/USDT','') 가 'SKHYNIX:USDT' 를 만든다.

    목록을 못 받으면 하던 대로 원래 이름을 돌려준다 — 이 함수
    때문에 조회가 멈추면 그건 개선이 아니다.
    """
    import time as _t
    now = _t.time()
    if now - _MARKETS["at"] > 3600 or not _MARKETS["syms"]:
        try:
            _MARKETS["syms"] = set(exchange.load_markets().keys())
            _MARKETS["at"] = now
        except Exception:
            return sym
    have = _MARKETS["syms"]
    if not have:
        return sym
    if sym in have:
        return sym
    perp = sym + ":USDT"
    if perp in have:
        return perp
    return None


def get_ohlcv(symbol, timeframe, limit=400):
    """OHLCV 수집 — 크립토(Bybit) / 주식·지수(yfinance) 자동 라우팅
    ※ v3.6부터 마감봉만 반환 (미완성 봉 자동 제거)"""
    if is_stock_symbol(symbol):
        return get_stock_ohlcv(symbol, timeframe, limit)
    real = market_symbol(symbol)
    if real is None:
        # 없는 이름으로 두 번 두드리고 오류를 찍을 이유가 없다.
        return None
    for attempt in range(2):
        try:
            ohlcv = exchange.fetch_ohlcv(real, timeframe, limit=limit)'''


PRICE_OLD = '''def get_current_price(symbol):
    """현재가 조회 — 크립토/주식 자동 라우팅, 재시도 포함"""
    if is_stock_symbol(symbol):
        return get_stock_price(symbol)
    for attempt in range(2):
        try:
            ticker = exchange.fetch_ticker(symbol)'''

PRICE_NEW = '''def get_current_price(symbol):
    """현재가 조회 — 크립토/주식 자동 라우팅, 재시도 포함"""
    if is_stock_symbol(symbol):
        return get_stock_price(symbol)
    # 시세도 같은 규칙으로 부른다. get_ohlcv 만 고치면 봉은 오는데
    # 현재가가 없어서 체결 검사가 조용히 건너뛴다.
    real = market_symbol(symbol)
    if real is None:
        return None
    for attempt in range(2):
        try:
            ticker = exchange.fetch_ticker(real)'''


# ── ㉘b 프롬프트 ① 사고 깊이 (ask_claude) ──
DEEP1_OLD = "  - 가격↑ + CVD↓ = 가짜 상승 (반전 주의)"

DEEP1_NEW = ("  - 가격↑ + CVD↓ — **쟀고 안 됐다** (1,252건 −0.022R,\n"
             "    무작위 숏 −0.008R보다 못함). '가짜 상승'이라고 단정하지 말고\n"
             "    '거래대금은 안 따라왔다'는 사실까지만 말할 것")


# ── ㉘c 프롬프트 ② 사고 깊이 (ask_claude_conversational) ──
DEEP2_OLD = "• 데이터 모순 적극 짚기 (가격↑ + CVD↓ = 가짜 상승 등)"

DEEP2_NEW = ("• 데이터 모순 적극 짚기 — 단, 모순을 **방향으로 번역하지 말 것**\n"
             "  (가격↑ + CVD↓ 는 쟀고 안 됐다. 사실만 말하고 방향은 붙이지 마라)")


# ── ㉘d "아직 안 쟀다" ① ──
NOTYET1_OLD = """30종 · 4년 · 진입 규칙 60가지를 무작위 진입과 겨루게 했고
통과한 것이 하나도 없다. 아래는 그 결과다."""

NOTYET1_NEW = """30종 · 4년 · 진입 규칙 70가지를 무작위 진입과 겨루게 했고
통과한 것이 하나도 없다. 아래는 그 결과다."""

NOTYET2_OLD = "• CVD↑+가격↑=진짜 / CVD↓+가격↑=가짜 — 이건 아직 안 쟀다"

NOTYET2_NEW = """• CVD 엇갈림: **쟀고 안 됐다.** 약세 엇갈림(가격 신고가인데
  CVD 안 따라옴) → 숏 1,252건 −0.022R, 무작위 숏 −0.008R보다 못함.
  CVD 20일 누적 양→음 전환 → 숏은 **무작위보다 유의하게 나빴다**
  (초과 −0.186 [−0.369, −0.004])"""


# ── ㉘e "아직 안 쟀다" ② ──
NOTYET3_OLD = """   · CVD↑+가격↑=진짜 / CVD↓+가격↑=가짜 — 이건 아직 안 쟀다.
     쓰되 '측정 안 됨'을 밝혀라."""

NOTYET3_NEW = """   · CVD 엇갈림은 쟀고 안 됐다. 값은 인용해도 되지만
     "CVD 가 안 따라오니 가짜 상승" 같은 방향 예측은 금지."""


# ── ㉘f 잰 개수 ──
COUNT_OLD = "• **진입 시점 규칙은 45가지를 쟀고 전부 실패했다.**"
COUNT_NEW = "• **진입 시점 규칙은 70가지를 쟀고 전부 실패했다.**"


SITES = [
    ("㉘a", "market_symbol() + get_ohlcv", RESOLVE_OLD, RESOLVE_NEW,
     "def market_symbol(sym):"),
    ("㉘b", "get_current_price 도 같은 규칙", PRICE_OLD, PRICE_NEW,
     "ticker = exchange.fetch_ticker(real)"),
    ("㉘c", "프롬프트 ① 가짜 상승 단정", DEEP1_OLD, DEEP1_NEW,
     "'거래대금은 안 따라왔다'는 사실까지만"),
    ("㉘d", "프롬프트 ② 모순을 방향으로 번역", DEEP2_OLD, DEEP2_NEW,
     "모순을 **방향으로 번역하지 말 것**"),
    ("㉘e", "잰 개수 60 → 70", NOTYET1_OLD, NOTYET1_NEW,
     "진입 규칙 70가지를 무작위"),
    ("㉘f", "CVD '아직 안 쟀다' ①", NOTYET2_OLD, NOTYET2_NEW,
     "CVD 엇갈림: **쟀고 안 됐다.**"),
    ("㉘g", "CVD '아직 안 쟀다' ②", NOTYET3_OLD, NOTYET3_NEW,
     "CVD 엇갈림은 쟀고 안 됐다."),
    ("㉘h", "잰 개수 45 → 70", COUNT_OLD, COUNT_NEW,
     "진입 시점 규칙은 70가지를 쟀고"),
]


def find(name="bot.py"):
    for cand in (os.path.join(os.getcwd(), name),
                 os.path.join(os.path.dirname(os.path.abspath(__file__)), name)):
        if os.path.exists(cand):
            return cand
    return None


def status_of(src, old, marker):
    if marker in src:
        return DONE
    if old in src:
        return TODO
    return GONE


def main(argv):
    apply = "--apply" in argv
    verify = "--verify" in argv

    path = find()
    print("=" * 62)
    print("  ㉘ 심볼 해석을 한 규칙으로 · CVD 를 잰 결과로")
    print("=" * 62)
    print(f"  대상 폴더: {os.getcwd()}")
    if not path:
        print("\n  bot.py 를 못 찾았습니다. 봇 폴더에서 실행하십시오.")
        return 1

    with open(path, encoding="utf-8") as fp:
        src = original = fp.read()

    if verify:
        print("\n  실제 파일 확인")
        for tag, label, _o, _n, marker in SITES:
            print(f"    {'✅' if marker in src else '□'} {tag} {label}")
        if "_MARKETS" not in src:
            print("\n  ⚠️ _MARKETS 가 없습니다. fix_bot_noise.py(㉔)를 먼저"
                  " 적용하십시오.")
        print("=" * 62)
        return 0

    if "_MARKETS" not in src:
        # market_symbol 은 ㉔ 이 만든 캐시를 쓴다. 없으면 붙여도
        # 첫 호출에서 NameError 다.
        print("\n  ❌ _MARKETS 가 없습니다. 먼저 이것부터:")
        print("       python fix_bot_noise.py --apply")
        return 1

    print("\n  bot.py")
    todo = gone = 0
    for tag, label, old, new, marker in SITES:
        st = status_of(src, old, marker)
        print(f"    {MARK[st]} {tag} {label} — {st}")
        if st == GONE:
            gone += 1
        if st == TODO:
            todo += 1
            if apply:
                src = src.replace(old, new, 1)

    if gone == len(SITES):
        print("\n  붙일 자리를 하나도 못 찾았습니다. 봇 판이 다릅니다.")
        print("  bot.py 를 그대로 보내 주시면 자리를 다시 맞추겠습니다.")
        return 1

    if not apply:
        print("\n" + "=" * 62)
        if todo:
            print(f"  {todo}개를 고칠 수 있습니다."
                  "  적용:  python fix_cvd_honest.py --apply")
        else:
            print("  고칠 것이 없습니다.")
        return 0

    if src == original:
        print("\n  바뀐 것이 없습니다.")
        return 0

    # 문법이 깨진 파일을 저장하면 봇이 아예 안 뜬다.
    try:
        ast.parse(src)
    except SyntaxError as e:
        print(f"\n  ❌ 고친 결과가 문법 오류입니다 ({e.lineno}행): {e.msg}")
        print("     저장하지 않았습니다. 원본 그대로입니다.")
        return 1

    shutil.copy2(path, path + ".bak")
    with open(path, "w", encoding="utf-8") as fp:
        fp.write(src)

    print("\n" + "=" * 62)
    print("  적용했습니다. 원본은 bot.py.bak")
    print("""
  봇을 재시작해야 반영됩니다.

  ㉘a 가 붙으면 이 줄들이 사라집니다.

      데이터 수집 오류 (SKHYNIX/USDT 1d): bybit does not have market symbol

  그리고 그 종목들이 **페이퍼 대상에 다시 들어옵니다** — 이제
  부를 수 있으니까요. 한 가지 알고 계십시오.

  ⚠️ 토큰화 주식은 장 시간이 있고 주말에 갭이 생깁니다. 우리가
     70가지를 잰 건 전부 24시간 도는 코인 일봉이었습니다. 같은
     규칙이 같게 돌 거라고 볼 근거가 없습니다. 페이퍼 결과를 볼 때
     그 종목들은 따로 떼어 보십시오.

     빼고 싶으면 config.py 의 PAPER_COINS 에서 제외하면 됩니다.

  ㉘c~h 는 AI 프롬프트입니다. /질문 에서 "CVD 가 안 따라오니 가짜
  상승" 같은 말이 사라지면 붙은 것입니다.""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
