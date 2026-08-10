"""alert_parse.py — 남의 알림 스트림을 거래로 되돌린다

    python alert_parse.py 내보낸대화.txt
    python alert_parse.py 내보낸대화.txt --signals   # 시험대용 파일로 저장

왜 필요한가
──────────
알림 피드는 **메시지 수**로 성과를 말한다. 우리가 봐야 하는 건
**거래 수**와 **R**이다. 이 셋은 전혀 다른 이야기를 한다.

    포지션 하나(DRIFT 롱)  →  메시지 6개 (진입·SR1·SR2·본절·SR3·SR4·SR5)
    포지션 하나(변동성숏)  →  메시지 2개 (손절·종료)

이기면 6개, 지면 2개. 피드는 초록으로 도배되는데 R 합계는 마이너스일
수 있다. 사람 눈으로는 절대 안 세어진다.

같은 레벨이 두 번 수확된다
─────────────────────────
실제로 본 것:

    08-02 16:46  PEPE SR3  익절 $0.00000296
    08-10 07:47  PEPE SR3  익절 $0.00000296   ← 같은 레벨 또

    07:47  DRIFT SR3  익절 $0.0141
    08:31  DRIFT SR4  익절 $0.0134   ← SR3보다 **싸게** 팔림
    10:59  DRIFT SR5  익절 $0.0141   ← 다시 위

가격이 같은 구간을 오가며 지날 때마다 5%씩 팔고 축하 메시지를 하나씩
만든다. '🔥 11회 연속 목표 도달' 은 거래 11번이 아니다.
이 도구는 그 재방문을 따로 센다.

무엇을 내놓나
────────────
  · 메시지 수 vs **포지션 수**
  · 포지션별 실현 R (알림이 적어 준 R을 그대로 쓴다)
  · 닫힌 거래의 R 분포 — 이긴 것과 진 것의 크기
  · 같은 레벨 재도달 횟수
  · --signals 로 {코인·방향·진입·손절·날짜} 를 뽑아 저장 →
    signal_bench 와 같은 방식으로 **무작위 진입과 겨루게** 할 수 있다

무엇을 못 하나
─────────────
알림에 안 적힌 것은 모른다. 특히 **열려 있는 포지션의 최종 결과**는
알 수 없다. 지금 +0.15R 인 포지션이 나중에 +2R 이 될 수도, 본절로
0R 이 될 수도 있다. 그래서 닫힌 것과 열린 것을 갈라서 적는다.
"""

import json
import os
import re
import sys

SIGNALS = "alert_signals.jsonl"

# [2026-08-10 오전 7:47] 보낸이: 내용
STAMP = re.compile(r"^\[(\d{4}-\d{2}-\d{2})\s+(오전|오후)\s*(\d{1,2}):(\d{2})\]\s*[^:]*:\s*(.*)$")

# 🟢 DRIFT 롱 진입 알림
ENTRY_HEAD = re.compile(r"[🟢🔴]\s*([A-Z0-9]{2,15})\s*(롱|숏)\s*진입 알림")
ENTRY_BODY = re.compile(r"진입가\s*\$([\d.]+)\s*·\s*손절\s*\$([\d.]+)")

# 💰 PEPE 롱 SR3 도달!
TP_HEAD = re.compile(r"([A-Z0-9]{2,15})\s*(롱|숏)\s*SR(\d+)\s*도달")
TP_PRICE = re.compile(r"진입\s*\$([\d.]+)\s*→\s*익절\s*\$([\d.]+)")
TP_ACC = re.compile(r"실현 누적\s*([+-][\d.]+)%\s*\(([+-][\d.]+)R\)")
TP_LEFT = re.compile(r"잔여\s*([\d.]+)%")

# 🔴 변동성 밴드 숏 알림 종료  /  결과  -13.24% (-1.0R)  |  손절  |  4일 16시간
CLOSE_HEAD = re.compile(r"[🟢🔴]?\s*(.+?)\s*(롱|숏)\s*알림 종료")
CLOSE_BODY = re.compile(r"결과\s*([+-][\d.]+)%\s*\(([+-][\d.]+)R\)\s*\|\s*([^|]+)")

STREAK = re.compile(r"🔥\s*(\d+)회 연속 목표 도달")


