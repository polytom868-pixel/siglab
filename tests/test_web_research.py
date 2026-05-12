from __future__ import annotations

import unittest
from pathlib import Path

from siglab.models import SignalSpec
from siglab.research.web import WebResearcher, _compact_text, _html_to_text
from siglab.settings import SiglabConfig


class WebResearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = SiglabConfig(
            root_dir=Path("/tmp"),
            sosovalue_config_path=Path("/tmp/config.json"),
            generated_strategy_dir=Path("/tmp/deployed_agents"),
            data_lake_dir=Path("/tmp"),
            artifact_dir=Path("/tmp"),
            live_dir=Path("/tmp/live"),
            ancestry_db_path=Path("/tmp/siglab_test.db"),
            sosovalue_api_key_override=None,
            claude_api_key=None,
            claude_model="claude-k2.5",
            claude_base_url="https://api.moonshot.ai/v1",
            claude_max_tokens=1024,
            claude_temperature=1.0,
            claude_top_p=0.95,
            claude_timeout_s=30.0,
            claude_thinking=None,
            claude_max_tool_rounds=3,
            population_size=1,
            tavily_api_key="tvly-test",
        )

    def test_html_cleanup_and_compaction(self) -> None:
        html = "<html><head><title> Test </title></head><body><script>ignore()</script><p>Hello <b>world</b></p></body></html>"
        self.assertEqual(_html_to_text(html), "Test Hello world")
        self.assertEqual(_compact_text("a" * 20, 10), "aaaaaaaaa…")

    def test_query_builder_uses_track_context(self) -> None:
        researcher = WebResearcher(self.settings, lake=type("Lake", (), {"latest_json": lambda *args, **kwargs: None, "write_json": lambda *args, **kwargs: None})())
        spec = SignalSpec.from_dict(
            {
                "track": "yield_flows",
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
            track="yield_flows",
            parent=spec,
            research_summary={"lending_markets": [{"basis_symbol": "ETH", "market": "cbETH market"}]},
            recent_results=[{"summary": {"gate_reasons": ["non_positive_median_return"]}}],
        )
        self.assertTrue(any("pendle" in query.lower() or "carry" in query.lower() for query in queries))
        self.assertTrue(any("look ahead bias" in query.lower() for query in queries))

    def test_no_tools_without_tavily_key(self) -> None:
        settings = SiglabConfig(
            root_dir=Path("/tmp"),
            sosovalue_config_path=Path("/tmp/config.json"),
            generated_strategy_dir=Path("/tmp/deployed_agents"),
            data_lake_dir=Path("/tmp"),
            artifact_dir=Path("/tmp"),
            live_dir=Path("/tmp/live"),
            ancestry_db_path=Path("/tmp/siglab_test.db"),
            sosovalue_api_key_override=None,
            claude_api_key=None,
            claude_model="claude-k2.5",
            claude_base_url="https://api.moonshot.ai/v1",
            claude_max_tokens=1024,
            claude_temperature=1.0,
            claude_top_p=0.95,
            claude_timeout_s=30.0,
            claude_thinking=None,
            claude_max_tool_rounds=3,
            population_size=1,
            tavily_api_key=None,
        )
        researcher = WebResearcher(
            settings,
            lake=type("Lake", (), {"latest_json": lambda *args, **kwargs: None, "write_json": lambda *args, **kwargs: None})(),
        )
        self.assertFalse(researcher.is_configured)
        self.assertEqual(researcher.claude_tools(), [])


if __name__ == "__main__":
    unittest.main()


