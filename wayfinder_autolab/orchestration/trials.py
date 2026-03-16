from __future__ import annotations

import copy
from typing import Any


SCORE_COMPONENT_WEIGHTS: tuple[tuple[str, float], ...] = (
    ("median_sharpe", 1.0),
    ("median_total_return", 4.0),
    ("median_calmar", 0.5),
    ("asset_breadth", 0.1),
    ("profitable_window_pct", 0.25),
    ("worst_max_drawdown", 1.5),
)

EXTRA_DIAGNOSTIC_COMPONENTS: tuple[str, ...] = (
    "validation_total_return",
    "pre_audit_canonical_total_return",
)

REGIME_CONTEXT_PRIORITY: tuple[str, ...] = (
    "market_volatility",
    "funding_regime",
    "market_trend",
    "co_movement",
    "breadth",
    "concentration",
)

DEFAULT_GENERALIZATION_WEIGHTS: dict[str, float] = {
    "negative_validation": 10.0,
    "negative_audit": 12.0,
    "generalization_gap": 6.0,
    "audit_gap": 6.0,
    "activity_shortfall": 8.0,
}


def score_diagnosis(
    candidate_summary: dict[str, Any] | None,
    incumbent_summary: dict[str, Any] | None,
) -> dict[str, Any]:
    candidate_summary = dict(candidate_summary or {})
    incumbent_summary = dict(incumbent_summary or {})
    components: list[dict[str, Any]] = []
    biggest_lift: dict[str, Any] | None = None
    biggest_drag: dict[str, Any] | None = None

    for key, weight in SCORE_COMPONENT_WEIGHTS:
        candidate_value = _float_or_none(candidate_summary.get(key))
        incumbent_value = _float_or_none(incumbent_summary.get(key))
        delta = None
        weighted_delta = None
        helped = False
        if candidate_value is not None and incumbent_value is not None:
            delta = candidate_value - incumbent_value
            weighted_delta = delta * weight
            helped = bool(weighted_delta > 0.0)
        component = {
            "name": key,
            "weight": weight,
            "candidate": candidate_value,
            "incumbent": incumbent_value,
            "delta": delta,
            "weighted_delta": weighted_delta,
            "helped": helped,
        }
        components.append(component)
        if weighted_delta is None:
            continue
        if biggest_lift is None or float(weighted_delta) > float(biggest_lift.get("weighted_delta") or -1e18):
            biggest_lift = component
        if biggest_drag is None or float(weighted_delta) < float(biggest_drag.get("weighted_delta") or 1e18):
            biggest_drag = component

    diagnostics: list[dict[str, Any]] = []
    for key in EXTRA_DIAGNOSTIC_COMPONENTS:
        candidate_value = _float_or_none(candidate_summary.get(key))
        incumbent_value = _float_or_none(incumbent_summary.get(key))
        diagnostics.append(
            {
                "name": key,
                "candidate": candidate_value,
                "incumbent": incumbent_value,
                "delta": (
                    candidate_value - incumbent_value
                    if candidate_value is not None and incumbent_value is not None
                    else None
                ),
                "helped": (
                    bool(candidate_value - incumbent_value > 0.0)
                    if candidate_value is not None and incumbent_value is not None
                    else False
                ),
            }
        )

    aggregate_score_delta = None
    candidate_aggregate = _float_or_none(candidate_summary.get("aggregate_score"))
    incumbent_aggregate = _float_or_none(incumbent_summary.get("aggregate_score"))
    if candidate_aggregate is not None and incumbent_aggregate is not None:
        aggregate_score_delta = candidate_aggregate - incumbent_aggregate

    return {
        "aggregate_score_delta": aggregate_score_delta,
        "components": components,
        "diagnostics": diagnostics,
        "biggest_lift": _component_brief(biggest_lift),
        "biggest_drag": _component_brief(biggest_drag),
        "nearest_miss_analysis": _nearest_miss_analysis(
            aggregate_score_delta=aggregate_score_delta,
            biggest_lift=biggest_lift,
            biggest_drag=biggest_drag,
            diagnostics=diagnostics,
        ),
    }


