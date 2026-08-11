"""feature_bench.py — 비가격 정보가 진입 시점을 알려주나

    cd /path/to/jaybot
    python feature_bench.py --peek       # 받은 값이 어떻게 생겼나 (먼저 이것)
    python feature_bench.py              # 시험대 실행
    python feature_bench.py 30 4y        # 30종 · 4년

무엇이 달라졌나
──────────────
지금까지 잰 45가지는 **전부 가격에서 나온 값**이었다. 이동평균, 고점,
RSI, 되돌림 — 이름만 다르지 재료가 하나다. 하나도 통과하지 못했다.

CoinGlass 로 다른 재료가 생겼다. 30종 × 약 5.8년:

    미결제약정      돈이 얼마나 걸려 있나 (포지션 총량)
    미결제약정 합산  같은 것, 전거래소
    펀딩비          누가 누구에게 돈을 내고 있나 (쏠림의 값)
    롱숏 계정비      계정 수 기준 롱:숏 — 개미 쪽에 가깝다
    상위 트레이더    상위 계정 기준 롱:숏 — 큰손 쪽에 가깝다
    테이커 매수/매도  시장가로 때린 쪽 (급한 사람)

  이건 가격이 아니다. **포지션의 상태**다.

가장 그럴듯한 것
───────────────
개미(롱숏 계정비)와 큰손(상위 트레이더)이 **반대로** 서 있을 때.
개미가 롱, 큰손이 숏이면 숏. 그 반대면 롱. 가격만 봐서는 절대
안 보이는 조건이다.

틀은 그대로다
────────────
signal_bench·funding_bench 와 **완전히 같다.** 손절 1.5×ATR,
목표 1R/2R/3R, 다음 봉 시가 체결, 수수료 0.06% + 슬리피지 0.05%,
한 코인 동시 1개, 무작위 진입과 비교, 전후반 분리.

    다른 건 언제 들어가느냐뿐이다.

문턱은 상수가 아니라 **그 코인의 지난 90일 분위수**다. 코인마다
수준이 다르고 시기마다 다르다. "롱숏비 2.0 넘으면" 같은 상수를
쓰면 그 상수를 고른 것 자체가 과최적화다.

미래 차단
────────
그날까지 확정된 값만 쓴다. 분위수 창도 그날까지다. 진입은 언제나
다음 봉 시가다.

몇 개를 시험하나 — 이게 중요하다
───────────────────────────────
후보가 N개면 그중 하나가 우연히 좋아 보일 확률도 N배다. 지금까지
45개를 쟀고 여기서 몇 개를 더 잰다. 통과한 것이 나오면 그 수를
**반드시 같이 적어야 한다.** 화면 끝에 찍는다.
"""

import json
import math
import os
import sys
import time

pd = None
bt = None
sb = None

CACHE = "coinglass_hist.jsonl"
MAX_DAYS = 1460
SAMPLES_PER_COIN = 60
MIN_PER_PERIOD = 15
PCT_WINDOW = 90            # 분위수 창 (일)
HI_Q, LO_Q = 0.90, 0.10
WARMUP = 150               # 지표가 익을 때까지는 진입하지 않는다
MIN_BARS = 250             # 이보다 짧으면 진입할 날이 얼마 안 남는다


def _load():
    global pd, bt, sb
    if bt is not None:
        return True
    for cand in (os.getcwd(), os.path.dirname(os.path.abspath(__file__))):
        if os.path.exists(os.path.join(cand, "backtest.py")) and cand not in sys.path:
            sys.path.insert(0, cand)
    try:
        import console
        console.enable_utf8()
    except Exception:
        pass
    try:
        import pandas
        import backtest
        import signal_bench
    except ImportError as e:
        print(f"  필요한 파일을 못 불렀습니다: {e}")
        print("  이 스크립트는 backtest.py · signal_bench.py 와 같은 폴더에 두십시오.")
        return False
    if not signal_bench._load():
        return False
    pd, bt, sb = pandas, backtest, signal_bench
    return True


