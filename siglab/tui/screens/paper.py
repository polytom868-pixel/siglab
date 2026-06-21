"""Paper Trading TUI screen for SigLab."""
from __future__ import annotations
import logging
import time
from typing import Any, ClassVar, cast
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Input, Static
from siglab.tui.formatting import ACCENT_GREEN, BORDER_DIM, COMPACT_CSS, ERROR_RED, EXPANDABLE_CSS, INFO_BLUE, TEXT_MUTED, TEXT_PRIMARY, TEXT_SECONDARY, compact_qty, format_pnl, format_price, order_status_style, safe_query, side_style, sanitize_status_text, truncate
from siglab.tui.loading import LoadingIndicator
from siglab.tui.screens.base import BaseScreen
from siglab.tui.api_client import TuiApiClient
from siglab.tui.widgets.sparkline import sparkline_text
logger = logging.getLogger(__name__)
PNL_HISTORY_MAX = 120

class PositionsTableWidget(Static):
    """Displays open positions with symbol, size, entry, mark, and PnL."""
    positions: reactive[list[dict[str, Any]]] = reactive(list, layout=True)
    mark_prices: reactive[dict[str, float]] = reactive(dict, layout=True)
    DEFAULT_CSS = f'PositionsTableWidget {{ {EXPANDABLE_CSS} }}'

    def render(self) -> Text:
        result = Text()
        result.append(' POSITIONS\n', style=f'bold {TEXT_PRIMARY}')
        if not self.positions:
            result.append('  No open positions\n', style=TEXT_MUTED)
            return result
        avail = getattr(self.size, 'width', 120) or 120
        show_mark = avail >= 70
        if show_mark:
            result.append('  SYMBOL          SIZE         ENTRY        MARK       UNREAL PnL\n', style=TEXT_MUTED)
            result.append('  ' + '─' * 68 + '\n', style=BORDER_DIM)
        else:
            result.append('  SYMBOL        SIZE       ENTRY      UNREAL PnL\n', style=TEXT_MUTED)
            result.append('  ' + '─' * 50 + '\n', style=BORDER_DIM)
        for pos in self.positions:
            sym = str(pos.get('symbol', '?'))
            qty = float(pos.get('quantity', 0))
            entry = float(pos.get('entry_price', 0))
            mark = self.mark_prices.get(sym, entry)
            unrealized = float(pos.get('unrealized_pnl', 0))
            if mark > 0 and entry > 0 and (qty != 0):
                if qty > 0:
                    unrealized = qty * (mark - entry)
                else:
                    unrealized = abs(qty) * (entry - mark)
            if show_mark:
                result.append(f'  {sym:<16}', style=TEXT_SECONDARY)
                result.append(f'{format_price(qty):>12}  ', style=TEXT_PRIMARY)
                result.append(f'{format_price(entry):>12}  ', style=TEXT_PRIMARY)
                result.append(f'{format_price(mark):>12}  ', style=INFO_BLUE)
                result.append_text(format_pnl(unrealized))
            else:
                result.append(f'  {truncate(sym, 14):<14}', style=TEXT_SECONDARY)
                result.append(f'{format_price(qty):>10}  ', style=TEXT_PRIMARY)
                result.append(f'{format_price(entry):>10}  ', style=TEXT_PRIMARY)
                result.append_text(format_pnl(unrealized))
            result.append('\n')
        return result

class AccountSummaryWidget(Static):
    """Shows session PnL summary and account stats."""
    pnl_data: reactive[dict[str, Any]] = reactive(dict, layout=True)
    session_name: reactive[str] = reactive('', layout=True)
    DEFAULT_CSS = f'AccountSummaryWidget {{ {COMPACT_CSS} }}'

    def render(self) -> Text:
        result = Text()
        name = self.session_name or 'No session'
        result.append(f' SESSION: {name}\n', style=f'bold {ACCENT_GREEN}')
        if not self.pnl_data:
            result.append('  No PnL data\n', style=TEXT_MUTED)
            return result
        realized = float(self.pnl_data.get('realized_pnl', 0))
        unrealized = float(self.pnl_data.get('unrealized_pnl', 0))
        total = float(self.pnl_data.get('total_pnl', 0))
        funding = float(self.pnl_data.get('total_funding_cost', 0))
        open_count = int(self.pnl_data.get('open_position_count', 0))
        result.append('  Realized:    ', style=TEXT_MUTED)
        result.append_text(format_pnl(realized))
        result.append('\n')
        result.append('  Unrealized:  ', style=TEXT_MUTED)
        result.append_text(format_pnl(unrealized))
        result.append('\n')
        result.append('  Total PnL:   ', style=TEXT_MUTED)
        result.append_text(format_pnl(total))
        result.append('\n')
        result.append('  Funding:     ', style=TEXT_MUTED)
        result.append_text(format_pnl(funding))
        result.append(f'   Open: {open_count}\n', style=TEXT_MUTED)
        return result

