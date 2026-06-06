"""
Tests for ``siglab.live.paper_client``.

Covers:
- VAL-PAPER-001: Paper order placement and .npy persistence
- VAL-PAPER-002: Order fills at real kline prices
- VAL-PAPER-003: .npy session state survives restart
- VAL-PAPER-004: Open order cancellation
- VAL-PAPER-005: Order expiry
- VAL-PAPER-006: Funding cost from real funding rates
- VAL-PAPER-010: Empty klines handled gracefully
- VAL-PAPER-011: Multiple parallel sessions isolated
- VAL-PAPER-013: Invalid params raise PaperClientError
- VAL-CLI-015: paper-start creates session (CLI)
- VAL-CLI-016: paper-status returns position/PnL/orders
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pandas as pd
import pytest

from siglab.live.paper_client import (
    PaperClientError,
    PaperOrderSide,
    PaperOrderType,
    PaperSessionNotFoundError,
    SoDEXPaperPerpsClient,
    _compute_fill_price,
    _compute_funding_cost,
    _validate_symbol,
    _validate_quantity,
    _validate_price,
    _validate_side,
    _validate_order_type,
    _validate_time_in_force,
)
from siglab.data.sodex_feeds import SoDEXFeeds

# Async tests are marked individually per class


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_sessions(tmp_path: Path) -> Path:
    """A temporary sessions directory."""
    path = tmp_path / "sessions"
    path.mkdir()
    return path


@pytest.fixture
def mock_feeds() -> MagicMock:
    """A mocked SoDEXFeeds instance."""
    feeds = MagicMock(spec=SoDEXFeeds)
    feeds.fetch_mark_prices = AsyncMock()
    feeds.fetch_klines = AsyncMock()
    return feeds


@pytest.fixture
def paper_client(tmp_sessions: Path, mock_feeds: MagicMock) -> SoDEXPaperPerpsClient:
    """A SoDEXPaperPerpsClient with mocked feeds and temp sessions dir."""
    return SoDEXPaperPerpsClient(
        feeds=mock_feeds, sessions_dir=tmp_sessions,
        slippage_bps=0.0, min_notional_usd=0.0,
    )


@pytest.fixture
def sample_kline_dicts() -> list[dict]:
    """Sample kline dicts in the raw SoDEX format."""
    base_ts = int(pd.Timestamp("2026-06-01 00:00", tz="UTC").timestamp() * 1000)
    return [
        {"t": base_ts + 0, "o": "100.0", "h": "102.0", "l": "99.0", "c": "101.0", "v": "10.0", "q": "1005.0"},
        {"t": base_ts + 3600_000, "o": "101.0", "h": "105.0", "l": "100.5", "c": "104.0", "v": "15.0", "q": "1522.5"},
        {"t": base_ts + 7200_000, "o": "104.0", "h": "106.0", "l": "103.0", "c": "105.0", "v": "20.0", "q": "2040.0"},
    ]


@pytest.fixture
def sample_kline_df(sample_kline_dicts: list[dict]) -> pd.DataFrame:
    """Sample kline data as a DataFrame (as returned by SoDEXFeeds.fetch_klines)."""
    rows = []
    for k in sample_kline_dicts:
        ts = pd.Timestamp(k["t"], unit="ms", tz="UTC")
        rows.append({
            "timestamp": ts,
            "open": float(k["o"]),
            "high": float(k["h"]),
            "low": float(k["l"]),
            "close": float(k["c"]),
            "volume": float(k["v"]),
            "quote_volume": float(k["q"]),
        })
    frame = pd.DataFrame(rows)
    frame = frame.set_index("timestamp").sort_index()
    return frame


# ---------------------------------------------------------------------------
# Input validation tests (VAL-PAPER-013)
# ---------------------------------------------------------------------------


class TestInputValidation:
    """VAL-PAPER-013: Invalid params raise PaperClientError."""

    def test_empty_symbol_raises(self) -> None:
        with pytest.raises(PaperClientError, match="must not be empty"):
            _validate_symbol("")

    def test_too_short_symbol_raises(self) -> None:
        with pytest.raises(PaperClientError, match="invalid symbol"):
            _validate_symbol("A")

    def test_negative_quantity_raises(self) -> None:
        with pytest.raises(PaperClientError, match="must be positive"):
            _validate_quantity(-1)

    def test_zero_quantity_raises(self) -> None:
        with pytest.raises(PaperClientError, match="must be positive"):
            _validate_quantity(0)

    def test_nan_quantity_raises(self) -> None:
        with pytest.raises(PaperClientError, match="must be a number"):
            _validate_quantity("not_a_number")

    def test_limit_order_without_price_raises(self) -> None:
        with pytest.raises(PaperClientError, match="price is required"):
            _validate_price(None, PaperOrderType.LIMIT)

    def test_market_order_allows_no_price(self) -> None:
        assert _validate_price(None, PaperOrderType.MARKET) is None

    def test_invalid_side_raises(self) -> None:
        with pytest.raises(PaperClientError, match="invalid side"):
            _validate_side("HOLD")

    def test_invalid_order_type_raises(self) -> None:
        with pytest.raises(PaperClientError, match="invalid order_type"):
            _validate_order_type("STOP")

    def test_invalid_time_in_force_raises(self) -> None:
        with pytest.raises(PaperClientError, match="invalid time_in_force"):
            _validate_time_in_force("INVALID")

    def test_negative_price_raises(self) -> None:
        with pytest.raises(PaperClientError, match="must be positive"):
            _validate_price(-10.0, PaperOrderType.LIMIT)

    def test_huge_quantity_raises(self) -> None:
        with pytest.raises(PaperClientError, match="too large"):
            _validate_quantity(1e13)

    def test_session_id_path_traversal_rejected(
        self,
        paper_client: SoDEXPaperPerpsClient,
    ) -> None:
        """session_id with path traversal chars raises PaperClientError."""
        for bad_id in ("../etc/passwd", "foo/bar", "foo\\bar"):
            with pytest.raises(PaperClientError, match="Invalid session_id"):
                paper_client.session_path(bad_id)


# ---------------------------------------------------------------------------
# Order placement and persistence (VAL-PAPER-001)
# ---------------------------------------------------------------------------


class TestPlaceOrder:
    """VAL-PAPER-001: Paper client places order and stores state."""
    pytestmark = pytest.mark.asyncio

    async def test_place_order_creates_record(self, paper_client: SoDEXPaperPerpsClient) -> None:
        session_id = paper_client.create_session("test")
        order = paper_client.place_order(
            session_id,
            symbol="BTC-USD",
            side="BUY",
            quantity=1.0,
            price=50000.0,
        )
        assert order["order_id"] is not None
        assert order["symbol"] == "BTC-USD"
        assert order["side"] == "BUY"
        assert order["quantity"] == 1.0
        assert order["price"] == 50000.0
        assert order["status"] == "OPEN"

    async def test_place_order_persists_to_json(self, paper_client: SoDEXPaperPerpsClient) -> None:
        session_id = paper_client.create_session("persist_test")
        paper_client.place_order(session_id, symbol="ETH-USD", side="SELL", quantity=2.0, price=3000.0)

        # Check .json file exists
        json_path = paper_client.session_path(session_id)
        assert json_path.exists()

        # Read JSON data directly
        with open(json_path) as f:
            data = json.load(f)
        assert isinstance(data, dict)
        orders = data.get("orders", {})
        assert len(orders) == 1
        order = list(orders.values())[0]
        assert order["symbol"] == "ETH-USD"
        assert order["side"] == "SELL"
        assert order["status"] == "OPEN"

    async def test_place_order_sets_all_fields(self, paper_client: SoDEXPaperPerpsClient) -> None:
        session_id = paper_client.create_session("full_fields")
        now = time.time()
        order = paper_client.place_order(
            session_id,
            symbol="BTC-USD",
            side="BUY",
            quantity=1.5,
            price=45000.0,
            order_type="LIMIT",
            time_in_force="GTC",
        )
        assert order["order_id"] is not None
        assert order["symbol"] == "BTC-USD"
        assert order["side"] == "BUY"
        assert order["quantity"] == 1.5
        assert order["price"] == 45000.0
        assert order["order_type"] == "LIMIT"
        assert order["time_in_force"] == "GTC"
        assert order["status"] == "OPEN"
        assert order["fill_price"] is None
        assert order["fill_timestamp"] is None
        assert order["created_at"] >= now


# ---------------------------------------------------------------------------
# Order fill at kline prices (VAL-PAPER-002)
# ---------------------------------------------------------------------------


class TestOrderFill:
    """VAL-PAPER-002: Paper order fills at real kline prices."""
    pytestmark = pytest.mark.asyncio

    async def test_buy_order_fills_when_low_crosses_limit(
        self,
        paper_client: SoDEXPaperPerpsClient,
        sample_kline_dicts: list[dict],
    ) -> None:
        session_id = paper_client.create_session("buy_fill")
        # Place a BUY LIMIT order at 101
        paper_client.place_order(session_id, symbol="BTC-USD", side="BUY", quantity=1.0, price=101.0)

        # Process klines - first kline has low=99, so this should fill
        fills = await paper_client.process_klines(session_id, sample_kline_dicts)
        assert len(fills) == 1
        assert fills[0]["status"] == "FILLED"
        assert fills[0]["fill_price"] is not None
        # Fill price should be within the first kline range and respect the limit
        assert fills[0]["fill_price"] <= 101.0

    async def test_sell_order_fills_when_high_crosses_limit(
        self,
        paper_client: SoDEXPaperPerpsClient,
        sample_kline_dicts: list[dict],
    ) -> None:
        session_id = paper_client.create_session("sell_fill")
        # Place a SELL LIMIT order at 99
        paper_client.place_order(session_id, symbol="BTC-USD", side="SELL", quantity=1.0, price=99.0)

        fills = await paper_client.process_klines(session_id, sample_kline_dicts)
        # First kline has high=102, which is >= 99, so this should fill
        assert len(fills) == 1
        assert fills[0]["status"] == "FILLED"
        assert fills[0]["fill_price"] is not None
        assert fills[0]["fill_price"] >= 99.0

    async def test_order_not_filled_when_kline_does_not_cross(
        self,
        paper_client: SoDEXPaperPerpsClient,
        sample_kline_dicts: list[dict],
    ) -> None:
        session_id = paper_client.create_session("no_fill")
        # Place a BUY at 98 (below the low of all klines)
        paper_client.place_order(session_id, symbol="BTC-USD", side="BUY", quantity=1.0, price=98.0)

        fills = await paper_client.process_klines(session_id, sample_kline_dicts)
        assert len(fills) == 0

        order = paper_client.get_orders(session_id)[0]
        assert order["status"] == "OPEN"

    async def test_fill_price_within_kline_range(
        self,
        paper_client: SoDEXPaperPerpsClient,
        sample_kline_dicts: list[dict],
    ) -> None:
        session_id = paper_client.create_session("fill_price_range")
        paper_client.place_order(session_id, symbol="BTC-USD", side="BUY", quantity=1.0, price=100.5)

        fills = await paper_client.process_klines(session_id, sample_kline_dicts)
        assert len(fills) == 1
        # Fill price should be within the kline range [99, 102] and at or below limit 100.5
        fp = fills[0]["fill_price"]
        assert fp is not None
        assert 99.0 <= fp <= 100.5

    async def test_fill_from_dataframe_klines(
        self,
        paper_client: SoDEXPaperPerpsClient,
        sample_kline_df: pd.DataFrame,
    ) -> None:
        """Fill detection works with DataFrame-based klines."""
        session_id = paper_client.create_session("df_fill")
        paper_client.place_order(session_id, symbol="BTC-USD", side="BUY", quantity=1.0, price=101.0)

        fills = await paper_client.process_klines(session_id, sample_kline_df)
        assert len(fills) == 1
        assert fills[0]["status"] == "FILLED"

    async def test_position_updated_after_fill(
        self,
        paper_client: SoDEXPaperPerpsClient,
        sample_kline_dicts: list[dict],
    ) -> None:
        session_id = paper_client.create_session("pos_after_fill")
        paper_client.place_order(session_id, symbol="BTC-USD", side="BUY", quantity=1.0, price=101.0)

        await paper_client.process_klines(session_id, sample_kline_dicts)

        positions = paper_client.get_positions(session_id)
        assert len(positions) == 1
        assert positions[0]["symbol"] == "BTC-USD"
        assert positions[0]["quantity"] > 0

    async def test_reduce_position_closes_and_realizes_pnl(
        self,
        paper_client: SoDEXPaperPerpsClient,
        sample_kline_dicts: list[dict],
    ) -> None:
        """Buy then sell to reduce position."""
        session_id = paper_client.create_session("reduce_pos")

        # Buy 2 at 100 (fills immediately since low=99 <= 100)
        paper_client.place_order(session_id, symbol="BTC-USD", side="BUY", quantity=2.0, price=101.0)
        await paper_client.process_klines(session_id, [sample_kline_dicts[0]])
        positions = paper_client.get_positions(session_id)
        assert len(positions) == 1
        assert positions[0]["quantity"] == 2.0

        # Sell 2 at 103 (fills since high=105 >= 103)
        paper_client.place_order(session_id, symbol="BTC-USD", side="SELL", quantity=2.0, price=103.0)
        await paper_client.process_klines(session_id, [sample_kline_dicts[1]])

        positions = paper_client.get_positions(session_id)
        assert len(positions) == 0  # Position fully closed

        pnl = paper_client.get_pnl(session_id)
        assert pnl["realized_pnl"] != 0.0  # Should have realized PnL from the trade


# ---------------------------------------------------------------------------
# .npy session survival (VAL-PAPER-003)
# ---------------------------------------------------------------------------


class TestSessionPersistence:
    """VAL-PAPER-003: .npy session state survives restart."""
    pytestmark = pytest.mark.asyncio

    async def test_session_reload_preserves_orders(
        self,
        paper_client: SoDEXPaperPerpsClient,
        tmp_sessions: Path,
    ) -> None:
        session_id = paper_client.create_session("survival")
        paper_client.place_order(session_id, symbol="BTC-USD", side="BUY", quantity=1.0, price=50000.0)
        paper_client.place_order(session_id, symbol="ETH-USD", side="SELL", quantity=2.0, price=3000.0)

        # Create a new client instance pointing to the same sessions dir
        client2 = SoDEXPaperPerpsClient(
            feeds=paper_client.feeds,
            sessions_dir=tmp_sessions,
        )

        # Load the session from disk
        orders = client2.get_orders(session_id)
        assert len(orders) == 2
        assert orders[0]["symbol"] in ("BTC-USD", "ETH-USD")
        assert orders[1]["symbol"] in ("BTC-USD", "ETH-USD")

    async def test_session_reload_preserves_positions(
        self,
        paper_client: SoDEXPaperPerpsClient,
        tmp_sessions: Path,
        sample_kline_dicts: list[dict],
    ) -> None:
        session_id = paper_client.create_session("pos_survival")
        paper_client.place_order(session_id, symbol="BTC-USD", side="BUY", quantity=1.0, price=101.0)
        await paper_client.process_klines(session_id, sample_kline_dicts)

        # New client
        client2 = SoDEXPaperPerpsClient(
            feeds=paper_client.feeds,
            sessions_dir=tmp_sessions,
        )

        positions = client2.get_positions(session_id)
        assert len(positions) == 1
        assert positions[0]["quantity"] > 0

        pnl = client2.get_pnl(session_id)
        assert pnl["realized_pnl"] is not None

    async def test_session_reload_preserves_pnl(
        self,
        paper_client: SoDEXPaperPerpsClient,
        tmp_sessions: Path,
    ) -> None:
        session_id = paper_client.create_session("pnl_survival")

        # Place and execute some trades to generate PnL
        paper_client.place_order(session_id, symbol="BTC-USD", side="BUY", quantity=1.0, price=50000.0)
        paper_client.place_order(session_id, symbol="BTC-USD", side="SELL", quantity=1.0, price=51000.0)

        # Save state
        paper_client._save_session_to_disk(paper_client.get_session(session_id))

        # New client
        client2 = SoDEXPaperPerpsClient(
            feeds=paper_client.feeds,
            sessions_dir=tmp_sessions,
        )

        pnl = client2.get_pnl(session_id)
        assert "realized_pnl" in pnl

    async def test_invalid_session_raises(self, paper_client: SoDEXPaperPerpsClient) -> None:
        with pytest.raises(PaperSessionNotFoundError):
            paper_client.get_session("nonexistent_session_12345")


# ---------------------------------------------------------------------------
# Order cancellation (VAL-PAPER-004)
# ---------------------------------------------------------------------------


class TestCancelOrder:
    """VAL-PAPER-004: Open order cancellation works."""
    pytestmark = pytest.mark.asyncio

    async def test_cancel_open_order(
        self,
        paper_client: SoDEXPaperPerpsClient,
    ) -> None:
        session_id = paper_client.create_session("cancel_test")
        order = paper_client.place_order(session_id, symbol="BTC-USD", side="BUY", quantity=1.0, price=50000.0)
        order_id = order["order_id"]

        cancelled = paper_client.cancel_order(session_id, order_id)
        assert cancelled["status"] == "CANCELLED"

    async def test_cancelled_order_not_filled(
        self,
        paper_client: SoDEXPaperPerpsClient,
        sample_kline_dicts: list[dict],
    ) -> None:
        session_id = paper_client.create_session("cancel_no_fill")
        order = paper_client.place_order(session_id, symbol="BTC-USD", side="BUY", quantity=1.0, price=101.0)
        order_id = order["order_id"]

        # Cancel before processing klines
        paper_client.cancel_order(session_id, order_id)

        # Process klines - should not fill cancelled order
        fills = await paper_client.process_klines(session_id, sample_kline_dicts)
        assert len(fills) == 0

        orders = paper_client.get_orders(session_id)
        cancelled_order = next(o for o in orders if o["order_id"] == order_id)
        assert cancelled_order["status"] == "CANCELLED"

    async def test_cancel_already_filled_order_raises(
        self,
        paper_client: SoDEXPaperPerpsClient,
        sample_kline_dicts: list[dict],
    ) -> None:
        session_id = paper_client.create_session("cancel_filled_raises")
        order = paper_client.place_order(session_id, symbol="BTC-USD", side="BUY", quantity=1.0, price=101.0)

        await paper_client.process_klines(session_id, sample_kline_dicts)

        with pytest.raises(PaperClientError, match="cannot cancel"):
            paper_client.cancel_order(session_id, order["order_id"])

    async def test_cancel_nonexistent_order_raises(
        self,
        paper_client: SoDEXPaperPerpsClient,
    ) -> None:
        session_id = paper_client.create_session("cancel_nonexistent")
        with pytest.raises(PaperClientError, match="not found"):
            paper_client.cancel_order(session_id, "nonexistent_order_123")


# ---------------------------------------------------------------------------
# Order expiry (VAL-PAPER-005)
# ---------------------------------------------------------------------------


class TestOrderExpiry:
    """VAL-PAPER-005: Order expiry transitions to EXPIRED."""
    pytestmark = pytest.mark.asyncio

    async def test_order_expires_after_time_in_force(
        self,
        paper_client: SoDEXPaperPerpsClient,
    ) -> None:
        session_id = paper_client.create_session("expiry_test")

        # Create an order with a short expiry by placing it and directly modifying
        order = paper_client.place_order(
            session_id,
            symbol="BTC-USD",
            side="BUY",
            quantity=1.0,
            price=1.0,  # Very low price, won't fill
            time_in_force="GTX",
        )
        order_id = order["order_id"]

        # Manually set the expires_at to the past
        session = paper_client.get_session(session_id)
        session.orders[order_id].expires_at = time.time() - 1  # expired 1 second ago
        paper_client._save_session_to_disk(session)

        # Process klines - should expire the order
        fills = await paper_client.process_klines(session_id, [])
        assert len(fills) == 0

        orders = paper_client.get_orders(session_id)
        expired_orders = [o for o in orders if o["order_id"] == order_id]
        assert len(expired_orders) == 1
        assert expired_orders[0]["status"] == "EXPIRED"

    async def test_expired_order_not_filled(
        self,
        paper_client: SoDEXPaperPerpsClient,
        sample_kline_dicts: list[dict],
    ) -> None:
        session_id = paper_client.create_session("expired_no_fill")

        order = paper_client.place_order(
            session_id,
            symbol="BTC-USD",
            side="BUY",
            quantity=1.0,
            price=101.0,
            time_in_force="GTX",
        )
        order_id = order["order_id"]

        # Set expiry to the past
        session = paper_client.get_session(session_id)
        session.orders[order_id].expires_at = time.time() - 1
        paper_client._save_session_to_disk(session)

        # Process klines that would have filled
        fills = await paper_client.process_klines(session_id, sample_kline_dicts)
        assert len(fills) == 0

        orders = paper_client.get_orders(session_id)
        expired_order = next(o for o in orders if o["order_id"] == order_id)
        assert expired_order["status"] == "EXPIRED"


# ---------------------------------------------------------------------------
# Funding cost simulation (VAL-PAPER-006)
# ---------------------------------------------------------------------------


class TestFundingCost:
    """VAL-PAPER-006: Funding cost simulation uses real funding rates."""
    pytestmark = pytest.mark.asyncio

    async def test_funding_cost_calculated_from_real_rates(
        self,
        paper_client: SoDEXPaperPerpsClient,
        sample_kline_dicts: list[dict],
    ) -> None:
        """Funding cost references real SoDEX funding rate data."""
        session_id = paper_client.create_session("funding_test")

        # Place and fill a position
        paper_client.place_order(session_id, symbol="BTC-USD", side="BUY", quantity=1.0, price=101.0)
        await paper_client.process_klines(session_id, [sample_kline_dicts[0]])

        # Mock mark_prices to return real funding rates
        paper_client.feeds.fetch_mark_prices.return_value = [
            {"symbol": "BTC-USD", "markPrice": "101000", "fundingRate": "0.0001"},
        ]

        # Process funding
        funding_events = await paper_client.process_funding(session_id, force=True)
        assert len(funding_events) >= 1
        assert funding_events[0]["symbol"] == "BTC-USD"
        assert funding_events[0]["funding_rate"] == 0.0001
        assert funding_events[0]["cost"] != 0.0

    async def test_funding_cost_non_zero_for_open_positions(
        self,
        paper_client: SoDEXPaperPerpsClient,
        sample_kline_dicts: list[dict],
    ) -> None:
        session_id = paper_client.create_session("funding_nonzero")
        paper_client.place_order(session_id, symbol="BTC-USD", side="BUY", quantity=10.0, price=101.0)
        await paper_client.process_klines(session_id, [sample_kline_dicts[0]])

        paper_client.feeds.fetch_mark_prices.return_value = [
            {"symbol": "BTC-USD", "markPrice": "105000", "fundingRate": "0.0001"},
        ]

        funding_events = await paper_client.process_funding(session_id, force=True)
        assert len(funding_events) > 0
        # Long position with positive funding rate should have negative cost (longs pay shorts)
        assert funding_events[0]["cost"] < 0

    async def test_no_funding_when_no_position(
        self,
        paper_client: SoDEXPaperPerpsClient,
    ) -> None:
        session_id = paper_client.create_session("no_pos_funding")
        funding_events = await paper_client.process_funding(session_id, force=True)
        assert len(funding_events) == 0

    async def test_funding_rate_from_mark_prices(
        self,
        paper_client: SoDEXPaperPerpsClient,
        sample_kline_dicts: list[dict],
    ) -> None:
        """Verify funding references real mark_prices data."""
        session_id = paper_client.create_session("funding_source")
        paper_client.place_order(session_id, symbol="BTC-USD", side="SELL", quantity=1.0, price=101.0)
        await paper_client.process_klines(session_id, [sample_kline_dicts[0]])

        # Mock funding data
        paper_client.feeds.fetch_mark_prices.return_value = [
            {"symbol": "BTC-USD", "markPrice": "100000", "fundingRate": "0.0002"},
        ]

        events = await paper_client.process_funding(session_id, force=True)
        assert len(events) == 1
        assert events[0]["funding_rate"] == 0.0002

    async def test_funding_does_not_apply_before_interval(
        self,
        paper_client: SoDEXPaperPerpsClient,
        sample_kline_dicts: list[dict],
    ) -> None:
        """Funding only applies on 8h intervals."""
        session_id = paper_client.create_session("funding_interval")
        paper_client.place_order(session_id, symbol="BTC-USD", side="BUY", quantity=1.0, price=101.0)
        await paper_client.process_klines(session_id, [sample_kline_dicts[0]])

        paper_client.feeds.fetch_mark_prices.return_value = [
            {"symbol": "BTC-USD", "markPrice": "105000", "fundingRate": "0.0001"},
        ]

        # First application
        events1 = await paper_client.process_funding(session_id, force=True)
        assert len(events1) == 1

        # Second call without force should not apply (not enough time passed)
        events2 = await paper_client.process_funding(session_id, force=False)
        assert len(events2) == 0


# ---------------------------------------------------------------------------
# Empty klines handling (VAL-PAPER-010)
# ---------------------------------------------------------------------------


class TestEmptyKlines:
    """VAL-PAPER-010: Empty klines queued (not crash) with warning log."""
    pytestmark = pytest.mark.asyncio

    async def test_empty_klines_dataframe_does_not_crash(
        self,
        paper_client: SoDEXPaperPerpsClient,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        session_id = paper_client.create_session("empty_kline_df")
        paper_client.place_order(session_id, symbol="BTC-USD", side="BUY", quantity=1.0, price=50000.0)

        empty_df = pd.DataFrame(
            {"open": pd.Series(dtype=float), "high": pd.Series(dtype=float),
             "low": pd.Series(dtype=float), "close": pd.Series(dtype=float),
             "volume": pd.Series(dtype=float), "quote_volume": pd.Series(dtype=float)},
        )
        empty_df.index = pd.DatetimeIndex([], name="timestamp")

        with caplog.at_level(logging.WARNING):
            fills = await paper_client.process_klines(session_id, empty_df)

        assert len(fills) == 0
        assert any("Empty klines" in msg for msg in caplog.messages)

    async def test_empty_klines_list_does_not_crash(
        self,
        paper_client: SoDEXPaperPerpsClient,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        session_id = paper_client.create_session("empty_kline_list")
        paper_client.place_order(session_id, symbol="BTC-USD", side="BUY", quantity=1.0, price=50000.0)

        with caplog.at_level(logging.WARNING):
            fills = await paper_client.process_klines(session_id, [])

        assert len(fills) == 0
        assert any("Empty klines" in msg for msg in caplog.messages)

    async def test_order_remains_open_with_empty_klines(
        self,
        paper_client: SoDEXPaperPerpsClient,
    ) -> None:
        session_id = paper_client.create_session("open_with_empty")
        order = paper_client.place_order(session_id, symbol="BTC-USD", side="BUY", quantity=1.0, price=50000.0)

        await paper_client.process_klines(session_id, [])

        fetched = paper_client.get_order(session_id, order["order_id"])
        assert fetched["status"] == "OPEN"

    async def test_invalid_klines_type_raises(
        self,
        paper_client: SoDEXPaperPerpsClient,
    ) -> None:
        session_id = paper_client.create_session("invalid_klines_type")
        with pytest.raises(PaperClientError, match="klines must be DataFrame or list"):
            await paper_client.process_klines(session_id, "not_valid")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Multiple parallel sessions (VAL-PAPER-011)
# ---------------------------------------------------------------------------


class TestMultipleSessions:
    """VAL-PAPER-011: Multiple parallel sessions have isolated state."""
    pytestmark = pytest.mark.asyncio

    async def test_sessions_have_independent_orders(
        self,
        paper_client: SoDEXPaperPerpsClient,
    ) -> None:
        session_a = paper_client.create_session("session_a")
        session_b = paper_client.create_session("session_b")

        paper_client.place_order(session_a, symbol="BTC-USD", side="BUY", quantity=1.0, price=50000.0)
        paper_client.place_order(session_b, symbol="ETH-USD", side="SELL", quantity=2.0, price=3000.0)

        orders_a = paper_client.get_orders(session_a)
        orders_b = paper_client.get_orders(session_b)

        assert len(orders_a) == 1
        assert len(orders_b) == 1
        assert orders_a[0]["symbol"] == "BTC-USD"
        assert orders_b[0]["symbol"] == "ETH-USD"

    async def test_sessions_have_independent_positions(
        self,
        paper_client: SoDEXPaperPerpsClient,
        sample_kline_dicts: list[dict],
    ) -> None:
        session_a = paper_client.create_session("pos_a")
        session_b = paper_client.create_session("pos_b")

        paper_client.place_order(session_a, symbol="BTC-USD", side="BUY", quantity=1.0, price=101.0)
        paper_client.place_order(session_b, symbol="ETH-USD", side="SELL", quantity=2.0, price=100.0)

        await paper_client.process_klines(session_a, sample_kline_dicts)
        await paper_client.process_klines(session_b, sample_kline_dicts)

        positions_a = paper_client.get_positions(session_a)
        paper_client.get_positions(session_b)  # verify no error

        # Session A has BTC position, session B might not have filled (ETH not in klines)
        assert len(positions_a) > 0
        assert positions_a[0]["symbol"] == "BTC-USD"

    async def test_sessions_have_independent_pnl(
        self,
        paper_client: SoDEXPaperPerpsClient,
    ) -> None:
        session_a = paper_client.create_session("pnl_a")
        session_b = paper_client.create_session("pnl_b")

        pnl_a = paper_client.get_pnl(session_a)
        pnl_b = paper_client.get_pnl(session_b)

        # Both should have zero PnL initially
        assert pnl_a["total_pnl"] == 0.0
        assert pnl_b["total_pnl"] == 0.0

        # Add an order to session_a only
        paper_client.place_order(session_a, symbol="BTC-USD", side="BUY", quantity=1.0, price=50000.0)

        pnl_b2 = paper_client.get_pnl(session_b)

        # Session B should still have zero PnL
        assert pnl_b2["total_pnl"] == 0.0


# ---------------------------------------------------------------------------
# Session status (VAL-CLI-016)
# ---------------------------------------------------------------------------


class TestSessionStatus:
    """VAL-CLI-016: paper-status returns position/PnL/orders."""
    pytestmark = pytest.mark.asyncio

    async def test_session_status_has_required_fields(
        self,
        paper_client: SoDEXPaperPerpsClient,
    ) -> None:
        session_id = paper_client.create_session("status_test")
        paper_client.place_order(session_id, symbol="BTC-USD", side="BUY", quantity=1.0, price=50000.0)

        status = paper_client.get_session_status(session_id)
        assert "session_id" in status
        assert "position" in status
        assert "pnl" in status
        assert "orders" in status

    async def test_session_status_position_is_list(
        self,
        paper_client: SoDEXPaperPerpsClient,
    ) -> None:
        session_id = paper_client.create_session("status_pos")
        status = paper_client.get_session_status(session_id)
        assert isinstance(status["position"], list)

    async def test_session_status_pnl_has_expected_fields(
        self,
        paper_client: SoDEXPaperPerpsClient,
    ) -> None:
        session_id = paper_client.create_session("status_pnl")
        status = paper_client.get_session_status(session_id)
        pnl = status["pnl"]
        assert "realized_pnl" in pnl
        assert "unrealized_pnl" in pnl
        assert "total_pnl" in pnl

    async def test_session_status_orders_is_list(
        self,
        paper_client: SoDEXPaperPerpsClient,
    ) -> None:
        session_id = paper_client.create_session("status_orders")
        paper_client.place_order(session_id, symbol="BTC-USD", side="BUY", quantity=1.0, price=50000.0)

        status = paper_client.get_session_status(session_id)
        assert isinstance(status["orders"], list)
        assert len(status["orders"]) == 1


# ---------------------------------------------------------------------------
# list_sessions
# ---------------------------------------------------------------------------


class TestListSessions:
    """Listing sessions works correctly."""
    pytestmark = pytest.mark.asyncio

    async def test_list_sessions_returns_known_sessions(
        self,
        paper_client: SoDEXPaperPerpsClient,
    ) -> None:
        paper_client.create_session("alpha")
        paper_client.create_session("beta")

        sessions = paper_client.list_sessions()
        assert len(sessions) >= 2
        names = {s["name"] for s in sessions}
        assert "alpha" in names
        assert "beta" in names

    async def test_list_sessions_has_expected_fields(
        self,
        paper_client: SoDEXPaperPerpsClient,
    ) -> None:
        paper_client.create_session("field_check")

        sessions = paper_client.list_sessions()
        for s in sessions:
            assert "session_id" in s
            assert "name" in s
            assert "created_at" in s
            assert "order_count" in s
            assert "pnl" in s


# ---------------------------------------------------------------------------
# _compute_fill_price unit tests
# ---------------------------------------------------------------------------


class TestComputeFillPrice:
    """Unit tests for the fill price computation."""

    def test_buy_fills_when_low_below_limit(self) -> None:
        kline = {"o": "100", "h": "105", "l": "99", "c": "103"}
        price, did_fill = _compute_fill_price(kline, PaperOrderSide.BUY, 100.5)
        assert did_fill
        assert price <= 100.5

    def test_sell_fills_when_high_above_limit(self) -> None:
        kline = {"o": "100", "h": "105", "l": "99", "c": "103"}
        price, did_fill = _compute_fill_price(kline, PaperOrderSide.SELL, 100.5)
        assert did_fill
        assert price >= 100.5

    def test_buy_no_fill_when_low_above_limit(self) -> None:
        kline = {"o": "101", "h": "105", "l": "100.5", "c": "103"}
        price, did_fill = _compute_fill_price(kline, PaperOrderSide.BUY, 100.0)
        assert not did_fill
        assert price == 0.0

    def test_sell_no_fill_when_high_below_limit(self) -> None:
        kline = {"o": "100", "h": "102", "l": "99", "c": "101"}
        price, did_fill = _compute_fill_price(kline, PaperOrderSide.SELL, 103.0)
        assert not did_fill
        assert price == 0.0

    # ------------------------------------------------------------------
    # MARKET order fill price tests
    # ------------------------------------------------------------------

    def test_market_buy_always_fills_at_close(self) -> None:
        """MARKET BUY always fills, using the kline close price."""
        kline = {"o": "100", "h": "105", "l": "99", "c": "103"}
        price, did_fill = _compute_fill_price(
            kline, PaperOrderSide.BUY, 0.0, PaperOrderType.MARKET
        )
        assert did_fill
        assert price == 103.0

    def test_market_sell_always_fills_at_close(self) -> None:
        """MARKET SELL always fills, using the kline close price."""
        kline = {"o": "100", "h": "105", "l": "99", "c": "103"}
        price, did_fill = _compute_fill_price(
            kline, PaperOrderSide.SELL, 0.0, PaperOrderType.MARKET
        )
        assert did_fill
        assert price == 103.0

    def test_market_buy_fills_regardless_of_limit_price(self) -> None:
        """MARKET BUY fills even when limit_price is below the kline low."""
        kline = {"o": "100", "h": "105", "l": "99", "c": "103"}
        # Limit price below low — would not fill for LIMIT, but MARKET fills
        price, did_fill = _compute_fill_price(
            kline, PaperOrderSide.BUY, 50.0, PaperOrderType.MARKET
        )
        assert did_fill
        assert price == 103.0

    def test_market_sell_fills_regardless_of_limit_price(self) -> None:
        """MARKET SELL fills even when limit_price is above the kline high."""
        kline = {"o": "100", "h": "105", "l": "99", "c": "103"}
        # Limit price above high — would not fill for LIMIT, but MARKET fills
        price, did_fill = _compute_fill_price(
            kline, PaperOrderSide.SELL, 200.0, PaperOrderType.MARKET
        )
        assert did_fill
        assert price == 103.0

    def test_market_fill_with_zero_close_still_fills(self) -> None:
        """MARKET fill with zero close still succeeds (edge case)."""
        kline = {"o": "0", "h": "0", "l": "0", "c": "0"}
        price, did_fill = _compute_fill_price(
            kline, PaperOrderSide.BUY, 0.0, PaperOrderType.MARKET
        )
        assert did_fill
        assert price == 0.0

    def test_market_fill_uses_close_not_open(self) -> None:
        """MARKET fill uses close price, not open, high, or low."""
        kline = {"o": "100", "h": "110", "l": "95", "c": "108"}
        price, did_fill = _compute_fill_price(
            kline, PaperOrderSide.BUY, 0.0, PaperOrderType.MARKET
        )
        assert did_fill
        # Close is 108, not open (100), high (110), or low (95)
        assert price == 108.0

    def test_limit_behavior_unchanged_with_market_param_default(self) -> None:
        """LIMIT order behavior is unchanged when order_type is not specified (default)."""
        kline = {"o": "100", "h": "105", "l": "99", "c": "103"}
        price, did_fill = _compute_fill_price(kline, PaperOrderSide.BUY, 100.5)
        assert did_fill
        assert price <= 100.5


class TestComputeFundingCost:
    """Unit tests for funding cost computation."""

    def test_long_pays_funding(self) -> None:
        from siglab.live.paper_client import PaperPosition
        pos = PaperPosition(symbol="BTC-USD", quantity=1.0, entry_price=50000.0)
        cost = _compute_funding_cost(pos, 50000.0, 0.0001)
        # Long pays: -1 * 50000 * 0.0001 = -5.0
        assert cost == -5.0

    def test_short_receives_funding(self) -> None:
        from siglab.live.paper_client import PaperPosition
        pos = PaperPosition(symbol="BTC-USD", quantity=-1.0, entry_price=50000.0)
        cost = _compute_funding_cost(pos, 50000.0, 0.0001)
        # Short receives: 1 * 50000 * 0.0001 = 5.0
        assert cost == 5.0

    def test_no_position_no_cost(self) -> None:
        from siglab.live.paper_client import PaperPosition
        pos = PaperPosition(symbol="BTC-USD", quantity=0.0, entry_price=0.0)
        cost = _compute_funding_cost(pos, 50000.0, 0.0001)
        assert cost == 0.0

    def test_zero_funding_rate_no_cost(self) -> None:
        from siglab.live.paper_client import PaperPosition
        pos = PaperPosition(symbol="BTC-USD", quantity=1.0, entry_price=50000.0)
        cost = _compute_funding_cost(pos, 50000.0, 0.0)
        assert cost == 0.0


# ---------------------------------------------------------------------------
# MARKET order tests (placement, fill, edge cases)
# ---------------------------------------------------------------------------


class TestMarketOrder:
    """MARKET order placement, fill behavior, and edge cases."""
    pytestmark = pytest.mark.asyncio

    async def test_place_market_order_without_price_succeeds(
        self,
        paper_client: SoDEXPaperPerpsClient,
    ) -> None:
        """MARKET order can be placed without specifying a price."""
        session_id = paper_client.create_session("market_no_price")
        order = paper_client.place_order(
            session_id,
            symbol="BTC-USD",
            side="BUY",
            quantity=1.0,
            order_type="MARKET",
        )
        assert order["order_id"] is not None
        assert order["order_type"] == "MARKET"
        assert order["price"] == 0.0  # No price for MARKET orders
        assert order["status"] == "OPEN"

    async def test_place_market_order_with_price_still_works(
        self,
        paper_client: SoDEXPaperPerpsClient,
    ) -> None:
        """MARKET order with a price still places (price is unused)."""
        session_id = paper_client.create_session("market_with_price")
        order = paper_client.place_order(
            session_id,
            symbol="BTC-USD",
            side="SELL",
            quantity=1.0,
            order_type="MARKET",
            price=50000.0,  # Price provided but should be ignored for MARKET
        )
        assert order["order_type"] == "MARKET"
        # Price field stores whatever was passed despite being unused
        assert order["status"] == "OPEN"

    async def test_market_buy_fills_at_kline_close(
        self,
        paper_client: SoDEXPaperPerpsClient,
        sample_kline_dicts: list[dict],
    ) -> None:
        """MARKET BUY fills at the kline close price."""
        session_id = paper_client.create_session("market_buy_fill")
        paper_client.place_order(
            session_id,
            symbol="BTC-USD",
            side="BUY",
            quantity=1.0,
            order_type="MARKET",
        )

        fills = await paper_client.process_klines(session_id, sample_kline_dicts)
        assert len(fills) == 1
        assert fills[0]["status"] == "FILLED"
        # First kline close is 101.0
        assert fills[0]["fill_price"] == 101.0

    async def test_market_sell_fills_at_kline_close(
        self,
        paper_client: SoDEXPaperPerpsClient,
        sample_kline_dicts: list[dict],
    ) -> None:
        """MARKET SELL fills at the kline close price."""
        session_id = paper_client.create_session("market_sell_fill")
        paper_client.place_order(
            session_id,
            symbol="BTC-USD",
            side="SELL",
            quantity=1.0,
            order_type="MARKET",
        )

        fills = await paper_client.process_klines(session_id, sample_kline_dicts)
        assert len(fills) == 1
        assert fills[0]["status"] == "FILLED"
        # First kline close is 101.0
        assert fills[0]["fill_price"] == 101.0

    async def test_market_buy_fill_creates_position(
        self,
        paper_client: SoDEXPaperPerpsClient,
        sample_kline_dicts: list[dict],
    ) -> None:
        """MARKET BUY creates a long position at the close price."""
        session_id = paper_client.create_session("market_buy_pos")
        paper_client.place_order(
            session_id,
            symbol="BTC-USD",
            side="BUY",
            quantity=2.5,
            order_type="MARKET",
        )

        await paper_client.process_klines(session_id, sample_kline_dicts)

        positions = paper_client.get_positions(session_id)
        assert len(positions) == 1
        assert positions[0]["symbol"] == "BTC-USD"
        assert positions[0]["quantity"] == 2.5
        assert positions[0]["entry_price"] == 101.0  # Close of first kline

    async def test_market_sell_fill_creates_position(
        self,
        paper_client: SoDEXPaperPerpsClient,
        sample_kline_dicts: list[dict],
    ) -> None:
        """MARKET SELL creates a short position at the close price."""
        session_id = paper_client.create_session("market_sell_pos")
        paper_client.place_order(
            session_id,
            symbol="BTC-USD",
            side="SELL",
            quantity=1.5,
            order_type="MARKET",
        )

        await paper_client.process_klines(session_id, sample_kline_dicts)

        positions = paper_client.get_positions(session_id)
        assert len(positions) == 1
        assert positions[0]["symbol"] == "BTC-USD"
        assert positions[0]["quantity"] == -1.5
        assert positions[0]["entry_price"] == 101.0  # Close of first kline

    async def test_market_order_fills_only_once(
        self,
        paper_client: SoDEXPaperPerpsClient,
        sample_kline_dicts: list[dict],
    ) -> None:
        """MARKET order fills on the first kline and doesn't fill again."""
        session_id = paper_client.create_session("market_once")
        paper_client.place_order(
            session_id,
            symbol="BTC-USD",
            side="BUY",
            quantity=1.0,
            order_type="MARKET",
        )

        fills = await paper_client.process_klines(session_id, sample_kline_dicts)
        assert len(fills) == 1  # Only fills once
        assert fills[0]["status"] == "FILLED"

        # Status shows FILLED
        order = paper_client.get_order(session_id, fills[0]["order_id"])
        assert order["status"] == "FILLED"

    async def test_market_order_fills_regardless_of_price_level(
        self,
        paper_client: SoDEXPaperPerpsClient,
        sample_kline_dicts: list[dict],
    ) -> None:
        """MARKET order always fills even when price limit would not cross kline."""
        session_id = paper_client.create_session("market_any_price")
        # BUY MARKET with effective limit of 0 — would never fill as LIMIT
        paper_client.place_order(
            session_id,
            symbol="BTC-USD",
            side="BUY",
            quantity=1.0,
            order_type="MARKET",
        )

        fills = await paper_client.process_klines(session_id, sample_kline_dicts)
        assert len(fills) == 1
        assert fills[0]["status"] == "FILLED"

    async def test_market_order_buy_at_close_with_dataframe(
        self,
        paper_client: SoDEXPaperPerpsClient,
        sample_kline_df: pd.DataFrame,
    ) -> None:
        """MARKET BUY fills correctly with DataFrame-based klines."""
        session_id = paper_client.create_session("market_df")
        paper_client.place_order(
            session_id,
            symbol="BTC-USD",
            side="BUY",
            quantity=1.0,
            order_type="MARKET",
        )

        fills = await paper_client.process_klines(session_id, sample_kline_df)
        assert len(fills) == 1
        assert fills[0]["status"] == "FILLED"
        # First row close = 101.0 (from sample_kline_dicts)
        assert fills[0]["fill_price"] == 101.0

    async def test_market_order_with_ioc_expires_if_not_immediately_filled(
        self,
        paper_client: SoDEXPaperPerpsClient,
    ) -> None:
        """MARKET IOC order without klines eventually expires."""
        session_id = paper_client.create_session("market_ioc")
        order = paper_client.place_order(
            session_id,
            symbol="BTC-USD",
            side="BUY",
            quantity=1.0,
            order_type="MARKET",
            time_in_force="IOC",
        )
        order_id = order["order_id"]

        # Set expiry to past so it expires even without klines
        session = paper_client.get_session(session_id)
        session.orders[order_id].expires_at = time.time() - 1
        paper_client._save_session_to_disk(session)

        # Process empty klines to trigger expiry check
        fills = await paper_client.process_klines(session_id, [])
        assert len(fills) == 0

        # Order should be EXPIRED now
        orders = paper_client.get_orders(session_id)
        expired_order = next(o for o in orders if o["order_id"] == order_id)
        assert expired_order["status"] == "EXPIRED"

    async def test_market_order_fill_price_is_close_as_number(
        self,
        paper_client: SoDEXPaperPerpsClient,
    ) -> None:
        """Verify MARKET fill price is the kline close as a float, not 0.0."""
        session_id = paper_client.create_session("market_close_fill")
        paper_client.place_order(
            session_id,
            symbol="BTC-USD",
            side="BUY",
            quantity=1.0,
            order_type="MARKET",
        )

        # Single kline with known close
        kline = {"t": 1000, "o": "100.0", "h": "105.0", "l": "99.0", "c": "103.5"}
        fills = await paper_client.process_klines(session_id, [kline])
        assert len(fills) == 1
        assert fills[0]["fill_price"] == 103.5
        assert fills[0]["fill_price"] != 0.0  # The bug: was 0.0 for BUY MARKET

    async def test_market_buy_then_limit_sell_position_reduction(
        self,
        paper_client: SoDEXPaperPerpsClient,
        sample_kline_dicts: list[dict],
    ) -> None:
        """MARKET BUY followed by LIMIT SELL to reduce position works."""
        session_id = paper_client.create_session("market_then_limit")
        # MARKET BUY 2 units
        paper_client.place_order(
            session_id,
            symbol="BTC-USD",
            side="BUY",
            quantity=2.0,
            order_type="MARKET",
        )
        fills1 = await paper_client.process_klines(session_id, [sample_kline_dicts[0]])
        assert len(fills1) == 1

        # Position should be 2.0 at entry price 101.0
        pos = paper_client.get_positions(session_id)
        assert len(pos) == 1
        assert pos[0]["quantity"] == 2.0
        assert pos[0]["entry_price"] == 101.0

        # LIMIT SELL at 103 (fills on second kline where high=105 >= 103)
        paper_client.place_order(
            session_id,
            symbol="BTC-USD",
            side="SELL",
            quantity=2.0,
            price=103.0,
            order_type="LIMIT",
        )
        fills2 = await paper_client.process_klines(session_id, [sample_kline_dicts[1]])
        assert len(fills2) == 1

        # Position should be closed
        pos2 = paper_client.get_positions(session_id)
        assert len(pos2) == 0

        # Should have realized PnL
        pnl = paper_client.get_pnl(session_id)
        assert pnl["realized_pnl"] > 0  # Bought at 101, sold at >=103


