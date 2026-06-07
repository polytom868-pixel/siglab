from __future__ import annotations

import unittest
from unittest.mock import patch

from siglab.llm.policy import LLMRoutingPolicy, ModelHealth


class MockSettings:
    def __init__(self, **kwargs: object) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)


class ModelHealthTests(unittest.TestCase):
    def test_default_construction(self) -> None:
        health = ModelHealth()
        self.assertEqual(health.unavailable, set())
        self.assertEqual(health.quota_blocked, set())
        self.assertEqual(health.latency_demoted, set())
        self.assertEqual(health.recent_errors, {})


class LLMRoutingPolicyConstructionTests(unittest.TestCase):
    def test_constructs_with_settings(self) -> None:
        settings = MockSettings()
        policy = LLMRoutingPolicy(settings)
        self.assertIs(policy.settings, settings)
        self.assertIsInstance(policy.health, ModelHealth)
        self.assertEqual(policy.health.unavailable, set())

    def test_latency_demote_ms_constant(self) -> None:
        self.assertEqual(LLMRoutingPolicy.LATENCY_DEMOTE_MS, 10_000.0)


class LLMRoutingPolicyModelForStageTests(unittest.TestCase):
    def test_non_bai_provider_calls_resolve_llm_model(self) -> None:
        settings = MockSettings(llm_provider="claude", claude_model="claude-sonnet-4-6")
        policy = LLMRoutingPolicy(settings)
        with patch("siglab.llm.policy.resolve_llm_model", return_value="claude-sonnet-4-6") as mock_resolve:
            model = policy.model_for_stage(provider="claude", stage="planner")
            mock_resolve.assert_called_once_with(
                settings, provider="claude", thinking_override=None
            )
            self.assertEqual(model, "claude-sonnet-4-6")

    def test_non_bai_passes_thinking_override(self) -> None:
        settings = MockSettings(llm_provider="deepseek", deepseek_model="deepseek-reasoner")
        policy = LLMRoutingPolicy(settings)
        with patch("siglab.llm.policy.resolve_llm_model", return_value="deepseek-reasoner") as mock_resolve:
            model = policy.model_for_stage(provider="deepseek", stage="planner", thinking_override="enabled")
            mock_resolve.assert_called_once_with(
                settings, provider="deepseek", thinking_override="enabled"
            )
            self.assertEqual(model, "deepseek-reasoner")

    def test_bai_planner_uses_planner_model(self) -> None:
        settings = MockSettings(bai_planner_model="deepseek-reasoner", bai_model="deepseek-v4-flash")
        policy = LLMRoutingPolicy(settings)
        model = policy.model_for_stage(provider="bai", stage="planner")
        self.assertEqual(model, "deepseek-reasoner")

    def test_bai_planner_falls_back_to_bai_model(self) -> None:
        settings = MockSettings(bai_model="deepseek-v4-flash")
        policy = LLMRoutingPolicy(settings)
        model = policy.model_for_stage(provider="bai", stage="planner")
        self.assertEqual(model, "deepseek-v4-flash")

    def test_bai_planner_deep_fallback_to_hardcoded_default(self) -> None:
        settings = MockSettings()
        policy = LLMRoutingPolicy(settings)
        model = policy.model_for_stage(provider="bai", stage="planner")
        self.assertEqual(model, "deepseek-v4-flash")

    def test_bai_writer_uses_writer_model(self) -> None:
        settings = MockSettings(bai_writer_model="kimi-k2.5", bai_model="deepseek-v4-flash")
        policy = LLMRoutingPolicy(settings)
        model = policy.model_for_stage(provider="bai", stage="writer")
        self.assertEqual(model, "kimi-k2.5")

    def test_bai_writer_falls_back_to_bai_model(self) -> None:
        settings = MockSettings(bai_model="deepseek-v4-flash")
        policy = LLMRoutingPolicy(settings)
        model = policy.model_for_stage(provider="bai", stage="writer")
        self.assertEqual(model, "deepseek-v4-flash")

    def test_bai_reflector_uses_reflector_model(self) -> None:
        settings = MockSettings(
            bai_reflector_model="kimi-k2.5", bai_fallback_fast_model="deepseek-chat"
        )
        policy = LLMRoutingPolicy(settings)
        model = policy.model_for_stage(provider="bai", stage="reflector")
        self.assertEqual(model, "kimi-k2.5")

    def test_bai_reflector_falls_back_to_fallback_fast(self) -> None:
        settings = MockSettings(bai_fallback_fast_model="deepseek-chat")
        policy = LLMRoutingPolicy(settings)
        model = policy.model_for_stage(provider="bai", stage="reflector")
        self.assertEqual(model, "deepseek-chat")

    def test_bai_reflector_deep_fallback_to_hardcoded_default(self) -> None:
        settings = MockSettings()
        policy = LLMRoutingPolicy(settings)
        model = policy.model_for_stage(provider="bai", stage="reflector")
        self.assertEqual(model, "kimi-k2.5")

    def test_bai_benchmark_uses_writer_model(self) -> None:
        settings = MockSettings(bai_writer_model="kimi-k2.5", bai_model="deepseek-v4-flash")
        policy = LLMRoutingPolicy(settings)
        model = policy.model_for_stage(provider="bai", stage="benchmark")
        self.assertEqual(model, "kimi-k2.5")

    def test_bai_benchmark_falls_back_to_bai_model(self) -> None:
        settings = MockSettings(bai_model="deepseek-v4-flash")
        policy = LLMRoutingPolicy(settings)
        model = policy.model_for_stage(provider="bai", stage="benchmark")
        self.assertEqual(model, "deepseek-v4-flash")

    def test_bai_default_when_stage_is_none(self) -> None:
        settings = MockSettings(bai_model="deepseek-v4-flash")
        policy = LLMRoutingPolicy(settings)
        model = policy.model_for_stage(provider="bai", stage=None)
        self.assertEqual(model, "deepseek-v4-flash")

    def test_bai_default_when_stage_is_unknown(self) -> None:
        settings = MockSettings(bai_model="deepseek-v4-flash")
        policy = LLMRoutingPolicy(settings)
        model = policy.model_for_stage(provider="bai", stage="unknown_stage")
        self.assertEqual(model, "deepseek-v4-flash")

    def test_bai_default_deep_fallback_to_hardcoded(self) -> None:
        settings = MockSettings()
        policy = LLMRoutingPolicy(settings)
        model = policy.model_for_stage(provider="bai", stage=None)
        self.assertEqual(model, "deepseek-v4-flash")

    def test_bai_stage_is_case_insensitive(self) -> None:
        settings = MockSettings(bai_planner_model="deepseek-reasoner")
        policy = LLMRoutingPolicy(settings)
        model = policy.model_for_stage(provider="bai", stage="PLANNER")
        self.assertEqual(model, "deepseek-reasoner")

    def test_bai_stage_strips_whitespace(self) -> None:
        settings = MockSettings(bai_planner_model="deepseek-reasoner")
        policy = LLMRoutingPolicy(settings)
        model = policy.model_for_stage(provider="bai", stage="  planner  ")
        self.assertEqual(model, "deepseek-reasoner")


