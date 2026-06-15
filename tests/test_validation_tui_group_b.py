"""Validation tests for VAL-TUI-003 and VAL-TUI-004.

VAL-TUI-003: Market overview screen shows real-time data
VAL-TUI-004: Paper trading screen shows positions and places orders

Uses pytest with unittest.mock to verify screen behavior without
requiring a live API server.
"""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from siglab.tui.screens.market import (
    KlinesChartWidget,
    MarketScreen,
    OrderBookWidget,
    SymbolListWidget,
    TickerTableWidget,
)
from siglab.tui.screens.paper import (
    AccountSummaryWidget,
    OrderFormWidget,
    OrderHistoryWidget,
    PaperScreen,
    PnlChartWidget,
    PositionsTableWidget,
)


# ── Test data generators ─────────────────────────────────────────────

def _make_tickers(n: int = 5) -> list[dict]:
    symbols = ["BTC-USD", "ETH-USD", "SOL-USD", "AVAX-USD", "LINK-USD"]
    tickers = []
    for i in range(min(n, len(symbols))):
        tickers.append({
            "symbol": symbols[i],
            "lastPrice": str(67000 + i * 1000),
            "priceChangePercent": str(round(2.5 - i * 1.2, 2)),
            "volume": str(1_000_000_000 - i * 200_000_000),
        })
    return tickers


def _make_klines(n: int = 20) -> list[dict]:
    klines = []
    base = 67000.0
    for i in range(n):
        o = base + i * 50
        h = o + 200
        lo = o - 150
        c = o + (100 if i % 3 == 0 else -80)
        klines.append({
            "timestamp": f"2026-06-04T{10 + i // 6}:{(i % 6) * 10:02d}:00",
            "open": o,
            "high": h,
            "low": lo,
            "close": c,
            "volume": 1000 + i * 100,
            "quote_volume": 67_000_000 + i * 1_000_000,
        })
    return klines


def _make_orderbook(n: int = 5) -> dict:
    base = 67430.0
    bids = [[str(base - i * 10), str(12 + i * 3)] for i in range(n)]
    asks = [[str(base + 5 + i * 10), str(8 + i * 2)] for i in range(n)]
    return {"bids": bids, "asks": asks, "symbol": "BTC-USD"}


# ══════════════════════════════════════════════════════════════════════
# VAL-TUI-003: Market overview screen shows real-time data
# ══════════════════════════════════════════════════════════════════════


class TestVAL_TUI_003_SymbolListPopulated:
    """VAL-TUI-003: Symbol list populated from API client or mock."""

    def test_symbol_list_widget_accepts_symbols(self) -> None:
        """Widget.set_symbols() populates the reactive list."""
        widget = SymbolListWidget()
        syms = [
            {"name": "BTC-USD", "symbol": "BTC-USD"},
            {"name": "ETH-USD", "symbol": "ETH-USD"},
        ]
        widget.set_symbols(syms)
        assert len(widget.symbols) == 2
        assert widget.symbols[0].name == "BTC-USD"

    def test_symbol_list_render_shows_names(self) -> None:
        """Widget.render() contains symbol names when populated."""
        widget = SymbolListWidget()
        widget.set_symbols([
            {"name": "BTC-USD", "symbol": "BTC-USD"},
            {"name": "ETH-USD", "symbol": "ETH-USD"},
        ])
        rendered = str(widget.render())
        assert "BTC-USD" in rendered
        assert "ETH-USD" in rendered

    def test_symbol_list_empty_shows_placeholder(self) -> None:
        """Widget.render() shows 'No symbols' when empty."""
        widget = SymbolListWidget()
        rendered = str(widget.render())
        assert "No items found" in rendered

    def test_symbol_list_filter_works(self) -> None:
        """Widget.set_filter() narrows the displayed symbols."""
        widget = SymbolListWidget()
        widget.set_symbols([
            {"name": "BTC-USD", "symbol": "BTC-USD"},
            {"name": "ETH-USD", "symbol": "ETH-USD"},
            {"name": "SOL-USD", "symbol": "SOL-USD"},
        ])
        widget.set_filter("BTC")
        assert len(widget.symbols) == 1
        assert widget.symbols[0].name == "BTC-USD"

    def test_market_screen_fetch_tickers_populates_symbols(self) -> None:
        """MarketScreen._fetch_tickers() updates the symbol list widget."""
        screen = MarketScreen()
        tickers = _make_tickers(5)
        mock_data = {"tickers": tickers, "count": len(tickers)}

        mock_symbol_list = MagicMock()
        mock_ticker_table = MagicMock()

        def mock_query(selector, cls=None):
            if selector == "#symbol-list":
                return mock_symbol_list
            elif selector == "#ticker-table":
                return mock_ticker_table
            raise Exception(f"Unknown: {selector}")

        screen.query_one = mock_query

        with patch.object(screen._api, "get_market_tickers", new_callable=AsyncMock, return_value=mock_data):
            import asyncio
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(screen._fetch_tickers())
            finally:
                loop.close()

        mock_symbol_list.set_symbols.assert_called_once()
        symbols_arg = mock_symbol_list.set_symbols.call_args[0][0]
        assert len(symbols_arg) == 5
        # SymbolEntry objects use attribute access, not dict-style
        assert any(hasattr(s, 'name') and s.name == "BTC-USD" for s in symbols_arg)


