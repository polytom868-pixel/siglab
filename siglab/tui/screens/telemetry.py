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

import json
import logging
from datetime import UTC, datetime
from typing import Any, ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Input, Static

from siglab.tui.api_client import TuiApiClient
from siglab.tui.cli_bridge import run_cli
from siglab.tui.formatting import (
    ACCENT_GREEN,
    ACCENT_PURPLE,
    BORDER_DIM,
    ERROR_RED,
    INFO_BLUE,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    WARNING_YELLOW,
    format_latency,
    format_score,
    truncate,
)
from siglab.tui.loading import LoadingIndicator

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────

REFRESH_SECONDS = 30.0
MAX_COMPARE = 4

# Date range filter presets
DATE_RANGE_FILTERS: list[str] = ["ALL", "7d", "30d", "TODAY"]

# Status filter options
STATUS_FILTERS: list[str] = ["ALL", "PASSED", "FAILED", "RUNNING", "PENDING"]

# Track filter options (populated from data)
TRACK_FILTERS_DEFAULT: list[str] = ["ALL"]


# ── Formatting helpers ───────────────────────────────────────────────
# Centralized in siglab.tui.formatting; local helpers removed.


def _format_status(passed: bool | None, deployed: bool = False) -> Text:
    """Format pass/fail/deployed status."""
    if deployed:
        return Text("▲", style=INFO_BLUE)
    if passed is None:
        return Text("·", style=TEXT_MUTED)
    if passed:
        return Text("●", style=ACCENT_GREEN)
    return Text("○", style=ERROR_RED)


def _format_date(date_str: str | None) -> str:
    """Format a date string for compact display."""
    if not date_str:
        return "──"
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        return dt.strftime("%m-%d %H:%M")
    except (ValueError, TypeError):
        return date_str[:10] if len(date_str) >= 10 else date_str


def _format_count(value: int | float | None) -> str:
    """Format a count with k/M suffix."""
    if value is None:
        return "─"
    v = float(value)
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    elif v >= 1_000:
        return f"{v / 1_000:.1f}k"
    else:
        return f"{v:.0f}"


def _confidence_color(confidence: str) -> str:
    """Return color for confidence level."""
    c = confidence.lower().strip()
    if c == "good":
        return ACCENT_GREEN
    elif c == "medium":
        return WARNING_YELLOW
    elif c == "poor":
        return ERROR_RED
    return TEXT_MUTED


def _classification_color(classification: str) -> str:
    """Return color for skill classification."""
    c = classification.upper().strip()
    if c == "HIGH_VALUE":
        return ACCENT_GREEN
    elif c == "MEDIUM_VALUE":
        return INFO_BLUE
    elif c == "LOW_VALUE":
        return TEXT_MUTED
    elif c == "NOISY":
        return ERROR_RED
    return TEXT_MUTED


# ══════════════════════════════════════════════════════════════════════
# Run List Widget
# ══════════════════════════════════════════════════════════════════════


