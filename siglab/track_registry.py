from __future__ import annotations

from pathlib import Path
from typing import Any, Final, cast
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
