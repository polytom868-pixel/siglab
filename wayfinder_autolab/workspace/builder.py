from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wayfinder_autolab.io_utils import read_json_if_exists, write_json, write_text_if_changed
from wayfinder_autolab.models import CandidateGraph
from wayfinder_autolab.orchestration.trials import summarize_generalization
from wayfinder_autolab.search.lineage import LineageStore
from wayfinder_autolab.search.mutate import CandidateMutator
from wayfinder_autolab.strategy_semantics import (
    NON_REGIME_ROLES,
    candidate_feature_roles,
    gate_dimensions,
    motif_signature,
)
from wayfinder_autolab.workspace.cards import (
    dump_yaml_block,
    relative_path,
    render_experiment_card,
    render_experiment_view_card,
    render_probe_card,
    strip_audit_fields,
    write_markdown,
)
from wayfinder_autolab.workspace.indexes import append_jsonl, ensure_index, load_jsonl, maybe_compact
from wayfinder_autolab.workspace.manifests import (
    build_feature_catalog,
    compute_spec_fingerprint,
    render_constraints,
    render_cookbook_pages,
    render_families_index,
    render_family_feature_manifest,
    render_feature_catalog_md,
    render_feature_surface,
    render_family_contract,
    render_family_manifest,
    render_policy_surface,
    render_regime_catalog,
    render_runbook,
)


@dataclass
class WorkspaceSession:
    root: Path
    track: str
    run_session_id: str
    families: list[str]
    memory_scope: str
    custom_symbols: list[str] | None = None
    use_historical_seeds: bool = False

    @property
    def current_dir(self) -> Path:
        return self.root / "current"

    @property
    def manifests_dir(self) -> Path:
        return self.root / "manifests"

    @property
    def cookbooks_dir(self) -> Path:
        return self.root / "cookbooks"

    @property
    def cards_dir(self) -> Path:
        return self.root / "cards"

    @property
    def indexes_dir(self) -> Path:
        return self.root / "indexes"

    @property
    def iterations_dir(self) -> Path:
        return self.root / "iterations"

    @property
    def cache_dir(self) -> Path:
        return self.root / "cache"

    @property
    def meta_dir(self) -> Path:
        return self.root / "meta"


