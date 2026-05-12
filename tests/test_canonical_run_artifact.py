from __future__ import annotations

import unittest
from pathlib import Path

import pandas as pd

from siglab.evaluator.core import ResearchEvaluator
from siglab.models import SignalSpec
from siglab.settings import SiglabConfig

REPO_ROOT = Path(__file__).resolve().parents[1]


def _settings() -> SiglabConfig:
    return SiglabConfig(
        root_dir=REPO_ROOT,
        sosovalue_config_path=Path("/tmp/config.json"),
        generated_strategy_dir=Path("/tmp/deployed_agents"),
        data_lake_dir=Path("/tmp"),
        artifact_dir=Path("/tmp"),
        live_dir=Path("/tmp/live"),
        ancestry_db_path=Path("/tmp/siglab_test.db"),
        sosovalue_api_key_override=None,
        claude_api_key=None,
        claude_model="claude-k2.5",
        claude_base_url="https://api.moonshot.ai/v1",
        claude_max_tokens=1024,
        claude_temperature=1.0,
        claude_top_p=0.95,
        claude_timeout_s=30.0,
        claude_thinking=None,
        claude_max_tool_rounds=3,
        population_size=1,
    )


class StubPerpProvider:
    async def discover_perp_symbols(self, preferred_symbols, *, limit: int) -> list[str]:
        return ["BTC", "ETH"][:limit]

    async def fetch_perp_bundle(self, *, symbols: list[str], lookback_days: int, interval: str) -> dict:
        index = pd.date_range("2026-01-01", periods=96, freq="h")
        prices = pd.DataFrame(
            {
                "BTC": [100 + idx * 0.4 for idx in range(len(index))],
                "ETH": [50 + ((idx % 8) - 4) * 0.15 + idx * 0.1 for idx in range(len(index))],
            },
            index=index,
        )
        funding = pd.DataFrame(
            {
                "BTC": [0.0] * len(index),
                "ETH": [0.0] * len(index),
            },
            index=index,
        )
        return {
            "prices": prices[symbols],
            "funding": funding[symbols],
            "source": "stub_perp",
            "bundle_as_of": index[-1].isoformat(),
        }


