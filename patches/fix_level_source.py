"""fix_level_source.py — ⑩ 지지·저항을 '20봉 극값'에서 '여러 번 닿은 자리'로

    cd /path/to/auto
    python fix_level_source.py           # 미리보기
    python fix_level_source.py --apply   # 적용 (원본은 .bak)
    python fix_level_source.py --verify  # 반영됐나

**backtest.py 만 고칩니다.** level_map.py 와 console.py 가 같은 폴더에
있어야 합니다(이미 있습니다).

────────────────────────────────────────────────────────────

왜

  20종 × 730일 = 14,600 코인일 동안 실제 봇이 낼 수 있는 신호가
  **25건** 나왔다. 중기숏 24 · 중기롱 1 · 천사 0 · 악마 0.
  코인 하나당 2년에 1.25건이다. 우위 이전에 **거래할 게 없다.**

  원인은 레벨 정의다:

      sup = row['recent_low']    # 최근 20봉 최저가
      res = row['recent_high']   # 최근 20봉 최고가

  이건 '레벨'이 아니라 '20봉 극값'이다. 두 가지가 동시에 잘못된다.

  1. 발화 조건이 '20일 최저점 ±0.5%'가 된다. 가격이 20일 신저가
     근처에 있는 날은 드물다 — 그래서 25건이다.

  2. 한 번 스친 꼬리에도 잡힌다. 세 번 닿고 세 번 튄 자리와,
     한 번 찍고 만 꼬리를 구분하지 못한다. 앞엣것이 지지고
     뒤엣것은 잡음인데 봇에는 그 구분이 없다.

무엇을 하나

  level_map.build_level_map() 이 하는 걸 그대로 쓴다:
    프랙탈 스윙 고/저점을 뽑고 → 0.6% 이내끼리 묶어 하나의 레벨로 →
    현재가 아래 가장 가까운 것이 지지 1차, 위가 저항 1차.

  '20일 신저가'일 필요가 없어진다. 과거에 여러 번 닿았던 자리에
  다시 오면 그게 신호다. 그게 원래 이 전략이 하려던 것이다.

미래를 보지 않게

  프랙탈은 좌우 3봉을 봐야 확정된다. 인덱스 j인 스윙은 j+3 봉이
  마감돼야 알 수 있다. i번째 봉에서는 **j+3 <= i 인 스윙만** 쓴다.
  이걸 안 지키면 백테스트가 미래를 보고 레벨을 그린다.

  스윙 자체는 함수 진입 시 한 번만 계산하고, 봉마다 '그때까지 확정된
  것'만 걸러 쓴다. 봉마다 다시 계산하면 O(n²)가 된다.

되돌리려면

    LEVEL_SOURCE = "rolling20"     ← 파일 위 토글 한 줄

  둘을 같은 조건으로 번갈아 돌려 비교하는 게 이 패치의 목적이다.
  숫자가 좋아진 걸 보고 나서 bot.py 로 옮긴다.
"""

import ast
import os
import re
import shutil
import sys

TODO, DONE, GONE = "적용 예정", "이미 적용됨", "대상 없음"
MARK = {TODO: "□", DONE: "✅", GONE: "⚠️"}

TOGGLE = '''
# ── ⑩ 레벨 소스 ──
# "rolling20" = 최근 20봉 극값 (예전). "levelmap" = 여러 번 닿은 자리를
# 묶어 만든 실제 레벨 (level_map.py).
#
# 20봉 극값은 '20일 신저가 ±0.5%'에서만 발화해서, 20종 730일에 신호가
# 25건밖에 안 나왔다. A/B 비교하려면 이 값만 바꿔 두 번 돌리면 된다.
LEVEL_SOURCE = "levelmap"
'''

