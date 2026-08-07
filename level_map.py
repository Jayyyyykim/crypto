"""
level_map.py — 지지·저항 1·2·3차 + 관성 무효화가 + 레벨 근접 레이더

MarketSurfer의 '전 종목 지지·저항 한 표' / '레벨 근접 TOP 50'을 봇으로 옮긴 모듈.

왜 필요한가
──────────
지금 봇의 analyze_timeframe()은 지지·저항을 이렇게 준다:

    "support"    : recent_low     # 최근 20봉 최저가
    "resistance" : recent_high    # 최근 20봉 최고가

레벨이 봉당 딱 하나씩이고, 그마저 '최근 20봉의 극값'이라 한 번 찍고 만
꼬리에도 잡힌다. 그런데 detect_core_signal()은 이 레벨 0.5% 이내에서만
발화한다 — 레벨이 부실하면 신호도 그만큼 부실하다. 페이퍼 트레이딩이
0건이거나 자꾸 털리는 원인이 전략이 아니라 여기일 수 있다.

이 모듈은 대신 이렇게 한다:
  1. 스윙 고/저점(프랙탈)을 뽑고
  2. 서로 가까운 것끼리 묶어 하나의 '레벨'로 만들고 (여러 번 닿은 자리가 진짜)
  3. 터치 횟수·최근성으로 강도를 매겨 현재가 기준 1·2·3차로 세운다

터치가 여러 번인 자리가 위쪽으로 뚫리면 그게 진짜 돌파고, 한 번 스친
꼬리를 뚫는 건 아무 의미가 없다. 그 구분이 지금 봇에는 없다.

관성 무효화가 (invalidation)
───────────────────────────
"지금 읽고 있는 방향이 틀렸다고 인정해야 하는 가격". 상승 읽기라면 마지막으로
확인된 higher-low, 하락 읽기라면 마지막 lower-high다. 손절을 임의의 %로
잡는 대신 구조가 깨지는 자리에 두라는 뜻 — MarketSurfer가 SR 표에
'관성 무효화 가격'을 한 칸 따로 두는 이유가 이것이다.
"""

# 프랙탈 판정 폭 — 좌우 이만큼보다 높으면(낮으면) 스윙 고점(저점)
DEFAULT_PIVOT_WIDTH = 3

# 레벨 병합 허용 오차 (가격 대비). 0.6% 이내면 같은 자리로 본다.
# 너무 좁게 잡으면 같은 지지선이 3개로 쪼개지고, 너무 넓게 잡으면
# 서로 다른 층이 뭉개진다.
DEFAULT_CLUSTER_TOL = 0.006

# 레벨 하나로 인정할 최소 봉 수
MIN_BARS = 40


def _ohlc(df):
    try:
        return list(df["high"]), list(df["low"]), list(df["close"])
    except (KeyError, TypeError):
        return None, None, None


def find_pivots(highs, lows, width=DEFAULT_PIVOT_WIDTH):
    """
    프랙탈 스윙 고점/저점.

    반환: (highs_list, lows_list) — 각 원소 (index, price)

    주의: 마지막 width개 봉은 오른쪽이 아직 안 채워져 스윙으로 확정할 수 없다.
    확정 안 된 걸 레벨로 쓰면 미래를 훔쳐보는 셈이라 제외한다.
    """
    sw_h, sw_l = [], []
    n = len(highs)
    for i in range(width, n - width):
        h = highs[i]
        l = lows[i]
        if all(h >= highs[i - j] for j in range(1, width + 1)) and \
           all(h >= highs[i + j] for j in range(1, width + 1)):
            sw_h.append((i, h))
        if all(l <= lows[i - j] for j in range(1, width + 1)) and \
           all(l <= lows[i + j] for j in range(1, width + 1)):
            sw_l.append((i, l))
    return sw_h, sw_l


