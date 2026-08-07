"""
regime_gate.py — 매매환경 판정 + 진입 게이트

MarketSurfer의 '오늘 알트 매매 환경' / '지금 봇이 보는 시장'을 봇으로 옮긴 모듈.

핵심 아이디어 (그 사이트가 화면에 그대로 써 둔 규칙)
────────────────────────────────────────────────
  "매매환경 판정은 일·3일·주·월 네 봉으로 봅니다. 그중 3개 이상이 같은
   방향이면 정렬로 봅니다. 4시간·12시간을 빼는 이유는, 매매환경 판정이
   장중 움직임 하나에 뒤집히면 판정으로 쓸 수 없기 때문입니다."

이건 지금 봇의 mtf_score.py와 결정적으로 다르다. mtf_score는 6개 봉
(월·주·3일·일·12H·4H)을 25:20:18:15:12:10으로 가중해 한 점수로 뭉갠다.
그러면 4H·12H가 22점 — 장중 한 번 출렁이면 총점이 22점 움직이고,
"상승 우위(65)"가 "방향성 없음(43)"으로 뒤집힌다. 환경 판정으로 못 쓴다.

그래서 역할을 나눈다:
  · 환경 판정(이 모듈)  = 느린 봉 4개만. 하루에 한 번 바뀔까 말까 한 값.
  · 타이밍(mtf_score)   = 6봉 전부. 진입 시점을 잡는 용도.

환경이 '관망'인 날엔 아무리 좋은 4H 신호가 떠도 페이퍼 진입을 막는다.
페이퍼 트레이딩 표본을 늘리는 것보다 '환경 맞을 때만 친 표본'을 모으는 게
훨씬 빨리 답을 준다.

전환(turn)과 겹침(overlap)
─────────────────────────
사이트는 '오늘 방향 전환 206건', '겹친 종목 136종'을 따로 센다.
  전환 = 어제와 오늘 정렬 방향이 달라진 종목
  겹침 = 정렬돼 있으면서 최근 7일 안에 그 방향으로 바뀐 종목
       ("방향이 모여 있고 최근에 바뀐 자리" — 가장 신선한 후보)
전환을 세려면 어제 값이 있어야 해서 매일 스냅샷을 남긴다.
"""

import time
from datetime import datetime, timedelta

import console
import jsonstore

SNAPSHOT_FILE = "regime_snapshots.json"

# 환경 판정에 쓰는 느린 봉 — 4H·12H는 의도적으로 뺐다 (위 설명 참고)
SLOW_TFS = ("1d", "3d", "1w", "1M")

# 정렬로 인정할 최소 동의 개수 (4개 중 3개)
MIN_VOTES = 3

# 스냅샷 보관 기간
SNAPSHOT_KEEP_DAYS = 60

# 겹침 판정 창
OVERLAP_WINDOW_DAYS = 7


# ================================
# 🧭 종목 단위 정렬 판정
# ================================

def align_from_states(states):
    """
    {tf: 'up'|'down'|'side'} → ('up'|'down'|None, votes, detail)

    4개 중 3개 이상이 같은 방향이어야 정렬. 'side'는 어느 쪽에도 안 센다.
    데이터가 없는 봉(None)도 표에서 빠지되, 유효 봉이 3개 미만이면
    판정 자체를 포기한다 — 2개로 3표를 채울 수 없으니 당연하다.
    """
    valid = {tf: s for tf, s in states.items() if s in ("up", "down", "side")}
    if len(valid) < MIN_VOTES:
        return None, 0, valid

    ups = sum(1 for s in valid.values() if s == "up")
    downs = sum(1 for s in valid.values() if s == "down")

    if ups >= MIN_VOTES:
        return "up", ups, valid
    if downs >= MIN_VOTES:
        return "down", downs, valid
    return None, max(ups, downs), valid


