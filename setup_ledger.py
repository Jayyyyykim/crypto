"""
setup_ledger.py — 셋업 자동 채점 원장

MarketSurfer의 '쏠린 자리 · 전일 고저 · 구간 위치' 채점표를 봇으로 옮기고,
거기서 한 걸음 더 나간 모듈.

━━ 왜 이게 페이퍼 트레이딩보다 먼저인가 ━━

지금 봇의 승격 게이트는 "4주 + 30건"이다. 그런데 detect_core_signal()은
'일봉 지지 0.5% 이내 + 4H 상승 + RSI 과매도'가 동시에 맞아야 발화한다.
/페이퍼진단이 직접 말하듯 "횡보장엔 며칠씩 0건일 수 있어요 (정상)".
30건을 채우는 데 몇 달이 걸리고, 30건은 통계적으로 아무것도 증명하지 못한다.
(승률 55%와 45%를 30표본으로 구분할 수 없다.)

친구 사이트는 같은 문제를 다르게 풀었다. 진입을 세지 않고 **셋업 사건**을
센다. 종목마다 매일 "전일 고점을 돌파했나 / 아래쪽 유동성이 쓸렸나 /
구간 어디에 있나"를 기록하고, 1일·3일·주 뒤에 자동으로 채점한다.
그래서 표본이 1,833건이다. 진입 30건이 아니라 사건 1,833건.

이 모듈은 그걸 그대로 하되, **과거 봉으로 소급 채점(backfill)**까지 한다.
4주를 기다릴 필요가 없다 — 오늘 돌리면 오늘 수천 건이 쌓인다.

━━ 사이트 숫자에서 발견한 것 (중요) ━━

사이트가 공개한 3일 기준 적중률:

    디스카운트 구간에서    326건  81.9%      프리미엄 구간에서    189건  37.0%
    아래쪽 유동성 쓸린 뒤  405건  74.8%      위쪽 유동성 쓸린 뒤  468건  26.3%
    전일 고점 돌파 뒤      226건  69.5%      전일 저점 이탈 뒤    219건  28.3%

왼쪽(전부 '상승' 방향 셋업)은 죄다 70~82%, 오른쪽(전부 '하락' 방향)은 죄다
26~37%. 각 쌍이 대략 100%로 합쳐진다. 그리고 전체 방향 적중률은 53.6%.

이건 셋업이 좋아서 나온 숫자가 아니다. **표본 기간에 시장이 올랐다**는
뜻이다. 상승 드리프트가 있는 구간에서 '상승 셋업'을 채점하면 뭘 골라도
70%가 나온다. 즉 저 표는 셋업의 실력이 아니라 시장 방향을 재고 있다.

그래서 이 모듈은 두 가지를 더 낸다:

  1. **기준선(baseline)** — 같은 기간, 같은 horizon에서 아무 날이나 잡았을 때의
     상승/하락 확률. 무조건부 전방 수익률로 직접 계산한다.
  2. **초과 적중률(edge)** = 적중률 − 그 방향의 기준선.
     이게 0 근처면 그 셋업은 시장 방향을 따라간 것뿐이고, 실력이 아니다.

여기에 환경(regime_gate) 라벨을 같이 붙여 저장하므로,
"디스카운트 롱은 상승국면일 때만 되는가, 관망일 때도 되는가"를
표본으로 답할 수 있다. 게이트를 켤 가치가 있는지 자체가 채점된다.
"""

import time
from datetime import datetime

import console
import jsonstore

LEDGER_FILE = "setup_ledger.json"

# 보합 밴드 — 이 안에서 끝나면 적중도 실패도 아니다.
# 사이트가 "±1% 이내는 보합"으로 표기하므로 같은 값을 기본으로 둔다
# (숫자를 그대로 비교할 수 있게 하려는 의도).
FLAT_BAND = 0.01

# 채점 시점 (봉 개수). 일봉이면 1일·3일·1주·1달에 해당.
DEFAULT_HORIZONS = (1, 3, 7, 30)

# 구간 위치를 잴 룩백 (일봉 20봉 ≈ 한 달)
RANGE_LOOKBACK = 20

# 프리미엄/디스카운트 경계
PREMIUM_AT = 0.70
DISCOUNT_AT = 0.30

# ATR 기간 (MFE/MAE 정규화용)
ATR_PERIOD = 14

