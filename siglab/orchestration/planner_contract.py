"""Contract extraction and normalization for the planner subsystem."""

from __future__ import annotations

import re
from typing import Any, cast


from siglab.orchestration.contracts import PlannerOutput
from siglab.evaluation.strategy_semantics import supports_explicit_trade_style
from siglab.workspace.builder import WorkspaceSession

from .planner_types import dict_value, string_list, unique_strings


def extract_planner_contract(
    *,
    note_text: str,
    note_body: str,
    raw_frontmatter: dict[str, Any],
    yaml_fragments: list[dict[str, Any]],
    parent: Any,
    current_state: dict[str, Any],
    tool_refs: list[str],
    session: WorkspaceSession,
) -> PlannerOutput:
    contract = fallback_contract(
        parent=parent,
        current_state=current_state,
        tool_refs=tool_refs,
    )
    explicit_keys = explicit_contract_keys(raw_frontmatter, yaml_fragments)
    contract = cast(PlannerOutput, merge_hint_fragment(contract, raw_frontmatter))
    for fragment in yaml_fragments:
        contract = cast(PlannerOutput, merge_hint_fragment(contract, fragment))

    note_body_text = note_body if note_body.strip() else note_text
    body_family = body_family_override(note_body_text, session.families)
    if body_family:
        contract["target_family"] = body_family

    target_family = str(contract.get("target_family") or parent.family)
    body_ts = body_trade_style(note_body_text)
    if (
        supports_explicit_trade_style(target_family)
        and body_ts
        and contract.get("target_trade_style") in (None, "", "null")
    ):
        contract["target_trade_style"] = body_ts

    if not string_list(contract.get("target_universe")):
        contract["target_universe"] = list(parent.universe.basis_groups)

    if tool_refs:
        contract["tools_used"] = unique_strings(
            [*string_list(contract.get("tools_used")), "workspace_tools"]
        )
    if not string_list(contract.get("evidence_paths")):
        contract["evidence_paths"] = unique_strings(
            [
                *string_list(current_state.get("selected_lesson_refs")),
                *string_list(current_state.get("selected_probe_refs")),
                *tool_refs,
            ]
        )

    if not str(contract.get("core_hypothesis") or "").strip():
        contract["core_hypothesis"] = section_or_fallback(
            note_body_text,
            headings=("Diagnosis", "Hypothesis"),
            fallback=str(current_state.get("open_question") or f"Refine {target_family}"),
        )
    if not str(contract.get("informative_test") or "").strip():
        contract["informative_test"] = section_or_fallback(
            note_body_text,
            headings=("Proposed next experiment", "What to test", "Next test"),
            fallback="Test one concrete change that resolves the current open question.",
        )
    if not string_list(contract.get("expected_success")):
        contract["expected_success"] = ["better validation robustness"]
    if not string_list(contract.get("expected_failure")):
        contract["expected_failure"] = ["no measurable change"]

    gate_intent = dict_value(contract.get("gate_intent"))
    if gate_intent and not string_list(contract.get("required_gate_dimensions")):
        target_dimension = str(gate_intent.get("target_dimension") or "").strip()
        if target_dimension:
            contract["required_gate_dimensions"] = [target_dimension]

    if not str(contract.get("must_answer") or "").strip():
        contract["must_answer"] = last_question(note_body_text)
    if not str(contract.get("decision") or "").strip():
        contract["decision"] = (
            "branch_family" if target_family != parent.family else "refine_current_family"
        )
    if not str(contract.get("search_mode") or "").strip():
        contract["search_mode"] = str(current_state.get("search_mode") or "refine")
    if not string_list(contract.get("tracking_tags")):
        contract["tracking_tags"] = [target_family]

    explicit_feature_roles = "required_feature_roles" in explicit_keys
    explicit_required_features = "required_features" in explicit_keys
    explicit_required_gate_dimensions = "required_gate_dimensions" in explicit_keys
    explicit_gate_intent_flag = "gate_intent" in explicit_keys
    explicit_regime_gates = bool(
        {"planner_regime_gates", "regime_gates"} & explicit_keys
    )

    if explicit_feature_roles:
        contract["required_feature_roles"] = normalize_required_feature_roles(
            family=target_family,
            required_variation_axis=str(contract.get("required_variation_axis") or ""),
            existing=string_list(contract.get("required_feature_roles")),
        )
    elif str(contract.get("required_variation_axis") or "").strip().lower() == "non_regime":
        contract["required_feature_roles"] = normalize_required_feature_roles(
            family=target_family,
            required_variation_axis="non_regime",
            existing=[],
        )
    else:
        contract["required_feature_roles"] = []

    contract["forbidden_motifs"] = string_list(contract.get("forbidden_motifs")) or default_forbidden_motifs(target_family)
    contract["forbidden_features"] = string_list(contract.get("forbidden_features"))
    contract["required_features"] = (
        string_list(contract.get("required_features")) if explicit_required_features else []
    )
    contract["required_gate_dimensions"] = (
        string_list(contract.get("required_gate_dimensions"))
        if explicit_required_gate_dimensions
        else []
    )
    contract["banned_motif_signatures"] = string_list(contract.get("banned_motif_signatures"))
    contract["writer_inputs"] = string_list(contract.get("writer_inputs")) or default_writer_inputs(target_family)
    contract["evidence_paths"] = string_list(contract.get("evidence_paths"))
    contract["tools_used"] = string_list(contract.get("tools_used"))
    contract["tracking_tags"] = string_list(contract.get("tracking_tags")) or [target_family]
    contract["target_trade_style"] = str(contract.get("target_trade_style") or "").strip() or None
    if not supports_explicit_trade_style(target_family):
        contract["target_trade_style"] = None
    contract["planner_regime_gates"] = normalize_regime_gates(
        contract.get("planner_regime_gates") or contract.get("regime_gates")
    )
    contract["gate_intent"] = dict_value(contract.get("gate_intent")) if explicit_gate_intent_flag else {}
    policy_hint = policy_control_hint(note_body_text)
    if policy_hint and not str(contract.get("required_variation_axis") or "").strip():
        contract["required_variation_axis"] = "policy"
    if policy_hint and not contract["gate_intent"]:
        contract["gate_intent"] = dict(policy_hint)
    if policy_hint and not contract["required_gate_dimensions"]:
        contract["required_gate_dimensions"] = ["policy_persistence"]
    if explicit_regime_gates and not contract["required_gate_dimensions"]:
        planner_gate_entries = list(contract["planner_regime_gates"].get("entry") or [])
        gate_dimensions = []
        for gate in planner_gate_entries:
            if isinstance(gate, dict):
                expression = str(gate.get("expression") or "").strip()
                if expression:
                    gate_dimensions.append(expression)
            elif isinstance(gate, str):
                text = gate.strip()
                if text:
                    gate_dimensions.append(text)
        contract["required_gate_dimensions"] = unique_strings(gate_dimensions)
    contract["must_answer"] = concretize_must_answer(contract)
    return contract


