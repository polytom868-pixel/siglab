from __future__ import annotations

import argparse
import asyncio
import html
import json
import os
import time
from itertools import count
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from siglab.benchmark import (
    DEFAULT_BENCHMARK_DECK,
    benchmark_status as benchmark_status_payload,
    evaluate_benchmark_deck,
    init_benchmark_deck,
    supported_deck_names,
)
from siglab.data import EvidenceStore, MarketDataProvider, ParquetLake, etf_inflow_evidence, news_evidence, sodex_ws_evidence
from siglab.data.sosovalue_client import SoSoValueClient, SoSoValueEndpoints
from siglab.dashboard import run_dashboard_server
from siglab.evaluator import ResearchEvaluator
from siglab.hardening_profile import build_profile, profile_as_text, strict_failure_count
from siglab.io_utils import write_json
from siglab.live import LiveDeploymentManager
from siglab.live.sodex_rate_limit import SODEX_ENDPOINT_WEIGHTS, SODEX_WEIGHT_BUDGET_PER_MINUTE
from siglab.live.sodex_ws import SoDEXWebSocketClient, SoDEXWebSocketError
from siglab.live.sodex_signing import (
    SoDEXSignedRequest,
    SUPPORTED_SODEX_SIGNED_ACTIONS,
    UNSUPPORTED_SODEX_SIGNED_ACTIONS,
    build_signature_input,
    canonical_json,
    http_body_from_action_payload,
    perps_cancel_item,
    perps_cancel_order_body,
    perps_new_order_body,
    perps_order_item,
    perps_schedule_cancel_body,
    perps_update_leverage_body,
    perps_update_margin_body,
)
from siglab.llm import ClaudeClient, LLMProviderError
from siglab.telemetry import aggregate_provider_metrics_artifacts, aggregate_trace_telemetry
from siglab.visualization import write_evidence_graph_html
from siglab.schemas import SignalSpec
from siglab.path_utils import display_path, resolve_path_from_root
from siglab.orchestration import (
    SpecWriterRunner,
    OptunaOptimizerRunner,
    ReflectionRunner,
    ResearchPlannerRunner,
    WorkspaceHooks,
)
from siglab.orchestration.trials import (
    build_spec_patch,
    deployment_rank,
    summarize_generalization,
    summarize_patch,
    summarize_return_attribution,
)
from siglab.research import HypothesisSandbox, WebResearcher
from siglab.run_config import (
    ancestry_scope_kwargs as _ancestry_scope_kwargs,
    load_seed_specs_for_run as _load_seed_specs_for_run,
    override_seed_spec_symbols as _override_seed_spec_symbols,
    parse_symbol_override as _parse_symbol_override,
    resolve_memory_scope as _resolve_memory_scope,
    resolve_resume_run as _resolve_resume_run,
    validate_symbol_override as _validate_symbol_override,
)
from siglab.search import (
    SpecMutator,
    LineageStore,
    pick_deterministic_parent,
    pick_parent,
)
from siglab.config import load_settings
from siglab.track_registry import TRACK_CLI_CHOICES, canonical_track_name
from siglab.workspace import WorkspaceBuilder


SODEX_SIDE_ALIASES = {"BUY": 1, "SELL": 2}
SODEX_ORDER_TYPE_ALIASES = {"LIMIT": 1, "MARKET": 2}
SODEX_TIME_IN_FORCE_ALIASES = {"GTC": 1, "FOK": 2, "IOC": 3, "GTX": 4}
SODEX_POSITION_SIDE_ALIASES = {"BOTH": 1, "LONG": 2, "SHORT": 3}
SODEX_MODIFIER_ALIASES = {"NORMAL": 1, "STOP": 2, "BRACKET": 3, "ATTACHED_STOP": 4}
SODEX_MARGIN_MODE_ALIASES = {"ISOLATED": 1, "CROSS": 2}


def main() -> None:
    parser = argparse.ArgumentParser(prog="siglab")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument(
        "--track",
        choices=["all", *TRACK_CLI_CHOICES],
        default="all",
    )
    run_parser.add_argument("--population-size", type=int, default=None)
    run_parser.add_argument("--family", default=None)
    run_parser.add_argument(
        "--families",
        default=None,
        help="Comma-separated family list to run within a single track.",
    )
    run_parser.add_argument(
        "--resume-run",
        default=None,
        help="Resume an existing run session by run_session_id and continue from the next iteration.",
    )
    run_parser.add_argument(
        "--burn-in-iterations",
        type=int,
        default=0,
        help="Run this many deterministic iterations before the main run phase.",
    )
    run_parser.add_argument(
        "--iterations",
        type=int,
        default=1,
        help="Number of generations to run per selected track. Use 0 for infinite.",
    )
    run_parser.add_argument(
        "--max-runtime-seconds",
        type=float,
        default=None,
        help="Stop cleanly after this wall-clock budget. Useful for bounded validation of --iterations 0.",
    )
    run_parser.add_argument("--max-total-cost", type=float, default=None)
    run_parser.add_argument(
        "--max-total-credits",
        type=float,
        default=None,
        help="Stop cooperatively when verified provider Credits telemetry reaches this budget. This is not USD.",
    )
    run_parser.add_argument(
        "--max-call-estimated-credits",
        type=float,
        default=None,
        help="Refuse a single B.AI call when pre-call estimated Credits exceeds this budget.",
    )
    run_parser.add_argument("--max-provider-errors", type=int, default=None)
    run_parser.add_argument("--max-consecutive-no-improvement", type=int, default=None)
    run_parser.add_argument("--max-consecutive-crashes", type=int, default=None)
    run_parser.add_argument("--cooldown-seconds-on-429", type=float, default=0.0)
    run_parser.add_argument("--provider-fallback-on-quota", action="store_true")
    run_parser.add_argument("--stop-on-live-surface-unavailable", action="store_true")
    run_parser.add_argument("--resume-safe-check", action="store_true")
    run_parser.add_argument(
        "--memory-scope",
        choices=["session_local", "track_shared"],
        default=None,
        help="Whether planner/search memory is isolated to this run or shared across the whole track.",
    )
    run_parser.add_argument(
        "--symbols",
        default=None,
        help="Comma-separated basis symbols to seed the run with. Cross-sectional families use the full list; pair families use the first two symbols.",
    )
    run_parser.add_argument(
        "--use-historical-seeds",
        action="store_true",
        default=None,
        help="Opt in to replacing static family seeds with the best historical artifact-backed family seeds.",
    )
    run_parser.add_argument("--skip-llm", action="store_true")
    run_parser.add_argument("--agent-label", default="siglab_harness")
    run_parser.add_argument("--run-label", default=None)

    inspect_parser = subparsers.add_parser("inspect")
    inspect_parser.add_argument(
        "--track",
        choices=["all", *TRACK_CLI_CHOICES],
        default="all",
    )

    profile_parser = subparsers.add_parser(
        "profile",
        help="Run the strict SigLab hardening profile.",
    )
    profile_parser.add_argument("--json", action="store_true")
    profile_parser.add_argument("--strict", action="store_true")

    evidence_parser = subparsers.add_parser(
        "evidence-build",
        help="Build a source-backed SoSoValue evidence JSONL from implemented verified surfaces.",
    )
    evidence_parser.add_argument("--etf-type", default="us-btc-spot")
    evidence_parser.add_argument("--currency", default="BTC")
    evidence_parser.add_argument("--news-page-size", type=int, default=10)
    evidence_parser.add_argument("--news-pages", type=int, default=1)
    evidence_parser.add_argument("--output", default=None)
    evidence_parser.add_argument("--summary-output", default=None)
    evidence_parser.add_argument("--summary-top-links", type=int, default=10)
    evidence_parser.add_argument("--json", action="store_true")

    evidence_map_parser = subparsers.add_parser(
        "evidence-map",
        help="Render an HTML evidence graph from an evidence summary artifact.",
    )
    evidence_map_parser.add_argument("--summary", default=None)
    evidence_map_parser.add_argument("--evidence", default=None)
    evidence_map_parser.add_argument("--output", default=None)
    evidence_map_parser.add_argument("--json", action="store_true")

    demo_parser = subparsers.add_parser(
        "demo-report",
        help="Emit a buildathon/operator demo report from latest evidence and readiness artifacts.",
    )
    demo_parser.add_argument("--output", default=None)
    demo_parser.add_argument("--html-output", default=None)
    demo_parser.add_argument("--json", action="store_true")

    demo_manifest_parser = subparsers.add_parser(
        "demo-manifest",
        help="Index latest demo artifacts, telemetry, evidence, and live-boundary readiness.",
    )
    demo_manifest_parser.add_argument("--output", default=None)
    demo_manifest_parser.add_argument("--html-output", default=None)
    demo_manifest_parser.add_argument("--json", action="store_true")

    demo_refresh_parser = subparsers.add_parser(
        "demo-refresh",
        help="Refresh safe demo artifacts for the ops board without submitting live trades.",
    )
    demo_refresh_parser.add_argument("--wave-number", type=int, default=1)
    demo_refresh_parser.add_argument("--goal", default="refresh buildathon demo artifacts")
    demo_refresh_parser.add_argument("--json", action="store_true")

    market_report_parser = subparsers.add_parser(
        "market-report",
        help="Build a deterministic SoSoValue + SoDEX evidence-linked market report.",
    )
    market_report_parser.add_argument("--entity", default="BTC")
    market_report_parser.add_argument("--sosovalue-evidence", default=None)
    market_report_parser.add_argument("--sodex-evidence", default=None)
    market_report_parser.add_argument("--output", default=None)
    market_report_parser.add_argument("--html-output", default=None)
    market_report_parser.add_argument("--json", action="store_true")

    api_surface_parser = subparsers.add_parser(
        "api-surface",
        help="Summarize source-of-truth SoSoValue/SoDEX API surface maps.",
    )
    api_surface_parser.add_argument("--json", action="store_true")

    ancestry_parser = subparsers.add_parser("ancestry")
    ancestry_parser.add_argument(
        "--track",
        choices=TRACK_CLI_CHOICES,
        default=None,
    )
    ancestry_parser.add_argument("--limit", type=int, default=10)

    clear_passed_parser = subparsers.add_parser("clear-passed")
    clear_passed_parser.add_argument(
        "--track",
        choices=["all", *TRACK_CLI_CHOICES],
        default="all",
    )

    dashboard_parser = subparsers.add_parser("dashboard")
    dashboard_parser.add_argument("--host", default="127.0.0.1")
    dashboard_parser.add_argument("--port", type=int, default=8765)

    deploy_parser = subparsers.add_parser("deploy")
    deploy_parser.add_argument("--spec", required=True)
    deploy_parser.add_argument("--agent-id", default=None)
    deploy_parser.add_argument("--wallet-label", default=None)
    deploy_parser.add_argument("--config", dest="config_path", default=None)
    deploy_parser.add_argument("--job-name", default=None)
    deploy_parser.add_argument("--interval", dest="interval_seconds", type=int, default=None)
    deploy_parser.add_argument("--schedule", action="store_true")
    deploy_parser.add_argument("--llm-finalize", action="store_true")
    deploy_parser.add_argument("--live", action="store_true")

    deployments_parser = subparsers.add_parser("deployments")
    deployments_parser.add_argument("--spec", default=None)

    sodex_preflight_parser = subparsers.add_parser("sodex-preflight")
    sodex_preflight_parser.add_argument("--json", action="store_true")

    valuechain_parser = subparsers.add_parser("valuechain-preflight")
    valuechain_parser.add_argument("--rpc-url", default="https://mainnet.valuechain.xyz")
    valuechain_parser.add_argument("--expected-chain-id", type=int, default=286623)
    valuechain_parser.add_argument("--json", action="store_true")

    sodex_ws_parser = subparsers.add_parser("sodex-ws-probe")
    sodex_ws_parser.add_argument("--environment", choices=["mainnet", "testnet"], default="mainnet")
    sodex_ws_parser.add_argument("--market", choices=["spot", "perps"], default="perps")
    sodex_ws_parser.add_argument("--channel", default="allBookTicker")
    sodex_ws_parser.add_argument("--symbol", default=None)
    sodex_ws_parser.add_argument("--user-address", default=None)
    sodex_ws_parser.add_argument("--account-id", type=int, default=None)
    sodex_ws_parser.add_argument("--timeout-seconds", type=float, default=8.0)
    sodex_ws_parser.add_argument("--evidence-output", default=None)
    sodex_ws_parser.add_argument("--json", action="store_true")

    sodex_preview_parser = subparsers.add_parser("sodex-preview")
    sodex_preview_parser.add_argument(
        "--kind",
        choices=["new-order", "cancel-order", "schedule-cancel", "update-leverage", "update-margin"],
        required=True,
    )
    sodex_preview_parser.add_argument("--account-id", type=int, required=True)
    sodex_preview_parser.add_argument("--symbol-id", type=int, required=True)
    sodex_preview_parser.add_argument("--nonce", type=int, required=True)
    sodex_preview_parser.add_argument("--cl-ord-id", default="siglab-preview")
    sodex_preview_parser.add_argument("--modifier", default="NORMAL")
    sodex_preview_parser.add_argument("--side", default="BUY")
    sodex_preview_parser.add_argument("--order-type", default="LIMIT")
    sodex_preview_parser.add_argument("--time-in-force", default="GTC")
    sodex_preview_parser.add_argument("--price", default=None)
    sodex_preview_parser.add_argument("--quantity", default=None)
    sodex_preview_parser.add_argument("--funds", default=None)
    sodex_preview_parser.add_argument("--order-id", type=int, default=None)
    sodex_preview_parser.add_argument("--orig-cl-ord-id", default=None)
    sodex_preview_parser.add_argument("--scheduled-timestamp", type=int, default=None)
    sodex_preview_parser.add_argument("--amount", default=None)
    sodex_preview_parser.add_argument("--reduce-only", action="store_true")
    sodex_preview_parser.add_argument("--position-side", default="BOTH")
    sodex_preview_parser.add_argument("--leverage", type=int, default=1)
    sodex_preview_parser.add_argument("--margin-mode", default="ISOLATED")
    sodex_preview_parser.add_argument("--json", action="store_true", help="Accepted for CLI consistency; output is always JSON.")

    benchmark_init_parser = subparsers.add_parser("benchmark-init")
    benchmark_init_parser.add_argument(
        "--deck",
        choices=supported_deck_names(),
        default=DEFAULT_BENCHMARK_DECK,
    )
    benchmark_init_parser.add_argument("--agent-label", default="external_agent")
    benchmark_init_parser.add_argument("--run-label", default=None)
    benchmark_init_parser.add_argument("--force", action="store_true")

    benchmark_eval_parser = subparsers.add_parser("benchmark-eval")
    benchmark_eval_parser.add_argument(
        "--deck",
        choices=supported_deck_names(),
        default=DEFAULT_BENCHMARK_DECK,
    )

    benchmark_status_parser = subparsers.add_parser("benchmark-status")
    benchmark_status_parser.add_argument(
        "--deck",
        choices=supported_deck_names(),
        default=DEFAULT_BENCHMARK_DECK,
    )

    wave_status_parser = subparsers.add_parser(
        "wave-status",
        help="Write the latest operator/agent wave status artifact consumed by the ops board.",
    )
    wave_status_parser.add_argument("--wave-number", type=int, required=True)
    wave_status_parser.add_argument("--phase", default="execution")
    wave_status_parser.add_argument("--status", choices=["running", "passed", "blocked", "failed"], default="running")
    wave_status_parser.add_argument("--goal", required=True)
    wave_status_parser.add_argument("--agents", default="", help="Comma-separated agent role labels.")
    wave_status_parser.add_argument("--outputs", default="", help="Comma-separated wave output labels.")
    wave_status_parser.add_argument("--blockers", default="", help="Comma-separated blockers.")
    wave_status_parser.add_argument("--validation-status", default="not_run")
    wave_status_parser.add_argument("--next-decision", default="")
    wave_status_parser.add_argument("--output", default=None)
    wave_status_parser.add_argument("--json", action="store_true")

    config_parser = subparsers.add_parser(
        "config",
        help="Configuration inspection and validation commands.",
    )
    config_sub = config_parser.add_subparsers(dest="config_command", required=True)
    config_sub.add_parser(
        "validate",
        help="Validate config.json and environment settings.",
    )

    telemetry_parser = subparsers.add_parser(
        "telemetry-report",
        help="Aggregate empirical LLM/tool telemetry from run trace artifacts.",
    )
    telemetry_parser.add_argument("--track", default="all")
    telemetry_parser.add_argument("--run-session-id", default=None)
    telemetry_parser.add_argument("--json", action="store_true")

    args = parser.parse_args()
    if args.command == "run":
        asyncio.run(run_command(args))
        return
    if args.command == "inspect":
        asyncio.run(inspect_command(args))
        return
    if args.command == "profile":
        profile_command(args)
        return
    if args.command == "evidence-build":
        asyncio.run(evidence_build_command(args))
        return
    if args.command == "evidence-map":
        evidence_map_command(args)
        return
    if args.command == "demo-report":
        demo_report_command(args)
        return
    if args.command == "demo-manifest":
        demo_manifest_command(args)
        return
    if args.command == "demo-refresh":
        demo_refresh_command(args)
        return
    if args.command == "market-report":
        market_report_command(args)
        return
    if args.command == "api-surface":
        api_surface_command(args)
        return
    if args.command == "ancestry":
        ancestry_command(args)
        return
    if args.command == "clear-passed":
        clear_passed_command(args)
        return
    if args.command == "config":
        if args.config_command == "validate":
            config_validate_command(args)
        return
    if args.command == "dashboard":
        dashboard_command(args)
        return
    if args.command == "deploy":
        asyncio.run(deploy_command(args))
        return
    if args.command == "deployments":
        deployments_command(args)
        return
    if args.command == "sodex-preflight":
        sodex_preflight_command(args)
        return
    if args.command == "valuechain-preflight":
        asyncio.run(valuechain_preflight_command(args))
        return
    if args.command == "sodex-ws-probe":
        asyncio.run(sodex_ws_probe_command(args))
        return
    if args.command == "sodex-preview":
        sodex_preview_command(args)
        return
    if args.command == "benchmark-init":
        benchmark_init_command(args)
        return
    if args.command == "benchmark-eval":
        asyncio.run(benchmark_eval_command(args))
        return
    if args.command == "benchmark-status":
        benchmark_status_command(args)
        return
    if args.command == "wave-status":
        wave_status_command(args)
        return
    if args.command == "telemetry-report":
        telemetry_report_command(args)
        return


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

    print(f"config valid: {config_path}")
    print(f"  api_base_url: {system.get('api_base_url')}")
    raise SystemExit(0)


