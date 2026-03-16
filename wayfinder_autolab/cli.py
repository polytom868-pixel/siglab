from __future__ import annotations

import argparse
import asyncio
import json
from itertools import count
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from wayfinder_autolab.benchmark import (
    DEFAULT_BENCHMARK_DECK,
    benchmark_status as benchmark_status_payload,
    evaluate_benchmark_deck,
    init_benchmark_deck,
    supported_deck_names,
)
from wayfinder_autolab.data import MarketDataProvider, ParquetLake
from wayfinder_autolab.dashboard import run_dashboard_server
from wayfinder_autolab.evaluator import ResearchEvaluator
from wayfinder_autolab.live import LivePromotionManager
from wayfinder_autolab.llm import KimiClient
from wayfinder_autolab.models import CandidateGraph
from wayfinder_autolab.path_utils import display_path, resolve_path_from_root
from wayfinder_autolab.orchestration import (
    CandidateWriterRunner,
    OptunaOptimizerRunner,
    ReflectionRunner,
    ResearchPlannerRunner,
    WorkspaceHooks,
)
from wayfinder_autolab.orchestration.trials import (
    build_candidate_patch,
    promotion_rank,
    summarize_generalization,
    summarize_patch,
    summarize_return_attribution,
)
from wayfinder_autolab.research import HypothesisSandbox, WebResearcher
from wayfinder_autolab.search import (
    CandidateMutator,
    LineageStore,
    pick_deterministic_parent,
    pick_parent,
)
from wayfinder_autolab.settings import load_settings
from wayfinder_autolab.track_registry import TRACK_CLI_CHOICES, canonical_track_name
from wayfinder_autolab.workspace import WorkspaceBuilder


def main() -> None:
    parser = argparse.ArgumentParser(prog="autolab")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument(
        "--track",
        choices=["all", *TRACK_CLI_CHOICES],
        default="all",
    )
    run_parser.add_argument("--population-size", type=int, default=None)
    run_parser.add_argument("--family", default=None)
    run_parser.add_argument(
        "--families",
        default=None,
        help="Comma-separated family list to run within a single track.",
    )
    run_parser.add_argument(
        "--burn-in-iterations",
        type=int,
        default=0,
        help="Run this many deterministic iterations before the main run phase.",
    )
    run_parser.add_argument(
        "--iterations",
        type=int,
        default=1,
        help="Number of generations to run per selected track. Use 0 for infinite.",
    )
    run_parser.add_argument("--skip-llm", action="store_true")
    run_parser.add_argument("--agent-label", default="autolab_harness")

    inspect_parser = subparsers.add_parser("inspect")
    inspect_parser.add_argument(
        "--track",
        choices=["all", *TRACK_CLI_CHOICES],
        default="all",
    )

    lineage_parser = subparsers.add_parser("lineage")
    lineage_parser.add_argument(
        "--track",
        choices=TRACK_CLI_CHOICES,
        default=None,
    )
    lineage_parser.add_argument("--limit", type=int, default=10)

    clear_passed_parser = subparsers.add_parser("clear-passed")
    clear_passed_parser.add_argument(
        "--track",
        choices=["all", *TRACK_CLI_CHOICES],
        default="all",
    )

    dashboard_parser = subparsers.add_parser("dashboard")
    dashboard_parser.add_argument("--host", default="127.0.0.1")
    dashboard_parser.add_argument("--port", type=int, default=8765)

    promote_parser = subparsers.add_parser("promote")
    promote_parser.add_argument("--candidate", required=True)
    promote_parser.add_argument("--wallet-label", default=None)
    promote_parser.add_argument("--config", dest="config_path", default=None)
    promote_parser.add_argument("--job-name", default=None)
    promote_parser.add_argument("--interval", dest="interval_seconds", type=int, default=None)
    promote_parser.add_argument("--schedule", action="store_true")
    promote_parser.add_argument("--llm-finalize", action="store_true")
    promote_parser.add_argument("--live", action="store_true")

    promotions_parser = subparsers.add_parser("promotions")
    promotions_parser.add_argument("--candidate", default=None)

    benchmark_init_parser = subparsers.add_parser("benchmark-init")
    benchmark_init_parser.add_argument(
        "--deck",
        choices=supported_deck_names(),
        default=DEFAULT_BENCHMARK_DECK,
    )
    benchmark_init_parser.add_argument("--agent-label", default="external_agent")
    benchmark_init_parser.add_argument("--run-label", default=None)
    benchmark_init_parser.add_argument("--force", action="store_true")

    benchmark_eval_parser = subparsers.add_parser("benchmark-eval")
    benchmark_eval_parser.add_argument(
        "--deck",
        choices=supported_deck_names(),
        default=DEFAULT_BENCHMARK_DECK,
    )

    benchmark_status_parser = subparsers.add_parser("benchmark-status")
    benchmark_status_parser.add_argument(
        "--deck",
        choices=supported_deck_names(),
        default=DEFAULT_BENCHMARK_DECK,
    )

    args = parser.parse_args()
    if args.command == "run":
        asyncio.run(run_command(args))
        return
    if args.command == "inspect":
        asyncio.run(inspect_command(args))
        return
    if args.command == "lineage":
        lineage_command(args)
        return
    if args.command == "clear-passed":
        clear_passed_command(args)
        return
    if args.command == "dashboard":
        dashboard_command(args)
        return
    if args.command == "promote":
        asyncio.run(promote_command(args))
        return
    if args.command == "promotions":
        promotions_command(args)
        return
    if args.command == "benchmark-init":
        benchmark_init_command(args)
        return
    if args.command == "benchmark-eval":
        asyncio.run(benchmark_eval_command(args))
        return
    if args.command == "benchmark-status":
        benchmark_status_command(args)
        return


async def run_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    _require_wayfinder_config(settings)
    settings.ensure_runtime_directories()
    selected_families = _parse_family_scope(args.family, args.families)
    if selected_families and args.track == "all":
        raise SystemExit("--family/--families require a single --track value")
    if args.track == "all":
        raise SystemExit("Workspace-flow phase 1 supports only --track directional_perps")

    lake = ParquetLake(settings.data_lake_dir)
    provider = MarketDataProvider(settings, lake)
    lineage = LineageStore(settings.lineage_db_path)
    kimi = KimiClient(settings)
    web_researcher = WebResearcher(settings, lake)
    hypothesis_sandbox = HypothesisSandbox(settings, lake, provider)
    mutator = CandidateMutator(settings, kimi)
    evaluator = ResearchEvaluator(settings, provider)
    workspace_builder = WorkspaceBuilder(
        settings=settings,
        lineage=lineage,
        mutator=mutator,
    )
    planner_runner = ResearchPlannerRunner(
        settings=settings,
        kimi=kimi,
        hypothesis_sandbox=hypothesis_sandbox,
        web_researcher=web_researcher,
        workspace_builder=workspace_builder,
    )
    writer_runner = CandidateWriterRunner(
        settings=settings,
        kimi=kimi,
        mutator=mutator,
        hypothesis_sandbox=hypothesis_sandbox,
    )
    optimizer_runner = OptunaOptimizerRunner(
        settings=settings,
        evaluator=evaluator,
        mutator=mutator,
        lineage=lineage,
    )
    reflector_runner = ReflectionRunner(
        settings=settings,
        kimi=kimi,
    )

    tracks = (
        list(settings.tracks)
        if args.track == "all"
        else [canonical_track_name(args.track) or args.track]
    )
    if tracks != ["directional_perps"]:
        raise SystemExit("Workspace-flow phase 1 supports only --track directional_perps")
    population_size = args.population_size or settings.population_size
    run_session_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    workspace_session = workspace_builder.initialize_session(
        track="directional_perps",
        run_session_id=run_session_id,
        family_scope=selected_families,
    )
    workspace_hooks = WorkspaceHooks(
        builder=workspace_builder,
        session=workspace_session,
    )

    try:
        next_iteration = 1
        if args.burn_in_iterations > 0:
            print(f"[run] starting burn-in phase iterations={args.burn_in_iterations}")
            next_iteration = await _run_directional_perps_iterations(
                settings=settings,
                provider=provider,
                lineage=lineage,
                mutator=mutator,
                evaluator=evaluator,
                web_researcher=web_researcher,
                hypothesis_sandbox=hypothesis_sandbox,
                population_size=population_size,
                family_scope=selected_families,
                skip_llm=True,
                iterations=args.burn_in_iterations,
                start_iteration=next_iteration,
                phase_label="burn_in",
                run_session_id=run_session_id,
                agent_label=str(args.agent_label or "autolab_harness"),
                workspace_session=workspace_session,
                workspace_builder=workspace_builder,
                workspace_hooks=workspace_hooks,
                planner_runner=planner_runner,
                writer_runner=writer_runner,
                optimizer_runner=optimizer_runner,
                reflector_runner=reflector_runner,
            )
        next_iteration = await _run_directional_perps_iterations(
            settings=settings,
            provider=provider,
            lineage=lineage,
            mutator=mutator,
            evaluator=evaluator,
            web_researcher=web_researcher,
            hypothesis_sandbox=hypothesis_sandbox,
            population_size=population_size,
            family_scope=selected_families,
            skip_llm=args.skip_llm,
            iterations=args.iterations,
            start_iteration=next_iteration,
            phase_label="main",
            run_session_id=run_session_id,
            agent_label=str(args.agent_label or "autolab_harness"),
            workspace_session=workspace_session,
            workspace_builder=workspace_builder,
            workspace_hooks=workspace_hooks,
            planner_runner=planner_runner,
            writer_runner=writer_runner,
            optimizer_runner=optimizer_runner,
            reflector_runner=reflector_runner,
        )
    finally:
        await web_researcher.close()
        await provider.close()


