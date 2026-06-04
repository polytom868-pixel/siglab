"""Base screen class for the SigLab TUI.

Provides common lifecycle, bindings, loading state, and status bar
management shared by all screens.
"""

from __future__ import annotations

import logging
from typing import ClassVar

from textual.binding import Binding
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Static

from siglab.tui.formatting import friendly_error
from siglab.tui.loading import LoadingIndicator

logger = logging.getLogger(__name__)


class BaseScreen(Screen[None]):
    """Base screen with common lifecycle, bindings, and loading state.

    Provides:
    - 7 common BINDINGS (escape, r, j, k, ctrl+c, ?)
    - ``is_loading`` / ``status_text`` reactives
    - Timer-based auto-refresh with configurable interval
    - LoadingIndicator management
    - Error-friendly status updates
    - ``action_go_back``, ``action_refresh_now``, ``action_move_up/down``

    Subclasses implement ``_fetch_data()`` for the actual data loading.
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "go_back", "Back", show=True),
        Binding("r", "refresh_now", "Refresh", show=True),
        Binding("j", "move_down", "Down", show=False),
        Binding("k", "move_up", "Up", show=False),
        Binding("ctrl+c", "go_back", "Back", show=False),
        Binding("question_mark", "app.show_help", "Help", show=False),
    ]

    is_loading: reactive[bool] = reactive(True)
    status_text: reactive[str] = reactive("Connecting…")

    # Subclasses override these
    _loading_widget_id: ClassVar[str] = ""
    _status_widget_id: ClassVar[str] = ""
    _refresh_interval: ClassVar[float] = 30.0

    def on_mount(self) -> None:
        """Start auto-refresh timer and trigger initial data load."""
        self._refresh_timer = self.set_interval(
            self._refresh_interval, self._refresh_all
        )
        self.call_after_refresh(self._refresh_all)

    async def on_unmount(self) -> None:
        """Clean up the refresh timer."""
        if hasattr(self, "_refresh_timer"):
            self._refresh_timer.stop()

    # ── Data fetching skeleton ───────────────────────────────────────

    async def _refresh_all(self) -> None:
        """Fetch all data with loading state management."""
        self.is_loading = True
        self._set_loading(True)
        try:
            await self._fetch_data()
        except Exception as exc:
            self._update_status_text(f"{friendly_error(exc)}  [r]etry")
            try:
                self.notify(friendly_error(exc), severity="error")
            except Exception:
                pass  # No active app context (e.g., in tests)
            logger.warning("%s refresh failed: %s", self.__class__.__name__, exc)
        finally:
            self.is_loading = False
            self._set_loading(False)

    async def _fetch_data(self) -> None:
        """Override to implement the actual data fetching."""
        raise NotImplementedError

    # ── Loading indicator management ─────────────────────────────────

    def _set_loading(self, loading: bool) -> None:
        """Toggle the LoadingIndicator widget."""
        if not self._loading_widget_id:
            return
        try:
            w = self.query_one(self._loading_widget_id, LoadingIndicator)
            w.loading = loading
            if not loading:
                w.status_text = self.status_text
        except Exception:
            pass

    def _update_status_text(self, text: str) -> None:
        """Update status_text reactive and the status widget."""
        self.status_text = text
        if self._status_widget_id:
            try:
                self.query_one(self._status_widget_id, Static).update(text)
            except Exception:
                pass

    # ── Common actions ───────────────────────────────────────────────

    def action_go_back(self) -> None:
        """Return to the previous screen."""
        self.app.pop_screen()

    def action_refresh_now(self) -> None:
        """Force an immediate data refresh."""
        self.call_after_refresh(self._refresh_all)

    def action_move_up(self) -> None:
        """Move focus to the previous widget."""
        self.screen.focus_previous()

    def action_move_down(self) -> None:
        """Move focus to the next widget."""
        self.screen.focus_next()