EVENT_LABELS = {
    "sweep_up"       : "위쪽 유동성이 쓸린 뒤",
    "sweep_down"     : "아래쪽 유동성이 쓸린 뒤",
    "break_prev_high": "전일 고점 돌파 뒤",
    "lose_prev_low"  : "전일 저점 이탈 뒤",
    "premium_zone"   : "프리미엄 구간에서",
    "discount_zone"  : "디스카운트 구간에서",
}

EVENT_DIRECTION = {
    "sweep_up"       : "down",   # 위를 쓸고 되돌아왔다 = 위쪽 물량 소진
    "sweep_down"     : "up",
    "break_prev_high": "up",
    "lose_prev_low"  : "down",
    "premium_zone"   : "down",   # 구간 상단 = 평균회귀 기대
    "discount_zone"  : "up",
}


# ================================
# 🔢 보조 계산
# ================================

def _ohlc(df):
    try:
        return (list(df["high"]), list(df["low"]),
                list(df["close"]), list(df.get("timestamp", [])))
    except (KeyError, TypeError, AttributeError):
        return None, None, None, None


def _atr_series(highs, lows, closes, period=ATR_PERIOD):
    """단순 ATR (Wilder 평활 대신 이동평균 — 정규화 용도라 충분하다)."""
    trs = [highs[0] - lows[0]] if highs else []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)

    out = [None] * len(trs)
    run = 0.0
    for i, tr in enumerate(trs):
        run += tr
        if i >= period:
            run -= trs[i - period]
        if i >= period - 1:
            out[i] = run / period
    return out


def _bar_date(ts_list, i):
    if not ts_list or i >= len(ts_list):
        return None
    try:
        return ts_list[i].strftime("%Y-%m-%d")
    except AttributeError:
        return str(ts_list[i])[:10]


def _range_position(highs, lows, closes, i, lookback=RANGE_LOOKBACK):
    """최근 lookback봉 레인지 안에서 현재 종가의 위치 0~1."""
    start = max(0, i - lookback + 1)
    hi = max(highs[start:i + 1])
    lo = min(lows[start:i + 1])
    if hi <= lo:
        return None
    return (closes[i] - lo) / (hi - lo)


# ================================
# 🎯 사건 탐지
# ================================

def detect_at(highs, lows, closes, i, lookback=RANGE_LOOKBACK):
    """
    i번째 마감봉에서 발생한 셋업 사건들.

    반환: [{"type", "direction"}] — 심볼/가격은 호출자가 붙인다.

    sweep_up과 break_prev_high는 배타적이다 (종가가 전일 고가 아래냐 위냐).
    프리미엄/디스카운트는 다른 사건과 같이 뜰 수 있다 — 그게 정상이고,
    나중에 "고점 돌파 × 프리미엄 구간"처럼 교차 집계할 수 있게 남겨둔다.
    """
    if i < 1 or i >= len(closes):
        return []

    events = []
    ph, pl = highs[i - 1], lows[i - 1]
    h, l, c = highs[i], lows[i], closes[i]

    # ── 전일 고저 대비 ──
    if h > ph:
        if c < ph:
            events.append("sweep_up")        # 뚫었다가 되돌아옴
        else:
            events.append("break_prev_high")  # 뚫고 유지
    if l < pl:
        if c > pl:
            events.append("sweep_down")
        else:
            events.append("lose_prev_low")

    # ── 구간 위치 ──
    pos = _range_position(highs, lows, closes, i, lookback)
    if pos is not None:
        if pos >= PREMIUM_AT:
            events.append("premium_zone")
        elif pos <= DISCOUNT_AT:
            events.append("discount_zone")

    return [{"type": t, "direction": EVENT_DIRECTION[t]} for t in events]


# ================================
# 📏 채점
# ================================

