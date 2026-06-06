"""Tests for siglab.tui.data_views dataclasses and closes_from_klines."""

from __future__ import annotations

import pytest

from siglab.tui.data_views import (
    KlineView,
    TickerView,
    OrderView,
    PositionView,
    PnlSnapshot,
    SymbolEntry,
    OrderBookView,
    GraphNode,
    GraphEdge,
    StrategyEntry,
    RiskSnapshot,
    closes_from_klines,
)


class TestTickerView:
    def test_from_dict(self):
        d = {"symbol": "BTC", "lastPrice": "50000", "priceChangePercent": "2.5", "volume": "1000"}
        tv = TickerView.from_dict(d)
        assert tv.symbol == "BTC"
        assert tv.last_price == 50000.0
        assert tv.price_change_pct == 2.5
        assert tv.volume == 1000.0

    def test_defaults(self):
        tv = TickerView.from_dict({})
        assert tv.symbol == "?"
        assert tv.last_price == 0.0

    def test_raw_access(self):
        d = {"symbol": "ETH"}
        tv = TickerView.from_dict(d)
        assert tv.raw is d


class TestSymbolEntry:
    def test_from_ticker(self):
        tv = TickerView.from_dict({"symbol": "BTC", "lastPrice": 50000, "priceChangePercent": 1.0, "volume": 500})
        se = SymbolEntry.from_ticker(tv)
        assert se.name == "BTC"
        assert se.price == 50000.0


class TestKlineView:
    def test_from_dict(self):
        d = {"open": 100, "high": 110, "low": 95, "close": 105, "volume": 1000}
        kv = KlineView.from_dict(d)
        assert kv.open == 100.0
        assert kv.high == 110.0
        assert kv.low == 95.0
        assert kv.close == 105.0
        assert kv.volume == 1000.0

    def test_frozen(self):
        kv = KlineView.from_dict({"close": 1.0})
        with pytest.raises((AttributeError, TypeError, Exception)):
            kv.close = 2.0  # type: ignore[misc]


class TestOrderView:
    def test_from_dict(self):
        d = {
            "order_id": "o1",
            "symbol": "BTC",
            "side": "buy",
            "order_type": "market",
            "quantity": 0.1,
            "price": 50000,
            "fill_price": 50010,
            "status": "filled",
            "created_at": 1234567890,
        }
        ov = OrderView.from_dict(d)
        assert ov.order_id == "o1"
        assert ov.fill_price == 50010.0
        assert ov.status == "filled"

    def test_fill_price_none(self):
        ov = OrderView.from_dict({"order_id": "x"})
        assert ov.fill_price is None


class TestPositionView:
    def test_from_dict(self):
        d = {"symbol": "ETH", "quantity": 2.0, "entry_price": 3000, "unrealized_pnl": 50.0}
        pv = PositionView.from_dict(d)
        assert pv.symbol == "ETH"
        assert pv.quantity == 2.0
        assert pv.unrealized_pnl == 50.0


class TestPnlSnapshot:
    def test_from_dict(self):
        d = {"realized_pnl": 100, "unrealized_pnl": -20, "total_pnl": 80, "total_funding_cost": -5, "open_position_count": 3}
        snap = PnlSnapshot.from_dict(d)
        assert snap.realized == 100.0
        assert snap.unrealized == -20.0
        assert snap.total == 80.0
        assert snap.funding == -5.0
        assert snap.open_count == 3

    def test_zero_values(self):
        snap = PnlSnapshot.from_dict({})
        assert snap.realized == 0.0
        assert snap.unrealized == 0.0
        assert snap.total == 0.0

    def test_positive_negative(self):
        snap = PnlSnapshot(realized=10.0, unrealized=-5.0, total=5.0, funding=-1.0, open_count=2)
        assert snap.realized > 0
        assert snap.unrealized < 0


class TestClosesFromKlines:
    def test_extraction(self):
        klines = [{"close": 100.0}, {"close": 200.0}, {"close": 300.0}]
        closes = closes_from_klines(klines)
        assert closes == (100.0, 200.0, 300.0)

    def test_empty(self):
        assert closes_from_klines([]) == ()

    def test_missing_close(self):
        klines = [{"open": 50.0}]
        closes = closes_from_klines(klines)
        assert closes == (0.0,)


class TestOrderBookView:
    def test_from_dict(self):
        d = {"bids": [["100", "1"]], "asks": [["101", "2"]]}
        ob = OrderBookView.from_dict(d, "BTC")
        assert ob.symbol == "BTC"
        assert len(ob.bids) == 1
        assert len(ob.asks) == 1


class TestGraphNode:
    def test_from_dict(self):
        d = {"id": "n1", "label": "Node 1", "kind": "evidence", "count": 5}
        gn = GraphNode.from_dict(d)
        assert gn.id == "n1"
        assert gn.count == 5


class TestGraphEdge:
    def test_from_dict(self):
        d = {"source": "a", "target": "b", "confidence": 0.9}
        ge = GraphEdge.from_dict(d)
        assert ge.source == "a"
        assert ge.confidence == 0.9
        assert ge.label == "linked"


class TestStrategyEntry:
    def test_from_dict(self):
        d = {"spec_hash": "abc123", "family": "trend", "track": "trend_signals", "passed": True, "aggregate_score": 0.85}
        se = StrategyEntry.from_dict(d)
        assert se.spec_hash == "abc123"
        assert se.passed is True
        assert se.aggregate_score == 0.85


class TestRiskSnapshot:
    def test_from_dict(self):
        d = {"composite_score": 0.75, "strategy_count": 3, "alerts": [{"severity": "warning"}]}
        rs = RiskSnapshot.from_dict(d)
        assert rs.composite_score == 0.75
        assert rs.strategy_count == 3
        assert len(rs.alerts) == 1
