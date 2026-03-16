from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from hashlib import sha256
from typing import Any

import pandas as pd


@dataclass
class UniverseSpec:
    basis_groups: list[str] = field(default_factory=list)
    chains: list[str] = field(default_factory=list)
    max_symbols: int = 6
    lookback_days: int = 90
    interval: str = "1h"
    min_liquidity_usd: float = 250_000.0
    min_volume_usd_24h: float = 25_000.0
    min_days_to_expiry: int = 7
    max_days_to_expiry: int = 180

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "UniverseSpec":
        payload = payload or {}
        return cls(
            basis_groups=list(payload.get("basis_groups") or []),
            chains=list(payload.get("chains") or []),
            max_symbols=int(payload.get("max_symbols", 6)),
            lookback_days=int(payload.get("lookback_days", 90)),
            interval=str(payload.get("interval", "1h")),
            min_liquidity_usd=float(payload.get("min_liquidity_usd", 250_000.0)),
            min_volume_usd_24h=float(payload.get("min_volume_usd_24h", 25_000.0)),
            min_days_to_expiry=int(payload.get("min_days_to_expiry", 7)),
            max_days_to_expiry=int(payload.get("max_days_to_expiry", 180)),
        )


@dataclass
class RiskSpec:
    max_asset_weight: float = 0.35
    max_chain_weight: float = 1.0
    rebalance_threshold: float = 0.03
    roll_days_before_expiry: int = 5
    max_leverage: float = 1.0

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "RiskSpec":
        payload = payload or {}
        return cls(
            max_asset_weight=float(payload.get("max_asset_weight", 0.35)),
            max_chain_weight=float(payload.get("max_chain_weight", 1.0)),
            rebalance_threshold=float(payload.get("rebalance_threshold", 0.03)),
            roll_days_before_expiry=int(payload.get("roll_days_before_expiry", 5)),
            max_leverage=float(payload.get("max_leverage", 1.0)),
        )


@dataclass
class CandidateGraph:
    track: str
    family: str
    hypothesis: str
    neutrality_basis: str | None
    features: list[str]
    universe: UniverseSpec = field(default_factory=UniverseSpec)
    risk: RiskSpec = field(default_factory=RiskSpec)
    regime_gates: dict[str, Any] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CandidateGraph":
        return cls(
            track=str(payload["track"]),
            family=str(payload["family"]),
            hypothesis=str(payload.get("hypothesis", "")).strip(),
            neutrality_basis=payload.get("neutrality_basis"),
            features=[str(item) for item in payload.get("features") or []],
            universe=UniverseSpec.from_dict(payload.get("universe")),
            risk=RiskSpec.from_dict(payload.get("risk")),
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
        return sha256(canonical.encode("utf-8")).hexdigest()[:16]


@dataclass
class CompiledChild:
    prices: pd.DataFrame
    target_positions: pd.DataFrame
    funding_rates: pd.DataFrame | None
    metadata: dict[str, Any]
    signal_score: pd.DataFrame | None = None
    signal_components: dict[str, pd.DataFrame] | None = None
    regime_gate_mask: pd.Series | None = None
