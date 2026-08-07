"""
risk_calc.py — 리스크 관리 계산기

MarketSurfer의 '리스크 관리 계산기' 화면을 봇으로 옮긴 모듈.
사이트가 화면에 써 둔 한 줄이 이 모듈 전부다:

  "포지션 크기는 손절 거리가 정하고, 성과는 R 로 잽니다."

왜 이게 필요한가
──────────────
`setup_ledger.py`가 적중률을 재 준다. 그런데 **적중률만으로는 돈을 벌지
잃을지 알 수 없다.** 적중률 40%짜리 셋업도 손익비 3R이면 벌고, 70%짜리도
0.3R이면 잃는다.

    본전 승률 = 1 / (1 + 손익비)

손익비 2R이면 33%만 맞아도 본전이고, 0.5R이면 67%를 맞아야 본전이다.
`setup_ledger.stats()`의 `hit_rate`와 이 값을 나란히 놓아야 비로소
"이 셋업에 자금을 실어도 되는가"에 답이 된다.

expected_range.py와 짝이다
─────────────────────────
expected_range가 "손절을 여기보다 좁게 잡으면 노이즈에 털린다"를 말해 주고,
여기서 "그 손절 폭이면 몇 개 살 수 있나 / 그때 손익비가 얼마인가"를 낸다.
두 모듈을 같이 쓰면 손절이 먼저 정해지고 크기가 따라 정해진다 — 순서가 반대면
(크기를 먼저 정하고 손절을 끼워 맞추면) 늘 손절이 너무 좁아진다.

수수료를 빼는 이유
────────────────
왕복 0.1%는 작아 보이지만 손절 폭이 2%인 매매에서는 리스크의 5%다.
손익비 계산에서 이걸 빼면 실제보다 항상 좋게 나오고, 그 차이가 승률
기준선을 몇 %p씩 움직인다.
"""

# 기본 매매당 리스크 (계좌 대비 %)
DEFAULT_RISK_PCT = 1.0

# 왕복 수수료 + 슬리피지 추정 (%). 거래소·종목에 따라 바꿔 쓴다.
DEFAULT_FEE_PCT = 0.1

# 켈리 비중 상한. 그대로 쓰면 파산 확률이 높다 — 승률 추정 자체가 표본에서
# 나온 값이라 오차가 있고, 켈리는 그 오차에 매우 민감하다.
KELLY_CAP = 0.25


# ================================
# 🧮 단일 진입
# ================================

def plan(account, entry, stop, target, risk_pct=DEFAULT_RISK_PCT,
         win_rate=None, fee_pct=DEFAULT_FEE_PCT):
    """
    매매 계획 하나를 통째로 계산한다.

    account : 계좌 크기
    entry   : 진입가
    stop    : 손절가 (진입가보다 낮으면 롱, 높으면 숏 — 방향을 따로 안 받는다)
    target  : 목표가
    win_rate: 0~1. setup_ledger.stats()의 hit_rate를 넣으면 기대값까지 나온다.

    반환 None = 계산 불가 (손절 거리 0 등). 숫자를 억지로 내지 않는다.
    """
    if not account or account <= 0 or not entry or entry <= 0:
        return None
    if stop is None or stop == entry:
        return None

    side = 1 if stop < entry else -1
    stop_dist = abs(entry - stop)
    risk_amount = account * risk_pct / 100
    qty = risk_amount / stop_dist
    notional = qty * entry
    fees = notional * fee_pct / 100

    reward = abs(target - entry) * qty if target else 0.0
    # 손익비는 수수료를 양쪽에서 뺀 실제 값으로 낸다.
    risk_total = risk_amount + fees
    rr = (reward - fees) / risk_total if risk_total else 0.0
    breakeven = 1 / (1 + rr) if rr > -1 else 1.0

    expectancy = None
    if win_rate is not None:
        expectancy = win_rate * rr - (1 - win_rate)

    return {
        "side"          : side,
        "side_kr"       : "롱 (상승 방향)" if side > 0 else "숏 (하락 방향)",
        "entry"         : entry,
        "stop"          : stop,
        "target"        : target,
        "stop_dist"     : stop_dist,
        "stop_pct"      : stop_dist / entry,
        "risk_amount"   : risk_amount,
        "risk_pct"      : risk_pct,
        "quantity"      : qty,
        "notional"      : notional,
        "leverage"      : notional / account,
        "fees"          : fees,
        "reward"        : reward,
        "rr"            : rr,
        "breakeven_rate": breakeven,
        "win_rate"      : win_rate,
        "expectancy_r"  : expectancy,
    }


