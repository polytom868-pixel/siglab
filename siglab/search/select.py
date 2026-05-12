from __future__ import annotations

import math
import random
from collections import defaultdict
from typing import Any

from siglab.models import SignalSpec
from siglab.search.ancestry import LineageStore
from siglab.strategy_semantics import (
    spec_feature_roles,
    gate_dimensions,
    motif_signature,
    supports_explicit_trade_style,
)

_RNG = random.Random()


def _row_quality(row: dict[str, Any]) -> float:
    summary = dict(row.get("summary") or {})
    quality = float(row.get("aggregate_score") or 0.0)
    if row.get("passed"):
        quality += 0.5
    if row.get("deployd"):
        quality += 0.25
    holdout_total_return = summary.get("holdout_total_return")
    if holdout_total_return is not None:
        quality += float(holdout_total_return) * 4.0
    holdout_sharpe = summary.get("holdout_sharpe")
    if holdout_sharpe is not None:
        quality += float(holdout_sharpe) * 0.05
    pre_audit_total_return = summary.get("pre_audit_canonical_total_return")
    if pre_audit_total_return is not None:
        quality += float(pre_audit_total_return) * 10.0
    validation_total_return = summary.get("validation_total_return")
    if validation_total_return is not None:
        quality += float(validation_total_return) * 6.0
    median_total_return = summary.get("median_total_return")
    if median_total_return is not None:
        quality += float(median_total_return) * 3.0
    pre_audit_max_drawdown = summary.get("pre_audit_canonical_max_drawdown")
    if pre_audit_max_drawdown is not None:
        quality += float(pre_audit_max_drawdown) * 2.0
    active_bar_fraction = summary.get("active_bar_fraction")
    if active_bar_fraction is not None and float(active_bar_fraction) < 0.05:
        quality -= 0.3
    return quality


def _mixed_softmax_choice(
    items: list[tuple[Any, float]],
    *,
    temperature: float = 2.5,
    uniform_mix: float = 0.2,
) -> Any:
    if len(items) == 1:
        return items[0][0]

    scores = [score for _item, score in items]
    anchor = max(scores)
    exp_weights = [
        math.exp(max(-60.0, min(60.0, (score - anchor) / max(temperature, 1e-6))))
        for score in scores
    ]
    total = sum(exp_weights) or 1.0
    softmax_weights = [weight / total for weight in exp_weights]
    uniform_weight = 1.0 / len(items)
    mixed_weights = [
        ((1.0 - uniform_mix) * weight) + (uniform_mix * uniform_weight)
        for weight in softmax_weights
    ]
    choice = _RNG.random()
    cumulative = 0.0
    for (item, _score), weight in zip(items, mixed_weights, strict=False):
        cumulative += weight
        if choice <= cumulative:
            return item
    return items[-1][0]


def _recent_rows(
    ancestry: LineageStore,
    track: str,
    *,
    limit: int,
    run_session_id: str | None = None,
) -> list[dict[str, Any]]:
    return ancestry.recent(track, limit=limit, run_session_id=run_session_id)


def pick_parent(
    track: str,
    ancestry: LineageStore,
    seed_specs: list[SignalSpec],
    *,
    run_session_id: str | None = None,
) -> SignalSpec:
    rows = _recent_rows(ancestry, track, limit=200, run_session_id=run_session_id)
    if not rows:
        return seed_specs[0]

    seed_by_family = {spec.family: spec for spec in seed_specs}
    rows_by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        rows_by_family[str(row.get("family") or "unknown")].append(row)

    family_pool: list[tuple[str, float]] = []
    for family, seed in seed_by_family.items():
        family_rows = sorted(
            rows_by_family.get(family) or [],
            key=lambda row: (
                _row_quality(row),
                float(row.get("aggregate_score") or 0.0),
            ),
            reverse=True,
        )
        if family_rows:
            family_pool.append((family, _row_quality(family_rows[0])))
        else:
            family_pool.append((family, -1.0))

    chosen_family = _mixed_softmax_choice(
        family_pool,
        temperature=3.0 if track == "trend_signals" else 2.0,
        uniform_mix=0.25 if track == "trend_signals" else 0.15,
    )
    family_rows = sorted(
        rows_by_family.get(chosen_family) or [],
        key=lambda row: (
            _row_quality(row),
            float(row.get("aggregate_score") or 0.0),
        ),
        reverse=True,
    )[:5]
    if not family_rows:
        return seed_by_family.get(chosen_family, seed_specs[0])

    chosen_row = _mixed_softmax_choice(
        [(row, _row_quality(row)) for row in family_rows],
        temperature=2.0,
        uniform_mix=0.2,
    )
    return SignalSpec.from_dict(chosen_row["spec"])


