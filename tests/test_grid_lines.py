import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import grid_lines as gl
from helpers import make_df


def zigzag_to(lows, pad=8):
    """지정한 저점들을 순서대로 찍는 연속 지그재그 봉 계열.

    저점 사이마다 위로 솟는 꼭짓점을 넣어 각 저점이 유일한 국소 최소가 되게
    한다. 솟는 폭은 저점 간 최대 간격의 1.5배 — 이보다 작으면 다음 저점이
    직전 꼭짓점보다 높아져서 지그재그가 아니라 단조 상승이 된다.

    평평한 구간(같은 값이 연달아)을 만들지 않는 것이 중요하다. 동점이 생기면
    그 봉들이 전부 피벗으로 잡혀 앵커가 오염된다.
    """
    steps = [abs(b - a) for a, b in zip(lows, lows[1:])] or [10.0]
    span = max(steps) * 1.5

    nodes = []
    for lo in lows:
        nodes += [lo, lo + span]

    prices = []
    for a, b in zip(nodes, nodes[1:]):
        for k in range(pad):
            prices.append(a + (b - a) * k / pad)
    prices.append(nodes[-1])

    return [(p, p + 0.5, p - 0.1, p) for p in prices]


class TestBuildGrid(unittest.TestCase):
    def test_needs_two_anchors(self):
        self.assertIsNone(gl.build_grid([(0, 100.0)]))

    def test_zero_unit_rejected(self):
        self.assertIsNone(gl.build_grid([(0, 100.0), (5, 100.0)]))

    def test_tiny_unit_rejected(self):
        """간격이 1%면 선이 다닥다닥 붙어 격자가 의미 없다."""
        self.assertIsNone(gl.build_grid([(0, 100.0), (5, 101.0)]))

    def test_lines_are_evenly_spaced(self):
        g = gl.build_grid([(0, 100.0), (5, 110.0), (9, 120.0)])
        self.assertEqual(g["unit"], 10.0)
        self.assertAlmostEqual(g["lines"][0.0], 100.0)
        self.assertAlmostEqual(g["lines"][1.0], 110.0)
        self.assertAlmostEqual(g["lines"][-1.0], 90.0)
        self.assertAlmostEqual(g["lines"][0.5], 105.0)

    def test_anchors_recorded(self):
        g = gl.build_grid([(0, 100.0), (5, 110.0), (9, 120.0)])
        self.assertEqual(len(g["anchors"]), 3)
        self.assertEqual(g["anchors"][0]["index"], 0)


class TestFitScore(unittest.TestCase):
    def test_anchors_excluded_from_denominator(self):
        """앵커 자신이 분모에 들어가면 점수가 공짜로 오른다."""
        g = gl.build_grid([(0, 100.0), (5, 110.0), (9, 120.0)])
        # 앵커만 있으면 채점할 피벗이 없다
        self.assertEqual(gl.fit_score(g, [(0, 100.0), (5, 110.0), (9, 120.0)]), 0.0)

    def test_pivots_on_lines_score_high(self):
        g = gl.build_grid([(0, 100.0), (5, 110.0), (9, 120.0)])
        # 앵커를 뺀 나머지(115·90·105)가 전부 격자선 위 — 격자는 80~120이라
        # 그 밖의 값을 넣으면 당연히 안 맞는다.
        pivots = [(0, 100.0), (5, 110.0), (9, 120.0),
                  (12, 115.0), (15, 90.0), (18, 105.0)]
        self.assertAlmostEqual(gl.fit_score(g, pivots), 1.0)

    def test_pivots_off_lines_score_low(self):
        g = gl.build_grid([(0, 100.0), (5, 110.0), (9, 120.0)])
        pivots = [(0, 100.0), (5, 110.0), (9, 120.0),
                  (12, 133.7), (15, 87.1), (18, 102.6)]
        self.assertLess(gl.fit_score(g, pivots), 0.5)

    def test_empty_inputs(self):
        self.assertEqual(gl.fit_score(None, [(1, 100.0)]), 0.0)
        self.assertEqual(gl.fit_score({"lines": {}, "anchors": []}, []), 0.0)


class TestNearestLine(unittest.TestCase):
    def test_picks_closest_and_reports_side(self):
        g = gl.build_grid([(0, 100.0), (5, 110.0)])
        above = gl.nearest_line(g, 109.0)
        self.assertEqual(above["number"], 1.0)
        self.assertEqual(above["side"], "현재가 위")
        self.assertAlmostEqual(above["gap"], 1.0 / 109.0)

        below = gl.nearest_line(g, 111.0)
        self.assertEqual(below["side"], "현재가 아래")

    def test_none_on_bad_input(self):
        self.assertIsNone(gl.nearest_line(None, 100.0))
        self.assertIsNone(gl.nearest_line(gl.build_grid([(0, 100.0), (5, 110.0)]), 0))


