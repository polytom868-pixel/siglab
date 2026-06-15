"""
Promotion engine for paper-to-live trading.

Computes composite scores from trading metrics and determines promotion
eligibility based on weighted sub-scores (PnL, Sharpe, win-rate, drawdown)
with minimum trading days and consecutive-day requirements.

The core scoring functions operate on raw metric dicts, making them
straightforward to test with known inputs. Session-level extraction
helpers bridge the engine to ``SoDEXPaperPerpsClient`` sessions.
"""

from __future__ import annotations

import logging
from typing import Any, cast

import numpy as np

from siglab.live.paper_client import SoDEXPaperPerpsClient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default configuration
# ---------------------------------------------------------------------------

DEFAULT_WEIGHTS: dict[str, float] = {
    "pnl": 0.25,
    "sharpe": 0.25,
    "win_rate": 0.25,
    "drawdown": 0.25,
}

DEFAULT_PROMOTION_THRESHOLD: float = 0.65
DEFAULT_CONSECUTIVE_DAYS: int = 5
DEFAULT_MIN_TRADING_DAYS: int = 10

# Sub-score normalisation targets
TARGET_ANNUAL_RETURN: float = 0.30  # 30 % annual target → full PnL score
MAX_SHARPE: float = 3.0  # Sharpe ≥ 3 → full score
MAX_TOLERABLE_DRAWDOWN: float = -0.30  # ≤ -30 % drawdown → zero score


# ---------------------------------------------------------------------------
# Sub-score normalisation helpers
# ---------------------------------------------------------------------------

def _normalize_pnl(pnl: float) -> float:
    """Normalise total return to [0, 1].

    ≤ 0 %  → 0,  ≥ *target_annual_return* → 1, linear in between.
    """
    if pnl <= 0.0:
        return 0.0
    if pnl >= TARGET_ANNUAL_RETURN:
        return 1.0
    return pnl / TARGET_ANNUAL_RETURN


def _normalize_sharpe(sharpe: float) -> float:
    """Normalise Sharpe ratio to [0, 1]."""
    if sharpe <= 0.0:
        return 0.0
    if sharpe >= MAX_SHARPE:
        return 1.0
    return sharpe / MAX_SHARPE


def _normalize_win_rate(win_rate: float) -> float:
    """Win rate is naturally [0, 1]; clamp to be safe."""
    return max(0.0, min(1.0, float(win_rate)))


def _normalize_drawdown(max_drawdown: float) -> float:
    """Normalise max drawdown (negative) to [0, 1].

    Drawdown ≥ 0 → 1.0, ≤ *max_tolerable* → 0.0, linear in between.
    """
    if max_drawdown >= 0.0:
        return 1.0
    if max_drawdown <= MAX_TOLERABLE_DRAWDOWN:
        return 0.0
    return 1.0 - abs(max_drawdown) / abs(MAX_TOLERABLE_DRAWDOWN)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_sub_scores(metrics: dict[str, Any]) -> dict[str, float]:
    """Compute normalised [0, 1] sub-scores from raw metrics.

    Parameters
    ----------
    metrics : dict
        Must contain keys ``total_return``, ``sharpe``, ``win_rate``,
        ``max_drawdown``.

    Returns
    -------
    dict[str, float]
        Sub-scores keyed by name, each capped to [0, 1].
    """
    return {
        "pnl": _normalize_pnl(float(metrics.get("total_return", 0))),
        "sharpe": _normalize_sharpe(float(metrics.get("sharpe", 0))),
        "win_rate": _normalize_win_rate(float(metrics.get("win_rate", 0))),
        "drawdown": _normalize_drawdown(float(metrics.get("max_drawdown", 0))),
    }


