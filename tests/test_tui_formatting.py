"""Tests for siglab.tui.formatting public API."""

from __future__ import annotations


import pytest
from rich.text import Text

from siglab.tui.formatting import (
    format_change,
    format_count,
    format_date,
    format_pnl,
    format_price,
    format_return,
    format_score,
    format_volume,
    safe_float,
    gauge_color,
    side_style,
    severity_color,
    bar_gauge,
    compact_qty,
    sanitize_status_text,
    truncate,
)


class TestFormatPrice:
    def test_large_price(self):
        assert format_price(1234.56) == "1,234.56"

    def test_medium_price(self):
        result = format_price(5.123456)
        assert "5.123" in result

    def test_small_price(self):
        result = format_price(0.001234567)
        assert "0.00123" in result

    def test_zero(self):
        result = format_price(0.0)
        assert "0" in result


class TestFormatPnl:
    def test_positive(self):
        t = format_pnl(0.05)
        assert isinstance(t, Text)
        assert "+0.05" in t.plain

    def test_negative(self):
        t = format_pnl(-1.50)
        assert isinstance(t, Text)
        assert "-1.50" in t.plain

    def test_zero(self):
        t = format_pnl(0.0)
        assert isinstance(t, Text)
        assert "0.00" in t.plain


class TestFormatReturn:
    def test_positive(self):
        t = format_return(0.1234)
        assert isinstance(t, Text)
        assert "+0.12%" in t.plain

    def test_negative(self):
        t = format_return(-0.05)
        assert isinstance(t, Text)
        assert "-0.05%" in t.plain

    def test_none(self):
        t = format_return(None)
        assert isinstance(t, Text)

    def test_nan(self):
        t = format_return(float("nan"))
        assert isinstance(t, Text)


class TestSafeFloat:
    def test_normal_number(self):
        assert safe_float("3.14") == pytest.approx(3.14)

    def test_bad_string(self):
        assert safe_float("bad", default=0.0) == 0.0

    def test_none(self):
        assert safe_float(None, default=0.0) == 0.0

    def test_nan(self):
        assert safe_float(float("nan"), default=0.0) == 0.0

    def test_custom_default(self):
        assert safe_float("bad", default=-1.0) == -1.0

    def test_empty_string(self):
        assert safe_float("", default=0.0) == 0.0


@pytest.mark.parametrize(
    "text,width,expected",
    [
        ("hello world", 5, "hell\u2026"),
        ("hi", 10, "hi"),
        ("hello", 5, "hello"),
        ("hello", 1, "\u2026"),
        ("hello", 0, ""),
        ("hello", -1, ""),
    ],
)
def test_truncate(text: str, width: int, expected: str) -> None:
    assert truncate(text, width) == expected


@pytest.mark.parametrize(
    "value,fragment",
    [(1.5, "+1.50%"), (-2.3, "-2.30%"), (0.0, "0.00%")],
)
def test_format_change(value: float, fragment: str) -> None:
    assert fragment in format_change(value).plain


@pytest.mark.parametrize(
    "value,expected",
    [
        (2_500_000_000, "2.5B"),
        (3_400_000, "3.4M"),
        (5_600, "5.6K"),
        (42, "42"),
    ],
)
def test_format_volume(value: float, expected: str) -> None:
    assert format_volume(value) == expected


class TestFormatScore:
    def test_none(self):
        t = format_score(None)
        assert t.plain == "\u2500"

    def test_high_score(self):
        t = format_score(0.9)
        assert "0.900" in t.plain

    def test_mid_score(self):
        t = format_score(0.5)
        assert "0.500" in t.plain

    def test_low_score(self):
        t = format_score(0.1)
        assert "0.100" in t.plain

    def test_nan(self):
        t = format_score(float("nan"))
        assert "NaN" in t.plain


class TestFormatCount:
    def test_none(self):
        assert format_count(None) == "\u2500"

    def test_millions(self):
        assert "M" in format_count(2_500_000)

    def test_thousands(self):
        assert "k" in format_count(3_400)

    def test_small(self):
        assert format_count(42) == "42"


class TestFormatDate:
    def test_none(self):
        assert format_date(None) == "\u2500\u2500"

    def test_empty(self):
        assert format_date("") == "\u2500\u2500"

    def test_valid_iso(self):
        result = format_date("2025-01-15T10:30:00Z")
        assert "01-15" in result


@pytest.mark.parametrize(
    "value,expected",
    [(0.9, "#4ade80"), (0.5, "#f0b456"), (0.2, "#f87171")],
)
def test_gauge_color(value: float, expected: str) -> None:
    assert gauge_color(value) == expected


class TestBarGauge:
    def test_full(self):
        assert bar_gauge(1.0, width=5) == "\u2588\u2588\u2588\u2588\u2588"

    def test_empty(self):
        assert bar_gauge(0.0, width=5) == "\u2591\u2591\u2591\u2591\u2591"

    def test_half(self):
        result = bar_gauge(0.5, width=10)
        assert result.count("\u2588") == 5
        assert result.count("\u2591") == 5


class TestCompactQty:
    def test_millions(self):
        assert "M" in compact_qty(1_500_000)

    def test_thousands(self):
        assert "K" in compact_qty(2_500)

    def test_small(self):
        result = compact_qty(0.5)
        assert "0.5" in result


class TestSanitizeStatusText:
    def test_strips_ansi(self):
        result = sanitize_status_text("\x1b[31mred\x1b[0m text")
        assert "\x1b" not in result
        assert "red" in result

    def test_strips_newlines(self):
        result = sanitize_status_text("line1\nline2\rline3")
        assert "\n" not in result
        assert "\r" not in result

    def test_truncates(self):
        result = sanitize_status_text("a" * 200, max_len=50)
        assert len(result) <= 50


@pytest.mark.parametrize(
    "value,expected",
    [("BUY", "#4ade80"), ("SELL", "#f87171")],
)
def test_side_style(value: str, expected: str) -> None:
    assert side_style(value) == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        ("critical", "#f87171"),
        ("warning", "#f0b456"),
        ("info", "#60a5fa"),
    ],
)
def test_severity_color(value: str, expected: str) -> None:
    assert severity_color(value) == expected
