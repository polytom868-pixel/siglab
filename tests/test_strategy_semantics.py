"""Tests for siglab.evaluation.strategy_semantics."""
from __future__ import annotations

from siglab.evaluation.strategy_semantics import (
    feature_roles_for_formula,
    gate_dimensions,
    inferred_trade_style,
    motif_signature,
    normalized_gate_entries,
    spec_feature_roles,
    supports_explicit_trade_style,
    trade_style_bucket,
)


class TestInferredTradeStyle:
    def test_momentum_hybrid(self):
        spec = {"family": "momentum", "features": [{"role": "regime"}]}
        # momentum is not a pair_trade family so falls through keyword detection
        style = inferred_trade_style(spec)
        assert style in {"continuation", "hybrid", "carry", "breakout", "pullback", "reversion", "directional", "basket_neutral"}

    def test_carry_family(self):
        spec = {"family": "carry_funding", "features": []}
        assert inferred_trade_style(spec) == "carry"

    def test_basket_family(self):
        spec = {"family": "basket_neutral", "features": []}
        assert inferred_trade_style(spec) == "basket_neutral"

    def test_decision_family(self):
        spec = {"family": "perp_multi_asset_decision", "features": []}
        assert inferred_trade_style(spec) == "directional"

    def test_breakout_keyword(self):
        spec = {"family": "generic", "features": ["donchian_position_20"], "hypothesis": ""}
        assert inferred_trade_style(spec) == "breakout"

    def test_reversion_keyword(self):
        spec = {"family": "generic", "features": ["bollinger_zscore"], "hypothesis": ""}
        assert inferred_trade_style(spec) == "reversion"

    def test_explicit_on_pair_trade_family(self):
        spec = {
            "family": "perp_pair_trade_unlevered",
            "features": [],
            "params": {"trade_style": "reversion"},
        }
        assert inferred_trade_style(spec) == "reversion"

    def test_explicit_ignored_on_non_pair_family(self):
        spec = {
            "family": "momentum",
            "features": [],
            "params": {"trade_style": "reversion"},
        }
        # Should NOT return reversion because momentum doesn't support explicit
        style = inferred_trade_style(spec)
        assert style != "reversion"


class TestTradeStyleBucket:
    def test_non_pair_family_returns_cross_sectional(self):
        payload = {"family": "momentum", "params": {"trade_style": "reversion"}}
        assert trade_style_bucket(payload) == "cross_sectional"

    def test_pair_family_with_style(self):
        payload = {"family": "perp_pair_trade_unlevered", "params": {"trade_style": "breakout"}}
        assert trade_style_bucket(payload) == "breakout"

    def test_pair_family_no_style(self):
        payload = {"family": "perp_pair_trade_levered", "params": {}}
        assert trade_style_bucket(payload) == "unspecified"


class TestMotifSignature:
    def test_deterministic(self):
        payload = {
            "family": "momentum",
            "features": ["momentum_score", "trend_strength"],
            "regime_gates": {"entry": ["volatility(low)"]},
        }
        sig1 = motif_signature(payload)
        sig2 = motif_signature(payload)
        assert sig1 == sig2

    def test_contains_family(self):
        payload = {"family": "carry", "features": []}
        sig = motif_signature(payload)
        assert sig.startswith("carry|")

    def test_different_features_different_sig(self):
        p1 = {"family": "f", "features": ["momentum_score"]}
        p2 = {"family": "f", "features": ["bollinger_zscore"]}
        assert motif_signature(p1) != motif_signature(p2)

    def test_empty_features(self):
        payload = {"family": "f", "features": []}
        sig = motif_signature(payload)
        assert "uncategorized" in sig


class TestGateDimensions:
    def test_empty_gates(self):
        assert gate_dimensions(None) == []
        assert gate_dimensions({}) == []

    def test_string_entry(self):
        # gate_dimensions extracts the first inner argument of the call
        gates = {"entry": ["volatility(low)"]}
        dims = gate_dimensions(gates)
        assert dims == ["low"]

    def test_dict_entry(self):
        gates = {"entry": [{"expression": "trend_strength(threshold)"}]}
        dims = gate_dimensions(gates)
        assert dims == ["threshold"]

    def test_mixed_entries(self):
        gates = {"entry": ["volatility(low)", {"expression": "corr(metric)"}]}
        dims = gate_dimensions(gates)
        assert dims == ["low", "metric"]


class TestNormalizedGateEntries:
    def test_empty(self):
        assert normalized_gate_entries(None) == []
        assert normalized_gate_entries({}) == []

    def test_string_gate(self):
        entries = normalized_gate_entries({"entry": ["volatility(low)"]})
        assert entries == [{"expression": "volatility(low)", "kind": "string"}]

    def test_dict_gate_with_bounds(self):
        entries = normalized_gate_entries({"entry": [{"expression": "trend", "min": 0.5, "max": 1.0}]})
        assert len(entries) == 1
        assert entries[0]["min"] == 0.5
        assert entries[0]["max"] == 1.0


class TestSupportsExplicitTradeStyle:
    def test_pair_unlevered(self):
        assert supports_explicit_trade_style("perp_pair_trade_unlevered") is True

    def test_pair_levered(self):
        assert supports_explicit_trade_style("perp_pair_trade_levered") is True

    def test_non_pair(self):
        assert supports_explicit_trade_style("momentum") is False

    def test_none(self):
        assert supports_explicit_trade_style(None) is False


class TestFeatureRolesForFormula:
    def test_momentum_keyword(self):
        roles = feature_roles_for_formula("momentum_score_24h")
        assert "trend_or_momentum" in roles

    def test_regime_keyword(self):
        roles = feature_roles_for_formula("volatility_regime")
        assert "orthogonal_regime" in roles

    def test_residual_keyword(self):
        roles = feature_roles_for_formula("bollinger_zscore_spread")
        assert "spread_or_residual" in roles

    def test_pair_state(self):
        roles = feature_roles_for_formula("asset_1_funding")
        assert "pair_state" in roles

    def test_empty_string(self):
        roles = feature_roles_for_formula("")
        assert roles == set()


class TestSpecFeatureRoles:
    def test_multiple_features(self):
        roles = spec_feature_roles(["momentum_score", "bollinger_zscore", "volatility_regime"])
        assert "trend_or_momentum" in roles
        assert "spread_or_residual" in roles
        assert "orthogonal_regime" in roles