class PnlChartWidget(Static):
    """Renders a sparkline chart of PnL over time."""
    pnl_history: reactive[list[float]] = reactive(list, layout=True)
    DEFAULT_CSS = 'PnlChartWidget { height: auto; min-height: 5; padding: 0 1; background: #0a0a0a; }'

    def render(self) -> Text:
        result = Text()
        result.append(' PnL PERFORMANCE\n', style=f'bold {TEXT_PRIMARY}')
        if not self.pnl_history:
            result.append('  Collecting data…\n', style=TEXT_MUTED)
            return result
        spark = sparkline_text(self.pnl_history, width=50)
        result.append('  ')
        result.append_text(spark)
        result.append('\n')
        lo = min(self.pnl_history)
        hi = max(self.pnl_history)
        cur = self.pnl_history[-1]
        result.append('  Low: ', style=TEXT_MUTED)
        result.append_text(format_pnl(lo))
        result.append('  High: ', style=TEXT_MUTED)
        result.append_text(format_pnl(hi))
        result.append('  Now: ', style=TEXT_MUTED)
        result.append_text(format_pnl(cur))
        result.append('\n')
        return result

class OrderFormWidget(Static):
    """Order entry form for MARKET and LIMIT orders."""
    DEFAULT_CSS = 'OrderFormWidget { height: auto; min-height: 14; padding: 0 1; background: #0d1210; }'
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [Binding('enter', 'submit_order', 'Submit Order', show=False)]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._side: str = 'BUY'
        self._order_type: str = 'MARKET'
        self._symbol: str = ''
        self._quantity: str = ''
        self._price: str = ''
        self._error: str = ''
        self._success: str = ''

    def render(self) -> Text:
        result = Text()
        result.append(' PLACE ORDER\n', style=f'bold {TEXT_PRIMARY}')
        sym_display = self._symbol or '—'
        result.append(f'  Symbol:  {sym_display}\n', style=TEXT_SECONDARY)
        buy_style = f'bold #000000 on {ACCENT_GREEN}' if self._side == 'BUY' else ACCENT_GREEN
        sell_style = f'bold #000000 on {ERROR_RED}' if self._side == 'SELL' else ERROR_RED
        result.append('  Side:    ')
        result.append(' BUY ', style=buy_style)
        result.append(' ')
        result.append(' SELL ', style=sell_style)
        result.append('\n')
        mkt_style = f'bold #000000 on {INFO_BLUE}' if self._order_type == 'MARKET' else INFO_BLUE
        lmt_style = f'bold #000000 on {INFO_BLUE}' if self._order_type == 'LIMIT' else INFO_BLUE
        result.append('  Type:    ')
        result.append(' MARKET ', style=mkt_style)
        result.append(' ')
        result.append(' LIMIT ', style=lmt_style)
        result.append('\n')
        qty_display = self._quantity or '—'
        result.append(f'  Qty:     {qty_display}\n', style=TEXT_SECONDARY)
        if self._order_type == 'LIMIT':
            price_display = self._price or '—'
            result.append(f'  Price:   {price_display}\n', style=TEXT_SECONDARY)
        if self._error:
            result.append(f'  ✗ {self._error}\n', style=ERROR_RED)
        if self._success:
            result.append(f'  ✓ {self._success}\n', style=ACCENT_GREEN)
        result.append('\n', style='')
        result.append('  [s]ymbol [b]uy/sell [t]ype [Q]ty [p]rice [Enter]submit\n', style=TEXT_MUTED)
        return result

    def set_symbol(self, symbol: str) -> None:
        self._symbol = symbol.upper().strip()
        self._error = ''
        self.refresh(layout=True)

    def set_quantity(self, qty: str) -> None:
        self._quantity = qty.strip()
        self._error = ''
        self.refresh(layout=True)

    def set_price(self, price: str) -> None:
        self._price = price.strip()
        self._error = ''
        self.refresh(layout=True)

    def toggle_side(self) -> None:
        self._side = 'SELL' if self._side == 'BUY' else 'BUY'
        self._error = ''
        self.refresh(layout=True)

    def toggle_type(self) -> None:
        self._order_type = 'LIMIT' if self._order_type == 'MARKET' else 'MARKET'
        self._error = ''
        self.refresh(layout=True)

    def get_order_params(self) -> dict[str, str] | None:
        """Validate and return order parameters, or None with error set."""
        self._error = ''
        self._success = ''
        if not self._symbol:
            self._error = 'Symbol is required'
            self.refresh(layout=True)
            return None
        if not self._quantity:
            self._error = 'Quantity is required'
            self.refresh(layout=True)
            return None
        try:
            qty = float(self._quantity)
            if qty <= 0:
                raise ValueError
        except (ValueError, TypeError):
            self._error = 'Quantity must be a positive number'
            self.refresh(layout=True)
            return None
        if self._order_type == 'LIMIT':
            if not self._price:
                self._error = 'Price is required for LIMIT orders'
                self.refresh(layout=True)
                return None
            try:
                p = float(self._price)
                if p <= 0:
                    raise ValueError
            except (ValueError, TypeError):
                self._error = 'Price must be a positive number'
                self.refresh(layout=True)
                return None
        params: dict[str, str] = {'symbol': self._symbol, 'side': self._side, 'order_type': self._order_type, 'quantity': self._quantity}
        if self._order_type == 'LIMIT':
            params['price'] = self._price
        return params

    def show_success(self, msg: str) -> None:
        self._error = ''
        self._success = msg
        self.refresh(layout=True)

    def show_error(self, msg: str) -> None:
        self._success = ''
        self._error = msg
        self.refresh(layout=True)

