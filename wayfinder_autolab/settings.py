from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

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
    optuna_trials: int = 20
    memory_scope: str = "run_local"
    kimi_thinking: str | None = None
    kimi_max_tool_rounds: int = 25
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
        kimi_thinking=_get("KIMI_THINKING"),
        kimi_max_tool_rounds=int(_get("KIMI_MAX_TOOL_ROUNDS", "25")),
        population_size=int(_get("AUTOLAB_POPULATION_SIZE", "4")),
        optuna_trials=int(_get("AUTOLAB_OPTUNA_TRIALS", "20")),
        memory_scope=str(_get("AUTOLAB_MEMORY_SCOPE", "run_local")),
        tavily_api_key=_get("TAVILY_API_KEY"),
        tavily_base_url=str(_get("TAVILY_BASE_URL", "https://api.tavily.com")),
        tavily_max_results=int(_get("TAVILY_MAX_RESULTS", "5")),
        web_explore_results_per_query=int(_get("WEB_EXPLORE_RESULTS_PER_QUERY", "2")),
    )