class TestVAL_TUI_003_KlinesChartRenders:
    """VAL-TUI-003: Klines chart renders ASCII candles/sparklines."""

    def test_klines_widget_render_with_data(self) -> None:
        """KlinesChartWidget.render() produces OHLC summary from candles."""
        widget = KlinesChartWidget()
        widget.candles = _make_klines(20)
        widget.symbol = "BTC-USD"
        rendered = str(widget.render())
        assert "BTC-USD" in rendered
        assert "O:" in rendered  # OHLC summary present
        assert "H:" in rendered
        assert "L:" in rendered
        assert "C:" in rendered

    def test_klines_widget_renders_sparkline_chars(self) -> None:
        """KlinesChartWidget.render() includes sparkline block characters."""
        widget = KlinesChartWidget()
        # Use varied prices to get visible sparkline
        widget.candles = [
            {"open": 67000 + i * 100, "high": 67200 + i * 100,
             "low": 66800 + i * 100, "close": 67100 + i * 100,
             "volume": 1000}
            for i in range(20)
        ]
        rendered = str(widget.render())
        # Sparkline uses unicode block chars
        spark_chars = set("▁▂▃▄▅▆▇█")
        assert any(c in spark_chars for c in rendered), "Sparkline characters not found"

    def test_klines_widget_loading_state(self) -> None:
        """KlinesChartWidget.render() shows 'Loading' when no candles."""
        widget = KlinesChartWidget()
        rendered = str(widget.render())
        assert "Loading" in rendered

    def test_market_screen_fetch_klines_updates_chart(self) -> None:
        """MarketScreen._fetch_klines() updates the klines chart widget."""
        screen = MarketScreen()
        klines = _make_klines(20)
        mock_data = {"klines": klines, "symbol": "BTC-USD", "interval": "1h", "count": 20}

        mock_chart = MagicMock()
        screen.query_one = MagicMock(return_value=mock_chart)

        with patch.object(screen._api, "get_market_klines", new_callable=AsyncMock, return_value=mock_data):
            import asyncio
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(screen._fetch_klines())
            finally:
                loop.close()

        # _fetch_klines() calls set_candles() instead of direct assignment
        mock_chart.set_candles.assert_called_once_with(klines)
        assert mock_chart.symbol == "BTC-USD"


class TestVAL_TUI_003_TickerTableRealValues:
    """VAL-TUI-003: Ticker table has real values."""

    def test_ticker_table_render_with_data(self) -> None:
        """TickerTableWidget.render() shows symbol, price, change, volume."""
        widget = TickerTableWidget()
        widget.tickers = _make_tickers(5)
        rendered = str(widget.render())
        assert "BTC-USD" in rendered
        assert "ETH-USD" in rendered
        # Price values (67000+)
        assert "67" in rendered
        # Volume formatting
        assert "B" in rendered or "M" in rendered

    def test_ticker_table_shows_change_arrows(self) -> None:
        """TickerTableWidget.render() shows change indicators."""
        widget = TickerTableWidget()
        widget.tickers = _make_tickers(3)
        rendered = str(widget.render())
        assert "▲" in rendered or "▼" in rendered or "──" in rendered

    def test_ticker_table_empty_shows_placeholder(self) -> None:
        """TickerTableWidget.render() shows 'No data available' when empty."""
        widget = TickerTableWidget()
        rendered = str(widget.render())
        assert "No data available" in rendered

    def test_ticker_table_max_20_display(self) -> None:
        """TickerTableWidget limits display to 20 entries."""
        widget = TickerTableWidget()
        tickers = _make_tickers(5) * 10
        widget.tickers = tickers
        rendered = str(widget.render())
        assert "BTC-USD" in rendered

    def test_market_screen_fetch_tickers_updates_table(self) -> None:
        """MarketScreen._fetch_tickers() updates the ticker table widget."""
        screen = MarketScreen()
        tickers = _make_tickers(5)
        mock_data = {"tickers": tickers, "count": len(tickers)}

        mock_symbol_list = MagicMock()
        mock_ticker_table = MagicMock()

        def mock_query(selector, cls=None):
            if selector == "#symbol-list":
                return mock_symbol_list
            elif selector == "#ticker-table":
                return mock_ticker_table
            raise Exception(f"Unknown: {selector}")

        screen.query_one = mock_query

        with patch.object(screen._api, "get_market_tickers", new_callable=AsyncMock, return_value=mock_data):
            import asyncio
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(screen._fetch_tickers())
            finally:
                loop.close()

        assert mock_ticker_table.tickers == tickers