def fallback_contract(
    *,
    parent: Any,
    current_state: dict[str, Any],
    tool_refs: list[str],
) -> PlannerOutput:
    target_family = parent.family
    return {
        "decision": "refine_current_family",
        "search_mode": str(current_state.get("search_mode") or "refine"),
        "target_family": target_family,
        "target_trade_style": (
            str(dict(parent.params or {}).get("trade_style") or "").strip() or None
        )
        if supports_explicit_trade_style(target_family)
        else None,
        "target_universe": list(parent.universe.basis_groups),
        "core_hypothesis": str(current_state.get("open_question") or f"Refine {target_family}"),
        "informative_test": "Test one concrete change tied to the current open question.",
        "expected_success": ["better validation robustness"],
        "expected_failure": ["no measurable change"],
        "evidence_paths": unique_strings(
            [
                *string_list(current_state.get("selected_lesson_refs")),
                *string_list(current_state.get("selected_probe_refs")),
                *tool_refs,
            ]
        ),
        "tools_used": ["workspace_tools"] if tool_refs else [],
        "tracking_tags": [target_family],
        "must_answer": str(current_state.get("open_question") or f"Refine {target_family}"),
        "required_feature_roles": [],
        "required_features": [],
        "forbidden_features": string_list(current_state.get("forbidden_features")),
        "forbidden_motifs": default_forbidden_motifs(target_family),
        "gate_intent": {},
        "required_gate_dimensions": [],
        "required_variation_axis": str(current_state.get("required_variation_axis") or "") or None,
        "banned_motif_signatures": string_list(current_state.get("banned_motif_signatures")),
        "writer_inputs": default_writer_inputs(target_family),
        "planner_regime_gates": {},
    }


