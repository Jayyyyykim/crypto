"""
grid_lines.py — 격자선(번선) + 구조 적합도

MarketSurfer의 '자금 격자 구 / 격자선' 화면을 봇으로 옮긴 모듈.
분석 문서에서 *"이번 구현엔 안 넣었다 — 다음 후보"* 로 남겨 둔 그것이다.

사이트 화면에 있던 것
──────────────────
    종목    기준              해당 선            현재가    이격    근거
    PLD     월봉·저점기준 1번선   $139.34 현재가 위  $139.14  0.14%  구조 적합도 0.56
    SPGI    월봉·저점기준 0번선   $404.03 현재가 아래 $405.24  0.3%   구조 적합도 0.762
    ECL     주봉·고점기준 0.5번선 $285.43 현재가 위  $283.46  0.69%  구조 적합도 0.542

    근거 칸: 2013-02-01 $34.57 → 2014-02-01 $61.94 → 2020-03-01 $167.07

그리고 화면에 이렇게 써 있었다:

  "선 이름은 격자 번호입니다 — 0번선이 기준, 위로 1·2번선, 아래로 -1번선,
   그 사이가 0.5·-0.5번선입니다. 추세선은 격자 없이 두 지점을 이은
   선입니다. 고점기준·저점기준은 그 선을 무엇으로 만들었는지입니다."

  "그은 점은 그 선을 만든 좌표입니다 — 직접 그으신 차트와 바로 대조할 수
   있습니다. 닿는 자리에서 어떻게 반응하는지가 관찰 대상이며, 도달 자체가
   방향을 뜻하지 않습니다."

무엇을 하는 모듈인가
─────────────────
피벗 **3점**으로 간격 단위를 정하고, 그 간격의 정수·반정수 배로 사다리를
만든다. 0번선이 기준, ±0.5·±1·±2번선이 그 위아래다.

핵심은 선이 아니라 **구조 적합도**다. 아무 3점이나 이으면 선은 항상 그려진다.
그 선이 실제로 가격을 설명하는지 — 과거 피벗들이 그 격자에 얼마나 얹혀
있는지 — 를 0~1로 같이 낸다. 적합도 없는 추세선은 그림일 뿐이다.

정직성에 대해
───────────
사이트의 정확한 공식은 화면에 없다. 이건 **화면에 보이는 출력으로부터의
재구성**이다. 그래서 두 가지를 지킨다.

  1. 적합도가 낮으면(기본 0.45 미만) 아예 선을 내지 않는다. 사이트가
     "집계되지 않은 종목은 목록에 넣지 않습니다"라고 하는 것과 같은 원칙.
  2. 격자를 만든 3점(anchors)을 항상 같이 돌려준다. 차트에 직접 대보고
     틀렸으면 버릴 수 있어야 한다.

level_map.py와 다른 점
────────────────────
level_map은 **닿은 자리**(터치가 쌓인 수평 레벨)를 찾는다. 이 모듈은
**간격의 규칙성**을 찾는다. 같은 종목에서 둘이 다른 자리를 가리키면
그건 모순이 아니라 서로 다른 질문의 답이다.
"""

import math

import level_map

# 기본 격자 사다리 — 0번선 기준 위아래. 사이트 표에 0·±0.5·±1·±2가 보였다.
# 다만 이건 '화면에 보여 준 범위'일 뿐이고, 격자는 원래 무한 사다리다.
# 실제 판정에는 관측된 피벗을 덮는 범위를 따로 만들어 쓴다(_steps_covering).
GRID_STEPS = (-2.0, -1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.0)

# 사다리가 무한정 길어지지 않게 자르는 상한. 이보다 많이 필요하면
# 간격 단위가 너무 작다는 뜻이라 격자 자체가 의미 없다.
MAX_LINES = 81

# 피벗이 격자선에 '얹혔다'고 볼 허용 오차 (가격 대비).
# level_map의 클러스터 오차(0.6%)보다 조금 넉넉하다 — 격자는 정확한 터치가
# 아니라 간격의 규칙성을 보는 것이라 같은 잣대를 쓰면 항상 0점이 나온다.
FIT_TOL = 0.012

# 이 미만이면 격자를 내지 않는다. 0.5는 "절반은 설명한다"는 뜻이고,
# 사이트 화면의 적합도들이 0.50~0.76 범위였다.
MIN_FIT = 0.45

# 격자를 세우는 데 필요한 최소 봉 수
MIN_BARS = 60

