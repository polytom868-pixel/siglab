from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime
from typing import Any, ClassVar, cast

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Input, Static

from siglab.tui.api_client import TuiApiClient
from siglab.tui.formatting import (
    ACCENT_GREEN,
    BORDER_DIM,
    COMPACT_CSS,
    ERROR_RED,
    EXPANDABLE_CSS,
    INFO_BLUE,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    WARNING_YELLOW,
    bar_gauge,
    compact_qty,
    format_pnl,
    format_price,
    gauge_color,
    order_status_style,
    safe_query,
    sanitize_status_text,
    severity_color,
    side_style,
    truncate,
)
from siglab.tui.loading import LoadingIndicator
from siglab.tui.screens.base import BaseScreen, render_header
from siglab.tui.widgets.sparkline import sparkline_text

logger = logging.getLogger(__name__)
PNL_HISTORY_MAX = 120

class PositionsTableWidget(Static):
    positions: reactive[list[dict[str, Any]]] = reactive(list, layout=True)
    mark_prices: reactive[dict[str, float]] = reactive(dict, layout=True)
    DEFAULT_CSS = f'PositionsTableWidget {{ {EXPANDABLE_CSS} }}'
    def render(self) -> Text:
        result = Text()
        result.append(' POSITIONS\n', style=f'bold {TEXT_PRIMARY}')
        if not self.positions: result.append('  No open positions\n', style=TEXT_MUTED); return result
        avail = (getattr(self.size, 'width', 120) or 120); show_mark = avail >= 70
        if show_mark:
            result.append('  SYMBOL          SIZE         ENTRY        MARK       UNREAL PnL\n', style=TEXT_MUTED)
            result.append('  ' + '─' * 68 + '\n', style=BORDER_DIM)
        else:
            result.append('  SYMBOL        SIZE       ENTRY      UNREAL PnL\n', style=TEXT_MUTED)
            result.append('  ' + '─' * 50 + '\n', style=BORDER_DIM)
        for pos in self.positions:
            sym = str(pos.get('symbol', '?')); qty = float(pos.get('quantity', 0)); entry = float(pos.get('entry_price', 0))
            mark = self.mark_prices.get(sym, entry)
            u = float(pos.get('unrealized_pnl', 0))
            if mark > 0 and entry > 0 and qty != 0: u = qty * (mark - entry) if qty > 0 else abs(qty) * (entry - mark)
            if show_mark:
                result.append(f'  {sym:<16}', style=TEXT_SECONDARY)
                result.append(f'{format_price(qty):>12}  {format_price(entry):>12}  {format_price(mark):>12}  ', style=TEXT_PRIMARY)
                result.append_text(format_pnl(u))
            else:
                result.append(f'  {truncate(sym, 14):<14}', style=TEXT_SECONDARY)
                result.append(f'{format_price(qty):>10}  {format_price(entry):>10}  ', style=TEXT_PRIMARY)
                result.append_text(format_pnl(u))
            result.append('\n')
        return result

class AccountSummaryWidget(Static):
    pnl_data: reactive[dict[str, Any]] = reactive(dict, layout=True)
    session_name: reactive[str] = reactive('', layout=True)
    DEFAULT_CSS = f'AccountSummaryWidget {{ {COMPACT_CSS} }}'
    def render(self) -> Text:
        result = Text()
        name = self.session_name or 'No session'
        result.append(f' SESSION: {name}\n', style=f'bold {ACCENT_GREEN}')
        if not self.pnl_data: result.append('  No PnL data\n', style=TEXT_MUTED); return result
        r = float(self.pnl_data.get('realized_pnl', 0)); u = float(self.pnl_data.get('unrealized_pnl', 0))
        t = float(self.pnl_data.get('total_pnl', 0)); f = float(self.pnl_data.get('total_funding_cost', 0))
        oc = int(self.pnl_data.get('open_position_count', 0))
        for lbl, val in [('Realized:', r), ('Unrealized:', u), ('Total PnL:', t), ('Funding:', f)]:
            result.append(f'  {lbl:<12}', style=TEXT_MUTED); result.append_text(format_pnl(val)); result.append('\n')
        result.append(f'   Open: {oc}\n', style=TEXT_MUTED)
        return result

