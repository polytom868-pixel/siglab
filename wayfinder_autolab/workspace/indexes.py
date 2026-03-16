from __future__ import annotations

import json
from pathlib import Path
from typing import Any


INDEX_COMPACT_INTERVAL = 25
INDEX_COMPACT_ROW_LIMIT = 1000


def ensure_index(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("")


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    ensure_index(path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=True, default=str) + "\n")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


def compact_jsonl(path: Path, *, key_fields: list[str]) -> None:
    rows = load_jsonl(path)
    if len(rows) <= INDEX_COMPACT_ROW_LIMIT:
        return
    deduped: dict[tuple[str, ...], dict[str, Any]] = {}
    for row in rows:
        key = tuple(str(row.get(field) or "") for field in key_fields)
        deduped[key] = row
    sorted_rows = sorted(
        deduped.values(),
        key=lambda row: (
            str(row.get("created_at") or ""),
            str(row.get("path") or ""),
        ),
    )
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=True, default=str) for row in sorted_rows) + "\n"
    )


def maybe_compact(path: Path, *, key_fields: list[str], iteration_number: int) -> None:
    if iteration_number <= 0 or iteration_number % INDEX_COMPACT_INTERVAL != 0:
        return
    compact_jsonl(path, key_fields=key_fields)


def search_rows(
    *,
    rows: list[dict[str, Any]],
    query: str,
    kind: str | None,
    family: str | None,
    outcome: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    tokens = [token for token in query.lower().split() if token]
    scored: list[tuple[float, dict[str, Any]]] = []
    for row in rows:
        if kind and str(row.get("kind") or "") != kind:
            continue
        if family and str(row.get("family") or "") != family:
            continue
        if outcome and str(row.get("outcome") or "") != outcome:
            continue
        search_text = str(row.get("search_text") or "").lower()
        if tokens:
            score = float(sum(search_text.count(token) for token in tokens))
            if score <= 0:
                continue
        else:
            score = 1.0
        scored.append((score, row))
    scored.sort(
        key=lambda item: (
            item[0],
            str(item[1].get("created_at") or ""),
        ),
        reverse=True,
    )
    return [row for _score, row in scored[: max(1, min(limit, 20))]]