# 간격 단위가 이보다 작으면 격자가 무의미하다 (선이 다닥다닥 붙는다)
MIN_UNIT_PCT = 0.02

# 앵커끼리 최소 이만큼은 봉이 떨어져 있어야 한다. 붙어 있는 피벗 두 개로
# 간격을 정하면 그건 구조가 아니라 노이즈다.
ANCHOR_MIN_BARS = 5

# 적합도를 낼 때 채점 대상이 되는 피벗(앵커 제외)의 최소 개수.
#
# 피벗이 4개면 앵커 3개를 뺀 1개로 적합도가 매겨지고, 그 하나가 맞으면
# 1.0이 나온다. 표본 1개짜리 100%는 숫자가 아니다 — setup_ledger가
# 표본 미달이면 적중률을 안 내는 것과 같은 이유로 여기서도 안 낸다.
MIN_JUDGED_PIVOTS = 4


def _ohlc(df):
    try:
        return list(df["high"]), list(df["low"]), list(df["close"])
    except (KeyError, TypeError):
        return None, None, None


def _steps_covering(base, unit, lo, hi, pad=1.0):
    """lo~hi 가격대를 덮는 격자 번호들 (0.5 간격).

    격자를 ±2번선으로 고정하면, 그 밖에 있는 피벗이 전부 '안 맞음'으로
    세어져 적합도가 부당하게 낮아진다. 처음에 그렇게 짰다가 규칙적인
    사다리조차 0.4점을 받아 탈락했다. 격자는 무한히 이어지는 것이고,
    화면에 몇 개를 보여 주느냐는 별개 문제다.
    """
    n1 = (lo - base) / unit
    n2 = (hi - base) / unit
    lo_n = math.floor((min(n1, n2) - pad) * 2) / 2
    hi_n = math.ceil((max(n1, n2) + pad) * 2) / 2

    count = int((hi_n - lo_n) / 0.5) + 1
    if count > MAX_LINES:
        return None

    return tuple(round(lo_n + i * 0.5, 1) for i in range(count))


def build_grid(anchors, steps=GRID_STEPS, cover=None):
    """
    앵커 3점 → 격자선.

    anchors: [(index, price), ...] 최소 2개. 시간순.
    cover  : (lo, hi) 가격대. 주면 그 범위를 덮도록 사다리를 늘린다.

    0번선 = 첫 앵커. 간격 단위 = 첫 두 앵커의 가격 차.
    세 번째 앵커는 간격을 만드는 데 쓰지 않고 **검증**에 쓴다 —
    3점이 다 간격을 정하면 어떤 3점이든 완벽히 맞아 적합도가 무의미해진다.
    """
    if len(anchors) < 2:
        return None

    base = anchors[0][1]
    unit = anchors[1][1] - anchors[0][1]
    if base <= 0 or unit == 0:
        return None
    if abs(unit) / base < MIN_UNIT_PCT:
        return None

    if cover:
        steps = _steps_covering(base, unit, cover[0], cover[1])
        if not steps:
            return None

    return {
        "base": base,
        "unit": unit,
        "lines": {n: base + n * unit for n in steps},
        "anchors": [{"index": i, "price": p} for i, p in anchors],
    }


def fit_score(grid, pivots, tol=FIT_TOL):
    """
    구조 적합도 0~1 — 피벗 중 몇 %가 격자선 위에 얹혀 있나.

    격자를 만든 앵커 자신은 분모에서 뺀다. 안 그러면 앵커가 항상 맞아서
    점수가 공짜로 올라간다 (3점짜리 표본이면 66%가 그냥 나온다).
    """
    if not grid or not pivots:
        return 0.0

    anchor_idx = {a["index"] for a in grid["anchors"]}
    judged = [(i, p) for i, p in pivots if i not in anchor_idx and p > 0]
    if not judged:
        return 0.0

    lines = list(grid["lines"].values())
    on = 0
    for _, price in judged:
        if any(abs(price - ln) / price <= tol for ln in lines):
            on += 1
    return on / len(judged)


