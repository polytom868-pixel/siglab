from __future__ import annotations

from pathlib import Path
from typing import cast


def resolve_path_from_root(value: str | Path, *, root_dir: Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (root_dir / path).resolve()


def display_path(value: str | Path | None, *, root_dir: Path | None) -> str | None:
    if value in {None, ""}:
        return None
    path = Path(cast("str | Path", value))
    if not path.is_absolute():
        return path.as_posix()
    resolved = path.resolve()
    if root_dir is not None:
        try:
            return resolved.relative_to(root_dir.resolve()).as_posix()
        except ValueError:
            return f"{resolved.name} (external)"
    return resolved.as_posix()
