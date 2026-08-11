"""fix_folder_audit.py — ㉙ 봇 폴더를 다 읽고 찾은 것 (4개 파일)

    cd /path/to/jaybot
    python fix_folder_audit.py           # 미리보기
    python fix_folder_audit.py --apply   # 적용 (원본은 각각 .bak)
    python fix_folder_audit.py --verify  # 반영됐나

지금까지의 패치와 달리 **bot.py 가 아닌 파일들**을 고칩니다.
paper_universe.py · backtest.py · cvd_flow.py · chart_render.py

────────────────────────────────────────────────────────────

㉙a paper_universe.py — 블록리스트가 새 종목을 못 따라갔다

  이 파일 첫머리에 이미 이렇게 적혀 있습니다.

      "비크립토(토큰화 주식·ETF·원자재)는 크립토 전략 검증에
       섞이면 안 되므로 제외."

  설계 의도가 명확한데 목록이 낡았습니다. TSLA·NVDA·XAU 는 들어
  있는데 **SKHYNIX·SAMSUNG·RKLB·AAOI·SPCX·DRAM 이 없습니다.**
  스캔 캐시에서 확인한 거래대금이 SPCX 2.5억, SKHYNIX 1억 달러라
  유동성 필터를 그냥 통과합니다.

  이게 ㉔에서 로그를 덮던 그 종목들입니다. ㉔·㉘은 "부를 수 있나"를
  고쳤고, 여기는 **"애초에 대상이어야 하나"** 입니다. 답은 아니오고,
  이 파일이 이미 그렇게 정해 뒀습니다.

  새 종목이 상장되면 config.py 의 PAPER_EXCLUDE 에 덧붙이면 됩니다.

㉙b backtest.py — 갭으로 뚫린 손절을 손절선에 체결한 걸로 친다

  `paper_trader._stop_price()` 에는 이 주석과 함께 고쳐져 있습니다.

      "갭하락한 봉에서 롱을 손절선(시가보다 위)에 청산해 실제보다
       유리하게(심하면 이익으로) 기록되는 버그"

  같은 버그가 `backtest.evaluate_trade()` 에 **그대로 남아 있습니다.**
  한쪽만 고쳐졌습니다.

  ⚠️ 이게 지금까지 잰 70가지에 무슨 뜻인가 — 정확히 말합니다.

     signal_bench.make_trade() 가 이 함수를 부르므로 **70가지 측정이
     전부 이 함수를 지났습니다.** 손실이 실제보다 덜 나쁘게 기록됐습니다.

     그런데 **무작위 진입 기준선도 똑같이 이 함수를 지납니다.**
     양쪽이 같은 크기로 후해졌으므로 **초과(신호 − 무작위)는 거의
     안 움직입니다.** "우위가 없다"는 결론은 그대로입니다.

     달라지는 건 절대값입니다 — "무작위 롱 −0.048R" 같은 숫자는
     실제보다 조금 후한 값이었습니다. 그리고 봇의 `/백테스트` 가
     내놓는 성적도 그만큼 후했습니다.

  TP1 이후 본전 스탑도 같이 고칩니다. 갭으로 본전선을 뚫으면
  잔량이 **손실**인데 지금은 0으로 칩니다.

㉙c cvd_flow.py — 알림이 방향을 말한다

      → 단기 반등 가능 / 단기 하락 압력

  `cvd_bench.py` 로 CVD 엇갈림을 쟀습니다. 약세 엇갈림 → 숏은
  1,252건 −0.022R 로 무작위 숏(−0.008R)보다 못했습니다.

  다만 정확히 말합니다 — **우리가 잰 건 일봉·20일 창이고, 이
  알림은 4시간 창입니다.** 같은 것이 아닙니다. 그래서 알림을
  끄지는 않습니다.

  대신 방향 문장만 뺍니다. 근거가 있는 건 "가격과 자금흐름이
  어긋났다"는 사실까지고, "그래서 반등한다"는 그 너머입니다.
  게다가 시간대별 비용을 재 보니 1시간봉은 거래당 0.142R,
  일봉은 0.023R 였습니다 — 짧은 쪽이 더 큰 우위를 요구합니다.

  알림 자체를 끄고 싶으면 bot.py 의 schedule 줄을 지우면 됩니다.

㉙d chart_render.py — 한글 폰트가 스타일에 덮인다

    UserWarning: Glyph 45824 (\N{HANGUL SYLLABLE DAE}) missing from font

  파일 첫머리에서 맑은 고딕을 찾아 `plt.rcParams` 에 넣는데,
  그 아래에서 `mpf.make_mpf_style(base_mpf_style="nightclouds")` 가
  **자기 rcParams 로 덮어씁니다.** 폰트 탐색은 제대로 돌고 있었고,
  설정이 나중에 지워졌습니다.

  스타일의 rc 에 같은 폰트를 다시 넣습니다.

되돌리려면
─────────
    각 파일의 .bak 을 덮어쓰면 됩니다.
"""

