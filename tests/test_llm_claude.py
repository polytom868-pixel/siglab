from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, patch

from siglab.config import SiglabConfig
from siglab.llm.claude import (
    BAI_CREDITS_PER_TOKEN,
    LLMAuthError,
    LLMConfigError,
    LLMFormatError,
    LLMProviderError,
    LLMQuotaError,
    LLMRateLimitError,
    LLMTransportError,
    LLMUpstreamError,
    ClaudeClient,
    ClaudeTool,
    _compact_scalar,
    _estimate_bai_credits,
    _estimate_message_tokens,
    _int_or_zero,
    _json_clone,
    _percentile,
)


def _minimal_config(**overrides: Any) -> SiglabConfig:
    return SiglabConfig(
        root_dir=Path("/tmp"),
        sosovalue_config_path=Path("/tmp/config.json"),
        generated_strategy_dir=Path("/tmp/deployed"),
        data_lake_dir=Path("/tmp/data"),
        artifact_dir=Path("/tmp/runs"),
        live_dir=Path("/tmp/live"),
        ancestry_db_path=Path("/tmp/siglab.db"),
        sosovalue_api_key_override=None,
        **overrides,
    )


# ─── BAI_CREDITS_PER_TOKEN ────────────────────────────────────────────────


class BaICreditsPerTokenTests(unittest.TestCase):

    def test_has_deepseek_v4_flash(self) -> None:
        rates = BAI_CREDITS_PER_TOKEN["deepseek-v4-flash"]
        self.assertEqual(len(rates), 4)
        self.assertEqual(rates[0], 0.14)  # input
        self.assertEqual(rates[1], 0.28)  # output
        self.assertEqual(rates[2], 0.14)  # cache_write
        self.assertEqual(rates[3], 0.003)  # cache_read

    def test_has_claude_opus_4_7(self) -> None:
        rates = BAI_CREDITS_PER_TOKEN["claude-opus-4-7"]
        self.assertEqual(rates[0], 5.00)
        self.assertEqual(rates[1], 25.00)
        self.assertEqual(rates[2], 6.25)
        self.assertEqual(rates[3], 0.50)

    def test_has_kimi_k2_5(self) -> None:
        rates = BAI_CREDITS_PER_TOKEN["kimi-k2.5"]
        self.assertEqual(rates[0], 0.59)
        self.assertEqual(rates[1], 3.00)
        self.assertEqual(rates[2], 0.59)
        self.assertEqual(rates[3], 0.177)

    def test_has_gemini_models(self) -> None:
        rates = BAI_CREDITS_PER_TOKEN["gemini-3.1-pro"]
        self.assertEqual(rates[0], 2.00)
        self.assertEqual(rates[1], 12.00)
        rates_flash = BAI_CREDITS_PER_TOKEN["gemini-3-flash"]
        self.assertEqual(rates_flash[0], 0.50)
        self.assertEqual(rates_flash[1], 3.00)

    def test_has_gpt_models(self) -> None:
        rates = BAI_CREDITS_PER_TOKEN["gpt-5.5"]
        self.assertEqual(rates[0], 5.00)
        rates_mini = BAI_CREDITS_PER_TOKEN["gpt-5-mini"]
        self.assertEqual(rates_mini[0], 0.25)

    def test_all_entries_have_four_values(self) -> None:
        for key, rates in BAI_CREDITS_PER_TOKEN.items():
            with self.subTest(model=key):
                self.assertEqual(len(rates), 4, msg=f"{key} should have 4 rates")
                for rate in rates:
                    self.assertIsInstance(rate, float)


# ─── LLMProviderError hierarchy ───────────────────────────────────────────


class LLMProviderErrorTests(unittest.TestCase):
    def test_base_error_has_provider_and_status_code(self) -> None:
        err = LLMProviderError("test", provider="bai", status_code=429)
        self.assertEqual(str(err), "test")
        self.assertEqual(err.provider, "bai")
        self.assertEqual(err.status_code, 429)

    def test_base_error_defaults_to_none(self) -> None:
        err = LLMProviderError("plain")
        self.assertIsNone(err.provider)
        self.assertIsNone(err.status_code)

    def test_llm_provider_error_is_runtime_error(self) -> None:
        self.assertTrue(issubclass(LLMProviderError, RuntimeError))

    def test_llm_config_error(self) -> None:
        err = LLMConfigError("bad config", provider="claude")
        self.assertIsInstance(err, LLMProviderError)
        self.assertEqual(err.provider, "claude")
        self.assertIsNone(err.status_code)

    def test_llm_auth_error(self) -> None:
        err = LLMAuthError("auth fail", provider="deepseek", status_code=401)
        self.assertIsInstance(err, LLMProviderError)
        self.assertEqual(err.provider, "deepseek")
        self.assertEqual(err.status_code, 401)

    def test_llm_rate_limit_error(self) -> None:
        err = LLMRateLimitError("too fast", provider="bai", status_code=429)
        self.assertIsInstance(err, LLMProviderError)
        self.assertIn("too fast", str(err))

    def test_llm_quota_error(self) -> None:
        err = LLMQuotaError("no credits", provider="bai")
        self.assertIsInstance(err, LLMProviderError)
        self.assertEqual(err.provider, "bai")

    def test_llm_transport_error(self) -> None:
        err = LLMTransportError("connect failed", provider="openrouter")
        self.assertIsInstance(err, LLMProviderError)

    def test_llm_upstream_error(self) -> None:
        err = LLMUpstreamError("500", provider="bai", status_code=500)
        self.assertIsInstance(err, LLMProviderError)
        self.assertEqual(err.status_code, 500)

    def test_llm_format_error(self) -> None:
        err = LLMFormatError("bad json", provider="claude")
        self.assertIsInstance(err, LLMProviderError)

    def test_all_subclasses_inherit_attributes(self) -> None:
        for cls, msg, prov, code in [
            (LLMConfigError, "c", "bai", None),
            (LLMAuthError, "a", "claude", 401),
            (LLMRateLimitError, "r", "deepseek", 429),
            (LLMQuotaError, "q", "openrouter", 403),
            (LLMTransportError, "t", "bai", None),
            (LLMUpstreamError, "u", "claude", 502),
            (LLMFormatError, "f", "bai", None),
        ]:
            with self.subTest(cls=cls.__name__):
                err = cls(msg, provider=prov, status_code=code)
                self.assertIsInstance(err, LLMProviderError)
                self.assertEqual(err.provider, prov)
                self.assertEqual(err.status_code, code)


# ─── ClaudeTool ────────────────────────────────────────────────────────────


