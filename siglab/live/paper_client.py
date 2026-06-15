"""
SoDEX Paper Perps Client — simulated paper trading on real SoDEX klines.

Provides ``SoDEXPaperPerpsClient``, a paper trading engine that uses
real SoDEX market data (klines, funding rates) to simulate order
execution without submitting live trades.

Session state is persisted as ``.npy`` files for survivability across
process restarts.

Order lifecycle
---------------
OPEN → FILLED (when kline crosses limit) / CANCELLED / EXPIRED

Exception hierarchy
--------------------
* ``PaperClientError`` — base error for paper trading operations
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import numpy as np
import pandas as pd

from siglab.live.sodex_client import SoDEXTransportError, SoDEXUpstreamError

if TYPE_CHECKING:
    from siglab.data.sodex_feeds import SoDEXFeeds

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FUNDING_INTERVAL_HOURS = 8  # Standard perp funding interval
DEFAULT_TIME_IN_FORCE_HOURS = 72  # Default TIF for paper orders

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class PaperClientError(ValueError):
    """Raised on invalid paper trading parameters or operations."""


class PaperSessionNotFoundError(PaperClientError):
    """Raised when a session ID does not exist."""


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class PaperOrderStatus(str, Enum):
    OPEN = "OPEN"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class PaperOrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class PaperOrderType(str, Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"


class PaperTimeInForce(str, Enum):
    GTC = "GTC"  # Good-til-cancelled
    IOC = "IOC"  # Immediate-or-cancel
    FOK = "FOK"  # Fill-or-kill
    GTX = "GTX"  # Good-til-cancelled (post-only)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class PaperOrder:
    """A single paper order."""

    order_id: str
    symbol: str
    side: PaperOrderSide
    quantity: float
    price: float
    order_type: PaperOrderType
    time_in_force: PaperTimeInForce
    status: PaperOrderStatus = PaperOrderStatus.OPEN
    fill_price: float | None = None
    fill_timestamp: float | None = None
    created_at: float = field(default_factory=time.time)
    expires_at: float | None = None
    cancelled_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "quantity": self.quantity,
            "price": self.price,
            "order_type": self.order_type.value,
            "time_in_force": self.time_in_force.value,
            "status": self.status.value,
            "fill_price": self.fill_price,
            "fill_timestamp": self.fill_timestamp,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "cancelled_at": self.cancelled_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PaperOrder:
        data = dict(data)
        data["side"] = PaperOrderSide(data["side"])
        data["order_type"] = PaperOrderType(data["order_type"])
        data["time_in_force"] = PaperTimeInForce(data["time_in_force"])
        data["status"] = PaperOrderStatus(data["status"])
        return cls(**data)


@dataclass
class PaperPosition:
    """A paper trading position for a single symbol."""

    symbol: str
    quantity: float = 0.0
    entry_price: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    accumulated_funding: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "quantity": self.quantity,
            "entry_price": self.entry_price,
            "realized_pnl": self.realized_pnl,
            "unrealized_pnl": self.unrealized_pnl,
            "accumulated_funding": self.accumulated_funding,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PaperPosition:
        return cls(**data)


@dataclass
class PaperSession:
    """Complete state for one paper trading session."""

    session_id: str
    name: str
    created_at: float = field(default_factory=time.time)
    orders: dict[str, PaperOrder] = field(default_factory=dict)
    positions: dict[str, PaperPosition] = field(default_factory=dict)
    pnl: float = 0.0
    last_funding_time: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    initial_balance: float = 10_000.0
    maintenance_margin_rate: float = 0.005

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "name": self.name,
            "created_at": self.created_at,
            "orders": {oid: order.to_dict() for oid, order in self.orders.items()},
            "positions": {sym: pos.to_dict() for sym, pos in self.positions.items()},
            "pnl": self.pnl,
            "last_funding_time": self.last_funding_time,
            "metadata": dict(self.metadata),
            "initial_balance": self.initial_balance,
            "maintenance_margin_rate": self.maintenance_margin_rate,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PaperSession:
        session = cls(
            session_id=data["session_id"],
            name=data.get("name", data["session_id"]),
            created_at=data.get("created_at", time.time()),
            pnl=data.get("pnl", 0.0),
            last_funding_time=data.get("last_funding_time"),
            metadata=dict(data.get("metadata", {})),
            initial_balance=data.get("initial_balance", 10_000.0),
            maintenance_margin_rate=data.get("maintenance_margin_rate", 0.005),
        )
        for oid, odata in data.get("orders", {}).items():
            session.orders[oid] = PaperOrder.from_dict(odata)
        for sym, pdata in data.get("positions", {}).items():
            session.positions[sym] = PaperPosition.from_dict(pdata)
        return session


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _generate_session_id() -> str:
    """Generate a short unique session ID."""
    return uuid.uuid4().hex[:12]


def _generate_order_id() -> str:
    """Generate a unique order ID."""
    return uuid.uuid4().hex[:16]


def _now_timestamp() -> float:
    """Current time as seconds since epoch."""
    return time.time()


def _validate_symbol(symbol: str) -> str:
    """Validate and normalize a perp symbol."""
    symbol = str(symbol).strip().upper()
    if not symbol:
        raise PaperClientError("symbol must not be empty")
    if len(symbol) < 3:
        raise PaperClientError(f"invalid symbol: {symbol!r}")
    return symbol


def _validate_quantity(quantity: float) -> float:
    """Validate order quantity."""
    try:
        qty = float(quantity)
    except (TypeError, ValueError):
        raise PaperClientError(f"quantity must be a number, got {quantity!r}")
    if qty <= 0:
        raise PaperClientError(f"quantity must be positive, got {qty}")
    if qty > 1e12:
        raise PaperClientError(f"quantity too large: {qty}")
    return qty


def _validate_price(price: float | None, order_type: PaperOrderType) -> float | None:
    """Validate order price (required for LIMIT, optional for MARKET)."""
    if order_type == PaperOrderType.MARKET:
        return None
    if price is None:
        raise PaperClientError("price is required for LIMIT orders")
    try:
        p = float(price)
    except (TypeError, ValueError):
        raise PaperClientError(f"price must be a number, got {price!r}")
    if p <= 0:
        raise PaperClientError(f"price must be positive, got {p}")
    if p > 1e12:
        raise PaperClientError(f"price too large: {p}")
    return p


def _validate_side(side: str) -> PaperOrderSide:
    """Validate order side."""
    try:
        return PaperOrderSide(side.upper())
    except ValueError:
        raise PaperClientError(f"invalid side: {side!r}; expected BUY or SELL")


def _validate_order_type(order_type: str) -> PaperOrderType:
    """Validate order type."""
    try:
        return PaperOrderType(order_type.upper())
    except ValueError:
        raise PaperClientError(f"invalid order_type: {order_type!r}; expected LIMIT or MARKET")


def _validate_time_in_force(tif: str) -> PaperTimeInForce:
    """Validate time-in-force."""
    try:
        return PaperTimeInForce(tif.upper())
    except ValueError:
        raise PaperClientError(
            f"invalid time_in_force: {tif!r}; expected GTC, IOC, FOK, or GTX"
        )


def _compute_fill_price(
    kline: dict[str, Any],
    side: PaperOrderSide,
    limit_price: float,
    order_type: PaperOrderType = PaperOrderType.LIMIT,
) -> tuple[float, bool]:
    """
    Determine if a kline crosses the order and compute fill price.

    For MARKET orders: always fills at the kline close price.
    For LIMIT orders:
      BUY: fills when low <= limit_price. Fill at min(limit_price, open).
      SELL: fills when high >= limit_price. Fill at max(limit_price, open).

    Returns (fill_price, did_fill).
    """
    close = float(kline.get("c", 0))

    # MARKET orders always fill at the kline close price
    if order_type == PaperOrderType.MARKET:
        return close, True

    high = float(kline.get("h", 0))
    low = float(kline.get("l", 0))
    open_price = float(kline.get("o", 0))

    if side == PaperOrderSide.BUY and low <= limit_price:
        # Fill at the higher of limit price or open price (conservative for buyer)
        fill_price = min(limit_price, max(open_price, low))
        return fill_price, True
    elif side == PaperOrderSide.SELL and high >= limit_price:
        # Fill at the lower of limit price or open price (conservative for seller)
        fill_price = max(limit_price, min(open_price, high))
        return fill_price, True
    return 0.0, False


def _compute_funding_cost(
    position: PaperPosition,
    mark_price: float,
    funding_rate: float,
) -> float:
    """
    Compute funding cost for a position.

    Funding cost = position_value * funding_rate.
    Positive funding_rate means longs pay shorts.
    """
    position_value = abs(position.quantity) * mark_price
    if position.quantity > 0:  # Long pays funding
        return -position_value * funding_rate
    else:  # Short receives funding
        return position_value * funding_rate


# ---------------------------------------------------------------------------
# SoDEXPaperPerpsClient
# ---------------------------------------------------------------------------


class SoDEXPaperPerpsClient:
    """
    Paper trading simulator using real SoDEX market data.

    Uses ``SoDEXFeeds`` for live kline prices and funding rates.
    Session state is persisted as ``.npy`` files.

    Parameters
    ----------
    feeds : SoDEXFeeds or None
        SoDEX market data feed for klines and funding rates.
        If ``None``, funding processing and feed-dependent operations
        are skipped gracefully.
    sessions_dir : str or Path
        Directory for ``.npy`` session files (default: ``sessions/``).
    """

    def __init__(
        self,
        feeds: SoDEXFeeds | None = None,
        sessions_dir: str | Path = "sessions",
        slippage_bps: float = 15.0,
        min_notional_usd: float = 10.0,
    ) -> None:
        self.feeds = feeds
        self.sessions_dir = Path(sessions_dir)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.slippage_bps = slippage_bps
        self.min_notional_usd = min_notional_usd
        self._sessions: dict[str, PaperSession] = {}

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def create_session(self, name: str | None = None) -> str:
        """
        Create a new paper trading session.

        Parameters
        ----------
        name : str, optional
            A human-readable label for the session.

        Returns
        -------
        str
            The unique session ID.
        """
        session_id = _generate_session_id()
        session = PaperSession(
            session_id=session_id,
            name=name or session_id,
        )
        self._sessions[session_id] = session
        self._save_session_to_disk(session)
        logger.info("Created paper session %s (name=%s)", session_id, session.name)
        return session_id

    def get_session(self, session_id: str) -> PaperSession:
        """Get a session by ID, loading from disk if needed."""
        if session_id not in self._sessions:
            self._sessions[session_id] = self._load_session_from_disk(session_id)
        return self._sessions[session_id]

    def list_sessions(self) -> list[dict[str, Any]]:
        """List all known sessions with their metadata."""
        sessions: list[dict[str, Any]] = []
        seen_stems: set[str] = set()
        for path in sorted(self.sessions_dir.glob("*.json")):
            try:
                data = self._read_session_file(path)
                sessions.append({
                    "session_id": data.get("session_id", path.stem),
                    "name": data.get("name", path.stem),
                    "created_at": data.get("created_at", 0.0),
                    "order_count": len(data.get("orders", {})),
                    "position_count": len(data.get("positions", {})),
                    "pnl": data.get("pnl", 0.0),
                })
                seen_stems.add(path.stem)
            except Exception:
                logger.warning("Failed to read session file %s", path)
        # Also pick up any legacy .npy-only sessions not yet migrated
        for path in sorted(self.sessions_dir.glob("*.npy")):
            if path.stem in seen_stems:
                continue
            try:
                data = self._read_session_file(path)
                sessions.append({
                    "session_id": data.get("session_id", path.stem),
                    "name": data.get("name", path.stem),
                    "created_at": data.get("created_at", 0.0),
                    "order_count": len(data.get("orders", {})),
                    "position_count": len(data.get("positions", {})),
                    "pnl": data.get("pnl", 0.0),
                })
            except Exception:
                logger.warning("Failed to read session file %s", path)
        return sessions

    def session_path(self, session_id: str) -> Path:
        """Return the .json file path for a session."""
        if "/" in session_id or ".." in session_id or "\\" in session_id:
            raise PaperClientError("Invalid session_id")
        return self.sessions_dir / f"{session_id}.json"

    # ------------------------------------------------------------------
    # Order management
    # ------------------------------------------------------------------

    def place_order(
        self,
        session_id: str,
        *,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str = "LIMIT",
        price: float | None = None,
        time_in_force: str = "GTC",
    ) -> dict[str, Any]:
        """
        Place a paper order.

        Parameters
        ----------
        session_id : str
            Target session ID.
        symbol : str
            Perp symbol (e.g. ``"BTC-USD"``).
        side : str
            ``"BUY"`` or ``"SELL"``.
        quantity : float
            Order quantity (positive).
        order_type : str
            ``"LIMIT"`` or ``"MARKET"``.
        price : float, optional
            Limit price (required for LIMIT orders).
        time_in_force : str
            ``"GTC"``, ``"IOC"``, ``"FOK"``, or ``"GTX"``.

        Returns
        -------
        dict
            The created order record.
        """
        # Validate inputs
        symbol = _validate_symbol(symbol)
        qty = _validate_quantity(quantity)
        side_enum = _validate_side(side)
        order_type_enum = _validate_order_type(order_type)
        tif_enum = _validate_time_in_force(time_in_force)
        validated_price = _validate_price(price, order_type_enum)

        # Load session
        session = self.get_session(session_id)

        # Create order
        now = _now_timestamp()
        order_id = _generate_order_id()

        # Compute expiry based on time-in-force
        expires_at: float | None = None
        if tif_enum == PaperTimeInForce.IOC:
            expires_at = now + 60  # 1 minute
        elif tif_enum == PaperTimeInForce.FOK:
            expires_at = now + 10  # 10 seconds
        else:  # GTC or GTX
            expires_at = now + DEFAULT_TIME_IN_FORCE_HOURS * 3600

        order = PaperOrder(
            order_id=order_id,
            symbol=symbol,
            side=side_enum,
            quantity=qty,
            price=validated_price or 0.0,
            order_type=order_type_enum,
            time_in_force=tif_enum,
            created_at=now,
            expires_at=expires_at,
        )

        # For MARKET orders, try immediate fill at next opportunity
        if order_type_enum == PaperOrderType.MARKET:
            order.status = PaperOrderStatus.OPEN

        # For IOC/FOK, set to expired if not filled immediately
        if tif_enum in (PaperTimeInForce.IOC, PaperTimeInForce.FOK):
            # These will be checked on the next kline processing
            pass

        session.orders[order_id] = order
        self._save_session_to_disk(session)
        logger.info(
            "Placed %s %s %s order %s for %s @ %s",
            side_enum.value,
            order_type_enum.value,
            symbol,
            order_id,
            qty,
            validated_price or "market",
        )
        return order.to_dict()

    def cancel_order(
        self,
        session_id: str,
        order_id: str,
    ) -> dict[str, Any]:
        """
        Cancel an open order.

        Parameters
        ----------
        session_id : str
            Target session ID.
        order_id : str
            Order ID to cancel.

        Returns
        -------
        dict
            The updated order record.
        """
        session = self.get_session(session_id)
        order = session.orders.get(order_id)
        if order is None:
            raise PaperClientError(f"order {order_id} not found in session {session_id}")

        if order.status != PaperOrderStatus.OPEN:
            raise PaperClientError(
                f"cannot cancel order {order_id} with status {order.status.value}"
            )

        order.status = PaperOrderStatus.CANCELLED
        order.cancelled_at = _now_timestamp()
        self._save_session_to_disk(session)
        logger.info("Cancelled order %s (session %s)", order_id, session_id)
        return order.to_dict()

    def get_orders(
        self,
        session_id: str,
        *,
        symbol: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Get orders for a session.

        Parameters
        ----------
        session_id : str
            Target session ID.
        symbol : str, optional
            Filter by symbol.
        status : str, optional
            Filter by order status.

        Returns
        -------
        list[dict]
            List of order records.
        """
        session = self.get_session(session_id)
        orders = list(session.orders.values())
        if symbol:
            orders = [o for o in orders if o.symbol == _validate_symbol(symbol)]
        if status:
            try:
                status_enum = PaperOrderStatus(status.upper())
            except ValueError:
                raise PaperClientError(f"invalid order status: {status!r}")
            orders = [o for o in orders if o.status == status_enum]
        return [o.to_dict() for o in sorted(orders, key=lambda o: o.created_at, reverse=True)]

    def get_order(
        self,
        session_id: str,
        order_id: str,
    ) -> dict[str, Any]:
        """Get a single order by ID."""
        session = self.get_session(session_id)
        order = session.orders.get(order_id)
        if order is None:
            raise PaperClientError(f"order {order_id} not found in session {session_id}")
        return order.to_dict()

    # ------------------------------------------------------------------
    # Positions
    # ------------------------------------------------------------------

    def get_positions(
        self,
        session_id: str,
        *,
        symbol: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Get positions for a session.

        Parameters
        ----------
        session_id : str
            Target session ID.
        symbol : str, optional
            Filter by symbol.

        Returns
        -------
        list[dict]
            List of position records.
        """
        session = self.get_session(session_id)
        positions = list(session.positions.values())
        if symbol:
            positions = [p for p in positions if p.symbol == _validate_symbol(symbol)]
        return [p.to_dict() for p in positions]

    # ------------------------------------------------------------------
    # PnL
    # ------------------------------------------------------------------

    def get_pnl(self, session_id: str) -> dict[str, Any]:
        """
        Get PnL summary for a session.

        Returns
        -------
        dict
            PnL summary with realized, unrealized, total fields.
        """
        session = self.get_session(session_id)
        realized = session.pnl
        unrealized = sum(
            pos.unrealized_pnl for pos in session.positions.values()
        )
        total_funding = sum(
            pos.accumulated_funding for pos in session.positions.values()
        )
        return {
            "realized_pnl": realized,
            "unrealized_pnl": unrealized,
            "total_pnl": realized + unrealized,
            "total_funding_cost": total_funding,
            "open_position_count": len(session.positions),
        }

    def get_session_status(self, session_id: str) -> dict[str, Any]:
        """
        Get full session status with positions, PnL, and orders.

        This is the canonical status payload expected by VAL-CLI-016.

        Returns
        -------
        dict
            Status with position, pnl, orders fields.
        """
        session = self.get_session(session_id)
        return {
            "session_id": session_id,
            "name": session.name,
            "created_at": session.created_at,
            "position": self.get_positions(session_id),
            "pnl": self.get_pnl(session_id),
            "orders": self.get_orders(session_id),
        }

    async def get_mark_prices(self) -> dict[str, float]:
        """Return current mark prices for all tracked symbols."""
        if self.feeds is None:
            return {}
        try:
            mark_data = await self.feeds.fetch_mark_prices()
            result: dict[str, float] = {}
            for entry in mark_data:
                sym = str(entry.get("symbol", ""))
                mp = entry.get("markPrice", "0")
                try:
                    result[sym] = float(mp)
                except (TypeError, ValueError):
                    continue
            return result
        except (SoDEXUpstreamError, SoDEXTransportError):
            return {}

    # ------------------------------------------------------------------
    # Kline processing (order matching and fills)
    # ------------------------------------------------------------------

    async def process_klines(
        self,
        session_id: str,
        klines: pd.DataFrame | list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        Process new klines for a session, matching open orders.

        Parameters
        ----------
        session_id : str
            Target session ID.
        klines : pd.DataFrame or list[dict]
            Kline data. Can be a DataFrame (from SoDEXFeeds) or raw kline
            dicts (with keys like o, h, l, c, t).

        Returns
        -------
        list[dict]
            List of fill events that occurred.
        """
        session = self.get_session(session_id)
        fills: list[dict[str, Any]] = []

        # Check for expired orders first (even with empty klines)
        expired = self._expire_orders(session)
        if expired:
            self._save_session_to_disk(session)

        # Convert DataFrame to list of dicts if needed
        if isinstance(klines, pd.DataFrame):
            if klines.empty:
                logger.warning(
                    "Empty klines for session %s — no orders processed",
                    session_id,
                )
                return fills
            kline_dicts = self._df_to_kline_dicts(klines)
        elif isinstance(klines, list):
            if not klines:
                logger.warning(
                    "Empty klines list for session %s — no orders processed",
                    session_id,
                )
                return fills
            kline_dicts = list(klines)
        else:
            raise PaperClientError(
                f"klines must be DataFrame or list, got {type(klines).__name__}"
            )

        # Process each kline
        for kline in kline_dicts:
            kline_fills = self._match_orders(session, kline)
            fills.extend(kline_fills)

        # Check for liquidations using kline close prices as mark prices
        mark_prices: dict[str, float] = {}
        for kline in kline_dicts:
            sym = kline.get("s", "")
            close = float(kline.get("c", 0))
            if close > 0:
                if sym:
                    mark_prices[sym] = close
                else:
                    # Kline without symbol applies to all positions
                    for pos_sym in session.positions:
                        mark_prices[pos_sym] = close
        liq_events = self._check_liquidation(session, mark_prices)

        if fills or liq_events:
            self._save_session_to_disk(session)
            logger.info(
                "Processed %d klines for session %s: %d fills",
                len(kline_dicts),
                session_id,
                len(fills),
            )

        return fills

    def _df_to_kline_dicts(self, df: pd.DataFrame) -> list[dict[str, Any]]:
        """Convert a klines DataFrame to a list of raw-style kline dicts."""
        kline_dicts: list[dict[str, Any]] = []
        for idx, row in df.iterrows():
            ts = int(pd.Timestamp(idx).timestamp() * 1000) if isinstance(idx, pd.Timestamp) else 0
            kline_dicts.append({
                "t": ts,
                "o": float(row.get("open", 0)),
                "h": float(row.get("high", 0)),
                "l": float(row.get("low", 0)),
                "c": float(row.get("close", 0)),
                "v": float(row.get("volume", 0)),
                "q": float(row.get("quote_volume", 0)),
            })
        return kline_dicts

    def _match_orders(
        self,
        session: PaperSession,
        kline: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Match a single kline against all open orders."""
        fills: list[dict[str, Any]] = []
        kline_symbol = kline.get("s", "")

        for order in list(session.orders.values()):
            if order.status != PaperOrderStatus.OPEN:
                continue
            if kline_symbol and order.symbol != kline_symbol:
                continue

            fill_price, did_fill = _compute_fill_price(
                kline, order.side, order.price, order.order_type
            )
            if did_fill:
                # Cap fill quantity based on kline volume (10% participation)
                kline_volume = float(kline.get("v", 0))
                if kline_volume > 0 and order.order_type == PaperOrderType.MARKET:
                    max_fill = kline_volume * 0.10
                    fill_qty = min(order.quantity, max_fill)
                    if fill_qty < order.quantity:
                        # Partial fill: fill what we can, create remainder order
                        original_qty = order.quantity
                        order.quantity = fill_qty
                        self._fill_order(session, order, fill_price)
                        # Create remainder order
                        remainder = PaperOrder(
                            order_id=str(uuid.uuid4()),
                            symbol=order.symbol,
                            side=order.side,
                            quantity=original_qty - fill_qty,
                            price=order.price,
                            order_type=order.order_type,
                            time_in_force=order.time_in_force,
                            expires_at=order.expires_at,
                        )
                        session.orders[remainder.order_id] = remainder
                    else:
                        self._fill_order(session, order, fill_price)
                else:
                    self._fill_order(session, order, fill_price)
                if cast(PaperOrderStatus, order.status) == PaperOrderStatus.FILLED:
                    fills.append(order.to_dict())

        return fills

    def _fill_order(
        self,
        session: PaperSession,
        order: PaperOrder,
        fill_price: float,
    ) -> None:
        """Execute a fill for an order."""
        # Apply slippage to market orders
        if order.order_type == PaperOrderType.MARKET:
            slip = fill_price * self.slippage_bps / 10_000
            if order.side == PaperOrderSide.BUY:
                fill_price = fill_price + slip
            else:
                fill_price = fill_price - slip

        # Reject orders below minimum notional
        notional = order.quantity * fill_price
        if notional < self.min_notional_usd:
            order.status = PaperOrderStatus.CANCELLED
            logger.warning(
                "Order %s cancelled: notional $%.2f below minimum $%.2f",
                order.order_id, notional, self.min_notional_usd,
            )
            return

        now = _now_timestamp()
        logger.info(
            "Filled order %s: %s %s %s @ %.4f",
            order.order_id,
            order.side.value,
            order.quantity,
            order.symbol,
            fill_price,
        )
        order.status = PaperOrderStatus.FILLED
        order.fill_price = fill_price
        order.fill_timestamp = now

        # Update position
        pos = session.positions.get(order.symbol)
        if pos is None:
            pos = PaperPosition(symbol=order.symbol)
            session.positions[order.symbol] = pos

        # Calculate PnL from this fill
        if pos.quantity != 0:
            # Reducing or increasing position
            if (pos.quantity > 0 and order.side == PaperOrderSide.SELL) or \
               (pos.quantity < 0 and order.side == PaperOrderSide.BUY):
                # Reducing position → realize PnL
                if pos.quantity > 0:  # Long, selling to reduce
                    pnl_realized = order.quantity * (fill_price - pos.entry_price)
                else:  # Short, buying to reduce
                    pnl_realized = order.quantity * (pos.entry_price - fill_price)
                session.pnl += pnl_realized
                pos.realized_pnl += pnl_realized

        # Update position quantity and entry price
        if order.side == PaperOrderSide.BUY:
            if pos.quantity >= 0:
                # Increasing long or flipping from short
                new_qty = pos.quantity + order.quantity
                if pos.quantity != 0:
                    # Average entry price
                    pos.entry_price = (
                        (pos.quantity * pos.entry_price + order.quantity * fill_price)
                        / new_qty
                    )
                else:
                    pos.entry_price = fill_price
                pos.quantity = new_qty
            else:
                # Reducing short
                pos.quantity += order.quantity
                if pos.quantity == 0:
                    pos.entry_price = 0.0
        else:  # SELL
            if pos.quantity <= 0:
                # Increasing short or flipping from long
                new_qty = pos.quantity - order.quantity
                if pos.quantity != 0:
                    pos.entry_price = (
                        (abs(pos.quantity) * pos.entry_price + order.quantity * fill_price)
                        / abs(new_qty)
                    )
                else:
                    pos.entry_price = fill_price
                pos.quantity = new_qty
            else:
                # Reducing long
                pos.quantity -= order.quantity
                if pos.quantity == 0:
                    pos.entry_price = 0.0

        # Mark positions with zero quantity for cleanup
        if pos.quantity == 0:
            del session.positions[order.symbol]

    def _check_liquidation(
        self,
        session: PaperSession,
        mark_prices: dict[str, float],
    ) -> list[dict[str, Any]]:
        """Check all positions for liquidation and force-close if margin breached."""
        liq_events: list[dict[str, Any]] = []
        for pos in list(session.positions.values()):
            mark = mark_prices.get(pos.symbol, pos.entry_price)
            notional = abs(pos.quantity) * mark
            mm = notional * session.maintenance_margin_rate
            # Compute unrealized PnL
            if pos.quantity > 0:
                unrealized = pos.quantity * (mark - pos.entry_price)
            else:
                unrealized = abs(pos.quantity) * (pos.entry_price - mark)
            equity = session.initial_balance + session.pnl + unrealized + pos.accumulated_funding
            if equity < mm:
                # Liquidate
                liq_slip = mark * 0.002  # 20 bps liquidation penalty
                close_price = mark * (1 - liq_slip) if pos.quantity > 0 else mark * (1 + liq_slip)
                liq_qty = abs(pos.quantity)
                pnl = liq_qty * (close_price - pos.entry_price) if pos.quantity > 0 else liq_qty * (pos.entry_price - close_price)
                session.pnl += pnl
                logger.warning(
                    "LIQUIDATION: %s qty=%.6f entry=%.4f close=%.4f pnl=%.2f",
                    pos.symbol, pos.quantity, pos.entry_price, close_price, pnl,
                )
                liq_events.append({
                    "symbol": pos.symbol,
                    "quantity": pos.quantity,
                    "entry_price": pos.entry_price,
                    "close_price": close_price,
                    "pnl": pnl,
                })
                del session.positions[pos.symbol]
        return liq_events

    def _expire_orders(self, session: PaperSession) -> bool:
        """Expire orders whose time_in_force has elapsed.

        Returns True if any orders were expired.
        """
        now = _now_timestamp()
        expired_any = False
        for order in list(session.orders.values()):
            if order.status != PaperOrderStatus.OPEN:
                continue
            if order.expires_at is not None and now >= order.expires_at:
                order.status = PaperOrderStatus.EXPIRED
                expired_any = True
                logger.info(
                    "Order %s expired (was %s %s %s)",
                    order.order_id,
                    order.side.value,
                    order.quantity,
                    order.symbol,
                )
        return expired_any

    # ------------------------------------------------------------------
    # Funding cost simulation
    # ------------------------------------------------------------------

    async def process_funding(
        self,
        session_id: str,
        *,
        force: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Apply funding costs to all open positions using real SoDEX
        funding rates.

        Funding is applied on 8-hour intervals. The method checks the
        current funding rate from SoDEX mark_prices and applies it to
        open positions.

        Parameters
        ----------
        session_id : str
            Target session ID.
        force : bool
            If True, apply funding even if not yet due (useful for testing).

        Returns
        -------
        list[dict]
            List of funding events applied.
        """
        session = self.get_session(session_id)
        if not session.positions:
            return []

        now = _now_timestamp()
        if not force and session.last_funding_time is not None:
            elapsed = now - session.last_funding_time
            if elapsed < FUNDING_INTERVAL_HOURS * 3600:
                return []

        # Fetch real funding rates from SoDEX mark prices
        if self.feeds is None:
            logger.warning(
                "Cannot fetch funding rates for session %s: feeds not available",
                session_id,
            )
            return []
        try:
            mark_prices = await self.feeds.fetch_mark_prices()
        except Exception as exc:
            logger.warning(
                "Failed to fetch funding rates for session %s: %s",
                session_id,
                exc,
            )
            return []

        # Build a map of symbol → funding_rate
        funding_map: dict[str, float] = {}
        price_map: dict[str, float] = {}
        for entry in mark_prices:
            sym = entry.get("symbol", "")
            fr_str = entry.get("fundingRate", "0")
            mp_str = entry.get("markPrice", "0")
            try:
                funding_map[sym] = float(fr_str)
                price_map[sym] = float(mp_str)
            except (TypeError, ValueError):
                continue

        funding_events: list[dict[str, Any]] = []
        for pos in list(session.positions.values()):
            if pos.quantity == 0:
                continue
            sym = pos.symbol
            funding_rate = funding_map.get(sym, 0.0)
            mark_price = price_map.get(sym, pos.entry_price or 0.0)

            if mark_price <= 0 or funding_rate == 0.0:
                continue

            cost = _compute_funding_cost(pos, mark_price, funding_rate)
            pos.accumulated_funding += cost
            session.pnl += cost

            funding_events.append({
                "symbol": sym,
                "funding_rate": funding_rate,
                "mark_price": mark_price,
                "cost": cost,
                "timestamp": now,
            })

        session.last_funding_time = now
        if funding_events:
            self._save_session_to_disk(session)
            logger.info(
                "Applied %d funding events for session %s",
                len(funding_events),
                session_id,
            )

        return funding_events

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _save_session_to_disk(self, session: PaperSession) -> None:
        """Persist session state to a JSON file (atomic write with retry)."""
        path = self.session_path(session.session_id)
        data = session.to_dict()
        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                fd, tmp_path = tempfile.mkstemp(
                    dir=str(self.sessions_dir), suffix=".tmp"
                )
                try:
                    with os.fdopen(fd, "w") as f:
                        json.dump(data, f)
                    os.replace(tmp_path, str(path))
                    npy_path = path.with_suffix(".npy")
                    np.save(str(npy_path), np.array(data, dtype=object), allow_pickle=True)
                except BaseException:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
                    raise
                return  # success
            except Exception as exc:
                last_exc = exc
                if attempt == 0:
                    logger.warning(
                        "Session %s save attempt %d failed, retrying: %s",
                        session.session_id,
                        attempt + 1,
                        exc,
                    )
        assert last_exc is not None
        logger.error("Failed to save session %s: %s", session.session_id, last_exc)
        raise last_exc

    def _load_session_from_disk(self, session_id: str) -> PaperSession:
        """Load session state from a JSON file (with npy fallback)."""
        path = self.session_path(session_id)
        # Try JSON first; fall back to legacy .npy path
        npy_path = path.with_suffix(".npy")
        if not path.exists() and not npy_path.exists():
            raise PaperSessionNotFoundError(
                f"session {session_id} not found (file {path} does not exist)"
            )
        try:
            data = self._read_session_file(path)
        except Exception as exc:
            raise PaperSessionNotFoundError(
                f"failed to load session {session_id}: {exc}"
            ) from exc
        return PaperSession.from_dict(data)

    @staticmethod
    def _read_session_file(path: Path) -> dict[str, Any]:
        """Read a session file (JSON first, legacy npy fallback)."""
        json_path = path if path.suffix == ".json" else path.with_suffix(".json")
        npy_path = path.with_suffix(".npy")

        # Prefer JSON
        if json_path.exists():
            with open(json_path, "r") as f:
                data: Any = json.load(f)
            if not isinstance(data, dict):
                raise PaperClientError(
                    f"expected dict in JSON file {json_path}, got {type(data).__name__}"
                )
            return data

        # Fall back to legacy npy
        if npy_path.exists():
            data: Any = np.load(str(npy_path), allow_pickle=True)  # type: ignore[no-redef]
            if isinstance(data, np.ndarray) and data.ndim == 0:
                data = data.item()
            if not isinstance(data, dict):
                raise PaperClientError(
                    f"expected dict in .npy file {npy_path}, got {type(data).__name__}"
                )
            return dict(data)

        raise PaperClientError(f"session file not found: {path}")

    async def close(self) -> None:
        """Release resources (no-op, kept for API consistency)."""
        if self.feeds is not None:
            await self.feeds.close()
