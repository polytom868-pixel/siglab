"""Market report subcommand: build a deterministic evidence-linked market report."""

from __future__ import annotations

import argparse
import html
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from siglab.cli.helpers import (
    add_json_flag,
    float_or_none,
    latest_path,
    latest_record,
    read_jsonl_with_stats,
    sodex_preflight_report,
)
from siglab.cli.rich_utils import print_json
from siglab.config import load_settings
from siglab.path_utils import resolve_path_from_root


def add_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser(
        "market-report",
        help="Build a deterministic SoSoValue + SoDEX evidence-linked market report.",
    )
    parser.add_argument("--entity", default="BTC")
    parser.add_argument("--sosovalue-evidence", default=None)
    parser.add_argument("--sodex-evidence", default=None)
    parser.add_argument("--output", default=None)
    parser.add_argument("--html-output", default=None)
    add_json_flag(parser)


def run_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    evidence_dir = settings.root_dir / "runs" / "evidence"
    sosovalue_path = (
        resolve_path_from_root(args.sosovalue_evidence, root_dir=settings.root_dir)
        if args.sosovalue_evidence
        else latest_path(evidence_dir, "*sosovalue*.jsonl")
    )
    sodex_path = (
        resolve_path_from_root(args.sodex_evidence, root_dir=settings.root_dir)
        if args.sodex_evidence
        else evidence_dir / "sodex_ws_evidence.jsonl"
    )
    report = build_market_report(
        entity=str(args.entity or "BTC").upper(),
        sosovalue_evidence=sosovalue_path,
        sodex_evidence=sodex_path,
    )
    output = (
        resolve_path_from_root(args.output, root_dir=settings.root_dir)
        if args.output
        else settings.root_dir / "runs" / "market_report.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    html_output = (
        resolve_path_from_root(args.html_output, root_dir=settings.root_dir)
        if args.html_output
        else settings.root_dir / "runs" / "market_report.html"
    )
    html_output.parent.mkdir(parents=True, exist_ok=True)
    html_output.write_text(market_report_html(report), encoding="utf-8")
    payload = {
        "output": str(output),
        "html_output": str(html_output),
        "entity": report["entity"],
        "status": report["status"],
        "warnings": report["warnings"],
    }
    if getattr(args, "as_json", False):
        print_json(payload)
        return
    print_json(payload)


