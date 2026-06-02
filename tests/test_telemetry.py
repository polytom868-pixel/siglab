from __future__ import annotations

import unittest
import tempfile
import json
from pathlib import Path

from siglab.telemetry import (
    aggregate_provider_metrics_artifacts,
    aggregate_trace_telemetry,
    estimate_from_provider_snapshots,
)


class TelemetryEstimateTests(unittest.TestCase):
    def test_estimate_uses_observed_provider_snapshots_without_fake_confidence(self) -> None:
        estimate = estimate_from_provider_snapshots(
            [
                {
                    "p50_ms": 1000,
                    "p95_ms": 1500,
                    "retry_count": 0,
                    "success_rate": 1.0,
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                },
                {
                    "p50_ms": 2000,
                    "p95_ms": 3000,
                    "retry_count": 1,
                    "success_rate": 0.5,
                    "usage": {"prompt_tokens": 30, "completion_tokens": 15, "total_tokens": 45},
                },
            ]
        )

        payload = estimate.to_dict()
        self.assertEqual(payload["sample_count"], 2)
        self.assertEqual(payload["confidence"], "poor")
        self.assertEqual(payload["mean_prompt_tokens"], 20.0)
        self.assertEqual(payload["mean_completion_tokens"], 10.0)
        self.assertEqual(payload["mean_total_tokens"], 30.0)
        self.assertEqual(payload["retry_rate"], 0.5)
        self.assertEqual(payload["failure_rate"], 0.25)
        self.assertFalse(payload["calibration_error_known"])

    def test_aggregate_trace_telemetry_uses_real_trace_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            trace_path = Path(tmp) / "planner_trace.json"
            trace_path.write_text(
                json.dumps(
                    {
                        "stage": "planner",
                        "claude_trace": {
                            "provider": "bai",
                            "model": "deepseek-v4-flash",
                            "tool_rounds_used": 1,
                            "tool_count_available": 3,
                            "tool_calls": [
                                {"name": "search_workspace", "latency_ms": 12.5, "result": {"ok": True}},
                                {"name": "probe_spec_gate_impact", "latency_ms": 22.5, "result": {"error": "bad gate"}},
                            ],
                        },
                    }
                ),
                encoding="utf-8",
            )

            payload = aggregate_trace_telemetry([trace_path])

            self.assertEqual(payload["trace_count"], 1)
            self.assertEqual(payload["stage_counts"], {"planner": 1})
            self.assertEqual(payload["provider_counts"], {"bai": 1})
            self.assertEqual(payload["model_counts"], {"deepseek-v4-flash": 1})
            self.assertEqual(payload["tool_counts"]["search_workspace"], 1)
            self.assertEqual(payload["tool_invocation_count"], 2)
            self.assertEqual(payload["tool_error_count"], 1)
            self.assertEqual(payload["tool_latency_ms"]["p50"], 17.5)
            self.assertEqual(payload["tool_latency_ms"]["p95"], 22.0)
            self.assertEqual(payload["confidence"], "poor")

    def test_provider_metrics_artifacts_report_usage_pressure_and_malformed_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            metrics_path = Path(tmp) / "run-1.jsonl"
            metrics_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "run_session_id": "run-1",
                                "iteration_number": 1,
                                "provider_metrics": {
                                    "provider": "bai",
                                    "model": "deepseek-v4-flash",
                                    "usage": {
                                        "prompt_tokens": 100,
                                        "completion_tokens": 40,
                                        "total_tokens": 140,
                                        "priced_tokens": 140,
                                        "credits_estimate": 2.8,
                                        "cost_usd": None,
                                        "cost_status": "verified_bai_credit_estimate_usd_unpriced",
                                    },
                                    "context_pressure": {"event_count": 1, "latest": {"reason": "near_limit"}},
                                    "credit_pressure": {"event_count": 0, "latest": None},
                                },
                            }
                        ),
                        "{bad-json",
                    ]
                ),
                encoding="utf-8",
            )

            payload = aggregate_provider_metrics_artifacts([metrics_path])

            self.assertEqual(payload["artifact_count"], 1)
            self.assertEqual(payload["malformed_count"], 1)
            self.assertEqual(payload["providers"], {"bai": 1})
            self.assertEqual(payload["models"], {"deepseek-v4-flash": 1})
            self.assertEqual(payload["usage"]["credits_estimate"], 2.8)
            self.assertEqual(payload["usage"]["cost_status"], "verified_bai_credit_estimate_usd_unpriced")
            self.assertEqual(payload["context_pressure"]["event_count"], 1.0)

    def test_provider_metrics_missing_is_explicit_not_silent_success(self) -> None:
        payload = aggregate_provider_metrics_artifacts([])

        self.assertEqual(payload["artifact_count"], 0)
        self.assertEqual(payload["malformed_count"], 0)
        self.assertIsNone(payload["usage"]["credits_estimate"])
        self.assertEqual(payload["confidence"], "poor")


if __name__ == "__main__":
    unittest.main()
