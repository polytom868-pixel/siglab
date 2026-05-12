from __future__ import annotations

from pathlib import Path
from typing import Any

from siglab.workspace.cards import extract_markdown_section


def open_workspace_file(
    *,
    workspace_root: Path,
    path: str,
    section: str | None = None,
    max_chars: int | None = None,
) -> dict[str, Any]:
    resolved = (workspace_root / path).resolve()
    root = workspace_root.resolve()
    if root not in resolved.parents and resolved != root:
        return {"ok": False, "error": "path_outside_workspace"}
    if not resolved.exists() or not resolved.is_file():
        return {"ok": False, "error": "file_not_found"}
    text = resolved.read_text()
    if section:
        text = extract_markdown_section(text, section, max_chars=max_chars)
    elif max_chars is not None:
        text = text[:max_chars]
    return {
        "ok": True,
        "path": str(resolved.relative_to(root)),
        "content": text,
    }

