"""fix_ask_context.py — ⑰ /질문에 역사적 위치 붙이기 · ⑱ 부정된 규칙 제거

    cd /path/to/jaybot
    python fix_ask_context.py           # 미리보기
    python fix_ask_context.py --apply   # 적용 (원본은 .bak)
    python fix_ask_context.py --verify  # 반영됐나

bot.py 를 고칩니다.

────────────────────────────────────────────────────────────

⑱ 이게 먼저다 — 시스템 프롬프트가 부정된 규칙을 가르치고 있다

  bot.py 의 AI 시스템 프롬프트에 이렇게 적혀 있습니다.

      • 펀비 +0.05%↑ → 롱 과열, 청산 위험
      • 펀비 -0.05%↓ → 숏 과열, 스퀴즈 가능
      • OI 급증 + 가격 옆걸음 → 곧 큰 움직임
      • 롱숏 70%+ 한쪽 쏠림 → 역지표 (반대로 갈 가능성)

  **이 셋을 전부 쟀습니다.** 30종 · 4년 · 무작위 진입과 비교
  (FINDINGS.md · 60가지 · 통과 0).

      펀딩 극단 양수 → 숏      1,811건   초과 -0.019  ❌
      펀딩 극단 음수 → 롱      1,340건   초과 +0.053  후반기 0
      OI 3일 급증 → 숏         1,450건   전반기만 ✅, 후반기 -0.053
      개미 롱 쏠림 → 숏          958건   전반기만 ✅, 후반기 -0.013
      큰손 롱 → 롱(추종)       1,162건   -0.114R (무작위 -0.073R)

  마지막 줄이 특히 중요합니다. **큰손을 따라가면 무작위보다 나빴습니다.**

  프롬프트를 그대로 두면 AI 는 시킨 대로 저 규칙으로 방향을 말합니다.
  그럴듯한 문장이 나오고, 근거는 없습니다. 우리가 두 달 걸려 알아낸
  것을 봇이 매일 반대로 말하는 셈입니다.

  → 지웠다가 아니라 **잰 결과로 바꿉니다.** 값을 쓰지 말라는 게
    아니라, 방향 예측이 아니라 '지금이 평소와 얼마나 다른가'와
    '무엇이 쏠려 있어 위험한가'로 쓰라고 적습니다.

⑰ /질문 에 역사적 위치를 붙인다

  지금 AI 는 "펀딩 0.018%" 라는 숫자만 받습니다. 그 코인의 4년치
  분포를 모르니 높은지 낮은지 알 수 없습니다.

  percentile_ctx 가 만든 기준표(30종 × 5.8년)를 붙이면 이렇게 됩니다.

      - 펀딩비: +0.0180% · 2020-10-16~2026-08-09 2,124일 기준 97분위

  숫자 하나가 문맥을 얻습니다. 그리고 해석 규칙이 **같이** 갑니다 —
  숫자만 주면 모델이 거기서 매매 조언을 지어냅니다.

  percentile_ctx.py 나 기준표가 없으면 이 블록은 조용히 빈 문자열이
  됩니다. 봇은 그대로 돌아갑니다.

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


# ── ⑰a 도우미 함수 삽입 ──
HELPER_ANCHOR = "def ask_claude_conversational(user_message, chat_id, market_data=None):"

HELPER_NEW = '''def build_percentile_block(market_data):
    """파생지표가 역사적으로 어디쯤인지 + 해석 규칙.

    percentile_ctx 가 CoinGlass 4년치로 만든 기준표를 쓴다. 지금 값은
    공짜로 온다 — 30일 제한은 과거에 걸린 것이지 현재값이 아니다.

    **해석 규칙을 반드시 같이 보낸다.** 숫자만 주면 모델이 거기서
    매매 조언을 지어낸다. 그럴듯한 문장이 나오지만 근거가 없다.

    도구나 기준표가 없으면 빈 문자열 — 봇은 그대로 돈다.
    """
    try:
        import percentile_ctx as _pc
    except Exception:
        return ""
    try:
        table = _pc.table_once()
        if not table:
            return ""
        coins = [c for c in market_data if c in table][:6]
        if not coins:
            return ""
        live = _pc.live_values(coins)
        if not live:
            return ""
        scales = _pc.detect_scales(live, table)
        ctxs = [_pc.context(c, live[c], table, scales) for c in coins if c in live]
        skip = _pc.miscalibrated(_pc.calibration(ctxs))
        blocks = [_pc.llm_context(c, live[c], table, scales, skip)
                  for c in coins if c in live]
        blocks = [b for b in blocks if b]
        return "\\n\\n".join(blocks) if blocks else ""
    except Exception as e:
        print(f"[percentile_ctx] 건너뜀: {type(e).__name__}: {e}")
        return ""


'''


# ── ⑰b 프롬프트에 블록 끼워넣기 ──
CALL_OLD = '''        full_message = f"""{user_message}