def _pick_anchors(pivots, count=3, min_bars=ANCHOR_MIN_BARS, min_pct=MIN_UNIT_PCT):
    """
    가장 최근 피벗 count개 — 단, 서로 충분히 떨어진 것만.

    그냥 뒤에서 3개를 자르면 안 된다. 거래가 뜸한 구간에서는 같은 값이
    연달아 피벗으로 잡히고(저가가 여러 봉 동일), 그 둘을 앵커로 쓰면
    간격 단위가 0에 가까워져 격자가 무의미해진다. 실제로 그렇게 짰다가
    평평한 계열에서 간격이 5로 잡혀 엉뚱한 격자가 나왔다.

    최근 것부터 거슬러 올라가며 **봉 간격과 가격 차가 둘 다 충분한** 것만
    받는다.
    """
    chosen = []
    for i, p in sorted(pivots, key=lambda x: -x[0]):     # 최근 → 과거
        if p <= 0:
            continue
        if not chosen:
            chosen.append((i, p))
            continue
        last_i, last_p = chosen[-1]
        if last_i - i < min_bars:
            continue
        if abs(p - last_p) / last_p < min_pct:
            continue
        chosen.append((i, p))
        if len(chosen) == count:
            break
    return sorted(chosen)                                 # 시간순으로 되돌린다


def nearest_line(grid, price):
    """현재가에서 가장 가까운 격자선. (번호, 가격, 이격%, 위/아래)"""
    if not grid or price <= 0:
        return None
    n, ln = min(grid["lines"].items(), key=lambda kv: abs(kv[1] - price))
    return {
        "number"  : n,
        "price"   : ln,
        "gap"     : abs(ln - price) / price,
        "side"    : "현재가 위" if ln > price else "현재가 아래",
    }


def analyse(df, price=None, basis="low", width=level_map.DEFAULT_PIVOT_WIDTH,
            min_fit=MIN_FIT, tol=FIT_TOL, min_judged=MIN_JUDGED_PIVOTS):
    """
    DataFrame → 격자선 한 벌.

    basis: "low"(저점기준) | "high"(고점기준)
      사이트가 두 기준을 따로 내는 이유는, 상승 구조는 저점이 규칙적이고
      하락 구조는 고점이 규칙적이기 때문이다. 같은 종목에서 둘 중 하나만
      적합도가 나오는 게 정상이다.

    반환 None = 봉 부족 / 격자 실패 / 적합도 미달. 숫자를 억지로 내지 않는다.
    """
    highs, lows, closes = _ohlc(df)
    if not closes or len(closes) < MIN_BARS:
        return None

    px = price if price else closes[-1]
    if not px or px <= 0:
        return None

    sw_h, sw_l = level_map.find_pivots(highs, lows, width)
    pivots = sw_l if basis == "low" else sw_h
    if len(pivots) < 3:
        return None

    # 사다리는 관측된 피벗과 현재가를 모두 덮도록 만든다.
    prices = [p for _, p in pivots] + [px]
    anchors = _pick_anchors(pivots, 3)
    grid = build_grid(anchors, cover=(min(prices), max(prices)))
    if not grid:
        return None

    anchor_idx = {a["index"] for a in grid["anchors"]}
    judged = [p for p in pivots if p[0] not in anchor_idx]
    if len(judged) < min_judged:
        return None

    fit = fit_score(grid, pivots, tol)
    if fit < min_fit:
        return None

    return {
        "basis"   : basis,
        "basis_kr": "저점기준" if basis == "low" else "고점기준",
        "price"   : px,
        "unit"    : grid["unit"],
        "unit_pct": abs(grid["unit"]) / grid["base"],
        "lines"   : grid["lines"],
        "anchors" : grid["anchors"],
        "fit"     : round(fit, 3),
        "nearest" : nearest_line(grid, px),
        "pivots"  : len(pivots),
        "judged"  : len(judged),
        "bars"    : len(closes),
    }


def best_basis(df, price=None, **kw):
    """저점기준·고점기준 중 적합도가 높은 쪽. 둘 다 미달이면 None."""
    cands = [g for g in (analyse(df, price, "low", **kw),
                         analyse(df, price, "high", **kw)) if g]
    return max(cands, key=lambda g: g["fit"]) if cands else None


# ================================
# 📡 전 종목 스캔
# ================================

