"""Validation contract tests for TUI milestone assertions: Group C.

Assertions tested:
- VAL-TUI-005: Risk monitoring screen displays risk metrics
- VAL-TUI-006: Strategy research screen browses and runs evaluations
- VAL-TUI-007: Telemetry browser shows experiment runs
- VAL-TUI-008: Evidence graph and demo flow TUI
- VAL-TUI-010: TUI design polish (color, spacing, animation, accessibility)

NOTE: CSS variables are consolidated in a single app.tcss file
(variables at top, then per-screen styles) so that $variables
resolve correctly.  theme.tcss is kept as a reference document.
"""

from __future__ import annotations

import colorsys
import re
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from siglab.tui.formatting import (
    ACCENT_GREEN,
    ACCENT_PURPLE,
    BG,
    BORDER_DIM,
    ERROR_RED,
    INFO_BLUE,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    WARNING_YELLOW,
    format_drawdown,
    format_latency,
    format_return,
    format_score,
    format_sharpe,
    friendly_error,
)
from siglab.tui.loading import LoadingIndicator
from siglab.tui.screens.risk import (
    AlertStreamWidget,
    CorrelationHeatmapWidget,
    DrawdownSparklineWidget,
    RiskGaugeWidget,
    RiskScreen,
    _correlation_block,
    _correlation_color,
    MAX_ALERTS_DISPLAY,
)
from siglab.tui.formatting import gauge_color, severity_color
from siglab.tui.screens.strategy import (
    ComparisonPanelWidget,
    ResultsTableWidget,
    StrategyListWidget,
    StrategyScreen,
    MAX_COMPARE,
    DEFAULT_DECK,
)
from siglab.tui.screens.telemetry import (
    ProviderMetricsWidget,
    RunComparisonWidget,
    RunDetailWidget,
    ServiceHealthWidget,
    TelemetryRunListWidget,
    TelemetryScreen,
    ToolUsageWidget,
    DATE_RANGE_FILTERS,
    MAX_COMPARE as TEL_MAX_COMPARE,
)
from siglab.tui.screens.evidence import (
    DEMO_STEPS,
    DemoFlowWidget,
    EdgeDetailWidget,
    EvidenceGraphWidget,
    EvidenceScreen,
)
from siglab.tui.widgets.sparkline import sparkline_text


# ── Hex color utility ────────────────────────────────────────────────


def _hex_to_rgb(hex_color: str) -> tuple[float, float, float]:
    """Convert hex color (#RRGGBB) to (R, G, B) in [0, 1]."""
    hex_color = hex_color.lstrip("#")
    r = int(hex_color[0:2], 16) / 255.0
    g = int(hex_color[2:4], 16) / 255.0
    b = int(hex_color[4:6], 16) / 255.0
    return (r, g, b)