# ── 값 뽑기 ──────────────────────────────────────────────────
#
# 응답 레코드를 통째로 저장해 뒀다. 항목마다 쓸 칼럼이 다르고,
# 이름도 문서 판마다 갈린다. 후보를 순서대로 찾고, 무엇을 썼는지
# 화면에 밝힌다 — 조용히 엉뚱한 칼럼을 쓰면 결과가 통째로 헛것이 된다.

TIME_KEYS = ("time", "timestamp", "t", "ts", "createTime")

# kind -> (표시 이름, 값 후보들, 두 값의 비로 만들 것)
EXTRACT = {
    "oi": ("미결제약정",
           ("close", "c", "openInterest", "open_interest", "value"), None),
    "oi_agg": ("미결제약정(합산)",
               ("close", "c", "openInterest", "open_interest", "value"), None),
    "funding": ("펀딩비",
                ("close", "c", "fundingRate", "funding_rate", "rate", "value"), None),
    # 실제로 온 이름 (--peek 으로 확인):
    #   global_account_long_short_ratio · global_account_long_percent
    #   top_account_long_short_ratio    · top_account_long_percent
    # 알파벳 순으로 long_percent 가 먼저라, 이름을 안 박아 두면
    # 마지막 수단이 그걸 집는다. 비율이 맞다.
    "ls_ratio": ("롱숏 계정비",
                 ("global_account_long_short_ratio", "longShortRatio",
                  "long_short_ratio", "ratio", "close", "value"),
                 ("longAccount", "shortAccount")),
    "top_ls": ("상위 트레이더 롱숏",
               ("top_account_long_short_ratio", "top_position_long_short_ratio",
                "longShortRatio", "long_short_ratio", "ratio", "close", "value"),
               ("longAccount", "shortAccount")),
    "taker": ("테이커 매수비",
              (), ("buy", "sell")),
    "liq": ("청산", (), ("longLiquidationUsd", "shortLiquidationUsd")),
}

# 비를 만들 때 쓸 이름 후보 (문서 판마다 갈린다)
PAIR_ALIASES = {
    "buy": ("buy", "taker_buy_volume_usd", "takerBuyVolumeUsd",
            "buyVol", "aggregated_buy_volume_usd", "long"),
    "sell": ("sell", "taker_sell_volume_usd", "takerSellVolumeUsd",
             "sellVol", "aggregated_sell_volume_usd", "short"),
    "longAccount": ("longAccount", "long_account", "longAccountRatio",
                    "global_account_long_percent", "top_account_long_percent",
                    "long"),
    "shortAccount": ("shortAccount", "short_account", "shortAccountRatio",
                     "global_account_short_percent", "top_account_short_percent",
                     "short"),
    "longLiquidationUsd": ("longLiquidationUsd", "long_liquidation_usd", "long"),
    "shortLiquidationUsd": ("shortLiquidationUsd", "short_liquidation_usd", "short"),
}


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


def _find(raw, names):
    for n in names:
        for alias in PAIR_ALIASES.get(n, (n,)):
            if alias in raw:
                v = _num(raw[alias])
                if v is not None:
                    return v, alias
    return None, None


def value_of(kind, raw):
    """(값, 어느 칼럼에서 왔나). 못 뽑으면 (None, None)."""
    if not isinstance(raw, dict):
        return None, None
    name, singles, pair = EXTRACT.get(kind, (kind, ("close", "value"), None))

    v, col = _find(raw, singles)
    if v is not None:
        return v, col

    if pair:
        a, ca = _find(raw, (pair[0],))
        b, cb = _find(raw, (pair[1],))
        if a is not None and b is not None:
            if kind == "taker":
                tot = a + b
                return (a / tot if tot else None), f"{ca}/({ca}+{cb})"
            if kind == "liq":
                # 청산이 0 인 날은 '값이 없는 날'이 아니라 **양쪽 다
                # 안 터진 날**이다. 정보다. 버리면 조용한 날이 통째로
                # 사라지고 시끄러운 날만 남는다.
                tot = a + b
                return ((a - b) / tot if tot else 0.0), f"({ca}-{cb})/합"
            return (a / b if b else None), f"{ca}/{cb}"

    # 마지막 수단: 시각이 아닌 첫 숫자. 무엇을 썼는지 화면에 뜬다.
    for k, v in raw.items():
        if k in TIME_KEYS:
            continue
        n = _num(v)
        if n is not None:
            return n, f"{k}?"
    return None, None


