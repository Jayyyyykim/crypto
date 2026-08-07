"""
demo_edge.py — "적중률 81.9%"가 왜 실력이 아닐 수 있는지 재현한다.

MarketSurfer가 공개한 3일 기준 채점표는 이렇게 생겼다:

    디스카운트 구간에서    326건  81.9%      프리미엄 구간에서    189건  37.0%
    아래쪽 유동성 쓸린 뒤  405건  74.8%      위쪽 유동성 쓸린 뒤  468건  26.3%
    전일 고점 돌파 뒤      226건  69.5%      전일 저점 이탈 뒤    219건  28.3%

'상승' 셋업은 전부 69~82%, '하락' 셋업은 전부 26~37%, 각 쌍은 대략 100%로
합쳐지고 전체 방향 적중률은 53.6%. 셋업이 좋아서 나올 수 있는 모양이 아니다.

이 스크립트는 **아무 우위도 없는 순수 랜덤워크에 상승 드리프트만 얹어서**
같은 채점을 돌린다. 셋업에 예측력이 0인데도 같은 표가 나오는지 보면 된다.

    python3 demo_edge.py

실행에 거래소도 API 키도 필요 없다 — 합성 데이터만 쓴다.
"""

import os
import random
import tempfile

import pandas as pd

import setup_ledger as sl

SEED = 20260807
N_SYMBOLS = 40
N_BARS = 400
DAILY_DRIFT = 0.010      # 하루 +1.0% — 사이트 표본 기간과 비슷한 상승장
DAILY_VOL = 0.030        # 하루 변동성 3%


def synth_symbol(rng, n=N_BARS, start=100.0):
    """드리프트 + 잡음. 셋업이 미래를 맞힐 구조적 이유가 전혀 없는 계열."""
    bars = []
    px = start
    for _ in range(n):
        ret = rng.gauss(DAILY_DRIFT, DAILY_VOL)
        nxt = max(px * (1 + ret), 0.01)
        hi = max(px, nxt) * (1 + abs(rng.gauss(0, DAILY_VOL * 0.4)))
        lo = min(px, nxt) * (1 - abs(rng.gauss(0, DAILY_VOL * 0.4)))
        bars.append({"open": px, "high": hi, "low": lo, "close": nxt,
                     "volume": 1000.0})
        px = nxt

    ts = pd.Timestamp("2025-01-01")
    for i, b in enumerate(bars):
        b["timestamp"] = ts + pd.Timedelta(days=i)
    return pd.DataFrame(bars)


def main():
    rng = random.Random(SEED)
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()
    path = tmp.name

    try:
        all_events = []
        baseline = {}
        for k in range(N_SYMBOLS):
            df = synth_symbol(rng)
            evs, base = sl.backfill(f"SYN{k}/USDT", df, horizons=(3,))
            all_events.extend(evs)
            for h, counts in base.items():
                slot = baseline.setdefault(h, {"up": 0, "down": 0, "flat": 0})
                for kk, vv in counts.items():
                    slot[kk] += vv

        sl.merge(all_events, baseline, path=path)
        st = sl.stats(horizon=3, path=path, min_samples=20)

        base = st["baseline"]
        decided = base["up"] + base["down"]
        base_up = base["up"] / decided

        print()
        print("=" * 72)
        print(" 예측력이 0인 합성 시장에서 돌린 셋업 채점표 (3일 기준)")
        print(f" 종목 {N_SYMBOLS} · 봉 {N_BARS} · 일일 드리프트 +{DAILY_DRIFT*100:.1f}%")
        print("=" * 72)
        print()
        print(f" 기준선 — 아무 날이나 잡았을 때:  상승 {base_up*100:.1f}%  "
              f"하락 {(1-base_up)*100:.1f}%   (표본 {decided:,}건)")
        print()
        print(f" {'셋업':<24} {'방향':<6} {'표본':>7} {'적중률':>8} {'기준선':>8} {'초과':>9}")
        print(" " + "-" * 68)

        for r in sorted(st["rows"], key=lambda x: x["direction"]):
            if r["hit_rate"] is None:
                continue
            dir_kr = "상승" if r["direction"] == "up" else "하락"
            edge = r["edge"]
            edge_s = f"{edge*100:+.1f}%p" if edge is not None else "—"
            print(f" {r['label']:<22} {dir_kr:<6} {r['samples']:>7,} "
                  f"{r['hit_rate']*100:>7.1f}% {r['baseline']*100:>7.1f}% "
                  f"{edge_s:>9}")

        print()
        print(" 참고 — MarketSurfer가 공개한 같은 셋업의 3일 적중률")
        print(" ──────────────────────────────────────────────")
        print("   디스카운트 81.9  아래쪽쓸림 74.8  고점돌파 69.5   (상승 셋업)")
        print("   프리미엄  37.0  위쪽쓸림   26.3  저점이탈 28.3   (하락 셋업)")
        print()
        print(" 읽는 법")
        print(" ─────────")
        print(" · 위 표와 이 합성 표가 거의 같은 자리에 있다. 그런데 이 계열은")
        print("   드리프트 얹은 잡음이라 셋업의 예측력이 정확히 0이다.")
        print(" · 즉 '적중률' 칸만으로는 실력과 시장 방향을 구분할 수 없다.")
        print(" · '초과' 칸이 진짜 값이다. 0 근처면 그 셋업이 한 일은 아무것도 없고,")
        print("   시장이 오른 것을 셋업 성적으로 착각한 것이다.")
        print(" · 그러니 '디스카운트 롱 81.9%'를 근거로 실돈을 넣으면 안 된다.")
        print("   같은 셋업을 하락장에서 돌리면 그대로 뒤집힌다.")
        print()
        print("=" * 72)
        print()
    finally:
        for p in (path, path + ".tmp"):
            if os.path.exists(p):
                os.remove(p)


if __name__ == "__main__":
    main()