def verdict(p):
    """계획 하나에 대한 한 줄 판정."""
    if not p:
        return "계산할 수 없습니다 (손절 거리가 0이거나 입력이 비었습니다)."

    be = p["breakeven_rate"] * 100
    if p["expectancy_r"] is None:
        return (f"승률을 모릅니다 — 본전 승률 {be:.1f}%를 넘길 자신이 있을 때만 "
                f"들어가십시오.")
    e = p["expectancy_r"]
    wr = p["win_rate"] * 100
    if e <= 0:
        return (f"기대값 {e:+.2f}R — 지금 조건에서는 걸수록 잃습니다 "
                f"(본전 {be:.1f}% vs 실제 {wr:.1f}%).")
    return (f"기대값 {e:+.2f}R — 100번 반복하면 약 {e*100:.0f}R입니다 "
            f"(본전 {be:.1f}% vs 실제 {wr:.1f}%).")


# ================================
# 🪜 분할 진입 · 분할 익절
# ================================

def scaled_plan(account, entries, stop, exits=None,
                risk_pct=DEFAULT_RISK_PCT, fee_pct=DEFAULT_FEE_PCT):
    """
    분할 진입 계획.

    entries: [(가격, 비중), ...]  비중 합은 아무 값이나 (내부에서 정규화)
    exits  : [(가격, 비중), ...]  없으면 실현 R을 내지 않는다

    평단이 먼저 정해지고, 그 평단과 손절 사이 거리로 전체 수량이 정해진다.
    분할로 들어간다고 리스크가 줄지 않는다는 걸 숫자로 보이는 게 목적이다 —
    평단이 좋아질 뿐 손절까지 거리는 그대로다.
    """
    if not entries or not account or account <= 0:
        return None
    w_sum = sum(w for _, w in entries)
    if w_sum <= 0:
        return None

    avg = sum(p * w for p, w in entries) / w_sum
    if stop is None or stop == avg:
        return None

    stop_dist = abs(avg - stop)
    risk_amount = account * risk_pct / 100
    qty = risk_amount / stop_dist
    side = 1 if stop < avg else -1

    realized_r = None
    if exits:
        ex_sum = sum(w for _, w in exits)
        if ex_sum > 0:
            pnl = sum((p - avg) * side * qty * (w / ex_sum) for p, w in exits)
            realized_r = pnl / (stop_dist * qty)

    return {
        "avg_entry"  : avg,
        "stop"       : stop,
        "side"       : side,
        "side_kr"    : "롱 (상승 방향)" if side > 0 else "숏 (하락 방향)",
        "quantity"   : qty,
        "notional"   : qty * avg,
        "risk_amount": risk_amount,
        "stop_pct"   : stop_dist / avg,
        "entries"    : [{"price": p, "weight": w / w_sum} for p, w in entries],
        "exits"      : ([{"price": p, "weight": w / sum(x for _, x in exits)}
                         for p, w in exits] if exits else []),
        "realized_r" : realized_r,
    }


# ================================
# 📐 크기 조절
# ================================

def kelly_fraction(win_rate, rr, cap=KELLY_CAP):
    """
    켈리 비중. 상한으로 자른다.

    실전에서 풀켈리를 쓰면 안 되는 이유는 수학이 틀려서가 아니라 입력이
    틀리기 때문이다 — win_rate는 표본에서 나온 추정치고, 그게 5%p만
    낙관적이어도 켈리는 두 배로 잘못 나온다.
    """
    if rr is None or rr <= 0 or win_rate is None:
        return 0.0
    f = (win_rate * (1 + rr) - 1) / rr
    return max(0.0, min(f, cap))


def suggest_risk_pct(win_rate, rr, base=DEFAULT_RISK_PCT, cap=2.0,
                     min_samples_met=True):
    """
    채점표의 적중률을 매매당 리스크 %로 옮긴다.

    min_samples_met=False (= setup_ledger가 아직 표본이 모자라다고 한 경우)
    면 기본값의 절반만 건다. 우위가 **확인되지 않은** 셋업과 우위가
    **없는** 셋업은 다르지만, 자금 앞에서는 같게 취급하는 게 안전하다.
    """
    if not min_samples_met or win_rate is None:
        return base / 2
    k = kelly_fraction(win_rate, rr)
    return max(0.25, min(cap, base + k * 100 * 0.25))


