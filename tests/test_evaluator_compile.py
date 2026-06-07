from __future__ import annotations

import unittest
from hashlib import sha256

import numpy as np
import pandas as pd

from siglab.evaluator.compile import (
    PAIR_TRADE_FAMILIES,
    PERP_EXECUTION_PROFILES,
    _align_cross_sectional_frame,
    _build_pair_positions,
    _build_pair_trade_positions,
    _build_ranked_positions,
    _cross_sectional_zscore,
    _feature_hash,
    _gate_mask_from_frame,
    _pair_policy_parameters,
    _pair_raw_frames,
    _perp_global_raw_frames,
    _perp_raw_frames,
    _ranked_policy_parameters,
    _resolve_regime_gates,
    _time_series_zscore,
    _weighted_component_frames,
    _weighted_score,
)


class CompileConstantsTests(unittest.TestCase):

    def test_piar_trade_families_contains_expected(self) -> None:
        self.assertIn("perp_pair_trade_unlevered", PAIR_TRADE_FAMILIES)
        self.assertIn("perp_pair_trade_levered", PAIR_TRADE_FAMILIES)
        self.assertEqual(len(PAIR_TRADE_FAMILIES), 2)

    def test_perp_execution_profiles_contains_expected(self) -> None:
        self.assertIn("ranked_directional", PERP_EXECUTION_PROFILES)
        self.assertIn("basket_neutral_spread", PERP_EXECUTION_PROFILES)
        self.assertIn("ranked_carry", PERP_EXECUTION_PROFILES)
        self.assertEqual(len(PERP_EXECUTION_PROFILES), 3)


class CrossSectionalZscoreTests(unittest.TestCase):

    def setUp(self) -> None:
        self.index = pd.date_range("2026-01-01", periods=4, freq="h")
        self.frame = pd.DataFrame(
            {
                "BTC": [100.0, 102.0, 101.0, 104.0],
                "ETH": [50.0, 51.0, 49.0, 52.0],
                "SOL": [20.0, 21.0, 22.0, 19.0],
            },
            index=self.index,
        )

    def test_normal_zscore_returns_correct_values(self) -> None:
        result = _cross_sectional_zscore(self.frame)
        row_0 = self.frame.iloc[0]
        mean_0 = row_0.mean()
        std_0 = row_0.std()
        expected_0 = (row_0 - mean_0) / std_0

        pd.testing.assert_series_equal(result.iloc[0], expected_0, check_names=False)

        row_means = result.mean(axis=1)
        pd.testing.assert_series_equal(
            row_means,
            pd.Series([0.0, 0.0, 0.0, 0.0], index=self.index),
        )

    def test_zero_std_returns_zero(self) -> None:
        const = pd.DataFrame(
            {"A": [5.0, 5.0, 5.0], "B": [5.0, 5.0, 5.0]},
            index=pd.date_range("2026-01-01", periods=3, freq="h"),
        )
        result = _cross_sectional_zscore(const)
        self.assertTrue((result == 0.0).all().all())

    def test_single_column_frame(self) -> None:
        single = pd.DataFrame(
            {"BTC": [100.0, 102.0]},
            index=pd.date_range("2026-01-01", periods=2, freq="h"),
        )
        result = _cross_sectional_zscore(single)
        self.assertTrue((result == 0.0).all().all())

    def test_nan_values_handled(self) -> None:
        frame = pd.DataFrame(
            {"A": [1.0, np.nan, 3.0], "B": [4.0, 5.0, np.nan], "C": [7.0, 8.0, 9.0]},
            index=pd.date_range("2026-01-01", periods=3, freq="h"),
        )
        result = _cross_sectional_zscore(frame)
        self.assertEqual(result.shape, frame.shape)
        self.assertTrue(result.notna().all().all())

    def test_inf_values_replaced_with_zero(self) -> None:
        frame = pd.DataFrame(
            {"A": [1.0, np.inf, 3.0], "B": [2.0, -np.inf, 4.0]},
            index=pd.date_range("2026-01-01", periods=3, freq="h"),
        )
        result = _cross_sectional_zscore(frame)
        self.assertTrue(np.isfinite(result).all().all())

    def test_empty_frame(self) -> None:
        empty = pd.DataFrame()
        result = _cross_sectional_zscore(empty)
        self.assertTrue(result.empty)


class TimeSeriesZscoreTests(unittest.TestCase):

    def setUp(self) -> None:
        np.random.seed(42)
        self.index = pd.date_range("2026-01-01", periods=50, freq="h")
        self.frame = pd.DataFrame(
            {
                "BTC": np.cumsum(np.random.randn(50) * 0.5 + 0.01),
                "ETH": np.cumsum(np.random.randn(50) * 0.3 + 0.02),
            },
            index=self.index,
        )

    def test_rolling_zscore_basic(self) -> None:
        result = _time_series_zscore(self.frame, window=12)
        self.assertEqual(result.shape, self.frame.shape)

        first_rows = result.iloc[:7]
        self.assertTrue((first_rows == 0.0).all().all())

        later_rows = result.iloc[20:]
        self.assertTrue((later_rows != 0.0).any().any())

    def test_zero_std_handling(self) -> None:
        const = pd.DataFrame(
            {"A": [5.0] * 50, "B": [5.0] * 50},
            index=pd.date_range("2026-01-01", periods=50, freq="h"),
        )
        result = _time_series_zscore(const, window=12)
        self.assertTrue((result == 0.0).all().all())

    def test_inf_values_handled(self) -> None:
        inf = pd.DataFrame(
            {"A": [1.0, np.inf, 3.0, 4.0, 5.0] * 10},
            index=pd.date_range("2026-01-01", periods=50, freq="h"),
        )
        result = _time_series_zscore(inf, window=12)
        self.assertTrue(np.isfinite(result).all().all())

    def test_single_column(self) -> None:
        single = pd.DataFrame(
            {"A": np.cumsum(np.random.randn(50))},
            index=pd.date_range("2026-01-01", periods=50, freq="h"),
        )
        result = _time_series_zscore(single, window=12)
        self.assertEqual(result.shape, single.shape)
        self.assertTrue(np.isfinite(result).all().all())

    def test_small_window(self) -> None:
        result = _time_series_zscore(self.frame, window=8)
        self.assertEqual(result.shape, self.frame.shape)
        self.assertTrue(np.isfinite(result).all().all())

    def test_nan_values_in_input(self) -> None:
        nan_frame = self.frame.copy()
        nan_frame.iloc[10:15, 0] = np.nan
        result = _time_series_zscore(nan_frame, window=12)
        self.assertEqual(result.shape, nan_frame.shape)
        self.assertTrue(np.isfinite(result).all().all())


