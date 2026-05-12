from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from siglab.llm_metadata import normalize_llm_provider
from siglab.track_registry import CANONICAL_TRACKS


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
class SiglabConfig:
    root_dir: Path
    sosovalue_config_path: Path
    generated_strategy_dir: Path
    data_lake_dir: Path
    artifact_dir: Path
    live_dir: Path
    ancestry_db_path: Path
    sosovalue_api_key_override: str | None
    claude_api_key: str | None
    claude_model: str
    claude_base_url: str
    claude_max_tokens: int
    claude_temperature: float
    claude_top_p: float
    claude_timeout_s: float
    population_size: int
    llm_provider: str = "claude"
    optuna_trials: int = 20
    memory_scope: str = "session_local"
    use_historical_seeds: bool = False
    claude_thinking: str | None = None
    claude_max_tool_rounds: int = 25
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


def load_settings() -> SiglabConfig:
    root_dir = Path(__file__).resolve().parents[1]
    env_values = _read_env_file(root_dir / ".env")

    def _get(name: str, default: str | None = None) -> str | None:
        return os.getenv(name) or env_values.get(name) or default

    def _get_bool(name: str, default: bool = False) -> bool:
        raw = _get(name)
        if raw is None:
            return default
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}

    config_value = _get("SOSOVALUE_CONFIG_PATH")
    strategy_export_value = _get(
        "SIGLAB_STRATEGY_EXPORT_DIR",
        str(root_dir / "siglab" / "live" / "deployed_agents"),
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
    elif _get("CLAUDE_API_KEY"):
        llm_provider = "claude"
    elif _get("DEEPSEEK_API_KEY"):
        llm_provider = "deepseek"
    elif _get("OPENROUTER_API_KEY") or _get("OPENROUTER_KEY"):
        llm_provider = "openrouter"
    else:
        llm_provider = "claude"

    return SiglabConfig(
        root_dir=root_dir,
        sosovalue_config_path=config_path,
        generated_strategy_dir=strategy_export_dir,
        data_lake_dir=root_dir / "data" / "lake",
        artifact_dir=root_dir / "runs",
        live_dir=root_dir / "live",
        ancestry_db_path=root_dir / "siglab.db",
        sosovalue_api_key_override=_get("SOSOVALUE_API_KEY"),
        claude_api_key=_get("CLAUDE_API_KEY"),
        claude_model=str(_get("CLAUDE_MODEL", "claude-k2.5")),
        claude_base_url=str(_get("CLAUDE_BASE_URL", "https://api.moonshot.ai/v1")),
        claude_max_tokens=int(_get("CLAUDE_MAX_TOKENS", "32768")),
        claude_temperature=float(_get("CLAUDE_TEMPERATURE", "1.0")),
        claude_top_p=float(_get("CLAUDE_TOP_P", "0.95")),
        claude_timeout_s=float(_get("CLAUDE_TIMEOUT_S", "300")),
        llm_provider=llm_provider,
        claude_thinking=_get("CLAUDE_THINKING"),
        claude_max_tool_rounds=int(_get("CLAUDE_MAX_TOOL_ROUNDS", "25")),
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
        population_size=int(_get("SIGLAB_POPULATION_SIZE", "4")),
        optuna_trials=int(_get("SIGLAB_OPTUNA_TRIALS", "20")),
        memory_scope=str(_get("SIGLAB_MEMORY_SCOPE", "session_local")),
        use_historical_seeds=_get_bool("SIGLAB_USE_HISTORICAL_SEEDS", False),
        tavily_api_key=_get("TAVILY_API_KEY"),
        tavily_base_url=str(_get("TAVILY_BASE_URL", "https://api.tavily.com")),
        tavily_max_results=int(_get("TAVILY_MAX_RESULTS", "5")),
        web_explore_results_per_query=int(_get("WEB_EXPLORE_RESULTS_PER_QUERY", "2")),
    )