def _is_deterministic_row(row: dict[str, Any]) -> bool:
    research_summary = dict(row.get("research_summary") or {})
    run_context = dict(research_summary.get("run_context") or {})
    if "deterministic" in run_context:
        return bool(run_context.get("deterministic"))
    return str(run_context.get("phase_label") or "").strip().lower() == "burn_in"


def _spec_descriptor(payload: dict[str, Any]) -> dict[str, str]:
    features = [str(feature) for feature in list(payload.get("features") or [])]
    roles = sorted(spec_feature_roles(features))
    role_key = "+".join(roles[:4]) or "uncategorized"
    universe = list(dict(payload.get("universe") or {}).get("basis_groups") or [])
    universe_key = ",".join(str(symbol) for symbol in universe) or "no_universe"
    gate_key = "+".join(sorted(gate_dimensions(dict(payload.get("regime_gates") or {})))) or "no_gates"
    params = dict(payload.get("params") or {})
    family = str(payload.get("family") or "unknown")
    trade_style = str(params.get("trade_style") or "").strip().lower() or "unspecified"
    long_count = params.get("long_count")
    short_count = params.get("short_count")
    if long_count is not None or short_count is not None:
        book_key = f"{long_count or 0}x{short_count or 0}"
    elif supports_explicit_trade_style(family):
        book_key = trade_style
    else:
        book_key = "cross_sectional"
    return {
        "family": family,
        "universe": universe_key,
        "roles": role_key,
        "gates": gate_key,
        "book": book_key,
        "motif": motif_signature(payload),
    }


def _archive_counts(rows: list[dict[str, Any]]) -> dict[str, defaultdict[str, int]]:
    counts: dict[str, defaultdict[str, int]] = {
        "family": defaultdict(int),
        "universe": defaultdict(int),
        "roles": defaultdict(int),
        "gates": defaultdict(int),
        "book": defaultdict(int),
        "motif": defaultdict(int),
    }
    for row in rows:
        payload = dict(row.get("spec") or {})
        descriptor = _spec_descriptor(payload)
        for key, value in descriptor.items():
            counts[key][value] += 1
    return counts


def _descriptor_novelty(
    *,
    payload: dict[str, Any],
    archive_counts: dict[str, defaultdict[str, int]],
) -> float:
    descriptor = _spec_descriptor(payload)
    parts = [
        1.0 / (1.0 + float(archive_counts["family"][descriptor["family"]])),
        1.0 / (1.0 + float(archive_counts["universe"][descriptor["universe"]])),
        1.0 / (1.0 + float(archive_counts["roles"][descriptor["roles"]])),
        1.0 / (1.0 + float(archive_counts["gates"][descriptor["gates"]])),
        1.0 / (1.0 + float(archive_counts["book"][descriptor["book"]])),
        1.0 / (1.0 + float(archive_counts["motif"][descriptor["motif"]])),
    ]
    return sum(parts) / len(parts)


def _descriptor_distance(left: dict[str, Any], right: dict[str, Any]) -> float:
    left_desc = _spec_descriptor(left)
    right_desc = _spec_descriptor(right)
    keys = ("family", "universe", "roles", "gates", "book", "motif")
    mismatches = sum(1 for key in keys if left_desc[key] != right_desc[key])
    return mismatches / float(len(keys))


def _seed_prior(seed_specs: list[SignalSpec]) -> dict[str, float]:
    total = max(len(seed_specs), 1)
    priors: dict[str, float] = {}
    for index, spec in enumerate(seed_specs):
        rank = total - index
        priors[spec.strategy_hash()] = 0.25 + (0.75 * (rank / total))
    return priors