class OrderHistoryWidget(Static):
    """Displays the full order history with status, type, and fill info."""
    orders: reactive[list[dict[str, Any]]] = reactive(list, layout=True)
    DEFAULT_CSS = f'OrderHistoryWidget {{ {EXPANDABLE_CSS} }}'

    def render(self) -> Text:
        result = Text()
        result.append(' ORDER HISTORY\n', style=f'bold {TEXT_PRIMARY}')
        if not self.orders:
            result.append('  No orders placed\n', style=TEXT_MUTED)
            return result
        avail = getattr(self.size, 'width', 120) or 120
        show_price = avail >= 72
        if show_price:
            result.append('  TIME        SIDE  TYPE    SYMBOL        QTY       PRICE    STATUS\n', style=TEXT_MUTED)
            result.append('  ' + '─' * 72 + '\n', style=BORDER_DIM)
        else:
            result.append('  TIME       SIDE  TYPE   SYM       QTY     STATUS\n', style=TEXT_MUTED)
            result.append('  ' + '─' * 55 + '\n', style=BORDER_DIM)
        for order in self.orders[:50]:
            created = float(order.get('created_at', 0))
            ts_str = time.strftime('%H:%M:%S', time.localtime(created))
            side = str(order.get('side', '?'))
            otype = str(order.get('order_type', '?'))
            sym = str(order.get('symbol', '?'))
            qty = float(order.get('quantity', 0))
            price = float(order.get('price', 0))
            fill_price = order.get('fill_price')
            status = str(order.get('status', '?'))
            s_style = order_status_style(status)
            sd_style = side_style(side)
            qty_str = compact_qty(qty)
            if show_price:
                result.append(f'  {ts_str:<11}', style=TEXT_MUTED)
                result.append(f' {side:<5}', style=sd_style)
                result.append(f' {otype:<7}', style=TEXT_SECONDARY)
                result.append(f' {sym:<13}', style=TEXT_PRIMARY)
                result.append(f' {qty_str:>9}', style=TEXT_PRIMARY)
                if fill_price is not None:
                    result.append(f' {format_price(float(fill_price)):>9}', style=ACCENT_GREEN)
                elif price > 0:
                    result.append(f' {format_price(price):>9}', style=TEXT_SECONDARY)
                else:
                    result.append(f' {'market':>9}', style=TEXT_MUTED)
                result.append(f' {status}', style=s_style)
            else:
                result.append(f'  {ts_str:<10}', style=TEXT_MUTED)
                result.append(f' {side:<5}', style=sd_style)
                result.append(f' {otype:<6}', style=TEXT_SECONDARY)
                result.append(f' {truncate(sym, 9):<9}', style=TEXT_PRIMARY)
                result.append(f' {qty_str:>8}', style=TEXT_PRIMARY)
                result.append(f' {status}', style=s_style)
            result.append('\n')
        return result