def score_event(highs, lows, closes, i, direction, horizon,
                atr_pct=None, flat_band=FLAT_BAND):
    """
    i번째 봉에서 발생한 사건을 horizon봉 뒤에 채점한다.

    반환 None = 아직 미래 봉이 없다 (채점 불가).

    result: hit / miss / flat
      flat은 적중률 분모에서 뺀다 — 사이트도 "±1% 이내는 보합"으로 뺀다.
      보합을 실패로 세면 변동성 낮은 종목이 전부 실패로 잡혀 표가 망가진다.

    mfe/mae: 창 안에서 유리한/불리한 최대 이동을 ATR 단위로.
    적중률만 보면 "맞았지만 그 전에 손절 두 배를 맞았다"를 못 잡는다.
    실제로 그 셋업으로 돈을 벌 수 있었는지는 mfe/mae 비율이 말한다.
    """
    end = i + horizon
    if end >= len(closes):
        return None

    ref = closes[i]
    if not ref or ref <= 0:
        return None

    sign = 1 if direction == "up" else -1
    move = (closes[end] - ref) / ref * sign

    if move > flat_band:
        result = "hit"
    elif move < -flat_band:
        result = "miss"
    else:
        result = "flat"

    # 경로 기반 최대 유·불리 이동
    win_h = highs[i + 1:end + 1]
    win_l = lows[i + 1:end + 1]
    if direction == "up":
        fav = (max(win_h) - ref) / ref
        adv = (ref - min(win_l)) / ref
    else:
        fav = (ref - min(win_l)) / ref
        adv = (max(win_h) - ref) / ref

    out = {
        "move_pct": round(move, 5),
        "result"  : result,
        "fav_pct" : round(max(0.0, fav), 5),
        "adv_pct" : round(max(0.0, adv), 5),
    }
    if atr_pct and atr_pct > 0:
        out["mfe_atr"] = round(max(0.0, fav) / atr_pct, 3)
        out["mae_atr"] = round(max(0.0, adv) / atr_pct, 3)
    return out


def _baseline_at(closes, i, horizon, flat_band=FLAT_BAND):
    """무조건부 전방 이동 — 기준선 표본 한 건."""
    end = i + horizon
    if end >= len(closes):
        return None
    ref = closes[i]
    if not ref or ref <= 0:
        return None
    move = (closes[end] - ref) / ref
    if move > flat_band:
        return "up"
    if move < -flat_band:
        return "down"
    return "flat"


# ================================
# 🏗 소급 채점 (backfill)
# ================================

def backfill(symbol, df, timeframe="1d", horizons=DEFAULT_HORIZONS,
             lookback=RANGE_LOOKBACK, regime_lookup=None, max_bars=None):
    """
    과거 봉 전체를 훑어 사건을 만들고 그 자리에서 채점한다.

    4주를 기다리는 대신 오늘 표본을 확보하는 경로. 200봉짜리 일봉 하나면
    종목당 수십~수백 건이 나오고, 100종목이면 수천 건이 된다.

    regime_lookup(date) → 환경 라벨 (없으면 None). 과거 환경을 모르면
    환경별 집계는 못 하지만 전체 집계는 그대로 유효하다.

    ⚠️ 소급 채점의 한계 (숨기지 않고 기록한다)
      · 지금 유니버스에 있는 종목만 본다 = 상장폐지·거래정지 종목이 빠진
        생존 편향. 실제 성적은 여기서 나온 것보다 나쁠 가능성이 높다.
      · 그래서 각 사건에 source="backfill"을 박아두고, 리포트에서
        live 표본과 따로 볼 수 있게 한다.
    """
    highs, lows, closes, ts = _ohlc(df)
    if not closes or len(closes) < lookback + max(horizons) + 5:
        return [], {}

    atrs = _atr_series(highs, lows, closes)
    coin = symbol.replace("/USDT", "")
    max_h = max(horizons)

    start = lookback
    if max_bars:
        start = max(start, len(closes) - max_bars)

    events = []
    baseline = {h: {"up": 0, "down": 0, "flat": 0} for h in horizons}

    for i in range(start, len(closes) - 1):
        date = _bar_date(ts, i)

        # 기준선은 사건 유무와 무관하게 모든 봉에서 모은다
        for h in horizons:
            b = _baseline_at(closes, i, h)
            if b:
                baseline[h][b] += 1

        detected = detect_at(highs, lows, closes, i, lookback)
        if not detected:
            continue

        atr = atrs[i] if i < len(atrs) else None
        atr_pct = (atr / closes[i]) if (atr and closes[i]) else None
        regime = regime_lookup(date) if (regime_lookup and date) else None

        for ev in detected:
            scores = {}
            for h in horizons:
                s = score_event(highs, lows, closes, i, ev["direction"], h, atr_pct)
                if s:
                    scores[str(h)] = s
            if not scores:
                continue

            events.append({
                "id"       : f"{symbol}|{timeframe}|{date}|{ev['type']}",
                "symbol"   : symbol,
                "coin"     : coin,
                "timeframe": timeframe,
                "type"     : ev["type"],
                "direction": ev["direction"],
                "date"     : date,
                "ref_price": closes[i],
                "atr_pct"  : round(atr_pct, 5) if atr_pct else None,
                "regime"   : regime,
                "source"   : "backfill",
                "scores"   : scores,
                "created_at": time.time(),
            })

    # 마지막 max_h봉은 채점할 미래가 없어 기준선에서도 자동으로 빠진다
    return events, baseline