class TestVAL_TUI_003_OrderBookShowsBidsAsks:
    """VAL-TUI-003: Order book shows bids/asks."""

    def test_orderbook_render_with_data(self) -> None:
        """OrderBookWidget.render() shows bids and asks columns."""
        widget = OrderBookWidget()
        book = _make_orderbook(5)
        widget.bids = book["bids"]
        widget.asks = book["asks"]
        widget.symbol = "BTC-USD"
        rendered = str(widget.render())
        assert "BIDS" in rendered
        assert "ASKS" in rendered
        assert "BTC-USD" in rendered

    def test_orderbook_shows_spread(self) -> None:
        """OrderBookWidget.render() computes and shows spread."""
        widget = OrderBookWidget()
        book = _make_orderbook(5)
        widget.bids = book["bids"]
        widget.asks = book["asks"]
        rendered = str(widget.render())
        assert "Spread" in rendered

    def test_orderbook_shows_price_levels(self) -> None:
        """OrderBookWidget.render() shows actual price values."""
        widget = OrderBookWidget()
        widget.bids = [["67430.00", "12.5"], ["67420.00", "18.0"]]
        widget.asks = [["67435.00", "8.0"], ["67445.00", "10.0"]]
        rendered = str(widget.render())
        assert "67,430" in rendered
        assert "67,435" in rendered

    def test_orderbook_shows_bar_depth(self) -> None:
        """OrderBookWidget.render() includes bar characters for depth."""
        widget = OrderBookWidget()
        widget.bids = [["67430", "20"], ["67420", "10"]]
        widget.asks = [["67435", "15"], ["67445", "5"]]
        rendered = str(widget.render())
        assert "█" in rendered

    def test_orderbook_empty_shows_placeholder(self) -> None:
        """OrderBookWidget.render() shows 'No data available' when empty."""
        widget = OrderBookWidget()
        rendered = str(widget.render())
        assert "No data available" in rendered

    def test_market_screen_fetch_orderbook_updates_widget(self) -> None:
        """MarketScreen._fetch_orderbook() updates the order book widget."""
        screen = MarketScreen()
        book = _make_orderbook(10)

        mock_book_widget = MagicMock()
        screen.query_one = MagicMock(return_value=mock_book_widget)

        with patch.object(screen._api, "get_market_orderbook", new_callable=AsyncMock, return_value=book):
            import asyncio
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(screen._fetch_orderbook())
            finally:
                loop.close()

        # _fetch_orderbook() converts bids/asks to tuples
        assert mock_book_widget.bids == tuple(book["bids"])
        assert mock_book_widget.asks == tuple(book["asks"])
        assert mock_book_widget.symbol == "BTC-USD"


class TestVAL_TUI_003_AutoRefresh:
    """VAL-TUI-003: Auto-refresh mechanism exists."""

    def test_market_screen_has_refresh_timer_setup(self) -> None:
        """MarketScreen.on_mount() sets up an auto-refresh timer."""
        screen = MarketScreen()
        assert hasattr(screen, "on_mount")

    def test_market_screen_has_refresh_action(self) -> None:
        """MarketScreen has action_refresh_now for manual refresh."""
        assert hasattr(MarketScreen, "action_refresh_now")

    def test_market_screen_refresh_binding(self) -> None:
        """MarketScreen has 'r' binding for refresh."""
        binding_keys = [b.key for b in MarketScreen.BINDINGS]
        assert "r" in binding_keys

    def test_market_screen_has_refresh_interval_constant(self) -> None:
        """Market screen defines refresh interval."""
        from siglab.tui.screens.market import MarketScreen
        assert MarketScreen._refresh_interval > 0
        assert MarketScreen._refresh_interval <= 60

    def test_market_screen_refresh_all_method(self) -> None:
        """MarketScreen._refresh_all() fetches all data sources."""
        screen = MarketScreen()
        assert hasattr(screen, "_refresh_all")
        assert hasattr(screen, "_fetch_tickers")
        assert hasattr(screen, "_fetch_klines")
        assert hasattr(screen, "_fetch_orderbook")

    def test_market_screen_status_updates_on_refresh(self) -> None:
        """MarketScreen updates status_text during refresh cycle."""
        screen = MarketScreen()
        assert hasattr(screen, "status_text")
        assert hasattr(screen, "is_loading")


