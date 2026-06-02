"""Telemetry report subcommand: aggregate LLM/tool telemetry."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from siglab.config import load_settings
from siglab.telemetry import aggregate_provider_metrics_artifacts, aggregate_trace_telemetry


def add_subparser(subparsers) -> None:
    parser = subparsers.add_parser(
        "telemetry-report",
        help="Aggregate empirical LLM/tool telemetry from run trace artifacts.",
    )
    parser.add_argument("--track", default="all")
    parser.add_argument("--run-session-id", default=None)
    parser.add_argument("--json", action="store_true")


def run_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    trace_paths = trace_paths_for_telemetry(
        settings=settings,
        track=str(args.track or "all"),
        run_session_id=args.run_session_id,
    )
    provider_metric_paths = provider_metric_paths_for_telemetry(
        settings=settings,
        run_session_id=args.run_session_id,
    )
    payload = aggregate_trace_telemetry(trace_paths)
    payload["trace_paths_scanned"] = len(trace_paths)
    payload["provider_metrics"] = aggregate_provider_metrics_artifacts(provider_metric_paths)
    payload["provider_metrics_paths_scanned"] = len(provider_metric_paths)
    payload["provider_metrics_status"] = (
        "missing"
        if trace_paths and payload["provider_metrics"]["artifact_count"] == 0
        else "present"
        if payload["provider_metrics"]["artifact_count"] > 0
        else "not_applicable"
    )
    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        print(
            "\n".join(
                [
                    f"trace_count: {payload['trace_count']}",
                    f"stage_counts: {payload['stage_counts']}",
                    f"provider_counts: {payload['provider_counts']}",
                    f"model_counts: {payload['model_counts']}",
                    f"tool_invocation_count: {payload['tool_invocation_count']}",
                    f"tool_counts: {payload['tool_counts']}",
                    f"tool_latency_ms: {payload['tool_latency_ms']}",
                    f"provider_metrics_status: {payload['provider_metrics_status']}",
                    f"provider_metrics: {payload['provider_metrics']}",
                    f"confidence: {payload['confidence']}",
                ]
            )
        )


def trace_paths_for_telemetry(*, settings: Any, track: str, run_session_id: str | None) -> list[Path]:
    base = settings.artifact_dir
    if run_session_id:
        pattern = f"*/workspaces/{run_session_id}/iterations/**/*_trace.json"
    elif track == "all":
        pattern = "*/workspaces/*/iterations/**/*_trace.json"
    else:
        pattern = f"{track}/workspaces/*/iterations/**/*_trace.json"
    return sorted(base.glob(pattern))


def provider_metric_paths_for_telemetry(*, settings: Any, run_session_id: str | None) -> list[Path]:
    base = settings.artifact_dir / "provider_metrics"
    if run_session_id:
        jsonl_path = base / f"{run_session_id}.jsonl"
        if jsonl_path.exists():
            return [jsonl_path]
        latest_path = base / f"{run_session_id}.latest.json"
        return [latest_path] if latest_path.exists() else []
    jsonl_paths = sorted(base.glob("*.jsonl"))
    if jsonl_paths:
        return jsonl_paths
    return sorted(base.glob("*.latest.json"))
