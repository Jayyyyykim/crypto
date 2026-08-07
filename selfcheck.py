"""
selfcheck.py — 봇 폴더에 제대로 붙었는지 확인

봇 폴더에 복사한 뒤 여기서 한 번 돌린다.

    python selfcheck.py

네트워크도 API 키도 pandas도 필요 없다. 합성 데이터로 각 모듈의 주요
경로를 실제로 한 번씩 태워 보고, 되는지 안 되는지만 말한다.

무엇을 잡나
──────────
1. 파일이 다 왔나 (12개)
2. import가 되나 — console.py를 빠뜨렸으면 여기서 잡힌다
3. **이름 충돌** — 봇에 이미 같은 이름의 파일이 있으면 그쪽이 import되고
   우리 모듈은 조용히 안 쓰인다. 예상 함수가 있는지로 가려낸다.
4. 실제로 도나 — 모듈마다 대표 함수를 합성 데이터로 호출해 본다
5. 파이썬 버전, 콘솔 인코딩 (윈도우)

이 파일 자체는 봇 동작에 필요 없다. 확인용이라 나중에 지워도 된다.
"""

import os
import sys
import tempfile
import traceback

# 있어야 하는 파일과, 그 모듈에 있어야 하는 이름
# (이름 충돌로 엉뚱한 모듈이 잡혔는지 가리는 지문 역할)
EXPECTED = {
    "console":        ["say", "safe", "enable_utf8"],
    "jsonstore":      ["load", "save"],
    "level_map":      ["build_level_map", "find_pivots", "cluster_levels"],
    "expected_range": ["expected_range", "coverage", "check_trade_levels"],
    "regime_gate":    ["scan_universe", "gate", "find_overlap"],
    "setup_ledger":   ["backfill_universe", "stats", "capture", "score_pending"],
    "call_journal":   ["register_criteria", "publish", "verify"],
    "six_bar_align":  ["scan_universe", "trend_series", "leaderboard"],
    "risk_calc":      ["plan", "scaled_plan", "size_from_edge"],
    "grid_lines":     ["analyse", "fit_score", "best_basis"],
    "kimchi_band":    ["premium", "record", "band"],
    "healthcheck":    ["run", "get_report", "verdict"],
}

PASS, FAIL, WARN = "PASS", "FAIL", "WARN"


class FakeDF(dict):
    """봇의 get_ohlcv()가 주는 DataFrame 흉내.

    모듈들이 실제로 쓰는 건 df["high"] 같은 열 접근과 len(df)뿐이라
    pandas 없이도 검사할 수 있다.
    """

    def __len__(self):
        return len(self.get("close", []))


