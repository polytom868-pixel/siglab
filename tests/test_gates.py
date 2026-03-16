from __future__ import annotations

import unittest

from wayfinder_autolab.evaluator.gates import evaluate_gates


class GateTests(unittest.TestCase):
    def test_negative_canonical_return_fails_even_when_selector_metrics_are_positive(self) -> None:
        passed, reasons = evaluate_gates(
            "directional_perps",
            {
                "liquidation_count": 0,
                "median_total_return": 0.02,
                "median_sharpe": 0.8,
                "validation_available": True,
                "validation_total_return": 0.01,
                "validation_sharpe": 0.5,
                "canonical_series_valid": True,
                "pre_audit_canonical_total_return": -0.18,
                "worst_max_drawdown": -0.2,
                "asset_breadth": 2,
            },
        )

        self.assertFalse(passed)
        self.assertIn("non_positive_pre_audit_canonical_return", reasons)

    def test_positive_canonical_return_does_not_add_extra_gate_reason(self) -> None:
        passed, reasons = evaluate_gates(
            "directional_perps",
            {
                "liquidation_count": 0,
                "median_total_return": 0.02,
                "median_sharpe": 0.8,
                "validation_available": True,
                "validation_total_return": 0.01,
                "validation_sharpe": 0.5,
                "canonical_series_valid": True,
                "pre_audit_canonical_total_return": 0.04,
                "worst_max_drawdown": -0.2,
                "asset_breadth": 2,
            },
        )

        self.assertTrue(passed)
        self.assertNotIn("non_positive_pre_audit_canonical_return", reasons)


if __name__ == "__main__":
    unittest.main()