def backfill_universe(symbols, get_ohlcv_fn, timeframe="1d",
                      horizons=DEFAULT_HORIZONS, bars=400,
                      regime_lookup=None, path=LEDGER_FILE):
    """전 종목 소급 채점 후 원장에 병합."""
    all_events = []
    # 종목·봉별로 따로 담는다 — 같은 백필을 다시 돌려도 기준선이 두 배가
    # 되지 않고 그 출처만 갈아끼워진다.
    baseline_keys = {}

    for symbol in symbols:
        try:
            df = get_ohlcv_fn(symbol, timeframe, limit=bars)
        except Exception as e:
            console.say(f"[원장] 조회 실패 ({symbol}): {e}")
            continue
        if df is None or len(df) == 0:
            continue

        evs, base = backfill(symbol, df, timeframe, horizons,
                             regime_lookup=regime_lookup)
        all_events.extend(evs)
        if base:
            baseline_keys[f"{symbol}|{timeframe}"] = base

    merge(all_events, path=path, baseline_keys=baseline_keys)
    console.say(f"✅ [원장] 소급 채점 {len(all_events)}건 적재 ({len(symbols)}종목)")
    return all_events


# ================================
# 📡 실시간 적재 · 채점
# ================================

def capture(symbols, get_ohlcv_fn, timeframe="1d", regime=None,
            horizons=DEFAULT_HORIZONS, path=LEDGER_FILE):
    """
    오늘 마감봉에서 사건을 잡아 미채점 상태로 넣는다 (일봉 마감 후 1회).

    regime: regime_gate.scan_universe()의 label. 나중에 환경별로 가르기 위해.
    """
    new_events = []
    for symbol in symbols:
        try:
            df = get_ohlcv_fn(symbol, timeframe, limit=RANGE_LOOKBACK + 40)
        except Exception as e:
            console.say(f"[원장] 조회 실패 ({symbol}): {e}")
            continue
        if df is None or len(df) == 0:
            continue

        highs, lows, closes, ts = _ohlc(df)
        if not closes or len(closes) < RANGE_LOOKBACK + 2:
            continue

        i = len(closes) - 1
        detected = detect_at(highs, lows, closes, i)
        if not detected:
            continue

        atrs = _atr_series(highs, lows, closes)
        atr = atrs[i] if i < len(atrs) else None
        atr_pct = (atr / closes[i]) if (atr and closes[i]) else None
        date = _bar_date(ts, i)

        for ev in detected:
            new_events.append({
                "id"       : f"{symbol}|{timeframe}|{date}|{ev['type']}",
                "symbol"   : symbol,
                "coin"     : symbol.replace("/USDT", ""),
                "timeframe": timeframe,
                "type"     : ev["type"],
                "direction": ev["direction"],
                "date"     : date,
                "ref_price": closes[i],
                "atr_pct"  : round(atr_pct, 5) if atr_pct else None,
                "regime"   : regime,
                "source"   : "live",
                "scores"   : {},
                "created_at": time.time(),
            })

    if new_events:
        merge(new_events, None, path=path)
        console.say(f"✅ [원장] 신규 사건 {len(new_events)}건")
    return new_events


