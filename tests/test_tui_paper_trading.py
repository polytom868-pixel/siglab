"""Tests for the SigLab Paper Trading TUI screen.

Covers: PaperScreen, PositionsTableWidget, OrderFormWidget,
OrderHistoryWidget, PnlChartWidget, AccountSummaryWidget,
text input modal, and integration with CLI bridge.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from siglab.tui.screens.paper import (
    AccountSummaryWidget,
    OrderFormWidget,
    OrderHistoryWidget,
    PaperScreen,
    PnlChartWidget,
    PositionsTableWidget,
    _TextInputScreen,
    _fmt_price,
    _fmt_pnl,
    _fmt_qty,
    _side_style,
    _status_style,
)


# ── Formatting Helper Tests ──────────────────────────────────────────


class TestFormattingHelpers:
    """Test the formatting utility functions."""

    def test_fmt_price_large(self) -> None:
        assert _fmt_price(67210.50) == "67,210.50"

    def test_fmt_price_medium(self) -> None:
        assert _fmt_price(1.2345) == "1.2345"

    def test_fmt_price_small(self) -> None:
        assert _fmt_price(0.001234) == "0.001234"

    def test_fmt_price_zero(self) -> None:
        assert _fmt_price(0) == "0.000000"

    def test_fmt_pnl_positive(self) -> None:
        text = _fmt_pnl(123.45)
        assert "+123.45" in text.plain

    def test_fmt_pnl_negative(self) -> None:
        text = _fmt_pnl(-456.78)
        assert "-456.78" in text.plain

    def test_fmt_pnl_zero(self) -> None:
        text = _fmt_pnl(0.0)
        assert "0.00" in text.plain

    def test_fmt_qty_large(self) -> None:
        assert _fmt_qty(1_500_000) == "1.50M"

    def test_fmt_qty_medium(self) -> None:
        assert _fmt_qty(5_000) == "5.00K"

    def test_fmt_qty_small(self) -> None:
        assert _fmt_qty(0.5) == "0.5000"

    def test_status_style_filled(self) -> None:
        assert _status_style("FILLED") == "#4ade80"

    def test_status_style_open(self) -> None:
        assert _status_style("OPEN") == "#60a5fa"

    def test_status_style_cancelled(self) -> None:
        assert _status_style("CANCELLED") == "#f0b456"

    def test_status_style_expired(self) -> None:
        assert _status_style("EXPIRED") == "#7d9483"

    def test_status_style_unknown(self) -> None:
        assert _status_style("UNKNOWN") == "#a3b5a8"

    def test_side_style_buy(self) -> None:
        assert _side_style("BUY") == "#4ade80"

    def test_side_style_sell(self) -> None:
        assert _side_style("SELL") == "#f87171"


# ── PositionsTableWidget Tests ───────────────────────────────────────


class TestPositionsTableWidget:
    """Test the positions table widget rendering."""

    def test_init(self) -> None:
        widget = PositionsTableWidget()
        assert widget.positions == []
        assert widget.mark_prices == {}

    def test_render_empty(self) -> None:
        widget = PositionsTableWidget()
        text = widget.render()
        assert "No open positions" in text.plain

    def test_render_with_positions(self) -> None:
        widget = PositionsTableWidget()
        widget.positions = [
            {
                "symbol": "BTC-USD",
                "quantity": 0.5,
                "entry_price": 65000.0,
                "unrealized_pnl": 1200.0,
            },
            {
                "symbol": "ETH-USD",
                "quantity": -2.0,
                "entry_price": 3500.0,
                "unrealized_pnl": -100.0,
            },
        ]
        widget.mark_prices = {"BTC-USD": 67400.0, "ETH-USD": 3450.0}
        text = widget.render()
        assert "BTC-USD" in text.plain
        assert "ETH-USD" in text.plain
        assert "POSITIONS" in text.plain

    def test_render_with_mark_prices(self) -> None:
        widget = PositionsTableWidget()
        widget.positions = [
            {"symbol": "BTC-USD", "quantity": 1.0, "entry_price": 60000.0, "unrealized_pnl": 0},
        ]
        widget.mark_prices = {"BTC-USD": 65000.0}
        text = widget.render()
        # Should show computed unrealized PnL
        assert "BTC-USD" in text.plain


# ── AccountSummaryWidget Tests ───────────────────────────────────────


class TestAccountSummaryWidget:
    """Test the account summary widget rendering."""

    def test_init(self) -> None:
        widget = AccountSummaryWidget()
        assert widget.pnl_data == {}
        assert widget.session_name == ""

    def test_render_empty(self) -> None:
        widget = AccountSummaryWidget()
        text = widget.render()
        assert "No PnL data" in text.plain

    def test_render_with_data(self) -> None:
        widget = AccountSummaryWidget()
        widget.session_name = "test-session"
        widget.pnl_data = {
            "realized_pnl": 500.0,
            "unrealized_pnl": -200.0,
            "total_pnl": 300.0,
            "total_funding_cost": -10.0,
            "open_position_count": 2,
        }
        text = widget.render()
        assert "test-session" in text.plain
        assert "Realized" in text.plain
        assert "Unrealized" in text.plain
        assert "Total PnL" in text.plain
        assert "Funding" in text.plain
        assert "Open: 2" in text.plain


# ── PnlChartWidget Tests ────────────────────────────────────────────


class TestPnlChartWidget:
    """Test the PnL sparkline chart widget."""

    def test_init(self) -> None:
        widget = PnlChartWidget()
        assert widget.pnl_history == []

    def test_render_empty(self) -> None:
        widget = PnlChartWidget()
        text = widget.render()
        assert "Collecting data" in text.plain

    def test_render_with_history(self) -> None:
        widget = PnlChartWidget()
        widget.pnl_history = [0.0, 100.0, 50.0, 200.0, -50.0, 150.0]
        text = widget.render()
        assert "PnL PERFORMANCE" in text.plain
        assert "Low:" in text.plain
        assert "High:" in text.plain
        assert "Now:" in text.plain


# ── OrderFormWidget Tests ────────────────────────────────────────────


class TestOrderFormWidget:
    """Test the order form widget validation and state."""

    def test_init(self) -> None:
        widget = OrderFormWidget()
        assert widget._side == "BUY"
        assert widget._order_type == "MARKET"
        assert widget._symbol == ""

    def test_set_symbol(self) -> None:
        widget = OrderFormWidget()
        widget.set_symbol("BTC-USD")
        assert widget._symbol == "BTC-USD"

    def test_set_quantity(self) -> None:
        widget = OrderFormWidget()
        widget.set_quantity("0.5")
        assert widget._quantity == "0.5"

    def test_set_price(self) -> None:
        widget = OrderFormWidget()
        widget.set_price("65000")
        assert widget._price == "65000"

    def test_toggle_side(self) -> None:
        widget = OrderFormWidget()
        assert widget._side == "BUY"
        widget.toggle_side()
        assert widget._side == "SELL"
        widget.toggle_side()
        assert widget._side == "BUY"

    def test_toggle_type(self) -> None:
        widget = OrderFormWidget()
        assert widget._order_type == "MARKET"
        widget.toggle_type()
        assert widget._order_type == "LIMIT"
        widget.toggle_type()
        assert widget._order_type == "MARKET"

    def test_get_order_params_market_valid(self) -> None:
        widget = OrderFormWidget()
        widget.set_symbol("BTC-USD")
        widget.set_quantity("0.5")
        params = widget.get_order_params()
        assert params is not None
        assert params["symbol"] == "BTC-USD"
        assert params["side"] == "BUY"
        assert params["order_type"] == "MARKET"
        assert params["quantity"] == "0.5"
        assert "price" not in params

    def test_get_order_params_limit_valid(self) -> None:
        widget = OrderFormWidget()
        widget.set_symbol("ETH-USD")
        widget.set_quantity("2.0")
        widget.set_price("3500")
        widget.toggle_type()  # switch to LIMIT
        params = widget.get_order_params()
        assert params is not None
        assert params["order_type"] == "LIMIT"
        assert params["price"] == "3500"

    def test_get_order_params_no_symbol(self) -> None:
        widget = OrderFormWidget()
        widget.set_quantity("1.0")
        params = widget.get_order_params()
        assert params is None
        assert "Symbol" in widget._error

    def test_get_order_params_no_quantity(self) -> None:
        widget = OrderFormWidget()
        widget.set_symbol("BTC-USD")
        params = widget.get_order_params()
        assert params is None
        assert "Quantity" in widget._error

    def test_get_order_params_invalid_quantity(self) -> None:
        widget = OrderFormWidget()
        widget.set_symbol("BTC-USD")
        widget.set_quantity("abc")
        params = widget.get_order_params()
        assert params is None
        assert "positive number" in widget._error

    def test_get_order_params_negative_quantity(self) -> None:
        widget = OrderFormWidget()
        widget.set_symbol("BTC-USD")
        widget.set_quantity("-1.0")
        params = widget.get_order_params()
        assert params is None
        assert "positive number" in widget._error

    def test_get_order_params_limit_no_price(self) -> None:
        widget = OrderFormWidget()
        widget.set_symbol("BTC-USD")
        widget.set_quantity("1.0")
        widget.toggle_type()  # LIMIT
        params = widget.get_order_params()
        assert params is None
        assert "Price" in widget._error

    def test_get_order_params_limit_invalid_price(self) -> None:
        widget = OrderFormWidget()
        widget.set_symbol("BTC-USD")
        widget.set_quantity("1.0")
        widget.set_price("abc")
        widget.toggle_type()  # LIMIT
        params = widget.get_order_params()
        assert params is None
        assert "positive number" in widget._error

    def test_show_success(self) -> None:
        widget = OrderFormWidget()
        widget.show_success("Order placed!")
        assert widget._success == "Order placed!"
        assert widget._error == ""

    def test_show_error(self) -> None:
        widget = OrderFormWidget()
        widget.show_error("Something failed")
        assert widget._error == "Something failed"
        assert widget._success == ""

    def test_render_contains_help_text(self) -> None:
        widget = OrderFormWidget()
        widget.set_symbol("BTC-USD")
        text = widget.render()
        assert "PLACE ORDER" in text.plain
        assert "BTC-USD" in text.plain
        assert "BUY" in text.plain
        assert "MARKET" in text.plain

    def test_render_shows_price_for_limit(self) -> None:
        widget = OrderFormWidget()
        widget.set_symbol("BTC-USD")
        widget.set_price("65000")
        widget.toggle_type()  # LIMIT
        text = widget.render()
        assert "Price:" in text.plain
        assert "65000" in text.plain

    def test_render_hides_price_for_market(self) -> None:
        widget = OrderFormWidget()
        widget.set_symbol("BTC-USD")
        text = widget.render()
        assert "Price:" not in text.plain


# ── OrderHistoryWidget Tests ─────────────────────────────────────────


class TestOrderHistoryWidget:
    """Test the order history widget rendering."""

    def test_init(self) -> None:
        widget = OrderHistoryWidget()
        assert widget.orders == []

    def test_render_empty(self) -> None:
        widget = OrderHistoryWidget()
        text = widget.render()
        assert "No orders placed" in text.plain

    def test_render_with_orders(self) -> None:
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
            {
                "order_id": "ghi789",
                "symbol": "SOL-USD",
                "side": "BUY",
                "order_type": "LIMIT",
                "quantity": 10.0,
                "price": 150.0,
                "fill_price": None,
                "status": "CANCELLED",
                "created_at": now - 120,
            },
        ]
        text = widget.render()
        assert "ORDER HISTORY" in text.plain
        assert "BTC-USD" in text.plain
        assert "ETH-USD" in text.plain
        assert "SOL-USD" in text.plain
        assert "FILLED" in text.plain
        assert "OPEN" in text.plain
        assert "CANCELLED" in text.plain

    def test_render_shows_fill_price_when_filled(self) -> None:
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
        text = widget.render()
        assert "65,000.00" in text.plain

    def test_render_shows_market_for_market_order(self) -> None:
        widget = OrderHistoryWidget()
        widget.orders = [
            {
                "order_id": "abc",
                "symbol": "BTC-USD",
                "side": "BUY",
                "order_type": "MARKET",
                "quantity": 1.0,
                "price": 0,
                "fill_price": None,
                "status": "OPEN",
                "created_at": time.time(),
            },
        ]
        text = widget.render()
        assert "market" in text.plain


# ── PaperScreen Tests ────────────────────────────────────────────────


class TestPaperScreen:
    """Test the PaperScreen class structure and configuration."""

    def test_screen_class_exists(self) -> None:
        assert PaperScreen is not None

    def test_screen_has_bindings(self) -> None:
        binding_keys = [b.key for b in PaperScreen.BINDINGS]
        assert "escape" in binding_keys
        assert "r" in binding_keys
        assert "enter" in binding_keys

    def test_screen_has_reactive_state(self) -> None:
        assert hasattr(PaperScreen, "session_id")
        assert hasattr(PaperScreen, "session_name")
        assert hasattr(PaperScreen, "status_text")
        assert hasattr(PaperScreen, "is_loading")

    def test_screen_has_compose(self) -> None:
        assert hasattr(PaperScreen, "compose")

    def test_screen_has_mount_handler(self) -> None:
        assert hasattr(PaperScreen, "on_mount")

    def test_screen_has_refresh_action(self) -> None:
        assert hasattr(PaperScreen, "action_refresh_now")

    def test_screen_has_place_order(self) -> None:
        assert hasattr(PaperScreen, "_place_order")

    def test_screen_has_init_session(self) -> None:
        assert hasattr(PaperScreen, "_init_session")

    def test_screen_init_defaults(self) -> None:
        screen = PaperScreen()
        assert screen.session_id == ""
        assert screen.session_name == ""
        assert screen._pnl_history == []
        assert screen._mark_prices == {}


# ── TextInputScreen Tests ────────────────────────────────────────────


class TestTextInputScreen:
    """Test the text input modal screen."""

    def test_init(self) -> None:
        screen = _TextInputScreen("symbol", "Enter symbol:")
        assert screen._field == "symbol"
        assert screen._prompt == "Enter symbol:"

    def test_has_bindings(self) -> None:
        binding_keys = [b.key for b in _TextInputScreen.BINDINGS]
        assert "escape" in binding_keys


# ── Integration Tests ────────────────────────────────────────────────


class TestPaperScreenIntegration:
    """Test PaperScreen integration with CLI bridge."""

    @pytest.mark.asyncio
    async def test_init_session_success(self) -> None:
        """Test that _init_session creates a session via CLI bridge."""
        screen = PaperScreen()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '{"session_id": "test123", "name": "tui-session"}'
        mock_result.stderr = ""

        with patch("siglab.tui.screens.paper.run_cli", new_callable=AsyncMock, return_value=mock_result):
            # Mock query_one to avoid widget tree issues
            screen.query_one = MagicMock(side_effect=Exception("not mounted"))
            await screen._init_session()
            assert screen.session_id == "test123"
            assert screen.session_name == "tui-session"

    @pytest.mark.asyncio
    async def test_init_session_failure(self) -> None:
        """Test that _init_session handles CLI failures gracefully."""
        screen = PaperScreen()
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "Session creation failed"

        with patch("siglab.tui.screens.paper.run_cli", new_callable=AsyncMock, return_value=mock_result):
            screen.query_one = MagicMock(side_effect=Exception("not mounted"))
            await screen._init_session()
            assert screen.session_id == ""
            assert "error" in screen.status_text.lower() or "failed" in screen.status_text.lower()

    @pytest.mark.asyncio
    async def test_refresh_all_no_session(self) -> None:
        """Test that _refresh_all is a no-op when no session is active."""
        screen = PaperScreen()
        # Should not raise
        await screen._refresh_all()

    @pytest.mark.asyncio
    async def test_refresh_all_success(self) -> None:
        """Test that _refresh_all updates widgets from CLI data."""
        screen = PaperScreen()
        screen.session_id = "test123"
        screen.session_name = "test"

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '{"session_id": "test123", "name": "test", "position": [], "pnl": {"realized_pnl": 0, "unrealized_pnl": 0, "total_pnl": 0, "total_funding_cost": 0, "open_position_count": 0}, "orders": []}'
        mock_result.stderr = ""

        # Mock widget queries
        mock_positions = MagicMock()
        mock_orders = MagicMock()
        mock_account = MagicMock()
        mock_chart = MagicMock()

        def mock_query(selector, cls=None):
            if selector == "#positions-table":
                return mock_positions
            elif selector == "#order-history":
                return mock_orders
            elif selector == "#account-summary":
                return mock_account
            elif selector == "#pnl-chart":
                return mock_chart
            raise Exception(f"Unknown selector: {selector}")

        screen.query_one = mock_query

        with patch("siglab.tui.screens.paper.run_cli", new_callable=AsyncMock, return_value=mock_result):
            await screen._refresh_all()
            assert "updated" in screen.status_text.lower() or "test123" in screen.status_text


# ── CSS Integration Tests ────────────────────────────────────────────


class TestPaperScreenCSS:
    """Test that paper screen CSS rules exist."""

    def test_app_tcss_has_paper_styles(self) -> None:
        from pathlib import Path

        tcss_path = Path(__file__).resolve().parents[1] / "siglab" / "tui" / "styles" / "app.tcss"
        content = tcss_path.read_text()
        assert "PaperScreen" in content
        assert "#paper-layout" in content
        assert "#paper-main" in content
        assert "#paper-left" in content
        assert "#paper-right" in content
        assert "#order-form" in content
        assert "#account-summary" in content
        assert "#pnl-chart" in content
        assert "#positions-table" in content
        assert "#order-history" in content
        assert "#paper-status" in content

    def test_app_tcss_paper_left_width(self) -> None:
        from pathlib import Path

        tcss_path = Path(__file__).resolve().parents[1] / "siglab" / "tui" / "styles" / "app.tcss"
        content = tcss_path.read_text()
        # Left column should be fixed width
        assert "width: 40" in content


# ── Module Export Tests ──────────────────────────────────────────────


class TestPaperModuleExports:
    """Test that paper screen is properly exported."""

    def test_screens_init_exports_paper_screen(self) -> None:
        from siglab.tui.screens import PaperScreen as Exported

        assert Exported is PaperScreen

    def test_paper_screen_in_app_screens(self) -> None:
        from siglab.tui.app import SigLabTUI

        assert "paper" in SigLabTUI.SCREENS
