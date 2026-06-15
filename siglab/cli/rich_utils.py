"""Rich formatting utilities for the SigLab CLI.

Provides a shared console instance, semantic color helpers,
table/panel/progress factories, and JSON syntax highlighting.
Respects --no-color flag and NO_COLOR env var.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from rich.console import Console
from rich.json import JSON
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table
from rich.text import Text
from rich.theme import Theme


# ── Semantic color theme ─────────────────────────────────────────────────
SIGLAB_THEME = Theme(
    {
        "success": "bold green",
        "error": "bold red",
        "warning": "bold yellow",
        "info": "bold blue",
        "muted": "dim",
        "accent": "bold cyan",
        "label": "bold",
        "value": "",
    }
)


def make_console(*, force_no_color: bool = False) -> Console:
    """Build a Rich Console respecting NO_COLOR and --no-color.

    Args:
        force_no_color: When True, disables all ANSI styling regardless
                        of environment.  Set from the parsed --no-color flag.

    Returns:
        A themed Rich Console instance.
    """
    no_color = force_no_color or bool(os.environ.get("NO_COLOR"))
    is_tty = sys.stdout.isatty()
    return Console(
        theme=SIGLAB_THEME,
        no_color=no_color,
        highlight=not no_color and is_tty,
        stderr=False,
        force_terminal=is_tty if not no_color else False,
        force_jupyter=False,
    )


# Module-level default console (replaced at CLI startup after arg parse)
_console: Console | None = None


def get_console() -> Console:
    """Return the active console.  Falls back to a default if not initialized."""
    global _console
    if _console is None:
        _console = make_console()
    return _console


def init_console(*, force_no_color: bool = False) -> Console:
    """Initialize the module-level console.  Called once from main()."""
    global _console
    _console = make_console(force_no_color=force_no_color)
    return _console


# ── JSON output ──────────────────────────────────────────────────────────


def print_json(data: Any, *, indent: int = 2, sort_keys: bool = True) -> None:
    """Print JSON with syntax highlighting in terminal, plain JSON when piped/no_color.

    Uses plain json.dumps (no ANSI) when:
    - stdout is not a terminal (piped)
    - NO_COLOR env var is set
    - --no-color flag was passed
    Otherwise uses Rich JSON syntax highlighting.
    """
    console = get_console()
    # Use plain JSON when: no_color set, NO_COLOR env, not a TTY, or piped
    no_color = console.no_color or bool(os.environ.get("NO_COLOR")) or not sys.stdout.isatty()
    if no_color:
        print(json.dumps(data, indent=indent, sort_keys=sort_keys, default=str))
        return
    json_obj = JSON.from_data(data, indent=indent, sort_keys=sort_keys, default=str)
    console.print(json_obj)


# ── Table factory ────────────────────────────────────────────────────────


def make_table(
    title: str | None = None,
    *,
    show_lines: bool = False,
    header_style: str = "bold",
    border_style: str = "muted",
    row_styles: tuple[str, ...] = ("", "dim"),
) -> Table:
    """Create a consistently styled Rich Table."""
    return Table(
        title=title,
        show_lines=show_lines,
        header_style=header_style,
        border_style=border_style,
        row_styles=row_styles,
        expand=False,
    )


def print_key_value_pairs(
    title: str | None,
    pairs: list[tuple[str, str, str]],
) -> None:
    """Render key-value pairs as a table.

    Each pair is (label, value, style) where style is a Rich style name
    applied to the value cell (e.g. "success", "error", "warning").
    """
    table = make_table(title=title)
    table.add_column("Field", style="label", no_wrap=True)
    table.add_column("Value")
    for label, value, style in pairs:
        table.add_row(label, Text(value, style=style))
    console = get_console()
    console.print(table)


# ── Panel factory ────────────────────────────────────────────────────────


def print_panel(
    content: str | Text,
    title: str | None = None,
    *,
    border_style: str = "info",
    expand: bool = False,
) -> None:
    """Print content in a Rich Panel."""
    console = get_console()
    console.print(Panel(content, title=title, border_style=border_style, expand=expand))


def print_status_line(message: str, *, style: str = "info") -> None:
    """Print a single styled status line (replaces bare print of status text)."""
    console = get_console()
    console.print(Text(message, style=style))


# ── Progress bar factory ─────────────────────────────────────────────────


def make_progress(**kwargs: Any) -> Progress:
    """Create a consistently styled progress bar for long operations.

    Default columns: spinner, description, bar, M/N, elapsed time.
    """
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=get_console(),
        **kwargs,
    )


# ── Semantic print helpers ───────────────────────────────────────────────


_PRINT_ICONS = {"success": "✔", "error": "✘", "warning": "⚠", "info": "ℹ"}


def _print_styled(message: str, style: str, *, icon: bool = True) -> None:
    """Print a styled message with an optional icon (✔/✘/⚠/ℹ) by style name."""
    prefix = f"[{style}]{_PRINT_ICONS[style]}[/] " if icon and style in _PRINT_ICONS else ""
    get_console().print(f"{prefix}{message}")


def print_success(message: str) -> None:
    """Print a success message with green checkmark."""
    _print_styled(message, "success")


def print_error(message: str) -> None:
    """Print an error message with red cross."""
    _print_styled(message, "error")


def print_warning(message: str) -> None:
    """Print a warning message with yellow warning sign."""
    _print_styled(message, "warning")


def print_info(message: str) -> None:
    """Print an informational message with blue info sign."""
    _print_styled(message, "info")


def print_header(title: str) -> None:
    """Print a section header with a horizontal rule."""
    console = get_console()
    console.rule(f"[bold]{title}")


def print_muted(message: str) -> None:
    """Print a dimmed/muted message."""
    console = get_console()
    console.print(f"[muted]{message}[/]")


# ── Status style mapper ──────────────────────────────────────────────────


def status_style(value: Any) -> str:
    """Return a Rich style name for a boolean-ish status value."""
    if isinstance(value, bool):
        return "success" if value else "error"
    s = str(value).strip().upper()
    if s in {"TRUE", "READY", "PASS", "1"}:
        return "success"
    if s in {"FALSE", "NOT READY", "FAIL", "0"}:
        return "error"
    if s in {"PARTIAL", "BLOCKED"}:
        return "warning"
    return ""
