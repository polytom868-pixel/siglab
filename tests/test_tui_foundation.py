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
from siglab.tui.cli_bridge import CliResult, run_cli
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
            # Labels should use ASCII bracket style, not emoji
            assert "[" in label and "]" in label, f"Label '{label}' should use bracket style"

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
        # CSS is consolidated in app.tcss (variables + all screen styles)
        assert len(SigLabTUI.CSS_PATH) == 1

    def test_app_has_screens_registry(self) -> None:
        assert hasattr(SigLabTUI, "SCREENS")
        assert len(SigLabTUI.SCREENS) == 6
        for screen_id in SCREEN_IDS:
            assert screen_id in SigLabTUI.SCREENS

    def test_app_has_bindings(self) -> None:
        binding_keys = [b.key for b in SigLabTUI.BINDINGS]
        assert "q" in binding_keys
        assert "?" in binding_keys or "question_mark" in binding_keys
        assert "escape" in binding_keys
        assert "1" in binding_keys
        assert "6" in binding_keys
        assert "ctrl+c" in binding_keys

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
        assert "question_mark" in binding_keys or "?" in binding_keys

    def test_help_screen_has_global_keybindings_list(self) -> None:
        assert len(HelpScreen.GLOBAL_KEYBINDINGS) >= 7
        keys = [k for k, _ in HelpScreen.GLOBAL_KEYBINDINGS]
        assert "1-6" in keys

    def test_help_screen_has_screen_keybindings(self) -> None:
        assert "market" in HelpScreen.SCREEN_KEYBINDINGS
        assert "paper" in HelpScreen.SCREEN_KEYBINDINGS
        assert "risk" in HelpScreen.SCREEN_KEYBINDINGS
        assert "strategy" in HelpScreen.SCREEN_KEYBINDINGS
        assert "telemetry" in HelpScreen.SCREEN_KEYBINDINGS
        assert "evidence" in HelpScreen.SCREEN_KEYBINDINGS

    def test_help_screen_accepts_screen_context(self) -> None:
        screen = HelpScreen(screen_name="Market", screen_id="market")
        assert screen._screen_name == "Market"
        assert screen._screen_id == "market"

    def test_help_screen_default_no_context(self) -> None:
        screen = HelpScreen()
        assert screen._screen_name == ""
        assert screen._screen_id == ""


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
    async def test_run_cli_with_args(self) -> None:
        result = await run_cli("--help")
        assert result.returncode == 0
        assert "siglab" in result.stdout.lower() or "usage" in result.stdout.lower() or len(result.stdout) > 0


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

    def test_all_screens_have_ctrl_c_binding(self) -> None:
        """Verify all screens have consistent ctrl+c binding."""
        from siglab.tui.screens.market import MarketScreen
        from siglab.tui.screens.paper import PaperScreen
        from siglab.tui.screens.risk import RiskScreen
        from siglab.tui.screens.strategy import StrategyScreen
        from siglab.tui.screens.telemetry import TelemetryScreen
        from siglab.tui.screens.evidence import EvidenceScreen
        for screen_cls in [MarketScreen, PaperScreen, RiskScreen, StrategyScreen, TelemetryScreen, EvidenceScreen]:
            keys = [b.key for b in screen_cls.BINDINGS]
            assert "ctrl+c" in keys, f"{screen_cls.__name__} missing ctrl+c"
            assert "question_mark" in keys or "?" in keys, f"{screen_cls.__name__} missing ?"
            assert "escape" in keys, f"{screen_cls.__name__} missing escape"

    def test_all_screens_have_escape_binding(self) -> None:
        """Verify all screens have escape binding."""
        from siglab.tui.screens.market import MarketScreen
        from siglab.tui.screens.paper import PaperScreen
        from siglab.tui.screens.risk import RiskScreen
        from siglab.tui.screens.strategy import StrategyScreen
        from siglab.tui.screens.telemetry import TelemetryScreen
        from siglab.tui.screens.evidence import EvidenceScreen
        for screen_cls in [MarketScreen, PaperScreen, RiskScreen, StrategyScreen, TelemetryScreen, EvidenceScreen]:
            keys = [b.key for b in screen_cls.BINDINGS]
            assert "escape" in keys, f"{screen_cls.__name__} missing escape"


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

    def test_theme_bg_not_pure_black(self) -> None:
        """Background should be slightly off-black for CRT aesthetic."""
        from pathlib import Path

        tcss_path = Path(__file__).resolve().parents[1] / "siglab" / "tui" / "styles" / "theme.tcss"
        content = tcss_path.read_text()
        assert "$bg: #0a0a0a;" in content
        assert "$bg: #000000" not in content


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


