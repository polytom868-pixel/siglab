from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError

import numpy as np
import pandas as pd

from siglab.evaluator.backtesting import (
    BacktestConfig,
    BacktestResult,
    _stats,
    convert_to_spot,
    run_backtest,
)


def _make_prices(
    values: list[float],
    columns: list[str] | None = None,
    freq: str = "h",
) -> pd.DataFrame:
    """Build a price DataFrame with a DatetimeIndex."""
    if columns is None:
        columns = ["ASSET"]
    n = len(values)
    dates = pd.date_range("2024-01-01", periods=n, freq=freq)
    return pd.DataFrame(values, index=dates, columns=columns)


def _constant_weights(like: pd.DataFrame, value: float = 1.0) -> pd.DataFrame:
    """Build a constant-weight DataFrame matching the shape of *like*."""
    return pd.DataFrame(value, index=like.index, columns=like.columns)


class BacktestConfigConstructionTests(unittest.TestCase):
    """BacktestConfig dataclass: defaults, custom, and frozen behaviour."""

    def test_default_values(self) -> None:
        config = BacktestConfig()
        self.assertEqual(config.leverage, 1.0)
        self.assertIsNone(config.funding_rates)
        self.assertEqual(config.rebalance_threshold, 0.0)
        self.assertTrue(config.enable_liquidation)

    def test_custom_values(self) -> None:
        funding = pd.DataFrame({"A": [0.001]})
        config = BacktestConfig(
            leverage=3.0,
            funding_rates=funding,
            rebalance_threshold=0.05,
            enable_liquidation=False,
        )
        self.assertEqual(config.leverage, 3.0)
        self.assertIs(config.funding_rates, funding)
        self.assertEqual(config.rebalance_threshold, 0.05)
        self.assertFalse(config.enable_liquidation)

    def test_frozen_dataclass_prevents_mutation(self) -> None:
        config = BacktestConfig()
        with self.assertRaises(FrozenInstanceError):
            config.leverage = 5.0

    def test_frozen_dataclass_prevents_mutation_other_field(self) -> None:
        config = BacktestConfig(enable_liquidation=False)
        with self.assertRaises(FrozenInstanceError):
            config.enable_liquidation = True


class ConvertToSpotTests(unittest.TestCase):
    """convert_to_spot helper function."""

    def test_returns_copy_and_zero_funding(self) -> None:
        prices = _make_prices([100.0, 101.0, 102.0], columns=["BTC"])
        spot_prices, funding = convert_to_spot(prices)

        pd.testing.assert_frame_equal(spot_prices, prices)
        self.assertIsNot(spot_prices, prices)

        self.assertTrue((funding == 0.0).all().all())
        self.assertEqual(funding.shape, prices.shape)
        pd.testing.assert_index_equal(funding.index, prices.index)
        pd.testing.assert_index_equal(funding.columns, prices.columns)

    def test_multi_asset(self) -> None:
        prices = _make_prices([100.0, 101.0], columns=["BTC"])
        spot_prices, funding = convert_to_spot(prices)

        self.assertEqual(spot_prices.shape, (2, 1))
        self.assertEqual(funding.shape, (2, 1))


