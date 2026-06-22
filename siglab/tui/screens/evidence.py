from __future__ import annotations

import json
import logging
import sys
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
    INFO_BLUE,
    SCROLLABLE_CSS,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    WARNING_YELLOW,
    SymbolEntry,
    TickerView,
    closes_from_klines,
    format_change,
    format_confidence,
    format_price,
    format_volume,
    friendly_error,
    safe_float,
    safe_query,
)
from siglab.tui.loading import LoadingIndicator
from siglab.tui.screens.base import BaseScreen
from siglab.tui.widgets.base import FilterableListWidget
from siglab.tui.widgets.sparkline import ohlc_summary, sparkline_text

logger = logging.getLogger(__name__)
DEMO_STEPS: list[dict[str, Any]] = [
    {
        "step": 1,
        "title": "Build SoSoValue Evidence",
        "command": "evidence-build --currency BTC --etf-type us-btc-spot --news-page-size 20 --news-pages 2 --output runs/evidence/live_sosovalue_probe_btc_pages.jsonl --summary-output runs/evidence/live_sosovalue_probe_btc_pages.summary.json --json",
        "description": "Ingest ETF inflow data and news from SoSoValue API",
        "expected": "record_count > 0, ETF + Feed records present",
    },
    {
        "step": 2,
        "title": "Probe SoDEX WebSocket",
        "command": "sodex-ws-probe --channel allBookTicker --timeout-seconds 12 --evidence-output runs/evidence/sodex_ws_evidence.jsonl --json",
        "description": "Capture public SoDEX quote evidence via WebSocket",
        "expected": "ready: true, signed: false, evidence_records_appended > 0",
    },
    {
        "step": 3,
        "title": "Render Evidence Graph",
        "command": "evidence-map --evidence runs/evidence/live_sosovalue_probe_btc_pages.jsonl --output runs/evidence/evidence_graph.html --json",
        "description": "Generate HTML evidence graph visualization",
        "expected": "HTML file exists, links are not causal claims",
    },
    {
        "step": 4,
        "title": "Generate Market Report",
        "command": "market-report --entity BTC --sosovalue-evidence runs/evidence/live_sosovalue_probe_btc_pages.jsonl --sodex-evidence runs/evidence/sodex_ws_evidence.jsonl --output runs/market_report_latest.json --html-output runs/market_report_latest.html --json",
        "description": "Operator-facing decision support from evidence",
        "expected": "status: READY_FOR_OPERATOR_REVIEW, stance, confirmations",
    },
    {
        "step": 5,
        "title": "Capture Provider Telemetry",
        "command": "telemetry-report --track trend_signals --json",
        "description": "Aggregate provider metrics and credit usage",
        "expected": "provider_metrics_status: present, latency, tokens",
    },
    {
        "step": 6,
        "title": "Verify Live Boundary",
        "command": "sodex-preflight --json",
        "description": "Check SoDEX signed-write readiness",
        "expected": "Missing credentials -> live write refused",
    },
    {
        "step": 7,
        "title": "Build Demo Manifest",
        "command": "demo-manifest --json",
        "description": "Index all demo artifacts",
        "expected": "artifact_count > 0, manifest JSON valid",
    },
]


def _kind_icon(kind: str) -> str:
    return {"source": "[*]", "entity": "[+]", "module": "[#]"}.get(kind, "[.]")


def _kind_style(kind: str) -> str:
    return {"source": INFO_BLUE, "entity": ACCENT_GREEN, "module": WARNING_YELLOW}.get(
        kind, TEXT_SECONDARY
    )