def make_df(n=260, start=100.0):
    """결정적 지그재그 — 난수 없이 매번 같은 결과."""
    from datetime import datetime, timedelta
    o = h = l = c = start
    ts, hi, lo, cl = [], [], [], []
    t0 = datetime(2026, 1, 1)
    px = start
    for i in range(n):
        # 완만한 상승 + 주기적 되돌림 (피벗이 생기도록)
        px = px * (1.012 if (i // 7) % 3 != 2 else 0.985)
        ts.append(t0 + timedelta(days=i))
        hi.append(px * 1.01)
        lo.append(px * 0.99)
        cl.append(px)
    return FakeDF(timestamp=ts, open=cl, high=hi, low=lo, close=cl,
                  volume=[1000.0] * n)


def _get_ohlcv(symbol, tf, limit=None):
    return make_df()


# ================================
# 개별 검사
# ================================

def check_files(folder):
    missing = [f"{m}.py" for m in EXPECTED
               if not os.path.exists(os.path.join(folder, f"{m}.py"))]
    if missing:
        return FAIL, f"파일이 없습니다: {', '.join(missing)}"
    return PASS, f"{len(EXPECTED)}개 파일 모두 있음"


def check_imports(folder):
    problems = []
    loaded = {}
    for name, attrs in EXPECTED.items():
        try:
            mod = __import__(name)
        except Exception as e:
            problems.append(f"{name}: import 실패 — {type(e).__name__}: {e}")
            continue

        # 이름 충돌 검사 — 봇에 이미 있던 파일이 잡혔는지
        where = os.path.dirname(os.path.abspath(getattr(mod, "__file__", "")))
        if os.path.abspath(where) != os.path.abspath(folder):
            problems.append(f"{name}: 다른 위치의 모듈이 잡혔습니다 → {mod.__file__}")
            continue

        missing = [a for a in attrs if not hasattr(mod, a)]
        if missing:
            problems.append(
                f"{name}: 같은 이름의 다른 파일일 수 있습니다 "
                f"(없는 함수: {', '.join(missing)})")
            continue

        loaded[name] = mod

    if problems:
        return FAIL, "\n      ".join(problems), loaded
    return PASS, f"{len(loaded)}개 모듈 정상 import", loaded


def check_python():
    v = sys.version_info
    if v < (3, 7):
        return FAIL, f"파이썬 {v.major}.{v.minor} — 3.7 이상이 필요합니다"
    return PASS, f"파이썬 {v.major}.{v.minor}.{v.micro}"


def check_encoding(mods):
    enc = (getattr(sys.stdout, "encoding", "") or "").lower().replace("-", "")
    if enc == "utf8":
        return PASS, f"출력 인코딩 {sys.stdout.encoding}"
    return WARN, (
        f"출력 인코딩이 {sys.stdout.encoding or '알 수 없음'}입니다. "
        f"라이브러리 출력은 안전하지만, 직접 print(get_report(...))를 쓰려면\n"
        f"      봇 진입점에 console.enable_utf8()를 넣으십시오.")


# ---- 기능 검사 --------------------------------------------------------

def probe_level_map(m, tmp):
    lm = m["level_map"].build_level_map(make_df())
    assert lm and lm["supports"] is not None, "레벨을 못 만들었습니다"
    return f"레벨 {len(lm['supports'])}지지 / {len(lm['resistances'])}저항"


def probe_expected_range(m, tmp):
    er = m["expected_range"].expected_range(make_df())
    assert er and er["upper"] > er["lower"], "예상 범위가 비었습니다"
    w = m["expected_range"].check_trade_levels(
        er, er["base"], er["base"] * 0.999, er["base"] * 1.5)
    return f"폭 {er['width_pct']*100:.1f}%, 경고 {len(w)}건"


def probe_grid_lines(m, tmp):
    g = m["grid_lines"].best_basis(make_df())
    return f"적합도 {g['fit']}" if g else "격자 없음 (정상 — 적합도 미달)"


def probe_six_bar(m, tmp):
    rows = m["six_bar_align"].scan_universe(["A/USDT"], _get_ohlcv)
    s = m["six_bar_align"].summarize(rows)
    return f"{s['watched']}종 판정, 칸 {s['cells']}"


def probe_regime_gate(m, tmp):
    def analyze(symbol, tf):
        return {"trend_code": "up"}
    scan = m["regime_gate"].scan_universe(["A/USDT", "B/USDT"], analyze)
    ok, _ = m["regime_gate"].gate("long", scan)
    return f"환경 '{scan['label']}', 롱 게이트 {'열림' if ok else '닫힘'}"


def probe_setup_ledger(m, tmp):
    sl = m["setup_ledger"]
    p = os.path.join(tmp, "led.json")
    sl.backfill_universe(["A/USDT"], _get_ohlcv, timeframe="1d", path=p)
    data = sl._load(p)
    st = sl.stats(3, path=p, min_samples=1)
    assert data["events"], "사건이 하나도 안 쌓였습니다"
    # 기준선 멱등성 — 두 번 돌려도 표본이 안 늘어야 한다
    before = dict(data["baseline"].get("3", {}))
    sl.backfill_universe(["A/USDT"], _get_ohlcv, timeframe="1d", path=p)
    after = sl._load(p)["baseline"].get("3", {})
    assert before == after, "재실행에 기준선이 늘었습니다 (예전 버전입니다)"
    return f"사건 {len(data['events'])}건, 셋업 {len(st['rows'])}종, 기준선 멱등 ✓"


def probe_call_journal(m, tmp):
    cj = m["call_journal"]
    p = os.path.join(tmp, "j.jsonl")
    cj.register_criteria({"보합_밴드": 0.01}, path=p)
    e = cj.publish("BTC/USDT", 1, 100.0, "자가진단", ["디스카운트 구간에서"], 95.0, path=p)
    cj.record_score(e["seq"], 3, "적중", 110.0, path=p)
    ok, msg = cj.verify(p)
    assert ok, f"사슬 검증 실패: {msg}"

    # 변조가 실제로 잡히는지
    with open(p, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()
    lines[1] = lines[1].replace('"발행가": 100.0', '"발행가": 999.0')
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write("\n".join(lines) + "\n")
    broken, _ = cj.verify(p)
    assert not broken, "변조를 못 잡았습니다"
    return "사슬 정상 + 변조 탐지 ✓"


def probe_risk_calc(m, tmp):
    rc = m["risk_calc"]
    p = rc.plan(10_000_000, 100.0, 95.0, 110.0, risk_pct=1.0)
    assert p and abs(p["risk_amount"] - 100_000) < 1, "포지션 계산이 이상합니다"
    # 우위 미확인이면 기대값을 내지 않아야 한다
    u = rc.size_from_edge(1e7, 100.0, 95.0, 112.0,
                          {"hit_rate": 0.82, "edge": -0.006, "decided": 326})
    assert u["expectancy_r"] is None, "우위 미확인인데 기대값을 냈습니다 (예전 버전입니다)"
    return f"손익비 {p['rr']:.2f}R, 본전 {p['breakeven_rate']*100:.0f}%, 우위 게이트 ✓"


def probe_kimchi_band(m, tmp):
    kb = m["kimchi_band"]
    p = os.path.join(tmp, "k.json")
    assert abs(kb.premium(1_414_000, 1000, 1400) - 1.0) < 1e-6, "김프 계산 오류"
    for i in range(12):
        kb.record(float(i), date=f"2026-08-{i+1:02d}", path=p)
    b = kb.band(current=5.0, window_days=3650, path=p)
    assert b, "밴드를 못 만들었습니다"
    return f"위치 {b['position']*100:.0f}% ({b['zone']})"


def probe_healthcheck(m, tmp):
    hc = m["healthcheck"]
    checks, real = hc.run(3,
                          journal_path=os.path.join(tmp, "hj.jsonl"),
                          ledger_path=os.path.join(tmp, "hl.json"))
    level, _ = hc.verdict(checks)
    return f"항목 {len(checks)}개 판정, 종합 {level}"


def _explain(exc):
    """검사가 왜 실패했는지 한두 줄로.

    traceback의 마지막 줄을 그냥 집으면 파이썬 3.11이 붙이는 '^^^^' 표시가
    잡혀서 아무 정보가 없다. 실패한 코드 줄과 위치를 직접 골라 쓴다.
    """
    if isinstance(exc, AssertionError) and str(exc):
        head = str(exc)          # 우리가 적어 둔 메시지가 이미 설명이다
    else:
        head = f"{type(exc).__name__}: {exc}"

    frames = traceback.extract_tb(exc.__traceback__)
    if frames:
        f = frames[-1]
        where = f"{os.path.basename(f.filename)}:{f.lineno}"
        line = (f.line or "").strip()
        return f"{head}\n      ({where}  {line})" if line else f"{head}  ({where})"
    return head


PROBES = [
    ("level_map", probe_level_map),
    ("expected_range", probe_expected_range),
    ("grid_lines", probe_grid_lines),
    ("six_bar_align", probe_six_bar),
    ("regime_gate", probe_regime_gate),
    ("setup_ledger", probe_setup_ledger),
    ("call_journal", probe_call_journal),
    ("risk_calc", probe_risk_calc),
    ("kimchi_band", probe_kimchi_band),
    ("healthcheck", probe_healthcheck),
]


# ================================
# 실행
# ================================

def main():
    folder = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, folder)

    results = []

    def add(level, title, detail):
        results.append((level, title, detail))
        mark = {PASS: "  OK ", FAIL: " FAIL", WARN: " WARN"}[level]
        print(f"[{mark}] {title}")
        if detail:
            print(f"      {detail}")

    print("=" * 66)
    print("  봇 폴더 자가진단")
    print(f"  {folder}")
    print("=" * 66)

    lvl, msg = check_python()
    add(lvl, "파이썬 버전", msg)

    lvl, msg = check_files(folder)
    add(lvl, "파일 존재", msg)
    if lvl == FAIL:
        return summarise(results)

    lvl, msg, mods = check_imports(folder)
    add(lvl, "모듈 import · 이름 충돌", msg)
    if lvl == FAIL:
        return summarise(results)

    lvl, msg = check_encoding(mods)
    add(lvl, "출력 인코딩", msg)

    print("-" * 66)

    tmp = tempfile.mkdtemp(prefix="selfcheck_")
    # 진단 중 라이브러리 로그가 섞이지 않게 조용히 시킨다
    quiet = mods["console"].say
    mods["console"].say = lambda *a, **k: None
    try:
        for name, probe in PROBES:
            try:
                add(PASS, name, probe(mods, tmp))
            except Exception as e:
                add(FAIL, name, _explain(e))
    finally:
        mods["console"].say = quiet

    return summarise(results)


def summarise(results):
    fails = [r for r in results if r[0] == FAIL]
    warns = [r for r in results if r[0] == WARN]
    print("=" * 66)
    if fails:
        print(f"  실패 {len(fails)}건 — 아래를 먼저 고치십시오")
        for _, title, detail in fails:
            print(f"    · {title}: {detail.splitlines()[0]}")
        print()
        print("  가장 흔한 원인")
        print("    1. console.py를 빠뜨렸다 (다른 모듈 대부분이 씁니다)")
        print("    2. 봇에 같은 이름의 파일이 이미 있어 그쪽이 잡혔다")
        print("    3. 예전 버전 파일이 섞였다 — 12개를 한 번에 다시 복사하십시오")
        return 1

    print(f"  전부 통과{f' (경고 {len(warns)}건)' if warns else ''} — 설치가 끝났습니다.")
    print()
    print("  다음 순서")
    print("    1. call_journal.register_criteria({...})   ← 제일 먼저, 딱 한 번")
    print("    2. setup_ledger.backfill_universe(...)     ← 표본 만들기")
    print("    3. healthcheck.get_report()                ← 무엇을 믿어도 되는지")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
