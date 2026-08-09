"""percentile_ctx.py — 지금 이 숫자가 역사적으로 어디쯤인가

    cd /path/to/jaybot
    python percentile_ctx.py --build     # 4년치로 기준표를 만든다 (한 번)
    python percentile_ctx.py             # 30종 지금 상태
    python percentile_ctx.py BTC         # 한 종목만
    python percentile_ctx.py --units     # 단위가 맞는지 점검 (중요)
    python percentile_ctx.py --taker BTC # 테이커를 24시간치로 받을 수 있나

무엇을 하나
──────────
봇이 이렇게 말한다:

    BTC  펀딩 0.018%   OI 24h +12%   롱숏비 1.83

숫자만 봐서는 이게 높은 건지 낮은 건지 알 수 없다. 그런데 우리는
30종 × 5.8년치 파생 이력을 갖고 있다. 그러면 이렇게 말할 수 있다:

    BTC  펀딩 0.018%   상위 3%
         OI 24h +12%   상위 5%
         롱숏비 1.83   상위 8%
         ⚠️ 세 지표가 동시에 상위 10%

**이건 매매 신호가 아니다.** 60개를 재서 이 값들로 진입 시점을 고를
수 없다는 것이 확인됐다(FINDINGS.md). 이건 사람이 볼 문맥이다.
"지금 뭔가 평소와 다르다"와 "지금 사라"는 다른 말이다.

구독을 끊어도 된다
─────────────────
과거는 CoinGlass 로 이미 받아 뒀고(coinglass_hist.jsonl), 그건 안
사라진다. **지금 값은 공짜로 온다** — 바이낸스 공개 엔드포인트가
현재 미결제약정·롱숏비·테이커를 주고, 펀딩은 ccxt 로 온다.

    30일 제한은 **과거**에 걸린 것이지 현재값에 걸린 게 아니다.

그래서 이 조합이 성립한다: 유료로 산 4년치 기준 + 공짜 현재값.

단위가 제일 위험하다
───────────────────
CoinGlass 의 펀딩 close 가 0.01 인데 ccxt 의 fundingRate 는 0.0001
이면, 같은 값인데 100배 차이다. 그대로 비교하면 **매일 "상위 0%"**
가 뜬다. 조용히 틀리는 종류다.

그래서 --units 로 현재값의 크기와 과거 분포의 크기를 견줘, 10의
거듭제곱 차이가 나면 잡아낸다. 자동으로 맞추되 **무엇을 맞췄는지
반드시 화면에 적는다.**
"""

import bisect
import json
import os
import sys

TABLE = "percentile_ctx.json"
CACHE = "coinglass_hist.jsonl"
STEPS = 101                # 0·1·…·100 분위 경계
MIN_DAYS = 200             # 이보다 짧으면 분위수가 의미 없다

# (키, 표시 이름, coinglass 항목, 파생?, 표시 형식)
#
# 파생이 필요한 것은 미결제약정뿐이다. 수준(1.5e9)은 코인끼리도
# 시기끼리도 비교가 안 된다. 하루 변화율(%)로 바꿔야 비교가 된다.
METRICS = [
    ("funding", "펀딩비", "funding", None, "{:+.4f}%"),
    ("oi_chg_24h", "OI 24h", "oi", "pct_change", "{:+.2f}%"),
    ("ls_ratio", "롱숏 계정비", "ls_ratio", None, "{:.3f}"),
    ("top_ls_ratio", "상위 트레이더", "top_ls", None, "{:.3f}"),
    ("taker_ratio", "테이커 매수비", "taker", None, "{:.3f}"),
]


# ── 캐시에서 기준표 만들기 ───────────────────────────────────

def to_ms(ts):
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
    import datetime
    try:
        return datetime.datetime.fromtimestamp(
            ms / 1000, datetime.timezone.utc).strftime("%Y-%m-%d")
    except (OSError, OverflowError, ValueError, TypeError):
        return None


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


