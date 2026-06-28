from __future__ import annotations

import json
import logging
import math
import os
import time
from siglab.config import _DEFAULT_PORT
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast
from collections.abc import AsyncIterator

if TYPE_CHECKING:
    from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.staticfiles import StaticFiles
    from fastapi.templating import Jinja2Templates
    from starlette.middleware.base import BaseHTTPMiddleware

from fastapi import APIRouter, Request, Response

from siglab.config import SiglabConfig, load_settings
from siglab.data.deployment_store import DeploymentStore
from siglab.utils import load_json_path
from siglab.live.exporter import LiveDeploymentManager, deployment_readiness
from siglab.llm import ClaudeClient
from siglab.llm.llm import (
    default_llm_model_display,
    infer_llm_provider,
    resolve_llm_provider,
)
from siglab.utils import display_path, resolve_path_from_root
from siglab.config import canonical_track_name, resolve_track, track_label
from siglab.utils import _now_iso, dget
from siglab.dashboard.experiment_repo import raw_experiments, raw_runs

logger = logging.getLogger(__name__)


class _LS(Protocol):
    def dashboard_rows(self) -> list[dict[str, Any]]: ...




def _ctd(config: SiglabConfig) -> dict[str, Any]:
    return {
        "system": {
            "root_dir": str(config.root_dir),
            "data_lake_dir": str(config.data_lake_dir),
            "artifact_dir": str(config.artifact_dir),
            "live_dir": str(config.live_dir),
            "ancestry_db_path": str(config.ancestry_db_path),
            "generated_strategy_dir": str(config.generated_strategy_dir),
            "population_size": config.population_size,
            "llm_provider": config.llm_provider,
            "memory_scope": config.memory_scope,
            "tracks": list(config.tracks),
        },
        "sosovalue": {
            "config_path": str(config.sosovalue_config_path),
            "openapi_base_url": config.sosovalue_base_url,
            "etf_base_url": config.etf_base_url,
            "news_base_url": config.news_base_url,
            "timeout_s": config.sosovalue_timeout_s,
            "retries": config.sosovalue_retries,
            "api_key_configured": config.sosovalue_api_key_override is not None,
        },
        "openai": {
            "model": config.openmodel_model,
            "base_url": config.openmodel_base_url,
            "timeout_s": config.claude_timeout_s,
            "api_key_configured": config.openmodel_api_key is not None,
        },
    }



