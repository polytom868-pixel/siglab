#!/usr/bin/env python3
"""Brute-force 10 free OpenRouter models with 3 call types each via raw HTTP.
Captures status, latency, content, usage, cost, tool_calls. Saves per-model JSON."""
import json
import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

API_KEY = "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"
CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
OUT = Path("/home/eya/soso/siglab/agent_workspace/audit_raw_P1")
OUT.mkdir(parents=True, exist_ok=True)

MODELS = [
    "meta-llama/llama-3.3-70b-instruct:free",
    "qwen/qwen3-coder:free",
    "qwen/qwen3-next-80b-a3b-instruct:free",
    "openai/gpt-oss-120b:free",
    "openai/gpt-oss-20b:free",
    "nvidia/nemotron-nano-9b-v2:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "google/gemma-4-31b-it:free",
    "liquid/lfm-2.5-1.2b-instruct:free",
    "nvidia/nemotron-nano-12b-v2-vl:free",
]

TOOL = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Return the current weather in a city.",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "City name"}
                },
                "required": ["city"],
            },
        }
    }
]

PROMPTS = {
    "basic": {
        "model": None,  # filled per call
        "messages": [{"role": "user", "content": "Reply with exactly the word PONG and nothing else."}],
        "max_tokens": 64,
        "usage": {"include": True},
    },
    "system": {
        "model": None,
        "messages": [
            {"role": "system", "content": "You always answer in haiku (3 lines, 5-7-5 syllable pattern)."},
            {"role": "user", "content": "Tell me about the moon."},
        ],
        "max_tokens": 128,
        "usage": {"include": True},
    },
    "tool": {
        "model": None,
        "messages": [{"role": "user", "content": "What's the weather in Tokyo?"}],
        "tools": TOOL,
        "tool_choice": "auto",
        "max_tokens": 200,
        "usage": {"include": True},
    },
}


