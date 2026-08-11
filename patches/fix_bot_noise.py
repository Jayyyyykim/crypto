"""fix_bot_noise.py — ㉔ 없는 심볼 제외 · ㉕ SMMA NaN · ㉖ 레벨 코멘트 모델

    cd /path/to/jaybot
    python fix_bot_noise.py           # 미리보기
    python fix_bot_noise.py --apply   # 적용 (원본은 .bak)
    python fix_bot_noise.py --verify  # 반영됐나

bot.py 를 고칩니다.

────────────────────────────────────────────────────────────

㉔ 이 봇의 거래소 객체로는 못 부르는 심볼이 페이퍼 대상에 있다

    데이터 수집 오류 (SKHYNIX/USDT 1d): bybit does not have market symbol
    데이터 수집 오류 (SAMSUNG/USDT 1d): ...
    데이터 수집 오류 (RKLB/USDT 1d): ...
    데이터 수집 오류 (龙虾/USDT 1d): ...

  SKHYNIX·SAMSUNG·RKLB·AAOI 는 토큰화 주식입니다. 요즘은 코인
  거래소에서도 매매됩니다 — 즉 **가짜 심볼이라 단정할 수 없습니다.**
  확실한 건 하나뿐입니다: 봇이 쓰는 이 거래소 객체가 저 이름으로는
  못 부릅니다. 이유는 셋 중 하나입니다.
      · 이 거래소에 없다
      · 있는데 표기가 다르다 (예: 접미사 X, 1000 접두사)
      · 있는데 이 객체의 시장 종류(선물/현물)에 안 잡힌다

  왜 고치나 — 못 부르는 심볼 하나가 매시간 **4개 타임프레임**씩
  실패하며 로그를 덮습니다. 스무 종이면 여든 줄입니다.
  **그 소음에 진짜 오류가 묻힙니다.** 실제로 이번 로그에서 NaN 경고와
  Claude 오류가 그 사이에 파묻혀 있었습니다.

  → 종목을 이름으로 판단하지 않습니다. **거래소 상장 목록에 대고
    물어봅니다.** 그 이름으로 부를 수 있으면 주식이든 뭐든 둡니다.

    중요한 것은 **거르는 규칙과 부르는 규칙이 같아야 한다**는 것입니다.
    이게 어긋난 게 이 버그의 정체였습니다 — 걸러 주는 쪽만 표기
    차이를 봐주고 부르는 쪽은 그대로 실패하면, 로그는 안 조용해지고
    화면에는 '다 통과했다'고 뜹니다. `fix_cvd_honest.py`(㉘)가
    `market_symbol()` 을 넣으면 이 함수가 그걸 씁니다.

    목록은 한 시간 캐시하고, 목록을 못 받으면 거르지 않고 그대로
    갑니다 — 이 검사 때문에 페이퍼가 멈추면 그건 개선이 아닙니다.

  → 그리고 뺀 것마다 **비슷한 이름이 목록에 있으면 같이 찍습니다.**
    표기만 다른 진짜 종목을 조용히 버리는 게 이 패치의 유일한
    위험이라, 그걸 화면에 남깁니다.

        python fix_bot_noise.py --symbols SKHYNIX,SAMSUNG,RKLB
    로 봇을 안 켜고도 거래소에 뭐가 실려 있는지 볼 수 있습니다.

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


def _near(base, have):
    """목록에서 이름이 비슷한 것 — 표기만 다른 것일 수 있다.

    SKHYNIX·AAOI 같은 토큰화 주식은 코인 거래소에서도 매매된다.
    이름만 보고 '주식이니 가짜'라고 판단하면 안 된다.
    """
    b = base.upper()
    hit = []
    for s in have:
        o = s.split("/")[0].split(":")[0].upper()
        if o != b and (b in o or o in b):
            hit.append(s)
    return sorted(hit)[:3]


def tradable_only(symbols):
    """이 거래소 객체로 실제로 부를 수 있는 심볼만 남긴다.

    종목을 이름으로 판단하지 않는다 — 상장 목록에 대고 물어본다.
    토큰화 주식(SKHYNIX·SAMSUNG·RKLB·AAOI)도 요즘은 코인 거래소에서
    매매된다. 그 이름 그대로 실려 있으면 주식이든 뭐든 둔다.

    못 부르는 심볼 하나가 매시간 4개 타임프레임씩 실패하며 로그를
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

    # 거를 때 쓰는 규칙과 **실제로 부를 때 쓰는 규칙이 같아야 한다.**
    # 이게 어긋난 게 이 버그의 정체였다 — 'SKHYNIX/USDT' 는 목록에
    # 없고 'SKHYNIX/USDT:USDT' 만 있는데, 걸러 주는 쪽만 봐주고
    # 부르는 쪽은 그대로 실패했다.
    #
    # market_symbol() 이 있으면 그걸 쓴다(㉘). 없으면 정확 일치다.
    _resolve = globals().get("market_symbol")

    def listed(s):
        if _resolve:
            return _resolve(s) is not None
        return s in have

    ok = [s for s in symbols if listed(s)]
    gone = [s for s in symbols if not listed(s)]

    # 안전판: 거의 다 떨어지면 목록과 부르는 방식이 안 맞는 것이다
    # (예: 이 객체가 현물을 안 싣는다). 그때 거르면 페이퍼가 멈춘다.
    if symbols and len(ok) < len(symbols) * 0.4:
        print(f"[페이퍼] {len(gone)}/{len(symbols)}종이 목록에 없습니다. "
              "목록과 조회 방식이 안 맞는 것 같아 거르지 않습니다.")
        return symbols

    if gone and set(gone) != _MARKETS["said"]:
        # 매시간 같은 줄을 다시 찍으면 그것도 소음이다. 목록이
        # 바뀔 때만 말한다.
        _MARKETS["said"] = set(gone)
        names = []
        for g in gone[:12]:
            base = g.split("/")[0]
            perp = g + ":USDT"
            alt = perp if perp in have else (_near(base, have) or [None])[0]
            names.append(base + (f"(→{alt}?)" if alt else ""))
        print(f"[페이퍼] 이 거래소 객체로 못 부름, 제외 {len(gone)}종: "
              + ", ".join(names) + (" ..." if len(gone) > 12 else ""))
        if any("→" in n for n in names):
            # 표기만 다른 진짜 종목을 조용히 버리는 게 이 검사의
            # 유일한 위험이다. 화살표가 보이면 그거다.
            print("        └ 화살표는 표기만 다른 것일 수 있습니다. "
                  "맞으면 스캔 심볼을 그 이름으로 고치십시오.")
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


