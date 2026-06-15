"""Evidence Graph and Demo Flow TUI screen for SigLab.

Displays:
- Evidence graph browser (nodes/edges in ASCII tree)
- Filter evidence by type, source, currency
- Interactive buildathon demo flow walkthrough
- Each demo step shows command and output

Connects to the FastAPI dashboard via TuiApiClient
and uses CLI bridge for demo step execution.
"""

from __future__ import annotations

import json
import logging
from typing import Any, ClassVar, Sequence

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import Input, Static

from siglab.tui.api_client import TuiApiClient
from siglab.tui.cli_bridge import run_cli
from siglab.tui.formatting import (
    ACCENT_GREEN,
    ERROR_RED,
    INFO_BLUE,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    WARNING_YELLOW,
    format_confidence,
    friendly_error,
)
from siglab.tui.loading import LoadingIndicator
from siglab.tui.screens.base import BaseScreen

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────

# Demo steps from docs/demo-script.md
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
        "expected": "Missing credentials → live write refused",
    },
    {
        "step": 7,
        "title": "Build Demo Manifest",
        "command": "demo-manifest --output runs/demo_manifest_latest.json --html-output runs/demo_manifest_latest.html --json",
        "description": "Index all demo artifacts",
        "expected": "Artifact paths, readiness flags, red_flags",
    },
    {
        "step": 8,
        "title": "Open Operator Board",
        "command": "wave-status --wave-number 1 --phase demo --status running --goal show-input-to-action-flow --agents operator,dashboard,hardening --outputs market-report,ops-board,preflight --blockers signed-SoDEX-unproven --validation-status targeted_pass --next-decision continue-demo-refresh",
        "description": "Record wave status for ops board",
        "expected": "Wave status recorded for dashboard display",
    },
]


# ── Helpers ──────────────────────────────────────────────────────────


def _kind_icon(kind: str) -> str:
    """Return an icon for a node kind."""
    return {"source": "[*]", "entity": "[+]", "module": "[#]"}.get(kind, "[.]")


def _kind_style(kind: str) -> str:
    """Return a Rich style for a node kind."""
    return {
        "source": INFO_BLUE,
        "entity": ACCENT_GREEN,
        "module": WARNING_YELLOW,
    }.get(kind, TEXT_SECONDARY)


# ── Evidence Graph Widget ────────────────────────────────────────────


