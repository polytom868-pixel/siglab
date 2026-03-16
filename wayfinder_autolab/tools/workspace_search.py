from __future__ import annotations

from pathlib import Path
from typing import Any

from wayfinder_autolab.workspace.indexes import load_jsonl, search_rows


def search_workspace(
    *,
    workspace_root: Path,
    query: str,
    kind: str | None = None,
    family: str | None = None,
    outcome: str | None = None,
    limit: int = 8,
) -> dict[str, Any]:
    rows = []
    for name in ["experiment_index.jsonl", "reflection_index.jsonl", "probe_index.jsonl", "trial_index.jsonl"]:
        rows.extend(load_jsonl(workspace_root / "indexes" / name))
    matches = search_rows(
        rows=rows,
        query=query,
        kind=kind,
        family=family,
        outcome=outcome,
        limit=limit,
    )
    return {
        "ok": True,
        "query": query,
        "matches": matches,
    }
