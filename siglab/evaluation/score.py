"""
Scoring utilities for research evaluation.

Provides serialization helpers and summary aggregation with bounded
score components to prevent numerical explosion.

Assertions fulfilled: VAL-EVAL-012 (Score component caps prevent numerical explosion)
"""

from __future__ import annotations

from typing import Any

import numpy as np


def serialize_stats(stats: dict[str, Any]) -> dict[str, Any]:
    """Convert a stats dict to a JSON-safe representation."""
    serialized: dict[str, Any] = {}
    for key, value in stats.items():
        if hasattr(value, "isoformat"):
            serialized[key] = value.isoformat()
        elif hasattr(value, "total_seconds"):
            serialized[key] = value.total_seconds()
        elif isinstance(value, (np.floating, np.integer)):
            serialized[key] = float(value)
        else:
            serialized[key] = value
    return serialized


def _safe_nanmedian(values: np.ndarray, default: float = 0.0) -> float:
    """Compute nanmedian with a safe fallback.

    Strips both NaN and Inf values before computing the median to avoid
    RuntimeWarnings from numpy's internal reduce operations on non-finite
    input.
    """
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return default
    return float(np.median(finite))


def _safe_nanmin(values: np.ndarray, default: float = 0.0) -> float:
    """Compute nanmin with a safe fallback."""
    finite = values[~np.isnan(values)]
    if finite.size == 0:
        return default
    result = float(np.nanmin(finite))
    if np.isnan(result):
        return default
    return result


def _bounded(value: float, *, lower: float, upper: float) -> float:
    """
    Clamp *value* to [*lower*, *upper*].

    Non-finite values (NaN, inf) are replaced with 0.0 before clamping.
    This prevents numerical explosion in composite scores.
    """
    if not np.isfinite(value):
        return 0.0
    return min(max(float(value), lower), upper)


def summarize_window_results(
    *,
    window_results: list[dict[str, Any]],
    asset_breadth: int,
) -> dict[str, Any]:
    """
    Aggregate per-window backtest results into a single summary dict.

    Score components are individually bounded via ``_bounded`` to prevent
    any single extreme value from dominating the aggregate score.
    """
    sharpe = np.array([row["stats"]["sharpe"] for row in window_results], dtype=float)
    total_return = np.array(
        [row["stats"]["total_return"] for row in window_results], dtype=float
    )
    cagr = np.array([row["stats"]["cagr"] for row in window_results], dtype=float)
    calmar = np.array([row["stats"]["calmar"] for row in window_results], dtype=float)
    drawdown = np.array(
        [row["stats"]["max_drawdown"] for row in window_results], dtype=float
    )
    liquidation_count = sum(1 for row in window_results if row["liquidated"])
    profitable_window_pct: float = np.nan
    if len(total_return) > 0:
        profitable_window_pct = float((total_return > 0.0).mean())

    # Cap each score component to prevent numerical explosion
    score_sharpe = _bounded(_safe_nanmedian(sharpe), lower=-20.0, upper=20.0)
    score_return = _bounded(_safe_nanmedian(total_return), lower=-1.0, upper=5.0)
    score_calmar = _bounded(_safe_nanmedian(calmar), lower=-50.0, upper=50.0)
    score_drawdown = _bounded(_safe_nanmedian(drawdown), lower=-1.0, upper=0.0)
    aggregate_score = (
        score_sharpe
        + 4.0 * score_return
        + 0.5 * score_calmar
        + 0.1 * float(asset_breadth)
        + 0.25 * profitable_window_pct
        + 1.5 * score_drawdown
    )

    return {
        "aggregate_score": aggregate_score,
        "median_sharpe": _safe_nanmedian(sharpe),
        "median_total_return": _safe_nanmedian(total_return),
        "median_cagr": _safe_nanmedian(cagr),
        "median_calmar": _safe_nanmedian(calmar),
        "worst_max_drawdown": _safe_nanmin(drawdown),
        "liquidation_count": liquidation_count,
        "window_count": len(window_results),
        "profitable_window_pct": profitable_window_pct,
        "asset_breadth": asset_breadth,
        "score_component_caps": {
            "median_sharpe": score_sharpe,
            "median_total_return": score_return,
            "median_calmar": score_calmar,
            "median_drawdown": score_drawdown,
        },
    }