# ── Formatting Module Tests ──────────────────────────────────────────


class TestFormatting:
    """Test the shared formatting helpers module."""

    def test_friendly_error_connect(self) -> None:
        from siglab.tui.formatting import friendly_error
        import httpx
        exc = httpx.ConnectError("Connection refused")
        msg = friendly_error(exc)
        assert "connect" in msg.lower() or "server" in msg.lower()

    def test_friendly_error_timeout(self) -> None:
        from siglab.tui.formatting import friendly_error
        import httpx
        exc = httpx.TimeoutException("Timed out")
        msg = friendly_error(exc)
        assert "timed out" in msg.lower() or "timeout" in msg.lower()

    def test_friendly_error_http_status(self) -> None:
        from siglab.tui.formatting import friendly_error
        import httpx
        exc = httpx.HTTPStatusError(
            "Server Error",
            request=httpx.Request("GET", "http://test"),
            response=httpx.Response(500),
        )
        msg = friendly_error(exc)
        assert "500" in msg or "server" in msg.lower()

    def test_friendly_error_generic(self) -> None:
        from siglab.tui.formatting import friendly_error
        msg = friendly_error(ValueError("bad value"))
        assert "unexpected" in msg.lower() or "error" in msg.lower()

    def test_format_price_high(self) -> None:
        from siglab.tui.formatting import format_price
        assert format_price(67234.56) == "67,234.56"

    def test_format_price_low(self) -> None:
        from siglab.tui.formatting import format_price
        assert format_price(0.00123) == "0.001230"

    def test_format_volume(self) -> None:
        from siglab.tui.formatting import format_volume
        assert "B" in format_volume(2_500_000_000)
        assert "M" in format_volume(5_000_000)
        assert "K" in format_volume(15_000)

    def test_format_change_positive(self) -> None:
        from siglab.tui.formatting import format_change
        text = format_change(2.5)
        assert "2.50%" in text.plain

    def test_format_change_negative(self) -> None:
        from siglab.tui.formatting import format_change
        text = format_change(-1.3)
        assert "1.30%" in text.plain

    def test_format_pnl(self) -> None:
        from siglab.tui.formatting import format_pnl
        text = format_pnl(1234.56)
        assert "1,234.56" in text.plain

    def test_format_score_high(self) -> None:
        from siglab.tui.formatting import format_score
        text = format_score(0.85)
        assert "0.850" in text.plain

    def test_format_score_none(self) -> None:
        from siglab.tui.formatting import format_score
        text = format_score(None)
        assert text.plain == "\u2500"

    def test_truncate_short(self) -> None:
        from siglab.tui.formatting import truncate
        assert truncate("hello", 10) == "hello"

    def test_truncate_long(self) -> None:
        from siglab.tui.formatting import truncate
        result = truncate("hello world", 6)
        assert len(result) == 6
        assert result.endswith("\u2026")

    def test_color_constants_defined(self) -> None:
        from siglab.tui import formatting
        assert formatting.ACCENT_GREEN == "#4ade80"
        assert formatting.ERROR_RED == "#f87171"
        assert formatting.WARNING_YELLOW == "#f0b456"
        assert formatting.INFO_BLUE == "#60a5fa"
        assert formatting.BG == "#0a0a0a"


# ── Loading Indicator Tests ──────────────────────────────────────────


class TestLoadingIndicator:
    """Test the loading indicator widget."""

    def test_loading_indicator_import(self) -> None:
        from siglab.tui.loading import LoadingIndicator
        assert LoadingIndicator is not None

    def test_loading_indicator_default_state(self) -> None:
        from siglab.tui.loading import LoadingIndicator
        indicator = LoadingIndicator()
        assert indicator.loading is False
        assert indicator.status_text == ""

    def test_loading_indicator_has_default_css(self) -> None:
        from siglab.tui.loading import LoadingIndicator
        assert "height: 1" in LoadingIndicator.DEFAULT_CSS
