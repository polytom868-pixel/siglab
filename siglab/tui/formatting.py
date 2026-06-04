"""Shared formatting helpers and color constants for the SigLab TUI.

Centralizes color references and formatting functions to avoid duplication
across screen modules and ensure theme consistency.
"""

from __future__ import annotations

from rich.text import Text


# ── Color Constants ──────────────────────────────────────────────────
# Single source of truth for all Rich Text colors in TUI widgets.
# Keep in sync with siglab/tui/styles/theme.tcss variables.

ACCENT_GREEN = "#4ade80"
WARNING_YELLOW = "#f0b456"
ERROR_RED = "#f87171"
INFO_BLUE = "#60a5fa"
ACCENT_PURPLE = "#a78bfa"

TEXT_PRIMARY = "#e2ebe5"
TEXT_SECONDARY = "#a3b5a8"
TEXT_MUTED = "#7d9483"

BG = "#0a0a0a"
SURFACE = "#0d1210"
SURFACE_RAISED = "#162019"
BORDER_DIM = "#2a3a30"
INPUT_BG = "#1a2a1f"

# ── Semantic Aliases ─────────────────────────────────────────────────
# Use these to make intent clear in widget code.
GAIN = ACCENT_GREEN
LOSS = ERROR_RED
LINK = INFO_BLUE
CAUTION = WARNING_YELLOW


# ── Formatting Helpers ───────────────────────────────────────────────


def friendly_error(exc: Exception) -> str:
    """Convert an exception into a user-friendly error message.

    Avoids leaking Python internals (tracebacks, module paths) to the user.
    """
    import httpx

    if isinstance(exc, httpx.ConnectError):
        return "Cannot connect to API server"
    if isinstance(exc, httpx.TimeoutException):
        return "Request timed out"
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code == 401:
            return "Authentication failed"
        if code == 404:
            return "Endpoint not found"
        if code == 429:
            return "Rate limited — try again later"
        if code >= 500:
            return f"Server error ({code})"
        return f"HTTP error ({code})"
    if isinstance(exc, httpx.HTTPError):
        return "Network error"
    if isinstance(exc, json.JSONDecodeError):
        return "Invalid response format"
    if isinstance(exc, TimeoutError):
        return "Operation timed out"
    if isinstance(exc, ConnectionError):
        return "Connection lost"
    return "Unexpected error"


# Lazy import to avoid circular dependency at module level
import json  # noqa: E402


def format_price(price: float, symbol: str = "") -> str:
    """Format a price with appropriate decimal places."""
    if price >= 1000:
        return f"{price:,.2f}"
    elif price >= 1:
        return f"{price:,.4f}"
    else:
        return f"{price:,.6f}"


def format_change(pct: float) -> Text:
    """Format a percentage change as coloured Rich Text."""
    if pct > 0:
        return Text(f"\u25b2+{pct:.2f}%", style=ACCENT_GREEN)
    elif pct < 0:
        return Text(f"\u25bc{pct:.2f}%", style=ERROR_RED)
    else:
        return Text(f"\u2500\u2500 {pct:.2f}%", style=TEXT_MUTED)


def format_volume(vol: float) -> str:
    """Format volume in compact form (K, M, B)."""
    if vol >= 1_000_000_000:
        return f"{vol / 1_000_000_000:.1f}B"
    elif vol >= 1_000_000:
        return f"{vol / 1_000_000:.1f}M"
    elif vol >= 1_000:
        return f"{vol / 1_000:.1f}K"
    else:
        return f"{vol:.0f}"


def format_pnl(pnl: float) -> Text:
    """Format PnL as coloured Rich Text."""
    if pnl > 0:
        return Text(f"+{pnl:,.2f}", style=ACCENT_GREEN)
    elif pnl < 0:
        return Text(f"{pnl:,.2f}", style=ERROR_RED)
    else:
        return Text(f"{pnl:,.2f}", style=TEXT_MUTED)


def format_score(score: float | None) -> Text:
    """Format a score with gauge-style color coding."""
    if score is None:
        return Text("\u2500", style=TEXT_MUTED)
    if score != score:  # NaN
        return Text("NaN", style=TEXT_MUTED)
    if score >= 0.7:
        return Text(f"{score:.3f}", style=ACCENT_GREEN)
    elif score >= 0.4:
        return Text(f"{score:.3f}", style=WARNING_YELLOW)
    else:
        return Text(f"{score:.3f}", style=ERROR_RED)


