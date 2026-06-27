"""Deploy subcommands: deploy, deployments."""

from __future__ import annotations

import argparse
import sys
from typing import Any

from siglab.cli.helpers import (
    deployment_eligible,
    display_deployment_record,
    require_sosovalue_config,
)
from siglab.cli.rich_utils import (
    print_error,
    print_info,
    print_json,
    print_success,
    print_warning,
)
from siglab.config import load_settings
from siglab.data.deployment_store import DeploymentStore as LineageStore
from siglab.live import LiveDeploymentManager
from siglab.llm import ClaudeClient


def add_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
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
    list_parser = subparsers.add_parser("deployments")
    list_parser.add_argument("--spec", default=None)


async def run_deploy(args: argparse.Namespace) -> None:
    settings = load_settings()
    require_sosovalue_config(settings)
    spec_hash = str(args.spec).strip()
    ancestry = LineageStore(settings.ancestry_db_path)
    claude = ClaudeClient(settings)
    manager = LiveDeploymentManager(settings=settings, ancestry=ancestry, claude=claude)
    existing = ancestry.deployment(spec_hash)
    if not existing:
        record = ancestry.experiment_detail(spec_hash)
        if not record:
            print(
                f"No matching spec or deployment found for hash: {spec_hash}",
                file=sys.stderr,
            )
            raise SystemExit(1)
        detail = display_deployment_record(settings=settings, record=record)
        print_info(f"Found spec {spec_hash} in ancestry (not yet deployed):")
        print_json(detail)
        evaluation = dict(record.get("summary") or {})
        trial_context = dict(
            dict(record.get("research_summary") or {}).get("trial") or {},
        )
        if not deployment_eligible(summary=evaluation, trial_context=trial_context):
            reasons = _deployment_ineligible_reasons_fn(
                summary=evaluation,
                trial_context=trial_context,
            )
            print(
                f"Spec {spec_hash} is not deployment-eligible: {', '.join(reasons)}",
                file=sys.stderr,
            )
            raise SystemExit(1)
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
        print_success(f"Exported snapshot to: {record_result.strategy_dir}")
        return
    print_info(f"Found existing deployment for {spec_hash}:")
    print_json(existing)
    print_warning(
        "Deployment already exists. Use 'deployments --spec <hash>' to inspect it.",
    )


def _deployment_ineligible_reasons_fn(
    *,
    summary: dict[str, Any],
    trial_context: dict[str, Any] | None,
) -> list[str]:
    return deployment_ineligible_reasons_fn(
        summary=summary,
        trial_context=trial_context,
    )


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
            print_json(display_deployment_record(settings=settings, record=record))
        else:
            print_error(f"No deployment found for spec {spec_hash}")
        return
    deployments = ancestry.list_deployments()
    payload = [
        display_deployment_record(settings=settings, record=r) for r in deployments
    ]
    print_json(payload)
