"""SigLab TUI — Main application with navigation shell."""

from __future__ import annotations

from typing import Any, Callable, ClassVar, cast

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.reactive import reactive
from textual.screen import ModalScreen, Screen
from textual.widgets import ListItem, ListView, Static

from siglab.tui.api_client import TuiApiClient
from siglab.tui.formatting import INFO_BLUE, TEXT_MUTED
from siglab.tui.screens.evidence import EvidenceScreen, MarketScreen
from siglab.tui.screens.paper import PaperScreen, RiskScreen
from siglab.tui.widgets import SigLabStatusBar

NAV_ITEMS: list[tuple[str, str, str]] = [
    ("1", "[ MARKET ]", "market"),
    ("2", "[ PAPER  ]", "paper"),
    ("3", "[ RISK   ]", "risk"),
    ("4", "[ EVIDENCE ]", "evidence"),
]
SCREEN_NAMES = {screen_id: label for _, label, screen_id in NAV_ITEMS}
SCREEN_IDS = {screen_id for _, _, screen_id in NAV_ITEMS}


class PlaceholderScreen(Screen[Any]):
    """A placeholder screen showing that the feature is coming soon."""

    def compose(self) -> ComposeResult:
        yield Static(id="placeholder-screen")
        yield Static("Coming soon", id="placeholder-text")

    DEFAULT_CSS = "PlaceholderScreen { align: center middle; height: 100%; width: 100%; } color: #7d9483; text-style: italic; }"

    def __init__(self, screen_name: str, screen_id: str = "") -> None:
        super().__init__()
        self._screen_name = screen_name
        if screen_id:
            self.id = screen_id

    def on_mount(self) -> None:
        self.query_one("#placeholder-text", Static).update(
            f"{self._screen_name} — Coming soon"
        )


class HelpScreen(ModalScreen[None]):
    """Overlay showing keyboard shortcuts (global + per-screen)."""

    DEFAULT_CSS = "HelpScreen { align: center middle; background: rgba(0, 0, 0, 0.85); } width: 56; max-width: 90%; height: auto; max-height: 80%; padding: 1 2; background: #0d1210; border: solid #2a3a30; overflow-y: auto; } text-style: bold; color: #4ade80; margin: 0 0 1 0; }"
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("escape", "dismiss", "Close"),
        Binding("q", "dismiss", "Close"),
        Binding("question_mark", "dismiss", "Close"),
    ]
    GLOBAL_KEYBINDINGS: ClassVar[list[tuple[str, str]]] = [
        ("1-4", "Switch to screen"),
        ("q / Ctrl+Q / Ctrl+C", "Quit application"),
        ("? / F1", "Show this help"),
        ("k/j", "Navigate lists"),
        ("Enter", "Select / confirm"),
        ("Escape", "Close dialog / go back"),
        ("r", "Refresh current screen"),
        ("/", "Focus search/filter"),
    ]
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
            ("c", "Cancel order"),
            ("r", "Refresh"),
        ],
        "risk": [
            ("r", "Refresh data"),
            ("j/k", "Scroll alerts"),
            ("f", "Cycle alert filter"),
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
            return f"⌨ Keyboard Shortcuts — {self._screen_name}"
        return "⌨ Keyboard Shortcuts"

    def _render_screen_section(self) -> list[Static]:
        bindings = self.SCREEN_KEYBINDINGS.get(self._screen_id, [])
        if not bindings:
            return []
        items: list[Static] = []
        items.append(Static(""))
        items.append(Static(f"  — {self._screen_name or 'Screen'} Shortcuts —"))
        for key, desc in bindings:
            items.append(self._render_binding(key, desc))
        return items

    @staticmethod
    def _render_binding(key: str, desc: str) -> Static:
        text = Text.assemble((f"  {key:<24} ", f"bold {INFO_BLUE}"), (desc, TEXT_MUTED))
        return Static(text)


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
                Static(f"  {idx}  {label}", classes="nav-item"), id=f"nav-{_screen_id}"
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


_BUILTIN_SCREENS: dict[str, Callable[[], Screen[Any]]] = {
    "market": MarketScreen,
    "paper": PaperScreen,
    "risk": RiskScreen,
    "evidence": EvidenceScreen,
}
for _idx, _label, _screen_id in NAV_ITEMS:
    if _screen_id in _BUILTIN_SCREENS:
        continue

    def _make_placeholder(_lbl: str = _label, _sid: str = _screen_id) -> Screen[Any]:
        return cast(Screen[Any], PlaceholderScreen(_lbl, screen_id=_sid))

    _BUILTIN_SCREENS[_screen_id] = _make_placeholder


class SigLabTUI(App[None]):
    """SigLab Terminal UI — main application class."""

    TITLE = "SigLab"
    SUB_TITLE = "Terminal Dashboard"
    CSS_PATH = ["styles/app.tcss"]
    SCREENS: ClassVar[dict[str, Callable[[], Screen[Any]]]] = _BUILTIN_SCREENS
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("q", "quit", "Quit", show=True),
        Binding("ctrl+q", "quit", "Quit", show=False),
        Binding("ctrl+c", "quit", "Quit", show=False),
        Binding("?", "show_help", "Help", show=True),
        Binding("f1", "show_help", "Help", show=False),
        Binding("escape", "go_back", "Back", show=False),
        Binding("1", "go_to_screen('market')", "Market", show=True),
        Binding("2", "go_to_screen('paper')", "Paper", show=True),
        Binding("3", "go_to_screen('risk')", "Risk", show=True),
        Binding("4", "go_to_screen('evidence')", "Evidence", show=False),
    ]
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
        first_screen_id = NAV_ITEMS[0][2]
        self.push_screen(first_screen_id)
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

    def _switch(self, screen_id: str) -> None:
        self.switch_screen(screen_id)

    def action_switch_to_market(self) -> None:
        self._switch("market")

    def action_switch_to_paper(self) -> None:
        self._switch("paper")

    def action_switch_to_risk(self) -> None:
        self._switch("risk")

    def action_switch_to_evidence(self) -> None:
        self._switch("evidence")


if __name__ == "__main__":
    app = SigLabTUI()
    app.run()
