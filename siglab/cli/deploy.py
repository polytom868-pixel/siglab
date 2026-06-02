"""Deploy subcommands: deploy, deployments."""

from __future__ import annotations

import argparse
import json
from typing import Any

from siglab.config import load_settings
from siglab.llm import ClaudeClient
from siglab.live import LiveDeploymentManager
from siglab.search.lineage import LineageStore
from siglab.cli.helpers import (
    require_sosovalue_config,
    display_deployment_record,
    deployment_eligible,
)


def add_subparser(subparsers) -> None:
    # deploy
    parser = subparsers.add_parser("deploy")
    parser.add_argument("--spec", required=True)
    parser.add_argument("--agent-id", default=None)
    parser.add_argument("--wallet-label", default=None)
    parser.add_argument("--config", dest="config_path", default=None)
    parser.add_argument("--job-name", default=None)
    parser.add_argument("--interval", dest="interval_seconds", type=int, default=None)
    parser.add_argument("--schedule", action="store_true")
    parser.add_argument("--llm-finalize", action="store_true")
    parser.add_argument("--live", action="store_true")

    # deployments
    list_parser = subparsers.add_parser("deployments")
    list_parser.add_argument("--spec", default=None)


async def run_deploy(args: argparse.Namespace) -> None:
    settings = load_settings()
    require_sosovalue_config(settings)
    spec_hash = str(args.spec).strip()
    ancestry = LineageStore(settings.ancestry_db_path)
    claude = ClaudeClient(settings)
    manager = LiveDeploymentManager(
        settings=settings,
        ancestry=ancestry,
        claude=claude,
    )
    existing = ancestry.deployment(spec_hash)
    if not existing:
        record = ancestry.experiment_detail(spec_hash)
        if not record:
            raise SystemExit(f"No matching spec or deployment found for hash: {spec_hash}")
        detail = display_deployment_record(settings=settings, record=record)
        print(f"Found spec {spec_hash} in ancestry (not yet deployed):")
        print(json.dumps(detail, indent=2))
        evaluation = dict(record.get("summary") or {})
        trial_context = dict(
            dict(record.get("research_summary") or {}).get("trial") or {}
        )
        if not deployment_eligible(summary=evaluation, trial_context=trial_context):
            reasons = _deployment_ineligible_reasons_fn(
                summary=evaluation, trial_context=trial_context
            )
            raise SystemExit(f"Spec {spec_hash} is not deployment-eligible: {', '.join(reasons)}")
        config_path = args.config or settings.sosovalue_config_path
        record_result = await manager.deploy(
            spec_hash=spec_hash,
            wallet_label=args.wallet_label,
            config_path=str(config_path),
            interval_seconds=args.interval_seconds,
            job_name=args.job_name,
            dry_run=not args.live,
            llm_finalize=bool(args.llm_finalize),
            schedule=bool(args.schedule),
        )
        print(f"Exported snapshot to: {record_result.strategy_dir}")
        return
    print(f"Found existing deployment for {spec_hash}: {json.dumps(existing, indent=2)}")
    print("Deployment already exists. Use 'deployments --spec <hash>' to inspect it.")


def _deployment_ineligible_reasons_fn(
    *,
    summary: dict[str, Any],
    trial_context: dict[str, Any] | None,
) -> list[str]:
    return deployment_ineligible_reasons_fn(summary=summary, trial_context=trial_context)


def deployment_ineligible_reasons_fn(
    *,
    summary: dict[str, Any],
    trial_context: dict[str, Any] | None,
) -> list[str]:
    from siglab.cli.helpers import deployment_ineligible_reasons
    return deployment_ineligible_reasons(summary=summary, trial_context=trial_context)


def run_deployments(args: argparse.Namespace) -> None:
    settings = load_settings()
    ancestry = LineageStore(settings.ancestry_db_path)
    spec_hash = args.spec
    if spec_hash:
        record = ancestry.deployment(spec_hash)
        if record:
            print(json.dumps(display_deployment_record(settings=settings, record=record), indent=2))
        else:
            print(json.dumps({"error": f"No deployment found for spec {spec_hash}"}))
        return
    deployments = ancestry.list_deployments()
    payload = [display_deployment_record(settings=settings, record=r) for r in deployments]
    print(json.dumps(payload, indent=2))
