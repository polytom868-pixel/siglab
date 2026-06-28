from __future__ import annotations
import argparse
import asyncio
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

BTC_CURRENCY_ID = 1


from siglab.config import SiglabConfig, load_settings
from siglab.data.evidence import (
    EvidenceRecord,
    EvidenceStore,
    _coerce_float,
    etf_inflow_evidence,
    news_evidence,
    sodex_quote_evidence,
)
from siglab.data.feeds import SoDEXPublicPerpsClient
from siglab.data.sosovalue_client import SoSoValueClient, SoSoValueEndpoints
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
            elif isinstance(news_rows, Exception):
                logger.warning("News evidence fetch failed: %s", news_rows)
            await ssv_client.close()
            written_paths.append(ssv_path)
        except Exception as exc:
            errors.append(f"SoSoValue evidence failed: {exc}")
    sodex_path = output_dir / "sodex_rest.jsonl"
    sodex_store = EvidenceStore(sodex_path)
    sodex_succeeded = False
    try:
        sdx_client = SoDEXPublicPerpsClient()
        sodex_records = await sodex_quote_evidence(
            sdx_client,
            observed_at=observed_at,
            evidence_path=str(sodex_path),
        )
        await sdx_client.close()
        if sodex_records:
            sodex_store.append_many(sodex_records)
            written_paths.append(sodex_path)
            sodex_succeeded = True
        else:
            logger.warning("SoDEX returned 0 records, falling back to SoSoValue market snapshot")
    except Exception as exc:
        errors.append(f"SoDEX evidence failed: {exc}")
        logger.warning("SoDEX failed (%s), falling back to SoSoValue market snapshot", exc)
    if not sodex_succeeded:
        try:
            ssv_fallback_path = output_dir / "sodex_fallback.jsonl"
            ssv_fallback_store = EvidenceStore(ssv_fallback_path)
            ssv_client = SoSoValueClient(api_key=settings.sosovalue_api_key_override)
            snapshot = await ssv_client.currency_market_snapshot(BTC_CURRENCY_ID)
            await ssv_client.close()
            data = snapshot.get("data", snapshot) if isinstance(snapshot, dict) else snapshot
            if isinstance(data, dict) and data.get("price") is not None:
                entity = str(data.get("symbol", "BTC")).upper()
                price = _coerce_float(data.get("price"))
                price_change = _coerce_float(data.get("priceChangePercent24Hr") or data.get("priceChangePercent"))
                market_cap = _coerce_float(data.get("marketCap") or data.get("market_cap"))
                volume = _coerce_float(data.get("volume24Hr") or data.get("volume"))
                records: list[EvidenceRecord] = []
                common = dict(
                    source="sosovalue.currency.market_snapshot",
                    observed_at=observed_at,
                    timestamp=observed_at,
                    entity=entity,
                    module="SoSoValue",
                    confidence=0.8,
                    evidence_path=str(ssv_fallback_path),
                    attributes={"symbol": entity},
                )
                if price is not None:
                    records.append(EvidenceRecord(**common, relation="mark_price", value=price))
                if price_change is not None:
                    records.append(EvidenceRecord(**common, relation="price_change_24h_pct", value=price_change))
                if market_cap is not None:
                    records.append(EvidenceRecord(**common, relation="market_cap", value=market_cap))
                if volume is not None:
                    records.append(EvidenceRecord(**common, relation="volume_24h", value=volume))
                ssv_fallback_store.append_many(records)
                written_paths.append(ssv_fallback_path)
        except Exception as exc:
            errors.append(f"SoDEX fallback (SoSoValue) failed: {exc}")
    # Stale-data fallback: if all sources failed, load last-good evidence
    if errors and not written_paths:
        last_good_dir = output_dir / ".last_good"
        for name in ("sosovalue.jsonl", "sodex_rest.jsonl"):
            last_good = last_good_dir / name
            if last_good.exists():
                target = output_dir / name
                import shutil
                shutil.copy2(str(last_good), str(target))
                written_paths.append(target)
                logger.warning("stale_data_fallback loaded last-good %s -> %s", last_good, target)
    # Preserve current successful results as last-good for future fallback
    if written_paths:
        last_good_dir = output_dir / ".last_good"
        last_good_dir.mkdir(parents=True, exist_ok=True)
        import shutil
        for p in written_paths:
            if p.exists() and p.suffix == ".jsonl":
                dest = last_good_dir / p.name
                shutil.copy2(str(p), str(dest))
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


