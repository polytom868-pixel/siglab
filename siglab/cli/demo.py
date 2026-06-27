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
    sodex_preflight_report,
)
from siglab.cli.helpers import build_market_report
from siglab.cli.rich_utils import print_json, print_success
from siglab.telemetry import (
    build_telemetry_payload,
    evidence_paths_for_telemetry,
    provider_metric_paths_for_telemetry,
    trace_paths_for_telemetry,
)
from siglab.config import SiglabConfig, load_settings

from siglab.utils import write_json
from siglab.utils import resolve_path_from_root


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
        sosovalue_path: Path | None = evidence_dir / "sosovalue.jsonl"
        sodex_ws_path: Path = evidence_dir / "sodex_ws.jsonl"
        _evidence_errors: list[str] = []

        def _gen_evidence() -> tuple[Path | None, Path | None]:
            import asyncio

            from siglab.data.evidence import (
                EvidenceStore,
                etf_inflow_evidence,
                news_evidence,
                sodex_quote_evidence,
            )
            from siglab.data.feeds import SoDEXPublicPerpsClient, SoSoValueClient
            observed_at = datetime.now(UTC).isoformat()
            ssv_path: Path | None = None
            sodex_rest_path: Path | None = None

            async def _fetch() -> None:
                nonlocal ssv_path, sodex_rest_path
                # Clean up old evidence files before writing new ones
                for p in evidence_dir.glob("sosovalue_evidence*.jsonl"):
                    p.unlink()
                for p in evidence_dir.glob("sodex_rest_evidence*.jsonl"):
                    p.unlink()
                for p in evidence_dir.glob("sodex_ws_evidence.jsonl"):
                    p.unlink()
                # Remove canonical files so EvidenceStore starts fresh
                for name in ("sosovalue.jsonl", "sodex_rest.jsonl", "sodex_ws.jsonl"):
                    p = evidence_dir / name
                    if p.exists():
                        p.unlink()
                ssv_path = evidence_dir / "sosovalue.jsonl"
                sodex_rest_path = evidence_dir / "sodex_rest.jsonl"
                # SoSoValue ETF + news
                if settings.sosovalue_api_key_override:
                    try:
                        ssv_client = SoSoValueClient(
                            api_key=settings.sosovalue_api_key_override,
                        )
                        etf_rows, news_rows = await asyncio.gather(
                            ssv_client.etf_historical_inflow(),
                            ssv_client.featured_news_by_currency(page_size=10),
                            return_exceptions=True,
                        )
                        ssv_store = EvidenceStore(ssv_path)
                        if isinstance(etf_rows, list):
                            ssv_store.append_many(
                                etf_inflow_evidence(
                                    etf_rows,
                                    etf_type="us-btc-spot",
                                    observed_at=observed_at,
                                    evidence_path=str(ssv_path),
                                ),
                            )
                        if isinstance(news_rows, list):
                            ssv_store.append_many(
                                news_evidence(
                                    news_rows,
                                    observed_at=observed_at,
                                    evidence_path=str(ssv_path),
                                ),
                            )
                        await ssv_client.close()
                    except Exception as exc:
                        _evidence_errors.append(f"sosovalue: {exc}")
                # SoDEX rest tickers
                try:
                    sdx_client = SoDEXPublicPerpsClient()
                    sodex_records = await sodex_quote_evidence(
                        sdx_client,
                        observed_at=observed_at,
                        evidence_path=str(sodex_rest_path),
                    )
                    sodex_store = EvidenceStore(sodex_rest_path)
                    sodex_store.append_many(sodex_records)
                    await sdx_client.close()
                except Exception as exc:
                    _evidence_errors.append(f"sodex: {exc}")

            asyncio.run(_fetch())
            return (
                ssv_path if ssv_path and ssv_path.exists() else sosovalue_path,
                sodex_rest_path if sodex_rest_path and sodex_rest_path.exists() else sodex_ws_path,
            )

        sosovalue_path, sodex_path = _gen_evidence()
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
        evidence_paths = evidence_paths_for_telemetry(settings)
        telemetry = build_telemetry_payload(
            trace_paths=trace_paths,
            provider_metric_paths=provider_metric_paths,
            evidence_paths=evidence_paths,
        )
        telemetry_summary = {
            "trace_count": telemetry.get("trace_count"),
            "tool_invocation_count": telemetry.get("tool_invocation_count"),
            "provider_metrics_status": telemetry.get("provider_metrics_status"),
            "confidence": telemetry.get("confidence"),
        }
        evidence_summary = {
            "files": [str(sosovalue_path), str(sodex_path)] if sosovalue_path else [str(sodex_path)],
            "errors": _evidence_errors,
        }
        preflight_line = f"preflight: public_read={preflight_summary['public_read_ready']} signed={preflight_summary['signed_path_ready']} live_write={preflight_summary['live_write_allowed']}"
        manifest_line = f"manifest: readiness={manifest_summary['readiness']} artifacts={manifest_summary['artifact_count']}"
        market_line = f"market: entity={market_summary['entity']} status={market_summary['status']} warnings={len(market_summary['warnings'] or [])}"
        telemetry_line = f"telemetry: traces={telemetry_summary['trace_count']} tools={telemetry_summary['tool_invocation_count']} providers={telemetry_summary['provider_metrics_status']}"
        evidence_line = f"evidence: files={len(evidence_summary['files'])} errors={len(evidence_summary['errors'])}"
        one_page_summary = " | ".join(
            [preflight_line, manifest_line, market_line, evidence_line, telemetry_line],
        )
        payload: dict[str, Any] = {
            "summary": one_page_summary,
            "sodex_preflight": preflight_summary,
            "demo_manifest": manifest_summary,
            "market_report": market_summary,
            "evidence": evidence_summary,
            "telemetry_report": telemetry_summary,
        }

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
    safe_title = html.escape(str(payload.get("summary", "SigLab Demo Run")))
    rows = ""
    for key, value in payload.items():
        if key == "summary":
            continue
        safe_key = html.escape(str(key))
        safe_value = html.escape(str(value))
        rows += f"<tr><td>{safe_key}</td><td><pre>{safe_value}</pre></td></tr>"
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>SigLab Demo Run</title>"
        "<style>body{font-family:sans-serif;margin:2em}"
        "td{vertical-align:top;padding:4px 12px}"
        "td:first-child{font-weight:bold;white-space:nowrap}"
        "pre{margin:0;white-space:pre-wrap}</style></head><body>"
        f"<h1>{safe_title}</h1>"
        f"<table>{rows}</table></body></html>"
    )