def score_pending(get_ohlcv_fn, timeframe="1d", horizons=DEFAULT_HORIZONS,
                  path=LEDGER_FILE):
    """
    미채점 사건 중 horizon이 지난 것을 채점한다 (하루 1회).

    사건의 date에 해당하는 봉을 다시 찾아 그 자리를 기준으로 채점하므로,
    봇이 며칠 꺼져 있었어도 켜지면 밀린 채점이 한 번에 따라잡힌다.
    """
    ledger = _load(path)
    events = ledger["events"]

    pending_by_symbol = {}
    for ev in events:
        if ev.get("source") != "live":
            continue
        missing = [h for h in horizons if str(h) not in ev.get("scores", {})]
        if missing:
            pending_by_symbol.setdefault(ev["symbol"], []).append((ev, missing))

    if not pending_by_symbol:
        return 0

    scored = 0
    for symbol, items in pending_by_symbol.items():
        try:
            df = get_ohlcv_fn(symbol, timeframe, limit=400)
        except Exception as e:
            console.say(f"[원장] 채점 조회 실패 ({symbol}): {e}")
            continue
        if df is None or len(df) == 0:
            continue

        highs, lows, closes, ts = _ohlc(df)
        dates = [_bar_date(ts, k) for k in range(len(closes))]

        for ev, missing in items:
            try:
                i = dates.index(ev["date"])
            except ValueError:
                continue   # 그 봉이 조회 범위 밖 — 다음 기회에
            for h in missing:
                s = score_event(highs, lows, closes, i, ev["direction"], h,
                                ev.get("atr_pct"))
                if s:
                    ev["scores"][str(h)] = s
                    scored += 1

    if scored:
        _save(ledger, path)
        console.say(f"✅ [원장] {scored}건 채점 완료")
    return scored


# ================================
# 💾 저장
# ================================

def _blank_baseline():
    return {"up": 0, "down": 0, "flat": 0}


def _add_counts(dst, src):
    for h, counts in (src or {}).items():
        slot = dst.setdefault(str(h), _blank_baseline())
        for k, v in (counts or {}).items():
            slot[k] = slot.get(k, 0) + v
    return dst


def _effective_baseline(data):
    """실제로 쓰는 기준선 = 멱등 저장분(by_key) + 예전 가산분(legacy)."""
    out = {}
    _add_counts(out, data.get("baseline_legacy"))
    for counts in (data.get("baseline_by_key") or {}).values():
        _add_counts(out, counts)
    return out


def _load(path=LEDGER_FILE):
    data = jsonstore.load(path, default={})
    if not isinstance(data, dict):
        data = {}
    data.setdefault("events", [])
    data.setdefault("baseline_by_key", {})
    if "baseline_legacy" not in data:
        # 예전 파일 이관 — 그때의 baseline은 가산분으로 본다.
        data["baseline_legacy"] = data.get("baseline") or {}
    # baseline은 저장된 값이 아니라 위 둘에서 매번 다시 만든 값이다.
    # (그래서 다시 읽어도 두 번 더해지지 않는다)
    data["baseline"] = _effective_baseline(data)
    return data


def _save(data, path=LEDGER_FILE):
    return jsonstore.save(path, data)


def merge(new_events, baseline=None, path=LEDGER_FILE, baseline_keys=None):
    """
    id 기준 중복 제거 병합. 같은 종목·같은 날·같은 타입은 한 건이다.

    baseline_keys: {"BTC/USDT|1d": {horizon: counts}} — **덮어쓴다**.

    사건은 id로 중복 제거되는데 기준선만 그냥 더해지면, 소급 채점을 두 번
    돌렸을 때 기준선 표본이 두 배가 된다. 초과 적중률(= 적중률 − 기준선)이
    이 저장소의 핵심 숫자라 기준선이 틀어지면 결론 전체가 흔들린다.
    그래서 출처(종목|봉)별로 저장하고, 다시 돌리면 그 출처만 갈아끼운다.

    baseline(키 없는 형태)은 예전 호출부 호환용이며 종전처럼 가산된다.
    """
    data = _load(path)
    seen = {e["id"]: e for e in data["events"]}
    for ev in new_events:
        if ev["id"] in seen:
            # 이미 있으면 점수만 채운다 (사건 자체는 덮어쓰지 않는다)
            seen[ev["id"]]["scores"].update(ev.get("scores", {}))
        else:
            seen[ev["id"]] = ev
    data["events"] = list(seen.values())

    if baseline_keys:
        for key, counts in baseline_keys.items():
            data["baseline_by_key"][key] = {
                str(h): dict(c) for h, c in (counts or {}).items()
            }
    if baseline:
        _add_counts(data["baseline_legacy"], baseline)

    data["baseline"] = _effective_baseline(data)
    _save(data, path)
    return data


