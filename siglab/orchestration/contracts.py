from __future__ import annotations

import importlib
import re
from dataclasses import dataclass
from typing import Any, TypedDict, cast

from siglab.evaluation.strategy_semantics import (
    MOMENTUM_KEYWORDS,
    NON_REGIME_ROLES,
    spec_feature_roles,
    dict_or_empty,
    feature_roles_for_formula,
    gate_dimensions,
    motif_signature,
    normalized_gate_entries,
    supports_explicit_trade_style,
)

__all__ = [
    "feature_roles_for_formula",
    "motif_signature",
    "MOMENTUM_KEYWORDS",
    "NON_REGIME_ROLES",
    "spec_feature_roles",
    "dict_or_empty",
    "gate_dimensions",
    "normalized_gate_entries",
    "supports_explicit_trade_style",
]

# ---------------------------------------------------------------------------
# Typed orchestration contracts — replace untyped dict[str, Any] pipeline
# ---------------------------------------------------------------------------


class PlannerOutput(TypedDict, total=False):
    """Contract dict produced by the planner stage for downstream consumers.

    All fields are optional (total=False) because the contract is built
    incrementally during extraction.  Downstream consumers should treat
    missing keys as absent intent, not as errors.
    """
    decision: str
    search_mode: str
    target_family: str
    target_trade_style: str | None
    target_universe: list[str]
    core_hypothesis: str
    informative_test: str
    expected_success: list[str]
    expected_failure: list[str]
    evidence_paths: list[str]
    tools_used: list[str]
    tracking_tags: list[str]
    must_answer: str
    required_feature_roles: list[str]
    required_features: list[str]
    forbidden_features: list[str]
    forbidden_motifs: list[str]
    gate_intent: dict[str, Any]
    required_gate_dimensions: list[str]
    required_variation_axis: str | None
    banned_motif_signatures: list[str]
    writer_inputs: list[str]
    planner_regime_gates: dict[str, Any]


class WriterOutput(TypedDict, total=False):
    """Output dict from the writer stage.

    Represents the structured result from SpecWriterRunner.run()
    suitable for serialisation or passing to downstream stages.
    """
    spec_payload: dict[str, Any] | None
    spec_path: str | None
    trace_path: str
    accepted: bool
    base_spec_payload: dict[str, Any] | None
    base_spec_path: str | None
    structure_spec: dict[str, Any] | None
    patch_payload: dict[str, Any] | None
    patch_summary: list[str] | None
    spec_after_patch_path: str | None
    failure_reason: str | None
    failure_packet: dict[str, Any] | None


class OptimizerOutput(TypedDict, total=False):
    """Output dict from the optimizer stage.

    Carries the best spec found by Optuna along with trial metadata
    and stability analysis.
    """
    spec_payload: dict[str, Any]
    best_summary: dict[str, Any]
    best_params: dict[str, Any]
    optuna_space: dict[str, Any]
    score_diagnosis: dict[str, Any]
    trial_count: int
    objective_value: float
    fragility_penalty: float
    deployment_score: float | None
    fragility_pack: dict[str, Any]
    stability_pack: dict[str, Any]


class ReflectorOutput(TypedDict, total=False):
    """Output dict from the reflector stage.

    Contains the saved lesson card location and parsed frontmatter
    for downstream traceability.
    """
    lesson_card_path: str
    trace_path: str
    frontmatter: dict[str, Any]


@dataclass
class PreflightResult:
    """Validated result from the writer preflight check.

    Typed as a dataclass so that computed properties (`material_drift`,
    `acceptable`) remain available.
    """
    parse_error: str | None
    hard_issues: list[str]
    conformance_issues: list[str]
    gate_lint: dict[str, Any] | None
    changed_fields: list[str]
    harmless_changed_fields: list[str]
    material_changed_fields: list[str]
    validated_payload: dict[str, Any] | None

    @property
    def material_drift(self) -> bool:
        return bool(self.material_changed_fields)

    @property
    def acceptable(self) -> bool:
        return (
            self.parse_error is None
            and not self.hard_issues
            and not self.conformance_issues
            and not self.material_drift
            and self.validated_payload is not None
        )


# ---------------------------------------------------------------------------
# Helper functions used across orchestration modules
# ---------------------------------------------------------------------------


def _numeric_equal(left: Any, right: Any) -> bool:
    try:
        return bool(abs(float(left) - float(right)) <= 1e-12)
    except (TypeError, ValueError):
        return bool(left == right)