def symbol_alignment(symbol, analyze_fn, tfs=SLOW_TFS):
    """
    analyze_fn(symbol, tf) → 봇의 analyze_timeframe() 결과 (trend_code 포함)

    반환: {symbol, aligned, votes, states}  — aligned None이면 엇갈림
    """
    states = {}
    for tf in tfs:
        try:
            d = analyze_fn(symbol, tf)
        except Exception as e:
            console.say(f"[환경] 분석 실패 ({symbol} {tf}): {e}")
            d = None
        states[tf] = d.get("trend_code") if d else None

    aligned, votes, valid = align_from_states(states)
    return {
        "symbol" : symbol,
        "coin"   : symbol.replace("/USDT", ""),
        "aligned": aligned,
        "votes"  : votes,
        "states" : states,
        "covered": len(valid),
    }


# ================================
# 🌏 유니버스 집계
# ================================

def _label(up_ratio, up_aligned, down_aligned, total):
    """
    유니버스 집계 → 환경 라벨.

    상승 비율만 보면 '오늘 오른 종목이 많다'는 것뿐이라 하루짜리 노이즈다.
    정렬 종목 수를 같이 봐야 '추세가 붙어 있는 시장'인지 구분된다.
    """
    if not total:
        return "판정불가", "데이터 부족"

    aligned_ratio = (up_aligned + down_aligned) / total
    net = (up_aligned - down_aligned) / total

    # 정렬 자체가 적으면 방향이 아니라 '추세 없음'이 결론이다.
    if aligned_ratio < 0.20:
        return "관망", "정렬된 종목이 적습니다 — 추세가 붙은 시장이 아닙니다"

    if net >= 0.25 and up_ratio >= 0.55:
        return "상승국면", "정렬이 위쪽으로 몰려 있습니다"
    if net >= 0.10:
        return "상승우위", "위쪽이 우세하지만 압도적이진 않습니다"
    if net <= -0.25 and up_ratio <= 0.45:
        return "하락국면", "정렬이 아래쪽으로 몰려 있습니다"
    if net <= -0.10:
        return "하락우위", "아래쪽이 우세하지만 압도적이진 않습니다"
    return "관망", "위아래 정렬이 비슷합니다 — 한쪽으로 쏠린 날이 아닙니다"


def scan_universe(symbols, analyze_fn, get_price_fn=None, tfs=SLOW_TFS):
    """
    전 종목 정렬 스캔 → 행 + 집계.

    주의: 종목 × 4TF만큼 analyze_fn을 부른다. 100종목이면 400회 —
    캐시 없이 매분 돌리면 안 된다. 하루 1~2회(일봉 마감 후)면 충분하다.
    환경은 그 주기로만 바뀌도록 설계한 값이라 자주 돌 이유도 없다.
    """
    rows = []
    for symbol in symbols:
        rows.append(symbol_alignment(symbol, analyze_fn, tfs))

    judged = [r for r in rows if r["covered"] >= MIN_VOTES]
    up_aligned = [r for r in judged if r["aligned"] == "up"]
    down_aligned = [r for r in judged if r["aligned"] == "down"]

    # 상승 비율은 정렬 여부와 무관하게 '일봉이 상승인 종목' 비율.
    # 사이트의 '상승 비율 40%'와 같은 의미.
    day_up = sum(1 for r in judged if r["states"].get("1d") == "up")
    up_ratio = day_up / len(judged) if judged else 0.0

    label, note = _label(up_ratio, len(up_aligned), len(down_aligned), len(judged))

    return {
        "date"        : datetime.now().strftime("%Y-%m-%d"),
        "total"       : len(judged),
        "skipped"     : len(rows) - len(judged),
        "up_ratio"    : round(up_ratio, 3),
        "up_aligned"  : len(up_aligned),
        "down_aligned": len(down_aligned),
        "label"       : label,
        "note"        : note,
        "rows"        : rows,
    }


# ================================
# 📸 스냅샷 · 전환 · 겹침
# ================================

