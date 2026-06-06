"""Profile subcommand: run the SigLab hardening profile."""

from __future__ import annotations

import argparse

from siglab.cli.rich_utils import print_json, print_panel
from siglab.config import load_settings
from siglab.hardening_profile import build_profile, profile_as_text, strict_failure_count


def add_subparser(subparsers) -> None:
    parser = subparsers.add_parser(
        "profile",
        help="Run the strict SigLab hardening profile.",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true")


def run_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    profile = build_profile(settings.root_dir)
    if getattr(args, "json", False):
        print_json(profile)
    else:
        print_panel(profile_as_text(profile), title="Hardening Profile", border_style="info")
    if getattr(args, "strict", False):
        failures = strict_failure_count(profile)
        if failures:
            raise SystemExit(min(failures, 125))