def value_of(kind, raw):
    """feature_bench 와 **같은 규칙**으로 값을 뽑는다.

    여기가 어긋나면 시험대에서 잰 것과 화면에 띄우는 것이 다른 값이
    된다. 같은 이름 후보를 같은 순서로 본다.
    """
    if not isinstance(raw, dict):
        return None
    if kind in ("funding", "oi", "oi_agg"):
        return _num(raw.get("close"))
    if kind == "ls_ratio":
        v = _num(raw.get("global_account_long_short_ratio"))
        if v is not None:
            return v
        a, b = _num(raw.get("global_account_long_percent")), \
            _num(raw.get("global_account_short_percent"))
        return a / b if a is not None and b else None
    if kind == "top_ls":
        v = _num(raw.get("top_account_long_short_ratio"))
        if v is not None:
            return v
        a, b = _num(raw.get("top_account_long_percent")), \
            _num(raw.get("top_account_short_percent"))
        return a / b if a is not None and b else None
    if kind == "taker":
        a = _num(raw.get("taker_buy_volume_usd"))
        b = _num(raw.get("taker_sell_volume_usd"))
        if a is None or b is None:
            return None
        return a / (a + b) if (a + b) else None
    return None


def read_series(path=CACHE):
    """{항목: {코인: [(날짜, 값), ...]}} — 날짜순."""
    tmp = {}
    if not os.path.exists(path):
        return {}
    wanted = {m[2] for m in METRICS}
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
            if kind not in wanted:
                continue
            ms = to_ms(r.get("ts"))
            date = day_of(ms) if ms is not None else None
            if date is None:
                continue
            v = value_of(kind, r.get("raw"))
            if v is None:
                continue
            day = tmp.setdefault(kind, {}).setdefault(coin, {})
            if date not in day or ms > day[date][0]:
                day[date] = (ms, v)
    return {k: {c: sorted((d, v) for d, (_, v) in days.items())
                for c, days in coins.items()}
            for k, coins in tmp.items()}


def derive(series, how):
    """(날짜, 값) 목록을 파생값으로 바꾼다."""
    if how is None:
        return [v for _, v in series]
    if how == "pct_change":
        # 하루 변화율(%). 앞뒤 날짜가 붙어 있지 않으면 건너뛴다 —
        # 사흘 건너뛴 변화를 '하루 변화'라고 부르면 분포가 부푼다.
        import datetime
        out = []
        for i in range(1, len(series)):
            d0, v0 = series[i - 1]
            d1, v1 = series[i]
            try:
                gap = (datetime.date.fromisoformat(d1)
                       - datetime.date.fromisoformat(d0)).days
            except ValueError:
                continue
            if gap != 1 or not v0:
                continue
            out.append((v1 / v0 - 1) * 100.0)
        return out
    raise ValueError(how)


def breakpoints(values, steps=STEPS):
    """0~100 분위 경계. 값이 적으면 None."""
    vals = sorted(v for v in values if v == v)
    if len(vals) < MIN_DAYS:
        return None
    n = len(vals) - 1
    return [vals[round(n * i / (steps - 1))] for i in range(steps)]


def build(path=CACHE):
    series = read_series(path)
    table, thin = {}, []
    for key, name, kind, how, _fmt in METRICS:
        per_coin = series.get(kind, {})
        for coin, rows in per_coin.items():
            vals = derive(rows, how)
            bp = breakpoints(vals)
            if bp is None:
                thin.append(f"{key}/{coin}({len(vals)})")
                continue
            table.setdefault(coin, {})[key] = {
                "bp": [round(x, 8) for x in bp],
                "n": len(vals),
                "from": rows[0][0],
                "to": rows[-1][0],
            }
    return table, thin


# ── 조회 ─────────────────────────────────────────────────────

def load_table(path=TABLE):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fp:
            return json.load(fp)
    except (json.JSONDecodeError, ValueError):
        return {}


def rank(bp, value):
    """이 값이 과거 분포에서 몇 분위인가 (0~100). 못 재면 None."""
    if not bp or value is None or value != value:
        return None
    i = bisect.bisect_left(bp, value)
    return max(0, min(100, i))


