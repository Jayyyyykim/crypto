import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import risk_calc as rc


class TestPlan(unittest.TestCase):
    def test_size_is_set_by_stop_distance(self):
        """손절이 좁을수록 수량이 커진다 — 걸린 돈은 같다."""
        tight = rc.plan(10_000_000, 100.0, 99.0, 110.0, risk_pct=1.0)
        wide = rc.plan(10_000_000, 100.0, 90.0, 110.0, risk_pct=1.0)
        self.assertEqual(tight["risk_amount"], wide["risk_amount"])
        self.assertEqual(tight["risk_amount"], 100_000)
        self.assertGreater(tight["quantity"], wide["quantity"])

    def test_direction_inferred_from_stop(self):
        self.assertEqual(rc.plan(1e7, 100.0, 95.0, 110.0)["side"], 1)
        self.assertEqual(rc.plan(1e7, 100.0, 105.0, 90.0)["side"], -1)

    def test_short_uses_same_formula(self):
        p = rc.plan(10_000_000, 100.0, 105.0, 90.0, risk_pct=1.0)
        self.assertAlmostEqual(p["quantity"], 100_000 / 5)
        self.assertGreater(p["rr"], 0)

    def test_zero_stop_distance_returns_none(self):
        self.assertIsNone(rc.plan(1e7, 100.0, 100.0, 110.0))

    def test_bad_inputs_return_none(self):
        self.assertIsNone(rc.plan(0, 100.0, 95.0, 110.0))
        self.assertIsNone(rc.plan(1e7, 0, 95.0, 110.0))
        self.assertIsNone(rc.plan(1e7, 100.0, None, 110.0))

    def test_fees_reduce_rr(self):
        free = rc.plan(1e7, 100.0, 95.0, 110.0, fee_pct=0.0)
        paid = rc.plan(1e7, 100.0, 95.0, 110.0, fee_pct=0.5)
        self.assertLess(paid["rr"], free["rr"])

    def test_breakeven_matches_rr(self):
        p = rc.plan(1e7, 100.0, 95.0, 110.0, fee_pct=0.0)
        self.assertAlmostEqual(p["breakeven_rate"], 1 / (1 + p["rr"]))
        self.assertAlmostEqual(p["rr"], 2.0, places=6)

    def test_expectancy_needs_win_rate(self):
        self.assertIsNone(rc.plan(1e7, 100.0, 95.0, 110.0)["expectancy_r"])
        p = rc.plan(1e7, 100.0, 95.0, 110.0, win_rate=0.5)
        self.assertIsNotNone(p["expectancy_r"])

    def test_expectancy_sign_flips_with_win_rate(self):
        lo = rc.plan(1e7, 100.0, 95.0, 110.0, win_rate=0.2, fee_pct=0.0)
        hi = rc.plan(1e7, 100.0, 95.0, 110.0, win_rate=0.8, fee_pct=0.0)
        self.assertLess(lo["expectancy_r"], 0)
        self.assertGreater(hi["expectancy_r"], 0)

    def test_verdict_mentions_losing_when_negative(self):
        p = rc.plan(1e7, 100.0, 95.0, 110.0, win_rate=0.1)
        self.assertIn("잃습니다", rc.verdict(p))

    def test_verdict_without_win_rate(self):
        self.assertIn("승률을 모릅니다", rc.verdict(rc.plan(1e7, 100.0, 95.0, 110.0)))


class TestScaled(unittest.TestCase):
    def test_average_entry(self):
        sp = rc.scaled_plan(1e7, [(100.0, 0.5), (96.0, 0.5)], stop=90.0)
        self.assertAlmostEqual(sp["avg_entry"], 98.0)

    def test_weights_are_normalized(self):
        a = rc.scaled_plan(1e7, [(100.0, 1), (96.0, 1)], stop=90.0)
        b = rc.scaled_plan(1e7, [(100.0, 50), (96.0, 50)], stop=90.0)
        self.assertAlmostEqual(a["avg_entry"], b["avg_entry"])

    def test_splitting_does_not_reduce_risk(self):
        """평단이 좋아질 뿐, 걸린 돈은 그대로다."""
        single = rc.plan(1e7, 98.0, 90.0, 110.0, risk_pct=1.0)
        split = rc.scaled_plan(1e7, [(100.0, 0.5), (96.0, 0.5)], stop=90.0,
                               risk_pct=1.0)
        self.assertAlmostEqual(single["risk_amount"], split["risk_amount"])
        self.assertAlmostEqual(single["quantity"], split["quantity"])

    def test_realized_r_positive_when_targets_above(self):
        sp = rc.scaled_plan(1e7, [(100.0, 1)], stop=90.0,
                            exits=[(110.0, 0.5), (120.0, 0.5)])
        self.assertGreater(sp["realized_r"], 0)

    def test_realized_r_none_without_exits(self):
        self.assertIsNone(rc.scaled_plan(1e7, [(100.0, 1)], stop=90.0)["realized_r"])

    def test_bad_inputs(self):
        self.assertIsNone(rc.scaled_plan(1e7, [], stop=90.0))
        self.assertIsNone(rc.scaled_plan(1e7, [(100.0, 0)], stop=90.0))
        self.assertIsNone(rc.scaled_plan(1e7, [(100.0, 1)], stop=100.0))


