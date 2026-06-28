from __future__ import annotations

import json
import os
import sys


_SIGLAB_THEME_STYLES = {
    "success": "bold green",
    "error": "bold red",
    "warning": "bold yellow",
    "info": "bold blue",
    "muted": "dim",
    "accent": "bold cyan",
    "label": "bold",
    "value": "",
}


def make_console(*, force_no_color: bool = False) -> Console:
    """Build a Rich Console respecting NO_COLOR and --no-color."""
    from rich.console import Console
    from rich.theme import Theme

    no_color = force_no_color or bool(os.environ.get("NO_COLOR"))
    is_tty = sys.stdout.isatty()
    return Console(
        theme=Theme(_SIGLAB_THEME_STYLES),
        no_color=no_color,
        highlight=not no_color and is_tty,
        stderr=False,
        force_terminal=is_tty if not no_color else False,
        force_jupyter=False,
    )
_console: Console | None = None


def get_console() -> Console:
    global _console
    if _console is None:
        _console = make_console()
    return _console


def init_console(*, force_no_color: bool = False) -> Console:
    """Initialize the module-level console. Called once from main()."""
    global _console
    _console = make_console(force_no_color=force_no_color)
    return _console


def print_json(data: object, *, indent: int = 2, sort_keys: bool = True) -> None:
    """Print JSON with syntax highlighting in terminal, plain JSON when piped/no_color."""
    from rich.json import JSON

    console = get_console()
    no_color = (
        console.no_color
        or bool(os.environ.get("NO_COLOR"))
        or (not sys.stdout.isatty())
    )
    if no_color:
        print(json.dumps(data, indent=indent, sort_keys=sort_keys, default=str))
        return
    json_obj = JSON.from_data(data, indent=indent, sort_keys=sort_keys, default=str)
    console.print(json_obj)


def make_table(
    title: str | None = None,
    *,
    show_lines: bool = False,
    header_style: str = "bold",
    border_style: str = "muted",
    row_styles: tuple[str, ...] = ("", "dim"),
) -> Table:
    from rich.table import Table

    return Table(
        title=title,
        show_lines=show_lines,
        header_style=header_style,
        border_style=border_style,
        row_styles=row_styles,
        expand=False,
    )


def print_status_line(message: str, *, style: str = "info") -> None:
    """Print a single styled status line (replaces bare print of status text)."""
    from rich.text import Text

    console = get_console()
    console.print(Text(message, style=style))


_PRINT_ICONS = {"success": "✔", "error": "✘", "warning": "⚠", "info": "ℹ"}


def _print_styled(message: str, style: str, *, icon: bool = True) -> None:
    prefix = (
        f"[{style}]{_PRINT_ICONS[style]}[/] " if icon and style in _PRINT_ICONS else ""
    )
    get_console().print(f"{prefix}{message}")


def print_success(message: str) -> None:
    _print_styled(message, "success")


def print_error(message: str) -> None:
    _print_styled(message, "error")


def print_warning(message: str) -> None:
    _print_styled(message, "warning")


def print_info(message: str) -> None:
    _print_styled(message, "info")


def status_style(value: object) -> str:
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
