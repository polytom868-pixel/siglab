"""Demo subcommands: demo-report, demo-manifest, demo-refresh, wave-status."""

from __future__ import annotations

import argparse
import html
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from siglab.cli.rich_utils import print_json, print_success
from siglab.config import load_settings
from siglab.io_utils import write_json
from siglab.path_utils import resolve_path_from_root
from siglab.telemetry import aggregate_provider_metrics_artifacts, aggregate_trace_telemetry
from siglab.cli.helpers import (
    display_paths,
    latest_path,
    load_json_if_exists,
    split_cli_list,
    sodex_preflight_report,
)
from siglab.cli.market import build_market_report, market_report_html


def add_subparser(subparsers) -> None:
    # demo-report
    parser = subparsers.add_parser(
        "demo-report",
        help="Emit a buildathon/operator demo report from latest evidence and readiness artifacts.",
    )
    parser.add_argument("--output", default=None)
    parser.add_argument("--html-output", default=None)
    parser.add_argument("--json", action="store_true")

    # demo-manifest
    manifest_parser = subparsers.add_parser(
        "demo-manifest",
        help="Index latest demo artifacts, telemetry, evidence, and live-boundary readiness.",
    )
    manifest_parser.add_argument("--output", default=None)
    manifest_parser.add_argument("--html-output", default=None)
    manifest_parser.add_argument("--json", action="store_true")

    # demo-refresh
    refresh_parser = subparsers.add_parser(
        "demo-refresh",
        help="Refresh safe demo artifacts for the ops board without submitting live trades.",
    )
    refresh_parser.add_argument("--wave-number", type=int, default=1)
    refresh_parser.add_argument("--goal", default="refresh buildathon demo artifacts")
    refresh_parser.add_argument("--json", action="store_true")

    # wave-status
    wave_parser = subparsers.add_parser(
        "wave-status",
        help="Write the latest operator/agent wave status artifact consumed by the ops board.",
    )
    wave_parser.add_argument("--wave-number", type=int, required=True)
    wave_parser.add_argument("--phase", default="execution")
    wave_parser.add_argument("--status", choices=["running", "passed", "blocked", "failed"], default="running")
    wave_parser.add_argument("--goal", required=True)
    wave_parser.add_argument("--agents", default="", help="Comma-separated agent role labels.")
    wave_parser.add_argument("--outputs", default="", help="Comma-separated wave output labels.")
    wave_parser.add_argument("--blockers", default="", help="Comma-separated blockers.")
    wave_parser.add_argument("--validation-status", default="not_run")
    wave_parser.add_argument("--next-decision", default="")
    wave_parser.add_argument("--output", default=None)
    wave_parser.add_argument("--json", action="store_true")


# ---------------------------------------------------------------------------
# Demo report
# ---------------------------------------------------------------------------