class PnlChartWidget(Static):
    pnl_history: reactive[list[float]] = reactive(list, layout=True)
    DEFAULT_CSS = 'PnlChartWidget { height: auto; min-height: 5; padding: 0 1; background: #0a0a0a; }'
    def render(self) -> Text:
        result = Text()
        result.append(' PnL PERFORMANCE\n', style=f'bold {TEXT_PRIMARY}')
        if not self.pnl_history: result.append('  Collecting data...\n', style=TEXT_MUTED); return result
        result.append('  '); result.append_text(sparkline_text(self.pnl_history, width=50)); result.append('\n')
        lo, hi, cur = min(self.pnl_history), max(self.pnl_history), self.pnl_history[-1]
        for lbl, v in [('Low:', lo), ('High:', hi), ('Now:', cur)]:
            result.append(f'  {lbl:<6}', style=TEXT_MUTED); result.append_text(format_pnl(v))
        result.append('\n'); return result

class OrderFormWidget(Static):
    DEFAULT_CSS = 'OrderFormWidget { height: auto; min-height: 14; padding: 0 1; background: #0d1210; }'
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [Binding('enter', 'submit_order', 'Submit Order', show=False)]
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._side, self._order_type = 'BUY', 'MARKET'; self._symbol = self._quantity = self._price = self._error = self._success = ''
    def render(self) -> Text:
        r = Text()
        r.append(' PLACE ORDER\n', style=f'bold {TEXT_PRIMARY}')
        r.append(f'  Symbol:  {self._symbol or "—"}\n', style=TEXT_SECONDARY)
        r.append('  Side:    ')
        r.append(' BUY ', style=f'bold #000000 on {ACCENT_GREEN}' if self._side == 'BUY' else ACCENT_GREEN)
        r.append(' '); r.append(' SELL ', style=f'bold #000000 on {ERROR_RED}' if self._side == 'SELL' else ERROR_RED); r.append('\n')
        r.append('  Type:    ')
        r.append(' MARKET ', style=f'bold #000000 on {INFO_BLUE}' if self._order_type == 'MARKET' else INFO_BLUE)
        r.append(' '); r.append(' LIMIT ', style=f'bold #000000 on {INFO_BLUE}' if self._order_type == 'LIMIT' else INFO_BLUE); r.append('\n')
        r.append(f'  Qty:     {self._quantity or "—"}\n', style=TEXT_SECONDARY)
        if self._order_type == 'LIMIT': r.append(f'  Price:   {self._price or "—"}\n', style=TEXT_SECONDARY)
        if self._error: r.append(f'  X {self._error}\n', style=ERROR_RED)
        if self._success: r.append(f'  V {self._success}\n', style=ACCENT_GREEN)
        r.append('\n'); r.append('  [s]ymbol [b]uy/sell [t]ype [Q]ty [p]rice [Enter]submit\n', style=TEXT_MUTED)
        return r
    def set_symbol(self, symbol: str) -> None: self._symbol = symbol.upper().strip(); self._error = ''; self.refresh()
    def set_quantity(self, qty: str) -> None: self._quantity = qty.strip(); self._error = ''; self.refresh()
    def set_price(self, price: str) -> None: self._price = price.strip(); self._error = ''; self.refresh()
    def toggle_side(self) -> None: self._side = 'SELL' if self._side == 'BUY' else 'BUY'; self._error = ''; self.refresh()
    def toggle_type(self) -> None: self._order_type = 'LIMIT' if self._order_type == 'MARKET' else 'MARKET'; self._error = ''; self.refresh()
    def get_order_params(self) -> dict[str, str] | None:
        self._error = self._success = ''
        if not self._symbol: self._error = 'Symbol is required'; self.refresh(); return None
        if not self._quantity: self._error = 'Quantity is required'; self.refresh(); return None
        try:
            if float(self._quantity) <= 0: raise ValueError
        except (ValueError, TypeError): self._error = 'Quantity must be a positive number'; self.refresh(); return None
        if self._order_type == 'LIMIT':
            if not self._price: self._error = 'Price is required for LIMIT orders'; self.refresh(); return None
            try:
                if float(self._price) <= 0: raise ValueError
            except (ValueError, TypeError): self._error = 'Price must be a positive number'; self.refresh(); return None
        p: dict[str, str] = {'symbol': self._symbol, 'side': self._side, 'order_type': self._order_type, 'quantity': self._quantity}
        if self._order_type == 'LIMIT': p['price'] = self._price
        return p
    def _set_msg(self, err: str, suc: str) -> None: self._error, self._success = err, suc; self.refresh()
    show_success = lambda s, m: s._set_msg('', m)
    show_error = lambda s, m: s._set_msg(m, '')

