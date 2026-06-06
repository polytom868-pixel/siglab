"""Tests for siglab.schemas — dataclass construction, fingerprinting."""
from __future__ import annotations

import json

from siglab.schemas import AssetUniverse, RiskBounds, SignalSpec


class TestAssetUniverse:
    def test_defaults(self):
        u = AssetUniverse()
        assert u.max_symbols == 6
        assert u.interval == "1h"
        assert u.min_liquidity_usd == 250_000.0
        assert u.basis_groups == []
        assert u.chains == []

    def test_from_dict(self):
        payload = {"max_symbols": 4, "interval": "4h", "min_liquidity_usd": 100_000}
        u = AssetUniverse.from_dict(payload)
        assert u.max_symbols == 4
        assert u.interval == "4h"
        assert u.min_liquidity_usd == 100_000.0

    def test_from_dict_none(self):
        u = AssetUniverse.from_dict(None)
        assert u.max_symbols == 6

    def test_from_dict_partial(self):
        u = AssetUniverse.from_dict({"chains": ["ethereum", "solana"]})
        assert u.chains == ["ethereum", "solana"]
        assert u.max_symbols == 6


class TestRiskBounds:
    def test_defaults(self):
        r = RiskBounds()
        assert r.max_asset_weight == 0.35
        assert r.max_leverage == 1.0
        assert r.rebalance_threshold == 0.03
        assert r.roll_days_before_expiry == 5

    def test_from_dict(self):
        payload = {"max_leverage": 2.0, "max_asset_weight": 0.5}
        r = RiskBounds.from_dict(payload)
        assert r.max_leverage == 2.0
        assert r.max_asset_weight == 0.5
        assert r.rebalance_threshold == 0.03  # default preserved

    def test_from_dict_none(self):
        r = RiskBounds.from_dict(None)
        assert r.max_leverage == 1.0


class TestSignalSpec:
    def test_minimal_construction(self):
        s = SignalSpec(
            track="trend_signals",
            family="momentum",
            hypothesis="test",
            neutrality_basis=None,
            features=["f1"],
        )
        assert s.track == "trend_signals"
        assert s.family == "momentum"
        assert s.features == ["f1"]
        assert isinstance(s.universe, AssetUniverse)
        assert isinstance(s.risk, RiskBounds)

    def test_from_dict(self):
        payload = {
            "track": "yield_flows",
            "family": "carry",
            "hypothesis": "funding carry",
            "neutrality_basis": "USD",
            "features": ["funding_72h", "price_return_24h"],
            "universe": {"max_symbols": 3},
            "risk": {"max_leverage": 1.5},
        }
        s = SignalSpec.from_dict(payload)
        assert s.track == "yield_flows"
        assert s.universe.max_symbols == 3
        assert s.risk.max_leverage == 1.5

    def test_canonical_dict_features_sorted(self):
        s = SignalSpec(
            track="t",
            family="f",
            hypothesis="h",
            neutrality_basis=None,
            features=["z_feature", "a_feature", "m_feature"],
        )
        canon = s.canonical_dict()
        assert canon["features"] == ["a_feature", "m_feature", "z_feature"]

    def test_canonical_dict_params_sorted(self):
        s = SignalSpec(
            track="t",
            family="f",
            hypothesis="h",
            neutrality_basis=None,
            features=[],
            params={"z_param": 1, "a_param": 2},
        )
        canon = s.canonical_dict()
        keys = list(canon["params"].keys())
        assert keys == ["a_param", "z_param"]

    def test_strategy_hash_deterministic(self):
        s = SignalSpec(
            track="t",
            family="f",
            hypothesis="h",
            neutrality_basis=None,
            features=["a", "b"],
        )
        h1 = s.strategy_hash()
        h2 = s.strategy_hash()
        assert h1 == h2
        assert len(h1) == 16

    def test_strategy_hash_differs_with_features(self):
        s1 = SignalSpec(track="t", family="f", hypothesis="h", neutrality_basis=None, features=["a"])
        s2 = SignalSpec(track="t", family="f", hypothesis="h", neutrality_basis=None, features=["b"])
        assert s1.strategy_hash() != s2.strategy_hash()

    def test_default_regime_gates(self):
        s = SignalSpec(track="t", family="f", hypothesis="h", neutrality_basis=None, features=[])
        assert s.regime_gates == {}
        assert s.params == {}
