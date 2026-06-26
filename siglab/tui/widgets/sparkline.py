"""Sparkline widget for rendering ASCII price charts in the terminal."""

from __future__ import annotations

from typing import Any
from collections.abc import Sequence

from rich.text import Text
from textual.reactive import reactive
from textual.widgets import Static

from siglab.tui.formatting import ACCENT_GREEN, ERROR_RED, TEXT_MUTED

_SPARK_CHARS = "▁▂▃▄▅▆▇█"


def sparkline_text(
    values: Sequence[float],
    *,
    width: int = 50,
    bullish_color: str = ACCENT_GREEN,
    bearish_color: str = ERROR_RED,
    neutral_color: str = TEXT_MUTED,
) -> Text:
    """Render a sequence of price values as a Rich ``Text`` sparkline."""
    if not values:
        return Text("─" * width, style=neutral_color)
    n = len(values)
    if n > width:
        step = n / width
        lo = hi = values[0]
        for i in range(width):
            v = values[int(i * step)]
            lo = min(lo, v)
            hi = max(hi, v)
        span = hi - lo if hi != lo else 1.0
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


def ohlc_summary(candles: Sequence[dict[str, Any]]) -> str:
    """Render a compact OHLC summary line from candle dicts."""
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
    DEFAULT_CSS = "SparklineWidget { height: auto; min-height: 3; padding: 0 1; }"

    def __init__(self, width: int = 50, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._chart_width = width

    def render(self) -> Text:
        return sparkline_text(self.values, width=self._chart_width)

    def set_values(self, values: Sequence[float]) -> None:
        """Update the sparkline data."""
        self.values = values if isinstance(values, list) else list(values)
