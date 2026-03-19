from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Sequence

import httpx

from wayfinder_autolab.llm_metadata import (
    resolve_llm_api_key,
    resolve_llm_base_url,
    resolve_llm_model,
    resolve_llm_provider,
    resolve_llm_thinking_mode,
)
from wayfinder_autolab.settings import AutolabSettings

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)

ToolHandler = Callable[[dict[str, Any]], Awaitable[Any] | Any]


@dataclass
class KimiTool:
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


class KimiClient:
    def __init__(self, settings: AutolabSettings) -> None:
        self.settings = settings
        self.last_trace: dict[str, Any] | None = None
        self.last_exchange: dict[str, Any] | None = None

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
    ) -> dict[str, Any]:
        return await self.complete_json_with_tools(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            tools=[],
            max_tokens=max_tokens,
            timeout_s=timeout_s,
            json_mode=json_mode,
            thinking_override=thinking_override,
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
    ) -> dict[str, Any]:
        if not self.is_configured:
            raise RuntimeError(f"{self._provider_label()} API key is not configured")

        payload_messages = [{"role": "system", "content": system_prompt}, *list(messages)]
        selected_model = self._provider_model(thinking_override=thinking_override)
        thinking_type = self._resolve_thinking_mode(thinking_override)
        payload = self._build_payload(
            messages=payload_messages,
            max_tokens=max_tokens,
            tools=[],
            json_mode=json_mode,
            thinking_override=thinking_override,
        )
        self.last_exchange = {
            "system_prompt": system_prompt,
            "messages": list(messages),
            "tool_names": [],
        }
        body = await self._chat_completion(payload=payload, timeout_s=timeout_s)
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
    ) -> str:
        if not self.is_configured:
            raise RuntimeError(f"{self._provider_label()} API key is not configured")

        selected_model = self._provider_model(thinking_override=thinking_override)
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
        )
        self.last_exchange = {
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "tool_names": [],
        }
        body = await self._chat_completion(payload=payload, timeout_s=timeout_s)
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
        tools: Sequence[KimiTool] | None = None,
        max_tokens: int | None = None,
        timeout_s: float | None = None,
        max_tool_rounds: int | None = None,
        thinking_override: str | None = None,
    ) -> str:
        if not self.is_configured:
            raise RuntimeError(f"{self._provider_label()} API key is not configured")

        tool_list = list(tools or [])
        tool_map = {tool.name: tool for tool in tool_list}
        thinking_type = self._resolve_thinking_mode(thinking_override)
        selected_model = self._provider_model(thinking_override=thinking_override)
        self.last_exchange = {
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "tool_names": [tool.name for tool in tool_list],
        }
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        remaining_rounds = max_tool_rounds or self.settings.kimi_max_tool_rounds
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
                tools=tool_list,
                json_mode=False,
                thinking_override=thinking_override,
            )
            body = await self._chat_completion(
                payload=payload,
                timeout_s=timeout_s,
            )
            choice = self._extract_choice(body)
            message = choice.get("message") or {}
            self._record_assistant_message(
                message=message,
                finish_reason=choice.get("finish_reason"),
            )
            tool_calls = list(message.get("tool_calls") or [])

            if tool_calls and tool_map:
                if remaining_rounds <= 0:
                    trace["error"] = "max_tool_rounds_exceeded"
                    raise RuntimeError("Kimi tool loop exceeded configured max rounds")
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
        tools: Sequence[KimiTool] | None = None,
        max_tokens: int | None = None,
        timeout_s: float | None = None,
        max_tool_rounds: int | None = None,
        json_mode: bool = False,
        thinking_override: str | None = None,
    ) -> dict[str, Any]:
        if not self.is_configured:
            raise RuntimeError(f"{self._provider_label()} API key is not configured")

        tool_list = list(tools or [])
        tool_map = {tool.name: tool for tool in tool_list}
        thinking_type = self._resolve_thinking_mode(thinking_override)
        selected_model = self._provider_model(thinking_override=thinking_override)
        self.last_exchange = {
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "tool_names": [tool.name for tool in tool_list],
        }
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        remaining_rounds = max_tool_rounds or self.settings.kimi_max_tool_rounds
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
                tools=tool_list,
                json_mode=json_mode,
                thinking_override=thinking_override,
            )
            body = await self._chat_completion(
                payload=payload,
                timeout_s=timeout_s,
            )
            choice = self._extract_choice(body)
            message = choice.get("message") or {}
            self._record_assistant_message(
                message=message,
                finish_reason=choice.get("finish_reason"),
            )
            tool_calls = list(message.get("tool_calls") or [])

            if tool_calls and tool_map:
                if remaining_rounds <= 0:
                    trace["error"] = "max_tool_rounds_exceeded"
                    raise RuntimeError("Kimi tool loop exceeded configured max rounds")
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
        tools: Sequence[KimiTool],
        json_mode: bool,
        thinking_override: str | None,
    ) -> dict[str, Any]:
        thinking_type = self._resolve_thinking_mode(thinking_override)
        provider_name = self.provider_name
        selected_model = self._provider_model(thinking_override=thinking_override)
        temperature = self.settings.kimi_temperature
        if thinking_type == "disabled":
            temperature = 0.6

        payload: dict[str, Any] = {
            "model": selected_model,
            "messages": messages,
            "temperature": temperature,
            "top_p": self.settings.kimi_top_p,
            "max_tokens": max_tokens or self.settings.kimi_max_tokens,
            "stream": False,
        }
        if tools:
            payload["tools"] = [tool.schema() for tool in tools]
            payload["tool_choice"] = "auto"
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        if provider_name == "kimi" and thinking_type in {"enabled", "disabled"}:
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
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(
            timeout=timeout_s or self.settings.kimi_timeout_s
        ) as client:
            response = await client.post(
                f"{self._provider_base_url().rstrip('/')}/chat/completions",
                headers=self._request_headers(),
                json=payload,
            )
            response.raise_for_status()
            return response.json()

    def _provider_api_key(self) -> str | None:
        return resolve_llm_api_key(self.settings, provider=self.provider_name)

    def _provider_base_url(self) -> str:
        return resolve_llm_base_url(self.settings, provider=self.provider_name)

    def _provider_model(self, *, thinking_override: str | None) -> str:
        return resolve_llm_model(
            self.settings,
            provider=self.provider_name,
            thinking_override=thinking_override,
        )

    def _request_headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._provider_api_key()}",
            "Content-Type": "application/json",
        }
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
            "kimi": "Kimi",
        }.get(self.provider_name, "LLM")

    def _extract_choice(self, body: dict[str, Any]) -> dict[str, Any]:
        choices = body.get("choices") or []
        if not choices:
            raise ValueError("Kimi response contained no choices")
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
        raise ValueError("Kimi response content was not a string")

    def _assistant_tool_call_message(self, message: dict[str, Any]) -> dict[str, Any]:
        assistant_message = {
            "role": str(message.get("role") or "assistant"),
            "content": message.get("content") or "",
            "tool_calls": list(message.get("tool_calls") or []),
        }
        if "reasoning_content" in message:
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
        tool_map: dict[str, KimiTool],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        function_payload = tool_call.get("function") or {}
        tool_name = str(function_payload.get("name") or "")
        arguments_raw = function_payload.get("arguments") or "{}"
        try:
            arguments = json.loads(arguments_raw)
        except json.JSONDecodeError as exc:
            result: Any = {"ok": False, "error": f"invalid_tool_arguments: {exc}"}
        else:
            tool = tool_map.get(tool_name)
            if tool is None:
                result = {"ok": False, "error": f"unknown_tool: {tool_name}"}
            else:
                try:
                    outcome = tool.handler(arguments)
                    result = await outcome if hasattr(outcome, "__await__") else outcome
                except Exception as exc:  # noqa: BLE001
                    result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

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
        candidate = text.strip()
        block_match = _JSON_BLOCK_RE.search(candidate)
        if block_match:
            candidate = block_match.group(1)
        return json.loads(candidate)


def _compact_scalar(value: Any) -> Any:
    if isinstance(value, str) and len(value) > 2200:
        return value[:2199].rstrip() + "…"
    return value


def _json_clone(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=True, default=str))
