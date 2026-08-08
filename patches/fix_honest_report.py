"""fix_honest_report.py — ⑮ 확정 손실 신호를 페이퍼에서 뺀다 · ⑯ 리포트 문구

    cd /path/to/auto
    python fix_honest_report.py           # 미리보기
    python fix_honest_report.py --apply   # 적용 (원본은 .bak)
    python fix_honest_report.py --verify  # 반영됐나

paper_trader.py 와 backtest.py 를 고칩니다.

────────────────────────────────────────────────────────────

⑮ 중기숏은 무작위 진입보다 **유의하게** 나쁘다

    초과 -0.280R   95% 신뢰구간 [-0.550, -0.010]

  구간이 0을 걸치지 않는다. 측정한 45가지 중 통계적으로 확정된 것은
  이것 하나뿐이고, 하필 **나쁜 쪽으로** 확정됐다.

  "20일 고점 ±0.5% + RSI 65 이상"에서 숏을 치는 건 아무 날이나 숏
  치는 것보다 확실히 못하다. 페이퍼가 이걸 계속 잡으면 기록이
  오염되고, 나중에 "페이퍼가 마이너스네"의 원인을 또 찾게 된다.

  MUTED_TYPES 에서 빼면 다시 잡는다. 지우는 게 아니라 끄는 것이다.

⑯ 리포트가 "기대값 +면 자동매매 후보"라고 말한다

    2단계 자동매매는 여기서 기대값 +인 타입만 화이트리스트

  이 문장이 위험하다. 실제로 이 기준을 통과한 SMA_SHORT(+0.223R,
  123건)를 따로 재 보니 이랬다:

      전반기(BTC +86%)  -0.004R      후반기(BTC -45%)  +0.401R
      같은 기간 무작위 숏도 +0.103R

  기대값이 +인 것은 **그 시기에 숏이었기 때문**이었다. 리포트만 보고
  화이트리스트를 만들면 방향 베팅에 실돈이 들어간다.

  문구를 사실대로 바꾸고, 무엇을 더 해야 하는지 가리킨다.

되돌리려면
─────────
    각 파일 옆 .bak 을 덮어쓰면 됩니다.
"""

import ast
import os
import shutil
import sys

TODO, DONE, GONE = "적용 예정", "이미 적용됨", "대상 없음"
MARK = {TODO: "□", DONE: "✅", GONE: "⚠️"}

# ── ⑮ paper_trader.py ──
PT_CONST_OLD = "DEDUPE_HOURS    = 12       # 같은 코인·같은 레벨 시그널 재등록 방지 시간"
PT_CONST_NEW = '''DEDUPE_HOURS    = 12       # 같은 코인·같은 레벨 시그널 재등록 방지 시간

# 여기 든 타입은 기록하지 않는다.
#
# MID_SHORT(중기숏)는 같은 코인·같은 기간에 **아무 날이나** 숏 친 것과
# 견줬을 때 초과 -0.280R, 95% 구간 [-0.550, -0.010] 이었다. 구간이 0을
# 걸치지 않는다 — 측정한 45가지 중 통계적으로 확정된 유일한 결과이고
# 하필 나쁜 쪽이다. 자세한 것은 FINDINGS.md.
#
# 지우는 게 아니라 끄는 것이다. 이 집합에서 빼면 다시 잡는다.
MUTED_TYPES = {"MID_SHORT"}'''

PT_GATE_OLD = """            if not signal_type or None in (entry, sl, tp1, tp2):
                continue"""
PT_GATE_NEW = """            if not signal_type or None in (entry, sl, tp1, tp2):
                continue
            if signal_type in MUTED_TYPES:
                continue"""

# ── ⑯ backtest.py ──
BT_LINE_OLD = '''        "<i>2단계 자동매매는 여기서 기대값 +인 타입만 화이트리스트</i>",'''
BT_LINE_NEW = '''        "<i>⚠️ 기대값이 +라고 자동매매 후보가 아닙니다.</i>",
        "<i>그 성적이 신호 덕인지 그 시기 방향(롱/숏) 덕인지 여기서는</i>",
        "<i>구분되지 않습니다. 실제로 +0.223R 로 1등이던 타입을 따로</i>",
        "<i>재 보니 같은 기간 무작위 숏도 +0.103R 이었습니다.</i>",
        "<i>diag_baseline.py 로 무작위 진입을 이긴 것만 후보입니다.</i>",'''


