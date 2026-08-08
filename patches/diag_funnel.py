"""diag_funnel.py — 신호가 어느 조건에서 죽는지 세어 본다 (읽기 전용)

    cd /path/to/auto
    python diag_funnel.py                # 20종 × 730일
    python diag_funnel.py 10 1y

**아무 파일도 고치지 않습니다.** backtest.py 의 함수와 설정을 그대로
불러 쓰고, 조건별 통과 횟수만 셉니다.

왜 필요한가
──────────
20종 × 730일에 실제 봇이 낼 수 있는 신호가 25건이었다. 레벨 정의를
바꿔 봤지만(⑩) 24건이 됐다 — 거의 변화가 없었다. 즉 **레벨은 병목이
아니다.** 어느 조건이 죽이는지 추측으로 하나씩 바꾸면 또 몇 바퀴 돈다.

여기서는 조건을 하나씩 켜면서 남는 봉 수를 센다. 급격히 줄어드는
자리가 병목이다. 그 자리 하나만 고치면 된다.

읽는 법
──────
    전체 봉                        13,900
    ① 레벨 0.5% 이내 (지지)         1,102   7.9%
    ② + RSI <= 35                      38   0.3%   ← 여기서 29배 줄었다
    ③ + 4H 역추세 아님                  1   0.0%   ← 여기서 또 38배

  각 줄의 % 는 전체 봉 대비다. 옆의 배수는 직전 줄 대비 몇 배로
  줄었는지다. 배수가 가장 큰 줄이 병목이다.

  두 조건이 **서로 모순**이면 (예: '최저점 근처'인데 '상승 추세')
  교집합이 곱한 값보다 훨씬 작게 나온다. 그것도 같이 표시한다 —
  독립이라면 나왔을 기대치와 실제를 나란히 놓는다.
"""

import os
import sys
import time

pd = None
bt = None


def _load():
    """backtest 를 **실행할 때** 부른다.

    import 만으로 SystemExit 하면 이 모듈을 불러 쓰지도, 시험하지도
    못한다. 무거운 의존은 main 에서 건다.
    """
    global pd, bt
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
    except ImportError as e:
        print(f"backtest.py 를 못 불렀습니다: {e}")
        print(f"지금 폴더: {os.getcwd()}")
        return False
    pd, bt = pandas, backtest
    return True


def pick_coins(n):
    for mod_name, var in (("spotlight", "SCAN_COINS"), ("config", "SCAN_COINS"),
                          ("config", "PAPER_COINS"), ("config", "COINS")):
        try:
            v = getattr(__import__(mod_name), var, None)
            if isinstance(v, (list, tuple)) and v:
                return [f"{c}/USDT" if "/" not in c else c for c in v[:n]]
        except Exception:
            pass
    return [f"{c}/USDT" for c in ("BTC", "ETH", "SOL", "XRP", "BNB")][:n]


class Tally:
    """조건을 순서대로 켜면서 남는 봉 수를 센다."""

    def __init__(self, name, labels):
        self.name = name
        self.labels = labels
        self.steps = [0] * len(labels)
        self.solo = [0] * len(labels)    # 각 조건 단독 통과 수
        self.total = 0

    def feed(self, flags):
        self.total += 1
        for k, f in enumerate(flags):
            if f:
                self.solo[k] += 1
        for k in range(len(flags)):
            if all(flags[:k + 1]):
                self.steps[k] += 1
            else:
                break

    def report(self):
        out = [f"\n  [{self.name}]", f"    전체 봉{'':>24}{self.total:>8,}"]
        prev = self.total
        worst = (0, -1)
        for k, label in enumerate(self.labels):
            n = self.steps[k]
            # n이 0이면 배수가 무한이 된다. 그러면 '2건 → 0건'이 '1,104건 →
            # 3건'을 제치고 병목으로 찍힌다. 0일 때는 '몇 건을 잃었나'로 센다.
            drop = (prev / n) if n else prev
            pct = 100 * n / self.total if self.total else 0
            if prev > 0 and (drop > worst[0] or worst[1] < 0):
                worst = (drop, k)
            out.append(f"    {label:<30}{n:>8,}  {pct:5.1f}%"
                       + (f"   ({drop:.0f}배 감소)" if n and drop >= 2 else ""))
            prev = n
        if worst[1] >= 0 and self.steps[worst[1]] < self.total:
            out.append(f"    → 병목: {self.labels[worst[1]].strip()}")

        # 조건이 서로 모순인지 — 독립이라면 나왔을 기대치와 비교
        if self.total and all(self.solo):
            exp = self.total
            for s in self.solo:
                exp *= s / self.total
            got = self.steps[-1]
            out.append(f"    각 조건 단독: " +
                       " / ".join(f"{l.strip().lstrip('+ ')}={s:,}"
                                  for l, s in zip(self.labels, self.solo)))
            out.append(f"    독립이라면 {exp:.1f}건이 나와야 하는데 실제 {got}건"
                       + ("   ← 조건들이 서로 모순이다"
                          if exp >= 3 and got < exp / 3 else ""))
        return out


def h4_trend(df_h4, ts):
    """해당 일봉 마감 시점까지 완결된 마지막 4H봉의 추세 (backtest.py와 동일)."""
    if df_h4 is None:
        return None, None
    at = df_h4[(df_h4['timestamp'] + pd.Timedelta(hours=4)) <= ts + pd.Timedelta(days=1)]
    if len(at) < 50:
        return False, False
    r = at.iloc[-1]
    return (r['close'] > r['ma20'] > r['ma50'],
            r['close'] < r['ma20'] < r['ma50'])