def reset(path=LEDGER_FILE):
    _save({"events": [], "baseline": {}, "baseline_by_key": {},
           "baseline_legacy": {}}, path)
    console.say("🗑️ 셋업 원장 초기화")


# ================================
# 📊 집계
# ================================

def baseline_rate(baseline, horizon, direction):
    """
    그 horizon에서 아무 날이나 잡았을 때 그 방향으로 갈 확률.

    보합을 분모에서 빼는 방식은 사건 채점과 동일하게 맞춘다 —
    분모가 다르면 초과 적중률이 사과와 오렌지 비교가 된다.
    """
    slot = (baseline or {}).get(str(horizon))
    if not slot:
        return None
    decided = slot.get("up", 0) + slot.get("down", 0)
    if decided < 100:      # 기준선이 흔들리면 초과값이 더 못 믿을 것이 된다
        return None
    return slot.get(direction, 0) / decided


def stats(horizon=3, path=LEDGER_FILE, source=None, regime=None,
          min_samples=20):
    """
    셋업별 적중률 표 — 사이트의 채점표에 기준선·초과값·MFE/MAE를 더한 것.

    source: 'live' | 'backfill' | None(전부)
    regime: 환경 라벨로 거르기 (환경별 성적 비교용)
    """
    data = _load(path)
    key = str(horizon)

    rows = {}
    for ev in data["events"]:
        if source and ev.get("source") != source:
            continue
        if regime and ev.get("regime") != regime:
            continue
        s = ev.get("scores", {}).get(key)
        if not s:
            continue
        r = rows.setdefault(ev["type"], {
            "type": ev["type"], "direction": ev["direction"],
            "hit": 0, "miss": 0, "flat": 0,
            "mfe": [], "mae": [], "moves": [],
        })
        r[s["result"]] += 1
        r["moves"].append(s["move_pct"])
        if "mfe_atr" in s:
            r["mfe"].append(s["mfe_atr"])
            r["mae"].append(s["mae_atr"])

    out = []
    for t, r in rows.items():
        decided = r["hit"] + r["miss"]
        total = decided + r["flat"]
        # 게이트는 **판정 건수**로 건다. 보합까지 세면 "표본 45건"이라 써 놓고
        # 실제로는 판정 5건으로 낸 100%를 싣게 된다 — 보합은 분자에도
        # 분모에도 안 들어가므로 적중률을 뒷받침하지 못한다.
        if decided < min_samples:
            continue
        hit_rate = r["hit"] / decided if decided else None
        base = baseline_rate(data["baseline"], horizon, r["direction"])
        avg_mfe = sum(r["mfe"]) / len(r["mfe"]) if r["mfe"] else None
        avg_mae = sum(r["mae"]) / len(r["mae"]) if r["mae"] else None

        out.append({
            "type"      : t,
            "label"     : EVENT_LABELS.get(t, t),
            "direction" : r["direction"],
            "samples"   : total,
            "decided"   : decided,
            "flat"      : r["flat"],
            "hit_rate"  : hit_rate,
            "baseline"  : base,
            # 초과 적중률 — 이 값이 0 근처면 셋업이 아니라 시장이 한 일이다
            "edge"      : (hit_rate - base) if (hit_rate is not None and base is not None) else None,
            "avg_move"  : sum(r["moves"]) / len(r["moves"]) if r["moves"] else None,
            "avg_mfe_atr": avg_mfe,
            "avg_mae_atr": avg_mae,
            # 유리/불리 이동 비율 — 1보다 커야 실제로 먹을 수 있는 셋업
            "mfe_mae"   : (avg_mfe / avg_mae) if (avg_mfe and avg_mae) else None,
        })

    out.sort(key=lambda r: (r["edge"] if r["edge"] is not None else -9), reverse=True)
    return {"horizon": horizon, "rows": out, "baseline": data["baseline"].get(key)}


def summary(path=LEDGER_FILE):
    """원장 전체 현황."""
    data = _load(path)
    evs = data["events"]
    live = [e for e in evs if e.get("source") == "live"]
    back = [e for e in evs if e.get("source") == "backfill"]
    scored = [e for e in evs if e.get("scores")]
    dates = sorted({e["date"] for e in evs if e.get("date")})
    return {
        "total": len(evs), "live": len(live), "backfill": len(back),
        "scored": len(scored),
        "first_date": dates[0] if dates else None,
        "last_date": dates[-1] if dates else None,
        "symbols": len({e["symbol"] for e in evs}),
    }


