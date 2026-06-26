from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, cast
from collections.abc import Callable, Sequence

from rich.text import Text

from siglab.utils import safe_float  # noqa: F401 — re-exported for tests


# _Queryable protocol removed — Screen.query_one has overloaded signatures
# that resist clean protocol matching. safe_query catches all exceptions anyway.



ACCENT_GREEN = "#4ade80"
WARNING_YELLOW = "#f0b456"
ERROR_RED = "#f87171"
INFO_BLUE = "#60a5fa"
TEXT_PRIMARY = "#e2ebe5"
TEXT_SECONDARY = "#a3b5a8"
TEXT_MUTED = "#7d9483"
SURFACE = "#0d1210"
BORDER_DIM = "#2a3a30"
BG = "#0a0a0a"


def friendly_error(exc: Exception) -> str:
    import httpx

    if isinstance(exc, httpx.ConnectError):
        return "Cannot connect to API server"
    if isinstance(exc, httpx.TimeoutException):
        return "Request timed out"
    if isinstance(exc, httpx.HTTPStatusError):
        c = exc.response.status_code
        if c == 401:
            return "Authentication failed"
        if c == 404:
            return "Endpoint not found"
        if c == 429:
            return "Rate limited — try again later"
        return f"Server error ({c})" if c >= 500 else f"HTTP error ({c})"
    if isinstance(exc, httpx.HTTPError):
        return "Network error"
    if isinstance(exc, json.JSONDecodeError):
        return "Invalid response format"
    if isinstance(exc, TimeoutError):
        return "Operation timed out"
    if isinstance(exc, ConnectionError):
        return "Connection lost"
    return "Unexpected error"


def format_price(price: float, symbol: str = "") -> str:
    return (
        f"{price:,.2f}"
        if price >= 1000
        else f"{price:,.4f}"
        if price >= 1
        else f"{price:,.6f}"
    )


def format_change(pct: float) -> Text:
    return (
        Text(f"▲+{pct:.2f}%", style=ACCENT_GREEN)
        if pct > 0
        else Text(f"▼{pct:.2f}%", style=ERROR_RED)
        if pct < 0
        else Text(f"── {pct:.2f}%", style=TEXT_MUTED)
    )


def format_volume(vol: float) -> str:
    return (
        f"{vol / 1000000000:.1f}B"
        if vol >= 1000000000
        else f"{vol / 1000000:.1f}M"
        if vol >= 1000000
        else f"{vol / 1000:.1f}K"
        if vol >= 1000
        else f"{vol:.0f}"
    )


def format_pnl(pnl: float) -> Text:
    return (
        Text(f"+{pnl:,.2f}", style=ACCENT_GREEN)
        if pnl > 0
        else Text(f"{pnl:,.2f}", style=ERROR_RED)
        if pnl < 0
        else Text(f"{pnl:,.2f}", style=TEXT_MUTED)
    )


def format_score(score: float | None) -> Text:
    if score is None or score != score:
        return Text("─" if score is None else "NaN", style=TEXT_MUTED)
    return Text(
        f"{score:.3f}",
        style=ACCENT_GREEN
        if score >= 0.7
        else WARNING_YELLOW
        if score >= 0.4
        else ERROR_RED,
    )


def format_return(ret: float | None) -> Text:
    if ret is None or ret != ret:
        return Text("─" if ret is None else "NaN", style=TEXT_MUTED)
    return (
        Text(f"+{ret:.2f}%", style=ACCENT_GREEN)
        if ret > 0
        else Text(f"{ret:.2f}%", style=ERROR_RED)
        if ret < 0
        else Text(f"{ret:.2f}%", style=TEXT_MUTED)
    )


def truncate(text: str, width: int) -> str:
    return (
        ""
        if width <= 0
        else text
        if len(text) <= width
        else "…"
        if width == 1
        else text[: width - 1] + "…"
    )


def format_date(date_str: str | None) -> str:
    if not date_str:
        return "──"
    try:
        from datetime import datetime

        return datetime.fromisoformat(date_str.replace("Z", "+00:00")).strftime(
            "%m-%d %H:%M",
        )
    except (ValueError, TypeError):
        return date_str[:10] if len(date_str) >= 10 else date_str


def format_count(value: float | None) -> str:
    if value is None:
        return "─"
    v = float(value)
    return (
        f"{v / 1000000:.1f}M"
        if v >= 1000000
        else f"{v / 1000:.1f}k"
        if v >= 1000
        else f"{v:.0f}"
    )


def severity_color(severity: str) -> str:
    return {"critical": ERROR_RED, "warning": WARNING_YELLOW, "info": INFO_BLUE}.get(
        severity.lower().strip(), TEXT_MUTED,
    )


def format_confidence(conf: float | None) -> Text:
    if conf is None:
        return Text("─", style=TEXT_MUTED)
    lb = f"{conf:.0%}"
    return Text(
        lb,
        style=ACCENT_GREEN
        if conf >= 0.8
        else WARNING_YELLOW
        if conf >= 0.5
        else ERROR_RED,
    )


def status_style(passed: bool | None, deployed: bool = False) -> tuple[str, str]:
    return (
        ("▲", INFO_BLUE)
        if deployed
        else ("·", TEXT_MUTED)
        if passed is None
        else ("●", ACCENT_GREEN)
        if passed
        else ("○", ERROR_RED)
    )


def side_style(side: str) -> str:
    return ACCENT_GREEN if side.upper() == "BUY" else ERROR_RED


def order_status_style(status: str) -> str:
    return {
        "FILLED": ACCENT_GREEN,
        "OPEN": INFO_BLUE,
        "CANCELLED": WARNING_YELLOW,
        "EXPIRED": TEXT_MUTED,
    }.get(status.upper().strip(), TEXT_SECONDARY)