class EvidenceGraphWidget(Static):
    __slots__ = ("_graph_nodes", "_edges", "_filter_kind", "_filter_text")
    can_focus = True

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._graph_nodes: tuple[dict[str, Any], ...] = ()
        self._edges: tuple[dict[str, Any], ...] = ()
        self._filter_kind = ""
        self._filter_text = ""

    def update_graph(
        self, nodes: Sequence[dict[str, Any]], edges: Sequence[dict[str, Any]]
    ) -> None:
        self._graph_nodes = tuple(nodes)
        self._edges = tuple(edges)
        self.refresh()

    def set_filter(self, kind: str = "", text: str = "") -> None:
        self._filter_kind = kind.lower()
        self._filter_text = text.lower()
        self.refresh()

    def _filtered_nodes(self) -> list[dict[str, Any]]:
        r = list(self._graph_nodes)
        if self._filter_kind:
            r = [n for n in r if n.get("kind") == self._filter_kind]
        if self._filter_text:
            r = [n for n in r if self._filter_text in str(n.get("label", "")).lower()]
        return r

    def render(self) -> Text:
        nodes = self._filtered_nodes()
        if not nodes:
            fd = self._filter_kind or self._filter_text
            return (
                Text(f"  No matches for '{fd}'", style=WARNING_YELLOW)
                if fd
                else Text("  No evidence data available", style=TEXT_MUTED)
            )
        bk: dict[str, list[dict[str, Any]]] = {}
        for n in nodes:
            bk.setdefault(n.get("kind", "unknown"), []).append(n)
        em: dict[str, list[dict[str, Any]]] = {}
        for e in self._edges:
            em.setdefault(e.get("source", ""), []).append(e)
        ls: list[Text] = [
            Text("  -- Evidence Graph --", style=f"bold {ACCENT_GREEN}"),
            Text(""),
        ]
        for k in ("source", "entity", "module"):
            g = bk.get(k, [])
            if not g:
                continue
            st = _kind_style(k)
            ls.append(
                Text(f"  {_kind_icon(k)} {k.upper()} ({len(g)})", style=f"bold {st}")
            )
            sn = sorted(g, key=lambda n: n.get("count", 0), reverse=True)[:15]
            for i, n in enumerate(sn):
                lb = str(n.get("label", "?"))
                lb = lb[:29] + "..." if len(lb) > 30 else lb
                cn = em.get(n.get("id", ""), [])
                ln = Text()
                ln.append(
                    f"  {'└──' if i == len(sn) - 1 else '├──'} ", style=TEXT_MUTED
                )
                ln.append(f"{lb}", style=st)
                ln.append(f"  ({n.get('count', 0)})", style=TEXT_MUTED)
                if cn:
                    ln.append(f"  ->{len(cn)} links", style=INFO_BLUE)
                ls.append(ln)
            rm = len(sorted(g, key=lambda n: n.get("count", 0), reverse=True)) - len(sn)
            if rm > 0:
                ls.append(Text(f"  │  ... +{rm} more", style=TEXT_MUTED))
            ls.append(Text(""))
        sm = Text()
        sm.append(
            f"  {len(nodes)}/{len(self._graph_nodes)} nodes", style=TEXT_SECONDARY
        )
        sm.append(f"  .  {len(self._edges)} edges", style=TEXT_SECONDARY)
        ls.append(sm)
        r = Text("\n")
        for ln in ls:
            r.append_text(ln)
            r.append("\n")
        return r


class EdgeDetailWidget(Static):
    __slots__ = ("_edges",)
    can_focus = True

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._edges: tuple[dict[str, Any], ...] = ()

    def update_edges(self, edges: Sequence[dict[str, Any]]) -> None:
        self._edges = tuple(edges)
        self.refresh()

    def render(self) -> Text:
        if not self._edges:
            return Text("  No connections to display", style=TEXT_MUTED)
        ls: list[Text] = [
            Text("  -- Connections --", style=f"bold {INFO_BLUE}"),
            Text(""),
        ]
        for e in self._edges[:20]:
            src = str(e.get("source", "?"))
            tgt = str(e.get("target", "?"))
            lb = str(e.get("label", "linked"))
            ss = src.split(":", 1)[-1] if ":" in src else src
            ts = tgt.split(":", 1)[-1] if ":" in tgt else tgt
            ln = Text()
            ln.append("  ├─ ", style=TEXT_MUTED)
            ln.append(ss[:20] + "..." if len(ss) > 20 else ss, style=INFO_BLUE)
            ln.append(" -> ", style=TEXT_MUTED)
            ln.append(ts[:20] + "..." if len(ts) > 20 else ts, style=ACCENT_GREEN)
            ln.append(f"  [{lb}]", style=TEXT_SECONDARY)
            cf = e.get("confidence")
            if cf is not None:
                ln.append(" ")
                ln.append_text(format_confidence(cf))
            wn = e.get("warning")
            if wn:
                ln.append(f"  \u26a0 {wn[:40]}", style=WARNING_YELLOW)
            ls.append(ln)
        if len(self._edges) > 20:
            ls.append(Text(f"  ... +{len(self._edges) - 20} more", style=TEXT_MUTED))
        r = Text("\n")
        for ln in ls:
            r.append_text(ln)
            r.append("\n")
        return r


