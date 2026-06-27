"""Evidence subcommands: build and map evidence."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from datetime import UTC, datetime

from siglab.cli.helpers import (
    display_paths,
    require_sosovalue_config,
    sosovalue_currency_id,
)
from siglab.cli.rich_utils import print_json, print_success
from siglab.config import load_settings
from siglab.data.feeds import SoSoValueClient, SoSoValueEndpoints
from siglab.data.evidence import write_evidence_graph_html


def add_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser(
        "evidence-build",
        help="Build a source-backed SoSoValue evidence JSONL from implemented verified surfaces.",
    )
    parser.add_argument("--etf-type", default="us-btc-spot")
    parser.add_argument("--currency", default="BTC")
    parser.add_argument("--news-page-size", type=int, default=10)
    parser.add_argument("--news-pages", type=int, default=1)
    parser.add_argument("--output", default=None)
    parser.add_argument("--summary-output", default=None)
    parser.add_argument("--summary-top-links", type=int, default=10)
    parser.add_argument("--json", action="store_true")
    map_parser = subparsers.add_parser(
        "evidence-map",
        help="Render an HTML evidence graph from an evidence summary artifact.",
    )
    map_parser.add_argument("--summary", default=None)
    map_parser.add_argument("--evidence", default=None)
    map_parser.add_argument("--output", default=None)
    map_parser.add_argument("--json", action="store_true")


async def run_evidence_build(args: argparse.Namespace) -> None:
    settings = load_settings()
    require_sosovalue_config(settings)
    observed_at = datetime.now(UTC).isoformat()
    output = (
        resolve_path_from_root(args.output, root_dir=settings.root_dir)
        if args.output
        else settings.artifact_dir / "evidence" / "sosovalue_evidence.jsonl"
    )
    client = SoSoValueClient(
        api_key=settings.sosovalue_api_key_override,
        endpoints=SoSoValueEndpoints(
            openapi_base_url=settings.sosovalue_base_url,
            etf_base_url=settings.etf_base_url,
            news_base_url=settings.news_base_url,
        ),
        timeout_s=settings.sosovalue_timeout_s,
        retries=settings.sosovalue_retries,
    )
    try:
        currencies = await client.listed_currencies()
        currency_id = sosovalue_currency_id(currencies, str(args.currency))
        etf_rows, news_rows, currency_news_rows = await asyncio.gather(
            client.etf_historical_inflow(etf_type=str(args.etf_type)),
            client.featured_news_pages(
                max_pages=int(args.news_pages),
                page_size=int(args.news_page_size),
            ),
            client.featured_news_by_currency_pages(
                max_pages=int(args.news_pages),
                page_size=int(args.news_page_size),
                currency_id=currency_id,
            )
            if currency_id is not None
            else asyncio.sleep(0, result=[]),
        )
    finally:
        await client.close()
    records = [
        *etf_inflow_evidence(
            etf_rows,
            etf_type=str(args.etf_type),
            observed_at=observed_at,
            evidence_path=f"sosovalue/etf/{args.etf_type}",
        ),
        *news_evidence(
            news_rows,
            observed_at=observed_at,
            evidence_path="sosovalue/news/featured",
        ),
        *news_evidence(
            currency_news_rows,
            observed_at=observed_at,
            evidence_path=f"sosovalue/news/featured/currency/{args.currency}",
            default_entity=str(args.currency).upper(),
            source="sosovalue.featured_news_by_currency",
        ),
    ]
    source_counts = Counter(record.source for record in records)
    store = EvidenceStore(output)
    appended = store.append_many(records)
    links = store.linked_relations(max_day_gap=1)
    summary_output = (
        resolve_path_from_root(args.summary_output, root_dir=settings.root_dir)
        if args.summary_output
        else output.with_suffix(".summary.json")
    )
    summary = store.write_summary(
        summary_output,
        max_day_gap=1,
        top_links=int(args.summary_top_links),
    )
    print_json(
        {
            "output": display_paths([output], root_dir=settings.root_dir)[0],
            "summary_output": display_paths(
                [summary_output],
                root_dir=settings.root_dir,
            )[0],
            "records_appended": appended,
            "cross_module_links": len(links),
            "currency": str(args.currency).upper(),
            "currency_id": currency_id,
            "link_relations": sorted({str(link.get("relation")) for link in links}),
            "modules": sorted({record.module for record in records}),
            "relations": sorted({record.relation for record in records}),
            "source_counts": dict(sorted(source_counts.items())),
            "summary_record_count": summary["record_count"],
            "summary_top_links": len(summary["top_links"]),
            "append_stats": dict(store.last_append_stats),
            "observed_at": observed_at,
        },
    )


def run_evidence_map(args: argparse.Namespace) -> None:
    settings = load_settings()
    if args.evidence:
        evidence_path = resolve_path_from_root(
            args.evidence,
            root_dir=settings.root_dir,
        )
        store = EvidenceStore(evidence_path)
        summary_path = evidence_path.with_suffix(".summary.json")
        store.write_summary(summary_path)
    elif args.summary:
        summary_path = resolve_path_from_root(args.summary, root_dir=settings.root_dir)
    else:
        candidates = sorted(
            (settings.root_dir / "runs" / "evidence").glob("*.summary.json"),
            key=lambda item: item.stat().st_mtime,
        )
        if not candidates:
            print(
                "No evidence summary found. Run `siglab evidence-build` first or pass --summary.",
                file=sys.stderr,
            )
            raise SystemExit(1)
        summary_path = candidates[-1]
    output_path = (
        resolve_path_from_root(args.output, root_dir=settings.root_dir)
        if args.output
        else settings.root_dir / "runs" / "evidence" / "evidence_graph.html"
    )
    rendered = write_evidence_graph_html(summary_path, output_path)
    payload = {"summary": str(summary_path), "output": str(rendered)}
    if getattr(args, "json", False):
        print_json(payload)
        return
    print_success(f"wrote evidence graph: {rendered}")
