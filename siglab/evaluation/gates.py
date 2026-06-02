"""
Gate conditions for research evaluation.

Provides 10+ gate conditions that determine whether a strategy spec passes
pre/post-audit checks. Used by ``runner.py`` after backtest windows are
aggregated.

Assertions fulfilled: VAL-EVAL-005
"""

from __future__ import annotations

from typing import Any

from siglab.track_registry import canonical_track_name


def evaluate_gates(track: str, summary: dict[str, Any]) -> tuple[bool, list[str]]:
    """
    Evaluate all gates for a given track and evaluation summary.

    Returns (passed: bool, reasons: list[str]) where ``passed`` is True only
    when all gates pass, and ``reasons`` lists the human-readable tags of
    any failing gates.
    """
    track = canonical_track_name(track) or track
    reasons: list[str] = []

    # ---- Liquidation gate ------------------------------------------------
    if int(summary.get("liquidation_count", 0)) > 0:
        reasons.append("liquidation")

    # ---- Return gates ----------------------------------------------------
    if float(summary.get("median_total_return", 0.0)) <= 0.0:
        reasons.append("non_positive_median_return")
    if float(summary.get("median_sharpe", 0.0)) <= 0.0:
        reasons.append("non_positive_median_sharpe")

    # ---- Validation gates ------------------------------------------------
    if bool(summary.get("validation_available")):
        if float(summary.get("validation_total_return", 0.0)) <= 0.0:
            reasons.append("non_positive_validation_return")
        if float(summary.get("validation_sharpe", 0.0)) <= 0.0:
            reasons.append("non_positive_validation_sharpe")

    # ---- Pre-audit canonical gates ---------------------------------------
    pre_audit_canonical_total_return = summary.get("pre_audit_canonical_total_return")
    if (
        pre_audit_canonical_total_return is not None
        and float(pre_audit_canonical_total_return) <= 0.0
    ):
        reasons.append("non_positive_pre_audit_canonical_return")
    if not bool(summary.get("canonical_series_valid", True)):
        reasons.append("invalid_canonical_series")

    # ---- Drawdown gate ---------------------------------------------------
    drawdown_limit = -0.35 if track == "trend_signals" else -0.25
    if float(summary.get("worst_max_drawdown", 0.0)) < drawdown_limit:
        reasons.append("drawdown_limit")

    # ---- Breadth gate ----------------------------------------------------
    breadth = int(summary.get("asset_breadth", 0))
    if breadth < 2 and track == "trend_signals":
        reasons.append("insufficient_breadth")
    if breadth < 1 and track == "yield_flows":
        reasons.append("insufficient_breadth")

    return not reasons, reasons
