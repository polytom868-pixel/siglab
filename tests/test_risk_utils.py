"""Unit tests for siglab.dashboard.risk_utils error paths."""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pytest

from siglab.dashboard.risk_utils import (
    compute_risk_metrics,
    empty_risk_response,
    load_equity_curves,
)


class TestLoadEquityCurves:
    """Tests for load_equity_curves() error and edge cases."""

    def test_no_npy_files_returns_empty_list(self) -> None:
        """When sessions_dir has no .npy files, returns empty list."""
        with tempfile.TemporaryDirectory() as tmp:
            curves = load_equity_curves(Path(tmp))
            assert curves == []

    def test_corrupted_npy_gracefully_skipped(self) -> None:
        """A corrupted .npy file is silently skipped."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # Write garbage bytes to simulate corruption
            bad_file = tmp_path / "corrupted.npy"
            bad_file.write_bytes(b"not a valid npy file at all")

            curves = load_equity_curves(tmp_path)
            assert curves == []


class TestEmptyRiskResponse:
    """Tests for empty_risk_response() shape."""

    def test_has_all_required_keys(self) -> None:
        """empty_risk_response() returns dict with all required keys."""
        resp = empty_risk_response()
        required_keys = {
            "composite_score",
            "max_drawdown",
            "correlation_matrix",
            "strategy_count",
            "strategy_names",
            "sub_scores",
            "current_drawdown",
            "recovery_periods",
            "drawdown_history",
            "alerts",
            "sharpe_ratio",
        }
        assert required_keys.issubset(resp.keys()), (
            f"Missing keys: {required_keys - resp.keys()}"
        )

    def test_sharpe_ratio_defaults_to_zero(self) -> None:
        """sharpe_ratio defaults to 0.0 in empty response."""
        assert empty_risk_response()["sharpe_ratio"] == 0.0

    def test_strategy_count_is_zero(self) -> None:
        """strategy_count defaults to 0 in empty response."""
        assert empty_risk_response()["strategy_count"] == 0


class TestComputeRiskMetrics:
    """Tests for compute_risk_metrics() error and happy paths."""

    def test_no_data_returns_empty_risk_response(self) -> None:
        """When sessions_dir has no .npy files, returns empty_risk_response()."""
        with tempfile.TemporaryDirectory() as tmp:
            result = compute_risk_metrics(Path(tmp))
            expected = empty_risk_response()
            assert result == expected

    def test_valid_data_returns_correct_fields(self) -> None:
        """With valid equity data, returns correct Sharpe, composite, drawdown."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            equity = [1.0, 1.1, 1.2, 1.15, 1.3, 1.25, 1.4]
            np.save(str(tmp_path / "session.npy"), np.array(equity, dtype=np.float64))

            result = compute_risk_metrics(tmp_path)

            # Should not be the empty response
            assert result["strategy_count"] == 1
            assert result["strategy_names"] == ["session"]

            # Sharpe should be positive (upward trending data)
            assert isinstance(result["sharpe_ratio"], float)
            assert result["sharpe_ratio"] != 0.0

            # Composite score should be a float
            assert isinstance(result["composite_score"], float)
            assert 0.0 <= result["composite_score"] <= 1.0

            # Max drawdown should be non-positive
            assert isinstance(result["max_drawdown"], float)
            assert result["max_drawdown"] <= 0.0

            # Current drawdown
            assert isinstance(result["current_drawdown"], float)

            # Drawdown history should be non-empty
            assert len(result["drawdown_history"]) > 0

            # Alerts list (may be empty for low-drawdown data)
            assert isinstance(result["alerts"], list)

            # Sub-scores
            assert "sharpe" in result["sub_scores"]
            assert "drawdown" in result["sub_scores"]
            assert "concentration" in result["sub_scores"]
            assert "correlation_risk" in result["sub_scores"]
