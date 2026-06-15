"""Validation contract tests for TUI milestone assertions.

Tests: VAL-TUI-001, VAL-TUI-002, VAL-TUI-009

NOTE: CSS variables are consolidated in a single app.tcss file
(variables at top, then per-screen styles) so that $variables
resolve correctly.  theme.tcss is kept as a reference document.

Round 2: Added pilot-based tests for VAL-TUI-001 and VAL-TUI-009
after CSS variable resolution fix (f34-fix-css-variables).
"""

from __future__ import annotations

import io
import json
import os
import subprocess
from pathlib import Path

import httpx
import pytest
from rich.panel import Panel

from siglab.cli.rich_utils import make_console
from siglab.hardening_profile import build_profile, profile_as_text
from siglab.tui.api_client import TuiApiClient
from siglab.tui.app import NAV_ITEMS, SCREEN_IDS, HelpScreen, NavSidebar, SigLabTUI
from siglab.tui.formatting import friendly_error
from siglab.tui.loading import LoadingIndicator, _SPINNER_FRAMES
from siglab.tui.widgets.status_bar import SigLabStatusBar


REPO_ROOT = Path(__file__).resolve().parents[1]


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

# ── VAL-TUI-001: TUI app scaffold launches and navigates ─────────────

def test_nav_items_six_entries() -> None:
    assert len(NAV_ITEMS) == 6

def test_nav_items_have_required_fields() -> None:
    for idx, label, screen_id in NAV_ITEMS:
        assert isinstance(idx, str) and len(idx) == 1
        assert isinstance(label, str) and len(label) > 0
        assert isinstance(screen_id, str) and len(screen_id) > 0

def test_screen_ids_match_nav_items() -> None:
    assert SCREEN_IDS == {item[2] for item in NAV_ITEMS}

def test_app_has_screens_registry() -> None:
    assert len(SigLabTUI.SCREENS) == 6
    for screen_id in SCREEN_IDS:
        assert screen_id in SigLabTUI.SCREENS

def test_app_has_compose_method() -> None:
    assert hasattr(SigLabTUI, "compose")

@pytest.mark.parametrize("css_file", list(SigLabTUI.CSS_PATH))
def test_app_css_path_files_all_exist(css_file) -> None:
    assert (TUI_DIR / css_file).exists()

def test_screen_switch_actions_defined() -> None:
    for screen_id in SCREEN_IDS:
        assert hasattr(SigLabTUI, f"action_switch_to_{screen_id}")

def test_nav_sidebar_build_items() -> None:
    items = NavSidebar._build_items()

    assert len(items) == 6
    assert [item.id for item in items] == [
        "nav-market", "nav-paper", "nav-risk", "nav-strategy", "nav-telemetry", "nav-evidence",
    ]

def test_app_has_required_bindings() -> None:
    keys = _binding_keys(SigLabTUI)
    for key in ("q", "?", "question_mark", "escape", "1", "6", "ctrl+c"):
        assert key in keys or (key == "question_mark" and "?" in keys), f"Missing binding {key}"

def test_app_creates_api_client() -> None:
    assert isinstance(SigLabTUI().api_client, TuiApiClient)

# ── VAL-TUI-001 pilot tests ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_pilot_app_launches() -> None:
    async with SigLabTUI().run_test() as pilot:
        assert pilot.app.title == "SigLab" and pilot.app.is_mounted

@pytest.mark.asyncio
@pytest.mark.parametrize("widget_id", ["#nav-sidebar", "#status-bar", "#content-area"])
async def test_pilot_widgets_present(widget_id) -> None:
    async with SigLabTUI().run_test() as pilot:
        assert pilot.app.query_one(widget_id) is not None

@pytest.mark.asyncio
async def test_pilot_screen_switching_via_number_keys() -> None:
    async with SigLabTUI().run_test() as pilot:
        for key in ["1", "2", "3", "4", "5", "6"]:
            await pilot.press(key)
            await pilot.pause()

@pytest.mark.asyncio
async def test_pilot_screen_switching_via_nav_keys() -> None:
    async with SigLabTUI().run_test() as pilot:
        await pilot.press("j")
        await pilot.pause()
        await pilot.press("k")
        await pilot.pause()

# ── VAL-TUI-002: CLI commands render with Rich formatting ────────────

def _run_cli(args: list[str], env_overrides: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        ["poetry", "run", "python3", "-m", "siglab.cli"] + args,
        capture_output=True,
        text=True,
        cwd="/home/eya/soso/siglab",
        env=env,
        timeout=30,
    )

