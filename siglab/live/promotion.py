"""Promotion engine for paper-to-live trading."""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from siglab.live.paper_client import SoDEXPaperPerpsClient
from siglab.live.position_ledger import compute_trade_pnl, update_position

logger = logging.getLogger(__name__)
DEFAULT_WEIGHTS: dict[str, float] = {
    "pnl": 0.25,
    "sharpe": 0.25,
    "win_rate": 0.25,
    "drawdown": 0.25,
}
DEFAULT_PROMOTION_THRESHOLD: float = 0.65
DEFAULT_CONSECUTIVE_DAYS: int = 5
DEFAULT_MIN_TRADING_DAYS: int = 10
TARGET_ANNUAL_RETURN: float = 0.3
MAX_SHARPE: float = 3.0
MAX_TOLERABLE_DRAWDOWN: float = -0.3


def _normalize_pnl(pnl: float) -> float:
    if pnl <= 0.0:
        return 0.0
    if pnl >= TARGET_ANNUAL_RETURN:
        return 1.0
    return pnl / TARGET_ANNUAL_RETURN


def _normalize_sharpe(sharpe: float) -> float:
    if sharpe <= 0.0:
        return 0.0
    if sharpe >= MAX_SHARPE:
        return 1.0
    return sharpe / MAX_SHARPE


def _normalize_win_rate(win_rate: float) -> float:
    """Win rate is naturally [0, 1]; clamp to be safe."""
    return max(0.0, min(1.0, float(win_rate)))


def _normalize_drawdown(max_drawdown: float) -> float:
    if max_drawdown >= 0.0:
        return 1.0
    if max_drawdown <= MAX_TOLERABLE_DRAWDOWN:
        return 0.0
    return 1.0 - abs(max_drawdown) / abs(MAX_TOLERABLE_DRAWDOWN)


def compute_sub_scores(metrics: dict[str, Any]) -> dict[str, float]:
    """Compute normalised [0, 1] sub-scores from raw metrics."""
    return {
        "pnl": _normalize_pnl(float(metrics.get("total_return", 0))),
        "sharpe": _normalize_sharpe(float(metrics.get("sharpe", 0))),
        "win_rate": _normalize_win_rate(float(metrics.get("win_rate", 0))),
        "drawdown": _normalize_drawdown(float(metrics.get("max_drawdown", 0))),
    }


def compute_composite_score(
    metrics: dict[str, Any], weights: dict[str, float] | None = None
) -> float:
    """Compute weighted composite score from raw metrics."""
    w = weights if weights is not None else dict(DEFAULT_WEIGHTS)
    sub_scores = compute_sub_scores(metrics)
    recognised = {k: v for k, v in w.items() if k in sub_scores}
    total_weight = sum(recognised.values())
    if total_weight <= 0.0:
        return 0.0
    composite = sum((sub_scores[k] * recognised[k] for k in recognised)) / total_weight
    return max(0.0, min(1.0, composite))


def promotion_eligible(
    daily_metrics: list[dict[str, Any]],
    *,
    threshold: float | None = None,
    consecutive_days: int | None = None,
    min_trading_days: int | None = None,
    weights: dict[str, float] | None = None,
) -> tuple[bool, str]:
    """Check whether a paper session is promotion-eligible."""
    t = threshold if threshold is not None else DEFAULT_PROMOTION_THRESHOLD
    c = consecutive_days if consecutive_days is not None else DEFAULT_CONSECUTIVE_DAYS
    m = min_trading_days if min_trading_days is not None else DEFAULT_MIN_TRADING_DAYS
    if not daily_metrics:
        return (False, "No trading data available")
    total_days = len(daily_metrics)
    if total_days < m:
        return (
            False,
            f"Minimum trading days not met: {total_days} < {m} (even perfect score needs {m} trading days)",
        )
    daily_scores = [compute_composite_score(d, weights) for d in daily_metrics]
    if total_days >= c:
        recent_scores = daily_scores[-c:]
        above = [s >= t for s in recent_scores]
        latest = daily_scores[-1]
        if all(above):
            return (
                True,
                f"Composite score {latest:.4f} above threshold {t} for {c} consecutive days",
            )
        below_count = sum((1 for s in recent_scores if s < t))
        return (
            False,
            f"Composite score below threshold {t} on {below_count} of last {c} days (latest: {latest:.4f})",
        )
    return (False, f"Not enough trading days for consecutive check: {total_days} < {c}")


def _ts_to_day_key(timestamp: float) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(timestamp, tz=UTC).strftime("%Y-%m-%d")