def record_snapshot(scan, path=SNAPSHOT_FILE):
    """오늘 정렬 상태를 남긴다 (전환을 세려면 어제 값이 필요하다)."""
    snaps = jsonstore.load(path, default={})
    if not isinstance(snaps, dict):
        snaps = {}

    snaps[scan["date"]] = {
        "label"       : scan["label"],
        "up_ratio"    : scan["up_ratio"],
        "up_aligned"  : scan["up_aligned"],
        "down_aligned": scan["down_aligned"],
        "total"       : scan["total"],
        "aligned"     : {r["symbol"]: r["aligned"] for r in scan["rows"]
                         if r["aligned"]},
        "saved_at"    : time.time(),
    }

    cutoff = (datetime.now() - timedelta(days=SNAPSHOT_KEEP_DAYS)).strftime("%Y-%m-%d")
    snaps = {d: v for d, v in snaps.items() if d >= cutoff}

    jsonstore.save(path, snaps)
    return snaps


def detect_turns(scan, path=SNAPSHOT_FILE, window_days=OVERLAP_WINDOW_DAYS):
    """
    방향 전환 종목 — 직전 스냅샷 대비 정렬 방향이 달라진 종목.

    반환: {"today": [...], "recent": {symbol: 바뀐 날짜}}
      today  = 어제(가장 최근 스냅샷) 대비 오늘 바뀐 종목
      recent = window_days 안에 한 번이라도 바뀐 종목 (겹침 판정용)
    """
    snaps = jsonstore.load(path, default={})
    if not isinstance(snaps, dict) or not snaps:
        return {"today": [], "recent": {}}

    today = scan["date"]
    past_dates = sorted(d for d in snaps if d < today)
    now_aligned = {r["symbol"]: r["aligned"] for r in scan["rows"] if r["aligned"]}

    turned_today = []
    if past_dates:
        prev = snaps[past_dates[-1]].get("aligned", {})
        for sym, direction in now_aligned.items():
            if prev.get(sym) and prev[sym] != direction:
                turned_today.append({"symbol": sym,
                                     "coin": sym.replace("/USDT", ""),
                                     "from": prev[sym], "to": direction})

    # 최근 window_days 안의 전환 — 날짜를 거슬러 올라가며 방향이 바뀐 지점을 찾는다
    cutoff = (datetime.now() - timedelta(days=window_days)).strftime("%Y-%m-%d")
    recent_window = [d for d in past_dates if d >= cutoff]

    recent = {}
    for sym, direction in now_aligned.items():
        for d in reversed(recent_window):
            prev_dir = snaps[d].get("aligned", {}).get(sym)
            if prev_dir and prev_dir != direction:
                recent[sym] = d
                break

    return {"today": turned_today, "recent": recent}


def find_overlap(scan, turns):
    """
    겹친 종목 — 정렬돼 있으면서 최근 7일 안에 그 방향으로 바뀐 종목.

    사이트 설명 그대로: "여섯 봉 중 4개 이상이 같은 방향이면서 최근 7일 안에
    그 방향으로 바뀐 종목이 겹칩니다. 방향이 모여 있고 최근에 바뀐 자리."
    (여긴 느린 봉 4개 중 3개 기준이라 개수만 다르고 뜻은 같다)

    오래 정렬된 종목은 이미 갈 만큼 간 자리다. 갓 바뀐 정렬이 남은 거리가 길다.
    """
    recent = turns.get("recent", {})
    out = []
    for r in scan["rows"]:
        if not r["aligned"]:
            continue
        if r["symbol"] in recent:
            out.append({**r, "turned_at": recent[r["symbol"]]})
    return out


# ================================
# 🚦 진입 게이트
# ================================

