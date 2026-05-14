from __future__ import annotations

from pathlib import Path
from typing import Any

from siglab.workspace.indexes import load_jsonl, search_rows

TEXT_SEARCH_SUFFIXES = {".json", ".jsonl", ".md", ".txt", ".yaml", ".yml"}


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


def search_workspace_text(
    *,
    workspace_root: Path,
    query: str,
    path_glob: str | None = None,
    limit: int = 8,
) -> dict[str, Any]:
    query_text = str(query or "").strip()
    if not query_text:
        return {"ok": False, "error": "empty_query"}
    root = workspace_root.resolve()
    lowered_query = query_text.lower()
    matches: list[dict[str, Any]] = []
    for spec in sorted(root.rglob("*")):
        if not spec.is_file() or spec.suffix.lower() not in TEXT_SEARCH_SUFFIXES:
            continue
        relative_path = spec.relative_to(root)
        if path_glob and not relative_path.match(path_glob):
            continue
        try:
            content = spec.read_text(errors="ignore")
        except OSError:
            continue
        lowered_content = content.lower()
        position = 0
        hit_count = 0
        snippets: list[dict[str, Any]] = []
        while True:
            hit = lowered_content.find(lowered_query, position)
            if hit < 0:
                break
            hit_count += 1
            line_number = content.count("\n", 0, hit) + 1
            line_start = content.rfind("\n", 0, hit) + 1
            line_end = content.find("\n", hit)
            if line_end < 0:
                line_end = len(content)
            snippet = content[line_start:line_end].strip()
            if len(snippets) < 3:
                snippets.append(
                    {
                        "line": line_number,
                        "snippet": snippet[:240],
                    }
                )
            position = hit + len(lowered_query)
        if hit_count <= 0:
            continue
        matches.append(
            {
                "path": str(relative_path),
                "match_count": hit_count,
                "snippets": snippets,
            }
        )
    matches.sort(key=lambda row: (-int(row.get("match_count") or 0), str(row.get("path") or "")))
    return {
        "ok": True,
        "query": query_text,
        "path_glob": path_glob,
        "matches": matches[: max(1, min(limit, 20))],
    }