class WeightedScoreTests(unittest.TestCase):

    def setUp(self) -> None:
        self.index = pd.date_range("2026-01-01", periods=6, freq="h")
        self.feature_frames = {
            "momentum": pd.DataFrame(
                {"BTC": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0], "ETH": [2.0, 3.0, 4.0, 5.0, 6.0, 7.0]},
                index=self.index,
            ),
            "carry": pd.DataFrame(
                {"BTC": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6], "ETH": [0.2, 0.3, 0.4, 0.5, 0.6, 0.7]},
                index=self.index,
            ),
        }

    def test_basic_weighted_score(self) -> None:
        result = _weighted_score(
            self.feature_frames,
            selected_features=["momentum", "carry"],
            feature_weights={"momentum": 2.0, "carry": 1.0},
            normalization="cross_sectional",
        )
        self.assertEqual(result.shape, (6, 2))
        self.assertTrue(result.notna().all().all())
        self.assertIn("BTC", result.columns)
        self.assertIn("ETH", result.columns)

    def test_with_time_series_normalization(self) -> None:
        index = pd.date_range("2026-01-01", periods=72, freq="h")
        frames = {
            "momentum": pd.DataFrame(
                {"BTC": np.cumsum(np.random.randn(72))},
                index=index,
            ),
            "carry": pd.DataFrame(
                {"BTC": np.cumsum(np.random.randn(72) * 0.5)},
                index=index,
            ),
        }
        result = _weighted_score(
            frames,
            selected_features=["momentum", "carry"],
            feature_weights={"momentum": 1.0, "carry": 2.0},
            normalization="time_series",
            z_window=24,
        )
        self.assertEqual(result.shape, (72, 1))
        self.assertTrue(result.notna().all().all())

    def test_no_features_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            _weighted_score(
                self.feature_frames,
                selected_features=["nonexistent"],
                feature_weights={},
            )
        self.assertIn("not reference", str(ctx.exception))

    def test_zero_weights_uses_uniform(self) -> None:
        result = _weighted_score(
            self.feature_frames,
            selected_features=["momentum", "carry"],
            feature_weights={"momentum": 0.0, "carry": 0.0},
        )
        self.assertEqual(result.shape, (6, 2))

    def test_missing_feature_weight_defaults_to_one(self) -> None:
        result = _weighted_score(
            self.feature_frames,
            selected_features=["momentum", "carry"],
            feature_weights={"momentum": 5.0},
            normalization="cross_sectional",
        )
        self.assertEqual(result.shape, (6, 2))
        self.assertTrue(result.notna().all().all())

    def test_nan_in_feature_frames(self) -> None:
        frames = {
            "a": pd.DataFrame(
                {"BTC": [1.0, np.nan, 3.0], "ETH": [4.0, 5.0, 6.0]},
                index=pd.date_range("2026-01-01", periods=3, freq="h"),
            ),
        }
        result = _weighted_score(
            frames,
            selected_features=["a"],
            feature_weights={},
        )
        self.assertEqual(result.shape, (3, 2))
        self.assertTrue(result.notna().all().all())

    def test_default_normalization_is_cross_sectional(self) -> None:
        result = _weighted_score(
            self.feature_frames,
            selected_features=["momentum"],
            feature_weights={},
        )
        self.assertEqual(result.shape, (6, 2))


class WeightedComponentFramesTests(unittest.TestCase):

    def setUp(self) -> None:
        self.index = pd.date_range("2026-01-01", periods=6, freq="h")
        self.feature_frames = {
            "momentum": pd.DataFrame(
                {"BTC": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0], "ETH": [2.0, 3.0, 4.0, 5.0, 6.0, 7.0]},
                index=self.index,
            ),
            "carry": pd.DataFrame(
                {"BTC": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6], "ETH": [0.2, 0.3, 0.4, 0.5, 0.6, 0.7]},
                index=self.index,
            ),
        }

    def test_returns_component_frames(self) -> None:
        components = _weighted_component_frames(
            self.feature_frames,
            selected_features=["momentum", "carry"],
            feature_weights={"momentum": 1.0, "carry": 2.0},
        )
        self.assertIn("momentum", components)
        self.assertIn("carry", components)
        self.assertEqual(len(components), 2)

    def test_empty_when_no_features_match(self) -> None:
        components = _weighted_component_frames(
            self.feature_frames,
            selected_features=["nonexistent"],
            feature_weights={},
        )
        self.assertEqual(components, {})

    def test_components_sum_to_score(self) -> None:
        components = _weighted_component_frames(
            self.feature_frames,
            selected_features=["momentum", "carry"],
            feature_weights={"momentum": 1.0, "carry": 1.0},
        )
        score = _weighted_score(
            self.feature_frames,
            selected_features=["momentum", "carry"],
            feature_weights={"momentum": 1.0, "carry": 1.0},
        )
        combined = components["momentum"] + components["carry"]
        pd.testing.assert_frame_equal(combined, score)