class OrderHistoryWidget(Static):
    orders: reactive[list[dict[str, Any]]] = reactive(list, layout=True)
    DEFAULT_CSS = f'OrderHistoryWidget {{ {EXPANDABLE_CSS} }}'
    def render(self) -> Text:
        result = Text()
        result.append(' ORDER HISTORY\n', style=f'bold {TEXT_PRIMARY}')
        if not self.orders: result.append('  No orders placed\n', style=TEXT_MUTED); return result
        avail = (getattr(self.size, 'width', 120) or 120); show_price = avail >= 72
        if show_price:
            result.append('  TIME        SIDE  TYPE    SYMBOL        QTY       PRICE    STATUS\n', style=TEXT_MUTED)
            result.append('  ' + '─' * 72 + '\n', style=BORDER_DIM)
        else:
            result.append('  TIME       SIDE  TYPE   SYM       QTY     STATUS\n', style=TEXT_MUTED)
            result.append('  ' + '─' * 55 + '\n', style=BORDER_DIM)
        for o in self.orders[:50]:
            ts = time.strftime('%H:%M:%S', time.localtime(float(o.get('created_at', 0))))
            sd = str(o.get('side', '?')); ot = str(o.get('order_type', '?')); sym = str(o.get('symbol', '?'))
            qty = float(o.get('quantity', 0)); price = float(o.get('price', 0)); fp = o.get('fill_price')
            st = str(o.get('status', '?')); s_st = order_status_style(st); sd_st = side_style(sd); qs = compact_qty(qty)
            if show_price:
                result.append(f'  {ts:<11}', style=TEXT_MUTED); result.append(f' {sd:<5}', style=sd_st)
                result.append(f' {ot:<7}', style=TEXT_SECONDARY); result.append(f' {sym:<13}', style=TEXT_PRIMARY)
                result.append(f' {qs:>9}', style=TEXT_PRIMARY)
                if fp is not None: result.append(f' {format_price(float(fp)):>9}', style=ACCENT_GREEN)
                elif price > 0: result.append(f' {format_price(price):>9}', style=TEXT_SECONDARY)
                else: result.append(f' {"market":>9}', style=TEXT_MUTED)
                result.append(f' {st}', style=s_st)
            else:
                result.append(f'  {ts:<10}', style=TEXT_MUTED); result.append(f' {sd:<5}', style=sd_st)
                result.append(f' {ot:<6}', style=TEXT_SECONDARY); result.append(f' {truncate(sym, 9):<9}', style=TEXT_PRIMARY)
                result.append(f' {qs:>8}', style=TEXT_PRIMARY); result.append(f' {st}', style=s_st)
            result.append('\n')
        return result

