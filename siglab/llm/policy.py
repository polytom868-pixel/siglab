from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from siglab.llm_metadata import resolve_llm_model


@dataclass
class ModelHealth:
    unavailable: set[str] = field(default_factory=set)
    quota_blocked: set[str] = field(default_factory=set)
    latency_demoted: set[str] = field(default_factory=set)
    recent_errors: dict[str, str] = field(default_factory=dict)


class LLMRoutingPolicy:
    LATENCY_DEMOTE_MS = 10_000.0

    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self.health = ModelHealth()

    def model_for_stage(self, *, provider: str, stage: str | None, thinking_override: str | None = None) -> str:
        if provider != "bai":
            return resolve_llm_model(self.settings, provider=provider, thinking_override=thinking_override)
        stage_name = str(stage or "default").strip().lower()
        if stage_name == "planner":
            return str(getattr(self.settings, "bai_planner_model", "") or getattr(self.settings, "bai_model", "deepseek-v4-flash"))
        if stage_name == "writer":
            return str(getattr(self.settings, "bai_writer_model", "") or getattr(self.settings, "bai_model", "deepseek-v4-flash"))
        if stage_name == "reflector":
            return str(getattr(self.settings, "bai_reflector_model", "") or getattr(self.settings, "bai_fallback_fast_model", "kimi-k2.5"))
        if stage_name == "benchmark":
            return str(getattr(self.settings, "bai_writer_model", "") or getattr(self.settings, "bai_model", "deepseek-v4-flash"))
        return str(getattr(self.settings, "bai_model", "deepseek-v4-flash"))

    def candidates(self, *, provider: str, stage: str | None, primary: str) -> list[str]:
        if provider != "bai":
            return [primary]
        configured = [
            primary,
            str(getattr(self.settings, "bai_fallback_fast_model", "") or ""),
            str(getattr(self.settings, "bai_fallback_reasoning_model", "") or ""),
            str(getattr(self.settings, "bai_model", "") or ""),
        ]
        ordered: list[str] = []
        for model in configured:
            normalized = model.strip()
            if normalized and normalized not in ordered:
                ordered.append(normalized)
        latency_sensitive = str(stage or "").strip().lower() in {"writer", "reflector"}
        viable = [
            model
            for model in ordered
            if model not in self.health.unavailable
            and model not in self.health.quota_blocked
            and not (latency_sensitive and model in self.health.latency_demoted)
        ]
        if viable:
            return viable
        if not latency_sensitive:
            return []
        return [
            model
            for model in ordered
            if model not in self.health.unavailable
            and model not in self.health.quota_blocked
            and model in self.health.latency_demoted
        ]

    def mark_auth_failure(self, model: str, error_class: str) -> None:
        self.health.unavailable.add(model)
        self.health.recent_errors[model] = error_class

    def mark_quota_failure(self, model: str, error_class: str) -> None:
        self.health.quota_blocked.add(model)
        self.health.recent_errors[model] = error_class

    def record_latency(self, *, model: str, stage: str | None, elapsed_ms: float) -> None:
        stage_name = str(stage or "").strip().lower()
        if stage_name in {"writer", "reflector"} and float(elapsed_ms) > self.LATENCY_DEMOTE_MS:
            self.health.latency_demoted.add(model)
            self.health.recent_errors[model] = "LLMLatencyDemoted"

    def snapshot(self) -> dict[str, Any]:
        return {
            "unavailable": sorted(self.health.unavailable),
            "quota_blocked": sorted(self.health.quota_blocked),
            "latency_demoted": sorted(self.health.latency_demoted),
            "recent_errors": dict(self.health.recent_errors),
        }
