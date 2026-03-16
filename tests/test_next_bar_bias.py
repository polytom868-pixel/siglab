from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pandas as pd

from wayfinder_autolab.evaluator.compile import PAIR_STATEFUL_POLICY_SCHEMA
from wayfinder_autolab.evaluator.core import ResearchEvaluator
from wayfinder_autolab.models import CandidateGraph, CompiledChild
from wayfinder_autolab.settings import AutolabSettings


class NextBarBiasTests(unittest.IsolatedAsyncioTestCase):
    def _settings(self) -> AutolabSettings:
        return AutolabSettings(
            root_dir=Path("/tmp"),
            wayfinder_config_path=Path("/tmp/config.json"),
            generated_strategy_dir=Path("/tmp/generated_strategies"),
            data_lake_dir=Path("/tmp"),
            artifact_dir=Path("/tmp"),
            live_dir=Path("/tmp/live"),
            lineage_db_path=Path("/tmp/wayfinder_autolab_test.db"),
            wayfinder_api_key_override=None,
            kimi_api_key=None,
            kimi_model="kimi-k2.5",
            kimi_base_url="https://api.moonshot.ai/v1",
            kimi_max_tokens=1024,
            kimi_temperature=1.0,
            kimi_top_p=0.95,
            kimi_timeout_s=30.0,
            kimi_thinking=None,
            kimi_max_tool_rounds=3,
            population_size=1,
        )

    async def test_evaluator_shifts_positions_and_drops_last_bar(self) -> None:
        index = pd.date_range("2026-01-01", periods=40, freq="h")
        prices = pd.DataFrame({"ETH": range(100, 140)}, index=index, dtype=float)
        positions = pd.DataFrame({"ETH": [0.0] + [1.0] * 39}, index=index, dtype=float)
        compiled = CompiledChild(
            prices=prices,
            target_positions=positions,
            funding_rates=pd.DataFrame({"ETH": [0.0] * 40}, index=index, dtype=float),
            metadata={},
        )

        evaluator = ResearchEvaluator(self._settings(), provider=SimpleNamespace())
        candidate = CandidateGraph.from_dict(
            {
                "track": "directional_perps",
                "family": "perp_multi_asset_decision",
                "hypothesis": "test",
                "neutrality_basis": None,
                "features": ["price_return_24h"],
                "universe": {"lookback_days": 40, "max_symbols": 1},
                "risk": {"max_leverage": 1.0, "rebalance_threshold": 0.03},
                "params": {"long_count": 1, "short_count": 0, "gross_target": 1.0},
            }
        )

        backtest_calls: list[dict[str, pd.DataFrame]] = []

        def fake_run_backtest(prices_arg, target_arg, config_arg):
            backtest_calls.append({"prices": prices_arg.copy(), "target": target_arg.copy()})
            equity_curve = pd.Series([1.0] * len(prices_arg.index), index=prices_arg.index, dtype=float)
            metrics_by_period = pd.DataFrame(
                {
                    "equity": [1.0] * len(prices_arg.index),
                    "turnover": [0.0] * len(prices_arg.index),
                    "cost": [0.0] * len(prices_arg.index),
                    "gross_exposure": [0.0] * len(prices_arg.index),
                    "net_exposure": [0.0] * len(prices_arg.index),
                    "cash_balance": [1.0] * len(prices_arg.index),
                    "inventory_value": [0.0] * len(prices_arg.index),
                    "maintenance_requirement": [0.0] * len(prices_arg.index),
                    "margin_headroom": [1.0] * len(prices_arg.index),
                },
                index=prices_arg.index,
            )
            return SimpleNamespace(
                stats={
                    "sharpe": 1.0,
                    "total_return": 0.1,
                    "cagr": 0.1,
                    "calmar": 0.8,
                    "max_drawdown": -0.1,
                },
                returns=pd.Series([0.0] * len(prices_arg.index), index=prices_arg.index, dtype=float),
                equity_curve=equity_curve,
                metrics_by_period=metrics_by_period,
                trades=[],
                liquidated=False,
                liquidation_timestamp=None,
            )

        with patch(
            "wayfinder_autolab.evaluator.core.compile_candidate",
            new=AsyncMock(return_value=compiled),
        ), patch(
            "wayfinder_autolab.evaluator.core.run_backtest",
            side_effect=fake_run_backtest,
        ):
            evaluation = await evaluator.evaluate(candidate)

        self.assertEqual(len(backtest_calls), 3)
        expected_full_prices = prices.iloc[:-1]
        expected_full_target = (
            positions.reindex(expected_full_prices.index).ffill().fillna(0.0).shift(1).fillna(0.0)
        )
        min_rows = max(14, min(30, len(expected_full_prices.index) // 2))
        holdout_size = max(min_rows, len(expected_full_prices.index) // 4)
        holdout_size = min(holdout_size, len(expected_full_prices.index) - min_rows)
        split_idx = max(min_rows, len(expected_full_prices.index) - holdout_size)
        in_sample_prices = expected_full_prices.iloc[:split_idx]
        holdout_prices = expected_full_prices.iloc[split_idx:]
        pd.testing.assert_frame_equal(backtest_calls[0]["prices"], in_sample_prices)
        pd.testing.assert_frame_equal(
            backtest_calls[0]["target"],
            expected_full_target.reindex(in_sample_prices.index),
        )
        pd.testing.assert_frame_equal(backtest_calls[1]["prices"], holdout_prices)
        pd.testing.assert_frame_equal(
            backtest_calls[1]["target"],
            expected_full_target.reindex(holdout_prices.index),
        )
        pd.testing.assert_frame_equal(backtest_calls[2]["prices"], expected_full_prices)
        pd.testing.assert_frame_equal(backtest_calls[2]["target"], expected_full_target)
        self.assertEqual(evaluation["compiled_metadata"]["signal_timing"], "next_bar")
        self.assertEqual(evaluation["compiled_metadata"]["bias_controls"]["position_shift_bars"], 1)
        self.assertTrue(evaluation["compiled_metadata"]["bias_controls"]["dropped_last_bar"])
        self.assertTrue(evaluation["compiled_metadata"]["bias_controls"]["leak_checks_passed"])
        self.assertFalse(evaluation["summary"]["strict_holdout"])
        self.assertTrue(evaluation["summary"]["validation_available"])
        self.assertFalse(evaluation["summary"]["audit_available"])
        self.assertTrue(evaluation["summary"]["holdout_available"])

    async def test_evaluator_scores_on_rolling_validation_chunks_before_audit(self) -> None:
        index = pd.date_range("2025-01-01", periods=301, freq="D")
        prices = pd.DataFrame({"ETH": range(100, 401)}, index=index, dtype=float)
        positions = pd.DataFrame({"ETH": [0.0] + [1.0] * 300}, index=index, dtype=float)
        compiled = CompiledChild(
            prices=prices,
            target_positions=positions,
            funding_rates=pd.DataFrame({"ETH": [0.0] * 301}, index=index, dtype=float),
            metadata={},
        )

        evaluator = ResearchEvaluator(self._settings(), provider=SimpleNamespace())
        candidate = CandidateGraph.from_dict(
            {
                "track": "directional_perps",
                "family": "perp_multi_asset_decision",
                "hypothesis": "test",
                "neutrality_basis": None,
                "features": ["price_return_24h"],
                "universe": {"lookback_days": 300, "max_symbols": 1},
                "risk": {"max_leverage": 1.0, "rebalance_threshold": 0.03},
                "params": {"long_count": 1, "short_count": 0, "gross_target": 1.0},
            }
        )

        backtest_calls: list[dict[str, pd.DataFrame]] = []

        def fake_run_backtest(prices_arg, target_arg, config_arg):
            backtest_calls.append({"prices": prices_arg.copy(), "target": target_arg.copy()})
            equity_curve = pd.Series([1.0] * len(prices_arg.index), index=prices_arg.index, dtype=float)
            metrics_by_period = pd.DataFrame(
                {
                    "equity": [1.0] * len(prices_arg.index),
                    "turnover": [0.0] * len(prices_arg.index),
                    "cost": [0.0] * len(prices_arg.index),
                    "gross_exposure": [0.0] * len(prices_arg.index),
                    "net_exposure": [0.0] * len(prices_arg.index),
                    "cash_balance": [1.0] * len(prices_arg.index),
                    "inventory_value": [0.0] * len(prices_arg.index),
                    "maintenance_requirement": [0.0] * len(prices_arg.index),
                    "margin_headroom": [1.0] * len(prices_arg.index),
                },
                index=prices_arg.index,
            )
            return SimpleNamespace(
                stats={
                    "sharpe": 1.0,
                    "total_return": 0.1,
                    "cagr": 0.1,
                    "calmar": 0.8,
                    "max_drawdown": -0.1,
                },
                returns=pd.Series([0.0] * len(prices_arg.index), index=prices_arg.index, dtype=float),
                equity_curve=equity_curve,
                metrics_by_period=metrics_by_period,
                trades=[],
                liquidated=False,
                liquidation_timestamp=None,
            )

        with patch(
            "wayfinder_autolab.evaluator.core.compile_candidate",
            new=AsyncMock(return_value=compiled),
        ), patch(
            "wayfinder_autolab.evaluator.core.run_backtest",
            side_effect=fake_run_backtest,
        ):
            evaluation = await evaluator.evaluate(candidate)

        expected_full_prices = prices.iloc[:-1]
        expected_full_target = (
            positions.reindex(expected_full_prices.index).ffill().fillna(0.0).shift(1).fillna(0.0)
        )
        expected_validation_ranges = [
            (90, 135),
            (135, 180),
            (180, 225),
            (225, 270),
        ]
        expected_audit_range = (270, 300)

        self.assertEqual(len(backtest_calls), 6)
        for idx, (start, end) in enumerate(expected_validation_ranges):
            pd.testing.assert_frame_equal(
                backtest_calls[idx]["prices"],
                expected_full_prices.iloc[start:end],
            )
            pd.testing.assert_frame_equal(
                backtest_calls[idx]["target"],
                expected_full_target.reindex(expected_full_prices.iloc[start:end].index),
            )
        pd.testing.assert_frame_equal(
            backtest_calls[4]["prices"],
            expected_full_prices.iloc[expected_audit_range[0]:expected_audit_range[1]],
        )
        pd.testing.assert_frame_equal(
            backtest_calls[4]["target"],
            expected_full_target.reindex(
                expected_full_prices.iloc[expected_audit_range[0]:expected_audit_range[1]].index
            ),
        )
        pd.testing.assert_frame_equal(backtest_calls[5]["prices"], expected_full_prices)
        pd.testing.assert_frame_equal(backtest_calls[5]["target"], expected_full_target)

        self.assertTrue(evaluation["summary"]["strict_holdout"])
        self.assertTrue(evaluation["summary"]["selector_uses_holdout"])
        self.assertEqual(evaluation["summary"]["selector_scope"], "rolling_validation_chunks")
        self.assertTrue(evaluation["summary"]["validation_available"])
        self.assertEqual(evaluation["summary"]["validation_window_count"], 4)
        self.assertTrue(evaluation["summary"]["audit_available"])
        self.assertEqual(
            evaluation["canonical_run"]["visual_split"]["ranges"][0]["kind"],
            "rolling_selector",
        )
        self.assertEqual(
            evaluation["canonical_run"]["visual_split"]["ranges"][1]["kind"],
            "audit_holdout",
        )

    async def test_pair_policy_sweep_freezes_params_from_train_windows_only(self) -> None:
        index = pd.date_range("2025-01-01", periods=301, freq="D")
        prices = pd.DataFrame(
            {
                "ETH": [100.0 + idx for idx in range(len(index))],
                "BTC": [80.0 + (idx * 0.8) for idx in range(len(index))],
            },
            index=index,
            dtype=float,
        )
        compiled = CompiledChild(
            prices=prices,
            target_positions=pd.DataFrame(0.0, index=index, columns=["ETH", "BTC"]),
            funding_rates=pd.DataFrame(0.0, index=index, columns=["ETH", "BTC"]),
            metadata={
                "asset_1_symbol": "ETH",
                "asset_2_symbol": "BTC",
                "gross_target": 1.0,
                "max_gross_target": 1.0,
                "entry_abs_score": 0.2,
                "exit_abs_score": 0.1,
                "flip_abs_score": 0.2,
                "max_holding_bars": 0,
                "cooldown_bars": 0,
                "signal_leverage_scale": 1.0,
                "asset_breadth": 2,
            },
            signal_score=pd.DataFrame({"PAIR": [0.18] * len(index)}, index=index, dtype=float),
        )

        evaluator = ResearchEvaluator(self._settings(), provider=SimpleNamespace())
        candidate = CandidateGraph.from_dict(
            {
                "track": "directional_perps",
                "family": "perp_pair_trade_unlevered",
                "hypothesis": "pair sweep test",
                "neutrality_basis": "none",
                "features": ["pair_ratio_return_24h"],
                "universe": {
                    "basis_groups": ["ETH", "BTC"],
                    "max_symbols": 2,
                    "lookback_days": 365,
                    "interval": "1h",
                },
                "risk": {"max_asset_weight": 0.5, "rebalance_threshold": 0.03, "max_leverage": 1.0},
                "params": {"gross_target": 1.0, "max_gross_target": 1.0, "min_abs_score": 0.2},
            }
        )

        backtest_ranges: list[tuple[pd.Timestamp, pd.Timestamp]] = []

        def fake_run_backtest(prices_arg, target_arg, config_arg):
            backtest_ranges.append((prices_arg.index[0], prices_arg.index[-1]))
            avg_gross = float(target_arg.abs().sum(axis=1).mean())
            equity_curve = pd.Series([1.0 + avg_gross] * len(prices_arg.index), index=prices_arg.index, dtype=float)
            metrics_by_period = pd.DataFrame(
                {
                    "equity": [1.0 + avg_gross] * len(prices_arg.index),
                    "turnover": [0.0] * len(prices_arg.index),
                    "cost": [0.0] * len(prices_arg.index),
                    "gross_exposure": [avg_gross] * len(prices_arg.index),
                    "net_exposure": [0.0] * len(prices_arg.index),
                    "cash_balance": [1.0] * len(prices_arg.index),
                    "inventory_value": [0.0] * len(prices_arg.index),
                    "maintenance_requirement": [0.0] * len(prices_arg.index),
                    "margin_headroom": [1.0] * len(prices_arg.index),
                },
                index=prices_arg.index,
            )
            return SimpleNamespace(
                stats={
                    "sharpe": avg_gross,
                    "total_return": avg_gross * 0.1,
                    "cagr": avg_gross * 0.1,
                    "calmar": avg_gross,
                    "max_drawdown": -0.1,
                },
                returns=pd.Series([0.0] * len(prices_arg.index), index=prices_arg.index, dtype=float),
                equity_curve=equity_curve,
                metrics_by_period=metrics_by_period,
                trades=[],
                liquidated=False,
                liquidation_timestamp=None,
            )

        candidate_policies = [
            {
                "gross_target": 1.0,
                "max_gross_target": 1.0,
                "entry_abs_score": 0.1,
                "exit_abs_score": 0.05,
                "flip_abs_score": 0.1,
                "max_holding_bars": 0,
                "cooldown_bars": 0,
                "signal_leverage_scale": 1.0,
                "min_abs_score": 0.1,
            },
            {
                "gross_target": 1.0,
                "max_gross_target": 1.0,
                "entry_abs_score": 0.3,
                "exit_abs_score": 0.15,
                "flip_abs_score": 0.3,
                "max_holding_bars": 0,
                "cooldown_bars": 0,
                "signal_leverage_scale": 1.0,
                "min_abs_score": 0.3,
            },
        ]

        with patch(
            "wayfinder_autolab.evaluator.core.compile_candidate",
            new=AsyncMock(return_value=compiled),
        ), patch.object(
            ResearchEvaluator,
            "_pair_policy_candidates",
            return_value=candidate_policies,
        ), patch(
            "wayfinder_autolab.evaluator.core.run_backtest",
            side_effect=fake_run_backtest,
        ):
            evaluation = await evaluator.evaluate(candidate)

        expected_train_ranges = [
            (index[0], index[89]),
            (index[45], index[134]),
            (index[90], index[179]),
            (index[135], index[224]),
        ]
        self.assertEqual(backtest_ranges[:8], expected_train_ranges + expected_train_ranges)
        self.assertEqual(len(backtest_ranges), 20)
        self.assertAlmostEqual(evaluation["candidate"]["params"]["entry_abs_score"], 0.1)
        self.assertAlmostEqual(evaluation["candidate"]["params"]["min_abs_score"], 0.1)
        self.assertTrue(evaluation["summary"]["policy_sweep_applied"])
        self.assertFalse(evaluation["summary"]["policy_sweep_narrowed"])
        self.assertEqual(evaluation["summary"]["policy_sweep_train_window_count"], 4)
        self.assertEqual(evaluation["summary"]["policy_sweep_trial_count"], 2)
        self.assertTrue(evaluation["summary"]["policy_sweep_material_change"])
        self.assertIn("entry_abs_score", evaluation["summary"]["policy_sweep_changed_keys"])
        self.assertEqual(
            evaluation["summary"]["policy_sweep_proposed_policy"]["entry_abs_score"],
            0.2,
        )
        self.assertEqual(
            evaluation["summary"]["policy_sweep_frozen_policy"]["entry_abs_score"],
            0.1,
        )
        self.assertTrue(evaluation["summary"]["policy_sweep_comparison_available"])
        self.assertIn(
            "pre_audit_canonical_total_return",
            evaluation["summary"]["policy_sweep_declared_evaluation"],
        )
        self.assertIn(
            "pre_audit_canonical_total_return",
            evaluation["summary"]["policy_sweep_frozen_evaluation"],
        )
        self.assertIsInstance(evaluation["summary"]["policy_sweep_declared_better_metrics"], list)
        self.assertIsInstance(evaluation["summary"]["policy_sweep_frozen_better_metrics"], list)
        self.assertIn(
            evaluation["summary"]["policy_sweep_realized_winner"],
            {"declared", "frozen", "equal", "mixed"},
        )

    async def test_fast_mode_caps_pair_policy_sweep_trials_and_windows(self) -> None:
        index = pd.date_range("2025-01-01", periods=301, freq="D")
        prices = pd.DataFrame(
            {
                "ETH": [100.0 + idx for idx in range(len(index))],
                "BTC": [80.0 + (idx * 0.8) for idx in range(len(index))],
            },
            index=index,
            dtype=float,
        )
        compiled = CompiledChild(
            prices=prices,
            target_positions=pd.DataFrame(0.0, index=index, columns=["ETH", "BTC"]),
            funding_rates=pd.DataFrame(0.0, index=index, columns=["ETH", "BTC"]),
            metadata={
                "policy_schema": PAIR_STATEFUL_POLICY_SCHEMA,
                "asset_1_symbol": "ETH",
                "asset_2_symbol": "BTC",
                "gross_target": 1.0,
                "max_gross_target": 1.0,
                "entry_abs_score": 0.25,
                "exit_abs_score": 0.12,
                "flip_abs_score": 0.32,
                "max_holding_bars": 48,
                "cooldown_bars": 12,
                "signal_leverage_scale": 0.8,
                "asset_breadth": 2,
                "regime_gates": {"entry": [{"expr": "ge(pair_corr_72h,0.0)"}], "exit_on_break": True},
            },
            signal_score=pd.DataFrame({"PAIR": [0.22] * len(index)}, index=index, dtype=float),
            regime_gate_mask=pd.Series(True, index=index, dtype=bool),
        )
        evaluator = ResearchEvaluator(self._settings(), provider=SimpleNamespace())
        candidate = CandidateGraph.from_dict(
            {
                "track": "directional_perps",
                "family": "perp_pair_trade_unlevered",
                "hypothesis": "fast sweep",
                "neutrality_basis": "none",
                "features": ["pair_residual_z_60"],
                "universe": {
                    "basis_groups": ["ETH", "BTC"],
                    "max_symbols": 2,
                    "lookback_days": 365,
                    "interval": "1h",
                },
                "risk": {"max_asset_weight": 0.5, "rebalance_threshold": 0.03, "max_leverage": 1.0},
                "params": {
                    "gross_target": 1.0,
                    "max_gross_target": 1.0,
                    "entry_abs_score": 0.25,
                    "exit_abs_score": 0.12,
                    "flip_abs_score": 0.32,
                    "max_holding_bars": 48,
                    "cooldown_bars": 12,
                    "signal_leverage_scale": 0.8,
                    "min_abs_score": 0.25,
                },
                "regime_gates": {"entry": [{"expr": "ge(pair_corr_72h,0.0)"}], "exit_on_break": True},
            }
        )

        def fake_run_backtest(prices_arg, target_arg, config_arg):
            equity_curve = pd.Series([1.0] * len(prices_arg.index), index=prices_arg.index, dtype=float)
            metrics_by_period = pd.DataFrame(
                {
                    "equity": [1.0] * len(prices_arg.index),
                    "turnover": [0.0] * len(prices_arg.index),
                    "cost": [0.0] * len(prices_arg.index),
                    "gross_exposure": [0.0] * len(prices_arg.index),
                    "net_exposure": [0.0] * len(prices_arg.index),
                    "cash_balance": [1.0] * len(prices_arg.index),
                    "inventory_value": [0.0] * len(prices_arg.index),
                    "maintenance_requirement": [0.0] * len(prices_arg.index),
                    "margin_headroom": [1.0] * len(prices_arg.index),
                },
                index=prices_arg.index,
            )
            return SimpleNamespace(
                stats={
                    "sharpe": 0.5,
                    "total_return": 0.01,
                    "cagr": 0.01,
                    "calmar": 0.2,
                    "max_drawdown": -0.02,
                },
                returns=pd.Series([0.0] * len(prices_arg.index), index=prices_arg.index, dtype=float),
                equity_curve=equity_curve,
                metrics_by_period=metrics_by_period,
                trades=[],
                liquidated=False,
                liquidation_timestamp=None,
            )

        with patch(
            "wayfinder_autolab.evaluator.core.compile_candidate",
            new=AsyncMock(return_value=compiled),
        ), patch(
            "wayfinder_autolab.evaluator.core.run_backtest",
            side_effect=fake_run_backtest,
        ):
            evaluation = await evaluator.evaluate(candidate, fast_mode=True)

        self.assertTrue(evaluation["summary"]["policy_sweep_applied"])
        self.assertLessEqual(evaluation["summary"]["policy_sweep_trial_count"], 18)
        self.assertLessEqual(evaluation["summary"]["policy_sweep_train_window_count"], 2)

    async def test_evaluation_plan_stays_usable_with_shorter_history(self) -> None:
        evaluator = ResearchEvaluator(self._settings(), provider=SimpleNamespace())
        index = pd.date_range("2025-01-01", periods=199, freq="D")

        plan = evaluator._evaluation_plan(index, min_rows=30)

        self.assertEqual(plan["selector_scope"], "rolling_validation_chunks")
        self.assertTrue(plan["visual_split"]["strict_holdout"])
        self.assertTrue(plan["visual_split"]["selector_uses_holdout"])
        self.assertIsNone(plan["validation_window"])
        self.assertIsNotNone(plan["audit_window"])
        self.assertEqual(plan["audit_window"]["start_idx"], 169)
        self.assertEqual(plan["audit_window"]["end_idx"], 199)
        self.assertEqual(len(plan["selector_windows"]), 3)
        self.assertEqual(
            [(row["start_idx"], row["end_idx"]) for row in plan["selector_windows"]],
            [(60, 90), (90, 120), (120, 169)],
        )

    async def test_evaluation_plan_skips_audit_when_30_day_floor_does_not_fit(self) -> None:
        evaluator = ResearchEvaluator(self._settings(), provider=SimpleNamespace())
        index = pd.date_range("2026-01-01", periods=300, freq="h")

        plan = evaluator._evaluation_plan(index, min_rows=30)

        self.assertEqual(plan["selector_scope"], "in_sample_only")
        self.assertFalse(plan["visual_split"]["strict_holdout"])
        self.assertFalse(plan["visual_split"]["selector_uses_holdout"])
        self.assertIsNotNone(plan["validation_window"])
        self.assertIsNone(plan["audit_window"])
        self.assertIn("minimum 30-day audit slice", plan["visual_split"]["note"])


if __name__ == "__main__":
    unittest.main()
