"""fix_stop_floor.py — ⑭ 손절이 노이즈 안에 있는 신호가 아직 넷 남았다

    cd /path/to/auto
    python fix_stop_floor.py           # 미리보기
    python fix_stop_floor.py --apply   # 적용 (원본은 .bak)
    python fix_stop_floor.py --verify  # 반영됐나

**backtest.py 만 고칩니다.** ⑧(fix_signal_geometry)이 먼저 적용돼 있어야
합니다 — 여기서 그때 만든 ATR 헬퍼를 씁니다.

무엇이 문제인가
──────────────
⑧은 중기롱·중기숏의 손절만 노이즈 밖으로 뺐다. 나머지 넷은 그대로다.
그중 FIB_SHORT 가 교과서적으로 망가져 있다:

    entry = price                       # price ≈ fib_618 (0.5% 이내에서 발화)
    sl    = round_px(fib_618 * 1.005)   # → 손절폭이 0.5%

  실측(20종·730일, 데이터 정상화 후):

      FIB_SHORT   47건 · 승률 12.8% · -1.166R/거래

  검산이 딱 맞는다. 손절폭 0.5%면 수수료·슬리피지가
  (0.0006 + 0.0011) / 0.005 = **0.34R**을 먹는다.
  거의 다 손절이므로 -1.0R, 합쳐서 -1.34R. 실제 -1.166R.
  **전략이 틀린 게 아니라 손절 자리가 산수적으로 불가능한 것이다.**

  같은 FIB_LONG 은 -0.069R 로 멀쩡하다. 손절을 fib_382(훨씬 아래)에
  두기 때문이다. 이 비대칭이 원인을 그대로 보여준다.

무엇을 하나
──────────
각 신호의 손절 논리는 그대로 두고, **바닥만 깐다**:

    롱:  sl = min(원래 손절, price - 1.5 × ATR14)
    숏:  sl = max(원래 손절, price + 1.5 × ATR14)

  원래 손절이 이미 노이즈 밖이면 아무것도 안 바뀐다(FIB_LONG,
  SMA_* 대부분). 노이즈 안이면 밖으로 밀린다(FIB_SHORT).
  손절을 **좁히는 일은 절대 없다.**

  대상: FIB_LONG · FIB_SHORT · SMA_LONG · SMA_SHORT · 천사 · 악마

되돌리려면
─────────
    ATR_STOP_MULT = 0.0    ← ⑧이 만든 토글. ⑧⑭가 함께 꺼집니다.
    또는 backtest.py.bak
"""

import ast
import os
import shutil
import sys

TODO, DONE, GONE = "적용 예정", "이미 적용됨", "대상 없음"
MARK = {TODO: "□", DONE: "✅", GONE: "⚠️"}

FLOOR_SRC = '''

def _floor_below(price, row):
    """롱 손절이 이보다 가까우면 노이즈 안이다. min() 으로 씌운다.

    ATR을 못 구하면 +inf 를 돌려준다 — min() 에서 무시되므로 원래
    손절이 그대로 살아난다.
    """
    atr = _atr_of(row)
    if not ATR_STOP_MULT or atr is None:
        return float("inf")
    return price - ATR_STOP_MULT * atr


def _floor_above(price, row):
    """숏 쪽 대칭. 못 구하면 -inf 라 max() 에서 무시된다."""
    atr = _atr_of(row)
    if not ATR_STOP_MULT or atr is None:
        return float("-inf")
    return price + ATR_STOP_MULT * atr
'''

FLOOR_ANCHOR = "def _stop_below(price, level, row):"

# (번호, 이름, 원래 줄, 바꿀 줄, 이미 적용됐나 확인할 조각)
SITES = [
    ("⑭a", "피보 롱",
     "            sl   = round_px(fib_382 * 0.995)",
     "            sl   = round_px(min(fib_382 * 0.995, _floor_below(price, row)))"),
    ("⑭b", "피보 숏  ← -1.166R 짜리",
     "            sl   = round_px(fib_618 * 1.005)",
     "            sl   = round_px(max(fib_618 * 1.005, _floor_above(price, row)))"),
    ("⑭c", "SMA 골든크로스 롱",
     "                sl   = round_px(row['ma50'] * 0.99)",
     "                sl   = round_px(min(row['ma50'] * 0.99, _floor_below(price, row)))"),
    ("⑭d", "SMA 데드크로스 숏",
     "                sl   = round_px(row['ma50'] * 1.01)",
     "                sl   = round_px(max(row['ma50'] * 1.01, _floor_above(price, row)))"),
    ("⑭e", "천사 (장기 롱)",
     "                    sl   = round_px(w_sup * 0.985)",
     "                    sl   = round_px(min(w_sup * 0.985, _floor_below(price, row)))"),
    ("⑭f", "악마 (장기 숏)",
     "                    sl   = round_px(w_res * 1.015)",
     "                    sl   = round_px(max(w_res * 1.015, _floor_above(price, row)))"),
]