def has_non_regime_variation(
    *,
    spec_payload: dict[str, Any],
    parent_payload: dict[str, Any] | None = None,
) -> bool:
    features = [str(feature) for feature in list(spec_payload.get("features") or [])]
    roles = spec_feature_roles(features)
    if any(role in roles for role in NON_REGIME_ROLES):
        return True
    if parent_payload is None:
        return False
    parent_features = [str(feature) for feature in list(parent_payload.get("features") or [])]
    spec_feature_set = {feature.lower() for feature in features}
    parent_feature_set = {feature.lower() for feature in parent_features}
    added_features = spec_feature_set - parent_feature_set
    if added_features:
        for feature in added_features:
            feature_roles = feature_roles_for_formula(feature)
            if any(role in feature_roles for role in NON_REGIME_ROLES):
                return True
    parent_params = dict(parent_payload.get("params") or {})
    spec_params = dict(spec_payload.get("params") or {})
    param_keys = ["long_count", "short_count", "gross_target"]
    family = str(spec_payload.get("family") or parent_payload.get("family") or "")
    if supports_explicit_trade_style(family):
        param_keys.append("trade_style")
    for key in param_keys:
        if parent_params.get(key) != spec_params.get(key):
            return True
    parent_universe = dict(parent_payload.get("universe") or {})
    spec_universe = dict(spec_payload.get("universe") or {})
    if list(parent_universe.get("basis_groups") or []) != list(spec_universe.get("basis_groups") or []):
        return True
    return False


def has_policy_variation(
    *,
    spec_payload: dict[str, Any],
    parent_payload: dict[str, Any] | None = None,
) -> bool:
    if parent_payload is None:
        return False
    parent_params = dict(parent_payload.get("params") or {})
    spec_params = dict(spec_payload.get("params") or {})
    for key in [
        "entry_abs_score",
        "exit_abs_score",
        "flip_abs_score",
        "max_holding_bars",
        "cooldown_bars",
        "min_abs_score",
    ]:
        if key in spec_params and spec_params.get(key) != parent_params.get(key):
            return True
    return False


