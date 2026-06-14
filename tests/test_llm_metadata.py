import unittest

from siglab.llm_metadata import (
    SUPPORTED_LLM_PROVIDERS,
    _normalize_bai_model,
    default_llm_model_display,
    infer_llm_provider,
    normalize_llm_provider,
    resolve_llm_api_key,
    resolve_llm_base_url,
    resolve_llm_model,
    resolve_llm_provider,
    resolve_llm_thinking_mode,
)


class MockSettings:
    def __init__(self, **kwargs: object) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)


class SupportedProvidersTests(unittest.TestCase):
    def test_contains_expected_providers(self) -> None:
        self.assertIn("claude", SUPPORTED_LLM_PROVIDERS)
        self.assertIn("deepseek", SUPPORTED_LLM_PROVIDERS)
        self.assertIn("openrouter", SUPPORTED_LLM_PROVIDERS)
        self.assertIn("bai", SUPPORTED_LLM_PROVIDERS)

    def test_is_frozenset(self) -> None:
        self.assertIsInstance(SUPPORTED_LLM_PROVIDERS, frozenset)


class NormalizeLlmProviderTests(unittest.TestCase):
    def test_recognizes_claude(self) -> None:
        self.assertEqual(normalize_llm_provider("claude"), "claude")

    def test_recognizes_deepseek(self) -> None:
        self.assertEqual(normalize_llm_provider("deepseek"), "deepseek")

    def test_recognizes_openrouter(self) -> None:
        self.assertEqual(normalize_llm_provider("openrouter"), "openrouter")

    def test_recognizes_bai(self) -> None:
        self.assertEqual(normalize_llm_provider("bai"), "bai")

    def test_is_case_insensitive(self) -> None:
        self.assertEqual(normalize_llm_provider("Claude"), "claude")
        self.assertEqual(normalize_llm_provider("DEEPSEEK"), "deepseek")
        self.assertEqual(normalize_llm_provider("OpenRouter"), "openrouter")
        self.assertEqual(normalize_llm_provider("BAI"), "bai")

    def test_strips_whitespace(self) -> None:
        self.assertEqual(normalize_llm_provider("  claude  "), "claude")

    def test_returns_none_for_unknown_provider(self) -> None:
        self.assertIsNone(normalize_llm_provider("unknown"))

    def test_returns_none_for_empty_string(self) -> None:
        self.assertIsNone(normalize_llm_provider(""))

    def test_returns_none_for_none(self) -> None:
        self.assertIsNone(normalize_llm_provider(None))


class ResolveLlmProviderTests(unittest.TestCase):
    def test_uses_explicit_provider(self) -> None:
        settings = MockSettings(llm_provider="deepseek")
        self.assertEqual(resolve_llm_provider(settings), "deepseek")

    def test_explicit_provider_wins_over_keys(self) -> None:
        settings = MockSettings(llm_provider="bai", claude_api_key="sk-ant-test")
        self.assertEqual(resolve_llm_provider(settings), "bai")

    def test_detects_claude_from_claude_api_key(self) -> None:
        settings = MockSettings(
            claude_api_key="sk-ant-test",
            bai_api_key=None,
            deepseek_api_key=None,
            openrouter_api_key=None,
        )
        self.assertEqual(resolve_llm_provider(settings), "claude")

    def test_bai_takes_priority_over_deepseek(self) -> None:
        settings = MockSettings(
            claude_api_key=None,
            bai_api_key="bai-key-test",
            deepseek_api_key="sk-ds-test",
        )
        self.assertEqual(resolve_llm_provider(settings), "bai")

    def test_deepseek_from_api_key(self) -> None:
        settings = MockSettings(
            claude_api_key=None,
            bai_api_key=None,
            deepseek_api_key="sk-ds-test",
            openrouter_api_key=None,
        )
        self.assertEqual(resolve_llm_provider(settings), "deepseek")

    def test_openrouter_from_api_key(self) -> None:
        settings = MockSettings(
            claude_api_key=None,
            bai_api_key=None,
            deepseek_api_key=None,
            openrouter_api_key="or-test",
        )
        self.assertEqual(resolve_llm_provider(settings), "openrouter")

    def test_defaults_to_claude_when_no_keys(self) -> None:
        settings = MockSettings(llm_provider=None)
        self.assertEqual(resolve_llm_provider(settings), "claude")