class AlignCrossSectionalFrameTests(unittest.TestCase):

    def setUp(self) -> None:
        self.index = pd.date_range("2026-01-01", periods=4, freq="h")
        self.tradable = ["BTC", "ETH", "SOL"]

    def test_empty_frame_returns_zeros(self) -> None:
        empty = pd.DataFrame(index=self.index)
        result = _align_cross_sectional_frame(empty, tradable_symbols=self.tradable)
        self.assertEqual(result.shape, (4, 3))
        self.assertListEqual(list(result.columns), self.tradable)
        self.assertTrue((result == 0.0).all().all())

    def test_aligns_to_tradable_subset(self) -> None:
        frame = pd.DataFrame(
            {"BTC": [100.0, 101.0, 102.0, 103.0], "ETH": [50.0, 51.0, 52.0, 53.0]},
            index=self.index,
        )
        result = _align_cross_sectional_frame(frame, tradable_symbols=self.tradable)
        self.assertListEqual(list(result.columns), self.tradable)
        pd.testing.assert_series_equal(result["BTC"], frame["BTC"])
        pd.testing.assert_series_equal(result["ETH"], frame["ETH"])
        self.assertTrue((result["SOL"] == 0.0).all())

    def test_global_broadcast_from_extra_columns(self) -> None:
        frame = pd.DataFrame(
            {"GLOBAL": [0.5, 0.6, 0.7, 0.8]},
            index=self.index,
        )
        result = _align_cross_sectional_frame(frame, tradable_symbols=self.tradable)
        self.assertListEqual(list(result.columns), self.tradable)
        for symbol in self.tradable:
            pd.testing.assert_series_equal(
                result[symbol],
                pd.Series([0.5, 0.6, 0.7, 0.8], index=self.index, name=symbol),
            )

    def test_global_broadcast_with_symbol_columns(self) -> None:
        frame = pd.DataFrame(
            {"BTC": [100.0, 101.0, 102.0, 103.0], "GLOBAL": [0.5, 0.6, 0.7, 0.8]},
            index=self.index,
        )
        result = _align_cross_sectional_frame(frame, tradable_symbols=self.tradable)
        self.assertListEqual(list(result.columns), self.tradable)
        expected_btc = pd.Series([100.5, 101.6, 102.7, 103.8], index=self.index)
        pd.testing.assert_series_equal(result["BTC"], expected_btc, check_names=False)
        expected_eth = pd.Series([0.5, 0.6, 0.7, 0.8], index=self.index)
        pd.testing.assert_series_equal(result["ETH"], expected_eth, check_names=False)

    def test_non_numeric_values_coerced(self) -> None:
        frame = pd.DataFrame(
            {"BTC": [100.0, "bad", 102.0], "ETH": [50.0, 51.0, 52.0]},
            index=pd.date_range("2026-01-01", periods=3, freq="h"),
        )
        result = _align_cross_sectional_frame(frame, tradable_symbols=["BTC", "ETH"])
        self.assertTrue(pd.api.types.is_float_dtype(result["BTC"]))

    def test_all_extra_columns_empty_broadcasts_zero(self) -> None:
        frame = pd.DataFrame(
            {"GLOBAL": [np.nan, np.nan, np.nan]},
            index=pd.date_range("2026-01-01", periods=3, freq="h"),
        )
        result = _align_cross_sectional_frame(frame, tradable_symbols=["BTC", "ETH"])
        self.assertTrue((result == 0.0).all().all())


class BuildRankedPositionsTests(unittest.TestCase):

    def setUp(self) -> None:
        self.index = pd.date_range("2026-01-01", periods=4, freq="h")
        self.score = pd.DataFrame(
            {
                "AAA": [1.5, 0.5, -0.5, 2.0],
                "BBB": [0.8, -1.2, 1.2, -1.5],
                "CCC": [-0.3, 2.0, -2.0, 0.0],
                "DDD": [-1.2, -0.8, 0.3, 1.8],
            },
            index=self.index,
        )

    def test_basic_long_short(self) -> None:
        positions = _build_ranked_positions(
            self.score,
            long_count=2,
            short_count=2,
            gross_target=2.0,
            max_asset_weight=1.0,
        )
        self.assertEqual(positions.shape, self.score.shape)
        self.assertAlmostEqual(float(positions.iloc[0, 0]), 0.5)  # AAA long
        self.assertAlmostEqual(float(positions.iloc[0, 1]), 0.5)  # BBB long
        self.assertAlmostEqual(float(positions.iloc[0, 2]), -0.5)  # CCC short
        self.assertAlmostEqual(float(positions.iloc[0, 3]), -0.5)  # DDD short

    def test_only_longs(self) -> None:
        positions = _build_ranked_positions(
            self.score,
            long_count=2,
            short_count=0,
            gross_target=1.0,
            max_asset_weight=1.0,
        )
        self.assertTrue((positions >= 0.0).all().all())
        self.assertGreater(positions.abs().sum().sum(), 0.0)

    def test_only_shorts(self) -> None:
        positions = _build_ranked_positions(
            self.score,
            long_count=0,
            short_count=2,
            gross_target=1.0,
            max_asset_weight=1.0,
        )
        self.assertTrue((positions <= 0.0).all().all())

    def test_require_positive_longs_excludes_negative_scores(self) -> None:
        score = pd.DataFrame(
            {"A": [-1.0, 0.5], "B": [0.3, -0.2]},
            index=pd.date_range("2026-01-01", periods=2, freq="h"),
        )
        positions = _build_ranked_positions(
            score,
            long_count=2,
            short_count=0,
            gross_target=1.0,
            max_asset_weight=1.0,
            require_positive_longs=True,
        )
        self.assertAlmostEqual(float(positions.iloc[0, 0]), 0.0)  # A excluded
        self.assertAlmostEqual(float(positions.iloc[0, 1]), 1.0)  # B gets full budget

    def test_min_abs_score_filters_weak_signals(self) -> None:
        score = pd.DataFrame(
            {"A": [0.1, 2.0], "B": [0.05, -1.0]},
            index=pd.date_range("2026-01-01", periods=2, freq="h"),
        )
        positions = _build_ranked_positions(
            score,
            long_count=2,
            short_count=2,
            gross_target=1.0,
            max_asset_weight=1.0,
            min_abs_score=0.2,
        )
        self.assertTrue((positions.iloc[0] == 0.0).all())
        self.assertAlmostEqual(float(positions.iloc[1, 0]), 0.5)
        self.assertAlmostEqual(float(positions.iloc[1, 1]), -0.5)

    def test_require_both_sides_skips_when_missing_one_side(self) -> None:
        score = pd.DataFrame(
            {"A": [1.5, -1.5], "B": [0.1, 0.1]},
            index=pd.date_range("2026-01-01", periods=2, freq="h"),
        )
        positions = _build_ranked_positions(
            score,
            long_count=2,
            short_count=2,
            gross_target=2.0,
            max_asset_weight=1.0,
            require_both_sides=True,
        )
        self.assertTrue((positions.iloc[0] == 0.0).all())
        self.assertTrue((positions.iloc[1] != 0.0).any())

    def test_with_regime_gate_mask(self) -> None:
        reg_mask = pd.Series([True, False, True, False], index=self.index)
        positions = _build_ranked_positions(
            self.score,
            long_count=2,
            short_count=2,
            gross_target=2.0,
            max_asset_weight=1.0,
            regime_gate_mask=reg_mask,
        )
        self.assertTrue((positions.iloc[1] == 0.0).all())
        self.assertTrue((positions.iloc[3] == 0.0).all())
        self.assertFalse((positions.iloc[0] == 0.0).all())

    def test_max_asset_weight_caps_positions(self) -> None:
        score = pd.DataFrame(
            {"A": [1.0], "B": [0.5], "C": [0.3]},
            index=pd.date_range("2026-01-01", periods=1, freq="h"),
        )
        positions = _build_ranked_positions(
            score,
            long_count=3,
            short_count=0,
            gross_target=2.0,
            max_asset_weight=0.3,
        )
        self.assertAlmostEqual(float(positions.iloc[0, 0]), 0.3)
        self.assertAlmostEqual(float(positions.iloc[0, 1]), 0.3)
        self.assertAlmostEqual(float(positions.iloc[0, 2]), 0.3)

    def test_forward_fill_and_nan_handling(self) -> None:
        score = pd.DataFrame(
            {"A": [1.0, np.nan, np.nan, 2.0]},
            index=pd.date_range("2026-01-01", periods=4, freq="h"),
        )
        positions = _build_ranked_positions(
            score,
            long_count=1,
            short_count=0,
            gross_target=1.0,
            max_asset_weight=1.0,
        )
        self.assertAlmostEqual(float(positions.iloc[0, 0]), 1.0)
        self.assertAlmostEqual(float(positions.iloc[1, 0]), 0.0)
        self.assertAlmostEqual(float(positions.iloc[2, 0]), 0.0)
        self.assertAlmostEqual(float(positions.iloc[3, 0]), 1.0)


    def test_overlap_long_short_resolved(self) -> None:
        score = pd.DataFrame(
            {"A": [0.5], "B": [-0.3]},
            index=pd.date_range("2026-01-01", periods=1, freq="h"),
        )
        positions = _build_ranked_positions(
            score,
            long_count=2,
            short_count=2,
            gross_target=2.0,
            max_asset_weight=1.0,
        )
        self.assertAlmostEqual(float(positions.iloc[0, 0]), 1.0)
        self.assertAlmostEqual(float(positions.iloc[0, 1]), -1.0)