def gate(direction, scan, symbol_row=None, strict=True):
    """
    이 방향으로 진입해도 되는가.

    direction : 'long' | 'short'
    scan      : scan_universe() 결과
    symbol_row: 해당 종목의 symbol_alignment() 결과 (있으면 종목 정렬도 본다)
    strict    : True면 종목 정렬이 반대인 경우도 막는다

    반환: (allowed: bool, reason: str)

    설계 원칙 — 막는 쪽이 기본이다. 환경이 애매하면 '허용'이 아니라 '보류'.
    페이퍼는 잃어도 안 아프지만, 애매한 표본이 섞이면 통계가 못 쓰게 된다.
    """
    label = scan.get("label", "판정불가")
    want_up = direction == "long"

    if label == "판정불가":
        return False, "환경 판정 불가 (데이터 부족)"

    # 종목 자체가 반대로 정렬돼 있으면 환경과 무관하게 막는다.
    if strict and symbol_row and symbol_row.get("aligned"):
        sym_up = symbol_row["aligned"] == "up"
        if sym_up != want_up:
            return False, (
                f"종목 정렬이 반대 ({symbol_row['aligned']} "
                f"{symbol_row['votes']}/{len(SLOW_TFS)})"
            )

    if want_up:
        if label in ("상승국면", "상승우위"):
            return True, f"환경 {label}"
        if label == "관망":
            return False, "환경 관망 — 한쪽으로 쏠린 날이 아님"
        return False, f"환경 {label} — 역방향 진입"
    else:
        if label in ("하락국면", "하락우위"):
            return True, f"환경 {label}"
        if label == "관망":
            return False, "환경 관망 — 한쪽으로 쏠린 날이 아님"
        return False, f"환경 {label} — 역방향 진입"


def load_latest(path=SNAPSHOT_FILE, max_age_hours=30):
    """
    저장된 최신 환경 스냅샷을 읽는다 (매 진입마다 전 종목 재스캔하지 않도록).

    오래됐으면 None — 어제 환경으로 오늘 진입을 허용하면 게이트가 무의미하다.
    """
    snaps = jsonstore.load(path, default={})
    if not isinstance(snaps, dict) or not snaps:
        return None
    latest_date = max(snaps)
    snap = snaps[latest_date]
    if time.time() - (snap.get("saved_at") or 0) > max_age_hours * 3600:
        return None
    return {"date": latest_date, **snap, "rows": []}


# ================================
# 📋 리포트
# ================================

def get_report(scan, turns=None, overlap=None):
    """텔레그램 리포트 — 사이트의 '오늘 알트 매매 환경' 카드에 대응."""
    emoji = {
        "상승국면": "🟢🟢", "상승우위": "🟢", "관망": "⚪",
        "하락우위": "🔴", "하락국면": "🔴🔴", "판정불가": "❔",
    }.get(scan["label"], "⚪")

    lines = [
        f"🧭 <b>오늘 매매 환경</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"{emoji} <b>{scan['label']}</b>  ({scan['date']})",
        f"  {scan['note']}",
        "",
        f"  상승 비율   {scan['up_ratio']*100:.0f}%",
        f"  총 분석     {scan['total']}종",
        f"  상승 정렬   {scan['up_aligned']}종",
        f"  하락 정렬   {scan['down_aligned']}종",
    ]
    if scan["skipped"]:
        lines.append(f"  판정 제외   {scan['skipped']}종 (봉 부족)")

    if turns:
        lines += ["", f"  오늘 방향 전환  {len(turns.get('today', []))}건"]
        for t in turns.get("today", [])[:8]:
            arrow = "🔼" if t["to"] == "up" else "🔽"
            lines.append(f"    {arrow} {t['coin']}  {t['from']} → {t['to']}")

    if overlap:
        lines += ["", f"  🎯 겹친 종목  {len(overlap)}종 (정렬 + 최근 7일 전환)"]
        for o in overlap[:10]:
            arrow = "🔼" if o["aligned"] == "up" else "🔽"
            lines.append(
                f"    {arrow} {o['coin']}  {o['votes']}/{len(SLOW_TFS)}봉 정렬 "
                f"({o['turned_at']} 전환)"
            )

    lines += [
        "━━━━━━━━━━━━━━━━━━━━",
        "※ 환경 판정은 일·3일·주·월 네 봉만 봅니다.",
        "  4H·12H는 장중 한 번에 뒤집혀 판정으로 못 씁니다.",
        "  (진입 타이밍은 /자리 · /mtf 로 따로 보세요)",
    ]
    return "\n".join(lines)
