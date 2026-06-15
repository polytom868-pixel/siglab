"""Shared loading indicator widget for the SigLab TUI.

Provides an animated spinner that cycles through Unicode braille characters
to give visual feedback during data fetches and long operations.
"""

from __future__ import annotations

from rich.text import Text
from textual.reactive import reactive
from textual.widgets import Static

from siglab.tui.formatting import ACCENT_GREEN, TEXT_MUTED

# Spinner frames (braille pattern cycling)
_SPINNER_FRAMES = "\u280b\u2819\u2839\u2838\u283c\u2834\u2826\u2827\u2807\u280f"


class LoadingIndicator(Static):
    """Animated spinner widget that shows activity during data fetches.

    Displays a cycling braille character when ``loading`` is True,
    and a static status text when idle.

    Usage::

        loading = LoadingIndicator(id="my-loading")
        loading.loading = True   # starts spinning
        loading.loading = False  # stops spinning, shows status
        loading.status_text = "Live \u00b7 refreshed"
    """

    loading: reactive[bool] = reactive(False)
    status_text: reactive[str] = reactive("")

    DEFAULT_CSS = """
    LoadingIndicator {
        height: 1;
        width: auto;
        min-width: 1;
        padding: 0 1;
        background: #0d1210;
        color: #7d9483;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._spinner_idx = 0
        self._timer = None

    def on_mount(self) -> None:
        """Start the spinner timer."""
        self._timer = self.set_interval(0.1, self._tick_spinner)

    def watch_loading(self, loading: bool) -> None:
        """Pause the spinner interval while idle to avoid needless repaints."""
        if self._timer is None:
            return
        if loading:
            self._timer.resume()
        else:
            self._timer.pause()

    def _tick_spinner(self) -> None:
        """Advance the spinner frame when loading."""
        if self.loading:
            self._spinner_idx = (self._spinner_idx + 1) % len(_SPINNER_FRAMES)
            self.refresh()

    def render(self) -> Text:
        """Render the spinner or status text."""
        if self.loading:
            frame = _SPINNER_FRAMES[self._spinner_idx]
            return Text(f" {frame} Loading\u2026", style=ACCENT_GREEN)
        if self.status_text:
            return Text(f" {self.status_text}", style=TEXT_MUTED)
        return Text("", style=TEXT_MUTED)