def _report_config_validation(errors: list[str]) -> None:
    for error in errors:
        print(f"ERROR: {error}", file=__import__("sys").stderr)
    raise SystemExit(1)


def profile_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    profile = build_profile(settings.root_dir)
    if getattr(args, "json", False):
        print(json.dumps(profile, indent=2, sort_keys=True, default=str))
    else:
        print(profile_as_text(profile))
    if getattr(args, "strict", False):
        failures = strict_failure_count(profile)
        if failures:
            raise SystemExit(min(failures, 125))


def api_surface_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    docs_dir = settings.root_dir / "docs"
    files = {
        "sosovalue": docs_dir / "sosovalue-api-surface.yaml",
        "sodex": docs_dir / "sodex-api-surface.yaml",
        "ecosystem": docs_dir / "sosovalue-ecosystem-surface.yaml",
        "buildathon": docs_dir / "buildathon-readiness-audit.md",
    }
    report: dict[str, Any] = {}
    for name, path in files.items():
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        report[name] = {
            "path": str(path),
            "exists": path.exists(),
            "line_count": len(text.splitlines()) if text else 0,
            "endpoint_path_mentions": text.count("path:"),
            "supported_mentions": text.count("supported"),
            "missing_mentions": text.count("missing"),
            "blocked_mentions": text.count("blocked"),
        }
    if getattr(args, "json", False):
        print(json.dumps(report, indent=2, sort_keys=True))
        return
    for name, payload in report.items():
        print(
            f"{name}: exists={payload['exists']} lines={payload['line_count']} "
            f"paths={payload['endpoint_path_mentions']} supported={payload['supported_mentions']} "
            f"missing={payload['missing_mentions']} blocked={payload['blocked_mentions']} "
            f"file={payload['path']}"
        )


def evidence_map_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    if args.evidence:
        evidence_path = resolve_path_from_root(args.evidence, root_dir=settings.root_dir)
        store = EvidenceStore(evidence_path)
        summary_path = evidence_path.with_suffix(".summary.json")
        store.write_summary(summary_path)
    elif args.summary:
        summary_path = resolve_path_from_root(args.summary, root_dir=settings.root_dir)
    else:
        candidates = sorted((settings.root_dir / "runs" / "evidence").glob("*.summary.json"), key=lambda item: item.stat().st_mtime)
        if not candidates:
            raise SystemExit("No evidence summary found. Run `siglab evidence-build` first or pass --summary.")
        summary_path = candidates[-1]
    output_path = (
        resolve_path_from_root(args.output, root_dir=settings.root_dir)
        if args.output
        else settings.root_dir / "runs" / "evidence" / "evidence_graph.html"
    )
    rendered = write_evidence_graph_html(summary_path, output_path)
    payload = {"summary": str(summary_path), "output": str(rendered)}
    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    print(f"wrote evidence graph: {rendered}")


def demo_report_command(args: argparse.Namespace) -> None:
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
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    print(json.dumps(payload, indent=2, sort_keys=True))


def _build_demo_report_payload(settings: Any) -> dict[str, Any]:
    evidence_dir = settings.root_dir / "runs" / "evidence"
    sodex_probe_dir = settings.root_dir / "runs" / "sodex_probes"
    sosovalue_summaries = sorted(evidence_dir.glob("*sosovalue*.summary.json"), key=lambda item: item.stat().st_mtime)
    sodex_summaries = sorted(evidence_dir.glob("*sodex*.summary.json"), key=lambda item: item.stat().st_mtime)
    ws_probes = sorted(sodex_probe_dir.glob("ws_*latest.json"), key=lambda item: item.stat().st_mtime)
    preflight = _sodex_preflight_report()
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
        "latest_sosovalue_summary": _load_json_if_exists(sosovalue_summaries[-1]) if sosovalue_summaries else None,
        "latest_sodex_summary": _load_json_if_exists(sodex_summaries[-1]) if sodex_summaries else None,
        "latest_sodex_ws_probe": _load_json_if_exists(ws_probes[-1]) if ws_probes else None,
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


def demo_manifest_command(args: argparse.Namespace) -> None:
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
        print(json.dumps(manifest, indent=2, sort_keys=True, default=str))
        return
    print(f"demo_manifest: {display_path(output, root_dir=settings.root_dir)}")
    print(f"demo_manifest_html: {display_path(html_output, root_dir=settings.root_dir)}")