def _row_quality_by_hash(rows: list[dict[str, Any]]) -> dict[str, float]:
    best_by_hash: dict[str, float] = {}
    for row in rows:
        spec_hash = str(row.get("spec_hash") or "")
        if not spec_hash:
            continue
        score = _row_quality(row)
        current = best_by_hash.get(spec_hash)
        if current is None or score > current:
            best_by_hash[spec_hash] = score
    return best_by_hash


def _family_quality(rows: list[dict[str, Any]]) -> dict[str, float]:
    best_by_family: dict[str, float] = {}
    for row in rows:
        family = str(row.get("family") or "unknown")
        score = _row_quality(row)
        current = best_by_family.get(family)
        if current is None or score > current:
            best_by_family[family] = score
    return best_by_family


def _normalize_scores(values: dict[str, float], default: float = 0.5) -> dict[str, float]:
    if not values:
        return {}
    floor = min(values.values())
    ceiling = max(values.values())
    if abs(ceiling - floor) <= 1e-12:
        return {key: default for key in values}
    return {
        key: (value - floor) / (ceiling - floor)
        for key, value in values.items()
    }


def _deterministic_search_weights(archive_size: int) -> tuple[float, float, float]:
    if archive_size < 4:
        return 0.55, 0.25, 0.20
    if archive_size < 12:
        return 0.6, 0.25, 0.15
    return 0.7, 0.15, 0.15


def pick_deterministic_parent(
    track: str,
    ancestry: LineageStore,
    seed_specs: list[SignalSpec],
    *,
    iteration_number: int,
    run_session_id: str | None = None,
) -> SignalSpec:
    recent_rows = _recent_rows(
        ancestry,
        track,
        limit=500,
        run_session_id=run_session_id,
    )
    deterministic_rows = [row for row in recent_rows if _is_deterministic_row(row)]
    archive_counts = _archive_counts(deterministic_rows)
    row_scores = _row_quality_by_hash(deterministic_rows)
    family_scores = _family_quality(deterministic_rows)
    seed_scores = _seed_prior(seed_specs)

    spec_pool: dict[str, SignalSpec] = {
        spec.strategy_hash(): spec for spec in seed_specs
    }
    for row in deterministic_rows[:24]:
        spec = SignalSpec.from_dict(dict(row.get("spec") or {}))
        spec_pool[spec.strategy_hash()] = spec

    raw_scores: dict[str, float] = {}
    novelty_scores: dict[str, float] = {}
    for spec_hash, spec in spec_pool.items():
        payload = spec.canonical_dict()
        raw_scores[spec_hash] = max(
            row_scores.get(spec_hash, -1.0),
            family_scores.get(spec.family, -1.0) * 0.6,
            seed_scores.get(spec_hash, 0.0),
        )
        novelty_scores[spec_hash] = _descriptor_novelty(
            payload=payload,
            archive_counts=archive_counts,
        )

    exploit_scores = _normalize_scores(raw_scores, default=0.6)
    exploit_weight, novelty_weight, anchor_weight = _deterministic_search_weights(len(deterministic_rows))
    items: list[tuple[SignalSpec, float]] = []
    best_seed_hash = seed_specs[0].strategy_hash() if seed_specs else ""
    for spec_hash, spec in spec_pool.items():
        anchor_bonus = 0.0
        if spec_hash == best_seed_hash:
            anchor_bonus += 0.35
        if row_scores.get(spec_hash, -1.0) > 0.0:
            anchor_bonus += 0.2
        total = (
            exploit_weight * exploit_scores.get(spec_hash, 0.5)
            + novelty_weight * novelty_scores.get(spec_hash, 0.0)
            + anchor_weight * anchor_bonus
        )
        items.append((spec, total))

    if not items:
        return seed_specs[0]

    items.sort(key=lambda item: item[1], reverse=True)
    frontier = items[: min(3, len(items))]
    return _mixed_softmax_choice(
        frontier,
        temperature=1.0 if iteration_number < 4 else 0.85,
        uniform_mix=0.08 if iteration_number < 4 else 0.05,
    )


