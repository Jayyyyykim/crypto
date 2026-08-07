"""
six_bar_align.py — 여섯 봉 정렬 · 전환 시각 · 겹친 종목 · 봉 방향 그리드

MarketSurfer의 '리더보드 / 겹친 종목'과 '봉 방향 그리드'를 봇으로 옮긴 모듈.

사이트 화면
─────────
    ETR   하락 우위 · 6/6봉 정렬
          정렬 4시간봉 · 12시간봉 · 일봉 · 3일봉 · 주봉 · 월봉
          전환 4시간봉 · 12시간봉 (08-07 02:09)

    "여섯 봉 중 4개 이상이 같은 방향이면서 최근 7일 안에 그 방향으로 바뀐
     종목이 겹칩니다. 방향이 모여 있고 최근에 바뀐 자리입니다."

regime_gate.py와 무엇이 다른가
────────────────────────────
겹치는 개념이 있어서 헷갈리기 쉬운데, 역할이 다르다.

  regime_gate  = **시장 전체**가 어느 국면인가. 느린 봉 4개(일·3일·주·월)로
                 판정하고, 진입을 막는 게이트로 쓴다.
  six_bar_align = **종목 하나**가 어떻게 서 있나. 여섯 봉 전부를 보고
                 "5/6봉 정렬 · 어제 12시간봉에서 전환" 같은 라벨을 만든다.

그리고 결정적 차이가 하나 더 있다. regime_gate.detect_turns()는 **어제
스냅샷**과 비교해서 전환을 센다 — 스냅샷이 며칠 쌓이기 전에는 전환을 셀 수
없고, 셀 수 있어도 "어제와 오늘 사이 어딘가"까지만 안다.

이 모듈은 전환을 **봉 데이터에서 직접** 찾는다. 그래서

  · 오늘 처음 돌려도 바로 나온다 (스냅샷 축적 불필요)
  · 어느 봉에서 바뀌었는지 **시각**까지 나온다 (사이트의 "08-07 02:09")
  · 봉별로 따로 나온다 (4시간봉은 어제 바뀌고 일봉은 지난주에 바뀜)

봉마다 EMA 길이가 다른 이유
─────────────────────────
전 봉에 같은 (10, 30)을 쓰면 월봉은 30개 = 2년 반치 데이터를 요구한다.
그러면 긴 봉이 영영 '집계 안 됨'으로 남아 4/6 정렬 자체가 성립하지 않는다.
(실제로 그렇게 짜서 돌려 봤더니 전 종목이 '방향 갈림'으로 나왔다.)
긴 봉일수록 짧은 창을 쓰되, 봉 하나의 무게가 크다는 성질은 그대로다.

'–'의 뜻
───────
사이트의 그리드에서 '–'는 *추세 없음*이 아니라 **아직 집계되지 않은 봉**이다.
그래서 여기서도 봉이 충분하면 거의 항상 ▲/▼가 나오고, None은 데이터 부족일
때만 쓴다. 이 구분을 흐리면 "월봉이 애매하다"와 "월봉 데이터가 없다"가
같은 칸에 들어가 표가 거짓말을 한다.
"""

from datetime import datetime, timedelta

# 여섯 봉. 순서가 짧은 봉 → 긴 봉이고, 전환 우선순위가 이 순서를 따른다.
SIX_TFS = ("4h", "12h", "1d", "3d", "1w", "1M")

TF_KR = {"4h": "4시간봉", "12h": "12시간봉", "1d": "일봉",
         "3d": "3일봉", "1w": "주봉", "1M": "월봉"}

# 봉별 EMA 길이 (fast, slow) — 위 설명 참고
PERIODS = {
    "4h": (10, 30), "12h": (10, 30), "1d": (10, 30),
    "3d": (8, 21), "1w": (6, 16), "1M": (4, 10),
}

# 정렬로 인정할 최소 개수 (여섯 봉 중) — 사이트와 동일
MIN_VOTES = 4

# 겹침 판정 창 (일) — 사이트와 동일
OVERLAP_WINDOW_DAYS = 7

