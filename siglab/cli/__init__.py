"""SigLab CLI package — modular subcommand modules.

This module sets up the argument parser and dispatches to the appropriate
subcommand module. All subcommands are organized into domain-specific modules.

It also re-exports symbols from submodules so that existing import statements
(like `from siglab.cli import ...`) continue to work.
"""

from __future__ import annotations

import argparse
import asyncio

# ── Backward-compatibility re-exports ──────────────────────────────────────
# These allow existing code and tests to import symbols directly from siglab.cli.
# New code should import from the specific submodule (e.g. siglab.cli.helpers).
# ruff: noqa: F401 — all imports here are re-exports, not directly used in this file.

# Helpers
from siglab.cli.helpers import (
    agent_safe_memory_packet as _agent_safe_memory_packet,
    agent_safe_recent_results as _agent_safe_recent_results,
    base_spec_payload_for_family as _base_spec_payload_for_family,
    deployment_eligible as _deployment_eligible,
    deployment_ineligible_reasons as _deployment_ineligible_reasons,
    external_research_from_llm_trace as _external_research_from_llm_trace,
    float_or_none as _float_or_none,
    incumbent_detail as _incumbent_detail,
    latest_path as _latest_path,
    load_json_if_exists as _load_json_if_exists,
    minimal_research_summary as _minimal_research_summary,
    motif_audit_streak as _motif_audit_streak,
    parse_family_scope as _parse_family_scope,
    parse_sodex_enum as _parse_sodex_enum,
    pick_deterministic_parent,
    read_jsonl as _read_jsonl,
    read_jsonl_with_stats as _read_jsonl_with_stats,
    require_sosovalue_config as _require_sosovalue_config,
    row_is_deterministic as _row_is_deterministic,
    sodex_preflight_report as _sodex_preflight_report,
    sosovalue_currency_id as _sosovalue_currency_id,
    spec_trade_style as _spec_trade_style,
    split_cli_list as _split_cli_list,
    strip_audit_fields as _strip_audit_fields,
    tool_only_external_research as _tool_only_external_research,
    write_artifact as _write_artifact,
)

# Demo module
from siglab.cli.demo import (
    _build_demo_manifest,
    _build_demo_report_payload,
    _build_wave_status_payload,
    _demo_manifest_html,
    _demo_report_html,
)

# Market module
from siglab.cli.market import (
    build_market_report as _build_market_report,
    market_report_html as _market_report_html,
)

# Telemetry module
from siglab.cli.telemetry import (
    provider_metric_paths_for_telemetry as _provider_metric_paths_for_telemetry,
    trace_paths_for_telemetry as _trace_paths_for_telemetry,
)

# Sodex module
from siglab.cli.sodex import (
    _sodex_preview_payload,
)

# Run module (internal helpers used by tests)
from siglab.cli.run import (
    _credit_budget_stop_payload_internal as _credit_budget_stop_payload,
    _write_provider_metrics_artifact_internal as _write_provider_metrics_artifact,
    _write_run_reflection_internal as _write_run_reflection,
)

# run_config re-exported from original module
from siglab.run_config import (
    resolve_resume_run as _resolve_resume_run,
)

# Command handler re-exports (backward compat for tests and external callers)
from siglab.cli.profile import run_command as profile_command
from siglab.cli.run import run_command
from siglab.cli.run import inspect_command


