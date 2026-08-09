"""coinglass_probe.py — 돈 내기 전에 무엇이 오는지 확인한다

    set COINGLASS_API_KEY=발급받은키          (윈도우 cmd)
    python coinglass_probe.py                 # 무엇이 되고 몇 년치가 오나
    python coinglass_probe.py --fetch         # 되는 것만 일봉으로 받아 캐시

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


def api_key(argv):
    if "--key" in argv:
        i = argv.index("--key")
        if i + 1 < len(argv):
            return argv[i + 1].strip()
    return (os.environ.get("COINGLASS_API_KEY") or "").strip()


def call(path, key, params, timeout=20):
    """(성공?, 데이터 또는 오류문구, HTTP 상태)"""
    url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={
        "CG-API-KEY": key,
        "accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read().decode("utf-8"))
            status = r.status
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8"))
        except Exception:
            body = {"msg": str(e)}
        return False, body.get("msg") or body.get("message") or str(e), e.code
    except Exception as e:
        return False, f"{type(e).__name__}: {e}", 0

    # code 는 "0" 또는 0 이 성공
    code = str(body.get("code", "")).strip()
    if code not in ("0", "00000", ""):
        return False, body.get("msg") or f"code={code}", status
    data = body.get("data")
    if not isinstance(data, list):
        return False, f"data 가 리스트가 아닙니다 ({type(data).__name__})", status
    return True, data, status


def probe_one(name, key_name, paths, key, coin):
    """경로 후보를 돌려 첫 성공을 돌려준다."""
    for path in paths:
        for params in (
            {"exchange": EXCHANGE, "symbol": f"{coin}USDT", "interval": "1d", "limit": 1000},
            {"symbol": coin, "interval": "1d", "limit": 1000},
        ):
            ok, data, status = call(path, key, params)
            if ok:
                return {"path": path, "params": params, "rows": len(data),
                        "data": data, "status": status}
            last = (path, status, data)
            time.sleep(0.35)
    return {"error": last[2], "status": last[1], "path": last[0]}


def oldest(data):
    ts = [d.get("time") or d.get("timestamp") or d.get("t") for d in data]
    ts = [int(t) for t in ts if t]
    if not ts:
        return None
    import datetime
    return datetime.datetime.utcfromtimestamp(min(ts) / 1000).strftime("%Y-%m-%d")


def cmd_probe(key):
    print("=" * 74)
    print("  CoinGlass 확인 — 무엇이 오고 몇 년치가 오나 (결제 전 점검)")
    print("=" * 74)

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

    usable = []
    for name, kname, paths in ENDPOINTS:
        r = probe_one(name, kname, paths, key, PROBE_COINS[0])
        if "error" in r:
            print("  " + w(name, 26) + w(f"❌ {r['status']}", 10) + w("—", 9, True)
                  + "  " + w("—", 16) + r["path"])
            continue
        old = oldest(r["data"]) or "?"
        print("  " + w(name, 26) + w("✅", 10) + w(r["rows"], 9, True)
              + "  " + w(old, 16) + r["path"])
        usable.append((name, kname, r))

    print("  " + "─" * 76)
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
        if not ok or not data:
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
            rows.append({"coin": coin, "kind": kname, "ts": ts,
                         "v": d.get("close", d.get("value", d.get("longShortRatio")))})
            got += 1
        if got == 0 or oldest_ts is None:
            break
        if oldest_ms and oldest_ts <= oldest_ms:
            break
        end = oldest_ts - 1
        time.sleep(0.35)
    return rows


def cmd_fetch(key, coins):
    ok, data, status = call("/api/futures/supported-coins", key, {})
    if not ok:
        print(f"  ❌ 키가 동작하지 않습니다 (HTTP {status}): {data}")
        return 1

    print(f"  경로를 먼저 확인합니다 ({PROBE_COINS[0]} 기준)...")
    live = []
    for name, kname, paths in ENDPOINTS:
        r = probe_one(name, kname, paths, key, PROBE_COINS[0])
        if "error" not in r:
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

    total = 0
    with open(CACHE, "a", encoding="utf-8", newline="") as fp:
        for coin in coins:
            for name, kname, path, base_params in live:
                params = dict(base_params)
                if "symbol" in params:
                    params["symbol"] = (f"{coin}USDT" if params["symbol"].endswith("USDT")
                                        else coin)
                rows = fetch_series(path, params, key, coin, kname, None)
                fresh = [r for r in rows if (r["coin"], r["kind"], r["ts"]) not in have]
                for r in fresh:
                    fp.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
                    have.add((r["coin"], r["kind"], r["ts"]))
                fp.flush()
                total += len(fresh)
                print(f"    {coin:<8}{name:<24} +{len(fresh)}")
    print(f"\n  {total}건 저장 → {CACHE}")
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

    key = api_key(argv)
    if not key:
        print("""API 키가 없습니다.

  1. https://www.coinglass.com  가입 → API 키 발급
  2. 윈도우 cmd 에서:
         set COINGLASS_API_KEY=발급받은키
         python coinglass_probe.py

     또는 한 번만 쓰려면:
         python coinglass_probe.py --key 발급받은키""")
        return 1

    if "--fetch" in argv:
        print("=" * 74)
        print("  CoinGlass 일봉 이력 받기")
        print("=" * 74)
        return cmd_fetch(key, pick_coins())
    return cmd_probe(key)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