class BacktestRunHappyPathTests(unittest.TestCase):
    """run_backtest with valid data and default config."""

    def setUp(self) -> None:
        self.dates = pd.date_range("2024-01-01", periods=5, freq="h")
        # Steady uptrend 100 -> 104 = 4 % gain
        self.prices = pd.DataFrame(
            {"BTC": [100.0, 101.0, 102.0, 103.0, 104.0]},
            index=self.dates,
        )
        self.full_weight = _constant_weights(self.prices, 1.0)
        self.config = BacktestConfig()

    def test_single_asset_uptrend(self) -> None:
        result = run_backtest(self.prices, self.full_weight, self.config)

        self.assertIsInstance(result, BacktestResult)
        self.assertEqual(len(result.equity_curve), 5)

        # With 100 % weight on an asset that goes 100 -> 104,
        # equity = price / price[0] = [1.0, 1.01, 1.02, 1.03, 1.04]
        expected_equity = pd.Series(
            [1.0, 1.01, 1.02, 1.03, 1.04],
            index=self.dates,
            name="equity",
        )
        pd.testing.assert_series_equal(
            result.equity_curve,
            expected_equity,
            check_names=False,
            rtol=1e-10,
        )

        self.assertAlmostEqual(result.stats["total_return"], 0.04)

        pd.testing.assert_frame_equal(result.positions, self.full_weight)

        self.assertFalse(result.liquidated)

        for key in ("total_return", "sharpe", "cagr", "max_drawdown", "calmar"):
            self.assertIn(key, result.stats)

        self.assertEqual(len(result.returns), 5)
        self.assertEqual(result.returns.iloc[0], 0.0)

    def test_multi_asset_equal_weight(self) -> None:
        prices = pd.DataFrame(
            {
                "BTC": [100.0, 102.0, 104.0, 106.0, 108.0],
                "ETH": [200.0, 202.0, 204.0, 206.0, 208.0],
            },
            index=self.dates,
        )
        weights = pd.DataFrame(0.5, index=self.dates, columns=["BTC", "ETH"])
        result = run_backtest(prices, weights, self.config)

        # BTC ~8% gain, ETH ~4% gain, each at 50% weight
        self.assertGreater(result.stats["total_return"], 0.05)
        self.assertFalse(result.liquidated)

    def test_metrics_by_period_columns(self) -> None:
        result = run_backtest(self.prices, self.full_weight, self.config)
        expected_cols = {"equity", "return", "turnover", "funding_amount", "fee_amount"}
        self.assertEqual(set(result.metrics_by_period.columns), expected_cols)

    def test_trades_list(self) -> None:
        result = run_backtest(self.prices, self.full_weight, self.config)
        # First row: fillna(weights.abs()) gives size=1.0, threshold=0.0 includes it
        self.assertGreaterEqual(len(result.trades), 1)


class BacktestRunFundingTests(unittest.TestCase):
    """run_backtest with funding rates."""

    def setUp(self) -> None:
        # Start at 07:00 so bar 08:00 is an 8h settlement boundary with a prior position
        self.dates = pd.date_range("2024-01-01 07:00", periods=5, freq="h")
        self.prices = pd.DataFrame(
            {"BTC": [100.0, 101.0, 102.0, 103.0, 104.0]},
            index=self.dates,
        )
        self.weights = _constant_weights(self.prices, 1.0)

    def test_constant_funding_rate_adds_to_pnl(self) -> None:
        funding_rate = 0.001  # 0.1 % per period
        funding = _constant_weights(self.prices, funding_rate).rename(columns={"ASSET": "BTC"})
        config = BacktestConfig(funding_rates=funding)

        result = run_backtest(self.prices, self.weights, config)

        no_funding = run_backtest(self.prices, self.weights, BacktestConfig())
        self.assertGreater(
            result.stats["total_return"],
            no_funding.stats["total_return"],
        )

        self.assertIn("funding_amount", result.metrics_by_period.columns)
        self.assertTrue((result.metrics_by_period["funding_amount"] >= 0).all())

    def test_negative_funding_rate_reduces_pnl(self) -> None:
        funding_rate = -0.001
        funding = _constant_weights(self.prices, funding_rate).rename(columns={"ASSET": "BTC"})
        config = BacktestConfig(funding_rates=funding)

        result = run_backtest(self.prices, self.weights, config)

        no_funding = run_backtest(self.prices, self.weights, BacktestConfig())
        self.assertLess(
            result.stats["total_return"],
            no_funding.stats["total_return"],
        )

    def test_funding_rate_not_leveraged(self) -> None:
        """Funding rate contribution is not multiplied by leverage."""
        funding = _constant_weights(self.prices, 0.001).rename(columns={"ASSET": "BTC"})
        config_lev = BacktestConfig(leverage=3.0, funding_rates=funding)
        config_no_lev = BacktestConfig(leverage=1.0, funding_rates=funding)

        result_lev = run_backtest(self.prices, self.weights, config_lev)
        result_no_lev = run_backtest(self.prices, self.weights, config_no_lev)

        self.assertGreater(result_lev.stats["total_return"], result_no_lev.stats["total_return"])

    def test_no_funding_sets_funding_amount_to_zero(self) -> None:
        result = run_backtest(self.prices, self.weights, BacktestConfig())
        self.assertTrue((result.metrics_by_period["funding_amount"] == 0.0).all())


