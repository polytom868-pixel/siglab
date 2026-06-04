"""SigLab TUI — Main application with navigation shell.

Provides a Textual-based terminal interface with a navigation sidebar,
content area, and status bar. Acts as the shell for all TUI screens.
"""

from __future__ import annotations

from typing import Callable, ClassVar

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.reactive import reactive
from textual.screen import ModalScreen, Screen
from textual.widgets import ListItem, ListView, Static

from siglab.tui.api_client import TuiApiClient
from siglab.tui.cli_bridge import run_cli_help
from siglab.tui.widgets import SigLabStatusBar
from siglab.tui.screens.market import MarketScreen
from siglab.tui.screens.paper import PaperScreen


# ── Navigation items ──────────────────────────────────────────────────

NAV_ITEMS: list[tuple[str, str, str]] = [
    ("1", "📊 Market", "market"),
    ("2", "💹 Paper Trade", "paper"),
    ("3", "🛡️ Risk", "risk"),
    ("4", "🔬 Strategy", "strategy"),
    ("5", "📡 Telemetry", "telemetry"),
    ("6", "📋 Evidence", "evidence"),
]

SCREEN_NAMES = {screen_id: label for _, label, screen_id in NAV_ITEMS}
SCREEN_IDS = {screen_id for _, _, screen_id in NAV_ITEMS}


# ── Placeholder screen used for screens not yet implemented ───────────


class PlaceholderScreen(Screen):
    """A placeholder screen showing that the feature is coming soon."""

    def compose(self) -> ComposeResult:
        yield Static(id="placeholder-screen")
        yield Static("🚧 Coming soon", id="placeholder-text")

    DEFAULT_CSS = """
    PlaceholderScreen {
        align: center middle;
        height: 100%;
        width: 100%;
    }
    #placeholder-text {
        color: #7d9483;
        text-style: italic;
    }
    """

    def __init__(self, screen_name: str, screen_id: str = "") -> None:
        super().__init__()
        self._screen_name = screen_name
        if screen_id:
            self.id = screen_id

    def on_mount(self) -> None:
        self.query_one("#placeholder-text", Static).update(
            f"🚧 {self._screen_name} — Coming soon"
        )


# ── Help Overlay ──────────────────────────────────────────────────────


class HelpScreen(ModalScreen[None]):
    """Overlay showing keyboard shortcuts."""

    DEFAULT_CSS = """
    HelpScreen {
        align: center middle;
        background: rgba(0, 0, 0, 0.85);
    }

    #help-dialog {
        width: 50;
        height: auto;
        padding: 1 2;
        background: #0d1210;
        border: solid #2a3a30;
    }

    #help-title {
        text-style: bold;
        color: #4ade80;
        margin: 0 0 1 0;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "dismiss", "Close"),
        Binding("q", "dismiss", "Close"),
        Binding("?", "dismiss", "Close"),
    ]

    KEYBINDINGS: ClassVar[list[tuple[str, str]]] = [
        ("1-6", "Switch to screen"),
        ("q / Ctrl+Q", "Quit application"),
        ("? / F1", "Show this help"),
        ("↑/↓ or k/j", "Navigate sidebar"),
        ("Enter", "Select sidebar item"),
        ("Escape", "Close dialog / go back"),
    ]

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("⌨ Keyboard Shortcuts", id="help-title"),
            *(self._render_binding(key, desc) for key, desc in self.KEYBINDINGS),
            Static(""),
            Static("Press Escape, q, or ? to close"),
            id="help-dialog",
        )

    @staticmethod
    def _render_binding(key: str, desc: str) -> Static:
        text = Text.assemble(
            (f"  {key:<20} ", "bold #60a5fa"),
            (desc, "#7d9483"),
        )
        return Static(text)


# ── Sidebar Widget ────────────────────────────────────────────────────


class NavSidebar(Static):
    """Vertical navigation sidebar with clickable items."""

    def compose(self) -> ComposeResult:
        yield Static(" SigLab TUI ", id="nav-title")
        yield ListView(*self._build_items(), id="nav-list")

    @staticmethod
    def _build_items() -> list[ListItem]:
        items: list[ListItem] = []
        for idx, label, _screen_id in NAV_ITEMS:
            item = ListItem(
                Static(f"  {idx}  {label}", classes="nav-item"),
                id=f"nav-{_screen_id}",
            )
            items.append(item)
        return items

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Handle sidebar item selection."""
        if event.item:
            item_id = event.item.id or ""
            screen_id = item_id.replace("nav-", "", 1)
            if screen_id in SCREEN_IDS:
                self.app.push_screen(screen_id)

    def highlight_item(self, screen_id: str) -> None:
        """Highlight the nav item corresponding to the given screen."""
        try:
            lv = self.app.query_one("#nav-list", ListView)
        except NoMatches:
            return
        for i, (_, _, sid) in enumerate(NAV_ITEMS):
            if sid == screen_id:
                lv.index = i
                break


# ── Main App ──────────────────────────────────────────────────────────


# Build the SCREENS dict at class definition time.
# Textual expects SCREENS values to be Screen subclasses or callables,
# not instances — it instantiates them lazily on first push_screen().
_BUILTIN_SCREENS: dict[str, Callable[[], Screen]] = {}

