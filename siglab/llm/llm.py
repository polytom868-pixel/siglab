from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import httpx
from openai import AsyncOpenAI

if TYPE_CHECKING:
    from siglab.config import SiglabConfig

logger = logging.getLogger(__name__)

__all__ = [
    "ClaudeClient",
    "ClaudeTool",
    "LLMAuthError",
    "LLMConfigError",
    "LLMFormatError",
    "LLMProviderError",
    "LLMQuotaError",
    "LLMRateLimitError",
    "LLMTransportError",
    "LLMUpstreamError",
]

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)
ToolHandler = Callable[[dict[str, Any]], Awaitable[Any] | Any]
_OPENAI_MODEL = "deepseek-v4-flash"


# ── Tool descriptor ────────────────────────────────────────────────


@dataclass
class ClaudeTool:
    """Descriptor for a tool/function the LLM may call."""

    name: str
    description: str
    params: dict[str, Any]

    def __init__(self, name: str, description: str, params: dict[str, Any] | None = None, *, parameters: dict[str, Any] | None = None) -> None:
        self.name = name
        self.description = description
        self.params = params if params is not None else (parameters or {})

    def canonical(self) -> dict[str, Any]:
        return {
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.params,
            },
            "type": "function",
        }


# ── Error hierarchy ────────────────────────────────────────────────


class LLMProviderError(RuntimeError):
    def __init__(self, message: str, *, provider: str, status_code: int | None = None):
        super().__init__(message)
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


# ── Client ─────────────────────────────────────────────────────────


