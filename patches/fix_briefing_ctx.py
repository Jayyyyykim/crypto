"""fix_briefing_ctx.py — ㉓ 봉마감 브리핑에 역사적 문맥 한 줄

    cd /path/to/jaybot
    python fix_briefing_ctx.py           # 미리보기
    python fix_briefing_ctx.py --apply   # 적용 (원본은 .bak)
    python fix_briefing_ctx.py --verify  # 반영됐나

bot.py 를 고칩니다.

────────────────────────────────────────────────────────────

/질문 은 **네가 물어봐야** 나옵니다. 봉마감 브리핑은 하루 여섯 번
저절로 옵니다. 실제로 매일 보게 되는 건 이쪽인데, 여기엔 5.8년치
문맥이 한 줄도 안 붙어 있습니다.

    🕯 BTC 4H봉 마감
    현재가 $65,242
       추세선 $65,100  🟢
       저항  $65,403 / $65,780
       지지  $64,727 / $64,120
       💠 펀딩비(선물) 상위 · 롱숏 계정비(선물·전체계정) 하위   ← 이 줄

**조용한 날에는 아무것도 안 붙습니다.** one_line() 은 상·하위 10%에
든 지표가 하나도 없으면 빈 문자열을 돌려줍니다. 매번 붙으면 아무도
안 읽게 되니까요.

그리고 '비교가 맞지 않는' 지표는 빠집니다. 지금은 테이커가 거기
해당합니다 — 종목의 75%를 극단이라고 하는 지표라, 붙이면 매 브리핑에
가짜 경고가 하나씩 생깁니다.

무엇을 안 하나
─────────────
방향을 말하지 않습니다. '펀딩 상위 3%' 까지만 적고, 그게 오른다는
뜻인지 내린다는 뜻인지는 **안 적습니다.** 60가지를 재서 그걸 알 수
없다는 게 확인됐기 때문입니다(FINDINGS.md).

없으면 그냥 넘어갑니다
────────────────────
percentile_ctx.py 나 percentile_ctx.json 이 없으면 이 줄은 조용히
빠지고 브리핑은 그대로 나갑니다. 분석 도구 하나 때문에 봉마감이
안 오면 그건 개선이 아닙니다.

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


ANCHOR = """    # TF 엇갈림 (v4.4 애드온)
    trend_int = {}"""

NEW = '''    # 파생지표가 역사적으로 어디쯤인가 (percentile_ctx).
    #
    # 조용한 날에는 아무것도 안 붙는다 — one_line 은 극단인 지표가
    # 없으면 빈 문자열이다. 매번 붙으면 아무도 안 읽는다.
    # 방향은 말하지 않는다. '상위 3%' 까지만 적는다.
    try:
        import percentile_ctx as _pc
        _tab = _pc.table_once()
        if _tab.get(coin):
            _v = _pc.live_cached([coin]).get(coin)
            if _v:
                _ln = _pc.one_line(coin, _v, _tab, None,
                                   _pc.miscalibrated({}))
                if _ln:
                    lines.append("   💠 " + _ln.split(": ", 1)[-1])
    except Exception as _pe:
        print(f"[percentile_ctx] {coin} 건너뜀: {type(_pe).__name__}: {_pe}")

''' + ANCHOR

MARKER = "percentile_ctx] {coin} 건너뜀"


def find(name="bot.py"):
    for cand in (os.path.join(os.getcwd(), name),
                 os.path.join(os.path.dirname(os.path.abspath(__file__)), name)):
        if os.path.exists(cand):
            return cand
    return None


def status_of(src):
    if MARKER in src:
        return DONE
    if ANCHOR in src:
        return TODO
    return GONE


def main(argv):
    apply = "--apply" in argv
    verify = "--verify" in argv

    path = find()
    print("=" * 62)
    print("  ㉓ 봉마감 브리핑에 역사적 문맥 한 줄")
    print("=" * 62)
    print(f"  대상 폴더: {os.getcwd()}")
    if not path:
        print("\n  bot.py 를 못 찾았습니다. 봇 폴더에서 실행하십시오.")
        return 1

    with open(path, encoding="utf-8") as fp:
        src = original = fp.read()

    if verify:
        ok = MARKER in src
        print(f"\n  실제 파일 확인")
        print(f"    {'✅' if ok else '□'} ㉓ 봉마감 브리핑 문맥 줄")
        print("=" * 62)
        return 0

    st = status_of(src)
    print(f"\n  bot.py")
    print(f"    {MARK[st]} ㉓ 봉마감 브리핑 문맥 줄 — {st}")

    if st == GONE:
        print("\n  get_candle_briefing_multi 의 'TF 엇갈림' 부분을 못 찾았습니다.")
        print("  봇 판이 다르면 이 패치는 맞지 않습니다.")
        return 1

    if not apply:
        print("\n" + "=" * 62)
        if st == TODO:
            print("  적용:  python fix_briefing_ctx.py --apply")
        else:
            print("  고칠 것이 없습니다.")
        return 0

    if st == DONE:
        print("\n  이미 들어가 있습니다.")
        return 0

    src = src.replace(ANCHOR, NEW, 1)

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

  다음 봉마감(4H: 1·5·9·13·17·21시 KST)에 이런 줄이 붙습니다.

      💠 펀딩비(선물) 상위 · 롱숏 계정비(선물·전체계정) 하위

  안 붙으면 그날은 극단인 지표가 없다는 뜻입니다 — 그게 정상입니다.
  기준표가 없으면 이 줄은 조용히 빠지고 브리핑은 그대로 나갑니다.
      python percentile_ctx.py --build""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
