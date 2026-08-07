"""
expected_range.py — 다음 봉 예상 범위 (전 종목 · 전 타임프레임)

MarketSurfer의 '전 종목 다음 봉 예상 범위' 화면을 봇으로 옮긴 모듈.

무엇을 계산하나
──────────────
"이 봉이 마감될 때까지 가격이 어디까지 갈 수 있나"를 과거 실현 변동성으로
추정한다. ATR 밴드처럼 위아래 대칭으로 잡지 않고, **직전 종가 대비 위 이탈폭과
아래 이탈폭을 각각 따로** 분포로 만들어 분위수를 쓴다.

    위 이탈폭   up[i] = (high[i] - close[i-1]) / close[i-1]
    아래 이탈폭 dn[i] = (close[i-1] - low[i])  / close[i-1]

코인은 아래쪽 꼬리가 위쪽보다 길다. 대칭 ATR 밴드는 그걸 담지 못해 하단을
항상 과소평가하고, 그 밴드로 손절을 잡으면 그만큼 자주 털린다. 위아래를
분리해서 재면 "롱 손절은 여기까지 열어둬야 한다"가 숫자로 나온다.

어디에 쓰나
──────────
1. 손절 폭 sanity check — SL이 예상 범위 하단보다 가까우면 '한 봉 안에 그냥
   닿는' 자리다. 시그널 품질과 무관하게 털린다.
2. 목표가 sanity check — TP1이 예상 범위 상단 밖이면 그 봉 안엔 안 온다.
3. 구간 내 위치 — 상단 부근이면 추격, 하단 부근이면 눌림. 같은 신호라도
   범위 어디서 뜬 신호인지에 따라 결과가 갈린다 (setup_ledger로 채점 가능).

정직성
──────
`coverage()`는 이 밴드가 과거에 실제로 몇 %를 담았는지 되돌려 채점한다.
분위수 0.7로 잡았는데 실측 커버리지가 0.5면 그 종목은 모델이 안 맞는 것이고,
그 사실을 숨기지 않고 같이 보여준다.
"""

import math

# 기본 분위수 — 과거 봉 중 70%가 이 안에서 끝났다는 뜻.
# 90%로 올리면 범위가 넓어져 "안 벗어남"이 당연해지고 판단에 못 쓴다.
DEFAULT_Q = 0.70

# 분포를 만들 최소 표본. 이보다 적으면 추정이 표본 하나에 휘둘린다.
MIN_SAMPLES = 30

# 분위수 계산에 쓸 최근 봉 수 (오래된 변동성 국면은 지금과 다르다)
DEFAULT_LOOKBACK = 120


