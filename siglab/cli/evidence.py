"""evidence-build subcommand: fetch SoSoValue + SoDEX evidence and write JSONL files."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from siglab.config import SiglabConfig, load_settings
from siglab.data.evidence import (
    EvidenceStore,
    etf_inflow_evidence,
    news_evidence,
    sodex_quote_evidence,
)
from siglab.data.feeds import SoDEXPublicPerpsClient, SoSoValueClient
from siglab.utils import resolve_path_from_root


def add_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser(
        "evidence-build",
        help="Fetch SoSoValue ETF + news and SoDEX ticker evidence, write JSONL files.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory for evidence JSONL files (default: <root>/runs/evidence)",
    )


def run_evidence_build(args: argparse.Namespace) -> None:
    asyncio.run(_async_evidence_build(args))


async def _collect_evidence(
    settings: SiglabConfig, output_dir: Path
) -> dict[str, Any]:
    """Shared evidence collection: cleanup + fetch SoSoValue + SoDEX.

    Returns dict with keys: observed_at, ssv_path, sodex_path, errors, written_paths.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    for p in output_dir.glob("sosovalue_evidence*.jsonl"):
        p.unlink()
    for p in output_dir.glob("sodex_rest_evidence*.jsonl"):
        p.unlink()
    for p in output_dir.glob("sodex_ws_evidence.jsonl"):
        p.unlink()
    for name in ("sosovalue.jsonl", "sodex_rest.jsonl", "sodex_ws.jsonl"):
        p = output_dir / name
        if p.exists():
            p.unlink()
    observed_at = datetime.now(UTC).isoformat()
    errors: list[str] = []
    written_paths: list[Path] = []
    ssv_path = output_dir / "sosovalue.jsonl"
    # --- SoSoValue evidence ---
    ssv_store = EvidenceStore(ssv_path)
    if settings.sosovalue_api_key_override:
        try:
            ssv_client = SoSoValueClient(api_key=settings.sosovalue_api_key_override)
            etf_rows, news_rows = await asyncio.gather(
                ssv_client.etf_historical_inflow(),
                ssv_client.featured_news_by_currency(page_size=10),
                return_exceptions=True,
            )
            if isinstance(etf_rows, list):
                ssv_store.append_many(
                    etf_inflow_evidence(
                        etf_rows,
                        etf_type="us-btc-spot",
                        observed_at=observed_at,
                        evidence_path=str(ssv_path),
                    )
                )
            if isinstance(news_rows, list):
                ssv_store.append_many(
                    news_evidence(
                        news_rows,
                        observed_at=observed_at,
                        evidence_path=str(ssv_path),
                    )
                )
            await ssv_client.close()
            written_paths.append(ssv_path)
        except Exception as exc:
            errors.append(f"SoSoValue evidence failed: {exc}")
    # --- SoDEX evidence ---
    sodex_path = output_dir / "sodex_rest.jsonl"
    sodex_store = EvidenceStore(sodex_path)
    try:
        sdx_client = SoDEXPublicPerpsClient()
        sodex_records = await sodex_quote_evidence(
            sdx_client,
            observed_at=observed_at,
            evidence_path=str(sodex_path),
        )
        sodex_store.append_many(sodex_records)
        await sdx_client.close()
        written_paths.append(sodex_path)
    except Exception as exc:
        errors.append(f"SoDEX evidence failed: {exc}")
    return {
        "observed_at": observed_at,
        "ssv_path": ssv_path,
        "sodex_path": sodex_path,
        "errors": errors,
        "written_paths": written_paths,
    }


async def _async_evidence_build(args: argparse.Namespace) -> None:
    settings = load_settings()
    output_dir = (
        resolve_path_from_root(args.output_dir, root_dir=settings.root_dir)
        if args.output_dir
        else settings.root_dir / "runs" / "evidence"
    )
    result = await _collect_evidence(settings, output_dir)
    print(
        json.dumps(
            {
                "observed_at": result["observed_at"],
                "evidence_files": [str(p) for p in result["written_paths"]],
                "errors": result["errors"],
            },
            indent=2,
            default=str,
        )
    )
    if result["errors"]:
        sys.exit(1)


