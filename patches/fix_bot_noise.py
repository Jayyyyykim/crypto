"""fix_bot_noise.py — ㉔ 없는 심볼 제외 · ㉕ SMMA NaN · ㉖ 레벨 코멘트 모델

    cd /path/to/jaybot
    python fix_bot_noise.py           # 미리보기
    python fix_bot_noise.py --apply   # 적용 (원본은 .bak)
    python fix_bot_noise.py --verify  # 반영됐나

bot.py 를 고칩니다.

────────────────────────────────────────────────────────────

㉔ 주식 심볼이 페이퍼 대상에 들어가 있다

    데이터 수집 오류 (SKHYNIX/USDT 1d): bybit does not have market symbol
    데이터 수집 오류 (SAMSUNG/USDT 1d): ...
    데이터 수집 오류 (RKLB/USDT 1d): ...
    데이터 수집 오류 (龙虾/USDT 1d): ...

  SKHYNIX·SAMSUNG·RKLB·AAOI·SPCX·DRAM 은 **주식**입니다. 스캔 캐시에
  다른 시장 심볼이 섞여 들어와 그대로 페이퍼 유니버스에 올라갔습니다.

  이게 왜 문제인가 — 없는 심볼 하나가 매시간 **4개 타임프레임**씩
  조회에 실패하며 로그를 덮습니다. 스무 종이면 여든 줄입니다.
  **그 소음에 진짜 오류가 묻힙니다.** 실제로 이번 로그에서 NaN 경고와
  Claude 오류가 그 사이에 파묻혀 있었습니다.

  → 거래소가 실제로 상장한 목록으로 한 번 거릅니다. 목록은 한 시간
    캐시합니다. 목록을 못 받으면 거르지 않고 그대로 갑니다 —
    이 검사 때문에 페이퍼가 멈추면 그건 개선이 아닙니다.

㉕ SMMA 폭 계산에 NaN 이 들어간다

    bot.py:3169: RuntimeWarning: invalid value encountered in scalar divide
      spread_pct = round((max(vals) - min(vals)) / current * 100, 2)

  smma 값에 NaN 이 섞이면 폭도 NaN 이고, 그 위의 **정배열/역배열 판정도
  전부 거짓**이 됩니다(NaN 비교는 항상 False라 '혼조'로 떨어집니다).
  경고만 시끄러운 게 아니라 판정이 조용히 틀립니다.

  → NaN 을 걸러내고, 남은 것이 넷 미만이거나 현재가가 이상하면
    None 을 돌려줍니다. 호출부는 이미 None 을 다룹니다.

㉖ 레벨 코멘트만 모델 이름이 다르다

    Claude 레벨 코멘트 오류: 'content'

  `get_claude_level_comment()` 만 "claude-sonnet-4-20250514" 를 쓰고,
  나머지(`ask_claude`·`ask_claude_conversational`·`ask_claude_simple`)는
  "claude-sonnet-4-5" 를 씁니다. 그쪽은 잘 돕니다.

  그리고 오류 처리가 'content' KeyError 만 찍어서 **저쪽이 무슨 말을
  했는지 알 수 없습니다.** 응답의 error 를 먼저 보고 그대로 남깁니다.

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


# ── ㉔ 페이퍼 유니버스를 거래소 상장 목록으로 거른다 ──
UNIV_OLD = '''def get_paper_coins():
    """최신 전 종목 스캔 결과에서 페이퍼 대상만 동적으로 선별."""
    return select_paper_universe(
        PAPER_COINS,
        max_coins=PAPER_MAX_COINS,
        min_usdt_volume=PAPER_MIN_USDT_VOLUME,
    )'''

UNIV_NEW = '''_MARKETS = {"at": 0.0, "syms": set(), "said": set()}


def tradable_only(symbols):
    """이 거래소에 실제로 있는 심볼만 남긴다.

    스캔 캐시에 다른 시장 것이 섞여 들어온다 — SKHYNIX·SAMSUNG·
    RKLB·AAOI(주식)와 龙虾 까지 페이퍼 대상에 올라와 있었다.
    없는 심볼 하나가 매시간 4개 타임프레임씩 조회에 실패하며 로그를
    덮는다. 스무 종이면 여든 줄이고, **그 소음에 진짜 오류가 묻힌다.**

    상장 목록을 못 받으면 거르지 않고 그대로 간다. 이 검사 때문에
    페이퍼가 멈추면 그건 개선이 아니다.
    """
    import time as _t
    now = _t.time()
    if now - _MARKETS["at"] > 3600 or not _MARKETS["syms"]:
        try:
            _MARKETS["syms"] = set(exchange.load_markets().keys())
            _MARKETS["at"] = now
        except Exception as e:
            print(f"[페이퍼] 상장 목록을 못 받아 그대로 갑니다: {e}")
            return symbols
    have = _MARKETS["syms"]

    def listed(s):
        # 무기한선물은 'BTC/USDT:USDT' 로도 실린다. 표기 차이로
        # 멀쩡한 코인을 떨어뜨리면 이 검사가 손해다.
        return s in have or (s + ":USDT") in have

    ok = [s for s in symbols if listed(s)]
    gone = [s for s in symbols if not listed(s)]
    if gone and set(gone) != _MARKETS["said"]:
        # 매시간 같은 줄을 다시 찍으면 그것도 소음이다. 목록이
        # 바뀔 때만 말한다.
        _MARKETS["said"] = set(gone)
        print(f"[페이퍼] 거래소에 없어 제외 {len(gone)}종: "
              + ", ".join(g.replace("/USDT", "") for g in gone[:12])
              + (" ..." if len(gone) > 12 else ""))
    return ok


def get_paper_coins():
    """최신 전 종목 스캔 결과에서 페이퍼 대상만 동적으로 선별."""
    return tradable_only(select_paper_universe(
        PAPER_COINS,
        max_coins=PAPER_MAX_COINS,
        min_usdt_volume=PAPER_MIN_USDT_VOLUME,
    ))'''


# ── ㉕ SMMA 폭 계산 NaN ──
SMMA_OLD = """    vals    = list(smma_vals.values())
    current = round(close.iloc[-1], 2)

    sorted_desc = all(vals[i] >= vals[i+1] for i in range(len(vals)-1))"""

SMMA_NEW = """    vals    = list(smma_vals.values())
    current = round(close.iloc[-1], 2)

    # NaN 이 섞이면 폭이 NaN 이 될 뿐 아니라 **정렬 판정이 조용히
    # 틀린다** — NaN 비교는 언제나 False라 무조건 '혼조'로 떨어진다.
    # 경고만 시끄러운 게 아니라 결론이 바뀐다.
    vals = [v for v in vals if v == v]
    if len(vals) < 4 or current != current or current <= 0:
        return None

    sorted_desc = all(vals[i] >= vals[i+1] for i in range(len(vals)-1))"""


# ── ㉖ 레벨 코멘트 모델 이름 + 오류 내용 ──
MODEL_OLD = '''        body = {
            "model"     : "claude-sonnet-4-20250514",
            "max_tokens": 300,
            "messages"  : [{"role": "user", "content": prompt}],
        }'''

MODEL_NEW = '''        # 다른 호출부(ask_claude·ask_claude_conversational·
        # ask_claude_simple)는 전부 이 이름을 쓰고 잘 돈다. 여기만
        # 옛 이름이 남아 있어서 'content' KeyError 로 죽고 있었다.
        body = {
            "model"     : "claude-sonnet-4-5",
            "max_tokens": 300,
            "messages"  : [{"role": "user", "content": prompt}],
        }'''

ERR_OLD = """        result = r.json()
        comment = result['content'][0]['text']"""

ERR_NEW = """        result = r.json()
        if 'error' in result:
            # 'content' KeyError 만 찍으면 저쪽이 무슨 말을 했는지
            # 알 수 없다. 응답을 그대로 남긴다.
            _e = result['error']
            print(f"[레벨 코멘트] API 오류 {_e.get('type','')}: "
                  f"{_e.get('message','')}")
            return None
        comment = result['content'][0]['text']"""


SITES = [
    ("㉔", "페이퍼 유니버스 — 없는 심볼 제외", UNIV_OLD, UNIV_NEW,
     "def tradable_only("),
    ("㉕", "SMMA 폭 계산 NaN 가드", SMMA_OLD, SMMA_NEW,
     "NaN 비교는 언제나 False라"),
    ("㉖a", "레벨 코멘트 모델 이름", MODEL_OLD, MODEL_NEW,
     '"model"     : "claude-sonnet-4-5",\n            "max_tokens": 300'),
    ("㉖b", "레벨 코멘트 오류 내용 남기기", ERR_OLD, ERR_NEW,
     "[레벨 코멘트] API 오류"),
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
    print("  ㉔ 없는 심볼 제외 · ㉕ SMMA NaN · ㉖ 레벨 코멘트")
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
            ok = marker in src
            print(f"    {'✅' if ok else '□'} {tag} {label}")
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
        # 한 군데도 못 찾으면 봇 판이 다른 것이다. 조용히 성공한
        # 척하면 사람은 고쳐진 줄 안다.
        print("\n  붙일 자리를 하나도 못 찾았습니다. 봇 판이 다릅니다.")
        print("  bot.py 를 그대로 보내 주시면 자리를 다시 맞추겠습니다.")
        return 1

    if not apply:
        print("\n" + "=" * 62)
        if todo:
            print(f"  {todo}개를 고칠 수 있습니다."
                  "  적용:  python fix_bot_noise.py --apply")
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

  다음 페이퍼 스캔(매시)에 이런 줄이 한 번 뜨고, 그 뒤로 조용해집니다.

      [페이퍼] 거래소에 없어 제외 21종: SKYAI, NIL, KAITO, AAOI, ...

  그 목록에 **진짜 코인**이 섞여 있으면 알려 주십시오 — 심볼 표기가
  다른 것일 수 있습니다(예: 1000PEPE).""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
