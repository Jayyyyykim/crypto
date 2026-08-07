"""
healthcheck.py — "지금 이 숫자를 근거로 써도 되나"를 한 화면에서

이 저장소에서 지금까지 찾은 버그는 전부 **조용히 틀린 숫자**였다.
예외도 안 나고 표도 그럴듯하게 그려지는데 숫자의 뜻이 달라지는 것들이다.

  · 지지·저항이 3.6% 폭을 레벨 하나로 뭉쳐 놓고 "터치 8회"라고 적었다.
  · 숏 손절을 하단 여유와 비교해 경고 근거가 반대였다.
  · "표본 45건"이라 적어 놓고 실제로는 판정 5건으로 낸 100%였다.
  · 소급 채점을 두 번 돌렸더니 기준선 표본이 두 배가 됐다.

넷 다 고쳤고 회귀 테스트로 못 박았다. 그런데 이런 건 또 들어온다.
그래서 **결론을 내기 전에 전제를 점검하는 화면**을 따로 둔다.

무엇을 보나
──────────
1. 발행 기록장 사슬이 온전한가 (고쳐졌거나 지워졌나)
2. 채점 기준이 박혀 있나, 도중에 바뀌었나
3. 기준선 표본이 충분한가
4. 지금 '우위 있음'으로 보이는 셋업이 정말 그런가 — 초과 적중률이 잡음
   범위 안이면 그건 시장 방향이지 셋업 실력이 아니다
5. 예상 범위 밴드가 실제로 맞고 있나 (커버리지 자가채점)

이 화면이 초록이 아니면 성과 숫자를 남에게 보여 주지 않는 게 맞다.
"""

import math

import call_journal
import expected_range
import setup_ledger

# 초과 적중률이 이 안이면 '시장 방향일 뿐'으로 본다.
# 표본 1,000건에서 표준오차가 약 1.5%p라, 그 두 배를 잡음 범위로 둔다.
EDGE_NOISE = 0.03

# 기준선이 이만큼은 쌓여야 초과 적중률을 믿는다.
MIN_BASELINE = 500

# 셋업 하나를 싣기 위한 최소 판정 건수 (보합 제외)
MIN_DECIDED = 30

OK, WARN, BAD = "ok", "warn", "bad"
_MARK = {OK: "✅", WARN: "⚠️", BAD: "🚨"}


def _check(level, title, detail):
    return {"level": level, "title": title, "detail": detail}


def wilson_halfwidth(p, n, z=1.96):
    """적중률의 신뢰구간 반폭 — 표본이 적으면 초과값도 그만큼 못 믿는다."""
    if not n:
        return 1.0
    return z * math.sqrt(max(p * (1 - p), 1e-9) / n)


# ================================
# 🩺 개별 점검
# ================================

def check_journal(path=call_journal.JOURNAL_FILE):
    ok, msg = call_journal.verify(path)
    if not ok:
        return _check(BAD, "발행 기록장", f"{msg} 이 기록은 근거로 쓸 수 없습니다.")

    calls = call_journal.calls(path)
    if not calls:
        return _check(WARN, "발행 기록장",
                      "발행 기록이 없습니다. 시그널을 내보낼 때 "
                      "call_journal.publish()를 같이 부르세요.")
    return _check(OK, "발행 기록장", f"{msg} 발행 {len(calls)}건.")


def check_criteria(path=call_journal.JOURNAL_FILE):
    hist = call_journal.criteria_history(path)
    if not hist:
        return _check(BAD, "채점 기준",
                      "기준이 박혀 있지 않습니다. 결과를 본 뒤에 기준을 고쳐도 "
                      "아무도 모르는 상태입니다 — register_criteria()를 먼저 부르세요.")
    if len(hist) > 1:
        return _check(WARN, "채점 기준",
                      f"{len(hist)}번 바뀌었습니다 (최초 {hist[0]['at'][:10]} · "
                      f"최근 {hist[-1]['at'][:10]}). 바뀐 시점 이후 표본만 "
                      f"지금 기준으로 유효합니다.")
    return _check(OK, "채점 기준", f"{hist[0]['at'][:10]}에 박아 두고 그대로입니다.")


def check_baseline(horizon=3, path=setup_ledger.LEDGER_FILE):
    data = setup_ledger._load(path)
    slot = (data.get("baseline") or {}).get(str(horizon))
    if not slot:
        return _check(BAD, "기준선",
                      "기준선이 없습니다. 초과 적중률을 낼 수 없으므로 적중률만으로는 "
                      "셋업 실력과 시장 방향을 구분할 수 없습니다.")

    decided = slot.get("up", 0) + slot.get("down", 0)
    if decided < MIN_BASELINE:
        return _check(WARN, "기준선",
                      f"표본 {decided:,}건 — {MIN_BASELINE:,}건 미만입니다. "
                      f"초과 적중률이 흔들립니다. 소급 채점을 더 돌리세요.")

    up_rate = slot.get("up", 0) / decided
    note = ""
    if up_rate >= 0.65 or up_rate <= 0.35:
        side = "상승" if up_rate >= 0.65 else "하락"
        note = (f" 표본 기간이 {side}으로 크게 치우쳤습니다 — 반대 국면에서는 "
                f"셋업 성적이 그대로 뒤집힐 수 있습니다.")
    return _check(WARN if note else OK, "기준선",
                  f"표본 {decided:,}건 · 상승 {up_rate*100:.1f}%.{note}")