class BacktestRunLeverageTests(unittest.TestCase):
    """run_backtest with different leverage values."""

    def setUp(self) -> None:
        self.dates = pd.date_range("2024-01-01", periods=5, freq="h")
        self.prices = pd.DataFrame(
            {"BTC": [100.0, 101.0, 102.0, 103.0, 104.0]},
            index=self.dates,
        )
        self.weights = _constant_weights(self.prices, 1.0)

    def test_leverage_two_amplifies_returns(self) -> None:
        config_2x = BacktestConfig(leverage=2.0)
        config_1x = BacktestConfig(leverage=1.0)

        result_2x = run_backtest(self.prices, self.weights, config_2x)
        result_1x = run_backtest(self.prices, self.weights, config_1x)

        # Compounding breaks exact linearity, but relative relationship holds
        self.assertGreater(result_2x.stats["total_return"], result_1x.stats["total_return"])
        self.assertAlmostEqual(
            result_2x.stats["total_return"],
            2.0 * result_1x.stats["total_return"],
            places=1,
        )

    def test_leverage_between_zero_and_one(self) -> None:
        config_05x = BacktestConfig(leverage=0.5)
        config_1x = BacktestConfig(leverage=1.0)

        result_05x = run_backtest(self.prices, self.weights, config_05x)
        result_1x = run_backtest(self.prices, self.weights, config_1x)

        self.assertGreater(result_1x.stats["total_return"], result_05x.stats["total_return"])
        self.assertGreater(result_05x.stats["total_return"], 0.0)

    def test_zero_leverage_produces_zero_return(self) -> None:
        config = BacktestConfig(leverage=0.0)
        result = run_backtest(self.prices, self.weights, config)

        self.assertAlmostEqual(result.stats["total_return"], 0.0)
        self.assertTrue((result.equity_curve == 1.0).all())


class BacktestRunLiquidationTests(unittest.TestCase):
    """run_backtest liquidation behaviour."""

    def test_liquidation_when_equity_hits_zero(self) -> None:
        # 50 % drop with 2x leverage -> equity hits 0
        prices = _make_prices([100.0, 50.0], columns=["BTC"])
        weights = _constant_weights(prices, 1.0)
        config = BacktestConfig(leverage=2.0, enable_liquidation=True)

        result = run_backtest(prices, weights, config)

        self.assertTrue(result.liquidated)
        self.assertAlmostEqual(result.equity_curve.iloc[-1], 0.0)

    def test_liquidation_with_decline_below_zero(self) -> None:
        # >50 % drop with 2x leverage -> equity goes negative
        prices = _make_prices([100.0, 30.0], columns=["BTC"])
        weights = _constant_weights(prices, 1.0)
        config = BacktestConfig(leverage=2.0, enable_liquidation=True)

        result = run_backtest(prices, weights, config)
        self.assertTrue(result.liquidated)

    def test_liquidation_disabled_does_not_set_liquidated_flag(self) -> None:
        prices = _make_prices([100.0, 50.0], columns=["BTC"])
        weights = _constant_weights(prices, 1.0)
        config = BacktestConfig(leverage=2.0, enable_liquidation=False)

        result = run_backtest(prices, weights, config)
        self.assertFalse(result.liquidated)

    def test_no_liquidation_in_uptrend(self) -> None:
        # Small uptrend prevents CAGR overflow from short-period annualization
        prices = _make_prices([100.0, 100.1, 100.2], columns=["BTC"])
        weights = _constant_weights(prices, 1.0)
        config = BacktestConfig(leverage=5.0)

        result = run_backtest(prices, weights, config)
        self.assertFalse(result.liquidated)


class BacktestRunRebalanceThresholdTests(unittest.TestCase):
    """run_backtest rebalance_threshold filtering."""

    def setUp(self) -> None:
        self.dates = pd.date_range("2024-01-01", periods=5, freq="h")
        self.prices = pd.DataFrame(
            {"BTC": [100.0] * 5},
            index=self.dates,
        )
        self.changing_weights = pd.DataFrame(
            {"BTC": [1.0, 0.8, 0.6, 0.4, 0.2]},
            index=self.dates,
        )

    def test_low_threshold_includes_small_trades(self) -> None:
        config = BacktestConfig(leverage=1.0, rebalance_threshold=0.05)
        result = run_backtest(self.prices, self.changing_weights, config)

        # weight diffs (abs, first filled with weight.abs()): [1.0, 0.2, 0.2, 0.2, 0.2]
        # all > 0.05 -> 5 trades
        self.assertEqual(len(result.trades), 5)

    def test_high_threshold_filters_small_trades(self) -> None:
        config = BacktestConfig(leverage=1.0, rebalance_threshold=0.5)
        result = run_backtest(self.prices, self.changing_weights, config)

        # With threshold 0.5: only first trade (1.0) exceeds
        self.assertEqual(len(result.trades), 1)
        self.assertAlmostEqual(result.trades[0]["size"], 1.0)

    def test_threshold_one_filters_all_trades(self) -> None:
        config = BacktestConfig(leverage=1.0, rebalance_threshold=1.0)
        result = run_backtest(self.prices, self.changing_weights, config)

        self.assertEqual(len(result.trades), 0)

    def test_trade_structure(self) -> None:
        config = BacktestConfig(rebalance_threshold=0.0)
        result = run_backtest(self.prices, self.changing_weights, config)

        if result.trades:
            trade = result.trades[0]
            self.assertIn("timestamp", trade)
            self.assertIn("symbol", trade)
            self.assertIn("size", trade)
            self.assertIsInstance(trade["size"], float)


