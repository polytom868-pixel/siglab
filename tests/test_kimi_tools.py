from __future__ import annotations

import unittest
from pathlib import Path

from siglab.llm import ClaudeClient, ClaudeTool
from siglab.settings import SiglabConfig


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
    ) -> dict:
        self.payloads.append(payload)
        if not self.responses:
            raise AssertionError("No scripted responses left")
        return self.responses.pop(0)


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


if __name__ == "__main__":
    unittest.main()