# 전환을 찾아볼 최근 봉 수
FLIP_LOOKBACK = 60

# 두 EMA가 이보다 가까우면 종가 위치로 가른다 (완전히 겹친 구간 처리)
FLAT_EPS = 1e-4


def _ohlc(df):
    try:
        return list(df["close"]), list(df.get("timestamp", []))
    except (KeyError, TypeError, AttributeError):
        return None, None


def _ema(values, period):
    """첫 값에서 출발하는 표준 EMA 재귀."""
    if not values:
        return []
    k = 2.0 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def trend_series(closes, fast, slow):
    """
    각 봉 시점의 방향 리스트 ('up' | 'down' | None).

    EMA는 첫 값에서 출발하는 순수 재귀라, 전체에 대해 한 번 돌린 배열의
    i번째가 '앞에서 i+1개만 넣고 계산한 값'과 정확히 같다. 그래서 시점마다
    다시 계산할 필요가 없다 — 전환 탐색이 O(n²)에서 O(n)이 된다.
    (처음엔 시점마다 다시 계산하게 짰다가 전 종목 스캔이 8분 걸렸다.)
    """
    n = len(closes)
    if n == 0:
        return []
    ef, es = _ema(closes, fast), _ema(closes, slow)
    need = slow + 5

    out = []
    for i in range(n):
        if i + 1 < need or es[i] <= 0:
            out.append(None)
            continue
        gap = (ef[i] - es[i]) / es[i]
        if abs(gap) < FLAT_EPS:
            out.append("up" if closes[i] >= es[i] else "down")
        else:
            out.append("up" if gap > 0 else "down")
    return out


def _bar_time(ts, i):
    if not ts or i >= len(ts):
        return None
    v = ts[i]
    try:
        return v.strftime("%Y-%m-%d %H:%M")
    except AttributeError:
        return str(v)[:16]


def analyse_tf(df, tf, lookback=FLIP_LOOKBACK):
    """
    한 봉의 방향과 마지막 전환.

    반환: {"state", "flipped_at", "flipped_index", "bars"} 또는 None(봉 부족)
    """
    closes, ts = _ohlc(df)
    fast, slow = PERIODS.get(tf, (10, 30))
    if not closes or len(closes) < slow + 5:
        return None

    series = trend_series(closes, fast, slow)
    state = series[-1]
    if state is None:
        return None

    # 마지막 전환 — 뒤에서부터 훑어 방향이 달랐던 첫 지점의 '다음' 봉이 전환점
    start = max(0, len(series) - lookback)
    flipped_index = None
    for i in range(len(series) - 1, start, -1):
        if series[i] is None:
            continue
        prev = next((series[j] for j in range(i - 1, start - 1, -1)
                     if series[j] is not None), None)
        if prev is None:
            break
        if prev != series[i]:
            flipped_index = i
            break

    return {
        "state"        : state,
        "flipped_at"   : _bar_time(ts, flipped_index) if flipped_index is not None else None,
        "flipped_index": flipped_index,
        "bars"         : len(closes),
    }


def symbol_grid(symbol, get_ohlcv_fn, tfs=SIX_TFS, bars=300):
    """
    종목 하나의 여섯 봉 판정.

    get_ohlcv_fn(symbol, tf, limit=...) → DataFrame

    주의: 종목당 여섯 번 조회한다. 100종목이면 600회 — 캐시 없이 자주
    돌리면 안 된다. 사이트도 "1~10분 캐시"라고 화면에 써 뒀다.
    """
    cells = {}
    for tf in tfs:
        try:
            df = get_ohlcv_fn(symbol, tf, limit=bars)
        except Exception as e:
            print(f"[여섯봉] 조회 실패 ({symbol} {tf}): {e}")
            cells[tf] = None
            continue
        if df is None or len(df) == 0:
            cells[tf] = None
            continue
        cells[tf] = analyse_tf(df, tf)

    return build_row(symbol, cells, tfs)