# 표기를 맞추는 방법들.
#
# 10의 거듭제곱만 볼 게 아니다. 테이커가 그랬다 — 과거는
# buy/(buy+sell) 인 비율(0~1)인데 봇은 buy/sell 인 비(比)를 준다.
# 매수와 매도가 비슷하면 비는 1.0, 비율은 0.5다. 두 배 차이라
# '거듭제곱 검사'는 그냥 통과시킨다. 초록불을 잘못 켜준 셈이다.
IDENTITY = {"kind": "그대로"}
ALT_FORMS = (
    IDENTITY,
    {"kind": "비→비율", "how": "share"},        # x/(1+x)
    {"kind": "비율→비", "how": "unshare"},      # x/(1-x)
    {"kind": "×100", "how": "mul", "k": 100.0},
    {"kind": "×0.01", "how": "mul", "k": 0.01},
    {"kind": "×10000", "how": "mul", "k": 10000.0},
    {"kind": "×0.0001", "how": "mul", "k": 0.0001},
    {"kind": "×1000", "how": "mul", "k": 1000.0},
    {"kind": "×0.001", "how": "mul", "k": 0.001},
)


def apply_form(form, x):
    """표기 변환. 못 하면 None."""
    if x is None or x != x or not form:
        return None
    how = form.get("how")
    if how is None:
        return x
    if how == "mul":
        return x * form["k"]
    if how == "share":
        return x / (1.0 + x) if x > -1 else None
    if how == "unshare":
        return x / (1.0 - x) if x < 1 else None
    return x


