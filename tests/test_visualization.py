from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from siglab.visualization import build_evidence_graph_html, write_evidence_graph_html


class EvidenceVisualizationTests(unittest.TestCase):
    def test_html_graph_contains_sources_entities_and_no_causality_claim(self) -> None:
        html = build_evidence_graph_html(
            {
                "source_counts": {"sosovalue.etf_historical_inflow": 2},
                "entity_counts": {"BTC": 3},
                "top_links": [
                    {
                        "source": "sosovalue.featured_news_by_currency",
                        "entities": ["BTC"],
                        "relation": "temporal_nearby",
                    }
                ],
            }
        )

        self.assertIn("sosovalue.etf_historical_inflow", html)
        self.assertIn("BTC", html)
        self.assertIn("not causal claims", html)

    def test_write_evidence_graph_html_writes_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            summary = root / "summary.json"
            output = root / "graph.html"
            summary.write_text(
                json.dumps({"source_counts": {"feed": 1}, "entity_counts": {"BTC": 1}}),
                encoding="utf-8",
            )

            rendered = write_evidence_graph_html(summary, output)

            self.assertEqual(rendered, output)
            self.assertIn("<!doctype html>", output.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
