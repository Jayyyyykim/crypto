"""percentile_ctx.py — 지금 이 숫자가 역사적으로 어디쯤인가

    cd /path/to/jaybot
    python percentile_ctx.py --build     # 4년치로 기준표를 만든다 (한 번)
    python percentile_ctx.py             # 30종 지금 상태
    python percentile_ctx.py BTC         # 한 종목만
    python percentile_ctx.py --units     # 단위가 맞는지 점검 (중요)

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


def scale_hint(bp, value):
    """단위가 10의 거듭제곱만큼 어긋났나. 어긋났으면 곱할 값을 준다.

    CoinGlass 펀딩 close 가 0.01 인데 ccxt fundingRate 는 0.0001 이다.
    그대로 비교하면 매일 '상위 0%' 가 뜬다 — 조용히 틀린다.
    """
    if value is None or value != value or value == 0 or not bp:
        return 1.0
    mid = bp[len(bp) // 2]
    typ = max(abs(mid), abs(bp[-1] - bp[0]) / 4 or abs(mid))
    if typ <= 0:
        return 1.0
    ratio = abs(value) / typ
    if 0.05 <= ratio <= 20:
        return 1.0
    import math
    p = round(math.log10(typ / abs(value)))
    return 10.0 ** p if p else 1.0


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
        fs = []
        for coin, f in live.items():
            ent = table.get(coin, {}).get(key)
            v = f.get(key)
            if ent and v is not None:
                fs.append(scale_hint(ent["bp"], v))
        if len(fs) < 3:
            continue
        fs.sort()
        med = fs[len(fs) // 2]
        # 과반이 같은 배수일 때만 단위로 본다.
        if med != 1.0 and sum(1 for x in fs if x == med) * 2 > len(fs):
            out[key] = med
    return out


def context(coin, values, table=None, scales=None):
    """{키: {값, 분위, 조정배수}} — 값이 없거나 기준이 없으면 뺀다.

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
        f = float(sc.get(key, 1.0))
        vv = v * f
        out[key] = {"name": name, "value": vv, "raw": v, "scale": f,
                    "pct": rank(ent["bp"], vv), "n": ent["n"],
                    "from": ent["from"], "to": ent["to"]}
    return out


def extremes(ctx, edge=10):
    """상·하위 edge% 안에 든 지표 수."""
    return [k for k, c in ctx.items()
            if c["pct"] is not None and (c["pct"] >= 100 - edge or c["pct"] <= edge)]


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
        out[c] = f
    return out


def cmd_units(coins, table):
    """현재값과 과거 분포의 크기가 맞는지 본다. **여기가 제일 위험하다.**"""
    print("=" * 74)
    print("  단위 점검 — 지금 값과 과거 분포의 크기가 맞는가")
    print("=" * 74)
    live = live_values(coins[:6])
    if live is None:
        return 1
    scales = detect_scales(live, table)
    print("\n  " + w("지표", 16) + w("지금 값", 14, True)
          + w("과거 중앙값", 14, True) + "  판정")
    print("  " + "─" * 64)
    bad = []
    for key, name, _k, _h, fmt in METRICS:
        vs, mids = [], []
        for coin, f in live.items():
            ent = table.get(coin, {}).get(key)
            v = f.get(key)
            if not ent or v is None:
                continue
            vs.append(v)
            mids.append(ent["bp"][len(ent["bp"]) // 2])
        if not vs:
            print("  " + w(name, 16) + "지금 값 또는 기준이 없습니다")
            continue
        v = sorted(vs)[len(vs) // 2]
        mid = sorted(mids)[len(mids) // 2]
        f = scales.get(key, 1.0)
        note = "맞습니다" if f == 1.0 else f"⚠️ {f:g}배로 맞춰 씁니다"
        if f != 1.0:
            bad.append(name)
        print("  " + w(name, 16) + w(f"{v:.6g}", 14, True)
              + w(f"{mid:.6g}", 14, True) + "  " + note)
    print("  " + "─" * 64)
    if bad:
        print(f"""
  {', '.join(bad)} 의 단위가 다릅니다. 자동으로 맞춰 쓰지만,
  값이 이상하면 이쪽을 먼저 의심하십시오. 예를 들어 CoinGlass 는
  펀딩을 0.01(%)로, ccxt 는 0.0001(비율)로 줍니다 — 같은 값입니다.""")
    else:
        print("\n  전부 맞습니다. 그대로 비교해도 됩니다.")
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
    rows = []
    for coin in coins:
        f = live.get(coin)
        if not f:
            continue
        ctx = context(coin, f, table, scales)
        if ctx:
            rows.append((coin, ctx, len(extremes(ctx))))
    if not rows:
        print("  띄울 것이 없습니다. --build 를 먼저 하셨습니까?")
        return 1

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
            if c["scale"] != 1.0:
                scaled.add(name)
            print("      " + w(name, 16) + w(fmt.format(c["value"]), 12, True)
                  + "   " + say(c["pct"]))
        print()

    print("=" * 78)
    ex = [c for c, _x, n in rows if n >= 3]
    if ex:
        print(f"  세 개 이상 극단: {', '.join(ex)}")
    else:
        print("  세 개 이상 극단인 종목은 없습니다. 평범한 날입니다.")
    if scaled:
        print(f"  · 단위를 맞춰 쓴 지표: {', '.join(sorted(scaled))}"
              "  (--units 로 확인)")
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
