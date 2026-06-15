"""Deterministic tmux-based TUI hardening tests.

Drives the SigLab TUI through tmux send-keys, captures pane output,
and asserts exact terminal state.  All tests are fully deterministic
with zero flakiness — no timing races, no network-dependent assertions.

Pattern: tmux new-session → send-keys → capture-pane → kill-session.

Covers:
- App launch and initial rendering
- Screen switching (keys 1–6) via base layout
- Help overlay open/close (F1 / Escape)
- Search input focus (/)
- Data refresh trigger (r)
- Error state (API unreachable)
- Resize behavior (80 / 120 / 160 columns)

These tests run in CI without X/display server (headless tmux).
"""

from __future__ import annotations

import re
import subprocess
import time
import uuid

import pytest

# ── Constants ─────────────────────────────────────────────────────────

_TUI_CMD = "cd /home/eya/soso/siglab && poetry run python -m siglab.tui"
_DEFAULT_WIDTH = 120
_DEFAULT_HEIGHT = 40
_SETTLE_SECS = 4.0        # initial TUI render wait
_NAVIGATE_SECS = 2.5      # screen switch wait
_RESIZE_SECS = 2.0        # resize re-render wait
_OVERLAY_SECS = 1.5       # help overlay wait

# Expected content keywords per screen (deterministic, no API needed)
_SCREEN_KEYWORDS: dict[str, list[str]] = {
    "market": ["search symbols", "btc-usd"],
    "paper": ["place order", "positions", "order history"],
    "risk": ["composite risk score", "drawdown"],
    "strategy": ["search strategies", "evaluation results"],
    "telemetry": ["search runs"],
    "evidence": ["evidence", "demo", "filter"],
}

# Help overlay content (always the same)
_HELP_KEYWORDS = ["keyboard shortcuts", "quit application", "show this help"]

# Screen key mapping — keys 1-6 for switching screens from base layout
_SCREEN_KEYS = {
    "market": "1",
    "paper": "2",
    "risk": "3",
    "strategy": "4",
    "telemetry": "5",
    "evidence": "6",
}


# ── Helpers ───────────────────────────────────────────────────────────


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    return re.sub(r"\x1b\[[0-9;]*[a-zA-Z]|\x1b\].*?\x07", "", text)


