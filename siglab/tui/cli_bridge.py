"""Async CLI bridge for the SigLab TUI.

Runs SigLab CLI commands as subprocesses and captures their output.
Uses Rich for formatting the captured output.
"""

from __future__ import annotations

import asyncio
import sys
from typing import NamedTuple

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax


class CliResult(NamedTuple):
    """Result of a CLI command execution.

    Attributes:
        returncode: Exit code of the process.
        stdout: Captured standard output text.
        stderr: Captured standard error text.
        command: The command that was run (for reference).
    """

    returncode: int
    stdout: str
    stderr: str
    command: str


def _find_python() -> str:
    """Find the Python executable (preferring the venv one)."""
    return sys.executable


async def run_cli(*args: str, timeout: float = 30.0) -> CliResult:
    """Run a SigLab CLI command and capture its output.

    Uses the same Python executable as the current process to ensure
    the venv is picked up correctly.

    Args:
        *args: CLI arguments (e.g., ``"--help"``, ``"profile"``, ``"--json"``).
        timeout: Timeout in seconds (default 30).

    Returns:
        A CliResult with returncode, stdout, stderr, and the command string.

    Raises:
        asyncio.TimeoutError: If the command exceeds the timeout.
    """
    python_exe = _find_python()
    cmd = [python_exe, "-m", "siglab.cli", *args]
    command_str = " ".join(cmd)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise

    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")

    return CliResult(
        returncode=proc.returncode or 0,
        stdout=stdout,
        stderr=stderr,
        command=command_str,
    )


def format_cli_output(result: CliResult, console: Console | None = None) -> str:
    """Format CLI output as Rich-styled text.

    Renders the command, return code, stdout, and stderr as Rich output.

    Args:
        result: The CliResult to format.
        console: Optional Console for Rich rendering. If None, creates a new one.

    Returns:
        A string with the rendered Rich output.
    """
    if console is None:
        console = Console(width=80)

    from io import StringIO

    buf = StringIO()
    local_console = Console(file=buf, width=80)

    label = (
        "[green]✓ Success[/]"
        if result.returncode == 0
        else f"[red]✗ Failed (exit {result.returncode})[/]"
    )

    if result.stdout.strip() and result.stderr.strip():
        # Both stdout and stderr
        syntax = Syntax(
            result.stdout.strip(),
            "text",
            theme="monokai",
            word_wrap=True,
        )
        panel = Panel(
            syntax,
            title=f"[bold]{result.command}[/]",
            subtitle=label,
            border_style="blue" if result.returncode == 0 else "red",
        )
        local_console.print(panel)
        # Also print stderr separately
        local_console.print(
            Panel(result.stderr.strip(), title="stderr", border_style="red")
        )
    elif result.stdout.strip():
        # Only stdout
        syntax = Syntax(result.stdout.strip(), "text", theme="monokai", word_wrap=True)
        panel = Panel(
            syntax,
            title=f"[bold]{result.command}[/]",
            subtitle=label,
            border_style="blue" if result.returncode == 0 else "red",
        )
        local_console.print(panel)
    elif result.stderr.strip():
        # Only stderr
        panel = Panel(
            result.stderr.strip(),
            title=f"[bold]{result.command}[/]",
            subtitle=label,
            border_style="red",
        )
        local_console.print(panel)
    else:
        # No output
        panel = Panel(
            "(no output)",
            title=f"[bold]{result.command}[/]",
            subtitle=label,
            border_style="blue" if result.returncode == 0 else "red",
        )
        local_console.print(panel)

    return buf.getvalue()


async def run_cli_help() -> CliResult:
    """Run ``python3 -m siglab.cli --help`` and return the result.

    Shorthand for ``run_cli("--help")``.
    """
    return await run_cli("--help")
