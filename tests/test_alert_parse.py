"""patches/alert_parse.py 회귀 테스트.

남의 알림 스트림을 거래로 되돌리는 도구다. 여기가 틀리면 **남의
성과를 잘못 판단한다** — 좋은 걸 버리거나, 나쁜 걸 따라가거나.

고정하는 것:
  · 메시지 여러 개가 포지션 하나로 묶인다 (그게 요점이다)
  · 같은 SR 번호가 두 번 뜨면 재방문으로 센다
  · 닫힌 거래와 열린 거래를 섞지 않는다
  · 못 알아본 형식은 조용히 성공한 척하지 않는다
"""

import os
import tempfile
import unittest

try:
    from .helpers import load_patch
except ImportError:            # 봇 폴더에 그대로 복사된 경우
    from helpers import load_patch

fx = load_patch("alert_parse")


SAMPLE = """[2026-08-02 오전 11:14] 보낸이: 💰 PEPE 롱 SR1 도달!
🟢 진입  $0.00000279  →  익절  $0.00000285
💰 실현 누적  +0.11% (+0.0R)  ·  잔여 95% 홀딩
[2026-08-02 오후 4:46] 보낸이: 💰 PEPE 롱 SR3 도달!
🟢 진입  $0.00000279  →  익절  $0.00000296
💰 실현 누적  +0.66% (+0.0R)  ·  잔여 85% 홀딩
[2026-08-10 오전 7:47] 보낸이: 💰 PEPE 롱 SR3 도달!
🟢 진입  $0.00000279  →  익절  $0.00000296
💰 실현 누적  +1.20% (+0.0R)  ·  잔여 75% 홀딩
🔥 6회 연속 목표 도달!
[2026-08-04 오전 10:18] 보낸이: 🟢 DRIFT 롱 진입 알림  ⭐⭐⭐ A급
진입가 $0.0122 · 손절 $0.0098 (-19.9%)
[2026-08-10 오전 8:31] 보낸이: 💰 DRIFT 롱 SR4 도달!
🟢 진입  $0.0122  →  익절  $0.0134
💰 실현 누적  +2.15% (+0.1R)  ·  잔여 80% 홀딩
[2026-08-10 오전 7:47] 보낸이: 🔴 변동성 밴드 숏 알림 종료
🔴 진입 $0.0142  →  종료 $0.0161
결과  -13.24% (-1.0R)  |  손절  |  4일 16시간
"""


class Base(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "chat.txt")
        self.write(SAMPLE)

    def tearDown(self):
        self.dir.cleanup()

    def write(self, text):
        with open(self.path, "w", encoding="utf-8") as fp:
            fp.write(text)

    def blocks(self):
        return fx.read_blocks(self.path)

    def events(self):
        return [e for e in (fx.classify(b) for b in self.blocks()) if e]


class TestRead(Base):

    def test_multiline_messages_stay_together(self):
        """한 메시지가 여러 줄이다. 줄 단위로 자르면 값을 다 잃는다."""
        bs = self.blocks()
        self.assertEqual(len(bs), 6)
        self.assertIn("실현 누적", bs[0]["text"])

    def test_afternoon_is_converted_to_24h(self):
        bs = self.blocks()
        self.assertEqual(bs[0]["time"], "11:14")      # 오전 11:14
        self.assertEqual(bs[1]["time"], "16:46")      # 오후 4:46

    def test_midnight_and_noon(self):
        self.write("[2026-08-02 오전 12:05] 보낸이: 💰 X 롱 SR1 도달!\n"
                   "[2026-08-02 오후 12:30] 보낸이: 💰 X 롱 SR2 도달!\n")
        bs = self.blocks()
        self.assertEqual(bs[0]["time"], "00:05")
        self.assertEqual(bs[1]["time"], "12:30")


class TestClassify(Base):

    def test_entry_gives_stop(self):
        e = [x for x in self.events() if x["kind"] == "entry"][0]
        self.assertEqual(e["coin"], "DRIFT")
        self.assertEqual(e["entry"], 0.0122)
        self.assertEqual(e["stop"], 0.0098)

    def test_partial_tp_reads_r_not_just_percent(self):
        """헤드라인 %는 가격 이동이고, R이 실제 성과다."""
        t = [x for x in self.events() if x["kind"] == "tp"][-1]
        self.assertEqual(t["acc_r"], 0.1)
        self.assertEqual(t["acc_pct"], 2.15)

    def test_tiny_prices_survive(self):
        """PEPE 는 $0.00000279 다. 반올림하면 진입가가 뭉개진다."""
        t = [x for x in self.events() if x["kind"] == "tp"][0]
        self.assertEqual(t["entry"], 0.00000279)

    def test_close_reads_r_and_reason(self):
        c = [x for x in self.events() if x["kind"] == "close"][0]
        self.assertEqual(c["r"], -1.0)
        self.assertIn("손절", c["why"])

    def test_streak_is_captured(self):
        ts = [x for x in self.events() if x.get("streak")]
        self.assertEqual(ts[0]["streak"], 6)

    def test_unknown_message_is_none(self):
        self.assertIsNone(fx.classify({"date": "2026-01-01", "time": "00:00",
                                       "text": "오늘 날씨 좋네요"}))