def run_demo_manifest(args: argparse.Namespace) -> None:
    settings = load_settings()
    manifest = _build_demo_manifest(settings)
    output = (
        resolve_path_from_root(args.output, root_dir=settings.root_dir)
        if getattr(args, "output", None)
        else settings.artifact_dir / "demo_manifest_latest.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(output, manifest)
    html_output: Path | None = (
        resolve_path_from_root(args.html_output, root_dir=settings.root_dir)
        if getattr(args, "html_output", None)
        else None
    )
    if html_output is not None:
        html_output.parent.mkdir(parents=True, exist_ok=True)
        html_output.write_text(_demo_manifest_html(manifest), encoding="utf-8")
    if getattr(args, "json", False):
        print_json(manifest)
        return
    print_success(
        f"demo_manifest: {display_paths([output], root_dir=settings.root_dir)[0]}",
    )
    if html_output is not None:
        print_success(
            f"demo_manifest_html: {display_paths([html_output], root_dir=settings.root_dir)[0]}",
        )


def _build_demo_manifest(settings: SiglabConfig) -> dict[str, Any]:
    runs_dir = settings.artifact_dir
    evidence_dir = runs_dir / "evidence"
    market_report_path = runs_dir / "market_report_latest.json"
    preflight = sodex_preflight_report()
    trace_paths = trace_paths_for_telemetry(
        settings=settings,
        track="all",
        run_session_id=None,
    )
    provider_metric_paths = provider_metric_paths_for_telemetry(
        settings=settings,
        run_session_id=None,
    )
    evidence_paths = evidence_paths_for_telemetry(settings)
    telemetry = build_telemetry_payload(
        trace_paths=trace_paths,
        provider_metric_paths=provider_metric_paths,
        evidence_paths=evidence_paths,
    )
    artifacts = {
        "sosovalue_evidence": str(evidence_dir / "sosovalue.jsonl")
        if (evidence_dir / "sosovalue.jsonl").exists()
        else "",
        "sodex_rest_evidence": str(runs_dir / "evidence" / "sodex_rest.jsonl"),
        "evidence_graph": str(latest_path(evidence_dir, "*graph*.html") or ""),
        "market_report_json": str(market_report_path)
        if market_report_path.exists()
        else "",
        "market_report_html": str(market_report_path.with_suffix(".html"))
        if market_report_path.with_suffix(".html").exists()
        else "",
        "preflight_json": str(runs_dir / "sodex_preflight_latest.json"),
        "telemetry_json": str(runs_dir / "latest_telemetry_report.json"),
        "demo_run_json": str(runs_dir / "demo_run_latest.json"),
        "demo_manifest_json": str(runs_dir / "demo_manifest_latest.json"),
    }
    artifact_status = {
        "sosovalue_input_to_output": bool(artifacts.get("market_report_json")),
        "sodex_public_market_data": bool(artifacts.get("sodex_rest_evidence")),
        "sodex_live_write_allowed": bool(preflight.get("live_write_allowed")),
        "provider_metrics_present": bool(provider_metric_paths),
        "telemetry_provider_metrics_status": telemetry.get("provider_metrics_status"),
    }
    readiness = all(
        [
            artifact_status["sosovalue_input_to_output"],
            artifact_status["sodex_public_market_data"],
        ],
    )
    unsafe_claims = [
        "Signed SoDEX execution is not live-validated unless sodex_live_write_allowed is true.",
        "Market report evidence is temporal/contextual; causality is not claimed.",
        "B.AI Credits are not USD and must not be presented as USD spend.",
    ]
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "readiness": readiness,
        "artifacts": artifacts,
        "artifact_status": artifact_status,
        "preflight_summary": {
            "public_read_ready": preflight.get("public_read_ready"),
            "schema_pinned": preflight.get("schema_pinned"),
            "signed_path_ready": preflight.get("signed_path", {}).get("ready"),
            "live_write_allowed": preflight.get("live_write_allowed"),
        },
        "telemetry_summary": {
            "trace_count": telemetry.get("trace_count"),
            "tool_invocation_count": telemetry.get("tool_invocation_count"),
            "evidence_count": telemetry.get("evidence", {}).get("evidence_count"),
            "evidence_sources": telemetry.get("evidence", {}).get("evidence_sources"),
        },
        "unsafe_claims": unsafe_claims,
        "causality_claimed": False,
        "usd_cost_claimed": False,
    }


