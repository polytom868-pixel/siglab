from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


def read_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {}
    return dict(payload) if isinstance(payload, dict) else {}


def load_json_path(value: str | Path | None, *, root_dir: Path | None = None) -> dict[str, Any] | None:
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute() and root_dir is not None:
        path = (root_dir / path).resolve()
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def write_json(path: Path, payload: object, *, indent: int = 2, ensure_ascii: bool = True) -> None:
    path.write_text(
        json.dumps(payload, indent=indent, ensure_ascii=ensure_ascii, default=str)
    )


def write_text_if_changed(path: Path, content: str) -> None:
    if path.exists() and path.read_text() == content:
        return
    path.write_text(content)


def json_clone(value: object) -> object:
    return json.loads(json.dumps(value, ensure_ascii=True, default=str))


def json_safe(value: object) -> object:
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value