class DemoFlowWidget(Static):
    can_focus = True

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._current_step: int = 1
        self._step_results: dict[int, dict[str, Any]] = {}
        self._running: bool = False

    @property
    def current_step(self) -> int:
        return self._current_step

    def set_step_result(self, step: int, result: dict[str, Any]) -> None:
        self._step_results[step] = result
        self.refresh()

    def advance_step(self) -> None:
        if self._current_step < len(DEMO_STEPS):
            self._current_step += 1
            self.refresh()

    def retreat_step(self) -> None:
        if self._current_step > 1:
            self._current_step -= 1
            self.refresh()

    def set_running(self, running: bool) -> None:
        self._running = running
        self.refresh()

    def render(self) -> Text:
        ls: list[Text] = [
            Text("  -- Buildathon Demo Flow --", style=f"bold {ACCENT_GREEN}"),
            Text(""),
        ]
        for sd in DEMO_STEPS:
            sn = sd["step"]
            ti = sd["title"]
            de = sd["description"]
            res = self._step_results.get(sn)
            if res is not None:
                st = (
                    Text("\u2713 ", style=ACCENT_GREEN)
                    if res.get("returncode", -1) == 0
                    else Text("\u2717 ", style=ERROR_RED)
                )
            elif sn == self._current_step and self._running:
                st = Text("\u27f3 ", style=WARNING_YELLOW)
            elif sn == self._current_step:
                st = Text("\u25b6 ", style=INFO_BLUE)
            else:
                st = Text("\u25cb ", style=TEXT_MUTED)
            nt = Text(f"  {sn}. ", style=TEXT_MUTED)
            if sn == self._current_step:
                tt = Text(ti, style=f"bold {TEXT_PRIMARY}")
            elif res is not None:
                tt = Text(
                    ti,
                    style=ACCENT_GREEN if res.get("returncode", -1) == 0 else ERROR_RED,
                )
            else:
                tt = Text(ti, style=TEXT_SECONDARY)
            ln = Text()
            ln.append_text(st)
            ln.append_text(nt)
            ln.append_text(tt)
            ls.append(ln)
            if sn == self._current_step or res is not None:
                ls.append(Text(f"       {de}", style=TEXT_MUTED))
            if res is not None:
                rc = res.get("returncode", -1)
                so = res.get("stdout", "")
                if rc == 0 and so.strip():
                    try:
                        data = json.loads(so)
                        sp = (
                            [f"records: {data['record_count']}"]
                            if "record_count" in data
                            else []
                        )
                        if "records_appended" in data:
                            sp.append(f"appended: {data['records_appended']}")
                        if "ready" in data:
                            sp.append(f"ready: {data['ready']}")
                        if "status" in data:
                            sp.append(f"status: {data['status']}")
                        if "artifacts" in data:
                            sp.append(f"artifacts: {len(data['artifacts'])}")
                        if sp:
                            ls.append(
                                Text(f"       -> {', '.join(sp)}", style=ACCENT_GREEN)
                            )
                    except (json.JSONDecodeError, TypeError):
                        fl = so.strip().split("\n")[0][:80]
                        if fl:
                            ls.append(Text(f"       -> {fl}", style=TEXT_SECONDARY))
                elif rc != 0:
                    se = res.get("stderr", "")
                    ls.append(
                        Text(
                            f"       -> Error: {se.strip().split(chr(10))[0][:60] if se else f'exit {rc}'}",
                            style=ERROR_RED,
                        )
                    )
            ls.append(Text(""))
        ls.append(
            Text("  \u27f3 Running... (Esc to cancel)", style=WARNING_YELLOW)
            if self._running
            else Text(
                "  Enter: run step  .  n/p: next/prev  .  a: run all", style=TEXT_MUTED
            )
        )
        r = Text("\n")
        for ln in ls:
            if ln is not None:
                r.append_text(ln)
            r.append("\n")
        return r


