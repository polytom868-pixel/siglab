"""Tests for siglab.orchestration.optimizer_runner public helpers."""

from __future__ import annotations


from siglab.orchestration.trials import clone_payload, get_path_value, apply_path_value
from siglab.orchestration.optimizer_runner import infer_optuna_space


class TestClonePayload:
    def test_deep_copy(self):
        original = {"a": {"b": [1, 2, 3]}, "c": 42}
        cloned = clone_payload(original)
        assert cloned == original
        assert cloned is not original
        assert cloned["a"] is not original["a"]
        assert cloned["a"]["b"] is not original["a"]["b"]

    def test_mutation_independence(self):
        original = {"x": [1]}
        cloned = clone_payload(original)
        cloned["x"].append(2)
        assert original["x"] == [1]

    def test_empty(self):
        assert clone_payload({}) == {}


class TestGetPathValue:
    def test_simple_key(self):
        assert get_path_value({"a": 1}, "a") == 1

    def test_nested_key(self):
        assert get_path_value({"a": {"b": 2}}, "a.b") == 2

    def test_array_index(self):
        assert get_path_value({"a": [10, 20, 30]}, "a[1]") == 20

    def test_missing_key(self):
        assert get_path_value({"a": 1}, "b") is None

    def test_missing_nested(self):
        assert get_path_value({"a": 1}, "a.b.c") is None

    def test_deep_nested(self):
        d = {"risk": {"max_asset_weight": 0.35}}
        assert get_path_value(d, "risk.max_asset_weight") == 0.35


class TestApplyPathValue:
    def test_simple_set(self):
        d = {"a": 1}
        apply_path_value(d, "a", 2)
        assert d["a"] == 2

    def test_nested_set(self):
        d = {"a": {"b": 1}}
        apply_path_value(d, "a.b", 99)
        assert d["a"]["b"] == 99

    def test_creates_intermediate(self):
        d: dict = {}
        apply_path_value(d, "a.b.c", 42)
        assert d["a"]["b"]["c"] == 42

    def test_array_set(self):
        d = {"a": [10, 20]}
        apply_path_value(d, "a[1]", 99)
        assert d["a"][1] == 99


class TestInferOptunaSpace:
    def test_basic_spec(self):
        spec = {
            "family": "momentum_cross_sectional",
            "risk": {"max_asset_weight": 0.35, "rebalance_threshold": 0.03},
            "params": {"gross_target": 1.0, "min_abs_score": 0.1},
        }
        space = infer_optuna_space(spec)
        assert space["family"] == "momentum_cross_sectional"
        assert isinstance(space["parameters"], list)
        assert len(space["parameters"]) > 0

    def test_param_structure(self):
        spec = {
            "family": "momentum_cross_sectional",
            "risk": {"max_asset_weight": 0.35, "rebalance_threshold": 0.03},
            "params": {"gross_target": 1.0, "min_abs_score": 0.1},
        }
        space = infer_optuna_space(spec)
        for p in space["parameters"]:
            assert "path" in p
            assert "kind" in p
            assert "low" in p
            assert "high" in p

    def test_empty_spec(self):
        space = infer_optuna_space({})
        assert space["family"] == ""
        assert space["parameters"] == []

    def test_pair_trade_families_add_extra_params(self):
        spec = {
            "family": "perp_pair_trade_levered",
            "risk": {"max_asset_weight": 0.35},
            "params": {"gross_target": 1.0, "max_gross_target": 1.5, "signal_leverage_scale": 1.0,
                        "entry_abs_score": 0.3, "exit_abs_score": 0.1, "flip_abs_score": 0.2,
                        "max_holding_bars": 48, "cooldown_bars": 12, "min_abs_score": 0.05},
        }
        space = infer_optuna_space(spec)
        paths = [p["path"] for p in space["parameters"]]
        assert "params.max_gross_target" in paths
        assert "params.signal_leverage_scale" in paths
        assert "params.entry_abs_score" in paths

    def test_entry_gate_thresholds(self):
        spec = {
            "family": "momentum_cross_sectional",
            "risk": {},
            "params": {},
            "regime_gates": {
                "entry": [
                    {"expression": "vol > 0.5", "min": 0.5, "max": 2.0},
                ]
            },
        }
        space = infer_optuna_space(spec)
        paths = [p["path"] for p in space["parameters"]]
        assert "regime_gates.entry[0].min" in paths
        assert "regime_gates.entry[0].max" in paths