async def _run_directional_perps_iterations(
    *,
    settings: Any,
    provider: MarketDataProvider,
    lineage: LineageStore,
    mutator: CandidateMutator,
    evaluator: ResearchEvaluator,
    web_researcher: WebResearcher,
    hypothesis_sandbox: HypothesisSandbox,
    population_size: int,
    family_scope: str | list[str] | None,
    skip_llm: bool,
    iterations: int,
    start_iteration: int,
    phase_label: str,
    run_session_id: str,
    agent_label: str,
    workspace_session: Any,
    workspace_builder: WorkspaceBuilder,
    workspace_hooks: WorkspaceHooks,
    planner_runner: ResearchPlannerRunner,
    writer_runner: CandidateWriterRunner,
    optimizer_runner: OptunaOptimizerRunner,
    reflector_runner: ReflectionRunner,
) -> int:
    iteration_iter = count(start_iteration) if iterations == 0 else range(start_iteration, start_iteration + iterations)
    last_iteration = start_iteration
    track = "directional_perps"
    allowed_families = mutator._allowed_families(track, family=family_scope)
    allowed_features_by_family = mutator._allowed_features_by_family(track, family=family_scope)
    family_defaults = mutator._family_defaults(track, family=family_scope)

    for iteration_number in iteration_iter:
        last_iteration = iteration_number
        print(f"[run:{phase_label}] iteration={iteration_number}")
        seed_candidates = mutator.load_seed_candidates(track, family=family_scope)
        recent_rows = lineage.recent(track, limit=500)
        if skip_llm:
            parent = pick_deterministic_parent(
                track=track,
                lineage=lineage,
                seed_candidates=seed_candidates,
                iteration_number=iteration_number,
            )
        else:
            parent = pick_parent(track, lineage, seed_candidates)
        parent_hash = parent.strategy_hash()
        best = lineage.best(track)
        print(
            f"[{track}] parent={parent.family} {parent_hash} recent_best={best['aggregate_score']:.4f}"
            if best is not None
            else f"[{track}] parent={parent.family} {parent_hash}"
        )
        run_context = {
            "run_session_id": run_session_id,
            "agent_label": str(agent_label or "autolab_harness"),
            "phase_label": phase_label,
            "iteration_number": int(iteration_number),
            "deterministic": bool(skip_llm),
            "llm_phase": not bool(skip_llm),
            "force_novelty": False,
        }
        provider.begin_iteration_bundle(track=track, parent=parent)
        try:
            if skip_llm:
                market_summary = _minimal_research_summary(
                    track=track,
                    parent=parent,
                    provider=provider,
                    web_researcher=web_researcher,
                    run_context=run_context,
                )
            else:
                market_summary = await provider.build_research_summary(track, parent)
                market_summary["external_research"] = _tool_only_external_research(
                    web_researcher=web_researcher
                )
                market_summary["run_context"] = run_context
            iteration_paths = workspace_builder.update_iteration(
                session=workspace_session,
                parent=parent,
                iteration_number=iteration_number,
                phase_label=phase_label,
                force_novelty=bool(run_context["force_novelty"]),
                market_summary=market_summary,
            )
            current_state = dict(iteration_paths.get("session_state") or {})

            if skip_llm:
                candidates = await mutator.propose(
                    track=track,
                    parent=parent,
                    research_summary=market_summary,
                    recent_results=[],
                    memory_packet={},
                    population_size=population_size,
                    skip_llm=True,
                    family=family_scope,
                    exclude_hashes=set(),
                    llm_tools=[],
                    deterministic_recent_rows=recent_rows,
                    deterministic_seed_candidates=seed_candidates,
                )
                planner_result = None
                writer_result = None
                optimization_result = None
            else:
                planner_result = await planner_runner.run(
                    session=workspace_session,
                    iteration_number=iteration_number,
                    parent=parent,
                    market_bundle=dict(market_summary.get("market_bundle") or {}),
                    iteration_paths=iteration_paths,
                )
                target_family = str(planner_result.frontmatter.get("target_family") or parent.family)
                base_candidate_payload = _base_candidate_payload_for_family(
                    track=track,
                    family=target_family,
                    parent=parent,
                    lineage=lineage,
                    mutator=mutator,
                )
                workspace_builder.store_evidence_cache(
                    session=workspace_session,
                    parent_hash=parent_hash,
                    bundle_id=str(current_state.get("bundle_id") or ""),
                    open_question=str(current_state.get("open_question") or ""),
                    lesson_refs=list(current_state.get("selected_lesson_refs") or []),
                    probe_refs=list(planner_result.tool_refs),
                    experiment_refs=list(planner_result.evidence_paths),
                )
                writer_result = await writer_runner.run(
                    session=workspace_session,
                    research_note_path=planner_result.research_note_path,
                    iteration_paths=iteration_paths,
                    parent=parent,
                    base_candidate_payload=base_candidate_payload,
                )
                if not writer_result.accepted:
                    print(
                        f"[{track}] planner/writer preflight failed in iteration {iteration_number}: "
                        f"{writer_result.failure_reason or 'unknown failure'}"
                    )
                    planner_result = await planner_runner.run(
                        session=workspace_session,
                        iteration_number=iteration_number,
                        parent=parent,
                        market_bundle=dict(market_summary.get("market_bundle") or {}),
                        iteration_paths=iteration_paths,
                        repair_feedback=dict(writer_result.failure_packet or {}),
                        previous_note_path=planner_result.research_note_path,
                    )
                    writer_result = await writer_runner.run(
                        session=workspace_session,
                        research_note_path=planner_result.research_note_path,
                        iteration_paths=iteration_paths,
                        parent=parent,
                        base_candidate_payload=base_candidate_payload,
                    )
                if not writer_result.accepted or writer_result.candidate_payload is None:
                    print(
                        f"[{track}] llm_proposal_failed iteration={iteration_number} "
                        f"reason={writer_result.failure_reason or 'writer_rejected_candidate'}"
                    )
                    continue
                incumbent_detail = _incumbent_detail(lineage=lineage, track=track)
                try:
                    optimization_result = await optimizer_runner.run(
                        session=workspace_session,
                        base_payload=dict(writer_result.base_candidate_payload or base_candidate_payload),
                        candidate_payload=dict(writer_result.candidate_payload),
                        iteration_paths=iteration_paths,
                        incumbent_summary=(
                            dict(incumbent_detail.get("summary") or {})
                            if incumbent_detail is not None
                            else None
                        ),
                    )
                except Exception as exc:  # noqa: BLE001
                    print(
                        f"[{track}] optimizer_failed iteration={iteration_number} "
                        f"reason={type(exc).__name__}: {exc}"
                    )
                    continue
                optimized_payload = dict(optimization_result.candidate_payload)
                iteration_paths["candidate_json_path"].write_text(
                    json.dumps(optimized_payload, indent=2, ensure_ascii=True, default=str)
                )
                validated = CandidateGraph.from_dict(optimized_payload)
                if validated.strategy_hash() in {
                    row["candidate_hash"] for row in lineage.recent(track, limit=500)
                }:
                    print(f"[{track}] duplicate candidate {validated.strategy_hash()} skipped")
                    continue
                candidates = [validated]
                trial_context = {
                    "structure_spec_path": str(iteration_paths.get("structure_spec_path")),
                    "base_candidate_path": str(iteration_paths.get("base_candidate_path")),
                    "candidate_patch_path": str(iteration_paths.get("candidate_patch_path")),
                    "candidate_after_patch_path": str(iteration_paths.get("candidate_after_patch_path")),
                    "optuna_space_path": str(iteration_paths.get("optuna_space_path")),
                    "optuna_trials_path": str(iteration_paths.get("optuna_trials_path")),
                    "optuna_best_path": str(iteration_paths.get("optuna_best_path")),
                    "base_candidate_hash": (
                        CandidateGraph.from_dict(
                            dict(writer_result.base_candidate_payload or base_candidate_payload)
                        ).strategy_hash()
                        if dict(writer_result.base_candidate_payload or base_candidate_payload)
                        else None
                    ),
                    "writer_candidate_hash": CandidateGraph.from_dict(
                        dict(writer_result.candidate_payload)
                    ).strategy_hash(),
                    "optimized_candidate_hash": validated.strategy_hash(),
                    "patch_summary": list(writer_result.patch_summary or []),
                    "optimized_param_summary": summarize_patch(
                        build_candidate_patch(
                            base_payload=dict(writer_result.candidate_payload),
                            target_payload=optimized_payload,
                        )
                    ),
                    "score_diagnosis": dict(optimization_result.score_diagnosis or {}),
                    "optuna_trial_count": int(optimization_result.trial_count),
                    "optuna_best_params": dict(optimization_result.best_params or {}),
                    "fragility_penalty": optimization_result.fragility_penalty,
                    "promotion_score": optimization_result.promotion_score,
                    "fragility_pack": dict(optimization_result.fragility_pack or {}),
                    "stability_pack": dict(optimization_result.stability_pack or {}),
                    "audit_alignment": summarize_generalization(
                        optimization_result.best_summary,
                        stability_pack=optimization_result.stability_pack,
                    ).get("audit_alignment"),
                    "fragility_label": summarize_generalization(
                        optimization_result.best_summary,
                        stability_pack=optimization_result.stability_pack,
                    ).get("fragility_label"),
                }

            if not candidates:
                print(f"[{track}] no new candidate generated in iteration {iteration_number}")
                continue

            best_passing: dict[str, Any] | None = None
            best_passing_trial_context: dict[str, Any] | None = None
            for candidate in candidates:
                try:
                    evaluation = await evaluator.evaluate(
                        candidate,
                        fast_mode=bool(skip_llm),
                    )
                except Exception as exc:  # noqa: BLE001
                    print(
                        f"[{track}] {candidate.family} {candidate.strategy_hash()} "
                        f"failed={type(exc).__name__}: {exc}"
                    )
                    continue

                lesson_card_path = (
                    workspace_session.cards_dir / "reflections" / f"{evaluation['candidate_hash']}.md"
                )
                research_summary = dict(market_summary)
                research_summary["run_context"] = dict(run_context)
                research_summary["workspace"] = {
                    "root": str(workspace_session.root),
                    "iteration_dir": str(iteration_paths["iteration_dir"]),
                    "research_note_path": str(iteration_paths.get("research_note_path")),
                    "planner_contract_path": str(iteration_paths.get("planner_contract_path")),
                    "structure_spec_path": str(iteration_paths.get("structure_spec_path")),
                    "base_candidate_path": str(iteration_paths.get("base_candidate_path")),
                    "candidate_patch_path": str(iteration_paths.get("candidate_patch_path")),
                    "candidate_after_patch_path": str(iteration_paths.get("candidate_after_patch_path")),
                    "candidate_json_path": str(iteration_paths.get("candidate_json_path")),
                    "optuna_space_path": str(iteration_paths.get("optuna_space_path")),
                    "optuna_trials_path": str(iteration_paths.get("optuna_trials_path")),
                    "optuna_best_path": str(iteration_paths.get("optuna_best_path")),
                    "planner_trace_path": str(iteration_paths.get("planner_trace_path")),
                    "writer_trace_path": str(iteration_paths.get("writer_trace_path")),
                    "reflector_trace_path": str(iteration_paths.get("reflector_trace_path")),
                    "lesson_card_path": str(lesson_card_path),
                }
                if not skip_llm:
                    evaluated_trial_context = dict(trial_context)
                    evaluated_trial_context.update(
                        summarize_return_attribution(
                            evaluation.get("summary"),
                            evaluation.get("canonical_run"),
                        )
                    )
                    evaluated_trial_context.update(
                        summarize_generalization(
                            evaluation.get("summary"),
                            stability_pack=dict(optimization_result.stability_pack or {}) if optimization_result is not None else {},
                        )
                    )
                    evaluated_trial_context["stability_status"] = dict(
                        evaluated_trial_context.get("stability_pack") or {}
                    ).get("status")
                    evaluated_trial_context["stability_pass_fraction"] = dict(
                        evaluated_trial_context.get("stability_pack") or {}
                    ).get("passed_fraction")
                    evaluated_trial_context["motif_audit_streak"] = _motif_audit_streak(
                        lineage=lineage,
                        track=track,
                        candidate_payload=dict(evaluation.get("candidate") or {}),
                    )
                    research_summary["trial"] = evaluated_trial_context
                artifact_path = _write_artifact(settings, track, evaluation)
                lineage.record(
                    evaluation=evaluation,
                    parent_hash=parent_hash,
                    research_summary=research_summary,
                    artifact_path=str(artifact_path),
                )
                experiment_card_ref = workspace_hooks.after_experiment(
                    candidate_hash=evaluation["candidate_hash"],
                    iteration_number=iteration_number,
                )

                if not skip_llm:
                    reflection_packet = _reflection_evaluation_packet(
                        lineage=lineage,
                        evaluation=evaluation,
                        parent_hash=parent_hash,
                        experiment_card_ref=experiment_card_ref,
                        workspace_session=workspace_session,
                        current_state=current_state,
                        trial_context=research_summary.get("trial"),
                    )
                    reflection_result = await reflector_runner.run(
                        session=workspace_session,
                        candidate_hash=evaluation["candidate_hash"],
                        iteration_paths=iteration_paths,
                        evaluation_packet=reflection_packet,
                    )
                    workspace_builder.record_lesson_card(
                        session=workspace_session,
                        iteration_number=iteration_number,
                        candidate_hash=evaluation["candidate_hash"],
                        content=reflection_result.lesson_card_path.read_text(),
                    )
                    workspace_hooks.after_reflection()

                summary = evaluation["summary"]
                validation_fragment = ""
                if summary.get("validation_available") and summary.get("validation_total_return") is not None:
                    validation_fragment = (
                        f" validation={float(summary['validation_total_return']):.3%}"
                    )
                print(
                    f"[{track}] iter={iteration_number} {candidate.family} "
                    f"{evaluation['candidate_hash']} "
                    f"score={summary['aggregate_score']:.4f} "
                    f"sharpe={summary['median_sharpe']:.3f} "
                    f"return={summary['median_total_return']:.3%} "
                    f"{validation_fragment}"
                    f" passed={summary['passed']}"
                )

                if summary["passed"]:
                    if best_passing is None:
                        best_passing = evaluation
                        best_passing_trial_context = dict(research_summary.get("trial") or {})
                    elif skip_llm:
                        if summary["aggregate_score"] > best_passing["summary"]["aggregate_score"]:
                            best_passing = evaluation
                            best_passing_trial_context = dict(research_summary.get("trial") or {})
                    else:
                        candidate_trial_context = dict(research_summary.get("trial") or {})
                        if promotion_rank(summary, candidate_trial_context) > promotion_rank(
                            dict(best_passing.get("summary") or {}),
                            best_passing_trial_context,
                        ):
                            best_passing = evaluation
                            best_passing_trial_context = candidate_trial_context

            if best_passing is not None:
                lineage.promote(best_passing["candidate_hash"])
                print(
                    f"[{track}] promoted {best_passing['candidate_hash']} "
                    f"family={best_passing['candidate']['family']}"
                )
                workspace_hooks.after_experiment(
                    candidate_hash=best_passing["candidate_hash"],
                    iteration_number=iteration_number,
                )
            else:
                print(f"[{track}] no passing candidate in iteration {iteration_number}")
        finally:
            provider.clear_iteration_bundle()
    return last_iteration + 1