class TelemetryRunListWidget(Static):
    """Vertical list of experiment runs with selection and multi-select."""

    runs: reactive[list[dict[str, Any]]] = reactive(list, layout=True)
    selected_index: reactive[int] = reactive(0)

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._all_runs: list[dict[str, Any]] = []
        self._filter_text: str = ""
        self._status_filter: str = "ALL"
        self._track_filter: str = "ALL"
        self._date_range: str = "ALL"
        self._selected_hashes: set[str] = set()

    def set_runs(self, runs: list[dict[str, Any]]) -> None:
        """Update the full run list."""
        self._all_runs = runs
        self._apply_filters()

    def set_filter(self, text: str) -> None:
        """Update the text search filter."""
        self._filter_text = text.lower().strip()
        self._apply_filters()

    def set_status_filter(self, status: str) -> None:
        """Update the status filter."""
        self._status_filter = status.upper().strip()
        self._apply_filters()

    def set_track_filter(self, track: str) -> None:
        """Update the track filter."""
        self._track_filter = track.upper().strip()
        self._apply_filters()

    def set_date_range(self, date_range: str) -> None:
        """Update the date range filter."""
        self._date_range = date_range.upper().strip()
        self._apply_filters()

    def _apply_filters(self) -> None:
        """Apply all active filters to the run list."""
        filtered = list(self._all_runs)

        # Text search: match spec_hash, track, family, hypothesis
        ft = self._filter_text
        if ft:
            filtered = [
                r
                for r in filtered
                if ft in str(r.get("spec_hash", "")).lower()
                or ft in str(r.get("track", "")).lower()
                or ft in str(r.get("family", "")).lower()
                or ft in str(r.get("hypothesis", "")).lower()
            ]

        # Status filter
        sf = self._status_filter
        if sf and sf != "ALL":
            if sf == "PASSED":
                filtered = [r for r in filtered if r.get("passed") is True]
            elif sf == "FAILED":
                filtered = [r for r in filtered if r.get("passed") is False]
            elif sf == "RUNNING":
                filtered = [r for r in filtered if r.get("status") == "running"]
            elif sf == "PENDING":
                filtered = [r for r in filtered if r.get("passed") is None and r.get("status") != "running"]

        # Track filter
        tf = self._track_filter
        if tf and tf != "ALL":
            filtered = [
                r for r in filtered
                if tf in str(r.get("track", "")).upper()
            ]

        # Date range filter
        dr = self._date_range
        if dr and dr != "ALL":
            now = datetime.now(UTC)
            max_days = {"TODAY": 0, "7D": 7, "30D": 30}.get(dr)
            if max_days is not None:
                def _within_range(r: dict[str, Any]) -> bool:
                    created = r.get("created_at", "")
                    if not created:
                        return True
                    try:
                        dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                        return (now - dt).days <= max_days
                    except (ValueError, TypeError):
                        return True
                filtered = [r for r in filtered if _within_range(r)]

        self.runs = filtered
        # Clamp selected index
        if self.runs and self.selected_index >= len(self.runs):
            self.selected_index = max(0, len(self.runs) - 1)

    def toggle_select(self) -> None:
        """Toggle multi-select on the current run for comparison."""
        if not self.runs or self.selected_index >= len(self.runs):
            return
        item = self.runs[self.selected_index]
        h = str(item.get("spec_hash", ""))
        if not h:
            return
        if h in self._selected_hashes:
            self._selected_hashes.discard(h)
        else:
            if len(self._selected_hashes) < MAX_COMPARE:
                self._selected_hashes.add(h)

    def get_selected_hashes(self) -> set[str]:
        """Return the set of multi-selected run hashes."""
        return set(self._selected_hashes)

    def get_current_run(self) -> dict[str, Any] | None:
        """Return the currently highlighted run."""
        if self.runs and 0 <= self.selected_index < len(self.runs):
            return self.runs[self.selected_index]
        return None

    def get_tracks(self) -> list[str]:
        """Return unique track names from all runs."""
        tracks = set()
        for r in self._all_runs:
            t = str(r.get("track", "")).strip()
            if t:
                tracks.add(t)
        return sorted(tracks)

    def action_move_up(self) -> None:
        if self.selected_index > 0:
            self.selected_index -= 1

    def action_move_down(self) -> None:
        if self.selected_index < len(self.runs) - 1:
            self.selected_index += 1

    def render(self) -> Text:
        if not self.runs:
            return Text("  No runs found", style=TEXT_MUTED)

        lines = Text()
        for i, run in enumerate(self.runs):
            h = str(run.get("spec_hash", "?"))[:12]
            track = str(run.get("track", ""))
            passed = run.get("passed")
            score = run.get("aggregate_score")
            deployed = bool(run.get("deployd"))
            is_selected = str(run.get("spec_hash", "")) in self._selected_hashes

            # Build row
            prefix = "\u2713 " if is_selected else "  "
            status_dot = "\u25cf" if passed is True else ("\u25cb" if passed is False else "\u00b7")
            status_color = ACCENT_GREEN if passed is True else (ERROR_RED if passed is False else TEXT_MUTED)
            if deployed:
                status_dot = "\u25b2"
                status_color = INFO_BLUE
            score_str = f"{score:.2f}" if score is not None and score == score else "\u2500"

            row = Text()
            row.append(prefix, style=INFO_BLUE if is_selected else TEXT_MUTED)
            row.append(status_dot + " ", style=status_color)
            row.append(truncate(h, 12), style=TEXT_PRIMARY)

            # Track tag
            padding = max(0, 16 - len(h) - len(prefix) - 2)
            row.append(" " * padding, style=TEXT_MUTED)
            row.append(truncate(track, 8), style=INFO_BLUE)

            if i == self.selected_index:
                lines.append("\u25b8 ", style=ACCENT_GREEN)
                styled_row = Text()
                styled_row.append(prefix, style=INFO_BLUE if is_selected else "#000000")
                styled_row.append(status_dot + " ", style=status_color if is_selected else "#000000")
                styled_row.append(truncate(h, 12), style="bold #000000")
                styled_row.append(" " * padding, style="#000000")
                styled_row.append(truncate(track, 8), style="#000000")
                lines.append_text(styled_row)
                lines.append(f"  {score_str}", style=f"bold #000000 on {ACCENT_GREEN}")
            else:
                lines.append("  ")
                lines.append_text(row)
                lines.append(f"  {score_str}", style=TEXT_MUTED)
            lines.append("\n")

        return lines


