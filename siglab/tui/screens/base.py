"""Base screen class for the SigLab TUI.

Provides common lifecycle, bindings, loading state, status bar
management, optional API client ownership, and a declarative
search/filter contract shared by all screens.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, ClassVar, Coroutine

from textual.binding import Binding
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Input, Static

from siglab.tui.formatting import (
    BORDER_DIM,
    TEXT_PRIMARY,
    friendly_error,
    safe_query,
)
from siglab.tui.loading import LoadingIndicator

from rich.text import Text


def render_header(result: Text, text: str, width: int = 50) -> None:
    """Append a styled "TITLE" + horizontal-rule header to ``result``.

    Centralizes the pattern shared by telemetry widgets (and similar
    Static subclasses): a bold uppercase title followed by a
    ``BORDER_DIM`` rule.  Mutates ``result`` in place — no allocation.
    """
    result.append(f" {text}\n", style=f"bold {TEXT_PRIMARY}")
    result.append("\u2500" * width + "\n", style=BORDER_DIM)

if TYPE_CHECKING:
    from siglab.tui.api_client import TuiApiClient

logger = logging.getLogger(__name__)


class BaseScreen(Screen[None]):
    """Base screen with common lifecycle, bindings, and loading state.

    Provides:
    - 7 common BINDINGS (escape, r, j, k, ctrl+c, /, ?)
    - ``is_loading`` / ``status_text`` reactives
    - Timer-based auto-refresh with configurable interval
    - LoadingIndicator management
    - Error-friendly status updates
    - Optional API client ownership (set ``_api_client_class`` or pass ``api_client``)
    - Declarative search/filter contract (set ``_search_input_id`` + ``_search_list_id``)
    - ``action_go_back``, ``action_refresh_now``, ``action_move_up/down``,
      ``action_focus_search``

    Subclasses implement ``_fetch_data()`` for the actual data loading.
    """

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("escape", "go_back", "Back", show=True),
        Binding("r", "refresh_now", "Refresh", show=True),
        Binding("j", "move_down", "Down", show=False),
        Binding("k", "move_up", "Up", show=False),
        Binding("ctrl+c", "go_back", "Back", show=False),
        Binding("/", "focus_search", "Search", show=False),
        Binding("question_mark", "app.show_help", "Help", show=False),
    ]

    is_loading: reactive[bool] = reactive(True)
    status_text: reactive[str] = reactive("Connecting…")

    # Subclasses override these
    _loading_widget_id: ClassVar[str] = ""
    _status_widget_id: ClassVar[str] = ""
    _refresh_interval: ClassVar[float] = 30.0

    # ── API client management ────────────────────────────────────────
    # Set ``_api_client_class`` in subclasses that use TuiApiClient.
    # The base class creates an instance if none is passed via the
    # constructor and closes it on unmount.
    _api_client_class: ClassVar[type | None] = None
    _api: TuiApiClient | None

    # ── Search/filter contract ───────────────────────────────────────
    # Set these in subclasses to get automatic search wiring:
    #   _search_input_id  — widget id of the Input (e.g. "symbol-search")
    #   _search_list_id   — widget id of the FilterableListWidget
    # The base class provides ``action_focus_search`` and a default
    # ``on_input_changed`` that delegates ``set_filter`` to the list.
    _search_input_id: ClassVar[str] = ""
    _search_list_id: ClassVar[str] = ""

    def __init__(
        self, *, api_client: TuiApiClient | None = None, **kwargs: Any
    ) -> None:
        super().__init__(**kwargs)
        if api_client is not None:
            self._api = api_client
            self._owns_api = False
        elif self._api_client_class is not None:
            self._api = self._api_client_class()
            self._owns_api = True
        else:
            self._api = None
            self._owns_api = False

    def on_mount(self) -> None:
        """Start auto-refresh timer and trigger initial data load."""
        self._refresh_timer = self.set_interval(
            self._refresh_interval, self._refresh_all
        )
        self.call_after_refresh(self._refresh_all)

    async def on_unmount(self) -> None:
        """Clean up the refresh timer and owned API client."""
        if hasattr(self, "_refresh_timer"):
            self._refresh_timer.stop()
        if self._owns_api and self._api is not None:
            try:
                await self._api.close()
            except Exception:
                pass

    # ── Data fetching skeleton ───────────────────────────────────────

    async def _refresh_all(self) -> None:
        """Fetch all data with loading state management."""
        self.is_loading = True
        self._set_loading(True)
        try:
            await self._fetch_data()
        except Exception as exc:
            self._update_status_error(friendly_error(exc))
            try:
                self.notify(friendly_error(exc), severity="error")
            except Exception:
                pass  # No active app context (e.g., in tests)
            logger.warning("%s refresh failed: %s", self.__class__.__name__, exc)
        finally:
            self.is_loading = False
            self._set_loading(False)

    async def _fetch_data(self) -> None:
        """Override in subclass to fetch screen data."""

    async def _fetch_multiple(
        self, *fetch_fns: Coroutine[Any, Any, None], label: str = "data"
    ) -> int:
        """Run multiple fetch coroutines with per-function error handling.

        Returns the number of successful fetches.  On partial failure
        the status bar is updated with a partial-update message.
        """
        successes = 0
        for fn in fetch_fns:
            try:
                await fn
                successes += 1
            except Exception as exc:
                logger.debug("%s sub-fetch failed: %s", label, exc)
        return successes

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

    def _update_status_error(self, error: str) -> None:
        """Update status bar with a friendly error message and retry hint."""
        self._update_status_text(f"{error}  [r]etry")

    # ── Search / filter support ──────────────────────────────────────

    def action_focus_search(self) -> None:
        """Focus the search/filter input widget.

        Subclasses that set ``_search_input_id`` get this for free.
        Screens with non-standard search widgets should override.
        """
        if self._search_input_id:
            safe_query(
                self, f"#{self._search_input_id}", Input, lambda w: w.focus()
            )

    def _on_search_input_changed(self, event: Input.Changed) -> bool:
        """Handle a search Input.Changed event for the declarative contract.

        Returns ``True`` if the event was consumed by the base-class
        search wiring, ``False`` if the subclass should handle it.
        """
        if not self._search_input_id or not self._search_list_id:
            return False
        if event.input.id != self._search_input_id:
            return False
        # Delegate to the FilterableListWidget
        from siglab.tui.widgets.base import FilterableListWidget

        safe_query(
            self,
            f"#{self._search_list_id}",
            FilterableListWidget,
            lambda w: w.set_filter(event.value),
        )
        return True

    # ── Common actions ───────────────────────────────────────────────

    def action_go_back(self) -> None:
        """Return to the previous screen."""
        self.app.pop_screen()

    def action_refresh_now(self) -> None:
        """Force an immediate data refresh."""
        self.call_after_refresh(self._refresh_all)

    def action_move_up(self) -> None:
        """Move selection up in the primary list widget.

        If ``_search_list_id`` is set, delegates to the list widget's
        ``action_move_up``.  Otherwise falls back to focus traversal.
        After moving, calls ``_on_selection_changed`` so subclasses can
        update detail panels.
        """
        if self._search_list_id:
            from siglab.tui.widgets.base import FilterableListWidget

            lw = safe_query(self, f"#{self._search_list_id}", FilterableListWidget)
            if lw:
                lw.action_move_up()
                self._on_selection_changed()
                return
        self.screen.focus_previous()
        self._on_selection_changed()

    def action_move_down(self) -> None:
        """Move selection down in the primary list widget.

        If ``_search_list_id`` is set, delegates to the list widget's
        ``action_move_down``.  Otherwise falls back to focus traversal.
        After moving, calls ``_on_selection_changed`` so subclasses can
        update detail panels.
        """
        if self._search_list_id:
            from siglab.tui.widgets.base import FilterableListWidget

            lw = safe_query(self, f"#{self._search_list_id}", FilterableListWidget)
            if lw:
                lw.action_move_down()
                self._on_selection_changed()
                return
        self.screen.focus_next()
        self._on_selection_changed()

    def _on_selection_changed(self) -> None:
        """Hook called after ``action_move_up``/``action_move_down``.

        Subclasses override this to update detail panels when the
        primary list selection changes.  Default is a no-op so
        screens without a detail panel don't need to override.
        """
