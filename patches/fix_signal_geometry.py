"""fix_signal_geometry.py — 중기 신호가 구조적으로 질 수밖에 없는 이유 3개

    cd /path/to/auto
    python fix_signal_geometry.py           # 뭘 바꿀지 먼저 보여주기만
    python fix_signal_geometry.py --apply   # 실제로 적용 (원본은 .bak 로 백업)
    python fix_signal_geometry.py --verify  # 실제로 반영됐나

**backtest.py 만 고칩니다. bot.py 는 건드리지 않습니다.**
백테스트로 먼저 재보고, 숫자가 좋아진 게 확인된 다음에 봇에 옮깁니다.
순서를 뒤집으면 또 뭐가 뭘 바꿨는지 모르게 됩니다.

되돌리려면 backtest.py.bak 을 덮어쓰거나, 파일 맨 위 토글 3개를
예전 값으로 되돌리면 됩니다.

────────────────────────────────────────────────────────────

⑦ 리포트가 처리 못 한 코인을 세지 않는다

    scanned += 1
    if not trades: continue        # ← 데이터 실패도, 신호 0건도 여기로

  run_multi_backtest._one() 은 예외를 잡아 `(coin, [])` 를 돌려주는데
  `scanned` 는 그대로 올라간다. 절반이 실패해도 리포트는 "대상 20종"
  이라고 적는다. 실패와 '신호 없음'을 나눠 세고 둘 다 표시한다.

  덤: by_type 이 as_completed 순서로 쌓여서 MDD가 실행할 때마다 달라졌다.
  집계 전에 날짜순으로 정렬한다.

⑧ 손절폭이 일봉 노이즈의 11% 지점에 있다  ← 가장 큰 원인

    sl = round_px(sup * 0.99)      # 레벨에서 정확히 1%

  변동성과 무관한 상수다. 일간 변동성 4.5%짜리 코인에서 1% 손절은
  **1.5×ATR14의 0.11배**다. 즉 시장이 아무 방향도 안 정해도 맞는 자리다.

  실측(대칭 인공시장 20종×730일):
      손절폭 ÷ (1.5×ATR14)                   중앙값 0.11배
      진입 다음 봉에 바로 손절가를 건드릴 확률   78%
      수수료+슬리피지가 먹는 R
          고정 1% 손절      0.155 R/거래
          1.5×ATR 손절      0.017 R/거래      (-0.138R)

  손절폭이 좁으면 손실 1건의 크기(R)는 작아 보이지만, **고정비가 R로
  환산될 때 폭이 분모**라서 수수료가 R을 통째로 먹는다. 실제 리포트의
  중기숏 승률 8.3%는 전략이 틀려서가 아니라 손절이 노이즈 안에 있어서다.

  레벨 아래(위)이면서 동시에 노이즈 밖인 자리로 옮긴다:
      sl = min(sup * 0.99, price - 1.5 * ATR14)      (롱)
      sl = max(res * 1.01, price + 1.5 * ATR14)      (숏)

⑨ 4H 필터가 조건상 성립할 수 없는 걸 요구한다

    if near_sup and h4_up and rsi <= 35:      # h4_up = close > ma20 > ma50

  "20일 **최저점**에 있고 RSI 35 이하"인데 "4H가 완전한 정배열 상승"
  이어야 한다. 서로 모순이다.

  실측: 상승/하락이 완벽히 대칭인 인공시장 20종×730일에서
      레벨 근접만        지지 361회 / 저항 353회   ← 대칭
      4H 필터 켬         중기롱 0건 / 중기숏 0건   ← 둘 다 전멸
      4H 필터 끔         중기롱 213건 / 중기숏 236건

  필터가 '좋은 자리'를 고르는 게 아니라 사실상 전부를 지운다. 실제
  백테스트에서 살아남은 24건은 이 모순을 뚫고 나온 예외적인 봉들이고,
  그래서 승률이 8.3%다.

  '정배열 요구'를 '역추세 배제'로 바꾼다 — 원래 필터의 의도였던 것:
      롱: h4_down 이 아니면 통과   (숏: h4_up 이 아니면 통과)
"""

import ast
import os
import re
import shutil
import sys

TODO, DONE, GONE = "적용 예정", "이미 적용됨", "대상 없음"
MARK = {TODO: "□", DONE: "✅", GONE: "⚠️"}

