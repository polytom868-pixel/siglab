"""Market Overview TUI screen for SigLab.

Displays:
- Symbol list with search/filter
- Klines chart (ASCII sparkline)
- Real-time ticker table (24h change, volume, mark price)
- Order book depth (bids/asks with levels)

Connects to the FastAPI dashboard via TuiApiClient.
Auto-refreshes every 30 seconds.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Input, Static

from siglab.tui.api_client import TuiApiClient
from siglab.tui.formatting import friendly_error
from siglab.tui.loading import LoadingIndicator
from siglab.tui.widgets.sparkline import ohlc_summary, sparkline_text

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────

REFRESH_SECONDS = 30.0
DEFAULT_SYMBOL = "BTC-USD"
DEFAULT_INTERVAL = "1h"
KLINES_LIMIT = 60
ORDERBOOK_LIMIT = 15


# ── Helpers ──────────────────────────────────────────────────────────


def _format_price(price: float, symbol: str = "") -> str:
    """Format a price with appropriate decimal places."""
    if price >= 1000:
        return f"{price:,.2f}"
    elif price >= 1:
        return f"{price:,.4f}"
    else:
        return f"{price:,.6f}"


def _format_change(pct: float) -> Text:
    """Format a percentage change as a coloured Rich Text."""
    if pct > 0:
        return Text(f"▲+{pct:.2f}%", style="#4ade80")
    elif pct < 0:
        return Text(f"▼{pct:.2f}%", style="#f87171")
    else:
        return Text(f"── {pct:.2f}%", style="#7d9483")


def _format_volume(vol: float) -> str:
    """Format volume in compact form (K, M, B)."""
    if vol >= 1_000_000_000:
        return f"{vol / 1_000_000_000:.1f}B"
    elif vol >= 1_000_000:
        return f"{vol / 1_000_000:.1f}M"
    elif vol >= 1_000:
        return f"{vol / 1_000:.1f}K"
    else:
        return f"{vol:.0f}"


# ── Symbol List Widget ───────────────────────────────────────────────


class SymbolListWidget(Static):
    """Vertical list of perp symbols with selection highlighting."""

    symbols: reactive[list[dict[str, Any]]] = reactive(list, layout=True)
    selected_index: reactive[int] = reactive(0)

    DEFAULT_CSS = """
    SymbolListWidget {
        width: 28;
        min-width: 22;
        height: 1fr;
        overflow-y: auto;
        padding: 0 1;
        background: $surface;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._all_symbols: list[dict[str, Any]] = []
        self._filter_text: str = ""

    def set_symbols(self, symbols: list[dict[str, Any]]) -> None:
        """Update the full symbol list."""
        self._all_symbols = symbols
        self._apply_filter()

    def _apply_filter(self) -> None:
        """Filter symbols by the current search text."""
        ft = self._filter_text.upper().strip()
        if ft:
            self.symbols = [
                s for s in self._all_symbols
                if ft in str(s.get("name", "")).upper()
                or ft in str(s.get("symbol", "")).upper()
            ]
        else:
            self.symbols = list(self._all_symbols)
        # Clamp selected index
        if self.symbols and self.selected_index >= len(self.symbols):
            self.selected_index = max(0, len(self.symbols) - 1)

    def set_filter(self, text: str) -> None:
        """Update the filter text."""
        self._filter_text = text
        self._apply_filter()

    def render(self) -> Text:
        if not self.symbols:
            return Text("  No symbols", style="#7d9483")

        lines = Text()
        for i, sym in enumerate(self.symbols):
            name = str(sym.get("name", sym.get("symbol", "?")))
            # Truncate to fit width
            display = f"  {name:<18}"
            if i == self.selected_index:
                lines.append(display, style="bold #000000 on #4ade80")
            else:
                lines.append(display, style="#a3b5a8")
            lines.append("\n")
        return lines

    def action_move_up(self) -> None:
        if self.selected_index > 0:
            self.selected_index -= 1

    def action_move_down(self) -> None:
        if self.selected_index < len(self.symbols) - 1:
            self.selected_index += 1

    def get_selected_symbol(self) -> str | None:
        """Return the symbol string of the currently selected item."""
        if self.symbols and 0 <= self.selected_index < len(self.symbols):
            return self.symbols[self.selected_index].get("name") or self.symbols[self.selected_index].get("symbol")
        return None


# ── Klines Chart Widget ──────────────────────────────────────────────


class KlinesChartWidget(Static):
    """Renders an ASCII sparkline chart of kline data with OHLC summary."""

    candles: reactive[list[dict]] = reactive(list, layout=True)
    symbol: reactive[str] = reactive(DEFAULT_SYMBOL)

    DEFAULT_CSS = """
    KlinesChartWidget {
        height: 1fr;
        min-height: 6;
        padding: 0 1;
        background: $bg;
    }
    """

    def render(self) -> Text:
        result = Text()

        # Header
        header = f" {self.symbol} "
        result.append(header, style="bold #4ade80")
        if self.candles:
            last = self.candles[-1]
            price = last.get("close", 0)
            result.append(f"  {_format_price(price)}  ", style="bold #e2ebe5")
        result.append("\n\n")

        if not self.candles:
            result.append("  Loading chart data…", style="#7d9483")
            return result

        # Sparkline from close prices
        closes = [c.get("close", 0) for c in self.candles]
        chart_width = max(20, min(80, len(closes)))
        spark = sparkline_text(closes, width=chart_width)
        result.append("  ")
        result.append_text(spark)
        result.append("\n\n")

        # OHLC summary
        result.append("  ")
        result.append(ohlc_summary(self.candles), style="#7d9483")
        result.append("\n")

        return result


# ── Ticker Table Widget ──────────────────────────────────────────────


class TickerTableWidget(Static):
    """Displays 24h ticker data for all perp symbols in a table."""

    tickers: reactive[list[dict[str, Any]]] = reactive(list, layout=True)

    DEFAULT_CSS = """
    TickerTableWidget {
        height: auto;
        max-height: 12;
        padding: 0 1;
        overflow-y: auto;
        background: $surface;
    }
    """

    def render(self) -> Text:
        if not self.tickers:
            return Text("  Loading tickers…", style="#7d9483")

        lines = Text()

        # Header
        lines.append("  SYMBOL          PRICE          24h CHG      VOLUME\n", style="#7d9483")
        lines.append("  " + "─" * 60 + "\n", style="#2a3a30")

        for t in self.tickers[:20]:  # Show top 20
            sym = str(t.get("symbol", "?"))
            price = float(t.get("lastPrice", t.get("last_price", 0)) or 0)
            change_pct = float(t.get("priceChangePercent", t.get("price_change_pct", 0)) or 0)
            volume = float(t.get("volume", t.get("volume_24h", 0)) or 0)

            lines.append(f"  {sym:<15}", style="#a3b5a8")
            lines.append(f"{_format_price(price, sym):>14}  ", style="#e2ebe5")
            lines.append_text(_format_change(change_pct))
            lines.append(f"   {_format_volume(volume):>10}", style="#60a5fa")
            lines.append("\n")

        return lines


# ── Order Book Widget ────────────────────────────────────────────────


class OrderBookWidget(Static):
    """Renders order book depth with bids and asks side by side."""

    bids: reactive[list[list]] = reactive(list, layout=True)
    asks: reactive[list[list]] = reactive(list, layout=True)
    symbol: reactive[str] = reactive(DEFAULT_SYMBOL)

    DEFAULT_CSS = """
    OrderBookWidget {
        height: 1fr;
        min-height: 8;
        padding: 0 1;
        background: $bg;
    }
    """

    def render(self) -> Text:
        result = Text()
        result.append(f" ORDER BOOK — {self.symbol}\n", style="bold #e2ebe5")

        if not self.bids and not self.asks:
            result.append("  Loading order book…\n", style="#7d9483")
            return result

        # Header
        result.append("  BIDS (buys)           │  ASKS (sells)\n", style="#7d9483")
        result.append("  " + "─" * 23 + "┼" + "─" * 23 + "\n", style="#2a3a30")

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
                result.append(f"  {bar}", style="#4ade80")
                result.append(f" {b_price:>10,.2f} ", style="#e2ebe5")
                result.append(f"{b_size:>8.2f}", style="#a3b5a8")
            else:
                result.append(" " * 36)

            result.append(" │ ", style="#2a3a30")

            # Ask side
            if i < len(self.asks):
                a = self.asks[i]
                try:
                    a_price = float(a[0])
                    a_size = float(a[1]) if len(a) > 1 else 0
                except (ValueError, IndexError):
                    a_price, a_size = 0, 0
                result.append(f"{a_size:>8.2f}", style="#a3b5a8")
                result.append(f" {a_price:>10,.2f} ", style="#e2ebe5")
                bar_len = int(a_size / max_size * bar_width) if max_size > 0 else 0
                bar = "█" * bar_len + " " * (bar_width - bar_len)
                result.append(f"{bar}", style="#f87171")

            result.append("\n")

        # Spread
        if self.bids and self.asks:
            try:
                best_bid = float(self.bids[0][0])
                best_ask = float(self.asks[0][0])
                spread = best_ask - best_bid
                spread_pct = (spread / best_ask * 100) if best_ask else 0
                result.append(f"\n  Spread: {spread:,.2f} ({spread_pct:.3f}%)\n", style="#7d9483")
            except (ValueError, IndexError):
                pass

        return result


# ── Market Overview Screen ───────────────────────────────────────────


class MarketScreen(Screen[None]):
    """Market overview screen showing perp market data.

    Layout:
    - Top: Search bar for symbol filtering
    - Left: Symbol list with selection
    - Right top: Klines chart (ASCII sparkline)
    - Right middle: Ticker table
    - Right bottom: Order book depth

    Auto-refreshes every 30 seconds.
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "go_back", "Back", show=True),
        Binding("/", "focus_search", "Search", show=True),
        Binding("j", "move_down", "Down", show=False),
        Binding("k", "move_up", "Up", show=False),
        Binding("enter", "select_symbol", "Select", show=False),
        Binding("r", "refresh_now", "Refresh", show=True),
        Binding("ctrl+c", "go_back", "Back", show=False),
        Binding("question_mark", "app.show_help", "Help", show=False),
    ]

    # Reactive state
    current_symbol: reactive[str] = reactive(DEFAULT_SYMBOL)
    status_text: reactive[str] = reactive("Connecting…")
    is_loading: reactive[bool] = reactive(True)

    def __init__(self, api_client: TuiApiClient | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._api = api_client or TuiApiClient()
        self._owns_client = api_client is None

    def compose(self) -> ComposeResult:
        with Vertical(id="market-layout"):
            # Search bar
            yield Input(
                placeholder="Search symbols…",
                id="symbol-search",
            )
            with Horizontal(id="market-main"):
                # Left: Symbol list
                yield SymbolListWidget(id="symbol-list")
                with Vertical(id="market-detail"):
                    # Right top: Klines chart
                    yield KlinesChartWidget(id="klines-chart")
                    # Right middle: Ticker table
                    yield TickerTableWidget(id="ticker-table")
                    # Right bottom: Order book
                    yield OrderBookWidget(id="order-book")
            # Loading indicator + status
            yield LoadingIndicator(id="market-loading")
            yield Static(self.status_text, id="market-status")

    def on_mount(self) -> None:
        """Initialize the screen and start auto-refresh."""
        self._refresh_timer = self.set_interval(REFRESH_SECONDS, self._refresh_all)
        # Fire immediately after mount
        self.call_after_refresh(self._refresh_all)

    async def on_unmount(self) -> None:
        """Clean up the API client and timer when leaving."""
        if hasattr(self, "_refresh_timer"):
            self._refresh_timer.stop()
        if self._owns_client:
            await self._api.close()

    # ── Data Fetching ────────────────────────────────────────────────

    async def _refresh_all(self) -> None:
        """Fetch all market data and update widgets."""
        self.is_loading = True
        self.status_text = "Refreshing…"
        try:
            loading = self.query_one("#market-loading", LoadingIndicator)
            loading.loading = True
        except Exception:
            pass
        successes = 0
        try:
            # Fetch tickers (includes symbol list info)
            try:
                await self._fetch_tickers()
                successes += 1
            except Exception:
                pass
            # Fetch klines for selected symbol
            try:
                await self._fetch_klines()
                successes += 1
            except Exception:
                pass
            # Fetch order book for selected symbol
            try:
                await self._fetch_orderbook()
                successes += 1
            except Exception:
                pass

            if successes == 3:
                self.status_text = f"Live · {self.current_symbol} · refreshed  [r]efresh  [/]search  [j/k]nav  [?]help"
            elif successes > 0:
                self.status_text = f"Partial update ({successes}/3) · {self.current_symbol}  [r]etry"
            else:
                self.status_text = "Cannot reach API server  [r]etry"
                self.notify("Data refresh failed", severity="error")
        except Exception as exc:
            self.status_text = f"{friendly_error(exc)}  [r]etry"
            self.notify(friendly_error(exc), severity="error")
            logger.warning("Market refresh failed: %s", exc)
        finally:
            self.is_loading = False
            try:
                loading = self.query_one("#market-loading", LoadingIndicator)
                loading.loading = False
                loading.status_text = self.status_text
            except Exception:
                pass

    async def _fetch_tickers(self) -> None:
        """Fetch ticker data and update symbol list + ticker table."""
        try:
            data = await self._api.get_market_tickers()
            tickers = data.get("tickers", [])
            if tickers:
                # Update symbol list
                symbols = [
                    {
                        "name": t.get("symbol", "?"),
                        "symbol": t.get("symbol", "?"),
                        "price": float(t.get("lastPrice", 0) or 0),
                        "change_pct": float(t.get("priceChangePercent", 0) or 0),
                    }
                    for t in tickers
                ]
                # Sort by volume descending
                symbols.sort(
                    key=lambda s: abs(s.get("price", 0) * float(s.get("change_pct", 0))),
                    reverse=True,
                )
                try:
                    symbol_list = self.query_one("#symbol-list", SymbolListWidget)
                    symbol_list.set_symbols(symbols)
                except Exception:
                    pass

                # Update ticker table
                try:
                    ticker_table = self.query_one("#ticker-table", TickerTableWidget)
                    ticker_table.tickers = tickers
                except Exception:
                    pass
        except Exception as exc:
            logger.debug("Ticker fetch failed: %s", exc)

    async def _fetch_klines(self) -> None:
        """Fetch kline data for the current symbol."""
        try:
            data = await self._api.get_market_klines(
                self.current_symbol, DEFAULT_INTERVAL, KLINES_LIMIT
            )
            klines = data.get("klines", [])
            try:
                chart = self.query_one("#klines-chart", KlinesChartWidget)
                chart.symbol = self.current_symbol
                chart.candles = klines
            except Exception:
                pass
        except Exception as exc:
            logger.debug("Klines fetch failed for %s: %s", self.current_symbol, exc)

    async def _fetch_orderbook(self) -> None:
        """Fetch order book for the current symbol."""
        try:
            data = await self._api.get_market_orderbook(
                self.current_symbol, ORDERBOOK_LIMIT
            )
            try:
                book = self.query_one("#order-book", OrderBookWidget)
                book.symbol = self.current_symbol
                book.bids = data.get("bids", [])
                book.asks = data.get("asks", [])
            except Exception:
                pass
        except Exception as exc:
            logger.debug("Orderbook fetch failed for %s: %s", self.current_symbol, exc)

    # ── Event Handlers ───────────────────────────────────────────────

    def on_input_changed(self, event: Input.Changed) -> None:
        """Filter the symbol list based on search input."""
        try:
            symbol_list = self.query_one("#symbol-list", SymbolListWidget)
            symbol_list.set_filter(event.value)
        except Exception:
            pass

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Select the first matching symbol on Enter."""
        try:
            symbol_list = self.query_one("#symbol-list", SymbolListWidget)
            selected = symbol_list.get_selected_symbol()
            if selected:
                self._select_symbol(selected)
        except Exception:
            pass

    # ── Actions ──────────────────────────────────────────────────────

    def action_go_back(self) -> None:
        """Return to the main screen."""
        self.app.pop_screen()

    def action_focus_search(self) -> None:
        """Focus the search input."""
        try:
            self.query_one("#symbol-search", Input).focus()
        except Exception:
            pass

    def action_move_up(self) -> None:
        """Move selection up in the symbol list."""
        try:
            self.query_one("#symbol-list", SymbolListWidget).action_move_up()
        except Exception:
            pass

    def action_move_down(self) -> None:
        """Move selection down in the symbol list."""
        try:
            self.query_one("#symbol-list", SymbolListWidget).action_move_down()
        except Exception:
            pass

    def action_select_symbol(self) -> None:
        """Select the highlighted symbol and load its data."""
        try:
            symbol_list = self.query_one("#symbol-list", SymbolListWidget)
            selected = symbol_list.get_selected_symbol()
            if selected:
                self._select_symbol(selected)
        except Exception:
            pass

    def action_refresh_now(self) -> None:
        """Force an immediate data refresh."""
        self.call_after_refresh(self._refresh_all)

    def _select_symbol(self, symbol: str) -> None:
        """Change the active symbol and refresh its data."""
        if symbol != self.current_symbol:
            self.current_symbol = symbol
            self.call_after_refresh(self._refresh_klines_and_book)

    async def _refresh_klines_and_book(self) -> None:
        """Refresh klines and order book for the current symbol."""
        self.is_loading = True
        try:
            await self._fetch_klines()
            await self._fetch_orderbook()
        finally:
            self.is_loading = False
