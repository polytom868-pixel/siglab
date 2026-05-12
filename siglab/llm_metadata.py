from __future__ import annotations

from typing import Any


SUPPORTED_LLM_PROVIDERS = frozenset({"claude", "deepseek", "openrouter"})


def normalize_llm_provider(value: str | None) -> str | None:
    provider = str(value or "").strip().lower()
    return provider if provider in SUPPORTED_LLM_PROVIDERS else None


def resolve_llm_provider(settings: Any) -> str:
    explicit = normalize_llm_provider(getattr(settings, "llm_provider", None))
    if explicit is not None:
        return explicit
    if getattr(settings, "claude_api_key", None):
        return "claude"
    if getattr(settings, "deepseek_api_key", None):
        return "deepseek"
    if getattr(settings, "openrouter_api_key", None):
        return "openrouter"
    return "claude"


def infer_llm_provider(model: str | None) -> str | None:
    model_name = str(model or "").strip().lower()
    if not model_name:
        return None
    if model_name.startswith("deepseek"):
        return "deepseek"
    if "/" in model_name:
        return "openrouter"
    return "claude"


def resolve_llm_thinking_mode(
    settings: Any,
    *,
    provider: str | None = None,
    override: str | None = None,
) -> str:
    if override is not None:
        return str(override).strip().lower()
    resolved_provider = normalize_llm_provider(provider) or resolve_llm_provider(settings)
    if resolved_provider == "claude":
        return str(getattr(settings, "claude_thinking", "") or "").strip().lower()
    if resolved_provider == "deepseek":
        model = str(getattr(settings, "deepseek_model", "") or "").strip().lower()
        if model == "deepseek-reasoner":
            return "enabled"
        if model == "deepseek-chat":
            return "disabled"
    return ""


def resolve_llm_model(
    settings: Any,
    *,
    provider: str | None = None,
    thinking_override: str | None = None,
) -> str:
    resolved_provider = normalize_llm_provider(provider) or resolve_llm_provider(settings)
    thinking_type = resolve_llm_thinking_mode(
        settings,
        provider=resolved_provider,
        override=thinking_override,
    )
    if resolved_provider == "deepseek":
        model = str(getattr(settings, "deepseek_model", "deepseek-reasoner"))
        if model in {"deepseek-chat", "deepseek-reasoner"} and thinking_type in {
            "enabled",
            "disabled",
        }:
            return "deepseek-reasoner" if thinking_type == "enabled" else "deepseek-chat"
        return model
    if resolved_provider == "openrouter":
        legacy_model = str(
            getattr(settings, "openrouter_model", "openai/gpt-4.1-mini")
            or "openai/gpt-4.1-mini"
        )
        reasoning_model = str(
            getattr(settings, "openrouter_reasoning_model", "") or ""
        ).strip()
        fast_model = str(getattr(settings, "openrouter_fast_model", "") or "").strip()
        if thinking_type == "enabled":
            return reasoning_model or legacy_model
        if thinking_type == "disabled":
            return fast_model or legacy_model
        if reasoning_model and fast_model and reasoning_model != fast_model:
            return reasoning_model
        return reasoning_model or fast_model or legacy_model
    return str(getattr(settings, "claude_model", "claude-k2.5") or "claude-k2.5")


def default_llm_model_display(settings: Any, *, provider: str | None = None) -> str:
    resolved_provider = normalize_llm_provider(provider) or resolve_llm_provider(settings)
    if resolved_provider == "deepseek":
        return str(getattr(settings, "deepseek_model", "deepseek-reasoner") or "deepseek-reasoner")
    if resolved_provider == "openrouter":
        reasoning_model = str(
            getattr(settings, "openrouter_reasoning_model", "") or ""
        ).strip()
        fast_model = str(getattr(settings, "openrouter_fast_model", "") or "").strip()
        legacy_model = str(
            getattr(settings, "openrouter_model", "openai/gpt-4.1-mini")
            or "openai/gpt-4.1-mini"
        )
        if reasoning_model and fast_model and reasoning_model != fast_model:
            return f"{reasoning_model} / {fast_model}"
        return reasoning_model or fast_model or legacy_model
    return str(getattr(settings, "claude_model", "claude-k2.5") or "claude-k2.5")


def resolve_llm_api_key(settings: Any, *, provider: str | None = None) -> str | None:
    resolved_provider = normalize_llm_provider(provider) or resolve_llm_provider(settings)
    if resolved_provider == "deepseek":
        return getattr(settings, "deepseek_api_key", None)
    if resolved_provider == "openrouter":
        return getattr(settings, "openrouter_api_key", None)
    return getattr(settings, "claude_api_key", None)


def resolve_llm_base_url(settings: Any, *, provider: str | None = None) -> str:
    resolved_provider = normalize_llm_provider(provider) or resolve_llm_provider(settings)
    if resolved_provider == "deepseek":
        return str(getattr(settings, "deepseek_base_url", "https://api.deepseek.com"))
    if resolved_provider == "openrouter":
        return str(
            getattr(settings, "openrouter_base_url", "https://openrouter.ai/api/v1")
        )
    return str(getattr(settings, "claude_base_url", "https://api.moonshot.ai/v1"))

