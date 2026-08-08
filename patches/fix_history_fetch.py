"""fix_history_fetch.py — ⑪ 과거 데이터를 요청한 만큼 받아오게

    cd /path/to/auto
    python fix_history_fetch.py           # 미리보기
    python fix_history_fetch.py --apply   # 적용 (원본은 .bak)
    python fix_history_fetch.py --verify  # 반영됐나

**backtest.py 만 고칩니다.**

무엇이 문제인가
──────────────

    while len(all_ohlcv) < needed_candles:
        batch = exchange.fetch_ohlcv(symbol, tf, since=since, limit=1000)
        if not batch:
            break
        all_ohlcv.extend(batch)
        since = batch[-1][0] + 1
        if len(batch) < 1000:
            break              # ← 여기

  마지막 줄이 "한 번에 1000봉을 못 받으면 더 안 받는다"는 뜻이다.
  거래소는 오래된 구간을 다른 엔드포인트로 주고, 그쪽 한 번 상한은
  1000보다 작다. 그러면 **첫 배치 한 번 받고 끝난다.**

  실측(diag_funnel, 20종 730일): 신호 판정에 쓰인 봉이 3,729개.
  14,600이어야 한다. 지표 워밍업으로 빠지는 건 코인당 99봉뿐이니,
  나머지는 애초에 받아오지 않은 것이다. 코인당 186봉 — 8개월치다.

  즉 **`/백테전체 20개 2y` 는 2년을 잰 적이 없다.** 1y로 돌리든 2y로
  돌리든 거의 같은 결과가 나온 이유가 이것이다.

  주봉은 더 나쁘다. backtest.py 는 50봉 미만 주봉을 통째로 버리므로,
  주봉이 모자라면 천사/악마는 검사 자체가 안 된다 — 실제로 20종
  730일에 0건이었다.

무엇으로 바꾸나
──────────────
  '짧은 배치'를 끝 신호로 쓰지 않는다. 대신 진짜 끝을 본다:

    · 빈 배치가 오면 끝
    · 마지막 봉이 현재 시각에 닿으면 끝
    · since 가 더 안 밀리면 끝 (무한루프 방지)
    · 목표 봉 수를 채우면 끝
    · 그리고 60회 상한 (거래소가 이상하게 굴 때의 안전장치)

  덤: 요청 기간의 70%도 못 받으면 그 자리에서 한 줄 경고한다.
      조용히 짧은 데이터로 재는 것보다 시끄러운 게 낫다.

되돌리려면
─────────
    backtest.py.bak 을 덮어쓰면 됩니다.
"""

import ast
import os
import re
import shutil
import sys

TODO, DONE, GONE = "적용 예정", "이미 적용됨", "대상 없음"
MARK = {TODO: "□", DONE: "✅", GONE: "⚠️"}

LOOP_OLD = """    all_ohlcv = []
    since     = int((datetime.now() - timedelta(days=days+10)).timestamp() * 1000)

    while len(all_ohlcv) < needed_candles:
        try:
            bitget_tf = _BITGET_TF_MAP.get(timeframe, timeframe)
            batch = exchange.fetch_ohlcv(symbol, bitget_tf, since=since, limit=1000)
            if not batch:
                break
            all_ohlcv.extend(batch)
            since = batch[-1][0] + 1
            if len(batch) < 1000:
                break
        except Exception as e:
            print(f"  데이터 수집 오류: {e}")
            break"""

LOOP_NEW = """    all_ohlcv = []
    since     = int((datetime.now() - timedelta(days=days+10)).timestamp() * 1000)
    now_ms    = int(datetime.now().timestamp() * 1000)
    step_ms   = minutes_per_candle * 60 * 1000
    bitget_tf = _BITGET_TF_MAP.get(timeframe, timeframe)
    last_ts   = None

    # '짧은 배치'를 끝 신호로 쓰면 안 된다. 거래소는 오래된 구간을 다른
    # 엔드포인트로 주고 그쪽 한 번 상한은 1000보다 작다. 예전 코드는
    # `if len(batch) < 1000: break` 때문에 첫 배치만 받고 끝났다 —
    # 730일을 요청해도 실제로는 8개월치만 재고 있었다.
    for _ in range(60):                       # 거래소가 이상하게 굴 때의 상한
        try:
            batch = exchange.fetch_ohlcv(symbol, bitget_tf, since=since, limit=1000)
        except Exception as e:
            print(f"  데이터 수집 오류 ({symbol} {timeframe}): {e}")
            break
        if not batch:
            break
        all_ohlcv.extend(batch)
        newest = batch[-1][0]
        if last_ts is not None and newest <= last_ts:
            break                             # 진전이 없으면 무한루프다
        last_ts = newest
        if newest + step_ms > now_ms:
            break                             # 현재까지 왔다
        if len(all_ohlcv) >= needed_candles:
            break
        since = newest + 1"""

