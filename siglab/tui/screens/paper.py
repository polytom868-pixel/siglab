"""Paper Trading TUI screen for SigLab.

Displays:
- Positions table with symbol, size, entry price, mark price, unrealized PnL
- Order form supporting MARKET and LIMIT order types
- Order history showing all orders with status
- PnL sparkline chart showing performance over time

Connects to CLI bridge for paper-start/status commands.
Auto-refreshes positions and PnL on a timer.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Input, Static

from siglab.tui.cli_bridge import run_cli
from siglab.tui.widgets.sparkline import sparkline_text

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────

REFRESH_SECONDS = 15.0
PNL_HISTORY_MAX = 120  # Max PnL data points for sparkline

# ── Formatting helpers ───────────────────────────────────────────────


def _fmt_price(price: float) -> str:
    """Format a price with appropriate decimal places."""
    if price >= 1000:
        return f"{price:,.2f}"
    elif price >= 1:
        return f"{price:,.4f}"
    else:
        return f"{price:,.6f}"


def _fmt_pnl(pnl: float) -> Text:
    """Format PnL as coloured Rich Text."""
    if pnl > 0:
        return Text(f"+{pnl:,.2f}", style="#4ade80")
    elif pnl < 0:
        return Text(f"{pnl:,.2f}", style="#f87171")
    else:
        return Text(f"{pnl:,.2f}", style="#7d9483")


def _fmt_qty(qty: float) -> str:
    """Format quantity compactly."""
    if abs(qty) >= 1_000_000:
        return f"{qty / 1_000_000:.2f}M"
    elif abs(qty) >= 1_000:
        return f"{qty / 1_000:.2f}K"
    else:
        return f"{qty:,.4f}"


def _status_style(status: str) -> str:
    """Return a Rich style for an order status."""
    s = status.upper()
    if s == "FILLED":
        return "#4ade80"
    elif s == "OPEN":
        return "#60a5fa"
    elif s == "CANCELLED":
        return "#f0b456"
    elif s == "EXPIRED":
        return "#7d9483"
    return "#a3b5a8"


def _side_style(side: str) -> str:
    """Return a Rich style for BUY/SELL."""
    return "#4ade80" if side.upper() == "BUY" else "#f87171"


# ══════════════════════════════════════════════════════════════════════
# Positions Table Widget
# ══════════════════════════════════════════════════════════════════════


class PositionsTableWidget(Static):
    """Displays open positions with symbol, size, entry, mark, and PnL."""

    positions: reactive[list[dict[str, Any]]] = reactive(list, layout=True)
    mark_prices: reactive[dict[str, float]] = reactive(dict, layout=True)

    DEFAULT_CSS = """
    PositionsTableWidget {
        height: 1fr;
        min-height: 6;
        padding: 0 1;
        overflow-y: auto;
        background: $surface;
    }
    """

    def render(self) -> Text:
        result = Text()
        result.append(" POSITIONS\n", style="bold #e2ebe5")

        if not self.positions:
            result.append("  No open positions\n", style="#7d9483")
            return result

        # Header
        result.append(
            "  SYMBOL          SIZE         ENTRY        MARK       UNREAL PnL\n",
            style="#7d9483",
        )
        result.append("  " + "─" * 68 + "\n", style="#2a3a30")

        for pos in self.positions:
            sym = str(pos.get("symbol", "?"))
            qty = float(pos.get("quantity", 0))
            entry = float(pos.get("entry_price", 0))
            mark = self.mark_prices.get(sym, entry)
            unrealized = float(pos.get("unrealized_pnl", 0))
            # Compute unrealized PnL if we have mark price
            if mark > 0 and entry > 0 and qty != 0:
                if qty > 0:
                    unrealized = qty * (mark - entry)
                else:
                    unrealized = abs(qty) * (entry - mark)

            result.append(f"  {sym:<16}", style="#a3b5a8")
            result.append(f"{_fmt_qty(qty):>12}  ", style="#e2ebe5")
            result.append(f"{_fmt_price(entry):>12}  ", style="#e2ebe5")
            result.append(f"{_fmt_price(mark):>12}  ", style="#60a5fa")
            result.append_text(_fmt_pnl(unrealized))
            result.append("\n")

        return result


# ══════════════════════════════════════════════════════════════════════
# Account Summary Widget
# ══════════════════════════════════════════════════════════════════════


class AccountSummaryWidget(Static):
    """Shows session PnL summary and account stats."""

    pnl_data: reactive[dict[str, Any]] = reactive(dict, layout=True)
    session_name: reactive[str] = reactive("", layout=True)

    DEFAULT_CSS = """
    AccountSummaryWidget {
        height: auto;
        min-height: 5;
        padding: 0 1;
        background: $surface;
    }
    """

    def render(self) -> Text:
        result = Text()
        name = self.session_name or "No session"
        result.append(f" SESSION: {name}\n", style="bold #4ade80")

        if not self.pnl_data:
            result.append("  No PnL data\n", style="#7d9483")
            return result

        realized = float(self.pnl_data.get("realized_pnl", 0))
        unrealized = float(self.pnl_data.get("unrealized_pnl", 0))
        total = float(self.pnl_data.get("total_pnl", 0))
        funding = float(self.pnl_data.get("total_funding_cost", 0))
        open_count = int(self.pnl_data.get("open_position_count", 0))

        result.append("  Realized:    ", style="#7d9483")
        result.append_text(_fmt_pnl(realized))
        result.append("\n")

        result.append("  Unrealized:  ", style="#7d9483")
        result.append_text(_fmt_pnl(unrealized))
        result.append("\n")

        result.append("  Total PnL:   ", style="#7d9483")
        result.append_text(_fmt_pnl(total))
        result.append("\n")

        result.append("  Funding:     ", style="#7d9483")
        result.append_text(_fmt_pnl(funding))
        result.append(f"   Open: {open_count}\n", style="#7d9483")

        return result


# ══════════════════════════════════════════════════════════════════════
# PnL Sparkline Chart Widget
# ══════════════════════════════════════════════════════════════════════


class PnlChartWidget(Static):
    """Renders a sparkline chart of PnL over time."""

    pnl_history: reactive[list[float]] = reactive(list, layout=True)

    DEFAULT_CSS = """
    PnlChartWidget {
        height: auto;
        min-height: 5;
        padding: 0 1;
        background: $bg;
    }
    """

    def render(self) -> Text:
        result = Text()
        result.append(" PnL PERFORMANCE\n", style="bold #e2ebe5")

        if not self.pnl_history:
            result.append("  Collecting data…\n", style="#7d9483")
            return result

        spark = sparkline_text(self.pnl_history, width=50)
        result.append("  ")
        result.append_text(spark)
        result.append("\n")

        # Show min/max/current
        lo = min(self.pnl_history)
        hi = max(self.pnl_history)
        cur = self.pnl_history[-1]
        result.append("  Low: ", style="#7d9483")
        result.append_text(_fmt_pnl(lo))
        result.append("  High: ", style="#7d9483")
        result.append_text(_fmt_pnl(hi))
        result.append("  Now: ", style="#7d9483")
        result.append_text(_fmt_pnl(cur))
        result.append("\n")

        return result


# ══════════════════════════════════════════════════════════════════════
# Order Form Widget
# ══════════════════════════════════════════════════════════════════════


class OrderFormWidget(Static):
    """Order entry form for MARKET and LIMIT orders.

    Layout:
    - Symbol input
    - Side toggle (BUY/SELL)
    - Order type select (MARKET/LIMIT)
    - Quantity input
    - Price input (only for LIMIT)
    - Submit button (Enter)
    """

    DEFAULT_CSS = """
    OrderFormWidget {
        height: auto;
        min-height: 14;
        padding: 0 1;
        background: $surface;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("enter", "submit_order", "Submit Order", show=False),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._side: str = "BUY"
        self._order_type: str = "MARKET"
        self._symbol: str = ""
        self._quantity: str = ""
        self._price: str = ""
        self._error: str = ""
        self._success: str = ""

    def render(self) -> Text:
        result = Text()
        result.append(" PLACE ORDER\n", style="bold #e2ebe5")

        # Symbol
        sym_display = self._symbol or "—"
        result.append(f"  Symbol:  {sym_display}\n", style="#a3b5a8")

        # Side toggle
        buy_style = "bold #000000 on #4ade80" if self._side == "BUY" else "#4ade80"
        sell_style = "bold #000000 on #f87171" if self._side == "SELL" else "#f87171"
        result.append("  Side:    ")
        result.append(" BUY ", style=buy_style)
        result.append(" ")
        result.append(" SELL ", style=sell_style)
        result.append("\n")

        # Order type
        mkt_style = "bold #000000 on #60a5fa" if self._order_type == "MARKET" else "#60a5fa"
        lmt_style = "bold #000000 on #60a5fa" if self._order_type == "LIMIT" else "#60a5fa"
        result.append("  Type:    ")
        result.append(" MARKET ", style=mkt_style)
        result.append(" ")
        result.append(" LIMIT ", style=lmt_style)
        result.append("\n")

        # Quantity
        qty_display = self._quantity or "—"
        result.append(f"  Qty:     {qty_display}\n", style="#a3b5a8")

        # Price (only for LIMIT)
        if self._order_type == "LIMIT":
            price_display = self._price or "—"
            result.append(f"  Price:   {price_display}\n", style="#a3b5a8")

        # Error / success messages
        if self._error:
            result.append(f"  ✗ {self._error}\n", style="#f87171")
        if self._success:
            result.append(f"  ✓ {self._success}\n", style="#4ade80")

        # Help text
        result.append("\n", style="")
        result.append(
            "  [s]ymbol [b]uy/sell [t]ype [Q]ty [p]rice [Enter]submit\n",
            style="#7d9483",
        )

        return result

    def set_symbol(self, symbol: str) -> None:
        self._symbol = symbol.upper().strip()
        self._error = ""
        self.refresh(layout=True)

    def set_quantity(self, qty: str) -> None:
        self._quantity = qty.strip()
        self._error = ""
        self.refresh(layout=True)

    def set_price(self, price: str) -> None:
        self._price = price.strip()
        self._error = ""
        self.refresh(layout=True)

    def toggle_side(self) -> None:
        self._side = "SELL" if self._side == "BUY" else "BUY"
        self._error = ""
        self.refresh(layout=True)

    def toggle_type(self) -> None:
        self._order_type = "LIMIT" if self._order_type == "MARKET" else "MARKET"
        self._error = ""
        self.refresh(layout=True)

    def get_order_params(self) -> dict[str, str] | None:
        """Validate and return order parameters, or None with error set."""
        self._error = ""
        self._success = ""

        if not self._symbol:
            self._error = "Symbol is required"
            self.refresh(layout=True)
            return None
        if not self._quantity:
            self._error = "Quantity is required"
            self.refresh(layout=True)
            return None
        try:
            qty = float(self._quantity)
            if qty <= 0:
                raise ValueError
        except (ValueError, TypeError):
            self._error = "Quantity must be a positive number"
            self.refresh(layout=True)
            return None

        if self._order_type == "LIMIT":
            if not self._price:
                self._error = "Price is required for LIMIT orders"
                self.refresh(layout=True)
                return None
            try:
                p = float(self._price)
                if p <= 0:
                    raise ValueError
            except (ValueError, TypeError):
                self._error = "Price must be a positive number"
                self.refresh(layout=True)
                return None

        params: dict[str, str] = {
            "symbol": self._symbol,
            "side": self._side,
            "order_type": self._order_type,
            "quantity": self._quantity,
        }
        if self._order_type == "LIMIT":
            params["price"] = self._price
        return params

    def show_success(self, msg: str) -> None:
        self._error = ""
        self._success = msg
        self.refresh(layout=True)

    def show_error(self, msg: str) -> None:
        self._success = ""
        self._error = msg
        self.refresh(layout=True)


# ══════════════════════════════════════════════════════════════════════
# Order History Widget
# ══════════════════════════════════════════════════════════════════════


class OrderHistoryWidget(Static):
    """Displays the full order history with status, type, and fill info."""

    orders: reactive[list[dict[str, Any]]] = reactive(list, layout=True)

    DEFAULT_CSS = """
    OrderHistoryWidget {
        height: 1fr;
        min-height: 6;
        padding: 0 1;
        overflow-y: auto;
        background: $bg;
    }
    """

    def render(self) -> Text:
        result = Text()
        result.append(" ORDER HISTORY\n", style="bold #e2ebe5")

        if not self.orders:
            result.append("  No orders placed\n", style="#7d9483")
            return result

        # Header
        result.append(
            "  TIME        SIDE  TYPE    SYMBOL        QTY       PRICE    STATUS\n",
            style="#7d9483",
        )
        result.append("  " + "─" * 72 + "\n", style="#2a3a30")

        for order in self.orders[:50]:  # Show last 50
            created = float(order.get("created_at", 0))
            ts_str = time.strftime("%H:%M:%S", time.localtime(created))
            side = str(order.get("side", "?"))
            otype = str(order.get("order_type", "?"))
            sym = str(order.get("symbol", "?"))
            qty = float(order.get("quantity", 0))
            price = float(order.get("price", 0))
            fill_price = order.get("fill_price")
            status = str(order.get("status", "?"))

            result.append(f"  {ts_str:<11}", style="#7d9483")
            result.append(f" {side:<5}", style=_side_style(side))
            result.append(f" {otype:<7}", style="#a3b5a8")
            result.append(f" {sym:<13}", style="#e2ebe5")
            result.append(f" {_fmt_qty(qty):>9}", style="#e2ebe5")

            if fill_price is not None:
                result.append(f" {_fmt_price(float(fill_price)):>9}", style="#4ade80")
            elif price > 0:
                result.append(f" {_fmt_price(price):>9}", style="#a3b5a8")
            else:
                result.append(f" {'market':>9}", style="#7d9483")

            result.append(f" {status}", style=_status_style(status))
            result.append("\n")

        return result


# ══════════════════════════════════════════════════════════════════════
# Paper Trading Screen
# ══════════════════════════════════════════════════════════════════════


class PaperScreen(Screen[None]):
    """Paper trading screen with positions, order form, history, and PnL chart.

    Layout:
    ┌──────────────────────────────────────────────┐
    │  Left column (38w)    │  Right column (fluid) │
    │  ┌────────────────┐   │  ┌──────────────────┐ │
    │  │ Order Form     │   │  │ PnL Sparkline    │ │
    │  ├────────────────┤   │  ├──────────────────┤ │
    │  │ Account Summary│   │  │ Positions Table  │ │
    │  └────────────────┘   │  ├──────────────────┤ │
    │                       │  │ Order History    │ │
    │                       │  └──────────────────┘ │
    │  Status bar                                    │
    └───────────────────────────────────────────────┘
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "go_back", "Back", show=True),
        Binding("r", "refresh_now", "Refresh", show=True),
        Binding("s", "focus_symbol", "Symbol", show=True),
        Binding("b", "toggle_side", "Buy/Sell", show=True),
        Binding("t", "toggle_type", "Type", show=True),
        Binding("Q", "focus_qty", "Qty", show=True),
        Binding("p", "focus_price", "Price", show=True),
        Binding("enter", "submit_order", "Submit", show=True),
        Binding("n", "new_session", "New Session", show=True),
        Binding("c", "cancel_order", "Cancel Order", show=True),
    ]

    # Reactive state
    session_id: reactive[str] = reactive("")
    session_name: reactive[str] = reactive("")
    status_text: reactive[str] = reactive("Initializing…")
    is_loading: reactive[bool] = reactive(True)

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._pnl_history: list[float] = []
        self._mark_prices: dict[str, float] = {}
        self._current_focus: str = "form"  # form, positions, history
        self._selected_order_idx: int = 0
        self._chart_width: int = 50

    def compose(self) -> ComposeResult:
        with Vertical(id="paper-layout"):
            with Horizontal(id="paper-main"):
                # Left column: order form + account summary
                with Vertical(id="paper-left"):
                    yield OrderFormWidget(id="order-form")
                    yield AccountSummaryWidget(id="account-summary")
                # Right column: PnL chart, positions, order history
                with Vertical(id="paper-right"):
                    yield PnlChartWidget(id="pnl-chart")
                    yield PositionsTableWidget(id="positions-table")
                    yield OrderHistoryWidget(id="order-history")
            # Status line
            yield Static(self.status_text, id="paper-status")

    def on_mount(self) -> None:
        """Initialize the screen — create or load a session."""
        self._timer = self.set_interval(REFRESH_SECONDS, self._refresh_all)
        self.call_after_refresh(self._init_session)

    def on_unmount(self) -> None:
        """Clean up the refresh timer when leaving the screen."""
        if hasattr(self, "_timer"):
            self._timer.stop()

    # ── Session Management ────────────────────────────────────────────

    async def _init_session(self) -> None:
        """Create a new paper session or load existing one."""
        self.status_text = "Creating paper session…"
        self.is_loading = True
        try:
            result = await run_cli(
                "paper-start", "--session", "tui-session"
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                self.session_id = data.get("session_id", "")
                self.session_name = data.get("name", "tui-session")
                self.status_text = f"Session {self.session_id[:8]}… ready"
                self.is_loading = False
                # Update form widget with default symbol
                try:
                    form = self.query_one("#order-form", OrderFormWidget)
                    form.set_symbol("BTC-USD")
                except Exception:
                    pass
                # Initial data fetch
                await self._refresh_all()
            else:
                self.status_text = f"Session error: {result.stderr[:80]}"
                self.is_loading = False
                logger.warning("paper-start failed: %s", result.stderr)
        except Exception as exc:
            self.status_text = f"Init error: {exc}"
            self.is_loading = False
            logger.warning("Session init failed: %s", exc)

    # ── Data Fetching ─────────────────────────────────────────────────

    async def _refresh_all(self) -> None:
        """Fetch all session data and update widgets."""
        if not self.session_id:
            return
        self.is_loading = True
        try:
            result = await run_cli(
                "paper-status", "--session", self.session_id
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                self._update_positions(data.get("position", []))
                self._update_orders(data.get("orders", []))
                self._update_pnl(data.get("pnl", {}))
                self.status_text = f"Session {self.session_id[:8]}… · updated"
            else:
                self.status_text = f"Refresh error: {result.stderr[:60]}"
                logger.warning("paper-status failed: %s", result.stderr)
        except Exception as exc:
            self.status_text = f"Refresh error: {exc}"
            logger.warning("Refresh failed: %s", exc)
        finally:
            self.is_loading = False

    def _update_positions(self, positions: list[dict[str, Any]]) -> None:
        """Update the positions table widget."""
        try:
            widget = self.query_one("#positions-table", PositionsTableWidget)
            widget.positions = positions
            widget.mark_prices = dict(self._mark_prices)
        except Exception:
            pass

    def _update_orders(self, orders: list[dict[str, Any]]) -> None:
        """Update the order history widget."""
        try:
            widget = self.query_one("#order-history", OrderHistoryWidget)
            widget.orders = orders
        except Exception:
            pass

    def _update_pnl(self, pnl_data: dict[str, Any]) -> None:
        """Update PnL summary and sparkline history."""
        # Update account summary
        try:
            widget = self.query_one("#account-summary", AccountSummaryWidget)
            widget.pnl_data = pnl_data
            widget.session_name = self.session_name
        except Exception:
            pass

        # Track PnL history for sparkline
        total_pnl = float(pnl_data.get("total_pnl", 0))
        self._pnl_history.append(total_pnl)
        if len(self._pnl_history) > PNL_HISTORY_MAX:
            self._pnl_history = self._pnl_history[-PNL_HISTORY_MAX:]

        # Update sparkline
        try:
            chart = self.query_one("#pnl-chart", PnlChartWidget)
            chart.pnl_history = list(self._pnl_history)
        except Exception:
            pass

    # ── Order Placement ───────────────────────────────────────────────

    async def _place_order(self, params: dict[str, str]) -> None:
        """Place a paper order via Python subprocess calling paper_client directly."""
        if not self.session_id:
            try:
                form = self.query_one("#order-form", OrderFormWidget)
                form.show_error("No active session")
            except Exception:
                pass
            return

        try:
            import asyncio as _asyncio
            import sys

            # Serialize parameters as JSON to avoid injection risk
            order_json = json.dumps({
                "session_id": self.session_id,
                "symbol": params["symbol"],
                "side": params["side"],
                "quantity": float(params["quantity"]),
                "order_type": params["order_type"],
                "price": float(params["price"]) if "price" in params else None,
            })
            # Use JSON stdin pattern to safely pass parameters
            code = (
                "import json, sys; "
                "from siglab.config import load_settings; "
                "from siglab.live.paper_client import SoDEXPaperPerpsClient; "
                "p = json.loads(sys.stdin.read()); "
                "s = load_settings(); "
                "c = SoDEXPaperPerpsClient(sessions_dir=str(s.root_dir / 'sessions')); "
                "kwargs = {k: v for k, v in p.items() if k != 'session_id' and v is not None}; "
                "r = c.place_order(session_id=p['session_id'], **kwargs); "
                "print(json.dumps(r))"
            )
            proc = await _asyncio.create_subprocess_exec(
                sys.executable, "-c", code,
                stdin=_asyncio.subprocess.PIPE,
                stdout=_asyncio.subprocess.PIPE,
                stderr=_asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await _asyncio.wait_for(
                proc.communicate(input=order_json.encode("utf-8")),
                timeout=15.0,
            )
            stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
            stderr = stderr_bytes.decode("utf-8", errors="replace").strip()

            if proc.returncode == 0 and stdout:
                order_data = json.loads(stdout.split("\n")[-1])
                order_id = order_data.get("order_id", "?")[:8]
                try:
                    form = self.query_one("#order-form", OrderFormWidget)
                    form.show_success(
                        f"Order {order_id}… {params['side']} {params['quantity']} {params['symbol']}"
                    )
                except Exception:
                    pass
                # Refresh to show new order
                await self._refresh_all()
            else:
                error_msg = stderr or "Order placement failed"
                try:
                    form = self.query_one("#order-form", OrderFormWidget)
                    form.show_error(error_msg[:80])
                except Exception:
                    pass
                logger.warning("Order placement failed: %s", stderr)

        except Exception as exc:
            try:
                form = self.query_one("#order-form", OrderFormWidget)
                form.show_error(str(exc)[:80])
            except Exception:
                pass
            logger.warning("Order placement error: %s", exc)

    # ── Input Handling ────────────────────────────────────────────────

    def on_key(self, event) -> None:
        """Route key events based on current focus context."""
        key = event.key

        # Symbol input mode
        if self._current_focus == "symbol_input":
            if key == "escape":
                self._current_focus = "form"
                event.prevent_default()
            return

        # Quantity input mode
        if self._current_focus == "qty_input":
            if key == "escape":
                self._current_focus = "form"
                event.prevent_default()
            return

        # Price input mode
        if self._current_focus == "price_input":
            if key == "escape":
                self._current_focus = "form"
                event.prevent_default()
            return

    def action_focus_symbol(self) -> None:
        """Focus symbol input for editing."""
        self._push_text_input("symbol", "Enter symbol (e.g. BTC-USD):")

    def action_toggle_side(self) -> None:
        """Toggle BUY/SELL side."""
        try:
            form = self.query_one("#order-form", OrderFormWidget)
            form.toggle_side()
        except Exception:
            pass

    def action_toggle_type(self) -> None:
        """Toggle MARKET/LIMIT order type."""
        try:
            form = self.query_one("#order-form", OrderFormWidget)
            form.toggle_type()
        except Exception:
            pass

    def action_focus_qty(self) -> None:
        """Focus quantity input."""
        self._push_text_input("quantity", "Enter quantity:")

    def action_focus_price(self) -> None:
        """Focus price input."""
        self._push_text_input("price", "Enter limit price:")

    async def action_submit_order(self) -> None:
        """Validate and submit the current order."""
        try:
            form = self.query_one("#order-form", OrderFormWidget)
            params = form.get_order_params()
            if params:
                await self._place_order(params)
        except Exception as exc:
            logger.warning("Submit order failed: %s", exc)

    def action_new_session(self) -> None:
        """Create a new paper trading session."""
        self.call_after_refresh(self._init_session)

    def action_cancel_order(self) -> None:
        """Cancel the selected open order (placeholder for future enhancement)."""
        self.status_text = "Cancel: select an open order first (coming soon)"

    def action_go_back(self) -> None:
        """Return to the main screen."""
        self.app.pop_screen()

    def action_refresh_now(self) -> None:
        """Force an immediate data refresh."""
        self.call_after_refresh(self._refresh_all)

    # ── Text Input Overlay ────────────────────────────────────────────

    def _push_text_input(self, field: str, prompt: str) -> None:
        """Push a modal text input screen for the given field."""
        self.app.push_screen(
            _TextInputScreen(field, prompt),
            callback=self._on_text_input_result,
        )

    def _on_text_input_result(self, result: tuple[str, str] | None) -> None:
        """Handle text input result from modal."""
        if result is None:
            return
        field, value = result
        try:
            form = self.query_one("#order-form", OrderFormWidget)
            if field == "symbol":
                form.set_symbol(value)
            elif field == "quantity":
                form.set_quantity(value)
            elif field == "price":
                form.set_price(value)
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════
# Text Input Modal Screen
# ══════════════════════════════════════════════════════════════════════


class _TextInputScreen(Screen[tuple[str, str] | None]):
    """A minimal modal text input for entering values."""

    DEFAULT_CSS = """
    _TextInputScreen {
        align: center middle;
        background: rgba(0, 0, 0, 0.85);
    }
    #text-input-dialog {
        width: 50;
        height: auto;
        padding: 1 2;
        background: #0d1210;
        border: solid #2a3a30;
    }
    #text-input-prompt {
        color: #4ade80;
        text-style: bold;
        margin: 0 0 1 0;
    }
    #text-input-field {
        background: #1a2a1f;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "dismiss", "Cancel"),
    ]

    def __init__(self, field: str, prompt: str) -> None:
        super().__init__()
        self._field = field
        self._prompt = prompt

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(self._prompt, id="text-input-prompt"),
            Input(id="text-input-field"),
            id="text-input-dialog",
        )

    def on_mount(self) -> None:
        self.query_one("#text-input-field", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        if value:
            self.dismiss((self._field, value))
        else:
            self.dismiss(None)