class InferLlmProviderTests(unittest.TestCase):
    def test_deepseek_prefix(self) -> None:
        self.assertEqual(infer_llm_provider("deepseek-reasoner"), "deepseek")

    def test_openrouter_has_slash_in_name(self) -> None:
        self.assertEqual(infer_llm_provider("openai/gpt-4o"), "openrouter")

    def test_anthropic_slash_returns_openrouter(self) -> None:
        self.assertEqual(infer_llm_provider("anthropic/claude-sonnet-4"), "openrouter")

    def test_claude_model_returns_claude(self) -> None:
        self.assertEqual(infer_llm_provider("claude-sonnet-4-6"), "claude")

    def test_deepseek_case_insensitive(self) -> None:
        self.assertEqual(infer_llm_provider("DeepSeek-Reasoner"), "deepseek")

    def test_returns_none_for_none(self) -> None:
        self.assertIsNone(infer_llm_provider(None))

    def test_returns_none_for_empty_string(self) -> None:
        self.assertIsNone(infer_llm_provider(""))


class ResolveLlmThinkingModeTests(unittest.TestCase):
    def test_returns_override_when_provided(self) -> None:
        settings = MockSettings()
        result = resolve_llm_thinking_mode(settings, provider="claude", override="enabled")
        self.assertEqual(result, "enabled")

    def test_override_strips_and_lowers_case(self) -> None:
        settings = MockSettings()
        result = resolve_llm_thinking_mode(settings, provider="claude", override="  ENABLED  ")
        self.assertEqual(result, "enabled")

    def test_claude_thinking_from_settings(self) -> None:
        settings = MockSettings(claude_thinking="enabled")
        result = resolve_llm_thinking_mode(settings, provider="claude")
        self.assertEqual(result, "enabled")

    def test_claude_thinking_defaults_to_empty(self) -> None:
        settings = MockSettings()
        result = resolve_llm_thinking_mode(settings, provider="claude")
        self.assertEqual(result, "")

    def test_deepseek_reasoner_returns_enabled(self) -> None:
        settings = MockSettings(deepseek_model="deepseek-reasoner")
        result = resolve_llm_thinking_mode(settings, provider="deepseek")
        self.assertEqual(result, "enabled")

    def test_deepseek_chat_returns_disabled(self) -> None:
        settings = MockSettings(deepseek_model="deepseek-chat")
        result = resolve_llm_thinking_mode(settings, provider="deepseek")
        self.assertEqual(result, "disabled")

    def test_deepseek_unknown_model_returns_empty(self) -> None:
        settings = MockSettings(deepseek_model="deepseek-v3")
        result = resolve_llm_thinking_mode(settings, provider="deepseek")
        self.assertEqual(result, "")

    def test_bai_returns_empty(self) -> None:
        settings = MockSettings()
        result = resolve_llm_thinking_mode(settings, provider="bai")
        self.assertEqual(result, "")

    def test_openrouter_returns_empty(self) -> None:
        settings = MockSettings()
        result = resolve_llm_thinking_mode(settings, provider="openrouter")
        self.assertEqual(result, "")

    def test_resolves_provider_when_none_given(self) -> None:
        settings = MockSettings(claude_api_key="sk-ant-test", claude_thinking="enabled")
        result = resolve_llm_thinking_mode(settings, provider=None)
        self.assertEqual(result, "enabled")

    def test_override_takes_precedence_over_everything(self) -> None:
        settings = MockSettings(claude_thinking="disabled")
        result = resolve_llm_thinking_mode(settings, provider="claude", override="enabled")
        self.assertEqual(result, "enabled")


