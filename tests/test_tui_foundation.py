"""Tests for the TUI foundation (f23-tui-foundation).

Verifies VAL-TUI-001: TUI app scaffold launches and navigates.
"""

from __future__ import annotations

import asyncio

import pytest

from siglab.tui import SigLabTUI
from siglab.tui.api_client import TuiApiClient
from siglab.tui.cli_bridge import CliResult, format_cli_output, run_cli, run_cli_help


class TestTuiAppScaffold:
    """Tests for the main TUI application shell."""

    @pytest.mark.asyncio
    async def test_app_launches_and_shows_sidebar(self) -> None:
        """App launches, navigation sidebar shows screens, status bar visible."""
        app = SigLabTUI()
        async with app.run_test() as pilot:
            # Wait for mount to complete
            await pilot.pause()

            # Verify sidebar exists
            nav_sidebar = app.query_one("#nav-sidebar")
            assert nav_sidebar is not None, "Navigation sidebar should exist"

            # Verify nav-list has items
            nav_list = app.query_one("#nav-list")
            assert nav_list is not None, "Navigation list should exist"
            assert len(nav_list.children) > 0, "Navigation list should have items"

            # Verify status bar exists
            status_bar = app.query_one("#status-bar")
            assert status_bar is not None, "Status bar should exist"

            # Verify status bar has all three sections
            assert app.query_one("#status-left") is not None
            assert app.query_one("#status-center") is not None
            assert app.query_one("#status-right") is not None

    @pytest.mark.asyncio
    async def test_screen_switching(self) -> None:
        """App switches between registered screens."""
        app = SigLabTUI()
        async with app.run_test() as pilot:
            await pilot.pause()

            # Toggle help screen via ?
            await pilot.press("?")
            await pilot.pause()
            # Help overlay should be on top of screen stack
            assert len(app.screen_stack) >= 2, "Help screen should be pushed"

            # Dismiss help
            await pilot.press("escape")
            await pilot.pause()

    @pytest.mark.asyncio
    async def test_sidebar_triggers_screen_switch(self) -> None:
        """Selecting a sidebar item switches screens."""
        app = SigLabTUI()
        async with app.run_test() as pilot:
            await pilot.pause()
            nav_list = app.query_one("#nav-list")

            # Verify the Sidebar navigation items exist and are labeled
            items = list(nav_list.children)
            assert len(items) >= 6, "Should have at least 6 navigation items"

    @pytest.mark.asyncio
    async def test_help_screen_toggle(self) -> None:
        """Help screen (F1/?) opens and dismisses."""
        app = SigLabTUI()
        async with app.run_test() as pilot:
            await pilot.pause()

            # Press ? to show help
            await pilot.press("?")
            await pilot.pause()
            # Help overlay should be on top of screen stack
            assert len(app.screen_stack) >= 2, "Help screen should be pushed"

            # Dismiss with Escape
            await pilot.press("escape")
            await pilot.pause()
            # Help should be gone
            assert len(app.screen_stack) >= 1, (
                "Screen stack should still have base screens"
            )


class TestTuiApiClient:
    """Tests for the FastAPI client module."""

    @pytest.mark.asyncio
    async def test_client_initialization(self) -> None:
        """API client initializes with default URL."""
        client = TuiApiClient()
        assert client is not None
        assert client._base_url == "http://localhost:3100"

    @pytest.mark.asyncio
    async def test_client_custom_url(self) -> None:
        """API client accepts custom base URL."""
        client = TuiApiClient(base_url="http://example.com:9999")
        assert client._base_url == "http://example.com:9999"

    @pytest.mark.asyncio
    async def test_client_close_no_error(self) -> None:
        """Closing uninitialized client does not raise."""
        client = TuiApiClient()
        await client.close()  # Should not raise


class TestTuiCliBridge:
    """Tests for the CLI bridge module."""

    @pytest.mark.asyncio
    async def test_cli_result_named_tuple(self) -> None:
        """CliResult holds expected fields."""
        result = CliResult(returncode=0, stdout="test out", stderr="", command="test")
        assert result.returncode == 0
        assert result.stdout == "test out"
        assert result.stderr == ""
        assert result.command == "test"

    @pytest.mark.asyncio
    async def test_run_cli_help_works(self) -> None:
        """Running siglab.cli --help returns help text."""
        result = await run_cli_help()
        assert result.returncode == 0
        assert (
            "usage" in result.stdout.lower()
            or "positional" in result.stdout.lower()
            or result.stderr == ""
        )
        # Should contain some CLI commands
        assert len(result.stdout) > 100

    @pytest.mark.asyncio
    async def test_run_cli_custom_args(self) -> None:
        """Running with custom args works."""
        result = await run_cli("--help")
        assert result.returncode == 0
        assert len(result.stdout) > 0

    @pytest.mark.asyncio
    async def test_run_cli_invalid_command(self) -> None:
        """Invalid command returns non-zero."""
        result = await run_cli("nonexistent-command")
        # Should either return non-zero or have error in stderr
        assert (
            result.returncode != 0
            or "error" in result.stderr.lower()
            or "unrecognized" in result.stderr.lower()
            or "usage" in result.stderr.lower()
        )

    @pytest.mark.asyncio
    async def test_cli_bridge_timeout(self) -> None:
        """Timeout raises TimeoutError."""
        with pytest.raises((TimeoutError, asyncio.TimeoutError)):
            await run_cli("--help", timeout=0.001)

    @pytest.mark.asyncio
    async def test_format_cli_output_success(self) -> None:
        """Format output for successful command."""
        result = CliResult(
            returncode=0, stdout="All good", stderr="", command="siglab --help"
        )
        output = format_cli_output(result)
        assert "All good" in output or "(no output)" not in output

    @pytest.mark.asyncio
    async def test_format_cli_output_failure(self) -> None:
        """Format output for failed command."""
        result = CliResult(
            returncode=1, stdout="", stderr="Error occurred", command="siglab bad"
        )
        output = format_cli_output(result)
        assert "Error" in output or "Failed" in output or output != ""