def weekly_at(df_w, ts):
    if df_w is None:
        return None
    at = df_w[(df_w['timestamp'] + pd.Timedelta(days=7)) <= ts + pd.Timedelta(days=1)]
    return at.iloc[-1] if len(at) >= 50 else None


def run(coins, days):
    strict = getattr(bt, "MID_H4_STRICT", True)
    src = getattr(bt, "LEVEL_SOURCE", "rolling20")

    lng = Tally("중기 롱", ["① 레벨 0.5% 이내 (지지)",
                            f"② + RSI <= {bt.RSI_OVERSOLD}",
                            "③ + 4H " + ("정배열 상승" if strict else "역추세 아님")])
    sht = Tally("중기 숏", ["① 레벨 0.5% 이내 (저항)",
                            f"② + RSI >= {bt.RSI_OVERBOUGHT}",
                            "③ + 4H " + ("역배열 하락" if strict else "정추세 아님")])
    ang = Tally("천사 (장기 롱)", ["① 주봉 지지 1% 이내",
                                    f"② + RSI <= {bt.RSI_OVERSOLD + 5}",
                                    "③ + 주봉 정배열 상승"])
    dem = Tally("악마 (장기 숏)", ["① 주봉 저항 1% 이내",
                                    f"② + RSI >= {bt.RSI_OVERBOUGHT - 5}",
                                    "③ + 주봉 역배열 하락"])

    done, failed = 0, []
    for sym in coins:
        coin = sym.replace("/USDT", "")
        try:
            df_d = bt.get_ohlcv_history(sym, "1d", days + 100)
            if df_d is None or len(df_d) < 60:
                failed.append(coin)
                continue
            df_h4 = bt.get_ohlcv_history(sym, "4h", days + 100)
            df_w = bt.get_ohlcv_history(sym, "1w", days + 200)
            df_d = bt.compute_indicators(df_d)
            df_h4 = bt.compute_indicators(df_h4) if (df_h4 is not None and len(df_h4) >= 50) else None
            df_w = bt.compute_indicators(df_w) if (df_w is not None and len(df_w) >= 50) else None
        except Exception as e:
            print(f"  [건너뜀] {coin}: {e}")
            failed.append(coin)
            continue

        prep = bt._lm_pivots_of(df_d) if (src == "levelmap" and
                                          hasattr(bt, "_lm_pivots_of")) else None

        for i in range(50, len(df_d)):
            row = df_d.iloc[i]
            price, rsi = row['close'], row['rsi']
            zone = price * bt.SUPPORT_ZONE_PCT / 100

            if src == "levelmap" and hasattr(bt, "_lm_levels"):
                s, r = bt._lm_levels(prep, i, price)
                sup = s if s is not None else row['recent_low']
                res = r if r is not None else row['recent_high']
            else:
                sup, res = row['recent_low'], row['recent_high']

            up, down = h4_trend(df_h4, row['timestamp'])
            if up is None:
                ok_l = ok_s = True
            elif strict:
                ok_l, ok_s = up, down
            else:
                ok_l, ok_s = (not down), (not up)

            lng.feed([abs(price - sup) <= zone, rsi <= bt.RSI_OVERSOLD, ok_l])
            sht.feed([abs(price - res) <= zone, rsi >= bt.RSI_OVERBOUGHT, ok_s])

            wr = weekly_at(df_w, row['timestamp'])
            if wr is not None:
                wz = price * bt.SUPPORT_ZONE_PCT / 100 * 2
                ang.feed([abs(price - wr['recent_low']) <= wz,
                          rsi <= bt.RSI_OVERSOLD + 5,
                          wr['close'] > wr['ma20'] > wr['ma50']])
                dem.feed([abs(price - wr['recent_high']) <= wz,
                          rsi >= bt.RSI_OVERBOUGHT - 5,
                          wr['close'] < wr['ma20'] < wr['ma50']])
        done += 1

    print(f"\n  처리 {done}종" + (f" · 실패 {len(failed)}종: {', '.join(failed)}" if failed else ""))
    for t in (lng, sht, ang, dem):
        if t.total:
            print("\n".join(t.report()))


def main(argv):
    if not _load():
        return 1
    rest = argv[1:]
    n = next((int(a) for a in rest if a.isdigit() and int(a) <= 50), 20)
    days = 730
    for a in rest:
        al = a.lower()
        if al.endswith("y") and al[:-1].isdigit():
            days = int(al[:-1]) * 365
        elif al.endswith("d") and al[:-1].isdigit():
            days = int(al[:-1])
    days = max(90, min(days, 730))

    coins = pick_coins(n)
    print("=" * 62)
    print("  신호 조건별 통과 횟수 (아무것도 고치지 않습니다)")
    print("=" * 62)
    print(f"  대상 폴더: {os.getcwd()}")
    print(f"  레벨 소스: {getattr(bt, 'LEVEL_SOURCE', 'rolling20')}"
          f" · 4H 정배열 요구: {getattr(bt, 'MID_H4_STRICT', True)}"
          f" · 근접 폭: {bt.SUPPORT_ZONE_PCT}%")
    print(f"  {len(coins)}종 × {days}일 — 2~4분 걸립니다...")
    t0 = time.time()
    run(coins, days)
    print(f"\n  ({time.time()-t0:.0f}초 소요)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