def build_market_report(
    *,
    entity: str,
    sosovalue_evidence: Path | None,
    sodex_evidence: Path | None,
) -> dict[str, Any]:
    soso_rows, soso_read_stats = read_jsonl_with_stats(sosovalue_evidence)
    sodex_rows, sodex_read_stats = read_jsonl_with_stats(sodex_evidence)
    entity_upper = entity.upper()
    etf_entity = f"us-{entity_upper.lower()}-spot"
    quote_entity = f"{entity_upper}-USD"
    latest_flow = latest_record(
        [
            row
            for row in soso_rows
            if row.get("entity") == etf_entity
            and row.get("relation") == "total_net_inflow"
        ],
        required_value="numeric",
    )
    latest_assets = latest_record(
        [
            row
            for row in soso_rows
            if row.get("entity") == etf_entity
            and row.get("relation") == "total_net_assets"
        ],
        required_value="numeric",
    )
    news_rows = [
        row
        for row in soso_rows
        if row.get("module") == "Feeds"
        and str(row.get("entity") or "").upper() in {entity_upper, "MARKET"}
    ]
    latest_news = sorted(
        [row for row in news_rows if str(row.get("value") or "").strip()],
        key=_record_sort_key_internal,
        reverse=True,
    )[:5]
    quote = latest_record(
        [
            row
            for row in sodex_rows
            if str(row.get("entity") or "").upper() == quote_entity
        ],
        required_value="quote",
    )
    preflight = sodex_preflight_report()
    warnings = [
        "Evidence links are temporal/contextual and are not causal claims.",
        "Signed SoDEX execution is refused unless preflight reports live_write_allowed=true.",
    ]
    missing: list[str] = []
    if latest_flow is None:
        missing.append("latest ETF flow evidence")
    if quote is None:
        missing.append("latest SoDEX quote evidence")
    if not latest_news:
        missing.append("recent feed evidence")
    status = "PARTIAL" if missing else "READY_FOR_OPERATOR_REVIEW"
    signal = _market_signal_summary(
        entity=entity_upper,
        latest_flow=latest_flow,
        latest_assets=latest_assets,
        quote=quote,
        latest_news=latest_news,
        preflight=preflight,
    )
    decision_support = _market_decision_support(
        entity=entity_upper,
        signal=signal,
        missing=missing,
        preflight=preflight,
    )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "entity": entity_upper,
        "status": status,
        "missing": missing,
        "signal_summary": signal,
        "decision_support": decision_support,
        "sosovalue": {
            "evidence_path": str(sosovalue_evidence) if sosovalue_evidence else None,
            "latest_flow": latest_flow,
            "latest_assets": latest_assets,
            "latest_news": latest_news,
        },
        "sodex": {
            "evidence_path": str(sodex_evidence) if sodex_evidence else None,
            "latest_quote": quote,
            "preflight": preflight,
        },
        "warnings": warnings,
        "evidence_selection": {
            "latest_valid_semantics": "parsed_timestamp_then_observed_at_skip_invalid_required_values",
            "sosovalue_rows_read": len(soso_rows),
            "sodex_rows_read": len(sodex_rows),
            "news_rows_considered": len(news_rows),
            "sosovalue_read_stats": soso_read_stats,
            "sodex_read_stats": sodex_read_stats,
        },
    }


def _market_signal_summary(
    *,
    entity: str,
    latest_flow: dict[str, Any] | None,
    latest_assets: dict[str, Any] | None,
    quote: dict[str, Any] | None,
    latest_news: list[dict[str, Any]],
    preflight: dict[str, Any],
) -> dict[str, Any]:
    flow_value = float_or_none((latest_flow or {}).get("value"))
    if flow_value is None:
        flow_bias = "unknown"
    elif flow_value > 0:
        flow_bias = "ETF inflow"
    elif flow_value < 0:
        flow_bias = "ETF outflow"
    else:
        flow_bias = "flat ETF flow"
    quote_attrs = dict((quote or {}).get("attributes") or {})
    bid = quote_attrs.get("bid") or (quote or {}).get("value")
    ask = quote_attrs.get("ask")
    return {
        "headline": f"{entity}: {flow_bias}; SoDEX quote bid={(bid if bid is not None else 'missing')} ask={(ask if ask is not None else 'missing')}; news_items={len(latest_news)}; live_write_allowed={bool(preflight.get('live_write_allowed'))}",
        "flow_direction": flow_bias,
        "flow_value": flow_value,
        "flow_timestamp": (latest_flow or {}).get("timestamp"),
        "net_assets": float_or_none((latest_assets or {}).get("value")),
        "quote_bid": bid,
        "quote_ask": ask,
        "news_titles": [str(row.get("value") or "")[:180] for row in latest_news],
        "operator_action": "review_only_signed_execution_blocked"
        if not preflight.get("live_write_allowed")
        else "review_before_any_live_order",
        "confidence": "medium" if latest_flow and quote else "low",
        "causality": "not_claimed",
    }