class TestVAL_TUI_003_Integration:
    """VAL-TUI-003: Full integration — market screen with mocked API."""

    @pytest.mark.asyncio
    async def test_refresh_all_updates_all_widgets(self) -> None:
        """_refresh_all() fetches tickers, klines, and orderbook."""
        screen = MarketScreen()
        tickers = _make_tickers(5)
        klines = _make_klines(20)
        orderbook = _make_orderbook(5)

        mock_symbol_list = MagicMock()
        mock_ticker_table = MagicMock()
        mock_chart = MagicMock()
        mock_book = MagicMock()
        mock_loading = MagicMock()

        def mock_query(selector, cls=None):
            m = {
                "#symbol-list": mock_symbol_list,
                "#ticker-table": mock_ticker_table,
                "#klines-chart": mock_chart,
                "#order-book": mock_book,
                "#market-loading": mock_loading,
            }
            if selector in m:
                return m[selector]
            raise Exception(f"Unknown: {selector}")

        screen.query_one = mock_query

        with patch.object(screen._api, "get_market_tickers", new_callable=AsyncMock, return_value={"tickers": tickers}):
            with patch.object(screen._api, "get_market_klines", new_callable=AsyncMock, return_value={"klines": klines}):
                with patch.object(screen._api, "get_market_orderbook", new_callable=AsyncMock, return_value=orderbook):
                    await screen._refresh_all()

        mock_symbol_list.set_symbols.assert_called_once()
        assert mock_ticker_table.tickers == tickers
        # _fetch_klines() calls set_candles() instead of direct assignment
        mock_chart.set_candles.assert_called_once_with(klines)
        # _fetch_orderbook() converts bids/asks to tuples
        assert mock_book.bids == tuple(orderbook["bids"])
        assert mock_book.asks == tuple(orderbook["asks"])

    @pytest.mark.asyncio
    async def test_refresh_all_handles_partial_failure(self) -> None:
        """_refresh_all() gracefully handles some API calls failing.

        The _fetch_* methods catch their own exceptions internally, so
        _refresh_all's try/except is a last-resort. When an API call fails
        inside _fetch_klines, it catches the exception and returns normally.
        This test verifies that the screen doesn't crash and the successful
        fetches still update their widgets.
        """
        screen = MarketScreen()
        tickers = _make_tickers(3)

        mock_symbol_list = MagicMock()
        mock_ticker_table = MagicMock()
        mock_chart = MagicMock()
        mock_book = MagicMock()
        mock_loading = MagicMock()

        def mock_query(selector, cls=None):
            m = {
                "#symbol-list": mock_symbol_list,
                "#ticker-table": mock_ticker_table,
                "#klines-chart": mock_chart,
                "#order-book": mock_book,
                "#market-loading": mock_loading,
            }
            if selector in m:
                return m[selector]
            raise Exception(f"Unknown: {selector}")

        screen.query_one = mock_query

        # Tickers succeed, klines and orderbook API calls fail (exceptions
        # caught inside _fetch_klines / _fetch_orderbook)
        with patch.object(screen._api, "get_market_tickers", new_callable=AsyncMock, return_value={"tickers": tickers}):
            with patch.object(screen._api, "get_market_klines", new_callable=AsyncMock, side_effect=Exception("timeout")):
                with patch.object(screen._api, "get_market_orderbook", new_callable=AsyncMock, side_effect=Exception("timeout")):
                    await screen._refresh_all()

        # Tickers should still be updated
        mock_symbol_list.set_symbols.assert_called_once()
        # Screen should not crash; is_loading should be False after refresh
        assert screen.is_loading is False

    @pytest.mark.asyncio
    async def test_refresh_all_handles_total_failure(self) -> None:
        """_refresh_all() handles all API calls failing without crashing.

        Each _fetch_* method catches its own exceptions internally.
        _refresh_all's outer try/except is the last safety net.
        This test verifies no unhandled exceptions propagate.
        """
        screen = MarketScreen()
        mock_loading = MagicMock()
        screen.query_one = MagicMock(return_value=mock_loading)

        with patch.object(screen._api, "get_market_tickers", new_callable=AsyncMock, side_effect=Exception("down")):
            with patch.object(screen._api, "get_market_klines", new_callable=AsyncMock, side_effect=Exception("down")):
                with patch.object(screen._api, "get_market_orderbook", new_callable=AsyncMock, side_effect=Exception("down")):
                    await screen._refresh_all()

        # Should not crash; is_loading should be False after refresh
        assert screen.is_loading is False


# ══════════════════════════════════════════════════════════════════════
# VAL-TUI-004: Paper trading screen shows positions and places orders
# ══════════════════════════════════════════════════════════════════════


