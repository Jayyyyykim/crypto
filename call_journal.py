"""
call_journal.py — 발행 기록장 (한 번 적으면 못 고치는 로그)

MarketSurfer의 '기록으로 남습니다' 섹션을 봇으로 옮긴 모듈.
분석 문서 표에 안 들어간 화면인데, 다시 보니 **사이트에서 가장 값진 부분**이었다.

사이트가 화면에 써 둔 것
──────────────────────
  "발행한 글은 지우지 않습니다 — 실제로 내보낸 브리핑과 분석을 7일 뒤
   전문 그대로 공개합니다. 요약이나 발췌가 아니라 구독자가 받은 원문입니다."

  "채점 기준을 먼저 적어 둡니다 — 무엇을·며칠 뒤·어떤 기준으로 채점하는지
   38종 전부 적어 두었습니다. 결과가 나온 뒤에 기준을 바꾸지 않기 위해
   먼저 공개하는 것입니다."

  "성과 수치는 아직 싣지 않습니다. 충분한 기간이 쌓여야 한 줄의 숫자가
   근거가 되겠습니다."

왜 이게 중요한가
──────────────
봇 성과를 자기가 평가할 때 가장 흔한 실패는 거짓말이 아니라 **기억의 편집**이다.

  · 맞은 신호는 스크린샷이 남고, 틀린 신호는 조용히 지나간다.
  · "이건 조건이 좀 달랐지"가 사후에 붙어 표본에서 빠진다.
  · 채점 기준이 결과를 본 뒤에 미세하게 바뀐다 (보합 밴드를 1%에서 2%로…).

setup_ledger.py가 셋업을 정직하게 채점해도, **무엇을 채점 대상으로 넣을지**를
사후에 고를 수 있으면 표 전체가 무의미해진다. 이 모듈은 그 구멍을 막는다.

어떻게 막나 — 해시 사슬
────────────────────
각 항목에 앞 항목의 SHA-256을 넣어 사슬로 잇는다. 중간을 하나라도 고치거나
지우면 그 뒤가 전부 어긋나고, verify()가 **몇 번째에서 끊겼는지** 짚어 준다.

말로 하는 "안 고칠게요"와 다른 점은, 고쳤을 때 **들킨다**는 것이다.
스스로에게도 마찬가지다 — 이게 진짜 목적이다.

jsonstore를 안 쓰는 이유
──────────────────────
jsonstore.save()는 파일 전체를 갈아끼운다(tmp → replace). 그건 append-only와
정반대다. 여기서는 JSONL에 한 줄씩 덧붙이기만 하고, 기존 줄은 건드리지 않는다.
파일 잠금이 없어도 append 한 줄은 사실상 원자적이라 그 편이 안전하기도 하다.
"""

import hashlib
import json
import os
from datetime import datetime
import console

JOURNAL_FILE = "call_journal.jsonl"

# 사슬의 시작점. 첫 항목의 prev 가 이 값이다.
GENESIS = "0" * 64

# 항목 종류
KIND_CRITERIA = "채점기준"   # 먼저 박아 두는 규칙
KIND_CALL = "발행"           # 실제로 내보낸 판단
KIND_SCORE = "채점"          # 그 판단의 결과 (원본을 고치지 않고 새 줄로)
KIND_NOTE = "메모"


# ================================
# 🔗 해시
# ================================

