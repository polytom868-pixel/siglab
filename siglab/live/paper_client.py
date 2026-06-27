from __future__ import annotations

import json
import logging
import math
import os
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar, cast

import numpy as np
import pandas as pd

from siglab.config import load_settings
from siglab.data import MarketDataProvider, ParquetLake
from siglab.utils import dget
from siglab.evaluation.compile import compile_spec
from siglab.data.feeds import (
    SoDEXNonceManager,
    SoDEXPrivateKeySigner,
    SoDEXSignedPerpsClient,
    SoDEXSigner,
    SoDEXTransportError,
    SoDEXUpstreamError,
    perps_order_item,
    validate_account_id,
)
from siglab.utils import compute_composite_score, compute_position_size, CircuitBreakerState
from siglab.config import SignalSpec
import contextlib

if TYPE_CHECKING:
    from siglab.data.feeds import SoDEXFeeds
logger = logging.getLogger(__name__)
E = TypeVar("E", bound=Enum)
FUNDING_INTERVAL_HOURS = 8
DEFAULT_TIME_IN_FORCE_HOURS = 72


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

class PaperClientError(ValueError): ...


class PaperSessionNotFoundError(PaperClientError): ...


class PaperOrderStatus(str, Enum):
    OPEN, FILLED, CANCELLED, EXPIRED = "OPEN", "FILLED", "CANCELLED", "EXPIRED"


class PaperOrderSide(str, Enum):
    BUY, SELL = "BUY", "SELL"


class PaperOrderType(str, Enum):
    LIMIT, MARKET = "LIMIT", "MARKET"


class PaperTimeInForce(str, Enum):
    GTC, IOC, FOK, GTX = "GTC", "IOC", "FOK", "GTX"


@dataclass
class PaperOrder:
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
    def from_dict(cls: type[PaperOrder], data: dict[str, Any]) -> PaperOrder:
        d = dict(data)
        for k, t in [
            ("side", PaperOrderSide),
            ("order_type", PaperOrderType),
            ("time_in_force", PaperTimeInForce),
            ("status", PaperOrderStatus),
        ]:
            d[k] = t(d[k])
        return cls(**d)


@dataclass
class PaperPosition:
    symbol: str
    quantity: float = 0.0
    entry_price: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    accumulated_funding: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls: type[PaperPosition], data: dict[str, Any]) -> PaperPosition:
        return cls(**data)


@dataclass
class PaperSession:
    session_id: str
    name: str
    created_at: float = field(default_factory=time.time)
    orders: dict[str, PaperOrder] = field(default_factory=dict)
    positions: dict[str, PaperPosition] = field(default_factory=dict)
    pnl: float = 0.0
    last_funding_time: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    initial_balance: float = 10000.0
    maintenance_margin_rate: float = 0.005

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "name": self.name,
            "created_at": self.created_at,
            "orders": {oid: o.to_dict() for oid, o in self.orders.items()},
            "positions": {s: p.to_dict() for s, p in self.positions.items()},
            "pnl": self.pnl,
            "last_funding_time": self.last_funding_time,
            "metadata": self.metadata,
            "initial_balance": self.initial_balance,
            "maintenance_margin_rate": self.maintenance_margin_rate,
        }

    @classmethod
    def from_dict(cls: type[PaperSession], data: dict[str, Any]) -> PaperSession:
        s = cls(
            session_id=data["session_id"],
            name=data.get("name", data["session_id"]),
            created_at=data.get("created_at", time.time()),
            pnl=data.get("pnl", 0.0),
            last_funding_time=data.get("last_funding_time"),
            metadata=dict(data.get("metadata", {})),
            initial_balance=data.get("initial_balance", 10000.0),
            maintenance_margin_rate=data.get("maintenance_margin_rate", 0.005),
        )
        for oid, od in data.get("orders", {}).items():
            s.orders[oid] = PaperOrder.from_dict(od)
        for sym, pd_ in data.get("positions", {}).items():
            s.positions[sym] = PaperPosition.from_dict(pd_)
        return s


def _gen_sid() -> str:
    return uuid.uuid4().hex[:12]


def _gen_oid() -> str:
    return uuid.uuid4().hex[:16]


def _ts() -> float:
    return time.time()


def _val_sym(symbol: str) -> str:
    s = str(symbol).strip().upper()
    if not s:
        raise PaperClientError("symbol must not be empty")
    if len(s) < 3:
        raise PaperClientError(f"invalid symbol: {s!r}")
    return s


def _to_pf(value: float | str, *, name: str) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise PaperClientError(f"{name} must be a number, got {value!r}")
    if v <= 0:
        raise PaperClientError(f"{name} must be positive, got {v}")
    if v > 1000000000000.0:
        raise PaperClientError(f"{name} too large: {v}")
    return v


def _val_qty(quantity: float) -> float:
    return _to_pf(quantity, name="quantity")


def _val_pr(price: float | None, ot: PaperOrderType) -> float | None:
    if ot == PaperOrderType.MARKET:
        return None
    if price is None:
        raise PaperClientError("price is required for LIMIT orders")
    return _to_pf(price, name="price")


def _coerce(value: str, ec: type[E], expected: str) -> E:
    try:
        return ec(value.upper())
    except ValueError as exc:
        raise PaperClientError(f"invalid {expected}: {value!r}") from exc


def _val_side(side: str) -> PaperOrderSide:
    return _coerce(side, PaperOrderSide, "side; expected BUY or SELL")


def _val_ot(ot: str) -> PaperOrderType:
    return _coerce(ot, PaperOrderType, "order_type; expected LIMIT or MARKET")


