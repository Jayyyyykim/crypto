"""tv_levels.py — 트레이딩뷰 지표의 지지/저항 계산을 역산한 것

    python tv_levels.py --check BINANCE_BTCUSDT_1D.csv    # 내보낸 CSV 로 검증
    python tv_levels.py --check *.csv                     # 여러 개 한 번에

무엇을 알아냈나
──────────────
트레이딩뷰 '차트 데이터 내보내기' CSV 300줄(1시간·4시간·일봉)에서
역산했다. 스크린샷이 아니라 **지표가 그린 값 그대로**라서 소수점까지
맞춰 볼 수 있었다.

구조는 완전히 풀렸다.

  ① 중심 = **그날 일봉 시가** (= 전날 종가)

     4시간봉에서 6봉마다, 1시간봉에서 24봉마다, 일봉에서 1봉마다
     값이 바뀐다 — 전부 정확히 24시간이다. 어느 타임프레임에서 보든
     **하루에 한 번** 바뀌는 일봉 기준 계산이다.

     바뀌는 순간의 값은 그 봉의 시가와 **오차 0** 으로 일치했다
     (일봉 300줄 전부, 4시간봉 50번 전부).

  ② 레벨 = 중심 ± 폭 × [1, 2.402, 4.512]

     세 배수는 상수다. 1시간·4시간·일봉 900줄에서 단 한 번도
     안 흔들렸다. 지지와 저항은 중심에 대해 완전 대칭이다.

  ③ 손절선 = 레벨에서 폭 × 0.6 만큼 더 바깥

     L1 SL 은 지지1 아래로 0.6×폭, S1 SL 은 저항1 위로 0.6×폭.
     이것도 세 파일 전부 정확히 0.600 이었다.

  ④ 스윙 레벨 = 같은 구조, 폭만 크다

     중심도 폭도 다르지만 배수는 똑같이 [1, 2.402, 4.512] 다.
     스윙 중심은 훨씬 드물게 바뀐다(4시간봉 300줄에서 3번).

  ⑤ 폭 ≈ 0.5 × ATR(9, 와일더)  ← **여기만 근사다**

     배수의 중앙값이 0.504, 흩어짐 2.9%. 0.5 라는 값이 너무
     깔끔해서 맞는 방향은 분명한데, 정확한 재현은 안 된다.
     남은 오차로 레벨이 얼마나 틀리는지:

         저항1 위치  중앙 0.052% · 90분위 0.163%  (가격 대비)
         저항3 위치  중앙 0.235% · 90분위 0.734%

     1차 레벨은 사실상 맞고, 3차는 조금 벌어진다.

아직 못 푼 것 — 정직하게
──────────────────────
· **폭 공식이 정확하지 않다.** ATR 기간·평활 방식이 조금 다르거나,
  일봉이 아닌 다른 봉으로 재고 있을 수 있다.

· **되그리기(repaint) 의심이 있다.** 폭을 '전날까지의 ATR' 로 맞추면
  흩어짐이 5.7%, '오늘 것 포함' 으로 맞추면 2.9% 다. 후자가 더 잘
  맞는다는 건 **오늘 일봉이 끝나야 알 수 있는 값**을 쓰고 있다는
  뜻일 수 있다. 그러면 실시간으로 본 레벨과 나중에 되돌아본 레벨이
  다르다 — 지표가 실제보다 잘 맞아 보이게 된다.

  내보낸 과거 자료로는 이걸 가릴 수 없다. 트레이딩뷰가 확정된 값만
  내보내기 때문이다. **살아 있는 값으로만 확인된다:**

      오늘 09:05 에 저항1·지지1 을 적어 두고,
      내일 09:05 에 어제 날짜의 저항1·지지1 을 다시 본다.
      숫자가 달라져 있으면 되그리기다.

· 총 추세점수(38.2) 는 손대지 않았다. 여러 값을 가중합한 것이라
  표본 몇 개로 맞추면 그건 맞춘 게 아니라 껴맞춘 것이다.

그래서 이걸로 뭘 하나
───────────────────
**베끼는 게 목적이 아니라 재는 게 목적이다.** 진입 규칙 70가지를
쟀고 하나도 통과하지 못했다. 이건 71번째 후보다.

    python signal_bench.py 30 4y --tv      (아직 안 붙임)

폭이 근사여도 **재는 데는 지장 없다** — 1차 레벨 오차가 가격의
0.05% 이고, 손절폭(1.5×ATR)의 수십 분의 일이다. 우위가 있으면
이 정도 오차로도 보인다. 없으면 정확히 맞춰도 안 보인다.
"""