class BuildPairPositionsTests(unittest.TestCase):

    def setUp(self) -> None:
        self.index = pd.date_range("2026-01-01", periods=3, freq="h")
        self.score = pd.DataFrame(
            {"BTC": [1.5, -0.5, 2.0], "ETH": [0.8, 1.2, -1.0]},
            index=self.index,
        )

    def test_basic_pair(self) -> None:
        positions = _build_pair_positions(
            self.score,
            selection_count=2,
            gross_target=2.0,
            max_asset_weight=1.0,
        )
        self.assertIn("BTC_SPOT", positions.columns)
        self.assertIn("BTC_PERP", positions.columns)
        self.assertIn("ETH_SPOT", positions.columns)
        self.assertIn("ETH_PERP", positions.columns)

        self.assertAlmostEqual(float(positions.iloc[0, 0]), 0.5)  # BTC_SPOT
        self.assertAlmostEqual(float(positions.iloc[0, 1]), -0.5)  # BTC_PERP
        self.assertAlmostEqual(float(positions.iloc[0, 2]), 0.5)  # ETH_SPOT
        self.assertAlmostEqual(float(positions.iloc[0, 3]), -0.5)  # ETH_PERP

    def test_only_positive_scores_selected(self) -> None:
        positions = _build_pair_positions(
            self.score,
            selection_count=2,
            gross_target=2.0,
            max_asset_weight=1.0,
        )
        self.assertAlmostEqual(float(positions.iloc[1, 0]), 0.0)  # BTC_SPOT = 0
        self.assertAlmostEqual(float(positions.iloc[1, 1]), 0.0)  # BTC_PERP = 0
        self.assertAlmostEqual(float(positions.iloc[1, 2]), 1.0)  # ETH_SPOT
        self.assertAlmostEqual(float(positions.iloc[1, 3]), -1.0)  # ETH_PERP

    def test_with_regime_gate(self) -> None:
        reg_mask = pd.Series([True, False, True], index=self.index)
        positions = _build_pair_positions(
            self.score,
            selection_count=1,
            gross_target=1.0,
            max_asset_weight=1.0,
            regime_gate_mask=reg_mask,
        )
        self.assertTrue((positions.iloc[1] == 0.0).all())

    def test_empty_score_returns_all_zeros(self) -> None:
        empty_score = pd.DataFrame(index=pd.date_range("2026-01-01", periods=2, freq="h"))
        positions = _build_pair_positions(
            empty_score,
            selection_count=2,
            gross_target=2.0,
            max_asset_weight=1.0,
        )
        self.assertTrue((positions == 0.0).all().all())


