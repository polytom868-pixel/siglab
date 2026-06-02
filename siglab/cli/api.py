"""API surface subcommand: summarize SoSoValue/SoDEX API surface maps."""

from __future__ import annotations

import argparse
import json
from typing import Any

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
        print(json.dumps(report, indent=2, sort_keys=True))
        return
    for name, payload in report.items():
        print(
            f"{name}: exists={payload['exists']} lines={payload['line_count']} "
            f"paths={payload['endpoint_path_mentions']} supported={payload['supported_mentions']} "
            f"missing={payload['missing_mentions']} blocked={payload['blocked_mentions']} "
            f"file={payload['path']}"
        )
