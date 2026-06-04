"""Sparkline widget for rendering ASCII price charts in the terminal.

Uses Unicode block characters (▁▂▃▄▅▆▇█) to render compact price
visualisations suitable for terminal UIs.
"""

from __future__ import annotations

from typing import Sequence

from rich.text import Text
from textual.reactive import reactive
from textual.widgets import Static

from siglab.tui.formatting import ACCENT_GREEN, ERROR_RED, TEXT_MUTED

# Unicode block elements from lowest to highest
_SPARK_CHARS = "▁▂▃▄▅▆▇█"


def sparkline_text(
    values: Sequence[float],
    *,
    width: int = 50,
    bullish_color: str = ACCENT_GREEN,
    bearish_color: str = ERROR_RED,
    neutral_color: str = TEXT_MUTED,
) -> Text:
    """Render a sequence of price values as a Rich ``Text`` sparkline.

    Each value is mapped to one of the 8 Unicode block characters.
    Colour is green if the overall trend is up, red if down.

    Parameters
    ----------
    values : Sequence[float]
        Price values (oldest first). Accepts any Sequence including
        tuples, memoryview, and list slices — no copy is made.
    width : int
        Maximum character width of the sparkline.
    bullish_color, bearish_color, neutral_color : str
        Rich colour names for the trend direction.

    Returns
    -------
    Text
        Rich-renderable sparkline text.
    """
    if not values:
        return Text("─" * width, style=neutral_color)

    n = len(values)

    # Resample to fit width — use index arithmetic directly on the
    # source sequence without copying into an intermediate list.
    if n > width:
        step = n / width
        lo = hi = values[0]
        for i in range(width):
            v = values[int(i * step)]
            if v < lo:
                lo = v
            if v > hi:
                hi = v
        span = hi - lo if hi != lo else 1.0

        # Determine trend colour from first and last original values
        if hi == lo:
            colour = neutral_color
        elif values[-1] >= values[0]:
            colour = bullish_color
        else:
            colour = bearish_color

        chars: list[str] = []
        for i in range(width):
            v = values[int(i * step)]
            idx = int((v - lo) / span * (len(_SPARK_CHARS) - 1))
            idx = max(0, min(idx, len(_SPARK_CHARS) - 1))
            chars.append(_SPARK_CHARS[idx])
        return Text("".join(chars), style=colour)

    # n <= width: scan the original sequence directly
    lo = min(values)
    hi = max(values)
    span = hi - lo if hi != lo else 1.0

    if hi == lo:
        colour = neutral_color
    elif len(values) >= 2:
        colour = bullish_color if values[-1] >= values[0] else bearish_color
    else:
        colour = neutral_color

    chars = []
    for v in values:
        idx = int((v - lo) / span * (len(_SPARK_CHARS) - 1))
        idx = max(0, min(idx, len(_SPARK_CHARS) - 1))
        chars.append(_SPARK_CHARS[idx])

    return Text("".join(chars), style=colour)


def ohlc_summary(candles: Sequence[dict]) -> str:
    """Render a compact OHLC summary line from candle dicts.

    Parameters
    ----------
    candles : Sequence[dict]
        Each dict must have 'open', 'high', 'low', 'close' keys.

    Returns
    -------
    str
        Formatted string like ``O:67,210  H:67,890  L:66,800  C:67,432``.
    """
    if not candles:
        return "No candle data"
    last = candles[-1]
    o = last.get("open", 0)
    h = last.get("high", 0)
    lo = last.get("low", 0)
    c = last.get("close", 0)
    return f"O:{o:,.2f}  H:{h:,.2f}  L:{lo:,.2f}  C:{c:,.2f}"


class SparklineWidget(Static):
    """A widget that renders a sparkline chart from a list of values."""

    __slots__ = ("_chart_width",)

    values: reactive[list[float]] = reactive(list, layout=True)

    DEFAULT_CSS = """
    SparklineWidget {
        height: auto;
        min-height: 3;
        padding: 0 1;
    }
    """

    def __init__(self, width: int = 50, **kwargs) -> None:
        super().__init__(**kwargs)
        self._chart_width = width

    def render(self) -> Text:
        return sparkline_text(self.values, width=self._chart_width)

    def set_values(self, values: Sequence[float]) -> None:
        """Update the sparkline data.

        Accepts any Sequence (tuple, list, memoryview).  A new list is
        only created when *values* is not already a list, to preserve
        Textual reactivity while avoiding redundant copies.
        """
        self.values = values if isinstance(values, list) else list(values)