TOGGLES = '''
# ── fix_signal_geometry 토글 ──
# 예전 동작으로 되돌리려면: ATR_STOP_MULT = 0.0, MID_H4_STRICT = True
#
# 손절을 레벨에서 몇 ATR 떨어뜨릴지. 0.0이면 예전처럼 레벨 ±1% 고정.
# 고정 1%는 일간 변동성 4.5%짜리 코인에서 1.5×ATR의 0.11배다 —
# 시장이 아무것도 안 해도 78% 확률로 다음 봉에 맞는다.
ATR_STOP_MULT = 1.5

# True면 예전처럼 4H 정배열(close>ma20>ma50)을 요구한다. 20일 최저점에서
# 4H 정배열은 모순이라 신호가 사실상 0이 된다. False면 역추세만 배제한다.
MID_H4_STRICT = False
'''

ATR_SRC = '''
    # ATR14 — 손절폭을 변동성에 맞추기 위해 (Wilder)
    prev_close = close.shift()
    true_range = pd.concat([high - low,
                            (high - prev_close).abs(),
                            (low - prev_close).abs()], axis=1).max(axis=1)
    df['atr'] = true_range.ewm(alpha=1/14, adjust=False, min_periods=14).mean()
'''

ATR_ANCHOR = """    # 피보나치 스윙 (최근 50봉)
    df['swing_high'] = high.rolling(50).max()
    df['swing_low']  = low.rolling(50).min()"""

# ── ⑧⑨ 중기 롱 ──
LONG_OLD = """        # ── 중기 롱 ──
        if near_sup and (h4_up or df_h4 is None) and rsi <= RSI_OVERSOLD:
            sl  = round_px(sup * 0.99)"""

LONG_NEW = """        # ── 중기 롱 ──
        # 4H 필터: 정배열을 요구하면 '20일 최저점 + RSI35'와 모순이라
        # 신호가 사실상 0이 된다. 역추세만 배제한다.
        h4_ok_long = (df_h4 is None) or (h4_up if MID_H4_STRICT else not h4_down)
        if near_sup and h4_ok_long and rsi <= RSI_OVERSOLD:
            # 레벨 아래이면서 동시에 노이즈 밖인 자리.
            sl  = round_px(_stop_below(price, sup, row))"""

SHORT_OLD = """        # ── 중기 숏 ──
        if near_res and (h4_down or df_h4 is None) and rsi >= RSI_OVERBOUGHT:
            sl   = round_px(res * 1.01)"""

SHORT_NEW = """        # ── 중기 숏 ──
        h4_ok_short = (df_h4 is None) or (h4_down if MID_H4_STRICT else not h4_up)
        if near_res and h4_ok_short and rsi >= RSI_OVERBOUGHT:
            sl   = round_px(_stop_above(price, res, row))"""

STOP_HELPERS = '''

def _atr_of(row):
    """해당 봉의 ATR14. 없으면 None."""
    try:
        v = row['atr'] if 'atr' in row.index else None
    except Exception:
        return None
    return float(v) if v is not None and v == v and v > 0 else None


def _stop_below(price, level, row):
    """롱 손절: 레벨 아래이면서 노이즈 밖."""
    base = level * 0.99
    atr = _atr_of(row)
    if not ATR_STOP_MULT or atr is None:
        return base
    return min(base, price - ATR_STOP_MULT * atr)


def _stop_above(price, level, row):
    """숏 손절: 레벨 위이면서 노이즈 밖."""
    base = level * 1.01
    atr = _atr_of(row)
    if not ATR_STOP_MULT or atr is None:
        return base
    return max(base, price + ATR_STOP_MULT * atr)
'''

HELPER_ANCHOR = "def detect_signals_vectorized(df_daily, df_weekly=None, df_h4=None):"

# ── ⑦ 처리 실패를 세고 표시 ──
ONE_FAIL_OLD = """        except Exception as e:
            print(f"[백테전체] {coin} 오류: {e}")
            return coin, []"""

ONE_FAIL_NEW = """        except Exception as e:
            print(f"[백테전체] {coin} 오류: {e}")
            return coin, None       # None = 처리 실패 ([] = 신호 0건과 구분)"""

ONE_SHORT_OLD = """            if df_d is None or len(df_d) < 60:
                return coin, []"""

ONE_SHORT_NEW = """            if df_d is None or len(df_d) < 60:
                return coin, None   # 데이터 부족도 '실패'다"""

LOOP_OLD = """    scanned = 0
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(_one, s): s for s in symbols}
        for f in as_completed(futs):
            coin, trades = f.result()
            scanned += 1
            if not trades:
                continue"""

LOOP_NEW = """    scanned = 0
    failed, no_signal = [], []      # 실패와 '신호 0건'은 다른 얘기다
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(_one, s): s for s in symbols}
        for f in as_completed(futs):
            coin, trades = f.result()
            scanned += 1
            if trades is None:
                failed.append(coin)
                continue
            if not trades:
                no_signal.append(coin)
                continue"""

