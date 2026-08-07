"""
fix_price_precision.py — 봇의 페이퍼 트레이딩이 멈춘 원인 두 가지를 고칩니다

    cd /path/to/auto
    python fix_price_precision.py          # 뭘 바꿀지 먼저 보여주기만
    python fix_price_precision.py --apply  # 실제로 적용 (원본은 .bak 로 백업)

무엇을 고치나
────────────

① analyze_timeframe()의 가격 반올림 (bot.py 15곳)

    current_price = round(close.iloc[-1], 2)
    recent_low    = round(low.tail(20).min(), 2)

  round(x, 2)의 절대 오차는 최대 0.005다. $0.018짜리 코인이면 **상대 오차가
  28%**다. 그 값으로 "레벨 0.5% 이내"를 따지면 판정이 반올림 잡음에 지배된다.

  실측: $0.018 코인에서 신호가 5,536번 떴는데, 반올림을 고치면 211번이다.
  **96%가 유령 신호**였다. $64,000 코인은 209 → 209로 그대로다 (비싼 코인엔
  영향이 없다는 정상 확인).

  봇에는 이미 round_px()가 있고 독스트링이 정확히 이 문제를 설명한다.
  다만 entry/sl 에만 쓰였고, 정작 레벨을 만드는 곳에는 안 쓰였다.

② detect_core_signal()의 레벨 근접 판정 기준 (bot.py 1곳)

    price = daily['current_price']       # ← 마감된 일봉 종가 (최대 24시간 전)

  get_ohlcv()가 v3.6부터 미완성 봉을 버리므로 이 값은 '어제 종가'다.
  그런데 paper_trader는 **실시간 시세**로 체결을 검사한다(MAX_LEVEL_GAP 1%).

  어제 종가로 신호를 내고 오늘 시세로 체결을 막으니, 하루 사이 1% 넘게
  움직이면 무조건 스킵된다 — 코인에선 거의 매일이다.

  실측(paper_trades.json): 9건 중 8건이 level_gap 스킵,
  레벨과의 괴리가 1.37% · 1.76% · 2.55% · 5.39% · 5.73% · 9.91% · 35.90% · 43.34%.

  레벨(sup_d/res_d) 자체는 마감봉으로 계속 구한다 — 레벨이 장중에 흔들리면
  같은 자리를 매번 새 자리로 착각하게 되기 때문이다. 바뀌는 건 '지금 어디
  있나'를 재는 기준뿐이다.

③ backtest.py의 같은 반올림 (44줄)

  백테스트는 기다리지 않고 표본을 얻는 유일한 길인데, 여기가 틀리면
  그 결과도 못 믿는다. R·통계 값(2자리가 맞다)은 건드리지 않는다.

되돌리려면
─────────
    각 파일 옆에 .bak 이 생깁니다. 그대로 덮어쓰면 원상복구됩니다.
"""

import ast
import os
import shutil
import sys

ROUND_PX_SRC = '''

def round_px(value):
    """가격 크기에 맞춘 반올림 (bot.round_px와 동일 규칙).

    고정 2자리 반올림은 $1 미만 알트에서 치명적이다. 절대 오차가 최대
    0.005인데, $0.018짜리 코인이면 상대 오차가 28%다. 그 값으로 '레벨
    0.5% 이내'를 따지면 판정이 반올림 잡음에 지배된다.
    """
    v = abs(value)
    if v >= 100:    nd = 1
    elif v >= 1:    nd = 3
    elif v >= 0.01: nd = 5
    else:           nd = 8
    return round(value, nd)'''

# ── bot.py: analyze_timeframe 안의 가격 반올림 ──
BOT_ROUNDING = [
    ("    ma20          = round(close.rolling(20).mean().iloc[-1], 2)",
     "    ma20          = round_px(close.rolling(20).mean().iloc[-1])"),
    ("    ma50          = round(close.rolling(50).mean().iloc[-1], 2)",
     "    ma50          = round_px(close.rolling(50).mean().iloc[-1])"),
    ("    current_price = round(close.iloc[-1], 2)",
     "    current_price = round_px(close.iloc[-1])"),
    ("        supertrend_val = round(st[-1], 2)",
     "        supertrend_val = round_px(st[-1])"),
    ("            ema200_val = round(ema200.iloc[-1], 2)",
     "            ema200_val = round_px(ema200.iloc[-1])"),
    ("    recent_high = round(high.tail(20).max(), 2)",
     "    recent_high = round_px(high.tail(20).max())"),
    ("    recent_low  = round(low.tail(20).min(), 2)",
     "    recent_low  = round_px(low.tail(20).min())"),
    ("    bb_upper = round(bb.bollinger_hband().iloc[-1], 2)",
     "    bb_upper = round_px(bb.bollinger_hband().iloc[-1])"),
    ("    bb_lower = round(bb.bollinger_lband().iloc[-1], 2)",
     "    bb_lower = round_px(bb.bollinger_lband().iloc[-1])"),
    ("    bb_mid   = round(bb.bollinger_mavg().iloc[-1], 2)",
     "    bb_mid   = round_px(bb.bollinger_mavg().iloc[-1])"),
    ("        mid_line      = round(intercept + slope*(n-1), 2)",
     "        mid_line      = round_px(intercept + slope*(n-1))"),
    ("        ch_width      = round((high.tail(n).max() - low.tail(n).min()) / 2, 2)",
     "        ch_width      = round_px((high.tail(n).max() - low.tail(n).min()) / 2)"),
    ("        channel_upper = round(mid_line + ch_width*0.6, 2)",
     "        channel_upper = round_px(mid_line + ch_width*0.6)"),
    ("        channel_lower = round(mid_line - ch_width*0.6, 2)",
     "        channel_lower = round_px(mid_line - ch_width*0.6)"),
    ("        mid_line      = round((recent_high + recent_low)/2, 2)",
     "        mid_line      = round_px((recent_high + recent_low)/2)"),
]