class TestKelly(unittest.TestCase):
    def test_capped(self):
        self.assertLessEqual(rc.kelly_fraction(0.99, 10.0), rc.KELLY_CAP)

    def test_zero_without_edge(self):
        self.assertEqual(rc.kelly_fraction(0.3, 0.5), 0.0)
        self.assertEqual(rc.kelly_fraction(0.5, 0.0), 0.0)
        self.assertEqual(rc.kelly_fraction(None, 2.0), 0.0)

    def test_grows_with_win_rate(self):
        # 상한(0.25)에 걸리지 않는 구간에서 비교해야 증가가 보인다
        self.assertGreater(rc.kelly_fraction(0.60, 1.0), rc.kelly_fraction(0.55, 1.0))


class TestSuggestRisk(unittest.TestCase):
    def test_halves_when_samples_not_met(self):
        self.assertEqual(rc.suggest_risk_pct(0.9, 3.0, base=1.0,
                                             min_samples_met=False), 0.5)

    def test_halves_when_win_rate_unknown(self):
        self.assertEqual(rc.suggest_risk_pct(None, 3.0, base=1.0), 0.5)

    def test_never_exceeds_cap(self):
        self.assertLessEqual(rc.suggest_risk_pct(0.99, 10.0, base=1.0, cap=2.0), 2.0)


class TestSizeFromEdge(unittest.TestCase):
    def test_zero_edge_is_treated_as_unconfirmed(self):
        """적중률이 높아도 초과가 0 이하면 우위로 치지 않는다."""
        row = {"hit_rate": 0.82, "edge": -0.006, "decided": 300}
        out = rc.size_from_edge(1e7, 100.0, 95.0, 110.0, row)
        self.assertFalse(out["edge_confirmed"])
        self.assertEqual(out["risk_pct"], rc.DEFAULT_RISK_PCT / 2)

    def test_positive_edge_with_samples_is_confirmed(self):
        row = {"hit_rate": 0.62, "edge": 0.09, "decided": 300}
        out = rc.size_from_edge(1e7, 100.0, 95.0, 110.0, row)
        self.assertTrue(out["edge_confirmed"])
        self.assertGreaterEqual(out["risk_pct"], rc.DEFAULT_RISK_PCT / 2)

    def test_small_sample_is_not_confirmed(self):
        row = {"hit_rate": 0.9, "edge": 0.3, "decided": 12}
        out = rc.size_from_edge(1e7, 100.0, 95.0, 110.0, row)
        self.assertFalse(out["edge_confirmed"])

    def test_without_ledger_row_is_halved(self):
        out = rc.size_from_edge(1e7, 100.0, 95.0, 110.0, None)
        self.assertEqual(out["risk_pct"], rc.DEFAULT_RISK_PCT / 2)

    def test_unconfirmed_edge_does_not_feed_expectancy(self):
        """우위를 부정해 놓고 그 적중률로 기대값을 내면 리포트가 자기모순이다."""
        row = {"hit_rate": 0.82, "edge": -0.006, "decided": 326}
        out = rc.size_from_edge(1e7, 100.0, 95.0, 112.0, row)
        self.assertFalse(out["edge_confirmed"])
        self.assertIsNone(out["win_rate"])
        self.assertIsNone(out["expectancy_r"])
        self.assertEqual(out["ledger_hit_rate"], 0.82)   # 참고로는 남는다

    def test_confirmed_edge_does_feed_expectancy(self):
        row = {"hit_rate": 0.62, "edge": 0.09, "decided": 300}
        out = rc.size_from_edge(1e7, 100.0, 95.0, 112.0, row)
        self.assertTrue(out["edge_confirmed"])
        self.assertEqual(out["win_rate"], 0.62)
        self.assertIsNotNone(out["expectancy_r"])


class TestReports(unittest.TestCase):
    def test_report_renders(self):
        r = rc.get_report(rc.plan(1e7, 100.0, 95.0, 110.0, win_rate=0.5))
        self.assertIn("손익비", r)
        self.assertIn("본전 승률", r)

    def test_report_warns_on_unconfirmed_edge(self):
        row = {"hit_rate": 0.82, "edge": -0.01, "decided": 300}
        r = rc.get_report(rc.size_from_edge(1e7, 100.0, 95.0, 110.0, row))
        self.assertIn("우위가 확인되지 않았습니다", r)

    def test_report_handles_none(self):
        self.assertIn("계산할 수 없어요", rc.get_report(None))

    def test_scaled_report_renders(self):
        sp = rc.scaled_plan(1e7, [(100.0, 0.5), (96.0, 0.5)], stop=90.0,
                            exits=[(110.0, 1)])
        self.assertIn("평단", rc.get_scaled_report(sp))


if __name__ == "__main__":
    unittest.main()
