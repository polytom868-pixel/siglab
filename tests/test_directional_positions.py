from __future__ import annotations

import unittest

import pandas as pd

from wayfinder_autolab.evaluator.compile import _build_ranked_positions


class DirectionalPositionTests(unittest.TestCase):
    def test_ranker_can_hold_both_assets_long(self) -> None:
        score = pd.DataFrame(
            [{"BTC": 0.55, "ETH": 0.30}],
            index=pd.to_datetime(["2026-03-13T00:00:00"]),
        )
        positions = _build_ranked_positions(
            score,
            long_count=2,
            short_count=2,
            gross_target=1.0,
            max_asset_weight=1.0,
            require_positive_longs=True,
            min_abs_score=0.2,
        )
        self.assertGreater(positions.iloc[0]["BTC"], 0.0)
        self.assertGreater(positions.iloc[0]["ETH"], 0.0)
        self.assertEqual(float(positions.iloc[0].clip(upper=0.0).abs().sum()), 0.0)

    def test_ranker_can_go_flat(self) -> None:
        score = pd.DataFrame(
            [{"BTC": 0.05, "ETH": -0.04}],
            index=pd.to_datetime(["2026-03-13T00:00:00"]),
        )
        positions = _build_ranked_positions(
            score,
            long_count=2,
            short_count=2,
            gross_target=1.0,
            max_asset_weight=1.0,
            require_positive_longs=True,
            min_abs_score=0.2,
        )
        self.assertEqual(float(positions.iloc[0].abs().sum()), 0.0)


if __name__ == "__main__":
    unittest.main()