HELPERS = '''

import bisect      # 아래 레벨 조회에서 씀 (쓰는 자리 옆에 둔다)


def _lm_pivots_of(df):
    """스윙 고/저점을 **한 번만** 계산해 둔다. 봉마다 다시 하면 O(n^2)다.

    반환 (level_map, 인덱스순 스윙, 인덱스 배열, 캐시) 또는 None.
    """
    try:
        import level_map as _lm
    except Exception as e:
        print(f"[레벨] level_map 을 못 불러 20봉 극값으로 갑니다: {e}")
        return None
    try:
        sw_h, sw_l = _lm.find_pivots(list(df['high']), list(df['low']),
                                     _lm.DEFAULT_PIVOT_WIDTH)
    except Exception as e:
        print(f"[레벨] 스윙 계산 실패, 20봉 극값으로 갑니다: {e}")
        return None
    pivots = sorted(sw_h + sw_l, key=lambda p: p[0])
    return _lm, pivots, [p[0] for p in pivots], {}


def _lm_levels(prep, i, price):
    """i번째 봉 시점의 지지 1차 / 저항 1차. 없으면 (None, None).

    미래 차단: 프랙탈은 좌우 width봉을 봐야 확정된다. 인덱스 j인 스윙은
    j+width 봉이 마감돼야 알 수 있으므로 j+width <= i 인 것만 쓴다.

    속도: 클러스터 결과는 '확정된 스윙 개수'에만 달렸고, 스윙은 몇 봉에
    하나씩만 늘어난다. 개수를 키로 캐시하면 봉마다 다시 묶지 않아도 된다
    (730봉에 클러스터 호출 ~200회). 캐시는 코인 하나 처리 동안만 산다.
    """
    if prep is None:
        return None, None
    lm, pivots, idxs, cache = prep
    n = bisect.bisect_right(idxs, i - lm.DEFAULT_PIVOT_WIDTH)
    if n < 4:                   # 레벨이라 부를 만한 최소한
        return None, None
    prices = cache.get(n)
    if prices is None:
        levels = lm.cluster_levels(pivots[:n], i + 1, lm.DEFAULT_CLUSTER_TOL)
        prices = sorted(l["price"] for l in levels)
        cache[n] = prices
    lo = bisect.bisect_left(prices, price)
    hi = bisect.bisect_right(prices, price)
    return (prices[lo - 1] if lo > 0 else None,
            prices[hi] if hi < len(prices) else None)
'''

HELPER_ANCHOR = "def detect_signals_vectorized(df_daily, df_weekly=None, df_h4=None):"

PREP_OLD = """    signals = []

    for i in range(50, len(df_daily)):"""

PREP_NEW = """    signals = []
    # 스윙은 한 번만 계산한다. 봉마다 다시 뽑으면 O(n^2)가 된다.
    lm_prep = _lm_pivots_of(df_daily) if LEVEL_SOURCE == "levelmap" else None

    for i in range(50, len(df_daily)):"""

LEVEL_OLD = """        sup = row['recent_low']
        res = row['recent_high']
        rsi = row['rsi']"""

LEVEL_NEW = """        rsi = row['rsi']

        # 레벨 소스. 20봉 극값은 '20일 신저가 ±0.5%'에서만 발화해
        # 신호가 거의 안 난다. levelmap 은 여러 번 닿은 자리를 쓴다.
        # 레벨을 못 만든 구간(초반 등)에서는 예전 방식으로 돌아간다.
        if LEVEL_SOURCE == "levelmap":
            _s, _r = _lm_levels(lm_prep, i, price)
            sup = _s if _s is not None else row['recent_low']
            res = _r if _r is not None else row['recent_high']
        else:
            sup = row['recent_low']
            res = row['recent_high']"""


