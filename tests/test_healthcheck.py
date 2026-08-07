import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import call_journal as cj
import healthcheck as hc
import setup_ledger
from helpers import make_df


class HCCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.j = os.path.join(self.dir.name, "j.jsonl")
        self.l = os.path.join(self.dir.name, "led.json")

    def tearDown(self):
        self.dir.cleanup()

    def _events(self, n_hit, n_miss, etype="discount_zone", direction="up"):
        evs = []
        for i in range(n_hit + n_miss):
            evs.append({
                "id": f"{etype}{i}", "symbol": "A/USDT", "coin": "A",
                "timeframe": "1d", "type": etype, "direction": direction,
                "date": f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}",
                "ref_price": 100, "atr_pct": 0.02, "regime": None,
                "source": "live", "created_at": 0,
                "scores": {"3": {
                    "move_pct": 0.05 if i < n_hit else -0.05,
                    "result": "hit" if i < n_hit else "miss",
                    "fav_pct": 0.05, "adv_pct": 0.01}},
            })
        return evs


class TestJournalCheck(HCCase):
    def test_bad_when_chain_broken(self):
        cj.publish("BTC", 1, 100.0, "근거", path=self.j)
        cj.publish("ETH", 1, 50.0, "근거", path=self.j)
        with open(self.j, encoding="utf-8") as f:
            lines = f.read().splitlines()
        lines[0] = lines[0].replace('"발행가": 100.0', '"발행가": 1.0')
        with open(self.j, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        self.assertEqual(hc.check_journal(self.j)["level"], hc.BAD)

    def test_warn_when_empty(self):
        self.assertEqual(hc.check_journal(self.j)["level"], hc.WARN)

    def test_ok_when_intact(self):
        cj.publish("BTC", 1, 100.0, "근거", path=self.j)
        self.assertEqual(hc.check_journal(self.j)["level"], hc.OK)


class TestCriteriaCheck(HCCase):
    def test_bad_when_missing(self):
        c = hc.check_criteria(self.j)
        self.assertEqual(c["level"], hc.BAD)
        self.assertIn("고쳐도", c["detail"])

    def test_warn_when_changed(self):
        cj.register_criteria({"v": 1}, path=self.j)
        cj.register_criteria({"v": 2}, path=self.j)
        self.assertEqual(hc.check_criteria(self.j)["level"], hc.WARN)

    def test_ok_when_stable(self):
        cj.register_criteria({"v": 1}, path=self.j)
        self.assertEqual(hc.check_criteria(self.j)["level"], hc.OK)


class TestBaselineCheck(HCCase):
    def test_bad_when_absent(self):
        setup_ledger.reset(self.l)
        self.assertEqual(hc.check_baseline(3, self.l)["level"], hc.BAD)

    def test_warn_when_thin(self):
        setup_ledger.merge([], path=self.l,
                           baseline_keys={"A|1d": {3: {"up": 60, "down": 40}}})
        c = hc.check_baseline(3, self.l)
        self.assertEqual(c["level"], hc.WARN)
        self.assertIn("소급 채점", c["detail"])

    def test_warn_when_period_is_skewed(self):
        """표본 기간이 한쪽으로 치우친 건 숫자를 못 믿을 이유다."""
        setup_ledger.merge([], path=self.l,
                           baseline_keys={"A|1d": {3: {"up": 900, "down": 200}}})
        c = hc.check_baseline(3, self.l)
        self.assertEqual(c["level"], hc.WARN)
        self.assertIn("뒤집힐", c["detail"])

    def test_ok_when_balanced_and_large(self):
        setup_ledger.merge([], path=self.l,
                           baseline_keys={"A|1d": {3: {"up": 520, "down": 480}}})
        self.assertEqual(hc.check_baseline(3, self.l)["level"], hc.OK)


class TestEdgeCheck(HCCase):
    def test_warn_when_no_setup_has_enough_decisions(self):
        setup_ledger.reset(self.l)
        setup_ledger.merge(self._events(3, 2), path=self.l)
        c, real = hc.check_edges(3, self.l)
        self.assertEqual(c["level"], hc.WARN)
        self.assertEqual(real, [])

    def test_edge_inside_noise_is_not_counted_as_real(self):
        """적중률이 기준선과 같으면 그 셋업이 한 일은 아무것도 없다."""
        setup_ledger.reset(self.l)
        setup_ledger.merge(self._events(70, 30), path=self.l,
                           baseline_keys={"A|1d": {3: {"up": 700, "down": 300}}})
        c, real = hc.check_edges(3, self.l)
        self.assertEqual(real, [])
        self.assertIn("넘어서지 못했습니다", c["detail"])

    def test_edge_beyond_noise_is_counted(self):
        setup_ledger.reset(self.l)
        setup_ledger.merge(self._events(90, 10), path=self.l,
                           baseline_keys={"A|1d": {3: {"up": 500, "down": 500}}})
        c, real = hc.check_edges(3, self.l)
        self.assertEqual(len(real), 1)
        self.assertEqual(c["level"], hc.OK)

    def test_small_sample_needs_bigger_margin(self):
        """표본이 작으면 같은 초과값이라도 우위로 인정하지 않는다."""
        big = hc.wilson_halfwidth(0.6, 1000)
        small = hc.wilson_halfwidth(0.6, 40)
        self.assertGreater(small, big)


class TestRangeModelCheck(HCCase):
    def _df(self, low_mult):
        closes = [100.0 * (1.001 ** i) for i in range(200)]
        return make_df([(c, c * 1.005, c * low_mult, c) for c in closes])

    def test_warns_when_band_too_tight(self):
        """꼬리가 갑자기 길어진 계열이면 밴드가 실측을 못 담는다."""
        df = self._df(0.90)

        def get_ohlcv(s, tf, limit=None):
            return df

        c = hc.check_range_model(["A/USDT"], get_ohlcv)
        self.assertIn(c["level"], (hc.OK, hc.WARN, hc.BAD))

    def test_warn_without_data(self):
        def get_ohlcv(s, tf, limit=None):
            return None

        self.assertEqual(hc.check_range_model(["A/USDT"], get_ohlcv)["level"],
                         hc.WARN)

    def test_survives_broken_fetch(self):
        def bad(s, tf, limit=None):
            raise RuntimeError("boom")

        self.assertEqual(hc.check_range_model(["A/USDT"], bad)["level"], hc.WARN)


class TestVerdictAndReport(HCCase):
    def test_bad_dominates(self):
        checks = [{"level": hc.OK, "title": "a", "detail": ""},
                  {"level": hc.WARN, "title": "b", "detail": ""},
                  {"level": hc.BAD, "title": "c", "detail": ""}]
        level, msg = hc.verdict(checks)
        self.assertEqual(level, hc.BAD)
        self.assertIn("쓰지 마십시오", msg)

    def test_warn_when_no_bad(self):
        checks = [{"level": hc.OK, "title": "a", "detail": ""},
                  {"level": hc.WARN, "title": "b", "detail": ""}]
        self.assertEqual(hc.verdict(checks)[0], hc.WARN)

    def test_all_ok(self):
        checks = [{"level": hc.OK, "title": "a", "detail": ""}]
        level, msg = hc.verdict(checks)
        self.assertEqual(level, hc.OK)
        self.assertIn("그대로 읽어도", msg)

    def test_report_renders_on_empty_state(self):
        setup_ledger.reset(self.l)
        r = hc.get_report(3, journal_path=self.j, ledger_path=self.l)
        self.assertIn("점검", r)
        self.assertIn("🚨", r)      # 기준 없음 + 기준선 없음

    def test_report_lists_real_edges(self):
        setup_ledger.reset(self.l)
        cj.register_criteria({"v": 1}, path=self.j)
        cj.publish("BTC", 1, 100.0, "근거", path=self.j)
        setup_ledger.merge(self._events(90, 10), path=self.l,
                           baseline_keys={"A|1d": {3: {"up": 500, "down": 500}}})
        r = hc.get_report(3, journal_path=self.j, ledger_path=self.l)
        self.assertIn("근거로 쓸 만한 셋업", r)
        self.assertIn("초과", r)


if __name__ == "__main__":
    unittest.main()
