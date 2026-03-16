from __future__ import annotations

import re
from typing import Any


REGIME_KEYWORDS = (
    "trend_strength",
    "trend_efficiency",
    "market_volatility",
    "volatility",
    "co_movement",
    "breadth",
    "corr",
    "correlation",
    "dispersion",
    "funding_dispersion",
    "funding_level",
)

NON_REGIME_ROLES = (
    "carry_term_structure",
    "cross_sectional_core",
    "trend_or_momentum",
    "spread_or_residual",
)

MOMENTUM_KEYWORDS = (
    "momentum",
    "return",
    "ema",
    "macd",
    "rsi",
    "breakout",
)

RESIDUAL_KEYWORDS = (
    "residual",
    "kalman",
    "pair_ratio",
    "log_spread",
    "bollinger",
    "z_",
    "zscore",
    "autocorr",
    "half_life",
    "hurst",
)


def _dict_or_empty(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def feature_roles_for_formula(feature: str) -> set[str]:
    text = str(feature or "").lower()
    roles: set[str] = set()
    if any(keyword in text for keyword in ("funding", "carry")):
        roles.add("core_carry")
        roles.add("funding")
    if any(keyword in text for keyword in ("term_structure", "decay")):
        roles.add("carry_term_structure")
    if any(keyword in text for keyword in REGIME_KEYWORDS):
        roles.add("orthogonal_regime")
    if any(keyword in text for keyword in MOMENTUM_KEYWORDS):
        roles.add("trend_or_momentum")
    if any(keyword in text for keyword in RESIDUAL_KEYWORDS):
        roles.add("spread_or_residual")
    if text.startswith("pair_") or "asset_1_" in text or "asset_2_" in text:
        roles.add("pair_state")
    if "relative_" in text or "breadth_adjusted_" in text:
        roles.add("cross_sectional_core")
    return roles


def candidate_feature_roles(features: list[str]) -> set[str]:
    roles: set[str] = set()
    for feature in features:
        roles.update(feature_roles_for_formula(feature))
    return roles


def gate_dimensions(regime_gates: dict[str, Any] | None) -> list[str]:
    dimensions: list[str] = []
    for gate in list(_dict_or_empty(regime_gates).get("entry") or []):
        expression = ""
        if isinstance(gate, dict):
            expression = str(gate.get("expression") or "")
        elif isinstance(gate, str):
            expression = gate
        if not expression:
            continue
        dimension = expression.split("(", 1)[0] if "(" not in expression else expression
        if "(" in expression:
            inner = expression.split("(", 1)[1].split(",", 1)[0].split(")", 1)[0]
            dimension = inner.strip() or expression
        dimensions.append(dimension.strip())
    return [dimension for dimension in dimensions if dimension]


def _normalized_gate_entries(regime_gates: dict[str, Any] | None) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for gate in list(_dict_or_empty(regime_gates).get("entry") or []):
        if isinstance(gate, str):
            expression = gate.strip()
            if expression:
                entries.append({"expression": expression, "kind": "string"})
            continue
        if not isinstance(gate, dict):
            continue
        expression = str(gate.get("expression") or "").strip()
        if not expression:
            continue
        normalized: dict[str, Any] = {"expression": expression, "kind": "dict"}
        if gate.get("min") is not None:
            normalized["min"] = gate.get("min")
        if gate.get("max") is not None:
            normalized["max"] = gate.get("max")
        entries.append(normalized)
    return entries


def _numeric_equal(left: Any, right: Any) -> bool:
    try:
        return abs(float(left) - float(right)) <= 1e-12
    except (TypeError, ValueError):
        return left == right


def motif_signature(payload: dict[str, Any]) -> str:
    family = str(payload.get("family") or "")
    params = dict(payload.get("params") or {})
    trade_style = str(params.get("trade_style") or "unspecified")
    features = [str(feature) for feature in list(payload.get("features") or [])]
    roles = sorted(candidate_feature_roles(features))
    gate_dims = sorted(gate_dimensions(dict(payload.get("regime_gates") or {})))
    role_head = "+".join(roles[:4]) or "uncategorized"
    gate_head = "+".join(gate_dims[:3]) or "no_gates"
    return f"{family}|{trade_style}|{role_head}|{gate_head}"


def has_non_regime_variation(
    *,
    candidate_payload: dict[str, Any],
    parent_payload: dict[str, Any] | None = None,
) -> bool:
    features = [str(feature) for feature in list(candidate_payload.get("features") or [])]
    roles = candidate_feature_roles(features)
    if any(role in roles for role in NON_REGIME_ROLES):
        return True
    if parent_payload is None:
        return False
    parent_features = [str(feature) for feature in list(parent_payload.get("features") or [])]
    candidate_feature_set = {feature.lower() for feature in features}
    parent_feature_set = {feature.lower() for feature in parent_features}
    added_features = candidate_feature_set - parent_feature_set
    if added_features:
        for feature in added_features:
            feature_roles = feature_roles_for_formula(feature)
            if any(role in feature_roles for role in NON_REGIME_ROLES):
                return True
    parent_params = dict(parent_payload.get("params") or {})
    candidate_params = dict(candidate_payload.get("params") or {})
    for key in ("long_count", "short_count", "gross_target", "trade_style"):
        if parent_params.get(key) != candidate_params.get(key):
            return True
    parent_universe = dict(parent_payload.get("universe") or {})
    candidate_universe = dict(candidate_payload.get("universe") or {})
    if list(parent_universe.get("basis_groups") or []) != list(candidate_universe.get("basis_groups") or []):
        return True
    return False


def _contract_feature_mentions(
    *,
    planner_contract: dict[str, Any],
    allowed_features: list[str] | None,
) -> list[str]:
    allowed = {str(feature).lower(): str(feature) for feature in list(allowed_features or [])}
    if not allowed:
        return []
    required = [str(feature) for feature in list(planner_contract.get("required_features") or []) if str(feature).strip()]
    if required:
        return required
    text_parts = [
        str(planner_contract.get("must_answer") or ""),
        str(planner_contract.get("core_hypothesis") or ""),
        str(planner_contract.get("informative_test") or ""),
    ]
    mentions: list[str] = []
    for token in re.findall(r"\b[a-z][a-z0-9_]{2,}\b", " ".join(text_parts).lower()):
        if token not in allowed:
            continue
        resolved = allowed[token]
        if resolved not in mentions:
            mentions.append(resolved)
    return mentions if len(mentions) == 1 else []


def conformance_violations(
    *,
    planner_contract: dict[str, Any],
    candidate_payload: dict[str, Any],
    allowed_features: list[str] | None = None,
    parent_payload: dict[str, Any] | None = None,
) -> list[str]:
    violations: list[str] = []
    family = str(candidate_payload.get("family") or "")
    target_family = str(planner_contract.get("target_family") or "").strip()
    if target_family and family != target_family:
        violations.append(
            f"family mismatch: expected `{target_family}`, got `{family}`"
        )

    target_trade_style = str(planner_contract.get("target_trade_style") or "").strip()
    if target_trade_style:
        actual_trade_style = str(dict(candidate_payload.get("params") or {}).get("trade_style") or "").strip()
        if actual_trade_style != target_trade_style:
            violations.append(
                f"trade_style mismatch: expected `{target_trade_style}`, got `{actual_trade_style or 'unspecified'}`"
            )

    feature_values = [str(feature) for feature in list(candidate_payload.get("features") or [])]
    feature_values_lower = {feature.lower() for feature in feature_values}
    roles = candidate_feature_roles(feature_values)
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

    required_features = _contract_feature_mentions(
        planner_contract=planner_contract,
        allowed_features=allowed_features,
    )
    for feature in required_features:
        if feature.lower() not in feature_values_lower:
            violations.append(f"missing required named feature: `{feature}`")

    for feature in list(planner_contract.get("forbidden_features") or []):
        feature_text = str(feature).strip().lower()
        if feature_text and feature_text in feature_values_lower:
            violations.append(f"forbidden feature repeated: `{feature}`")

    features = [str(feature or "").lower() for feature in list(candidate_payload.get("features") or [])]
    trend_feature_count = sum(
        1
        for feature in features
        if any(keyword in feature for keyword in MOMENTUM_KEYWORDS + ("trend_strength",))
    )
    for motif in list(planner_contract.get("forbidden_motifs") or []):
        motif_text = str(motif).strip().lower()
        if motif_text == "second pure trend overlay" and trend_feature_count >= 2:
            violations.append("forbidden_motif violated: second pure trend overlay")

    gate_intent = _dict_or_empty(planner_contract.get("gate_intent"))
    target_dimension = str(gate_intent.get("target_dimension") or "").strip().lower()
    gate_type = str(gate_intent.get("type") or "").strip().lower()
    gate_dims = [dimension.lower() for dimension in gate_dimensions(_dict_or_empty(candidate_payload.get("regime_gates")))]
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
                f"candidate does not implement gate intent on `{target_dimension}`"
            )
    for dimension in required_gate_dimensions:
        if not any(dimension in gate_dim for gate_dim in gate_dims):
            violations.append(f"candidate does not implement required gate dimension `{dimension}`")

    planner_regime_gates = _dict_or_empty(planner_contract.get("planner_regime_gates"))
    expected_gate_entries = _normalized_gate_entries(planner_regime_gates)
    actual_gate_entries = _normalized_gate_entries(_dict_or_empty(candidate_payload.get("regime_gates")))
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
        candidate_payload=candidate_payload,
        parent_payload=parent_payload,
    ):
        violations.append("candidate does not include the required non-regime axis of variation")

    banned_motif_signatures = [
        str(value).strip()
        for value in list(planner_contract.get("banned_motif_signatures") or [])
        if str(value).strip()
    ]
    current_motif = motif_signature(candidate_payload)
    if current_motif in banned_motif_signatures:
        violations.append(f"candidate repeats banned failed motif `{current_motif}`")

    must_answer = str(planner_contract.get("must_answer") or "").strip().lower()
    if must_answer:
        if "return to `perp_multi_asset_carry`" in must_answer and family != "perp_multi_asset_carry":
            violations.append("candidate does not answer must_answer: return to perp_multi_asset_carry")
        if "still evidence for `perp_pair_trade_levered`" in must_answer and target_family == "perp_pair_trade_levered" and family != "perp_pair_trade_levered":
            violations.append("candidate does not answer must_answer: pair-trade evidence branch")
    return violations


def extract_embedded_yaml_block(text: str) -> dict[str, Any]:
    import yaml

    match = re.search(r"```yaml\s*\n(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if not match:
        return {}
    blob = match.group(1).strip()
    if blob.startswith("---"):
        blob = blob[3:].lstrip()
    if blob.endswith("---"):
        blob = blob[:-3].rstrip()
    try:
        parsed = yaml.safe_load(blob) or {}
    except Exception:  # noqa: BLE001
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}
