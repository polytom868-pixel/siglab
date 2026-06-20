"""
Shared position math for paper trading and promotion scoring.

Provides standalone computation functions for trade PnL, position
updates, funding costs, and fill prices — used by both
``SoDEXPaperPerpsClient`` and the promotion engine.
"""

from __future__ import annotations

from typing import cast


# ---------------------------------------------------------------------------
# Funding cost
# ---------------------------------------------------------------------------


def compute_funding_cost(
    quantity: float,
    mark_price: float,
    funding_rate: float,
) -> float:
    """
    Compute funding cost for a position.

    Funding cost = position_value * funding_rate.
    Positive funding_rate means longs pay shorts.

    Parameters
    ----------
    quantity : float
        Position quantity (positive = long, negative = short).
    mark_price : float
        Current mark price.
    funding_rate : float
        Current funding rate.

    Returns
    -------
    float
        Funding cost (negative = cost to position, positive = credit).
    """
    position_value = abs(quantity) * mark_price
    if quantity > 0:  # Long pays funding
        return -position_value * funding_rate
    else:  # Short receives funding
        return position_value * funding_rate


# ---------------------------------------------------------------------------
# Trade PnL
# ---------------------------------------------------------------------------


def compute_trade_pnl(
    fill_price: float,
    quantity: float,
    prior_position: float,
    prior_entry: float,
    side: str,
) -> float:
    """
    Compute realised PnL contribution of a single filled order.

    Parameters
    ----------
    fill_price : float
        The price at which the order was filled.
    quantity : float
        Order fill quantity.
    prior_position : float
        Position quantity *before* this fill.
    prior_entry : float
        Position entry price *before* this fill.
    side : str
        ``"BUY"`` or ``"SELL"``.

    Returns
    -------
    float
        Realised PnL contributed by this fill.
    """
    if prior_position == 0:
        return 0.0  # Opening a new position — no PnL realised yet

    if prior_position > 0 and side == "SELL":
        # Reducing long
        close_qty = min(quantity, prior_position)
        return cast(float, close_qty * (fill_price - prior_entry))
    elif prior_position < 0 and side == "BUY":
        # Reducing short
        close_qty = min(quantity, abs(prior_position))
        return cast(float, close_qty * (prior_entry - fill_price))
    return 0.0


# ---------------------------------------------------------------------------
# Position update (qty + avg entry)
# ---------------------------------------------------------------------------


def update_position(
    side: str,
    quantity: float,
    fill_price: float,
    prior_qty: float,
    prior_entry: float,
) -> tuple[float, float]:
    """
    Return ``(new_qty, new_entry)`` after applying a fill.

    Parameters
    ----------
    side : str
        ``"BUY"`` or ``"SELL"``.
    quantity : float
        Order fill quantity.
    fill_price : float
        The price at which the order was filled.
    prior_qty : float
        Position quantity before this fill.
    prior_entry : float
        Position entry price before this fill.

    Returns
    -------
    tuple[float, float]
        ``(new_qty, new_entry)``.
    """
    if side == "BUY":
        new_qty = prior_qty + quantity
    else:
        new_qty = prior_qty - quantity

    if prior_qty == 0:
        new_entry = fill_price
    elif prior_qty * new_qty > 0:
        new_entry = (
            (abs(prior_qty) * prior_entry + quantity * fill_price) / abs(new_qty)
        )
    else:
        new_entry = fill_price if new_qty != 0 else 0.0

    return new_qty, new_entry if new_qty != 0 else 0.0


# ---------------------------------------------------------------------------
# Fill price determination
# ---------------------------------------------------------------------------


def calculate_fill_price(
    kline_close: float,
    kline_high: float,
    kline_low: float,
    kline_open: float,
    side: str,
    limit_price: float,
    order_type: str = "LIMIT",
) -> tuple[float, bool]:
    """
    Determine if a kline crosses the order and compute fill price.

    For MARKET orders: always fills at the kline close price.
    For LIMIT orders:
      BUY: fills when low <= limit_price. Fill at min(limit_price, open).
      SELL: fills when high >= limit_price. Fill at max(limit_price, open).

    Parameters
    ----------
    kline_close : float
        Kline close price.
    kline_high : float
        Kline high price.
    kline_low : float
        Kline low price.
    kline_open : float
        Kline open price.
    side : str
        ``"BUY"`` or ``"SELL"``.
    limit_price : float
        Limit price for the order.
    order_type : str
        ``"LIMIT"`` or ``"MARKET"``. Default ``"LIMIT"``.

    Returns
    -------
    tuple[float, bool]
        ``(fill_price, did_fill)``.
    """
    if order_type == "MARKET":
        return kline_close, True

    if side == "BUY" and kline_low <= limit_price:
        # Fill at the higher of limit price or open price (conservative for buyer)
        fill_price = min(limit_price, max(kline_open, kline_low))
        return fill_price, True
    elif side == "SELL" and kline_high >= limit_price:
        # Fill at the lower of limit price or open price (conservative for seller)
        fill_price = max(limit_price, min(kline_open, kline_high))
        return fill_price, True
    return 0.0, False


# ---------------------------------------------------------------------------
# Average entry price
# ---------------------------------------------------------------------------


def compute_avg_entry(
    prior_qty: float,
    prior_entry: float,
    add_qty: float,
    add_price: float,
) -> float:
    """
    Compute the new average entry price after adding to an existing position.

    Used when increasing an existing position in the same direction.

    Parameters
    ----------
    prior_qty : float
        Position quantity before addition (signed).
    prior_entry : float
        Position entry price before addition.
    add_qty : float
        Quantity being added (positive, direction same as prior).
    add_price : float
        Price at which the addition fills.

    Returns
    -------
    float
        New average entry price, or 0.0 if the combined quantity is zero.
    """
    new_qty = prior_qty + add_qty
    if abs(new_qty) < 1e-12:
        return 0.0
    return (abs(prior_qty) * prior_entry + abs(add_qty) * add_price) / abs(new_qty)
