"""SigLab CLI package — modular subcommand modules.

This module sets up the argument parser and dispatches to the appropriate
subcommand module. All subcommands are organized into domain-specific modules.
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys

# During module-load time, SIGINT exits silently to avoid tracebacks during
# heavy import chains. The default handler is restored inside main() so
# subcommand handlers can catch KeyboardInterrupt.
_import_sigint = signal.signal(signal.SIGINT, lambda s, f: sys.exit(130))

# ── Backward-compatibility re-exports ──────────────────────────────────────
# These allow existing code and tests to import symbols directly from siglab.cli.
# New code should import from the specific submodule (e.g. siglab.cli.helpers).
# ruff: noqa: F401 — all imports here are re-exports, not directly used in this file.

# Helpers
from siglab.cli.helpers import (
    deployment_eligible as _deployment_eligible,
    deployment_ineligible_reasons as _deployment_ineligible_reasons,
    parse_sodex_enum as _parse_sodex_enum,
    require_sosovalue_config as _require_sosovalue_config,
    sodex_preflight_report as _sodex_preflight_report,
)

# Demo module
from siglab.cli.demo import (
    _build_demo_manifest,
    _build_wave_status_payload,
    _demo_manifest_html,
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


def main() -> None:
    """Entry point: parse args, dispatch to subcommand handler."""
    # Restore default SIGINT handler so subcommand handlers can catch
    # KeyboardInterrupt for graceful shutdown.
    signal.signal(signal.SIGINT, signal.default_int_handler)

    parser = argparse.ArgumentParser(prog="siglab")
    parser.add_argument(
        "--no-color",
        action="store_true",
        default=False,
        help="Disable ANSI color output. Also respects NO_COLOR env var.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Import and register all subcommand parsers
    from siglab.cli import (
        evidence as _evidence_mod,
        demo as _demo_mod,
        market as _market_mod,
        telemetry as _telemetry_mod,
        dashboard as _dashboard_mod,
        deploy as _deploy_mod,
        sodex as _sodex_mod,
        paper as _paper_mod,
    )

    _evidence_mod.add_subparser(subparsers)
    _demo_mod.add_subparser(subparsers)
    _market_mod.add_subparser(subparsers)
    _telemetry_mod.add_subparser(subparsers)
    _dashboard_mod.add_subparser(subparsers)
    _deploy_mod.add_subparser(subparsers)
    _sodex_mod.add_subparser(subparsers)
    _paper_mod.add_subparser(subparsers)
    tui_p = subparsers.add_parser("tui", help="Launch the SigLab Terminal UI.")
    tui_p.set_defaults(_handler="tui")

    args = parser.parse_args()

    # Initialize shared Rich console before any command runs
    from siglab.cli.rich_utils import init_console
    init_console(force_no_color=getattr(args, "no_color", False))

    # Dispatch by command name
    if args.command == "evidence-build":
        asyncio.run(_evidence_mod.run_evidence_build(args))
        return
    if args.command == "evidence-map":
        _evidence_mod.run_evidence_map(args)
        return
    if args.command == "demo":
        if args.demo_command == "run":
            _demo_mod.run_demo_run(args)
            return
        if args.command == "demo" and args.demo_command == "manifest":
            _demo_mod.run_demo_manifest(args)
            return
    if args.command == "market-report":
        _market_mod.run_command(args)
        return
    if args.command == "telemetry-report":
        _telemetry_mod.run_command(args)
        return
    if args.command == "dashboard":
        _dashboard_mod.run_dashboard(args)
        return
    if args.command == "dashboard-start":
        _dashboard_mod.run_dashboard_start(args)
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
    if args.command == "tui":
        from siglab.tui.__main__ import main as _tui_main
        _tui_main()
        return

if __name__ == "__main__":
    main()
