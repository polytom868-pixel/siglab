from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from siglab.dashboard.server import DashboardApp
from siglab.schemas import SignalSpec
from siglab.search.lineage import LineageStore


def _spec(*, family: str, hypothesis: str, features: list[str]) -> SignalSpec:
    return SignalSpec.from_dict(
        {
            "track": "trend_signals",
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
            ancestry = LineageStore(root / "ancestry.db")
            static_dir = Path(__file__).resolve().parents[1] / "siglab" / "dashboard" / "static"

            benchmark_spec = _spec(
                family="perp_multi_asset_carry",
                hypothesis="benchmark keep",
                features=["funding_72h_mean", "funding_carry_to_vol"],
            )
            harness_spec = _spec(
                family="perp_basket_neutral_levered",
                hypothesis="harness pass",
                features=["price_return_24h", "relative_carry_z_72h"],
            )

            benchmark_artifact = root / "benchmark.json"
            benchmark_artifact.write_text(
                json.dumps(
                    {
                        "spec_hash": benchmark_spec.strategy_hash(),
                        "spec": benchmark_spec.canonical_dict(),
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
                        "spec_hash": harness_spec.strategy_hash(),
                        "spec": harness_spec.canonical_dict(),
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

            ancestry.record(
                evaluation={
                    "spec_hash": benchmark_spec.strategy_hash(),
                    "spec": benchmark_spec.canonical_dict(),
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
                        "runner_label": "claude_code",
                        "run_label": "claude-benchmark-1",
                        "benchmark_mode": True,
                        "benchmark_deck": "trend_signals_external",
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
            ancestry.record(
                evaluation={
                    "spec_hash": harness_spec.strategy_hash(),
                    "spec": harness_spec.canonical_dict(),
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
                        "runner_label": "siglab_harness",
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
                ancestry=ancestry,
                static_dir=static_dir,
            )
            payload = app.experiments_payload(track="trend_signals", family=None)
            runs_payload = app.runs_payload(track="trend_signals", family=None)

            self.assertEqual(payload["summary"]["run_count"], 2)
            self.assertEqual(payload["summary"]["benchmark_run_count"], 1)
            self.assertEqual(payload["summary"]["harness_run_count"], 1)
            self.assertEqual(len(payload["runs"]), 2)
            self.assertEqual(runs_payload["summary"]["run_count"], 2)
            self.assertEqual(runs_payload["summary"]["experiment_count"], 2)
            self.assertIn("perp_multi_asset_carry", runs_payload["summary"]["families"])

            benchmark_run = next(row for row in payload["runs"] if row["benchmark_mode"])
            self.assertEqual(benchmark_run["runner_label"], "claude_code")
            self.assertEqual(benchmark_run["run_label"], "claude-benchmark-1")
            self.assertEqual(benchmark_run["tool_call_count"], 2)
            self.assertEqual(benchmark_run["status"], "pass")
            self.assertEqual(benchmark_run["best_spec_hash"], benchmark_spec.strategy_hash())
            self.assertEqual(len(benchmark_run["series_points"]), 1)
            self.assertEqual(
                benchmark_run["series_points"][0]["spec_hash"],
                benchmark_spec.strategy_hash(),
            )

            harness_run = next(row for row in payload["runs"] if not row["benchmark_mode"])
            self.assertEqual(harness_run["runner_label"], "siglab_harness")
            self.assertEqual(harness_run["run_label"], "harness-1")
            self.assertEqual(harness_run["status"], "fail")

            experiment = next(
                row for row in payload["experiments"] if row["spec_hash"] == benchmark_spec.strategy_hash()
            )
            self.assertEqual(experiment["run_session_id"], "benchmark::deck::claude_code::1")
            self.assertEqual(experiment["runner_label"], "claude_code")
            self.assertEqual(experiment["run_kind"], "benchmark")
            self.assertEqual(experiment["artifact_path"], "benchmark.json")

    def test_dashboard_reads_tool_traces_from_workspace_trace_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ancestry = LineageStore(root / "ancestry.db")
            static_dir = Path(__file__).resolve().parents[1] / "siglab" / "dashboard" / "static"

            spec = _spec(
                family="perp_multi_asset_carry",
                hypothesis="workspace trace",
                features=["funding_72h_mean", "funding_carry_to_vol"],
            )
            artifact = root / "trace-artifact.json"
            artifact.write_text(
                json.dumps(
                    {
                        "spec_hash": spec.strategy_hash(),
                        "spec": spec.canonical_dict(),
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
                        "claude_trace": {
                            "model": "claude-k2.5",
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

            ancestry.record(
                evaluation={
                    "spec_hash": spec.strategy_hash(),
                    "spec": spec.canonical_dict(),
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
                        "runner_label": "siglab_harness",
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
                ancestry=ancestry,
                static_dir=static_dir,
            )
            payload = app.experiments_payload(track="trend_signals", family=None)
            runs_payload = app.runs_payload(track="trend_signals", family=None)

            experiment = payload["experiments"][0]
            self.assertEqual(experiment["tool_call_count"], 1)
            self.assertEqual(experiment["tool_trace"]["tool_rounds_used"], 1)
            self.assertEqual(experiment["tool_trace"]["tool_calls"][0]["name"], "search_features")
            self.assertEqual(len(experiment["tool_trace_stages"]), 1)
            self.assertEqual(experiment["tool_trace_stages"][0]["stage"], "planner")
            self.assertEqual(
                experiment["skill_value_report"],
                [
                    {
                        "skill_name": "search_features",
                        "stages": ["planner"],
                        "invocation_count": 1,
                        "cost_contribution": 1,
                        "latency_cost_ms": 0.0,
                        "token_context_cost": 0,
                        "value_contribution": "feature_surface_grounding",
                        "effect_on_output_quality": "medium",
                        "keep_rate_impact": "unmeasured",
                        "error_reduction": "unmeasured",
                        "evidence_quality_effect": "medium",
                        "classification": "HIGH_VALUE",
                    }
                ],
            )

            run = runs_payload["runs"][0]
            self.assertEqual(run["tool_call_count"], 1)
            self.assertEqual(run["llm_provider"], "claude")
            self.assertEqual(run["llm_model"], "claude-k2.5")

    def test_skill_value_report_marks_redundant_low_signal_calls_noisy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = DashboardApp(
                settings=SimpleNamespace(root_dir=root),
                ancestry=SimpleNamespace(),
                static_dir=root,
            )

            report = app._skill_value_report(
                [
                    {"name": "search_workspace", "stage": "planner", "latency_ms": 10, "context_tokens": 5}
                    for _ in range(9)
                ]
            )

            self.assertEqual(report[0]["classification"], "NOISY")
            self.assertEqual(report[0]["latency_cost_ms"], 90.0)
            self.assertEqual(report[0]["token_context_cost"], 45)

    def test_ops_payload_summarizes_latest_operator_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs = root / "runs"
            runs.mkdir()
            (runs / "demo_manifest_latest.json").write_text(
                json.dumps(
                    {
                        "readiness": {
                            "sosovalue_input_to_output": True,
                            "sodex_public_market_data": True,
                            "provider_metrics_present": True,
                        },
                        "market_report_status": "ready",
                        "red_flags": ["signed live writes blocked"],
                        "artifacts": [{"label": "demo", "path": "runs/demo.html"}],
                    }
                )
            )
            (runs / "latest_telemetry_report.json").write_text(
                json.dumps(
                    {
                        "confidence": "proxy",
                        "trace_count": 3,
                        "tool_invocation_count": 7,
                        "tool_error_count": 1,
                        "provider_metrics_status": "present",
                        "provider_metrics": {
                            "request_count": 2,
                            "estimated_credits": 0.12,
                            "returned_input_tokens": 120,
                            "returned_output_tokens": 40,
                            "context_pressure_events": 1,
                            "credit_pressure_events": 0,
                        },
                    }
                )
            )
            (runs / "market_report_latest.json").write_text(
                json.dumps(
                    {
                        "status": "ready",
                        "entity": "BTC",
                        "signal_summary": {
                            "headline": "ETF flow positive; SoDEX spread normal",
                            "flow_direction": "positive",
                            "quote_bid": "102.1",
                            "quote_ask": "102.3",
                        },
                        "decision_support": {"stance": "watch"},
                        "warnings": ["correlation only"],
                    }
                )
            )
            (runs / "sodex_preflight_latest.json").write_text(
                json.dumps(
                    {
                        "public_read_ready": True,
                        "schema_pinned": True,
                        "live_write_allowed": False,
                        "live_write_refusal_reason": "missing signer",
                        "request_weight_budget_per_minute": 1200,
                        "signed_path": {"ready": False},
                        "next_actions": ["set SODEX_PRIVATE_KEY"],
                    }
                )
            )
            (runs / "wave_status_latest.json").write_text(
                json.dumps(
                    {
                        "wave_number": 4,
                        "phase": "execution",
                        "status": "running",
                        "goal": "wire ops board wave visibility",
                        "agents": ["dashboard", "hardening"],
                        "outputs": ["ops payload"],
                        "blockers": ["signed SoDEX blocked"],
                        "validation_status": "targeted_pass",
                        "next_decision": "continue product flow",
                        "stop_allowed": False,
                        "unsafe_claims": ["private WS unvalidated"],
                    }
                )
            )

            app = DashboardApp(
                settings=SimpleNamespace(root_dir=root),
                ancestry=SimpleNamespace(),
                static_dir=root,
            )
            payload = app.ops_payload()

            self.assertEqual(payload["artifact_status"]["demo_manifest"]["status"], "present")
            self.assertTrue(payload["summary"]["buildathon"]["sosovalue_flow"])
            self.assertEqual(payload["summary"]["market"]["headline"], "ETF flow positive; SoDEX spread normal")
            self.assertEqual(payload["summary"]["sodex"]["live_write_allowed"], False)
            self.assertEqual(payload["summary"]["telemetry"]["provider_request_count"], 2)
            self.assertEqual(payload["summary"]["telemetry"]["estimated_credits"], 0.12)
            self.assertEqual(payload["artifact_status"]["wave_status"]["status"], "present")
            self.assertEqual(payload["summary"]["wave"]["wave_number"], 4)
            self.assertEqual(payload["summary"]["wave"]["agents"], ["dashboard", "hardening"])
            self.assertEqual(payload["summary"]["wave"]["blockers"], ["signed SoDEX blocked"])

    def test_ops_payload_marks_missing_and_malformed_artifacts_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs = root / "runs"
            runs.mkdir()
            (runs / "demo_manifest_latest.json").write_text("{bad json")

            app = DashboardApp(
                settings=SimpleNamespace(root_dir=root),
                ancestry=SimpleNamespace(),
                static_dir=root,
            )
            payload = app.ops_payload()

            self.assertEqual(payload["artifact_status"]["demo_manifest"]["status"], "malformed")
            self.assertEqual(payload["artifact_status"]["telemetry"]["status"], "missing")
            self.assertEqual(payload["artifact_status"]["wave_status"]["status"], "missing")
            self.assertIsNone(payload["summary"]["buildathon"]["sosovalue_flow"])
            self.assertIsNone(payload["summary"]["sodex"]["live_write_allowed"])
            self.assertIsNone(payload["summary"]["wave"]["wave_number"])

    def test_dashboard_includes_active_workspace_run_without_ancestry_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ancestry = LineageStore(root / "ancestry.db")
            static_dir = Path(__file__).resolve().parents[1] / "siglab" / "dashboard" / "static"

            state_dir = root / "runs" / "trend_signals" / "workspaces" / "20260317T031125Z" / "current"
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
                artifact_dir=root / "runs",
                llm_provider="deepseek",
                deepseek_model="deepseek-reasoner",
            )
            app = DashboardApp(
                settings=settings,
                ancestry=ancestry,
                static_dir=static_dir,
            )
            runs_payload = app.runs_payload(track="trend_signals", family=None)
            experiments_payload = app.experiments_payload(track="trend_signals", family=None)

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
            ancestry = LineageStore(root / "ancestry.db")
            static_dir = Path(__file__).resolve().parents[1] / "siglab" / "dashboard" / "static"

            spec = _spec(
                family="perp_multi_asset_carry",
                hypothesis="repeat burn-in",
                features=["funding_72h_mean", "funding_carry_to_vol"],
            )
            artifact = root / "repeat.json"
            artifact.write_text(
                json.dumps(
                    {
                        "spec_hash": spec.strategy_hash(),
                        "spec": spec.canonical_dict(),
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
                ancestry.record(
                    evaluation={
                        "spec_hash": spec.strategy_hash(),
                        "spec": spec.canonical_dict(),
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
                            "runner_label": "siglab_harness",
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
                ancestry=ancestry,
                static_dir=static_dir,
            )
            payload = app.experiments_payload(track="trend_signals", family=None)
            runs_payload = app.runs_payload(track="trend_signals", family=None)

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



