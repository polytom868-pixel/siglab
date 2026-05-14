from __future__ import annotations

import json
import unittest
from pathlib import Path

import httpx

from siglab.llm import ClaudeClient, ClaudeTool
from siglab.llm.claude import LLMFormatError, LLMQuotaError
from siglab.config import SiglabConfig


class ScriptedClaudeClient(ClaudeClient):
    def __init__(self, settings: SiglabConfig, responses: list[dict]) -> None:
        super().__init__(settings)
        self.responses = list(responses)
        self.payloads: list[dict] = []

    async def _chat_completion(
        self,
        *,
        payload: dict,
        timeout_s: float | None,
        stage: str | None = None,
    ) -> dict:
        self.payloads.append(payload)
        if not self.responses:
            raise AssertionError("No scripted responses left")
        return self.responses.pop(0)


class MockHttpClaudeClient(ClaudeClient):
    def __init__(self, settings: SiglabConfig, handler) -> None:
        super().__init__(settings)
        self._mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    def _http(self, *, timeout_s: float | None) -> httpx.AsyncClient:
        return self._mock_client


class ClaudeToolCallingTests(unittest.IsolatedAsyncioTestCase):
    async def test_complete_json_with_tools_runs_tool_loop(self) -> None:
        settings = SiglabConfig(
            root_dir=Path("/tmp"),
            sosovalue_config_path=Path("/tmp/config.json"),
            generated_strategy_dir=Path("/tmp/deployed_agents"),
            data_lake_dir=Path("/tmp"),
            artifact_dir=Path("/tmp"),
            live_dir=Path("/tmp/live"),
            ancestry_db_path=Path("/tmp/siglab_test.db"),
            sosovalue_api_key_override=None,
            claude_api_key="sk-test",
            claude_model="claude-k2.5",
            claude_base_url="https://api.moonshot.ai/v1",
            claude_max_tokens=1024,
            claude_temperature=1.0,
            claude_top_p=0.95,
            claude_timeout_s=30.0,
            claude_thinking="enabled",
            claude_max_tool_rounds=3,
            population_size=1,
        )
        client = ScriptedClaudeClient(
            settings,
            responses=[
                {
                    "choices": [
                        {
                            "finish_reason": "tool_calls",
                            "message": {
                                "role": "assistant",
                                "content": "",
                                "reasoning_content": "use the tool",
                                "tool_calls": [
                                    {
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {
                                            "name": "lookup",
                                            "arguments": "{\"topic\":\"carry\"}",
                                        },
                                    }
                                ],
                            },
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": "{\"specs\":[]}",
                            },
                        }
                    ]
                },
            ],
        )

        seen_arguments: list[dict] = []

        async def lookup(arguments: dict) -> dict:
            seen_arguments.append(arguments)
            return {"ok": True, "topic": arguments["topic"]}

        payload = await client.complete_json_with_tools(
            system_prompt="test",
            user_prompt="return json",
            tools=[
                ClaudeTool(
                    name="lookup",
                    description="test tool",
                    parameters={
                        "type": "object",
                        "properties": {"topic": {"type": "string"}},
                        "required": ["topic"],
                    },
                    handler=lookup,
                )
            ],
        )

        self.assertEqual(payload, {"specs": []})
        self.assertEqual(seen_arguments, [{"topic": "carry"}])
        self.assertEqual(client.last_trace["tool_rounds_used"], 1)
        self.assertEqual(client.last_trace["tool_calls"][0]["name"], "lookup")
        self.assertEqual(client.last_exchange["system_prompt"], "test")
        self.assertEqual(client.last_exchange["user_prompt"], "return json")
        self.assertEqual(client.last_exchange["parsed_output"], {"specs": []})
        self.assertEqual(
            client.last_exchange["assistant_messages"][0]["message"]["reasoning_content"],
            "use the tool",
        )
        self.assertEqual(
            client.last_exchange["assistant_messages"][0]["message"]["tool_calls"][0],
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "lookup",
                    "arguments": "{\"topic\":\"carry\"}",
                },
            },
        )
        self.assertTrue(client.last_trace["assistant_turns"][0]["has_reasoning_content"])
        self.assertEqual(client.last_trace["assistant_turns"][0]["tool_call_count"], 1)
        self.assertEqual(client.payloads[0]["tool_choice"], "auto")
        self.assertEqual(client.payloads[0]["thinking"], {"type": "enabled"})
        self.assertEqual(client.payloads[1]["messages"][-1]["role"], "tool")
        self.assertEqual(client.payloads[1]["messages"][-1]["name"], "lookup")
        self.assertEqual(
            client.payloads[1]["messages"][-2]["reasoning_content"],
            "use the tool",
        )

    async def test_tool_loop_forces_final_answer_when_budget_exhausted(self) -> None:
        settings = SiglabConfig(
            root_dir=Path("/tmp"),
            sosovalue_config_path=Path("/tmp/config.json"),
            generated_strategy_dir=Path("/tmp/deployed_agents"),
            data_lake_dir=Path("/tmp"),
            artifact_dir=Path("/tmp"),
            live_dir=Path("/tmp/live"),
            ancestry_db_path=Path("/tmp/siglab_test.db"),
            sosovalue_api_key_override=None,
            claude_api_key="sk-test",
            claude_model="claude-k2.5",
            claude_base_url="https://api.moonshot.ai/v1",
            claude_max_tokens=1024,
            claude_temperature=1.0,
            claude_top_p=0.95,
            claude_timeout_s=30.0,
            claude_max_tool_rounds=3,
            population_size=1,
        )
        client = ScriptedClaudeClient(
            settings,
            responses=[
                {
                    "choices": [
                        {
                            "finish_reason": "tool_calls",
                            "message": {
                                "role": "assistant",
                                "content": "",
                                "tool_calls": [
                                    {
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {"name": "lookup", "arguments": "{}"},
                                    }
                                ],
                            },
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": "final without tools"},
                        }
                    ]
                },
            ],
        )

        result = await client.complete_text_with_tools(
            system_prompt="test",
            user_prompt="answer",
            tools=[
                ClaudeTool(
                    name="lookup",
                    description="test tool",
                    parameters={"type": "object", "properties": {}},
                    handler=lambda _args: {"ok": True},
                )
            ],
            max_tool_rounds=0,
        )

        self.assertEqual(result, "final without tools")
        self.assertEqual(client.last_trace["error"], "max_tool_rounds_exhausted_forced_final")
        self.assertNotIn("tools", client.payloads[1])
        self.assertIn("Tool budget exhausted", client.payloads[1]["messages"][-1]["content"])

    def test_deepseek_provider_switches_chat_and_reasoner_from_thinking_override(self) -> None:
        settings = SiglabConfig(
            root_dir=Path("/tmp"),
            sosovalue_config_path=Path("/tmp/config.json"),
            generated_strategy_dir=Path("/tmp/deployed_agents"),
            data_lake_dir=Path("/tmp"),
            artifact_dir=Path("/tmp"),
            live_dir=Path("/tmp/live"),
            ancestry_db_path=Path("/tmp/siglab_test.db"),
            sosovalue_api_key_override=None,
            claude_api_key=None,
            claude_model="claude-k2.5",
            claude_base_url="https://api.moonshot.ai/v1",
            claude_max_tokens=1024,
            claude_temperature=1.0,
            claude_top_p=0.95,
            claude_timeout_s=30.0,
            population_size=1,
            llm_provider="deepseek",
            deepseek_api_key="sk-test",
            deepseek_base_url="https://api.deepseek.com",
            deepseek_model="deepseek-reasoner",
        )
        client = ClaudeClient(settings)

        enabled_payload = client._build_payload(
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=None,
            tools=[],
            json_mode=False,
            thinking_override="enabled",
        )
        disabled_payload = client._build_payload(
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=None,
            tools=[],
            json_mode=False,
            thinking_override="disabled",
        )

        self.assertEqual(client.provider_name, "deepseek")
        self.assertEqual(enabled_payload["model"], "deepseek-reasoner")
        self.assertEqual(disabled_payload["model"], "deepseek-chat")
        self.assertNotIn("thinking", enabled_payload)
        self.assertNotIn("thinking", disabled_payload)

    def test_openrouter_provider_switches_models_and_uses_headers(self) -> None:
        settings = SiglabConfig(
            root_dir=Path("/tmp"),
            sosovalue_config_path=Path("/tmp/config.json"),
            generated_strategy_dir=Path("/tmp/deployed_agents"),
            data_lake_dir=Path("/tmp"),
            artifact_dir=Path("/tmp"),
            live_dir=Path("/tmp/live"),
            ancestry_db_path=Path("/tmp/siglab_test.db"),
            sosovalue_api_key_override=None,
            claude_api_key=None,
            claude_model="claude-k2.5",
            claude_base_url="https://api.moonshot.ai/v1",
            claude_max_tokens=1024,
            claude_temperature=1.0,
            claude_top_p=0.95,
            claude_timeout_s=30.0,
            population_size=1,
            llm_provider="openrouter",
            openrouter_api_key="sk-test",
            openrouter_base_url="https://openrouter.ai/api/v1",
            openrouter_model="openai/gpt-4.1-mini",
            openrouter_reasoning_model="z-ai/glm-5",
            openrouter_fast_model="openai/gpt-4.1",
            openrouter_http_referer="https://strategies.sosovalue.ai/",
            openrouter_title="SoSoValue SigLab",
        )
        client = ClaudeClient(settings)

        enabled_payload = client._build_payload(
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=None,
            tools=[],
            json_mode=False,
            thinking_override="enabled",
        )
        disabled_payload = client._build_payload(
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=None,
            tools=[],
            json_mode=False,
            thinking_override="disabled",
        )
        headers = client._request_headers()

        self.assertEqual(client.provider_name, "openrouter")
        self.assertEqual(enabled_payload["model"], "z-ai/glm-5")
        self.assertEqual(disabled_payload["model"], "openai/gpt-4.1")
        self.assertNotIn("thinking", enabled_payload)
        self.assertNotIn("thinking", disabled_payload)
        self.assertEqual(headers["HTTP-Referer"], "https://strategies.sosovalue.ai/")
        self.assertEqual(headers["X-Title"], "SoSoValue SigLab")

    def test_bai_tool_replay_preserves_reasoning_content(self) -> None:
        settings = SiglabConfig(
            root_dir=Path("/tmp"),
            sosovalue_config_path=Path("/tmp/config.json"),
            generated_strategy_dir=Path("/tmp/deployed_agents"),
            data_lake_dir=Path("/tmp"),
            artifact_dir=Path("/tmp"),
            live_dir=Path("/tmp/live"),
            ancestry_db_path=Path("/tmp/siglab_test.db"),
            sosovalue_api_key_override=None,
            claude_api_key=None,
            claude_model="claude-k2.5",
            claude_base_url="https://api.moonshot.ai/v1",
            claude_max_tokens=1024,
            claude_temperature=1.0,
            claude_top_p=0.95,
            claude_timeout_s=30.0,
            population_size=1,
            llm_provider="bai",
            bai_api_key="sk-test",
            bai_base_url="https://api.b.ai",
            bai_model="deepseek-v4-flash",
        )
        client = ClaudeClient(settings)

        replay = client._assistant_tool_call_message(
            {
                "role": "assistant",
                "content": "",
                "reasoning_content": "tool rationale",
                "tool_calls": [{"id": "call_1", "type": "function"}],
            }
        )

        self.assertEqual(replay["reasoning_content"], "tool rationale")

    def test_bai_latency_demotes_writer_and_reflector_candidates_only(self) -> None:
        settings = SiglabConfig(
            root_dir=Path("/tmp"),
            sosovalue_config_path=Path("/tmp/config.json"),
            generated_strategy_dir=Path("/tmp/deployed_agents"),
            data_lake_dir=Path("/tmp"),
            artifact_dir=Path("/tmp"),
            live_dir=Path("/tmp/live"),
            ancestry_db_path=Path("/tmp/siglab_test.db"),
            sosovalue_api_key_override=None,
            claude_api_key=None,
            claude_model="claude-k2.5",
            claude_base_url="https://api.moonshot.ai/v1",
            claude_max_tokens=1024,
            claude_temperature=1.0,
            claude_top_p=0.95,
            claude_timeout_s=30.0,
            population_size=1,
            llm_provider="bai",
            bai_api_key="sk-test",
            bai_base_url="https://api.b.ai",
            bai_model="slow-model",
            bai_writer_model="slow-model",
            bai_planner_model="slow-model",
            bai_fallback_fast_model="fast-model",
            bai_fallback_reasoning_model="reasoning-model",
        )
        client = ClaudeClient(settings)
        client.routing_policy.record_latency(
            model="slow-model",
            stage="writer",
            elapsed_ms=20_000.0,
        )

        self.assertEqual(
            client.routing_policy.candidates(provider="bai", stage="writer", primary="slow-model"),
            ["fast-model", "reasoning-model"],
        )
        self.assertEqual(
            client.routing_policy.candidates(provider="bai", stage="planner", primary="slow-model")[0],
            "slow-model",
        )
        self.assertIn("slow-model", client.routing_policy.snapshot()["latency_demoted"])

    def test_bai_latency_demotion_does_not_remove_last_viable_writer_model(self) -> None:
        settings = SiglabConfig(
            root_dir=Path("/tmp"),
            sosovalue_config_path=Path("/tmp/config.json"),
            generated_strategy_dir=Path("/tmp/deployed_agents"),
            data_lake_dir=Path("/tmp"),
            artifact_dir=Path("/tmp"),
            live_dir=Path("/tmp/live"),
            ancestry_db_path=Path("/tmp/siglab_test.db"),
            sosovalue_api_key_override=None,
            claude_api_key=None,
            claude_model="claude-k2.5",
            claude_base_url="https://api.moonshot.ai/v1",
            claude_max_tokens=1024,
            claude_temperature=1.0,
            claude_top_p=0.95,
            claude_timeout_s=30.0,
            population_size=1,
            llm_provider="bai",
            bai_api_key="sk-test",
            bai_base_url="https://api.b.ai",
            bai_model="slow-model",
            bai_writer_model="slow-model",
            bai_fallback_fast_model="quota-model",
            bai_fallback_reasoning_model="unavailable-model",
        )
        client = ClaudeClient(settings)
        client.routing_policy.record_latency(
            model="slow-model",
            stage="writer",
            elapsed_ms=20_000.0,
        )
        client.routing_policy.mark_quota_failure("quota-model", "LLMQuotaError")
        client.routing_policy.mark_auth_failure("unavailable-model", "LLMAuthError")

        self.assertEqual(
            client.routing_policy.candidates(provider="bai", stage="writer", primary="slow-model"),
            ["slow-model"],
        )

    async def test_bai_entitlement_failure_blacklists_model_and_falls_back(self) -> None:
        settings = SiglabConfig(
            root_dir=Path("/tmp"),
            sosovalue_config_path=Path("/tmp/config.json"),
            generated_strategy_dir=Path("/tmp/deployed_agents"),
            data_lake_dir=Path("/tmp"),
            artifact_dir=Path("/tmp"),
            live_dir=Path("/tmp/live"),
            ancestry_db_path=Path("/tmp/siglab_test.db"),
            sosovalue_api_key_override=None,
            claude_api_key=None,
            claude_model="claude-k2.5",
            claude_base_url="https://api.moonshot.ai/v1",
            claude_max_tokens=1024,
            claude_temperature=1.0,
            claude_top_p=0.95,
            claude_timeout_s=30.0,
            population_size=1,
            llm_provider="bai",
            bai_api_key="sk-test",
            bai_base_url="https://api.b.ai",
            bai_model="claude-sonnet-4-6",
            bai_planner_model="claude-sonnet-4-6",
            bai_fallback_fast_model="deepseek-v4-flash",
            bai_fallback_reasoning_model="kimi-k2.5",
        )
        seen_models: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            seen_models.append(str(payload["model"]))
            if payload["model"] == "claude-sonnet-4-6":
                return httpx.Response(403, json={"error": {"message": "access denied"}})
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "{\"ok\":true}"}, "finish_reason": "stop"}]},
            )

        client = MockHttpClaudeClient(settings, handler)
        try:
            payload = await client.complete_json(
                system_prompt="Return JSON.",
                user_prompt="Return ok.",
                json_mode=True,
                thinking_override="disabled",
                stage="planner",
            )
        finally:
            await client.close()

        self.assertEqual(payload, {"ok": True})
        self.assertEqual(seen_models, ["claude-sonnet-4-6", "deepseek-v4-flash"])
        self.assertIn("claude-sonnet-4-6", client.metrics_snapshot()["routing_policy"]["unavailable"])

    async def test_bai_quota_failure_blocks_model_and_uses_next_candidate(self) -> None:
        settings = SiglabConfig(
            root_dir=Path("/tmp"),
            sosovalue_config_path=Path("/tmp/config.json"),
            generated_strategy_dir=Path("/tmp/deployed_agents"),
            data_lake_dir=Path("/tmp"),
            artifact_dir=Path("/tmp"),
            live_dir=Path("/tmp/live"),
            ancestry_db_path=Path("/tmp/siglab_test.db"),
            sosovalue_api_key_override=None,
            claude_api_key=None,
            claude_model="claude-k2.5",
            claude_base_url="https://api.moonshot.ai/v1",
            claude_max_tokens=1024,
            claude_temperature=1.0,
            claude_top_p=0.95,
            claude_timeout_s=30.0,
            population_size=1,
            llm_provider="bai",
            bai_api_key="sk-test",
            bai_base_url="https://api.b.ai",
            bai_model="gpt-5.2",
            bai_writer_model="gpt-5.2",
            bai_fallback_fast_model="deepseek-v4-flash",
            bai_fallback_reasoning_model="kimi-k2.5",
        )
        seen_models: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            seen_models.append(str(payload["model"]))
            if payload["model"] == "gpt-5.2":
                return httpx.Response(402, text='{"error":"insufficient_user_quota"}')
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "{\"ok\":true}"}, "finish_reason": "stop"}]},
            )

        client = MockHttpClaudeClient(settings, handler)
        try:
            payload = await client.complete_json(
                system_prompt="Return JSON.",
                user_prompt="Return ok.",
                json_mode=True,
                thinking_override="disabled",
                stage="writer",
            )
        finally:
            await client.close()

        self.assertEqual(payload, {"ok": True})
        self.assertEqual(seen_models, ["gpt-5.2", "deepseek-v4-flash"])
        self.assertIn("gpt-5.2", client.metrics_snapshot()["routing_policy"]["quota_blocked"])

    async def test_bai_credit_wording_is_classified_as_quota_failure(self) -> None:
        settings = SiglabConfig(
            root_dir=Path("/tmp"),
            sosovalue_config_path=Path("/tmp/config.json"),
            generated_strategy_dir=Path("/tmp/deployed_agents"),
            data_lake_dir=Path("/tmp"),
            artifact_dir=Path("/tmp"),
            live_dir=Path("/tmp/live"),
            ancestry_db_path=Path("/tmp/siglab_test.db"),
            sosovalue_api_key_override=None,
            claude_api_key=None,
            claude_model="claude-k2.5",
            claude_base_url="https://api.moonshot.ai/v1",
            claude_max_tokens=1024,
            claude_temperature=1.0,
            claude_top_p=0.95,
            claude_timeout_s=30.0,
            population_size=1,
            llm_provider="bai",
            bai_api_key="sk-test",
            bai_base_url="https://api.b.ai",
            bai_model="expensive-model",
            bai_writer_model="expensive-model",
            bai_fallback_fast_model="deepseek-v4-flash",
        )
        seen_models: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            seen_models.append(str(payload["model"]))
            if payload["model"] == "expensive-model":
                return httpx.Response(402, json={"error": {"message": "Credits exhausted"}})
            return httpx.Response(200, json={"choices": [{"message": {"content": "{\"ok\":true}"}, "finish_reason": "stop"}]})

        client = MockHttpClaudeClient(settings, handler)
        try:
            payload = await client.complete_json(
                system_prompt="Return JSON.",
                user_prompt="Return ok.",
                json_mode=True,
                thinking_override="disabled",
                stage="writer",
            )
        finally:
            await client.close()

        self.assertEqual(payload, {"ok": True})
        self.assertEqual(seen_models, ["expensive-model", "deepseek-v4-flash"])
        self.assertIn("expensive-model", client.metrics_snapshot()["routing_policy"]["quota_blocked"])

    async def test_bai_context_limit_http_error_is_not_retried_as_upstream(self) -> None:
        settings = SiglabConfig(
            root_dir=Path("/tmp"),
            sosovalue_config_path=Path("/tmp/config.json"),
            generated_strategy_dir=Path("/tmp/deployed_agents"),
            data_lake_dir=Path("/tmp"),
            artifact_dir=Path("/tmp"),
            live_dir=Path("/tmp/live"),
            ancestry_db_path=Path("/tmp/siglab_test.db"),
            sosovalue_api_key_override=None,
            claude_api_key=None,
            claude_model="claude-k2.5",
            claude_base_url="https://api.moonshot.ai/v1",
            claude_max_tokens=1024,
            claude_temperature=1.0,
            claude_top_p=0.95,
            claude_timeout_s=30.0,
            population_size=1,
            llm_provider="bai",
            bai_api_key="sk-test",
            bai_base_url="https://api.b.ai",
            bai_model="deepseek-v4-flash",
            bai_writer_model="deepseek-v4-flash",
        )

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"error": {"message": "maximum context length exceeded"}})

        client = MockHttpClaudeClient(settings, handler)
        try:
            with self.assertRaises(LLMFormatError):
                await client.complete_json(
                    system_prompt="Return JSON.",
                    user_prompt="Return ok.",
                    json_mode=True,
                    thinking_override="disabled",
                    stage="writer",
                )
        finally:
            await client.close()

    async def test_metrics_capture_provider_token_usage_without_pricing(self) -> None:
        settings = SiglabConfig(
            root_dir=Path("/tmp"),
            sosovalue_config_path=Path("/tmp/config.json"),
            generated_strategy_dir=Path("/tmp/deployed_agents"),
            data_lake_dir=Path("/tmp"),
            artifact_dir=Path("/tmp"),
            live_dir=Path("/tmp/live"),
            ancestry_db_path=Path("/tmp/siglab_test.db"),
            sosovalue_api_key_override=None,
            claude_api_key=None,
            claude_model="claude-k2.5",
            claude_base_url="https://api.moonshot.ai/v1",
            claude_max_tokens=1024,
            claude_temperature=1.0,
            claude_top_p=0.95,
            claude_timeout_s=30.0,
            population_size=1,
            llm_provider="bai",
            bai_api_key="sk-test",
            bai_base_url="https://api.b.ai",
            bai_model="deepseek-v4-flash",
        )

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "{\"ok\":true}"}, "finish_reason": "stop"}],
                    "usage": {
                        "prompt_tokens": 11,
                        "completion_tokens": 7,
                        "total_tokens": 18,
                        "prompt_tokens_details": {"cached_tokens": 3},
                    },
                },
            )

        client = MockHttpClaudeClient(settings, handler)
        try:
            payload = await client.complete_json(
                system_prompt="Return JSON.",
                user_prompt="Return ok.",
                json_mode=True,
                thinking_override="disabled",
                stage="writer",
            )
        finally:
            await client.close()

        self.assertEqual(payload, {"ok": True})
        usage = client.metrics_snapshot()["usage"]
        self.assertEqual(usage["prompt_tokens"], 11)
        self.assertEqual(usage["completion_tokens"], 7)
        self.assertEqual(usage["total_tokens"], 18)
        self.assertEqual(usage["cache_read_tokens"], 3)
        self.assertEqual(usage["cache_write_tokens"], 0)
        self.assertEqual(usage["credits_estimate"], 3.089)
        self.assertEqual(usage["priced_tokens"], 18)
        self.assertIsNone(usage["cost_usd"])
        self.assertEqual(usage["cost_status"], "verified_bai_credit_estimate_usd_unpriced")
        self.assertEqual(usage["pricing_source"], "https://docs.b.ai/llmservice/pricing-and-usage/")

    async def test_bai_context_pressure_is_reported_and_clamps_default_output(self) -> None:
        settings = SiglabConfig(
            root_dir=Path("/tmp"),
            sosovalue_config_path=Path("/tmp/config.json"),
            generated_strategy_dir=Path("/tmp/deployed_agents"),
            data_lake_dir=Path("/tmp"),
            artifact_dir=Path("/tmp"),
            live_dir=Path("/tmp/live"),
            ancestry_db_path=Path("/tmp/siglab_test.db"),
            sosovalue_api_key_override=None,
            claude_api_key=None,
            claude_model="claude-k2.5",
            claude_base_url="https://api.moonshot.ai/v1",
            claude_max_tokens=4096,
            claude_temperature=1.0,
            claude_top_p=0.95,
            claude_timeout_s=30.0,
            population_size=1,
            llm_provider="bai",
            bai_api_key="sk-test",
            bai_base_url="https://api.b.ai",
            bai_model="deepseek-v4-flash",
            bai_context_tokens=1200,
        )
        client = ScriptedClaudeClient(
            settings,
            responses=[
                {
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": "{\"ok\":true}"},
                        }
                    ]
                }
            ],
        )

        await client.complete_json(
            system_prompt="Return JSON.",
            user_prompt="x" * 5000,
            json_mode=True,
            thinking_override="disabled",
            stage="writer",
        )

        metrics = client.metrics_snapshot()
        pressure = metrics["context_pressure"]["latest"]
        self.assertEqual(metrics["context_pressure"]["event_count"], 1)
        self.assertEqual(pressure["severity"], "critical")
        self.assertIn("requested_output_tokens_after_clamp", pressure)
        self.assertLess(client.payloads[0]["max_tokens"], 4096)

    async def test_bai_pre_call_credit_guard_refuses_oversized_call(self) -> None:
        settings = SiglabConfig(
            root_dir=Path("/tmp"),
            sosovalue_config_path=Path("/tmp/config.json"),
            generated_strategy_dir=Path("/tmp/deployed_agents"),
            data_lake_dir=Path("/tmp"),
            artifact_dir=Path("/tmp"),
            live_dir=Path("/tmp/live"),
            ancestry_db_path=Path("/tmp/siglab_test.db"),
            sosovalue_api_key_override=None,
            claude_api_key=None,
            claude_model="claude-k2.5",
            claude_base_url="https://api.moonshot.ai/v1",
            claude_max_tokens=4096,
            claude_temperature=1.0,
            claude_top_p=0.95,
            claude_timeout_s=30.0,
            population_size=1,
            llm_provider="bai",
            bai_api_key="sk-test",
            bai_base_url="https://api.b.ai",
            bai_model="deepseek-v4-flash",
            bai_max_call_credits=1.0,
        )
        client = ScriptedClaudeClient(settings, responses=[])

        with self.assertRaisesRegex(LLMQuotaError, "estimated call credits"):
            await client.complete_json(
                system_prompt="Return JSON.",
                user_prompt="x" * 5000,
                json_mode=True,
                thinking_override="disabled",
                stage="writer",
            )

        metrics = client.metrics_snapshot()
        pressure = metrics["credit_pressure"]["latest"]
        self.assertEqual(pressure["severity"], "critical")
        self.assertEqual(client.payloads, [])

    async def test_bai_credit_rates_match_current_official_kimi_table(self) -> None:
        settings = SiglabConfig(
            root_dir=Path("/tmp"),
            sosovalue_config_path=Path("/tmp/config.json"),
            generated_strategy_dir=Path("/tmp/deployed_agents"),
            data_lake_dir=Path("/tmp"),
            artifact_dir=Path("/tmp"),
            live_dir=Path("/tmp/live"),
            ancestry_db_path=Path("/tmp/siglab_test.db"),
            sosovalue_api_key_override=None,
            claude_api_key=None,
            claude_model="claude-k2.5",
            claude_base_url="https://api.moonshot.ai/v1",
            claude_max_tokens=1024,
            claude_temperature=1.0,
            claude_top_p=0.95,
            claude_timeout_s=30.0,
            population_size=1,
            llm_provider="bai",
            bai_api_key="sk-test",
            bai_base_url="https://api.b.ai",
            bai_model="kimi-k2.5",
            bai_writer_model="kimi-k2.5",
        )

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "{\"ok\":true}"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                },
            )

        client = MockHttpClaudeClient(settings, handler)
        try:
            await client.complete_json(
                system_prompt="Return JSON.",
                user_prompt="Return ok.",
                json_mode=True,
                thinking_override="disabled",
                stage="writer",
            )
        finally:
            await client.close()

        usage = client.metrics_snapshot()["usage"]
        self.assertEqual(usage["credits_estimate"], 20.9)


if __name__ == "__main__":
    unittest.main()