class EvidenceScreen(BaseScreen):
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = (
        BaseScreen.BINDINGS
        + [
            Binding("tab", "switch_pane", "Switch Pane", show=True),
            Binding("enter", "run_step", "Run Step", show=True),
            Binding("n", "next_step", "Next Step", show=True),
            Binding("p", "prev_step", "Prev Step", show=True),
            Binding("a", "run_all", "Run All", show=True),
            Binding("f", "filter_source", "Sources", show=True),
            Binding("e", "filter_entity", "Entities", show=True),
            Binding("ctrl+l", "filter_clear", "Clear", show=False),
        ]
    )
    api_connected: reactive[bool] = reactive(False)
    graph_loading: reactive[bool] = reactive(False)
    demo_running: reactive[bool] = reactive(False)
    active_pane: reactive[str] = reactive("graph")
    _loading_widget_id: ClassVar[str] = "#evidence-loading"
    _status_widget_id: ClassVar[str] = "#evidence-status"
    _refresh_interval: ClassVar[float] = 30.0
    _api_client_class: ClassVar[type] = TuiApiClient
    _search_input_id: ClassVar[str] = "evidence-filter"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._graph_nodes: list[dict[str, Any]] = []
        self._edges: list[dict[str, Any]] = []
        self._current_filter: str = ""

    def compose(self) -> ComposeResult:
        with Vertical(id="evidence-layout"):
            yield Input(
                placeholder="Filter by source, entity, or type...", id="evidence-filter"
            )
            with Horizontal(id="evidence-main"):
                with Vertical(id="evidence-graph-pane"):
                    yield EvidenceGraphWidget(id="evidence-graph")
                    yield EdgeDetailWidget(id="edge-detail")
                with Vertical(id="evidence-demo-pane"):
                    yield DemoFlowWidget(id="demo-flow")
            yield LoadingIndicator(id="evidence-loading")
            yield Static("Loading evidence data...", id="evidence-status")

    async def _fetch_data(self) -> None:
        await self._refresh_graph()

    async def _refresh_graph(self) -> None:
        if self._api is None:
            return
        self.graph_loading = True
        try:
            d = await self._api.get_evidence_graph()
            nds, eds = d.get("nodes", []), d.get("edges", [])
            self.api_connected = True
            self.query_one("#evidence-graph", EvidenceGraphWidget).update_graph(
                nds, eds
            )
            self.query_one("#edge-detail", EdgeDetailWidget).update_edges(eds)
            self._graph_nodes, self._edges = nds, eds
            self._update_status()
        except Exception as exc:
            self.api_connected = False
            logger.debug("Evidence graph refresh failed: %s", exc)
            self._update_status_error(friendly_error(exc))
        finally:
            self.graph_loading = False

    def _update_status(self) -> None:
        ft = f"  Filter: {self._current_filter}" if self._current_filter else ""
        self._update_status_text(
            f"  {len(self._graph_nodes)} nodes  {len(self._edges)} edges{ft}  {'Connected' if self.api_connected else 'Disconnected'}  [r]efresh  [/]search  [tab]switch  [n/p]step  [?]help"
        )

    def action_switch_pane(self) -> None:
        if self.active_pane == "graph":
            self.active_pane = "demo"
            self.query_one("#demo-flow", DemoFlowWidget).focus()
        else:
            self.active_pane = "graph"
            self.query_one("#evidence-graph", EvidenceGraphWidget).focus()

    def _apply_filter(self, kind: str) -> None:
        g = self.query_one("#evidence-graph", EvidenceGraphWidget)
        g.set_filter(kind=kind) if kind else g.set_filter()
        self._current_filter = kind
        self._update_status()

    def action_filter_source(self) -> None:
        self._apply_filter("source")

    def action_filter_entity(self) -> None:
        self._apply_filter("entity")

    def action_filter_clear(self) -> None:
        self._apply_filter("")

    def action_next_step(self) -> None:
        self.query_one("#demo-flow", DemoFlowWidget).advance_step()

    def action_prev_step(self) -> None:
        self.query_one("#demo-flow", DemoFlowWidget).retreat_step()

    async def _run_demo_step(self, sd: dict[str, Any]) -> int:
        import asyncio

        d = self.query_one("#demo-flow", DemoFlowWidget)
        sn, args = sd["step"], sd["command"].split()
        try:
            p = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "siglab.cli",
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                ob, eb = await asyncio.wait_for(p.communicate(), timeout=60.0)
            except asyncio.TimeoutError:
                p.kill()
                await p.wait()
                raise
            rc = p.returncode or 0
            d.set_step_result(
                sn,
                {
                    "returncode": rc,
                    "stdout": ob.decode("utf-8", errors="replace"),
                    "stderr": eb.decode("utf-8", errors="replace"),
                },
            )
            return rc
        except Exception as exc:
            d.set_step_result(sn, {"returncode": -1, "stdout": "", "stderr": str(exc)})
            logger.debug("Demo step %s failed: %s", sn, exc)
            return -1

    async def action_run_step(self) -> None:
        d = self.query_one("#demo-flow", DemoFlowWidget)
        sd = next((s for s in DEMO_STEPS if s["step"] == d.current_step), None)
        if sd is None:
            return
        d.set_running(True)
        self.demo_running = True
        self._update_status_running(sd["title"])
        try:
            if await self._run_demo_step(sd) == 0:
                d.advance_step()
        finally:
            d.set_running(False)
            self.demo_running = False
            self._update_status()

    async def action_run_all(self) -> None:
        d = self.query_one("#demo-flow", DemoFlowWidget)
        self.demo_running = True
        for sd in DEMO_STEPS:
            if sd["step"] in d._step_results:
                continue
            d._current_step = sd["step"]
            d.set_running(True)
            self._update_status_running(sd["title"])
            await self._run_demo_step(sd)
        d.set_running(False)
        self.demo_running = False
        self._update_status()

    def _update_status_running(self, step_title: str) -> None:
        self._update_status_text(f"  Running: {step_title}...")

    def on_input_changed(self, event: Input.Changed) -> None:
        v = event.value.strip()
        self._current_filter = v
        g = self.query_one("#evidence-graph", EvidenceGraphWidget)
        if v:
            g.set_filter(kind=v) if v.lower() in (
                "source",
                "entity",
                "module",
            ) else g.set_filter(text=v)
        else:
            g.set_filter()
        self._update_status()


