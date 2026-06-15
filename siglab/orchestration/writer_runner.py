from __future__ import annotations

import importlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from siglab.evaluator.compile import compile_spec
from siglab.families import family_prompt_module
from siglab.io_utils import json_clone, write_json
from siglab.llm import ClaudeClient, LLMProviderError
from siglab.schemas import SignalSpec
from siglab.orchestration.contracts import PlannerOutput, PreflightResult, WriterOutput, conformance_violations
from siglab.orchestration.trials import build_spec_patch, summarize_patch
from siglab.research import HypothesisSandbox
from siglab.search.mutate import SpecMutator
from siglab.evaluation.strategy_semantics import motif_signature
from siglab.workspace.cards import dump_yaml_block
from siglab.workspace.builder import WorkspaceSession


@dataclass
class WriterResult:
    spec_payload: dict[str, Any] | None
    spec_path: Path | None
    trace_path: Path
    accepted: bool
    base_spec_payload: dict[str, Any] | None = None
    base_spec_path: Path | None = None
    structure_spec: dict[str, Any] | None = None
    patch_payload: dict[str, Any] | None = None
    patch_summary: list[str] | None = None
    spec_after_patch_path: Path | None = None
    failure_reason: str | None = None
    failure_packet: dict[str, Any] | None = None


