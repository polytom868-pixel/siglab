from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, cast
import httpx


if TYPE_CHECKING:
    from siglab.config import SiglabConfig

logger = logging.getLogger(__name__)

__all__ = [
    "ClaudeClient",
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
_OPENAI_MODEL = "deepseek-v4-flash"




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
    """Anthropic Messages API client using the Anthropic SDK.

    Reads api_key from env ``OPENMODEL_API_KEY`` and base_url from env
    ``OPENMODEL_BASE_URL``.  The model is always *deepseek-v4-flash*
    served via OpenModel AI.
    """

    def __init__(self, settings: SiglabConfig) -> None:
        self.settings = settings
        self.last_trace: dict[str, Any] | None = None
        self.last_exchange: dict[str, Any] | None = None
        self._client: "AsyncAnthropic | None" = None
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
        return "anthropic"

    # ── transport ───────────────────────────────────────────────

    def _get_client(self) -> "AsyncAnthropic":
        if self._client is None:
            from anthropic import AsyncAnthropic, RateLimitError as AnthropicRateLimitError

            api_key = str(self.settings.openmodel_api_key or "")
            base_url = str(
                self.settings.openmodel_base_url
            ).rstrip("/")
            self._client = AsyncAnthropic(
                api_key=api_key,
                base_url=base_url,
                max_retries=2,
                timeout=self.settings.claude_timeout_s,
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
        # Convert OpenAI-format messages to Anthropic Messages API format
        system_prompt = None
        anthropic_messages: list[dict[str, Any]] = []
        i = 0
        while i < len(messages):
            msg = messages[i]
            role = msg.get("role", "")
            if role == "system":
                system_prompt = str(msg.get("content") or "")
                i += 1
                continue
            if role == "tool":
                # Batch consecutive tool messages into one user message
                # with multiple tool_result content blocks (Anthropic API requirement)
                content_blocks = []
                while i < len(messages) and messages[i].get("role") == "tool":
                    tool_msg = messages[i]
                    content_blocks.append({
                        "type": "tool_result",
                        "tool_use_id": str(tool_msg.get("tool_call_id", "")),
                        "content": str(tool_msg.get("content") or ""),
                    })
                    i += 1
                anthropic_messages.append({"role": "user", "content": content_blocks})
                continue
            if role == "assistant" and msg.get("tool_calls"):
                content_blocks: list[dict[str, Any]] = []
                text_content = str(msg.get("content") or "")
                if text_content:
                    content_blocks.append({"type": "text", "text": text_content})
                for tc in msg["tool_calls"]:
                    raw_args = tc.get("function", {}).get("arguments", "{}")
                    try:
                        tc_input = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                    except (json.JSONDecodeError, TypeError):
                        tc_input = {}
                    content_blocks.append({
                        "type": "tool_use",
                        "id": str(tc.get("id", "")),
                        "name": str(tc.get("function", {}).get("name", "")),
                        "input": tc_input,
                    })
                anthropic_messages.append({"role": "assistant", "content": content_blocks})
                i += 1
                continue
            anthropic_messages.append({"role": role, "content": str(msg.get("content") or "")})
            i += 1

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": anthropic_messages,
            "max_tokens": max_tokens or 4096,
        }
        if system_prompt:
            kwargs["system"] = system_prompt
        if tools:
            kwargs["tools"] = tools

        from anthropic import RateLimitError as AnthropicRateLimitError

        started = time.perf_counter()
        self._request_count += 1
        try:
            response = await self._get_client().messages.create(**kwargs)
        except AnthropicRateLimitError as exc:
            raise LLMRateLimitError(str(exc)[:200], provider=self.provider_name, status_code=429) from exc
        except Exception as exc:
            self._transport_failures += 1
            raise _map_openai_error(exc, self.provider_name) from exc
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        self._success_count += 1
        self._latencies_ms.append(elapsed_ms)

        usage = response.usage
        if usage is not None:
            self._prompt_tokens += usage.input_tokens
            self._completion_tokens += usage.output_tokens
            self._total_tokens += usage.input_tokens + usage.output_tokens

        text_content = ""
        tool_calls: list[dict[str, Any]] = []
        for block in response.content:
            if block.type == "text":
                text_content += block.text
            elif block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "type": "function",
                    "function": {
                        "name": block.name,
                        "arguments": json.dumps(block.input if isinstance(block.input, dict) else {}),
                    },
                })

        # Map Anthropic stop_reason to OpenAI-compatible finish_reason
        finish_reason_map = {
            "end_turn": "stop",
            "tool_use": "tool_calls",
            "max_tokens": "length",
            "stop_sequence": "stop",
        }
        finish_reason = finish_reason_map.get(response.stop_reason, response.stop_reason)

        message_dict: dict[str, Any] = {
            "role": "assistant",
            "content": text_content,
        }
        if tool_calls:
            message_dict["tool_calls"] = tool_calls

        input_tokens = usage.input_tokens if usage else 0
        output_tokens = usage.output_tokens if usage else 0

        return {
            "id": response.id,
            "model": response.model,
            "choices": [
                {
                    "index": 0,
                    "message": message_dict,
                    "finish_reason": finish_reason,
                }
            ],
            "usage": {
                "prompt_tokens": input_tokens,
                "completion_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
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
        for attempt in range(2):
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
                    await asyncio.sleep(2 ** attempt)
                    continue
                break
            except LLMAuthError as exc:
                last_error = exc
                break
            except LLMRateLimitError:
                self._rate_limits += 1
                if attempt < 2:
                    await asyncio.sleep(5)
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
        max_tokens: int = 4096,
        timeout_s: float | None = None,
        json_mode: bool = True,
        thinking_override: str | None = None,
        stage: str | None = None,
    ) -> dict[str, Any]:
        ct = await self.complete_with_tools(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            timeout_s=timeout_s,
            stage=stage,
        )
        return self._parse_j(ct)

    async def complete_json_messages(
        self,
        *,
        system_prompt: str,
        messages: Sequence[dict[str, Any]],
        max_tokens: int = 4096,
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
            json_mode=json_mode,
            thinking_override=thinking_override,
            stage=stage,
        )
        self.last_exchange = {
            "system_prompt": system_prompt,
            "messages": list(messages),
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
        max_tokens: int = 4096,
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
            json_mode=False,
            thinking_override=thinking_override,
            stage=stage,
        )
        self.last_exchange = {
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
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

    async def complete_with_tools(
        self,
        *,
        user_prompt: str,
        system_prompt: str | None = "You are a crypto research assistant with access to real-time data tools. Use them to answer the user's questions factually.",
        max_tokens: int = 4096,
        timeout_s: float | None = None,
        max_tool_rounds: int = 5,
        stage: str | None = None,
    ) -> str:
        """Send a prompt with tool-calling enabled. Runs the tool-use loop
        (LLM calls tool → handler executes → result fed back → LLM
        produces final answer) and returns the final text."""
        if not self.is_configured:
            raise LLMConfigError(
                "OpenAI API key is not configured", provider=self.provider_name
            )

        from siglab.llm.tools import RESEARCH_TOOLS, tools_to_anthropic_format

        tool_defs = tools_to_anthropic_format(RESEARCH_TOOLS)
        tool_map = {t.name: t for t in RESEARCH_TOOLS}

        msgs: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt or "You are a helpful assistant."},
            {"role": "user", "content": user_prompt},
        ]

        self.last_exchange = {
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
        }

        round_count = 0
        trace: dict[str, Any] = {
            "provider": self.provider_name,
            "model": _OPENAI_MODEL,
            "thinking_mode": "default",
            "tool_choice": "auto",
            "tool_count_available": len(tool_defs),
            "tool_rounds_used": 0,
            "final_content_preview": None,
        }
        self.last_trace = trace

        while round_count < max_tool_rounds:
            payload = self._build_pl(
                messages=msgs,
                max_tokens=max_tokens,
                json_mode=False,
                thinking_override=None,
                stage=stage,
            )
            payload["tools"] = tool_defs
            body = await self._chat_comp(
                payload=payload, timeout_s=timeout_s, stage=stage
            )
            trace["model"] = str(body.get("_siglab_model_used", _OPENAI_MODEL))
            choice = self._choice(body)
            finish_reason = choice.get("finish_reason")
            message = choice.get("message", {})
            content = str(message.get("content") or "")
            tool_calls = message.get("tool_calls") or []

            if not tool_calls or finish_reason == "stop":
                # No more tool calls — this is the final answer
                trace["tool_rounds_used"] = round_count
                trace["final_content_preview"] = _compact_scalar(content[:2200])
                trace["response_finish_reason"] = finish_reason
                if self.last_exchange is not None:
                    self.last_exchange["final_content"] = content
                return content

            # Append the assistant message with tool_calls to the conversation
            msgs.append({"role": "assistant", "content": content, "tool_calls": tool_calls})

            # Execute each tool call and append the result
            for tc in tool_calls:
                tool_name = tc.get("function", {}).get("name", "")
                tool_args_str = tc.get("function", {}).get("arguments", "{}")
                tool_call_id = tc.get("id", "")

                tool = tool_map.get(tool_name)
                if tool is None:
                    result_text = f"Error: unknown tool '{tool_name}'"
                else:
                    try:
                        args = json.loads(tool_args_str) if isinstance(tool_args_str, str) else dict(tool_args_str)
                        result_text = await tool.handler(**args)
                    except Exception as exc:
                        result_text = f"Error executing {tool_name}: {exc}"

                msgs.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": result_text,
                })

            round_count += 1

        # Fell through max rounds — return last partial content
        trace["tool_rounds_used"] = round_count
        trace["final_content_preview"] = _compact_scalar("(max rounds reached)")
        trace["response_finish_reason"] = "max_tool_rounds"
        return "Maximum tool rounds reached without final answer."

    def _build_pl(
        self,
        *,
        messages: list[dict[str, Any]],
        max_tokens: int | None,
        json_mode: bool,
        thinking_override: str | None,
        stage: str | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"model": _OPENAI_MODEL, "messages": messages}
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        return payload


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






SUPPORTED_LLM_PROVIDERS = frozenset({"anthropic", "openai"})


def normalize_llm_provider(value: str | None) -> str | None:
    provider = str(value or "").strip().lower()
    return provider if provider in SUPPORTED_LLM_PROVIDERS else None


def resolve_llm_provider(settings: SiglabConfig) -> str:
    return "anthropic"


def infer_llm_provider(model: str | None) -> str | None:
    return "anthropic"


def resolve_llm_thinking_mode(
    settings: SiglabConfig,
    *,
    override: str | None = None,
) -> str:
    return str(override or "").strip().lower() if override is not None else ""


def resolve_llm_model(
    settings: SiglabConfig,
    *,
    thinking_override: str | None = None,
) -> str:
    return str(settings.openmodel_model or "deepseek-v4-flash")

def default_llm_model_display(
    settings: SiglabConfig,
) -> str:
    return str(settings.openmodel_model or "deepseek-v4-flash")

def resolve_llm_api_key(
    settings: SiglabConfig,
) -> str | None:
    return settings.openmodel_api_key

def resolve_llm_base_url(settings: SiglabConfig) -> str:
    return str(settings.openmodel_base_url or "https://api.openmodel.ai")
