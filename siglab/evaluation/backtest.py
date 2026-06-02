"""
Backtesting engine for research evaluation.

Provides the core ``run_backtest`` function and supporting data structures.
Used by ``runner.py`` to evaluate strategy performance over historical data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class BacktestConfig:
    """Configuration parameters for a single backtest run."""
    leverage: float = 1.0
    funding_rates: pd.DataFrame | None = None
    rebalance_threshold: float = 0.0
    enable_liquidation: bool = True


@dataclass(frozen=True)
class BacktestResult:
    """Result of a single backtest run."""
    equity_curve: pd.Series
    returns: pd.Series
    positions: pd.DataFrame
    trades: list[dict[str, Any]]
    metrics_by_period: pd.DataFrame
    stats: dict[str, Any]
    liquidated: bool = False
    liquidation_timestamp: Any = None


def convert_to_spot(prices: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Convert perp prices to spot-equivalent with zero funding."""
    funding = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
    return prices.copy(), funding


def run_backtest(
    prices: pd.DataFrame,
    target_weights: pd.DataFrame,
    config: BacktestConfig,
) -> BacktestResult:
    """
    Run a single backtest given prices and target weights.

    Returns a ``BacktestResult`` with equity curve, returns, positions,
    trades, and summary statistics.
    """
    prices = prices.sort_index().astype(float)
    weights = target_weights.reindex(prices.index).ffill().fillna(0.0).astype(float)
    returns = prices.pct_change().replace([np.inf, -np.inf], np.nan).fillna(0.0)
    weights = weights.reindex(columns=prices.columns, fill_value=0.0)
    pnl = returns.mul(weights.shift(1).fillna(0.0)).sum(axis=1) * float(config.leverage)
    if config.funding_rates is not None:
        funding = config.funding_rates.reindex(prices.index).ffill().fillna(0.0)
        funding = funding.reindex(columns=prices.columns, fill_value=0.0)
        pnl = pnl.add(funding.mul(weights.shift(1).fillna(0.0)).sum(axis=1), fill_value=0.0)
    equity = (1.0 + pnl).cumprod()
    if equity.empty:
        equity = pd.Series([1.0], index=prices.index[:1])
    stats = _stats(equity, pnl)
    metrics_by_period = pd.DataFrame(
        {
            "equity": equity,
            "return": pnl,
            "turnover": weights.diff().abs().sum(axis=1).fillna(weights.abs().sum(axis=1)),
        }
    )
    if config.funding_rates is not None:
        funding = config.funding_rates.reindex(prices.index).ffill().fillna(0.0)
        funding = funding.reindex(columns=prices.columns, fill_value=0.0)
        metrics_by_period["funding_amount"] = funding.mul(weights.shift(1).fillna(0.0)).sum(axis=1)
    else:
        metrics_by_period["funding_amount"] = 0.0
    metrics_by_period["fee_amount"] = 0.0
    trades_frame = weights.diff().abs().fillna(weights.abs())
    trades_frame = trades_frame.stack().rename("size").reset_index()
    trades_frame.columns = ["timestamp", "symbol", "size"]
    trades_frame = trades_frame[trades_frame["size"] > float(config.rebalance_threshold)]
    trades = [
        {
            "timestamp": row.timestamp,
            "symbol": row.symbol,
            "size": float(row.size),
        }
        for row in trades_frame.itertuples(index=False)
    ]
    liquidated = bool(config.enable_liquidation and float(equity.min()) <= 0.0)
    liquidation_timestamp = None
    if liquidated:
        liquidation_mask = equity <= 0.0
        first_liquidation_idx = liquidation_mask.idxmax() if liquidation_mask.any() else None
        if first_liquidation_idx is not None:
            liquidation_timestamp = first_liquidation_idx
    return BacktestResult(
        equity_curve=equity,
        returns=pnl,
        positions=weights,
        trades=trades,
        metrics_by_period=metrics_by_period,
        stats=stats,
        liquidated=liquidated,
        liquidation_timestamp=liquidation_timestamp,
    )


def _stats(equity: pd.Series, returns: pd.Series) -> dict[str, Any]:
    """Compute summary statistics from equity curve and returns."""
    total_return = float(equity.iloc[-1] / equity.iloc[0] - 1.0) if len(equity) else 0.0
    periods = max(1, len(returns))
    annual_factor = 365.25 * 24.0
    mean = float(returns.mean()) if len(returns) else 0.0
    std = float(returns.std()) if len(returns) else 0.0
    sharpe = mean / std * (annual_factor ** 0.5) if std > 0 else 0.0
    cagr = float((1.0 + total_return) ** (annual_factor / periods) - 1.0) if total_return > -1.0 else -1.0
    drawdown = equity / equity.cummax() - 1.0
    max_drawdown = float(drawdown.min()) if len(drawdown) else 0.0
    calmar = cagr / abs(max_drawdown) if max_drawdown < 0 else 0.0
    return {
        "total_return": total_return,
        "sharpe": sharpe,
        "cagr": cagr,
        "max_drawdown": max_drawdown,
        "calmar": calmar,
        "liquidated": False,
    }
