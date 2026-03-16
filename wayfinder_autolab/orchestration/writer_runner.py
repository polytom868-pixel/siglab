from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wayfinder_autolab.evaluator.compile import compile_candidate
from wayfinder_autolab.families import family_prompt_module
from wayfinder_autolab.llm import KimiClient
from wayfinder_autolab.models import CandidateGraph
from wayfinder_autolab.orchestration.contracts import conformance_violations, motif_signature
from wayfinder_autolab.orchestration.trials import build_candidate_patch, summarize_patch
from wayfinder_autolab.research import HypothesisSandbox
from wayfinder_autolab.search.mutate import CandidateMutator
from wayfinder_autolab.workspace.cards import dump_yaml_block, parse_frontmatter
from wayfinder_autolab.workspace.builder import WorkspaceSession


@dataclass
class WriterResult:
    candidate_payload: dict[str, Any] | None
    candidate_path: Path | None
    trace_path: Path
    accepted: bool
    base_candidate_payload: dict[str, Any] | None = None
    base_candidate_path: Path | None = None
    structure_spec: dict[str, Any] | None = None
    patch_payload: dict[str, Any] | None = None
    patch_summary: list[str] | None = None
    candidate_after_patch_path: Path | None = None
    failure_reason: str | None = None
    failure_packet: dict[str, Any] | None = None


@dataclass
class PreflightResult:
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