# Market screen — real implementation
_BUILTIN_SCREENS["market"] = MarketScreen

# Paper trading screen — real implementation
_BUILTIN_SCREENS["paper"] = PaperScreen

# Remaining screens — placeholders for now
for _idx, _label, _screen_id in NAV_ITEMS:
    if _screen_id in ("market", "paper"):
        continue  # already registered above
    _BUILTIN_SCREENS[_screen_id] = lambda _lbl=_label, _sid=_screen_id: (
        PlaceholderScreen(_lbl, screen_id=_sid)
    )


class SigLabTUI(App):
    """SigLab Terminal UI — main application class.

    Provides a navigation shell with sidebar, content area, and status bar.
    Screens are registered in the SCREENS dict and switched via
    ``push_screen()``.
    """

    TITLE = "SigLab"
    SUB_TITLE = "Terminal Dashboard"
    CSS_PATH = ["styles/theme.tcss", "styles/app.tcss"]

    # Register screens (placeholders for now, expanded in later features)
    SCREENS: ClassVar[dict[str, Callable[[], Screen]]] = _BUILTIN_SCREENS

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("q", "quit", "Quit", show=True),
        Binding("ctrl+q", "quit", "Quit", show=False),
        Binding("?", "show_help", "Help", show=True),
        Binding("f1", "show_help", "Help", show=False),
        Binding("escape", "go_back", "Back", show=False),
        Binding("1", "switch_to_market", "Market", show=False),
        Binding("2", "switch_to_paper", "Paper", show=False),
        Binding("3", "switch_to_risk", "Risk", show=False),
        Binding("4", "switch_to_strategy", "Strategy", show=False),
        Binding("5", "switch_to_telemetry", "Telemetry", show=False),
        Binding("6", "switch_to_evidence", "Evidence", show=False),
    ]

    # Reactive state
    api_connected: reactive[bool] = reactive(False)

    def __init__(self) -> None:
        super().__init__()
        self.api_client = TuiApiClient()

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield NavSidebar(id="nav-sidebar")
            yield Static(id="content-area")
        yield SigLabStatusBar(id="status-bar")

    def on_mount(self) -> None:
        """Initialize the app after mounting."""
        # Start on the first screen
        first_screen_id = NAV_ITEMS[0][2]
        self.push_screen(first_screen_id)

        # Check API connection on startup
        self.set_interval(15.0, self._check_api_connection)
        self.call_after_refresh(self._check_api_connection)

    async def on_unmount(self) -> None:
        """Clean up resources when the app is shutting down."""
        await self.api_client.close()

    async def _check_api_connection(self) -> None:
        """Check whether the FastAPI backend is reachable."""
        try:
            await self.api_client.get_health()
            self.api_connected = True
        except Exception as exc:
            self.api_connected = False
            self.log.debug(f"API connection check failed: {exc}")

    def watch_api_connected(self, connected: bool) -> None:
        """React to changes in API connection state."""
        try:
            status_bar = self.query_one("#status-bar", SigLabStatusBar)
            status_bar.set_connected(connected)
        except NoMatches:
            pass

    def action_show_help(self) -> None:
        """Show the help overlay."""
        self.push_screen(HelpScreen())

    def action_go_back(self) -> None:
        """Go back to the previous screen or dismiss current modal."""
        if len(self.screen_stack) > 1:
            self.pop_screen()

    def on_screen_resume(self) -> None:
        """Update sidebar highlight when a screen becomes active."""
        current = self.screen
        current_id = getattr(current, "id", "")
        if current_id in SCREEN_IDS:
            try:
                sidebar = self.query_one(NavSidebar)
                sidebar.highlight_item(current_id)
            except NoMatches:
                pass

    # ── Keyboard navigation shortcuts ──

    def action_switch_screen(self, screen_id: str) -> None:
        """Switch to a specific screen by its ID."""
        self.push_screen(screen_id)

    def action_switch_to_market(self) -> None:
        """Switch to the market screen (key 1)."""
        self.push_screen("market")

    def action_switch_to_paper(self) -> None:
        """Switch to the paper trading screen (key 2)."""
        self.push_screen("paper")

    def action_switch_to_risk(self) -> None:
        """Switch to the risk screen (key 3)."""
        self.push_screen("risk")

    def action_switch_to_strategy(self) -> None:
        """Switch to the strategy screen (key 4)."""
        self.push_screen("strategy")

    def action_switch_to_telemetry(self) -> None:
        """Switch to the telemetry screen (key 5)."""
        self.push_screen("telemetry")

    def action_switch_to_evidence(self) -> None:
        """Switch to the evidence screen (key 6)."""
        self.push_screen("evidence")

    async def action_run_cli_help(self) -> None:
        """Run the CLI help command and display in a debug panel."""
        try:
            result = await run_cli_help()
            self.notify(
                title=f"CLI — exit {result.returncode}",
                message=f"Found {len(result.stdout)} chars of output",
                timeout=3,
            )
        except Exception as exc:
            self.notify(
                title="CLI Error",
                message=str(exc),
                severity="error",
                timeout=5,
            )