import glob
import sys

TF_MULT = (1.0, 2.402, 4.512)     # 세 레벨의 폭 배수 — 상수
SL_MULT = 0.6                     # 손절선은 레벨에서 폭×0.6 더 바깥
ATR_LEN = 9                       # 와일더
ATR_K = 0.5                       # 폭 = ATR × 이것 (근사)


def wilder_atr(high, low, close, n=ATR_LEN):
    """와일더 ATR. pandas Series 를 받아 Series 로 돌려준다."""
    import pandas as pd
    prev = close.shift()
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()],
                   axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def daily_levels(daily_df, atr_k=ATR_K, atr_len=ATR_LEN):
    """일봉 DataFrame(open/high/low/close) → 그날의 레벨 dict 시리즈.

    중심은 **그날 시가**다. 폭은 ATR 로 잡는다.

    ⚠️ 폭은 근사다. 원본이 오늘 봉을 포함한 ATR 을 쓰는 것으로
       보이는데, 그건 그날이 끝나야 확정되는 값이다. 여기서는
       **전날까지**만 쓴다 — 되그리기를 흉내 내지 않는다.
       원본보다 조금 좁게 나온다.
    """
    atr = wilder_atr(daily_df["high"], daily_df["low"], daily_df["close"],
                     atr_len).shift()
    center = daily_df["open"]
    width = atr * atr_k
    out = {"center": center, "width": width}
    for i, m in enumerate(TF_MULT, 1):
        out[f"res{i}"] = center + width * m
        out[f"sup{i}"] = center - width * m
        out[f"res{i}_sl"] = center + width * (m + SL_MULT)
        out[f"sup{i}_sl"] = center - width * (m + SL_MULT)
    return out


def levels_now(get_ohlcv_fn, symbol, atr_k=ATR_K, atr_len=ATR_LEN, bars=60):
    """봇에서 쓰는 입구. 오늘의 레벨을 dict 로 돌려준다.

    get_ohlcv_fn(종목, 타임프레임, limit=개수) — 봇의 것을 그대로 쓴다.
    """
    df = get_ohlcv_fn(symbol, "1d", limit=max(bars, atr_len + 5))
    if df is None or len(df) < atr_len + 2:
        return None
    lv = daily_levels(df, atr_k, atr_len)
    last = {}
    for k, s in lv.items():
        v = s.iloc[-1]
        if v != v:                      # NaN
            return None
        last[k] = float(v)
    return last


# ── 검증 ─────────────────────────────────────────────────────

