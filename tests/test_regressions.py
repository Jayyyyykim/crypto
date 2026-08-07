"""2차 점검에서 찾은 버그들의 회귀 테스트.

전부 '조용히 틀린' 부류였다. 예외도 안 나고 표도 그럴듯하게 그려지는데
숫자의 뜻이 달라지는 것들이라, 고친 뒤에 다시 들어오지 않게 못 박아 둔다.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import expected_range
import level_map
import setup_ledger
from helpers import make_df


class TestClusterDoesNotChain(unittest.TestCase):
    """level_map.cluster_levels — 사슬 병합.

    직전 값과 비교하면 tol 안의 걸음이 계속 이어져 클러스터가 tol보다
    훨씬 넓어진다. 그러면 '여러 번 닿은 자리'가 아니라 '넓은 구간'이 되고,
    레벨이라는 개념 자체가 사라진다.
    """

    def test_walking_pivots_do_not_merge_into_one(self):
        # 0.5%씩 떨어진 피벗 8개 (전체 폭 3.6%), 허용오차는 0.6%
        pivots = [(i, 100.0 * (1.005 ** i)) for i in range(8)]
        levels = level_map.cluster_levels(pivots, total_bars=100, tol=0.006)
        self.assertGreater(len(levels), 1, "3.6% 폭이 레벨 하나로 뭉쳤다")

    def test_cluster_count_scales_with_span(self):
        """전체 폭이 허용오차의 N배면 클러스터도 대략 그만큼 나와야 한다.

        사슬 병합이면 폭과 무관하게 1개가 나온다 — 그게 이 테스트가 잡는 것.
        """
        tol = 0.006
        pivots = [(i, 100.0 * (1.005 ** i)) for i in range(20)]
        span = pivots[-1][1] / pivots[0][1] - 1          # 약 9.9%
        levels = level_map.cluster_levels(pivots, total_bars=100, tol=tol)
        self.assertGreaterEqual(len(levels), int(span / (2 * tol)))

    def test_no_single_cluster_swallows_everything(self):
        tol = 0.006
        pivots = [(i, 100.0 * (1.005 ** i)) for i in range(20)]
        levels = level_map.cluster_levels(pivots, total_bars=100, tol=tol)
        self.assertLess(max(l["touches"] for l in levels), len(pivots))

    def test_genuinely_close_pivots_still_merge(self):
        """고치면서 반대로 너무 잘게 쪼개지면 그것도 문제다."""
        pivots = [(1, 100.0), (5, 100.2), (9, 100.4), (20, 150.0)]
        levels = level_map.cluster_levels(pivots, total_bars=30, tol=0.006)
        self.assertEqual(len(levels), 2)
        big = [l for l in levels if l["price"] < 120][0]
        self.assertEqual(big["touches"], 3)


class TestExpectedRangeDirection(unittest.TestCase):
    """expected_range.check_trade_levels — 숏에서 상·하단이 뒤바뀌던 문제.

    이 모듈의 존재 이유가 "코인은 위아래 폭이 다르다"인데, 숏에서 두 폭을
    바꿔 쓰면 경고의 근거가 반대가 된다.
    """

    def _skewed_df(self):
        """아래 꼬리가 위 꼬리보다 훨씬 긴 계열 (상단 좁고 하단 넓음)."""
        closes = [100.0 * (1.001 ** i) for i in range(200)]
        return make_df([(c, c * 1.005, c * 0.97, c) for c in closes])

    def setUp(self):
        self.er = expected_range.expected_range(self._skewed_df())
        self.assertIsNotNone(self.er)
        self.base = self.er["base"]
        self.up_room = (self.er["upper"] - self.base) / self.base
        self.dn_room = (self.base - self.er["lower"]) / self.base
        # 전제: 위아래 폭이 실제로 다르다
        self.assertGreater(self.dn_room, self.up_room * 2)

    def test_short_stop_is_measured_against_upper_room(self):
        e = self.base
        w = expected_range.check_trade_levels(self.er, e, e * 1.002, e * 0.90)
        stop_warn = [x for x in w if x.startswith("손절")]
        self.assertTrue(stop_warn)
        self.assertIn("상단", stop_warn[0])
        self.assertNotIn("하단", stop_warn[0])

    def test_long_stop_is_measured_against_lower_room(self):
        e = self.base
        w = expected_range.check_trade_levels(self.er, e, e * 0.998, e * 1.10)
        stop_warn = [x for x in w if x.startswith("손절")]
        self.assertTrue(stop_warn)
        self.assertIn("하단", stop_warn[0])

    def test_short_target_is_measured_against_lower_room(self):
        e = self.base
        # 목표를 하단 여유의 3배 아래로 — 하단 기준으로 재야 경고가 맞다
        w = expected_range.check_trade_levels(
            self.er, e, e * 1.05, e * (1 - self.dn_room * 3))
        tp_warn = [x for x in w if x.startswith("TP1")]
        self.assertTrue(tp_warn)
        self.assertIn("하단", tp_warn[0])

    def test_short_wide_stop_is_not_falsely_warned(self):
        """상단 여유를 넉넉히 넘는 숏 손절은 경고 대상이 아니다.

        예전 코드는 (더 넓은) 하단 여유와 비교해서 이걸 잘못 잡았다.
        """
        e = self.base
        sl = e * (1 + self.up_room * 0.9)      # 상단 여유의 90% → 절반 초과
        self.assertLess(abs(e - sl) / e, self.dn_room * 0.5)   # 옛 기준이면 경고
        w = expected_range.check_trade_levels(self.er, e, sl, e * 0.95)
        self.assertFalse([x for x in w if x.startswith("손절")])

    def test_direction_falls_back_to_target(self):
        e = self.base
        w = expected_range.check_trade_levels(self.er, e, None, e * 0.5)
        self.assertTrue([x for x in w if x.startswith("TP1")])


class LedgerCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "led.json")

    def tearDown(self):
        self.dir.cleanup()

    def _event(self, eid, result, etype="discount_zone", date="2026-01-01"):
        return {
            "id": eid, "symbol": "A/USDT", "coin": "A", "timeframe": "1d",
            "type": etype, "direction": "up", "date": date, "ref_price": 100,
            "atr_pct": 0.02, "regime": None, "source": "live", "created_at": 0,
            "scores": {"3": {"move_pct": 0.05 if result == "hit" else 0.0,
                             "result": result, "fav_pct": 0.05, "adv_pct": 0.01}},
        }


class TestSampleGateCountsDecisions(LedgerCase):
    """setup_ledger.stats — 보합까지 세어 표본 게이트를 통과시키던 문제.

    보합은 분자에도 분모에도 안 들어간다. 그걸 표본으로 세면 "표본 45건"이라
    적어 놓고 실제로는 판정 5건짜리 100%를 싣게 된다.
    """

    def test_flat_heavy_setup_is_excluded(self):
        evs = [self._event(f"h{i}", "hit", date=f"2026-01-{i+1:02d}")
               for i in range(5)]
        evs += [self._event(f"f{i}", "flat", date=f"2026-02-{i+1:02d}")
                for i in range(40)]
        setup_ledger.merge(evs, None, path=self.path)
        rows = setup_ledger.stats(3, path=self.path, min_samples=20)["rows"]
        self.assertEqual(rows, [])

    def test_enough_decisions_still_pass(self):
        evs = [self._event(f"h{i}", "hit", date=f"2026-01-{i+1:02d}")
               for i in range(25)]
        evs += [self._event(f"f{i}", "flat", date=f"2026-03-{i+1:02d}")
                for i in range(5)]
        setup_ledger.merge(evs, None, path=self.path)
        rows = setup_ledger.stats(3, path=self.path, min_samples=20)["rows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["decided"], 25)
        self.assertEqual(rows[0]["flat"], 5)


class TestBaselineIsIdempotent(LedgerCase):
    """setup_ledger.merge — 소급 채점을 두 번 돌리면 기준선이 두 배가 되던 문제.

    사건은 id로 중복 제거되는데 기준선만 가산됐다. 초과 적중률
    (= 적중률 − 기준선)이 이 저장소의 핵심 숫자라 여기가 틀어지면 결론이
    통째로 흔들린다.
    """

    BASE = {3: {"up": 700, "down": 300, "flat": 50}}

    def test_rerunning_same_source_does_not_double(self):
        setup_ledger.merge([], path=self.path,
                           baseline_keys={"A/USDT|1d": self.BASE})
        first = dict(setup_ledger._load(self.path)["baseline"]["3"])
        setup_ledger.merge([], path=self.path,
                           baseline_keys={"A/USDT|1d": self.BASE})
        second = setup_ledger._load(self.path)["baseline"]["3"]
        self.assertEqual(first, second)

    def test_different_sources_still_add_up(self):
        setup_ledger.merge([], path=self.path,
                           baseline_keys={"A/USDT|1d": self.BASE})
        setup_ledger.merge([], path=self.path,
                           baseline_keys={"B/USDT|1d": self.BASE})
        b = setup_ledger._load(self.path)["baseline"]["3"]
        self.assertEqual(b["up"], 1400)
        self.assertEqual(b["down"], 600)

    def test_reload_does_not_inflate(self):
        """파일을 다시 읽기만 해도 늘어나면 안 된다."""
        setup_ledger.merge([], path=self.path,
                           baseline_keys={"A/USDT|1d": self.BASE})
        a = dict(setup_ledger._load(self.path)["baseline"]["3"])
        setup_ledger._save(setup_ledger._load(self.path), self.path)
        b = setup_ledger._load(self.path)["baseline"]["3"]
        self.assertEqual(a, b)

    def test_baseline_rate_unaffected_by_rerun(self):
        for _ in range(3):
            setup_ledger.merge([], path=self.path,
                               baseline_keys={"A/USDT|1d": self.BASE})
        data = setup_ledger._load(self.path)
        self.assertAlmostEqual(
            setup_ledger.baseline_rate(data["baseline"], 3, "up"), 0.7)

    def test_legacy_flat_baseline_still_read(self):
        """예전 형식(키 없는 baseline)으로 저장된 파일도 그대로 읽힌다."""
        setup_ledger._save({"events": [], "baseline": {"3": {"up": 60, "down": 40}}},
                           self.path)
        data = setup_ledger._load(self.path)
        self.assertAlmostEqual(
            setup_ledger.baseline_rate(data["baseline"], 3, "up"), 0.6)

    def test_legacy_and_keyed_combine(self):
        setup_ledger._save({"events": [], "baseline": {"3": {"up": 60, "down": 40}}},
                           self.path)
        setup_ledger.merge([], path=self.path,
                           baseline_keys={"A/USDT|1d": {3: {"up": 40, "down": 60}}})
        b = setup_ledger._load(self.path)["baseline"]["3"]
        self.assertEqual(b["up"], 100)
        self.assertEqual(b["down"], 100)


class TestBackfillUniverseIsIdempotent(LedgerCase):
    """backfill_universe를 두 번 돌려도 기준선이 그대로여야 한다."""

    def _df(self):
        bars = []
        px = 100.0
        for i in range(200):
            px = px * (1.004 if i % 3 else 0.997)
            bars.append((px, px * 1.02, px * 0.98, px))
        return make_df(bars)

    def test_double_backfill_keeps_baseline(self):
        df = self._df()

        def get_ohlcv(symbol, tf, limit=None):
            return df

        setup_ledger.backfill_universe(["A/USDT"], get_ohlcv, timeframe="1d",
                                       path=self.path)
        first = dict(setup_ledger._load(self.path)["baseline"].get("3", {}))
        n1 = len(setup_ledger._load(self.path)["events"])

        setup_ledger.backfill_universe(["A/USDT"], get_ohlcv, timeframe="1d",
                                       path=self.path)
        second = setup_ledger._load(self.path)["baseline"].get("3", {})
        n2 = len(setup_ledger._load(self.path)["events"])

        self.assertTrue(first, "기준선이 비어 있으면 이 테스트가 무의미하다")
        self.assertEqual(first, second)
        self.assertEqual(n1, n2)


if __name__ == "__main__":
    unittest.main()