BOT_PRICE_OLD = """    price = daily['current_price']

    # ── 핵심 레벨 수집 ──"""

BOT_PRICE_NEW = """    # 레벨 근접 판정은 '지금 시세'로 해야 한다.
    #
    # daily['current_price']는 get_ohlcv()가 미완성 봉을 버리므로(v3.6)
    # **마감된 일봉 종가** = 최대 24시간 묵은 값이다. 반면 paper_trader는
    # 실시간 시세로 체결을 검사한다(MAX_LEVEL_GAP 1%). 둘이 어긋나면
    # 신호가 등록되자마자 level_gap으로 스킵된다 — 실제로 9건 중 8건이
    # 그렇게 죽었고 레벨과의 괴리가 1.4%~43%였다.
    #
    # 레벨(sup_d/res_d)은 마감봉으로 계속 구한다. 레벨이 장중에 흔들리면
    # 같은 자리를 매번 새 자리로 착각하게 되기 때문이다.
    live_price = get_current_price(symbol)
    price = live_price if live_price else daily['current_price']

    # ── 핵심 레벨 수집 ──"""

# ── backtest.py: 가격 반올림만 (R·통계는 2자리가 맞다) ──
BT_ANCHOR = "SLIPPAGE = 0.0005\nFEE_RATE = 0.0006"
BT_PRICE_HINT = ("entry", "sl", "tp1", "tp2", "tp3", "fib_", "w_sup", "w_res",
                 "ma50", "sup", "res", "exit", "last_close")
BT_STAT_HINT = ('"r"', "total_r", "avg_r", "avg_win", "avg_loss",
                "expectancy", "max_dd", "win_rate")


def patch_bot(src):
    """(새 소스, 변경 내역, 이미 적용됨?)"""
    changes = []
    done = 0

    for old, new in BOT_ROUNDING:
        if old in src:
            src = src.replace(old, new, 1)
            changes.append(f"반올림: {old.strip()[:52]}")
        elif new in src:
            done += 1

    if BOT_PRICE_OLD in src:
        src = src.replace(BOT_PRICE_OLD, BOT_PRICE_NEW, 1)
        changes.append("근접 판정 기준을 실시간 시세로")
    elif "live_price = get_current_price(symbol)" in src:
        done += 1

    return src, changes, done


def patch_backtest(src):
    import re
    changes = []
    if "def round_px(" not in src:
        if BT_ANCHOR not in src:
            return src, [], 0
        src = src.replace(BT_ANCHOR, BT_ANCHOR + ROUND_PX_SRC, 1)
        changes.append("round_px() 추가")

    lines = src.split("\n")
    n = 0
    for i, ln in enumerate(lines):
        if ", 2)" not in ln or "round(" not in ln:
            continue
        if any(h in ln for h in BT_STAT_HINT):
            new = re.sub(r'"exit":\s*round\(([^,]+), 2\)', r'"exit": round_px(\1)', ln)
            if new != ln:
                lines[i] = new
                n += 1
            continue
        if any(h in ln for h in BT_PRICE_HINT):
            new = re.sub(r"round\(([^;]+?), 2\)", r"round_px(\1)", ln)
            if new != ln:
                lines[i] = new
                n += 1
    if n:
        changes.append(f"가격 반올림 {n}줄")
    return "\n".join(lines), changes, 0


def process(path, fn, apply):
    if not os.path.exists(path):
        print(f"  [건너뜀] {path} 없음")
        return None

    with open(path, encoding="utf-8") as f:
        src = f.read()

    new, changes, already = fn(src)

    if not changes:
        state = "이미 적용됨" if already else "바꿀 것 없음"
        print(f"  [{state}] {path}")
        return True

    try:
        ast.parse(new)
    except SyntaxError as e:
        print(f"  [실패] {path} — 수정 후 문법 오류: {e}")
        return False

    print(f"  [{'적용' if apply else '적용 예정'}] {path} — {len(changes)}건")
    for c in changes[:4]:
        print(f"      · {c}")
    if len(changes) > 4:
        print(f"      · 외 {len(changes)-4}건")

    if apply:
        shutil.copy2(path, path + ".bak")
        with open(path, "w", encoding="utf-8") as f:
            f.write(new)
        print(f"      백업: {path}.bak")
    return True


def main(argv):
    apply = "--apply" in argv
    print("=" * 62)
    print("  가격 정밀도 · 근접 판정 기준 수정")
    print("=" * 62)
    if not apply:
        print("\n  미리보기입니다. 실제로 바꾸려면 --apply 를 붙이십시오.\n")

    ok = True
    for path, fn in (("bot.py", patch_bot), ("backtest.py", patch_backtest)):
        r = process(path, fn, apply)
        ok = ok and (r is not False)

    print("=" * 62)
    if not ok:
        print("  실패한 파일이 있습니다. 원본은 그대로입니다.")
        return 1
    if apply:
        print("""  적용 완료.

  다음에 할 일
    1. 페이퍼 기록을 비웁니다 — 지금 쌓인 9건은 버그가 만든 것입니다
         /페이퍼 리셋   (또는 paper_trades.json 삭제)
    2. 봇을 재시작합니다
    3. 백테스트로 표본을 즉시 확보합니다 (4주를 기다리지 않는 길)
         python backtest.py""")
    else:
        print("  python fix_price_precision.py --apply  로 실제 적용")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