[현재 시장 데이터]
{json.dumps(market_data, ensure_ascii=False, default=str)}
'''

CALL_NEW = '''        # 파생지표가 역사적으로 어디쯤인가 (percentile_ctx).
        # 숫자만 주면 모델이 거기서 방향을 지어낸다 — 해석 규칙이
        # 블록 안에 같이 들어 있다.
        _pctl_block = build_percentile_block(market_data)

        full_message = f"""{user_message}

[현재 시장 데이터]
{json.dumps(market_data, ensure_ascii=False, default=str)}

{_pctl_block}
'''


# ── ⑱a 대화형 시스템 프롬프트 ──
FLOW_CONV_OLD = """━━━ 💰 자금흐름 ━━━
펀비 +0.05%↑→롱 과열 / -0.05%↓→숏 과열
OI 급증+가격 옆걸음→큰 움직임 임박
CVD↑+가격↑=진짜 / CVD↓+가격↑=가짜
롱숏 70%+ 한쪽 쏠림→역지표"""

FLOW_CONV_NEW = """━━━ 💰 자금흐름 — 흔한 규칙을 실제로 재 봤다 ━━━
30종 · 4년 · 진입 규칙 60가지를 무작위 진입과 겨루게 했다.
**통과한 것은 하나도 없다.**

  펀비 극단 → 반대 진입      쟀음. 우위 없음
  OI 급증 → 방향 예측        쟀음. 전반기만, 후반기에 사라짐
  롱숏 쏠림 → 역지표         쟀음. 전반기만, 후반기에 사라짐
  큰손 포지션 추종           쟀음. **무작위보다 나빴음**

→ 이 값들로 **방향을 예측하지 마라.** "펀비가 높으니 떨어진다" 금지.
→ 대신 이렇게 써라:
   · 지금이 평소와 얼마나 다른가 (분위수가 주어지면 그걸 인용)
   · 무엇이 쏠려 있어, 그게 청산되면 움직임이 커질 수 있는가 (위험)
   · 여러 지표가 동시에 극단이면 그 사실 자체를 알린다
   · CVD↑+가격↑=진짜 / CVD↓+가격↑=가짜 — 이건 아직 안 쟀다.
     쓰되 '측정 안 됨'을 밝혀라."""


# ── ⑱b 단발 시스템 프롬프트 ──
FLOW_ASK_OLD = """━━━ 💰 자금흐름 ━━━
• 펀비 +0.05%↑ → 롱 과열, 청산 위험
• 펀비 -0.05%↓ → 숏 과열, 스퀴즈 가능
• OI 급증 + 가격 옆걸음 → 곧 큰 움직임
• CVD↑ + 가격↑ = 진짜 상승 / CVD↓ + 가격↑ = 가짜 상승
• 롱숏 70%+ 한쪽 쏠림 → 역지표 (반대로 갈 가능성)"""

FLOW_ASK_NEW = """━━━ 💰 자금흐름 — 흔한 규칙을 실제로 재 봤다 ━━━
30종 · 4년 · 진입 규칙 60가지를 무작위 진입과 겨루게 했고
통과한 것이 하나도 없다. 아래는 그 결과다.

