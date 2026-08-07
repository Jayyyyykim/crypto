import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import regime_gate as rg


class TestAlignment(unittest.TestCase):
    def test_three_of_four_aligns(self):
        a, votes, _ = rg.align_from_states(
            {"1d": "up", "3d": "up", "1w": "up", "1M": "down"})
        self.assertEqual(a, "up")
        self.assertEqual(votes, 3)

    def test_two_two_is_not_aligned(self):
        a, _, _ = rg.align_from_states(
            {"1d": "up", "3d": "up", "1w": "down", "1M": "down"})
        self.assertIsNone(a)

    def test_side_counts_for_neither(self):
        a, _, _ = rg.align_from_states(
            {"1d": "up", "3d": "up", "1w": "side", "1M": "side"})
        self.assertIsNone(a)   # up이 2표뿐

    def test_missing_tf_excluded_but_can_still_judge(self):
        a, votes, valid = rg.align_from_states(
            {"1d": "up", "3d": "up", "1w": "up", "1M": None})
        self.assertEqual(a, "up")
        self.assertEqual(len(valid), 3)

    def test_too_few_valid_tfs_gives_no_verdict(self):
        a, _, _ = rg.align_from_states(
            {"1d": "up", "3d": "up", "1w": None, "1M": None})
        self.assertIsNone(a)


class TestLabels(unittest.TestCase):
    def test_low_alignment_is_wait(self):
        label, _ = rg._label(up_ratio=0.5, up_aligned=5, down_aligned=5, total=100)
        self.assertEqual(label, "관망")

    def test_strong_up(self):
        label, _ = rg._label(up_ratio=0.60, up_aligned=40, down_aligned=5, total=100)
        self.assertEqual(label, "상승국면")

    def test_strong_down(self):
        label, _ = rg._label(up_ratio=0.30, up_aligned=5, down_aligned=40, total=100)
        self.assertEqual(label, "하락국면")

    def test_balanced_alignment_is_wait(self):
        label, _ = rg._label(up_ratio=0.50, up_aligned=25, down_aligned=25, total=100)
        self.assertEqual(label, "관망")

    def test_empty(self):
        label, _ = rg._label(0, 0, 0, 0)
        self.assertEqual(label, "판정불가")


def _fake_analyze(table):
    """table: {symbol: {tf: trend_code}}"""
    def fn(symbol, tf):
        st = table.get(symbol, {}).get(tf)
        return {"trend_code": st} if st else None
    return fn


class TestScanUniverse(unittest.TestCase):
    def test_counts_aligned_symbols(self):
        table = {
            "A/USDT": {"1d": "up", "3d": "up", "1w": "up", "1M": "up"},
            "B/USDT": {"1d": "down", "3d": "down", "1w": "down", "1M": "side"},
            "C/USDT": {"1d": "up", "3d": "down", "1w": "up", "1M": "down"},
        }
        scan = rg.scan_universe(list(table), _fake_analyze(table))
        self.assertEqual(scan["total"], 3)
        self.assertEqual(scan["up_aligned"], 1)
        self.assertEqual(scan["down_aligned"], 1)

    def test_skips_symbols_without_data(self):
        table = {"A/USDT": {"1d": "up"}}     # 유효 TF 1개뿐
        scan = rg.scan_universe(list(table), _fake_analyze(table))
        self.assertEqual(scan["total"], 0)
        self.assertEqual(scan["skipped"], 1)

    def test_survives_analyze_exception(self):
        def boom(symbol, tf):
            raise RuntimeError("x")
        scan = rg.scan_universe(["A/USDT"], boom)
        self.assertEqual(scan["total"], 0)


class TestGate(unittest.TestCase):
    def _scan(self, label):
        return {"label": label, "up_ratio": 0.5, "total": 100}

    def test_long_allowed_in_uptrend(self):
        ok, _ = rg.gate("long", self._scan("상승국면"))
        self.assertTrue(ok)

    def test_long_blocked_in_wait(self):
        ok, reason = rg.gate("long", self._scan("관망"))
        self.assertFalse(ok)
        self.assertIn("관망", reason)

    def test_long_blocked_in_downtrend(self):
        ok, _ = rg.gate("long", self._scan("하락국면"))
        self.assertFalse(ok)

    def test_short_allowed_in_downtrend(self):
        ok, _ = rg.gate("short", self._scan("하락우위"))
        self.assertTrue(ok)

    def test_symbol_counter_alignment_blocks(self):
        row = {"aligned": "down", "votes": 4}
        ok, reason = rg.gate("long", self._scan("상승국면"), symbol_row=row)
        self.assertFalse(ok)
        self.assertIn("반대", reason)

    def test_symbol_check_skipped_when_not_strict(self):
        row = {"aligned": "down", "votes": 4}
        ok, _ = rg.gate("long", self._scan("상승국면"), symbol_row=row, strict=False)
        self.assertTrue(ok)

    def test_unjudgeable_blocks(self):
        ok, _ = rg.gate("long", self._scan("판정불가"))
        self.assertFalse(ok)


class TestSnapshotsAndTurns(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        self.path = self.tmp.name

    def tearDown(self):
        for p in (self.path, self.path + ".tmp"):
            if os.path.exists(p):
                os.remove(p)

    def _scan(self, date, aligned):
        return {
            "date": date, "label": "상승우위", "up_ratio": 0.5,
            "up_aligned": 1, "down_aligned": 1, "total": 2, "skipped": 0,
            "rows": [{"symbol": s, "coin": s.split("/")[0], "aligned": a,
                      "votes": 3, "states": {}, "covered": 4}
                     for s, a in aligned.items()],
        }

    def test_records_and_detects_turn(self):
        rg.record_snapshot(self._scan("2026-08-01", {"A/USDT": "down"}), self.path)
        today = self._scan("2026-08-02", {"A/USDT": "up"})
        turns = rg.detect_turns(today, self.path)
        self.assertEqual(len(turns["today"]), 1)
        self.assertEqual(turns["today"][0]["from"], "down")
        self.assertEqual(turns["today"][0]["to"], "up")

    def test_no_turn_when_unchanged(self):
        rg.record_snapshot(self._scan("2026-08-01", {"A/USDT": "up"}), self.path)
        turns = rg.detect_turns(self._scan("2026-08-02", {"A/USDT": "up"}), self.path)
        self.assertEqual(turns["today"], [])

    def test_empty_history_is_safe(self):
        turns = rg.detect_turns(self._scan("2026-08-02", {"A/USDT": "up"}), self.path)
        self.assertEqual(turns["today"], [])
        self.assertEqual(turns["recent"], {})

    def test_overlap_requires_recent_turn(self):
        turns = {"recent": {"A/USDT": "2026-08-01"}}
        scan = self._scan("2026-08-02", {"A/USDT": "up", "B/USDT": "up"})
        ov = rg.find_overlap(scan, turns)
        self.assertEqual([o["symbol"] for o in ov], ["A/USDT"])

    def test_overlap_excludes_unaligned(self):
        turns = {"recent": {"A/USDT": "2026-08-01"}}
        scan = self._scan("2026-08-02", {"A/USDT": None})
        self.assertEqual(rg.find_overlap(scan, turns), [])


class TestReport(unittest.TestCase):
    def test_renders_without_optional_sections(self):
        scan = {"date": "2026-08-07", "label": "관망", "note": "n",
                "up_ratio": 0.4, "total": 10, "up_aligned": 2,
                "down_aligned": 2, "skipped": 1, "rows": []}
        out = rg.get_report(scan)
        self.assertIn("관망", out)
        self.assertIn("판정 제외", out)


if __name__ == "__main__":
    unittest.main()