def _f(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def read_blocks(path):
    """내보낸 대화를 (날짜, 시각, 본문) 덩어리로 나눈다.

    한 메시지가 여러 줄이므로 새 시각표가 나올 때까지 이어 붙인다.
    """
    out = []
    cur = None
    with open(path, encoding="utf-8", errors="replace") as fp:
        for line in fp:
            line = line.rstrip("\n")
            m = STAMP.match(line)
            if m:
                if cur:
                    out.append(cur)
                date, ampm, hh, mm, rest = m.groups()
                h = int(hh) % 12 + (12 if ampm == "오후" else 0)
                cur = {"date": date, "time": f"{h:02d}:{mm}", "text": rest}
            elif cur is not None:
                cur["text"] += "\n" + line
    if cur:
        out.append(cur)
    return out


def classify(block):
    """메시지 하나를 뜯는다. 못 알아보면 None."""
    t = block["text"]

    m = ENTRY_HEAD.search(t)
    if m:
        b = ENTRY_BODY.search(t)
        return {"kind": "entry", "coin": m.group(1), "side": m.group(2),
                "entry": _f(b.group(1)) if b else None,
                "stop": _f(b.group(2)) if b else None,
                "date": block["date"], "time": block["time"]}

    m = TP_HEAD.search(t)
    if m:
        p, a, l = TP_PRICE.search(t), TP_ACC.search(t), TP_LEFT.search(t)
        s = STREAK.search(t)
        return {"kind": "tp", "coin": m.group(1), "side": m.group(2),
                "sr": int(m.group(3)),
                "entry": _f(p.group(1)) if p else None,
                "exit": _f(p.group(2)) if p else None,
                "acc_pct": _f(a.group(1)) if a else None,
                "acc_r": _f(a.group(2)) if a else None,
                "left": _f(l.group(1)) if l else None,
                "streak": int(s.group(1)) if s else None,
                "date": block["date"], "time": block["time"]}

    m = CLOSE_HEAD.search(t)
    if m:
        b = CLOSE_BODY.search(t)
        return {"kind": "close", "coin": m.group(1).strip(), "side": m.group(2),
                "pct": _f(b.group(1)) if b else None,
                "r": _f(b.group(2)) if b else None,
                "why": b.group(3).strip() if b else "",
                "date": block["date"], "time": block["time"]}
    return None


def build(events):
    """이벤트를 **포지션**으로 묶는다.

    같은 코인·같은 방향·같은 진입가면 한 포지션으로 본다. 진입가가
    같은 것을 기준으로 삼는 이유는, 알림이 매번 진입가를 다시
    적어 주기 때문이다 — 그게 포지션의 신분증이다.
    """
    pos = {}
    order = []
    for e in events:
        if e["kind"] == "close":
            key = ("close", e["coin"], e["side"])
            p = pos.get(key)
            if p is None:
                p = {"coin": e["coin"], "side": e["side"], "entry": None,
                     "stop": None, "tps": [], "closed": None,
                     "opened": f"{e['date']} {e['time']}"}
                pos[key] = p
                order.append(key)
            p["closed"] = e
            continue

        key = (e["coin"], e["side"], e.get("entry"))
        p = pos.get(key)
        if p is None:
            p = {"coin": e["coin"], "side": e["side"], "entry": e.get("entry"),
                 "stop": e.get("stop"), "tps": [], "closed": None,
                 "opened": f"{e['date']} {e['time']}"}
            pos[key] = p
            order.append(key)
        if e["kind"] == "entry":
            p["stop"] = e.get("stop") or p["stop"]
            p["opened"] = f"{e['date']} {e['time']}"
        else:
            p["tps"].append(e)

    # 손절 메시지는 진입가를 다시 적어 주기도 한다 — 그때는 이어 붙인다.
    return [pos[k] for k in order]


def revisits(p):
    """같은 SR 번호가 두 번 이상 떴나. '연속 목표 도달' 의 정체."""
    seen, again = set(), 0
    for t in p["tps"]:
        if t["sr"] in seen:
            again += 1
        seen.add(t["sr"])
    return again


def realized_r(p):
    """알림이 적어 준 실현 누적 R 중 마지막 값."""
    rs = [t["acc_r"] for t in p["tps"] if t["acc_r"] is not None]
    return rs[-1] if rs else 0.0


def w(text, width, right=False):
    import unicodedata
    n = sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in str(text))
    pad = " " * max(0, width - n)
    return (pad + str(text)) if right else (str(text) + pad)