• 펀비 극단 → 반대 방향 진입: 우위 없음 (숏 1,811건 초과 -0.019)
• OI 급증 → 방향 예측: 전반기만 되고 후반기에 사라짐
• 롱숏 70%+ 쏠림 → 역지표: 전반기만 되고 후반기에 사라짐
• 큰손 포지션 추종: **무작위보다 나빴다** (롱 -0.114R vs 무작위 -0.073R)
• CVD↑+가격↑=진짜 / CVD↓+가격↑=가짜 — 이건 아직 안 쟀다

→ 방향 예측에 쓰지 말 것. '지금이 평소와 얼마나 다른가'와
  '무엇이 쏠려 있어 위험한가'까지만 말할 것."""


# ── ⑲a 매매 철학 (단발) ──
#
# 자금흐름만 고쳤더니 AI 가 이번엔 가격 구조로 진입가를 제시했다.
#   "$65,266~65,400 돌파 확인 후 재진입이 안전할 듯"
# 그 규칙도 쟀다 — 돌파롱 20일신고 -0.211R. 45가지 전부 실패.
#
# 특히 '비위남 시그널' 네 개는 우리가 직접 잰 그 신호들이고,
# 그중 미드숏은 **무작위보다 유의하게 나빴다** (초과 -0.280R,
# 95% 구간 [-0.550, -0.010]). 프롬프트가 그걸 '핵심 신호'로 가르치면
# AI 는 매일 그 신호로 진입을 권한다.
PHIL_ASK_OLD = """━━━ 📊 매매 철학 ━━━
• 구조가 먼저 (주봉 → 일봉 → 4H → 1H → 15분)
• 손익비 1:2 이상, 분할 진입, 몰빵 금지
• BTC가 방향, 알트는 그림자
• 비위남 시그널: 👼 천사 롱 / 😈 악마 숏 / 🟢 미드롱 / 🔴 미드숏
• 김태욱 피보나치: 0.382 / 0.618이 핵심 진입/지지"""

PHIL_ASK_NEW = """━━━ 📊 매매 철학 — 어디까지가 측정된 것인가 ━━━
• 손익비 1:2 이상, 분할 진입, 몰빵 금지 — 위험 관리다. 그대로 유지.
• BTC가 방향, 알트는 그림자 — 상관관계 관찰. 유지.

• 아래는 **진입 시점을 고르는 규칙**이고, 재 봤다.
  30종 · 4년 · 가격 규칙 45가지를 무작위 진입과 겨루게 했다.
  통과한 것이 하나도 없다.

    비위남 4종(천사·악마·미드롱·미드숏)   전부 실패
      └ 미드숏은 **무작위보다 유의하게 나빴다** (초과 -0.280R)
    돌파 진입 (20일 신고/신저)            -0.211R
    되돌림 진입 · 피보 0.382/0.618        전부 실패
    출구 4종 × 진입 7종 = 28조합          전부 실패

→ **진입가·손절가를 단정적으로 제시하지 마라.**
  "$65,266 돌파 확인 후 진입" 같은 문장 금지 — 그 규칙을 쟀고 졌다.
  레벨은 '사람들이 거기서 반응할 수 있는 자리'로만 말해라.
→ 다만 사용자가 **이미 진입가를 말하면** 그 계획의 손익비·위험을
  계산해 주는 것은 괜찮다. 그건 산수지 예측이 아니다."""


# ── ⑲b 매매 철학 (대화형) ──
PHIL_CONV_OLD = """━━━ 📈 트레이딩 배경 지식 ━━━
• 구조 우선 (주봉→일봉→4H)
• 손익비 1:2 이상, 분할 진입, 몰빵 X
• BTC가 방향, 알트는 그림자
• 비위남 시그널: 👼 천사 롱 / 😈 악마 숏 / 🟢 미드롱 / 🔴 미드숏"""

PHIL_CONV_NEW = """━━━ 📈 트레이딩 배경 지식 — 잰 것과 안 잰 것 ━━━
• 손익비 1:2 이상, 분할 진입, 몰빵 X — 위험 관리. 유지.
• BTC가 방향, 알트는 그림자 — 관찰. 유지.
• **진입 시점 규칙은 45가지를 쟀고 전부 실패했다.**
  비위남 4종도 포함이고, 미드숏은 무작위보다 유의하게 나빴다.
  돌파 진입 -0.211R. 피보 되돌림도 실패.