# ================================
# 📋 리포트
# ================================

def get_report(horizon=3, path=LEDGER_FILE, source=None, regime=None):
    """텔레그램 리포트 — 사이트 채점표와 나란히 놓고 볼 수 있는 형식."""
    st = stats(horizon, path, source, regime)
    s = summary(path)

    if not st["rows"]:
        return (
            "📭 <b>셋업 채점표</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"아직 집계할 표본이 없어요 (총 {s['total']}건).\n\n"
            "소급 채점을 먼저 돌리세요:\n"
            "<code>/원장백필</code> — 과거 봉으로 수천 건을 즉시 확보합니다."
        )

    scope = {"live": "실시간만", "backfill": "소급만"}.get(source, "전체")
    lines = [
        f"🎯 <b>셋업 채점표 ({horizon}봉 뒤)</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"표본 {s['total']}건 · {s['symbols']}종목 · {scope}",
    ]
    if regime:
        lines.append(f"환경 필터: {regime}")
    if s["first_date"]:
        lines.append(f"기간 {s['first_date']} ~ {s['last_date']}")

    base = st.get("baseline")
    if base:
        dec = base.get("up", 0) + base.get("down", 0)
        if dec:
            lines += [
                "",
                f"<b>기준선</b> — 아무 날이나 잡았을 때",
                f"  상승 {base['up']/dec*100:.1f}% · 하락 {base['down']/dec*100:.1f}%  "
                f"(표본 {dec:,}건)",
                "  ※ 셋업 적중률은 이 값과 비교해야 의미가 있습니다.",
            ]

    lines += ["", "<b>셋업별</b>  (초과 = 적중률 − 기준선)"]
    for r in st["rows"]:
        if r["hit_rate"] is None:
            continue
        edge = r["edge"]
        if edge is None:
            mark, edge_str = "▪️", "기준선 없음"
        elif edge >= 0.08:
            mark, edge_str = "🟢", f"초과 +{edge*100:.1f}%p"
        elif edge <= -0.08:
            mark, edge_str = "🔴", f"초과 {edge*100:.1f}%p"
        else:
            mark, edge_str = "⚪", f"초과 {edge*100:+.1f}%p — 시장 방향일 뿐"

        dir_kr = "상승" if r["direction"] == "up" else "하락"
        line = (
            f"{mark} <b>{r['label']}</b> ({dir_kr})\n"
            f"    적중 {r['hit_rate']*100:.1f}%  ·  {edge_str}\n"
            f"    표본 {r['samples']}건 (판정 {r['decided']} / 보합 {r['flat']})"
        )
        if r["mfe_mae"]:
            line += (
                f"\n    최대유리/최대불리 {r['mfe_mae']:.2f}배 "
                f"(MFE {r['avg_mfe_atr']:.2f} / MAE {r['avg_mae_atr']:.2f} ATR)"
            )
        lines.append(line)

    lines += [
        "━━━━━━━━━━━━━━━━━━━━",
        "🟢 = 시장 방향을 빼고도 남는 우위가 있는 셋업",
        "⚪ = 적중률은 높아도 시장이 그 방향이었을 뿐",
        "",
        "※ 소급 표본은 지금 상장된 종목만 봅니다 (생존 편향).",
        "  실제 성적은 이보다 나쁠 수 있습니다.",
        f"{datetime.now().strftime('%m/%d %H:%M')} KST",
    ]
    return "\n".join(lines)


def compare_regimes(horizon=3, path=LEDGER_FILE,
                    labels=("상승국면", "상승우위", "관망", "하락우위", "하락국면")):
    """
    환경별 셋업 성적 비교 — regime_gate를 켤 가치가 있는지 채점한다.

    "디스카운트 롱이 관망일 때도 되는가"에 표본으로 답하는 표.
    환경 게이트가 실제로 성적을 올리지 못하면 그냥 표본만 줄이는 장치다.
    """
    out = {}
    for lb in labels:
        st = stats(horizon, path, regime=lb, min_samples=10)
        if st["rows"]:
            out[lb] = {r["type"]: r for r in st["rows"]}
    return out
