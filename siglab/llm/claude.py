from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, cast
from collections.abc import Awaitable, Callable, Sequence

import httpx

from siglab.config import SiglabConfig
from siglab.llm.policy import LLMRoutingPolicy
from siglab.llm_metadata import (
    resolve_llm_api_key,
    resolve_llm_base_url,
    resolve_llm_model,
    resolve_llm_provider,
    resolve_llm_thinking_mode,
)
from siglab.utils import (
    _compact_scalar,
    _estimate_message_tokens,
    int_or_zero,
    safe_float,
)
from siglab.utils import percentile as _percentile

__all__ = ["resolve_llm_model"]

_JSON_BLOCK_RE = re.compile("```(?:json)?\\s*(\\{.*\\})\\s*```", re.DOTALL)
ToolHandler = Callable[[dict[str, Any]], Awaitable[Any] | Any]
BAI_CREDITS_PER_TOKEN: dict[str, tuple[float, float, float, float]] = {
    "minimax-m2.7": (0.3, 1.2, 0.375, 0.06),
    "minimax-m2.5": (0.3, 1.2, 0.3, 0.03),
    "kimi-k2.6": (0.95, 4.0, 0.95, 0.16),
    "kimi-k2.5": (0.59, 3.0, 0.59, 0.177),
    "glm-5.1": (1.4, 4.4, 1.4, 0.26),
    "glm-5": (1.0, 3.2, 1.0, 0.2),
    "deepseek-v3.2": (0.29, 0.44, 0.29, 0.145),
    "deepseek-v4-flash": (0.14, 0.28, 0.14, 0.003),
    "deepseek-v4-pro": (0.435, 0.87, 0.435, 0.004),
    "gpt-5.5": (5.0, 30.0, 5.0, 0.5),
    "gpt-5.5-instant": (5.0, 30.0, 5.0, 0.5),
    "gpt-5.4": (2.5, 15.0, 2.5, 0.25),
    "gpt-5.4-pro": (30.0, 180.0, 30.0, 3.0),
    "gpt-5.2": (1.75, 14.0, 1.75, 0.175),
    "gpt-5.4-mini": (0.75, 4.5, 0.75, 0.075),
    "gpt-5-mini": (0.25, 2.0, 0.25, 0.025),
    "gpt-5.4-nano": (0.2, 1.25, 0.2, 0.02),
    "gpt-5-nano": (0.05, 0.4, 0.05, 0.005),
    "claude-opus-4-7": (5.0, 25.0, 6.25, 0.5),
    "claude-opus-4.7": (5.0, 25.0, 6.25, 0.5),
    "claude-opus-4-6": (5.0, 25.0, 6.25, 0.5),
    "claude-opus-4.6": (5.0, 25.0, 6.25, 0.5),
    "claude-opus-4-5": (5.0, 25.0, 6.25, 0.5),
    "claude-opus-4.5": (5.0, 25.0, 6.25, 0.5),
    "claude-sonnet-4-6": (3.0, 15.0, 3.75, 0.3),
    "claude-sonnet-4.6": (3.0, 15.0, 3.75, 0.3),
    "claude-sonnet-4-5": (3.0, 15.0, 3.75, 0.3),
    "claude-sonnet-4.5": (3.0, 15.0, 3.75, 0.3),
    "claude-haiku-4-5": (1.0, 5.0, 1.25, 0.1),
    "claude-haiku-4.5": (1.0, 5.0, 1.25, 0.1),
    "gemini-3.1-pro": (2.0, 12.0, 2.0, 0.2),
    "gemini-3-flash": (0.5, 3.0, 0.5, 0.05),
}
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
OPENROUTER_DEFAULT_LIST_CACHE_TTL_S = 600.0


@dataclass(frozen=True)
class OpenRouterModelInfo:
    model_id: str
    name: str
    prompt_usd_per_token: float
    completion_usd_per_token: float


async def _or_models(
    *,
    cache_ttl: float = OPENROUTER_DEFAULT_LIST_CACHE_TTL_S,
    api_key: str | None = None,
    timeout_s: float = 30.0,
) -> dict[str, OpenRouterModelInfo]:
    now = time.monotonic()
    cache = cast(
        dict[str, OpenRouterModelInfo], _or_models.__dict__.setdefault("_cache", {}),
    )
    ca = _or_models.__dict__.get("_cached_at")
    cached_at: float | None = ca if isinstance(ca, (int, float)) else None
    if isinstance(cached_at, float) and cache and (now - cached_at < cache_ttl):
        return cache
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(
            timeout=timeout_s,
            connect=min(10.0, timeout_s),
            read=timeout_s,
            write=30.0,
            pool=10.0,
        ),
        limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
    ) as client:
        response = await client.get(OPENROUTER_MODELS_URL, headers=headers)
        response.raise_for_status()
        payload = response.json()
    raw_models = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(raw_models, list):
        raw_models = []
    out: dict[str, OpenRouterModelInfo] = {}
    for entry in raw_models:
        if not isinstance(entry, dict):
            continue
        model_id = str(entry.get("id") or "").strip()
        if not model_id:
            continue
        pricing = entry.get("pricing") or {}
        if not isinstance(pricing, dict):
            pricing = {}
        out[model_id] = OpenRouterModelInfo(
            model_id=model_id,
            name=str(entry.get("name") or model_id),
            prompt_usd_per_token=max(
                0.0, safe_float(pricing.get("prompt"), default=0.0) or 0.0,
            ),
            completion_usd_per_token=max(
                0.0, safe_float(pricing.get("completion"), default=0.0) or 0.0,
            ),
        )
    _or_models.__dict__["_cache"] = out
    _or_models.__dict__["_cached_at"] = now
    return out


