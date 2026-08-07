"""
kimchi_band.py — 김치 프리미엄의 '최근 범위 중 지금 위치'

MarketSurfer의 김프 화면에서 가져온 것. 분석 문서 4절에 *"봇의 /김프에 같은
맥락을 붙이면 바로 좋아진다"* 로 적어 두고 안 만든 그것이다.

사이트 화면
─────────
    최근 30일 범위 중 지금   최저 -1.26% · 평균 -0.61% · 최고 0.19%
    지금 -0.25% — 최근 범위의 가운데입니다 (아래에서 70% 지점).

    "같은 김프라도 최근 범위의 어디인지에 따라 읽는 뜻이 달라집니다.
     이 값은 하루에 한 번 기록된 것으로, 장중 등락은 담기지 않습니다."

왜 절대값만으로는 못 읽나
──────────────────────
김프 -0.25%는 그 자체로 아무 뜻이 없다. 지난 30일이 -3% ~ -0.5% 였다면
-0.25%는 **역대급으로 비싼** 것이고, +2% ~ -0.3% 였다면 **바닥권**이다.
같은 숫자가 정반대를 뜻한다.

봇의 /김프가 지금 절대값만 보여준다면, 그건 온도계 없이 "덥다"고 말하는 것과
같다. 백분위를 붙이는 순간 판단이 된다.

expected_range.py와 같은 발상
────────────────────────────
그쪽은 가격이 예상 범위 어디인지를 보고, 이쪽은 김프가 최근 범위 어디인지를
본다. 둘 다 "지금 값"이 아니라 "지금 값의 위치"를 재는 것이고, 위치가
절대값보다 판단에 가깝다는 같은 전제 위에 있다.

기록은 하루 한 번
───────────────
사이트가 명시한 대로 일 1회 스냅샷만 남긴다. 장중 값을 다 담으면 변동성이
큰 날의 표본이 과대 대표되어 백분위가 그 날에 끌려간다.
"""

import time
from datetime import datetime, timedelta

import jsonstore

HISTORY_FILE = "kimchi_history.json"

# 백분위를 낼 기본 창 (일). 사이트와 같은 30일.
DEFAULT_WINDOW_DAYS = 30

# 이만큼은 쌓여야 백분위를 낸다. 5개짜리 백분위는 "최고/최저" 말고 뜻이 없다.
MIN_SAMPLES = 10

# 보관 기간 — 창보다 넉넉히 둔다
KEEP_DAYS = 120


def premium(domestic_krw, overseas_usd, usdkrw):
    """
    김치 프리미엄(%).

    사이트가 붙여 둔 단서를 그대로 옮긴다 — *"표시된 김프가 그대로 차익이
    되지 않습니다. 송금 수수료·시간·출금 제한이 실제 수익을 깎습니다."*
    이 함수는 표시값만 낸다. 실현 가능한 차익이 아니다.
    """
    if not overseas_usd or overseas_usd <= 0 or not usdkrw or usdkrw <= 0:
        return None
    fair = overseas_usd * usdkrw
    return (domestic_krw - fair) / fair * 100


# ================================
# 📸 기록
# ================================