class TestPositions(Base):
    """메시지 수와 포지션 수가 다르다는 게 이 도구의 요점이다."""

    def positions(self):
        return fx.build(self.events())

    def test_many_messages_one_position(self):
        ps = self.positions()
        self.assertEqual(len(ps), 3, [p["coin"] for p in ps])
        pepe = [p for p in ps if p["coin"] == "PEPE"][0]
        self.assertEqual(len(pepe["tps"]), 3)

    def test_entry_alert_supplies_the_stop(self):
        drift = [p for p in self.positions() if p["coin"] == "DRIFT"][0]
        self.assertEqual(drift["stop"], 0.0098)

    def test_revisited_level_is_counted(self):
        """SR3 가 두 번 떴다. '연속 목표 도달' 의 정체가 이것이다."""
        pepe = [p for p in self.positions() if p["coin"] == "PEPE"][0]
        self.assertEqual(fx.revisits(pepe), 1)

    def test_closed_and_open_are_separate(self):
        ps = self.positions()
        closed = [p for p in ps if p["closed"]]
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0]["closed"]["r"], -1.0)

    def test_realized_r_takes_the_latest(self):
        drift = [p for p in self.positions() if p["coin"] == "DRIFT"][0]
        self.assertAlmostEqual(fx.realized_r(drift), 0.1)

    def test_a_position_with_no_tp_is_zero_not_none(self):
        p = {"coin": "X", "side": "롱", "tps": [], "closed": None}
        self.assertEqual(fx.realized_r(p), 0.0)


class TestReport(Base):

    def run_report(self):
        import io
        from contextlib import redirect_stdout
        out = io.StringIO()
        with redirect_stdout(out):
            rc = fx.report(self.blocks())
        return rc, out.getvalue()

    def test_it_separates_closed_from_open(self):
        rc, out = self.run_report()
        self.assertIn("닫힌 거래 1건", out)
        self.assertIn("열린 거래 2건", out)

    def test_it_refuses_to_score_open_positions(self):
        """열린 포지션의 실현 R을 성과로 세면 안 된다."""
        rc, out = self.run_report()
        self.assertIn("이걸 성과로 세면 안 된다", out)

    def test_it_names_the_message_to_trade_gap(self):
        rc, out = self.run_report()
        self.assertIn("단위가", out)

    def test_it_flags_revisits(self):
        rc, out = self.run_report()
        self.assertIn("재도달", out)

    def test_unreadable_file_says_so(self):
        self.write("안녕하세요\n오늘 날씨가 좋네요\n")
        rc, out = self.run_report()
        self.assertEqual(rc, 1)
        self.assertIn("못 찾았습니다", out)

    def test_breakeven_winrate_is_shown_when_both_sides_exist(self):
        self.write(SAMPLE + """[2026-08-11 오전 9:00] 보낸이: 🟢 AAA 롱 알림 종료
🟢 진입 $1.0  →  종료 $2.0
결과  +100.00% (+2.0R)  |  목표  |  1일
""")
        rc, out = self.run_report()
        self.assertIn("승률", out)


class TestSignals(Base):

    def test_only_positions_with_a_stop_are_exported(self):
        import io
        from contextlib import redirect_stdout
        old = os.getcwd()
        os.chdir(self.dir.name)
        try:
            out = io.StringIO()
            with redirect_stdout(out):
                fx.dump_signals(self.blocks())
            with open(fx.SIGNALS, encoding="utf-8") as fp:
                rows = [l for l in fp if l.strip()]
            # 손절가를 아는 건 진입 알림이 있는 DRIFT 뿐이다
            self.assertEqual(len(rows), 1)
            self.assertIn("DRIFT", rows[0])
        finally:
            os.chdir(old)

    def test_it_explains_why_nothing_came_out(self):
        import io
        from contextlib import redirect_stdout
        self.write("""[2026-08-02 오전 11:14] 보낸이: 💰 PEPE 롱 SR1 도달!
🟢 진입  $0.00000279  →  익절  $0.00000285
💰 실현 누적  +0.11% (+0.0R)  ·  잔여 95% 홀딩
""")
        old = os.getcwd()
        os.chdir(self.dir.name)
        try:
            out = io.StringIO()
            with redirect_stdout(out):
                fx.dump_signals(self.blocks())
            self.assertIn("손절가를 알 수 없어", out.getvalue())
        finally:
            os.chdir(old)


if __name__ == "__main__":
    unittest.main()