class EvidenceGraphWidget(Static):
    """Displays evidence nodes and edges in an ASCII tree view.

    Zero-copy: stores references to node and edge lists from the API
    response.  Filtering produces a new list of references (no dict
    copies).
    """

    __slots__ = ("_graph_nodes", "_edges", "_filter_kind", "_filter_text")

    can_focus = True

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._graph_nodes: tuple[dict[str, Any], ...] = ()
        self._edges: tuple[dict[str, Any], ...] = ()
        self._filter_kind: str = ""
        self._filter_text: str = ""

    def update_graph(self, nodes: Sequence[dict[str, Any]], edges: Sequence[dict[str, Any]]) -> None:
        """Store nodes and edges as immutable tuples.

        Accepts any Sequence (list, tuple).  Individual dicts are
        shared by reference — no data is copied.
        """
        self._graph_nodes = tuple(nodes)
        self._edges = tuple(edges)
        self.refresh()

    def set_filter(self, kind: str = "", text: str = "") -> None:
        """Apply filters to the graph view."""
        self._filter_kind = kind.lower()
        self._filter_text = text.lower()
        self.refresh()

    def _filtered_nodes(self) -> list[dict[str, Any]]:
        """Return nodes matching current filters."""
        result: list[dict[str, Any]] = list(self._graph_nodes)
        if self._filter_kind:
            result = [n for n in result if n.get("kind") == self._filter_kind]
        if self._filter_text:
            result = [
                n for n in result
                if self._filter_text in str(n.get("label", "")).lower()
            ]
        return result

    def render(self) -> Text:
        """Render the graph as Rich Text."""
        nodes = self._filtered_nodes()
        if not nodes:
            if self._filter_kind or self._filter_text:
                filter_desc = self._filter_kind or self._filter_text
                return Text(f"  No matches for '{filter_desc}'", style=WARNING_YELLOW)
            return Text("  No evidence data available", style=TEXT_MUTED)

        # Group nodes by kind
        by_kind: dict[str, list[dict[str, Any]]] = {}
        for node in nodes:
            kind = node.get("kind", "unknown")
            by_kind.setdefault(kind, []).append(node)

        # Build edge lookup
        edge_map: dict[str, list[dict[str, Any]]] = {}
        for edge in self._edges:
            src = edge.get("source", "")
            edge_map.setdefault(src, []).append(edge)

        lines: list[Text] = []
        lines.append(Text("  ── Evidence Graph ──", style=f"bold {ACCENT_GREEN}"))
        lines.append(Text(""))

        for kind in ("source", "entity", "module"):
            group = by_kind.get(kind, [])
            if not group:
                continue
            icon = _kind_icon(kind)
            style = _kind_style(kind)
            lines.append(Text(f"  {icon} {kind.upper()} ({len(group)})", style=f"bold {style}"))

            sorted_nodes = sorted(group, key=lambda n: n.get("count", 0), reverse=True)
            shown = sorted_nodes[:15]
            for i, node in enumerate(shown):
                is_last = i == len(shown) - 1
                connector = "└──" if is_last else "├──"
                label = str(node.get("label", "?"))
                if len(label) > 30:
                    label = label[:29] + "…"
                count = node.get("count", 0)
                node_id = node.get("id", "")
                connected = edge_map.get(node_id, [])
                edge_count = len(connected)

                line = Text()
                line.append(f"  {connector} ", style=TEXT_MUTED)
                line.append(f"{label}", style=style)
                line.append(f"  ({count})", style=TEXT_MUTED)
                if edge_count > 0:
                    line.append(f"  →{edge_count} links", style=INFO_BLUE)
                lines.append(line)

            remaining = len(sorted_nodes) - len(shown)
            if remaining > 0:
                lines.append(Text(f"  │  ... +{remaining} more", style=TEXT_MUTED))
            lines.append(Text(""))

        # Summary
        total_nodes = len(self._graph_nodes)
        total_edges = len(self._edges)
        filtered = len(nodes)
        summary = Text()
        summary.append(f"  {filtered}/{total_nodes} nodes", style=TEXT_SECONDARY)
        summary.append(f"  •  {total_edges} edges", style=TEXT_SECONDARY)
        lines.append(summary)

        result = Text("\n")
        for line in lines:
            result.append_text(line)
            result.append("\n")
        return result


# ── Edge Detail Widget ───────────────────────────────────────────────


class EdgeDetailWidget(Static):
    """Shows edge/connection details for selected evidence.

    Zero-copy: stores a reference to the edges tuple from the graph
    widget — shared, not copied.
    """

    __slots__ = ("_edges",)

    can_focus = True

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._edges: tuple[dict[str, Any], ...] = ()

    def update_edges(self, edges: Sequence[dict[str, Any]]) -> None:
        """Store the edges as an immutable tuple."""
        self._edges = tuple(edges)
        self.refresh()

    def render(self) -> Text:
        """Render edges as Rich Text."""
        if not self._edges:
            return Text("  No connections to display", style=TEXT_MUTED)

        lines: list[Text] = []
        lines.append(Text("  ── Connections ──", style=f"bold {INFO_BLUE}"))
        lines.append(Text(""))

        for edge in self._edges[:20]:
            src = str(edge.get("source", "?"))
            tgt = str(edge.get("target", "?"))
            label = str(edge.get("label", "linked"))
            conf = edge.get("confidence")
            warning = edge.get("warning")

            # Shorten IDs for display with ellipsis
            src_short = src.split(":", 1)[-1] if ":" in src else src
            tgt_short = tgt.split(":", 1)[-1] if ":" in tgt else tgt
            src_display = src_short[:20] + "…" if len(src_short) > 20 else src_short
            tgt_display = tgt_short[:20] + "…" if len(tgt_short) > 20 else tgt_short

            line = Text()
            line.append("  ├─ ", style=TEXT_MUTED)
            line.append(src_display, style=INFO_BLUE)
            line.append(" ─→ ", style=TEXT_MUTED)
            line.append(tgt_display, style=ACCENT_GREEN)
            line.append(f"  [{label}]", style=TEXT_SECONDARY)
            if conf is not None:
                line.append(" ")
                line.append_text(format_confidence(conf))
            if warning:
                line.append(f"  ⚠ {warning[:40]}", style=WARNING_YELLOW)
            lines.append(line)

        if len(self._edges) > 20:
            lines.append(Text(f"  ... +{len(self._edges) - 20} more", style=TEXT_MUTED))

        result = Text("\n")
        for line in lines:
            result.append_text(line)
            result.append("\n")
        return result