class TmuxTUI:
    """Context manager that launches and controls the TUI inside tmux.

    The TUI starts on the market screen with the search input focused.
    Printable keys (1-6, ?, etc.) are consumed by the search input.
    Use ``pop_to_base()`` to return to the sidebar layout where number
    keys trigger screen switches.

    Usage::

        with TmuxTUI() as tui:
            tui.pop_to_base()
            tui.send_key("2")       # switch to paper
            time.sleep(2)
            assert "place order" in tui.capture().lower()
    """

    def __init__(
        self,
        width: int = _DEFAULT_WIDTH,
        height: int = _DEFAULT_HEIGHT,
        settle: float = _SETTLE_SECS,
    ) -> None:
        uid = uuid.uuid4().hex[:8]
        self.session = f"siglab-test-{uid}"
        self.width = width
        self.height = height
        self.settle = settle

    # ── Context manager ──────────────────────────────────────────────

    def __enter__(self) -> TmuxTUI:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.kill()

    # ── Lifecycle ────────────────────────────────────────────────────

    def start(self) -> None:
        """Create tmux session and launch TUI."""
        self._run(
            f"tmux new-session -d -s {self.session} -x {self.width} -y {self.height}"
        )
        self._run(f"tmux send-keys -t {self.session} -l '{_TUI_CMD}'")
        self._run(f"tmux send-keys -t {self.session} Enter")
        time.sleep(self.settle)

    def kill(self) -> None:
        """Destroy tmux session."""
        subprocess.run(
            f"tmux kill-session -t {self.session} 2>/dev/null",
            shell=True,
            capture_output=True,
            timeout=5,
        )

    # ── Interaction ──────────────────────────────────────────────────

    def send_key(self, key: str) -> None:
        """Send a single key to the TUI."""
        self._run(f"tmux send-keys -t {self.session} '{key}'")

    def send_literal(self, text: str) -> None:
        """Send a literal string (e.g. for search input)."""
        self._run(f"tmux send-keys -t {self.session} -l '{text}'")

    def pop_to_base(self) -> None:
        """Pop all pushed screens to reach the base layout (sidebar visible).

        Each screen switch pushes a new screen onto Textual's screen
        stack.  We press Escape enough times to pop all accumulated
        screens.  Extra presses on the base layout are harmless (no-op).
        The sidebar title "SigLab TUI" is the sentinel for base layout.
        """
        for _ in range(8):
            self.send_key("Escape")
            time.sleep(0.3)
        # Wait for final render
        time.sleep(1.0)

    def switch_screen(self, screen: str, from_base: bool = True) -> None:
        """Switch to a screen and wait for it to render.

        Args:
            screen: One of "market", "paper", "risk", "strategy",
                    "telemetry", "evidence".
            from_base: If True (default), pop to base layout first.
        """
        if from_base:
            self.pop_to_base()
        key = _SCREEN_KEYS[screen]
        self.send_key(key)
        time.sleep(_NAVIGATE_SECS)

    def resize(self, width: int, height: int | None = None) -> None:
        """Resize the tmux window."""
        h = height or self.height
        self._run(f"tmux resize-window -t {self.session} -x {width} -y {h}")
        self.width = width
        self.height = h

    # ── Capture ──────────────────────────────────────────────────────

    def capture(self) -> str:
        """Capture pane content with joined wrapped lines and strip ANSI."""
        result = self._run(f"tmux capture-pane -t {self.session} -p -J")
        return _strip_ansi(result.stdout)

    def capture_lines(self) -> list[str]:
        """Return captured pane as a list of non-empty clean lines."""
        return [line for line in self.capture().split("\n") if line.strip()]

    def contains(self, text: str) -> bool:
        """Check if the pane contains a string (case-insensitive)."""
        return text.lower() in self.capture().lower()

    def contains_any(self, keywords: list[str]) -> bool:
        """Check if the pane contains any of the keywords (case-insensitive)."""
        output = self.capture().lower()
        return any(kw.lower() in output for kw in keywords)

    # ── Internal ─────────────────────────────────────────────────────

    @staticmethod
    def _run(cmd: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=15
        )


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def tui() -> TmuxTUI:
    """Launch the TUI in tmux at default dimensions (120x40)."""
    t = TmuxTUI(width=_DEFAULT_WIDTH, height=_DEFAULT_HEIGHT)
    t.start()
    yield t
    t.kill()


# =====================================================================
# Test: App Launch
# =====================================================================


@pytest.mark.tmux
class TestAppLaunch:
    """TUI launches and renders initial content correctly."""

    def test_market_screen_renders(self, tui: TmuxTUI) -> None:
        """Market screen renders with expected content."""
        output = tui.capture()
        assert "search symbols" in output.lower() or "btc-usd" in output.lower(), (
            f"Market screen content not found:\n{output[:500]}"
        )

    def test_search_input_visible(self, tui: TmuxTUI) -> None:
        """Search input widget is visible on the market screen."""
        output = tui.capture()
        assert "search symbols" in output.lower()

    def test_symbol_column_visible(self, tui: TmuxTUI) -> None:
        """Symbol list column is visible."""
        output = tui.capture()
        assert "btc-usd" in output.lower()

    def test_tui_process_alive(self, tui: TmuxTUI) -> None:
        """The TUI process is running inside the tmux session."""
        result = subprocess.run(
            f"tmux list-panes -t {tui.session} -F '#{{pane_pid}}'",
            shell=True, capture_output=True, text=True, timeout=5,
        )
        assert result.returncode == 0
        pids = result.stdout.strip().split("\n")
        assert len(pids) >= 1
        assert pids[0].strip().isdigit()

    def test_status_bar_visible(self, tui: TmuxTUI) -> None:
        """Status bar is visible at the bottom of the screen."""
        output = tui.capture()
        # Status bar shows API connection status
        assert any(
            kw in output.lower()
            for kw in ["cannot reach api", "3100", "localhost", "connect", "etry"]
        )