def summarize_return_attribution(
    summary: dict[str, Any] | None,
    canonical_run: dict[str, Any] | None,
) -> dict[str, Any]:
    summary = dict(summary or {})
    canonical_run = dict(canonical_run or {})
    total_return = _float_or_none(summary.get("pre_audit_canonical_total_return"))
    if total_return is None:
        total_return = _canonical_total_return(canonical_run)

    fee_total = _metric_total(canonical_run.get("metrics_by_period"), "fee_amount")
    funding_total = _metric_total(canonical_run.get("metrics_by_period"), "funding_amount")
    decomposition_available = (
        total_return is not None and fee_total is not None and funding_total is not None
    )

    price_contribution = None
    carry_contribution = None
    tx_cost_contribution = None
    if decomposition_available:
        price_contribution = total_return + fee_total + funding_total
        carry_contribution = -funding_total
        tx_cost_contribution = -fee_total

    exposure_profile = _normalize_exposure_profile(
        dict(canonical_run.get("pre_audit_drawdown_pack") or {}).get("dominant_position_direction")
    )
    regime_pack = dict(dict(canonical_run.get("pre_audit_context_pack") or {}).get("trade_regime_pack") or {})

    return {
        "return_driver": (
            _return_driver_label(price_contribution, carry_contribution)
            if decomposition_available
            else _inferred_return_driver(
                canonical_run=canonical_run,
                exposure_profile=exposure_profile,
            )
        ),
        "return_driver_source": "decomposition" if decomposition_available else "inferred",
        "exposure_profile": exposure_profile,
        "price_contribution": price_contribution,
        "carry_contribution": carry_contribution,
        "tx_cost_contribution": tx_cost_contribution,
        "best_regime_context": _regime_context_label(regime_pack, which="best_label"),
        "worst_regime_context": _regime_context_label(regime_pack, which="worst_label"),
    }


def summarize_generalization(
    summary: dict[str, Any] | None,
    *,
    stability_pack: dict[str, Any] | None = None,
) -> dict[str, Any]:
    summary = dict(summary or {})
    stability_pack = dict(stability_pack or {})

    aggregate_score = _float_or_none(summary.get("aggregate_score"))
    validation_total_return = _float_or_none(summary.get("validation_total_return"))
    pre_audit_total_return = _float_or_none(summary.get("pre_audit_canonical_total_return"))
    audit_total_return = _float_or_none(summary.get("audit_total_return"))
    audit_available = bool(summary.get("audit_available")) if "audit_available" in summary else audit_total_return is not None
    active_bar_fraction = _float_or_none(
        summary.get("active_bar_fraction", summary.get("policy_active_bar_fraction"))
    )

    negative_validation_penalty = max(0.0, -(validation_total_return or 0.0)) * DEFAULT_GENERALIZATION_WEIGHTS["negative_validation"]
    audit_penalty = (
        max(0.0, -(audit_total_return or 0.0)) * DEFAULT_GENERALIZATION_WEIGHTS["negative_audit"]
        if audit_available and audit_total_return is not None
        else 0.0
    )
    generalization_gap = (
        max(0.0, (pre_audit_total_return or 0.0) - (validation_total_return or 0.0))
        if pre_audit_total_return is not None and validation_total_return is not None
        else 0.0
    )
    generalization_gap_penalty = generalization_gap * DEFAULT_GENERALIZATION_WEIGHTS["generalization_gap"]
    audit_gap = (
        max(0.0, (validation_total_return or 0.0) - (audit_total_return or 0.0))
        if audit_available and validation_total_return is not None and audit_total_return is not None
        else 0.0
    )
    audit_gap_penalty = audit_gap * DEFAULT_GENERALIZATION_WEIGHTS["audit_gap"]
    activity_shortfall = (
        max(0.0, 0.15 - active_bar_fraction)
        if active_bar_fraction is not None
        else 0.0
    )
    activity_penalty = activity_shortfall * DEFAULT_GENERALIZATION_WEIGHTS["activity_shortfall"]
    stability_penalty = _float_or_none(stability_pack.get("stability_penalty")) or 0.0

    fragility_penalty = (
        negative_validation_penalty
        + audit_penalty
        + generalization_gap_penalty
        + audit_gap_penalty
        + activity_penalty
        + stability_penalty
    )
    promotion_score = (
        aggregate_score - fragility_penalty
        if aggregate_score is not None
        else None
    )
    audit_alignment = _audit_alignment_label(
        validation_total_return=validation_total_return,
        audit_total_return=audit_total_return,
        audit_available=audit_available,
    )
    fragility_label = _fragility_label(
        fragility_penalty=fragility_penalty,
        stability_pack=stability_pack,
        audit_alignment=audit_alignment,
        audit_available=audit_available,
    )

    return {
        "fragility_penalty": fragility_penalty,
        "promotion_score": promotion_score,
        "audit_alignment": audit_alignment,
        "fragility_label": fragility_label,
        "fragility_pack": {
            "negative_validation_penalty": negative_validation_penalty,
            "audit_penalty": audit_penalty,
            "generalization_gap": generalization_gap,
            "generalization_gap_penalty": generalization_gap_penalty,
            "audit_gap": audit_gap,
            "audit_gap_penalty": audit_gap_penalty,
            "activity_shortfall": activity_shortfall,
            "activity_penalty": activity_penalty,
            "stability_penalty": stability_penalty,
            "weights": dict(DEFAULT_GENERALIZATION_WEIGHTS),
            "active_bar_fraction": active_bar_fraction,
            "validation_total_return": validation_total_return,
            "pre_audit_canonical_total_return": pre_audit_total_return,
            "audit_total_return": audit_total_return if audit_available else None,
        },
        "stability_pack": stability_pack or {},
    }