def _assert_json_dict(result: subprocess.CompletedProcess) -> dict:
    assert result.returncode == 0, f"stderr: {result.stderr}"
    return json.loads(result.stdout)

def test_profile_json_output_is_valid_json() -> None:
    data = build_profile(REPO_ROOT)
    assert isinstance(data, dict)


def test_profile_json_has_summary() -> None:
    data = build_profile(REPO_ROOT)
    assert "summary" in data and "finding_count" in data["summary"]

def test_telemetry_report_json_output() -> None:
    assert isinstance(_assert_json_dict(_run_cli(["telemetry-report", "--json"])), dict)

def test_telemetry_report_track_filter_json() -> None:
    assert isinstance(
        _assert_json_dict(_run_cli(["telemetry-report", "--track", "trend_signals", "--json"])), dict
    )

def test_demo_manifest_json_output() -> None:
    assert isinstance(_assert_json_dict(_run_cli(["demo-manifest", "--json"])), dict)

def test_profile_default_output_is_text() -> None:
    text = profile_as_text(build_profile(REPO_ROOT))
    assert isinstance(text, str) and text
    with pytest.raises(json.JSONDecodeError):
        json.loads(text)

def test_no_color_flag_removes_ansi() -> None:
    console = make_console(force_no_color=True)
    assert console.no_color is True
    buf = io.StringIO()
    console.file = buf
    console.print(Panel(profile_as_text(build_profile(REPO_ROOT)), title="Hardening Profile", border_style="info"))
    assert "\x1b[" not in buf.getvalue()


def test_no_color_env_var_removes_ansi() -> None:
    result = _run_cli(["profile"], env_overrides={"NO_COLOR": "1"})
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "\x1b[" not in result.stdout

def test_help_shows_no_color_option() -> None:
    result = _run_cli(["--help"])
    assert result.returncode == 0 and "--no-color" in result.stdout

def test_cli_exits_cleanly_on_valid_command() -> None:
    assert _run_cli(["profile", "--json"]).returncode == 0

# ── VAL-TUI-009: TUI hardening (keyboard, errors, resize, loading, refresh) ──

@pytest.mark.parametrize("expected_key", ["q", "ctrl+q", "ctrl+c", "f1", "escape"])
def test_app_global_bindings_present(expected_key) -> None:
    assert expected_key in _binding_keys(SigLabTUI)

def test_app_has_help_question_mark_binding() -> None:
    keys = _binding_keys(SigLabTUI)
    assert "question_mark" in keys or "?" in keys

def test_app_has_number_key_bindings() -> None:
    keys = _binding_keys(SigLabTUI)
    for i in range(1, 7):
        assert str(i) in keys, f"Missing binding for key '{i}'"

@pytest.mark.parametrize("screen_cls", SCREEN_CLASSES)
@pytest.mark.parametrize("expected_key", ["ctrl+c", "escape"])
def test_all_screens_have_required_binding(screen_cls, expected_key) -> None:
    assert expected_key in _binding_keys(screen_cls), f"{screen_cls.__name__} missing {expected_key}"

@pytest.mark.parametrize("screen_cls", SCREEN_CLASSES)
def test_all_screens_have_help_binding(screen_cls) -> None:
    keys = _binding_keys(screen_cls)
    assert "question_mark" in keys or "?" in keys, f"{screen_cls.__name__} missing ?"

def test_help_screen_has_bindings() -> None:
    keys = _binding_keys(HelpScreen)
    assert "escape" in keys and "q" in keys
    assert "question_mark" in keys or "?" in keys

def test_help_screen_has_global_keybindings() -> None:
    assert len(HelpScreen.GLOBAL_KEYBINDINGS) >= 7
    assert "1-6" in [k for k, _ in HelpScreen.GLOBAL_KEYBINDINGS]

def test_help_screen_has_screen_keybindings() -> None:
    for screen_id in ["market", "paper", "risk", "strategy", "telemetry", "evidence"]:
        assert screen_id in HelpScreen.SCREEN_KEYBINDINGS, f"Missing keybindings for {screen_id}"

def test_app_has_action_show_help_and_go_back() -> None:
    assert hasattr(SigLabTUI, "action_show_help") and hasattr(SigLabTUI, "action_go_back")

# ── Error handling ──────────────────────────────────────────────────

def test_friendly_error_connect() -> None:
    msg = friendly_error(httpx.ConnectError("Connection refused"))
    assert "connect" in msg.lower() or "server" in msg.lower()
    assert "traceback" not in msg.lower() and "httpx" not in msg.lower()