@dataclass
class DashboardState:
    config: SiglabConfig | None = None
    deployment_store: DeploymentStore | None = None
    lineage: _LS | None = None
    static_dir: Path | None = None
    templates: Jinja2Templates | None = None
    start_time: float = 0.0
    _json_cache: dict[str, Any] = field(default_factory=dict)
    _experiments_cache: dict[str, Any] = field(default_factory=dict)
    _experiments_cache_ts: float = 0.0
    _EXPERIMENTS_CACHE_TTL: float = 30.0
    _ops_cache: dict[str, Any] | None = None
    _ops_cache_ts: float = 0.0
    _runs_cache_ts: float = 0.0
    _RUNS_CACHE_TTL: float = 30.0
    _runs_cache: dict[str, dict[str, Any]] = field(default_factory=dict)
    def _lp(self) -> str:
        return "unknown" if self.config is None else resolve_llm_provider(self.config)

    def _lm(self) -> str:
        return "unknown" if self.config is None else default_llm_model_display(self.config)

    def _dp(self, value: str | Path | None) -> str | None:
        return (
            display_path(value, root_dir=self.config.root_dir)
            if self.config is not None
            else str(value)
            if value
            else None
        )

    def _dd(self, deployment: dict[str, Any] | None) -> dict[str, Any] | None:
        if not deployment:
            return None
        normalized = dict(deployment)
        for key in (
            "strategy_dir",
            "spec_path",
            "manifest_path",
            "readme_path",
            "config_path",
        ):
            normalized[key] = self._dp(normalized.get(key))
        return normalized

    def _dbp(self) -> Path | None:
        return None if self.config is None else self.config.ancestry_db_path

    def _rss(self, research_summary: dict[str, Any]) -> list[dict[str, Any]]:
        def _load_trace(
            trace_path: str | Path | None, stage_name: str
        ) -> dict[str, Any] | None:
            if not trace_path:
                return None
            path = Path(trace_path).expanduser()
            if not path.is_absolute() and self.config is not None:
                path = (self.config.root_dir / path).resolve()
            cached = self._json_cache.get(str(path))
            if cached is not None:
                payload = cast(dict[str, Any] | None, cached)
            else:
                payload = load_json_path(path)
                self._json_cache[str(path)] = payload
            if not isinstance(payload, dict):
                return None
            trace = payload.get("claude_trace", {}) or {}
            if not trace:
                for key in ("attempts", "planner_attempts", "repair_attempts"):
                    attempts = list(payload.get(key) or [])
                    for attempt in reversed(attempts):
                        attempt_trace = (attempt or {}).get("claude_trace", {}) or {}
                        if attempt_trace:
                            trace = attempt_trace
                            break
                    if trace:
                        break
            if not trace and (not payload.get("error")):
                return None
            tool_calls = []
            for call in list(trace.get("tool_calls") or []):
                normalized_call = {**(call or {})}
                normalized_call.setdefault("stage", stage_name)
                tool_calls.append(normalized_call)
            model = trace.get("model")
            return {
                "stage": str(payload.get("stage") or stage_name),
                "trace_path": self._dp(trace_path),
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

        stages: list[dict[str, Any]] = []
        workspace = research_summary.get("workspace", {}) or {}
        for stage_name, path_key in (
            ("planner", "planner_trace_path"),
            ("writer", "writer_trace_path"),
            ("reflector", "reflector_trace_path"),
        ):
            trace_path = workspace.get(path_key)
            stage = _load_trace(trace_path, stage_name)
            if stage is not None:
                stages.append(stage)
        if stages:
            return stages
        tool_trace = research_summary.get("llm_tool_trace", {}) or {}
        trace_core = tool_trace.get("trace", {}) or {}
        if tool_trace or trace_core:
            legacy_calls = []
            for call in list(trace_core.get("tool_calls") or []):
                normalized_call = {**(call or {})}
                normalized_call.setdefault("stage", "proposal")
                legacy_calls.append(normalized_call)
            return [
                {
                    "stage": "proposal",
                    "trace_path": self._dp(tool_trace.get("log_path")),
                    "provider": trace_core.get("provider")
                    or infer_llm_provider(trace_core.get("model")),
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
                },
            ]
        return []

    def _svr(self, tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
            row["latency_cost_ms"] += float(
                call.get("latency_ms") or call.get("duration_ms") or 0.0,
            )
            row["token_context_cost"] += int(
                call.get("context_tokens")
                or call.get("input_tokens")
                or call.get("token_count")
                or 0,
            )
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
            if row["invocation_count"] > 8 and row["classification"] in {
                "LOW_VALUE",
                "MEDIUM_VALUE",
            }:
                row["classification"] = "NOISY"
            normalized = {**row}
            normalized["stages"] = sorted(row["stages"])
            normalized["latency_cost_ms"] = round(
                float(normalized["latency_cost_ms"]),
                3,
            )
            report.append(normalized)
        return report

    def _wsp(
        self,
        *,
        track: str | None = None,
        family: str | None = None,
        existing_run_ids: set[str],
    ) -> list[dict[str, Any]]:
        placeholders: list[dict[str, Any]] = []
        if self.config is None:
            return placeholders
        runs_root = getattr(self.config, "artifact_dir", self.config.root_dir / "runs")
        if not runs_root.exists():
            return placeholders
        for state_path in sorted(
            runs_root.glob("*/workspaces/*/current/SESSION_STATE.json"),
        ):
            try:
                payload = json.loads(state_path.read_text())
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                continue
            run_session_id = str(
                payload.get("run_session_id") or state_path.parents[1].name,
            )
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
                },
            )
            if family and family not in families:
                continue
            created_at = (
                datetime.fromtimestamp(state_path.stat().st_mtime)
                .astimezone()
                .isoformat()
            )
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
                    "llm_provider": self._lp(),
                    "llm_model": self._lm(),
                    "status": "running",
                    "series_points": [],
                },
            )
        placeholders.sort(
            key=lambda row: str(row.get("last_created_at") or ""),
            reverse=True,
        )
        return placeholders

    def _ed(self, spec_hash: str) -> dict[str, Any] | None:
        if self.deployment_store is not None:
            return self.deployment_store.experiment_detail(spec_hash)
        if (db_path := self._dbp()) is None:
            return None
        return next(
            (row for row in raw_experiments(str(db_path)) if row.get("spec_hash") == spec_hash),
            None,
        )

    def _aes(
        self,
        *,
        track: str | None = None,
        family: str | None = None,
    ) -> list[dict[str, Any]]:
        if (db_path := self._dbp()) is None:
            return []
        return self._ap(
            [self._ae(row) for row in raw_experiments(str(db_path), track=track, family=family)],
        )

    def _ni(self) -> str:
        return _now_iso()

    def _invalidate_caches(self) -> None:
        self._ops_cache = None
        self._ops_cache_ts = 0.0
        self._runs_cache.clear()
        self._runs_cache_ts = 0.0
        self._experiments_cache.clear()
        self._experiments_cache_ts = 0.0

    def _enrich_summary_fields(self, summary: dict[str, Any], artifact: dict[str, Any]) -> None:
        median_cagr = summary.get("median_cagr")
        if "median_cagr" not in summary or not (
            isinstance(median_cagr, (int, float)) and math.isfinite(float(median_cagr))
        ):
            windows = artifact.get("windows") or []
            cagr_values = [
                float(dget(window, "stats", "cagr"))
                for window in windows
                if isinstance(dget(window, "stats", "cagr"), (int, float))
                and math.isfinite(float(dget(window, "stats", "cagr")))
            ]
            if cagr_values:
                midpoint = len(cagr_values) // 2
                ordered = sorted(cagr_values)
                summary["median_cagr"] = (
                    ordered[midpoint] if len(ordered) % 2 == 1
                    else 0.5 * (ordered[midpoint - 1] + ordered[midpoint])
                )
            else:
                summary["median_cagr"] = None
        if "validation_available" not in summary:
            summary["validation_available"] = bool(summary.get("holdout_available"))
            for k in (
                "sharpe",
                "total_return",
                "cagr",
                "calmar",
                "max_drawdown",
                "liquidated",
            ):
                summary[f"validation_{k}"] = summary.get(f"holdout_{k}")
        if "audit_available" not in summary:
            summary["audit_available"] = False
            for k in (
                "sharpe",
                "total_return",
                "cagr",
                "calmar",
                "max_drawdown",
                "liquidated",
            ):
                summary[f"audit_{k}"] = None

    def _ae(
        self,
        experiment: dict[str, Any] | None,
        *,
        include_artifact: bool = False,
    ) -> dict[str, Any]:
        exp = experiment if isinstance(experiment, dict) else {}
        spec = exp.get("spec", {}) or {}
        summary = exp.get("summary", {}) or {}
        research_summary = exp.get("research_summary", {}) or {}
        raw_artifact_path = exp.get("artifact_path")
        artifact = (exp.get("artifact") or {}) if include_artifact else {}
        if not artifact and raw_artifact_path and (self.config is not None):
            artifact_path = resolve_path_from_root(
                raw_artifact_path,
                root_dir=self.config.root_dir,
            )
            if artifact_path.exists():
                try:
                    artifact = dict(json.loads(artifact_path.read_text()))
                except (json.JSONDecodeError, OSError, UnicodeDecodeError):
                    artifact = {}
        compiled_metadata = (
            artifact.get("compiled_metadata") or artifact.get("compiledMetadata") or {}
        )
        self._enrich_summary_fields(summary, artifact)
        bias_controls = compiled_metadata.get("bias_controls", {}) or {}
        params = spec.get("params", {}) or {}
        tool_trace = research_summary.get("llm_tool_trace", {}) or {}
        tool_trace_stages = self._rss(research_summary)
        aggregated_tool_calls: list[dict[str, Any]] = []
        for stage in tool_trace_stages:
            for call in list(stage.get("tool_calls") or []):
                normalized_call = {**(call or {})}
                normalized_call.setdefault("stage", stage.get("stage"))
                aggregated_tool_calls.append(normalized_call)
        primary_tool_trace = next(
            (
                stage
                for stage in tool_trace_stages
                if list(stage.get("tool_calls") or [])
            ),
            tool_trace_stages[0] if tool_trace_stages else None,
        )
        experiment["track"] = resolve_track(experiment.get("track"))
        experiment["track_label"] = track_label(experiment.get("track"))
        experiment["spec"] = spec
        experiment["summary"] = summary
        experiment["research_summary"] = research_summary
        run_context = research_summary.get("run_context", {}) or {}
        experiment["run_session_id"] = str(
            run_context.get("run_session_id")
            or f"legacy::{experiment.get('spec_hash')}",
        )
        experiment["runner_label"] = str(
            run_context.get("runner_label")
            or (
                "external_agent"
                if run_context.get("benchmark_mode")
                else "siglab_harness"
            ),
        )
        experiment["run_label"] = str(
            run_context.get("run_label") or experiment["run_session_id"],
        )
        experiment["run_kind"] = (
            "benchmark" if run_context.get("benchmark_mode") else "harness"
        )
        experiment["benchmark_mode"] = bool(run_context.get("benchmark_mode"))
        experiment["benchmark_deck"] = run_context.get("benchmark_deck")
        experiment["run_iteration_number"] = (
            int(run_context["iteration_number"])
            if run_context.get("iteration_number") is not None
            else None
        )
        experiment["run_phase_label"] = str(run_context.get("phase_label") or "")
        rin = experiment["run_iteration_number"]
        experiment["run_iteration_label"] = (
            f"{experiment['run_phase_label']} {rin}"
            if rin is not None and experiment["run_phase_label"]
            else f"iter {rin}"
            if rin is not None
            else ""
        )
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
                [
                    int(stage.get("tool_count_available") or 0)
                    for stage in tool_trace_stages
                ]
                or [0],
            ),
            "tool_calls": aggregated_tool_calls,
            "final_content_preview": (primary_tool_trace or {}).get(
                "final_content_preview",
            ),
            "response_finish_reason": (primary_tool_trace or {}).get(
                "response_finish_reason",
            ),
            "stage_count": len(tool_trace_stages),
        }
        experiment["tool_call_count"] = len(aggregated_tool_calls)
        experiment["skill_value_report"] = self._svr(aggregated_tool_calls)
        lifecycle_policy = compiled_metadata.get("lifecycle_policy", {}) or {}
        experiment["roll_lifecycle"] = {
            "policy": lifecycle_policy,
            "roll_event_count": int(compiled_metadata.get("roll_event_count") or 0),
            "roll_events": list(compiled_metadata.get("roll_events") or []),
            "badges": list(compiled_metadata.get("pt_strategy_badges") or []),
            "eligible_market_count_min": compiled_metadata.get(
                "eligible_market_count_min",
            ),
            "eligible_market_count_max": compiled_metadata.get(
                "eligible_market_count_max",
            ),
            "eligible_market_count_median": compiled_metadata.get(
                "eligible_market_count_median",
            ),
            "eligible_market_count_latest": compiled_metadata.get(
                "eligible_market_count_latest",
            ),
            "markets_entered_during_backtest": list(
                compiled_metadata.get("markets_entered_during_backtest") or [],
            ),
        }
        experiment["mode_flags"] = {
            "long_enabled": params.get("long_enabled"),
            "short_enabled": params.get("short_enabled"),
            "hedge_mode": params.get(
                "hedge_mode",
                compiled_metadata.get("hedge_mode", "none"),
            ),
            "hedge_ratio": params.get(
                "hedge_ratio",
                compiled_metadata.get("hedge_ratio"),
            ),
        }
        experiment["feature_hash"] = experiment.get(
            "feature_hash",
        ) or compiled_metadata.get("feature_hash")
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
            },
        )
        experiment["deployment_readiness"] = readiness
        experiment["artifact_path"] = self._dp(raw_artifact_path)
        experiment["live_deployment"] = self._dd(experiment.get("deployment"))
        if include_artifact and artifact:
            artifact = {**artifact}
            artifact.pop("canonical_run", None)
        experiment["artifact"] = (
            artifact if include_artifact else experiment.get("artifact")
        )
        return experiment

    def _ap(self, experiments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        generation_by_track: dict[str, int] = {}
        run_position_by_session: dict[str, int] = {}
        best_by_track_metric: dict[str, float] = {}
        for global_index, experiment in enumerate(experiments, start=1):
            track_name = str(experiment.get("track") or "")
            run_session_id = str(experiment.get("run_session_id") or "")
            generation_by_track[track_name] = generation_by_track.get(track_name, 0) + 1
            run_position_by_session[run_session_id] = (
                run_position_by_session.get(run_session_id, 0) + 1
            )
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

    def _ar(self, canonical_run: dict[str, Any]) -> dict[str, Any]:
        if not canonical_run or canonical_run.get("visual_split"):
            return canonical_run
        equity_curve = canonical_run.get("equity_curve", {}) or {}
        timestamps = equity_curve.get("index") or []
        size = len(timestamps)
        if size < 2:
            canonical_run["visual_split"] = {
                "strict_holdout": False,
                "note": "This artifact predates the current in-sample, validation, and audit split metadata.",
                "ranges": [],
            }
            return canonical_run
        split_idx = max(1, min(size - 1, size * 2 // 3))
        canonical_run["visual_split"] = {
            "strict_holdout": False,
            "note": "This artifact predates the current three-way split. The chart shows a fallback in-sample versus holdout view only.",
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
        return canonical_run

    def experiments_payload(
        self,
        track: str | None = None,
        family: str | None = None,
    ) -> dict[str, Any]:
        import time

        cache_key = f"{track}:{family}"
        now = time.monotonic()
        if (
            cache_key in self._experiments_cache
            and (now - self._experiments_cache_ts) < self._EXPERIMENTS_CACHE_TTL
        ):
            return self._experiments_cache[cache_key]
        scoped_rows = self._aes(track=track)
        experiments = [
            row for row in scoped_rows if not family or row["family"] == family
        ]
        runs = self.runs_payload(track=track, family=family)["runs"]
        summary: dict[str, Any] = {
            "experiment_count": len(experiments),
            "run_count": len(runs),
            "benchmark_run_count": sum(1 for row in runs if row.get("benchmark_mode")),
            "harness_run_count": sum(
                1 for row in runs if not row.get("benchmark_mode")
            ),
            "deployd_count": sum(1 for row in experiments if row["deployd"]),
            "tool_traced_count": sum(
                1 for row in experiments if dget(row, "tool_trace", "tool_calls")
            ),
            "tracks": cast(dict[str, dict[str, Any]], {}),
        }
        for track_name in sorted({row["track"] for row in experiments}):
            rows = [row for row in experiments if row["track"] == track_name]
            if not rows:
                continue
            best = max(
                rows,
                key=lambda row: float(row["summary"].get("aggregate_score", 0.0)),
            )
            summary["tracks"][track_name] = {
                "label": track_label(track_name),
                "count": len(rows),
                "best_spec_hash": best["spec_hash"],
                "best_aggregate_score": best["summary"].get("aggregate_score", 0.0),
                "best_sharpe": best["summary"].get("median_sharpe", 0.0),
                "best_return": best["summary"].get("median_total_return", 0.0),
                "best_cagr": best["summary"].get("median_cagr", 0.0),
            }
        result = {
            "generated_at": self._ni(),
            "scope": {"track": track, "family": family},
            "summary": summary,
            "runs": runs,
            "experiments": experiments,
            "selector_metric": {
                "key": "aggregate_score",
                "label": "Aggregate Score",
                "description": "Primary selection metric used by SigLab. It is computed on the evaluator's selector windows. Validation slices are visible during search, while the final audit slice stays out of the selector.",
            },
        }
        self._experiments_cache[cache_key] = result
        self._experiments_cache_ts = now
        return result

    def runs_payload(
        self,
        track: str | None = None,
        family: str | None = None,
    ) -> dict[str, Any]:
        import time

        cache_key = f"{track}:{family}"
        now = time.monotonic()
        if (
            cache_key in self._runs_cache
            and (now - self._runs_cache_ts) < self._RUNS_CACHE_TTL
        ):
            return self._runs_cache[cache_key]
        experiments = self._aes(track=track, family=family)
        if (db_path := self._dbp()) is None:
            result = {
                "generated_at": self._ni(),
                "scope": {"track": track, "family": family},
                "summary": {},
                "runs": [],
            }
            self._runs_cache.clear()
            self._runs_cache[cache_key] = result
            self._runs_cache_ts = now
            return result
        runs = raw_runs(str(db_path), track=track, family=family)
        series_by_run: dict[str, list[dict[str, Any]]] = {}
        families = sorted(
            {str(row.get("family") or "") for row in experiments if row.get("family")},
        )
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
                    "aggregate_score": dget(experiment, "summary", "aggregate_score"),
                    "median_sharpe": dget(experiment, "summary", "median_sharpe"),
                    "median_cagr": dget(experiment, "summary", "median_cagr"),
                    "median_total_return": dget(experiment, "summary", "median_total_return"),
                    "median_calmar": dget(experiment, "summary", "median_calmar"),
                    "pre_audit_canonical_total_return": dget(experiment, "summary", "pre_audit_canonical_total_return"),
                    "validation_total_return": dget(experiment, "summary", "validation_total_return"),
                    "audit_total_return": dget(experiment, "summary", "audit_total_return"),
                },
            )
        annotated_runs = []
        for row in runs:
            annotated = {**row}
            annotated["track"] = resolve_track(annotated.get("track"))
            annotated["track_label"] = track_label(annotated.get("track"))
            run_session_id = str(annotated.get("run_session_id") or "")
            run_experiments = [
                exp
                for exp in experiments
                if str(exp.get("run_session_id") or "") == run_session_id
            ]
            annotated["series_points"] = list(series_by_run.get(run_session_id, []))
            annotated["tool_call_count"] = sum(
                int(exp.get("tool_call_count") or 0) for exp in run_experiments
            )
            primary_trace = next(
                (
                    exp.get("tool_trace") or {}
                    for exp in reversed(run_experiments)
                    if dget(exp, "tool_trace", "model")
                ),
                {},
            )
            annotated["llm_provider"] = str(primary_trace.get("provider") or "") or None
            annotated["llm_model"] = str(primary_trace.get("model") or "") or None
            annotated_runs.append(annotated)
        existing_run_ids = {
            str(row.get("run_session_id") or "") for row in annotated_runs
        }
        annotated_runs.extend(
            self._wsp(track=track, family=family, existing_run_ids=existing_run_ids),
        )
        best_run = max(
            annotated_runs,
            key=lambda row: float(row.get("best_aggregate_score") or float("-inf")),
            default=None,
        )
        result = {
            "generated_at": self._ni(),
            "scope": {"track": track, "family": family},
            "summary": {
                "run_count": len(annotated_runs),
                "benchmark_run_count": sum(
                    1 for row in annotated_runs if row.get("benchmark_mode")
                ),
                "harness_run_count": sum(
                    1 for row in annotated_runs if not row.get("benchmark_mode")
                ),
                "experiment_count": len(experiments),
                "deployd_count": sum(1 for row in experiments if row.get("deployd")),
                "families": families,
                "best_run_session_id": best_run.get("run_session_id")
                if best_run
                else None,
                "best_run_label": best_run.get("run_label") if best_run else None,
                "best_aggregate_score": best_run.get("best_aggregate_score")
                if best_run
                else None,
            },
            "runs": annotated_runs,
        }
        self._runs_cache.clear()
        self._runs_cache[cache_key] = result
        self._runs_cache_ts = now
        return result

    def _loa(self, relative_path: str) -> dict[str, Any]:
        if self.config is None:
            return {
                "status": "blocked",
                "path": relative_path,
                "error": "config not loaded",
                "payload": None,
            }
        path = (self.config.root_dir / relative_path).resolve()
        root = self.config.root_dir.resolve()
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
        age_seconds = max(
            0.0,
            (datetime.now(UTC) - mtime.astimezone(UTC)).total_seconds(),
        )
        return {
            "status": "present",
            "path": relative_path,
            "mtime": mtime.isoformat(),
            "age_seconds": round(age_seconds, 3),
            "freshness": "fresh"
            if age_seconds <= 15 * 60
            else "stale"
            if age_seconds <= 24 * 60 * 60
            else "expired",
            "payload": payload,
        }
    def _soa(self, artifacts: dict[str, dict[str, Any] | None]) -> dict[str, Any]:
        def _payload(name: str) -> dict[str, Any]:
            entry = artifacts.get(name) or {}
            if not isinstance(entry, dict):
                return {}
            return dict(entry.get("payload") or {})
        demo = _payload("demo_manifest")
        telemetry = _payload("telemetry")
        market = _payload("market_report")
        preflight = _payload("sodex_preflight")
        readiness = demo.get("readiness", {}) or {}
        decision_support = market.get("decision_support", {}) or {}
        signal_summary = market.get("signal_summary", {}) or {}
        signed_path = preflight.get("signed_path", {}) or {}
        provider_metrics = telemetry.get("provider_metrics", {}) or {}
        return {
            "buildathon": {
                "sosovalue_flow": readiness.get("sosovalue_input_to_output"),
                "sodex_public_market_data": readiness.get("sodex_public_market_data"),
                "provider_metrics_present": readiness.get("provider_metrics_present"),
                "market_report_status": demo.get("market_report_status")
                or market.get("status"),
                "red_flags": list(demo.get("red_flags") or []),
                "demo_artifacts": list(demo.get("artifacts") or []),
            },
            "market": {
                "status": market.get("status"),
                "entity": market.get("entity"),
                "headline": signal_summary.get("headline")
                or demo.get("market_report_headline"),
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
                "request_weight_budget_per_minute": preflight.get(
                    "request_weight_budget_per_minute",
                ),
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
                "returned_output_tokens": provider_metrics.get(
                    "returned_output_tokens",
                ),
                "context_pressure_events": provider_metrics.get(
                    "context_pressure_events",
                ),
                "credit_pressure_events": provider_metrics.get(
                    "credit_pressure_events",
                ),
                "model_counts": telemetry.get("model_counts"),
            },
        }

    def ops_payload(self) -> dict[str, Any]:
        now = time.perf_counter()
        if self._ops_cache is not None and (now - self._ops_cache_ts) < self._OPS_CACHE_TTL:
            return self._ops_cache
        artifacts = {
            "demo_manifest": self._loa("runs/demo_manifest_latest.json"),
            "telemetry": self._loa("runs/latest_telemetry_report.json"),
            "market_report": self._loa("runs/market_report_latest.json"),
            "sodex_preflight": self._loa("runs/sodex_preflight_latest.json"),
            "wave_status": self._loa("runs/wave_status_latest.json"),
        }
        result = {
            "generated_at": self._ni(),
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
            "summary": self._soa(artifacts),
        }
        self._ops_cache = result
        self._ops_cache_ts = now
        return result

    def experiment_detail_payload(self, spec_hash: str) -> dict[str, Any] | None:
        detail = self._ed(spec_hash)
        if detail is None:
            return None
        track = resolve_track(detail.get("track"))
        if (db_path := self._dbp()) is not None:
            track_rows = self._ap(
                [self._ae(row) for row in raw_experiments(str(db_path), track=track)],
            )
        else:
            track_rows = []
        positions = next(
            (row for row in track_rows if str(row.get("spec_hash") or "") == spec_hash),
            {},
        )
        detail = self._ae(detail, include_artifact=True)
        for key in (
            "generation",
            "track_generation",
            "global_index",
            "run_position",
            "run_iteration_number",
            "run_phase_label",
            "run_iteration_label",
        ):
            if key in positions:
                detail[key] = positions[key]
        detail.pop("research_summary", None)
        detail.pop("tool_trace", None)
        return {"generated_at": self._ni(), "experiment": detail}

    def experiment_series_payload(self, spec_hash: str) -> dict[str, Any] | None:
        detail = self._ed(spec_hash)
        if detail is None:
            return None
        artifact = detail.get("artifact", {}) or {}
        annotated = self._ae({**detail}, include_artifact=True)
        canonical_run = self._ar(artifact.get("canonical_run", {}) or {})
        return {
            "generated_at": self._ni(),
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
                "run_session_id": annotated.get("run_session_id"),
                "runner_label": annotated.get("runner_label"),
                "run_label": annotated.get("run_label"),
                "run_iteration_number": annotated.get("run_iteration_number"),
                "run_iteration_label": annotated.get("run_iteration_label"),
                "run_phase_label": annotated.get("run_phase_label"),
                "run_kind": annotated.get("run_kind"),
                "run_position": annotated.get("run_position"),
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
        if self.config is None:
            return {
                "generated_at": self._ni(),
                "deployment": None,
                "error": "config not loaded",
            }
        store = self.deployment_store or DeploymentStore(self.config.ancestry_db_path)
        manager = LiveDeploymentManager(
            self.config,
            store,
            claude=ClaudeClient(self.config),
        )
        record = await manager.deploy(
            spec_hash=spec_hash,
            wallet_label=payload.get("wallet_label"),
            config_path=str(
                payload.get("config_path") or self.config.sosovalue_config_path,
            ),
            interval_seconds=int(payload["interval_seconds"])
            if payload.get("interval_seconds") is not None
            else None,
            job_name=payload.get("job_name"),
            dry_run=bool(payload.get("dry_run", True)),
            llm_finalize=bool(payload.get("llm_finalize", False)),
            schedule=bool(payload.get("schedule", False)),
        )
        return {"generated_at": self._ni(), "deployment": self._dd(record.to_dict())}




def _tmpl(request: Request) -> Any:
    """Resolve templates, returning the dict sentinel or the Jinja2Templates object."""
    t = request.app.state.dashboard.templates
    return t if isinstance(t, dict) else t


router = APIRouter()
SIGLAB_VERSION = "0.1.0"



@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    return {"status": "ok"}

@router.get("/api/experiments")
async def api_experiments(
    request: Request,
    track: str | None = None,
    family: str | None = None,
) -> dict[str, Any]:
    return request.app.state.dashboard.experiments_payload(
        track=canonical_track_name(track) if track else None,
        family=family,
    )


@router.get("/api/runs")
async def api_runs(
    request: Request,
    track: str | None = None,
    family: str | None = None,
) -> dict[str, Any]:
    return request.app.state.dashboard.runs_payload(
        track=canonical_track_name(track) if track else None,
        family=family,
    )


@router.get("/api/experiments/{spec_hash}")
async def api_experiment_detail(request: Request, spec_hash: str) -> dict[str, Any]:
    from fastapi import HTTPException
    payload = request.app.state.dashboard.experiment_detail_payload(spec_hash)
    if payload is None:
        raise HTTPException(status_code=404, detail="Experiment not found")
    return payload


@router.get("/api/experiments/{spec_hash}/series")
async def api_experiment_series(request: Request, spec_hash: str) -> dict[str, Any]:
    from fastapi import HTTPException
    try:
        payload = request.app.state.dashboard.experiment_series_payload(spec_hash)
        if payload is None:
            raise HTTPException(status_code=404, detail="Experiment not found")
        return payload
    except Exception as exc:
        return {"error": "Internal error", "detail": str(exc)}


@router.post("/api/experiments/{spec_hash}/deploy")
async def api_experiment_deploy(request: Request, spec_hash: str) -> dict[str, Any]:
    from fastapi import HTTPException
    try:
        body = await request.json()
    except (json.JSONDecodeError, TypeError, ValueError):
        body = {}
    try:
        return await request.app.state.dashboard.deploy_experiment(
            spec_hash=spec_hash,
            payload=body,
        )
    except (ValueError, LookupError) as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/api/search")
async def api_search(
    request: Request,
    q: str = "",
    limit: int = 20,
) -> dict[str, Any]:
    """Search across runs, experiments, and actions."""
    query = q.strip().lower()
    if not query:
        return {"results": [], "query": q, "count": 0}

    dashboard = request.app.state.dashboard
    results: list[dict[str, Any]] = []

    runs_data = dashboard.runs_payload()
    for run in runs_data.get("runs", []):
        run_id = str(run.get("run_session_id", ""))
        label = str(run.get("run_label", ""))
        track = str(run.get("track", ""))
        family = str(run.get("families", ""))
        searchable = f"{run_id} {label} {track} {family}".lower()
        if query in searchable:
            results.append(
                {
                    "type": "run",
                    "title": label or run_id,
                    "subtitle": f"{track} · {family}"
                    if track and family
                    else track or family,
                    "url": f"/runs/{run_id}",
                    "icon": "📊",
                }
            )

    experiments_data = dashboard.experiments_payload()
    for exp in experiments_data.get("experiments", []):
        spec_hash = str(exp.get("spec_hash", ""))
        hypothesis = str(exp.get("hypothesis", "") or exp.get("spec", {}).get("hypothesis", ""))
        family = str(exp.get("family", ""))
        track = str(exp.get("track", ""))
        searchable = f"{spec_hash} {hypothesis} {family} {track}".lower()
        if query in searchable:
            results.append(
                {
                    "type": "experiment",
                    "title": hypothesis[:80] or spec_hash[:16],
                    "subtitle": f"{family} · {track}",
                    "url": f"/experiments/{spec_hash}",
                    "icon": "🧪",
                }
            )

    actions = [
        {
            "title": "View Ops Board",
            "url": "/ops",
            "icon": "📋",
            "keywords": "ops board operations status",
        },
        {
            "title": "View Dashboard",
            "url": "/",
            "icon": "📊",
            "keywords": "dashboard runs home",
        },
        {
            "title": "Export Runs (CSV)",
            "url": "/api/export/runs?format=csv",
            "icon": "📥",
            "keywords": "export runs csv download",
        },
        {
            "title": "Export Runs (JSON)",
            "url": "/api/export/runs?format=json",
            "icon": "📥",
            "keywords": "export runs json download",
        },
        {
            "title": "Export Experiments (CSV)",
            "url": "/api/export/experiments?format=csv",
            "icon": "📥",
            "keywords": "export experiments csv download",
        },
        {
            "title": "Export Experiments (JSON)",
            "url": "/api/export/experiments?format=json",
            "icon": "📥",
            "keywords": "export experiments json download",
        },
        {
            "title": "Refresh Data",
            "url": "#",
            "icon": "🔄",
            "keywords": "refresh reload update",
        },
        {
            "title": "Toggle Theme",
            "url": "#",
            "icon": "🌙",
            "keywords": "theme dark light mode",
        },
        {
            "title": "Help & Documentation",
            "url": "#",
            "icon": "❓",
            "keywords": "help documentation guide glossary metrics",
        },
    ]
    for action in actions:
        searchable = f"{action['title']} {action['keywords']}".lower()
        if query in searchable:
            results.append(
                {
                    "type": "action",
                    "title": action["title"],
                    "subtitle": "Action",
                    "url": action["url"],
                    "icon": action["icon"],
                }
            )

    results = results[:limit]

    return {"results": results, "query": q, "count": len(results)}


@router.get("/api/export/runs")
async def api_export_runs(
    request: Request,
    format: str = "json",
    track: str | None = None,
    family: str | None = None,
) -> Response:
    """Export runs as CSV or JSON."""
    from fastapi import Response
    dashboard = request.app.state.dashboard
    data = dashboard.runs_payload(
        track=canonical_track_name(track) if track else None,
        family=family,
    )
    runs = data.get("runs", [])

    if format == "csv":
        import csv
        import io

        output = io.StringIO()
        if runs:
            writer = csv.DictWriter(output, fieldnames=runs[0].keys())
            writer.writeheader()
            writer.writerows(runs)
        return Response(
            content=output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=siglab_runs.csv"},
        )
    return Response(
        content=json.dumps(data, default=str, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=siglab_runs.json"},
    )


@router.get("/api/export/experiments")
async def api_export_experiments(
    request: Request,
    format: str = "json",
    track: str | None = None,
    family: str | None = None,
) -> Response:
    """Export experiments as CSV or JSON."""
    from fastapi import Response
    dashboard = request.app.state.dashboard
    data = dashboard.experiments_payload(
        track=canonical_track_name(track) if track else None,
        family=family,
    )
    experiments = data.get("experiments", [])

    if format == "csv":
        import csv
        import io

        output = io.StringIO()
        if experiments:
            # Flatten nested fields for CSV
            flat = []
            for exp in experiments:
                row = {k: v for k, v in exp.items() if not isinstance(v, (dict, list))}
                summary = exp.get("summary", {})
                for k, v in summary.items():
                    row[f"summary.{k}"] = v
                flat.append(row)
            if flat:
                writer = csv.DictWriter(output, fieldnames=flat[0].keys())
                writer.writeheader()
                writer.writerows(flat)
        return Response(
            content=output.getvalue(),
            media_type="text/csv",
            headers={
                "Content-Disposition": "attachment; filename=siglab_experiments.csv"
            },
        )
    return Response(
        content=json.dumps(data, default=str, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=siglab_experiments.json"},
    )

@router.get("/api/ops")
async def api_ops(request: Request) -> dict[str, Any]:
    return request.app.state.dashboard.ops_payload()


async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    state = DashboardState()
    try:
        state.config = load_settings()
    except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        from pathlib import Path as _Path

        state.config = SiglabConfig(
            root_dir=_Path.cwd(),
            sosovalue_config_path=_Path("config.json"),
            generated_strategy_dir=_Path("generated"),
            data_lake_dir=_Path("data/cache"),
            artifact_dir=_Path("runs"),
            live_dir=_Path("live"),
            ancestry_db_path=_Path("siglab.db"),
            sosovalue_api_key_override=None,
        )
    try:
        from siglab.data.deployment_store import DeploymentStore

        state.deployment_store = DeploymentStore(state.config.ancestry_db_path)
    except Exception:
        logger.exception("Failed to create DeploymentStore, running without it")
        state.deployment_store = None
    state.static_dir = Path(__file__).resolve().parent / "static"
    state.start_time = time.time()
    _template_dir = Path(__file__).resolve().parent / "templates"
    from fastapi.templating import Jinja2Templates
    state.templates = Jinja2Templates(directory=str(_template_dir))
    _env = state.templates.env

    def _fmt_num(value: float | str | None, decimals: int = 2) -> str:
        try:
            if value is None:
                return "n/a"
            v = float(value)
            if not (v != v or v == float("inf") or v == float("-inf")):
                return f"{v:.{decimals}f}"
        except (TypeError, ValueError):
            pass
        return "n/a"

    def _fmt_pct(value: float | str | None) -> str:
        try:
            if value is None:
                return "n/a"
            v = float(value)
            if not (v != v or v == float("inf") or v == float("-inf")):
                return f"{v * 100:.2f}%"
        except (TypeError, ValueError):
            pass
        return "n/a"

    def _fmt_dt(value: object) -> str:
        if not value:
            return ""
        try:
            from datetime import datetime

            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).strftime(
                "%Y-%m-%d %H:%M:%S",
            )
        except (ValueError, TypeError):
            return str(value)

    _env.filters["format_number"] = _fmt_num
    _env.filters["format_pct"] = _fmt_pct
    _env.filters["format_dt"] = _fmt_dt
    app.state.dashboard = state
    yield


def create_app() -> FastAPI:
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.staticfiles import StaticFiles
    from starlette.middleware.base import BaseHTTPMiddleware

    class SecurityHeadersMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            response: Response = await call_next(request)
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self' 'unsafe-inline' https://unpkg.com; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; form-action 'self'; base-uri 'self'; frame-ancestors 'none'; upgrade-insecure-requests; block-all-mixed-content;"
            )
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
            response.headers["Permissions-Policy"] = (
                "camera=(), microphone=(), geolocation=()"
            )
            return response
    app = FastAPI(title="SigLab Dashboard", version="0.1.0", lifespan=lifespan)
    _cors_origins = os.getenv("CORS_ORIGINS", "http://localhost:8080").split(",")
    _cors_origins_stripped = [o.strip() for o in _cors_origins if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins_stripped,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(router)
    _static_dir = Path(__file__).resolve().parent / "static"

    @app.get("/")
    async def serve_dashboard(request: Request):
        return request.app.state.dashboard.templates.TemplateResponse(
            request,
            "dashboard.html",
            {"request": request},
        )

    @app.get("/runs/{run_id:path}")
    async def serve_run_page(run_id: str, request: Request):
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=f"/?run_id={run_id}", status_code=307)

    @app.get("/experiments/{spec_hash:path}")
    async def serve_experiment_page(spec_hash: str, request: Request):
        return request.app.state.dashboard.templates.TemplateResponse(
            request,
            "experiment.html",
            {"request": request, "spec_hash": spec_hash},
        )

    @app.exception_handler(404)
    async def not_found(request: Request, exc):
        from fastapi.responses import HTMLResponse, JSONResponse
        path = request.url.path
        if path.startswith("/api/") or "Accept" in request.headers and "json" in request.headers["Accept"]:
            return JSONResponse(content={"detail": "Not Found"}, status_code=404)
        return HTMLResponse(
            content="""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>SigLab — Not Found</title>
  <link rel="stylesheet" href="/styles.css" />
</head>
<body>
  <nav class="navbar" role="navigation">
    <a class="navbar-brand" href="/">
      <img src="/logo.svg" class="navbar-logo-svg" alt="SigLab" />
      <span class="navbar-title">SigLab</span>
    </a>
    <div class="navbar-nav">
      <a class="navbar-link" href="/">Signals</a>
    </div>
  </nav>
  <div class="page-shell" style="text-align:center;padding-top:80px;">
    <h1>Page Not Found</h1>
    <p style="color:var(--muted);margin:16px 0 32px;">The page you requested does not exist.</p>
    <a class="button-link" href="/">Back to Dashboard</a>
  </div>
</body>
</html>""",
            status_code=404,
        )




    if _static_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(_static_dir)), name="static")
    return app




app = create_app()