def size_from_edge(account, entry, stop, target, ledger_row=None,
                   base_risk=DEFAULT_RISK_PCT, fee_pct=DEFAULT_FEE_PCT):
    """
    setup_ledger.stats()의 행 하나를 그대로 받아 크기까지 낸다.

    ledger_row: {"hit_rate", "edge", "decided", ...} — stats()["rows"]의 원소

    **초과 적중률(edge)이 0 이하면 크기를 절반으로 깎는다.** 적중률이
    아무리 높아도 기준선과 같으면 그 셋업이 한 일은 없기 때문이다
    (docs/marketsurfer_분석.md 3절).
    """
    raw_rate = (ledger_row or {}).get("hit_rate")
    met = False
    if ledger_row:
        met = (ledger_row.get("decided") or 0) >= 30
        edge = ledger_row.get("edge")
        if edge is not None and edge <= 0:
            met = False       # 시장 방향을 따라간 것뿐 — 우위로 치지 않는다

    # 우위가 확인되지 않았으면 그 적중률로 기대값을 내지 않는다.
    #
    # 이걸 빼먹으면 리포트가 자기모순에 빠진다. "초과 적중률 -0.6%p —
    # 우위가 확인되지 않았습니다"라고 경고해 놓고, 바로 그 82%로
    # "기대값 +1.73R"을 화면에서 제일 큰 숫자로 찍게 된다. 방금 못 믿겠다고
    # 한 입력으로 계산한 값이라 그중 가장 위험한 숫자다.
    win_rate = raw_rate if met else None

    probe = plan(account, entry, stop, target, base_risk, win_rate, fee_pct)
    if not probe:
        return None

    risk_pct = suggest_risk_pct(win_rate, probe["rr"], base_risk,
                                min_samples_met=met)
    out = plan(account, entry, stop, target, risk_pct, win_rate, fee_pct)
    if out:
        out["edge_confirmed"] = met
        out["ledger_edge"] = (ledger_row or {}).get("edge")
        out["ledger_hit_rate"] = raw_rate      # 참고로만 — 기대값에는 안 쓴다
    return out


# ================================
# 📋 리포트
# ================================

def get_report(p, title="리스크 관리 계산기"):
    """텔레그램 리포트."""
    if not p:
        return "📭 계산할 수 없어요 (손절 거리가 0이거나 입력이 비었습니다)."

    lines = [
        f"🧮 <b>{title}</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"{p['side_kr']}",
        f"  진입 {p['entry']:,.6g}",
        f"  손절 {p['stop']:,.6g}  ({p['stop_pct']*100:.2f}%)",
    ]
    if p.get("target"):
        lines.append(f"  목표 {p['target']:,.6g}")

    lines += [
        "",
        f"<b>크기</b>  (손절 거리가 정합니다)",
        f"  수량      {p['quantity']:,.6g}",
        f"  명목      {p['notional']:,.0f}",
        f"  레버리지  {p['leverage']:.2f}x",
        f"  걸린 돈  {p['risk_amount']:,.0f}  (계좌의 {p['risk_pct']}%)",
        f"  수수료    {p['fees']:,.0f} (추정)",
        "",
        f"<b>손익비</b>  {p['rr']:.2f}R",
        f"  본전 승률 {p['breakeven_rate']*100:.1f}%",
    ]

    if p.get("edge_confirmed") is False:
        lines += [
            "",
            "⚠️ 이 셋업은 <b>우위가 확인되지 않았습니다</b>",
            "  (표본 부족이거나 초과 적중률이 0 이하) — 크기를 절반으로 낮췄습니다.",
        ]
        if p.get("ledger_hit_rate") is not None:
            lines.append(
                f"  채점표 적중률 {p['ledger_hit_rate']*100:.1f}%는 기대값 계산에 "
                f"쓰지 않았습니다"
            )
    if p.get("ledger_edge") is not None:
        lines.append(f"  채점표 초과 적중률 {p['ledger_edge']*100:+.1f}%p")

    lines += ["", verdict(p), "━━━━━━━━━━━━━━━━━━━━",
              "※ 승률은 표본에서 나온 추정치입니다. 기대값을 확정 수익으로",
              "  읽지 마십시오."]
    return "\n".join(lines)


def get_scaled_report(sp):
    """분할 진입 리포트."""
    if not sp:
        return "📭 계산할 수 없어요."
    lines = [
        "🪜 <b>분할 진입</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"{sp['side_kr']}",
        f"  평단 {sp['avg_entry']:,.6g} · 손절 {sp['stop']:,.6g} "
        f"({sp['stop_pct']*100:.2f}%)",
        f"  수량 {sp['quantity']:,.6g} · 명목 {sp['notional']:,.0f}",
        "",
        "<b>진입</b>",
    ]
    for e in sp["entries"]:
        lines.append(f"  {e['price']:,.6g}  × {e['weight']*100:.0f}%")
    if sp["exits"]:
        lines += ["", "<b>익절</b>"]
        for x in sp["exits"]:
            lines.append(f"  {x['price']:,.6g}  × {x['weight']*100:.0f}%")
    if sp["realized_r"] is not None:
        lines += ["", f"계획대로 전부 채워지면 <b>{sp['realized_r']:+.2f}R</b>"]
    lines += [
        "━━━━━━━━━━━━━━━━━━━━",
        "※ 분할로 들어가도 손절까지 거리는 그대로입니다.",
        "  평단이 좋아질 뿐 리스크가 줄지 않습니다.",
    ]
    return "\n".join(lines)
