"""ExperimentEnricher — data transformation layer for the dashboard."""

from __future__ import annotations

import json
import math
import logging
from pathlib import Path
from typing import Any

from siglab.config import SiglabConfig
from siglab.dashboard.experiment_repo import ExperimentRepo
from siglab.llm.llm import (
    default_llm_model_display,
    infer_llm_provider,
    resolve_llm_provider,
)
from siglab.path_utils import display_path, resolve_path_from_root
from siglab.track_registry import resolve_track, track_label
from siglab.live import deployment_readiness
from siglab.utils import _now_iso, dget

logger = logging.getLogger(__name__)


def _classify_skill(n: str, uc: int) -> str:
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


class ExperimentEnricher:
    """Data transformation layer — enriches raw data for API responses.

    Uses a reference to ExperimentRepo for JSON file loading (trace files).
    """

    def __init__(self, config: SiglabConfig | None, repo: ExperimentRepo) -> None:
        self.config = config
        self.repo = repo

    # ── Display helpers ──────────────────────────────────────────────

    def llm_provider_label(self) -> str:
        return (
            "unknown"
            if self.config is None
            else resolve_llm_provider(self.config)
        )

    def llm_model_label(self) -> str:
        prov = self.llm_provider_label()
        return (
            "unknown"
            if self.config is None
            else default_llm_model_display(self.config, provider=prov)
        )

    def display_path(self, value: str | Path | None) -> str | None:
        if not value:
            return None
        return (
            display_path(value, root_dir=self.config.root_dir)
            if self.config is not None
            else str(value)
        )

    def normalize_deployment(
        self,
        deployment: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
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
            normalized[key] = self.display_path(normalized.get(key))
        return normalized

    @staticmethod
    def now_iso() -> str:
        return _now_iso()

    # ── Trace / research summary helpers ─────────────────────────────

    def normalize_stage(
        self,
        *,
        stage_name: str,
        payload: dict[str, Any] | None,
        trace_path: str | Path | None,
    ) -> dict[str, Any] | None:
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
            "trace_path": self.display_path(trace_path),
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

    def research_stages(
        self,
        research_summary: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Extract tool trace stages from a research summary dict."""
        stages: list[dict[str, Any]] = []
        workspace = research_summary.get("workspace", {}) or {}
        for stage_name, path_key in (
            ("planner", "planner_trace_path"),
            ("writer", "writer_trace_path"),
            ("reflector", "reflector_trace_path"),
        ):
            trace_path = workspace.get(path_key)
            stage = self.normalize_stage(
                stage_name=stage_name,
                payload=self.repo.load_json(trace_path),
                trace_path=trace_path,
            )
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
                    "trace_path": self.display_path(tool_trace.get("log_path")),
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

    def skill_value_report(
        self,
        tool_calls: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Aggregate tool call stats into a skill value report."""
        by_name: dict[str, dict[str, Any]] = {}
        for call in tool_calls:
            name = str(call.get("name") or "").strip()
            if not name:
                continue
            row = by_name.setdefault(name, {
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
            })
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

    # ── Core experiment enrichment ───────────────────────────────────

    def enrich_experiment(
        self,
        experiment: dict[str, Any],
        *,
        include_artifact: bool = False,
    ) -> dict[str, Any]:
        """Enrich a raw experiment row with computed metadata, traces, etc."""
        spec = experiment.get("spec", {}) or {}
        summary = experiment.get("summary", {}) or {}
        research_summary = experiment.get("research_summary", {}) or {}
        raw_artifact_path = experiment.get("artifact_path")
        artifact = (experiment.get("artifact", {}) or {}) if include_artifact else {}
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
            artifact.get("compiled_metadata")
            or artifact.get("compiledMetadata")
            or {}
        )
        median_cagr = summary.get("median_cagr")
        if "median_cagr" not in summary or not (
            isinstance(median_cagr, (int, float))
            and math.isfinite(float(median_cagr))
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
        bias_controls = compiled_metadata.get("bias_controls", {}) or {}
        params = spec.get("params", {}) or {}
        tool_trace = research_summary.get("llm_tool_trace", {}) or {}
        tool_trace_stages = self.research_stages(research_summary)
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
            "error": (primary_tool_trace or {}).get("error")
            or tool_trace.get("error"),
            "provider": (primary_tool_trace or {}).get("provider"),
            "model": (primary_tool_trace or {}).get("model"),
            "thinking_mode": (primary_tool_trace or {}).get("thinking_mode"),
            "tool_rounds_used": sum(
                int(stage.get("tool_rounds_used") or 0)
                for stage in tool_trace_stages
            ),
            "tool_count_available": max(
                [int(stage.get("tool_count_available") or 0) for stage in tool_trace_stages]
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
        experiment["skill_value_report"] = self.skill_value_report(aggregated_tool_calls)
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
        experiment["feature_hash"] = experiment.get("feature_hash") or compiled_metadata.get(
            "feature_hash",
        )
        experiment["timing"] = {
            "signal_timing": compiled_metadata.get("signal_timing", "unknown"),
            "bundle_as_of": compiled_metadata.get("bundle_as_of"),
            "history_start": compiled_metadata.get("history_start"),
            "history_end": compiled_metadata.get("history_end"),
            "bias_controls": bias_controls,
        }
        experiment["feature_preview"] = list(spec.get("features") or [])[:4]
        experiment["source"] = compiled_metadata.get("source")
        readiness = deployment_readiness({
            "track": experiment["track"],
            "family": experiment["family"],
            "spec_hash": experiment["spec_hash"],
            "spec": spec,
            "summary": summary,
            "artifact": artifact,
        })
        experiment["deployment_readiness"] = readiness
        experiment["artifact_path"] = self.display_path(raw_artifact_path)
        experiment["live_deployment"] = self.normalize_deployment(
            experiment.get("deployment"),
        )
        if include_artifact and artifact:
            artifact = {**artifact}
            artifact.pop("canonical_run", None)
        experiment["artifact"] = (
            artifact if include_artifact else experiment.get("artifact")
        )
        return experiment

    def annotate_positions(
        self,
        experiments: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Annotate experiments with generation, run position, and best-so-far."""
        generation_by_track: dict[str, int] = {}
        run_position_by_session: dict[str, int] = {}
        best_by_track_metric: dict[str, float] = {}
        for global_index, experiment in enumerate(experiments, start=1):
            track_name = str(experiment.get("track") or "")
            run_session_id = str(experiment.get("run_session_id") or "")
            generation_by_track[track_name] = (
                generation_by_track.get(track_name, 0) + 1
            )
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

    def annotate_canonical_run(
        self,
        canonical_run: dict[str, Any],
    ) -> dict[str, Any]:
        """Add visual split information to a canonical run."""
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

    def summarize_ops(
        self,
        artifacts: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """Build the ops summary from loaded artifacts."""
        demo = dict(artifacts["demo_manifest"].get("payload") or {})
        telemetry_m = dict(artifacts["telemetry"].get("payload") or {})
        market = dict(artifacts["market_report"].get("payload") or {})
        preflight = dict(artifacts["sodex_preflight"].get("payload") or {})
        readiness = demo.get("readiness", {}) or {}
        decision_support = market.get("decision_support", {}) or {}
        signal_summary = market.get("signal_summary", {}) or {}
        signed_path = preflight.get("signed_path", {}) or {}
        provider_metrics = telemetry_m.get("provider_metrics", {}) or {}
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
                "returned_output_tokens": provider_metrics.get("returned_output_tokens"),
                "context_pressure_events": provider_metrics.get(
                    "context_pressure_events",
                ),
                "credit_pressure_events": provider_metrics.get(
                    "credit_pressure_events",
                ),
                "model_counts": telemetry.get("model_counts"),
            },
        }