DEFAULT_SYMBOL = "BTC-USD"
DEFAULT_INTERVAL = "1h"
KLINES_LIMIT = 60
ORDERBOOK_LIMIT = 15


class SymbolListWidget(FilterableListWidget[SymbolEntry]):
    __slots__ = ()
    symbols: reactive[list[SymbolEntry]] = reactive(list, layout=True)
    _items_reactive: ClassVar[str] = "symbols"
    DEFAULT_CSS = f"SymbolListWidget {{ width: 28; min-width: 22; height: 1fr; {SCROLLABLE_CSS} }}"

    @staticmethod
    def _to_symbol_entry(item: Any) -> SymbolEntry:
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
        raise TypeError(f"Cannot convert {type(item).__name__} to SymbolEntry")

    def set_symbols(self, entries: Sequence[SymbolEntry | dict[str, Any]]) -> None:
        self.set_data([self._to_symbol_entry(e) for e in entries])

    def _matches(self, item: SymbolEntry) -> bool:
        ft = self._filter_text.upper().strip()
        return True if not ft else ft in item.name.upper() or ft in item.symbol.upper()

    def _render_item(self, item: SymbolEntry, index: int, is_selected: bool) -> Text:
        return (
            Text(f"  {item.name:<18}", style=f"bold #000000 on {ACCENT_GREEN}")
            if is_selected
            else Text(f"  {item.name:<18}", style=TEXT_SECONDARY)
        )

    def get_selected_symbol(self) -> str | None:
        if self.symbols and 0 <= self.selected_index < len(self.symbols):
            return self.symbols[self.selected_index].name


class KlinesChartWidget(Static):
    __slots__ = ("_closes_cache",)
    candles: reactive[list[dict[str, Any]]] = reactive(list, layout=True)
    symbol: reactive[str] = reactive(DEFAULT_SYMBOL)
    DEFAULT_CSS = "KlinesChartWidget { height: 1fr; min-height: 6; padding: 0 1; background: #0a0a0a; }"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._closes_cache: tuple[float, ...] = ()

    def set_candles(self, klines: list[dict[str, Any]]) -> None:
        self.candles = klines
        self._closes_cache = closes_from_klines(klines)

    def render(self) -> Text:
        r = Text(f" {self.symbol} ", style=f"bold {ACCENT_GREEN}")
        if self.candles:
            r.append(
                f"  {format_price(safe_float(self.candles[-1].get('close', 0)) or 0.0)}  ",
                style=f"bold {TEXT_PRIMARY}",
            )
        r.append("\n\n")
        if not self.candles:
            r.append("  Loading chart data...", style=TEXT_MUTED)
            return r
        cs = (
            self._closes_cache
            if self._closes_cache
            else closes_from_klines(self.candles)
        )
        r.append("  ")
        r.append_text(sparkline_text(cs, width=max(20, min(80, len(cs)))))
        r.append("\n\n  ")
        r.append(ohlc_summary(self.candles), style=TEXT_MUTED)
        r.append("\n")
        return r