async def _run_iterations(
    *,
    settings: Any,
    provider: MarketDataProvider,
    lineage: LineageStore,
    mutator: CandidateMutator,
    evaluator: ResearchEvaluator,
    web_researcher: WebResearcher,
    hypothesis_sandbox: HypothesisSandbox,
    tracks: list[str],
    population_size: int,
    family_scope: str | list[str] | None,
    skip_llm: bool,
    iterations: int,
    start_iteration: int,
    phase_label: str,
    run_session_id: str,
) -> int:
    iteration_iter = count(start_iteration) if iterations == 0 else range(start_iteration, start_iteration + iterations)
    last_iteration = start_iteration
    for iteration_number in iteration_iter:
        last_iteration = iteration_number
        print(f"[run:{phase_label}] iteration={iteration_number}")
        for track in tracks:
            seed_candidates = mutator.load_seed_candidates(track, family=family_scope)
            recent_rows = lineage.recent(track, limit=500)
            if skip_llm:
                parent = pick_deterministic_parent(
                    track=track,
                    lineage=lineage,
                    seed_candidates=seed_candidates,
                    iteration_number=iteration_number,
                )
            else:
                parent = pick_parent(track, lineage, seed_candidates)
            parent_hash = parent.strategy_hash()
            print(
                f"[{track}] parent={parent.family} {parent_hash} "
                f"recent_best={lineage.best(track)['aggregate_score']:.4f}"
                if lineage.best(track) is not None
                else f"[{track}] parent={parent.family} {parent_hash}"
            )
            run_context = {
                "run_session_id": run_session_id,
                "phase_label": phase_label,
                "iteration_number": int(iteration_number),
                "deterministic": bool(skip_llm),
                "llm_phase": not bool(skip_llm),
                "force_novelty": False,
            }
            provider.begin_iteration_bundle(track=track, parent=parent)
            try:
                if skip_llm:
                    agent_recent_results: list[dict[str, Any]] = []
                    agent_memory_packet: dict[str, Any] = {}
                    research_summary = _minimal_research_summary(
                        track=track,
                        parent=parent,
                        provider=provider,
                        web_researcher=web_researcher,
                        run_context=run_context,
                    )
                    llm_tools: list[Any] = []
                else:
                    recent_results = lineage.recent(track, limit=5, include_deterministic=False)
                    if not recent_results:
                        recent_results = lineage.recent(track, limit=5)
                    agent_recent_results = _agent_safe_recent_results(recent_results)
                    research_summary = await provider.build_research_summary(track, parent)
                    research_summary["external_research"] = _tool_only_external_research(
                        web_researcher=web_researcher
                    )
                    memory_packet = lineage.memory_packet(
                        track=track,
                        parent=parent,
                        market_bundle=research_summary.get("market_bundle"),
                    )
                    run_context["force_novelty"] = bool(
                        iteration_number % max(3, population_size) == 0
                        or bool((memory_packet.get("novelty_pressure") or {}).get("required"))
                    )
                    research_summary["run_context"] = run_context
                    agent_memory_packet = _agent_safe_memory_packet(memory_packet)
                    research_summary["memory_packet"] = agent_memory_packet
                    llm_tools = [
                        *web_researcher.kimi_tools(),
                        *hypothesis_sandbox.kimi_tools(track=track, parent=parent),
                    ]
                candidates = await mutator.propose(
                    track=track,
                    parent=parent,
                    research_summary=research_summary,
                    recent_results=agent_recent_results,
                    memory_packet=agent_memory_packet,
                    population_size=population_size,
                    skip_llm=skip_llm,
                    family=family_scope,
                    exclude_hashes={
                        row["candidate_hash"] for row in lineage.recent(track, limit=200)
                    },
                    llm_tools=llm_tools,
                    deterministic_recent_rows=recent_rows,
                    deterministic_seed_candidates=seed_candidates,
                )
                if mutator.last_llm_trace is not None:
                    research_summary["llm_tool_trace"] = mutator.last_llm_trace
                    external_research = _external_research_from_llm_trace(
                        llm_trace=mutator.last_llm_trace,
                        web_researcher=web_researcher,
                    )
                    if external_research.get("reports"):
                        research_summary["external_research"] = external_research
                        lineage.record_query_cards(
                            track=track,
                            family=parent.family,
                            parent_hash=parent_hash,
                            market_bundle=research_summary.get("market_bundle"),
                            external_research=external_research,
                        )

                if not candidates:
                    print(f"[{track}] no new candidate generated in iteration {iteration_number}")
                    continue

                best_passing: dict[str, Any] | None = None
                for candidate in candidates:
                    try:
                        evaluation = await evaluator.evaluate(
                            candidate,
                            fast_mode=bool(skip_llm),
                        )
                    except Exception as exc:  # noqa: BLE001
                        print(
                            f"[{track}] {candidate.family} {candidate.strategy_hash()} "
                            f"failed={type(exc).__name__}: {exc}"
                        )
                        continue

                    artifact_path = _write_artifact(settings, track, evaluation)
                    lineage.record(
                        evaluation=evaluation,
                        parent_hash=parent_hash,
                        research_summary=research_summary,
                        artifact_path=str(artifact_path),
                    )

                    summary = evaluation["summary"]
                    validation_fragment = ""
                    if summary.get("validation_available") and summary.get("validation_total_return") is not None:
                        validation_fragment = (
                            f" validation={float(summary['validation_total_return']):.3%}"
                        )
                    audit_fragment = ""
                    if summary.get("audit_available") and summary.get("audit_total_return") is not None:
                        audit_fragment = (
                            f" audit={float(summary['audit_total_return']):.3%}"
                        )
                    print(
                        f"[{track}] iter={iteration_number} {candidate.family} "
                        f"{evaluation['candidate_hash']} "
                        f"score={summary['aggregate_score']:.4f} "
                        f"sharpe={summary['median_sharpe']:.3f} "
                        f"return={summary['median_total_return']:.3%} "
                        f"{validation_fragment}"
                        f"{audit_fragment}"
                        f" passed={summary['passed']}"
                    )

                    if summary["passed"]:
                        if best_passing is None or (
                            summary["aggregate_score"]
                            > best_passing["summary"]["aggregate_score"]
                        ):
                            best_passing = evaluation

                if best_passing is not None:
                    lineage.promote(best_passing["candidate_hash"])
                    print(
                        f"[{track}] promoted {best_passing['candidate_hash']} "
                        f"family={best_passing['candidate']['family']}"
                    )
                else:
                    print(f"[{track}] no passing candidate in iteration {iteration_number}")
            finally:
                provider.clear_iteration_bundle()
    return last_iteration + 1