import ast
import os
import shutil
import sys

TODO, DONE, GONE = "적용 예정", "이미 적용됨", "대상 없음"
MARK = {TODO: "□", DONE: "✅", GONE: "⚠️"}


# ── ㉙a 비크립토 블록리스트 ──
EXCL_OLD = '''    "SMCI", "AVGO", "QCOM", "CRCL",'''

EXCL_NEW = '''    "SMCI", "AVGO", "QCOM", "CRCL",
    # 2026-08 확인 — 스캔 캐시에 있는데 목록에 없던 것들.
    # 거래대금이 SPCX 2.5억·SKHYNIX 1억 달러라 유동성 필터를
    # 그냥 통과했다. 장 시간이 있고 주말에 갭이 생기는 상품이라
    # 24시간 도는 코인과 같은 규칙으로 재면 안 된다.
    "SKHYNIX", "SAMSUNG", "RKLB", "AAOI", "SPCX", "DRAM",'''


# ── ㉙b 갭 통과 손절 ──
GAP_OLD = '''        if is_long:
            # 보수적: 같은 봉에서 스탑+TP 동시 터치 시 스탑 우선
            if l <= stop:
                if tp1_hit:
                    exits = [(tp1, 0.5), (stop, 0.5)]
                    return {"result":"TP1+BE", "r": round(realized - _cost_r(entry, exits, unit_risk), 2), "exit": stop}
                gross = (stop - entry) / unit_risk
                return {"result":"SL", "r":round(gross - _cost_r(entry, [(stop, 1.0)], unit_risk), 2), "exit": stop}'''

GAP_NEW = '''        if is_long:
            # 보수적: 같은 봉에서 스탑+TP 동시 터치 시 스탑 우선
            if l <= stop:
                # 갭으로 시가가 스탑 너머면 **시가에 체결된다.**
                # 스탑 값 그대로 쓰면 갭하락한 봉에서 손절을 시가보다
                # 위에서 청산한 걸로 쳐서 실제보다 유리하게(심하면
                # 이익으로) 기록된다. paper_trader._stop_price() 에는
                # 이미 고쳐져 있었고 여기만 남아 있었다.
                stop_fill = min(stop, float(row['open']))
                if tp1_hit:
                    # 본전 스탑도 갭을 맞는다. 뚫렸으면 잔량은
                    # 0 이 아니라 손실이다.
                    exits = [(tp1, 0.5), (stop_fill, 0.5)]
                    gross = realized + 0.5 * (stop_fill - entry) / unit_risk
                    return {"result":"TP1+BE", "r": round(gross - _cost_r(entry, exits, unit_risk), 2), "exit": stop_fill}
                gross = (stop_fill - entry) / unit_risk
                return {"result":"SL", "r":round(gross - _cost_r(entry, [(stop_fill, 1.0)], unit_risk), 2), "exit": stop_fill}'''

