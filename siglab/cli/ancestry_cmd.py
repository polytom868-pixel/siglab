"""Ancestry subcommands: ancestry, clear-passed."""

from __future__ import annotations

import argparse

from siglab.cli.rich_utils import get_console, make_table, print_json, status_style
from siglab.cli.helpers import add_json_flag, maybe_print_json
from siglab.config import load_settings
from siglab.search import LineageStore
from siglab.track_registry import TRACK_CLI_CHOICES, resolve_track


def add_subparser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    # ancestry
    ancestry_parser = subparsers.add_parser("ancestry")
    ancestry_parser.add_argument(
        "--track",
        choices=TRACK_CLI_CHOICES,
        default=None,
    )
    ancestry_parser.add_argument("--limit", type=int, default=10)
    add_json_flag(ancestry_parser)

    # clear-passed
    clear_parser = subparsers.add_parser("clear-passed")
    clear_parser.add_argument(
        "--track",
        choices=["all", *TRACK_CLI_CHOICES],
        default="all",
    )


def run_ancestry(args: argparse.Namespace) -> None:
    settings = load_settings()
    ancestry = LineageStore(settings.ancestry_db_path)
    rows = ancestry.list_rows(
        track=resolve_track(args.track),
        limit=args.limit,
    )
    if args.as_json:
        maybe_print_json(rows, as_json=True)
        return
    from rich.text import Text
    table = make_table(title="Ancestry")
    table.add_column("Created", style="muted")
    table.add_column("Track")
    table.add_column("Family")
    table.add_column("Spec Hash", style="accent")
    table.add_column("Score", justify="right")
    table.add_column("Passed")
    table.add_column("Deployed")
    for row in rows:
        table.add_row(
            str(row["created_at"]),
            str(row["track"]),
            str(row["family"]),
            str(row["spec_hash"]),
            f"{row['aggregate_score']:.4f}",
            Text(str(row["passed"]), style=status_style(row["passed"])),
            Text(str(row["deployd"]), style=status_style(row["deployd"])),
        )
    get_console().print(table)


def run_clear_passed(args: argparse.Namespace) -> None:
    settings = load_settings()
    ancestry = LineageStore(settings.ancestry_db_path)
    tracks = (
        list(settings.tracks)
        if args.track == "all"
        else [resolve_track(args.track)]
    )
    removed = 0
    for track in tracks:
        rows = ancestry.dashboard_rows(track=track)
        for row in rows:
            if row.get("passed") and not row.get("deployd"):
                ancestry.clear_spec(str(row.get("spec_hash") or ""))
                removed += 1
    payload = {
        "track": args.track,
        "tracks_cleared": len(tracks),
        "passed_specs_removed": removed,
    }
    print_json(payload)
