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
from siglab.tui.formatting import INFO_BLUE, TEXT_MUTED
from siglab.tui.widgets import SigLabStatusBar
from siglab.tui.screens.market import MarketScreen
from siglab.tui.screens.paper import PaperScreen
from siglab.tui.screens.risk import RiskScreen
from siglab.tui.screens.strategy import StrategyScreen
from siglab.tui.screens.telemetry import TelemetryScreen
from siglab.tui.screens.evidence import EvidenceScreen


# ── Navigation items ──────────────────────────────────────────────────

NAV_ITEMS: list[tuple[str, str, str]] = [
    ("1", "[ MARKET ]", "market"),
    ("2", "[ PAPER  ]", "paper"),
    ("3", "[ RISK   ]", "risk"),
    ("4", "[ STRAT  ]", "strategy"),
    ("5", "[ TELE   ]", "telemetry"),
    ("6", "[ EVID   ]", "evidence"),
]

SCREEN_NAMES = {screen_id: label for _, label, screen_id in NAV_ITEMS}
SCREEN_IDS = {screen_id for _, _, screen_id in NAV_ITEMS}


# ── Placeholder screen used for screens not yet implemented ───────────


class PlaceholderScreen(Screen):
    """A placeholder screen showing that the feature is coming soon."""

    def compose(self) -> ComposeResult:
        yield Static(id="placeholder-screen")
        yield Static("Coming soon", id="placeholder-text")

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
            f"{self._screen_name} — Coming soon"
        )


# ── Help Overlay ──────────────────────────────────────────────────────


class HelpScreen(ModalScreen[None]):
    """Overlay showing keyboard shortcuts (global + per-screen)."""

    DEFAULT_CSS = """
    HelpScreen {
        align: center middle;
        background: rgba(0, 0, 0, 0.85);
    }

    #help-dialog {
        width: 56;
        height: auto;
        max-height: 80%;
        padding: 1 2;
        background: $surface;
        border: solid $border-dim;
        overflow-y: auto;
    }

    #help-title {
        text-style: bold;
        color: $accent-green;
        margin: 0 0 1 0;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "dismiss", "Close"),
        Binding("q", "dismiss", "Close"),
        Binding("question_mark", "dismiss", "Close"),
    ]

    # Global keyboard shortcuts (always shown)
    GLOBAL_KEYBINDINGS: ClassVar[list[tuple[str, str]]] = [
        ("1-6", "Switch to screen"),
        ("q / Ctrl+Q / Ctrl+C", "Quit application"),
        ("? / F1", "Show this help"),
        ("k/j", "Navigate lists"),
        ("Enter", "Select / confirm"),
        ("Escape", "Close dialog / go back"),
        ("r", "Refresh current screen"),
        ("/", "Focus search/filter"),
    ]

    # Per-screen keyboard shortcuts
    SCREEN_KEYBINDINGS: ClassVar[dict[str, list[tuple[str, str]]]] = {
        "market": [
            ("j/k", "Navigate symbol list"),
            ("/", "Search symbols"),
            ("Enter", "Select symbol"),
            ("r", "Refresh data"),
        ],
        "paper": [
            ("s", "Set symbol"),
            ("b", "Toggle buy/sell"),
            ("t", "Toggle market/limit"),
            ("Q", "Set quantity"),
            ("p", "Set price"),
            ("Enter", "Submit order"),
            ("n", "New session"),
            ("r", "Refresh"),
        ],
        "risk": [
            ("r", "Refresh data"),
            ("j/k", "Scroll alerts"),
            ("f", "Cycle alert filter"),
        ],
        "strategy": [
            ("j/k", "Navigate strategies"),
            ("/", "Search strategies"),
            ("Space", "Toggle select"),
            ("c", "Toggle comparison"),
            ("e", "Run evaluation"),
            ("i", "Initialize deck"),
            ("s", "Cycle sort column"),
        ],
        "telemetry": [
            ("j/k", "Navigate runs"),
            ("/", "Search runs"),
            ("Space", "Toggle select"),
            ("c", "Toggle comparison"),
            ("d", "Cycle date range"),
            ("f", "Cycle status filter"),
            ("t", "Cycle track filter"),
            ("v", "Toggle view"),
        ],
        "evidence": [
            ("/", "Filter evidence"),
            ("Tab", "Switch pane"),
            ("Enter", "Run demo step"),
            ("n/p", "Next/prev step"),
            ("a", "Run all steps"),
            ("f", "Filter by source"),
        ],
    }

    def __init__(self, screen_name: str = "", screen_id: str = "") -> None:
        super().__init__()
        self._screen_name = screen_name
        self._screen_id = screen_id

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(self._title_text(), id="help-title"),
            *(self._render_binding(key, desc) for key, desc in self.GLOBAL_KEYBINDINGS),
            *self._render_screen_section(),
            Static(""),
            Static("Press Escape, q, or ? to close"),
            id="help-dialog",
        )

    def _title_text(self) -> str:
        if self._screen_name:
            return f"\u2328 Keyboard Shortcuts \u2014 {self._screen_name}"
        return "\u2328 Keyboard Shortcuts"

    def _render_screen_section(self) -> list[Static]:
        """Render per-screen shortcuts section."""
        bindings = self.SCREEN_KEYBINDINGS.get(self._screen_id, [])
        if not bindings:
            return []
        items: list[Static] = []
        items.append(Static(""))
        items.append(Static(f"  \u2014 {self._screen_name or 'Screen'} Shortcuts \u2014", style=f"bold {INFO_BLUE}"))
        for key, desc in bindings:
            items.append(self._render_binding(key, desc))
        return items

    @staticmethod
    def _render_binding(key: str, desc: str) -> Static:
        text = Text.assemble(
            (f"  {key:<24} ", f"bold {INFO_BLUE}"),
            (desc, TEXT_MUTED),
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

# Risk monitoring screen — real implementation
_BUILTIN_SCREENS["risk"] = RiskScreen

# Strategy research screen — real implementation
_BUILTIN_SCREENS["strategy"] = StrategyScreen

# Telemetry screen — real implementation
_BUILTIN_SCREENS["telemetry"] = TelemetryScreen

# Evidence screen — real implementation
_BUILTIN_SCREENS["evidence"] = EvidenceScreen

# Remaining screens — placeholders for now
for _idx, _label, _screen_id in NAV_ITEMS:
    if _screen_id in ("market", "paper", "risk", "strategy", "telemetry", "evidence"):
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
    CSS_PATH = ["styles/theme.tcss", "styles/app.tcss", "styles/market.tcss", "styles/paper.tcss", "styles/risk.tcss", "styles/strategy.tcss", "styles/telemetry.tcss", "styles/evidence.tcss"]

    # Register screens (placeholders for now, expanded in later features)
    SCREENS: ClassVar[dict[str, Callable[[], Screen]]] = _BUILTIN_SCREENS

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("q", "quit", "Quit", show=True),
        Binding("ctrl+q", "quit", "Quit", show=False),
        Binding("ctrl+c", "quit", "Quit", show=False),
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
        """Show the help overlay with context for the current screen."""
        current = self.screen
        screen_id = getattr(current, "id", "")
        screen_name = SCREEN_NAMES.get(screen_id, "")
        self.push_screen(HelpScreen(screen_name=screen_name, screen_id=screen_id))

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