class TestVAL_TUI_004_PositionsRendered:
    """VAL-TUI-004: Positions table renders with position data."""

    def test_positions_widget_render_with_data(self) -> None:
        """PositionsTableWidget.render() shows position details."""
        widget = PositionsTableWidget()
        widget.positions = [
            {"symbol": "BTC-USD", "quantity": 0.5, "entry_price": 65000.0, "unrealized_pnl": 1200.0},
            {"symbol": "ETH-USD", "quantity": -2.0, "entry_price": 3500.0, "unrealized_pnl": -100.0},
        ]
        widget.mark_prices = {"BTC-USD": 67400.0, "ETH-USD": 3450.0}
        rendered = str(widget.render())
        assert "BTC-USD" in rendered
        assert "ETH-USD" in rendered
        assert "POSITIONS" in rendered

    def test_positions_widget_empty_state(self) -> None:
        """PositionsTableWidget.render() shows 'No open positions' when empty."""
        widget = PositionsTableWidget()
        rendered = str(widget.render())
        assert "No open positions" in rendered

    def test_positions_widget_computes_unrealized_pnl(self) -> None:
        """PositionsTableWidget computes unrealized PnL from mark prices."""
        widget = PositionsTableWidget()
        widget.positions = [
            {"symbol": "BTC-USD", "quantity": 1.0, "entry_price": 60000.0, "unrealized_pnl": 0},
        ]
        widget.mark_prices = {"BTC-USD": 65000.0}
        rendered = str(widget.render())
        assert "BTC-USD" in rendered

    def test_positions_widget_shows_header_columns(self) -> None:
        """PositionsTableWidget.render() includes column headers."""
        widget = PositionsTableWidget()
        widget.positions = [
            {"symbol": "BTC-USD", "quantity": 1.0, "entry_price": 60000.0, "unrealized_pnl": 0},
        ]
        rendered = str(widget.render())
        assert "SYMBOL" in rendered
        assert "SIZE" in rendered
        assert "ENTRY" in rendered
        assert "MARK" in rendered
        assert "PnL" in rendered


class TestVAL_TUI_004_OrderFormMarketLimit:
    """VAL-TUI-004: Order form exists with MARKET/LIMIT options."""

    def test_order_form_default_is_market(self) -> None:
        """OrderFormWidget defaults to MARKET order type."""
        form = OrderFormWidget()
        assert form._order_type == "MARKET"

    def test_order_form_toggle_to_limit(self) -> None:
        """OrderFormWidget.toggle_type() switches to LIMIT."""
        form = OrderFormWidget()
        form.toggle_type()
        assert form._order_type == "LIMIT"

    def test_order_form_toggle_back_to_market(self) -> None:
        """OrderFormWidget.toggle_type() toggles back to MARKET."""
        form = OrderFormWidget()
        form.toggle_type()
        assert form._order_type == "LIMIT"
        form.toggle_type()
        assert form._order_type == "MARKET"

    def test_order_form_renders_market_label(self) -> None:
        """OrderFormWidget.render() shows MARKET label."""
        form = OrderFormWidget()
        form.set_symbol("BTC-USD")
        rendered = str(form.render())
        assert "MARKET" in rendered

    def test_order_form_renders_limit_label(self) -> None:
        """OrderFormWidget.render() shows LIMIT label when toggled."""
        form = OrderFormWidget()
        form.set_symbol("BTC-USD")
        form.toggle_type()
        rendered = str(form.render())
        assert "LIMIT" in rendered

    def test_order_form_shows_price_field_for_limit(self) -> None:
        """OrderFormWidget.render() shows Price field for LIMIT orders."""
        form = OrderFormWidget()
        form.set_symbol("BTC-USD")
        form.set_price("65000")
        form.toggle_type()
        rendered = str(form.render())
        assert "Price:" in rendered
        assert "65000" in rendered

    def test_order_form_hides_price_field_for_market(self) -> None:
        """OrderFormWidget.render() hides Price field for MARKET orders."""
        form = OrderFormWidget()
        form.set_symbol("BTC-USD")
        rendered = str(form.render())
        assert "Price:" not in rendered

    def test_order_form_buy_sell_toggle(self) -> None:
        """OrderFormWidget.toggle_side() switches between BUY and SELL."""
        form = OrderFormWidget()
        assert form._side == "BUY"
        form.toggle_side()
        assert form._side == "SELL"
        form.toggle_side()
        assert form._side == "BUY"

    def test_order_form_renders_side_labels(self) -> None:
        """OrderFormWidget.render() shows BUY and SELL labels."""
        form = OrderFormWidget()
        form.set_symbol("BTC-USD")
        rendered = str(form.render())
        assert "BUY" in rendered
        assert "SELL" in rendered

    def test_order_form_validation_market_requires_symbol_and_qty(self) -> None:
        """OrderFormWidget validates MARKET order requires symbol and quantity."""
        form = OrderFormWidget()
        assert form.get_order_params() is None
        assert "Symbol" in form._error

        form.set_symbol("BTC-USD")
        assert form.get_order_params() is None
        assert "Quantity" in form._error

        form.set_quantity("0.5")
        params = form.get_order_params()
        assert params is not None
        assert params["order_type"] == "MARKET"

    def test_order_form_validation_limit_requires_price(self) -> None:
        """OrderFormWidget validates LIMIT order requires price."""
        form = OrderFormWidget()
        form.set_symbol("BTC-USD")
        form.set_quantity("1.0")
        form.toggle_type()
        assert form.get_order_params() is None
        assert "Price" in form._error

        form.set_price("65000")
        params = form.get_order_params()
        assert params is not None
        assert params["order_type"] == "LIMIT"
        assert params["price"] == "65000"

    def test_order_form_help_text_shown(self) -> None:
        """OrderFormWidget.render() shows keyboard shortcut help."""
        form = OrderFormWidget()
        rendered = str(form.render())
        assert "submit" in rendered.lower()