class BuildPairTradePositionsTests(unittest.TestCase):

    def setUp(self) -> None:
        self.index = pd.date_range("2026-01-01", periods=20, freq="h")
        self.asset_1 = "BTC"
        self.asset_2 = "ETH"

    def test_entry_on_strong_signal(self) -> None:
        values = [0.0] * 5 + [2.0] + [0.0] * 14
        score = pd.DataFrame({"pair": values}, index=self.index)
        result = _build_pair_trade_positions(
            score,
            asset_1_symbol=self.asset_1,
            asset_2_symbol=self.asset_2,
            gross_target=1.0,
            max_gross_target=1.0,
            max_asset_weight=1.0,
            entry_abs_score=1.5,
            exit_abs_score=0.5,
            flip_abs_score=2.0,
            max_holding_bars=0,
            cooldown_bars=0,
            signal_leverage_scale=0.75,
        )
        self.assertTrue((result.iloc[:5] == 0.0).all().all())
        self.assertAlmostEqual(float(result.iloc[5, 0]), 0.5)  # BTC = leg_weight
        self.assertAlmostEqual(float(result.iloc[5, 1]), -0.5)  # ETH = -leg_weight

    def test_exit_when_signal_decays(self) -> None:
        values = [2.0] + [0.4] + [0.0] * 18
        score = pd.DataFrame({"pair": values}, index=self.index)
        result = _build_pair_trade_positions(
            score=score,
            asset_1_symbol=self.asset_1,
            asset_2_symbol=self.asset_2,
            gross_target=1.0,
            max_gross_target=1.0,
            max_asset_weight=1.0,
            entry_abs_score=1.5,
            exit_abs_score=0.5,
            flip_abs_score=2.0,
            max_holding_bars=0,
            cooldown_bars=0,
            signal_leverage_scale=0.75,
        )
        self.assertAlmostEqual(float(result.iloc[0, 0]), 0.5)
        self.assertTrue((result.iloc[1] == 0.0).all().all())
        self.assertTrue((result.iloc[2] == 0.0).all().all())

    def test_flip_signal_reverses_position(self) -> None:
        values = [0.0] * 3 + [2.0] + [1.0] * 2 + [-3.0] + [0.0] * 13
        score = pd.DataFrame({"pair": values}, index=self.index)
        result = _build_pair_trade_positions(
            score,
            asset_1_symbol=self.asset_1,
            asset_2_symbol=self.asset_2,
            gross_target=1.0,
            max_gross_target=1.0,
            max_asset_weight=1.0,
            entry_abs_score=1.5,
            exit_abs_score=0.5,
            flip_abs_score=2.5,
            max_holding_bars=0,
            cooldown_bars=0,
            signal_leverage_scale=0.75,
        )
        self.assertAlmostEqual(float(result.iloc[3, 0]), 0.5)
        self.assertAlmostEqual(float(result.iloc[6, 0]), -0.5)
        self.assertAlmostEqual(float(result.iloc[6, 1]), 0.5)

    def test_cooldown_prevents_reentry(self) -> None:
        values = [0.0] * 3 + [2.0] + [0.0] + [2.0] + [0.0] * 14
        score = pd.DataFrame({"pair": values}, index=self.index)
        result = _build_pair_trade_positions(
            score,
            asset_1_symbol=self.asset_1,
            asset_2_symbol=self.asset_2,
            gross_target=1.0,
            max_gross_target=1.0,
            max_asset_weight=1.0,
            entry_abs_score=1.5,
            exit_abs_score=0.5,
            flip_abs_score=2.0,
            max_holding_bars=0,
            cooldown_bars=3,
            signal_leverage_scale=0.75,
        )
        self.assertAlmostEqual(float(result.iloc[3, 0]), 0.5)
        self.assertTrue((result.iloc[4] == 0.0).all().all())
        self.assertTrue((result.iloc[5] == 0.0).all().all())
        self.assertTrue((result.iloc[5] == 0.0).all().all())

    def test_regime_gate_exit_after_entry(self) -> None:
        values = [2.0] * 10 + [0.0] * 10
        score = pd.DataFrame({"pair": values}, index=self.index)
        reg_mask = pd.Series([True] * 5 + [False] * 15, index=self.index)
        result = _build_pair_trade_positions(
            score,
            asset_1_symbol=self.asset_1,
            asset_2_symbol=self.asset_2,
            gross_target=1.0,
            max_gross_target=1.0,
            max_asset_weight=1.0,
            entry_abs_score=1.5,
            exit_abs_score=0.5,
            flip_abs_score=2.0,
            max_holding_bars=0,
            cooldown_bars=0,
            signal_leverage_scale=0.75,
            regime_gate_mask=reg_mask,
            exit_on_regime_break=True,
        )
        self.assertAlmostEqual(float(result.iloc[0, 0]), 0.5)
        self.assertTrue((result.iloc[5] == 0.0).all().all())

    def test_max_holding_bars_exits_position(self) -> None:
        values = [2.0] * 3 + [0.0] * 17
        score = pd.DataFrame({"pair": values}, index=self.index)
        result = _build_pair_trade_positions(
            score=score,
            asset_1_symbol=self.asset_1,
            asset_2_symbol=self.asset_2,
            gross_target=1.0,
            max_gross_target=1.0,
            max_asset_weight=1.0,
            entry_abs_score=1.5,
            exit_abs_score=0.5,
            flip_abs_score=2.0,
            max_holding_bars=3,
            cooldown_bars=0,
            signal_leverage_scale=0.75,
        )
        self.assertAlmostEqual(float(result.iloc[0, 0]), 0.5)
        self.assertAlmostEqual(float(result.iloc[1, 0]), 0.5)
        self.assertAlmostEqual(float(result.iloc[2, 0]), 0.5)
        self.assertTrue((result.iloc[3] == 0.0).all().all())
        self.assertTrue((result.iloc[4] == 0.0).all().all())

    def test_negative_entry_enters_short(self) -> None:
        score = pd.DataFrame({"pair": [0.0] * 3 + [-2.0] + [0.0] * 16}, index=self.index)
        result = _build_pair_trade_positions(
            score,
            asset_1_symbol=self.asset_1,
            asset_2_symbol=self.asset_2,
            gross_target=1.0,
            max_gross_target=1.0,
            max_asset_weight=1.0,
            entry_abs_score=1.5,
            exit_abs_score=0.5,
            flip_abs_score=2.0,
            max_holding_bars=0,
            cooldown_bars=0,
            signal_leverage_scale=0.75,
        )
        self.assertAlmostEqual(float(result.iloc[3, 0]), -0.5)
        self.assertAlmostEqual(float(result.iloc[3, 1]), 0.5)