class PaperScreen(BaseScreen):
    """Paper trading screen with positions, order form, history, and PnL chart."""
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = BaseScreen.BINDINGS + [Binding('s', 'focus_symbol', 'Symbol', show=True), Binding('b', 'toggle_side', 'Buy/Sell', show=True), Binding('t', 'toggle_type', 'Type', show=True), Binding('Q', 'focus_qty', 'Qty', show=True), Binding('p', 'focus_price', 'Price', show=True), Binding('enter', 'submit_order', 'Submit', show=True), Binding('n', 'new_session', 'New Session', show=True), Binding('c', 'cancel_order', 'Cancel Order', show=True)]
    session_id: reactive[str] = reactive('')
    session_name: reactive[str] = reactive('')
    _loading_widget_id: ClassVar[str] = '#paper-loading'
    _refresh_interval: ClassVar[float] = 15.0
    _api_client_class: ClassVar[type] = TuiApiClient

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._pnl_history: list[float] = []
        self._mark_prices: dict[str, float] = {}
        self._orders: list[dict[str, Any]] = []
        self._current_focus: str = 'form'
        self._selected_order_idx: int = 0
        self._chart_width: int = 50

    def compose(self) -> ComposeResult:
        with Vertical(id='paper-layout'):
            with Horizontal(id='paper-main'):
                with Vertical(id='paper-left'):
                    yield OrderFormWidget(id='order-form')
                    yield AccountSummaryWidget(id='account-summary')
                with Vertical(id='paper-right'):
                    yield PnlChartWidget(id='pnl-chart')
                    yield PositionsTableWidget(id='positions-table')
                    yield OrderHistoryWidget(id='order-history')
            yield LoadingIndicator(id='paper-loading')
            yield Static(self.status_text, id='paper-status')

    def on_mount(self) -> None:
        """Initialize the screen — create or load a session."""
        super().on_mount()
        self.call_after_refresh(self._init_session)

    async def _find_existing_session(self, name: str) -> dict[str, Any] | None:
        """Check for an existing session with the given name."""
        if self._api is None:
            return None
        data = await self._api.list_paper_sessions()
        all_sessions = data.get('sessions', [])
        matches = [s for s in all_sessions if s.get('name') == name]
        if matches:
            return cast(dict[str, Any], sorted(matches, key=lambda s: s.get('created_at', 0))[-1])
        return None

    async def _init_session(self) -> None:
        """Create a new paper session or resume an existing 'tui-session'."""
        self.status_text = 'Creating paper session…'
        self.is_loading = True
        try:
            try:
                existing = await self._find_existing_session('tui-session')
                if existing:
                    self.session_id = existing['session_id']
                    self.session_name = existing.get('name', 'tui-session')
                    self.status_text = f'Resumed session {self.session_id[:8]}…'
                    self.is_loading = False
                    safe_query(self, '#order-form', OrderFormWidget, lambda f: f.set_symbol('BTC-USD'))
                    await self._refresh_all()
                    return
            except (ConnectionError, TimeoutError, ValueError, KeyError) as exc:
                logger.debug('Session reuse check failed, creating new: %s', exc)
            assert self._api is not None
            data = await self._api.create_paper_session('tui-session')
            self.session_id = data.get('session_id', '')
            self.session_name = data.get('name', 'tui-session')
            self.status_text = f'Session {self.session_id[:8]}… ready'
            self.is_loading = False
            safe_query(self, '#order-form', OrderFormWidget, lambda f: f.set_symbol('BTC-USD'))
            await self._refresh_all()
        except (ConnectionError, TimeoutError, ValueError, KeyError) as exc:
            self.status_text = f'Init error: {exc}'
            self.is_loading = False
            logger.warning('Session init failed: %s', exc)

    async def _fetch_data(self) -> None:
        """Fetch all session data and update widgets."""
        if not self.session_id:
            return
        try:
            assert self._api is not None
            data = await self._api.get_paper_session(self.session_id)
            mp = data.get('mark_prices')
            if isinstance(mp, dict):
                self._mark_prices = mp
            self._update_positions(data.get('position', []))
            self._update_orders(data.get('orders', []))
            self._update_pnl(data.get('pnl', {}))
            self._update_status_text(f'Session {self.session_id[:8]}… · updated  [r]efresh  [s]ymbol [b]uy/sell [?]help')
        except Exception as exc:
            self._update_status_text(f'Refresh error: {sanitize_status_text(str(exc), 60)}  [r]etry')
            logger.warning('paper-status failed: %s', exc)

    def _update_positions(self, positions: list[dict[str, Any]]) -> None:

        def _update(w: PositionsTableWidget) -> None:
            w.positions = positions
            w.mark_prices = self._mark_prices
        safe_query(self, '#positions-table', PositionsTableWidget, _update)

    def _update_orders(self, orders: list[dict[str, Any]]) -> None:
        self._orders = orders
        safe_query(self, '#order-history', OrderHistoryWidget, lambda w: setattr(w, 'orders', orders))

    def _update_pnl(self, pnl_data: dict[str, Any]) -> None:

        def _update_summary(w: AccountSummaryWidget) -> None:
            w.pnl_data = pnl_data
            w.session_name = self.session_name
        safe_query(self, '#account-summary', AccountSummaryWidget, _update_summary)
        total_pnl = float(pnl_data.get('total_pnl', 0))
        self._pnl_history.append(total_pnl)
        if len(self._pnl_history) > PNL_HISTORY_MAX:
            self._pnl_history[:] = self._pnl_history[-PNL_HISTORY_MAX:]
        safe_query(self, '#pnl-chart', PnlChartWidget, lambda w: setattr(w, 'pnl_history', self._pnl_history))

    async def _place_order(self, params: dict[str, str]) -> None:
        """Place a paper order via the FastAPI HTTP client."""
        if self._api is None:
            return
        if not self.session_id:
            safe_query(self, '#order-form', OrderFormWidget, lambda w: w.show_error('No active session'))
            return
        try:
            order_data = await self._api.place_paper_order(self.session_id, symbol=params['symbol'], side=params['side'], quantity=float(params['quantity']), order_type=params['order_type'], price=float(params['price']) if 'price' in params else None)
            order_id = order_data.get('order_id', '?')[:8]
            safe_query(self, '#order-form', OrderFormWidget, lambda w: w.show_success(f'Order {order_id}… {params['side']} {params['quantity']} {params['symbol']}'))
            self.notify(f'Order placed: {params['side']} {params['quantity']} {params['symbol']}', severity='information', timeout=3)
            await self._refresh_all()
        except Exception as exc:
            err_msg = str(exc)[:80]

            def _show_err(w: OrderFormWidget, m: str=err_msg) -> None:
                w.show_error(m)
            safe_query(self, '#order-form', OrderFormWidget, _show_err)
            logger.warning('Order placement error: %s', exc)

    def action_focus_symbol(self) -> None:
        """Focus symbol input for editing."""
        self._push_text_input('symbol', 'Enter symbol (e.g. BTC-USD):')

    def action_toggle_side(self) -> None:
        """Toggle BUY/SELL side."""
        safe_query(self, '#order-form', OrderFormWidget, lambda w: w.toggle_side())

    def action_toggle_type(self) -> None:
        """Toggle MARKET/LIMIT order type."""
        safe_query(self, '#order-form', OrderFormWidget, lambda w: w.toggle_type())

    def action_focus_qty(self) -> None:
        """Focus quantity input."""
        self._push_text_input('quantity', 'Enter quantity:')

    def action_focus_price(self) -> None:
        """Focus price input."""
        self._push_text_input('price', 'Enter limit price:')

    async def action_submit_order(self) -> None:
        """Validate and submit the current order."""
        try:
            form = self.query_one('#order-form', OrderFormWidget)
            params = form.get_order_params()
            if params:
                await self._place_order(params)
        except Exception as exc:
            logger.warning('Submit order failed: %s', exc)

    def action_new_session(self) -> None:
        """Create a new paper trading session."""
        self.call_after_refresh(self._init_session)

    async def action_cancel_order(self) -> None:
        """Cancel an open order — auto-select if only one, otherwise prompt."""
        if not self.session_id:
            self.status_text = 'No active session'
            return
        open_orders = [o for o in self._orders if o.get('status') == 'OPEN']
        if not open_orders:
            self.status_text = 'No open orders to cancel'
            return
        if len(open_orders) == 1:
            await self._do_cancel_order(open_orders[0]['order_id'])
        else:
            self.app.push_screen(_CancelOrderScreen(open_orders), callback=self._on_cancel_order_result)

    def _on_cancel_order_result(self, order_id: str | None) -> None:
        if order_id:
            self.call_after_refresh(self._do_cancel_order, order_id)

    async def _do_cancel_order(self, order_id: str) -> None:
        """Cancel an open paper order via the FastAPI HTTP client."""
        if self._api is None:
            return
        try:
            data = await self._api.cancel_paper_order(self.session_id, order_id)
            sym = data.get('symbol', '?')
            self.status_text = f'Cancelled order for {sym}'
            self.notify(f'Order cancelled: {sym}', severity='information', timeout=3)
            await self._refresh_all()
        except Exception as exc:
            self.status_text = f'Cancel error: {exc}'
            logger.warning('Cancel order error: %s', exc)

    def _push_text_input(self, field: str, prompt: str) -> None:
        self.app.push_screen(_TextInputScreen(field, prompt), callback=self._on_text_input_result)

    def _on_text_input_result(self, result: tuple[str, str] | None) -> None:
        if result is None:
            return
        field, value = result
        setters = {'symbol': 'set_symbol', 'quantity': 'set_quantity', 'price': 'set_price'}
        method = setters.get(field)
        if method:
            safe_query(self, '#order-form', OrderFormWidget, lambda w: getattr(w, method)(value))