def _spread(bp):
    """과거 분포의 '보통 폭'. 0 이면 중앙값 크기로 대신한다."""
    lo, hi = bp[10], bp[90]
    s = abs(hi - lo)
    return s if s > 0 else (abs(bp[len(bp) // 2]) or 1.0)


def outside(bp, value):
    """과거 범위(1~99분위) 밖으로 얼마나 벗어났나 (폭 단위). 안이면 0.

    표기가 다르다는 신호는 '중앙값에서 멀다'가 아니라 **'과거 범위
    안에 아예 들어가지 못한다'** 이다. 테이커 1.0 은 비율 분포
    0.45~0.55 안에 들어갈 수가 없다.
    """
    if value is None or value != value:
        return float("inf")
    lo, hi = bp[1], bp[99]
    if lo <= value <= hi:
        return 0.0
    return (lo - value if value < lo else value - hi) / _spread(bp)


def form_score(bp, value):
    """(범위 밖 거리, 중앙값과의 거리). 작을수록 잘 맞는다.

    범위 안에 들어가는 것이 먼저고, 그 안에서 중앙에 가까운 것이 다음.
    """
    if value is None or value != value:
        return (float("inf"), float("inf"))
    return (outside(bp, value),
            abs(value - bp[len(bp) // 2]) / _spread(bp))


def best_form(bp, value):
    """이 값에 가장 맞는 표기와 그 점수. **여기서 바로 쓰면 안 된다.**

    값 하나로 정하면 진짜 극단값을 '표기 오류'로 지워 버린다.
    detect_scales 가 여러 코인을 모아 보고 정한다.
    """
    best, best_s = IDENTITY, form_score(bp, value)
    for form in ALT_FORMS[1:]:
        s = form_score(bp, apply_form(form, value))
        if s < best_s:
            best, best_s = form, s
    return best, best_s


def scale_hint(bp, value):
    """되돌림용 — 예전 이름. 곱셈 배수만 돌려준다."""
    if not bp or value is None:
        return 1.0
    form, _ = best_form(bp, value)
    return form.get("k", 1.0) if form.get("how") == "mul" else 1.0


def detect_scales(live, table):
    """지표별 단위 배수를 **여러 코인을 한꺼번에 보고** 정한다.

    코인 하나만 보고 정하면 안 된다. 그러면 '진짜 극단값'과 '단위가
    다름'을 구분할 수 없고, 하필 **우리가 띄우려는 극단을 조용히
    평범한 값으로 지워 버린다.**

    단위 문제라면 30종이 전부 같은 배수로 어긋나 있다. 한 종목만
    어긋나 있으면 그건 오늘 그 코인에 무슨 일이 있는 것이다.
    """
    out = {}
    for key, *_rest in METRICS:
        picks, id_out = [], []
        for coin, f in live.items():
            ent = table.get(coin, {}).get(key)
            v = f.get(key)
            if not ent or v is None:
                continue
            form, _s = best_form(ent["bp"], v)
            picks.append(form["kind"])
            id_out.append(outside(ent["bp"], v))
        if len(picks) < 3:
            continue
        # 두 가지가 다 맞아야 표기를 바꾼다.
        #   ① 과반이 같은 표기를 가리킨다 (한 종목만이면 그건 사건이다)
        #   ② '그대로'로는 대부분이 과거 범위 **밖**이다
        # ②가 없으면 살짝 나은 표기로 갈아타면서 진짜 극단을 지운다.
        best = max(set(picks), key=picks.count)
        id_out.sort()
        typical = id_out[len(id_out) // 2]
        if (best != IDENTITY["kind"] and picks.count(best) * 2 > len(picks)
                and typical > 0.5):
            out[key] = next(f for f in ALT_FORMS if f["kind"] == best)
    return out


def context(coin, values, table=None, scales=None):
    """{키: {값, 분위, 표기변환}} — 값이 없거나 기준이 없으면 뺀다.

    scales 는 detect_scales 가 준 것을 그대로 넘긴다. 여기서 코인마다
    따로 추측하지 않는다 — 그러면 극단값이 지워진다.
    """
    tab = (table if table is not None else load_table()).get(coin, {})
    sc = scales or {}
    out = {}
    for key, name, _kind, _how, _fmt in METRICS:
        v = values.get(key)
        ent = tab.get(key)
        if v is None or not ent:
            continue
        form = sc.get(key) or IDENTITY
        if isinstance(form, (int, float)):          # 예전 형식
            form = IDENTITY if form == 1.0 else {"kind": f"×{form:g}",
                                                 "how": "mul", "k": float(form)}
        vv = apply_form(form, v)
        if vv is None:
            continue
        out[key] = {"name": name, "value": vv, "raw": v,
                    "form": form["kind"], "scale": form.get("k", 1.0),
                    "pct": rank(ent["bp"], vv), "n": ent["n"],
                    "from": ent["from"], "to": ent["to"]}
    return out


def extremes(ctx, edge=10, skip=()):
    """상·하위 edge% 안에 든 지표. skip 에 든 것은 세지 않는다."""
    return [k for k, c in ctx.items()
            if k not in skip and c["pct"] is not None
            and (c["pct"] >= 100 - edge or c["pct"] <= edge)]


# 지표 하나가 종목의 이만큼을 극단이라고 하면, 그건 종목이 아니라
# 비교가 이상한 것이다.
MISCAL = 0.40


def lean(ctxs, key, edge=10):
    """극단이 **한쪽으로만** 몰렸나. 1.0 이면 전부 한쪽이다.

    이걸 봐야 원인이 갈린다.
      · 위아래로 고루 흩어져 극단이 많다 → 지금 값이 과거보다 요동친다
        (시간 창이 다르거나 출처가 다르다). 비교가 깨진 것이다.
      · 한쪽으로만 몰렸다 → 오늘 시장 전체가 그쪽으로 기울었을 수 있다.
        비교는 멀쩡하고 **정말로 다 같이 극단인 날**일 수 있다.

    도구가 이 둘을 구분하지 못하면서 하나라고 단정하면 안 된다.
    """
    hi = lo = 0
    for c in ctxs:
        p = c.get(key, {}).get("pct")
        if p is None:
            continue
        if p >= 100 - edge:
            hi += 1
        elif p <= edge:
            lo += 1
    tot = hi + lo
    return (max(hi, lo) / tot, "위" if hi >= lo else "아래") if tot else (0.0, "")


def calibration(ctxs, edge=10):
    """지표별로 '극단'이 몇 몫이나 나오나. 눈금이 맞으면 ~0.2 다.

    상·하위 10%씩이니 제대로 맞으면 다섯에 하나쯤이 극단이어야 한다.
    스물 중 열둘이 '역대 최고'면 그 지표는 종목이 특이한 게 아니라
    **비교 자체가 안 맞는 것**이다.

    실제로 테이커가 그랬다. 과거는 CoinGlass 의 **하루치** 매수/매도인데
    봇이 주는 지금 값은 **한 시간치**다. 한 시간 매수비는 하루 매수비보다
    훨씬 요동치니 매일 극단이 뜬다. 단위가 아니라 **시간 창**이 다르다.
    """
    out = {}
    for key, *_rest in METRICS:
        vals = [c[key]["pct"] for c in ctxs
                if key in c and c[key]["pct"] is not None]
        if len(vals) < 8:
            continue
        ext = sum(1 for p in vals if p >= 100 - edge or p <= edge)
        out[key] = ext / len(vals)
    return out


def miscalibrated(cal):
    return {k for k, v in cal.items() if v > MISCAL}


def say(pct):
    """'상위 0%' 같은 말이 나오면 안 된다 — 읽는 사람이 멈칫한다."""
    if pct is None:
        return "—"
    if pct >= 100:
        return "역대 최고 수준  ◀◀"
    if pct <= 0:
        return "역대 최저 수준  ▶▶"
    if pct >= 98:
        return f"상위 {100 - pct}%  ◀◀"
    if pct >= 90:
        return f"상위 {100 - pct}%  ◀"
    if pct <= 2:
        return f"하위 {pct}%  ▶▶"
    if pct <= 10:
        return f"하위 {pct}%  ▶"
    return f"{pct}분위"


# ── 화면 ─────────────────────────────────────────────────────

def w(text, width, right=False):
    import unicodedata
    n = sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in str(text))
    pad = " " * max(0, width - n)
    return (pad + str(text)) if right else (str(text) + pad)


def cmd_build():
    if not os.path.exists(CACHE):
        print(f"  {CACHE} 이 없습니다.")
        print("  python coinglass_probe.py --fetch  으로 먼저 받으십시오.")
        return 1
    print("  4년치를 읽어 기준표를 만듭니다...")
    table, thin = build()
    if not table:
        print("  만들 수 있는 기준이 없습니다.")
        return 1
    with open(TABLE, "w", encoding="utf-8") as fp:
        json.dump(table, fp, ensure_ascii=False, separators=(",", ":"))
    keys = sorted({k for v in table.values() for k in v})
    print(f"\n  {len(table)}종 · 지표 {len(keys)}개 → {TABLE}"
          f" ({os.path.getsize(TABLE) // 1024}KB)")
    for key, name, _k, _h, _f in METRICS:
        have = [c for c, v in table.items() if key in v]
        if have:
            ex = table[have[0]][key]
            print(f"    {w(name, 16)}{len(have):>3}종 · 예) {have[0]} "
                  f"{ex['n']:,}일 ({ex['from']} ~ {ex['to']})")
    if thin:
        print(f"\n  · 자료가 짧아 뺀 것 {len(thin)}: {', '.join(thin[:6])}")
    print(f"""
  이제 구독을 끊어도 됩니다. 이 표와 {CACHE} 은 남습니다.
  지금 값은 공짜로 옵니다 — 30일 제한은 **과거**에 걸린 것입니다.

  확인:  python percentile_ctx.py --units""")
    return 0


def daily_taker(liq, coin):
    """테이커 매수비를 **하루치로** 받는다. 못 받으면 None.

    feature_log 는 period='1h' 로 받고 buy/sell 인 비(比)를 준다.
    그런데 과거(CoinGlass)는 하루치 매수/매도를 합친 **비율**이다.
    한 시간 매수비는 하루보다 훨씬 요동쳐서, 그대로 견주면 종목의
    79% 가 '역대 최고/최저'로 나온다 — 매일.

    period='1d' 가 되고 buy·sell 원값을 주므로, 변환을 추측할 것도
    없이 과거와 같은 방식으로 바로 계산한다.
    """
    try:
        r = liq.get_taker_buy_sell(f"{coin}/USDT", period="1d", limit=1)
    except Exception:
        return None
    if not isinstance(r, dict):
        return None
    b, s = _num(r.get("buy")), _num(r.get("sell"))
    if b is None or s is None or (b + s) == 0:
        return None
    return b / (b + s)


def live_values(coins):
    """지금 값을 가져온다. feature_log 의 수집기를 그대로 쓴다."""
    sys.path.insert(0, os.getcwd())
    try:
        import feature_log as fl
        import liquidation as liq
    except ImportError as e:
        print(f"  feature_log.py / liquidation.py 를 못 불렀습니다: {e}")
        print(f"  지금 폴더: {os.getcwd()}")
        return None
    fm = fl.funding_all()
    missing = [c for c in coins if c not in fm]
    if missing:
        try:
            fm.update(fl.funding_via_ccxt(missing))
        except Exception:
            pass
    out = {}
    for c in coins:
        try:
            f, _bad = fl.collect(c, liq, fm)
        except Exception as e:
            print(f"    {c}: {type(e).__name__}: {e}")
            continue
        # 테이커만 다시 받는다 — 과거와 같은 창(하루)이어야 비교가 된다.
        t = daily_taker(liq, c)
        if t is not None:
            f["taker_ratio"] = t
        out[c] = f
    return out


def cmd_taker(coin):
    """테이커를 살릴 수 있나 — 24시간치로 받을 방법이 있는지 본다.

    과거는 하루치인데 지금 값은 한 시간치라 매일 극단이 뜬다.
    거래소가 하루치를 바로 주거나, 한 시간치를 24개 모아 합칠 수
    있으면 풀린다. 추측하지 말고 **실제로 뭐가 오는지 본다.**
    """
    sys.path.insert(0, os.getcwd())
    try:
        import liquidation as liq
    except ImportError as e:
        print(f"  liquidation.py 를 못 불렀습니다: {e}")
        return 1
    symbol = f"{coin}/USDT"
    print("=" * 74)
    print(f"  테이커 — 무엇이 오는지 본다 ({symbol})")
    print("=" * 74)

    for period, limit in (("1h", 1), ("1d", 1), ("4h", 1), ("1h", 24)):
        print(f"\n  period={period!r} limit={limit}")
        try:
            r = liq.get_taker_buy_sell(symbol, period=period, limit=limit)
        except Exception as e:
            print(f"    ❌ {type(e).__name__}: {e}")
            continue
        if r is None:
            print("    (None)")
            continue
        if isinstance(r, dict):
            print(f"    dict · 칼럼={list(r)[:10]}")
            print(f"    값={ {k: r[k] for k in list(r)[:6]} }")
        elif isinstance(r, list):
            print(f"    list · {len(r)}개")
            if r:
                first = r[0]
                print(f"    첫 원소={first if not isinstance(first, dict) else list(first)[:10]}")
                if isinstance(first, dict):
                    print(f"    값={ {k: first[k] for k in list(first)[:6]} }")
                # 매수·매도 양을 찾으면 24시간치를 합쳐 본다
                bk = next((k for k in first if "buy" in k.lower()), None)
                sk = next((k for k in first if "sell" in k.lower()), None)
                if bk and sk and len(r) > 1:
                    try:
                        b = sum(float(x[bk]) for x in r)
                        s = sum(float(x[sk]) for x in r)
                    except (TypeError, ValueError, KeyError):
                        b = s = 0
                    if b + s:
                        print(f"    ▶ {len(r)}개를 합치면 매수비 = "
                              f"{b / (b + s):.4f}   ({bk} / ({bk}+{sk}))")
        else:
            print(f"    {type(r).__name__}: {r!r}")

    print("\n" + "=" * 74)
    print("""  읽는 법
    · period='1d' 가 되면 그걸 쓰면 됩니다 — 과거와 같은 창입니다.
    · 안 되면 '1h × 24개'를 합친 매수비를 쓰면 됩니다. 위에 ▶ 로
      계산해 두었습니다. 그 값이 0.5 근처면 맞는 것입니다.
    · 둘 다 안 되면 테이커는 '비교 안 맞음'으로 두십시오.
      틀린 극단을 띄우는 것보다 낫습니다.""")
    return 0


def cmd_units(coins, table):
    """현재값과 과거 분포의 크기가 맞는지 본다. **여기가 제일 위험하다.**"""
    print("=" * 74)
    print("  단위 점검 — 지금 값과 과거 분포의 크기가 맞는가")
    print("=" * 74)
    live = live_values(coins[:6])
    if live is None:
        return 1
    scales = detect_scales(live, table)
    # 중앙값만 보여 주면 못 잡는다. 테이커가 그랬다 — 지금 값 1.0,
    # 과거 중앙값 0.49. 두 배 차이라 눈으로도 그냥 넘어갔다.
    # **과거가 어디부터 어디까지인지**를 같이 보여야 벗어난 게 보인다.
    print("\n  " + w("지표", 16) + w("지금 값", 11, True)
          + w("과거 범위(10~90분위)", 24, True) + "  판정")
    print("  " + "─" * 70)
    bad = []
    for key, name, _k, _h, fmt in METRICS:
        vs, los, his = [], [], []
        for coin, f in live.items():
            ent = table.get(coin, {}).get(key)
            v = f.get(key)
            if not ent or v is None:
                continue
            vs.append(v)
            los.append(ent["bp"][10])
            his.append(ent["bp"][90])
        if not vs:
            print("  " + w(name, 16) + "지금 값 또는 기준이 없습니다")
            continue
        v = sorted(vs)[len(vs) // 2]
        lo = sorted(los)[len(los) // 2]
        hi = sorted(his)[len(his) // 2]
        form = scales.get(key)
        if form:
            bad.append(f"{name}({form['kind']})")
            note = f"⚠️ {form['kind']} 로 맞춰 씁니다"
        elif not (lo <= v <= hi):
            note = "· 범위 밖 (오늘이 특이할 수 있음)"
        else:
            note = "맞습니다"
        print("  " + w(name, 16) + w(f"{v:.6g}", 11, True)
              + w(f"{lo:.6g} ~ {hi:.6g}", 24, True) + "  " + note)
    print("  " + "─" * 70)
    if bad:
        print(f"""
  표기가 다릅니다: {', '.join(bad)}
  자동으로 맞춰 쓰지만, 값이 이상하면 이쪽을 먼저 의심하십시오.

  흔한 경우
    · 펀딩   CoinGlass 0.01(%)  vs  ccxt 0.0001(비율)
    · 테이커 과거는 buy/(buy+sell) 인 **비율**(0~1),
             봇은 buy/sell 인 **비**(比). 매수≈매도면 비는 1.0,
             비율은 0.5다 — 두 배 차이라 눈으로는 잘 안 보인다.""")
    else:
        print("\n  전부 맞습니다. 그대로 비교해도 됩니다.")
        print("  · '범위 밖'은 표기 문제가 아니라 오늘 값이 특이하다는 뜻입니다.")
    return 0


def cmd_show(coins, table):
    live = live_values(coins)
    if live is None:
        return 1
    print("=" * 78)
    print("  지금 값이 역사적으로 어디쯤인가")
    print("=" * 78)
    print("  ※ 매매 신호가 아닙니다. 이 값들로 진입 시점을 고를 수 없다는 것은")
    print("    이미 확인됐습니다(FINDINGS.md · 60개 시험 · 통과 0).")
    print("    '평소와 다르다'와 '지금 사라'는 다른 말입니다.\n")

    scales = detect_scales(live, table)
    built = []
    for coin in coins:
        f = live.get(coin)
        if not f:
            continue
        ctx = context(coin, f, table, scales)
        if ctx:
            built.append((coin, ctx))
    if not built:
        print("  띄울 것이 없습니다. --build 를 먼저 하셨습니까?")
        return 1

    # 눈금이 안 맞는 지표는 극단 세기에서 뺀다. 안 그러면 "3개 지표가
    # 동시에 극단" 이 깨진 지표 하나 때문에 매일 뜬다.
    cal = calibration([c for _n, c in built])
    bad_metric = miscalibrated(cal)

    rows = [(coin, ctx, len(extremes(ctx, skip=bad_metric)))
            for coin, ctx in built]
    rows.sort(key=lambda r: -r[2])
    scaled = set()
    for coin, ctx, n in rows:
        head = f"  {coin}"
        if n >= 3:
            head += f"    ⚠️ {n}개 지표가 동시에 극단"
        elif n == 2:
            head += f"    · {n}개 지표가 극단"
        print(head)
        for key, name, _k, _h, fmt in METRICS:
            c = ctx.get(key)
            if not c:
                continue
            if c["form"] != IDENTITY["kind"]:
                scaled.add(f"{name}({c['form']})")
            tail = "   (비교 안 맞음)" if key in bad_metric else ""
            print("      " + w(name, 16) + w(fmt.format(c["value"]), 12, True)
                  + "   " + say(c["pct"]) + tail)
        print()

    print("=" * 78)
    ex = [c for c, _x, n in rows if n >= 3]
    if ex:
        print(f"  세 개 이상 극단: {', '.join(ex)}")
    else:
        print("  세 개 이상 극단인 종목은 없습니다. 평범한 날입니다.")
    if scaled:
        print(f"  · 표기를 맞춘 지표: {', '.join(sorted(scaled))}  (--units 로 확인)")

    if bad_metric:
        print("\n  ⚠️ 극단이 너무 많은 지표 — 극단 세기에서 뺐습니다")
        one_sided = []
        for key, name, _k, _h, _f in METRICS:
            if key not in bad_metric:
                continue
            side_frac, side = lean([c for _n, c in built], key)
            tag = f" · {side_frac * 100:.0f}% 가 '{side}'쪽" if side else ""
            print(f"      {w(name, 16)}종목의 {cal[key] * 100:.0f}% 가 극단"
                  f"  (제대로 맞으면 20% 안팎){tag}")
            if side_frac >= 0.8:
                one_sided.append(name)
        print("""
     원인이 둘 중 하나인데, 이 도구는 그 둘을 구분하지 못합니다.

       ① 비교가 깨졌다 — 지금 값과 과거의 출처나 시간 창이 다르다.
          그러면 위아래로 고루 극단이 나옵니다.
       ② 오늘 정말로 다 같이 쏠렸다 — 코인은 같이 움직입니다.
          그러면 한쪽으로만 몰립니다.""")
        if one_sided:
            print(f"""
     지금은 {', '.join(one_sided)} 가 **한쪽으로만** 몰려 있어 ②쪽에
     가깝습니다. 다만 어느 쪽이든, 스물 중 열다섯이 극단이면 '무엇이
     튀는가'를 가릴 수 없으므로 세기에서는 뺍니다.

     ②라면 고칠 것은 비교 대상입니다 — 과거가 아니라 **오늘 다른
     종목들**과 견주면 됩니다. 필요하면 그 화면을 붙이겠습니다.""")
        else:
            print("""
     지금은 위아래로 흩어져 있어 ①쪽에 가깝습니다. 지금 값의 출처와
     시간 창이 과거와 같은지 확인하십시오 (--taker 참고).""")
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

    if "--build" in argv:
        return cmd_build()
    if "--taker" in argv:
        i = argv.index("--taker")
        coin = argv[i + 1].upper() if i + 1 < len(argv) else "BTC"
        return cmd_taker(coin)

    table = load_table()
    if not table:
        print(f"  {TABLE} 이 없습니다. 먼저 만드십시오:")
        print("      python percentile_ctx.py --build")
        return 1

    named = [a.upper() for a in argv[1:] if not a.startswith("-")]
    if named:
        coins = named
    else:
        try:
            import spotlight
            coins = [c.replace("/USDT", "") for c in spotlight.SCAN_COINS][:30]
        except Exception:
            coins = sorted(table)[:30]

    if "--units" in argv:
        return cmd_units(coins, table)
    return cmd_show(coins, table)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
