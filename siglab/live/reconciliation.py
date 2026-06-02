"""
Backtest vs paper PnL reconciliation engine.

Compares a backtest PnL series with a paper trading PnL series over
overlapping time windows and produces divergence metrics:

* **Correlation coefficient** — how closely the two PnL series move together
* **Tracking error** — standard deviation of the return differences
* **Bias** — mean of the return differences (backtest minus paper)

When tracking error exceeds a configurable threshold the engine emits a
divergence warning.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_DIVERGENCE_WARNING_THRESHOLD: float = 0.05  # 5 % tracking error


# ---------------------------------------------------------------------------
# ReconciliationEngine
# ---------------------------------------------------------------------------


class ReconciliationEngine:
    """Compare backtest and paper PnL series.

    Parameters
    ----------
    divergence_threshold : float, optional
        Tracking error above this level triggers a divergence warning.
        Default: 0.05 (5 %).
    """

    def __init__(self, divergence_threshold: float | None = None) -> None:
        self.divergence_threshold = (
            divergence_threshold
            if divergence_threshold is not None
            else DEFAULT_DIVERGENCE_WARNING_THRESHOLD
        )

    def compare(
        self,
        backtest_pnl: pd.Series,
        paper_pnl: pd.Series,
    ) -> dict[str, Any]:
        """Compare backtest and paper PnL over overlapping time windows.

        Parameters
        ----------
        backtest_pnl : pd.Series
            Time-indexed backtest PnL (returns).  Index must be
            datetime-like.
        paper_pnl : pd.Series
            Time-indexed paper trading PnL (returns).  Index must be
            datetime-like.

        Returns
        -------
        dict
            Result dictionary with keys:

            * ``overlapping_periods`` — number of common time points
            * ``correlation`` — Pearson correlation of overlapping returns
            * ``tracking_error`` — std(backtest_returns - paper_returns)
            * ``bias`` — mean(backtest_returns - paper_returns)
            * ``divergence_warning`` — true when tracking error > threshold
            * ``start_date`` — start of overlapping window
            * ``end_date`` — end of overlapping window
        """
        # Align on overlapping index
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

        bt = backtest_pnl.loc[common_idx].values.astype(float)
        pt = paper_pnl.loc[common_idx].values.astype(float)

        # --- Correlation coefficient -----------------------------------------
        if np.std(bt) > 0 and np.std(pt) > 0:
            correlation = float(np.corrcoef(bt, pt)[0, 1])
        else:
            correlation = None

        # --- Tracking error --------------------------------------------------
        diff = bt - pt
        tracking_error = float(np.std(diff, ddof=1))

        # --- Bias ------------------------------------------------------------
        bias = float(np.mean(diff))

        # --- Divergence warning ----------------------------------------------
        divergence_warning = tracking_error > self.divergence_threshold if tracking_error is not None else False

        # --- Date range ------------------------------------------------------
        start_date = str(common_idx[0])
        end_date = str(common_idx[-1])

        return {
            "overlapping_periods": len(common_idx),
            "correlation": correlation,
            "tracking_error": round(tracking_error, 6) if tracking_error is not None else None,
            "bias": round(bias, 6) if bias is not None else None,
            "divergence_warning": divergence_warning,
            "start_date": start_date,
            "end_date": end_date,
            "divergence_threshold": self.divergence_threshold,
        }