def promotion_rank(
    summary: dict[str, Any] | None,
    trial_context: dict[str, Any] | None,
) -> tuple[float, float, float, float]:
    summary = dict(summary or {})
    trial_context = dict(trial_context or {})
    return (
        _float_or_none(trial_context.get("promotion_score")) or -1e18,
        _float_or_none(summary.get("aggregate_score")) or -1e18,
        _float_or_none(summary.get("validation_total_return")) or -1e18,
        _float_or_none(summary.get("pre_audit_canonical_total_return")) or -1e18,
    )


def build_candidate_patch(
    *,
    base_payload: dict[str, Any],
    target_payload: dict[str, Any],
) -> dict[str, Any]:
    changes: list[dict[str, Any]] = []
    _diff_payloads(base_payload, target_payload, prefix="", changes=changes)
    return {
        "base_candidate_hash": _candidate_hash(base_payload),
        "target_candidate_hash": _candidate_hash(target_payload),
        "change_count": len(changes),
        "changes": changes,
    }


def summarize_patch(patch: dict[str, Any], *, limit: int = 8) -> list[str]:
    lines: list[str] = []
    for change in list(patch.get("changes") or [])[:limit]:
        path = str(change.get("path") or "")
        old_value = _format_patch_value(change.get("old"))
        new_value = _format_patch_value(change.get("new"))
        lines.append(f"{path}: {old_value} -> {new_value}")
    if len(list(patch.get("changes") or [])) > limit:
        lines.append(f"... {len(list(patch.get('changes') or [])) - limit} more changes")
    return lines


def clone_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(dict(payload))


def apply_path_value(payload: dict[str, Any], path: str, value: Any) -> dict[str, Any]:
    target = payload
    parts = _path_parts(path)
    for index, part in enumerate(parts):
        is_last = index == len(parts) - 1
        if isinstance(part, str):
            if is_last:
                target[part] = value
                return payload
            next_part = parts[index + 1]
            if part not in target or not isinstance(target[part], (dict, list)):
                target[part] = [] if isinstance(next_part, int) else {}
            target = target[part]
            continue
        while len(target) <= part:
            target.append({})
        if is_last:
            target[part] = value
            return payload
        next_part = parts[index + 1]
        if not isinstance(target[part], (dict, list)):
            target[part] = [] if isinstance(next_part, int) else {}
        target = target[part]
    return payload


def get_path_value(payload: dict[str, Any], path: str) -> Any:
    current: Any = payload
    for part in _path_parts(path):
        if isinstance(part, str):
            if not isinstance(current, dict):
                return None
            current = current.get(part)
            continue
        if not isinstance(current, list) or part >= len(current):
            return None
        current = current[part]
    return current


def _diff_payloads(
    left: Any,
    right: Any,
    *,
    prefix: str,
    changes: list[dict[str, Any]],
) -> None:
    if isinstance(left, dict) and isinstance(right, dict):
        for key in sorted(set(left) | set(right)):
            path = f"{prefix}.{key}" if prefix else str(key)
            _diff_payloads(left.get(key), right.get(key), prefix=path, changes=changes)
        return
    if isinstance(left, list) and isinstance(right, list):
        if left != right:
            changes.append({"path": prefix, "old": left, "new": right})
        return
    if left != right:
        changes.append({"path": prefix, "old": left, "new": right})


