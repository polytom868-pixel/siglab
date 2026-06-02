"""Benchmark subcommands: benchmark-init, benchmark-eval, benchmark-status."""

from __future__ import annotations

import argparse
import json

from siglab.benchmark import (
    DEFAULT_BENCHMARK_DECK,
    benchmark_status as benchmark_status_payload,
    evaluate_benchmark_deck,
    init_benchmark_deck,
    supported_deck_names,
)
from siglab.config import load_settings


def add_subparser(subparsers) -> None:
    # benchmark-init
    init_parser = subparsers.add_parser("benchmark-init")
    init_parser.add_argument(
        "--deck",
        choices=supported_deck_names(),
        default=DEFAULT_BENCHMARK_DECK,
    )
    init_parser.add_argument("--agent-label", default="external_agent")
    init_parser.add_argument("--run-label", default=None)
    init_parser.add_argument("--force", action="store_true")

    # benchmark-eval
    eval_parser = subparsers.add_parser("benchmark-eval")
    eval_parser.add_argument(
        "--deck",
        choices=supported_deck_names(),
        default=DEFAULT_BENCHMARK_DECK,
    )

    # benchmark-status
    status_parser = subparsers.add_parser("benchmark-status")
    status_parser.add_argument(
        "--deck",
        choices=supported_deck_names(),
        default=DEFAULT_BENCHMARK_DECK,
    )


def run_benchmark_init(args: argparse.Namespace) -> None:
    init_benchmark_deck(
        deck_name=str(args.deck),
        agent_label=str(args.agent_label),
        run_label=args.run_label,
        force=bool(args.force),
    )
    print(f"benchmark deck initialized: {args.deck}")


async def run_benchmark_eval(args: argparse.Namespace) -> None:
    settings = load_settings()
    await evaluate_benchmark_deck(
        settings=settings,
        deck_name=str(args.deck),
    )
    print(f"benchmark evaluation complete: {args.deck}")


def run_benchmark_status(args: argparse.Namespace) -> None:
    settings = load_settings()
    payload = benchmark_status_payload(
        settings=settings,
        deck_name=str(args.deck),
    )
    print(json.dumps(payload, indent=2))
