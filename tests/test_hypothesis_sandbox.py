from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from siglab.data.lake import ParquetLake
from siglab.models import SignalSpec
from siglab.research.hypothesis import HypothesisSandbox
from siglab.search import LineageStore
from siglab.settings import SiglabConfig

REPO_ROOT = Path(__file__).resolve().parents[1]


class StubPerpProvider:
    def __init__(self) -> None:
        index = pd.date_range("2024-01-01", periods=2_000, freq="h")
        steps = np.arange(len(index), dtype=float)
        asset_1 = 100.0 + (0.03 * steps) + (2.5 * np.sin(steps / 18.0))
        asset_2 = 95.0 + (0.018 * steps) + (2.0 * np.cos(steps / 24.0))
        self.bundle = {
            "prices": pd.DataFrame(
                {
                    "ETH": asset_1,
                    "BTC": asset_2,
                },
                index=index,
            ),
            "funding": pd.DataFrame(
                {
                    "ETH": 0.00015 + (0.00005 * np.sin(steps / 30.0)),
                    "BTC": 0.00010 + (0.00004 * np.cos(steps / 35.0)),
                },
                index=index,
            ),
            "source": "stub_delta_lab",
        }

    async def discover_perp_symbols(self, preferred_symbols: list[str], *, limit: int) -> list[str]:
        return [str(symbol).upper() for symbol in preferred_symbols][:limit]

    async def fetch_perp_bundle(
        self,
        *,
        symbols: list[str],
        lookback_days: int,
        interval: str,
    ) -> dict:
        return {
            "prices": self.bundle["prices"][symbols],
            "funding": self.bundle["funding"][symbols],
            "source": self.bundle["source"],
        }

    def current_bundle_context(self) -> dict[str, str]:
        return {"as_of": "2026-03-13T20:00:00+00:00"}


class HypothesisSandboxTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.settings = SiglabConfig(
            root_dir=REPO_ROOT,
            sosovalue_config_path=root / "config.json",
            generated_strategy_dir=root / "deployed_agents",
            data_lake_dir=root / "lake",
            artifact_dir=root / "runs",
            live_dir=root / "live",
            ancestry_db_path=root / "siglab_test.db",
            sosovalue_api_key_override=None,
            claude_api_key="sk-test",
            claude_model="claude-k2.5",
            claude_base_url="https://api.moonshot.ai/v1",
            claude_max_tokens=1024,
            claude_temperature=1.0,
            claude_top_p=0.95,
            claude_timeout_s=30.0,
            claude_thinking=None,
            claude_max_tool_rounds=4,
            population_size=1,
        )
        self.lake = ParquetLake(self.settings.data_lake_dir)
        self.provider = StubPerpProvider()
        self.sandbox = HypothesisSandbox(self.settings, self.lake, self.provider)
        self.parent = SignalSpec.from_dict(
            {
                "track": "trend_signals",
                "family": "perp_pair_trade_unlevered",
                "hypothesis": "test pair parent",
                "neutrality_basis": "none",
                "features": ["pair_realized_vol_168h", "pair_bollinger_width_20"],
                "universe": {
                    "basis_groups": ["ETH", "BTC"],
                    "max_symbols": 2,
                    "lookback_days": 365,
                    "interval": "1h",
                },
                "risk": {},
                "params": {},
            }
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    async def test_probe_feature_forward_stats_reports_horizons_and_predictor_correlations(self) -> None:
        tool = self.sandbox.claude_tools(track="trend_signals", parent=self.parent)[0]

        result = await tool.handler(
            {
                "feature": "sub(pct_change(asset_1_price,24), pct_change(asset_2_price,24))",
            }
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["family"], "perp_pair_trade_unlevered")
        self.assertEqual(result["basis_groups"], ["ETH", "BTC"])
        self.assertTrue(result["feature_profile"]["available"])
        self.assertGreaterEqual(result["analysis_scope"]["train_window_count"], 3)
        horizons = result["forward_return_predictiveness"]
        self.assertTrue(any(row["horizon_bars"] == 24 for row in horizons))
        horizon_24 = next(row for row in horizons if row["horizon_bars"] == 24)
        self.assertTrue(horizon_24["available"])
        self.assertIn("median_spearman", horizon_24)
        self.assertGreaterEqual(len(result["predictor_correlations"]), 1)
        compared = {row["feature"] for row in result["predictor_correlations"]}
        self.assertIn("pair_realized_vol_168h", compared)

    async def test_probe_feature_forward_stats_is_explicitly_train_only(self) -> None:
        tool = self.sandbox.claude_tools(track="trend_signals", parent=self.parent)[0]

        result = await tool.handler(
            {
                "feature": "neg(div(sub(price_ratio, rolling_mean(price_ratio,60)), clip(rolling_std(price_ratio,60),0.0001,10.0)))",
                "compare_features": ["pair_bollinger_width_20", "not_a_real_feature"],
            }
        )

        self.assertTrue(result["ok"])
        scope = result["analysis_scope"]
        self.assertEqual(scope["mode"], "train_only")
        self.assertTrue(scope["validation_excluded"])
        self.assertTrue(scope["audit_excluded"])
        self.assertGreaterEqual(scope["train_window_count"], 3)
        self.assertIn("not_a_real_feature", result["invalid_compare_features"])
        self.assertIsNotNone(result["best_directional_horizon"])

    async def test_probe_feature_supports_expanded_pair_alias_surface(self) -> None:
        tool = self.sandbox.claude_tools(track="trend_signals", parent=self.parent)[0]

        result = await tool.handler(
            {
                "feature": "pair_return_spread_24h",
                "compare_features": ["pair_corr_72h", "pair_beta_stability_72h"],
            }
        )

        self.assertTrue(result["ok"])
        compared = {row["feature"] for row in result["predictor_correlations"]}
        self.assertIn("pair_corr_72h", compared)
        self.assertIn("pair_beta_stability_72h", compared)

    async def test_probe_spec_gate_impact_reports_gated_vs_ungated_train_summary(self) -> None:
        tools = {
            tool.name: tool
            for tool in self.sandbox.claude_tools(track="trend_signals", parent=self.parent)
        }
        tool = tools["probe_spec_gate_impact"]

        result = await tool.handler(
            {
                "features": ["pair_return_spread_24h", "pair_residual_z_60"],
                "regime_gates": {
                    "entry": [
                        {"expression": "ge(pair_corr_72h, 0.7)", "min": 0.0, "max": 1.0},
                        {"expression": "le(pair_corr_72h, 0.98)", "min": 0.0, "max": 1.0},
                    ],
                    "exit_on_break": True,
                },
                "params": {
                    "entry_abs_score": 0.2,
                    "exit_abs_score": 0.1,
                    "flip_abs_score": 0.25,
                    "max_holding_bars": 24,
                    "cooldown_bars": 4,
                },
            }
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["analysis_scope"]["mode"], "train_only")
        self.assertEqual(result["gate_coverage"]["gate_count"], 2)
        self.assertIn("gated", result["selector_train_comparison"])
        self.assertIn("ungated", result["selector_train_comparison"])
        self.assertIn("delta", result["selector_train_comparison"])
        self.assertGreaterEqual(len(result["kept_vs_blocked_forward_returns"]), 1)

    async def test_compare_intended_vs_frozen_spec_reports_drift_and_dropped_gates(self) -> None:
        tools = {
            tool.name: tool
            for tool in self.sandbox.claude_tools(track="trend_signals", parent=self.parent)
        }
        tool = tools["compare_intended_vs_frozen_spec"]
        ancestry = LineageStore(self.settings.ancestry_db_path)

        child = SignalSpec.from_dict(
            {
                **self.parent.canonical_dict(),
                "hypothesis": "child spec",
                "features": ["pair_residual_z_60", "pair_corr_72h"],
                "regime_gates": {
                    "entry": [
                        {"expression": "ge(pair_beta_stability_72h,0.1)"},
                        {"expression": "ge(pair_corr_72h,0.75)"},
                    ],
                    "exit_on_break": True,
                },
                "params": {
                    "trade_style": "reversion",
                    "entry_abs_score": 0.25,
                    "exit_abs_score": 0.1,
                    "flip_abs_score": 0.3,
                    "max_holding_bars": 24,
                    "cooldown_bars": 4,
                },
            }
        )

        llm_log = self.settings.artifact_dir / "llm_traces" / "trend_signals" / "intent.json"
        llm_log.parent.mkdir(parents=True, exist_ok=True)
        llm_log.write_text(
            json.dumps(
                {
                    "parsed_response": {
                        "spec": {
                            **child.canonical_dict(),
                            "regime_gates": {
                                "entry": [
                                    {"expression": "ge(pair_beta_stability_72h,0.1)"},
                                    {"expression": "ge(pair_corr_72h,0.75)"},
                                    {"expression": "le(pair_corr_72h,0.92)"},
                                ],
                                "exit_on_break": True,
                            },
                            "params": {
                                **child.params,
                                "entry_abs_score": 0.22,
                                "exit_abs_score": 0.09,
                                "flip_abs_score": 0.28,
                                "max_holding_bars": 18,
                                "cooldown_bars": 2,
                            },
                        }
                    }
                }
            )
        )

        child_artifact = self.settings.artifact_dir / "trend_signals" / "intent_child.json"
        child_artifact.parent.mkdir(parents=True, exist_ok=True)
        child_artifact.write_text(
            json.dumps(
                {
                    "compiled_metadata": {
                        "regime_gates": {
                            "configured": True,
                            "combined_active_fraction": 1.0,
                            "entry": [
                                {"expression": "ge(pair_beta_stability_72h,0.1)", "active_fraction": 1.0},
                                {"expression": "ge(pair_corr_72h,0.75)", "active_fraction": 1.0},
                            ],
                            "exit_on_break": True,
                        }
                    }
                }
            )
        )

        ancestry.record(
            evaluation={
                "spec": child.canonical_dict(),
                "spec_hash": child.strategy_hash(),
                "summary": {
                    "aggregate_score": -0.5,
                    "median_total_return": -0.01,
                    "validation_total_return": -0.02,
                    "pre_audit_canonical_total_return": -0.04,
                    "policy_sweep_material_change": True,
                    "policy_sweep_changed_keys": [
                        "entry_abs_score",
                        "exit_abs_score",
                        "flip_abs_score",
                        "max_holding_bars",
                        "cooldown_bars",
                    ],
                    "policy_sweep_proposed_policy": {
                        "entry_abs_score": 0.22,
                        "exit_abs_score": 0.09,
                        "flip_abs_score": 0.28,
                        "max_holding_bars": 18,
                        "cooldown_bars": 2,
                    },
                    "policy_sweep_frozen_policy": {
                        "entry_abs_score": 0.25,
                        "exit_abs_score": 0.1,
                        "flip_abs_score": 0.3,
                        "max_holding_bars": 24,
                        "cooldown_bars": 4,
                    },
                    "passed": False,
                    "gate_reasons": ["non_positive_validation_return"],
                },
            },
            parent_hash=self.parent.strategy_hash(),
            research_summary={
                "track": "trend_signals",
                "llm_tool_trace": {
                    "log_path": str(llm_log),
                    "trace": {"tool_names": ["probe_spec_gate_impact"]},
                },
            },
            artifact_path=str(child_artifact),
        )

        result = await tool.handler({"spec_hash": child.strategy_hash()})

        self.assertTrue(result["ok"])
        self.assertTrue(result["intent_alignment"]["sweep_drift"]["material_change"])
        self.assertIn(
            "le(pair_corr_72h,0.92)",
            result["intent_alignment"]["dropped_gate_expressions"],
        )
        self.assertIn(
            "compiled_regime_gates_were_effectively_always_open",
            result["warnings"],
        )

    async def test_summarize_experiment_frontier_reports_family_level_positive_anchors(self) -> None:
        tools = {
            tool.name: tool
            for tool in self.sandbox.claude_tools(track="trend_signals", parent=self.parent)
        }
        tool = tools["summarize_experiment_frontier"]
        ancestry = LineageStore(self.settings.ancestry_db_path)

        carry_seed = SignalSpec.from_dict(
            {
                "track": "trend_signals",
                "family": "perp_multi_asset_carry",
                "hypothesis": "carry seed",
                "neutrality_basis": "underlying",
                "features": [
                    "funding_168h_mean",
                    "funding_72h_mean",
                    "funding_carry_to_vol",
                ],
                "universe": {
                    "basis_groups": ["BTC", "ETH", "SOL", "HYPE"],
                    "max_symbols": 4,
                    "lookback_days": 365,
                    "interval": "1h",
                },
                "risk": {},
                "params": {},
            }
        )
        carry_child = SignalSpec.from_dict(
            {
                **carry_seed.canonical_dict(),
                "hypothesis": "carry with momentum overlay",
                "features": [
                    "funding_168h_mean",
                    "funding_flip_prob_14d",
                    "price_return_24h",
                ],
            }
        )
        directional_loser = SignalSpec.from_dict(
            {
                "track": "trend_signals",
                "family": "perp_multi_asset_decision",
                "hypothesis": "absolute momentum loser",
                "neutrality_basis": "none",
                "features": ["price_return_24h", "price_return_72h"],
                "universe": {
                    "basis_groups": ["BTC", "ETH", "SOL", "HYPE"],
                    "max_symbols": 4,
                    "lookback_days": 365,
                    "interval": "1h",
                },
                "risk": {},
                "params": {},
            }
        )

        def write_artifact(name: str, *, active_bar_fraction: float, bottlenecks: list[str]) -> Path:
            artifact_path = self.settings.artifact_dir / "trend_signals" / name
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            artifact_path.write_text(
                json.dumps(
                    {
                        "canonical_run": {
                            "pre_audit_context_pack": {
                                "gate_diagnostics": {
                                    "active_bar_fraction": active_bar_fraction,
                                    "bottleneck_tags": bottlenecks,
                                }
                            }
                        }
                    }
                )
            )
            return artifact_path

        ancestry.record(
            evaluation={
                "spec": carry_seed.canonical_dict(),
                "spec_hash": carry_seed.strategy_hash(),
                "summary": {
                    "aggregate_score": 3.4,
                    "median_total_return": 0.04,
                    "validation_total_return": 0.03,
                    "pre_audit_canonical_total_return": 0.28,
                    "passed": True,
                    "gate_reasons": [],
                },
            },
            parent_hash=None,
            research_summary={
                "track": "trend_signals",
                "run_context": {"deterministic": True, "phase_label": "burn_in"},
            },
            artifact_path=str(
                write_artifact(
                    "carry_seed_frontier.json",
                    active_bar_fraction=0.61,
                    bottlenecks=[],
                )
            ),
        )
        ancestry.record(
            evaluation={
                "spec": carry_child.canonical_dict(),
                "spec_hash": carry_child.strategy_hash(),
                "summary": {
                    "aggregate_score": 3.9,
                    "median_total_return": 0.05,
                    "validation_total_return": 0.04,
                    "pre_audit_canonical_total_return": 0.34,
                    "passed": True,
                    "gate_reasons": [],
                },
            },
            parent_hash=carry_seed.strategy_hash(),
            research_summary={
                "track": "trend_signals",
                "run_context": {"deterministic": False, "phase_label": "llm"},
            },
            artifact_path=str(
                write_artifact(
                    "carry_child_frontier.json",
                    active_bar_fraction=0.58,
                    bottlenecks=[],
                )
            ),
        )
        ancestry.record(
            evaluation={
                "spec": directional_loser.canonical_dict(),
                "spec_hash": directional_loser.strategy_hash(),
                "summary": {
                    "aggregate_score": -0.7,
                    "median_total_return": -0.03,
                    "validation_total_return": -0.05,
                    "pre_audit_canonical_total_return": -0.22,
                    "passed": False,
                    "gate_reasons": ["non_positive_validation_return"],
                },
            },
            parent_hash=None,
            research_summary={
                "track": "trend_signals",
                "run_context": {"deterministic": False, "phase_label": "llm"},
            },
            artifact_path=str(
                write_artifact(
                    "directional_loser_frontier.json",
                    active_bar_fraction=0.87,
                    bottlenecks=["always_in_market"],
                )
            ),
        )

        result = await tool.handler({"top_n": 3, "recent_limit": 3})

        self.assertTrue(result["ok"])
        family_summary = {row["family"]: row for row in result["family_summary"]}
        self.assertIn("perp_multi_asset_carry", family_summary)
        self.assertIn("perp_multi_asset_decision", family_summary)
        self.assertEqual(family_summary["perp_multi_asset_carry"]["positive_pre_audit_total"], 2)
        self.assertEqual(family_summary["perp_multi_asset_carry"]["deterministic_total"], 1)
        self.assertEqual(result["top_positive_anchors"][0]["family"], "perp_multi_asset_carry")
        self.assertEqual(
            result["top_positive_anchors"][0]["spec_hash"],
            carry_child.strategy_hash(),
        )
        positive_features = {row["feature"] for row in result["positive_feature_frequencies"]}
        self.assertIn("funding_168h_mean", positive_features)
        self.assertIn("funding_flip_prob_14d", positive_features)

        result_no_deterministic = await tool.handler(
            {"top_n": 3, "recent_limit": 3, "include_deterministic": False}
        )
        self.assertTrue(result_no_deterministic["ok"])
        self.assertEqual(result_no_deterministic["analysis_scope"]["non_deterministic_runs"], 2)
        self.assertEqual(
            result_no_deterministic["family_summary"][0]["deterministic_total"],
            0,
        )

    async def test_inspect_pre_audit_spec_filters_trade_episodes_and_hides_audit(self) -> None:
        tools = self.sandbox.claude_tools(track="trend_signals", parent=self.parent)
        tool = next(tool for tool in tools if tool.name == "inspect_pre_audit_spec")
        ancestry = LineageStore(self.settings.ancestry_db_path)

        parent = SignalSpec.from_dict(
            {
                **self.parent.canonical_dict(),
                "hypothesis": "parent spec",
                "features": ["pair_return_spread_24h"],
            }
        )
        child = SignalSpec.from_dict(
            {
                **self.parent.canonical_dict(),
                "hypothesis": "child spec",
                "features": ["pair_residual_z_60", "pair_corr_72h"],
                "params": {
                    "trade_style": "reversion",
                    "entry_abs_score": 0.2,
                    "exit_abs_score": 0.1,
                    "flip_abs_score": 0.3,
                    "max_holding_bars": 24,
                    "cooldown_bars": 4,
                },
            }
        )

        parent_artifact = self.settings.artifact_dir / "trend_signals" / "parent.json"
        parent_artifact.parent.mkdir(parents=True, exist_ok=True)
        parent_artifact.write_text(
            json.dumps(
                {
                    "canonical_run": {
                        "visual_split": {
                            "ranges": [
                                {
                                    "kind": "rolling_selector",
                                    "start_timestamp": "2026-01-01T00:00:00+00:00",
                                    "end_timestamp": "2026-02-28T00:00:00+00:00",
                                },
                                {
                                    "kind": "audit_holdout",
                                    "start_timestamp": "2026-03-01T00:00:00+00:00",
                                    "end_timestamp": "2026-03-31T00:00:00+00:00",
                                },
                            ]
                        },
                        "trade_episodes": [
                            {
                                "direction": "long_asset_1_short_asset_2",
                                "start_timestamp": "2026-01-05T00:00:00+00:00",
                                "end_timestamp": "2026-01-06T00:00:00+00:00",
                                "bars": 12,
                                "total_return": 0.03,
                                "entry_regime": {
                                    "pair_correlation_label": "high_correlation",
                                },
                                "exit_regime": {},
                            }
                        ],
                        "pre_audit_drawdown_pack": {"drawdown": -0.04},
                        "pre_audit_context_pack": {
                            "gate_diagnostics": {"position_flip_rate": 0.1},
                            "equity_shift_pack": {"max_drawdown": -0.04},
                            "time_bin_pack": {"windows": []},
                            "exemplar_trades": {"winners": [], "losers": []},
                        },
                    }
                }
            )
        )

        child_artifact = self.settings.artifact_dir / "trend_signals" / "child.json"
        child_artifact.write_text(
            json.dumps(
                {
                    "canonical_run": {
                        "visual_split": {
                            "ranges": [
                                {
                                    "kind": "rolling_selector",
                                    "start_timestamp": "2026-01-01T00:00:00+00:00",
                                    "end_timestamp": "2026-02-28T00:00:00+00:00",
                                },
                                {
                                    "kind": "audit_holdout",
                                    "start_timestamp": "2026-03-01T00:00:00+00:00",
                                    "end_timestamp": "2026-03-31T00:00:00+00:00",
                                },
                            ]
                        },
                        "trade_episodes": [
                            {
                                "direction": "short_asset_1_long_asset_2",
                                "start_timestamp": "2026-01-10T00:00:00+00:00",
                                "end_timestamp": "2026-01-10T08:00:00+00:00",
                                "bars": 5,
                                "total_return": -0.02,
                                "entry_regime": {
                                    "pair_correlation_label": "low_correlation",
                                    "market_trend_label": "market_downtrend",
                                },
                                "exit_regime": {},
                            },
                            {
                                "direction": "long_asset_1_short_asset_2",
                                "start_timestamp": "2026-02-10T00:00:00+00:00",
                                "end_timestamp": "2026-02-11T00:00:00+00:00",
                                "bars": 18,
                                "total_return": 0.01,
                                "entry_regime": {
                                    "pair_correlation_label": "high_correlation",
                                    "market_trend_label": "market_uptrend",
                                },
                                "exit_regime": {},
                            },
                            {
                                "direction": "short_asset_1_long_asset_2",
                                "start_timestamp": "2026-03-05T00:00:00+00:00",
                                "end_timestamp": "2026-03-05T08:00:00+00:00",
                                "bars": 5,
                                "total_return": -0.05,
                                "entry_regime": {
                                    "pair_correlation_label": "low_correlation",
                                    "market_trend_label": "market_downtrend",
                                },
                                "exit_regime": {},
                            },
                        ],
                        "pre_audit_drawdown_pack": {"drawdown": -0.12},
                        "pre_audit_context_pack": {
                            "gate_diagnostics": {"position_flip_rate": 0.35},
                            "equity_shift_pack": {"max_drawdown": -0.12},
                            "time_bin_pack": {"windows": [{"window_days": 14}]},
                            "exemplar_trades": {"winners": [], "losers": []},
                        },
                    }
                }
            )
        )

        ancestry.record(
            evaluation={
                "spec": parent.canonical_dict(),
                "spec_hash": parent.strategy_hash(),
                "summary": {
                    "aggregate_score": 1.0,
                    "median_total_return": 0.02,
                    "validation_total_return": 0.02,
                    "pre_audit_canonical_total_return": 0.03,
                    "audit_total_return": 0.99,
                    "passed": True,
                    "gate_reasons": [],
                },
            },
            parent_hash=None,
            research_summary={"track": "trend_signals"},
            artifact_path=str(parent_artifact),
        )
        ancestry.record(
            evaluation={
                "spec": child.canonical_dict(),
                "spec_hash": child.strategy_hash(),
                "summary": {
                    "aggregate_score": -0.5,
                    "median_total_return": -0.01,
                    "validation_total_return": -0.02,
                    "pre_audit_canonical_total_return": -0.04,
                    "audit_total_return": 0.88,
                    "passed": False,
                    "gate_reasons": ["non_positive_validation_return"],
                },
            },
            parent_hash=parent.strategy_hash(),
            research_summary={"track": "trend_signals"},
            artifact_path=str(child_artifact),
        )

        result = await tool.handler(
            {
                "spec_hash": child.strategy_hash(),
                "direction": "short_asset_1_long_asset_2",
                "pnl_sign": "negative",
                "regime_dimension": "pair_correlation",
                "regime_label": "low_correlation",
            }
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["episode_filter"]["all_pre_audit_episode_count"], 2)
        self.assertEqual(result["episode_filter"]["matching_episode_count"], 1)
        self.assertEqual(len(result["trade_episodes"]), 1)
        self.assertEqual(result["trade_episodes"][0]["direction"], "short_asset_1_long_asset_2")
        self.assertNotIn("audit_total_return", result["summary"])
        self.assertEqual(result["ancestry"]["parent"]["spec_hash"], parent.strategy_hash())
        self.assertEqual(result["pre_audit_context"]["gate_diagnostics"]["position_flip_rate"], 0.35)


if __name__ == "__main__":
    unittest.main()



