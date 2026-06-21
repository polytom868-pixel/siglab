"""Shared risk computation utilities for dashboard routes and WebSocket streams."""

from __future__ import annotations

import logging
import pickle
import time
from pathlib import Path
from typing import Any

import numpy as np

from siglab.risk.guardian import (
    compute_composite_score,
    correlation_matrix,
    current_drawdown,
    max_drawdown,
    recovery_time,
    track_drawdown_events,
    _normalize_sharpe_score,
    _normalize_drawdown_score,
    _normalize_concentration_score,
    _normalize_correlation_score,
)

logger = logging.getLogger(__name__)

STALE_THRESHOLD_SECONDS = 7 * 24 * 3600  # 7 days


def load_equity_curves(sessions_dir: Path) -> list[tuple[str, np.ndarray]]:
    """Load all .npy session files and extract equity curves.

    Returns a list of (session_name, equity_array) pairs.
    """
    npy_files = sorted(sessions_dir.glob("*.npy"))
    curves: list[tuple[str, np.ndarray]] = []
    for npy_file in npy_files:
        mtime = npy_file.stat().st_mtime
        if (time.time() - mtime) > STALE_THRESHOLD_SECONDS:
            logger.warning("Session %s is stale (last modified %ds ago), skipping", npy_file.stem, int(time.time() - mtime))
            continue
        try:
            data = np.load(npy_file, allow_pickle=True)
            if isinstance(data, np.ndarray) and data.size > 0:
                if data.dtype.names is not None and "equity" in data.dtype.names:
                    eq = data["equity"]
                    if isinstance(eq, np.ndarray) and eq.size > 0:
                        curves.append((npy_file.stem, eq.astype(float)))
                elif data.dtype in (np.float64, np.float32):
                    curves.append((npy_file.stem, data))
        except (OSError, ValueError, TypeError, pickle.UnpicklingError):
            logger.debug("Failed to load npy equity curve %s", npy_file)
            continue
    return curves


def empty_risk_response() -> dict[str, Any]:
    """Return an empty risk response with all fields set to None/empty."""
    return {
        "composite_score": None,
        "max_drawdown": None,
        "correlation_matrix": None,
        "strategy_count": 0,
        "strategy_names": [],
        "sub_scores": {},
        "current_drawdown": None,
        "recovery_periods": None,
        "drawdown_history": [],
        "alerts": [],
        "sharpe_ratio": 0.0,
    }


def compute_risk_metrics(sessions_dir: Path, *, periods_per_year: int = 365) -> dict[str, Any]:
    """Compute full risk metrics from session data.

    Returns a dict with composite_score, max_drawdown, correlation_matrix,
    sub_scores, drawdown_history, strategy_names, alerts, sharpe_ratio,
    current_drawdown, recovery_periods, and strategy_count.
    """
    curves = load_equity_curves(sessions_dir)
    if not curves:
        return empty_risk_response()

    session_names = [name for name, _ in curves]
    equity_arrays = [eq for _, eq in curves]

    # Drawdown metrics from worst equity curve across all sessions
    if equity_arrays:
        all_max_dds = [float(max_drawdown(eq)) for eq in equity_arrays]
        all_cur_dds = [float(current_drawdown(eq)) for eq in equity_arrays]
        max_dd = min(all_max_dds)  # most negative = worst
        cur_dd = min(all_cur_dds)
        worst_idx = all_max_dds.index(max_dd)
        rec_time = recovery_time(equity_arrays[worst_idx])
    else:
        max_dd = cur_dd = 0.0
        rec_time = None
        worst_idx = 0

    # Drawdown history for sparkline (from worst session)
    first_eq = equity_arrays[worst_idx] if equity_arrays else np.array([])
    if first_eq.size > 0:
        peak = np.maximum.accumulate(first_eq)
        with np.errstate(divide="ignore", invalid="ignore"):
            dd_series = np.where(peak > 0, (first_eq - peak) / peak, 0.0)
        n = len(dd_series)
        if n > 60:
            step = n / 60
            dd_history: list[float] = [float(dd_series[int(i * step)]) for i in range(60)]
        else:
            dd_history = dd_series.tolist()
    else:
        dd_history = []

    # Compute returns for all equity curves
    returns_list = []
    for eq in equity_arrays:
        if eq.size >= 2:
            rets = np.diff(eq) / np.where(eq[:-1] != 0, eq[:-1], 1.0)
            returns_list.append(rets)

    # Sharpe ratio (per-strategy average, frequency-aware)
    sharpe = 0.0
    if returns_list:
        sharpes = []
        for rets in returns_list:
            s = np.std(rets, ddof=1)
            if s > 0:
                sharpes.append(float(np.mean(rets) / s * np.sqrt(periods_per_year)))
        sharpe = float(np.mean(sharpes)) if sharpes else 0.0

    # Correlation matrix
    corr_matrix: list[list[float]] | None = None
    if len(returns_list) >= 2:
        matrix = correlation_matrix(returns_list)
        if matrix.size > 0:
            corr_matrix = matrix.tolist()

    # Average pairwise correlation
    avg_corr = 0.0
    if corr_matrix is not None and len(corr_matrix) >= 2:
        num = len(corr_matrix)
        corr_values = []
        for i in range(num):
            for j in range(i + 1, num):
                corr_values.append(corr_matrix[i][j])
        avg_corr = float(np.mean(corr_values)) if corr_values else 0.0

    # Concentration from HHI
    n = len(returns_list)
    hhi = 1.0 / n if n > 0 else 1.0
    concentration = 1.0 - hhi  # 0.0 = max concentration (1 strat), approaches 1.0 as N grows

    # Sub-scores
    sub_scores = {
        "sharpe": _normalize_sharpe_score(sharpe),
        "drawdown": _normalize_drawdown_score(max_dd),
        "concentration": _normalize_concentration_score(concentration),
        "correlation_risk": _normalize_correlation_score(avg_corr),
    }

    # Composite score
    composite: float | None = None
    composite = float(compute_composite_score(
        sharpe=sharpe,
        drawdown=max_dd,
        concentration=concentration,
        correlation_risk=avg_corr,
    ))

    # Alerts from drawdown events (worst session)
    alerts: list[dict[str, Any]] = []
    worst_eq = equity_arrays[worst_idx] if equity_arrays and worst_idx < len(equity_arrays) else np.array([])
    events = track_drawdown_events(worst_eq) if worst_eq.size > 0 else []
    for event in events[-20:]:
        sev = "warning" if abs(event.max_drawdown_pct) < 0.15 else "critical"
        alerts.append({
            "timestamp": event.trough_date,
            "metric": "drawdown",
            "severity": sev,
            "value": event.max_drawdown_pct,
            "threshold": 0.0,
            "message": (
                f"Drawdown {event.max_drawdown_pct * 100:.1f}% "
                f"({event.peak_date} → {event.trough_date})"
            ),
        })

    return {
        "composite_score": composite,
        "max_drawdown": max_dd,
        "correlation_matrix": corr_matrix,
        "strategy_count": len(equity_arrays),
        "strategy_names": session_names,
        "sub_scores": sub_scores,
        "current_drawdown": cur_dd,
        "recovery_periods": rec_time,
        "drawdown_history": dd_history,
        "alerts": alerts,
        "sharpe_ratio": sharpe,
    }