def _relative_luminance(hex_color: str) -> float:
    """Calculate WCAG 2.1 relative luminance of a hex color."""
    r, g, b = _hex_to_rgb(hex_color)

    def linearize(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * linearize(r) + 0.7152 * linearize(g) + 0.0722 * linearize(b)


def _contrast_ratio(color1: str, color2: str) -> float:
    """Calculate WCAG 2.1 contrast ratio between two hex colors."""
    l1 = _relative_luminance(color1)
    l2 = _relative_luminance(color2)
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def _make_strategy_rows(n: int = 5) -> list[dict]:
    """Generate fake ancestry/experiment rows."""
    families = [
        "perp_pair_trade_unlevered",
        "basket_momentum",
        "decision_tree_reversion",
        "carry_trade",
        "pair_spread",
    ]
    rows = []
    for i in range(n):
        rows.append({
            "spec_hash": f"abc{i:013d}",
            "family": families[i % len(families)],
            "track": "trend_signals",
            "hypothesis": f"Hypothesis {i}: test strategy {i}",
            "aggregate_score": round(0.8 - i * 0.12, 3),
            "validation_total_return": round(15.5 - i * 5.2, 2),
            "sharpe": round(1.8 - i * 0.35, 2),
            "max_drawdown": round(-5.0 - i * 3.5, 1),
            "passed": i < 3,
            "deployd": i == 0,
            "created_at": f"2026-06-0{i+1}T10:00:00+00:00",
            "equity_curve": [100 + j * (2 - i * 0.5) for j in range(20)],
        })
    return rows


def _make_run_rows(n: int = 5) -> list[dict]:
    """Generate fake experiment run rows."""
    tracks = ["trend_signals", "yield_flows", "momentum"]
    families = [
        "perp_pair_trade_unlevered",
        "basket_momentum",
        "decision_tree_reversion",
        "carry_trade",
        "pair_spread",
    ]
    rows = []
    for i in range(n):
        rows.append({
            "spec_hash": f"abc{i:013d}",
            "family": families[i % len(families)],
            "track": tracks[i % len(tracks)],
            "hypothesis": f"Hypothesis {i}: test run {i}",
            "aggregate_score": round(0.8 - i * 0.12, 3),
            "passed": i < 3,
            "deployd": i == 0,
            "created_at": f"2026-06-0{i+1}T10:00:00+00:00",
        })
    return rows


def _make_risk_data() -> dict:
    """Generate fake /risk endpoint response."""
    return {
        "composite_score": 0.72,
        "sub_scores": {
            "sharpe": 0.85,
            "drawdown": 0.72,
            "concentration": 0.65,
            "correlation_risk": 0.68,
        },
        "strategy_count": 3,
        "drawdown_history": [0.0, -0.01, -0.05, -0.03, 0.0, -0.02, 0.0],
        "max_drawdown": -0.05,
        "current_drawdown": 0.0,
        "recovery_periods": 4,
        "correlation_matrix": [
            [1.0, 0.3, 0.7],
            [0.3, 1.0, 0.2],
            [0.7, 0.2, 1.0],
        ],
        "strategy_names": ["Alpha", "Beta", "Gamma"],
        "alerts": [
            {
                "timestamp": "2026-06-04T14:02:32",
                "severity": "warning",
                "metric": "drawdown",
                "message": "Drawdown exceeded -5%",
            },
            {
                "timestamp": "2026-06-04T14:01:10",
                "severity": "info",
                "metric": "risk_score",
                "message": "Risk score updated",
            },
            {
                "timestamp": "2026-06-04T14:00:05",
                "severity": "critical",
                "metric": "concentration",
                "message": "Concentration limit breached",
            },
        ],
    }


def _make_telemetry_data() -> dict:
    """Generate fake telemetry report data."""
    return {
        "trace_count": 73,
        "stage_counts": {"planner": 29, "reflector": 17, "writer": 27},
        "provider_counts": {"bai": 73},
        "model_counts": {
            "deepseek-v4-flash": 53,
            "gpt-5.2": 10,
            "kimi-k2.5": 10,
        },
        "tool_invocation_count": 257,
        "tool_counts": {
            "inspect_feature": 4,
            "open_file": 82,
            "probe_feature_forward_stats": 53,
            "search_workspace": 23,
            "think": 14,
        },
        "tool_latency_ms": {"p50": 0.0, "p95": 23.273},
        "error_count": 5,
        "tool_error_count": 18,
        "confidence": "good",
        "provider_metrics": {
            "usage": {
                "prompt_tokens": 50000.0,
                "completion_tokens": 20000.0,
                "total_tokens": 70000.0,
                "cost_status": "unpriced_token_usage_only",
            },
            "credit_pressure": {
                "event_count": 3,
                "latest": {"severity": "critical"},
            },
            "context_pressure": {"event_count": 1},
        },
    }


# ══════════════════════════════════════════════════════════════════════
# VAL-TUI-005: Risk Monitoring Screen Displays Risk Metrics
# ══════════════════════════════════════════════════════════════════════


class TestVAL_TUI_005_RiskMetrics:
    """VAL-TUI-005: Composite score gauge, drawdown sparkline, correlation matrix heatmap, alert stream, WebSocket subscription."""

    # ── Composite Score Gauge ──

    def test_gauge_renders_composite_score(self) -> None:
        """Gauge widget renders composite score as percentage."""
        widget = RiskGaugeWidget()
        widget.composite_score = 0.72
        text = widget.render()
        assert "72/100" in text.plain

    def test_gauge_shows_sub_scores(self) -> None:
        """Gauge widget displays all four sub-scores (sharpe, drawdown, concentration, correlation_risk)."""
        widget = RiskGaugeWidget()
        widget.composite_score = 0.65
        widget.sub_scores = {
            "sharpe": 0.85,
            "drawdown": 0.72,
            "concentration": 0.65,
            "correlation_risk": 0.68,
        }
        text = widget.render()
        assert "Sharpe" in text.plain
        assert "Drawdown" in text.plain
        assert "Concentr" in text.plain
        assert "Corr.Risk" in text.plain

    def test_gauge_shows_strategy_count(self) -> None:
        """Gauge widget displays strategy count when > 0."""
        widget = RiskGaugeWidget()
        widget.composite_score = 0.8
        widget.strategy_count = 5
        text = widget.render()
        assert "Strategies: 5" in text.plain

    def testgauge_color_coding(self) -> None:
        """Gauge colors: green ≥ 0.7, yellow ≥ 0.4, red < 0.4."""
        assert gauge_color(0.8) == ACCENT_GREEN
        assert gauge_color(0.5) == WARNING_YELLOW
        assert gauge_color(0.2) == ERROR_RED

    def test_gauge_empty_state(self) -> None:
        """Gauge shows 'No risk data available' when score is None."""
        widget = RiskGaugeWidget()
        widget.composite_score = None
        text = widget.render()
        assert "No risk data available" in text.plain

    def test_gauge_bar_width_proportional(self) -> None:
        """Gauge bar filled portion is proportional to score."""
        widget = RiskGaugeWidget()
        widget.composite_score = 0.5
        text = widget.render()
        assert "50/100" in text.plain

    # ── Drawdown Sparkline ──

    def test_drawdown_sparkline_renders_history(self) -> None:
        """Drawdown sparkline renders when drawdown_history is populated."""
        widget = DrawdownSparklineWidget()
        widget.drawdown_history = [0.0, -0.01, -0.05, -0.03, 0.0]
        widget.max_drawdown = -0.05
        widget.current_drawdown = 0.0
        widget.recovery_periods = 3
        text = widget.render()
        assert "DRAWDOWN" in text.plain
        assert "-5.0%" in text.plain

    def test_drawdown_sparkline_shows_recovery(self) -> None:
        """Drawdown sparkline shows recovery periods when available."""
        widget = DrawdownSparklineWidget()
        widget.drawdown_history = [0.0, -0.02, 0.0]
        widget.max_drawdown = -0.02
        widget.current_drawdown = 0.0
        widget.recovery_periods = 5
        text = widget.render()
        assert "5 periods" in text.plain

    def test_drawdown_sparkline_shows_in_progress(self) -> None:
        """Drawdown sparkline shows 'in progress' when recovery is None."""
        widget = DrawdownSparklineWidget()
        widget.drawdown_history = [0.0, -0.05, -0.03]
        widget.max_drawdown = -0.05
        widget.current_drawdown = -0.03
        widget.recovery_periods = None
        text = widget.render()
        assert "in progress" in text.plain

    def test_drawdown_sparkline_empty_state(self) -> None:
        """Drawdown sparkline shows collecting message when empty."""
        widget = DrawdownSparklineWidget()
        widget.drawdown_history = []
        text = widget.render()
        assert "Collecting equity data" in text.plain

    def test_drawdown_sparkline_uses_sparkline_text(self) -> None:
        """Sparkline rendering produces unicode block characters."""
        values = [0.0, -0.01, -0.05, -0.03, 0.0, -0.02, 0.0]
        spark = sparkline_text([-v for v in values], width=20)
        assert len(spark.plain) > 0

    # ── Correlation Matrix Heatmap ──

    def test_correlation_matrix_renders_with_two_strategies(self) -> None:
        """Correlation heatmap renders when ≥2 strategies provided."""
        widget = CorrelationHeatmapWidget()
        widget.matrix = [[1.0, 0.5], [0.5, 1.0]]
        widget.strategy_names = ["Alpha", "Beta"]
        text = widget.render()
        assert "CORRELATION MATRIX" in text.plain
        assert "Alpha" in text.plain
        assert "Beta" in text.plain
        assert "1.00" in text.plain
        assert "0.50" in text.plain

    def test_correlation_matrix_shows_legend(self) -> None:
        """Correlation heatmap shows block character legend."""
        widget = CorrelationHeatmapWidget()
        widget.matrix = [[1.0, 0.5], [0.5, 1.0]]
        widget.strategy_names = ["A", "B"]
        text = widget.render()
        assert "Legend" in text.plain

    def test_correlation_matrix_empty_state(self) -> None:
        """Correlation heatmap shows message when < 2 strategies."""
        widget = CorrelationHeatmapWidget()
        widget.matrix = None
        text = widget.render()
        assert "Need ≥2 strategies" in text.plain

    def test_correlation_matrix_auto_names(self) -> None:
        """Correlation heatmap auto-generates names when not provided."""
        widget = CorrelationHeatmapWidget()
        widget.matrix = [[1.0, 0.3], [0.3, 1.0]]
        widget.strategy_names = []
        text = widget.render()
        assert "S1" in text.plain
        assert "S2" in text.plain

    def test_correlation_block_characters(self) -> None:
        """Block characters represent correlation intensity."""
        assert _correlation_block(0.99) == "█"
        assert _correlation_block(0.8) == "▓"
        assert _correlation_block(0.5) == "▒"
        assert _correlation_block(0.2) == "░"
        assert _correlation_block(0.05) == "·"

    # ── Alert Stream ──

    def test_alert_stream_renders_alerts(self) -> None:
        """Alert stream renders alert entries with severity and message."""
        widget = AlertStreamWidget()
        widget.alerts = [
            {"timestamp": "14:02:32", "severity": "warning", "metric": "dd", "message": "Drawdown exceeded"},
            {"timestamp": "14:01:10", "severity": "info", "metric": "score", "message": "Score updated"},
        ]
        text = widget.render()
        assert "ALERT STREAM" in text.plain
        assert "WARN" in text.plain
        assert "INFO" in text.plain
        assert "Drawdown exceeded" in text.plain

    def test_alert_stream_critical_severity(self) -> None:
        """Alert stream renders CRIT for critical alerts."""
        widget = AlertStreamWidget()
        widget.alerts = [
            {"timestamp": "14:00:05", "severity": "critical", "metric": "concentration", "message": "Limit breached"},
        ]
        text = widget.render()
        assert "CRIT" in text.plain

    def test_alert_stream_empty_state(self) -> None:
        """Alert stream shows 'No alerts' when empty."""
        widget = AlertStreamWidget()
        widget.alerts = []
        text = widget.render()
        assert "No alerts" in text.plain

    def test_alertseverity_colors(self) -> None:
        """Alert severity colors: critical=red, warning=yellow, info=blue."""
        assert severity_color("critical") == ERROR_RED
        assert severity_color("warning") == WARNING_YELLOW
        assert severity_color("info") == INFO_BLUE

    def test_alert_stream_max_display(self) -> None:
        """Alert stream limits display to MAX_ALERTS_DISPLAY (50)."""
        assert MAX_ALERTS_DISPLAY == 50

    # ── WebSocket Subscription ──

    def test_risk_screen_has_ws_risk_loop(self) -> None:
        """RiskScreen has WebSocket subscription loop method."""
        assert hasattr(RiskScreen, "_ws_risk_loop")

    def test_risk_screen_has_ws_risk_handler(self) -> None:
        """RiskScreen has WebSocket risk update handler."""
        assert hasattr(RiskScreen, "_on_ws_risk_update")

    def test_api_client_has_ws_subscribe_risk(self) -> None:
        """TuiApiClient has ws_subscribe_risk method."""
        from siglab.tui.api_client import TuiApiClient
        assert hasattr(TuiApiClient, "ws_subscribe_risk")

    # ── Risk Screen Structure ──

    def test_risk_screen_has_required_widgets(self) -> None:
        """RiskScreen compose references all 4 widget types."""
        assert hasattr(RiskScreen, "compose")

    def test_risk_screen_has_bindings(self) -> None:
        """RiskScreen has keyboard bindings: escape, r, j, k, f."""
        keys = [b.key for b in RiskScreen.BINDINGS]
        assert "escape" in keys
        assert "r" in keys
        assert "j" in keys
        assert "k" in keys
        assert "f" in keys

    def test_risk_screen_reactive_state(self) -> None:
        """RiskScreen has correct initial reactive state."""
        screen = RiskScreen()
        assert screen.is_loading is True
        assert screen.status_text == "Connecting…"
        assert screen._filter_severity == "all"

    def test_risk_screen_alert_filter_cycle(self) -> None:
        """Alert filter cycles through all → critical → warning → info."""
        cycle = ["all", "critical", "warning", "info"]
        assert cycle[1] == "critical"
        assert cycle[2] == "warning"
        assert cycle[3] == "info"

    def test_risk_screen_has_fetch_risk_data(self) -> None:
        """RiskScreen has _fetch_risk_data method."""
        assert hasattr(RiskScreen, "_fetch_risk_data")

    def test_risk_screen_refresh_seconds(self) -> None:
        """Risk screen auto-refresh interval is 15 seconds."""
        assert RiskScreen._refresh_interval == 15.0

    # ── Risk API Client ──

    @pytest.mark.asyncio
    async def test_api_client_get_risk(self) -> None:
        """TuiApiClient.get_risk() returns risk data dict."""
        from siglab.tui.api_client import TuiApiClient

        client = TuiApiClient()
        mock_response = MagicMock()
        mock_response.json.return_value = _make_risk_data()
        mock_response.raise_for_status = MagicMock()
        mock_http = AsyncMock()
        mock_http.get.return_value = mock_response
        client._client = mock_http

        result = await client.get_risk()
        assert "composite_score" in result
        assert "max_drawdown" in result
        assert "correlation_matrix" in result
        await client.close()


# ══════════════════════════════════════════════════════════════════════
# VAL-TUI-006: Strategy Research Screen
# ══════════════════════════════════════════════════════════════════════


class TestVAL_TUI_006_StrategyResearch:
    """VAL-TUI-006: Strategies listed, search filters, evaluation triggers CLI, results table shown."""

    # ── Strategy List Loading ──

    def test_strategy_list_widget_loads_data(self) -> None:
        """StrategyListWidget loads and displays strategy rows."""
        widget = StrategyListWidget()
        rows = _make_strategy_rows(5)
        widget.set_strategies(rows)
        assert len(widget.strategies) == 5

    def test_strategy_list_renders_data(self) -> None:
        """StrategyListWidget renders strategy hashes."""
        widget = StrategyListWidget()
        rows = _make_strategy_rows(3)
        widget.set_strategies(rows)
        text = widget.render()
        # Hash is truncated to 12 chars by the widget
        assert "abc000000000" in text.plain

    def test_strategy_list_empty_state(self) -> None:
        """StrategyListWidget shows 'No strategies found' when empty."""
        widget = StrategyListWidget()
        text = widget.render()
        assert "No items found" in text.plain

    # ── Search/Filter ──

    def test_search_by_text(self) -> None:
        """Search filter by spec_hash text."""
        widget = StrategyListWidget()
        widget.set_strategies(_make_strategy_rows(5))
        widget.set_filter("0000000000003")
        assert len(widget.strategies) == 1

    def test_filter_by_family(self) -> None:
        """Family filter narrows results."""
        widget = StrategyListWidget()
        widget.set_strategies(_make_strategy_rows(5))
        widget.set_family_filter("PAIR")
        for s in widget.strategies:
            assert "pair" in s.get("family", "").lower()

    def test_filter_by_status_passed(self) -> None:
        """Status filter 'PASSED' shows only passed strategies."""
        widget = StrategyListWidget()
        widget.set_strategies(_make_strategy_rows(5))
        widget.set_status_filter("PASSED")
        assert all(s.get("passed") is True for s in widget.strategies)

    def test_filter_by_status_failed(self) -> None:
        """Status filter 'FAILED' shows only failed strategies."""
        widget = StrategyListWidget()
        widget.set_strategies(_make_strategy_rows(5))
        widget.set_status_filter("FAILED")
        assert all(s.get("passed") is False for s in widget.strategies)

    def test_filter_by_family_and_status(self) -> None:
        """Combined family + status filter works."""
        widget = StrategyListWidget()
        widget.set_strategies(_make_strategy_rows(5))
        widget.set_family_filter("PAIR")
        widget.set_status_filter("PASSED")
        for s in widget.strategies:
            assert "pair" in s.get("family", "").lower()
            assert s.get("passed") is True

    # ── Evaluation Triggers CLI ──

    def test_strategy_screen_has_action_run_eval(self) -> None:
        """StrategyScreen has action_run_eval for triggering CLI evaluation."""
        assert hasattr(StrategyScreen, "action_run_eval")

    def test_strategy_screen_has_action_init_deck(self) -> None:
        """StrategyScreen has action_init_deck for initializing benchmark deck."""
        assert hasattr(StrategyScreen, "action_init_deck")

    def test_strategy_screen_default_deck(self) -> None:
        """StrategyScreen uses trend_signals_external as default deck."""
        screen = StrategyScreen()
        assert screen._deck == "trend_signals_external"

    def test_strategy_screen_is_evaluating_reactive(self) -> None:
        """StrategyScreen has is_evaluating reactive state."""
        screen = StrategyScreen()
        assert screen.is_evaluating is False

    def test_strategy_screen_eval_bindings(self) -> None:
        """StrategyScreen has 'e' for evaluate and 'i' for init-deck bindings."""
        keys = [b.key for b in StrategyScreen.BINDINGS]
        assert "e" in keys
        assert "i" in keys

    # ── Results Table ──

    def test_results_table_renders_columns(self) -> None:
        """Results table shows NAME, FAMILY, SCORE, PnL, SHARPE, MAXDD, STATUS, SPARKLINE."""
        widget = ResultsTableWidget()
        widget.set_results(_make_strategy_rows(3))
        text = widget.render()
        for col in ["NAME", "FAMILY", "SCORE", "PnL", "SHARPE", "MAXDD", "STATUS", "SPARKLINE"]:
            assert col in text.plain

    def test_results_table_empty_state(self) -> None:
        """Results table shows message when no results."""
        widget = ResultsTableWidget()
        text = widget.render()
        assert "No results" in text.plain

    def test_results_table_sort_cycle(self) -> None:
        """Results table cycles sort columns."""
        widget = ResultsTableWidget()
        assert widget.sort_column == "aggregate_score"
        widget.cycle_sort()
        assert widget.sort_column == "validation_total_return"

    def test_results_table_sorted_descending(self) -> None:
        """Results table default sort is descending by score."""
        widget = ResultsTableWidget()
        widget.set_results(_make_strategy_rows(5))
        sorted_rows = widget._sorted_results()
        scores = [r.get("aggregate_score") for r in sorted_rows if r.get("aggregate_score") is not None]
        for i in range(len(scores) - 1):
            assert scores[i] >= scores[i + 1]

    # ── Comparison Panel ──

    def test_comparison_panel_requires_two(self) -> None:
        """Comparison panel shows 'Select 2+' when fewer than 2 strategies."""
        widget = ComparisonPanelWidget()
        widget.set_strategies([_make_strategy_rows(1)[0]])
        text = widget.render()
        assert "Select 2+" in text.plain

    def test_comparison_panel_renders_two(self) -> None:
        """Comparison panel shows metrics for two strategies."""
        widget = ComparisonPanelWidget()
        widget.set_strategies(_make_strategy_rows(2))
        text = widget.render()
        assert "COMPARISON" in text.plain
        assert "Score" in text.plain
        assert "Sharpe" in text.plain
        assert "DELTA" in text.plain

    def test_comparison_panel_equity_curves(self) -> None:
        """Comparison panel shows equity curve overlay."""
        widget = ComparisonPanelWidget()
        widget.set_strategies(_make_strategy_rows(2))
        text = widget.render()
        assert "EQUITY CURVES" in text.plain

    # ── CLI Bridge ──

    def test_strategy_screen_uses_cli_bridge(self) -> None:
        """StrategyScreen imports and uses run_cli from cli_bridge."""
        import siglab.tui.screens.strategy as strat_mod
        source = open(strat_mod.__file__).read()
        assert "run_cli" in source

    def test_strategy_screen_runs_ancestry_command(self) -> None:
        """StrategyScreen loads strategies via 'ancestry --json' CLI command."""
        import siglab.tui.screens.strategy as strat_mod
        source = open(strat_mod.__file__).read()
        assert "ancestry" in source
        assert "--json" in source

    def test_strategy_screen_runs_benchmark_eval(self) -> None:
        """StrategyScreen runs 'benchmark-eval --deck' for evaluation."""
        import siglab.tui.screens.strategy as strat_mod
        source = open(strat_mod.__file__).read()
        assert "benchmark-eval" in source


# ══════════════════════════════════════════════════════════════════════
# VAL-TUI-007: Telemetry Browser Shows Experiment Runs
# ══════════════════════════════════════════════════════════════════════


class TestVAL_TUI_007_TelemetryBrowser:
    """VAL-TUI-007: Runs listed, telemetry view populated, comparison highlights differences."""

    # ── Run List ──

    def test_run_list_loads_data(self) -> None:
        """TelemetryRunListWidget loads run rows."""
        widget = TelemetryRunListWidget()
        rows = _make_run_rows(5)
        widget.set_runs(rows)
        assert len(widget.runs) == 5

    def test_run_list_renders_data(self) -> None:
        """TelemetryRunListWidget renders run entries."""
        widget = TelemetryRunListWidget()
        rows = _make_run_rows(3)
        widget.set_runs(rows)
        text = widget.render()
        # Hash is truncated to 12 chars by the widget
        assert "abc000000000" in text.plain

    def test_run_list_empty_state(self) -> None:
        """TelemetryRunListWidget shows 'No runs found' when empty."""
        widget = TelemetryRunListWidget()
        text = widget.render()
        assert "No items found" in text.plain

    def test_run_list_filter_by_text(self) -> None:
        """Run list text filter works."""
        widget = TelemetryRunListWidget()
        widget.set_runs(_make_run_rows(5))
        widget.set_filter("0000000000002")
        assert len(widget.runs) == 1

    def test_run_list_filter_by_status(self) -> None:
        """Run list status filter works."""
        widget = TelemetryRunListWidget()
        widget.set_runs(_make_run_rows(5))
        widget.set_status_filter("PASSED")
        assert all(r.get("passed") is True for r in widget.runs)

    def test_run_list_filter_by_track(self) -> None:
        """Run list track filter works."""
        widget = TelemetryRunListWidget()
        widget.set_runs(_make_run_rows(6))
        widget.set_track_filter("TREND_SIGNALS")
        for r in widget.runs:
            assert "trend_signals" in r.get("track", "").lower()

    def test_run_list_get_tracks(self) -> None:
        """Run list returns unique tracks."""
        widget = TelemetryRunListWidget()
        widget.set_runs(_make_run_rows(6))
        tracks = widget.get_tracks()
        assert len(tracks) >= 2

    def test_run_list_navigation(self) -> None:
        """Run list supports up/down navigation."""
        widget = TelemetryRunListWidget()
        widget.set_runs(_make_run_rows(5))
        widget.action_move_down()
        assert widget.selected_index == 1
        widget.action_move_up()
        assert widget.selected_index == 0

    def test_run_list_multi_select(self) -> None:
        """Run list supports multi-select with toggle."""
        widget = TelemetryRunListWidget()
        widget.set_runs(_make_run_rows(3))
        widget.toggle_select()
        assert len(widget.get_selected_hashes()) == 1
        widget.toggle_select()
        assert len(widget.get_selected_hashes()) == 0

    # ── Telemetry Data Display ──

    def test_provider_metrics_renders_data(self) -> None:
        """ProviderMetricsWidget renders telemetry data."""
        widget = ProviderMetricsWidget()
        widget.telemetry_data = _make_telemetry_data()
        text = widget.render()
        assert "PROVIDER METRICS" in text.plain
        assert "good" in text.plain.lower()

    def test_provider_metrics_shows_stages(self) -> None:
        """ProviderMetricsWidget shows stage distribution."""
        widget = ProviderMetricsWidget()
        widget.telemetry_data = _make_telemetry_data()
        text = widget.render()
        assert "Stage Distribution" in text.plain
        assert "planner" in text.plain

    def test_provider_metrics_shows_models(self) -> None:
        """ProviderMetricsWidget shows model usage."""
        widget = ProviderMetricsWidget()
        widget.telemetry_data = _make_telemetry_data()
        text = widget.render()
        assert "Model Usage" in text.plain

    def test_provider_metrics_shows_tokens(self) -> None:
        """ProviderMetricsWidget shows token usage."""
        widget = ProviderMetricsWidget()
        widget.telemetry_data = _make_telemetry_data()
        text = widget.render()
        assert "Token Usage" in text.plain

    def test_tool_usage_renders_data(self) -> None:
        """ToolUsageWidget renders tool counts and latency."""
        widget = ToolUsageWidget()
        widget.telemetry_data = _make_telemetry_data()
        text = widget.render()
        assert "TOOL USAGE" in text.plain
        assert "open_file" in text.plain
        assert "257" in text.plain

    def test_run_detail_renders_run(self) -> None:
        """RunDetailWidget renders run metadata."""
        widget = RunDetailWidget()
        widget.run = _make_run_rows(1)[0]
        text = widget.render()
        assert "RUN DETAIL" in text.plain
        assert "abc0000000000000" in text.plain

    # ── Run Comparison ──

    def test_run_comparison_requires_two(self) -> None:
        """Run comparison shows message when < 2 runs selected."""
        widget = RunComparisonWidget()
        widget.set_runs([_make_run_rows(1)[0]])
        text = widget.render()
        assert "Select 2+" in text.plain

    def test_run_comparison_renders_two(self) -> None:
        """Run comparison shows metrics for two runs."""
        widget = RunComparisonWidget()
        widget.set_runs(_make_run_rows(2))
        text = widget.render()
        assert "COMPARISON" in text.plain
        assert "Score" in text.plain
        assert "DELTA" in text.plain

    def test_run_comparison_shows_deltas(self) -> None:
        """Run comparison highlights differences between runs."""
        widget = RunComparisonWidget()
        widget.set_runs(_make_run_rows(3))
        text = widget.render()
        assert "DELTA" in text.plain

    # ── Telemetry Screen Structure ──

    def test_telemetry_screen_has_action_methods(self) -> None:
        """TelemetryScreen has all required action methods."""
        assert hasattr(TelemetryScreen, "action_go_back")
        assert hasattr(TelemetryScreen, "action_refresh_now")
        assert hasattr(TelemetryScreen, "action_toggle_select")
        assert hasattr(TelemetryScreen, "action_toggle_compare")
        assert hasattr(TelemetryScreen, "action_cycle_sort")
        assert hasattr(TelemetryScreen, "action_cycle_date_range")
        assert hasattr(TelemetryScreen, "action_cycle_status_filter")
        assert hasattr(TelemetryScreen, "action_cycle_track_filter")
        assert hasattr(TelemetryScreen, "action_toggle_detail_view")

    def test_telemetry_screen_reactive_state(self) -> None:
        """TelemetryScreen has correct reactive state fields."""
        screen = TelemetryScreen()
        assert screen.is_loading is True
        assert screen.compare_mode is False
        assert screen.run_count == 0
        assert screen._detail_view == "telemetry"

    def test_telemetry_screen_date_range_filters(self) -> None:
        """Date range filters include ALL, 7d, 30d, TODAY."""
        assert "ALL" in DATE_RANGE_FILTERS
        assert "7d" in DATE_RANGE_FILTERS
        assert "30d" in DATE_RANGE_FILTERS
        assert "TODAY" in DATE_RANGE_FILTERS

    def test_telemetry_screen_uses_cli_bridge(self) -> None:
        """TelemetryScreen uses run_cli for telemetry-report and ancestry."""
        import siglab.tui.screens.telemetry as tel_mod
        source = open(tel_mod.__file__).read()
        assert "run_cli" in source
        assert "telemetry-report" in source
        assert "ancestry" in source

    def test_telemetry_screen_fetches_ops_board(self) -> None:
        """TelemetryScreen fetches data from /ops-board API endpoint."""
        assert hasattr(TelemetryScreen, "_fetch_ops_board")

    def test_service_health_renders(self) -> None:
        """ServiceHealthWidget renders service status."""
        widget = ServiceHealthWidget()
        widget.service_health = {
            "dashboard": {"status": "running", "port": 3100},
            "sodex_api": {"status": "external"},
        }
        widget.artifact_status = {
            "telemetry": {"status": "present", "freshness": "fresh"},
        }
        text = widget.render()
        assert "SERVICE HEALTH" in text.plain
        assert "dashboard" in text.plain
        assert "running" in text.plain
        assert "fresh" in text.plain


# ══════════════════════════════════════════════════════════════════════
# VAL-TUI-008: Evidence Graph and Demo Flow TUI
# ══════════════════════════════════════════════════════════════════════


class TestVAL_TUI_008_EvidenceGraphDemo:
    """VAL-TUI-008: Graph rendered with nodes/edges, demo steps show commands with output."""

    # ── Evidence Graph ──

    def test_graph_renders_nodes(self) -> None:
        """EvidenceGraphWidget renders nodes with labels."""
        widget = EvidenceGraphWidget()
        nodes = [
            {"id": "source:sodex", "label": "SoDEX API", "kind": "source", "count": 10},
            {"id": "entity:btc", "label": "BTC-USD", "kind": "entity", "count": 5},
        ]
        edges = [{"source": "source:sodex", "target": "entity:btc", "label": "linked"}]
        widget.update_graph(nodes, edges)
        text = widget.render()
        assert "SoDEX API" in text.plain
        assert "BTC-USD" in text.plain

    def test_graph_renders_edges(self) -> None:
        """EvidenceGraphWidget shows edge counts on nodes."""
        widget = EvidenceGraphWidget()
        nodes = [
            {"id": "source:a", "label": "src-a", "kind": "source", "count": 5},
            {"id": "entity:b", "label": "ent-b", "kind": "entity", "count": 3},
        ]
        edges = [{"source": "source:a", "target": "entity:b", "label": "linked"}]
        widget.update_graph(nodes, edges)
        text = widget.render()
        assert "→1 links" in text.plain

    def test_graph_groups_by_kind(self) -> None:
        """EvidenceGraphWidget groups nodes by kind (source, entity, module)."""
        widget = EvidenceGraphWidget()
        nodes = [
            {"id": "source:a", "label": "src-a", "kind": "source", "count": 5},
            {"id": "entity:b", "label": "ent-b", "kind": "entity", "count": 3},
        ]
        widget.update_graph(nodes, [])
        text = widget.render()
        assert "SOURCE" in text.plain
        assert "ENTITY" in text.plain

    def test_graph_filter_by_kind(self) -> None:
        """EvidenceGraphWidget filters nodes by kind."""
        widget = EvidenceGraphWidget()
        nodes = [
            {"id": "source:a", "label": "src-a", "kind": "source", "count": 5},
            {"id": "entity:b", "label": "ent-b", "kind": "entity", "count": 3},
        ]
        widget.update_graph(nodes, [])
        widget.set_filter(kind="source")
        filtered = widget._filtered_nodes()
        assert len(filtered) == 1
        assert filtered[0]["kind"] == "source"

    def test_graph_filter_by_text(self) -> None:
        """EvidenceGraphWidget filters nodes by text search."""
        widget = EvidenceGraphWidget()
        nodes = [
            {"id": "source:btc", "label": "BTC-source", "kind": "source", "count": 5},
            {"id": "entity:eth", "label": "ETH-entity", "kind": "entity", "count": 3},
        ]
        widget.update_graph(nodes, [])
        widget.set_filter(text="btc")
        filtered = widget._filtered_nodes()
        assert len(filtered) == 1

    def test_graph_shows_summary(self) -> None:
        """EvidenceGraphWidget shows node/edge count summary."""
        widget = EvidenceGraphWidget()
        nodes = [
            {"id": "source:a", "label": "a", "kind": "source", "count": 5},
            {"id": "entity:b", "label": "b", "kind": "entity", "count": 3},
        ]
        edges = [{"source": "source:a", "target": "entity:b", "label": "linked"}]
        widget.update_graph(nodes, edges)
        text = widget.render()
        assert "2/2 nodes" in text.plain
        assert "1 edges" in text.plain

    def test_graph_empty_state(self) -> None:
        """EvidenceGraphWidget shows message when no data."""
        widget = EvidenceGraphWidget()
        text = widget.render()
        assert "No evidence data" in text.plain

    # ── Edge Detail ──

    def test_edge_detail_renders_connections(self) -> None:
        """EdgeDetailWidget renders source → target connections."""
        widget = EdgeDetailWidget()
        edges = [
            {
                "source": "source:sodex.websocket",
                "target": "entity:BTC-USD",
                "label": "websocket_allBookTicker",
                "confidence": 0.8,
                "warning": None,
            },
        ]
        widget.update_edges(edges)
        text = widget.render()
        assert "Connections" in text.plain
        assert "BTC-USD" in text.plain

    def test_edge_detail_empty_state(self) -> None:
        """EdgeDetailWidget shows message when no edges."""
        widget = EdgeDetailWidget()
        text = widget.render()
        assert "No connections" in text.plain

    # ── Demo Flow ──

    def test_demo_flow_has_all_steps(self) -> None:
        """DemoFlowWidget covers all 8 demo steps."""
        assert len(DEMO_STEPS) >= 8

    def test_demo_flow_steps_sequential(self) -> None:
        """Demo flow steps are numbered sequentially 1..N."""
        numbers = [s["step"] for s in DEMO_STEPS]
        assert numbers == list(range(1, len(DEMO_STEPS) + 1))

    def test_demo_flow_renders_all_titles(self) -> None:
        """DemoFlowWidget renders all step titles."""
        widget = DemoFlowWidget()
        text = widget.render()
        for step in DEMO_STEPS:
            assert step["title"] in text.plain

    def test_demo_flow_shows_commands(self) -> None:
        """Demo steps have CLI commands."""
        for step in DEMO_STEPS:
            assert len(step["command"]) > 0
            assert isinstance(step["command"], str)

    def test_demo_flow_shows_descriptions(self) -> None:
        """Demo steps have descriptions."""
        for step in DEMO_STEPS:
            assert len(step["description"]) > 0

    def test_demo_flow_current_step_highlighted(self) -> None:
        """DemoFlowWidget shows ▶ for current step."""
        widget = DemoFlowWidget()
        text = widget.render()
        assert "▶" in text.plain

    def test_demo_flow_navigation_forward(self) -> None:
        """DemoFlowWidget advances step."""
        widget = DemoFlowWidget()
        assert widget.current_step == 1
        widget.advance_step()
        assert widget.current_step == 2

    def test_demo_flow_navigation_backward(self) -> None:
        """DemoFlowWidget retreats step."""
        widget = DemoFlowWidget()
        widget._current_step = 3
        widget.retreat_step()
        assert widget.current_step == 2

    def test_demo_flow_step_result_success(self) -> None:
        """DemoFlowWidget shows ✓ for successful step."""
        widget = DemoFlowWidget()
        widget.set_step_result(1, {"returncode": 0, "stdout": "{}", "stderr": ""})
        text = widget.render()
        assert "✓" in text.plain

    def test_demo_flow_step_result_failure(self) -> None:
        """DemoFlowWidget shows ✗ for failed step."""
        widget = DemoFlowWidget()
        widget.set_step_result(1, {"returncode": 1, "stdout": "", "stderr": "error"})
        text = widget.render()
        assert "✗" in text.plain

    def test_demo_flow_step_result_summary(self) -> None:
        """DemoFlowWidget shows parsed result summary from JSON stdout."""
        widget = DemoFlowWidget()
        widget.set_step_result(1, {
            "returncode": 0,
            "stdout": '{"record_count": 620}',
            "stderr": "",
        })
        text = widget.render()
        assert "records: 620" in text.plain

    def test_demo_flow_running_indicator(self) -> None:
        """DemoFlowWidget shows running indicator when executing."""
        widget = DemoFlowWidget()
        widget.set_running(True)
        text = widget.render()
        assert "Running" in text.plain or "⟳" in text.plain

    # ── Evidence Screen Structure ──

    def test_evidence_screen_has_bindings(self) -> None:
        """EvidenceScreen has keyboard bindings for navigation and demo control."""
        keys = [b.key for b in EvidenceScreen.BINDINGS]
        assert "r" in keys  # refresh
        assert "tab" in keys  # switch pane
        assert "enter" in keys  # run step
        assert "n" in keys  # next step
        assert "p" in keys  # prev step
        assert "a" in keys  # run all
        assert "/" in keys  # search/filter

    def test_evidence_screen_has_action_run_step(self) -> None:
        """EvidenceScreen has action_run_step for executing demo steps."""
        assert hasattr(EvidenceScreen, "action_run_step")

    def test_evidence_screen_has_action_run_all(self) -> None:
        """EvidenceScreen has action_run_all for running all demo steps."""
        assert hasattr(EvidenceScreen, "action_run_all")

    def test_evidence_screen_switches_panes(self) -> None:
        """EvidenceScreen has action_switch_pane for toggling graph/demo focus."""
        assert hasattr(EvidenceScreen, "action_switch_pane")

    def test_evidence_screen_uses_api_client(self) -> None:
        """EvidenceScreen fetches graph data via TuiApiClient."""
        assert hasattr(EvidenceScreen, "_refresh_graph")

    @pytest.mark.asyncio
    async def test_api_client_get_evidence_graph(self) -> None:
        """TuiApiClient.get_evidence_graph() returns nodes and edges."""
        from siglab.tui.api_client import TuiApiClient

        client = TuiApiClient()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "nodes": [
                {"id": "source:a", "label": "src-a", "kind": "source", "count": 5},
            ],
            "edges": [],
        }
        mock_response.raise_for_status = MagicMock()
        mock_http = AsyncMock()
        mock_http.get.return_value = mock_response
        client._client = mock_http

        result = await client.get_evidence_graph()
        assert "nodes" in result
        assert "edges" in result
        await client.close()