def check(paths):
    """트레이딩뷰가 내보낸 CSV 로 구조를 확인한다.

    구조(중심·배수·손절 간격)는 정확히 맞아야 한다. 폭만 근사다.
    """
    try:
        import pandas as pd
    except ImportError:
        print("  pandas 가 필요합니다.")
        return 1

    files, empty = [], []
    for p in paths:
        hit = sorted(glob.glob(p))
        if hit:
            files.extend(hit)
        elif any(ch in p for ch in "*?["):
            # 무늬인데 맞는 파일이 없다. 그 무늬를 그대로 열려고 하면
            # [Errno 22] 같은 알 수 없는 소리가 난다 — 윈도우 cmd 는
            # 무늬를 안 풀어 주므로 파이썬까지 그대로 온다.
            empty.append(p)
        else:
            files.append(p)
    if empty:
        import os as _os
        print(f"\n  '{', '.join(empty)}' 에 맞는 파일이 없습니다.")
        print(f"  지금 폴더: {_os.getcwd()}")
        here = sorted(glob.glob("*.csv"))
        print("  이 폴더의 CSV: " + (", ".join(here[:8]) if here else "없음"))
        print("\n  내려받은 곳에서 실행하거나 경로를 그대로 적으십시오:")
        print(r"      python tv_levels.py --check %USERPROFILE%\Downloads\*.csv")
    if not files:
        return 1

    print("=" * 72)
    print("  트레이딩뷰 지표 역산 — 구조 확인")
    print("=" * 72)
    bad = 0
    for path in files:
        try:
            d = pd.read_csv(path)
        except Exception as e:
            print(f"\n  {path}: 못 읽음 ({e})")
            bad += 1
            continue
        need = ["open", "high", "low", "close", "저항 1", "지지 1"]
        if any(n not in d.columns for n in need):
            print(f"\n  {path}: 지표 칸이 없습니다. 내보낼 때 지표를 켜 두십시오.")
            bad += 1
            continue

        c = (d["저항 1"] + d["지지 1"]) / 2
        w = d["저항 1"] - c
        print(f"\n  {path}  ({len(d)}줄)")

        # ① 중심 = 값이 바뀌는 봉의 시가
        moved = c.diff().fillna(1) != 0
        moved.iloc[0] = False
        idx = c.index[moved]
        if len(idx):
            e = (c.loc[idx] - d["open"].loc[idx]).abs().max()
            gaps = idx.to_series().diff().dropna()
            per = f"{int(gaps.median())}봉마다" if len(gaps) else "-"
            print(f"    중심 = 바뀌는 봉의 시가   최대오차 {e:.6f}   ({per}, {len(idx)}회)")
            if e > 1e-6:
                bad += 1

        # ② 배수
        for i, m in ((2, TF_MULT[1]), (3, TF_MULT[2])):
            if f"저항 {i}" not in d.columns:
                continue
            r = ((d[f"저항 {i}"] - c) / w)
            print(f"    저항{i}/폭  {r.min():.4f}~{r.max():.4f}   (기대 {m})")
            if abs(r.median() - m) > 1e-3:
                bad += 1

        # ③ 손절 간격
        if "L1 SL" in d.columns:
            g = ((d["지지 1"] - d["L1 SL"]) / w)
            print(f"    손절 간격/폭  {g.min():.4f}~{g.max():.4f}   (기대 {SL_MULT})")
            if abs(g.median() - SL_MULT) > 1e-3:
                bad += 1

        # ④ 폭 — 여기만 근사
        atr = wilder_atr(d["high"], d["low"], d["close"])
        for lbl, s in (("전날까지", atr.shift()), ("오늘 포함", atr)):
            k = (w / s).dropna()
            if len(k) < 30:
                continue
            print(f"    폭/ATR9 ({lbl})  중앙 {k.median():.4f}  "
                  f"흩어짐 {k.std() / k.median() * 100:.2f}%")

    print("\n" + "=" * 72)
    if bad:
        print(f"  ⚠️ {bad}군데가 기대와 다릅니다. 지표 판이 바뀌었을 수 있습니다.")
        return 1
    print("""  구조는 그대로입니다 (중심·배수·손절 간격).

  폭만 근사입니다. '오늘 포함' 쪽 흩어짐이 더 작으면 원본이
  **오늘 봉이 끝나야 아는 값**을 쓴다는 뜻일 수 있습니다 —
  되그리기입니다. 살아 있는 값으로만 확인됩니다:

      오늘 09:05 에 저항1·지지1 을 적어 두고
      내일 09:05 에 어제 날짜의 같은 값을 다시 봅니다.
      달라져 있으면 되그리기입니다.""")
    return 0


def main(argv):
    if "--check" in argv:
        i = argv.index("--check")
        return check(argv[i + 1:])
    print(__doc__)
    print("    python tv_levels.py --check <내보낸 CSV>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
