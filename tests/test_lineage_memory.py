from __future__ import annotations

import json
import unittest
from pathlib import Path

from siglab.schemas import SignalSpec
from tests._factories import make_lineage_store_ctx


def _spec_payload(
    *,
    track: str,
    family: str,
    assets: list[str],
    features: list[str],
    params: dict | None = None,
) -> dict:
    return {
        "track": track,
        "family": family,
        "hypothesis": "test spec",
        "neutrality_basis": "none" if track == "trend_signals" else "underlying",
        "features": features,
        "universe": {
            "basis_groups": assets,
            "chains": ["hyperevm"],
            "max_symbols": len(assets),
            "lookback_days": 90,
            "interval": "1h",
            "min_days_to_expiry": 7,
            "max_days_to_expiry": 45,
        },
        "risk": {
            "max_asset_weight": 0.35,
            "max_chain_weight": 1.0,
            "rebalance_threshold": 0.02,
            "roll_days_before_expiry": 5,
            "max_leverage": 1.0,
        },
        "params": {
            "gross_target": 1.0,
            "long_enabled": True,
            "short_enabled": True,
            "long_count": 1,
            "short_count": 1,
            **dict(params or {}),
        },
    }


class LineageMemoryTests(unittest.TestCase):
    def test_novelty_pressure_allows_family_concentration_when_family_has_positive_anchor(self) -> None:
        with make_lineage_store_ctx() as (ancestry, _tmp):

            rows = [
                {
                    "spec_hash": f"hash-{idx}",
                    "family": "perp_multi_asset_carry",
                    "spec": {"features": ["funding_72h_mean", "funding_carry_to_vol"]},
                    "summary": {
                        "pre_audit_canonical_total_return": 0.18 if idx == 0 else -0.01 * idx,
                    },
                }
                for idx in range(5)
            ]
            diagnostics_by_hash = {
                f"hash-{idx}": {"trade_style": style}
                for idx, style in enumerate(
                    ["carry", "hybrid", "momentum", "reversion", "carry"]
                )
            }

            novelty = ancestry._novelty_pressure(rows, diagnostics_by_hash=diagnostics_by_hash)

            self.assertFalse(novelty["required"])
            self.assertTrue(novelty["dominant_family_positive_anchor"])
            self.assertEqual(novelty["dominant_family"]["family"], "perp_multi_asset_carry")

    def test_recent_rows_include_parent_hash_and_parse_cleanly(self) -> None:
        with make_lineage_store_ctx() as (ancestry, _tmp):

            parent = SignalSpec.from_dict(
                _spec_payload(
                    track="trend_signals",
                    family="perp_pair_trade_unlevered",
                    assets=["ETH", "BTC"],
                    features=["pair_return_spread_24h"],
                )
            )
            child = SignalSpec.from_dict(
                _spec_payload(
                    track="trend_signals",
                    family="perp_pair_trade_unlevered",
                    assets=["ETH", "BTC"],
                    features=["pair_return_spread_24h", "pair_corr_72h"],
                )
            )

            ancestry.record(
                evaluation={
                    "spec": child.canonical_dict(),
                    "spec_hash": child.strategy_hash(),
                    "summary": {
                        "aggregate_score": 1.0,
                        "median_sharpe": 0.4,
                        "median_cagr": 0.03,
                        "median_total_return": 0.01,
                        "passed": False,
                        "gate_reasons": ["drawdown_limit"],
                    },
                },
                parent_hash=parent.strategy_hash(),
                research_summary={"track": "trend_signals"},
                artifact_path=None,
            )

            rows = ancestry.recent("trend_signals", limit=5)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["spec_hash"], child.strategy_hash())
            self.assertEqual(rows[0]["parent_hash"], parent.strategy_hash())

    def test_recent_can_exclude_deterministic_rows(self) -> None:
        with make_lineage_store_ctx() as (ancestry, _tmp):
            deterministic = SignalSpec.from_dict(
                _spec_payload(
                    track="trend_signals",
                    family="perp_pair_trade_unlevered",
                    assets=["ETH", "BTC"],
                    features=["pair_return_spread_24h"],
                )
            )
            llm_child = SignalSpec.from_dict(
                _spec_payload(
                    track="trend_signals",
                    family="perp_pair_trade_unlevered",
                    assets=["ETH", "BTC"],
                    features=["pair_corr_72h"],
                )
            )

            ancestry.record(
                evaluation={
                    "spec": deterministic.canonical_dict(),
                    "spec_hash": deterministic.strategy_hash(),
                    "summary": {
                        "aggregate_score": 0.5,
                        "median_sharpe": 0.1,
                        "median_cagr": 0.01,
                        "median_total_return": 0.0,
                        "passed": False,
                        "gate_reasons": [],
                    },
                },
                parent_hash=None,
                research_summary={
                    "track": "trend_signals",
                    "run_context": {"deterministic": True, "phase_label": "burn_in"},
                },
                artifact_path=None,
            )
            ancestry.record(
                evaluation={
                    "spec": llm_child.canonical_dict(),
                    "spec_hash": llm_child.strategy_hash(),
                    "summary": {
                        "aggregate_score": 0.8,
                        "median_sharpe": 0.2,
                        "median_cagr": 0.02,
                        "median_total_return": 0.01,
                        "passed": False,
                        "gate_reasons": [],
                    },
                },
                parent_hash=deterministic.strategy_hash(),
                research_summary={
                    "track": "trend_signals",
                    "run_context": {"deterministic": False, "phase_label": "main"},
                },
                artifact_path=None,
            )

            rows = ancestry.recent("trend_signals", limit=5, include_deterministic=False)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["spec_hash"], llm_child.strategy_hash())

    def test_session_local_scope_filters_recent_dashboard_and_best(self) -> None:
        with make_lineage_store_ctx() as (ancestry, _tmp):

            first = SignalSpec.from_dict(
                _spec_payload(
                    track="trend_signals",
                    family="perp_multi_asset_carry",
                    assets=["BTC", "ETH", "SOL", "HYPE"],
                    features=["funding_72h_mean"],
                )
            )
            second = SignalSpec.from_dict(
                _spec_payload(
                    track="trend_signals",
                    family="perp_multi_asset_carry",
                    assets=["BTC", "ETH", "SOL", "HYPE"],
                    features=["funding_168h_mean"],
                )
            )

            for spec, run_session_id, score in (
                (first, "run-1", 4.0),
                (second, "run-2", 7.5),
            ):
                ancestry.record(
                    evaluation={
                        "spec": spec.canonical_dict(),
                        "spec_hash": spec.strategy_hash(),
                        "summary": {
                            "aggregate_score": score,
                            "median_sharpe": 0.5,
                            "median_cagr": 0.03,
                            "median_total_return": 0.02,
                            "passed": True,
                            "gate_reasons": [],
                        },
                    },
                    parent_hash=None,
                    research_summary={
                        "track": "trend_signals",
                        "run_context": {
                            "run_session_id": run_session_id,
                            "phase_label": "main",
                            "deterministic": False,
                        },
                    },
                    artifact_path=None,
                )

            self.assertEqual(len(ancestry.recent("trend_signals", limit=5)), 2)
            self.assertEqual(
                len(ancestry.recent("trend_signals", limit=5, run_session_id="run-1")),
                1,
            )
            self.assertEqual(
                ancestry.best("trend_signals", run_session_id="run-1")["spec_hash"],
                first.strategy_hash(),
            )
            self.assertEqual(
                ancestry.best("trend_signals", run_session_id="run-2")["spec_hash"],
                second.strategy_hash(),
            )
            self.assertEqual(
                [row["spec_hash"] for row in ancestry.dashboard_rows(track="trend_signals", run_session_id="run-1")],
                [first.strategy_hash()],
            )

    def test_dashboard_rows_keep_repeat_evaluations_while_recent_stays_unique(self) -> None:
        with make_lineage_store_ctx() as (ancestry, tmp):
            spec = SignalSpec.from_dict(
                _spec_payload(
                    track="trend_signals",
                    family="perp_multi_asset_carry",
                    assets=["BTC", "ETH", "SOL", "HYPE"],
                    features=["funding_72h_mean", "funding_carry_to_vol"],
                )
            )

            for iteration_number in (1, 2):
                ancestry.record(
                    evaluation={
                        "spec": spec.canonical_dict(),
                        "spec_hash": spec.strategy_hash(),
                        "summary": {
                            "aggregate_score": 1.0 + iteration_number,
                            "median_sharpe": 0.2,
                            "median_cagr": 0.01,
                            "median_total_return": 0.005,
                            "passed": False,
                            "gate_reasons": [],
                        },
                    },
                    parent_hash=None,
                    research_summary={
                        "track": "trend_signals",
                        "run_context": {
                            "run_session_id": "run-1",
                            "phase_label": "burn_in",
                            "iteration_number": iteration_number,
                            "deterministic": True,
                        },
                    },
                    artifact_path=None,
                )

            rows = ancestry.recent("trend_signals", limit=5)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["spec_hash"], spec.strategy_hash())

            dashboard_rows = ancestry.dashboard_rows(track="trend_signals")
            self.assertEqual(len(dashboard_rows), 2)
            self.assertEqual(
                [row["research_summary"]["run_context"]["iteration_number"] for row in dashboard_rows],
                [1, 2],
            )

    def test_memory_packet_surfaces_similar_runs_and_query_cards(self) -> None:
        with make_lineage_store_ctx() as (ancestry, tmp):
            winner = _spec_payload(
                track="trend_signals",
                family="perp_pair_trade_unlevered",
                assets=["ETH", "BTC"],
                features=["pair_return_spread_24h", "pair_trend_efficiency_spread_72h"],
                params={"trade_style": "continuation", "entry_abs_score": 0.2, "cooldown_bars": 4},
            )
            failure = _spec_payload(
                track="trend_signals",
                family="perp_pair_trade_unlevered",
                assets=["ETH", "BTC"],
                features=["pair_residual_z_60", "pair_bollinger_width_20"],
                params={"trade_style": "reversion", "entry_abs_score": 0.18, "cooldown_bars": 0},
            )
            unrelated = _spec_payload(
                track="yield_flows",
                family="stable_pt_ladder",
                assets=["USD"],
                features=["pt_discount_to_par", "expiry_roll_down"],
            )

            winner_artifact = Path(tmp) / "winner.json"
            winner_artifact.write_text(
                json.dumps(
                    {
                        "canonical_run": {
                            "pre_audit_drawdown_pack": {
                                "drawdown": -0.03,
                                "dominant_position_direction": "long_asset_1_short_asset_2",
                                "signal_story": {
                                    "window_median_score": 0.18,
                                    "trough_score": 0.12,
                                    "aligned_with_position_fraction": 0.75,
                                },
                                "top_feature_contributors": [
                                    {
                                        "feature": "pair_trend_efficiency_spread_72h",
                                        "window_median_component": 0.11,
                                        "trough_component": 0.08,
                                        "aligned_with_position_fraction": 0.75,
                                    }
                                ],
                            },
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
                                    "end_timestamp": "2026-01-08T00:00:00+00:00",
                                    "bars": 12,
                                    "total_return": 0.03,
                                    "entry_regime": {
                                        "market_trend_label": "market_uptrend",
                                        "pair_volatility_label": "low_volatility",
                                        "funding_dispersion_label": "funding_compressed",
                                        "pair_correlation_label": "high_correlation",
                                        "pair_direction_label": "asset_1_leading",
                                    },
                                    "exit_regime": {},
                                },
                                {
                                    "direction": "long_asset_1_short_asset_2",
                                    "start_timestamp": "2026-02-01T00:00:00+00:00",
                                    "end_timestamp": "2026-02-04T00:00:00+00:00",
                                    "bars": 10,
                                    "total_return": 0.01,
                                    "entry_regime": {
                                        "market_trend_label": "market_uptrend",
                                        "pair_volatility_label": "low_volatility",
                                        "funding_dispersion_label": "funding_compressed",
                                        "pair_correlation_label": "high_correlation",
                                        "pair_direction_label": "asset_1_leading",
                                    },
                                    "exit_regime": {},
                                },
                            ],
                        }
                    }
                )
            )
            failure_artifact = Path(tmp) / "failure.json"
            failure_artifact.write_text(
                json.dumps(
                    {
                        "canonical_run": {
                            "pre_audit_drawdown_pack": {
                                "drawdown": -0.14,
                                "dominant_position_direction": "long_asset_1_short_asset_2",
                                "signal_story": {
                                    "window_median_score": 0.63,
                                    "trough_score": 0.71,
                                    "aligned_with_position_fraction": 1.0,
                                },
                                "top_feature_contributors": [
                                    {
                                        "feature": "pair_residual_z_60",
                                        "window_median_component": 0.24,
                                        "trough_component": 0.31,
                                        "aligned_with_position_fraction": 1.0,
                                    },
                                    {
                                        "feature": "pair_bollinger_width_20",
                                        "window_median_component": -0.09,
                                        "trough_component": -0.04,
                                        "aligned_with_position_fraction": 0.0,
                                    },
                                ],
                            },
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
                                    "start_timestamp": "2026-01-03T00:00:00+00:00",
                                    "end_timestamp": "2026-01-03T06:00:00+00:00",
                                    "bars": 3,
                                    "total_return": -0.02,
                                    "entry_regime": {
                                        "market_trend_label": "market_downtrend",
                                        "pair_volatility_label": "high_volatility",
                                        "funding_dispersion_label": "funding_dispersed",
                                        "pair_correlation_label": "low_correlation",
                                        "pair_direction_label": "asset_2_leading",
                                    },
                                    "exit_regime": {},
                                },
                                {
                                    "direction": "short_asset_1_long_asset_2",
                                    "start_timestamp": "2026-01-03T12:00:00+00:00",
                                    "end_timestamp": "2026-01-03T18:00:00+00:00",
                                    "bars": 3,
                                    "total_return": -0.01,
                                    "entry_regime": {
                                        "market_trend_label": "market_downtrend",
                                        "pair_volatility_label": "high_volatility",
                                        "funding_dispersion_label": "funding_dispersed",
                                        "pair_correlation_label": "low_correlation",
                                        "pair_direction_label": "asset_2_leading",
                                    },
                                    "exit_regime": {},
                                },
                            ],
                        }
                    }
                )
            )

            ancestry.record(
                evaluation={
                    "spec": winner,
                    "spec_hash": SignalSpec.from_dict(winner).strategy_hash(),
                    "summary": {
                        "aggregate_score": 2.5,
                        "median_sharpe": 1.4,
                        "median_cagr": 0.11,
                        "median_total_return": 0.03,
                        "validation_total_return": 0.02,
                        "validation_sharpe": 1.2,
                        "holdout_total_return": 0.02,
                        "passed": True,
                        "gate_reasons": [],
                    },
                },
                parent_hash=None,
                research_summary={"track": "trend_signals"},
                artifact_path=str(winner_artifact),
            )
            ancestry.record(
                evaluation={
                    "spec": failure,
                    "spec_hash": SignalSpec.from_dict(failure).strategy_hash(),
                    "summary": {
                        "aggregate_score": -0.2,
                        "median_sharpe": -0.3,
                        "median_cagr": -0.02,
                        "median_total_return": -0.01,
                        "validation_total_return": -0.03,
                        "validation_sharpe": -1.1,
                        "holdout_total_return": -0.02,
                        "passed": False,
                        "gate_reasons": [
                            "non_positive_median_return",
                            "non_positive_validation_sharpe",
                        ],
                    },
                },
                parent_hash=None,
                research_summary={"track": "trend_signals"},
                artifact_path=str(failure_artifact),
            )
            ancestry.record(
                evaluation={
                    "spec": unrelated,
                    "spec_hash": SignalSpec.from_dict(unrelated).strategy_hash(),
                    "summary": {
                        "aggregate_score": 3.0,
                        "median_sharpe": 1.1,
                        "median_cagr": 0.09,
                        "median_total_return": 0.02,
                        "holdout_total_return": 0.01,
                        "passed": True,
                        "gate_reasons": [],
                    },
                },
                parent_hash=None,
                research_summary={"track": "yield_flows"},
                artifact_path=str(Path(tmp) / "carry.json"),
            )

            parent = SignalSpec.from_dict(winner)
            ancestry.record_query_cards(
                track="trend_signals",
                family=parent.family,
                parent_hash=parent.strategy_hash(),
                market_bundle={"bundle_id": "bundle-123", "as_of": "2026-03-13T00:00:00+00:00"},
                external_research={
                    "provider": "tavily+web",
                    "reports": [
                        {
                            "query": "BTC ETH perp momentum funding overlay risk management",
                            "answer": "Momentum with funding confirmation can improve selection stability.",
                            "insights": [
                                "Funding works better as a filter than as a standalone long-short signal.",
                            ],
                            "sources": [{"url": "https://example.com/research"}],
                        }
                    ],
                },
            )

            packet = ancestry.memory_packet(
                track="trend_signals",
                parent=parent,
                market_bundle={"bundle_id": "bundle-123", "as_of": "2026-03-13T00:00:00+00:00"},
            )

            self.assertEqual(packet["market_bundle"]["bundle_id"], "bundle-123")
            self.assertEqual(packet["coverage_summary"]["experiments_total"], 2)
            self.assertEqual(packet["nearest_winners"][0]["family"], "perp_pair_trade_unlevered")
            self.assertIn("non_positive_median_return", packet["nearest_failures"][0]["gate_reasons"])
            self.assertTrue(packet["pareto_frontier"])
            self.assertTrue(packet["query_cards"])
            self.assertIn("BTC", packet["query_cards"][0]["query"])
            self.assertEqual(packet["validation_leaders"][0]["trade_style"], "continuation")
            self.assertEqual(packet["nearest_failures"][0]["trade_style"], "reversion")
            self.assertEqual(packet["nearest_failures"][0]["policy"]["entry_abs_score"], 0.18)
            self.assertTrue(packet["outstanding_runs"])
            self.assertEqual(packet["outstanding_runs"][0]["spec_hash"], SignalSpec.from_dict(failure).strategy_hash())
            self.assertTrue(packet["last_five_runs"])
            self.assertIn("novelty_pressure", packet)
            self.assertIn("sweep_drift", packet["last_five_runs"][0])
            self.assertIn("diagnostic_tags", packet["nearest_failures"][0])
            self.assertEqual(
                packet["failure_pattern_summary"]["gate_reasons"][0]["reason"],
                "non_positive_median_return",
            )
            self.assertTrue(packet["behavior_pattern_summary"]["median_flip_rate"] is not None)
            self.assertEqual(
                packet["regime_pattern_summary"]["pair_volatility"][0]["label"],
                "high_volatility",
            )
            self.assertEqual(
                packet["nearest_failures"][0]["drawdown_pack"]["dominant_position_direction"],
                "long_asset_1_short_asset_2",
            )
            self.assertEqual(
                packet["nearest_failures"][0]["drawdown_pack"]["top_feature_contributors"][0]["feature"],
                "pair_residual_z_60",
            )
            self.assertEqual(
                packet["drawdown_pattern_summary"]["common_feature_contributors"][0]["feature"],
                "pair_residual_z_60",
            )
            self.assertIn("gate_pattern_summary", packet)
            self.assertIn("equity_pattern_summary", packet)
            self.assertIn("gate_diagnostics", packet["nearest_failures"][0])
            self.assertIn("equity_shift_pack", packet["nearest_failures"][0])
            self.assertIn("time_bin_pack", packet["nearest_failures"][0])
            self.assertIn("exemplar_trade_pack", packet["nearest_failures"][0])
            archetypes = {row["trade_style"] for row in packet["archetype_coverage"]}
            self.assertIn("continuation", archetypes)
            self.assertIn("reversion", archetypes)

    def test_memory_packet_handles_generic_cross_sectional_regime_labels(self) -> None:
        with make_lineage_store_ctx() as (ancestry, tmp):

            spec = SignalSpec.from_dict(
                _spec_payload(
                    track="trend_signals",
                    family="perp_basket_neutral_unlevered",
                    assets=["BTC", "ETH", "SOL"],
                    features=["price_return_24h", "funding_carry_to_vol"],
                    params={"long_count": 2, "short_count": 2},
                )
            )
            artifact = Path(tmp) / "basket.json"
            artifact.write_text(
                json.dumps(
                    {
                        "canonical_run": {
                            "visual_split": {
                                "ranges": [
                                    {
                                        "kind": "rolling_selector",
                                        "start_timestamp": "2026-01-01T00:00:00+00:00",
                                        "end_timestamp": "2026-02-28T00:00:00+00:00",
                                    }
                                ]
                            },
                            "trade_episodes": [
                                {
                                    "direction": "market_neutral",
                                    "start_timestamp": "2026-01-05T00:00:00+00:00",
                                    "end_timestamp": "2026-01-06T00:00:00+00:00",
                                    "bars": 12,
                                    "total_return": -0.02,
                                    "entry_regime": {
                                        "market_trend_label": "market_downtrend",
                                        "market_volatility_label": "high_volatility",
                                        "co_movement_label": "low_co_movement",
                                    },
                                    "exit_regime": {},
                                }
                            ],
                            "pre_audit_drawdown_pack": {"drawdown": -0.08},
                            "pre_audit_context_pack": {
                                "gate_diagnostics": {"position_flip_rate": 0.05},
                                "equity_shift_pack": {
                                    "max_drawdown": -0.08,
                                    "drawdown_window": {
                                        "entries_per_day": 0.3,
                                        "regime": {
                                            "market_trend_label": "market_downtrend",
                                            "co_movement_label": "low_co_movement",
                                        },
                                    },
                                },
                                "time_bin_pack": {"windows": []},
                                "exemplar_trades": {"winners": [], "losers": []},
                            },
                        }
                    }
                )
            )

            ancestry.record(
                evaluation={
                    "spec": spec.canonical_dict(),
                    "spec_hash": spec.strategy_hash(),
                    "summary": {
                        "aggregate_score": 1.0,
                        "median_sharpe": -0.1,
                        "median_cagr": -0.02,
                        "median_total_return": -0.01,
                        "pre_audit_canonical_total_return": -0.03,
                        "passed": False,
                        "gate_reasons": ["non_positive_median_return"],
                    },
                },
                parent_hash=None,
                research_summary={"track": "trend_signals"},
                artifact_path=str(artifact),
            )

            packet = ancestry.memory_packet(
                track="trend_signals",
                parent=spec,
                market_bundle={"bundle_id": "bundle-xyz"},
            )

            self.assertIn("co_movement", packet["regime_pattern_summary"])
            self.assertEqual(
                packet["regime_pattern_summary"]["co_movement"][0]["label"],
                "low_co_movement",
            )
            self.assertIn("co_movement_label", packet["equity_pattern_summary"]["drawdown_window_regimes"])


if __name__ == "__main__":
    unittest.main()


