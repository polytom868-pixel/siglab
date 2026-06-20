"""Kill switch — emergency stop mechanisms for live trading.

Provides three independent kill-switch mechanisms:

1. **File-watch trigger** — when ``/tmp/siglab.KILL`` exists, all trading halts.
2. **SIGUSR1 signal handler** — graceful shutdown on demand.
3. **Daily loss threshold** — automatic halt at -5 % daily drawdown.
"""

from __future__ import annotations

import logging
import os
import signal
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

KILL_FILE = Path("/tmp/siglab.KILL")
"""Path consulted at runtime; halt when present."""

DAILY_LOSS_THRESHOLD: float = -0.05
"""Fractional daily loss that triggers automatic halt (-5 %)."""

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------

_kill_triggered: bool = False
"""Set to ``True`` by SIGUSR1, file-trigger, or daily-loss check."""


# ---------------------------------------------------------------------------
# Signal handler
# ---------------------------------------------------------------------------


def _sigusr1_handler(signum: int, _frame: Any | None) -> None:
    """SIGUSR1 handler — sets the global kill flag."""
    global _kill_triggered
    _kill_triggered = True
    logger.warning("SIGUSR1 received — kill switch engaged (signum=%s)", signum)


def _install_signal_handler() -> None:
    """Install the SIGUSR1 handler (safe to call multiple times).

    Silently no-ops when not on the main thread or when SIGUSR1 is
    unavailable on the current platform.
    """
    try:
        signal.signal(signal.SIGUSR1, _sigusr1_handler)
    except (ValueError, AttributeError):
        pass


# Install at import time so the handler is always registered.
_install_signal_handler()


# ---------------------------------------------------------------------------
# Kill-check helpers
# ---------------------------------------------------------------------------


def check_file_trigger() -> bool:
    """Return ``True`` if ``/tmp/siglab.KILL`` exists."""
    return KILL_FILE.exists()


def check_daily_loss(equity: float, start_equity: float) -> bool:
    """Check whether the daily loss threshold has been breached.

    Parameters
    ----------
    equity : float
        Current portfolio equity.
    start_equity : float
        Portfolio equity at the start of the trading day.

    Returns
    -------
    bool
        ``True`` when the daily return is at or below -5 %.
    """
    if start_equity <= 0.0:
        return False
    daily_return = (equity - start_equity) / start_equity
    return daily_return <= DAILY_LOSS_THRESHOLD


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def check_kill_switch(
    equity: float | None = None,
    start_equity: float | None = None,
) -> tuple[bool, str]:
    """Combined kill-switch check: file-trigger + signal + daily loss.

    All three mechanisms are checked in order.  The first triggered
    mechanism sets the global kill flag so subsequent calls return
    immediately.

    Parameters
    ----------
    equity : float, optional
        Current portfolio equity (required for the daily-loss check).
    start_equity : float, optional
        Start-of-day equity (required for the daily-loss check).

    Returns
    -------
    tuple[bool, str]
        ``(kill_engaged, reason)``.  When ``kill_engaged`` is ``True``,
        all trading should cease immediately.
    """
    global _kill_triggered

    # 1. File-watch trigger
    if check_file_trigger():
        _kill_triggered = True
        return True, f"Kill file present: {KILL_FILE}"

    # 2. Signal trigger
    if _kill_triggered:
        return True, "Kill switch engaged via SIGUSR1"

    # 3. Daily loss threshold
    if equity is not None and start_equity is not None:
        if check_daily_loss(equity, start_equity):
            _kill_triggered = True
            daily_return = (equity - start_equity) / start_equity
            return True, f"Daily loss threshold reached: {daily_return:.2%}"

    return False, ""


def reset_kill_switch() -> None:
    """Reset the kill switch — clear flags and remove the kill file."""
    global _kill_triggered
    _kill_triggered = False
    if KILL_FILE.exists():
        try:
            KILL_FILE.unlink()
        except OSError:
            pass
