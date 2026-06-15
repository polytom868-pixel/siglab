from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml

from siglab.track_registry import storage_track_name


def load_track_family_specs(root_dir: Path, track: str) -> dict[str, Any]:
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


def family_prompt_module(spec: dict[str, Any] | None) -> str | None:
    return _family_capability(spec, "prompt_module")