def build_row(symbol, cells, tfs=SIX_TFS):
    """
    셀 묶음 → 종목 한 줄. (조회를 이미 한 경우 재사용하려고 분리해 뒀다)

    cells: {tf: analyse_tf() 결과 또는 None}
    """
    counted = [tf for tf in tfs if cells.get(tf)]
    ups = [tf for tf in counted if cells[tf]["state"] == "up"]
    downs = [tf for tf in counted if cells[tf]["state"] == "down"]

    aligned = None
    if len(ups) >= MIN_VOTES and len(ups) > len(downs):
        aligned = "up"
    elif len(downs) >= MIN_VOTES and len(downs) > len(ups):
        aligned = "down"

    votes = len(ups) if aligned == "up" else len(downs) if aligned == "down" else max(len(ups), len(downs))

    return {
        "symbol"  : symbol,
        "coin"    : symbol.replace("/USDT", ""),
        "cells"   : cells,
        "counted" : len(counted),
        "up"      : len(ups),
        "down"    : len(downs),
        "aligned" : aligned,
        "votes"   : votes,
        "score"   : len(ups) - len(downs),
        "aligned_tfs": ups if aligned == "up" else downs if aligned == "down" else [],
    }


def recent_flips(row, within_days=OVERLAP_WINDOW_DAYS, now=None):
    """
    최근 within_days 안에 전환한 봉들 — 긴 봉 순.

    긴 봉의 전환이 더 무겁다. 4시간봉은 하루에도 몇 번 바뀌지만 주봉이
    바뀌면 그건 국면이 바뀐 것이다.
    """
    now = now or datetime.now()
    cutoff = now - timedelta(days=within_days)
    order = {tf: i for i, tf in enumerate(SIX_TFS)}

    out = []
    for tf, cell in (row.get("cells") or {}).items():
        if not cell or not cell.get("flipped_at"):
            continue
        try:
            when = datetime.strptime(cell["flipped_at"], "%Y-%m-%d %H:%M")
        except (ValueError, TypeError):
            continue
        if when >= cutoff:
            out.append({"tf": tf, "tf_kr": TF_KR.get(tf, tf),
                        "at": cell["flipped_at"], "to": cell["state"]})

    out.sort(key=lambda f: -order.get(f["tf"], 0))
    return out


def is_overlap(row, within_days=OVERLAP_WINDOW_DAYS, now=None):
    """
    겹친 종목인가 — 정렬돼 있으면서 최근에 그 방향으로 바뀌었나.

    오래 정렬된 종목은 이미 갈 만큼 간 자리다. 갓 바뀐 정렬이 남은 거리가 길다.
    """
    if not row.get("aligned"):
        return False
    return any(f["to"] == row["aligned"]
               for f in recent_flips(row, within_days, now))


# ================================
# 📡 전 종목 스캔
# ================================

def scan_universe(symbols, get_ohlcv_fn, tfs=SIX_TFS, bars=300):
    """전 종목 여섯 봉 스캔."""
    rows = []
    for symbol in symbols:
        row = symbol_grid(symbol, get_ohlcv_fn, tfs, bars)
        if row["counted"] >= MIN_VOTES:
            rows.append(row)
    return rows


def summarize(rows, within_days=OVERLAP_WINDOW_DAYS, now=None):
    """
    사이트의 '봇이 지금 보고 있는 것' 카드에 대응하는 집계.
    """
    total = len(rows)
    up = sum(1 for r in rows if r["aligned"] == "up")
    down = sum(1 for r in rows if r["aligned"] == "down")
    flipped = sum(1 for r in rows if recent_flips(r, 1, now))
    overlap = [r for r in rows if is_overlap(r, within_days, now)]

    return {
        "watched"    : total,
        "cells"      : sum(r["counted"] for r in rows),
        "up_aligned" : up,
        "down_aligned": down,
        "flipped_today": flipped,
        "overlap"    : len(overlap),
        "up_ratio"   : (up / total) if total else 0.0,
        "overlap_rows": overlap,
    }


