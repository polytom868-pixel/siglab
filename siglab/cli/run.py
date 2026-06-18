"""Run subcommand: the main research loop, inspect command, and all run-loop helpers."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from itertools import count
from pathlib import Path
from typing import Any

from siglab.config import load_settings
from siglab.data import MarketDataProvider
from siglab.evaluator import ResearchEvaluator
from siglab.llm import ClaudeClient
from siglab.orchestration import (
    ReflectionRunner,
    ResearchPlannerRunner,
    SpecWriterRunner,
    WorkspaceHooks,
)
from siglab.orchestration.run_context import build_run_context
from siglab.orchestration.trials import (
    summarize_generalization,
)
from siglab.research import HypothesisSandbox, WebResearcher
from siglab.run_config import (
    load_seed_specs_for_run as _load_seed_specs_for_run,
    override_seed_spec_symbols as _override_seed_spec_symbols,
    parse_symbol_override as _parse_symbol_override,
    resolve_resume_run as _resolve_resume_run,
    validate_symbol_override as _validate_symbol_override,
)
from siglab.schemas import SignalSpec
from siglab.search import (
    LineageStore,
    SpecMutator,
    pick_parent as _pick_parent_lib,
)
from siglab.track_registry import TRACK_CLI_CHOICES, resolve_track
from siglab.workspace import WorkspaceBuilder, WorkspaceSession
from siglab.io_utils import write_json
from siglab.cli.helpers import (
    require_sosovalue_config,
    parse_family_scope,
    write_artifact,
    strip_audit_fields,
    agent_safe_memory_packet,
    tool_only_external_research,
    base_spec_payload_for_family,
    incumbent_detail as _incumbent_detail,
    pick_deterministic_parent,
    row_is_deterministic,
    spec_trade_style,
    external_research_from_llm_trace,
    print_run_reflection_short,
)
from siglab.cli.rich_utils import print_error, print_info, print_json, print_success, print_warning


def add_subparser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    # run
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
        "--resume-run",
        default=None,
        help="Resume an existing run session by run_session_id and continue from the next iteration.",
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
    run_parser.add_argument(
        "--max-runtime-seconds",
        type=float,
        default=None,
        help="Stop cleanly after this wall-clock budget. Useful for bounded validation of --iterations 0.",
    )
    run_parser.add_argument("--max-total-cost", type=float, default=None)
    run_parser.add_argument(
        "--max-total-credits",
        type=float,
        default=None,
        help="Stop cooperatively when verified provider Credits telemetry reaches this budget. This is not USD.",
    )
    run_parser.add_argument(
        "--max-call-estimated-credits",
        type=float,
        default=None,
        help="Refuse a single B.AI call when pre-call estimated Credits exceeds this budget.",
    )
    run_parser.add_argument("--max-provider-errors", type=int, default=None)
    run_parser.add_argument("--max-consecutive-no-improvement", type=int, default=None)
    run_parser.add_argument("--max-consecutive-crashes", type=int, default=None)
    run_parser.add_argument("--cooldown-seconds-on-429", type=float, default=0.0)
    run_parser.add_argument("--provider-fallback-on-quota", action="store_true")
    run_parser.add_argument("--stop-on-live-surface-unavailable", action="store_true")
    run_parser.add_argument("--resume-safe-check", action="store_true")
    run_parser.add_argument(
        "--memory-scope",
        choices=["session_local", "track_shared"],
        default=None,
        help="Whether planner/search memory is isolated to this run or shared across the whole track.",
    )
    run_parser.add_argument(
        "--symbols",
        default=None,
        help="Comma-separated basis symbols to seed the run with. Cross-sectional families use the full list; pair families use the first two symbols.",
    )
    run_parser.add_argument(
        "--use-historical-seeds",
        action="store_true",
        default=None,
        help="Opt in to replacing static family seeds with the best historical artifact-backed family seeds.",
    )
    run_parser.add_argument("--skip-llm", action="store_true")
    run_parser.add_argument("--agent-label", default="siglab_harness")
    run_parser.add_argument("--run-label", default=None)

    # inspect
    inspect_parser = subparsers.add_parser("inspect")
    inspect_parser.add_argument(
        "--track",
        choices=["all", *TRACK_CLI_CHOICES],
        default="all",
    )


async def run_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    if getattr(args, "max_call_estimated_credits", None) is not None:
        settings.bai_max_call_credits = float(args.max_call_estimated_credits)
    require_sosovalue_config(settings)
    settings.ensure_runtime_directories()
    burn_in_iterations = int(getattr(args, "burn_in_iterations", 0) or 0)
    max_runtime_seconds = getattr(args, "max_runtime_seconds", None)
    if getattr(args, "max_total_cost", None) is not None:
        print(
            "--max-total-cost is not enforced yet because provider token/cost telemetry is not available; "
            "omit it or add real cost accounting first",
            file=sys.stderr,
        )
        raise SystemExit(1)
    loop_policy = {
        "max_total_cost": getattr(args, "max_total_cost", None),
        "max_total_credits": getattr(args, "max_total_credits", None),
        "max_call_estimated_credits": getattr(args, "max_call_estimated_credits", None),
        "max_provider_errors": getattr(args, "max_provider_errors", None),
        "max_consecutive_no_improvement": getattr(args, "max_consecutive_no_improvement", None),
        "max_consecutive_crashes": getattr(args, "max_consecutive_crashes", None),
        "cooldown_seconds_on_429": float(getattr(args, "cooldown_seconds_on_429", 0.0) or 0.0),
        "provider_fallback_on_quota": bool(getattr(args, "provider_fallback_on_quota", False)),
        "stop_on_live_surface_unavailable": bool(getattr(args, "stop_on_live_surface_unavailable", False)),
        "resume_safe_check": bool(getattr(args, "resume_safe_check", False)),
        "max_runtime_semantics": "between_iterations_cooperative",
    }
    runner_label = str(getattr(args, "agent_label", None) or getattr(args, "runner_label", None) or "siglab_harness")
    run_label = str(getattr(args, "run_label", None) or "").strip() or None
    selected_families = parse_family_scope(args.family, args.families)
    custom_symbols = _parse_symbol_override(getattr(args, "symbols", None))

    if custom_symbols is not None:
        provider = MarketDataProvider(settings, build_run_context(settings).lake)
        await _validate_symbol_override(provider=provider, custom_symbols=custom_symbols)
    use_historical_seeds = bool(getattr(args, "use_historical_seeds", False))
    memory_scope = getattr(args, "memory_scope", None) or "session_local"
    skip_llm = bool(getattr(args, "skip_llm", False))
    max_runtime_timestamp = (
        (datetime.now(UTC).timestamp() + max_runtime_seconds)
        if max_runtime_seconds and max_runtime_seconds > 0
        else None
    )

    tracks = (
        list(settings.tracks)
        if args.track == "all"
        else [resolve_track(args.track)]
    )
    for track in tracks:
        track_iterations = int(getattr(args, "iterations", 1) or 1)
        track_burn_in = int(burn_in_iterations) if burn_in_iterations > 0 else 0
        await _run_iterations(
            settings=settings,
            track=track,
            iterations=track_iterations,
            burn_in_iterations=track_burn_in,
            loop_policy=loop_policy,
            selected_families=selected_families,
            run_label=run_label,
            runner_label=runner_label,
            custom_symbols=custom_symbols,
            use_historical_seeds=use_historical_seeds,
            memory_scope=memory_scope,
            skip_llm=skip_llm,
            resume_run_session_id=getattr(args, "resume_run", None),
            population_size=int(args.population_size) if args.population_size else None,
            max_runtime_timestamp=max_runtime_timestamp,
        )


async def _run_iterations(
    *,
    settings: Any,
    track: str,
    iterations: int,
    burn_in_iterations: int,
    loop_policy: dict[str, Any],
    selected_families: str | list[str] | None,
    run_label: str | None,
    runner_label: str,
    custom_symbols: list[str] | None,
    use_historical_seeds: bool,
    memory_scope: str,
    skip_llm: bool,
    resume_run_session_id: str | None,
    population_size: int | None,
    max_runtime_timestamp: float | None,
) -> None:
    ctx = build_run_context(settings)
    provider = MarketDataProvider(settings, ctx.lake)
    claude = ctx.claude
    web_researcher = WebResearcher(settings, ctx.lake)
    ancestry = ctx.ancestry
    assert claude is not None, "build_run_context must yield a Claude client"
    assert ancestry is not None, "build_run_context must yield a LineageStore"
    mutator = SpecMutator(settings, claude)
    sandbox = HypothesisSandbox(settings, ctx.lake, provider)
    workspace = WorkspaceBuilder(settings, ancestry, mutator)
    planner = ResearchPlannerRunner(
        settings=settings,
        claude=claude,
        hypothesis_sandbox=sandbox,
        web_researcher=web_researcher,
        workspace_builder=workspace,
    )
    writer = SpecWriterRunner(settings=settings, claude=claude, mutator=mutator)
    evaluator = ResearchEvaluator(settings)
    if resume_run_session_id:
        resume = _resolve_resume_run(
            settings=settings,
            run_session_id=resume_run_session_id,
        )
        run_session_id = resume["workspace_root"].name
        session = workspace.resume_session(
            track=track,
            run_session_id=run_session_id,
            families=list(resume["families"]),
            memory_scope=str(resume["memory_scope"]),
            custom_symbols=list(resume["custom_symbols"] or []) or None,
            use_historical_seeds=bool(resume["use_historical_seeds"]),
        )
    else:
        run_session_id = "default"
        session = workspace.initialize_session(
            track=track,
            run_session_id=run_session_id,
            family_scope=selected_families,
            memory_scope=memory_scope,
            custom_symbols=custom_symbols,
            use_historical_seeds=use_historical_seeds,
        )
    hooks = WorkspaceHooks(builder=workspace, session=session)
    phase_label = "burn_in" if burn_in_iterations > 0 else "main"
    resume_safe_check_enabled = bool(loop_policy.get("resume_safe_check"))
    if resume_safe_check_enabled and resume_run_session_id:
        _resume_safe_check_internal(settings=settings, run_session_id=resume_run_session_id)
    if burn_in_iterations > 0:
        await _run_burn_in_phase(
            settings=settings,
            track=track,
            burn_in_iterations=burn_in_iterations,
            run_session_id=run_session_id,
            runner_label=runner_label,
            custom_symbols=custom_symbols,
            use_historical_seeds=use_historical_seeds,
            provider=provider,
            claude=claude,
            web_researcher=web_researcher,
            ancestry=ancestry,
            mutator=mutator,
            hooks=hooks,
            evaluator=evaluator,
        )
        phase_label = "main"

    seed_specs = _load_seed_specs_for_run(
        mutator=mutator,
        track=track,
        family_scope=selected_families,
        custom_symbols=custom_symbols,
        use_historical_seeds=use_historical_seeds,
    )
    trial_context: dict[str, Any] = {}
    iteration = count(1)
    while True:
        iteration_number = next(iteration)
        if iterations > 0 and iteration_number > iterations:
            break
        if max_runtime_timestamp and datetime.now(UTC).timestamp() >= max_runtime_timestamp:
            print_warning(f"[{track}] max runtime reached, stopping at iteration {iteration_number}")
            break

        if burn_in_iterations > 0 and iteration_number <= burn_in_iterations:
            continue

        parent = _pick_parent_lib(track, ancestry, seed_specs)
        provider.begin_iteration_bundle(track=track, parent=parent)
        try:
            try:
                research_summary = await provider.build_research_summary(track, parent)
            except Exception as exc:
                print_error(f"[{track}] research_summary failed: {exc}")
                continue

            research_summary["external_research"] = await _external_research_track(
                track=track,
                web_researcher=web_researcher,
                claude=claude,
                ancestry=ancestry,
                skip_llm=skip_llm,
            )
            research_summary["memory_packet"] = agent_safe_memory_packet(
                ancestry.memory_packet(
                    track=track,
                    parent=parent,
                    market_bundle=research_summary.get("market_bundle"),
                )
            )

            iteration_paths = workspace.update_iteration(
                session=session,
                parent=parent,
                iteration_number=iteration_number,
                phase_label=phase_label,
                force_novelty=False,
                market_summary=research_summary,
            )
            market_bundle = dict(research_summary.get("market_bundle") or {})

            planner_result = await planner.run(
                session=session,
                iteration_number=iteration_number,
                parent=parent,
                market_bundle=market_bundle,
                iteration_paths=iteration_paths,
            )

            base_spec_payload: dict[str, Any] | None = None
            if skip_llm:
                base_spec_payload = base_spec_payload_for_family(
                    track=track,
                    family=parent.family,
                    parent=parent,
                    ancestry=ancestry,
                    mutator=mutator,
                    custom_symbols=custom_symbols,
                    use_historical_seeds=use_historical_seeds,
                )
                if not base_spec_payload:
                    print_info(f"[{track}] no seed spec payload available, skipping iteration")
                    continue
                spec_payload = base_spec_payload
            else:
                writer_output = await writer.run(
                    session=session,
                    research_note_path=planner_result.research_note_path,
                    iteration_paths=iteration_paths,
                    parent=parent,
                    base_spec_payload=base_spec_payload,
                )
                spec_payload = writer_output.get("spec_payload") or {}
            if not spec_payload:
                print_error(f"[{track}] writer returned no spec payload")
                continue

            evaluation = evaluator.evaluate(
                track=track,
                spec=spec_payload,
                lineage=ancestry,
                trial_context=trial_context,
            )
            evaluation.setdefault("spec_hash", SignalSpec.from_dict(spec_payload).strategy_hash())
            evaluation.setdefault("spec", spec_payload)
            evaluation.setdefault("track", track)
            summary = dict(evaluation.get("summary") or {})
            evaluation.setdefault("passed", bool(summary.get("passed")))

            if not evaluation.get("spec_hash"):
                print_error(f"[{track}] evaluation returned empty spec_hash, skipping")
                continue

            if evaluation.get("spec") is None:
                evaluation["spec"] = spec_payload

            _ = write_artifact(settings, track, evaluation)  # noqa: F841
            hooks.after_experiment(
                spec_hash=str(evaluation["spec_hash"]),
                iteration_number=iteration_number,
            )

            # Write provider metrics
            _write_provider_metrics_artifact_internal(
                settings=settings,
                run_session_id=run_session_id,
                iteration_number=iteration_number,
                phase_label=phase_label,
                reason="iteration_complete",
                claude=claude,
            )

            # Check credit budget
            credit_stop = _credit_budget_stop_payload_internal(
                claude=claude,
                loop_policy=loop_policy,
                run_label=run_label or "",
                runner_label=runner_label,
                phase_label=phase_label,
                next_iteration=iteration_number + 1,
            )
            if credit_stop is not None:
                _write_loop_stop_internal(
                    settings=settings,
                    run_session_id=run_session_id,
                    reason="credit_budget_exhausted",
                    payload=credit_stop,
                )
                print_warning(f"[{track}] credit budget exhausted, stopping")
                break

            trial_context.setdefault(
                "structure_spec_path",
                str(iteration_paths.get("structure_spec_path") or ""),
            )
            trial_context.setdefault(
                "base_spec_path",
                str(iteration_paths.get("base_spec_path") or ""),
            )
            trial_context.setdefault("return_driver", summary.get("return_driver"))
            trial_context.setdefault("return_driver_source", summary.get("return_driver_source"))
            trial_context.setdefault("exposure_profile", summary.get("exposure_profile"))
            trial_context.setdefault("price_contribution", summary.get("price_contribution"))
            trial_context.setdefault("carry_contribution", summary.get("carry_contribution"))
            trial_context.setdefault("tx_cost_contribution", summary.get("tx_cost_contribution"))
            trial_context.setdefault("best_regime_context", summary.get("best_regime_context"))
            trial_context.setdefault("worst_regime_context", summary.get("worst_regime_context"))
            fragility_pack = dict(trial_context.get("fragility_pack") or {})
            if not fragility_pack and evaluation.get("canonical_run"):
                fragility_pack = dict(
                    dict(evaluation["canonical_run"].get("pre_audit_context_pack") or {}).get(
                        "fragility_pack"
                    )
                    or {}
                )
                trial_context["fragility_pack"] = fragility_pack
            current_generalization = summarize_generalization(
                dict(evaluation.get("summary") or {}),
                stability_pack=dict(trial_context.get("stability_pack") or {}),
            )
            trial_context.setdefault("fragility_penalty", current_generalization.get("fragility_penalty"))
            trial_context.setdefault("deployment_score", current_generalization.get("deployment_score"))
            trial_context.setdefault("audit_alignment", current_generalization.get("audit_alignment"))
            trial_context.setdefault("fragility_label", current_generalization.get("fragility_label"))
            trial_context.setdefault("stability_pack", current_generalization.get("stability_pack"))

            if skip_llm:
                continue

            reflection = await _reflect_on_iteration(
                track=track,
                evaluation=evaluation,
                ancestry=ancestry,
                session=session,
                iteration_paths=iteration_paths,
                trial_context=trial_context,
                run_session_id=run_session_id,
            )
            if reflection is not None:
                print_success(f"[{track}] reflection recorded at {reflection}")

        finally:
            provider.clear_iteration_bundle()

    _write_run_reflection_internal(
        settings=settings,
        ancestry=ancestry,
        track=track,
        phase_label=phase_label,
        family_scope=selected_families,
        run_session_id=run_session_id,
    )
    await web_researcher.close()
    await provider.close()


async def _run_burn_in_phase(
    *,
    settings: Any,
    track: str,
    burn_in_iterations: int,
    run_session_id: str,
    runner_label: str,
    custom_symbols: list[str] | None,
    use_historical_seeds: bool,
    provider: MarketDataProvider,
    claude: ClaudeClient,
    web_researcher: WebResearcher,
    ancestry: LineageStore,
    mutator: SpecMutator,
    hooks: WorkspaceHooks,
    evaluator: ResearchEvaluator,
) -> None:
    seed_specs = _load_seed_specs_for_run(
        mutator=mutator,
        track=track,
        family_scope=None,
        custom_symbols=custom_symbols,
        use_historical_seeds=use_historical_seeds,
    )
    for i in range(1, burn_in_iterations + 1):
        parent = pick_deterministic_parent(
            track=track,
            ancestry=ancestry,
            seed_specs=seed_specs,
            iteration_number=i,
        )
        provider.begin_iteration_bundle(track=track, parent=parent)
        try:
            try:
                research_summary = await provider.build_research_summary(track, parent)
            except Exception as exc:
                print_error(f"[{track}] burn_in research_summary failed: {exc}")
                continue
            research_summary["external_research"] = tool_only_external_research(
                web_researcher=web_researcher
            )
            research_summary["memory_packet"] = agent_safe_memory_packet(
                ancestry.memory_packet(
                    track=track,
                    parent=parent,
                    market_bundle=research_summary.get("market_bundle"),
                )
            )
            research_summary["run_context"] = {
                "phase_label": "burn_in",
                "iteration_number": i,
                "run_session_id": run_session_id,
                "runner_label": runner_label,
                "deterministic": True,
            }
            spec_payload = _override_seed_spec_symbols(parent, custom_symbols).canonical_dict()
            evaluation = evaluator.evaluate(
                track=track,
                spec=spec_payload,
                lineage=ancestry,
            )
            evaluation.setdefault("spec_hash", parent.strategy_hash())
            evaluation.setdefault("spec", spec_payload)
            evaluation.setdefault("track", track)
            summary = dict(evaluation.get("summary") or {})
            evaluation.setdefault("passed", bool(summary.get("passed")))
            write_artifact(settings, track, evaluation)
            hooks.after_experiment(
                spec_hash=str(evaluation["spec_hash"]),
                iteration_number=i,
            )
            _write_provider_metrics_artifact_internal(
                settings=settings,
                run_session_id=run_session_id,
                iteration_number=i,
                phase_label="burn_in",
                reason="burn_in_iteration_complete",
                claude=claude,
            )
        finally:
            provider.clear_iteration_bundle()


async def _external_research_track(
    *,
    track: str,
    web_researcher: WebResearcher,
    claude: ClaudeClient,
    ancestry: LineageStore,
    skip_llm: bool,
) -> dict[str, Any]:
    if skip_llm:
        return tool_only_external_research(web_researcher=web_researcher)
    recent = ancestry.recent(track, limit=1, include_deterministic=False)
    if not recent:
        return tool_only_external_research(web_researcher=web_researcher)
    tools = web_researcher.claude_tools()
    if not tools:
        return tool_only_external_research(web_researcher=web_researcher)
    await claude.complete_text_with_tools(
        system_prompt="You are a crypto strategy research assistant. Use the provided web search tools to gather up-to-date market and protocol context for the user's research task.",
        user_prompt=f"Research context for the {track} strategy track.",
        tools=tools,
        max_tokens=256,
        stage="external_research",
    )
    return external_research_from_llm_trace(
        llm_trace=dict(claude.last_trace or {}),
        web_researcher=web_researcher,
    )


async def _reflect_on_iteration(
    *,
    track: str,
    evaluation: dict[str, Any],
    ancestry: LineageStore,
    session: WorkspaceSession,
    iteration_paths: dict[str, Any],
    trial_context: dict[str, Any],
    run_session_id: str | None = None,
) -> str | None:
    parent_hash = evaluation.get("parent_hash")
    experiment_card_ref = _incumbent_detail(
        ancestry=ancestry,
        track=track,
    )
    if experiment_card_ref:
        experiment_card_ref = experiment_card_ref.get("spec_hash")
    current_state = dict(iteration_paths.get("session_state") or {})
    evaluation_packet = _reflection_evaluation_packet_internal(
        ancestry=ancestry,
        evaluation=evaluation,
        parent_hash=parent_hash,
        experiment_card_ref=experiment_card_ref,
        workspace_session=session,
        current_state=current_state,
        trial_context=trial_context,
        run_session_id=run_session_id,
    )
    from siglab.live import LiveDeploymentManager

    deployment_manager = LiveDeploymentManager(session.settings, ancestry)
    spec_hash = str(evaluation.get("spec_hash") or "")
    reflector = ReflectionRunner(
        settings=session.settings,
        claude=ClaudeClient(session.settings),
    )
    reflection = await reflector.run(
        session=session,
        spec_hash=spec_hash,
        iteration_paths=iteration_paths,
        evaluation_packet=evaluation_packet,
    )
    if reflection:
        summary_path = session.root / f"{spec_hash or 'unknown'}_reflection.json"
        write_json(summary_path, reflection)
        return str(summary_path)
    return None


def _reflection_evaluation_packet_internal(
    *,
    ancestry: Any,
    evaluation: dict[str, Any],
    parent_hash: str | None,
    experiment_card_ref: str | None,
    workspace_session: Any,
    current_state: dict[str, Any],
    trial_context: dict[str, Any] | None = None,
    run_session_id: str | None = None,
) -> dict[str, Any]:
    from siglab.orchestration.contracts import motif_signature

    summary = strip_audit_fields(dict(evaluation.get("summary") or {}))
    raw_summary = dict(evaluation.get("summary") or {})
    canonical_run = strip_audit_fields(dict(evaluation.get("canonical_run") or {}))
    spec = strip_audit_fields(dict(evaluation.get("spec") or {}))
    context_pack = dict(canonical_run.get("pre_audit_context_pack") or {})
    parent_delta: dict[str, Any] = {}
    if parent_hash:
        parent_detail = ancestry.experiment_detail(parent_hash)
        if parent_detail is not None:
            parent_summary = strip_audit_fields(dict(parent_detail.get("summary") or {}))
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
    trial_context.setdefault("deployment_score", current_generalization.get("deployment_score"))
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
    for row in ancestry.recent(
        str(evaluation.get("track") or ""),
        limit=12,
        include_deterministic=False,
        run_session_id=run_session_id,
    ):
        if str(row.get("spec_hash") or "") == str(evaluation.get("spec_hash") or ""):
            continue
        row_spec = strip_audit_fields(dict(row.get("spec") or {}))
        row_summary = strip_audit_fields(dict(row.get("summary") or {}))
        row_raw_summary = dict(row.get("summary") or {})
        row_trial = dict(dict(row.get("research_summary") or {}).get("trial") or {})
        row_generalization = summarize_generalization(
            row_raw_summary,
            stability_pack=dict(row_trial.get("stability_pack") or {}),
        )
        recent_rows.append(
            {
                "spec_hash": row.get("spec_hash"),
                "family": row.get("family"),
                "features": list(row_spec.get("features") or []),
                "params": dict(row_spec.get("params") or {}),
                "regime_gates": dict(row_spec.get("regime_gates") or {}),
                "motif_signature": motif_signature(row_spec),
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
                "deployment_score": row_trial.get("deployment_score", row_generalization.get("deployment_score")),
                "audit_alignment": row_trial.get("audit_alignment", row_generalization.get("audit_alignment")),
                "fragility_label": row_trial.get("fragility_label", row_generalization.get("fragility_label")),
                "stability_pack": dict(row_trial.get("stability_pack") or row_generalization.get("stability_pack") or {}),
                "motif_audit_streak": row_trial.get("motif_audit_streak"),
            }
        )
        if len(recent_rows) >= 5:
            break
    return {
        "spec_hash": evaluation.get("spec_hash"),
        "family": spec.get("family"),
        "spec": spec,
        "failed_motif_signature": motif_signature(spec),
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
        "base_spec_ref": trial_context.get("base_spec_path"),
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
        "deployment_score": trial_context.get("deployment_score"),
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


async def inspect_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    require_sosovalue_config(settings)
    settings.ensure_runtime_directories()
    ctx = build_run_context(settings)
    provider = MarketDataProvider(settings, ctx.lake)
    claude = ctx.claude
    web_researcher = WebResearcher(settings, ctx.lake)
    ancestry = ctx.ancestry
    assert claude is not None, "build_run_context must yield a Claude client"
    assert ancestry is not None, "build_run_context must yield a LineageStore"
    mutator = SpecMutator(settings, claude)
    tracks: list[str] = (
        [str(track) for track in list(settings.tracks)]
        if args.track == "all"
        else [str(resolve_track(args.track))]
    )
    try:
        for track in tracks:
            parent = _pick_parent_lib(track, ancestry, mutator.load_seed_specs(track))
            provider.begin_iteration_bundle(track=track, parent=parent)
            try:
                summary = await provider.build_research_summary(track, parent)
                summary["external_research"] = tool_only_external_research(
                    web_researcher=web_researcher
                )
                summary["memory_packet"] = agent_safe_memory_packet(
                    ancestry.memory_packet(
                        track=track,
                        parent=parent,
                        market_bundle=summary.get("market_bundle"),
                    )
                )
                print_json(summary)
            finally:
                provider.clear_iteration_bundle()
    finally:
        await web_researcher.close()
        await provider.close()


# ---------------------------------------------------------------------------
# Internal helpers for run loop
# ---------------------------------------------------------------------------


def _credit_budget_stop_payload_internal(
    *,
    claude: ClaudeClient,
    loop_policy: dict[str, Any],
    run_label: str,
    runner_label: str,
    phase_label: str,
    next_iteration: int,
) -> dict[str, Any] | None:
    limit = loop_policy.get("max_total_credits")
    if limit is None:
        return None
    try:
        max_total_credits = float(limit)
    except (TypeError, ValueError):
        return {
            "run_label": run_label,
            "runner_label": runner_label,
            "phase_label": phase_label,
            "next_iteration": next_iteration,
            "credits_estimate": None,
            "max_total_credits": limit,
            "provider_metrics": claude.metrics_snapshot(),
            "loop_policy": loop_policy,
            "policy_error": "invalid_max_total_credits",
        }
    metrics = claude.metrics_snapshot()
    usage = dict(metrics.get("usage") or {})
    credits = usage.get("credits_estimate")
    if credits is None:
        return None
    try:
        credits_float = float(credits)
    except (TypeError, ValueError):
        return None
    if credits_float < max_total_credits:
        return None
    return {
        "run_label": run_label,
        "runner_label": runner_label,
        "phase_label": phase_label,
        "next_iteration": next_iteration,
        "credits_estimate": round(credits_float, 6),
        "max_total_credits": max_total_credits,
        "credit_budget_semantics": "verified_bai_credits_between_iterations_cooperative",
        "provider_metrics": metrics,
        "loop_policy": loop_policy,
    }


def _write_loop_stop_internal(
    *,
    settings: Any,
    run_session_id: str,
    reason: str,
    payload: dict[str, Any],
) -> None:
    path = settings.artifact_dir / "loop_stops" / f"{run_session_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(
        path,
        {
            "run_session_id": run_session_id,
            "reason": reason,
            "created_at": datetime.now(UTC).isoformat(),
            **payload,
        },
    )


def _write_provider_metrics_artifact_internal(
    *,
    settings: Any,
    run_session_id: str,
    iteration_number: int,
    phase_label: str,
    reason: str,
    claude: ClaudeClient,
) -> Path:
    metrics_dir = settings.artifact_dir / "provider_metrics"
    metrics_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "recorded_at": datetime.now(UTC).isoformat(),
        "run_session_id": run_session_id,
        "iteration_number": int(iteration_number),
        "phase_label": phase_label,
        "reason": reason,
        "provider_metrics": claude.metrics_snapshot(),
    }
    latest_path = metrics_dir / f"{run_session_id}.latest.json"
    write_json(latest_path, payload)
    jsonl_path = metrics_dir / f"{run_session_id}.jsonl"
    with jsonl_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True, default=str) + "\n")
    return jsonl_path


def _resume_safe_check_internal(*, settings: Any, run_session_id: str) -> None:
    session_dir = settings.artifact_dir / "trend_signals" / "workspaces" / run_session_id
    if not session_dir.exists():
        print(f"--resume-safe-check failed: workspace session not found: {session_dir}", file=sys.stderr)
        raise SystemExit(1)
    state_path = session_dir / "current" / "SESSION_STATE.json"
    if not state_path.exists():
        print(f"--resume-safe-check failed: missing session state: {state_path}", file=sys.stderr)
        raise SystemExit(1)


def _write_run_reflection_internal(
    *,
    settings: Any,
    ancestry: Any,
    track: str,
    phase_label: str,
    family_scope: str | list[str] | None,
    run_session_id: str,
) -> tuple[Path | None, dict[str, Any] | None]:
    rows = [
        row
        for row in ancestry.dashboard_rows(track=track)
        if (
            not row_is_deterministic(row)
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
    deployd_rows = [row for row in rows if bool(row.get("deployd"))]

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
        trade_style_counts[spec_trade_style(row.get("spec") or {})] += 1
        feature_counts.update(str(feature) for feature in (row.get("spec") or {}).get("features") or [])
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
        _append_policy_delta_internal(entry_score_drifts, proposed_policy, frozen_policy, "entry_abs_score")
        _append_policy_delta_internal(exit_score_drifts, proposed_policy, frozen_policy, "exit_abs_score")
        _append_policy_delta_internal(flip_score_drifts, proposed_policy, frozen_policy, "flip_abs_score")
        _append_policy_delta_internal(holding_bar_drifts, proposed_policy, frozen_policy, "max_holding_bars")
        _append_policy_delta_internal(cooldown_bar_drifts, proposed_policy, frozen_policy, "cooldown_bars")
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
        "deployd_count": len(deployd_rows),
        "median_pre_audit_canonical_total_return": _median_or_none_internal(pre_audit_returns),
        "median_validation_total_return": _median_or_none_internal(validation_returns),
        "median_selector_total_return": _median_or_none_internal(selector_returns),
        "median_active_bar_fraction": _median_or_none_internal(active_fractions),
        "low_activity_share": _share_internal(low_activity_count, len(rows)),
        "restrictive_gate_share": _share_internal(restrictive_count, len(rows)),
        "material_sweep_change_share": _share_internal(material_sweep_changes, len(rows)),
        "pre_audit_return_change_vs_first_five": _delta_median_internal(late_pre_audit, early_pre_audit),
        "active_bar_fraction_change_vs_first_five": _delta_median_internal(late_active, early_active),
    }
    intent_vs_sweep = {
        "material_change_share": _share_internal(material_sweep_changes, len(rows)),
        "median_changed_param_count": _median_or_none_internal(changed_param_counts),
        "most_changed_params": [
            {"param": key, "count": count}
            for key, count in sweep_changed_key_counts.most_common(6)
        ],
        "median_entry_abs_score_delta": _median_or_none_internal(entry_score_drifts),
        "median_exit_abs_score_delta": _median_or_none_internal(exit_score_drifts),
        "median_flip_abs_score_delta": _median_or_none_internal(flip_score_drifts),
        "median_max_holding_bars_delta": _median_or_none_internal(holding_bar_drifts),
        "median_cooldown_bars_delta": _median_or_none_internal(cooldown_bar_drifts),
    }
    last_five_runs = []
    for row in reversed(recent_rows):
        summary_row = dict(row.get("summary") or {})
        changed_keys = list(summary_row.get("policy_sweep_changed_keys") or [])
        last_five_runs.append(
            {
                "spec_hash": row.get("spec_hash"),
                "parent_hash": row.get("parent_hash"),
                "family": row.get("family"),
                "hypothesis": str((row.get("spec") or {}).get("hypothesis") or ""),
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
        what_improved.append("The run produced at least one passing spec in the non-deterministic phase.")

    what_failed: list[str] = []
    if summary["low_activity_share"] is not None and summary["low_activity_share"] >= 0.4:
        what_failed.append("Too many specs survived only by trading almost nothing.")
    if summary["restrictive_gate_share"] is not None and summary["restrictive_gate_share"] >= 0.4:
        what_failed.append("Restrictive regime gating remained a dominant bottleneck.")
    if summary["material_sweep_change_share"] is not None and summary["material_sweep_change_share"] >= 0.4:
        what_failed.append("The policy sweep materially rewrote many proposals instead of only tuning them.")
    _median_changed = intent_vs_sweep["median_changed_param_count"]
    if isinstance(_median_changed, (int, float)) and _median_changed >= 2.0:
        what_failed.append("Typical specs changed multiple policy parameters between intent and frozen evaluation.")
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

    reflection = strip_audit_fields(
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
    write_json(target, reflection)
    print_run_reflection_short(track=track, reflection=reflection)
    return target, reflection


def _median_or_none_internal(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _delta_median_internal(current: list[float], baseline: list[float]) -> float | None:
    if not current or not baseline:
        return None
    current_median = _median_or_none_internal(current)
    baseline_median = _median_or_none_internal(baseline)
    if current_median is None or baseline_median is None:
        return None
    return current_median - baseline_median


def _share_internal(count: int, total: int) -> float | None:
    if total <= 0:
        return None
    return round(float(count) / float(total), 4)


def _append_policy_delta_internal(
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