# ── 캐시 ─────────────────────────────────────────────────────

def to_ms(ts):
    """초·밀리초·마이크로초가 섞여 온다. 밀리초로 맞춘다."""
    try:
        t = int(ts)
    except (TypeError, ValueError):
        return None
    if t <= 0:
        return None
    if t < 100_000_000_000:
        return t * 1000
    if t < 100_000_000_000_000:
        return t
    if t < 100_000_000_000_000_000:
        return t // 1000
    return t // 1_000_000


def day_of(ms):
    """밀리초 → 'YYYY-MM-DD'. 못 바꾸면 None.

    pandas 없이 돈다 — 캐시를 읽는 데 backtest.py 가 필요하면
    자료 점검조차 못 한다.
    """
    import datetime
    try:
        return datetime.datetime.fromtimestamp(
            ms / 1000, datetime.timezone.utc).strftime("%Y-%m-%d")
    except (OSError, OverflowError, ValueError, TypeError):
        return None


# 못 읽은 줄의 표본. 개수만 세면 '왜' 를 알 수 없다 —
# BTC 자료가 통째로 빠진 걸 개수만 보고는 못 찾았다.
SKIP_SAMPLE = {}


def _skip(skipped, kind, coin, why, rec):
    """못 읽은 줄을 세고, 종목별로 갈라 두고, 하나는 통째로 남긴다.

    개수만 세면 '몇 개'는 알아도 '어디'와 '왜'를 모른다. BTC 자료가
    통째로 빠진 것을 개수만 보고는 못 찾았다.
    """
    skipped[kind] = skipped.get(kind, 0) + 1
    box = SKIP_SAMPLE.setdefault(kind, {"why": why, "coins": {}, "one": rec})
    box["coins"][coin] = box["coins"].get(coin, 0) + 1