def rank_deterministic_specs(
    *,
    specs: list[SignalSpec],
    parent: SignalSpec,
    recent_rows: list[dict[str, Any]],
    seed_specs: list[SignalSpec],
    population_size: int,
) -> list[SignalSpec]:
    if len(specs) <= population_size:
        return list(specs)

    deterministic_rows = [row for row in recent_rows if _is_deterministic_row(row)]
    archive_counts = _archive_counts(deterministic_rows)
    row_scores = _row_quality_by_hash(deterministic_rows)
    family_scores = _family_quality(deterministic_rows)
    seed_scores = _seed_prior(seed_specs)
    best_seed_hash = seed_specs[0].strategy_hash() if seed_specs else ""
    parent_hash = parent.strategy_hash()

    raw_scores: dict[str, float] = {}
    novelty_scores: dict[str, float] = {}
    for spec in specs:
        spec_hash = spec.strategy_hash()
        payload = spec.canonical_dict()
        raw_scores[spec_hash] = max(
            row_scores.get(spec_hash, -1.0),
            family_scores.get(spec.family, -1.0) * 0.65,
            seed_scores.get(spec_hash, 0.0),
            0.1 if spec_hash == parent_hash else -1.0,
        )
        novelty_scores[spec_hash] = _descriptor_novelty(
            payload=payload,
            archive_counts=archive_counts,
        )

    exploit_scores = _normalize_scores(raw_scores, default=0.5)
    exploit_weight, novelty_weight, anchor_weight = _deterministic_search_weights(len(deterministic_rows))
    base_scores: dict[str, float] = {}
    for spec in specs:
        spec_hash = spec.strategy_hash()
        anchor_bonus = 0.0
        if spec_hash == best_seed_hash:
            anchor_bonus += 0.2
        if spec_hash == parent_hash:
            anchor_bonus += 0.1
        if spec.family == parent.family:
            anchor_bonus += 0.05
        base_scores[spec_hash] = (
            exploit_weight * exploit_scores.get(spec_hash, 0.5)
            + novelty_weight * novelty_scores.get(spec_hash, 0.0)
            + anchor_weight * anchor_bonus
        )

    selected: list[SignalSpec] = []
    remaining = list(specs)
    while remaining and len(selected) < population_size:
        scored_items: list[tuple[SignalSpec, float]] = []
        for spec in remaining:
            spec_hash = spec.strategy_hash()
            diversity_bonus = 0.0
            family_penalty = 0.0
            motif_penalty = 0.0
            if selected:
                diversity_bonus = max(
                    _descriptor_distance(spec.canonical_dict(), prior.canonical_dict())
                    for prior in selected
                )
                if any(spec.family == prior.family for prior in selected):
                    family_penalty = 0.15
                spec_motif = motif_signature(spec.canonical_dict())
                if any(spec_motif == motif_signature(prior.canonical_dict()) for prior in selected):
                    motif_penalty = 0.2
            scored_items.append(
                (
                    spec,
                    base_scores.get(spec_hash, 0.0)
                    + (0.35 * diversity_bonus)
                    - family_penalty
                    - motif_penalty,
                )
            )
        scored_items.sort(key=lambda item: item[1], reverse=True)
        spec_frontier = scored_items[: min(4, len(scored_items))]
        if selected:
            selected_families = {spec.family for spec in selected}
            diverse_frontier = [
                item for item in spec_frontier if item[0].family not in selected_families
            ]
            if diverse_frontier:
                best_diverse = diverse_frontier[0][1]
                best_total = spec_frontier[0][1]
                if best_diverse >= (best_total - 0.18):
                    spec_frontier = diverse_frontier
        choice = _mixed_softmax_choice(
            spec_frontier,
            temperature=0.95 if not selected else 0.85,
            uniform_mix=0.08,
        )
        selected.append(choice)
        choice_hash = choice.strategy_hash()
        remaining = [
            spec for spec in remaining if spec.strategy_hash() != choice_hash
        ]
    return selected