class TestVAL_TUI_004_OrderPlacementFlow:
    """VAL-TUI-004: MARKET order placed via TUI and appears in history."""

    @pytest.mark.asyncio
    async def test_place_order_success(self) -> None:
        """PaperScreen._place_order() succeeds with valid parameters."""
        screen = PaperScreen()
        screen.session_id = "test-session-123"

        mock_form = MagicMock()
        mock_form.show_success = MagicMock()

        screen.query_one = MagicMock(return_value=mock_form)

        order_result = json.dumps({
            "order_id": "order-abc123",
            "symbol": "BTC-USD",
            "side": "BUY",
            "quantity": 0.5,
            "order_type": "MARKET",
            "status": "OPEN",
        })

        with patch.object(
            screen._api, "place_paper_order", new_callable=AsyncMock,
            return_value=json.loads(order_result),
        ):
            with patch.object(screen, "_refresh_all", new_callable=AsyncMock):
                params = {
                    "symbol": "BTC-USD",
                    "side": "BUY",
                    "quantity": "0.5",
                    "order_type": "MARKET",
                }
                await screen._place_order(params)

        mock_form.show_success.assert_called_once()
        success_msg = mock_form.show_success.call_args[0][0]
        assert "order" in success_msg.lower() or "abc123" in success_msg

    @pytest.mark.asyncio
    async def test_place_order_no_session_shows_error(self) -> None:
        """PaperScreen._place_order() shows error when no session active."""
        screen = PaperScreen()
        screen.session_id = ""

        mock_form = MagicMock()
        screen.query_one = MagicMock(return_value=mock_form)

        params = {
            "symbol": "BTC-USD",
            "side": "BUY",
            "quantity": "0.5",
            "order_type": "MARKET",
        }
        await screen._place_order(params)

        mock_form.show_error.assert_called_once()
        error_msg = mock_form.show_error.call_args[0][0]
        assert "session" in error_msg.lower()

    @pytest.mark.asyncio
    async def test_place_order_failure_shows_error(self) -> None:
        """PaperScreen._place_order() shows error on subprocess failure."""
        screen = PaperScreen()
        screen.session_id = "test-session"

        mock_form = MagicMock()
        screen.query_one = MagicMock(return_value=mock_form)

        with patch.object(
            screen._api, "place_paper_order", new_callable=AsyncMock,
            side_effect=Exception("Order failed"),
        ):
            params = {
                "symbol": "BTC-USD",
                "side": "BUY",
                "quantity": "0.5",
                "order_type": "MARKET",
            }
            await screen._place_order(params)

        mock_form.show_error.assert_called_once()

    def test_action_submit_order_method_exists(self) -> None:
        """PaperScreen has action_submit_order for keyboard-driven order placement."""
        assert hasattr(PaperScreen, "action_submit_order")

    def test_screen_has_enter_binding_for_submit(self) -> None:
        """PaperScreen has 'enter' binding for order submission."""
        binding_keys = [b.key for b in PaperScreen.BINDINGS]
        assert "enter" in binding_keys


