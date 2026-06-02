"""Ancestry subcommands: ancestry, clear-passed."""

from __future__ import annotations

import argparse
import json

from siglab.config import load_settings
from siglab.search import LineageStore
from siglab.track_registry import TRACK_CLI_CHOICES, canonical_track_name


def add_subparser(subparsers) -> None:
    # ancestry
    ancestry_parser = subparsers.add_parser("ancestry")
    ancestry_parser.add_argument(
        "--track",
        choices=TRACK_CLI_CHOICES,
        default=None,
    )
    ancestry_parser.add_argument("--limit", type=int, default=10)

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
        track=canonical_track_name(args.track) or args.track,
        limit=args.limit,
    )
    for row in rows:
        print(
            f"{row['created_at']} {row['track']} {row['family']} "
            f"{row['spec_hash']} score={row['aggregate_score']:.4f} "
            f"passed={row['passed']} deployd={row['deployd']}"
        )


def run_clear_passed(args: argparse.Namespace) -> None:
    settings = load_settings()
    ancestry = LineageStore(settings.ancestry_db_path)
    tracks = (
        list(settings.tracks)
        if args.track == "all"
        else [canonical_track_name(args.track) or args.track]
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
    print(json.dumps(payload, indent=2))
