"""Telemetry report subcommand: aggregate LLM/tool telemetry."""

from __future__ import annotations

import argparse

from siglab.cli.rich_utils import get_console, make_table, print_json
from siglab.config import load_settings
from siglab.telemetry import (
    build_telemetry_payload,
    provider_metric_paths_for_telemetry,
    trace_paths_for_telemetry,
)



def add_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
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
    payload = build_telemetry_payload(
        trace_paths=trace_paths,
        provider_metric_paths=provider_metric_paths,
    )
    if getattr(args, "json", False):
        print_json(payload)
    else:
        import json as _json

        table = make_table(title="Telemetry Report")
        table.add_column("Metric", style="label", no_wrap=True)
        table.add_column("Value")
        table.add_row("trace_count", str(payload["trace_count"]))
        table.add_row(
            "stage_counts",
            _json.dumps(payload["stage_counts"], sort_keys=True),
        )
        table.add_row(
            "provider_counts",
            _json.dumps(payload["provider_counts"], sort_keys=True),
        )
        table.add_row(
            "model_counts",
            _json.dumps(payload["model_counts"], sort_keys=True),
        )
        table.add_row("tool_invocation_count", str(payload["tool_invocation_count"]))
        table.add_row(
            "tool_counts",
            _json.dumps(payload["tool_counts"], sort_keys=True),
        )
        table.add_row(
            "tool_latency_ms",
            _json.dumps(payload["tool_latency_ms"], sort_keys=True),
        )
        table.add_row(
            "provider_metrics_status",
            str(payload["provider_metrics_status"]),
        )
        table.add_row("confidence", str(payload["confidence"]))
        get_console().print(table)



