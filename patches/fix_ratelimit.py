"""fix_ratelimit.py — ⑬ 429 때문에 결과가 실행할 때마다 달라진다

    cd /path/to/auto
    python fix_ratelimit.py           # 미리보기
    python fix_ratelimit.py --apply   # 적용 (원본은 .bak)
    python fix_ratelimit.py --verify  # 반영됐나

**backtest.py 만 고칩니다.**

무엇이 문제인가
──────────────
⑪로 페이지를 끝까지 넘기게 되자 요청 수가 4~5배로 늘었고, 거래소가
막기 시작했다:

    데이터 수집 오류 (ETH/USDT 4h): bitget {"code":"429","msg":"Too Many Requests"}
    ⚠️ ETH/USDT 4h: 830일 요청했는데 33일(200봉)만 받았습니다

  429가 나면 그 코인의 4H가 통째로 잘리고, **잘리는 코인이 매번
  다르다.** 실제로 같은 명령을 두 번 돌린 결과가 이렇게 갈렸다:

      1회차   중기롱 29건 -0.143R · 중기숏 75건 -0.165R
      2회차   중기롱 31건 -0.103R · 중기숏 62건 -0.177R

  건수가 20% 흔들린다. 이 상태로는 **어떤 비교도 성립하지 않는다.**
  ⑧을 넣어서 좋아진 건지 429가 덜 나서 좋아진 건지 구분할 수 없다.

무엇을 하나
──────────
① ccxt 의 자체 속도 조절을 켠다 (enableRateLimit)
     요청 사이 간격을 거래소 규정에 맞춰 자동으로 벌린다.

② 429가 나면 기다렸다 다시 시도한다 (1.5초 → 3초 → 6초, 최대 4회)
     지금은 한 번 막히면 그대로 포기하고 잘린 데이터로 계산한다.
     429는 '데이터가 없다'가 아니라 '지금은 말고'라는 뜻이다.

③ 동시 실행을 4 → 2로 줄인다
     4개 코인이 동시에 페이지를 넘기면서 429를 부른다. 2로 줄이면
     조금 느려지지만 결과가 흔들리지 않는다. 지금 필요한 건 속도가
     아니라 **같은 입력에 같은 답**이다.

  ①②③를 하고도 429가 남으면 그건 리포트에 그대로 찍힌다(⑪b 경고).
  조용히 잘린 데이터로 재는 일은 없다.

되돌리려면
─────────
    backtest.py.bak 을 덮어쓰면 됩니다.
"""

import ast
import os
import shutil
import sys

TODO, DONE, GONE = "적용 예정", "이미 적용됨", "대상 없음"
MARK = {TODO: "□", DONE: "✅", GONE: "⚠️"}

EX_OLD = "exchange = ccxt.bitget()"
EX_NEW = '''# enableRateLimit: ccxt 가 요청 사이 간격을 거래소 규정에 맞춰 벌린다.
# ⑪로 페이지를 끝까지 넘기게 되면서 요청이 4~5배로 늘었고, 이걸 안 켜면
# 429가 나면서 코인마다 데이터가 들쭉날쭉 잘린다 — 같은 명령을 두 번
# 돌렸는데 신호 건수가 20% 달랐다.
exchange = ccxt.bitget({"enableRateLimit": True})

# 429는 '데이터가 없다'가 아니라 '지금은 말고'다. 기다렸다 다시 묻는다.
RETRY_ON_429 = 4
RETRY_BACKOFF_SEC = 1.5'''

FETCH_OLD = """        try:
            batch = exchange.fetch_ohlcv(symbol, bitget_tf, since=since, limit=1000)
        except Exception as e:
            print(f"  데이터 수집 오류 ({symbol} {timeframe}): {e}")
            break"""

