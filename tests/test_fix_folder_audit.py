"""patches/fix_folder_audit.py 회귀 테스트.

bot.py 가 아닌 **네 파일**을 고치는 첫 패치다. 그래서 틀 자체가
새것이고, 여기가 틀리면 파일 하나만 반쯤 고쳐진 채로 남을 수 있다.

가장 값진 것은 ㉙b 다. `backtest.evaluate_trade()` 가 갭으로 뚫린
손절을 손절선에 체결한 걸로 쳐서 **손실을 실제보다 작게** 적었다.
TP1 뒤 본전 스탑이 갭에 뚫리는 경우는 부호까지 뒤집힌다
(+0.46R 로 적히던 것이 실제로는 −0.54R).

고정하는 것:
  · 파일마다 따로 .bak, 문법 깨진 파일은 저장 안 함
  · 두 번 돌려도 두 번 안 넣음
  · 갭 통과 손절이 **시가**로 체결된다 (롱·숏 양쪽)
  · 갭 없는 손절은 값이 그대로다 (고치면서 멀쩡한 걸 바꾸지 않음)
  · 알림이 방향을 말하지 않는다
  · 블록리스트에 토큰화 주식이 들어간다
"""

import ast
import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

import pandas as pd

try:
    from .helpers import load_patch
except ImportError:            # 봇 폴더에 그대로 복사된 경우
    from helpers import load_patch

fx = load_patch("fix_folder_audit")


UNIVERSE = '''_DEFAULT_EXCLUDE = {
    "AAPL", "TSLA", "NVDA",
    "SMCI", "AVGO", "QCOM", "CRCL",
    "QQQ", "XAU",
}
'''

CHART = '''_KR_FONT = "Malgun Gothic"


def draw():
    style = make(
        rc={
            "axes.labelcolor": C_TEXT, "xtick.color": C_TEXT,
            "ytick.color": C_TEXT, "text.color": C_TEXT,
            "axes.linewidth": 0.6, "font.size": 10,
        },
    )
    return style
'''

CVD = '''def check_cvd_divergence_alert(div, coin):
        emoji = "📉" if div['type'] == 'bearish' else "📈"
        action = "단기 반등 가능" if div['type'] == 'bullish' else "단기 하락 압력"

        msg = (
            f"💡 다이버전스 = 가격과 자금흐름 불일치\\n"
            f"   → {action}\\n"
            f"   → 단독 진입 X, 다른 지표와 같이 확인\\n"
        )
        return msg
'''


def backtest_src():
    """진짜 backtest.py 의 evaluate_trade 만 떼어낸 것."""
    return '''SLIPPAGE = 0.0005
FEE_RATE = 0.0006


def round_px(v):
    return round(v, 8)


def _cost_r(entry, exits, unit_risk):
    """왕복 테이커 수수료와 청산 슬리피지를 R로 환산."""
    exit_fee_and_slippage = sum(
        ((price * FEE_RATE) + (price * SLIPPAGE)) * portion
        for price, portion in exits
    )
    return (entry * FEE_RATE + exit_fee_and_slippage) / unit_risk


def evaluate_trade(signal, df_daily, max_bars=60):
    idx      = signal['idx']
    planned_entry = signal['entry']
    sl       = signal['sl']
    tp1      = signal['tp1']
    tp2      = signal['tp2']
    tp3      = signal['tp3']
    is_long  = signal['type'] in ('MID_LONG','FIB_LONG','SMA_LONG','ANGEL')

    future = df_daily.iloc[idx+1 : idx+1+max_bars]
    if len(future) == 0:
        return {"result":"NO_FILL", "r":0.0, "exit":planned_entry}

    next_open = float(future.iloc[0]['open'])
    entry = next_open * (1 + SLIPPAGE if is_long else 1 - SLIPPAGE)
    unit_risk = abs(entry - sl)
    if unit_risk <= 0 or (is_long and entry <= sl) or (not is_long and entry >= sl):
        return {"result":"INVALID_GAP", "r":0.0, "exit": round_px(entry)}

    realized = 0.0
    tp1_hit  = False
    stop     = sl

    for _, row in future.iterrows():
        h = row['high']
        l = row['low']

        if is_long:
            # 보수적: 같은 봉에서 스탑+TP 동시 터치 시 스탑 우선
            if l <= stop:
                if tp1_hit:
                    exits = [(tp1, 0.5), (stop, 0.5)]
                    return {"result":"TP1+BE", "r": round(realized - _cost_r(entry, exits, unit_risk), 2), "exit": stop}
                gross = (stop - entry) / unit_risk
                return {"result":"SL", "r":round(gross - _cost_r(entry, [(stop, 1.0)], unit_risk), 2), "exit": stop}
            if not tp1_hit and h >= tp1:
                realized = 0.5 * (tp1 - entry) / unit_risk
                tp1_hit   = True
                stop      = entry        # 스탑 본전 이동
            if tp1_hit and h >= tp2:
                realized += 0.5 * (tp2 - entry) / unit_risk
                return {"result":"TP2", "r": round(realized - _cost_r(entry, [(tp1, 0.5), (tp2, 0.5)], unit_risk), 2), "exit": tp2}
        else:
            if h >= stop:
                if tp1_hit:
                    return {"result":"TP1+BE", "r": round(realized - _cost_r(entry, [(tp1, 0.5), (stop, 0.5)], unit_risk), 2), "exit": stop}
                gross = (entry - stop) / unit_risk
                return {"result":"SL", "r":round(gross - _cost_r(entry, [(stop, 1.0)], unit_risk), 2), "exit": stop}
            if not tp1_hit and l <= tp1:
                realized = 0.5 * (entry - tp1) / unit_risk
                tp1_hit   = True
                stop      = entry
            if tp1_hit and l <= tp2:
                realized += 0.5 * (entry - tp2) / unit_risk
                return {"result":"TP2", "r": round(realized - _cost_r(entry, [(tp1, 0.5), (tp2, 0.5)], unit_risk), 2), "exit": tp2}

    last_close = future.iloc[-1]['close'] if len(future) > 0 else entry
    raw_r      = (last_close - entry) / unit_risk if is_long else (entry - last_close) / unit_risk
    r = realized + 0.5 * raw_r if tp1_hit else raw_r
    exits = [(tp1, 0.5), (last_close, 0.5)] if tp1_hit else [(last_close, 1.0)]
    r -= _cost_r(entry, exits, unit_risk)
    return {"result":"TIMEOUT", "r": round(r, 2), "exit": round_px(last_close)}
'''


