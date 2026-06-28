from __future__ import annotations

import argparse

from rich.text import Text

from siglab.cli.helpers import sodex_preflight_report
from siglab.cli.rich_utils import (
    get_console,
    make_table,
    print_json,
    status_style,
)



def add_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    preflight_parser = subparsers.add_parser("sodex-preflight")
    preflight_parser.add_argument("--json", action="store_true")


def run_sodex_preflight(args: argparse.Namespace) -> None:
    report = sodex_preflight_report()
    if getattr(args, "json", False):
        print_json(report)
        return
    table = make_table(title="SoDEX Preflight")
    table.add_column("Check", style="label", no_wrap=True)
    table.add_column("Status")
    table.add_row(
        "public_read_ready",
        Text(
            str(report["public_read_ready"]),
            style=status_style(report["public_read_ready"]),
        ),
    )
    table.add_row(
        "schema_pinned",
        Text(str(report["schema_pinned"]), style=status_style(report["schema_pinned"])),
    )
    table.add_row(
        "signed_path_ready",
        Text(
            str(report["signed_path"]["ready"]),
            style=status_style(report["signed_path"]["ready"]),
        ),
    )
    table.add_row("environment", Text(report["signed_path"]["environment"]))
    if report["signed_path"]["missing_prerequisites"]:
        table.add_row(
            "missing_prerequisites",
            Text(
                ", ".join(report["signed_path"]["missing_prerequisites"]),
                style="warning",
            ),
        )
    table.add_row(
        "live_write_allowed",
        Text(
            str(report["live_write_allowed"]),
            style=status_style(report["live_write_allowed"]),
        ),
    )
    get_console().print(table)


