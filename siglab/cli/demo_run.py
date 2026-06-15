"""One-shot demo-run entry point: aggregates sodex-preflight, demo-manifest, market-report, and telemetry-report into a single judge-friendly summary.

This module exists so a judge / operator can run a single command and get a
one-page summary of the entire buildathon demo surface. It is intentionally
narrow: it delegates to existing builders and prints one-line summaries for
each subsystem, then returns a single JSON dict with the 4 sub-summaries plus
a text summary line.
"""

from __future__ import annotations

import argparse
from typing import Any

from siglab.cli.demo import _build_demo_manifest
from siglab.cli.helpers import (
    latest_path,
    sodex_preflight_report,
)
from siglab.cli.market import build_market_report
from siglab.cli.rich_utils import get_console, print_json
from siglab.cli.telemetry import (
    build_telemetry_payload,
    provider_metric_paths_for_telemetry,
    trace_paths_for_telemetry,
)

def add_subparser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the demo-run subparser next to demo-refresh."""
    parser = subparsers.add_parser(
        "demo-run",
        help="One-shot judge-friendly demo summary: preflight + manifest + market + telemetry.",
    )
    parser.add_argument("--json", action="store_true", help="Output the full summary as JSON.")


def run_demo_run(settings: Any, *, json: bool = False) -> dict[str, Any]:
    """Run the 4 demo subsystems and return a single judge-friendly summary.

    Steps (each is delegated to the existing builder; no actual I/O is performed
    on the live system — the builders read from local artifact paths only):
      1. sodex-preflight  -> public_read_ready / signed_path ready / live_write_allowed
      2. demo-manifest    -> readiness / unsafe_claims
      3. market-report    -> entity / status / warnings
      4. telemetry-report -> trace_count / provider_metrics_status / confidence

    Returns a single dict with the 4 sub-summaries and a one-page text summary.
    """
    # 1. sodex-preflight
    preflight = sodex_preflight_report()
    preflight_summary = {
        "public_read_ready": preflight.get("public_read_ready"),
        "schema_pinned": preflight.get("schema_pinned"),
        "signed_path_ready": preflight.get("signed_path", {}).get("ready"),
        "environment": preflight.get("signed_path", {}).get("environment"),
        "live_write_allowed": preflight.get("live_write_allowed"),
    }

    # 2. demo-manifest
    manifest = _build_demo_manifest(settings)
    manifest_summary = {
        "readiness": manifest.get("readiness"),
        "unsafe_claims": manifest.get("unsafe_claims"),
        "blockers": manifest.get("blockers"),
        "artifact_count": len(manifest.get("artifacts", []) or []),
    }

    # 3. market-report
    evidence_dir = settings.root_dir / "runs" / "evidence"
    sosovalue_path = latest_path(evidence_dir, "*sosovalue*.jsonl")
    sodex_path = evidence_dir / "sodex_ws_evidence.jsonl"
    market = build_market_report(
        entity="BTC",
        sosovalue_evidence=sosovalue_path,
        sodex_evidence=sodex_path,
    )
    market_summary = {
        "entity": market.get("entity"),
        "status": market.get("status"),
        "warnings": market.get("warnings"),
        "as_of": market.get("as_of"),
    }

    # 4. telemetry-report
    trace_paths = trace_paths_for_telemetry(settings=settings, track="all", run_session_id=None)
    provider_metric_paths = provider_metric_paths_for_telemetry(settings=settings, run_session_id=None)
    telemetry = build_telemetry_payload(
        trace_paths=trace_paths,
        provider_metric_paths=provider_metric_paths,
    )
    telemetry_summary = {
        "trace_count": telemetry.get("trace_count"),
        "tool_invocation_count": telemetry.get("tool_invocation_count"),
        "provider_metrics_status": telemetry.get("provider_metrics_status"),
        "confidence": telemetry.get("confidence"),
    }

    # One-line text summaries (printed so a judge can read stdout directly).
    preflight_line = (
        f"preflight: public_read={preflight_summary['public_read_ready']} "
        f"signed={preflight_summary['signed_path_ready']} "
        f"live_write={preflight_summary['live_write_allowed']}"
    )
    manifest_line = (
        f"manifest: readiness={manifest_summary['readiness']} "
        f"artifacts={manifest_summary['artifact_count']}"
    )
    market_line = (
        f"market: entity={market_summary['entity']} status={market_summary['status']} "
        f"warnings={len(market_summary['warnings'] or [])}"
    )
    telemetry_line = (
        f"telemetry: traces={telemetry_summary['trace_count']} "
        f"tools={telemetry_summary['tool_invocation_count']} "
        f"providers={telemetry_summary['provider_metrics_status']}"
    )
    one_page_summary = " | ".join([preflight_line, manifest_line, market_line, telemetry_line])

    payload: dict[str, Any] = {
        "summary": one_page_summary,
        "sodex_preflight": preflight_summary,
        "demo_manifest": manifest_summary,
        "market_report": market_summary,
        "telemetry_report": telemetry_summary,
    }

    if json:
        print_json(payload)
    else:
        console = get_console()
        console.print(preflight_line)
        console.print(manifest_line)
        console.print(market_line)
        console.print(telemetry_line)
        console.print()
        console.print(one_page_summary)
    return payload