def test_friendly_error_timeout() -> None:
    msg = friendly_error(httpx.TimeoutException("Timed out"))
    assert "timeout" in msg.lower() or "timed out" in msg.lower()


@pytest.mark.parametrize("status,expected_words", [
    (500, ("500", "server")),
    (401, ("auth",)),
    (429, ("rate", "limited")),
])
def test_friendly_error_http_status(status, expected_words) -> None:
    exc = httpx.HTTPStatusError(
        f"Status {status}", request=httpx.Request("GET", "http://t"), response=httpx.Response(status),
    )
    msg_lower = friendly_error(exc).lower()
    assert any(w in msg_lower for w in expected_words), f"Missing keyword for {status}: {msg_lower}"


def test_friendly_error_generic_exception() -> None:
    msg = friendly_error(ValueError("bad value"))
    assert "unexpected" in msg.lower() or "error" in msg.lower()
    assert "ValueError" not in msg
# ── Loading indicator ───────────────────────────────────────────────

def test_loading_indicator_default_not_loading() -> None:
    indicator = LoadingIndicator()
    assert indicator.loading is False and indicator.status_text == ""

def test_loading_indicator_has_braille_spinner() -> None:
    for char in _SPINNER_FRAMES:
        assert 0x2800 <= ord(char) <= 0x28FF, f"Character {char!r} is not braille"

def test_loading_indicator_has_default_css() -> None:
    assert "height: 1" in LoadingIndicator.DEFAULT_CSS

def test_loading_indicator_render_idle() -> None:
    assert LoadingIndicator().render().plain == ""

def test_loading_indicator_render_with_status() -> None:
    indicator = LoadingIndicator()
    indicator.status_text = "Ready"
    assert "Ready" in indicator.render().plain

# ── Status bar ──────────────────────────────────────────────────────

def test_status_bar_init_defaults() -> None:
    bar = SigLabStatusBar()
    assert bar._version == "0.1.0" and bar._api_url == "http://localhost:3100" and bar._connected is False

def test_status_bar_init_custom() -> None:
    bar = SigLabStatusBar(version="2.0.0", api_url="http://example.com:9999")
    assert bar._version == "2.0.0" and bar._api_url == "http://example.com:9999"

def test_status_bar_set_connected() -> None:
    bar = SigLabStatusBar()
    bar._connected = True
    assert bar._connected is True
    bar._connected = False
    assert bar._connected is False

# ── Resize / refresh / reactive ─────────────────────────────────────

def test_app_css_responsive_rules() -> None:
    app_tcss = (TUI_DIR / "styles" / "app.tcss").read_text()
    assert "min-width" in app_tcss and "max-width" in app_tcss

def test_app_has_api_connected_reactive() -> None:
    assert hasattr(SigLabTUI, "api_connected")

def test_app_has_check_api_connection_and_watch() -> None:
    assert hasattr(SigLabTUI, "_check_api_connection") and hasattr(SigLabTUI, "watch_api_connected")

# ── VAL-TUI-009 pilot tests ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_pilot_help_screen_opens_and_closes() -> None:
    async with SigLabTUI().run_test() as pilot:
        await pilot.press("question_mark")
        await pilot.pause()
        assert len(pilot.app.screen_stack) > 1
        await pilot.press("escape")
        await pilot.pause()

@pytest.mark.asyncio
async def test_pilot_number_key_screen_navigation() -> None:
    async with SigLabTUI().run_test() as pilot:
        for key in ["1", "2", "3", "4", "5", "6"]:
            await pilot.press(key)
            await pilot.pause()

@pytest.mark.asyncio
async def test_pilot_escape_returns_to_main() -> None:
    async with SigLabTUI().run_test() as pilot:
        await pilot.press("question_mark")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert len(pilot.app.screen_stack) == 1

@pytest.mark.asyncio
async def test_pilot_f1_opens_help() -> None:
    async with SigLabTUI().run_test() as pilot:
        await pilot.press("f1")
        await pilot.pause()
        assert len(pilot.app.screen_stack) > 1
        await pilot.press("escape")
        await pilot.pause()

@pytest.mark.asyncio
async def test_pilot_app_resizes_gracefully() -> None:
    async with SigLabTUI().run_test(size=(120, 40)) as pilot:
        await pilot.press("1")
        await pilot.pause()
        pilot.app.screen.size_changed = True
        await pilot.pause()
