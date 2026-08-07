import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import call_journal as cj


class JournalCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "j.jsonl")

    def tearDown(self):
        self.dir.cleanup()

    def _lines(self):
        with open(self.path, encoding="utf-8") as f:
            return [l for l in f.read().splitlines() if l.strip()]

    def _write(self, lines):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")


class TestChain(JournalCase):
    def test_empty_journal_verifies(self):
        ok, msg = cj.verify(self.path)
        self.assertTrue(ok)
        self.assertIn("0건", msg)

    def test_first_entry_links_to_genesis(self):
        e = cj.publish("BTC", 1, 100.0, "6/6봉 정렬", path=self.path)
        self.assertEqual(e["prev"], cj.GENESIS)
        self.assertEqual(e["seq"], 0)

    def test_chain_links_forward(self):
        a = cj.publish("BTC", 1, 100.0, "근거", path=self.path)
        b = cj.publish("ETH", -1, 50.0, "근거", path=self.path)
        self.assertEqual(b["prev"], a["hash"])
        self.assertTrue(cj.verify(self.path)[0])

    def test_key_order_does_not_break_chain(self):
        """dict 순서가 바뀌어도 같은 내용이면 같은 해시여야 한다."""
        cj.publish("BTC", 1, 100.0, "근거", path=self.path)
        line = json.loads(self._lines()[0])
        # payload 키 순서를 뒤집어 다시 쓴다
        line["payload"] = dict(reversed(list(line["payload"].items())))
        self._write([json.dumps(line, ensure_ascii=False)])
        self.assertTrue(cj.verify(self.path)[0])


class TestTamperDetection(JournalCase):
    def test_edit_is_detected(self):
        cj.publish("BTC", 1, 100.0, "근거", path=self.path)
        cj.publish("ETH", -1, 50.0, "근거", path=self.path)

        lines = self._lines()
        lines[0] = lines[0].replace('"발행가": 100.0', '"발행가": 999.0')
        self._write(lines)

        ok, msg = cj.verify(self.path)
        self.assertFalse(ok)
        self.assertIn("0번째", msg)
        self.assertIn("바뀌었습니다", msg)

    def test_delete_is_detected(self):
        for i in range(3):
            cj.publish(f"S{i}", 1, 100.0, "근거", path=self.path)
        lines = self._lines()
        self._write([lines[0], lines[2]])       # 가운데를 지운다

        ok, msg = cj.verify(self.path)
        self.assertFalse(ok)
        self.assertIn("지워졌습니다", msg)

    def test_reordering_is_detected(self):
        for i in range(3):
            cj.publish(f"S{i}", 1, 100.0, "근거", path=self.path)
        lines = self._lines()
        self._write([lines[0], lines[2], lines[1]])
        self.assertFalse(cj.verify(self.path)[0])

    def test_appending_forged_entry_is_detected(self):
        cj.publish("BTC", 1, 100.0, "근거", path=self.path)
        forged = {"seq": 1, "at": "2026-01-01T00:00:00", "kind": "발행",
                  "payload": {"종목": "가짜"}, "prev": "0" * 64, "hash": "x" * 64}
        self._write(self._lines() + [json.dumps(forged, ensure_ascii=False)])
        self.assertFalse(cj.verify(self.path)[0])


class TestScoringIsAppendOnly(JournalCase):
    def test_score_does_not_touch_original(self):
        e = cj.publish("BTC", 1, 100.0, "근거", path=self.path)
        cj.record_score(e["seq"], 3, "적중", 110.0, path=self.path)

        entries = cj.load(self.path)
        self.assertEqual(entries[0]["payload"]["발행가"], 100.0)
        self.assertEqual(entries[1]["payload"]["발행_seq"], e["seq"])
        self.assertTrue(cj.verify(self.path)[0])

    def test_scores_for_filters_by_call(self):
        a = cj.publish("BTC", 1, 100.0, "근거", path=self.path)
        b = cj.publish("ETH", 1, 50.0, "근거", path=self.path)
        cj.record_score(a["seq"], 3, "적중", 110.0, path=self.path)
        cj.record_score(b["seq"], 3, "미적중", 45.0, path=self.path)

        self.assertEqual(len(cj.scores_for(a["seq"], self.path)), 1)
        self.assertEqual(cj.scores_for(a["seq"], self.path)[0]["payload"]["결과"], "적중")

    def test_pending_lists_unscored_horizons(self):
        e = cj.publish("BTC", 1, 100.0, "근거", path=self.path)
        cj.record_score(e["seq"], 1, "적중", 105.0, path=self.path)
        pend = cj.pending_calls([1, 3, 7], self.path)
        self.assertEqual(len(pend), 1)
        self.assertEqual(pend[0]["missing"], [3, 7])

    def test_fully_scored_call_is_not_pending(self):
        e = cj.publish("BTC", 1, 100.0, "근거", path=self.path)
        for h in (1, 3):
            cj.record_score(e["seq"], h, "적중", 105.0, path=self.path)
        self.assertEqual(cj.pending_calls([1, 3], self.path), [])


