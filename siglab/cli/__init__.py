"""SigLab CLI package — modular subcommand modules."""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys

_import_sigint = signal.signal(signal.SIGINT, lambda s, f: sys.exit(130))


def main() -> None:
    """Entry point: parse args, dispatch to subcommand handler."""
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
    from siglab.cli import market as _market_mod
    from siglab.cli import operator as _operator_mod
    from siglab.cli import paper as _paper_mod
    from siglab.cli import sodex as _sodex_mod

    _demo_mod.add_subparser(subparsers)
    _market_mod.add_subparser(subparsers)
    _dashboard_mod.add_subparser(subparsers)
    _sodex_mod.add_subparser(subparsers)
    _paper_mod.add_subparser(subparsers)
    _operator_mod.add_subparser(subparsers)
    tui_p = subparsers.add_parser("tui", help="Launch the SigLab Terminal UI.")
    tui_p.set_defaults(_handler="tui")
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
    if args.command == "dashboard":
        _dashboard_mod.run_dashboard(args)
        return
    if args.command == "dashboard-start":
        _dashboard_mod.run_dashboard_start(args)
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
    if args.command == "operator":
        asyncio.run(_operator_mod.run_operator(args))
        return


if __name__ == "__main__":
    main()
