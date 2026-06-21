"""Base screen class for the SigLab TUI."""
from __future__ import annotations
import logging
from typing import TYPE_CHECKING, Any, ClassVar, Coroutine
from textual.binding import Binding
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Input, Static
from siglab.tui.formatting import BORDER_DIM, TEXT_PRIMARY, friendly_error, safe_query
from siglab.tui.loading import LoadingIndicator
from rich.text import Text

def render_header(result: Text, text: str, width: int=50) -> None:
    """Append a styled "TITLE" + horizontal-rule header to ``result``."""
    result.append(f' {text}\n', style=f'bold {TEXT_PRIMARY}')
    result.append('─' * width + '\n', style=BORDER_DIM)
if TYPE_CHECKING:
    from siglab.tui.api_client import TuiApiClient
logger = logging.getLogger(__name__)

class BaseScreen(Screen[None]):
    """Base screen with common lifecycle, bindings, and loading state."""
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [Binding('escape', 'go_back', 'Back', show=True), Binding('r', 'refresh_now', 'Refresh', show=True), Binding('j', 'move_down', 'Down', show=False), Binding('k', 'move_up', 'Up', show=False), Binding('ctrl+c', 'go_back', 'Back', show=False), Binding('/', 'focus_search', 'Search', show=False), Binding('question_mark', 'app.show_help', 'Help', show=False)]
    is_loading: reactive[bool] = reactive(True)
    status_text: reactive[str] = reactive('Connecting…')
    _loading_widget_id: ClassVar[str] = ''
    _status_widget_id: ClassVar[str] = ''
    _refresh_interval: ClassVar[float] = 30.0
    _api_client_class: ClassVar[type | None] = None
    _api: TuiApiClient | None
    _search_input_id: ClassVar[str] = ''
    _search_list_id: ClassVar[str] = ''

    def __init__(self, *, api_client: TuiApiClient | None=None, **kwargs: Any) -> None:
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
        self._refresh_timer = self.set_interval(self._refresh_interval, self._refresh_all)
        self.call_after_refresh(self._refresh_all)

    async def on_unmount(self) -> None:
        """Clean up the refresh timer and owned API client."""
        if hasattr(self, '_refresh_timer'):
            self._refresh_timer.stop()
        if self._owns_api and self._api is not None:
            try:
                await self._api.close()
            except (AttributeError, TypeError):
                pass

    async def _refresh_all(self) -> None:
        """Fetch all data with loading state management."""
        self.is_loading = True
        self._set_loading(True)
        try:
            await self._fetch_data()
        except Exception as exc:
            self._update_status_error(friendly_error(exc))
            try:
                self.notify(friendly_error(exc), severity='error')
            except (AttributeError, TypeError):
                pass
            logger.warning('%s refresh failed: %s', self.__class__.__name__, exc)
        finally:
            self.is_loading = False
            self._set_loading(False)

    async def _fetch_data(self) -> None:
        """Override in subclass to fetch screen data."""

    async def _fetch_multiple(self, *fetch_fns: Coroutine[Any, Any, None], label: str='data') -> int:
        """Run multiple fetch coroutines with per-function error handling."""
        successes = 0
        for fn in fetch_fns:
            try:
                await fn
                successes += 1
            except Exception as exc:
                logger.debug('%s sub-fetch failed: %s', label, exc)
        return successes

    def _set_loading(self, loading: bool) -> None:
        if not self._loading_widget_id:
            return
        w = safe_query(self, self._loading_widget_id, LoadingIndicator)
        if w is not None:
            w.loading = loading
            if not loading:
                w.status_text = self.status_text

    def _update_status_text(self, text: str) -> None:
        self.status_text = text
        if self._status_widget_id:
            safe_query(self, self._status_widget_id, Static, lambda w: w.update(text))

    def _update_status_error(self, error: str) -> None:
        self._update_status_text(f'{error}  [r]etry')

    def action_focus_search(self) -> None:
        """Focus the search/filter input widget."""
        if self._search_input_id:
            safe_query(self, f'#{self._search_input_id}', Input, lambda w: w.focus())

    def _on_search_input_changed(self, event: Input.Changed) -> bool:
        if not self._search_input_id or not self._search_list_id:
            return False
        if event.input.id != self._search_input_id:
            return False
        from siglab.tui.widgets.base import FilterableListWidget
        safe_query(self, f'#{self._search_list_id}', FilterableListWidget, lambda w: w.set_filter(event.value))
        return True

    def action_go_back(self) -> None:
        """Return to the previous screen."""
        self.app.pop_screen()

    def action_refresh_now(self) -> None:
        """Force an immediate data refresh."""
        self.call_after_refresh(self._refresh_all)

    def action_move_up(self) -> None:
        """Move selection up in the primary list widget."""
        if self._search_list_id:
            from siglab.tui.widgets.base import FilterableListWidget
            lw = safe_query(self, f'#{self._search_list_id}', FilterableListWidget)
            if lw:
                lw.action_move_up()
                self._on_selection_changed()
                return
        self.screen.focus_previous()
        self._on_selection_changed()

    def action_move_down(self) -> None:
        """Move selection down in the primary list widget."""
        if self._search_list_id:
            from siglab.tui.widgets.base import FilterableListWidget
            lw = safe_query(self, f'#{self._search_list_id}', FilterableListWidget)
            if lw:
                lw.action_move_down()
                self._on_selection_changed()
                return
        self.screen.focus_next()
        self._on_selection_changed()

    def _on_selection_changed(self) -> None:
        """Hook called after ``action_move_up``/``action_move_down``."""