async def inspect_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    _require_wayfinder_config(settings)
    settings.ensure_runtime_directories()
    lake = ParquetLake(settings.data_lake_dir)
    provider = MarketDataProvider(settings, lake)
    kimi = KimiClient(settings)
    web_researcher = WebResearcher(settings, lake)
    mutator = CandidateMutator(settings, kimi)
    lineage = LineageStore(settings.lineage_db_path)

    tracks = (
        list(settings.tracks)
        if args.track == "all"
        else [canonical_track_name(args.track) or args.track]
    )
    try:
        for track in tracks:
            parent = pick_parent(track, lineage, mutator.load_seed_candidates(track))
            provider.begin_iteration_bundle(track=track, parent=parent)
            try:
                recent_results = lineage.recent(track, limit=20)
                summary = await provider.build_research_summary(track, parent)
                summary["external_research"] = _tool_only_external_research(
                    web_researcher=web_researcher
                )
                summary["memory_packet"] = _agent_safe_memory_packet(
                    lineage.memory_packet(
                        track=track,
                        parent=parent,
                        market_bundle=summary.get("market_bundle"),
                    )
                )
                print(json.dumps(summary, indent=2))
            finally:
                provider.clear_iteration_bundle()
    finally:
        await web_researcher.close()
        await provider.close()


def lineage_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    lineage = LineageStore(settings.lineage_db_path)
    rows = lineage.list_rows(
        track=canonical_track_name(args.track) or args.track,
        limit=args.limit,
    )
    for row in rows:
        print(
            f"{row['created_at']} {row['track']} {row['family']} "
            f"{row['candidate_hash']} score={row['aggregate_score']:.4f} "
            f"passed={row['passed']} promoted={row['promoted']}"
        )


def dashboard_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    run_dashboard_server(settings, host=args.host, port=args.port)


