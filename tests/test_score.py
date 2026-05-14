from __future__ import annotations

import unittest

from siglab.evaluator.score import summarize_window_results


class ScoreSummaryTests(unittest.TestCase):
    def test_aggregate_score_caps_explosive_calmar_component(self) -> None:
        summary = summarize_window_results(
            window_results=[
                {
                    "stats": {
                        "sharpe": 4.0,
                        "total_return": 0.05,
                        "cagr": 1e30,
                        "calmar": 1e30,
                        "max_drawdown": -0.2,
                    },
                    "liquidated": False,
                }
            ],
            asset_breadth=4,
        )

        self.assertLess(summary["aggregate_score"], 40.0)
        self.assertEqual(summary["median_calmar"], 1e30)
        self.assertEqual(summary["score_component_caps"]["median_calmar"], 50.0)


if __name__ == "__main__":
    unittest.main()