class LLMRoutingPolicyCandidatesTests(unittest.TestCase):
    def test_non_bai_returns_single_primary(self) -> None:
        settings = MockSettings()
        policy = LLMRoutingPolicy(settings)
        candidates = policy.candidates(provider="claude", stage="planner", primary="claude-sonnet-4-6")
        self.assertEqual(candidates, ["claude-sonnet-4-6"])

    def test_bai_returns_ordered_viable_list(self) -> None:
        settings = MockSettings(
            bai_fallback_fast_model="deepseek-chat",
            bai_fallback_reasoning_model="deepseek-reasoner",
            bai_model="deepseek-v4-flash",
        )
        policy = LLMRoutingPolicy(settings)
        candidates = policy.candidates(provider="bai", stage="planner", primary="kimi-k2.5")
        self.assertEqual(
            candidates, ["kimi-k2.5", "deepseek-chat", "deepseek-reasoner", "deepseek-v4-flash"]
        )

    def test_bai_deduplicates_identical_models(self) -> None:
        settings = MockSettings(
            bai_fallback_fast_model="kimi-k2.5",
            bai_fallback_reasoning_model="kimi-k2.5",
            bai_model="deepseek-v4-flash",
        )
        policy = LLMRoutingPolicy(settings)
        candidates = policy.candidates(provider="bai", stage="planner", primary="kimi-k2.5")
        self.assertEqual(candidates, ["kimi-k2.5", "deepseek-v4-flash"])

    def test_bai_handles_empty_fallback_settings(self) -> None:
        settings = MockSettings()
        policy = LLMRoutingPolicy(settings)
        candidates = policy.candidates(provider="bai", stage="planner", primary="kimi-k2.5")
        self.assertEqual(candidates, ["kimi-k2.5"])

    def test_bai_filters_unavailable_models(self) -> None:
        settings = MockSettings(
            bai_fallback_fast_model="deepseek-chat",
            bai_fallback_reasoning_model="deepseek-reasoner",
            bai_model="deepseek-v4-flash",
        )
        policy = LLMRoutingPolicy(settings)
        policy.mark_auth_failure("deepseek-chat", "AuthError")
        candidates = policy.candidates(provider="bai", stage="planner", primary="kimi-k2.5")
        self.assertNotIn("deepseek-chat", candidates)
        self.assertIn("kimi-k2.5", candidates)

    def test_bai_filters_quota_blocked_models(self) -> None:
        settings = MockSettings(
            bai_fallback_fast_model="deepseek-chat",
            bai_fallback_reasoning_model="deepseek-reasoner",
            bai_model="deepseek-v4-flash",
        )
        policy = LLMRoutingPolicy(settings)
        policy.mark_quota_failure("deepseek-reasoner", "QuotaExceeded")
        candidates = policy.candidates(provider="bai", stage="planner", primary="kimi-k2.5")
        self.assertNotIn("deepseek-reasoner", candidates)
        self.assertIn("deepseek-chat", candidates)

    def test_bai_filters_latency_demoted_for_sensitive_stages(self) -> None:
        settings = MockSettings(
            bai_fallback_fast_model="deepseek-chat",
            bai_fallback_reasoning_model="deepseek-reasoner",
            bai_model="deepseek-v4-flash",
        )
        policy = LLMRoutingPolicy(settings)
        policy.record_latency(model="deepseek-chat", stage="writer", elapsed_ms=15_000.0)
        candidates = policy.candidates(provider="bai", stage="writer", primary="kimi-k2.5")
        self.assertNotIn("deepseek-chat", candidates)

    def test_bai_does_not_filter_latency_demoted_for_insensitive_stages(self) -> None:
        settings = MockSettings(
            bai_fallback_fast_model="deepseek-chat",
            bai_fallback_reasoning_model="deepseek-reasoner",
            bai_model="deepseek-v4-flash",
        )
        policy = LLMRoutingPolicy(settings)
        policy.record_latency(model="deepseek-chat", stage="writer", elapsed_ms=15_000.0)
        candidates = policy.candidates(provider="bai", stage="planner", primary="kimi-k2.5")
        self.assertIn("deepseek-chat", candidates)

    def test_bai_allows_latency_demoted_when_no_other_viable_sensitive(self) -> None:
        settings = MockSettings(
            bai_fallback_fast_model="deepseek-chat",
            bai_model="deepseek-v4-flash",
        )
        policy = LLMRoutingPolicy(settings)
        policy.record_latency(model="deepseek-chat", stage="writer", elapsed_ms=15_000.0)
        policy.mark_auth_failure("kimi-k2.5", "AuthError")
        policy.mark_quota_failure("deepseek-v4-flash", "Quota")
        candidates = policy.candidates(provider="bai", stage="writer", primary="kimi-k2.5")
        self.assertEqual(candidates, ["deepseek-chat"])

    def test_bai_returns_empty_when_no_viable_and_not_latency_sensitive(self) -> None:
        settings = MockSettings(
            bai_fallback_fast_model="deepseek-chat",
            bai_fallback_reasoning_model="deepseek-reasoner",
            bai_model="deepseek-v4-flash",
        )
        policy = LLMRoutingPolicy(settings)
        for model in ("kimi-k2.5", "deepseek-chat", "deepseek-reasoner", "deepseek-v4-flash"):
            policy.mark_auth_failure(model, "AuthError")
        candidates = policy.candidates(provider="bai", stage="planner", primary="kimi-k2.5")
        self.assertEqual(candidates, [])


