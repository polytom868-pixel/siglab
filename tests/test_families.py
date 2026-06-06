"""Tests for siglab.families — family spec loading and capability accessors."""
from __future__ import annotations

from pathlib import Path

import pytest

from siglab.families import (
    family_capabilities,
    family_diagnostic_adapter,
    family_execution_profile,
    family_policy_schema,
    family_prompt_module,
    load_family_spec,
    load_track_family_specs,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


class TestLoadTrackFamilySpecs:
    def test_loads_trend_signals(self):
        specs = load_track_family_specs(REPO_ROOT, "trend_signals")
        assert isinstance(specs, dict)
        assert len(specs) > 0
        assert "perp_multi_asset_decision" in specs

    def test_unknown_track_returns_empty(self):
        specs = load_track_family_specs(REPO_ROOT, "nonexistent_track")
        assert specs == {}


class TestLoadFamilySpec:
    def test_loads_known_family(self):
        spec = load_family_spec(REPO_ROOT, "trend_signals", "perp_multi_asset_decision")
        assert isinstance(spec, dict)
        assert "capabilities" in spec

    def test_missing_family_returns_empty(self):
        spec = load_family_spec(REPO_ROOT, "trend_signals", "nonexistent_family")
        assert spec == {}


class TestFamilyCapabilities:
    def test_with_valid_spec(self):
        spec = load_family_spec(REPO_ROOT, "trend_signals", "perp_multi_asset_decision")
        caps = family_capabilities(spec)
        assert isinstance(caps, dict)
        assert "signal_shape" in caps

    def test_with_none(self):
        assert family_capabilities(None) == {}

    def test_with_empty_dict(self):
        assert family_capabilities({}) == {}


class TestFamilyExecutionProfile:
    def test_returns_string(self):
        spec = load_family_spec(REPO_ROOT, "trend_signals", "perp_multi_asset_decision")
        profile = family_execution_profile(spec)
        assert isinstance(profile, str)
        assert profile == "ranked_directional"

    def test_none_spec(self):
        assert family_execution_profile(None) is None


class TestFamilyDiagnosticAdapter:
    def test_returns_string(self):
        spec = load_family_spec(REPO_ROOT, "trend_signals", "perp_multi_asset_decision")
        adapter = family_diagnostic_adapter(spec)
        assert adapter == "perp_cross_sectional"

    def test_none_spec(self):
        assert family_diagnostic_adapter(None) is None


class TestFamilyPolicySchema:
    def test_returns_string(self):
        spec = load_family_spec(REPO_ROOT, "trend_signals", "perp_multi_asset_decision")
        schema = family_policy_schema(spec)
        assert schema == "ranked_cross_sectional"

    def test_none_spec(self):
        assert family_policy_schema(None) is None


class TestFamilyPromptModule:
    def test_returns_string(self):
        spec = load_family_spec(REPO_ROOT, "trend_signals", "perp_multi_asset_decision")
        module = family_prompt_module(spec)
        assert module == "directional"

    def test_none_spec(self):
        assert family_prompt_module(None) is None