# ---------------------------------------------------------------------------
# Slippage model tests
# ---------------------------------------------------------------------------


class TestSlippage:
    """Slippage is applied to market order fills."""

    @pytest.fixture
    def no_slip_client(self, tmp_sessions: Path, mock_feeds: MagicMock) -> SoDEXPaperPerpsClient:
        return SoDEXPaperPerpsClient(
            feeds=mock_feeds, sessions_dir=tmp_sessions,
            slippage_bps=0.0, min_notional_usd=0.0,
        )

    @pytest.fixture
    def slip_client(self, tmp_sessions: Path, mock_feeds: MagicMock) -> SoDEXPaperPerpsClient:
        return SoDEXPaperPerpsClient(
            feeds=mock_feeds, sessions_dir=tmp_sessions,
            slippage_bps=100.0, min_notional_usd=0.0,  # 100 bps = 1%
        )

    async def test_market_buy_gets_worse_fill_with_slippage(
        self, slip_client: SoDEXPaperPerpsClient,
    ) -> None:
        """MARKET BUY fill price is higher than kline close (buyer pays more)."""
        sid = slip_client.create_session("slip_buy")
        slip_client.place_order(sid, symbol="BTC-USD", side="BUY", quantity=1.0, order_type="MARKET")
        kline = {"t": 1, "o": "100", "h": "105", "l": "99", "c": "100.0"}
        fills = await slip_client.process_klines(sid, [kline])
        assert len(fills) == 1
        assert fills[0]["fill_price"] > 100.0  # slippage makes it worse for buyer

    async def test_market_sell_gets_worse_fill_with_slippage(
        self, slip_client: SoDEXPaperPerpsClient,
    ) -> None:
        """MARKET SELL fill price is lower than kline close (seller gets less)."""
        sid = slip_client.create_session("slip_sell")
        slip_client.place_order(sid, symbol="BTC-USD", side="SELL", quantity=1.0, order_type="MARKET")
        kline = {"t": 1, "o": "100", "h": "105", "l": "99", "c": "100.0"}
        fills = await slip_client.process_klines(sid, [kline])
        assert len(fills) == 1
        assert fills[0]["fill_price"] < 100.0

    async def test_slippage_amount_matches_bps(
        self, slip_client: SoDEXPaperPerpsClient,
    ) -> None:
        """Fill price delta equals close * slippage_bps / 10_000."""
        sid = slip_client.create_session("slip_amount")
        slip_client.place_order(sid, symbol="BTC-USD", side="BUY", quantity=1.0, order_type="MARKET")
        kline = {"t": 1, "o": "100", "h": "105", "l": "99", "c": "1000.0"}
        fills = await slip_client.process_klines(sid, [kline])
        expected_slip = 1000.0 * 100.0 / 10_000  # = 10.0
        assert fills[0]["fill_price"] == pytest.approx(1000.0 + expected_slip)

    async def test_zero_slippage_fills_at_close(
        self, no_slip_client: SoDEXPaperPerpsClient,
    ) -> None:
        """With slippage_bps=0, market fill equals kline close."""
        sid = no_slip_client.create_session("no_slip")
        no_slip_client.place_order(sid, symbol="BTC-USD", side="BUY", quantity=1.0, order_type="MARKET")
        kline = {"t": 1, "o": "100", "h": "105", "l": "99", "c": "103.5"}
        fills = await no_slip_client.process_klines(sid, [kline])
        assert fills[0]["fill_price"] == 103.5

    async def test_slippage_not_applied_to_limit_orders(
        self, slip_client: SoDEXPaperPerpsClient,
    ) -> None:
        """LIMIT orders are not affected by slippage."""
        sid = slip_client.create_session("slip_limit")
        slip_client.place_order(sid, symbol="BTC-USD", side="BUY", quantity=1.0, price=100.0)
        kline = {"t": 1, "o": "100", "h": "105", "l": "95", "c": "103.0"}
        fills = await slip_client.process_klines(sid, [kline])
        assert len(fills) == 1
        # LIMIT fill at 100.0 (no slippage adjustment)
        assert fills[0]["fill_price"] <= 100.0