class LLMRoutingPolicyMarkAuthFailureTests(unittest.TestCase):
    def test_adds_model_to_unavailable(self) -> None:
        settings = MockSettings()
        policy = LLMRoutingPolicy(settings)
        policy.mark_auth_failure("deepseek-chat", "AuthenticationError")
        self.assertIn("deepseek-chat", policy.health.unavailable)

    def test_records_error_class(self) -> None:
        settings = MockSettings()
        policy = LLMRoutingPolicy(settings)
        policy.mark_auth_failure("deepseek-chat", "AuthenticationError")
        self.assertEqual(policy.health.recent_errors["deepseek-chat"], "AuthenticationError")

    def test_multiple_auth_failures(self) -> None:
        settings = MockSettings()
        policy = LLMRoutingPolicy(settings)
        policy.mark_auth_failure("model-a", "ErrA")
        policy.mark_auth_failure("model-b", "ErrB")
        self.assertIn("model-a", policy.health.unavailable)
        self.assertIn("model-b", policy.health.unavailable)


class LLMRoutingPolicyMarkQuotaFailureTests(unittest.TestCase):
    def test_adds_model_to_quota_blocked(self) -> None:
        settings = MockSettings()
        policy = LLMRoutingPolicy(settings)
        policy.mark_quota_failure("deepseek-reasoner", "QuotaExceeded")
        self.assertIn("deepseek-reasoner", policy.health.quota_blocked)

    def test_records_error_class(self) -> None:
        settings = MockSettings()
        policy = LLMRoutingPolicy(settings)
        policy.mark_quota_failure("model-x", "RateLimitError")
        self.assertEqual(policy.health.recent_errors["model-x"], "RateLimitError")