class ResolveLlmModelTests(unittest.TestCase):
    def test_deepseek_reasoner_default(self) -> None:
        settings = MockSettings(llm_provider="deepseek", deepseek_model="deepseek-reasoner")
        model = resolve_llm_model(settings, provider="deepseek")
        self.assertEqual(model, "deepseek-reasoner")

    def test_deepseek_chat_default(self) -> None:
        settings = MockSettings(llm_provider="deepseek", deepseek_model="deepseek-chat")
        model = resolve_llm_model(settings, provider="deepseek")
        self.assertEqual(model, "deepseek-chat")

    def test_deepseek_thinking_enabled_returns_reasoner(self) -> None:
        settings = MockSettings(llm_provider="deepseek", deepseek_model="deepseek-reasoner")
        model = resolve_llm_model(settings, provider="deepseek", thinking_override="enabled")
        self.assertEqual(model, "deepseek-reasoner")

    def test_deepseek_thinking_disabled_switches_to_chat(self) -> None:
        settings = MockSettings(llm_provider="deepseek", deepseek_model="deepseek-reasoner")
        model = resolve_llm_model(settings, provider="deepseek", thinking_override="disabled")
        self.assertEqual(model, "deepseek-chat")

    def test_deepseek_non_standard_model_passthrough(self) -> None:
        settings = MockSettings(llm_provider="deepseek", deepseek_model="deepseek-v3")
        model = resolve_llm_model(settings, provider="deepseek")
        self.assertEqual(model, "deepseek-v3")

    def test_openrouter_reasoning_with_thinking_enabled(self) -> None:
        settings = MockSettings(
            llm_provider="openrouter",
            openrouter_reasoning_model="openai/o3",
            openrouter_fast_model="openai/gpt-4o-mini",
            openrouter_model="openai/gpt-4.1-mini",
        )
        model = resolve_llm_model(settings, provider="openrouter", thinking_override="enabled")
        self.assertEqual(model, "openai/o3")

    def test_openrouter_fast_with_thinking_disabled(self) -> None:
        settings = MockSettings(
            llm_provider="openrouter",
            openrouter_reasoning_model="openai/o3",
            openrouter_fast_model="openai/gpt-4o-mini",
            openrouter_model="openai/gpt-4.1-mini",
        )
        model = resolve_llm_model(settings, provider="openrouter", thinking_override="disabled")
        self.assertEqual(model, "openai/gpt-4o-mini")

    def test_openrouter_only_legacy_model(self) -> None:
        settings = MockSettings(llm_provider="openrouter", openrouter_model="openai/gpt-4o")
        model = resolve_llm_model(settings, provider="openrouter")
        self.assertEqual(model, "openai/gpt-4o")

    def test_openrouter_uses_reasoning_when_both_different(self) -> None:
        settings = MockSettings(
            llm_provider="openrouter",
            openrouter_reasoning_model="openai/o3",
            openrouter_fast_model="openai/gpt-4o-mini",
            openrouter_model="openai/gpt-4.1-mini",
        )
        model = resolve_llm_model(settings, provider="openrouter")
        self.assertEqual(model, "openai/o3")

    def test_openrouter_reasoning_and_fast_same_falls_to_legacy(self) -> None:
        settings = MockSettings(
            llm_provider="openrouter",
            openrouter_reasoning_model="openai/gpt-4o-mini",
            openrouter_fast_model="openai/gpt-4o-mini",
            openrouter_model="openai/gpt-4.1-mini",
        )
        model = resolve_llm_model(settings, provider="openrouter")
        self.assertEqual(model, "openai/gpt-4o-mini")

    def test_bai_returns_normalized_model(self) -> None:
        settings = MockSettings(llm_provider="bai", bai_model="deepseek-v4-flash")
        model = resolve_llm_model(settings, provider="bai")
        self.assertEqual(model, "deepseek-v4-flash")

    def test_bai_normalizes_claude_sonnet(self) -> None:
        settings = MockSettings(llm_provider="bai", bai_model="claude-sonnet-4-6")
        model = resolve_llm_model(settings, provider="bai")
        self.assertEqual(model, "claude-sonnet-4.6")

    def test_claude_returns_configured_model(self) -> None:
        settings = MockSettings(llm_provider="claude", claude_model="claude-opus-4-7")
        model = resolve_llm_model(settings, provider="claude")
        self.assertEqual(model, "claude-opus-4-7")

    def test_claude_default_when_no_model_set(self) -> None:
        settings = MockSettings(llm_provider="claude")
        model = resolve_llm_model(settings, provider="claude")
        self.assertEqual(model, "claude-k2.5")

    def test_resolves_provider_when_none_given(self) -> None:
        settings = MockSettings(llm_provider="deepseek", deepseek_model="deepseek-chat")
        model = resolve_llm_model(settings, provider=None)
        self.assertEqual(model, "deepseek-chat")

    def test_bai_empty_model_falls_back_to_hardcoded(self) -> None:
        settings = MockSettings(llm_provider="bai")
        model = resolve_llm_model(settings, provider="bai")
        self.assertEqual(model, "deepseek-v4-flash")