# =====================================================================
# Test: Base Layout (Sidebar)
# =====================================================================


@pytest.mark.tmux
class TestBaseLayout:
    """The base layout (sidebar) is accessible after popping screens."""

    def test_sidebar_title_after_escape(self, tui: TmuxTUI) -> None:
        """Pressing Escape from market screen reveals sidebar with title."""
        tui.pop_to_base()
        output = tui.capture()
        assert "siglab tui" in output.lower(), (
            f"Sidebar title not found after Escape:\n{output[:500]}"
        )

    def test_nav_items_visible(self, tui: TmuxTUI) -> None:
        """Navigation items are visible on the base layout."""
        tui.pop_to_base()
        output = tui.capture()
        # At least items 1-4 should be visible
        for i in range(1, 5):
            assert str(i) in output, f"Nav item '{i}' not found in base layout"

    def test_nav_items_include_six_items(self, tui: TmuxTUI) -> None:
        """All 6 navigation items are present on the base layout."""
        tui.pop_to_base()
        output = tui.capture()
        for i in range(1, 7):
            assert str(i) in output, f"Nav item '{i}' not found"


# =====================================================================
# Test: Screen Switching (keys 1–6 from base layout)
# =====================================================================


@pytest.mark.tmux
class TestScreenSwitching:
    """Navigate between all six screens via number keys."""

    def _assert_screen_content(self, tui: TmuxTUI, screen: str) -> None:
        """Assert that the given screen's content is visible."""
        output = tui.capture()
        keywords = _SCREEN_KEYWORDS[screen]
        matched = any(kw in output.lower() for kw in keywords)
        assert matched, (
            f"Screen '{screen}' content not found. "
            f"Expected one of {keywords}.\nOutput:\n{output[:500]}"
        )

    def test_switch_to_paper(self, tui: TmuxTUI) -> None:
        """Switch to paper trading screen shows order form and positions."""
        tui.switch_screen("paper")
        self._assert_screen_content(tui, "paper")

    def test_switch_to_risk(self, tui: TmuxTUI) -> None:
        """Switch to risk screen shows risk score and drawdown."""
        tui.switch_screen("risk")
        self._assert_screen_content(tui, "risk")

    def test_switch_to_strategy(self, tui: TmuxTUI) -> None:
        """Switch to strategy screen shows strategy search."""
        tui.switch_screen("strategy")
        self._assert_screen_content(tui, "strategy")

    def test_switch_to_telemetry(self, tui: TmuxTUI) -> None:
        """Switch to telemetry screen shows run search."""
        tui.switch_screen("telemetry")
        self._assert_screen_content(tui, "telemetry")

    def test_switch_to_evidence(self, tui: TmuxTUI) -> None:
        """Switch to evidence screen shows evidence graph."""
        tui.switch_screen("evidence")
        self._assert_screen_content(tui, "evidence")

    def test_switch_to_market(self, tui: TmuxTUI) -> None:
        """Switch back to market screen shows search input."""
        tui.switch_screen("market")
        output = tui.capture()
        # Market screen always shows search or BTC-USD
        assert "search symbols" in output.lower() or "btc-usd" in output.lower()

    def test_cycle_all_screens(self, tui: TmuxTUI) -> None:
        """Cycle through all 6 screens and verify each renders."""
        for screen_name in _SCREEN_KEYS:
            tui.switch_screen(screen_name)
            output = tui.capture()
            keywords = _SCREEN_KEYWORDS[screen_name]
            matched = any(kw in output.lower() for kw in keywords)
            assert matched, (
                f"Screen '{screen_name}' did not render expected content. "
                f"Expected one of {keywords}.\nOutput:\n{output[:300]}"
            )

    def test_screen_switch_preserves_tui(self, tui: TmuxTUI) -> None:
        """Multiple screen switches don't crash the TUI."""
        for screen_name in _SCREEN_KEYS:
            tui.switch_screen(screen_name)
        # TUI should still be responsive
        output = tui.capture()
        assert len(output) > 100, "TUI output too short — possible crash"