class SpecWriterRunner:
    MAX_ATTEMPTS = 2
    TOP_LEVEL_KEYS = {
        "track",
        "family",
        "hypothesis",
        "neutrality_basis",
        "features",
        "universe",
        "risk",
        "regime_gates",
        "params",
    }
    HARMLESS_NORMALIZATION_FIELDS = {
        "params.long_enabled",
        "params.short_enabled",
        "risk.max_chain_weight",
        "risk.roll_days_before_expiry",
        "universe.chains",
        "universe.min_liquidity_usd",
        "universe.min_volume_usd_24h",
        "universe.min_days_to_expiry",
        "universe.max_days_to_expiry",
    }

    def __init__(
        self,
        *,
        settings: Any,
        claude: ClaudeClient,
        mutator: SpecMutator,
        hypothesis_sandbox: HypothesisSandbox | None = None,
    ) -> None:
        self.settings = settings
        self.claude = claude
        self.mutator = mutator
        self.hypothesis_sandbox = hypothesis_sandbox

    def _dict_or_empty(self, value: Any) -> dict[str, Any]:
        return dict(value) if isinstance(value, dict) else {}

    async def run(
        self,
        *,
        session: WorkspaceSession,
        research_note_path: Path,
        iteration_paths: dict[str, Any],
        parent: Any,
        base_spec_payload: dict[str, Any] | None = None,
    ) -> WriterOutput:
        research_note_text = research_note_path.read_text()
        planner_contract_path = iteration_paths.get("planner_contract_path")
        planner_contract = self._load_planner_contract(
            planner_contract_path=planner_contract_path,
            research_note_text=research_note_text,
            families=session.families,
            parent=parent,
        )
        family = str(planner_contract.get("target_family") or parent.family)
        base_payload = self._clone_payload(base_spec_payload or parent.canonical_dict())
        base_spec_path = iteration_paths["base_spec_path"]
        base_spec_path.write_text(dump_yaml_block(base_payload) + "\n")
        structure_spec = self._build_structure_spec(
            family=family,
            planner_contract=planner_contract,
            research_note_path=research_note_path,
            base_spec_payload=base_payload,
        )
        structure_spec_path = iteration_paths["structure_spec_path"]
        write_json(structure_spec_path, structure_spec)
        manifest_path = session.manifests_dir / "family" / f"{family}.md"
        family_contract_path = session.manifests_dir / "family" / f"{family}.json"
        family_feature_manifest_path = session.manifests_dir / "features" / "family" / f"{family}.md"
        family_feature_contract_path = session.manifests_dir / "features" / "family" / f"{family}.json"
        constraints_path = session.manifests_dir / "constraints.md"
        regime_catalog_path = session.manifests_dir / "regime_catalog.md"
        policy_surface_path = session.manifests_dir / "policy_surface.md"
        cookbook_paths = self._cookbook_paths(session=session, family=family)
        spec_schema_path = (
            self.settings.root_dir
            / ".agents"
            / "skills"
            / "siglab-spec-writer"
            / "templates"
            / "spec_schema.json"
        )
        system_prompt = (
            self.settings.root_dir
            / ".agents"
            / "skills"
            / "siglab-spec-writer"
            / "SKILL.md"
        ).read_text()
        user_prompt = self._build_user_prompt(
            research_note_text=research_note_text,
            research_note_path=research_note_path,
            parent_card_path=session.current_dir / "parent_card.md",
            manifest_path=manifest_path,
            family_contract_path=family_contract_path,
            family_feature_manifest_path=family_feature_manifest_path,
            family_feature_contract_path=family_feature_contract_path,
            constraints_path=constraints_path,
            regime_catalog_path=regime_catalog_path,
            policy_surface_path=policy_surface_path,
            cookbook_paths=cookbook_paths,
            spec_schema_path=spec_schema_path,
            planner_contract=planner_contract,
            base_spec_payload=base_payload,
            evidence_paths=[str(path) for path in list(planner_contract.get("evidence_paths") or [])],
            planner_regime_gates=self._dict_or_empty(
                planner_contract.get("planner_regime_gates") or planner_contract.get("regime_gates")
            ),
            workspace_root=session.root,
        )
        allowed_families = list(session.families)
        allowed_features_by_family = self.mutator._allowed_features_by_family(
            session.track,
            family=allowed_families,
        )
        family_defaults = self.mutator._family_defaults(
            session.track,
            family=allowed_families,
        )
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": user_prompt},
        ]
        attempt_log: list[dict[str, Any]] = []
        final_payload: dict[str, Any] | None = None
        fallback_payload: dict[str, Any] | None = None
        target_family = str(planner_contract.get("target_family") or parent.family)
        repair_packet_path = iteration_paths["repair_packet_path"]
        latest_repair_packet: dict[str, Any] | None = None
        last_preflight: PreflightResult | None = None

        for attempt in range(1, self._max_attempts() + 1):
            parse_error: str | None = None
            payload: dict[str, Any] | None = None
            try:
                payload = await self.claude.complete_json_messages(
                    system_prompt=system_prompt,
                    messages=messages,
                    max_tokens=self._writer_max_tokens(),
                    timeout_s=max(self.settings.claude_timeout_s, 90.0),
                    json_mode=True,
                    thinking_override="disabled",
                    stage="writer",
                )
            except (json.JSONDecodeError, LLMProviderError, TypeError, ValueError) as exc:
                parse_error = f"{type(exc).__name__}: {exc}"

            preflight = await self._preflight_spec(
                session=session,
                payload=payload,
                parse_error=parse_error,
                track=session.track,
                parent=parent,
                target_family=target_family,
                planner_contract=planner_contract,
                allowed_families=allowed_families,
                allowed_features_by_family=allowed_features_by_family,
                family_defaults=family_defaults,
            )
            if preflight.validated_payload is not None:
                fallback_payload = preflight.validated_payload
            last_preflight = preflight

            attempt_log.append(
                {
                    "attempt": attempt,
                    "payload": payload,
                    "parse_error": preflight.parse_error,
                    "hard_issues": list(preflight.hard_issues),
                    "conformance_issues": list(preflight.conformance_issues),
                    "gate_lint": preflight.gate_lint,
                    "changed_fields": list(preflight.changed_fields),
                    "harmless_changed_fields": list(preflight.harmless_changed_fields),
                    "material_changed_fields": list(preflight.material_changed_fields),
                    "material_drift": preflight.material_drift,
                    "claude_trace": dict(self.claude.last_trace or {}),
                }
            )

            if preflight.acceptable and preflight.validated_payload is not None:
                final_payload = preflight.validated_payload
                break

            latest_repair_packet = self._build_repair_packet(
                target_family=target_family,
                planner_contract=planner_contract,
                payload=payload,
                preflight=preflight,
            )
            write_json(repair_packet_path, latest_repair_packet)

            if attempt >= self._max_attempts():
                break

            if payload is not None:
                messages.append(
                    {
                        "role": "assistant",
                        "content": json.dumps(payload, ensure_ascii=True, default=str),
                    }
                )
            messages.append(
                {
                    "role": "user",
                    "content": self._repair_prompt(
                        repair_packet=latest_repair_packet,
                    ),
                }
            )

        spec_path: Path | None = None
        failure_reason: str | None = None
        accepted = final_payload is not None
        patch_payload: dict[str, Any] | None = None
        patch_summary: list[str] = []
        spec_after_patch_path: Path | None = None
        if final_payload is None:
            if last_preflight is not None and (last_preflight.hard_issues or last_preflight.conformance_issues):
                failure_reason = (
                    "Writer failed planner/writer conformance after repair: "
                    + "; ".join(list(last_preflight.hard_issues) + list(last_preflight.conformance_issues))
                )
            elif fallback_payload is None:
                failure_reason = "Writer failed to produce a valid spec JSON object"
            else:
                failure_reason = "Writer fell back to a deterministic payload but did not satisfy preflight"
            spec_payload = fallback_payload
        else:
            spec_payload = final_payload
            spec_path = cast(Path, iteration_paths["spec_json_path"])
            write_json(spec_path, spec_payload)
            if repair_packet_path.exists():
                repair_packet_path.unlink()
            latest_repair_packet = None
        if spec_payload is not None:
            patch_payload = build_spec_patch(
                base_payload=base_payload,
                target_payload=spec_payload,
            )
            patch_summary = summarize_patch(patch_payload)
            spec_patch_path = cast(Path, iteration_paths["spec_patch_path"])
            write_json(spec_patch_path, patch_payload)
            spec_after_patch_path = cast(Path, iteration_paths["spec_after_patch_path"])
            spec_after_patch_path.write_text(dump_yaml_block(spec_payload) + "\n")
            if repair_packet_path.exists():
                repair_packet_path.unlink()
            latest_repair_packet = None
        trace_path = iteration_paths["writer_trace_path"]
        parent_card_path = session.current_dir / "parent_card.md"
        write_json(
            trace_path,
            {
                "stage": "writer",
                "system_prompt_path": str(
                    (
                        self.settings.root_dir
                        / ".agents"
                        / "skills"
                        / "siglab-spec-writer"
                        / "SKILL.md"
                    ).relative_to(self.settings.root_dir)
                ),
                "inputs": {
                    "system_prompt": system_prompt,
                    "initial_user_prompt": user_prompt,
                    "research_note_path": str(research_note_path),
                    "planner_contract_path": str(planner_contract_path) if planner_contract_path else None,
                    "structure_spec_path": str(structure_spec_path),
                    "base_spec_path": str(base_spec_path),
                    "parent_card_path": str(parent_card_path),
                    "manifest_path": str(manifest_path),
                    "family_contract_path": str(family_contract_path),
                    "family_feature_manifest_path": str(family_feature_manifest_path),
                    "family_feature_contract_path": str(family_feature_contract_path),
                    "constraints_path": str(constraints_path),
                    "regime_catalog_path": str(regime_catalog_path),
                    "policy_surface_path": str(policy_surface_path),
                    "cookbook_paths": [str(path) for path in cookbook_paths],
                    "spec_schema_path": str(spec_schema_path),
                    "evidence_paths": [str(path) for path in list(planner_contract.get("evidence_paths") or [])],
                    "planner_contract": planner_contract,
                },
                "outputs": {
                    "accepted": accepted,
                    "failure_reason": failure_reason,
                    "spec_path": str(spec_path) if spec_path is not None else None,
                    "spec_payload": spec_payload,
                    "spec_after_patch_path": (
                        str(spec_after_patch_path) if spec_after_patch_path is not None else None
                    ),
                    "spec_patch_path": (
                        str(iteration_paths["spec_patch_path"])
                        if patch_payload is not None
                        else None
                    ),
                    "patch_summary": patch_summary,
                    "structure_spec": structure_spec,
                    "repair_packet_path": (
                        str(repair_packet_path) if latest_repair_packet is not None else None
                    ),
                    "latest_repair_packet": latest_repair_packet,
                },
                "attempt_count": len(attempt_log),
                "attempts": attempt_log,
                "conversation_messages": messages,
                "claude_trace": dict(self.claude.last_trace or {}),
                "claude_exchange": dict(self.claude.last_exchange or {}),
            },
        )
        return {
            "spec_payload": spec_payload,
            "spec_path": str(spec_path) if spec_path else None,
            "trace_path": str(trace_path),
            "accepted": accepted,
            "base_spec_payload": base_payload,
            "base_spec_path": str(base_spec_path) if base_spec_path else None,
            "structure_spec": structure_spec,
            "patch_payload": patch_payload,
            "patch_summary": patch_summary,
            "spec_after_patch_path": str(spec_after_patch_path) if spec_after_patch_path else None,
            "failure_reason": failure_reason,
            "failure_packet": latest_repair_packet,
        }

    @staticmethod
    def _empty_preflight(
        *,
        parse_error: str | None = None,
        hard_issues: list[str] | None = None,
        conformance_issues: list[str] | None = None,
    ) -> PreflightResult:
        return PreflightResult(
            parse_error=parse_error,
            hard_issues=list(hard_issues or []),
            conformance_issues=list(conformance_issues or []),
            gate_lint=None,
            changed_fields=[],
            harmless_changed_fields=[],
            material_changed_fields=[],
            validated_payload=None,
        )

    async def _preflight_spec(
        self,
        *,
        session: WorkspaceSession,
        payload: dict[str, Any] | None,
        parse_error: str | None,
        track: str,
        parent: Any,
        target_family: str,
        planner_contract: PlannerOutput,
        allowed_families: list[str],
        allowed_features_by_family: dict[str, list[str]],
        family_defaults: dict[str, Any],
    ) -> PreflightResult:
        hard_issues: list[str] = []
        contract_issues: list[str] = []
        changed_fields: list[str] = []
        harmless_changed_fields: list[str] = []
        material_changed_fields: list[str] = []
        validated_payload: dict[str, Any] | None = None
        gate_lint: dict[str, Any] | None = None
        activity_lint: dict[str, Any] | None = None
        if parse_error is not None or payload is None:
            return self._empty_preflight(
                parse_error=parse_error,
                hard_issues=[f"parse error: {parse_error}"] if parse_error else ["empty spec payload"],
            )
        payload = self._apply_planner_gate_spec(payload=payload, planner_contract=planner_contract)
        extra_keys = sorted(set(payload) - self.TOP_LEVEL_KEYS)
        if extra_keys:
            hard_issues.append(f"unsupported top-level keys: {', '.join(extra_keys)}")
        missing_keys = sorted(self.TOP_LEVEL_KEYS - set(payload))
        if missing_keys:
            hard_issues.append(f"missing required top-level keys: {', '.join(missing_keys)}")
        try:
            raw_spec = SignalSpec.from_dict(payload)
        except (KeyError, TypeError, ValueError) as exc:
            hard_issues.append(f"spec schema parse failed: {type(exc).__name__}: {exc}")
            return self._empty_preflight(hard_issues=hard_issues)
        if raw_spec.family != target_family:
            hard_issues.append(
                f"family mismatch: expected `{target_family}` from the research note but got `{raw_spec.family}`"
            )
        contract_issues.extend(
            conformance_violations(
                planner_contract=planner_contract,
                spec_payload=payload,
                allowed_features=list(allowed_features_by_family.get(target_family) or []),
                parent_payload=parent.canonical_dict(),
            )
        )
        try:
            validated = self.mutator._validate_spec(
                spec=SignalSpec.from_dict(payload),
                track=track,
                allowed_families=allowed_families,
                allowed_features_by_family=allowed_features_by_family,
                family_defaults=family_defaults,
            )
        except (KeyError, TypeError, ValueError) as exc:
            hard_issues.append(f"validator rejected spec: {type(exc).__name__}: {exc}")
            return self._empty_preflight(hard_issues=hard_issues, conformance_issues=contract_issues)

        raw_canonical = raw_spec.canonical_dict()
        validated_payload = validated.canonical_dict()
        changed_fields = self._changed_fields(
            self._drift_compare_payload(raw_canonical),
            self._drift_compare_payload(validated_payload),
        )
        harmless_changed_fields = [
            field for field in changed_fields if field in self.HARMLESS_NORMALIZATION_FIELDS
        ]
        material_changed_fields = [
            field for field in changed_fields if field not in self.HARMLESS_NORMALIZATION_FIELDS
        ]
        if self.hypothesis_sandbox is not None and dict(validated.regime_gates or {}).get("entry"):
            gate_lint = await self._gate_lint(
                session=session,
                parent=parent,
                spec=validated,
            )
            severe_gate_warnings = [
                warning
                for warning in list(gate_lint.get("warnings") or [])
                if warning in {
                    "regime_gates_are_effectively_always_open",
                    "regime_gates_are_extremely_restrictive",
                    "gated_spec_is_near_flat",
                    "gates_do_not_change_train_outcomes",
                }
            ]
            if severe_gate_warnings:
                hard_issues.extend(
                    [f"gate_lint: {warning}" for warning in severe_gate_warnings]
                )
        if self.hypothesis_sandbox is not None and validated.family not in {
            "perp_pair_trade_unlevered",
            "perp_pair_trade_levered",
        }:
            activity_lint = await self._activity_lint(spec=validated)
            if activity_lint.get("ok") and float(activity_lint.get("active_bar_fraction") or 0.0) < 0.01:
                hard_issues.append("activity_lint: spec_is_near_flat")
        return PreflightResult(
            parse_error=None,
            hard_issues=hard_issues,
            conformance_issues=contract_issues,
            gate_lint={
                **dict(gate_lint or {}),
                "activity_lint": activity_lint,
            }
            if gate_lint or activity_lint
            else None,
            changed_fields=changed_fields,
            harmless_changed_fields=harmless_changed_fields,
            material_changed_fields=material_changed_fields,
            validated_payload=validated_payload,
        )

    def _build_repair_packet(
        self,
        *,
        target_family: str,
        planner_contract: PlannerOutput,
        payload: dict[str, Any] | None,
        preflight: PreflightResult,
    ) -> dict[str, Any]:
        normalization_diff = {}
        if payload is not None and preflight.validated_payload is not None:
            for field in preflight.changed_fields:
                proposed = self._get_by_path(payload, field)
                normalized = self._get_by_path(preflight.validated_payload, field)
                normalization_diff[field] = {
                    "proposed": proposed,
                    "normalized": normalized,
                }
        return {
            "family": target_family,
            "errors": list(preflight.hard_issues),
            "conformance_issues": list(preflight.conformance_issues),
            "planner_contract": planner_contract,
            "gate_lint": preflight.gate_lint,
            "normalization_diff": normalization_diff,
            "material_drift": preflight.material_drift,
            "reason": (
                "proposal relied on invalid schema or materially drifted under validation"
                if preflight.hard_issues or preflight.material_drift or preflight.conformance_issues
                else "harmless normalization only"
            ),
            "material_changed_fields": list(preflight.material_changed_fields),
            "harmless_changed_fields": list(preflight.harmless_changed_fields),
            "motif_signature": motif_signature(payload or {}),
        }

    def _changed_fields(
        self,
        raw_payload: dict[str, Any],
        validated_payload: dict[str, Any],
        *,
        prefix: str = "",
    ) -> list[str]:
        changed: list[str] = []
        keys = sorted(set(raw_payload) | set(validated_payload))
        for key in keys:
            path = f"{prefix}.{key}" if prefix else key
            left = raw_payload.get(key)
            right = validated_payload.get(key)
            if isinstance(left, dict) and isinstance(right, dict):
                changed.extend(self._changed_fields(left, right, prefix=path))
                continue
            if left != right:
                changed.append(path)
        return changed

    def _drift_compare_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = json_clone(payload)
        regime_gates = self._dict_or_empty(normalized.get("regime_gates"))
        if not regime_gates:
            normalized["regime_gates"] = {"entry": [], "exit_on_break": False}
            return cast(dict[str, Any], normalized)
        entry = regime_gates.get("entry")
        if entry is None or entry == []:
            regime_gates["entry"] = []
            regime_gates["exit_on_break"] = False
        normalized["regime_gates"] = regime_gates
        return cast(dict[str, Any], normalized)

    def _repair_prompt(
        self,
        *,
        repair_packet: dict[str, Any],
    ) -> str:
        return "\n".join(
            [
                "Your previous spec is not acceptable yet.",
                f"Target family remains `{repair_packet.get('family')}`.",
                "Return one corrected minified JSON object only.",
                "Do not include explanations, markdown, comments, YAML, trailing commas, or unterminated strings.",
                "Keep string fields concise so the entire JSON object completes before the token limit.",
                "If errors mention `regime_gates_are_extremely_restrictive`, `gated_spec_is_near_flat`, or `spec_is_near_flat`, relax or remove entry gates rather than adding more gates.",
                "For near-flat failures, prefer `\"regime_gates\":{\"entry\":[],\"exit_on_break\":false}` unless the planner contract explicitly requires a legal exact gate.",
                "A valid active strategy is better than a clever inactive gated strategy.",
                "Repair packet:",
                dump_yaml_block(repair_packet),
            ]
        )

    def _build_user_prompt(
        self,
        *,
        research_note_text: str,
        research_note_path: Path,
        parent_card_path: Path,
        manifest_path: Path,
        family_contract_path: Path,
        family_feature_manifest_path: Path,
        family_feature_contract_path: Path,
        constraints_path: Path,
        regime_catalog_path: Path,
        policy_surface_path: Path,
        cookbook_paths: list[Path],
        spec_schema_path: Path,
        planner_contract: PlannerOutput,
        base_spec_payload: dict[str, Any],
        evidence_paths: list[str],
        planner_regime_gates: dict[str, Any],
        workspace_root: Path,
    ) -> str:
        parts = [
            "Emit exactly one spec JSON object that satisfies the schema and the chosen family manifest.",
            "Output must be valid compact JSON only: no markdown, no comments, no YAML, no trailing commas, no prose before or after the object.",
            "Keep string values concise and close every string, array, and object. Prefer minified JSON over pretty-printed JSON.",
            "The research note is free-form. The extracted planner contract below is binding if it conflicts with the prose.",
            "Treat the base spec below as the starting point for this trial. Preserve its working structure unless the research note calls for a clear structural change.",
            "Claude owns structural changes here: family, feature set, universe, gate expressions, and other discrete design choices.",
            "Continuous numeric tuning will be handled by Optuna after this stage. Set reasonable defaults and bounds-friendly values instead of trying to brute-force the final thresholds yourself.",
            "The family manifest and family contract are the source of truth. Reuse an existing alias when it already expresses the intended idea; otherwise compose a new formula only from listed aliases, raw series, and operators.",
            "Regime gate contract: `regime_gates.entry` must be [] or a list of either string expressions or dicts with `expression` and optional `min` / `max`.",
            "Valid examples: `\"ge(pair_corr_72h,0.9)\"`, `{\"expression\":\"market_volatility_168h\",\"max\":0.0085}`, `{\"expression\":\"funding_dispersion_72h\",\"min\":0.00001}`.",
            "Invalid gate examples: `{\"feature\":\"x\",\"op\":\"gt\",\"threshold\":0.1}`, `{\"expression\":\"x\",\"condition\":\"gt\",\"threshold\":0.1}`, `{\"expression\":\"x\",\"active\":true}`.",
            "If the note suggests a regime change but does not provide a legal gate shape, either encode it with the valid contract or leave `regime_gates.entry` empty instead of inventing invalid keys.",
            "If the extracted planner contract provides an explicit `planner_regime_gates` block, treat that block as canonical.",
            "Copy the exact gate expression and the exact numeric literal from that block. Do not rescale, round, or reinterpret values.",
            "Never rewrite small thresholds into scientific notation or different magnitudes. Keep `0.000015` as `0.000015`, not `1.5e-05`, `1.5`, or `15e-6`.",
            "Example: if the planner says `{\"expression\":\"funding_dispersion_72h\",\"min\":0.000001}`, you must emit `0.000001` for that gate, not `1.0`, `0.0000010` rewritten into a different threshold, or a different bound type.",
            "Novel feature formulas are allowed if they use only aliases, raw series, and formula operators listed in the family manifest.",
            "Satisfy the planner contract exactly: family, trade style, required feature roles, forbidden motifs, and gate intent.",
            "If the planner names exact features or gate dimensions, preserve them exactly unless the repair packet explicitly tells you they were invalid.",
            "If the planner requires a non-regime axis of variation, do not answer with another regime-only carry variant.",
            "If the planner requires a policy/persistence axis, change `params.entry_abs_score`, `params.exit_abs_score`, `params.flip_abs_score`, `params.max_holding_bars`, or `params.cooldown_bars`; a feature-only patch is invalid.",
            "Do not create restrictive entry gates unless the extracted planner contract explicitly requires exact gates. If uncertain, emit empty entry gates.",
            "The writer must avoid near-flat output. Gate logic that blocks almost every bar is invalid even if the JSON schema is valid.",
            "Some policy fields may be locally swept by the evaluator. Choose coherent starting values rather than knife-edge thresholds.",
            "",
            "## Research Note",
            research_note_text,
            "",
            "## Extracted Planner Contract",
            dump_yaml_block(planner_contract),
            "",
            "## Base Spec",
            dump_yaml_block(base_spec_payload),
            "",
            "## Parent Card",
            parent_card_path.read_text(),
            "",
            "## Family Manifest",
            manifest_path.read_text(),
            "",
            "## Family Contract",
            family_contract_path.read_text(),
            "",
            "## Family Feature Contract",
            family_feature_manifest_path.read_text(),
            "",
            "## Family Feature Contract JSON",
            family_feature_contract_path.read_text(),
            "",
            "## Constraints",
            constraints_path.read_text(),
            "",
            "## Regime Catalog",
            regime_catalog_path.read_text(),
            "",
            "## Policy Surface",
            policy_surface_path.read_text(),
            "",
            "## Spec Schema",
            spec_schema_path.read_text(),
        ]
        if planner_regime_gates:
            parts.extend(
                [
                    "",
                    "## Exact Planner Gate Spec",
                    dump_yaml_block(planner_regime_gates),
                ]
            )
        for cookbook_path in cookbook_paths:
            parts.extend(["", f"## Cookbook {cookbook_path.name}", cookbook_path.read_text()])
        for evidence_ref in evidence_paths:
            if not evidence_ref.startswith("cards/probes/"):
                continue
            path = workspace_root / evidence_ref
            if not path.exists():
                continue
            parts.extend(["", f"## Evidence {evidence_ref}", path.read_text()])
        return "\n".join(parts)

    def _build_structure_spec(
        self,
        *,
        family: str,
        planner_contract: PlannerOutput,
        research_note_path: Path,
        base_spec_payload: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "target_family": family,
            "research_note_path": str(research_note_path),
            "base_spec_hash": self._spec_hash(base_spec_payload),
            "base_spec_family": str(base_spec_payload.get("family") or ""),
            "must_answer": planner_contract.get("must_answer"),
            "core_hypothesis": planner_contract.get("core_hypothesis"),
            "required_features": list(planner_contract.get("required_features") or []),
            "required_feature_roles": list(planner_contract.get("required_feature_roles") or []),
            "required_gate_dimensions": list(planner_contract.get("required_gate_dimensions") or []),
            "forbidden_features": list(planner_contract.get("forbidden_features") or []),
            "forbidden_motifs": list(planner_contract.get("forbidden_motifs") or []),
            "planner_regime_gates": self._dict_or_empty(
                planner_contract.get("planner_regime_gates") or planner_contract.get("regime_gates")
            ),
            "evidence_paths": list(planner_contract.get("evidence_paths") or []),
            "writer_inputs": list(planner_contract.get("writer_inputs") or []),
            "continuous_tuning_owner": "optuna",
        }

    def _spec_hash(self, payload: dict[str, Any]) -> str | None:
        try:
            return SignalSpec.from_dict(payload).strategy_hash()
        except (KeyError, TypeError, ValueError):
            return None

    def _parse_frontmatter_safe(self, text: str) -> dict[str, Any]:
        stripped = text.lstrip()
        if not stripped.startswith("---\n"):
            return {}
        _, remainder = stripped.split("---\n", 1)
        frontmatter_blob, separator, _body = remainder.partition("\n---\n")
        if not separator:
            return {}
        try:
            yaml_module = cast(Any, importlib.import_module("yaml"))
        except ImportError:
            return {}
        try:
            parsed = yaml_module.safe_load(frontmatter_blob) or {}
        except getattr(yaml_module, "YAMLError", ValueError):
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}

    def _writer_max_tokens(self) -> int:
        provider = str(getattr(self.settings, "llm_provider", "") or "").strip().lower()
        if provider == "bai":
            return 2200
        return 1200

    def _max_attempts(self) -> int:
        provider = str(getattr(self.settings, "llm_provider", "") or "").strip().lower()
        if provider == "bai":
            return 3
        return self.MAX_ATTEMPTS

    def _clone_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return cast(dict[str, Any], json_clone(payload))

    def _load_planner_contract(
        self,
        *,
        planner_contract_path: Path | None,
        research_note_text: str,
        families: list[str],
        parent: Any,
    ) -> PlannerOutput:
        if planner_contract_path and planner_contract_path.exists():
            try:
                payload = json.loads(planner_contract_path.read_text())
            except json.JSONDecodeError:
                payload = {}
            if isinstance(payload, dict):
                contract = dict(payload)
                note_family = self._family_from_note(research_note_text=research_note_text, families=families)
                if note_family:
                    contract["target_family"] = note_family
                return cast(PlannerOutput, contract)
        note_frontmatter = self._parse_frontmatter_safe(research_note_text)
        return cast(PlannerOutput, {
            "target_family": str(note_frontmatter.get("target_family") or parent.family),
            "target_trade_style": str(note_frontmatter.get("target_trade_style") or "") or None,
            "must_answer": str(note_frontmatter.get("must_answer") or ""),
            "core_hypothesis": str(note_frontmatter.get("core_hypothesis") or ""),
            "informative_test": str(note_frontmatter.get("informative_test") or ""),
            "required_feature_roles": list(note_frontmatter.get("required_feature_roles") or []),
            "required_features": list(note_frontmatter.get("required_features") or []),
            "forbidden_features": list(note_frontmatter.get("forbidden_features") or []),
            "forbidden_motifs": list(note_frontmatter.get("forbidden_motifs") or []),
            "gate_intent": self._dict_or_empty(note_frontmatter.get("gate_intent")),
            "required_gate_dimensions": list(note_frontmatter.get("required_gate_dimensions") or []),
            "planner_regime_gates": self._dict_or_empty(
                note_frontmatter.get("planner_regime_gates") or note_frontmatter.get("regime_gates")
            ),
            "required_variation_axis": str(note_frontmatter.get("required_variation_axis") or "") or None,
            "banned_motif_signatures": list(note_frontmatter.get("banned_motif_signatures") or []),
            "writer_inputs": list(note_frontmatter.get("writer_inputs") or []),
            "evidence_paths": list(note_frontmatter.get("evidence_paths") or []),
            "tracking_tags": list(note_frontmatter.get("tracking_tags") or []),
        })

    def _family_from_note(self, *, research_note_text: str, families: list[str]) -> str | None:
        escaped = "|".join(re.escape(family) for family in families)
        explicit_patterns = [
            rf"(?im)^\*\*family:\*\*\s*`?({escaped})`?\s*$",
            rf"(?im)^family:\s*`?({escaped})`?\s*$",
            rf"(?im)\b(return to|switch to|stay in|keep the family and test)\s+`?({escaped})`?",
        ]
        for pattern in explicit_patterns:
            match = re.search(pattern, research_note_text)
            if not match:
                continue
            assert match.lastindex is not None
            family = match.group(match.lastindex)
            if family:
                return family
        section_match = re.search(
            r"(?ims)^##\s+(Proposed next experiment|What To Test|What to test)\s*\n(.*?)(?=^##\s+|\Z)",
            research_note_text,
        )
        if section_match:
            section = section_match.group(2)
            for family in families:
                if family in section:
                    return family
        return None

    def _apply_planner_gate_spec(
        self,
        *,
        payload: dict[str, Any],
        planner_contract: PlannerOutput,
    ) -> dict[str, Any]:
        planner_regime_gates = self._dict_or_empty(
            planner_contract.get("planner_regime_gates") or planner_contract.get("regime_gates")
        )
        expected_entries = list(planner_regime_gates.get("entry") or [])
        if not expected_entries:
            return payload
        normalized = json_clone(payload)
        regime_gates = self._dict_or_empty(normalized.get("regime_gates"))
        actual_entries = list(regime_gates.get("entry") or [])
        if not actual_entries:
            return cast(dict[str, Any], normalized)
        rewritten_entries: list[Any] = []
        for gate in actual_entries:
            gate_expression = ""
            if isinstance(gate, str):
                gate_expression = gate.strip()
            elif isinstance(gate, dict):
                gate_expression = str(gate.get("expression") or "").strip()
            if not gate_expression:
                rewritten_entries.append(gate)
                continue
            expected_match = None
            for expected in expected_entries:
                if not isinstance(expected, dict):
                    continue
                if str(expected.get("expression") or "").strip() == gate_expression:
                    expected_match = expected
                    break
            if expected_match is None:
                rewritten_entries.append(gate)
                continue
            if expected_match.get("min") is None and expected_match.get("max") is None:
                rewritten_entries.append(gate)
                continue
            rewritten: dict[str, object] = {
                "expression": gate_expression,
            }
            min_val = expected_match.get("min")
            if min_val is not None:
                rewritten["min"] = min_val
            max_val = expected_match.get("max")
            if max_val is not None:
                rewritten["max"] = max_val
            rewritten_entries.append(rewritten)
        regime_gates["entry"] = rewritten_entries
        normalized["regime_gates"] = regime_gates
        return cast(dict[str, Any], normalized)

    def _cookbook_paths(self, *, session: WorkspaceSession, family: str) -> list[Path]:
        prompt_module = family_prompt_module(self.mutator._family_spec(session.track, family)) or ""
        mapping = {
            "basket_neutral": session.cookbooks_dir / "basket_neutral_patterns.md",
            "directional": session.cookbooks_dir / "directional_patterns.md",
            "carry": session.cookbooks_dir / "carry_patterns.md",
        }
        paths: list[Path] = []
        if family in {
            "perp_pair_trade_unlevered",
            "perp_pair_trade_levered",
        }:
            paths.append(session.cookbooks_dir / "pair_trade_patterns.md")
        if prompt_module in mapping:
            paths.append(mapping[prompt_module])
        deduped: list[Path] = []
        seen: set[Path] = set()
        for path in paths:
            if path in seen or not path.exists():
                continue
            seen.add(path)
            deduped.append(path)
        return deduped

    def _get_by_path(self, payload: dict[str, Any], path: str) -> Any:
        current: Any = payload
        for part in path.split("."):
            if not isinstance(current, dict):
                return None
            current = current.get(part)
        return current

    async def _gate_lint(
        self,
        *,
        session: WorkspaceSession,
        parent: Any,
        spec: SignalSpec,
    ) -> dict[str, Any]:
        if self.hypothesis_sandbox is None:
            return {}
        arguments = {
            "family": spec.family,
            "neutrality_basis": spec.neutrality_basis,
            "basis_groups": list(spec.universe.basis_groups),
            "features": list(spec.features),
            "regime_gates": dict(spec.regime_gates or {}),
            "params": dict(spec.params or {}),
        }
        result = await self.hypothesis_sandbox._tool_probe_spec_gate_impact(
            track=session.track,
            parent=parent,
            arguments=arguments,
        )
        if not isinstance(result, dict):
            return {"ok": False, "error": "gate_lint_failed"}
        warnings = list(result.get("warnings") or [])
        selector_train_delta = dict(
            ((result.get("selector_train_comparison") or {}).get("delta") or {})
        )
        if float(selector_train_delta.get("median_total_return") or 0.0) < 0.0:
            warnings.append("negative_selector_train_return_delta")
        if float(selector_train_delta.get("median_sharpe") or 0.0) < 0.0:
            warnings.append("negative_selector_train_sharpe_delta")
        return {
            "ok": bool(result.get("ok")),
            "warnings": warnings,
            "gate_coverage": dict(result.get("gate_coverage") or {}),
            "selector_train_delta": selector_train_delta,
        }

    async def _activity_lint(
        self,
        *,
        spec: SignalSpec,
    ) -> dict[str, Any]:
        if self.hypothesis_sandbox is None:
            return {"ok": False, "error": "activity_lint_unavailable"}
        try:
            compiled = await compile_spec(
                self.settings,
                self.hypothesis_sandbox.provider,
                spec,
            )
        except (AttributeError, KeyError, RuntimeError, TypeError, ValueError) as exc:
            return {
                "ok": False,
                "error": f"compile_failed: {type(exc).__name__}: {exc}",
            }
        positions = compiled.target_positions.fillna(0.0)
        if positions.empty:
            return {"ok": True, "active_bar_fraction": 0.0, "median_active_asset_count": 0.0}
        active_mask = positions.abs().sum(axis=1) > 1e-12
        active_fraction = float(active_mask.mean()) if len(active_mask.index) else 0.0
        active_counts = positions.ne(0.0).sum(axis=1)
        active_counts = active_counts[active_mask]
        return {
            "ok": True,
            "active_bar_fraction": active_fraction,
            "median_active_asset_count": float(active_counts.median()) if not active_counts.empty else 0.0,
        }