class LLMRoutingPolicyRecordLatencyTests(unittest.TestCase):
    def test_demotes_writer_stage_above_threshold(self) -> None:
        settings = MockSettings()
        policy = LLMRoutingPolicy(settings)
        policy.record_latency(model="slow-model", stage="writer", elapsed_ms=12_000.0)
        self.assertIn("slow-model", policy.health.latency_demoted)
        self.assertEqual(policy.health.recent_errors["slow-model"], "LLMLatencyDemoted")

    def test_demotes_reflector_stage_above_threshold(self) -> None:
        settings = MockSettings()
        policy = LLMRoutingPolicy(settings)
        policy.record_latency(model="slow-model", stage="reflector", elapsed_ms=15_000.0)
        self.assertIn("slow-model", policy.health.latency_demoted)

    def test_does_not_demote_below_threshold(self) -> None:
        settings = MockSettings()
        policy = LLMRoutingPolicy(settings)
        policy.record_latency(model="fast-model", stage="writer", elapsed_ms=5_000.0)
        self.assertNotIn("fast-model", policy.health.latency_demoted)

    def test_does_not_demote_at_exact_threshold(self) -> None:
        settings = MockSettings()
        policy = LLMRoutingPolicy(settings)
        policy.record_latency(model="edge-model", stage="writer", elapsed_ms=10_000.0)
        self.assertNotIn("edge-model", policy.health.latency_demoted)

    def test_does_not_demote_non_sensitive_stage(self) -> None:
        settings = MockSettings()
        policy = LLMRoutingPolicy(settings)
        policy.record_latency(model="slow-model", stage="planner", elapsed_ms=20_000.0)
        self.assertNotIn("slow-model", policy.health.latency_demoted)

    def test_does_not_demote_for_none_stage(self) -> None:
        settings = MockSettings()
        policy = LLMRoutingPolicy(settings)
        policy.record_latency(model="slow-model", stage=None, elapsed_ms=20_000.0)
        self.assertNotIn("slow-model", policy.health.latency_demoted)


