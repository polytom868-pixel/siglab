from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Final, cast, overload

import pandas as pd

from siglab.utils import short_hash


_DEFAULT_PORT = int(os.environ.get("PORT", "8080"))


# ---------------------------------------------------------------------------
# Track registry — constants + helpers (formerly siglab.track_registry)
# ---------------------------------------------------------------------------

CANONICAL_TRACKS: Final[tuple[str, ...]] = ("trend_signals", "yield_flows")
TRACK_ALIASES: Final[dict[str, str]] = {
    "trend_signals": "trend_signals",
    "yield_flows": "yield_flows",
}
TRACK_STORAGE_NAMES: Final[dict[str, str]] = {
    "trend_signals": "trend_signals",
    "yield_flows": "yield_flows",
}
TRACK_LABELS: Final[dict[str, str]] = {
    "trend_signals": "Directional Perps",
    "yield_flows": "Systematic Carry",
}
TRACK_CLI_CHOICES: Final[tuple[str, ...]] = ("trend_signals", "yield_flows")


def canonical_track_name(track: str | None) -> str | None:
    if track is None:
        return None
    return TRACK_ALIASES.get(track, track)


def resolve_track(raw: str | None) -> str | None:
    return canonical_track_name(raw) or raw


def storage_track_name(track: str | None) -> str | None:
    canonical = canonical_track_name(track)
    if canonical is None:
        return None
    return TRACK_STORAGE_NAMES.get(canonical, canonical)


def track_label(track: str | None) -> str:
    canonical = canonical_track_name(track)
    if canonical is None:
        return "Unknown Track"
    return TRACK_LABELS.get(canonical, canonical.replace("_", " ").title())


def load_track_family_specs(root_dir: Path, track: str) -> dict[str, Any]:
    import yaml
    payload = yaml.safe_load((root_dir / "mutable" / "family_lab.yaml").read_text())
    return cast(
        dict[str, Any],
        payload.get("tracks", {})
        .get(storage_track_name(track) or track, {})
        .get("families", {}),
    )


def load_family_spec(root_dir: Path, track: str, family: str) -> dict[str, Any]:
    return dict(load_track_family_specs(root_dir, track).get(family) or {})


def family_capabilities(spec: dict[str, Any] | None) -> dict[str, Any]:
    return dict((spec or {}).get("capabilities") or {})


def _family_capability(spec: dict[str, Any] | None, key: str) -> str | None:
    value = family_capabilities(spec).get(key)
    return str(value) if value is not None else None


def family_execution_profile(spec: dict[str, Any] | None) -> str | None:
    return _family_capability(spec, "execution_profile")


def family_diagnostic_adapter(spec: dict[str, Any] | None) -> str | None:
    return _family_capability(spec, "diagnostic_adapter")


def family_policy_schema(spec: dict[str, Any] | None) -> str | None:
    return _family_capability(spec, "policy_schema")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


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
    openmodel_base_url: str = "https://api.openmodel.ai"
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
        openmodel_base_url=_get("OPENMODEL_BASE_URL", "https://api.openmodel.ai"),
        openmodel_model=_get("OPENMODEL_MODEL", "deepseek-v4-flash"),
        memory_scope=_get("SIGLAB_MEMORY_SCOPE", default="session_local"),
        population_size=int(_get("SIGLAB_POPULATION_SIZE", "4")),
    )


# ---------------------------------------------------------------------------
# Schemas (formerly siglab.schemas)
# ---------------------------------------------------------------------------


@dataclass
class AssetUniverse:
    basis_groups: list[str] = field(default_factory=list)
    chains: list[str] = field(default_factory=list)
    max_symbols: int = 6
    lookback_days: int = 90
    interval: str = "1h"
    min_liquidity_usd: float = 250000.0
    min_volume_usd_24h: float = 25000.0
    min_days_to_expiry: int = 7
    max_days_to_expiry: int = 180

    @classmethod
    def from_dict(
        cls: type[AssetUniverse],
        payload: dict[str, Any] | None,
    ) -> AssetUniverse:
        payload = payload or {}
        return cls(
            basis_groups=list(payload.get("basis_groups") or []),
            chains=list(payload.get("chains") or []),
            max_symbols=int(payload.get("max_symbols", 6)),
            lookback_days=int(payload.get("lookback_days", 90)),
            interval=str(payload.get("interval", "1h")),
            min_liquidity_usd=float(payload.get("min_liquidity_usd", 250000.0)),
            min_volume_usd_24h=float(payload.get("min_volume_usd_24h", 25000.0)),
            min_days_to_expiry=int(payload.get("min_days_to_expiry", 7)),
            max_days_to_expiry=int(payload.get("max_days_to_expiry", 180)),
        )


@dataclass
class RiskBounds:
    max_asset_weight: float = 0.35
    max_chain_weight: float = 1.0
    rebalance_threshold: float = 0.03
    roll_days_before_expiry: int = 5
    max_leverage: float = 1.0

    @classmethod
    def from_dict(cls: type[RiskBounds], payload: dict[str, Any] | None) -> RiskBounds:
        payload = payload or {}
        return cls(
            max_asset_weight=float(payload.get("max_asset_weight", 0.35)),
            max_chain_weight=float(payload.get("max_chain_weight", 1.0)),
            rebalance_threshold=float(payload.get("rebalance_threshold", 0.03)),
            roll_days_before_expiry=int(payload.get("roll_days_before_expiry", 5)),
            max_leverage=float(payload.get("max_leverage", 1.0)),
        )


@dataclass
class SignalSpec:
    track: str
    family: str
    hypothesis: str
    neutrality_basis: str | None
    features: list[str]
    universe: AssetUniverse = field(default_factory=AssetUniverse)
    risk: RiskBounds = field(default_factory=RiskBounds)
    regime_gates: dict[str, Any] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls: type[SignalSpec], payload: dict[str, Any]) -> SignalSpec:
        return cls(
            track=str(payload["track"]),
            family=str(payload["family"]),
            hypothesis=str(payload.get("hypothesis", "")).strip(),
            neutrality_basis=payload.get("neutrality_basis"),
            features=[str(item) for item in payload.get("features") or []],
            universe=AssetUniverse.from_dict(payload.get("universe")),
            risk=RiskBounds.from_dict(payload.get("risk")),
            regime_gates=dict(payload.get("regime_gates") or {}),
            params=dict(payload.get("params") or {}),
        )

    def canonical_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["features"] = sorted(set(self.features))
        payload["params"] = dict(sorted(self.params.items()))
        return payload

    def strategy_hash(self) -> str:
        canonical = json.dumps(
            self.canonical_dict(),
            sort_keys=True,
            separators=(",", ":"),
        )
        return short_hash(canonical)


@dataclass
class CompiledChild:
    prices: pd.DataFrame
    target_positions: pd.DataFrame
    funding_rates: pd.DataFrame | None
    metadata: dict[str, Any]
    signal_score: pd.DataFrame | None = None
    signal_components: dict[str, pd.DataFrame] | None = None
    regime_gate_mask: pd.Series | None = None
