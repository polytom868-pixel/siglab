from __future__ import annotations

import unittest
from pathlib import Path

import pandas as pd

from siglab.evaluator.compile import compile_spec
from siglab.models import SignalSpec
from siglab.settings import SiglabConfig

REPO_ROOT = Path(__file__).resolve().parents[1]


class StubPtProvider:
    def __init__(self) -> None:
        self.markets = [
            {
                "marketName": "PT_A",
                "chainId": 1,
                "marketAddress": "0xa",
                "expiry": "2026-01-06T00:00:00",
            },
            {
                "marketName": "PT_B",
                "chainId": 1,
                "marketAddress": "0xb",
                "expiry": "2026-01-14T00:00:00",
            },
        ]

    def market_label(self, row: dict) -> str:
        return str(row["marketName"])

    async def discover_stable_pt_markets(self, universe, *, limit: int) -> list[dict]:
        return self.markets[:limit]

    async def discover_pt_markets(self, universe, *, limit: int) -> list[dict]:
        return self.markets[:limit]

    async def fetch_pt_histories(self, markets: list[dict], *, lookback_days: int) -> dict[str, pd.DataFrame]:
        index_a = pd.date_range("2026-01-01", periods=6, freq="D")
        index_b = pd.date_range("2026-01-02", periods=6, freq="D")
        return {
            "PT_A": pd.DataFrame(
                {
                    "ptPrice": [0.90, 0.91, 0.92, 0.93, 0.94, 0.95],
                    "impliedApy": [0.10] * 6,
                    "underlyingApy": [0.04] * 6,
                    "totalTvl": [1_000_000.0] * 6,
                },
                index=index_a,
            ),
            "PT_B": pd.DataFrame(
                {
                    "ptPrice": [0.97, 0.97, 0.97, 0.97, 0.97, 0.97],
                    "impliedApy": [0.06] * 6,
                    "underlyingApy": [0.04] * 6,
                    "totalTvl": [800_000.0] * 6,
                },
                index=index_b,
            ),
        }


class PtRollForwardTests(unittest.IsolatedAsyncioTestCase):
    async def test_stable_pt_ladder_rolls_forward_into_next_eligible_market(self) -> None:
        settings = SiglabConfig(
            root_dir=REPO_ROOT,
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
        )
        spec = SignalSpec.from_dict(
            {
                "track": "yield_flows",
                "family": "stable_pt_ladder",
                "hypothesis": "test roll forward",
                "neutrality_basis": "usd",
                "features": ["pt_discount_to_par"],
                "universe": {
                    "basis_groups": ["USD"],
                    "chains": ["base"],
                    "max_symbols": 2,
                    "lookback_days": 30,
                    "interval": "1d",
                    "min_days_to_expiry": 1,
                    "max_days_to_expiry": 30,
                },
                "risk": {
                    "max_asset_weight": 1.0,
                    "roll_days_before_expiry": 1,
                    "rebalance_threshold": 0.0,
                    "max_leverage": 1.0,
                },
                "params": {
                    "selection_count": 1,
                    "gross_target": 1.0,
                },
            }
        )

        compiled = await compile_spec(settings, StubPtProvider(), spec)
        positions = compiled.target_positions.fillna(0.0)

        self.assertGreater(positions.loc[pd.Timestamp("2026-01-04"), "PT_A"], 0.0)
        self.assertEqual(positions.loc[pd.Timestamp("2026-01-05"), "PT_A"], 0.0)
        self.assertGreater(positions.loc[pd.Timestamp("2026-01-05"), "PT_B"], 0.0)

        metadata = compiled.metadata
        self.assertEqual(metadata["lifecycle_policy"]["open_ended_policy"], "continuous_rotation")
        self.assertEqual(metadata["roll_event_count"], 1)
        self.assertIn("PT_B", metadata["markets_entered_during_backtest"])
        first_event = metadata["roll_events"][0]
        self.assertEqual(first_event["reason"], "inside_roll_window")
        self.assertEqual(first_event["from_markets"], ["PT_A"])
        self.assertEqual(first_event["to_markets"], ["PT_B"])


if __name__ == "__main__":
    unittest.main()