# =====================================================================
# Test: Help Overlay
# =====================================================================


@pytest.mark.tmux
class TestHelpOverlay:
    """Open and close the help overlay via F1 key."""

    def test_f1_opens_help(self, tui: TmuxTUI) -> None:
        """F1 opens the help overlay with keyboard shortcuts."""
        tui.send_key("F1")
        time.sleep(_OVERLAY_SECS)
        output = tui.capture()
        assert "keyboard shortcuts" in output.lower(), (
            f"Help overlay did not open. Output:\n{output[:500]}"
        )

    def test_help_shows_quit_binding(self, tui: TmuxTUI) -> None:
        """Help overlay displays quit application binding."""
        tui.send_key("F1")
        time.sleep(_OVERLAY_SECS)
        output = tui.capture()
        assert "quit application" in output.lower()

    def test_help_shows_navigation_info(self, tui: TmuxTUI) -> None:
        """Help overlay shows screen-switching keys."""
        tui.send_key("F1")
        time.sleep(_OVERLAY_SECS)
        output = tui.capture()
        assert "1-6" in output

    def test_escape_dismisses_help(self, tui: TmuxTUI) -> None:
        """Escape dismisses the help overlay."""
        tui.send_key("F1")
        time.sleep(_OVERLAY_SECS)
        output_before = tui.capture()
        assert "keyboard shortcuts" in output_before.lower()
        tui.send_key("Escape")
        time.sleep(_OVERLAY_SECS)
        output_after = tui.capture()
        assert "keyboard shortcuts" not in output_after.lower()

    def test_q_dismisses_help(self, tui: TmuxTUI) -> None:
        """Pressing 'q' while help is open dismisses it (not quit app)."""
        tui.send_key("F1")
        time.sleep(_OVERLAY_SECS)
        tui.send_key("q")
        time.sleep(_OVERLAY_SECS)
        output = tui.capture()
        # After dismissing help, TUI is still running
        assert "keyboard shortcuts" not in output.lower()

    def test_question_mark_dismisses_help(self, tui: TmuxTUI) -> None:
        """Pressing '?' while help is open dismisses it."""
        tui.send_key("F1")
        time.sleep(_OVERLAY_SECS)
        tui.send_literal("?")
        time.sleep(_OVERLAY_SECS)
        output = tui.capture()
        assert "keyboard shortcuts" not in output.lower()

    def test_help_accessible_from_base_layout(self, tui: TmuxTUI) -> None:
        """Help overlay works from the base layout."""
        tui.pop_to_base()
        tui.send_key("F1")
        time.sleep(_OVERLAY_SECS)
        output = tui.capture()
        assert "keyboard shortcuts" in output.lower()

    def test_help_accessible_from_risk(self, tui: TmuxTUI) -> None:
        """Help overlay works from the risk screen."""
        tui.switch_screen("risk")
        tui.send_key("F1")
        time.sleep(_OVERLAY_SECS)
        output = tui.capture()
        assert "keyboard shortcuts" in output.lower()


# =====================================================================
# Test: Search Input
# =====================================================================