def patch_paper(src):
    items = []
    for num, label, old, new, probe in (
        ("⑮a", "MUTED_TYPES 상수", PT_CONST_OLD, PT_CONST_NEW, "MUTED_TYPES = {"),
        ("⑮b", "기록 전 걸러내기", PT_GATE_OLD, PT_GATE_NEW, "in MUTED_TYPES:"),
    ):
        if probe in src:
            items.append((num, label, DONE))
        elif old in src:
            src = src.replace(old, new, 1)
            items.append((num, label, TODO))
        else:
            items.append((num, label, GONE))
    return src, items


def patch_backtest(src):
    if "무작위 진입을 이긴 것만 후보" in src:
        return src, [("⑯", "리포트 문구", DONE)]
    if BT_LINE_OLD in src:
        return src.replace(BT_LINE_OLD, BT_LINE_NEW, 1), [("⑯", "리포트 문구", TODO)]
    return src, [("⑯", "리포트 문구", GONE)]


def process(path, fn, apply):
    if not os.path.exists(path):
        print(f"\n  {path} — 파일이 없습니다. 봇 폴더에서 실행하십시오.")
        return None, 0
    with open(path, encoding="utf-8") as f:
        src = f.read()
    new, items = fn(src)
    todo = [i for i in items if i[2] == TODO]

    print(f"\n  {path}")
    for num, label, state in items:
        print(f"    {MARK[state]} {num} {label} — "
              f"{'적용' if (state == TODO and apply) else state}")

    if not todo:
        return True, 0
    try:
        ast.parse(new)
    except SyntaxError as e:
        print(f"    [실패] 수정 후 문법 오류: {e} — 원본은 그대로 둡니다")
        return False, len(todo)
    if apply:
        shutil.copy2(path, path + ".bak")
        with open(path, "w", encoding="utf-8") as f:
            f.write(new)
        print(f"    백업: {path}.bak")
        return True, 0
    return True, len(todo)


def verify():
    print("=" * 62)
    print("  실제 로드되는 모듈 확인")
    print("=" * 62)
    ok = True
    for label, mod_name, check in (
        ("⑮ 중기숏 차단", "paper_trader",
         lambda m: "MID_SHORT" in getattr(m, "MUTED_TYPES", set())),
        ("⑯ 리포트 문구", "backtest",
         lambda m: "무작위 진입을 이긴 것만 후보"
                   in __import__("inspect").getsource(m.format_multi_backtest)),
    ):
        try:
            m = __import__(mod_name)
            good = bool(check(m))
        except Exception as e:
            good = False
            print(f"  ⚠️ {mod_name} 확인 실패: {type(e).__name__}: {e}")
        ok = ok and good
        print(f"  {'✅' if good else '❌'} {label}")
    print("=" * 62)
    if not ok:
        print("  python fix_honest_report.py --apply  를 이 폴더에서 돌리십시오.")
    return 0 if ok else 1


def main(argv):
    if "--verify" in argv:
        return verify()
    apply = "--apply" in argv
    print("=" * 62)
    print("  ⑮ 확정 손실 신호 차단 · ⑯ 리포트 문구")
    print("=" * 62)
    print(f"  대상 폴더: {os.getcwd()}")
    if not apply:
        print("\n  미리보기입니다. 실제로 바꾸려면 --apply 를 붙이십시오.")

    ok, todo = True, 0
    for path, fn in (("paper_trader.py", patch_paper), ("backtest.py", patch_backtest)):
        r, n = process(path, fn, apply)
        ok = ok and (r is not False)
        todo += n

    print("\n" + "=" * 62)
    if not ok:
        print("  실패한 파일이 있습니다. 원본은 그대로입니다.")
        return 1
    if todo:
        print(f"  아직 적용되지 않은 수정이 {todo}건 있습니다.")
        print("  python fix_honest_report.py --apply")
        return 0
    print("""  적용돼 있습니다. 봇을 재시작해야 반영됩니다.

  중기숏을 다시 켜려면 paper_trader.py 의
      MUTED_TYPES = {"MID_SHORT"}   →   MUTED_TYPES = set()""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
