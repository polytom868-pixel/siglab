"""Tests for siglab.track_registry public API."""

from __future__ import annotations

import pytest

from siglab.track_registry import (
    CANONICAL_TRACKS,
    TRACK_ALIASES,
    TRACK_LABELS,
    canonical_track_name,
    matching_track_names,
    track_label,
)


class TestCanonicalTrackName:
    def test_known_track(self):
        assert canonical_track_name("trend_signals") == "trend_signals"

    def test_alias_resolves(self):
        for alias, expected in TRACK_ALIASES.items():
            assert canonical_track_name(alias) == expected

    def test_unknown_passthrough(self):
        assert canonical_track_name("unknown_track") == "unknown_track"

    def test_none_returns_none(self):
        assert canonical_track_name(None) is None


class TestTrackLabel:
    def test_trend_signals_label(self):
        assert track_label("trend_signals") == "Directional Perps"

    def test_yield_flows_label(self):
        assert track_label("yield_flows") == "Systematic Carry"

    def test_unknown_track_fallback(self):
        label = track_label("some_new_track")
        assert label == "Some New Track"

    def test_none_returns_unknown(self):
        assert track_label(None) == "Unknown Track"


class TestMatchingTrackNames:
    def test_returns_canonical(self):
        names = matching_track_names("trend_signals")
        assert "trend_signals" in names

    def test_partial_match(self):
        names = matching_track_names("yield_flows")
        assert "yield_flows" in names

    def test_none_returns_empty(self):
        assert matching_track_names(None) == ()

    def test_unknown_track_returns_tuple(self):
        names = matching_track_names("custom_track")
        assert isinstance(names, tuple)
        assert "custom_track" in names