def compute_composite_score(
    metrics: dict[str, Any],
    weights: dict[str, float] | None = None,
) -> float:
    """Compute weighted composite score from raw metrics.

    Parameters
    ----------
    metrics : dict
        Must contain keys ``total_return``, ``sharpe``, ``win_rate``,
        ``max_drawdown``.
    weights : dict, optional
        Sub-score weights.  Defaults to equal weighting.

    Returns
    -------
    float
        Composite score in [0, 1].
    """
    w = weights if weights is not None else dict(DEFAULT_WEIGHTS)
    sub_scores = compute_sub_scores(metrics)

    # Only consider recognised sub-score keys in the weighted sum.
    # Unknown weight entries (e.g. custom keys that are not part of the
    # scoring model) are silently skipped so callers do not get a KeyError.
    recognised = {k: v for k, v in w.items() if k in sub_scores}
    total_weight = sum(recognised.values())
    if total_weight <= 0.0:
        return 0.0

    composite = sum(sub_scores[k] * recognised[k] for k in recognised) / total_weight
    return max(0.0, min(1.0, composite))


def promotion_eligible(
    daily_metrics: list[dict[str, Any]],
    *,
    threshold: float | None = None,
    consecutive_days: int | None = None,
    min_trading_days: int | None = None,
    weights: dict[str, float] | None = None,
) -> tuple[bool, str]:
    """Check whether a paper session is promotion-eligible.

    Parameters
    ----------
    daily_metrics : list[dict]
        Chronological list of daily metric dicts, each containing
        ``total_return``, ``sharpe``, ``win_rate``, ``max_drawdown``.
    threshold : float, optional
        Composite-score threshold.  Default: 0.65.
    consecutive_days : int, optional
        How many consecutive days must exceed *threshold*.
        Default: 5.
    min_trading_days : int, optional
        Absolute minimum number of trading days required.
        Default: 10.
    weights : dict, optional
        Sub-score weights forwarded to ``compute_composite_score``.

    Returns
    -------
    tuple[bool, str]
        ``(eligible, human-readable reason)``.
    """
    t = threshold if threshold is not None else DEFAULT_PROMOTION_THRESHOLD
    c = consecutive_days if consecutive_days is not None else DEFAULT_CONSECUTIVE_DAYS
    m = min_trading_days if min_trading_days is not None else DEFAULT_MIN_TRADING_DAYS

    if not daily_metrics:
        return False, "No trading data available"

    total_days = len(daily_metrics)

    # --- Minimum trading days gate (VAL-PAPER-012) ---------------------------
    if total_days < m:
        return (
            False,
            f"Minimum trading days not met: {total_days} < {m} "
            f"(even perfect score needs {m} trading days)",
        )

    # --- Compute daily composite scores --------------------------------------
    daily_scores = [compute_composite_score(d, weights) for d in daily_metrics]

    # --- Consecutive-day check -----------------------------------------------
    if total_days >= c:
        recent_scores = daily_scores[-c:]
        above = [s >= t for s in recent_scores]
        latest = daily_scores[-1]
        if all(above):
            return (
                True,
                f"Composite score {latest:.4f} above threshold {t} "
                f"for {c} consecutive days",
            )
        below_count = sum(1 for s in recent_scores if s < t)
        return (
            False,
            f"Composite score below threshold {t} on {below_count} of "
            f"last {c} days (latest: {latest:.4f})",
        )

    return (
        False,
        f"Not enough trading days for consecutive check: {total_days} < {c}",
    )


# ---------------------------------------------------------------------------
# Session metrics extraction
# ---------------------------------------------------------------------------

def _ts_to_day_key(timestamp: float) -> str:
    """Convert a Unix timestamp to a ``YYYY-MM-DD`` string."""
    from datetime import UTC, datetime

    return datetime.fromtimestamp(timestamp, tz=UTC).strftime("%Y-%m-%d")