→ 진입가·손절가를 단정적으로 제시하지 말 것. 레벨은 '반응할 수
  있는 자리'까지만. 사용자가 계획을 말하면 손익비 계산은 해줘도 된다."""


# ── ⑳ /리스크 가 죽어 있다 ──
#
# handle_message 에 /리스크 분기가 두 번 나오는데, 앞의 것이
# startswith 라 뒤의 것까지 오지 못한다. 그래서 그냥 /리스크 를 치면
# 현황 대신 계산기 사용법만 나온다. /리스크현황 은 아예 죽어 있다.
RISK_OLD = """    elif tl.startswith("/리스크"):
        parsed = parse_risk_command(text)"""

RISK_NEW = """    elif tl in ["/리스크", "/risk", "/리스크현황"]:
        # 인자 없이 부르면 현황 보고. 아래 계산기 분기가 startswith 라
        # 이게 없으면 뒤쪽 분기까지 흐르지 못하고 죽는다.
        return get_risk_report()

    elif tl.startswith("/리스크"):
        parsed = parse_risk_command(text)"""


# ── ㉑ /질문이 AI에게 null 을 보내고 있다 ──
#
# analyze_timeframe 은 'price'·'signal' 이라는 칼럼을 주지 않는다.
# (현재가는 'current_price' 다.) 그래서 /질문 이 보내는 시장 데이터의
# 모든 타임프레임에 price: null, signal: null 이 들어간다.
# 있는 척하는 빈칸이 없는 것보다 나쁘다 — 모델이 "가격 정보 없음"으로
# 읽거나, 최악의 경우 지어낸다.
QUICK_OLD = """            results[name] = {
                "trend"     : data.get("trend"),
                "rsi"       : data.get("rsi"),
                "price"     : data.get("price"),
                "support"   : data.get("support"),
                "resistance": data.get("resistance"),
                "signal"    : data.get("signal"),
            }"""

QUICK_NEW = """            # analyze_timeframe 이 주는 실제 칼럼만 담는다.
            # 'price'·'signal' 은 없는 칼럼이라 매번 null 이 갔다.
            results[name] = {
                "trend"      : data.get("trend"),
                "trend_강도" : data.get("trend_strength"),
                "rsi"        : data.get("rsi"),
                "price"      : data.get("current_price"),
                "support"    : data.get("support"),
                "resistance" : data.get("resistance"),
                "adx"        : data.get("adx"),
            }"""


# ── ㉒ /질문이 통째로 조용히 죽을 수 있다 ──
#
# as_completed(..., timeout=) 은 **반복자 자체가** TimeoutError 를
# 던진다. 안쪽 try 는 fut.result() 만 감싸고 있어서 이걸 못 잡는다.
# 그러면 handle_message 가 예외로 빠져 메인 루프의 except 로 가고,
# 사용자는 "분석 중..." 만 받고 답을 영영 못 받는다.
BRIEF_OLD = """    market_data = {}
    with ThreadPoolExecutor(max_workers=len(coins)) as ex:
        futures = {ex.submit(quick_analysis, sym): sym for sym in coins}
        for fut in as_completed(futures, timeout=timeout_per_coin * len(coins)):
            sym = futures[fut]
            c = sym.replace("/USDT", "")
            try:
                market_data[c] = fut.result(timeout=timeout_per_coin)
            except Exception as e:
                print(f"[build_market_brief] {c} 실패: {type(e).__name__}: {e}")
                market_data[c] = {"error": "분석 실패"}
    return market_data"""