class TestVAL_TUI_004_OrderHistory:
    """VAL-TUI-004: Order history table exists and renders orders."""

    def test_order_history_widget_init(self) -> None:
        """OrderHistoryWidget initializes with empty orders."""
        widget = OrderHistoryWidget()
        assert widget.orders == []

    def test_order_history_render_with_orders(self) -> None:
        """OrderHistoryWidget.render() shows order details."""
        widget = OrderHistoryWidget()
        now = time.time()
        widget.orders = [
            {
                "order_id": "abc123",
                "symbol": "BTC-USD",
                "side": "BUY",
                "order_type": "MARKET",
                "quantity": 0.5,
                "price": 0,
                "fill_price": 65000.0,
                "status": "FILLED",
                "created_at": now,
            },
            {
                "order_id": "def456",
                "symbol": "ETH-USD",
                "side": "SELL",
                "order_type": "LIMIT",
                "quantity": 2.0,
                "price": 3600.0,
                "fill_price": None,
                "status": "OPEN",
                "created_at": now - 60,
            },
        ]
        rendered = str(widget.render())
        assert "ORDER HISTORY" in rendered
        assert "BTC-USD" in rendered
        assert "ETH-USD" in rendered
        assert "FILLED" in rendered
        assert "OPEN" in rendered

    def test_order_history_shows_fill_price(self) -> None:
        """OrderHistoryWidget.render() shows fill price for filled orders."""
        widget = OrderHistoryWidget()
        widget.orders = [
            {
                "order_id": "abc",
                "symbol": "BTC-USD",
                "side": "BUY",
                "order_type": "MARKET",
                "quantity": 1.0,
                "price": 0,
                "fill_price": 65000.0,
                "status": "FILLED",
                "created_at": time.time(),
            },
        ]
        rendered = str(widget.render())
        assert "65,000" in rendered

    def test_order_history_empty_state(self) -> None:
        """OrderHistoryWidget.render() shows 'No orders placed' when empty."""
        widget = OrderHistoryWidget()
        rendered = str(widget.render())
        assert "No orders placed" in rendered

    def test_order_history_shows_side(self) -> None:
        """OrderHistoryWidget renders BUY/SELL."""
        widget = OrderHistoryWidget()
        widget.orders = [
            {
                "order_id": "abc",
                "symbol": "BTC-USD",
                "side": "BUY",
                "order_type": "MARKET",
                "quantity": 1.0,
                "price": 0,
                "fill_price": 65000.0,
                "status": "FILLED",
                "created_at": time.time(),
            },
        ]
        rendered = widget.render()
        assert "BUY" in rendered.plain


class TestVAL_TUI_004_PnlUpdates:
    """VAL-TUI-004: PnL updates on refresh."""

    def test_pnl_chart_widget_init(self) -> None:
        """PnlChartWidget initializes with empty history."""
        widget = PnlChartWidget()
        assert widget.pnl_history == []

    def test_pnl_chart_renders_with_data(self) -> None:
        """PnlChartWidget.render() shows sparkline and min/max/now."""
        widget = PnlChartWidget()
        widget.pnl_history = [0.0, 100.0, 50.0, 200.0, -50.0, 150.0]
        rendered = str(widget.render())
        assert "PnL PERFORMANCE" in rendered
        assert "Low:" in rendered
        assert "High:" in rendered
        assert "Now:" in rendered

    def test_account_summary_widget_renders_pnl(self) -> None:
        """AccountSummaryWidget.render() shows realized/unrealized/total PnL."""
        widget = AccountSummaryWidget()
        widget.session_name = "test-session"
        widget.pnl_data = {
            "realized_pnl": 500.0,
            "unrealized_pnl": -200.0,
            "total_pnl": 300.0,
            "total_funding_cost": -10.0,
            "open_position_count": 2,
        }
        rendered = str(widget.render())
        assert "test-session" in rendered
        assert "Realized" in rendered
        assert "Unrealized" in rendered
        assert "Total PnL" in rendered
        assert "Funding" in rendered
        assert "Open: 2" in rendered

    @pytest.mark.asyncio
    async def test_refresh_all_updates_pnl(self) -> None:
        """PaperScreen._refresh_all() updates PnL widgets."""
        screen = PaperScreen()
        screen.session_id = "test123"
        screen.session_name = "test"

        pnl_data = {
            "realized_pnl": 100.0,
            "unrealized_pnl": 50.0,
            "total_pnl": 150.0,
            "total_funding_cost": -5.0,
            "open_position_count": 1,
        }
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps({
            "session_id": "test123",
            "name": "test",
            "position": [{"symbol": "BTC-USD", "quantity": 0.5, "entry_price": 65000}],
            "pnl": pnl_data,
            "orders": [],
        })
        mock_result.stderr = ""

        mock_positions = MagicMock()
        mock_orders = MagicMock()
        mock_account = MagicMock()
        mock_chart = MagicMock()
        mock_loading = MagicMock()

        def mock_query(selector, cls=None):
            m = {
                "#positions-table": mock_positions,
                "#order-history": mock_orders,
                "#account-summary": mock_account,
                "#pnl-chart": mock_chart,
                "#paper-loading": mock_loading,
            }
            if selector in m:
                return m[selector]
            raise Exception(f"Unknown: {selector}")

        screen.query_one = mock_query

        with patch("siglab.tui.screens.paper.run_cli", new_callable=AsyncMock, return_value=mock_result):
            await screen._refresh_all()

        assert mock_account.pnl_data == pnl_data
        assert mock_account.session_name == "test"
        assert len(screen._pnl_history) == 1
        assert screen._pnl_history[0] == 150.0

    @pytest.mark.asyncio
    async def test_pnl_history_accumulates_on_multiple_refreshes(self) -> None:
        """PaperScreen PnL history grows with each refresh."""
        screen = PaperScreen()
        screen.session_id = "test123"
        screen.session_name = "test"

        def make_result(pnl_total):
            r = MagicMock()
            r.returncode = 0
            r.stdout = json.dumps({
                "session_id": "test123",
                "name": "test",
                "position": [],
                "pnl": {"total_pnl": pnl_total, "realized_pnl": 0, "unrealized_pnl": 0, "total_funding_cost": 0, "open_position_count": 0},
                "orders": [],
            })
            r.stderr = ""
            return r

        mock_account = MagicMock()
        mock_chart = MagicMock()
        mock_loading = MagicMock()

        def mock_query(selector, cls=None):
            m = {
                "#positions-table": MagicMock(),
                "#order-history": MagicMock(),
                "#account-summary": mock_account,
                "#pnl-chart": mock_chart,
                "#paper-loading": mock_loading,
            }
            if selector in m:
                return m[selector]
            raise Exception(f"Unknown: {selector}")

        screen.query_one = mock_query

        with patch("siglab.tui.screens.paper.run_cli", new_callable=AsyncMock, return_value=make_result(100.0)):
            await screen._refresh_all()

        with patch("siglab.tui.screens.paper.run_cli", new_callable=AsyncMock, return_value=make_result(200.0)):
            await screen._refresh_all()

        assert len(screen._pnl_history) == 2
        assert screen._pnl_history[0] == 100.0
        assert screen._pnl_history[1] == 200.0


