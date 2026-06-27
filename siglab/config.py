from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import overload

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
    sosovalue_base_url: str = "https://openapi.sosovalue.com"
    etf_base_url: str = "https://openapi.sosovalue.com"
    news_base_url: str = "https://openapi.sosovalue.com"
    sosovalue_timeout_s: float = 30.0
    sosovalue_retries: int = 2
    claude_timeout_s: float = 300.0
    population_size: int = 4
    llm_provider: str = "openai"
    memory_scope: str = "session_local"
    claude_max_tool_rounds: int = 25
    openmodel_api_key: str | None = None
    openmodel_base_url: str = "https://api.openmodel.ai/v1"
    openmodel_model: str = "deepseek-v4-flash"
    tracks: tuple[str, ...] = CANONICAL_TRACKS

    def ensure_runtime_directories(self) -> None:
        self.data_lake_dir.mkdir(parents=True, exist_ok=True)
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self.live_dir.mkdir(parents=True, exist_ok=True)
        self.generated_strategy_dir.mkdir(parents=True, exist_ok=True)


def load_settings() -> SiglabConfig:
    root_dir = Path(__file__).resolve().parents[1]
    env_values = _read_env_file(root_dir / ".env")
    provider_config_value = os.getenv("SIGLAB_PROVIDER_CONFIG_PATH") or env_values.get(
        "SIGLAB_PROVIDER_CONFIG_PATH",
    )
    provider_config_path = (
        Path(provider_config_value).expanduser()
        if provider_config_value
        else root_dir / ".siglab-provider.env"
    )
    if not provider_config_path.is_absolute():
        provider_config_path = (root_dir / provider_config_path).resolve()
    env_values = {**env_values, **_read_env_file(provider_config_path)}

    @overload
    def _get(name: str, default: str) -> str: ...

    @overload
    def _get(name: str, default: None = None) -> str | None: ...

    def _get(name: str, default: str | None = None) -> str | None:
        return os.getenv(name) or env_values.get(name) or default


    config_value = _get("SOSOVALUE_CONFIG_PATH")
    strategy_export_value = _get(
        "SIGLAB_STRATEGY_EXPORT_DIR",
        str(root_dir / "siglab" / "live" / "deployed_agents"),
    )
    config_path = (
        Path(config_value).expanduser() if config_value else root_dir / "config.json"
    )
    if not config_path.is_absolute():
        config_path = (root_dir / config_path).resolve()
    strategy_export_dir = Path(strategy_export_value).expanduser()
    if not strategy_export_dir.is_absolute():
        strategy_export_dir = (root_dir / strategy_export_dir).resolve()
    llm_provider = _get("LLM_PROVIDER", default="openai")
    sosovalue_api_key_override = _get("SOSOVALUE_API_KEY")
    if sosovalue_api_key_override is None and config_path.exists():
        try:
            with open(config_path) as f:
                cfg = json.load(f)
            sosovalue_api_key_override = cfg.get("system", {}).get("api_key")
        except (json.JSONDecodeError, OSError):
            pass
    return SiglabConfig(
        root_dir=root_dir,
        sosovalue_config_path=config_path,
        generated_strategy_dir=strategy_export_dir,
        data_lake_dir=root_dir / "data" / "cache",
        artifact_dir=root_dir / "runs",
        live_dir=root_dir / "live",
        ancestry_db_path=root_dir / "siglab.db",
        etf_base_url=_get("SOSOVALUE_ETF_BASE_URL", "https://openapi.sosovalue.com"),
        news_base_url=_get("SOSOVALUE_NEWS_BASE_URL", "https://openapi.sosovalue.com"),
        sosovalue_base_url=_get("SOSOVALUE_BASE_URL", "https://openapi.sosovalue.com"),
        sosovalue_timeout_s=float(_get("SOSOVALUE_TIMEOUT_S", "30")),
        sosovalue_retries=int(_get("SOSOVALUE_RETRIES", "2")),
        sosovalue_api_key_override=sosovalue_api_key_override,
        claude_timeout_s=float(_get("CLAUDE_TIMEOUT_S", "300")),
        llm_provider=llm_provider,
        claude_max_tool_rounds=int(_get("CLAUDE_MAX_TOOL_ROUNDS", "25")),
        openmodel_api_key=_get("OPENMODEL_API_KEY"),
        openmodel_base_url=_get("OPENMODEL_BASE_URL", "https://api.openmodel.ai/v1"),
        openmodel_model=_get("OPENMODEL_MODEL", "deepseek-v4-flash"),
        population_size=int(_get("SIGLAB_POPULATION_SIZE", "4")),
    )
