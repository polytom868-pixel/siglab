"""Shared position math for paper trading and promotion scoring."""

from __future__ import annotations

from typing import cast


def compute_funding_cost(
    quantity: float,
    mark_price: float,
    funding_rate: float,
) -> float:
    """Compute funding cost for a position."""
    position_value = abs(quantity) * mark_price
    if quantity > 0:
        return -position_value * funding_rate
    return position_value * funding_rate


def compute_trade_pnl(
    fill_price: float,
    quantity: float,
    prior_position: float,
    prior_entry: float,
    side: str,
) -> float:
    """Compute realised PnL contribution of a single filled order."""
    if prior_position == 0:
        return 0.0
    if prior_position > 0 and side == "SELL":
        close_qty = min(quantity, prior_position)
        return cast(float, close_qty * (fill_price - prior_entry))
    if prior_position < 0 and side == "BUY":
        close_qty = min(quantity, abs(prior_position))
        return cast(float, close_qty * (prior_entry - fill_price))
    return 0.0


def update_position(
    side: str,
    quantity: float,
    fill_price: float,
    prior_qty: float,
    prior_entry: float,
) -> tuple[float, float]:
    """Return (new_qty, new_entry) after applying a fill."""
    if side == "BUY":
        new_qty = prior_qty + quantity
    else:
        new_qty = prior_qty - quantity
    if prior_qty == 0:
        new_entry = fill_price
    elif prior_qty * new_qty > 0:
        new_entry = (abs(prior_qty) * prior_entry + quantity * fill_price) / abs(
            new_qty,
        )
    else:
        new_entry = fill_price if new_qty != 0 else 0.0
    return (new_qty, new_entry if new_qty != 0 else 0.0)


def calculate_fill_price(
    kline_close: float,
    kline_high: float,
    kline_low: float,
    kline_open: float,
    side: str,
    limit_price: float,
    order_type: str = "LIMIT",
) -> tuple[float, bool]:
    """Determine if a kline crosses the order and compute fill price."""
    if order_type == "MARKET":
        return (kline_close, True)
    if side == "BUY" and kline_low <= limit_price:
        fill_price = min(limit_price, max(kline_open, kline_low))
        return (fill_price, True)
    if side == "SELL" and kline_high >= limit_price:
        fill_price = max(limit_price, min(kline_open, kline_high))
        return (fill_price, True)
    return (0.0, False)


def compute_avg_entry(
    prior_qty: float,
    prior_entry: float,
    add_qty: float,
    add_price: float,
) -> float:
    """Compute the new average entry price after adding to an existing position."""
    new_qty = prior_qty + add_qty
    if abs(new_qty) < 1e-12:
        return 0.0
    return (abs(prior_qty) * prior_entry + abs(add_qty) * add_price) / abs(new_qty)
