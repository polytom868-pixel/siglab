"""Tests for the Telemetry and Run Browser TUI screen.

Covers: run list, telemetry display, comparison, filters, widgets,
provider metrics, tool usage, service health, formatting helpers.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from siglab.tui.api_client import TuiApiClient
from siglab.tui.formatting import (
    confidence_color,
    format_count,
    format_date,
    format_latency,
    format_score,
    format_status,
    truncate,
)
from siglab.tui.screens.telemetry import (
    ProviderMetricsWidget,
    RunComparisonWidget,
    RunDetailWidget,
    ServiceHealthWidget,
    TelemetryRunListWidget,
    TelemetryScreen,
    ToolUsageWidget,
    _classification_color,
)


# ── Helper Fixtures ──────────────────────────────────────────────────


def _make_run_rows(n: int = 5) -> list[dict]:
    """Generate fake experiment run rows for testing."""
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


def _make_telemetry_data() -> dict:
    """Generate fake telemetry report data for testing."""
    return {
        "trace_count": 73,
        "stage_counts": {
            "planner": 29,
            "reflector": 17,
            "writer": 27,
        },
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


def _make_ops_board_data() -> dict:
    """Generate fake ops-board data for testing."""
    return {
        "service_health": {
            "dashboard": {"status": "running", "port": 3100},
            "siglab_db": {"status": "ok"},
            "sodex_api": {"status": "external"},
        },
        "artifact_status": {
            "telemetry": {"status": "present", "freshness": "fresh"},
            "demo_manifest": {"status": "present", "freshness": "stale"},
            "market_report": {"status": "missing", "freshness": "expired"},
        },
    }


# ══════════════════════════════════════════════════════════════════════
# Formatting Helper Tests
# ══════════════════════════════════════════════════════════════════════


class TestFormatHelpers:
    """Test formatting helper functions."""

    def testformat_score_high(self) -> None:
        text = format_score(0.85)
        assert "0.850" in text.plain
        assert text.style == "#4ade80"

    def testformat_score_medium(self) -> None:
        text = format_score(0.55)
        assert "0.550" in text.plain
        assert text.style == "#f0b456"

    def testformat_score_low(self) -> None:
        text = format_score(0.25)
        assert "0.250" in text.plain
        assert text.style == "#f87171"

    def testformat_score_none(self) -> None:
        text = format_score(None)
        assert "\u2500" in text.plain

    def testformat_score_nan(self) -> None:
        text = format_score(float("nan"))
        assert "NaN" in text.plain

    def testformat_latency_fast(self) -> None:
        text = format_latency(50.0)
        assert "50ms" in text.plain
        assert text.style == "#4ade80"

    def testformat_latency_moderate(self) -> None:
        text = format_latency(200.0)
        assert "200ms" in text.plain
        assert text.style == "#f0b456"

    def testformat_latency_slow(self) -> None:
        text = format_latency(800.0)
        assert "800ms" in text.plain
        assert text.style == "#f87171"

    def testformat_latency_none(self) -> None:
        text = format_latency(None)
        assert "\u2500" in text.plain

    def testformat_status_passed(self) -> None:
        text = format_status(True)
        assert "\u25cf" in text.plain
        assert text.style == "#4ade80"

    def testformat_status_failed(self) -> None:
        text = format_status(False)
        assert "\u25cb" in text.plain
        assert text.style == "#f87171"

    def testformat_status_deployed(self) -> None:
        text = format_status(True, deployed=True)
        assert "\u25b2" in text.plain
        assert text.style == "#60a5fa"

    def testformat_status_none(self) -> None:
        text = format_status(None)
        assert "\u00b7" in text.plain
        assert text.style == "#7d9483"

    def testformat_date_valid(self) -> None:
        result = format_date("2026-06-01T10:00:00+00:00")
        assert "06-01" in result

    def testformat_date_none(self) -> None:
        result = format_date(None)
        assert result == "\u2500\u2500"

    def testformat_date_empty(self) -> None:
        result = format_date("")
        assert result == "\u2500\u2500"

    def testformat_date_short(self) -> None:
        result = format_date("2026-06")
        assert "2026" in result

    def testformat_count_small(self) -> None:
        assert format_count(42) == "42"

    def testformat_count_thousands(self) -> None:
        assert format_count(3200) == "3.2k"

    def testformat_count_millions(self) -> None:
        assert format_count(1500000) == "1.5M"

    def testformat_count_none(self) -> None:
        assert format_count(None) == "\u2500"

    def testformat_count_float(self) -> None:
        assert format_count(50000.0) == "50.0k"

    def testtruncate_short(self) -> None:
        assert truncate("hello", 10) == "hello"

    def testtruncate_long(self) -> None:
        result = truncate("hello world long text", 10)
        assert len(result) == 10
        assert result.endswith("\u2026")

    def testtruncate_exact(self) -> None:
        assert truncate("hello", 5) == "hello"

    def testconfidence_color_good(self) -> None:
        assert confidence_color("good") == "#4ade80"

    def testconfidence_color_medium(self) -> None:
        assert confidence_color("medium") == "#f0b456"

    def testconfidence_color_poor(self) -> None:
        assert confidence_color("poor") == "#f87171"

    def testconfidence_color_unknown(self) -> None:
        assert confidence_color("unknown") == "#7d9483"

    def test_classification_color_high_value(self) -> None:
        assert _classification_color("HIGH_VALUE") == "#4ade80"

    def test_classification_color_medium_value(self) -> None:
        assert _classification_color("MEDIUM_VALUE") == "#60a5fa"

    def test_classification_color_noisy(self) -> None:
        assert _classification_color("NOISY") == "#f87171"


# ══════════════════════════════════════════════════════════════════════
# TelemetryRunListWidget Tests
# ══════════════════════════════════════════════════════════════════════


class TestTelemetryRunListWidget:
    """Test the run list widget."""

    def test_init_defaults(self) -> None:
        widget = TelemetryRunListWidget()
        assert widget.runs == []
        assert widget.selected_index == 0
        assert widget._filter_text == ""
        assert widget._status_filter == "ALL"
        assert widget._track_filter == "ALL"
        assert widget._date_range == "ALL"

    def test_set_runs(self) -> None:
        widget = TelemetryRunListWidget()
        rows = _make_run_rows(3)
        widget.set_runs(rows)
        assert len(widget.runs) == 3
        assert len(widget._all_data) == 3

    def test_filter_by_text(self) -> None:
        widget = TelemetryRunListWidget()
        rows = _make_run_rows(5)
        widget.set_runs(rows)
        widget.set_filter("0000000000003")
        assert len(widget.runs) == 1
        assert widget.runs[0]["spec_hash"] == "abc0000000000003"

    def test_filter_by_status_passed(self) -> None:
        widget = TelemetryRunListWidget()
        rows = _make_run_rows(5)
        widget.set_runs(rows)
        widget.set_status_filter("PASSED")
        assert all(r.get("passed") is True for r in widget.runs)

    def test_filter_by_status_failed(self) -> None:
        widget = TelemetryRunListWidget()
        rows = _make_run_rows(5)
        widget.set_runs(rows)
        widget.set_status_filter("FAILED")
        assert all(r.get("passed") is False for r in widget.runs)

    def test_filter_by_track(self) -> None:
        widget = TelemetryRunListWidget()
        rows = _make_run_rows(6)
        widget.set_runs(rows)
        widget.set_track_filter("TREND_SIGNALS")
        for r in widget.runs:
            assert "trend_signals" in r.get("track", "").lower()

    def test_filter_combined(self) -> None:
        widget = TelemetryRunListWidget()
        rows = _make_run_rows(6)
        widget.set_runs(rows)
        widget.set_track_filter("TREND_SIGNALS")
        widget.set_status_filter("PASSED")
        for r in widget.runs:
            assert "trend_signals" in r.get("track", "").lower()
            assert r.get("passed") is True

    def test_move_up_down(self) -> None:
        widget = TelemetryRunListWidget()
        rows = _make_run_rows(5)
        widget.set_runs(rows)
        assert widget.selected_index == 0
        widget.action_move_down()
        assert widget.selected_index == 1
        widget.action_move_up()
        assert widget.selected_index == 0

    def test_move_up_at_top(self) -> None:
        widget = TelemetryRunListWidget()
        rows = _make_run_rows(3)
        widget.set_runs(rows)
        widget.action_move_up()
        assert widget.selected_index == 0

    def test_move_down_at_bottom(self) -> None:
        widget = TelemetryRunListWidget()
        rows = _make_run_rows(3)
        widget.set_runs(rows)
        widget.selected_index = 2
        widget.action_move_down()
        assert widget.selected_index == 2

    def test_toggle_select(self) -> None:
        widget = TelemetryRunListWidget()
        rows = _make_run_rows(3)
        widget.set_runs(rows)
        assert len(widget.get_selected_hashes()) == 0
        widget.toggle_select()
        assert len(widget.get_selected_hashes()) == 1
        widget.toggle_select()  # deselect
        assert len(widget.get_selected_hashes()) == 0

    def test_toggle_select_max(self) -> None:
        widget = TelemetryRunListWidget()
        rows = _make_run_rows(6)
        widget.set_runs(rows)
        for i in range(5):
            widget.selected_index = i
            widget.toggle_select()
        # Should cap at MAX_COMPARE (4)
        assert len(widget.get_selected_hashes()) <= 4

    def test_get_current_run(self) -> None:
        widget = TelemetryRunListWidget()
        rows = _make_run_rows(3)
        widget.set_runs(rows)
        current = widget.get_current_run()
        assert current is not None
        assert current["spec_hash"] == "abc0000000000000"

    def test_get_current_run_empty(self) -> None:
        widget = TelemetryRunListWidget()
        assert widget.get_current_run() is None

    def test_get_tracks(self) -> None:
        widget = TelemetryRunListWidget()
        rows = _make_run_rows(6)
        widget.set_runs(rows)
        tracks = widget.get_tracks()
        assert "trend_signals" in tracks
        assert "yield_flows" in tracks
        assert "momentum" in tracks

    def test_render_empty(self) -> None:
        widget = TelemetryRunListWidget()
        text = widget.render()
        assert "No items found" in text.plain

    def test_render_with_data(self) -> None:
        widget = TelemetryRunListWidget()
        rows = _make_run_rows(3)
        widget.set_runs(rows)
        text = widget.render()
        assert "abc0" in text.plain

    def test_filter_clamps_index(self) -> None:
        widget = TelemetryRunListWidget()
        rows = _make_run_rows(5)
        widget.set_runs(rows)
        widget.selected_index = 4
        widget.set_filter("0000000000000")  # Only 1 result
        assert widget.selected_index <= 0


# ══════════════════════════════════════════════════════════════════════
# ProviderMetricsWidget Tests
# ══════════════════════════════════════════════════════════════════════


class TestProviderMetricsWidget:
    """Test the provider metrics widget."""

    def test_init_defaults(self) -> None:
        widget = ProviderMetricsWidget()
        assert widget.telemetry_data == {}

    def test_render_empty(self) -> None:
        widget = ProviderMetricsWidget()
        text = widget.render()
        assert "No telemetry data" in text.plain

    def test_render_with_data(self) -> None:
        widget = ProviderMetricsWidget()
        data = _make_telemetry_data()
        widget.telemetry_data = data
        text = widget.render()
        assert "PROVIDER METRICS" in text.plain
        assert "good" in text.plain.lower()
        assert "planner" in text.plain.lower()
        assert "writer" in text.plain.lower()
        assert "deepseek" in text.plain.lower()

    def test_render_shows_stage_distribution(self) -> None:
        widget = ProviderMetricsWidget()
        data = _make_telemetry_data()
        widget.telemetry_data = data
        text = widget.render()
        assert "Stage Distribution" in text.plain
        assert "planner" in text.plain

    def test_render_shows_model_usage(self) -> None:
        widget = ProviderMetricsWidget()
        data = _make_telemetry_data()
        widget.telemetry_data = data
        text = widget.render()
        assert "Model Usage" in text.plain

    def test_render_shows_token_usage(self) -> None:
        widget = ProviderMetricsWidget()
        data = _make_telemetry_data()
        widget.telemetry_data = data
        text = widget.render()
        assert "Token Usage" in text.plain
        assert "50.0k" in text.plain

    def test_render_shows_credit_pressure(self) -> None:
        widget = ProviderMetricsWidget()
        data = _make_telemetry_data()
        widget.telemetry_data = data
        text = widget.render()
        assert "Credit Pressure" in text.plain
        assert "critical" in text.plain.lower()


# ══════════════════════════════════════════════════════════════════════
# ToolUsageWidget Tests
# ══════════════════════════════════════════════════════════════════════


class TestToolUsageWidget:
    """Test the tool usage widget."""

    def test_init_defaults(self) -> None:
        widget = ToolUsageWidget()
        assert widget.telemetry_data == {}

    def test_render_empty(self) -> None:
        widget = ToolUsageWidget()
        text = widget.render()
        assert "No tool data" in text.plain

    def test_render_with_data(self) -> None:
        widget = ToolUsageWidget()
        data = _make_telemetry_data()
        widget.telemetry_data = data
        text = widget.render()
        assert "TOOL USAGE" in text.plain
        assert "open_file" in text.plain
        assert "257" in text.plain

    def test_render_shows_latency(self) -> None:
        widget = ToolUsageWidget()
        data = _make_telemetry_data()
        widget.telemetry_data = data
        text = widget.render()
        assert "p50" in text.plain
        assert "p95" in text.plain

    def test_render_shows_error_rate(self) -> None:
        widget = ToolUsageWidget()
        data = _make_telemetry_data()
        widget.telemetry_data = data
        text = widget.render()
        assert "Errors" in text.plain
        assert "18" in text.plain

    def test_render_sorted_by_count(self) -> None:
        widget = ToolUsageWidget()
        data = _make_telemetry_data()
        widget.telemetry_data = data
        text = widget.render()
        # open_file (82) should appear before think (14)
        plain = text.plain
        assert plain.index("open_file") < plain.index("think")


# ══════════════════════════════════════════════════════════════════════
# RunDetailWidget Tests
# ══════════════════════════════════════════════════════════════════════


class TestRunDetailWidget:
    """Test the run detail widget."""

    def test_init_defaults(self) -> None:
        widget = RunDetailWidget()
        assert widget.run is None

    def test_render_empty(self) -> None:
        widget = RunDetailWidget()
        text = widget.render()
        assert "Select a run" in text.plain

    def test_render_with_run(self) -> None:
        widget = RunDetailWidget()
        run = _make_run_rows(1)[0]
        widget.run = run
        text = widget.render()
        assert "RUN DETAIL" in text.plain
        assert "abc0000000000000" in text.plain
        assert "trend_signals" in text.plain

    def test_render_shows_score(self) -> None:
        widget = RunDetailWidget()
        run = _make_run_rows(1)[0]
        widget.run = run
        text = widget.render()
        assert "Score" in text.plain

    def test_render_shows_status(self) -> None:
        widget = RunDetailWidget()
        run = _make_run_rows(1)[0]
        widget.run = run
        text = widget.render()
        assert "Status" in text.plain


# ══════════════════════════════════════════════════════════════════════
# RunComparisonWidget Tests
# ══════════════════════════════════════════════════════════════════════


class TestRunComparisonWidget:
    """Test the run comparison widget."""

    def test_init_defaults(self) -> None:
        widget = RunComparisonWidget()
        assert widget.runs == []

    def test_set_runs(self) -> None:
        widget = RunComparisonWidget()
        rows = _make_run_rows(3)
        widget.set_runs(rows)
        assert len(widget.runs) == 3

    def test_render_insufficient(self) -> None:
        widget = RunComparisonWidget()
        widget.set_runs([_make_run_rows(1)[0]])
        text = widget.render()
        assert "Select 2+" in text.plain

    def test_render_two_runs(self) -> None:
        widget = RunComparisonWidget()
        rows = _make_run_rows(2)
        widget.set_runs(rows)
        text = widget.render()
        assert "COMPARISON" in text.plain
        assert "Score" in text.plain
        assert "Track" in text.plain
        assert "DELTA" in text.plain

    def test_render_shows_delta(self) -> None:
        widget = RunComparisonWidget()
        rows = _make_run_rows(3)
        widget.set_runs(rows)
        text = widget.render()
        assert "DELTA" in text.plain

    def test_render_four_runs(self) -> None:
        widget = RunComparisonWidget()
        rows = _make_run_rows(4)
        widget.set_runs(rows)
        text = widget.render()
        assert "COMPARISON" in text.plain


# ══════════════════════════════════════════════════════════════════════
# ServiceHealthWidget Tests
# ══════════════════════════════════════════════════════════════════════


class TestServiceHealthWidget:
    """Test the service health widget."""

    def test_init_defaults(self) -> None:
        widget = ServiceHealthWidget()
        assert widget.service_health == {}
        assert widget.artifact_status == {}

    def test_render_empty(self) -> None:
        widget = ServiceHealthWidget()
        text = widget.render()
        assert "No health data" in text.plain

    def test_render_with_health(self) -> None:
        widget = ServiceHealthWidget()
        data = _make_ops_board_data()
        widget.service_health = data["service_health"]
        widget.artifact_status = data["artifact_status"]
        text = widget.render()
        assert "SERVICE HEALTH" in text.plain
        assert "dashboard" in text.plain
        assert "running" in text.plain

    def test_render_shows_artifacts(self) -> None:
        widget = ServiceHealthWidget()
        data = _make_ops_board_data()
        widget.service_health = data["service_health"]
        widget.artifact_status = data["artifact_status"]
        text = widget.render()
        assert "ARTIFACTS" in text.plain
        assert "telemetry" in text.plain

    def test_render_freshness_colors(self) -> None:
        widget = ServiceHealthWidget()
        data = _make_ops_board_data()
        widget.service_health = data["service_health"]
        widget.artifact_status = data["artifact_status"]
        text = widget.render()
        # Should have fresh/stale/expired indicators
        assert "fresh" in text.plain
        assert "stale" in text.plain


# ══════════════════════════════════════════════════════════════════════
# TelemetryScreen Tests
# ══════════════════════════════════════════════════════════════════════


class TestTelemetryScreen:
    """Test the telemetry screen."""

    def test_screen_class_exists(self) -> None:
        assert TelemetryScreen is not None

    def test_screen_has_bindings(self) -> None:
        binding_keys = [b.key for b in TelemetryScreen.BINDINGS]
        assert "escape" in binding_keys
        assert "r" in binding_keys
        assert "space" in binding_keys
        assert "c" in binding_keys
        assert "s" in binding_keys
        assert "/" in binding_keys
        assert "d" in binding_keys
        assert "f" in binding_keys
        assert "t" in binding_keys
        assert "j" in binding_keys
        assert "k" in binding_keys
        assert "v" in binding_keys

    def test_screen_has_compose(self) -> None:
        assert hasattr(TelemetryScreen, "compose")

    def test_screen_has_action_methods(self) -> None:
        assert hasattr(TelemetryScreen, "action_go_back")
        assert hasattr(TelemetryScreen, "action_refresh_now")
        assert hasattr(TelemetryScreen, "action_toggle_select")
        assert hasattr(TelemetryScreen, "action_toggle_compare")
        assert hasattr(TelemetryScreen, "action_cycle_sort")
        assert hasattr(TelemetryScreen, "action_focus_search")
        assert hasattr(TelemetryScreen, "action_cycle_date_range")
        assert hasattr(TelemetryScreen, "action_cycle_status_filter")
        assert hasattr(TelemetryScreen, "action_cycle_track_filter")
        assert hasattr(TelemetryScreen, "action_toggle_detail_view")

    def test_screen_reactive_state(self) -> None:
        assert hasattr(TelemetryScreen, "status_text")
        assert hasattr(TelemetryScreen, "is_loading")
        assert hasattr(TelemetryScreen, "compare_mode")
        assert hasattr(TelemetryScreen, "run_count")
        assert hasattr(TelemetryScreen, "_date_range")
        assert hasattr(TelemetryScreen, "_status_filter")
        assert hasattr(TelemetryScreen, "_track_filter")
        assert hasattr(TelemetryScreen, "_detail_view")

    def test_screen_default_css(self) -> None:
        assert "TelemetryScreen" in TelemetryScreen.DEFAULT_CSS

    def test_screen_date_range_filters(self) -> None:
        from siglab.tui.screens.telemetry import DATE_RANGE_FILTERS
        assert "ALL" in DATE_RANGE_FILTERS
        assert "7d" in DATE_RANGE_FILTERS
        assert "30d" in DATE_RANGE_FILTERS
        assert "TODAY" in DATE_RANGE_FILTERS

    def test_screen_status_filters(self) -> None:
        from siglab.tui.screens.telemetry import STATUS_FILTERS
        assert "ALL" in STATUS_FILTERS
        assert "PASSED" in STATUS_FILTERS
        assert "FAILED" in STATUS_FILTERS


# ══════════════════════════════════════════════════════════════════════
# API Client Telemetry Methods Tests
# ══════════════════════════════════════════════════════════════════════


class TestTuiApiClientTelemetry:
    """Test API client telemetry methods."""

    @pytest.mark.asyncio
    async def test_get_ops_board(self) -> None:
        client = TuiApiClient()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "artifact_status": {},
            "summary": {},
            "service_health": {},
        }
        mock_response.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.get.return_value = mock_response
        client._client = mock_http

        result = await client.get_ops_board()
        assert isinstance(result, dict)
        await client.close()

    @pytest.mark.asyncio
    async def test_get_skill_report(self) -> None:
        client = TuiApiClient()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "skills": [],
            "total_skills": 0,
            "total_invocations": 0,
        }
        mock_response.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.get.return_value = mock_response
        client._client = mock_http

        result = await client.get_skill_report()
        assert isinstance(result, dict)
        assert result["total_skills"] == 0
        await client.close()

    @pytest.mark.asyncio
    async def test_get_telemetry_report(self) -> None:
        client = TuiApiClient()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "artifact_status": {},
            "summary": {},
            "service_health": {},
        }
        mock_response.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.get.return_value = mock_response
        client._client = mock_http

        result = await client.get_telemetry_report()
        assert isinstance(result, dict)
        await client.close()


# ══════════════════════════════════════════════════════════════════════
# App Registration Tests
# ══════════════════════════════════════════════════════════════════════


class TestTelemetryRegistration:
    """Test that the telemetry screen is properly registered in the app."""

    def test_telemetry_screen_in_app_screens(self) -> None:
        from siglab.tui.app import SigLabTUI
        assert "telemetry" in SigLabTUI.SCREENS

    def test_telemetry_screen_binding_key_5(self) -> None:
        from siglab.tui.app import SigLabTUI
        binding_keys = [b.key for b in SigLabTUI.BINDINGS]
        assert "5" in binding_keys

    def test_telemetry_action_switch_exists(self) -> None:
        from siglab.tui.app import SigLabTUI
        assert hasattr(SigLabTUI, "action_switch_to_telemetry")

    def test_telemetry_css_in_path(self) -> None:
        """Telemetry styles are consolidated in app.tcss."""
        from pathlib import Path
        tcss_path = Path(__file__).resolve().parents[1] / "siglab" / "tui" / "styles" / "app.tcss"
        content = tcss_path.read_text()
        assert "TelemetryScreen" in content
        assert "#telemetry-main" in content

    def test_telemetry_screen_exported(self) -> None:
        from siglab.tui.screens import TelemetryScreen as Exported
        assert Exported is TelemetryScreen

    def test_no_placeholder_for_telemetry(self) -> None:
        """Ensure telemetry screen is NOT a PlaceholderScreen."""
        from siglab.tui.app import SigLabTUI, PlaceholderScreen
        screen_factory = SigLabTUI.SCREENS.get("telemetry")
        assert screen_factory is not None
        # The factory should return a TelemetryScreen, not a PlaceholderScreen
        if callable(screen_factory):
            instance = screen_factory()
            assert not isinstance(instance, PlaceholderScreen)