class LLMRoutingPolicySnapshotTests(unittest.TestCase):
    def test_returns_expected_structure(self) -> None:
        settings = MockSettings()
        policy = LLMRoutingPolicy(settings)
        policy.mark_auth_failure("model-a", "AuthError")
        policy.mark_quota_failure("model-b", "QuotaError")
        policy.record_latency(model="model-c", stage="writer", elapsed_ms=12_000.0)

        snap = policy.snapshot()
        self.assertEqual(snap["unavailable"], ["model-a"])
        self.assertEqual(snap["quota_blocked"], ["model-b"])
        self.assertEqual(snap["latency_demoted"], ["model-c"])
        self.assertEqual(snap["recent_errors"]["model-a"], "AuthError")
        self.assertEqual(snap["recent_errors"]["model-b"], "QuotaError")
        self.assertEqual(snap["recent_errors"]["model-c"], "LLMLatencyDemoted")

    def test_empty_snapshot_when_no_health_issues(self) -> None:
        settings = MockSettings()
        policy = LLMRoutingPolicy(settings)
        snap = policy.snapshot()
        self.assertEqual(snap["unavailable"], [])
        self.assertEqual(snap["quota_blocked"], [])
        self.assertEqual(snap["latency_demoted"], [])
        self.assertEqual(snap["recent_errors"], {})

    def test_snapshot_returns_sorted_lists(self) -> None:
        settings = MockSettings()
        policy = LLMRoutingPolicy(settings)
        policy.mark_auth_failure("z-model", "Err")
        policy.mark_auth_failure("a-model", "Err")
        snap = policy.snapshot()
        self.assertEqual(snap["unavailable"], ["a-model", "z-model"])


class LLMRoutingPolicyIntegrationTests(unittest.TestCase):
    def test_health_state_reduces_candidates_over_multiple_failures(self) -> None:
        settings = MockSettings(
            bai_fallback_fast_model="deepseek-chat",
            bai_fallback_reasoning_model="deepseek-reasoner",
            bai_model="deepseek-v4-flash",
        )
        policy = LLMRoutingPolicy(settings)

        all_candidates = policy.candidates(provider="bai", stage="planner", primary="kimi-k2.5")
        self.assertEqual(len(all_candidates), 4)

        policy.mark_auth_failure("deepseek-chat", "AuthError")
        after_auth = policy.candidates(provider="bai", stage="planner", primary="kimi-k2.5")
        self.assertEqual(len(after_auth), 3)
        self.assertNotIn("deepseek-chat", after_auth)

        policy.mark_quota_failure("deepseek-reasoner", "Quota")
        after_quota = policy.candidates(provider="bai", stage="planner", primary="kimi-k2.5")
        self.assertEqual(len(after_quota), 2)

        policy.mark_auth_failure("kimi-k2.5", "AuthError")
        after_all = policy.candidates(provider="bai", stage="planner", primary="kimi-k2.5")
        self.assertEqual(after_all, ["deepseek-v4-flash"])

        snap = policy.snapshot()
        self.assertEqual(set(snap["unavailable"]), {"kimi-k2.5", "deepseek-chat"})
        self.assertEqual(snap["quota_blocked"], ["deepseek-reasoner"])

    def test_latency_then_failure_fallthrough(self) -> None:
        settings = MockSettings(
            bai_fallback_fast_model="deepseek-chat",
            bai_fallback_reasoning_model="deepseek-reasoner",
            bai_model="deepseek-v4-flash",
        )
        policy = LLMRoutingPolicy(settings)

        policy.record_latency(model="kimi-k2.5", stage="writer", elapsed_ms=15_000.0)
        candidates = policy.candidates(provider="bai", stage="writer", primary="kimi-k2.5")
        self.assertNotIn("kimi-k2.5", candidates)

        policy.mark_auth_failure("deepseek-chat", "AuthError")
        candidates2 = policy.candidates(provider="bai", stage="writer", primary="kimi-k2.5")
        self.assertNotIn("deepseek-chat", candidates2)

        policy.mark_auth_failure("deepseek-reasoner", "AuthError")
        policy.mark_quota_failure("deepseek-v4-flash", "Quota")
        candidates3 = policy.candidates(provider="bai", stage="writer", primary="kimi-k2.5")
        self.assertEqual(candidates3, ["kimi-k2.5"])


if __name__ == "__main__":
    unittest.main()
