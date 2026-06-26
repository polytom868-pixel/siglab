"""Operator subcommand: run the OperatorPipeline from the CLI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from siglab.cli.rich_utils import print_error, print_json
from siglab.config import load_settings
from siglab.data.sodex_feeds import SoDEXFeeds
from siglab.data.store import ParquetLake
from siglab.live.paper_client import SoDEXPaperPerpsClient
from siglab.operator import OperatorPipeline


def add_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser(
        "operator", help="Run the OperatorPipeline — evidence-to-decision cycle.",
    )
    parser.add_argument(
        "--spec",
        default=None,
        help="Path to a JSON spec file (optional; inline via --evidence otherwise).",
    )
    parser.add_argument(
        "--session",
        default=None,
        help="Paper-trading session ID (optional; auto-created when omitted).",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Disable dry-run (requires SIGLAB_LIVE_ENABLED=1).",
    )
    parser.add_argument(
        "--sessions-dir", default=None, help="Directory for paper session files.",
    )


async def run_operator(args: argparse.Namespace) -> None:
    settings = load_settings()
    sessions_dir = args.sessions_dir or str(settings.root_dir / "sessions")
    dry_run = not args.live
    lake = ParquetLake(settings.root_dir / "data" / "cache")
    feeds = SoDEXFeeds(lake=lake)
    paper_client = SoDEXPaperPerpsClient(feeds=feeds, sessions_dir=sessions_dir)
    pipeline = OperatorPipeline(dry_run=dry_run, paper_client=paper_client)
    spec: dict[str, Any] = {}
    if args.spec:
        spec_path = Path(str(args.spec))
        if spec_path.exists():
            spec = dict(json.loads(spec_path.read_text()))
        else:
            print_error(f"Spec file not found: {args.spec}")
            raise SystemExit(1)
    market_data: dict[str, Any] = {
        "portfolio_value": 100000.0,
        "price": 0.0,
        "allocation": {},
    }
    signal, position, risk = await pipeline.run_once(spec, market_data)
    result: dict[str, Any] = {
        "signal": {
            "direction": signal.direction,
            "symbol": signal.symbol,
            "confidence": signal.confidence,
            "size": signal.size,
            "reasoning": signal.reasoning,
            "timestamp": signal.timestamp,
        },
        "position": None,
        "risk_report": {
            "passed": risk.passed,
            "reasons": risk.reasons,
            "composite_score": risk.composite_score,
        },
    }
    if position is not None:
        result["position"] = {
            "symbol": position.symbol,
            "side": position.side,
            "quantity": position.quantity,
            "entry_price": position.entry_price,
            "timestamp": position.timestamp,
        }
    if args.session and position is not None:
        order = pipeline.position_to_paper(signal, session_id=args.session)
        result["order"] = order
    print_json(result)