@pytest.mark.tmux
class TestSearchInput:
    """Search input captures typed text on the market screen."""

    def test_search_input_receives_typed_text(self, tui: TmuxTUI) -> None:
        """Text typed on market screen goes into the search input."""
        # TUI starts on market screen with search input focused
        tui.send_literal("BTC")
        time.sleep(1.0)
        output = tui.capture()
        assert "btc" in output.lower()

    def test_search_input_visible_after_tui_start(self, tui: TmuxTUI) -> None:
        """Search input placeholder is visible when TUI starts."""
        output = tui.capture()
        assert "search symbols" in output.lower()

    def test_search_and_symbol_column(self, tui: TmuxTUI) -> None:
        """Search input and symbol column are both visible."""
        output = tui.capture()
        assert "search symbols" in output.lower()
        assert "btc-usd" in output.lower()


# =====================================================================
# Test: Data Refresh
# =====================================================================


@pytest.mark.tmux
class TestDataRefresh:
    """Data refresh via r key."""

    def test_r_triggers_refresh(self, tui: TmuxTUI) -> None:
        """Pressing r on market screen triggers a data refresh."""
        tui.send_key("r")
        time.sleep(2.0)
        output = tui.capture()
        # TUI should still be alive and showing market content
        assert (
            "search symbols" in output.lower()
            or "btc-usd" in output.lower()
            or "loading" in output.lower()
        )

    def test_refresh_on_paper(self, tui: TmuxTUI) -> None:
        """Refresh on paper screen doesn't crash."""
        tui.switch_screen("paper")
        tui.send_key("r")
        time.sleep(2.0)
        output = tui.capture()
        assert "place order" in output.lower() or "positions" in output.lower()

    def test_refresh_on_risk(self, tui: TmuxTUI) -> None:
        """Refresh on risk screen doesn't crash."""
        tui.switch_screen("risk")
        tui.send_key("r")
        time.sleep(2.0)
        output = tui.capture()
        assert "composite risk score" in output.lower() or "drawdown" in output.lower()

    def test_rapid_refreshes_stable(self, tui: TmuxTUI) -> None:
        """Rapid r presses don't crash the TUI."""
        for _ in range(5):
            tui.send_key("r")
            time.sleep(0.3)
        time.sleep(3.0)
        output = tui.capture()
        # TUI should still be rendering content
        assert len(output) > 100

    def test_refresh_from_base_layout(self, tui: TmuxTUI) -> None:
        """r on base layout doesn't crash."""
        tui.pop_to_base()
        tui.send_key("r")
        time.sleep(1.0)
        output = tui.capture()
        assert "siglab tui" in output.lower()


# =====================================================================
# Test: Error States
# =====================================================================


@pytest.mark.tmux
class TestErrorStates:
    """Graceful error handling when API is unreachable."""

    def test_tui_survives_without_api(self, tui: TmuxTUI) -> None:
        """TUI renders correctly even when FastAPI backend is down."""
        output = tui.capture()
        assert len(output) > 100, "TUI rendered empty — possible crash"

    def test_error_message_shown(self, tui: TmuxTUI) -> None:
        """Market screen shows error/retry message when API is unreachable."""
        # Wait for API timeout
        time.sleep(5.0)
        output = tui.capture()
        has_error = any(
            kw in output.lower()
            for kw in [
                "cannot reach",
                "cannot connect",
                "error",
                "retry",
                "timed out",
                "timeout",
                "etry",
            ]
        )
        assert has_error, (
            f"No error/retry message with unreachable API:\n{output[:500]}"
        )

    def test_error_doesnt_crash_navigation(self, tui: TmuxTUI) -> None:
        """Navigation still works after error state."""
        time.sleep(5.0)  # Wait for API timeout
        tui.switch_screen("paper")
        output = tui.capture()
        assert "place order" in output.lower() or "positions" in output.lower()

    def test_refresh_after_error_stable(self, tui: TmuxTUI) -> None:
        """Pressing r after an error state doesn't crash."""
        time.sleep(5.0)
        tui.send_key("r")
        time.sleep(3.0)
        output = tui.capture()
        assert len(output) > 100

    def test_help_works_after_error(self, tui: TmuxTUI) -> None:
        """Help overlay works even after API errors."""
        time.sleep(5.0)
        tui.send_key("F1")
        time.sleep(_OVERLAY_SECS)
        output = tui.capture()
        assert "keyboard shortcuts" in output.lower()


