"""
Tests for ``siglab.live.reconciliation``.

Covers:
- VAL-PAPER-009: Backtest vs paper PnL reconciliation produces divergence metrics
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from siglab.live.reconciliation import ReconciliationEngine


# ======================================================================
# VAL-PAPER-009: Reconciliation produces expected metrics
# ======================================================================


class TestReconciliation:
    """VAL-PAPER-009: Backtest vs paper PnL reconciliation produces divergence metrics."""

    @pytest.fixture
    def engine(self) -> ReconciliationEngine:
        return ReconciliationEngine(divergence_threshold=0.05)

    def test_correlation_one_for_identical_series(self, engine: ReconciliationEngine) -> None:
        """Identical series → correlation = 1.0."""
        dates = pd.date_range("2026-01-01", periods=100, freq="D")
        returns = np.random.default_rng(42).normal(0.001, 0.01, 100)
        bt = pd.Series(returns, index=dates)
        pt = pd.Series(returns, index=dates)

        result = engine.compare(bt, pt)
        assert result["correlation"] == pytest.approx(1.0, abs=1e-10)
        assert result["overlapping_periods"] == 100
        assert result["tracking_error"] == pytest.approx(0.0, abs=1e-10)
        assert result["bias"] == pytest.approx(0.0, abs=1e-10)
        assert result["divergence_warning"] is False

    def test_correlation_in_range(self, engine: ReconciliationEngine) -> None:
        """Correlation is in [-1, 1]."""
        dates = pd.date_range("2026-01-01", periods=100, freq="D")
        rng = np.random.default_rng(42)
        bt = pd.Series(rng.normal(0.001, 0.01, 100), index=dates)
        pt = pd.Series(rng.normal(0.001, 0.01, 100), index=dates)

        result = engine.compare(bt, pt)
        assert result["correlation"] is not None
        assert -1.0 <= result["correlation"] <= 1.0

    def test_tracking_error_non_negative(self, engine: ReconciliationEngine) -> None:
        """Tracking error is non-negative."""
        dates = pd.date_range("2026-01-01", periods=100, freq="D")
        rng = np.random.default_rng(42)
        bt = pd.Series(rng.normal(0.001, 0.01, 100), index=dates)
        pt = pd.Series(rng.normal(0.002, 0.015, 100), index=dates)

        result = engine.compare(bt, pt)
        assert result["tracking_error"] is not None
        assert result["tracking_error"] >= 0.0

    def test_bias_present(self, engine: ReconciliationEngine) -> None:
        """Bias reflects mean difference between series."""
        dates = pd.date_range("2026-01-01", periods=100, freq="D")
        rng = np.random.default_rng(42)
        noise = rng.normal(0, 0.005, 100)
        bt = pd.Series(noise + 0.001, index=dates)
        pt = pd.Series(noise, index=dates)  # Paper is consistently lower

        result = engine.compare(bt, pt)
        assert result["bias"] is not None
        assert result["bias"] > 0  # bt > pt on average

    def test_divergence_warning_on_high_tracking_error(self) -> None:
        """Divergence warning when tracking error exceeds threshold."""
        engine = ReconciliationEngine(divergence_threshold=0.01)
        dates = pd.date_range("2026-01-01", periods=100, freq="D")
        rng = np.random.default_rng(42)
        bt = pd.Series(rng.normal(0, 0.001, 100), index=dates)
        pt = pd.Series(rng.normal(0, 0.05, 100), index=dates)  # Much more volatile

        result = engine.compare(bt, pt)
        assert result["divergence_warning"] is True
        assert result["tracking_error"] > 0.01

    def test_no_divergence_warning_when_below_threshold(self, engine: ReconciliationEngine) -> None:
        """No divergence warning when tracking error below threshold."""
        dates = pd.date_range("2026-01-01", periods=100, freq="D")
        rng = np.random.default_rng(42)
        bt = pd.Series(rng.normal(0, 0.01, 100), index=dates)
        pt = pd.Series(rng.normal(0, 0.01, 100), index=dates)

        result = engine.compare(bt, pt)
        # Tracking error should be small
        assert result["tracking_error"] is not None
        if result["tracking_error"] <= 0.05:
            assert result["divergence_warning"] is False

    def test_all_three_metrics_present(self, engine: ReconciliationEngine) -> None:
        """Correlation, tracking error, and bias are all present."""
        dates = pd.date_range("2026-01-01", periods=50, freq="D")
        bt = pd.Series(np.random.default_rng(42).normal(0.001, 0.01, 50), index=dates)
        pt = pd.Series(np.random.default_rng(42).normal(0.001, 0.01, 50), index=dates)

        result = engine.compare(bt, pt)
        assert "correlation" in result
        assert "tracking_error" in result
        assert "bias" in result
        assert result["correlation"] is not None
        assert result["tracking_error"] is not None
        assert result["bias"] is not None

    def test_insufficient_overlap(self, engine: ReconciliationEngine) -> None:
        """Fewer than 2 overlapping periods returns note."""
        dates1 = pd.date_range("2026-01-01", periods=5, freq="D")
        dates2 = pd.date_range("2026-02-01", periods=5, freq="D")  # No overlap
        bt = pd.Series([0.01] * 5, index=dates1)
        pt = pd.Series([0.01] * 5, index=dates2)

        result = engine.compare(bt, pt)
        assert result["overlapping_periods"] < 2
        assert result["correlation"] is None
        assert result["tracking_error"] is None
        assert result["bias"] is None
        assert "note" in result

    def test_overlapping_periods_counted(self, engine: ReconciliationEngine) -> None:
        """Overlapping periods count is correct."""
        dates = pd.date_range("2026-01-01", periods=30, freq="D")
        bt = pd.Series(np.random.default_rng(42).normal(0.001, 0.01, 30), index=dates)
        pt = pd.Series(np.random.default_rng(42).normal(0.001, 0.01, 20), index=dates[:20])

        result = engine.compare(bt, pt)
        assert result["overlapping_periods"] == 20

    def test_start_and_end_dates(self, engine: ReconciliationEngine) -> None:
        """Start and end dates of overlap reported."""
        dates = pd.date_range("2026-01-10", periods=50, freq="D")
        bt = pd.Series(np.random.default_rng(42).normal(0.001, 0.01, 50), index=dates)
        pt = pd.Series(np.random.default_rng(42).normal(0.001, 0.01, 50), index=dates)

        result = engine.compare(bt, pt)
        assert result["start_date"] == "2026-01-10 00:00:00"
        assert "end_date" in result

    def test_known_values(self, engine: ReconciliationEngine) -> None:
        """Hand-calculated known values."""
        dates = pd.date_range("2026-01-01", periods=5, freq="D")
        bt = pd.Series([0.01, 0.02, -0.01, 0.03, 0.01], index=dates)
        pt = pd.Series([0.005, 0.015, -0.005, 0.025, 0.005], index=dates)

        result = engine.compare(bt, pt)

        # diff = [0.005, 0.005, -0.005, 0.005, 0.005]
        diff = np.array([0.005, 0.005, -0.005, 0.005, 0.005])
        expected_bias = float(np.mean(diff))
        expected_te = float(np.std(diff, ddof=1))

        assert result["bias"] == pytest.approx(expected_bias, abs=1e-6)
        assert result["tracking_error"] == pytest.approx(expected_te, abs=1e-6)

    def test_divergence_threshold_configurable(self) -> None:
        """Divergence threshold is configurable."""
        engine = ReconciliationEngine(divergence_threshold=0.10)
        assert engine.divergence_threshold == 0.10

    def test_results_have_expected_keys(self, engine: ReconciliationEngine) -> None:
        """Result dict has all expected keys."""
        dates = pd.date_range("2026-01-01", periods=5, freq="D")
        bt = pd.Series([0.01] * 5, index=dates)
        pt = pd.Series([0.01] * 5, index=dates)

        result = engine.compare(bt, pt)
        expected_keys = {
            "overlapping_periods", "correlation", "tracking_error", "bias",
            "divergence_warning", "start_date", "end_date",
        }
        assert expected_keys.issubset(result.keys())
