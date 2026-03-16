from __future__ import annotations

from typing import Final

CANONICAL_TRACKS: Final[tuple[str, ...]] = (
    "directional_perps",
    "systematic_carry",
)
TRACK_ALIASES: Final[dict[str, str]] = {
    "directional_perps": "directional_perps",
    "systematic_carry": "systematic_carry",
    "market_neutral_carry": "systematic_carry",
}
TRACK_STORAGE_NAMES: Final[dict[str, str]] = {
    "directional_perps": "directional_perps",
    "systematic_carry": "market_neutral_carry",
}
TRACK_LABELS: Final[dict[str, str]] = {
    "directional_perps": "Directional Perps",
    "systematic_carry": "Systematic Carry",
}
TRACK_CLI_CHOICES: Final[tuple[str, ...]] = (
    "directional_perps",
    "systematic_carry",
    "market_neutral_carry",
)


def canonical_track_name(track: str | None) -> str | None:
    if track is None:
        return None
    return TRACK_ALIASES.get(track, track)


def storage_track_name(track: str | None) -> str | None:
    canonical = canonical_track_name(track)
    if canonical is None:
        return None
    return TRACK_STORAGE_NAMES.get(canonical, canonical)


def matching_track_names(track: str | None) -> tuple[str, ...]:
    canonical = canonical_track_name(track)
    if canonical is None:
        return ()
    storage = storage_track_name(canonical)
    names = [canonical]
    if storage and storage not in names:
        names.append(storage)
    return tuple(names)


def track_label(track: str | None) -> str:
    canonical = canonical_track_name(track)
    if canonical is None:
        return "Unknown Track"
    return TRACK_LABELS.get(canonical, canonical.replace("_", " ").title())