def _candidate_hash(payload: dict[str, Any]) -> str | None:
    try:
        from wayfinder_autolab.models import CandidateGraph

        return CandidateGraph.from_dict(payload).strategy_hash()
    except Exception:
        return None


def _format_patch_value(value: Any) -> str:
    if isinstance(value, float):
        return format(value, ".6f").rstrip("0").rstrip(".") or "0"
    return str(value)


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _component_brief(component: dict[str, Any] | None) -> dict[str, Any] | None:
    if component is None:
        return None
    return {
        "name": component.get("name"),
        "delta": component.get("delta"),
        "weighted_delta": component.get("weighted_delta"),
        "helped": component.get("helped"),
    }


def _nearest_miss_analysis(
    *,
    aggregate_score_delta: float | None,
    biggest_lift: dict[str, Any] | None,
    biggest_drag: dict[str, Any] | None,
    diagnostics: list[dict[str, Any]],
) -> str:
    validation_delta = next(
        (
            item.get("delta")
            for item in diagnostics
            if str(item.get("name") or "") == "validation_total_return"
        ),
        None,
    )
    pre_audit_delta = next(
        (
            item.get("delta")
            for item in diagnostics
            if str(item.get("name") or "") == "pre_audit_canonical_total_return"
        ),
        None,
    )
    if aggregate_score_delta is None:
        return "No incumbent score available for comparison."
    if aggregate_score_delta >= 0.0:
        lift_name = str((biggest_lift or {}).get("name") or "unknown")
        return f"Beat the incumbent; biggest lift came from `{lift_name}`."
    drag_name = str((biggest_drag or {}).get("name") or "unknown")
    lift_name = str((biggest_lift or {}).get("name") or "unknown")
    if aggregate_score_delta > -0.5:
        return (
            f"Nearest miss: overall score was slightly worse, mostly dragged by `{drag_name}` "
            f"while `{lift_name}` improved. Validation {_delta_direction(validation_delta)}; "
            f"pre-audit {_delta_direction(pre_audit_delta)}."
        )
    return (
        f"Missed materially: `{drag_name}` outweighed gains from `{lift_name}`. "
        f"Validation {_delta_direction(validation_delta)}; pre-audit {_delta_direction(pre_audit_delta)}."
    )


def _fmt_delta(value: Any) -> str:
    numeric = _float_or_none(value)
    if numeric is None:
        return "n/a"
    sign = "+" if numeric >= 0.0 else ""
    return f"{sign}{numeric:.4f}"


def _delta_direction(value: Any) -> str:
    numeric = _float_or_none(value)
    if numeric is None:
        return "was unavailable"
    if numeric > 0.0:
        return "improved"
    if numeric < 0.0:
        return "worsened"
    return "was flat"


def _canonical_total_return(canonical_run: dict[str, Any]) -> float | None:
    metrics_total = _metric_total(dict(canonical_run or {}).get("metrics_by_period"), "equity", use_last=True)
    if metrics_total is not None:
        return metrics_total - 1.0
    equity_curve = dict(dict(canonical_run or {}).get("equity_curve") or {})
    values = list(equity_curve.get("values") or [])
    if not values:
        return None
    first = _float_or_none(values[0])
    last = _float_or_none(values[-1])
    if first in (None, 0.0) or last is None:
        return None
    return (last / first) - 1.0


def _metric_total(frame: Any, column_name: str, *, use_last: bool = False) -> float | None:
    if not isinstance(frame, dict):
        return None
    columns = list(frame.get("columns") or [])
    rows = list(frame.get("rows") or [])
    if column_name not in columns or not rows:
        return None
    index = columns.index(column_name)
    values: list[float] = []
    for row in rows:
        if not isinstance(row, list) or index >= len(row):
            continue
        value = _float_or_none(row[index])
        if value is not None:
            values.append(value)
    if not values:
        return None
    return values[-1] if use_last else sum(values)


def _return_driver_label(
    price_contribution: float | None,
    carry_contribution: float | None,
) -> str:
    price_abs = abs(_float_or_none(price_contribution) or 0.0)
    carry_abs = abs(_float_or_none(carry_contribution) or 0.0)
    if price_abs < 1e-9 and carry_abs < 1e-9:
        return "mixed"
    if price_abs > carry_abs * 1.5:
        return "price_dominant"
    if carry_abs > price_abs * 1.5:
        return "carry_dominant"
    return "mixed"


