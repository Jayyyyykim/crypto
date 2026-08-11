"""fix_daily_record.py — ㉗ 스캐너가 한 말을 앞으로 기록한다

    cd /path/to/jaybot
    python fix_daily_record.py           # 미리보기
    python fix_daily_record.py --apply   # 적용 (원본은 .bak)
    python fix_daily_record.py --verify  # 반영됐나

bot.py 를 고칩니다.

────────────────────────────────────────────────────────────

왜 이게 남았나

  진입 규칙 70가지를 쟀고 하나도 통과하지 못했습니다. 그래서 봇을
  스캐너로 돌렸습니다. 그런데 **그 스캐너가 하는 말이 쓸모 있는지는
  아무도 안 재고 있습니다.** 지나가면 사라집니다.

  이게 정확히 README 에 적어 둔 실패입니다 —

      "맞은 신호는 스크린샷이 남고 틀린 신호는 조용히 지나간다."

  지금까지 잰 것은 **전부 과거 자료 되짚기**였습니다. 같은 4년에
  71번째 규칙을 들이대는 건 값이 떨어집니다. 새 정보가 나오는 건
  앞으로 쌓는 기록뿐입니다.

무엇을 붙이나

  `setup_ledger.py` 와 `call_journal.py` 가 이미 있고, 정확히 이걸
  하려고 만든 것입니다. 그런데 **봇 본체에 안 붙어 있습니다.**
  `start.py` 로 사람이 손수 돌려야 하고, 사흘 잊으면 그 사흘의
  사건은 영영 안 잡힙니다.

  → 봉마감 브리핑이 뜰 때 하루 한 번, 저절로 돕니다.

      1. 채점 기준을 기록장에 박습니다 (맨 처음 한 번만)
      2. 오늘 마감봉에서 셋업 사건을 잡아 미채점으로 넣습니다
      3. 기한이 지난 사건을 채점합니다 — 봇이 며칠 꺼져 있었어도
         켜지면 밀린 것을 따라잡습니다

  4~8주 뒤에 이걸 물을 수 있게 됩니다:

      이 스캐너를 읽는 것이 아무 날이나 보는 것보다 나았나

  `setup_ledger` 는 그 답을 **기준선 대비 초과**로 냅니다. 적중률
  81.9% 같은 숫자가 왜 실력이 아닐 수 있는지는 이미 겪었습니다.

브리핑을 늦추지 않습니다

  기록은 **딴 실에서** 돕니다. 종목 서른 개 시세를 받는 동안
  봉마감이 멈춰 있으면 그건 개선이 아닙니다. 실패하면 한 줄
  찍고 그날은 넘어갑니다. 브리핑은 그대로 나갑니다.

  같은 날 두 번 돌지 않습니다. 설령 돌아도 사건 id 가
  `종목|1d|날짜|종류` 라 원장이 알아서 겹치는 것을 버립니다 —
  하루 한 번 제한은 정확성이 아니라 **일을 덜 하려고** 있는 것입니다.

소급 채점은 자동으로 안 합니다

  과거 1,500봉을 서른 종목에 돌리는 건 몇 분 걸리고 거래소를
  두드립니다. 봇이 뜰 때마다 할 일이 아닙니다. 원장이 비어 있으면
  화면에 이 줄이 뜹니다.

      [기록장] 소급 표본이 없습니다. 한 번만: python start.py setup

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


# ── ㉗a 기록 함수를 모듈 수준에 넣는다 ──
#
# 앞에 줄바꿈을 붙여 **맨 위 def** 만 잡는다. 이름만으로 찾으면
# 이 함수를 부르는 자리가 위에 있을 때 거기에 끼워 넣게 되고,
# 그러면 함수 안에 모듈 수준 코드가 들어간다.
HEAD_OLD = "\ndef get_candle_briefing_multi("

HEAD_NEW = '''

_REC = {"day": None, "busy": False, "on": True}


def _record_probe(fn, coins):
    """시세 함수 모양이 맞나 한 번만 확인한다.

    setup_ledger 는 get_ohlcv(종목, 타임프레임, limit=개수) 를
    기대한다. 모양이 다르면 서른 종목이 전부 조용히 실패하고
    원장은 빈 채로 며칠이 간다 — 그 며칠은 되돌릴 수 없다.
    """
    try:
        df = fn(coins[0], "1d", limit=50)
    except TypeError as e:
        print(f"[기록장] 시세 함수 인자 모양이 다릅니다 ({e}). "
              f"get_ohlcv(종목, 타임프레임, limit=개수) 형태여야 합니다.")
        return False
    except Exception as e:
        print(f"[기록장] {coins[0]} 시세를 못 받았습니다: "
              f"{type(e).__name__}: {e}")
        return False
    if df is None or len(df) == 0:
        print(f"[기록장] {coins[0]} 시세가 비어 있습니다.")
        return False
    return True


def _record_now():
    """오늘치 사건을 잡고 밀린 것을 채점한다. 딴 실에서 돈다."""
    try:
        import call_journal
        import setup_ledger
    except ImportError as e:
        print(f"[기록장] 모듈이 없어 끕니다: {e}")
        _REC["on"] = False
        return
    try:
        coins = get_paper_coins()
    except Exception as e:
        print(f"[기록장] 대상 종목을 못 받았습니다: {type(e).__name__}: {e}")
        return
    if not coins:
        return
    if not _record_probe(get_ohlcv, coins):
        _REC["on"] = False
        return

    # 채점 기준은 **결과를 보기 전에** 박아 둔다. 나중에 고치면
    # 새 항목으로 남아서 언제 무엇을 바꿨는지 티가 난다.
    try:
        if not call_journal.criteria_history():
            call_journal.register_criteria({
                "보합_밴드"      : setup_ledger.FLAT_BAND,
                "채점_시점"      : list(setup_ledger.DEFAULT_HORIZONS),
                "구간_룩백"      : setup_ledger.RANGE_LOOKBACK,
                "발표_최소표본"  : 30,
                "주의": "이 기준은 결과가 나온 뒤에 바꾸지 않습니다.",
            })
            print("[기록장] 채점 기준을 박았습니다.")
    except Exception as e:
        print(f"[기록장] 기준 기록 실패: {type(e).__name__}: {e}")

    try:
        new = setup_ledger.capture(coins, get_ohlcv, timeframe="1d")
        done = setup_ledger.score_pending(get_ohlcv, timeframe="1d")
        s = setup_ledger.summary()
        print(f"[기록장] 오늘 사건 {len(new)}건 · 채점 {len(done)}건 "
              f"· 누적 {s['total']:,}건({s['scored']:,}건 채점됨)")
        if not s["backfill"]:
            print("[기록장] 소급 표본이 없습니다. 한 번만: "
                  "python start.py setup")
    except Exception as e:
        print(f"[기록장] 오늘은 건너뜁니다: {type(e).__name__}: {e}")


def record_day():
    """하루 한 번 기록을 돌린다. 브리핑을 절대 늦추지 않는다.

    스캐너가 한 말이 쓸모 있었는지는 앞으로 쌓는 기록으로만 알 수
    있다. 진입 규칙 70가지를 재서 하나도 통과하지 못했으므로,
    남은 질문은 "이걸 읽는 게 아무 날이나 보는 것보다 나았나"뿐이다.
    """
    if not _REC["on"] or _REC["busy"]:
        return
    import datetime as _dt
    import threading as _th
    today = _dt.date.today().isoformat()
    if _REC["day"] == today:
        return
    _REC["day"] = today
    _REC["busy"] = True

    def _go():
        try:
            _record_now()
        except NameError as e:
            # 함수 이름이 다른 봇이다. 매일 같은 줄을 찍지 않는다.
            print(f"[기록장] 봇에 없는 이름이라 끕니다: {e}")
            _REC["on"] = False
        except Exception as e:
            print(f"[기록장] {type(e).__name__}: {e}")
        finally:
            _REC["busy"] = False

    _th.Thread(target=_go, name="record_day", daemon=True).start()


''' + HEAD_OLD


# ── ㉗b 봉마감 때 부른다 ──
CALL_OLD = """    # TF 엇갈림 (v4.4 애드온)
    trend_int = {}"""

CALL_NEW = """    # 오늘 무엇을 봤는지 앞으로 기록한다 (하루 1회, 딴 실).
    # 브리핑은 기다리지 않는다.
    try:
        record_day()
    except Exception as _re:
        print(f"[기록장] {type(_re).__name__}: {_re}")