class CanonicalRunArtifactTests(unittest.IsolatedAsyncioTestCase):
    async def test_evaluation_retains_canonical_run_series(self) -> None:
        settings = _settings()
        spec = SignalSpec.from_dict(
            {
                "track": "trend_signals",
                "family": "perp_multi_asset_decision",
                "hypothesis": "test canonical run retention",
                "neutrality_basis": "none",
                "features": ["price_return_72h", "ema_gap_12_26"],
                "universe": {
                    "basis_groups": ["BTC", "ETH"],
                    "chains": ["hyperevm"],
                    "max_symbols": 2,
                    "lookback_days": 30,
                    "interval": "1h",
                },
                "risk": {
                    "max_asset_weight": 0.5,
                    "rebalance_threshold": 0.0,
                    "roll_days_before_expiry": 5,
                    "max_leverage": 1.0,
                },
                "params": {
                    "long_count": 1,
                    "short_count": 1,
                    "gross_target": 1.0,
                    "long_enabled": True,
                    "short_enabled": True,
                },
            }
        )

        evaluation = await ResearchEvaluator(settings, StubPerpProvider()).evaluate(spec)

        canonical_run = evaluation["canonical_run"]
        summary = evaluation["summary"]
        self.assertIsNotNone(canonical_run)
        self.assertEqual(canonical_run["window"], "full")
        self.assertEqual(canonical_run["leverage"], 1.0)
        self.assertFalse(summary["strict_holdout"])
        self.assertTrue(summary["validation_available"])
        self.assertFalse(summary["audit_available"])
        self.assertTrue(summary["holdout_available"])
        self.assertIn("pre_audit_canonical_total_return", summary)
        self.assertIn("pre_audit_canonical_end_equity", summary)
        self.assertIn("pre_audit_canonical_max_drawdown", summary)
        self.assertGreater(len(canonical_run["equity_curve"]["index"]), 10)
        self.assertEqual(
            len(canonical_run["equity_curve"]["index"]),
            len(canonical_run["equity_curve"]["values"]),
        )
        self.assertIn("visual_split", canonical_run)
        self.assertIn("evaluation_windows", canonical_run)
        self.assertFalse(canonical_run["visual_split"]["strict_holdout"])
        metrics = canonical_run["metrics_by_period"]
        self.assertIn("cash_balance", metrics["columns"])
        self.assertIn("margin_headroom", metrics["columns"])
        self.assertIn("changes", canonical_run["target_weights"])
        self.assertTrue(canonical_run["trade_count"] >= 0)

    async def test_pair_canonical_run_includes_regime_diagnostics(self) -> None:
        settings = _settings()
        spec = SignalSpec.from_dict(
            {
                "track": "trend_signals",
                "family": "perp_pair_trade_unlevered",
                "hypothesis": "test pair regime diagnostics",
                "neutrality_basis": "none",
                "features": ["pair_return_spread_24h", "pair_residual_z_60"],
                "universe": {
                    "basis_groups": ["ETH", "BTC"],
                    "chains": ["hyperevm"],
                    "max_symbols": 2,
                    "lookback_days": 30,
                    "interval": "1h",
                },
                "risk": {
                    "max_asset_weight": 0.5,
                    "rebalance_threshold": 0.0,
                    "roll_days_before_expiry": 5,
                    "max_leverage": 1.0,
                },
                "params": {
                    "gross_target": 1.0,
                    "max_gross_target": 1.0,
                    "entry_abs_score": 0.1,
                    "exit_abs_score": 0.05,
                    "flip_abs_score": 0.15,
                    "max_holding_bars": 24,
                    "cooldown_bars": 2,
                },
            }
        )

        evaluation = await ResearchEvaluator(settings, StubPerpProvider()).evaluate(spec)

        regime = evaluation["canonical_run"]["regime_diagnostics"]
        self.assertTrue(regime["available"])
        self.assertIn("market_trend", regime["bar_slices"])
        self.assertIn("pair_volatility", regime["bar_slices"])
        self.assertIn("holding_period_buckets", regime)
        self.assertEqual(len(regime["holding_period_buckets"]), 4)
        self.assertIn("returns", evaluation["canonical_run"])
        self.assertIn("trade_episodes", evaluation["canonical_run"])
        self.assertTrue(len(evaluation["canonical_run"]["trade_episodes"]) >= 1)
        self.assertIn("entry_regime", evaluation["canonical_run"]["trade_episodes"][0])
        self.assertIn("exit_regime", evaluation["canonical_run"]["trade_episodes"][0])
        self.assertIn("regime_snapshot", evaluation["canonical_run"]["trades"][0])
        self.assertIn("pre_audit_drawdown_pack", evaluation["canonical_run"])
        self.assertIn("pre_audit_context_pack", evaluation["canonical_run"])
        self.assertIn("gate_diagnostics", evaluation["canonical_run"]["pre_audit_context_pack"])
        self.assertIn("equity_shift_pack", evaluation["canonical_run"]["pre_audit_context_pack"])
        self.assertIn("time_bin_pack", evaluation["canonical_run"]["pre_audit_context_pack"])
        self.assertIn("exemplar_trades", evaluation["canonical_run"]["pre_audit_context_pack"])
        self.assertIn(
            "market_trend_label",
            evaluation["canonical_run"]["trades"][0]["regime_snapshot"],
        )

    async def test_cross_sectional_global_features_do_not_leak_into_target_weights(self) -> None:
        settings = _settings()
        spec = SignalSpec.from_dict(
            {
                "track": "trend_signals",
                "family": "perp_multi_asset_carry",
                "hypothesis": "test cross-sectional global feature alignment",
                "neutrality_basis": "basket",
                "features": [
                    "funding_72h_mean",
                    "funding_dispersion_72h",
                    "relative_carry_72h",
                ],
                "universe": {
                    "basis_groups": ["BTC", "ETH"],
                    "chains": ["hyperevm"],
                    "max_symbols": 2,
                    "lookback_days": 30,
                    "interval": "1h",
                },
                "risk": {
                    "max_asset_weight": 0.5,
                    "rebalance_threshold": 0.0,
                    "roll_days_before_expiry": 5,
                    "max_leverage": 1.0,
                },
                "params": {
                    "long_count": 1,
                    "short_count": 1,
                    "gross_target": 1.0,
                    "min_abs_score": 0.0,
                },
            }
        )

        evaluation = await ResearchEvaluator(settings, StubPerpProvider()).evaluate(spec)

        weight_columns = evaluation["canonical_run"]["target_weights"]["columns"]
        self.assertNotIn("GLOBAL", weight_columns)
        self.assertEqual(weight_columns, ["BTC", "ETH"])

    async def test_pair_regime_gates_can_block_entries(self) -> None:
        settings = _settings()
        spec = SignalSpec.from_dict(
            {
                "track": "trend_signals",
                "family": "perp_pair_trade_unlevered",
                "hypothesis": "test pair regime gate blocking",
                "neutrality_basis": "none",
                "features": ["pair_return_spread_24h", "pair_residual_z_60"],
                "universe": {
                    "basis_groups": ["ETH", "BTC"],
                    "chains": ["hyperevm"],
                    "max_symbols": 2,
                    "lookback_days": 30,
                    "interval": "1h",
                },
                "risk": {
                    "max_asset_weight": 0.5,
                    "rebalance_threshold": 0.0,
                    "roll_days_before_expiry": 5,
                    "max_leverage": 1.0,
                },
                "regime_gates": {
                    "entry": [
                        "ge(pair_corr_72h,1.5)",
                    ],
                    "exit_on_break": True,
                },
                "params": {
                    "gross_target": 1.0,
                    "max_gross_target": 1.0,
                    "entry_abs_score": 0.1,
                    "exit_abs_score": 0.05,
                    "flip_abs_score": 0.15,
                    "max_holding_bars": 24,
                    "cooldown_bars": 2,
                },
            }
        )

        evaluation = await ResearchEvaluator(settings, StubPerpProvider()).evaluate(spec)

        canonical_run = evaluation["canonical_run"]
        self.assertEqual(canonical_run["trade_count"], 0)
        gate_diagnostics = canonical_run["pre_audit_context_pack"]["gate_diagnostics"]
        self.assertTrue(gate_diagnostics["regime_gates"]["configured"])
        self.assertEqual(gate_diagnostics["regime_gates"]["active_fraction"], 0.0)

    async def test_basket_neutral_family_uses_generic_pre_audit_context(self) -> None:
        settings = _settings()
        spec = SignalSpec.from_dict(
            {
                "track": "trend_signals",
                "family": "perp_basket_neutral_unlevered",
                "hypothesis": "test basket neutral diagnostics",
                "neutrality_basis": "none",
                "features": [
                    "price_return_24h",
                    "funding_carry_to_vol",
                    "ema_gap_12_26",
                ],
                "universe": {
                    "basis_groups": ["BTC", "ETH"],
                    "chains": ["hyperevm"],
                    "max_symbols": 2,
                    "lookback_days": 30,
                    "interval": "1h",
                },
                "risk": {
                    "max_asset_weight": 0.5,
                    "rebalance_threshold": 0.0,
                    "roll_days_before_expiry": 5,
                    "max_leverage": 1.0,
                },
                "params": {
                    "gross_target": 1.0,
                    "long_count": 1,
                    "short_count": 1,
                    "min_abs_score": 0.0,
                },
            }
        )

        evaluation = await ResearchEvaluator(settings, StubPerpProvider()).evaluate(spec)

        regime = evaluation["canonical_run"]["regime_diagnostics"]
        self.assertTrue(regime["available"])
        self.assertIn("market_trend", regime["bar_slices"])
        self.assertIn("market_volatility", regime["bar_slices"])
        self.assertIn("co_movement", regime["bar_slices"])
        self.assertIn("trade_episodes", evaluation["canonical_run"])
        self.assertTrue(len(evaluation["canonical_run"]["trade_episodes"]) >= 1)
        first_episode = evaluation["canonical_run"]["trade_episodes"][0]
        self.assertIn("active_assets", first_episode)
        self.assertIn("entry_regime", first_episode)
        context_pack = evaluation["canonical_run"]["pre_audit_context_pack"]
        self.assertIn("gate_diagnostics", context_pack)
        self.assertIn("trade_regime_pack", context_pack)
        self.assertIn("co_movement", context_pack["trade_regime_pack"])

    async def test_multi_asset_carry_family_compiles_with_generic_signal_context(self) -> None:
        settings = _settings()
        spec = SignalSpec.from_dict(
            {
                "track": "trend_signals",
                "family": "perp_multi_asset_carry",
                "hypothesis": "test carry family diagnostics",
                "neutrality_basis": "none",
                "features": [
                    "price_return_24h",
                    "funding_carry_to_vol",
                    "ema_gap_12_26",
                ],
                "universe": {
                    "basis_groups": ["BTC", "ETH"],
                    "chains": ["hyperevm"],
                    "max_symbols": 2,
                    "lookback_days": 30,
                    "interval": "1h",
                },
                "risk": {
                    "max_asset_weight": 0.5,
                    "rebalance_threshold": 0.0,
                    "roll_days_before_expiry": 5,
                    "max_leverage": 1.5,
                },
                "params": {
                    "gross_target": 1.0,
                    "long_count": 1,
                    "short_count": 1,
                    "min_abs_score": 0.05,
                },
            }
        )

        evaluation = await ResearchEvaluator(settings, StubPerpProvider()).evaluate(spec)

        gate_diag = evaluation["canonical_run"]["pre_audit_context_pack"]["gate_diagnostics"]
        self.assertIn("median_active_asset_count", gate_diag)
        self.assertIn("score_alignment_when_active", gate_diag)
        self.assertIn("drawdown", evaluation["canonical_run"]["pre_audit_drawdown_pack"])
        self.assertIn("trade_episodes", evaluation["canonical_run"])


if __name__ == "__main__":
    unittest.main()


