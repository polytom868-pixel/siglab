"""Kill switch — emergency stop mechanisms for live trading."""

from __future__ import annotations

import logging
import signal
from pathlib import Path
from typing import Any
import contextlib

logger = logging.getLogger(__name__)
KILL_FILE = Path("/tmp/siglab.KILL")
"Path consulted at runtime; halt when present."
DAILY_LOSS_THRESHOLD: float = -0.05
"Fractional daily loss that triggers automatic halt (-5 %)."
_kill_triggered: bool = False
"Set to ``True`` by SIGUSR1, file-trigger, or daily-loss check."


def _sigusr1_handler(signum: int, _frame: Any | None) -> None:
    """SIGUSR1 handler — sets the global kill flag."""
    global _kill_triggered
    _kill_triggered = True
    logger.warning("SIGUSR1 received — kill switch engaged (signum=%s)", signum)


def _install_signal_handler() -> None:
    with contextlib.suppress(ValueError, AttributeError):
        signal.signal(signal.SIGUSR1, _sigusr1_handler)


_install_signal_handler()


def check_file_trigger() -> bool:
    """Return ``True`` if ``/tmp/siglab.KILL`` exists."""
    return KILL_FILE.exists()


def check_daily_loss(equity: float, start_equity: float) -> bool:
    """Check whether the daily loss threshold has been breached."""
    if start_equity <= 0.0:
        return False
    daily_return = (equity - start_equity) / start_equity
    return daily_return <= DAILY_LOSS_THRESHOLD


def check_kill_switch(
    equity: float | None = None,
    start_equity: float | None = None,
) -> tuple[bool, str]:
    """Combined kill-switch check: file-trigger + signal + daily loss."""
    global _kill_triggered
    if check_file_trigger():
        _kill_triggered = True
        return (True, f"Kill file present: {KILL_FILE}")
    if _kill_triggered:
        return (True, "Kill switch engaged via SIGUSR1")
    if equity is not None and start_equity is not None:
        if check_daily_loss(equity, start_equity):
            _kill_triggered = True
            daily_return = (equity - start_equity) / start_equity
            return (True, f"Daily loss threshold reached: {daily_return:.2%}")
    return (False, "")


def reset_kill_switch() -> None:
    """Reset the kill switch — clear flags and remove the kill file."""
    global _kill_triggered
    _kill_triggered = False
    if KILL_FILE.exists():
        with contextlib.suppress(OSError):
            KILL_FILE.unlink()