# ---------------------------------------------------------------------------
# Minimum notional enforcement tests
# ---------------------------------------------------------------------------


class TestMinNotional:
    """Orders below minimum notional are cancelled at fill time."""

    @pytest.fixture
    def min_notional_client(self, tmp_sessions: Path, mock_feeds: MagicMock) -> SoDEXPaperPerpsClient:
        return SoDEXPaperPerpsClient(
            feeds=mock_feeds, sessions_dir=tmp_sessions,
            slippage_bps=0.0, min_notional_usd=50.0,
        )

    async def test_small_order_cancelled_below_minimum(
        self, min_notional_client: SoDEXPaperPerpsClient,
    ) -> None:
        """Order with notional < min_notional_usd is cancelled."""
        sid = min_notional_client.create_session("min_cancel")
        min_notional_client.place_order(
            sid, symbol="BTC-USD", side="BUY", quantity=0.1, order_type="MARKET",
        )
        kline = {"t": 1, "o": "100", "h": "105", "l": "99", "c": "100.0"}
        fills = await min_notional_client.process_klines(sid, [kline])
        assert len(fills) == 0  # no fill event
        orders = min_notional_client.get_orders(sid)
        assert orders[0]["status"] == "CANCELLED"

    async def test_order_above_minimum_fills_normally(
        self, min_notional_client: SoDEXPaperPerpsClient,
    ) -> None:
        """Order with notional >= min_notional_usd fills normally."""
        sid = min_notional_client.create_session("min_fill")
        min_notional_client.place_order(
            sid, symbol="BTC-USD", side="BUY", quantity=1.0, order_type="MARKET",
        )
        kline = {"t": 1, "o": "100", "h": "105", "l": "99", "c": "100.0"}
        fills = await min_notional_client.process_klines(sid, [kline])
        assert len(fills) == 1
        assert fills[0]["status"] == "FILLED"

    async def test_cancelled_below_minimum_no_position_created(
        self, min_notional_client: SoDEXPaperPerpsClient,
    ) -> None:
        """Cancelled order does not create a position."""
        sid = min_notional_client.create_session("min_no_pos")
        min_notional_client.place_order(
            sid, symbol="BTC-USD", side="BUY", quantity=0.1, order_type="MARKET",
        )
        kline = {"t": 1, "o": "100", "h": "105", "l": "99", "c": "100.0"}
        await min_notional_client.process_klines(sid, [kline])
        positions = min_notional_client.get_positions(sid)
        assert len(positions) == 0

    async def test_exact_minimum_notional_fills(
        self, tmp_sessions: Path, mock_feeds: MagicMock,
    ) -> None:
        """Order with notional exactly equal to minimum still fills."""
        client = SoDEXPaperPerpsClient(
            feeds=mock_feeds, sessions_dir=tmp_sessions,
            slippage_bps=0.0, min_notional_usd=100.0,
        )
        sid = client.create_session("exact_min")
        client.place_order(
            sid, symbol="BTC-USD", side="BUY", quantity=1.0, order_type="MARKET",
        )
        kline = {"t": 1, "o": "100", "h": "105", "l": "99", "c": "100.0"}
        fills = await client.process_klines(sid, [kline])
        assert len(fills) == 1
        assert fills[0]["status"] == "FILLED"

    async def test_min_notional_with_slippage_combined(
        self, tmp_sessions: Path, mock_feeds: MagicMock,
    ) -> None:
        """Slippage-adjusted price used in notional check."""
        client = SoDEXPaperPerpsClient(
            feeds=mock_feeds, sessions_dir=tmp_sessions,
            slippage_bps=15.0, min_notional_usd=100.0,
        )
        sid = client.create_session("combined")
        # qty=1, close=99 → notional = 99 * (1 + 15/10000) = 99.1485 → below 100
        client.place_order(
            sid, symbol="BTC-USD", side="BUY", quantity=1.0, order_type="MARKET",
        )
        kline = {"t": 1, "o": "99", "h": "100", "l": "98", "c": "99.0"}
        fills = await client.process_klines(sid, [kline])
        assert len(fills) == 0
        orders = client.get_orders(sid)
        assert orders[0]["status"] == "CANCELLED"
