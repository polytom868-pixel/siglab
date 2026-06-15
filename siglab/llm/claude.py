from __future__ import annotations

import json
import math
import re
import time
import uuid
import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Sequence

import httpx

from siglab.llm_metadata import (
    resolve_llm_api_key,
    resolve_llm_base_url,
    resolve_llm_model,  # noqa: F401 — re-exported for test mocking
    resolve_llm_provider,
    resolve_llm_thinking_mode,
)
from siglab.llm.policy import LLMRoutingPolicy
from siglab.config import SiglabConfig
from siglab.utils import percentile as _percentile

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)

ToolHandler = Callable[[dict[str, Any]], Awaitable[Any] | Any]

# input, output, cache_write, cache_read in B.AI Credits/Token.
BAI_CREDITS_PER_TOKEN: dict[str, tuple[float, float, float, float]] = {
    "minimax-m2.7": (0.30, 1.20, 0.375, 0.06),
    "minimax-m2.5": (0.30, 1.20, 0.30, 0.03),
    "kimi-k2.6": (0.95, 4.00, 0.95, 0.16),
    "kimi-k2.5": (0.59, 3.00, 0.59, 0.177),
    "glm-5.1": (1.40, 4.40, 1.40, 0.26),
    "glm-5": (1.00, 3.20, 1.00, 0.20),
    "deepseek-v3.2": (0.29, 0.44, 0.29, 0.145),
    "deepseek-v4-flash": (0.14, 0.28, 0.14, 0.003),
    "deepseek-v4-pro": (0.435, 0.87, 0.435, 0.004),
    "gpt-5.5": (5.00, 30.00, 5.00, 0.50),
    "gpt-5.5-instant": (5.00, 30.00, 5.00, 0.50),
    "gpt-5.4": (2.50, 15.00, 2.50, 0.25),
    "gpt-5.4-pro": (30.00, 180.00, 30.00, 3.00),
    "gpt-5.2": (1.75, 14.00, 1.75, 0.175),
    "gpt-5.4-mini": (0.75, 4.50, 0.75, 0.075),
    "gpt-5-mini": (0.25, 2.00, 0.25, 0.025),
    "gpt-5.4-nano": (0.20, 1.25, 0.20, 0.02),
    "gpt-5-nano": (0.05, 0.40, 0.05, 0.005),
    "claude-opus-4-7": (5.00, 25.00, 6.25, 0.50),
    "claude-opus-4.7": (5.00, 25.00, 6.25, 0.50),
    "claude-opus-4-6": (5.00, 25.00, 6.25, 0.50),
    "claude-opus-4.6": (5.00, 25.00, 6.25, 0.50),
    "claude-opus-4-5": (5.00, 25.00, 6.25, 0.50),
    "claude-opus-4.5": (5.00, 25.00, 6.25, 0.50),
    "claude-sonnet-4-6": (3.00, 15.00, 3.75, 0.30),
    "claude-sonnet-4.6": (3.00, 15.00, 3.75, 0.30),
    "claude-sonnet-4-5": (3.00, 15.00, 3.75, 0.30),
    "claude-sonnet-4.5": (3.00, 15.00, 3.75, 0.30),
    "claude-haiku-4-5": (1.00, 5.00, 1.25, 0.10),
    "claude-haiku-4.5": (1.00, 5.00, 1.25, 0.10),
    "gemini-3.1-pro": (2.00, 12.00, 2.00, 0.20),
    "gemini-3-flash": (0.50, 3.00, 0.50, 0.05),
}


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
    def __init__(self, message: str, *, provider: str | None = None, status_code: int | None = None) -> None:
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code


class LLMConfigError(LLMProviderError):
    """Missing or invalid provider configuration."""


class LLMAuthError(LLMProviderError):
    """Provider authentication failed."""


class LLMRateLimitError(LLMProviderError):
    """Provider rate limited the request."""


class LLMQuotaError(LLMProviderError):
    """Provider account has insufficient balance or quota for the request."""