class TestCriteria(JournalCase):
    def test_criteria_changes_are_tracked(self):
        cj.register_criteria({"flat_band": 0.01}, path=self.path)
        cj.register_criteria({"flat_band": 0.02}, path=self.path)
        hist = cj.criteria_history(self.path)
        self.assertEqual(len(hist), 2)
        self.assertEqual(hist[0]["payload"]["flat_band"], 0.01)
        self.assertEqual(hist[-1]["payload"]["flat_band"], 0.02)

    def test_report_warns_when_no_criteria(self):
        cj.publish("BTC", 1, 100.0, "근거", path=self.path)
        self.assertIn("채점 기준이 아직 없습니다", cj.get_report(path=self.path))

    def test_report_warns_when_criteria_changed(self):
        cj.register_criteria({"v": 1}, path=self.path)
        cj.register_criteria({"v": 2}, path=self.path)
        self.assertIn("2번", cj.get_report(path=self.path))


class TestTally(JournalCase):
    def _fill(self, n, result="적중"):
        for i in range(n):
            e = cj.publish(f"S{i}", 1, 100.0, "근거", path=self.path)
            cj.record_score(e["seq"], 3, result, 110.0, path=self.path)

    def test_below_min_samples_is_not_publishable(self):
        self._fill(5)
        t = cj.tally(3, self.path, min_samples=30)
        self.assertFalse(t["publishable"])
        self.assertIn("아직 싣지 않습니다", cj.get_report(3, self.path))

    def test_above_min_samples_publishes(self):
        self._fill(30)
        t = cj.tally(3, self.path, min_samples=30)
        self.assertTrue(t["publishable"])
        self.assertAlmostEqual(t["hit_rate"], 1.0)

    def test_flat_excluded_from_denominator(self):
        for i in range(10):
            e = cj.publish(f"S{i}", 1, 100.0, "근거", path=self.path)
            cj.record_score(e["seq"], 3, "보합", 100.2, path=self.path)
        t = cj.tally(3, self.path)
        self.assertEqual(t["decided"], 0)
        self.assertEqual(t["flat"], 10)
        self.assertIsNone(t["hit_rate"])

    def test_horizons_do_not_mix(self):
        e = cj.publish("BTC", 1, 100.0, "근거", path=self.path)
        cj.record_score(e["seq"], 1, "적중", 110.0, path=self.path)
        cj.record_score(e["seq"], 3, "미적중", 90.0, path=self.path)
        self.assertEqual(cj.tally(1, self.path)["hit"], 1)
        self.assertEqual(cj.tally(3, self.path)["hit"], 0)


class TestExport(JournalCase):
    def test_plaintext_includes_reason_and_scores(self):
        e = cj.publish("BTC", 1, 100.0, "6/6봉 정렬", setups=["디스카운트 구간에서"],
                       invalidation=95.0, path=self.path)
        cj.record_score(e["seq"], 3, "적중", 110.0, path=self.path)
        txt = cj.export_plaintext(self.path)
        self.assertIn("6/6봉 정렬", txt)
        self.assertIn("디스카운트 구간에서", txt)
        self.assertIn("적중", txt)

    def test_reset_removes_file(self):
        cj.publish("BTC", 1, 100.0, "근거", path=self.path)
        cj.reset(self.path)
        self.assertEqual(cj.load(self.path), [])


if __name__ == "__main__":
    unittest.main()
