from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import sqlite3
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, AsyncIterator, Protocol, cast

from fastapi import (
    APIRouter,
    FastAPI,
    HTTPException,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware

from siglab.config import SiglabConfig, load_settings
from siglab.dashboard.risk_utils import compute_risk_metrics, empty_risk_response
from siglab.data.deployment_store import DeploymentStore
from siglab.io_utils import load_json_path
from siglab.live import LiveDeploymentManager, deployment_readiness
from siglab.llm import ClaudeClient
from siglab.llm_metadata import (
    default_llm_model_display,
    infer_llm_provider,
    resolve_llm_provider,
)
from siglab.path_utils import display_path, resolve_path_from_root
from siglab.track_registry import canonical_track_name, resolve_track, track_label
from siglab.utils import _now_iso

logger = logging.getLogger(__name__)


class _LS(Protocol):
    def dashboard_rows(self) -> list[dict[str, Any]]: ...


def _ts(request: Request) -> Jinja2Templates | dict[str, str]:
    t = request.app.state.dashboard.templates
    return t if t is not None else {"error": "templates not configured"}


def _st(request: Request) -> Any:
    return request.app.state.dashboard


def _ifn(value: object) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def _cs(n: str, uc: int) -> str:
    hp = {
        "probe_",
        "compare_intended_vs_frozen_spec",
        "search_features",
        "suggest_feature_set",
        "inspect_feature",
    }
    mp = {"search_workspace", "search_workspace_text", "open_file"}
    return (
        "HIGH_VALUE"
        if any(n.startswith(p) or n == p for p in hp)
        else "MEDIUM_VALUE"
        if any(n.startswith(p) or n == p for p in mp)
        else "LOW_VALUE"
        if n == "think"
        else "NOISY"
        if uc > 8
        else "MEDIUM_VALUE"
    )


_classify_skill = _cs


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
            "etf_base_url": config.sosovalue_base_url,
            "news_base_url": config.sosovalue_base_url,
            "timeout_s": config.sosovalue_timeout_s,
            "retries": config.sosovalue_retries,
            "api_key_configured": config.sosovalue_api_key_override is not None,
        },
        "claude": {
            "model": config.claude_model,
            "base_url": config.claude_base_url,
            "max_tokens": config.claude_max_tokens,
            "temperature": config.claude_temperature,
            "timeout_s": config.claude_timeout_s,
            "api_key_configured": config.claude_api_key is not None,
        },
    }


_config_to_dict = _ctd


def _dr(
    db_path: str | Path, track: str | None = None, family: str | None = None
) -> list[dict[str, Any]]:
    path = Path(db_path)
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        q = "SELECT created_at, track, family, spec_hash, parent_hash, aggregate_score, passed, deployd, spec_json, research_summary, summary_json, artifact_path FROM experiments"
        params: list[Any] = []
        conditions: list[str] = []
        if track:
            conditions.append("track = ?")
            params.append(track)
        if family:
            conditions.append("family = ?")
            params.append(family)
        if conditions:
            q += " WHERE " + " AND ".join(conditions)
        q += " ORDER BY created_at ASC"
        cursor = conn.execute(q, params)
        for row in cursor.fetchall():
            spec = json.loads(row["spec_json"]) if row["spec_json"] else {}
            spec["track"] = resolve_track(spec.get("track"))
            rows.append(
                {
                    "created_at": row["created_at"],
                    "track": resolve_track(row["track"]),
                    "family": row["family"],
                    "spec_hash": row["spec_hash"],
                    "parent_hash": row["parent_hash"],
                    "aggregate_score": row["aggregate_score"],
                    "passed": bool(row["passed"]),
                    "deployd": bool(row["deployd"]),
                    "spec": spec,
                    "research_summary": json.loads(row["research_summary"])
                    if row["research_summary"]
                    else {},
                    "summary": json.loads(row["summary_json"])
                    if row["summary_json"]
                    else {},
                    "artifact_path": row["artifact_path"],
                }
            )
        conn.close()
    except (sqlite3.Error, OSError):
        pass
    return rows


def _rs(
    db_path: str | Path, track: str | None = None, family: str | None = None
) -> list[dict[str, Any]]:
    rows = _dr(db_path, track=track, family=family)
    runs_map: dict[str, dict[str, Any]] = {}
    for row in rows:
        rs = row.get("research_summary") or {}
        rc = rs.get("run_context") or {}
        run_session_id = str(
            rc.get("run_session_id") or f"legacy::{row.get('spec_hash')}"
        )
        if run_session_id not in runs_map:
            runs_map[run_session_id] = {
                "run_session_id": run_session_id,
                "run_label": str(rc.get("run_label") or run_session_id),
                "track": row["track"],
                "runner_label": str(rc.get("runner_label") or "siglab_harness"),
                "run_kind": "benchmark" if rc.get("benchmark_mode") else "harness",
                "benchmark_mode": bool(rc.get("benchmark_mode")),
                "benchmark_deck": rc.get("benchmark_deck"),
                "memory_scope": str(rc.get("memory_scope") or "track_shared"),
                "phase_labels": [],
                "families": set(),
                "experiment_count": 0,
                "llm_experiment_count": 0,
                "deterministic_experiment_count": 0,
                "passed_count": 0,
                "deployd_count": 0,
                "first_created_at": row["created_at"],
                "last_created_at": row["created_at"],
                "best_spec_hash": None,
                "best_family": None,
                "best_aggregate_score": None,
                "best_validation_total_return": None,
                "best_pre_audit_canonical_total_return": None,
                "status": "pass" if row["passed"] else "fail",
            }
        entry = runs_map[run_session_id]
        entry["experiment_count"] += 1
        is_llm = bool(row.get("research_summary", {}).get("llm_tool_trace"))
        is_det = bool(rc.get("deterministic"))
        if is_llm:
            entry["llm_experiment_count"] += 1
        if is_det:
            entry["deterministic_experiment_count"] += 1
        if row["passed"]:
            entry["passed_count"] += 1
        if row["deployd"]:
            entry["deployd_count"] += 1
        phase = str(rc.get("phase_label") or "")
        if phase and phase not in entry["phase_labels"]:
            entry["phase_labels"].append(phase)
        fv = row.get("family")
        if fv:
            entry["families"].add(fv)
        score = float(row.get("aggregate_score") or 0.0)
        if (
            entry["best_aggregate_score"] is None
            or score > entry["best_aggregate_score"]
        ):
            entry["best_aggregate_score"] = score
            entry["best_spec_hash"] = row["spec_hash"]
            entry["best_family"] = fv
            entry["best_validation_total_return"] = row.get("summary", {}).get(
                "validation_total_return"
            )
            entry["best_pre_audit_canonical_total_return"] = row.get("summary", {}).get(
                "pre_audit_canonical_total_return"
            )
        if row["created_at"] < entry["first_created_at"]:
            entry["first_created_at"] = row["created_at"]
        if row["created_at"] > entry["last_created_at"]:
            entry["last_created_at"] = row["created_at"]
        if not row["passed"]:
            entry["status"] = "fail"
    for entry in runs_map.values():
        entry["phase_labels"] = sorted(set(entry["phase_labels"]))
        entry["families"] = sorted(entry["families"])
    return list(runs_map.values())


