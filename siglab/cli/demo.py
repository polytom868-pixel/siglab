"""Demo subcommands: demo run, demo manifest."""

from __future__ import annotations

import argparse
import html
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from siglab.cli.helpers import (
    display_paths,
    latest_path,
    load_json_if_exists,
    sodex_preflight_report,
)
from siglab.cli.market import build_market_report
from siglab.cli.rich_utils import print_json, print_success
from siglab.cli.telemetry import (
    build_telemetry_payload,
    provider_metric_paths_for_telemetry,
    trace_paths_for_telemetry,
)
from siglab.config import SiglabConfig, load_settings
from siglab.evaluation.signal_narrative import build_signal_narrative
from siglab.utils import write_json
from siglab.path_utils import resolve_path_from_root


def add_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register nested demo subcommands: run, manifest."""
    demo_parser = subparsers.add_parser("demo", help="Demo commands: run, manifest")
    demo_subparsers = demo_parser.add_subparsers(dest="demo_command", required=True)
    run_parser = demo_subparsers.add_parser(
        "run",
        help="One-shot judge-friendly demo summary: preflight + manifest + market + telemetry.",
    )
    run_parser.add_argument("--output", default=None)
    run_parser.add_argument("--html-output", default=None)
    run_parser.add_argument("--json", action="store_true")
    run_parser.add_argument(
        "--narrative",
        action="store_true",
        help="Include signal narrative from latest evaluation",
    )
    manifest_parser = demo_subparsers.add_parser(
        "manifest",
        help="Index latest demo artifacts, telemetry, evidence, and live-boundary readiness.",
    )
    manifest_parser.add_argument("--output", default=None)
    manifest_parser.add_argument("--html-output", default=None)
    manifest_parser.add_argument("--json", action="store_true")


def run_demo_run(args: argparse.Namespace) -> None:
    """One-shot judge-friendly demo summary: collection -> manifest -> summary."""
    settings = load_settings()
    output_path: Path | None = None
    try:
        preflight = sodex_preflight_report()
        preflight_summary = {
            "public_read_ready": preflight.get("public_read_ready"),
            "schema_pinned": preflight.get("schema_pinned"),
            "signed_path_ready": preflight.get("signed_path", {}).get("ready"),
            "environment": preflight.get("signed_path", {}).get("environment"),
            "live_write_allowed": preflight.get("live_write_allowed"),
        }
        manifest = _build_demo_manifest(settings)
        manifest_summary = {
            "readiness": manifest.get("readiness"),
            "unsafe_claims": manifest.get("unsafe_claims"),
            "artifact_count": len(manifest.get("artifacts", {}) or {}),
        }
        evidence_dir = settings.artifact_dir / "evidence"
        sosovalue_path = latest_path(evidence_dir, "*sosovalue*.jsonl")
        sodex_path = evidence_dir / "sodex_ws_evidence.jsonl"
        market = build_market_report(
            entity="BTC",
            sosovalue_evidence=sosovalue_path,
            sodex_evidence=sodex_path,
        )
        market_summary = {
            "entity": market.get("entity"),
            "status": market.get("status"),
            "warnings": market.get("warnings"),
            "as_of": market.get("as_of"),
        }
        trace_paths = trace_paths_for_telemetry(
            settings=settings,
            track="all",
            run_session_id=None,
        )
        provider_metric_paths = provider_metric_paths_for_telemetry(
            settings=settings,
            run_session_id=None,
        )
        telemetry = build_telemetry_payload(
            trace_paths=trace_paths,
            provider_metric_paths=provider_metric_paths,
        )
        telemetry_summary = {
            "trace_count": telemetry.get("trace_count"),
            "tool_invocation_count": telemetry.get("tool_invocation_count"),
            "provider_metrics_status": telemetry.get("provider_metrics_status"),
            "confidence": telemetry.get("confidence"),
        }
        preflight_line = f"preflight: public_read={preflight_summary['public_read_ready']} signed={preflight_summary['signed_path_ready']} live_write={preflight_summary['live_write_allowed']}"
        manifest_line = f"manifest: readiness={manifest_summary['readiness']} artifacts={manifest_summary['artifact_count']}"
        market_line = f"market: entity={market_summary['entity']} status={market_summary['status']} warnings={len(market_summary['warnings'] or [])}"
        telemetry_line = f"telemetry: traces={telemetry_summary['trace_count']} tools={telemetry_summary['tool_invocation_count']} providers={telemetry_summary['provider_metrics_status']}"
        one_page_summary = " | ".join(
            [preflight_line, manifest_line, market_line, telemetry_line],
        )
        payload: dict[str, Any] = {
            "summary": one_page_summary,
            "sodex_preflight": preflight_summary,
            "demo_manifest": manifest_summary,
            "market_report": market_summary,
            "telemetry_report": telemetry_summary,
        }
        if getattr(args, "narrative", False):
            try:
                narrative_text = build_signal_narrative({})
            except Exception:
                import logging

                logging.getLogger(__name__).exception("Signal narrative build failed")
                narrative_text = "[Narrative unavailable]"
            payload["narrative"] = narrative_text
        output_path = (
            resolve_path_from_root(args.output, root_dir=settings.root_dir)
            if getattr(args, "output", None)
            else settings.artifact_dir / "demo_run_latest.json"
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        write_json(output_path, payload)
        html_output: Path | None = (
            resolve_path_from_root(args.html_output, root_dir=settings.root_dir)
            if getattr(args, "html_output", None)
            else None
        )
        if html_output is not None:
            html_output.parent.mkdir(parents=True, exist_ok=True)
            html_output.write_text(_demo_run_html(payload), encoding="utf-8")
        if getattr(args, "json", False):
            print_json(payload)
            return
        print_success(
            f"demo_run: {display_paths([output_path], root_dir=settings.root_dir)[0]}",
        )
        if html_output is not None:
            print_success(
                f"demo_run_html: {display_paths([html_output], root_dir=settings.root_dir)[0]}",
            )
        print(one_page_summary)
    except KeyboardInterrupt:
        print(
            "\n[yellow]demo-run interrupted by user — partial output may remain.[/yellow]",
        )
        if output_path is not None and output_path.exists():
            output_path.unlink()
        return


def _demo_run_html(payload: dict[str, Any]) -> str:
    """Minimal HTML page displaying the demo-run summary payload."""

    def _esc(v: object) -> str:
        return html.escape(str(v))

    summary = _esc(payload.get("summary", ""))

    def kv_rows(d: dict[str, Any]) -> str:
        return "\n".join(
            (
                f"<tr><th>{_esc(k)}</th><td>{_esc(v)}</td></tr>"
                for k, v in sorted(d.items())
            ),
        )

    from siglab.cli.helpers import _render_html_template

    return _render_html_template(
        "demo_report",
        summary=summary,
        preflight_rows=kv_rows(dict(payload.get("sodex_preflight") or {})),
        manifest_rows=kv_rows(dict(payload.get("demo_manifest") or {})),
        market_rows=kv_rows(dict(payload.get("market_report") or {})),
        telemetry_rows=kv_rows(dict(payload.get("telemetry_report") or {})),
        generated_at=_esc(datetime.now(UTC).isoformat()),
    )


def run_demo_manifest(args: argparse.Namespace) -> None:
    settings = load_settings()
    manifest = _build_demo_manifest(settings)
    output = (
        resolve_path_from_root(args.output, root_dir=settings.root_dir)
        if args.output
        else settings.root_dir / "runs" / "demo_manifest_latest.json"
    )
    write_json(output, manifest)
    html_output = (
        resolve_path_from_root(args.html_output, root_dir=settings.root_dir)
        if getattr(args, "html_output", None)
        else output.with_suffix(".html")
    )
    html_output.parent.mkdir(parents=True, exist_ok=True)
    html_output.write_text(_demo_manifest_html(manifest), encoding="utf-8")
    if getattr(args, "json", False):
        print_json(manifest)
        return
    print_success(
        f"demo_manifest: {display_paths([output], root_dir=settings.root_dir)[0]}",
    )
    print_success(
        f"demo_manifest_html: {display_paths([html_output], root_dir=settings.root_dir)[0]}",
    )


def _build_demo_manifest(settings: SiglabConfig) -> dict[str, Any]:
    runs_dir = settings.artifact_dir
    from siglab.cli.telemetry import provider_metric_paths_for_telemetry

    provider_metric_paths = provider_metric_paths_for_telemetry(
        settings=settings,
        run_session_id=None,
    )
    telemetry_path = runs_dir / "latest_telemetry_report.json"
    market_report_path = runs_dir / "market_report_latest.json"
    demo_report_path = runs_dir / "demo_report.json"
    market_report = load_json_if_exists(market_report_path) or {}
    telemetry = load_json_if_exists(telemetry_path) or {}
    preflight = sodex_preflight_report()
    artifacts = {
        "sosovalue_evidence": str(
            latest_path(runs_dir / "evidence", "*sosovalue*.jsonl") or "",
        ),
        "sodex_ws_evidence": str(runs_dir / "evidence" / "sodex_ws_evidence.jsonl"),
        "evidence_graph": str(latest_path(runs_dir / "evidence", "*graph*.html") or ""),
        "market_report_json": str(market_report_path)
        if market_report_path.exists()
        else "",
        "market_report_html": str(runs_dir / "market_report_latest.html"),
        "demo_report_json": str(demo_report_path) if demo_report_path.exists() else "",
        "demo_report_html": str(runs_dir / "demo_report_latest.html"),
        "telemetry_report_json": str(telemetry_path) if telemetry_path.exists() else "",
        "provider_metrics": [str(path) for path in provider_metric_paths],
        "sosovalue_surface": str(
            settings.root_dir / "docs" / "sosovalue-api-surface.yaml",
        ),
        "sodex_surface": str(settings.root_dir / "docs" / "sodex-api-surface.yaml"),
        "buildathon_audit": str(
            settings.root_dir / "docs" / "buildathon-readiness-audit.md",
        ),
        "demo_script": str(settings.root_dir / "docs" / "demo-script.md"),
    }
    artifact_status = {
        key: bool(value) and (isinstance(value, str) and Path(value).exists())
        for key, value in artifacts.items()
        if key != "provider_metrics"
    }
    artifact_status["provider_metrics"] = bool(provider_metric_paths)
    readiness = {
        "sosovalue_input_to_output": bool(artifact_status.get("market_report_json")),
        "sodex_public_market_data": bool(artifact_status.get("sodex_ws_evidence")),
        "sodex_live_write_allowed": bool(preflight.get("live_write_allowed")),
        "provider_metrics_present": bool(provider_metric_paths),
        "telemetry_provider_metrics_status": telemetry.get("provider_metrics_status"),
        "llm_cost_status": dict(telemetry.get("provider_metrics") or {})
        .get("usage", {})
        .get("cost_status"),
        "causality_claimed": False,
        "usd_cost_claimed": False,
    }
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "purpose": "buildathon_demo_artifact_index",
        "artifacts": artifacts,
        "artifact_status": artifact_status,
        "readiness": readiness,
        "market_report_status": market_report.get("status"),
        "market_report_headline": dict(market_report.get("signal_summary") or {}).get(
            "headline",
        ),
        "sodex_preflight": preflight,
        "red_flags": [
            "Signed SoDEX execution is not live-validated unless sodex_live_write_allowed is true.",
            "Market report evidence is temporal/contextual; causality is not claimed.",
            "B.AI Credits are not USD and must not be presented as USD spend.",
        ],
    }


def _demo_manifest_html(manifest: dict[str, Any]) -> str:

    def esc(value: object) -> str:
        return html.escape(str(value))

    readiness = dict(manifest.get("readiness") or {})
    artifacts = dict(manifest.get("artifacts") or {})
    artifact_status = dict(manifest.get("artifact_status") or {})
    red_flags = list(manifest.get("red_flags") or [])
    readiness_cards = "\n".join(
        (
            f"<li><strong>{esc(key)}</strong>: {esc(value)}</li>"
            for key, value in sorted(readiness.items())
        ),
    )
    artifact_rows = "\n".join(
        (
            f"<tr><th>{esc(key)}</th><td>{esc(artifact_status.get(key))}</td><td><code>{esc(value)}</code></td></tr>"
            for key, value in sorted(artifacts.items())
        ),
    )
    red_flag_items = "\n".join(f"<li>{esc(item)}</li>" for item in red_flags)
    live_class = "bad" if not readiness.get("sodex_live_write_allowed") else "ok"
    from siglab.cli.helpers import _render_html_template

    return _render_html_template(
        "demo_manifest",
        market_report_status=esc(manifest.get("market_report_status")),
        live_class=live_class,
        readiness_sodex_live_write=esc(readiness.get("sodex_live_write_allowed")),
        readiness_provider_metrics=esc(readiness.get("provider_metrics_present")),
        readiness_cards=readiness_cards,
        market_report_headline=esc(manifest.get("market_report_headline")),
        red_flag_items=red_flag_items,
        artifact_rows=artifact_rows,
        generated_at=esc(manifest.get("generated_at")),
    )