class TickerTableWidget(Static):
    tickers: reactive[list[dict[str, Any]]] = reactive(list, layout=True)
    DEFAULT_CSS = (
        f"TickerTableWidget {{ height: auto; max-height: 12; {SCROLLABLE_CSS} }}"
    )

    def render(self) -> Text:
        if not self.tickers:
            return Text("  No data available", style=TEXT_MUTED)
        ls = Text()
        ls.append(
            "  SYMBOL          PRICE          24h CHG      VOLUME\n", style=TEXT_MUTED
        )
        ls.append("  " + "-" * 60 + "\n", style=BORDER_DIM)
        for t in self.tickers[:20]:
            sy = str(t.get("symbol", "?"))
            pr = safe_float(t.get("lastPrice", t.get("last_price", 0))) or 0.0
            cp_ = (
                safe_float(t.get("priceChangePercent", t.get("price_change_pct", 0)))
                or 0.0
            )
            vo = safe_float(t.get("volume", t.get("volume_24h", 0))) or 0.0
            ls.append(f"  {sy:<15}", style=TEXT_SECONDARY)
            ls.append(f"{format_price(pr, sy):>14}  ", style=TEXT_PRIMARY)
            ls.append_text(format_change(cp_))
            ls.append(f"   {format_volume(vo):>10}", style="info_blue")
            ls.append("\n")
        return ls


class OrderBookWidget(Static):
    bids: reactive[tuple[list[Any], ...]] = reactive(tuple, layout=True)
    asks: reactive[tuple[list[Any], ...]] = reactive(tuple, layout=True)
    symbol: reactive[str] = reactive(DEFAULT_SYMBOL)
    DEFAULT_CSS = "OrderBookWidget { height: 1fr; min-height: 8; padding: 0 1; background: #0a0a0a; }"

    def render(self) -> Text:
        r = Text(f" ORDER BOOK -- {self.symbol}\n", style=f"bold {TEXT_PRIMARY}")
        if not self.bids and not self.asks:
            return r + Text("  No data available\n", style=TEXT_MUTED)
        r.append("  BIDS (buys)           |  ASKS (sells)\n", style=TEXT_MUTED)
        r.append("  " + "-" * 23 + "|" + "-" * 23 + "\n", style=BORDER_DIM)
        sz = []
        for x in [*self.bids[:ORDERBOOK_LIMIT], *self.asks[:ORDERBOOK_LIMIT]]:
            try:
                sz.append(float(x[1]) if len(x) > 1 else 0)
            except (ValueError, IndexError):
                pass
        mx = max(sz) if sz else 1.0
        bw = 10
        for i in range(min(max(len(self.bids), len(self.asks)), ORDERBOOK_LIMIT)):
            if i < len(self.bids):
                b = self.bids[i]
                try:
                    bp, bs_ = float(b[0]), float(b[1]) if len(b) > 1 else 0
                except (ValueError, IndexError):
                    bp, bs_ = 0, 0
                bl = int(bs_ / mx * bw) if mx > 0 else 0
                r.append(f"  {'█' * bl + ' ' * (bw - bl)}", style=ACCENT_GREEN)
                r.append(f" {bp:>10,.2f} ", style=TEXT_PRIMARY)
                r.append(f"{bs_:>8.2f}", style=TEXT_SECONDARY)
            else:
                r.append(" " * 36)
            r.append(" | ", style=BORDER_DIM)
            if i < len(self.asks):
                a = self.asks[i]
                try:
                    ap, as_ = float(a[0]), float(a[1]) if len(a) > 1 else 0
                except (ValueError, IndexError):
                    ap, as_ = 0, 0
                bl = int(as_ / mx * bw) if mx > 0 else 0
                r.append(f"{as_:>8.2f}", style=TEXT_SECONDARY)
                r.append(f" {ap:>10,.2f} ", style=TEXT_PRIMARY)
                r.append(f"{'█' * bl + ' ' * (bw - bl)}", style=ERROR_RED)
            r.append("\n")
        if self.bids and self.asks:
            try:
                bb, ba = float(self.bids[0][0]), float(self.asks[0][0])
                r.append(
                    f"\n  Spread: {ba - bb:,.2f} ({(ba - bb) / ba * 100 if ba else 0:.3f}%)\n",
                    style=TEXT_MUTED,
                )
            except (ValueError, IndexError):
                pass
        return r


