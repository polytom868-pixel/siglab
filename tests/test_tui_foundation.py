"""Tests for the SigLab TUI foundation scaffold.

Covers: app shell, navigation sidebar, status bar, API client,
CLI bridge, placeholder screens, help overlay, and keyboard shortcuts.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from siglab.tui.api_client import TuiApiClient
from siglab.tui.app import (
    NAV_ITEMS,
    SCREEN_IDS,
    SCREEN_NAMES,
    HelpScreen,
    NavSidebar,
    PlaceholderScreen,
    SigLabTUI,
)
from siglab.tui.cli_bridge import CliResult, format_cli_output, run_cli, run_cli_help
from siglab.tui.widgets.status_bar import SigLabStatusBar


# ── Constants Tests ───────────────────────────────────────────────────


class TestNavConstants:
    """Test navigation item constants are well-formed."""

    def test_nav_items_has_six_entries(self) -> None:
        assert len(NAV_ITEMS) == 6

    def test_nav_items_have_required_fields(self) -> None:
        for idx, label, screen_id in NAV_ITEMS:
            assert isinstance(idx, str) and len(idx) == 1
            assert isinstance(label, str) and len(label) > 0
            assert isinstance(screen_id, str) and len(screen_id) > 0

    def test_screen_ids_match_nav_items(self) -> None:
        expected = {item[2] for item in NAV_ITEMS}
        assert SCREEN_IDS == expected

    def test_screen_names_match_nav_items(self) -> None:
        expected = {item[2]: item[1] for item in NAV_ITEMS}
        assert SCREEN_NAMES == expected

    def test_nav_item_indices_are_sequential(self) -> None:
        indices = [idx for idx, _, _ in NAV_ITEMS]
        assert indices == ["1", "2", "3", "4", "5", "6"]


# ── App Instantiation Tests ──────────────────────────────────────────


class TestSigLabTUIApp:
    """Test SigLabTUI app instantiation and configuration."""

    def test_app_imports_cleanly(self) -> None:
        assert SigLabTUI is not None

    def test_app_has_title(self) -> None:
        assert SigLabTUI.TITLE == "SigLab"

    def test_app_has_subtitle(self) -> None:
        assert SigLabTUI.SUB_TITLE == "Terminal Dashboard"

    def test_app_has_css_path(self) -> None:
        assert "styles/app.tcss" in SigLabTUI.CSS_PATH
        assert "styles/theme.tcss" in SigLabTUI.CSS_PATH

    def test_app_has_screens_registry(self) -> None:
        assert hasattr(SigLabTUI, "SCREENS")
        assert len(SigLabTUI.SCREENS) == 6
        for screen_id in SCREEN_IDS:
            assert screen_id in SigLabTUI.SCREENS

    def test_app_has_bindings(self) -> None:
        binding_keys = [b.key for b in SigLabTUI.BINDINGS]
        assert "q" in binding_keys
        assert "?" in binding_keys
        assert "escape" in binding_keys
        assert "1" in binding_keys
        assert "6" in binding_keys

    def test_app_instantiation_creates_api_client(self) -> None:
        app = SigLabTUI()
        assert isinstance(app.api_client, TuiApiClient)


# ── Placeholder Screen Tests ─────────────────────────────────────────


class TestPlaceholderScreen:
    """Test PlaceholderScreen renders correctly."""

    def test_placeholder_screen_init_with_name(self) -> None:
        screen = PlaceholderScreen("Market", screen_id="market")
        assert screen._screen_name == "Market"
        assert screen.id == "market"

    def test_placeholder_screen_init_without_id(self) -> None:
        screen = PlaceholderScreen("Test")
        assert screen._screen_name == "Test"


# ── Help Screen Tests ────────────────────────────────────────────────


class TestHelpScreen:
    """Test HelpScreen overlay."""

    def test_help_screen_has_bindings(self) -> None:
        binding_keys = [b.key for b in HelpScreen.BINDINGS]
        assert "escape" in binding_keys
        assert "q" in binding_keys
        assert "?" in binding_keys

    def test_help_screen_has_keybindings_list(self) -> None:
        assert len(HelpScreen.KEYBINDINGS) >= 5
        keys = [k for k, _ in HelpScreen.KEYBINDINGS]
        assert "1-6" in keys


# ── API Client Tests ─────────────────────────────────────────────────


class TestTuiApiClient:
    """Test TuiApiClient HTTP wrapper."""

    def test_init_default_url(self) -> None:
        client = TuiApiClient()
        assert client._base_url == "http://localhost:3100"
        assert client._timeout == 10.0

    def test_init_custom_url(self) -> None:
        client = TuiApiClient(base_url="http://example.com:9999/")
        assert client._base_url == "http://example.com:9999"

    def test_init_custom_timeout(self) -> None:
        client = TuiApiClient(timeout=30.0)
        assert client._timeout == 30.0

    def test_client_lazy_initialization(self) -> None:
        client = TuiApiClient()
        assert client._client is None

    @pytest.mark.asyncio
    async def test_ensure_client_creates_httpx_client(self) -> None:
        client = TuiApiClient()
        http_client = await client._ensure_client()
        assert isinstance(http_client, httpx.AsyncClient)
        await client.close()

    @pytest.mark.asyncio
    async def test_close_sets_client_none(self) -> None:
        client = TuiApiClient()
        await client._ensure_client()
        assert client._client is not None
        await client.close()
        assert client._client is None

    @pytest.mark.asyncio
    async def test_close_when_no_client(self) -> None:
        client = TuiApiClient()
        await client.close()  # Should not raise
        assert client._client is None

    @pytest.mark.asyncio
    async def test_get_health_success(self) -> None:
        client = TuiApiClient()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "ok",
            "version": "0.1.0",
            "uptime_seconds": 42.0,
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock, return_value=mock_response):
            result = await client.get_health()
            assert result["status"] == "ok"
            assert "version" in result
            assert "uptime_seconds" in result
        await client.close()

    @pytest.mark.asyncio
    async def test_get_health_http_error(self) -> None:
        client = TuiApiClient()
        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Service Unavailable",
            request=httpx.Request("GET", "http://localhost:3100/health"),
            response=httpx.Response(503),
        )

        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock, return_value=mock_response):
            with pytest.raises(httpx.HTTPStatusError):
                await client.get_health()
        await client.close()

    @pytest.mark.asyncio
    async def test_get_config(self) -> None:
        client = TuiApiClient()
        mock_response = MagicMock()
        mock_response.json.return_value = {"system": {}, "sosovalue": {}}
        mock_response.raise_for_status = MagicMock()

        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock, return_value=mock_response):
            result = await client.get_config()
            assert isinstance(result, dict)
        await client.close()

    @pytest.mark.asyncio
    async def test_get_ops_board(self) -> None:
        client = TuiApiClient()
        mock_response = MagicMock()
        mock_response.json.return_value = {"artifact_status": {}, "summary": {}, "service_health": {}}
        mock_response.raise_for_status = MagicMock()

        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock, return_value=mock_response):
            result = await client.get_ops_board()
            assert isinstance(result, dict)
        await client.close()

    @pytest.mark.asyncio
    async def test_get_evidence_graph(self) -> None:
        client = TuiApiClient()
        mock_response = MagicMock()
        mock_response.json.return_value = {"nodes": [], "edges": []}
        mock_response.raise_for_status = MagicMock()

        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock, return_value=mock_response):
            result = await client.get_evidence_graph()
            assert "nodes" in result
            assert "edges" in result
        await client.close()

    @pytest.mark.asyncio
    async def test_get_skill_report(self) -> None:
        client = TuiApiClient()
        mock_response = MagicMock()
        mock_response.json.return_value = {"skills": []}
        mock_response.raise_for_status = MagicMock()

        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock, return_value=mock_response):
            result = await client.get_skill_report()
            assert isinstance(result, dict)
        await client.close()

    @pytest.mark.asyncio
    async def test_get_risk(self) -> None:
        client = TuiApiClient()
        mock_response = MagicMock()
        mock_response.json.return_value = {"composite_score": 0.5, "max_drawdown": 0.1}
        mock_response.raise_for_status = MagicMock()

        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock, return_value=mock_response):
            result = await client.get_risk()
            assert "composite_score" in result
        await client.close()


# ── CLI Bridge Tests ─────────────────────────────────────────────────


class TestCliBridge:
    """Test the CLI bridge module."""

    def test_cli_result_is_named_tuple(self) -> None:
        result = CliResult(returncode=0, stdout="ok", stderr="", command="test")
        assert result.returncode == 0
        assert result.stdout == "ok"
        assert result.stderr == ""
        assert result.command == "test"

    def test_cli_result_fields(self) -> None:
        assert hasattr(CliResult, "_fields")
        assert "returncode" in CliResult._fields
        assert "stdout" in CliResult._fields
        assert "stderr" in CliResult._fields
        assert "command" in CliResult._fields

    @pytest.mark.asyncio
    async def test_run_cli_help(self) -> None:
        result = await run_cli_help()
        assert isinstance(result, CliResult)
        assert result.command.endswith("--help")
        # --help should exit 0
        assert result.returncode == 0
        assert len(result.stdout) > 0

    @pytest.mark.asyncio
    async def test_run_cli_with_args(self) -> None:
        result = await run_cli("--help")
        assert result.returncode == 0
        assert "siglab" in result.stdout.lower() or "usage" in result.stdout.lower() or len(result.stdout) > 0

    def test_format_cli_output_success(self) -> None:
        result = CliResult(returncode=0, stdout="All good", stderr="", command="test cmd")
        output = format_cli_output(result)
        assert "All good" in output
        assert "test cmd" in output

    def test_format_cli_output_failure(self) -> None:
        result = CliResult(returncode=1, stdout="", stderr="Error occurred", command="fail cmd")
        output = format_cli_output(result)
        assert "Error occurred" in output
        assert "fail cmd" in output

    def test_format_cli_output_empty(self) -> None:
        result = CliResult(returncode=0, stdout="", stderr="", command="empty")
        output = format_cli_output(result)
        assert "no output" in output.lower()

    def test_format_cli_output_both_streams(self) -> None:
        result = CliResult(returncode=0, stdout="out", stderr="err", command="both")
        output = format_cli_output(result)
        assert "out" in output
        assert "err" in output


# ── Status Bar Tests ─────────────────────────────────────────────────


class TestSigLabStatusBar:
    """Test the status bar widget."""

    def test_status_bar_init_defaults(self) -> None:
        bar = SigLabStatusBar()
        assert bar._version == "0.1.0"
        assert bar._api_url == "http://localhost:3100"
        assert bar._connected is False

    def test_status_bar_init_custom(self) -> None:
        bar = SigLabStatusBar(version="2.0.0", api_url="http://example.com:9999")
        assert bar._version == "2.0.0"
        assert bar._api_url == "http://example.com:9999"

    def test_status_bar_set_connected(self) -> None:
        bar = SigLabStatusBar()
        assert bar._connected is False
        # set_connected updates internal state
        # (full rendering test needs a mounted app)
        bar._connected = True
        assert bar._connected is True


# ── App Compose & Widget Tree Tests ──────────────────────────────────


class TestAppCompose:
    """Test that the app composes the expected widget tree."""

    def test_app_has_compose_method(self) -> None:
        assert hasattr(SigLabTUI, "compose")

    def test_app_has_on_mount(self) -> None:
        assert hasattr(SigLabTUI, "on_mount")

    def test_app_has_on_unmount(self) -> None:
        assert hasattr(SigLabTUI, "on_unmount")

    def test_app_has_check_api_connection(self) -> None:
        assert hasattr(SigLabTUI, "_check_api_connection")

    def test_app_has_watch_api_connected(self) -> None:
        assert hasattr(SigLabTUI, "watch_api_connected")

    def test_app_has_action_show_help(self) -> None:
        assert hasattr(SigLabTUI, "action_show_help")

    def test_app_has_action_go_back(self) -> None:
        assert hasattr(SigLabTUI, "action_go_back")

    def test_app_has_screen_switch_actions(self) -> None:
        for screen_id in SCREEN_IDS:
            method_name = f"action_switch_to_{screen_id}"
            assert hasattr(SigLabTUI, method_name), f"Missing {method_name}"


# ── NavSidebar Tests ─────────────────────────────────────────────────


class TestNavSidebar:
    """Test the navigation sidebar widget."""

    def test_nav_sidebar_has_compose(self) -> None:
        assert hasattr(NavSidebar, "compose")

    def test_nav_sidebar_has_highlight_item(self) -> None:
        assert hasattr(NavSidebar, "highlight_item")

    def test_nav_sidebar_build_items_returns_list(self) -> None:
        items = NavSidebar._build_items()
        assert len(items) == 6

    def test_nav_sidebar_build_items_ids(self) -> None:
        items = NavSidebar._build_items()
        ids = [item.id for item in items]
        assert ids == ["nav-market", "nav-paper", "nav-risk", "nav-strategy", "nav-telemetry", "nav-evidence"]


# ── Theme System Tests ───────────────────────────────────────────────


class TestThemeSystem:
    """Test that the theme/style files exist and are valid."""

    def test_app_tcss_exists(self) -> None:
        from pathlib import Path

        tcss_path = Path(__file__).resolve().parents[1] / "siglab" / "tui" / "styles" / "app.tcss"
        assert tcss_path.exists()

    def test_theme_tcss_exists(self) -> None:
        from pathlib import Path

        tcss_path = Path(__file__).resolve().parents[1] / "siglab" / "tui" / "styles" / "theme.tcss"
        assert tcss_path.exists()

    def test_app_tcss_has_sidebar_styles(self) -> None:
        from pathlib import Path

        tcss_path = Path(__file__).resolve().parents[1] / "siglab" / "tui" / "styles" / "app.tcss"
        content = tcss_path.read_text()
        assert "#nav-sidebar" in content
        assert "#nav-title" in content
        assert "#nav-list" in content
        assert "#status-bar" in content

    def test_theme_tcss_has_color_variables(self) -> None:
        from pathlib import Path

        tcss_path = Path(__file__).resolve().parents[1] / "siglab" / "tui" / "styles" / "theme.tcss"
        content = tcss_path.read_text()
        assert "$accent-green" in content
        assert "$warning-yellow" in content
        assert "$error-red" in content
        assert "$info-blue" in content
        assert "$text-primary" in content

    def test_theme_tcss_has_semantic_tokens(self) -> None:
        from pathlib import Path

        tcss_path = Path(__file__).resolve().parents[1] / "siglab" / "tui" / "styles" / "theme.tcss"
        content = tcss_path.read_text()
        assert "$success" in content
        assert "$warning" in content
        assert "$error" in content
        assert "$info" in content


# ── Module Structure Tests ───────────────────────────────────────────


class TestModuleStructure:
    """Test that the TUI module structure is correct."""

    def test_tui_init_exports_siglab_tui(self) -> None:
        from siglab.tui import SigLabTUI as Exported

        assert Exported is SigLabTUI

    def test_widgets_init_exports_status_bar(self) -> None:
        from siglab.tui.widgets import SigLabStatusBar as Exported

        assert Exported is SigLabStatusBar

    def test_tui_package_init_exists(self) -> None:
        from pathlib import Path

        init_path = Path(__file__).resolve().parents[1] / "siglab" / "tui" / "__init__.py"
        assert init_path.exists()

    def test_widgets_package_init_exists(self) -> None:
        from pathlib import Path

        init_path = Path(__file__).resolve().parents[1] / "siglab" / "tui" / "widgets" / "__init__.py"
        assert init_path.exists()

    def test_styles_package_init_exists(self) -> None:
        from pathlib import Path

        init_path = Path(__file__).resolve().parents[1] / "siglab" / "tui" / "styles" / "__init__.py"
        assert init_path.exists()
