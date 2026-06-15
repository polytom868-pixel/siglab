"""Risk Monitoring TUI screen for SigLab.

Displays:
- Composite risk score gauge (ASCII bar, 0-100%)
- Max drawdown sparkline (historical drawdown chart)
- Correlation matrix heatmap (ASCII grid with color-coded cells)
- Alert stream (timestamp/severity/message log)

Connects to FastAPI /risk endpoint and WebSocket for real-time updates.
Auto-refreshes every 15 seconds.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any, ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import Static

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
    gauge_color,
    safe_query,
    severity_color,
)
from siglab.tui.loading import LoadingIndicator
from siglab.tui.screens.base import BaseScreen, render_header
from siglab.tui.widgets.sparkline import sparkline_text

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────

MAX_ALERTS_DISPLAY = 50

# gauge_color is now imported from siglab.tui.formatting (shared)


def _correlation_color(value: float) -> str:
    """Return color hex for correlation value."""
    if value >= 0.7:
        return ERROR_RED  # high correlation — red
    elif value >= 0.4:
        return WARNING_YELLOW  # moderate — yellow
    else:
        return TEXT_MUTED  # low — muted


def _correlation_block(value: float) -> str:
    """Return a block character representing correlation intensity."""
    if value >= 0.95:
        return "█"  # diagonal / identity
    elif value >= 0.7:
        return "▓"  # high
    elif value >= 0.4:
        return "▒"  # moderate
    elif value >= 0.1:
        return "░"  # low
    else:
        return "·"  # negligible


# ── Composite Score Gauge Widget ─────────────────────────────────────


class RiskGaugeWidget(Static):
    """ASCII bar gauge showing composite risk score (0-100%).

    Zero-copy: stores references to score data from the API response.
    """

    composite_score: reactive[float | None] = reactive(None, layout=True)
    sub_scores: reactive[dict[str, float]] = reactive(dict, layout=True)
    strategy_count: reactive[int] = reactive(0)

    def render(self) -> Text:
        result = Text()

        # Header
        render_header(result, "COMPOSITE RISK SCORE", 36)

        if self.composite_score is None:
            result.append("\n  No risk data available\n", style=TEXT_MUTED)
            result.append("  ", style="")
            result.append("░" * 24, style=BORDER_DIM)
            result.append("\n\n", style="")
            result.append(
                "  Start a paper session to\n"
                "  see risk metrics.\n",
                style=TEXT_MUTED,
            )
            return result

        score = self.composite_score
        pct = int(score * 100)
        color = gauge_color(score)

        # Gauge bar
        result.append("\n  ")
        result.append(bar_gauge(score, width=24), style=f"bold {color}")
        result.append(f"  {pct}/100\n", style=f"bold {color}")

        # Sub-scores
        result.append("\n", style="")
        sub_labels = {
            "sharpe": "Sharpe",
            "drawdown": "Drawdown",
            "concentration": "Concentr.",
            "correlation_risk": "Corr.Risk",
        }
        for key, label in sub_labels.items():
            val = self.sub_scores.get(key)
            if val is not None:
                val_color = gauge_color(val)
                result.append(f"  {label:<12}", style=TEXT_SECONDARY)
                result.append(bar_gauge(val, width=10), style=val_color)
                result.append(f" {val:.2f}\n", style=TEXT_PRIMARY)
            else:
                result.append(f"  {label:<12}", style=TEXT_SECONDARY)
                result.append("░" * 10, style=BORDER_DIM)
                result.append(" ──\n", style=TEXT_MUTED)

        # Strategy count
        if self.strategy_count > 0:
            result.append(
                f"\n  Strategies: {self.strategy_count}\n", style=TEXT_MUTED
            )

        return result


# ── Drawdown Sparkline Widget ────────────────────────────────────────


class DrawdownSparklineWidget(Static):
    """Historical drawdown sparkline chart using Unicode block characters."""

    drawdown_history: reactive[list[float]] = reactive(list, layout=True)
    max_drawdown: reactive[float | None] = reactive(None)
    current_drawdown: reactive[float | None] = reactive(None)
    recovery_periods: reactive[int | None] = reactive(None)

    def render(self) -> Text:
        result = Text()

        # Header
        render_header(result, "DRAWDOWN", 36)
        if not self.drawdown_history:
            result.append("\n  Collecting equity data…\n", style=TEXT_MUTED)
            result.append("  ")
            result.append("─" * 30, style=BORDER_DIM)
            result.append("\n")
            return result

        # Sparkline from drawdown values (inverted: more negative = lower)
        # Negate so that drawdowns appear as dips below the baseline
        values = [-v for v in self.drawdown_history]
        # Responsive: cap chart width to available widget width
        avail = getattr(self.size, 'width', 80) or 80
        chart_width = max(20, min(avail - 6, min(60, len(values))))
        spark = sparkline_text(values, width=chart_width, bearish_color=ERROR_RED)
        result.append("  ")
        result.append_text(spark)
        result.append("\n\n")

        # Summary stats
        max_dd = self.max_drawdown
        cur_dd = self.current_drawdown
        recovery = self.recovery_periods

        result.append("  Max DD: ", style=TEXT_SECONDARY)
        if max_dd is not None:
            dd_color = ERROR_RED if max_dd < -0.1 else WARNING_YELLOW if max_dd < -0.05 else TEXT_MUTED
            result.append(f"{max_dd * 100:.1f}%", style=dd_color)
        else:
            result.append("──", style=TEXT_MUTED)

        result.append("   Current: ", style=TEXT_SECONDARY)
        if cur_dd is not None:
            dd_color = ERROR_RED if cur_dd < -0.1 else WARNING_YELLOW if cur_dd < -0.05 else TEXT_MUTED
            result.append(f"{cur_dd * 100:.1f}%", style=dd_color)
        else:
            result.append("──", style=TEXT_MUTED)

        result.append("   Recovery: ", style=TEXT_SECONDARY)
        if recovery is not None:
            result.append(f"{recovery} periods", style=ACCENT_GREEN)
        else:
            result.append("in progress", style=WARNING_YELLOW)

        result.append("\n")
        return result


# ── Correlation Matrix Heatmap Widget ────────────────────────────────


class CorrelationHeatmapWidget(Static):
    """ASCII heatmap showing cross-strategy correlation matrix."""

    matrix: reactive[list[list[float]] | None] = reactive(None, layout=True)
    strategy_names: reactive[list[str]] = reactive(list)

    def render(self) -> Text:
        result = Text()

        # Header
        render_header(result, "CORRELATION MATRIX", 36)

        matrix = self.matrix
        names = self.strategy_names

        if not matrix or len(matrix) < 2:
            result.append(
                "\n  Need ≥2 strategies for\n  correlation analysis\n",
                style=TEXT_MUTED,
            )
            return result

        n = len(matrix)

        # Generate short names if not provided
        if not names or len(names) != n:
            names = [f"S{i+1}" for i in range(n)]

        # Responsive: determine available width and adjust accordingly
        avail = getattr(self.size, 'width', 80) or 80
        # Each cell: block + "0.00" = 5 chars + 1 space = 6 chars
        # Row label: max_name_len + 2 indent + 2 padding
        # Total: (max_name_len + 2) + n * 6
        # Solve for max_name_len given avail:
        #   avail >= max_name_len + 2 + n * 6
        #   max_name_len <= avail - 2 - n * 6
        cell_width = 6  # block + "0.00" + space
        max_name_for_avail = max(3, avail - 2 - n * cell_width)
        max_name_len = min(8, max_name_for_avail)

        # If even with min name length the matrix is too wide, limit strategies shown
        min_row_width = 3 + 2 + n * cell_width  # min_name(3) + indent(2) + cells
        show_n = n
        if min_row_width > avail and n > 2:
            # Show as many strategies as fit
            show_n = max(2, (avail - 5) // cell_width)
            if show_n < n:
                names = names[:show_n]
                # We'll show a truncation hint below

        # Column header
        result.append(f"  {'':>{max_name_len}}  ", style=TEXT_MUTED)
        for name in names[:show_n]:
            short = name[:max_name_len].rjust(max_name_len)
            result.append(f"{short} ", style=INFO_BLUE)
        result.append("\n")

        # Rows
        for i in range(min(n, show_n)):
            row_label = names[i][:max_name_len].rjust(max_name_len)
            result.append(f"  {row_label}  ", style=TEXT_SECONDARY)
            for j in range(min(n, show_n)):
                val = matrix[i][j] if i < len(matrix) and j < len(matrix[i]) else 0.0
                block = _correlation_block(val)
                color = _correlation_color(val) if i != j else TEXT_SECONDARY
                cell = f"{block}{val:.2f}"
                result.append(f"{cell:>{max_name_len}} ", style=color)
            result.append("\n")

        # Show truncation hint if strategies were hidden
        if show_n < n:
            result.append(f"  … +{n - show_n} more strategies\n", style=TEXT_MUTED)

        # Legend
        result.append("\n  Legend: ", style=TEXT_MUTED)
        result.append("█", style=TEXT_SECONDARY)
        result.append("=1.0 ", style=TEXT_MUTED)
        result.append("▓", style=ERROR_RED)
        result.append("≥0.7 ", style=TEXT_MUTED)
        result.append("▒", style=WARNING_YELLOW)
        result.append("≥0.4 ", style=TEXT_MUTED)
        result.append("░", style=TEXT_MUTED)
        result.append("≥0.1 ", style=TEXT_MUTED)
        result.append("·", style=TEXT_MUTED)
        result.append("<0.1\n", style=TEXT_MUTED)

        return result


# ── Alert Stream Widget ──────────────────────────────────────────────


class AlertStreamWidget(Static):
    """Scrollable log of risk alerts with severity-colored entries."""

    alerts: reactive[list[dict[str, Any]]] = reactive(list, layout=True)

    def render(self) -> Text:
        result = Text()

        # Header
        render_header(result, "ALERT STREAM", 36)

        if not self.alerts:
            result.append(
                f"\n  No alerts\n  Last check: "
                f"{datetime.now(UTC).strftime('%H:%M:%S')} UTC\n",
                style=TEXT_MUTED,
            )
            return result

        # Show alerts (newest first, limited)
        for alert in self.alerts[:MAX_ALERTS_DISPLAY]:
            ts = str(alert.get("timestamp", ""))[-8:]  # HH:MM:SS
            severity = str(alert.get("severity", "info")).upper()[:4]
            message = str(alert.get("message", ""))
            metric = str(alert.get("metric", ""))

            sev_color = severity_color(severity.lower())

            result.append(f"  {ts} ", style=TEXT_MUTED)
            result.append(f"{severity:<5}", style=f"bold {sev_color}")
            if metric:
                result.append(f" {metric}", style=TEXT_SECONDARY)
            result.append(f"  {message}\n", style=TEXT_PRIMARY)

        return result


# ── Risk Monitor Screen ──────────────────────────────────────────────


class RiskScreen(BaseScreen):
    """Risk monitoring screen showing portfolio risk metrics.

    Layout:
    - Left column: Composite score gauge + Alert stream
    - Right column: Drawdown sparkline + Correlation matrix heatmap

    Supports WebSocket risk_score subscription for real-time updates.
    """

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = BaseScreen.BINDINGS + [
        Binding("f", "filter_alerts", "Filter", show=False),
    ]

    _filter_severity: reactive[str] = reactive("all")

    _loading_widget_id: ClassVar[str] = "#risk-loading"
    _status_widget_id: ClassVar[str] = "#risk-status"
    _refresh_interval: ClassVar[float] = 15.0
    _api_client_class: ClassVar[type] = TuiApiClient

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._all_alerts: list[dict[str, Any]] = []
        self._ws_task: asyncio.Task[None] | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="risk-layout"):
            with Horizontal(id="risk-main"):
                with Vertical(id="risk-left"):
                    yield RiskGaugeWidget(id="risk-gauge")
                    yield AlertStreamWidget(id="risk-alerts")
                with Vertical(id="risk-right"):
                    yield DrawdownSparklineWidget(id="risk-drawdown")
                    yield CorrelationHeatmapWidget(id="risk-correlation")
            yield LoadingIndicator(id="risk-loading")
            yield Static(self.status_text, id="risk-status")

    def on_mount(self) -> None:
        """Initialize the screen and start auto-refresh + WebSocket."""
        super().on_mount()
        self._ws_task = asyncio.create_task(self._ws_risk_loop())

    async def on_unmount(self) -> None:
        """Clean up WS task, then delegate to base for API client cleanup."""
        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
        await super().on_unmount()

    async def _ws_risk_loop(self) -> None:
        """Subscribe to risk_score WebSocket updates in background.

        Reconnects with exponential backoff (capped at 30s) when the
        subscription drops. Backoff only doubles — it does NOT reset
        between iterations, so a sustained outage throttles retries.
        """
        if self._api is None:
            return
        backoff = 1.0
        max_backoff = 30.0
        while True:
            try:
                await self._api.ws_subscribe_risk(self._on_ws_risk_update)
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.debug("WS risk loop error (retry in %.0fs): %s", backoff, exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)

    async def _on_ws_risk_update(self, msg: dict[str, Any]) -> None:
        """Handle an incoming risk_score WebSocket message."""
        try:
            # Update gauge
            composite = msg.get("composite_score")
            strategy_count = msg.get("strategy_count", 0)
            def _update_gauge_from_ws(w: RiskGaugeWidget) -> None:
                w.composite_score = composite
                w.strategy_count = strategy_count
            safe_query(self, "#risk-gauge", RiskGaugeWidget, _update_gauge_from_ws)

            # Update drawdown
            safe_query(self, "#risk-drawdown", DrawdownSparklineWidget,
                       lambda w: setattr(w, "max_drawdown", msg.get("max_drawdown")))

            # Update correlation matrix
            safe_query(self, "#risk-correlation", CorrelationHeatmapWidget,
                       lambda w: setattr(w, "matrix", msg.get("correlation_matrix")))

            self.status_text = "Live · Risk · WS updated"
            if composite is not None:
                self.notify(f"Risk score updated: {composite:.2f}", severity="information", timeout=2)
        except Exception as exc:
            logger.debug("WS risk update handler error: %s", exc)

    # ── Data Fetching ────────────────────────────────────────────────

    async def _fetch_data(self) -> None:
        """Fetch all risk data and update widgets."""
        await self._fetch_risk_data()
        self._update_status_text(
            "Live \u00b7 Risk \u00b7 refreshed  [r]efresh  [j/k]scroll  [f]ilter  [?]help"
        )

    async def _fetch_risk_data(self) -> None:
        """Fetch risk metrics from the /risk endpoint.

        Zero-copy: API response fields are passed directly as
        references to widget reactive attributes.  Lists from the
        response are stored as-is (no intermediate copies).
        """
        if self._api is None:
            return
        try:
            data = await self._api.get_risk()

            # Update composite score gauge — pass references
            def _update_gauge(w: RiskGaugeWidget) -> None:
                w.composite_score = data.get("composite_score")
                w.sub_scores = data.get("sub_scores", {})
                w.strategy_count = int(data.get("strategy_count", 0))
            safe_query(self, "#risk-gauge", RiskGaugeWidget, _update_gauge)

            # Update drawdown sparkline — pass references
            def _update_dd(w: DrawdownSparklineWidget) -> None:
                w.drawdown_history = data.get("drawdown_history", [])
                w.max_drawdown = data.get("max_drawdown")
                w.current_drawdown = data.get("current_drawdown")
                w.recovery_periods = data.get("recovery_periods")
            safe_query(self, "#risk-drawdown", DrawdownSparklineWidget, _update_dd)

            # Update correlation matrix — pass references
            def _update_corr(w: CorrelationHeatmapWidget) -> None:
                w.matrix = data.get("correlation_matrix")
                w.strategy_names = data.get("strategy_names", [])
            safe_query(self, "#risk-correlation", CorrelationHeatmapWidget, _update_corr)

            # Update alerts — store reference, not a copy
            self._all_alerts = data.get("alerts", [])
            self._apply_alert_filter()

        except Exception as exc:
            logger.debug("Risk data fetch failed: %s", exc)
            # Set empty state on all widgets
            safe_query(self, "#risk-gauge", RiskGaugeWidget,
                       lambda w: setattr(w, "composite_score", None))
            safe_query(self, "#risk-drawdown", DrawdownSparklineWidget,
                       lambda w: setattr(w, "drawdown_history", []))
            safe_query(self, "#risk-correlation", CorrelationHeatmapWidget,
                       lambda w: setattr(w, "matrix", None))
            safe_query(self, "#risk-alerts", AlertStreamWidget,
                       lambda w: setattr(w, "alerts", []))

    def _apply_alert_filter(self) -> None:
        """Apply the current severity filter to the alert list.

        Zero-copy: when showing all alerts, shares the reference to
        ``_all_alerts`` instead of creating a copy.
        """
        if self._filter_severity == "all":
            # Share reference — no copy needed
            filtered = self._all_alerts
        else:
            filtered = [
                a for a in self._all_alerts
                if str(a.get("severity", "")).lower() == self._filter_severity
            ]
        safe_query(self, "#risk-alerts", AlertStreamWidget,
                   lambda w: setattr(w, "alerts", filtered))

    # ── Actions ──────────────────────────────────────────────────────

    def action_move_down(self) -> None:
        """Scroll the alert stream down."""
        safe_query(self, "#risk-alerts", AlertStreamWidget, lambda w: w.scroll_down())

    def action_move_up(self) -> None:
        """Scroll the alert stream up."""
        safe_query(self, "#risk-alerts", AlertStreamWidget, lambda w: w.scroll_up())

    def action_filter_alerts(self) -> None:
        """Cycle through alert severity filters: all → critical → warning → info → all."""
        cycle = ["all", "critical", "warning", "info"]
        current = cycle.index(self._filter_severity) if self._filter_severity in cycle else 0
        self._filter_severity = cycle[(current + 1) % len(cycle)]
        self._apply_alert_filter()
        self.notify(
            title="Alert Filter",
            message=f"Showing: {self._filter_severity}",
            timeout=2,
        )