class PaperScreen(BaseScreen):
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = BaseScreen.BINDINGS + [Binding('s', 'focus_symbol', 'Symbol', show=True), Binding('b', 'toggle_side', 'Buy/Sell', show=True), Binding('t', 'toggle_type', 'Type', show=True), Binding('Q', 'focus_qty', 'Qty', show=True), Binding('p', 'focus_price', 'Price', show=True), Binding('enter', 'submit_order', 'Submit', show=True), Binding('n', 'new_session', 'New Session', show=True), Binding('c', 'cancel_order', 'Cancel Order', show=True)]
    session_id: reactive[str] = reactive(''); session_name: reactive[str] = reactive('')
    _loading_widget_id: ClassVar[str] = '#paper-loading'; _refresh_interval: ClassVar[float] = 15.0
    _api_client_class: ClassVar[type] = TuiApiClient
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._pnl_history: list[float] = []; self._mark_prices: dict[str, float] = {}; self._orders: list[dict[str, Any]] = []
    def compose(self) -> ComposeResult:
        with Vertical(id='paper-layout'):
            with Horizontal(id='paper-main'):
                with Vertical(id='paper-left'): yield OrderFormWidget(id='order-form'); yield AccountSummaryWidget(id='account-summary')
                with Vertical(id='paper-right'): yield PnlChartWidget(id='pnl-chart'); yield PositionsTableWidget(id='positions-table'); yield OrderHistoryWidget(id='order-history')
            yield LoadingIndicator(id='paper-loading'); yield Static(self.status_text, id='paper-status')
    def on_mount(self) -> None: super().on_mount(); self.call_after_refresh(self._init_session)
    async def _find_existing_session(self, name: str) -> dict[str, Any] | None:
        if self._api is None: return None
        ms = [s for s in (await self._api.list_paper_sessions()).get('sessions', []) if s.get('name') == name]
        return cast(dict[str, Any], sorted(ms, key=lambda s: s.get('created_at', 0))[-1]) if ms else None
    async def _init_session(self) -> None:
        self.is_loading, self.status_text = True, 'Creating paper session...'
        try:
            try:
                ex = await self._find_existing_session('tui-session')
                if ex:
                    self.session_id = ex['session_id']; self.session_name = ex.get('name', 'tui-session')
                    self.is_loading, self.status_text = False, f'Resumed session {self.session_id[:8]}...'
                    safe_query(self, '#order-form', OrderFormWidget, lambda f: f.set_symbol('BTC-USD'))
                    await self._refresh_all(); return
            except (ConnectionError, TimeoutError, ValueError, KeyError) as exc: logger.debug('Session reuse check failed, creating new: %s', exc)
            assert self._api is not None
            data = await self._api.create_paper_session('tui-session')
            self.session_id = data.get('session_id', ''); self.session_name = data.get('name', 'tui-session')
            self.is_loading, self.status_text = False, f'Session {self.session_id[:8]}... ready'
            safe_query(self, '#order-form', OrderFormWidget, lambda f: f.set_symbol('BTC-USD'))
            await self._refresh_all()
        except (ConnectionError, TimeoutError, ValueError, KeyError) as exc:
            self.is_loading, self.status_text = False, f'Init error: {exc}'; logger.warning('Session init failed: %s', exc)
    async def _fetch_data(self) -> None:
        if not self.session_id: return
        try:
            assert self._api is not None
            data = await self._api.get_paper_session(self.session_id); mp = data.get('mark_prices')
            if isinstance(mp, dict): self._mark_prices = mp
            self._upd_pos(data.get('position', [])); self._upd_ords(data.get('orders', [])); self._upd_pnl(data.get('pnl', {}))
            self._update_status_text(f'Session {self.session_id[:8]}... . updated  [r]efresh  [s]ymbol [b]uy/sell [?]help')
        except Exception as exc:
            self._update_status_text(f'Refresh error: {sanitize_status_text(str(exc), 60)}  [r]etry'); logger.warning('paper-status failed: %s', exc)
    def _upd_pos(self, positions: list[dict[str, Any]]) -> None:
        safe_query(self, '#positions-table', PositionsTableWidget, lambda w: (setattr(w, 'positions', positions), setattr(w, 'mark_prices', self._mark_prices)))
    def _upd_ords(self, orders: list[dict[str, Any]]) -> None:
        self._orders = orders; safe_query(self, '#order-history', OrderHistoryWidget, lambda w: setattr(w, 'orders', orders))
    def _upd_pnl(self, pnl_data: dict[str, Any]) -> None:
        safe_query(self, '#account-summary', AccountSummaryWidget, lambda w: (setattr(w, 'pnl_data', pnl_data), setattr(w, 'session_name', self.session_name)))
        self._pnl_history.append(float(pnl_data.get('total_pnl', 0))); self._pnl_history = self._pnl_history[-PNL_HISTORY_MAX:]
        safe_query(self, '#pnl-chart', PnlChartWidget, lambda w: setattr(w, 'pnl_history', self._pnl_history))
    async def _place_order(self, params: dict[str, str]) -> None:
        if self._api is None: return
        if not self.session_id: safe_query(self, '#order-form', OrderFormWidget, lambda w: w.show_error('No active session')); return
        try:
            od = await self._api.place_paper_order(self.session_id, symbol=params['symbol'], side=params['side'], quantity=float(params['quantity']), order_type=params['order_type'], price=float(params['price']) if 'price' in params else None)
            safe_query(self, '#order-form', OrderFormWidget, lambda w: w.show_success(f'Order {od.get("order_id","?")[:8]}... {params["side"]} {params["quantity"]} {params["symbol"]}'))
            self.notify(f'Order placed: {params["side"]} {params["quantity"]} {params["symbol"]}', severity='information', timeout=3)
            await self._refresh_all()
        except Exception as exc: safe_query(self, '#order-form', OrderFormWidget, lambda w, m=str(exc)[:80]: w.show_error(m)); logger.warning('Order placement error: %s', exc)
    def action_focus_symbol(self) -> None: self._push_txt('symbol', 'Enter symbol (e.g. BTC-USD):')
    def action_toggle_side(self) -> None: safe_query(self, '#order-form', OrderFormWidget, lambda w: w.toggle_side())
    def action_toggle_type(self) -> None: safe_query(self, '#order-form', OrderFormWidget, lambda w: w.toggle_type())
    def action_focus_qty(self) -> None: self._push_txt('quantity', 'Enter quantity:')
    def action_focus_price(self) -> None: self._push_txt('price', 'Enter limit price:')
    async def action_submit_order(self) -> None:
        try:
            params = self.query_one('#order-form', OrderFormWidget).get_order_params()
            if params: await self._place_order(params)
        except Exception as exc: logger.warning('Submit order failed: %s', exc)
    def action_new_session(self) -> None: self.call_after_refresh(self._init_session)
    async def action_cancel_order(self) -> None:
        if not self.session_id: self.status_text = 'No active session'; return
        oo = [o for o in self._orders if o.get('status') == 'OPEN']
        if not oo: self.status_text = 'No open orders to cancel'; return
        if len(oo) == 1: await self._do_cancel(oo[0]['order_id'])
        else: self.app.push_screen(_CancelOrderScreen(oo), callback=self._on_cancel_res)
    def _on_cancel_res(self, order_id: str | None) -> None:
        if order_id: self.call_after_refresh(self._do_cancel, order_id)
    async def _do_cancel(self, order_id: str) -> None:
        if self._api is None: return
        try:
            sym = (await self._api.cancel_paper_order(self.session_id, order_id)).get('symbol', '?')
            self.status_text = f'Cancelled order for {sym}'; self.notify(f'Order cancelled: {sym}', severity='information', timeout=3)
            await self._refresh_all()
        except Exception as exc: self.status_text = f'Cancel error: {exc}'; logger.warning('Cancel order error: %s', exc)
    def _push_txt(self, field: str, prompt: str) -> None:
        self.app.push_screen(_TextInputScreen(field, prompt), callback=self._on_txt_res)
    def _on_txt_res(self, result: tuple[str, str] | None) -> None:
        if result is None: return
        f, v = result; m = {'symbol': 'set_symbol', 'quantity': 'set_quantity', 'price': 'set_price'}.get(f)
        if m: safe_query(self, '#order-form', OrderFormWidget, lambda w: getattr(w, m)(v))