def check_edges(horizon=3, path=setup_ledger.LEDGER_FILE):
    """'우위 있음'으로 보이는 셋업이 정말 그런지."""
    st = setup_ledger.stats(horizon, path, min_samples=MIN_DECIDED)
    rows = st["rows"]
    if not rows:
        return _check(WARN, "셋업 우위",
                      f"판정 {MIN_DECIDED}건을 넘긴 셋업이 없습니다. "
                      f"아직 실을 숫자가 없습니다."), []

    real, noise, unknown = [], [], []
    for r in rows:
        edge, hit, n = r.get("edge"), r.get("hit_rate"), r.get("decided") or 0
        if edge is None or hit is None:
            unknown.append(r)
            continue
        # 초과값이 그 자체의 오차보다 커야 우위라고 부를 수 있다.
        margin = max(EDGE_NOISE, wilson_halfwidth(hit, n))
        (real if abs(edge) > margin else noise).append(r)

    detail = f"우위 {len(real)}종 · 잡음 범위 {len(noise)}종"
    if unknown:
        detail += f" · 기준선 없어 판단 불가 {len(unknown)}종"
    if not real:
        detail += " — 지금은 어느 셋업도 시장 방향을 넘어서지 못했습니다."
    level = OK if real else WARN
    return _check(level, "셋업 우위", detail), real


def check_range_model(symbols, get_ohlcv_fn, timeframe="4h", sample=8):
    """예상 범위 밴드가 실제로 맞고 있나 (손절 근거로 쓰기 전에)."""
    tight = checked = 0
    for symbol in list(symbols)[:sample]:
        try:
            df = get_ohlcv_fn(symbol, timeframe, limit=300)
        except Exception:
            continue
        if df is None or len(df) == 0:
            continue
        cov = expected_range.coverage(df)
        if not cov:
            continue
        checked += 1
        if cov["band_too_tight"]:
            tight += 1

    if not checked:
        return _check(WARN, "예상 범위", "커버리지를 잴 만한 데이터가 없습니다.")
    if tight > checked / 2:
        return _check(BAD, "예상 범위",
                      f"{checked}종 중 {tight}종에서 밴드가 실측보다 좁습니다 — "
                      f"이 밴드로 손절을 잡으면 그만큼 자주 털립니다.")
    if tight:
        return _check(WARN, "예상 범위",
                      f"{checked}종 중 {tight}종에서 밴드가 좁습니다.")
    return _check(OK, "예상 범위", f"{checked}종 검사, 밴드가 실측을 담고 있습니다.")


# ================================
# 📋 리포트
# ================================

def run(horizon=3, symbols=None, get_ohlcv_fn=None,
        journal_path=call_journal.JOURNAL_FILE,
        ledger_path=setup_ledger.LEDGER_FILE):
    """전 점검을 돌려 (checks, real_edges) 를 돌려준다."""
    checks = [
        check_journal(journal_path),
        check_criteria(journal_path),
        check_baseline(horizon, ledger_path),
    ]
    edge_check, real = check_edges(horizon, ledger_path)
    checks.append(edge_check)

    if symbols and get_ohlcv_fn:
        checks.append(check_range_model(symbols, get_ohlcv_fn))

    return checks, real


def verdict(checks):
    if any(c["level"] == BAD for c in checks):
        return BAD, "성과 숫자를 근거로 쓰지 마십시오. 위 🚨부터 고쳐야 합니다."
    if any(c["level"] == WARN for c in checks):
        return WARN, "조건부로만 읽으십시오. ⚠️ 항목이 숫자의 뜻을 바꿉니다."
    return OK, "전제가 성립합니다. 숫자를 그대로 읽어도 됩니다."


def get_report(horizon=3, symbols=None, get_ohlcv_fn=None,
               journal_path=call_journal.JOURNAL_FILE,
               ledger_path=setup_ledger.LEDGER_FILE):
    """텔레그램 리포트."""
    checks, real = run(horizon, symbols, get_ohlcv_fn, journal_path, ledger_path)
    level, msg = verdict(checks)

    lines = [
        "🩺 <b>점검 — 이 숫자를 믿어도 되나</b>",
        "━━━━━━━━━━━━━━━━━━━━",
    ]
    for c in checks:
        lines.append(f"{_MARK[c['level']]} <b>{c['title']}</b>")
        lines.append(f"    {c['detail']}")

    if real:
        lines += ["", "<b>지금 근거로 쓸 만한 셋업</b>"]
        for r in real:
            lines.append(
                f"  · {r['label']} — 적중 {r['hit_rate']*100:.1f}% · "
                f"초과 {r['edge']*100:+.1f}%p (판정 {r['decided']}건)"
            )

    lines += [
        "━━━━━━━━━━━━━━━━━━━━",
        f"{_MARK[level]} {msg}",
        "",
        "※ 여기서 찾은 버그는 전부 예외 없이 조용히 틀린 숫자였습니다.",
        "  결론을 내기 전에 전제를 먼저 보십시오.",
    ]
    return "\n".join(lines)