def scan_universe(symbols, get_ohlcv_fn, timeframes=("1w", "1M"),
                  get_price_fn=None, max_gap=0.03, limit=50):
    """
    전 종목 × 전 TF 중 '지금 격자선에 닿아 있는' 순으로 세운 표.

    사이트가 월봉·주봉을 쓰는 이유가 있다. 격자는 간격의 규칙성을 보는
    것이라 봉이 짧으면 노이즈가 규칙처럼 보인다.

    max_gap: 이 이격 밖은 버린다 (기본 3%)
    """
    rows = []
    for symbol in symbols:
        live = None
        if get_price_fn:
            try:
                live = get_price_fn(symbol)
            except Exception:
                live = None

        for tf in timeframes:
            try:
                df = get_ohlcv_fn(symbol, tf, limit=300)
            except Exception as e:
                print(f"[격자선] 조회 실패 ({symbol} {tf}): {e}")
                continue
            if df is None or len(df) == 0:
                continue

            g = best_basis(df, price=live)
            if not g or not g["nearest"]:
                continue
            if g["nearest"]["gap"] > max_gap:
                continue

            rows.append({
                "symbol"   : symbol,
                "coin"     : symbol.replace("/USDT", ""),
                "timeframe": tf,
                **g,
            })

    # 이격이 작은 순 — 닿아 있는 것부터. 동률이면 적합도 높은 쪽.
    rows.sort(key=lambda r: (r["nearest"]["gap"], -r["fit"]))
    return rows[:limit]


# ================================
# 📋 리포트
# ================================

def _fmt_line_no(n):
    """0.0 → '0번선', -0.5 → '-0.5번선', 1.0 → '1번선'"""
    return f"{int(n) if float(n).is_integer() else n}번선"


def get_report(symbol, df, price=None):
    """단일 종목 격자선 리포트."""
    coin = symbol.replace("/USDT", "")
    g = best_basis(df, price=price)
    if not g:
        return (f"📭 {coin} — 격자를 세울 만한 구조가 없어요.\n"
                f"(피벗 부족이거나 구조 적합도 {MIN_FIT} 미만)")

    near = g["nearest"]
    lines = [
        f"📐 <b>{coin} 격자선</b> ({g['basis_kr']})",
        "━━━━━━━━━━━━━━━━━━━━",
        f"현재가 <b>{g['price']:,.6g}</b>",
        f"구조 적합도 <b>{g['fit']}</b>  (앵커 뺀 피벗 {g['judged']}개 채점)",
        f"격자 간격 {abs(g['unit']):,.6g} ({g['unit_pct']*100:.1f}%)",
        "",
        f"<b>가장 가까운 선</b>  {_fmt_line_no(near['number'])} "
        f"{near['price']:,.6g} {near['side']}",
        f"  이격 {near['gap']*100:.2f}%",
        "",
        "<b>격자</b> (현재가 주변)",
    ]
    # 사다리 전체가 아니라 현재가 주변만 보여 준다 — 멀리 있는 선은 지금
    # 판단에 쓰이지 않고 목록만 길어진다.
    near_n = near["number"]
    for n in sorted(g["lines"], reverse=True):
        if abs(n - near_n) > 2.0:
            continue
        ln = g["lines"][n]
        mark = " ←" if n == near_n else ""
        lines.append(f"  {_fmt_line_no(n):>8}  {ln:,.6g}{mark}")

    lines += ["", "<b>그은 점</b> (차트에 직접 대보세요)"]
    for a in g["anchors"]:
        lines.append(f"  봉 {a['index']}  {a['price']:,.6g}")

    lines += [
        "━━━━━━━━━━━━━━━━━━━━",
        "※ 적합도는 이 격자가 과거 피벗을 얼마나 설명하는지입니다.",
        "  닿는 자리에서 어떻게 반응하는지가 관찰 대상이며,",
        "  도달 자체가 방향을 뜻하지 않습니다.",
    ]
    return "\n".join(lines)


def get_scan_report(rows, limit=20):
    """전 종목 격자 근접 리포트."""
    if not rows:
        return "📭 지금 격자선에 닿아 있는 종목이 없어요."

    lines = [
        "📐 <b>격자선 근접</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"{len(rows)}종목이 격자선 3% 이내에 있습니다 (적합도 {MIN_FIT} 이상만).",
        "",
    ]
    for r in rows[:limit]:
        near = r["nearest"]
        lines.append(
            f"▪️ <b>{r['coin']}</b> {r['timeframe']} · {r['basis_kr']} "
            f"{_fmt_line_no(near['number'])}\n"
            f"    {near['price']:,.6g} {near['side']}  이격 {near['gap']*100:.2f}%  "
            f"적합도 {r['fit']}"
        )
    lines += [
        "━━━━━━━━━━━━━━━━━━━━",
        "※ 적합도가 낮은 종목은 애초에 목록에 넣지 않았습니다.",
    ]
    return "\n".join(lines)
