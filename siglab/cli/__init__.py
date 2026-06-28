from __future__ import annotations

import argparse
import asyncio
import signal
import sys

_import_sigint = signal.signal(signal.SIGINT, lambda s, f: sys.exit(130))


def main() -> None:
    signal.signal(signal.SIGINT, signal.default_int_handler)
    parser = argparse.ArgumentParser(prog="siglab")
    parser.add_argument(
        "--no-color",
        action="store_true",
        default=False,
        help="Disable ANSI color output. Also respects NO_COLOR env var.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    from siglab.cli import dashboard as _dashboard_mod
    from siglab.cli import demo as _demo_mod
    from siglab.cli import helpers as _market_mod
    from siglab.cli import operator as _operator_mod
    from siglab.cli import sodex as _sodex_mod
    from siglab.cli import evidence as _evidence_mod
    _demo_mod.add_subparser(subparsers)
    _market_mod.add_subparser(subparsers)
    _market_mod.telemetry_add_subparser(subparsers)
    _dashboard_mod.add_subparser(subparsers)
    _sodex_mod.add_subparser(subparsers)
    _operator_mod.add_subparser(subparsers)
    _evidence_mod.add_subparser(subparsers)
    args = parser.parse_args()
    from siglab.cli.rich_utils import init_console

    init_console(force_no_color=getattr(args, "no_color", False))
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
        _market_mod.telemetry_run_command(args)
        return
    if args.command == "dashboard":
        _dashboard_mod.run_dashboard(args)
        return
    if args.command == "dashboard-start":
        _dashboard_mod.run_dashboard_start(args)
        return
    if args.command == "sodex-preflight":
        _sodex_mod.run_sodex_preflight(args)
        return
    if args.command == "operator":
        asyncio.run(_operator_mod.run_operator(args))
    if args.command == "evidence-build":
        _evidence_mod.run_evidence_build(args)
        return