class DefaultLlmModelDisplayTests(unittest.TestCase):
    def test_deepseek(self) -> None:
        settings = MockSettings(llm_provider="deepseek", deepseek_model="deepseek-reasoner")
        self.assertEqual(default_llm_model_display(settings), "deepseek-reasoner")

    def test_openrouter_both_reasoning_and_fast_different(self) -> None:
        settings = MockSettings(
            llm_provider="openrouter",
            openrouter_reasoning_model="openai/o3",
            openrouter_fast_model="openai/gpt-4o-mini",
        )
        result = default_llm_model_display(settings)
        self.assertEqual(result, "openai/o3 / openai/gpt-4o-mini")

    def test_openrouter_only_legacy(self) -> None:
        settings = MockSettings(llm_provider="openrouter", openrouter_model="openai/gpt-4o")
        self.assertEqual(default_llm_model_display(settings), "openai/gpt-4o")

    def test_openrouter_only_reasoning(self) -> None:
        settings = MockSettings(
            llm_provider="openrouter", openrouter_reasoning_model="openai/o3"
        )
        self.assertEqual(default_llm_model_display(settings), "openai/o3")

    def test_openrouter_only_fast(self) -> None:
        settings = MockSettings(
            llm_provider="openrouter", openrouter_fast_model="openai/gpt-4o-mini"
        )
        self.assertEqual(default_llm_model_display(settings), "openai/gpt-4o-mini")

    def test_openrouter_reasoning_and_fast_same(self) -> None:
        settings = MockSettings(
            llm_provider="openrouter",
            openrouter_reasoning_model="openai/gpt-4o-mini",
            openrouter_fast_model="openai/gpt-4o-mini",
        )
        self.assertEqual(default_llm_model_display(settings), "openai/gpt-4o-mini")

    def test_bai(self) -> None:
        settings = MockSettings(llm_provider="bai", bai_model="deepseek-v4-flash")
        self.assertEqual(default_llm_model_display(settings), "deepseek-v4-flash")

    def test_bai_normalizes_claude_sonnet(self) -> None:
        settings = MockSettings(llm_provider="bai", bai_model="claude-sonnet-4-6")
        self.assertEqual(default_llm_model_display(settings), "claude-sonnet-4.6")

    def test_claude(self) -> None:
        settings = MockSettings(llm_provider="claude", claude_model="claude-sonnet-4-5")
        self.assertEqual(default_llm_model_display(settings), "claude-sonnet-4-5")

    def test_claude_default(self) -> None:
        settings = MockSettings(llm_provider="claude")
        self.assertEqual(default_llm_model_display(settings), "claude-k2.5")


class ResolveLlmApiKeyTests(unittest.TestCase):
    def test_deepseek(self) -> None:
        settings = MockSettings(deepseek_api_key="sk-ds-test")
        self.assertEqual(resolve_llm_api_key(settings, provider="deepseek"), "sk-ds-test")

    def test_openrouter(self) -> None:
        settings = MockSettings(openrouter_api_key="or-test")
        self.assertEqual(resolve_llm_api_key(settings, provider="openrouter"), "or-test")

    def test_bai(self) -> None:
        settings = MockSettings(bai_api_key="bai-test")
        self.assertEqual(resolve_llm_api_key(settings, provider="bai"), "bai-test")

    def test_claude(self) -> None:
        settings = MockSettings(claude_api_key="sk-ant-test")
        self.assertEqual(resolve_llm_api_key(settings, provider="claude"), "sk-ant-test")

    def test_returns_none_when_key_missing(self) -> None:
        settings = MockSettings()
        self.assertIsNone(resolve_llm_api_key(settings, provider="claude"))

    def test_resolves_provider_when_none_given(self) -> None:
        settings = MockSettings(llm_provider="bai", bai_api_key="bai-test")
        self.assertEqual(resolve_llm_api_key(settings), "bai-test")

    def test_unknown_provider_falls_to_claude(self) -> None:
        settings = MockSettings()
        self.assertIsNone(resolve_llm_api_key(settings, provider="nonexistent"))


