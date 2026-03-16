from __future__ import annotations

import unittest
from pathlib import Path

from wayfinder_autolab.llm import KimiClient, KimiTool
from wayfinder_autolab.settings import AutolabSettings


class ScriptedKimiClient(KimiClient):
    def __init__(self, settings: AutolabSettings, responses: list[dict]) -> None:
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


class KimiToolCallingTests(unittest.IsolatedAsyncioTestCase):
    async def test_complete_json_with_tools_runs_tool_loop(self) -> None:
        settings = AutolabSettings(
            root_dir=Path("/tmp"),
            wayfinder_config_path=Path("/tmp/config.json"),
            generated_strategy_dir=Path("/tmp/generated_strategies"),
            data_lake_dir=Path("/tmp"),
            artifact_dir=Path("/tmp"),
            live_dir=Path("/tmp/live"),
            lineage_db_path=Path("/tmp/autolab_test.db"),
            wayfinder_api_key_override=None,
            kimi_api_key="sk-test",
            kimi_model="kimi-k2.5",
            kimi_base_url="https://api.moonshot.ai/v1",
            kimi_max_tokens=1024,
            kimi_temperature=1.0,
            kimi_top_p=0.95,
            kimi_timeout_s=30.0,
            kimi_thinking="enabled",
            kimi_max_tool_rounds=3,
            population_size=1,
        )
        client = ScriptedKimiClient(
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
                                "content": "{\"candidates\":[]}",
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
                KimiTool(
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

        self.assertEqual(payload, {"candidates": []})
        self.assertEqual(seen_arguments, [{"topic": "carry"}])
        self.assertEqual(client.last_trace["tool_rounds_used"], 1)
        self.assertEqual(client.last_trace["tool_calls"][0]["name"], "lookup")
        self.assertEqual(client.last_exchange["system_prompt"], "test")
        self.assertEqual(client.last_exchange["user_prompt"], "return json")
        self.assertEqual(client.last_exchange["parsed_output"], {"candidates": []})
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


if __name__ == "__main__":
    unittest.main()