class _TextInputScreen(Screen[tuple[str, str] | None]):
    DEFAULT_CSS = '_TextInputScreen { align: center middle; background: rgba(0, 0, 0, 0.85); } width: 50; height: auto; padding: 1 2; background: #0d1210; border: solid #2a3a30; } color: #4ade80; text-style: bold; margin: 0 0 1 0; } background: #1a2a1f; }'
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [Binding('escape', 'dismiss', 'Cancel')]
    def __init__(self, field: str, prompt: str) -> None:
        super().__init__(); self._field = field; self._prompt = prompt
    def compose(self) -> ComposeResult: yield Vertical(Static(self._prompt, id='text-input-prompt'), Input(id='text-input-field'), id='text-input-dialog')
    def on_mount(self) -> None: self.query_one('#text-input-field', Input).focus()
    def on_input_submitted(self, event: Input.Submitted) -> None:
        v = event.value.strip(); self.dismiss((self._field, v) if v else None)

class _CancelOrderScreen(Screen[str | None]):
    DEFAULT_CSS = '_CancelOrderScreen { align: center middle; background: rgba(0, 0, 0, 0.85); } width: 60; height: auto; padding: 1 2; background: #0d1210; border: solid #2a3a30; } color: #4ade80; text-style: bold; margin: 0 0 1 0; } background: #1a2a1f; }'
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [Binding('escape', 'dismiss', 'Cancel')]
    def __init__(self, open_orders: list[dict[str, Any]]) -> None:
        super().__init__(); self._open_orders = open_orders
    def compose(self) -> ComposeResult:
        ls = [f'{i + 1}) {str(o.get("order_id","?"))[:8]}... {o.get("side","?")} {o.get("quantity",0)} {o.get("symbol","?")}' for i, o in enumerate(self._open_orders)]
        yield Vertical(Static('Enter order # to cancel:\n' + '\n'.join(ls), id='cancel-order-prompt'), Input(id='cancel-order-field'), id='cancel-order-dialog')
    def on_mount(self) -> None: self.query_one('#cancel-order-field', Input).focus()
    def on_input_submitted(self, event: Input.Submitted) -> None:
        v = event.value.strip()
        try:
            idx = int(v) - 1
            if 0 <= idx < len(self._open_orders): self.dismiss(self._open_orders[idx]['order_id']); return
        except (ValueError, TypeError): pass
        self.dismiss(None)