WARN_OLD = """    # 요청 기간만큼 자르기
    cutoff = datetime.now() - timedelta(days=days)
    df = df[df['timestamp'] >= cutoff].reset_index(drop=True)

    return df"""

WARN_NEW = """    # 요청 기간만큼 자르기
    cutoff = datetime.now() - timedelta(days=days)
    df = df[df['timestamp'] >= cutoff].reset_index(drop=True)

    # 조용히 짧은 데이터로 재는 것보다 시끄러운 게 낫다. 실제로 730일을
    # 요청하고 8개월치로 재면서 그걸 몇 주 동안 몰랐다.
    if len(df) >= 2:
        span = (df['timestamp'].iloc[-1] - df['timestamp'].iloc[0]).days
        if span < days * 0.7:
            print(f"  ⚠️ {symbol} {timeframe}: {days}일 요청했는데 "
                  f"{span}일({len(df)}봉)만 받았습니다")

    return df"""


def patch_backtest(src):
    items = []

    if "for _ in range(60):" in src and "진전이 없으면 무한루프다" in src:
        items.append(("⑪a", "페이지 넘기기를 끝까지", DONE))
    elif LOOP_OLD in src:
        src = src.replace(LOOP_OLD, LOOP_NEW, 1)
        items.append(("⑪a", "페이지 넘기기를 끝까지", TODO))
    else:
        items.append(("⑪a", "페이지 넘기기를 끝까지", GONE))

    if "일 요청했는데" in src:
        items.append(("⑪b", "데이터가 짧으면 경고", DONE))
    elif WARN_OLD in src:
        src = src.replace(WARN_OLD, WARN_NEW, 1)
        items.append(("⑪b", "데이터가 짧으면 경고", TODO))
    else:
        items.append(("⑪b", "데이터가 짧으면 경고", GONE))

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
        import inspect
        print(f"\n  backtest.__file__  {getattr(backtest, '__file__', '?')}")
        src = inspect.getsource(backtest.get_ohlcv_history)
        for label, good in (
            ("짧은 배치에서 중단 안 함 (⑪a)", "if len(batch) < 1000:" not in src),
            ("진전 없으면 중단        (⑪a)", "무한루프다" in src),
            ("짧으면 경고            (⑪b)", "일 요청했는데" in src),
        ):
            ok = ok and good
            print(f"  {'✅' if good else '❌'} {label}")
    except Exception as e:
        print(f"\n  import 실패: {type(e).__name__}: {e}")
        print("  (봇이 쓰는 파이썬/가상환경으로 돌리십시오)\n")
        print("  대신 파일 내용만으로 확인합니다:")
        if not os.path.exists("backtest.py"):
            print("    backtest.py 가 이 폴더에 없습니다.")
            return 1
        src = open("backtest.py", encoding="utf-8").read()
        for label, good in (
            ("짧은 배치에서 중단 안 함 (⑪a)", "if len(batch) < 1000:" not in src),
            ("진전 없으면 중단        (⑪a)", "무한루프다" in src),
            ("짧으면 경고            (⑪b)", "일 요청했는데" in src),
        ):
            ok = ok and good
            print(f"    {'✅' if good else '❌'} {label}")
    print("=" * 62)
    if not ok:
        print("  python fix_history_fetch.py --apply  를 이 폴더에서 돌리십시오.")
    return 0 if ok else 1


def main(argv):
    if "--verify" in argv:
        return verify()
    apply = "--apply" in argv
    print("=" * 62)
    print("  ⑪ 과거 데이터 수집 (backtest.py 전용)")
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
        print("  python fix_history_fetch.py --apply")
        return 0
    print("""  적용돼 있습니다.

  다음
    1. python diag_data.py 20 2y      ← 이제 기간을 채우는지 확인
    2. python diag_funnel.py          ← 전체 봉이 14,000쯤 나와야 정상
    3. python run_backtest.py --all 20 2y

  데이터를 더 받으므로 3번은 예전보다 오래 걸립니다.""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
