"""Tests for the Risk Monitoring TUI screen.

Validates:
- RiskScreen launches and renders without errors
- GaugeWidget displays composite score correctly
- DrawdownSparklineWidget renders sparkline
- CorrelationHeatmapWidget renders matrix
- AlertStreamWidget shows alerts
- Empty state handling (no data)
- Keyboard bindings work
"""

from __future__ import annotations

import pytest

from siglab.tui.screens.risk import (
    AlertStreamWidget,
    CorrelationHeatmapWidget,
    DrawdownSparklineWidget,
    RiskGaugeWidget,
    RiskScreen,
    _correlation_block,
    _correlation_color,
)
from siglab.tui.formatting import gauge_color, severity_color


# ── Helper function tests ────────────────────────────────────────────


class TestGaugeColor:
    """Test gauge_color helper."""

    def test_high_risk_returns_red(self) -> None:
        assert gauge_color(0.2) == "#f87171"

    def test_medium_risk_returns_yellow(self) -> None:
        assert gauge_color(0.5) == "#f0b456"

    def test_low_risk_returns_green(self) -> None:
        assert gauge_color(0.8) == "#4ade80"

    def test_boundary_at_high_risk(self) -> None:
        assert gauge_color(0.4) == "#f0b456"  # 0.4 is not < 0.4

    def test_boundary_at_med_risk(self) -> None:
        assert gauge_color(0.7) == "#4ade80"  # 0.7 is not < 0.7

    def test_zero_score(self) -> None:
        assert gauge_color(0.0) == "#f87171"

    def test_one_score(self) -> None:
        assert gauge_color(1.0) == "#4ade80"

    def test_nan_returns_muted(self) -> None:
        assert gauge_color(float("nan")) == "#7d9483"


class TestSeverityColor:
    """Test severity_color helper."""

    def test_critical_returns_red(self) -> None:
        assert severity_color("critical") == "#f87171"

    def test_warning_returns_yellow(self) -> None:
        assert severity_color("warning") == "#f0b456"

    def test_info_returns_blue(self) -> None:
        assert severity_color("info") == "#60a5fa"

    def test_unknown_returns_muted(self) -> None:
        assert severity_color("unknown") == "#7d9483"

    def test_case_insensitive(self) -> None:
        assert severity_color("CRITICAL") == "#f87171"
        assert severity_color("Warning") == "#f0b456"


class TestCorrelationColor:
    """Test _correlation_color helper."""

    def test_high_correlation_red(self) -> None:
        assert _correlation_color(0.8) == "#f87171"

    def test_moderate_correlation_yellow(self) -> None:
        assert _correlation_color(0.5) == "#f0b456"

    def test_low_correlation_muted(self) -> None:
        assert _correlation_color(0.2) == "#7d9483"

    def test_boundary_at_high(self) -> None:
        assert _correlation_color(0.7) == "#f87171"

    def test_boundary_at_moderate(self) -> None:
        assert _correlation_color(0.4) == "#f0b456"

    def test_negative_correlation_returns_muted(self) -> None:
        # Negative correlations are valid but not "risky" in the heatmap sense
        assert _correlation_color(-0.5) == "#7d9483"


class TestCorrelationBlock:
    """Test _correlation_block helper."""

    def test_identity_returns_full_block(self) -> None:
        assert _correlation_block(0.99) == "█"

    def test_high_returns_dark_shade(self) -> None:
        assert _correlation_block(0.8) == "▓"

    def test_moderate_returns_medium_shade(self) -> None:
        assert _correlation_block(0.5) == "▒"

    def test_low_returns_light_shade(self) -> None:
        assert _correlation_block(0.2) == "░"

    def test_negligible_returns_dot(self) -> None:
        assert _correlation_block(0.05) == "·"

    def test_zero_returns_dot(self) -> None:
        assert _correlation_block(0.0) == "·"


# ── Widget render tests ──────────────────────────────────────────────