def gauge_color(score: float) -> str:
    return (
        TEXT_MUTED
        if score != score
        else ERROR_RED
        if score < 0.4
        else WARNING_YELLOW
        if score < 0.7
        else ACCENT_GREEN
    )

def safe_query(
    screen: Any,
    widget_id: str,
    widget_type: type[Any],
    fn: Callable[[Any], Any] | None = None,
) -> Any:
    try:
        w = screen.query_one(widget_id, widget_type)
        return fn(w) if fn else w
    except Exception:
        return None


def sanitize_status_text(text: str, max_len: int = 120) -> str:
    import re

    return truncate(
        re.sub(
            " {2,}",
            " ",
            re.sub(
                "[\\x00-\\x08\\x0b\\x0c\\x0e-\\x1f\\x7f]",
                "",
                re.sub("\\x1b\\[[0-9;]*[a-zA-Z]", "", text),
            )
            .replace("\n", " ")
            .replace("\r", ""),
        ).strip(),
        max_len,
    )


def bar_gauge(
    value: float, width: int = 10, *, filled_char: str = "█", empty_char: str = "░",
) -> str:
    return filled_char * max(0, min(width, int(value * width))) + empty_char * (
        width - max(0, min(width, int(value * width)))
    )


def compact_qty(qty: float) -> str:
    return (
        f"{qty / 1000000:.2f}M"
        if abs(qty) >= 1000000
        else f"{qty / 1000:.2f}K"
        if abs(qty) >= 1000
        else f"{qty:,.4f}"
    )


PANEL_CSS = f"padding: 0 1; background: {SURFACE};"
SCROLLABLE_CSS = f"overflow-y: auto; {PANEL_CSS}"
COMPACT_CSS = f"height: auto; min-height: 5; {PANEL_CSS}"
EXPANDABLE_CSS = f"height: 1fr; min-height: 6; {SCROLLABLE_CSS}"


@dataclass(frozen=True, slots=True)
class TickerView:
    _raw: dict[str, Any]

    @classmethod
    def from_dict(cls: type[TickerView], d: dict[str, Any]) -> TickerView:
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
            self._raw.get("priceChangePercent", self._raw.get("price_change_pct", 0))
            or 0,
        )

    @property
    def volume(self) -> float:
        return float(self._raw.get("volume", self._raw.get("volume_24h", 0)) or 0)

    @property
    def raw(self) -> dict[str, Any]:
        return self._raw


@dataclass(frozen=True, slots=True)
class SymbolEntry:
    name: str
    symbol: str
    price: float
    change_pct: float
    volume: float

    @classmethod
    def from_ticker(cls: type[SymbolEntry], tv: TickerView) -> SymbolEntry:
        return cls(
            name=tv.symbol,
            symbol=tv.symbol,
            price=tv.last_price,
            change_pct=tv.price_change_pct,
            volume=tv.volume,
        )


@dataclass(frozen=True, slots=True)
class KlineView:
    _raw: dict[str, Any]

    @classmethod
    def from_dict(cls: type[KlineView], d: dict[str, Any]) -> KlineView:
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
    bids: tuple[list[Any], ...]
    asks: tuple[list[Any], ...]
    symbol: str

    @classmethod
    def from_dict(
        cls: type[OrderBookView], data: dict[str, Any], symbol: str,
    ) -> OrderBookView:
        return cls(
            bids=tuple(data.get("bids", [])),
            asks=tuple(data.get("asks", [])),
            symbol=symbol,
        )


@dataclass(frozen=True, slots=True)
class PositionView:
    _raw: dict[str, Any]

    @classmethod
    def from_dict(cls: type[PositionView], d: dict[str, Any]) -> PositionView:
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
    _raw: dict[str, Any]

    @classmethod
    def from_dict(cls: type[OrderView], d: dict[str, Any]) -> OrderView:
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
    realized: float
    unrealized: float
    total: float
    funding: float
    open_count: int

    @classmethod
    def from_dict(cls: type[PnlSnapshot], d: dict[str, Any]) -> PnlSnapshot:
        return cls(
            realized=float(d.get("realized_pnl", 0)),
            unrealized=float(d.get("unrealized_pnl", 0)),
            total=float(d.get("total_pnl", 0)),
            funding=float(d.get("total_funding_cost", 0)),
            open_count=int(d.get("open_position_count", 0)),
        )


@dataclass(frozen=True, slots=True)
class RiskSnapshot:
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
    def from_dict(cls: type[RiskSnapshot], data: dict[str, Any]) -> RiskSnapshot:
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


@dataclass(frozen=True, slots=True)
class GraphNode:
    _raw: dict[str, Any]

    @classmethod
    def from_dict(cls: type[GraphNode], d: dict[str, Any]) -> GraphNode:
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
    _raw: dict[str, Any]

    @classmethod
    def from_dict(cls: type[GraphEdge], d: dict[str, Any]) -> GraphEdge:
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


@dataclass(frozen=True, slots=True)
class StrategyEntry:
    _raw: dict[str, Any]

    @classmethod
    def from_dict(cls: type[StrategyEntry], d: dict[str, Any]) -> StrategyEntry:
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
        return cast(list[float], self._raw.get("equity_curve", []))

    @property
    def created_at(self) -> str:
        return str(self._raw.get("created_at", ""))

    @property
    def raw(self) -> dict[str, Any]:
        return self._raw


def closes_from_klines(klines: Sequence[dict[str, Any]]) -> tuple[float, ...]:
    return tuple(float(k.get("close", 0)) for k in klines)