FETCH_NEW = """        batch = None
        for attempt in range(RETRY_ON_429):
            try:
                batch = exchange.fetch_ohlcv(symbol, bitget_tf, since=since, limit=1000)
                break
            except Exception as e:
                msg = str(e)
                if "429" in msg or "Too Many Requests" in msg:
                    if attempt < RETRY_ON_429 - 1:
                        _time.sleep(RETRY_BACKOFF_SEC * (2 ** attempt))
                        continue
                print(f"  데이터 수집 오류 ({symbol} {timeframe}): {e}")
                break
        if batch is None:
            break"""

IMPORT_OLD = "import ccxt\nimport pandas as pd"
IMPORT_NEW = "import ccxt\nimport time as _time\nimport pandas as pd"

WORKERS_OLD = "def run_multi_backtest(symbols, days=365, max_workers=4):"
WORKERS_NEW = ("# 4개 코인이 동시에 페이지를 넘기면 429가 난다. 지금 필요한 건\n"
               "# 속도가 아니라 같은 입력에 같은 답이다.\n"
               "def run_multi_backtest(symbols, days=365, max_workers=2):")


def patch_backtest(src):
    items = []
    for num, label, old, new, probe in (
        ("⑬a", "ccxt 속도 조절 켜기", EX_OLD, EX_NEW, "enableRateLimit"),
        ("⑬b", "time 모듈", IMPORT_OLD, IMPORT_NEW, "import time as _time"),
        ("⑬c", "429면 기다렸다 재시도", FETCH_OLD, FETCH_NEW, "RETRY_ON_429):"),
        ("⑬d", "동시 실행 4 → 2", WORKERS_OLD, WORKERS_NEW, "max_workers=2):"),
    ):
        if probe in src:
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
        import inspect
        print(f"\n  backtest.__file__  {getattr(backtest, '__file__', '?')}")
        rl = getattr(getattr(backtest, "exchange", None), "enableRateLimit", None)
        src = inspect.getsource(backtest.get_ohlcv_history)
        sig = inspect.signature(backtest.run_multi_backtest)
        checks = (
            ("속도 조절 켜짐   (⑬a)", rl is True),
            ("429 재시도       (⑬c)", "RETRY_ON_429" in src),
            ("동시 실행 2      (⑬d)", sig.parameters["max_workers"].default == 2),
        )
    except Exception as e:
        print(f"\n  import 실패: {type(e).__name__}: {e}")
        print("  (봇이 쓰는 파이썬/가상환경으로 돌리십시오)\n")
        print("  대신 파일 내용만으로 확인합니다:")
        if not os.path.exists("backtest.py"):
            print("    backtest.py 가 이 폴더에 없습니다.")
            return 1
        s = open("backtest.py", encoding="utf-8").read()
        checks = (
            ("속도 조절 켜짐   (⑬a)", "enableRateLimit" in s),
            ("429 재시도       (⑬c)", "RETRY_ON_429" in s),
            ("동시 실행 2      (⑬d)", "max_workers=2):" in s),
        )
    for label, good in checks:
        ok = ok and good
        print(f"  {'✅' if good else '❌'} {label}")
    print("=" * 62)
    if not ok:
        print("  python fix_ratelimit.py --apply  를 이 폴더에서 돌리십시오.")
    return 0 if ok else 1


def main(argv):
    if "--verify" in argv:
        return verify()
    apply = "--apply" in argv
    print("=" * 62)
    print("  ⑬ 429 대응 (backtest.py 전용)")
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
        print("  python fix_ratelimit.py --apply")
        return 0
    print("""  적용돼 있습니다.

  확인 방법 — **같은 명령을 두 번** 돌려 결과가 같은지 봅니다.

      python run_backtest.py --all 20 2y
      python run_backtest.py --all 20 2y

  · 429 줄이 안 나오고
  · ⚠️ 경고가 안 나오고
  · 두 리포트의 건수가 똑같으면

  그때부터 비교가 성립합니다. 지금까지의 숫자는 429가 섞여 있어
  20%까지 흔들렸습니다. 느려지지만 그게 맞는 거래입니다.""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