class ResolveLlmBaseUrlTests(unittest.TestCase):
    def test_deepseek_default(self) -> None:
        settings = MockSettings()
        result = resolve_llm_base_url(settings, provider="deepseek")
        self.assertEqual(result, "https://api.deepseek.com")

    def test_deepseek_custom(self) -> None:
        settings = MockSettings(deepseek_base_url="https://custom.deepseek.com")
        result = resolve_llm_base_url(settings, provider="deepseek")
        self.assertEqual(result, "https://custom.deepseek.com")

    def test_openrouter_default(self) -> None:
        settings = MockSettings()
        result = resolve_llm_base_url(settings, provider="openrouter")
        self.assertEqual(result, "https://openrouter.ai/api/v1")

    def test_openrouter_custom(self) -> None:
        settings = MockSettings(openrouter_base_url="https://custom.openrouter.ai")
        result = resolve_llm_base_url(settings, provider="openrouter")
        self.assertEqual(result, "https://custom.openrouter.ai")

    def test_bai_default(self) -> None:
        settings = MockSettings()
        result = resolve_llm_base_url(settings, provider="bai")
        self.assertEqual(result, "https://api.b.ai")

    def test_bai_custom(self) -> None:
        settings = MockSettings(bai_base_url="https://custom.b.ai")
        result = resolve_llm_base_url(settings, provider="bai")
        self.assertEqual(result, "https://custom.b.ai")

    def test_claude_default(self) -> None:
        settings = MockSettings()
        result = resolve_llm_base_url(settings, provider="claude")
        self.assertEqual(result, "https://api.moonshot.ai/v1")

    def test_claude_custom(self) -> None:
        settings = MockSettings(claude_base_url="https://custom.anthropic.com")
        result = resolve_llm_base_url(settings, provider="claude")
        self.assertEqual(result, "https://custom.anthropic.com")

    def test_resolves_provider_when_none_given(self) -> None:
        settings = MockSettings(llm_provider="deepseek")
        result = resolve_llm_base_url(settings)
        self.assertEqual(result, "https://api.deepseek.com")

    def test_unknown_provider_falls_to_claude_default(self) -> None:
        settings = MockSettings()
        result = resolve_llm_base_url(settings, provider="nonexistent")
        self.assertEqual(result, "https://api.moonshot.ai/v1")


class NormalizeBaiModelTests(unittest.TestCase):
    def test_claude_sonnet_4_6(self) -> None:
        self.assertEqual(_normalize_bai_model("claude-sonnet-4-6"), "claude-sonnet-4.6")

    def test_claude_opus_4_7(self) -> None:
        self.assertEqual(_normalize_bai_model("claude-opus-4-7"), "claude-opus-4.7")

    def test_claude_opus_4_6(self) -> None:
        self.assertEqual(_normalize_bai_model("claude-opus-4-6"), "claude-opus-4.6")

    def test_passthrough_deepseek(self) -> None:
        self.assertEqual(_normalize_bai_model("deepseek-v4-flash"), "deepseek-v4-flash")

    def test_passthrough_kimi(self) -> None:
        self.assertEqual(_normalize_bai_model("kimi-k2.5"), "kimi-k2.5")

    def test_strips_whitespace(self) -> None:
        self.assertEqual(_normalize_bai_model("  claude-sonnet-4-6  "), "claude-sonnet-4.6")

    def test_empty_string(self) -> None:
        self.assertEqual(_normalize_bai_model(""), "")

    def test_only_normalizes_specific_patterns(self) -> None:
        self.assertEqual(_normalize_bai_model("claude-sonnet-4-5"), "claude-sonnet-4-5")


if __name__ == "__main__":
    unittest.main()