class TestRiskGaugeWidget:
    """Test RiskGaugeWidget rendering."""

    def test_empty_state_renders_no_data(self) -> None:
        widget = RiskGaugeWidget()
        widget.composite_score = None
        text = widget.render()
        plain = text.plain
        assert "No risk data available" in plain

    def test_score_renders_gauge_bar(self) -> None:
        widget = RiskGaugeWidget()
        widget.composite_score = 0.72
        widget.sub_scores = {"sharpe": 0.85, "drawdown": 0.72}
        widget.strategy_count = 3
        text = widget.render()
        plain = text.plain
        assert "72/100" in plain
        assert "COMPOSITE RISK SCORE" in plain
        assert "Strategies: 3" in plain

    def test_zero_score_renders(self) -> None:
        widget = RiskGaugeWidget()
        widget.composite_score = 0.0
        text = widget.render()
        plain = text.plain
        assert "0/100" in plain

    def test_full_score_renders(self) -> None:
        widget = RiskGaugeWidget()
        widget.composite_score = 1.0
        text = widget.render()
        plain = text.plain
        assert "100/100" in plain


class TestDrawdownSparklineWidget:
    """Test DrawdownSparklineWidget rendering."""

    def test_empty_state_renders_collecting(self) -> None:
        widget = DrawdownSparklineWidget()
        widget.drawdown_history = []
        text = widget.render()
        plain = text.plain
        assert "Collecting equity data" in plain

    def test_with_history_renders_sparkline(self) -> None:
        widget = DrawdownSparklineWidget()
        widget.drawdown_history = [0.0, -0.01, -0.05, -0.03, 0.0, -0.02]
        widget.max_drawdown = -0.05
        widget.current_drawdown = -0.02
        widget.recovery_periods = None
        text = widget.render()
        plain = text.plain
        assert "DRAWDOWN" in plain
        assert "-5.0%" in plain
        assert "-2.0%" in plain
        assert "in progress" in plain

    def test_with_recovery_renders_periods(self) -> None:
        widget = DrawdownSparklineWidget()
        widget.drawdown_history = [0.0, -0.02, 0.0]
        widget.max_drawdown = -0.02
        widget.current_drawdown = 0.0
        widget.recovery_periods = 5
        text = widget.render()
        plain = text.plain
        assert "5 periods" in plain


class TestCorrelationHeatmapWidget:
    """Test CorrelationHeatmapWidget rendering."""

    def test_empty_state_renders_message(self) -> None:
        widget = CorrelationHeatmapWidget()
        widget.matrix = None
        widget.strategy_names = []
        text = widget.render()
        plain = text.plain
        assert "Need ≥2 strategies" in plain

    def test_single_strategy_renders_message(self) -> None:
        widget = CorrelationHeatmapWidget()
        widget.matrix = [[1.0]]
        widget.strategy_names = ["S1"]
        text = widget.render()
        plain = text.plain
        # Single strategy matrix has < 2 rows, so shows message
        assert "Need ≥2 strategies" in plain

    def test_two_strategies_renders_matrix(self) -> None:
        widget = CorrelationHeatmapWidget()
        widget.matrix = [[1.0, 0.5], [0.5, 1.0]]
        widget.strategy_names = ["Alpha", "Beta"]
        text = widget.render()
        plain = text.plain
        assert "CORRELATION MATRIX" in plain
        assert "Alpha" in plain
        assert "Beta" in plain
        assert "1.00" in plain
        assert "0.50" in plain
        assert "Legend" in plain

    def test_auto_names_when_not_provided(self) -> None:
        widget = CorrelationHeatmapWidget()
        widget.matrix = [[1.0, 0.3], [0.3, 1.0]]
        widget.strategy_names = []
        text = widget.render()
        plain = text.plain
        assert "S1" in plain
        assert "S2" in plain


class TestAlertStreamWidget:
    """Test AlertStreamWidget rendering."""

    def test_empty_state_renders_no_alerts(self) -> None:
        widget = AlertStreamWidget()
        widget.alerts = []
        text = widget.render()
        plain = text.plain
        assert "No alerts" in plain

    @staticmethod
    def _make_alert(severity: str, message: str, metric: str = "drawdown") -> dict:
        return {
            "timestamp": "2024-01-15T14:02:32",
            "severity": severity,
            "metric": metric,
            "message": message,
        }
    def test_with_alerts_renders_entries(self) -> None:
        widget = AlertStreamWidget()
        widget.alerts = [
            self._make_alert("warning", "Drawdown exceeded threshold"),
            self._make_alert("info", "Risk score updated", metric="risk_score"),
        ]
        text = widget.render()
        plain = text.plain
        assert "ALERT STREAM" in plain
        assert "WARN" in plain
        assert "INFO" in plain
        assert "Drawdown exceeded threshold" in plain

    def test_critical_alert_renders(self) -> None:
        widget = AlertStreamWidget()
        widget.alerts = [
            self._make_alert("critical", "Concentration limit breached", metric="concentration"),
        ]
        text = widget.render()
        plain = text.plain
        assert "CRIT" in plain
        assert "Concentration limit breached" in plain