MAX_ALERTS_DISPLAY = 50
_CORR_BLOCK_MAP = [(0.95, '█'), (0.7, '▓'), (0.4, '▒'), (0.1, '░')]
def _correlation_color(value: float) -> str: return ERROR_RED if value >= 0.7 else WARNING_YELLOW if value >= 0.4 else TEXT_MUTED
def _correlation_block(value: float) -> str:
    for t, c in _CORR_BLOCK_MAP:
        if value >= t: return c
    return chr(183)

class RiskGaugeWidget(Static):
    composite_score: reactive[float | None] = reactive(None, layout=True)
    sub_scores: reactive[dict[str, float]] = reactive(dict, layout=True); strategy_count: reactive[int] = reactive(0)
    def render(self) -> Text:
        result = Text(); render_header(result, 'COMPOSITE RISK SCORE', 36)
        if self.composite_score is None:
            result.append('\n  No risk data available\n  '); result.append(chr(9617) * 24, style=BORDER_DIM)
            result.append('\n\n  Start a paper session to\n  see risk metrics.\n', style=TEXT_MUTED); return result
        sc = self.composite_score; pct = int(sc * 100); color = gauge_color(sc)
        result.append('\n  '); result.append(bar_gauge(sc, width=24), style=f'bold {color}'); result.append(f'  {pct}/100\n\n', style=f'bold {color}')
        for key, lbl in {'sharpe': 'Sharpe', 'drawdown': 'Drawdown', 'concentration': 'Concentr.', 'correlation_risk': 'Corr.Risk'}.items():
            val = self.sub_scores.get(key)
            if val is not None:
                vc = gauge_color(val); result.append(f'  {lbl:<12}', style=TEXT_SECONDARY)
                result.append(bar_gauge(val, width=10), style=vc); result.append(f' {val:.2f}\n', style=TEXT_PRIMARY)
            else: result.append(f'  {lbl:<12}', style=TEXT_SECONDARY); result.append(chr(9617) * 10, style=BORDER_DIM); result.append(' --\n', style=TEXT_MUTED)
        if self.strategy_count > 0: result.append(f'\n  Strategies: {self.strategy_count}\n', style=TEXT_MUTED)
        return result

class DrawdownSparklineWidget(Static):
    drawdown_history: reactive[list[float]] = reactive(list, layout=True)
    max_drawdown: reactive[float | None] = reactive(None); current_drawdown: reactive[float | None] = reactive(None)
    recovery_periods: reactive[int | None] = reactive(None)
    def render(self) -> Text:
        result = Text(); render_header(result, 'DRAWDOWN', 36)
        if not self.drawdown_history:
            result.append('\n  Collecting equity data...\n  '); result.append(chr(9472) * 30, style=BORDER_DIM); result.append('\n'); return result
        vals = [-v for v in self.drawdown_history]; avail = (getattr(self.size, 'width', 80) or 80)
        cw = max(20, min(avail - 6, min(60, len(vals))))
        result.append('  '); result.append_text(sparkline_text(vals, width=cw, bearish_color=ERROR_RED)); result.append('\n\n')
        md, cd, rp = self.max_drawdown, self.current_drawdown, self.recovery_periods
        result.append('  Max DD: ', style=TEXT_SECONDARY)
        if md is not None: result.append(f'{md * 100:.1f}%', style=ERROR_RED if md < -0.1 else WARNING_YELLOW if md < -0.05 else TEXT_MUTED)
        else: result.append('--', style=TEXT_MUTED)
        result.append('   Current: ', style=TEXT_SECONDARY)
        if cd is not None: result.append(f'{cd * 100:.1f}%', style=ERROR_RED if cd < -0.1 else WARNING_YELLOW if cd < -0.05 else TEXT_MUTED)
        else: result.append('--', style=TEXT_MUTED)
        result.append('   Recovery: ', style=TEXT_SECONDARY); result.append(f'{rp} periods' if rp is not None else 'in progress', style=ACCENT_GREEN if rp is not None else WARNING_YELLOW)
        result.append('\n'); return result