# ── Demo Flow Widget ─────────────────────────────────────────────────


class DemoFlowWidget(Static):
    """Interactive buildathon demo flow with step-by-step execution."""

    can_focus = True

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._current_step: int = 1  # 1-indexed to match DEMO_STEPS
        self._step_results: dict[int, dict[str, Any]] = {}
        self._running: bool = False

    @property
    def current_step(self) -> int:
        return self._current_step

    def set_step_result(self, step: int, result: dict[str, Any]) -> None:
        """Store the result of a demo step execution."""
        self._step_results[step] = result
        self.refresh()

    def advance_step(self) -> None:
        """Move to the next demo step."""
        if self._current_step < len(DEMO_STEPS):
            self._current_step += 1
            self.refresh()

    def retreat_step(self) -> None:
        """Move to the previous demo step."""
        if self._current_step > 1:
            self._current_step -= 1
            self.refresh()

    def set_running(self, running: bool) -> None:
        """Set whether a step is currently executing."""
        self._running = running
        self.refresh()

    def render(self) -> Text:
        """Render the demo flow as Rich Text."""
        lines: list[Text] = []
        lines.append(Text("  ── Buildathon Demo Flow ──", style=f"bold {ACCENT_GREEN}"))
        lines.append(Text(""))

        for step_data in DEMO_STEPS:
            step_num = step_data["step"]
            title = step_data["title"]
            desc = step_data["description"]
            result = self._step_results.get(step_num)

            # Status indicator
            if result is not None:
                rc = result.get("returncode", -1)
                if rc == 0:
                    status = Text("✓ ", style=ACCENT_GREEN)
                else:
                    status = Text("✗ ", style=ERROR_RED)
            elif step_num == self._current_step and self._running:
                status = Text("⟳ ", style=WARNING_YELLOW)
            elif step_num == self._current_step:
                status = Text("▶ ", style=INFO_BLUE)
            else:
                status = Text("○ ", style=TEXT_MUTED)

            # Step number
            num_text = Text(f"  {step_num}. ", style=TEXT_MUTED)

            # Title
            if step_num == self._current_step:
                title_text = Text(title, style=f"bold {TEXT_PRIMARY}")
            elif result is not None:
                rc = result.get("returncode", -1)
                title_style = ACCENT_GREEN if rc == 0 else ERROR_RED
                title_text = Text(title, style=title_style)
            else:
                title_text = Text(title, style=TEXT_SECONDARY)

            line = Text()
            line.append_text(status)
            line.append_text(num_text)
            line.append_text(title_text)
            lines.append(line)

            # Description (always shown for current step, or if has result)
            if step_num == self._current_step or result is not None:
                desc_text = Text(f"       {desc}", style=TEXT_MUTED)
                lines.append(desc_text)

            # Show result summary if available
            if result is not None:
                rc = result.get("returncode", -1)
                stdout = result.get("stdout", "")
                if rc == 0 and stdout.strip():
                    try:
                        data = json.loads(stdout)
                        # Show key fields
                        summary_parts = []
                        if "record_count" in data:
                            summary_parts.append(f"records: {data['record_count']}")
                        if "records_appended" in data:
                            summary_parts.append(f"appended: {data['records_appended']}")
                        if "ready" in data:
                            summary_parts.append(f"ready: {data['ready']}")
                        if "status" in data:
                            summary_parts.append(f"status: {data['status']}")
                        if "artifacts" in data:
                            summary_parts.append(f"artifacts: {len(data['artifacts'])}")
                        if summary_parts:
                            result_line = Text(
                                f"       → {', '.join(summary_parts)}",
                                style=ACCENT_GREEN,
                            )
                            lines.append(result_line)
                    except (json.JSONDecodeError, TypeError):
                        # Show first line of output
                        first_line = stdout.strip().split("\n")[0][:80]
                        if first_line:
                            result_line = Text(f"       → {first_line}", style=TEXT_SECONDARY)
                            lines.append(result_line)
                elif rc != 0:
                    stderr = result.get("stderr", "")
                    err_msg = stderr.strip().split("\n")[0][:60] if stderr else f"exit {rc}"
                    err_line = Text(f"       → Error: {err_msg}", style=ERROR_RED)
                    lines.append(err_line)

            lines.append(Text(""))

        # Navigation hint
        if self._running:
            lines.append(Text("  ⟳ Running... (Esc to cancel)", style=WARNING_YELLOW))
        else:
            lines.append(Text("  Enter: run step  •  n/p: next/prev  •  a: run all", style=TEXT_MUTED))

        rendered = Text("\n")
        for line in lines:
            if line is not None:
                rendered.append_text(line)
            rendered.append("\n")
        return rendered


