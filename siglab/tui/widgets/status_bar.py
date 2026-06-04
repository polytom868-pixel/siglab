"""Status bar widget for the SigLab TUI.

Displays version info, connection status, and current time.
"""

from __future__ import annotations

from datetime import UTC, datetime

from rich.text import Text
from textual.app import ComposeResult
from textual.widgets import Static
from textual.widget import Widget


class SigLabStatusBar(Widget):
    """A status bar showing connection state, version, and current time.

    Composed of several ``Static`` widgets arranged in a horizontal bar.
    """

    DEFAULT_CSS = """
    SigLabStatusBar {
        layout: horizontal;
        height: 1;
        background: #0d1210;
        color: #7d9483;
    }

    SigLabStatusBar > .status-item {
        padding: 0 1;
        height: 1;
    }

    SigLabStatusBar > .status-left {
        width: 1fr;
        content-align: left top;
    }

    SigLabStatusBar > .status-right {
        width: 1fr;
        content-align: right top;
    }

    SigLabStatusBar > .status-center {
        width: 1fr;
        content-align: center top;
    }
    """

    def __init__(
        self,
        version: str = "0.1.0",
        api_url: str = "http://localhost:3100",
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._version = version
        self._api_url = api_url
        self._connected = False

    def compose(self) -> ComposeResult:
        yield Static(id="status-left", classes="status-item status-left")
        yield Static(id="status-center", classes="status-item status-center")
        yield Static(id="status-right", classes="status-item status-right")

    def on_mount(self) -> None:
        self._update_display()
        self.set_interval(1.0, self._update_display)

    def _update_display(self) -> None:
        now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        conn_icon = "●" if self._connected else "○"
        conn_color = "green" if self._connected else "red"

        left = Text.assemble(
            (f" SigLab v{self._version} ", "bold"),
            (f"[{conn_icon}] ", conn_color),
            (f"{self._api_url}", "dim"),
        )

        center = Text("", style="dim")

        right = Text(now, style="dim")

        self.query_one("#status-left", Static).update(left)
        self.query_one("#status-center", Static).update(center)
        self.query_one("#status-right", Static).update(right)

    def set_connected(self, connected: bool) -> None:
        """Update the connection status indicator.

        Args:
            connected: Whether the API client is connected.
        """
        self._connected = connected
        self._update_display()