def run_demo_report(args: argparse.Namespace) -> None:
    settings = load_settings()
    report = _build_demo_report_payload(settings)
    output = (
        resolve_path_from_root(args.output, root_dir=settings.root_dir)
        if args.output
        else settings.root_dir / "runs" / "demo_report.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    html_output = None
    if getattr(args, "html_output", None):
        html_output = resolve_path_from_root(args.html_output, root_dir=settings.root_dir)
    elif not getattr(args, "json", False):
        html_output = settings.root_dir / "runs" / "demo_report.html"
    if html_output is not None:
        html_output.parent.mkdir(parents=True, exist_ok=True)
        html_output.write_text(_demo_report_html(report), encoding="utf-8")
    payload = {
        "output": str(output),
        "html_output": str(html_output) if html_output is not None else None,
        "readiness": report["readiness"],
        "red_flags": report["red_flags"],
    }
    if getattr(args, "json", False):
        print_json(payload)
        return
    print_json(payload)


def _build_demo_report_payload(settings: Any) -> dict[str, Any]:
    evidence_dir = settings.root_dir / "runs" / "evidence"
    sodex_probe_dir = settings.root_dir / "runs" / "sodex_probes"
    sosovalue_summaries = sorted(evidence_dir.glob("*sosovalue*.summary.json"), key=lambda item: item.stat().st_mtime)
    sodex_summaries = sorted(evidence_dir.glob("*sodex*.summary.json"), key=lambda item: item.stat().st_mtime)
    ws_probes = sorted(sodex_probe_dir.glob("ws_*latest.json"), key=lambda item: item.stat().st_mtime)
    preflight = sodex_preflight_report()
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "use_case": "SoSoValue and SoDEX backed research-to-action evidence flow",
        "input_to_output_flow": [
            "SoSoValue evidence ingestion",
            "SoDEX public market stream ingestion",
            "Evidence normalization and dedupe",
            "Operator evidence graph/report",
            "SoDEX live-write preflight refusal until credentials exist",
        ],
        "latest_sosovalue_summary": load_json_if_exists(sosovalue_summaries[-1]) if sosovalue_summaries else None,
        "latest_sodex_summary": load_json_if_exists(sodex_summaries[-1]) if sodex_summaries else None,
        "latest_sodex_ws_probe": load_json_if_exists(ws_probes[-1]) if ws_probes else None,
        "sodex_preflight": preflight,
        "readiness": {
            "sosovalue_api": "PASS" if sosovalue_summaries else "PARTIAL",
            "sodex_public_api": "PASS" if ws_probes else "PARTIAL",
            "sodex_signed_execution": "FAIL_BLOCKED_BY_CREDENTIALS" if not preflight["live_write_allowed"] else "READY_NOT_VALIDATED_LIVE",
            "demo_materials": "PARTIAL",
        },
        "red_flags": [
            "Signed SoDEX writes are not live-proven.",
            "SoSoValue Index/Macro/Treasury/Fundraising/Crypto Stocks/Analysis Charts callable wrappers are missing.",
            "Evidence links are not causal claims.",
        ],
    }