def extract_session_metrics(
    client: SoDEXPaperPerpsClient, session_id: str
) -> dict[str, Any]:
    """Extract raw aggregate metrics from a paper trading session."""
    session = client.get_session(session_id)
    filled_orders: list[Any] = []
    for o in session.orders.values():
        if o.status.value == "FILLED" and o.fill_timestamp is not None:
            filled_orders.append(o)
    filled_orders.sort(key=lambda o: o.fill_timestamp)
    if not filled_orders:
        return {
            "total_return": 0.0,
            "sharpe": 0.0,
            "win_rate": 0.5,
            "max_drawdown": 0.0,
            "trade_count": 0,
            "total_pnl": session.pnl,
        }
    daily_pnl: dict[str, float] = {}
    pos_qty: dict[str, float] = {}
    pos_entry: dict[str, float] = {}
    for order in filled_orders:
        sym = order.symbol
        day = _ts_to_day_key(order.fill_timestamp)
        prior_qty = pos_qty.get(sym, 0.0)
        prior_entry = pos_entry.get(sym, 0.0)
        _fill_price = order.fill_price if order.fill_price is not None else order.price
        trade_pnl = compute_trade_pnl(
            _fill_price, order.quantity, prior_qty, prior_entry, order.side.value
        )
        daily_pnl[day] = daily_pnl.get(day, 0.0) + trade_pnl
        _fill_price = order.fill_price if order.fill_price is not None else order.price
        new_qty, new_entry = update_position(
            order.side.value, order.quantity, _fill_price, prior_qty, prior_entry
        )
        pos_qty[sym] = new_qty
        pos_entry[sym] = new_entry
    days_sorted = sorted(daily_pnl.keys())
    pnl_values = np.array([daily_pnl[d] for d in days_sorted])
    equity = np.cumsum(pnl_values) + 1.0
    total_return = session.pnl / 1.0 if session.pnl != 0 else 0.0
    if len(pnl_values) > 1 and float(np.std(pnl_values)) > 0:
        sharpe = float(np.mean(pnl_values) / np.std(pnl_values) * np.sqrt(365))
    else:
        sharpe = 0.0
    trade_pnls = []
    pos_qty_wr: dict[str, float] = {}
    pos_entry_wr: dict[str, float] = {}
    for order in filled_orders:
        sym = order.symbol
        prior_qty = pos_qty_wr.get(sym, 0.0)
        prior_entry = pos_entry_wr.get(sym, 0.0)
        _fill_price = order.fill_price if order.fill_price is not None else order.price
        tp = compute_trade_pnl(
            _fill_price, order.quantity, prior_qty, prior_entry, order.side.value
        )
        trade_pnls.append(tp)
        _fill_price = order.fill_price if order.fill_price is not None else order.price
        new_qty, new_entry = update_position(
            order.side.value, order.quantity, _fill_price, prior_qty, prior_entry
        )
        pos_qty_wr[sym] = new_qty
        pos_entry_wr[sym] = new_entry
    profitable = sum((1 for p in trade_pnls if p > 0))
    total_trades = len(trade_pnls)
    win_rate = profitable / total_trades if total_trades > 0 else 0.5
    if len(equity) > 0:
        peak = np.maximum.accumulate(equity)
        drawdown = (equity - peak) / peak
        max_dd = float(np.min(drawdown))
    else:
        max_dd = 0.0
    return {
        "total_return": total_return,
        "sharpe": sharpe,
        "win_rate": win_rate,
        "max_drawdown": max_dd,
        "trade_count": total_trades,
        "total_pnl": session.pnl,
    }


def extract_daily_metrics(
    client: SoDEXPaperPerpsClient, session_id: str
) -> list[dict[str, Any]]:
    """Extract per-trading-day raw metrics from a paper session."""
    session = client.get_session(session_id)
    filled_orders: list[Any] = []
    for o in session.orders.values():
        if o.status.value == "FILLED" and o.fill_timestamp is not None:
            filled_orders.append(o)
    filled_orders.sort(key=lambda o: o.fill_timestamp)
    if not filled_orders:
        return []
    from collections import defaultdict

    day_orders: dict[str, list[Any]] = defaultdict(list)
    for order in filled_orders:
        day = _ts_to_day_key(order.fill_timestamp)
        day_orders[day].append(order)
    days_sorted = sorted(day_orders.keys())
    daily_list: list[dict[str, Any]] = []
    running_pos_qty: dict[str, float] = {}
    running_pos_entry: dict[str, float] = {}
    cumulative_equity = 1.0
    for day in days_sorted:
        orders = day_orders[day]
        day_pnl = 0.0
        trade_pnls_day = []
        for order in orders:
            sym = order.symbol
            prior_qty = running_pos_qty.get(sym, 0.0)
            prior_entry = running_pos_entry.get(sym, 0.0)
            _fill_price = (
                order.fill_price if order.fill_price is not None else order.price
            )
            tp = compute_trade_pnl(
                _fill_price, order.quantity, prior_qty, prior_entry, order.side.value
            )
            trade_pnls_day.append(tp)
            day_pnl += tp
            new_qty, new_entry = update_position(
                order.side.value, order.quantity, _fill_price, prior_qty, prior_entry
            )
            running_pos_qty[sym] = new_qty
            running_pos_entry[sym] = new_entry
        cumulative_equity += day_pnl
        total_trades_day = len(trade_pnls_day)
        win_rate_day = (
            sum((1 for p in trade_pnls_day if p > 0)) / total_trades_day
            if total_trades_day > 0
            else 0.5
        )
        daily_list.append(
            {
                "total_return": day_pnl,
                "sharpe": 0.0,
                "win_rate": win_rate_day,
                "max_drawdown": min(0.0, day_pnl / cumulative_equity)
                if cumulative_equity > 0
                else 0.0,
                "day_pnl": day_pnl,
                "trade_count": total_trades_day,
            }
        )
    return daily_list