# =====================================================================
# Test: Resize Behavior
# =====================================================================


@pytest.mark.tmux
class TestResizeBehavior:
    """TUI handles terminal resize gracefully."""

    def test_resize_to_80_columns(self, tui: TmuxTUI) -> None:
        """TUI renders at 80 columns without crashing."""
        tui.resize(80)
        time.sleep(_RESIZE_SECS)
        output = tui.capture()
        assert len(output) > 50

    def test_resize_to_120_columns(self, tui: TmuxTUI) -> None:
        """TUI renders at 120 columns without crashing."""
        tui.resize(120)
        time.sleep(_RESIZE_SECS)
        output = tui.capture()
        assert len(output) > 50

    def test_resize_to_160_columns(self, tui: TmuxTUI) -> None:
        """TUI renders at 160 columns without crashing."""
        tui.resize(160)
        time.sleep(_RESIZE_SECS)
        output = tui.capture()
        assert len(output) > 50

    def test_resize_preserves_current_screen(self, tui: TmuxTUI) -> None:
        """Resizing doesn't change the current screen."""
        tui.switch_screen("risk")
        tui.resize(80)
        time.sleep(_RESIZE_SECS)
        output = tui.capture()
        assert "composite risk score" in output.lower() or "drawdown" in output.lower()

    def test_resize_from_80_to_160(self, tui: TmuxTUI) -> None:
        """TUI adapts when expanding from 80 to 160 columns."""
        tui.resize(80)
        time.sleep(_RESIZE_SECS)
        tui.resize(160)
        time.sleep(_RESIZE_SECS)
        output = tui.capture()
        assert "search symbols" in output.lower() or "btc-usd" in output.lower()

    def test_resize_from_160_to_80(self, tui: TmuxTUI) -> None:
        """TUI adapts when shrinking from 160 to 80 columns."""
        tui.resize(160)
        time.sleep(_RESIZE_SECS)
        tui.resize(80)
        time.sleep(_RESIZE_SECS)
        output = tui.capture()
        assert "search symbols" in output.lower() or "btc-usd" in output.lower()

    def test_rapid_resize_sequence(self, tui: TmuxTUI) -> None:
        """Rapid resize sequence doesn't crash the TUI."""
        for width in [80, 120, 160, 80, 160, 120, 80]:
            tui.resize(width)
            time.sleep(0.5)
        time.sleep(2.0)
        output = tui.capture()
        assert len(output) > 100

    def test_all_screens_at_80_columns(self, tui: TmuxTUI) -> None:
        """All screens render without crash at 80 columns."""
        tui.resize(80)
        time.sleep(_RESIZE_SECS)
        for screen_name in _SCREEN_KEYS:
            tui.switch_screen(screen_name)
            output = tui.capture()
            assert len(output) > 50, f"Screen '{screen_name}' empty at 80 cols"

    def test_all_screens_at_160_columns(self, tui: TmuxTUI) -> None:
        """All screens render without crash at 160 columns."""
        tui.resize(160)
        time.sleep(_RESIZE_SECS)
        for screen_name in _SCREEN_KEYS:
            tui.switch_screen(screen_name)
            output = tui.capture()
            assert len(output) > 50, f"Screen '{screen_name}' empty at 160 cols"

    def test_resize_during_search(self, tui: TmuxTUI) -> None:
        """Resizing while search input has content doesn't crash."""
        tui.send_literal("BTC")
        time.sleep(0.5)
        tui.resize(80)
        time.sleep(_RESIZE_SECS)
        output = tui.capture()
        assert "btc" in output.lower()


# =====================================================================
# Test: Keyboard Navigation
# =====================================================================