class ClaudeToolTests(unittest.TestCase):
    def test_schema_returns_function_definition(self) -> None:
        tool = ClaudeTool(
            name="get_weather",
            description="Get current weather",
            parameters={"type": "object", "properties": {"loc": {"type": "string"}}},
            handler=MagicMock(),
        )
        expected = {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get current weather",
                "parameters": {"type": "object", "properties": {"loc": {"type": "string"}}},
            },
        }
        self.assertEqual(tool.schema(), expected)

    def test_schema_empty_params(self) -> None:
        tool = ClaudeTool(name="ping", description="Simple ping", parameters={}, handler=MagicMock())
        self.assertEqual(tool.schema()["function"]["name"], "ping")
        self.assertEqual(tool.schema()["function"]["parameters"], {})


# ─── _compact_scalar ──────────────────────────────────────────────────────


class CompactScalarTests(unittest.TestCase):
    def test_short_string_unchanged(self) -> None:
        self.assertEqual(_compact_scalar("hello"), "hello")

    def test_exact_2200_unchanged(self) -> None:
        s = "x" * 2200
        self.assertEqual(len(_compact_scalar(s)), 2200)

    def test_long_string_truncated(self) -> None:
        s = "x" * 2201
        result = _compact_scalar(s)
        self.assertEqual(len(result), 2200)
        self.assertTrue(result.endswith("…"))

    def test_none_passes_through(self) -> None:
        self.assertIsNone(_compact_scalar(None))

    def test_int_passes_through(self) -> None:
        self.assertEqual(_compact_scalar(42), 42)

    def test_dict_passes_through(self) -> None:
        d = {"key": "value"}
        self.assertIs(_compact_scalar(d), d)

    def test_list_passes_through(self) -> None:
        lst = [1, 2, 3]
        self.assertIs(_compact_scalar(lst), lst)


# ─── _int_or_zero ─────────────────────────────────────────────────────────


class IntOrZeroTests(unittest.TestCase):
    def test_positive_int(self) -> None:
        self.assertEqual(_int_or_zero(5), 5)

    def test_zero(self) -> None:
        self.assertEqual(_int_or_zero(0), 0)

    def test_negative_becomes_zero(self) -> None:
        self.assertEqual(_int_or_zero(-3), 0)

    def test_float_truncated(self) -> None:
        self.assertEqual(_int_or_zero(4.7), 4)

    def test_string_number(self) -> None:
        self.assertEqual(_int_or_zero("10"), 10)

    def test_none_returns_zero(self) -> None:
        self.assertEqual(_int_or_zero(None), 0)

    def test_bool_true_becomes_one(self) -> None:
        self.assertEqual(_int_or_zero(True), 1)

    def test_bool_false_becomes_zero(self) -> None:
        self.assertEqual(_int_or_zero(False), 0)

    def test_empty_string_raises_value_error_then_zero(self) -> None:
        self.assertEqual(_int_or_zero(""), 0)

    def test_list_returns_zero(self) -> None:
        self.assertEqual(_int_or_zero([1, 2, 3]), 0)

    def test_large_int(self) -> None:
        self.assertEqual(_int_or_zero(10**12), 10**12)

    def test_negative_float_becomes_zero(self) -> None:
        self.assertEqual(_int_or_zero(-0.5), 0)


# ─── _estimate_message_tokens ─────────────────────────────────────────────


class EstimateMessageTokensTests(unittest.TestCase):
    def test_single_message(self) -> None:
        msgs = [{"role": "user", "content": "hello"}]
        # json.dumps(...) = 37 chars -> (37+3)//4 = 10
        self.assertEqual(_estimate_message_tokens(msgs), 10)

    def test_multiple_messages(self) -> None:
        msgs = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "What is the weather?"},
        ]
        est = _estimate_message_tokens(msgs)
        self.assertGreater(est, 0)

    def test_minimum_of_one_for_empty_list(self) -> None:
        # json.dumps([]) = "[]" = 2 chars -> (2+3)//4 = 1
        self.assertEqual(_estimate_message_tokens([]), 1)

    def test_large_content(self) -> None:
        msgs = [{"role": "user", "content": "x" * 10000}]
        est = _estimate_message_tokens(msgs)
        self.assertGreater(est, 1000)

    def test_result_is_at_least_one(self) -> None:
        msgs = [{"role": "user", "content": ""}]
        self.assertGreaterEqual(_estimate_message_tokens(msgs), 1)


# ─── _estimate_bai_credits ────────────────────────────────────────────────


class EstimateBaiCreditsTests(unittest.TestCase):
    def test_simple_calculation(self) -> None:
        rates = (0.14, 0.28, 0.14, 0.003)
        credits = _estimate_bai_credits(input_tokens=1000, output_tokens=500, rates=rates)
        expected = (1000 * 0.14) + (500 * 0.28)
        self.assertEqual(credits, expected)

    def test_negative_input_clamped_to_zero(self) -> None:
        rates = (0.14, 0.28, 0.14, 0.003)
        credits = _estimate_bai_credits(input_tokens=-10, output_tokens=100, rates=rates)
        expected = (0 * 0.14) + (100 * 0.28)
        self.assertEqual(credits, expected)

    def test_negative_output_clamped_to_zero(self) -> None:
        rates = (0.14, 0.28, 0.14, 0.003)
        credits = _estimate_bai_credits(input_tokens=100, output_tokens=-50, rates=rates)
        expected = (100 * 0.14) + (0 * 0.28)
        self.assertEqual(credits, expected)

    def test_zero_tokens(self) -> None:
        rates = (0.14, 0.28, 0.14, 0.003)
        credits = _estimate_bai_credits(input_tokens=0, output_tokens=0, rates=rates)
        self.assertEqual(credits, 0.0)

    def test_float_input_tokens(self) -> None:
        rates = (0.14, 0.28, 0.14, 0.003)
        credits = _estimate_bai_credits(input_tokens=100, output_tokens=200, rates=rates)
        expected = (100 * 0.14) + (200 * 0.28)
        self.assertEqual(credits, expected)

    def test_different_rate_families(self) -> None:
        rates_minimax = (0.30, 1.20, 0.375, 0.06)
        credits = _estimate_bai_credits(input_tokens=100, output_tokens=100, rates=rates_minimax)
        self.assertEqual(credits, (100 * 0.30) + (100 * 1.20))

    def test_large_values(self) -> None:
        rates = (5.00, 25.00, 6.25, 0.50)
        credits = _estimate_bai_credits(input_tokens=10**6, output_tokens=10**5, rates=rates)
        expected = (10**6 * 5.00) + (10**5 * 25.00)
        self.assertEqual(credits, expected)


# ─── _json_clone ──────────────────────────────────────────────────────────