# ══════════════════════════════════════════════════════════════════════
# Provider Metrics Widget
# ══════════════════════════════════════════════════════════════════════


class ProviderMetricsWidget(Static):
    """Displays provider metrics as horizontal bar gauges."""

    telemetry_data: reactive[dict[str, Any]] = reactive(dict, layout=True)

    def render(self) -> Text:
        result = Text()

        # Header
        result.append(" PROVIDER METRICS\n", style=f"bold {TEXT_PRIMARY}")
        result.append("\u2500" * 50 + "\n", style=BORDER_DIM)

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
        conf_color = _confidence_color(confidence)
        result.append("  Confidence: ", style=TEXT_SECONDARY)
        result.append(f"{confidence}\n\n", style=f"bold {conf_color}")

        # Stage counts as horizontal bars
        stage_counts = data.get("stage_counts", {})
        if stage_counts:
            result.append("  Stage Distribution\n", style=f"bold {TEXT_SECONDARY}")
            max_count = max(stage_counts.values()) if stage_counts else 1
            for stage, count in sorted(stage_counts.items()):
                bar_len = int((count / max_count) * 16) if max_count > 0 else 0
                bar = "\u2588" * bar_len + "\u2591" * (16 - bar_len)
                result.append(f"  {stage:<12}", style=TEXT_SECONDARY)
                result.append(bar, style=INFO_BLUE)
                result.append(f" {count}\n", style=TEXT_PRIMARY)
            result.append("\n")

        # Model counts
        model_counts = data.get("model_counts", {})
        if model_counts:
            result.append("  Model Usage\n", style=f"bold {TEXT_SECONDARY}")
            total = sum(model_counts.values())
            for model, count in sorted(model_counts.items(), key=lambda x: -x[1]):
                pct = (count / total * 100) if total > 0 else 0
                bar_len = int(pct / 100 * 16)
                bar = "\u2588" * bar_len + "\u2591" * (16 - bar_len)
                result.append(f"  {model[:12]:<12}", style=TEXT_SECONDARY)
                result.append(bar, style=ACCENT_GREEN)
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
            result.append(f"{_format_count(prompt)}\n", style=TEXT_PRIMARY)
            result.append("  Completion: ", style=TEXT_SECONDARY)
            result.append(f"{_format_count(completion)}\n", style=TEXT_PRIMARY)
            result.append("  Total:      ", style=TEXT_SECONDARY)
            result.append(f"{_format_count(total)}\n", style=TEXT_PRIMARY)
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
        result.append(" TOOL USAGE\n", style=f"bold {TEXT_PRIMARY}")
        result.append("\u2500" * 50 + "\n", style=BORDER_DIM)

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
        result.append(f"  {'TOOL':<28}{'COUNT':>6}{'ERR':>6}\n", style=TEXT_MUTED)
        result.append("  " + "\u2500" * 42 + "\n", style=BORDER_DIM)

        # Tool rows (sorted by count descending)
        for tool_name, count in sorted(tool_counts.items(), key=lambda x: -x[1]):
            name = truncate(tool_name, 26)
            result.append(f"  {name:<28}", style=TEXT_SECONDARY)
            result.append(f"{count:>6}", style=TEXT_PRIMARY)
            # Error indicator (simplified — we don't have per-tool error counts in telemetry data)
            result.append(f"{'':>6}\n", style=TEXT_MUTED)

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
        result.append(" RUN DETAIL\n", style=f"bold {TEXT_PRIMARY}")
        result.append("\u2500" * 50 + "\n", style=BORDER_DIM)

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
        result.append(f"{_format_date(created)}\n", style=TEXT_PRIMARY)

        # Score
        score = run.get("aggregate_score")
        result.append("  Score: ", style=TEXT_SECONDARY)
        result.append_text(format_score(score))
        result.append("\n")

        # Status
        passed = run.get("passed")
        deployed = bool(run.get("deployd"))
        result.append("  Status: ", style=TEXT_SECONDARY)
        result.append_text(_format_status(passed, deployed))
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