@dataclass
class DashboardState:
    config: SiglabConfig | None = None
    deployment_store: DeploymentStore | None = None
    lineage: _LS | None = None
    static_dir: Path | None = None
    templates: Jinja2Templates | None = None
    ws_manager: Any = None
    start_time: float = 0.0
    _json_cache: dict[str, Any] = field(default_factory=dict)

    def _lp(self) -> str:
        return "unknown" if self.config is None else resolve_llm_provider(self.config)

    def _lm(self) -> str:
        return (
            "unknown"
            if self.config is None
            else default_llm_model_display(self.config, provider=self._lp())
        )

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

    def _lj(self, value: str | Path | None) -> dict[str, Any] | None:
        if not value:
            return None
        path = Path(value).expanduser()
        if not path.is_absolute() and self.config is not None:
            path = (self.config.root_dir / path).resolve()
        cached = self._json_cache.get(str(path))
        if cached is not None:
            return cast(dict[str, Any] | None, cached)
        payload: dict[str, Any] | None = load_json_path(path)
        self._json_cache[str(path)] = payload
        return payload

    def _dbp(self) -> Path | None:
        return None if self.config is None else self.config.ancestry_db_path

    def _ns(
        self,
        *,
        stage_name: str,
        payload: dict[str, Any] | None,
        trace_path: str | Path | None,
    ) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            return None
        trace = {**payload.get("claude_trace", {})}
        if not trace:
            for key in ("attempts", "planner_attempts", "repair_attempts"):
                attempts = list(payload.get(key) or [])
                for attempt in reversed(attempts):
                    attempt_trace = {**(attempt or {}).get("claude_trace", {})}
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

    def _rss(self, research_summary: dict[str, Any]) -> list[dict[str, Any]]:
        stages: list[dict[str, Any]] = []
        workspace = {**research_summary.get("workspace", {})}
        for stage_name, path_key in (
            ("planner", "planner_trace_path"),
            ("writer", "writer_trace_path"),
            ("reflector", "reflector_trace_path"),
        ):
            trace_path = workspace.get(path_key)
            stage = self._ns(
                stage_name=stage_name,
                payload=self._lj(trace_path),
                trace_path=trace_path,
            )
            if stage is not None:
                stages.append(stage)
        if stages:
            return stages
        tool_trace = {**research_summary.get("llm_tool_trace", {})}
        trace_core = {**tool_trace.get("trace", {})}
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
                }
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
                call.get("latency_ms") or call.get("duration_ms") or 0.0
            )
            row["token_context_cost"] += int(
                call.get("context_tokens")
                or call.get("input_tokens")
                or call.get("token_count")
                or 0
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
                float(normalized["latency_cost_ms"]), 3
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
            runs_root.glob("*/workspaces/*/current/SESSION_STATE.json")
        ):
            try:
                payload = json.loads(state_path.read_text())
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                continue
            run_session_id = str(
                payload.get("run_session_id") or state_path.parents[1].name
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
                }
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
                }
            )
        placeholders.sort(
            key=lambda row: str(row.get("last_created_at") or ""), reverse=True
        )
        return placeholders

    def _ed(self, spec_hash: str) -> dict[str, Any] | None:
        if self.deployment_store is not None:
            return self.deployment_store.experiment_detail(spec_hash)
        if (db_path := self._dbp()) is None:
            return None
        return next(
            (row for row in _dr(str(db_path)) if row.get("spec_hash") == spec_hash),
            None,
        )

    def _aes(
        self, *, track: str | None = None, family: str | None = None
    ) -> list[dict[str, Any]]:
        if (db_path := self._dbp()) is None:
            return []
        return self._ap(
            [self._ae(row) for row in _dr(str(db_path), track=track, family=family)]
        )

    def _ni(self) -> str:
        return _now_iso()

    def _ae(
        self, experiment: dict[str, Any], *, include_artifact: bool = False
    ) -> dict[str, Any]:
        spec = {**experiment.get("spec", {})}
        summary = {**experiment.get("summary", {})}
        research_summary = {**experiment.get("research_summary", {})}
        raw_artifact_path = experiment.get("artifact_path")
        artifact = {**experiment.get("artifact", {})} if include_artifact else {}
        if not artifact and raw_artifact_path and (self.config is not None):
            artifact_path = resolve_path_from_root(
                raw_artifact_path, root_dir=self.config.root_dir
            )
            if artifact_path.exists():
                try:
                    artifact = dict(json.loads(artifact_path.read_text()))
                except (json.JSONDecodeError, OSError, UnicodeDecodeError):
                    artifact = {}
        compiled_metadata = (
            artifact.get("compiled_metadata") or artifact.get("compiledMetadata") or {}
        )
        if "median_cagr" not in summary or not (
            isinstance(summary.get("median_cagr"), (int, float))
            and math.isfinite(float(summary.get("median_cagr")))
        ):
            windows = artifact.get("windows") or []
            cagr_values = [
                float(window.get("stats", {}).get("cagr"))
                for window in windows
                if isinstance(window.get("stats", {}).get("cagr"), (int, float))
                and math.isfinite(float(window.get("stats", {}).get("cagr")))
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
        bias_controls = {**compiled_metadata.get("bias_controls", {})}
        params = {**spec.get("params", {})}
        tool_trace = {**research_summary.get("llm_tool_trace", {})}
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
        run_context = {**research_summary.get("run_context", {})}
        experiment["run_session_id"] = str(
            run_context.get("run_session_id")
            or f"legacy::{experiment.get('spec_hash')}"
        )
        experiment["runner_label"] = str(
            run_context.get("runner_label")
            or (
                "external_agent"
                if run_context.get("benchmark_mode")
                else "siglab_harness"
            )
        )
        experiment["run_label"] = str(
            run_context.get("run_label") or experiment["run_session_id"]
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
                (int(stage.get("tool_rounds_used") or 0) for stage in tool_trace_stages)
            ),
            "tool_count_available": max(
                [
                    int(stage.get("tool_count_available") or 0)
                    for stage in tool_trace_stages
                ]
                or [0]
            ),
            "tool_calls": aggregated_tool_calls,
            "final_content_preview": (primary_tool_trace or {}).get(
                "final_content_preview"
            ),
            "response_finish_reason": (primary_tool_trace or {}).get(
                "response_finish_reason"
            ),
            "stage_count": len(tool_trace_stages),
        }
        experiment["tool_call_count"] = len(aggregated_tool_calls)
        experiment["skill_value_report"] = self._svr(aggregated_tool_calls)
        lifecycle_policy = {**compiled_metadata.get("lifecycle_policy", {})}
        experiment["roll_lifecycle"] = {
            "policy": lifecycle_policy,
            "roll_event_count": int(compiled_metadata.get("roll_event_count") or 0),
            "roll_events": list(compiled_metadata.get("roll_events") or []),
            "badges": list(compiled_metadata.get("pt_strategy_badges") or []),
            "eligible_market_count_min": compiled_metadata.get(
                "eligible_market_count_min"
            ),
            "eligible_market_count_max": compiled_metadata.get(
                "eligible_market_count_max"
            ),
            "eligible_market_count_median": compiled_metadata.get(
                "eligible_market_count_median"
            ),
            "eligible_market_count_latest": compiled_metadata.get(
                "eligible_market_count_latest"
            ),
            "markets_entered_during_backtest": list(
                compiled_metadata.get("markets_entered_during_backtest") or []
            ),
        }
        experiment["mode_flags"] = {
            "long_enabled": params.get("long_enabled"),
            "short_enabled": params.get("short_enabled"),
            "hedge_mode": params.get(
                "hedge_mode", compiled_metadata.get("hedge_mode", "none")
            ),
            "hedge_ratio": params.get(
                "hedge_ratio", compiled_metadata.get("hedge_ratio")
            ),
        }
        experiment["feature_hash"] = experiment.get(
            "feature_hash"
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
            }
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
                score, best_by_track_metric.get(track_name, float("-inf"))
            )
            experiment["best_so_far_aggregate_score"] = best_by_track_metric[track_name]
        return experiments

    def _ar(self, canonical_run: dict[str, Any]) -> dict[str, Any]:
        if not canonical_run or canonical_run.get("visual_split"):
            return canonical_run
        equity_curve = {**canonical_run.get("equity_curve", {})}
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
        canonical_run.setdefault("evaluation_windows", [])
        return canonical_run

    def experiments_payload(
        self, track: str | None = None, family: str | None = None
    ) -> dict[str, Any]:
        scoped_rows = self._aes(track=track)
        experiments = [
            row for row in scoped_rows if not family or row["family"] == family
        ]
        runs = self.runs_payload(track=track, family=family)["runs"]
        summary: dict[str, Any] = {
            "experiment_count": len(experiments),
            "run_count": len(runs),
            "benchmark_run_count": sum(
                (1 for row in runs if row.get("benchmark_mode"))
            ),
            "harness_run_count": sum(
                (1 for row in runs if not row.get("benchmark_mode"))
            ),
            "deployd_count": sum((1 for row in experiments if row["deployd"])),
            "tool_traced_count": sum(
                (
                    1
                    for row in experiments
                    if row.get("tool_trace", {}).get("tool_calls")
                )
            ),
            "tracks": cast(dict[str, dict[str, Any]], {}),
            "families": sorted({row["family"] for row in scoped_rows}),
        }
        for track_name in sorted({row["track"] for row in experiments}):
            rows = [row for row in experiments if row["track"] == track_name]
            if not rows:
                continue
            best = max(
                rows, key=lambda row: float(row["summary"].get("aggregate_score", 0.0))
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
        return {
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

    def runs_payload(
        self, track: str | None = None, family: str | None = None
    ) -> dict[str, Any]:
        experiments = self._aes(track=track, family=family)
        if (db_path := self._dbp()) is None:
            return {
                "generated_at": self._ni(),
                "scope": {"track": track, "family": family},
                "summary": {},
                "runs": [],
            }
        runs = _rs(str(db_path), track=track, family=family)
        series_by_run: dict[str, list[dict[str, Any]]] = {}
        families = sorted(
            {str(row.get("family") or "") for row in experiments if row.get("family")}
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
                    "aggregate_score": experiment.get("summary", {}).get(
                        "aggregate_score"
                    ),
                    "median_sharpe": experiment.get("summary", {}).get("median_sharpe"),
                    "median_cagr": experiment.get("summary", {}).get("median_cagr"),
                    "median_total_return": experiment.get("summary", {}).get(
                        "median_total_return"
                    ),
                    "median_calmar": experiment.get("summary", {}).get("median_calmar"),
                    "pre_audit_canonical_total_return": experiment.get(
                        "summary", {}
                    ).get("pre_audit_canonical_total_return"),
                    "validation_total_return": experiment.get("summary", {}).get(
                        "validation_total_return"
                    ),
                    "audit_total_return": experiment.get("summary", {}).get(
                        "audit_total_return"
                    ),
                }
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
                (int(exp.get("tool_call_count") or 0) for exp in run_experiments)
            )
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
        existing_run_ids = {
            str(row.get("run_session_id") or "") for row in annotated_runs
        }
        annotated_runs.extend(
            self._wsp(track=track, family=family, existing_run_ids=existing_run_ids)
        )
        best_run = max(
            annotated_runs,
            key=lambda row: float(row.get("best_aggregate_score") or float("-inf")),
            default=None,
        )
        return {
            "generated_at": self._ni(),
            "scope": {"track": track, "family": family},
            "summary": {
                "run_count": len(annotated_runs),
                "benchmark_run_count": sum(
                    (1 for row in annotated_runs if row.get("benchmark_mode"))
                ),
                "harness_run_count": sum(
                    (1 for row in annotated_runs if not row.get("benchmark_mode"))
                ),
                "experiment_count": len(experiments),
                "deployd_count": sum((1 for row in experiments if row.get("deployd"))),
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
            0.0, (datetime.now(UTC) - mtime.astimezone(UTC)).total_seconds()
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

    _load_artifact = _loa

    def _soa(self, artifacts: dict[str, dict[str, Any]]) -> dict[str, Any]:
        demo = {**artifacts["demo_manifest"].get("payload", {})}
        telemetry = {**artifacts["telemetry"].get("payload", {})}
        market = {**artifacts["market_report"].get("payload", {})}
        preflight = {**artifacts["sodex_preflight"].get("payload", {})}
        readiness = {**demo.get("readiness", {})}
        decision_support = {**market.get("decision_support", {})}
        signal_summary = {**market.get("signal_summary", {})}
        signed_path = {**preflight.get("signed_path", {})}
        provider_metrics = {**telemetry.get("provider_metrics", {})}
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
                    "request_weight_budget_per_minute"
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
                    "returned_output_tokens"
                ),
                "context_pressure_events": provider_metrics.get(
                    "context_pressure_events"
                ),
                "credit_pressure_events": provider_metrics.get(
                    "credit_pressure_events"
                ),
                "model_counts": telemetry.get("model_counts"),
            },
        }

    def ops_payload(self) -> dict[str, Any]:
        artifacts = {
            "demo_manifest": self._loa("runs/demo_manifest_latest.json"),
            "telemetry": self._loa("runs/latest_telemetry_report.json"),
            "market_report": self._loa("runs/market_report_latest.json"),
            "sodex_preflight": self._loa("runs/sodex_preflight_latest.json"),
            "wave_status": self._loa("runs/wave_status_latest.json"),
        }
        return {
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

    def experiment_detail_payload(self, spec_hash: str) -> dict[str, Any] | None:
        detail = self._ed(spec_hash)
        if detail is None:
            return None
        track = resolve_track(detail.get("track"))
        if (db_path := self._dbp()) is not None:
            track_rows = self._ap(
                [self._ae(row) for row in _dr(str(db_path), track=track)]
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
        return {"generated_at": self._ni(), "experiment": detail}

    def experiment_series_payload(self, spec_hash: str) -> dict[str, Any] | None:
        detail = self._ed(spec_hash)
        if detail is None:
            return None
        artifact = {**detail.get("artifact", {})}
        annotated = self._ae({**detail}, include_artifact=True)
        canonical_run = self._ar({**artifact.get("canonical_run", {})})
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
        self, *, spec_hash: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        if self.config is None:
            return {
                "generated_at": self._ni(),
                "deployment": None,
                "error": "config not loaded",
            }
        store = self.deployment_store or DeploymentStore(self.config.ancestry_db_path)
        manager = LiveDeploymentManager(
            self.config, store, claude=ClaudeClient(self.config)
        )
        record = await manager.deploy(
            spec_hash=spec_hash,
            wallet_label=payload.get("wallet_label"),
            config_path=str(
                payload.get("config_path") or self.config.sosovalue_config_path
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


def _load_artifact(root_dir: Path, relative_path: str) -> dict[str, Any]:
    path = (root_dir / relative_path).resolve()
    root = root_dir.resolve()
    if root not in path.parents and path != root:
        return {
            "status": "blocked",
            "path": relative_path,
            "error": "artifact path escapes repo root",
        }
    if not path.exists():
        return {"status": "missing", "path": relative_path, "error": "artifact missing"}
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return {"status": "malformed", "path": relative_path, "error": str(exc)}
    if not isinstance(payload, dict):
        return {
            "status": "malformed",
            "path": relative_path,
            "error": "artifact root must be a JSON object",
        }
    mtime = datetime.fromtimestamp(path.stat().st_mtime).astimezone()
    age = max(0.0, (datetime.now(UTC) - mtime.astimezone(UTC)).total_seconds())
    return {
        "status": "present",
        "path": relative_path,
        "mtime": mtime.isoformat(),
        "age_seconds": round(age, 3),
        "freshness": "fresh"
        if age <= 15 * 60
        else "stale"
        if age <= 24 * 60 * 60
        else "expired",
        "payload": payload,
    }


def _soa_standalone(artifacts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "buildathon_demo": {
            "demo_manifest": artifacts.get("demo_manifest", {}).get("status"),
            "telemetry_report": artifacts.get("telemetry", {}).get("status"),
            "market_report": artifacts.get("market_report", {}).get("status"),
            "sodex_preflight": artifacts.get("sodex_preflight", {}).get("status"),
            "wave_status": artifacts.get("wave_status", {}).get("status"),
        }
    }


_summarize_ops_artifacts = _soa_standalone
router = APIRouter()
SIGLAB_VERSION = "0.1.0"
_DEFAULT_PORT = int(os.environ.get("PORT", "8080"))


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    return {
        "status": "ok",
        "version": SIGLAB_VERSION,
        "uptime_seconds": round(
            time.time() - request.app.state.dashboard.start_time, 3
        ),
    }


@router.get("/config")
async def get_config(request: Request) -> dict[str, Any]:
    if (c := request.app.state.dashboard.config) is None:
        raise HTTPException(status_code=503, detail="Config not loaded")
    return {
        "system": {
            "root_dir": str(c.root_dir),
            "data_lake_dir": str(c.data_lake_dir),
            "artifact_dir": str(c.artifact_dir),
            "live_dir": str(c.live_dir),
            "ancestry_db_path": str(c.ancestry_db_path),
            "generated_strategy_dir": str(c.generated_strategy_dir),
            "population_size": c.population_size,
            "llm_provider": c.llm_provider,
            "memory_scope": c.memory_scope,
            "tracks": list(c.tracks),
        },
        "sosovalue": {
            "config_path": str(c.sosovalue_config_path),
            "openapi_base_url": c.sosovalue_base_url,
            "etf_base_url": c.sosovalue_base_url,
            "news_base_url": c.sosovalue_base_url,
            "timeout_s": c.sosovalue_timeout_s,
            "retries": c.sosovalue_retries,
            "api_key_configured": c.sosovalue_api_key_override is not None,
        },
        "claude": {
            "model": c.claude_model,
            "base_url": c.claude_base_url,
            "max_tokens": c.claude_max_tokens,
            "temperature": c.claude_temperature,
            "timeout_s": c.claude_timeout_s,
            "api_key_configured": c.claude_api_key is not None,
        },
    }


@router.get("/api/experiments")
async def api_experiments(
    request: Request, track: str | None = None, family: str | None = None
) -> dict[str, Any]:
    return request.app.state.dashboard.experiments_payload(
        track=canonical_track_name(track) if track else None, family=family
    )


@router.get("/api/runs")
async def api_runs(
    request: Request, track: str | None = None, family: str | None = None
) -> dict[str, Any]:
    return request.app.state.dashboard.runs_payload(
        track=canonical_track_name(track) if track else None, family=family
    )


@router.get("/api/experiments/{spec_hash}")
async def api_experiment_detail(request: Request, spec_hash: str) -> dict[str, Any]:
    payload = request.app.state.dashboard.experiment_detail_payload(spec_hash)
    if payload is None:
        raise HTTPException(status_code=404, detail="Experiment not found")
    return payload


@router.get("/api/experiments/{spec_hash}/series")
async def api_experiment_series(request: Request, spec_hash: str) -> dict[str, Any]:
    payload = request.app.state.dashboard.experiment_series_payload(spec_hash)
    if payload is None:
        raise HTTPException(status_code=404, detail="Experiment not found")
    return payload


@router.post("/api/experiments/{spec_hash}/deploy")
async def api_experiment_deploy(request: Request, spec_hash: str) -> dict[str, Any]:
    try:
        body = await request.json()
    except (json.JSONDecodeError, TypeError, ValueError):
        body = {}
    return await request.app.state.dashboard.deploy_experiment(
        spec_hash=spec_hash, payload=body
    )


@router.get("/api/search")
async def api_search(
    request: Request, q: str = "", limit: int = 20
) -> dict[str, Any]:
    """Search across runs, experiments, and actions."""
    query = q.strip().lower()
    if not query:
        return {"results": [], "query": q}

    dashboard = request.app.state.dashboard
    results: list[dict[str, Any]] = []

    # Search runs
    runs_data = dashboard.runs_payload()
    for run in runs_data.get("runs", []):
        run_id = str(run.get("run_id", ""))
        label = str(run.get("label", ""))
        track = str(run.get("track", ""))
        family = str(run.get("family", ""))
        searchable = f"{run_id} {label} {track} {family}".lower()
        if query in searchable:
            results.append({
                "type": "run",
                "title": label or run_id,
                "subtitle": f"{track} · {family}" if track and family else track or family,
                "url": f"/runs/{run_id}",
                "icon": "📊",
            })

    # Search experiments
    experiments_data = dashboard.experiments_payload()
    for exp in experiments_data.get("experiments", []):
        spec_hash = str(exp.get("spec_hash", ""))
        hypothesis = str(exp.get("hypothesis", ""))
        family = str(exp.get("family", ""))
        track = str(exp.get("track", ""))
        searchable = f"{spec_hash} {hypothesis} {family} {track}".lower()
        if query in searchable:
            results.append({
                "type": "experiment",
                "title": hypothesis[:80] or spec_hash[:16],
                "subtitle": f"{family} · {track}",
                "url": f"/experiments/{spec_hash}",
                "icon": "🧪",
            })

    # Search actions (static list)
    actions = [
        {"title": "View Ops Board", "url": "/ops", "icon": "📋", "keywords": "ops board operations status"},
        {"title": "View Dashboard", "url": "/", "icon": "📊", "keywords": "dashboard runs home"},
        {"title": "Export Runs (CSV)", "url": "/api/export/runs?format=csv", "icon": "📥", "keywords": "export runs csv download"},
        {"title": "Export Runs (JSON)", "url": "/api/export/runs?format=json", "icon": "📥", "keywords": "export runs json download"},
        {"title": "Export Experiments (CSV)", "url": "/api/export/experiments?format=csv", "icon": "📥", "keywords": "export experiments csv download"},
        {"title": "Export Experiments (JSON)", "url": "/api/export/experiments?format=json", "icon": "📥", "keywords": "export experiments json download"},
        {"title": "Refresh Data", "url": "#", "icon": "🔄", "keywords": "refresh reload update"},
        {"title": "Toggle Theme", "url": "#", "icon": "🌙", "keywords": "theme dark light mode"},
        {"title": "Help & Documentation", "url": "#", "icon": "❓", "keywords": "help documentation guide glossary metrics"},
    ]
    for action in actions:
        searchable = f"{action['title']} {action['keywords']}".lower()
        if query in searchable:
            results.append({
                "type": "action",
                "title": action["title"],
                "subtitle": "Action",
                "url": action["url"],
                "icon": action["icon"],
            })

    # Limit results
    results = results[:limit]

    return {"results": results, "query": q, "count": len(results)}

@router.get("/api/export/runs")
async def api_export_runs(
    request: Request, format: str = "json", track: str | None = None, family: str | None = None
) -> Response:
    """Export runs as CSV or JSON."""
    dashboard = request.app.state.dashboard
    data = dashboard.runs_payload(
        track=canonical_track_name(track) if track else None, family=family
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
    request: Request, format: str = "json", track: str | None = None, family: str | None = None
) -> Response:
    """Export experiments as CSV or JSON."""
    dashboard = request.app.state.dashboard
    data = dashboard.experiments_payload(
        track=canonical_track_name(track) if track else None, family=family
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
            headers={"Content-Disposition": "attachment; filename=siglab_experiments.csv"},
        )
    return Response(
        content=json.dumps(data, default=str, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=siglab_experiments.json"},
    )

@router.get("/ops-board")
async def ops_board(request: Request) -> dict[str, Any]:
    if (config := request.app.state.dashboard.config) is None:
        raise HTTPException(status_code=503, detail="Config not loaded")
    artifacts = {
        "demo_manifest": request.app.state.dashboard._loa(
            "runs/demo_manifest_latest.json"
        ),
        "telemetry": request.app.state.dashboard._loa(
            "runs/latest_telemetry_report.json"
        ),
        "market_report": request.app.state.dashboard._loa(
            "runs/market_report_latest.json"
        ),
        "sodex_preflight": request.app.state.dashboard._loa(
            "runs/sodex_preflight_latest.json"
        ),
        "wave_status": request.app.state.dashboard._loa("runs/wave_status_latest.json"),
    }
    return {
        "generated_at": _now_iso(),
        "artifact_status": {
            name: {
                "status": a.get("status"),
                "path": a.get("path"),
                "mtime": a.get("mtime"),
                "age_seconds": a.get("age_seconds"),
                "freshness": a.get("freshness"),
                "error": a.get("error"),
            }
            for name, a in artifacts.items()
        },
        "summary": {
            "buildathon_demo": {
                k: artifacts.get(k, {}).get("status")
                for k in (
                    "demo_manifest",
                    "telemetry",
                    "market_report",
                    "sodex_preflight",
                    "wave_status",
                )
            }
        },
        "service_health": {
            "dashboard": {"status": "running", "port": _DEFAULT_PORT},
            "siglab_db": {
                "status": "ok"
                if Path(str(config.ancestry_db_path)).exists()
                else "missing"
            },
            "sodex_api": {
                "status": "external",
                "note": "SoDEX public REST API (no auth)",
            },
            "sosovalue_api": {
                "status": "external",
                "note": "SoSoValue OpenAPI (requires API key)",
            },
        },
    }


@router.get("/api/ops")
async def api_ops(request: Request) -> dict[str, Any]:
    return request.app.state.dashboard.ops_payload()


def _beg(state: DashboardState) -> dict[str, Any] | None:
    if (config := state.config) is None:
        return None
    evidence_dir = config.artifact_dir / "evidence"
    if not evidence_dir.exists():
        return None
    summaries = sorted(
        evidence_dir.glob("*.summary.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not summaries:
        return None
    try:
        summary = json.loads(summaries[0].read_text())
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.warning("Failed to load evidence summary: %s", exc)
        return None
    source_counts = summary.get("source_counts") or {}
    entity_counts = summary.get("entity_counts") or {}
    links = summary.get("top_links") or summary.get("links") or []
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    seen_edges: set[tuple[str, str, str]] = set()

    def _n(node_id: str, label: str, kind: str, count: int) -> dict[str, Any]:
        return {
            "id": node_id,
            "label": label,
            "kind": kind,
            "count": count,
            "spec_hash": None,
            "family": None,
            "score": None,
        }

    for source, count in source_counts.items():
        nodes[f"source:{source}"] = _n(
            f"source:{source}", str(source), "source", int(count)
        )
    for entity, count in entity_counts.items():
        nodes[f"entity:{entity}"] = _n(
            f"entity:{entity}", str(entity), "entity", int(count)
        )
    for link in links:
        if not isinstance(link, dict):
            continue
        relation = str(link.get("relation") or "linked")
        source_name = str(link.get("source") or "cross-module")
        entities_raw = link.get("entities")
        if isinstance(entities_raw, list) and entities_raw:
            entities = [str(item) for item in entities_raw if item]
        else:
            entities = []
            for key in ("entity", "feed_entity"):
                val = link.get(key)
                if val:
                    entities.append(str(val))
        for entity in entities:
            entity_id = f"entity:{entity}"
            source_id = f"source:{source_name}"
            nodes.setdefault(entity_id, _n(entity_id, entity, "entity", 0))
            nodes.setdefault(source_id, _n(source_id, source_name, "source", 0))
            edge_key = (source_id, entity_id, relation)
            if edge_key in seen_edges:
                continue
            seen_edges.add(edge_key)
            edges.append(
                {
                    "source": source_id,
                    "target": entity_id,
                    "label": relation,
                    "confidence": link.get("confidence"),
                    "warning": link.get("warning"),
                    "day_gap": link.get("day_gap"),
                }
            )
    return {"nodes": list(nodes.values()), "edges": edges}


@router.get("/evidence-graph")
async def evidence_graph(request: Request) -> dict[str, Any]:
    logger.debug("Evidence graph requested")
    graph = _beg(request.app.state.dashboard)
    return (
        graph
        if graph is not None
        else {"nodes": [], "edges": [], "note": "No evidence data available"}
    )


def _bsr(state: DashboardState) -> list[dict[str, Any]]:
    if state.lineage is None:
        return []
    try:
        rows = state.lineage.dashboard_rows()
    except Exception as exc:
        logger.warning("Failed to load skill report rows: %s", exc)
        return []
    skill_usage: dict[str, dict[str, Any]] = {}
    for row in rows:
        research_summary = row.get("research_summary") or {}
        if isinstance(research_summary, str):
            try:
                research_summary = json.loads(research_summary)
            except (json.JSONDecodeError, TypeError, ValueError):
                research_summary = {}
        if not isinstance(research_summary, dict):
            continue
        tool_trace = research_summary.get("llm_tool_trace") or {}
        trace = tool_trace.get("trace") or {}
        tool_calls = list(trace.get("tool_calls") or [])
        workspace = research_summary.get("workspace") or {}
        for stage_key in (
            "planner_trace_path",
            "writer_trace_path",
            "reflector_trace_path",
        ):
            trace_path = workspace.get(stage_key)
            if trace_path:
                try:
                    stage_payload = json.loads(Path(trace_path).read_text())
                except (OSError, json.JSONDecodeError, TypeError, ValueError):
                    continue
                stage_trace = stage_payload.get("claude_trace") or {}
                for call in stage_trace.get("tool_calls") or []:
                    tool_calls.append(call)
        for call in tool_calls:
            if not isinstance(call, dict):
                continue
            name = str(call.get("name") or "").strip()
            if not name:
                continue
            entry = skill_usage.setdefault(
                name,
                {
                    "skill_name": name,
                    "usage_count": 0,
                    "total_latency_ms": 0.0,
                    "total_input_tokens": 0,
                    "total_output_tokens": 0,
                    "error_count": 0,
                    "stages": set(),
                    "classification": "unknown",
                },
            )
            entry["usage_count"] += 1
            entry["total_latency_ms"] += float(
                call.get("latency_ms") or call.get("duration_ms") or 0.0
            )
            entry["total_input_tokens"] += int(
                call.get("input_tokens") or call.get("context_tokens") or 0
            )
            entry["total_output_tokens"] += int(call.get("output_tokens") or 0)
            if call.get("is_error") or call.get("error"):
                entry["error_count"] += 1
            stage = str(call.get("stage") or "").strip()
            if stage:
                entry["stages"].add(stage)
    report = []
    for name, entry in sorted(skill_usage.items()):
        stages = sorted(entry["stages"])
        n = name
        uc = entry["usage_count"]
        classification = _cs(n, uc)
        avg_latency = (
            round(entry["total_latency_ms"] / entry["usage_count"], 2)
            if entry["usage_count"] > 0
            else 0.0
        )
        report.append(
            {
                "skill_name": name,
                "usage_count": entry["usage_count"],
                "average_latency_ms": avg_latency,
                "total_input_tokens": entry["total_input_tokens"],
                "total_output_tokens": entry["total_output_tokens"],
                "error_count": entry["error_count"],
                "stages": stages,
                "classification": classification,
            }
        )
    return report


@router.get("/skill-report")
async def skill_report(request: Request) -> dict[str, Any]:
    logger.debug("Skill report requested")
    report = _bsr(request.app.state.dashboard)
    return {
        "generated_at": _now_iso(),
        "skills": report,
        "total_skills": len(report),
        "total_invocations": sum((s["usage_count"] for s in report)),
    }


def _crm(state: DashboardState) -> dict[str, Any]:
    logger.debug("Computing risk metrics")
    if (config := state.config) is None:
        return empty_risk_response()
    sessions_dir = config.root_dir / "sessions"
    if not sessions_dir.exists():
        return empty_risk_response()
    try:
        return compute_risk_metrics(sessions_dir)
    except ImportError:
        return {**empty_risk_response(), "note": "numpy not available"}
    except Exception as exc:
        logger.warning("Risk computation failed: %s", exc)
        return {**empty_risk_response(), "note": "Error computing risk metrics"}


@router.get("/risk")
async def risk(request: Request) -> dict[str, Any]:
    logger.debug("Risk metrics requested")
    return {"generated_at": _now_iso(), **_crm(request.app.state.dashboard)}


@router.get("/market/symbols")
async def market_symbols(request: Request) -> dict[str, Any]:
    logger.debug("Market symbols requested")
    if (feeds := request.app.state.dashboard.get_sodex_feeds()) is None:
        return {"symbols": [], "note": "SoDEXFeeds not available"}
    try:
        symbols = await feeds.fetch_symbols()
        return {"symbols": symbols, "count": len(symbols)}
    except Exception as exc:
        logger.warning("Market symbols error: %s", exc)
        return {"symbols": [], "error": "Internal error"}


@router.get("/market/tickers")
async def market_tickers(request: Request) -> dict[str, Any]:
    logger.debug("Market tickers requested")
    if (feeds := request.app.state.dashboard.get_sodex_feeds()) is None:
        return {"tickers": [], "note": "SoDEXFeeds not available"}
    try:
        tickers = await feeds.fetch_tickers()
        return {"tickers": tickers, "count": len(tickers)}
    except Exception as exc:
        logger.warning("Market tickers error: %s", exc)
        return {"tickers": [], "error": "Internal error"}


@router.get("/market/klines/{symbol}")
async def market_klines(
    request: Request, symbol: str, interval: str = "1h", limit: int = 60
) -> dict[str, Any]:
    logger.debug("Market klines requested: %s", symbol)
    if (feeds := request.app.state.dashboard.get_sodex_feeds()) is None:
        return {"klines": [], "symbol": symbol, "note": "SoDEXFeeds not available"}
    try:
        frame = await feeds.fetch_klines(symbol, interval, limit=limit)
        records = (
            frame.reset_index().to_dict(orient="records") if not frame.empty else []
        )
        for rec in records:
            ts = rec.get("timestamp")
            if ts is not None and hasattr(ts, "isoformat"):
                rec["timestamp"] = ts.isoformat()
        return {
            "klines": records,
            "symbol": symbol,
            "interval": interval,
            "count": len(records),
        }
    except Exception as exc:
        logger.warning("Market klines error (%s): %s", symbol, exc)
        return {"klines": [], "symbol": symbol, "error": "Internal error"}


@router.get("/market/orderbook/{symbol}")
async def market_orderbook(
    request: Request, symbol: str, limit: int = 20
) -> dict[str, Any]:
    logger.debug("Market orderbook requested: %s", symbol)
    if (feeds := request.app.state.dashboard.get_sodex_feeds()) is None:
        return {
            "bids": [],
            "asks": [],
            "symbol": symbol,
            "note": "SoDEXFeeds not available",
        }
    try:
        data = await feeds.fetch_orderbook(symbol, limit=limit)
        return {
            "bids": data.get("bids", []),
            "asks": data.get("asks", []),
            "symbol": symbol,
        }
    except Exception as exc:
        logger.warning("Market orderbook error (%s): %s", symbol, exc)
        return {"bids": [], "asks": [], "symbol": symbol, "error": "Internal error"}


@router.get("/partials/dashboard/summary")
async def partial_dashboard_summary(request: Request) -> Any:
    if isinstance(tmpl := request.app.state.dashboard.templates, dict):
        return tmpl
    return tmpl.TemplateResponse(
        request,
        "partials/dashboard/_summary_cards.html",
        {"request": request, **request.app.state.dashboard.runs_payload()},
    )


@router.get("/partials/dashboard/runs")
async def partial_dashboard_runs(
    request: Request, track: str = "all", family: str = "all"
) -> Any:
    if isinstance(tmpl := request.app.state.dashboard.templates, dict):
        return tmpl
    _track = track if track and track != "all" else None
    _family = family if family and family != "all" else None
    return tmpl.TemplateResponse(
        request,
        "partials/dashboard/_run_cards.html",
        {
            "request": request,
            **request.app.state.dashboard.runs_payload(track=_track, family=_family),
            "track": track,
            "family": family,
            "metric": "aggregate_score",
        },
    )

@router.get("/partials/dashboard/most_recent_run")
async def partial_dashboard_most_recent_run(request: Request) -> Any:
    """Return the most recent run as a hero section."""
    dashboard = request.app.state.dashboard
    data = dashboard.runs_payload()
    runs = data.get("runs", [])
    if not runs:
        return Response(content="", media_type="text/html")
    most_recent = runs[0]
    run_id = most_recent.get("run_id", "")
    label = most_recent.get("label", run_id)
    track = most_recent.get("track", "")
    family = most_recent.get("family", "")
    status = most_recent.get("status", "")
    status_class = "status-pass" if status in ("deployd", "pass") else "status-fail"
    return Response(
        content=f"""
        <div class="most-recent-run-content">
          <div class="most-recent-run-header">
            <span class="most-recent-run-badge">Most Recent Run</span>
            <span class="{status_class}">{status}</span>
          </div>
          <h2 class="most-recent-run-title">{label}</h2>
          <p class="most-recent-run-meta">{track} · {family}</p>
          <a href="/runs/{run_id}" class="button-link">View Run Details →</a>
        </div>
        """,
        media_type="text/html",
    )


_PARTIAL_OPS = {
    "summary": (
        "partials/ops/_ops_summary.html",
        lambda s, r, tmpl: tmpl.TemplateResponse(
            r, "partials/ops/_ops_summary.html", {"request": r, **s.ops_payload()}
        ),
    ),
    "artifact_health": (
        "partials/ops/_artifact_health.html",
        lambda s, r, tmpl: tmpl.TemplateResponse(
            r,
            "partials/ops/_artifact_health.html",
            {
                "request": r,
                "artifact_status": s.ops_payload().get("artifact_status", {}),
            },
        ),
    ),
    "wave_state": (
        "partials/ops/_wave_state.html",
        lambda s, r, tmpl: tmpl.TemplateResponse(
            r,
            "partials/ops/_wave_state.html",
            {"request": r, "summary": s.ops_payload().get("summary", {})},
        ),
    ),
    "buildathon_proof": (
        "partials/ops/_buildathon_proof.html",
        lambda s, r, tmpl: tmpl.TemplateResponse(
            r,
            "partials/ops/_buildathon_proof.html",
            {"request": r, "summary": s.ops_payload().get("summary", {})},
        ),
    ),
    "market_state": (
        "partials/ops/_market_state.html",
        lambda s, r, tmpl: tmpl.TemplateResponse(
            r,
            "partials/ops/_market_state.html",
            {"request": r, "summary": s.ops_payload().get("summary", {})},
        ),
    ),
    "sodex_boundary": (
        "partials/ops/_sodex_boundary.html",
        lambda s, r, tmpl: tmpl.TemplateResponse(
            r,
            "partials/ops/_sodex_boundary.html",
            {"request": r, "summary": s.ops_payload().get("summary", {})},
        ),
    ),
    "telemetry_state": (
        "partials/ops/_telemetry_state.html",
        lambda s, r, tmpl: tmpl.TemplateResponse(
            r,
            "partials/ops/_telemetry_state.html",
            {"request": r, "summary": s.ops_payload().get("summary", {})},
        ),
    ),
    "blockers": (
        "partials/ops/_blockers.html",
        lambda s, r, tmpl: tmpl.TemplateResponse(
            r,
            "partials/ops/_blockers.html",
            {"request": r, "summary": s.ops_payload().get("summary", {})},
        ),
    ),
}


@router.get("/partials/ops/{name}")
async def partial_ops_router(request: Request, name: str) -> Any:
    if isinstance(tmpl := request.app.state.dashboard.templates, dict):
        return tmpl
    handler = _PARTIAL_OPS.get(name)
    if handler is None:
        return {"error": f"Unknown partial: {name}"}
    return handler[1](request.app.state.dashboard, request, tmpl)


@router.get("/partials/run/summary")
async def partial_run_summary(
    request: Request, run_id: str = "", track: str = "all", family: str = "all"
) -> Any:
    if isinstance(tmpl := request.app.state.dashboard.templates, dict):
        return tmpl
    _track = track if track and track != "all" else None
    _family = family if family and family != "all" else None
    payload = request.app.state.dashboard.experiments_payload(
        track=_track, family=_family
    )
    runs = payload.get("runs", [])
    experiments = payload.get("experiments", [])
    selected_run = (
        next((r for r in runs if r.get("run_session_id") == run_id), None)
        if run_id
        else None
    )
    return tmpl.TemplateResponse(
        request,
        "partials/run/_run_summary.html",
        {
            "request": request,
            "runs": runs,
            "experiments": experiments,
            "selected_run": selected_run,
            "metric": "aggregate_score",
        },
    )


@router.get("/partials/run/family_pills")
async def partial_run_family_pills(
    request: Request, run_id: str = "", family: str = "all"
) -> Any:
    if isinstance(tmpl := request.app.state.dashboard.templates, dict):
        return tmpl
    families = sorted(
        request.app.state.dashboard.experiments_payload()
        .get("summary", {})
        .get("families", [])
        or []
    )
    return tmpl.TemplateResponse(
        request,
        "partials/run/_family_pills.html",
        {"request": request, "families": families, "family": family},
    )


@router.get("/partials/run/improvement_chart")
async def partial_run_improvement_chart(
    request: Request,
    run_id: str = "",
    metric: str = "aggregate_score",
    track: str = "all",
    family: str = "all",
) -> Any:
    if isinstance(tmpl := request.app.state.dashboard.templates, dict):
        return tmpl
    _track = track if track and track != "all" else None
    _family = family if family and family != "all" else None
    experiments = request.app.state.dashboard.experiments_payload(
        track=_track, family=_family
    ).get("experiments", [])
    if run_id:
        experiments = [e for e in experiments if e.get("run_session_id") == run_id]
    return tmpl.TemplateResponse(
        request,
        "partials/run/_improvement_chart.html",
        {"request": request, "experiments": experiments, "metric": metric},
    )


@router.get("/partials/run/experiment_table")
async def partial_run_experiment_table(
    request: Request, run_id: str = "", track: str = "all", family: str = "all"
) -> Any:
    if isinstance(tmpl := request.app.state.dashboard.templates, dict):
        return tmpl
    _track = track if track and track != "all" else None
    _family = family if family and family != "all" else None
    experiments = request.app.state.dashboard.experiments_payload(
        track=_track, family=_family
    ).get("experiments", [])
    if run_id:
        experiments = [e for e in experiments if e.get("run_session_id") == run_id]
    if _family:
        experiments = [e for e in experiments if e.get("family") == _family]
    return tmpl.TemplateResponse(
        request,
        "partials/run/_experiment_table.html",
        {"request": request, "experiments": experiments},
    )


@router.get("/partials/run/detail_panel")
async def partial_run_detail_panel(request: Request, spec_hash: str = "") -> Any:
    if isinstance(tmpl := request.app.state.dashboard.templates, dict):
        return tmpl
    if not spec_hash:
        return tmpl.TemplateResponse(
            request,
            "partials/run/_detail_panel.html",
            {"request": request, "experiment": {}},
        )
    return tmpl.TemplateResponse(
        request,
        "partials/run/_detail_panel.html",
        {
            "request": request,
            "experiment": (
                request.app.state.dashboard.experiment_detail_payload(spec_hash) or {}
            ).get("experiment", {})
            or {},
        },
    )


_PARTIAL_EXP = {
    "summary": lambda s, r, tmpl, spec_hash: tmpl.TemplateResponse(
        r,
        "partials/experiment/_experiment_summary.html",
        {
            "request": r,
            "experiment": (s.experiment_detail_payload(spec_hash) or {}).get(
                "experiment", {}
            )
            or {}
            if spec_hash
            else {},
        },
    ),
    "equity_chart": lambda s, r, tmpl, spec_hash: tmpl.TemplateResponse(
        r,
        "partials/experiment/_equity_chart.html",
        {
            "request": r,
            "run": (s.experiment_series_payload(spec_hash) or {}).get("canonical_run")
            or {}
            if spec_hash
            else {},
        },
    ),
    "metrics_chart": lambda s, r, tmpl, spec_hash: tmpl.TemplateResponse(
        r,
        "partials/experiment/_metrics_chart.html",
        {
            "request": r,
            "run": (s.experiment_series_payload(spec_hash) or {}).get("canonical_run")
            or {}
            if spec_hash
            else {},
        },
    ),
    "snapshot": lambda s, r, tmpl, spec_hash: tmpl.TemplateResponse(
        r,
        "partials/experiment/_snapshot.html",
        {
            "request": r,
            **(
                {}
                if not spec_hash
                else (
                    lambda d, ser: {
                        "experiment": (d or {}).get("experiment", {}) or {},
                        **(
                            {}
                            if not (ser := s.experiment_series_payload(spec_hash))
                            else {
                                "run": ser.get("canonical_run") or {},
                                "series_available": bool(ser.get("series_available")),
                                "compiled_metadata": ser.get("compiled_metadata") or {},
                            }
                        ),
                    }
                )(s.experiment_detail_payload(spec_hash), None)
            ),
        },
    ),
    "deployment": lambda s, r, tmpl, spec_hash: tmpl.TemplateResponse(
        r,
        "partials/experiment/_deployment.html",
        {
            "request": r,
            "experiment": (s.experiment_detail_payload(spec_hash) or {}).get(
                "experiment", {}
            )
            or {}
            if spec_hash
            else {},
        },
    ),
    "heatmap": lambda s, r, tmpl, spec_hash: tmpl.TemplateResponse(
        r,
        "partials/experiment/_heatmap.html",
        {
            "request": r,
            "run": (s.experiment_series_payload(spec_hash) or {}).get("canonical_run")
            or {}
            if spec_hash
            else {},
        },
    ),
    "trades": lambda s, r, tmpl, spec_hash: tmpl.TemplateResponse(
        r,
        "partials/experiment/_trades.html",
        {
            "request": r,
            **(
                {
                    "trades": (s.experiment_series_payload(spec_hash) or {}).get(
                        "canonical_run"
                    )
                    or {}
                    if spec_hash
                    else []
                }
                if spec_hash
                else {"trades": []}
            ),
            "page": 1,
            "display_capital": 100000,
        },
    ),
    "actions": lambda s, r, tmpl, spec_hash: tmpl.TemplateResponse(
        r,
        "partials/experiment/_actions.html",
        {
            "request": r,
            "run": (s.experiment_series_payload(spec_hash) or {}).get("canonical_run")
            or {}
            if spec_hash
            else {},
        },
    ),
}


@router.get("/partials/experiment/{name}")
async def partial_experiment_router(
    request: Request, name: str, spec_hash: str = ""
) -> Any:
    if isinstance(tmpl := request.app.state.dashboard.templates, dict):
        return tmpl
    handler = _PARTIAL_EXP.get(name)
    if handler is None:
        return {"error": f"Unknown partial: {name}"}
    return handler(request.app.state.dashboard, request, tmpl, spec_hash)


@router.get("/templates/dashboard")
async def template_dashboard(request: Request) -> Any:
    if isinstance(tmpl := request.app.state.dashboard.templates, dict):
        return tmpl
    return tmpl.TemplateResponse(
        request, "dashboard.html", {"request": request, "track": "all", "family": "all"}
    )


@router.get("/templates/runs/{run_id:path}")
async def template_run(request: Request, run_id: str = "") -> Any:
    if isinstance(tmpl := request.app.state.dashboard.templates, dict):
        return tmpl
    return tmpl.TemplateResponse(
        request,
        "run.html",
        {"request": request, "run_id": run_id, "track": "all", "family": "all"},
    )


@router.get("/templates/experiments/{spec_hash:path}")
async def template_experiment(request: Request, spec_hash: str = "") -> Any:
    if isinstance(tmpl := request.app.state.dashboard.templates, dict):
        return tmpl
    return tmpl.TemplateResponse(
        request, "experiment.html", {"request": request, "spec_hash": spec_hash}
    )


@router.get("/templates/ops")
async def template_ops(request: Request) -> Any:
    if isinstance(tmpl := request.app.state.dashboard.templates, dict):
        return tmpl
    return tmpl.TemplateResponse(request, "ops.html", {"request": request})


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


class WebSocketManager:
    def __init__(self) -> None:
        self._connections: set[Any] = set()
        self._subscriptions: dict[str, set[Any]] = {}

    def register(self, websocket: object) -> None:
        self._connections.add(websocket)

    def unregister(self, websocket: object) -> None:
        self._connections.discard(websocket)
        for subs in self._subscriptions.values():
            subs.discard(websocket)

    def subscribe(self, symbol: str, websocket: object) -> None:
        self._subscriptions.setdefault(symbol, set()).add(websocket)

    def unsubscribe(self, symbol: str, websocket: object) -> None:
        subs = self._subscriptions.get(symbol)
        if subs:
            subs.discard(websocket)


@asynccontextmanager
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
    state.ws_manager = WebSocketManager()
    state.start_time = time.time()
    _template_dir = Path(__file__).resolve().parent / "templates"
    state.templates = Jinja2Templates(directory=str(_template_dir))
    _env = state.templates.env

    def _fmt_num(value: float | int | str | None, decimals: int = 2) -> str:
        try:
            if value is None:
                return "n/a"
            v = float(value)
            if not (v != v or v == float("inf") or v == float("-inf")):
                return f"{v:.{decimals}f}"
        except (TypeError, ValueError):
            pass
        return "n/a"

    def _fmt_pct(value: float | int | str | None) -> str:
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
                "%Y-%m-%d %H:%M:%S"
            )
        except (ValueError, TypeError):
            return str(value)

    _env.filters["format_number"] = _fmt_num
    _env.filters["format_pct"] = _fmt_pct
    _env.filters["format_dt"] = _fmt_dt
    app.state.dashboard = state
    yield


def create_app() -> FastAPI:
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
            request, "dashboard.html", {"request": request}
        )

    @app.get("/ops")
    async def serve_ops(request: Request):
        return request.app.state.dashboard.templates.TemplateResponse(
            request, "ops.html", {"request": request}
        )

    @app.get("/runs/{run_id:path}")
    async def serve_run_page(run_id: str, request: Request):
        return request.app.state.dashboard.templates.TemplateResponse(
            request, "run.html", {"request": request, "run_id": run_id}
        )

    @app.get("/experiments/{spec_hash:path}")
    async def serve_experiment_page(spec_hash: str, request: Request):
        return request.app.state.dashboard.templates.TemplateResponse(
            request, "experiment.html", {"request": request, "spec_hash": spec_hash}
        )

    if _static_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(_static_dir)), name="static")
    return app


app = create_app()
_WS_HANDLERS = {}


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    state = websocket.app.state.dashboard
    manager = state.ws_manager
    manager.register(websocket)
    subscribed_symbols: set[str] = set()
    subscription_types: set[str] = set()
    risk_push_tasks: set[asyncio.Task[None]] = set()
    try:
        await _send_json(
            websocket,
            {
                "type": "connected",
                "message": "SigLab WebSocket connected",
                "timestamp": _now_iso(),
            },
        )
        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
            except asyncio.TimeoutError:
                try:
                    await _send_json(
                        websocket, {"type": "ping", "timestamp": _now_iso()}
                    )
                except (OSError, ValueError):
                    logger.debug("WebSocket send error in ping keepalive")
                    break
                continue
            if not raw.strip():
                continue
            try:
                message = json.loads(raw)
            except (json.JSONDecodeError, TypeError, ValueError):
                await _send_json(
                    websocket, {"type": "error", "message": "Invalid JSON payload"}
                )
                continue
            await _handle_message(
                websocket,
                message,
                manager,
                subscribed_symbols,
                subscription_types,
                risk_push_tasks,
            )
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.warning("WS error: %s", exc)
    finally:
        for task in risk_push_tasks:
            task.cancel()
        risk_push_tasks.clear()
        manager.unregister(websocket)
        for symbol in list(subscribed_symbols):
            manager.unsubscribe(symbol, websocket)


async def _periodic_risk_push(ws: WebSocket) -> None:
    try:
        while True:
            await asyncio.sleep(15)
            await _stream_risk_scores(ws)
    except (asyncio.CancelledError, Exception):
        pass


async def _handle_subscribe(
    websocket, message, manager, subscribed_symbols, subscription_types, risk_push_tasks
):
    symbol = str(message.get("symbol") or "").strip().upper()
    sub_type = str(message.get("subscription_type") or "klines").strip().lower()
    if sub_type == "risk_score":
        subscribed_symbols.add("_risk")
        subscription_types.add("risk_score")
        manager.subscribe("_risk", websocket)
        await _send_json(
            websocket,
            {
                "type": "subscribed",
                "subscription_type": "risk_score",
                "message": "Subscribed to risk score updates",
            },
        )
        await _stream_risk_scores(websocket)
        if risk_push_tasks is not None:
            task = asyncio.create_task(_periodic_risk_push(websocket))
            risk_push_tasks.add(task)
            task.add_done_callback(risk_push_tasks.discard)
        return
    if not symbol:
        await _send_json(
            websocket,
            {"type": "error", "message": "Missing 'symbol' field for subscribe"},
        )
        return
    subscribed_symbols.add(symbol)
    subscription_types.add(sub_type)
    manager.subscribe(symbol, websocket)
    await _send_json(
        websocket,
        {
            "type": "subscribed",
            "symbol": symbol,
            "subscription_type": sub_type,
            "message": f"Subscribed to {sub_type} for {symbol}",
        },
    )
    await _stream_initial_data(websocket, symbol, sub_type)


async def _handle_unsubscribe(
    websocket, message, manager, subscribed_symbols, subscription_types, risk_push_tasks
):
    symbol = str(message.get("symbol") or "").strip().upper()
    if symbol:
        subscribed_symbols.discard(symbol)
        manager.unsubscribe(symbol, websocket)
    else:
        for sym in list(subscribed_symbols):
            manager.unsubscribe(sym, websocket)
        subscribed_symbols.clear()
    await _send_json(
        websocket, {"type": "unsubscribed", "symbol": symbol if symbol else "all"}
    )


async def _handle_message(
    websocket,
    message,
    manager,
    subscribed_symbols,
    subscription_types,
    risk_push_tasks=None,
):
    action = str(message.get("action") or message.get("type") or "").strip().lower()
    _WS_HANDLERS.clear()
    _WS_HANDLERS.update(
        {
            "ping": lambda: _send_json(
                websocket, {"type": "pong", "timestamp": _now_iso()}
            ),
            "pong": lambda: _send_json(
                websocket, {"type": "pong", "timestamp": _now_iso()}
            ),
            "subscribe": lambda: _handle_subscribe(
                websocket,
                message,
                manager,
                subscribed_symbols,
                subscription_types,
                risk_push_tasks,
            ),
            "unsubscribe": lambda: _handle_unsubscribe(
                websocket,
                message,
                manager,
                subscribed_symbols,
                subscription_types,
                risk_push_tasks,
            ),
            "get_positions": lambda: _stream_positions(websocket),
            "get_risk": lambda: _stream_risk_scores(websocket),
        }
    )
    handler = _WS_HANDLERS.get(action)
    if handler:
        await handler()
    else:
        await _send_json(
            websocket,
            {
                "type": "error",
                "message": f"Unknown action: {action}. Supported: ping, subscribe, unsubscribe, get_positions, get_risk",
            },
        )


async def _stream_initial_data(
    websocket: WebSocket, symbol: str, sub_type: str
) -> None:
    if sub_type == "klines":
        await _send_json(
            websocket,
            {
                "type": "klines",
                "symbol": symbol,
                "data": await _fetch_cached_klines(websocket, symbol),
                "interval": "1h",
            },
        )
    elif sub_type in ("ticks", "ticker"):
        await _send_json(
            websocket,
            {
                "type": "ticker",
                "symbol": symbol,
                **await _fetch_cached_ticker(websocket, symbol),
                "timestamp": _now_iso(),
            },
        )
    elif sub_type == "positions":
        await _stream_positions(websocket)


async def _fetch_cached_klines(
    websocket: WebSocket, symbol: str
) -> list[dict[str, Any]]:
    try:
        if (feeds := websocket.app.state.dashboard.get_sodex_feeds()) is not None:
            frame = await feeds.fetch_klines(symbol, "1h", limit=60)
            if not frame.empty:
                records = frame.reset_index().to_dict(orient="records")
                for rec in records:
                    ts = rec.get("timestamp")
                    if ts is not None and hasattr(ts, "isoformat"):
                        rec["timestamp"] = int(ts.timestamp() * 1000)
                    for col in (
                        "open",
                        "high",
                        "low",
                        "close",
                        "volume",
                        "quote_volume",
                    ):
                        val = rec.get(col)
                        if val is not None:
                            rec[col] = float(val)
                return records
    except Exception as exc:
        logger.warning("WS klines fetch error for %s: %s", symbol, exc)
    now = datetime.now(UTC)
    timestamp = int(now.timestamp() * 1000)
    return [
        {
            "timestamp": timestamp - 3600000 * i,
            "open": 0.0,
            "high": 0.0,
            "low": 0.0,
            "close": 0.0,
            "volume": 0.0,
            "quote_volume": 0.0,
        }
        for i in range(5)
    ]


async def _fetch_cached_ticker(websocket: WebSocket, symbol: str) -> dict[str, Any]:
    try:
        if (feeds := websocket.app.state.dashboard.get_sodex_feeds()) is not None:
            tickers = await feeds.fetch_tickers(symbol=symbol)
            if tickers:
                t = tickers[0]
                return {
                    "bid": float(t.get("bidPrice", t.get("bid", 0.0))),
                    "ask": float(t.get("askPrice", t.get("ask", 0.0))),
                    "last_price": float(t.get("lastPrice", t.get("close", 0.0))),
                }
    except Exception as exc:
        logger.warning("WS ticker fetch error for %s: %s", symbol, exc)
    return {"bid": 0.0, "ask": 0.0, "last_price": 0.0}


async def _stream_positions(websocket: WebSocket) -> None:
    try:
        if (config := websocket.app.state.dashboard.config) is None:
            await _send_json(
                websocket,
                {"type": "positions", "positions": [], "note": "Config not loaded"},
            )
            return
        sessions_dir = config.root_dir / "sessions"
        if not sessions_dir.exists():
            await _send_json(
                websocket,
                {
                    "type": "positions",
                    "positions": [],
                    "note": "No paper sessions found",
                },
            )
            return
        positions_list: list[dict[str, Any]] = []
        for npy_file in sorted(sessions_dir.glob("*.npy")):
            try:
                positions_list.append(
                    {
                        "session_id": npy_file.stem,
                        "symbol": "unknown",
                        "size": 0.0,
                        "entry_price": 0.0,
                        "current_price": 0.0,
                        "unrealized_pnl": 0.0,
                    }
                )
            except (OSError, ValueError, TypeError):
                logger.debug("Failed to read npy session file %s", npy_file)
                continue
        await _send_json(websocket, {"type": "positions", "positions": positions_list})
    except ImportError:
        await _send_json(
            websocket,
            {
                "type": "positions",
                "positions": [],
                "note": "Paper trading not available",
            },
        )
    except Exception as exc:
        await _send_json(
            websocket, {"type": "positions", "positions": [], "note": f"Error: {exc}"}
        )


async def _stream_risk_scores(websocket: WebSocket) -> None:
    try:
        if (config := websocket.app.state.dashboard.config) is None:
            await _send_json(
                websocket,
                {
                    "type": "risk_score",
                    **empty_risk_response(),
                    "note": "Config not loaded",
                },
            )
            return
        sessions_dir = config.root_dir / "sessions"
        if not sessions_dir.exists():
            await _send_json(
                websocket,
                {
                    "type": "risk_score",
                    **empty_risk_response(),
                    "note": "No paper sessions found",
                },
            )
            return
        await _send_json(
            websocket,
            {
                "type": "risk_score",
                **compute_risk_metrics(sessions_dir),
                "timestamp": _now_iso(),
            },
        )
    except ImportError:
        await _send_json(
            websocket,
            {
                "type": "risk_score",
                **empty_risk_response(),
                "note": "numpy not available",
            },
        )
    except Exception as exc:
        await _send_json(
            websocket,
            {"type": "risk_score", **empty_risk_response(), "note": f"Error: {exc}"},
        )


async def _send_json(websocket: WebSocket, data: dict[str, Any]) -> None:
    try:
        await websocket.send_json(data)
    except Exception:
        logger.debug("WebSocket send_json failed (client likely disconnected)")
