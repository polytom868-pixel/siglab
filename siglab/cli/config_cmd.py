"""Config subcommand: validate config.json and environment settings."""

from __future__ import annotations
from typing import Any, cast

import argparse
import json

from siglab.config import load_settings


def add_subparser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    config_parser = subparsers.add_parser(
        "config",
        help="Configuration inspection and validation commands.",
    )
    config_sub = config_parser.add_subparsers(dest="config_command", required=True)
    config_sub.add_parser(
        "validate",
        help="Validate config.json and environment settings.",
    )


def run_command(args: argparse.Namespace) -> None:
    if args.config_command == "validate":
        config_validate_command(args)


def config_validate_command(args: argparse.Namespace) -> None:
    """Validate config.json and environment settings."""
    settings = load_settings()
    config_path = settings.sosovalue_config_path
    errors: list[str] = []

    if not config_path.exists():
        errors.append(f"config file not found: {config_path}")
        _report_config_validation(errors)
        return

    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errors.append(f"config file is not valid JSON: {exc}")
        _report_config_validation(errors)
        return

    if not isinstance(raw, dict):
        errors.append("config root must be a JSON object")
        _report_config_validation(errors)
        return

    system = raw.get("system")
    if system is None:
        errors.append("missing required field: system")
    elif not isinstance(system, dict):
        errors.append("system must be a JSON object")
    else:
        if not system.get("api_key"):
            errors.append("missing required field: system.api_key")
        if not system.get("api_base_url"):
            errors.append("missing required field: system.api_base_url")

    if errors:
        _report_config_validation(errors)
        return

    from siglab.cli.rich_utils import print_key_value_pairs, print_success
    print_success(f"config valid: {config_path}")
    print_key_value_pairs(
        title="Config Details",
        pairs=[
            ("api_base_url", str(cast(dict[str, Any], system).get("api_base_url")), ""),
        ],
    )
    raise SystemExit(0)


def _report_config_validation(errors: list[str]) -> None:
    from siglab.cli.rich_utils import print_error
    for error in errors:
        print_error(error)
    raise SystemExit(1)