def _demo_manifest_html(manifest: dict[str, Any]) -> str:
    def esc(value: object) -> str:
        return html.escape(str(value))

    lines: list[str] = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>SigLab Demo Manifest</title>"
        "<style>body{font-family:sans-serif;margin:2em}"
        "td{vertical-align:top;padding:4px 12px}"
        "td:first-child{font-weight:bold;white-space:nowrap}" 
        "pre{margin:0;white-space:pre-wrap}"
        ".pass{color:green}.fail{color:red}</style></head><body>"
        f"<h1>SigLab Demo Manifest</h1>"
        f"<p>Readiness: <strong class=\"{'pass' if manifest.get('readiness') else 'fail'}\">{esc(manifest.get('readiness'))}</strong></p>"
    ]
    artifact_status = manifest.get("artifact_status", {})
    if artifact_status:
        lines.append("<h2>Artifact Status</h2><table>")
        for key, value in artifact_status.items():
            cls = "pass" if value else "fail"
            lines.append(
                f"<tr><td>{esc(key)}</td><td class=\"{cls}\">{esc(value)}</td></tr>"
            )
        lines.append("</table>")
    artifacts = manifest.get("artifacts", {})
    if artifacts:
        lines.append("<h2>Artifacts</h2><ul>")
        for key, value in artifacts.items():
            if value:
                lines.append(f"<li><strong>{esc(key)}:</strong> {esc(value)}</li>")
        lines.append("</ul>")
    unsafe = manifest.get("unsafe_claims", [])
    if unsafe:
        lines.append("<h2>Red Flags</h2><ul>")
        for claim in unsafe:
            lines.append(f"<li>{esc(claim)}</li>")
        lines.append("</ul>")
    lines.append("</body></html>")
    return "\n".join(lines)