class MarketScreen(BaseScreen):
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = (
        BaseScreen.BINDINGS + [Binding("enter", "select_symbol", "Select", show=False)]
    )
    current_symbol: reactive[str] = reactive(DEFAULT_SYMBOL)
    _loading_widget_id: ClassVar[str] = "#market-loading"
    _status_widget_id: ClassVar[str] = "#market-status"
    _refresh_interval: ClassVar[float] = 30.0
    _api_client_class: ClassVar[type] = TuiApiClient
    _search_input_id: ClassVar[str] = "symbol-search"
    _search_list_id: ClassVar[str] = "symbol-list"

    def compose(self) -> ComposeResult:
        with Vertical(id="market-layout"):
            yield Input(placeholder="Search symbols...", id="symbol-search")
            with Horizontal(id="market-main"):
                yield SymbolListWidget(id="symbol-list")
                with Vertical(id="market-detail"):
                    yield KlinesChartWidget(id="klines-chart")
                    yield TickerTableWidget(id="ticker-table")
                    yield OrderBookWidget(id="order-book")
            yield LoadingIndicator(id="market-loading")
            yield Static(self.status_text, id="market-status")

    async def _fetch_data(self) -> None:
        sc = await self._fetch_multiple(
            self._fetch_tickers(),
            self._fetch_klines(),
            self._fetch_orderbook(),
            label="market",
        )
        if sc == 3:
            self._update_status_text(
                f"Live . {self.current_symbol} . refreshed  [r]efresh  [/]search  [j/k]nav  [?]help"
            )
        elif sc > 0:
            self._update_status_text(
                f"Partial update ({sc}/3) . {self.current_symbol}  [r]etry"
            )
        else:
            self._update_status_error("Cannot reach API server")

    async def _fetch_tickers(self) -> None:
        if self._api is None:
            return
        d = await self._api.get_market_tickers()
        ts = d.get("tickers", [])
        if ts:
            es = sorted(
                (SymbolEntry.from_ticker(TickerView.from_dict(t)) for t in ts),
                key=lambda e: abs(e.price * e.change_pct),
                reverse=True,
            )
            safe_query(
                self, "#symbol-list", SymbolListWidget, lambda w: w.set_symbols(es)
            )
            safe_query(
                self,
                "#ticker-table",
                TickerTableWidget,
                lambda w: setattr(w, "tickers", ts),
            )

    async def _fetch_klines(self) -> None:
        if self._api is None:
            return
        d = await self._api.get_market_klines(
            self.current_symbol, DEFAULT_INTERVAL, KLINES_LIMIT
        )
        ks = d.get("klines", [])
        safe_query(
            self,
            "#klines-chart",
            KlinesChartWidget,
            lambda w: [setattr(w, "symbol", self.current_symbol), w.set_candles(ks)],
        )

    async def _fetch_orderbook(self) -> None:
        if self._api is None:
            return
        d = await self._api.get_market_orderbook(self.current_symbol, ORDERBOOK_LIMIT)
        safe_query(
            self,
            "#order-book",
            OrderBookWidget,
            lambda w: [
                setattr(w, attr, val)
                for attr, val in (
                    ("symbol", self.current_symbol),
                    ("bids", tuple(d.get("bids", []))),
                    ("asks", tuple(d.get("asks", []))),
                )
            ],
        )

    def on_input_changed(self, event: Input.Changed) -> None:
        if not self._on_search_input_changed(event):
            pass

    def on_input_submitted(self, event: Input.Submitted) -> None:
        lw = safe_query(self, "#symbol-list", SymbolListWidget)
        if lw:
            sel = lw.get_selected_symbol()
            if sel:
                self._select_symbol(sel)

    def action_select_symbol(self) -> None:
        lw = safe_query(self, "#symbol-list", SymbolListWidget)
        if lw:
            sel = lw.get_selected_symbol()
            if sel:
                self._select_symbol(sel)

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