def _quantile(sorted_vals, q):
    """선형보간 분위수 — numpy 없이도 돌게 직접 구현.

    이 모듈은 봇의 다른 파일에 끌려들어가지 않게 의존성을 최소로 둔다.
    """
    if not sorted_vals:
        return 0.0
    if q <= 0:
        return sorted_vals[0]
    if q >= 1:
        return sorted_vals[-1]
    pos = (len(sorted_vals) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return sorted_vals[int(pos)]
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def _excursions(highs, lows, closes, lookback):
    """직전 종가 대비 위/아래 이탈폭 시계열을 만든다.

    첫 봉은 직전 종가가 없으므로 제외한다.
    """
    ups, dns = [], []
    start = max(1, len(closes) - lookback)
    for i in range(start, len(closes)):
        prev_close = closes[i - 1]
        if not prev_close or prev_close <= 0:
            continue
        up = (highs[i] - prev_close) / prev_close
        dn = (prev_close - lows[i]) / prev_close
        # 갭으로 봉 전체가 직전 종가 위/아래에 있으면 반대쪽 이탈폭은 음수가
        # 된다. 음수를 그대로 넣으면 "아래로 -2% 갔다"가 분포에 섞여 하단을
        # 위로 밀어올린다. 0으로 눌러 "그쪽으로는 안 갔다"로 기록한다.
        ups.append(max(0.0, up))
        dns.append(max(0.0, dn))
    return ups, dns


def _ohlc_lists(df):
    """DataFrame → 파이썬 리스트. 봇의 get_ohlcv() 결과 형식을 그대로 받는다."""
    try:
        return (
            list(df["high"]),
            list(df["low"]),
            list(df["close"]),
        )
    except (KeyError, TypeError):
        return None, None, None


def expected_range(df, live_price=None, q=DEFAULT_Q, lookback=DEFAULT_LOOKBACK):
    """
    마감봉 DataFrame → 다음 한 봉의 예상 범위.

    df        : 봇 get_ohlcv() 결과 (마감봉만, timestamp/open/high/low/close/volume)
    live_price: 진행 중인 봉의 현재가. 없으면 마지막 종가를 쓴다.
    q         : 분위수 (0.70 = 과거 70%가 이 범위 안에서 끝났음)

    반환 None = 표본 부족. 숫자를 억지로 내지 않는다 —
    MarketSurfer도 "집계되지 않은 종목은 목록에 넣지 않습니다"라고 명시한다.
    """
    highs, lows, closes = _ohlc_lists(df)
    if not closes or len(closes) < MIN_SAMPLES + 1:
        return None

    ups, dns = _excursions(highs, lows, closes, lookback)
    if len(ups) < MIN_SAMPLES:
        return None

    up_r = _quantile(sorted(ups), q)
    dn_r = _quantile(sorted(dns), q)

    base = closes[-1]
    if not base or base <= 0:
        return None

    upper = base * (1 + up_r)
    lower = base * (1 - dn_r)
    width = upper - lower
    if width <= 0:
        return None

    price = live_price if live_price else base

    # 범위 안 위치 0~1. 밖으로 나가면 클립하되 out_of_range로 표시한다 —
    # "이미 예상 범위를 벗어났다"는 것 자체가 중요한 정보라 지우면 안 된다.
    raw_pos = (price - lower) / width
    pos = min(1.0, max(0.0, raw_pos))

    if raw_pos > 1:
        zone = "상단이탈"
    elif raw_pos < 0:
        zone = "하단이탈"
    elif pos >= 0.67:
        zone = "상단"
    elif pos <= 0.33:
        zone = "하단"
    else:
        zone = "중간"

    return {
        "base"        : base,        # 계산 기준 = 마지막 마감 종가
        "price"       : price,
        "lower"       : lower,
        "upper"       : upper,
        "lower_pct"   : -dn_r,       # 기준가 대비 (음수)
        "upper_pct"   : up_r,
        "width_pct"   : (upper - lower) / base,
        "position"    : pos,
        "raw_position": raw_pos,
        "zone"        : zone,
        "out_of_range": raw_pos > 1 or raw_pos < 0,
        "q"           : q,
        "samples"     : len(ups),
    }


def coverage(df, q=DEFAULT_Q, lookback=DEFAULT_LOOKBACK, warmup=MIN_SAMPLES):
    """
    이 밴드가 과거에 실제로 몇 %의 봉을 담았는지 되돌려 채점한다.

    각 시점에서 '그 시점까지의 데이터만으로' 밴드를 만들고 다음 봉이 그 안에서
    끝났는지 본다 (look-ahead 없음). 결과가 q에 한참 못 미치면 그 종목·TF에서는
    이 추정이 안 맞는다는 뜻이고, 그러면 밴드를 손절 근거로 쓰면 안 된다.
    """
    highs, lows, closes = _ohlc_lists(df)
    if not closes or len(closes) < warmup + 10:
        return None

    inside = 0
    total = 0
    for i in range(warmup, len(closes)):
        ups, dns = _excursions(highs[:i], lows[:i], closes[:i], lookback)
        if len(ups) < warmup:
            continue
        base = closes[i - 1]
        if not base or base <= 0:
            continue
        upper = base * (1 + _quantile(sorted(ups), q))
        lower = base * (1 - _quantile(sorted(dns), q))
        # 다음 봉이 밴드 안에서 '끝났나'가 아니라 밴드를 '건드리지 않았나'로 본다.
        # 손절/목표는 종가가 아니라 고저에 닿는 순간 체결되기 때문이다.
        total += 1
        if highs[i] <= upper and lows[i] >= lower:
            inside += 1

    if not total:
        return None
    return {
        "nominal_q"  : q,
        "actual"     : inside / total,
        "samples"    : total,
        # 실측이 명목보다 크게 낮으면 밴드가 좁다 = 손절이 자주 스친다
        "band_too_tight": (inside / total) < q - 0.10,
    }


def scan_universe(symbols, get_ohlcv_fn, timeframe="4h", get_price_fn=None,
                  q=DEFAULT_Q, limit=None):
    """
    전 종목 예상 범위 표 (MarketSurfer '전 종목 다음 봉 예상 범위').

    get_ohlcv_fn(symbol, timeframe, limit=...) → DataFrame
    get_price_fn(symbol) → float  (없으면 마감 종가 사용)

    반환: 행 리스트. 집계 실패한 종목은 애초에 넣지 않는다.
    """
    rows = []
    for symbol in symbols:
        try:
            df = get_ohlcv_fn(symbol, timeframe, limit=DEFAULT_LOOKBACK + 20)
        except Exception as e:
            print(f"[예상범위] 조회 실패 ({symbol} {timeframe}): {e}")
            continue
        if df is None or len(df) == 0:
            continue

        live = None
        if get_price_fn:
            try:
                live = get_price_fn(symbol)
            except Exception:
                live = None

        er = expected_range(df, live_price=live, q=q)
        if not er:
            continue
        er["symbol"] = symbol
        er["coin"] = symbol.replace("/USDT", "")
        er["timeframe"] = timeframe
        rows.append(er)

    if limit:
        rows = rows[:limit]
    return rows


def rank(rows, by="wide"):
    """
    표 정렬 — MarketSurfer의 정렬 버튼과 동일한 네 가지.

    wide   : 범위 넓은 순  (변동성 큰 종목 = 손절 넓게 잡아야 하는 종목)
    narrow : 범위 좁은 순  (스퀴즈 후보)
    top    : 상단 부근     (추격 위험 / 숏 관심)
    bottom : 하단 부근     (눌림 / 롱 관심)
    """
    if by == "wide":
        return sorted(rows, key=lambda r: r["width_pct"], reverse=True)
    if by == "narrow":
        return sorted(rows, key=lambda r: r["width_pct"])
    if by == "top":
        return sorted(rows, key=lambda r: r["raw_position"], reverse=True)
    if by == "bottom":
        return sorted(rows, key=lambda r: r["raw_position"])
    return rows


def check_trade_levels(er, entry, sl, tp1):
    """
    시그널의 SL/TP를 예상 범위에 대보는 sanity check.

    페이퍼 트레이딩에서 '왜 이 신호는 항상 털렸나'를 사후에 따지지 않고
    사전에 거르기 위한 것. 반환은 경고 문자열 리스트 (비어 있으면 통과).

    ⚠️ 방향을 반드시 가려야 한다
    ──────────────────────────
    처음엔 손절을 늘 하단 여유와, 목표를 늘 상단 여유와 비교했다. 롱에서는
    맞지만 **숏에서는 정확히 반대**다 — 숏의 손절은 위에 있으므로 상단
    여유와 재야 하고, 목표는 아래에 있으므로 하단 여유와 재야 한다.

    이 모듈이 존재하는 이유가 "코인은 위아래 폭이 다르다"인데, 정작 숏에서
    두 폭을 바꿔 쓰면 경고가 반대로 나온다. 실제로 상단 1.15% / 하단 3.51%인
    계열에서 숏 손절 0.2%를 "하단 3.5%의 절반보다 가깝다"고 잡았다 —
    맞는 결론이지만 근거가 틀렸고, 폭이 뒤집힌 종목에서는 결론까지 틀린다.

    방향은 손절 위치로 판단한다 (risk_calc.plan()과 같은 규칙).
    """
    if not er or not entry:
        return []

    warnings = []
    base = er["base"]
    dn_room = (base - er["lower"]) / base   # 한 봉 안에 아래로 갈 수 있는 폭
    up_room = (er["upper"] - base) / base

    # 손절이 진입가 아래면 롱, 위면 숏. 손절이 없으면 목표로 가른다.
    if sl:
        is_long = sl < entry
    elif tp1:
        is_long = tp1 > entry
    else:
        is_long = True

    # 손절이 놓이는 쪽 / 목표가 놓이는 쪽
    sl_room, sl_side = (dn_room, "하단") if is_long else (up_room, "상단")
    tp_room, tp_side = (up_room, "상단") if is_long else (dn_room, "하단")

    if sl:
        sl_dist = abs(entry - sl) / entry
        if sl_dist < sl_room * 0.5:
            warnings.append(
                f"손절이 한 봉 예상 {sl_side}의 절반보다 가깝다 "
                f"(손절 {sl_dist*100:.1f}% vs 예상 {sl_side} {sl_room*100:.1f}%) — "
                f"신호 품질과 무관하게 노이즈로 털릴 자리"
            )

    if tp1:
        tp_dist = abs(tp1 - entry) / entry
        if tp_dist > tp_room * 2:
            warnings.append(
                f"TP1이 한 봉 예상 {tp_side}의 2배 밖 "
                f"(목표 {tp_dist*100:.1f}% vs 예상 {tp_side} {tp_room*100:.1f}%) — "
                f"여러 봉을 들고 가야 닿는 목표"
            )

    if er.get("out_of_range"):
        warnings.append(f"현재가가 이미 예상 범위 {er['zone']} — 추격 구간")

    return warnings


def get_report(rows, by="wide", limit=15, timeframe="4h"):
    """텔레그램용 요약 리포트."""
    if not rows:
        return "📭 예상 범위를 계산할 수 있는 종목이 없어요 (표본 부족)."

    ranked = rank(rows, by)[:limit]
    by_label = {
        "wide": "범위 넓은 순", "narrow": "범위 좁은 순",
        "top": "상단 부근", "bottom": "하단 부근",
    }

    lines = [
        f"📐 <b>다음 봉 예상 범위 ({timeframe})</b>",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"{by_label.get(by, by)} · {len(rows)}종목 집계",
        f"과거 {int(rows[0]['q']*100)}%의 봉이 이 범위 안에서 끝났습니다.",
        "",
    ]
    for r in ranked:
        zone_emoji = {"상단": "🔺", "하단": "🔻", "중간": "▪️",
                      "상단이탈": "🚨", "하단이탈": "🚨"}.get(r["zone"], "▪️")
        lines.append(
            f"{zone_emoji} <b>{r['coin']}</b>  폭 {r['width_pct']*100:.1f}%  [{r['zone']}]\n"
            f"    {r['lower']:,.4g} ~ {r['upper']:,.4g}  (현재 {r['price']:,.4g})"
        )

    lines += [
        "━━━━━━━━━━━━━━━━━━━━",
        "※ 통계로 낸 범위라 벗어날 수 있습니다. 손절 폭이 이 범위보다",
        "  좁으면 신호와 무관하게 털립니다.",
    ]
    return "\n".join(lines)
