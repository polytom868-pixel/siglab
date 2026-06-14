"""Tests for siglab.tui.formatting public API."""

from __future__ import annotations

import math

import pytest
from rich.text import Text

from siglab.tui.formatting import (
    format_change,
    format_count,
    format_date,
    format_drawdown,
    format_latency,
    format_pnl,
    format_price,
    format_return,
    format_score,
    format_sharpe,
    format_status,
    format_volume,
    safe_float,
    truncate,
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


class TestTruncate:
    def test_basic_truncation(self):
        assert truncate("hello world", 5) == "hell\u2026"

    def test_no_truncation_needed(self):
        assert truncate("hi", 10) == "hi"

    def test_exact_width(self):
        assert truncate("hello", 5) == "hello"

    def test_width_one(self):
        assert truncate("hello", 1) == "\u2026"

    def test_width_zero(self):
        assert truncate("hello", 0) == ""

    def test_negative_width(self):
        assert truncate("hello", -1) == ""


class TestFormatChange:
    def test_positive(self):
        t = format_change(1.5)
        assert "+1.50%" in t.plain

    def test_negative(self):
        t = format_change(-2.3)
        assert "-2.30%" in t.plain

    def test_zero(self):
        t = format_change(0.0)
        assert "0.00%" in t.plain


class TestFormatVolume:
    def test_billions(self):
        assert format_volume(2_500_000_000) == "2.5B"

    def test_millions(self):
        assert format_volume(3_400_000) == "3.4M"

    def test_thousands(self):
        assert format_volume(5_600) == "5.6K"

    def test_small(self):
        assert format_volume(42) == "42"


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


class TestGaugeColor:
    def test_high(self):
        assert gauge_color(0.9) == "#4ade80"

    def test_mid(self):
        assert gauge_color(0.5) == "#f0b456"

    def test_low(self):
        assert gauge_color(0.2) == "#f87171"


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


class TestSideStyle:
    def test_buy(self):
        assert side_style("BUY") == "#4ade80"

    def test_sell(self):
        assert side_style("SELL") == "#f87171"


class TestSeverityColor:
    def test_critical(self):
        assert severity_color("critical") == "#f87171"

    def test_warning(self):
        assert severity_color("warning") == "#f0b456"

    def test_info(self):
        assert severity_color("info") == "#60a5fa"