class PairPolicyParametersTests(unittest.TestCase):

    def test_unlevered_clamps_gross_target(self) -> None:
        params = _pair_policy_parameters(
            family="perp_pair_trade_unlevered",
            params={"gross_target": 5.0, "max_gross_target": 10.0},
            defaults={},
        )
        self.assertEqual(params["gross_target"], 5.0)
        self.assertEqual(params["max_gross_target"], 5.0)

    def test_levered_allows_higher_gross_target(self) -> None:
        params = _pair_policy_parameters(
            family="perp_pair_trade_levered",
            params={"gross_target": 2.0, "max_gross_target": 5.0},
            defaults={},
        )
        self.assertEqual(params["gross_target"], 2.0)
        self.assertLessEqual(params["max_gross_target"], 3.0)

    def test_entry_score_clamped(self) -> None:
        params = _pair_policy_parameters(
            family="perp_pair_trade_unlevered",
            params={"entry_abs_score": 5.0},
            defaults={},
        )
        self.assertLessEqual(params["entry_abs_score"], 1.5)

    def test_exit_score_derived_from_entry(self) -> None:
        params = _pair_policy_parameters(
            family="perp_pair_trade_unlevered",
            params={"entry_abs_score": 1.0},
            defaults={},
        )
        self.assertAlmostEqual(params["exit_abs_score"], 0.5)
        self.assertLessEqual(params["exit_abs_score"], params["entry_abs_score"])

    def test_flip_score_derived_from_entry(self) -> None:
        params = _pair_policy_parameters(
            family="perp_pair_trade_unlevered",
            params={"entry_abs_score": 1.0},
            defaults={},
        )
        self.assertAlmostEqual(params["flip_abs_score"], 1.0)
        self.assertGreaterEqual(params["flip_abs_score"], params["entry_abs_score"])

    def test_max_holding_bars_clamped(self) -> None:
        params = _pair_policy_parameters(
            family="perp_pair_trade_unlevered",
            params={"max_holding_bars": 999},
            defaults={},
        )
        self.assertEqual(params["max_holding_bars"], 336)

    def test_cooldown_bars_clamped(self) -> None:
        params = _pair_policy_parameters(
            family="perp_pair_trade_unlevered",
            params={"cooldown_bars": 999},
            defaults={},
        )
        self.assertEqual(params["cooldown_bars"], 168)  # 24*7

    def test_signal_leverage_scale_clamped(self) -> None:
        params = _pair_policy_parameters(
            family="perp_pair_trade_unlevered",
            params={"signal_leverage_scale": 10.0},
            defaults={},
        )
        self.assertEqual(params["signal_leverage_scale"], 3.0)

    def test_defaults_used_when_params_missing(self) -> None:
        params = _pair_policy_parameters(
            family="perp_pair_trade_unlevered",
            params={},
            defaults={
                "gross_target": 0.5,
                "entry_abs_score": 0.8,
                "max_holding_bars": 48,
                "cooldown_bars": 12,
            },
        )
        self.assertAlmostEqual(params["gross_target"], 0.5)
        self.assertAlmostEqual(params["entry_abs_score"], 0.8)
        self.assertEqual(params["max_holding_bars"], 48)
        self.assertEqual(params["cooldown_bars"], 12)

    def test_negative_values_clamped_to_zero(self) -> None:
        params = _pair_policy_parameters(
            family="perp_pair_trade_unlevered",
            params={"entry_abs_score": -1.0, "exit_abs_score": -1.0, "cooldown_bars": -5},
            defaults={},
        )
        self.assertEqual(params["entry_abs_score"], 0.0)
        self.assertEqual(params["exit_abs_score"], 0.0)
        self.assertEqual(params["cooldown_bars"], 0)

    def test_min_abs_score_in_output_matches_entry(self) -> None:
        params = _pair_policy_parameters(
            family="perp_pair_trade_unlevered",
            params={"entry_abs_score": 1.2},
            defaults={},
        )
        self.assertAlmostEqual(params["min_abs_score"], params["entry_abs_score"])


class RankedPolicyParametersTests(unittest.TestCase):

    def test_defaults(self) -> None:
        params = _ranked_policy_parameters(
            params={},
            defaults={},
            long_enabled_default=True,
            short_enabled_default=False,
        )
        self.assertAlmostEqual(params["gross_target"], 1.0)
        self.assertAlmostEqual(params["min_abs_score"], 0.0)
        self.assertEqual(params["long_count"], 0)
        self.assertEqual(params["short_count"], 0)
        self.assertTrue(params["long_enabled"])
        self.assertFalse(params["short_enabled"])

    def test_clamping(self) -> None:
        params = _ranked_policy_parameters(
            params={
                "gross_target": 10.0,
                "min_abs_score": 3.0,
                "long_count": 100,
                "short_count": 100,
            },
            defaults={},
            long_enabled_default=True,
            short_enabled_default=False,
        )
        self.assertAlmostEqual(params["gross_target"], 3.0)
        self.assertAlmostEqual(params["min_abs_score"], 1.5)
        self.assertEqual(params["long_count"], 8)
        self.assertEqual(params["short_count"], 8)

    def test_lower_bounds(self) -> None:
        params = _ranked_policy_parameters(
            params={
                "gross_target": 0.0,
                "min_abs_score": -1.0,
                "long_count": -5,
                "short_count": -5,
            },
            defaults={},
            long_enabled_default=True,
            short_enabled_default=True,
        )
        self.assertAlmostEqual(params["gross_target"], 0.1)
        self.assertAlmostEqual(params["min_abs_score"], 0.0)
        self.assertEqual(params["long_count"], 0)
        self.assertEqual(params["short_count"], 0)

    def test_params_override_defaults(self) -> None:
        params = _ranked_policy_parameters(
            params={"long_count": 5, "short_count": 3},
            defaults={"long_count": 2, "short_count": 1},
            long_enabled_default=True,
            short_enabled_default=True,
        )
        self.assertEqual(params["long_count"], 5)
        self.assertEqual(params["short_count"], 3)

    def test_long_short_enabled_from_params(self) -> None:
        params = _ranked_policy_parameters(
            params={"long_enabled": False, "short_enabled": True},
            defaults={},
            long_enabled_default=True,
            short_enabled_default=False,
        )
        self.assertFalse(params["long_enabled"])
        self.assertTrue(params["short_enabled"])


class GateMaskFromFrameTests(unittest.TestCase):

    def setUp(self) -> None:
        self.index = pd.date_range("2026-01-01", periods=5, freq="h")
        self.frame = pd.DataFrame(
            {"A": [1.0, 2.0, 3.0, 4.0, 5.0], "B": [0.5, 1.5, 2.5, 3.5, 4.5]},
            index=self.index,
        )

    def test_no_min_max_returns_true_for_any_positive(self) -> None:
        mask = _gate_mask_from_frame(self.frame)
        self.assertEqual(len(mask), 5)
        self.assertTrue(mask.dtype == bool)
        self.assertTrue(mask.all())

    def test_min_threshold(self) -> None:
        mask = _gate_mask_from_frame(self.frame, minimum=2.0)
        self.assertFalse(mask.iloc[0])
        self.assertTrue(mask.iloc[2])

    def test_max_threshold(self) -> None:
        mask = _gate_mask_from_frame(self.frame, maximum=3.0)
        self.assertTrue(mask.iloc[0])
        self.assertFalse(mask.iloc[3])

    def test_both_thresholds(self) -> None:
        mask = _gate_mask_from_frame(self.frame, minimum=1.5, maximum=4.0)
        self.assertTrue(mask.iloc[1])
        self.assertFalse(mask.iloc[0])
        self.assertFalse(mask.iloc[4])

    def test_nan_values_false(self) -> None:
        frame = pd.DataFrame(
            {"A": [1.0, np.nan, 3.0]},
            index=pd.date_range("2026-01-01", periods=3, freq="h"),
        )
        mask = _gate_mask_from_frame(frame)
        self.assertFalse(mask.iloc[1])

    def test_inf_values_coerced(self) -> None:
        frame = pd.DataFrame(
            {"A": [1.0, np.inf, 3.0]},
            index=pd.date_range("2026-01-01", periods=3, freq="h"),
        )
        mask = _gate_mask_from_frame(frame)
        self.assertEqual(len(mask), 3)

    def test_single_row_frame(self) -> None:
        frame = pd.DataFrame(
            {"A": [5.0]},
            index=pd.date_range("2026-01-01", periods=1, freq="h"),
        )
        mask = _gate_mask_from_frame(frame, minimum=3.0)
        self.assertTrue(mask.iloc[0])

    def test_zero_values_not_positive(self) -> None:
        frame = pd.DataFrame(
            {"A": [0.0, 1.0]},
            index=pd.date_range("2026-01-01", periods=2, freq="h"),
        )
        mask = _gate_mask_from_frame(frame)
        self.assertFalse(mask.iloc[0])
        self.assertTrue(mask.iloc[1])

    def test_non_numeric_values_coerced(self) -> None:
        frame = pd.DataFrame(
            {"A": [1.0, "bad", 3.0]},
            index=pd.date_range("2026-01-01", periods=3, freq="h"),
        )
        mask = _gate_mask_from_frame(frame)
        self.assertEqual(len(mask), 3)
        self.assertFalse(mask.iloc[1])


