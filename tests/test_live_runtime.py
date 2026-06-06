"""Tests for siglab.live.runtime public API."""

from __future__ import annotations

import json
import math
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from siglab.live.runtime import (
    SoDEXExecutionAdapter,
    DirectionalPerpsSigLabStrategy,
    _finite_float,
    _compact_weights,
)
from siglab.config import load_settings


class TestFiniteFloat:
    def test_normal(self):
        assert _finite_float(3.14) == pytest.approx(3.14)

    def test_string(self):
        assert _finite_float("2.5") == pytest.approx(2.5)

    def test_bad_string(self):
        assert _finite_float("bad", 1.0) == 1.0

    def test_none(self):
        assert _finite_float(None) == 0.0

    def test_inf(self):
        assert _finite_float(float("inf")) == 0.0

    def test_nan(self):
        assert _finite_float(float("nan")) == 0.0

    def test_negative_inf(self):
        assert _finite_float(float("-inf"), -1.0) == -1.0


class TestCompactWeights:
    def test_filters_zero(self):
        result = _compact_weights({"BTC": 0.5, "ETH": 0.0, "SOL": -0.1})
        assert "BTC" in result
        assert "ETH" not in result
        assert "SOL" in result

    def test_rounds(self):
        result = _compact_weights({"BTC": 0.123456789})
        assert result["BTC"] == pytest.approx(0.123457, abs=1e-5)

    def test_empty(self):
        assert _compact_weights({}) == {}


class TestSoDEXExecutionAdapter:
    @pytest.mark.asyncio
    async def test_no_client_raises(self):
        adapter = SoDEXExecutionAdapter()
        with pytest.raises(RuntimeError, match="SoDEX client must be provided"):
            await adapter.get_user_state()

    def test_dependency_report_no_client(self):
        adapter = SoDEXExecutionAdapter()
        report = adapter.dependency_report()
        assert report["client_configured"] is False
        assert len(report["missing_methods"]) == 4

    def test_dependency_report_with_mock_client(self):
        mock_client = MagicMock()
        mock_client.get_user_state = MagicMock()
        mock_client.update_leverage = MagicMock()
        mock_client.place_market_order = MagicMock()
        mock_client.all_mids = MagicMock()
        adapter = SoDEXExecutionAdapter(config={"sodex_client": mock_client})
        report = adapter.dependency_report()
        assert report["client_configured"] is True
        assert report["missing_methods"] == []

    def test_coin_to_asset_from_config(self):
        adapter = SoDEXExecutionAdapter(config={"coin_to_asset": {"BTC": 1, "ETH": 2}})
        assert adapter.coin_to_asset == {"BTC": 1, "ETH": 2}

    def test_get_valid_order_size_no_client(self):
        adapter = SoDEXExecutionAdapter()
        assert adapter.get_valid_order_size(0, 1.5) == 1.5


class TestLoadSettings:
    def test_returns_config(self):
        settings = load_settings()
        assert settings is not None
        assert settings.root_dir.exists()
        assert settings.optuna_trials > 0

    def test_has_required_fields(self):
        settings = load_settings()
        assert hasattr(settings, "root_dir")
        assert hasattr(settings, "sosovalue_config_path")
        assert hasattr(settings, "data_lake_dir")
        assert hasattr(settings, "artifact_dir")


class TestDirectionalPerpsStrategy:
    def test_init(self):
        strat = DirectionalPerpsSigLabStrategy(config={"key": "value"})
        assert strat.live_spec == {}
        assert strat.spec is None

    def test_spec_path_missing_raises(self):
        strat = DirectionalPerpsSigLabStrategy(config={})
        with pytest.raises(ValueError, match="missing SPEC_PATH"):
            strat._spec_path()

    def test_spec_path_from_config(self):
        strat = DirectionalPerpsSigLabStrategy(config={"siglab_live_spec_path": "/tmp/spec.json"})
        assert strat._spec_path() == Path("/tmp/spec.json")

    def test_build_trade_plan(self):
        strat = DirectionalPerpsSigLabStrategy(config={})
        plan = strat._build_trade_plan(
            target_weights={"BTC": 0.5},
            current_positions={"BTC": 0.0},
            mids={"BTC": 50000.0},
            account_value=10000.0,
            leverage=2.0,
            min_trade_usd=10.0,
        )
        assert len(plan) == 1
        assert plan[0]["symbol"] == "BTC"
        assert plan[0]["is_buy"] is True

    def test_build_trade_plan_below_min(self):
        strat = DirectionalPerpsSigLabStrategy(config={})
        # target_qty = 0.5 * 10000 * 1 / 50000 = 0.1
        # current_qty = 0.09999, delta_usd = 0.00001 * 50000 = 0.5 < 100
        plan = strat._build_trade_plan(
            target_weights={"BTC": 0.5},
            current_positions={"BTC": 0.09999},
            mids={"BTC": 50000.0},
            account_value=10000.0,
            leverage=1.0,
            min_trade_usd=100.0,
        )
        assert len(plan) == 0

    def test_extract_perp_positions(self):
        strat = DirectionalPerpsSigLabStrategy(config={})
        state = {
            "assetPositions": [
                {"position": {"coin": "BTC", "szi": "1.5"}},
                {"position": {"coin": "ETH", "szi": "-0.5"}},
            ]
        }
        positions = strat._extract_perp_positions(state)
        assert positions["BTC"] == 1.5
        assert positions["ETH"] == -0.5

    def test_extract_empty_positions(self):
        strat = DirectionalPerpsSigLabStrategy(config={})
        assert strat._extract_perp_positions({}) == {}

    def test_account_value_cross_margin(self):
        strat = DirectionalPerpsSigLabStrategy(config={})
        state = {"crossMarginSummary": {"accountValue": "1000"}, "marginSummary": {"accountValue": "500"}}
        assert strat._account_value(state) == 1000.0

    def test_account_value_margin_only(self):
        strat = DirectionalPerpsSigLabStrategy(config={})
        state = {"marginSummary": {"accountValue": "750"}}
        assert strat._account_value(state) == 750.0

    def test_account_value_empty(self):
        strat = DirectionalPerpsSigLabStrategy(config={})
        assert strat._account_value({}) == 0.0

    @pytest.mark.asyncio
    async def test_deposit_returns_message(self):
        strat = DirectionalPerpsSigLabStrategy(config={})
        ok, msg = await strat.deposit()
        assert ok is False
        assert "not automated" in msg.lower() or "fund" in msg.lower()