# ── ㉔u 이미 붙인 느슨한 판을 갈아 끼운다 ──
#
# 처음 판은 'SKHYNIX/USDT:USDT' 가 있으면 'SKHYNIX/USDT' 도 봐줬다.
# 종목이 진짜로 거래되니 떨어뜨리기 아깝다고 봤는데 — **거르는 쪽만
# 봐주고 부르는 쪽은 그대로 실패했다.** 두 규칙이 어긋나면 로그는
# 안 조용해지는데 화면에는 '다 통과'로 뜬다.
LOOSE_OLD = '    have = _MARKETS["syms"]\n\n    def listed(s):\n        # 무기한선물은 \'BTC/USDT:USDT\' 로도 실린다. 표기 차이로\n        # 멀쩡한 코인을 떨어뜨리면 이 검사가 손해다.\n        return s in have or (s + ":USDT") in have\n\n    ok = [s for s in symbols if listed(s)]\n    gone = [s for s in symbols if not listed(s)]\n    if gone and set(gone) != _MARKETS["said"]:\n        # 매시간 같은 줄을 다시 찍으면 그것도 소음이다. 목록이\n        # 바뀔 때만 말한다.\n        _MARKETS["said"] = set(gone)\n        names = []\n        for g in gone[:12]:\n            base = g.split("/")[0]\n            alt = _near(base, have)\n            names.append(base + (f"(→{alt[0]}?)" if alt else ""))\n        print(f"[페이퍼] 이 거래소 객체로 못 부름, 제외 {len(gone)}종: "\n              + ", ".join(names) + (" ..." if len(gone) > 12 else ""))'

# 위 UNIV_NEW 에서 그대로 떼어 온다. 손으로 두 벌 적어 두면 언젠가
# 갈라지고, 갈라진 걸 아무도 모른다.
_A = UNIV_NEW.index('    have = _MARKETS["syms"]')
_B = UNIV_NEW.index('        if any("\u2192" in n for n in names):')
TIGHT_NEW = UNIV_NEW[_A:_B].rstrip("\n")
assert TIGHT_NEW in UNIV_NEW and "market_symbol" in TIGHT_NEW