class RunComparisonWidget(Static):
    """Side-by-side comparison of 2+ selected runs."""

    runs: reactive[list[dict[str, Any]]] = reactive(list, layout=True)

    _RUN_COLORS: ClassVar[list[str]] = [
        ACCENT_GREEN,   # green
        INFO_BLUE,      # blue
        WARNING_YELLOW, # yellow
        ACCENT_PURPLE,  # purple
    ]

    def set_runs(self, runs: list[dict[str, Any]]) -> None:
        """Set runs for comparison."""
        self.runs = runs

    def render(self) -> Text:
        result = Text()
        result.append(" RUN COMPARISON\n", style=f"bold {TEXT_PRIMARY}")

        if len(self.runs) < 2:
            result.append(
                "  Select 2+ runs with Space, then press c\n", style=TEXT_MUTED
            )
            return result

        n = len(self.runs)
        col_w = max(12, 60 // (n + 1))

        # Column headers
        header = Text()
        header.append("  ")
        for i, run in enumerate(self.runs):
            name = str(run.get("spec_hash", f"R{i+1}"))[:col_w]
            color = self._RUN_COLORS[i % len(self._RUN_COLORS)]
            header.append(f"{name:<{col_w}}", style=f"bold {color}")
        header.append("DELTA", style=f"bold {WARNING_YELLOW}")
        result.append_text(header)
        result.append("\n")
        result.append("  " + "\u2500" * (col_w * (n + 1) + 4) + "\n", style=BORDER_DIM)

        # Metrics rows
        metrics = [
            ("Score", "aggregate_score", "{:.3f}"),
            ("Track", "track", "{}"),
            ("Family", "family", "{}"),
            ("Created", "created_at", "{}"),
            ("Status", "passed", "{}"),
        ]

        for label, key, fmt in metrics:
            row = Text()
            row.append(f"  {label:<12}", style=TEXT_PRIMARY)

            values: list[float] = []
            for run in self.runs:
                val = run.get(key)
                if val is not None and isinstance(val, (int, float)) and val == val:
                    values.append(float(val))

            for i, run in enumerate(self.runs):
                val = run.get(key)
                color = self._RUN_COLORS[i % len(self._RUN_COLORS)]
                if val is None:
                    row.append(f"{'─':<{col_w}}", style=TEXT_MUTED)
                elif isinstance(val, bool):
                    status = "passed" if val else "failed"
                    row.append(f"{status:<{col_w}}", style=color)
                elif isinstance(val, str):
                    if key == "created_at":
                        row.append(f"{_format_date(val):<{col_w}}", style=color)
                    else:
                        row.append(f"{truncate(val, col_w - 1):<{col_w}}", style=color)
                else:
                    formatted = fmt.format(val)
                    row.append(f"{formatted:<{col_w}}", style=color)

            # Delta column
            if values and len(values) >= 2 and key != "family" and key != "track":
                delta = max(values) - min(values)
                if key == "aggregate_score":
                    row.append(f"\u00b1{delta:.3f}", style=WARNING_YELLOW)
                else:
                    row.append(f"\u00b1{delta:.3f}", style=WARNING_YELLOW)
            elif key in ("family", "track"):
                unique_vals = len(set(str(r.get(key, "")) for r in self.runs))
                row.append("diff" if unique_vals > 1 else "same", style=WARNING_YELLOW if unique_vals > 1 else TEXT_MUTED)

            result.append_text(row)
            result.append("\n")

        return result


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
        result.append(" SERVICE HEALTH\n", style=f"bold {TEXT_PRIMARY}")
        result.append("\u2500" * 50 + "\n", style=BORDER_DIM)

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


class TelemetryScreen(Screen[None]):
    """Telemetry and Run Browser screen.

    Layout:
    - Filter bar at top (search, date range, track, status)
    - Left column: Run list with multi-select
    - Right column: Run detail + provider metrics + tool usage + comparison

    Auto-refreshes every 30 seconds. Connects to /ops-board, /skill-report,
    and CLI telemetry-report.
    """

    DEFAULT_CSS = """
    TelemetryScreen {
        layout: vertical;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "go_back", "Back", show=True),
        Binding("r", "refresh_now", "Refresh", show=True),
        Binding("space", "toggle_select", "Select", show=True),
        Binding("c", "toggle_compare", "Compare", show=True),
        Binding("s", "cycle_sort", "Sort", show=True),
        Binding("/", "focus_search", "Search", show=True),
        Binding("d", "cycle_date_range", "Date", show=True),
        Binding("f", "cycle_status_filter", "Filter", show=True),
        Binding("t", "cycle_track_filter", "Track", show=True),
        Binding("j", "move_down", "Down", show=False),
        Binding("k", "move_up", "Up", show=False),
        Binding("v", "toggle_detail_view", "View", show=True),
        Binding("ctrl+c", "go_back", "Back", show=False),
        Binding("question_mark", "app.show_help", "Help", show=False),
    ]

    # Reactive state
    status_text: reactive[str] = reactive("Connecting\u2026")
    is_loading: reactive[bool] = reactive(True)
    compare_mode: reactive[bool] = reactive(False)
    run_count: reactive[int] = reactive(0)
    _date_range: reactive[str] = reactive("ALL")
    _status_filter: reactive[str] = reactive("ALL")
    _track_filter: reactive[str] = reactive("ALL")
    _detail_view: reactive[str] = reactive("telemetry")  # "telemetry" or "health"

    def __init__(self, api_client: TuiApiClient | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._api = api_client or TuiApiClient()
        self._owns_client = api_client is None
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
        self._update_filters_bar()
        try:
            loading = self.query_one("#telemetry-loading", LoadingIndicator)
            loading.loading = True
        except Exception:
            pass
        self._update_status("Loading runs and telemetry\u2026")
        self.call_after_refresh(self._refresh_all)
        self._refresh_timer = self.set_interval(REFRESH_SECONDS, self._refresh_all)

    async def on_unmount(self) -> None:
        """Clean up resources when the screen is closing."""
        if hasattr(self, "_refresh_timer"):
            self._refresh_timer.stop()
        if self._owns_client:
            await self._api.close()

    def _update_status(self, text: str) -> None:
        """Update the status bar text."""
        try:
            status = self.query_one("#telemetry-status", Static)
            status.update(text)
        except Exception:
            pass

    def _update_filters_bar(self) -> None:
        """Update the filters display bar."""
        try:
            filters = self.query_one("#telemetry-filters", Static)
            parts = [
                f"Date: {self._date_range}",
                f"Status: {self._status_filter}",
                f"Track: {self._track_filter}",
            ]
            filters.update(
                "  " + "  \u2502  ".join(parts)
                + "  |  [d]ate  [f]ilter  [t]rack"
            )
        except Exception:
            pass

    # ── Data Fetching ────────────────────────────────────────────────

    async def _refresh_all(self) -> None:
        """Fetch all telemetry and run data."""
        self.is_loading = True
        self._update_status("Refreshing\u2026")
        try:
            # Fetch in parallel-ish sequence
            await self._fetch_telemetry()
            await self._fetch_ops_board()
            await self._fetch_runs()
            self._update_status(
                f"  {self.run_count} runs loaded  |  "
                "[r]efresh  [c]ompare  [s]ort  [/]search  [d]ate  [v]iew"
            )
            self.is_loading = False
        except Exception as exc:
            self._update_status(f"Error: {exc}")
            self.is_loading = False
            logger.warning("Telemetry refresh failed: %s", exc)

    async def _fetch_telemetry(self) -> None:
        """Fetch telemetry data from CLI telemetry-report command."""
        try:
            result = await run_cli("telemetry-report", "--json", timeout=15.0)
            if result.returncode == 0 and result.stdout.strip():
                data = json.loads(result.stdout)
                self._telemetry_data = data
                self._update_provider_metrics(data)
                self._update_tool_usage(data)
        except json.JSONDecodeError:
            logger.warning("Failed to parse telemetry report JSON")
        except Exception as exc:
            logger.debug("Telemetry fetch failed: %s", exc)

    async def _fetch_ops_board(self) -> None:
        """Fetch ops-board data from FastAPI."""
        try:
            data = await self._api.get_ops_board()
            self._ops_data = data
            self._update_service_health(data)
        except Exception as exc:
            logger.debug("Ops board fetch failed: %s", exc)

    async def _fetch_runs(self) -> None:
        """Fetch experiment runs from CLI ancestry command."""
        try:
            result = await run_cli("ancestry", "--json", timeout=15.0)
            if result.returncode == 0 and result.stdout.strip():
                data = json.loads(result.stdout)
                rows = data if isinstance(data, list) else data.get("rows", data.get("experiments", []))
                self._runs_data = rows
                self._update_run_list(rows)
        except json.JSONDecodeError:
            logger.warning("Failed to parse ancestry JSON output")
        except Exception as exc:
            logger.debug("Runs fetch failed: %s", exc)

    def _update_provider_metrics(self, data: dict[str, Any]) -> None:
        """Update the provider metrics widget."""
        try:
            widget = self.query_one("#provider-metrics", ProviderMetricsWidget)
            widget.telemetry_data = data
        except Exception:
            pass

    def _update_tool_usage(self, data: dict[str, Any]) -> None:
        """Update the tool usage widget."""
        try:
            widget = self.query_one("#tool-usage", ToolUsageWidget)
            widget.telemetry_data = data
        except Exception:
            pass

    def _update_service_health(self, data: dict[str, Any]) -> None:
        """Update the service health widget."""
        try:
            widget = self.query_one("#service-health", ServiceHealthWidget)
            widget.service_health = data.get("service_health", {})
            widget.artifact_status = data.get("artifact_status", {})
        except Exception:
            pass

    def _update_run_list(self, rows: list[dict[str, Any]]) -> None:
        """Update the run list widget with loaded data."""
        try:
            list_widget = self.query_one("#telemetry-run-list", TelemetryRunListWidget)
            list_widget.set_runs(rows)
            self.run_count = len(rows)
        except Exception:
            pass

    # ── Actions ──────────────────────────────────────────────────────

    def action_go_back(self) -> None:
        """Return to the main screen."""
        self.app.pop_screen()

    def action_refresh_now(self) -> None:
        """Force an immediate data refresh."""
        self.call_after_refresh(self._refresh_all)

    def action_focus_search(self) -> None:
        """Focus the search input."""
        try:
            search = self.query_one("#telemetry-search", Input)
            search.focus()
        except Exception:
            pass

    def action_move_up(self) -> None:
        """Move selection up in the run list."""
        try:
            lw = self.query_one("#telemetry-run-list", TelemetryRunListWidget)
            lw.action_move_up()
            self._on_selection_changed()
        except Exception:
            pass

    def action_move_down(self) -> None:
        """Move selection down in the run list."""
        try:
            lw = self.query_one("#telemetry-run-list", TelemetryRunListWidget)
            lw.action_move_down()
            self._on_selection_changed()
        except Exception:
            pass

    def action_toggle_select(self) -> None:
        """Toggle multi-select on current run for comparison."""
        try:
            lw = self.query_one("#telemetry-run-list", TelemetryRunListWidget)
            lw.toggle_select()
            self._update_comparison()
        except Exception:
            pass

    def action_toggle_compare(self) -> None:
        """Toggle between detail view and comparison view."""
        self.compare_mode = not self.compare_mode
        try:
            detail = self.query_one("#run-detail", RunDetailWidget)
            provider = self.query_one("#provider-metrics", ProviderMetricsWidget)
            tool_usage = self.query_one("#tool-usage", ToolUsageWidget)
            health = self.query_one("#service-health", ServiceHealthWidget)
            comparison = self.query_one("#telemetry-comparison", RunComparisonWidget)

            if self.compare_mode:
                detail.add_class("hidden")
                provider.add_class("hidden")
                tool_usage.add_class("hidden")
                health.add_class("hidden")
                comparison.remove_class("hidden")
                self._update_comparison()
            else:
                comparison.add_class("hidden")
                detail.remove_class("hidden")
                if self._detail_view == "telemetry":
                    provider.remove_class("hidden")
                    tool_usage.remove_class("hidden")
                    health.add_class("hidden")
                else:
                    provider.add_class("hidden")
                    tool_usage.add_class("hidden")
                    health.remove_class("hidden")
        except Exception:
            pass

    def action_toggle_detail_view(self) -> None:
        """Toggle between telemetry and health views."""
        if self.compare_mode:
            return
        self._detail_view = "health" if self._detail_view == "telemetry" else "telemetry"
        try:
            provider = self.query_one("#provider-metrics", ProviderMetricsWidget)
            tool_usage = self.query_one("#tool-usage", ToolUsageWidget)
            health = self.query_one("#service-health", ServiceHealthWidget)

            if self._detail_view == "telemetry":
                provider.remove_class("hidden")
                tool_usage.remove_class("hidden")
                health.add_class("hidden")
                self.notify("View: Telemetry", severity="information", timeout=1)
            else:
                provider.add_class("hidden")
                tool_usage.add_class("hidden")
                health.remove_class("hidden")
                self.notify("View: Service Health", severity="information", timeout=1)
        except Exception:
            pass

    def action_cycle_sort(self) -> None:
        """Cycle sort column (date, score, track)."""
        # Sort the run list by different criteria
        try:
            lw = self.query_one("#telemetry-run-list", TelemetryRunListWidget)
            # Cycle through sort options
            current = getattr(self, "_sort_by", "date")
            sort_cycle = ["date", "score", "track"]
            idx = sort_cycle.index(current) if current in sort_cycle else -1
            next_sort = sort_cycle[(idx + 1) % len(sort_cycle)]
            self._sort_by = next_sort

            runs = list(lw._all_runs)
            if next_sort == "date":
                runs.sort(key=lambda r: r.get("created_at", ""), reverse=True)
            elif next_sort == "score":
                runs.sort(key=lambda r: r.get("aggregate_score") or 0, reverse=True)
            elif next_sort == "track":
                runs.sort(key=lambda r: r.get("track", ""))

            lw.set_runs(runs)
            self.notify(f"Sorted by: {next_sort}", severity="information", timeout=1)
        except Exception:
            pass

    def action_cycle_date_range(self) -> None:
        """Cycle date range filter: ALL → 7d → 30d → TODAY → ALL."""
        idx = DATE_RANGE_FILTERS.index(self._date_range) if self._date_range in DATE_RANGE_FILTERS else 0
        self._date_range = DATE_RANGE_FILTERS[(idx + 1) % len(DATE_RANGE_FILTERS)]
        try:
            lw = self.query_one("#telemetry-run-list", TelemetryRunListWidget)
            lw.set_date_range(self._date_range)
            self.run_count = len(lw.runs)
            self._update_filters_bar()
            self.notify(f"Date range: {self._date_range}", severity="information", timeout=1)
        except Exception:
            pass

    def action_cycle_status_filter(self) -> None:
        """Cycle status filter: ALL → PASSED → FAILED → RUNNING → PENDING → ALL."""
        idx = STATUS_FILTERS.index(self._status_filter) if self._status_filter in STATUS_FILTERS else 0
        self._status_filter = STATUS_FILTERS[(idx + 1) % len(STATUS_FILTERS)]
        try:
            lw = self.query_one("#telemetry-run-list", TelemetryRunListWidget)
            lw.set_status_filter(self._status_filter)
            self.run_count = len(lw.runs)
            self._update_filters_bar()
            self.notify(f"Status: {self._status_filter}", severity="information", timeout=1)
        except Exception:
            pass

    def action_cycle_track_filter(self) -> None:
        """Cycle track filter through available tracks."""
        try:
            lw = self.query_one("#telemetry-run-list", TelemetryRunListWidget)
            tracks = ["ALL"] + lw.get_tracks()
            idx = tracks.index(self._track_filter) if self._track_filter in tracks else 0
            self._track_filter = tracks[(idx + 1) % len(tracks)]
            lw.set_track_filter(self._track_filter)
            self.run_count = len(lw.runs)
            self._update_filters_bar()
            self.notify(f"Track: {self._track_filter}", severity="information", timeout=1)
        except Exception:
            pass

    # ── Event Handlers ───────────────────────────────────────────────

    def on_input_changed(self, event: Input.Changed) -> None:
        """Handle search input changes."""
        if event.input.id == "telemetry-search":
            try:
                lw = self.query_one("#telemetry-run-list", TelemetryRunListWidget)
                lw.set_filter(event.value)
                self.run_count = len(lw.runs)
            except Exception:
                pass

    def _on_selection_changed(self) -> None:
        """Update detail panel when selection changes."""
        try:
            lw = self.query_one("#telemetry-run-list", TelemetryRunListWidget)
            current = lw.get_current_run()
            if current:
                detail = self.query_one("#run-detail", RunDetailWidget)
                detail.run = current
        except Exception:
            pass

    def _update_comparison(self) -> None:
        """Update the comparison panel with selected runs."""
        try:
            lw = self.query_one("#telemetry-run-list", TelemetryRunListWidget)
            selected = lw.get_selected_hashes()
            comparison = self.query_one("#telemetry-comparison", RunComparisonWidget)

            # Gather data for selected hashes
            runs = []
            for h in selected:
                for r in self._runs_data:
                    if str(r.get("spec_hash", "")) == h:
                        runs.append(r)
                        break

            comparison.set_runs(runs)
            count = len(runs)
            if count >= 2:
                self._update_status(f"  Comparing {count} runs  |  [c] toggle view  [space] select")
        except Exception:
            pass
