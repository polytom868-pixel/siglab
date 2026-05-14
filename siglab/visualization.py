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
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{escaped_title}</title>
  <style>
    :root {{ --ink:#14213d; --paper:#f8f4ea; --accent:#d97706; --muted:#5f6c7b; --line:#d6ccc2; }}
    body {{ margin:0; font-family: Georgia, 'Times New Roman', serif; background: radial-gradient(circle at top left,#fff7d6,var(--paper)); color:var(--ink); }}
    header {{ padding:28px 36px 12px; }}
    h1 {{ margin:0; font-size:34px; letter-spacing:-0.03em; }}
    .sub {{ color:var(--muted); margin-top:8px; }}
    main {{ display:grid; grid-template-columns: 1fr 360px; gap:20px; padding:20px 36px 36px; }}
    #graph {{ min-height:560px; border:1px solid var(--line); border-radius:18px; background:rgba(255,255,255,.55); position:relative; overflow:hidden; }}
    aside {{ border:1px solid var(--line); border-radius:18px; background:rgba(255,255,255,.68); padding:18px; }}
    .node {{ position:absolute; transform:translate(-50%,-50%); border-radius:999px; padding:8px 11px; border:1px solid var(--line); background:white; box-shadow:0 8px 24px rgba(20,33,61,.10); font-size:13px; max-width:180px; text-align:center; }}
    .source {{ background:#fff1c2; }}
    .entity {{ background:#dff3ed; }}
    svg {{ position:absolute; inset:0; width:100%; height:100%; }}
    .row {{ display:flex; justify-content:space-between; gap:12px; padding:8px 0; border-bottom:1px solid var(--line); }}
    code {{ color:var(--accent); }}
    @media (max-width: 900px) {{ main {{ grid-template-columns:1fr; padding:16px; }} header {{ padding:22px 16px 8px; }} }}
  </style>
</head>
<body>
  <header>
    <h1>{escaped_title}</h1>
    <div class="sub">Evidence graph is an inspection aid. Links are evidentiary/temporal, not causal claims.</div>
  </header>
  <main>
    <section id="graph"><svg id="edges"></svg></section>
    <aside>
      <h2>Surface Counts</h2>
      <div id="counts"></div>
      <h2>Blind Spots</h2>
      <p>Missing modules stay visible in <code>docs/sosovalue-api-surface.yaml</code>; do not treat this graph as full-market coverage.</p>
    </aside>
  </main>
  <script>
    const graph = {graph_json};
    const box = document.getElementById('graph');
    const svg = document.getElementById('edges');
    const cx = box.clientWidth / 2, cy = box.clientHeight / 2;
    const radius = Math.max(180, Math.min(cx, cy) - 70);
    const positions = new Map();
    graph.nodes.forEach((n, i) => {{
      const angle = (Math.PI * 2 * i) / Math.max(1, graph.nodes.length);
      const x = cx + Math.cos(angle) * radius;
      const y = cy + Math.sin(angle) * radius;
      positions.set(n.id, [x,y]);
      const el = document.createElement('div');
      el.className = 'node ' + n.kind;
      el.style.left = x + 'px'; el.style.top = y + 'px';
      el.textContent = n.label + (n.count ? ' (' + n.count + ')' : '');
      box.appendChild(el);
    }});
    graph.edges.forEach(e => {{
      const a = positions.get(e.from), b = positions.get(e.to);
      if (!a || !b) return;
      const line = document.createElementNS('http://www.w3.org/2000/svg','line');
      line.setAttribute('x1', a[0]); line.setAttribute('y1', a[1]);
      line.setAttribute('x2', b[0]); line.setAttribute('y2', b[1]);
      line.setAttribute('stroke', '#b08968'); line.setAttribute('stroke-width', '1.5'); line.setAttribute('opacity', '.58');
      svg.appendChild(line);
    }});
    document.getElementById('counts').innerHTML = graph.nodes.slice(0,16).map(n => '<div class="row"><span>'+n.label+'</span><b>'+ (n.count||0) +'</b></div>').join('');
  </script>
</body>
</html>
"""


def write_evidence_graph_html(summary_path: Path, output_path: Path) -> Path:
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(build_evidence_graph_html(summary), encoding="utf-8")
    return output_path
