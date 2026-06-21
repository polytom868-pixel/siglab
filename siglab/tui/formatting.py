"""Shared formatting helpers and color constants for the SigLab TUI."""

from __future__ import annotations

import json
from typing import Any, Callable, Protocol

from rich.text import Text
from siglab.utils import safe_float

__all__ = ["safe_float"]
# Single source of truth for all Rich Text colors in TUI widgets.
# Keep in sync with siglab/tui/styles/theme.tcss variables.


class _Queryable(Protocol):
    """Any Textual DOM node with a query_one method."""
    def query_one(self, widget_id: str, widget_type: type[Any]) -> Any: ...


ACCENT_GREEN = "#4ade80"
WARNING_YELLOW = "#f0b456"
ERROR_RED = "#f87171"
INFO_BLUE = "#60a5fa"
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
    """Convert an exception into a user-friendly error message."""
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



def truncate(text: str, width: int) -> str:
    """Truncate text to width with ellipsis."""
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width == 1:
        return "\u2026"
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



def format_confidence(conf: float | None) -> "Text":
    """Format a numeric confidence value (0.0–1.0) as coloured Rich Text."""
    if conf is None:
        return Text("\u2500", style=TEXT_MUTED)
    label = f"{conf:.0%}"
    if conf >= 0.8:
        return Text(label, style=ACCENT_GREEN)
    elif conf >= 0.5:
        return Text(label, style=WARNING_YELLOW)
    else:
        return Text(label, style=ERROR_RED)



# ── Status / Side / Gauge Style Helpers ──────────────────────────────


def status_style(passed: bool | None, deployed: bool = False) -> tuple[str, str]:
    """Return ``(dot_char, color_hex)`` for a pass/fail/deployed status."""
    if deployed:
        return ("\u25b2", INFO_BLUE)
    if passed is None:
        return ("\u00b7", TEXT_MUTED)
    if passed:
        return ("\u25cf", ACCENT_GREEN)
    return ("\u25cb", ERROR_RED)


def side_style(side: str) -> str:
    """Return colour hex for a BUY/SELL side label."""
    return ACCENT_GREEN if side.upper() == "BUY" else ERROR_RED


def order_status_style(status: str) -> str:
    """Return colour hex for an order status label (FILLED, OPEN, …)."""
    s = status.upper().strip()
    if s == "FILLED":
        return ACCENT_GREEN
    if s == "OPEN":
        return INFO_BLUE
    if s == "CANCELLED":
        return WARNING_YELLOW
    if s == "EXPIRED":
        return TEXT_MUTED
    return TEXT_SECONDARY


def gauge_color(score: float) -> str:
    """Return colour hex for a 0.0–1.0 score gauge."""
    if score != score:  # NaN
        return TEXT_MUTED
    if score < 0.4:
        return ERROR_RED
    if score < 0.7:
        return WARNING_YELLOW
    return ACCENT_GREEN



def safe_query(screen: _Queryable, widget_id: str, widget_type: type[Any], fn: Callable[[Any], Any] | None = None) -> Any:
    """Safely query a widget and optionally apply a function to it."""
    try:
        widget = screen.query_one(widget_id, widget_type)
        if fn is not None:
            return fn(widget)
        return widget
    except (AttributeError, TypeError, ValueError):
        return None
    except Exception:
        return None



def sanitize_status_text(text: str, max_len: int = 120) -> str:
    """Sanitize text for display in a status bar."""
    import re
    # Strip ANSI escape sequences
    clean = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', text)
    # Strip control characters (keep printable + space)
    clean = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', clean)
    # Replace newlines with spaces
    clean = clean.replace('\n', ' ').replace('\r', '')
    # Collapse multiple spaces
    clean = re.sub(r' {2,}', ' ', clean).strip()
    return truncate(clean, max_len)



def bar_gauge(value: float, width: int = 10, *, filled_char: str = "\u2588", empty_char: str = "\u2591") -> str:
    """Render an ASCII bar gauge string."""
    filled = max(0, min(width, int(value * width)))
    return filled_char * filled + empty_char * (width - filled)


def compact_qty(qty: float) -> str:
    """Format a quantity in compact form (K, M) for table cells."""
    if abs(qty) >= 1_000_000:
        return f"{qty / 1_000_000:.2f}M"
    if abs(qty) >= 1_000:
        return f"{qty / 1_000:.2f}K"
    return f"{qty:,.4f}"


# ── Shared CSS Snippets ─────────────────────────────────────────────
# Reusable CSS fragments for Textual widgets.  Import and interpolate
# into ``DEFAULT_CSS`` to avoid repeating the same style blocks across
# every widget.

PANEL_CSS = "padding: 0 1; background: {bg};".format(bg=SURFACE)
SCROLLABLE_CSS = "overflow-y: auto; {panel}".format(panel=PANEL_CSS)
COMPACT_CSS = "height: auto; min-height: 5; {panel}".format(panel=PANEL_CSS)
EXPANDABLE_CSS = "height: 1fr; min-height: 6; {scroll}".format(scroll=SCROLLABLE_CSS)