def leaderboard(rows, within_days=OVERLAP_WINDOW_DAYS, now=None, limit=15):
    """겹친 종목을 정렬 강도 순으로."""
    picks = [r for r in rows if is_overlap(r, within_days, now)]
    picks.sort(key=lambda r: (-abs(r["score"]), -r["votes"], r["symbol"]))
    return picks[:limit]


def direction_matrix(rows, tfs=SIX_TFS):
    """
    봉 방향 그리드 — 종목 × 여섯 봉의 ▲▼ 매트릭스.

    반환: {"header": [...], "rows": [{"coin", "cells": ["▲","▼","–",...]}]}
    """
    mark = {"up": "▲", "down": "▼", None: "–"}
    out = []
    for r in rows:
        cells = []
        for tf in tfs:
            c = (r.get("cells") or {}).get(tf)
            cells.append(mark[c["state"]] if c else "–")
        out.append({"coin": r["coin"], "symbol": r["symbol"], "cells": cells})
    return {"header": [TF_KR.get(tf, tf) for tf in tfs], "rows": out}


# ================================
# 📋 리포트
# ================================

def label(row):
    """'상승 우위 · 5/6봉 정렬' 같은 한 줄."""
    if not row.get("aligned"):
        return "방향 갈림"
    side = "상승" if row["aligned"] == "up" else "하락"
    return f"{side} 우위 · {row['votes']}/{row['counted']}봉 정렬"


def get_report(rows, within_days=OVERLAP_WINDOW_DAYS, now=None, limit=12):
    """텔레그램 리포트 — 사이트의 리더보드에 대응."""
    if not rows:
        return "📭 여섯 봉을 판정할 수 있는 종목이 없어요 (봉 부족)."

    s = summarize(rows, within_days, now)
    lines = [
        "🎯 <b>여섯 봉 정렬</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"감시 {s['watched']}종 · 판정 칸 {s['cells']}",
        f"상승 정렬 {s['up_aligned']}종 · 하락 정렬 {s['down_aligned']}종 "
        f"(상승 비율 {s['up_ratio']*100:.0f}%)",
        f"오늘 전환 {s['flipped_today']}종 · <b>겹친 종목 {s['overlap']}종</b>",
    ]

    board = leaderboard(rows, within_days, now, limit)
    if not board:
        lines += ["", "겹친 종목 없음 — 정렬과 최근 전환이 같이 있는 자리가 없습니다."]
    else:
        lines += ["", f"<b>겹친 종목</b> (정렬 + 최근 {within_days}일 전환)"]
        for r in board:
            arrow = "🔼" if r["aligned"] == "up" else "🔽"
            flips = recent_flips(r, within_days, now)
            same = [f for f in flips if f["to"] == r["aligned"]]
            lines.append(f"{arrow} <b>{r['coin']}</b>  {label(r)}")
            lines.append(
                f"    정렬 {' · '.join(TF_KR.get(t, t) for t in r['aligned_tfs'])}"
            )
            if same:
                lines.append(
                    f"    전환 {' · '.join(f['tf_kr'] for f in same)} ({same[0]['at']})"
                )

    lines += [
        "━━━━━━━━━━━━━━━━━━━━",
        "※ '–'는 추세 없음이 아니라 아직 집계되지 않은 봉입니다.",
        "※ 오래 정렬된 종목보다 갓 바뀐 정렬이 남은 거리가 깁니다.",
    ]
    return "\n".join(lines)


def get_matrix_report(rows, tfs=SIX_TFS, limit=30):
    """봉 방향 그리드 리포트 (고정폭 텍스트)."""
    m = direction_matrix(rows, tfs)
    if not m["rows"]:
        return "📭 그릴 종목이 없어요."

    head = "        " + " ".join(f"{h[:3]:>3}" for h in m["header"])
    lines = ["📊 <b>봉 방향 그리드</b>", "<pre>", head]
    for r in m["rows"][:limit]:
        lines.append(f"{r['coin'][:7]:<7} " + "  ".join(f"{c:>1}" for c in r["cells"]))
    lines += ["</pre>", "※ '–'는 아직 집계되지 않은 봉입니다."]
    return "\n".join(lines)