def format_return(ret: float | None) -> Text:
    """Format a return percentage with color."""
    if ret is None:
        return Text("\u2500", style=TEXT_MUTED)
    if ret != ret:  # NaN
        return Text("NaN", style=TEXT_MUTED)
    if ret > 0:
        return Text(f"+{ret:.2f}%", style=ACCENT_GREEN)
    elif ret < 0:
        return Text(f"{ret:.2f}%", style=ERROR_RED)
    else:
        return Text(f"{ret:.2f}%", style=TEXT_MUTED)


def format_sharpe(sharpe: float | None) -> Text:
    """Format Sharpe ratio with color."""
    if sharpe is None:
        return Text("\u2500", style=TEXT_MUTED)
    if sharpe != sharpe:
        return Text("NaN", style=TEXT_MUTED)
    if sharpe >= 1.0:
        return Text(f"{sharpe:.2f}", style=ACCENT_GREEN)
    elif sharpe >= 0.5:
        return Text(f"{sharpe:.2f}", style=WARNING_YELLOW)
    else:
        return Text(f"{sharpe:.2f}", style=ERROR_RED)


def format_drawdown(dd: float | None) -> Text:
    """Format max drawdown with color."""
    if dd is None:
        return Text("\u2500", style=TEXT_MUTED)
    if dd != dd:
        return Text("NaN", style=TEXT_MUTED)
    abs_dd = abs(dd)
    if abs_dd > 20:
        return Text(f"{abs_dd:.1f}%", style=ERROR_RED)
    elif abs_dd > 10:
        return Text(f"{abs_dd:.1f}%", style=WARNING_YELLOW)
    else:
        return Text(f"{abs_dd:.1f}%", style=TEXT_MUTED)


def format_status(passed: bool | None, deployed: bool = False) -> Text:
    """Format pass/fail/deployed status."""
    if deployed:
        return Text("\u25b2", style=INFO_BLUE)
    if passed is None:
        return Text("\u00b7", style=TEXT_MUTED)
    if passed:
        return Text("\u25cf", style=ACCENT_GREEN)
    return Text("\u25cb", style=ERROR_RED)


def truncate(text: str, width: int) -> str:
    """Truncate text to width with ellipsis."""
    if len(text) <= width:
        return text
    return text[: width - 1] + "\u2026"


def format_date(date_str: str | None) -> str:
    """Format an ISO date string for compact display (MM-DD HH:MM)."""
    if not date_str:
        return "──"
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        return dt.strftime("%m-%d %H:%M")
    except (ValueError, TypeError):
        return date_str[:10] if len(date_str) >= 10 else date_str


def format_count(value: int | float | None) -> str:
    """Format a count with k/M suffix."""
    if value is None:
        return "─"
    v = float(value)
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    elif v >= 1_000:
        return f"{v / 1_000:.1f}k"
    else:
        return f"{v:.0f}"


def severity_color(severity: str) -> str:
    """Return color hex for alert severity level."""
    sev = severity.lower().strip()
    if sev == "critical":
        return ERROR_RED
    elif sev == "warning":
        return WARNING_YELLOW
    elif sev == "info":
        return INFO_BLUE
    return TEXT_MUTED


def confidence_color(confidence: str) -> str:
    """Return color hex for confidence level."""
    c = confidence.lower().strip()
    if c == "good":
        return ACCENT_GREEN
    elif c == "medium":
        return WARNING_YELLOW
    elif c == "poor":
        return ERROR_RED
    return TEXT_MUTED


def format_latency(ms: float | None) -> Text:
    """Format latency in milliseconds with color."""
    if ms is None:
        return Text("\u2500", style=TEXT_MUTED)
    if ms != ms:  # NaN
        return Text("NaN", style=TEXT_MUTED)
    if ms < 100:
        return Text(f"{ms:.0f}ms", style=ACCENT_GREEN)
    elif ms < 500:
        return Text(f"{ms:.0f}ms", style=WARNING_YELLOW)
    else:
        return Text(f"{ms:.0f}ms", style=ERROR_RED)


def safe_float(value: Any, default: float = 0.0) -> float:
    """Safely convert a value to float, returning *default* on failure.

    Handles ``None``, non-numeric strings, and ``NaN``.
    """
    try:
        result = float(value) if value is not None else default
        return default if result != result else result  # NaN guard
    except (ValueError, TypeError):
        return default


def widget_header(title: str) -> Text:
    """Render a standardised widget header.

    Consistent uppercase bold style used across all screen widgets.
    """
    return Text(f" {title.upper()}\n", style=f"bold {TEXT_PRIMARY}")


def section_divider(width: int = 40) -> Text:
    """Render a horizontal divider line with consistent style."""
    return Text("\u2500" * width + "\n", style=BORDER_DIM)


# Re-export Any for callers that import safe_float
from typing import Any  # noqa: E402