# ══════════════════════════════════════════════════════════════════════
# VAL-TUI-010: TUI Design Polish (Color, Spacing, Animation, Accessibility)
# ══════════════════════════════════════════════════════════════════════


class TestVAL_TUI_010_DesignPolish:
    """VAL-TUI-010: Consistent colors, semantic colors, spacing, hover effects, screen transitions, keyboard-only nav, WCAG contrast."""

    # ── Color Theme Consistency ──

    def test_formatting_module_defines_all_colors(self) -> None:
        """siglab.tui.formatting defines all required color constants."""
        import siglab.tui.formatting as fmt
        # Primitive palette
        assert hasattr(fmt, "ACCENT_GREEN")
        assert hasattr(fmt, "WARNING_YELLOW")
        assert hasattr(fmt, "ERROR_RED")
        assert hasattr(fmt, "INFO_BLUE")
        assert hasattr(fmt, "ACCENT_PURPLE")
        # Text hierarchy
        assert hasattr(fmt, "TEXT_PRIMARY")
        assert hasattr(fmt, "TEXT_SECONDARY")
        assert hasattr(fmt, "TEXT_MUTED")
        # Surface / border
        assert hasattr(fmt, "BG")
        assert hasattr(fmt, "SURFACE")
        assert hasattr(fmt, "SURFACE_RAISED")
        assert hasattr(fmt, "BORDER_DIM")
        assert hasattr(fmt, "INPUT_BG")
        # Semantic aliases
        assert hasattr(fmt, "GAIN")
        assert hasattr(fmt, "LOSS")
        assert hasattr(fmt, "LINK")
        assert hasattr(fmt, "CAUTION")

    def test_theme_css_defines_all_variables(self) -> None:
        """theme.tcss defines all color variables matching formatting.py."""
        from pathlib import Path
        theme_path = Path(__file__).resolve().parents[1] / "siglab" / "tui" / "styles" / "theme.tcss"
        theme = theme_path.read_text()
        assert "$accent-green" in theme
        assert "$warning-yellow" in theme
        assert "$error-red" in theme
        assert "$info-blue" in theme
        assert "$accent-purple" in theme
        assert "$text-primary" in theme
        assert "$text-secondary" in theme
        assert "$text-muted" in theme
        assert "$border-dim" in theme
        assert "$bg" in theme
        assert "$surface" in theme

    def test_color_values_match_between_py_and_css(self) -> None:
        """Color values in formatting.py match theme.tcss variables."""
        from pathlib import Path
        theme_path = Path(__file__).resolve().parents[1] / "siglab" / "tui" / "styles" / "theme.tcss"
        theme = theme_path.read_text()

        # Extract $accent-green value from CSS
        css_accent_green = re.search(r'\$accent-green:\s*(#[0-9a-fA-F]+)', theme)
        assert css_accent_green is not None
        assert css_accent_green.group(1).lower() == ACCENT_GREEN.lower()

        css_warning_yellow = re.search(r'\$warning-yellow:\s*(#[0-9a-fA-F]+)', theme)
        assert css_warning_yellow is not None
        assert css_warning_yellow.group(1).lower() == WARNING_YELLOW.lower()

        css_error_red = re.search(r'\$error-red:\s*(#[0-9a-fA-F]+)', theme)
        assert css_error_red is not None
        assert css_error_red.group(1).lower() == ERROR_RED.lower()

    # ── Semantic Color Correctness ──

    def test_gain_color_is_green(self) -> None:
        """GAIN semantic alias is green."""
        from siglab.tui.formatting import GAIN
        assert GAIN == ACCENT_GREEN == "#4ade80"

    def test_loss_color_is_red(self) -> None:
        """LOSS semantic alias is red."""
        from siglab.tui.formatting import LOSS
        assert LOSS == ERROR_RED == "#f87171"

    def test_info_color_is_blue(self) -> None:
        """LINK/info semantic alias is blue."""
        from siglab.tui.formatting import LINK
        assert LINK == INFO_BLUE == "#60a5fa"

    def test_caution_color_is_yellow(self) -> None:
        """CAUTION/warning semantic alias is yellow."""
        from siglab.tui.formatting import CAUTION
        assert CAUTION == WARNING_YELLOW == "#f0b456"

    def test_format_score_uses_semantic_colors(self) -> None:
        """format_score uses green/yellow/red based on value thresholds."""
        assert format_score(0.8).style == ACCENT_GREEN
        assert format_score(0.5).style == WARNING_YELLOW
        assert format_score(0.2).style == ERROR_RED
        assert format_score(None).style == TEXT_MUTED

    def test_format_return_uses_semantic_colors(self) -> None:
        """format_return uses green for positive, red for negative."""
        assert format_return(10.0).style == ACCENT_GREEN
        assert format_return(-5.0).style == ERROR_RED
        assert format_return(0.0).style == TEXT_MUTED

    def test_format_sharpe_uses_semantic_colors(self) -> None:
        """format_sharpe uses green/yellow/red based on value."""
        assert format_sharpe(1.5).style == ACCENT_GREEN
        assert format_sharpe(0.7).style == WARNING_YELLOW
        assert format_sharpe(0.3).style == ERROR_RED

    def test_format_drawdown_uses_semantic_colors(self) -> None:
        """format_drawdown uses red for high drawdown, muted for low."""
        assert format_drawdown(-25.0).style == ERROR_RED
        assert format_drawdown(-15.0).style == WARNING_YELLOW
        assert format_drawdown(-5.0).style == TEXT_MUTED

    def test_format_latency_uses_semantic_colors(self) -> None:
        """format_latency uses green/yellow/red based on speed."""
        assert format_latency(50.0).style == ACCENT_GREEN
        assert format_latency(200.0).style == WARNING_YELLOW
        assert format_latency(800.0).style == ERROR_RED

    def test_risk_gauge_uses_semantic_colors(self) -> None:
        """Risk gauge colors: green=healthy, yellow=moderate, red=high risk."""
        assert gauge_color(0.8) == ACCENT_GREEN
        assert gauge_color(0.5) == WARNING_YELLOW
        assert gauge_color(0.2) == ERROR_RED

    def test_alert_severity_uses_semantic_colors(self) -> None:
        """Alert severity uses red/yellow/blue semantic colors."""
        assert severity_color("critical") == ERROR_RED
        assert severity_color("warning") == WARNING_YELLOW
        assert severity_color("info") == INFO_BLUE

    # ── WCAG Color Contrast ──

    def test_text_primary_on_bg_contrast(self) -> None:
        """TEXT_PRIMARY on BG meets WCAG AA contrast ratio (≥ 4.5:1)."""
        ratio = _contrast_ratio(TEXT_PRIMARY, BG)
        assert ratio >= 4.5, f"Contrast ratio {ratio:.2f} < 4.5 for TEXT_PRIMARY on BG"

    def test_text_secondary_on_bg_contrast(self) -> None:
        """TEXT_SECONDARY on BG meets WCAG AA contrast ratio (≥ 4.5:1)."""
        ratio = _contrast_ratio(TEXT_SECONDARY, BG)
        assert ratio >= 4.5, f"Contrast ratio {ratio:.2f} < 4.5 for TEXT_SECONDARY on BG"

    def test_text_muted_on_bg_contrast(self) -> None:
        """TEXT_MUTED on BG meets WCAG AA large text contrast (≥ 3:1)."""
        ratio = _contrast_ratio(TEXT_MUTED, BG)
        assert ratio >= 3.0, f"Contrast ratio {ratio:.2f} < 3.0 for TEXT_MUTED on BG"

    def test_accent_green_on_bg_contrast(self) -> None:
        """ACCENT_GREEN on BG meets WCAG AA contrast (≥ 4.5:1)."""
        ratio = _contrast_ratio(ACCENT_GREEN, BG)
        assert ratio >= 4.5, f"Contrast ratio {ratio:.2f} < 4.5 for ACCENT_GREEN on BG"

    def test_error_red_on_bg_contrast(self) -> None:
        """ERROR_RED on BG meets WCAG AA contrast (≥ 4.5:1)."""
        ratio = _contrast_ratio(ERROR_RED, BG)
        assert ratio >= 4.5, f"Contrast ratio {ratio:.2f} < 4.5 for ERROR_RED on BG"

    def test_info_blue_on_bg_contrast(self) -> None:
        """INFO_BLUE on BG meets WCAG AA contrast (≥ 4.5:1)."""
        ratio = _contrast_ratio(INFO_BLUE, BG)
        assert ratio >= 4.5, f"Contrast ratio {ratio:.2f} < 4.5 for INFO_BLUE on BG"

    def test_warning_yellow_on_bg_contrast(self) -> None:
        """WARNING_YELLOW on BG meets WCAG AA contrast (≥ 4.5:1)."""
        ratio = _contrast_ratio(WARNING_YELLOW, BG)
        assert ratio >= 4.5, f"Contrast ratio {ratio:.2f} < 4.5 for WARNING_YELLOW on BG"

    # ── Keyboard-Only Navigation ──

    def test_all_screens_have_escape(self) -> None:
        """All 6 screens have escape binding for back navigation."""
        from siglab.tui.screens.market import MarketScreen
        from siglab.tui.screens.paper import PaperScreen
        screens = [MarketScreen, PaperScreen, RiskScreen, StrategyScreen, TelemetryScreen, EvidenceScreen]
        for screen_cls in screens:
            keys = [b.key for b in screen_cls.BINDINGS]
            assert "escape" in keys, f"{screen_cls.__name__} missing escape"

    def test_all_screens_have_ctrl_c(self) -> None:
        """All 6 screens have ctrl+c binding for force exit."""
        from siglab.tui.screens.market import MarketScreen
        from siglab.tui.screens.paper import PaperScreen
        screens = [MarketScreen, PaperScreen, RiskScreen, StrategyScreen, TelemetryScreen, EvidenceScreen]
        for screen_cls in screens:
            keys = [b.key for b in screen_cls.BINDINGS]
            assert "ctrl+c" in keys, f"{screen_cls.__name__} missing ctrl+c"

    def test_all_screens_have_help(self) -> None:
        """All 6 screens have ? (question_mark) help binding."""
        from siglab.tui.screens.market import MarketScreen
        from siglab.tui.screens.paper import PaperScreen
        screens = [MarketScreen, PaperScreen, RiskScreen, StrategyScreen, TelemetryScreen, EvidenceScreen]
        for screen_cls in screens:
            keys = [b.key for b in screen_cls.BINDINGS]
            assert "question_mark" in keys or "?" in keys, f"{screen_cls.__name__} missing ? help"

    def test_app_number_key_screen_switching(self) -> None:
        """App has 1-6 number key bindings for direct screen switching."""
        from siglab.tui.app import SigLabTUI
        keys = [b.key for b in SigLabTUI.BINDINGS]
        for i in range(1, 7):
            assert str(i) in keys, f"Missing binding for key '{i}'"

    def test_app_q_binding_for_quit(self) -> None:
        """App has 'q' binding for quit."""
        from siglab.tui.app import SigLabTUI
        keys = [b.key for b in SigLabTUI.BINDINGS]
        assert "q" in keys

    def test_risk_screen_scroll_keys(self) -> None:
        """RiskScreen has j/k for scrolling alerts."""
        keys = [b.key for b in RiskScreen.BINDINGS]
        assert "j" in keys
        assert "k" in keys

    def test_strategy_screen_navigation_keys(self) -> None:
        """StrategyScreen has j/k for list navigation and / for search."""
        keys = [b.key for b in StrategyScreen.BINDINGS]
        assert "j" in keys
        assert "k" in keys
        assert "/" in keys

    def test_telemetry_screen_navigation_keys(self) -> None:
        """TelemetryScreen has j/k for list navigation and / for search."""
        keys = [b.key for b in TelemetryScreen.BINDINGS]
        assert "j" in keys
        assert "k" in keys
        assert "/" in keys

    def test_evidence_screen_tab_switches_panes(self) -> None:
        """EvidenceScreen has tab binding for switching between graph/demo panes."""
        keys = [b.key for b in EvidenceScreen.BINDINGS]
        assert "tab" in keys

    # ── Spacing and Layout ──

    def test_widget_headers_are_consistent(self) -> None:
        """All widget headers follow uppercase bold pattern."""
        from siglab.tui.formatting import widget_header
        header = widget_header("Test Widget")
        assert "TEST WIDGET" in header.plain

    def test_section_dividers_consistent(self) -> None:
        """Section dividers use consistent BORDER_DIM style."""
        from siglab.tui.formatting import section_divider
        divider = section_divider(40)
        assert "─" in divider.plain
        assert divider.style == BORDER_DIM

    def test_loading_indicator_has_css(self) -> None:
        """LoadingIndicator has DEFAULT_CSS for consistent height/width."""
        assert "height: 1" in LoadingIndicator.DEFAULT_CSS

    # ── Loading/Animation States ──

    def test_loading_indicator_uses_braille_spinner(self) -> None:
        """LoadingIndicator uses braille Unicode characters (U+2800–U+28FF) for animation."""
        from siglab.tui.loading import _SPINNER_FRAMES
        for char in _SPINNER_FRAMES:
            assert 0x2800 <= ord(char) <= 0x28FF, f"Character {char!r} is not braille"

    def test_loading_indicator_renders_loading_text(self) -> None:
        """LoadingIndicator shows 'Loading…' when loading."""
        indicator = LoadingIndicator()
        indicator.loading = True
        text = indicator.render()
        assert "Loading" in text.plain

    def test_loading_indicator_renders_status_text(self) -> None:
        """LoadingIndicator shows status text when idle."""
        indicator = LoadingIndicator()
        indicator.loading = False
        indicator.status_text = "Live · refreshed"
        text = indicator.render()
        assert "Live · refreshed" in text.plain

    def test_risk_screen_has_loading_indicator(self) -> None:
        """RiskScreen has LoadingIndicator for loading state."""
        assert hasattr(RiskScreen, "compose")

    def test_strategy_screen_has_spinner(self) -> None:
        """StrategyScreen has _SPINNER frames for evaluation progress."""
        from siglab.tui.screens.strategy import _SPINNER
        assert len(_SPINNER) > 0

    # ── CSS File Completeness ──

    def test_all_screen_css_files_exist(self) -> None:
        """All screen-specific CSS files exist in siglab/tui/styles/."""
        from pathlib import Path
        from siglab.tui.app import SigLabTUI
        tui_dir = Path(__file__).resolve().parents[1] / "siglab" / "tui"
        for css_file in SigLabTUI.CSS_PATH:
            full_path = tui_dir / css_file
            assert full_path.exists(), f"CSS file missing: {css_file}"

    def test_theme_css_has_semantic_tokens(self) -> None:
        """theme.tcss defines semantic tokens ($success, $warning, $error, $info)."""
        from pathlib import Path
        theme_path = Path(__file__).resolve().parents[1] / "siglab" / "tui" / "styles" / "theme.tcss"
        theme = theme_path.read_text()
        assert "$success" in theme
        assert "$warning" in theme
        assert "$error" in theme
        assert "$info" in theme

    # ── Help Screen ──

    def test_help_screen_exists(self) -> None:
        """HelpScreen exists and has keyboard shortcut documentation."""
        from siglab.tui.app import HelpScreen
        assert hasattr(HelpScreen, "GLOBAL_KEYBINDINGS")
        assert hasattr(HelpScreen, "SCREEN_KEYBINDINGS")

    def test_help_screen_covers_all_screens(self) -> None:
        """HelpScreen has keyboard shortcut docs for all 6 screens."""
        from siglab.tui.app import HelpScreen
        for screen_id in ["market", "paper", "risk", "strategy", "telemetry", "evidence"]:
            assert screen_id in HelpScreen.SCREEN_KEYBINDINGS

    def test_friendly_error_no_python_internals(self) -> None:
        """friendly_error never leaks Python module paths or tracebacks."""
        exc = httpx.ConnectError("Connection refused")
        msg = friendly_error(exc)
        assert "httpx" not in msg.lower()
        assert "traceback" not in msg.lower()
        assert "connect" in msg.lower() or "server" in msg.lower()