def clear_passed_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    settings.ensure_runtime_directories()
    lineage = LineageStore(settings.lineage_db_path)
    track = None if args.track == "all" else (canonical_track_name(args.track) or args.track)
    result = lineage.clear_passed(track=track)
    scope = track or "all tracks"
    print(
        f"cleared passed experiments from {scope}: "
        f"experiments={result['experiments_deleted']} "
        f"artifacts={result['artifacts_deleted']} "
        f"promotions={result['promotions_deleted']} "
        f"query_cards={result['query_cards_deleted']}"
    )
    if result["candidate_hashes"]:
        preview = ", ".join(result["candidate_hashes"][:10])
        print(f"candidate_hashes={preview}")


async def promote_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    _require_wayfinder_config(settings)
    settings.ensure_runtime_directories()
    lineage = LineageStore(settings.lineage_db_path)
    kimi = KimiClient(settings)
    manager = LivePromotionManager(settings, lineage, kimi=kimi)
    config_path = resolve_path_from_root(
        args.config_path or settings.wayfinder_config_path,
        root_dir=settings.root_dir,
    )
    record = await manager.promote(
        candidate_hash=str(args.candidate),
        wallet_label=args.wallet_label,
        config_path=str(config_path),
        interval_seconds=args.interval_seconds,
        job_name=args.job_name,
        dry_run=not bool(args.live),
        llm_finalize=bool(args.llm_finalize),
        schedule=bool(args.schedule),
    )
    print(json.dumps(_display_promotion_record(settings=settings, record=record.to_dict()), indent=2))


def promotions_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    lineage = LineageStore(settings.lineage_db_path)
    if args.candidate:
        payload = lineage.promotion(str(args.candidate))
        print(json.dumps(_display_promotion_record(settings=settings, record=dict(payload or {})), indent=2))
        return

    rows = []
    for experiment in lineage.dashboard_rows():
        promotion = experiment.get("promotion")
        if promotion:
            rows.append(_display_promotion_record(settings=settings, record=dict(promotion)))
    print(json.dumps(rows, indent=2))


def benchmark_init_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    settings.ensure_runtime_directories()
    lineage = LineageStore(settings.lineage_db_path)
    kimi = KimiClient(settings)
    mutator = CandidateMutator(settings, kimi)
    payload = init_benchmark_deck(
        settings=settings,
        lineage=lineage,
        mutator=mutator,
        deck_name=str(args.deck),
        agent_label=str(args.agent_label),
        run_label=args.run_label,
        force=bool(args.force),
    )
    print(json.dumps(payload, indent=2))


async def benchmark_eval_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    _require_wayfinder_config(settings)
    settings.ensure_runtime_directories()
    lake = ParquetLake(settings.data_lake_dir)
    provider = MarketDataProvider(settings, lake)
    lineage = LineageStore(settings.lineage_db_path)
    kimi = KimiClient(settings)
    mutator = CandidateMutator(settings, kimi)
    evaluator = ResearchEvaluator(settings, provider)
    try:
        payload = await evaluate_benchmark_deck(
            settings=settings,
            lineage=lineage,
            mutator=mutator,
            evaluator=evaluator,
            provider=provider,
            deck_name=str(args.deck),
        )
    finally:
        await provider.close()
    print(json.dumps(payload, indent=2))


def benchmark_status_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    payload = benchmark_status_payload(
        settings=settings,
        deck_name=str(args.deck),
    )
    print(json.dumps(payload, indent=2))


def _write_artifact(
    settings: Any,
    track: str,
    evaluation: dict[str, Any],
) -> Path:
    target_dir = settings.artifact_dir / track
    target_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = target_dir / f"{timestamp}_{evaluation['candidate_hash']}.json"
    target.write_text(json.dumps(evaluation, indent=2))
    return target


def _parse_family_scope(
    family: str | None,
    families: str | None,
) -> str | list[str] | None:
    if family and families:
        raise SystemExit("Use either --family or --families, not both")
    if family:
        return family
    if not families:
        return None
    parsed = [item.strip() for item in str(families).split(",") if item.strip()]
    if not parsed:
        raise SystemExit("--families must contain at least one family")
    return parsed


def _require_wayfinder_config(settings: Any) -> Path:
    config_path = resolve_path_from_root(
        settings.wayfinder_config_path,
        root_dir=settings.root_dir,
    )
    if not config_path.exists():
        raise SystemExit(
            "WAYFINDER_CONFIG_PATH is required for this command and must point to an existing file. "
            f"Tried: {config_path}"
        )
    settings.wayfinder_config_path = config_path
    return config_path