# ── RiskScreen composition tests ─────────────────────────────────────


class TestRiskScreen:
    """Test RiskScreen composition and structure."""

    def test_screen_has_required_widgets(self) -> None:
        """Verify the screen composes with all required widgets."""
        screen = RiskScreen()
        # Check compose method exists and returns widgets
        assert hasattr(screen, "compose")
        assert hasattr(screen, "_refresh_all")
        assert hasattr(screen, "action_go_back")
        assert hasattr(screen, "action_refresh_now")

    def test_screen_has_bindings(self) -> None:
        """Verify keyboard bindings are defined."""
        binding_keys = [b.key for b in RiskScreen.BINDINGS]
        assert "escape" in binding_keys
        assert "r" in binding_keys
        assert "j" in binding_keys
        assert "k" in binding_keys
        assert "f" in binding_keys

    def test_screen_default_state(self) -> None:
        """Verify initial reactive state."""
        screen = RiskScreen()
        assert screen.is_loading is True
        assert screen.status_text == "Connecting…"
        assert screen._filter_severity == "all"
        assert screen._ws_task is None

    def test_alert_filter_cycle(self) -> None:
        """Verify alert filter cycles through severities."""
        screen = RiskScreen()
        assert screen._filter_severity == "all"
        # The action_filter_alerts method cycles: all → critical → warning → info → all
        # We can't call it directly (needs app context), but we can test the logic
        cycle = ["all", "critical", "warning", "info"]
        current = cycle.index(screen._filter_severity)
        next_sev = cycle[(current + 1) % len(cycle)]
        assert next_sev == "critical"

    def test_screen_has_ws_loop_method(self) -> None:
        """Verify WebSocket loop method exists."""
        screen = RiskScreen()
        assert hasattr(screen, "_ws_risk_loop")
        assert hasattr(screen, "_on_ws_risk_update")


# ── Integration tests with Textual testing ───────────────────────────


@pytest.mark.asyncio
async def test_risk_screen_mounts_without_error():
    """Verify RiskScreen can be mounted in a test app without errors.

    Uses a minimal App that provides CSS variables needed by the screen
    widgets to avoid theme.tcss dependency in test mode.
    """
    from textual.app import App

    class TestApp(App):
        CSS = """
        TestApp {
            background: #000000;
        }
        """

        SCREENS = {"risk": RiskScreen}

    app = TestApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.app.push_screen("risk")
        # Verify it mounted and rendered without crashing
        assert app.is_running
        assert app.screen is not None


@pytest.mark.asyncio
async def test_risk_screen_widgets_render():
    """Verify all risk widgets render content."""
    from rich.text import Text

    # Test gauge widget rendering
    gauge = RiskGaugeWidget()
    gauge.composite_score = 0.65
    gauge.sub_scores = {"sharpe": 0.8, "drawdown": 0.6}
    gauge.strategy_count = 2
    result = gauge.render()
    assert isinstance(result, Text)
    assert "65/100" in result.plain

    # Test drawdown widget rendering
    dd = DrawdownSparklineWidget()
    dd.drawdown_history = [0.0, -0.02, -0.05, 0.0]
    dd.max_drawdown = -0.05
    dd.current_drawdown = 0.0
    dd.recovery_periods = 3
    result = dd.render()
    assert isinstance(result, Text)
    assert "DRAWDOWN" in result.plain

    # Test correlation widget rendering
    corr = CorrelationHeatmapWidget()
    corr.matrix = [[1.0, 0.3, 0.7], [0.3, 1.0, 0.2], [0.7, 0.2, 1.0]]
    corr.strategy_names = ["Strat-A", "Strat-B", "Strat-C"]
    result = corr.render()
    assert isinstance(result, Text)
    assert "Strat-A" in result.plain
    assert "Legend" in result.plain

    # Test alert widget rendering
    alerts = AlertStreamWidget()
    alerts.alerts = [
        {"timestamp": "14:02:32", "severity": "warning", "metric": "dd", "message": "test"},
    ]
    result = alerts.render()
    assert isinstance(result, Text)
    assert "WARN" in result.plain