class CorrelationHeatmapWidget(Static):
    matrix: reactive[list[list[float]] | None] = reactive(None, layout=True)
    strategy_names: reactive[list[str]] = reactive(list)
    def render(self) -> Text:
        result = Text(); render_header(result, 'CORRELATION MATRIX', 36)
        mx, names = self.matrix, self.strategy_names
        if not mx or len(mx) < 2: result.append('\n  Need ≥2 strategies for\n  correlation analysis\n', style=TEXT_MUTED); return result
        n = len(mx)
        if not names or len(names) != n: names = [f'S{i + 1}' for i in range(n)]
        avail = (getattr(self.size, 'width', 80) or 80); cw = 6; mnl = min(8, max(3, avail - 2 - n * cw)); sn = n
        if (3 + 2 + n * cw) > avail and n > 2:
            sn = max(2, (avail - 5) // cw)
            if sn < n: names = names[:sn]
        result.append(f'  {"":>{mnl}}  ', style=TEXT_MUTED)
        for name in names[:sn]: result.append(f'{name[:mnl].rjust(mnl)} ', style=INFO_BLUE)
        result.append('\n')
        for i in range(min(n, sn)):
            result.append(f'  {names[i][:mnl].rjust(mnl)}  ', style=TEXT_SECONDARY)
            for j in range(min(n, sn)):
                val = mx[i][j] if i < len(mx) and j < len(mx[i]) else 0.0
                result.append(f'{_correlation_block(val)}{val:.2f}'.rjust(mnl) + ' ', style=_correlation_color(val) if i != j else TEXT_SECONDARY)
            result.append('\n')
        if sn < n: result.append(f'  ... +{n - sn} more strategies\n', style=TEXT_MUTED)
        result.append('\n  Legend: '); result.append(chr(9608), style=TEXT_SECONDARY); result.append('=1.0 ', style=TEXT_MUTED)
        result.append(chr(9619), style=ERROR_RED); result.append('>=0.7 ', style=TEXT_MUTED)
        result.append(chr(9618), style=WARNING_YELLOW); result.append('>=0.4 ', style=TEXT_MUTED)
        result.append(chr(9617), style=TEXT_MUTED); result.append('>=0.1 ', style=TEXT_MUTED)
        result.append(chr(183), style=TEXT_MUTED); result.append('<0.1\n', style=TEXT_MUTED)
        return result

class AlertStreamWidget(Static):
    alerts: reactive[list[dict[str, Any]]] = reactive(list, layout=True)
    def render(self) -> Text:
        result = Text(); render_header(result, 'ALERT STREAM', 36)
        if not self.alerts: result.append(f'\n  No alerts\n  Last check: {datetime.now(UTC).strftime("%H:%M:%S")} UTC\n', style=TEXT_MUTED); return result
        for alert in self.alerts[:MAX_ALERTS_DISPLAY]:
            ts = str(alert.get('timestamp', ''))[-8:]; sev = str(alert.get('severity', 'info')).upper()[:4]
            msg = str(alert.get('message', '')); metric = str(alert.get('metric', ''))
            result.append(f'  {ts} ', style=TEXT_MUTED); result.append(f'{sev:<5}', style=f'bold {severity_color(sev.lower())}')
            if metric: result.append(f' {metric}', style=TEXT_SECONDARY)
            result.append(f'  {msg}\n', style=TEXT_PRIMARY)
        return result

class RiskScreen(BaseScreen):
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = BaseScreen.BINDINGS + [Binding('f', 'filter_alerts', 'Filter', show=False)]
    _filter_severity: reactive[str] = reactive('all')
    _loading_widget_id: ClassVar[str] = '#risk-loading'; _status_widget_id: ClassVar[str] = '#risk-status'
    _refresh_interval: ClassVar[float] = 15.0; _api_client_class: ClassVar[type] = TuiApiClient
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._alerts: list[dict[str, Any]] = []; self._ws_task: asyncio.Task[None] | None = None
    def compose(self) -> ComposeResult:
        with Vertical(id='risk-layout'):
            with Horizontal(id='risk-main'):
                with Vertical(id='risk-left'): yield RiskGaugeWidget(id='risk-gauge'); yield AlertStreamWidget(id='risk-alerts')
                with Vertical(id='risk-right'): yield DrawdownSparklineWidget(id='risk-drawdown'); yield CorrelationHeatmapWidget(id='risk-correlation')
            yield LoadingIndicator(id='risk-loading'); yield Static(self.status_text, id='risk-status')
    def on_mount(self) -> None: super().on_mount(); self._ws_task = asyncio.create_task(self._ws_risk_loop())
    async def on_unmount(self) -> None:
        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
            try: await self._ws_task
            except asyncio.CancelledError: pass
        await super().on_unmount()
    async def _ws_risk_loop(self) -> None:
        if self._api is None: return
        bo = 1.0
        while True:
            try: await self._api.ws_subscribe_risk(self._on_ws_risk_update)
            except asyncio.CancelledError: return
            except Exception as exc: logger.debug('WS risk loop error (retry in %.0fs): %s', bo, exc); await asyncio.sleep(bo); bo = min(bo * 2, 30.0)
    async def _on_ws_risk_update(self, msg: dict[str, Any]) -> None:
        try:
            cs = msg.get('composite_score')
            safe_query(self, '#risk-gauge', RiskGaugeWidget, lambda w: (setattr(w, 'composite_score', cs), setattr(w, 'strategy_count', msg.get('strategy_count', 0))))
            safe_query(self, '#risk-drawdown', DrawdownSparklineWidget, lambda w: setattr(w, 'max_drawdown', msg.get('max_drawdown')))
            safe_query(self, '#risk-correlation', CorrelationHeatmapWidget, lambda w: setattr(w, 'matrix', msg.get('correlation_matrix')))
            self.status_text = 'Live . Risk . WS updated'
            if cs is not None: self.notify(f'Risk score updated: {cs:.2f}', severity='information', timeout=2)
        except Exception as exc: logger.debug('WS risk update handler error: %s', exc)
    async def _fetch_data(self) -> None: await self._fetch_risk_data(); self._update_status_text('Live . Risk . refreshed  [r]efresh  [j/k]scroll  [f]ilter  [?]help')
    async def _fetch_risk_data(self) -> None:
        if self._api is None: return
        try:
            data = await self._api.get_risk()
            safe_query(self, '#risk-gauge', RiskGaugeWidget, lambda w: (setattr(w, 'composite_score', data.get('composite_score')), setattr(w, 'sub_scores', data.get('sub_scores', {})), setattr(w, 'strategy_count', int(data.get('strategy_count', 0)))))
            safe_query(self, '#risk-drawdown', DrawdownSparklineWidget, lambda w: (setattr(w, 'drawdown_history', data.get('drawdown_history', [])), setattr(w, 'max_drawdown', data.get('max_drawdown')), setattr(w, 'current_drawdown', data.get('current_drawdown')), setattr(w, 'recovery_periods', data.get('recovery_periods'))))
            safe_query(self, '#risk-correlation', CorrelationHeatmapWidget, lambda w: (setattr(w, 'matrix', data.get('correlation_matrix')), setattr(w, 'strategy_names', data.get('strategy_names', []))))
            self._alerts = data.get('alerts', []); self._apply_alert_filter()
        except Exception as exc:
            logger.debug('Risk data fetch failed: %s', exc)
            safe_query(self, '#risk-gauge', RiskGaugeWidget, lambda w: setattr(w, 'composite_score', None))
            safe_query(self, '#risk-drawdown', DrawdownSparklineWidget, lambda w: setattr(w, 'drawdown_history', []))
            safe_query(self, '#risk-correlation', CorrelationHeatmapWidget, lambda w: setattr(w, 'matrix', None))
            safe_query(self, '#risk-alerts', AlertStreamWidget, lambda w: setattr(w, 'alerts', []))
    def _apply_alert_filter(self) -> None:
        fl = self._alerts if self._sev == 'all' else [a for a in self._alerts if str(a.get('severity', '')).lower() == self._sev]
        safe_query(self, '#risk-alerts', AlertStreamWidget, lambda w: setattr(w, 'alerts', fl))
    def action_move_down(self) -> None: safe_query(self, '#risk-alerts', AlertStreamWidget, lambda w: w.scroll_down())
    def action_move_up(self) -> None: safe_query(self, '#risk-alerts', AlertStreamWidget, lambda w: w.scroll_up())
    def action_filter_alerts(self) -> None:
        cy = ['all', 'critical', 'warning', 'info']; self._sev = cy[(cy.index(self._sev) + 1) % 4] if self._sev in cy else 'all'
        self._apply_alert_filter(); self.notify(title='Alert Filter', message=f'Showing: {self._sev}', timeout=2)