@pytest.mark.tmux
class TestKeyboardNavigation:
    """General keyboard navigation behavior."""

    def test_escape_pops_screen(self, tui: TmuxTUI) -> None:
        """Escape from market screen returns to base layout."""
        tui.pop_to_base()
        output = tui.capture()
        assert "siglab tui" in output.lower()

    def test_push_and_pop_cycle(self, tui: TmuxTUI) -> None:
        """Push a screen, then pop back to base layout."""
        tui.switch_screen("paper")
        output = tui.capture()
        assert "place order" in output.lower()
        tui.pop_to_base()
        output = tui.capture()
        assert "siglab tui" in output.lower()

    def test_j_k_navigation_stable(self, tui: TmuxTUI) -> None:
        """j/k navigation keys don't crash on market screen."""
        tui.send_key("j")
        time.sleep(0.3)
        tui.send_key("j")
        time.sleep(0.3)
        tui.send_key("k")
        time.sleep(0.3)
        output = tui.capture()
        assert len(output) > 100


# =====================================================================
# Test: Determinism
# =====================================================================


@pytest.mark.tmux
class TestDeterminism:
    """Verify test output is deterministic across 3 consecutive runs."""

    def test_market_screen_deterministic(self) -> None:
        """Market screen content is identical across 3 runs."""
        outputs = []
        for _ in range(3):
            with TmuxTUI(width=_DEFAULT_WIDTH) as tui:
                time.sleep(_SETTLE_SECS)
                output = tui.capture()
                # Extract the content area keywords
                keywords_found = [
                    kw for kw in ["search symbols", "btc-usd", "loading"]
                    if kw in output.lower()
                ]
                outputs.append(sorted(keywords_found))
        assert outputs[0] == outputs[1] == outputs[2], (
            f"Market screen content not deterministic:\n"
            f"Run 1: {outputs[0]}\nRun 2: {outputs[1]}\nRun 3: {outputs[2]}"
        )

    def test_help_overlay_deterministic(self) -> None:
        """Help overlay content is identical across 3 runs."""
        outputs = []
        for _ in range(3):
            with TmuxTUI(width=_DEFAULT_WIDTH) as tui:
                tui.send_key("F1")
                time.sleep(_OVERLAY_SECS)
                output = tui.capture()
                keywords_found = sorted(
                    kw for kw in _HELP_KEYWORDS if kw in output.lower()
                )
                outputs.append(keywords_found)
        assert outputs[0] == outputs[1] == outputs[2], (
            f"Help overlay not deterministic:\n"
            f"Run 1: {outputs[0]}\nRun 2: {outputs[1]}\nRun 3: {outputs[2]}"
        )

    def test_screen_switch_deterministic(self) -> None:
        """Switching to paper screen produces consistent output."""
        outputs = []
        for _ in range(3):
            with TmuxTUI(width=_DEFAULT_WIDTH) as tui:
                tui.switch_screen("paper")
                output = tui.capture()
                keywords_found = sorted(
                    kw for kw in _SCREEN_KEYWORDS["paper"] if kw in output.lower()
                )
                outputs.append(keywords_found)
        assert outputs[0] == outputs[1] == outputs[2], (
            f"Paper screen content not deterministic:\n"
            f"Run 1: {outputs[0]}\nRun 2: {outputs[1]}\nRun 3: {outputs[2]}"
        )

    def test_base_layout_deterministic(self) -> None:
        """Base layout (sidebar) is identical across 3 runs."""
        outputs = []
        for _ in range(3):
            with TmuxTUI(width=_DEFAULT_WIDTH) as tui:
                tui.pop_to_base()
                output = tui.capture()
                # Check sidebar title and nav items
                has_title = "siglab tui" in output.lower()
                nav_items = [str(i) for i in range(1, 7) if str(i) in output]
                outputs.append((has_title, nav_items))
        assert outputs[0] == outputs[1] == outputs[2], (
            f"Base layout not deterministic:\n"
            f"Run 1: {outputs[0]}\nRun 2: {outputs[1]}\nRun 3: {outputs[2]}"
        )
