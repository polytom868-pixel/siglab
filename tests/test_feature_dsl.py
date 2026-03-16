from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from wayfinder_autolab.feature_dsl import is_valid_feature_expression, resolve_feature_frames


class FeatureDslTests(unittest.TestCase):
    def setUp(self) -> None:
        index = pd.date_range("2026-01-01", periods=6, freq="D")
        self.price = pd.DataFrame({"ETH": [100.0, 102.0, 101.0, 104.0, 106.0, 108.0]}, index=index)
        self.funding = pd.DataFrame({"ETH": [0.01, 0.02, -0.01, 0.00, 0.03, 0.02]}, index=index)

    def test_formula_expression_resolves_against_raw_frames(self) -> None:
        expression = "sub(pct_change(price,2), rolling_mean(funding,2))"
        resolved = resolve_feature_frames(
            [expression],
            aliases={},
            raw_frames={"price": self.price, "funding": self.funding},
        )
        expected = self.price.pct_change(2).sub(self.funding.rolling(2).mean(), fill_value=0.0)
        pd.testing.assert_frame_equal(resolved[expression], expected)

    def test_invalid_feature_expression_is_rejected(self) -> None:
        self.assertFalse(
            is_valid_feature_expression(
                "unknown_signal(price,2)",
                aliases={},
                raw_series={"price", "funding"},
            )
        )

    def test_ema_rsi_and_rolling_extrema_resolve(self) -> None:
        resolved = resolve_feature_frames(
            [
                "ema(price,3)",
                "rsi(price,3)",
                "rolling_max(price,3)",
                "rolling_min(price,3)",
            ],
            aliases={},
            raw_frames={"price": self.price, "funding": self.funding},
        )
        self.assertIn("ema(price,3)", resolved)
        self.assertIn("rsi(price,3)", resolved)
        self.assertIn("rolling_max(price,3)", resolved)
        self.assertIn("rolling_min(price,3)", resolved)
        self.assertFalse(resolved["ema(price,3)"].empty)
        self.assertFalse(resolved["rsi(price,3)"].empty)
        self.assertFalse(
            is_valid_feature_expression(
                "future_series",
                aliases={},
                raw_series={"price", "funding"},
            )
        )

    def test_log_abs_sum_corr_and_beta_resolve(self) -> None:
        benchmark = pd.DataFrame(
            {"ETH": [50.0, 50.5, 51.0, 50.8, 51.4, 51.9]},
            index=self.price.index,
        )
        resolved = resolve_feature_frames(
            [
                "log(price)",
                "abs(diff(price,1))",
                "rolling_sum(abs(diff(price,1)),3)",
                "rolling_corr(pct_change(price,1), pct_change(benchmark,1), 3)",
                "rolling_beta(pct_change(price,1), pct_change(benchmark,1), 3)",
            ],
            aliases={},
            raw_frames={"price": self.price, "funding": self.funding, "benchmark": benchmark},
        )

        pd.testing.assert_frame_equal(resolved["log(price)"], self.price.apply(np.log))
        pd.testing.assert_frame_equal(resolved["abs(diff(price,1))"], self.price.diff(1).abs())
        pd.testing.assert_frame_equal(
            resolved["rolling_sum(abs(diff(price,1)),3)"],
            self.price.diff(1).abs().rolling(3).sum(),
        )
        expected_corr = self.price.pct_change(1).rolling(3).corr(benchmark.pct_change(1))
        pd.testing.assert_frame_equal(
            resolved["rolling_corr(pct_change(price,1), pct_change(benchmark,1), 3)"],
            expected_corr,
        )
        expected_beta = (
            self.price.pct_change(1).rolling(3).cov(benchmark.pct_change(1))
            .div(benchmark.pct_change(1).rolling(3).var())
        )
        pd.testing.assert_frame_equal(
            resolved["rolling_beta(pct_change(price,1), pct_change(benchmark,1), 3)"],
            expected_beta,
        )

    def test_pair_leg_formula_resolves_against_symmetric_asset_inputs(self) -> None:
        asset_1_price = pd.DataFrame({"PAIR": [100.0, 102.0, 101.0, 104.0, 106.0, 108.0]}, index=self.price.index)
        asset_2_price = pd.DataFrame({"PAIR": [200.0, 201.0, 203.0, 202.0, 204.0, 205.0]}, index=self.price.index)
        asset_1_funding = pd.DataFrame({"PAIR": [0.01, 0.02, -0.01, 0.00, 0.03, 0.02]}, index=self.price.index)
        asset_2_funding = pd.DataFrame({"PAIR": [0.00, 0.01, 0.01, -0.01, 0.02, 0.01]}, index=self.price.index)
        expression = "add(sub(pct_change(asset_1_price,1), pct_change(asset_2_price,1)), sub(asset_1_funding, asset_2_funding))"

        resolved = resolve_feature_frames(
            [expression],
            aliases={},
            raw_frames={
                "asset_1_price": asset_1_price,
                "asset_2_price": asset_2_price,
                "asset_1_funding": asset_1_funding,
                "asset_2_funding": asset_2_funding,
            },
        )

        expected = asset_1_price.pct_change(1).sub(asset_2_price.pct_change(1), fill_value=0.0).add(
            asset_1_funding.sub(asset_2_funding, fill_value=0.0),
            fill_value=0.0,
        )
        pd.testing.assert_frame_equal(resolved[expression], expected)
        self.assertTrue(
            is_valid_feature_expression(
                expression,
                aliases={},
                raw_series={"asset_1_price", "asset_2_price", "asset_1_funding", "asset_2_funding"},
            )
        )

    def test_pair_deterministic_seed_formulas_validate(self) -> None:
        aliases = {
            "pair_realized_vol_168h": "rolling_std(pct_change(price_ratio,1),168)",
            "pair_bollinger_width_20": "div(mul(2.0,rolling_std(price_ratio,20)),rolling_mean(price_ratio,20))",
        }
        raw_series = {
            "asset_1_price",
            "asset_2_price",
            "asset_1_funding",
            "asset_2_funding",
            "price_ratio",
            "funding_spread",
        }

        self.assertTrue(
            is_valid_feature_expression(
                "neg(div(sub(price_ratio, rolling_mean(price_ratio,60)), clip(rolling_std(price_ratio,60),0.0001,10.0)))",
                aliases=aliases,
                raw_series=raw_series,
            )
        )

    def test_conditional_and_gate_operators_resolve(self) -> None:
        resolved = resolve_feature_frames(
            [
                "gt(price,103)",
                "and(gt(price,103), lt(funding,0.02))",
                "where(gt(price,103), diff(price,1), 0.0)",
            ],
            aliases={},
            raw_frames={"price": self.price, "funding": self.funding},
        )

        expected_gt = (self.price > 103).astype(float)
        expected_and = ((self.price > 103) & (self.funding < 0.02)).astype(float)
        expected_where = self.price.diff(1).where(self.price > 103, 0.0)

        pd.testing.assert_frame_equal(resolved["gt(price,103)"], expected_gt)
        pd.testing.assert_frame_equal(
            resolved["and(gt(price,103), lt(funding,0.02))"],
            expected_and,
        )
        pd.testing.assert_frame_equal(
            resolved["where(gt(price,103), diff(price,1), 0.0)"],
            expected_where,
        )
        self.assertTrue(
            is_valid_feature_expression(
                "gt(funding,5e-06)",
                aliases={},
                raw_series={"funding"},
            )
        )

    def test_stat_arb_operators_resolve(self) -> None:
        benchmark = pd.DataFrame(
            {"ETH": [50.0, 50.8, 50.6, 51.0, 51.2, 51.5]},
            index=self.price.index,
        )
        resolved = resolve_feature_frames(
            [
                "rolling_zscore(price,3)",
                "rolling_skew(price,3)",
                "rolling_kurt(price,4)",
                "rolling_autocorr(diff(price,1),1,3)",
                "mean_reversion_halflife(sub(log(price),log(benchmark)),4)",
                "kalman_beta(log(price),log(benchmark))",
                "kalman_residual(log(price),log(benchmark))",
            ],
            aliases={},
            raw_frames={"price": self.price, "benchmark": benchmark},
        )

        expected_zscore = self.price.sub(self.price.rolling(3).mean()).div(self.price.rolling(3).std())
        expected_skew = self.price.rolling(3).skew()
        expected_kurt = self.price.rolling(4).kurt()
        expected_autocorr = self.price.diff(1).rolling(3).corr(self.price.diff(1).shift(1))

        pd.testing.assert_frame_equal(resolved["rolling_zscore(price,3)"], expected_zscore)
        pd.testing.assert_frame_equal(resolved["rolling_skew(price,3)"], expected_skew)
        pd.testing.assert_frame_equal(resolved["rolling_kurt(price,4)"], expected_kurt)
        pd.testing.assert_frame_equal(
            resolved["rolling_autocorr(diff(price,1),1,3)"],
            expected_autocorr,
        )

        self.assertIn("mean_reversion_halflife(sub(log(price),log(benchmark)),4)", resolved)
        self.assertIn("kalman_beta(log(price),log(benchmark))", resolved)
        self.assertIn("kalman_residual(log(price),log(benchmark))", resolved)
        self.assertEqual(
            resolved["kalman_beta(log(price),log(benchmark))"].shape,
            self.price.shape,
        )
        self.assertEqual(
            resolved["kalman_residual(log(price),log(benchmark))"].shape,
            self.price.shape,
        )
        self.assertTrue(
            is_valid_feature_expression(
                "rolling_hurst(price,32)",
                aliases={},
                raw_series={"price"},
            )
        )

    def test_rolling_hurst_validates_and_resolves_on_longer_series(self) -> None:
        index = pd.date_range("2026-01-01", periods=80, freq="h")
        trend = pd.DataFrame({"PAIR": np.linspace(100.0, 120.0, len(index))}, index=index)

        self.assertTrue(
            is_valid_feature_expression(
                "rolling_hurst(price,32)",
                aliases={},
                raw_series={"price"},
            )
        )

        resolved = resolve_feature_frames(
            ["rolling_hurst(price,32)"],
            aliases={},
            raw_frames={"price": trend},
        )
        hurst = resolved["rolling_hurst(price,32)"]
        self.assertEqual(hurst.shape, trend.shape)
        self.assertTrue(hurst.notna().any().any())


if __name__ == "__main__":
    unittest.main()