class JsonCloneTests(unittest.TestCase):
    def test_produces_independent_copy(self) -> None:
        original: Any = {"a": [1, 2, {"b": "hello"}]}
        clone = _json_clone(original)
        self.assertEqual(original, clone)
        original["a"][2]["b"] = "changed"
        self.assertEqual(clone["a"][2]["b"], "hello")

    def test_simple_types(self) -> None:
        self.assertEqual(_json_clone(42), 42)
        self.assertEqual(_json_clone("hello"), "hello")
        self.assertEqual(_json_clone(None), None)

    def test_list(self) -> None:
        original: Any = [1, {"x": "y"}, [3, 4]]
        clone = _json_clone(original)
        self.assertEqual(original, clone)
        original[1]["x"] = "z"
        self.assertEqual(clone[1]["x"], "y")

    def test_non_serializable_defaults_to_str(self) -> None:
        result = _json_clone({"obj": object()})
        self.assertIsInstance(result["obj"], str)


# ─── _percentile ──────────────────────────────────────────────────────────


class PercentileTests(unittest.TestCase):
    def test_empty_list_returns_none(self) -> None:
        self.assertIsNone(_percentile([], 50))

    def test_single_value_returns_that_value(self) -> None:
        self.assertEqual(_percentile([42.0], 50), 42.0)
        self.assertEqual(_percentile([42.0], 95), 42.0)
        self.assertEqual(_percentile([42.0], 0), 42.0)

    def test_two_values_p50(self) -> None:
        self.assertEqual(_percentile([10.0, 20.0], 50), 15.0)

    def test_two_values_p95(self) -> None:
        self.assertEqual(_percentile([10.0, 20.0], 95), 19.5)

    def test_three_values_p50_mid(self) -> None:
        self.assertEqual(_percentile([1.0, 5.0, 10.0], 50), 5.0)

    def test_p0_is_first(self) -> None:
        self.assertEqual(_percentile([1.0, 5.0, 10.0], 0), 1.0)

    def test_p100_is_last(self) -> None:
        self.assertEqual(_percentile([1.0, 5.0, 10.0], 100), 10.0)

    def test_large_list(self) -> None:
        vals = [float(i) for i in range(1000)]
        p50 = cast(float, _percentile(vals, 50))
        p95 = cast(float, _percentile(vals, 95))
        # R-7: rank = 0.5 * 999 = 499.5 -> interpolated between 499 and 500
        self.assertAlmostEqual(p50, 499.5, delta=1)
        # R-7: rank = 0.95 * 999 = 949.05 -> interpolated between 949 and 950
        self.assertAlmostEqual(p95, 949.05, delta=1)

    def test_unsorted_values(self) -> None:
        # _percentile sorts values first, then uses R-7 interpolation
        vals = [100.0, 1.0, 50.0]
        # sorted = [1.0, 50.0, 100.0], rank = 0.5 * 2 = 1.0 -> lower=upper=1 -> 50.0
        self.assertEqual(_percentile(vals, 50), 50.0)

    def test_floating_percentile_rounds_correctly(self) -> None:
        vals = [10.0, 20.0, 30.0]
        # R-7: rank = (37/100) * 2 = 0.74, lower=0, upper=1, frac=0.74
        # result = 10.0 + 0.74 * 10.0 = 17.4
        self.assertAlmostEqual(_percentile(vals, 37), 17.4)

    def test_out_of_range_clamped(self) -> None:
        vals = [1.0, 2.0]
        # R-7: rank = 2.0 * 1 = 2.0, clamped to n-1=1, returns ordered[1] = 2.0
        self.assertEqual(_percentile(vals, 200), 2.0)
        # R-7: rank = -0.5 * 1 = -0.5, clamped to 0, returns ordered[0] = 1.0
        self.assertEqual(_percentile(vals, -50), 1.0)


# ─── _parse_json ──────────────────────────────────────────────────────────