def patch_backtest(src):
    items = []

    # ⑩a 토글
    if "LEVEL_SOURCE" in src:
        items.append(("⑩a", "레벨 소스 토글", DONE))
    elif re.search(r"^RSI_OVERBOUGHT\s*=.*$", src, re.M):
        src = re.sub(r"^(RSI_OVERBOUGHT\s*=.*)$", r"\1\n" + TOGGLE.strip("\n"),
                     src, count=1, flags=re.M)
        items.append(("⑩a", "레벨 소스 토글", TODO))
    else:
        items.append(("⑩a", "레벨 소스 토글", GONE))

    # ⑩b 도우미
    if "def _lm_levels(" in src:
        items.append(("⑩b", "레벨 계산 도우미 (미래 차단 포함)", DONE))
    elif HELPER_ANCHOR in src:
        src = src.replace(HELPER_ANCHOR, HELPERS.strip("\n") + "\n\n\n" + HELPER_ANCHOR, 1)
        items.append(("⑩b", "레벨 계산 도우미 (미래 차단 포함)", TODO))
    else:
        items.append(("⑩b", "레벨 계산 도우미", GONE))

    # ⑩c 스윙 1회 계산
    if "lm_prep = _lm_pivots_of" in src:
        items.append(("⑩c", "스윙 1회 계산", DONE))
    elif PREP_OLD in src:
        src = src.replace(PREP_OLD, PREP_NEW, 1)
        items.append(("⑩c", "스윙 1회 계산", TODO))
    else:
        items.append(("⑩c", "스윙 1회 계산", GONE))

    # ⑩d 레벨 소스 교체
    if 'if LEVEL_SOURCE == "levelmap":' in src:
        items.append(("⑩d", "지지·저항을 level_map 으로", DONE))
    elif LEVEL_OLD in src:
        src = src.replace(LEVEL_OLD, LEVEL_NEW, 1)
        items.append(("⑩d", "지지·저항을 level_map 으로", TODO))
    else:
        items.append(("⑩d", "지지·저항을 level_map 으로", GONE))

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
        shown = "적용" if (state == TODO and apply) else state
        print(f"    {MARK[state]} {num} {label} — {shown}")

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
        for label, got, want in (
            ("LEVEL_SOURCE   (⑩)", getattr(backtest, "LEVEL_SOURCE", None), "levelmap"),
            ("_lm_levels 정의 (⑩)", hasattr(backtest, "_lm_levels"), True),
        ):
            good = got == want
            ok = ok and good
            print(f"  {'✅' if good else '❌'} {label}  {got!r}"
                  + ("" if good else f"   (기대: {want!r})"))
        try:
            import level_map
            print(f"  ✅ level_map      {level_map.__file__}")
        except Exception as e:
            ok = False
            print(f"  ❌ level_map 을 못 불렀습니다: {e}")
    except Exception as e:
        print(f"\n  import 실패: {type(e).__name__}: {e}")
        print("  (봇이 쓰는 파이썬/가상환경으로 돌리십시오)\n")
        print("  대신 파일 내용만으로 확인합니다:")
        if not os.path.exists("backtest.py"):
            print("    backtest.py 가 이 폴더에 없습니다.")
            return 1
        src = open("backtest.py", encoding="utf-8").read()
        for label, good in (
            ('LEVEL_SOURCE = "levelmap" (⑩)', 'LEVEL_SOURCE = "levelmap"' in src),
            ("_lm_levels 정의           (⑩)", "def _lm_levels(" in src),
            ("level_map.py 존재         (⑩)", os.path.exists("level_map.py")),
        ):
            ok = ok and good
            print(f"    {'✅' if good else '❌'} {label}")
    print("=" * 62)
    if not ok:
        print("  python fix_level_source.py --apply  를 이 폴더에서 돌리십시오.")
    return 0 if ok else 1


def main(argv):
    if "--verify" in argv:
        return verify()
    apply = "--apply" in argv
    print("=" * 62)
    print("  ⑩ 레벨 소스 교체 (backtest.py 전용)")
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
        print("  python fix_level_source.py --apply")
        return 0
    print("""  적용돼 있습니다.

  A/B 로 재는 법 — 조건은 똑같이, 토글만 바꿔서 두 번 돌립니다.

    python run_backtest.py --all 20 2y        (levelmap)

    backtest.py 위 LEVEL_SOURCE = "rolling20" 으로 바꾸고
    python run_backtest.py --all 20 2y        (예전 방식)

  볼 것
    · 중기롱/중기숏 **건수**가 25건에서 얼마나 늘었나  ← 이게 핵심
    · 늘어난 표본에서 기대값 부호가 유지되나
    · 30건 하한을 넘겨 ⚪ 가 색으로 바뀌는 타입이 있나

  건수가 늘고 기대값이 안 나빠지면 그때 bot.py 로 옮깁니다.""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