BRIEF_NEW = """    market_data = {}
    with ThreadPoolExecutor(max_workers=len(coins)) as ex:
        futures = {ex.submit(quick_analysis, sym): sym for sym in coins}
        try:
            for fut in as_completed(futures, timeout=timeout_per_coin * len(coins)):
                sym = futures[fut]
                c = sym.replace("/USDT", "")
                try:
                    market_data[c] = fut.result(timeout=timeout_per_coin)
                except Exception as e:
                    print(f"[build_market_brief] {c} 실패: {type(e).__name__}: {e}")
                    market_data[c] = {"error": "분석 실패"}
        except Exception as e:
            # as_completed 는 **반복자 자체가** 타임아웃을 던진다.
            # 안 잡으면 /질문 이 통째로 죽고, 사용자는 "분석 중..."만
            # 받은 채 답을 영영 못 받는다. 늦은 코인은 버리고 간다.
            print(f"[build_market_brief] 전체 대기 중단: {type(e).__name__}: {e}")
    for sym in coins:
        c = sym.replace("/USDT", "")
        market_data.setdefault(c, {"error": "시간 초과"})
    return market_data"""


SITES = [
    ("⑰a", "역사적 위치 도우미", HELPER_ANCHOR, HELPER_NEW + HELPER_ANCHOR,
     "build_percentile_block"),
    ("⑰b", "/질문 프롬프트에 붙이기", CALL_OLD, CALL_NEW, "_pctl_block"),
    ("⑱a", "대화형 프롬프트 — 자금흐름", FLOW_CONV_OLD, FLOW_CONV_NEW,
     "무작위보다 나빴음"),
    ("⑱b", "단발 프롬프트 — 자금흐름", FLOW_ASK_OLD, FLOW_ASK_NEW,
     "무작위보다 나빴다"),
    ("⑲a", "단발 프롬프트 — 매매 철학", PHIL_ASK_OLD, PHIL_ASK_NEW,
     "그 규칙을 쟀고 졌다"),
    ("⑲b", "대화형 프롬프트 — 매매 철학", PHIL_CONV_OLD, PHIL_CONV_NEW,
     "45가지를 쟀고 전부 실패했다"),
    ("⑳", "/리스크 죽은 분기 살리기", RISK_OLD, RISK_NEW,
     "이게 없으면 뒤쪽 분기까지"),
    ("㉑", "/질문이 보내던 null 제거", QUICK_OLD, QUICK_NEW,
     "'price'·'signal' 은 없는 칼럼이라"),
    ("㉒", "/질문 조용한 죽음 막기", BRIEF_OLD, BRIEF_NEW,
     "전체 대기 중단"),
]


def find(name="bot.py"):
    for cand in (os.path.join(os.getcwd(), name),
                 os.path.join(os.path.dirname(os.path.abspath(__file__)), name)):
        if os.path.exists(cand):
            return cand
    return None


def status_of(src, old, new, marker):
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
    print("  ⑰ /질문에 역사적 위치 · ⑱ 부정된 규칙 제거")
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
            ok = marker in src
            print(f"    {'✅' if ok else '□'} {tag} {label}")
        print("=" * 62)
        return 0

    print(f"\n  bot.py")
    todo = 0
    for tag, label, old, new, marker in SITES:
        st = status_of(src, old, new, marker)
        print(f"    {MARK[st]} {tag} {label} — {st}")
        if st == TODO:
            todo += 1
            if apply:
                src = src.replace(old, new, 1)

    if not apply:
        print("\n" + "=" * 62)
        if todo:
            print(f"  {todo}개를 고칠 수 있습니다.  적용:  python fix_ask_context.py --apply")
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
    print(f"  적용했습니다. 원본은 bot.py.bak")
    print("""
  봇을 재시작해야 반영됩니다.

  확인
    1. python fix_ask_context.py --verify
    2. 텔레그램에서  /질문 BTC 지금 펀딩 어때?
       → 답에 '몇 분위' 같은 말이 나오면 붙은 것입니다.

  ⑰ 는 percentile_ctx.py 와 percentile_ctx.json 이 같은 폴더에
  있어야 동작합니다. 없으면 조용히 건너뛰고 봇은 그대로 돕니다.
      python percentile_ctx.py --build""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