class ParseJsonTests(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.client = ClaudeClient(_minimal_config())

    def test_raw_json(self) -> None:
        result = self.client._parse_json('{"a": 1, "b": "hello"}')
        self.assertEqual(result, {"a": 1, "b": "hello"})

    def test_json_in_code_block(self) -> None:
        text = "```json\n{\"key\": \"value\"}\n```"
        result = self.client._parse_json(text)
        self.assertEqual(result, {"key": "value"})

    def test_json_in_code_block_without_lang(self) -> None:
        text = "```\n{\"nested\": {\"x\": 42}}\n```"
        result = self.client._parse_json(text)
        self.assertEqual(result, {"nested": {"x": 42}})

    def test_code_block_with_surrounding_text(self) -> None:
        text = "Here is the data:\n```json\n{\"result\": \"ok\"}\n```\nEnd."
        result = self.client._parse_json(text)
        self.assertEqual(result, {"result": "ok"})

    def test_whitespace_around_json(self) -> None:
        result = self.client._parse_json('  {"a": 1}  ')
        self.assertEqual(result, {"a": 1})

    def test_raises_on_invalid_json(self) -> None:
        with self.assertRaises(json.JSONDecodeError):
            self.client._parse_json("{invalid}")

    def test_raises_on_empty_string(self) -> None:
        with self.assertRaises(json.JSONDecodeError):
            self.client._parse_json("")

    def test_single_code_block_with_extra_text_before(self) -> None:
        text = "prefix\n```json\n{\"key\": \"value\"}\n```\nsuffix"
        result = self.client._parse_json(text)
        self.assertEqual(result, {"key": "value"})

    def test_nested_objects(self) -> None:
        text = '{"level1": {"level2": {"level3": 42}}}'
        result = self.client._parse_json(text)
        self.assertEqual(result["level1"]["level2"]["level3"], 42)


# ─── ClaudeClient Construction ────────────────────────────────────────────


class ClaudeClientConstructionTests(unittest.TestCase):
    def test_constructs_with_minimal_config(self) -> None:
        config = _minimal_config()
        client = ClaudeClient(config)
        self.assertIsInstance(client, ClaudeClient)
        self.assertIs(client.settings, config)
        self.assertIsNone(client.last_trace)
        self.assertIsNone(client.last_exchange)
        self.assertIsNone(client._client)
        self.assertEqual(client._latencies_ms, [])
        self.assertEqual(client._request_count, 0)
        self.assertEqual(client._success_count, 0)
        self.assertIsNotNone(client.routing_policy)

    def test_counts_initialize_to_zero(self) -> None:
        client = ClaudeClient(_minimal_config())
        self.assertEqual(client._retries, 0)
        self.assertEqual(client._rate_limits, 0)
        self.assertEqual(client._transport_failures, 0)
        self.assertEqual(client._prompt_tokens, 0)
        self.assertEqual(client._completion_tokens, 0)
        self.assertEqual(client._total_tokens, 0)
        self.assertEqual(client._cache_write_tokens, 0)
        self.assertEqual(client._cache_read_tokens, 0)
        self.assertEqual(client._usage_credits, 0.0)
        self.assertEqual(client._priced_token_count, 0)
        self.assertEqual(client._context_pressure_events, [])
        self.assertEqual(client._credit_pressure_events, [])


# ─── ClaudeClient is_configured / provider_name ───────────────────────────


class ClaudeClientConfiguredTests(unittest.TestCase):
    @patch("siglab.llm.claude.resolve_llm_provider")
    @patch("siglab.llm.claude.resolve_llm_api_key")
    def test_is_configured_true_when_key_present(
        self, mock_key: MagicMock, mock_provider: MagicMock
    ) -> None:
        mock_key.return_value = "sk-test"
        mock_provider.return_value = "bai"
        client = ClaudeClient(_minimal_config())
        self.assertTrue(client.is_configured)

    @patch("siglab.llm.claude.resolve_llm_provider")
    @patch("siglab.llm.claude.resolve_llm_api_key")
    def test_is_configured_false_when_key_missing(
        self, mock_key: MagicMock, mock_provider: MagicMock
    ) -> None:
        mock_key.return_value = None
        mock_provider.return_value = "bai"
        client = ClaudeClient(_minimal_config())
        self.assertFalse(client.is_configured)

    @patch("siglab.llm.claude.resolve_llm_api_key")
    @patch("siglab.llm.claude.resolve_llm_provider")
    def test_is_configured_empty_key_is_false(
        self, mock_provider: MagicMock, mock_key: MagicMock
    ) -> None:
        mock_key.return_value = ""
        mock_provider.return_value = "bai"
        client = ClaudeClient(_minimal_config())
        self.assertFalse(client.is_configured)

    @patch("siglab.llm.claude.resolve_llm_provider")
    def test_provider_name_delegates_to_resolver(
        self, mock_provider: MagicMock
    ) -> None:
        mock_provider.return_value = "deepseek"
        client = ClaudeClient(_minimal_config())
        self.assertEqual(client.provider_name, "deepseek")

    @patch("siglab.llm.claude.resolve_llm_provider")
    def test_provider_name_bai(self, mock_provider: MagicMock) -> None:
        mock_provider.return_value = "bai"
        client = ClaudeClient(_minimal_config())
        self.assertEqual(client.provider_name, "bai")


# ─── _compact_tool_payload ────────────────────────────────────────────────


class CompactToolPayloadTests(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.client = ClaudeClient(_minimal_config())

    def test_none_value(self) -> None:
        self.assertIsNone(self.client._compact_tool_payload(None))

    def test_string(self) -> None:
        self.assertEqual(self.client._compact_tool_payload("hello"), "hello")

    def test_number(self) -> None:
        self.assertEqual(self.client._compact_tool_payload(42), 42)

    def test_small_dict(self) -> None:
        d = {"a": 1, "b": "two"}
        result = self.client._compact_tool_payload(d)
        self.assertEqual(result, {"a": 1, "b": "two"})

    def test_dict_truncated_at_12_keys(self) -> None:
        d = {str(i): i for i in range(15)}
        result = self.client._compact_tool_payload(d)
        self.assertEqual(len(result), 13)  # 12 keys + 'truncated'
        self.assertTrue(result["truncated"])
        for i in range(12):
            self.assertIn(str(i), result)
        self.assertNotIn("12", result)

    def test_small_list(self) -> None:
        result = self.client._compact_tool_payload([1, 2, 3])
        self.assertEqual(result, [1, 2, 3])

    def test_list_truncated_at_8_items(self) -> None:
        lst = list(range(15))
        result = self.client._compact_tool_payload(lst)
        self.assertEqual(len(result), 9)  # 8 items + truncation marker
        self.assertEqual(result[:8], list(range(8)))
        self.assertEqual(result[8], {"truncated": True, "remaining": 7})

    def test_list_at_boundary_8_not_truncated(self) -> None:
        lst = list(range(8))
        result = self.client._compact_tool_payload(lst)
        self.assertEqual(len(result), 8)
        self.assertNotIsInstance(result[-1], dict)

    def test_list_9_items_truncated(self) -> None:
        lst = list(range(9))
        result = self.client._compact_tool_payload(lst)
        self.assertEqual(len(result), 9)
        self.assertEqual(result[8], {"truncated": True, "remaining": 1})

    def test_depth_limiting_at_4(self) -> None:
        nested = {"l1": {"l2": {"l3": {"l4": {"l5": "deep"}}}}}
        result = self.client._compact_tool_payload(nested)
        # At depth >= 4, _compact_scalar returns the value as-is (dict passes through)
        self.assertEqual(result["l1"]["l2"]["l3"]["l4"], {"l5": "deep"})

    def test_long_string_at_depth_gets_truncated(self) -> None:
        nested = {"deep": "x" * 3000}
        # Depth 0, 1 entry -> depth 1, str > 2200 gets compacted
        self.client._compact_tool_payload(nested)
        result = self.client._compact_tool_payload(nested)
        self.assertEqual(len(result["deep"]), 2200)
        self.assertTrue(result["deep"].endswith("…"))

    def test_dict_within_list_at_depth(self) -> None:
        value = [{"a": {"b": {"c": {"d": "final", "e": "extra"}}}}]
        result = self.client._compact_tool_payload(value)
        # depth: 0 (list) -> 1 (dict) -> 2 (dict) -> 3 (dict) -> 4 (dict, so scalar)
        self.assertIsInstance(result[0]["a"]["b"]["c"]["d"], str)
        self.assertIsInstance(result[0]["a"]["b"]["c"]["e"], str)


# ─── metrics_snapshot ─────────────────────────────────────────────────────


class MetricsSnapshotTests(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.config = _minimal_config()
        self.client = ClaudeClient(self.config)

    @patch("siglab.llm.claude.resolve_llm_provider")
    @patch("siglab.llm.claude.resolve_llm_model")
    def test_empty_snapshot_has_structure(
        self, mock_model: MagicMock, mock_provider: MagicMock
    ) -> None:
        mock_provider.return_value = "bai"
        mock_model.return_value = "deepseek-v4-flash"

        snap = self.client.metrics_snapshot()
        self.assertEqual(snap["provider"], "bai")
        self.assertEqual(snap["model"], "deepseek-v4-flash")
        self.assertIsNone(snap["p50_ms"])
        self.assertIsNone(snap["p95_ms"])
        self.assertEqual(snap["retry_count"], 0)
        self.assertEqual(snap["rate_limit_count"], 0)
        self.assertEqual(snap["transport_failures"], 0)
        self.assertEqual(snap["success_rate"], 0.0)
        self.assertIn("usage", snap)
        self.assertIn("context_pressure", snap)
        self.assertIn("credit_pressure", snap)
        self.assertIn("routing_policy", snap)

    @patch("siglab.llm.claude.resolve_llm_provider")
    @patch("siglab.llm.claude.resolve_llm_model")
    def test_snapshot_with_latency_data(
        self, mock_model: MagicMock, mock_provider: MagicMock
    ) -> None:
        mock_provider.return_value = "bai"
        mock_model.return_value = "deepseek-v4-flash"
        self.client._latencies_ms = [10.0, 20.0, 30.0, 40.0, 50.0]
        self.client._request_count = 5
        self.client._success_count = 5

        snap = self.client.metrics_snapshot()
        self.assertEqual(snap["p50_ms"], 30.0)
        self.assertEqual(snap["p95_ms"], 48.0)
        self.assertEqual(snap["success_rate"], 1.0)

    @patch("siglab.llm.claude.resolve_llm_provider")
    @patch("siglab.llm.claude.resolve_llm_model")
    def test_snapshot_with_failures(
        self, mock_model: MagicMock, mock_provider: MagicMock
    ) -> None:
        mock_provider.return_value = "bai"
        mock_model.return_value = "deepseek-v4-flash"
        self.client._request_count = 10
        self.client._success_count = 7
        self.client._retries = 3
        self.client._rate_limits = 2
        self.client._transport_failures = 1

        snap = self.client.metrics_snapshot()
        self.assertEqual(snap["retry_count"], 3)
        self.assertEqual(snap["rate_limit_count"], 2)
        self.assertEqual(snap["transport_failures"], 1)
        self.assertEqual(snap["success_rate"], 0.7)

    @patch("siglab.llm.claude.resolve_llm_provider")
    @patch("siglab.llm.claude.resolve_llm_model")
    def test_snapshot_usage_no_priced_tokens(
        self, mock_model: MagicMock, mock_provider: MagicMock
    ) -> None:
        mock_provider.return_value = "bai"
        mock_model.return_value = "deepseek-v4-flash"
        self.client._prompt_tokens = 100
        self.client._completion_tokens = 50
        self.client._total_tokens = 150

        usage = self.client.metrics_snapshot()["usage"]
        self.assertEqual(usage["prompt_tokens"], 100)
        self.assertEqual(usage["completion_tokens"], 50)
        self.assertEqual(usage["total_tokens"], 150)
        self.assertIsNone(usage["credits_estimate"])
        self.assertEqual(usage["priced_tokens"], 0)
        self.assertEqual(usage["cost_status"], "unpriced_token_usage_only")
        self.assertIsNone(usage["pricing_source"])

    @patch("siglab.llm.claude.resolve_llm_provider")
    @patch("siglab.llm.claude.resolve_llm_model")
    def test_snapshot_usage_with_priced_tokens(
        self, mock_model: MagicMock, mock_provider: MagicMock
    ) -> None:
        mock_provider.return_value = "bai"
        mock_model.return_value = "deepseek-v4-flash"
        self.client._priced_token_count = 500
        self.client._usage_credits = 123.456789

        usage = self.client.metrics_snapshot()["usage"]
        self.assertEqual(usage["credits_estimate"], 123.456789)
        self.assertEqual(usage["priced_tokens"], 500)
        self.assertEqual(
            usage["cost_status"], "verified_bai_credit_estimate_usd_unpriced"
        )
        self.assertEqual(
            usage["pricing_source"],
            "https://docs.b.ai/llmservice/pricing-and-usage/",
        )

    @patch("siglab.llm.claude.resolve_llm_provider")
    @patch("siglab.llm.claude.resolve_llm_model")
    def test_snapshot_context_pressure(
        self, mock_model: MagicMock, mock_provider: MagicMock
    ) -> None:
        mock_provider.return_value = "bai"
        mock_model.return_value = "deepseek-v4-flash"
        self.client._context_pressure_events = [
            {"stage": "writer", "severity": "warning"}
        ]

        cp = self.client.metrics_snapshot()["context_pressure"]
        self.assertEqual(cp["event_count"], 1)
        self.assertEqual(cp["latest"], {"stage": "writer", "severity": "warning"})

    @patch("siglab.llm.claude.resolve_llm_provider")
    @patch("siglab.llm.claude.resolve_llm_model")
    def test_snapshot_context_pressure_empty(
        self, mock_model: MagicMock, mock_provider: MagicMock
    ) -> None:
        mock_provider.return_value = "bai"
        mock_model.return_value = "deepseek-v4-flash"

        cp = self.client.metrics_snapshot()["context_pressure"]
        self.assertEqual(cp["event_count"], 0)
        self.assertIsNone(cp["latest"])

    @patch("siglab.llm.claude.resolve_llm_provider")
    @patch("siglab.llm.claude.resolve_llm_model")
    def test_snapshot_credit_pressure(
        self, mock_model: MagicMock, mock_provider: MagicMock
    ) -> None:
        mock_provider.return_value = "bai"
        mock_model.return_value = "deepseek-v4-flash"
        self.client._credit_pressure_events = [
            {"stage": "planner", "severity": "ok"}
        ]

        cp = self.client.metrics_snapshot()["credit_pressure"]
        self.assertEqual(cp["event_count"], 1)
        self.assertEqual(cp["latest"], {"stage": "planner", "severity": "ok"})

    @patch("siglab.llm.claude.resolve_llm_provider")
    @patch("siglab.llm.claude.resolve_llm_model")
    def test_success_rate_never_divides_by_zero(
        self, mock_model: MagicMock, mock_provider: MagicMock
    ) -> None:
        mock_provider.return_value = "bai"
        mock_model.return_value = "deepseek-v4-flash"
        # _request_count is 0, but metrics_snapshot uses max(1, _request_count)
        snap = self.client.metrics_snapshot()
        self.assertEqual(snap["success_rate"], 0.0)

    @patch("siglab.llm.claude.resolve_llm_provider")
    @patch("siglab.llm.claude.resolve_llm_model")
    def test_snapshot_credits_estimate_rounded(
        self, mock_model: MagicMock, mock_provider: MagicMock
    ) -> None:
        mock_provider.return_value = "bai"
        mock_model.return_value = "deepseek-v4-flash"
        self.client._priced_token_count = 100
        self.client._usage_credits = 0.123456789

        usage = self.client.metrics_snapshot()["usage"]
        self.assertEqual(usage["credits_estimate"], 0.123457)  # rounded to 6dp


# ─── _record_usage ────────────────────────────────────────────────────────


class RecordUsageTests(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.client = ClaudeClient(_minimal_config())

    @patch("siglab.llm.claude.resolve_llm_provider")
    def test_record_usage_skips_non_dict(self, mock_provider: MagicMock) -> None:
        mock_provider.return_value = "bai"
        self.client._record_usage(None)
        self.assertEqual(self.client._prompt_tokens, 0)

    def test_record_usage_empty_dict(self) -> None:
        self.client._record_usage({})
        self.assertEqual(self.client._total_tokens, 0)

    def test_record_usage_prompt_tokens(self) -> None:
        self.client._record_usage({"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150})
        self.assertEqual(self.client._prompt_tokens, 100)
        self.assertEqual(self.client._completion_tokens, 50)
        self.assertEqual(self.client._total_tokens, 150)

    def test_record_usage_aliases_input_tokens(self) -> None:
        self.client._record_usage({"input_tokens": 200, "output_tokens": 75})
        self.assertEqual(self.client._prompt_tokens, 200)
        self.assertEqual(self.client._completion_tokens, 75)

    def test_record_usage_aliases_camel_case(self) -> None:
        self.client._record_usage({"promptTokens": 300, "completionTokens": 100, "totalTokens": 400})
        self.assertEqual(self.client._prompt_tokens, 300)

    def test_record_usage_fills_total_when_missing(self) -> None:
        self.client._record_usage({"prompt_tokens": 50, "completion_tokens": 30})
        self.assertEqual(self.client._total_tokens, 80)

    def test_record_usage_cache_tokens(self) -> None:
        self.client._record_usage({
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "cache_creation_input_tokens": 20,
            "cache_read_input_tokens": 10,
        })
        self.assertEqual(self.client._cache_write_tokens, 20)
        self.assertEqual(self.client._cache_read_tokens, 10)

    def test_record_usage_cache_aliases(self) -> None:
        self.client._record_usage({
            "cache_write_tokens": 15,
            "cached_tokens": 5,
            "prompt_tokens": 100,
            "completion_tokens": 50,
        })
        self.assertEqual(self.client._cache_write_tokens, 15)
        self.assertEqual(self.client._cache_read_tokens, 5)

    def test_record_usage_cache_read_from_prompt_details(self) -> None:
        self.client._record_usage({
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "prompt_tokens_details": {"cached_tokens": 30},
        })
        self.assertEqual(self.client._cache_read_tokens, 30)

    def test_record_usage_negative_values_clamped(self) -> None:
        self.client._record_usage({
            "prompt_tokens": -10,
            "completion_tokens": 50,
            "total_tokens": -5,
        })
        # total_tokens(-5)->0; since 0 and prompt(0)+completion(50)>0, total=50
        self.assertEqual(self.client._prompt_tokens, 0)
        self.assertEqual(self.client._completion_tokens, 50)
        self.assertEqual(self.client._total_tokens, 50)

    @patch("siglab.llm.claude.resolve_llm_provider")
    def test_record_usage_credits_calculation(
        self, mock_provider: MagicMock
    ) -> None:
        mock_provider.return_value = "bai"
        self.client._record_usage(
            {"prompt_tokens": 1000, "completion_tokens": 500},
            model="deepseek-v4-flash",
        )
        # 1000 * 0.14 + 500 * 0.28 = 140 + 140 = 280
        self.assertAlmostEqual(self.client._usage_credits, 280.0)
        self.assertEqual(self.client._priced_token_count, 1500)

    @patch("siglab.llm.claude.resolve_llm_provider")
    def test_record_usage_skips_credits_when_no_rates(
        self, mock_provider: MagicMock
    ) -> None:
        mock_provider.return_value = "bai"
        # unknown model -> no rates -> credits not computed
        self.client._record_usage(
            {"prompt_tokens": 100, "completion_tokens": 50},
            model="unknown-model",
        )
        self.assertEqual(self.client._usage_credits, 0.0)

    @patch("siglab.llm.claude.resolve_llm_provider")
    def test_record_usage_non_bai_skips_credit_computation(
        self, mock_provider: MagicMock
    ) -> None:
        mock_provider.return_value = "claude"
        self.client._record_usage(
            {"prompt_tokens": 100, "completion_tokens": 50},
            model="claude-opus-4-5",
        )
        self.assertEqual(self.client._usage_credits, 0.0)

    def test_record_usage_cumulative(self) -> None:
        self.client._record_usage({"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
        self.client._record_usage({"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30})
        self.assertEqual(self.client._prompt_tokens, 30)
        self.assertEqual(self.client._completion_tokens, 15)
        self.assertEqual(self.client._total_tokens, 45)


# ─── _extract_choice ──────────────────────────────────────────────────────


class ExtractChoiceTests(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.client = ClaudeClient(_minimal_config())

    def test_extracts_first_choice(self) -> None:
        body = {"choices": [{"index": 0, "message": {"content": "hello"}}]}
        choice = self.client._extract_choice(body)
        self.assertEqual(choice["index"], 0)
        self.assertEqual(choice["message"]["content"], "hello")

    def test_returns_dict_copy(self) -> None:
        body = {"choices": [{"finish_reason": "stop"}]}
        choice = self.client._extract_choice(body)
        body["choices"][0]["finish_reason"] = "changed"
        self.assertEqual(choice["finish_reason"], "stop")

    @patch("siglab.llm.claude.resolve_llm_provider")
    def test_raises_on_empty_choices(self, mock_provider: MagicMock) -> None:
        mock_provider.return_value = "bai"
        body = {"choices": []}
        with self.assertRaises(LLMFormatError) as ctx:
            self.client._extract_choice(body)
        self.assertIn("no choices", str(ctx.exception).lower())

    @patch("siglab.llm.claude.resolve_llm_provider")
    def test_raises_on_missing_choices(self, mock_provider: MagicMock) -> None:
        mock_provider.return_value = "bai"
        body = {}
        with self.assertRaises(LLMFormatError):
            self.client._extract_choice(body)

    def test_handles_none_choice(self) -> None:
        body = {"choices": [None]}
        choice = self.client._extract_choice(body)
        self.assertEqual(choice, {})


# ─── _extract_message_content ─────────────────────────────────────────────


class ExtractMessageContentTests(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.client = ClaudeClient(_minimal_config())

    @patch("siglab.llm.claude.resolve_llm_provider")
    def test_string_content(self, mock_provider: MagicMock) -> None:
        mock_provider.return_value = "bai"
        body = {"choices": [{"message": {"content": "hello world"}}]}
        self.assertEqual(self.client._extract_message_content(body), "hello world")

    @patch("siglab.llm.claude.resolve_llm_provider")
    def test_list_content_text_pieces(self, mock_provider: MagicMock) -> None:
        mock_provider.return_value = "bai"
        body = {
            "choices": [
                {
                    "message": {
                        "content": [
                            {"type": "text", "text": "Hello"},
                            {"type": "text", "text": "World"},
                        ]
                    }
                }
            ]
        }
        self.assertEqual(self.client._extract_message_content(body), "Hello\nWorld")

    @patch("siglab.llm.claude.resolve_llm_provider")
    def test_list_content_skips_non_text(self, mock_provider: MagicMock) -> None:
        mock_provider.return_value = "bai"
        body = {
            "choices": [
                {
                    "message": {
                        "content": [
                            {"type": "text", "text": "Result"},
                            {"type": "image", "source": {}},
                        ]
                    }
                }
            ]
        }
        self.assertEqual(self.client._extract_message_content(body), "Result")

    @patch("siglab.llm.claude.resolve_llm_provider")
    def test_empty_content_list_raises(self, mock_provider: MagicMock) -> None:
        mock_provider.return_value = "bai"
        body = {"choices": [{"message": {"content": []}}]}
        with self.assertRaises(LLMFormatError):
            self.client._extract_message_content(body)

    @patch("siglab.llm.claude.resolve_llm_provider")
    def test_none_content_raises(self, mock_provider: MagicMock) -> None:
        mock_provider.return_value = "bai"
        body = {"choices": [{"message": {"content": None}}]}
        with self.assertRaises(LLMFormatError):
            self.client._extract_message_content(body)

    @patch("siglab.llm.claude.resolve_llm_provider")
    def test_missing_content_raises(self, mock_provider: MagicMock) -> None:
        mock_provider.return_value = "bai"
        body = {"choices": [{"message": {}}]}
        with self.assertRaises(LLMFormatError):
            self.client._extract_message_content(body)

    @patch("siglab.llm.claude.resolve_llm_provider")
    def test_list_content_all_non_text_raises(self, mock_provider: MagicMock) -> None:
        mock_provider.return_value = "bai"
        body = {
            "choices": [
                {
                    "message": {
                        "content": [
                            {"type": "image", "source": {}},
                            {"type": "tool_use", "id": "x"},
                        ]
                    }
                }
            ]
        }
        with self.assertRaises(LLMFormatError):
            self.client._extract_message_content(body)


# ─── _chat_url ────────────────────────────────────────────────────────────


class ChatUrlTests(unittest.TestCase):
    @patch("siglab.llm.claude.resolve_llm_provider")
    @patch("siglab.llm.claude.resolve_llm_base_url")
    def test_bai_base_url_appends_v1(
        self, mock_base: MagicMock, mock_provider: MagicMock
    ) -> None:
        mock_provider.return_value = "bai"
        mock_base.return_value = "https://api.b.ai"
        client = ClaudeClient(_minimal_config())
        self.assertEqual(client._chat_url(), "https://api.b.ai/v1/chat/completions")

    @patch("siglab.llm.claude.resolve_llm_provider")
    @patch("siglab.llm.claude.resolve_llm_base_url")
    def test_bai_with_v1_no_double_append(
        self, mock_base: MagicMock, mock_provider: MagicMock
    ) -> None:
        mock_provider.return_value = "bai"
        mock_base.return_value = "https://api.b.ai/v1"
        client = ClaudeClient(_minimal_config())
        self.assertEqual(client._chat_url(), "https://api.b.ai/v1/chat/completions")

    @patch("siglab.llm.claude.resolve_llm_provider")
    @patch("siglab.llm.claude.resolve_llm_base_url")
    def test_deepseek_chat_url(
        self, mock_base: MagicMock, mock_provider: MagicMock
    ) -> None:
        mock_provider.return_value = "deepseek"
        mock_base.return_value = "https://api.deepseek.com"
        client = ClaudeClient(_minimal_config())
        self.assertEqual(client._chat_url(), "https://api.deepseek.com/chat/completions")

    @patch("siglab.llm.claude.resolve_llm_provider")
    @patch("siglab.llm.claude.resolve_llm_base_url")
    def test_chat_url_strips_trailing_slash(
        self, mock_base: MagicMock, mock_provider: MagicMock
    ) -> None:
        mock_provider.return_value = "deepseek"
        mock_base.return_value = "https://api.deepseek.com/"
        client = ClaudeClient(_minimal_config())
        self.assertEqual(client._chat_url(), "https://api.deepseek.com/chat/completions")


# ─── _provider_label ──────────────────────────────────────────────────────


class ProviderLabelTests(unittest.TestCase):
    @patch("siglab.llm.claude.resolve_llm_provider")
    def test_deepseek_label(self, mock_provider: MagicMock) -> None:
        mock_provider.return_value = "deepseek"
        client = ClaudeClient(_minimal_config())
        self.assertEqual(client._provider_label(), "DeepSeek")

    @patch("siglab.llm.claude.resolve_llm_provider")
    def test_openrouter_label(self, mock_provider: MagicMock) -> None:
        mock_provider.return_value = "openrouter"
        client = ClaudeClient(_minimal_config())
        self.assertEqual(client._provider_label(), "OpenRouter")

    @patch("siglab.llm.claude.resolve_llm_provider")
    def test_bai_label(self, mock_provider: MagicMock) -> None:
        mock_provider.return_value = "bai"
        client = ClaudeClient(_minimal_config())
        self.assertEqual(client._provider_label(), "B.AI")

    @patch("siglab.llm.claude.resolve_llm_provider")
    def test_claude_label(self, mock_provider: MagicMock) -> None:
        mock_provider.return_value = "claude"
        client = ClaudeClient(_minimal_config())
        self.assertEqual(client._provider_label(), "Claude")

    @patch("siglab.llm.claude.resolve_llm_provider")
    def test_unknown_provider_label(self, mock_provider: MagicMock) -> None:
        mock_provider.return_value = "unknown"
        client = ClaudeClient(_minimal_config())
        self.assertEqual(client._provider_label(), "LLM")


# ─── _request_headers ─────────────────────────────────────────────────────


class RequestHeadersTests(unittest.TestCase):
    @patch("siglab.llm.claude.resolve_llm_provider")
    @patch("siglab.llm.claude.resolve_llm_api_key")
    def test_basic_auth_header(
        self, mock_key: MagicMock, mock_provider: MagicMock
    ) -> None:
        mock_key.return_value = "sk-test"
        mock_provider.return_value = "deepseek"
        client = ClaudeClient(_minimal_config())
        headers = client._request_headers()
        self.assertEqual(headers["Authorization"], "Bearer sk-test")
        self.assertEqual(headers["Content-Type"], "application/json")

    @patch("siglab.llm.claude.resolve_llm_provider")
    @patch("siglab.llm.claude.resolve_llm_api_key")
    def test_bai_has_api_key_header(
        self, mock_key: MagicMock, mock_provider: MagicMock
    ) -> None:
        mock_key.return_value = "bai-key"
        mock_provider.return_value = "bai"
        client = ClaudeClient(_minimal_config())
        headers = client._request_headers()
        self.assertIn("x-api-key", headers)
        self.assertEqual(headers["x-api-key"], "bai-key")

    @patch("siglab.llm.claude.resolve_llm_provider")
    @patch("siglab.llm.claude.resolve_llm_api_key")
    def test_request_id_header(
        self, mock_key: MagicMock, mock_provider: MagicMock
    ) -> None:
        mock_key.return_value = "sk-test"
        mock_provider.return_value = "deepseek"
        client = ClaudeClient(_minimal_config())
        headers = client._request_headers(request_id="abc-123")
        self.assertEqual(headers["X-Request-ID"], "abc-123")

    @patch("siglab.llm.claude.resolve_llm_provider")
    @patch("siglab.llm.claude.resolve_llm_api_key")
    def test_openrouter_headers(
        self, mock_key: MagicMock, mock_provider: MagicMock
    ) -> None:
        mock_key.return_value = "or-key"
        mock_provider.return_value = "openrouter"
        config = _minimal_config(
            openrouter_http_referer="https://myapp.com",
            openrouter_title="MyApp",
        )
        client = ClaudeClient(config)
        headers = client._request_headers()
        self.assertEqual(headers["HTTP-Referer"], "https://myapp.com")
        self.assertEqual(headers["X-Title"], "MyApp")

    @patch("siglab.llm.claude.resolve_llm_provider")
    @patch("siglab.llm.claude.resolve_llm_api_key")
    def test_openrouter_skips_empty_referer(
        self, mock_key: MagicMock, mock_provider: MagicMock
    ) -> None:
        mock_key.return_value = "or-key"
        mock_provider.return_value = "openrouter"
        config = _minimal_config(openrouter_http_referer="", openrouter_title="")
        client = ClaudeClient(config)
        headers = client._request_headers()
        self.assertNotIn("HTTP-Referer", headers)
        self.assertNotIn("X-Title", headers)


# ─── _assistant_tool_call_message ─────────────────────────────────────────


class AssistantToolCallMessageTests(unittest.TestCase):
    @patch("siglab.llm.claude.resolve_llm_provider")
    def test_basic_message(self, mock_provider: MagicMock) -> None:
        mock_provider.return_value = "bai"
        client = ClaudeClient(_minimal_config())
        msg = {"role": "assistant", "content": "", "tool_calls": [{"id": "call_1", "function": {"name": "f"}}]}
        result = client._assistant_tool_call_message(msg)
        self.assertEqual(result["role"], "assistant")
        self.assertEqual(result["content"], "")
        self.assertEqual(len(result["tool_calls"]), 1)

    @patch("siglab.llm.claude.resolve_llm_provider")
    def test_reasoning_content_included_for_supported_providers(
        self, mock_provider: MagicMock
    ) -> None:
        mock_provider.return_value = "bai"
        client = ClaudeClient(_minimal_config())
        msg = {"role": "assistant", "content": "", "reasoning_content": "thinking...", "tool_calls": []}
        result = client._assistant_tool_call_message(msg)
        self.assertEqual(result["reasoning_content"], "thinking...")

    @patch("siglab.llm.claude.resolve_llm_provider")
    def test_reasoning_content_excluded_for_unsupported_providers(
        self, mock_provider: MagicMock
    ) -> None:
        mock_provider.return_value = "openrouter"
        client = ClaudeClient(_minimal_config())
        msg = {"role": "assistant", "content": "", "reasoning_content": "thinking...", "tool_calls": []}
        result = client._assistant_tool_call_message(msg)
        self.assertNotIn("reasoning_content", result)


# ─── _record_assistant_message ────────────────────────────────────────────


class RecordAssistantMessageTests(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.client = ClaudeClient(_minimal_config())

    @patch("siglab.llm.claude.resolve_llm_provider")
    def test_records_in_last_exchange(
        self, mock_provider: MagicMock
    ) -> None:
        mock_provider.return_value = "bai"
        self.client.last_exchange = {"system_prompt": "sys", "messages": []}
        self.client._record_assistant_message(
            message={"content": "hello", "role": "assistant"},
            finish_reason="stop",
        )
        self.assertIn("assistant_messages", self.client.last_exchange)
        turns = self.client.last_exchange["assistant_messages"]
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["finish_reason"], "stop")

    @patch("siglab.llm.claude.resolve_llm_provider")
    def test_records_multiple_turns(
        self, mock_provider: MagicMock
    ) -> None:
        mock_provider.return_value = "bai"
        self.client.last_exchange = {"system_prompt": "sys", "messages": []}
        self.client._record_assistant_message(
            message={"content": "first"},
            finish_reason="tool_use",
        )
        self.client._record_assistant_message(
            message={"content": "second"},
            finish_reason="stop",
        )
        self.assertEqual(
            len(self.client.last_exchange["assistant_messages"]), 2
        )

    @patch("siglab.llm.claude.resolve_llm_provider")
    def test_skips_when_no_last_exchange(
        self, mock_provider: MagicMock
    ) -> None:
        mock_provider.return_value = "bai"
        self.client.last_exchange = None
        # Should not raise
        self.client._record_assistant_message(
            message={"content": "hello"},
            finish_reason="stop",
        )

    @patch("siglab.llm.claude.resolve_llm_provider")
    def test_trace_has_reasoning_content_preview(
        self, mock_provider: MagicMock
    ) -> None:
        mock_provider.return_value = "bai"
        self.client.last_trace = {"provider": "bai"}
        self.client._record_assistant_message(
            message={"content": "hello", "reasoning_content": "x" * 100},
            finish_reason="stop",
        )
        trace_turn = self.client.last_trace["assistant_turns"][0]
        self.assertTrue(trace_turn["has_reasoning_content"])
        self.assertIsNotNone(trace_turn["reasoning_content_preview"])

    @patch("siglab.llm.claude.resolve_llm_provider")
    def test_trace_no_reasoning_content(
        self, mock_provider: MagicMock
    ) -> None:
        mock_provider.return_value = "bai"
        self.client.last_trace = {"provider": "bai"}
        self.client._record_assistant_message(
            message={"content": "hello"},
            finish_reason="stop",
        )
        trace_turn = self.client.last_trace["assistant_turns"][0]
        self.assertFalse(trace_turn["has_reasoning_content"])
        self.assertIsNone(trace_turn["reasoning_content_preview"])

    @patch("siglab.llm.claude.resolve_llm_provider")
    def test_trace_tool_call_counts(
        self, mock_provider: MagicMock
    ) -> None:
        mock_provider.return_value = "bai"
        self.client.last_trace = {"provider": "bai"}
        self.client._record_assistant_message(
            message={
                "content": "",
                "tool_calls": [{"id": "c1"}, {"id": "c2"}],
            },
            finish_reason="tool_use",
        )
        trace_turn = self.client.last_trace["assistant_turns"][0]
        self.assertTrue(trace_turn["has_tool_calls"])
        self.assertEqual(trace_turn["tool_call_count"], 2)


if __name__ == "__main__":
    unittest.main()