def post(payload, timeout=90, _retries=4):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        CHAT_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://siglab.local/audit",
            "X-Title": "siglab-bruteforce",
        },
        method="POST",
    )
    started = time.perf_counter()
    last = None
    for attempt in range(_retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                text = resp.read().decode("utf-8", errors="replace")
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                return {
                    "ok": True,
                    "status": int(resp.status),
                    "headers": dict(resp.headers),
                    "text": text,
                    "elapsed_ms": round(elapsed_ms, 1),
                }
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", errors="replace")
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            last = {
                "ok": False,
                "status": int(e.code),
                "headers": dict(e.headers),
                "text": text,
                "elapsed_ms": round(elapsed_ms, 1),
            }
            if int(e.code) == 429 and attempt < _retries - 1:
                # honor Retry-After when present
                ra = e.headers.get("retry-after")
                wait = float(ra) if ra and ra.replace(".", "").isdigit() else 6.0
                time.sleep(wait)
                continue
            return last
        except (TimeoutError, urllib.error.URLError, OSError) as e:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            return {
                "ok": False,
                "status": None,
                "headers": {},
                "text": f"{type(e).__name__}: {e}",
                "elapsed_ms": round(elapsed_ms, 1),
            }
    return last

def summarize(parsed, raw_text):
    """Return a flat dict of the relevant response fields."""
    out = {"raw": raw_text[:1500]}
    if not isinstance(parsed, dict):
        out["parse_error"] = True
        return out
    out["id"] = parsed.get("id")
    out["model_returned"] = parsed.get("model")
    usage = parsed.get("usage") or {}
    out["usage"] = usage
    out["usage_cost"] = usage.get("cost") if isinstance(usage, dict) else None
    out["usage_prompt"] = usage.get("prompt_tokens") if isinstance(usage, dict) else None
    out["usage_completion"] = usage.get("completion_tokens") if isinstance(usage, dict) else None
    out["usage_total"] = usage.get("total_tokens") if isinstance(usage, dict) else None
    out["usage_native"] = usage.get("native_tokens") if isinstance(usage, dict) else None
    out["usage_cost_details"] = usage.get("cost_details") if isinstance(usage, dict) else None
    choices = parsed.get("choices") or []
    if not choices:
        out["choices_count"] = 0
        out["finish_reason"] = None
        out["content_type"] = None
        out["content_preview"] = None
        out["content_text"] = None
        out["tool_calls"] = None
        return out
    out["choices_count"] = len(choices)
    c0 = choices[0] or {}
    out["finish_reason"] = c0.get("finish_reason")
    msg = c0.get("message") or {}
    content = msg.get("content")
    if isinstance(content, str):
        out["content_type"] = "str"
        out["content_text"] = content
        out["content_preview"] = content[:400]
    elif isinstance(content, list):
        pieces = []
        for item in content:
            if isinstance(item, dict):
                pieces.append({k: v for k, v in item.items() if k in ("type", "text", "image_url")})
        out["content_type"] = "list"
        out["content_text"] = json.dumps(pieces)[:400]
        out["content_preview"] = out["content_text"]
    elif content is None:
        out["content_type"] = "None"
        out["content_text"] = None
        out["content_preview"] = None
    else:
        out["content_type"] = type(content).__name__
        out["content_text"] = str(content)[:400]
        out["content_preview"] = out["content_text"]
    tc = msg.get("tool_calls")
    if isinstance(tc, list) and tc:
        tcs = []
        for call in tc:
            if not isinstance(call, dict):
                tcs.append({"raw": str(call)[:200]})
                continue
            fn = call.get("function") or {}
            tcs.append({
                "id": call.get("id"),
                "type": call.get("type"),
                "function_name": fn.get("name") if isinstance(fn, dict) else None,
                "function_arguments": fn.get("arguments") if isinstance(fn, dict) else None,
                "function_arguments_parsed": (
                    json.loads(fn.get("arguments"))
                    if isinstance(fn, dict) and isinstance(fn.get("arguments"), str)
                    else None
                ),
            })
        out["tool_calls"] = tcs
        out["tool_call_count"] = len(tcs)
    else:
        out["tool_calls"] = []
        out["tool_call_count"] = 0
    # Reasoning content (OpenAI reasoning models)
    if "reasoning_content" in msg:
        out["reasoning_content_present"] = True
        out["reasoning_content_preview"] = str(msg.get("reasoning_content") or "")[:400]
    if "reasoning" in msg:
        out["reasoning_field_present"] = True
        out["reasoning_field_preview"] = str(msg.get("reasoning") or "")[:400]
    return out


def run_model(model):
    record = {"model": model, "calls": {}}
    for call_name, base in PROMPTS.items():
        payload = dict(base)
        payload["model"] = model
        if call_name != "tool":
            payload.pop("tools", None)
            payload.pop("tool_choice", None)
        if call_name != "basic":
            pass  # keep system as-is
        resp = post(payload, timeout=90)
        parsed = None
        parse_err = None
        try:
            parsed = json.loads(resp["text"])
        except (ValueError, TypeError) as e:
            parse_err = str(e)
        if parsed is not None:
            record["calls"][call_name] = {
                "elapsed_ms": resp["elapsed_ms"],
                "status": resp["status"],
                "summary": summarize(parsed, resp["text"]),
            }
        else:
            record["calls"][call_name] = {
                "elapsed_ms": resp["elapsed_ms"],
                "status": resp["status"],
                "summary": {"parse_error": parse_err, "raw": resp["text"][:1500]},
            }
    out_path = OUT / f"{model.replace('/', '__').replace(':', '_')}.json"
    out_path.write_text(json.dumps(record, indent=2, ensure_ascii=False))
    return record


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else None
    delay = float(os.environ.get("MODEL_DELAY_S", "6"))
    summary = []
    for m in MODELS:
        if target and target not in m:
            continue
        print(f"=== {m} ===", flush=True)
        rec = run_model(m)
        for cn, c in rec["calls"].items():
            s = c["summary"]
            tcmt = s.get("tool_call_count")
            cost = s.get("usage_cost")
            print(
                f"  {cn}: status={c['status']} latency_ms={c['elapsed_ms']} "
                f"finish={s.get('finish_reason')} tool_calls={tcmt} "
                f"cost={cost} content_preview={(s.get('content_preview') or '')[:80]!r}",
                flush=True,
            )
        summary.append(rec)
        time.sleep(delay)
    (OUT / "_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
