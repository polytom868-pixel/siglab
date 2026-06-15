"""Tests for siglab.tui.data_views dataclasses and closes_from_klines."""

from __future__ import annotations

import pytest

from siglab.tui.data_views import (
    GraphEdge,
    GraphNode,
    KlineView,
    OrderBookView,
    OrderView,
    PnlSnapshot,
    PositionView,
    RiskSnapshot,
    StrategyEntry,
    SymbolEntry,
    TickerView,
    closes_from_klines,
)


@pytest.mark.parametrize(
    "cls,d,attrs",
    [
        (TickerView, {"symbol": "BTC", "lastPrice": "50000", "priceChangePercent": "2.5", "volume": "1000"},
         {"symbol": "BTC", "last_price": 50000.0, "price_change_pct": 2.5, "volume": 1000.0}),
        (TickerView, {}, {"symbol": "?", "last_price": 0.0}),
        (KlineView, {"open": 100, "high": 110, "low": 95, "close": 105, "volume": 1000},
         {"open": 100.0, "high": 110.0, "low": 95.0, "close": 105.0, "volume": 1000.0}),
        (OrderView, {"order_id": "o1", "symbol": "BTC", "side": "buy", "order_type": "market",
                      "quantity": 0.1, "price": 50000, "fill_price": 50010, "status": "filled",
                      "created_at": 1234567890},
         {"order_id": "o1", "fill_price": 50010.0, "status": "filled"}),
        (PositionView, {"symbol": "ETH", "quantity": 2.0, "entry_price": 3000, "unrealized_pnl": 50.0},
         {"symbol": "ETH", "quantity": 2.0, "unrealized_pnl": 50.0}),
        (PnlSnapshot, {"realized_pnl": 100, "unrealized_pnl": -20, "total_pnl": 80,
                        "total_funding_cost": -5, "open_position_count": 3},
         {"realized": 100.0, "unrealized": -20.0, "total": 80.0, "funding": -5.0, "open_count": 3}),
        (PnlSnapshot, {}, {"realized": 0.0, "unrealized": 0.0, "total": 0.0}),
        (GraphNode, {"id": "n1", "label": "Node 1", "kind": "evidence", "count": 5},
         {"id": "n1", "count": 5}),
        (GraphEdge, {"source": "a", "target": "b", "confidence": 0.9},
         {"source": "a", "confidence": 0.9, "label": "linked"}),
        (StrategyEntry, {"spec_hash": "abc123", "family": "trend", "track": "trend_signals",
                          "passed": True, "aggregate_score": 0.85},
         {"spec_hash": "abc123", "passed": True, "aggregate_score": 0.85}),
        (RiskSnapshot, {"composite_score": 0.75, "strategy_count": 3, "alerts": [{"severity": "warning"}]},
         {"composite_score": 0.75, "strategy_count": 3, "alerts_len": 1}),
    ],
)
def test_from_dict(cls, d, attrs) -> None:
    obj = cls.from_dict(d)
    for attr, value in attrs.items():
        if attr == "alerts_len":
            assert len(getattr(obj, "alerts")) == value
        else:
            assert getattr(obj, attr) == value


def test_ticker_view_raw_access() -> None:
    d = {"symbol": "ETH"}
    assert TickerView.from_dict(d).raw is d


def test_symbol_entry_from_ticker() -> None:
    tv = TickerView.from_dict({"symbol": "BTC", "lastPrice": 50000, "priceChangePercent": 1.0, "volume": 500})
    se = SymbolEntry.from_ticker(tv)
    assert se.name == "BTC" and se.price == 50000.0

def test_kline_view_frozen() -> None:
    kv = KlineView.from_dict({"close": 1.0})
    with pytest.raises((AttributeError, TypeError, Exception)):
        object.__setattr__(kv, "close", 2.0)


def test_order_view_fill_price_none() -> None:
    assert OrderView.from_dict({"order_id": "x"}).fill_price is None


def test_pnl_snapshot_positive_negative() -> None:
    snap = PnlSnapshot(realized=10.0, unrealized=-5.0, total=5.0, funding=-1.0, open_count=2)
    assert snap.realized > 0 and snap.unrealized < 0


def test_order_book_view_from_dict() -> None:
    d = {"bids": [["100", "1"]], "asks": [["101", "2"]]}
    ob = OrderBookView.from_dict(d, "BTC")
    assert ob.symbol == "BTC" and len(ob.bids) == 1 and len(ob.asks) == 1


@pytest.mark.parametrize(
    "klines,expected",
    [
        ([{"close": 100.0}, {"close": 200.0}, {"close": 300.0}], (100.0, 200.0, 300.0)),
        ([], ()),
        ([{"open": 50.0}], (0.0,)),
    ],
)
def test_closes_from_klines(klines, expected) -> None:
    assert closes_from_klines(klines) == expected
