"""Zero-copy typed views for TUI data flow.

Provides frozen dataclass wrappers that reference API response dicts
without copying data. Widgets consume these views instead of creating
intermediate dict/list copies on every refresh cycle.

Design principles:
- ``__slots__`` on every class to minimise per-instance memory
- Frozen dataclasses prevent accidental mutation
- ``from_dict`` classmethods wrap raw dicts without copying values
- Views are cheap to create and safe to share between widgets
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence


# ── Market Data Views ────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class TickerView:
    """Read-only view of a 24-hour ticker entry from the API.

    Wraps a raw dict without copying; field access is via ``__getattr__``
    delegation so callers no longer need ``dict.get()`` chains.
    """

    _raw: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TickerView:
        return cls(_raw=d)

    @property
    def symbol(self) -> str:
        return str(self._raw.get("symbol", "?"))

    @property
    def last_price(self) -> float:
        return float(self._raw.get("lastPrice", self._raw.get("last_price", 0)) or 0)

    @property
    def price_change_pct(self) -> float:
        return float(
            self._raw.get("priceChangePercent", self._raw.get("price_change_pct", 0)) or 0
        )

    @property
    def volume(self) -> float:
        return float(self._raw.get("volume", self._raw.get("volume_24h", 0)) or 0)

    @property
    def raw(self) -> dict[str, Any]:
        """Access the underlying dict for legacy code paths."""
        return self._raw


@dataclass(frozen=True, slots=True)
class SymbolEntry:
    """Derived symbol entry for the symbol list widget.

    Created once per ticker refresh and shared with the symbol list,
    avoiding the repeated dict comprehension that was building new
    dicts on every cycle.
    """

    name: str
    symbol: str
    price: float
    change_pct: float
    volume: float

    @classmethod
    def from_ticker(cls, tv: TickerView) -> SymbolEntry:
        return cls(
            name=tv.symbol,
            symbol=tv.symbol,
            price=tv.last_price,
            change_pct=tv.price_change_pct,
            volume=tv.volume,
        )


@dataclass(frozen=True, slots=True)
class KlineView:
    """Read-only view of a single kline/candle entry."""

    _raw: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> KlineView:
        return cls(_raw=d)

    @property
    def open(self) -> float:
        return float(self._raw.get("open", 0))

    @property
    def high(self) -> float:
        return float(self._raw.get("high", 0))

    @property
    def low(self) -> float:
        return float(self._raw.get("low", 0))

    @property
    def close(self) -> float:
        return float(self._raw.get("close", 0))

    @property
    def volume(self) -> float:
        return float(self._raw.get("volume", 0))

    @property
    def raw(self) -> dict[str, Any]:
        return self._raw


@dataclass(frozen=True, slots=True)
class OrderBookView:
    """Read-only view of order book depth data."""

    bids: tuple[list, ...]
    asks: tuple[list, ...]
    symbol: str

    @classmethod
    def from_dict(cls, data: dict[str, Any], symbol: str) -> OrderBookView:
        return cls(
            bids=tuple(data.get("bids", [])),
            asks=tuple(data.get("asks", [])),
            symbol=symbol,
        )


# ── Paper Trading Views ──────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PositionView:
    """Read-only view of a paper trading position."""

    _raw: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PositionView:
        return cls(_raw=d)

    @property
    def symbol(self) -> str:
        return str(self._raw.get("symbol", "?"))

    @property
    def quantity(self) -> float:
        return float(self._raw.get("quantity", 0))

    @property
    def entry_price(self) -> float:
        return float(self._raw.get("entry_price", 0))

    @property
    def unrealized_pnl(self) -> float:
        return float(self._raw.get("unrealized_pnl", 0))

    @property
    def raw(self) -> dict[str, Any]:
        return self._raw


@dataclass(frozen=True, slots=True)
class OrderView:
    """Read-only view of a paper trading order."""

    _raw: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> OrderView:
        return cls(_raw=d)

    @property
    def order_id(self) -> str:
        return str(self._raw.get("order_id", "?"))

    @property
    def symbol(self) -> str:
        return str(self._raw.get("symbol", "?"))

    @property
    def side(self) -> str:
        return str(self._raw.get("side", "?"))

    @property
    def order_type(self) -> str:
        return str(self._raw.get("order_type", "?"))

    @property
    def quantity(self) -> float:
        return float(self._raw.get("quantity", 0))

    @property
    def price(self) -> float:
        return float(self._raw.get("price", 0))

    @property
    def fill_price(self) -> float | None:
        fp = self._raw.get("fill_price")
        return float(fp) if fp is not None else None

    @property
    def status(self) -> str:
        return str(self._raw.get("status", "?"))

    @property
    def created_at(self) -> float:
        return float(self._raw.get("created_at", 0))

    @property
    def raw(self) -> dict[str, Any]:
        return self._raw


@dataclass(frozen=True, slots=True)
class PnlSnapshot:
    """Immutable snapshot of PnL state at a point in time."""

    realized: float
    unrealized: float
    total: float
    funding: float
    open_count: int

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PnlSnapshot:
        return cls(
            realized=float(d.get("realized_pnl", 0)),
            unrealized=float(d.get("unrealized_pnl", 0)),
            total=float(d.get("total_pnl", 0)),
            funding=float(d.get("total_funding_cost", 0)),
            open_count=int(d.get("open_position_count", 0)),
        )


# ── Risk Data Views ─────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RiskSnapshot:
    """Immutable snapshot of risk data from the /risk endpoint."""

    composite_score: float | None
    sub_scores: dict[str, float]
    strategy_count: int
    max_drawdown: float | None
    current_drawdown: float | None
    recovery_periods: int | None
    drawdown_history: tuple[float, ...]
    correlation_matrix: list[list[float]] | None
    strategy_names: tuple[str, ...]
    alerts: tuple[dict[str, Any], ...]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RiskSnapshot:
        return cls(
            composite_score=data.get("composite_score"),
            sub_scores=dict(data.get("sub_scores", {})),
            strategy_count=int(data.get("strategy_count", 0)),
            max_drawdown=data.get("max_drawdown"),
            current_drawdown=data.get("current_drawdown"),
            recovery_periods=data.get("recovery_periods"),
            drawdown_history=tuple(data.get("drawdown_history", [])),
            correlation_matrix=data.get("correlation_matrix"),
            strategy_names=tuple(data.get("strategy_names", [])),
            alerts=tuple(data.get("alerts", [])),
        )


# ── Evidence Graph Views ────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class GraphNode:
    """Read-only view of an evidence graph node."""

    _raw: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> GraphNode:
        return cls(_raw=d)

    @property
    def id(self) -> str:
        return str(self._raw.get("id", ""))

    @property
    def label(self) -> str:
        return str(self._raw.get("label", "?"))

    @property
    def kind(self) -> str:
        return str(self._raw.get("kind", "unknown"))

    @property
    def count(self) -> int:
        return int(self._raw.get("count", 0))

    @property
    def raw(self) -> dict[str, Any]:
        return self._raw


@dataclass(frozen=True, slots=True)
class GraphEdge:
    """Read-only view of an evidence graph edge."""

    _raw: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> GraphEdge:
        return cls(_raw=d)

    @property
    def source(self) -> str:
        return str(self._raw.get("source", ""))

    @property
    def target(self) -> str:
        return str(self._raw.get("target", ""))

    @property
    def label(self) -> str:
        return str(self._raw.get("label", "linked"))

    @property
    def confidence(self) -> float | None:
        c = self._raw.get("confidence")
        return float(c) if c is not None else None

    @property
    def warning(self) -> str | None:
        w = self._raw.get("warning")
        return str(w) if w else None

    @property
    def raw(self) -> dict[str, Any]:
        return self._raw


# ── Strategy Views ──────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class StrategyEntry:
    """Read-only view of a strategy/experiment entry."""

    _raw: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> StrategyEntry:
        return cls(_raw=d)

    @property
    def spec_hash(self) -> str:
        return str(self._raw.get("spec_hash", "?"))

    @property
    def family(self) -> str:
        return str(self._raw.get("family", ""))

    @property
    def track(self) -> str:
        return str(self._raw.get("track", ""))

    @property
    def hypothesis(self) -> str:
        return str(self._raw.get("hypothesis", ""))

    @property
    def passed(self) -> bool | None:
        return self._raw.get("passed")

    @property
    def aggregate_score(self) -> float | None:
        v = self._raw.get("aggregate_score")
        return float(v) if v is not None else None

    @property
    def validation_total_return(self) -> float | None:
        v = self._raw.get("validation_total_return")
        return float(v) if v is not None else None

    @property
    def sharpe(self) -> float | None:
        v = self._raw.get("sharpe")
        return float(v) if v is not None else None

    @property
    def max_drawdown(self) -> float | None:
        v = self._raw.get("max_drawdown")
        return float(v) if v is not None else None

    @property
    def equity_curve(self) -> list[float]:
        return self._raw.get("equity_curve", [])

    @property
    def created_at(self) -> str:
        return str(self._raw.get("created_at", ""))

    @property
    def raw(self) -> dict[str, Any]:
        return self._raw


# ── Shared Sequence Utilities ────────────────────────────────────────


def closes_from_klines(klines: Sequence[dict[str, Any]]) -> tuple[float, ...]:
    """Extract close prices as a tuple (zero-copy for tuple of floats).

    Returns a tuple instead of a list to signal immutability and
    avoid downstream ``list()`` copies.
    """
    return tuple(float(k.get("close", 0)) for k in klines)