def record(value, date=None, path=HISTORY_FILE):
    """
    오늘 김프를 남긴다 (하루 한 번).

    같은 날 두 번 부르면 덮어쓴다 — 하루에 한 점만 남기는 게 이 모듈의 전제다.
    """
    if value is None:
        return None
    hist = jsonstore.load(path, default={})
    if not isinstance(hist, dict):
        hist = {}

    d = date or datetime.now().strftime("%Y-%m-%d")
    hist[d] = {"value": round(float(value), 4), "saved_at": time.time()}

    cutoff = (datetime.now() - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    hist = {k: v for k, v in hist.items() if k >= cutoff}

    jsonstore.save(path, hist)
    return hist


def series(window_days=DEFAULT_WINDOW_DAYS, path=HISTORY_FILE):
    """최근 window_days 안의 (날짜, 값) — 날짜순."""
    hist = jsonstore.load(path, default={})
    if not isinstance(hist, dict):
        return []
    cutoff = (datetime.now() - timedelta(days=window_days)).strftime("%Y-%m-%d")
    return sorted(((d, v["value"]) for d, v in hist.items()
                   if d >= cutoff and isinstance(v, dict) and "value" in v))


# ================================
# 📊 위치
# ================================

def band(current=None, window_days=DEFAULT_WINDOW_DAYS, path=HISTORY_FILE,
         min_samples=MIN_SAMPLES):
    """
    최근 범위 중 지금 위치.

    current: 지금 김프. 없으면 기록의 마지막 값.

    반환 None = 표본 부족. 사이트와 같은 원칙 — 모자라면 수치를 내지 않는다.
    """
    pts = series(window_days, path)
    if len(pts) < min_samples:
        return None

    vals = [v for _, v in pts]
    lo, hi = min(vals), max(vals)
    avg = sum(vals) / len(vals)
    cur = current if current is not None else vals[-1]

    span = hi - lo
    if span <= 0:
        pos = 0.5
    else:
        pos = (cur - lo) / span
    pos_clipped = min(1.0, max(0.0, pos))

    # 백분위 — 지금보다 낮았던 날의 비율. 위치(pos)와 다르다.
    # pos는 최저~최고 사이의 선형 위치고, 백분위는 분포에서의 순위다.
    # 값이 한쪽에 몰린 달에는 둘이 크게 벌어지므로 같이 낸다.
    below = sum(1 for v in vals if v < cur)
    pct = below / len(vals)

    if pos_clipped >= 0.8:
        zone = "상단"
    elif pos_clipped <= 0.2:
        zone = "하단"
    else:
        zone = "가운데"

    return {
        "current"     : cur,
        "low"         : lo,
        "high"        : hi,
        "avg"         : avg,
        "position"    : pos_clipped,
        "raw_position": pos,
        "percentile"  : pct,
        "zone"        : zone,
        "out_of_range": pos > 1 or pos < 0,
        "samples"     : len(vals),
        "window_days" : window_days,
        "first_date"  : pts[0][0],
        "last_date"   : pts[-1][0],
    }


def read_as(b):
    """위치를 사람 말로. 사이트의 한 줄 요약에 대응."""
    if not b:
        return "표본이 모자라 아직 위치를 낼 수 없습니다."
    if b["out_of_range"]:
        side = "위로" if b["raw_position"] > 1 else "아래로"
        return f"최근 {b['window_days']}일 범위를 {side} 벗어났습니다 — 새 국면입니다."
    return (f"최근 범위의 {b['zone']}입니다 "
            f"(아래에서 {b['position']*100:.0f}% 지점).")


# ================================
# 📋 리포트
# ================================

def get_report(current=None, window_days=DEFAULT_WINDOW_DAYS, path=HISTORY_FILE):
    """텔레그램 리포트 — 봇의 /김프에 덧붙일 블록."""
    b = band(current, window_days, path)
    if not b:
        pts = series(window_days, path)
        return (
            "📭 <b>김프 위치</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"기록이 {len(pts)}일치뿐입니다 (최소 {MIN_SAMPLES}일 필요).\n"
            "하루 한 번 <code>kimchi_band.record(김프)</code>를 부르면\n"
            "며칠 뒤부터 '지금이 비싼지 싼지'가 나옵니다."
        )

    emoji = {"상단": "🔺", "하단": "🔻", "가운데": "▪️"}[b["zone"]]
    lines = [
        f"🇰🇷 <b>김프 위치</b> (최근 {b['window_days']}일)",
        "━━━━━━━━━━━━━━━━━━━━",
        f"{emoji} 지금 <b>{b['current']:+.2f}%</b> — {read_as(b)}",
        "",
        f"  최저 {b['low']:+.2f}%  ·  평균 {b['avg']:+.2f}%  ·  최고 {b['high']:+.2f}%",
        f"  분포상 백분위 {b['percentile']*100:.0f}%",
        f"  표본 {b['samples']}일 ({b['first_date']} ~ {b['last_date']})",
        "━━━━━━━━━━━━━━━━━━━━",
        "※ 하루 한 번 기록된 값이라 장중 등락은 담기지 않습니다.",
        "※ 표시된 김프가 그대로 차익이 되지 않습니다 —",
        "  송금 수수료·시간·출금 제한이 실제 수익을 깎습니다.",
    ]
    return "\n".join(lines)
