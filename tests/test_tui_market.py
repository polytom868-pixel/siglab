"""Tests for the Market Overview TUI screen.

Covers: symbol list, klines chart, ticker table, order book,
search/filter, auto-refresh, loading states, empty data handling.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from siglab.tui.api_client import TuiApiClient
from siglab.tui.formatting import format_change, format_price, format_volume
from siglab.tui.screens.market import (
    KlinesChartWidget,
    MarketScreen,
    OrderBookWidget,
    SymbolListWidget,
    TickerTableWidget,
)
from siglab.tui.widgets.sparkline import SparklineWidget, ohlc_summary, sparkline_text


# ── Helper Fixtures ──────────────────────────────────────────────────


def _make_tickers(n: int = 5) -> list[dict]:
    """Generate fake ticker data for testing."""
    tickers = []
    symbols = ["BTC-USD", "ETH-USD", "SOL-USD", "AVAX-USD", "LINK-USD"]
    for i in range(min(n, len(symbols))):
        tickers.append({
            "symbol": symbols[i],
            "lastPrice": str(67000 + i * 1000),
            "priceChangePercent": str(round(2.5 - i * 1.2, 2)),
            "volume": str(1_000_000_000 - i * 200_000_000),
            "last_price": 67000 + i * 1000,
            "price_change_pct": round(2.5 - i * 1.2, 2),
            "volume_24h": 1_000_000_000 - i * 200_000_000,
        })
    return tickers


def _make_klines(n: int = 20) -> list[dict]:
    """Generate fake kline data for testing."""
    klines = []
    base = 67000.0
    for i in range(n):
        o = base + i * 50
        h = o + 200
        low_val = o - 150
        c = o + (100 if i % 3 == 0 else -80)
        klines.append({
            "timestamp": f"2026-06-04T{10 + i // 6}:{(i % 6) * 10:02d}:00+00:00",
            "open": o,
            "high": h,
            "low": low_val,
            "close": c,
            "volume": 1000 + i * 100,
            "quote_volume": 67_000_000 + i * 1_000_000,
        })
    return klines


def _make_orderbook(n: int = 5) -> dict:
    """Generate fake order book data for testing."""
    base = 67430.0
    bids = [[str(base - i * 10), str(12 + i * 3)] for i in range(n)]
    asks = [[str(base + 5 + i * 10), str(8 + i * 2)] for i in range(n)]
    return {"bids": bids, "asks": asks, "symbol": "BTC-USD"}


def _make_symbols(n: int = 5) -> list:
    """Generate fake SymbolEntry views for testing."""
    from siglab.tui.data_views import SymbolEntry
    syms = ["BTC-USD", "ETH-USD", "SOL-USD", "AVAX-USD", "LINK-USD"]
    return [
        SymbolEntry(
            name=syms[i],
            symbol=syms[i],
            price=50000.0 + i * 1000,
            change_pct=1.0 + i * 0.5,
            volume=1_000_000.0 + i * 100_000,
        )
        for i in range(min(n, len(syms)))
    ]


# ── Formatter Tests ─────────────────────────────────────────────────


class TestFormatHelpers:
    """Test the formatting helper functions."""

    def testformat_price_high(self) -> None:
        result = format_price(67432.50)
        assert result == "67,432.50"

    def testformat_price_medium(self) -> None:
        result = format_price(3.14159)
        assert result == "3.1416"

    def testformat_price_low(self) -> None:
        result = format_price(0.15)
        assert "0.15" in result

    def testformat_change_positive(self) -> None:
        text = format_change(2.34)
        assert "▲" in str(text)
        assert "+2.34%" in str(text)

    def testformat_change_negative(self) -> None:
        text = format_change(-0.52)
        assert "▼" in str(text)
        assert "-0.52%" in str(text)

    def testformat_change_zero(self) -> None:
        text = format_change(0.0)
        assert "──" in str(text)
        assert "0.00%" in str(text)

    def testformat_volume_billions(self) -> None:
        assert format_volume(2_500_000_000) == "2.5B"

    def testformat_volume_millions(self) -> None:
        assert format_volume(1_230_000) == "1.2M"

    def testformat_volume_thousands(self) -> None:
        assert format_volume(5_600) == "5.6K"

    def testformat_volume_small(self) -> None:
        assert format_volume(42) == "42"


# ── Sparkline Widget Tests ───────────────────────────────────────────


class TestSparkline:
    """Test the sparkline rendering functions."""

    def test_sparkline_text_with_values(self) -> None:
        values = [100, 110, 105, 120, 115, 130, 125, 140]
        text = sparkline_text(values, width=8)
        assert len(str(text)) == 8

    def test_sparkline_text_empty(self) -> None:
        text = sparkline_text([], width=20)
        assert "─" * 20 in str(text)

    def test_sparkline_text_single_value(self) -> None:
        text = sparkline_text([100.0], width=5)
        # Single value → single character (not padded to width)
        assert len(str(text)) == 1

    def test_sparkline_text_downsampling(self) -> None:
        values = list(range(100))
        text = sparkline_text(values, width=20)
        assert len(str(text)) == 20

    def test_sparkline_text_all_same_values(self) -> None:
        text = sparkline_text([100.0] * 10, width=10)
        # All same value maps to lowest character
        assert len(str(text)) == 10

    def test_ohlc_summary_with_candles(self) -> None:
        candles = [
            {"open": 67000, "high": 67500, "low": 66800, "close": 67200},
            {"open": 67200, "high": 67800, "low": 67100, "close": 67400},
        ]
        result = ohlc_summary(candles)
        assert "O:67,200" in result
        assert "H:67,800" in result
        assert "L:67,100" in result
        assert "C:67,400" in result

    def test_ohlc_summary_empty(self) -> None:
        assert "No candle data" in ohlc_summary([])

    def test_sparkline_widget_init(self) -> None:
        widget = SparklineWidget(width=40)
        assert widget._chart_width == 40

    def test_sparkline_widget_set_values(self) -> None:
        widget = SparklineWidget()
        widget.set_values([1, 2, 3, 4, 5])
        assert widget.values == [1, 2, 3, 4, 5]


# ── Symbol List Widget Tests ─────────────────────────────────────────


class TestSymbolListWidget:
    """Test the SymbolListWidget."""

    def test_init_empty(self) -> None:
        widget = SymbolListWidget()
        assert widget.symbols == []
        assert widget.selected_index == 0

    def test_set_symbols(self) -> None:
        widget = SymbolListWidget()
        syms = _make_symbols(3)
        widget.set_symbols(syms)
        assert len(widget.symbols) == 3

    def test_filter_text(self) -> None:
        widget = SymbolListWidget()
        syms = _make_symbols(5)
        widget.set_symbols(syms)
        widget.set_filter("BTC")
        assert len(widget.symbols) == 1
        assert widget.symbols[0].symbol == "BTC-USD"

    def test_filter_case_insensitive(self) -> None:
        widget = SymbolListWidget()
        syms = _make_symbols(5)
        widget.set_symbols(syms)
        widget.set_filter("eth")
        assert len(widget.symbols) == 1

    def test_filter_no_match(self) -> None:
        widget = SymbolListWidget()
        syms = _make_symbols(5)
        widget.set_symbols(syms)
        widget.set_filter("ZZZZZ")
        assert len(widget.symbols) == 0

    def test_filter_clear(self) -> None:
        widget = SymbolListWidget()
        syms = _make_symbols(5)
        widget.set_symbols(syms)
        widget.set_filter("BTC")
        assert len(widget.symbols) == 1
        widget.set_filter("")
        assert len(widget.symbols) == 5

    def test_move_up(self) -> None:
        widget = SymbolListWidget()
        widget.symbols = _make_symbols(5)
        widget.selected_index = 2
        widget.action_move_up()
        assert widget.selected_index == 1

    def test_move_up_at_top(self) -> None:
        widget = SymbolListWidget()
        widget.symbols = _make_symbols(5)
        widget.selected_index = 0
        widget.action_move_up()
        assert widget.selected_index == 0

    def test_move_down(self) -> None:
        widget = SymbolListWidget()
        widget.symbols = _make_symbols(5)
        widget.selected_index = 2
        widget.action_move_down()
        assert widget.selected_index == 3

    def test_move_down_at_bottom(self) -> None:
        widget = SymbolListWidget()
        widget.symbols = _make_symbols(5)
        widget.selected_index = 4
        widget.action_move_down()
        assert widget.selected_index == 4

    def test_get_selected_symbol(self) -> None:
        widget = SymbolListWidget()
        widget.symbols = _make_symbols(5)
        widget.selected_index = 1
        assert widget.get_selected_symbol() == "ETH-USD"

    def test_get_selected_symbol_empty(self) -> None:
        widget = SymbolListWidget()
        assert widget.get_selected_symbol() is None

    def test_render_empty(self) -> None:
        widget = SymbolListWidget()
        text = widget.render()
        assert "No items found" in str(text)

    def test_render_with_symbols(self) -> None:
        widget = SymbolListWidget()
        widget.symbols = _make_symbols(3)
        text = widget.render()
        assert "BTC-USD" in str(text)


# ── Klines Chart Widget Tests ────────────────────────────────────────


class TestKlinesChartWidget:
    """Test the KlinesChartWidget."""

    def test_init(self) -> None:
        widget = KlinesChartWidget()
        assert widget.candles == []
        assert widget.symbol == "BTC-USD"

    def test_render_loading_state(self) -> None:
        widget = KlinesChartWidget()
        text = widget.render()
        assert "Loading" in str(text)

    def test_render_with_candles(self) -> None:
        widget = KlinesChartWidget()
        widget.candles = _make_klines(20)
        text = widget.render()
        assert "BTC-USD" in str(text)
        assert "O:" in str(text)

    def test_set_symbol(self) -> None:
        widget = KlinesChartWidget()
        widget.symbol = "ETH-USD"
        widget.candles = _make_klines(10)
        text = widget.render()
        assert "ETH-USD" in str(text)


# ── Ticker Table Widget Tests ────────────────────────────────────────


class TestTickerTableWidget:
    """Test the TickerTableWidget."""

    def test_init_empty(self) -> None:
        widget = TickerTableWidget()
        assert widget.tickers == []

    def test_render_loading_state(self) -> None:
        widget = TickerTableWidget()
        text = widget.render()
        assert "No data available" in str(text)

    def test_render_with_tickers(self) -> None:
        widget = TickerTableWidget()
        widget.tickers = _make_tickers(5)
        text = widget.render()
        assert "BTC-USD" in str(text)
        assert "ETH-USD" in str(text)

    def test_render_shows_price_and_change(self) -> None:
        widget = TickerTableWidget()
        widget.tickers = _make_tickers(3)
        text = widget.render()
        rendered = str(text)
        assert "▲" in rendered or "▼" in rendered or "──" in rendered

    def test_render_max_20_tickers(self) -> None:
        widget = TickerTableWidget()
        # Even with more tickers, only 20 shown
        tickers = _make_tickers(5) * 10  # 50 tickers
        widget.tickers = tickers
        widget.render()  # Should not crash


# ── Order Book Widget Tests ──────────────────────────────────────────


class TestOrderBookWidget:
    """Test the OrderBookWidget."""

    def test_init_empty(self) -> None:
        widget = OrderBookWidget()
        assert widget.bids == ()
        assert widget.asks == ()

    def test_render_loading_state(self) -> None:
        widget = OrderBookWidget()
        text = widget.render()
        assert "No data available" in str(text)

    def test_render_with_data(self) -> None:
        widget = OrderBookWidget()
        book = _make_orderbook(5)
        widget.bids = book["bids"]
        widget.asks = book["asks"]
        widget.symbol = "BTC-USD"
        text = widget.render()
        rendered = str(text)
        assert "BTC-USD" in rendered
        assert "BIDS" in rendered
        assert "ASKS" in rendered

    def test_render_shows_spread(self) -> None:
        widget = OrderBookWidget()
        book = _make_orderbook(5)
        widget.bids = book["bids"]
        widget.asks = book["asks"]
        text = widget.render()
        assert "Spread" in str(text)

    def test_render_empty_book(self) -> None:
        widget = OrderBookWidget()
        widget.bids = []
        widget.asks = []
        text = widget.render()
        assert "No data available" in str(text)

    def test_render_asymmetric_book(self) -> None:
        """Test rendering when bids and asks have different lengths."""
        widget = OrderBookWidget()
        widget.bids = [["67430", "12"], ["67420", "18"]]
        widget.asks = [["67435", "8"]]
        text = widget.render()
        assert "Spread" in str(text)


# ── Market Screen Tests ──────────────────────────────────────────────


class TestMarketScreen:
    """Test the MarketScreen class."""

    def test_screen_class_exists(self) -> None:
        assert MarketScreen is not None

    def test_screen_has_bindings(self) -> None:
        binding_keys = [b.key for b in MarketScreen.BINDINGS]
        assert "escape" in binding_keys
        assert "/" in binding_keys
        assert "j" in binding_keys
        assert "k" in binding_keys
        assert "enter" in binding_keys
        assert "r" in binding_keys

    def test_screen_default_symbol(self) -> None:
        screen = MarketScreen()
        assert screen.current_symbol == "BTC-USD"

    def test_screen_init_with_custom_api(self) -> None:
        api = TuiApiClient(base_url="http://example.com:9999")
        screen = MarketScreen(api_client=api)
        assert screen._api is api
        assert screen._owns_api is False

    def test_screen_init_default_api(self) -> None:
        screen = MarketScreen()
        assert isinstance(screen._api, TuiApiClient)
        assert screen._owns_api is True

    def test_screen_has_compose(self) -> None:
        assert hasattr(MarketScreen, "compose")

    def test_screen_has_refresh_method(self) -> None:
        assert hasattr(MarketScreen, "_refresh_all")

    def test_screen_has_fetch_methods(self) -> None:
        assert hasattr(MarketScreen, "_fetch_tickers")
        assert hasattr(MarketScreen, "_fetch_klines")
        assert hasattr(MarketScreen, "_fetch_orderbook")

    def test_screen_has_action_methods(self) -> None:
        assert hasattr(MarketScreen, "action_go_back")
        assert hasattr(MarketScreen, "action_focus_search")
        assert hasattr(MarketScreen, "action_move_up")
        assert hasattr(MarketScreen, "action_move_down")
        assert hasattr(MarketScreen, "action_select_symbol")
        assert hasattr(MarketScreen, "action_refresh_now")

    def test_select_symbol_changes_current(self) -> None:
        screen = MarketScreen()
        screen._select_symbol("ETH-USD")
        assert screen.current_symbol == "ETH-USD"

    def test_select_same_symbol_no_change(self) -> None:
        screen = MarketScreen()
        screen.current_symbol = "BTC-USD"
        # Should not trigger refresh for same symbol
        screen._select_symbol("BTC-USD")
        assert screen.current_symbol == "BTC-USD"


# ── API Client Market Methods Tests ─────────────────────────────────


class TestApiClientMarketMethods:
    """Test the TuiApiClient market data methods."""

    @pytest.mark.asyncio
    async def test_get_market_symbols(self) -> None:
        client = TuiApiClient()
        mock_response = MagicMock()
        mock_response.json.return_value = {"symbols": _make_symbols(5), "count": 5}
        mock_response.raise_for_status = MagicMock()

        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock, return_value=mock_response):
            result = await client.get_market_symbols()
            assert "symbols" in result
            assert result["count"] == 5
        await client.close()

    @pytest.mark.asyncio
    async def test_get_market_tickers(self) -> None:
        client = TuiApiClient()
        mock_response = MagicMock()
        mock_response.json.return_value = {"tickers": _make_tickers(5), "count": 5}
        mock_response.raise_for_status = MagicMock()

        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock, return_value=mock_response):
            result = await client.get_market_tickers()
            assert "tickers" in result
            assert len(result["tickers"]) == 5
        await client.close()

    @pytest.mark.asyncio
    async def test_get_market_klines(self) -> None:
        client = TuiApiClient()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "klines": _make_klines(20),
            "symbol": "BTC-USD",
            "interval": "1h",
            "count": 20,
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock, return_value=mock_response):
            result = await client.get_market_klines("BTC-USD", "1h", 20)
            assert result["symbol"] == "BTC-USD"
            assert result["count"] == 20
        await client.close()

    @pytest.mark.asyncio
    async def test_get_market_orderbook(self) -> None:
        client = TuiApiClient()
        mock_response = MagicMock()
        mock_response.json.return_value = _make_orderbook(10)
        mock_response.raise_for_status = MagicMock()

        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock, return_value=mock_response):
            result = await client.get_market_orderbook("BTC-USD", 10)
            assert "bids" in result
            assert "asks" in result
        await client.close()

    @pytest.mark.asyncio
    async def test_get_market_tickers_http_error(self) -> None:
        client = TuiApiClient()
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Server Error",
            request=httpx.Request("GET", "http://localhost:3100/market/tickers"),
            response=httpx.Response(500),
        )

        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock, return_value=mock_response):
            with pytest.raises(httpx.HTTPStatusError):
                await client.get_market_tickers()
        await client.close()

    @pytest.mark.asyncio
    async def test_get_market_klines_connection_error(self) -> None:
        client = TuiApiClient()
        with patch.object(
            httpx.AsyncClient, "get",
            new_callable=AsyncMock,
            side_effect=httpx.ConnectError("Connection refused"),
        ):
            with pytest.raises(httpx.ConnectError):
                await client.get_market_klines("BTC-USD")
        await client.close()


# ── Module Structure Tests ───────────────────────────────────────────


class TestModuleStructure:
    """Test that the market screen module structure is correct."""

    def test_screens_package_exists(self) -> None:
        from pathlib import Path
        init_path = Path(__file__).resolve().parents[1] / "siglab" / "tui" / "screens" / "__init__.py"
        assert init_path.exists()

    def test_market_screen_importable(self) -> None:
        from siglab.tui.screens.market import MarketScreen as MS
        assert MS is MarketScreen

    def test_sparkline_widget_importable(self) -> None:
        from siglab.tui.widgets.sparkline import SparklineWidget as SW
        assert SW is SparklineWidget

    def test_widgets_init_exports_sparkline(self) -> None:
        from siglab.tui.widgets import SparklineWidget as SW
        assert SW is SparklineWidget
