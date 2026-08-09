"""coinglass_probe.py — 돈 내기 전에 무엇이 오는지 확인한다

    set COINGLASS_API_KEY=발급받은키          (윈도우 cmd)
    python coinglass_probe.py                 # 무엇이 되고 몇 년치가 오나
    python coinglass_probe.py --fetch         # 되는 것만 일봉으로 받아 캐시
    python coinglass_probe.py --coverage      # 받은 게 온전한가 (구독 끊기 전 점검)

    키를 환경변수 대신 직접 줄 수도 있습니다:
    python coinglass_probe.py --key 발급받은키

왜 이걸 먼저 하나
────────────────
비가격 정보(미결제약정·롱숏비·테이커) 과거는 거래소가 30일치만 준다.
그래서 feature_log 로 매일 적어 3개월을 기다리기로 했다.

CoinGlass 문서를 보니 **일봉은 전 구간이 온다** — 제일 싼 등급부터.

    plan            1m   15m   1h    4h     1d
    HOBBYIST        ✗    ✗     ✗     180일  전 구간
    STARTUP         ✗    ✗     180일 180일  전 구간
    STANDARD        6일  90일  360일 360일  전 구간

  우리 백테스트는 **일봉**이다. 그러면 제일 싼 등급으로 충분하고,
  3개월을 기다릴 필요가 없다. 한 달치 요금으로 오늘 재고, 아니면
  끊으면 된다.

  다만 이건 문서에 적힌 말이고, **실제로 몇 년치가 오는지는 키로
  찔러봐야 안다.** 그게 이 스크립트다. 코인마다 상장 시점이 다르고
  "코인·거래소별 최초 시점을 넘을 수 없다"는 단서도 붙어 있다.

무엇을 확인하나
──────────────
BTC·ETH·SOL 셋으로 각 항목을 일봉으로 찔러 보고 이렇게 찍는다:

    항목                    상태      받은 봉   가장 오래된 날
    미결제약정              ✅         1000     2022-11-13
    펀딩비                  ✅         1000     2022-11-13
    롱숏 계정비             ❌ 403     —        —

  ❌ 가 뜨면 그 항목은 그 등급에서 안 오는 것이다. 무엇이 되고
  무엇이 안 되는지 보고 결제 여부를 정하면 된다.

  엔드포인트 경로는 문서 판마다 표기가 갈려서(open-interest vs
  openInterest) 후보를 몇 개씩 돌려 본다. 되는 경로를 찾으면 그걸 쓴다.

받은 뒤
──────
--fetch 는 coinglass_hist.jsonl 에 덧붙인다. funding_bench 가 읽는
형식과 같아서, 받은 다음 바로 시험대에 올릴 수 있다.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://open-api-v4.coinglass.com"

# 등급별 분당 요청 한도. 넘기면 429 가 오고, 그걸 "안 되는 항목"으로
# 오해하게 된다 — 결제 판단이 통째로 틀어진다.
#   HOBBYIST 30/분 · STARTUP 80/분 · STANDARD 300/분 · PROFESSIONAL 1200/분
RATE_PER_MIN = 30
RATE_SLEEP = 60.0 / RATE_PER_MIN + 0.1
CACHE = "coinglass_hist.jsonl"
EXCHANGE = "Binance"
PROBE_COINS = ("BTC", "ETH", "SOL")

# (이름, 저장 키, 경로 후보들)
#
# 경로 표기가 문서 판마다 갈린다. 후보를 순서대로 찔러 되는 것을 쓴다.
ENDPOINTS = [
    ("미결제약정", "oi", [
        "/api/futures/open-interest/history",
        "/api/futures/openInterest/ohlc-history",
    ]),
    ("미결제약정(전거래소 합산)", "oi_agg", [
        "/api/futures/open-interest/aggregated-history",
        "/api/futures/openInterest/aggregated-history",
    ]),
    ("펀딩비", "funding", [
        "/api/futures/funding-rate/history",
        "/api/futures/fundingRate/ohlc-history",
    ]),
    ("롱숏 계정비", "ls_ratio", [
        "/api/futures/global-long-short-account-ratio/history",
        "/api/futures/globalLongShortAccountRatio/history",
        "/api/futures/long-short-ratio/history",
    ]),
    ("상위 트레이더 롱숏", "top_ls", [
        "/api/futures/top-long-short-account-ratio/history",
        "/api/futures/top-long-short-position-ratio/history",
    ]),
    ("테이커 매수/매도", "taker", [
        "/api/futures/taker-buy-sell-volume/history",
        "/api/futures/aggregated-taker-buy-sell-volume/history",
    ]),
    ("청산", "liq", [
        "/api/futures/liquidation/history",
        "/api/futures/liquidation/aggregated-history",
    ]),
]


def w(text, width, right=False):
    """한글은 두 칸을 먹는다. 그걸 세어 맞춘다 — 안 그러면 표가 어긋난다."""
    import unicodedata
    n = sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in str(text))
    pad = " " * max(0, width - n)
    return (pad + str(text)) if right else (str(text) + pad)


def set_rate(argv):
    """--rate 80 처럼 등급에 맞춰 올릴 수 있다. 기본은 제일 낮은 등급 기준."""
    global RATE_PER_MIN, RATE_SLEEP
    if "--rate" in argv:
        i = argv.index("--rate")
        if i + 1 < len(argv) and argv[i + 1].isdigit():
            RATE_PER_MIN = max(1, int(argv[i + 1]))
            RATE_SLEEP = 60.0 / RATE_PER_MIN + 0.1


KEY_FILE = "coinglass_key.txt"


def api_key(argv):
    """키를 찾는 순서: --key > 환경변수 > coinglass_key.txt

    환경변수는 cmd 창을 닫으면 사라져서 헷갈리기 쉽다. 메모장으로
    파일 하나 만들어 두는 쪽이 실수가 적다.
    """
    if "--key" in argv:
        i = argv.index("--key")
        if i + 1 < len(argv):
            return argv[i + 1].strip()

    env = (os.environ.get("COINGLASS_API_KEY") or "").strip()
    if env:
        return env

    for cand in (os.path.join(os.getcwd(), KEY_FILE),
                 os.path.join(os.path.dirname(os.path.abspath(__file__)), KEY_FILE)):
        if os.path.exists(cand):
            try:
                with open(cand, encoding="utf-8-sig") as fp:   # 메모장 BOM 대비
                    for line in fp:
                        line = line.strip().strip('"').strip("'")
                        if line and not line.startswith("#"):
                            return line
            except Exception as e:
                print(f"  {KEY_FILE} 을 못 읽었습니다: {e}")
    return ""


_last_call = [0.0]


def _throttle():
    """호출 **하나하나** 사이에 간격을 둔다.

    예전엔 페이지 사이에만 쉬어서, 엔드포인트가 바뀌거나 코인이
    바뀔 때는 그냥 연달아 때렸다. 그러면 429 가 나고, 그 뒤 요청이
    전부 조용히 실패해 '+0' 으로만 보인다 — BTC 만 받히고 나머지
    29종이 0건이던 원인이 이것이다.
    """
    wait = RATE_SLEEP - (time.time() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    _last_call[0] = time.time()


def call(path, key, params, timeout=20, retries=3):
    """(성공?, 데이터 또는 오류문구, HTTP 상태)

    429 는 '없다'가 아니라 '지금은 말고'다. 기다렸다 다시 묻는다.
    """
    url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={
        "CG-API-KEY": key,
        "accept": "application/json",
    })
    for attempt in range(retries):
        _throttle()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = json.loads(r.read().decode("utf-8"))
                status = r.status
            break
        except urllib.error.HTTPError as e:
            try:
                body = json.loads(e.read().decode("utf-8"))
            except Exception:
                body = {"msg": str(e)}
            msg = body.get("msg") or body.get("message") or str(e)
            if e.code == 429 and attempt < retries - 1:
                time.sleep(RATE_SLEEP * (2 ** (attempt + 1)))
                continue
            return False, msg, e.code
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(RATE_SLEEP)
                continue
            return False, f"{type(e).__name__}: {e}", 0

    # code 는 "0" 또는 0 이 성공
    code = str(body.get("code", "")).strip()
    if code not in ("0", "00000", ""):
        return False, body.get("msg") or f"code={code}", status
    data = body.get("data")
    if not isinstance(data, list):
        return False, f"data 가 리스트가 아닙니다 ({type(data).__name__})", status
    return True, data, status


def _looks_empty(r):
    """code 0 인데 빈 리스트 — 파라미터가 그 엔드포인트와 안 맞는 경우다.

    ✅ 로 세면 '되는 줄 알았는데 0건'이 된다. oi_agg 가 딱 이랬다:
    전거래소 합산인데 exchange 를 같이 보내서 빈 리스트가 왔고,
    그게 성공으로 찍혀 결제 판단을 흐렸다.
    """
    return "error" not in r and r.get("rows", 0) == 0


def _param_sets(coin):
    """같은 엔드포인트라도 문서 판마다 받는 파라미터가 다르다.

    합산(aggregated) 계열은 exchange 를 주면 오히려 빈 리스트가 온다.
    그래서 넓은 것부터 좁은 것까지 돌려 본다.
    """
    return (
        {"exchange": EXCHANGE, "symbol": f"{coin}USDT", "interval": "1d", "limit": 1000},
        {"symbol": f"{coin}USDT", "interval": "1d", "limit": 1000},
        {"symbol": coin, "interval": "1d", "limit": 1000},
        {"exchange_list": EXCHANGE, "symbol": coin, "interval": "1d", "limit": 1000},
    )


def probe_one(name, key_name, paths, key, coin):
    """경로·파라미터 후보를 돌려 **실제로 봉이 오는** 조합을 찾는다.

    예전엔 첫 성공에서 멈췄다. 그런데 '성공인데 0봉'이 첫 후보로
    걸리면 거기서 멈춰 버려서, 뒤에 있는 되는 조합을 못 봤다.
    이제는 0봉이면 예비로만 잡아 두고 계속 찾는다.
    """
    last = (paths[0] if paths else "?", 0, "후보 없음")
    spare = None
    for path in paths:
        for params in _param_sets(coin):
            ok, data, status = call(path, key, params)
            if ok:
                r = {"path": path, "params": params, "rows": len(data),
                     "data": data, "status": status}
                if data:
                    return r
                if spare is None:
                    spare = r
                continue
            last = (path, status, data)
    if spare is not None:
        return spare
    return {"error": last[2], "status": last[1], "path": last[0]}


def oldest(data):
    ts = [d.get("time") or d.get("timestamp") or d.get("t") for d in data]
    ts = [int(t) for t in ts if t]
    if not ts:
        return None
    import datetime
    return datetime.datetime.fromtimestamp(
        min(ts) / 1000, datetime.timezone.utc).strftime("%Y-%m-%d")


def cmd_probe(key):
    print("=" * 74)
    print("  CoinGlass 확인 — 무엇이 오고 몇 년치가 오나 (결제 전 점검)")
    print("=" * 74)
    print(f"  분당 {RATE_PER_MIN}회 기준으로 천천히 부릅니다 (요청 간 {RATE_SLEEP:.1f}초)")
    print("  등급이 높으면  --rate 80  처럼 올리십시오. 1~2분 걸립니다.")

    ok, data, status = call("/api/futures/supported-coins", key, {})
    if not ok:
        print(f"\n  ❌ 키가 동작하지 않습니다 (HTTP {status}): {data}")
        print("\n  확인할 것")
        print("    · 키를 그대로 붙였는지 (앞뒤 공백 없이)")
        print("    · CoinGlass 계정에서 API 키가 활성 상태인지")
        return 1
    print(f"\n  ✅ 키 정상 · 지원 코인 {len(data)}종\n")

    print("  " + w("항목", 26) + w("상태", 10) + w("받은 봉", 9, True)
          + "  " + w("가장 오래된 날", 16) + "경로")
    print("  " + "─" * 76)

    usable, empty = [], []
    for name, kname, paths in ENDPOINTS:
        r = probe_one(name, kname, paths, key, PROBE_COINS[0])
        if "error" in r:
            print("  " + w(name, 26) + w(f"❌ {r['status']}", 10) + w("—", 9, True)
                  + "  " + w("—", 16) + r["path"])
            continue
        if _looks_empty(r):
            # 200 인데 0봉. ✅ 로 찍으면 '되는 줄 알았는데 0건'이 된다.
            print("  " + w(name, 26) + w("⚠️ 0봉", 10) + w(0, 9, True)
                  + "  " + w("—", 16) + r["path"])
            empty.append(name)
            continue
        old = oldest(r["data"]) or "?"
        print("  " + w(name, 26) + w("✅", 10) + w(r["rows"], 9, True)
              + "  " + w(old, 16) + r["path"])
        usable.append((name, kname, r))

    print("  " + "─" * 76)
    if empty:
        print(f"\n  ⚠️ 응답은 왔는데 봉이 0개인 항목: {', '.join(empty)}")
        print("     경로는 맞지만 파라미터가 안 맞거나, 그 등급에서 빈 값을 줍니다.")
        print("     이 항목은 --fetch 해도 아무것도 안 쌓입니다. 없는 셈 치십시오.")
    if not usable:
        print("\n  되는 항목이 없습니다. 등급을 올려야 하거나 경로가 바뀌었습니다.")
        return 1

    print(f"\n  쓸 수 있는 항목 {len(usable)}개")
    print("""
  '받은 봉' 이 1000이면 한 번에 주는 최대치라 더 있을 수 있습니다.
  --fetch 는 페이지를 넘겨 끝까지 받습니다.

  판단 기준
    · 미결제약정·펀딩이 ✅ 이고 가장 오래된 날이 2~3년 전이면 충분합니다.
      그 한 달로 재고 끊어도 됩니다.
    · 둘 다 ❌ 면 그 등급으로는 우리가 하려는 걸 못 합니다.

  받으려면:  python coinglass_probe.py --fetch""")
    return 0


def fetch_series(path, params, key, coin, kname, oldest_ms):
    """end_time 을 뒤로 밀며 끝까지 받는다."""
    rows, end, guard, seen = [], None, 0, set()
    while guard < 40:
        guard += 1
        p = dict(params)
        if end:
            p["end_time"] = end
        ok, data, status = call(path, key, p)
        if not ok:
            # 조용히 0건으로 끝내면 '데이터가 없다'와 구분이 안 된다.
            return rows, f"{status} {data}"
        if not data:
            break
        got = 0
        oldest_ts = None
        for d in data:
            ts = d.get("time") or d.get("timestamp") or d.get("t")
            if ts is None:
                continue
            ts = int(ts)
            oldest_ts = ts if oldest_ts is None else min(oldest_ts, ts)
            if ts in seen:
                continue
            seen.add(ts)
            # 응답 레코드를 **통째로** 저장한다.
            #
            # 구독은 한 달이고 그 뒤엔 키가 죽는다. 지금 close 만 뽑아
            # 두면 나중에 high/low 나 다른 칼럼이 필요해질 때 다시
            # 결제해야 한다. 지금은 다 받아 두고 쓸 건 나중에 고른다.
            rows.append({"coin": coin, "kind": kname, "ts": ts, "raw": d})
            got += 1
        if got == 0 or oldest_ts is None:
            break
        if oldest_ms and oldest_ts <= oldest_ms:
            break
        end = oldest_ts - 1
    return rows, None


def cmd_fetch(key, coins):
    ok, data, status = call("/api/futures/supported-coins", key, {})
    if not ok:
        print(f"  ❌ 키가 동작하지 않습니다 (HTTP {status}): {data}")
        return 1

    print(f"  경로를 먼저 확인합니다 ({PROBE_COINS[0]} 기준)...")
    live = []
    for name, kname, paths in ENDPOINTS:
        r = probe_one(name, kname, paths, key, PROBE_COINS[0])
        if "error" in r:
            print(f"    ❌ {name}  ({r['status']})")
            continue
        if _looks_empty(r):
            # 0봉짜리를 목록에 넣으면 30종 × 여러 페이지를 헛돈다.
            print(f"    ⚠️ {name} — 응답은 오는데 0봉이라 건너뜁니다")
            continue
        live.append((name, kname, r["path"], r["params"]))
        print(f"    ✅ {name}")
    if not live:
        print("  되는 항목이 없습니다.")
        return 1

    have = set()
    if os.path.exists(CACHE):
        with open(CACHE, encoding="utf-8") as fp:
            for line in fp:
                try:
                    r = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                have.add((r["coin"], r["kind"], int(r["ts"])))

    total = failed = 0
    with open(CACHE, "a", encoding="utf-8", newline="") as fp:
        for coin in coins:
            for name, kname, path, base_params in live:
                params = dict(base_params)
                if "symbol" in params:
                    params["symbol"] = (f"{coin}USDT" if params["symbol"].endswith("USDT")
                                        else coin)
                rows, why = fetch_series(path, params, key, coin, kname, None)
                fresh = [r for r in rows if (r["coin"], r["kind"], r["ts"]) not in have]
                for r in fresh:
                    fp.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
                    have.add((r["coin"], r["kind"], r["ts"]))
                fp.flush()
                total += len(fresh)
                # +0 은 세 가지 뜻이 있다: 이미 받았다 / 서버가 거절했다 /
                # 정말 없다. 구분해서 찍지 않으면 원인을 못 찾는다.
                tail = ""
                if why:
                    tail = f"   ⚠️ {why}"
                    failed += 1
                elif not fresh and rows:
                    tail = "   (이미 받음)"
                elif not fresh:
                    tail = "   (데이터 없음)"
                print(f"    {coin:<8}{w(name, 24)}{len(fresh):>7}{tail}")
    print(f"\n  {total}건 저장 → {CACHE}")
    if failed:
        print(f"  ⚠️ 요청 {failed}건이 거절당했습니다. 위 사유를 보십시오.")
        print("     429 가 많으면  --rate 를 낮추고,  403/40x 면 등급 문제입니다.")
        print("     --fetch 를 다시 돌리면 못 받은 것만 이어받습니다.")
    return 0


def cmd_coverage():
    """받은 게 온전한지 — 구독을 끊기 전에 반드시 확인할 것."""
    if not os.path.exists(CACHE):
        print(f"  {CACHE} 이 없습니다. python coinglass_probe.py --fetch")
        return 1
    import datetime
    agg = {}
    bad = 0
    with open(CACHE, encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                bad += 1
                continue
            k = (r["kind"], r["coin"])
            lo, hi, n = agg.get(k, (r["ts"], r["ts"], 0))
            agg[k] = (min(lo, r["ts"]), max(hi, r["ts"]), n + 1)

    def day(ms):
        return datetime.datetime.fromtimestamp(
            ms / 1000, datetime.timezone.utc).strftime("%Y-%m-%d")

    print("=" * 74)
    print("  받은 것 확인 — 구독 끊기 전에 여기가 채워졌는지 보십시오")
    print("=" * 74)
    kinds = sorted({k for k, _ in agg})
    for kind in kinds:
        rows = [(c, v) for (kd, c), v in agg.items() if kd == kind]
        rows.sort()
        spans = [(hi - lo) / 86_400_000 for _, (lo, hi, _) in rows]
        total = sum(n for _, (_, _, n) in rows)
        print(f"\n  [{kind}]  {len(rows)}종 · {total:,}줄")
        print(f"    기간 중앙값 {sorted(spans)[len(spans)//2]:.0f}일"
              f" (최소 {min(spans):.0f} · 최대 {max(spans):.0f})")
        short = [c for c, (lo, hi, _) in rows if (hi - lo) / 86_400_000 < 400]
        if short:
            print(f"    ⚠️ 400일 미만 {len(short)}종: {', '.join(short[:10])}")
        c0, (lo0, hi0, n0) = rows[0]
        print(f"    예) {c0}  {day(lo0)} ~ {day(hi0)} · {n0}줄")
    if bad:
        print(f"\n  ⚠️ 읽을 수 없는 줄 {bad}개")
    print("\n" + "=" * 74)
    print("""  다 받았으면 구독을 끊어도 됩니다. 이 파일은 남습니다.

  끊기 전 점검
    · 쓰려는 항목(미결제약정·펀딩)이 위에 있는가
    · 종목 수가 30에 가까운가
    · 기간이 2년 이상인가
  하나라도 아니면 --fetch 를 한 번 더 돌리십시오 (없는 것만 이어받습니다).""")
    return 0


def pick_coins(n=30):
    for mod_name, var in (("spotlight", "SCAN_COINS"), ("config", "SCAN_COINS"),
                          ("config", "COINS")):
        try:
            v = getattr(__import__(mod_name), var, None)
            if isinstance(v, (list, tuple)) and v:
                return [c.replace("/USDT", "") for c in v][:n]
        except Exception:
            pass
    return list(PROBE_COINS)


def main(argv):
    for cand in (os.getcwd(), os.path.dirname(os.path.abspath(__file__))):
        if cand not in sys.path:
            sys.path.insert(0, cand)
    try:
        import console
        console.enable_utf8()
    except Exception:
        pass

    set_rate(argv)

    # --coverage 는 이미 받아 둔 파일만 읽는다. 구독을 끊은 뒤에도
    # 확인할 수 있어야 하므로 키를 요구하지 않는다.
    if "--coverage" in argv:
        return cmd_coverage()

    key = api_key(argv)
    if not key:
        print(f"""API 키가 없습니다. 셋 중 아무 방법이나 쓰면 됩니다.

  [방법 1 — 제일 쉬움] 파일에 넣기
      메모장을 열고 키만 한 줄 붙여넣은 뒤,
      이 폴더에 {KEY_FILE} 이라는 이름으로 저장하십시오.
          {os.path.join(os.getcwd(), KEY_FILE)}
      그리고 그냥:  python coinglass_probe.py

      ※ 메모장 '다른 이름으로 저장' 에서 파일 형식을 '모든 파일'로
        바꾸십시오. 안 그러면 확장자가 한 번 더 붙습니다.
      ※ 이 파일은 남에게 보내지 마십시오. 키가 그대로 들어 있습니다.

  [방법 2] 명령에 직접 붙이기 (한 번만 쓸 때)
      python coinglass_probe.py --key 여기에키

  [방법 3] 환경변수 (지금 열려 있는 cmd 창에서만 유효)
      set COINGLASS_API_KEY=여기에키
      python coinglass_probe.py""")
        return 1

    if "--fetch" in argv:
        print("=" * 74)
        print("  CoinGlass 일봉 이력 받기")
        print("=" * 74)
        print(f"  분당 {RATE_PER_MIN}회 기준 · 요청 간 {RATE_SLEEP:.1f}초")
        print("  30종이면 20~40분쯤 걸립니다. 중간에 끊겨도 받은 것은 남습니다.")
        return cmd_fetch(key, pick_coins())
    return cmd_probe(key)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