class ClaudeClient:
    """OpenAI-compatible LLM client using the OpenAI SDK.

    Reads api_key from env ``OPENMODEL_API_KEY`` and base_url from env
    ``OPENMODEL_BASE_URL``.  The model is always *deepseek-v4-flash*
    served via OpenModel AI.
    """

    def __init__(self, settings: SiglabConfig) -> None:
        self.settings = settings
        self.last_trace: dict[str, Any] | None = None
        self.last_exchange: dict[str, Any] | None = None
        self._client: AsyncOpenAI | None = None
        self._latencies_ms: list[float] = []
        self._retries = self._rate_limits = self._transport_failures = 0
        self._request_count = self._success_count = 0
        self._prompt_tokens = self._completion_tokens = self._total_tokens = 0
        self._cache_write_tokens = self._cache_read_tokens = 0
        self._usage_credits = 0.0
        self._priced_token_count = 0
        self._context_pressure_events: list[dict[str, Any]] = []
        self._credit_pressure_events: list[dict[str, Any]] = []
        self._usage_cost_usd = 0.0

    # ── property helpers ───────────────────────────────────────

    @property
    def is_configured(self) -> bool:
        return bool(self.settings.openmodel_api_key)

    @property
    def provider_name(self) -> str:
        return "openai"

    # ── transport ───────────────────────────────────────────────

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            api_key = str(self.settings.openmodel_api_key or "")
            base_url = str(
                self.settings.openmodel_base_url
            ).rstrip("/")
            self._client = AsyncOpenAI(
                api_key=api_key,
                base_url=base_url,
                max_retries=2,
                timeout=self.settings.claude_timeout_s,
                http_client=httpx.AsyncClient(
                    timeout=httpx.Timeout(
                        self.settings.claude_timeout_s,
                        connect=10.0,
                        read=self.settings.claude_timeout_s,
                        write=30.0,
                        pool=10.0,
                    ),
                    limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
                ),
            )
        return self._client

    async def _call_chat(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"model": model, "messages": messages}
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if tools:
            kwargs["tools"] = tools
        if response_format:
            kwargs["response_format"] = response_format
        started = time.perf_counter()
        self._request_count += 1
        try:
            response = await self._get_client().chat.completions.create(**kwargs)
        except Exception as exc:
            self._transport_failures += 1
            raise _map_openai_error(exc, self.provider_name) from exc
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        self._success_count += 1
        self._latencies_ms.append(elapsed_ms)
        usage = response.usage
        if usage is not None:
            self._prompt_tokens += usage.prompt_tokens
            self._completion_tokens += usage.completion_tokens
            self._total_tokens += usage.total_tokens
        choice = response.choices[0] if response.choices else None
        return {
            "id": response.id,
            "model": response.model,
            "choices": [
                {
                    "index": choice.index if choice else 0,
                    "message": dict(choice.message) if choice else {},
                    "finish_reason": choice.finish_reason if choice else "stop",
                }
            ],
            "usage": {
                "prompt_tokens": usage.prompt_tokens if usage else 0,
                "completion_tokens": usage.completion_tokens if usage else 0,
                "total_tokens": usage.total_tokens if usage else 0,
            },
            "_siglab_model_used": model,
        }

    async def _chat_comp(
        self,
        *,
        payload: dict[str, Any],
        timeout_s: float | None = None,
        stage: str | None = None,
    ) -> dict[str, Any]:
        last_error: LLMProviderError | None = None
        bp = dict(payload)
        model = str(bp.get("model", _OPENAI_MODEL))
        for attempt in range(3):
            started = time.perf_counter()
            self._request_count += 1
            try:
                response = await self._call_chat(
                    model=model,
                    messages=bp.get("messages", []),
                    max_tokens=bp.get("max_tokens"),
                    tools=bp.get("tools"),
                    response_format=bp.get("response_format"),
                )
            except (httpx.ConnectError, httpx.TimeoutException, OSError, TimeoutError) as exc:
                self._transport_failures += 1
                last_error = LLMTransportError(
                    f"OpenAI transport failure: {exc}", provider=self.provider_name
                )
                if attempt < 2:
                    await asyncio.sleep(0.5 * (attempt + 2))
                    continue
                break
            except LLMAuthError as exc:
                last_error = exc
                break
            except LLMRateLimitError:
                self._rate_limits += 1
                if attempt < 2:
                    await asyncio.sleep(0.5 * (attempt + 2))
                    continue
                last_error = LLMQuotaError(
                    "Rate limited after retries", provider=self.provider_name
                )
                break
            except LLMProviderError as exc:
                last_error = exc
                if attempt < 2:
                    continue
                break
            else:
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                self._latencies_ms.append(elapsed_ms)
                self._success_count += 1
                return response
        raise last_error or LLMProviderError(
            "No model succeeded", provider=self.provider_name
        )

    def _choice(self, body: dict[str, Any]) -> dict[str, Any]:
        choices = body.get("choices") or []
        return dict(choices[0]) if choices else {}

    def _extract_ct(self, body: dict[str, Any]) -> str:
        choice = self._choice(body)
        msg = dict(choice.get("message") or {})
        return str(msg.get("content") or "")

    def _parse_j(self, text: str) -> dict[str, Any]:
        text = text.strip()
        m = _JSON_BLOCK_RE.search(text)
        if m:
            text = m.group(1)
        try:
            return cast(dict[str, Any], json.loads(text))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise LLMFormatError(
                f"Failed to parse JSON response: {exc}", provider=self.provider_name
            ) from exc

    # ── public API ──────────────────────────────────────────────

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
                "OpenAI API key is not configured", provider=self.provider_name
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
        choice = self._choice(body)
        self.last_trace = {
            "provider": self.provider_name,
            "model": _OPENAI_MODEL,
            "thinking_mode": "default",
            "tool_choice": "none",
            "tool_count_available": 0,
            "tool_rounds_used": 0,
            "final_content_preview": None,
            "response_finish_reason": choice.get("finish_reason"),
        }
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
                "OpenAI API key is not configured", provider=self.provider_name
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
        choice = self._choice(body)
        self.last_trace = {
            "provider": self.provider_name,
            "model": _OPENAI_MODEL,
            "thinking_mode": "default",
            "tool_choice": "none",
            "tool_count_available": 0,
            "tool_rounds_used": 0,
            "final_content_preview": None,
            "response_finish_reason": choice.get("finish_reason"),
        }
        ct = self._extract_ct(body)
        self.last_trace["final_content_preview"] = _compact_scalar(ct[:2200])
        if self.last_exchange is not None:
            self.last_exchange["final_content"] = ct
        return ct

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
            exhausted_msg=_TOOL_EXHAUSTED_JSON,
        )
        return self._parse_j(ct)

    # ── tool loop ───────────────────────────────────────────────

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
                "OpenAI API key is not configured", provider=self.provider_name
            )
        tl = list(tools or [])
        tm = {tool.name: tool for tool in tl}
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
            "model": _OPENAI_MODEL,
            "thinking_mode": "default",
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
                payload=payload, timeout_s=timeout_s, stage=stage
            )
            trace["model"] = str(body.get("_siglab_model_used", _OPENAI_MODEL))
            choice = self._choice(body)
            msg = choice.get("message") or {}
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
                    tool_msg, trace_entry = await self._exec_tool(
                        tool_call=tc, tool_map=tm,
                    )
                    trace["tool_calls"].append(trace_entry)
                    msgs.append(tool_msg)
                continue
            ct = self._extract_ct(body)
            trace["final_content_preview"] = _compact_scalar(ct[:2200])
            trace["response_finish_reason"] = choice.get("finish_reason")
            if self.last_exchange is not None:
                self.last_exchange["final_content"] = ct
            return ct

    def _build_pl(
        self,
        *,
        messages: list[dict[str, Any]],
        max_tokens: int | None,
        tools: Sequence[ClaudeTool] | None,
        json_mode: bool,
        thinking_override: str | None,
        stage: str | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"model": _OPENAI_MODEL, "messages": messages}
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        tl = list(tools or [])
        if tl:
            payload["tools"] = [t.canonical() for t in tl]
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        return payload

    def _tool_call_msg(self, msg: dict[str, Any]) -> dict[str, Any]:
        return {
            "role": msg.get("role", "assistant"),
            "content": msg.get("content") or "",
            "tool_calls": msg.get("tool_calls") or [],
        }

    async def _exec_tool(
        self,
        *,
        tool_call: dict[str, Any],
        tool_map: dict[str, ClaudeTool],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        tc_id = tool_call.get("id", "")
        func = tool_call.get("function") or {}
        name = func.get("name", "")
        raw_args = func.get("arguments", "{}")
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
        except (json.JSONDecodeError, TypeError, ValueError):
            args = {}
        trace_entry = {
            "tool_call_id": tc_id,
            "name": name,
            "arguments": args,
            "error": None,
            "summary": None,
        }
        tool = tool_map.get(name)
        if tool is None:
            result = f"Unknown tool: {name}"
            trace_entry["error"] = result
        else:
            try:
                result = tool
            except Exception as exc:
                result = f"Tool error: {exc}"
                trace_entry["error"] = str(exc)
        return {
            "role": "tool",
            "tool_call_id": tc_id,
            "content": str(result),
        }, trace_entry

    def _record_msg(self, *, message: dict[str, Any], finish_reason: str | None) -> None:
        pass

    _record_assistant_message = _record_msg

    @property
    def stats(self) -> dict[str, Any]:
        return {"usage": {"request_count": self._request_count}}


# ── helpers ────────────────────────────────────────────────────────


def _map_openai_error(exc: Exception, provider: str) -> LLMProviderError:
    exc_str = str(exc).lower()
    if "401" in exc_str or "unauthorized" in exc_str or "auth" in exc_str:
        return LLMAuthError(str(exc)[:200], provider=provider, status_code=401)
    if "429" in exc_str or "rate limit" in exc_str or "too many" in exc_str:
        return LLMRateLimitError(str(exc)[:200], provider=provider, status_code=429)
    if "timeout" in exc_str or "timed out" in exc_str:
        return LLMTransportError(str(exc)[:200], provider=provider)
    if "connect" in exc_str or "connection" in exc_str:
        return LLMTransportError(str(exc)[:200], provider=provider)
    return LLMUpstreamError(str(exc)[:200], provider=provider)



def _compact_scalar(value: object, max_len: int = 200) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    return text[:max_len] + "..." if len(text) > max_len else text


_TOOL_EXHAUSTED_JSON = json.dumps({
    "error": "Max tool rounds exhausted",
    "action": "return the best partial result",
})





SUPPORTED_LLM_PROVIDERS = frozenset({"openai"})


def normalize_llm_provider(value: str | None) -> str | None:
    provider = str(value or "").strip().lower()
    return provider if provider in SUPPORTED_LLM_PROVIDERS else None


def resolve_llm_provider(settings: SiglabConfig) -> str:
    return "openai"


def infer_llm_provider(model: str | None) -> str | None:
    return "openai"


def resolve_llm_thinking_mode(
    settings: SiglabConfig,
    *,
    provider: str | None = None,
    override: str | None = None,
) -> str:
    return str(override or "").strip().lower() if override is not None else ""


def resolve_llm_model(
    settings: SiglabConfig,
    *,
    provider: str | None = None,
    thinking_override: str | None = None,
) -> str:
    return str(settings.openmodel_model or "deepseek-v4-flash")


def default_llm_model_display(
    settings: SiglabConfig,
    *,
    provider: str | None = None,
) -> str:
    return str(settings.openmodel_model or "deepseek-v4-flash")


def resolve_llm_api_key(
    settings: SiglabConfig,
    *,
    provider: str | None = None,
) -> str | None:
    return settings.openmodel_api_key


def resolve_llm_base_url(settings: SiglabConfig, *, provider: str | None = None) -> str:
    return str(settings.openmodel_base_url or "https://api.openmodel.ai/v1")