def _digest(prev, body):
    """prev + 정규화한 body → SHA-256.

    sort_keys=True 가 핵심이다. 파이썬 dict 순서가 바뀌어도 같은 내용이면
    같은 해시가 나와야, "내용은 그대로인데 사슬이 깨졌다"는 오검출이 없다.
    """
    blob = prev + json.dumps(body, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _body(seq, at, kind, payload):
    return {"seq": seq, "at": at, "kind": kind, "payload": payload}


# ================================
# 💾 읽기 · 쓰기
# ================================

def load(path=JOURNAL_FILE):
    """전체 항목. 깨진 줄은 건너뛰지 않고 그대로 예외를 낸다 —
    기록장은 조용히 일부를 잃으면 안 되는 파일이다."""
    if not os.path.exists(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def append(kind, payload, path=JOURNAL_FILE, at=None):
    """항목 하나를 사슬 끝에 붙인다."""
    entries = load(path)
    prev = entries[-1]["hash"] if entries else GENESIS
    body = _body(len(entries), at or datetime.now().isoformat(timespec="seconds"),
                 kind, payload)
    entry = dict(body, prev=prev, hash=_digest(prev, body))

    # newline="" 로 줄바꿈 변환을 끈다. 안 그러면 윈도우에서 CRLF로 저장돼
    # 같은 기록장이 OS마다 다른 바이트가 된다. 해시는 내용으로 계산하므로
    # 사슬 자체는 어느 쪽이든 깨지지 않지만, "한 번 적으면 안 바뀐다"는
    # 파일을 OS 사이에 옮겼다고 바이트가 달라지면 곤란하다.
    with open(path, "a", encoding="utf-8", newline="") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


# ================================
# ✍️ 기록
# ================================

def register_criteria(criteria, path=JOURNAL_FILE):
    """
    채점 기준을 *먼저* 박아 둔다.

    setup_ledger의 FLAT_BAND, DEFAULT_HORIZONS, min_samples 같은 값을
    통째로 넣어 두면 된다. 나중에 이 값을 바꾸고 싶어지면 — 그게 정확히
    이 모듈이 잡으려는 순간이다. 바꾸는 것 자체는 막지 않지만,
    **새 항목으로** 들어가서 언제 무엇을 왜 바꿨는지가 남는다.
    """
    return append(KIND_CRITERIA, criteria, path)


def publish(symbol, direction, price, reason, setups=None,
            invalidation=None, path=JOURNAL_FILE, at=None):
    """
    판단 하나를 발행한다. 발행 *시점의* 근거를 통째로 남기는 게 요점이다.

    나중에 "왜 이걸 샀지"가 아니라 "그때 나는 이렇게 봤고 그게 맞았나/틀렸나"를
    따질 수 있어야 채점이 학습이 된다.
    """
    return append(KIND_CALL, {
        "종목"   : symbol,
        "방향"   : "상승" if direction in (1, "up", "long") else "하락",
        "발행가" : price,
        "근거"   : reason,
        "셋업"   : list(setups or []),
        "무효화" : invalidation,
    }, path, at)


def record_score(call_seq, horizon, result, price, path=JOURNAL_FILE, at=None):
    """
    채점 결과. 원본 발행을 **고치지 않고** 새 항목으로 잇는다.

    이게 append-only의 실질이다. 결과를 원본에 써 넣으면 그 줄의 해시가
    바뀌고 사슬이 끊긴다 — 구조가 편집을 물리적으로 거부한다.
    """
    return append(KIND_SCORE, {
        "발행_seq": call_seq,
        "시점"    : horizon,
        "결과"    : result,
        "채점가"  : price,
    }, path, at)


def note(text, path=JOURNAL_FILE, at=None):
    return append(KIND_NOTE, {"내용": text}, path, at)


# ================================
# 🔍 검증
# ================================

def verify(path=JOURNAL_FILE):
    """
    사슬이 온전한지. (ok, 메시지)

    깨졌으면 몇 번째에서 깨졌는지, 어떤 종류로 깨졌는지 구분해서 알려준다 —
    "고쳐졌다"와 "지워졌다"는 대응이 다르기 때문이다.
    """
    entries = load(path)
    prev = GENESIS
    for i, e in enumerate(entries):
        if e.get("seq") != i:
            return False, f"{i}번째 항목의 seq가 {e.get('seq')}입니다 — 중간이 지워졌습니다."
        if e.get("prev") != prev:
            return False, f"{i}번째 항목이 앞 항목과 이어지지 않습니다 — 앞부분이 고쳐졌습니다."
        recomputed = _digest(e["prev"], _body(e["seq"], e["at"], e["kind"], e["payload"]))
        if recomputed != e.get("hash"):
            return False, f"{i}번째 항목의 내용이 발행 뒤에 바뀌었습니다."
        prev = e["hash"]
    return True, f"{len(entries)}건, 사슬 정상."


def calls(path=JOURNAL_FILE):
    return [e for e in load(path) if e["kind"] == KIND_CALL]


def scores_for(call_seq, path=JOURNAL_FILE):
    return [e for e in load(path)
            if e["kind"] == KIND_SCORE and e["payload"].get("발행_seq") == call_seq]


def criteria_history(path=JOURNAL_FILE):
    """
    채점 기준이 몇 번 바뀌었는지. 두 번 이상이면 그 자체가 신호다.

    기준을 결과 본 뒤에 바꿨다면, 바뀐 시점 **이후** 표본만 그 기준으로
    유효하다. 그걸 알려면 언제 바뀌었는지가 기록에 남아 있어야 한다.
    """
    return [e for e in load(path) if e["kind"] == KIND_CRITERIA]


def pending_calls(horizons, path=JOURNAL_FILE):
    """아직 전 시점이 채점되지 않은 발행."""
    scored = {}
    for e in load(path):
        if e["kind"] == KIND_SCORE:
            seq = e["payload"].get("발행_seq")
            scored.setdefault(seq, set()).add(str(e["payload"].get("시점")))
    out = []
    for c in calls(path):
        done = scored.get(c["seq"], set())
        missing = [h for h in horizons if str(h) not in done]
        if missing:
            out.append({"call": c, "missing": missing})
    return out


# ================================
# 📊 집계
# ================================

def tally(horizon, path=JOURNAL_FILE, min_samples=30):
    """
    발행 기준 적중률. setup_ledger의 사건 채점과 **별개**로 센다.

    사건 채점은 표본이 크지만 "봇이 실제로 내보낸 것"은 아니다.
    실제 발행분은 표본이 작은 대신 그게 진짜 성적이다. 둘을 섞으면
    어느 쪽 숫자인지 알 수 없게 되므로 이 모듈은 발행분만 센다.
    """
    hits = misses = flats = 0
    for e in load(path):
        if e["kind"] != KIND_SCORE or str(e["payload"].get("시점")) != str(horizon):
            continue
        r = e["payload"].get("결과")
        if r in ("hit", "적중"):
            hits += 1
        elif r in ("miss", "미적중"):
            misses += 1
        else:
            flats += 1

    decided = hits + misses
    return {
        "horizon"    : horizon,
        "hit"        : hits,
        "miss"       : misses,
        "flat"       : flats,
        "decided"    : decided,
        "hit_rate"   : (hits / decided) if decided else None,
        # 사이트와 같은 원칙 — 표본이 차기 전엔 수치를 내지 않는다.
        "publishable": decided >= min_samples,
        "min_samples": min_samples,
    }


# ================================
# 📋 리포트
# ================================

def get_report(horizon=3, path=JOURNAL_FILE, limit=10):
    """텔레그램 리포트 — 사이트의 '기록으로 남습니다' 카드에 대응."""
    ok, msg = verify(path)
    entries = load(path)
    cs = calls(path)
    crit = criteria_history(path)

    lines = [
        "🧾 <b>발행 기록장</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"{'✅' if ok else '🚨'} {msg}",
        f"총 {len(entries)}줄 · 발행 {len(cs)}건",
    ]

    if not crit:
        lines += [
            "",
            "⚠️ <b>채점 기준이 아직 없습니다.</b>",
            "  기준을 먼저 박아 두지 않으면 나중에 결과를 보고",
            "  기준을 고쳐도 아무도 모릅니다.",
            "  <code>call_journal.register_criteria({...})</code>",
        ]
    elif len(crit) > 1:
        lines += [
            "",
            f"⚠️ 채점 기준이 <b>{len(crit)}번</b> 바뀌었습니다.",
            f"  최초 {crit[0]['at'][:10]} · 최근 {crit[-1]['at'][:10]}",
            "  바뀐 시점 이후 표본만 지금 기준으로 유효합니다.",
        ]

    t = tally(horizon, path)
    lines += ["", f"<b>발행 적중률 ({horizon} 시점)</b>"]
    if t["publishable"]:
        lines.append(
            f"  {t['hit_rate']*100:.1f}%  "
            f"(적중 {t['hit']} / 미적중 {t['miss']} / 보합 {t['flat']})"
        )
    else:
        lines += [
            f"  판정 {t['decided']}건 — 아직 싣지 않습니다.",
            f"  표본 {t['min_samples']}건이 차야 한 줄의 숫자가 근거가 됩니다.",
        ]

    if cs:
        lines += ["", "<b>최근 발행</b>"]
        for c in cs[-limit:]:
            p = c["payload"]
            done = scores_for(c["seq"], path)
            mark = "·".join(s["payload"]["결과"] for s in done) or "채점 대기"
            lines.append(
                f"  #{c['seq']} {c['at'][:16]} {p['종목']} {p['방향']} "
                f"@{p['발행가']:,.6g} → {mark}"
            )

    lines += [
        "━━━━━━━━━━━━━━━━━━━━",
        "※ 각 줄은 앞 줄의 해시를 품습니다. 하나만 고쳐도 뒤가 전부 어긋납니다.",
    ]
    if not ok:
        lines.append("🚨 사슬이 손상됐습니다 — 위 숫자를 근거로 쓰지 마십시오.")
    return "\n".join(lines)


def export_plaintext(path=JOURNAL_FILE, days=7):
    """
    사이트처럼 '발행 원문 그대로' 내보내기.

    요약하지 않는다. 요약하는 순간 무엇을 뺄지 고르게 되고,
    그게 정확히 이 기록장이 막으려던 것이다.
    """
    out = []
    for c in calls(path):
        p = c["payload"]
        block = [
            f"[#{c['seq']}] {c['at']}",
            f"{p['종목']} {p['방향']} @ {p['발행가']}",
            f"근거: {p['근거']}",
        ]
        if p.get("셋업"):
            block.append(f"셋업: {', '.join(p['셋업'])}")
        if p.get("무효화") is not None:
            block.append(f"무효화: {p['무효화']}")
        for s in scores_for(c["seq"], path):
            sp = s["payload"]
            block.append(f"  └ {sp['시점']} → {sp['결과']} @ {sp['채점가']}")
        out.append("\n".join(block))
    return "\n\n".join(out)


def reset(path=JOURNAL_FILE):
    """기록장을 통째로 지운다.

    일부러 다른 함수들과 다르게 만들었다 — 부분 삭제는 아예 제공하지 않는다.
    전부 지우는 것은 흔적이 남지만(파일이 비어 있다), 한 줄만 지우는 것은
    안 남는다. 그래서 후자를 코드로 열어 두지 않는다.
    """
    if os.path.exists(path):
        os.remove(path)
    console.say("🗑️ 발행 기록장 초기화 (전체 삭제만 가능합니다)")
