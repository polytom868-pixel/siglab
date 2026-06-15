"""
SoDEX public REST perp market data source with ParquetLake caching.

Provides ``SoDEXFeeds``, a high-level data feed that wraps
``SoDEXPublicPerpsClient`` with DataFrame conversion and persistent
caching via ``ParquetLake``.

Supported intervals for klines
-------------------------------
``1m``, ``5m``, ``15m``, ``30m``, ``1h``, ``4h``, ``1d``, ``1w``, ``1M``

Exception hierarchy
--------------------
All exceptions extend ``SoDEXError`` (re-exported from the live client):

* ``SoDEXTransportError`` — network, DNS, TLS, timeout failures
* ``SoDEXRateLimitError`` — 429 rate-limit response
* ``SoDEXUpstreamError`` — business-logic / HTTP error responses
* ``SoDEXFormatError`` — malformed response envelope
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import pandas as pd
import httpx

from siglab.data.store import ParquetLake
from siglab.data.sodex_client import (
    SoDEXError,
    SoDEXFormatError,
    SoDEXPublicPerpsClient,
    SoDEXRateLimitError,
    SoDEXTransportError,
    SoDEXUpstreamError,
)
from siglab.data.sodex_rate_limit import SoDEXWeightScheduler

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Re-export error classes so the data layer has a single public surface
# ---------------------------------------------------------------------------
__all__ = [
    "SoDEXError",
    "SoDEXTransportError",
    "SoDEXRateLimitError",
    "SoDEXUpstreamError",
    "SoDEXFormatError",
    "SoDEXFeeds",
]

KLINE_INTERVALS = frozenset({"1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w", "1M"})

# Mapping of SoDEX kline dict keys to canonical column names
_KLINE_FIELDS = {
    "t": "timestamp",
    "o": "open",
    "h": "high",
    "l": "low",
    "c": "close",
    "v": "volume",
    "q": "quote_volume",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _kline_to_row(kline: dict[str, Any]) -> dict[str, Any]:
    """Normalise a single SoDEX kline dict to canonical column names."""
    return {_KLINE_FIELDS.get(k, k): v for k, v in kline.items()}


def _parse_kline_timestamp_ms(ts_ms: int) -> datetime:
    """Convert a millisecond epoch to a UTC datetime."""
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=UTC)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_KLINES_CACHE_TTL_HOURS = 1.0
DEFAULT_SYMBOLS_CACHE_TTL_HOURS = 24.0
DEFAULT_TICKERS_CACHE_TTL_HOURS = 0.25  # 15 minutes
DEFAULT_MARK_PRICES_CACHE_TTL_HOURS = 0.25
DEFAULT_BOOK_TICKERS_CACHE_TTL_HOURS = 0.08  # ~5 minutes
DEFAULT_ORDERBOOK_CACHE_TTL_HOURS = 0.03  # ~2 minutes
DEFAULT_TRADES_CACHE_TTL_HOURS = 0.08


# ---------------------------------------------------------------------------
# SoDEXFeeds
# ---------------------------------------------------------------------------


class SoDEXFeeds:
    """
    High-level SoDEX perp market data feed with ParquetLake caching.

    Wraps ``SoDEXPublicPerpsClient`` and adds DataFrame conversion for
    structured data, persistent caching, and graceful handling of
    nonexistent symbols (returns empty data instead of raising).

    Parameters
    ----------
    lake : ParquetLake
        Caching backend.
    base_url : str
        SoDEX perp REST API base URL (default: mainnet).
    timeout_s : float
        Per-request timeout in seconds.
    retries : int
        Number of retries on transport / 5xx errors.
    klines_cache_ttl_hours : float
        How long kline data stays fresh in the lake cache.
    symbols_cache_ttl_hours : float
        How long the symbol list stays fresh.
    """

    def __init__(
        self,
        lake: ParquetLake,
        *,
        base_url: str = "https://mainnet-gw.sodex.dev/api/v1/perps",
        timeout_s: float = 10.0,
        retries: int = 1,
        klines_cache_ttl_hours: float = DEFAULT_KLINES_CACHE_TTL_HOURS,
        symbols_cache_ttl_hours: float = DEFAULT_SYMBOLS_CACHE_TTL_HOURS,
        tickers_cache_ttl_hours: float = DEFAULT_TICKERS_CACHE_TTL_HOURS,
        mark_prices_cache_ttl_hours: float = DEFAULT_MARK_PRICES_CACHE_TTL_HOURS,
        book_tickers_cache_ttl_hours: float = DEFAULT_BOOK_TICKERS_CACHE_TTL_HOURS,
        orderbook_cache_ttl_hours: float = DEFAULT_ORDERBOOK_CACHE_TTL_HOURS,
        trades_cache_ttl_hours: float = DEFAULT_TRADES_CACHE_TTL_HOURS,
        weight_scheduler: SoDEXWeightScheduler | None = None,
    ) -> None:
        self.lake = lake
        self._http_client = httpx.AsyncClient(
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=4)
        )
        self._client = SoDEXPublicPerpsClient(
            base_url=base_url,
            timeout_s=timeout_s,
            retries=retries,
            weight_scheduler=weight_scheduler,
            client=self._http_client,
        )
        self._klines_cache_ttl_hours = klines_cache_ttl_hours
        self._symbols_cache_ttl_hours = symbols_cache_ttl_hours
        self._tickers_cache_ttl_hours = tickers_cache_ttl_hours
        self._mark_prices_cache_ttl_hours = mark_prices_cache_ttl_hours
        self._book_tickers_cache_ttl_hours = book_tickers_cache_ttl_hours
        self._orderbook_cache_ttl_hours = orderbook_cache_ttl_hours
        self._trades_cache_ttl_hours = trades_cache_ttl_hours

    async def close(self) -> None:
        """Release the underlying HTTP client resources."""
        await self._http_client.aclose()

    # ------------------------------------------------------------------
    # Klines
    # ------------------------------------------------------------------

    async def fetch_klines(
        self,
        symbol: str,
        interval: str,
        limit: int = 100,
        *,
        start_time: int | None = None,
        end_time: int | None = None,
        skip_cache: bool = False,
    ) -> pd.DataFrame:
        """
        Fetch kline / candlestick data for a perp symbol.

        Returns a DataFrame with columns ``timestamp, open, high, low,
        close, volume, quote_volume``.

        Parameters
        ----------
        symbol : str
            Perp symbol, e.g. ``"BTC-USD"``.
        interval : str
            One of ``1m, 5m, 15m, 30m, 1h, 4h, 1d, 1w, 1M``.
        limit : int
            Maximum number of candles (default 100).
        start_time, end_time : int, optional
            Filter by millisecond epoch.
        skip_cache : bool
            If True, bypass the lake cache and always hit the API.

        Returns
        -------
        pd.DataFrame
            Columns ``[timestamp, open, high, low, close, volume,
            quote_volume]``.  Empty if the symbol does not exist.
        """
        interval = str(interval).lower()
        if interval not in KLINE_INTERVALS:
            raise ValueError(
                f"Unsupported kline interval {interval!r}; "
                f"expected one of {sorted(KLINE_INTERVALS)}"
            )

        if not symbol or not symbol.strip():
            return self._empty_klines_frame()

        cache_key = self._kline_cache_key(symbol, interval, limit, start_time, end_time)

        if not skip_cache:
            cached = self.lake.latest_frame(
                "sodex_klines",
                cache_key,
                max_age_hours=self._klines_cache_ttl_hours,
            )
            if cached is not None and not cached.empty:
                return cached

        try:
            rows = await self._client.klines(
                symbol=symbol.strip(),
                interval=interval,
                start_time=start_time,
                end_time=end_time,
                limit=limit,
            )
        except SoDEXUpstreamError as exc:
            # Nonexistent symbol → return empty data (VAL-DATA-015)
            logger.warning("SoDEX klines upstream error for %s: %s", symbol, exc)
            empty = self._empty_klines_frame()
            self.lake.write_frame("sodex_klines", cache_key, empty)
            return empty

        frame = self._klines_to_frame(rows)

        # Write to cache even if empty (negative cache)
        self.lake.write_frame("sodex_klines", cache_key, frame)
        return frame

    def _kline_cache_key(
        self,
        symbol: str,
        interval: str,
        limit: int,
        start_time: int | None,
        end_time: int | None,
    ) -> str:
        parts = [symbol, interval, str(limit)]
        if start_time is not None:
            parts.append(f"st{start_time}")
        if end_time is not None:
            parts.append(f"et{end_time}")
        return "_".join(parts)

    @staticmethod
    def _empty_klines_frame() -> pd.DataFrame:
        """Return an empty klines DataFrame with canonical columns and a DatetimeIndex."""
        frame = pd.DataFrame(
            {"open": pd.Series(dtype=float), "high": pd.Series(dtype=float),
             "low": pd.Series(dtype=float), "close": pd.Series(dtype=float),
             "volume": pd.Series(dtype=float), "quote_volume": pd.Series(dtype=float)},
        )
        frame.index = pd.DatetimeIndex([], name="timestamp")
        return frame

    @staticmethod
    def _klines_to_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
        """Convert raw SoDEX kline dicts to a typed DataFrame."""
        if not rows:
            return SoDEXFeeds._empty_klines_frame()

        data = [_kline_to_row(k) for k in rows]
        frame = pd.DataFrame(data)

        # Ensure canonical column order
        expected = ["timestamp", "open", "high", "low", "close", "volume", "quote_volume"]
        for col in expected:
            if col not in frame.columns:
                frame[col] = 0

        # Convert to numeric
        numeric_cols = ["open", "high", "low", "close", "volume", "quote_volume"]
        for col in numeric_cols:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")

        # Parse timestamp and use it as the index
        if "timestamp" in frame.columns:
            frame["timestamp"] = pd.to_numeric(frame["timestamp"], errors="coerce")
            frame["timestamp"] = pd.to_datetime(frame["timestamp"], unit="ms", utc=True)
            frame = frame.set_index("timestamp").sort_index()
            if not frame.empty:
                frame.index = frame.index.round("1h")

        return frame

    async def _fetch_and_cache_json_list(
        self,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        cache_path: tuple[str, str],
        ttl_hours: float | None = None,
        skip_cache: bool = False,
    ) -> list[dict[str, Any]]:
        namespace, cache_key = cache_path
        if not skip_cache:
            cached = self.lake.latest_json(
                namespace,
                cache_key,
                max_age_hours=ttl_hours,
            )
            if cached is not None:
                return list(cached)
        try:
            method = getattr(self._client, endpoint)
            rows = await method(**(params or {}))
        except SoDEXUpstreamError:
            return []
        self.lake.write_json(namespace, cache_key, rows)
        return rows

    # ------------------------------------------------------------------
    # Symbols
    # ------------------------------------------------------------------

    async def fetch_symbols(
        self,
        *,
        skip_cache: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Fetch all tradable perp symbols with metadata.

        Returns a list of dicts, each containing symbol metadata such as
        ``name``, ``pricePrecision``, ``quantityPrecision``,
        ``minNotional``, ``maxLeverage``, ``marginTiers``, ``status``,
        etc.
        """
        return await self._fetch_and_cache_json_list(
            "symbols",
            cache_path=("sodex_symbols", "all_symbols"),
            ttl_hours=self._symbols_cache_ttl_hours,
            skip_cache=skip_cache,
        )

    # ------------------------------------------------------------------
    # Tickers
    # ------------------------------------------------------------------

    async def fetch_tickers(
        self,
        *,
        symbol: str | None = None,
        skip_cache: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Fetch 24-hour ticker statistics.

        If *symbol* is provided, only data for that symbol is returned.
        Otherwise, all symbols are returned.
        """
        cache_key = f"tickers_{symbol}" if symbol else "tickers_all"
        return await self._fetch_and_cache_json_list(
            "tickers",
            params={"symbol": symbol},
            cache_path=("sodex_tickers", cache_key),
            ttl_hours=self._tickers_cache_ttl_hours,
            skip_cache=skip_cache,
        )

    # ------------------------------------------------------------------
    # Mark prices
    # ------------------------------------------------------------------

    async def fetch_mark_prices(
        self,
        *,
        symbol: str | None = None,
        skip_cache: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Fetch current mark prices, index prices, and funding rates.

        If *symbol* is provided, only data for that symbol is returned.
        """
        cache_key = f"mark_prices_{symbol}" if symbol else "mark_prices_all"
        return await self._fetch_and_cache_json_list(
            "mark_prices",
            params={"symbol": symbol},
            cache_path=("sodex_mark_prices", cache_key),
            ttl_hours=self._mark_prices_cache_ttl_hours,
            skip_cache=skip_cache,
        )

    # ------------------------------------------------------------------
    # Book tickers (best bid / ask)
    # ------------------------------------------------------------------

    async def fetch_book_tickers(
        self,
        *,
        symbol: str | None = None,
        skip_cache: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Fetch best bid/ask for perp symbols.

        If *symbol* is provided, only the matching entry is returned
        (as a single-element list).
        """
        cache_key = f"book_tickers_{symbol}" if symbol else "book_tickers_all"
        return await self._fetch_and_cache_json_list(
            "book_tickers",
            params={"symbol": symbol},
            cache_path=("sodex_book_tickers", cache_key),
            ttl_hours=self._book_tickers_cache_ttl_hours,
            skip_cache=skip_cache,
        )

    # ------------------------------------------------------------------
    # Order book (depth)
    # ------------------------------------------------------------------

    async def fetch_orderbook(
        self,
        symbol: str,
        limit: int = 100,
        *,
        skip_cache: bool = False,
    ) -> dict[str, Any]:
        """
        Fetch order book depth for a perp symbol.

        Returns a dict with ``bids`` and ``asks`` arrays, each
        containing ``[price, size]`` pairs.
        """
        if not symbol or not symbol.strip():
            return {"bids": [], "asks": [], "symbol": symbol}

        cache_key = f"orderbook_{symbol}_{limit}"

        if not skip_cache:
            cached = self.lake.latest_json(
                "sodex_orderbook",
                cache_key,
                max_age_hours=self._orderbook_cache_ttl_hours,
            )
            if cached is not None:
                return dict(cached)

        try:
            data = await self._client.orderbook(symbol=symbol.strip(), limit=limit)
        except SoDEXUpstreamError:
            empty: dict[str, Any] = {"bids": [], "asks": [], "symbol": symbol}
            self.lake.write_json("sodex_orderbook", cache_key, empty)
            return empty

        result = dict(data)
        result["symbol"] = symbol
        self.lake.write_json("sodex_orderbook", cache_key, result)
        return result

    # ------------------------------------------------------------------
    # Recent trades
    # ------------------------------------------------------------------

    async def fetch_trades(
        self,
        symbol: str,
        limit: int = 100,
        *,
        skip_cache: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Fetch recent trades for a perp symbol.

        Returns a list of trade dicts with fields like ``t`` (trade ID),
        ``T`` (timestamp ms), ``s`` (symbol), ``S`` (side), ``p``
        (price), ``q`` (quantity).
        """
        if not symbol or not symbol.strip():
            return []

        return await self._fetch_and_cache_json_list(
            "trades",
            params={"symbol": symbol.strip(), "limit": limit},
            cache_path=("sodex_trades", f"trades_{symbol}_{limit}"),
            ttl_hours=self._trades_cache_ttl_hours,
            skip_cache=skip_cache,
        )

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def metrics_snapshot(self) -> dict[str, Any]:
        """
        Return client-level metrics for the underlying HTTP client.

        Includes per-endpoint latency percentiles, attempt counts,
        success rates, and weight-scheduler state.
        """
        return self._client.metrics_snapshot()