def load_cache(path=CACHE):
    """{kind: {coin: {날짜: 값}}}, {kind: 쓴 칼럼 이름}

    하루에 여러 줄이 있으면 **그날 마지막 것**을 쓴다. 미결제약정도
    롱숏비도 '수준'이라 그날 끝값이 맞다. 평균을 내면 그날 종가와
    시점이 어긋난다.
    """
    out, cols, skipped = {}, {}, {}
    SKIP_SAMPLE.clear()
    if not os.path.exists(path):
        return out, cols, skipped
    tmp = {}                      # kind -> coin -> date -> (ts, 값)
    with open(path, encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                kind, coin = r["kind"], r["coin"]
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
            ms = to_ms(r.get("ts"))
            date = day_of(ms) if ms is not None else None
            if date is None:
                _skip(skipped, kind, coin, "시각이 시각이 아님", r)
                continue
            v, col = value_of(kind, r.get("raw"))
            if v is None:
                _skip(skipped, kind, coin, "값을 못 뽑음", r)
                continue
            if col and kind not in cols:
                cols[kind] = col
            day = tmp.setdefault(kind, {}).setdefault(coin, {})
            if date not in day or ms > day[date][0]:
                day[date] = (ms, v)
    for kind, coins in tmp.items():
        out[kind] = {c: {d: v for d, (_, v) in days.items()} for c, days in coins.items()}

    # 못 읽은 줄이 **아직 구멍인지, 이미 메워졌는지** 갈라 둔다.
    #
    # 캐시는 덧붙이기만 한다. 옛날 판이 남긴 껍데기 줄은 지워지지
    # 않고, 그 위에 제대로 된 줄이 얹힌다. 그러면 못 읽은 개수는
    # 그대로인데 자료는 멀쩡하다. 그걸 구분해 주지 않으면 화면이
    # "아직 안 고쳐졌다"고 거짓말한다.
    for kind, box in SKIP_SAMPLE.items():
        have = out.get(kind, {})
        box["gone"] = sorted(c for c in box["coins"] if c in have)
        box["still"] = sorted(c for c in box["coins"] if c not in have)
    return out, cols, skipped


def cmd_peek(path=CACHE):
    """받은 값이 실제로 어떻게 생겼는지 본다. 칼럼 이름을 눈으로 확인할 것."""
    if not os.path.exists(path):
        print(f"  {path} 이 없습니다. coinglass_probe.py --fetch 로 먼저 받으십시오.")
        return 1
    seen = {}
    with open(path, encoding="utf-8") as fp:
        for line in fp:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            k = r.get("kind")
            if k and k not in seen and isinstance(r.get("raw"), dict):
                seen[k] = r["raw"]
            if len(seen) >= len(EXTRACT):
                break
    print("=" * 78)
    print("  받은 값이 어떻게 생겼나 — 어느 칼럼을 쓰는지 눈으로 확인하십시오")
    print("=" * 78)
    for kind in sorted(seen):
        name = EXTRACT.get(kind, (kind,))[0]
        raw = seen[kind]
        v, col = value_of(kind, raw)
        print(f"\n  [{kind}] {name}")
        print(f"    칼럼: {', '.join(list(raw)[:12])}")
        print(f"    쓰는 값: {col}  →  {v}")
        if col and col.endswith("?"):
            print("    ⚠️ 아는 이름이 없어 첫 숫자를 집었습니다. 맞는지 확인하십시오.")
    print("\n" + "=" * 78)
    print("  이상하면 알려 주십시오. EXTRACT 의 칼럼 후보를 고치면 됩니다.")

    # 못 읽는 줄이 있으면 그게 어느 종목인지, 어떻게 생겼는지 보여준다.
    _, _, skipped = load_cache(path)
    if SKIP_SAMPLE:
        print("\n" + "=" * 78)
        print("  못 읽은 줄 — 어느 종목이 빠지고 있나")
        print("=" * 78)
        for kind, box in sorted(SKIP_SAMPLE.items()):
            still, gone = box.get("still", []), box.get("gone", [])
            head = "⚠️ 아직 구멍" if still else "· 이미 메워짐"
            print(f"\n  [{kind}]  {skipped.get(kind, 0):,}줄 · {box['why']}  {head}")
            if gone:
                print(f"    이미 메워짐: {', '.join(gone[:8])}"
                      "  — 옛날 껍데기 줄입니다. 그냥 두십시오.")
            if still:
                print("    ⚠️ 아직 못 쓰는 종목: "
                      + ", ".join(f"{c} {box['coins'][c]:,}" for c in still[:6]))
                one = box["one"]
                raw = one.get("raw")
                print(f"    표본: ts={one.get('ts')!r}")
                if isinstance(raw, dict):
                    print(f"          raw 칼럼={list(raw)[:8]}")
                    print(f"          raw 값={[raw[k] for k in list(raw)[:8]]}")
                else:
                    print(f"          raw={raw!r}")
        if not any(b.get("still") for b in SKIP_SAMPLE.values()):
            print("\n  ✅ 구멍 없음 — 못 읽은 줄은 전부 다른 줄로 메워졌습니다.")
    return 0


# ── 지표 만들기 ──────────────────────────────────────────────

def attach(d, cache, coin):
    """비가격 값들을 날짜로 붙이고, 그 코인의 분위수 문턱을 만든다.

    전부 **그날까지만** 본다 (rolling). 미래는 안 쓴다.
    """
    d = d.copy()
    dates = [ts.strftime("%Y-%m-%d") for ts in d["timestamp"]]

    nan = float("nan")

    def col(kind):
        m = cache.get(kind, {}).get(coin)
        if not m:
            return None
        s = d["close"].astype("float64").copy()   # index·dtype 을 빌린다
        s.iloc[:] = [float(m[x]) if x in m else nan for x in dates]
        return s.ffill(limit=2)          # 하루 빠진 건 직전 값으로

    d["oi"] = col("oi")
    if d["oi"] is None or d["oi"].isna().all():
        d["oi"] = col("oi_agg")
    d["fund"] = col("funding")
    d["lsr"] = col("ls_ratio")
    d["tls"] = col("top_ls")
    d["tkr"] = col("taker")

    # 미결제약정은 '수준'이라 코인끼리·시기끼리 비교가 안 된다.
    # 변화율로 바꿔야 비교가 된다.
    d["oi_chg"] = d["oi"].pct_change() if d["oi"] is not None else None
    d["oi_chg3"] = d["oi"].pct_change(3) if d["oi"] is not None else None
    d["px_chg"] = d["close"].pct_change()

    for name in ("oi_chg", "oi_chg3", "fund", "lsr", "tls", "tkr"):
        s = d.get(name)
        if s is None:
            d[name + "_hi"] = float("nan")
            d[name + "_lo"] = float("nan")
            continue
        r = s.rolling(PCT_WINDOW, min_periods=30)
        d[name + "_hi"] = r.quantile(HI_Q)
        d[name + "_lo"] = r.quantile(LO_Q)
    return d


def _v(d, i, c):
    """i 번째 값. 없거나 NaN 이면 None."""
    if c not in d.columns:
        return None
    x = d[c].iloc[i]
    return None if x != x else x


def _hi(d, i, c):
    a, b = _v(d, i, c), _v(d, i, c + "_hi")
    return a is not None and b is not None and a >= b


def _lo(d, i, c):
    a, b = _v(d, i, c), _v(d, i, c + "_lo")
    return a is not None and b is not None and a <= b


def _up(d, i):
    x = _v(d, i, "px_chg")
    return x is not None and x > 0


def _down(d, i):
    x = _v(d, i, "px_chg")
    return x is not None and x < 0


# ── 후보 ─────────────────────────────────────────────────────
#
# (이름, 롱?, 조건)

CANDIDATES = [
    # ① 미결제약정 — 새 돈이 들어왔나, 빠졌나
    ("OI급증+가격상승 → 롱(추종)", True,
     lambda d, i: _hi(d, i, "oi_chg") and _up(d, i)),
    ("OI급증+가격상승 → 숏(역추종)", False,
     lambda d, i: _hi(d, i, "oi_chg") and _up(d, i)),
    ("OI급증+가격하락 → 숏(신규 숏 유입)", False,
     lambda d, i: _hi(d, i, "oi_chg") and _down(d, i)),
    ("OI급감+가격상승 → 롱(숏커버 종료)", True,
     lambda d, i: _lo(d, i, "oi_chg") and _up(d, i)),
    ("OI 3일 급증 → 숏", False,
     lambda d, i: _hi(d, i, "oi_chg3")),

    # ② 쏠림 — 펀딩
    ("펀딩 극단 양수 → 숏", False, lambda d, i: _hi(d, i, "fund")),
    ("펀딩 극단 음수 → 롱", True, lambda d, i: _lo(d, i, "fund")),

    # ③ 개미 — 롱숏 계정비
    ("개미 롱 쏠림 → 숏", False, lambda d, i: _hi(d, i, "lsr")),
    ("개미 숏 쏠림 → 롱", True, lambda d, i: _lo(d, i, "lsr")),

    # ④ 큰손 — 상위 트레이더
    ("큰손 롱 → 롱(추종)", True, lambda d, i: _hi(d, i, "tls")),
    ("큰손 숏 → 숏(추종)", False, lambda d, i: _lo(d, i, "tls")),

    # ⑤ 테이커 — 시장가로 때린 쪽
    ("테이커 매수 극단 → 숏", False, lambda d, i: _hi(d, i, "tkr")),
    ("테이커 매도 극단 → 롱", True, lambda d, i: _lo(d, i, "tkr")),

    # ⑥ 개미와 큰손이 반대 — 가격만 봐서는 안 보이는 조건
    ("개미롱 · 큰손숏 → 숏", False,
     lambda d, i: _hi(d, i, "lsr") and _lo(d, i, "tls")),
    ("개미숏 · 큰손롱 → 롱", True,
     lambda d, i: _lo(d, i, "lsr") and _hi(d, i, "tls")),
]


# ── 통계 (signal_bench 와 동일) ───────────────────────────────

def mean_r(ts):
    return sum(t["r"] for t in ts) / len(ts) if ts else 0.0


def se_r(ts):
    n = len(ts)
    if n < 2:
        return float("inf")
    m = mean_r(ts)
    return math.sqrt(sum((t["r"] - m) ** 2 for t in ts) / (n - 1)) / math.sqrt(n)


def excess(a, b):
    if len(a) < MIN_PER_PERIOD or len(b) < MIN_PER_PERIOD:
        return 0.0, float("-inf"), float("inf")
    d = mean_r(a) - mean_r(b)
    se = math.sqrt(se_r(a) ** 2 + se_r(b) ** 2)
    return d, d - 1.96 * se, d + 1.96 * se


def split(ts, mid):
    return ([t for t in ts if t["date"] < mid], [t for t in ts if t["date"] >= mid])


# ── 실행 ─────────────────────────────────────────────────────

LAST_LEN = {}          # 코인 -> 실제로 받아진 봉 수 (빠진 이유를 남긴다)


def ohlcv(sym, coin, days):
    """시세를 받는다. 현물에 없으면 무기한 선물 표기로 다시 시도한다.

    CoinGlass 자료는 선물 쪽인데 시세는 현물에서 받고 있었다. 최근
    상장 종목은 거래소에 현물이 없어서 8종이 통째로 빠졌다 —
    ARB·SUI·TIA·SEI·RENDER·TON·PEPE·WIF. 하필 **최근 상장만**
    빠지면 남은 표본이 오래된 코인 쪽으로 기운다.
    """
    forms, seen = [], set()
    for s in (sym, f"{coin}/USDT:USDT", f"{coin}USDT",
              f"1000{coin}/USDT", f"1000{coin}/USDT:USDT"):
        if s not in seen:
            seen.add(s)
            forms.append(s)

    # 요청량도 낮춰 본다.
    #
    # 4년을 달라고 하면 상장한 지 얼마 안 된 코인은 빈손으로 온다.
    # ARB·SUI·TIA·SEI·RENDER·PEPE·WIF 가 그랬다 — 이름이 틀린 게
    # 아니라 **그만큼의 과거가 없는 것**이었다. 2년만 달라고 하면
    # 온다. 짧은 이력이라도 시험대에는 올릴 수 있다.
    best, best_n = None, 0
    for want in (days + 200, 1000, 600, 400):
        for s in forms:
            try:
                df = bt.get_ohlcv_history(s, "1d", want)
            except Exception:
                continue
            n = 0 if df is None else len(df)
            if n > best_n:
                best, best_n = df, n
            if n >= MIN_BARS:
                LAST_LEN[coin] = n
                return df
    LAST_LEN[coin] = best_n
    return best if best_n >= MIN_BARS else None


def run(coins, days, cache, candidates=None, attach_fn=None):
    """후보와 값 붙이는 법만 갈아 끼울 수 있다.

    cvd_bench 가 이 함수를 그대로 쓴다. 판정 방식(무작위 기준선·
    두 기간 게이트·손절 1.5×ATR)이 도구마다 달라지면 결과를 나란히
    놓을 수 없다. **재는 자는 하나여야 한다.**
    """
    candidates = candidates or CANDIDATES
    attach_fn = attach_fn or attach
    hits = {n: [] for n, _, _ in candidates}
    base = {True: [], False: []}
    failed, nodata = [], []

    for sym in coins:
        coin = sym.replace("/USDT", "")
        if not any(coin in cache.get(k, {}) for k in EXTRACT):
            nodata.append(coin)
            continue
        try:
            df = ohlcv(sym, coin, days)
            if df is None:
                # 왜 빠졌는지를 남긴다. '못 받음'만으로는 이름이
                # 틀린 건지 과거가 짧은 건지 알 수 없다.
                failed.append(f"{coin}({LAST_LEN.get(coin, 0)}봉)")
                continue
            d = attach_fn(bt.compute_indicators(df), cache, coin)
        except Exception as e:
            print(f"  [건너뜀] {coin}: {type(e).__name__}: {e}")
            failed.append(coin)
            continue

        for side in (True, False):
            base[side].extend(sb.random_trades(d, coin, side, n=SAMPLES_PER_COIN))

        for name, long_side, cond in candidates:
            free = -1
            for i in range(WARMUP, len(d) - 1):
                if i <= free:
                    continue
                try:
                    ok = bool(cond(d, i))
                except Exception:
                    ok = False
                if not ok:
                    continue
                t = sb.make_trade(d, i, long_side)
                if t:
                    t["_coin"] = coin
                    hits[name].append(t)
                    free = i + sb.hold_bars(d, t)
    return hits, base, failed, nodata


def main(argv):
    # --peek 은 캐시만 읽는다. backtest.py 없이도 돌아야 한다 —
    # 값이 제대로 들어왔는지는 시험대와 별개로 확인할 수 있어야 한다.
    if "--peek" in argv:
        return cmd_peek()

    if not _load():
        return 1

    rest = [a for a in argv[1:] if not a.startswith("-")]
    n = next((int(a) for a in rest if a.isdigit() and int(a) <= 50), 30)
    days = MAX_DAYS
    for a in rest:
        al = a.lower()
        if al.endswith("y") and al[:-1].isdigit():
            days = int(al[:-1]) * 365
        elif al.endswith("d") and al[:-1].isdigit():
            days = int(al[:-1])
    days = max(180, min(days, MAX_DAYS))
    coins = sb._pick(n)

    cache, cols, skipped = load_cache()
    if not cache:
        print(f"  {CACHE} 이 없습니다. 먼저 받으십시오:")
        print("      python coinglass_probe.py --fetch")
        return 1

    print("=" * 80)
    print("  비가격 시험대 — 포지션의 상태가 진입 시점을 알려주나")
    print("=" * 80)
    print(f"  대상 폴더: {os.getcwd()}")
    print(f"  {len(coins)}종 × {days}일 · 후보 {len(CANDIDATES)}개")
    print(f"  문턱: 그 코인의 지난 {PCT_WINDOW}일 상·하위"
          f" {round((1 - HI_Q) * 100)}% 분위수")
    print("  손절 1.5×ATR · 목표 1R/2R/3R · 한 코인 동시 1개")
    print("\n  쓰는 값")
    for kind in sorted(cache):
        name = EXTRACT.get(kind, (kind,))[0]
        ncoin = len(cache[kind])
        mark = "  ⚠️ 칼럼 추정" if cols.get(kind, "").endswith("?") else ""
        drop = f"  (못 읽음 {skipped[kind]:,})" if skipped.get(kind) else ""
        print(f"    {kind:<9} {name:<18} {ncoin:>3}종 · {cols.get(kind, '?')}{mark}{drop}")
    print("\n  3~8분 걸립니다...")
    t0 = time.time()

    hits, base, failed, nodata = run(coins, days, cache)
    if nodata:
        print(f"\n  ⚠️ 비가격 자료가 없어 빠짐 {len(nodata)}: {', '.join(nodata[:10])}")
    if failed:
        print(f"  ⚠️ 시세를 못 받아 빠짐 {len(failed)}: {', '.join(failed[:10])}")

    dates = sorted(t["date"] for ts in hits.values() for t in ts)
    if not dates:
        print("\n  거래가 하나도 없습니다. --peek 로 값이 제대로 붙는지 보십시오.")
        return 1
    mid = str(pd.Timestamp(dates[0])
              + (pd.Timestamp(dates[-1]) - pd.Timestamp(dates[0])) / 2)[:10]
    print(f"\n  구간 {dates[0]} ~ {dates[-1]}   (반 나누는 지점 {mid})")
    print(f"  기준선  무작위 롱 {mean_r(base[True]):+.3f}R ({len(base[True])}건)"
          f" · 무작위 숏 {mean_r(base[False]):+.3f}R ({len(base[False])}건)")

    passed = []
    for name, long_side, _ in sorted(CANDIDATES, key=lambda c: -mean_r(hits[c[0]])):
        ts, b = hits[name], base[long_side]
        print(f"\n  [{name}]  {'롱' if long_side else '숏'}  "
              f"{len(ts)}건 · {mean_r(ts):+.3f}R")
        if len(ts) < 30:
            print("    ⚠️ 30건 미만 — 판단 보류")
            continue
        marks = []
        for lbl, a, bb in (("전체", ts, b),
                           ("전반기", split(ts, mid)[0], split(b, mid)[0]),
                           ("후반기", split(ts, mid)[1], split(b, mid)[1])):
            dd, lo, hi = excess(a, bb)
            good = lo > 0 and len(a) >= MIN_PER_PERIOD
            if lbl != "전체":
                marks.append(good)
            tail = (f"   초과 {dd:+.3f}  [{lo:+.3f}, {hi:+.3f}] "
                    f"{'✅' if good else ('·' if dd > 0 else '❌')}"
                    if len(a) >= MIN_PER_PERIOD
                    else f"   — {MIN_PER_PERIOD}건 미만이라 판정 보류")
            print(f"    {lbl:<7} {mean_r(a):+.3f}R ({len(a):>4}건)  "
                  f"vs 무작위 {mean_r(bb):+.3f}R" + tail)
        if marks and all(marks):
            print("    ✅ 두 기간 모두 무작위보다 유의하게 낫다")
            passed.append(name)
        else:
            print("    ❌ 통과 못 함")

    print("\n" + "=" * 80)
    if passed:
        print(f"  통과: {', '.join(passed)}")
        print(f"""
  ⚠️ 여기서 {len(CANDIDATES)}개를 쟀습니다. 그 전에 45개를 쟀습니다.
     후보가 많으면 그중 하나가 우연히 좋아 보일 확률도 그만큼 높습니다.
     통과했다고 바로 돈을 걸지 마십시오. 순서를 지키십시오.

    1. diag_regime 으로 상승장/하락장 어느 한쪽 덕이 아닌지
    2. 봇에 **알림 전용**으로 넣고 페이퍼 4주 · 30건
    3. 백테스트 기대값과 페이퍼 실측이 비슷하면 그때 실매매""")
    else:
        print(f"""  통과한 후보가 없습니다. ({len(CANDIDATES)}개 시험)

  가격이 아닌 재료로도 진입 시점이 안 나온다는 뜻입니다. 남은 길:

    · 진입이 아니라 **크기**를 정하는 데 쓰기 — 언제 들어갈지가
      아니라 얼마나 걸지. 시험대가 다릅니다.
    · 봇을 스캐너·알림 도구로 확정하기. 사람이 판단하고 봇은
      6TF 정렬·레벨맵·펀딩 경보만 띄웁니다.

  어느 쪽이든, 무엇을 몇 개 재서 안 나왔는지가 남았습니다.
  그게 이 도구의 값어치입니다.""")
    print(f"\n  ({time.time() - t0:.0f}초 소요)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