def _compute_trade_pnl(order: Any, prior_position: float, prior_entry: float) -> float:
    """Compute realised PnL contribution of a single filled order.

    Parameters
    ----------
    order : PaperOrder
        A filled order.
    prior_position : float
        Position quantity *before* this fill.
    prior_entry : float
        Position entry price *before* this fill.

    Returns
    -------
    float
        Realised PnL contributed by this fill.
    """
    from siglab.live.paper_client import PaperOrderSide

    fill_price = order.fill_price if order.fill_price is not None else order.price
    qty = order.quantity

    if prior_position == 0:
        return 0.0  # Opening a new position — no PnL realised yet

    if prior_position > 0 and order.side == PaperOrderSide.SELL:
        # Reducing long
        close_qty = min(qty, prior_position)
        return cast(float, close_qty * (fill_price - prior_entry))
    elif prior_position < 0 and order.side == PaperOrderSide.BUY:
        # Reducing short
        close_qty = min(qty, abs(prior_position))
        return cast(float, close_qty * (prior_entry - fill_price))

    return 0.0


def extract_session_metrics(
    client: SoDEXPaperPerpsClient,
    session_id: str,
) -> dict[str, Any]:
    """Extract raw aggregate metrics from a paper trading session.

    Parameters
    ----------
    client : SoDEXPaperPerpsClient
        The paper trading client holding the session.
    session_id : str
        Session identifier.

    Returns
    -------
    dict
        Raw metrics: ``total_return``, ``sharpe``, ``win_rate``,
        ``max_drawdown``, ``trade_count``, ``total_pnl``.
    """
    session = client.get_session(session_id)

    filled_orders = [
        o for o in session.orders.values()
        if o.status.value == "FILLED" and o.fill_timestamp is not None
    ]
    filled_orders.sort(key=lambda o: o.fill_timestamp)  # type: ignore[arg-type, return-value]

    if not filled_orders:
        return {
            "total_return": 0.0,
            "sharpe": 0.0,
            "win_rate": 0.5,
            "max_drawdown": 0.0,
            "trade_count": 0,
            "total_pnl": session.pnl,
        }

    # Group fills by trading day and compute daily PnL
    daily_pnl: dict[str, float] = {}
    pos_qty: dict[str, float] = {}
    pos_entry: dict[str, float] = {}

    for order in filled_orders:
        sym = order.symbol
        day = _ts_to_day_key(order.fill_timestamp)  # type: ignore[arg-type]

        prior_qty = pos_qty.get(sym, 0.0)
        prior_entry = pos_entry.get(sym, 0.0)
        trade_pnl = _compute_trade_pnl(order, prior_qty, prior_entry)
        daily_pnl[day] = daily_pnl.get(day, 0.0) + trade_pnl

        # Update simulated position after this fill
        from siglab.live.paper_client import PaperOrderSide

        if order.side == PaperOrderSide.BUY:
            new_qty = prior_qty + order.quantity
        else:
            new_qty = prior_qty - order.quantity

        if prior_qty == 0:
            new_entry = order.fill_price if order.fill_price is not None else order.price
        elif prior_qty * new_qty > 0:
            # Same direction — average entry
            new_entry = (
                (abs(prior_qty) * prior_entry + order.quantity * (order.fill_price or order.price))
                / abs(new_qty)
            )
        else:
            # Flattening or flipping
            new_entry = order.fill_price if order.fill_price is not None else order.price if new_qty != 0 else 0.0

        pos_qty[sym] = new_qty
        pos_entry[sym] = new_entry if new_qty != 0 else 0.0

    # Build equity curve (starting equity = 1.0)
    days_sorted = sorted(daily_pnl.keys())
    pnl_values = np.array([daily_pnl[d] for d in days_sorted])
    equity = np.cumsum(pnl_values) + 1.0

    total_return = session.pnl / 1.0 if session.pnl != 0 else 0.0

    # --- Sharpe ratio (annualised from daily returns) ------------------------
    if len(pnl_values) > 1 and float(np.std(pnl_values)) > 0:
        sharpe = float(np.mean(pnl_values) / np.std(pnl_values) * np.sqrt(365))
    else:
        sharpe = 0.0

    # --- Win rate per order --------------------------------------------------
    # A "winning" order is one whose fill contributed positive PnL.
    trade_pnls = []
    pos_qty_wr: dict[str, float] = {}
    pos_entry_wr: dict[str, float] = {}

    for order in filled_orders:
        sym = order.symbol
        prior_qty = pos_qty_wr.get(sym, 0.0)
        prior_entry = pos_entry_wr.get(sym, 0.0)
        tp = _compute_trade_pnl(order, prior_qty, prior_entry)
        trade_pnls.append(tp)

        from siglab.live.paper_client import PaperOrderSide

        if order.side == PaperOrderSide.BUY:
            new_qty = prior_qty + order.quantity
        else:
            new_qty = prior_qty - order.quantity

        if prior_qty == 0:
            new_entry = order.fill_price if order.fill_price is not None else order.price
        elif prior_qty * new_qty > 0:
            new_entry = (
                (abs(prior_qty) * prior_entry + order.quantity * (order.fill_price or order.price))
                / abs(new_qty)
            )
        else:
            new_entry = order.fill_price if order.fill_price is not None else order.price if new_qty != 0 else 0.0

        pos_qty_wr[sym] = new_qty
        pos_entry_wr[sym] = new_entry if new_qty != 0 else 0.0

    profitable = sum(1 for p in trade_pnls if p > 0)
    total_trades = len(trade_pnls)
    win_rate = profitable / total_trades if total_trades > 0 else 0.5

    # --- Max drawdown --------------------------------------------------------
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
    client: SoDEXPaperPerpsClient,
    session_id: str,
) -> list[dict[str, Any]]:
    """Extract per-trading-day raw metrics from a paper session.

    Each element of the returned list corresponds to one trading day and
    contains the same keys as the dict accepted by ``compute_composite_score``,
    computed from that day's fills only.

    Parameters
    ----------
    client : SoDEXPaperPerpsClient
        The paper trading client.
    session_id : str
        Session identifier.

    Returns
    -------
    list[dict]
        Chronological list of daily metric dicts.
    """
    session = client.get_session(session_id)

    filled_orders = [
        o for o in session.orders.values()
        if o.status.value == "FILLED" and o.fill_timestamp is not None
    ]
    filled_orders.sort(key=lambda o: o.fill_timestamp)  # type: ignore[arg-type, return-value]

    if not filled_orders:
        return []

    # Group by day
    from collections import defaultdict

    day_orders: dict[str, list[Any]] = defaultdict(list)
    for order in filled_orders:
        day = _ts_to_day_key(order.fill_timestamp)  # type: ignore[arg-type]
        day_orders[day].append(order)

    days_sorted = sorted(day_orders.keys())
    daily_list: list[dict[str, Any]] = []

    # Track running position for PnL computation across days
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
            tp = _compute_trade_pnl(order, prior_qty, prior_entry)
            trade_pnls_day.append(tp)
            day_pnl += tp

            from siglab.live.paper_client import PaperOrderSide

            if order.side == PaperOrderSide.BUY:
                new_qty = prior_qty + order.quantity
            else:
                new_qty = prior_qty - order.quantity

            if prior_qty == 0:
                new_entry = order.fill_price if order.fill_price is not None else order.price
            elif prior_qty * new_qty > 0:
                new_entry = (
                    (abs(prior_qty) * prior_entry + order.quantity * (order.fill_price or order.price))
                    / abs(new_qty)
                )
            else:
                new_entry = order.fill_price if order.fill_price is not None else order.price if new_qty != 0 else 0.0

            running_pos_qty[sym] = new_qty
            running_pos_entry[sym] = new_entry if new_qty != 0 else 0.0

        cumulative_equity += day_pnl

        # For daily-level metrics, compute from that day's data only
        total_trades_day = len(trade_pnls_day)
        win_rate_day = sum(1 for p in trade_pnls_day if p > 0) / total_trades_day if total_trades_day > 0 else 0.5

        daily_list.append({
            "total_return": day_pnl,
            "sharpe": 0.0,  # Cannot compute from a single day reliably
            "win_rate": win_rate_day,
            "max_drawdown": min(0.0, day_pnl / cumulative_equity) if cumulative_equity > 0 else 0.0,
            "day_pnl": day_pnl,
            "trade_count": total_trades_day,
        })

    return daily_list
