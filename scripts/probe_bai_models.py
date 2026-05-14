from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from siglab.config import load_settings
from siglab.llm import ClaudeClient, LLMProviderError


async def _probe_model(model: str) -> dict[str, Any]:
    settings = load_settings()
    settings.bai_model = model
    settings.bai_planner_model = model
    settings.bai_writer_model = model
    settings.bai_reflector_model = model
    client = ClaudeClient(settings)
    started = datetime.now(UTC).isoformat()
    try:
        try:
            payload = await client.complete_json(
                system_prompt="Return only JSON.",
                user_prompt='Return {"ok": true} as JSON.',
                max_tokens=80,
                timeout_s=60,
                json_mode=True,
                thinking_override="disabled",
                stage="probe",
            )
            status = "PASS" if payload.get("ok") is True else "FORMAT_WEAK"
            error = None
        except LLMProviderError as exc:
            status = "FAIL"
            error = {"class": type(exc).__name__, "status_code": exc.status_code, "message": str(exc)[:500]}
        metrics = client.metrics_snapshot()
    finally:
        await client.close()
    return {
        "model": model,
        "started_at": started,
        "status": status,
        "reachable": status != "FAIL" or (error or {}).get("class") not in {"LLMTransportError"},
        "entitled": (error or {}).get("class") != "LLMAuthError",
        "quota_available": (error or {}).get("class") != "LLMQuotaError",
        "too_expensive": (error or {}).get("class") == "LLMQuotaError",
        "error": error,
        "metrics": metrics,
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", default="deepseek-v4-flash,kimi-k2.5,gpt-5.2,deepseek-v4-pro,claude-sonnet-4.6")
    parser.add_argument("--output", default="runs/provider_capabilities/bai_latest.json")
    args = parser.parse_args()
    models = [item.strip() for item in args.models.split(",") if item.strip()]
    rows = []
    for model in models:
        rows.append(await _probe_model(model))
    out = {
        "provider": "bai",
        "created_at": datetime.now(UTC).isoformat(),
        "models": rows,
    }
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2, ensure_ascii=True, default=str))
    print(json.dumps(out, ensure_ascii=True, default=str))


if __name__ == "__main__":
    asyncio.run(main())
