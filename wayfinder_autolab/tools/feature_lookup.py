from __future__ import annotations

from pathlib import Path
from typing import Any

from wayfinder_autolab.workspace.indexes import load_jsonl


def _catalog_rows(workspace_root: Path) -> list[dict[str, Any]]:
    return load_jsonl(workspace_root / "manifests" / "features" / "feature_catalog.jsonl")


def search_features(
    *,
    workspace_root: Path,
    query: str,
    family: str | None = None,
    kind: str | None = None,
    subkind: str | None = None,
    limit: int = 8,
) -> dict[str, Any]:
    tokens = [token for token in query.lower().split() if token]
    scored: list[tuple[float, dict[str, Any]]] = []
    for row in _catalog_rows(workspace_root):
        if family and family not in list(row.get("family") or []):
            continue
        if kind and str(row.get("kind") or "") != kind:
            continue
        if subkind and str(row.get("subkind") or "") != subkind:
            continue
        search_text = " ".join(
            [
                str(row.get("name") or ""),
                str(row.get("kind") or ""),
                str(row.get("subkind") or ""),
                str(row.get("description") or ""),
                " ".join(str(item) for item in row.get("common_uses") or []),
                " ".join(str(item) for item in row.get("similar_features") or []),
            ]
        )
        score = float(sum(search_text.lower().count(token) for token in tokens)) if tokens else 1.0
        if score <= 0:
            continue
        scored.append((score, row))
    scored.sort(key=lambda item: (item[0], str(item[1].get("name") or "")), reverse=True)
    matches = [row for _score, row in scored[: max(1, min(limit, 20))]]
    normalized = []
    for row in matches:
        normalized.append(
            {
                "name": row.get("name"),
                "family": row.get("family"),
                "kind": row.get("kind"),
                "subkind": row.get("subkind"),
                "formula": row.get("formula"),
                "description": row.get("description"),
                "common_uses": row.get("common_uses"),
                "similar_features": row.get("similar_features"),
            }
        )
    return {"ok": True, "query": query, "matches": normalized}


def inspect_feature(
    *,
    workspace_root: Path,
    name: str,
    family: str | None = None,
) -> dict[str, Any]:
    for row in _catalog_rows(workspace_root):
        if str(row.get("name") or "") != name:
            continue
        if family and family not in list(row.get("family") or []):
            continue
        return {"ok": True, "feature": row}
    return {"ok": False, "error": "feature_not_found", "name": name, "family": family}


def suggest_feature_set(
    *,
    workspace_root: Path,
    family: str,
    hypothesis: str,
    avoid: list[str] | None = None,
    limit: int = 4,
) -> dict[str, Any]:
    avoid_set = {str(item) for item in list(avoid or [])}
    tokens = {token for token in hypothesis.lower().replace("_", " ").split() if len(token) > 2}
    candidates: list[tuple[float, dict[str, Any]]] = []
    for row in _catalog_rows(workspace_root):
        if family not in list(row.get("family") or []):
            continue
        name = str(row.get("name") or "")
        if name in avoid_set:
            continue
        text = " ".join(
            [
                name.lower(),
                str(row.get("subkind") or "").lower(),
                str(row.get("description") or "").lower(),
                " ".join(str(item).lower() for item in row.get("common_uses") or []),
            ]
        )
        score = float(sum(text.count(token) for token in tokens))
        if score <= 0:
            score = 0.1
        candidates.append((score, row))
    candidates.sort(key=lambda item: (item[0], str(item[1].get("name") or "")), reverse=True)
    selected: list[dict[str, Any]] = []
    seen_subkind: set[str] = set()
    for _score, row in candidates:
        subkind = str(row.get("subkind") or "")
        if subkind and subkind in seen_subkind and len(selected) < max(1, limit // 2):
            continue
        selected.append(
            {
                "name": row.get("name"),
                "kind": row.get("kind"),
                "subkind": row.get("subkind"),
                "formula": row.get("formula"),
                "description": row.get("description"),
            }
        )
        if subkind:
            seen_subkind.add(subkind)
        if len(selected) >= max(1, min(limit, 8)):
            break
    return {
        "ok": True,
        "family": family,
        "hypothesis": hypothesis,
        "suggestions": selected,
    }
