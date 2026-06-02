"""
Tests for ``siglab.data.sodex_feeds.SoDEXFeeds``.

Covers:
- fetch_klines with valid data, empty symbol, nonexistent symbol, cache
- fetch_symbols
- fetch_tickers, fetch_mark_prices, fetch_book_tickers
- fetch_orderbook
- fetch_trades
- Error classification (rate limit, transport)
- Caching behaviour
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from siglab.data.sodex_feeds import (
    KLINE_INTERVALS,
    SoDEXFeeds,
    SoDEXRateLimitError,
    SoDEXTransportError,
    SoDEXUpstreamError,
)
from siglab.data.store import ParquetLake
from siglab.live.sodex_client import SoDEXPublicPerpsClient


# Mark every test in this module as asyncio
pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_lake(tmp_path: Path) -> ParquetLake:
    """A ParquetLake rooted in a temporary directory."""
    return ParquetLake(tmp_path / "lake")


@pytest.fixture
def sample_klines() -> list[dict]:
    """Simulate a 3-kline response from the SoDEX API."""
    base_ts = int(datetime(2026, 6, 1, tzinfo=UTC).timestamp() * 1000)
    return [
        {"t": base_ts + 0, "o": "100.0", "h": "101.0", "l": "99.5", "c": "100.5", "v": "10.0", "q": "1005.0"},
        {"t": base_ts + 3600_000, "o": "100.5", "h": "102.0", "l": "100.0", "c": "101.5", "v": "15.0", "q": "1522.5"},
        {"t": base_ts + 7200_000, "o": "101.5", "h": "103.0", "l": "101.0", "c": "102.0", "v": "20.0", "q": "2040.0"},
    ]


@pytest.fixture
def sample_symbols() -> list[dict]:
    """Simulate the symbols endpoint response."""
    return [
        {
            "name": "BTC-USD",
            "pricePrecision": 4,
            "quantityPrecision": 1,
            "minNotional": "10",
            "maxLeverage": 10,
            "status": "TRADING",
            "marginTiers": [
                {"maxNotionalValue": "2000000", "maintenanceMarginRate": "0.05", "maxLeverage": 10}
            ],
        },
        {
            "name": "ETH-USD",
            "pricePrecision": 3,
            "quantityPrecision": 2,
            "minNotional": "10",
            "maxLeverage": 20,
            "status": "TRADING",
            "marginTiers": [
                {"maxNotionalValue": "1000000", "maintenanceMarginRate": "0.04", "maxLeverage": 20}
            ],
        },
    ]


@pytest.fixture
def sample_tickers() -> list[dict]:
    return [
        {"symbol": "BTC-USD", "lastPx": "70800", "volume": "1000", "quoteVolume": "70800000"},
        {"symbol": "ETH-USD", "lastPx": "2500", "volume": "5000", "quoteVolume": "12500000"},
    ]


@pytest.fixture
def sample_mark_prices() -> list[dict]:
    return [
        {"symbol": "BTC-USD", "markPrice": "70750", "indexPrice": "70800", "fundingRate": "0.0001"},
        {"symbol": "ETH-USD", "markPrice": "2500", "indexPrice": "2501", "fundingRate": "0.0001"},
    ]


@pytest.fixture
def sample_book_tickers() -> list[dict]:
    return [
        {"symbol": "BTC-USD", "askPx": "70845", "askSz": "1.25618", "bidPx": "70844", "bidSz": "0.2478"},
    ]


@pytest.fixture
def sample_orderbook() -> dict:
    return {
        "bids": [["70800", "1.5"], ["70700", "2.0"]],
        "asks": [["70900", "1.0"], ["71000", "1.5"]],
    }


@pytest.fixture
def sample_trades() -> list[dict]:
    return [
        {"t": 1, "T": 1780375942676, "s": "BTC-USD", "S": "BUY", "p": "70800", "q": "0.1"},
        {"t": 2, "T": 1780375939963, "s": "BTC-USD", "S": "SELL", "p": "70700", "q": "0.2"},
    ]


# ---------------------------------------------------------------------------
# Helper: create a SoDEXFeeds instance with a mocked client
# ---------------------------------------------------------------------------


def _make_feeds(
    lake: ParquetLake,
    **kwargs: object,
) -> tuple[SoDEXFeeds, MagicMock]:
    """
    Create a SoDEXFeeds with a mocked ``SoDEXPublicPerpsClient``.

    Returns ``(feeds, mock_client)`` where ``mock_client`` is the
    underlying ``SoDEXPublicPerpsClient`` (or rather its overridden
    methods via a MagicMock).
    """
    mock = MagicMock(spec=SoDEXPublicPerpsClient)
    mock.klines = AsyncMock()
    mock.symbols = AsyncMock()
    mock.tickers = AsyncMock()
    mock.mark_prices = AsyncMock()
    mock.book_tickers = AsyncMock()
    mock.trades = AsyncMock()
    mock.orderbook = AsyncMock()
    mock.close = AsyncMock()

    feeds = SoDEXFeeds(lake=lake, retries=0, **kwargs)
    feeds._client = mock  # type: ignore[assignment]
    return feeds, mock


# ---------------------------------------------------------------------------
# Klines tests
# ---------------------------------------------------------------------------


class TestFetchKlines:
    """VAL-DATA-001, VAL-DATA-015."""

    async def test_returns_valid_klines_with_ohlcqv(
        self,
        tmp_lake: ParquetLake,
        sample_klines: list[dict],
    ) -> None:
        """Happy path: returns a DataFrame with o/h/l/c/v/q columns."""
        feeds, mock = _make_feeds(tmp_lake)
        mock.klines.return_value = sample_klines

        frame = await feeds.fetch_klines("BTC-USD", "1h", limit=3)

        assert isinstance(frame, pd.DataFrame)
        assert not frame.empty
        assert list(frame.columns) == ["open", "high", "low", "close", "volume", "quote_volume"]
        assert frame.index.name == "timestamp"
        assert pd.api.types.is_datetime64_any_dtype(frame.index)
        assert frame["close"].iloc[-1] == 102.0
        assert frame["volume"].iloc[-1] == 20.0
        assert frame["quote_volume"].iloc[-1] == 2040.0

    async def test_empty_symbol_returns_empty_dataframe(
        self,
        tmp_lake: ParquetLake,
    ) -> None:
        """Empty symbol returns empty DataFrame without calling the API."""
        feeds, mock = _make_feeds(tmp_lake)

        frame = await feeds.fetch_klines("", "1h")

        assert frame.empty
        assert list(frame.columns) == ["open", "high", "low", "close", "volume", "quote_volume"]
        mock.klines.assert_not_called()

    async def test_empty_symbol_blank_returns_empty(
        self,
        tmp_lake: ParquetLake,
    ) -> None:
        """Whitespace-only symbol also returns empty."""
        feeds, mock = _make_feeds(tmp_lake)

        frame = await feeds.fetch_klines("  ", "1h")

        assert frame.empty
        mock.klines.assert_not_called()

    async def test_nonexistent_symbol_returns_empty_dataframe(
        self,
        tmp_lake: ParquetLake,
    ) -> None:
        """SoDEXUpstreamError (e.g. 404) yields empty DataFrame, not an exception."""
        feeds, mock = _make_feeds(tmp_lake)
        mock.klines.side_effect = SoDEXUpstreamError(
            "invalid parameter: symbol",
            status_code=400,
        )

        frame = await feeds.fetch_klines("NONEXISTENT-USD", "1h")

        assert frame.empty
        assert list(frame.columns) == ["open", "high", "low", "close", "volume", "quote_volume"]

    async def test_invalid_interval_raises_value_error(
        self,
        tmp_lake: ParquetLake,
    ) -> None:
        """Unsupported intervals raise ValueError."""
        feeds, mock = _make_feeds(tmp_lake)

        with pytest.raises(ValueError, match="Unsupported kline interval"):
            await feeds.fetch_klines("BTC-USD", "invalid_interval")

    async def test_all_supported_intervals(
        self,
        tmp_lake: ParquetLake,
        sample_klines: list[dict],
    ) -> None:
        """All defined intervals are accepted."""
        feeds, mock = _make_feeds(tmp_lake)
        mock.klines.return_value = sample_klines

        for interval in KLINE_INTERVALS:
            frame = await feeds.fetch_klines("BTC-USD", interval, limit=3)
            assert isinstance(frame, pd.DataFrame)

    async def test_rate_limit_raised_as_typed_exception(
        self,
        tmp_lake: ParquetLake,
    ) -> None:
        """429 from upstream raises SoDEXRateLimitError."""
        feeds, mock = _make_feeds(tmp_lake)
        mock.klines.side_effect = SoDEXRateLimitError("rate limited", status_code=429)

        with pytest.raises(SoDEXRateLimitError):
            await feeds.fetch_klines("BTC-USD", "1h")

    async def test_transport_failure_raised_as_typed_exception(
        self,
        tmp_lake: ParquetLake,
    ) -> None:
        """Network failure raises SoDEXTransportError."""
        feeds, mock = _make_feeds(tmp_lake)
        mock.klines.side_effect = SoDEXTransportError("connection refused")

        with pytest.raises(SoDEXTransportError):
            await feeds.fetch_klines("BTC-USD", "1h")

    async def test_caching_behaviour(
        self,
        tmp_lake: ParquetLake,
        sample_klines: list[dict],
    ) -> None:
        """Data is cached and reused within TTL."""
        feeds, mock = _make_feeds(tmp_lake)
        mock.klines.return_value = sample_klines

        # First call goes to API
        await feeds.fetch_klines("BTC-USD", "1h", limit=3)
        assert mock.klines.awaited_once

        # Second call should hit cache (if within TTL)
        mock.klines.reset_mock()
        frame2 = await feeds.fetch_klines("BTC-USD", "1h", limit=3)
        mock.klines.assert_not_awaited()  # served from cache
        assert frame2 is not None and not frame2.empty

        # skip_cache=True bypasses cache
        mock.klines.return_value = sample_klines
        await feeds.fetch_klines("BTC-USD", "1h", limit=3, skip_cache=True)
        assert mock.klines.awaited_once

    async def test_cache_key_variants(
        self,
        tmp_lake: ParquetLake,
        sample_klines: list[dict],
    ) -> None:
        """Different params generate different cache keys."""
        feeds, mock = _make_feeds(tmp_lake)
        mock.klines.return_value = sample_klines

        await feeds.fetch_klines("BTC-USD", "1h", limit=100)
        mock.klines.reset_mock()

        # Different symbol → should miss cache
        mock.klines.return_value = sample_klines
        await feeds.fetch_klines("ETH-USD", "1h", limit=100)
        assert mock.klines.awaited_once


# ---------------------------------------------------------------------------
# Symbols tests
# ---------------------------------------------------------------------------


class TestFetchSymbols:
    """VAL-DATA-002."""

    async def test_returns_all_symbols_with_metadata(
        self,
        tmp_lake: ParquetLake,
        sample_symbols: list[dict],
    ) -> None:
        """fetch_symbols returns full metadata list."""
        feeds, mock = _make_feeds(tmp_lake)
        mock.symbols.return_value = sample_symbols

        symbols = await feeds.fetch_symbols()

        assert len(symbols) == 2
        assert symbols[0]["name"] == "BTC-USD"
        assert symbols[0]["pricePrecision"] == 4
        assert symbols[0]["minNotional"] == "10"
        assert symbols[0]["maxLeverage"] == 10
        assert symbols[0]["status"] == "TRADING"

    async def test_caches_symbols(
        self,
        tmp_lake: ParquetLake,
        sample_symbols: list[dict],
    ) -> None:
        """Subsequent calls use cache."""
        feeds, mock = _make_feeds(tmp_lake)
        mock.symbols.return_value = sample_symbols

        await feeds.fetch_symbols()
        assert mock.symbols.awaited_once

        mock.symbols.reset_mock()
        result = await feeds.fetch_symbols()
        mock.symbols.assert_not_awaited()
        assert len(result) == 2


# ---------------------------------------------------------------------------
# Tickers tests
# ---------------------------------------------------------------------------


class TestFetchTickers:

    async def test_returns_tickers(
        self,
        tmp_lake: ParquetLake,
        sample_tickers: list[dict],
    ) -> None:
        feeds, mock = _make_feeds(tmp_lake)
        mock.tickers.return_value = sample_tickers

        tickers = await feeds.fetch_tickers()

        assert len(tickers) == 2
        assert tickers[0]["symbol"] == "BTC-USD"

    async def test_tickers_with_symbol_filter(
        self,
        tmp_lake: ParquetLake,
        sample_tickers: list[dict],
    ) -> None:
        feeds, mock = _make_feeds(tmp_lake)
        mock.tickers.return_value = [sample_tickers[0]]

        tickers = await feeds.fetch_tickers(symbol="BTC-USD")

        assert len(tickers) == 1
        assert tickers[0]["symbol"] == "BTC-USD"

    async def test_tickers_handles_upstream_error(
        self,
        tmp_lake: ParquetLake,
    ) -> None:
        feeds, mock = _make_feeds(tmp_lake)
        mock.tickers.side_effect = SoDEXUpstreamError("not found")

        tickers = await feeds.fetch_tickers(symbol="NONEXISTENT-USD")

        assert tickers == []
        assert mock.tickers.awaited_once


# ---------------------------------------------------------------------------
# Mark prices tests
# ---------------------------------------------------------------------------


class TestFetchMarkPrices:

    async def test_returns_mark_prices(
        self,
        tmp_lake: ParquetLake,
        sample_mark_prices: list[dict],
    ) -> None:
        feeds, mock = _make_feeds(tmp_lake)
        mock.mark_prices.return_value = sample_mark_prices

        prices = await feeds.fetch_mark_prices()

        assert len(prices) == 2
        assert prices[0]["symbol"] == "BTC-USD"
        assert "markPrice" in prices[0]
        assert "fundingRate" in prices[0]

    async def test_mark_prices_caching(
        self,
        tmp_lake: ParquetLake,
        sample_mark_prices: list[dict],
    ) -> None:
        feeds, mock = _make_feeds(tmp_lake)
        mock.mark_prices.return_value = sample_mark_prices

        await feeds.fetch_mark_prices()
        mock.mark_prices.reset_mock()

        result = await feeds.fetch_mark_prices()
        mock.mark_prices.assert_not_awaited()
        assert len(result) == 2


# ---------------------------------------------------------------------------
# Book tickers tests
# ---------------------------------------------------------------------------


class TestFetchBookTickers:

    async def test_returns_book_tickers(
        self,
        tmp_lake: ParquetLake,
        sample_book_tickers: list[dict],
    ) -> None:
        feeds, mock = _make_feeds(tmp_lake)
        mock.book_tickers.return_value = sample_book_tickers

        tickers = await feeds.fetch_book_tickers()

        assert len(tickers) == 1
        assert tickers[0]["symbol"] == "BTC-USD"
        assert "askPx" in tickers[0]
        assert "bidPx" in tickers[0]


# ---------------------------------------------------------------------------
# Order book tests
# ---------------------------------------------------------------------------


class TestFetchOrderbook:

    async def test_returns_orderbook(
        self,
        tmp_lake: ParquetLake,
        sample_orderbook: dict,
    ) -> None:
        feeds, mock = _make_feeds(tmp_lake)
        mock.orderbook.return_value = sample_orderbook

        ob = await feeds.fetch_orderbook("BTC-USD", limit=5)

        assert "bids" in ob
        assert "asks" in ob
        assert ob["symbol"] == "BTC-USD"
        assert ob["bids"][0] == ["70800", "1.5"]

    async def test_empty_symbol_returns_empty(
        self,
        tmp_lake: ParquetLake,
    ) -> None:
        feeds, mock = _make_feeds(tmp_lake)

        ob = await feeds.fetch_orderbook("")

        assert ob == {"bids": [], "asks": [], "symbol": ""}
        mock.orderbook.assert_not_called()

    async def test_upstream_error_returns_empty(
        self,
        tmp_lake: ParquetLake,
    ) -> None:
        feeds, mock = _make_feeds(tmp_lake)
        mock.orderbook.side_effect = SoDEXUpstreamError("not found")

        ob = await feeds.fetch_orderbook("NONEXISTENT-USD")

        assert ob["bids"] == []
        assert ob["asks"] == []


# ---------------------------------------------------------------------------
# Trades tests
# ---------------------------------------------------------------------------


class TestFetchTrades:

    async def test_returns_trades(
        self,
        tmp_lake: ParquetLake,
        sample_trades: list[dict],
    ) -> None:
        feeds, mock = _make_feeds(tmp_lake)
        mock.trades.return_value = sample_trades

        trades = await feeds.fetch_trades("BTC-USD", limit=5)

        assert len(trades) == 2
        assert trades[0]["s"] == "BTC-USD"
        assert trades[0]["S"] == "BUY"

    async def test_empty_symbol_returns_empty(
        self,
        tmp_lake: ParquetLake,
    ) -> None:
        feeds, mock = _make_feeds(tmp_lake)

        trades = await feeds.fetch_trades("")

        assert trades == []
        mock.trades.assert_not_called()


# ---------------------------------------------------------------------------
# Error classification tests
# ---------------------------------------------------------------------------


class TestErrorClassification:
    """VAL-DATA-011 (typed exception), VAL-DATA-017 (rate limit)."""

    async def test_typed_rate_limit_exception(self) -> None:
        """SoDEXRateLimitError is a SoDEXError subclass."""
        err = SoDEXRateLimitError("rate limit", status_code=429)
        assert isinstance(err, SoDEXRateLimitError)

    async def test_typed_transport_exception(self) -> None:
        """SoDEXTransportError is a SoDEXError subclass."""
        err = SoDEXTransportError("connection failed")
        assert isinstance(err, SoDEXTransportError)


# ---------------------------------------------------------------------------
# Integration tests (real API calls)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestIntegration:

    async def test_real_klines(self) -> None:
        """VAL-DATA-001: real API klines."""
        import tempfile

        lake = ParquetLake(Path(tempfile.mkdtemp()))
        feeds = SoDEXFeeds(lake=lake, retries=0)
        try:
            frame = await feeds.fetch_klines("BTC-USD", "1h", limit=5)
            assert not frame.empty
            assert list(frame.columns) == ["open", "high", "low", "close", "volume", "quote_volume"]
            assert frame["close"].iloc[-1] > 0
        finally:
            await feeds.close()

    async def test_real_symbols(self) -> None:
        """VAL-DATA-002: real API symbols."""
        import tempfile

        lake = ParquetLake(Path(tempfile.mkdtemp()))
        feeds = SoDEXFeeds(lake=lake, retries=0)
        try:
            symbols = await feeds.fetch_symbols()
            assert len(symbols) > 0
            btc = [s for s in symbols if "BTC" in str(s.get("name", ""))]
            assert len(btc) > 0
        finally:
            await feeds.close()

    async def test_nonexistent_symbol_klines(self) -> None:
        """VAL-DATA-015: nonexistent symbol returns empty data, no crash."""
        import tempfile

        lake = ParquetLake(Path(tempfile.mkdtemp()))
        feeds = SoDEXFeeds(lake=lake, retries=0)
        try:
            frame = await feeds.fetch_klines("NONEXISTENT-USD", "1h", limit=3)
            assert frame.empty
        finally:
            await feeds.close()
