"""Async CLI bridge for the SigLab TUI.

Runs SigLab CLI commands as subprocesses and captures their output.
Uses Rich for formatting the captured output.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any, NamedTuple


def parse_rows_from_json(stdout: str) -> list[dict[str, Any]]:
    """Parse a JSON document into a list of row dicts.

    The ``ancestry`` and similar commands return either a top-level
    list or an object with ``"rows"`` / ``"experiments"`` keys.
    """
    data = json.loads(stdout)
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    rows: list[dict[str, Any]] = data.get("rows", data.get("experiments", []))
    return [r for r in rows if isinstance(r, dict)]

MAX_COMPARE: int = 4

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



