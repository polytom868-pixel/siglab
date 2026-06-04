"""Benchmark subcommands: benchmark-init, benchmark-eval, benchmark-status."""

from __future__ import annotations

import argparse

from siglab.benchmark import (
    DEFAULT_BENCHMARK_DECK,
    benchmark_status as benchmark_status_payload,
    evaluate_benchmark_deck,
    init_benchmark_deck,
    supported_deck_names,
)
from siglab.cli.helpers import require_sosovalue_config
from siglab.config import load_settings
from siglab.data import MarketDataProvider, ParquetLake
from siglab.evaluator import ResearchEvaluator
from siglab.llm import ClaudeClient
from siglab.search import LineageStore, SpecMutator


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
    settings = load_settings()
    settings.ensure_runtime_directories()
    ancestry = LineageStore(settings.ancestry_db_path)
    claude = ClaudeClient(settings)
    mutator = SpecMutator(settings, claude)
    payload = init_benchmark_deck(
        settings=settings,
        ancestry=ancestry,
        mutator=mutator,
        deck_name=str(args.deck),
        runner_label=str(
            getattr(args, "agent_label", None)
            or getattr(args, "runner_label", None)
            or "external_agent"
        ),
        run_label=args.run_label,
        force=bool(args.force),
    )
    from siglab.cli.rich_utils import print_json
    print_json(payload)


async def run_benchmark_eval(args: argparse.Namespace) -> None:
    settings = load_settings()
    require_sosovalue_config(settings)
    settings.ensure_runtime_directories()
    lake = ParquetLake(settings.data_lake_dir)
    provider = MarketDataProvider(settings, lake)
    ancestry = LineageStore(settings.ancestry_db_path)
    claude = ClaudeClient(settings)
    mutator = SpecMutator(settings, claude)
    evaluator = ResearchEvaluator(settings, provider)
    try:
        payload = await evaluate_benchmark_deck(
            settings=settings,
            ancestry=ancestry,
            mutator=mutator,
            evaluator=evaluator,
            provider=provider,
            deck_name=str(args.deck),
        )
    finally:
        await provider.close()
    from siglab.cli.rich_utils import print_json
    print_json(payload)


def run_benchmark_status(args: argparse.Namespace) -> None:
    settings = load_settings()
    payload = benchmark_status_payload(
        settings=settings,
        deck_name=str(args.deck),
    )
    from siglab.cli.rich_utils import print_json
    print_json(payload)
