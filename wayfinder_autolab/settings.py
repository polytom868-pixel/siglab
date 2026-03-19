from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from wayfinder_autolab.llm_metadata import normalize_llm_provider
from wayfinder_autolab.track_registry import CANONICAL_TRACKS


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'").strip('"')
    return values


@dataclass
class AutolabSettings:
    root_dir: Path
    wayfinder_config_path: Path
    generated_strategy_dir: Path
    data_lake_dir: Path
    artifact_dir: Path
    live_dir: Path
    lineage_db_path: Path
    wayfinder_api_key_override: str | None
    kimi_api_key: str | None
    kimi_model: str
    kimi_base_url: str
    kimi_max_tokens: int
    kimi_temperature: float
    kimi_top_p: float
    kimi_timeout_s: float
    population_size: int
    llm_provider: str = "kimi"
    optuna_trials: int = 20
    memory_scope: str = "run_local"
    use_historical_seeds: bool = False
    kimi_thinking: str | None = None
    kimi_max_tool_rounds: int = 25
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-reasoner"
    openrouter_api_key: str | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = "openai/gpt-4.1-mini"
    openrouter_reasoning_model: str | None = None
    openrouter_fast_model: str | None = None
    openrouter_http_referer: str | None = None
    openrouter_title: str | None = None
    tracks: tuple[str, ...] = CANONICAL_TRACKS
    tavily_api_key: str | None = None
    tavily_base_url: str = "https://api.tavily.com"
    tavily_max_results: int = 5
    web_explore_results_per_query: int = 2

    def ensure_runtime_directories(self) -> None:
        self.data_lake_dir.mkdir(parents=True, exist_ok=True)
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self.live_dir.mkdir(parents=True, exist_ok=True)
        self.generated_strategy_dir.mkdir(parents=True, exist_ok=True)


def load_settings() -> AutolabSettings:
    root_dir = Path(__file__).resolve().parents[1]
    env_values = _read_env_file(root_dir / ".env")

    def _get(name: str, default: str | None = None) -> str | None:
        return os.getenv(name) or env_values.get(name) or default

    def _get_bool(name: str, default: bool = False) -> bool:
        raw = _get(name)
        if raw is None:
            return default
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}

    config_value = _get("WAYFINDER_CONFIG_PATH")
    strategy_export_value = _get(
        "AUTOLAB_STRATEGY_EXPORT_DIR",
        str(root_dir / "wayfinder_autolab" / "live" / "generated_strategies"),
    )
    config_path = Path(config_value).expanduser() if config_value else (root_dir / "config.json")
    if not config_path.is_absolute():
        config_path = (root_dir / config_path).resolve()
    strategy_export_dir = Path(strategy_export_value).expanduser()
    if not strategy_export_dir.is_absolute():
        strategy_export_dir = (root_dir / strategy_export_dir).resolve()

    explicit_provider = normalize_llm_provider(_get("LLM_PROVIDER"))
    if explicit_provider is not None:
        llm_provider = explicit_provider
    elif _get("KIMI_API_KEY"):
        llm_provider = "kimi"
    elif _get("DEEPSEEK_API_KEY"):
        llm_provider = "deepseek"
    elif _get("OPENROUTER_API_KEY") or _get("OPENROUTER_KEY"):
        llm_provider = "openrouter"
    else:
        llm_provider = "kimi"

    return AutolabSettings(
        root_dir=root_dir,
        wayfinder_config_path=config_path,
        generated_strategy_dir=strategy_export_dir,
        data_lake_dir=root_dir / "data" / "lake",
        artifact_dir=root_dir / "artifacts",
        live_dir=root_dir / "live",
        lineage_db_path=root_dir / "wayfinder_autolab.db",
        wayfinder_api_key_override=_get("WAYFINDER_API_KEY"),
        kimi_api_key=_get("KIMI_API_KEY"),
        kimi_model=str(_get("KIMI_MODEL", "kimi-k2.5")),
        kimi_base_url=str(_get("KIMI_BASE_URL", "https://api.moonshot.ai/v1")),
        kimi_max_tokens=int(_get("KIMI_MAX_TOKENS", "32768")),
        kimi_temperature=float(_get("KIMI_TEMPERATURE", "1.0")),
        kimi_top_p=float(_get("KIMI_TOP_P", "0.95")),
        kimi_timeout_s=float(_get("KIMI_TIMEOUT_S", "300")),
        llm_provider=llm_provider,
        kimi_thinking=_get("KIMI_THINKING"),
        kimi_max_tool_rounds=int(_get("KIMI_MAX_TOOL_ROUNDS", "25")),
        deepseek_api_key=_get("DEEPSEEK_API_KEY"),
        deepseek_base_url=str(_get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")),
        deepseek_model=str(_get("DEEPSEEK_MODEL", "deepseek-reasoner")),
        openrouter_api_key=_get("OPENROUTER_API_KEY") or _get("OPENROUTER_KEY"),
        openrouter_base_url=str(_get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")),
        openrouter_model=str(_get("OPENROUTER_MODEL", "openai/gpt-4.1-mini")),
        openrouter_reasoning_model=_get("OPENROUTER_REASONING_MODEL"),
        openrouter_fast_model=_get("OPENROUTER_FAST_MODEL"),
        openrouter_http_referer=_get("OPENROUTER_HTTP_REFERER"),
        openrouter_title=_get("OPENROUTER_TITLE"),
        population_size=int(_get("AUTOLAB_POPULATION_SIZE", "4")),
        optuna_trials=int(_get("AUTOLAB_OPTUNA_TRIALS", "20")),
        memory_scope=str(_get("AUTOLAB_MEMORY_SCOPE", "run_local")),
        use_historical_seeds=_get_bool("AUTOLAB_USE_HISTORICAL_SEEDS", False),
        tavily_api_key=_get("TAVILY_API_KEY"),
        tavily_base_url=str(_get("TAVILY_BASE_URL", "https://api.tavily.com")),
        tavily_max_results=int(_get("TAVILY_MAX_RESULTS", "5")),
        web_explore_results_per_query=int(_get("WEB_EXPLORE_RESULTS_PER_QUERY", "2")),
    )
