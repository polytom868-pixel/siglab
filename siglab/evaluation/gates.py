"""
Gate conditions for research evaluation.

Provides 10+ gate conditions that determine whether a strategy spec passes
pre/post-audit checks. Used by ``runner.py`` after backtest windows are
aggregated.

Assertions fulfilled: VAL-EVAL-005
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from siglab.track_registry import resolve_track


def evaluate_gates(track: str, summary: dict[str, Any]) -> tuple[bool, list[str]]:
    """
    Evaluate all gates for a given track and evaluation summary.

    Returns (passed: bool, reasons: list[str]) where ``passed`` is True only
    when all gates pass, and ``reasons`` lists the human-readable tags of
    any failing gates.
    """
    track = cast(str, resolve_track(track))
    reasons: list[str] = []

    # ---- Liquidation gate ------------------------------------------------
    if int(summary.get("liquidation_count", 0)) > 0:
        reasons.append("liquidation")

    # ---- Return gates ----------------------------------------------------
    if float(summary.get("median_total_return", 0.0)) <= 0.0:
        reasons.append("non_positive_median_return")
    if float(summary.get("median_sharpe", 0.0)) <= 0.0:
        reasons.append("non_positive_median_sharpe")

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

    # ---- Data freshness gate ---------------------------------------------
    # Fail if the backtest data is more than 1 hour stale
    bundle_as_of = summary.get("bundle_as_of")
    if bundle_as_of is not None:
        try:
            if isinstance(bundle_as_of, str):
                data_ts = datetime.fromisoformat(bundle_as_of)
            else:
                data_ts = datetime.fromisoformat(str(bundle_as_of))
            age_seconds = (datetime.now(UTC) - data_ts).total_seconds()
            if age_seconds > 3600:
                reasons.append(f"stale_data_{int(age_seconds)}s")
        except (ValueError, TypeError):
            reasons.append("unparseable_data_timestamp")

    # ---- Lookahead bias gate ---------------------------------------------
    leak_checks = summary.get("leak_checks_passed")
    if leak_checks is not None and not bool(leak_checks):
        reasons.append("lookahead_bias_detected")

    # ---- Position sizing sanity gate -------------------------------------
    # Verify that a position-sizing configuration file exists on disk
    config_path = summary.get("position_sizing_config_path")
    if config_path is not None:
        resolved = Path(str(config_path)).expanduser().resolve()
        if not resolved.exists():
            reasons.append(f"position_sizing_config_missing:{resolved}")

    return not reasons, reasons
