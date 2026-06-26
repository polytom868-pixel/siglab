"""Backtest vs paper PnL reconciliation engine."""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)
DEFAULT_DIVERGENCE_WARNING_THRESHOLD: float = 0.05


class ReconciliationEngine:
    """Compare backtest and paper PnL series."""

    def __init__(self, divergence_threshold: float | None = None) -> None:
        self.divergence_threshold = (
            divergence_threshold
            if divergence_threshold is not None
            else DEFAULT_DIVERGENCE_WARNING_THRESHOLD
        )

    def compare(self, backtest_pnl: pd.Series, paper_pnl: pd.Series) -> dict[str, Any]:
        """Compare backtest and paper PnL over overlapping time windows."""
        common_idx = backtest_pnl.index.intersection(paper_pnl.index)
        if len(common_idx) < 2:
            return {
                "overlapping_periods": len(common_idx),
                "correlation": None,
                "tracking_error": None,
                "bias": None,
                "divergence_warning": False,
                "start_date": None,
                "end_date": None,
                "note": "Insufficient overlapping periods for reconciliation",
            }
        bt = np.asarray(backtest_pnl.loc[common_idx].values, dtype=float)
        pt = np.asarray(paper_pnl.loc[common_idx].values, dtype=float)
        if float(np.std(bt)) > 0 and float(np.std(pt)) > 0:
            correlation = float(
                np.corrcoef(np.asarray(bt, dtype=float), np.asarray(pt, dtype=float))[
                    0, 1,
                ],
            )
        else:
            correlation = None
        diff = np.asarray(bt, dtype=float) - np.asarray(pt, dtype=float)
        tracking_error = float(np.std(diff, ddof=1))
        bias = float(np.mean(diff))
        divergence_warning = (
            tracking_error > self.divergence_threshold
            if tracking_error is not None
            else False
        )
        start_date = str(common_idx[0])
        end_date = str(common_idx[-1])
        return {
            "overlapping_periods": len(common_idx),
            "correlation": correlation,
            "tracking_error": round(tracking_error, 6)
            if tracking_error is not None
            else None,
            "bias": round(bias, 6) if bias is not None else None,
            "divergence_warning": divergence_warning,
            "start_date": start_date,
            "end_date": end_date,
            "divergence_threshold": self.divergence_threshold,
        }
