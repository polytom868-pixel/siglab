"""Deploy subcommands: deploy, deployments."""

from __future__ import annotations

import argparse
import json
from typing import Any

from siglab.config import load_settings
from siglab.data.store import ParquetLake
from siglab.data.sodex_feeds import SoDEXFeeds
from siglab.live import LiveDeploymentManager
from siglab.live.paper_client import SoDEXPaperPerpsClient
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
    lake = ParquetLake(settings.data_lake_dir)
    feeds = SoDEXFeeds(lake=lake)
    client = SoDEXPaperPerpsClient(feeds=feeds)
    manager = LiveDeploymentManager(
        settings=settings,
        client=client,
    )
    existing = manager.lookup_deployment(spec_hash)
    if not existing:
        ancestry_store = manager.ancestry_store
        record = ancestry_store.experiment_detail(spec_hash)
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
        snapshot_dir = manager.export_snapshot(
            record=record,
            agent_id=args.agent_id,
            wallet_label=args.wallet_label,
        )
        print(f"Exported snapshot to: {snapshot_dir}")
        return
    print(f"Found existing deployment for {spec_hash}: {json.dumps(existing, indent=2)}")
    kwargs: dict[str, Any] = {}
    if args.job_name:
        kwargs.setdefault("deployment_metadata", {})["job_name"] = args.job_name
    if args.interval_seconds:
        kwargs.setdefault("deployment_metadata", {})["interval_seconds"] = args.interval_seconds
    if args.schedule:
        kwargs.setdefault("deployment_metadata", {})["scheduled"] = True
    if args.llm_finalize:
        kwargs.setdefault("deployment_metadata", {})["llm_finalize"] = True
    if args.live:
        kwargs.setdefault("deployment_metadata", {})["live"] = True
    manager.update_deployment(spec_hash, **kwargs)
    print(f"Updated deployment {spec_hash}")


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
    lake = ParquetLake(settings.data_lake_dir)
    feeds = SoDEXFeeds(lake=lake)
    client = SoDEXPaperPerpsClient(feeds=feeds)
    manager = LiveDeploymentManager(
        settings=settings,
        client=client,
    )
    spec_hash = args.spec
    if spec_hash:
        record = manager.lookup_deployment(spec_hash)
        if record:
            print(json.dumps(display_deployment_record(settings=settings, record=record), indent=2))
        else:
            print(json.dumps({"error": f"No deployment found for spec {spec_hash}"}))
        return
    deployments = manager.list_deployments()
    payload = [display_deployment_record(settings=settings, record=r) for r in deployments]
    print(json.dumps(payload, indent=2))