def _display_promotion_record(*, settings: Any, record: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(record)
    for key in ["strategy_dir", "spec_path", "manifest_path", "readme_path", "config_path"]:
        normalized[key] = display_path(normalized.get(key), root_dir=settings.root_dir)
    return normalized


def _strip_audit_fields(payload: Any) -> Any:
    if isinstance(payload, dict):
        cleaned: dict[str, Any] = {}
        for key, value in payload.items():
            key_str = str(key)
            if key_str.startswith("audit_"):
                continue
            cleaned[key_str] = _strip_audit_fields(value)
        return cleaned
    if isinstance(payload, list):
        return [_strip_audit_fields(item) for item in payload]
    return payload


def _agent_safe_recent_results(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned_rows: list[dict[str, Any]] = []
    for row in rows:
        cleaned = dict(row)
        cleaned["summary"] = _strip_audit_fields(dict(row.get("summary") or {}))
        cleaned_rows.append(cleaned)
    return cleaned_rows


def _agent_safe_memory_packet(packet: dict[str, Any]) -> dict[str, Any]:
    return _strip_audit_fields(dict(packet or {}))


def _tool_only_external_research(*, web_researcher: WebResearcher) -> dict[str, Any]:
    return {
        "enabled": bool(web_researcher.is_configured),
        "provider": "tool_only",
        "queries": [],
        "reports": [],
    }


def _minimal_research_summary(
    *,
    track: str,
    parent: CandidateGraph,
    provider: MarketDataProvider,
    web_researcher: WebResearcher,
    run_context: dict[str, Any],
) -> dict[str, Any]:
    return {
        "track": track,
        "parent_family": parent.family,
        "parent_hash": parent.strategy_hash(),
        "market_bundle": dict(provider.current_bundle_context() or {}),
        "external_research": _tool_only_external_research(web_researcher=web_researcher),
        "run_context": dict(run_context),
        "memory_packet": {},
    }


def _incumbent_detail(*, lineage: LineageStore, track: str) -> dict[str, Any] | None:
    best = lineage.best(track)
    if best is None:
        return None
    candidate_hash = str(best.get("candidate_hash") or "")
    if not candidate_hash:
        return None
    return lineage.experiment_detail(candidate_hash)


def _base_candidate_payload_for_family(
    *,
    track: str,
    family: str,
    parent: CandidateGraph,
    lineage: LineageStore,
    mutator: CandidateMutator,
) -> dict[str, Any]:
    family_rows = lineage.dashboard_rows(track=track, family=family)
    if family_rows:
        family_rows.sort(
            key=lambda row: (
                int(bool(row.get("passed"))),
                int(bool(row.get("promoted"))),
                float(dict(row.get("summary") or {}).get("aggregate_score") or -1e18),
                str(row.get("created_at") or ""),
            ),
            reverse=True,
        )
        return dict(family_rows[0].get("candidate") or {})
    if parent.family == family:
        return parent.canonical_dict()
    seed_candidates = mutator.load_seed_candidates(track, family=family)
    if seed_candidates:
        return seed_candidates[0].canonical_dict()
    return parent.canonical_dict()


def _motif_audit_streak(
    *,
    lineage: LineageStore,
    track: str,
    candidate_payload: dict[str, Any],
    limit: int = 40,
) -> int:
    from wayfinder_autolab.orchestration.contracts import motif_signature

    target_motif = motif_signature(candidate_payload)
    streak = 0
    for row in lineage.recent(track, limit=limit, include_deterministic=False):
        row_candidate = dict(row.get("candidate") or {})
        if motif_signature(row_candidate) != target_motif:
            continue
        row_trial = dict(dict(row.get("research_summary") or {}).get("trial") or {})
        row_generalization = summarize_generalization(
            dict(row.get("summary") or {}),
            stability_pack=dict(row_trial.get("stability_pack") or {}),
        )
        alignment = str(
            row_trial.get("audit_alignment")
            or row_generalization.get("audit_alignment")
            or "not_run"
        )
        if alignment in {"negative", "mismatch"}:
            streak += 1
            continue
        break
    return streak


def _reflection_evaluation_packet(
    *,
    lineage: LineageStore,
    evaluation: dict[str, Any],
    parent_hash: str | None,
    experiment_card_ref: str | None,
    workspace_session: Any,
    current_state: dict[str, Any],
    trial_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from wayfinder_autolab.orchestration.contracts import motif_signature

    summary = _strip_audit_fields(dict(evaluation.get("summary") or {}))
    raw_summary = dict(evaluation.get("summary") or {})
    canonical_run = _strip_audit_fields(dict(evaluation.get("canonical_run") or {}))
    candidate = _strip_audit_fields(dict(evaluation.get("candidate") or {}))
    context_pack = dict(canonical_run.get("pre_audit_context_pack") or {})
    parent_delta: dict[str, Any] = {}
    if parent_hash:
        parent_detail = lineage.experiment_detail(parent_hash)
        if parent_detail is not None:
            parent_summary = _strip_audit_fields(dict(parent_detail.get("summary") or {}))
            for key in [
                "pre_audit_canonical_total_return",
                "validation_total_return",
                "median_total_return",
                "active_bar_fraction",
            ]:
                if summary.get(key) is None or parent_summary.get(key) is None:
                    continue
                parent_delta[f"{key}_delta"] = float(summary[key]) - float(parent_summary[key])
    changed_keys = list(summary.get("policy_sweep_changed_keys") or [])
    intended_vs_frozen = {}
    if changed_keys:
        intended_vs_frozen = {
            "material_change": bool(summary.get("policy_sweep_material_change")),
            "changed_keys": changed_keys,
            "proposed_policy": dict(summary.get("policy_sweep_proposed_policy") or {}),
            "frozen_policy": dict(summary.get("policy_sweep_frozen_policy") or {}),
        }
    drawdown_excerpt = dict(canonical_run.get("pre_audit_drawdown_pack") or {})
    regime_excerpt = dict(context_pack.get("trade_regime_pack") or {})
    gate_excerpt = dict(context_pack.get("gate_diagnostics") or {})
    gate_reasons = list(summary.get("gate_reasons") or [])
    gate_bottlenecks = list(summary.get("gate_bottleneck_tags") or [])
    trial_context = dict(trial_context or {})
    current_generalization = summarize_generalization(
        raw_summary,
        stability_pack=dict(trial_context.get("stability_pack") or {}),
    )
    trial_context.setdefault("fragility_penalty", current_generalization.get("fragility_penalty"))
    trial_context.setdefault("promotion_score", current_generalization.get("promotion_score"))
    trial_context.setdefault("audit_alignment", current_generalization.get("audit_alignment"))
    trial_context.setdefault("fragility_label", current_generalization.get("fragility_label"))
    trial_context.setdefault("fragility_pack", current_generalization.get("fragility_pack"))
    trial_context.setdefault("stability_pack", current_generalization.get("stability_pack"))
    trial_context.setdefault(
        "stability_status",
        dict(current_generalization.get("stability_pack") or {}).get("status"),
    )
    trial_context.setdefault(
        "stability_pass_fraction",
        dict(current_generalization.get("stability_pack") or {}).get("passed_fraction"),
    )
    dominant_failure_mode = (
        str(gate_bottlenecks[0])
        if gate_bottlenecks
        else (str(gate_reasons[0]) if gate_reasons else "needs_follow_up")
    )
    recent_rows = []
    for row in lineage.recent(str(evaluation.get("track") or ""), limit=12, include_deterministic=False):
        if str(row.get("candidate_hash") or "") == str(evaluation.get("candidate_hash") or ""):
            continue
        row_candidate = _strip_audit_fields(dict(row.get("candidate") or {}))
        row_summary = _strip_audit_fields(dict(row.get("summary") or {}))
        row_raw_summary = dict(row.get("summary") or {})
        row_trial = dict(dict(row.get("research_summary") or {}).get("trial") or {})
        row_generalization = summarize_generalization(
            row_raw_summary,
            stability_pack=dict(row_trial.get("stability_pack") or {}),
        )
        recent_rows.append(
            {
                "candidate_hash": row.get("candidate_hash"),
                "family": row.get("family"),
                "features": list(row_candidate.get("features") or []),
                "params": dict(row_candidate.get("params") or {}),
                "regime_gates": dict(row_candidate.get("regime_gates") or {}),
                "motif_signature": motif_signature(row_candidate),
                "pre_audit_canonical_total_return": row_summary.get("pre_audit_canonical_total_return"),
                "validation_total_return": row_summary.get("validation_total_return"),
                "median_total_return": row_summary.get("median_total_return"),
                "active_bar_fraction": row_summary.get("active_bar_fraction"),
                "passed": bool(row.get("passed")),
                "created_at": row.get("created_at"),
                "patch_summary": list(row_trial.get("patch_summary") or []),
                "optimized_param_summary": list(row_trial.get("optimized_param_summary") or []),
                "score_diagnosis": dict(row_trial.get("score_diagnosis") or {}),
                "return_driver": row_trial.get("return_driver"),
                "return_driver_source": row_trial.get("return_driver_source"),
                "exposure_profile": row_trial.get("exposure_profile"),
                "price_contribution": row_trial.get("price_contribution"),
                "carry_contribution": row_trial.get("carry_contribution"),
                "tx_cost_contribution": row_trial.get("tx_cost_contribution"),
                "best_regime_context": row_trial.get("best_regime_context"),
                "worst_regime_context": row_trial.get("worst_regime_context"),
                "fragility_penalty": row_trial.get("fragility_penalty", row_generalization.get("fragility_penalty")),
                "promotion_score": row_trial.get("promotion_score", row_generalization.get("promotion_score")),
                "audit_alignment": row_trial.get("audit_alignment", row_generalization.get("audit_alignment")),
                "fragility_label": row_trial.get("fragility_label", row_generalization.get("fragility_label")),
                "stability_pack": dict(row_trial.get("stability_pack") or row_generalization.get("stability_pack") or {}),
                "motif_audit_streak": row_trial.get("motif_audit_streak"),
            }
        )
        if len(recent_rows) >= 5:
            break
    return {
        "candidate_hash": evaluation.get("candidate_hash"),
        "family": candidate.get("family"),
        "candidate": candidate,
        "failed_motif_signature": motif_signature(candidate),
        "summary": summary,
        "parent_delta": parent_delta,
        "drawdown_excerpt": drawdown_excerpt,
        "regime_excerpt": regime_excerpt,
        "gate_excerpt": gate_excerpt,
        "intended_vs_frozen_diff": intended_vs_frozen,
        "dominant_failure_mode": dominant_failure_mode,
        "suggested_next_move": current_state.get("open_question"),
        "trial_context": trial_context,
        "structure_spec_ref": trial_context.get("structure_spec_path"),
        "base_candidate_ref": trial_context.get("base_candidate_path"),
        "patch_summary": list(trial_context.get("patch_summary") or []),
        "optimized_param_summary": list(trial_context.get("optimized_param_summary") or []),
        "score_diagnosis": dict(trial_context.get("score_diagnosis") or {}),
        "return_driver": trial_context.get("return_driver"),
        "return_driver_source": trial_context.get("return_driver_source"),
        "exposure_profile": trial_context.get("exposure_profile"),
        "price_contribution": trial_context.get("price_contribution"),
        "carry_contribution": trial_context.get("carry_contribution"),
        "tx_cost_contribution": trial_context.get("tx_cost_contribution"),
        "best_regime_context": trial_context.get("best_regime_context"),
        "worst_regime_context": trial_context.get("worst_regime_context"),
        "fragility_penalty": trial_context.get("fragility_penalty"),
        "promotion_score": trial_context.get("promotion_score"),
        "audit_alignment": trial_context.get("audit_alignment"),
        "fragility_label": trial_context.get("fragility_label"),
        "stability_pack": dict(trial_context.get("stability_pack") or {}),
        "motif_audit_streak": trial_context.get("motif_audit_streak"),
        "recent_completed_runs": recent_rows,
        "evidence_paths": [
            ref
            for ref in [
                experiment_card_ref,
                *list(current_state.get("selected_lesson_refs") or []),
                *list(current_state.get("selected_probe_refs") or []),
            ]
            if ref
        ],
        "workspace_root": str(workspace_session.root),
    }


def _external_research_from_llm_trace(
    *,
    llm_trace: dict[str, Any] | None,
    web_researcher: WebResearcher,
) -> dict[str, Any]:
    payload = _tool_only_external_research(web_researcher=web_researcher)
    trace = dict((llm_trace or {}).get("trace") or {})
    tool_calls = list(trace.get("tool_calls") or [])
    reports: list[dict[str, Any]] = []
    queries: list[str] = []
    for tool_call in tool_calls:
        if str(tool_call.get("name") or "") != "tavily_search":
            continue
        result = dict(tool_call.get("result") or {})
        if not bool(result.get("ok")):
            continue
        query = str(result.get("query") or "").strip()
        if not query:
            continue
        queries.append(query)
        reports.append(
            {
                "query": query,
                "answer": result.get("answer"),
                "insights": list(result.get("insights") or []),
                "sources": list(result.get("sources") or []),
            }
        )
    if reports:
        payload["provider"] = "tavily_tool_calls"
        payload["queries"] = queries
        payload["reports"] = reports
    return payload


def _pick_deterministic_parent(
    *,
    track: str,
    lineage: LineageStore,
    seed_candidates: list[Any],
    iteration_number: int,
) -> Any:
    recent_rows = lineage.recent(track, limit=500)
    deterministic_rows = [row for row in recent_rows if _row_is_deterministic(row)]
    family_counts: Counter[str] = Counter(str(row.get("family") or "") for row in deterministic_rows)
    seed_order = list(seed_candidates)
    min_count = min((family_counts.get(seed.family, 0) for seed in seed_order), default=0)
    least_used = [seed for seed in seed_order if family_counts.get(seed.family, 0) == min_count]
    if not least_used:
        return seed_order[0]
    return least_used[(iteration_number - 1) % len(least_used)]


def _row_is_deterministic(row: dict[str, Any]) -> bool:
    research_summary = dict(row.get("research_summary") or {})
    run_context = dict(research_summary.get("run_context") or {})
    if "deterministic" in run_context:
        return bool(run_context.get("deterministic"))
    return str(run_context.get("phase_label") or "").strip().lower() == "burn_in"


def _candidate_trade_style(candidate: dict[str, Any]) -> str:
    params = dict(candidate.get("params") or {})
    trade_style = str(params.get("trade_style") or "").strip().lower()
    return trade_style or "unspecified"


def _write_run_reflection(
    *,
    settings: Any,
    lineage: LineageStore,
    track: str,
    phase_label: str,
    family_scope: str | list[str] | None,
    run_session_id: str,
) -> tuple[Path | None, dict[str, Any] | None]:
    rows = [
        row
        for row in lineage.dashboard_rows(track=track)
        if (
            not _row_is_deterministic(row)
            and str(
                dict(dict(row.get("research_summary") or {}).get("run_context") or {}).get(
                    "run_session_id"
                )
                or ""
            )
            == run_session_id
        )
    ]
    if not rows:
        return None, None

    rows.sort(key=lambda row: str(row.get("created_at") or ""))
    recent_rows = rows[-5:]
    passed_rows = [row for row in rows if bool(row.get("passed"))]
    promoted_rows = [row for row in rows if bool(row.get("promoted"))]

    family_counts: Counter[str] = Counter()
    trade_style_counts: Counter[str] = Counter()
    feature_counts: Counter[str] = Counter()
    gate_reason_counts: Counter[str] = Counter()
    bottleneck_counts: Counter[str] = Counter()
    sweep_changed_key_counts: Counter[str] = Counter()
    active_fractions: list[float] = []
    pre_audit_returns: list[float] = []
    validation_returns: list[float] = []
    selector_returns: list[float] = []
    changed_param_counts: list[float] = []
    entry_score_drifts: list[float] = []
    exit_score_drifts: list[float] = []
    flip_score_drifts: list[float] = []
    holding_bar_drifts: list[float] = []
    cooldown_bar_drifts: list[float] = []
    material_sweep_changes = 0
    restrictive_count = 0
    low_activity_count = 0

    for row in rows:
        family = str(row.get("family") or "unknown")
        family_counts[family] += 1
        trade_style_counts[_candidate_trade_style(row.get("candidate") or {})] += 1
        feature_counts.update(str(feature) for feature in (row.get("candidate") or {}).get("features") or [])
        summary = dict(row.get("summary") or {})
        gate_reason_counts.update(str(reason) for reason in summary.get("gate_reasons") or [])
        bottleneck_counts.update(str(tag) for tag in summary.get("gate_bottleneck_tags") or [])
        active_fraction = summary.get("active_bar_fraction")
        if active_fraction is not None:
            numeric = float(active_fraction)
            active_fractions.append(numeric)
            if numeric <= 0.02:
                low_activity_count += 1
        pre_audit = summary.get("pre_audit_canonical_total_return")
        if pre_audit is not None:
            pre_audit_returns.append(float(pre_audit))
        validation = summary.get("validation_total_return")
        if validation is not None:
            validation_returns.append(float(validation))
        selector = summary.get("median_total_return")
        if selector is not None:
            selector_returns.append(float(selector))
        if bool(summary.get("policy_sweep_material_change")):
            material_sweep_changes += 1
        changed_keys = list(summary.get("policy_sweep_changed_keys") or [])
        sweep_changed_key_counts.update(str(key) for key in changed_keys)
        changed_param_counts.append(float(len(changed_keys)))
        proposed_policy = dict(summary.get("policy_sweep_proposed_policy") or {})
        frozen_policy = dict(summary.get("policy_sweep_frozen_policy") or {})
        _append_policy_delta(entry_score_drifts, proposed_policy, frozen_policy, "entry_abs_score")
        _append_policy_delta(exit_score_drifts, proposed_policy, frozen_policy, "exit_abs_score")
        _append_policy_delta(flip_score_drifts, proposed_policy, frozen_policy, "flip_abs_score")
        _append_policy_delta(holding_bar_drifts, proposed_policy, frozen_policy, "max_holding_bars")
        _append_policy_delta(cooldown_bar_drifts, proposed_policy, frozen_policy, "cooldown_bars")
        if "restrictive_regime_gate" in set(str(tag) for tag in summary.get("gate_bottleneck_tags") or []):
            restrictive_count += 1

    early_rows = rows[: min(5, len(rows))]
    late_rows = recent_rows
    early_pre_audit = [
        float(row["summary"]["pre_audit_canonical_total_return"])
        for row in early_rows
        if row["summary"].get("pre_audit_canonical_total_return") is not None
    ]
    late_pre_audit = [
        float(row["summary"]["pre_audit_canonical_total_return"])
        for row in late_rows
        if row["summary"].get("pre_audit_canonical_total_return") is not None
    ]
    early_active = [
        float(row["summary"]["active_bar_fraction"])
        for row in early_rows
        if row["summary"].get("active_bar_fraction") is not None
    ]
    late_active = [
        float(row["summary"]["active_bar_fraction"])
        for row in late_rows
        if row["summary"].get("active_bar_fraction") is not None
    ]

    allowed_families = (
        [family_scope]
        if isinstance(family_scope, str)
        else list(family_scope or [])
    )
    family_attempted = {family: family_counts.get(family, 0) for family in allowed_families}
    underexplored_families = [
        family for family, count in family_attempted.items() if count <= 1
    ]

    summary = {
        "llm_run_count": len(rows),
        "passed_count": len(passed_rows),
        "promoted_count": len(promoted_rows),
        "median_pre_audit_canonical_total_return": _median_or_none(pre_audit_returns),
        "median_validation_total_return": _median_or_none(validation_returns),
        "median_selector_total_return": _median_or_none(selector_returns),
        "median_active_bar_fraction": _median_or_none(active_fractions),
        "low_activity_share": _share(low_activity_count, len(rows)),
        "restrictive_gate_share": _share(restrictive_count, len(rows)),
        "material_sweep_change_share": _share(material_sweep_changes, len(rows)),
        "pre_audit_return_change_vs_first_five": _delta_median(late_pre_audit, early_pre_audit),
        "active_bar_fraction_change_vs_first_five": _delta_median(late_active, early_active),
    }
    intent_vs_sweep = {
        "material_change_share": _share(material_sweep_changes, len(rows)),
        "median_changed_param_count": _median_or_none(changed_param_counts),
        "most_changed_params": [
            {"param": key, "count": count}
            for key, count in sweep_changed_key_counts.most_common(6)
        ],
        "median_entry_abs_score_delta": _median_or_none(entry_score_drifts),
        "median_exit_abs_score_delta": _median_or_none(exit_score_drifts),
        "median_flip_abs_score_delta": _median_or_none(flip_score_drifts),
        "median_max_holding_bars_delta": _median_or_none(holding_bar_drifts),
        "median_cooldown_bars_delta": _median_or_none(cooldown_bar_drifts),
    }
    last_five_runs = []
    for row in reversed(recent_rows):
        summary_row = dict(row.get("summary") or {})
        changed_keys = list(summary_row.get("policy_sweep_changed_keys") or [])
        last_five_runs.append(
            {
                "candidate_hash": row.get("candidate_hash"),
                "parent_hash": row.get("parent_hash"),
                "family": row.get("family"),
                "hypothesis": str((row.get("candidate") or {}).get("hypothesis") or ""),
                "median_total_return": summary_row.get("median_total_return"),
                "validation_total_return": summary_row.get("validation_total_return"),
                "pre_audit_canonical_total_return": summary_row.get("pre_audit_canonical_total_return"),
                "active_bar_fraction": summary_row.get("active_bar_fraction"),
                "gate_bottlenecks": list(summary_row.get("gate_bottleneck_tags") or [])[:4],
                "sweep_drift": {
                    "material_change": bool(summary_row.get("policy_sweep_material_change")),
                    "changed_keys": changed_keys,
                    "changed_param_count": len(changed_keys),
                    "activity_penalty": summary_row.get("policy_sweep_activity_penalty"),
                    "proposed_policy": dict(summary_row.get("policy_sweep_proposed_policy") or {}),
                    "frozen_policy": dict(summary_row.get("policy_sweep_frozen_policy") or {}),
                },
            }
        )

    what_improved: list[str] = []
    if summary["pre_audit_return_change_vs_first_five"] is not None and summary["pre_audit_return_change_vs_first_five"] > 0.0:
        what_improved.append("Late-run pre-audit returns improved relative to the first five LLM runs.")
    if summary["active_bar_fraction_change_vs_first_five"] is not None and summary["active_bar_fraction_change_vs_first_five"] < 0.0:
        what_improved.append("Later runs became more selective on active bars.")
    if len(passed_rows) > 0:
        what_improved.append("The run produced at least one passing candidate in the non-deterministic phase.")

    what_failed: list[str] = []
    if summary["low_activity_share"] is not None and summary["low_activity_share"] >= 0.4:
        what_failed.append("Too many candidates survived only by trading almost nothing.")
    if summary["restrictive_gate_share"] is not None and summary["restrictive_gate_share"] >= 0.4:
        what_failed.append("Restrictive regime gating remained a dominant bottleneck.")
    if summary["material_sweep_change_share"] is not None and summary["material_sweep_change_share"] >= 0.4:
        what_failed.append("The policy sweep materially rewrote many proposals instead of only tuning them.")
    if intent_vs_sweep["median_changed_param_count"] is not None and intent_vs_sweep["median_changed_param_count"] >= 2.0:
        what_failed.append("Typical candidates changed multiple policy parameters between intent and frozen evaluation.")
    if family_counts:
        dominant_family, dominant_family_count = family_counts.most_common(1)[0]
        if dominant_family_count >= max(4, len(rows) - 1):
            what_failed.append(f"The run concentrated heavily in {dominant_family}.")

    areas_for_improvement: list[str] = []
    if underexplored_families:
        areas_for_improvement.append(
            "Underexplored families: " + ", ".join(sorted(underexplored_families))
        )
    if feature_counts:
        top_features = ", ".join(feature for feature, _count in feature_counts.most_common(4))
        areas_for_improvement.append(f"Overused feature neighborhood: {top_features}")
    if bottleneck_counts:
        top_bottlenecks = ", ".join(tag for tag, _count in bottleneck_counts.most_common(4))
        areas_for_improvement.append(f"Recurring gate bottlenecks: {top_bottlenecks}")
    if sweep_changed_key_counts:
        top_sweep_keys = ", ".join(key for key, _count in sweep_changed_key_counts.most_common(4))
        areas_for_improvement.append(f"Most frequently sweep-rewritten params: {top_sweep_keys}")
    if trade_style_counts:
        top_trade_style, top_trade_style_count = trade_style_counts.most_common(1)[0]
        if top_trade_style_count >= 4:
            areas_for_improvement.append(
                f"Trade-style concentration suggests more novelty pressure is needed outside {top_trade_style}."
            )

    reflection = _strip_audit_fields(
        {
            "created_at": datetime.now(UTC).isoformat(),
            "track": track,
            "phase_label": phase_label,
            "summary": summary,
            "intent_vs_sweep": intent_vs_sweep,
            "family_counts": [
                {"family": family, "count": count}
                for family, count in family_counts.most_common(8)
            ],
            "trade_style_counts": [
                {"trade_style": trade_style, "count": count}
                for trade_style, count in trade_style_counts.most_common(8)
            ],
            "overused_features": [
                {"feature": feature, "count": count}
                for feature, count in feature_counts.most_common(8)
            ],
            "gate_reasons": [
                {"reason": reason, "count": count}
                for reason, count in gate_reason_counts.most_common(8)
            ],
            "gate_bottlenecks": [
                {"tag": tag, "count": count}
                for tag, count in bottleneck_counts.most_common(8)
            ],
            "what_improved": what_improved,
            "what_failed": what_failed,
            "areas_for_improvement": areas_for_improvement,
            "last_five_runs": last_five_runs,
        }
    )

    target_dir = settings.artifact_dir / track / "run_reflections"
    target_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = target_dir / f"{timestamp}_{phase_label}.json"
    target.write_text(json.dumps(reflection, indent=2, ensure_ascii=True))
    _print_run_reflection(track=track, reflection=reflection)
    return target, reflection


def _print_run_reflection(*, track: str, reflection: dict[str, Any]) -> None:
    summary = dict(reflection.get("summary") or {})
    print(
        f"[{track}] run reflection: llm_runs={summary.get('llm_run_count', 0)} "
        f"passes={summary.get('passed_count', 0)} "
        f"median_pre_audit={_format_optional_pct(summary.get('median_pre_audit_canonical_total_return'))} "
        f"median_active={_format_optional_pct(summary.get('median_active_bar_fraction'))}"
    )
    intent_vs_sweep = dict(reflection.get("intent_vs_sweep") or {})
    print(
        f"[{track}] sweep drift: material_share={_format_optional_pct(intent_vs_sweep.get('material_change_share'))} "
        f"median_changed_params={_format_optional_number(intent_vs_sweep.get('median_changed_param_count'))}"
    )
    for line in list(reflection.get("what_improved") or [])[:3]:
        print(f"[{track}] improved: {line}")
    for line in list(reflection.get("what_failed") or [])[:3]:
        print(f"[{track}] failed: {line}")
    last_five_runs = list(reflection.get("last_five_runs") or [])[:5]
    if last_five_runs:
        print(f"[{track}] last five non-deterministic runs:")
    for row in last_five_runs:
        print(
            f"[{track}]   {row['candidate_hash']} family={row['family']} "
            f"median={_format_optional_pct(row.get('median_total_return'))} "
            f"validation={_format_optional_pct(row.get('validation_total_return'))} "
            f"pre_audit={_format_optional_pct(row.get('pre_audit_canonical_total_return'))} "
            f"active={_format_optional_pct(row.get('active_bar_fraction'))} "
            f"sweep_changes={len(list((row.get('sweep_drift') or {}).get('changed_keys') or []))} "
            f"bottlenecks={','.join(row.get('gate_bottlenecks') or [])}"
        )


def _median_or_none(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _delta_median(current: list[float], baseline: list[float]) -> float | None:
    if not current or not baseline:
        return None
    current_median = _median_or_none(current)
    baseline_median = _median_or_none(baseline)
    if current_median is None or baseline_median is None:
        return None
    return current_median - baseline_median


def _share(count: int, total: int) -> float | None:
    if total <= 0:
        return None
    return round(float(count) / float(total), 4)


def _format_optional_pct(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.2%}"
    except (TypeError, ValueError):
        return "n/a"


def _format_optional_number(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "n/a"


def _append_policy_delta(
    values: list[float],
    proposed_policy: dict[str, Any],
    frozen_policy: dict[str, Any],
    key: str,
) -> None:
    if key not in proposed_policy or key not in frozen_policy:
        return
    try:
        values.append(float(frozen_policy[key]) - float(proposed_policy[key]))
    except (TypeError, ValueError):
        return
