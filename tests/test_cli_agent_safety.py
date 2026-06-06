from __future__ import annotations

import contextlib
import io
import json
import inspect
import subprocess
import sys
import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace

from siglab.cli import (
    _agent_safe_memory_packet,
    _agent_safe_recent_results,
    _build_demo_manifest,
    _build_demo_report_payload,
    _build_market_report,
    _build_wave_status_payload,
    _credit_budget_stop_payload,
    _demo_manifest_html,
    _demo_report_html,
    _market_report_html,
    _deployment_ineligible_reasons,
    _external_research_from_llm_trace,
    _resolve_resume_run,
    _require_sosovalue_config,
    _sosovalue_currency_id,
    _sodex_preview_payload,
    _sodex_preflight_report,
    _strip_audit_fields,
    _tool_only_external_research,
    _trace_paths_for_telemetry,
    _provider_metric_paths_for_telemetry,
    _write_provider_metrics_artifact,
    _write_run_reflection,
    profile_command,
    run_command,
)
from siglab.config import SiglabConfig
from siglab.schemas import SignalSpec
from siglab.search.lineage import LineageStore


class CliAgentSafetyTests(unittest.TestCase):

    @staticmethod
    @contextlib.contextmanager
    def _capture_stderr():
        """Context manager that captures stderr as a StringIO."""
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            yield buf

    def test_profile_command_exposes_strict_json_profile(self) -> None:
        root = Path(__file__).resolve().parents[1]
        completed = subprocess.run(
            [sys.executable, "-m", "siglab.cli", "profile", "--strict", "--json"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        payload = json.loads(completed.stdout)

        from siglab.hardening_profile import strict_failure_count
        self.assertEqual(strict_failure_count(payload), 0)
        self.assertGreater(payload["summary"]["module_count"], 20)

    def test_run_cli_exposes_run_label_for_wayfinder_parity(self) -> None:
        root = Path(__file__).resolve().parents[1]
        completed = subprocess.run(
            [sys.executable, "-m", "siglab.cli", "run", "--help"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )

        self.assertIn("--run-label", completed.stdout)
        self.assertIn("--max-total-credits", completed.stdout)
        self.assertIn("--max-call-estimated-credits", completed.stdout)

    def test_max_call_credit_override_is_applied_to_run_not_profile(self) -> None:
        self.assertIn("settings.bai_max_call_credits", inspect.getsource(run_command))
        self.assertNotIn("settings.bai_max_call_credits", inspect.getsource(profile_command))

    def test_trace_paths_for_telemetry_filters_track_and_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trace = root / "runs" / "trend_signals" / "workspaces" / "run-1" / "iterations" / "001" / "planner_trace.json"
            other = root / "runs" / "yield_flows" / "workspaces" / "run-2" / "iterations" / "001" / "planner_trace.json"
            trace.parent.mkdir(parents=True)
            other.parent.mkdir(parents=True)
            trace.write_text("{}", encoding="utf-8")
            other.write_text("{}", encoding="utf-8")
            settings = SimpleNamespace(artifact_dir=root / "runs")

            self.assertEqual(_trace_paths_for_telemetry(settings=settings, track="trend_signals", run_session_id=None), [trace])
            self.assertEqual(_trace_paths_for_telemetry(settings=settings, track="all", run_session_id="run-2"), [other])

    def test_provider_metrics_artifact_is_persisted_and_discoverable(self) -> None:
        class FakeClaude:
            def metrics_snapshot(self) -> dict[str, object]:
                return {
                    "provider": "bai",
                    "model": "deepseek-v4-flash",
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "total_tokens": 15,
                        "credits_estimate": 0.3,
                        "cost_usd": None,
                    },
                    "context_pressure": {"event_count": 0, "latest": None},
                    "credit_pressure": {"event_count": 0, "latest": None},
                }

        with tempfile.TemporaryDirectory() as tmp:
            settings = SimpleNamespace(artifact_dir=Path(tmp) / "runs")
            jsonl_path = _write_provider_metrics_artifact(
                settings=settings,
                run_session_id="run-1",
                iteration_number=2,
                phase_label="main",
                reason="iteration_finally",
                claude=FakeClaude(),  # type: ignore[arg-type]
            )

            self.assertTrue(jsonl_path.exists())
            latest_path = settings.artifact_dir / "provider_metrics" / "run-1.latest.json"
            self.assertTrue(latest_path.exists())
            payload = json.loads(latest_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["provider_metrics"]["usage"]["credits_estimate"], 0.3)
            self.assertEqual(
                _provider_metric_paths_for_telemetry(settings=settings, run_session_id="run-1"),
                [jsonl_path],
            )

    def test_deployment_ineligible_reasons_are_explicit(self) -> None:
        reasons = _deployment_ineligible_reasons(
            summary={"passed": True, "audit_total_return": -0.03},
            trial_context={
                "fragility_label": "fragile",
                "fragility_pack": {"active_bar_count": 20},
            },
        )

        self.assertEqual(
            reasons,
            [
                "fragility_label_fragile",
                "audit_total_return_below_minus_2pct",
                "active_bar_count_below_72",
            ],
        )

    def test_max_total_cost_flag_fails_fast_until_cost_accounting_exists(self) -> None:
        root = Path(__file__).resolve().parents[1]
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "siglab.cli",
                "run",
                "--track",
                "trend_signals",
                "--skip-llm",
                "--iterations",
                "1",
                "--max-total-cost",
                "1.0",
            ],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=60,
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("--max-total-cost is not enforced yet", completed.stderr)

    def test_credit_budget_stop_uses_provider_credit_telemetry_without_usd_claim(self) -> None:
        class FakeClaude:
            def metrics_snapshot(self) -> dict[str, object]:
                return {
                    "provider": "bai",
                    "usage": {
                        "credits_estimate": 12.5,
                        "cost_usd": None,
                        "cost_status": "verified_bai_credit_estimate_usd_unpriced",
                    },
                }

        payload = _credit_budget_stop_payload(
            claude=FakeClaude(),  # type: ignore[arg-type]
            loop_policy={"max_total_credits": 10.0},
            run_label="run-1",
            runner_label="agent-1",
            phase_label="main",
            next_iteration=4,
        )

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["credits_estimate"], 12.5)
        self.assertEqual(payload["max_total_credits"], 10.0)
        self.assertEqual(
            payload["credit_budget_semantics"],
            "verified_bai_credits_between_iterations_cooperative",
        )
        self.assertIsNone(payload["provider_metrics"]["usage"]["cost_usd"])  # type: ignore[index]

    def test_credit_budget_does_not_stop_without_priced_usage(self) -> None:
        class FakeClaude:
            def metrics_snapshot(self) -> dict[str, object]:
                return {"provider": "bai", "usage": {"credits_estimate": None}}

        self.assertIsNone(
            _credit_budget_stop_payload(
                claude=FakeClaude(),  # type: ignore[arg-type]
                loop_policy={"max_total_credits": 10.0},
                run_label="run-1",
                runner_label="agent-1",
                phase_label="main",
                next_iteration=4,
            )
        )

    def test_demo_report_html_is_operator_facing_and_honest(self) -> None:
        html = _demo_report_html(
            {
                "generated_at": "2026-05-14T00:00:00Z",
                "use_case": "SoSoValue and SoDEX backed flow",
                "input_to_output_flow": ["ingest", "normalize", "preflight refuse"],
                "readiness": {"sodex_signed_execution": "FAIL_BLOCKED_BY_CREDENTIALS"},
                "latest_sosovalue_summary": {"record_count": 3, "link_count": 1},
                "latest_sodex_summary": {"record_count": 2},
                "latest_sodex_ws_probe": {"first_update_type": "snapshot"},
                "sodex_preflight": {
                    "live_write_allowed": False,
                    "missing_prerequisites": ["SODEX_ACCOUNT_ID"],
                },
                "red_flags": ["Evidence links are not causal claims."],
            }
        )

        self.assertIn("<!doctype html>", html)
        self.assertIn("FAIL_BLOCKED_BY_CREDENTIALS", html)
        self.assertIn("SODEX_ACCOUNT_ID", html)
        self.assertIn("not causal proof", html)

    def test_demo_report_payload_keeps_live_execution_blocked_without_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs = root / "runs"
            runs.mkdir(parents=True)
            report = _build_demo_report_payload(SimpleNamespace(root_dir=root, artifact_dir=runs))

        self.assertEqual(report["readiness"]["sodex_signed_execution"], "FAIL_BLOCKED_BY_CREDENTIALS")
        self.assertIn("Signed SoDEX writes are not live-proven.", report["red_flags"])
        self.assertIn("SoSoValue evidence ingestion", report["input_to_output_flow"])

    def test_demo_manifest_indexes_artifacts_without_execution_overclaim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs = root / "runs"
            (runs / "evidence").mkdir(parents=True)
            (runs / "provider_metrics").mkdir(parents=True)
            (root / "docs").mkdir()
            for path in [
                runs / "evidence" / "live_sosovalue_probe_btc_pages.jsonl",
                runs / "evidence" / "sodex_ws_evidence.jsonl",
                runs / "evidence" / "evidence_graph.html",
                runs / "market_report_latest.html",
                runs / "demo_report_latest.html",
                runs / "provider_metrics" / "run-1.jsonl",
                root / "docs" / "sosovalue-api-surface.yaml",
                root / "docs" / "sodex-api-surface.yaml",
                root / "docs" / "buildathon-readiness-audit.md",
                root / "docs" / "demo-script.md",
            ]:
                path.write_text("{}", encoding="utf-8")
            (runs / "market_report_latest.json").write_text(
                json.dumps(
                    {
                        "status": "READY_FOR_OPERATOR_REVIEW",
                        "signal_summary": {"headline": "BTC report"},
                    }
                ),
                encoding="utf-8",
            )
            (runs / "latest_telemetry_report.json").write_text(
                json.dumps({"provider_metrics_status": "present"}),
                encoding="utf-8",
            )

            manifest = _build_demo_manifest(SimpleNamespace(root_dir=root, artifact_dir=runs))

        self.assertEqual(manifest["purpose"], "buildathon_demo_artifact_index")
        self.assertTrue(manifest["artifact_status"]["provider_metrics"])
        self.assertTrue(manifest["readiness"]["provider_metrics_present"])
        self.assertEqual(manifest["readiness"]["telemetry_provider_metrics_status"], "present")
        self.assertFalse(manifest["readiness"]["sodex_live_write_allowed"])
        self.assertFalse(manifest["readiness"]["causality_claimed"])
        self.assertFalse(manifest["readiness"]["usd_cost_claimed"])

    def test_demo_manifest_html_is_a_judge_facing_panel_without_live_overclaim(self) -> None:
        html = _demo_manifest_html(
            {
                "generated_at": "2026-05-14T00:00:00Z",
                "market_report_status": "READY_FOR_OPERATOR_REVIEW",
                "market_report_headline": "BTC: ETF outflow; quote present",
                "readiness": {
                    "sosovalue_input_to_output": True,
                    "sodex_live_write_allowed": False,
                    "provider_metrics_present": True,
                    "causality_claimed": False,
                    "usd_cost_claimed": False,
                },
                "artifacts": {"market_report_json": "/tmp/market.json"},
                "artifact_status": {"market_report_json": True},
                "red_flags": ["Signed SoDEX execution is not live-validated."],
            }
        )

        self.assertIn("SigLab Buildathon Demo Panel", html)
        self.assertIn("signed SoDEX live write: False", html)
        self.assertIn("BTC: ETF outflow", html)
        self.assertIn("does not claim live signed execution", html)

    def test_market_report_links_sosovalue_and_sodex_without_causality_claim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            soso = root / "soso.jsonl"
            sodex = root / "sodex.jsonl"
            soso.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "source": "sosovalue.etf_historical_inflow",
                                "module": "ETF",
                                "relation": "total_net_inflow",
                                "entity": "us-btc-spot",
                                "timestamp": "2026-05-12",
                                "value": -10,
                                "evidence_path": "soso/etf",
                            }
                        ),
                        json.dumps(
                            {
                                "source": "sosovalue.featured_news_by_currency",
                                "module": "Feeds",
                                "relation": "news_mention",
                                "entity": "BTC",
                                "timestamp": "2026-05-13",
                                "value": "BTC ETF flow context",
                                "evidence_path": "soso/news",
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            sodex.write_text(
                json.dumps(
                    {
                        "source": "sodex.websocket",
                        "module": "SoDEX",
                        "relation": "websocket_allBookTicker",
                        "entity": "BTC-USD",
                        "timestamp": "2026-05-14T00:00:00Z",
                        "value": "100",
                        "attributes": {"bid": "100", "ask": "101"},
                        "evidence_path": "sodex/ws",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            report = _build_market_report(entity="BTC", sosovalue_evidence=soso, sodex_evidence=sodex)
            html = _market_report_html(report)

        self.assertEqual(report["status"], "READY_FOR_OPERATOR_REVIEW")
        self.assertEqual(report["signal_summary"]["flow_direction"], "ETF outflow")
        self.assertEqual(report["signal_summary"]["quote_bid"], "100")
        self.assertEqual(report["signal_summary"]["quote_ask"], "101")
        self.assertEqual(report["signal_summary"]["causality"], "not_claimed")
        self.assertEqual(report["decision_support"]["stance"], "REVIEW_CONTEXT_NOT_TRADE_SIGNAL")
        self.assertTrue(report["decision_support"]["not_a_trade_signal"])
        self.assertIn("no automatic order submission", report["decision_support"]["risk_controls"])
        self.assertIn("BTC ETF flow context", html)
        self.assertIn("Decision Support", html)
        self.assertIn("not a trade signal: True", html)
        self.assertIn("Evidence Quality", html)
        self.assertIn("SoSoValue rows", html)

    def test_market_report_uses_latest_valid_evidence_not_stale_or_malformed_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            soso = root / "soso.jsonl"
            sodex = root / "sodex.jsonl"
            soso.write_text(
                "\n".join(
                    [
                        "{malformed-json",
                        json.dumps(
                            {
                                "module": "ETF",
                                "relation": "total_net_inflow",
                                "entity": "us-btc-spot",
                                "timestamp": "2026-05-10",
                                "value": 99,
                                "evidence_path": "stale-positive",
                            }
                        ),
                        json.dumps(
                            {
                                "module": "ETF",
                                "relation": "total_net_inflow",
                                "entity": "us-btc-spot",
                                "timestamp": "not-a-time",
                                "value": -999,
                                "evidence_path": "malformed-time",
                            }
                        ),
                        json.dumps(
                            {
                                "module": "ETF",
                                "relation": "total_net_inflow",
                                "entity": "us-btc-spot",
                                "timestamp": "2026-05-13",
                                "value": -12,
                                "evidence_path": "fresh-valid",
                            }
                        ),
                        json.dumps(
                            {
                                "module": "Feeds",
                                "relation": "news_mention",
                                "entity": "BTC",
                                "timestamp": "2026-05-09",
                                "value": "old news",
                            }
                        ),
                        json.dumps(
                            {
                                "module": "Feeds",
                                "relation": "news_mention",
                                "entity": "BTC",
                                "timestamp": "2026-05-14T01:00:00Z",
                                "value": "fresh news",
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            sodex.write_text(
                "\n".join(
                    [
                        "[]",
                        json.dumps(
                            {
                                "module": "SoDEX",
                                "relation": "websocket_allBookTicker",
                                "entity": "BTC-USD",
                                "timestamp": "2026-05-14T01:00:00Z",
                                "value": "101",
                                "attributes": {"bid": "101"},
                                "evidence_path": "missing-ask",
                            }
                        ),
                        json.dumps(
                            {
                                "module": "SoDEX",
                                "relation": "websocket_allBookTicker",
                                "entity": "BTC-USD",
                                "timestamp": "2026-05-14T00:00:00Z",
                                "value": "90",
                                "attributes": {"bid": "90", "ask": "91"},
                                "evidence_path": "stale-quote",
                            }
                        ),
                        json.dumps(
                            {
                                "module": "SoDEX",
                                "relation": "websocket_allBookTicker",
                                "entity": "BTC-USD",
                                "timestamp": "2026-05-14T02:00:00Z",
                                "value": "105",
                                "attributes": {"bid": "105", "ask": "106"},
                                "evidence_path": "fresh-quote",
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            report = _build_market_report(entity="BTC", sosovalue_evidence=soso, sodex_evidence=sodex)

        self.assertEqual(report["sosovalue"]["latest_flow"]["evidence_path"], "fresh-valid")
        self.assertEqual(report["signal_summary"]["flow_direction"], "ETF outflow")
        self.assertEqual(report["signal_summary"]["news_titles"][0], "fresh news")
        self.assertEqual(report["sodex"]["latest_quote"]["evidence_path"], "fresh-quote")
        self.assertEqual(report["signal_summary"]["quote_bid"], "105")
        self.assertEqual(report["signal_summary"]["quote_ask"], "106")
        self.assertEqual(report["decision_support"]["stance"], "REVIEW_CONTEXT_NOT_TRADE_SIGNAL")
        self.assertEqual(
            report["evidence_selection"]["latest_valid_semantics"],
            "parsed_timestamp_then_observed_at_skip_invalid_required_values",
        )
        self.assertEqual(report["evidence_selection"]["sosovalue_read_stats"]["malformed_count"], 1)
        self.assertEqual(report["evidence_selection"]["sodex_read_stats"]["non_object_count"], 1)

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

        with self.assertRaises(SystemExit) as ctx, self._capture_stderr() as err:
            _require_sosovalue_config(settings)

        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("config.example.json", err.getvalue())

    def test_sosovalue_currency_id_resolves_currency_name_or_full_name(self) -> None:
        rows = [
            {"currencyId": 1, "currencyName": "eth", "fullName": "Ethereum"},
            {"currencyId": 2, "currencyName": "btc", "fullName": "Bitcoin"},
        ]

        self.assertEqual(_sosovalue_currency_id(rows, "BTC"), 2)
        self.assertEqual(_sosovalue_currency_id(rows, "bitcoin"), 2)
        self.assertIsNone(_sosovalue_currency_id(rows, "DOGE"))

    def test_sodex_preflight_reports_exact_missing_signed_prerequisites(self) -> None:
        report = _sodex_preflight_report({})

        self.assertTrue(report["public_read_ready"])
        self.assertTrue(report["schema_pinned"])
        self.assertFalse(report["signed_path"]["ready"])
        self.assertFalse(report["live_write_allowed"])
        self.assertIn("SODEX_API_KEY_NAME", report["signed_path"]["missing_prerequisites"])
        self.assertIn("SODEX_ACCOUNT_ID", report["signed_path"]["missing_prerequisites"])
        self.assertIn("SODEX_NONCE_STORE_PATH", report["signed_path"]["missing_prerequisites"])
        self.assertIn("SODEX_PRIVATE_KEY", report["signed_path"]["missing_prerequisites"])
        self.assertEqual(report["request_weight_budget_per_minute"], 1200)
        self.assertEqual(report["documented_endpoint_weights"]["perps.symbols"], 2)
        self.assertEqual(report["signed_path"]["environment"], "testnet")
        self.assertEqual(report["access_plan"]["preferred_validation_environment"], "testnet")
        self.assertIn("prefer SODEX_ENVIRONMENT=testnet", report["next_actions"][0])

    def test_sodex_preflight_rejects_malformed_account_id(self) -> None:
        report = _sodex_preflight_report(
            {
                "SODEX_API_KEY_NAME": "key",
                "SODEX_ACCOUNT_ID": "not-int",
                "SODEX_NONCE_STORE_PATH": "/tmp/nonce.json",
                "SODEX_PRIVATE_KEY": "present",
                "SODEX_ENVIRONMENT": "testnet",
            }
        )

        self.assertFalse(report["signed_path"]["ready"])
        self.assertIn("SODEX_ACCOUNT_ID must be an unsigned integer", report["signed_path"]["missing_prerequisites"])
        self.assertEqual(report["documented_endpoint_weights"]["perps.klines"], 20)
        self.assertEqual(report["rate_limit_scope"]["scope"], "per_ip")
        self.assertTrue(report["rate_limit_scope"]["local_scheduler_only"])
        self.assertIn("cancelOrder", report["supported_signed_actions"])
        self.assertIn("replaceOrder", report["unsupported_signed_actions"])

    def test_sodex_preflight_marks_signed_ready_without_printing_secret(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = _sodex_preflight_report(
                {
                    "SODEX_API_KEY_NAME": "siglab-key",
                    "SODEX_ACCOUNT_ID": "1001",
                    "SODEX_NONCE_STORE_PATH": str(Path(tmp) / "sodex-nonce.json"),
                    "SODEX_PRIVATE_KEY": "0x" + "11" * 32,
                    "SODEX_ENVIRONMENT": "testnet",
                }
            )

        self.assertTrue(report["signed_path"]["ready"])
        self.assertTrue(report["live_write_allowed"])
        self.assertEqual(report["signed_path"]["signer_type"], "evm-private-key")
        self.assertTrue(report["signed_path"]["nonce_store_ready"])
        self.assertNotIn("11" * 32, str(report))

    def test_sodex_preflight_rejects_unwritable_or_corrupt_nonce_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing_parent = Path(tmp) / "missing" / "nonce.json"
            report = _sodex_preflight_report(
                {
                    "SODEX_API_KEY_NAME": "siglab-key",
                    "SODEX_ACCOUNT_ID": "1001",
                    "SODEX_NONCE_STORE_PATH": str(missing_parent),
                    "SODEX_PRIVATE_KEY": "0x" + "11" * 32,
                    "SODEX_ENVIRONMENT": "testnet",
                }
            )
            self.assertFalse(report["signed_path"]["ready"])
            self.assertFalse(report["signed_path"]["nonce_store_ready"])
            self.assertIn("parent directory does not exist", report["signed_path"]["nonce_store"]["error"])

            corrupt = Path(tmp) / "nonce.json"
            corrupt.write_text("{bad json")
            report = _sodex_preflight_report(
                {
                    "SODEX_API_KEY_NAME": "siglab-key",
                    "SODEX_ACCOUNT_ID": "1001",
                    "SODEX_NONCE_STORE_PATH": str(corrupt),
                    "SODEX_PRIVATE_KEY": "0x" + "11" * 32,
                    "SODEX_ENVIRONMENT": "testnet",
                }
            )
            self.assertFalse(report["signed_path"]["ready"])
            self.assertFalse(report["signed_path"]["nonce_store"]["parseable"])
            self.assertIn("not parseable JSON", report["signed_path"]["nonce_store"]["error"])

    def test_sodex_preflight_blocks_mainnet_until_testnet_and_operator_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base_env = {
                "SODEX_API_KEY_NAME": "siglab-key",
                "SODEX_ACCOUNT_ID": "1001",
                "SODEX_NONCE_STORE_PATH": str(Path(tmp) / "sodex-nonce.json"),
                "SODEX_PRIVATE_KEY": "0x" + "11" * 32,
                "SODEX_ENVIRONMENT": "mainnet",
            }
            report = _sodex_preflight_report(base_env)
            self.assertFalse(report["live_write_allowed"])
            self.assertIn(
                "SODEX_TESTNET_PREFLIGHT_PASSED must be true before mainnet",
                report["signed_path"]["missing_prerequisites"],
            )
            self.assertIn(
                "SODEX_MAINNET_LIVE_WRITE_CONFIRMATION must equal I_UNDERSTAND_MAINNET_RISK",
                report["signed_path"]["missing_prerequisites"],
            )

            report = _sodex_preflight_report(
                {
                    **base_env,
                    "SODEX_TESTNET_PREFLIGHT_PASSED": "true",
                    "SODEX_MAINNET_LIVE_WRITE_CONFIRMATION": "I_UNDERSTAND_MAINNET_RISK",
                }
            )
            self.assertTrue(report["signed_path"]["ready"])
            self.assertTrue(report["live_write_allowed"])
            self.assertTrue(report["signed_path"]["testnet_preflight_passed"])
            self.assertTrue(report["signed_path"]["mainnet_confirmation_present"])

    def test_wave_status_payload_keeps_unsafe_claims_and_lists_structured(self) -> None:
        payload = _build_wave_status_payload(
            SimpleNamespace(
                wave_number=7,
                phase="validation",
                status="running",
                goal="prove operator board wave visibility",
                agents="dashboard, hardening",
                outputs="ops artifact, tests",
                blockers="signed SoDEX blocked, private WS blocked",
                validation_status="targeted_pass",
                next_decision="continue demo flow",
            )
        )

        self.assertEqual(payload["wave_number"], 7)
        self.assertEqual(payload["agents"], ["dashboard", "hardening"])
        self.assertEqual(payload["outputs"], ["ops artifact", "tests"])
        self.assertEqual(payload["blockers"], ["signed SoDEX blocked", "private WS blocked"])
        self.assertFalse(payload["stop_allowed"])
        self.assertIn("signed SoDEX live execution remains unproven", payload["unsafe_claims"])

    def test_sodex_preview_payload_does_not_sign_or_submit(self) -> None:
        payload = _sodex_preview_payload(
            SimpleNamespace(
                kind="new-order",
                account_id=1001,
                symbol_id=1,
                nonce=1760373925000,
                cl_ord_id="siglab-preview",
                modifier="NORMAL",
                side="BUY",
                order_type="LIMIT",
                time_in_force="FOK",
                price=None,
                quantity="0.01",
                funds=None,
                reduce_only=False,
                position_side="BOTH",
                leverage=1,
                margin_mode="ISOLATED",
            )
        )

        self.assertEqual(payload["path"], "/trade/orders")
        self.assertNotIn('"type":"newOrder"', payload["canonical_body"])
        self.assertIn('"type":"newOrder"', payload["canonical_signing_payload"])
        self.assertTrue(payload["signature_input"]["payloadHash"].startswith("0x"))
        self.assertIsNone(payload["signature"])
        self.assertFalse(payload["submitted"])

    def test_sodex_preview_accepts_named_enum_aliases_and_rejects_unknowns(self) -> None:
        payload = _sodex_preview_payload(
            SimpleNamespace(
                kind="new-order",
                account_id=1001,
                symbol_id=1,
                nonce=1760373925000,
                cl_ord_id="siglab-preview",
                modifier="normal",
                side="sell",
                order_type="market",
                time_in_force="ioc",
                price=None,
                quantity="0.01",
                funds=None,
                reduce_only=True,
                position_side="short",
                leverage=1,
                margin_mode="cross",
            )
        )

        self.assertIn('"side":2', payload["canonical_body"])
        self.assertIn('"type":2', payload["canonical_body"])
        self.assertIn('"timeInForce":3', payload["canonical_body"])
        self.assertIn('"positionSide":3', payload["canonical_body"])

        with self.assertRaises(SystemExit) as ctx, self._capture_stderr() as err:
            _sodex_preview_payload(
                SimpleNamespace(
                    kind="new-order",
                    account_id=1001,
                    symbol_id=1,
                    nonce=1760373925000,
                    cl_ord_id="siglab-preview",
                    modifier="NORMAL",
                    side="BAD",
                    order_type="LIMIT",
                    time_in_force="GTC",
                    price="1",
                    quantity="0.01",
                    funds=None,
                    reduce_only=False,
                    position_side="BOTH",
                    leverage=1,
                    margin_mode="ISOLATED",
                )
            )
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("--side must be one of", err.getvalue())

    def test_sodex_preview_payload_supports_cancel_safety_path(self) -> None:
        payload = _sodex_preview_payload(
            SimpleNamespace(
                kind="cancel-order",
                account_id=1001,
                symbol_id=1,
                nonce=1760373925000,
                cl_ord_id="unused",
                modifier=1,
                side=1,
                order_type=1,
                time_in_force=2,
                price=None,
                quantity=None,
                funds=None,
                order_id=None,
                orig_cl_ord_id="siglab-preview",
                scheduled_timestamp=None,
                reduce_only=False,
                position_side=1,
                leverage=1,
                margin_mode=1,
            )
        )

        self.assertEqual(payload["method"], "DELETE")
        self.assertEqual(payload["path"], "/trade/orders")
        self.assertNotIn('"type":"cancelOrder"', payload["canonical_body"])
        self.assertIn('"type":"cancelOrder"', payload["canonical_signing_payload"])
        self.assertIsNone(payload["signature"])

    def test_sodex_preview_payload_supports_schedule_cancel_safety_path(self) -> None:
        payload = _sodex_preview_payload(
            SimpleNamespace(
                kind="schedule-cancel",
                account_id=1001,
                symbol_id=1,
                nonce=1760373925000,
                cl_ord_id="unused",
                modifier=1,
                side=1,
                order_type=1,
                time_in_force=2,
                price=None,
                quantity=None,
                funds=None,
                order_id=None,
                orig_cl_ord_id=None,
                scheduled_timestamp=1760373930000,
                reduce_only=False,
                position_side=1,
                leverage=1,
                margin_mode=1,
            )
        )

        self.assertEqual(payload["method"], "POST")
        self.assertEqual(payload["path"], "/trade/orders/schedule-cancel")
        self.assertNotIn('"type":"scheduleCancel"', payload["canonical_body"])
        self.assertIn('"type":"scheduleCancel"', payload["canonical_signing_payload"])
        self.assertIsNone(payload["signature"])
        self.assertFalse(payload["submitted"])

    def test_sodex_preview_payload_supports_update_margin_decimal_string(self) -> None:
        payload = _sodex_preview_payload(
            SimpleNamespace(
                kind="update-margin",
                account_id=1001,
                symbol_id=1,
                nonce=1760373925000,
                cl_ord_id="unused",
                modifier=1,
                side=1,
                order_type=1,
                time_in_force=2,
                price=None,
                quantity=None,
                funds=None,
                order_id=None,
                orig_cl_ord_id=None,
                scheduled_timestamp=None,
                amount="-0.25",
                reduce_only=False,
                position_side=1,
                leverage=1,
                margin_mode=1,
            )
        )

        self.assertEqual(payload["path"], "/trade/margin")
        self.assertNotIn('"type":"updateMargin"', payload["canonical_body"])
        self.assertIn('"type":"updateMargin"', payload["canonical_signing_payload"])
        self.assertIn('"amount":"-0.25"', payload["canonical_body"])
        self.assertIsNone(payload["signature"])
        self.assertFalse(payload["submitted"])

    def test_sodex_preview_cli_accepts_json_flag(self) -> None:
        root = Path(__file__).resolve().parents[1]
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "siglab.cli",
                "sodex-preview",
                "--kind",
                "update-margin",
                "--account-id",
                "1001",
                "--symbol-id",
                "1",
                "--nonce",
                "1760373925000",
                "--amount",
                "-0.25",
                "--json",
            ],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=60,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["canonical_body"], '{"accountID":1001,"symbolID":1,"amount":"-0.25"}')
        self.assertIn('"type":"updateMargin"', payload["canonical_signing_payload"])

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



