"""Tests for the SigLab TUI foundation scaffold.

Covers: app shell, navigation sidebar, status bar, API client,
CLI bridge, placeholder screens, help overlay, and keyboard shortcuts.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from siglab.tui import SigLabTUI as _SigLabTUIExported
from siglab.tui import formatting as _formatting
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
from siglab.tui.formatting import (
    format_change,
    format_pnl,
    format_price,
    format_score,
    format_volume,
    friendly_error,
    truncate,
)
from siglab.tui.loading import LoadingIndicator
from siglab.tui.widgets import SigLabStatusBar as _SigLabStatusBarExported
from siglab.tui.widgets.status_bar import SigLabStatusBar

TUI_DIR = Path(__file__).resolve().parents[1] / "siglab" / "tui"
SCREEN_CLASSES = (
    __import__("siglab.tui.screens.market", fromlist=["MarketScreen"]).MarketScreen,
    __import__("siglab.tui.screens.paper", fromlist=["PaperScreen"]).PaperScreen,
    __import__("siglab.tui.screens.risk", fromlist=["RiskScreen"]).RiskScreen,
    __import__("siglab.tui.screens.strategy", fromlist=["StrategyScreen"]).StrategyScreen,
    __import__("siglab.tui.screens.telemetry", fromlist=["TelemetryScreen"]).TelemetryScreen,
    __import__("siglab.tui.screens.evidence", fromlist=["EvidenceScreen"]).EvidenceScreen,
)

def _binding_keys(cls) -> list[str]:
    return [b.key for b in cls.BINDINGS]

def test_nav_items_has_six_entries() -> None:
    assert len(NAV_ITEMS) == 6

def test_nav_items_have_required_fields() -> None:
    for idx, label, screen_id in NAV_ITEMS:
        assert isinstance(idx, str) and len(idx) == 1
        assert isinstance(label, str) and len(label) > 0
        assert isinstance(screen_id, str) and len(screen_id) > 0
        assert "[" in label and "]" in label, f"Label '{label}' should use bracket style"

def test_screen_ids_and_names_match_nav_items() -> None:
    assert SCREEN_IDS == {item[2] for item in NAV_ITEMS}
    assert SCREEN_NAMES == {item[2]: item[1] for item in NAV_ITEMS}

def test_nav_item_indices_are_sequential() -> None:
    assert [idx for idx, _, _ in NAV_ITEMS] == ["1", "2", "3", "4", "5", "6"]

def test_app_title_and_subtitle() -> None:
    assert SigLabTUI.TITLE == "SigLab"
    assert SigLabTUI.SUB_TITLE == "Terminal Dashboard"

def test_app_has_css_path() -> None:
    assert "styles/app.tcss" in SigLabTUI.CSS_PATH
    assert len(SigLabTUI.CSS_PATH) == 1

def test_app_has_screens_registry() -> None:
    assert hasattr(SigLabTUI, "SCREENS")
    assert len(SigLabTUI.SCREENS) == 6
    for screen_id in SCREEN_IDS:
        assert screen_id in SigLabTUI.SCREENS

@pytest.mark.parametrize("expected_key", ["q", "?", "question_mark", "escape", "1", "6", "ctrl+c"])
def test_app_has_required_bindings(expected_key) -> None:
    keys = _binding_keys(SigLabTUI)
    assert expected_key in keys or (
        expected_key == "question_mark" and "?" in keys
    ), f"Missing binding {expected_key}"

def test_app_instantiation_creates_api_client() -> None:
    assert isinstance(SigLabTUI().api_client, TuiApiClient)

def test_placeholder_screen_init() -> None:
    with_id = PlaceholderScreen("Market", screen_id="market")
    assert with_id._screen_name == "Market" and with_id.id == "market"
    assert PlaceholderScreen("Test")._screen_name == "Test"

def test_help_screen_has_bindings() -> None:
    keys = _binding_keys(HelpScreen)
    assert "escape" in keys and "q" in keys
    assert "question_mark" in keys or "?" in keys

def test_help_screen_global_and_screen_keybindings() -> None:
    assert len(HelpScreen.GLOBAL_KEYBINDINGS) >= 7
    assert "1-6" in [k for k, _ in HelpScreen.GLOBAL_KEYBINDINGS]
    for screen_id in ["market", "paper", "risk", "strategy", "telemetry", "evidence"]:
        assert screen_id in HelpScreen.SCREEN_KEYBINDINGS, f"Missing keybindings for {screen_id}"

def test_help_screen_accepts_screen_context() -> None:
    s = HelpScreen(screen_name="Market", screen_id="market")
    assert s._screen_name == "Market" and s._screen_id == "market"
    empty = HelpScreen()
    assert empty._screen_name == "" and empty._screen_id == ""

def test_api_client_init_default_url() -> None:
    client = TuiApiClient()
    assert client._base_url == "http://localhost:3100"
    assert client._timeout == 10.0

def test_api_client_init_custom_url() -> None:
    assert TuiApiClient(base_url="http://example.com:9999/")._base_url == "http://example.com:9999"

def test_api_client_init_custom_timeout() -> None:
    assert TuiApiClient(timeout=30.0)._timeout == 30.0

def test_api_client_lazy_initialization() -> None:
    assert TuiApiClient()._client is None

@pytest.mark.asyncio
async def test_ensure_client_creates_httpx_client() -> None:
    client = TuiApiClient()
    try:
        assert isinstance(await client._ensure_client(), httpx.AsyncClient)
    finally:
        await client.close()

@pytest.mark.asyncio
async def test_close_sets_client_none() -> None:
    client = TuiApiClient()
    await client._ensure_client()
    assert client._client is not None
    await client.close()
    assert client._client is None

@pytest.mark.asyncio
async def test_close_when_no_client() -> None:
    client = TuiApiClient()
    await client.close()  # Should not raise
    assert client._client is None

def _patched_get(payload: object, status_code: int = 200):
    """Patch httpx.AsyncClient.get to return the given JSON payload."""
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.json.return_value = payload
    mock_response.raise_for_status = MagicMock()
    return patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock, return_value=mock_response)

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method_name,payload,assertions",
    [
        ("get_health", {"status": "ok", "version": "0.1.0", "uptime_seconds": 42.0},
         lambda r: r["status"] == "ok" and "version" in r and "uptime_seconds" in r),
        ("get_config", {"system": {}, "sosovalue": {}}, lambda r: isinstance(r, dict)),
        ("get_ops_board", {"artifact_status": {}, "summary": {}, "service_health": {}},
         lambda r: isinstance(r, dict)),
        ("get_evidence_graph", {"nodes": [], "edges": []},
         lambda r: "nodes" in r and "edges" in r),
        ("get_skill_report", {"skills": []}, lambda r: isinstance(r, dict)),
        ("get_risk", {"composite_score": 0.5, "max_drawdown": 0.1},
         lambda r: "composite_score" in r),
    ],
)
async def test_api_client_get_methods(method_name, payload, assertions) -> None:
    client = TuiApiClient()
    try:
        with _patched_get(payload):
            result = await getattr(client, method_name)()
            assert assertions(result)
    finally:
        await client.close()

@pytest.mark.asyncio
async def test_get_health_http_error() -> None:
    client = TuiApiClient()
    try:
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
    finally:
        await client.close()

def test_cli_result_is_named_tuple() -> None:
    result = CliResult(returncode=0, stdout="ok", stderr="", command="test")
    assert result.returncode == 0 and result.stdout == "ok" and result.stderr == "" and result.command == "test"

def test_cli_result_fields() -> None:
    for field in ("returncode", "stdout", "stderr", "command"):
        assert field in CliResult._fields

@pytest.mark.asyncio
async def test_run_cli_with_args() -> None:
    result = await run_cli("--help")
    assert result.returncode == 0
    assert "siglab" in result.stdout.lower() or "usage" in result.stdout.lower() or len(result.stdout) > 0

def test_status_bar_init_defaults() -> None:
    bar = SigLabStatusBar()
    assert bar._version == "0.1.0" and bar._api_url == "http://localhost:3100" and bar._connected is False

def test_status_bar_init_custom() -> None:
    bar = SigLabStatusBar(version="2.0.0", api_url="http://example.com:9999")
    assert bar._version == "2.0.0" and bar._api_url == "http://example.com:9999"

def test_status_bar_set_connected() -> None:
    bar = SigLabStatusBar()
    assert bar._connected is False
    bar._connected = True
    assert bar._connected is True

@pytest.mark.parametrize("attr", ["compose", "on_mount", "on_unmount", "_check_api_connection",
                                  "watch_api_connected", "action_show_help", "action_go_back"])
def test_app_has_required_method(attr) -> None:
    assert hasattr(SigLabTUI, attr)

def test_app_has_screen_switch_actions() -> None:
    for screen_id in SCREEN_IDS:
        assert hasattr(SigLabTUI, f"action_switch_to_{screen_id}")

@pytest.mark.parametrize("screen_cls", SCREEN_CLASSES)
@pytest.mark.parametrize("expected_key", ["ctrl+c", "escape"])
def test_all_screens_have_required_binding(screen_cls, expected_key) -> None:
    assert expected_key in _binding_keys(screen_cls), f"{screen_cls.__name__} missing {expected_key}"

@pytest.mark.parametrize("screen_cls", SCREEN_CLASSES)
def test_all_screens_have_help_binding(screen_cls) -> None:
    keys = _binding_keys(screen_cls)
    assert "question_mark" in keys or "?" in keys, f"{screen_cls.__name__} missing ?"

def test_nav_sidebar_has_compose_and_highlight() -> None:
    assert hasattr(NavSidebar, "compose") and hasattr(NavSidebar, "highlight_item")

def test_nav_sidebar_build_items() -> None:
    items = NavSidebar._build_items()
    assert len(items) == 6
    assert [item.id for item in items] == [
        "nav-market", "nav-paper", "nav-risk", "nav-strategy", "nav-telemetry", "nav-evidence",
    ]

@pytest.mark.parametrize("css_file", ["app.tcss", "theme.tcss"])
def test_css_files_exist(css_file) -> None:
    assert (TUI_DIR / "styles" / css_file).exists()

def test_app_tcss_has_sidebar_and_status_bar_styles() -> None:
    content = (TUI_DIR / "styles" / "app.tcss").read_text()
    for selector in ("#nav-sidebar", "#nav-title", "#nav-list", "#status-bar"):
        assert selector in content

def test_theme_tcss_has_color_variables() -> None:
    content = (TUI_DIR / "styles" / "theme.tcss").read_text()
    for var in ("$accent-green", "$warning-yellow", "$error-red", "$info-blue", "$text-primary",
                "$success", "$warning", "$error", "$info"):
        assert var in content

def test_theme_bg_not_pure_black() -> None:
    content = (TUI_DIR / "styles" / "theme.tcss").read_text()
    assert "$bg: #0a0a0a;" in content
    assert "$bg: #000000" not in content

def test_tui_init_exports_siglab_tui() -> None:
    assert _SigLabTUIExported is SigLabTUI

def test_widgets_init_exports_status_bar() -> None:
    assert _SigLabStatusBarExported is SigLabStatusBar

@pytest.mark.parametrize("subdir", ["", "widgets", "styles"])
def test_package_init_exists(subdir) -> None:
    assert (TUI_DIR / subdir / "__init__.py").exists()

def test_friendly_error_handles_known_exceptions() -> None:
    cases = [
        (httpx.ConnectError("Connection refused"), ("connect", "server"), True),
        (httpx.TimeoutException("Timed out"), ("timeout", "timed out"), False),
        (httpx.HTTPStatusError("Server Error", request=httpx.Request("GET", "http://t"),
                                response=httpx.Response(500)),
         ("500", "server"), False),
        (ValueError("bad value"), ("unexpected", "error"), False),
    ]
    for exc, required_words, no_traceback in cases:
        msg_lower = friendly_error(exc).lower()
        assert any(w in msg_lower for w in required_words), f"Missing keyword for {exc!r}"
        if no_traceback:
            assert "traceback" not in msg_lower and "httpx" not in msg_lower

def test_format_price() -> None:
    assert format_price(67234.56) == "67,234.56" and format_price(0.00123) == "0.001230"

@pytest.mark.parametrize("value,expected", [(2_500_000_000, "B"), (5_000_000, "M"), (15_000, "K")])
def test_format_volume_suffix(value, expected) -> None:
    assert expected in format_volume(value)

def test_format_change_positive_and_negative() -> None:
    assert "2.50%" in format_change(2.5).plain
    assert "1.30%" in format_change(-1.3).plain

def test_format_pnl() -> None:
    assert "1,234.56" in format_pnl(1234.56).plain

def test_format_score() -> None:
    assert "0.850" in format_score(0.85).plain
    assert format_score(None).plain == "─"

def test_truncate() -> None:
    assert truncate("hello", 10) == "hello"
    long = truncate("hello world", 6)
    assert len(long) == 6 and long.endswith("…")

def test_color_constants_defined() -> None:
    assert (_formatting.ACCENT_GREEN == "#4ade80" and _formatting.ERROR_RED == "#f87171"
            and _formatting.WARNING_YELLOW == "#f0b456" and _formatting.INFO_BLUE == "#60a5fa"
            and _formatting.BG == "#0a0a0a")

def test_loading_indicator_import() -> None:
    assert LoadingIndicator is not None

def test_loading_indicator_default_state() -> None:
    indicator = LoadingIndicator()
    assert indicator.loading is False and indicator.status_text == ""

def test_loading_indicator_has_default_css() -> None:
    assert "height: 1" in LoadingIndicator.DEFAULT_CSS
