from __future__ import annotations

import asyncio
import json
import math
import mimetypes
from dataclasses import dataclass
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from wayfinder_autolab.io_utils import json_safe, load_json_path
from wayfinder_autolab.llm_metadata import (
    default_llm_model_display,
    infer_llm_provider,
    resolve_llm_provider,
)
from wayfinder_autolab.live import LivePromotionManager, promotion_readiness
from wayfinder_autolab.llm import KimiClient
from wayfinder_autolab.path_utils import display_path, resolve_path_from_root
from wayfinder_autolab.search.lineage import LineageStore
from wayfinder_autolab.settings import AutolabSettings
from wayfinder_autolab.track_registry import canonical_track_name, track_label


def _is_finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


@dataclass
class DashboardApp:
    settings: AutolabSettings
    lineage: LineageStore
    static_dir: Path

    def _dashboard_llm_provider(self) -> str:
        return resolve_llm_provider(self.settings)

    def _dashboard_llm_model(self) -> str:
        return default_llm_model_display(self.settings, provider=self._dashboard_llm_provider())

    def _display_path(self, value: str | Path | None) -> str | None:
        return display_path(value, root_dir=self.settings.root_dir)

    def _display_promotion(self, promotion: dict[str, Any] | None) -> dict[str, Any] | None:
        if not promotion:
            return None
        normalized = dict(promotion)
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
            return cache[cache_key]
        payload = load_json_path(path)
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
        trace = dict(payload.get("kimi_trace") or {})
        if not trace:
            for key in ("attempts", "planner_attempts", "repair_attempts"):
                attempts = list(payload.get(key) or [])
                for attempt in reversed(attempts):
                    attempt_trace = dict((attempt or {}).get("kimi_trace") or {})
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
                    "candidate_count": tool_trace.get("candidate_count"),
                }
            ]
        return []

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
        artifacts_root = getattr(self.settings, "artifact_dir", self.settings.root_dir / "artifacts")
        if not artifacts_root.exists():
            return placeholders
        workspace_glob = artifacts_root.glob("*/workspaces/*/current/SESSION_STATE.json")
        for state_path in sorted(workspace_glob):
            try:
                payload = json.loads(state_path.read_text())
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                continue
            run_session_id = str(payload.get("run_session_id") or state_path.parents[1].name)
            if not run_session_id or run_session_id in existing_run_ids:
                continue
            track_name = canonical_track_name(state_path.parents[3].name) or state_path.parents[3].name
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
                    "agent_label": "autolab_harness",
                    "run_kind": "harness",
                    "memory_scope": str(payload.get("memory_scope") or "track_global"),
                    "benchmark_mode": False,
                    "benchmark_deck": None,
                    "phase_labels": [str(payload.get("phase_label") or "starting")],
                    "families": families,
                    "experiment_count": 0,
                    "llm_experiment_count": 0,
                    "deterministic_experiment_count": 0,
                    "tool_call_count": 0,
                    "passed_count": 0,
                    "promoted_count": 0,
                    "first_created_at": created_at,
                    "last_created_at": created_at,
                    "best_candidate_hash": None,
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
            [self._annotate_experiment(row) for row in self.lineage.dashboard_rows(track=track, family=family)]
        )

    def experiments_payload(
        self,
        track: str | None = None,
        family: str | None = None,
    ) -> dict[str, Any]:
        scoped_rows = self._annotated_experiments(track=track)
        experiments = [row for row in scoped_rows if not family or row["family"] == family]
        runs = self.runs_payload(track=track, family=family)["runs"]

        summary = {
            "experiment_count": len(experiments),
            "run_count": len(runs),
            "benchmark_run_count": sum(1 for row in runs if row.get("benchmark_mode")),
            "harness_run_count": sum(1 for row in runs if not row.get("benchmark_mode")),
            "promoted_count": sum(1 for row in experiments if row["promoted"]),
            "tool_traced_count": sum(1 for row in experiments if row.get("tool_trace", {}).get("tool_calls")),
            "tracks": {},
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
                "best_candidate_hash": best["candidate_hash"],
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
                    "Primary selection metric used by Autolab. It is computed on "
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
        runs = self.lineage.run_summaries(track=track, family=family)
        series_by_run: dict[str, list[dict[str, Any]]] = {}
        families = sorted({str(row.get("family") or "") for row in experiments if row.get("family")})
        for experiment in experiments:
            run_session_id = str(experiment.get("run_session_id") or "")
            if not run_session_id:
                continue
            series_by_run.setdefault(run_session_id, []).append(
                {
                    "candidate_hash": experiment.get("candidate_hash"),
                    "family": experiment.get("family"),
                    "created_at": experiment.get("created_at"),
                    "run_position": experiment.get("run_position"),
                    "run_iteration_label": experiment.get("run_iteration_label"),
                    "passed": bool(experiment.get("passed")),
                    "promoted": bool(experiment.get("promoted")),
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
            annotated["track"] = canonical_track_name(annotated.get("track")) or annotated.get("track")
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
                "promoted_count": sum(1 for row in experiments if row.get("promoted")),
                "families": families,
                "best_run_session_id": best_run.get("run_session_id") if best_run else None,
                "best_run_label": best_run.get("run_label") if best_run else None,
                "best_aggregate_score": best_run.get("best_aggregate_score") if best_run else None,
            },
            "runs": annotated_runs,
        }

    def experiment_detail_payload(self, candidate_hash: str) -> dict[str, Any] | None:
        detail = self.lineage.experiment_detail(candidate_hash)
        if detail is None:
            return None
        track = canonical_track_name(detail.get("track")) or detail.get("track")
        track_rows = self._attach_positions(
            [self._annotate_experiment(row) for row in self.lineage.dashboard_rows(track=track)]
        )
        positions = next(
            (row for row in track_rows if str(row.get("candidate_hash") or "") == candidate_hash),
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

    def experiment_series_payload(self, candidate_hash: str) -> dict[str, Any] | None:
        detail = self.lineage.experiment_detail(candidate_hash)
        if detail is None:
            return None
        artifact = dict(detail.get("artifact") or {})
        annotated = self._annotate_experiment(dict(detail), include_artifact=True)
        canonical_run = self._augment_canonical_run(dict(artifact.get("canonical_run") or {}))
        return {
            "generated_at": self._now_iso(),
            "experiment": {
                "candidate_hash": annotated.get("candidate_hash"),
                "created_at": annotated.get("created_at"),
                "track": annotated.get("track"),
                "track_label": annotated.get("track_label"),
                "family": annotated.get("family"),
                "summary": annotated.get("summary"),
                "candidate": annotated.get("candidate"),
                "source": annotated.get("source"),
                "mode_flags": annotated.get("mode_flags"),
                "timing": annotated.get("timing"),
                "artifact_path": annotated.get("artifact_path"),
                "promotion_readiness": annotated.get("promotion_readiness"),
                "live_promotion": annotated.get("live_promotion"),
            },
            "series_available": bool(canonical_run),
            "canonical_run": canonical_run or None,
            "compiled_metadata": artifact.get("compiled_metadata")
            or artifact.get("compiledMetadata")
            or {},
        }

    async def promote_experiment(
        self,
        *,
        candidate_hash: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        manager = LivePromotionManager(
            self.settings,
            self.lineage,
            kimi=KimiClient(self.settings),
        )
        record = await manager.promote(
            candidate_hash=candidate_hash,
            wallet_label=payload.get("wallet_label"),
            config_path=str(payload.get("config_path") or self.settings.wayfinder_config_path),
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
            "promotion": self._display_promotion(record.to_dict()),
        }

    def static_path(self, relative_path: str) -> Path:
        clean = relative_path.lstrip("/") or "index.html"
        candidate = (self.static_dir / clean).resolve()
        if self.static_dir.resolve() not in candidate.parents and candidate != self.static_dir.resolve():
            raise FileNotFoundError(clean)
        return candidate

    def _now_iso(self) -> str:
        from datetime import UTC, datetime

        return datetime.now(UTC).isoformat()

    def _annotate_experiment(
        self,
        experiment: dict[str, Any],
        *,
        include_artifact: bool = False,
    ) -> dict[str, Any]:
        candidate = dict(experiment.get("candidate") or {})
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
        params = dict(candidate.get("params") or {})
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

        experiment["track"] = canonical_track_name(experiment.get("track")) or experiment.get("track")
        experiment["track_label"] = track_label(experiment.get("track"))
        experiment["candidate"] = candidate
        experiment["summary"] = summary
        experiment["research_summary"] = research_summary
        run_context = dict(research_summary.get("run_context") or {})
        experiment["run_session_id"] = str(
            run_context.get("run_session_id") or f"legacy::{experiment.get('candidate_hash')}"
        )
        experiment["agent_label"] = str(
            run_context.get("agent_label")
            or ("external_agent" if run_context.get("benchmark_mode") else "autolab_harness")
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
            "candidate_count": tool_trace.get("candidate_count"),
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
        experiment["feature_preview"] = list(candidate.get("features") or [])[:4]
        experiment["source"] = compiled_metadata.get("source")
        readiness = promotion_readiness(
            {
                "track": experiment["track"],
                "family": experiment["family"],
                "candidate_hash": experiment["candidate_hash"],
                "candidate": candidate,
                "summary": summary,
                "artifact": artifact,
            }
        )
        experiment["promotion_readiness"] = readiness
        experiment["artifact_path"] = self._display_path(raw_artifact_path)
        experiment["live_promotion"] = self._display_promotion(experiment.get("promotion"))
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
            if parsed.path.startswith("/api/experiments/") and parsed.path.endswith("/promote"):
                candidate_hash = parsed.path.split("/")[-2]
                payload = self._read_json_body()
                result = asyncio.run(
                    self.server.app.promote_experiment(
                        candidate_hash=candidate_hash,
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
            if parsed.path in {"/app.js", "/common.js", "/home.js", "/styles.css", "/experiment.js", "/experiment.html", "/run.html"}:
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
            if parsed.path.startswith("/api/experiments/") and parsed.path.endswith("/series"):
                candidate_hash = parsed.path.split("/")[-2]
                payload = self.server.app.experiment_series_payload(candidate_hash)
                if payload is None:
                    self.send_error(HTTPStatus.NOT_FOUND, "Experiment not found")
                    return
                self._json_response(payload, send_body=send_body)
                return
            if parsed.path.startswith("/api/experiments/"):
                candidate_hash = parsed.path.rsplit("/", 1)[-1]
                payload = self.server.app.experiment_detail_payload(candidate_hash)
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
    settings: AutolabSettings,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> None:
    app = DashboardApp(
        settings=settings,
        lineage=LineageStore(settings.lineage_db_path),
        static_dir=Path(__file__).resolve().parent / "static",
    )
    server = DashboardServer((host, port), app)
    print(f"Autolab dashboard listening on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