def _or_cost(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    *,
    catalog: dict[str, OpenRouterModelInfo] | None = None,
) -> float:
    if catalog is None:
        catalog = cast(
            dict[str, OpenRouterModelInfo], _or_models.__dict__.get("_cache", {}),
        )
    info = (
        catalog.get(model) or catalog.get(model.strip().lower())
        if isinstance(catalog, dict)
        else None
    )
    return (
        0.0
        if info is None
        else max(0, int(prompt_tokens)) * info.prompt_usd_per_token
        + max(0, int(completion_tokens)) * info.completion_usd_per_token
    )


@dataclass
class ClaudeTool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class LLMProviderError(RuntimeError):
    def __init__(
        self, msg: str, *, provider: str | None = None, status_code: int | None = None,
    ) -> None:
        super().__init__(msg)
        self.provider = provider
        self.status_code = status_code


class LLMConfigError(LLMProviderError):
    pass


class LLMAuthError(LLMProviderError):
    pass


class LLMRateLimitError(LLMProviderError):
    pass


class LLMQuotaError(LLMProviderError):
    pass


class LLMTransportError(LLMProviderError):
    pass


class LLMUpstreamError(LLMProviderError):
    pass


class LLMFormatError(LLMProviderError):
    pass


class ClaudeClient:
    def __init__(self, settings: SiglabConfig) -> None:
        self.settings = settings
        self.last_trace: dict[str, Any] | None = None
        self.last_exchange: dict[str, Any] | None = None
        self._client: httpx.AsyncClient | None = None
        self._latencies_ms: list[float] = []
        self._retries = self._rate_limits = self._transport_failures = 0
        self._request_count = self._success_count = 0
        self._prompt_tokens = self._completion_tokens = self._total_tokens = 0
        self._cache_write_tokens = self._cache_read_tokens = 0
        self._usage_credits = 0.0
        self._priced_token_count = 0
        self._context_pressure_events: list[dict[str, Any]] = []
        self._credit_pressure_events: list[dict[str, Any]] = []
        self.routing_policy = LLMRoutingPolicy(settings)
        self._usage_cost_usd = 0.0

    @property
    def is_configured(self) -> bool:
        return bool(resolve_llm_api_key(self.settings, provider=self.provider_name))

    @property
    def provider_name(self) -> str:
        return resolve_llm_provider(self.settings)

    async def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int | None = None,
        timeout_s: float | None = None,
        json_mode: bool = False,
        thinking_override: str | None = None,
        stage: str | None = None,
    ) -> dict[str, Any]:
        return await self.complete_json_with_tools(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            tools=[],
            max_tokens=max_tokens,
            timeout_s=timeout_s,
            json_mode=json_mode,
            thinking_override=thinking_override,
            stage=stage,
        )

    async def complete_json_messages(
        self,
        *,
        system_prompt: str,
        messages: Sequence[dict[str, Any]],
        max_tokens: int | None = None,
        timeout_s: float | None = None,
        json_mode: bool = False,
        thinking_override: str | None = None,
        stage: str | None = None,
    ) -> dict[str, Any]:
        if not self.is_configured:
            raise LLMConfigError(
                f"{'DeepSeek' if self.provider_name == 'deepseek' else 'OpenRouter' if self.provider_name == 'openrouter' else 'B.AI' if self.provider_name == 'bai' else 'Claude' if self.provider_name == 'claude' else 'LLM'} API key is not configured",
                provider=self.provider_name,
            )
        sm = self.routing_policy.model_for_stage(
            provider=self.provider_name,
            stage=stage,
            thinking_override=thinking_override,
        )
        tt = resolve_llm_thinking_mode(
            self.settings, provider=self.provider_name, override=thinking_override,
        )
        payload = self._build_pl(
            messages=[{"role": "system", "content": system_prompt}, *list(messages)],
            max_tokens=max_tokens,
            tools=[],
            json_mode=json_mode,
            thinking_override=thinking_override,
            stage=stage,
        )
        self.last_exchange = {
            "system_prompt": system_prompt,
            "messages": list(messages),
            "tool_names": [],
        }
        body = await self._chat_comp(payload=payload, timeout_s=timeout_s, stage=stage)
        sm = str(body.pop("_siglab_model_used", sm))
        choice = self._choice(body)
        self.last_trace = {
            "provider": self.provider_name,
            "model": sm,
            "thinking_mode": tt or "default",
            "tool_choice": "none",
            "tool_count_available": 0,
            "tool_rounds_used": 0,
            "final_content_preview": None,
            "response_finish_reason": choice.get("finish_reason"),
        }
        self._record_msg(
            message=dict(choice.get("message") or {}),
            finish_reason=choice.get("finish_reason"),
        )
        ct = self._extract_ct(body)
        self.last_trace["final_content_preview"] = _compact_scalar(ct[:2200])
        parsed = self._parse_j(ct)
        if self.last_exchange is not None:
            self.last_exchange["final_content"] = ct
            self.last_exchange["parsed_output"] = parsed
        return parsed

    async def complete_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int | None = None,
        timeout_s: float | None = None,
        thinking_override: str | None = None,
        stage: str | None = None,
    ) -> str:
        if not self.is_configured:
            raise LLMConfigError(
                f"{'DeepSeek' if self.provider_name == 'deepseek' else 'OpenRouter' if self.provider_name == 'openrouter' else 'B.AI' if self.provider_name == 'bai' else 'Claude' if self.provider_name == 'claude' else 'LLM'} API key is not configured",
                provider=self.provider_name,
            )
        sm = self.routing_policy.model_for_stage(
            provider=self.provider_name,
            stage=stage,
            thinking_override=thinking_override,
        )
        tt = resolve_llm_thinking_mode(
            self.settings, provider=self.provider_name, override=thinking_override,
        )
        payload = self._build_pl(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=max_tokens,
            tools=[],
            json_mode=False,
            thinking_override=thinking_override,
            stage=stage,
        )
        self.last_exchange = {
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "tool_names": [],
        }
        body = await self._chat_comp(payload=payload, timeout_s=timeout_s, stage=stage)
        sm = str(body.pop("_siglab_model_used", sm))
        choice = self._choice(body)
        self.last_trace = {
            "provider": self.provider_name,
            "model": sm,
            "thinking_mode": tt or "default",
            "tool_choice": "none",
            "tool_count_available": 0,
            "tool_rounds_used": 0,
            "final_content_preview": None,
            "response_finish_reason": choice.get("finish_reason"),
        }
        self._record_msg(
            message=dict(choice.get("message") or {}),
            finish_reason=choice.get("finish_reason"),
        )
        ct = self._extract_ct(body)
        self.last_trace["final_content_preview"] = _compact_scalar(ct[:2200])
        if self.last_exchange is not None:
            self.last_exchange["final_content"] = ct
        return ct

    async def _tool_loop(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        tools: Sequence[ClaudeTool] | None,
        max_tokens: int | None,
        timeout_s: float | None,
        max_tool_rounds: int | None,
        json_mode: bool,
        thinking_override: str | None,
        stage: str | None,
        exhausted_msg: str,
    ) -> str:
        if not self.is_configured:
            raise LLMConfigError(
                f"{'DeepSeek' if self.provider_name == 'deepseek' else 'OpenRouter' if self.provider_name == 'openrouter' else 'B.AI' if self.provider_name == 'bai' else 'Claude' if self.provider_name == 'claude' else 'LLM'} API key is not configured",
                provider=self.provider_name,
            )
        tl = list(tools or [])
        tm = {tool.name: tool for tool in tl}
        tt = resolve_llm_thinking_mode(
            self.settings, provider=self.provider_name, override=thinking_override,
        )
        sm = self.routing_policy.model_for_stage(
            provider=self.provider_name,
            stage=stage,
            thinking_override=thinking_override,
        )
        self.last_exchange = {
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "tool_names": [tool.name for tool in tl],
        }
        msgs: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        rr = (
            self.settings.claude_max_tool_rounds
            if max_tool_rounds is None
            else max_tool_rounds
        )
        ffwt = False
        trace: dict[str, Any] = {
            "provider": self.provider_name,
            "model": sm,
            "thinking_mode": tt or "default",
            "tool_choice": "auto" if tl else "none",
            "tool_count_available": len(tl),
            "max_tool_rounds": rr,
            "tool_rounds_used": 0,
            "tool_calls": [],
            "final_content_preview": None,
        }
        self.last_trace = trace
        while True:
            payload = self._build_pl(
                messages=msgs,
                max_tokens=max_tokens,
                tools=[] if ffwt else tl,
                json_mode=json_mode,
                thinking_override=thinking_override,
                stage=stage,
            )
            body = await self._chat_comp(
                payload=payload, timeout_s=timeout_s, stage=stage,
            )
            sm = str(body.pop("_siglab_model_used", sm))
            trace["model"] = sm
            choice = self._choice(body)
            msg = choice.get("message") or {}
            self._record_msg(message=msg, finish_reason=choice.get("finish_reason"))
            tcs = list(msg.get("tool_calls") or [])
            if tcs and tm:
                if rr <= 0:
                    trace["error"] = "max_tool_rounds_exhausted_forced_final"
                    msgs.append({"role": "user", "content": exhausted_msg})
                    tm = {}
                    ffwt = True
                    continue
                rr -= 1
                trace["tool_rounds_used"] = int(trace["tool_rounds_used"]) + 1
                msgs.append(self._tool_call_msg(msg))
                for tc in tcs:
                    tool_message, trace_entry = await self._exec_tool(
                        tool_call=tc, tool_map=tm,
                    )
                    trace["tool_calls"].append(trace_entry)
                    msgs.append(tool_message)
                continue
            ct = self._extract_ct(body)
            trace["final_content_preview"] = _compact_scalar(ct[:2200])
            trace["response_finish_reason"] = choice.get("finish_reason")
            if self.last_exchange is not None:
                self.last_exchange["final_content"] = ct
            return ct

    async def complete_text_with_tools(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        tools: Sequence[ClaudeTool] | None = None,
        max_tokens: int | None = None,
        timeout_s: float | None = None,
        max_tool_rounds: int | None = None,
        thinking_override: str | None = None,
        stage: str | None = None,
    ) -> str:
        return await self._tool_loop(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            tools=tools,
            max_tokens=max_tokens,
            timeout_s=timeout_s,
            max_tool_rounds=max_tool_rounds,
            json_mode=False,
            thinking_override=thinking_override,
            stage=stage,
            exhausted_msg="Tool budget exhausted. Do not call more tools. Return the final answer now using only evidence already collected.",
        )

    async def complete_json_with_tools(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        tools: Sequence[ClaudeTool] | None = None,
        max_tokens: int | None = None,
        timeout_s: float | None = None,
        max_tool_rounds: int | None = None,
        json_mode: bool = False,
        thinking_override: str | None = None,
        stage: str | None = None,
    ) -> dict[str, Any]:
        ct = await self._tool_loop(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            tools=tools,
            max_tokens=max_tokens,
            timeout_s=timeout_s,
            max_tool_rounds=max_tool_rounds,
            json_mode=json_mode,
            thinking_override=thinking_override,
            stage=stage,
            exhausted_msg="Tool budget exhausted. Do not call more tools. Return the final JSON now using only evidence already collected.",
        )
        parsed = self._parse_j(ct)
        if self.last_exchange is not None:
            self.last_exchange["parsed_output"] = parsed
        return parsed

    def _build_pl(
        self,
        *,
        messages: list[dict[str, Any]],
        max_tokens: int | None,
        tools: Sequence[ClaudeTool],
        json_mode: bool,
        thinking_override: str | None,
        stage: str | None = None,
    ) -> dict[str, Any]:
        tt = resolve_llm_thinking_mode(
            self.settings, provider=self.provider_name, override=thinking_override,
        )
        pn = self.provider_name
        sm = self.routing_policy.model_for_stage(
            provider=self.provider_name,
            stage=stage,
            thinking_override=thinking_override,
        )
        rot = int(max_tokens or self.settings.claude_max_tokens)
        if pn in ("bai", "openrouter"):
            eit = _estimate_message_tokens(messages)
            cl = int(getattr(self.settings, f"{pn}_context_tokens", 0) or 0)
            if cl > 0:
                pt = eit + rot
                pr = pt / float(cl)
                if pr >= 0.85:
                    ev = {
                        "stage": str(stage or "default"),
                        "model": sm,
                        "estimated_input_tokens": eit,
                        "requested_output_tokens": rot,
                        "context_limit_tokens": cl,
                        "projected_total_tokens": pt,
                        "pressure_ratio": round(pr, 4),
                        "severity": "critical" if pr >= 1.0 else "warning",
                    }
                    self._context_pressure_events.append(ev)
                    if pr >= 1.0 and max_tokens is None:
                        rot = max(512, cl - eit - 256)
                        ev["requested_output_tokens_after_clamp"] = rot
            if pn == "bai":
                max_call_credits = getattr(self.settings, "bai_max_call_credits", None)
                rates = BAI_CREDITS_PER_TOKEN.get(sm.strip().lower())
                if max_call_credits is not None and rates is not None:
                    ec = max(0, int(eit)) * rates[0] + max(0, int(rot)) * rates[1]
                    ce = {
                        "stage": str(stage or "default"),
                        "model": sm,
                        "estimated_input_tokens": eit,
                        "requested_output_tokens": rot,
                        "estimated_credits": round(ec, 6),
                        "max_call_credits": float(max_call_credits),
                        "pricing_source": "https://docs.b.ai/llmservice/pricing-and-usage/",
                        "usd_priced": False,
                    }
                    if ec > float(max_call_credits):
                        ce["severity"] = "critical"
                        self._credit_pressure_events.append(ce)
                        raise LLMQuotaError(
                            f"B.AI estimated call credits {ec:.6f} exceed BAI_MAX_CALL_CREDITS={float(max_call_credits):.6f}",
                            provider=self.provider_name,
                        )
                    ce["severity"] = "ok"
                    self._credit_pressure_events.append(ce)
            if pn == "openrouter":
                max_call_usd = getattr(self.settings, "openrouter_max_call_usd", None)
                estimated_cost = _or_cost(
                    model=sm, prompt_tokens=eit, completion_tokens=rot,
                )
                ce = {
                    "stage": str(stage or "default"),
                    "model": sm,
                    "estimated_input_tokens": eit,
                    "requested_output_tokens": rot,
                    "estimated_cost_usd": round(estimated_cost, 8),
                    "max_call_usd": float(max_call_usd)
                    if max_call_usd is not None
                    else None,
                    "pricing_source": OPENROUTER_MODELS_URL,
                    "usd_priced": True,
                }
                if max_call_usd is not None and float(estimated_cost) > float(
                    max_call_usd,
                ):
                    raise LLMQuotaError(
                        f"OpenRouter estimated call cost ${estimated_cost:.6f} exceeds OPENROUTER_MAX_CALL_USD=${float(max_call_usd):.6f}",
                        provider="openrouter",
                    )
                ce["severity"] = (
                    "ok"
                    if max_call_usd is None or estimated_cost <= float(max_call_usd)
                    else "critical"
                )
                self._credit_pressure_events.append(ce)
        payload: dict[str, Any] = {
            "model": sm,
            "messages": messages,
            "temperature": 0.6
            if tt == "disabled"
            else self.settings.claude_temperature,
            "top_p": self.settings.claude_top_p,
            "max_tokens": rot,
            "stream": False,
        }
        if pn == "openrouter":
            payload["usage"] = {"include": True}
        if tools:
            payload["tools"] = [tool.schema() for tool in tools]
            payload["tool_choice"] = "auto"
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if pn == "claude" and tt in {"enabled", "disabled"}:
            payload["thinking"] = {"type": tt}
        return payload

    async def _chat_comp(
        self,
        *,
        payload: dict[str, Any],
        timeout_s: float | None,
        stage: str | None = None,
    ) -> dict[str, Any]:
        last_error: LLMProviderError | None = None
        bp = dict(payload)
        pm = str(
            bp.get("model")
            or self.routing_policy.model_for_stage(
                provider=self.provider_name, stage=stage, thinking_override=None,
            ),
        )
        cs = self.routing_policy.candidates(
            provider=self.provider_name, stage=stage, primary=pm,
        )
        if not cs:
            raise LLMQuotaError(
                f"{'DeepSeek' if self.provider_name == 'deepseek' else 'OpenRouter' if self.provider_name == 'openrouter' else 'B.AI' if self.provider_name == 'bai' else 'Claude' if self.provider_name == 'claude' else 'LLM'} has no available routed models",
                provider=self.provider_name,
            )
        for model in cs:
            payload = {**bp, "model": model}
            for attempt in range(3):
                rid = uuid.uuid4().hex
                started = time.perf_counter()
                self._request_count += 1
                try:
                    response = await self._cli(timeout_s=timeout_s).post(
                        self._c_url(),
                        headers=self._req_headers(request_id=rid),
                        json=payload,
                    )
                except (
                    httpx.ConnectError,
                    httpx.TimeoutException,
                    httpx.HTTPError,
                    OSError,
                    TimeoutError,
                ) as exc:
                    self._transport_failures += 1
                    last_error = LLMTransportError(
                        f"{'DeepSeek' if self.provider_name == 'deepseek' else 'OpenRouter' if self.provider_name == 'openrouter' else 'B.AI' if self.provider_name == 'bai' else 'Claude' if self.provider_name == 'claude' else 'LLM'} transport failure: {exc}",
                        provider=self.provider_name,
                    )
                else:
                    elapsed_ms = (time.perf_counter() - started) * 1000.0
                    status = int(response.status_code)
                    self._latencies_ms.append(elapsed_ms)
                    if status in (401, 403):
                        self.routing_policy.mark_auth_failure(model, "LLMAuthError")
                        last_error = LLMAuthError(
                            f"{'DeepSeek' if self.provider_name == 'deepseek' else 'OpenRouter' if self.provider_name == 'openrouter' else 'B.AI' if self.provider_name == 'bai' else 'Claude' if self.provider_name == 'claude' else 'LLM'} auth failed with HTTP {status}",
                            provider=self.provider_name,
                            status_code=status,
                        )
                        break
                    if status == 429:
                        self._rate_limits += 1
                        body_429 = (response.text or "")[:500].lower()
                        if "free-models-per-day" in body_429 or "quota" in body_429:
                            self.routing_policy.mark_quota_failure(
                                model, "LLMQuotaError",
                            )
                            last_error = LLMQuotaError(
                                f"{'DeepSeek' if self.provider_name == 'deepseek' else 'OpenRouter' if self.provider_name == 'openrouter' else 'B.AI' if self.provider_name == 'bai' else 'Claude' if self.provider_name == 'claude' else 'LLM'} quota exceeded with HTTP 429: {body_429[:200]}",
                                provider=self.provider_name,
                                status_code=status,
                            )
                        else:
                            last_error = LLMRateLimitError(
                                f"{'DeepSeek' if self.provider_name == 'deepseek' else 'OpenRouter' if self.provider_name == 'openrouter' else 'B.AI' if self.provider_name == 'bai' else 'Claude' if self.provider_name == 'claude' else 'LLM'} rate limited with HTTP 429",
                                provider=self.provider_name,
                                status_code=status,
                            )
                    elif status in {408, 500, 502, 503, 504}:
                        last_error = LLMUpstreamError(
                            f"{'DeepSeek' if self.provider_name == 'deepseek' else 'OpenRouter' if self.provider_name == 'openrouter' else 'B.AI' if self.provider_name == 'bai' else 'Claude' if self.provider_name == 'claude' else 'LLM'} upstream HTTP {status}",
                            provider=self.provider_name,
                            status_code=status,
                        )
                    elif status >= 400:
                        detail = _compact_scalar(response.text[:500])
                        ld = str(detail).lower()
                        if (
                            "insufficient_user_quota" in ld
                            or "insufficient balance" in ld
                            or "quota" in ld
                            or "credit" in ld
                            or "balance" in ld
                        ):
                            self.routing_policy.mark_quota_failure(
                                model, "LLMQuotaError",
                            )
                            last_error = LLMQuotaError(
                                f"{'DeepSeek' if self.provider_name == 'deepseek' else 'OpenRouter' if self.provider_name == 'openrouter' else 'B.AI' if self.provider_name == 'bai' else 'Claude' if self.provider_name == 'claude' else 'LLM'} quota failed with HTTP {status}: {detail}",
                                provider=self.provider_name,
                                status_code=status,
                            )
                            break
                        if status in (400, 422) and (
                            "is not a valid model ID" in str(detail)
                            or "invalid model" in ld
                        ):
                            raise LLMFormatError(
                                f"OpenRouter invalid model: {str(detail)[:200]}",
                                provider=self.provider_name,
                                status_code=status,
                            )
                        if (
                            "context" in ld
                            or "maximum context" in ld
                            or "token limit" in ld
                            or "max tokens" in ld
                            or "too many tokens" in ld
                        ):
                            raise LLMFormatError(
                                f"{'DeepSeek' if self.provider_name == 'deepseek' else 'OpenRouter' if self.provider_name == 'openrouter' else 'B.AI' if self.provider_name == 'bai' else 'Claude' if self.provider_name == 'claude' else 'LLM'} context limit failed with HTTP {status}: {detail}",
                                provider=self.provider_name,
                                status_code=status,
                            )
                        raise LLMUpstreamError(
                            f"{'DeepSeek' if self.provider_name == 'deepseek' else 'OpenRouter' if self.provider_name == 'openrouter' else 'B.AI' if self.provider_name == 'bai' else 'Claude' if self.provider_name == 'claude' else 'LLM'} upstream HTTP {status}: {detail}",
                            provider=self.provider_name,
                            status_code=status,
                        )
                    else:
                        try:
                            body = response.json()
                        except ValueError as exc:
                            raise LLMFormatError(
                                f"{'DeepSeek' if self.provider_name == 'deepseek' else 'OpenRouter' if self.provider_name == 'openrouter' else 'B.AI' if self.provider_name == 'bai' else 'Claude' if self.provider_name == 'claude' else 'LLM'} returned malformed JSON",
                                provider=self.provider_name,
                                status_code=status,
                            ) from exc
                        if not isinstance(body, dict):
                            raise LLMFormatError(
                                f"{'DeepSeek' if self.provider_name == 'deepseek' else 'OpenRouter' if self.provider_name == 'openrouter' else 'B.AI' if self.provider_name == 'bai' else 'Claude' if self.provider_name == 'claude' else 'LLM'} response was not an object",
                                provider=self.provider_name,
                                status_code=status,
                            )
                        self._success_count += 1
                        self.routing_policy.record_latency(
                            model=model, stage=stage, elapsed_ms=elapsed_ms,
                        )
                        self._record_use(body.get("usage"), model=model)
                        body["_siglab_model_used"] = model
                        return body
                    if attempt >= 2:
                        break
                    self._retries += 1
                    await asyncio.sleep(0.25 * 2**attempt)
        raise last_error or LLMTransportError(
            f"{'DeepSeek' if self.provider_name == 'deepseek' else 'OpenRouter' if self.provider_name == 'openrouter' else 'B.AI' if self.provider_name == 'bai' else 'Claude' if self.provider_name == 'claude' else 'LLM'} request failed",
            provider=self.provider_name,
        )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def metrics_snapshot(self) -> dict[str, Any]:
        latencies = sorted(self._latencies_ms)
        attempts = max(1, self._request_count)
        cost_usd_value = (
            round(self._usage_cost_usd, 8) if self._priced_token_count else None
        )
        io = self.provider_name == "openrouter"
        return {
            "provider": self.provider_name,
            "model": self.routing_policy.model_for_stage(
                provider=self.provider_name, stage=None, thinking_override=None,
            ),
            "p50_ms": _percentile(latencies, 50),
            "p95_ms": _percentile(latencies, 95),
            "retry_count": self._retries,
            "rate_limit_count": self._rate_limits,
            "transport_failures": self._transport_failures,
            "success_rate": self._success_count / attempts,
            "usage": {
                "prompt_tokens": self._prompt_tokens,
                "completion_tokens": self._completion_tokens,
                "total_tokens": self._total_tokens,
                "cache_write_tokens": self._cache_write_tokens,
                "cache_read_tokens": self._cache_read_tokens,
                "credits_estimate": round(self._usage_credits, 6)
                if self._priced_token_count and (not io)
                else None,
                "cost_usd": cost_usd_value,
                "cost_status": "verified_openrouter_usd_priced"
                if io and self._priced_token_count
                else "verified_bai_credit_estimate_usd_unpriced"
                if self._priced_token_count
                else "unpriced_token_usage_only",
                "pricing_source": OPENROUTER_MODELS_URL
                if io and self._priced_token_count
                else "https://docs.b.ai/llmservice/pricing-and-usage/"
                if self._priced_token_count
                else None,
                "model_pricing_source": OPENROUTER_MODELS_URL if io else None,
            },
            "context_pressure": {
                "event_count": len(self._context_pressure_events),
                "latest": dict(self._context_pressure_events[-1])
                if self._context_pressure_events
                else None,
            },
            "credit_pressure": {
                "event_count": len(self._credit_pressure_events),
                "latest": dict(self._credit_pressure_events[-1])
                if self._credit_pressure_events
                else None,
            },
            "routing_policy": self.routing_policy.snapshot(),
        }

    def _record_use(self, usage: object, *, model: str | None = None) -> None:
        if not isinstance(usage, dict):
            return
        prompt = int_or_zero(
            usage.get("prompt_tokens")
            or usage.get("input_tokens")
            or usage.get("promptTokens")
            or usage.get("inputTokens"),
        )
        completion = int_or_zero(
            usage.get("completion_tokens")
            or usage.get("output_tokens")
            or usage.get("completionTokens")
            or usage.get("outputTokens"),
        )
        total = int_or_zero(usage.get("total_tokens") or usage.get("totalTokens"))
        if total == 0 and (prompt or completion):
            total = prompt + completion
        cw = int_or_zero(
            usage.get("cache_creation_input_tokens")
            or usage.get("cache_write_tokens")
            or usage.get("cacheWriteTokens"),
        )
        cr = int_or_zero(
            usage.get("cache_read_input_tokens")
            or usage.get("cached_tokens")
            or usage.get("cache_read_tokens")
            or usage.get("cacheReadTokens"),
        )
        if isinstance(prompt_details := usage.get("prompt_tokens_details"), dict):
            cr = max(cr, int_or_zero(prompt_details.get("cached_tokens")))
        self._prompt_tokens += prompt
        self._completion_tokens += completion
        self._total_tokens += total
        self._cache_write_tokens += cw
        self._cache_read_tokens += cr
        if self.provider_name == "bai":
            rates = BAI_CREDITS_PER_TOKEN.get(str(model or "").strip().lower())
            if rates is not None:
                input_rate, output_rate, cwr, crr = rates
                sp = max(0, prompt - cw - cr)
                self._usage_credits += (
                    sp * input_rate + cw * cwr + cr * crr + completion * output_rate
                )
                self._priced_token_count += prompt + completion
        if self.provider_name != "bai":
            cv = usage.get("cost")
            if cv is None and self.provider_name == "openrouter":
                cv = _or_cost(
                    model=str(model or ""),
                    prompt_tokens=prompt,
                    completion_tokens=completion,
                )
            if cv is not None:
                self._usage_cost_usd += safe_float(cv, default=0.0) or 0.0
                self._priced_token_count += prompt + completion

    def _cli(self, *, timeout_s: float | None) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    timeout=timeout_s or self.settings.claude_timeout_s,
                    connect=min(10.0, timeout_s or self.settings.claude_timeout_s),
                    read=timeout_s or self.settings.claude_timeout_s,
                    write=30.0,
                    pool=10.0,
                ),
                limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
            )
        return self._client

    def _c_url(self) -> str:
        bu = resolve_llm_base_url(self.settings, provider=self.provider_name).rstrip(
            "/",
        )
        return (
            f"{bu}/v1/chat/completions"
            if self.provider_name == "bai" and (not bu.endswith("/v1"))
            else f"{bu}/chat/completions"
        )

    def _req_headers(self, *, request_id: str | None = None) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {resolve_llm_api_key(self.settings, provider=self.provider_name)}",
            "Content-Type": "application/json",
        }
        if self.provider_name == "bai":
            headers["x-api-key"] = str(
                resolve_llm_api_key(self.settings, provider=self.provider_name) or "",
            )
        if request_id:
            headers["X-Request-ID"] = request_id
        if self.provider_name == "openrouter":
            referer = str(
                getattr(self.settings, "openrouter_http_referer", "") or "",
            ).strip()
            title = str(getattr(self.settings, "openrouter_title", "") or "").strip()
            if referer:
                headers["HTTP-Referer"] = referer
            if title:
                headers["X-Title"] = title
        return headers

    def _prov_label(self) -> str:
        return {
            "deepseek": "DeepSeek",
            "openrouter": "OpenRouter",
            "bai": "B.AI",
            "claude": "Claude",
        }.get(self.provider_name, "LLM")

    def _choice(self, body: dict[str, Any]) -> dict[str, Any]:
        if not (choices := body.get("choices") or []):
            raise LLMFormatError(
                f"{'DeepSeek' if self.provider_name == 'deepseek' else 'OpenRouter' if self.provider_name == 'openrouter' else 'B.AI' if self.provider_name == 'bai' else 'Claude' if self.provider_name == 'claude' else 'LLM'} response contained no choices",
                provider=self.provider_name,
            )
        return dict(choices[0] or {})

    def _extract_ct(self, body: dict[str, Any]) -> str:
        if isinstance(
            ct := (self._choice(body).get("message") or {}).get("content"), str,
        ):
            return ct
        if isinstance(ct, list):
            pieces: list[str] = []
            for item in ct:
                if isinstance(item, dict) and item.get("type") == "text":
                    pieces.append(str(item.get("text", "")))
            if pieces:
                return "\n".join(pieces)
        raise LLMFormatError(
            f"{'DeepSeek' if self.provider_name == 'deepseek' else 'OpenRouter' if self.provider_name == 'openrouter' else 'B.AI' if self.provider_name == 'bai' else 'Claude' if self.provider_name == 'claude' else 'LLM'} response content was not a string",
            provider=self.provider_name,
        )

    def _tool_call_msg(self, message: dict[str, Any]) -> dict[str, Any]:
        am = {
            "role": str(message.get("role") or "assistant"),
            "content": message.get("content") or "",
            "tool_calls": list(message.get("tool_calls") or []),
        }
        if "reasoning_content" in message and self.provider_name in {
            "claude",
            "deepseek",
            "bai",
        }:
            am["reasoning_content"] = message.get("reasoning_content") or ""
        return am

    def _record_msg(self, *, message: dict[str, Any], finish_reason: object) -> None:
        if self.last_exchange is not None:
            turns = list(self.last_exchange.get("assistant_messages") or [])
            turns.append(
                json.loads(
                    json.dumps(
                        {"finish_reason": finish_reason, "message": message},
                        ensure_ascii=True,
                        default=str,
                    ),
                ),
            )
            self.last_exchange["assistant_messages"] = turns
        if self.last_trace is not None:
            turns = list(self.last_trace.get("assistant_turns") or [])
            turns.append(
                {
                    "finish_reason": finish_reason,
                    "has_reasoning_content": "reasoning_content" in message,
                    "reasoning_content_preview": _compact_scalar(
                        str(message.get("reasoning_content") or ""),
                    )
                    if "reasoning_content" in message
                    else None,
                    "has_tool_calls": bool(message.get("tool_calls")),
                    "tool_call_count": len(list(message.get("tool_calls") or [])),
                },
            )
            self.last_trace["assistant_turns"] = turns

    async def _exec_tool(
        self, *, tool_call: dict[str, Any], tool_map: dict[str, ClaudeTool],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        function_payload = tool_call.get("function") or {}
        tool_name = str(function_payload.get("name") or "")
        args_raw = function_payload.get("arguments") or "{}"
        lat_ms: float | None = None
        try:
            arguments = json.loads(args_raw)
        except json.JSONDecodeError as exc:
            result: Any = {"ok": False, "error": f"invalid_tool_arguments: {exc}"}
        else:
            tool = tool_map.get(tool_name)
            if tool is None:
                result = {"ok": False, "error": f"unknown_tool: {tool_name}"}
            else:
                started = time.perf_counter()
                try:
                    outcome = tool.handler(arguments)
                    result = await outcome if hasattr(outcome, "__await__") else outcome
                except Exception as exc:
                    result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                else:
                    lat_ms = (time.perf_counter() - started) * 1000.0
        compact_result = self._compact_tl(result)
        tool_message = {
            "role": "tool",
            "tool_call_id": str(tool_call.get("id") or ""),
            "name": tool_name,
            "content": json.dumps(compact_result, ensure_ascii=True, default=str),
        }
        trace_entry = {
            "id": str(tool_call.get("id") or ""),
            "name": tool_name,
            "arguments": args_raw,
            "result": compact_result,
        }
        if lat_ms is not None:
            trace_entry["latency_ms"] = round(float(lat_ms), 3)
        return (tool_message, trace_entry)

    def _compact_tl(self, value: object, *, depth: int = 0) -> object:
        if depth >= 4:
            return _compact_scalar(value)
        if isinstance(value, dict):
            compacted: dict[str, Any] = {}
            for index, (key, item) in enumerate(value.items()):
                if index >= 12:
                    compacted["truncated"] = True
                    break
                compacted[str(key)] = self._compact_tl(item, depth=depth + 1)
            return compacted
        if isinstance(value, list):
            limited = value[:8]
            compacted_list = [
                self._compact_tl(item, depth=depth + 1) for item in limited
            ]
            if len(value) > len(limited):
                compacted_list.append(
                    {"truncated": True, "remaining": len(value) - len(limited)},
                )
            return compacted_list
        return _compact_scalar(value)

    def _parse_j(self, text: str) -> dict[str, Any]:
        m = _JSON_BLOCK_RE.search(text.strip())
        return cast(dict[str, Any], json.loads(m.group(1) if m else text.strip()))

    _record_usage = _record_use
    _compact_tool_payload = _compact_tl
    _parse_json = _parse_j
    _extract_choice = _choice
    _extract_message_content = _extract_ct
    _chat_url = _c_url
    _provider_label = _prov_label
    _request_headers = _req_headers
    _assistant_tool_call_message = _tool_call_msg
    _record_assistant_message = _record_msg


def _estimate_bai_credits(
    *, input_tokens: int, output_tokens: int, rates: tuple[float, float, float, float],
) -> float:
    return max(0, int(input_tokens)) * rates[0] + max(0, int(output_tokens)) * rates[1]


_int_or_zero = int_or_zero
def _json_clone(v: Any) -> Any:
    return json.loads(json.dumps(v, ensure_ascii=True, default=str))
_or_models.__dict__.update({"_cache": {}, "_cached_at": 0.0})