def patch_backtest(src):
    items = []

    if "_atr_of(" not in src:
        # ⑧이 안 들어간 파일. 여기서 뭘 해도 NameError 가 난다.
        return src, [("⑭", "⑧(fix_signal_geometry)을 먼저 적용하십시오", GONE)]

    if "def _floor_below(" in src:
        items.append(("⑭0", "노이즈 바닥 헬퍼", DONE))
    elif FLOOR_ANCHOR in src:
        src = src.replace(FLOOR_ANCHOR, FLOOR_SRC.strip("\n") + "\n\n\n" + FLOOR_ANCHOR, 1)
        items.append(("⑭0", "노이즈 바닥 헬퍼", TODO))
    else:
        items.append(("⑭0", "노이즈 바닥 헬퍼", GONE))

    for num, label, old, new in SITES:
        if new in src:
            items.append((num, label, DONE))
        elif old in src:
            src = src.replace(old, new, 1)
            items.append((num, label, TODO))
        else:
            items.append((num, label, GONE))

    return src, items


def process(path, apply):
    if not os.path.exists(path):
        print(f"\n  {path} — 파일이 없습니다. 봇 폴더에서 실행하십시오.")
        return None, 0
    with open(path, encoding="utf-8") as f:
        src = f.read()
    new, items = patch_backtest(src)
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
    print("  실제 로드되는 backtest 확인")
    print("=" * 62)
    ok = True
    try:
        import backtest
        print(f"\n  backtest.__file__  {getattr(backtest, '__file__', '?')}")
        checks = [("_floor_below 정의 (⑭)", hasattr(backtest, "_floor_below")),
                  ("_floor_above 정의 (⑭)", hasattr(backtest, "_floor_above"))]
        import inspect
        s = inspect.getsource(backtest.detect_signals_vectorized)
    except Exception as e:
        print(f"\n  import 실패: {type(e).__name__}: {e}")
        print("  (봇이 쓰는 파이썬/가상환경으로 돌리십시오)\n")
        print("  대신 파일 내용만으로 확인합니다:")
        if not os.path.exists("backtest.py"):
            print("    backtest.py 가 이 폴더에 없습니다.")
            return 1
        s = open("backtest.py", encoding="utf-8").read()
        checks = [("_floor_below 정의 (⑭)", "def _floor_below(" in s),
                  ("_floor_above 정의 (⑭)", "def _floor_above(" in s)]
    for num, label, _old, new in SITES:
        checks.append((f"{label} ({num})", new.strip() in s))
    for label, good in checks:
        ok = ok and good
        print(f"  {'✅' if good else '❌'} {label}")
    print("=" * 62)
    if not ok:
        print("  python fix_stop_floor.py --apply  를 이 폴더에서 돌리십시오.")
    return 0 if ok else 1


def main(argv):
    if "--verify" in argv:
        return verify()
    apply = "--apply" in argv
    print("=" * 62)
    print("  ⑭ 손절 바닥 (backtest.py 전용)")
    print("=" * 62)
    print(f"  대상 폴더: {os.getcwd()}")
    if not apply:
        print("\n  미리보기입니다. 실제로 바꾸려면 --apply 를 붙이십시오.")

    ok, todo = process("backtest.py", apply)

    print("\n" + "=" * 62)
    if ok is False:
        print("  실패했습니다. 원본은 그대로입니다.")
        return 1
    if ok is None:
        return 1
    if todo:
        print(f"  아직 적용되지 않은 수정이 {todo}건 있습니다.")
        print("  python fix_stop_floor.py --apply")
        return 0
    print("""  적용돼 있습니다.

  ⑬(429 대응)을 먼저 넣고 두 번 돌려 결과가 같은 걸 확인한 뒤에
  이걸 재십시오. 안 그러면 좋아진 게 ⑭ 덕인지 429가 덜 나서인지
  구분이 안 됩니다.

      python run_backtest.py --all 20 2y

  볼 것
    · FIB_SHORT 가 -1.166R 에서 얼마나 올라왔나  ← ⑭의 직접 대상
    · 나머지 타입의 건수는 그대로여야 한다 (손절만 바뀌지 발화
      조건은 안 건드렸다)""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