class _TextInputScreen(Screen[tuple[str, str] | None]):
    """A minimal modal text input for entering values."""
    DEFAULT_CSS = '_TextInputScreen { align: center middle; background: rgba(0, 0, 0, 0.85); } width: 50; height: auto; padding: 1 2; background: #0d1210; border: solid #2a3a30; } color: #4ade80; text-style: bold; margin: 0 0 1 0; } background: #1a2a1f; }'
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [Binding('escape', 'dismiss', 'Cancel')]

    def __init__(self, field: str, prompt: str) -> None:
        super().__init__()
        self._field = field
        self._prompt = prompt

    def compose(self) -> ComposeResult:
        yield Vertical(Static(self._prompt, id='text-input-prompt'), Input(id='text-input-field'), id='text-input-dialog')

    def on_mount(self) -> None:
        self.query_one('#text-input-field', Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        if value:
            self.dismiss((self._field, value))
        else:
            self.dismiss(None)

class _CancelOrderScreen(Screen[str | None]):
    """A modal for selecting which open order to cancel."""
    DEFAULT_CSS = '_CancelOrderScreen { align: center middle; background: rgba(0, 0, 0, 0.85); } width: 60; height: auto; padding: 1 2; background: #0d1210; border: solid #2a3a30; } color: #4ade80; text-style: bold; margin: 0 0 1 0; } background: #1a2a1f; }'
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [Binding('escape', 'dismiss', 'Cancel')]

    def __init__(self, open_orders: list[dict[str, Any]]) -> None:
        super().__init__()
        self._open_orders = open_orders

    def compose(self) -> ComposeResult:
        lines = []
        for i, o in enumerate(self._open_orders):
            oid = str(o.get('order_id', '?'))[:8]
            sym = str(o.get('symbol', '?'))
            side = str(o.get('side', '?'))
            qty = o.get('quantity', 0)
            lines.append(f'{i + 1}) {oid}… {side} {qty} {sym}')
        prompt = 'Enter order # to cancel:\n' + '\n'.join(lines)
        yield Vertical(Static(prompt, id='cancel-order-prompt'), Input(id='cancel-order-field'), id='cancel-order-dialog')

    def on_mount(self) -> None:
        self.query_one('#cancel-order-field', Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        try:
            idx = int(value) - 1
            if 0 <= idx < len(self._open_orders):
                self.dismiss(self._open_orders[idx]['order_id'])
                return
        except (ValueError, TypeError):
            pass
        self.dismiss(None)