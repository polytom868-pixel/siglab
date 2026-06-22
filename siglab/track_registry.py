from __future__ import annotations

from typing import Final

CANONICAL_TRACKS: Final[tuple[str, ...]] = ('trend_signals', 'yield_flows')
TRACK_ALIASES: Final[dict[str, str]] = {'trend_signals': 'trend_signals', 'yield_flows': 'yield_flows'}
TRACK_STORAGE_NAMES: Final[dict[str, str]] = {'trend_signals': 'trend_signals', 'yield_flows': 'yield_flows'}
TRACK_LABELS: Final[dict[str, str]] = {'trend_signals': 'Directional Perps', 'yield_flows': 'Systematic Carry'}
TRACK_CLI_CHOICES: Final[tuple[str, ...]] = ('trend_signals', 'yield_flows')

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
        return 'Unknown Track'
    return TRACK_LABELS.get(canonical, canonical.replace('_', ' ').title())
