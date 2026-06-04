"""Market Overview TUI screen for SigLab.

Displays:
- Symbol list with search/filter
- Klines chart (ASCII sparkline)
- Real-time ticker table (24h change, volume, mark price)
- Order book depth (bids/asks with levels)

Connects to the FastAPI dashboard via TuiApiClient.
Auto-refreshes every 30 seconds.

Zero-copy data flow: ticker data fetched once is shared between
SymbolListWidget and TickerTableWidget as references.  Kline close
prices are extracted as tuples (immutable sequence) so sparkline
can render without copying.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar, Sequence

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import Input, Static

from siglab.tui.api_client import TuiApiClient
from siglab.tui.formatting import (
    ACCENT_GREEN,
    BORDER_DIM,
    ERROR_RED,
    SCROLLABLE_CSS,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    format_change,
    format_price,
    format_volume,
    safe_float,
    safe_query,
)
from siglab.tui.loading import LoadingIndicator
from siglab.tui.screens.base import BaseScreen
from siglab.tui.types import SymbolEntry, TickerView, closes_from_klines
from siglab.tui.widgets.base import FilterableListWidget
from siglab.tui.widgets.sparkline import ohlc_summary, sparkline_text

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────

DEFAULT_SYMBOL = "BTC-USD"
DEFAULT_INTERVAL = "1h"
KLINES_LIMIT = 60
ORDERBOOK_LIMIT = 15


# ── Symbol List Widget ───────────────────────────────────────────────


class SymbolListWidget(FilterableListWidget):
    """Vertical list of perp symbols with selection highlighting."""

    __slots__ = ()

    symbols: reactive[list[SymbolEntry]] = reactive(list, layout=True)
    _items_reactive: ClassVar[str] = "symbols"

    DEFAULT_CSS = f"""
    SymbolListWidget {{
        width: 28;
        min-width: 22;
        height: 1fr;
        {SCROLLABLE_CSS}
    }}
    """

    @staticmethod
    def _to_symbol_entry(item: Any) -> SymbolEntry:
        """Convert a dict or SymbolEntry to a SymbolEntry."""
        if isinstance(item, SymbolEntry):
            return item
        if isinstance(item, dict):
            return SymbolEntry(
                name=str(item.get("name", item.get("symbol", "?"))),
                symbol=str(item.get("symbol", "?")),
                price=float(item.get("price", 0) or 0),
                change_pct=float(item.get("change_pct", 0) or 0),
                volume=float(item.get("volume", 0) or 0),
            )
        return item  # type: ignore[return-value]

    def set_symbols(self, entries: Sequence[Any]) -> None:
        """Update the full symbol list."""
        self.set_data([self._to_symbol_entry(e) for e in entries])

    def _matches(self, item: Any) -> bool:
        ft = self._filter_text.upper().strip()
        if not ft:
            return True
        return ft in item.name.upper() or ft in item.symbol.upper()

    def _render_item(self, item: Any, index: int, is_selected: bool) -> Text:
        display = f"  {item.name:<18}"
        if is_selected:
            return Text(display, style=f"bold #000000 on {ACCENT_GREEN}")
        return Text(display, style=TEXT_SECONDARY)

    def get_selected_symbol(self) -> str | None:
        """Return the symbol string of the currently selected item."""
        items = self.symbols
        if items and 0 <= self.selected_index < len(items):
            return items[self.selected_index].name
        return None


# ── Klines Chart Widget ──────────────────────────────────────────────


class KlinesChartWidget(Static):
    """Renders an ASCII sparkline chart of kline data with OHLC summary.

    Zero-copy: stores a reference to the klines list from the API
    response.  Close prices are extracted as a tuple (immutable
    sequence) so ``sparkline_text`` can render without copying.
    """

    __slots__ = ("_closes_cache",)

    candles: reactive[list[dict]] = reactive(list, layout=True)
    symbol: reactive[str] = reactive(DEFAULT_SYMBOL)

    DEFAULT_CSS = """
    KlinesChartWidget {
        height: 1fr;
        min-height: 6;
        padding: 0 1;
        background: #0a0a0a;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._closes_cache: tuple[float, ...] = ()

    def set_candles(self, klines: list[dict]) -> None:
        """Store a reference to klines and pre-compute close tuple.

        The klines list itself is stored by reference (no copy).
        Close prices are extracted once into an immutable tuple that
        ``sparkline_text`` can iterate without further allocation.
        """
        self.candles = klines
        self._closes_cache = closes_from_klines(klines)

    def render(self) -> Text:
        result = Text()

        # Header
        header = f" {self.symbol} "
        result.append(header, style=f"bold {ACCENT_GREEN}")
        if self.candles:
            last = self.candles[-1]
            price = safe_float(last.get("close", 0))
            result.append(f"  {format_price(price)}  ", style=f"bold {TEXT_PRIMARY}")
        result.append("\n\n")

        if not self.candles:
            result.append("  Loading chart data\u2026", style=TEXT_MUTED)
            return result

        # Use pre-computed close prices if available, else extract now
        closes = self._closes_cache if self._closes_cache else closes_from_klines(self.candles)
        chart_width = max(20, min(80, len(closes)))
        spark = sparkline_text(closes, width=chart_width)
        result.append("  ")
        result.append_text(spark)
        result.append("\n\n")

        # OHLC summary
        result.append("  ")
        result.append(ohlc_summary(self.candles), style=TEXT_MUTED)
        result.append("\n")

        return result


# ── Ticker Table Widget ──────────────────────────────────────────────


class TickerTableWidget(Static):
    """Displays 24h ticker data for all perp symbols in a table.

    Zero-copy: stores a reference to the tickers list from the API
    response — no data is copied into widget state.
    """

    tickers: reactive[list[dict[str, Any]]] = reactive(list, layout=True)

    DEFAULT_CSS = f"""
    TickerTableWidget {{
        height: auto;
        max-height: 12;
        {SCROLLABLE_CSS}
    }}
    """

    def render(self) -> Text:
        if not self.tickers:
            return Text("  No data available", style=TEXT_MUTED)

        lines = Text()

        # Header
        lines.append("  SYMBOL          PRICE          24h CHG      VOLUME\n", style=TEXT_MUTED)
        lines.append("  " + "─" * 60 + "\n", style=BORDER_DIM)

        for t in self.tickers[:20]:  # Show top 20
            sym = str(t.get("symbol", "?"))
            price = safe_float(t.get("lastPrice", t.get("last_price", 0)))
            change_pct = safe_float(t.get("priceChangePercent", t.get("price_change_pct", 0)))
            volume = safe_float(t.get("volume", t.get("volume_24h", 0)))

            lines.append(f"  {sym:<15}", style=TEXT_SECONDARY)
            lines.append(f"{format_price(price, sym):>14}  ", style=TEXT_PRIMARY)
            lines.append_text(format_change(change_pct))
            lines.append(f"   {format_volume(volume):>10}", style="info_blue")
            lines.append("\n")

        return lines


# ── Order Book Widget ────────────────────────────────────────────────


class OrderBookWidget(Static):
    """Renders order book depth with bids and asks side by side.

    Zero-copy: bids and asks are stored as tuples (immutable sequences)
    shared from the API response — no per-refresh list copies.
    """

    bids: reactive[tuple[list, ...]] = reactive(tuple, layout=True)
    asks: reactive[tuple[list, ...]] = reactive(tuple, layout=True)
    symbol: reactive[str] = reactive(DEFAULT_SYMBOL)

    DEFAULT_CSS = """
    OrderBookWidget {
        height: 1fr;
        min-height: 8;
        padding: 0 1;
        background: #0a0a0a;
    }
    """

    def render(self) -> Text:
        result = Text()
        result.append(f" ORDER BOOK — {self.symbol}\n", style=f"bold {TEXT_PRIMARY}")

        if not self.bids and not self.asks:
            result.append("  No data available\n", style=TEXT_MUTED)
            return result

        # Header
        result.append("  BIDS (buys)           │  ASKS (sells)\n", style=TEXT_MUTED)
        result.append("  " + "─" * 23 + "┼" + "─" * 23 + "\n", style=BORDER_DIM)

        # Determine max size for bar scaling
        all_sizes = []
        for b in self.bids[:ORDERBOOK_LIMIT]:
            try:
                all_sizes.append(float(b[1]) if len(b) > 1 else 0)
            except (ValueError, IndexError):
                pass
        for a in self.asks[:ORDERBOOK_LIMIT]:
            try:
                all_sizes.append(float(a[1]) if len(a) > 1 else 0)
            except (ValueError, IndexError):
                pass
        max_size = max(all_sizes) if all_sizes else 1.0

        bar_width = 10
        rows = max(len(self.bids), len(self.asks))
        for i in range(min(rows, ORDERBOOK_LIMIT)):
            # Bid side
            if i < len(self.bids):
                b = self.bids[i]
                try:
                    b_price = float(b[0])
                    b_size = float(b[1]) if len(b) > 1 else 0
                except (ValueError, IndexError):
                    b_price, b_size = 0, 0
                bar_len = int(b_size / max_size * bar_width) if max_size > 0 else 0
                bar = "█" * bar_len + " " * (bar_width - bar_len)
                result.append(f"  {bar}", style=ACCENT_GREEN)
                result.append(f" {b_price:>10,.2f} ", style=TEXT_PRIMARY)
                result.append(f"{b_size:>8.2f}", style=TEXT_SECONDARY)
            else:
                result.append(" " * 36)

            result.append(" │ ", style=BORDER_DIM)

            # Ask side
            if i < len(self.asks):
                a = self.asks[i]
                try:
                    a_price = float(a[0])
                    a_size = float(a[1]) if len(a) > 1 else 0
                except (ValueError, IndexError):
                    a_price, a_size = 0, 0
                result.append(f"{a_size:>8.2f}", style=TEXT_SECONDARY)
                result.append(f" {a_price:>10,.2f} ", style=TEXT_PRIMARY)
                bar_len = int(a_size / max_size * bar_width) if max_size > 0 else 0
                bar = "█" * bar_len + " " * (bar_width - bar_len)
                result.append(f"{bar}", style=ERROR_RED)

            result.append("\n")

        # Spread
        if self.bids and self.asks:
            try:
                best_bid = float(self.bids[0][0])
                best_ask = float(self.asks[0][0])
                spread = best_ask - best_bid
                spread_pct = (spread / best_ask * 100) if best_ask else 0
                result.append(f"\n  Spread: {spread:,.2f} ({spread_pct:.3f}%)\n", style=TEXT_MUTED)
            except (ValueError, IndexError):
                pass

        return result


# ── Market Overview Screen ───────────────────────────────────────────


class MarketScreen(BaseScreen):
    """Market overview screen showing perp market data.

    Layout:
    - Top: Search bar for symbol filtering
    - Left: Symbol list with selection
    - Right top: Klines chart (ASCII sparkline)
    - Right middle: Ticker table
    - Right bottom: Order book depth
    """

    BINDINGS: ClassVar[list[Binding]] = BaseScreen.BINDINGS + [
        Binding("enter", "select_symbol", "Select", show=False),
    ]

    current_symbol: reactive[str] = reactive(DEFAULT_SYMBOL)

    _loading_widget_id: ClassVar[str] = "#market-loading"
    _status_widget_id: ClassVar[str] = "#market-status"
    _refresh_interval: ClassVar[float] = 30.0
    _api_client_class: ClassVar[type] = TuiApiClient
    _search_input_id: ClassVar[str] = "symbol-search"
    _search_list_id: ClassVar[str] = "symbol-list"

    def compose(self) -> ComposeResult:
        with Vertical(id="market-layout"):
            yield Input(placeholder="Search symbols\u2026", id="symbol-search")
            with Horizontal(id="market-main"):
                yield SymbolListWidget(id="symbol-list")
                with Vertical(id="market-detail"):
                    yield KlinesChartWidget(id="klines-chart")
                    yield TickerTableWidget(id="ticker-table")
                    yield OrderBookWidget(id="order-book")
            yield LoadingIndicator(id="market-loading")
            yield Static(self.status_text, id="market-status")

    # ── Data Fetching ────────────────────────────────────────────────

    async def _fetch_data(self) -> None:
        """Fetch all market data and update widgets."""
        successes = await self._fetch_multiple(
            self._fetch_tickers(),
            self._fetch_klines(),
            self._fetch_orderbook(),
            label="market",
        )

        if successes == 3:
            self._update_status_text(
                f"Live \u00b7 {self.current_symbol} \u00b7 refreshed  [r]efresh  [/]search  [j/k]nav  [?]help"
            )
        elif successes > 0:
            self._update_status_text(
                f"Partial update ({successes}/3) \u00b7 {self.current_symbol}  [r]etry"
            )
        else:
            self._update_status_error("Cannot reach API server")

    async def _fetch_tickers(self) -> None:
        """Fetch ticker data and update symbol list + ticker table."""
        data = await self._api.get_market_tickers()
        tickers = data.get("tickers", [])
        if tickers:
            entries = sorted(
                (SymbolEntry.from_ticker(TickerView.from_dict(t)) for t in tickers),
                key=lambda e: abs(e.price * e.change_pct),
                reverse=True,
            )
            safe_query(self, "#symbol-list", SymbolListWidget,
                       lambda w: w.set_symbols(entries))
            safe_query(self, "#ticker-table", TickerTableWidget,
                       lambda w: setattr(w, "tickers", tickers))

    async def _fetch_klines(self) -> None:
        """Fetch kline data for the current symbol."""
        data = await self._api.get_market_klines(
            self.current_symbol, DEFAULT_INTERVAL, KLINES_LIMIT
        )
        klines = data.get("klines", [])
        def _update_chart(w):
            w.symbol = self.current_symbol
            w.set_candles(klines)
        safe_query(self, "#klines-chart", KlinesChartWidget, _update_chart)

    async def _fetch_orderbook(self) -> None:
        """Fetch order book for the current symbol."""
        data = await self._api.get_market_orderbook(
            self.current_symbol, ORDERBOOK_LIMIT
        )
        def _update_book(w):
            w.symbol = self.current_symbol
            w.bids = tuple(data.get("bids", []))
            w.asks = tuple(data.get("asks", []))
        safe_query(self, "#order-book", OrderBookWidget, _update_book)

    # ── Event Handlers ───────────────────────────────────────────────

    def on_input_changed(self, event: Input.Changed) -> None:
        if not self._on_search_input_changed(event):
            pass  # unhandled input

    def on_input_submitted(self, event: Input.Submitted) -> None:
        lw = safe_query(self, "#symbol-list", SymbolListWidget)
        if lw:
            selected = lw.get_selected_symbol()
            if selected:
                self._select_symbol(selected)

    # ── Actions ──────────────────────────────────────────────────────

    def action_select_symbol(self) -> None:
        lw = safe_query(self, "#symbol-list", SymbolListWidget)
        if lw:
            selected = lw.get_selected_symbol()
            if selected:
                self._select_symbol(selected)

    def _select_symbol(self, symbol: str) -> None:
        if symbol != self.current_symbol:
            self.current_symbol = symbol
            self.call_after_refresh(self._refresh_klines_and_book)

    async def _refresh_klines_and_book(self) -> None:
        self.is_loading = True
        try:
            await self._fetch_klines()
            await self._fetch_orderbook()
        finally:
            self.is_loading = False
