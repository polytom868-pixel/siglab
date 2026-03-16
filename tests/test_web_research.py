from __future__ import annotations

import unittest
from pathlib import Path

from wayfinder_autolab.models import CandidateGraph
from wayfinder_autolab.research.web import WebResearcher, _compact_text, _html_to_text
from wayfinder_autolab.settings import AutolabSettings


class WebResearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = AutolabSettings(
            root_dir=Path("/tmp"),
            wayfinder_config_path=Path("/tmp/config.json"),
            generated_strategy_dir=Path("/tmp/generated_strategies"),
            data_lake_dir=Path("/tmp"),
            artifact_dir=Path("/tmp"),
            live_dir=Path("/tmp/live"),
            lineage_db_path=Path("/tmp/autolab_test.db"),
            wayfinder_api_key_override=None,
            kimi_api_key=None,
            kimi_model="kimi-k2.5",
            kimi_base_url="https://api.moonshot.ai/v1",
            kimi_max_tokens=1024,
            kimi_temperature=1.0,
            kimi_top_p=0.95,
            kimi_timeout_s=30.0,
            kimi_thinking=None,
            kimi_max_tool_rounds=3,
            population_size=1,
            tavily_api_key="tvly-test",
        )

    def test_html_cleanup_and_compaction(self) -> None:
        html = "<html><head><title> Test </title></head><body><script>ignore()</script><p>Hello <b>world</b></p></body></html>"
        self.assertEqual(_html_to_text(html), "Test Hello world")
        self.assertEqual(_compact_text("a" * 20, 10), "aaaaaaaaa…")

    def test_query_builder_uses_track_context(self) -> None:
        researcher = WebResearcher(self.settings, lake=type("Lake", (), {"latest_json": lambda *args, **kwargs: None, "write_json": lambda *args, **kwargs: None})())
        candidate = CandidateGraph.from_dict(
            {
                "track": "systematic_carry",
                "family": "lending_carry_rotation",
                "hypothesis": "test",
                "neutrality_basis": "underlying",
                "features": ["combined_supply_apy", "utilization"],
                "universe": {"basis_groups": ["ETH", "BTC"], "max_symbols": 3},
                "risk": {},
                "params": {"hedge_mode": "perp"},
            }
        )
        queries = researcher._build_queries(
            track="systematic_carry",
            parent=candidate,
            research_summary={"lending_markets": [{"basis_symbol": "ETH", "market": "cbETH market"}]},
            recent_results=[{"summary": {"gate_reasons": ["non_positive_median_return"]}}],
        )
        self.assertTrue(any("pendle" in query.lower() or "carry" in query.lower() for query in queries))
        self.assertTrue(any("look ahead bias" in query.lower() for query in queries))


if __name__ == "__main__":
    unittest.main()
