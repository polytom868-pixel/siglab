"""Paper trading subcommands: paper-start, paper-status, paper-promote."""

from __future__ import annotations

import argparse
import asyncio

from siglab.cli.rich_utils import print_error, print_json
from siglab.config import load_settings
from siglab.data.store import ParquetLake
from siglab.data.sodex_feeds import SoDEXFeeds
from siglab.live.paper_client import PaperClientError, SoDEXPaperPerpsClient


def _make_paper_client(args: argparse.Namespace) -> SoDEXPaperPerpsClient:
    """Shared construction of a SoDEXPaperPerpsClient from CLI args."""
    settings = load_settings()
    sessions_dir = args.sessions_dir or str(settings.root_dir / "sessions")
    lake = ParquetLake(settings.root_dir / "data" / "cache")
    feeds = SoDEXFeeds(lake=lake)
    return SoDEXPaperPerpsClient(feeds=feeds, sessions_dir=sessions_dir)


def add_subparser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    # paper-start
    start_parser = subparsers.add_parser(
        "paper-start",
        help="Create a new paper trading session.",
    )
    start_parser.add_argument("--session", default=None, help="Optional label for the session.")
    start_parser.add_argument("--sessions-dir", default=None, help="Directory for .npy session files.")

    # paper-status
    status_parser = subparsers.add_parser(
        "paper-status",
        help="Show paper trading session status.",
    )
    status_parser.add_argument("--session", required=True, help="Session ID.")
    status_parser.add_argument("--sessions-dir", default=None, help="Directory for .npy session files.")

    # paper-promote
    promote_parser = subparsers.add_parser(
        "paper-promote",
        help="Check paper session promotion eligibility and promote if eligible.",
    )
    promote_parser.add_argument("--session", required=True, help="Session ID.")
    promote_parser.add_argument("--sessions-dir", default=None, help="Directory for .npy session files.")
    promote_parser.add_argument("--threshold", type=float, default=None, help="Promotion score threshold.")
    promote_parser.add_argument("--consecutive-days", type=int, default=None, help="Required consecutive days above threshold.")
    promote_parser.add_argument("--min-trading-days", type=int, default=None, help="Minimum trading days required.")


async def run_paper_start(args: argparse.Namespace) -> None:
    """Create a new paper trading session."""
    client = _make_paper_client(args)
    session_id = client.create_session(name=args.session)
    print_json({"session_id": session_id, "name": args.session or session_id})


async def run_paper_status(args: argparse.Namespace) -> None:
    """Show paper trading session status."""
    client = _make_paper_client(args)
    try:
        # Process open orders against latest klines
        try:
            open_orders = client.get_orders(args.session, status="OPEN") if hasattr(client, 'get_orders') else []
            open_symbols = {o["symbol"] for o in open_orders}
            settings = load_settings()
            lake = ParquetLake(settings.root_dir / "data" / "cache")
            feeds = SoDEXFeeds(lake=lake)
            results = await asyncio.gather(*(feeds.fetch_klines(sym, "1m", limit=5) for sym in open_symbols), return_exceptions=True)
            for klines in results:
                if isinstance(klines, BaseException):
                    continue
                if not klines.empty:
                    try:
                        await client.process_klines(args.session, klines)
                    except Exception:
                        pass
        except Exception:
            pass

        status = client.get_session_status(args.session)

        # Add mark prices for unrealized PnL
        try:
            mark_prices = await client.get_mark_prices()
            status["mark_prices"] = mark_prices
        except Exception:
            status["mark_prices"] = {}

        print_json(status)
    except PaperClientError as exc:
        print_error(str(exc))
        raise SystemExit(1)


async def run_paper_promote(args: argparse.Namespace) -> None:
    """Check paper session promotion eligibility and promote if eligible."""
    from siglab.live.promotion import (
        compute_composite_score,
        compute_sub_scores,
        extract_session_metrics,
        extract_daily_metrics,
        promotion_eligible,
        DEFAULT_PROMOTION_THRESHOLD,
        DEFAULT_CONSECUTIVE_DAYS,
        DEFAULT_MIN_TRADING_DAYS,
    )

    client = _make_paper_client(args)

    try:
        metrics = extract_session_metrics(client, args.session)
        daily_metrics = extract_daily_metrics(client, args.session)

        threshold = args.threshold or DEFAULT_PROMOTION_THRESHOLD
        consecutive_days = args.consecutive_days or DEFAULT_CONSECUTIVE_DAYS
        min_trading_days = args.min_trading_days or DEFAULT_MIN_TRADING_DAYS

        sub_scores = {
            k: round(v, 4) for k, v in compute_sub_scores(metrics).items()
        }
        composite = compute_composite_score(metrics)

        eligible, reason = promotion_eligible(
            daily_metrics,
            threshold=threshold,
            consecutive_days=consecutive_days,
            min_trading_days=min_trading_days,
        )

        result = {
            "promoted": eligible,
            "reason": reason,
            "composite_score": round(composite, 4),
            "sub_scores": sub_scores,
            "trade_count": metrics.get("trade_count", 0),
            "trading_days": len(daily_metrics),
            "threshold": threshold,
            "consecutive_days_required": consecutive_days,
            "min_trading_days_required": min_trading_days,
        }

        print_json(result)

        if not eligible:
            raise SystemExit(1)

    except PaperClientError as exc:
        result = {
            "promoted": False,
            "reason": str(exc),
            "composite_score": 0.0,
            "sub_scores": {},
            "trade_count": 0,
            "trading_days": 0,
            "threshold": args.threshold or DEFAULT_PROMOTION_THRESHOLD,
            "consecutive_days_required": args.consecutive_days or DEFAULT_CONSECUTIVE_DAYS,
            "min_trading_days_required": args.min_trading_days or DEFAULT_MIN_TRADING_DAYS,
        }
        print_json(result)
        raise SystemExit(1)