AGG_OLD = """    type_stats = {tp: calc_stats(tr) for tp, tr in by_type.items() if tr}
    return format_multi_backtest(type_stats, coin_summary, days, scanned)"""

AGG_NEW = """    # 날짜순으로 정렬한 뒤 집계한다. as_completed 순서로 쌓으면 MDD가
    # 실행할 때마다 달라진다 — 같은 입력에 같은 답이 나와야 한다.
    type_stats = {tp: calc_stats(sorted(tr, key=lambda t: t.get('date', '')))
                  for tp, tr in by_type.items() if tr}
    return format_multi_backtest(type_stats, coin_summary, days, scanned,
                                 failed=sorted(failed), no_signal=sorted(no_signal))"""

FMT_SIG_OLD = "def format_multi_backtest(type_stats, coin_summary, days, scanned):"
FMT_SIG_NEW = ("def format_multi_backtest(type_stats, coin_summary, days, scanned,\n"
               "                          failed=(), no_signal=()):")

FMT_HEAD_OLD = """        f"대상 {scanned}종 · {days}일 · 수수료·슬리피지 반영","""

FMT_HEAD_NEW = """        f"대상 {scanned}종 중 {scanned - len(failed) - len(no_signal)}종에서 신호"
        f" · {days}일 · 수수료·슬리피지 반영","""

FMT_WARN_OLD = """        "<i>2단계 자동매매는 여기서 기대값 +인 타입만 화이트리스트</i>",
        "","""

FMT_WARN_NEW = """        "<i>2단계 자동매매는 여기서 기대값 +인 타입만 화이트리스트</i>",
        "",
    ]
    # 조용히 빠진 코인을 표시한다. 예전에는 절반이 실패해도 "대상 20종"
    # 이라고만 적혀서, 표본이 왜 줄었는지 알 수가 없었다.
    if failed:
        lines.append(f"⚠️ 데이터를 못 받아 빠짐 {len(failed)}종: "
                     f"{', '.join(failed[:8])}{' 외' if len(failed) > 8 else ''}")
    if no_signal:
        lines.append(f"· 신호 0건 {len(no_signal)}종: "
                     f"{', '.join(no_signal[:8])}{' 외' if len(no_signal) > 8 else ''}")
    if failed or no_signal:
        lines.append("")
    lines += ["""


def patch_backtest(src):
    items = []

    # ── 토글 상수 ──
    if "ATR_STOP_MULT" in src:
        items.append(("⑧a", "토글 상수 (ATR_STOP_MULT / MID_H4_STRICT)", DONE))
    elif re.search(r"^RSI_OVERBOUGHT\s*=.*$", src, re.M):
        src = re.sub(r"^(RSI_OVERBOUGHT\s*=.*)$", r"\1\n" + TOGGLES.strip("\n"),
                     src, count=1, flags=re.M)
        items.append(("⑧a", "토글 상수 (ATR_STOP_MULT / MID_H4_STRICT)", TODO))
    else:
        items.append(("⑧a", "토글 상수", GONE))

    # ── ATR14 지표 ──
    if "df['atr']" in src:
        items.append(("⑧b", "ATR14 지표", DONE))
    elif ATR_ANCHOR in src:
        src = src.replace(ATR_ANCHOR, ATR_ANCHOR + "\n" + ATR_SRC.rstrip(), 1)
        items.append(("⑧b", "ATR14 지표", TODO))
    else:
        items.append(("⑧b", "ATR14 지표", GONE))

    # ── 손절 계산 도우미 ──
    if "def _stop_below(" in src:
        items.append(("⑧c", "손절 계산 도우미", DONE))
    elif HELPER_ANCHOR in src:
        src = src.replace(HELPER_ANCHOR, STOP_HELPERS.strip("\n") + "\n\n\n" + HELPER_ANCHOR, 1)
        items.append(("⑧c", "손절 계산 도우미", TODO))
    else:
        items.append(("⑧c", "손절 계산 도우미", GONE))

    # ── ⑧⑨ 중기 롱/숏 ──
    for num, label, old, new, probe in (
        ("⑨a", "중기 롱 — 손절 ATR화 + 4H 필터 완화", LONG_OLD, LONG_NEW, "h4_ok_long"),
        ("⑨b", "중기 숏 — 손절 ATR화 + 4H 필터 완화", SHORT_OLD, SHORT_NEW, "h4_ok_short"),
    ):
        if probe in src:
            items.append((num, label, DONE))
        elif old in src:
            src = src.replace(old, new, 1)
            items.append((num, label, TODO))
        else:
            items.append((num, label, GONE))

    # ── ⑦ 실패 집계 · 결정적 MDD ──
    seven = []
    for old, new, probe in (
        (ONE_FAIL_OLD, ONE_FAIL_NEW, "return coin, None       # None = 처리 실패"),
        (ONE_SHORT_OLD, ONE_SHORT_NEW, "# 데이터 부족도 '실패'다"),
        (LOOP_OLD, LOOP_NEW, "failed, no_signal = [], []"),
        (AGG_OLD, AGG_NEW, "key=lambda t: t.get('date', '')"),
        (FMT_SIG_OLD, FMT_SIG_NEW, "no_signal=()):"),
        (FMT_HEAD_OLD, FMT_HEAD_NEW, "종에서 신호"),
        (FMT_WARN_OLD, FMT_WARN_NEW, "데이터를 못 받아 빠짐"),
    ):
        if probe in src:
            seven.append(DONE)
        elif old in src:
            src = src.replace(old, new, 1)
            seven.append(TODO)
        else:
            seven.append(GONE)

    label7 = "빠진 코인 표시 + MDD 결정화"
    if GONE in seven:
        items.append(("⑦", f"{label7} ({seven.count(GONE)}/{len(seven)}조각 못 찾음)", GONE))
    elif TODO in seven:
        items.append(("⑦", label7, TODO))
    else:
        items.append(("⑦", label7, DONE))

    return src, items