class LLMTransportError(LLMProviderError):
    """Network, DNS, TLS, timeout, or socket transport failure."""


class LLMUpstreamError(LLMProviderError):
    """Provider returned an upstream HTTP failure."""


class LLMFormatError(LLMProviderError):
    """Provider returned a response shape the loop cannot use."""


class ClaudeClient:
    def __init__(self, settings: SiglabConfig) -> None:
        self.settings = settings
        self.last_trace: dict[str, Any] | None = None
        self.last_exchange: dict[str, Any] | None = None
        self._client: httpx.AsyncClient | None = None
        self._latencies_ms: list[float] = []
        self._retries = 0
        self._rate_limits = 0
        self._transport_failures = 0
        self._request_count = 0
        self._success_count = 0
        self._prompt_tokens = 0
        self._completion_tokens = 0
        self._total_tokens = 0
        self._cache_write_tokens = 0
        self._cache_read_tokens = 0
        self._usage_credits = 0.0
        self._priced_token_count = 0
        self._context_pressure_events: list[dict[str, Any]] = []
        self._credit_pressure_events: list[dict[str, Any]] = []
        self.routing_policy = LLMRoutingPolicy(settings)

    @property
    def is_configured(self) -> bool:
        return bool(self._provider_api_key())

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
            raise LLMConfigError(f"{self._provider_label()} API key is not configured", provider=self.provider_name)

        payload_messages = [{"role": "system", "content": system_prompt}, *list(messages)]
        selected_model = self._provider_model(thinking_override=thinking_override, stage=stage)
        thinking_type = self._resolve_thinking_mode(thinking_override)
        payload = self._build_payload(
            messages=payload_messages,
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
        body = await self._chat_completion(payload=payload, timeout_s=timeout_s, stage=stage)
        selected_model = str(body.pop("_siglab_model_used", selected_model))
        choice = self._extract_choice(body)
        self.last_trace = {
            "provider": self.provider_name,
            "model": selected_model,
            "thinking_mode": thinking_type or "default",
            "tool_choice": "none",
            "tool_count_available": 0,
            "tool_rounds_used": 0,
            "final_content_preview": None,
            "response_finish_reason": choice.get("finish_reason"),
        }
        message = dict(choice.get("message") or {})
        self._record_assistant_message(
            message=message,
            finish_reason=choice.get("finish_reason"),
        )
        content = self._extract_message_content(body)
        self.last_trace["final_content_preview"] = _compact_scalar(content[:2200])
        parsed = self._parse_json(content)
        if self.last_exchange is not None:
            self.last_exchange["final_content"] = content
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
            raise LLMConfigError(f"{self._provider_label()} API key is not configured", provider=self.provider_name)

        selected_model = self._provider_model(thinking_override=thinking_override, stage=stage)
        thinking_type = self._resolve_thinking_mode(thinking_override)
        payload = self._build_payload(
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
        body = await self._chat_completion(payload=payload, timeout_s=timeout_s, stage=stage)
        selected_model = str(body.pop("_siglab_model_used", selected_model))
        choice = self._extract_choice(body)
        self.last_trace = {
            "provider": self.provider_name,
            "model": selected_model,
            "thinking_mode": thinking_type or "default",
            "tool_choice": "none",
            "tool_count_available": 0,
            "tool_rounds_used": 0,
            "final_content_preview": None,
            "response_finish_reason": choice.get("finish_reason"),
        }
        message = dict(choice.get("message") or {})
        self._record_assistant_message(
            message=message,
            finish_reason=choice.get("finish_reason"),
        )
        content = self._extract_message_content(body)
        self.last_trace["final_content_preview"] = _compact_scalar(content[:2200])
        if self.last_exchange is not None:
            self.last_exchange["final_content"] = content
        return content

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
        if not self.is_configured:
            raise LLMConfigError(f"{self._provider_label()} API key is not configured", provider=self.provider_name)

        tool_list = list(tools or [])
        tool_map = {tool.name: tool for tool in tool_list}
        thinking_type = self._resolve_thinking_mode(thinking_override)
        selected_model = self._provider_model(thinking_override=thinking_override, stage=stage)
        self.last_exchange = {
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "tool_names": [tool.name for tool in tool_list],
        }
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        remaining_rounds = (
            self.settings.claude_max_tool_rounds if max_tool_rounds is None else max_tool_rounds
        )
        force_final_without_tools = False
        trace: dict[str, Any] = {
            "provider": self.provider_name,
            "model": selected_model,
            "thinking_mode": thinking_type or "default",
            "tool_choice": "auto" if tool_list else "none",
            "tool_count_available": len(tool_list),
            "max_tool_rounds": remaining_rounds,
            "tool_rounds_used": 0,
            "tool_calls": [],
            "final_content_preview": None,
        }
        self.last_trace = trace

        while True:
            payload = self._build_payload(
                messages=messages,
                max_tokens=max_tokens,
                tools=[] if force_final_without_tools else tool_list,
                json_mode=False,
                thinking_override=thinking_override,
                stage=stage,
            )
            body = await self._chat_completion(
                payload=payload,
                timeout_s=timeout_s,
                stage=stage,
            )
            selected_model = str(body.pop("_siglab_model_used", selected_model))
            trace["model"] = selected_model
            choice = self._extract_choice(body)
            message = choice.get("message") or {}
            self._record_assistant_message(
                message=message,
                finish_reason=choice.get("finish_reason"),
            )
            tool_calls = list(message.get("tool_calls") or [])

            if tool_calls and tool_map:
                if remaining_rounds <= 0:
                    trace["error"] = "max_tool_rounds_exhausted_forced_final"
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "Tool budget exhausted. Do not call more tools. "
                                "Return the final answer now using only evidence already collected."
                            ),
                        }
                    )
                    tool_map = {}
                    force_final_without_tools = True
                    continue
                remaining_rounds -= 1
                trace["tool_rounds_used"] = int(trace["tool_rounds_used"]) + 1
                messages.append(self._assistant_tool_call_message(message))
                for tool_call in tool_calls:
                    tool_message, trace_entry = await self._execute_tool_call(
                        tool_call=tool_call,
                        tool_map=tool_map,
                    )
                    trace["tool_calls"].append(trace_entry)
                    messages.append(tool_message)
                continue

            content = self._extract_message_content(body)
            trace["final_content_preview"] = _compact_scalar(content[:2200])
            trace["response_finish_reason"] = choice.get("finish_reason")
            if self.last_exchange is not None:
                self.last_exchange["final_content"] = content
            return content

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
        if not self.is_configured:
            raise LLMConfigError(f"{self._provider_label()} API key is not configured", provider=self.provider_name)

        tool_list = list(tools or [])
        tool_map = {tool.name: tool for tool in tool_list}
        thinking_type = self._resolve_thinking_mode(thinking_override)
        selected_model = self._provider_model(thinking_override=thinking_override, stage=stage)
        self.last_exchange = {
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "tool_names": [tool.name for tool in tool_list],
        }
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        remaining_rounds = (
            self.settings.claude_max_tool_rounds if max_tool_rounds is None else max_tool_rounds
        )
        force_final_without_tools = False
        trace: dict[str, Any] = {
            "provider": self.provider_name,
            "model": selected_model,
            "thinking_mode": thinking_type or "default",
            "tool_choice": "auto" if tool_list else "none",
            "tool_count_available": len(tool_list),
            "max_tool_rounds": remaining_rounds,
            "tool_rounds_used": 0,
            "tool_calls": [],
            "final_content_preview": None,
        }
        self.last_trace = trace

        while True:
            payload = self._build_payload(
                messages=messages,
                max_tokens=max_tokens,
                tools=[] if force_final_without_tools else tool_list,
                json_mode=json_mode,
                thinking_override=thinking_override,
                stage=stage,
            )
            body = await self._chat_completion(
                payload=payload,
                timeout_s=timeout_s,
                stage=stage,
            )
            selected_model = str(body.pop("_siglab_model_used", selected_model))
            trace["model"] = selected_model
            choice = self._extract_choice(body)
            message = choice.get("message") or {}
            self._record_assistant_message(
                message=message,
                finish_reason=choice.get("finish_reason"),
            )
            tool_calls = list(message.get("tool_calls") or [])

            if tool_calls and tool_map:
                if remaining_rounds <= 0:
                    trace["error"] = "max_tool_rounds_exhausted_forced_final"
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "Tool budget exhausted. Do not call more tools. "
                                "Return the final JSON now using only evidence already collected."
                            ),
                        }
                    )
                    tool_map = {}
                    force_final_without_tools = True
                    continue
                remaining_rounds -= 1
                trace["tool_rounds_used"] = int(trace["tool_rounds_used"]) + 1
                messages.append(self._assistant_tool_call_message(message))
                for tool_call in tool_calls:
                    tool_message, trace_entry = await self._execute_tool_call(
                        tool_call=tool_call,
                        tool_map=tool_map,
                    )
                    trace["tool_calls"].append(trace_entry)
                    messages.append(tool_message)
                continue

            content = self._extract_message_content(body)
            trace["final_content_preview"] = _compact_scalar(content[:2200])
            trace["response_finish_reason"] = choice.get("finish_reason")
            parsed = self._parse_json(content)
            if self.last_exchange is not None:
                self.last_exchange["final_content"] = content
                self.last_exchange["parsed_output"] = parsed
            return parsed

    def _build_payload(
        self,
        *,
        messages: list[dict[str, Any]],
        max_tokens: int | None,
        tools: Sequence[ClaudeTool],
        json_mode: bool,
        thinking_override: str | None,
        stage: str | None = None,
    ) -> dict[str, Any]:
        thinking_type = self._resolve_thinking_mode(thinking_override)
        provider_name = self.provider_name
        selected_model = self._provider_model(thinking_override=thinking_override, stage=stage)
        temperature = self.settings.claude_temperature
        if thinking_type == "disabled":
            temperature = 0.6
        requested_output_tokens = int(max_tokens or self.settings.claude_max_tokens)
        if provider_name == "bai":
            estimated_input_tokens = _estimate_message_tokens(messages)
            context_limit = int(getattr(self.settings, "bai_context_tokens", 0) or 0)
            if context_limit > 0:
                projected_total = estimated_input_tokens + requested_output_tokens
                pressure_ratio = projected_total / float(context_limit)
                if pressure_ratio >= 0.85:
                    event = {
                        "stage": str(stage or "default"),
                        "model": selected_model,
                        "estimated_input_tokens": estimated_input_tokens,
                        "requested_output_tokens": requested_output_tokens,
                        "context_limit_tokens": context_limit,
                        "projected_total_tokens": projected_total,
                        "pressure_ratio": round(pressure_ratio, 4),
                        "severity": "critical" if pressure_ratio >= 1.0 else "warning",
                    }
                    self._context_pressure_events.append(event)
                    if pressure_ratio >= 1.0 and max_tokens is None:
                        requested_output_tokens = max(512, context_limit - estimated_input_tokens - 256)
                        event["requested_output_tokens_after_clamp"] = requested_output_tokens
            max_call_credits = getattr(self.settings, "bai_max_call_credits", None)
            rates = BAI_CREDITS_PER_TOKEN.get(selected_model.strip().lower())
            if max_call_credits is not None and rates is not None:
                estimated_credits = _estimate_bai_credits(
                    input_tokens=estimated_input_tokens,
                    output_tokens=requested_output_tokens,
                    rates=rates,
                )
                credit_event = {
                    "stage": str(stage or "default"),
                    "model": selected_model,
                    "estimated_input_tokens": estimated_input_tokens,
                    "requested_output_tokens": requested_output_tokens,
                    "estimated_credits": round(estimated_credits, 6),
                    "max_call_credits": float(max_call_credits),
                    "pricing_source": "https://docs.b.ai/llmservice/pricing-and-usage/",
                    "usd_priced": False,
                }
                if estimated_credits > float(max_call_credits):
                    credit_event["severity"] = "critical"
                    self._credit_pressure_events.append(credit_event)
                    raise LLMQuotaError(
                        f"B.AI estimated call credits {estimated_credits:.6f} exceed "
                        f"BAI_MAX_CALL_CREDITS={float(max_call_credits):.6f}",
                        provider=self.provider_name,
                    )
                credit_event["severity"] = "ok"
                self._credit_pressure_events.append(credit_event)

        payload: dict[str, Any] = {
            "model": selected_model,
            "messages": messages,
            "temperature": temperature,
            "top_p": self.settings.claude_top_p,
            "max_tokens": requested_output_tokens,
            "stream": False,
        }
        if tools:
            payload["tools"] = [tool.schema() for tool in tools]
            payload["tool_choice"] = "auto"
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        if provider_name == "claude" and thinking_type in {"enabled", "disabled"}:
            payload["thinking"] = {"type": thinking_type}
        return payload

    def _resolve_thinking_mode(self, override: str | None) -> str:
        return resolve_llm_thinking_mode(
            self.settings,
            provider=self.provider_name,
            override=override,
        )

    async def _chat_completion(
        self,
        *,
        payload: dict[str, Any],
        timeout_s: float | None,
        stage: str | None = None,
    ) -> dict[str, Any]:
        last_error: LLMProviderError | None = None
        base_payload = dict(payload)
        primary_model = str(base_payload.get("model") or self._provider_model(thinking_override=None, stage=stage))
        candidates = self.routing_policy.candidates(provider=self.provider_name, stage=stage, primary=primary_model)
        if not candidates:
            raise LLMQuotaError(f"{self._provider_label()} has no available routed models", provider=self.provider_name)
        for model in candidates:
            payload = dict(base_payload)
            payload["model"] = model
            for attempt in range(3):
                request_id = uuid.uuid4().hex
                started = time.perf_counter()
                self._request_count += 1
                try:
                    response = await self._http(timeout_s=timeout_s).post(
                        self._chat_url(),
                        headers=self._request_headers(request_id=request_id),
                        json=payload,
                    )
                except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError, OSError, TimeoutError) as exc:
                    self._transport_failures += 1
                    last_error = LLMTransportError(
                        f"{self._provider_label()} transport failure: {exc}",
                        provider=self.provider_name,
                    )
                else:
                    elapsed_ms = (time.perf_counter() - started) * 1000.0
                    status = int(response.status_code)
                    self._latencies_ms.append(elapsed_ms)
                    if status in (401, 403):
                        self.routing_policy.mark_auth_failure(model, "LLMAuthError")
                        last_error = LLMAuthError(
                            f"{self._provider_label()} auth failed with HTTP {status}",
                            provider=self.provider_name,
                            status_code=status,
                        )
                        break
                    if status == 429:
                        self._rate_limits += 1
                        last_error = LLMRateLimitError(
                            f"{self._provider_label()} rate limited with HTTP 429",
                            provider=self.provider_name,
                            status_code=status,
                        )
                    elif status in {408, 500, 502, 503, 504}:
                        last_error = LLMUpstreamError(
                            f"{self._provider_label()} upstream HTTP {status}",
                            provider=self.provider_name,
                            status_code=status,
                        )
                    elif status >= 400:
                        detail = _compact_scalar(response.text[:500])
                        lower_detail = str(detail).lower()
                        if (
                            "insufficient_user_quota" in lower_detail
                            or "insufficient balance" in lower_detail
                            or "quota" in lower_detail
                            or "credit" in lower_detail
                            or "balance" in lower_detail
                        ):
                            self.routing_policy.mark_quota_failure(model, "LLMQuotaError")
                            last_error = LLMQuotaError(
                                f"{self._provider_label()} quota failed with HTTP {status}: {detail}",
                                provider=self.provider_name,
                                status_code=status,
                            )
                            break
                        if (
                            "context" in lower_detail
                            or "maximum context" in lower_detail
                            or "token limit" in lower_detail
                            or "max tokens" in lower_detail
                            or "too many tokens" in lower_detail
                        ):
                            raise LLMFormatError(
                                f"{self._provider_label()} context limit failed with HTTP {status}: {detail}",
                                provider=self.provider_name,
                                status_code=status,
                            )
                        raise LLMUpstreamError(
                            f"{self._provider_label()} upstream HTTP {status}: {detail}",
                            provider=self.provider_name,
                            status_code=status,
                        )
                    else:
                        try:
                            body = response.json()
                        except ValueError as exc:
                            raise LLMFormatError(
                                f"{self._provider_label()} returned malformed JSON",
                                provider=self.provider_name,
                                status_code=status,
                            ) from exc
                        if not isinstance(body, dict):
                            raise LLMFormatError(
                                f"{self._provider_label()} response was not an object",
                                provider=self.provider_name,
                                status_code=status,
                            )
                        self._success_count += 1
                        self.routing_policy.record_latency(
                            model=model,
                            stage=stage,
                            elapsed_ms=elapsed_ms,
                        )
                        self._record_usage(body.get("usage"), model=model)
                        body["_siglab_model_used"] = model
                        return body
                    if attempt >= 2:
                        break
                    self._retries += 1
                    await asyncio.sleep(0.25 * (2**attempt))
        raise last_error or LLMTransportError(f"{self._provider_label()} request failed", provider=self.provider_name)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def metrics_snapshot(self) -> dict[str, Any]:
        latencies = sorted(self._latencies_ms)
        attempts = max(1, self._request_count)
        return {
            "provider": self.provider_name,
            "model": self._provider_model(thinking_override=None),
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
                "credits_estimate": round(self._usage_credits, 6) if self._priced_token_count else None,
                "priced_tokens": self._priced_token_count,
                "cost_usd": None,
                "cost_status": (
                    "verified_bai_credit_estimate_usd_unpriced"
                    if self._priced_token_count
                    else "unpriced_token_usage_only"
                ),
                "pricing_source": (
                    "https://docs.b.ai/llmservice/pricing-and-usage/"
                    if self._priced_token_count
                    else None
                ),
            },
            "context_pressure": {
                "event_count": len(self._context_pressure_events),
                "latest": dict(self._context_pressure_events[-1]) if self._context_pressure_events else None,
            },
            "credit_pressure": {
                "event_count": len(self._credit_pressure_events),
                "latest": dict(self._credit_pressure_events[-1]) if self._credit_pressure_events else None,
            },
            "routing_policy": self.routing_policy.snapshot(),
        }

    def _record_usage(self, usage: Any, *, model: str | None = None) -> None:
        if not isinstance(usage, dict):
            return
        prompt = _int_or_zero(
            usage.get("prompt_tokens")
            or usage.get("input_tokens")
            or usage.get("promptTokens")
            or usage.get("inputTokens")
        )
        completion = _int_or_zero(
            usage.get("completion_tokens")
            or usage.get("output_tokens")
            or usage.get("completionTokens")
            or usage.get("outputTokens")
        )
        total = _int_or_zero(usage.get("total_tokens") or usage.get("totalTokens"))
        if total == 0 and (prompt or completion):
            total = prompt + completion
        cache_write = _int_or_zero(
            usage.get("cache_creation_input_tokens")
            or usage.get("cache_write_tokens")
            or usage.get("cacheWriteTokens")
        )
        cache_read = _int_or_zero(
            usage.get("cache_read_input_tokens")
            or usage.get("cached_tokens")
            or usage.get("cache_read_tokens")
            or usage.get("cacheReadTokens")
        )
        prompt_details = usage.get("prompt_tokens_details")
        if isinstance(prompt_details, dict):
            cache_read = max(cache_read, _int_or_zero(prompt_details.get("cached_tokens")))
        self._prompt_tokens += prompt
        self._completion_tokens += completion
        self._total_tokens += total
        self._cache_write_tokens += cache_write
        self._cache_read_tokens += cache_read
        if self.provider_name == "bai":
            rates = BAI_CREDITS_PER_TOKEN.get(str(model or "").strip().lower())
            if rates is not None:
                input_rate, output_rate, cache_write_rate, cache_read_rate = rates
                standard_prompt = max(0, prompt - cache_write - cache_read)
                self._usage_credits += (
                    (standard_prompt * input_rate)
                    + (cache_write * cache_write_rate)
                    + (cache_read * cache_read_rate)
                    + (completion * output_rate)
                )
                self._priced_token_count += prompt + completion

    def _http(self, *, timeout_s: float | None) -> httpx.AsyncClient:
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

    def _chat_url(self) -> str:
        base_url = self._provider_base_url().rstrip("/")
        if self.provider_name == "bai" and not base_url.endswith("/v1"):
            return f"{base_url}/v1/chat/completions"
        return f"{base_url}/chat/completions"

    def _provider_api_key(self) -> str | None:
        return resolve_llm_api_key(self.settings, provider=self.provider_name)

    def _provider_base_url(self) -> str:
        return resolve_llm_base_url(self.settings, provider=self.provider_name)

    def _provider_model(self, *, thinking_override: str | None, stage: str | None = None) -> str:
        return self.routing_policy.model_for_stage(
            provider=self.provider_name,
            stage=stage,
            thinking_override=thinking_override,
        )

    def _request_headers(self, *, request_id: str | None = None) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._provider_api_key()}",
            "Content-Type": "application/json",
        }
        if self.provider_name == "bai":
            headers["x-api-key"] = str(self._provider_api_key() or "")
        if request_id:
            headers["X-Request-ID"] = request_id
        if self.provider_name == "openrouter":
            referer = str(getattr(self.settings, "openrouter_http_referer", "") or "").strip()
            title = str(getattr(self.settings, "openrouter_title", "") or "").strip()
            if referer:
                headers["HTTP-Referer"] = referer
            if title:
                headers["X-Title"] = title
        return headers

    def _provider_label(self) -> str:
        return {
            "deepseek": "DeepSeek",
            "openrouter": "OpenRouter",
            "bai": "B.AI",
            "claude": "Claude",
        }.get(self.provider_name, "LLM")

    def _extract_choice(self, body: dict[str, Any]) -> dict[str, Any]:
        choices = body.get("choices") or []
        if not choices:
            raise LLMFormatError(f"{self._provider_label()} response contained no choices", provider=self.provider_name)
        return dict(choices[0] or {})

    def _extract_message_content(self, body: dict[str, Any]) -> str:
        message = self._extract_choice(body).get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            pieces: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    pieces.append(str(item.get("text", "")))
            if pieces:
                return "\n".join(pieces)
        raise LLMFormatError(f"{self._provider_label()} response content was not a string", provider=self.provider_name)

    def _assistant_tool_call_message(self, message: dict[str, Any]) -> dict[str, Any]:
        assistant_message = {
            "role": str(message.get("role") or "assistant"),
            "content": message.get("content") or "",
            "tool_calls": list(message.get("tool_calls") or []),
        }
        if "reasoning_content" in message and self.provider_name in {"claude", "deepseek", "bai"}:
            assistant_message["reasoning_content"] = message.get("reasoning_content") or ""
        return assistant_message

    def _record_assistant_message(
        self,
        *,
        message: dict[str, Any],
        finish_reason: Any,
    ) -> None:
        if self.last_exchange is not None:
            turns = list(self.last_exchange.get("assistant_messages") or [])
            turns.append(
                _json_clone(
                    {
                        "finish_reason": finish_reason,
                        "message": message,
                    }
                )
            )
            self.last_exchange["assistant_messages"] = turns
        if self.last_trace is not None:
            turns = list(self.last_trace.get("assistant_turns") or [])
            turns.append(
                {
                    "finish_reason": finish_reason,
                    "has_reasoning_content": "reasoning_content" in message,
                    "reasoning_content_preview": (
                        _compact_scalar(str(message.get("reasoning_content") or ""))
                        if "reasoning_content" in message
                        else None
                    ),
                    "has_tool_calls": bool(message.get("tool_calls")),
                    "tool_call_count": len(list(message.get("tool_calls") or [])),
                }
            )
            self.last_trace["assistant_turns"] = turns

    async def _execute_tool_call(
        self,
        *,
        tool_call: dict[str, Any],
        tool_map: dict[str, ClaudeTool],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        function_payload = tool_call.get("function") or {}
        tool_name = str(function_payload.get("name") or "")
        arguments_raw = function_payload.get("arguments") or "{}"
        latency_ms: float | None = None
        try:
            arguments = json.loads(arguments_raw)
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
                except Exception as exc:  # noqa: BLE001
                    result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                else:
                    latency_ms = (time.perf_counter() - started) * 1000.0

        compact_result = self._compact_tool_payload(result)
        tool_message = {
            "role": "tool",
            "tool_call_id": str(tool_call.get("id") or ""),
            "name": tool_name,
            "content": json.dumps(
                compact_result,
                ensure_ascii=True,
                default=str,
            ),
        }
        trace_entry = {
            "id": str(tool_call.get("id") or ""),
            "name": tool_name,
            "arguments": arguments_raw,
            "result": compact_result,
        }
        if latency_ms is not None:
            trace_entry["latency_ms"] = round(float(latency_ms), 3)
        return tool_message, trace_entry

    def _compact_tool_payload(self, value: Any, *, depth: int = 0) -> Any:
        if depth >= 4:
            return _compact_scalar(value)
        if isinstance(value, dict):
            compacted: dict[str, Any] = {}
            for index, (key, item) in enumerate(value.items()):
                if index >= 12:
                    compacted["truncated"] = True
                    break
                compacted[str(key)] = self._compact_tool_payload(item, depth=depth + 1)
            return compacted
        if isinstance(value, list):
            limited = value[:8]
            compacted_list = [
                self._compact_tool_payload(item, depth=depth + 1) for item in limited
            ]
            if len(value) > len(limited):
                compacted_list.append({"truncated": True, "remaining": len(value) - len(limited)})
            return compacted_list
        return _compact_scalar(value)

    def _parse_json(self, text: str) -> dict[str, Any]:
        spec = text.strip()
        block_match = _JSON_BLOCK_RE.search(spec)
        if block_match:
            spec = block_match.group(1)
        return json.loads(spec)


def _compact_scalar(value: Any) -> Any:
    if isinstance(value, str) and len(value) > 2200:
        return value[:2199].rstrip() + "…"
    return value


def _int_or_zero(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, parsed)


def _estimate_message_tokens(messages: Sequence[dict[str, Any]]) -> int:
    # Conservative cheap proxy. It is not billing truth; it only flags context pressure before wasting a live call.
    chars = len(json.dumps(list(messages), ensure_ascii=True, default=str))
    return max(1, (chars + 3) // 4)


def _estimate_bai_credits(
    *,
    input_tokens: int,
    output_tokens: int,
    rates: tuple[float, float, float, float],
) -> float:
    input_rate, output_rate, _cache_write_rate, _cache_read_rate = rates
    return (max(0, int(input_tokens)) * input_rate) + (max(0, int(output_tokens)) * output_rate)


def _json_clone(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=True, default=str))