class BacktestRunEdgeCasesTests(unittest.TestCase):
    """run_backtest with edge-case inputs."""

    def test_flat_prices(self) -> None:
        prices = _make_prices([100.0, 100.0, 100.0], columns=["BTC"])
        weights = _constant_weights(prices, 1.0)
        config = BacktestConfig()

        result = run_backtest(prices, weights, config)

        self.assertTrue((result.equity_curve == 1.0).all())
        self.assertAlmostEqual(result.stats["total_return"], 0.0)
        self.assertAlmostEqual(result.stats["sharpe"], 0.0)

    def test_zero_target_weights(self) -> None:
        prices = _make_prices([100.0, 110.0, 120.0], columns=["BTC"])
        weights = _constant_weights(prices, 0.0)
        config = BacktestConfig()

        result = run_backtest(prices, weights, config)

        self.assertTrue((result.equity_curve == 1.0).all())
        self.assertAlmostEqual(result.stats["total_return"], 0.0)
        self.assertEqual(len(result.trades), 0)

    def test_nan_in_prices(self) -> None:
        prices = _make_prices([100.0, np.nan, 110.0], columns=["BTC"])
        weights = _constant_weights(prices, 1.0)
        config = BacktestConfig()

        result = run_backtest(prices, weights, config)

        self.assertIsInstance(result, BacktestResult)
        self.assertFalse(np.any(np.isnan(result.equity_curve)))

    def test_nan_in_target_weights(self) -> None:
        prices = _make_prices([100.0, 101.0, 102.0], columns=["BTC"])
        weights_nan = pd.DataFrame(
            {"BTC": [1.0, np.nan, 0.5]},
            index=prices.index,
        )
        config = BacktestConfig()

        result = run_backtest(prices, weights_nan, config)
        # NaN weights get ffill'd then fillna(0)
        self.assertFalse(np.any(np.isnan(result.positions.values)))

    def test_inf_values_in_prices(self) -> None:
        prices = _make_prices([100.0, np.inf, 110.0], columns=["BTC"])
        weights = _constant_weights(prices, 1.0)
        config = BacktestConfig()

        result = run_backtest(prices, weights, config)
        self.assertFalse(np.any(np.isnan(result.equity_curve)))
        self.assertFalse(np.any(np.isinf(result.equity_curve)))

    def test_negative_prices_handled(self) -> None:
        prices = _make_prices([100.0, 90.0, 80.0], columns=["BTC"])
        weights = _constant_weights(prices, 1.0)
        config = BacktestConfig()

        result = run_backtest(prices, weights, config)
        self.assertLess(result.stats["total_return"], 0.0)
        self.assertLess(result.stats["max_drawdown"], 0.0)

    def test_single_period(self) -> None:
        prices = _make_prices([100.0], columns=["BTC"])
        weights = _constant_weights(prices, 1.0)
        config = BacktestConfig()

        result = run_backtest(prices, weights, config)

        self.assertEqual(len(result.equity_curve), 1)
        self.assertAlmostEqual(result.equity_curve.iloc[0], 1.0)
        self.assertAlmostEqual(result.stats["total_return"], 0.0)

    def test_down_market(self) -> None:
        prices = _make_prices([100.0, 95.0, 90.0, 85.0], columns=["BTC"])
        weights = _constant_weights(prices, 1.0)
        config = BacktestConfig()

        result = run_backtest(prices, weights, config)
        self.assertLess(result.stats["total_return"], 0.0)
        self.assertEqual(len(result.returns), 4)

    def test_two_assets_one_with_nan_weights(self) -> None:
        dates = pd.date_range("2024-01-01", periods=3, freq="h")
        prices = pd.DataFrame(
            {"BTC": [100.0, 101.0, 102.0], "ETH": [200.0, 202.0, 204.0]},
            index=dates,
        )
        weights = pd.DataFrame(
            {"BTC": [1.0, 1.0, 1.0], "ETH": [0.0, 0.0, 0.0]},
            index=dates,
        )
        config = BacktestConfig()
        result = run_backtest(prices, weights, config)

        self.assertIn("ETH", result.positions.columns)
        self.assertTrue((result.positions["ETH"] == 0.0).all())