class ResolveRegimeGatesTests(unittest.TestCase):

    def setUp(self) -> None:
        self.index = pd.date_range("2026-01-01", periods=10, freq="h")
        self.raw_frames = {
            "price": pd.DataFrame(
                {"BTC": [100.0 + i for i in range(10)]},
                index=self.index,
            ),
            "funding": pd.DataFrame(
                {"BTC": [0.01 * i for i in range(10)]},
                index=self.index,
            ),
        }

    def test_no_gates_returns_none(self) -> None:
        mask, metadata = _resolve_regime_gates(
            {},
            aliases={},
            raw_frames=self.raw_frames,
        )
        self.assertIsNone(mask)
        self.assertFalse(metadata["configured"])

    def test_none_gates_returns_none(self) -> None:
        mask, metadata = _resolve_regime_gates(
            None,
            aliases={},
            raw_frames=self.raw_frames,
        )
        self.assertIsNone(mask)
        self.assertFalse(metadata["configured"])

    def test_empty_entry_list_returns_none(self) -> None:
        mask, metadata = _resolve_regime_gates(
            {"entry": []},
            aliases={},
            raw_frames=self.raw_frames,
        )
        self.assertIsNone(mask)
        self.assertFalse(metadata["configured"])

    def test_string_expression_gate(self) -> None:
        mask, metadata = _resolve_regime_gates(
            {"entry": ["price"]},
            aliases={},
            raw_frames=self.raw_frames,
        )
        self.assertIsNotNone(mask)
        self.assertTrue(metadata["configured"])
        self.assertEqual(len(mask), len(self.index))
        self.assertTrue(mask.dtype == bool)

    def test_dict_spec_with_min(self) -> None:
        mask, metadata = _resolve_regime_gates(
            {"entry": [{"expression": "price", "min": 102.0}]},
            aliases={},
            raw_frames=self.raw_frames,
        )
        self.assertIsNotNone(mask)
        self.assertTrue(metadata["configured"])
        self.assertFalse(mask.iloc[0])
        self.assertFalse(mask.iloc[1])
        self.assertTrue(mask.iloc[2])

    def test_dict_spec_with_max(self) -> None:
        mask, metadata = _resolve_regime_gates(
            {"entry": [{"expression": "price", "max": 104.0}]},
            aliases={},
            raw_frames=self.raw_frames,
        )
        self.assertIsNotNone(mask)
        self.assertTrue(mask.iloc[0])
        self.assertTrue(mask.iloc[4])
        self.assertFalse(mask.iloc[5])

    def test_combined_gates(self) -> None:
        mask, metadata = _resolve_regime_gates(
            {
                "entry": [
                    {"expression": "price", "min": 102.0},
                    {"expression": "funding", "min": 0.02},
                ]
            },
            aliases={},
            raw_frames=self.raw_frames,
        )
        self.assertIsNotNone(mask)
        self.assertFalse(mask.iloc[0])
        self.assertFalse(mask.iloc[1])
        self.assertTrue(mask.iloc[2])

    def test_exit_on_break_default_true(self) -> None:
        _, metadata = _resolve_regime_gates(
            {"entry": ["price"]},
            aliases={},
            raw_frames=self.raw_frames,
        )
        self.assertTrue(metadata["exit_on_break"])

    def test_exit_on_break_from_config(self) -> None:
        _, metadata = _resolve_regime_gates(
            {"entry": ["price"], "exit_on_break": False},
            aliases={},
            raw_frames=self.raw_frames,
        )
        self.assertFalse(metadata["exit_on_break"])

    def test_feature_key_as_alias_for_expression(self) -> None:
        mask, metadata = _resolve_regime_gates(
            {"entry": [{"feature": "price", "min": 100.0}]},
            aliases={},
            raw_frames=self.raw_frames,
        )
        self.assertIsNotNone(mask)
        self.assertTrue(metadata["configured"])


class PerpGlobalRawFramesTests(unittest.TestCase):

    def setUp(self) -> None:
        self.index = pd.date_range("2026-01-01", periods=100, freq="h")
        np.random.seed(42)
        self.prices = pd.DataFrame(
            {
                "BTC": 100.0 + np.cumsum(np.random.randn(100) * 0.5),
                "ETH": 50.0 + np.cumsum(np.random.randn(100) * 0.3),
            },
            index=self.index,
        )
        self.funding = pd.DataFrame(
            {
                "BTC": np.random.randn(100) * 0.001,
                "ETH": np.random.randn(100) * 0.001,
            },
            index=self.index,
        )

    def test_returns_all_expected_global_frames(self) -> None:
        result = _perp_global_raw_frames(self.prices, self.funding)
        expected_keys = {
            "market_price_mean",
            "market_funding_mean",
            "market_funding_dispersion",
            "market_breadth_24h",
            "market_co_movement_72h",
            "market_realized_vol_168h",
        }
        self.assertSetEqual(set(result.keys()), expected_keys)

    def test_all_frames_have_global_column(self) -> None:
        result = _perp_global_raw_frames(self.prices, self.funding)
        for name, frame in result.items():
            self.assertIn("GLOBAL", frame.columns, f"{name} missing GLOBAL column")
            self.assertEqual(frame.columns.tolist(), ["GLOBAL"])

    def test_funding_dispersion_zero_when_single_column(self) -> None:
        single_price = self.prices[["BTC"]]
        single_funding = self.funding[["BTC"]]
        result = _perp_global_raw_frames(single_price, single_funding)
        self.assertTrue(result["market_funding_dispersion"]["GLOBAL"].isnull().all())

    def test_market_price_mean_is_cross_sectional_mean(self) -> None:
        result = _perp_global_raw_frames(self.prices, self.funding)
        expected_mean = self.prices.mean(axis=1)
        pd.testing.assert_series_equal(
            result["market_price_mean"]["GLOBAL"],
            expected_mean,
            check_names=False,
        )

    def test_price_sorting(self) -> None:
        unsorted_prices = self.prices.iloc[::-1].copy()
        result = _perp_global_raw_frames(unsorted_prices, self.funding)
        self.assertEqual(result["market_price_mean"].index[0], self.prices.index[0])

    def test_nan_in_funding_handled(self) -> None:
        funding_nan = self.funding.copy()
        funding_nan.iloc[5:10] = np.nan
        result = _perp_global_raw_frames(self.prices, funding_nan)
        self.assertTrue(result["market_funding_mean"].notna().all().all())


