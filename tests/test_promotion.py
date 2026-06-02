"""
Tests for ``siglab.live.promotion``.

Covers:
- VAL-PAPER-007: Composite score computed correctly, sub-scores in [0,1]
- VAL-PAPER-008: Auto-promotion triggered when score > threshold for N consecutive days
- VAL-PAPER-012: Minimum trading days enforced (even perfect score needs min days)
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from siglab.live.promotion import (
    DEFAULT_CONSECUTIVE_DAYS,
    DEFAULT_MIN_TRADING_DAYS,
    DEFAULT_PROMOTION_THRESHOLD,
    DEFAULT_WEIGHTS,
    compute_composite_score,
    compute_sub_scores,
    promotion_eligible,
)


# ======================================================================
# VAL-PAPER-007: Composite score computed correctly
# ======================================================================


class TestCompositeScore:
    """VAL-PAPER-007: Composite score matches hand-calculated value."""

    def test_known_inputs_produce_expected_score(self) -> None:
        """Verify composite score for a known set of inputs."""
        # total_return=0.15 (50% of target 0.30 → pnl_score=0.50)
        # sharpe=1.5 (50% of max 3.0 → sharpe_score=0.50)
        # win_rate=0.6 → win_rate_score=0.60
        # max_drawdown=-0.10 (10% of tolerable -0.30 → dd_score=0.6667)
        metrics = {
            "total_return": 0.15,
            "sharpe": 1.5,
            "win_rate": 0.6,
            "max_drawdown": -0.10,
        }

        scores = compute_sub_scores(metrics)
        # PnL: 0.15 / 0.30 = 0.5
        assert scores["pnl"] == pytest.approx(0.5, abs=1e-4)
        # Sharpe: 1.5 / 3.0 = 0.5
        assert scores["sharpe"] == pytest.approx(0.5, abs=1e-4)
        # Win rate: 0.6
        assert scores["win_rate"] == pytest.approx(0.6, abs=1e-4)
        # Drawdown: 1 - (0.10 / 0.30) = 0.6667
        assert scores["drawdown"] == pytest.approx(0.6667, abs=1e-3)

        # Composite with equal weights (0.25 each)
        composite = compute_composite_score(metrics)
        expected = (0.5 + 0.5 + 0.6 + 0.6667) / 4
        assert composite == pytest.approx(expected, abs=1e-4)

    def test_perfect_score(self) -> None:
        """All maximum scores should yield composite = 1.0."""
        metrics = {
            "total_return": 0.30,  # At target
            "sharpe": 3.0,  # At max
            "win_rate": 1.0,  # Perfect
            "max_drawdown": 0.0,  # No drawdown
        }
        composite = compute_composite_score(metrics)
        assert composite == pytest.approx(1.0, abs=1e-4)

    def test_zero_score(self) -> None:
        """All minimum scores should yield composite = 0.0."""
        metrics = {
            "total_return": 0.0,
            "sharpe": 0.0,
            "win_rate": 0.0,
            "max_drawdown": -0.30,
        }
        composite = compute_composite_score(metrics)
        assert composite == pytest.approx(0.0, abs=1e-4)

    def test_negative_return_zero_pnl_score(self) -> None:
        """Negative total return gives pnl_score = 0."""
        scores = compute_sub_scores({"total_return": -0.10, "sharpe": 1.0, "win_rate": 0.5, "max_drawdown": -0.05})
        assert scores["pnl"] == 0.0

    def test_sharpe_capped_at_max(self) -> None:
        """Sharpe above max gives sharpe_score = 1."""
        scores = compute_sub_scores({"total_return": 0.10, "sharpe": 5.0, "win_rate": 0.5, "max_drawdown": -0.05})
        assert scores["sharpe"] == 1.0

    def test_drawdown_capped_at_max(self) -> None:
        """Drawdown below -0.30 gives drawdown_score = 0."""
        scores = compute_sub_scores({"total_return": 0.10, "sharpe": 1.0, "win_rate": 0.5, "max_drawdown": -0.50})
        assert scores["drawdown"] == 0.0

    def test_custom_weights(self) -> None:
        """Custom weights affect composite score."""
        metrics = {"total_return": 0.15, "sharpe": 1.5, "win_rate": 0.6, "max_drawdown": -0.10}

        # All weight on pnl only
        composite = compute_composite_score(metrics, weights={"pnl": 1.0, "sharpe": 0.0, "win_rate": 0.0, "drawdown": 0.0})
        assert composite == pytest.approx(0.5, abs=1e-4)

    def test_hand_calculation_known_value(self) -> None:
        """Composite score matches hand-calculated value for known inputs."""
        # Known input set
        metrics = {
            "total_return": 0.20,  # → 0.6667
            "sharpe": 2.0,  # → 0.6667
            "win_rate": 0.75,  # → 0.75
            "max_drawdown": -0.05,  # → 0.8333
        }
        scores = compute_sub_scores(metrics)
        assert scores["pnl"] == pytest.approx(0.6667, abs=1e-3)
        assert scores["sharpe"] == pytest.approx(0.6667, abs=1e-3)
        assert scores["win_rate"] == 0.75
        assert scores["drawdown"] == pytest.approx(0.8333, abs=1e-3)

        composite = compute_composite_score(metrics)
        expected = (0.6667 + 0.6667 + 0.75 + 0.8333) / 4
        assert composite == pytest.approx(expected, abs=1e-3)


# ======================================================================
# Each sub-score capped to [0,1] range
# ======================================================================


class TestSubScoresCapped:
    """Each sub-score capped to [0, 1] range."""

    def test_all_sub_scores_in_range(self) -> None:
        """All sub-scores are within [0, 1] for any inputs."""
        # Negative values
        scores = compute_sub_scores({"total_return": -10.0, "sharpe": -100.0, "win_rate": -1.0, "max_drawdown": -10.0})
        for key, val in scores.items():
            assert 0.0 <= val <= 1.0, f"{key} = {val} not in [0, 1]"

        # Extreme positive values
        scores = compute_sub_scores({"total_return": 100.0, "sharpe": 100.0, "win_rate": 5.0, "max_drawdown": 1.0})
        for key, val in scores.items():
            assert 0.0 <= val <= 1.0, f"{key} = {val} not in [0, 1]"

        # NaN-like (zero)
        scores = compute_sub_scores({"total_return": 0.0, "sharpe": 0.0, "win_rate": 0.0, "max_drawdown": 0.0})
        for key, val in scores.items():
            assert 0.0 <= val <= 1.0, f"{key} = {val} not in [0, 1]"

    def test_composite_capped(self) -> None:
        """Even with extreme inputs, composite score stays in [0, 1]."""
        c1 = compute_composite_score({"total_return": -999, "sharpe": -999, "win_rate": -999, "max_drawdown": -999})
        assert 0.0 <= c1 <= 1.0

        c2 = compute_composite_score({"total_return": 999, "sharpe": 999, "win_rate": 999, "max_drawdown": 999})
        assert 0.0 <= c2 <= 1.0


# ======================================================================
# VAL-PAPER-008: Auto-promotion triggered when score > threshold for N consecutive days
# ======================================================================


class TestPromotionEligibility:
    """VAL-PAPER-008: Auto-promotion triggered when composite score > threshold for N consecutive days."""

    @staticmethod
    def _make_daily(score: float, count: int) -> list[dict]:
        """Create *count* days of identical metrics that produce a given composite score."""
        # target_return, sharpe, win_rate, drawdown that produce a known composite
        # We'll just use known metrics
        return [
            {"total_return": 0.20, "sharpe": 2.0, "win_rate": 0.75, "max_drawdown": -0.05}
            for _ in range(count)
        ]

    def test_above_threshold_consecutive_days_passes(self) -> None:
        """Score above threshold for enough consecutive days → eligible."""
        # These metrics produce composite ~0.73 which is > 0.65
        daily = self._make_daily(0.73, DEFAULT_MIN_TRADING_DAYS)
        eligible, reason = promotion_eligible(daily)
        assert eligible, f"Expected eligible, got: {reason}"
        assert "above threshold" in reason

    def test_below_threshold_fails(self) -> None:
        """Score below threshold → not eligible."""
        # Use metrics that produce a low composite score
        daily = [
            {"total_return": 0.01, "sharpe": 0.1, "win_rate": 0.3, "max_drawdown": -0.30}
            for _ in range(DEFAULT_MIN_TRADING_DAYS)
        ]
        eligible, reason = promotion_eligible(daily)
        assert not eligible, f"Expected not eligible, got: {reason}"
        assert "below threshold" in reason

    def test_not_enough_consecutive_days(self) -> None:
        """Not enough consecutive days above threshold → not eligible."""
        # Mix of good and bad days
        good_daily = {"total_return": 0.20, "sharpe": 2.0, "win_rate": 0.75, "max_drawdown": -0.05}
        bad_daily = {"total_return": 0.01, "sharpe": 0.1, "win_rate": 0.3, "max_drawdown": -0.30}

        # 8 good days, 2 bad at the end → last 5 have bad days
        daily = [good_daily] * 8 + [bad_daily] * 2
        eligible, reason = promotion_eligible(daily, consecutive_days=5)
        assert not eligible
        assert "below threshold" in reason

    def test_empty_daily_metrics(self) -> None:
        """No trading data → not eligible."""
        eligible, reason = promotion_eligible([])
        assert not eligible
        assert "No trading data" in reason

    def test_custom_threshold(self) -> None:
        """Custom threshold is respected."""
        # Very high threshold that won't be met
        daily = self._make_daily(0.73, DEFAULT_MIN_TRADING_DAYS)
        eligible, reason = promotion_eligible(daily, threshold=0.95)
        assert not eligible
        assert "below threshold" in reason

    def test_custom_consecutive_days(self) -> None:
        """Custom consecutive days requirement is respected."""
        daily = self._make_daily(0.73, DEFAULT_MIN_TRADING_DAYS)
        # Require more consecutive days than we have
        eligible, reason = promotion_eligible(daily, consecutive_days=DEFAULT_MIN_TRADING_DAYS + 1)
        assert not eligible
        assert "Not enough trading days for consecutive check" in reason


# ======================================================================
# VAL-PAPER-012: Minimum trading days enforced
# ======================================================================


class TestMinimumTradingDays:
    """VAL-PAPER-012: Even with perfect score, session needs >= min_trading_days."""

    @staticmethod
    def _perfect_daily(count: int) -> list[dict]:
        """Create *count* days of metrics that give a perfect composite score."""
        return [
            {"total_return": 0.30, "sharpe": 3.0, "win_rate": 1.0, "max_drawdown": 0.0}
            for _ in range(count)
        ]

    def test_perfect_score_but_too_few_days(self) -> None:
        """Even with perfect score, too few trading days → not eligible."""
        daily = self._perfect_daily(DEFAULT_MIN_TRADING_DAYS - 1)
        eligible, reason = promotion_eligible(daily)
        assert not eligible
        assert "Minimum trading days not met" in reason
        assert str(DEFAULT_MIN_TRADING_DAYS) in reason

    def test_perfect_score_with_min_days(self) -> None:
        """Perfect score AND enough days → eligible."""
        daily = self._perfect_daily(DEFAULT_MIN_TRADING_DAYS)
        eligible, reason = promotion_eligible(daily)
        assert eligible

    def test_custom_min_trading_days(self) -> None:
        """Custom min_trading_days is respected."""
        daily = self._perfect_daily(3)
        # Require 20 days
        eligible, reason = promotion_eligible(daily, min_trading_days=20, consecutive_days=2)
        assert not eligible
        assert "Minimum trading days not met" in reason

        # Require only 2 days (but also need to pass consecutive check)
        eligible, reason = promotion_eligible(daily, min_trading_days=2, consecutive_days=2)
        assert eligible


# ======================================================================
# Score computation helper tests
# ======================================================================


class TestComputeSubScores:
    """Sub-score computation helpers."""

    def test_normalize_pnl(self) -> None:
        from siglab.live.promotion import _normalize_pnl

        assert _normalize_pnl(0.0) == 0.0
        assert _normalize_pnl(-0.1) == 0.0
        assert _normalize_pnl(0.15) == pytest.approx(0.5, abs=1e-4)
        assert _normalize_pnl(0.30) == 1.0
        assert _normalize_pnl(1.0) == 1.0

    def test_normalize_sharpe(self) -> None:
        from siglab.live.promotion import _normalize_sharpe

        assert _normalize_sharpe(0.0) == 0.0
        assert _normalize_sharpe(-1.0) == 0.0
        assert _normalize_sharpe(1.5) == pytest.approx(0.5, abs=1e-4)
        assert _normalize_sharpe(3.0) == 1.0
        assert _normalize_sharpe(10.0) == 1.0

    def test_normalize_win_rate(self) -> None:
        from siglab.live.promotion import _normalize_win_rate

        assert _normalize_win_rate(0.5) == 0.5
        assert _normalize_win_rate(0.0) == 0.0
        assert _normalize_win_rate(1.0) == 1.0
        assert _normalize_win_rate(-0.1) == 0.0
        assert _normalize_win_rate(1.5) == 1.0

    def test_normalize_drawdown(self) -> None:
        from siglab.live.promotion import _normalize_drawdown

        assert _normalize_drawdown(0.0) == 1.0
        assert _normalize_drawdown(0.1) == 1.0
        assert _normalize_drawdown(-0.15) == pytest.approx(0.5, abs=1e-4)
        assert _normalize_drawdown(-0.30) == 0.0
        assert _normalize_drawdown(-0.50) == 0.0