def _demo_report_html(report: dict[str, Any]) -> str:
    readiness = dict(report.get("readiness") or {})
    flow = [str(item) for item in list(report.get("input_to_output_flow") or [])]
    red_flags = [str(item) for item in list(report.get("red_flags") or [])]
    sosovalue_summary = dict(report.get("latest_sosovalue_summary") or {})
    sodex_summary = dict(report.get("latest_sodex_summary") or {})
    ws_probe = dict(report.get("latest_sodex_ws_probe") or {})
    preflight = dict(report.get("sodex_preflight") or {})

    def esc(value: object) -> str:
        return html.escape(str(value))

    readiness_rows = "\n".join(
        f"<tr><th>{esc(key)}</th><td>{esc(value)}</td></tr>"
        for key, value in sorted(readiness.items())
    )
    flow_items = "\n".join(f"<li>{esc(item)}</li>" for item in flow)
    red_flag_items = "\n".join(f"<li>{esc(item)}</li>" for item in red_flags)
    evidence_rows = "\n".join(
        [
            f"<tr><th>SoSoValue evidence records</th><td>{esc(sosovalue_summary.get('record_count', 'missing'))}</td></tr>",
            f"<tr><th>SoSoValue evidence links</th><td>{esc(sosovalue_summary.get('link_count', 'missing'))}</td></tr>",
            f"<tr><th>SoDEX evidence records</th><td>{esc(sodex_summary.get('record_count', 'missing'))}</td></tr>",
            f"<tr><th>SoDEX WS first update</th><td>{esc(ws_probe.get('first_update_type', 'missing'))}</td></tr>",
            f"<tr><th>SoDEX live write allowed</th><td>{esc(preflight.get('live_write_allowed', False))}</td></tr>",
        ]
    )
    missing = preflight.get("missing_prerequisites")
    if isinstance(missing, list) and missing:
        missing_items = "\n".join(f"<li>{esc(item)}</li>" for item in missing)
    else:
        missing_items = "<li>none reported</li>"

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SigLab Buildathon Demo Report</title>
  <style>
    :root {{ --ink:#1c1917; --paper:#fbf7ef; --line:#d7c7ac; --accent:#0f766e; --warn:#b45309; --bad:#991b1b; }}
    body {{ margin:0; font-family: Georgia, 'Times New Roman', serif; color:var(--ink); background:linear-gradient(135deg,#fbf7ef,#eef7f4); }}
    main {{ max-width:1100px; margin:0 auto; padding:40px 24px 64px; }}
    h1 {{ font-size:42px; margin:0 0 8px; letter-spacing:-0.03em; }}
    h2 {{ margin-top:32px; border-bottom:1px solid var(--line); padding-bottom:8px; }}
    .lede {{ font-size:18px; max-width:820px; }}
    .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:18px; }}
    .card {{ background:rgba(255,255,255,.74); border:1px solid var(--line); border-radius:18px; padding:18px; box-shadow:0 12px 30px rgba(41,31,19,.08); }}
    table {{ border-collapse:collapse; width:100%; }}
    th,td {{ text-align:left; vertical-align:top; border-bottom:1px solid var(--line); padding:10px; }}
    th {{ width:44%; color:#4b3b2a; }}
    .flag li {{ color:var(--bad); margin:8px 0; }}
    .flow li {{ margin:8px 0; }}
    code {{ background:#efe6d7; padding:2px 5px; border-radius:5px; }}
  </style>
</head>
<body>
<main>
  <h1>SigLab Buildathon Demo Report</h1>
  <p class="lede">{esc(report.get('use_case', ''))}. This report shows what is real, what is partial, and what is blocked. Evidence links are correlation/context, not causal proof.</p>
  <div class="grid">
    <section class="card"><h2>Readiness</h2><table>{readiness_rows}</table></section>
    <section class="card"><h2>Evidence</h2><table>{evidence_rows}</table></section>
  </div>
  <section class="card"><h2>Input To Output Flow</h2><ol class="flow">{flow_items}</ol></section>
  <section class="card"><h2>Live Boundary Missing Prerequisites</h2><ul>{missing_items}</ul></section>
  <section class="card"><h2>Red Flags</h2><ul class="flag">{red_flag_items}</ul></section>
  <section class="card"><h2>Generated</h2><p><code>{esc(report.get('generated_at'))}</code></p></section>
</main>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Demo manifest
# ---------------------------------------------------------------------------


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
    print_success(f"demo_manifest: {display_paths([output], root_dir=settings.root_dir)[0]}")
    print_success(f"demo_manifest_html: {display_paths([html_output], root_dir=settings.root_dir)[0]}")


def _build_demo_manifest(settings: Any) -> dict[str, Any]:
    runs_dir = settings.artifact_dir
    from siglab.cli.telemetry import provider_metric_paths_for_telemetry

    provider_metric_paths = provider_metric_paths_for_telemetry(settings=settings, run_session_id=None)
    telemetry_path = runs_dir / "latest_telemetry_report.json"
    market_report_path = runs_dir / "market_report_latest.json"
    demo_report_path = runs_dir / "demo_report.json"
    market_report = load_json_if_exists(market_report_path) or {}
    telemetry = load_json_if_exists(telemetry_path) or {}
    preflight = sodex_preflight_report()
    artifacts = {
        "sosovalue_evidence": str(latest_path(runs_dir / "evidence", "*sosovalue*.jsonl") or ""),
        "sodex_ws_evidence": str(runs_dir / "evidence" / "sodex_ws_evidence.jsonl"),
        "evidence_graph": str(latest_path(runs_dir / "evidence", "*graph*.html") or ""),
        "market_report_json": str(market_report_path) if market_report_path.exists() else "",
        "market_report_html": str(runs_dir / "market_report_latest.html"),
        "demo_report_json": str(demo_report_path) if demo_report_path.exists() else "",
        "demo_report_html": str(runs_dir / "demo_report_latest.html"),
        "telemetry_report_json": str(telemetry_path) if telemetry_path.exists() else "",
        "provider_metrics": [str(path) for path in provider_metric_paths],
        "sosovalue_surface": str(settings.root_dir / "docs" / "sosovalue-api-surface.yaml"),
        "sodex_surface": str(settings.root_dir / "docs" / "sodex-api-surface.yaml"),
        "buildathon_audit": str(settings.root_dir / "docs" / "buildathon-readiness-audit.md"),
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
        "llm_cost_status": dict(telemetry.get("provider_metrics") or {}).get("usage", {}).get("cost_status"),
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
        "market_report_headline": dict(market_report.get("signal_summary") or {}).get("headline"),
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
        f"<li><strong>{esc(key)}</strong>: {esc(value)}</li>"
        for key, value in sorted(readiness.items())
    )
    artifact_rows = "\n".join(
        f"<tr><th>{esc(key)}</th><td>{esc(artifact_status.get(key))}</td><td><code>{esc(value)}</code></td></tr>"
        for key, value in sorted(artifacts.items())
    )
    red_flag_items = "\n".join(f"<li>{esc(item)}</li>" for item in red_flags)
    live_class = "bad" if not readiness.get("sodex_live_write_allowed") else "ok"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SigLab Buildathon Demo Panel</title>
  <style>
    :root {{ --ink:#1c1917; --paper:#f8f3e8; --card:#fffdf7; --line:#d6c8b4; --ok:#0f766e; --bad:#b91c1c; --warn:#a16207; }}
    body {{ margin:0; color:var(--ink); background:radial-gradient(circle at 20% 0%,#e0f2fe 0,#f8f3e8 34%,#fff7ed 100%); font-family:Georgia,'Times New Roman',serif; }}
    main {{ max-width:1120px; margin:0 auto; padding:42px 24px 72px; }}
    h1 {{ font-size:48px; letter-spacing:-0.04em; margin:0 0 8px; }}
    h2 {{ margin:0 0 12px; }}
    .lede {{ font-size:18px; max-width:820px; }}
    .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); gap:18px; margin:22px 0; }}
    .card {{ background:rgba(255,253,247,.86); border:1px solid var(--line); border-radius:20px; padding:20px; box-shadow:0 18px 40px rgba(60,45,25,.10); }}
    .badge {{ display:inline-block; padding:6px 10px; border-radius:999px; border:1px solid var(--line); background:white; margin-right:8px; }}
    .ok {{ color:var(--ok); }} .bad {{ color:var(--bad); }} .warn {{ color:var(--warn); }}
    table {{ width:100%; border-collapse:collapse; font-size:14px; }}
    th,td {{ text-align:left; vertical-align:top; border-bottom:1px solid var(--line); padding:9px; }}
    code {{ word-break:break-all; background:#f0e7d8; padding:2px 5px; border-radius:5px; }}
    li {{ margin:8px 0; }}
  </style>
</head>
<body>
<main>
  <h1>SigLab Buildathon Demo Panel</h1>
  <p class="lede">One operator flow: SoSoValue evidence -> SoDEX market context -> non-causal market report -> risk/preflight boundary -> telemetry-backed AI loop. This panel indexes proof artifacts; it does not claim live signed execution.</p>
  <p>
    <span class="badge">market report: {esc(manifest.get('market_report_status'))}</span>
    <span class="badge {live_class}">signed SoDEX live write: {esc(readiness.get('sodex_live_write_allowed'))}</span>
    <span class="badge">provider metrics: {esc(readiness.get('provider_metrics_present'))}</span>
  </p>
  <section class="grid">
    <div class="card"><h2>Readiness</h2><ul>{readiness_cards}</ul></div>
    <div class="card"><h2>Market Headline</h2><p>{esc(manifest.get('market_report_headline'))}</p></div>
    <div class="card"><h2>Red Flags</h2><ul class="bad">{red_flag_items}</ul></div>
  </section>
  <section class="card"><h2>Artifact Index</h2><table><tr><th>artifact</th><th>exists</th><th>path</th></tr>{artifact_rows}</table></section>
  <section class="card"><h2>Generated</h2><p><code>{esc(manifest.get('generated_at'))}</code></p></section>
</main>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Demo refresh
# ---------------------------------------------------------------------------


def run_demo_refresh(args: argparse.Namespace) -> None:
    settings = load_settings()
    runs_dir = settings.artifact_dir
    runs_dir.mkdir(parents=True, exist_ok=True)

    preflight = sodex_preflight_report()
    preflight_path = runs_dir / "sodex_preflight_latest.json"
    write_json(preflight_path, preflight)

    from siglab.cli.telemetry import trace_paths_for_telemetry, provider_metric_paths_for_telemetry

    trace_paths = trace_paths_for_telemetry(settings=settings, track="all", run_session_id=None)
    provider_metric_paths = provider_metric_paths_for_telemetry(settings=settings, run_session_id=None)
    telemetry = aggregate_trace_telemetry(trace_paths)
    telemetry["trace_paths_scanned"] = len(trace_paths)
    telemetry["provider_metrics"] = aggregate_provider_metrics_artifacts(provider_metric_paths)
    telemetry["provider_metrics_paths_scanned"] = len(provider_metric_paths)
    telemetry["provider_metrics_status"] = (
        "missing"
        if trace_paths and telemetry["provider_metrics"]["artifact_count"] == 0
        else "present"
        if telemetry["provider_metrics"]["artifact_count"] > 0
        else "not_applicable"
    )
    telemetry_path = runs_dir / "latest_telemetry_report.json"
    write_json(telemetry_path, telemetry)

    evidence_dir = runs_dir / "evidence"
    market = build_market_report(
        entity="BTC",
        sosovalue_evidence=latest_path(evidence_dir, "*sosovalue*.jsonl"),
        sodex_evidence=evidence_dir / "sodex_ws_evidence.jsonl",
    )
    market_path = runs_dir / "market_report_latest.json"
    market_html_path = runs_dir / "market_report_latest.html"
    write_json(market_path, market)
    market_html_path.write_text(market_report_html(market), encoding="utf-8")

    demo_report = _build_demo_report_payload(settings)
    demo_report_path = runs_dir / "demo_report.json"
    demo_report_html_path = runs_dir / "demo_report_latest.html"
    write_json(demo_report_path, demo_report)
    demo_report_html_path.write_text(_demo_report_html(demo_report), encoding="utf-8")

    wave_payload = _build_wave_status_payload(
        argparse.Namespace(
            wave_number=int(args.wave_number),
            phase="demo_refresh",
            status="running",
            goal=str(args.goal),
            agents="operator,dashboard,hardening",
            outputs="preflight,telemetry,market_report,demo_report,demo_manifest,wave_status",
            blockers="signed SoDEX live execution unproven,private account WS unvalidated",
            validation_status="demo_refresh_generated",
            next_decision="open /ops and review unsafe claims before demo",
        )
    )
    wave_path = runs_dir / "wave_status_latest.json"
    write_json(wave_path, wave_payload)

    manifest = _build_demo_manifest(settings)
    manifest_path = runs_dir / "demo_manifest_latest.json"
    manifest_html_path = runs_dir / "demo_manifest_latest.html"
    write_json(manifest_path, manifest)
    manifest_html_path.write_text(_demo_manifest_html(manifest), encoding="utf-8")

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "artifacts": {
            "sodex_preflight": display_paths([preflight_path], root_dir=settings.root_dir)[0],
            "telemetry": display_paths([telemetry_path], root_dir=settings.root_dir)[0],
            "market_report": display_paths([market_path], root_dir=settings.root_dir)[0],
            "market_report_html": display_paths([market_html_path], root_dir=settings.root_dir)[0],
            "demo_report": display_paths([demo_report_path], root_dir=settings.root_dir)[0],
            "demo_manifest": display_paths([manifest_path], root_dir=settings.root_dir)[0],
            "demo_manifest_html": display_paths([manifest_html_path], root_dir=settings.root_dir)[0],
            "wave_status": display_paths([wave_path], root_dir=settings.root_dir)[0],
        },
        "readiness": manifest.get("readiness"),
        "market_report_status": market.get("status"),
        "live_write_allowed": preflight.get("live_write_allowed"),
        "unsafe_claims": wave_payload.get("unsafe_claims"),
    }
    if getattr(args, "json", False):
        print_json(payload)
        return
    print_json(payload)


# ---------------------------------------------------------------------------
# Wave status
# ---------------------------------------------------------------------------


def run_wave_status(args: argparse.Namespace) -> None:
    settings = load_settings()
    output = (
        resolve_path_from_root(args.output, root_dir=settings.root_dir)
        if args.output
        else settings.artifact_dir / "wave_status_latest.json"
    )
    payload = _build_wave_status_payload(args)
    write_json(output, payload)
    if getattr(args, "json", False):
        print_json(payload)
    print_success(f"wave_status: {display_paths([output], root_dir=settings.root_dir)[0]}")


def _build_wave_status_payload(args: argparse.Namespace) -> dict[str, Any]:
    blockers = split_cli_list(args.blockers)
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "wave_number": int(args.wave_number),
        "phase": str(args.phase or "execution"),
        "status": str(args.status or "running"),
        "goal": str(args.goal or "").strip(),
        "agents": split_cli_list(args.agents),
        "outputs": split_cli_list(args.outputs),
        "blockers": blockers,
        "validation_status": str(args.validation_status or "not_run"),
        "next_decision": str(args.next_decision or "").strip(),
        "stop_allowed": False,
        "unsafe_claims": [
            "signed SoDEX live execution remains unproven",
            "private/account SoDEX WebSocket remains unvalidated",
        ],
    }
