"""Shared formatting helpers and color constants for the SigLab TUI.

Centralizes color references and formatting functions to avoid duplication
across screen modules and ensure theme consistency.
"""

from __future__ import annotations

from typing import Any, Callable

from rich.text import Text
# ── Color Constants ──────────────────────────────────────────────────
# Single source of truth for all Rich Text colors in TUI widgets.
# Keep in sync with siglab/tui/styles/theme.tcss variables.

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
    """Truncate text to width with ellipsis.

    Handles edge cases: width <= 0 returns empty string, width == 1
    returns just the ellipsis character.
    """
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


def format_confidence(conf: float | None) -> "Text":
    """Format a numeric confidence value (0.0–1.0) as coloured Rich Text.

    Thresholds:
      - >= 0.8  → green (high confidence)
      - >= 0.5  → yellow (moderate)
      - < 0.5   → red (low)
    """
    if conf is None:
        return Text("\u2500", style=TEXT_MUTED)
    label = f"{conf:.0%}"
    if conf >= 0.8:
        return Text(label, style=ACCENT_GREEN)
    elif conf >= 0.5:
        return Text(label, style=WARNING_YELLOW)
    else:
        return Text(label, style=ERROR_RED)


def classification_color(classification: str) -> str:
    """Return color hex for a skill classification label.

    Handles classification strings like HIGH_VALUE, MEDIUM_VALUE,
    LOW_VALUE, NOISY.
    """
    c = classification.upper().strip()
    if c == "HIGH_VALUE":
        return ACCENT_GREEN
    elif c == "MEDIUM_VALUE":
        return INFO_BLUE
    elif c == "LOW_VALUE":
        return TEXT_MUTED
    elif c == "NOISY":
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




def widget_header(title: str) -> Text:
    """Render a standardised widget header.

    Consistent uppercase bold style used across all screen widgets.
    """
    return Text(f" {title.upper()}\n", style=f"bold {TEXT_PRIMARY}")


def section_divider(width: int = 40) -> Text:
    """Render a horizontal divider line with consistent style."""
    return Text("\u2500" * width + "\n", style=BORDER_DIM)


# ── Status / Side / Gauge Style Helpers ──────────────────────────────


def status_style(passed: bool | None, deployed: bool = False) -> tuple[str, str]:
    """Return ``(dot_char, color_hex)`` for a pass/fail/deployed status.

    Used by list widgets and detail views to render consistent
    status indicators without duplicating the colour mapping logic.
    """
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
    """Return colour hex for a 0.0–1.0 score gauge.

    Semantics: 1.0 = best/healthiest, 0.0 = worst.
    Thresholds:
      - >= 0.7 → green (healthy)
      - >= 0.4 → yellow (moderate)
      - < 0.4  → red (high risk)
    """
    if score != score:  # NaN
        return TEXT_MUTED
    if score < 0.4:
        return ERROR_RED
    if score < 0.7:
        return WARNING_YELLOW
    return ACCENT_GREEN


# ── Reusable Widget Helpers ──────────────────────────────────────────


def render_list_item(
    hash_text: str,
    secondary_text: str,
    score: float | None,
    passed: bool | None,
    deployed: bool = False,
    *,
    is_selected: bool = False,
    is_multi: bool = False,
    secondary_width: int = 10,
) -> Text:
    """Render a standard list-row with status dot, hash, secondary label, and score.

    This is the canonical rendering pattern used by both the strategy
    list and the telemetry run list, eliminating ~40 lines of
    duplication between them.
    """
    h = hash_text[:12]
    prefix = "\u2713 " if is_multi else "  "
    dot, dot_color = status_style(passed, deployed)
    score_str = f"{score:.2f}" if score is not None and score == score else "\u2500"
    padding = max(0, 16 - len(h) - len(prefix) - 2)

    if is_selected:
        styled_row = Text()
        styled_row.append(prefix, style=INFO_BLUE if is_multi else "#000000")
        styled_row.append(dot + " ", style=dot_color if is_multi else "#000000")
        styled_row.append(truncate(h, 12), style="bold #000000")
        styled_row.append(" " * padding, style="#000000")
        styled_row.append(truncate(secondary_text, secondary_width), style="#000000")
        result = Text()
        result.append("\u25b8 ", style=ACCENT_GREEN)
        result.append_text(styled_row)
        result.append(f"  {score_str}", style=f"bold #000000 on {ACCENT_GREEN}")
        return result
    else:
        row = Text()
        row.append(prefix, style=INFO_BLUE if is_multi else TEXT_MUTED)
        row.append(dot + " ", style=dot_color)
        row.append(truncate(h, 12), style=TEXT_PRIMARY)
        row.append(" " * padding, style=TEXT_MUTED)
        row.append(truncate(secondary_text, secondary_width), style=TEXT_SECONDARY)
        result = Text()
        result.append("  ")
        result.append_text(row)
        result.append(f"  {score_str}", style=TEXT_MUTED)
        return result


def safe_query(screen: Any, widget_id: str, widget_type: type[Any], fn: Callable[[Any], Any] | None = None) -> Any:
    """Safely query a widget and optionally apply a function to it.

    Eliminates the pervasive ``try: self.query_one(id, Type).fn()
    except Exception: pass`` pattern.  Returns the widget when *fn*
    is ``None``, or the function result otherwise.

    Usage::

        # Get widget reference
        widget = safe_query(self, "#my-id", MyWidget)

        # Apply mutation
        safe_query(self, "#my-id", MyWidget, lambda w: setattr(w, "data", data))
        safe_query(self, "#my-id", MyWidget, lambda w: w.refresh())
        safe_query(self, "#my-id", Static, lambda w: w.update(text))
    """
    try:
        widget = screen.query_one(widget_id, widget_type)
        if fn is not None:
            return fn(widget)
        return widget
    except Exception:
        return None


def safe_update_text(screen: Any, widget_id: str, text: str) -> None:
    """Shorthand for ``safe_query`` on a ``Static`` widget ``update()``."""
    from textual.widgets import Static  # local import to avoid circular
    def _do_update(w: Static) -> None:
        w.update(text)
    safe_query(screen, widget_id, Static, _do_update)


def sanitize_status_text(text: str, max_len: int = 120) -> str:
    """Sanitize text for display in a status bar.

    Strips ANSI escape codes, control characters, newlines, and
    truncates to *max_len* characters.  Prevents raw subprocess
    stderr from corrupting the TUI layout.
    """
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


# ── Table Rendering Helpers ──────────────────────────────────────────


def table_header(*columns: tuple[str, int]) -> Text:
    """Render a table header row and separator line.

    Each column is ``(name, width)``.  Returns a ``Text`` object with
    the header labels and a ``─`` separator below.
    """
    hdr = Text("  ")
    total = 0
    for name, width in columns:
        hdr.append(f"{name:<{width}}", style=TEXT_MUTED)
        total += width
    hdr.append("\n")
    hdr.append("  " + "\u2500" * total + "\n", style=BORDER_DIM)
    return hdr


def bar_gauge(value: float, width: int = 10, *, filled_char: str = "\u2588", empty_char: str = "\u2591") -> str:
    """Render an ASCII bar gauge string.

    *value* should be in ``[0.0, 1.0]``.  Returns a string of
    *filled_char* and *empty_char* characters.
    """
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