SITES = [
    ("㉔", "페이퍼 유니버스 — 없는 심볼 제외", UNIV_OLD, UNIV_NEW,
     "def tradable_only("),
    ("㉔u", "거르는 규칙과 부르는 규칙을 하나로 (이미 붙인 판만)",
     LOOSE_OLD, TIGHT_NEW, "실제로 부를 때 쓰는 규칙이 같아야"),
    ("㉕", "SMMA 폭 계산 NaN 가드", SMMA_OLD, SMMA_NEW,
     "NaN 비교는 언제나 False라"),
    ("㉖a", "레벨 코멘트 모델 이름", MODEL_OLD, MODEL_NEW,
     '"model"     : "claude-sonnet-4-5",\n            "max_tokens": 300'),
    ("㉖b", "레벨 코멘트 오류 내용 남기기", ERR_OLD, ERR_NEW,
     "[레벨 코멘트] API 오류"),
]


def _near(base, have):
    """이름이 비슷한 시장 — 위 UNIV_NEW 안의 것과 같은 규칙."""
    b = base.upper()
    hit = []
    for s in have:
        o = s.split("/")[0].split(":")[0].upper()
        if o != b and (b in o or o in b):
            hit.append(s)
    return sorted(hit)[:3]


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


def lookup(names, ex_name="bybit"):
    """거래소에 이 이름들이 실제로 실려 있나 — 봇을 안 켜고 본다.

    토큰화 주식이 코인 거래소에서 매매되는 지금, '이건 주식이니까
    가짜'라는 판단은 틀린다. 목록에 물어보는 수밖에 없다.
    """
    print("=" * 62)
    print(f"  {ex_name} 상장 목록 조회")
    print("=" * 62)
    try:
        import ccxt
    except ImportError:
        print("\n  ccxt 가 없습니다. 봇 폴더에서 실행하십시오.")
        return 1
    try:
        ex = getattr(ccxt, ex_name)()
        have = list(ex.load_markets().keys())
    except Exception as e:
        print(f"\n  목록을 못 받았습니다: {type(e).__name__}: {e}")
        return 1

    print(f"  실린 시장 {len(have)}개\n")
    for n in names:
        base = n.split("/")[0].strip().upper()
        plain = f"{base}/USDT"
        if plain in have:
            print(f"    ✅ {base:<10} {plain}")
            continue
        # 이름은 있는데 표기가 다르다 — 봇은 그 이름으로 못 부른다.
        same = sorted(s for s in have
                      if s.split("/")[0].split(":")[0].upper() == base)
        if same:
            print(f"    ⚠️  {base:<10} '{plain}' 는 없고: {', '.join(same[:3])}")
            continue
        alt = _near(base, have)
        if alt:
            print(f"    ⚠️  {base:<10} 그 이름은 없고, 비슷한 것: "
                  f"{', '.join(alt)}")
        else:
            print(f"    ❌ {base:<10} 목록에 없음")
    print("\n  ✅ 만 페이퍼가 그대로 씁니다. ⚠️ 는 그 이름으로는 못 부르므로")
    print("     제외되고, 화살표로 실제 표기를 알려 줍니다.")
    print("=" * 62)
    return 0


def main(argv):
    apply = "--apply" in argv
    verify = "--verify" in argv

    for i, a in enumerate(argv):
        if a == "--symbols" and i + 1 < len(argv):
            return lookup([x for x in argv[i + 1].split(",") if x.strip()])
        if a.startswith("--symbols="):
            return lookup([x for x in a.split("=", 1)[1].split(",") if x.strip()])

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

      [페이퍼] 이 거래소 객체로 못 부름, 제외 21종: SKYAI, NIL,
              AAOI(→AAOIX/USDT?), ...

  화살표가 붙은 것은 **표기만 다른 진짜 종목**일 수 있습니다.
  토큰화 주식도 요즘은 코인 거래소에서 매매되므로, 이름만 보고
  가짜라고 판단하지 않습니다. 직접 확인하려면:

      python fix_bot_noise.py --symbols SKHYNIX,SAMSUNG,RKLB,AAOI""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
