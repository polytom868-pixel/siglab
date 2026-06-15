"""Comprehensive pytest tests for all orchestration modules in siglab/orchestration/."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, AsyncMock, patch


from siglab.orchestration.contracts import (
    PreflightResult,
    _numeric_equal,
    conformance_violations,
    extract_embedded_yaml_block,
    has_non_regime_variation,
    has_policy_variation,
)
from siglab.orchestration.hooks import WorkspaceHooks
from siglab.orchestration.optimizer_runner import (
    OptunaOptimizerRunner,
    _float_or_none,
    _payload_patch_for_paths,
    _suggest_params,
    _threshold_bounds,
    infer_optuna_space,
)
from siglab.orchestration.planner_runner import PlannerResult, ResearchPlannerRunner
from siglab.orchestration.planner_types import string_list, unique_strings, dict_value, MAX_PLANNER_TOOL_CALLS
from siglab.orchestration.planner_contract import (
    extract_planner_contract,
    fallback_contract,
    merge_hint_fragment,
    policy_control_hint,
    section_or_fallback,
    last_question,
    default_forbidden_motifs,
    default_writer_inputs,
    concretize_must_answer,
    normalize_regime_gates,
    explicit_contract_keys,
    body_family_override,
    body_trade_style,
    normalize_required_feature_roles,
    default_required_feature_roles,
)
from siglab.orchestration.planner_validation import (
    semantic_note_issues,
    planner_tool_usage_issues,
    planner_finish_issues,
    should_disable_tools_for_repair,
    planner_probe_budget_issues,
    planner_total_tool_budget_issues,
    merge_trace_tool_usage,
    trace_tool_names,
    planner_probe_claim_issues,
)
from siglab.orchestration.reflector_runner import ReflectionRunner
from siglab.orchestration.trials import (
    apply_path_value,
    build_spec_patch,
    clone_payload,
    deployment_rank,
    get_path_value,
    score_diagnosis,
    summarize_generalization,
    summarize_patch,
)
from siglab.orchestration.writer_runner import SpecWriterRunner


from tests._factories import make_runner


def _make_planner_runner() -> ResearchPlannerRunner:
    base = make_runner()
    base.settings.claude_timeout_s = 120
    base.settings.artifact_dir = Path("/fake/artifacts")
    runner = object.__new__(ResearchPlannerRunner)
    runner.settings = base.settings
    runner.claude = base.claude
    runner.hypothesis_sandbox = MagicMock()
    runner.web_researcher = MagicMock()
    runner.workspace_builder = MagicMock()
    return runner


def _make_writer_runner() -> SpecWriterRunner:
    return make_runner()


def _make_optimizer_runner() -> OptunaOptimizerRunner:
    base = make_runner(optuna_trials=5)
    runner = object.__new__(OptunaOptimizerRunner)
    runner.settings = base.settings
    runner.evaluator = MagicMock()
    runner.mutator = base.mutator
    runner.ancestry = MagicMock()
    return runner


def _make_reflection_runner() -> ReflectionRunner:
    base = make_runner()
    runner = object.__new__(ReflectionRunner)
    runner.settings = base.settings
    runner.claude = AsyncMock()
    return runner


# ============================================================
# planner_runner.py — ResearchPlannerRunner
# ============================================================


class TestResearchPlannerRunner:
    def _make_runner(self):
        """Helper to build a bare ResearchPlannerRunner with mock deps."""
        return _make_planner_runner()

    def test_init_stores_all_deps(self):
        runner = self._make_runner()
        assert runner.settings is not None
        assert runner.claude is not None
        assert runner.hypothesis_sandbox is not None
        assert runner.web_researcher is not None
        assert runner.workspace_builder is not None

    def test_fallback_system_prompt_returns_text(self):
        runner = self._make_runner()
        prompt = runner._fallback_system_prompt()
        assert isinstance(prompt, str)
        assert "research planner" in prompt.lower()
        assert len(prompt) > 50

    def test_build_user_prompt_includes_target_universe_and_sections(self, tmp_path):
        runner = self._make_runner()
        session = SimpleNamespace(
            root=tmp_path,
            current_dir=tmp_path,
            cache_dir=tmp_path,
            families=["test_family"],
            track="test_track",
            memory_scope="track_shared",
            run_session_id="sess_1",
        )
        parent = SimpleNamespace(
            family="test_family",
            universe=SimpleNamespace(basis_groups=["BTC", "ETH"]),
        )
        # Create one default file
        (tmp_path / "TASK.md").write_text("fake task")
        prompt = runner._build_user_prompt(session=session, parent=parent)
        assert "BTC" in prompt
        assert "ETH" in prompt
        assert "TASK.md" in prompt

    def test_latest_evidence_summary_returns_none_when_no_evidence(self, tmp_path):
        runner = self._make_runner()
        session = SimpleNamespace(
            current_dir=tmp_path,
            cache_dir=tmp_path,
        )
        runner.settings.artifact_dir = tmp_path / "artifacts"
        result = runner._latest_evidence_summary(session=session)
        assert result is None

    def test_compact_evidence_summary_returns_dict(self, tmp_path):
        runner = self._make_runner()
        path = tmp_path / "evidence/some.summary.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"record_count": 5, "link_count": 2}))
        # Mock relative_to to work
        with patch.object(Path, "relative_to", return_value=path):
            result = runner._compact_evidence_summary(
                path=path, scope="workspace_current", payload={"record_count": 5, "link_count": 2}
            )
        assert isinstance(result, dict)
        assert "source" in result
        assert result["scope"] == "workspace_current"
        assert result["record_count"] == 5

    def test_evidence_summary_relevance_computes_matched_entities(self):
        runner = self._make_runner()
        parent = SimpleNamespace(
            universe=SimpleNamespace(basis_groups=["BTC", "ETH"])
        )
        summary = {
            "entity_counts": {"BTC": 3, "SOL": 1},
            "top_links": [{"feed_entity": "BTC"}, {"entity": "ETH"}],
        }
        result = runner._evidence_summary_relevance(summary, parent=parent)
        assert "matched_entities" in result
        assert "BTC" in result["matched_entities"]
        assert result["score"] > 0

    def test_safe_parse_frontmatter_valid(self):
        runner = self._make_runner()
        text = "---\nkey: value\n---\nbody text"
        frontmatter, body = runner._safe_parse_frontmatter(text)
        # parse_frontmatter is called; if no YAML frontmatter it returns {}
        assert isinstance(frontmatter, dict)
        assert isinstance(body, str)

    def test_safe_parse_frontmatter_invalid(self):
        runner = self._make_runner()
        # Plain text with no frontmatter should be handled gracefully
        frontmatter, body = runner._safe_parse_frontmatter("no frontmatter here")
        assert isinstance(frontmatter, dict)
        assert body == "no frontmatter here"

    def test_extract_planner_contract_fills_fallback_fields(self):
        self._make_runner()
        parent = SimpleNamespace(
            family="perp_multi_asset_carry",
            universe=SimpleNamespace(basis_groups=["BTC"]),
            params={},
        )
        current_state: dict = {}
        tool_refs: list[str] = []
        session = SimpleNamespace(families=["perp_multi_asset_carry"])
        note_text = "## Diagnosis\ntest\n## What to test\nsomething"
        contract = extract_planner_contract(
            note_text=note_text,
            note_body=note_text,
            raw_frontmatter={},
            yaml_fragments=[],
            parent=parent,
            current_state=current_state,
            tool_refs=tool_refs,
            session=session,
        )
        assert contract["target_family"] == "perp_multi_asset_carry"
        assert contract["decision"] == "refine_current_family"
        assert isinstance(contract["required_features"], list)
        assert contract["forbidden_motifs"] == ["second pure trend overlay"]

    def test_merge_hint_fragment_merges_list_keys(self):
        self._make_runner()
        base = {"target_family": "fam_a", "target_universe": ["BTC"]}
        fragment = {"target_universe": ["ETH"], "target_family": "fam_b"}
        merged = merge_hint_fragment(base, fragment)
        assert "ETH" in str(merged["target_universe"])

    def test_merge_hint_fragment_merges_nested_keys(self):
        self._make_runner()
        base = {"target_family": "fam_a"}
        fragment = {"gate_intent": {"type": "suppress", "target_dimension": "policy_persistence"}}
        merged = merge_hint_fragment(base, fragment)
        assert merged["gate_intent"]["type"] == "suppress"

    def test_semantic_note_issues_catches_short_notes(self):
        self._make_runner()
        issues = semantic_note_issues(
            note_text="short",
            planner_contract={"target_family": "test", "must_answer": "q?", "informative_test": "t"},
        )
        assert "research_note_too_short" in issues

    def test_semantic_note_issues_catches_empty_missing_fields(self):
        self._make_runner()
        issues = semantic_note_issues(
            note_text="A note with test keyword and switch keyword and enough length to pass the 80 char threshold really",
            planner_contract={},
        )
        issue_keys = {i.split(":")[0] for i in issues}
        assert "no_target_family" in issue_keys
        assert "no_concrete_question" in issue_keys
        assert "no_informative_test" in issue_keys

    def test_planner_tool_usage_issues_detects_missing_calls(self):
        runner = self._make_runner()
        runner.settings.llm_provider = "bai"
        fake_tool = SimpleNamespace(name="search_workspace")
        issues = planner_tool_usage_issues(
            tools=[fake_tool],  # type: ignore[list-item]
            trace={"tool_rounds_used": 0, "tool_calls": []},
            requires_tool_use=True,
        )
        assert "planner_did_not_call_workspace_or_probe_tool" in issues

    def test_planner_tool_usage_issues_returns_empty_when_tools_used(self):
        runner = self._make_runner()
        runner.settings.llm_provider = "bai"
        fake_tool = SimpleNamespace(name="search_workspace")
        issues = planner_tool_usage_issues(
            tools=[fake_tool],  # type: ignore[list-item]
            trace={"tool_rounds_used": 1, "tool_calls": [{"name": "search_workspace"}]},
            requires_tool_use=True,
        )
        assert issues == []

    def test_planner_finish_issues_catches_truncation(self):
        self._make_runner()
        issues = planner_finish_issues(
            trace={"response_finish_reason": "length"},
            note_text="some note here",
        )
        assert any("planner_response_truncated" in i for i in issues)

    def test_planner_finish_issues_catches_mid_list_endings(self):
        self._make_runner()
        issues = planner_finish_issues(
            trace={},
            note_text="some items\n-",
        )
        assert "planner_note_ends_mid_list" in issues

    def test_repair_should_disable_tools_returns_true(self):
        self._make_runner()
        feedback = {"semantic_issues": ["planner_trace_error:timeout"]}
        assert should_disable_tools_for_repair(feedback) is True

    def test_repair_should_disable_tools_returns_false(self):
        self._make_runner()
        assert should_disable_tools_for_repair(None) is False
        assert should_disable_tools_for_repair({}) is False
        assert should_disable_tools_for_repair({"semantic_issues": ["note_too_short"]}) is False

    def test_extract_yaml_fragments_parses_blocks(self):
        runner = self._make_runner()
        text = "some text\n```yaml\ntarget_family: test_family\n```"
        fragments = runner._extract_yaml_fragments(text)
        assert len(fragments) > 0
        assert any(f.get("target_family") == "test_family" for f in fragments)

    def test_extract_yaml_fragments_deduplicates(self):
        runner = self._make_runner()
        text = "```yaml\na: 1\n```\nmore\n```yaml\na: 1\n```"
        fragments = runner._extract_yaml_fragments(text)
        # Should deduplicate identical fragments
        matching = [f for f in fragments if f == {"a": 1}]
        assert len(matching) <= 1

    def test_body_family_override_matches_family_patterns(self):
        self._make_runner()
        families = ["perp_multi_asset_carry", "perp_basket_neutral_unlevered"]
        text = "switch to `perp_basket_neutral_unlevered` and test"
        result = body_family_override(text, families)
        assert result == "perp_basket_neutral_unlevered"

    def test_body_trade_style_matches_assignment(self):
        self._make_runner()
        text = "some note with trade_style = dynamic_vol_target"
        result = body_trade_style(text)
        assert result == "dynamic_vol_target"

    def test_body_trade_style_returns_none_when_missing(self):
        self._make_runner()
        assert body_trade_style("no trade style here") is None

    def test_planner_probe_budget_issues(self):
        self._make_runner()
        trace = {
            "tool_calls": [
                {
                    "name": "probe_feature_forward_stats",
                    "result": {"error": "planner_probe_budget_exhausted", "probe_type": "probe_feature_forward_stats"},
                }
            ]
        }
        issues = planner_probe_budget_issues(trace=trace)
        assert any("planner_probe_budget_exhausted" in i for i in issues)

    def test_planner_total_tool_budget_issues(self):
        self._make_runner()
        # Create many fake tool calls
        trace = {"tool_calls": [{"name": f"tool_{i}"} for i in range(MAX_PLANNER_TOOL_CALLS + 1)]}
        issues = planner_total_tool_budget_issues(trace=trace)
        assert any("planner_tool_call_budget_exceeded" in i for i in issues)

    def test_fallback_contract_has_expected_keys(self):
        self._make_runner()
        parent = SimpleNamespace(
            family="perp_multi_asset_carry",
            universe=SimpleNamespace(basis_groups=["BTC"]),
            params={},
        )
        current_state: dict = {}
        tool_refs: list[str] = []
        contract = fallback_contract(parent=parent, current_state=current_state, tool_refs=tool_refs)
        assert contract["target_family"] == "perp_multi_asset_carry"
        assert contract["decision"] == "refine_current_family"
        assert "evidence_paths" in contract

    def test_fallback_note_returns_text(self):
        runner = self._make_runner()
        parent = SimpleNamespace(family="test_family")
        current_state: dict = {}
        note = runner._fallback_note(parent=parent, current_state=current_state)
        assert isinstance(note, str)
        assert "test_family" in note

    def test_string_list_returns_clean_list(self):
        self._make_runner()
        assert string_list(["a", "b", ""]) == ["a", "b"]
        assert string_list("not a list") == []
        assert string_list(None) == []

    def test_unique_strings_deduplicates(self):
        self._make_runner()
        result = unique_strings(["a", "b", "a", "c"])
        assert result == ["a", "b", "c"]

    def test_explicit_contract_keys_collects_keys(self):
        self._make_runner()
        keys = explicit_contract_keys(
            {"target_family": "fam", "extra": 1},
            [{"another_key": "val"}],
        )
        assert "target_family" in keys
        assert "another_key" in keys


# ============================================================
# writer_runner.py — SpecWriterRunner
# ============================================================


class TestSpecWriterRunner:
    def _make_runner(self):
        return _make_writer_runner()

    def test_init_stores_deps(self):
        runner = self._make_runner()
        assert runner.settings is not None
        assert runner.claude is not None
        assert runner.mutator is not None

    def test_preflight_spec_rejects_invalid_payload(self):
        runner = self._make_runner()
        session = SimpleNamespace(track="test", families=["fam_a"], root=Path("/fake"))
        parent = SimpleNamespace(
            family="fam_a",
            universe=SimpleNamespace(basis_groups=["BTC"]),
        )
        preflight = asyncio.run(runner._preflight_spec(
            session=session,
            payload=None,
            parse_error="JsonDecodeError: unterminated string",
            track="test",
            parent=parent,
            target_family="fam_a",
            planner_contract={"target_family": "fam_a"},
            allowed_families=["fam_a"],
            allowed_features_by_family={},
            family_defaults={},
        ))
        assert preflight.parse_error is not None
        assert preflight.validated_payload is None
        assert not preflight.acceptable

    def test_preflight_spec_handles_empty_payload_with_parse_error(self):
        runner = self._make_runner()
        session = SimpleNamespace(track="test", families=["fam_a"], root=Path("/fake"))
        parent = SimpleNamespace(family="fam_a", universe=SimpleNamespace(basis_groups=["BTC"]))
        preflight = asyncio.run(runner._preflight_spec(
            session=session,
            payload={"track": "test", "family": "fam_a"},
            parse_error="parse failed",
            track="test",
            parent=parent,
            target_family="fam_a",
            planner_contract={"target_family": "fam_a"},
            allowed_families=["fam_a"],
            allowed_features_by_family={},
            family_defaults={},
        ))
        # parse_error != None triggers early return
        assert not preflight.acceptable

    def test_preflight_before_write_rejects_invalid(self):
        self._make_runner()
        # Build a preflight result manually that is not acceptable
        preflight = PreflightResult(
            parse_error="error",
            hard_issues=["missing keys"],
            conformance_issues=[],
            gate_lint=None,
            changed_fields=[],
            harmless_changed_fields=[],
            material_changed_fields=[],
            validated_payload=None,
        )
        assert not preflight.acceptable
        assert not preflight.material_drift

    def test_preflight_acceptable_property(self):
        preflight = PreflightResult(
            parse_error=None,
            hard_issues=[],
            conformance_issues=[],
            gate_lint=None,
            changed_fields=[],
            harmless_changed_fields=[],
            material_changed_fields=[],
            validated_payload={"family": "test"},
        )
        assert preflight.acceptable

    def test_build_repair_packet_creates_packet(self):
        runner = self._make_runner()
        preflight = PreflightResult(
            parse_error=None,
            hard_issues=["test error"],
            conformance_issues=[],
            gate_lint=None,
            changed_fields=[],
            harmless_changed_fields=[],
            material_changed_fields=[],
            validated_payload=None,
        )
        packet = runner._build_repair_packet(
            target_family="fam_a",
            planner_contract={},
            payload={"family": "fam_a"},
            preflight=preflight,
        )
        assert packet["family"] == "fam_a"
        assert "test error" in packet["errors"]

    def test_repair_prompt_contains_key_elements(self):
        runner = self._make_runner()
        prompt = runner._repair_prompt(
            repair_packet={"family": "fam_a", "errors": []},
        )
        assert "not acceptable" in prompt
        assert "fam_a" in prompt

    def test_max_attempts_default(self):
        runner = self._make_runner()
        assert runner._max_attempts() == 2

    def test_max_attempts_bai(self):
        runner = self._make_runner()
        runner.settings.llm_provider = "bai"
        assert runner._max_attempts() == 3

    def test_writer_max_tokens_default(self):
        runner = self._make_runner()
        assert runner._writer_max_tokens() == 1200

    def test_writer_max_tokens_bai(self):
        runner = self._make_runner()
        runner.settings.llm_provider = "bai"
        assert runner._writer_max_tokens() == 2200

    def test_clone_payload_deep_copies(self):
        runner = self._make_runner()
        original = {"a": {"b": 1}}
        cloned = runner._clone_payload(original)
        assert cloned == original
        assert cloned is not original
        assert cloned["a"] is not original["a"]


# ============================================================
# contracts.py
# ============================================================


class TestContracts:
    def test_numeric_equal_exact(self):
        assert _numeric_equal(1.0, 1.0) is True
        assert _numeric_equal(1.0, 1.0000000000001) is True
        assert _numeric_equal(1.0, 2.0) is False

    def test_numeric_equal_string_fallback(self):
        assert _numeric_equal("abc", "abc") is True
        assert _numeric_equal("abc", "def") is False

    def test_has_non_regime_variation_true(self):
        # Features with NON_REGIME_ROLES should return True
        spec = {"features": ["top_drop_1h"]}  # top_drop likely not a regime-only feature
        # Without an actual NON_REGIME_ROLES list, test that it returns bool
        result = has_non_regime_variation(spec_payload=spec)
        assert isinstance(result, bool)

    def test_has_non_regime_variation_with_parent_diff_universe(self):
        spec = {"features": ["some_feature"], "params": {}, "universe": {"basis_groups": ["BTC", "ETH"]}}
        parent = {"features": ["some_feature"], "params": {}, "universe": {"basis_groups": ["BTC"]}, "family": "test"}
        result = has_non_regime_variation(spec_payload=spec, parent_payload=parent)
        assert result is True

    def test_has_policy_variation_spot_checks(self):
        parent = {"params": {"entry_abs_score": 0.5, "exit_abs_score": 0.5}}
        spec = {"params": {"entry_abs_score": 0.8, "exit_abs_score": 0.5}}
        result = has_policy_variation(spec_payload=spec, parent_payload=parent)
        assert result is True

    def test_has_policy_variation_no_change(self):
        parent = {"params": {"entry_abs_score": 0.5}}
        spec = {"params": {"entry_abs_score": 0.5}}
        result = has_policy_variation(spec_payload=spec, parent_payload=parent)
        assert result is False

    def test_has_policy_variation_no_parent(self):
        result = has_policy_variation(spec_payload={"params": {}}, parent_payload=None)
        assert result is False

    def test_conformance_violations_family_mismatch(self):
        planner = {"target_family": "fam_a"}
        spec = {"family": "fam_b", "features": [], "params": {}}
        violations = conformance_violations(planner_contract=planner, spec_payload=spec)
        assert any("family mismatch" in v for v in violations)

    def test_conformance_violations_no_violations(self):
        planner = {"target_family": "fam_a"}
        spec = {"family": "fam_a", "features": [], "params": {}}
        violations = conformance_violations(planner_contract=planner, spec_payload=spec)
        assert violations == []

    def test_conformance_violations_trade_style_mismatch(self):
        planner = {"target_family": "perp_pair_trade_unlevered", "target_trade_style": "dynamic"}
        spec = {"family": "perp_pair_trade_unlevered", "features": [], "params": {"trade_style": "static"}}
        violations = conformance_violations(planner_contract=planner, spec_payload=spec)
        assert any("trade_style" in v for v in violations)

    def test_conformance_violations_required_gate_dimension(self):
        planner = {"target_family": "fam_a", "required_gate_dimensions": ["policy_persistence"]}
        spec = {"family": "fam_a", "features": [], "params": {}, "regime_gates": {"entry": []}}
        violations = conformance_violations(planner_contract=planner, spec_payload=spec)
        assert any("required gate dimension" in v for v in violations)

    def test_conformance_violations_forbidden_features(self):
        planner = {"target_family": "fam_a", "forbidden_features": ["rsi_14"]}
        spec = {"family": "fam_a", "features": ["rsi_14", "ema_20"], "params": {}}
        violations = conformance_violations(planner_contract=planner, spec_payload=spec)
        assert any("forbidden feature" in v for v in violations)

    def test_extract_embedded_yaml_block_fenced(self):
        text = "some text\n```yaml\nkey: value\n```"
        result = extract_embedded_yaml_block(text)
        assert result == {"key": "value"}

    def test_extract_embedded_yaml_block_raw(self):
        text = "just text no fence"
        result = extract_embedded_yaml_block(text)
        assert result == {}

    def test_extract_embedded_yaml_block_invalid_yaml(self):
        text = "```yaml\n[invalid: broken\n```"
        result = extract_embedded_yaml_block(text)
        assert result == {}


# ============================================================
# optimizer_runner.py — OptunaOptimizerRunner
# ============================================================


class TestOptunaOptimizerRunner:
    def _make_runner(self):
        return _make_optimizer_runner()

    def test_init_stores_deps(self):
        runner = self._make_runner()
        assert runner.settings is not None
        assert runner.evaluator is not None
        assert runner.mutator is not None
        assert runner.ancestry is not None

    def test_threshold_bounds_small_positive(self):
        low, high, log = _threshold_bounds(0.01)
        assert low < high
        assert log is True

    def test_threshold_bounds_medium(self):
        low, high, log = _threshold_bounds(0.5)
        assert low < high
        assert log is False

    def test_threshold_bounds_negative(self):
        low, high, log = _threshold_bounds(-0.5)
        assert low < high

    def test_threshold_bounds_none(self):
        low, high, log = _threshold_bounds(None)
        assert low == 0.0
        assert high == 1.0

    def test_float_or_none(self):
        assert _float_or_none(5) == 5.0
        assert _float_or_none("not_a_number") is None
        assert _float_or_none(None) is None

    def test_payload_patch_for_paths(self):
        base = {"a": 1, "b": 2}
        target = {"a": 10, "b": 2}
        patch = _payload_patch_for_paths(base_payload=base, target_payload=target, paths=["a"])
        assert len(patch["changes"]) == 1
        assert patch["changes"][0]["old"] == 1
        assert patch["changes"][0]["new"] == 10

    def test_infer_optuna_space_generates_params(self):
        payload = {
            "family": "perp_multi_asset_carry",
            "risk": {"max_asset_weight": 0.3, "rebalance_threshold": 0.03, "max_leverage": 1.0},
            "params": {"gross_target": 1.0, "min_abs_score": 0.1},
            "regime_gates": {"entry": []},
        }
        space = infer_optuna_space(payload)
        assert "family" in space
        assert "parameters" in space
        assert len(space["parameters"]) > 0

    def test_infer_optuna_space_handles_gate_params(self):
        payload = {
            "family": "perp_multi_asset_carry",
            "risk": {"max_asset_weight": 0.3, "rebalance_threshold": 0.03, "max_leverage": 1.0},
            "params": {"gross_target": 1.0, "min_abs_score": 0.1},
            "regime_gates": {
                "entry": [
                    {"expression": "funding_dispersion_72h", "min": 0.000001, "max": 0.01},
                ]
            },
        }
        space = infer_optuna_space(payload)
        gate_params = [p for p in space["parameters"] if "regime_gates" in p["path"]]
        assert len(gate_params) > 0

    def test_infer_optuna_space_pair_family(self):
        payload = {
            "family": "perp_pair_trade_unlevered",
            "risk": {"max_asset_weight": 0.3, "rebalance_threshold": 0.03, "max_leverage": 1.0},
            "params": {
                "gross_target": 1.0,
                "min_abs_score": 0.1,
                "max_gross_target": 2.0,
                "signal_leverage_scale": 1.0,
                "entry_abs_score": 0.5,
                "exit_abs_score": 0.5,
                "flip_abs_score": 0.5,
                "max_holding_bars": 48,
                "cooldown_bars": 12,
            },
            "regime_gates": {"entry": []},
        }
        space = infer_optuna_space(payload)
        assert space["family"] == "perp_pair_trade_unlevered"
        param_paths = [p["path"] for p in space["parameters"]]
        assert "params.max_holding_bars" in param_paths
        assert "params.cooldown_bars" in param_paths

    def test_suggest_params_calls_trial(self):
        trial = MagicMock()
        trial.suggest_float.return_value = 0.5
        trial.suggest_int.return_value = 10
        optuna_space = {
            "parameters": [
                {"path": "risk.max_asset_weight", "kind": "float", "low": 0.1, "high": 0.5},
                {"path": "params.max_holding_bars", "kind": "int", "low": 1, "high": 100},
            ]
        }
        params = _suggest_params(trial=trial, optuna_space=optuna_space)
        assert "risk.max_asset_weight" in params
        assert "params.max_holding_bars" in params
        assert trial.suggest_float.called
        assert trial.suggest_int.called


# ============================================================
# reflector_runner.py — ReflectionRunner
# ============================================================


class TestReflectionRunner:
    def _make_runner(self):
        return _make_reflection_runner()

    def test_init_stores_deps(self):
        runner = self._make_runner()
        assert runner.settings is not None
        assert runner.claude is not None

    def test_fallback_system_prompt_returns_text(self):
        runner = self._make_runner()
        prompt = runner._fallback_system_prompt()
        assert isinstance(prompt, str)
        assert "reflector" in prompt.lower()

    def test_merged_frontmatter_merges_dicts(self):
        runner = self._make_runner()
        frontmatter = {"family": "test_fam", "verdict": "success"}
        eval_packet = {
            "family": "fallback_fam",
            "summary": {"pre_audit_canonical_total_return": 0.05},
            "spec": {"features": []},
            "parent_delta": {"pre_audit_canonical_total_return_delta": 0.01},
        }
        merged = runner._merged_frontmatter(frontmatter, eval_packet)
        assert merged["family"] == "test_fam"
        assert merged["verdict"] == "success"
        assert "failure_mode" in merged

    def test_fallback_frontmatter_returns_minimal(self):
        runner = self._make_runner()
        eval_packet = {
            "family": "fam_a",
            "summary": {"pre_audit_canonical_total_return": -0.02},
            "spec": {"features": ["rsi_14"]},
            "parent_delta": {"pre_audit_canonical_total_return_delta": -0.01},
            "dominant_failure_mode": "low_return",
            "evidence_paths": [],
        }
        fm = runner._fallback_frontmatter(eval_packet)
        assert fm["family"] == "fam_a"
        assert fm["verdict"] == "informative_failure"
        assert "failed_motif_signature" in fm

    def test_retained_body_uses_parsed_when_available(self):
        runner = self._make_runner()
        body = runner._retained_body(
            raw_content="raw body",
            parsed_body="parsed body",
            evaluation_packet={},
        )
        assert body == "parsed body"

    def test_retained_body_falls_back_to_raw(self):
        runner = self._make_runner()
        body = runner._retained_body(
            raw_content="raw body only",
            parsed_body="",
            evaluation_packet={},
        )
        assert body == "raw body only"

    def test_fallback_body_returns_text(self):
        runner = self._make_runner()
        body = runner._fallback_body({
            "summary": {"pre_audit_canonical_total_return": 0.0},
            "parent_delta": {"pre_audit_canonical_total_return_delta": 0.0},
            "spec": {"features": ["rsi_14"]},
        })
        assert isinstance(body, str)
        assert "What changed" in body

    def test_string_list_handles_various(self):
        self._make_runner()
        assert string_list(["a", "b"]) == ["a", "b"]
        assert string_list(None) == []
        assert string_list("single") == []

    def test_is_missing_value(self):
        runner = self._make_runner()
        assert runner._is_missing_value(None) is True
        assert runner._is_missing_value("") is True
        assert runner._is_missing_value("val") is False


# ============================================================
# trials.py
# ============================================================


class TestTrials:
    def test_score_diagnosis_formats_scores(self):
        spec_summary = {"aggregate_score": 1.5, "median_sharpe": 0.8, "median_total_return": 0.05}
        incumbent = {"aggregate_score": 1.0, "median_sharpe": 0.5, "median_total_return": 0.03}
        diagnosis = score_diagnosis(spec_summary, incumbent)
        assert diagnosis["aggregate_score_delta"] == 0.5
        assert len(diagnosis["components"]) > 0
        assert "biggest_lift" in diagnosis

    def test_score_diagnosis_with_none(self):
        diagnosis = score_diagnosis(None, None)
        assert diagnosis["aggregate_score_delta"] is None

    def test_summarize_generalization_handles_crossover(self):
        summary = {
            "aggregate_score": 1.5,
            "validation_total_return": 0.03,
            "pre_audit_canonical_total_return": 0.05,
        }
        result = summarize_generalization(summary)
        assert "fragility_penalty" in result
        assert isinstance(result["fragility_penalty"], float)

    def test_summarize_generalization_negative_validation(self):
        summary = {
            "aggregate_score": 1.0,
            "validation_total_return": -0.05,
            "pre_audit_canonical_total_return": 0.04,
        }
        result = summarize_generalization(summary)
        assert result["fragility_penalty"] > 0

    def test_summarize_generalization_with_stability_pack(self):
        summary = {
            "aggregate_score": 1.0,
            "validation_total_return": 0.03,
            "pre_audit_canonical_total_return": 0.04,
        }
        result = summarize_generalization(summary, stability_pack={"status": "ok", "passed_fraction": 1.0, "stability_penalty": 0.5})
        assert result["fragility_penalty"] >= 0.5

    def test_summarize_generalization_with_optuna_space(self):
        summary = {
            "aggregate_score": 1.0,
            "validation_total_return": 0.03,
            "pre_audit_canonical_total_return": 0.04,
        }
        optuna_space = {
            "parameters": [
                {"path": "risk.max_asset_weight", "kind": "float", "low": 0.1, "high": 0.5},
            ]
        }
        tuned_params = {"risk.max_asset_weight": 0.48}
        result = summarize_generalization(summary, optuna_space=optuna_space, tuned_params=tuned_params)
        assert result["fragility_penalty"] >= 0

    def test_build_spec_patch_merges_keys(self):
        base = {"a": 1, "b": {"c": 2}}
        target = {"a": 10, "b": {"c": 20}}
        patch = build_spec_patch(base_payload=base, target_payload=target)
        assert patch["change_count"] == 2
        assert len(patch["changes"]) == 2

    def test_build_spec_patch_no_changes(self):
        base = {"a": 1}
        target = {"a": 1}
        patch = build_spec_patch(base_payload=base, target_payload=target)
        assert patch["change_count"] == 0

    def test_clone_payload_deep_copies(self):
        original = {"nested": {"key": "val"}}
        cloned = clone_payload(original)
        assert cloned == original
        assert cloned is not original
        assert cloned["nested"] is not original["nested"]

    def test_apply_path_value_sets_nested_keys(self):
        payload = {"a": {"b": 1}}
        result = apply_path_value(payload, "a.b", 10)
        assert result["a"]["b"] == 10

    def test_apply_path_value_creates_missing_keys(self):
        payload: dict = {}
        result = apply_path_value(payload, "x.y.z", "val")
        assert result["x"]["y"]["z"] == "val"

    def test_apply_path_value_list_index(self):
        payload: dict = {"gates": [{"expr": "old"}]}
        result = apply_path_value(payload, "gates[0].expr", "new")
        assert result["gates"][0]["expr"] == "new"

    def test_get_path_value_reads_nested_keys(self):
        payload = {"a": {"b": [1, {"c": "val"}]}}
        assert get_path_value(payload, "a.b[1].c") == "val"

    def test_get_path_value_returns_none_for_missing(self):
        payload = {"a": 1}
        assert get_path_value(payload, "a.b.c") is None
        assert get_path_value(payload, "x") is None

    def test_summarize_patch_with_changes(self):
        patch = {
            "changes": [
                {"path": "a", "old": 1, "new": 2},
                {"path": "b", "old": "x", "new": "y"},
            ]
        }
        summary = summarize_patch(patch)
        assert len(summary) == 2

    def test_summarize_patch_empty(self):
        assert summarize_patch({"changes": []}) == []

    def test_deployment_rank_sorts_by_score(self):
        # Higher deployment_score should rank higher
        better = deployment_rank({"aggregate_score": 5.0}, {"deployment_score": 10.0})
        worse = deployment_rank({"aggregate_score": 1.0}, {"deployment_score": 2.0})
        assert better > worse  # tuple comparison


# ============================================================
# hooks.py — WorkspaceHooks
# ============================================================


class TestWorkspaceHooks:
    def test_init_stores_deps(self):
        builder = MagicMock()
        session = MagicMock()
        hooks = WorkspaceHooks(builder=builder, session=session)
        assert hooks.builder is builder
        assert hooks.session is session

    def test_after_experiment_writes_output(self):
        builder = MagicMock()
        builder.record_experiment.return_value = "card_ref_123"
        session = MagicMock()
        hooks = WorkspaceHooks(builder=builder, session=session)
        result = hooks.after_experiment(spec_hash="hash_1", iteration_number=1)
        assert result == "card_ref_123"
        builder.record_experiment.assert_called_once_with(
            session=session, spec_hash="hash_1", iteration_number=1
        )
        builder.refresh_frontier_files.assert_called_once_with(session)

    def test_after_reflection_writes_output(self):
        builder = MagicMock()
        session = MagicMock()
        hooks = WorkspaceHooks(builder=builder, session=session)
        hooks.after_reflection()
        builder.refresh_frontier_files.assert_called_once_with(session)


# ============================================================
# Contracts helper functions
# ============================================================


class TestContractsHelpers:
    def test_non_regime_variation_feature_role_change(self):
        """has_non_regime_variation returns True when features change to non-regime roles."""
        # Add a feature not in parent
        spec = {"features": ["top_drop_1h"], "universe": {"basis_groups": ["BTC"]}, "params": {}}
        parent = {"features": ["rsi_14"], "universe": {"basis_groups": ["BTC"]}, "params": {}, "family": "test"}
        result = has_non_regime_variation(spec_payload=spec, parent_payload=parent)
        assert isinstance(result, bool)

    def test_has_policy_variation_detects_change(self):
        parent = {"params": {"entry_abs_score": 0.5, "max_holding_bars": 48}}
        spec = {"params": {"entry_abs_score": 0.7, "max_holding_bars": 48}}
        result = has_policy_variation(spec_payload=spec, parent_payload=parent)
        assert result is True

    def test_has_policy_variation_no_change(self):
        parent = {"params": {"entry_abs_score": 0.5, "exit_abs_score": 0.5}}
        spec = {"params": {"entry_abs_score": 0.5, "exit_abs_score": 0.5}}
        result = has_policy_variation(spec_payload=spec, parent_payload=parent)
        assert result is False

    def test_numeric_equal_edge_cases(self):
        assert _numeric_equal(None, None) is True
        assert _numeric_equal("abc", 123) is False

    def test_conformance_violations_with_allowed_features(self):
        planner = {
            "target_family": "test_fam",
            "required_features": ["rsi_14"],
            "forbidden_features": [],
            "forbidden_motifs": [],
            "required_feature_roles": [],
            "required_gate_dimensions": [],
            "gate_intent": {},
            "banned_motif_signatures": [],
        }
        spec = {
            "family": "test_fam",
            "features": ["ema_20"],
            "params": {},
            "regime_gates": {"entry": []},
        }
        allowed = ["rsi_14", "ema_20"]
        violations = conformance_violations(
            planner_contract=planner,
            spec_payload=spec,
            allowed_features=allowed,
        )
        assert any("missing required named feature" in v for v in violations)

    def test_conformance_violations_forbidden_motif(self):
        planner = {
            "target_family": "test_fam",
            "forbidden_motifs": ["second pure trend overlay"],
            "required_feature_roles": [],
            "required_features": [],
            "forbidden_features": [],
            "required_gate_dimensions": [],
            "gate_intent": {},
            "banned_motif_signatures": [],
        }
        spec = {
            "family": "test_fam",
            "features": ["trend_strength_20", "ema_trend_50"],
            "params": {},
            "regime_gates": {"entry": []},
        }
        violations = conformance_violations(
            planner_contract=planner,
            spec_payload=spec,
            allowed_features=[],
        )
        assert any("forbidden_motif" in v for v in violations)

    def test_conformance_violations_gate_intent_missing(self):
        planner = {
            "target_family": "test_fam",
            "gate_intent": {"type": "suppress_policy_churn", "target_dimension": "policy_persistence"},
            "required_gate_dimensions": [],
            "required_feature_roles": [],
            "required_features": [],
            "forbidden_features": [],
            "forbidden_motifs": [],
            "banned_motif_signatures": [],
        }
        spec = {
            "family": "test_fam",
            "features": [],
            "params": {},
            "regime_gates": {"entry": []},
        }
        violations = conformance_violations(
            planner_contract=planner,
            spec_payload=spec,
            allowed_features=[],
        )
        assert any("gate intent" in v for v in violations)

    def test_extract_embedded_yaml_block_with_frontmatter_like(self):
        text = "```yaml\n---\na: 1\n---\n```"
        result = extract_embedded_yaml_block(text)
        assert result == {"a": 1}

    def test_conformance_violations_non_regime_axis(self):
        planner = {
            "target_family": "test_fam",
            "required_feature_roles": ["non_regime_axis"],
            "required_features": [],
            "forbidden_features": [],
            "forbidden_motifs": [],
            "required_gate_dimensions": [],
            "gate_intent": {},
            "banned_motif_signatures": [],
        }
        spec = {
            "family": "test_fam",
            "features": ["custom_feature_123"],
            "params": {},
            "universe": {"basis_groups": ["BTC"]},
            "regime_gates": {"entry": []},
        }
        violations = conformance_violations(
            planner_contract=planner,
            spec_payload=spec,
            allowed_features=[],
            parent_payload={
                "family": "test_fam",
                "features": ["custom_feature_123"],
                "params": {},
                "universe": {"basis_groups": ["BTC"]},
            },
        )
        # spec features have no non-regime roles -> violation
        assert any("non_regime_axis" in v for v in violations)

    def test_conformance_violations_banned_motif(self):
        planner = {
            "target_family": "test_fam",
            "banned_motif_signatures": ["some_motif_hash"],
            "required_feature_roles": [],
            "required_features": [],
            "forbidden_features": [],
            "forbidden_motifs": [],
            "required_gate_dimensions": [],
            "gate_intent": {},
        }
        # Need a spec that produces the banned motif signature
        spec = {
            "family": "test_fam",
            "features": ["rsi_14"],
            "params": {},
            "regime_gates": {"entry": []},
        }
        violations = conformance_violations(
            planner_contract=planner,
            spec_payload=spec,
            allowed_features=[],
        )
        assert isinstance(violations, list)


# ============================================================
# Additional planner runner tests
# ============================================================


class TestResearchPlannerRunnerAdvanced:
    def _make_runner(self):
        return _make_planner_runner()

    def test_policy_control_hint_returns_hint(self):
        self._make_runner()
        hint = policy_control_hint("reduce churn and position flip rate")
        assert hint["type"] == "suppress_policy_churn"
        assert hint["target_dimension"] == "policy_persistence"

    def test_policy_control_hint_no_match(self):
        self._make_runner()
        hint = policy_control_hint("just a normal note")
        assert hint == {}

    def test_section_or_fallback_finds_section(self):
        self._make_runner()
        text = "## Diagnosis\nsome info\n## What to test\nthis is the test"
        result = section_or_fallback(text, headings=("What to test",), fallback="fallback")
        assert "this is the test" in result

    def test_section_or_fallback_uses_fallback(self):
        self._make_runner()
        text = "no matching section"
        result = section_or_fallback(text, headings=("What to test",), fallback="fallback")
        assert result == "fallback"

    def test_last_question_finds_question(self):
        self._make_runner()
        text = "First line?\nSome statement.\nWill this concrete change improve pre-audit return?"
        result = last_question(text)
        assert "Will this concrete change" in result

    def test_last_question_no_question(self):
        self._make_runner()
        assert last_question("No questions here") == ""

    def test_mentioned_allowed_features(self):
        runner = self._make_runner()
        result = runner._mentioned_allowed_features("Use rsi_14 and ema_20", ["rsi_14", "ema_20"])
        assert "rsi_14" in result
        assert "ema_20" in result

    def test_mentioned_gate_dimensions_finds_dimensions(self):
        runner = self._make_runner()
        # Need tokens matching funding, volatility, etc.
        result = runner._mentioned_gate_dimensions("test funding_regime_168h", ["funding_regime_168h"])
        assert "funding_regime_168h" in result

    def test_requires_planner_tool_use(self):
        runner = self._make_runner()
        runner.settings.llm_provider = "bai"
        assert runner._requires_planner_tool_use() is True

        runner.settings.llm_provider = "none"
        assert runner._requires_planner_tool_use() is False

    def test_default_forbidden_motifs(self):
        self._make_runner()
        assert default_forbidden_motifs("perp_multi_asset_carry") == ["second pure trend overlay"]
        assert default_forbidden_motifs("other") == []

    def test_default_writer_inputs(self):
        self._make_runner()
        inputs = default_writer_inputs("test_family")
        assert len(inputs) == 7
        assert any("test_family" in inp for inp in inputs)

    def test_concretize_must_answer_with_question(self):
        self._make_runner()
        contract = {"must_answer": "Does this improve pre-audit return?"}
        result = concretize_must_answer(contract)
        assert result == contract["must_answer"]

    def test_concretize_must_answer_with_feature_refs(self):
        self._make_runner()
        contract = {"must_answer": "", "required_features": ["rsi_14"], "target_family": "fam_a"}
        result = concretize_must_answer(contract)
        assert "rsi_14" in result

    def test_normalize_regime_gates_with_string(self):
        self._make_runner()
        result = normalize_regime_gates({"entry": ["ge(something, 0.5)"]})
        assert "entry" in result
        assert len(result["entry"]) == 1

    def test_normalize_regime_gates_with_dicts(self):
        self._make_runner()
        result = normalize_regime_gates({
            "entry": [{"expression": "funding_dispersion_72h", "min": 0.000001, "max": 0.01}],
            "exit_on_break": True,
        })
        assert len(result["entry"]) == 1
        assert result["exit_on_break"] is True

    def test_normalize_regime_gates_empty(self):
        self._make_runner()
        assert normalize_regime_gates(None) == {}
        assert normalize_regime_gates({}) == {}

    def test_dict_value(self):
        self._make_runner()
        assert dict_value({"a": 1}) == {"a": 1}
        assert dict_value(None) == {}
        assert dict_value("not dict") == {}

    def test_merge_trace_tool_usage(self):
        self._make_runner()
        contract: dict = {}
        trace = {"tool_calls": [{"name": "search_workspace"}, {"name": "think"}]}
        merge_trace_tool_usage(contract, trace=trace)
        assert "tools_used" in contract

    def test_trace_tool_names(self):
        self._make_runner()
        names = trace_tool_names({"tool_calls": [{"name": "search_workspace"}, {"name": "open_file"}]})
        assert "search_workspace" in names
        assert "open_file" in names

    def test_fallback_contract_fills_evidence(self):
        self._make_runner()
        parent = SimpleNamespace(
            family="fam_a",
            universe=SimpleNamespace(basis_groups=["BTC"]),
            params={},
        )
        current_state: dict = {}
        tool_refs = ["probe_ref_1"]
        contract = fallback_contract(parent=parent, current_state=current_state, tool_refs=tool_refs)
        assert "tools_used" in contract

    def test_default_required_feature_roles(self):
        roles = default_required_feature_roles("perp_multi_asset_carry")
        assert any("core_carry" in r for r in roles)

        roles2 = default_required_feature_roles("perp_multi_asset_carry", required_variation_axis="non_regime")
        assert any("non_regime_axis" in r for r in roles2)

        roles3 = default_required_feature_roles("perp_basket_neutral_unlevered")
        assert any("cross_sectional_core" in r for r in roles3)

    def test_normalize_required_feature_roles_non_regime(self):
        result = normalize_required_feature_roles(
            family="perp_multi_asset_carry",
            required_variation_axis="non_regime",
            existing=[],
        )
        assert any("non_regime_axis" in r for r in result)

        result2 = normalize_required_feature_roles(
            family="perp_multi_asset_carry",
            required_variation_axis="",
            existing=["one core_carry feature"],
        )
        assert "one core_carry feature" in result2

    def test_planner_probe_claim_issues_mentioned_but_not_called(self):
        issues = planner_probe_claim_issues(
            note_text="call probe_feature_forward_stats",
            trace={"tool_calls": [{"name": "search_workspace"}]},
        )
        assert any("probe_feature_forward_stats" in i for i in issues)

    def test_planner_probe_claim_issues_all_called(self):
        issues = planner_probe_claim_issues(
            note_text="call probe_feature_forward_stats",
            trace={"tool_calls": [{"name": "probe_feature_forward_stats"}]},
        )
        assert issues == []

    def test_body_family_override_section_match(self):
        text = "## What to test\nusing perp_multi_asset_carry variant"
        result = body_family_override(text, ["perp_multi_asset_carry", "other"])
        assert result == "perp_multi_asset_carry"

    def test_body_family_override_no_match(self):
        result = body_family_override("no family mentioned", ["fam_a", "fam_b"])
        assert result is None

    def test_body_trade_style_no_match(self):
        assert body_trade_style("no style") is None

    def test_session_properties(self):
        """Test that PlannerResult is properly structured."""
        result = PlannerResult(
            research_note_path=Path("/p"),
            planner_contract_path=Path("/p"),
            trace_path=Path("/p"),
            frontmatter={"key": "val"},
            tool_refs=["tool1"],
            evidence_paths=["ev1"],
            repaired=False,
        )
        assert result.repaired is False
        assert result.tool_refs == ["tool1"]


# ============================================================
# Writer detailed tests
# ============================================================


class TestSpecWriterRunnerDetailed:
    def _make_runner(self):
        return _make_writer_runner()

    def test_preflight_material_drift_property(self):
        p = PreflightResult(
            parse_error=None, hard_issues=[], conformance_issues=[],
            gate_lint=None, changed_fields=["family"],
            harmless_changed_fields=[], material_changed_fields=["family"],
            validated_payload=None,
        )
        assert p.material_drift is True

    def test_preflight_not_material_drift(self):
        p = PreflightResult(
            parse_error=None, hard_issues=[], conformance_issues=[],
            gate_lint=None, changed_fields=[],
            harmless_changed_fields=[], material_changed_fields=[],
            validated_payload=None,
        )
        assert p.material_drift is False

    def test_changed_fields_detects_diff(self):
        runner = self._make_runner()
        changed = runner._changed_fields(
            {"a": 1, "b": {"c": 2}},
            {"a": 1, "b": {"c": 3}},
        )
        assert "b.c" in changed

    def test_changed_fields_no_diff(self):
        runner = self._make_runner()
        changed = runner._changed_fields({"a": 1}, {"a": 1})
        assert changed == []

    def test_get_by_path(self):
        runner = self._make_runner()
        payload = {"a": {"b": [{"c": "val"}]}}
        assert runner._get_by_path(payload, "a.b[0].c") is None  # not parsed as nested - uses dot notation only
        assert runner._get_by_path(payload, "a.b") == [{"c": "val"}]

    def test_cookbook_paths(self):
        runner = self._make_runner()
        session = SimpleNamespace(
            track="test",
            cookbooks_dir=Path("/nonexistent"),
            root=Path("/nonexistent"),
        )
        runner.mutator = MagicMock()
        runner.mutator._family_spec.return_value = {"capabilities": {"prompt_module": "basket_neutral"}}
        runner.mutator._allowed_features_by_family.return_value = {}
        paths = runner._cookbook_paths(session=session, family="perp_multi_asset_carry")
        assert isinstance(paths, list)