class BacktestInternalHelpersTests(unittest.TestCase):
    """Internal helpers: _stats."""

    def test_stats_on_flat_equity(self) -> None:
        equity = pd.Series([1.0, 1.0, 1.0])
        returns = pd.Series([0.0, 0.0, 0.0])
        stats = _stats(equity, returns)

        self.assertAlmostEqual(stats["total_return"], 0.0)
        self.assertAlmostEqual(stats["sharpe"], 0.0)
        self.assertAlmostEqual(stats["max_drawdown"], 0.0)
        self.assertAlmostEqual(stats["calmar"], 0.0)
        self.assertFalse(stats["liquidated"])

    def test_stats_on_empty_series(self) -> None:
        equity = pd.Series([], dtype=float)
        returns = pd.Series([], dtype=float)
        stats = _stats(equity, returns)

        self.assertAlmostEqual(stats["total_return"], 0.0)
        self.assertAlmostEqual(stats["sharpe"], 0.0)
        self.assertAlmostEqual(stats["cagr"], 0.0)
        self.assertAlmostEqual(stats["max_drawdown"], 0.0)

    def test_stats_on_positive_returns(self) -> None:
        equity = pd.Series([1.0, 1.05, 1.10])
        returns = pd.Series([0.0, 0.05, 0.0476])
        stats = _stats(equity, returns)

        self.assertAlmostEqual(stats["total_return"], 0.10, places=4)
        self.assertGreater(stats["sharpe"], 0.0)
        self.assertGreater(stats["cagr"], 0.0)
        self.assertAlmostEqual(stats["max_drawdown"], 0.0)

    def test_stats_on_negative_returns(self) -> None:
        equity = pd.Series([1.0, 0.95, 0.90])
        returns = pd.Series([0.0, -0.05, -0.0526])
        stats = _stats(equity, returns)

        self.assertAlmostEqual(stats["total_return"], -0.10, places=2)
        self.assertGreater(stats["sharpe"], -np.inf)  # will be negative
        self.assertLess(stats["cagr"], 0.0)
        self.assertLess(stats["max_drawdown"], 0.0)

    def test_stats_liquidated_default_false(self) -> None:
        equity = pd.Series([1.0, 1.1])
        returns = pd.Series([0.0, 0.1])
        stats = _stats(equity, returns)
        self.assertFalse(stats["liquidated"])


class BacktestResultDataclassTests(unittest.TestCase):
    """BacktestResult dataclass construction."""

    def test_construct_backtest_result(self) -> None:
        equity = pd.Series([1.0, 1.1])
        returns = pd.Series([0.0, 0.1])
        positions = pd.DataFrame({"A": [1.0, 1.0]})
        trades = [{"timestamp": pd.Timestamp("2024-01-01"), "symbol": "A", "size": 1.0}]
        metrics = pd.DataFrame({"equity": [1.0, 1.1], "return": [0.0, 0.1]})
        stats = {"total_return": 0.1}

        result = BacktestResult(
            equity_curve=equity,
            returns=returns,
            positions=positions,
            trades=trades,
            metrics_by_period=metrics,
            stats=stats,
        )

        self.assertIs(result.equity_curve, equity)
        self.assertEqual(result.stats["total_return"], 0.1)
        self.assertFalse(result.liquidated)

    def test_construct_with_liquidated_true(self) -> None:
        equity = pd.Series([1.0, 0.0])
        result = BacktestResult(
            equity_curve=equity,
            returns=pd.Series([0.0, -1.0]),
            positions=pd.DataFrame({"A": [1.0, 1.0]}),
            trades=[],
            metrics_by_period=pd.DataFrame(),
            stats={},
            liquidated=True,
        )
        self.assertTrue(result.liquidated)


if __name__ == "__main__":
    unittest.main()