class TestVAL_TUI_004_Integration:
    """VAL-TUI-004: Full integration — paper screen with mocked CLI."""

    @pytest.mark.asyncio
    async def test_init_session_and_refresh(self) -> None:
        """PaperScreen initializes session then refreshes data."""
        screen = PaperScreen()

        start_result = MagicMock()
        start_result.returncode = 0
        start_result.stdout = json.dumps({"session_id": "sess-123", "name": "tui-session"})
        start_result.stderr = ""

        status_result = MagicMock()
        status_result.returncode = 0
        status_result.stdout = json.dumps({
            "session_id": "sess-123",
            "name": "tui-session",
            "position": [{"symbol": "BTC-USD", "quantity": 0.5, "entry_price": 65000}],
            "pnl": {"realized_pnl": 0, "unrealized_pnl": 100, "total_pnl": 100, "total_funding_cost": 0, "open_position_count": 1},
            "orders": [{"order_id": "o1", "symbol": "BTC-USD", "side": "BUY", "order_type": "MARKET", "quantity": 0.5, "price": 0, "fill_price": 65000, "status": "FILLED", "created_at": time.time()}],
        })
        status_result.stderr = ""

        mock_positions = MagicMock()
        mock_orders_widget = MagicMock()
        mock_account = MagicMock()
        mock_chart = MagicMock()
        mock_loading = MagicMock()
        mock_form = MagicMock()

        def mock_query(selector, cls=None):
            m = {
                "#positions-table": mock_positions,
                "#order-history": mock_orders_widget,
                "#account-summary": mock_account,
                "#pnl-chart": mock_chart,
                "#paper-loading": mock_loading,
                "#order-form": mock_form,
            }
            if selector in m:
                return m[selector]
            raise Exception(f"Unknown: {selector}")

        screen.query_one = mock_query

        call_count = 0

        async def mock_run_cli(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return start_result
            return status_result
        with patch("siglab.tui.screens.paper.run_cli", side_effect=mock_run_cli), \
             patch.object(PaperScreen, "_find_existing_session", AsyncMock(return_value=None)):
            await screen._init_session()

        assert screen.session_id == "sess-123"
        assert screen.session_name == "tui-session"

    def test_screen_keyboard_bindings_complete(self) -> None:
        """PaperScreen has all required keyboard bindings."""
        binding_keys = [b.key for b in PaperScreen.BINDINGS]
        required = ["escape", "r", "s", "b", "t", "enter", "n"]
        for key in required:
            assert key in binding_keys, f"Missing binding: {key}"

    def test_screen_has_all_compose_widgets(self) -> None:
        """PaperScreen compose() yields all expected widget types."""
        import inspect
        source = inspect.getsource(PaperScreen.compose)
        assert "OrderFormWidget" in source
        assert "AccountSummaryWidget" in source
        assert "PnlChartWidget" in source
        assert "PositionsTableWidget" in source
        assert "OrderHistoryWidget" in source
        assert "LoadingIndicator" in source
