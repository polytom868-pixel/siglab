#!/usr/bin/env python3
"""Drive siglab.llm.llm.ClaudeClient against free OpenRouter models.
Captures: what method does, what exception (if any), what trace fields.
"""
import asyncio
import json
import os
import time
import sys
from dataclasses import replace
from pathlib import Path

os.environ["OPENROUTER_API_KEY"] = "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"
os.environ["LLM_PROVIDER"] = "openrouter"
os.environ.setdefault("SOSOVALUE_API_KEY", "unused-for-this-probe")

from siglab.config import SiglabConfig, load_settings

WORKING_MODELS = [
    "openai/gpt-oss-120b:free",
    "openai/gpt-oss-20b:free",
]

OUT = Path("/home/eya/soso/siglab/agent_workspace/audit_raw_P1")


def make_config(model: str) -> SiglabConfig:
    base = load_settings()
    return replace(
        base,
        llm_provider="openrouter",
        openrouter_api_key=os.environ["OPENROUTER_API_KEY"],
        openrouter_base_url="https://openrouter.ai/api/v1",
        openrouter_model=model,
        openrouter_planner_model=model,
        openrouter_writer_model=model,
        openrouter_reflector_model=model,
        openrouter_reasoning_model=model,
        openrouter_fast_model=model,
        openrouter_max_call_usd=0.0,
        claude_max_tokens=64,
        claude_temperature=0.0,
        claude_max_tool_rounds=2,
        claude_timeout_s=60.0,
    )


async def probe(model: str):
    from siglab.llm.llm import ClaudeClient, ClaudeTool

    settings = make_config(model)
    client = ClaudeClient(settings)

    weather_handler = lambda args: {"ok": True, "temp_c": 22.0, "city": args.get("city", "?")}

    def probe_basic():
        return client.complete_text(
            system_prompt="You always answer with a single word.",
            user_prompt="Reply with PONG",
            max_tokens=24,
            timeout_s=60.0,
            stage="writer",
        )

    def probe_system():
        return client.complete_text(
            system_prompt="You are a haiku poet. Always respond in 5-7-5 syllable haiku.",
            user_prompt="Tell me about the moon.",
            max_tokens=128,
            timeout_s=60.0,
            stage="writer",
        )

    def probe_tool():
        return client.complete_text_with_tools(
            system_prompt="You can call a tool to answer the user.",
            user_prompt="What is the weather in Tokyo?",
            tools=[ClaudeTool(
                name="get_weather",
                description="Return the current weather in a city.",
                parameters={
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
                handler=weather_handler,
            )],
            max_tokens=200,
            timeout_s=60.0,
            stage="writer",
        )

    def probe_json():
        return client.complete_json(
            system_prompt='Return only JSON: {"answer": "<one word>"}',
            user_prompt='Return answer="ping"',
            max_tokens=64,
            timeout_s=60.0,
            json_mode=True,
            stage="writer",
        )

    record = {"model": model, "is_configured": client.is_configured, "provider": client.provider_name, "probes": {}}
    for name, fn in [
        ("complete_text_basic", probe_basic),
        ("complete_text_with_system", probe_system),
        ("complete_text_with_tools", probe_tool),
        ("complete_json_basic", probe_json),
    ]:
        print(f"  → {name}", flush=True)
        started = time.perf_counter()
        probe_record = {
            "ok": None,
            "exception_class": None,
            "exception_message": None,
            "elapsed_ms": None,
            "provider": client.provider_name,
        }
        try:
            result = await fn()
            elapsed = (time.perf_counter() - started) * 1000.0
            probe_record["ok"] = True
            probe_record["elapsed_ms"] = round(elapsed, 1)
            probe_record["result_type"] = type(result).__name__
            if isinstance(result, str):
                probe_record["result_preview"] = result[:300]
            elif isinstance(result, dict):
                probe_record["result_preview"] = json.dumps(result)[:300]
            else:
                probe_record["result_preview"] = str(result)[:300]
        except Exception as e:
            elapsed = (time.perf_counter() - started) * 1000.0
            probe_record["ok"] = False
            probe_record["elapsed_ms"] = round(elapsed, 1)
            probe_record["exception_class"] = type(e).__name__
            probe_record["exception_module"] = type(e).__module__
            probe_record["exception_message"] = str(e)[:500]
            probe_record["exception_bases"] = [b.__name__ for b in type(e).__mro__]
        probe_record["last_trace"] = client.last_trace
        probe_record["last_exchange_keys"] = (
            list(client.last_exchange.keys()) if client.last_exchange else None
        )
        record["probes"][name] = probe_record
    record["metrics_snapshot"] = client.metrics_snapshot()
    return record


async def main():
    out = []
    for m in WORKING_MODELS:
        print(f"=== ClaudeClient probe: {m} ===", flush=True)
        rec = await probe(m)
        out.append(rec)
        for pname, p in rec["probes"].items():
            print(
                f"  {pname}: ok={p['ok']} exc={p['exception_class']} "
                f"latency_ms={p['elapsed_ms']} "
                f"result_preview={(p.get('result_preview') or '')[:80]!r}",
                flush=True,
            )
    (OUT / "_claude_client_summary.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False)
    )


if __name__ == "__main__":
    asyncio.run(main())