class CandidateWriterRunner:
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
        kimi: KimiClient,
        mutator: CandidateMutator,
        hypothesis_sandbox: HypothesisSandbox | None = None,
    ) -> None:
        self.settings = settings
        self.kimi = kimi
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
        base_candidate_payload: dict[str, Any] | None = None,
    ) -> WriterResult:
        research_note_text = research_note_path.read_text()
        planner_contract_path = iteration_paths.get("planner_contract_path")
        planner_contract = self._load_planner_contract(
            planner_contract_path=planner_contract_path,
            research_note_text=research_note_text,
            families=session.families,
            parent=parent,
        )
        family = str(planner_contract.get("target_family") or parent.family)
        base_payload = self._clone_payload(base_candidate_payload or parent.canonical_dict())
        base_candidate_path = iteration_paths["base_candidate_path"]
        base_candidate_path.write_text(dump_yaml_block(base_payload) + "\n")
        structure_spec = self._build_structure_spec(
            family=family,
            planner_contract=planner_contract,
            research_note_path=research_note_path,
            base_candidate_payload=base_payload,
        )
        structure_spec_path = iteration_paths["structure_spec_path"]
        structure_spec_path.write_text(
            json.dumps(structure_spec, indent=2, ensure_ascii=True, default=str)
        )
        manifest_path = session.manifests_dir / "family" / f"{family}.md"
        family_contract_path = session.manifests_dir / "family" / f"{family}.json"
        family_feature_manifest_path = session.manifests_dir / "features" / "family" / f"{family}.md"
        family_feature_contract_path = session.manifests_dir / "features" / "family" / f"{family}.json"
        constraints_path = session.manifests_dir / "constraints.md"
        regime_catalog_path = session.manifests_dir / "regime_catalog.md"
        policy_surface_path = session.manifests_dir / "policy_surface.md"
        cookbook_paths = self._cookbook_paths(session=session, family=family)
        candidate_schema_path = (
            self.settings.root_dir
            / ".agents"
            / "skills"
            / "autolab-candidate-writer"
            / "templates"
            / "candidate_schema.json"
        )
        system_prompt = (
            self.settings.root_dir
            / ".agents"
            / "skills"
            / "autolab-candidate-writer"
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
            candidate_schema_path=candidate_schema_path,
            planner_contract=planner_contract,
            base_candidate_payload=base_payload,
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

        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            parse_error: str | None = None
            payload: dict[str, Any] | None = None
            try:
                payload = await self.kimi.complete_json_messages(
                    system_prompt=system_prompt,
                    messages=messages,
                    max_tokens=900,
                    timeout_s=max(self.settings.kimi_timeout_s, 90.0),
                    json_mode=True,
                    thinking_override="disabled",
                )
            except Exception as exc:  # noqa: BLE001
                parse_error = f"{type(exc).__name__}: {exc}"

            preflight = await self._preflight_candidate(
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
                    "kimi_trace": dict(self.kimi.last_trace or {}),
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
            repair_packet_path.write_text(
                json.dumps(latest_repair_packet, indent=2, ensure_ascii=True, default=str)
            )

            if attempt >= self.MAX_ATTEMPTS:
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

        candidate_path: Path | None = None
        failure_reason: str | None = None
        accepted = final_payload is not None
        patch_payload: dict[str, Any] | None = None
        patch_summary: list[str] = []
        candidate_after_patch_path: Path | None = None
        if final_payload is None:
            if last_preflight is not None and (last_preflight.hard_issues or last_preflight.conformance_issues):
                failure_reason = (
                    "Writer failed planner/writer conformance after repair: "
                    + "; ".join(list(last_preflight.hard_issues) + list(last_preflight.conformance_issues))
                )
            elif fallback_payload is None:
                failure_reason = "Writer failed to produce a valid candidate JSON object"
            else:
                failure_reason = "Writer fell back to a deterministic payload but did not satisfy preflight"
            candidate_payload = fallback_payload
        else:
            candidate_payload = final_payload
            candidate_path = iteration_paths["candidate_json_path"]
            candidate_path.write_text(json.dumps(candidate_payload, indent=2, ensure_ascii=True, default=str))
        if candidate_payload is not None:
            patch_payload = build_candidate_patch(
                base_payload=base_payload,
                target_payload=candidate_payload,
            )
            patch_summary = summarize_patch(patch_payload)
            iteration_paths["candidate_patch_path"].write_text(
                json.dumps(patch_payload, indent=2, ensure_ascii=True, default=str)
            )
            candidate_after_patch_path = iteration_paths["candidate_after_patch_path"]
            candidate_after_patch_path.write_text(dump_yaml_block(candidate_payload) + "\n")
        trace_path = iteration_paths["writer_trace_path"]
        parent_card_path = session.current_dir / "parent_card.md"
        trace_path.write_text(
            json.dumps(
                {
                    "stage": "writer",
                    "system_prompt_path": str(
                        (
                            self.settings.root_dir
                            / ".agents"
                            / "skills"
                            / "autolab-candidate-writer"
                            / "SKILL.md"
                        ).relative_to(self.settings.root_dir)
                    ),
                    "inputs": {
                        "system_prompt": system_prompt,
                        "initial_user_prompt": user_prompt,
                        "research_note_path": str(research_note_path),
                        "planner_contract_path": str(planner_contract_path) if planner_contract_path else None,
                        "structure_spec_path": str(structure_spec_path),
                        "base_candidate_path": str(base_candidate_path),
                        "parent_card_path": str(parent_card_path),
                        "manifest_path": str(manifest_path),
                        "family_contract_path": str(family_contract_path),
                        "family_feature_manifest_path": str(family_feature_manifest_path),
                        "family_feature_contract_path": str(family_feature_contract_path),
                        "constraints_path": str(constraints_path),
                        "regime_catalog_path": str(regime_catalog_path),
                        "policy_surface_path": str(policy_surface_path),
                        "cookbook_paths": [str(path) for path in cookbook_paths],
                        "candidate_schema_path": str(candidate_schema_path),
                        "evidence_paths": [str(path) for path in list(planner_contract.get("evidence_paths") or [])],
                        "planner_contract": planner_contract,
                    },
                    "outputs": {
                        "accepted": accepted,
                        "failure_reason": failure_reason,
                        "candidate_path": str(candidate_path) if candidate_path is not None else None,
                        "candidate_payload": candidate_payload,
                        "candidate_after_patch_path": (
                            str(candidate_after_patch_path) if candidate_after_patch_path is not None else None
                        ),
                        "candidate_patch_path": (
                            str(iteration_paths["candidate_patch_path"])
                            if patch_payload is not None
                            else None
                        ),
                        "patch_summary": patch_summary,
                        "structure_spec": structure_spec,
                        "repair_packet_path": str(repair_packet_path) if repair_packet_path.exists() else None,
                        "latest_repair_packet": latest_repair_packet,
                    },
                    "attempt_count": len(attempt_log),
                    "attempts": attempt_log,
                    "conversation_messages": messages,
                    "kimi_trace": dict(self.kimi.last_trace or {}),
                    "kimi_exchange": dict(self.kimi.last_exchange or {}),
                },
                indent=2,
                ensure_ascii=True,
                default=str,
            )
        )
        return WriterResult(
            candidate_payload=candidate_payload,
            candidate_path=candidate_path,
            trace_path=trace_path,
            accepted=accepted,
            base_candidate_payload=base_payload,
            base_candidate_path=base_candidate_path,
            structure_spec=structure_spec,
            patch_payload=patch_payload,
            patch_summary=patch_summary,
            candidate_after_patch_path=candidate_after_patch_path,
            failure_reason=failure_reason,
            failure_packet=latest_repair_packet,
        )

    async def _preflight_candidate(
        self,
        *,
        session: WorkspaceSession,
        payload: dict[str, Any] | None,
        parse_error: str | None,
        track: str,
        parent: Any,
        target_family: str,
        planner_contract: dict[str, Any],
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
            return PreflightResult(
                parse_error=parse_error,
                hard_issues=[f"parse error: {parse_error}"] if parse_error else ["empty candidate payload"],
                conformance_issues=[],
                gate_lint=None,
                changed_fields=[],
                harmless_changed_fields=[],
                material_changed_fields=[],
                validated_payload=None,
            )
        payload = self._apply_planner_gate_spec(payload=payload, planner_contract=planner_contract)
        extra_keys = sorted(set(payload) - self.TOP_LEVEL_KEYS)
        if extra_keys:
            hard_issues.append(f"unsupported top-level keys: {', '.join(extra_keys)}")
        missing_keys = sorted(self.TOP_LEVEL_KEYS - set(payload))
        if missing_keys:
            hard_issues.append(f"missing required top-level keys: {', '.join(missing_keys)}")
        try:
            raw_candidate = CandidateGraph.from_dict(payload)
        except Exception as exc:  # noqa: BLE001
            hard_issues.append(f"candidate schema parse failed: {type(exc).__name__}: {exc}")
            return PreflightResult(
                parse_error=None,
                hard_issues=hard_issues,
                conformance_issues=[],
                gate_lint=None,
                changed_fields=[],
                harmless_changed_fields=[],
                material_changed_fields=[],
                validated_payload=None,
            )
        if raw_candidate.family != target_family:
            hard_issues.append(
                f"family mismatch: expected `{target_family}` from the research note but got `{raw_candidate.family}`"
            )
        contract_issues.extend(
            conformance_violations(
                planner_contract=planner_contract,
                candidate_payload=payload,
                allowed_features=list(allowed_features_by_family.get(target_family) or []),
                parent_payload=parent.canonical_dict(),
            )
        )
        try:
            validated = self.mutator._validate_candidate(
                candidate=CandidateGraph.from_dict(payload),
                track=track,
                allowed_families=allowed_families,
                allowed_features_by_family=allowed_features_by_family,
                family_defaults=family_defaults,
            )
        except Exception as exc:  # noqa: BLE001
            hard_issues.append(f"validator rejected candidate: {type(exc).__name__}: {exc}")
            return PreflightResult(
                parse_error=None,
                hard_issues=hard_issues,
                conformance_issues=contract_issues,
                gate_lint=None,
                changed_fields=[],
                harmless_changed_fields=[],
                material_changed_fields=[],
                validated_payload=None,
            )

        raw_canonical = raw_candidate.canonical_dict()
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
                candidate=validated,
            )
            severe_gate_warnings = [
                warning
                for warning in list(gate_lint.get("warnings") or [])
                if warning in {
                    "regime_gates_are_effectively_always_open",
                    "regime_gates_are_extremely_restrictive",
                    "gated_candidate_is_near_flat",
                    "gates_do_not_change_train_outcomes",
                    "negative_selector_train_return_delta",
                    "negative_selector_train_sharpe_delta",
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
            activity_lint = await self._activity_lint(candidate=validated)
            if activity_lint.get("ok") and float(activity_lint.get("active_bar_fraction") or 0.0) < 0.01:
                hard_issues.append("activity_lint: candidate_is_near_flat")
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
        planner_contract: dict[str, Any],
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
        normalized = json.loads(json.dumps(payload, ensure_ascii=True, default=str))
        regime_gates = self._dict_or_empty(normalized.get("regime_gates"))
        if not regime_gates:
            normalized["regime_gates"] = {"entry": [], "exit_on_break": False}
            return normalized
        entry = regime_gates.get("entry")
        if entry is None or entry == []:
            regime_gates["entry"] = []
            regime_gates["exit_on_break"] = False
        normalized["regime_gates"] = regime_gates
        return normalized

    def _repair_prompt(
        self,
        *,
        repair_packet: dict[str, Any],
    ) -> str:
        return "\n".join(
            [
                "Your previous candidate is not acceptable yet.",
                f"Target family remains `{repair_packet.get('family')}`.",
                "Return one corrected JSON object only.",
                "Do not include explanations or markdown.",
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
        candidate_schema_path: Path,
        planner_contract: dict[str, Any],
        base_candidate_payload: dict[str, Any],
        evidence_paths: list[str],
        planner_regime_gates: dict[str, Any],
        workspace_root: Path,
    ) -> str:
        parts = [
            "Emit exactly one candidate JSON object that satisfies the schema and the chosen family manifest.",
            "The research note is free-form. The extracted planner contract below is binding if it conflicts with the prose.",
            "Treat the base candidate below as the starting point for this trial. Preserve its working structure unless the research note calls for a clear structural change.",
            "Kimi owns structural changes here: family, feature set, universe, gate expressions, and other discrete design choices.",
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
            "Some policy fields may be locally swept by the evaluator. Choose coherent starting values rather than knife-edge thresholds.",
            "",
            "## Research Note",
            research_note_text,
            "",
            "## Extracted Planner Contract",
            dump_yaml_block(planner_contract),
            "",
            "## Base Candidate",
            dump_yaml_block(base_candidate_payload),
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
            "## Candidate Schema",
            candidate_schema_path.read_text(),
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
        planner_contract: dict[str, Any],
        research_note_path: Path,
        base_candidate_payload: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "target_family": family,
            "research_note_path": str(research_note_path),
            "base_candidate_hash": self._candidate_hash(base_candidate_payload),
            "base_candidate_family": str(base_candidate_payload.get("family") or ""),
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

    def _candidate_hash(self, payload: dict[str, Any]) -> str | None:
        try:
            return CandidateGraph.from_dict(payload).strategy_hash()
        except Exception:
            return None

    def _clone_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return json.loads(json.dumps(payload, ensure_ascii=True, default=str))

    def _load_planner_contract(
        self,
        *,
        planner_contract_path: Path | None,
        research_note_text: str,
        families: list[str],
        parent: Any,
    ) -> dict[str, Any]:
        if planner_contract_path and planner_contract_path.exists():
            try:
                payload = json.loads(planner_contract_path.read_text())
            except Exception:  # noqa: BLE001
                payload = {}
            if isinstance(payload, dict):
                contract = dict(payload)
                note_family = self._family_from_note(research_note_text=research_note_text, families=families)
                if note_family:
                    contract["target_family"] = note_family
                return contract
        note_frontmatter: dict[str, Any]
        try:
            note_frontmatter, _body = parse_frontmatter(research_note_text)
        except Exception:
            note_frontmatter = {}
        return {
            "target_family": str(note_frontmatter.get("target_family") or parent.family),
            "target_trade_style": note_frontmatter.get("target_trade_style"),
            "must_answer": note_frontmatter.get("must_answer"),
            "core_hypothesis": note_frontmatter.get("core_hypothesis"),
            "informative_test": note_frontmatter.get("informative_test"),
            "required_feature_roles": list(note_frontmatter.get("required_feature_roles") or []),
            "required_features": list(note_frontmatter.get("required_features") or []),
            "forbidden_features": list(note_frontmatter.get("forbidden_features") or []),
            "forbidden_motifs": list(note_frontmatter.get("forbidden_motifs") or []),
            "gate_intent": self._dict_or_empty(note_frontmatter.get("gate_intent")),
            "required_gate_dimensions": list(note_frontmatter.get("required_gate_dimensions") or []),
            "planner_regime_gates": self._dict_or_empty(
                note_frontmatter.get("planner_regime_gates") or note_frontmatter.get("regime_gates")
            ),
            "required_variation_axis": note_frontmatter.get("required_variation_axis"),
            "banned_motif_signatures": list(note_frontmatter.get("banned_motif_signatures") or []),
            "writer_inputs": list(note_frontmatter.get("writer_inputs") or []),
            "evidence_paths": list(note_frontmatter.get("evidence_paths") or []),
            "tracking_tags": list(note_frontmatter.get("tracking_tags") or []),
        }

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
        planner_contract: dict[str, Any],
    ) -> dict[str, Any]:
        planner_regime_gates = self._dict_or_empty(
            planner_contract.get("planner_regime_gates") or planner_contract.get("regime_gates")
        )
        expected_entries = list(planner_regime_gates.get("entry") or [])
        if not expected_entries:
            return payload
        normalized = json.loads(json.dumps(payload, ensure_ascii=True, default=str))
        regime_gates = self._dict_or_empty(normalized.get("regime_gates"))
        actual_entries = list(regime_gates.get("entry") or [])
        if not actual_entries:
            return normalized
        rewritten_entries: list[Any] = []
        for gate in actual_entries:
            gate_entry = gate
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
            rewritten = {
                "expression": gate_expression,
            }
            if expected_match.get("min") is not None:
                rewritten["min"] = expected_match.get("min")
            if expected_match.get("max") is not None:
                rewritten["max"] = expected_match.get("max")
            rewritten_entries.append(rewritten)
        regime_gates["entry"] = rewritten_entries
        normalized["regime_gates"] = regime_gates
        return normalized

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
        candidate: CandidateGraph,
    ) -> dict[str, Any]:
        if self.hypothesis_sandbox is None:
            return {}
        arguments = {
            "family": candidate.family,
            "neutrality_basis": candidate.neutrality_basis,
            "basis_groups": list(candidate.universe.basis_groups),
            "features": list(candidate.features),
            "regime_gates": dict(candidate.regime_gates or {}),
            "params": dict(candidate.params or {}),
        }
        result = await self.hypothesis_sandbox._tool_probe_candidate_gate_impact(
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
        candidate: CandidateGraph,
    ) -> dict[str, Any]:
        if self.hypothesis_sandbox is None:
            return {"ok": False, "error": "activity_lint_unavailable"}
        try:
            compiled = await compile_candidate(
                self.settings,
                self.hypothesis_sandbox.provider,
                candidate,
            )
        except Exception as exc:  # noqa: BLE001
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