class PerpRawFramesTests(unittest.TestCase):

    def setUp(self) -> None:
        np.random.seed(42)
        self.index = pd.date_range("2026-01-01", periods=50, freq="h")
        self.prices = pd.DataFrame(
            {
                "BTC": 100.0 + np.cumsum(np.random.randn(50) * 0.5),
                "ETH": 50.0 + np.cumsum(np.random.randn(50) * 0.3),
            },
            index=self.index,
        )
        self.funding = pd.DataFrame(
            {
                "BTC": np.random.randn(50) * 0.001,
                "ETH": np.random.randn(50) * 0.001,
            },
            index=self.index,
        )

    def test_returns_price_and_funding_and_global_frames(self) -> None:
        result = _perp_raw_frames(self.prices, self.funding)
        self.assertIn("price", result)
        self.assertIn("funding", result)
        self.assertIn("market_price_mean", result)
        self.assertIn("market_funding_mean", result)

    def test_price_and_funding_passed_through(self) -> None:
        result = _perp_raw_frames(self.prices, self.funding)
        pd.testing.assert_frame_equal(result["price"], self.prices)
        pd.testing.assert_frame_equal(result["funding"], self.funding)


class PairRawFramesTests(unittest.TestCase):

    def setUp(self) -> None:
        np.random.seed(42)
        self.index = pd.date_range("2026-01-01", periods=50, freq="h")
        self.prices = pd.DataFrame(
            {
                "BTC": 100.0 + np.cumsum(np.random.randn(50) * 0.5),
                "ETH": 50.0 + np.cumsum(np.random.randn(50) * 0.3),
            },
            index=self.index,
        )
        self.funding = pd.DataFrame(
            {
                "BTC": np.random.randn(50) * 0.001,
                "ETH": np.random.randn(50) * 0.001,
            },
            index=self.index,
        )

    def test_returns_pair_specific_frames(self) -> None:
        result = _pair_raw_frames(
            prices=self.prices,
            funding=self.funding,
            asset_1_symbol="BTC",
            asset_2_symbol="ETH",
        )
        expected_keys = {
            "asset_1_price",
            "asset_2_price",
            "asset_1_funding",
            "asset_2_funding",
            "price_ratio",
            "funding_spread",
            "market_price_mean",
            "market_funding_mean",
            "market_funding_dispersion",
            "market_breadth_24h",
            "market_co_movement_72h",
            "market_realized_vol_168h",
        }
        self.assertSetEqual(set(result.keys()), expected_keys)

    def test_asset_prices_have_pair_column(self) -> None:
        result = _pair_raw_frames(
            prices=self.prices,
            funding=self.funding,
            asset_1_symbol="BTC",
            asset_2_symbol="ETH",
        )
        self.assertEqual(result["asset_1_price"].columns.tolist(), ["PAIR"])
        self.assertEqual(result["asset_2_price"].columns.tolist(), ["PAIR"])

    def test_price_ratio_correct(self) -> None:
        result = _pair_raw_frames(
            prices=self.prices,
            funding=self.funding,
            asset_1_symbol="BTC",
            asset_2_symbol="ETH",
        )
        expected_ratio = self.prices["BTC"] / self.prices["ETH"]
        pd.testing.assert_series_equal(
            result["price_ratio"]["PAIR"],
            expected_ratio,
            check_names=False,
        )

    def test_funding_spread_correct(self) -> None:
        result = _pair_raw_frames(
            prices=self.prices,
            funding=self.funding,
            asset_1_symbol="BTC",
            asset_2_symbol="ETH",
        )
        expected_spread = self.funding["BTC"] - self.funding["ETH"]
        pd.testing.assert_series_equal(
            result["funding_spread"]["PAIR"],
            expected_spread,
            check_names=False,
        )

    def test_inf_values_replaced(self) -> None:
        prices_inf = self.prices.copy()
        prices_inf.iloc[0, 0] = np.inf
        prices_inf.iloc[1, 1] = -np.inf
        result = _pair_raw_frames(
            prices=prices_inf,
            funding=self.funding,
            asset_1_symbol="BTC",
            asset_2_symbol="ETH",
        )
        self.assertFalse(np.isinf(result["asset_1_price"]).any().any())
        self.assertFalse(np.isinf(result["asset_2_price"]).any().any())


class FeatureHashTests(unittest.TestCase):

    def test_deterministic(self) -> None:
        features = ["momentum", "carry", "volatility"]
        h1 = _feature_hash(features)
        h2 = _feature_hash(features)
        self.assertEqual(h1, h2)

    def test_order_independent(self) -> None:
        h1 = _feature_hash(["a", "b", "c"])
        h2 = _feature_hash(["c", "a", "b"])
        self.assertEqual(h1, h2)

    def test_different_features_different_hash(self) -> None:
        h1 = _feature_hash(["momentum"])
        h2 = _feature_hash(["carry"])
        self.assertNotEqual(h1, h2)

    def test_length(self) -> None:
        h = _feature_hash(["test"])
        self.assertEqual(len(h), 16)

    def test_hex_chars(self) -> None:
        h = _feature_hash(["test"])
        self.assertTrue(all(c in "0123456789abcdef" for c in h))

    def test_empty_list(self) -> None:
        h = _feature_hash([])
        self.assertEqual(len(h), 16)

    def test_single_feature(self) -> None:
        h = _feature_hash(["only"])
        self.assertEqual(len(h), 16)

    def test_known_hash_value(self) -> None:
        features = ["a"]
        payload = "|".join(sorted(features))
        expected = sha256(payload.encode("utf-8")).hexdigest()[:16]
        self.assertEqual(_feature_hash(features), expected)


if __name__ == "__main__":
    unittest.main()