def conformance_violations(
    *,
    planner_contract: PlannerOutput,
    spec_payload: dict[str, Any],
    allowed_features: list[str] | None = None,
    parent_payload: dict[str, Any] | None = None,
) -> list[str]:
    violations: list[str] = []
    family = str(spec_payload.get("family") or "")
    target_family = str(planner_contract.get("target_family") or "").strip()
    if target_family and family != target_family:
        violations.append(
            f"family mismatch: expected `{target_family}`, got `{family}`"
        )

    target_trade_style = str(planner_contract.get("target_trade_style") or "").strip()
    trade_style_family = target_family or family
    if target_trade_style and supports_explicit_trade_style(trade_style_family):
        actual_trade_style = str(dict(spec_payload.get("params") or {}).get("trade_style") or "").strip()
        if actual_trade_style != target_trade_style:
            violations.append(
                f"trade_style mismatch: expected `{target_trade_style}`, got `{actual_trade_style or 'unspecified'}`"
            )

    feature_values = [str(feature) for feature in list(spec_payload.get("features") or [])]
    feature_values_lower = {feature.lower() for feature in feature_values}
    roles = spec_feature_roles(feature_values)
    for requirement in list(planner_contract.get("required_feature_roles") or []):
        spec = str(requirement).strip().lower()
        if not spec:
            continue
        if "core_carry" in spec and "core_carry" not in roles:
            violations.append("missing required feature role: core_carry")
        elif "orthogonal_regime" in spec and "orthogonal_regime" not in roles:
            violations.append("missing required feature role: orthogonal_regime")
        elif "spread_or_residual" in spec and "spread_or_residual" not in roles:
            violations.append("missing required feature role: spread_or_residual")
        elif "cross_sectional_core" in spec and "cross_sectional_core" not in roles:
            violations.append("missing required feature role: cross_sectional_core")
        elif "trend_or_momentum" in spec and "trend_or_momentum" not in roles:
            violations.append("missing required feature role: trend_or_momentum")
        elif "non_regime_axis" in spec and not any(role in roles for role in NON_REGIME_ROLES):
            violations.append("missing required feature role: non_regime_axis")

    allowed = {str(feature).lower(): str(feature) for feature in list(allowed_features or [])}
    required_features = [
        allowed.get(str(feature).strip().lower(), str(feature).strip())
        for feature in list(planner_contract.get("required_features") or [])
        if str(feature).strip()
    ]
    for feature in required_features:
        if feature.lower() not in feature_values_lower:
            violations.append(f"missing required named feature: `{feature}`")

    for feature in list(planner_contract.get("forbidden_features") or []):
        feature_text = str(feature).strip().lower()
        if feature_text and feature_text in feature_values_lower:
            violations.append(f"forbidden feature repeated: `{feature}`")

    features = [str(feature or "").lower() for feature in list(spec_payload.get("features") or [])]
    trend_feature_count = sum(
        1
        for feature in features
        if any(keyword in feature for keyword in MOMENTUM_KEYWORDS + ("trend_strength",))
    )
    for motif in list(planner_contract.get("forbidden_motifs") or []):
        motif_text = str(motif).strip().lower()
        if motif_text == "second pure trend overlay" and trend_feature_count >= 2:
            violations.append("forbidden_motif violated: second pure trend overlay")

    gate_intent = dict_or_empty(planner_contract.get("gate_intent"))
    target_dimension = str(gate_intent.get("target_dimension") or "").strip().lower()
    gate_type = str(gate_intent.get("type") or "").strip().lower()
    gate_dims = [dimension.lower() for dimension in gate_dimensions(dict_or_empty(spec_payload.get("regime_gates")))]
    required_gate_dimensions = [
        str(value).strip().lower()
        for value in list(planner_contract.get("required_gate_dimensions") or [])
        if str(value).strip()
    ]
    if target_dimension and target_dimension not in required_gate_dimensions:
        required_gate_dimensions.append(target_dimension)
    if gate_type.startswith("suppress") and target_dimension:
        if not any(target_dimension in dimension for dimension in gate_dims):
            violations.append(
                f"spec does not implement gate intent on `{target_dimension}`"
            )
    for dimension in required_gate_dimensions:
        if not any(dimension in gate_dim for gate_dim in gate_dims):
            violations.append(f"spec does not implement required gate dimension `{dimension}`")

    planner_regime_gates = dict_or_empty(planner_contract.get("planner_regime_gates"))
    expected_gate_entries = normalized_gate_entries(planner_regime_gates)
    actual_gate_entries = normalized_gate_entries(dict_or_empty(spec_payload.get("regime_gates")))
    for expected_gate in expected_gate_entries:
        expression = str(expected_gate.get("expression") or "")
        matches = [
            gate for gate in actual_gate_entries if str(gate.get("expression") or "") == expression
        ]
        if not matches:
            violations.append(f"missing planner-provided gate spec for `{expression}`")
            continue
        expected_min = expected_gate.get("min")
        expected_max = expected_gate.get("max")
        if expected_min is None and expected_max is None:
            continue
        exact_match = False
        for gate in matches:
            min_ok = expected_min is None or _numeric_equal(gate.get("min"), expected_min)
            max_ok = expected_max is None or _numeric_equal(gate.get("max"), expected_max)
            if min_ok and max_ok:
                exact_match = True
                break
        if exact_match:
            continue
        expected_parts: list[str] = []
        actual_parts: list[str] = []
        if expected_min is not None:
            expected_parts.append(f"min={expected_min}")
        if expected_max is not None:
            expected_parts.append(f"max={expected_max}")
        first_match = matches[0]
        if first_match.get("min") is not None:
            actual_parts.append(f"min={first_match.get('min')}")
        if first_match.get("max") is not None:
            actual_parts.append(f"max={first_match.get('max')}")
        violations.append(
            f"planner-provided gate spec changed for `{expression}`: expected "
            f"{', '.join(expected_parts) or 'expression only'}, got {', '.join(actual_parts) or 'expression only'}"
        )

    required_variation_axis = str(planner_contract.get("required_variation_axis") or "").strip().lower()
    if required_variation_axis == "non_regime" and not has_non_regime_variation(
        spec_payload=spec_payload,
        parent_payload=parent_payload,
    ):
        violations.append("spec does not include the required non-regime axis of variation")
    if required_variation_axis in {"policy", "policy_control", "persistence"} and not has_policy_variation(
        spec_payload=spec_payload,
        parent_payload=parent_payload,
    ):
        violations.append("spec does not include the required policy/persistence axis of variation")

    banned_motif_signatures = [
        str(value).strip()
        for value in list(planner_contract.get("banned_motif_signatures") or [])
        if str(value).strip()
    ]
    current_motif = motif_signature(spec_payload)
    if current_motif in banned_motif_signatures:
        violations.append(f"spec repeats banned failed motif `{current_motif}`")

    return violations


def extract_embedded_yaml_block(text: str) -> dict[str, Any]:
    match = re.search(r"```yaml\s*\n(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if not match:
        return {}
    blob = match.group(1).strip()
    if blob.startswith("---"):
        blob = blob[3:].lstrip()
    if blob.endswith("---"):
        blob = blob[:-3].rstrip()
    try:
        yaml_module = cast(Any, importlib.import_module("yaml"))
    except ImportError:
        return {}
    try:
        parsed = yaml_module.safe_load(blob) or {}
    except getattr(yaml_module, "YAMLError", ValueError):
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}
