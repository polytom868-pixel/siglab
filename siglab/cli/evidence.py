"""evidence-build subcommand: fetch SoSoValue + SoDEX evidence and write JSONL files."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from siglab.config import load_settings
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


async def _async_evidence_build(args: argparse.Namespace) -> None:
    settings = load_settings()
    output_dir = (
        resolve_path_from_root(args.output_dir, root_dir=settings.root_dir)
        if args.output_dir
        else settings.root_dir / "runs" / "evidence"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    observed_at = datetime.now(UTC).isoformat()

    errors: list[str] = []
    written_paths: list[Path] = []

    # --- SoSoValue evidence ---
    ssv_path = output_dir / f"sosovalue_evidence_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.jsonl"
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
                etf_records = etf_inflow_evidence(
                    etf_rows,
                    etf_type="us-btc-spot",
                    observed_at=observed_at,
                    evidence_path=str(ssv_path),
                )
                ssv_store.append_many(etf_records)

            if isinstance(news_rows, list):
                news_records = news_evidence(
                    news_rows,
                    observed_at=observed_at,
                    evidence_path=str(ssv_path),
                )
                ssv_store.append_many(news_records)

            await ssv_client.close()
            written_paths.append(ssv_path)
        except Exception as exc:
            errors.append(f"SoSoValue evidence failed: {exc}")

    # --- SoDEX evidence ---
    sodex_path = output_dir / f"sodex_rest_evidence_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.jsonl"
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

    # --- output summary ---
    result: dict[str, Any] = {
        "observed_at": observed_at,
        "evidence_files": [str(p) for p in written_paths],
        "errors": errors,
    }
    print(json.dumps(result, indent=2, default=str))
    if errors:
        sys.exit(1)