# ── Main Screen ──────────────────────────────────────────────────────


class EvidenceScreen(BaseScreen):
    """Evidence Graph and Demo Flow screen.

    Two-pane layout:
    - Left: Evidence graph browser with filters
    - Right: Interactive demo flow walkthrough
    """

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = BaseScreen.BINDINGS + [
        Binding("tab", "switch_pane", "Switch Pane", show=True),
        Binding("enter", "run_step", "Run Step", show=True),
        Binding("n", "next_step", "Next Step", show=True),
        Binding("p", "prev_step", "Prev Step", show=True),
        Binding("a", "run_all", "Run All", show=True),
        Binding("f", "filter_source", "Sources", show=True),
        Binding("e", "filter_entity", "Entities", show=True),
        Binding("ctrl+l", "filter_clear", "Clear", show=False),
    ]

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
            # Filter bar
            yield Input(
                placeholder="Filter by source, entity, or type...",
                id="evidence-filter",
            )
            # Main two-pane layout
            with Horizontal(id="evidence-main"):
                # Left: Evidence graph
                with Vertical(id="evidence-graph-pane"):
                    yield EvidenceGraphWidget(id="evidence-graph")
                    yield EdgeDetailWidget(id="edge-detail")
                # Right: Demo flow
                with Vertical(id="evidence-demo-pane"):
                    yield DemoFlowWidget(id="demo-flow")
            # Loading indicator + status bar
            yield LoadingIndicator(id="evidence-loading")
            yield Static("Loading evidence data...", id="evidence-status")

    async def _fetch_data(self) -> None:
        """Fetch evidence graph data."""
        await self._refresh_graph()

    async def _refresh_graph(self) -> None:
        """Fetch evidence graph data from the API.

        Zero-copy: the API response nodes/edges lists are converted
        to tuples once in the graph widget; the edge widget shares
        the same tuple reference.
        """
        if self._api is None:
            return
        self.graph_loading = True
        try:
            data = await self._api.get_evidence_graph()
            nodes = data.get("nodes", [])
            edges = data.get("edges", [])
            self.api_connected = True

            graph_widget = self.query_one("#evidence-graph", EvidenceGraphWidget)
            graph_widget.update_graph(nodes, edges)

            # Share the same edges tuple with the edge detail widget
            edge_widget = self.query_one("#edge-detail", EdgeDetailWidget)
            edge_widget.update_edges(graph_widget._edges)

            # Store reference for status display
            self._graph_nodes = nodes
            self._edges = edges

            self._update_status()
        except Exception as exc:
            self.api_connected = False
            logger.debug("Evidence graph refresh failed: %s", exc)
            self._update_status_error(friendly_error(exc))
        finally:
            self.graph_loading = False

    def _update_status(self) -> None:
        """Update the status bar with current state."""
        node_count = len(self._graph_nodes)
        edge_count = len(self._edges)
        filter_text = f"  Filter: {self._current_filter}" if self._current_filter else ""
        conn = "Connected" if self.api_connected else "Disconnected"
        hints = "  [r]efresh  [/]search  [tab]switch  [n/p]step  [?]help"
        self._update_status_text(
            f"  {node_count} nodes  {edge_count} edges{filter_text}  {conn}{hints}"
        )

    # ── Actions ───────────────────────────────────────────────────────

    def action_switch_pane(self) -> None:
        """Toggle focus between graph and demo panes."""
        if self.active_pane == "graph":
            self.active_pane = "demo"
            self.query_one("#demo-flow", DemoFlowWidget).focus()
        else:
            self.active_pane = "graph"
            self.query_one("#evidence-graph", EvidenceGraphWidget).focus()

    def _apply_filter(self, kind: str) -> None:
        """Apply a kind filter to the graph (or clear when ``kind`` is empty)."""
        graph = self.query_one("#evidence-graph", EvidenceGraphWidget)
        if kind:
            graph.set_filter(kind=kind)
        else:
            graph.set_filter()
        self._current_filter = kind
        self._update_status()

    def action_filter_source(self) -> None:
        """Filter graph to show only source nodes."""
        self._apply_filter("source")

    def action_filter_entity(self) -> None:
        """Filter graph to show only entity nodes."""
        self._apply_filter("entity")

    def action_filter_clear(self) -> None:
        """Clear all filters."""
        self._apply_filter("")

    def action_next_step(self) -> None:
        """Move to next demo step."""
        demo = self.query_one("#demo-flow", DemoFlowWidget)
        demo.advance_step()

    def action_prev_step(self) -> None:
        """Move to previous demo step."""
        demo = self.query_one("#demo-flow", DemoFlowWidget)
        demo.retreat_step()

    async def _run_demo_step(self, step_data: dict[str, Any]) -> int:
        """Execute a single demo step and record the result on the widget.

        Returns the CLI returncode (or ``-1`` on exception).
        """
        demo = self.query_one("#demo-flow", DemoFlowWidget)
        step_num = step_data["step"]
        args = step_data["command"].split()
        try:
            result = await run_cli(*args, timeout=60.0)
            demo.set_step_result(step_num, {
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            })
            return result.returncode
        except Exception as exc:
            demo.set_step_result(step_num, {
                "returncode": -1,
                "stdout": "",
                "stderr": str(exc),
            })
            logger.debug("Demo step %s failed: %s", step_num, exc)
            return -1

    async def action_run_step(self) -> None:
        """Run the current demo step."""
        demo = self.query_one("#demo-flow", DemoFlowWidget)
        step_num = demo.current_step
        step_data = next((s for s in DEMO_STEPS if s["step"] == step_num), None)
        if step_data is None:
            return

        demo.set_running(True)
        self.demo_running = True
        self._update_status_running(step_data["title"])

        try:
            returncode = await self._run_demo_step(step_data)
            if returncode == 0:
                demo.advance_step()
        finally:
            demo.set_running(False)
            self.demo_running = False
            self._update_status()

    async def action_run_all(self) -> None:
        """Run all remaining demo steps sequentially."""
        demo = self.query_one("#demo-flow", DemoFlowWidget)
        self.demo_running = True

        for step_data in DEMO_STEPS:
            step_num = step_data["step"]
            # Skip already-completed steps
            if step_num in demo._step_results:
                continue

            demo._current_step = step_num
            demo.set_running(True)
            self._update_status_running(step_data["title"])
            await self._run_demo_step(step_data)

        demo.set_running(False)
        self.demo_running = False
        self._update_status()

    def _update_status_running(self, step_title: str) -> None:
        """Update status bar while a step is running."""
        self._update_status_text(f"  Running: {step_title}...")

    # ── Event Handlers ────────────────────────────────────────────────

    def on_input_changed(self, event: Input.Changed) -> None:
        """Handle filter input changes."""
        value = event.value.strip()
        self._current_filter = value
        graph = self.query_one("#evidence-graph", EvidenceGraphWidget)
        if value:
            # Try to match as kind first, then as text filter
            if value.lower() in ("source", "entity", "module"):
                graph.set_filter(kind=value.lower())
            else:
                graph.set_filter(text=value)
        else:
            graph.set_filter()
        self._update_status()