def cluster_levels(pivots, total_bars, tol=DEFAULT_CLUSTER_TOL):
    """
    가까운 피벗끼리 묶어 레벨로 만든다.

    각 레벨: {price(터치 평균), touches, last_idx, recency, strength}

    strength = 터치 횟수 × 최근성. 5년 전에 세 번 닿고 그 뒤로 안 온 자리보다
    지난달에 두 번 닿은 자리가 지금 더 유효하다.
    """
    if not pivots:
        return []

    ordered = sorted(pivots, key=lambda p: p[1])
    groups = []
    cur = [ordered[0]]
    for idx, price in ordered[1:]:
        # 기준은 그룹의 **첫 값**이다. 직전 값과 비교하면 사슬처럼 이어져
        # 클러스터가 tol보다 훨씬 넓어진다 — 0.5%씩 떨어진 피벗 8개가
        # tol 0.6%인데도 3.6% 폭의 레벨 하나로 뭉쳤다. 그러면 "여러 번
        # 닿은 자리"가 아니라 "넓은 구간"이 되어 레벨의 뜻이 사라진다.
        # 첫 값 기준이면 클러스터 폭이 tol을 넘지 않는다.
        ref = cur[0][1]
        if ref > 0 and abs(price - ref) / ref <= tol:
            cur.append((idx, price))
        else:
            groups.append(cur)
            cur = [(idx, price)]
    groups.append(cur)

    levels = []
    for g in groups:
        prices = [p for _, p in g]
        idxs = [i for i, _ in g]
        last_idx = max(idxs)
        # 최근성 0~1 — 마지막 터치가 지금에 가까울수록 1
        recency = (last_idx + 1) / total_bars if total_bars else 0
        levels.append({
            "price"   : sum(prices) / len(prices),
            "touches" : len(g),
            "last_idx": last_idx,
            "recency" : round(recency, 3),
            "strength": round(len(g) * (0.4 + 0.6 * recency), 3),
        })
    return levels


def build_level_map(df, price=None, width=DEFAULT_PIVOT_WIDTH,
                    tol=DEFAULT_CLUSTER_TOL, depth=3):
    """
    DataFrame → 현재가 기준 지지 1·2·3차 / 저항 1·2·3차.

    price: 현재가. 없으면 마지막 종가.
    depth: 몇 차까지 낼지 (기본 3 — MarketSurfer와 동일)

    반환 None = 봉 부족.
    """
    highs, lows, closes = _ohlc(df)
    if not closes or len(closes) < MIN_BARS:
        return None

    px = price if price else closes[-1]
    if not px or px <= 0:
        return None

    sw_h, sw_l = find_pivots(highs, lows, width)
    n = len(closes)

    # 지지 후보는 스윙 저점, 저항 후보는 스윙 고점에서 출발하되,
    # 뚫린 지지는 저항이 된다(역할 전환)는 고전적 성질을 살려 양쪽 다 섞는다.
    all_pivots = sw_h + sw_l
    levels = cluster_levels(all_pivots, n, tol)

    supports = [l for l in levels if l["price"] < px]
    resistances = [l for l in levels if l["price"] > px]

    # 1차 = 현재가에서 가장 가까운 자리. 강도가 아니라 거리 순이다 —
    # 가격은 가까운 벽부터 부딪힌다.
    supports.sort(key=lambda l: px - l["price"])
    resistances.sort(key=lambda l: l["price"] - px)

    def _fmt(lst):
        out = []
        for rank, l in enumerate(lst[:depth], start=1):
            out.append({
                "rank"    : rank,
                "price"   : l["price"],
                "distance": abs(px - l["price"]) / px,
                "touches" : l["touches"],
                "recency" : l["recency"],
                "strength": l["strength"],
            })
        return out

    sup = _fmt(supports)
    res = _fmt(resistances)

    nearest = None
    cands = []
    if sup:
        cands.append(("support", sup[0]))
    if res:
        cands.append(("resistance", res[0]))
    if cands:
        side, lv = min(cands, key=lambda c: c[1]["distance"])
        nearest = {"side": side, **lv}

    return {
        "price"       : px,
        "supports"    : sup,
        "resistances" : res,
        "nearest"     : nearest,
        "invalidation": invalidation_price(sw_h, sw_l, px),
        "bars"        : n,
    }


def invalidation_price(swing_highs, swing_lows, price):
    """
    관성 무효화가 — 지금의 방향 읽기가 틀렸다고 인정해야 하는 가격.

    상승 읽기(현재가가 마지막 스윙 저점 위): 마지막으로 확인된 스윙 저점.
      그 아래로 종가가 내려가면 higher-low 구조가 깨진 것이다.
    하락 읽기: 마지막으로 확인된 스윙 고점.

    손절을 "진입가 -2%"처럼 임의로 잡는 대신 여기에 두면, 털렸을 때
    "노이즈에 스쳤다"가 아니라 "읽기가 틀렸다"가 된다 — 그래야 채점이 된다.
    """
    last_low = swing_lows[-1][1] if swing_lows else None
    last_high = swing_highs[-1][1] if swing_highs else None

    if last_low is not None and price > last_low:
        return {"direction": "up", "price": last_low,
                "distance": (price - last_low) / price,
                "note": "이 아래로 마감하면 상승 구조 붕괴"}
    if last_high is not None and price < last_high:
        return {"direction": "down", "price": last_high,
                "distance": (last_high - price) / price,
                "note": "이 위로 마감하면 하락 구조 붕괴"}
    return None