def report(blocks):
    events = [e for e in (classify(b) for b in blocks) if e]
    positions = build(events)
    kinds = {}
    for e in events:
        kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1

    print("=" * 76)
    print("  알림 스트림 → 거래로 되돌리기")
    print("=" * 76)
    print(f"  메시지 {len(blocks):,}개 중 알아본 것 {len(events):,}개"
          f"  →  **포지션 {len(positions)}개**")
    print(f"    진입 알림 {kinds.get('entry', 0)} · 부분익절 {kinds.get('tp', 0)}"
          f" · 종료 {kinds.get('close', 0)}")

    if not positions:
        print("\n  포지션을 하나도 못 찾았습니다. 내보낸 형식이 다를 수 있습니다.")
        print("  텔레그램 → 대화 내보내기 → 'Machine-readable JSON' 말고")
        print("  사람이 읽는 텍스트(.txt)로 내보내면 이 도구가 읽습니다.")
        return 1

    closed = [p for p in positions if p["closed"]]
    openp = [p for p in positions if not p["closed"]]

    print("\n  " + w("코인", 10) + w("방향", 6) + w("상태", 8)
          + w("부분익절", 10, True) + w("재방문", 8, True) + w("실현R", 9, True))
    print("  " + "─" * 62)
    for p in positions:
        c = p["closed"]
        st = "닫힘" if c else "열림"
        r = c["r"] if (c and c.get("r") is not None) else realized_r(p)
        print("  " + w(p["coin"], 10) + w(p["side"], 6) + w(st, 8)
              + w(len(p["tps"]), 10, True) + w(revisits(p) or "", 8, True)
              + w(f"{r:+.2f}", 9, True))
    print("  " + "─" * 62)

    # ── 닫힌 것만이 결론을 낼 수 있다 ──
    rs = [p["closed"]["r"] for p in closed if p["closed"].get("r") is not None]
    print(f"\n  [닫힌 거래 {len(rs)}건]  — 결론은 이쪽으로만 낼 수 있다")
    if rs:
        wins = [r for r in rs if r > 0]
        loss = [r for r in rs if r <= 0]
        print(f"    합계 {sum(rs):+.2f}R · 거래당 {sum(rs) / len(rs):+.3f}R")
        print(f"    이긴 {len(wins)}건 평균 {sum(wins) / len(wins):+.2f}R"
              if wins else "    이긴 거래 없음")
        print(f"    진 {len(loss)}건 평균 {sum(loss) / len(loss):+.2f}R"
              if loss else "    진 거래 없음")
        if wins and loss:
            need = -sum(loss) / len(loss) / (sum(wins) / len(wins) - sum(loss) / len(loss))
            print(f"    → 이 크기 비율이면 **승률 {need * 100:.0f}%** 는 나와야 본전")
    else:
        print("    닫힌 거래가 없습니다. 아직 아무 결론도 낼 수 없습니다.")

    if openp:
        oc = sum(realized_r(p) for p in openp)
        print(f"\n  [열린 거래 {len(openp)}건]  실현 누적 {oc:+.2f}R")
        print("    남은 물량이 얼마가 될지는 **모른다.** 본절로 0R 이 될 수도,")
        print("    더 갈 수도 있다. 이걸 성과로 세면 안 된다.")

    ag = sum(revisits(p) for p in positions)
    if ag:
        print(f"\n  ⚠️ 같은 목표 레벨 재도달 {ag}회")
        print("     가격이 한 구간을 오가며 지날 때마다 5%씩 팔고 축하 메시지가")
        print("     하나씩 생긴다. '연속 목표 도달' 은 거래 수가 아니다.")

    tp_msgs = kinds.get("tp", 0)
    if tp_msgs and closed:
        print(f"\n  · 부분익절 메시지 {tp_msgs}개 ↔ 닫힌 거래 {len(closed)}건")
        print(f"    피드에서 보이는 것과 성과의 단위가 {tp_msgs / max(1, len(closed)):.0f}배 다르다.")

    print("""
  ─────────────────────────────────────────────────────────────
  여기까지는 '무엇이 일어났나'다. '우위가 있나'는 아직 모른다.
  같은 코인·같은 기간에 **아무 날이나** 들어갔으면 얼마였는지와
  견줘야 한다. --signals 로 뽑아서 시험대에 올리면 된다.""")
    return 0


def dump_signals(blocks, path=SIGNALS):
    events = [e for e in (classify(b) for b in blocks) if e]
    rows = []
    for p in build(events):
        if p["entry"] and p["stop"]:
            rows.append({"coin": p["coin"], "side": p["side"],
                         "entry": p["entry"], "stop": p["stop"],
                         "date": p["opened"][:10]})
    with open(path, "w", encoding="utf-8") as fp:
        for r in rows:
            fp.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
    print(f"  진입 정보가 온전한 {len(rows)}건 → {path}")
    if not rows:
        print("  ⚠️ 진입 알림(진입가·손절가가 적힌 메시지)이 없으면 못 만듭니다.")
        print("     부분익절 메시지만으로는 손절가를 알 수 없어 R을 못 잽니다.")
    return 0


def main(argv):
    for cand in (os.getcwd(), os.path.dirname(os.path.abspath(__file__))):
        if cand not in sys.path:
            sys.path.insert(0, cand)
    try:
        import console
        console.enable_utf8()
    except Exception:
        pass

    files = [a for a in argv[1:] if not a.startswith("-")]
    if not files:
        print(__doc__)
        return 1
    if not os.path.exists(files[0]):
        print(f"  파일을 못 찾았습니다: {files[0]}")
        return 1

    blocks = read_blocks(files[0])
    if "--signals" in argv:
        return dump_signals(blocks)
    return report(blocks)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
