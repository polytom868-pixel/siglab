"""Telemetry and Run Browser TUI screen for SigLab.

Displays:
- Experiment run list with metadata (spec_hash, track, family, score, status)
- Telemetry data: provider metrics, credit usage, latency
- Run comparison highlighting differences between selected runs
- Filters: date range, track, status

Connects to FastAPI /ops-board and /skill-report endpoints.
Auto-refreshes every 30 seconds.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, ClassVar

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
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    WARNING_YELLOW,
    bar_gauge,
    confidence_color,
    format_count,
    format_date,
    format_latency,
    format_score,
    format_status,
    render_list_item,
    safe_query,
    safe_update_text,
    truncate,
)
from siglab.tui.loading import LoadingIndicator
from siglab.tui.screens.base import BaseScreen, render_header
from siglab.tui.widgets.base import ComparisonWidget, FilterableListWidget

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────

MAX_COMPARE: int = 4

# Date range filter presets
DATE_RANGE_FILTERS: list[str] = ["ALL", "7d", "30d", "TODAY"]

# Status filter options
STATUS_FILTERS: list[str] = ["ALL", "PASSED", "FAILED", "RUNNING", "PENDING"]


# ══════════════════════════════════════════════════════════════════════
# Run List Widget
# ══════════════════════════════════════════════════════════════════════


class TelemetryRunListWidget(FilterableListWidget):
    """Vertical list of experiment runs with selection and multi-select."""

    __slots__ = ("_status_filter", "_track_filter", "_date_range")

    runs: reactive[list[dict[str, Any]]] = reactive(list, layout=True)
    _items_reactive: ClassVar[str] = "runs"
    _multi_select: ClassVar[bool] = True
    _max_select: ClassVar[int] = MAX_COMPARE

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._status_filter: str = "ALL"
        self._track_filter: str = "ALL"
        self._date_range: str = "ALL"

    def set_runs(self, runs: list[dict[str, Any]]) -> None:
        self.set_data(runs)

    def set_status_filter(self, status: str) -> None:
        self._status_filter = status.upper().strip()
        self._apply_filters()

    def set_track_filter(self, track: str) -> None:
        self._track_filter = track.upper().strip()
        self._apply_filters()

    def set_date_range(self, date_range: str) -> None:
        self._date_range = date_range.upper().strip()
        self._apply_filters()

    def _matches(self, item: dict[str, Any]) -> bool:
        ft = self._filter_text
        sf = self._status_filter
        tf = self._track_filter
        dr = self._date_range

        if ft:
            if not (
                ft in str(item.get("spec_hash", "")).lower()
                or ft in str(item.get("track", "")).lower()
                or ft in str(item.get("family", "")).lower()
                or ft in str(item.get("hypothesis", "")).lower()
            ):
                return False
        if sf and sf != "ALL":
            if sf == "PASSED" and item.get("passed") is not True:
                return False
            if sf == "FAILED" and item.get("passed") is not False:
                return False
            if sf == "RUNNING" and item.get("status") != "running":
                return False
            if sf == "PENDING" and not (item.get("passed") is None and item.get("status") != "running"):
                return False
        if tf and tf != "ALL":
            if tf not in str(item.get("track", "")).upper():
                return False
        if dr and dr != "ALL":
            max_days = {"TODAY": 0, "7D": 7, "30D": 30}.get(dr)
            if max_days is not None:
                created = item.get("created_at", "")
                if created:
                    try:
                        dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                        if (datetime.now(UTC) - dt).days > max_days:
                            return False
                    except (ValueError, TypeError):
                        pass
        return True

    def _get_item_key(self, item: dict[str, Any]) -> str | None:
        return str(item.get("spec_hash", "")) or None

    def get_current_run(self) -> dict[str, Any] | None:
        return self.get_current_item()

    def get_tracks(self) -> list[str]:
        tracks = set()
        for r in self._all_data:
            t = str(r.get("track", "")).strip()
            if t:
                tracks.add(t)
        return sorted(tracks)

    def _render_item(self, item: dict[str, Any], index: int, is_selected: bool) -> Text:
        return render_list_item(
            hash_text=str(item.get("spec_hash", "?")),
            secondary_text=str(item.get("track", "")),
            score=item.get("aggregate_score"),
            passed=item.get("passed"),
            deployed=bool(item.get("deployd")),
            is_selected=is_selected,
            is_multi=str(item.get("spec_hash", "")) in self._selected_hashes,
            secondary_width=8,
        )


# ══════════════════════════════════════════════════════════════════════
# Provider Metrics Widget
# ══════════════════════════════════════════════════════════════════════


class ProviderMetricsWidget(Static):
    """Displays provider metrics as horizontal bar gauges."""

    telemetry_data: reactive[dict[str, Any]] = reactive(dict, layout=True)

    def render(self) -> Text:
        result = Text()

        # Header
        render_header(result, "PROVIDER METRICS")

        data = self.telemetry_data
        if not data:
            result.append("\n  No telemetry data available\n", style=TEXT_MUTED)
            result.append(
                "  Run a benchmark evaluation\n"
                "  to see provider metrics.\n",
                style=TEXT_MUTED,
            )
            return result

        # Confidence indicator
        confidence = data.get("confidence", "unknown")
        conf_color = confidence_color(confidence)
        result.append("  Confidence: ", style=TEXT_SECONDARY)
        result.append(f"{confidence}\n\n", style=f"bold {conf_color}")

        # Stage counts as horizontal bars
        stage_counts = data.get("stage_counts", {})
        if stage_counts:
            result.append("  Stage Distribution\n", style=f"bold {TEXT_SECONDARY}")
            max_count = max(stage_counts.values()) if stage_counts else 1
            for stage, count in sorted(stage_counts.items()):
                ratio = (count / max_count) if max_count > 0 else 0
                result.append(f"  {stage:<12}", style=TEXT_SECONDARY)
                result.append(bar_gauge(ratio, width=16), style=INFO_BLUE)
                result.append(f" {count}\n", style=TEXT_PRIMARY)
            result.append("\n")

        # Model counts
        model_counts = data.get("model_counts", {})
        if model_counts:
            result.append("  Model Usage\n", style=f"bold {TEXT_SECONDARY}")
            total = sum(model_counts.values())
            for model, count in sorted(model_counts.items(), key=lambda x: -x[1]):
                pct = (count / total * 100) if total > 0 else 0
                result.append(f"  {model[:12]:<12}", style=TEXT_SECONDARY)
                result.append(bar_gauge(pct / 100, width=16), style=ACCENT_GREEN)
                result.append(f" {count} ({pct:.0f}%)\n", style=TEXT_PRIMARY)
            result.append("\n")

        # Provider metrics
        provider_metrics = data.get("provider_metrics", {})
        usage = provider_metrics.get("usage", {})
        if usage:
            result.append("  Token Usage\n", style=f"bold {TEXT_SECONDARY}")
            prompt = usage.get("prompt_tokens")
            completion = usage.get("completion_tokens")
            total = usage.get("total_tokens")
            result.append("  Prompt:     ", style=TEXT_SECONDARY)
            result.append(f"{format_count(prompt)}\n", style=TEXT_PRIMARY)
            result.append("  Completion: ", style=TEXT_SECONDARY)
            result.append(f"{format_count(completion)}\n", style=TEXT_PRIMARY)
            result.append("  Total:      ", style=TEXT_SECONDARY)
            result.append(f"{format_count(total)}\n", style=TEXT_PRIMARY)
            cost_status = usage.get("cost_status", "")
            if cost_status:
                result.append("  Cost:       ", style=TEXT_SECONDARY)
                result.append(f"{cost_status}\n", style=TEXT_MUTED)
            result.append("\n")

        # Credit pressure
        credit_pressure = provider_metrics.get("credit_pressure", {})
        cp_count = credit_pressure.get("event_count", 0)
        cp_latest = credit_pressure.get("latest")
        if cp_count or cp_latest:
            result.append("  Credit Pressure\n", style=f"bold {TEXT_SECONDARY}")
            result.append("  Events: ", style=TEXT_SECONDARY)
            cp_color = ERROR_RED if (cp_count or 0) > 0 else ACCENT_GREEN
            result.append(f"{cp_count}\n", style=cp_color)
            if cp_latest and isinstance(cp_latest, dict):
                sev = str(cp_latest.get("severity", "")).lower()
                sev_color = ERROR_RED if sev == "critical" else WARNING_YELLOW if sev == "warning" else TEXT_MUTED
                result.append("  Latest: ", style=TEXT_SECONDARY)
                result.append(f"{sev}\n", style=f"bold {sev_color}")
            result.append("\n")

        # Context pressure
        context_pressure = provider_metrics.get("context_pressure", {})
        ctx_count = context_pressure.get("event_count", 0)
        if ctx_count:
            result.append("  Context Pressure\n", style=f"bold {TEXT_SECONDARY}")
            result.append("  Events: ", style=TEXT_SECONDARY)
            ctx_color = ERROR_RED if ctx_count > 0 else ACCENT_GREEN
            result.append(f"{ctx_count}\n", style=ctx_color)

        return result


# ══════════════════════════════════════════════════════════════════════
# Tool Usage Widget
# ══════════════════════════════════════════════════════════════════════


class ToolUsageWidget(Static):
    """Displays tool invocation counts, latency, and error rates."""

    telemetry_data: reactive[dict[str, Any]] = reactive(dict, layout=True)

    def render(self) -> Text:
        result = Text()

        # Header
        render_header(result, "TOOL USAGE")

        data = self.telemetry_data
        if not data:
            result.append("\n  No tool data available\n", style=TEXT_MUTED)
            return result

        tool_counts = data.get("tool_counts", {})
        tool_latency = data.get("tool_latency_ms", {})
        tool_error_count = data.get("tool_error_count", 0)
        tool_invocation_count = data.get("tool_invocation_count", 0)

        if not tool_counts:
            result.append("\n  No tool invocations recorded\n", style=TEXT_MUTED)
            return result

        # Summary line
        error_rate = (tool_error_count / tool_invocation_count * 100) if tool_invocation_count > 0 else 0
        err_color = ERROR_RED if error_rate > 10 else WARNING_YELLOW if error_rate > 5 else ACCENT_GREEN
        result.append(f"  Total: {tool_invocation_count}  ", style=TEXT_SECONDARY)
        result.append(f"Errors: {tool_error_count} ", style=err_color)
        result.append(f"({error_rate:.1f}%)\n", style=err_color)

        # Latency summary
        p50 = tool_latency.get("p50")
        p95 = tool_latency.get("p95")
        result.append("  Latency ", style=TEXT_SECONDARY)
        result.append("p50:", style=TEXT_SECONDARY)
        result.append_text(format_latency(p50))
        result.append("  p95:", style=TEXT_SECONDARY)
        result.append_text(format_latency(p95))
        result.append("\n\n")

        # Tool table header
        result.append(f"  {'TOOL':<28}{'COUNT':>6}\n", style=TEXT_MUTED)
        result.append("  " + "\u2500" * 34 + "\n", style=BORDER_DIM)

        # Tool rows (sorted by count descending)
        for tool_name, count in sorted(tool_counts.items(), key=lambda x: -x[1]):
            name = truncate(tool_name, 26)
            result.append(f"  {name:<28}", style=TEXT_SECONDARY)
            result.append(f"{count:>6}\n", style=TEXT_PRIMARY)

        return result


# ══════════════════════════════════════════════════════════════════════
# Run Detail Widget
# ══════════════════════════════════════════════════════════════════════


class RunDetailWidget(Static):
    """Displays detailed information for a selected run."""

    run: reactive[dict[str, Any] | None] = reactive(None, layout=True)

    def render(self) -> Text:
        result = Text()

        # Header
        render_header(result, "RUN DETAIL")

        run = self.run
        if not run:
            result.append("\n  Select a run to view details\n", style=TEXT_MUTED)
            return result

        # Spec hash
        spec_hash = str(run.get("spec_hash", "?"))
        result.append("  Hash: ", style=TEXT_SECONDARY)
        result.append(f"{spec_hash}\n", style=TEXT_PRIMARY)

        # Track + Family
        track = str(run.get("track", ""))
        family = str(run.get("family", ""))
        result.append("  Track: ", style=TEXT_SECONDARY)
        result.append(f"{track}\n", style=INFO_BLUE)
        result.append("  Family: ", style=TEXT_SECONDARY)
        result.append(f"{family}\n", style=INFO_BLUE)

        # Date
        created = run.get("created_at", "")
        result.append("  Created: ", style=TEXT_SECONDARY)
        result.append(f"{format_date(created)}\n", style=TEXT_PRIMARY)

        # Score
        score = run.get("aggregate_score")
        result.append("  Score: ", style=TEXT_SECONDARY)
        result.append_text(format_score(score))
        result.append("\n")

        # Status
        passed = run.get("passed")
        deployed = bool(run.get("deployd"))
        result.append("  Status: ", style=TEXT_SECONDARY)
        result.append_text(format_status(passed, deployed))
        if deployed:
            result.append(" deployed", style=INFO_BLUE)
        elif passed is True:
            result.append(" passed", style=ACCENT_GREEN)
        elif passed is False:
            result.append(" failed", style=ERROR_RED)
        else:
            result.append(" pending", style=TEXT_MUTED)
        result.append("\n")

        # Experiment count (for run summaries)
        if "experiment_count" in run:
            result.append("  Experiments: ", style=TEXT_SECONDARY)
            result.append(f"{run['experiment_count']}\n", style=TEXT_PRIMARY)
        if "passed_count" in run:
            result.append("  Passed: ", style=TEXT_SECONDARY)
            result.append(f"{run['passed_count']}\n", style=TEXT_PRIMARY)
        if "best_aggregate_score" in run:
            result.append("  Best Score: ", style=TEXT_SECONDARY)
            result.append_text(format_score(run.get("best_aggregate_score")))
            result.append("\n")

        return result


# ══════════════════════════════════════════════════════════════════════
# Run Comparison Widget
# ══════════════════════════════════════════════════════════════════════


class RunComparisonWidget(ComparisonWidget):
    """Side-by-side comparison of 2+ selected runs."""

    runs: reactive[list[dict[str, Any]]] = reactive(list, layout=True)
    items = runs  # alias for ComparisonWidget base

    _metrics: ClassVar[list[tuple[str, str, str]]] = [
        ("Score", "aggregate_score", "{:.3f}"),
        ("Track", "track", "{}"),
        ("Family", "family", "{}"),
        ("Created", "created_at", "{}"),
        ("Status", "passed", "{}"),
    ]
    _empty_message: ClassVar[str] = "Select 2+ runs with Space, then press c"

    def set_runs(self, runs: list[dict[str, Any]]) -> None:
        self.set_items(runs)

    def _get_item_name(self, item: dict[str, Any], index: int) -> str:
        return str(item.get("spec_hash", f"R{index + 1}"))


# ══════════════════════════════════════════════════════════════════════
# Service Health Widget
# ══════════════════════════════════════════════════════════════════════


class ServiceHealthWidget(Static):
    """Displays service health from /ops-board."""

    service_health: reactive[dict[str, Any]] = reactive(dict, layout=True)
    artifact_status: reactive[dict[str, Any]] = reactive(dict, layout=True)

    def render(self) -> Text:
        result = Text()

        # Header
        render_header(result, "SERVICE HEALTH")

        health = self.service_health
        if not health:
            result.append("\n  No health data available\n", style=TEXT_MUTED)
            return result

        for name, info in sorted(health.items()):
            if not isinstance(info, dict):
                continue
            status = str(info.get("status", "unknown")).lower()
            if status in ("ok", "running"):
                icon = "\u25cf"
                color = ACCENT_GREEN
            elif status == "external":
                icon = "\u25cb"
                color = INFO_BLUE
            elif status == "missing":
                icon = "\u25cb"
                color = ERROR_RED
            else:
                icon = "\u00b7"
                color = TEXT_MUTED

            result.append(f"  {icon} ", style=color)
            result.append(f"{name:<16}", style=TEXT_SECONDARY)
            result.append(f"{status}\n", style=color)

        # Artifact freshness
        artifacts = self.artifact_status
        if artifacts:
            result.append("\n  ARTIFACTS\n", style=f"bold {TEXT_SECONDARY}")
            for name, info in sorted(artifacts.items()):
                if not isinstance(info, dict):
                    continue
                freshness = str(info.get("freshness", "unknown")).lower()
                status = str(info.get("status", "unknown")).lower()
                if freshness == "fresh":
                    icon = "\u25cf"
                    color = ACCENT_GREEN
                elif freshness == "stale":
                    icon = "\u25cb"
                    color = WARNING_YELLOW
                elif freshness == "expired":
                    icon = "\u25cb"
                    color = ERROR_RED
                else:
                    icon = "\u00b7"
                    color = TEXT_MUTED

                result.append(f"  {icon} ", style=color)
                result.append(f"{name:<20}", style=TEXT_SECONDARY)
                result.append(f"{freshness if status == 'present' else status}\n", style=color)

        return result


# ══════════════════════════════════════════════════════════════════════
# Telemetry Screen
# ══════════════════════════════════════════════════════════════════════


class TelemetryScreen(BaseScreen):
    """Telemetry and Run Browser screen.

    Layout:
    - Filter bar at top (search, date range, track, status)
    - Left column: Run list with multi-select
    - Right column: Run detail + provider metrics + tool usage + comparison
    """

    DEFAULT_CSS = """
    TelemetryScreen {
        layout: vertical;
    }
    """

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = BaseScreen.BINDINGS + [
        Binding("space", "toggle_select", "Select", show=True),
        Binding("c", "toggle_compare", "Compare", show=True),
        Binding("s", "cycle_sort", "Sort", show=True),
        Binding("d", "cycle_date_range", "Date", show=True),
        Binding("f", "cycle_status_filter", "Filter", show=True),
        Binding("t", "cycle_track_filter", "Track", show=True),
        Binding("v", "toggle_detail_view", "View", show=True),
    ]

    compare_mode: reactive[bool] = reactive(False)
    run_count: reactive[int] = reactive(0)
    _date_range: reactive[str] = reactive("ALL")
    _status_filter: reactive[str] = reactive("ALL")
    _track_filter: reactive[str] = reactive("ALL")
    _detail_view: reactive[str] = reactive("telemetry")

    _loading_widget_id: ClassVar[str] = "#telemetry-loading"
    _status_widget_id: ClassVar[str] = "#telemetry-status"
    _refresh_interval: ClassVar[float] = 30.0
    _api_client_class: ClassVar[type] = TuiApiClient
    _search_input_id: ClassVar[str] = "telemetry-search"
    _search_list_id: ClassVar[str] = "telemetry-run-list"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._telemetry_data: dict[str, Any] = {}
        self._ops_data: dict[str, Any] = {}
        self._runs_data: list[dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        yield Input(
            placeholder="Search runs\u2026 (hash, track, family)",
            id="telemetry-search",
        )
        yield Static(id="telemetry-filters")
        with Horizontal(id="telemetry-main"):
            yield TelemetryRunListWidget(id="telemetry-run-list")
            with Vertical(id="telemetry-detail"):
                yield RunDetailWidget(id="run-detail")
                yield ProviderMetricsWidget(id="provider-metrics")
                yield ToolUsageWidget(id="tool-usage")
                yield ServiceHealthWidget(id="service-health", classes="hidden")
                yield RunComparisonWidget(id="telemetry-comparison", classes="hidden")
        yield LoadingIndicator(id="telemetry-loading")
        yield Static(self.status_text, id="telemetry-status")

    def on_mount(self) -> None:
        """Initialize the screen and start auto-refresh."""
        super().on_mount()
        self._update_filters_bar()
        self._update_status_text("Loading runs and telemetry\u2026")

    def _update_filters_bar(self) -> None:
        """Update the filters display bar."""
        parts = [
            f"Date: {self._date_range}",
            f"Status: {self._status_filter}",
            f"Track: {self._track_filter}",
        ]
        text = "  " + "  \u2502  ".join(parts) + "  |  [d]ate  [f]ilter  [t]rack"
        safe_update_text(self, "#telemetry-filters", text)

    # ── Data Fetching ────────────────────────────────────────────────

    async def _fetch_data(self) -> None:
        """Fetch all telemetry and run data."""
        await self._fetch_telemetry()
        await self._fetch_ops_board()
        await self._fetch_runs()
        self._update_status_text(
            f"  {self.run_count} runs loaded  |  "
            "[r]efresh  [c]ompare  [s]ort  [/]search  [d]ate  [v]iew"
        )

    async def _fetch_telemetry(self) -> None:
        """Fetch telemetry data from API."""
        if self._api is None:
            return
        try:
            data = await self._api.get_telemetry_report()
            self._telemetry_data = data
            self._update_provider_metrics(data)
            self._update_tool_usage(data)
        except Exception as exc:
            logger.debug("Telemetry fetch failed: %s", exc)

    async def _fetch_ops_board(self) -> None:
        """Fetch ops-board data from FastAPI."""
        if self._api is None:
            return
        try:
            data = await self._api.get_ops_board()
            self._ops_data = data
            self._update_service_health(data)
        except Exception as exc:
            logger.debug("Ops board fetch failed: %s", exc)

    async def _fetch_runs(self) -> None:
        """Fetch experiment runs from API."""
        if self._api is None:
            return
        try:
            data = await self._api.get_strategies()
            rows = data.get("strategies", data.get("experiments", []))
            self._runs_data = rows
            self._update_run_list(rows)
        except Exception as exc:
            logger.debug("Runs fetch failed: %s", exc)

    def _update_provider_metrics(self, data: dict[str, Any]) -> None:
        """Update the provider metrics widget."""
        safe_query(self, "#provider-metrics", ProviderMetricsWidget,
                   lambda w: setattr(w, "telemetry_data", data))

    def _update_tool_usage(self, data: dict[str, Any]) -> None:
        """Update the tool usage widget."""
        safe_query(self, "#tool-usage", ToolUsageWidget,
                   lambda w: setattr(w, "telemetry_data", data))

    def _update_service_health(self, data: dict[str, Any]) -> None:
        def _update(w: ServiceHealthWidget) -> None:
            w.service_health = data.get("service_health", {})
            w.artifact_status = data.get("artifact_status", {})
        safe_query(self, "#service-health", ServiceHealthWidget, _update)

    def _update_run_list(self, rows: list[dict[str, Any]]) -> None:
        """Update the run list widget with loaded data."""
        lw = safe_query(self, "#telemetry-run-list", TelemetryRunListWidget)
        if lw:
            lw.set_runs(rows)
            self.run_count = len(rows)

    # ── Actions ──────────────────────────────────────────────────────


    def action_toggle_select(self) -> None:
        """Toggle multi-select on current run for comparison."""
        lw = safe_query(self, "#telemetry-run-list", TelemetryRunListWidget)
        if lw:
            lw.toggle_select()
            self._update_comparison()

    def _show_detail_view(self) -> None:
        """Apply the current ``_detail_view`` setting to the right-column widgets."""
        provider = safe_query(self, "#provider-metrics", ProviderMetricsWidget)
        tool_usage = safe_query(self, "#tool-usage", ToolUsageWidget)
        health = safe_query(self, "#service-health", ServiceHealthWidget)
        if not all([provider, tool_usage, health]):
            return
        is_telemetry = self._detail_view == "telemetry"
        provider.set_class(is_telemetry, "hidden")
        tool_usage.set_class(is_telemetry, "hidden")
        health.set_class(not is_telemetry, "hidden")

    def action_toggle_compare(self) -> None:
        """Toggle between detail view and comparison view."""
        self.compare_mode = not self.compare_mode
        detail = safe_query(self, "#run-detail", RunDetailWidget)
        comparison = safe_query(self, "#telemetry-comparison", RunComparisonWidget)
        if not detail or not comparison:
            return
        if self.compare_mode:
            for w_id in ("#provider-metrics", "#tool-usage", "#service-health"):
                w = safe_query(self, w_id, Static)
                if w is not None:
                    w.add_class("hidden")
            comparison.remove_class("hidden")
            self._update_comparison()
        else:
            comparison.add_class("hidden")
            detail.remove_class("hidden")
            self._show_detail_view()

    def action_toggle_detail_view(self) -> None:
        """Toggle between telemetry and health views."""
        if self.compare_mode:
            return
        self._detail_view = "health" if self._detail_view == "telemetry" else "telemetry"
        self._show_detail_view()
        label = "Telemetry" if self._detail_view == "telemetry" else "Service Health"
        self.notify(f"View: {label}", severity="information", timeout=1)

    def action_cycle_sort(self) -> None:
        """Cycle sort column (date, score, track)."""
        lw = safe_query(self, "#telemetry-run-list", TelemetryRunListWidget)
        if not lw:
            return
        current = getattr(self, "_sort_by", "date")
        sort_cycle = ["date", "score", "track"]
        idx = sort_cycle.index(current) if current in sort_cycle else -1
        next_sort = sort_cycle[(idx + 1) % len(sort_cycle)]
        self._sort_by = next_sort
        sort_keys = {
            "date": lambda r: r.get("created_at", ""),
            "score": lambda r: r.get("aggregate_score") or 0,
            "track": lambda r: r.get("track", ""),
        }
        runs = sorted(lw._all_data, key=sort_keys[next_sort], reverse=True)
        lw.set_runs(runs)
        self.notify(f"Sorted by: {next_sort}", severity="information", timeout=1)

    def _advance_filter(
        self, attr: str, cycle: list[str], setter: str, label: str
    ) -> None:
        """Advance ``attr`` to the next value in ``cycle`` and apply it."""
        current = getattr(self, attr)
        idx = cycle.index(current) if current in cycle else 0
        value = cycle[(idx + 1) % len(cycle)]
        setattr(self, attr, value)
        lw = safe_query(self, "#telemetry-run-list", TelemetryRunListWidget)
        if not lw:
            return
        getattr(lw, setter)(value)
        self.run_count = len(lw.runs)
        self._update_filters_bar()
        self.notify(f"{label}: {value}", severity="information", timeout=1)

    def action_cycle_date_range(self) -> None:
        """Cycle date range filter: ALL → 7d → 30d → TODAY → ALL."""
        self._advance_filter(
            "_date_range", DATE_RANGE_FILTERS, "set_date_range", "Date range"
        )

    def action_cycle_status_filter(self) -> None:
        """Cycle status filter: ALL → PASSED → FAILED → RUNNING → PENDING → ALL."""
        self._advance_filter(
            "_status_filter", STATUS_FILTERS, "set_status_filter", "Status"
        )

    def action_cycle_track_filter(self) -> None:
        """Cycle track filter through available tracks."""
        lw = safe_query(self, "#telemetry-run-list", TelemetryRunListWidget)
        if not lw:
            return
        tracks: list[str] = ["ALL"] + lw.get_tracks()
        self._advance_filter("_track_filter", tracks, "set_track_filter", "Track")

    # ── Event Handlers ───────────────────────────────────────────────

    def on_input_changed(self, event: Input.Changed) -> None:
        """Handle search input changes."""
        if self._on_search_input_changed(event):
            # Update run count after filter
            lw = safe_query(self, "#telemetry-run-list", TelemetryRunListWidget)
            if lw:
                self.run_count = len(lw.runs)

    def _on_selection_changed(self) -> None:
        """Update detail panel when selection changes."""
        lw = safe_query(self, "#telemetry-run-list", TelemetryRunListWidget)
        if lw:
            current = lw.get_current_run()
            if current:
                safe_query(self, "#run-detail", RunDetailWidget,
                           lambda w: setattr(w, "run", current))

    def _update_comparison(self) -> None:
        """Update the comparison panel with selected runs."""
        lw = safe_query(self, "#telemetry-run-list", TelemetryRunListWidget)
        comparison = safe_query(self, "#telemetry-comparison", RunComparisonWidget)
        if not lw or not comparison:
            return
        selected = lw.get_selected_hashes()
        runs = [r for h in selected for r in self._runs_data
                if str(r.get("spec_hash", "")) == h]
        comparison.set_runs(runs)
        if len(runs) >= 2:
            self._update_status_text(f"  Comparing {len(runs)} runs  |  [c] toggle view  [space] select")