FILES = {
    "paper_universe.py": UNIVERSE,
    "backtest.py": backtest_src(),
    "cvd_flow.py": CVD,
    "chart_render.py": CHART,
}


class Base(unittest.TestCase):

    _n = 0

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.old = os.getcwd()
        os.chdir(self.dir.name)
        for name, body in FILES.items():
            self.write(name, body)
        self.loaded = []

    def tearDown(self):
        os.chdir(self.old)
        for name in self.loaded:
            sys.modules.pop(name, None)
        self.dir.cleanup()

    def write(self, name, text):
        with open(name, "w", encoding="utf-8") as fp:
            fp.write(text)

    def read(self, name):
        with open(name, encoding="utf-8") as fp:
            return fp.read()

    def run_fx(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = fx.main(["fix_folder_audit.py", *args])
        return rc, out.getvalue()

    def load(self, name):
        import importlib.util
        Base._n += 1
        mod = f"_audit{Base._n}"
        spec = importlib.util.spec_from_file_location(mod, name)
        m = importlib.util.module_from_spec(spec)
        sys.modules[mod] = m
        self.loaded.append(mod)
        spec.loader.exec_module(m)
        return m


class TestApply(Base):

    def test_preview_changes_nothing(self):
        before = {n: self.read(n) for n in FILES}
        rc, out = self.run_fx()
        for n in FILES:
            self.assertEqual(self.read(n), before[n])
        self.assertIn("적용 예정", out)

    def test_every_site_is_found(self):
        rc, out = self.run_fx()
        self.assertNotIn("대상 없음", out)

    def test_all_files_still_parse(self):
        self.run_fx("--apply")
        for n in FILES:
            ast.parse(self.read(n))

    def test_a_backup_per_file(self):
        self.run_fx("--apply")
        for n in FILES:
            self.assertTrue(os.path.exists(n + ".bak"), f"{n}.bak 이 없다")

    def test_running_twice_does_not_double(self):
        self.run_fx("--apply")
        once = {n: self.read(n) for n in FILES}
        rc, out = self.run_fx("--apply")
        for n in FILES:
            self.assertEqual(self.read(n), once[n])
        self.assertIn("이미 적용됨", out)

    def test_a_missing_file_does_not_stop_the_rest(self):
        os.remove("cvd_flow.py")
        rc, out = self.run_fx("--apply")
        self.assertIn("cvd_flow.py", out)
        self.assertIn('"SKHYNIX"', self.read("paper_universe.py"))

    def test_all_missing_is_refused(self):
        for n in FILES:
            os.remove(n)
        rc, out = self.run_fx()
        self.assertEqual(rc, 1)
        self.assertIn("봇 폴더에서 실행", out)

    def test_verify(self):
        rc, out = self.run_fx("--verify")
        self.assertIn("□", out)
        self.run_fx("--apply")
        rc, out = self.run_fx("--verify")
        self.assertNotIn("□", out)


class TestGapStop(Base):
    """㉙b — 갭으로 뚫린 손절을 손절선에 체결한 걸로 치고 있었다."""

    def frame(self, bars):
        return pd.DataFrame([
            {"timestamp": pd.Timestamp("2026-01-01") + pd.Timedelta(days=i),
             "open": o, "high": h, "low": l, "close": c}
            for i, (o, h, l, c) in enumerate(bars)])

    LONG = {"idx": 0, "type": "MID_LONG", "entry": 100.0, "sl": 95.0,
            "tp1": 105.0, "tp2": 110.0, "tp3": 115.0}
    SHORT = {"idx": 0, "type": "MID_SHORT", "entry": 100.0, "sl": 105.0,
             "tp1": 95.0, "tp2": 90.0, "tp3": 85.0}

    def both(self, bars, sig):
        """고치기 전/후 값을 같이 낸다."""
        shutil.copy("backtest.py", "old_bt.py")
        self.run_fx("--apply")
        old = self.load("old_bt.py")
        new = self.load("backtest.py")
        df = self.frame(bars)
        return old.evaluate_trade(sig, df), new.evaluate_trade(sig, df)

    def test_a_clean_stop_is_unchanged(self):
        """멀쩡한 것까지 바꾸면 그건 고친 게 아니다."""
        bars = [(100, 101, 99, 100), (100, 101, 94, 96), (96, 97, 95, 96)]
        a, b = self.both(bars, self.LONG)
        self.assertAlmostEqual(a["r"], b["r"])
        self.assertAlmostEqual(b["r"], -1.03, places=2)

    def test_a_long_gapping_through_the_stop(self):
        bars = [(100, 101, 99, 100), (100, 101, 99, 100), (90, 92, 88, 89)]
        a, b = self.both(bars, self.LONG)
        self.assertAlmostEqual(a["r"], -1.03, places=2)
        self.assertAlmostEqual(b["r"], -2.02, places=2)
        self.assertEqual(b["exit"], 90.0, "시가가 아니라 손절선에 체결했다")

    def test_a_short_gapping_through_the_stop(self):
        bars = [(100, 101, 99, 100), (100, 101, 99, 100), (112, 114, 110, 111)]
        a, b = self.both(bars, self.SHORT)
        self.assertAlmostEqual(a["r"], -1.03, places=2)
        self.assertLess(b["r"], -2.0)
        self.assertEqual(b["exit"], 112.0)

    def test_the_break_even_stop_flips_sign(self):
        """TP1 뒤 본전 스탑이 갭에 뚫리면 잔량은 0 이 아니라 손실이다.
        이게 부호까지 뒤집힌 자리다."""
        bars = [(100, 101, 99, 100), (100, 106, 99, 105), (90, 92, 88, 89)]
        a, b = self.both(bars, self.LONG)
        self.assertGreater(a["r"], 0, "고치기 전에는 이익으로 적혔다")
        self.assertLess(b["r"], 0, "갭에 뚫렸는데 아직 이익이다")

    def test_a_win_is_unchanged(self):
        bars = [(100, 101, 99, 100), (100, 106, 99, 105), (105, 111, 104, 110)]
        a, b = self.both(bars, self.LONG)
        self.assertAlmostEqual(a["r"], b["r"])
        self.assertGreater(b["r"], 0)


class TestOthers(Base):

    def test_tokenized_stocks_join_the_blocklist(self):
        self.run_fx("--apply")
        m = self.load("paper_universe.py")
        for s in ("SKHYNIX", "SAMSUNG", "RKLB", "AAOI", "SPCX", "DRAM"):
            self.assertIn(s, m._DEFAULT_EXCLUDE)
        self.assertIn("TSLA", m._DEFAULT_EXCLUDE, "원래 있던 게 사라졌다")

    def test_the_alert_stops_predicting_direction(self):
        self.run_fx("--apply")
        s = self.read("cvd_flow.py")
        self.assertNotIn("단기 반등 가능", s)
        self.assertNotIn("단기 하락 압력", s)
        self.assertNotIn("{action}", s)

    def test_the_alert_still_says_what_happened(self):
        """방향만 빼는 것이지 알림을 끄는 게 아니다."""
        self.run_fx("--apply")
        m = self.load("cvd_flow.py")
        msg = m.check_cvd_divergence_alert({"type": "bearish"}, "BTC")
        self.assertIn("가격과 자금흐름 불일치", msg)
        self.assertIn("단독 진입 X", msg)

    def test_the_chart_font_survives_the_style(self):
        self.run_fx("--apply")
        s = self.read("chart_render.py")
        self.assertIn('"font.family": _KR_FONT', s)
        i_rc = s.index('"axes.linewidth": 0.6')
        self.assertGreater(s.index('"font.family": _KR_FONT'), i_rc,
                           "스타일 rc 안에 안 들어갔다")

    def test_no_font_no_crash(self):
        """폰트를 못 찾은 컴퓨터에서도 차트는 나와야 한다."""
        self.write("chart_render.py", CHART.replace('_KR_FONT = "Malgun Gothic"',
                                                    "_KR_FONT = None"))
        self.run_fx("--apply")
        m = self.load("chart_render.py")
        m.C_TEXT = "#fff"
        m.make = lambda rc: rc
        self.assertNotIn("font.family", m.draw())


if __name__ == "__main__":
    unittest.main()