def demo_refresh_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    runs_dir = settings.artifact_dir
    runs_dir.mkdir(parents=True, exist_ok=True)

    preflight = _sodex_preflight_report()
    preflight_path = runs_dir / "sodex_preflight_latest.json"
    write_json(preflight_path, preflight)

    trace_paths = _trace_paths_for_telemetry(settings=settings, track="all", run_session_id=None)
    provider_metric_paths = _provider_metric_paths_for_telemetry(settings=settings, run_session_id=None)
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
    market = _build_market_report(
        entity="BTC",
        sosovalue_evidence=_latest_path(evidence_dir, "*sosovalue*.jsonl"),
        sodex_evidence=evidence_dir / "sodex_ws_evidence.jsonl",
    )
    market_path = runs_dir / "market_report_latest.json"
    market_html_path = runs_dir / "market_report_latest.html"
    write_json(market_path, market)
    market_html_path.write_text(_market_report_html(market), encoding="utf-8")

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
            "sodex_preflight": display_path(preflight_path, root_dir=settings.root_dir),
            "telemetry": display_path(telemetry_path, root_dir=settings.root_dir),
            "market_report": display_path(market_path, root_dir=settings.root_dir),
            "market_report_html": display_path(market_html_path, root_dir=settings.root_dir),
            "demo_report": display_path(demo_report_path, root_dir=settings.root_dir),
            "demo_manifest": display_path(manifest_path, root_dir=settings.root_dir),
            "demo_manifest_html": display_path(manifest_html_path, root_dir=settings.root_dir),
            "wave_status": display_path(wave_path, root_dir=settings.root_dir),
        },
        "readiness": manifest.get("readiness"),
        "market_report_status": market.get("status"),
        "live_write_allowed": preflight.get("live_write_allowed"),
        "unsafe_claims": wave_payload.get("unsafe_claims"),
    }
    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
        return
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def _build_demo_manifest(settings: Any) -> dict[str, Any]:
    runs_dir = settings.artifact_dir
    provider_metric_paths = _provider_metric_paths_for_telemetry(settings=settings, run_session_id=None)
    telemetry_path = runs_dir / "latest_telemetry_report.json"
    market_report_path = runs_dir / "market_report_latest.json"
    demo_report_path = runs_dir / "demo_report.json"
    market_report = _load_json_if_exists(market_report_path) or {}
    telemetry = _load_json_if_exists(telemetry_path) or {}
    preflight = _sodex_preflight_report()
    artifacts = {
        "sosovalue_evidence": str(_latest_path(runs_dir / "evidence", "*sosovalue*.jsonl") or ""),
        "sodex_ws_evidence": str(runs_dir / "evidence" / "sodex_ws_evidence.jsonl"),
        "evidence_graph": str(_latest_path(runs_dir / "evidence", "*graph*.html") or ""),
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


def market_report_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    evidence_dir = settings.root_dir / "runs" / "evidence"
    sosovalue_path = (
        resolve_path_from_root(args.sosovalue_evidence, root_dir=settings.root_dir)
        if args.sosovalue_evidence
        else _latest_path(evidence_dir, "*sosovalue*.jsonl")
    )
    sodex_path = (
        resolve_path_from_root(args.sodex_evidence, root_dir=settings.root_dir)
        if args.sodex_evidence
        else evidence_dir / "sodex_ws_evidence.jsonl"
    )
    report = _build_market_report(
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
    output.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    html_output = (
        resolve_path_from_root(args.html_output, root_dir=settings.root_dir)
        if args.html_output
        else settings.root_dir / "runs" / "market_report.html"
    )
    html_output.parent.mkdir(parents=True, exist_ok=True)
    html_output.write_text(_market_report_html(report), encoding="utf-8")
    payload = {
        "output": str(output),
        "html_output": str(html_output),
        "entity": report["entity"],
        "status": report["status"],
        "warnings": report["warnings"],
    }
    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    print(json.dumps(payload, indent=2, sort_keys=True))


def _build_market_report(
    *,
    entity: str,
    sosovalue_evidence: Path | None,
    sodex_evidence: Path | None,
) -> dict[str, Any]:
    soso_rows, soso_read_stats = _read_jsonl_with_stats(sosovalue_evidence)
    sodex_rows, sodex_read_stats = _read_jsonl_with_stats(sodex_evidence)
    entity_upper = entity.upper()
    etf_entity = f"us-{entity_upper.lower()}-spot"
    quote_entity = f"{entity_upper}-USD"
    latest_flow = _latest_record(
        [
            row
            for row in soso_rows
            if row.get("entity") == etf_entity and row.get("relation") == "total_net_inflow"
        ],
        required_value="numeric",
    )
    latest_assets = _latest_record(
        [
            row
            for row in soso_rows
            if row.get("entity") == etf_entity and row.get("relation") == "total_net_assets"
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
        key=_record_sort_key,
        reverse=True,
    )[:5]
    quote = _latest_record(
        [
            row
            for row in sodex_rows
            if str(row.get("entity") or "").upper() == quote_entity
        ],
        required_value="quote",
    )
    preflight = _sodex_preflight_report()
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
    flow_value = _float_or_none((latest_flow or {}).get("value"))
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
        "headline": (
            f"{entity}: {flow_bias}; SoDEX quote "
            f"bid={bid if bid is not None else 'missing'} ask={ask if ask is not None else 'missing'}; "
            f"news_items={len(latest_news)}; live_write_allowed={bool(preflight.get('live_write_allowed'))}"
        ),
        "flow_direction": flow_bias,
        "flow_value": flow_value,
        "flow_timestamp": (latest_flow or {}).get("timestamp"),
        "net_assets": _float_or_none((latest_assets or {}).get("value")),
        "quote_bid": bid,
        "quote_ask": ask,
        "news_titles": [str(row.get("value") or "")[:180] for row in latest_news],
        "operator_action": (
            "review_only_signed_execution_blocked"
            if not preflight.get("live_write_allowed")
            else "review_before_any_live_order"
        ),
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
    elif flow_direction in {"ETF inflow", "ETF outflow"} and quote_bid is not None and quote_ask is not None:
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
        next_actions.append("if operator still proceeds, require manual confirmation and dry-run preview before live write")
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


def _market_report_html(report: dict[str, Any]) -> str:
    def esc(value: object) -> str:
        return html.escape(str(value))

    signal = dict(report.get("signal_summary") or {})
    decision = dict(report.get("decision_support") or {})
    selection = dict(report.get("evidence_selection") or {})
    soso_stats = dict(selection.get("sosovalue_read_stats") or {})
    sodex_stats = dict(selection.get("sodex_read_stats") or {})
    warnings = "".join(f"<li>{esc(item)}</li>" for item in list(report.get("warnings") or []))
    missing = "".join(f"<li>{esc(item)}</li>" for item in list(report.get("missing") or [])) or "<li>none</li>"
    news = "".join(f"<li>{esc(item)}</li>" for item in list(signal.get("news_titles") or [])) or "<li>missing</li>"
    return f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>SigLab Market Report {esc(report.get('entity'))}</title>
<style>
body{{margin:0;background:#f5f0e8;color:#211b16;font-family:Georgia,'Times New Roman',serif}}
main{{max-width:980px;margin:0 auto;padding:42px 24px}}
.card{{background:#fffaf2;border:1px solid #d8c7ad;border-radius:18px;padding:20px;margin:18px 0;box-shadow:0 14px 30px rgba(45,35,25,.08)}}
h1{{font-size:42px;margin:0}} h2{{margin-top:0}} code{{background:#eadfcc;padding:2px 5px;border-radius:5px}}
li{{margin:8px 0}} .warn li{{color:#9a3412}}
</style></head>
<body><main>
<h1>SigLab Market Report: {esc(report.get('entity'))}</h1>
<p><code>{esc(report.get('status'))}</code> · generated {esc(report.get('generated_at'))}</p>
<section class="card"><h2>Signal Summary</h2><p>{esc(signal.get('headline'))}</p><ul>
<li>flow direction: {esc(signal.get('flow_direction'))}</li>
<li>flow value: {esc(signal.get('flow_value'))}</li>
<li>net assets: {esc(signal.get('net_assets'))}</li>
<li>operator action: {esc(signal.get('operator_action'))}</li>
<li>causality: {esc(signal.get('causality'))}</li>
</ul></section>
<section class="card"><h2>Decision Support</h2><p><strong>{esc(decision.get('stance'))}</strong></p><ul>
{''.join(f"<li>{esc(item)}</li>" for item in list(decision.get('next_actions') or []))}
</ul><p>not a trade signal: {esc(decision.get('not_a_trade_signal'))}</p></section>
<section class="card"><h2>News Context</h2><ul>{news}</ul></section>
<section class="card"><h2>Evidence Quality</h2><ul>
<li>selection: {esc(selection.get('latest_valid_semantics'))}</li>
<li>SoSoValue rows: {esc(soso_stats.get('record_count'))}; malformed: {esc(soso_stats.get('malformed_count'))}; non-object: {esc(soso_stats.get('non_object_count'))}</li>
<li>SoDEX rows: {esc(sodex_stats.get('record_count'))}; malformed: {esc(sodex_stats.get('malformed_count'))}; non-object: {esc(sodex_stats.get('non_object_count'))}</li>
</ul></section>
<section class="card"><h2>Missing Evidence</h2><ul>{missing}</ul></section>
<section class="card"><h2>Warnings</h2><ul class="warn">{warnings}</ul></section>
</main></body></html>
"""


def _latest_path(directory: Path, pattern: str) -> Path | None:
    matches = sorted(directory.glob(pattern), key=lambda item: item.stat().st_mtime)
    return matches[-1] if matches else None


def _read_jsonl(path: Path | None) -> list[dict[str, Any]]:
    rows, _stats = _read_jsonl_with_stats(path)
    return rows


def _read_jsonl_with_stats(path: Path | None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if path is None or not path.exists():
        return [], {"path": str(path) if path else None, "line_count": 0, "record_count": 0, "malformed_count": 0, "non_object_count": 0}
    rows: list[dict[str, Any]] = []
    malformed_count = 0
    non_object_count = 0
    lines = path.read_text(encoding="utf-8").splitlines()
    for line in lines:
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            malformed_count += 1
            continue
        if isinstance(payload, dict):
            rows.append(payload)
        else:
            non_object_count += 1
    return rows, {
        "path": str(path),
        "line_count": len(lines),
        "record_count": len(rows),
        "malformed_count": malformed_count,
        "non_object_count": non_object_count,
    }


def _latest_record(rows: list[dict[str, Any]], *, required_value: str | None = None) -> dict[str, Any] | None:
    rows = [row for row in rows if _record_has_required_value(row, required_value)]
    if not rows:
        return None
    return sorted(rows, key=_record_sort_key, reverse=True)[0]


def _record_has_required_value(row: dict[str, Any], required_value: str | None) -> bool:
    if required_value is None:
        return True
    if _record_timestamp(row) is None:
        return False
    if required_value == "numeric":
        return _float_or_none(row.get("value")) is not None
    if required_value == "quote":
        attrs = dict(row.get("attributes") or {})
        return bool(attrs.get("bid") or row.get("value")) and bool(attrs.get("ask"))
    return True


def _record_sort_key(row: dict[str, Any]) -> tuple[int, float, str]:
    timestamp = _record_timestamp(row)
    if timestamp is None:
        return (0, 0.0, str(row.get("evidence_path") or ""))
    return (1, timestamp.timestamp(), str(row.get("evidence_path") or ""))


def _record_timestamp(row: dict[str, Any]) -> datetime | None:
    value = row.get("timestamp") or row.get("observed_at")
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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


def _load_json_if_exists(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def telemetry_report_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    trace_paths = _trace_paths_for_telemetry(
        settings=settings,
        track=str(args.track or "all"),
        run_session_id=args.run_session_id,
    )
    provider_metric_paths = _provider_metric_paths_for_telemetry(
        settings=settings,
        run_session_id=args.run_session_id,
    )
    payload = aggregate_trace_telemetry(trace_paths)
    payload["trace_paths_scanned"] = len(trace_paths)
    payload["provider_metrics"] = aggregate_provider_metrics_artifacts(provider_metric_paths)
    payload["provider_metrics_paths_scanned"] = len(provider_metric_paths)
    payload["provider_metrics_status"] = (
        "missing"
        if trace_paths and payload["provider_metrics"]["artifact_count"] == 0
        else "present"
        if payload["provider_metrics"]["artifact_count"] > 0
        else "not_applicable"
    )
    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        print(
            "\n".join(
                [
                    f"trace_count: {payload['trace_count']}",
                    f"stage_counts: {payload['stage_counts']}",
                    f"provider_counts: {payload['provider_counts']}",
                    f"model_counts: {payload['model_counts']}",
                    f"tool_invocation_count: {payload['tool_invocation_count']}",
                    f"tool_counts: {payload['tool_counts']}",
                    f"tool_latency_ms: {payload['tool_latency_ms']}",
                    f"provider_metrics_status: {payload['provider_metrics_status']}",
                    f"provider_metrics: {payload['provider_metrics']}",
                    f"confidence: {payload['confidence']}",
                ]
            )
        )


def wave_status_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    output = (
        resolve_path_from_root(args.output, root_dir=settings.root_dir)
        if args.output
        else settings.artifact_dir / "wave_status_latest.json"
    )
    payload = _build_wave_status_payload(args)
    write_json(output, payload)
    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
        return
    print(f"wave_status: {display_path(output, root_dir=settings.root_dir)}")


def _split_cli_list(value: str) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _build_wave_status_payload(args: argparse.Namespace) -> dict[str, Any]:
    blockers = _split_cli_list(args.blockers)
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "wave_number": int(args.wave_number),
        "phase": str(args.phase or "execution"),
        "status": str(args.status or "running"),
        "goal": str(args.goal or "").strip(),
        "agents": _split_cli_list(args.agents),
        "outputs": _split_cli_list(args.outputs),
        "blockers": blockers,
        "validation_status": str(args.validation_status or "not_run"),
        "next_decision": str(args.next_decision or "").strip(),
        "stop_allowed": False,
        "unsafe_claims": [
            "signed SoDEX live execution remains unproven",
            "private/account SoDEX WebSocket remains unvalidated",
        ],
    }


def _trace_paths_for_telemetry(*, settings: Any, track: str, run_session_id: str | None) -> list[Path]:
    base = settings.artifact_dir
    if run_session_id:
        pattern = f"*/workspaces/{run_session_id}/iterations/**/*_trace.json"
    elif track == "all":
        pattern = "*/workspaces/*/iterations/**/*_trace.json"
    else:
        pattern = f"{track}/workspaces/*/iterations/**/*_trace.json"
    return sorted(base.glob(pattern))


def _provider_metric_paths_for_telemetry(*, settings: Any, run_session_id: str | None) -> list[Path]:
    base = settings.artifact_dir / "provider_metrics"
    if run_session_id:
        jsonl_path = base / f"{run_session_id}.jsonl"
        if jsonl_path.exists():
            return [jsonl_path]
        latest_path = base / f"{run_session_id}.latest.json"
        return [latest_path] if latest_path.exists() else []
    jsonl_paths = sorted(base.glob("*.jsonl"))
    if jsonl_paths:
        return jsonl_paths
    return sorted(base.glob("*.latest.json"))


async def evidence_build_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    _require_sosovalue_config(settings)
    observed_at = datetime.now(UTC).isoformat()
    output = (
        resolve_path_from_root(args.output, root_dir=settings.root_dir)
        if args.output
        else settings.artifact_dir / "evidence" / "sosovalue_evidence.jsonl"
    )
    raw = json.loads(settings.sosovalue_config_path.read_text(encoding="utf-8"))
    api_key = settings.sosovalue_api_key_override or str((raw.get("system") or {}).get("api_key") or "").strip()
    client = SoSoValueClient(
        api_key=api_key,
        endpoints=SoSoValueEndpoints(
            openapi_base_url=settings.sosovalue_openapi_base_url,
            etf_base_url=settings.sosovalue_etf_base_url,
            news_base_url=settings.sosovalue_news_base_url,
        ),
        timeout_s=settings.sosovalue_timeout_s,
        retries=settings.sosovalue_retries,
    )
    try:
        currencies = await client.listed_currencies()
        currency_id = _sosovalue_currency_id(currencies, str(args.currency))
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
    summary = store.write_summary(summary_output, max_day_gap=1, top_links=int(args.summary_top_links))
    print(
        json.dumps(
            {
                "output": display_path(output, root_dir=settings.root_dir),
                "summary_output": display_path(summary_output, root_dir=settings.root_dir),
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
            indent=2,
            sort_keys=True,
        )
    )


def _sosovalue_currency_id(rows: list[dict[str, Any]], symbol: str) -> int | None:
    needle = str(symbol or "").strip().lower()
    for row in rows:
        if str(row.get("currencyName") or "").strip().lower() == needle:
            return int(row["currencyId"])
        if str(row.get("fullName") or "").strip().lower() == needle:
            return int(row["currencyId"])
    return None


def _deployment_eligible(
    *,
    summary: dict[str, Any],
    trial_context: dict[str, Any] | None,
) -> bool:
    return not _deployment_ineligible_reasons(summary=summary, trial_context=trial_context)


def _deployment_ineligible_reasons(
    *,
    summary: dict[str, Any],
    trial_context: dict[str, Any] | None,
) -> list[str]:
    summary = dict(summary or {})
    trial_context = dict(trial_context or {})
    reasons: list[str] = []
    if not bool(summary.get("passed")):
        reasons.append("summary_not_passed")
    if str(trial_context.get("fragility_label") or "").strip().lower() == "fragile":
        reasons.append("fragility_label_fragile")
    audit_total_return = summary.get("audit_total_return")
    if audit_total_return is not None and float(audit_total_return) < -0.02:
        reasons.append("audit_total_return_below_minus_2pct")
    fragility_pack = dict(trial_context.get("fragility_pack") or {})
    active_bar_count = fragility_pack.get("active_bar_count")
    if active_bar_count is not None and int(active_bar_count) < 72:
        reasons.append("active_bar_count_below_72")
    return reasons


def _sodex_preflight_report(env: dict[str, str] | None = None) -> dict[str, Any]:
    source = env if env is not None else os.environ
    api_key_name = str(source.get("SODEX_API_KEY_NAME") or "").strip()
    account_id = str(source.get("SODEX_ACCOUNT_ID") or "").strip()
    nonce_store = str(source.get("SODEX_NONCE_STORE_PATH") or "").strip()
    environment = str(source.get("SODEX_ENVIRONMENT") or "testnet").strip().lower()
    private_key_present = bool(str(source.get("SODEX_PRIVATE_KEY") or "").strip())
    missing: list[str] = []
    if not api_key_name:
        missing.append("SODEX_API_KEY_NAME")
    if not account_id:
        missing.append("SODEX_ACCOUNT_ID")
    else:
        try:
            if int(account_id) < 0:
                missing.append("SODEX_ACCOUNT_ID must be an unsigned integer")
        except ValueError:
            missing.append("SODEX_ACCOUNT_ID must be an unsigned integer")
    if not nonce_store:
        missing.append("SODEX_NONCE_STORE_PATH")
        nonce_store_status = {
            "ready": False,
            "path_present": False,
            "parent_writable": False,
            "file_writable": False,
            "parseable": False,
            "error": "SODEX_NONCE_STORE_PATH is required",
        }
    else:
        nonce_path = Path(nonce_store).expanduser()
        if not nonce_path.is_absolute():
            nonce_path = (Path.cwd() / nonce_path).resolve()
        parent = nonce_path.parent
        parent_exists = parent.exists()
        parent_writable = bool(parent_exists and os.access(parent, os.W_OK))
        file_writable = bool((not nonce_path.exists() and parent_writable) or os.access(nonce_path, os.W_OK))
        parseable = True
        nonce_error = None
        if nonce_path.exists():
            try:
                parsed = json.loads(nonce_path.read_text())
                if not isinstance(parsed, dict):
                    parseable = False
                    nonce_error = "nonce store must be a JSON object"
            except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
                parseable = False
                nonce_error = f"nonce store is not parseable JSON: {exc}"
        if not parent_exists:
            nonce_error = "nonce store parent directory does not exist"
        elif not parent_writable:
            nonce_error = "nonce store parent directory is not writable"
        elif not file_writable:
            nonce_error = "nonce store file is not writable"
        nonce_store_status = {
            "ready": bool(parent_exists and parent_writable and file_writable and parseable),
            "path_present": True,
            "parent_writable": parent_writable,
            "file_writable": file_writable,
            "parseable": parseable,
            "error": nonce_error,
        }
        if not nonce_store_status["ready"]:
            missing.append(f"SODEX_NONCE_STORE_PATH not ready: {nonce_store_status['error']}")
    if not private_key_present:
        missing.append("SODEX_PRIVATE_KEY")
    if environment not in {"mainnet", "testnet"}:
        missing.append("SODEX_ENVIRONMENT must be mainnet or testnet")
    mainnet_confirmation = str(source.get("SODEX_MAINNET_LIVE_WRITE_CONFIRMATION") or "").strip()
    testnet_passed = str(source.get("SODEX_TESTNET_PREFLIGHT_PASSED") or "").strip().lower()
    if environment == "mainnet":
        if testnet_passed not in {"1", "true", "yes"}:
            missing.append("SODEX_TESTNET_PREFLIGHT_PASSED must be true before mainnet")
        if mainnet_confirmation != "I_UNDERSTAND_MAINNET_RISK":
            missing.append("SODEX_MAINNET_LIVE_WRITE_CONFIRMATION must equal I_UNDERSTAND_MAINNET_RISK")
    return {
        "public_read_ready": True,
        "schema_pinned": True,
        "signed_path": {
            "ready": not missing,
            "environment": environment,
            "signer_ready": private_key_present,
            "signer_type": "evm-private-key" if private_key_present else None,
            "accountID_present": bool(account_id),
            "api_key_name_present": bool(api_key_name),
            "nonce_store_ready": bool(nonce_store_status["ready"]),
            "nonce_store": nonce_store_status,
            "testnet_preflight_passed": testnet_passed in {"1", "true", "yes"},
            "mainnet_confirmation_present": mainnet_confirmation == "I_UNDERSTAND_MAINNET_RISK",
            "missing_prerequisites": missing,
        },
        "live_write_allowed": not missing,
        "live_write_refusal_reason": None if not missing else "missing signed-path prerequisites",
        "access_plan": {
            "preferred_validation_environment": "testnet",
            "mainnet_warning": "Do not attempt signed mainnet writes until testnet/account preflight passes and operator confirms supported chain/deposit requirements.",
            "buildathon_access_request": "Request SoSoValue/SoDEX buildathon access early when missing API/account prerequisites block live validation.",
            "required_operator_inputs": [
                "SoDEX environment: testnet first, mainnet only after explicit operator confirmation",
                "SoDEX API key name",
                "SoDEX accountID",
                "nonce store path",
                "isolated EVM signer private key",
                "user address for private/account WebSocket probes",
            ],
        },
        "next_actions": [
            "prefer SODEX_ENVIRONMENT=testnet for first signed validation",
            *[f"set {name}" for name in missing if name.startswith("SODEX_")],
            "run siglab sodex-preflight --json again before any deploy/export/live attempt",
        ],
        "request_weight_budget_per_minute": SODEX_WEIGHT_BUDGET_PER_MINUTE,
        "documented_endpoint_weights": dict(sorted(SODEX_ENDPOINT_WEIGHTS.items())),
        "rate_limit_scope": {
            "scope": "per_ip",
            "local_scheduler_only": True,
            "operator_warning": (
                "SigLab's built-in SoDEX weight scheduler is process-local. "
                "Use an external shared limiter when multiple processes share one egress IP."
            ),
        },
        "supported_signed_actions": sorted(SUPPORTED_SODEX_SIGNED_ACTIONS),
        "unsupported_signed_actions": dict(UNSUPPORTED_SODEX_SIGNED_ACTIONS),
    }


def sodex_preflight_command(args: argparse.Namespace) -> None:
    report = _sodex_preflight_report()
    if getattr(args, "json", False):
        print(json.dumps(report, indent=2, sort_keys=True))
        return
    print(f"public_read_ready={report['public_read_ready']}")
    print(f"schema_pinned={report['schema_pinned']}")
    print(f"signed_path_ready={report['signed_path']['ready']}")
    print(f"environment={report['signed_path']['environment']}")
    if report["signed_path"]["missing_prerequisites"]:
        print("missing_prerequisites=" + ",".join(report["signed_path"]["missing_prerequisites"]))
    print(f"live_write_allowed={report['live_write_allowed']}")


async def valuechain_preflight_command(args: argparse.Namespace) -> None:
    rpc_url = str(args.rpc_url).rstrip("/")
    expected = int(args.expected_chain_id)
    report: dict[str, Any] = {
        "rpc_url": rpc_url,
        "expected_chain_id": expected,
        "source": "https://sodex.com/documentation/user-guide/faq/how-do-i-add-the-valuechain-network",
        "ready": False,
    }
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.post(
                rpc_url,
                json={"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []},
                headers={"Content-Type": "application/json"},
            )
        report["http_status"] = int(response.status_code)
        payload = response.json()
        report["response_shape"] = sorted(payload.keys()) if isinstance(payload, dict) else type(payload).__name__
        chain_hex = payload.get("result") if isinstance(payload, dict) else None
        report["chain_id_hex"] = chain_hex
        report["chain_id"] = int(str(chain_hex), 16) if isinstance(chain_hex, str) and chain_hex.startswith("0x") else None
        report["ready"] = report["chain_id"] == expected
        if not report["ready"]:
            report["missing_or_wrong"] = "ValueChain RPC did not return the documented chain ID"
    except (httpx.HTTPError, TypeError, ValueError) as exc:
        report["error_class"] = type(exc).__name__
        report["error"] = str(exc)
    if getattr(args, "json", False):
        print(json.dumps(report, indent=2, sort_keys=True))
        return
    status = "READY" if report.get("ready") else "NOT READY"
    print(f"ValueChain RPC {status}: chain_id={report.get('chain_id')} expected={expected} rpc={rpc_url}")


async def sodex_ws_probe_command(args: argparse.Namespace) -> None:
    params: dict[str, Any] = {"channel": str(args.channel)}
    if args.symbol:
        params["symbol"] = str(args.symbol)
    if args.user_address:
        params["user"] = str(args.user_address)
    if args.account_id is not None:
        params["accountID"] = int(args.account_id)
    report: dict[str, Any] = {
        "environment": args.environment,
        "market": args.market,
        "params": params,
        "live_write": False,
        "signed": False,
        "ready": False,
    }
    client = SoDEXWebSocketClient(
        environment=args.environment,
        market=args.market,
        idle_timeout_s=float(args.timeout_seconds),
        pong_timeout_s=min(5.0, float(args.timeout_seconds)),
        max_reconnects=0,
    )
    try:
        ack = await client.subscribe(params, request_id=1)
        report["subscribe_ack"] = ack
        try:
            update = await client.recv_update(timeout_s=float(args.timeout_seconds))
        except SoDEXWebSocketError as exc:
            report["update_error_class"] = type(exc).__name__
            report["update_error"] = str(exc)
        else:
            report["first_update_keys"] = sorted(update.keys())
            report["first_update_channel"] = update.get("channel")
            report["first_update_type"] = update.get("type")
            if args.evidence_output:
                settings = load_settings()
                evidence_output = resolve_path_from_root(args.evidence_output, root_dir=settings.root_dir)
                records = sodex_ws_evidence(
                    update,
                    observed_at=datetime.now(UTC).isoformat(),
                    evidence_path=f"sodex/ws/{args.market}/{args.channel}",
                )
                store = EvidenceStore(evidence_output)
                appended = store.append_many(records)
                summary_output = evidence_output.with_suffix(".summary.json")
                summary = store.write_summary(summary_output)
                report["evidence_output"] = str(evidence_output)
                report["evidence_summary_output"] = str(summary_output)
                report["evidence_records_appended"] = appended
                report["evidence_summary_record_count"] = summary["record_count"]
        report["ready"] = True
    except (SoDEXWebSocketError, OSError, TypeError, ValueError) as exc:
        report["error_class"] = type(exc).__name__
        report["error"] = str(exc)
    finally:
        report["snapshot"] = client.snapshot()
        await client.close()
    if getattr(args, "json", False):
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
        return
    print(json.dumps(report, indent=2, sort_keys=True, default=str))


def _parse_sodex_enum(value: Any, aliases: dict[str, int], field_name: str) -> int:
    raw = str(value).strip()
    if raw.isdigit():
        parsed = int(raw)
        if parsed in set(aliases.values()):
            return parsed
    normalized = raw.upper().replace("-", "_")
    if normalized in aliases:
        return aliases[normalized]
    accepted = ", ".join([*aliases.keys(), *[str(v) for v in sorted(set(aliases.values()))]])
    raise SystemExit(f"--{field_name.replace('_', '-')} must be one of: {accepted}")


def _sodex_preview_payload(args: argparse.Namespace) -> dict[str, Any]:
    if args.kind == "new-order":
        order = perps_order_item(
            cl_ord_id=str(args.cl_ord_id),
            modifier=_parse_sodex_enum(args.modifier, SODEX_MODIFIER_ALIASES, "modifier"),
            side=_parse_sodex_enum(args.side, SODEX_SIDE_ALIASES, "side"),
            order_type=_parse_sodex_enum(args.order_type, SODEX_ORDER_TYPE_ALIASES, "order_type"),
            time_in_force=_parse_sodex_enum(args.time_in_force, SODEX_TIME_IN_FORCE_ALIASES, "time_in_force"),
            price=args.price,
            quantity=args.quantity,
            funds=args.funds,
            reduce_only=bool(args.reduce_only),
            position_side=_parse_sodex_enum(args.position_side, SODEX_POSITION_SIDE_ALIASES, "position_side"),
        )
        body = perps_new_order_body(account_id=int(args.account_id), symbol_id=int(args.symbol_id), orders=[order])
        request = SoDEXSignedRequest(method="POST", path="/trade/orders", body=body, weight=1)
    elif args.kind == "cancel-order":
        cancel = perps_cancel_item(
            symbol_id=int(args.symbol_id),
            order_id=args.order_id,
            cl_ord_id=args.orig_cl_ord_id,
        )
        body = perps_cancel_order_body(account_id=int(args.account_id), cancels=[cancel])
        request = SoDEXSignedRequest(method="DELETE", path="/trade/orders", body=body, weight=1)
    elif args.kind == "schedule-cancel":
        body = perps_schedule_cancel_body(
            account_id=int(args.account_id),
            scheduled_timestamp=args.scheduled_timestamp,
        )
        request = SoDEXSignedRequest(method="POST", path="/trade/orders/schedule-cancel", body=body, weight=1)
    elif args.kind == "update-margin":
        if args.amount is None:
            raise SystemExit("--amount is required for --kind update-margin")
        body = perps_update_margin_body(
            account_id=int(args.account_id),
            symbol_id=int(args.symbol_id),
            amount=str(args.amount),
        )
        request = SoDEXSignedRequest(method="POST", path="/trade/margin", body=body, weight=1)
    else:
        body = perps_update_leverage_body(
            account_id=int(args.account_id),
            symbol_id=int(args.symbol_id),
            leverage=int(args.leverage),
            margin_mode=_parse_sodex_enum(args.margin_mode, SODEX_MARGIN_MODE_ALIASES, "margin_mode"),
        )
        request = SoDEXSignedRequest(method="POST", path="/trade/leverage", body=body, weight=1)
    signature_input = build_signature_input(
        domain=request.domain,
        account_id=int(args.account_id),
        body=request.body,
        nonce=int(args.nonce),
    )
    return {
        "method": request.method,
        "path": request.path,
        "domain": request.domain,
        "weight": request.weight,
        "canonical_body": canonical_json(http_body_from_action_payload(request.body)),
        "canonical_signing_payload": canonical_json(request.body),
        "signature_input": signature_input,
        "signature": None,
        "submitted": False,
    }


def sodex_preview_command(args: argparse.Namespace) -> None:
    print(json.dumps(_sodex_preview_payload(args), indent=2, sort_keys=True))


async def run_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    if getattr(args, "max_call_estimated_credits", None) is not None:
        settings.bai_max_call_credits = float(args.max_call_estimated_credits)
    _require_sosovalue_config(settings)
    settings.ensure_runtime_directories()
    burn_in_iterations = int(getattr(args, "burn_in_iterations", 0) or 0)
    max_runtime_seconds = getattr(args, "max_runtime_seconds", None)
    if getattr(args, "max_total_cost", None) is not None:
        raise SystemExit(
            "--max-total-cost is not enforced yet because provider token/cost telemetry is not available; "
            "omit it or add real cost accounting first"
        )
    loop_policy = {
        "max_total_cost": getattr(args, "max_total_cost", None),
        "max_total_credits": getattr(args, "max_total_credits", None),
        "max_call_estimated_credits": getattr(args, "max_call_estimated_credits", None),
        "max_provider_errors": getattr(args, "max_provider_errors", None),
        "max_consecutive_no_improvement": getattr(args, "max_consecutive_no_improvement", None),
        "max_consecutive_crashes": getattr(args, "max_consecutive_crashes", None),
        "cooldown_seconds_on_429": float(getattr(args, "cooldown_seconds_on_429", 0.0) or 0.0),
        "provider_fallback_on_quota": bool(getattr(args, "provider_fallback_on_quota", False)),
        "stop_on_live_surface_unavailable": bool(getattr(args, "stop_on_live_surface_unavailable", False)),
        "resume_safe_check": bool(getattr(args, "resume_safe_check", False)),
        "max_runtime_semantics": "between_iterations_cooperative",
    }
    runner_label = str(getattr(args, "agent_label", None) or getattr(args, "runner_label", None) or "siglab_harness")
    run_label = str(getattr(args, "run_label", None) or "").strip() or None
    selected_families = _parse_family_scope(args.family, args.families)
    custom_symbols = _parse_symbol_override(getattr(args, "symbols", None))
    resume_run_id = str(getattr(args, "resume_run", "") or "").strip() or None
    use_historical_seeds = (
        bool(getattr(args, "use_historical_seeds"))
        if getattr(args, "use_historical_seeds", None) is not None
        else bool(getattr(settings, "use_historical_seeds", False))
    )
    if resume_run_id and selected_families:
        raise SystemExit("--resume-run cannot be combined with --family/--families")
    memory_scope = _resolve_memory_scope(
        explicit=getattr(args, "memory_scope", None),
        default=getattr(settings, "memory_scope", "session_local"),
    )
    if selected_families and args.track == "all":
        raise SystemExit("--family/--families require a single --track value")
    if args.track == "all" and not resume_run_id:
        raise SystemExit("Workspace-flow phase 1 supports only --track trend_signals")

    lake = ParquetLake(settings.data_lake_dir)
    provider = MarketDataProvider(settings, lake)
    custom_symbols = await _validate_symbol_override(
        provider=provider,
        custom_symbols=custom_symbols,
    )
    ancestry = LineageStore(settings.ancestry_db_path)
    claude = ClaudeClient(settings)
    web_researcher = WebResearcher(settings, lake)
    hypothesis_sandbox = HypothesisSandbox(settings, lake, provider)
    mutator = SpecMutator(settings, claude)
    evaluator = ResearchEvaluator(settings, provider)
    workspace_builder = WorkspaceBuilder(
        settings=settings,
        ancestry=ancestry,
        mutator=mutator,
    )
    planner_runner = ResearchPlannerRunner(
        settings=settings,
        claude=claude,
        hypothesis_sandbox=hypothesis_sandbox,
        web_researcher=web_researcher,
        workspace_builder=workspace_builder,
    )
    writer_runner = SpecWriterRunner(
        settings=settings,
        claude=claude,
        mutator=mutator,
        hypothesis_sandbox=hypothesis_sandbox,
    )
    optimizer_runner = OptunaOptimizerRunner(
        settings=settings,
        evaluator=evaluator,
        mutator=mutator,
        ancestry=ancestry,
    )
    reflector_runner = ReflectionRunner(
        settings=settings,
        claude=claude,
    )

    tracks = (
        list(settings.tracks)
        if args.track == "all"
        else [canonical_track_name(args.track) or args.track]
    )
    resume_info: dict[str, Any] | None = None
    if resume_run_id is not None:
        resume_info = _resolve_resume_run(
            settings=settings,
            run_session_id=resume_run_id,
        )
        resume_track = str(resume_info.get("track") or "")
        if args.track != "all" and tracks != [resume_track]:
            raise SystemExit(
                f"--track {args.track!r} does not match resumed run track `{resume_track}`"
            )
        tracks = [resume_track]
        if getattr(args, "memory_scope", None) is not None and memory_scope != resume_info["memory_scope"]:
            raise SystemExit(
                f"--memory-scope {memory_scope!r} does not match resumed run memory scope `{resume_info['memory_scope']}`"
            )
        resume_symbols = list(resume_info.get("custom_symbols") or [])
        if custom_symbols is not None and custom_symbols != resume_symbols:
            raise SystemExit(
                f"--symbols {custom_symbols!r} do not match resumed run symbols `{resume_symbols}`"
            )
        custom_symbols = resume_symbols or None
        if loop_policy["resume_safe_check"]:
            _resume_safe_check(settings=settings, run_session_id=resume_run_id)
        if (
            getattr(args, "use_historical_seeds", None) is not None
            and use_historical_seeds != bool(resume_info.get("use_historical_seeds"))
        ):
            raise SystemExit(
                "--use-historical-seeds does not match resumed run historical seed setting"
            )
        use_historical_seeds = bool(resume_info.get("use_historical_seeds"))
        memory_scope = str(resume_info["memory_scope"])
        if burn_in_iterations > 0:
            raise SystemExit("--resume-run cannot be combined with --burn-in-iterations")
    if tracks != ["trend_signals"]:
        raise SystemExit("Workspace-flow phase 1 supports only --track trend_signals")
    population_size = args.population_size or settings.population_size
    run_session_id = resume_run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    if resume_info is None:
        workspace_session = workspace_builder.initialize_session(
            track="trend_signals",
            run_session_id=run_session_id,
            family_scope=selected_families,
            memory_scope=memory_scope,
            custom_symbols=custom_symbols,
            use_historical_seeds=use_historical_seeds,
        )
        next_iteration = 1
    else:
        selected_families = list(resume_info.get("families") or []) or None
        workspace_session = workspace_builder.resume_session(
            track="trend_signals",
            run_session_id=run_session_id,
            families=list(selected_families or mutator._allowed_families("trend_signals", family=None)),
            memory_scope=memory_scope,
            custom_symbols=custom_symbols,
            use_historical_seeds=use_historical_seeds,
        )
        next_iteration = int(resume_info.get("next_iteration") or 1)
        print(
            f"[run] resuming `{run_session_id}` from iteration {next_iteration} "
            f"memory_scope={memory_scope}"
        )
    workspace_hooks = WorkspaceHooks(
        builder=workspace_builder,
        session=workspace_session,
    )

    try:
        if burn_in_iterations > 0:
            print(f"[run] starting burn-in phase iterations={burn_in_iterations}")
            next_iteration = await _run_trend_signals_iterations(
                settings=settings,
                claude=claude,
                provider=provider,
                ancestry=ancestry,
                mutator=mutator,
                evaluator=evaluator,
                web_researcher=web_researcher,
                hypothesis_sandbox=hypothesis_sandbox,
                population_size=population_size,
                family_scope=selected_families,
                skip_llm=True,
                iterations=burn_in_iterations,
                start_iteration=next_iteration,
                phase_label="burn_in",
                run_session_id=run_session_id,
                runner_label=runner_label,
                run_label=run_label,
                workspace_session=workspace_session,
                workspace_builder=workspace_builder,
                workspace_hooks=workspace_hooks,
                planner_runner=planner_runner,
                writer_runner=writer_runner,
                optimizer_runner=optimizer_runner,
                reflector_runner=reflector_runner,
                memory_scope=memory_scope,
                custom_symbols=custom_symbols,
                use_historical_seeds=use_historical_seeds,
                max_runtime_seconds=max_runtime_seconds,
                loop_policy=loop_policy,
            )
        next_iteration = await _run_trend_signals_iterations(
            settings=settings,
            claude=claude,
            provider=provider,
            ancestry=ancestry,
            mutator=mutator,
            evaluator=evaluator,
            web_researcher=web_researcher,
            hypothesis_sandbox=hypothesis_sandbox,
            population_size=population_size,
            family_scope=selected_families,
            skip_llm=args.skip_llm,
            iterations=args.iterations,
            start_iteration=next_iteration,
            phase_label="main",
            run_session_id=run_session_id,
            runner_label=runner_label,
            run_label=run_label,
            workspace_session=workspace_session,
            workspace_builder=workspace_builder,
            workspace_hooks=workspace_hooks,
            planner_runner=planner_runner,
            writer_runner=writer_runner,
            optimizer_runner=optimizer_runner,
            reflector_runner=reflector_runner,
            memory_scope=memory_scope,
            custom_symbols=custom_symbols,
            use_historical_seeds=use_historical_seeds,
            max_runtime_seconds=max_runtime_seconds,
            loop_policy=loop_policy,
        )
    finally:
        await web_researcher.close()
        await provider.close()
        await claude.close()


async def _run_trend_signals_iterations(
    *,
    settings: Any,
    claude: ClaudeClient,
    provider: MarketDataProvider,
    ancestry: LineageStore,
    mutator: SpecMutator,
    evaluator: ResearchEvaluator,
    web_researcher: WebResearcher,
    hypothesis_sandbox: HypothesisSandbox,
    population_size: int,
    family_scope: str | list[str] | None,
    skip_llm: bool,
    iterations: int,
    start_iteration: int,
    phase_label: str,
    run_session_id: str,
    runner_label: str,
    run_label: str | None,
    workspace_session: Any,
    workspace_builder: WorkspaceBuilder,
    workspace_hooks: WorkspaceHooks,
    planner_runner: ResearchPlannerRunner,
    writer_runner: SpecWriterRunner,
    optimizer_runner: OptunaOptimizerRunner,
    reflector_runner: ReflectionRunner,
    memory_scope: str,
    custom_symbols: list[str] | None,
    use_historical_seeds: bool,
    max_runtime_seconds: float | None,
    loop_policy: dict[str, Any],
) -> int:
    iteration_iter = count(start_iteration) if iterations == 0 else range(start_iteration, start_iteration + iterations)
    last_iteration = start_iteration
    track = "trend_signals"
    deadline = time.monotonic() + float(max_runtime_seconds) if max_runtime_seconds else None
    loop_started_at = time.monotonic()
    provider_errors = 0
    consecutive_no_improvement = 0
    consecutive_crashes = 0

    for iteration_number in iteration_iter:
        credit_stop = _credit_budget_stop_payload(
            claude=claude,
            loop_policy=loop_policy,
            run_label=run_label or run_session_id,
            runner_label=runner_label,
            phase_label=phase_label,
            next_iteration=iteration_number,
        )
        if credit_stop is not None:
            _write_provider_metrics_artifact(
                settings=settings,
                run_session_id=run_session_id,
                iteration_number=iteration_number,
                phase_label=phase_label,
                reason="policy_stop:max_total_credits",
                claude=claude,
            )
            _write_loop_stop(
                settings=settings,
                run_session_id=run_session_id,
                reason="policy_stop:max_total_credits",
                payload=credit_stop,
            )
            print(
                f"[run:{phase_label}] max_total_credits reached before iteration={iteration_number} "
                f"credits={credit_stop['credits_estimate']}"
            )
            break
        if deadline is not None and time.monotonic() >= deadline:
            _write_provider_metrics_artifact(
                settings=settings,
                run_session_id=run_session_id,
                iteration_number=iteration_number,
                phase_label=phase_label,
                reason="policy_stop:max_runtime_seconds",
                claude=claude,
            )
            _write_loop_stop(
                settings=settings,
                run_session_id=run_session_id,
                reason="policy_stop:max_runtime_seconds",
                payload={
                    "run_label": run_label or run_session_id,
                    "runner_label": runner_label,
                    "phase_label": phase_label,
                    "next_iteration": iteration_number,
                    "elapsed_runtime_seconds": round(time.monotonic() - loop_started_at, 3),
                    "max_runtime_seconds": max_runtime_seconds,
                    "runtime_guard_semantics": "between_iterations_cooperative",
                    "loop_policy": loop_policy,
                },
            )
            print(f"[run:{phase_label}] max_runtime_seconds reached before iteration={iteration_number}")
            break
        last_iteration = iteration_number
        print(f"[run:{phase_label}] iteration={iteration_number}")
        scope_kwargs = _ancestry_scope_kwargs(
            memory_scope=memory_scope,
            run_session_id=run_session_id,
        )
        seed_specs = _load_seed_specs_for_run(
            mutator=mutator,
            track=track,
            family_scope=family_scope,
            custom_symbols=custom_symbols,
            use_historical_seeds=use_historical_seeds,
        )
        recent_rows = ancestry.recent(track, limit=500, run_session_id=scope_kwargs.get("run_session_id"))
        if skip_llm:
            parent = pick_deterministic_parent(
                track=track,
                ancestry=ancestry,
                seed_specs=seed_specs,
                iteration_number=iteration_number,
                run_session_id=scope_kwargs.get("run_session_id"),
            )
        else:
            parent = pick_parent(
                track,
                ancestry,
                seed_specs,
                run_session_id=scope_kwargs.get("run_session_id"),
            )
        parent_hash = parent.strategy_hash()
        best = ancestry.best(track, **scope_kwargs)
        print(
            f"[{track}] parent={parent.family} {parent_hash} recent_best={best['aggregate_score']:.4f}"
            if best is not None
            else f"[{track}] parent={parent.family} {parent_hash}"
        )
        run_context = {
            "run_session_id": run_session_id,
            "runner_label": str(runner_label or "siglab_harness"),
            "run_label": run_label or run_session_id,
            "phase_label": phase_label,
            "iteration_number": int(iteration_number),
            "deterministic": bool(skip_llm),
            "llm_phase": not bool(skip_llm),
            "force_novelty": False,
            "memory_scope": memory_scope,
            "custom_symbols": list(custom_symbols or []),
            "use_historical_seeds": bool(use_historical_seeds),
        }
        provider.begin_iteration_bundle(track=track, parent=parent)
        try:
            if skip_llm:
                market_summary = _minimal_research_summary(
                    track=track,
                    parent=parent,
                    provider=provider,
                    web_researcher=web_researcher,
                    run_context=run_context,
                )
            else:
                market_summary = await provider.build_research_summary(track, parent)
                market_summary["external_research"] = _tool_only_external_research(
                    web_researcher=web_researcher
                )
                market_summary["run_context"] = run_context
            iteration_paths = workspace_builder.update_iteration(
                session=workspace_session,
                parent=parent,
                iteration_number=iteration_number,
                phase_label=phase_label,
                force_novelty=bool(run_context["force_novelty"]),
                market_summary=market_summary,
            )
            current_state = dict(iteration_paths.get("session_state") or {})

            if skip_llm:
                specs = await mutator.propose(
                    track=track,
                    parent=parent,
                    research_summary=market_summary,
                    recent_results=[],
                    memory_packet={},
                    population_size=population_size,
                    skip_llm=True,
                    family=family_scope,
                    exclude_hashes=set(),
                    llm_tools=[],
                    deterministic_recent_rows=recent_rows,
                    deterministic_seed_specs=seed_specs,
                )
                planner_result = None
                writer_result = None
                optimization_result = None
            else:
                try:
                    planner_result = await planner_runner.run(
                        session=workspace_session,
                        iteration_number=iteration_number,
                        parent=parent,
                        market_bundle=dict(market_summary.get("market_bundle") or {}),
                        iteration_paths=iteration_paths,
                    )
                except (LLMProviderError, RuntimeError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
                    reason = f"{type(exc).__name__}: {exc}"
                    print(f"[{track}] planner_failed iteration={iteration_number} reason={reason}")
                    if isinstance(exc, LLMProviderError) or "LLM" in reason:
                        provider_errors += 1
                        max_provider_errors = loop_policy.get("max_provider_errors")
                        if max_provider_errors is not None and provider_errors >= int(max_provider_errors):
                            _write_loop_stop(
                                settings=settings,
                                run_session_id=run_session_id,
                                reason="policy_stop:max_provider_errors",
                                payload={"run_label": run_label or run_session_id, "runner_label": runner_label, "phase_label": phase_label, "provider_errors": provider_errors, "last_error": reason, "loop_policy": loop_policy},
                            )
                            print(f"[{track}] provider_error_limit_reached count={provider_errors}")
                            return iteration_number + 1
                    else:
                        consecutive_crashes += 1
                        max_crashes = loop_policy.get("max_consecutive_crashes")
                        if max_crashes is not None and consecutive_crashes >= int(max_crashes):
                            _write_loop_stop(
                                settings=settings,
                                run_session_id=run_session_id,
                                reason="policy_stop:max_consecutive_crashes",
                                payload={"run_label": run_label or run_session_id, "runner_label": runner_label, "phase_label": phase_label, "consecutive_crashes": consecutive_crashes, "last_error": reason, "loop_policy": loop_policy},
                            )
                            return iteration_number + 1
                    continue
                target_family = str(planner_result.frontmatter.get("target_family") or parent.family)
                base_spec_payload = _base_spec_payload_for_family(
                    track=track,
                    family=target_family,
                    parent=parent,
                    ancestry=ancestry,
                    mutator=mutator,
                    run_session_id=scope_kwargs.get("run_session_id"),
                    custom_symbols=custom_symbols,
                    use_historical_seeds=use_historical_seeds,
                )
                workspace_builder.store_evidence_cache(
                    session=workspace_session,
                    parent_hash=parent_hash,
                    bundle_id=str(current_state.get("bundle_id") or ""),
                    open_question=str(current_state.get("open_question") or ""),
                    lesson_refs=list(current_state.get("selected_lesson_refs") or []),
                    probe_refs=list(planner_result.tool_refs),
                    experiment_refs=list(planner_result.evidence_paths),
                )
                writer_result = await writer_runner.run(
                    session=workspace_session,
                    research_note_path=planner_result.research_note_path,
                    iteration_paths=iteration_paths,
                    parent=parent,
                    base_spec_payload=base_spec_payload,
                )
                if not writer_result.accepted:
                    print(
                        f"[{track}] planner/writer preflight failed in iteration {iteration_number}: "
                        f"{writer_result.failure_reason or 'unknown failure'}"
                    )
                    try:
                        planner_result = await planner_runner.run(
                            session=workspace_session,
                            iteration_number=iteration_number,
                            parent=parent,
                            market_bundle=dict(market_summary.get("market_bundle") or {}),
                            iteration_paths=iteration_paths,
                            repair_feedback=dict(writer_result.failure_packet or {}),
                            previous_note_path=planner_result.research_note_path,
                        )
                    except (RuntimeError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
                        reason = f"{type(exc).__name__}: {exc}"
                        print(f"[{track}] planner_repair_failed iteration={iteration_number} reason={reason}")
                        if isinstance(exc, LLMProviderError) or "LLM" in reason:
                            provider_errors += 1
                            max_provider_errors = loop_policy.get("max_provider_errors")
                            if max_provider_errors is not None and provider_errors >= int(max_provider_errors):
                                _write_loop_stop(
                                    settings=settings,
                                    run_session_id=run_session_id,
                                    reason="policy_stop:max_provider_errors",
                                    payload={"run_label": run_label or run_session_id, "runner_label": runner_label, "phase_label": phase_label, "provider_errors": provider_errors, "last_error": reason, "loop_policy": loop_policy},
                                )
                                print(f"[{track}] provider_error_limit_reached count={provider_errors}")
                                return iteration_number + 1
                        else:
                            consecutive_crashes += 1
                            max_crashes = loop_policy.get("max_consecutive_crashes")
                            if max_crashes is not None and consecutive_crashes >= int(max_crashes):
                                _write_loop_stop(
                                    settings=settings,
                                    run_session_id=run_session_id,
                                    reason="policy_stop:max_consecutive_crashes",
                                    payload={"run_label": run_label or run_session_id, "runner_label": runner_label, "phase_label": phase_label, "consecutive_crashes": consecutive_crashes, "last_error": reason, "loop_policy": loop_policy},
                                )
                                return iteration_number + 1
                        continue
                    writer_result = await writer_runner.run(
                        session=workspace_session,
                        research_note_path=planner_result.research_note_path,
                        iteration_paths=iteration_paths,
                        parent=parent,
                        base_spec_payload=base_spec_payload,
                    )
                if not writer_result.accepted or writer_result.spec_payload is None:
                    reason = str(writer_result.failure_reason or "writer_rejected_spec")
                    if "LLM" in reason or "quota" in reason.lower() or "rate limited" in reason.lower():
                        provider_errors += 1
                    max_provider_errors = loop_policy.get("max_provider_errors")
                    if max_provider_errors is not None and provider_errors >= int(max_provider_errors):
                        _write_loop_stop(
                            settings=settings,
                            run_session_id=run_session_id,
                            reason="policy_stop:max_provider_errors",
                            payload={"run_label": run_label or run_session_id, "runner_label": runner_label, "phase_label": phase_label, "provider_errors": provider_errors, "last_error": reason, "loop_policy": loop_policy},
                        )
                        print(f"[{track}] provider_error_limit_reached count={provider_errors}")
                        return iteration_number + 1
                    print(
                        f"[{track}] llm_proposal_failed iteration={iteration_number} "
                        f"reason={writer_result.failure_reason or 'writer_rejected_spec'}"
                    )
                    continue
                incumbent_detail = _incumbent_detail(
                    ancestry=ancestry,
                    track=track,
                    run_session_id=scope_kwargs.get("run_session_id"),
                )
                try:
                    optimization_result = await optimizer_runner.run(
                        session=workspace_session,
                        base_payload=dict(writer_result.base_spec_payload or base_spec_payload),
                        spec_payload=dict(writer_result.spec_payload),
                        iteration_paths=iteration_paths,
                        incumbent_summary=(
                            dict(incumbent_detail.get("summary") or {})
                            if incumbent_detail is not None
                            else None
                        ),
                    )
                except (RuntimeError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
                    print(
                        f"[{track}] optimizer_failed iteration={iteration_number} "
                        f"reason={type(exc).__name__}: {exc}"
                    )
                    consecutive_crashes += 1
                    max_crashes = loop_policy.get("max_consecutive_crashes")
                    if max_crashes is not None and consecutive_crashes >= int(max_crashes):
                        _write_loop_stop(
                            settings=settings,
                            run_session_id=run_session_id,
                            reason="policy_stop:max_consecutive_crashes",
                            payload={"run_label": run_label or run_session_id, "runner_label": runner_label, "phase_label": phase_label, "consecutive_crashes": consecutive_crashes, "last_error": f"{type(exc).__name__}: {exc}", "loop_policy": loop_policy},
                        )
                        return iteration_number + 1
                    continue
                optimized_payload = dict(optimization_result.spec_payload)
                iteration_paths["spec_json_path"].write_text(
                    json.dumps(optimized_payload, indent=2, ensure_ascii=True, default=str)
                )
                validated = SignalSpec.from_dict(optimized_payload)
                if validated.strategy_hash() in {
                    row["spec_hash"] for row in ancestry.recent(track, limit=500, run_session_id=scope_kwargs.get("run_session_id"))
                }:
                    print(f"[{track}] duplicate spec {validated.strategy_hash()} skipped")
                    continue
                specs = [validated]
                trial_context = {
                    "structure_spec_path": str(iteration_paths.get("structure_spec_path")),
                    "base_spec_path": str(iteration_paths.get("base_spec_path")),
                    "spec_patch_path": str(iteration_paths.get("spec_patch_path")),
                    "spec_after_patch_path": str(iteration_paths.get("spec_after_patch_path")),
                    "optuna_space_path": str(iteration_paths.get("optuna_space_path")),
                    "optuna_trials_path": str(iteration_paths.get("optuna_trials_path")),
                    "optuna_best_path": str(iteration_paths.get("optuna_best_path")),
                    "base_spec_hash": (
                        SignalSpec.from_dict(
                            dict(writer_result.base_spec_payload or base_spec_payload)
                        ).strategy_hash()
                        if dict(writer_result.base_spec_payload or base_spec_payload)
                        else None
                    ),
                    "writer_spec_hash": SignalSpec.from_dict(
                        dict(writer_result.spec_payload)
                    ).strategy_hash(),
                    "optimized_spec_hash": validated.strategy_hash(),
                    "patch_summary": list(writer_result.patch_summary or []),
                    "optimized_param_summary": summarize_patch(
                        build_spec_patch(
                            base_payload=dict(writer_result.spec_payload),
                            target_payload=optimized_payload,
                        )
                    ),
                    "score_diagnosis": dict(optimization_result.score_diagnosis or {}),
                    "optuna_trial_count": int(optimization_result.trial_count),
                    "optuna_best_params": dict(optimization_result.best_params or {}),
                    "fragility_penalty": optimization_result.fragility_penalty,
                    "deployment_score": optimization_result.deployment_score,
                    "fragility_pack": dict(optimization_result.fragility_pack or {}),
                    "stability_pack": dict(optimization_result.stability_pack or {}),
                    "audit_alignment": summarize_generalization(
                        optimization_result.best_summary,
                        optuna_space=dict(optimization_result.optuna_space or {}),
                        tuned_params=dict(optimization_result.best_params or {}),
                        stability_pack=optimization_result.stability_pack,
                    ).get("audit_alignment"),
                    "fragility_label": summarize_generalization(
                        optimization_result.best_summary,
                        optuna_space=dict(optimization_result.optuna_space or {}),
                        tuned_params=dict(optimization_result.best_params or {}),
                        stability_pack=optimization_result.stability_pack,
                    ).get("fragility_label"),
                }

            if not specs:
                print(f"[{track}] no new spec generated in iteration {iteration_number}")
                continue

            best_passing: dict[str, Any] | None = None
            best_passing_trial_context: dict[str, Any] | None = None
            passed_not_deployable = 0
            deployment_ineligible_reasons: Counter[str] = Counter()
            for spec in specs:
                try:
                    evaluation = await evaluator.evaluate(
                        spec,
                        fast_mode=bool(skip_llm),
                    )
                except (OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError) as exc:
                    print(
                        f"[{track}] {spec.family} {spec.strategy_hash()} "
                        f"failed={type(exc).__name__}: {exc}"
                    )
                    continue

                lesson_card_path = (
                    workspace_session.cards_dir / "reflections" / f"{evaluation['spec_hash']}.md"
                )
                research_summary = dict(market_summary)
                research_summary["run_context"] = dict(run_context)
                research_summary["workspace"] = {
                    "root": str(workspace_session.root),
                    "iteration_dir": str(iteration_paths["iteration_dir"]),
                    "research_note_path": str(iteration_paths.get("research_note_path")),
                    "planner_contract_path": str(iteration_paths.get("planner_contract_path")),
                    "structure_spec_path": str(iteration_paths.get("structure_spec_path")),
                    "base_spec_path": str(iteration_paths.get("base_spec_path")),
                    "spec_patch_path": str(iteration_paths.get("spec_patch_path")),
                    "spec_after_patch_path": str(iteration_paths.get("spec_after_patch_path")),
                    "spec_json_path": str(iteration_paths.get("spec_json_path")),
                    "optuna_space_path": str(iteration_paths.get("optuna_space_path")),
                    "optuna_trials_path": str(iteration_paths.get("optuna_trials_path")),
                    "optuna_best_path": str(iteration_paths.get("optuna_best_path")),
                    "planner_trace_path": str(iteration_paths.get("planner_trace_path")),
                    "writer_trace_path": str(iteration_paths.get("writer_trace_path")),
                    "reflector_trace_path": str(iteration_paths.get("reflector_trace_path")),
                    "lesson_card_path": str(lesson_card_path),
                }
                if not skip_llm:
                    evaluated_trial_context = dict(trial_context)
                    evaluated_trial_context.update(
                        summarize_return_attribution(
                            evaluation.get("summary"),
                            evaluation.get("canonical_run"),
                        )
                    )
                    evaluated_trial_context.update(
                        summarize_generalization(
                            evaluation.get("summary"),
                            evaluation=evaluation,
                            optuna_space=dict(optimization_result.optuna_space or {}) if optimization_result is not None else None,
                            tuned_params=dict(optimization_result.best_params or {}) if optimization_result is not None else None,
                            stability_pack=dict(optimization_result.stability_pack or {}) if optimization_result is not None else {},
                        )
                    )
                    _stability_pack = evaluated_trial_context.get("stability_pack")
                    _stability_pack_dict = _stability_pack if isinstance(_stability_pack, dict) else {}
                    evaluated_trial_context["stability_status"] = _stability_pack_dict.get("status")
                    evaluated_trial_context["stability_pass_fraction"] = _stability_pack_dict.get("passed_fraction")
                    evaluated_trial_context["motif_audit_streak"] = _motif_audit_streak(
                        ancestry=ancestry,
                        track=track,
                        spec_payload=dict(evaluation.get("spec") or {}),
                        run_session_id=scope_kwargs.get("run_session_id"),
                    )
                    research_summary["trial"] = evaluated_trial_context
                artifact_path = _write_artifact(settings, track, evaluation)
                ancestry.record(
                    evaluation=evaluation,
                    parent_hash=parent_hash,
                    research_summary=research_summary,
                    artifact_path=str(artifact_path),
                )
                experiment_card_ref = workspace_hooks.after_experiment(
                    spec_hash=evaluation["spec_hash"],
                    iteration_number=iteration_number,
                )

                if not skip_llm:
                    reflection_packet = _reflection_evaluation_packet(
                        ancestry=ancestry,
                        evaluation=evaluation,
                        parent_hash=parent_hash,
                        experiment_card_ref=experiment_card_ref,
                        workspace_session=workspace_session,
                        current_state=current_state,
                        trial_context=research_summary.get("trial"),
                        run_session_id=scope_kwargs.get("run_session_id"),
                    )
                    reflection_result = await reflector_runner.run(
                        session=workspace_session,
                        spec_hash=evaluation["spec_hash"],
                        iteration_paths=iteration_paths,
                        evaluation_packet=reflection_packet,
                    )
                    workspace_builder.record_lesson_card(
                        session=workspace_session,
                        iteration_number=iteration_number,
                        spec_hash=evaluation["spec_hash"],
                        content=reflection_result.lesson_card_path.read_text(),
                    )
                    workspace_hooks.after_reflection()

                summary = evaluation["summary"]
                validation_fragment = ""
                if summary.get("validation_available") and summary.get("validation_total_return") is not None:
                    validation_fragment = (
                        f" validation={float(summary['validation_total_return']):.3%}"
                    )
                print(
                    f"[{track}] iter={iteration_number} {spec.family} "
                    f"{evaluation['spec_hash']} "
                    f"score={summary['aggregate_score']:.4f} "
                    f"sharpe={summary['median_sharpe']:.3f} "
                    f"return={summary['median_total_return']:.3%} "
                    f"{validation_fragment}"
                    f" passed={summary['passed']}"
                )

                ineligible_reasons = _deployment_ineligible_reasons(
                    summary=summary,
                    trial_context=dict(research_summary.get("trial") or {}),
                )
                deployment_eligible = not ineligible_reasons
                if summary["passed"] and deployment_eligible:
                    if best_passing is None:
                        best_passing = evaluation
                        best_passing_trial_context = dict(research_summary.get("trial") or {})
                    elif skip_llm:
                        if summary["aggregate_score"] > best_passing["summary"]["aggregate_score"]:
                            best_passing = evaluation
                            best_passing_trial_context = dict(research_summary.get("trial") or {})
                    else:
                        spec_trial_context = dict(research_summary.get("trial") or {})
                        if deployment_rank(summary, spec_trial_context) > deployment_rank(
                            dict(best_passing.get("summary") or {}),
                            best_passing_trial_context,
                        ):
                            best_passing = evaluation
                            best_passing_trial_context = spec_trial_context
                elif summary["passed"]:
                    passed_not_deployable += 1
                    deployment_ineligible_reasons.update(ineligible_reasons)

            if best_passing is not None:
                consecutive_no_improvement = 0
                ancestry.deploy(best_passing["spec_hash"])
                print(
                    f"[{track}] deployd {best_passing['spec_hash']} "
                    f"family={best_passing['spec']['family']}"
                )
                workspace_hooks.after_experiment(
                    spec_hash=best_passing["spec_hash"],
                    iteration_number=iteration_number,
                )
            else:
                consecutive_no_improvement += 1
                if passed_not_deployable:
                    reason_text = ", ".join(
                        f"{reason}={count}"
                        for reason, count in sorted(deployment_ineligible_reasons.items())
                    ) or "unknown"
                    print(
                        f"[{track}] {passed_not_deployable} passing spec(s) failed deployment eligibility "
                        f"in iteration {iteration_number}: {reason_text}"
                    )
                else:
                    print(f"[{track}] no passing spec in iteration {iteration_number}")
                max_no_improvement = loop_policy.get("max_consecutive_no_improvement")
                if max_no_improvement is not None and consecutive_no_improvement >= int(max_no_improvement):
                    _write_loop_stop(
                        settings=settings,
                        run_session_id=run_session_id,
                        reason="policy_stop:max_consecutive_no_improvement",
                        payload={"run_label": run_label or run_session_id, "runner_label": runner_label, "phase_label": phase_label, "consecutive_no_improvement": consecutive_no_improvement, "loop_policy": loop_policy},
                    )
                    return iteration_number + 1
        finally:
            _write_provider_metrics_artifact(
                settings=settings,
                run_session_id=run_session_id,
                iteration_number=iteration_number,
                phase_label=phase_label,
                reason="iteration_finally",
                claude=claude,
            )
            provider.clear_iteration_bundle()
    return last_iteration + 1


async def _run_iterations(
    *,
    settings: Any,
    provider: MarketDataProvider,
    ancestry: LineageStore,
    mutator: SpecMutator,
    evaluator: ResearchEvaluator,
    web_researcher: WebResearcher,
    hypothesis_sandbox: HypothesisSandbox,
    tracks: list[str],
    population_size: int,
    family_scope: str | list[str] | None,
    skip_llm: bool,
    iterations: int,
    start_iteration: int,
    phase_label: str,
    run_session_id: str,
) -> int:
    iteration_iter = count(start_iteration) if iterations == 0 else range(start_iteration, start_iteration + iterations)
    last_iteration = start_iteration
    for iteration_number in iteration_iter:
        last_iteration = iteration_number
        print(f"[run:{phase_label}] iteration={iteration_number}")
        for track in tracks:
            seed_specs = mutator.load_seed_specs(track, family=family_scope)
            recent_rows = ancestry.recent(track, limit=500)
            if skip_llm:
                parent = pick_deterministic_parent(
                    track=track,
                    ancestry=ancestry,
                    seed_specs=seed_specs,
                    iteration_number=iteration_number,
                )
            else:
                parent = pick_parent(track, ancestry, seed_specs)
            parent_hash = parent.strategy_hash()
            _best = ancestry.best(track)
            print(
                f"[{track}] parent={parent.family} {parent_hash} "
                f"recent_best={_best['aggregate_score']:.4f}"
                if _best is not None
                else f"[{track}] parent={parent.family} {parent_hash}"
            )
            run_context = {
                "run_session_id": run_session_id,
                "phase_label": phase_label,
                "iteration_number": int(iteration_number),
                "deterministic": bool(skip_llm),
                "llm_phase": not bool(skip_llm),
                "force_novelty": False,
            }
            provider.begin_iteration_bundle(track=track, parent=parent)
            try:
                if skip_llm:
                    agent_recent_results: list[dict[str, Any]] = []
                    agent_memory_packet: dict[str, Any] = {}
                    research_summary = _minimal_research_summary(
                        track=track,
                        parent=parent,
                        provider=provider,
                        web_researcher=web_researcher,
                        run_context=run_context,
                    )
                    llm_tools: list[Any] = []
                else:
                    recent_results = ancestry.recent(track, limit=5, include_deterministic=False)
                    if not recent_results:
                        recent_results = ancestry.recent(track, limit=5)
                    agent_recent_results = _agent_safe_recent_results(recent_results)
                    research_summary = await provider.build_research_summary(track, parent)
                    research_summary["external_research"] = _tool_only_external_research(
                        web_researcher=web_researcher
                    )
                    memory_packet = ancestry.memory_packet(
                        track=track,
                        parent=parent,
                        market_bundle=research_summary.get("market_bundle"),
                    )
                    run_context["force_novelty"] = bool(
                        iteration_number % max(3, population_size) == 0
                        or bool((memory_packet.get("novelty_pressure") or {}).get("required"))
                    )
                    research_summary["run_context"] = run_context
                    agent_memory_packet = _agent_safe_memory_packet(memory_packet)
                    research_summary["memory_packet"] = agent_memory_packet
                    llm_tools = [
                        *web_researcher.claude_tools(),
                        *hypothesis_sandbox.claude_tools(track=track, parent=parent),
                    ]
                specs = await mutator.propose(
                    track=track,
                    parent=parent,
                    research_summary=research_summary,
                    recent_results=agent_recent_results,
                    memory_packet=agent_memory_packet,
                    population_size=population_size,
                    skip_llm=skip_llm,
                    family=family_scope,
                    exclude_hashes={
                        row["spec_hash"] for row in ancestry.recent(track, limit=200)
                    },
                    llm_tools=llm_tools,
                    deterministic_recent_rows=recent_rows,
                    deterministic_seed_specs=seed_specs,
                )
                if mutator.last_llm_trace is not None:
                    research_summary["llm_tool_trace"] = mutator.last_llm_trace
                    external_research = _external_research_from_llm_trace(
                        llm_trace=mutator.last_llm_trace,
                        web_researcher=web_researcher,
                    )
                    if external_research.get("reports"):
                        research_summary["external_research"] = external_research
                        ancestry.record_query_cards(
                            track=track,
                            family=parent.family,
                            parent_hash=parent_hash,
                            market_bundle=research_summary.get("market_bundle"),
                            external_research=external_research,
                        )

                if not specs:
                    print(f"[{track}] no new spec generated in iteration {iteration_number}")
                    continue

                best_passing: dict[str, Any] | None = None
                for spec in specs:
                    try:
                        evaluation = await evaluator.evaluate(
                            spec,
                            fast_mode=bool(skip_llm),
                        )
                    except (OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError) as exc:
                        print(
                            f"[{track}] {spec.family} {spec.strategy_hash()} "
                            f"failed={type(exc).__name__}: {exc}"
                        )
                        continue

                    artifact_path = _write_artifact(settings, track, evaluation)
                    ancestry.record(
                        evaluation=evaluation,
                        parent_hash=parent_hash,
                        research_summary=research_summary,
                        artifact_path=str(artifact_path),
                    )

                    summary = evaluation["summary"]
                    validation_fragment = ""
                    if summary.get("validation_available") and summary.get("validation_total_return") is not None:
                        validation_fragment = (
                            f" validation={float(summary['validation_total_return']):.3%}"
                        )
                    audit_fragment = ""
                    if summary.get("audit_available") and summary.get("audit_total_return") is not None:
                        audit_fragment = (
                            f" audit={float(summary['audit_total_return']):.3%}"
                        )
                    print(
                        f"[{track}] iter={iteration_number} {spec.family} "
                        f"{evaluation['spec_hash']} "
                        f"score={summary['aggregate_score']:.4f} "
                        f"sharpe={summary['median_sharpe']:.3f} "
                        f"return={summary['median_total_return']:.3%} "
                        f"{validation_fragment}"
                        f"{audit_fragment}"
                        f" passed={summary['passed']}"
                    )

                    if summary["passed"]:
                        if best_passing is None or (
                            summary["aggregate_score"]
                            > best_passing["summary"]["aggregate_score"]
                        ):
                            best_passing = evaluation

                if best_passing is not None:
                    ancestry.deploy(best_passing["spec_hash"])
                    print(
                        f"[{track}] deployd {best_passing['spec_hash']} "
                        f"family={best_passing['spec']['family']}"
                    )
                else:
                    print(f"[{track}] no passing spec in iteration {iteration_number}")
            finally:
                provider.clear_iteration_bundle()
    return last_iteration + 1


def _credit_budget_stop_payload(
    *,
    claude: ClaudeClient,
    loop_policy: dict[str, Any],
    run_label: str,
    runner_label: str,
    phase_label: str,
    next_iteration: int,
) -> dict[str, Any] | None:
    limit = loop_policy.get("max_total_credits")
    if limit is None:
        return None
    try:
        max_total_credits = float(limit)
    except (TypeError, ValueError):
        return {
            "run_label": run_label,
            "runner_label": runner_label,
            "phase_label": phase_label,
            "next_iteration": next_iteration,
            "credits_estimate": None,
            "max_total_credits": limit,
            "provider_metrics": claude.metrics_snapshot(),
            "loop_policy": loop_policy,
            "policy_error": "invalid_max_total_credits",
        }
    metrics = claude.metrics_snapshot()
    usage = dict(metrics.get("usage") or {})
    credits = usage.get("credits_estimate")
    if credits is None:
        return None
    try:
        credits_float = float(credits)
    except (TypeError, ValueError):
        return None
    if credits_float < max_total_credits:
        return None
    return {
        "run_label": run_label,
        "runner_label": runner_label,
        "phase_label": phase_label,
        "next_iteration": next_iteration,
        "credits_estimate": round(credits_float, 6),
        "max_total_credits": max_total_credits,
        "credit_budget_semantics": "verified_bai_credits_between_iterations_cooperative",
        "provider_metrics": metrics,
        "loop_policy": loop_policy,
    }


def _write_loop_stop(
    *,
    settings: Any,
    run_session_id: str,
    reason: str,
    payload: dict[str, Any],
) -> None:
    path = settings.artifact_dir / "loop_stops" / f"{run_session_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(
        path,
        {
            "run_session_id": run_session_id,
            "reason": reason,
            "created_at": datetime.now(UTC).isoformat(),
            **payload,
        },
    )


def _write_provider_metrics_artifact(
    *,
    settings: Any,
    run_session_id: str,
    iteration_number: int,
    phase_label: str,
    reason: str,
    claude: ClaudeClient,
) -> Path:
    metrics_dir = settings.artifact_dir / "provider_metrics"
    metrics_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "recorded_at": datetime.now(UTC).isoformat(),
        "run_session_id": run_session_id,
        "iteration_number": int(iteration_number),
        "phase_label": phase_label,
        "reason": reason,
        "provider_metrics": claude.metrics_snapshot(),
    }
    latest_path = metrics_dir / f"{run_session_id}.latest.json"
    write_json(latest_path, payload)
    jsonl_path = metrics_dir / f"{run_session_id}.jsonl"
    with jsonl_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True, default=str) + "\n")
    return jsonl_path


def _resume_safe_check(*, settings: Any, run_session_id: str) -> None:
    session_dir = settings.artifact_dir / "trend_signals" / "workspaces" / run_session_id
    if not session_dir.exists():
        raise SystemExit(f"--resume-safe-check failed: workspace session not found: {session_dir}")
    state_path = session_dir / "current" / "SESSION_STATE.json"
    if not state_path.exists():
        raise SystemExit(f"--resume-safe-check failed: missing session state: {state_path}")


async def inspect_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    _require_sosovalue_config(settings)
    settings.ensure_runtime_directories()
    lake = ParquetLake(settings.data_lake_dir)
    provider = MarketDataProvider(settings, lake)
    claude = ClaudeClient(settings)
    web_researcher = WebResearcher(settings, lake)
    mutator = SpecMutator(settings, claude)
    ancestry = LineageStore(settings.ancestry_db_path)

    tracks = (
        list(settings.tracks)
        if args.track == "all"
        else [canonical_track_name(args.track) or args.track]
    )
    try:
        for track in tracks:
            parent = pick_parent(track, ancestry, mutator.load_seed_specs(track))
            provider.begin_iteration_bundle(track=track, parent=parent)
            try:
                summary = await provider.build_research_summary(track, parent)
                summary["external_research"] = _tool_only_external_research(
                    web_researcher=web_researcher
                )
                summary["memory_packet"] = _agent_safe_memory_packet(
                    ancestry.memory_packet(
                        track=track,
                        parent=parent,
                        market_bundle=summary.get("market_bundle"),
                    )
                )
                print(json.dumps(summary, indent=2))
            finally:
                provider.clear_iteration_bundle()
    finally:
        await web_researcher.close()
        await provider.close()


def ancestry_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    ancestry = LineageStore(settings.ancestry_db_path)
    rows = ancestry.list_rows(
        track=canonical_track_name(args.track) or args.track,
        limit=args.limit,
    )
    for row in rows:
        print(
            f"{row['created_at']} {row['track']} {row['family']} "
            f"{row['spec_hash']} score={row['aggregate_score']:.4f} "
            f"passed={row['passed']} deployd={row['deployd']}"
        )


def dashboard_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    run_dashboard_server(settings, host=args.host, port=args.port)


def clear_passed_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    settings.ensure_runtime_directories()
    ancestry = LineageStore(settings.ancestry_db_path)
    track = None if args.track == "all" else (canonical_track_name(args.track) or args.track)
    result = ancestry.clear_passed(track=track)
    scope = track or "all tracks"
    print(
        f"cleared passed experiments from {scope}: "
        f"experiments={result['experiments_deleted']} "
        f"runs={result['runs_deleted']} "
        f"deployments={result['deployments_deleted']} "
        f"query_cards={result['query_cards_deleted']}"
    )
    if result["spec_hashes"]:
        preview = ", ".join(result["spec_hashes"][:10])
        print(f"spec_hashes={preview}")


async def deploy_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    _require_sosovalue_config(settings)
    settings.ensure_runtime_directories()
    ancestry = LineageStore(settings.ancestry_db_path)
    claude = ClaudeClient(settings)
    manager = LiveDeploymentManager(settings, ancestry, claude=claude)
    config_path = resolve_path_from_root(
        args.config_path or settings.sosovalue_config_path,
        root_dir=settings.root_dir,
    )
    record = await manager.deploy(
        spec_hash=str(args.spec),
        wallet_label=args.wallet_label,
        config_path=str(config_path),
        interval_seconds=args.interval_seconds,
        job_name=args.job_name,
        dry_run=not bool(args.live),
        llm_finalize=bool(args.llm_finalize),
        schedule=bool(args.schedule),
    )
    print(json.dumps(_display_deployment_record(settings=settings, record=record.to_dict()), indent=2))


def deployments_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    ancestry = LineageStore(settings.ancestry_db_path)
    if args.spec:
        payload = ancestry.deployment(str(args.spec))
        print(json.dumps(_display_deployment_record(settings=settings, record=dict(payload or {})), indent=2))
        return

    rows = []
    for experiment in ancestry.dashboard_rows():
        deployment = experiment.get("deployment")
        if deployment:
            rows.append(_display_deployment_record(settings=settings, record=dict(deployment)))
    print(json.dumps(rows, indent=2))


def benchmark_init_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    settings.ensure_runtime_directories()
    ancestry = LineageStore(settings.ancestry_db_path)
    claude = ClaudeClient(settings)
    mutator = SpecMutator(settings, claude)
    payload = init_benchmark_deck(
        settings=settings,
        ancestry=ancestry,
        mutator=mutator,
        deck_name=str(args.deck),
        runner_label=str(getattr(args, "agent_label", None) or getattr(args, "runner_label", None) or "external_agent"),
        run_label=args.run_label,
        force=bool(args.force),
    )
    print(json.dumps(payload, indent=2))


async def benchmark_eval_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    _require_sosovalue_config(settings)
    settings.ensure_runtime_directories()
    lake = ParquetLake(settings.data_lake_dir)
    provider = MarketDataProvider(settings, lake)
    ancestry = LineageStore(settings.ancestry_db_path)
    claude = ClaudeClient(settings)
    mutator = SpecMutator(settings, claude)
    evaluator = ResearchEvaluator(settings, provider)
    try:
        payload = await evaluate_benchmark_deck(
            settings=settings,
            ancestry=ancestry,
            mutator=mutator,
            evaluator=evaluator,
            provider=provider,
            deck_name=str(args.deck),
        )
    finally:
        await provider.close()
    print(json.dumps(payload, indent=2))


def benchmark_status_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    payload = benchmark_status_payload(
        settings=settings,
        deck_name=str(args.deck),
    )
    print(json.dumps(payload, indent=2))


def _write_artifact(
    settings: Any,
    track: str,
    evaluation: dict[str, Any],
) -> Path:
    target_dir = settings.artifact_dir / track
    target_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = target_dir / f"{timestamp}_{evaluation['spec_hash']}.json"
    write_json(target, evaluation)
    return target


def _parse_family_scope(
    family: str | None,
    families: str | None,
) -> str | list[str] | None:
    if family and families:
        raise SystemExit("Use either --family or --families, not both")
    if family:
        return family
    if not families:
        return None
    parsed = [item.strip() for item in str(families).split(",") if item.strip()]
    if not parsed:
        raise SystemExit("--families must contain at least one family")
    return parsed


def _require_sosovalue_config(settings: Any) -> Path:
    config_path = resolve_path_from_root(
        settings.sosovalue_config_path,
        root_dir=settings.root_dir,
    )
    if not config_path.exists():
        raise SystemExit(
            "SOSOVALUE_CONFIG_PATH is required for this command and must point to an existing file. "
            f"Tried: {config_path}. Create it with `cp config.example.json config.json` "
            "or point SOSOVALUE_CONFIG_PATH at an existing SoSoValue config."
        )
    settings.sosovalue_config_path = config_path
    return config_path


def _display_deployment_record(*, settings: Any, record: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(record)
    for key in ["strategy_dir", "spec_path", "manifest_path", "readme_path", "config_path"]:
        normalized[key] = display_path(normalized.get(key), root_dir=settings.root_dir)
    return normalized


def _strip_audit_fields(payload: Any) -> Any:
    if isinstance(payload, dict):
        cleaned: dict[str, Any] = {}
        for key, value in payload.items():
            key_str = str(key)
            if key_str.startswith("audit_"):
                continue
            cleaned[key_str] = _strip_audit_fields(value)
        return cleaned
    if isinstance(payload, list):
        return [_strip_audit_fields(item) for item in payload]
    return payload


def _agent_safe_recent_results(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned_rows: list[dict[str, Any]] = []
    for row in rows:
        cleaned = dict(row)
        cleaned["summary"] = _strip_audit_fields(dict(row.get("summary") or {}))
        cleaned_rows.append(cleaned)
    return cleaned_rows


def _agent_safe_memory_packet(packet: dict[str, Any]) -> dict[str, Any]:
    return _strip_audit_fields(dict(packet or {}))


def _tool_only_external_research(*, web_researcher: WebResearcher) -> dict[str, Any]:
    return {
        "enabled": bool(web_researcher.is_configured),
        "provider": "tool_only",
        "queries": [],
        "reports": [],
    }


def _minimal_research_summary(
    *,
    track: str,
    parent: SignalSpec,
    provider: MarketDataProvider,
    web_researcher: WebResearcher,
    run_context: dict[str, Any],
) -> dict[str, Any]:
    return {
        "track": track,
        "parent_family": parent.family,
        "parent_hash": parent.strategy_hash(),
        "market_bundle": dict(provider.current_bundle_context() or {}),
        "external_research": _tool_only_external_research(web_researcher=web_researcher),
        "run_context": dict(run_context),
        "memory_packet": {},
    }


def _incumbent_detail(
    *,
    ancestry: LineageStore,
    track: str,
    run_session_id: str | None = None,
) -> dict[str, Any] | None:
    best = ancestry.best(track, run_session_id=run_session_id)
    if best is None:
        return None
    spec_hash = str(best.get("spec_hash") or "")
    if not spec_hash:
        return None
    return ancestry.experiment_detail(spec_hash)


def _base_spec_payload_for_family(
    *,
    track: str,
    family: str,
    parent: SignalSpec,
    ancestry: LineageStore,
    mutator: SpecMutator,
    run_session_id: str | None = None,
    custom_symbols: list[str] | None = None,
    use_historical_seeds: bool = False,
) -> dict[str, Any]:
    family_rows = ancestry.dashboard_rows(track=track, family=family, run_session_id=run_session_id)
    if family_rows:
        family_rows.sort(
            key=lambda row: (
                int(bool(row.get("passed"))),
                int(bool(row.get("deployd"))),
                float(dict(row.get("summary") or {}).get("aggregate_score") or -1e18),
                str(row.get("created_at") or ""),
            ),
            reverse=True,
        )
        return dict(family_rows[0].get("spec") or {})
    if parent.family == family:
        return _override_seed_spec_symbols(parent, custom_symbols).canonical_dict()
    seed_specs = _load_seed_specs_for_run(
        mutator=mutator,
        track=track,
        family_scope=family,
        custom_symbols=custom_symbols,
        use_historical_seeds=use_historical_seeds,
    )
    if seed_specs:
        return seed_specs[0].canonical_dict()
    return _override_seed_spec_symbols(parent, custom_symbols).canonical_dict()


def _motif_audit_streak(
    *,
    ancestry: LineageStore,
    track: str,
    spec_payload: dict[str, Any],
    limit: int = 40,
    run_session_id: str | None = None,
) -> int:
    from siglab.orchestration.contracts import motif_signature

    target_motif = motif_signature(spec_payload)
    streak = 0
    for row in ancestry.recent(
        track,
        limit=limit,
        include_deterministic=False,
        run_session_id=run_session_id,
    ):
        row_spec = dict(row.get("spec") or {})
        if motif_signature(row_spec) != target_motif:
            continue
        row_trial = dict(dict(row.get("research_summary") or {}).get("trial") or {})
        row_generalization = summarize_generalization(
            dict(row.get("summary") or {}),
            stability_pack=dict(row_trial.get("stability_pack") or {}),
        )
        alignment = str(
            row_trial.get("audit_alignment")
            or row_generalization.get("audit_alignment")
            or "not_run"
        )
        if alignment in {"negative", "mismatch"}:
            streak += 1
            continue
        break
    return streak


def _reflection_evaluation_packet(
    *,
    ancestry: LineageStore,
    evaluation: dict[str, Any],
    parent_hash: str | None,
    experiment_card_ref: str | None,
    workspace_session: Any,
    current_state: dict[str, Any],
    trial_context: dict[str, Any] | None = None,
    run_session_id: str | None = None,
) -> dict[str, Any]:
    from siglab.orchestration.contracts import motif_signature

    summary = _strip_audit_fields(dict(evaluation.get("summary") or {}))
    raw_summary = dict(evaluation.get("summary") or {})
    canonical_run = _strip_audit_fields(dict(evaluation.get("canonical_run") or {}))
    spec = _strip_audit_fields(dict(evaluation.get("spec") or {}))
    context_pack = dict(canonical_run.get("pre_audit_context_pack") or {})
    parent_delta: dict[str, Any] = {}
    if parent_hash:
        parent_detail = ancestry.experiment_detail(parent_hash)
        if parent_detail is not None:
            parent_summary = _strip_audit_fields(dict(parent_detail.get("summary") or {}))
            for key in [
                "pre_audit_canonical_total_return",
                "validation_total_return",
                "median_total_return",
                "active_bar_fraction",
            ]:
                if summary.get(key) is None or parent_summary.get(key) is None:
                    continue
                parent_delta[f"{key}_delta"] = float(summary[key]) - float(parent_summary[key])
    changed_keys = list(summary.get("policy_sweep_changed_keys") or [])
    intended_vs_frozen = {}
    if changed_keys:
        intended_vs_frozen = {
            "material_change": bool(summary.get("policy_sweep_material_change")),
            "changed_keys": changed_keys,
            "proposed_policy": dict(summary.get("policy_sweep_proposed_policy") or {}),
            "frozen_policy": dict(summary.get("policy_sweep_frozen_policy") or {}),
        }
    drawdown_excerpt = dict(canonical_run.get("pre_audit_drawdown_pack") or {})
    regime_excerpt = dict(context_pack.get("trade_regime_pack") or {})
    gate_excerpt = dict(context_pack.get("gate_diagnostics") or {})
    gate_reasons = list(summary.get("gate_reasons") or [])
    gate_bottlenecks = list(summary.get("gate_bottleneck_tags") or [])
    trial_context = dict(trial_context or {})
    current_generalization = summarize_generalization(
        raw_summary,
        stability_pack=dict(trial_context.get("stability_pack") or {}),
    )
    trial_context.setdefault("fragility_penalty", current_generalization.get("fragility_penalty"))
    trial_context.setdefault("deployment_score", current_generalization.get("deployment_score"))
    trial_context.setdefault("audit_alignment", current_generalization.get("audit_alignment"))
    trial_context.setdefault("fragility_label", current_generalization.get("fragility_label"))
    trial_context.setdefault("fragility_pack", current_generalization.get("fragility_pack"))
    trial_context.setdefault("stability_pack", current_generalization.get("stability_pack"))
    trial_context.setdefault(
        "stability_status",
        dict(current_generalization.get("stability_pack") or {}).get("status"),
    )
    trial_context.setdefault(
        "stability_pass_fraction",
        dict(current_generalization.get("stability_pack") or {}).get("passed_fraction"),
    )
    dominant_failure_mode = (
        str(gate_bottlenecks[0])
        if gate_bottlenecks
        else (str(gate_reasons[0]) if gate_reasons else "needs_follow_up")
    )
    recent_rows = []
    for row in ancestry.recent(
        str(evaluation.get("track") or ""),
        limit=12,
        include_deterministic=False,
        run_session_id=run_session_id,
    ):
        if str(row.get("spec_hash") or "") == str(evaluation.get("spec_hash") or ""):
            continue
        row_spec = _strip_audit_fields(dict(row.get("spec") or {}))
        row_summary = _strip_audit_fields(dict(row.get("summary") or {}))
        row_raw_summary = dict(row.get("summary") or {})
        row_trial = dict(dict(row.get("research_summary") or {}).get("trial") or {})
        row_generalization = summarize_generalization(
            row_raw_summary,
            stability_pack=dict(row_trial.get("stability_pack") or {}),
        )
        recent_rows.append(
            {
                "spec_hash": row.get("spec_hash"),
                "family": row.get("family"),
                "features": list(row_spec.get("features") or []),
                "params": dict(row_spec.get("params") or {}),
                "regime_gates": dict(row_spec.get("regime_gates") or {}),
                "motif_signature": motif_signature(row_spec),
                "pre_audit_canonical_total_return": row_summary.get("pre_audit_canonical_total_return"),
                "validation_total_return": row_summary.get("validation_total_return"),
                "median_total_return": row_summary.get("median_total_return"),
                "active_bar_fraction": row_summary.get("active_bar_fraction"),
                "passed": bool(row.get("passed")),
                "created_at": row.get("created_at"),
                "patch_summary": list(row_trial.get("patch_summary") or []),
                "optimized_param_summary": list(row_trial.get("optimized_param_summary") or []),
                "score_diagnosis": dict(row_trial.get("score_diagnosis") or {}),
                "return_driver": row_trial.get("return_driver"),
                "return_driver_source": row_trial.get("return_driver_source"),
                "exposure_profile": row_trial.get("exposure_profile"),
                "price_contribution": row_trial.get("price_contribution"),
                "carry_contribution": row_trial.get("carry_contribution"),
                "tx_cost_contribution": row_trial.get("tx_cost_contribution"),
                "best_regime_context": row_trial.get("best_regime_context"),
                "worst_regime_context": row_trial.get("worst_regime_context"),
                "fragility_penalty": row_trial.get("fragility_penalty", row_generalization.get("fragility_penalty")),
                "deployment_score": row_trial.get("deployment_score", row_generalization.get("deployment_score")),
                "audit_alignment": row_trial.get("audit_alignment", row_generalization.get("audit_alignment")),
                "fragility_label": row_trial.get("fragility_label", row_generalization.get("fragility_label")),
                "stability_pack": dict(row_trial.get("stability_pack") or row_generalization.get("stability_pack") or {}),
                "motif_audit_streak": row_trial.get("motif_audit_streak"),
            }
        )
        if len(recent_rows) >= 5:
            break
    return {
        "spec_hash": evaluation.get("spec_hash"),
        "family": spec.get("family"),
        "spec": spec,
        "failed_motif_signature": motif_signature(spec),
        "summary": summary,
        "parent_delta": parent_delta,
        "drawdown_excerpt": drawdown_excerpt,
        "regime_excerpt": regime_excerpt,
        "gate_excerpt": gate_excerpt,
        "intended_vs_frozen_diff": intended_vs_frozen,
        "dominant_failure_mode": dominant_failure_mode,
        "suggested_next_move": current_state.get("open_question"),
        "trial_context": trial_context,
        "structure_spec_ref": trial_context.get("structure_spec_path"),
        "base_spec_ref": trial_context.get("base_spec_path"),
        "patch_summary": list(trial_context.get("patch_summary") or []),
        "optimized_param_summary": list(trial_context.get("optimized_param_summary") or []),
        "score_diagnosis": dict(trial_context.get("score_diagnosis") or {}),
        "return_driver": trial_context.get("return_driver"),
        "return_driver_source": trial_context.get("return_driver_source"),
        "exposure_profile": trial_context.get("exposure_profile"),
        "price_contribution": trial_context.get("price_contribution"),
        "carry_contribution": trial_context.get("carry_contribution"),
        "tx_cost_contribution": trial_context.get("tx_cost_contribution"),
        "best_regime_context": trial_context.get("best_regime_context"),
        "worst_regime_context": trial_context.get("worst_regime_context"),
        "fragility_penalty": trial_context.get("fragility_penalty"),
        "deployment_score": trial_context.get("deployment_score"),
        "audit_alignment": trial_context.get("audit_alignment"),
        "fragility_label": trial_context.get("fragility_label"),
        "stability_pack": dict(trial_context.get("stability_pack") or {}),
        "motif_audit_streak": trial_context.get("motif_audit_streak"),
        "recent_completed_runs": recent_rows,
        "evidence_paths": [
            ref
            for ref in [
                experiment_card_ref,
                *list(current_state.get("selected_lesson_refs") or []),
                *list(current_state.get("selected_probe_refs") or []),
            ]
            if ref
        ],
        "workspace_root": str(workspace_session.root),
    }


def _external_research_from_llm_trace(
    *,
    llm_trace: dict[str, Any] | None,
    web_researcher: WebResearcher,
) -> dict[str, Any]:
    payload = _tool_only_external_research(web_researcher=web_researcher)
    trace = dict((llm_trace or {}).get("trace") or {})
    tool_calls = list(trace.get("tool_calls") or [])
    reports: list[dict[str, Any]] = []
    queries: list[str] = []
    for tool_call in tool_calls:
        if str(tool_call.get("name") or "") != "tavily_search":
            continue
        result = dict(tool_call.get("result") or {})
        if not bool(result.get("ok")):
            continue
        query = str(result.get("query") or "").strip()
        if not query:
            continue
        queries.append(query)
        reports.append(
            {
                "query": query,
                "answer": result.get("answer"),
                "insights": list(result.get("insights") or []),
                "sources": list(result.get("sources") or []),
            }
        )
    if reports:
        payload["provider"] = "tavily_tool_calls"
        payload["queries"] = queries
        payload["reports"] = reports
    return payload


def _pick_deterministic_parent(
    *,
    track: str,
    ancestry: LineageStore,
    seed_specs: list[Any],
    iteration_number: int,
) -> Any:
    recent_rows = ancestry.recent(track, limit=500)
    deterministic_rows = [row for row in recent_rows if _row_is_deterministic(row)]
    family_counts: Counter[str] = Counter(str(row.get("family") or "") for row in deterministic_rows)
    seed_order = list(seed_specs)
    min_count = min((family_counts.get(seed.family, 0) for seed in seed_order), default=0)
    least_used = [seed for seed in seed_order if family_counts.get(seed.family, 0) == min_count]
    if not least_used:
        return seed_order[0]
    return least_used[(iteration_number - 1) % len(least_used)]


def _row_is_deterministic(row: dict[str, Any]) -> bool:
    research_summary = dict(row.get("research_summary") or {})
    run_context = dict(research_summary.get("run_context") or {})
    if "deterministic" in run_context:
        return bool(run_context.get("deterministic"))
    return str(run_context.get("phase_label") or "").strip().lower() == "burn_in"


def _spec_trade_style(spec: dict[str, Any]) -> str:
    params = dict(spec.get("params") or {})
    trade_style = str(params.get("trade_style") or "").strip().lower()
    return trade_style or "unspecified"


def _write_run_reflection(
    *,
    settings: Any,
    ancestry: LineageStore,
    track: str,
    phase_label: str,
    family_scope: str | list[str] | None,
    run_session_id: str,
) -> tuple[Path | None, dict[str, Any] | None]:
    rows = [
        row
        for row in ancestry.dashboard_rows(track=track)
        if (
            not _row_is_deterministic(row)
            and str(
                dict(dict(row.get("research_summary") or {}).get("run_context") or {}).get(
                    "run_session_id"
                )
                or ""
            )
            == run_session_id
        )
    ]
    if not rows:
        return None, None

    rows.sort(key=lambda row: str(row.get("created_at") or ""))
    recent_rows = rows[-5:]
    passed_rows = [row for row in rows if bool(row.get("passed"))]
    deployd_rows = [row for row in rows if bool(row.get("deployd"))]

    family_counts: Counter[str] = Counter()
    trade_style_counts: Counter[str] = Counter()
    feature_counts: Counter[str] = Counter()
    gate_reason_counts: Counter[str] = Counter()
    bottleneck_counts: Counter[str] = Counter()
    sweep_changed_key_counts: Counter[str] = Counter()
    active_fractions: list[float] = []
    pre_audit_returns: list[float] = []
    validation_returns: list[float] = []
    selector_returns: list[float] = []
    changed_param_counts: list[float] = []
    entry_score_drifts: list[float] = []
    exit_score_drifts: list[float] = []
    flip_score_drifts: list[float] = []
    holding_bar_drifts: list[float] = []
    cooldown_bar_drifts: list[float] = []
    material_sweep_changes = 0
    restrictive_count = 0
    low_activity_count = 0

    for row in rows:
        family = str(row.get("family") or "unknown")
        family_counts[family] += 1
        trade_style_counts[_spec_trade_style(row.get("spec") or {})] += 1
        feature_counts.update(str(feature) for feature in (row.get("spec") or {}).get("features") or [])
        summary = dict(row.get("summary") or {})
        gate_reason_counts.update(str(reason) for reason in summary.get("gate_reasons") or [])
        bottleneck_counts.update(str(tag) for tag in summary.get("gate_bottleneck_tags") or [])
        active_fraction = summary.get("active_bar_fraction")
        if active_fraction is not None:
            numeric = float(active_fraction)
            active_fractions.append(numeric)
            if numeric <= 0.02:
                low_activity_count += 1
        pre_audit = summary.get("pre_audit_canonical_total_return")
        if pre_audit is not None:
            pre_audit_returns.append(float(pre_audit))
        validation = summary.get("validation_total_return")
        if validation is not None:
            validation_returns.append(float(validation))
        selector = summary.get("median_total_return")
        if selector is not None:
            selector_returns.append(float(selector))
        if bool(summary.get("policy_sweep_material_change")):
            material_sweep_changes += 1
        changed_keys = list(summary.get("policy_sweep_changed_keys") or [])
        sweep_changed_key_counts.update(str(key) for key in changed_keys)
        changed_param_counts.append(float(len(changed_keys)))
        proposed_policy = dict(summary.get("policy_sweep_proposed_policy") or {})
        frozen_policy = dict(summary.get("policy_sweep_frozen_policy") or {})
        _append_policy_delta(entry_score_drifts, proposed_policy, frozen_policy, "entry_abs_score")
        _append_policy_delta(exit_score_drifts, proposed_policy, frozen_policy, "exit_abs_score")
        _append_policy_delta(flip_score_drifts, proposed_policy, frozen_policy, "flip_abs_score")
        _append_policy_delta(holding_bar_drifts, proposed_policy, frozen_policy, "max_holding_bars")
        _append_policy_delta(cooldown_bar_drifts, proposed_policy, frozen_policy, "cooldown_bars")
        if "restrictive_regime_gate" in set(str(tag) for tag in summary.get("gate_bottleneck_tags") or []):
            restrictive_count += 1

    early_rows = rows[: min(5, len(rows))]
    late_rows = recent_rows
    early_pre_audit = [
        float(row["summary"]["pre_audit_canonical_total_return"])
        for row in early_rows
        if row["summary"].get("pre_audit_canonical_total_return") is not None
    ]
    late_pre_audit = [
        float(row["summary"]["pre_audit_canonical_total_return"])
        for row in late_rows
        if row["summary"].get("pre_audit_canonical_total_return") is not None
    ]
    early_active = [
        float(row["summary"]["active_bar_fraction"])
        for row in early_rows
        if row["summary"].get("active_bar_fraction") is not None
    ]
    late_active = [
        float(row["summary"]["active_bar_fraction"])
        for row in late_rows
        if row["summary"].get("active_bar_fraction") is not None
    ]

    allowed_families = (
        [family_scope]
        if isinstance(family_scope, str)
        else list(family_scope or [])
    )
    family_attempted = {family: family_counts.get(family, 0) for family in allowed_families}
    underexplored_families = [
        family for family, count in family_attempted.items() if count <= 1
    ]

    summary = {
        "llm_run_count": len(rows),
        "passed_count": len(passed_rows),
        "deployd_count": len(deployd_rows),
        "median_pre_audit_canonical_total_return": _median_or_none(pre_audit_returns),
        "median_validation_total_return": _median_or_none(validation_returns),
        "median_selector_total_return": _median_or_none(selector_returns),
        "median_active_bar_fraction": _median_or_none(active_fractions),
        "low_activity_share": _share(low_activity_count, len(rows)),
        "restrictive_gate_share": _share(restrictive_count, len(rows)),
        "material_sweep_change_share": _share(material_sweep_changes, len(rows)),
        "pre_audit_return_change_vs_first_five": _delta_median(late_pre_audit, early_pre_audit),
        "active_bar_fraction_change_vs_first_five": _delta_median(late_active, early_active),
    }
    intent_vs_sweep = {
        "material_change_share": _share(material_sweep_changes, len(rows)),
        "median_changed_param_count": _median_or_none(changed_param_counts),
        "most_changed_params": [
            {"param": key, "count": count}
            for key, count in sweep_changed_key_counts.most_common(6)
        ],
        "median_entry_abs_score_delta": _median_or_none(entry_score_drifts),
        "median_exit_abs_score_delta": _median_or_none(exit_score_drifts),
        "median_flip_abs_score_delta": _median_or_none(flip_score_drifts),
        "median_max_holding_bars_delta": _median_or_none(holding_bar_drifts),
        "median_cooldown_bars_delta": _median_or_none(cooldown_bar_drifts),
    }
    last_five_runs = []
    for row in reversed(recent_rows):
        summary_row = dict(row.get("summary") or {})
        changed_keys = list(summary_row.get("policy_sweep_changed_keys") or [])
        last_five_runs.append(
            {
                "spec_hash": row.get("spec_hash"),
                "parent_hash": row.get("parent_hash"),
                "family": row.get("family"),
                "hypothesis": str((row.get("spec") or {}).get("hypothesis") or ""),
                "median_total_return": summary_row.get("median_total_return"),
                "validation_total_return": summary_row.get("validation_total_return"),
                "pre_audit_canonical_total_return": summary_row.get("pre_audit_canonical_total_return"),
                "active_bar_fraction": summary_row.get("active_bar_fraction"),
                "gate_bottlenecks": list(summary_row.get("gate_bottleneck_tags") or [])[:4],
                "sweep_drift": {
                    "material_change": bool(summary_row.get("policy_sweep_material_change")),
                    "changed_keys": changed_keys,
                    "changed_param_count": len(changed_keys),
                    "activity_penalty": summary_row.get("policy_sweep_activity_penalty"),
                    "proposed_policy": dict(summary_row.get("policy_sweep_proposed_policy") or {}),
                    "frozen_policy": dict(summary_row.get("policy_sweep_frozen_policy") or {}),
                },
            }
        )

    what_improved: list[str] = []
    if summary["pre_audit_return_change_vs_first_five"] is not None and summary["pre_audit_return_change_vs_first_five"] > 0.0:
        what_improved.append("Late-run pre-audit returns improved relative to the first five LLM runs.")
    if summary["active_bar_fraction_change_vs_first_five"] is not None and summary["active_bar_fraction_change_vs_first_five"] < 0.0:
        what_improved.append("Later runs became more selective on active bars.")
    if len(passed_rows) > 0:
        what_improved.append("The run produced at least one passing spec in the non-deterministic phase.")

    what_failed: list[str] = []
    if summary["low_activity_share"] is not None and summary["low_activity_share"] >= 0.4:
        what_failed.append("Too many specs survived only by trading almost nothing.")
    if summary["restrictive_gate_share"] is not None and summary["restrictive_gate_share"] >= 0.4:
        what_failed.append("Restrictive regime gating remained a dominant bottleneck.")
    if summary["material_sweep_change_share"] is not None and summary["material_sweep_change_share"] >= 0.4:
        what_failed.append("The policy sweep materially rewrote many proposals instead of only tuning them.")
    _median_changed = intent_vs_sweep["median_changed_param_count"]
    if isinstance(_median_changed, (int, float)) and _median_changed >= 2.0:
        what_failed.append("Typical specs changed multiple policy parameters between intent and frozen evaluation.")
    if family_counts:
        dominant_family, dominant_family_count = family_counts.most_common(1)[0]
        if dominant_family_count >= max(4, len(rows) - 1):
            what_failed.append(f"The run concentrated heavily in {dominant_family}.")

    areas_for_improvement: list[str] = []
    if underexplored_families:
        areas_for_improvement.append(
            "Underexplored families: " + ", ".join(sorted(underexplored_families))
        )
    if feature_counts:
        top_features = ", ".join(feature for feature, _count in feature_counts.most_common(4))
        areas_for_improvement.append(f"Overused feature neighborhood: {top_features}")
    if bottleneck_counts:
        top_bottlenecks = ", ".join(tag for tag, _count in bottleneck_counts.most_common(4))
        areas_for_improvement.append(f"Recurring gate bottlenecks: {top_bottlenecks}")
    if sweep_changed_key_counts:
        top_sweep_keys = ", ".join(key for key, _count in sweep_changed_key_counts.most_common(4))
        areas_for_improvement.append(f"Most frequently sweep-rewritten params: {top_sweep_keys}")
    if trade_style_counts:
        top_trade_style, top_trade_style_count = trade_style_counts.most_common(1)[0]
        if top_trade_style_count >= 4:
            areas_for_improvement.append(
                f"Trade-style concentration suggests more novelty pressure is needed outside {top_trade_style}."
            )

    reflection = _strip_audit_fields(
        {
            "created_at": datetime.now(UTC).isoformat(),
            "track": track,
            "phase_label": phase_label,
            "summary": summary,
            "intent_vs_sweep": intent_vs_sweep,
            "family_counts": [
                {"family": family, "count": count}
                for family, count in family_counts.most_common(8)
            ],
            "trade_style_counts": [
                {"trade_style": trade_style, "count": count}
                for trade_style, count in trade_style_counts.most_common(8)
            ],
            "overused_features": [
                {"feature": feature, "count": count}
                for feature, count in feature_counts.most_common(8)
            ],
            "gate_reasons": [
                {"reason": reason, "count": count}
                for reason, count in gate_reason_counts.most_common(8)
            ],
            "gate_bottlenecks": [
                {"tag": tag, "count": count}
                for tag, count in bottleneck_counts.most_common(8)
            ],
            "what_improved": what_improved,
            "what_failed": what_failed,
            "areas_for_improvement": areas_for_improvement,
            "last_five_runs": last_five_runs,
        }
    )

    target_dir = settings.artifact_dir / track / "run_reflections"
    target_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = target_dir / f"{timestamp}_{phase_label}.json"
    write_json(target, reflection)
    _print_run_reflection(track=track, reflection=reflection)
    return target, reflection


def _print_run_reflection(*, track: str, reflection: dict[str, Any]) -> None:
    summary = dict(reflection.get("summary") or {})
    print(
        f"[{track}] run reflection: llm_runs={summary.get('llm_run_count', 0)} "
        f"passes={summary.get('passed_count', 0)} "
        f"median_pre_audit={_format_optional_pct(summary.get('median_pre_audit_canonical_total_return'))} "
        f"median_active={_format_optional_pct(summary.get('median_active_bar_fraction'))}"
    )
    intent_vs_sweep = dict(reflection.get("intent_vs_sweep") or {})
    print(
        f"[{track}] sweep drift: material_share={_format_optional_pct(intent_vs_sweep.get('material_change_share'))} "
        f"median_changed_params={_format_optional_number(intent_vs_sweep.get('median_changed_param_count'))}"
    )
    for line in list(reflection.get("what_improved") or [])[:3]:
        print(f"[{track}] improved: {line}")
    for line in list(reflection.get("what_failed") or [])[:3]:
        print(f"[{track}] failed: {line}")
    last_five_runs = list(reflection.get("last_five_runs") or [])[:5]
    if last_five_runs:
        print(f"[{track}] last five non-deterministic runs:")
    for row in last_five_runs:
        print(
            f"[{track}]   {row['spec_hash']} family={row['family']} "
            f"median={_format_optional_pct(row.get('median_total_return'))} "
            f"validation={_format_optional_pct(row.get('validation_total_return'))} "
            f"pre_audit={_format_optional_pct(row.get('pre_audit_canonical_total_return'))} "
            f"active={_format_optional_pct(row.get('active_bar_fraction'))} "
            f"sweep_changes={len(list((row.get('sweep_drift') or {}).get('changed_keys') or []))} "
            f"bottlenecks={','.join(row.get('gate_bottlenecks') or [])}"
        )


def _median_or_none(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _delta_median(current: list[float], baseline: list[float]) -> float | None:
    if not current or not baseline:
        return None
    current_median = _median_or_none(current)
    baseline_median = _median_or_none(baseline)
    if current_median is None or baseline_median is None:
        return None
    return current_median - baseline_median


def _share(count: int, total: int) -> float | None:
    if total <= 0:
        return None
    return round(float(count) / float(total), 4)


def _format_optional_pct(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.2%}"
    except (TypeError, ValueError):
        return "n/a"


def _format_optional_number(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "n/a"


def _append_policy_delta(
    values: list[float],
    proposed_policy: dict[str, Any],
    frozen_policy: dict[str, Any],
    key: str,
) -> None:
    if key not in proposed_policy or key not in frozen_policy:
        return
    try:
        values.append(float(frozen_policy[key]) - float(proposed_policy[key]))
    except (TypeError, ValueError):
        return


if __name__ == "__main__":
    main()