def main() -> None:
    """Entry point: parse args, dispatch to subcommand handler."""
    parser = argparse.ArgumentParser(prog="siglab")
    parser.add_argument(
        "--no-color",
        action="store_true",
        default=False,
        help="Disable ANSI color output. Also respects NO_COLOR env var.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Import and register all subcommand parsers
    # (imports are lazy — each module registers its parsers inline)
    from siglab.cli import (
        profile as _profile_mod,
        config_cmd as _config_mod,
        api as _api_mod,
        evidence as _evidence_mod,
        demo as _demo_mod,
        market as _market_mod,
        telemetry as _telemetry_mod,
        dashboard as _dashboard_mod,
        deploy as _deploy_mod,
        sodex as _sodex_mod,
        benchmark as _benchmark_mod,
        paper as _paper_mod,
        ancestry_cmd as _ancestry_mod,
        run as _run_mod,
    )

    _profile_mod.add_subparser(subparsers)
    _config_mod.add_subparser(subparsers)
    _api_mod.add_subparser(subparsers)
    _evidence_mod.add_subparser(subparsers)
    _demo_mod.add_subparser(subparsers)
    _market_mod.add_subparser(subparsers)
    _telemetry_mod.add_subparser(subparsers)
    _dashboard_mod.add_subparser(subparsers)
    _deploy_mod.add_subparser(subparsers)
    _sodex_mod.add_subparser(subparsers)
    _benchmark_mod.add_subparser(subparsers)
    _paper_mod.add_subparser(subparsers)
    _ancestry_mod.add_subparser(subparsers)
    _run_mod.add_subparser(subparsers)

    args = parser.parse_args()

    # Initialize shared Rich console before any command runs
    from siglab.cli.rich_utils import init_console
    init_console(force_no_color=getattr(args, "no_color", False))

    # Dispatch by command name
    if args.command == "run":
        asyncio.run(_run_mod.run_command(args))
        return
    if args.command == "inspect":
        asyncio.run(_run_mod.inspect_command(args))
        return
    if args.command == "profile":
        _profile_mod.run_command(args)
        return
    if args.command == "evidence-build":
        asyncio.run(_evidence_mod.run_evidence_build(args))
        return
    if args.command == "evidence-map":
        _evidence_mod.run_evidence_map(args)
        return
    if args.command == "demo-report":
        _demo_mod.run_demo_report(args)
        return
    if args.command == "demo-manifest":
        _demo_mod.run_demo_manifest(args)
        return
    if args.command == "demo-refresh":
        _demo_mod.run_demo_refresh(args)
        return
    if args.command == "market-report":
        _market_mod.run_command(args)
        return
    if args.command == "api-surface":
        _api_mod.run_command(args)
        return
    if args.command == "ancestry":
        _ancestry_mod.run_ancestry(args)
        return
    if args.command == "clear-passed":
        _ancestry_mod.run_clear_passed(args)
        return
    if args.command == "config":
        _config_mod.run_command(args)
        return
    if args.command == "dashboard":
        _dashboard_mod.run_dashboard(args)
        return
    if args.command == "dashboard-start":
        _dashboard_mod.run_dashboard_start(args)
        return
    if args.command == "dashboard-stop":
        _dashboard_mod.run_dashboard_stop(args)
        return
    if args.command == "deploy":
        asyncio.run(_deploy_mod.run_deploy(args))
        return
    if args.command == "deployments":
        _deploy_mod.run_deployments(args)
        return
    if args.command == "sodex-preflight":
        _sodex_mod.run_sodex_preflight(args)
        return
    if args.command == "valuechain-preflight":
        asyncio.run(_sodex_mod.run_valuechain_preflight(args))
        return
    if args.command == "sodex-ws-probe":
        asyncio.run(_sodex_mod.run_sodex_ws_probe(args))
        return
    if args.command == "sodex-preview":
        _sodex_mod.run_sodex_preview(args)
        return
    if args.command == "benchmark-init":
        _benchmark_mod.run_benchmark_init(args)
        return
    if args.command == "benchmark-eval":
        asyncio.run(_benchmark_mod.run_benchmark_eval(args))
        return
    if args.command == "benchmark-status":
        _benchmark_mod.run_benchmark_status(args)
        return
    if args.command == "wave-status":
        _demo_mod.run_wave_status(args)
        return
    if args.command == "telemetry-report":
        _telemetry_mod.run_command(args)
        return
    if args.command == "paper-start":
        asyncio.run(_paper_mod.run_paper_start(args))
        return
    if args.command == "paper-status":
        asyncio.run(_paper_mod.run_paper_status(args))
        return
    if args.command == "paper-promote":
        asyncio.run(_paper_mod.run_paper_promote(args))
        return


if __name__ == "__main__":
    main()