""" + CALL_OLD


SITES = [
    ("㉗a", "기록 함수", HEAD_OLD, HEAD_NEW, "def record_day():"),
    ("㉗b", "봉마감에서 부르기", CALL_OLD, CALL_NEW, "record_day()\n    except"),
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
    print("  ㉗ 스캐너가 한 말을 앞으로 기록한다")
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
        missing = [n for n in ("setup_ledger.py", "call_journal.py")
                   if not os.path.exists(os.path.join(os.getcwd(), n))]
        if missing:
            print(f"\n  ⚠️ 같은 폴더에 없습니다: {', '.join(missing)}")
            print("     없으면 기록은 조용히 꺼집니다(봇은 그대로 돕니다).")
        print("=" * 62)
        return 0

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
                  "  적용:  python fix_daily_record.py --apply")
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

  하루 한 번, 첫 봉마감 브리핑 때 이런 줄이 뜹니다.

      [기록장] 오늘 사건 7건 · 채점 12건 · 누적 1,284건(950건 채점됨)

  아직 안 하셨으면 소급 채점을 **한 번만** 돌리십시오. 과거 봉으로
  기준선 표본을 만드는 절차라, 이게 없으면 몇 주 뒤에 나올 숫자를
  무엇과 견줄지가 없습니다.

      python start.py setup

  4~8주 뒤에 이걸 보십시오.

      python start.py report

  거기 나오는 **초과 적중률**이 이 스캐너의 성적입니다. 적중률
  자체가 아니라, 아무 날이나 잡았을 때보다 얼마나 나은지입니다.""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
