"""Tests for the Strategy Research TUI screen.

Covers: strategy list, search/filter, results table, comparison panel,
evaluation run, benchmark init, sort cycling, multi-select, keyboard navigation.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from siglab.tui.api_client import TuiApiClient
from siglab.tui.screens.strategy import (
    ComparisonPanelWidget,
    ResultsTableWidget,
    StrategyListWidget,
    StrategyScreen,
    _format_drawdown,
    _format_return,
    _format_score,
    _format_sharpe,
    _format_status,
    _truncate,
)


# ── Helper Fixtures ──────────────────────────────────────────────────


def _make_strategy_rows(n: int = 5) -> list[dict]:
    """Generate fake ancestry/experiment rows for testing."""
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


def _make_result(spec_hash: str = "abc0000000000000") -> dict:
    """Generate a single evaluation result for testing."""
    return {
        "spec_hash": spec_hash,
        "family": "perp_pair_trade_unlevered",
        "track": "trend_signals",
        "aggregate_score": 0.75,
        "validation_total_return": 12.5,
        "sharpe": 1.65,
        "max_drawdown": -8.3,
        "passed": True,
        "equity_curve": [100 + i * 1.5 for i in range(30)],
    }


# ══════════════════════════════════════════════════════════════════════
# Formatting Helper Tests
# ══════════════════════════════════════════════════════════════════════


class TestFormatHelpers:
    """Test formatting helper functions."""

    def test_format_score_high(self) -> None:
        text = _format_score(0.85)
        assert "0.850" in text.plain
        # Style is applied to the whole Text object
        assert text.style == "#4ade80"

    def test_format_score_medium(self) -> None:
        text = _format_score(0.55)
        assert "0.550" in text.plain
        assert text.style == "#f0b456"

    def test_format_score_low(self) -> None:
        text = _format_score(0.25)
        assert "0.250" in text.plain
        assert text.style == "#f87171"

    def test_format_score_none(self) -> None:
        text = _format_score(None)
        assert "─" in text.plain

    def test_format_score_nan(self) -> None:
        text = _format_score(float("nan"))
        assert "NaN" in text.plain

    def test_format_return_positive(self) -> None:
        text = _format_return(12.5)
        assert "+12.50%" in text.plain
        assert text.style == "#4ade80"

    def test_format_return_negative(self) -> None:
        text = _format_return(-8.3)
        assert "-8.30%" in text.plain
        assert text.style == "#f87171"

    def test_format_return_zero(self) -> None:
        text = _format_return(0.0)
        assert "0.00%" in text.plain
        assert text.style == "#7d9483"

    def test_format_return_none(self) -> None:
        text = _format_return(None)
        assert "─" in text.plain

    def test_format_sharpe_high(self) -> None:
        text = _format_sharpe(1.8)
        assert "1.80" in text.plain
        assert text.style == "#4ade80"

    def test_format_sharpe_medium(self) -> None:
        text = _format_sharpe(0.7)
        assert "0.70" in text.plain
        assert text.style == "#f0b456"

    def test_format_sharpe_low(self) -> None:
        text = _format_sharpe(0.3)
        assert "0.30" in text.plain
        assert text.style == "#f87171"

    def test_format_sharpe_none(self) -> None:
        text = _format_sharpe(None)
        assert "─" in text.plain

    def test_format_drawdown_moderate(self) -> None:
        text = _format_drawdown(-5.0)
        assert "5.0%" in text.plain
        assert text.style == "#7d9483"

    def test_format_drawdown_high(self) -> None:
        text = _format_drawdown(-25.0)
        assert "25.0%" in text.plain
        assert text.style == "#f87171"

    def test_format_drawdown_none(self) -> None:
        text = _format_drawdown(None)
        assert "─" in text.plain

    def test_format_status_passed(self) -> None:
        text = _format_status(True)
        assert "●" in text.plain
        assert text.style == "#4ade80"

    def test_format_status_failed(self) -> None:
        text = _format_status(False)
        assert "○" in text.plain
        assert text.style == "#f87171"

    def test_format_status_none(self) -> None:
        text = _format_status(None)
        assert "pending" in text.plain
        assert text.style == "#7d9483"

    def test_truncate_short(self) -> None:
        assert _truncate("hello", 10) == "hello"

    def test_truncate_long(self) -> None:
        result = _truncate("hello world long text", 10)
        assert len(result) == 10
        assert result.endswith("…")

    def test_truncate_exact(self) -> None:
        assert _truncate("hello", 5) == "hello"


# ══════════════════════════════════════════════════════════════════════
# StrategyListWidget Tests
# ══════════════════════════════════════════════════════════════════════


class TestStrategyListWidget:
    """Test the strategy list widget."""

    def test_init_defaults(self) -> None:
        widget = StrategyListWidget()
        assert widget.strategies == []
        assert widget.selected_index == 0
        assert widget._filter_text == ""
        assert widget._family_filter == "ALL"
        assert widget._status_filter == "ALL"

    def test_set_strategies(self) -> None:
        widget = StrategyListWidget()
        rows = _make_strategy_rows(3)
        widget.set_strategies(rows)
        assert len(widget.strategies) == 3
        assert len(widget._all_strategies) == 3

    def test_filter_by_text(self) -> None:
        widget = StrategyListWidget()
        rows = _make_strategy_rows(5)
        widget.set_strategies(rows)
        # Use a unique suffix to match only one entry
        widget.set_filter("0000000000003")
        assert len(widget.strategies) == 1
        assert widget.strategies[0]["spec_hash"] == "abc0000000000003"

    def test_filter_by_family(self) -> None:
        widget = StrategyListWidget()
        rows = _make_strategy_rows(5)
        widget.set_strategies(rows)
        widget.set_family_filter("MOM")
        # basket_momentum contains MOM
        assert all("MOM" in str(s.get("family", "")).upper() or "momentum" in s.get("family", "").lower() for s in widget.strategies)

    def test_filter_by_status_passed(self) -> None:
        widget = StrategyListWidget()
        rows = _make_strategy_rows(5)
        widget.set_strategies(rows)
        widget.set_status_filter("PASSED")
        assert all(s.get("passed") is True for s in widget.strategies)

    def test_filter_by_status_failed(self) -> None:
        widget = StrategyListWidget()
        rows = _make_strategy_rows(5)
        widget.set_strategies(rows)
        widget.set_status_filter("FAILED")
        assert all(s.get("passed") is False for s in widget.strategies)

    def test_filter_combined(self) -> None:
        widget = StrategyListWidget()
        rows = _make_strategy_rows(5)
        widget.set_strategies(rows)
        widget.set_family_filter("PAIR")
        widget.set_status_filter("PASSED")
        # Only rows matching both filters
        for s in widget.strategies:
            assert "PAIR" in str(s.get("family", "")).upper() or "pair" in s.get("family", "").lower()
            assert s.get("passed") is True

    def test_move_up_down(self) -> None:
        widget = StrategyListWidget()
        rows = _make_strategy_rows(5)
        widget.set_strategies(rows)
        assert widget.selected_index == 0
        widget.action_move_down()
        assert widget.selected_index == 1
        widget.action_move_down()
        assert widget.selected_index == 2
        widget.action_move_up()
        assert widget.selected_index == 1

    def test_move_up_at_top(self) -> None:
        widget = StrategyListWidget()
        rows = _make_strategy_rows(3)
        widget.set_strategies(rows)
        widget.action_move_up()
        assert widget.selected_index == 0

    def test_move_down_at_bottom(self) -> None:
        widget = StrategyListWidget()
        rows = _make_strategy_rows(3)
        widget.set_strategies(rows)
        widget.selected_index = 2
        widget.action_move_down()
        assert widget.selected_index == 2

    def test_toggle_select(self) -> None:
        widget = StrategyListWidget()
        rows = _make_strategy_rows(3)
        widget.set_strategies(rows)
        assert len(widget.get_selected_hashes()) == 0
        widget.toggle_select()
        assert len(widget.get_selected_hashes()) == 1
        widget.toggle_select()  # deselect
        assert len(widget.get_selected_hashes()) == 0

    def test_toggle_select_max(self) -> None:
        widget = StrategyListWidget()
        rows = _make_strategy_rows(6)
        widget.set_strategies(rows)
        for i in range(5):
            widget.selected_index = i
            widget.toggle_select()
        # Should cap at MAX_COMPARE (4)
        assert len(widget.get_selected_hashes()) <= 4

    def test_get_current_hash(self) -> None:
        widget = StrategyListWidget()
        rows = _make_strategy_rows(3)
        widget.set_strategies(rows)
        assert widget.get_current_hash() == "abc0000000000000"

    def test_get_current_hash_empty(self) -> None:
        widget = StrategyListWidget()
        assert widget.get_current_hash() is None

    def test_render_empty(self) -> None:
        widget = StrategyListWidget()
        text = widget.render()
        assert "No strategies" in text.plain

    def test_render_with_data(self) -> None:
        widget = StrategyListWidget()
        rows = _make_strategy_rows(3)
        widget.set_strategies(rows)
        text = widget.render()
        assert "abc00000000000" in text.plain or "abc0" in text.plain

    def test_filter_clamps_index(self) -> None:
        widget = StrategyListWidget()
        rows = _make_strategy_rows(5)
        widget.set_strategies(rows)
        widget.selected_index = 4
        widget.set_filter("0000000000000")  # Only 1 result (abc0000000000000)
        assert widget.selected_index <= 0


# ══════════════════════════════════════════════════════════════════════
# ResultsTableWidget Tests
# ══════════════════════════════════════════════════════════════════════


class TestResultsTableWidget:
    """Test the results table widget."""

    def test_init_defaults(self) -> None:
        widget = ResultsTableWidget()
        assert widget.results == []
        assert widget.sort_column == "aggregate_score"
        assert widget.sort_ascending is False

    def test_set_results(self) -> None:
        widget = ResultsTableWidget()
        rows = _make_strategy_rows(3)
        widget.set_results(rows)
        assert len(widget.results) == 3

    def test_cycle_sort(self) -> None:
        widget = ResultsTableWidget()
        assert widget.sort_column == "aggregate_score"
        widget.cycle_sort()
        assert widget.sort_column == "validation_total_return"
        widget.cycle_sort()
        assert widget.sort_column == "sharpe"

    def test_toggle_sort_direction(self) -> None:
        widget = ResultsTableWidget()
        assert widget.sort_ascending is False
        widget.toggle_sort_direction()
        assert widget.sort_ascending is True

    def test_sorted_results_descending(self) -> None:
        widget = ResultsTableWidget()
        rows = _make_strategy_rows(5)
        widget.set_results(rows)
        sorted_rows = widget._sorted_results()
        scores = [r.get("aggregate_score", 0) for r in sorted_rows if r.get("aggregate_score") is not None]
        # Should be descending (default)
        for i in range(len(scores) - 1):
            assert scores[i] >= scores[i + 1]

    def test_sorted_results_ascending(self) -> None:
        widget = ResultsTableWidget()
        rows = _make_strategy_rows(5)
        widget.set_results(rows)
        widget.sort_ascending = True
        sorted_rows = widget._sorted_results()
        scores = [r.get("aggregate_score", 0) for r in sorted_rows if r.get("aggregate_score") is not None]
        for i in range(len(scores) - 1):
            assert scores[i] <= scores[i + 1]

    def test_render_empty(self) -> None:
        widget = ResultsTableWidget()
        text = widget.render()
        assert "No results" in text.plain

    def test_render_with_data(self) -> None:
        widget = ResultsTableWidget()
        rows = _make_strategy_rows(3)
        widget.set_results(rows)
        text = widget.render()
        assert "EVALUATION RESULTS" in text.plain
        assert "SCORE" in text.plain
        assert "PnL" in text.plain
        assert "SHARPE" in text.plain

    def test_render_shows_all_columns(self) -> None:
        widget = ResultsTableWidget()
        rows = _make_strategy_rows(1)
        widget.set_results(rows)
        text = widget.render()
        for col in ["NAME", "FAMILY", "SCORE", "PnL", "SHARPE", "MAXDD", "STATUS"]:
            assert col in text.plain


# ══════════════════════════════════════════════════════════════════════
# ComparisonPanelWidget Tests
# ══════════════════════════════════════════════════════════════════════


class TestComparisonPanelWidget:
    """Test the comparison panel widget."""

    def test_init_defaults(self) -> None:
        widget = ComparisonPanelWidget()
        assert widget.strategies == []

    def test_set_strategies(self) -> None:
        widget = ComparisonPanelWidget()
        rows = _make_strategy_rows(3)
        widget.set_strategies(rows)
        assert len(widget.strategies) == 3

    def test_render_insufficient(self) -> None:
        widget = ComparisonPanelWidget()
        widget.set_strategies([_make_result()])
        text = widget.render()
        assert "Select 2+" in text.plain

    def test_render_two_strategies(self) -> None:
        widget = ComparisonPanelWidget()
        rows = _make_strategy_rows(2)
        widget.set_strategies(rows)
        text = widget.render()
        assert "STRATEGY COMPARISON" in text.plain
        assert "Score" in text.plain
        assert "PnL%" in text.plain
        assert "Sharpe" in text.plain
        assert "MaxDD" in text.plain
        assert "Family" in text.plain
        assert "EQUITY CURVES" in text.plain

    def test_render_shows_delta(self) -> None:
        widget = ComparisonPanelWidget()
        rows = _make_strategy_rows(3)
        widget.set_strategies(rows)
        text = widget.render()
        # Delta column should be present
        assert "DELTA" in text.plain

    def test_render_four_strategies(self) -> None:
        widget = ComparisonPanelWidget()
        rows = _make_strategy_rows(4)
        widget.set_strategies(rows)
        text = widget.render()
        assert "STRATEGY COMPARISON" in text.plain


# ══════════════════════════════════════════════════════════════════════
# StrategyScreen Tests
# ══════════════════════════════════════════════════════════════════════


class TestStrategyScreen:
    """Test the strategy screen."""

    def test_screen_class_exists(self) -> None:
        assert StrategyScreen is not None

    def test_screen_has_bindings(self) -> None:
        binding_keys = [b.key for b in StrategyScreen.BINDINGS]
        assert "escape" in binding_keys
        assert "r" in binding_keys
        assert "e" in binding_keys
        assert "c" in binding_keys
        assert "space" in binding_keys
        assert "s" in binding_keys
        assert "/" in binding_keys
        assert "j" in binding_keys
        assert "k" in binding_keys
        assert "i" in binding_keys

    def test_screen_init_default_deck(self) -> None:
        screen = StrategyScreen()
        assert screen._deck == "trend_signals_external"

    def test_screen_init_custom_deck(self) -> None:
        screen = StrategyScreen(deck="custom_deck")
        assert screen._deck == "custom_deck"

    def test_screen_has_compose(self) -> None:
        assert hasattr(StrategyScreen, "compose")

    def test_screen_has_action_methods(self) -> None:
        assert hasattr(StrategyScreen, "action_go_back")
        assert hasattr(StrategyScreen, "action_refresh")
        assert hasattr(StrategyScreen, "action_run_eval")
        assert hasattr(StrategyScreen, "action_toggle_compare")
        assert hasattr(StrategyScreen, "action_cycle_sort")
        assert hasattr(StrategyScreen, "action_toggle_select")
        assert hasattr(StrategyScreen, "action_focus_search")
        assert hasattr(StrategyScreen, "action_init_deck")

    def test_screen_reactive_state(self) -> None:
        assert hasattr(StrategyScreen, "is_evaluating")
        assert hasattr(StrategyScreen, "eval_status")
        assert hasattr(StrategyScreen, "compare_mode")
        assert hasattr(StrategyScreen, "strategy_count")

    def test_screen_default_css(self) -> None:
        assert "StrategyScreen" in StrategyScreen.DEFAULT_CSS


# ══════════════════════════════════════════════════════════════════════
# API Client Strategy Methods Tests
# ══════════════════════════════════════════════════════════════════════


class TestTuiApiClientStrategies:
    """Test API client strategy methods."""

    @pytest.mark.asyncio
    async def test_get_strategies(self) -> None:
        client = TuiApiClient()
        mock_response = MagicMock()
        mock_response.json.return_value = {"strategies": [], "count": 0}
        mock_response.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.get.return_value = mock_response
        client._client = mock_http

        result = await client.get_strategies()
        assert isinstance(result, dict)
        assert result["count"] == 0
        await client.close()

    @pytest.mark.asyncio
    async def test_get_strategy_detail(self) -> None:
        client = TuiApiClient()
        mock_response = MagicMock()
        mock_response.json.return_value = {"spec_hash": "abc123", "summary": {}}
        mock_response.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.get.return_value = mock_response
        client._client = mock_http

        result = await client.get_strategy_detail("abc123")
        assert result["spec_hash"] == "abc123"
        await client.close()

    @pytest.mark.asyncio
    async def test_get_benchmark_status(self) -> None:
        client = TuiApiClient()
        mock_response = MagicMock()
        mock_response.json.return_value = {"state": {}, "recent_results": []}
        mock_response.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.get.return_value = mock_response
        client._client = mock_http

        result = await client.get_benchmark_status("trend_signals_external")
        assert isinstance(result, dict)
        await client.close()

    @pytest.mark.asyncio
    async def test_get_benchmark_results(self) -> None:
        client = TuiApiClient()
        mock_response = MagicMock()
        mock_response.json.return_value = {"results": []}
        mock_response.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.get.return_value = mock_response
        client._client = mock_http

        result = await client.get_benchmark_results()
        assert isinstance(result, dict)
        await client.close()


# ══════════════════════════════════════════════════════════════════════
# App Registration Tests
# ══════════════════════════════════════════════════════════════════════


class TestStrategyRegistration:
    """Test that the strategy screen is properly registered in the app."""

    def test_strategy_screen_in_app_screens(self) -> None:
        from siglab.tui.app import SigLabTUI
        assert "strategy" in SigLabTUI.SCREENS

    def test_strategy_screen_binding_key_4(self) -> None:
        from siglab.tui.app import SigLabTUI
        binding_keys = [b.key for b in SigLabTUI.BINDINGS]
        assert "4" in binding_keys

    def test_strategy_action_switch_exists(self) -> None:
        from siglab.tui.app import SigLabTUI
        assert hasattr(SigLabTUI, "action_switch_to_strategy")

    def test_strategy_css_in_path(self) -> None:
        from siglab.tui.app import SigLabTUI
        assert "styles/strategy.tcss" in SigLabTUI.CSS_PATH

    def test_strategy_screen_exported(self) -> None:
        from siglab.tui.screens import StrategyScreen as Exported
        assert Exported is StrategyScreen