def _market_decision_support(
    *,
    entity: str,
    signal: dict[str, Any],
    missing: list[str],
    preflight: dict[str, Any],
) -> dict[str, Any]:
    quote_bid = signal.get("quote_bid")
    quote_ask = signal.get("quote_ask")
    flow_direction = str(signal.get("flow_direction") or "unknown")
    evidence_complete = not missing
    if not evidence_complete:
        stance = "NO_ACTION_INCOMPLETE_EVIDENCE"
    elif (
        flow_direction in {"ETF inflow", "ETF outflow"}
        and quote_bid is not None
        and (quote_ask is not None)
    ):
        stance = "REVIEW_CONTEXT_NOT_TRADE_SIGNAL"
    else:
        stance = "WATCH_ONLY"
    confirmations = [
        "refresh SoSoValue ETF flow/news evidence before acting",
        "refresh SoDEX quote/orderbook context before acting",
        "run strategy evaluation; do not trade from this report alone",
    ]
    invalidations = [
        "missing or malformed evidence rows increase uncertainty",
        "latest SoDEX quote is unavailable or stale",
        "SoDEX preflight refuses live write prerequisites",
    ]
    next_actions = [
        f"run bounded SigLab evaluation for {entity}-related families",
        "inspect evidence graph for non-causal narrative links",
        "keep signed execution disabled unless sodex-preflight reports live_write_allowed=true",
    ]
    if preflight.get("live_write_allowed"):
        next_actions.append(
            "if operator still proceeds, require manual confirmation and dry-run preview before live write",
        )
    return {
        "stance": stance,
        "use_case": "operator_decision_support",
        "not_a_trade_signal": True,
        "evidence_complete": evidence_complete,
        "confirmations_required": confirmations,
        "invalidation_checks": invalidations,
        "next_actions": next_actions,
        "risk_controls": [
            "no causal claim",
            "no automatic order submission",
            "signed SoDEX execution requires explicit preflight success",
            "USD cost is not claimed for provider usage",
        ],
    }


def market_report_html(report: dict[str, Any]) -> str:

    def esc(value: object) -> str:
        return html.escape(str(value))

    signal = dict(report.get("signal_summary") or {})
    decision = dict(report.get("decision_support") or {})
    selection = dict(report.get("evidence_selection") or {})
    soso_stats = dict(selection.get("sosovalue_read_stats") or {})
    sodex_stats = dict(selection.get("sodex_read_stats") or {})
    from siglab.cli.helpers import _render_html_template

    return _render_html_template(
        "market_report",
        entity=esc(report.get("entity")),
        status=esc(report.get("status")),
        generated_at=esc(report.get("generated_at")),
        headline=esc(signal.get("headline")),
        flow_direction=esc(signal.get("flow_direction")),
        flow_value=esc(signal.get("flow_value")),
        net_assets=esc(signal.get("net_assets")),
        operator_action=esc(signal.get("operator_action")),
        causality=esc(signal.get("causality")),
        stance=esc(decision.get("stance")),
        next_actions="".join(
            f"<li>{esc(item)}</li>" for item in list(decision.get("next_actions") or [])
        ),
        not_a_trade_signal=esc(decision.get("not_a_trade_signal")),
        news_items="".join(
            f"<li>{esc(item)}</li>" for item in list(signal.get("news_titles") or [])
        )
        or "<li>missing</li>",
        selection_semantics=esc(selection.get("latest_valid_semantics")),
        soso_record_count=esc(soso_stats.get("record_count")),
        soso_malformed=esc(soso_stats.get("malformed_count")),
        soso_non_object=esc(soso_stats.get("non_object_count")),
        sodex_record_count=esc(sodex_stats.get("record_count")),
        sodex_malformed=esc(sodex_stats.get("malformed_count")),
        sodex_non_object=esc(sodex_stats.get("non_object_count")),
        missing_items="".join(
            f"<li>{esc(item)}</li>" for item in list(report.get("missing") or [])
        )
        or "<li>none</li>",
        warnings_items="".join(
            f"<li>{esc(item)}</li>" for item in list(report.get("warnings") or [])
        ),
    )


def _record_sort_key_internal(row: dict[str, Any]) -> tuple[int, float, str]:
    """Internal sort key for market report news rows."""
    from siglab.cli.helpers import _record_timestamp as _ts

    timestamp = _ts(row)
    if timestamp is None:
        return (0, 0.0, str(row.get("evidence_path") or ""))
    return (1, timestamp.timestamp(), str(row.get("evidence_path") or ""))
