from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from wayfinder_autolab.llm import KimiClient, KimiTool
from wayfinder_autolab.orchestration.contracts import extract_embedded_yaml_block
from wayfinder_autolab.research import HypothesisSandbox, WebResearcher
from wayfinder_autolab.tools import (
    inspect_feature,
    open_workspace_file,
    search_features,
    search_workspace,
    suggest_feature_set,
)
from wayfinder_autolab.workspace.cards import parse_frontmatter
from wayfinder_autolab.workspace.builder import WorkspaceBuilder, WorkspaceSession


@dataclass
class PlannerResult:
    research_note_path: Path
    planner_contract_path: Path
    trace_path: Path
    frontmatter: dict[str, Any]
    tool_refs: list[str]
    evidence_paths: list[str]
    repaired: bool = False


class ResearchPlannerRunner:
    MAX_REPAIR_ATTEMPTS = 5
    DEFAULT_FILES = (
        "RUNBOOK.md",
        "TASK.md",
        "WORKSPACE_INDEX.md",
        "current/SESSION_STATE.json",
        "current/frontier_brief.md",
        "current/market_brief.md",
        "current/parent_card.md",
        "current/families_index.md",
        "current/incumbent_candidate.yaml",
        "current/family_incumbents.json",
        "current/recent_trials.md",
        "manifests/regime_catalog.md",
        "manifests/policy_surface.md",
        "manifests/features/feature_surface.md",
    )
    ACTION_KEYWORDS = (
        "test ",
        "try ",
        "use ",
        "switch ",
        "return to ",
        "branch ",
        "add ",
        "remove ",
        "replace ",
        "gate ",
        "suppress ",
        "what to test",
        "proposed next experiment",
        "next test",
    )

    def __init__(
        self,
        *,
        settings: Any,
        kimi: KimiClient,
        hypothesis_sandbox: HypothesisSandbox,
        web_researcher: WebResearcher,
        workspace_builder: WorkspaceBuilder,
    ) -> None:
        self.settings = settings
        self.kimi = kimi
        self.hypothesis_sandbox = hypothesis_sandbox
        self.web_researcher = web_researcher
        self.workspace_builder = workspace_builder

    async def run(
        self,
        *,
        session: WorkspaceSession,
        iteration_number: int,
        parent: Any,
        market_bundle: dict[str, Any],
        iteration_paths: dict[str, Any],
        repair_feedback: dict[str, Any] | None = None,
        previous_note_path: Path | None = None,
    ) -> PlannerResult:
        skill_path = self.settings.root_dir / ".agents" / "skills" / "autolab-research-planner" / "SKILL.md"
        system_prompt = skill_path.read_text()
        current_state = json.loads((session.current_dir / "SESSION_STATE.json").read_text())
        default_context_files = [
            *self.DEFAULT_FILES,
            f"manifests/family/{parent.family}.md",
        ]
        search_mode = str(current_state.get("search_mode") or "refine")
        thinking_override = "enabled" if search_mode in {"family_switch", "contradiction_resolution"} else "disabled"
        tool_refs: list[str] = []
        tools = self._planner_tools(
            session=session,
            iteration_number=iteration_number,
            parent=parent,
            market_bundle=market_bundle,
            tool_refs=tool_refs,
        )

        attempts: list[dict[str, Any]] = []
        next_repair_feedback = dict(repair_feedback or {}) if repair_feedback is not None else None
        final_note = ""
        final_contract: dict[str, Any] = {}
        final_raw_content = ""
        final_raw_frontmatter: dict[str, Any] = {}
        final_yaml_fragments: list[dict[str, Any]] = []
        repaired = repair_feedback is not None

        for attempt_number in range(1, self.MAX_REPAIR_ATTEMPTS + 1):
            user_prompt = (
                self._build_repair_prompt(
                    session=session,
                    parent=parent,
                    previous_note_path=previous_note_path,
                    repair_feedback=next_repair_feedback or {},
                )
                if next_repair_feedback is not None
                else self._build_user_prompt(session=session, parent=parent)
            )
            raw_content = await self.kimi.complete_text_with_tools(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                tools=tools,
                max_tokens=1800,
                timeout_s=max(self.settings.kimi_timeout_s, 120.0),
                max_tool_rounds=self.settings.kimi_max_tool_rounds,
                thinking_override=thinking_override,
            )
            raw_frontmatter, body_text = self._safe_parse_frontmatter(raw_content)
            yaml_fragments = self._extract_yaml_fragments(raw_content)
            planner_contract = self._extract_planner_contract(
                note_text=raw_content,
                note_body=body_text,
                raw_frontmatter=raw_frontmatter,
                yaml_fragments=yaml_fragments,
                parent=parent,
                current_state=current_state,
                tool_refs=tool_refs,
                session=session,
            )
            semantic_issues = self._semantic_note_issues(
                note_text=raw_content,
                planner_contract=planner_contract,
            )
            attempt_payload = {
                "attempt_number": attempt_number,
                "repair_feedback": dict(next_repair_feedback or {}),
                "inputs": {"user_prompt": user_prompt},
                "tool_names": [tool.name for tool in tools],
                "outputs": {
                    "raw_research_note": raw_content,
                    "raw_frontmatter": raw_frontmatter,
                    "yaml_fragments": yaml_fragments,
                    "planner_contract": planner_contract,
                },
                "kimi_trace": dict(self.kimi.last_trace or {}),
                "kimi_exchange": dict(self.kimi.last_exchange or {}),
            }
            if semantic_issues:
                repaired = True
                next_repair_feedback = self._planner_failure_feedback(
                    parent=parent,
                    current_state=current_state,
                    previous_feedback=next_repair_feedback,
                    raw_content=raw_content,
                    attempt_number=attempt_number,
                    semantic_issues=semantic_issues,
                )
                attempt_payload["success"] = False
                attempt_payload["error"] = dict(next_repair_feedback)
                attempts.append(attempt_payload)
                continue

            final_note = raw_content.strip() or self._fallback_note(
                parent=parent,
                current_state=current_state,
            )
            final_contract = planner_contract
            final_raw_content = raw_content
            final_raw_frontmatter = raw_frontmatter
            final_yaml_fragments = yaml_fragments
            attempt_payload["success"] = True
            attempts.append(attempt_payload)
            repaired = repaired or attempt_number > 1
            break
        else:
            final_note = self._fallback_note(
                parent=parent,
                current_state=current_state,
            )
            final_contract = self._fallback_contract(
                parent=parent,
                current_state=current_state,
                tool_refs=tool_refs,
            )
            final_raw_content = attempts[-1]["outputs"]["raw_research_note"] if attempts else ""
            final_raw_frontmatter = (
                dict(attempts[-1]["outputs"]["raw_frontmatter"]) if attempts else {}
            )
            final_yaml_fragments = (
                list(attempts[-1]["outputs"]["yaml_fragments"]) if attempts else []
            )

        research_note_path = iteration_paths["research_note_path"]
        research_note_path.write_text(final_note.strip() + "\n")
        planner_contract_path = iteration_paths["planner_contract_path"]
        planner_contract_path.write_text(
            json.dumps(final_contract, indent=2, ensure_ascii=True, default=str)
        )
        trace_path = iteration_paths["planner_trace_path"]
        self._write_trace(
            trace_path=trace_path,
            skill_path=skill_path,
            system_prompt=system_prompt,
            default_context_files=default_context_files,
            tools=tools,
            note_text=final_note,
            raw_content=final_raw_content,
            raw_frontmatter=final_raw_frontmatter,
            yaml_fragments=final_yaml_fragments,
            planner_contract=final_contract,
            tool_refs=tool_refs,
            initial_repair_feedback=repair_feedback,
            attempts=attempts,
            used_fallback_note=not bool(final_raw_content.strip()),
        )
        evidence_paths = [
            str(path)
            for path in list(final_contract.get("evidence_paths") or [])
            if isinstance(path, str) and path.strip()
        ]
        return PlannerResult(
            research_note_path=research_note_path,
            planner_contract_path=planner_contract_path,
            trace_path=trace_path,
            frontmatter=final_contract,
            tool_refs=tool_refs,
            evidence_paths=evidence_paths,
            repaired=repaired,
        )

    def _build_user_prompt(self, *, session: WorkspaceSession, parent: Any) -> str:
        parts = [
            "Write one research note in normal markdown.",
            "Do not emit candidate JSON.",
            "You may include one small fenced yaml block if it helps pin down required features, gates, or the intended family, but it is optional.",
            "Focus on what to test and why, not on exact candidate syntax.",
            "Make the note concrete enough that a deterministic extractor and the writer can preserve the intended family, features, and gate dimensions.",
            "Optimize for aggregate_score, which weights median_sharpe*1.0, median_total_return*4.0, median_calmar*0.5, asset_breadth*0.1, profitable_window_pct*0.25, and worst_max_drawdown*1.5.",
            "Use recent_trials to avoid repeating failed patches and to build on structure that Optuna already improved.",
        ]
        for rel_path in [*self.DEFAULT_FILES, f"manifests/family/{parent.family}.md"]:
            path = session.root / rel_path
            if not path.exists():
                continue
            parts.extend(["", f"## {rel_path}", path.read_text()[:9000]])
        return "\n".join(parts)

    def _build_repair_prompt(
        self,
        *,
        session: WorkspaceSession,
        parent: Any,
        previous_note_path: Path | None,
        repair_feedback: dict[str, Any],
    ) -> str:
        previous_note = previous_note_path.read_text() if previous_note_path and previous_note_path.exists() else ""
        parts = [
            "Rewrite the research note after downstream failure.",
            "Keep it as normal markdown. Candidate JSON is not allowed.",
            "The failure packet shows what the writer or preflight could not preserve.",
            "State one clear next test. If a specific family, feature, or gate dimension matters, say it explicitly in the note.",
            "You may call planner tools again if they help repair the plan.",
            "",
            "## Failure Packet",
            json.dumps(repair_feedback, indent=2, ensure_ascii=True, default=str),
        ]
        if previous_note:
            parts.extend(["", "## Previous Research Note", previous_note])
        for rel_path in [
            "TASK.md",
            "current/SESSION_STATE.json",
            "current/frontier_brief.md",
            "current/incumbent_candidate.yaml",
            "current/recent_trials.md",
            "current/parent_card.md",
            f"manifests/family/{parent.family}.md",
        ]:
            path = session.root / rel_path
            if not path.exists():
                continue
            parts.extend(["", f"## {rel_path}", path.read_text()[:7000]])
        return "\n".join(parts)

    def _planner_tools(
        self,
        *,
        session: WorkspaceSession,
        iteration_number: int,
        parent: Any,
        market_bundle: dict[str, Any],
        tool_refs: list[str],
    ) -> list[KimiTool]:
        tools: list[KimiTool] = []
        tools.append(
            KimiTool(
                name="search_workspace",
                description="Search workspace cards and indexes by query and optional filters.",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "kind": {"type": "string"},
                        "family": {"type": "string"},
                        "outcome": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                    },
                    "required": ["query"],
                },
                handler=lambda arguments: search_workspace(
                    workspace_root=session.root,
                    query=str(arguments.get("query") or ""),
                    kind=str(arguments.get("kind") or "") or None,
                    family=str(arguments.get("family") or "") or None,
                    outcome=str(arguments.get("outcome") or "") or None,
                    limit=int(arguments.get("limit", 8)),
                ),
            )
        )
        tools.append(
            KimiTool(
                name="open_file",
                description="Open a workspace file by relative path, optionally extracting one heading section.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "section": {"type": "string"},
                        "max_chars": {"type": "integer", "minimum": 100, "maximum": 20000},
                    },
                    "required": ["path"],
                },
                handler=lambda arguments: open_workspace_file(
                    workspace_root=session.root,
                    path=str(arguments.get("path") or ""),
                    section=str(arguments.get("section") or "") or None,
                    max_chars=int(arguments.get("max_chars", 6000))
                    if arguments.get("max_chars") is not None
                    else None,
                ),
            )
        )
        tools.append(
            KimiTool(
                name="search_features",
                description="Search the generated feature catalog by semantic query and optional family/kind filters.",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "family": {"type": "string"},
                        "kind": {"type": "string"},
                        "subkind": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                    },
                    "required": ["query"],
                },
                handler=lambda arguments: search_features(
                    workspace_root=session.root,
                    query=str(arguments.get("query") or ""),
                    family=str(arguments.get("family") or "") or None,
                    kind=str(arguments.get("kind") or "") or None,
                    subkind=str(arguments.get("subkind") or "") or None,
                    limit=int(arguments.get("limit", 8)),
                ),
            )
        )
        tools.append(
            KimiTool(
                name="inspect_feature",
                description="Inspect one feature alias in the generated feature catalog.",
                parameters={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "family": {"type": "string"},
                    },
                    "required": ["name"],
                },
                handler=lambda arguments: inspect_feature(
                    workspace_root=session.root,
                    name=str(arguments.get("name") or ""),
                    family=str(arguments.get("family") or "") or None,
                ),
            )
        )
        tools.append(
            KimiTool(
                name="suggest_feature_set",
                description="Suggest a small semantically diverse feature set from the feature catalog for a given family and hypothesis.",
                parameters={
                    "type": "object",
                    "properties": {
                        "family": {"type": "string"},
                        "hypothesis": {"type": "string"},
                        "avoid": {"type": "array", "items": {"type": "string"}},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 8},
                    },
                    "required": ["family", "hypothesis"],
                },
                handler=lambda arguments: suggest_feature_set(
                    workspace_root=session.root,
                    family=str(arguments.get("family") or ""),
                    hypothesis=str(arguments.get("hypothesis") or ""),
                    avoid=[str(item) for item in list(arguments.get("avoid") or [])],
                    limit=int(arguments.get("limit", 4)),
                ),
            )
        )

        sandbox_tools = {
            tool.name: tool
            for tool in self.hypothesis_sandbox.kimi_tools(track=session.track, parent=parent)
            if tool.name in {
                "probe_feature_forward_stats",
                "probe_candidate_gate_impact",
                "compare_intended_vs_frozen_candidate",
            }
        }
        for tool_name in [
            "probe_feature_forward_stats",
            "probe_candidate_gate_impact",
            "compare_intended_vs_frozen_candidate",
        ]:
            tool = sandbox_tools.get(tool_name)
            if tool is None:
                continue
            tools.append(
                KimiTool(
                    name=tool.name,
                    description=tool.description,
                    parameters=tool.parameters,
                    handler=self._wrap_probe_tool(
                        session=session,
                        iteration_number=iteration_number,
                        parent=parent,
                        market_bundle=market_bundle,
                        tool=tool,
                        tool_refs=tool_refs,
                    ),
                )
            )

        if self.web_researcher.is_configured:
            tools.append(
                KimiTool(
                    name="web_search",
                    description="Search the public web for current information.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "max_results": {"type": "integer", "minimum": 1, "maximum": 5},
                        },
                        "required": ["query"],
                    },
                    handler=lambda arguments: self.web_researcher._tool_tavily_search(arguments),
                )
            )
            tools.append(
                KimiTool(
                    name="web_fetch",
                    description="Fetch and summarize a specific public URL.",
                    parameters={
                        "type": "object",
                        "properties": {"url": {"type": "string"}},
                        "required": ["url"],
                    },
                    handler=lambda arguments: self.web_researcher._tool_web_fetch(arguments),
                )
            )

        tools.append(
            KimiTool(
                name="think",
                description="Write a short private reasoning note and return it unchanged.",
                parameters={
                    "type": "object",
                    "properties": {"note": {"type": "string"}},
                    "required": ["note"],
                },
                handler=lambda arguments: {"ok": True, "note": str(arguments.get("note") or "")},
            )
        )
        return tools

    def _wrap_probe_tool(
        self,
        *,
        session: WorkspaceSession,
        iteration_number: int,
        parent: Any,
        market_bundle: dict[str, Any],
        tool: KimiTool,
        tool_refs: list[str],
    ):
        async def _handler(arguments: dict[str, Any]) -> Any:
            outcome = tool.handler(arguments)
            result = await outcome if hasattr(outcome, "__await__") else outcome
            probe_ref = self.workspace_builder.record_probe(
                session=session,
                iteration_number=iteration_number,
                probe_type=tool.name,
                family=parent.family,
                universe=list(parent.universe.basis_groups),
                bundle_id=str(market_bundle.get("bundle_id") or ""),
                arguments=arguments,
                result=dict(result or {}),
                tracking_tags=[parent.family, tool.name],
            )
            tool_refs.append(probe_ref)
            enriched = dict(result or {})
            enriched["workspace_probe_ref"] = probe_ref
            return enriched

        return _handler

    def _safe_parse_frontmatter(self, text: str) -> tuple[dict[str, Any], str]:
        try:
            frontmatter, body = parse_frontmatter(text)
        except Exception:
            return {}, text
        return dict(frontmatter or {}), body

    def _extract_yaml_fragments(self, text: str) -> list[dict[str, Any]]:
        fragments: list[dict[str, Any]] = []
        direct_fragment = extract_embedded_yaml_block(text)
        if direct_fragment:
            fragments.append(direct_fragment)
        for match in re.finditer(r"```yaml\s*\n(.*?)```", text, flags=re.DOTALL | re.IGNORECASE):
            blob = match.group(1).strip()
            if blob.startswith("---"):
                blob = blob[3:].lstrip()
            if blob.endswith("---"):
                blob = blob[:-3].rstrip()
            try:
                parsed = yaml.safe_load(blob) or {}
            except Exception:
                continue
            if isinstance(parsed, dict) and parsed not in fragments:
                fragments.append(dict(parsed))
        return fragments

    def _extract_planner_contract(
        self,
        *,
        note_text: str,
        note_body: str,
        raw_frontmatter: dict[str, Any],
        yaml_fragments: list[dict[str, Any]],
        parent: Any,
        current_state: dict[str, Any],
        tool_refs: list[str],
        session: WorkspaceSession,
    ) -> dict[str, Any]:
        contract = self._fallback_contract(
            parent=parent,
            current_state=current_state,
            tool_refs=tool_refs,
        )
        explicit_keys = self._explicit_contract_keys(raw_frontmatter, yaml_fragments)
        contract = self._merge_hint_fragment(contract, raw_frontmatter)
        for fragment in yaml_fragments:
            contract = self._merge_hint_fragment(contract, fragment)

        note_body_text = note_body if note_body.strip() else note_text
        body_family = self._body_family_override(note_body_text, session.families)
        if body_family:
            contract["target_family"] = body_family

        target_family = str(contract.get("target_family") or parent.family)
        body_trade_style = self._body_trade_style(note_body_text)
        if body_trade_style and contract.get("target_trade_style") in (None, "", "null"):
            contract["target_trade_style"] = body_trade_style

        if not self._string_list(contract.get("target_universe")):
            contract["target_universe"] = list(parent.universe.basis_groups)

        if tool_refs:
            contract["tools_used"] = self._unique_strings(
                [*self._string_list(contract.get("tools_used")), "workspace_tools"]
            )
        if not self._string_list(contract.get("evidence_paths")):
            contract["evidence_paths"] = self._unique_strings(
                [
                    *self._string_list(current_state.get("selected_lesson_refs")),
                    *self._string_list(current_state.get("selected_probe_refs")),
                    *tool_refs,
                ]
            )

        allowed_features = self.workspace_builder.mutator._allowed_features_by_family(
            session.track,
            family=[target_family],
        ).get(target_family, [])

        if not str(contract.get("core_hypothesis") or "").strip():
            contract["core_hypothesis"] = self._section_or_fallback(
                note_body_text,
                headings=("Diagnosis", "Hypothesis"),
                fallback=str(current_state.get("open_question") or f"Refine {target_family}"),
            )
        if not str(contract.get("informative_test") or "").strip():
            contract["informative_test"] = self._section_or_fallback(
                note_body_text,
                headings=("Proposed next experiment", "What to test", "Next test"),
                fallback="Test one concrete change that resolves the current open question.",
            )
        if not self._string_list(contract.get("expected_success")):
            contract["expected_success"] = ["better validation robustness"]
        if not self._string_list(contract.get("expected_failure")):
            contract["expected_failure"] = ["no measurable change"]

        gate_intent = self._dict_value(contract.get("gate_intent"))
        if gate_intent and not self._string_list(contract.get("required_gate_dimensions")):
            target_dimension = str(gate_intent.get("target_dimension") or "").strip()
            if target_dimension:
                contract["required_gate_dimensions"] = [target_dimension]

        if not str(contract.get("must_answer") or "").strip():
            contract["must_answer"] = self._last_question(note_body_text)
        if not str(contract.get("decision") or "").strip():
            contract["decision"] = (
                "branch_family" if target_family != parent.family else "refine_current_family"
            )
        if not str(contract.get("search_mode") or "").strip():
            contract["search_mode"] = str(current_state.get("search_mode") or "refine")
        if not self._string_list(contract.get("tracking_tags")):
            contract["tracking_tags"] = [target_family]

        explicit_feature_roles = "required_feature_roles" in explicit_keys
        explicit_required_features = "required_features" in explicit_keys
        explicit_required_gate_dimensions = "required_gate_dimensions" in explicit_keys
        explicit_gate_intent = "gate_intent" in explicit_keys
        explicit_regime_gates = bool(
            {"planner_regime_gates", "regime_gates"} & explicit_keys
        )

        if explicit_feature_roles:
            contract["required_feature_roles"] = self._normalize_required_feature_roles(
                family=target_family,
                required_variation_axis=str(contract.get("required_variation_axis") or ""),
                existing=self._string_list(contract.get("required_feature_roles")),
            )
        elif str(contract.get("required_variation_axis") or "").strip().lower() == "non_regime":
            contract["required_feature_roles"] = self._normalize_required_feature_roles(
                family=target_family,
                required_variation_axis="non_regime",
                existing=[],
            )
        else:
            contract["required_feature_roles"] = []

        contract["forbidden_motifs"] = self._string_list(contract.get("forbidden_motifs")) or self._default_forbidden_motifs(target_family)
        contract["forbidden_features"] = self._string_list(contract.get("forbidden_features"))
        contract["required_features"] = (
            self._string_list(contract.get("required_features")) if explicit_required_features else []
        )
        contract["required_gate_dimensions"] = (
            self._string_list(contract.get("required_gate_dimensions"))
            if explicit_required_gate_dimensions
            else []
        )
        contract["banned_motif_signatures"] = self._string_list(contract.get("banned_motif_signatures"))
        contract["writer_inputs"] = self._string_list(contract.get("writer_inputs")) or self._default_writer_inputs(target_family)
        contract["evidence_paths"] = self._string_list(contract.get("evidence_paths"))
        contract["tools_used"] = self._string_list(contract.get("tools_used"))
        contract["tracking_tags"] = self._string_list(contract.get("tracking_tags")) or [target_family]
        contract["target_trade_style"] = str(contract.get("target_trade_style") or "").strip() or None
        contract["planner_regime_gates"] = self._normalize_regime_gates(
            contract.get("planner_regime_gates") or contract.get("regime_gates")
        )
        contract["gate_intent"] = self._dict_value(contract.get("gate_intent")) if explicit_gate_intent else {}
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
            contract["required_gate_dimensions"] = self._unique_strings(gate_dimensions)
        contract["must_answer"] = self._concretize_must_answer(contract)
        return contract

    def _merge_hint_fragment(
        self,
        base: dict[str, Any],
        fragment: dict[str, Any],
    ) -> dict[str, Any]:
        merged = dict(base)
        if not isinstance(fragment, dict):
            return merged
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
                merged[key] = self._string_list(value)
                continue
            if key in {"gate_intent", "regime_gates", "planner_regime_gates"}:
                if isinstance(value, dict):
                    merged["planner_regime_gates" if key != "gate_intent" else key] = dict(value)
                continue
            merged[key] = value
        return merged

    def _body_family_override(self, text: str, families: list[str]) -> str | None:
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

    def _body_trade_style(self, text: str) -> str | None:
        matches = list(
            re.finditer(r"\btrade_style\b\s*[:=]\s*([a-z0-9_]+)", text, flags=re.IGNORECASE)
        )
        if not matches:
            return None
        return matches[-1].group(1).strip()

    def _mentioned_allowed_features(self, text: str, allowed_features: list[str]) -> list[str]:
        lowered = text.lower()
        matches = [
            feature
            for feature in allowed_features
            if str(feature).lower() in lowered
        ]
        return self._unique_strings(matches)

    def _mentioned_gate_dimensions(self, text: str, allowed_features: list[str]) -> list[str]:
        dims: list[str] = []
        lowered = text.lower()
        for feature in allowed_features:
            feature_text = str(feature).strip()
            if not feature_text:
                continue
            if feature_text.lower() in lowered and any(
                token in feature_text.lower()
                for token in ("funding", "volatility", "trend", "co_movement", "breadth", "corr", "dispersion")
            ):
                dims.append(feature_text)
        return self._unique_strings(dims)

    def _section_or_fallback(
        self,
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

    def _last_question(self, text: str) -> str:
        questions = [
            line.strip()
            for line in text.splitlines()
            if line.strip().endswith("?") and len(line.strip()) >= 15
        ]
        return questions[-1] if questions else ""

    def _semantic_note_issues(
        self,
        *,
        note_text: str,
        planner_contract: dict[str, Any],
    ) -> list[str]:
        stripped = note_text.strip()
        issues: list[str] = []
        if len(stripped) < 80:
            issues.append("research_note_too_short")
        lowered = stripped.lower()
        if not any(keyword in lowered for keyword in self.ACTION_KEYWORDS):
            issues.append("no_clear_proposed_test")
        if not str(planner_contract.get("target_family") or "").strip():
            issues.append("no_target_family")
        if not str(planner_contract.get("must_answer") or "").strip():
            issues.append("no_concrete_question")
        if not str(planner_contract.get("informative_test") or "").strip():
            issues.append("no_informative_test")
        return issues

    def _fallback_contract(
        self,
        *,
        parent: Any,
        current_state: dict[str, Any],
        tool_refs: list[str],
    ) -> dict[str, Any]:
        target_family = parent.family
        return {
            "decision": "refine_current_family",
            "search_mode": str(current_state.get("search_mode") or "refine"),
            "target_family": target_family,
            "target_trade_style": str(dict(parent.params or {}).get("trade_style") or "").strip() or None,
            "target_universe": list(parent.universe.basis_groups),
            "core_hypothesis": str(current_state.get("open_question") or f"Refine {target_family}"),
            "informative_test": "Test one concrete change tied to the current open question.",
            "expected_success": ["better validation robustness"],
            "expected_failure": ["no measurable change"],
            "evidence_paths": self._unique_strings(
                [
                    *self._string_list(current_state.get("selected_lesson_refs")),
                    *self._string_list(current_state.get("selected_probe_refs")),
                    *tool_refs,
                ]
            ),
            "tools_used": ["workspace_tools"] if tool_refs else [],
            "tracking_tags": [target_family],
            "must_answer": str(current_state.get("open_question") or f"Refine {target_family}"),
            "required_feature_roles": [],
            "required_features": [],
            "forbidden_features": self._string_list(current_state.get("forbidden_features")),
            "forbidden_motifs": self._default_forbidden_motifs(target_family),
            "gate_intent": {},
            "required_gate_dimensions": [],
            "required_variation_axis": str(current_state.get("required_variation_axis") or "") or None,
            "banned_motif_signatures": self._string_list(current_state.get("banned_motif_signatures")),
            "writer_inputs": self._default_writer_inputs(target_family),
            "planner_regime_gates": {},
        }

    def _fallback_note(self, *, parent: Any, current_state: dict[str, Any]) -> str:
        family = parent.family
        question = str(current_state.get("open_question") or f"Refine {family}")
        return "\n".join(
            [
                "## Diagnosis",
                f"The planner note could not be repaired cleanly, so keep `{family}` as the anchor.",
                "",
                "## What to test",
                f"Test one concrete change that answers: {question}",
                "",
                "## Risks",
                "Do not repeat the last failed motif without changing the feature mix or gate dimension materially.",
            ]
        ).strip()

    def _planner_failure_feedback(
        self,
        *,
        parent: Any,
        current_state: dict[str, Any],
        previous_feedback: dict[str, Any] | None,
        raw_content: str,
        attempt_number: int,
        semantic_issues: list[str],
    ) -> dict[str, Any]:
        feedback: dict[str, Any] = {
            "error_type": "planner_note_semantic_failure",
            "attempt_number": attempt_number,
            "semantic_issues": list(semantic_issues),
            "raw_response_excerpt": raw_content[:4000],
            "requirements": [
                "Return one normal markdown research note.",
                "State one clear next test.",
                "Make the family choice explicit when it changes.",
                "Make must_answer answerable by the next experiment.",
                "If a named feature or gate dimension matters, say it explicitly.",
                f"Keep the note anchored to `{parent.family}` unless you are intentionally switching families.",
            ],
            "open_question": str(current_state.get("open_question") or ""),
        }
        if previous_feedback:
            feedback["previous_feedback"] = dict(previous_feedback)
        return feedback

    def _write_trace(
        self,
        *,
        trace_path: Path,
        skill_path: Path,
        system_prompt: str,
        default_context_files: list[str],
        tools: list[KimiTool],
        note_text: str,
        raw_content: str,
        raw_frontmatter: dict[str, Any],
        yaml_fragments: list[dict[str, Any]],
        planner_contract: dict[str, Any],
        tool_refs: list[str],
        initial_repair_feedback: dict[str, Any] | None,
        attempts: list[dict[str, Any]],
        used_fallback_note: bool,
    ) -> None:
        payload = {
            "stage": "planner",
            "system_prompt_path": str(skill_path.relative_to(self.settings.root_dir)),
            "inputs": {
                "system_prompt": system_prompt,
                "default_context_files": default_context_files,
                "initial_repair_feedback": dict(initial_repair_feedback or {}),
            },
            "outputs": {
                "research_note_path": str(trace_path.parent / "research_note.md"),
                "planner_contract_path": str(trace_path.parent / "planner_contract.json"),
                "research_note": note_text,
                "raw_research_note": raw_content,
                "raw_frontmatter": raw_frontmatter,
                "yaml_fragments": yaml_fragments,
                "planner_contract": planner_contract,
                "tool_refs": list(tool_refs),
                "tool_names": [tool.name for tool in tools],
                "used_fallback_note": used_fallback_note,
            },
            "tool_names": [tool.name for tool in tools],
            "planner_attempts": attempts,
            "repair_attempts": [
                attempt for attempt in attempts if dict(attempt.get("repair_feedback") or {})
            ],
            "kimi_trace": dict(self.kimi.last_trace or {}),
            "kimi_exchange": dict(self.kimi.last_exchange or {}),
        }
        trace_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True, default=str))

    def _default_required_feature_roles(self, family: str, *, required_variation_axis: str = "") -> list[str]:
        if family == "perp_multi_asset_carry" and required_variation_axis == "non_regime":
            return ["one core_carry feature", "one non_regime_axis feature"]
        if family == "perp_multi_asset_carry":
            return ["one core_carry feature", "one orthogonal_regime feature"]
        if family in {"perp_pair_trade_unlevered", "perp_pair_trade_levered"}:
            return ["one spread_or_residual feature", "one orthogonal_regime feature"]
        if family in {"perp_basket_neutral_unlevered", "perp_basket_neutral_levered"}:
            return ["one cross_sectional_core feature", "one orthogonal_regime feature"]
        return ["one trend_or_momentum feature", "one orthogonal_regime feature"]

    def _normalize_required_feature_roles(
        self,
        *,
        family: str,
        required_variation_axis: str,
        existing: list[str],
    ) -> list[str]:
        roles = [str(value) for value in existing if str(value).strip()]
        if not roles:
            return self._default_required_feature_roles(
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

    def _default_forbidden_motifs(self, family: str) -> list[str]:
        if family == "perp_multi_asset_carry":
            return ["second pure trend overlay"]
        return []

    def _default_writer_inputs(self, family: str) -> list[str]:
        return [
            f"manifests/family/{family}.md",
            f"manifests/family/{family}.json",
            f"manifests/features/family/{family}.md",
            f"manifests/features/family/{family}.json",
            "manifests/constraints.md",
            "manifests/regime_catalog.md",
            "manifests/policy_surface.md",
        ]

    def _concretize_must_answer(self, contract: dict[str, Any]) -> str:
        must_answer = str(contract.get("must_answer") or "").strip()
        feature_refs = self._string_list(contract.get("required_features"))
        gate_dims = self._string_list(contract.get("required_gate_dimensions"))
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

    def _string_list(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return self._unique_strings(str(item) for item in value if str(item).strip())

    def _dict_value(self, value: Any) -> dict[str, Any]:
        return dict(value) if isinstance(value, dict) else {}

    def _explicit_contract_keys(
        self,
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

    def _unique_strings(self, values: Any) -> list[str]:
        seen: set[str] = set()
        items: list[str] = []
        for value in values:
            text = str(value).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            items.append(text)
        return items

    def _normalize_regime_gates(self, value: Any) -> dict[str, Any]:
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
