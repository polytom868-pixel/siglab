from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace

from siglab.cli import (
    _agent_safe_memory_packet,
    _agent_safe_recent_results,
    _external_research_from_llm_trace,
    _resolve_resume_run,
    _require_sosovalue_config,
    _strip_audit_fields,
    _tool_only_external_research,
    _write_run_reflection,
)
from siglab.settings import SiglabConfig
from siglab.models import SignalSpec
from siglab.search.ancestry import LineageStore


class CliAgentSafetyTests(unittest.TestCase):
    def test_resolve_resume_run_reads_workspace_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifact_dir = Path(tmp) / "runs"
            workspace_root = artifact_dir / "trend_signals" / "workspaces" / "session-123"
            (workspace_root / "meta").mkdir(parents=True, exist_ok=True)
            (workspace_root / "current").mkdir(parents=True, exist_ok=True)
            (workspace_root / "iterations" / "0003_parenthash").mkdir(parents=True, exist_ok=True)
            (workspace_root / "meta" / "session.json").write_text(
                """
{
  "track": "trend_signals",
  "run_session_id": "session-123",
  "families": ["perp_multi_asset_carry", "perp_multi_asset_decision"],
  "memory_scope": "session_local",
  "custom_symbols": ["XPL", "ENA", "XRP", "BTC"],
  "use_historical_seeds": true
}
""".strip()
            )
            (workspace_root / "current" / "SESSION_STATE.json").write_text(
                """
{
  "run_session_id": "session-123",
  "memory_scope": "session_local",
  "custom_symbols": ["XPL", "ENA", "XRP", "BTC"],
  "use_historical_seeds": true,
  "iteration_number": 3
}
""".strip()
            )

            info = _resolve_resume_run(
                settings=SimpleNamespace(artifact_dir=artifact_dir),
                run_session_id="session-123",
            )

            self.assertEqual(info["track"], "trend_signals")
            self.assertEqual(info["families"], ["perp_multi_asset_carry", "perp_multi_asset_decision"])
            self.assertEqual(info["memory_scope"], "session_local")
            self.assertEqual(info["custom_symbols"], ["XPL", "ENA", "XRP", "BTC"])
            self.assertTrue(info["use_historical_seeds"])
            self.assertEqual(info["next_iteration"], 4)

    def test_require_sosovalue_config_points_to_example_config(self) -> None:
        settings = SiglabConfig(
            root_dir=Path("/tmp"),
            sosovalue_config_path=Path("/tmp/missing-config.json"),
            generated_strategy_dir=Path("/tmp/deployed_agents"),
            data_lake_dir=Path("/tmp/lake"),
            artifact_dir=Path("/tmp/runs"),
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
            population_size=1,
        )

        with self.assertRaises(SystemExit) as ctx:
            _require_sosovalue_config(settings)

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
                "spec_hash": "abc",
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
                    "spec_hash": "winner",
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
            artifact_dir = Path(tmp) / "runs"
            ancestry = LineageStore(Path(tmp) / "ancestry.db")
            deterministic = SignalSpec.from_dict(
                {
                    "track": "trend_signals",
                    "family": "perp_pair_trade_unlevered",
                    "hypothesis": "deterministic",
                    "neutrality_basis": "none",
                    "features": ["pair_residual_z_60"],
                    "universe": {"basis_groups": ["ETH", "BTC"], "max_symbols": 2},
                    "risk": {},
                    "params": {},
                }
            )
            llm_child = SignalSpec.from_dict(
                {
                    "track": "trend_signals",
                    "family": "perp_pair_trade_unlevered",
                    "hypothesis": "llm child",
                    "neutrality_basis": "none",
                    "features": ["pair_corr_72h"],
                    "universe": {"basis_groups": ["ETH", "BTC"], "max_symbols": 2},
                    "risk": {},
                    "params": {},
                }
            )

            ancestry.record(
                evaluation={
                    "spec": deterministic.canonical_dict(),
                    "spec_hash": deterministic.strategy_hash(),
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
                    "track": "trend_signals",
                    "run_context": {
                        "run_session_id": "session-1",
                        "deterministic": True,
                        "phase_label": "burn_in",
                    },
                },
                artifact_path=None,
            )
            ancestry.record(
                evaluation={
                    "spec": llm_child.canonical_dict(),
                    "spec_hash": llm_child.strategy_hash(),
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
                    "track": "trend_signals",
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
                ancestry=ancestry,
                track="trend_signals",
                phase_label="main",
                family_scope=["perp_pair_trade_unlevered"],
                run_session_id="session-1",
            )

            self.assertIsNotNone(path)
            self.assertIsNotNone(reflection)
            assert reflection is not None
            self.assertEqual(reflection["summary"]["llm_run_count"], 1)
            self.assertEqual(len(reflection["last_five_runs"]), 1)
            self.assertEqual(reflection["last_five_runs"][0]["spec_hash"], llm_child.strategy_hash())
            self.assertTrue(reflection["last_five_runs"][0]["sweep_drift"]["material_change"])
            self.assertEqual(
                reflection["intent_vs_sweep"]["median_changed_param_count"],
                2.0,
            )
            self.assertNotIn("audit_total_return", reflection["last_five_runs"][0])
            self.assertNotIn("audit_total_return", reflection["summary"])


if __name__ == "__main__":
    unittest.main()



