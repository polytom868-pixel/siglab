"""Telemetry report subcommand: aggregate LLM/tool telemetry."""
from __future__ import annotations
import argparse
from pathlib import Path
from typing import Any
from siglab.cli.rich_utils import get_console, make_table, print_json
from siglab.config import SiglabConfig, load_settings
from siglab.telemetry import aggregate_provider_metrics_artifacts, aggregate_trace_telemetry

def build_telemetry_payload(*, trace_paths: list[Path], provider_metric_paths: list[Path]) -> dict[str, Any]:
    """Aggregate trace + provider-metrics into one telemetry payload."""
    payload = aggregate_trace_telemetry(trace_paths)
    payload['trace_paths_scanned'] = len(trace_paths)
    payload['provider_metrics'] = aggregate_provider_metrics_artifacts(provider_metric_paths)
    payload['provider_metrics_paths_scanned'] = len(provider_metric_paths)
    payload['provider_metrics_status'] = 'missing' if trace_paths and payload['provider_metrics']['artifact_count'] == 0 else 'present' if payload['provider_metrics']['artifact_count'] > 0 else 'not_applicable'
    return payload

def add_subparser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser('telemetry-report', help='Aggregate empirical LLM/tool telemetry from run trace artifacts.')
    parser.add_argument('--track', default='all')
    parser.add_argument('--run-session-id', default=None)
    parser.add_argument('--json', action='store_true')

def run_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    trace_paths = trace_paths_for_telemetry(settings=settings, track=str(args.track or 'all'), run_session_id=args.run_session_id)
    provider_metric_paths = provider_metric_paths_for_telemetry(settings=settings, run_session_id=args.run_session_id)
    payload = build_telemetry_payload(trace_paths=trace_paths, provider_metric_paths=provider_metric_paths)
    if getattr(args, 'json', False):
        print_json(payload)
    else:
        import json as _json
        table = make_table(title='Telemetry Report')
        table.add_column('Metric', style='label', no_wrap=True)
        table.add_column('Value')
        table.add_row('trace_count', str(payload['trace_count']))
        table.add_row('stage_counts', _json.dumps(payload['stage_counts'], sort_keys=True))
        table.add_row('provider_counts', _json.dumps(payload['provider_counts'], sort_keys=True))
        table.add_row('model_counts', _json.dumps(payload['model_counts'], sort_keys=True))
        table.add_row('tool_invocation_count', str(payload['tool_invocation_count']))
        table.add_row('tool_counts', _json.dumps(payload['tool_counts'], sort_keys=True))
        table.add_row('tool_latency_ms', _json.dumps(payload['tool_latency_ms'], sort_keys=True))
        table.add_row('provider_metrics_status', str(payload['provider_metrics_status']))
        table.add_row('confidence', str(payload['confidence']))
        get_console().print(table)

def trace_paths_for_telemetry(*, settings: SiglabConfig, track: str, run_session_id: str | None) -> list[Path]:
    base = settings.artifact_dir
    if run_session_id:
        pattern = f'*/workspaces/{run_session_id}/iterations/**/*_trace.json'
    elif track == 'all':
        pattern = '*/workspaces/*/iterations/**/*_trace.json'
    else:
        pattern = f'{track}/workspaces/*/iterations/**/*_trace.json'
    return sorted(base.glob(pattern))

def provider_metric_paths_for_telemetry(*, settings: SiglabConfig, run_session_id: str | None) -> list[Path]:
    base = settings.artifact_dir / 'provider_metrics'
    if run_session_id:
        jsonl_path = base / f'{run_session_id}.jsonl'
        if jsonl_path.exists():
            return [jsonl_path]
        latest_path = base / f'{run_session_id}.latest.json'
        return [latest_path] if latest_path.exists() else []
    jsonl_paths = sorted(base.glob('*.jsonl'))
    if jsonl_paths:
        return jsonl_paths
    return sorted(base.glob('*.latest.json'))