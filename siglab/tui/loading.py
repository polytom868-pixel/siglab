"""Shared loading indicator widget for the SigLab TUI."""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.reactive import reactive
from textual.timer import Timer
from textual.widgets import Static

from siglab.tui.formatting import ACCENT_GREEN, TEXT_MUTED

_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class LoadingIndicator(Static):
    """Animated spinner widget that shows activity during data fetches."""

    loading: reactive[bool] = reactive(False)
    status_text: reactive[str] = reactive("")
    DEFAULT_CSS = "LoadingIndicator { height: 1; width: auto; min-width: 1; padding: 0 1; background: #0d1210; color: #7d9483; }"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._timer: Timer | None = None
        self._spinner_idx: int = 0

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
        if self.loading:
            self._spinner_idx = (self._spinner_idx + 1) % len(_SPINNER_FRAMES)
            self.refresh()

    def render(self) -> Text:
        """Render the spinner or status text."""
        if self.loading:
            frame = _SPINNER_FRAMES[self._spinner_idx]
            return Text(f" {frame} Loading…", style=ACCENT_GREEN)
        if self.status_text:
            return Text(f" {self.status_text}", style=TEXT_MUTED)
        return Text("", style=TEXT_MUTED)