class TestAnalyse(unittest.TestCase):
    def test_none_on_short_history(self):
        self.assertIsNone(gl.analyse(make_df(zigzag_to([100, 110])[:20])))

    def test_regular_ladder_is_detected(self):
        """저점이 100·110·120·130으로 규칙적이면 적합도가 나와야 한다."""
        df = make_df(zigzag_to([100, 110, 120, 130, 140, 150, 160, 170, 180, 190]))
        g = gl.analyse(df, price=141.0, basis="low")
        self.assertIsNotNone(g)
        self.assertGreaterEqual(g["fit"], gl.MIN_FIT)
        self.assertEqual(g["basis_kr"], "저점기준")

    def test_irregular_lows_are_rejected(self):
        """간격이 제멋대로면 격자를 내지 않는다 — 그림만 그리지 않기 위해."""
        df = make_df(zigzag_to([100, 137, 112, 189, 143, 166, 121, 204, 158, 131]))
        self.assertIsNone(gl.analyse(df, price=150.0, basis="low"))

    def test_anchors_returned_for_chart_check(self):
        df = make_df(zigzag_to([100, 110, 120, 130, 140, 150, 160, 170, 180, 190]))
        g = gl.analyse(df, price=141.0, basis="low")
        self.assertEqual(len(g["anchors"]), 3)
        for a in g["anchors"]:
            self.assertIn("index", a)
            self.assertIn("price", a)

    def test_min_fit_gate_can_be_relaxed(self):
        df = make_df(zigzag_to([100, 137, 112, 189, 143, 166, 121, 204, 158, 131]))
        self.assertIsNone(gl.analyse(df, price=150.0, basis="low"))
        self.assertIsNotNone(gl.analyse(df, price=150.0, basis="low", min_fit=0.0))

    def test_best_basis_picks_higher_fit(self):
        df = make_df(zigzag_to([100, 110, 120, 130, 140, 150, 160, 170, 180, 190]))
        g = gl.best_basis(df, price=141.0)
        self.assertIsNotNone(g)
        self.assertIn(g["basis"], ("low", "high"))


class TestScanUniverse(unittest.TestCase):
    def _fetch(self, df):
        def get_ohlcv(symbol, tf, limit=None):
            return df
        return get_ohlcv

    def test_far_from_line_is_filtered(self):
        df = make_df(zigzag_to([100, 110, 120, 130, 140, 150, 160, 170, 180, 190]))
        rows = gl.scan_universe(["A/USDT"], self._fetch(df), timeframes=("1w",),
                                get_price_fn=lambda s: 1000.0, max_gap=0.03)
        self.assertEqual(rows, [])

    def test_near_line_is_kept_and_sorted(self):
        df = make_df(zigzag_to([100, 110, 120, 130, 140, 150, 160, 170, 180, 190]))
        rows = gl.scan_universe(["A/USDT"], self._fetch(df), timeframes=("1w",),
                                get_price_fn=lambda s: 140.5, max_gap=0.05)
        self.assertTrue(rows)
        self.assertLessEqual(rows[0]["nearest"]["gap"], 0.05)

    def test_survives_broken_fetch(self):
        def bad(symbol, tf, limit=None):
            raise RuntimeError("boom")
        self.assertEqual(gl.scan_universe(["X/USDT"], bad, timeframes=("1w",)), [])


class TestReports(unittest.TestCase):
    def test_single_report_includes_fit_and_anchors(self):
        df = make_df(zigzag_to([100, 110, 120, 130, 140, 150, 160, 170, 180, 190]))
        r = gl.get_report("A/USDT", df, price=141.0)
        self.assertIn("구조 적합도", r)
        self.assertIn("그은 점", r)

    def test_report_when_no_grid(self):
        df = make_df(zigzag_to([100, 137, 112, 189, 143, 166, 121, 204, 158, 131]))
        self.assertIn("격자를 세울 만한 구조가 없어요", gl.get_report("A/USDT", df))

    def test_line_numbers_formatted(self):
        self.assertEqual(gl._fmt_line_no(0.0), "0번선")
        self.assertEqual(gl._fmt_line_no(1.0), "1번선")
        self.assertEqual(gl._fmt_line_no(-0.5), "-0.5번선")

    def test_scan_report_empty(self):
        self.assertIn("없어요", gl.get_scan_report([]))


if __name__ == "__main__":
    unittest.main()
