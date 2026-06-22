"""Visualization helpers for SigLab evidence graphs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_evidence_graph_html(summary_path: Path, output_path: Path) -> Path:
    """Render an evidence summary JSON file as a standalone HTML graph."""
    try:
        with open(summary_path, "r") as f:
            records: list[dict[str, Any]] = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        records = []
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for rec in records:
        symbol = rec.get("symbol", "unknown")
        direction = rec.get("signal", "NEUTRAL")
        source = rec.get("source", "unknown")
        confidence = rec.get("confidence", 0.0)
        for label in (source, symbol):
            if label and label not in seen_ids:
                seen_ids.add(label)
                kind = "source" if label == source else "symbol"
                nodes.append({"id": label, "label": label, "kind": kind})
        if source and symbol:
            edges.append(
                {
                    "source": source,
                    "target": symbol,
                    "direction": direction,
                    "confidence": confidence,
                }
            )
    html_parts: list[str] = [
        "<!DOCTYPE html>",
        "<html><head><meta charset='utf-8'><title>SigLab Evidence Graph</title>",
        "<style>body{font-family:sans-serif;margin:2em}.node{display:inline-block;padding:4px 10px;margin:4px;border-radius:4px}.source{background:#ddf}.symbol{background:#dfd}</style></head><body>",
        f"<h1>Evidence Graph</h1><p>{len(nodes)} nodes, {len(edges)} edges</p>",
        "<h2>Nodes</h2><div>",
    ]
    for n in nodes:
        html_parts.append(f'<span class="node {n["kind"]}">{n["label"]}</span>')
    html_parts.append("</div><h2>Edges</h2><ul>")
    for e in edges:
        html_parts.append(
            f"<li>{e['source']} → {e['target']} ({e['direction']}, confidence={e['confidence']})</li>"
        )
    html_parts.append("</ul></body></html>")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(html_parts), encoding="utf-8")
    return output_path