GAP_S_OLD = '''            if h >= stop:
                if tp1_hit:
                    return {"result":"TP1+BE", "r": round(realized - _cost_r(entry, [(tp1, 0.5), (stop, 0.5)], unit_risk), 2), "exit": stop}
                gross = (entry - stop) / unit_risk
                return {"result":"SL", "r":round(gross - _cost_r(entry, [(stop, 1.0)], unit_risk), 2), "exit": stop}'''

GAP_S_NEW = '''            if h >= stop:
                # 숏은 갭상승이 같은 문제다. 시가가 스탑 위면 시가 체결.
                stop_fill = max(stop, float(row['open']))
                if tp1_hit:
                    gross = realized + 0.5 * (entry - stop_fill) / unit_risk
                    return {"result":"TP1+BE", "r": round(gross - _cost_r(entry, [(tp1, 0.5), (stop_fill, 0.5)], unit_risk), 2), "exit": stop_fill}
                gross = (entry - stop_fill) / unit_risk
                return {"result":"SL", "r":round(gross - _cost_r(entry, [(stop_fill, 1.0)], unit_risk), 2), "exit": stop_fill}'''


# ── ㉙c CVD 알림에서 방향 빼기 ──
CVD_OLD = '''        emoji = "📉" if div['type'] == 'bearish' else "📈"
        action = "단기 반등 가능" if div['type'] == 'bullish' else "단기 하락 압력"'''

CVD_NEW = '''        emoji = "📉" if div['type'] == 'bearish' else "📈"
        # 방향은 말하지 않는다.
        #
        # CVD 엇갈림을 쟀다 — 약세 엇갈림 → 숏 1,252건 −0.022R,
        # 무작위 숏 −0.008R 보다 못했다. 다만 그건 일봉·20일 창이고
        # 이 알림은 4시간 창이라 같은 것은 아니다. 그래서 알림을
        # 끄지는 않고, 근거가 있는 데까지만 말한다.
        #
        # 근거가 있는 것: "가격과 자금흐름이 어긋났다"
        # 근거가 없는 것: "그래서 반등한다"'''

CVD_MSG_OLD = '''            f"💡 다이버전스 = 가격과 자금흐름 불일치\\n"
            f"   → {action}\\n"
            f"   → 단독 진입 X, 다른 지표와 같이 확인\\n"'''

CVD_MSG_NEW = '''            f"💡 다이버전스 = 가격과 자금흐름 불일치\\n"
            f"   → 여기까지가 사실입니다. 방향은 재 봤지만 안 나왔습니다\\n"
            f"   → 단독 진입 X, 다른 지표와 같이 확인\\n"'''


# ── ㉙d 차트 한글 폰트 ──
FONT_OLD = '''        rc={
            "axes.labelcolor": C_TEXT, "xtick.color": C_TEXT,
            "ytick.color": C_TEXT, "text.color": C_TEXT,
            "axes.linewidth": 0.6, "font.size": 10,
        },'''

FONT_NEW = '''        rc={
            "axes.labelcolor": C_TEXT, "xtick.color": C_TEXT,
            "ytick.color": C_TEXT, "text.color": C_TEXT,
            "axes.linewidth": 0.6, "font.size": 10,
            # 위에서 맑은 고딕을 rcParams 에 넣어 뒀는데
            # make_mpf_style(base_mpf_style=...) 이 자기 rcParams 로
            # 덮어쓴다. 폰트 탐색은 제대로 돌고 있었고 설정이 나중에
            # 지워진 것이라, 여기서 다시 넣는다.
            **({"font.family": _KR_FONT} if _KR_FONT else {}),
        },'''