def merge_hint_fragment(
    base: dict[str, Any] | PlannerOutput,
    fragment: dict[str, Any],
) -> dict[str, Any] | PlannerOutput:
    merged = dict(base)
    if not isinstance(fragment, dict):
        return merged
    family_alias = str(fragment.get("family") or "").strip()
    if family_alias and not str(fragment.get("target_family") or "").strip():
        fragment = {
            **fragment,
            "target_family": family_alias,
        }
    list_keys = {
        "target_universe",
        "expected_success",
        "expected_failure",
        "evidence_paths",
        "tools_used",
        "tracking_tags",
        "required_feature_roles",
        "required_features",
        "forbidden_features",
        "forbidden_motifs",
        "required_gate_dimensions",
        "banned_motif_signatures",
        "writer_inputs",
    }
    for key, value in fragment.items():
        if value in (None, "", [], {}):
            continue
        if key in list_keys:
            merged[key] = string_list(value)
            continue
        if key in {"gate_intent", "regime_gates", "planner_regime_gates"}:
            if isinstance(value, dict):
                merged["planner_regime_gates" if key != "gate_intent" else key] = dict(value)
            continue
        merged[key] = value
    return merged


def normalize_required_feature_roles(
    *,
    family: str,
    required_variation_axis: str,
    existing: list[str],
) -> list[str]:
    roles = [str(value) for value in existing if str(value).strip()]
    if not roles:
        return default_required_feature_roles(
            family,
            required_variation_axis=required_variation_axis,
        )
    if family == "perp_multi_asset_carry" and required_variation_axis == "non_regime":
        filtered = [role for role in roles if "orthogonal_regime" not in role.lower()]
        if not any("core_carry" in role.lower() for role in filtered):
            filtered.insert(0, "one core_carry feature")
        if not any("non_regime_axis" in role.lower() for role in filtered):
            filtered.append("one non_regime_axis feature")
        return filtered
    return roles


def default_required_feature_roles(family: str, *, required_variation_axis: str = "") -> list[str]:
    if family == "perp_multi_asset_carry" and required_variation_axis == "non_regime":
        return ["one core_carry feature", "one non_regime_axis feature"]
    if family == "perp_multi_asset_carry":
        return ["one core_carry feature", "one orthogonal_regime feature"]
    if family in {"perp_pair_trade_unlevered", "perp_pair_trade_levered"}:
        return ["one spread_or_residual feature", "one orthogonal_regime feature"]
    if family in {"perp_basket_neutral_unlevered", "perp_basket_neutral_levered"}:
        return ["one cross_sectional_core feature", "one orthogonal_regime feature"]
    return ["one trend_or_momentum feature", "one orthogonal_regime feature"]


def default_forbidden_motifs(family: str) -> list[str]:
    if family == "perp_multi_asset_carry":
        return ["second pure trend overlay"]
    return []


def default_writer_inputs(family: str) -> list[str]:
    return [
        f"manifests/family/{family}.md",
        f"manifests/family/{family}.json",
        f"manifests/features/family/{family}.md",
        f"manifests/features/family/{family}.json",
        "manifests/constraints.md",
        "manifests/regime_catalog.md",
        "manifests/policy_surface.md",
    ]


def concretize_must_answer(contract: dict[str, Any] | PlannerOutput) -> str:
    must_answer = str(contract.get("must_answer") or "").strip()
    feature_refs = string_list(contract.get("required_features"))
    gate_dims = string_list(contract.get("required_gate_dimensions"))
    family = str(contract.get("target_family") or "this family")
    required_variation_axis = str(contract.get("required_variation_axis") or "").strip().lower()
    if must_answer:
        lowered = must_answer.lower()
        concrete_enough = (
            must_answer.endswith("?")
            and lowered.startswith(("does ", "is ", "can ", "should ", "will ", "did "))
            and any(token in lowered for token in ("validation", "pre-audit", "return", "drawdown"))
        )
        if concrete_enough:
            return must_answer
    if required_variation_axis in {"policy", "policy_control", "persistence"}:
        return (
            f"Does changing the policy/persistence controls in `{family}` reduce churn and drawdown "
            "while improving pre-audit return without making validation negative?"
        )
    if feature_refs and gate_dims:
        return (
            f"Does using `{feature_refs[0]}` with a `{gate_dims[0]}` gate improve pre-audit return "
            f"without making validation negative for `{family}`?"
        )
    if feature_refs:
        return (
            f"Does replacing one overused feature with `{feature_refs[0]}` improve pre-audit return "
            f"without making validation negative for `{family}`?"
        )
    if gate_dims:
        return (
            f"Does gating on `{gate_dims[0]}` improve pre-audit return without making validation "
            f"negative for `{family}`?"
        )
    if required_variation_axis == "non_regime":
        return (
            f"Does changing one non-regime axis in `{family}` improve pre-audit return without making "
            f"validation negative, or are additional regime filters still the wrong fix?"
        )
    return (
        f"Does one concrete change improve pre-audit return without making validation negative for `{family}`, "
        f"or should this line of attack be rejected?"
    )


