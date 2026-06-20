from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any


def build_evidence_graph_html(summary: dict[str, Any], *, title: str = "SigLab Evidence Graph") -> str:
    source_counts = dict(summary.get("source_counts") or {})
    entity_counts = dict(summary.get("entity_counts") or {})
    links = list(summary.get("top_links") or summary.get("links") or [])
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    for source, count in source_counts.items():
        nodes[f"source:{source}"] = {"id": f"source:{source}", "label": str(source), "kind": "source", "count": int(count)}
    for entity, count in entity_counts.items():
        nodes[f"entity:{entity}"] = {"id": f"entity:{entity}", "label": str(entity), "kind": "entity", "count": int(count)}
    for link in links:
        if not isinstance(link, dict):
            continue
        entities = [str(item) for item in link.get("entities") or [] if item]
        relation = str(link.get("relation") or "linked")
        source = str(link.get("source") or "cross-module")
        for entity in entities:
            node_id = f"entity:{entity}"
            nodes.setdefault(node_id, {"id": node_id, "label": entity, "kind": "entity", "count": 0})
            edges.append({"from": f"source:{source}", "to": node_id, "label": relation})
            nodes.setdefault(f"source:{source}", {"id": f"source:{source}", "label": source, "kind": "source", "count": 0})
    payload = {"nodes": list(nodes.values()), "edges": edges}
    graph_json = json.dumps(payload, ensure_ascii=True)
    escaped_title = html.escape(title)
    from siglab.cli.helpers import _render_html_template
    return _render_html_template(
        "evidence_graph",
        title=escaped_title,
        graph_json=graph_json,
    )


def write_evidence_graph_html(summary_path: Path, output_path: Path) -> Path:
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(build_evidence_graph_html(summary), encoding="utf-8")
    return output_path
