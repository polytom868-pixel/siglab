"""Shared types and pure helper functions for the lineage subsystem."""

from __future__ import annotations

from datetime import datetime
from siglab.utils import safe_float as _safe_float
from typing import Any

from typing_extensions import TypedDict

__all__ = ["_safe_float"]


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

SpecHash = str
TrackName = str
JsonDict = dict[str, Any]


# ---------------------------------------------------------------------------
# TypedDicts
# ---------------------------------------------------------------------------

class ExperimentRow(TypedDict, total=False):
    created_at: str
    spec_hash: str
    family: str
    parent_hash: str | None
    aggregate_score: float
    passed: bool
    deployd: bool
    spec: dict[str, Any]
    research_summary: dict[str, Any]
    summary: dict[str, Any]
    artifact_path: str | None


class DeploymentRow(TypedDict):
    spec_hash: str
    created_at: str
    strategy_name: str
    strategy_dir: str
    spec_path: str
    manifest_path: str
    readme_path: str
    job_name: str | None
    interval_seconds: int | None
    wallet_label: str | None
    config_path: str
    scheduled: bool
    dry_run: bool
    llm_finalized: bool
    support_status: str
    support_reason: str | None
    metadata: dict[str, Any]


class DashboardRow(TypedDict, total=False):
    event_id: int
    created_at: str
    track: str
    family: str
    spec_hash: str
    parent_hash: str | None
    aggregate_score: float
    passed: bool
    deployd: bool
    spec: dict[str, Any]
    research_summary: dict[str, Any]
    summary: dict[str, Any]
    artifact_path: str | None
    feature_hash: str
    deployment: dict[str, Any] | None


class DiagnosticSnapshot(TypedDict, total=False):
    trade_style: str
    policy: dict[str, Any]
    behavior_pack: dict[str, Any]
    regime_pack: dict[str, Any]
    trade_regime_pack: dict[str, Any]
    drawdown_pack: dict[str, Any]
    equity_shift_pack: dict[str, Any]
    time_bin_pack: dict[str, Any]
    exemplar_trade_pack: dict[str, Any]
    gate_diagnostics: dict[str, Any]
    diagnostic_tags: list[str]


class MemoryPacket(TypedDict, total=False):
    market_bundle: dict[str, Any]
    pareto_frontier: list[dict[str, Any]]
    validation_leaders: list[dict[str, Any]]
    nearest_winners: list[dict[str, Any]]
    nearest_failures: list[dict[str, Any]]
    outstanding_runs: list[dict[str, Any]]
    last_five_runs: list[dict[str, Any]]
    coverage_summary: dict[str, Any]
    archetype_coverage: list[dict[str, Any]]
    novelty_pressure: dict[str, Any]
    failure_pattern_summary: dict[str, Any]
    behavior_pattern_summary: dict[str, Any]
    regime_pattern_summary: dict[str, Any]
    drawdown_pattern_summary: dict[str, Any]
    gate_pattern_summary: dict[str, Any]
    equity_pattern_summary: dict[str, Any]
    query_cards: list[dict[str, Any]]


class RunSummary(TypedDict, total=False):
    run_session_id: str
    run_label: str
    track: str
    runner_label: str
    run_kind: str
    memory_scope: str
    benchmark_mode: bool
    benchmark_deck: Any
    phase_labels: list[str]
    families: list[str]
    experiment_count: int
    llm_experiment_count: int
    deterministic_experiment_count: int
    tool_call_count: int
    passed_count: int
    deployd_count: int
    first_created_at: str | None
    last_created_at: str | None
    best_spec_hash: str | None
    best_family: str | None
    best_aggregate_score: float | None
    best_validation_total_return: float | None
    best_pre_audit_canonical_total_return: float | None
    status: str


class CoverageSummary(TypedDict, total=False):
    experiments_total: int
    passed_total: int
    families: list[dict[str, Any]]
    assets: list[dict[str, Any]]
    features: list[dict[str, Any]]
    failure_modes: list[dict[str, Any]]


class NoveltyPressure(TypedDict, total=False):
    required: bool
    reason: str
    recent_count: int
    dominant_family: dict[str, Any]
    dominant_family_positive_anchor: bool
    dominant_family_best_pre_audit: float | None
    dominant_trade_style: dict[str, Any]
    restrictive_gate_share: float
    low_activity_share: float
    overused_features: list[dict[str, Any]]


class FailurePatternSummary(TypedDict):
    gate_reasons: list[dict[str, Any]]
    diagnostic_tags: list[dict[str, Any]]


class BehaviorPatternSummary(TypedDict, total=False):
    median_trade_count: float | None
    median_holding_bars: float | None
    median_gap_hours: float | None
    median_flip_rate: float | None
    patterns: list[dict[str, Any]]


class RegimePatternSummary(TypedDict):
    pass  # dynamic keys per dimension


class DrawdownPatternSummary(TypedDict, total=False):
    median_drawdown: float | None
    median_signal_alignment: float | None
    dominant_position_directions: list[dict[str, Any]]
    common_feature_contributors: list[dict[str, Any]]


class GatePatternSummary(TypedDict, total=False):
    median_active_bar_fraction: float | None
    median_entry_signal_bar_fraction: float | None
    median_score_alignment_when_active: float | None
    median_position_flip_rate: float | None
    bottleneck_tags: list[dict[str, Any]]


class EquityPatternSummary(TypedDict, total=False):
    median_max_drawdown: float | None
    median_post_peak_entries_per_day: float | None
    median_drawdown_window_entries_per_day: float | None
    drawdown_window_regimes: dict[str, list[dict[str, Any]]]


class QueryCardRow(TypedDict, total=False):
    created_at: str
    family: str
    parent_hash: str | None
    market_bundle_id: str | None
    as_of: str | None
    provider: str
    query: str
    answer: str | None
    insights: list[str]
    sources: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# Pure helper functions
# ---------------------------------------------------------------------------



def _median_value(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _delta(current: Any, previous: Any) -> float | None:
    current_value = _safe_float(current, default=None)
    previous_value = _safe_float(previous, default=None)
    if current_value is None or previous_value is None:
        return None
    return current_value - previous_value


def _parse_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _tokens(value: Any) -> set[str]:
    text = str(value or "").lower()
    cleaned = "".join(char if char.isalnum() else " " for char in text)
    return {token for token in cleaned.split() if len(token) >= 3}


def _spec_assets(spec: dict[str, Any]) -> list[str]:
    universe = spec.get("universe") or {}
    return [str(asset).upper() for asset in universe.get("basis_groups") or []]


def _maturity_bucket(universe: dict[str, Any]) -> str:
    min_days = int(universe.get("min_days_to_expiry", 0) or 0)
    max_days = int(universe.get("max_days_to_expiry", 0) or 0)
    if max_days <= 30:
        return "short"
    if min_days >= 60:
        return "long"
    return "medium"