def _inferred_return_driver(
    *,
    canonical_run: dict[str, Any],
    exposure_profile: str | None,
) -> str:
    if exposure_profile in {"net_long", "net_short"}:
        return "price_dominant"

    try:
        from wayfinder_autolab.orchestration.contracts import feature_roles_for_formula
    except Exception:  # noqa: BLE001
        feature_roles_for_formula = None

    contributors = list(
        dict(canonical_run.get("pre_audit_drawdown_pack") or {}).get("top_feature_contributors") or []
    )
    price_roles = 0
    carry_roles = 0
    for payload in contributors[:4]:
        feature = str((payload or {}).get("feature") or "").strip()
        if not feature:
            continue
        roles = set()
        if feature_roles_for_formula is not None:
            try:
                roles = set(feature_roles_for_formula(feature))
            except Exception:  # noqa: BLE001
                roles = set()
        if "core_carry" in roles or "carry_term_structure" in roles or "funding" in roles:
            carry_roles += 1
        if "trend_or_momentum" in roles or any(
            token in feature.lower()
            for token in ("price_", "trend_", "momentum", "ema_", "macd", "rsi", "donchian")
        ):
            price_roles += 1

    if carry_roles > price_roles + 1:
        return "carry_dominant"
    if price_roles > carry_roles:
        return "price_dominant"
    if exposure_profile == "market_neutral":
        return "carry_dominant"
    return "mixed"


def _normalize_exposure_profile(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    if text in {"net_long", "market_neutral", "net_short"}:
        return text
    if "long" in text and "short" not in text:
        return "net_long"
    if "short" in text and "long" not in text:
        return "net_short"
    if "neutral" in text:
        return "market_neutral"
    return "mixed"


def _regime_context_label(regime_pack: dict[str, Any], *, which: str) -> str | None:
    for dimension in REGIME_CONTEXT_PRIORITY:
        payload = dict(regime_pack.get(dimension) or {})
        label = str(payload.get(which) or "").strip()
        if label:
            return f"{dimension}/{label}"
    for dimension, raw_payload in regime_pack.items():
        payload = dict(raw_payload or {})
        label = str(payload.get(which) or "").strip()
        if label:
            return f"{dimension}/{label}"
    return None


def _audit_alignment_label(
    *,
    validation_total_return: float | None,
    audit_total_return: float | None,
    audit_available: bool,
) -> str:
    if not audit_available:
        return "not_run"
    if audit_total_return is None:
        return "not_run"
    if audit_total_return < 0.0:
        return "negative"
    if validation_total_return is None:
        return "aligned"
    if (
        validation_total_return == 0.0 and audit_total_return == 0.0
    ) or (
        validation_total_return > 0.0 and audit_total_return > 0.0
    ) or (
        validation_total_return < 0.0 and audit_total_return < 0.0
    ):
        if abs(validation_total_return - audit_total_return) <= 0.03:
            return "aligned"
    return "mismatch"


def _fragility_label(
    *,
    fragility_penalty: float,
    stability_pack: dict[str, Any],
    audit_alignment: str,
    audit_available: bool,
) -> str:
    if not audit_available and not stability_pack:
        return "untested"
    if audit_alignment in {"negative", "mismatch"}:
        return "fragile"
    if stability_pack and (
        str(stability_pack.get("status") or "") != "ok"
        or (_float_or_none(stability_pack.get("passed_fraction")) or 0.0) < 1.0
    ):
        return "fragile"
    if fragility_penalty >= 1.5:
        return "fragile"
    return "stable"


def _path_parts(path: str) -> list[str | int]:
    tokens: list[str | int] = []
    buffer = ""
    index_buffer = ""
    in_index = False
    for char in path:
        if char == "." and not in_index:
            if buffer:
                tokens.append(buffer)
                buffer = ""
            continue
        if char == "[":
            if buffer:
                tokens.append(buffer)
                buffer = ""
            in_index = True
            index_buffer = ""
            continue
        if char == "]":
            if index_buffer:
                tokens.append(int(index_buffer))
            in_index = False
            index_buffer = ""
            continue
        if in_index:
            index_buffer += char
        else:
            buffer += char
    if buffer:
        tokens.append(buffer)
    return tokens