def _val_tif(tif: str) -> PaperTimeInForce:
    return _coerce(
        tif,
        PaperTimeInForce,
        "time_in_force; expected GTC, IOC, FOK, or GTX",
    )


_validate_symbol = _val_sym
_validate_quantity = _val_qty
_validate_price = _val_pr
_validate_side = _val_side
_validate_order_type = _val_ot
_validate_time_in_force = _val_tif
_to_positive_float = _to_pf
_coerce_enum = _coerce


class SoDEXPaperPerpsClient:
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
        self._ss: dict[str, PaperSession] = {}

    def create_session(self, name: str | None = None) -> str:
        sid = _gen_sid()
        s = PaperSession(session_id=sid, name=name or sid)
        self._ss[sid] = s
        self._save_ss(s)
        logger.info("Created paper session %s (name=%s)", sid, s.name)
        return sid

    def get_session(self, session_id: str) -> PaperSession:
        if session_id not in self._ss:
            self._ss[session_id] = self._load_ss(session_id)
        return self._ss[session_id]

    def list_sessions(self) -> list[dict[str, Any]]:
        rv: list[dict[str, Any]] = []
        seen: set[str] = set()
        for g, p in [("*.json", None), ("*.npy", None)]:
            g = g if p is None else "*.npy"
            for path in sorted(self.sessions_dir.glob(g)):
                if g == "*.npy" and path.stem in seen:
                    continue
                if self._append_ss(rv, path) and g == "*.json":
                    seen.add(path.stem)
        return rv

    def _append_ss(self, rv: list[dict[str, Any]], path: Path) -> bool:
        try:
            data = self._read_sf(path)
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            logger.warning("Failed to read session file %s", path)
            return False
        rv.append(
            {
                "session_id": data.get("session_id", path.stem),
                "name": data.get("name", path.stem),
                "created_at": data.get("created_at", 0.0),
                "order_count": len(data.get("orders", {})),
                "position_count": len(data.get("positions", {})),
                "pnl": data.get("pnl", 0.0),
            },
        )
        return True

    def session_path(self, session_id: str) -> Path:
        if "/" in session_id or ".." in session_id or "\\" in session_id:
            raise PaperClientError("Invalid session_id")
        return self.sessions_dir / f"{session_id}.json"

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
        sym = _val_sym(symbol)
        qty = _val_qty(quantity)
        se = _val_side(side)
        oe = _val_ot(order_type)
        te = _val_tif(time_in_force)
        vp = _val_pr(price, oe)
        s = self.get_session(session_id)
        now = _ts()
        oid = _gen_oid()
        ex = now + (
            60
            if te == PaperTimeInForce.IOC
            else 10
            if te == PaperTimeInForce.FOK
            else DEFAULT_TIME_IN_FORCE_HOURS * 3600
        )
        o = PaperOrder(
            order_id=oid,
            symbol=sym,
            side=se,
            quantity=qty,
            price=vp or 0.0,
            order_type=oe,
            time_in_force=te,
            created_at=now,
            expires_at=ex,
        )
        if oe == PaperOrderType.MARKET:
            o.status = PaperOrderStatus.OPEN
        s.orders[oid] = o
        self._save_ss(s)
        logger.info(
            "Placed %s %s %s order %s for %s @ %s",
            se.value,
            oe.value,
            sym,
            oid,
            qty,
            vp or "market",
        )
        return o.to_dict()

    def cancel_order(self, session_id: str, order_id: str) -> dict[str, Any]:
        s = self.get_session(session_id)
        o = s.orders.get(order_id)
        if o is None:
            raise PaperClientError(
                f"order {order_id} not found in session {session_id}",
            )
        if o.status != PaperOrderStatus.OPEN:
            raise PaperClientError(
                f"cannot cancel order {order_id} with status {o.status.value}",
            )
        o.status = PaperOrderStatus.CANCELLED
        o.cancelled_at = _ts()
        self._save_ss(s)
        logger.info("Cancelled order %s (session %s)", order_id, session_id)
        return o.to_dict()

    def get_orders(
        self,
        session_id: str,
        *,
        symbol: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        s = self.get_session(session_id)
        orders = list(s.orders.values())
        if symbol:
            orders = [o for o in orders if o.symbol == _val_sym(symbol)]
        if status:
            try:
                se = PaperOrderStatus(status.upper())
            except ValueError:
                raise PaperClientError(f"invalid order status: {status!r}")
            orders = [o for o in orders if o.status == se]
        return [
            o.to_dict()
            for o in sorted(orders, key=lambda o: o.created_at, reverse=True)
        ]

    def get_order(self, session_id: str, order_id: str) -> dict[str, Any]:
        s = self.get_session(session_id)
        o = s.orders.get(order_id)
        if o is None:
            raise PaperClientError(
                f"order {order_id} not found in session {session_id}",
            )
        return o.to_dict()

    def get_positions(
        self,
        session_id: str,
        *,
        symbol: str | None = None,
    ) -> list[dict[str, Any]]:
        s = self.get_session(session_id)
        ps = list(s.positions.values())
        if symbol:
            ps = [p for p in ps if p.symbol == _val_sym(symbol)]
        return [p.to_dict() for p in ps]

    def get_pnl(self, session_id: str) -> dict[str, Any]:
        s = self.get_session(session_id)
        r = s.pnl
        u = sum(p.unrealized_pnl for p in s.positions.values())
        f = sum(p.accumulated_funding for p in s.positions.values())
        return {
            "realized_pnl": r,
            "unrealized_pnl": u,
            "total_pnl": r + u,
            "total_funding_cost": f,
            "open_position_count": len(s.positions),
        }

    def get_session_status(self, session_id: str) -> dict[str, Any]:
        s = self.get_session(session_id)
        return {
            "session_id": session_id,
            "name": s.name,
            "created_at": s.created_at,
            "position": self.get_positions(session_id),
            "pnl": self.get_pnl(session_id),
            "orders": self.get_orders(session_id),
        }

    async def get_mark_prices(self) -> dict[str, float]:
        if self.feeds is None:
            return {}
        try:
            md = await self.feeds.fetch_mark_prices()
            rv: dict[str, float] = {}
            for e in md:
                try:
                    rv[str(e.get("symbol", ""))] = float(e.get("markPrice", "0"))
                except (TypeError, ValueError):
                    continue
            return rv
        except (SoDEXUpstreamError, SoDEXTransportError):
            return {}

    async def process_klines(
        self,
        session_id: str,
        klines: pd.DataFrame | list[dict[str, Any]],
        *,
        symbol: str | None = None,
    ) -> list[dict[str, Any]]:
        s = self.get_session(session_id)
        fills: list[dict[str, Any]] = []
        if self._expire(s):
            self._save_ss(s)
        if isinstance(klines, pd.DataFrame):
            if klines.empty:
                logger.warning(
                    "Empty klines for session %s — no orders processed",
                    session_id,
                )
                return fills
            kd = self._df_to_kd(klines, symbol=symbol)
        elif isinstance(klines, list):
            if not klines:
                logger.warning(
                    "Empty klines list for session %s — no orders processed",
                    session_id,
                )
                return fills
            kd = list(klines)
            if symbol:
                for k in kd:
                    k.setdefault("s", symbol)
        else:
            raise PaperClientError(
                f"klines must be DataFrame or list, got {type(klines).__name__}",
            )
        for k in kd:
            fills.extend(self._match(s, k))
        mp: dict[str, float] = {}
        for k in kd:
            sym = k.get("s", "")
            close = float(k.get("c", 0))
            if close > 0:
                if sym:
                    mp[sym] = close
                else:
                    for ps in s.positions:
                        mp[ps] = close
        liq = self._chk_liq(s, mp)
        if fills or liq:
            self._save_ss(s)
            logger.info(
                "Processed %d klines for session %s: %d fills",
                len(kd),
                session_id,
                len(fills),
            )
        return fills

    def _df_to_kd(
        self,
        df: pd.DataFrame,
        *,
        symbol: str | None = None,
    ) -> list[dict[str, Any]]:
        return [
            {
                "t": int(pd.Timestamp(idx).timestamp() * 1000)
                if isinstance(idx, pd.Timestamp)
                else 0,
                "o": float(r.get("open", 0)),
                "h": float(r.get("high", 0)),
                "l": float(r.get("low", 0)),
                "c": float(r.get("close", 0)),
                "v": float(r.get("volume", 0)),
                "q": float(r.get("quote_volume", 0)),
                "s": symbol or "",
            }
            for idx, r in df.iterrows()
        ]

    def _match(self, s: PaperSession, k: dict[str, Any]) -> list[dict[str, Any]]:
        fills: list[dict[str, Any]] = []
        ks = k.get("s", "")
        for o in list(s.orders.values()):
            if o.status != PaperOrderStatus.OPEN:
                continue
            if ks and o.symbol != ks:
                continue
            fp, did = calculate_fill_price(
                float(k.get("c", 0)),
                float(k.get("h", 0)),
                float(k.get("l", 0)),
                float(k.get("o", 0)),
                o.side.value,
                o.price,
                o.order_type.value,
            )
            if did:
                kv = float(k.get("v", 0))
                if kv > 0 and o.order_type == PaperOrderType.MARKET:
                    mf = kv * 0.1
                    fq = min(o.quantity, mf)
                    if fq < o.quantity:
                        oq = o.quantity
                        o.quantity = fq
                        self._fill(s, o, fp)
                        r = PaperOrder(
                            order_id=str(uuid.uuid4()),
                            symbol=o.symbol,
                            side=o.side,
                            quantity=oq - fq,
                            price=o.price,
                            order_type=o.order_type,
                            time_in_force=o.time_in_force,
                            expires_at=o.expires_at,
                        )
                        s.orders[r.order_id] = r
                    else:
                        self._fill(s, o, fp)
                else:
                    self._fill(s, o, fp)
                if cast(PaperOrderStatus, o.status) == PaperOrderStatus.FILLED:
                    fills.append(o.to_dict())
        return fills

    def _fill(self, s: PaperSession, o: PaperOrder, fp: float) -> None:
        if o.order_type == PaperOrderType.MARKET:
            sl = fp * self.slippage_bps / 10000
            fp = fp + sl if o.side == PaperOrderSide.BUY else fp - sl
        n = o.quantity * fp
        if n < self.min_notional_usd:
            o.status = PaperOrderStatus.CANCELLED
            logger.warning(
                "Order %s cancelled: notional $%.2f below minimum $%.2f",
                o.order_id,
                n,
                self.min_notional_usd,
            )
            return
        now = _ts()
        logger.info(
            "Filled order %s: %s %s %s @ %.4f",
            o.order_id,
            o.side.value,
            o.quantity,
            o.symbol,
            fp,
        )
        o.status = PaperOrderStatus.FILLED
        o.fill_price = fp
        o.fill_timestamp = now
        pos = s.positions.get(o.symbol)
        if pos is None:
            pos = PaperPosition(symbol=o.symbol)
            s.positions[o.symbol] = pos
        if pos.quantity == 0:
            pos.quantity = o.quantity if o.side == PaperOrderSide.BUY else -o.quantity
            pos.entry_price = fp
        else:
            lp = pos.quantity > 0
            rd = (lp and o.side == PaperOrderSide.SELL) or (
                not lp and o.side == PaperOrderSide.BUY
            )
            if rd:
                cq = min(o.quantity, abs(pos.quantity))
                pnl = compute_trade_pnl(
                    fp,
                    cq,
                    pos.quantity,
                    pos.entry_price,
                    o.side.value,
                )
                s.pnl += pnl
                pos.realized_pnl += pnl
                rm = o.quantity - cq
                if rm > 0:
                    pos.quantity = rm if o.side == PaperOrderSide.BUY else -rm
                    pos.entry_price = fp
                else:
                    if lp:
                        pos.quantity -= o.quantity
                    else:
                        pos.quantity += o.quantity
                    if pos.quantity == 0:
                        pos.entry_price = 0.0
            else:
                aq = o.quantity if o.side == PaperOrderSide.BUY else -o.quantity
                nq = pos.quantity + aq
                pos.entry_price = compute_avg_entry(
                    pos.quantity,
                    pos.entry_price,
                    aq,
                    fp,
                )
                pos.quantity = nq
        if pos.quantity == 0:
            del s.positions[o.symbol]

    def _chk_liq(self, s: PaperSession, mp: dict[str, float]) -> list[dict[str, Any]]:
        ev: list[dict[str, Any]] = []
        for pos in list(s.positions.values()):
            mk = mp.get(pos.symbol, pos.entry_price)
            nt = abs(pos.quantity) * mk
            mm = nt * s.maintenance_margin_rate
            u = (
                pos.quantity * (mk - pos.entry_price)
                if pos.quantity > 0
                else abs(pos.quantity) * (pos.entry_price - mk)
            )
            eq = s.initial_balance + s.pnl + u + pos.accumulated_funding
            if eq < mm:
                ls_ = mk * 0.002
                cp = mk * (1 - ls_) if pos.quantity > 0 else mk * (1 + ls_)
                lq = abs(pos.quantity)
                pnl = (
                    lq * (cp - pos.entry_price)
                    if pos.quantity > 0
                    else lq * (pos.entry_price - cp)
                )
                s.pnl += pnl
                logger.warning(
                    "LIQUIDATION: %s qty=%.6f entry=%.4f close=%.4f pnl=%.2f",
                    pos.symbol,
                    pos.quantity,
                    pos.entry_price,
                    cp,
                    pnl,
                )
                ev.append(
                    {
                        "symbol": pos.symbol,
                        "quantity": pos.quantity,
                        "entry_price": pos.entry_price,
                        "close_price": cp,
                        "pnl": pnl,
                    },
                )
                del s.positions[pos.symbol]
        return ev

    def _expire(self, s: PaperSession) -> bool:
        now = _ts()
        any_ = False
        for o in list(s.orders.values()):
            if o.status != PaperOrderStatus.OPEN:
                continue
            if o.expires_at is not None and now >= o.expires_at:
                o.status = PaperOrderStatus.EXPIRED
                any_ = True
                logger.info(
                    "Order %s expired (was %s %s %s)",
                    o.order_id,
                    o.side.value,
                    o.quantity,
                    o.symbol,
                )
        return any_

    async def process_funding(
        self,
        session_id: str,
        *,
        force: bool = False,
    ) -> list[dict[str, Any]]:
        s = self.get_session(session_id)
        if not s.positions:
            return []
        now = _ts()
        if (
            not force
            and s.last_funding_time is not None
            and now - s.last_funding_time < FUNDING_INTERVAL_HOURS * 3600
        ):
            return []
        if self.feeds is None:
            logger.warning(
                "Cannot fetch funding rates for session %s: feeds not available",
                session_id,
            )
            return []
        try:
            mp = await self.feeds.fetch_mark_prices()
        except (OSError, ValueError) as exc:
            logger.warning(
                "Failed to fetch funding rates for session %s: %s",
                session_id,
                exc,
            )
            return []
        fm: dict[str, float] = {}
        pm: dict[str, float] = {}
        for e in mp:
            sym = e.get("symbol", "")
            fr = e.get("fundingRate", "0")
            mps = e.get("markPrice", "0")
            try:
                fm[sym] = float(fr)
                pm[sym] = float(mps)
            except (TypeError, ValueError):
                continue
        ev: list[dict[str, Any]] = []
        for pos in list(s.positions.values()):
            if pos.quantity == 0:
                continue
            fr = fm.get(pos.symbol, 0.0)
            mk = pm.get(pos.symbol, pos.entry_price or 0.0)
            if mk <= 0 or fr == 0.0:
                continue
            c = compute_funding_cost(pos.quantity, mk, fr)
            pos.accumulated_funding += c
            s.pnl += c
            ev.append(
                {
                    "symbol": pos.symbol,
                    "funding_rate": fr,
                    "mark_price": mk,
                    "cost": c,
                    "timestamp": now,
                },
            )
        s.last_funding_time = now
        if ev:
            self._save_ss(s)
            logger.info("Applied %d funding events for session %s", len(ev), session_id)
        return ev

    def _save_ss(self, s: PaperSession) -> None:
        path = self.session_path(s.session_id)
        data = s.to_dict()
        fd, tmp = tempfile.mkstemp(dir=str(self.sessions_dir), suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f)
            os.replace(tmp, str(path))
        except (OSError, TypeError, ValueError):
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    def _load_ss(self, session_id: str) -> PaperSession:
        path = self.session_path(session_id)
        np_ = path.with_suffix(".npy")
        if not path.exists() and not np_.exists():
            raise PaperSessionNotFoundError(
                f"session {session_id} not found (file {path} does not exist)",
            )
        try:
            data = self._read_sf(path)
        except Exception as exc:
            raise PaperSessionNotFoundError(
                f"failed to load session {session_id}: {exc}",
            ) from exc
        return PaperSession.from_dict(data)

    @staticmethod
    def _read_sf(path: Path) -> dict[str, Any]:
        jp = path if path.suffix == ".json" else path.with_suffix(".json")
        np_ = path.with_suffix(".npy")
        if jp.exists():
            with open(jp) as f:
                d: Any = json.load(f)
            if not isinstance(d, dict):
                raise PaperClientError(
                    f"expected dict in JSON file {jp}, got {type(d).__name__}",
                )
            return d
        if np_.exists():
            d = np.load(str(np_), allow_pickle=True)
            if isinstance(d, np.ndarray) and d.ndim == 0:
                d = d.item()
            if not isinstance(d, dict):
                raise PaperClientError(
                    f"expected dict in .npy file {np_}, got {type(d).__name__}",
                )
            return dict(d)
        raise PaperClientError(f"session file not found: {path}")

    _save_session_to_disk = _save_ss


# Runtime classes
StatusDict = dict[str, Any]
StatusTuple = tuple[bool, str]


def _check_live() -> None:
    if os.environ.get("SIGLAB_LIVE_ENABLED") != "1":
        raise RuntimeError("dry_run=False requires SIGLAB_LIVE_ENABLED=1")


class Strategy:
    def __init__(self, *, dry_run: bool, **kwargs: Any) -> None:
        if not isinstance(dry_run, bool):
            raise TypeError("dry_run must be a bool")
        if not dry_run:
            _check_live()
        self.dry_run = dry_run
        self.config = kwargs.get("config", {})
        self.name = kwargs.get("name", self.__class__.__name__)


class SoDEXExecutionAdapter:
    coin_to_asset: dict[str, int] = {}

    def __init__(self, config: dict[str, Any] | None = None, **kwargs: Any) -> None:
        self.config = config or {}
        self.client = self.config.get("sodex_client")
        self.wallet_address = kwargs.get("wallet_address")
        self.coin_to_asset = (
            self.config.get("coin_to_asset")
            or getattr(self.client, "coin_to_asset", {})
            or {}
        )

    def _read_signing_config(self) -> dict[str, Any]:
        sg = self.config.get("sodex_signing") or {}
        akn = sg.get("api_key_name") or self.config.get("sodex_api_key_name")
        aid = (
            sg.get("accountID")
            or sg.get("account_id")
            or self.config.get("sodex_account_id")
        )
        env = sg.get("environment") or self.config.get("sodex_environment") or "testnet"
        nsp = sg.get("nonce_store_path") or self.config.get("sodex_nonce_store_path")
        pk = sg.get("private_key") or self.config.get("sodex_private_key")
        signer: SoDEXSigner | None = sg.get("signer")
        return {
            "sg": sg,
            "akn": akn,
            "aid": aid,
            "env": env,
            "nsp": nsp,
            "pk": pk,
            "signer": signer,
        }

    def setup(self) -> dict[str, Any]:
        if self.client is not None:
            return self.dependency_report()
        cfg = self._read_signing_config()
        sg = cfg["sg"]
        akn = cfg["akn"]
        aid_r = cfg["aid"]
        env = cfg["env"]
        nsp = cfg["nsp"]
        pk = cfg["pk"]
        signer = cfg["signer"]
        if signer is None and pk:
            signer = SoDEXPrivateKeySigner(private_key=pk, environment=env)
        if not (bool(akn) and aid_r is not None and signer is not None):
            return self.dependency_report()
        assert aid_r is not None
        aid = validate_account_id(aid_r)
        sp = Path(nsp) if nsp else None
        nm = SoDEXNonceManager(store_path=sp, environment=env)
        sg["signer"] = signer
        self.config["sodex_signing"] = sg
        self.client = SoDEXSignedPerpsClient(
            api_key_name=str(akn),
            account_id=aid,
            signer=signer,
            nonce_manager=nm,
            environment=str(env),
            dry_run=bool(sg.get("dry_run", self.config.get("sodex_dry_run", True))),
        )
        if not self.coin_to_asset:
            self.coin_to_asset = dict(getattr(self.client, "coin_to_asset", {}) or {})
        return self.dependency_report()

    async def update_leverage(self, **kwargs: Any) -> None:
        await _await_if(
            self._resolve_meth("update_leverage", fallback="update_leverage_request")(
                **kwargs,
            ),
        )

    async def place_market_order(self, **kwargs: Any) -> tuple[bool, str]:
        c = self._req_client()
        ai = int(kwargs.get("asset_id", 0))
        ib = bool(kwargs.get("is_buy", True))
        sz = float(kwargs.get("size", 0))
        ro = bool(kwargs.get("reduce_only", False))
        o = perps_order_item(
            cl_ord_id=f"siglab_{uuid.uuid4().hex[:12]}",
            modifier=1,
            side=1 if ib else 2,
            order_type=2,
            time_in_force=1,
            quantity=str(sz),
            reduce_only=ro,
        )
        r = await c.send_signed_request(c.new_order_request(symbol_id=ai, orders=[o]))
        if isinstance(r, dict):
            if r.get("dry_run"):
                return (True, "dry-run market order submitted")
            ok = bool(r.get("ok", r.get("success", False)))
            return (ok, str(r.get("message") or r.get("result") or r))
        return (bool(r), str(r))

    async def get_state(self, *_a: Any, **_kw: Any) -> dict[str, Any]:
        ok, state = await self.get_user_state(*_a, **_kw)
        if not ok or not isinstance(state, dict):
            raise RuntimeError(f"Unable to fetch SoDEX state: {state}")
        return state

    async def get_user_state(self, *args: Any, **kwargs: Any) -> tuple[bool, Any]:
        r = await _await_if(
            self._resolve_meth("get_user_state", fallback="get_state")(*args, **kwargs),
        )
        return (bool(r[0]), r[1]) if isinstance(r, tuple) and len(r) == 2 else (True, r)

    async def all_mids(self) -> dict[str, float]:
        return {
            str(s).upper(): float(p)
            for s, p in dict(
                await _await_if(self._resolve_meth("all_mids")()) or {},
            ).items()
        }

    def get_valid_order_size(self, _ai: int, size: float) -> float:
        m = (
            getattr(self.client, "get_valid_order_size", None)
            if self.client is not None
            else None
        )
        return float(m(_ai, size)) if m else float(size)

    def _req_client(self) -> Any:
        if self.client is None:
            raise RuntimeError(
                "A real SoDEX client must be provided in runtime config before live execution",
            )
        return self.client

    def _resolve_meth(self, name: str, *, fallback: str | None = None) -> Any:
        c = self._req_client()
        m = getattr(c, name, None)
        if m is None and fallback is not None:
            m = getattr(c, fallback, None)
            missing = f"{name}() or {fallback}()"
        else:
            missing = f"{name}()"
        if m is None:
            raise RuntimeError(f"Configured SoDEX client does not provide {missing}")
        return m

    def dependency_report(self) -> dict[str, Any]:
        c = self.client
        cfg = self._read_signing_config()
        sg = cfg["sg"]
        akn = cfg["akn"]
        aid = cfg["aid"]
        env = cfg["env"]
        ns = cfg["nsp"]
        signer = cfg["signer"]
        req = {
            "get_user_state": bool(
                getattr(c, "get_user_state", None)
                or getattr(c, "get_state", None)
                or getattr(c, "account_state", None),
            ),
            "update_leverage": bool(
                getattr(c, "update_leverage", None)
                or getattr(c, "update_leverage_request", None),
            ),
            "place_market_order": bool(
                getattr(c, "new_order_request", None)
                and getattr(c, "send_signed_request", None),
            ),
            "all_mids": bool(
                getattr(c, "all_mids", None)
                or getattr(c, "mark_prices", None)
                or getattr(c, "tickers", None),
            ),
        }
        sr = all(
            [
                c is not None,
                signer is not None,
                bool(akn),
                aid is not None,
                bool(ns),
                not [n for n, p in req.items() if not p],
            ],
        )
        ms: list[str] = []
        if signer is None:
            ms.append("signer")
        if not akn:
            ms.append("api_key_name")
        if aid is None:
            ms.append("accountID")
        if not ns:
            ms.append("nonce_store_path")
        ms.extend([f"client.{n}" for n, p in req.items() if not p])
        return {
            "client_configured": c is not None,
            "required_methods": req,
            "missing_methods": [n for n, p in req.items() if not p],
            "wallet_address_configured": bool(self.wallet_address),
            "dry_run": getattr(c, "dry_run", True) if c is not None else True,
            "signed_path": {
                "ready": sr,
                "environment": str(env),
                "signer_available": signer is not None,
                "signer_type": getattr(signer, "signer_type", None)
                if signer is not None
                else None,
                "accountID_present": aid is not None,
                "api_key_name_present": bool(akn),
                "nonce_store_ready": bool(ns),
                "missing_prerequisites": ms,
            },
            "rate_limit_scope": {
                "budget_per_minute": 1200,
                "scope": "per_ip",
                "local_scheduler_only": True,
                "operator_warning": "SigLab's built-in SoDEX weight scheduler is process-local. Use an external shared limiter when multiple processes share one egress IP.",
            },
        }


def _ff(value: float | str | None, default: float = 0.0) -> float:
    try:
        n = float(value) if value is not None else default
    except (TypeError, ValueError):
        return default
    return n if math.isfinite(n) else default


def _cw(payload: dict[str, float], *, epsilon: float = 1e-09) -> dict[str, float]:
    return {
        str(s): round(float(w), 6)
        for s, w in payload.items()
        if abs(float(w)) > epsilon
    }


async def _await_if(result: Any) -> Any:
    return await result if hasattr(result, "__await__") else result


class DirectionalPerpsSigLabStrategy(Strategy):
    SPEC_PATH: Path | None = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.live_spec: dict[str, Any] = {}
        self.spec: SignalSpec | None = None
        self.sodex_adapter: SoDEXExecutionAdapter | None = None
        self._cb = CircuitBreakerState()

    def _wallet_addr(self) -> str:
        r = self.live_spec.get("runtime") or {}
        a = r.get("wallet_address") or r.get("address") or ""
        return str(a)

    def _adr_dry(self) -> bool:
        return (
            True
            if self.sodex_adapter is None or self.sodex_adapter.client is None
            else bool(getattr(self.sodex_adapter.client, "dry_run", True))
        )

    async def setup(self) -> None:
        self.live_spec = self._load_spec()
        self.spec = SignalSpec.from_dict(self.live_spec["spec"])
        self.name = str(
            self.live_spec.get("strategy_name") or self.spec.strategy_hash(),
        )
        self.sodex_adapter = SoDEXExecutionAdapter(
            self.config if isinstance(self.config, dict) else {},
            wallet_address=self._wallet_addr(),
        )
        rp = self.sodex_adapter.setup()
        if not self._adr_dry():
            sp = rp.get("signed_path") or {}
            if (
                not rp["client_configured"]
                or rp["missing_methods"]
                or not sp.get("ready")
            ):
                raise RuntimeError(f"Live SoDEX dependencies are incomplete: {rp}")

    async def deposit(self, **kwargs: Any) -> StatusTuple:
        return (
            False,
            "Deposit is not automated for siglab-generated perp strategies. Fund the configured strategy wallet / venue, then run update().",
        )

    async def update(self) -> StatusTuple:
        st = await self._build_status(include_trade_plan=True)
        ss = st["strategy_status"]
        plan = list(ss.get("trade_plan") or [])
        av = float(st.get("portfolio_value", 0.0))
        dr = self._adr_dry()
        self._cb.equity = av
        if self._cb.daily_start_equity <= 0.0:
            self._cb.daily_start_equity = av
        if self._cb.peak_equity <= 0.0 or av > self._cb.peak_equity:
            self._cb.peak_equity = av
        cb_p, cb_r = self._cb.check_circuit_breakers()
        if not cb_p:
            return (False, f"Circuit breaker tripped: {cb_r}")
        if not plan:
            return (True, "No rebalance needed")
        if dr:
            return (True, f"Dry run generated {len(plan)} rebalance orders")
        pdd = (
            (av - self._cb.peak_equity) / self._cb.peak_equity
            if self._cb.peak_equity > 0
            else 0.0
        )
        ddr = abs(pdd)
        if ddr >= 0.2:
            return (False, f"20% drawdown stop triggered: {pdd:.1%}")
        if ddr >= 0.15:
            for o in plan:
                o["size"] = _ff(o["size"]) * 0.5
        elif ddr >= 0.1:
            for o in plan:
                o["size"] = _ff(o["size"]) * 0.75
        ad = self._req_adapter()
        addr = self._wallet_addr()
        rt = self.live_spec.get("runtime") or {}
        lv = max(1, math.ceil(_ff(rt.get("live_leverage"), 1.0)))
        ex = 0
        for o in plan:
            sym = str(o["symbol"])
            ov = _ff(o.get("delta_usd", 0.0))
            if ov > av * self._cb.max_position_pct:
                continue
            ai = ad.coin_to_asset.get(sym)
            if ai is None:
                raise ValueError(f"SoDEX asset id not found for {sym}")
            await ad.update_leverage(
                asset_id=ai,
                leverage=lv,
                is_cross=True,
                address=addr,
            )
            ok_, r_ = await ad.place_market_order(
                asset_id=ai,
                is_buy=bool(o["is_buy"]),
                slippage=_ff(rt.get("slippage"), 0.0035),
                size=ad.get_valid_order_size(ai, _ff(o["size"])),
                address=addr,
                reduce_only=bool(o.get("reduce_only", False)),
            )
            if not ok_:
                return (False, f"{sym} order failed: {r_}")
            ex += 1
        return (True, f"Executed {ex} rebalance orders")

    async def withdraw(self, **kwargs: Any) -> StatusTuple:
        st = await self._build_status(include_trade_plan=False)
        cp = st["strategy_status"].get("current_positions") or {}
        if not cp:
            return (True, "No open perp positions to close")
        if self._adr_dry():
            return (True, f"Dry run would close {len(cp)} perp positions")
        rt = self.live_spec.get("runtime") or {}
        ad = self._req_adapter()
        addr = self._wallet_addr()
        cl = 0
        for sym, qty in cp.items():
            ai = ad.coin_to_asset.get(sym)
            if ai is None:
                continue
            sz = ad.get_valid_order_size(ai, abs(_ff(qty)))
            if sz <= 0:
                continue
            ok_, r_ = await ad.place_market_order(
                asset_id=ai,
                is_buy=_ff(qty) < 0.0,
                slippage=_ff(rt.get("slippage"), 0.0035),
                size=sz,
                address=addr,
                reduce_only=True,
            )
            if not ok_:
                return (False, f"{sym} close failed: {r_}")
            cl += 1
        return (True, f"Closed {cl} perp positions")

    async def exit(self, **kwargs: Any) -> StatusTuple:
        return await self.withdraw(**kwargs)

    @staticmethod
    async def policies() -> list[str]:
        return []

    async def _status(self) -> StatusDict:
        return await self._build_status(include_trade_plan=True)

    async def dependency_report(self) -> dict[str, Any]:
        if self.sodex_adapter is None:
            await self.setup()
        ar = self._req_adapter().dependency_report()
        return {
            "strategy_name": self.name,
            "spec_hash": self.live_spec.get("spec_hash"),
            "runtime": self.live_spec.get("runtime") or {},
            "sodex_adapter": ar,
        }

    async def _build_status(self, *, include_trade_plan: bool) -> StatusDict:
        if self.spec is None:
            await self.setup()
        addr = self._wallet_addr()
        ts_ = await self._target_snap()
        st = await self._user_state(addr)
        cp = self._perp_pos(st)
        av = self._acct_val(st)
        nd = await self._net_dep(addr, fallback=av)
        rt = self.live_spec.get("runtime") or {}
        lv = _ff(rt.get("live_leverage"), 1.0)
        tp = (
            self._trade_plan(
                target_weights=ts_["target_weights"],
                current_positions=cp,
                mids=ts_["mid_prices"],
                account_value=av,
                leverage=lv,
                min_trade_usd=_ff(rt.get("min_trade_usd"), 25.0),
                circuit_breaker=self._cb,
            )
            if include_trade_plan
            else []
        )
        return {
            "portfolio_value": av,
            "net_deposit": nd,
            "gas_available": 0.0,
            "gassed_up": True,
            "strategy_status": {
                "spec_hash": self.live_spec.get("spec_hash"),
                "strategy_name": self.live_spec.get("strategy_name"),
                "family": self.live_spec.get("family"),
                "source": ts_.get("source"),
                "bundle_as_of": ts_.get("bundle_as_of"),
                "latest_signal_timestamp": ts_.get("timestamp"),
                "dry_run": self._adr_dry(),
                "current_account_value": round(av, 6),
                "target_weights": _cw(ts_["target_weights"]),
                "current_positions": _cw(cp),
                "trade_plan": tp,
                "compiled_metadata": ts_.get("compiled_metadata"),
            },
        }

    async def _target_snap(self) -> dict[str, Any]:
        settings = load_settings()
        settings.ensure_runtime_directories()
        spec = self._req_spec()
        p = MarketDataProvider(
            settings,
            ParquetLake(settings.data_lake_dir),
        )
        try:
            c = await compile_spec(settings, p, spec)
            prices = c.prices.sort_index()
            if len(prices.index) > 1:
                prices = prices.iloc[:-1]
            if prices.empty:
                raise ValueError("No live price history available for deployd strategy")
            tgts = (
                c.target_positions.reindex(prices.index)
                .ffill()
                .fillna(0.0)
                .shift(1)
                .fillna(0.0)
            )
            if tgts.empty:
                raise ValueError("No target history available for deployd strategy")
            ts_ = prices.index[-1]
            return {
                "timestamp": ts_.isoformat(),
                "target_weights": {
                    str(s): float(w) for s, w in tgts.iloc[-1].to_dict().items()
                },
                "mid_prices": {
                    str(s): float(p_) for s, p_ in prices.iloc[-1].to_dict().items()
                },
                "source": c.metadata.get("source"),
                "bundle_as_of": c.metadata.get("bundle_as_of"),
                "compiled_metadata": c.metadata,
            }
        finally:
            await p.close()

    async def _user_state(self, address: str) -> dict[str, Any]:
        ad = self._req_adapter()
        ok, st = await ad.get_user_state(address)
        if not ok or not isinstance(st, dict):
            raise ValueError(f"Unable to fetch SoDEX state for {address}: {st}")
        return st

    async def _net_dep(self, address: str, *, fallback: float) -> float:
        la = (
            self.config.get("ledger_adapter") if isinstance(self.config, dict) else None
        )
        if la is None:
            return fallback
        ok, nd = await la.get_strategy_net_deposit(wallet_address=address)
        return _ff(nd, fallback) if ok else fallback

    def _acct_val(self, state: dict[str, Any]) -> float:
        return max(
            _ff(dget(state, "crossMarginSummary", "accountValue"), 0.0),
            _ff(dget(state, "marginSummary", "accountValue"), 0.0),
            0.0,
        )

    def _perp_pos(self, state: dict[str, Any]) -> dict[str, float]:
        rv: dict[str, float] = {}
        for wr in state.get("assetPositions", []) or []:
            p = wr.get("position") or {}
            c = str(p.get("coin") or "").strip().upper()
            if c:
                rv[c] = _ff(p.get("szi"), 0.0)
        return rv

    def _trade_plan(
        self,
        *,
        target_weights: dict[str, float],
        current_positions: dict[str, float],
        mids: dict[str, float],
        account_value: float,
        leverage: float,
        min_trade_usd: float,
        circuit_breaker: CircuitBreakerState | None = None,
    ) -> list[dict[str, Any]]:
        plan: list[dict[str, Any]] = []
        for sym in sorted(set(target_weights) | set(current_positions)):
            mid = _ff(mids.get(sym), 0.0)
            if mid <= 0:
                continue
            tn = _ff(target_weights.get(sym), 0.0) * account_value * leverage
            tq = tn / mid
            cq = _ff(current_positions.get(sym), 0.0)
            dq = tq - cq
            du = abs(dq) * mid
            if circuit_breaker is not None and account_value > 0.0:
                mpf = compute_position_size(
                    risk_budget=circuit_breaker.max_risk_per_trade_pct,
                    volatility=0.05,
                    max_size=circuit_breaker.max_position_pct,
                )
                mpv = account_value * mpf
                mq = mpv / mid
                tqa = cq + dq
                if abs(tqa) > mq:
                    if cq == 0.0:
                        dq = (1 if dq >= 0 else -1) * mq
                    else:
                        dq = (1 if dq >= 0 else -1) * max(0.0, mq - abs(cq))
                    du = abs(dq) * mid
            if du < min_trade_usd:
                continue
            plan.append(
                {
                    "symbol": sym,
                    "target_weight": round(_ff(target_weights.get(sym), 0.0), 6),
                    "target_qty": round(tq, 8),
                    "current_qty": round(cq, 8),
                    "delta_qty": round(dq, 8),
                    "delta_usd": round(du, 4),
                    "size": abs(dq),
                    "is_buy": dq > 0.0,
                    "reduce_only": False,
                },
            )
        return plan

    def _load_spec(self) -> dict[str, Any]:
        sp = self._spec_p()
        if not sp.exists():
            raise FileNotFoundError(f"Live spec not found: {sp}")
        return cast(dict[str, Any], json.loads(sp.read_text()))

    def _spec_p(self) -> Path:
        if isinstance(self.SPEC_PATH, Path):
            return self.SPEC_PATH
        if isinstance(self.SPEC_PATH, str):
            return Path(self.SPEC_PATH)
        p = (
            self.config.get("siglab_live_spec_path")
            if isinstance(self.config, dict)
            else None
        )
        if p:
            return Path(str(p))
        raise ValueError("Generated siglab strategy is missing SPEC_PATH")

    def _req_adapter(self) -> SoDEXExecutionAdapter:
        if self.sodex_adapter is None:
            raise ValueError("SoDEX adapter not initialized")
        return self.sodex_adapter

    def _req_spec(self) -> SignalSpec:
        if self.spec is None:
            raise ValueError("Spec not initialized")
        return self.spec

    _spec_path = _spec_p
    _account_value = _acct_val
    _perp_positions = _perp_pos
    _build_trade_plan = _trade_plan
    _extract_perp_positions = _perp_pos
