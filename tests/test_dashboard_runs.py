from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from wayfinder_autolab.dashboard.server import DashboardApp
from wayfinder_autolab.models import CandidateGraph
from wayfinder_autolab.search.lineage import LineageStore


def _candidate(*, family: str, hypothesis: str, features: list[str]) -> CandidateGraph:
    return CandidateGraph.from_dict(
        {
            "track": "directional_perps",
            "family": family,
            "hypothesis": hypothesis,
            "neutrality_basis": "market",
            "features": list(features),
            "universe": {
                "basis_groups": ["BTC", "ETH", "SOL", "HYPE"] if "pair" not in family else ["ETH", "BTC"],
                "max_symbols": 4 if "pair" not in family else 2,
                "lookback_days": 365,
                "interval": "1h",
            },
            "risk": {"max_leverage": 1.0, "rebalance_threshold": 0.03},
            "regime_gates": {},
            "params": {"gross_target": 1.0},
        }
    )


class DashboardRunSummaryTests(unittest.TestCase):
    def test_dashboard_payload_includes_grouped_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lineage = LineageStore(root / "lineage.db")
            static_dir = Path(__file__).resolve().parents[1] / "wayfinder_autolab" / "dashboard" / "static"

            benchmark_candidate = _candidate(
                family="perp_multi_asset_carry",
                hypothesis="benchmark keep",
                features=["funding_72h_mean", "funding_carry_to_vol"],
            )
            harness_candidate = _candidate(
                family="perp_basket_neutral_levered",
                hypothesis="harness pass",
                features=["price_return_24h", "relative_carry_z_72h"],
            )

            benchmark_artifact = root / "benchmark.json"
            benchmark_artifact.write_text(
                json.dumps(
                    {
                        "candidate_hash": benchmark_candidate.strategy_hash(),
                        "candidate": benchmark_candidate.canonical_dict(),
                        "summary": {
                            "aggregate_score": 4.5,
                            "median_total_return": 0.03,
                            "validation_total_return": 0.02,
                            "pre_audit_canonical_total_return": 0.01,
                            "passed": True,
                        },
                    }
                )
            )
            harness_artifact = root / "harness.json"
            harness_artifact.write_text(
                json.dumps(
                    {
                        "candidate_hash": harness_candidate.strategy_hash(),
                        "candidate": harness_candidate.canonical_dict(),
                        "summary": {
                            "aggregate_score": 1.5,
                            "median_total_return": 0.01,
                            "validation_total_return": 0.005,
                            "pre_audit_canonical_total_return": 0.002,
                            "passed": False,
                        },
                    }
                )
            )

            lineage.record(
                evaluation={
                    "candidate_hash": benchmark_candidate.strategy_hash(),
                    "candidate": benchmark_candidate.canonical_dict(),
                    "summary": {
                        "aggregate_score": 4.5,
                        "median_total_return": 0.03,
                        "validation_total_return": 0.02,
                        "pre_audit_canonical_total_return": 0.01,
                        "passed": True,
                    },
                },
                parent_hash=None,
                research_summary={
                    "run_context": {
                        "run_session_id": "benchmark::deck::claude_code::1",
                        "agent_label": "claude_code",
                        "run_label": "claude-benchmark-1",
                        "benchmark_mode": True,
                        "benchmark_deck": "directional_perps_external",
                        "phase_label": "benchmark",
                    },
                    "llm_tool_trace": {
                        "trace": {
                            "tool_calls": [
                                {"name": "search_features"},
                                {"name": "inspect_feature"},
                            ]
                        }
                    },
                },
                artifact_path=str(benchmark_artifact),
            )
            lineage.record(
                evaluation={
                    "candidate_hash": harness_candidate.strategy_hash(),
                    "candidate": harness_candidate.canonical_dict(),
                    "summary": {
                        "aggregate_score": 1.5,
                        "median_total_return": 0.01,
                        "validation_total_return": 0.005,
                        "pre_audit_canonical_total_return": 0.002,
                        "passed": False,
                    },
                },
                parent_hash=None,
                research_summary={
                    "run_context": {
                        "run_session_id": "20260315T180000Z",
                        "agent_label": "autolab_harness",
                        "run_label": "harness-1",
                        "benchmark_mode": False,
                        "phase_label": "main",
                        "deterministic": False,
                    }
                },
                artifact_path=str(harness_artifact),
            )

            app = DashboardApp(
                settings=SimpleNamespace(root_dir=root),
                lineage=lineage,
                static_dir=static_dir,
            )
            payload = app.experiments_payload(track="directional_perps", family=None)
            runs_payload = app.runs_payload(track="directional_perps", family=None)

            self.assertEqual(payload["summary"]["run_count"], 2)
            self.assertEqual(payload["summary"]["benchmark_run_count"], 1)
            self.assertEqual(payload["summary"]["harness_run_count"], 1)
            self.assertEqual(len(payload["runs"]), 2)
            self.assertEqual(runs_payload["summary"]["run_count"], 2)
            self.assertEqual(runs_payload["summary"]["experiment_count"], 2)
            self.assertIn("perp_multi_asset_carry", runs_payload["summary"]["families"])

            benchmark_run = next(row for row in payload["runs"] if row["benchmark_mode"])
            self.assertEqual(benchmark_run["agent_label"], "claude_code")
            self.assertEqual(benchmark_run["run_label"], "claude-benchmark-1")
            self.assertEqual(benchmark_run["tool_call_count"], 2)
            self.assertEqual(benchmark_run["status"], "pass")
            self.assertEqual(benchmark_run["best_candidate_hash"], benchmark_candidate.strategy_hash())
            self.assertEqual(len(benchmark_run["series_points"]), 1)
            self.assertEqual(
                benchmark_run["series_points"][0]["candidate_hash"],
                benchmark_candidate.strategy_hash(),
            )

            harness_run = next(row for row in payload["runs"] if not row["benchmark_mode"])
            self.assertEqual(harness_run["agent_label"], "autolab_harness")
            self.assertEqual(harness_run["run_label"], "harness-1")
            self.assertEqual(harness_run["status"], "fail")

            experiment = next(
                row for row in payload["experiments"] if row["candidate_hash"] == benchmark_candidate.strategy_hash()
            )
            self.assertEqual(experiment["run_session_id"], "benchmark::deck::claude_code::1")
            self.assertEqual(experiment["agent_label"], "claude_code")
            self.assertEqual(experiment["run_kind"], "benchmark")
            self.assertEqual(experiment["artifact_path"], "benchmark.json")

    def test_dashboard_reads_tool_traces_from_workspace_trace_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lineage = LineageStore(root / "lineage.db")
            static_dir = Path(__file__).resolve().parents[1] / "wayfinder_autolab" / "dashboard" / "static"

            candidate = _candidate(
                family="perp_multi_asset_carry",
                hypothesis="workspace trace",
                features=["funding_72h_mean", "funding_carry_to_vol"],
            )
            artifact = root / "trace-artifact.json"
            artifact.write_text(
                json.dumps(
                    {
                        "candidate_hash": candidate.strategy_hash(),
                        "candidate": candidate.canonical_dict(),
                        "summary": {
                            "aggregate_score": 2.5,
                            "median_total_return": 0.02,
                            "validation_total_return": 0.01,
                            "pre_audit_canonical_total_return": 0.01,
                            "passed": True,
                        },
                    }
                )
            )
            planner_trace = root / "planner_trace.json"
            planner_trace.write_text(
                json.dumps(
                    {
                        "stage": "planner",
                        "kimi_trace": {
                            "model": "kimi-k2.5",
                            "thinking_mode": "disabled",
                            "tool_rounds_used": 1,
                            "tool_count_available": 3,
                            "response_finish_reason": "stop",
                            "final_content_preview": "preview",
                            "tool_calls": [
                                {
                                    "id": "search:1",
                                    "name": "search_features",
                                    "arguments": "{\"query\":\"carry gate\"}",
                                    "result": {"ok": True},
                                }
                            ],
                        },
                    }
                )
            )

            lineage.record(
                evaluation={
                    "candidate_hash": candidate.strategy_hash(),
                    "candidate": candidate.canonical_dict(),
                    "summary": {
                        "aggregate_score": 2.5,
                        "median_total_return": 0.02,
                        "validation_total_return": 0.01,
                        "pre_audit_canonical_total_return": 0.01,
                        "passed": True,
                    },
                },
                parent_hash=None,
                research_summary={
                    "run_context": {
                        "run_session_id": "20260316T220000Z",
                        "agent_label": "autolab_harness",
                        "run_label": "trace-run",
                        "benchmark_mode": False,
                        "phase_label": "main",
                        "deterministic": False,
                    },
                    "workspace": {
                        "planner_trace_path": str(planner_trace),
                    },
                },
                artifact_path=str(artifact),
            )

            app = DashboardApp(
                settings=SimpleNamespace(root_dir=root),
                lineage=lineage,
                static_dir=static_dir,
            )
            payload = app.experiments_payload(track="directional_perps", family=None)
            runs_payload = app.runs_payload(track="directional_perps", family=None)

            experiment = payload["experiments"][0]
            self.assertEqual(experiment["tool_call_count"], 1)
            self.assertEqual(experiment["tool_trace"]["tool_rounds_used"], 1)
            self.assertEqual(experiment["tool_trace"]["tool_calls"][0]["name"], "search_features")
            self.assertEqual(len(experiment["tool_trace_stages"]), 1)
            self.assertEqual(experiment["tool_trace_stages"][0]["stage"], "planner")

            run = runs_payload["runs"][0]
            self.assertEqual(run["tool_call_count"], 1)
            self.assertEqual(run["llm_provider"], "kimi")
            self.assertEqual(run["llm_model"], "kimi-k2.5")

    def test_dashboard_includes_active_workspace_run_without_lineage_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lineage = LineageStore(root / "lineage.db")
            static_dir = Path(__file__).resolve().parents[1] / "wayfinder_autolab" / "dashboard" / "static"

            state_dir = root / "artifacts" / "directional_perps" / "workspaces" / "20260317T031125Z" / "current"
            state_dir.mkdir(parents=True, exist_ok=True)
            (state_dir / "SESSION_STATE.json").write_text(
                json.dumps(
                    {
                        "run_session_id": "20260317T031125Z",
                        "iteration_number": 1,
                        "current_parent_family": "perp_multi_asset_decision",
                        "best_family": "perp_multi_asset_carry",
                    }
                )
            )

            settings = SimpleNamespace(
                root_dir=root,
                artifact_dir=root / "artifacts",
                llm_provider="deepseek",
                deepseek_model="deepseek-reasoner",
            )
            app = DashboardApp(
                settings=settings,
                lineage=lineage,
                static_dir=static_dir,
            )
            runs_payload = app.runs_payload(track="directional_perps", family=None)
            experiments_payload = app.experiments_payload(track="directional_perps", family=None)

            self.assertEqual(runs_payload["summary"]["run_count"], 1)
            self.assertEqual(experiments_payload["summary"]["run_count"], 1)
            run = runs_payload["runs"][0]
            self.assertEqual(run["run_session_id"], "20260317T031125Z")
            self.assertEqual(run["status"], "running")
            self.assertEqual(run["experiment_count"], 0)
            self.assertEqual(run["series_points"], [])
            self.assertEqual(run["llm_provider"], "deepseek")
            self.assertEqual(run["llm_model"], "deepseek-reasoner")

    def test_dashboard_run_summary_counts_repeat_burn_in_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lineage = LineageStore(root / "lineage.db")
            static_dir = Path(__file__).resolve().parents[1] / "wayfinder_autolab" / "dashboard" / "static"

            candidate = _candidate(
                family="perp_multi_asset_carry",
                hypothesis="repeat burn-in",
                features=["funding_72h_mean", "funding_carry_to_vol"],
            )
            artifact = root / "repeat.json"
            artifact.write_text(
                json.dumps(
                    {
                        "candidate_hash": candidate.strategy_hash(),
                        "candidate": candidate.canonical_dict(),
                        "summary": {
                            "aggregate_score": 2.0,
                            "median_total_return": 0.01,
                            "validation_total_return": 0.0,
                            "pre_audit_canonical_total_return": 0.0,
                            "passed": False,
                        },
                    }
                )
            )

            for iteration_number in (1, 2):
                lineage.record(
                    evaluation={
                        "candidate_hash": candidate.strategy_hash(),
                        "candidate": candidate.canonical_dict(),
                        "summary": {
                            "aggregate_score": 2.0,
                            "median_total_return": 0.01,
                            "validation_total_return": 0.0,
                            "pre_audit_canonical_total_return": 0.0,
                            "passed": False,
                        },
                    },
                    parent_hash=None,
                    research_summary={
                        "run_context": {
                            "run_session_id": "20260316T000000Z",
                            "agent_label": "autolab_harness",
                            "run_label": "repeat-burn-in",
                            "benchmark_mode": False,
                            "phase_label": "burn_in",
                            "iteration_number": iteration_number,
                            "deterministic": True,
                        }
                    },
                    artifact_path=str(artifact),
                )

            app = DashboardApp(
                settings=SimpleNamespace(root_dir=root),
                lineage=lineage,
                static_dir=static_dir,
            )
            payload = app.experiments_payload(track="directional_perps", family=None)
            runs_payload = app.runs_payload(track="directional_perps", family=None)

            run = next(row for row in payload["runs"] if row["run_session_id"] == "20260316T000000Z")
            self.assertEqual(run["experiment_count"], 2)
            self.assertEqual(run["deterministic_experiment_count"], 2)
            self.assertEqual(run["llm_experiment_count"], 0)
            self.assertEqual(len(run["series_points"]), 2)
            self.assertEqual(
                [point["run_position"] for point in run["series_points"]],
                [1, 2],
            )
            self.assertEqual(runs_payload["summary"]["best_run_session_id"], "20260316T000000Z")

            experiments = [row for row in payload["experiments"] if row["run_session_id"] == "20260316T000000Z"]
            self.assertEqual(len(experiments), 2)
            self.assertEqual(
                [row["run_iteration_number"] for row in experiments],
                [1, 2],
            )


if __name__ == "__main__":
    unittest.main()
