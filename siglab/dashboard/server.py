from __future__ import annotations

import asyncio
import json
import math
import mimetypes
from dataclasses import dataclass
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlparse

from siglab.io_utils import json_safe, load_json_path
from siglab.llm_metadata import (
    default_llm_model_display,
    infer_llm_provider,
    resolve_llm_provider,
)
from siglab.live import LiveDeploymentManager, deployment_readiness
from siglab.llm import ClaudeClient
from siglab.path_utils import display_path, resolve_path_from_root
from siglab.search.lineage import LineageStore
from siglab.config import SiglabConfig
from siglab.track_registry import canonical_track_name, resolve_track, track_label


def _is_finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


@dataclass
class DashboardApp:
    settings: SiglabConfig
    ancestry: LineageStore
    static_dir: Path

    def _dashboard_llm_provider(self) -> str:
        return resolve_llm_provider(self.settings)

    def _dashboard_llm_model(self) -> str:
        return default_llm_model_display(self.settings, provider=self._dashboard_llm_provider())

    def _display_path(self, value: str | Path | None) -> str | None:
        return display_path(value, root_dir=self.settings.root_dir)

    def _display_deployment(self, deployment: dict[str, Any] | None) -> dict[str, Any] | None:
        if not deployment:
            return None
        normalized = dict(deployment)
        for key in [
            "strategy_dir",
            "spec_path",
            "manifest_path",
            "readme_path",
            "config_path",
        ]:
            normalized[key] = self._display_path(normalized.get(key))
        return normalized

    def _json_cache(self) -> dict[str, Any]:
        cache = getattr(self, "_dashboard_json_cache", None)
        if cache is None:
            cache = {}
            setattr(self, "_dashboard_json_cache", cache)
        return cache

    def _load_json_path(self, value: str | Path | None) -> dict[str, Any] | None:
        if not value:
            return None
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = (self.settings.root_dir / path).resolve()
        cache = self._json_cache()
        cache_key = str(path)
        if cache_key in cache:
            return cast(dict[str, Any] | None, cache[cache_key])
        payload: dict[str, Any] | None = load_json_path(path)
        cache[cache_key] = payload
        return payload

    def _normalize_trace_stage(
        self,
        *,
        stage_name: str,
        payload: dict[str, Any] | None,
        trace_path: str | Path | None,
    ) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            return None
        trace = dict(payload.get("claude_trace") or {})
        if not trace:
            for key in ("attempts", "planner_attempts", "repair_attempts"):
                attempts = list(payload.get(key) or [])
                for attempt in reversed(attempts):
                    attempt_trace = dict((attempt or {}).get("claude_trace") or {})
                    if attempt_trace:
                        trace = attempt_trace
                        break
                if trace:
                    break
        if not trace and not payload.get("error"):
            return None
        tool_calls = []
        for call in list(trace.get("tool_calls") or []):
            normalized_call = dict(call or {})
            normalized_call.setdefault("stage", stage_name)
            tool_calls.append(normalized_call)
        model = trace.get("model")
        return {
            "stage": str(payload.get("stage") or stage_name),
            "trace_path": self._display_path(trace_path),
            "provider": trace.get("provider") or infer_llm_provider(model),
            "model": model,
            "thinking_mode": trace.get("thinking_mode"),
            "tool_rounds_used": trace.get("tool_rounds_used", 0),
            "tool_count_available": trace.get("tool_count_available", 0),
            "tool_calls": tool_calls,
            "final_content_preview": trace.get("final_content_preview"),
            "response_finish_reason": trace.get("response_finish_reason"),
            "error": payload.get("error") or trace.get("error"),
        }

    def _resolve_tool_trace_stages(self, research_summary: dict[str, Any]) -> list[dict[str, Any]]:
        stages: list[dict[str, Any]] = []
        workspace = dict(research_summary.get("workspace") or {})
        for stage_name, path_key in (
            ("planner", "planner_trace_path"),
            ("writer", "writer_trace_path"),
            ("reflector", "reflector_trace_path"),
        ):
            trace_path = workspace.get(path_key)
            payload = self._load_json_path(trace_path)
            stage = self._normalize_trace_stage(
                stage_name=stage_name,
                payload=payload,
                trace_path=trace_path,
            )
            if stage is not None:
                stages.append(stage)

        if stages:
            return stages

        tool_trace = dict(research_summary.get("llm_tool_trace") or {})
        trace_core = dict(tool_trace.get("trace") or {})
        if tool_trace or trace_core:
            legacy_calls = []
            for call in list(trace_core.get("tool_calls") or []):
                normalized_call = dict(call or {})
                normalized_call.setdefault("stage", "proposal")
                legacy_calls.append(normalized_call)
            return [
                {
                    "stage": "proposal",
                    "trace_path": self._display_path(tool_trace.get("log_path")),
                    "provider": trace_core.get("provider") or infer_llm_provider(trace_core.get("model")),
                    "model": trace_core.get("model"),
                    "thinking_mode": trace_core.get("thinking_mode"),
                    "tool_rounds_used": trace_core.get("tool_rounds_used", 0),
                    "tool_count_available": trace_core.get("tool_count_available", 0),
                    "tool_calls": legacy_calls,
                    "final_content_preview": trace_core.get("final_content_preview"),
                    "response_finish_reason": trace_core.get("response_finish_reason"),
                    "error": tool_trace.get("error"),
                    "parent_family": tool_trace.get("parent_family"),
                    "parent_hash": tool_trace.get("parent_hash"),
                    "spec_count": tool_trace.get("spec_count"),
                }
            ]
        return []

    def _skill_value_report(self, tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_name: dict[str, dict[str, Any]] = {}
        for call in tool_calls:
            name = str(call.get("name") or "").strip()
            if not name:
                continue
            row = by_name.setdefault(
                name,
                {
                    "skill_name": name,
                    "stages": set(),
                    "invocation_count": 0,
                    "cost_contribution": 0,
                    "latency_cost_ms": 0.0,
                    "token_context_cost": 0,
                    "value_contribution": "unknown",
                    "effect_on_output_quality": "unmeasured",
                    "keep_rate_impact": "unmeasured",
                    "error_reduction": "unmeasured",
                    "evidence_quality_effect": "unmeasured",
                    "classification": "LOW_VALUE",
                },
            )
            row["invocation_count"] += 1
            row["cost_contribution"] += 1
            row["latency_cost_ms"] += float(call.get("latency_ms") or call.get("duration_ms") or 0.0)
            row["token_context_cost"] += int(call.get("context_tokens") or call.get("input_tokens") or call.get("token_count") or 0)
            stage = str(call.get("stage") or "").strip()
            if stage:
                row["stages"].add(stage)

        report: list[dict[str, Any]] = []
        for name, row in sorted(by_name.items()):
            if name.startswith("probe_") or name == "compare_intended_vs_frozen_spec":
                row["value_contribution"] = "direct_train_only_evidence"
                row["effect_on_output_quality"] = "high"
                row["error_reduction"] = "high"
                row["evidence_quality_effect"] = "high"
                row["classification"] = "HIGH_VALUE"
            elif name in {"search_features", "suggest_feature_set", "inspect_feature"}:
                row["value_contribution"] = "feature_surface_grounding"
                row["effect_on_output_quality"] = "medium"
                row["evidence_quality_effect"] = "medium"
                row["classification"] = "HIGH_VALUE"
            elif name in {"search_workspace", "search_workspace_text", "open_file"}:
                row["value_contribution"] = "workspace_context_grounding"
                row["effect_on_output_quality"] = "medium"
                row["evidence_quality_effect"] = "medium"
                row["classification"] = "MEDIUM_VALUE"
            elif name == "think":
                row["value_contribution"] = "reasoning_scratchpad"
                row["effect_on_output_quality"] = "low"
                row["classification"] = "LOW_VALUE"
            if row["invocation_count"] > 8 and row["classification"] in {"LOW_VALUE", "MEDIUM_VALUE"}:
                row["classification"] = "NOISY"
            normalized = dict(row)
            normalized["stages"] = sorted(row["stages"])
            normalized["latency_cost_ms"] = round(float(normalized["latency_cost_ms"]), 3)
            report.append(normalized)
        return report

    def _workspace_run_placeholders(
        self,
        *,
        track: str | None = None,
        family: str | None = None,
        existing_run_ids: set[str],
    ) -> list[dict[str, Any]]:
        placeholders: list[dict[str, Any]] = []
        llm_provider = self._dashboard_llm_provider()
        llm_model = self._dashboard_llm_model()
        runs_root = getattr(self.settings, "artifact_dir", self.settings.root_dir / "runs")
        if not runs_root.exists():
            return placeholders
        workspace_glob = runs_root.glob("*/workspaces/*/current/SESSION_STATE.json")
        for state_path in sorted(workspace_glob):
            try:
                payload = json.loads(state_path.read_text())
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                continue
            run_session_id = str(payload.get("run_session_id") or state_path.parents[1].name)
            if not run_session_id or run_session_id in existing_run_ids:
                continue
            track_name = resolve_track(state_path.parents[3].name)
            if track and canonical_track_name(track) != track_name:
                continue
            families = sorted(
                {
                    str(value)
                    for value in [
                        payload.get("current_parent_family"),
                        payload.get("best_family"),
                    ]
                    if str(value or "").strip()
                }
            )
            if family and family not in families:
                continue
            created_at = datetime.fromtimestamp(
                state_path.stat().st_mtime,
            ).astimezone().isoformat()
            placeholders.append(
                {
                    "run_session_id": run_session_id,
                    "run_label": run_session_id,
                    "track": track_name,
                    "runner_label": "siglab_harness",
                    "run_kind": "harness",
                    "memory_scope": str(payload.get("memory_scope") or "track_shared"),
                    "benchmark_mode": False,
                    "benchmark_deck": None,
                    "phase_labels": [str(payload.get("phase_label") or "starting")],
                    "families": families,
                    "experiment_count": 0,
                    "llm_experiment_count": 0,
                    "deterministic_experiment_count": 0,
                    "tool_call_count": 0,
                    "passed_count": 0,
                    "deployd_count": 0,
                    "first_created_at": created_at,
                    "last_created_at": created_at,
                    "best_spec_hash": None,
                    "best_family": str(payload.get("best_family") or "") or None,
                    "best_aggregate_score": None,
                    "best_validation_total_return": None,
                    "best_pre_audit_canonical_total_return": None,
                    "llm_provider": llm_provider,
                    "llm_model": llm_model,
                    "status": "running",
                    "series_points": [],
                }
            )
        placeholders.sort(
            key=lambda row: str(row.get("last_created_at") or ""),
            reverse=True,
        )
        return placeholders

    def _annotated_experiments(
        self,
        *,
        track: str | None = None,
        family: str | None = None,
    ) -> list[dict[str, Any]]:
        return self._attach_positions(
            [self._annotate_experiment(row) for row in self.ancestry.dashboard_rows(track=track, family=family)]
        )

    def experiments_payload(
        self,
        track: str | None = None,
        family: str | None = None,
    ) -> dict[str, Any]:
        scoped_rows = self._annotated_experiments(track=track)
        experiments = [row for row in scoped_rows if not family or row["family"] == family]
        runs = self.runs_payload(track=track, family=family)["runs"]

        summary: dict[str, Any] = {
            "experiment_count": len(experiments),
            "run_count": len(runs),
            "benchmark_run_count": sum(1 for row in runs if row.get("benchmark_mode")),
            "harness_run_count": sum(1 for row in runs if not row.get("benchmark_mode")),
            "deployd_count": sum(1 for row in experiments if row["deployd"]),
            "tool_traced_count": sum(1 for row in experiments if row.get("tool_trace", {}).get("tool_calls")),
            "tracks": cast(dict[str, dict[str, Any]], {}),
            "families": sorted({row["family"] for row in scoped_rows}),
        }
        for track_name in sorted({row["track"] for row in experiments}):
            rows = [row for row in experiments if row["track"] == track_name]
            if not rows:
                continue
            best = max(rows, key=lambda row: float(row["summary"].get("aggregate_score", 0.0)))
            summary["tracks"][track_name] = {
                "label": track_label(track_name),
                "count": len(rows),
                "best_spec_hash": best["spec_hash"],
                "best_aggregate_score": best["summary"].get("aggregate_score", 0.0),
                "best_sharpe": best["summary"].get("median_sharpe", 0.0),
                "best_return": best["summary"].get("median_total_return", 0.0),
                "best_cagr": best["summary"].get("median_cagr", 0.0),
            }

        return {
            "generated_at": self._now_iso(),
            "scope": {
                "track": track,
                "family": family,
            },
            "summary": summary,
            "runs": runs,
            "experiments": experiments,
            "selector_metric": {
                "key": "aggregate_score",
                "label": "Aggregate Score",
                "description": (
                    "Primary selection metric used by SigLab. It is computed on "
                    "the evaluator's selector windows. Validation slices are visible "
                    "during search, while the final audit slice stays out of the selector."
                ),
            },
        }

    def runs_payload(
        self,
        track: str | None = None,
        family: str | None = None,
    ) -> dict[str, Any]:
        experiments = self._annotated_experiments(track=track, family=family)
        runs = self.ancestry.run_summaries(track=track, family=family)
        series_by_run: dict[str, list[dict[str, Any]]] = {}
        families = sorted({str(row.get("family") or "") for row in experiments if row.get("family")})
        for experiment in experiments:
            run_session_id = str(experiment.get("run_session_id") or "")
            if not run_session_id:
                continue
            series_by_run.setdefault(run_session_id, []).append(
                {
                    "spec_hash": experiment.get("spec_hash"),
                    "family": experiment.get("family"),
                    "created_at": experiment.get("created_at"),
                    "run_position": experiment.get("run_position"),
                    "run_iteration_label": experiment.get("run_iteration_label"),
                    "passed": bool(experiment.get("passed")),
                    "deployd": bool(experiment.get("deployd")),
                    "aggregate_score": experiment.get("summary", {}).get("aggregate_score"),
                    "median_sharpe": experiment.get("summary", {}).get("median_sharpe"),
                    "median_cagr": experiment.get("summary", {}).get("median_cagr"),
                    "median_total_return": experiment.get("summary", {}).get("median_total_return"),
                    "median_calmar": experiment.get("summary", {}).get("median_calmar"),
                    "pre_audit_canonical_total_return": experiment.get("summary", {}).get("pre_audit_canonical_total_return"),
                    "validation_total_return": experiment.get("summary", {}).get("validation_total_return"),
                    "audit_total_return": experiment.get("summary", {}).get("audit_total_return"),
                }
            )
        annotated_runs = []
        for row in runs:
            annotated = dict(row)
            annotated["track"] = resolve_track(annotated.get("track"))
            annotated["track_label"] = track_label(annotated.get("track"))
            run_session_id = str(annotated.get("run_session_id") or "")
            run_experiments = [exp for exp in experiments if str(exp.get("run_session_id") or "") == run_session_id]
            annotated["series_points"] = list(series_by_run.get(run_session_id, []))
            annotated["tool_call_count"] = sum(int(exp.get("tool_call_count") or 0) for exp in run_experiments)
            primary_trace = next(
                (
                    exp.get("tool_trace") or {}
                    for exp in reversed(run_experiments)
                    if (exp.get("tool_trace") or {}).get("model")
                ),
                {},
            )
            annotated["llm_provider"] = str(primary_trace.get("provider") or "") or None
            annotated["llm_model"] = str(primary_trace.get("model") or "") or None
            annotated_runs.append(annotated)
        existing_run_ids = {str(row.get("run_session_id") or "") for row in annotated_runs}
        annotated_runs.extend(
            self._workspace_run_placeholders(
                track=track,
                family=family,
                existing_run_ids=existing_run_ids,
            )
        )
        best_run = max(
            annotated_runs,
            key=lambda row: float(row.get("best_aggregate_score") or float("-inf")),
            default=None,
        )
        return {
            "generated_at": self._now_iso(),
            "scope": {
                "track": track,
                "family": family,
            },
            "summary": {
                "run_count": len(annotated_runs),
                "benchmark_run_count": sum(1 for row in annotated_runs if row.get("benchmark_mode")),
                "harness_run_count": sum(1 for row in annotated_runs if not row.get("benchmark_mode")),
                "experiment_count": len(experiments),
                "deployd_count": sum(1 for row in experiments if row.get("deployd")),
                "families": families,
                "best_run_session_id": best_run.get("run_session_id") if best_run else None,
                "best_run_label": best_run.get("run_label") if best_run else None,
                "best_aggregate_score": best_run.get("best_aggregate_score") if best_run else None,
            },
            "runs": annotated_runs,
        }

    def _load_ops_artifact(self, relative_path: str) -> dict[str, Any]:
        path = (self.settings.root_dir / relative_path).resolve()
        root = self.settings.root_dir.resolve()
        if root not in path.parents and path != root:
            return {
                "status": "blocked",
                "path": relative_path,
                "error": "artifact path escapes repo root",
                "payload": None,
            }
        if not path.exists():
            return {
                "status": "missing",
                "path": relative_path,
                "error": "artifact missing",
                "payload": None,
            }
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            return {
                "status": "malformed",
                "path": relative_path,
                "error": str(exc),
                "payload": None,
            }
        if not isinstance(payload, dict):
            return {
                "status": "malformed",
                "path": relative_path,
                "error": "artifact root must be a JSON object",
                "payload": None,
            }
        mtime = datetime.fromtimestamp(path.stat().st_mtime).astimezone()
        age_seconds = max(0.0, (datetime.now(UTC) - mtime.astimezone(UTC)).total_seconds())
        return {
            "status": "present",
            "path": relative_path,
            "mtime": mtime.isoformat(),
            "age_seconds": round(age_seconds, 3),
            "freshness": (
                "fresh"
                if age_seconds <= 15 * 60
                else "stale"
                if age_seconds <= 24 * 60 * 60
                else "expired"
            ),
            "payload": payload,
        }

    def _summarize_ops_artifacts(
        self,
        artifacts: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        demo = dict(artifacts["demo_manifest"].get("payload") or {})
        telemetry = dict(artifacts["telemetry"].get("payload") or {})
        market = dict(artifacts["market_report"].get("payload") or {})
        preflight = dict(artifacts["sodex_preflight"].get("payload") or {})
        wave = dict(artifacts["wave_status"].get("payload") or {})
        readiness = dict(demo.get("readiness") or {})
        decision_support = dict(market.get("decision_support") or {})
        signal_summary = dict(market.get("signal_summary") or {})
        signed_path = dict(preflight.get("signed_path") or {})
        provider_metrics = dict(telemetry.get("provider_metrics") or {})
        return {
            "buildathon": {
                "sosovalue_flow": readiness.get("sosovalue_input_to_output"),
                "sodex_public_market_data": readiness.get("sodex_public_market_data"),
                "provider_metrics_present": readiness.get("provider_metrics_present"),
                "market_report_status": demo.get("market_report_status") or market.get("status"),
                "red_flags": list(demo.get("red_flags") or []),
                "demo_artifacts": list(demo.get("artifacts") or []),
            },
            "market": {
                "status": market.get("status"),
                "entity": market.get("entity"),
                "headline": signal_summary.get("headline") or demo.get("market_report_headline"),
                "flow_direction": signal_summary.get("flow_direction"),
                "quote_bid": signal_summary.get("quote_bid"),
                "quote_ask": signal_summary.get("quote_ask"),
                "stance": decision_support.get("stance"),
                "warnings": list(market.get("warnings") or []),
            },
            "sodex": {
                "public_read_ready": preflight.get("public_read_ready"),
                "schema_pinned": preflight.get("schema_pinned"),
                "live_write_allowed": preflight.get("live_write_allowed"),
                "live_write_refusal_reason": preflight.get("live_write_refusal_reason"),
                "signed_path_ready": signed_path.get("ready"),
                "request_weight_budget_per_minute": preflight.get("request_weight_budget_per_minute"),
                "next_actions": list(preflight.get("next_actions") or []),
            },
            "telemetry": {
                "confidence": telemetry.get("confidence"),
                "trace_count": telemetry.get("trace_count"),
                "tool_invocation_count": telemetry.get("tool_invocation_count"),
                "tool_error_count": telemetry.get("tool_error_count"),
                "provider_metrics_status": telemetry.get("provider_metrics_status"),
                "provider_request_count": provider_metrics.get("request_count"),
                "estimated_credits": provider_metrics.get("estimated_credits"),
                "returned_input_tokens": provider_metrics.get("returned_input_tokens"),
                "returned_output_tokens": provider_metrics.get("returned_output_tokens"),
                "context_pressure_events": provider_metrics.get("context_pressure_events"),
                "credit_pressure_events": provider_metrics.get("credit_pressure_events"),
                "model_counts": telemetry.get("model_counts") or {},
                "stage_counts": telemetry.get("stage_counts") or {},
            },
            "wave": {
                "wave_number": wave.get("wave_number"),
                "phase": wave.get("phase"),
                "status": wave.get("status"),
                "goal": wave.get("goal"),
                "agents": list(wave.get("agents") or []),
                "outputs": list(wave.get("outputs") or []),
                "blockers": list(wave.get("blockers") or []),
                "validation_status": wave.get("validation_status"),
                "next_decision": wave.get("next_decision"),
                "stop_allowed": wave.get("stop_allowed"),
                "unsafe_claims": list(wave.get("unsafe_claims") or []),
            },
        }

    def ops_payload(self) -> dict[str, Any]:
        artifacts = {
            "demo_manifest": self._load_ops_artifact("runs/demo_manifest_latest.json"),
            "telemetry": self._load_ops_artifact("runs/latest_telemetry_report.json"),
            "market_report": self._load_ops_artifact("runs/market_report_latest.json"),
            "sodex_preflight": self._load_ops_artifact("runs/sodex_preflight_latest.json"),
            "wave_status": self._load_ops_artifact("runs/wave_status_latest.json"),
        }
        return {
            "generated_at": self._now_iso(),
            "artifact_status": {
                name: {
                    "status": artifact.get("status"),
                    "path": artifact.get("path"),
                    "mtime": artifact.get("mtime"),
                    "age_seconds": artifact.get("age_seconds"),
                    "freshness": artifact.get("freshness"),
                    "error": artifact.get("error"),
                }
                for name, artifact in artifacts.items()
            },
            "summary": self._summarize_ops_artifacts(artifacts),
        }

    def experiment_detail_payload(self, spec_hash: str) -> dict[str, Any] | None:
        detail = self.ancestry.experiment_detail(spec_hash)
        if detail is None:
            return None
        track = resolve_track(detail.get("track"))
        track_rows = self._attach_positions(
            [self._annotate_experiment(row) for row in self.ancestry.dashboard_rows(track=track)]
        )
        positions = next(
            (row for row in track_rows if str(row.get("spec_hash") or "") == spec_hash),
            {},
        )
        detail = self._annotate_experiment(detail, include_artifact=True)
        for key in [
            "generation",
            "track_generation",
            "global_index",
            "run_position",
            "run_iteration_number",
            "run_phase_label",
            "run_iteration_label",
        ]:
            if key in positions:
                detail[key] = positions[key]
        return {
            "generated_at": self._now_iso(),
            "experiment": detail,
        }

    def experiment_series_payload(self, spec_hash: str) -> dict[str, Any] | None:
        detail = self.ancestry.experiment_detail(spec_hash)
        if detail is None:
            return None
        artifact = dict(detail.get("artifact") or {})
        annotated = self._annotate_experiment(dict(detail), include_artifact=True)
        canonical_run = self._augment_canonical_run(dict(artifact.get("canonical_run") or {}))
        return {
            "generated_at": self._now_iso(),
            "experiment": {
                "spec_hash": annotated.get("spec_hash"),
                "created_at": annotated.get("created_at"),
                "track": annotated.get("track"),
                "track_label": annotated.get("track_label"),
                "family": annotated.get("family"),
                "summary": annotated.get("summary"),
                "spec": annotated.get("spec"),
                "source": annotated.get("source"),
                "mode_flags": annotated.get("mode_flags"),
                "timing": annotated.get("timing"),
                "artifact_path": annotated.get("artifact_path"),
                "deployment_readiness": annotated.get("deployment_readiness"),
                "live_deployment": annotated.get("live_deployment"),
            },
            "series_available": bool(canonical_run),
            "canonical_run": canonical_run or None,
            "compiled_metadata": artifact.get("compiled_metadata")
            or artifact.get("compiledMetadata")
            or {},
        }

    async def deploy_experiment(
        self,
        *,
        spec_hash: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        manager = LiveDeploymentManager(
            self.settings,
            self.ancestry,
            claude=ClaudeClient(self.settings),
        )
        record = await manager.deploy(
            spec_hash=spec_hash,
            wallet_label=payload.get("wallet_label"),
            config_path=str(payload.get("config_path") or self.settings.sosovalue_config_path),
            interval_seconds=(
                int(payload["interval_seconds"])
                if payload.get("interval_seconds") is not None
                else None
            ),
            job_name=payload.get("job_name"),
            dry_run=bool(payload.get("dry_run", True)),
            llm_finalize=bool(payload.get("llm_finalize", False)),
            schedule=bool(payload.get("schedule", False)),
        )
        return {
            "generated_at": self._now_iso(),
            "deployment": self._display_deployment(record.to_dict()),
        }

    def static_path(self, relative_path: str) -> Path:
        clean = relative_path.lstrip("/") or "index.html"
        spec = (self.static_dir / clean).resolve()
        if self.static_dir.resolve() not in spec.parents and spec != self.static_dir.resolve():
            raise FileNotFoundError(clean)
        return spec

    def _now_iso(self) -> str:
        return datetime.now(UTC).isoformat()

    def _annotate_experiment(
        self,
        experiment: dict[str, Any],
        *,
        include_artifact: bool = False,
    ) -> dict[str, Any]:
        spec = dict(experiment.get("spec") or {})
        summary = dict(experiment.get("summary") or {})
        research_summary = dict(experiment.get("research_summary") or {})
        raw_artifact_path = experiment.get("artifact_path")
        artifact = dict(experiment.get("artifact") or {}) if include_artifact else {}
        if not artifact and raw_artifact_path:
            artifact_path = resolve_path_from_root(
                raw_artifact_path,
                root_dir=self.settings.root_dir,
            )
            if artifact_path.exists():
                try:
                    artifact = dict(json.loads(artifact_path.read_text()))
                except Exception:
                    artifact = {}
        compiled_metadata = (
            artifact.get("compiled_metadata")
            or artifact.get("compiledMetadata")
            or {}
        )
        if "median_cagr" not in summary or not _is_finite_number(summary.get("median_cagr")):
            windows = artifact.get("windows") or []
            cagr_values = [
                float(window.get("stats", {}).get("cagr"))
                for window in windows
                if _is_finite_number(window.get("stats", {}).get("cagr"))
            ]
            if cagr_values:
                midpoint = len(cagr_values) // 2
                ordered = sorted(cagr_values)
                summary["median_cagr"] = (
                    ordered[midpoint]
                    if len(ordered) % 2 == 1
                    else 0.5 * (ordered[midpoint - 1] + ordered[midpoint])
                )
            else:
                summary["median_cagr"] = None
        if "validation_available" not in summary:
            summary["validation_available"] = bool(summary.get("holdout_available"))
            summary["validation_sharpe"] = summary.get("holdout_sharpe")
            summary["validation_total_return"] = summary.get("holdout_total_return")
            summary["validation_cagr"] = summary.get("holdout_cagr")
            summary["validation_calmar"] = summary.get("holdout_calmar")
            summary["validation_max_drawdown"] = summary.get("holdout_max_drawdown")
            summary["validation_liquidated"] = summary.get("holdout_liquidated")
        if "audit_available" not in summary:
            summary["audit_available"] = False
            summary["audit_sharpe"] = None
            summary["audit_total_return"] = None
            summary["audit_cagr"] = None
            summary["audit_calmar"] = None
            summary["audit_max_drawdown"] = None
            summary["audit_liquidated"] = None
        bias_controls = dict(compiled_metadata.get("bias_controls") or {})
        params = dict(spec.get("params") or {})
        tool_trace = dict(research_summary.get("llm_tool_trace") or {})
        tool_trace_stages = self._resolve_tool_trace_stages(research_summary)
        aggregated_tool_calls: list[dict[str, Any]] = []
        for stage in tool_trace_stages:
            for call in list(stage.get("tool_calls") or []):
                normalized_call = dict(call or {})
                normalized_call.setdefault("stage", stage.get("stage"))
                aggregated_tool_calls.append(normalized_call)
        primary_tool_trace = next(
            (stage for stage in tool_trace_stages if list(stage.get("tool_calls") or [])),
            tool_trace_stages[0] if tool_trace_stages else None,
        )

        experiment["track"] = resolve_track(experiment.get("track"))
        experiment["track_label"] = track_label(experiment.get("track"))
        experiment["spec"] = spec
        experiment["summary"] = summary
        experiment["research_summary"] = research_summary
        run_context = dict(research_summary.get("run_context") or {})
        experiment["run_session_id"] = str(
            run_context.get("run_session_id") or f"legacy::{experiment.get('spec_hash')}"
        )
        experiment["runner_label"] = str(
            run_context.get("runner_label")
            or ("external_agent" if run_context.get("benchmark_mode") else "siglab_harness")
        )
        experiment["run_label"] = str(run_context.get("run_label") or experiment["run_session_id"])
        experiment["run_kind"] = "benchmark" if run_context.get("benchmark_mode") else "harness"
        experiment["benchmark_mode"] = bool(run_context.get("benchmark_mode"))
        experiment["benchmark_deck"] = run_context.get("benchmark_deck")
        experiment["run_iteration_number"] = (
            int(run_context["iteration_number"])
            if run_context.get("iteration_number") is not None
            else None
        )
        experiment["run_phase_label"] = str(run_context.get("phase_label") or "")
        if experiment["run_iteration_number"] is not None and experiment["run_phase_label"]:
            experiment["run_iteration_label"] = (
                f"{experiment['run_phase_label']} {experiment['run_iteration_number']}"
            )
        elif experiment["run_iteration_number"] is not None:
            experiment["run_iteration_label"] = f"iter {experiment['run_iteration_number']}"
        else:
            experiment["run_iteration_label"] = ""
        experiment["series_available"] = bool(artifact.get("canonical_run"))
        experiment["tool_trace_stages"] = tool_trace_stages
        experiment["tool_trace"] = {
            "track": tool_trace.get("track"),
            "parent_family": tool_trace.get("parent_family"),
            "parent_hash": tool_trace.get("parent_hash"),
            "spec_count": tool_trace.get("spec_count"),
            "error": (primary_tool_trace or {}).get("error") or tool_trace.get("error"),
            "provider": (primary_tool_trace or {}).get("provider"),
            "model": (primary_tool_trace or {}).get("model"),
            "thinking_mode": (primary_tool_trace or {}).get("thinking_mode"),
            "tool_rounds_used": sum(
                int(stage.get("tool_rounds_used") or 0) for stage in tool_trace_stages
            ),
            "tool_count_available": max(
                [int(stage.get("tool_count_available") or 0) for stage in tool_trace_stages] or [0]
            ),
            "tool_calls": aggregated_tool_calls,
            "final_content_preview": (primary_tool_trace or {}).get("final_content_preview"),
            "response_finish_reason": (primary_tool_trace or {}).get("response_finish_reason"),
            "stage_count": len(tool_trace_stages),
        }
        experiment["tool_call_count"] = len(aggregated_tool_calls)
        experiment["skill_value_report"] = self._skill_value_report(aggregated_tool_calls)
        lifecycle_policy = dict(compiled_metadata.get("lifecycle_policy") or {})
        experiment["roll_lifecycle"] = {
            "policy": lifecycle_policy,
            "roll_event_count": int(compiled_metadata.get("roll_event_count") or 0),
            "roll_events": list(compiled_metadata.get("roll_events") or []),
            "badges": list(compiled_metadata.get("pt_strategy_badges") or []),
            "eligible_market_count_min": compiled_metadata.get("eligible_market_count_min"),
            "eligible_market_count_max": compiled_metadata.get("eligible_market_count_max"),
            "eligible_market_count_median": compiled_metadata.get("eligible_market_count_median"),
            "eligible_market_count_latest": compiled_metadata.get("eligible_market_count_latest"),
            "markets_entered_during_backtest": list(
                compiled_metadata.get("markets_entered_during_backtest") or []
            ),
        }
        experiment["mode_flags"] = {
            "long_enabled": params.get("long_enabled"),
            "short_enabled": params.get("short_enabled"),
            "hedge_mode": params.get("hedge_mode", compiled_metadata.get("hedge_mode", "none")),
            "hedge_ratio": params.get("hedge_ratio", compiled_metadata.get("hedge_ratio")),
        }
        experiment["feature_hash"] = experiment.get("feature_hash") or compiled_metadata.get("feature_hash")
        experiment["timing"] = {
            "signal_timing": compiled_metadata.get("signal_timing", "unknown"),
            "bundle_as_of": compiled_metadata.get("bundle_as_of"),
            "history_start": compiled_metadata.get("history_start"),
            "history_end": compiled_metadata.get("history_end"),
            "bias_controls": bias_controls,
        }
        experiment["feature_preview"] = list(spec.get("features") or [])[:4]
        experiment["source"] = compiled_metadata.get("source")
        readiness = deployment_readiness(
            {
                "track": experiment["track"],
                "family": experiment["family"],
                "spec_hash": experiment["spec_hash"],
                "spec": spec,
                "summary": summary,
                "artifact": artifact,
            }
        )
        experiment["deployment_readiness"] = readiness
        experiment["artifact_path"] = self._display_path(raw_artifact_path)
        experiment["live_deployment"] = self._display_deployment(experiment.get("deployment"))
        if include_artifact and artifact:
            artifact = dict(artifact)
            artifact.pop("canonical_run", None)
        experiment["artifact"] = artifact if include_artifact else experiment.get("artifact")
        return experiment

    def _attach_positions(self, experiments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        generation_by_track: dict[str, int] = {}
        run_position_by_session: dict[str, int] = {}
        best_by_track_metric: dict[str, float] = {}
        for global_index, experiment in enumerate(experiments, start=1):
            track_name = str(experiment.get("track") or "")
            run_session_id = str(experiment.get("run_session_id") or "")
            generation_by_track[track_name] = generation_by_track.get(track_name, 0) + 1
            run_position_by_session[run_session_id] = run_position_by_session.get(run_session_id, 0) + 1
            experiment["track_generation"] = generation_by_track[track_name]
            experiment["generation"] = generation_by_track[track_name]
            experiment["global_index"] = global_index
            experiment["run_position"] = run_position_by_session[run_session_id]
            score = float(experiment["summary"].get("aggregate_score", 0.0))
            best_by_track_metric[track_name] = max(
                score,
                best_by_track_metric.get(track_name, float("-inf")),
            )
            experiment["best_so_far_aggregate_score"] = best_by_track_metric[track_name]
        return experiments

    def _augment_canonical_run(self, canonical_run: dict[str, Any]) -> dict[str, Any]:
        if not canonical_run:
            return canonical_run
        if canonical_run.get("visual_split"):
            return canonical_run

        equity_curve = dict(canonical_run.get("equity_curve") or {})
        timestamps = list(equity_curve.get("index") or [])
        size = len(timestamps)
        if size < 2:
            canonical_run["visual_split"] = {
                "strict_holdout": False,
                "note": (
                    "This artifact predates the current in-sample, validation, and audit split metadata."
                ),
                "ranges": [],
            }
            return canonical_run

        split_idx = max(1, min(size - 1, size * 2 // 3))
        canonical_run["visual_split"] = {
            "strict_holdout": False,
            "note": (
                "This artifact predates the current three-way split. The chart shows a "
                "fallback in-sample versus holdout view only."
            ),
            "ranges": [
                {
                    "label": "In-Sample View",
                    "kind": "in_sample",
                    "start_idx": 0,
                    "end_idx": split_idx,
                    "start_timestamp": timestamps[0],
                    "end_timestamp": timestamps[split_idx - 1],
                },
                {
                    "label": "Final Third",
                    "kind": "holdout_view",
                    "start_idx": split_idx,
                    "end_idx": size,
                    "start_timestamp": timestamps[split_idx],
                    "end_timestamp": timestamps[-1],
                },
            ],
        }
        canonical_run.setdefault("evaluation_windows", [])
        return canonical_run


class DashboardHandler(BaseHTTPRequestHandler):
    server: "DashboardServer"

    def do_GET(self) -> None:  # noqa: N802
        self._handle_request(send_body=True)

    def do_HEAD(self) -> None:  # noqa: N802
        self._handle_request(send_body=False)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            if parsed.path.startswith("/api/experiments/") and parsed.path.endswith("/deploy"):
                spec_hash = parsed.path.split("/")[-2]
                payload = self._read_json_body()
                result = asyncio.run(
                    self.server.app.deploy_experiment(
                        spec_hash=spec_hash,
                        payload=payload,
                    )
                )
                self._json_response(result, send_body=True)
                return
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
        except Exception as exc:  # noqa: BLE001
            self.send_error(HTTPStatus.BAD_REQUEST, str(exc))

    def _handle_request(self, *, send_body: bool) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path in {"/", "/index.html"}:
                self._serve_static("index.html", send_body=send_body)
                return
            if parsed.path == "/ops":
                self._serve_static("ops.html", send_body=send_body)
                return
            if parsed.path in {"/app.js", "/common.js", "/home.js", "/styles.css", "/experiment.js", "/experiment.html", "/run.html", "/ops.html", "/ops.js"}:
                self._serve_static(parsed.path.lstrip("/"), send_body=send_body)
                return
            if parsed.path.startswith("/runs/"):
                self._serve_static("run.html", send_body=send_body)
                return
            if parsed.path.startswith("/experiments/"):
                self._serve_static("experiment.html", send_body=send_body)
                return
            if parsed.path == "/api/experiments":
                query = parse_qs(parsed.query)
                track = canonical_track_name(query.get("track", [None])[0])
                family = query.get("family", [None])[0] or None
                self._json_response(
                    self.server.app.experiments_payload(track=track, family=family),
                    send_body=send_body,
                )
                return
            if parsed.path == "/api/runs":
                query = parse_qs(parsed.query)
                track = canonical_track_name(query.get("track", [None])[0])
                family = query.get("family", [None])[0] or None
                self._json_response(
                    self.server.app.runs_payload(track=track, family=family),
                    send_body=send_body,
                )
                return
            if parsed.path == "/api/ops":
                self._json_response(self.server.app.ops_payload(), send_body=send_body)
                return
            if parsed.path.startswith("/api/experiments/") and parsed.path.endswith("/series"):
                spec_hash = parsed.path.split("/")[-2]
                payload = self.server.app.experiment_series_payload(spec_hash)
                if payload is None:
                    self.send_error(HTTPStatus.NOT_FOUND, "Experiment not found")
                    return
                self._json_response(payload, send_body=send_body)
                return
            if parsed.path.startswith("/api/experiments/"):
                spec_hash = parsed.path.rsplit("/", 1)[-1]
                payload = self.server.app.experiment_detail_payload(spec_hash)
                if payload is None:
                    self.send_error(HTTPStatus.NOT_FOUND, "Experiment not found")
                    return
                self._json_response(payload, send_body=send_body)
                return
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
        except FileNotFoundError:
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
        except Exception as exc:  # noqa: BLE001
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def _serve_static(self, relative_path: str, *, send_body: bool) -> None:
        path = self.server.app.static_path(relative_path)
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(relative_path)
        mime_type, _ = mimetypes.guess_type(path.name)
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if send_body:
            self.wfile.write(body)

    def _json_response(self, payload: dict[str, Any], *, send_body: bool) -> None:
        body = json.dumps(json_safe(payload), allow_nan=False).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if send_body:
            self.wfile.write(body)

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        decoded = json.loads(raw.decode("utf-8"))
        if not isinstance(decoded, dict):
            raise ValueError("Request body must be a JSON object")
        return decoded


class DashboardServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], app: DashboardApp) -> None:
        self.app = app
        super().__init__(server_address, DashboardHandler)


def run_dashboard_server(
    settings: SiglabConfig,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> None:
    app = DashboardApp(
        settings=settings,
        ancestry=LineageStore(settings.ancestry_db_path),
        static_dir=Path(__file__).resolve().parent / "static",
    )
    server = DashboardServer((host, port), app)
    print(f"SigLab dashboard listening on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()



