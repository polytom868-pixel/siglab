from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from siglab.config import SiglabConfig

SUPPORTED_LLM_PROVIDERS = frozenset({"openai"})


def normalize_llm_provider(value: str | None) -> str | None:
    provider = str(value or "").strip().lower()
    return provider if provider in SUPPORTED_LLM_PROVIDERS else None


def resolve_llm_provider(settings: SiglabConfig) -> str:
    explicit = normalize_llm_provider(getattr(settings, "llm_provider", None))
    if explicit is not None:
        return explicit
    if getattr(settings, "openmodel_api_key", None):
        return "openai"
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
    return str(
        getattr(settings, "openmodel_model", "deepseek-v4-flash") or "deepseek-v4-flash",
    )


def default_llm_model_display(
    settings: SiglabConfig,
    *,
    provider: str | None = None,
) -> str:
    return str(
        getattr(settings, "openmodel_model", "deepseek-v4-flash") or "deepseek-v4-flash",
    )


def resolve_llm_api_key(
    settings: SiglabConfig,
    *,
    provider: str | None = None,
) -> str | None:
    return getattr(settings, "openmodel_api_key", None)


def resolve_llm_base_url(settings: SiglabConfig, *, provider: str | None = None) -> str:
    return str(getattr(settings, "openmodel_base_url", "https://api.b.ai"))