# (파일, 태그, 설명, 옛것, 새것, 표식)
SITES = [
    ("paper_universe.py", "㉙a", "비크립토 블록리스트 갱신",
     EXCL_OLD, EXCL_NEW, '"SKHYNIX", "SAMSUNG", "RKLB"'),
    ("backtest.py", "㉙b-1", "갭 통과 손절 (롱)",
     GAP_OLD, GAP_NEW, "stop_fill = min(stop, float(row['open']))"),
    ("backtest.py", "㉙b-2", "갭 통과 손절 (숏)",
     GAP_S_OLD, GAP_S_NEW, "stop_fill = max(stop, float(row['open']))"),
    ("cvd_flow.py", "㉙c-1", "알림에서 방향 빼기",
     CVD_OLD, CVD_NEW, "근거가 없는 것: \"그래서 반등한다\""),
    ("cvd_flow.py", "㉙c-2", "알림 문구",
     CVD_MSG_OLD, CVD_MSG_NEW, "방향은 재 봤지만 안 나왔습니다"),
    ("chart_render.py", "㉙d", "한글 폰트가 스타일에 덮임",
     FONT_OLD, FONT_NEW, '"font.family": _KR_FONT'),
]


def find(name):
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

    print("=" * 66)
    print("  ㉙ 봇 폴더를 다 읽고 찾은 것 — 4개 파일")
    print("=" * 66)
    print(f"  대상 폴더: {os.getcwd()}")

    files = {}
    for name in dict.fromkeys(s[0] for s in SITES):
        path = find(name)
        if path:
            with open(path, encoding="utf-8") as fp:
                files[name] = {"path": path, "src": fp.read(), "n": 0}
        else:
            files[name] = None

    missing = [n for n, v in files.items() if v is None]
    if missing:
        print(f"\n  못 찾은 파일: {', '.join(missing)}")
        print("  봇 폴더에서 실행하십시오.")
        if len(missing) == len(files):
            return 1

    todo = gone = 0
    cur = None
    for name, tag, label, old, new, marker in SITES:
        box = files.get(name)
        if box is None:
            continue
        if name != cur:
            print(f"\n  {name}")
            cur = name
        st = (DONE if marker in box["src"] else
              TODO if old in box["src"] else GONE)
        print(f"    {MARK[st]} {tag} {label} — {st}")
        if st == GONE:
            gone += 1
        if st == TODO:
            todo += 1
            if apply:
                box["src"] = box["src"].replace(old, new, 1)
                box["n"] += 1

    if verify:
        print("\n" + "=" * 66)
        return 0

    if not apply:
        print("\n" + "=" * 66)
        if todo:
            print(f"  {todo}개를 고칠 수 있습니다."
                  "  적용:  python fix_folder_audit.py --apply")
        else:
            print("  고칠 것이 없습니다.")
        return 0

    saved, refused = [], []
    for name, box in files.items():
        if not box or not box["n"]:
            continue
        # 문법이 깨진 파일을 저장하면 봇이 아예 안 뜬다.
        try:
            ast.parse(box["src"])
        except SyntaxError as e:
            refused.append(f"{name} ({e.lineno}행: {e.msg})")
            continue
        shutil.copy2(box["path"], box["path"] + ".bak")
        with open(box["path"], "w", encoding="utf-8") as fp:
            fp.write(box["src"])
        saved.append(f"{name} ({box['n']}곳)")

    print("\n" + "=" * 66)
    if refused:
        print("  ❌ 문법 오류라 저장하지 않은 파일: " + ", ".join(refused))
    if not saved:
        print("  바뀐 것이 없습니다.")
        return 1 if refused else 0

    print("  고쳤습니다: " + ", ".join(saved))
    print("  원본은 각각 .bak 로 남았습니다.")
    print("""
  봇을 재시작해야 반영됩니다.

  ㉙a 는 다음 페이퍼 스캔부터 그 여섯 종목을 아예 안 부릅니다.
  ㉙b 는 지금부터 돌리는 백테스트에만 적용됩니다 — 이미 나온
     숫자는 그만큼 후했습니다. 다시 재고 싶으면:

         python signal_bench.py 30 4y

     결론은 안 바뀔 것입니다. 신호와 무작위 기준선이 **같은
     함수를 지나서** 같은 크기로 후해졌기 때문입니다. 바뀌는 건
     절대값입니다.

  ㉙d 는 다음 차트부터 한글이 제대로 나옵니다.""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
