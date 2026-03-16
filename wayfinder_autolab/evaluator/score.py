from __future__ import annotations

from typing import Any

import numpy as np


def serialize_stats(stats: dict[str, Any]) -> dict[str, Any]:
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
    finite = values[~np.isnan(values)]
    if finite.size == 0:
        return default
    result = float(np.nanmedian(finite))
    if np.isnan(result):
        return default
    return result


def _safe_nanmin(values: np.ndarray, default: float = 0.0) -> float:
    finite = values[~np.isnan(values)]
    if finite.size == 0:
        return default
    result = float(np.nanmin(finite))
    if np.isnan(result):
        return default
    return result


def summarize_window_results(
    *,
    window_results: list[dict[str, Any]],
    asset_breadth: int,
) -> dict[str, Any]:
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
    profitable_window_pct = float((total_return > 0.0).mean())

    aggregate_score = (
        _safe_nanmedian(sharpe)
        + 4.0 * _safe_nanmedian(total_return)
        + 0.5 * _safe_nanmedian(calmar)
        + 0.1 * float(asset_breadth)
        + 0.25 * profitable_window_pct
        + 1.5 * _safe_nanmedian(drawdown)
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
    }