class WorkspaceBuilder:
    def __init__(
        self,
        *,
        settings: Any,
        lineage: LineageStore,
        mutator: CandidateMutator,
    ) -> None:
        self.settings = settings
        self.lineage = lineage
        self.mutator = mutator

    def _session_scope_kwargs(self, session: WorkspaceSession) -> dict[str, str]:
        if session.memory_scope == "track_global":
            return {}
        return {"run_session_id": session.run_session_id}

    def initialize_session(
        self,
        *,
        track: str,
        run_session_id: str,
        family_scope: str | list[str] | None,
        memory_scope: str = "run_local",
        custom_symbols: list[str] | None = None,
        use_historical_seeds: bool = False,
    ) -> WorkspaceSession:
        families = self.mutator._allowed_families(track, family=family_scope)
        root = self.settings.artifact_dir / track / "workspaces" / run_session_id
        session = WorkspaceSession(
            root=root,
            track=track,
            run_session_id=run_session_id,
            families=families,
            memory_scope=memory_scope,
            custom_symbols=list(custom_symbols or []) or None,
            use_historical_seeds=bool(use_historical_seeds),
        )
        self._prepare_session(session)
        self._materialize_existing_cards(session)
        self.refresh_frontier_files(session)
        self._write_if_changed(
            session.root / "WORKSPACE_INDEX.md",
            self.render_workspace_index(session),
        )
        return session

    def resume_session(
        self,
        *,
        track: str,
        run_session_id: str,
        families: list[str],
        memory_scope: str = "run_local",
        custom_symbols: list[str] | None = None,
        use_historical_seeds: bool = False,
    ) -> WorkspaceSession:
        root = self.settings.artifact_dir / track / "workspaces" / run_session_id
        session = WorkspaceSession(
            root=root,
            track=track,
            run_session_id=run_session_id,
            families=list(families),
            memory_scope=memory_scope,
            custom_symbols=list(custom_symbols or []) or None,
            use_historical_seeds=bool(use_historical_seeds),
        )
        self._prepare_session(session)
        self.refresh_frontier_files(session)
        self._write_if_changed(
            session.root / "WORKSPACE_INDEX.md",
            self.render_workspace_index(session),
        )
        return session

    def _prepare_session(self, session: WorkspaceSession) -> None:
        self._ensure_layout(session)
        self._ensure_skill_mirror()
        self._write_session_meta(session)
        self._initialize_stable_files(session)

    def update_iteration(
        self,
        *,
        session: WorkspaceSession,
        parent: CandidateGraph,
        iteration_number: int,
        phase_label: str,
        force_novelty: bool,
        market_summary: dict[str, Any],
    ) -> dict[str, Any]:
        bundle = dict(market_summary.get("market_bundle") or {})
        bundle_id = str(bundle.get("bundle_id") or "")
        rows = self.lineage.dashboard_rows(
            track=session.track,
            **self._session_scope_kwargs(session),
        )
        frontier_digest = self._frontier_digest(session=session, rows=rows)
        previous_state = self._read_session_state(session)
        parent_changed = str(previous_state.get("current_parent_hash") or "") != parent.strategy_hash()
        bundle_changed = str(previous_state.get("bundle_id") or "") != bundle_id
        family_changed = (
            previous_state
            and str(previous_state.get("current_parent_family") or "") != parent.family
        )
        cooldown = int(previous_state.get("family_branch_cooldown") or 0)
        if family_changed:
            cooldown = 2
        else:
            cooldown = max(cooldown - 1, 0)
        iterations_since_switch = 0 if family_changed else int(previous_state.get("iterations_since_last_family_switch") or 0) + 1
        best_family = str(frontier_digest.get("best_family") or parent.family)
        under_tested_axes = list(frontier_digest.get("under_tested_axes") or [])
        carry_guidance = self._carry_variation_guidance(rows=rows)
        open_question = self._open_question(
            parent=parent,
            best_family=best_family,
            frontier_digest=frontier_digest,
            required_variation_axis=str(carry_guidance.get("required_variation_axis") or ""),
        )
        search_mode = self._search_mode(
            parent=parent,
            best_family=best_family,
            frontier_digest=frontier_digest,
            force_novelty=force_novelty,
        )
        selected_lesson_refs = self._select_lesson_refs(
            session=session,
            parent_family=parent.family,
            best_family=best_family,
            open_question=open_question,
            under_tested_axes=under_tested_axes,
        )
        evidence_cache_refs = self._evidence_cache_refs(
            session=session,
            parent_hash=parent.strategy_hash(),
            bundle_id=bundle_id,
            open_question=open_question,
        )
        selected_probe_refs = evidence_cache_refs or self._select_probe_refs(
            session=session,
            family=parent.family,
            bundle_id=bundle_id,
        )
        session_state = {
            "run_session_id": session.run_session_id,
            "memory_scope": session.memory_scope,
            "custom_symbols": list(session.custom_symbols or []),
            "use_historical_seeds": bool(session.use_historical_seeds),
            "iteration_number": int(iteration_number),
            "current_parent_hash": parent.strategy_hash(),
            "current_parent_family": parent.family,
            "best_family": best_family,
            "open_question": open_question,
            "search_mode": search_mode,
            "under_tested_axes": under_tested_axes,
            "selected_lesson_refs": selected_lesson_refs[:2],
            "selected_probe_refs": selected_probe_refs[:3],
            "bundle_id": bundle_id,
            "bundle_changed": bool(bundle_changed),
            "family_branch_cooldown": cooldown,
            "iterations_since_last_family_switch": iterations_since_switch,
            "required_variation_axis": str(carry_guidance.get("required_variation_axis") or ""),
            "carry_regime_streak": int(carry_guidance.get("carry_regime_streak") or 0),
            "banned_motif_signatures": list(carry_guidance.get("banned_motif_signatures") or []),
            "required_features": list(carry_guidance.get("required_features") or []),
            "forbidden_features": list(carry_guidance.get("forbidden_features") or []),
            "required_gate_dimensions": list(carry_guidance.get("required_gate_dimensions") or []),
        }
        iteration_dir = session.iterations_dir / f"{iteration_number:04d}_{parent.strategy_hash()}"
        iteration_dir.mkdir(parents=True, exist_ok=True)
        self._write_if_changed(
            session.current_dir / "SESSION_STATE.json",
            json.dumps(session_state, indent=2, ensure_ascii=True),
        )
        self._write_if_changed(
            session.root / "TASK.md",
            self._render_task(
                session=session,
                parent=parent,
                iteration_number=iteration_number,
                phase_label=phase_label,
                force_novelty=force_novelty,
                search_mode=search_mode,
                open_question=open_question,
                best_family=best_family,
                selected_lesson_refs=selected_lesson_refs,
                selected_probe_refs=selected_probe_refs,
                required_variation_axis=str(carry_guidance.get("required_variation_axis") or "") or None,
            ),
        )
        self._write_if_changed(
            session.current_dir / "search_mode.md",
            self._render_search_mode(
                search_mode=search_mode,
                open_question=open_question,
                best_family=best_family,
                under_tested_axes=under_tested_axes,
                required_variation_axis=str(carry_guidance.get("required_variation_axis") or "") or None,
                banned_motif_signatures=list(carry_guidance.get("banned_motif_signatures") or []),
            ),
        )
        if bundle_changed or not (session.current_dir / "market_brief.md").exists():
            self._write_if_changed(
                session.current_dir / "market_brief.md",
                self._render_market_brief(market_summary),
            )
        if parent_changed or not (session.current_dir / "parent_card.md").exists():
            self._write_if_changed(
                session.current_dir / "parent_card.md",
                self._render_parent_card(session=session, parent=parent),
            )
        self._write_if_changed(
            session.current_dir / "frontier_brief.md",
            self._render_frontier_brief(frontier_digest),
        )
        self._write_if_changed(
            session.current_dir / "frontier_digest.json",
            json.dumps(frontier_digest, indent=2, ensure_ascii=True),
        )
        self._write_if_changed(
            session.current_dir / "families_index.md",
            render_families_index(
                track=session.track,
                families=session.families,
                root_dir=self.settings.root_dir,
                rows=rows,
            ),
        )
        self._write_if_changed(
            session.root / "WORKSPACE_INDEX.md",
            self.render_workspace_index(session),
        )
        return {
            "session_state": session_state,
            "iteration_dir": iteration_dir,
            "research_note_path": iteration_dir / "research_note.md",
            "planner_contract_path": iteration_dir / "planner_contract.json",
            "structure_spec_path": iteration_dir / "structure_spec.json",
            "base_candidate_path": iteration_dir / "base_candidate.yaml",
            "candidate_patch_path": iteration_dir / "candidate_patch.json",
            "candidate_after_patch_path": iteration_dir / "candidate_after_patch.yaml",
            "candidate_json_path": iteration_dir / "candidate.json",
            "repair_packet_path": iteration_dir / "repair_packet.json",
            "optuna_space_path": iteration_dir / "optuna_space.json",
            "optuna_trials_path": iteration_dir / "optuna_trials.jsonl",
            "optuna_best_path": iteration_dir / "optuna_best.json",
            "planner_trace_path": iteration_dir / "planner_trace.json",
            "writer_trace_path": iteration_dir / "writer_trace.json",
            "reflector_trace_path": iteration_dir / "reflector_trace.json",
        }

    def record_experiment(self, *, session: WorkspaceSession, candidate_hash: str, iteration_number: int) -> str | None:
        row = self.lineage.experiment_detail(candidate_hash)
        if row is None:
            return None
        artifact = dict(row.get("artifact") or {})
        canonical_path = session.cards_dir / "experiments" / f"{candidate_hash}.md"
        body, frontmatter = render_experiment_card(row=row, artifact=artifact)
        write_markdown(canonical_path, frontmatter=frontmatter, body=body)
        proxy_kind = "winners" if bool(row.get("passed")) else "failures"
        proxy_path = session.cards_dir / proxy_kind / f"{candidate_hash}.md"
        proxy_body, proxy_frontmatter = render_experiment_view_card(
            row=row,
            canonical_card_ref=relative_path(canonical_path, session.root),
            kind="winner" if bool(row.get("passed")) else "failure",
        )
        write_markdown(proxy_path, frontmatter=proxy_frontmatter, body=proxy_body)
        search_text = " ".join(
            [
                str(row.get("family") or ""),
                str(dict(row.get("candidate") or {}).get("hypothesis") or ""),
                " ".join(str(feature) for feature in dict(row.get("candidate") or {}).get("features") or []),
                " ".join(str(tag) for tag in frontmatter.get("tracking_tags") or []),
            ]
        )
        append_jsonl(
            session.indexes_dir / "experiment_index.jsonl",
            {
                "path": relative_path(canonical_path, session.root),
                "kind": "experiment",
                "candidate_hash": candidate_hash,
                "parent_hash": row.get("parent_hash"),
                "family": row.get("family"),
                "passed": bool(row.get("passed")),
                "promoted": bool(row.get("promoted")),
                "outcome": "passed" if bool(row.get("passed")) else "failed",
                "pre_audit_canonical_total_return": dict(row.get("summary") or {}).get("pre_audit_canonical_total_return"),
                "validation_total_return": dict(row.get("summary") or {}).get("validation_total_return"),
                "active_bar_fraction": dict(row.get("summary") or {}).get("active_bar_fraction"),
                "tracking_tags": list(frontmatter.get("tracking_tags") or []),
                "created_at": row.get("created_at"),
                "search_text": search_text,
            },
        )
        maybe_compact(
            session.indexes_dir / "experiment_index.jsonl",
            key_fields=["path", "candidate_hash"],
            iteration_number=iteration_number,
        )
        return relative_path(canonical_path, session.root)

    def record_probe(
        self,
        *,
        session: WorkspaceSession,
        iteration_number: int,
        probe_type: str,
        family: str,
        universe: list[str],
        bundle_id: str | None,
        arguments: dict[str, Any],
        result: dict[str, Any],
        tracking_tags: list[str] | None = None,
    ) -> str:
        probe_key = hashlib.sha256(
            json.dumps(
                {
                    "probe_type": probe_type,
                    "family": family,
                    "universe": universe,
                    "bundle_id": bundle_id,
                    "arguments": arguments,
                },
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        ).hexdigest()[:16]
        card_path = session.cards_dir / "probes" / f"{probe_key}.md"
        body, frontmatter = render_probe_card(
            probe_key=probe_key,
            probe_type=probe_type,
            family=family,
            universe=universe,
            bundle_id=bundle_id,
            arguments=arguments,
            result=result,
            tracking_tags=tracking_tags,
        )
        write_markdown(card_path, frontmatter=frontmatter, body=body)
        append_jsonl(
            session.indexes_dir / "probe_index.jsonl",
            {
                "path": relative_path(card_path, session.root),
                "kind": "probe",
                "probe_key": probe_key,
                "family": family,
                "universe": universe,
                "bundle_id": bundle_id,
                "probe_type": probe_type,
                "tracking_tags": list(frontmatter.get("tracking_tags") or []),
                "created_at": self._now(),
                "search_text": " ".join(
                    [
                        probe_type,
                        family,
                        " ".join(universe),
                        json.dumps(arguments, ensure_ascii=True, sort_keys=True, default=str),
                    ]
                ),
            },
        )
        maybe_compact(
            session.indexes_dir / "probe_index.jsonl",
            key_fields=["path", "probe_key"],
            iteration_number=iteration_number,
        )
        return relative_path(card_path, session.root)

    def record_lesson_card(
        self,
        *,
        session: WorkspaceSession,
        iteration_number: int,
        candidate_hash: str,
        content: str,
    ) -> str:
        path = session.cards_dir / "reflections" / f"{candidate_hash}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        frontmatter = self._read_frontmatter(path)
        family = str(frontmatter.get("family") or "")
        failure_mode = str(frontmatter.get("failure_mode") or "")
        self._supersede_prior_lessons(
            session=session,
            candidate_hash=candidate_hash,
            family=family,
            failure_mode=failure_mode,
        )
        append_jsonl(
            session.indexes_dir / "reflection_index.jsonl",
            {
                "path": relative_path(path, session.root),
                "kind": "reflection",
                "candidate_hash": candidate_hash,
                "family": family,
                "verdict": frontmatter.get("verdict"),
                "failure_mode": frontmatter.get("failure_mode"),
                "failed_motif_signature": frontmatter.get("failed_motif_signature"),
                "next_move": frontmatter.get("next_move"),
                "status": frontmatter.get("status", "active"),
                "tracking_tags": list(frontmatter.get("tracking_tags") or []),
                "outcome": str(frontmatter.get("verdict") or ""),
                "created_at": self._now(),
                "search_text": " ".join(
                    [
                        family,
                        str(frontmatter.get("verdict") or ""),
                        str(frontmatter.get("failure_mode") or ""),
                        str(frontmatter.get("failed_motif_signature") or ""),
                        " ".join(str(tag) for tag in frontmatter.get("tracking_tags") or []),
                        str(frontmatter.get("next_move") or ""),
                    ]
                ),
            },
        )
        maybe_compact(
            session.indexes_dir / "reflection_index.jsonl",
            key_fields=["path", "candidate_hash"],
            iteration_number=iteration_number,
        )
        return relative_path(path, session.root)

    def refresh_frontier_files(self, session: WorkspaceSession) -> None:
        rows = self.lineage.dashboard_rows(
            track=session.track,
            **self._session_scope_kwargs(session),
        )
        frontier_digest = self._frontier_digest(session=session, rows=rows)
        self._write_if_changed(
            session.current_dir / "frontier_brief.md",
            self._render_frontier_brief(frontier_digest),
        )
        self._write_if_changed(
            session.current_dir / "frontier_digest.json",
            json.dumps(frontier_digest, indent=2, ensure_ascii=True),
        )
        self._write_if_changed(
            session.current_dir / "families_index.md",
            render_families_index(
                track=session.track,
                families=session.families,
                root_dir=self.settings.root_dir,
                rows=rows,
            ),
        )
        self._write_if_changed(
            session.current_dir / "incumbent_candidate.yaml",
            self._render_incumbent_candidate(session=session),
        )
        self._write_if_changed(
            session.current_dir / "family_incumbents.json",
            json.dumps(
                self._family_incumbents_payload(session=session),
                indent=2,
                ensure_ascii=True,
                default=str,
            ),
        )
        recent_trials = self._recent_trials(session=session, rows=rows)
        self._write_if_changed(
            session.current_dir / "recent_trials.md",
            self._render_recent_trials(recent_trials),
        )
        self._write_trial_index(session=session, rows=rows)

    def store_evidence_cache(
        self,
        *,
        session: WorkspaceSession,
        parent_hash: str,
        bundle_id: str,
        open_question: str,
        lesson_refs: list[str],
        probe_refs: list[str],
        experiment_refs: list[str],
    ) -> None:
        cache_path = self._evidence_cache_path(
            session=session,
            parent_hash=parent_hash,
            bundle_id=bundle_id,
            open_question=open_question,
        )
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(
                {
                    "lesson_refs": lesson_refs[:2],
                    "probe_refs": probe_refs[:3],
                    "experiment_refs": experiment_refs[:5],
                    "updated_at": self._now(),
                },
                indent=2,
                ensure_ascii=True,
            )
        )

    def render_workspace_index(self, session: WorkspaceSession) -> str:
        return "\n".join(
            [
                "# Workspace Index",
                "",
                "Current files:",
                "- `TASK.md`",
                "- `current/SESSION_STATE.json`",
                "- `current/frontier_brief.md`",
                "- `current/market_brief.md`",
                "- `current/parent_card.md`",
                "- `current/search_mode.md`",
                "- `current/families_index.md`",
                "- `current/incumbent_candidate.yaml`",
                "- `current/family_incumbents.json`",
                "- `current/recent_trials.md`",
                "- `manifests/regime_catalog.md`",
                "- `manifests/policy_surface.md`",
                "- `manifests/features/feature_surface.md`",
                "",
                "Static references:",
                "- `manifests/constraints.md`",
                "- `manifests/family/*.md`",
                "- `manifests/family/*.json`",
                "- `manifests/features/feature_catalog.md`",
                "- `manifests/features/feature_catalog.jsonl`",
                "- `manifests/features/family/*.md`",
                "- `manifests/features/family/*.json`",
                "- `cookbooks/*.md`",
                "",
                "Card roots:",
                "- `cards/experiments/`",
                "- `cards/winners/`",
                "- `cards/failures/`",
                "- `cards/reflections/`",
                "- `cards/probes/`",
                "",
                "Indexes:",
                "- `indexes/experiment_index.jsonl`",
                "- `indexes/reflection_index.jsonl`",
                "- `indexes/probe_index.jsonl`",
                "- `indexes/trial_index.jsonl`",
            ]
        ).strip() + "\n"

    def _ensure_layout(self, session: WorkspaceSession) -> None:
        for path in [
            session.root,
            session.current_dir,
            session.manifests_dir / "family",
            session.cookbooks_dir,
            session.cards_dir / "experiments",
            session.cards_dir / "winners",
            session.cards_dir / "failures",
            session.cards_dir / "reflections",
            session.cards_dir / "probes",
            session.indexes_dir,
            session.iterations_dir,
            session.cache_dir / "evidence",
            session.meta_dir,
        ]:
            path.mkdir(parents=True, exist_ok=True)

    def _write_session_meta(self, session: WorkspaceSession) -> None:
        self._write_if_changed(
            session.meta_dir / "session.json",
            json.dumps(
                {
                    "track": session.track,
                    "run_session_id": session.run_session_id,
                    "families": list(session.families),
                    "memory_scope": session.memory_scope,
                    "custom_symbols": list(session.custom_symbols or []),
                    "use_historical_seeds": bool(session.use_historical_seeds),
                },
                indent=2,
                ensure_ascii=True,
            ),
        )

    def _initialize_stable_files(self, session: WorkspaceSession) -> None:
        fingerprint_payload = compute_spec_fingerprint(self.settings.root_dir)
        fingerprint_path = session.meta_dir / "spec_fingerprint.json"
        existing = read_json_if_exists(fingerprint_path)
        if existing.get("fingerprint") == fingerprint_payload.get("fingerprint"):
            return
        write_json(fingerprint_path, fingerprint_payload)
        self._write_if_changed(session.root / "RUNBOOK.md", render_runbook())
        self._write_if_changed(
            session.manifests_dir / "constraints.md",
            render_constraints(
                track=session.track,
                families=session.families,
                root_dir=self.settings.root_dir,
            ),
        )
        feature_catalog = build_feature_catalog(
            track=session.track,
            families=session.families,
            root_dir=self.settings.root_dir,
        )
        self._write_if_changed(
            session.manifests_dir / "regime_catalog.md",
            render_regime_catalog(),
        )
        self._write_if_changed(
            session.manifests_dir / "policy_surface.md",
            render_policy_surface(families=session.families),
        )
        feature_dir = session.manifests_dir / "features"
        feature_family_dir = feature_dir / "family"
        feature_family_dir.mkdir(parents=True, exist_ok=True)
        self._write_if_changed(
            feature_dir / "feature_surface.md",
            render_feature_surface(catalog=feature_catalog),
        )
        self._write_if_changed(
            feature_dir / "feature_catalog.md",
            render_feature_catalog_md(catalog=feature_catalog),
        )
        catalog_jsonl = "\n".join(
            json.dumps(row, ensure_ascii=True, default=str) for row in feature_catalog
        ).strip()
        self._write_if_changed(
            feature_dir / "feature_catalog.jsonl",
            (catalog_jsonl + "\n") if catalog_jsonl else "",
        )
        for family in session.families:
            self._write_if_changed(
                session.manifests_dir / "family" / f"{family}.md",
                render_family_manifest(
                    track=session.track,
                    family=family,
                    root_dir=self.settings.root_dir,
                ),
            )
            self._write_if_changed(
                session.manifests_dir / "family" / f"{family}.json",
                json.dumps(
                    render_family_contract(
                        track=session.track,
                        family=family,
                        root_dir=self.settings.root_dir,
                    ),
                    indent=2,
                    ensure_ascii=True,
                    default=str,
                ),
            )
            family_feature_rows = [
                row for row in feature_catalog if family in list(row.get("family") or [])
            ]
            self._write_if_changed(
                feature_family_dir / f"{family}.md",
                render_family_feature_manifest(
                    family=family,
                    catalog=feature_catalog,
                ),
            )
            self._write_if_changed(
                feature_family_dir / f"{family}.json",
                json.dumps(family_feature_rows, indent=2, ensure_ascii=True, default=str),
            )
        for name, content in render_cookbook_pages(session.track).items():
            self._write_if_changed(session.cookbooks_dir / name, content)
        ensure_index(session.indexes_dir / "experiment_index.jsonl")
        ensure_index(session.indexes_dir / "reflection_index.jsonl")
        ensure_index(session.indexes_dir / "probe_index.jsonl")
        ensure_index(session.indexes_dir / "trial_index.jsonl")

    def _materialize_existing_cards(self, session: WorkspaceSession) -> None:
        rows = self.lineage.dashboard_rows(
            track=session.track,
            **self._session_scope_kwargs(session),
        )
        for index, row in enumerate(rows, start=1):
            candidate_hash = str(row.get("candidate_hash") or "")
            if not candidate_hash:
                continue
            self.record_experiment(session=session, candidate_hash=candidate_hash, iteration_number=index)

    def _ensure_skill_mirror(self) -> None:
        claude_dir = self.settings.root_dir / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        target = claude_dir / "skills"
        if target.is_symlink() or target.exists():
            if target.is_symlink() and os.readlink(target) == "../.agents/skills":
                return
            if target.is_symlink() or target.is_file():
                target.unlink()
            elif target.is_dir():
                return
        os.symlink("../.agents/skills", target)

    def _read_session_state(self, session: WorkspaceSession) -> dict[str, Any]:
        return read_json_if_exists(session.current_dir / "SESSION_STATE.json")

    def _frontier_digest(self, *, session: WorkspaceSession, rows: list[dict[str, Any]]) -> dict[str, Any]:
        family_scores: dict[str, list[float]] = {}
        positive_anchors: list[dict[str, Any]] = []
        feature_counts: dict[str, int] = {}
        failure_modes: dict[str, int] = {}
        family_counts: dict[str, int] = {family: 0 for family in session.families}
        for row in rows:
            family = str(row.get("family") or "")
            family_counts[family] = family_counts.get(family, 0) + 1
            summary = dict(row.get("summary") or {})
            pre_audit = float(summary.get("pre_audit_canonical_total_return") or 0.0)
            family_scores.setdefault(family, []).append(pre_audit)
            if pre_audit > 0.0:
                positive_anchors.append(
                    {
                        "candidate_hash": row.get("candidate_hash"),
                        "family": family,
                        "pre_audit_canonical_total_return": pre_audit,
                        "validation_total_return": summary.get("validation_total_return"),
                    }
                )
            for feature in dict(row.get("candidate") or {}).get("features") or []:
                feature_counts[str(feature)] = feature_counts.get(str(feature), 0) + 1
            for reason in summary.get("gate_reasons") or []:
                failure_modes[str(reason)] = failure_modes.get(str(reason), 0) + 1
        best_family = session.families[0]
        best_score = float("-inf")
        for family, scores in family_scores.items():
            if not scores:
                continue
            score = max(scores)
            if score > best_score:
                best_score = score
                best_family = family
        under_tested_axes = [
            family
            for family, count in sorted(family_counts.items(), key=lambda item: (item[1], item[0]))
            if count <= 2
        ][:4]
        positive_anchors.sort(
            key=lambda row: (
                float(row.get("pre_audit_canonical_total_return") or 0.0),
                float(row.get("validation_total_return") or 0.0),
            ),
            reverse=True,
        )
        dominant_failure_mode = ""
        if failure_modes:
            dominant_failure_mode = max(failure_modes.items(), key=lambda item: item[1])[0]
        overused_features = [
            {"feature": feature, "count": count}
            for feature, count in sorted(feature_counts.items(), key=lambda item: (-item[1], item[0]))[:8]
        ]
        return {
            "best_family": best_family,
            "positive_anchor_count": len(positive_anchors),
            "top_positive_anchors": positive_anchors[:5],
            "dominant_failure_mode": dominant_failure_mode,
            "overused_features": overused_features,
            "under_tested_axes": under_tested_axes,
            "family_attempts": family_counts,
        }

    def _render_frontier_brief(self, frontier_digest: dict[str, Any]) -> str:
        anchors = frontier_digest.get("top_positive_anchors") or []
        overused = frontier_digest.get("overused_features") or []
        lines = [
            "# Frontier Brief",
            "",
            f"Best family: `{frontier_digest.get('best_family')}`",
            f"Dominant failure mode: `{frontier_digest.get('dominant_failure_mode') or 'unknown'}`",
            f"Under-tested axes: `{frontier_digest.get('under_tested_axes')}`",
            "",
            "## Top Positive Anchors",
        ]
        for anchor in anchors:
            lines.append(
                f"- `{anchor.get('candidate_hash')}` `{anchor.get('family')}` pre_audit={anchor.get('pre_audit_canonical_total_return')} validation={anchor.get('validation_total_return')}"
            )
        if not anchors:
            lines.append("- none")
        lines.extend(["", "## Overused Features"])
        for feature in overused:
            lines.append(f"- `{feature.get('feature')}` count={feature.get('count')}")
        if not overused:
            lines.append("- none")
        return "\n".join(lines).strip() + "\n"

    def _render_market_brief(self, market_summary: dict[str, Any]) -> str:
        bundle = dict(market_summary.get("market_bundle") or {})
        snapshot = list(market_summary.get("perp_snapshot") or [])[:6]
        return "\n".join(
            [
                "# Market Brief",
                "",
                f"- `bundle_id`: `{bundle.get('bundle_id')}`",
                f"- `as_of`: `{bundle.get('as_of')}`",
                f"- `symbols`: `{bundle.get('symbols')}`",
                "",
                "## Snapshot",
                "```json",
                json.dumps(strip_audit_fields(snapshot), indent=2, ensure_ascii=True, default=str)[:2500],
                "```",
            ]
        ).strip() + "\n"

    def _render_parent_card(self, *, session: WorkspaceSession, parent: CandidateGraph) -> str:
        detail = self.lineage.experiment_detail(parent.strategy_hash())
        if detail is not None:
            ref = f"cards/experiments/{parent.strategy_hash()}.md"
            summary = strip_audit_fields(dict(detail.get("summary") or {}))
            return "\n".join(
                [
                    "# Parent Card",
                    "",
                    f"Existing experiment card: `{ref}`",
                    f"Family: `{parent.family}`",
                    f"Hypothesis: {parent.hypothesis or 'n/a'}",
                    f"Pre-audit canonical return: {summary.get('pre_audit_canonical_total_return')}",
                    f"Validation return: {summary.get('validation_total_return')}",
                    f"Active bar fraction: {summary.get('active_bar_fraction')}",
                ]
            ).strip() + "\n"
        candidate = parent.canonical_dict()
        return "\n".join(
            [
                "# Parent Card",
                "",
                "Parent is a seed or has not been evaluated in this lineage store yet.",
                f"Family: `{parent.family}`",
                f"Hypothesis: {parent.hypothesis or 'n/a'}",
                "```json",
                json.dumps(candidate, indent=2, ensure_ascii=True, default=str),
                "```",
            ]
        ).strip() + "\n"

    def _render_search_mode(
        self,
        *,
        search_mode: str,
        open_question: str,
        best_family: str,
        under_tested_axes: list[str],
        required_variation_axis: str | None = None,
        banned_motif_signatures: list[str] | None = None,
    ) -> str:
        return "\n".join(
            [
                "# Search Mode",
                "",
                f"- `search_mode`: `{search_mode}`",
                f"- `best_family`: `{best_family}`",
                f"- `open_question`: {open_question}",
                f"- `under_tested_axes`: {under_tested_axes}",
                f"- `required_variation_axis`: `{required_variation_axis or 'none'}`",
                f"- `banned_motif_signatures`: {list(banned_motif_signatures or [])}",
            ]
        ).strip() + "\n"

    def _render_task(
        self,
        *,
        session: WorkspaceSession,
        parent: CandidateGraph,
        iteration_number: int,
        phase_label: str,
        force_novelty: bool,
        search_mode: str,
        open_question: str,
        best_family: str,
        selected_lesson_refs: list[str],
        selected_probe_refs: list[str],
        required_variation_axis: str | None = None,
    ) -> str:
        why_this_run = [
            f"best family is `{best_family}`",
            f"current parent family is `{parent.family}`",
            f"search mode is `{search_mode}`",
        ]
        if required_variation_axis:
            why_this_run.append(f"required variation axis is `{required_variation_axis}`")
        start_here = [
            "current/frontier_brief.md",
            "current/parent_card.md",
            "current/incumbent_candidate.yaml",
            "current/recent_trials.md",
            f"manifests/family/{parent.family}.md",
            *selected_lesson_refs[:1],
            *selected_probe_refs[:1],
        ]
        frontmatter = {
            "track": session.track,
            "run_session_id": session.run_session_id,
            "iteration_number": int(iteration_number),
            "phase_label": phase_label,
            "parent_hash": parent.strategy_hash(),
            "parent_family": parent.family,
            "search_mode": search_mode,
            "force_novelty": bool(force_novelty),
            "why_this_run": why_this_run,
            "open_question": open_question,
            "start_here": [item for item in start_here if item],
        }
        body = "\n".join(
            [
                "Use this task brief to choose the most informative next experiment, not the easiest tweak.",
                "",
                f"Open question: {open_question}",
            ]
        )
        from wayfinder_autolab.workspace.cards import dump_frontmatter

        return dump_frontmatter(frontmatter, body)

    def _render_incumbent_candidate(self, *, session: WorkspaceSession) -> str:
        best = self.lineage.best(
            session.track,
            run_session_id=session.run_session_id if session.memory_scope != "track_global" else None,
        )
        if best is not None:
            payload = dict(best.get("candidate") or {})
        else:
            payload = self._seed_candidate_payload(track=session.track, family=None)
        return dump_yaml_block(payload) + "\n"

    def _family_incumbents_payload(
        self,
        *,
        session: WorkspaceSession,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for family in session.families:
            best_row = self._best_lineage_row(session=session, family=family)
            if best_row is not None:
                payload[family] = {
                    "source": "lineage",
                    "candidate_hash": best_row.get("candidate_hash"),
                    "aggregate_score": dict(best_row.get("summary") or {}).get("aggregate_score"),
                    "passed": bool(best_row.get("passed")),
                    "promoted": bool(best_row.get("promoted")),
                    "candidate": dict(best_row.get("candidate") or {}),
                    "experiment_ref": f"cards/experiments/{best_row.get('candidate_hash')}.md",
                }
                continue
            seed_payload = self._seed_candidate_payload(track=session.track, family=family)
            payload[family] = {
                "source": "seed",
                "candidate_hash": None,
                "aggregate_score": None,
                "passed": False,
                "promoted": False,
                "candidate": seed_payload,
                "experiment_ref": None,
            }
        return payload

    def _recent_trials(
        self,
        *,
        session: WorkspaceSession,
        rows: list[dict[str, Any]],
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for row in list(reversed(rows))[:limit]:
            entry = self._trial_entry_from_row(session=session, row=row)
            if entry is not None:
                entries.append(entry)
        return entries

    def _trial_entry_from_row(
        self,
        *,
        session: WorkspaceSession,
        row: dict[str, Any],
    ) -> dict[str, Any] | None:
        candidate_hash = str(row.get("candidate_hash") or "")
        if not candidate_hash:
            return None
        research_summary = dict(row.get("research_summary") or {})
        workspace = dict(research_summary.get("workspace") or {})
        trial = dict(research_summary.get("trial") or {})
        raw_summary = dict(row.get("summary") or {})
        summary = strip_audit_fields(raw_summary)
        generalization = summarize_generalization(
            raw_summary,
            stability_pack=dict(trial.get("stability_pack") or {}),
        )
        reflection_ref = f"cards/reflections/{candidate_hash}.md"
        base_ref = self._relative_workspace_ref(
            session=session,
            value=trial.get("base_candidate_path") or workspace.get("base_candidate_path"),
        )
        patch_ref = self._relative_workspace_ref(
            session=session,
            value=trial.get("candidate_patch_path") or workspace.get("candidate_patch_path"),
        )
        candidate_after_patch_ref = self._relative_workspace_ref(
            session=session,
            value=trial.get("candidate_after_patch_path") or workspace.get("candidate_after_patch_path"),
        )
        structure_spec_ref = self._relative_workspace_ref(
            session=session,
            value=trial.get("structure_spec_path") or workspace.get("structure_spec_path"),
        )
        optuna_best_ref = self._relative_workspace_ref(
            session=session,
            value=trial.get("optuna_best_path") or workspace.get("optuna_best_path"),
        )
        optuna_trials_ref = self._relative_workspace_ref(
            session=session,
            value=trial.get("optuna_trials_path") or workspace.get("optuna_trials_path"),
        )
        score_diag = dict(trial.get("score_diagnosis") or {})
        biggest_lift = dict(score_diag.get("biggest_lift") or {})
        biggest_drag = dict(score_diag.get("biggest_drag") or {})
        return {
            "created_at": row.get("created_at"),
            "candidate_hash": candidate_hash,
            "family": str(row.get("family") or ""),
            "outcome": "keep" if bool(row.get("passed")) else "discard",
            "validation_result": self._coarse_result_label(
                summary.get("validation_total_return"),
                available=summary.get("validation_available"),
            ),
            "pre_audit_result": self._coarse_result_label(
                summary.get("pre_audit_canonical_total_return"),
                available=True,
            ),
            "audit_result": self._coarse_result_label(
                raw_summary.get("audit_total_return"),
                available=raw_summary.get("audit_available"),
            ),
            "patch_summary": list(trial.get("patch_summary") or []),
            "optimized_param_summary": list(trial.get("optimized_param_summary") or []),
            "nearest_miss_analysis": str(score_diag.get("nearest_miss_analysis") or ""),
            "biggest_lift": biggest_lift.get("name"),
            "biggest_drag": biggest_drag.get("name"),
            "reflection_excerpt": self._reflection_excerpt(session=session, candidate_hash=candidate_hash),
            "return_driver": str(trial.get("return_driver") or "").strip() or None,
            "return_driver_source": str(trial.get("return_driver_source") or "").strip() or None,
            "exposure_profile": str(trial.get("exposure_profile") or "").strip() or None,
            "price_contribution": trial.get("price_contribution"),
            "carry_contribution": trial.get("carry_contribution"),
            "tx_cost_contribution": trial.get("tx_cost_contribution"),
            "best_regime_context": str(trial.get("best_regime_context") or "").strip() or None,
            "worst_regime_context": str(trial.get("worst_regime_context") or "").strip() or None,
            "fragility_penalty": trial.get("fragility_penalty", generalization.get("fragility_penalty")),
            "promotion_score": trial.get("promotion_score", generalization.get("promotion_score")),
            "audit_alignment": str(
                trial.get("audit_alignment") or generalization.get("audit_alignment") or ""
            ).strip() or None,
            "fragility_label": str(
                trial.get("fragility_label") or generalization.get("fragility_label") or ""
            ).strip() or None,
            "stability_status": str(
                trial.get("stability_status")
                or dict(generalization.get("stability_pack") or {}).get("status")
                or ""
            ).strip() or None,
            "stability_pass_fraction": trial.get(
                "stability_pass_fraction",
                dict(generalization.get("stability_pack") or {}).get("passed_fraction"),
            ),
            "motif_audit_streak": trial.get("motif_audit_streak"),
            "refs": {
                "structure_spec": structure_spec_ref,
                "base_candidate": base_ref,
                "candidate_patch": patch_ref,
                "candidate_after_patch": candidate_after_patch_ref,
                "optuna_best": optuna_best_ref,
                "optuna_trials": optuna_trials_ref,
                "reflection": reflection_ref if (session.root / reflection_ref).exists() else None,
            },
            "score_diagnosis": score_diag,
        }

    def _render_recent_trials(self, recent_trials: list[dict[str, Any]]) -> str:
        lines = [
            "# Recent Trials",
            "",
            "Score formula:",
            "- `aggregate_score = median_sharpe*1.0 + median_total_return*4.0 + median_calmar*0.5 + asset_breadth*0.1 + profitable_window_pct*0.25 + worst_max_drawdown*1.5`",
            "",
        ]
        if not recent_trials:
            lines.extend(
                [
                    "No completed trials yet.",
                    "",
                    "Use the incumbent candidate plus manifests to propose the first structural test.",
                ]
            )
            return "\n".join(lines).strip() + "\n"

        for entry in recent_trials:
            refs = {key: value for key, value in dict(entry.get("refs") or {}).items() if value}
            lines.extend(
                [
                    f"## `{entry.get('candidate_hash')}` `{entry.get('family')}` `{entry.get('outcome')}`",
                    f"- created_at: `{entry.get('created_at')}`",
                    (
                        "- results: "
                        f"validation=`{entry.get('validation_result')}` "
                        f"pre_audit=`{entry.get('pre_audit_result')}` "
                        f"audit=`{entry.get('audit_result')}`"
                    ),
                ]
            )
            patch_summary = list(entry.get("patch_summary") or [])
            if patch_summary:
                lines.append(f"- kimi_patch: {'; '.join(patch_summary[:6])}")
            optimized_summary = list(entry.get("optimized_param_summary") or [])
            if optimized_summary:
                lines.append(f"- optuna_best_diff: {'; '.join(optimized_summary[:6])}")
            driver_bits: list[str] = []
            if entry.get("return_driver"):
                driver_bits.append(f"driver=`{entry.get('return_driver')}`")
            if entry.get("exposure_profile"):
                driver_bits.append(f"exposure=`{entry.get('exposure_profile')}`")
            if driver_bits:
                lines.append(f"- {' '.join(driver_bits)}")
            if any(
                entry.get(name) is not None
                for name in ("price_contribution", "carry_contribution", "tx_cost_contribution")
            ):
                lines.append(
                    "- contrib: "
                    f"price=`{self._format_signed_pct(entry.get('price_contribution'))}` "
                    f"carry=`{self._format_signed_pct(entry.get('carry_contribution'))}` "
                    f"tx=`{self._format_signed_pct(entry.get('tx_cost_contribution'))}`"
                )
            if entry.get("best_regime_context") or entry.get("worst_regime_context"):
                lines.append(
                    "- regime_context: "
                    f"best=`{entry.get('best_regime_context') or 'n/a'}` "
                    f"worst=`{entry.get('worst_regime_context') or 'n/a'}`"
                )
            generalization_bits: list[str] = []
            if entry.get("audit_alignment"):
                generalization_bits.append(f"audit_alignment=`{entry.get('audit_alignment')}`")
            if entry.get("fragility_label"):
                generalization_bits.append(f"fragility=`{entry.get('fragility_label')}`")
            if entry.get("promotion_score") is not None:
                generalization_bits.append(
                    f"promotion_score=`{self._format_metric(entry.get('promotion_score'))}`"
                )
            if entry.get("fragility_penalty") is not None:
                generalization_bits.append(
                    f"fragility_penalty=`{self._format_metric(entry.get('fragility_penalty'))}`"
                )
            if entry.get("stability_status"):
                stability_bits = [f"stability=`{entry.get('stability_status')}`"]
                if entry.get("stability_pass_fraction") is not None:
                    stability_bits.append(
                        f"pass_fraction=`{self._format_pct(entry.get('stability_pass_fraction'))}`"
                    )
                generalization_bits.extend(stability_bits)
            if entry.get("motif_audit_streak") is not None:
                generalization_bits.append(f"motif_audit_streak=`{entry.get('motif_audit_streak')}`")
            if generalization_bits:
                lines.append(f"- generalization: {' '.join(generalization_bits)}")
            if entry.get("biggest_lift") or entry.get("biggest_drag"):
                lines.append(
                    f"- score_diagnosis: biggest_lift=`{entry.get('biggest_lift') or 'n/a'}` biggest_drag=`{entry.get('biggest_drag') or 'n/a'}`"
                )
            if entry.get("nearest_miss_analysis"):
                lines.append(f"- nearest_miss: {entry.get('nearest_miss_analysis')}")
            if entry.get("reflection_excerpt"):
                lines.append(f"- reflection: {entry.get('reflection_excerpt')}")
            if refs:
                ref_parts = [f"{name}=`{value}`" for name, value in refs.items()]
                lines.append(f"- refs: {' '.join(ref_parts)}")
            lines.append("")
        return "\n".join(lines).strip() + "\n"

    def _write_trial_index(
        self,
        *,
        session: WorkspaceSession,
        rows: list[dict[str, Any]],
    ) -> None:
        index_rows = []
        for row in rows:
            entry = self._trial_entry_from_row(session=session, row=row)
            if entry is None:
                continue
            refs = {key: value for key, value in dict(entry.get("refs") or {}).items() if value}
            index_rows.append(
                {
                    "kind": "trial",
                    "path": refs.get("optuna_best") or refs.get("candidate_after_patch") or refs.get("structure_spec") or "",
                    "candidate_hash": entry.get("candidate_hash"),
                    "family": entry.get("family"),
                    "outcome": entry.get("outcome"),
                    "created_at": entry.get("created_at"),
                    "refs": refs,
                    "search_text": " ".join(
                        [
                            str(entry.get("candidate_hash") or ""),
                            str(entry.get("family") or ""),
                            str(entry.get("outcome") or ""),
                            " ".join(str(item) for item in entry.get("patch_summary") or []),
                            " ".join(str(item) for item in entry.get("optimized_param_summary") or []),
                            str(entry.get("return_driver") or ""),
                            str(entry.get("exposure_profile") or ""),
                            str(entry.get("best_regime_context") or ""),
                            str(entry.get("worst_regime_context") or ""),
                            str(entry.get("audit_alignment") or ""),
                            str(entry.get("fragility_label") or ""),
                            str(entry.get("promotion_score") or ""),
                            str(entry.get("fragility_penalty") or ""),
                            str(entry.get("stability_status") or ""),
                            str(entry.get("motif_audit_streak") or ""),
                            str(entry.get("nearest_miss_analysis") or ""),
                            str(entry.get("reflection_excerpt") or ""),
                        ]
                    ),
                }
            )
        content = "\n".join(json.dumps(row, ensure_ascii=True, default=str) for row in index_rows)
        self._write_if_changed(
            session.indexes_dir / "trial_index.jsonl",
            (content + "\n") if content else "",
        )

    def _best_lineage_row(
        self,
        *,
        session: WorkspaceSession,
        family: str,
    ) -> dict[str, Any] | None:
        rows = self.lineage.dashboard_rows(
            track=session.track,
            family=family,
            run_session_id=session.run_session_id if session.memory_scope != "track_global" else None,
        )
        if not rows:
            return None
        rows.sort(
            key=lambda row: (
                int(bool(row.get("passed"))),
                int(bool(row.get("promoted"))),
                float(dict(row.get("summary") or {}).get("aggregate_score") or -1e18),
                str(row.get("created_at") or ""),
            ),
            reverse=True,
        )
        return rows[0]

    def _seed_candidate_payload(self, *, track: str, family: str | None) -> dict[str, Any]:
        try:
            candidates = self.mutator.load_seed_candidates(track, family=family)
        except Exception:  # noqa: BLE001
            return {}
        if not candidates:
            return {}
        return candidates[0].canonical_dict()

    def _relative_workspace_ref(
        self,
        *,
        session: WorkspaceSession,
        value: Any,
    ) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        path = Path(text)
        if not path.is_absolute():
            return text
        try:
            return relative_path(path, session.root)
        except Exception:  # noqa: BLE001
            return text

    def _reflection_excerpt(self, *, session: WorkspaceSession, candidate_hash: str) -> str:
        path = session.cards_dir / "reflections" / f"{candidate_hash}.md"
        if not path.exists():
            return ""
        try:
            from wayfinder_autolab.workspace.cards import parse_frontmatter

            _frontmatter, body = parse_frontmatter(path.read_text())
        except Exception:  # noqa: BLE001
            body = path.read_text()
        snippets = [
            line.strip()
            for line in body.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        return " ".join(snippets[:2])[:240]

    def _format_metric(self, value: Any) -> str:
        if value is None:
            return "n/a"
        try:
            return f"{float(value):.3f}"
        except (TypeError, ValueError):
            return str(value)

    def _format_pct(self, value: Any) -> str:
        if value is None:
            return "n/a"
        try:
            return f"{float(value):.2%}"
        except (TypeError, ValueError):
            return str(value)

    def _format_signed_pct(self, value: Any) -> str:
        if value is None:
            return "n/a"
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return str(value)
        sign = "+" if numeric >= 0.0 else ""
        return f"{sign}{numeric:.2%}"

    def _coarse_result_label(self, value: Any, *, available: Any = True) -> str:
        if available is False:
            return "not_run"
        if value is None:
            return "unknown"
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return "unknown"
        if numeric > 0.0:
            return "positive"
        if numeric < 0.0:
            return "negative"
        return "flat"

    def _select_lesson_refs(
        self,
        *,
        session: WorkspaceSession,
        parent_family: str,
        best_family: str,
        open_question: str,
        under_tested_axes: list[str],
    ) -> list[str]:
        rows = load_jsonl(session.indexes_dir / "reflection_index.jsonl")
        scored: list[tuple[float, str]] = []
        for row in rows:
            verdict = str(row.get("verdict") or "")
            if verdict not in {"informative_success", "informative_failure", "promising_but_fragile"}:
                continue
            if str(row.get("status") or "active") != "active":
                continue
            family = str(row.get("family") or "")
            if family not in {parent_family, best_family}:
                continue
            tags = [str(tag) for tag in row.get("tracking_tags") or []]
            overlap = sum(1 for tag in tags if tag in open_question or tag in under_tested_axes)
            score = float(overlap) + (0.5 if family == parent_family else 0.0)
            scored.append((score, str(row.get("path") or "")))
        scored.sort(reverse=True)
        refs: list[str] = []
        for _score, ref in scored:
            if ref and ref not in refs:
                refs.append(ref)
            if len(refs) >= 2:
                break
        return refs

    def _build_motif_registry(self, *, rows: list[dict[str, Any]]) -> dict[str, Any]:
        motifs: dict[str, dict[str, Any]] = {}
        recent_rows = rows[-30:]
        for row in recent_rows:
            candidate = dict(row.get("candidate") or {})
            signature = motif_signature(candidate)
            entry = motifs.setdefault(
                signature,
                {
                    "motif_signature": signature,
                    "family": str(row.get("family") or ""),
                    "attempt_count": 0,
                    "pass_count": 0,
                    "best_pre_audit_return": float("-inf"),
                    "recent_candidates": [],
                },
            )
            entry["attempt_count"] += 1
            if bool(row.get("passed")):
                entry["pass_count"] += 1
            pre_audit = float(dict(row.get("summary") or {}).get("pre_audit_canonical_total_return") or 0.0)
            entry["best_pre_audit_return"] = max(float(entry["best_pre_audit_return"]), pre_audit)
            candidate_hash = str(row.get("candidate_hash") or "")
            if candidate_hash:
                entry["recent_candidates"].append(candidate_hash)
        motif_rows = sorted(
            motifs.values(),
            key=lambda item: (
                -int(item.get("attempt_count") or 0),
                float(item.get("best_pre_audit_return") or float("-inf")),
            ),
        )
        for item in motif_rows:
            item["recent_candidates"] = list(item.get("recent_candidates") or [])[-5:]
            item["cooldown_recommended"] = bool(
                int(item.get("attempt_count") or 0) >= 3
                and int(item.get("pass_count") or 0) == 0
                and float(item.get("best_pre_audit_return") or 0.0) <= 0.0
            )
        return {"motifs": motif_rows[:12]}

    def _select_probe_refs(self, *, session: WorkspaceSession, family: str, bundle_id: str) -> list[str]:
        rows = load_jsonl(session.indexes_dir / "probe_index.jsonl")
        refs: list[str] = []
        for row in reversed(rows):
            if str(row.get("family") or "") != family:
                continue
            if bundle_id and str(row.get("bundle_id") or "") not in {"", bundle_id}:
                continue
            ref = str(row.get("path") or "")
            if ref and ref not in refs:
                refs.append(ref)
            if len(refs) >= 3:
                break
        return refs

    def _evidence_cache_refs(
        self,
        *,
        session: WorkspaceSession,
        parent_hash: str,
        bundle_id: str,
        open_question: str,
    ) -> list[str]:
        path = self._evidence_cache_path(
            session=session,
            parent_hash=parent_hash,
            bundle_id=bundle_id,
            open_question=open_question,
        )
        if not path.exists():
            return []
        try:
            payload = json.loads(path.read_text())
        except Exception:  # noqa: BLE001
            return []
        refs = []
        for ref in list(payload.get("probe_refs") or [])[:3]:
            if isinstance(ref, str):
                refs.append(ref)
        return refs

    def _evidence_cache_path(
        self,
        *,
        session: WorkspaceSession,
        parent_hash: str,
        bundle_id: str,
        open_question: str,
    ) -> Path:
        open_question_hash = hashlib.sha256(open_question.encode("utf-8")).hexdigest()[:12]
        return session.cache_dir / "evidence" / f"{parent_hash}__{bundle_id or 'none'}__{open_question_hash}.json"

    def _open_question(
        self,
        *,
        parent: CandidateGraph,
        best_family: str,
        frontier_digest: dict[str, Any],
        required_variation_axis: str = "",
    ) -> str:
        dominant_failure = str(frontier_digest.get("dominant_failure_mode") or "")
        if parent.family == best_family:
            return self._falsification_question(
                family=parent.family,
                dominant_failure_mode=dominant_failure,
                required_variation_axis=required_variation_axis,
            )
        if dominant_failure:
            return (
                f"Should the search return to `{best_family}` because `{dominant_failure}` is dominating `{parent.family}`, "
                f"or is there still enough edge in `{parent.family}` to justify another test?"
            )
        return f"Is `{best_family}` still the best family to refine, or does `{parent.family}` have untested edge?"

    def _falsification_question(
        self,
        *,
        family: str,
        dominant_failure_mode: str,
        required_variation_axis: str,
    ) -> str:
        if family == "perp_multi_asset_carry" and required_variation_axis == "non_regime":
            return (
                f"Does changing one non-regime axis in `{family}` "
                "(carry core, cross-sectional ranking, universe, or book construction) "
                "improve pre-audit return without making validation negative, or are more regime gates still a dead end?"
            )
        if family == "perp_multi_asset_carry":
            return (
                f"Does one concrete regime discriminator improve pre-audit return without making validation negative "
                f"for `{family}`, or are repeated regime-gate variants failing because the carry core or book structure is the real problem?"
            )
        if dominant_failure_mode:
            return (
                f"Does one concrete change fix `{dominant_failure_mode}` for `{family}` without making validation negative, "
                "or should this line of attack be rejected?"
            )
        return (
            f"Does one concrete change improve pre-audit return without making validation negative for `{family}`, "
            "or should this line of attack be rejected?"
        )

    def _carry_variation_guidance(self, *, rows: list[dict[str, Any]]) -> dict[str, Any]:
        recent_carry_rows = []
        for row in reversed(rows):
            research_summary = dict(row.get("research_summary") or {})
            run_context = dict(research_summary.get("run_context") or {})
            if bool(run_context.get("deterministic")):
                continue
            if str(row.get("family") or "") != "perp_multi_asset_carry":
                continue
            recent_carry_rows.append(row)
            if len(recent_carry_rows) >= 6:
                break

        regime_streak = 0
        for row in recent_carry_rows:
            if self._is_regime_focused_carry_attempt(row):
                regime_streak += 1
            else:
                break

        motif_registry = self._build_motif_registry(rows=rows)
        banned_motif_signatures = [
            str(item.get("motif_signature") or "")
            for item in list(motif_registry.get("motifs") or [])
            if str(item.get("family") or "") == "perp_multi_asset_carry"
            and bool(item.get("cooldown_recommended"))
            and str(item.get("motif_signature") or "")
        ][:3]
        required_variation_axis = "non_regime" if regime_streak >= 2 else ""
        guidance: dict[str, Any] = {
            "carry_regime_streak": regime_streak,
            "required_variation_axis": required_variation_axis,
            "banned_motif_signatures": banned_motif_signatures,
            "required_features": [],
            "forbidden_features": [],
            "required_gate_dimensions": [],
        }
        if required_variation_axis == "non_regime":
            guidance["forbidden_features"] = [
                "co_movement_72h",
                "breadth_24h",
                "funding_dispersion_72h",
                "market_volatility_168h",
                "trend_strength_72h",
            ]
        return guidance

    def _is_regime_focused_carry_attempt(self, row: dict[str, Any]) -> bool:
        candidate = dict(row.get("candidate") or {})
        features = [str(feature) for feature in list(candidate.get("features") or [])]
        roles = candidate_feature_roles(features)
        gate_dims = gate_dimensions(dict(candidate.get("regime_gates") or {}))
        if not features:
            return False
        if not ("orthogonal_regime" in roles or gate_dims):
            return False
        if any(role in roles for role in NON_REGIME_ROLES):
            return False
        return True

    def _search_mode(
        self,
        *,
        parent: CandidateGraph,
        best_family: str,
        frontier_digest: dict[str, Any],
        force_novelty: bool,
    ) -> str:
        if force_novelty and parent.family != best_family:
            return "family_switch"
        if force_novelty:
            return "orthogonal_probe"
        if parent.family != best_family:
            return "family_switch"
        if frontier_digest.get("overused_features"):
            return "branch_same_family"
        return "refine"

    def _supersede_prior_lessons(
        self,
        *,
        session: WorkspaceSession,
        candidate_hash: str,
        family: str,
        failure_mode: str,
    ) -> None:
        if not family or not failure_mode:
            return
        for path in (session.cards_dir / "reflections").glob("*.md"):
            if path.stem == candidate_hash:
                continue
            from wayfinder_autolab.workspace.cards import parse_frontmatter, dump_frontmatter

            frontmatter, body = parse_frontmatter(path.read_text())
            if str(frontmatter.get("family") or "") != family:
                continue
            if str(frontmatter.get("failure_mode") or "") != failure_mode:
                continue
            if str(frontmatter.get("status") or "active") == "superseded":
                continue
            frontmatter["status"] = "superseded"
            path.write_text(dump_frontmatter(frontmatter, body))

    def _read_frontmatter(self, path: Path) -> dict[str, Any]:
        from wayfinder_autolab.workspace.cards import parse_frontmatter

        frontmatter, _body = parse_frontmatter(path.read_text())
        return frontmatter

    def _write_if_changed(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_text_if_changed(path, content)

    def _now(self) -> str:
        from datetime import UTC, datetime

        return datetime.now(UTC).isoformat()
