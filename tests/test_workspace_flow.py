from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from siglab.schemas import SignalSpec
from siglab.orchestration.contracts import conformance_violations
from siglab.orchestration.optimizer_runner import OptunaOptimizerRunner
from siglab.orchestration.planner_runner import ResearchPlannerRunner
from siglab.orchestration.reflector_runner import ReflectionRunner
from siglab.orchestration.trials import (
    deployment_rank,
    score_diagnosis,
    summarize_generalization,
    summarize_return_attribution,
)
from siglab.orchestration.writer_runner import SpecWriterRunner
from siglab.search.lineage import LineageStore
from siglab.search.mutate import SpecMutator
from siglab.tools import (
    inspect_feature,
    open_workspace_file,
    search_features,
    search_workspace,
    search_workspace_text,
    suggest_feature_set,
)
from siglab.workspace import WorkspaceBuilder
from siglab.workspace.cards import dump_frontmatter, parse_frontmatter


class WorkspaceFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]

    def test_writer_token_budget_expands_for_bai(self) -> None:
        runner = object.__new__(SpecWriterRunner)
        runner.settings = type("Settings", (), {"llm_provider": "bai"})()

        self.assertEqual(runner._writer_max_tokens(), 2200)
        self.assertEqual(runner._max_attempts(), 3)

    def test_writer_repair_prompt_relaxes_near_flat_gates(self) -> None:
        runner = object.__new__(SpecWriterRunner)

        prompt = runner._repair_prompt(
            repair_packet={
                "family": "perp_multi_asset_decision",
                "errors": ["gate_lint: gated_spec_is_near_flat"],
            }
        )

        self.assertIn("relax or remove entry gates", prompt)
        self.assertIn('"regime_gates":{"entry":[],"exit_on_break":false}', prompt)

    def test_bai_planner_requires_tool_use(self) -> None:
        runner = object.__new__(ResearchPlannerRunner)
        runner.settings = SimpleNamespace(llm_provider="bai")
        fake_tool = SimpleNamespace(name="search_workspace")

        issues = runner._planner_tool_usage_issues(  # type: ignore[attr-defined]
            tools=[fake_tool],  # type: ignore[list-item]
            trace={"tool_rounds_used": 0, "tool_calls": []},
        )
        clean = runner._planner_tool_usage_issues(  # type: ignore[attr-defined]
            tools=[fake_tool],  # type: ignore[list-item]
            trace={"tool_rounds_used": 1, "tool_calls": [{"name": "search_workspace"}]},
        )

        self.assertEqual(issues, ["planner_did_not_call_workspace_or_probe_tool"])
        self.assertEqual(clean, [])

    def test_bai_planner_caps_tool_rounds_to_reduce_loop_waste(self) -> None:
        runner = object.__new__(ResearchPlannerRunner)
        runner.settings = SimpleNamespace(llm_provider="bai", claude_max_tool_rounds=12)

        self.assertEqual(runner._planner_max_tool_rounds(), 4)  # type: ignore[attr-defined]
        self.assertEqual(runner._planner_max_tokens(), 2600)  # type: ignore[attr-defined]

    def test_planner_promotes_churn_reflection_to_policy_axis(self) -> None:
        runner = object.__new__(ResearchPlannerRunner)
        parent = SimpleNamespace(
            family="perp_multi_asset_decision",
            universe=SimpleNamespace(basis_groups=["BTC", "ETH"]),
            params={},
        )
        contract = runner._extract_planner_contract(  # type: ignore[attr-defined]
            note_text=(
                "## Diagnosis\n"
                "The failure mode is high_position_flip_rate from score sign flips and churn.\n"
                "## What to test\n"
                "Introduce position persistence with max_holding_bars and wider flip_abs_score.\n"
            ),
            note_body=(
                "## Diagnosis\n"
                "The failure mode is high_position_flip_rate from score sign flips and churn.\n"
                "## What to test\n"
                "Introduce position persistence with max_holding_bars and wider flip_abs_score.\n"
            ),
            raw_frontmatter={},
            yaml_fragments=[],
            parent=parent,
            current_state={"open_question": "How do we reduce churn?"},
            tool_refs=[],
            session=SimpleNamespace(families=["perp_multi_asset_decision"]),
        )

        self.assertEqual(contract["required_variation_axis"], "policy")
        self.assertEqual(
            contract["gate_intent"],
            {"type": "suppress_policy_churn", "target_dimension": "policy_persistence"},
        )
        self.assertEqual(contract["required_gate_dimensions"], ["policy_persistence"])
        self.assertIn("policy/persistence controls", contract["must_answer"])

    def test_policy_axis_conformance_rejects_feature_only_patch(self) -> None:
        parent_payload = {
            "family": "perp_multi_asset_decision",
            "features": ["ema_gap_12_26"],
            "params": {
                "entry_abs_score": 0.2,
                "exit_abs_score": 0.1,
                "flip_abs_score": 0.2,
                "max_holding_bars": 0,
                "cooldown_bars": 0,
                "min_abs_score": 0.2,
            },
        }
        feature_only = {
            **parent_payload,
            "features": ["ema_gap_12_26", "relative_momentum_24h"],
            "params": dict(parent_payload["params"]),
        }
        policy_patch = {
            **feature_only,
            "params": {
                **parent_payload["params"],
                "flip_abs_score": 0.35,
                "max_holding_bars": 8,
            },
        }
        contract = {
            "target_family": "perp_multi_asset_decision",
            "required_variation_axis": "policy",
        }

        self.assertIn(
            "spec does not include the required policy/persistence axis of variation",
            conformance_violations(
                planner_contract=contract,
                spec_payload=feature_only,
                parent_payload=parent_payload,
            ),
        )
        self.assertNotIn(
            "spec does not include the required policy/persistence axis of variation",
            conformance_violations(
                planner_contract=contract,
                spec_payload=policy_patch,
                parent_payload=parent_payload,
            ),
        )

    def test_non_bai_planner_preserves_larger_tool_round_budget(self) -> None:
        runner = object.__new__(ResearchPlannerRunner)
        runner.settings = SimpleNamespace(llm_provider="claude", claude_max_tool_rounds=12)

        self.assertEqual(runner._planner_max_tool_rounds(), 8)  # type: ignore[attr-defined]

    def test_legacy_test_planner_does_not_require_tool_use_without_provider(self) -> None:
        runner = object.__new__(ResearchPlannerRunner)
        runner.settings = SimpleNamespace()
        fake_tool = SimpleNamespace(name="search_workspace")

        self.assertEqual(
            runner._planner_tool_usage_issues(  # type: ignore[attr-defined]
                tools=[fake_tool],  # type: ignore[list-item]
                trace={"tool_rounds_used": 0, "tool_calls": []},
            ),
            [],
        )

    def test_planner_prompt_includes_compact_evidence_summary_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact_dir = root / "runs"
            evidence_dir = artifact_dir / "evidence"
            evidence_dir.mkdir(parents=True)
            (evidence_dir / "btc.summary.json").write_text(
                json.dumps(
                    {
                        "record_count": 604,
                        "link_count": 4,
                        "module_counts": {"ETF": 600, "Feeds": 4},
                        "relation_counts": {"news_mention": 4},
                        "source_counts": {"sosovalue.featured_news_by_currency": 2},
                        "entity_counts": {"BTC": 4},
                        "top_links": [
                            {
                                "relation": "feed_event_near_etf_flow",
                                "feed_entity": "BTC",
                                "warning": "temporal/categorical link only; not causal",
                                "feed_title": "ETF flow context",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            workspace = root / "workspace"
            current_dir = workspace / "current"
            cache_dir = workspace / "cache"
            current_dir.mkdir(parents=True)
            cache_dir.mkdir()
            runner = object.__new__(ResearchPlannerRunner)
            runner.settings = SimpleNamespace(root_dir=root, artifact_dir=artifact_dir)
            session = SimpleNamespace(root=workspace, current_dir=current_dir, cache_dir=cache_dir)
            parent = SimpleNamespace(
                family="perp_multi_asset_carry",
                universe=SimpleNamespace(basis_groups=["BTC", "ETH"]),
            )

            prompt = runner._build_user_prompt(session=session, parent=parent)  # type: ignore[arg-type]

            self.assertIn("Latest Source-Backed Evidence Summary", prompt)
            self.assertIn("runs/evidence/btc.summary.json", prompt)
            self.assertIn('"scope": "global"', prompt)
            self.assertIn('"matched_entities": [', prompt)
            self.assertIn("feed_event_near_etf_flow", prompt)
            self.assertIn("not causal", prompt)

    def test_planner_prompt_skips_irrelevant_global_evidence_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact_dir = root / "runs"
            evidence_dir = artifact_dir / "evidence"
            evidence_dir.mkdir(parents=True)
            (evidence_dir / "eth.summary.json").write_text(
                json.dumps({"record_count": 1, "entity_counts": {"ETH": 1}, "top_links": []}),
                encoding="utf-8",
            )
            workspace = root / "workspace"
            current_dir = workspace / "current"
            cache_dir = workspace / "cache"
            current_dir.mkdir(parents=True)
            cache_dir.mkdir()
            runner = object.__new__(ResearchPlannerRunner)
            runner.settings = SimpleNamespace(root_dir=root, artifact_dir=artifact_dir)
            session = SimpleNamespace(root=workspace, current_dir=current_dir, cache_dir=cache_dir)
            parent = SimpleNamespace(
                family="perp_multi_asset_carry",
                universe=SimpleNamespace(basis_groups=["BTC", "SOL"]),
            )

            prompt = runner._build_user_prompt(session=session, parent=parent)  # type: ignore[arg-type]

            self.assertNotIn("Latest Source-Backed Evidence Summary", prompt)

    def test_planner_prompt_prefers_workspace_scoped_evidence_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact_dir = root / "runs"
            global_dir = artifact_dir / "evidence"
            global_dir.mkdir(parents=True)
            (global_dir / "global.summary.json").write_text(
                json.dumps({"record_count": 1, "link_count": 0, "top_links": [{"feed_title": "global"}]}),
                encoding="utf-8",
            )
            workspace = root / "workspace"
            current_dir = workspace / "current"
            cache_dir = workspace / "cache"
            current_dir.mkdir(parents=True)
            cache_dir.mkdir()
            (current_dir / "evidence_summary.json").write_text(
                json.dumps({"record_count": 2, "link_count": 0, "top_links": [{"feed_title": "workspace"}]}),
                encoding="utf-8",
            )
            runner = object.__new__(ResearchPlannerRunner)
            runner.settings = SimpleNamespace(root_dir=root, artifact_dir=artifact_dir)
            session = SimpleNamespace(root=workspace, current_dir=current_dir, cache_dir=cache_dir)
            parent = SimpleNamespace(
                family="perp_multi_asset_carry",
                universe=SimpleNamespace(basis_groups=["BTC", "ETH"]),
            )

            prompt = runner._build_user_prompt(session=session, parent=parent)  # type: ignore[arg-type]

            self.assertIn('"scope": "workspace_current"', prompt)
            self.assertIn("workspace", prompt)
            self.assertNotIn("global.summary.json", prompt)

    def test_planner_rejects_uncalled_probe_claims(self) -> None:
        runner = object.__new__(ResearchPlannerRunner)

        issues = runner._planner_probe_claim_issues(  # type: ignore[attr-defined]
            note_text="Next test: call probe_spec_gate_impact with a volatility gate.",
            trace={"tool_calls": [{"name": "search_workspace"}]},
        )
        clean = runner._planner_probe_claim_issues(  # type: ignore[attr-defined]
            note_text="The probe_spec_gate_impact result supports a volatility gate.",
            trace={"tool_calls": [{"name": "probe_spec_gate_impact"}]},
        )

        self.assertEqual(issues, ["planner_named_uncalled_probe:probe_spec_gate_impact"])
        self.assertEqual(clean, [])

    def test_planner_contract_records_trace_tool_names(self) -> None:
        runner = object.__new__(ResearchPlannerRunner)
        contract = {"tools_used": ["search_workspace"]}

        runner._merge_trace_tool_usage(  # type: ignore[attr-defined]
            contract,
            trace={
                "tool_calls": [
                    {"name": "search_workspace"},
                    {"name": "open_file"},
                    {"name": "probe_spec_gate_impact"},
                ]
            },
        )

        self.assertEqual(
            contract["tools_used"],
            ["search_workspace", "open_file", "probe_spec_gate_impact"],
        )

    def test_planner_probe_tool_budget_refuses_excess_calls(self) -> None:
        runner = object.__new__(ResearchPlannerRunner)

        class FakeTool:
            name = "probe_feature_forward_stats"

            def handler(self, _arguments: dict[str, object]) -> dict[str, object]:
                raise AssertionError("budgeted-out probe should not execute")

        handler = runner._wrap_probe_tool(  # type: ignore[attr-defined]
            session=SimpleNamespace(track="trend_signals", memory_scope="session_local", run_session_id="run"),
            iteration_number=1,
            parent=SimpleNamespace(family="perp_multi_asset_decision", universe=SimpleNamespace(basis_groups=["BTC"])),
            market_bundle={"bundle_id": "bundle"},
            tool=FakeTool(),
            tool_refs=[],
            probe_budget={
                "total": ResearchPlannerRunner.MAX_PROBE_TOOL_CALLS,
                "per_tool": {"probe_feature_forward_stats": ResearchPlannerRunner.MAX_PROBE_CALLS_PER_TOOL},
            },
        )

        result = self.async_run(handler({"feature": "price_return_72h"}))

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "planner_probe_budget_exhausted")

    def test_planner_rejects_budget_exhausted_probe_trace(self) -> None:
        runner = object.__new__(ResearchPlannerRunner)

        issues = runner._planner_probe_budget_issues(  # type: ignore[attr-defined]
            trace={
                "tool_calls": [
                    {
                        "name": "probe_feature_forward_stats",
                        "result": {
                            "ok": False,
                            "error": "planner_probe_budget_exhausted",
                            "probe_type": "probe_feature_forward_stats",
                        },
                    }
                ]
            }
        )

        self.assertEqual(
            issues,
            ["planner_probe_budget_exhausted:probe_feature_forward_stats"],
        )

    def test_planner_rejects_excess_total_tool_calls(self) -> None:
        runner = object.__new__(ResearchPlannerRunner)

        issues = runner._planner_total_tool_budget_issues(  # type: ignore[attr-defined]
            trace={
                "tool_calls": [
                    {"name": "open_file"}
                    for _ in range(ResearchPlannerRunner.MAX_PLANNER_TOOL_CALLS + 1)
                ]
            }
        )

        self.assertEqual(
            issues,
            [f"planner_tool_call_budget_exceeded:{ResearchPlannerRunner.MAX_PLANNER_TOOL_CALLS + 1}>{ResearchPlannerRunner.MAX_PLANNER_TOOL_CALLS}"],
        )

    def test_planner_rejects_truncated_or_forced_final_notes(self) -> None:
        runner = object.__new__(ResearchPlannerRunner)

        issues = runner._planner_finish_issues(  # type: ignore[attr-defined]
            trace={
                "response_finish_reason": "length",
                "error": "max_tool_rounds_exhausted_forced_final",
            },
            note_text="## Next Test\n-",
        )

        self.assertIn("planner_response_truncated:length", issues)
        self.assertIn("planner_trace_error:max_tool_rounds_exhausted_forced_final", issues)
        self.assertIn("planner_note_ends_mid_list", issues)

    def test_planner_repair_disables_tools_after_budget_or_truncation_failure(self) -> None:
        runner = object.__new__(ResearchPlannerRunner)

        self.assertTrue(
            runner._repair_should_disable_tools(  # type: ignore[attr-defined]
                {"semantic_issues": ["planner_trace_error:max_tool_rounds_exhausted_forced_final"]}
            )
        )
        self.assertTrue(
            runner._repair_should_disable_tools(  # type: ignore[attr-defined]
                {"semantic_issues": ["planner_response_truncated:length"]}
            )
        )
        self.assertFalse(runner._repair_should_disable_tools({"semantic_issues": ["no_target_family"]}))  # type: ignore[attr-defined]

    def _settings(self, tmp: str) -> SimpleNamespace:
        return SimpleNamespace(
            root_dir=self.repo_root,
            artifact_dir=Path(tmp) / "runs",
            ancestry_db_path=Path(tmp) / "ancestry.db",
        )

    def test_session_initializer_and_iteration_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = self._settings(tmp)
            evidence_dir = settings.artifact_dir / "evidence"
            evidence_dir.mkdir(parents=True)
            (evidence_dir / "latest.summary.json").write_text(
                json.dumps({"record_count": 2, "link_count": 1, "top_links": []}),
                encoding="utf-8",
            )
            ancestry = LineageStore(settings.ancestry_db_path)
            mutator = SpecMutator(settings, claude=SimpleNamespace())
            builder = WorkspaceBuilder(
                settings=settings,
                ancestry=ancestry,
                mutator=mutator,
            )
            session = builder.initialize_session(
                track="trend_signals",
                run_session_id="session-1",
                family_scope=None,
            )
            self.assertTrue((session.root / "RUNBOOK.md").exists())
            self.assertTrue((session.manifests_dir / "constraints.md").exists())
            self.assertTrue((session.manifests_dir / "regime_catalog.md").exists())
            self.assertTrue((session.manifests_dir / "policy_surface.md").exists())
            self.assertTrue((session.manifests_dir / "family" / "perp_multi_asset_carry.json").exists())
            self.assertTrue((session.manifests_dir / "features" / "feature_surface.md").exists())
            self.assertTrue((session.manifests_dir / "features" / "feature_catalog.jsonl").exists())
            self.assertTrue((session.manifests_dir / "features" / "family" / "perp_multi_asset_carry.json").exists())
            self.assertTrue((session.cookbooks_dir / "carry_patterns.md").exists())
            self.assertTrue((session.indexes_dir / "experiment_index.jsonl").exists())
            self.assertTrue((session.current_dir / "incumbent_spec.yaml").exists())
            self.assertTrue((session.current_dir / "family_incumbents.json").exists())
            self.assertTrue((session.current_dir / "recent_trials.md").exists())
            self.assertTrue((session.current_dir / "evidence_summary.json").exists())
            self.assertTrue((session.indexes_dir / "trial_index.jsonl").exists())
            seeded_evidence = json.loads((session.current_dir / "evidence_summary.json").read_text())
            self.assertEqual(seeded_evidence["workspace_scope"]["run_session_id"], "session-1")
            self.assertEqual(seeded_evidence["record_count"], 2)
            runbook = (session.root / "RUNBOOK.md").read_text()
            carry_manifest = (session.manifests_dir / "family" / "perp_multi_asset_carry.md").read_text()
            carry_cookbook = (session.cookbooks_dir / "carry_patterns.md").read_text()
            self.assertIn("current/recent_trials.md", runbook)
            self.assertNotIn("current/thesis_ledger.json", runbook)
            self.assertIn("## Formula operators", carry_manifest)
            self.assertIn("Novel feature formulas are allowed", carry_manifest)
            self.assertIn("## Alias definitions", carry_manifest)
            self.assertIn("relative_carry_z_72h", carry_manifest)
            self.assertIn("carry in spirit", carry_manifest)
            self.assertIn("cross-sectional ranked long/short book", carry_manifest)
            self.assertIn("not limited to funding-only features", carry_manifest)
            self.assertIn("Carry Plus Relative Momentum", carry_cookbook)
            self.assertIn("relative_momentum_24h", carry_cookbook)
            self.assertIn("Carry Plus Trend Quality", carry_cookbook)

            parent = mutator.load_seed_specs("trend_signals")[0]
            state = builder.update_iteration(
                session=session,
                parent=parent,
                iteration_number=1,
                phase_label="burn_in",
                force_novelty=False,
                market_summary={
                    "market_bundle": {
                        "bundle_id": "bundle-1",
                        "as_of": "2026-03-14T00:00:00Z",
                        "symbols": ["BTC", "ETH"],
                    },
                    "perp_snapshot": [{"symbol": "BTC", "price": 100.0}],
                },
            )
            self.assertTrue((session.current_dir / "SESSION_STATE.json").exists())
            self.assertTrue((session.root / "TASK.md").exists())
            self.assertEqual(state["session_state"]["run_session_id"], "session-1")
            self.assertEqual(state["session_state"]["current_parent_hash"], parent.strategy_hash())

    def test_workspace_defaults_to_session_local_isolation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = self._settings(tmp)
            ancestry = LineageStore(settings.ancestry_db_path)
            mutator = SpecMutator(settings, claude=SimpleNamespace())
            builder = WorkspaceBuilder(
                settings=settings,
                ancestry=ancestry,
                mutator=mutator,
            )

            archived = mutator.load_seed_specs("trend_signals", family="perp_multi_asset_carry")[0]
            ancestry.record(
                evaluation={
                    "spec": archived.canonical_dict(),
                    "spec_hash": archived.strategy_hash(),
                    "summary": {
                        "aggregate_score": 6.0,
                        "median_sharpe": 1.0,
                        "median_cagr": 0.04,
                        "median_total_return": 0.03,
                        "passed": True,
                        "gate_reasons": [],
                    },
                },
                parent_hash=None,
                research_summary={
                    "track": "trend_signals",
                    "run_context": {
                        "run_session_id": "older-run",
                        "phase_label": "main",
                        "deterministic": False,
                    },
                },
                artifact_path="artifact.json",
            )

            isolated = builder.initialize_session(
                track="trend_signals",
                run_session_id="new-run",
                family_scope=None,
            )
            self.assertEqual(isolated.memory_scope, "session_local")
            self.assertEqual(len(list((isolated.cards_dir / "experiments").glob("*.md"))), 0)
            self.assertIn("No completed trials yet.", (isolated.current_dir / "recent_trials.md").read_text())

            global_session = builder.initialize_session(
                track="trend_signals",
                run_session_id="global-run",
                family_scope=None,
                memory_scope="track_shared",
            )
            self.assertEqual(global_session.memory_scope, "track_shared")
            self.assertTrue((global_session.cards_dir / "experiments" / f"{archived.strategy_hash()}.md").exists())

    def test_recent_trials_render_uses_coarse_result_labels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = self._settings(tmp)
            ancestry = LineageStore(settings.ancestry_db_path)
            mutator = SpecMutator(settings, claude=SimpleNamespace())
            builder = WorkspaceBuilder(
                settings=settings,
                ancestry=ancestry,
                mutator=mutator,
            )
            session = builder.initialize_session(
                track="trend_signals",
                run_session_id="session-results-only",
                family_scope=None,
            )
            parent = mutator.load_seed_specs("trend_signals")[0]
            spec_hash = parent.strategy_hash()
            (session.cards_dir / "reflections" / f"{spec_hash}.md").write_text(
                "---\nfamily: perp_multi_asset_carry\n---\n\n"
                "What changed: kept the carry structure intact.\n"
                "Why it failed/worked: validation held up but audit did not.\n"
            )
            entry = builder._trial_entry_from_row(
                session=session,
                row={
                    "spec_hash": spec_hash,
                    "family": parent.family,
                    "passed": False,
                    "created_at": "2026-03-16T05:00:00Z",
                    "summary": {
                        "aggregate_score": 5.25,
                        "validation_available": True,
                        "validation_total_return": 0.02,
                        "pre_audit_canonical_total_return": 0.015,
                        "audit_available": True,
                        "audit_total_return": -0.01,
                    },
                        "research_summary": {
                        "trial": {
                            "patch_summary": ["params.min_abs_score: 0.12 -> 0.14"],
                            "optimized_param_summary": ["risk.max_asset_weight: 0.35 -> 0.30"],
                            "return_driver": "price_dominant",
                            "return_driver_source": "decomposition",
                            "exposure_profile": "net_long",
                            "price_contribution": 0.2872,
                            "carry_contribution": 0.0315,
                            "tx_cost_contribution": -0.012,
                            "best_regime_context": "market_volatility/high_volatility",
                            "worst_regime_context": "funding_regime/funding_compressed",
                            "fragility_penalty": 2.5,
                            "deployment_score": 2.75,
                            "audit_alignment": "negative",
                            "fragility_label": "fragile",
                            "stability_status": "fragile",
                            "stability_pass_fraction": 0.5,
                            "motif_audit_streak": 2,
                            "score_diagnosis": score_diagnosis(
                                {
                                    "aggregate_score": 5.25,
                                    "median_sharpe": 1.1,
                                    "median_total_return": 0.04,
                                    "median_calmar": 1.2,
                                    "asset_breadth": 3,
                                    "profitable_window_pct": 0.5,
                                    "worst_max_drawdown": -0.08,
                                    "validation_total_return": 0.02,
                                    "pre_audit_canonical_total_return": 0.015,
                                },
                                {
                                    "aggregate_score": 5.5,
                                    "median_sharpe": 1.3,
                                    "median_total_return": 0.045,
                                    "median_calmar": 1.3,
                                    "asset_breadth": 3,
                                    "profitable_window_pct": 0.55,
                                    "worst_max_drawdown": -0.07,
                                    "validation_total_return": 0.03,
                                    "pre_audit_canonical_total_return": 0.02,
                                },
                            ),
                        },
                    },
                },
            )

            self.assertIsNotNone(entry)
            self.assertEqual(entry["validation_result"], "positive")
            self.assertEqual(entry["pre_audit_result"], "positive")
            self.assertEqual(entry["audit_result"], "negative")

            rendered = builder._render_recent_trials([entry])
            self.assertIn("validation=`positive` pre_audit=`positive` audit=`negative`", rendered)
            self.assertIn("driver=`price_dominant` exposure=`net_long`", rendered)
            self.assertIn("price=`+28.72%` carry=`+3.15%` tx=`-1.20%`", rendered)
            self.assertIn("best=`market_volatility/high_volatility`", rendered)
            self.assertIn("worst=`funding_regime/funding_compressed`", rendered)
            self.assertIn("audit_alignment=`negative`", rendered)
            self.assertIn("fragility=`fragile`", rendered)
            self.assertIn("deployment_score=`2.750`", rendered)
            self.assertIn("pass_fraction=`50.00%`", rendered)
            self.assertIn("motif_audit_streak=`2`", rendered)
            self.assertNotIn("aggregate_score_delta", rendered)
            self.assertNotIn("Validation delta=", rendered)
            self.assertNotIn("pre-audit delta=", rendered)

    def test_summarize_return_attribution_uses_price_carry_decomposition(self) -> None:
        attribution = summarize_return_attribution(
            {
                "pre_audit_canonical_total_return": 0.30,
            },
            {
                "metrics_by_period": {
                    "columns": ["equity", "fee_amount", "funding_amount"],
                    "rows": [
                        [1.0, 0.0, 0.0],
                        [1.3, 0.012, -0.0315],
                    ],
                },
                "pre_audit_drawdown_pack": {
                    "dominant_position_direction": "net_long",
                    "top_feature_contributors": [
                        {"feature": "price_return_24h"},
                        {"feature": "funding_72h_mean"},
                    ],
                },
                "pre_audit_context_pack": {
                    "trade_regime_pack": {
                        "market_volatility": {
                            "best_label": "high_volatility",
                            "worst_label": "low_volatility",
                        },
                    },
                },
            },
        )

        self.assertEqual(attribution["return_driver"], "price_dominant")
        self.assertEqual(attribution["return_driver_source"], "decomposition")
        self.assertEqual(attribution["exposure_profile"], "net_long")
        self.assertAlmostEqual(attribution["price_contribution"], 0.2805)
        self.assertAlmostEqual(attribution["carry_contribution"], 0.0315)
        self.assertAlmostEqual(attribution["tx_cost_contribution"], -0.012)
        self.assertEqual(attribution["best_regime_context"], "market_volatility/high_volatility")
        self.assertEqual(attribution["worst_regime_context"], "market_volatility/low_volatility")

    def test_summarize_generalization_penalizes_fragility(self) -> None:
        generalization = summarize_generalization(
            {
                "aggregate_score": 10.0,
                "validation_total_return": 0.04,
                "pre_audit_canonical_total_return": 0.09,
                "audit_available": True,
                "audit_total_return": -0.02,
                "active_bar_fraction": 0.05,
            },
            stability_pack={"status": "fragile", "passed_fraction": 0.5, "stability_penalty": 1.25},
        )

        self.assertGreater(generalization["fragility_penalty"], 0.0)
        self.assertLess(generalization["deployment_score"], 10.0)
        self.assertEqual(generalization["audit_alignment"], "negative")
        self.assertEqual(generalization["fragility_label"], "fragile")
        self.assertAlmostEqual(generalization["fragility_pack"]["generalization_gap"], 0.05)
        self.assertAlmostEqual(generalization["fragility_pack"]["activity_shortfall"], 0.10)

    def test_summarize_generalization_penalizes_turnover_extremes_and_low_bar_count(self) -> None:
        generalization = summarize_generalization(
            {
                "aggregate_score": 9.0,
                "validation_total_return": 0.03,
                "pre_audit_canonical_total_return": 0.025,
                "audit_available": True,
                "audit_total_return": 0.0,
                "active_bar_fraction": 0.02,
            },
            evaluation={
                "windows": [
                    {"used_for_selector": True, "stats": {"total_return": 0.08, "sharpe": 1.8}},
                    {"used_for_selector": True, "stats": {"total_return": -0.03, "sharpe": 0.2}},
                    {"used_for_selector": True, "stats": {"total_return": 0.01, "sharpe": 0.7}},
                ],
                "canonical_run": {
                    "metrics_by_period": {
                        "index": ["t1", "t2", "t3", "t4", "t5"],
                        "columns": ["equity", "turnover", "fee_amount", "funding_amount"],
                        "rows": [
                            [1.0, 0.12, 0.0, 0.0],
                            [1.01, 0.14, 0.003, -0.001],
                            [1.0, 0.16, 0.003, -0.001],
                            [1.02, 0.18, 0.003, -0.001],
                            [1.025, 0.12, 0.003, -0.001],
                        ],
                    },
                },
            },
            optuna_space={
                "parameters": [
                    {"path": "params.min_abs_score", "kind": "float", "low": 0.05, "high": 0.30, "default": 0.12},
                    {"path": "risk.max_leverage", "kind": "float", "low": 0.5, "high": 2.0, "default": 1.0},
                ]
            },
            tuned_params={
                "params.min_abs_score": 0.29,
                "risk.max_leverage": 1.98,
            },
            stability_pack={"status": "ok", "passed_fraction": 1.0, "stability_penalty": 0.0},
        )

        fragility_pack = generalization["fragility_pack"]
        self.assertGreater(fragility_pack["turnover_penalty"], 0.0)
        self.assertGreater(fragility_pack["tx_cost_penalty"], 0.0)
        self.assertGreater(fragility_pack["selector_variation_penalty"], 0.0)
        self.assertGreater(fragility_pack["extreme_param_penalty"], 0.0)
        self.assertGreater(fragility_pack["low_bar_penalty"], 0.0)
        self.assertEqual(fragility_pack["active_bar_count"], 0)
        self.assertEqual(generalization["fragility_label"], "fragile")

    def test_deployment_rank_prefers_higher_deployment_score(self) -> None:
        higher_raw = (
            {"aggregate_score": 11.0, "validation_total_return": 0.02, "pre_audit_canonical_total_return": 0.03},
            {"deployment_score": 8.5},
        )
        lower_raw_better_generalization = (
            {"aggregate_score": 10.2, "validation_total_return": 0.04, "pre_audit_canonical_total_return": 0.05},
            {"deployment_score": 9.4},
        )

        self.assertGreater(
            deployment_rank(*lower_raw_better_generalization),
            deployment_rank(*higher_raw),
        )

    def test_optuna_warm_start_filters_incompatible_gate_thresholds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = self._settings(tmp)
            settings.optuna_trials = 3
            ancestry = LineageStore(settings.ancestry_db_path)
            mutator = SpecMutator(settings, claude=SimpleNamespace())
            session = SimpleNamespace(track="trend_signals", families=["perp_multi_asset_carry"])
            current_payload = mutator.load_seed_specs("trend_signals", family="perp_multi_asset_carry")[0].canonical_dict()
            current_payload["regime_gates"] = {
                "entry": [{"expression": "funding_dispersion_72h", "min": 0.00001}],
                "exit_on_break": True,
            }

            matching_payload = json.loads(json.dumps(current_payload))
            matching_payload["params"]["min_abs_score"] = 0.14
            matching_payload["regime_gates"]["entry"][0]["min"] = 0.00002
            mismatched_payload = json.loads(json.dumps(current_payload))
            mismatched_payload["params"]["min_abs_score"] = 0.16
            mismatched_payload["regime_gates"]["entry"][0] = {
                "expression": "market_volatility_168h",
                "min": 0.007,
            }

            for payload, aggregate in ((matching_payload, 7.0), (mismatched_payload, 6.0)):
                spec = mutator._validate_spec(
                    spec=SignalSpec.from_dict(payload),
                    track="trend_signals",
                    allowed_families=["perp_multi_asset_carry"],
                    allowed_features_by_family=mutator._allowed_features_by_family("trend_signals"),
                    family_defaults=mutator._family_defaults("trend_signals"),
                )
                evaluation = {
                    "spec_hash": spec.strategy_hash(),
                    "spec": spec.canonical_dict(),
                    "summary": {"aggregate_score": aggregate, "passed": True},
                }
                ancestry.record(
                    evaluation=evaluation,
                    parent_hash=None,
                    research_summary={},
                    artifact_path="artifact.json",
                )

            runner = OptunaOptimizerRunner(
                settings=settings,
                evaluator=SimpleNamespace(),
                mutator=mutator,
                ancestry=ancestry,
            )
            optuna_space = {
                "family": "perp_multi_asset_carry",
                "parameters": [
                    {"path": "params.min_abs_score", "kind": "float", "low": 0.05, "high": 0.30, "default": 0.12},
                    {
                        "path": "regime_gates.entry[0].min",
                        "kind": "float",
                        "low": 0.000001,
                        "high": 0.00005,
                        "default": 0.00001,
                    },
                ],
            }

            seeds = runner._warm_start_params(
                session=session,
                family="perp_multi_asset_carry",
                spec_payload=current_payload,
                optuna_space=optuna_space,
            )

            self.assertTrue(
                any(seed.get("regime_gates.entry[0].min") == 0.00002 for seed in seeds)
            )
            self.assertTrue(
                all(seed.get("regime_gates.entry[0].min") != 0.007 for seed in seeds)
            )

    def test_optuna_stability_sweep_penalizes_neighbor_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = self._settings(tmp)
            settings.optuna_trials = 3
            mutator = SpecMutator(settings, claude=SimpleNamespace())
            ancestry = LineageStore(settings.ancestry_db_path)
            session = SimpleNamespace(track="trend_signals", families=["perp_multi_asset_carry"])
            spec_payload = mutator.load_seed_specs("trend_signals", family="perp_multi_asset_carry")[0].canonical_dict()

            class FakeEvaluator:
                async def evaluate(self, spec, fast_mode=False):  # noqa: ANN001, ARG002
                    min_abs_score = float(spec.canonical_dict()["params"]["min_abs_score"])
                    passed = min_abs_score >= 0.12
                    aggregate = 9.7 if passed else 7.0
                    return {
                        "summary": {
                            "aggregate_score": aggregate,
                            "passed": passed,
                            "validation_total_return": 0.03 if passed else -0.01,
                            "pre_audit_canonical_total_return": 0.04 if passed else 0.0,
                            "active_bar_fraction": 0.25 if passed else 0.08,
                        }
                    }

            runner = OptunaOptimizerRunner(
                settings=settings,
                evaluator=FakeEvaluator(),
                mutator=mutator,
                ancestry=ancestry,
            )
            stability = self.async_run(
                runner._stability_sweep(
                    session=session,
                    spec_payload=spec_payload,
                    optuna_space={
                        "family": "perp_multi_asset_carry",
                        "parameters": [
                            {"path": "params.min_abs_score", "kind": "float", "low": 0.05, "high": 0.30, "default": 0.12}
                        ],
                    },
                    best_summary={
                        "aggregate_score": 10.0,
                        "passed": True,
                        "validation_total_return": 0.04,
                        "pre_audit_canonical_total_return": 0.05,
                        "active_bar_fraction": 0.25,
                    },
                )
            )

            self.assertEqual(stability["neighbor_count"], 2)
            self.assertEqual(stability["status"], "fragile")
            self.assertAlmostEqual(stability["passed_fraction"], 0.5)
            self.assertGreaterEqual(stability["stability_penalty"], 1.0)

    def test_workspace_search_and_open(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = self._settings(tmp)
            ancestry = LineageStore(settings.ancestry_db_path)
            mutator = SpecMutator(settings, claude=SimpleNamespace())
            builder = WorkspaceBuilder(
                settings=settings,
                ancestry=ancestry,
                mutator=mutator,
            )
            session = builder.initialize_session(
                track="trend_signals",
                run_session_id="session-2",
                family_scope=None,
            )
            probe_ref = builder.record_probe(
                session=session,
                iteration_number=1,
                probe_type="probe_feature_forward_stats",
                family="perp_multi_asset_carry",
                universe=["BTC", "ETH", "SOL", "HYPE"],
                bundle_id="bundle-1",
                arguments={"feature": "relative_carry_z_72h"},
                result={"ok": True, "signal": "carry", "median_spearman": 0.053},
                tracking_tags=["carry", "probe"],
            )

            search_result = search_workspace(
                workspace_root=session.root,
                query="relative carry",
                kind="probe",
                family="perp_multi_asset_carry",
                limit=5,
            )
            self.assertTrue(search_result["ok"])
            self.assertGreaterEqual(len(search_result["matches"]), 1)
            self.assertEqual(search_result["matches"][0]["path"], probe_ref)

            text_search = search_workspace_text(
                workspace_root=session.root,
                query="median_spearman",
                path_glob="cards/probes/*.md",
                limit=5,
            )
            self.assertTrue(text_search["ok"])
            self.assertGreaterEqual(len(text_search["matches"]), 1)
            self.assertEqual(text_search["matches"][0]["path"], probe_ref)
            self.assertIn("median_spearman", text_search["matches"][0]["snippets"][0]["snippet"])

            open_result = open_workspace_file(
                workspace_root=session.root,
                path=probe_ref,
                section="Result",
                max_chars=1000,
            )
            self.assertTrue(open_result["ok"])
            self.assertIn("carry", open_result["content"])

            feature_search = search_features(
                workspace_root=session.root,
                query="relative carry dispersion",
                family="perp_multi_asset_carry",
                limit=5,
            )
            self.assertTrue(feature_search["ok"])
            self.assertTrue(any(match["name"] == "relative_carry_z_72h" for match in feature_search["matches"]))

            inspected = inspect_feature(
                workspace_root=session.root,
                name="relative_carry_z_72h",
                family="perp_multi_asset_carry",
            )
            self.assertTrue(inspected["ok"])
            self.assertEqual(inspected["feature"]["formula"], "div(relative_carry_72h,clip(funding_dispersion_72h,0.000001,1.0))")

            suggested = suggest_feature_set(
                workspace_root=session.root,
                family="perp_multi_asset_carry",
                hypothesis="carry edge needs one orthogonal co movement or funding dispersion regime discriminator",
                avoid=["funding_72h_mean"],
                limit=4,
            )
            self.assertTrue(suggested["ok"])
            self.assertGreaterEqual(len(suggested["suggestions"]), 1)
            self.assertNotIn("funding_72h_mean", [item["name"] for item in suggested["suggestions"]])

    def test_planner_embedded_yaml_overrides_generic_frontmatter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = self._settings(tmp)
            settings.claude_timeout_s = 30.0
            settings.claude_max_tool_rounds = 5
            ancestry = LineageStore(settings.ancestry_db_path)

            class FakeClaude:
                def __init__(self) -> None:
                    self.last_trace = {"ok": True}
                    self.last_exchange = {"ok": True}

                async def complete_text_with_tools(self, **_kwargs: object) -> str:
                    return """---
decision: refine_current_family
search_mode: branch_same_family
target_family: perp_multi_asset_carry
target_universe: [BTC, ETH, SOL, HYPE]
core_hypothesis: generic top-level note
informative_test: generic top-level test
expected_success: [better validation robustness]
expected_failure: [no measurable change]
evidence_paths: []
tools_used: []
tracking_tags: [perp_multi_asset_carry]
must_answer: Does one concrete regime discriminator improve pre-audit return without making validation negative for `perp_multi_asset_carry`?
required_feature_roles: [one core_carry feature, one orthogonal_regime feature]
forbidden_motifs: [second pure trend overlay]
gate_intent: {}
writer_inputs: [manifests/family/perp_multi_asset_carry.md]
---

```yaml
---
target_family: perp_multi_asset_carry
must_answer: Does adding a market_volatility_168h gate improve pre-audit return above 0.336 while keeping validation positive for `perp_multi_asset_carry`?
required_features:
  - funding_carry_to_vol
  - market_volatility_168h
required_gate_dimensions:
  - market_volatility_168h
forbidden_motifs:
  - perp_multi_asset_carry|unspecified|core_carry+funding+orthogonal_regime|funding_dispersion_72h
---
```

## Diagnosis
Use the embedded spec, not the generic one.
"""

            class FakeHypothesisSandbox:
                def claude_tools(self, **_kwargs: object) -> list[object]:
                    return []

            claude = FakeClaude()
            mutator = SpecMutator(settings, claude=claude)  # type: ignore[arg-type]
            builder = WorkspaceBuilder(
                settings=settings,
                ancestry=ancestry,
                mutator=mutator,
            )
            session = builder.initialize_session(
                track="trend_signals",
                run_session_id="session-planner-merge",
                family_scope=None,
            )
            parent = mutator.load_seed_specs("trend_signals")[0]
            iteration_paths = builder.update_iteration(
                session=session,
                parent=parent,
                iteration_number=1,
                phase_label="main",
                force_novelty=False,
                market_summary={
                    "market_bundle": {"bundle_id": "bundle-1", "symbols": ["BTC", "ETH", "SOL", "HYPE"]},
                    "perp_snapshot": [],
                },
            )
            runner = ResearchPlannerRunner(
                settings=settings,
                claude=claude,  # type: ignore[arg-type]
                hypothesis_sandbox=FakeHypothesisSandbox(),  # type: ignore[arg-type]
                web_researcher=SimpleNamespace(is_configured=False),
                workspace_builder=builder,
            )
            result = self.async_run(
                runner.run(
                    session=session,
                    iteration_number=1,
                    parent=parent,
                    market_bundle={"bundle_id": "bundle-1"},
                    iteration_paths=iteration_paths,
                )
            )
            self.assertEqual(
                result.frontmatter["required_features"],
                ["funding_carry_to_vol", "market_volatility_168h"],
            )
            self.assertEqual(
                result.frontmatter["required_gate_dimensions"],
                ["market_volatility_168h"],
            )
            self.assertIn("0.336", result.frontmatter["must_answer"])

    def test_planner_contract_keeps_only_explicit_binding_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = self._settings(tmp)
            settings.claude_timeout_s = 30.0
            settings.claude_max_tool_rounds = 5
            ancestry = LineageStore(settings.ancestry_db_path)

            class FakeClaude:
                def __init__(self) -> None:
                    self.last_trace = {"ok": True}
                    self.last_exchange = {"ok": True}

                async def complete_text_with_tools(self, **_kwargs: object) -> str:
                    return """## Diagnosis
Carry works on BTC/ETH/SOL/HYPE, but the recent carry_term_structure stack did not.

## Proposed next experiment
Stay in `perp_multi_asset_carry` and test a funding dispersion gate on the winning universe.

## Suggested Gate Spec
```yaml
family: perp_multi_asset_carry
regime_gates:
  entry:
    - expression: funding_dispersion_72h
      min: 0.000005
```

## Must Answer
Does a funding_dispersion_72h gate improve pre-audit return without making validation negative?
"""

            class FakeHypothesisSandbox:
                def claude_tools(self, **_kwargs: object) -> list[object]:
                    return []

            claude = FakeClaude()
            mutator = SpecMutator(settings, claude=claude)  # type: ignore[arg-type]
            builder = WorkspaceBuilder(
                settings=settings,
                ancestry=ancestry,
                mutator=mutator,
            )
            session = builder.initialize_session(
                track="trend_signals",
                run_session_id="session-planner-explicit",
                family_scope=None,
            )
            parent = mutator.load_seed_specs("trend_signals")[0]
            iteration_paths = builder.update_iteration(
                session=session,
                parent=parent,
                iteration_number=1,
                phase_label="main",
                force_novelty=False,
                market_summary={
                    "market_bundle": {"bundle_id": "bundle-1", "symbols": ["BTC", "ETH", "SOL", "HYPE"]},
                    "perp_snapshot": [],
                },
            )
            runner = ResearchPlannerRunner(
                settings=settings,
                claude=claude,  # type: ignore[arg-type]
                hypothesis_sandbox=FakeHypothesisSandbox(),  # type: ignore[arg-type]
                web_researcher=SimpleNamespace(is_configured=False),
                workspace_builder=builder,
            )
            result = self.async_run(
                runner.run(
                    session=session,
                    iteration_number=1,
                    parent=parent,
                    market_bundle={"bundle_id": "bundle-1"},
                    iteration_paths=iteration_paths,
                )
            )
            self.assertEqual(result.frontmatter["target_family"], "perp_multi_asset_carry")
            self.assertEqual(result.frontmatter["required_feature_roles"], [])
            self.assertEqual(result.frontmatter["required_features"], [])
            self.assertEqual(result.frontmatter["required_gate_dimensions"], ["funding_dispersion_72h"])
            self.assertEqual(
                result.frontmatter["planner_regime_gates"]["entry"][0]["min"],
                0.000005,
            )

    def test_planner_contract_drops_trade_style_for_cross_sectional_families(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = self._settings(tmp)
            settings.claude_timeout_s = 30.0
            settings.claude_max_tool_rounds = 5
            ancestry = LineageStore(settings.ancestry_db_path)

            class FakeClaude:
                def __init__(self) -> None:
                    self.last_trace = {"ok": True}
                    self.last_exchange = {"ok": True}

                async def complete_text_with_tools(self, **_kwargs: object) -> str:
                    return """## Diagnosis
Stay in carry but tilt toward a momentum-flavored ranking.

## Proposed next experiment
Stay in `perp_multi_asset_carry`.
trade_style: continuation
"""

            class FakeHypothesisSandbox:
                def claude_tools(self, **_kwargs: object) -> list[object]:
                    return []

            claude = FakeClaude()
            mutator = SpecMutator(settings, claude=claude)  # type: ignore[arg-type]
            builder = WorkspaceBuilder(
                settings=settings,
                ancestry=ancestry,
                mutator=mutator,
            )
            session = builder.initialize_session(
                track="trend_signals",
                run_session_id="session-planner-cross-style",
                family_scope=None,
            )
            parent = mutator.load_seed_specs("trend_signals")[0]
            iteration_paths = builder.update_iteration(
                session=session,
                parent=parent,
                iteration_number=1,
                phase_label="main",
                force_novelty=False,
                market_summary={
                    "market_bundle": {"bundle_id": "bundle-1", "symbols": ["BTC", "ETH", "SOL", "HYPE"]},
                    "perp_snapshot": [],
                },
            )
            runner = ResearchPlannerRunner(
                settings=settings,
                claude=claude,  # type: ignore[arg-type]
                hypothesis_sandbox=FakeHypothesisSandbox(),  # type: ignore[arg-type]
                web_researcher=SimpleNamespace(is_configured=False),
                workspace_builder=builder,
            )
            result = self.async_run(
                runner.run(
                    session=session,
                    iteration_number=1,
                    parent=parent,
                    market_bundle={"bundle_id": "bundle-1"},
                    iteration_paths=iteration_paths,
                )
            )
            self.assertEqual(result.frontmatter["target_family"], "perp_multi_asset_carry")
            self.assertIsNone(result.frontmatter["target_trade_style"])

    def test_writer_runner_retries_with_validator_feedback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = self._settings(tmp)
            ancestry = LineageStore(settings.ancestry_db_path)

            class FakeClaude:
                def __init__(self) -> None:
                    self.calls: list[list[dict[str, str]]] = []
                    self.last_trace = {"ok": True}
                    self.last_exchange = {"ok": True}

                async def complete_json_messages(self, **kwargs: object) -> dict[str, object]:
                    messages = list(kwargs["messages"])  # type: ignore[index]
                    self.calls.append(messages)
                    if len(self.calls) == 1:
                        return {
                            "track": "trend_signals",
                            "family": "perp_multi_asset_carry",
                            "hypothesis": "bad first pass",
                            "neutrality_basis": "none",
                            "features": ["not_a_real_feature"],
                            "universe": {"basis_groups": ["BTC"], "max_symbols": 1},
                            "risk": {"max_leverage": 9.0},
                            "regime_gates": {"entry": []},
                            "params": {"long_count": 0, "short_count": 0},
                            "unsupported_key": "extra",
                        }
                    return {
                        "track": "trend_signals",
                        "family": "perp_multi_asset_carry",
                        "hypothesis": "fixed second pass",
                        "neutrality_basis": "none",
                        "features": ["relative_carry_z_72h", "carry_term_structure_24_168"],
                        "universe": {
                            "basis_groups": ["BTC", "ETH", "SOL", "HYPE"],
                            "max_symbols": 4,
                            "lookback_days": 365,
                            "interval": "1h",
                        },
                        "risk": {"max_asset_weight": 0.35, "rebalance_threshold": 0.03, "max_leverage": 1.0},
                        "regime_gates": {"entry": [], "exit_on_break": True},
                        "params": {"long_count": 2, "short_count": 2, "gross_target": 1.0},
                    }

            claude = FakeClaude()
            mutator = SpecMutator(settings, claude=claude)  # type: ignore[arg-type]
            builder = WorkspaceBuilder(
                settings=settings,
                ancestry=ancestry,
                mutator=mutator,
            )
            session = builder.initialize_session(
                track="trend_signals",
                run_session_id="session-3",
                family_scope=None,
            )
            parent = mutator.load_seed_specs("trend_signals")[0]
            iteration_paths = builder.update_iteration(
                session=session,
                parent=parent,
                iteration_number=1,
                phase_label="main",
                force_novelty=False,
                market_summary={
                    "market_bundle": {"bundle_id": "bundle-1", "symbols": ["BTC", "ETH", "SOL", "HYPE"]},
                    "perp_snapshot": [],
                },
            )
            research_note_path = iteration_paths["research_note_path"]
            research_note_path.write_text(
                dump_frontmatter(
                    {
                        "decision": "test_carry",
                        "search_mode": "refine",
                        "target_family": "perp_multi_asset_carry",
                        "target_universe": ["BTC", "ETH", "SOL", "HYPE"],
                        "core_hypothesis": "Carry remains best.",
                        "informative_test": "Use relative carry plus term structure.",
                        "expected_success": ["better carry robustness"],
                        "expected_failure": ["no change"],
                        "evidence_paths": [],
                        "tools_used": [],
                        "tracking_tags": ["carry"],
                        "must_answer": "Return one clean carry spec.",
                        "required_feature_roles": ["one core_carry feature"],
                        "forbidden_motifs": [],
                        "gate_intent": {},
                        "writer_inputs": ["manifests/family/perp_multi_asset_carry.md"],
                    },
                    "## Proposed next experiment\nUse a clean carry spec.",
                )
            )

            runner = SpecWriterRunner(
                settings=SimpleNamespace(
                    root_dir=self.repo_root,
                    claude_timeout_s=30.0,
                ),
                claude=claude,  # type: ignore[arg-type]
                mutator=mutator,
            )
            result = self.async_run(
                runner.run(
                    session=session,
                    research_note_path=research_note_path,
                    iteration_paths=iteration_paths,
                    parent=parent,
                )
            )
            self.assertEqual(result['spec_payload']["family"], "perp_multi_asset_carry")
            self.assertIn("relative_carry_z_72h", result['spec_payload']["features"])
            self.assertIn("carry_term_structure_24_168", result['spec_payload']["features"])
            self.assertGreaterEqual(len(claude.calls), 2)
            repair_messages = [
                messages[-1]["content"]
                for messages in claude.calls[1:]
                if messages and messages[-1]["role"] == "user"
            ]
            self.assertTrue(any("Repair packet" in content for content in repair_messages))
            self.assertTrue(any("unsupported top-level keys" in content for content in repair_messages))
            trace = json.loads(Path(result['trace_path']).read_text())
            self.assertIn("inputs", trace)
            self.assertIn("outputs", trace)
            self.assertIn("conversation_messages", trace)
            self.assertEqual(trace["outputs"]["spec_payload"]["family"], "perp_multi_asset_carry")
            self.assertIn("initial_user_prompt", trace["inputs"])
            self.assertIn("Regime gate contract", trace["inputs"]["initial_user_prompt"])
            self.assertIn("Family Contract", trace["inputs"]["initial_user_prompt"])
            self.assertTrue(Path(result['base_spec_path']).exists())
            self.assertEqual(result['structure_spec']["continuous_tuning_owner"], "optuna")
            self.assertTrue((iteration_paths["structure_spec_path"]).exists())
            self.assertTrue((iteration_paths["spec_patch_path"]).exists())
            self.assertTrue((iteration_paths["spec_after_patch_path"]).exists())
            patch_payload = json.loads(iteration_paths["spec_patch_path"].read_text())
            self.assertGreaterEqual(patch_payload["change_count"], 1)
            self.assertIn("repair_packet_path", trace["outputs"])
            self.assertIsNone(trace["outputs"]["repair_packet_path"])
            self.assertIsNone(trace["outputs"]["latest_repair_packet"])
            self.assertFalse(iteration_paths["repair_packet_path"].exists())
            self.assertTrue(any(msg["role"] == "user" for msg in trace["conversation_messages"]))

    def test_bai_writer_uses_third_retry_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = self._settings(tmp)
            ancestry = LineageStore(settings.ancestry_db_path)

            class FakeClaude:
                def __init__(self) -> None:
                    self.calls: list[list[dict[str, str]]] = []
                    self.last_trace = {"provider": "bai", "model": "deepseek-v4-flash"}
                    self.last_exchange = {"ok": True}

                async def complete_json_messages(self, **kwargs: object) -> dict[str, object]:
                    self.calls.append(list(kwargs["messages"]))  # type: ignore[index]
                    if len(self.calls) < 3:
                        return {
                            "track": "trend_signals",
                            "family": "perp_multi_asset_decision",
                            "hypothesis": "bad retry",
                            "neutrality_basis": "none",
                            "features": ["not_a_real_feature"],
                            "universe": {"basis_groups": ["BTC"], "max_symbols": 1},
                            "risk": {"max_leverage": 9.0},
                            "regime_gates": {"entry": []},
                            "params": {"long_count": 0, "short_count": 0},
                            "unsupported_key": "extra",
                        }
                    return {
                        "track": "trend_signals",
                        "family": "perp_multi_asset_decision",
                        "hypothesis": "fixed third pass",
                        "neutrality_basis": "none",
                        "features": [
                            "donchian_position_20",
                            "ema_gap_12_26",
                            "macd_hist_12_26_9",
                            "price_return_72h",
                            "realized_vol_168h",
                            "rsi_centered_14",
                        ],
                        "universe": {
                            "basis_groups": ["BTC", "ETH", "SOL", "HYPE"],
                            "max_symbols": 4,
                            "lookback_days": 365,
                            "interval": "1h",
                        },
                        "risk": {"max_asset_weight": 0.3, "rebalance_threshold": 0.03, "max_leverage": 2.0},
                        "regime_gates": {"entry": [], "exit_on_break": False},
                        "params": {"long_count": 4, "short_count": 2, "gross_target": 1.0},
                    }

            claude = FakeClaude()
            mutator = SpecMutator(settings, claude=claude)  # type: ignore[arg-type]
            builder = WorkspaceBuilder(settings=settings, ancestry=ancestry, mutator=mutator)
            session = builder.initialize_session(
                track="trend_signals",
                run_session_id="session-bai-third-retry",
                family_scope=None,
            )
            parent = mutator.load_seed_specs("trend_signals")[0]
            iteration_paths = builder.update_iteration(
                session=session,
                parent=parent,
                iteration_number=1,
                phase_label="main",
                force_novelty=False,
                market_summary={
                    "market_bundle": {"bundle_id": "bundle-1", "symbols": ["BTC", "ETH", "SOL", "HYPE"]},
                    "perp_snapshot": [],
                },
            )
            research_note_path = iteration_paths["research_note_path"]
            research_note_path.write_text(
                dump_frontmatter(
                    {
                        "target_family": "perp_multi_asset_decision",
                        "target_universe": ["BTC", "ETH", "SOL", "HYPE"],
                        "core_hypothesis": "Decision momentum remains best.",
                        "informative_test": "Keep the proven decision feature set active.",
                        "expected_success": ["better carry robustness"],
                        "expected_failure": ["no change"],
                        "must_answer": "Return one clean decision spec.",
                        "required_feature_roles": [],
                        "writer_inputs": ["manifests/family/perp_multi_asset_decision.md"],
                    },
                    "## Proposed next experiment\nUse a clean decision spec.",
                )
            )
            runner = SpecWriterRunner(
                settings=SimpleNamespace(
                    root_dir=self.repo_root,
                    claude_timeout_s=30.0,
                    llm_provider="bai",
                ),
                claude=claude,  # type: ignore[arg-type]
                mutator=mutator,
            )

            result = self.async_run(
                runner.run(
                    session=session,
                    research_note_path=research_note_path,
                    iteration_paths=iteration_paths,
                    parent=parent,
                )
            )

            self.assertEqual(len(claude.calls), 3)
            trace = json.loads(Path(result['trace_path']).read_text())
            self.assertEqual(trace["attempt_count"], 3)
            self.assertEqual(trace["attempts"][2]["payload"]["hypothesis"], "fixed third pass")

    def test_writer_runner_repairs_planner_writer_family_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = self._settings(tmp)
            ancestry = LineageStore(settings.ancestry_db_path)

            class FakeClaude:
                def __init__(self) -> None:
                    self.calls: list[list[dict[str, str]]] = []
                    self.last_trace = {"ok": True}
                    self.last_exchange = {"ok": True}

                async def complete_json_messages(self, **kwargs: object) -> dict[str, object]:
                    messages = list(kwargs["messages"])  # type: ignore[index]
                    self.calls.append(messages)
                    if len(self.calls) == 1:
                        return {
                            "track": "trend_signals",
                            "family": "perp_basket_neutral_levered",
                            "hypothesis": "wrong family first pass",
                            "neutrality_basis": "dollar_neutral",
                            "features": ["relative_carry_z_72h", "funding_dispersion_72h"],
                            "universe": {
                                "basis_groups": ["BTC", "ETH", "SOL", "HYPE"],
                                "max_symbols": 4,
                                "lookback_days": 365,
                                "interval": "1h",
                            },
                            "risk": {"max_asset_weight": 0.35, "rebalance_threshold": 0.03, "max_leverage": 1.0},
                            "regime_gates": {"entry": [], "exit_on_break": True},
                            "params": {"long_count": 2, "short_count": 2, "gross_target": 1.0},
                        }
                    return {
                        "track": "trend_signals",
                        "family": "perp_multi_asset_carry",
                        "hypothesis": "carry with one orthogonal regime feature",
                        "neutrality_basis": "none",
                        "features": ["relative_carry_z_72h", "funding_dispersion_72h"],
                        "universe": {
                            "basis_groups": ["BTC", "ETH", "SOL", "HYPE"],
                            "max_symbols": 4,
                            "lookback_days": 365,
                            "interval": "1h",
                        },
                        "risk": {"max_asset_weight": 0.35, "rebalance_threshold": 0.03, "max_leverage": 1.0},
                        "regime_gates": {"entry": [], "exit_on_break": True},
                        "params": {"long_count": 2, "short_count": 2, "gross_target": 1.0},
                    }

            claude = FakeClaude()
            mutator = SpecMutator(settings, claude=claude)  # type: ignore[arg-type]
            builder = WorkspaceBuilder(
                settings=settings,
                ancestry=ancestry,
                mutator=mutator,
            )
            session = builder.initialize_session(
                track="trend_signals",
                run_session_id="session-3b",
                family_scope=None,
            )
            parent = mutator.load_seed_specs("trend_signals")[0]
            iteration_paths = builder.update_iteration(
                session=session,
                parent=parent,
                iteration_number=2,
                phase_label="main",
                force_novelty=False,
                market_summary={
                    "market_bundle": {"bundle_id": "bundle-1", "symbols": ["BTC", "ETH", "SOL", "HYPE"]},
                    "perp_snapshot": [],
                },
            )
            research_note_path = iteration_paths["research_note_path"]
            research_note_path.write_text(
                dump_frontmatter(
                    {
                        "decision": "return_to_carry",
                        "search_mode": "branch_same_family",
                        "target_family": "perp_multi_asset_carry",
                        "target_trade_style": None,
                        "target_universe": ["BTC", "ETH", "SOL", "HYPE"],
                        "core_hypothesis": "Carry remains the best family.",
                        "informative_test": "Use one carry core plus one orthogonal regime discriminator.",
                        "expected_success": ["stay in carry while adding regime fit"],
                        "expected_failure": ["no improvement"],
                        "evidence_paths": [],
                        "tools_used": [],
                        "tracking_tags": ["carry"],
                        "must_answer": "Return to perp_multi_asset_carry and test one orthogonal regime feature.",
                        "required_feature_roles": [
                            "one core_carry feature",
                            "one orthogonal_regime feature",
                        ],
                        "forbidden_motifs": ["second pure trend overlay"],
                        "gate_intent": {},
                        "writer_inputs": [
                            "manifests/family/perp_multi_asset_carry.md",
                            "manifests/family/perp_multi_asset_carry.json",
                        ],
                    },
                    "## Proposed next experiment\nReturn to carry.",
                )
            )

            runner = SpecWriterRunner(
                settings=SimpleNamespace(
                    root_dir=self.repo_root,
                    claude_timeout_s=30.0,
                ),
                claude=claude,  # type: ignore[arg-type]
                mutator=mutator,
            )
            result = self.async_run(
                runner.run(
                    session=session,
                    research_note_path=research_note_path,
                    iteration_paths=iteration_paths,
                    parent=parent,
                )
            )
            self.assertEqual(result['spec_payload']["family"], "perp_multi_asset_carry")
            trace = json.loads(Path(result['trace_path']).read_text())
            first_attempt = trace["attempts"][0]
            self.assertTrue(first_attempt["conformance_issues"])
            self.assertIn("family mismatch", first_attempt["conformance_issues"][0])
            self.assertEqual(trace["outputs"]["spec_payload"]["family"], "perp_multi_asset_carry")

    def test_planner_runner_keeps_body_when_frontmatter_is_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = SimpleNamespace(
                root_dir=self.repo_root,
                artifact_dir=Path(tmp) / "runs",
                ancestry_db_path=Path(tmp) / "ancestry.db",
                claude_timeout_s=30.0,
                claude_max_tool_rounds=25,
            )
            ancestry = LineageStore(settings.ancestry_db_path)

            class FakeClaude:
                def __init__(self) -> None:
                    self.last_trace = {"ok": True, "tool_rounds_used": 0}
                    self.last_exchange = {"ok": True}

                async def complete_text_with_tools(self, **_kwargs: object) -> str:
                    return (
                        "---\n"
                        "family: perp_multi_asset_carry\n"
                        "open_question: Test whether co-movement is the missing state variable.\n"
                        "---\n\n"
                        "## Diagnosis\n"
                        "The previous carry winner is still the best anchor, but the next test should add one orthogonal state variable.\n\n"
                        "## Proposed next experiment\n"
                        "Keep the family and test co_movement_72h as a regime discriminator.\n"
                    )

            class FakeHypothesisSandbox:
                def claude_tools(self, **_kwargs: object) -> list[object]:
                    return []

            builder = WorkspaceBuilder(
                settings=settings,
                ancestry=ancestry,
                mutator=SpecMutator(settings, claude=SimpleNamespace()),
            )
            session = builder.initialize_session(
                track="trend_signals",
                run_session_id="session-4",
                family_scope=None,
            )
            parent = SpecMutator(settings, claude=SimpleNamespace()).load_seed_specs("trend_signals")[0]
            iteration_paths = builder.update_iteration(
                session=session,
                parent=parent,
                iteration_number=4,
                phase_label="main",
                force_novelty=False,
                market_summary={
                    "market_bundle": {"bundle_id": "bundle-1", "symbols": ["BTC", "ETH", "SOL", "HYPE"]},
                    "perp_snapshot": [],
                },
            )

            runner = ResearchPlannerRunner(
                settings=settings,
                claude=FakeClaude(),  # type: ignore[arg-type]
                hypothesis_sandbox=FakeHypothesisSandbox(),  # type: ignore[arg-type]
                web_researcher=SimpleNamespace(is_configured=False),
                workspace_builder=builder,
            )
            result = self.async_run(
                runner.run(
                    session=session,
                    iteration_number=4,
                    parent=parent,
                    market_bundle={"bundle_id": "bundle-1"},
                    iteration_paths=iteration_paths,
                )
            )

            saved_frontmatter, saved_body = parse_frontmatter(result.research_note_path.read_text())
            self.assertEqual(saved_frontmatter["family"], "perp_multi_asset_carry")
            self.assertIn("co_movement_72h", saved_body)
            self.assertNotIn("The planner response was invalid", saved_body)
            planner_contract = json.loads(result.planner_contract_path.read_text())
            self.assertEqual(planner_contract["target_family"], "perp_multi_asset_carry")
            self.assertTrue(planner_contract["must_answer"].startswith("Does "))
            self.assertIn("validation", planner_contract["must_answer"])
            trace = json.loads(result.trace_path.read_text())
            self.assertEqual(trace["outputs"]["planner_contract"]["target_family"], "perp_multi_asset_carry")
            self.assertEqual(trace["outputs"]["raw_frontmatter"]["family"], "perp_multi_asset_carry")
            self.assertIn("co_movement_72h", trace["outputs"]["raw_research_note"])
            self.assertIn("current/incumbent_spec.yaml", trace["inputs"]["default_context_files"])
            self.assertIn("current/family_incumbents.json", trace["inputs"]["default_context_files"])
            self.assertIn("current/recent_trials.md", trace["inputs"]["default_context_files"])
            self.assertIn("manifests/regime_catalog.md", trace["inputs"]["default_context_files"])
            self.assertIn("manifests/policy_surface.md", trace["inputs"]["default_context_files"])
            self.assertIn("manifests/features/feature_surface.md", trace["inputs"]["default_context_files"])

    def test_conformance_ignores_must_answer_branch_text_and_prose_feature_mentions(self) -> None:
        planner_contract = {
            "target_family": "perp_multi_asset_decision",
            "must_answer": "Should the search return to `perp_multi_asset_carry` or keep testing decision?",
            "core_hypothesis": "Co-movement should matter if the gate is causal.",
            "informative_test": "Test co_movement_72h directly.",
            "required_features": [],
        }
        spec_payload = {
            "family": "perp_multi_asset_decision",
            "features": ["price_return_72h", "funding_72h_mean"],
            "regime_gates": {},
            "params": {},
            "universe": {"basis_groups": ["BTC", "ETH", "SOL", "HYPE"]},
        }
        violations = conformance_violations(
            planner_contract=planner_contract,
            spec_payload=spec_payload,
            allowed_features=["co_movement_72h", "price_return_72h", "funding_72h_mean"],
            parent_payload=None,
        )
        self.assertEqual(violations, [])

    def test_conformance_ignores_trade_style_for_cross_sectional_families(self) -> None:
        planner_contract = {
            "target_family": "perp_multi_asset_carry",
            "target_trade_style": "continuation",
            "required_features": [],
        }
        spec_payload = {
            "family": "perp_multi_asset_carry",
            "features": ["relative_carry_z_72h", "funding_dispersion_72h"],
            "regime_gates": {},
            "params": {},
            "universe": {"basis_groups": ["BTC", "ETH", "SOL", "HYPE"]},
        }
        violations = conformance_violations(
            planner_contract=planner_contract,
            spec_payload=spec_payload,
            allowed_features=["relative_carry_z_72h", "funding_dispersion_72h"],
            parent_payload=None,
        )
        self.assertEqual(violations, [])

    def test_planner_runner_retries_parse_failures_with_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = SimpleNamespace(
                root_dir=self.repo_root,
                artifact_dir=Path(tmp) / "runs",
                ancestry_db_path=Path(tmp) / "ancestry.db",
                claude_timeout_s=30.0,
                claude_max_tool_rounds=25,
            )
            ancestry = LineageStore(settings.ancestry_db_path)

            class FakeClaude:
                def __init__(self) -> None:
                    self.calls: list[dict[str, object]] = []
                    self.last_trace = {"ok": True, "tool_rounds_used": 0}
                    self.last_exchange = {"ok": True}
                    self._responses = [
                        (
                            "---\n"
                            "family: perp_multi_asset_carry\n"
                            "must_answer: Does co_movement_72h help: yes or no?\n"
                            "target_family: perp_multi_asset_carry\n"
                            "---\n\n"
                            "## Diagnosis\n"
                            "Broken YAML on purpose.\n"
                        ),
                        (
                            "---\n"
                            "family: perp_multi_asset_carry\n"
                            "open_question: Test whether co_movement_72h improves validation.\n"
                            "---\n\n"
                            "## Diagnosis\n"
                            "The previous carry winner is still the best anchor.\n\n"
                            "## Proposed next experiment\n"
                            "Keep the family and test co_movement_72h as a regime discriminator.\n"
                        ),
                    ]

                async def complete_text_with_tools(self, **kwargs: object) -> str:
                    self.calls.append(
                        {
                            "user_prompt": str(kwargs["user_prompt"]),
                            "tool_count": len(list(kwargs["tools"])),  # type: ignore[arg-type]
                        }
                    )
                    return self._responses[len(self.calls) - 1]

            class FakeHypothesisSandbox:
                def claude_tools(self, **_kwargs: object) -> list[object]:
                    return []

            claude = FakeClaude()
            builder = WorkspaceBuilder(
                settings=settings,
                ancestry=ancestry,
                mutator=SpecMutator(settings, claude=SimpleNamespace()),
            )
            session = builder.initialize_session(
                track="trend_signals",
                run_session_id="session-4b",
                family_scope=None,
            )
            parent = SpecMutator(settings, claude=SimpleNamespace()).load_seed_specs("trend_signals")[0]
            iteration_paths = builder.update_iteration(
                session=session,
                parent=parent,
                iteration_number=4,
                phase_label="main",
                force_novelty=False,
                market_summary={
                    "market_bundle": {"bundle_id": "bundle-1", "symbols": ["BTC", "ETH", "SOL", "HYPE"]},
                    "perp_snapshot": [],
                },
            )

            runner = ResearchPlannerRunner(
                settings=settings,
                claude=claude,  # type: ignore[arg-type]
                hypothesis_sandbox=FakeHypothesisSandbox(),  # type: ignore[arg-type]
                web_researcher=SimpleNamespace(is_configured=False),
                workspace_builder=builder,
            )
            result = self.async_run(
                runner.run(
                    session=session,
                    iteration_number=4,
                    parent=parent,
                    market_bundle={"bundle_id": "bundle-1"},
                    iteration_paths=iteration_paths,
                )
            )

            self.assertTrue(result.repaired)
            self.assertEqual(len(claude.calls), 2)
            self.assertTrue(all(int(call["tool_count"]) > 0 for call in claude.calls))
            self.assertIn("Failure Packet", str(claude.calls[1]["user_prompt"]))
            saved_frontmatter, saved_body = parse_frontmatter(result.research_note_path.read_text())
            self.assertEqual(saved_frontmatter["family"], "perp_multi_asset_carry")
            self.assertIn("co_movement_72h", saved_body)
            planner_contract = json.loads(result.planner_contract_path.read_text())
            self.assertEqual(planner_contract["target_family"], "perp_multi_asset_carry")
            trace = json.loads(result.trace_path.read_text())
            self.assertEqual(len(trace["planner_attempts"]), 2)
            self.assertFalse(trace["planner_attempts"][0]["success"])
            self.assertEqual(
                trace["planner_attempts"][0]["error"]["error_type"],
                "planner_note_semantic_failure",
            )
            self.assertTrue(trace["planner_attempts"][1]["success"])

    def test_live_provider_planner_refuses_fallback_after_repair_exhaustion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = SimpleNamespace(
                root_dir=self.repo_root,
                artifact_dir=Path(tmp) / "runs",
                ancestry_db_path=Path(tmp) / "ancestry.db",
                claude_timeout_s=30.0,
                claude_max_tool_rounds=25,
                llm_provider="bai",
            )
            ancestry = LineageStore(settings.ancestry_db_path)

            class FakeClaude:
                def __init__(self) -> None:
                    self.last_trace = {"provider": "bai", "model": "deepseek-v4-flash", "tool_rounds_used": 1, "tool_calls": [{"name": "think"}]}
                    self.last_exchange = {"ok": True}

                async def complete_text_with_tools(self, **_kwargs: object) -> str:
                    return "too short"

            class FakeHypothesisSandbox:
                def claude_tools(self, **_kwargs: object) -> list[object]:
                    return []

            builder = WorkspaceBuilder(
                settings=settings,
                ancestry=ancestry,
                mutator=SpecMutator(settings, claude=SimpleNamespace()),
            )
            session = builder.initialize_session(
                track="trend_signals",
                run_session_id="session-planner-fallback-refusal",
                family_scope=None,
            )
            parent = SpecMutator(settings, claude=SimpleNamespace()).load_seed_specs("trend_signals")[0]
            iteration_paths = builder.update_iteration(
                session=session,
                parent=parent,
                iteration_number=1,
                phase_label="main",
                force_novelty=False,
                market_summary={
                    "market_bundle": {"bundle_id": "bundle-1", "symbols": ["BTC", "ETH", "SOL", "HYPE"]},
                    "perp_snapshot": [],
                },
            )
            runner = ResearchPlannerRunner(
                settings=settings,
                claude=FakeClaude(),  # type: ignore[arg-type]
                hypothesis_sandbox=FakeHypothesisSandbox(),  # type: ignore[arg-type]
                web_researcher=SimpleNamespace(is_configured=False),
                workspace_builder=builder,
            )

            with self.assertRaisesRegex(RuntimeError, "refusing fallback note"):
                self.async_run(
                    runner.run(
                        session=session,
                        iteration_number=1,
                        parent=parent,
                        market_bundle={"bundle_id": "bundle-1"},
                        iteration_paths=iteration_paths,
                    )
                )

            trace = json.loads(iteration_paths["planner_trace_path"].read_text())
            self.assertTrue(trace["outputs"]["used_fallback_note"])
            self.assertEqual(len(trace["planner_attempts"]), ResearchPlannerRunner.MAX_REPAIR_ATTEMPTS)

    def test_writer_runner_preserves_required_named_feature(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = self._settings(tmp)
            ancestry = LineageStore(settings.ancestry_db_path)

            class FakeClaude:
                def __init__(self) -> None:
                    self.calls: list[list[dict[str, str]]] = []
                    self.last_trace = {"ok": True}
                    self.last_exchange = {"ok": True}

                async def complete_json_messages(self, **kwargs: object) -> dict[str, object]:
                    messages = list(kwargs["messages"])  # type: ignore[index]
                    self.calls.append(messages)
                    if len(self.calls) == 1:
                        return {
                            "track": "trend_signals",
                            "family": "perp_multi_asset_carry",
                            "hypothesis": "missing the named feature",
                            "neutrality_basis": "none",
                            "features": ["relative_carry_z_72h", "carry_term_structure_24_168"],
                            "universe": {
                                "basis_groups": ["BTC", "ETH", "SOL", "HYPE"],
                                "max_symbols": 4,
                                "lookback_days": 365,
                                "interval": "1h",
                            },
                            "risk": {"max_asset_weight": 0.35, "rebalance_threshold": 0.03, "max_leverage": 1.0},
                            "regime_gates": {"entry": [], "exit_on_break": True},
                            "params": {"long_count": 2, "short_count": 2, "gross_target": 1.0},
                        }
                    return {
                        "track": "trend_signals",
                        "family": "perp_multi_asset_carry",
                        "hypothesis": "now includes the named feature",
                        "neutrality_basis": "none",
                        "features": ["relative_carry_z_72h", "co_movement_72h"],
                        "universe": {
                            "basis_groups": ["BTC", "ETH", "SOL", "HYPE"],
                            "max_symbols": 4,
                            "lookback_days": 365,
                            "interval": "1h",
                        },
                        "risk": {"max_asset_weight": 0.35, "rebalance_threshold": 0.03, "max_leverage": 1.0},
                        "regime_gates": {
                            "entry": [{"expression": "co_movement_72h", "min": 0.2}],
                            "exit_on_break": True,
                        },
                        "params": {"long_count": 2, "short_count": 2, "gross_target": 1.0},
                    }

            class FakeHypothesisSandbox:
                provider = None

                async def _tool_probe_spec_gate_impact(self, **_kwargs: object) -> dict[str, object]:
                    return {
                        "ok": True,
                        "warnings": [],
                        "gate_coverage": {"configured": True},
                        "selector_train_comparison": {
                            "delta": {
                                "median_total_return": 0.01,
                                "median_sharpe": 0.1,
                            }
                        },
                    }

            claude = FakeClaude()
            mutator = SpecMutator(settings, claude=claude)  # type: ignore[arg-type]
            builder = WorkspaceBuilder(
                settings=settings,
                ancestry=ancestry,
                mutator=mutator,
            )
            session = builder.initialize_session(
                track="trend_signals",
                run_session_id="session-5",
                family_scope=None,
            )
            parent = mutator.load_seed_specs("trend_signals")[0]
            iteration_paths = builder.update_iteration(
                session=session,
                parent=parent,
                iteration_number=5,
                phase_label="main",
                force_novelty=False,
                market_summary={
                    "market_bundle": {"bundle_id": "bundle-1", "symbols": ["BTC", "ETH", "SOL", "HYPE"]},
                    "perp_snapshot": [],
                },
            )
            research_note_path = iteration_paths["research_note_path"]
            research_note_path.write_text(
                dump_frontmatter(
                    {
                        "decision": "test_named_feature",
                        "search_mode": "branch_same_family",
                        "target_family": "perp_multi_asset_carry",
                        "target_universe": ["BTC", "ETH", "SOL", "HYPE"],
                        "core_hypothesis": "Co-movement should matter if the gate is causal.",
                        "informative_test": "Test co_movement_72h directly.",
                        "expected_success": ["better validation robustness"],
                        "expected_failure": ["no measurable change"],
                        "evidence_paths": [],
                        "tools_used": [],
                        "tracking_tags": ["carry"],
                        "must_answer": "Does `co_movement_72h` improve pre-audit return without making validation negative for `perp_multi_asset_carry`?",
                        "required_feature_roles": ["one core_carry feature", "one orthogonal_regime feature"],
                        "required_features": ["co_movement_72h"],
                        "forbidden_motifs": [],
                        "gate_intent": {"type": "suppress_bad_regime", "target_dimension": "co_movement"},
                        "required_gate_dimensions": ["co_movement"],
                        "writer_inputs": ["manifests/family/perp_multi_asset_carry.md"],
                    },
                    "## Proposed next experiment\nUse co_movement_72h directly.",
                )
            )

            runner = SpecWriterRunner(
                settings=SimpleNamespace(
                    root_dir=self.repo_root,
                    claude_timeout_s=30.0,
                ),
                claude=claude,  # type: ignore[arg-type]
                mutator=mutator,
                hypothesis_sandbox=FakeHypothesisSandbox(),  # type: ignore[arg-type]
            )
            result = self.async_run(
                runner.run(
                    session=session,
                    research_note_path=research_note_path,
                    iteration_paths=iteration_paths,
                    parent=parent,
                )
            )
            trace = json.loads(Path(result['trace_path']).read_text())
            self.assertTrue(
                any(
                    "missing required named feature" in issue
                    for issue in trace["attempts"][0]["conformance_issues"]
                )
            )
            self.assertIn("co_movement_72h", result['spec_payload']["features"])

    def test_writer_runner_treats_empty_regime_gates_as_semantic_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = self._settings(tmp)
            ancestry = LineageStore(settings.ancestry_db_path)

            class FakeClaude:
                def __init__(self) -> None:
                    self.last_trace = {"ok": True}
                    self.last_exchange = {"ok": True}

                async def complete_json_messages(self, **_kwargs: object) -> dict[str, object]:
                    return {
                        "track": "trend_signals",
                        "family": "perp_multi_asset_carry",
                        "hypothesis": "Test asymmetric book construction without regime gates.",
                        "neutrality_basis": "none",
                        "features": [
                            "funding_168h_mean",
                            "funding_72h_mean",
                            "funding_accel_24h",
                            "funding_carry_to_vol",
                            "funding_z_168h",
                            "realized_vol_168h",
                        ],
                        "universe": {
                            "basis_groups": ["BTC", "ETH", "SOL", "HYPE"],
                            "max_symbols": 4,
                            "lookback_days": 365,
                            "interval": "1h",
                        },
                        "risk": {
                            "max_asset_weight": 0.35,
                            "rebalance_threshold": 0.03,
                            "max_leverage": 1.0,
                        },
                        "regime_gates": {"entry": [], "exit_on_break": True},
                        "params": {
                            "gross_target": 1.0,
                            "long_count": 3,
                            "short_count": 1,
                            "min_abs_score": 0.12,
                        },
                    }

            claude = FakeClaude()
            mutator = SpecMutator(settings, claude=claude)  # type: ignore[arg-type]
            builder = WorkspaceBuilder(
                settings=settings,
                ancestry=ancestry,
                mutator=mutator,
            )
            session = builder.initialize_session(
                track="trend_signals",
                run_session_id="session-empty-gates",
                family_scope=None,
            )
            parent = mutator.load_seed_specs("trend_signals")[0]
            iteration_paths = builder.update_iteration(
                session=session,
                parent=parent,
                iteration_number=6,
                phase_label="main",
                force_novelty=False,
                market_summary={
                    "market_bundle": {"bundle_id": "bundle-1", "symbols": ["BTC", "ETH", "SOL", "HYPE"]},
                    "perp_snapshot": [],
                },
            )
            iteration_paths["planner_contract_path"].write_text(
                json.dumps(
                    {
                        "target_family": "perp_multi_asset_carry",
                        "must_answer": "Does asymmetric 3L/1S improve carry returns without regime gates?",
                        "required_variation_axis": "non_regime",
                        "required_feature_roles": ["one core_carry feature", "one non_regime_axis feature"],
                        "planner_regime_gates": {},
                    },
                    indent=2,
                )
            )
            research_note_path = iteration_paths["research_note_path"]
            research_note_path.write_text(
                "## Proposed next experiment\n"
                "Stay in perp_multi_asset_carry and test 3L/1S book construction with no regime gate.\n"
            )

            runner = SpecWriterRunner(
                settings=SimpleNamespace(
                    root_dir=self.repo_root,
                    claude_timeout_s=30.0,
                ),
                claude=claude,  # type: ignore[arg-type]
                mutator=mutator,
            )
            result = self.async_run(
                runner.run(
                    session=session,
                    research_note_path=research_note_path,
                    iteration_paths=iteration_paths,
                    parent=parent,
                )
            )
            self.assertTrue(result['accepted'])
            trace = json.loads(Path(result['trace_path']).read_text())
            self.assertEqual(trace["attempts"][0]["material_changed_fields"], [])
            self.assertFalse(trace["attempts"][0]["material_drift"])

    def test_builder_marks_non_regime_axis_after_regime_focused_carry_streak(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = self._settings(tmp)
            ancestry = LineageStore(settings.ancestry_db_path)
            mutator = SpecMutator(settings, claude=SimpleNamespace())
            builder = WorkspaceBuilder(
                settings=settings,
                ancestry=ancestry,
                mutator=mutator,
            )
            rows = [
                {
                    "family": "perp_multi_asset_carry",
                    "spec": {
                        "family": "perp_multi_asset_carry",
                        "features": ["funding_72h_mean", "co_movement_72h"],
                        "regime_gates": {"entry": [{"expression": "co_movement_72h", "min": 0.2}]},
                    },
                    "research_summary": {"run_context": {"deterministic": False}},
                },
                {
                    "family": "perp_multi_asset_carry",
                    "spec": {
                        "family": "perp_multi_asset_carry",
                        "features": ["funding_carry_to_vol", "trend_strength_72h"],
                        "regime_gates": {"entry": [{"expression": "trend_strength_72h", "max": 0.4}]},
                    },
                    "research_summary": {"run_context": {"deterministic": False}},
                },
            ]
            guidance = builder._carry_variation_guidance(rows=rows)
            self.assertEqual(guidance["required_variation_axis"], "non_regime")

    def test_writer_prompt_includes_exact_planner_gate_spec(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = self._settings(tmp)
            ancestry = LineageStore(settings.ancestry_db_path)

            class FakeClaude:
                def __init__(self) -> None:
                    self.last_trace = {"ok": True}
                    self.last_exchange = {"ok": True}

                async def complete_json_messages(self, **_kwargs: object) -> dict[str, object]:
                    return {
                        "track": "trend_signals",
                        "family": "perp_multi_asset_carry",
                        "hypothesis": "copy the planner gate literally",
                        "neutrality_basis": "none",
                        "features": [
                            "funding_168h_mean",
                            "funding_72h_mean",
                            "funding_carry_to_vol",
                            "funding_dispersion_72h",
                            "realized_vol_168h",
                        ],
                        "universe": {
                            "basis_groups": ["BTC", "ETH", "SOL", "HYPE"],
                            "max_symbols": 4,
                            "lookback_days": 365,
                            "interval": "1h",
                        },
                        "risk": {"max_asset_weight": 0.35, "rebalance_threshold": 0.03, "max_leverage": 1.0},
                        "regime_gates": {
                            "entry": [{"expression": "funding_dispersion_72h", "min": 1e-06}],
                            "exit_on_break": True,
                        },
                        "params": {"long_count": 2, "short_count": 2, "gross_target": 1.0},
                    }

            class FakeHypothesisSandbox:
                provider = None

                async def _tool_probe_spec_gate_impact(self, **_kwargs: object) -> dict[str, object]:
                    return {
                        "ok": True,
                        "warnings": [],
                        "gate_coverage": {"configured": True, "combined_active_fraction": 0.3},
                        "selector_train_comparison": {
                            "delta": {
                                "median_total_return": 0.01,
                                "median_sharpe": 0.05,
                            }
                        },
                    }

            claude = FakeClaude()
            mutator = SpecMutator(settings, claude=claude)  # type: ignore[arg-type]
            builder = WorkspaceBuilder(
                settings=settings,
                ancestry=ancestry,
                mutator=mutator,
            )
            session = builder.initialize_session(
                track="trend_signals",
                run_session_id="session-exact-gate",
                family_scope=None,
            )
            parent = mutator.load_seed_specs("trend_signals")[0]
            iteration_paths = builder.update_iteration(
                session=session,
                parent=parent,
                iteration_number=5,
                phase_label="main",
                force_novelty=False,
                market_summary={
                    "market_bundle": {"bundle_id": "bundle-1", "symbols": ["BTC", "ETH", "SOL", "HYPE"]},
                    "perp_snapshot": [],
                },
            )
            research_note_path = iteration_paths["research_note_path"]
            research_note_path.write_text(
                dump_frontmatter(
                    {
                        "decision": "test_exact_gate_copy",
                        "search_mode": "branch_same_family",
                        "target_family": "perp_multi_asset_carry",
                        "target_universe": ["BTC", "ETH", "SOL", "HYPE"],
                        "core_hypothesis": "Keep the carry core and use a permissive funding dispersion gate.",
                        "informative_test": "Use the planner-provided threshold literally.",
                        "expected_success": ["better validation robustness"],
                        "expected_failure": ["no measurable change"],
                        "evidence_paths": [],
                        "tools_used": [],
                        "tracking_tags": ["carry"],
                        "must_answer": "Does a permissive funding_dispersion_72h gate help without killing activity?",
                        "required_feature_roles": ["one core_carry feature", "one orthogonal_regime feature"],
                        "required_features": ["funding_dispersion_72h"],
                        "required_gate_dimensions": ["funding_dispersion_72h"],
                        "regime_gates": {
                            "entry": [{"expression": "funding_dispersion_72h", "min": 1e-06}],
                            "exit_on_break": True,
                        },
                        "writer_inputs": ["manifests/family/perp_multi_asset_carry.md"],
                    },
                    "## Suggested gate spec\nUse the exact gate threshold from frontmatter.",
                )
            )

            runner = SpecWriterRunner(
                settings=SimpleNamespace(
                    root_dir=self.repo_root,
                    claude_timeout_s=30.0,
                ),
                claude=claude,  # type: ignore[arg-type]
                mutator=mutator,
                hypothesis_sandbox=FakeHypothesisSandbox(),  # type: ignore[arg-type]
            )
            result = self.async_run(
                runner.run(
                    session=session,
                    research_note_path=research_note_path,
                    iteration_paths=iteration_paths,
                    parent=parent,
                )
            )
            trace = json.loads(Path(result['trace_path']).read_text())
            self.assertIn("## Exact Planner Gate Spec", trace["inputs"]["initial_user_prompt"])
            self.assertIn("min: 0.000001", trace["inputs"]["initial_user_prompt"])
            self.assertEqual(
                trace["inputs"]["planner_contract"]["planner_regime_gates"]["entry"][0]["min"],
                1e-06,
            )
            self.assertIsNotNone(result['spec_payload'])
            self.assertEqual(result['spec_payload']["regime_gates"]["entry"][0]["min"], 1e-06)

    def test_writer_runner_keeps_negative_gate_lint_deltas_as_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = self._settings(tmp)
            ancestry = LineageStore(settings.ancestry_db_path)

            class FakeClaude:
                def __init__(self) -> None:
                    self.calls: list[list[dict[str, str]]] = []
                    self.last_trace = {"ok": True}
                    self.last_exchange = {"ok": True}

                async def complete_json_messages(self, **kwargs: object) -> dict[str, object]:
                    messages = list(kwargs["messages"])  # type: ignore[index]
                    self.calls.append(messages)
                    if len(self.calls) == 1:
                        return {
                            "track": "trend_signals",
                            "family": "perp_multi_asset_carry",
                            "hypothesis": "bad gate first pass",
                            "neutrality_basis": "none",
                            "features": ["relative_carry_z_72h", "funding_dispersion_72h"],
                            "universe": {
                                "basis_groups": ["BTC", "ETH", "SOL", "HYPE"],
                                "max_symbols": 4,
                                "lookback_days": 365,
                                "interval": "1h",
                            },
                            "risk": {"max_asset_weight": 0.35, "rebalance_threshold": 0.03, "max_leverage": 1.0},
                            "regime_gates": {
                                "entry": [{"expression": "funding_dispersion_72h", "min": 0.00001}],
                                "exit_on_break": True,
                            },
                            "params": {"long_count": 2, "short_count": 2, "gross_target": 1.0},
                        }
                    return {
                        "track": "trend_signals",
                        "family": "perp_multi_asset_carry",
                        "hypothesis": "keep the named gate after repairing the harmful threshold",
                        "neutrality_basis": "none",
                        "features": ["relative_carry_z_72h", "funding_dispersion_72h"],
                        "universe": {
                            "basis_groups": ["BTC", "ETH", "SOL", "HYPE"],
                            "max_symbols": 4,
                            "lookback_days": 365,
                            "interval": "1h",
                        },
                        "risk": {"max_asset_weight": 0.35, "rebalance_threshold": 0.03, "max_leverage": 1.0},
                        "regime_gates": {
                            "entry": [{"expression": "funding_dispersion_72h", "min": 0.00001}],
                            "exit_on_break": True,
                        },
                        "params": {"long_count": 2, "short_count": 2, "gross_target": 1.0},
                    }

            class FakeHypothesisSandbox:
                def __init__(self) -> None:
                    self.calls = 0

                async def _tool_probe_spec_gate_impact(self, **_kwargs: object) -> dict[str, object]:
                    self.calls += 1
                    if self.calls == 1:
                        return {
                            "ok": True,
                            "warnings": [],
                            "gate_coverage": {"configured": True},
                            "selector_train_comparison": {
                                "delta": {
                                    "median_total_return": -0.02,
                                    "median_sharpe": -0.15,
                                }
                            },
                        }
                    return {
                        "ok": True,
                        "warnings": [],
                        "gate_coverage": {"configured": False},
                        "selector_train_comparison": {"delta": {}},
                    }

            claude = FakeClaude()
            mutator = SpecMutator(settings, claude=claude)  # type: ignore[arg-type]
            builder = WorkspaceBuilder(
                settings=settings,
                ancestry=ancestry,
                mutator=mutator,
            )
            session = builder.initialize_session(
                track="trend_signals",
                run_session_id="session-6",
                family_scope=None,
            )
            parent = mutator.load_seed_specs("trend_signals")[0]
            iteration_paths = builder.update_iteration(
                session=session,
                parent=parent,
                iteration_number=6,
                phase_label="main",
                force_novelty=False,
                market_summary={
                    "market_bundle": {"bundle_id": "bundle-1", "symbols": ["BTC", "ETH", "SOL", "HYPE"]},
                    "perp_snapshot": [],
                },
            )
            research_note_path = iteration_paths["research_note_path"]
            research_note_path.write_text(
                dump_frontmatter(
                    {
                        "decision": "test_gate_lint",
                        "search_mode": "branch_same_family",
                        "target_family": "perp_multi_asset_carry",
                        "target_universe": ["BTC", "ETH", "SOL", "HYPE"],
                        "core_hypothesis": "Try a funding dispersion gate.",
                        "informative_test": "See whether the gate helps.",
                        "expected_success": ["better validation robustness"],
                        "expected_failure": ["no measurable change"],
                        "evidence_paths": [],
                        "tools_used": [],
                        "tracking_tags": ["carry"],
                        "must_answer": "Does gating on `funding_dispersion_72h` improve pre-audit return without making validation negative for `perp_multi_asset_carry`?",
                        "required_feature_roles": ["one core_carry feature", "one orthogonal_regime feature"],
                        "required_gate_dimensions": ["funding_dispersion_72h"],
                        "forbidden_motifs": [],
                        "gate_intent": {"type": "suppress_bad_regime", "target_dimension": "funding_dispersion_72h"},
                        "writer_inputs": ["manifests/family/perp_multi_asset_carry.md"],
                    },
                    "## Proposed next experiment\nUse a funding dispersion gate.",
                )
            )

            runner = SpecWriterRunner(
                settings=SimpleNamespace(
                    root_dir=self.repo_root,
                    claude_timeout_s=30.0,
                ),
                claude=claude,  # type: ignore[arg-type]
                mutator=mutator,
                hypothesis_sandbox=FakeHypothesisSandbox(),  # type: ignore[arg-type]
            )
            result = self.async_run(
                runner.run(
                    session=session,
                    research_note_path=research_note_path,
                    iteration_paths=iteration_paths,
                    parent=parent,
                )
            )
            trace = json.loads(Path(result['trace_path']).read_text())
            self.assertFalse(
                any(
                    "negative_selector_train_return_delta" in issue
                    for issue in trace["attempts"][0]["hard_issues"]
                )
            )
            self.assertIn(
                "negative_selector_train_return_delta",
                list((trace["attempts"][0]["gate_lint"] or {}).get("warnings") or []),
            )
            self.assertIn(
                "negative_selector_train_sharpe_delta",
                list((trace["attempts"][0]["gate_lint"] or {}).get("warnings") or []),
            )
            self.assertEqual(result['spec_payload']["regime_gates"]["entry"][0]["expression"], "funding_dispersion_72h")

    def test_writer_runner_retains_negative_gate_lint_warning_without_hard_reject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = self._settings(tmp)
            ancestry = LineageStore(settings.ancestry_db_path)

            class FakeClaude:
                def __init__(self) -> None:
                    self.last_trace = {"ok": True}
                    self.last_exchange = {"ok": True}

                async def complete_json_messages(self, **_kwargs: object) -> dict[str, object]:
                    return {
                        "track": "trend_signals",
                        "family": "perp_multi_asset_carry",
                        "hypothesis": "still bad after repair",
                        "neutrality_basis": "none",
                        "features": ["funding_carry_to_vol", "market_volatility_168h"],
                        "universe": {
                            "basis_groups": ["BTC", "ETH", "SOL", "HYPE"],
                            "max_symbols": 4,
                            "lookback_days": 365,
                            "interval": "1h",
                        },
                        "risk": {"max_asset_weight": 0.35, "rebalance_threshold": 0.03, "max_leverage": 1.0},
                        "regime_gates": {
                            "entry": [{"expression": "market_volatility_168h", "max": 0.0085}],
                            "exit_on_break": True,
                        },
                        "params": {"long_count": 2, "short_count": 2, "gross_target": 1.0},
                    }

            class FakeHypothesisSandbox:
                provider = None

                async def _tool_probe_spec_gate_impact(self, **_kwargs: object) -> dict[str, object]:
                    return {
                        "ok": True,
                        "warnings": [],
                        "gate_coverage": {"configured": True, "combined_active_fraction": 0.76},
                        "selector_train_comparison": {
                            "delta": {
                                "median_total_return": 0.01,
                                "median_sharpe": -0.2,
                            }
                        },
                    }

            claude = FakeClaude()
            mutator = SpecMutator(settings, claude=claude)  # type: ignore[arg-type]
            builder = WorkspaceBuilder(
                settings=settings,
                ancestry=ancestry,
                mutator=mutator,
            )
            session = builder.initialize_session(
                track="trend_signals",
                run_session_id="session-soft-fail",
                family_scope=None,
            )
            parent = mutator.load_seed_specs("trend_signals")[0]
            iteration_paths = builder.update_iteration(
                session=session,
                parent=parent,
                iteration_number=7,
                phase_label="main",
                force_novelty=False,
                market_summary={
                    "market_bundle": {"bundle_id": "bundle-1", "symbols": ["BTC", "ETH", "SOL", "HYPE"]},
                    "perp_snapshot": [],
                },
            )
            research_note_path = iteration_paths["research_note_path"]
            research_note_path.write_text(
                dump_frontmatter(
                    {
                        "decision": "test_gate_lint_soft_fail",
                        "search_mode": "branch_same_family",
                        "target_family": "perp_multi_asset_carry",
                        "target_universe": ["BTC", "ETH", "SOL", "HYPE"],
                        "core_hypothesis": "Try market volatility as the gate dimension.",
                        "informative_test": "See whether the gate helps.",
                        "expected_success": ["better validation robustness"],
                        "expected_failure": ["no measurable change"],
                        "evidence_paths": [],
                        "tools_used": [],
                        "tracking_tags": ["carry"],
                        "must_answer": "Does gating on `market_volatility_168h` improve pre-audit return without making validation negative for `perp_multi_asset_carry`?",
                        "required_feature_roles": ["one core_carry feature", "one orthogonal_regime feature"],
                        "required_gate_dimensions": ["market_volatility_168h"],
                        "forbidden_motifs": [],
                        "gate_intent": {"type": "suppress_bad_regime", "target_dimension": "market_volatility_168h"},
                        "writer_inputs": ["manifests/family/perp_multi_asset_carry.md"],
                    },
                    "## Proposed next experiment\nUse a market volatility gate.",
                )
            )

            runner = SpecWriterRunner(
                settings=SimpleNamespace(
                    root_dir=self.repo_root,
                    claude_timeout_s=30.0,
                ),
                claude=claude,  # type: ignore[arg-type]
                mutator=mutator,
                hypothesis_sandbox=FakeHypothesisSandbox(),  # type: ignore[arg-type]
            )
            runner._activity_lint = self.async_identity({"ok": True, "active_bar_fraction": 0.25, "median_active_asset_count": 3.0})  # type: ignore[method-assign]
            result = self.async_run(
                runner.run(
                    session=session,
                    research_note_path=research_note_path,
                    iteration_paths=iteration_paths,
                    parent=parent,
                )
            )
            trace = json.loads(Path(result['trace_path']).read_text())
            self.assertFalse(
                any(
                    "negative_selector_train_sharpe_delta" in issue
                    for issue in trace["attempts"][0]["hard_issues"]
                )
            )
            self.assertIn(
                "negative_selector_train_sharpe_delta",
                json.dumps(trace["attempts"][0]["gate_lint"]),
            )

    def test_reflector_runner_keeps_raw_body_and_backfills_missing_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = SimpleNamespace(
                root_dir=self.repo_root,
                artifact_dir=Path(tmp) / "runs",
                ancestry_db_path=Path(tmp) / "ancestry.db",
                claude_timeout_s=30.0,
            )
            ancestry = LineageStore(settings.ancestry_db_path)
            mutator = SpecMutator(settings, claude=SimpleNamespace())
            builder = WorkspaceBuilder(
                settings=settings,
                ancestry=ancestry,
                mutator=mutator,
            )
            session = builder.initialize_session(
                track="trend_signals",
                run_session_id="session-reflector-raw",
                family_scope=None,
            )
            parent = mutator.load_seed_specs("trend_signals")[0]
            iteration_paths = builder.update_iteration(
                session=session,
                parent=parent,
                iteration_number=8,
                phase_label="main",
                force_novelty=False,
                market_summary={
                    "market_bundle": {"bundle_id": "bundle-1", "symbols": ["BTC", "ETH", "SOL", "HYPE"]},
                    "perp_snapshot": [],
                },
            )

            class FakeClaude:
                def __init__(self) -> None:
                    self.last_trace = {"ok": True}
                    self.last_exchange = {"ok": True}

                async def complete_text(self, **_kwargs: object) -> str:
                    return (
                        "---\n"
                        "family: perp_multi_asset_carry\n"
                        "verdict: promising_but_fragile\n"
                        "---\n\n"
                        "What changed: kept carry core fixed and added a volatility floor.\n"
                        "Why it failed/worked: this improved validation but not enough to make the gate reusable.\n"
                        "Do not repeat: single-factor volatility ceilings.\n"
                        "Next test: interact funding level with volatility instead of another standalone gate.\n"
                    )

            runner = ReflectionRunner(
                settings=settings,
                claude=FakeClaude(),  # type: ignore[arg-type]
            )
            result = self.async_run(
                runner.run(
                    session=session,
                    spec_hash="spec-1",
                    iteration_paths=iteration_paths,
                    evaluation_packet={
                        "family": "perp_multi_asset_carry",
                        "spec": {
                            "features": ["relative_carry_z_72h", "market_volatility_168h"],
                            "regime_gates": {"entry": [{"expression": "market_volatility_168h", "min": 0.001}]},
                        },
                        "summary": {"pre_audit_canonical_total_return": 0.02},
                        "parent_delta": {"pre_audit_canonical_total_return_delta": 0.01},
                        "dominant_failure_mode": "fragile_gate",
                        "failed_motif_signature": "carry|vol_floor",
                        "suggested_next_move": "test_interaction_gate_funding_level_x_volatility_168h",
                        "evidence_paths": ["cards/experiments/spec-1.md"],
                        "recent_completed_runs": [],
                    },
                )
            )

            saved_frontmatter, saved_body = parse_frontmatter(Path(result['lesson_card_path']).read_text())
            self.assertEqual(saved_frontmatter["family"], "perp_multi_asset_carry")
            self.assertEqual(saved_frontmatter["verdict"], "promising_but_fragile")
            self.assertEqual(saved_frontmatter["failure_mode"], "fragile_gate")
            self.assertEqual(
                saved_frontmatter["one_next_test"],
                "test_interaction_gate_funding_level_x_volatility_168h",
            )
            self.assertIn("kept carry core fixed and added a volatility floor", saved_body)
            self.assertIn("single-factor volatility ceilings", saved_body)

            trace = json.loads(Path(result['trace_path']).read_text())
            self.assertIn("raw_reflection", trace)
            self.assertIn("saved_frontmatter", trace)
            self.assertIn("saved_body", trace)
            self.assertIsNone(trace["frontmatter_parse_error"])

    def async_run(self, awaitable):
        import asyncio

        return asyncio.run(awaitable)

    def async_identity(self, value):
        async def _runner(*_args, **_kwargs):
            return value

        return _runner


if __name__ == "__main__":
    unittest.main()


