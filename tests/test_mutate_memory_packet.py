from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
import json
import tempfile
from types import SimpleNamespace

from siglab.search.mutate import SpecMutator
from siglab.search.select import _row_quality
from siglab.models import SignalSpec

REPO_ROOT = Path(__file__).resolve().parents[1]


class MutateMemoryPacketTests(unittest.TestCase):
    def test_load_seed_specs_uses_static_seeds_by_default_and_historical_only_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root_dir = Path(temp_dir) / "repo"
            (root_dir / "mutable").mkdir(parents=True, exist_ok=True)
            (root_dir / "mutable" / "graph_lab.yaml").write_text(
                """
specs:
  - track: trend_signals
    family: perp_multi_asset_carry
    hypothesis: static carry seed
    neutrality_basis: none
    features:
      - funding_72h_mean
      - funding_carry_to_vol
    universe:
      basis_groups: [BTC, ETH, SOL, HYPE]
      max_symbols: 4
    risk: {}
    params:
      long_count: 2
      short_count: 2
      gross_target: 1.0
""".strip()
            )
            artifact_dir = Path(temp_dir) / "runs"
            (artifact_dir / "trend_signals").mkdir(parents=True, exist_ok=True)
            settings = SimpleNamespace(
                root_dir=root_dir,
                artifact_dir=artifact_dir,
                use_historical_seeds=False,
            )
            mutator = SpecMutator(settings, claude=SimpleNamespace())

            historical_spec = {
                "track": "trend_signals",
                "family": "perp_multi_asset_carry",
                "hypothesis": "historical carry winner",
                "neutrality_basis": "none",
                "features": ["funding_72h_mean", "funding_carry_to_vol"],
                "universe": {"basis_groups": ["BTC", "ETH", "SOL", "HYPE"], "max_symbols": 4},
                "risk": {},
                "params": {"long_count": 2, "short_count": 2, "gross_target": 1.0},
            }
            negative_spec = {
                "track": "trend_signals",
                "family": "perp_pair_trade_unlevered",
                "hypothesis": "negative pair run",
                "neutrality_basis": "none",
                "features": ["pair_residual_z_60"],
                "universe": {"basis_groups": ["ETH", "BTC"], "max_symbols": 2},
                "risk": {},
                "params": {"gross_target": 1.0},
            }
            (artifact_dir / "trend_signals" / "winner.json").write_text(
                json.dumps(
                    {
                        "spec": historical_spec,
                        "summary": {
                            "pre_audit_canonical_total_return": 0.22,
                            "validation_total_return": 0.04,
                            "median_total_return": 0.03,
                            "median_sharpe": 1.4,
                            "pre_audit_canonical_max_drawdown": -0.08,
                            "active_bar_fraction": 0.5,
                        },
                    }
                )
            )
            (artifact_dir / "trend_signals" / "loser.json").write_text(
                json.dumps(
                    {
                        "spec": negative_spec,
                        "summary": {
                            "pre_audit_canonical_total_return": -0.2,
                            "validation_total_return": 0.01,
                            "median_total_return": 0.01,
                            "median_sharpe": 0.6,
                            "pre_audit_canonical_max_drawdown": -0.25,
                            "active_bar_fraction": 0.6,
                        },
                    }
                )
            )

            seeds = mutator.load_seed_specs("trend_signals")

            self.assertEqual(seeds[0].family, "perp_multi_asset_carry")
            self.assertEqual(seeds[0].hypothesis, "static carry seed")

            historical_seeds = mutator.load_seed_specs(
                "trend_signals",
                include_historical=True,
            )

            self.assertEqual(historical_seeds[0].family, "perp_multi_asset_carry")
            self.assertEqual(historical_seeds[0].hypothesis, "historical carry winner")

    def test_row_quality_prefers_positive_pre_audit_strength(self) -> None:
        weak_row = {
            "aggregate_score": 5.0,
            "passed": False,
            "deployd": False,
            "summary": {
                "validation_total_return": 0.01,
                "median_total_return": 0.02,
                "pre_audit_canonical_total_return": -0.15,
                "pre_audit_canonical_max_drawdown": -0.2,
                "active_bar_fraction": 0.5,
            },
        }
        strong_row = {
            "aggregate_score": 4.5,
            "passed": False,
            "deployd": False,
            "summary": {
                "validation_total_return": 0.03,
                "median_total_return": 0.04,
                "pre_audit_canonical_total_return": 0.2,
                "pre_audit_canonical_max_drawdown": -0.08,
                "active_bar_fraction": 0.5,
            },
        }

        self.assertGreater(_row_quality(strong_row), _row_quality(weak_row))

    def test_propose_does_not_mix_deterministic_variants_into_llm_batches(self) -> None:
        mutator = object.__new__(SpecMutator)
        mutator.settings = SimpleNamespace(
            root_dir=REPO_ROOT
        )
        mutator.claude = SimpleNamespace(is_configured=True)
        mutator.last_llm_trace = None
        mutator.last_llm_log_path = None

        parent = SignalSpec.from_dict(
            {
                "track": "trend_signals",
                "family": "perp_pair_trade_unlevered",
                "hypothesis": "parent",
                "neutrality_basis": "none",
                "features": ["pair_realized_vol_168h"],
                "universe": {"basis_groups": ["ETH", "BTC"], "max_symbols": 2},
                "risk": {},
                "params": {},
            }
        )
        llm_spec = SignalSpec.from_dict(
            {
                "track": "trend_signals",
                "family": "perp_pair_trade_levered",
                "hypothesis": "llm child",
                "neutrality_basis": "none",
                "features": ["pair_corr_72h"],
                "universe": {"basis_groups": ["ETH", "BTC"], "max_symbols": 2},
                "risk": {},
                "params": {},
            }
        )
        deterministic_spec = SignalSpec.from_dict(
            {
                "track": "trend_signals",
                "family": "perp_pair_trade_unlevered",
                "hypothesis": "deterministic child",
                "neutrality_basis": "none",
                "features": ["pair_beta_stability_72h"],
                "universe": {"basis_groups": ["ETH", "BTC"], "max_symbols": 2},
                "risk": {},
                "params": {},
            }
        )

        mutator._llm_proposals = lambda **_kwargs: asyncio.sleep(0, result=[llm_spec])
        mutator._deterministic_variants = lambda *_args, **_kwargs: [deterministic_spec]
        mutator._validate_spec = lambda **kwargs: kwargs["spec"]

        proposals = asyncio.run(
            SpecMutator.propose(
                mutator,
                track="trend_signals",
                parent=parent,
                research_summary={},
                recent_results=[],
                memory_packet={},
                population_size=4,
                skip_llm=False,
                family=["perp_pair_trade_unlevered", "perp_pair_trade_levered"],
                exclude_hashes=set(),
                llm_tools=[],
            )
        )

        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0].hypothesis, "llm child")

    def test_compact_memory_packet_keeps_validation_leaders(self) -> None:
        mutator = object.__new__(SpecMutator)
        mutator.settings = SimpleNamespace()
        mutator.claude = SimpleNamespace()
        mutator.last_llm_trace = None

        compact = SpecMutator._compact_memory_packet(
            mutator,
            {
                "coverage_summary": {"experiments_total": 12, "passed_total": 0},
                "validation_leaders": [
                    {
                        "spec_hash": "abc123",
                        "family": "perp_pair_trade_unlevered",
                        "trade_style": "continuation",
                        "summary": {
                            "validation_total_return": -0.09,
                            "validation_sharpe": -3.9,
                        },
                    }
                ],
                "outstanding_runs": [
                    {
                        "spec_hash": "run123",
                        "family": "perp_pair_trade_levered",
                        "trade_style": "reversion",
                        "summary": {
                            "validation_total_return": -0.04,
                            "pre_audit_canonical_total_return": -0.08,
                        },
                    }
                ],
                "failure_pattern_summary": {
                    "diagnostic_tags": [{"tag": "overtrading", "count": 3}],
                },
                "behavior_pattern_summary": {
                    "median_trade_count": 4,
                    "median_flip_rate": 0.5,
                },
                "regime_pattern_summary": {
                    "pair_volatility": [{"label": "high_volatility", "count": 3}],
                },
                "drawdown_pattern_summary": {
                    "median_drawdown": -0.11,
                    "common_feature_contributors": [
                        {"feature": "pair_residual_z_60", "count": 2}
                    ],
                },
                "gate_pattern_summary": {
                    "bottleneck_tags": [{"tag": "weak_score_alignment", "count": 2}],
                },
                "equity_pattern_summary": {
                    "median_max_drawdown": -0.21,
                },
                "archetype_coverage": [
                    {"trade_style": "continuation", "attempted": 4, "passed": 1}
                ],
            },
        )

        self.assertEqual(len(compact["validation_leaders"]), 1)
        self.assertEqual(compact["validation_leaders"][0]["spec_hash"], "abc123")
        self.assertEqual(compact["validation_leaders"][0]["trade_style"], "continuation")
        self.assertEqual(compact["outstanding_runs"][0]["spec_hash"], "run123")
        self.assertEqual(compact["outstanding_runs"][0]["trade_style"], "reversion")
        self.assertEqual(compact["last_five_runs"], [])
        self.assertFalse(compact["novelty_pressure"])
        self.assertEqual(compact["failure_pattern_summary"]["diagnostic_tags"][0]["tag"], "overtrading")
        self.assertEqual(compact["behavior_pattern_summary"]["median_flip_rate"], 0.5)
        self.assertEqual(compact["regime_pattern_summary"]["pair_volatility"][0]["label"], "high_volatility")
        self.assertEqual(
            compact["drawdown_pattern_summary"]["common_feature_contributors"][0]["feature"],
            "pair_residual_z_60",
        )
        self.assertEqual(compact["gate_pattern_summary"]["bottleneck_tags"][0]["tag"], "weak_score_alignment")
        self.assertEqual(compact["equity_pattern_summary"]["median_max_drawdown"], -0.21)
        self.assertEqual(compact["archetype_coverage"][0]["trade_style"], "continuation")

    def test_compact_research_summary_keeps_pair_calibration(self) -> None:
        mutator = object.__new__(SpecMutator)
        compact = SpecMutator._compact_research_summary(
            mutator,
            {
                "track": "trend_signals",
                "pair_calibration": {
                    "pair": ["ETH", "BTC"],
                    "funding_spread_percentiles": {"p5": -0.0002, "p95": 0.0003},
                },
            },
        )

        self.assertEqual(compact["pair_calibration"]["pair"], ["ETH", "BTC"])
        self.assertIn("funding_spread_percentiles", compact["pair_calibration"])

    def test_pair_formula_examples_use_symmetric_asset_inputs(self) -> None:
        mutator = object.__new__(SpecMutator)
        examples = SpecMutator._formula_examples(
            mutator,
            "trend_signals",
            "perp_pair_trade_unlevered",
        )

        self.assertTrue(any("asset_1_price" in example for example in examples))
        self.assertTrue(any("asset_2_funding" in example for example in examples))
        self.assertTrue(any("asset_1_funding_carry_to_vol" in example for example in examples))

    def test_pair_feature_idea_examples_include_interpretable_patterns(self) -> None:
        mutator = object.__new__(SpecMutator)
        ideas = SpecMutator._feature_idea_examples(
            mutator,
            "trend_signals",
            "perp_pair_trade_unlevered",
        )

        labels = {row["idea"] for row in ideas}
        self.assertIn("Relative momentum spread", labels)
        self.assertIn("Funding divergence", labels)
        self.assertTrue(all("formula" in row and "why" in row for row in ideas))

    def test_basket_and_directional_examples_include_carry_overlays(self) -> None:
        mutator = object.__new__(SpecMutator)
        basket_examples = SpecMutator._formula_examples(
            mutator,
            "trend_signals",
            "perp_basket_neutral_unlevered",
        )
        directional_ideas = SpecMutator._feature_idea_examples(
            mutator,
            "trend_signals",
            "perp_multi_asset_decision",
        )

        self.assertTrue(any("funding_carry_to_vol" in example for example in basket_examples))
        self.assertTrue(any(row["idea"] == "Carry-stable momentum overlay" for row in directional_ideas))

    def test_write_llm_exchange_log_persists_payload_and_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            artifact_dir = Path(temp_dir) / "runs"
            mutator = object.__new__(SpecMutator)
            mutator.settings = SimpleNamespace(artifact_dir=artifact_dir)
            mutator.claude = SimpleNamespace(
                last_trace={"tool_rounds_used": 1},
                last_exchange={"final_content": "{\"specs\":[]}"},
            )
            parent = SignalSpec.from_dict(
                {
                    "track": "trend_signals",
                    "family": "perp_pair_trade_unlevered",
                    "hypothesis": "test",
                    "neutrality_basis": "none",
                    "features": ["pair_realized_vol_168h"],
                    "universe": {"basis_groups": ["ETH", "BTC"], "max_symbols": 2},
                    "risk": {},
                    "params": {},
                }
            )

            path = SpecMutator._write_llm_exchange_log(
                mutator,
                track="trend_signals",
                parent=parent,
                system_prompt="sys",
                user_payload={"task": "Return exactly 1 spec"},
                parsed_response={"specs": []},
                error=None,
                tool_names=["probe_feature_forward_stats"],
            )

            payload = path.read_text()
            self.assertIn("\"system_prompt\": \"sys\"", payload)
            self.assertIn("\"probe_feature_forward_stats\"", payload)
            self.assertIn("\"parsed_response\"", payload)

    def test_validate_spec_normalizes_pair_regime_gates(self) -> None:
        mutator = object.__new__(SpecMutator)
        mutator.settings = SimpleNamespace(
            root_dir=REPO_ROOT
        )
        mutator.claude = SimpleNamespace(is_configured=False)
        mutator.last_llm_trace = None
        mutator.last_llm_log_path = None

        track = "trend_signals"
        allowed_families = SpecMutator._allowed_families(mutator, track, family=None)
        allowed_features_by_family = SpecMutator._allowed_features_by_family(
            mutator, track, family=None
        )
        family_defaults = SpecMutator._family_defaults(mutator, track, family=None)

        spec = SignalSpec.from_dict(
            {
                "track": track,
                "family": "perp_pair_trade_unlevered",
                "hypothesis": "regime gate normalization",
                "neutrality_basis": "none",
                "features": ["pair_residual_z_60"],
                "universe": {"basis_groups": ["ETH", "BTC"], "max_symbols": 2},
                "risk": {},
                "regime_gates": {
                    "entry": [
                        {"expression": "pair_corr_72h", "min": 0.9},
                        "le(funding_spread_dispersion_72h,0.00002)",
                        {"expression": "unknown_gate", "min": 1.0},
                    ],
                    "exit_on_break": "false",
                },
                "params": {},
            }
        )

        fixed = SpecMutator._validate_spec(
            mutator,
            spec=spec,
            track=track,
            allowed_families=allowed_families,
            allowed_features_by_family=allowed_features_by_family,
            family_defaults=family_defaults,
        )

        self.assertEqual(len(fixed.regime_gates["entry"]), 2)
        by_expression = {
            gate["expression"]: gate
            for gate in fixed.regime_gates["entry"]
        }
        self.assertIn("pair_corr_72h", by_expression)
        self.assertEqual(by_expression["pair_corr_72h"]["min"], 0.9)
        self.assertIn(
            "le(funding_spread_dispersion_72h,0.00002)",
            by_expression,
        )
        self.assertFalse(fixed.regime_gates["exit_on_break"])

    def test_validate_spec_keeps_scientific_notation_regime_gate(self) -> None:
        mutator = object.__new__(SpecMutator)
        mutator.settings = SimpleNamespace(
            root_dir=REPO_ROOT
        )
        track = "trend_signals"
        spec = SignalSpec.from_dict(
            {
                "track": track,
                "family": "perp_pair_trade_unlevered",
                "hypothesis": "scientific gate",
                "neutrality_basis": "none",
                "features": ["pair_residual_z_60"],
                "universe": {"basis_groups": ["ETH", "BTC"], "max_symbols": 2},
                "risk": {},
                "regime_gates": {
                    "entry": [
                        "gt(funding_spread_dispersion_72h,5e-06)",
                    ],
                    "exit_on_break": True,
                },
                "params": {},
            }
        )

        fixed = SpecMutator._validate_spec(
            mutator,
            spec=spec,
            track=track,
            allowed_families=SpecMutator._allowed_families(mutator, track, family=None),
            allowed_features_by_family=SpecMutator._allowed_features_by_family(
                mutator,
                track,
                family=None,
            ),
            family_defaults=SpecMutator._family_defaults(mutator, track, family=None),
        )

        self.assertEqual(
            fixed.regime_gates["entry"][0]["expression"],
            "gt(funding_spread_dispersion_72h,5e-06)",
        )

    def test_validate_spec_keeps_regime_gates_for_basket_neutral_family(self) -> None:
        mutator = object.__new__(SpecMutator)
        mutator.settings = SimpleNamespace(
            root_dir=REPO_ROOT
        )
        mutator.claude = SimpleNamespace(is_configured=False)
        mutator.last_llm_trace = None
        mutator.last_llm_log_path = None

        track = "trend_signals"
        allowed_families = SpecMutator._allowed_families(mutator, track, family=None)
        allowed_features_by_family = SpecMutator._allowed_features_by_family(
            mutator, track, family=None
        )
        family_defaults = SpecMutator._family_defaults(mutator, track, family=None)

        spec = SignalSpec.from_dict(
            {
                "track": track,
                "family": "perp_basket_neutral_unlevered",
                "hypothesis": "basket neutral gate normalization",
                "neutrality_basis": "none",
                "features": ["price_return_24h", "funding_carry_to_vol"],
                "universe": {"basis_groups": ["ETH", "BTC", "SOL"], "max_symbols": 3},
                "risk": {},
                "regime_gates": {
                    "entry": [
                        {"expression": "ge(co_movement_72h,0.2)"},
                        {"expression": "funding_dispersion_72h", "max": 0.001},
                    ],
                    "exit_on_break": True,
                },
                "params": {"long_count": 2, "short_count": 2},
            }
        )

        fixed = SpecMutator._validate_spec(
            mutator,
            spec=spec,
            track=track,
            allowed_families=allowed_families,
            allowed_features_by_family=allowed_features_by_family,
            family_defaults=family_defaults,
        )

        self.assertEqual(len(fixed.regime_gates["entry"]), 2)
        self.assertEqual(fixed.params["long_count"], 2)
        self.assertEqual(fixed.params["short_count"], 2)

    def test_validate_spec_does_not_truncate_five_pair_regime_gates(self) -> None:
        mutator = object.__new__(SpecMutator)
        mutator.settings = SimpleNamespace(
            root_dir=REPO_ROOT
        )
        spec = SignalSpec.from_dict(
            {
                "track": "trend_signals",
                "family": "perp_pair_trade_unlevered",
                "hypothesis": "keep all gates",
                "neutrality_basis": "none",
                "features": ["pair_residual_z_60"],
                "universe": {"basis_groups": ["ETH", "BTC"], "max_symbols": 2},
                "risk": {},
                "regime_gates": {
                    "entry": [
                        {"expression": "ge(pair_beta_stability_72h,0.1)"},
                        {"expression": "ge(pair_corr_72h,0.75)"},
                        {"expression": "le(pair_corr_72h,0.92)"},
                        {"expression": "ge(pair_spread_vol_72h,0.003)"},
                        {"expression": "ge(funding_spread_dispersion_72h,4.0e-06)"},
                    ],
                    "exit_on_break": True,
                },
                "params": {},
            }
        )

        fixed = SpecMutator._validate_spec(
            mutator,
            spec=spec,
            track="trend_signals",
            allowed_families=SpecMutator._allowed_families(mutator, "trend_signals", family=None),
            allowed_features_by_family=SpecMutator._allowed_features_by_family(
                mutator,
                "trend_signals",
                family=None,
            ),
            family_defaults=SpecMutator._family_defaults(
                mutator,
                "trend_signals",
                family=None,
            ),
        )

        expressions = [gate["expression"] for gate in fixed.regime_gates["entry"]]
        self.assertEqual(len(expressions), 5)
        self.assertIn("ge(funding_spread_dispersion_72h,4.0e-06)", expressions)

    def test_validate_spec_normalizes_multi_asset_carry_family(self) -> None:
        mutator = object.__new__(SpecMutator)
        mutator.settings = SimpleNamespace(
            root_dir=REPO_ROOT
        )
        mutator.claude = SimpleNamespace(is_configured=False)
        mutator.last_llm_trace = None
        mutator.last_llm_log_path = None

        track = "trend_signals"
        spec = SignalSpec.from_dict(
            {
                "track": track,
                "family": "perp_multi_asset_carry",
                "hypothesis": "carry normalization",
                "neutrality_basis": "none",
                "features": ["funding_72h_mean", "funding_carry_to_vol"],
                "universe": {"basis_groups": ["ETH", "BTC", "SOL"], "max_symbols": 3},
                "risk": {},
                "params": {"gross_target": 5.0, "long_count": 4, "short_count": 4},
            }
        )

        fixed = SpecMutator._validate_spec(
            mutator,
            spec=spec,
            track=track,
            allowed_families=SpecMutator._allowed_families(mutator, track, family=None),
            allowed_features_by_family=SpecMutator._allowed_features_by_family(
                mutator,
                track,
                family=None,
            ),
            family_defaults=SpecMutator._family_defaults(mutator, track, family=None),
        )

        self.assertLessEqual(fixed.params["gross_target"], 2.5)
        self.assertEqual(fixed.params["long_count"], 3)
        self.assertEqual(fixed.params["short_count"], 3)

    def test_validate_spec_rejects_single_asset_alias_for_pair_family(self) -> None:
        mutator = object.__new__(SpecMutator)
        mutator.settings = SimpleNamespace(
            root_dir=REPO_ROOT
        )
        spec = SignalSpec.from_dict(
            {
                "track": "trend_signals",
                "family": "perp_pair_trade_unlevered",
                "hypothesis": "bad pair alias",
                "neutrality_basis": "none",
                "features": ["funding_carry_to_vol"],
                "universe": {"basis_groups": ["ETH", "BTC"], "max_symbols": 2},
                "risk": {},
                "params": {},
            }
        )

        validated = SpecMutator._validate_spec(
            mutator,
            spec=spec,
            track="trend_signals",
            allowed_families=["perp_pair_trade_unlevered", "perp_pair_trade_levered"],
            allowed_features_by_family=SpecMutator._allowed_features_by_family(
                mutator,
                "trend_signals",
                family=["perp_pair_trade_unlevered", "perp_pair_trade_levered"],
            ),
            family_defaults=SpecMutator._family_defaults(
                mutator,
                "trend_signals",
                family=["perp_pair_trade_unlevered", "perp_pair_trade_levered"],
            ),
        )

        self.assertNotIn("funding_carry_to_vol", validated.features)
        self.assertTrue(validated.features)

    def test_validate_spec_keeps_pair_safe_one_sided_carry_alias(self) -> None:
        mutator = object.__new__(SpecMutator)
        mutator.settings = SimpleNamespace(
            root_dir=REPO_ROOT
        )
        spec = SignalSpec.from_dict(
            {
                "track": "trend_signals",
                "family": "perp_pair_trade_unlevered",
                "hypothesis": "one sided carry",
                "neutrality_basis": "none",
                "features": ["asset_1_funding_carry_to_vol"],
                "universe": {"basis_groups": ["ETH", "BTC"], "max_symbols": 2},
                "risk": {},
                "params": {},
            }
        )

        validated = SpecMutator._validate_spec(
            mutator,
            spec=spec,
            track="trend_signals",
            allowed_families=["perp_pair_trade_unlevered", "perp_pair_trade_levered"],
            allowed_features_by_family=SpecMutator._allowed_features_by_family(
                mutator,
                "trend_signals",
                family=["perp_pair_trade_unlevered", "perp_pair_trade_levered"],
            ),
            family_defaults=SpecMutator._family_defaults(
                mutator,
                "trend_signals",
                family=["perp_pair_trade_unlevered", "perp_pair_trade_levered"],
            ),
        )

        self.assertIn("asset_1_funding_carry_to_vol", validated.features)

    def test_validate_spec_normalizes_trade_style_for_pair_family(self) -> None:
        mutator = object.__new__(SpecMutator)
        mutator.settings = SimpleNamespace(
            root_dir=REPO_ROOT
        )
        spec = SignalSpec.from_dict(
            {
                "track": "trend_signals",
                "family": "perp_pair_trade_unlevered",
                "hypothesis": "bad style",
                "neutrality_basis": "none",
                "features": ["pair_realized_vol_168h"],
                "universe": {"basis_groups": ["ETH", "BTC"], "max_symbols": 2},
                "risk": {},
                "params": {"trade_style": "random_walk"},
            }
        )

        validated = SpecMutator._validate_spec(
            mutator,
            spec=spec,
            track="trend_signals",
            allowed_families=["perp_pair_trade_unlevered", "perp_pair_trade_levered"],
            allowed_features_by_family=SpecMutator._allowed_features_by_family(
                mutator,
                "trend_signals",
                family=["perp_pair_trade_unlevered", "perp_pair_trade_levered"],
            ),
            family_defaults=SpecMutator._family_defaults(
                mutator,
                "trend_signals",
                family=["perp_pair_trade_unlevered", "perp_pair_trade_levered"],
            ),
        )

        self.assertEqual(validated.params["trade_style"], "hybrid")

    def test_extract_llm_spec_rows_accepts_top_level_single_spec(self) -> None:
        mutator = object.__new__(SpecMutator)

        rows = SpecMutator._extract_llm_spec_rows(
            mutator,
            {
                "track": "trend_signals",
                "family": "perp_pair_trade_unlevered",
                "hypothesis": "top level spec",
                "neutrality_basis": "none",
                "features": ["pair_corr_72h"],
                "universe": {"basis_groups": ["ETH", "BTC"], "max_symbols": 2},
                "risk": {},
                "regime_gates": {},
                "params": {"trade_style": "continuation"},
            },
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["hypothesis"], "top level spec")

    def test_extract_llm_spec_rows_accepts_nested_single_spec(self) -> None:
        mutator = object.__new__(SpecMutator)

        rows = SpecMutator._extract_llm_spec_rows(
            mutator,
            {
                "spec": {
                    "track": "trend_signals",
                    "family": "perp_pair_trade_unlevered",
                    "hypothesis": "nested spec",
                    "neutrality_basis": "none",
                    "features": ["pair_corr_72h"],
                    "universe": {"basis_groups": ["ETH", "BTC"], "max_symbols": 2},
                    "risk": {},
                    "params": {"trade_style": "continuation"},
                }
            },
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["hypothesis"], "nested spec")

    def test_pair_deterministic_variants_include_cross_family_and_novelty_seeds(self) -> None:
        mutator = object.__new__(SpecMutator)
        mutator.settings = SimpleNamespace(
            root_dir=REPO_ROOT
        )
        parent = SignalSpec.from_dict(
            {
                "track": "trend_signals",
                "family": "perp_pair_trade_unlevered",
                "hypothesis": "parent",
                "neutrality_basis": "none",
                "features": ["pair_residual_z_60"],
                "universe": {"basis_groups": ["ETH", "BTC"], "max_symbols": 2},
                "risk": {},
                "params": {"min_abs_score": 0.2},
            }
        )

        variants = SpecMutator._deterministic_variants(
            mutator,
            "trend_signals",
            parent,
            family=["perp_pair_trade_unlevered", "perp_multi_asset_decision"],
        )

        self.assertTrue(any(spec.family == "perp_multi_asset_decision" for spec in variants[:4]))
        self.assertTrue(
            any(spec.universe.basis_groups != ["ETH", "BTC"] for spec in variants[:6] if spec.family == "perp_pair_trade_unlevered")
        )
        self.assertTrue(
            any("neg(pair_kalman_residual_z_72h)" in spec.features for spec in variants)
        )

    def test_cross_sectional_deterministic_variants_rotate_alternate_baskets(self) -> None:
        mutator = object.__new__(SpecMutator)
        mutator.settings = SimpleNamespace(
            root_dir=REPO_ROOT
        )
        parent = SignalSpec.from_dict(
            {
                "track": "trend_signals",
                "family": "perp_multi_asset_carry",
                "hypothesis": "parent carry",
                "neutrality_basis": "none",
                "features": ["funding_72h_mean", "funding_carry_to_vol"],
                "universe": {"basis_groups": ["BTC", "ETH", "SOL", "HYPE"], "max_symbols": 4},
                "risk": {},
                "params": {"long_count": 2, "short_count": 2, "min_abs_score": 0.12},
            }
        )

        variants = SpecMutator._deterministic_variants(
            mutator,
            "trend_signals",
            parent,
            family=["perp_multi_asset_carry", "perp_basket_neutral_unlevered"],
        )

        seen_baskets = {
            tuple(spec.universe.basis_groups)
            for spec in variants[:6]
            if spec.family == "perp_multi_asset_carry"
        }
        self.assertGreaterEqual(len(seen_baskets), 2)


if __name__ == "__main__":
    unittest.main()