def normalize_regime_gates(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    entries: list[Any] = []
    for gate in list(value.get("entry") or []):
        if isinstance(gate, str):
            text = gate.strip()
            if text:
                entries.append(text)
            continue
        if not isinstance(gate, dict):
            continue
        expression = str(gate.get("expression") or "").strip()
        if not expression:
            continue
        normalized: dict[str, Any] = {"expression": expression}
        if gate.get("min") is not None:
            normalized["min"] = gate.get("min")
        if gate.get("max") is not None:
            normalized["max"] = gate.get("max")
        entries.append(normalized)
    normalized_gates: dict[str, Any] = {}
    if entries:
        normalized_gates["entry"] = entries
    if value.get("exit_on_break") is not None:
        normalized_gates["exit_on_break"] = bool(value.get("exit_on_break"))
    return normalized_gates


def policy_control_hint(text: str) -> dict[str, str]:
    lowered = text.lower()
    churn_tokens = (
        "high_position_flip_rate",
        "position flip rate",
        "score sign flips",
        "churn",
        "max_holding_bars",
        "cooldown_bars",
        "flip_abs_score",
        "symmetric entry/exit",
        "position persistence",
    )
    if not any(token in lowered for token in churn_tokens):
        return {}
    return {
        "type": "suppress_policy_churn",
        "target_dimension": "policy_persistence",
    }


def body_family_override(text: str, families: list[str]) -> str | None:
    escaped = "|".join(re.escape(family) for family in families)
    explicit_patterns = [
        rf"(?im)^\*\*family:\*\*\s*`?({escaped})`?\s*$",
        rf"(?im)^family:\s*`?({escaped})`?\s*$",
        rf"(?im)\b(return to|switch to|stay in)\s+`?({escaped})`?",
    ]
    for pattern in explicit_patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        assert match.lastindex is not None
        family = match.group(match.lastindex)
        if family:
            return family
    section_match = re.search(
        r"(?ims)^##\s+(Proposed next experiment|What To Test|What to test)\s*\n(.*?)(?=^##\s+|\Z)",
        text,
    )
    if section_match:
        section = section_match.group(2)
        for family in families:
            if family in section:
                return family
    return None


def body_trade_style(text: str) -> str | None:
    matches = list(
        re.finditer(r"\btrade_style\b\s*[:=]\s*([a-z0-9_]+)", text, flags=re.IGNORECASE)
    )
    if not matches:
        return None
    return matches[-1].group(1).strip()


def explicit_contract_keys(
    raw_frontmatter: dict[str, Any],
    yaml_fragments: list[dict[str, Any]],
) -> set[str]:
    keys: set[str] = set()
    if isinstance(raw_frontmatter, dict):
        keys.update(str(key) for key in raw_frontmatter.keys())
    for fragment in yaml_fragments:
        if not isinstance(fragment, dict):
            continue
        keys.update(str(key) for key in fragment.keys())
    return keys


def section_or_fallback(
    text: str,
    *,
    headings: tuple[str, ...],
    fallback: str,
) -> str:
    for heading in headings:
        match = re.search(
            rf"(?ms)^##\s+{re.escape(heading)}\s*\n(.*?)(?=^##\s+|\Z)",
            text,
        )
        if match:
            section = match.group(1).strip()
            if section:
                return " ".join(section.split())[:600]
    return fallback


def last_question(text: str) -> str:
    questions = [
        line.strip()
        for line in text.splitlines()
        if line.strip().endswith("?") and len(line.strip()) >= 15
    ]
    return questions[-1] if questions else ""
