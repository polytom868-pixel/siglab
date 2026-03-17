from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace

from wayfinder_autolab.cli import (
    _agent_safe_memory_packet,
    _agent_safe_recent_results,
    _external_research_from_llm_trace,
    _require_wayfinder_config,
    _strip_audit_fields,
    _tool_only_external_research,
    _write_run_reflection,
)
from wayfinder_autolab.settings import AutolabSettings
from wayfinder_autolab.models import CandidateGraph
from wayfinder_autolab.search.lineage import LineageStore


class CliAgentSafetyTests(unittest.TestCase):
    def test_require_wayfinder_config_points_to_example_config(self) -> None:
        settings = AutolabSettings(
            root_dir=Path("/tmp"),
            wayfinder_config_path=Path("/tmp/missing-config.json"),
            generated_strategy_dir=Path("/tmp/generated_strategies"),
            data_lake_dir=Path("/tmp/lake"),
            artifact_dir=Path("/tmp/artifacts"),
            live_dir=Path("/tmp/live"),
            lineage_db_path=Path("/tmp/autolab_test.db"),
            wayfinder_api_key_override=None,
            kimi_api_key=None,
            kimi_model="kimi-k2.5",
            kimi_base_url="https://api.moonshot.ai/v1",
            kimi_max_tokens=1024,
            kimi_temperature=1.0,
            kimi_top_p=0.95,
            kimi_timeout_s=30.0,
            population_size=1,
        )

        with self.assertRaises(SystemExit) as ctx:
            _require_wayfinder_config(settings)

        self.assertIn("config.example.json", str(ctx.exception))

    def test_strip_audit_fields_removes_audit_keys_recursively(self) -> None:
        payload = {
            "audit_total_return": 0.12,
            "validation_total_return": 0.08,
            "nested": {
                "audit_sharpe": 1.1,
                "holdout_total_return": 0.07,
            },
            "items": [
                {"audit_available": True, "validation_available": True},
                {"audit_cagr": 0.2, "holdout_sharpe": 0.9},
            ],
        }

        cleaned = _strip_audit_fields(payload)

        self.assertNotIn("audit_total_return", cleaned)
        self.assertEqual(cleaned["validation_total_return"], 0.08)
        self.assertNotIn("audit_sharpe", cleaned["nested"])
        self.assertEqual(cleaned["nested"]["holdout_total_return"], 0.07)
        self.assertNotIn("audit_available", cleaned["items"][0])
        self.assertTrue(cleaned["items"][0]["validation_available"])
        self.assertNotIn("audit_cagr", cleaned["items"][1])
        self.assertEqual(cleaned["items"][1]["holdout_sharpe"], 0.9)

    def test_agent_safe_recent_results_and_memory_packet_preserve_non_audit_fields(self) -> None:
        recent_results = [
            {
                "candidate_hash": "abc",
                "summary": {
                    "aggregate_score": 1.0,
                    "validation_total_return": 0.05,
                    "holdout_total_return": 0.05,
                    "audit_total_return": 0.15,
                    "gate_reasons": [],
                },
            }
        ]
        memory_packet = {
            "nearest_winners": [
                {
                    "candidate_hash": "winner",
                    "summary": {
                        "median_total_return": 0.03,
                        "holdout_total_return": 0.02,
                        "audit_total_return": 0.09,
                    },
                }
            ]
        }

        cleaned_results = _agent_safe_recent_results(recent_results)
        cleaned_packet = _agent_safe_memory_packet(memory_packet)

        self.assertEqual(cleaned_results[0]["summary"]["aggregate_score"], 1.0)
        self.assertEqual(cleaned_results[0]["summary"]["validation_total_return"], 0.05)
        self.assertEqual(cleaned_results[0]["summary"]["holdout_total_return"], 0.05)
        self.assertNotIn("audit_total_return", cleaned_results[0]["summary"])
        winner_summary = cleaned_packet["nearest_winners"][0]["summary"]
        self.assertEqual(winner_summary["holdout_total_return"], 0.02)
        self.assertNotIn("audit_total_return", winner_summary)

    def test_external_research_stays_empty_without_tavily_tool_calls(self) -> None:
        web_researcher = SimpleNamespace(is_configured=True)

        payload = _tool_only_external_research(web_researcher=web_researcher)
        self.assertEqual(payload["provider"], "tool_only")
        self.assertEqual(payload["reports"], [])

        from_trace = _external_research_from_llm_trace(
            llm_trace={
                "trace": {
                    "tool_calls": [
                        {
                            "name": "probe_feature_forward_stats",
                            "result": {"ok": True},
                        }
                    ]
                }
            },
            web_researcher=web_researcher,
        )
        self.assertEqual(from_trace["provider"], "tool_only")
        self.assertEqual(from_trace["reports"], [])

    def test_external_research_extracts_only_tavily_tool_calls(self) -> None:
        web_researcher = SimpleNamespace(is_configured=True)
        payload = _external_research_from_llm_trace(
            llm_trace={
                "trace": {
                    "tool_calls": [
                        {
                            "name": "tavily_search",
                            "result": {
                                "ok": True,
                                "query": "eth btc pair trading funding momentum",
                                "answer": "Funding works better as a filter.",
                                "insights": ["Prefer carry as confirmation."],
                                "sources": [{"url": "https://example.com"}],
                            },
                        },
                        {
                            "name": "web_fetch",
                            "result": {
                                "ok": True,
                                "url": "https://example.com",
                            },
                        },
                    ]
                }
            },
            web_researcher=web_researcher,
        )

        self.assertEqual(payload["provider"], "tavily_tool_calls")
        self.assertEqual(payload["queries"], ["eth btc pair trading funding momentum"])
        self.assertEqual(payload["reports"][0]["answer"], "Funding works better as a filter.")

    def test_run_reflection_excludes_deterministic_rows_and_audit_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifact_dir = Path(tmp) / "artifacts"
            lineage = LineageStore(Path(tmp) / "lineage.db")
            deterministic = CandidateGraph.from_dict(
                {
                    "track": "directional_perps",
                    "family": "perp_pair_trade_unlevered",
                    "hypothesis": "deterministic",
                    "neutrality_basis": "none",
                    "features": ["pair_residual_z_60"],
                    "universe": {"basis_groups": ["ETH", "BTC"], "max_symbols": 2},
                    "risk": {},
                    "params": {},
                }
            )
            llm_child = CandidateGraph.from_dict(
                {
                    "track": "directional_perps",
                    "family": "perp_pair_trade_unlevered",
                    "hypothesis": "llm child",
                    "neutrality_basis": "none",
                    "features": ["pair_corr_72h"],
                    "universe": {"basis_groups": ["ETH", "BTC"], "max_symbols": 2},
                    "risk": {},
                    "params": {},
                }
            )

            lineage.record(
                evaluation={
                    "candidate": deterministic.canonical_dict(),
                    "candidate_hash": deterministic.strategy_hash(),
                    "summary": {
                        "aggregate_score": 0.1,
                        "median_total_return": 0.0,
                        "pre_audit_canonical_total_return": 0.0,
                        "audit_total_return": 0.1,
                        "passed": False,
                        "gate_reasons": [],
                    },
                },
                parent_hash=None,
                research_summary={
                    "track": "directional_perps",
                    "run_context": {
                        "run_session_id": "session-1",
                        "deterministic": True,
                        "phase_label": "burn_in",
                    },
                },
                artifact_path=None,
            )
            lineage.record(
                evaluation={
                    "candidate": llm_child.canonical_dict(),
                    "candidate_hash": llm_child.strategy_hash(),
                    "summary": {
                        "aggregate_score": 0.6,
                        "median_total_return": 0.02,
                        "validation_total_return": 0.03,
                        "pre_audit_canonical_total_return": 0.01,
                        "active_bar_fraction": 0.05,
                        "gate_bottleneck_tags": ["restrictive_regime_gate"],
                        "policy_sweep_material_change": True,
                        "policy_sweep_changed_keys": ["entry_abs_score", "cooldown_bars"],
                        "policy_sweep_activity_penalty": 0.05,
                        "policy_sweep_proposed_policy": {
                            "entry_abs_score": 0.2,
                            "cooldown_bars": 0,
                        },
                        "policy_sweep_frozen_policy": {
                            "entry_abs_score": 0.15,
                            "cooldown_bars": 4,
                        },
                        "audit_total_return": -0.2,
                        "passed": False,
                        "gate_reasons": [],
                    },
                },
                parent_hash=deterministic.strategy_hash(),
                research_summary={
                    "track": "directional_perps",
                    "run_context": {
                        "run_session_id": "session-1",
                        "deterministic": False,
                        "phase_label": "main",
                    },
                },
                artifact_path=None,
            )

            path, reflection = _write_run_reflection(
                settings=SimpleNamespace(artifact_dir=artifact_dir),
                lineage=lineage,
                track="directional_perps",
                phase_label="main",
                family_scope=["perp_pair_trade_unlevered"],
                run_session_id="session-1",
            )

            self.assertIsNotNone(path)
            self.assertIsNotNone(reflection)
            assert reflection is not None
            self.assertEqual(reflection["summary"]["llm_run_count"], 1)
            self.assertEqual(len(reflection["last_five_runs"]), 1)
            self.assertEqual(reflection["last_five_runs"][0]["candidate_hash"], llm_child.strategy_hash())
            self.assertTrue(reflection["last_five_runs"][0]["sweep_drift"]["material_change"])
            self.assertEqual(
                reflection["intent_vs_sweep"]["median_changed_param_count"],
                2.0,
            )
            self.assertNotIn("audit_total_return", reflection["last_five_runs"][0])
            self.assertNotIn("audit_total_return", reflection["summary"])


if __name__ == "__main__":
    unittest.main()
