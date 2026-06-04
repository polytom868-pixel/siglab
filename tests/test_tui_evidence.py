"""Tests for the SigLab Evidence Graph and Demo Flow TUI screen.

Covers: screen composition, graph widget rendering, demo flow widget,
filter functionality, API integration, and keyboard shortcuts.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from siglab.tui.screens.evidence import (
    DEMO_STEPS,
    DemoFlowWidget,
    EdgeDetailWidget,
    EvidenceGraphWidget,
    EvidenceScreen,
    _format_confidence,
    _kind_icon,
    _kind_style,
)


# ── Constants Tests ──────────────────────────────────────────────────


class TestDemoSteps:
    """Test demo steps configuration."""

    def test_demo_steps_has_entries(self) -> None:
        assert len(DEMO_STEPS) >= 7

    def test_demo_steps_have_required_fields(self) -> None:
        for step in DEMO_STEPS:
            assert "step" in step
            assert "title" in step
            assert "command" in step
            assert "description" in step
            assert "expected" in step

    def test_demo_steps_are_sequential(self) -> None:
        numbers = [s["step"] for s in DEMO_STEPS]
        assert numbers == list(range(1, len(DEMO_STEPS) + 1))

    def test_demo_step_commands_are_strings(self) -> None:
        for step in DEMO_STEPS:
            assert isinstance(step["command"], str)
            assert len(step["command"]) > 0


# ── Helper Function Tests ────────────────────────────────────────────


class TestHelpers:
    """Test helper functions."""

    def test_kind_icon_source(self) -> None:
        assert _kind_icon("source") == "📡"

    def test_kind_icon_entity(self) -> None:
        assert _kind_icon("entity") == "🔗"

    def test_kind_icon_unknown(self) -> None:
        assert _kind_icon("unknown") == "●"

    def test_kind_style_source(self) -> None:
        assert _kind_style("source") == "#60a5fa"

    def test_kind_style_entity(self) -> None:
        assert _kind_style("entity") == "#4ade80"

    def test_format_confidence_high(self) -> None:
        result = _format_confidence(0.95)
        assert "95%" in str(result.plain)

    def test_format_confidence_medium(self) -> None:
        result = _format_confidence(0.6)
        assert "60%" in str(result.plain)

    def test_format_confidence_low(self) -> None:
        result = _format_confidence(0.3)
        assert "30%" in str(result.plain)

    def test_format_confidence_none(self) -> None:
        result = _format_confidence(None)
        assert "—" in str(result.plain)


# ── Evidence Graph Widget Tests ──────────────────────────────────────


class TestEvidenceGraphWidget:
    """Test EvidenceGraphWidget rendering."""

    def test_widget_init(self) -> None:
        widget = EvidenceGraphWidget()
        assert widget._nodes == []
        assert widget._edges == []
        assert widget._filter_kind == ""
        assert widget._filter_text == ""

    def test_widget_can_focus(self) -> None:
        widget = EvidenceGraphWidget()
        assert widget.can_focus is True

    def test_widget_update_graph(self) -> None:
        widget = EvidenceGraphWidget()
        nodes = [
            {"id": "source:a", "label": "a", "kind": "source", "count": 5},
            {"id": "entity:b", "label": "b", "kind": "entity", "count": 3},
        ]
        edges = [{"source": "source:a", "target": "entity:b", "label": "linked"}]
        widget.update_graph(nodes, edges)
        assert len(widget._nodes) == 2
        assert len(widget._edges) == 1

    def test_widget_filter_by_kind(self) -> None:
        widget = EvidenceGraphWidget()
        nodes = [
            {"id": "source:a", "label": "a", "kind": "source", "count": 5},
            {"id": "entity:b", "label": "b", "kind": "entity", "count": 3},
        ]
        widget.update_graph(nodes, [])
        widget.set_filter(kind="source")
        filtered = widget._filtered_nodes()
        assert len(filtered) == 1
        assert filtered[0]["kind"] == "source"

    def test_widget_filter_by_text(self) -> None:
        widget = EvidenceGraphWidget()
        nodes = [
            {"id": "source:btc", "label": "BTC-source", "kind": "source", "count": 5},
            {"id": "entity:eth", "label": "ETH-entity", "kind": "entity", "count": 3},
        ]
        widget.update_graph(nodes, [])
        widget.set_filter(text="btc")
        filtered = widget._filtered_nodes()
        assert len(filtered) == 1
        assert "btc" in filtered[0]["label"].lower()

    def test_widget_render_empty(self) -> None:
        widget = EvidenceGraphWidget()
        result = widget.render()
        assert "No evidence data" in str(result.plain)

    def test_widget_render_filter_no_match(self) -> None:
        widget = EvidenceGraphWidget()
        nodes = [{"id": "source:a", "label": "a", "kind": "source", "count": 5}]
        widget.update_graph(nodes, [])
        widget.set_filter(text="nonexistent")
        result = widget.render()
        text = str(result.plain)
        assert "No matches" in text
        assert "nonexistent" in text

    def test_widget_render_with_data(self) -> None:
        widget = EvidenceGraphWidget()
        nodes = [
            {"id": "source:a", "label": "test-source", "kind": "source", "count": 10},
            {"id": "entity:b", "label": "BTC-USD", "kind": "entity", "count": 5},
        ]
        edges = [{"source": "source:a", "target": "entity:b", "label": "linked"}]
        widget.update_graph(nodes, edges)
        result = widget.render()
        text = str(result.plain)
        assert "Evidence Graph" in text
        assert "test-source" in text
        assert "BTC-USD" in text

    def test_widget_render_groups_by_kind(self) -> None:
        widget = EvidenceGraphWidget()
        nodes = [
            {"id": "source:a", "label": "src-a", "kind": "source", "count": 5},
            {"id": "source:b", "label": "src-b", "kind": "source", "count": 3},
            {"id": "entity:c", "label": "ent-c", "kind": "entity", "count": 2},
        ]
        widget.update_graph(nodes, [])
        result = widget.render()
        text = str(result.plain)
        assert "SOURCE" in text
        assert "ENTITY" in text


# ── Edge Detail Widget Tests ─────────────────────────────────────────


class TestEdgeDetailWidget:
    """Test EdgeDetailWidget rendering."""

    def test_widget_init(self) -> None:
        widget = EdgeDetailWidget()
        assert widget._edges == []

    def test_widget_can_focus(self) -> None:
        widget = EdgeDetailWidget()
        assert widget.can_focus is True

    def test_widget_update_edges(self) -> None:
        widget = EdgeDetailWidget()
        edges = [{"source": "a", "target": "b", "label": "linked"}]
        widget.update_edges(edges)
        assert len(widget._edges) == 1

    def test_widget_render_empty(self) -> None:
        widget = EdgeDetailWidget()
        result = widget.render()
        assert "No connections" in str(result.plain)

    def test_widget_render_with_edges(self) -> None:
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
        result = widget.render()
        text = str(result.plain)
        assert "Connections" in text
        assert "BTC-USD" in text

    def test_widget_render_with_warning(self) -> None:
        widget = EdgeDetailWidget()
        edges = [
            {
                "source": "source:a",
                "target": "entity:b",
                "label": "linked",
                "confidence": 0.45,
                "warning": "temporal link only",
            },
        ]
        widget.update_edges(edges)
        result = widget.render()
        text = str(result.plain)
        assert "temporal link" in text


# ── Demo Flow Widget Tests ───────────────────────────────────────────


class TestDemoFlowWidget:
    """Test DemoFlowWidget rendering."""

    def test_widget_init(self) -> None:
        widget = DemoFlowWidget()
        assert widget._current_step == 1
        assert widget._step_results == {}
        assert widget._running is False

    def test_widget_current_step(self) -> None:
        widget = DemoFlowWidget()
        assert widget.current_step == 1

    def test_widget_advance_step(self) -> None:
        widget = DemoFlowWidget()
        widget.advance_step()
        assert widget._current_step == 2

    def test_widget_advance_step_at_end(self) -> None:
        widget = DemoFlowWidget()
        widget._current_step = len(DEMO_STEPS)
        widget.advance_step()
        assert widget._current_step == len(DEMO_STEPS)

    def test_widget_retreat_step(self) -> None:
        widget = DemoFlowWidget()
        widget._current_step = 3
        widget.retreat_step()
        assert widget._current_step == 2

    def test_widget_retreat_step_at_start(self) -> None:
        widget = DemoFlowWidget()
        widget._current_step = 1
        widget.retreat_step()
        assert widget._current_step == 1

    def test_widget_can_focus(self) -> None:
        widget = DemoFlowWidget()
        assert widget.can_focus is True

    def test_widget_set_step_result(self) -> None:
        widget = DemoFlowWidget()
        widget.set_step_result(1, {"returncode": 0, "stdout": "{}", "stderr": ""})
        assert 1 in widget._step_results

    def test_widget_set_running(self) -> None:
        widget = DemoFlowWidget()
        widget.set_running(True)
        assert widget._running is True
        widget.set_running(False)
        assert widget._running is False

    def test_widget_render_shows_all_steps(self) -> None:
        widget = DemoFlowWidget()
        result = widget.render()
        text = str(result.plain)
        for step in DEMO_STEPS:
            assert step["title"] in text

    def test_widget_render_current_step_highlighted(self) -> None:
        widget = DemoFlowWidget()
        result = widget.render()
        text = str(result.plain)
        # First step should be the current one
        assert "▶" in text

    def test_widget_render_completed_step(self) -> None:
        widget = DemoFlowWidget()
        widget.set_step_result(1, {"returncode": 0, "stdout": "{}", "stderr": ""})
        result = widget.render()
        text = str(result.plain)
        assert "✓" in text

    def test_widget_render_failed_step(self) -> None:
        widget = DemoFlowWidget()
        widget.set_step_result(1, {"returncode": 1, "stdout": "", "stderr": "error"})
        result = widget.render()
        text = str(result.plain)
        assert "✗" in text

    def test_widget_render_running_state(self) -> None:
        widget = DemoFlowWidget()
        widget.set_running(True)
        result = widget.render()
        text = str(result.plain)
        assert "Running" in text or "⟳" in text

    def test_widget_render_shows_result_summary(self) -> None:
        widget = DemoFlowWidget()
        widget.set_step_result(1, {
            "returncode": 0,
            "stdout": '{"record_count": 620}',
            "stderr": "",
        })
        result = widget.render()
        text = str(result.plain)
        assert "records: 620" in text


# ── Screen Registration Tests ────────────────────────────────────────


class TestEvidenceScreenRegistration:
    """Test that the evidence screen is properly registered."""

    def test_evidence_screen_importable(self) -> None:
        assert EvidenceScreen is not None

    def test_evidence_screen_in_screens_registry(self) -> None:
        from siglab.tui.app import SigLabTUI
        assert "evidence" in SigLabTUI.SCREENS

    def test_evidence_screen_bindings(self) -> None:
        binding_keys = [b.key for b in EvidenceScreen.BINDINGS]
        assert "r" in binding_keys  # refresh
        assert "f" in binding_keys  # filter
        assert "tab" in binding_keys  # switch pane
        assert "enter" in binding_keys  # run step
        assert "n" in binding_keys  # next step
        assert "p" in binding_keys  # prev step
        assert "a" in binding_keys  # run all

    def test_app_css_includes_evidence(self) -> None:
        from siglab.tui.app import SigLabTUI
        assert "styles/evidence.tcss" in SigLabTUI.CSS_PATH


# ── API Integration Tests ────────────────────────────────────────────


class TestEvidenceApiIntegration:
    """Test API client integration for evidence."""

    @pytest.mark.asyncio
    async def test_api_client_has_evidence_method(self) -> None:
        from siglab.tui.api_client import TuiApiClient
        client = TuiApiClient()
        assert hasattr(client, "get_evidence_graph")
        await client.close()