def process(path, apply):
    """(성공?, 아직 적용 안 된 수정 수)"""
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
    try:
        import backtest
        where = getattr(backtest, "__file__", "?")
        checks = [
            ("ATR_STOP_MULT       (⑧)", getattr(backtest, "ATR_STOP_MULT", None), 1.5),
            ("MID_H4_STRICT       (⑨)", getattr(backtest, "MID_H4_STRICT", None), False),
            ("_stop_below 정의    (⑧)", hasattr(backtest, "_stop_below"), True),
        ]
        print(f"\n  backtest.__file__  {where}")
        ok = True
        for label, got, want in checks:
            good = got == want
            ok = ok and good
            print(f"  {'✅' if good else '❌'} {label}  {got!r}"
                  + ("" if good else f"   (기대: {want!r})"))
    except Exception as e:
        print(f"\n  import 실패: {type(e).__name__}: {e}")
        print("  (봇이 쓰는 파이썬/가상환경으로 돌리십시오)")
        print("\n  대신 파일 내용만으로 확인합니다:")
        if not os.path.exists("backtest.py"):
            print("    backtest.py 가 이 폴더에 없습니다.")
            return 1
        with open("backtest.py", encoding="utf-8") as f:
            src = f.read()
        ok = True
        for label, good in (
            ("ATR_STOP_MULT = 1.5   (⑧)", "ATR_STOP_MULT = 1.5" in src),
            ("MID_H4_STRICT = False (⑨)", "MID_H4_STRICT = False" in src),
            ("_stop_below 정의      (⑧)", "def _stop_below(" in src),
            ("failed/no_signal 집계 (⑦)", "failed, no_signal = [], []" in src),
        ):
            ok = ok and good
            print(f"    {'✅' if good else '❌'} {label}")
    print("=" * 62)
    if not ok:
        print("  python fix_signal_geometry.py --apply  를 이 폴더에서 돌리십시오.")
    return 0 if ok else 1


def main(argv):
    if "--verify" in argv:
        return verify()

    apply = "--apply" in argv
    print("=" * 62)
    print("  중기 신호 기하 수정 (backtest.py 전용)")
    print("=" * 62)
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
        print("  python fix_signal_geometry.py --apply")
        return 0
    print("""  모두 적용돼 있습니다.

  다음에 할 일
    1. python fix_signal_geometry.py --verify
    2. 같은 조건으로 다시 돌립니다 — 직전과 비교할 수 있게 조건을 바꾸지 마십시오
         /백테전체 20개 2y      (또는 python run_backtest.py --all 20 2y)
    3. 볼 것 세 가지
         · 중기롱이 0건에서 벗어났나          (⑨가 먹혔나)
         · 중기숏 승률이 8.3%에서 올라갔나    (⑧이 먹혔나)
         · '데이터를 못 받아 빠짐' 줄에 몇 종  (⑦ — 표본이 왜 적은지)

  bot.py 는 아직 안 건드렸습니다. 위 숫자가 좋아진 걸 보고 나서 옮깁니다.""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
