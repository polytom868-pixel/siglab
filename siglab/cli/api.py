"""API surface subcommand: summarize SoSoValue/SoDEX API surface maps."""

from __future__ import annotations

import argparse
from typing import Any

from siglab.cli.rich_utils import get_console, make_table, print_json, status_style
from siglab.config import load_settings


def add_subparser(subparsers) -> None:
    parser = subparsers.add_parser(
        "api-surface",
        help="Summarize source-of-truth SoSoValue/SoDEX API surface maps.",
    )
    parser.add_argument("--json", action="store_true")


def run_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    docs_dir = settings.root_dir / "docs"
    files = {
        "sosovalue": docs_dir / "sosovalue-api-surface.yaml",
        "sodex": docs_dir / "sodex-api-surface.yaml",
        "ecosystem": docs_dir / "sosovalue-ecosystem-surface.yaml",
        "buildathon": docs_dir / "buildathon-readiness-audit.md",
    }
    report: dict[str, Any] = {}
    for name, path in files.items():
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        report[name] = {
            "path": str(path),
            "exists": path.exists(),
            "line_count": len(text.splitlines()) if text else 0,
            "endpoint_path_mentions": text.count("path:"),
            "supported_mentions": text.count("supported"),
            "missing_mentions": text.count("missing"),
            "blocked_mentions": text.count("blocked"),
        }
    if getattr(args, "json", False):
        print_json(report)
        return
    from rich.text import Text
    table = make_table(title="API Surface")
    table.add_column("Surface", style="label")
    table.add_column("Exists")
    table.add_column("Lines", justify="right")
    table.add_column("Paths", justify="right")
    table.add_column("Supported", justify="right")
    table.add_column("Missing", justify="right")
    table.add_column("Blocked", justify="right")
    for name, payload in report.items():
        table.add_row(
            name,
            Text(str(payload["exists"]), style=status_style(payload["exists"])),
            str(payload["line_count"]),
            str(payload["endpoint_path_mentions"]),
            str(payload["supported_mentions"]),
            str(payload["missing_mentions"]),
            str(payload["blocked_mentions"]),
        )
    get_console().print(table)