# ================================
# 📡 레벨 근접 레이더 (전 종목 횡단)
# ================================

def level_radar(symbols, get_ohlcv_fn, timeframes=("4h", "1d"),
                get_price_fn=None, max_distance=0.05, limit=50):
    """
    전 종목 × 전 TF 중 '지금 레벨에 가장 가까운' 순으로 세운 표.

    detect_core_signal()은 레벨 0.5% 이내에서만 발화한다. 이 레이더는
    그 0.5%에 들어오기 전 단계 — 1~5% 거리에 있는 종목들 — 을 미리 보여준다.
    시그널이 뜨고 나서 보는 게 아니라, 뜰 자리를 미리 아는 용도다.

    max_distance: 이 거리 밖은 버린다 (기본 5%)
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
                print(f"[레벨레이더] 조회 실패 ({symbol} {tf}): {e}")
                continue
            if df is None or len(df) == 0:
                continue

            lm = build_level_map(df, price=live)
            if not lm or not lm.get("nearest"):
                continue

            near = lm["nearest"]
            if near["distance"] > max_distance:
                continue

            rows.append({
                "symbol"      : symbol,
                "coin"        : symbol.replace("/USDT", ""),
                "timeframe"   : tf,
                "side"        : near["side"],
                "level"       : near["price"],
                "distance"    : near["distance"],
                "touches"     : near["touches"],
                "strength"    : near["strength"],
                "price"       : lm["price"],
                "invalidation": lm.get("invalidation"),
            })

    rows.sort(key=lambda r: r["distance"])
    return rows[:limit]


def get_level_report(symbol, df, price=None):
    """단일 종목 SR 리포트 (텔레그램)."""
    lm = build_level_map(df, price=price)
    coin = symbol.replace("/USDT", "")
    if not lm:
        return f"📭 {coin} — 레벨을 세울 만큼 봉이 없어요."

    lines = [
        f"📏 <b>{coin} 지지·저항</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"현재가 <b>{lm['price']:,.4g}</b>",
        "",
        "<b>저항</b>",
    ]
    if lm["resistances"]:
        for r in reversed(lm["resistances"]):
            lines.append(
                f"  {r['rank']}차  {r['price']:,.4g}  "
                f"(+{r['distance']*100:.2f}%, 터치 {r['touches']}회)"
            )
    else:
        lines.append("  위쪽에 잡힌 레벨 없음 (신고가 구간)")

    lines += ["", "<b>지지</b>"]
    if lm["supports"]:
        for s in lm["supports"]:
            lines.append(
                f"  {s['rank']}차  {s['price']:,.4g}  "
                f"(-{s['distance']*100:.2f}%, 터치 {s['touches']}회)"
            )
    else:
        lines.append("  아래쪽에 잡힌 레벨 없음 (신저가 구간)")

    inv = lm.get("invalidation")
    if inv:
        lines += [
            "",
            f"<b>관성 무효화</b>  {inv['price']:,.4g} "
            f"({inv['distance']*100:.2f}% 거리)",
            f"  {inv['note']}",
        ]

    lines += [
        "━━━━━━━━━━━━━━━━━━━━",
        "※ 터치 횟수가 많은 자리가 진짜 벽입니다.",
        "  한 번 스친 꼬리를 뚫는 건 돌파가 아닙니다.",
    ]
    return "\n".join(lines)


def get_radar_report(rows, limit=20):
    """레벨 근접 레이더 리포트 (텔레그램)."""
    if not rows:
        return "📭 지금 레벨 근처에 온 종목이 없어요."

    lines = [
        "📡 <b>레벨 근접 레이더</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"{len(rows)}종목이 지지·저항 5% 이내에 들어와 있습니다.",
        "가까운 순 — 시그널이 뜬 게 아니라 '뜰 자리'입니다.",
        "",
    ]
    for r in rows[:limit]:
        emoji = "🟢" if r["side"] == "support" else "🔴"
        side_kr = "지지" if r["side"] == "support" else "저항"
        lines.append(
            f"{emoji} <b>{r['coin']}</b> {r['timeframe']}  {side_kr} "
            f"{r['level']:,.4g}  거리 {r['distance']*100:.2f}%  "
            f"(터치 {r['touches']}회)"
        )
    lines += [
        "━━━━━━━━━━━━━━━━━━━━",
        "※ 거리는 조회 시점 기준입니다.",
    ]
    return "\n".join(lines)
