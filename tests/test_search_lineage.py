from __future__ import annotations

import unittest

from siglab.schemas import SignalSpec
from siglab.search.select import plurality_select


def _spec(
    *,
    family: str,
    features: list[str],
    symbols: list[str],
    hypothesis: str,
    params: dict | None = None,
    regime_gates: dict | None = None,
) -> SignalSpec:
    return SignalSpec.from_dict(
        {
            "track": "trend_signals",
            "family": family,
            "hypothesis": hypothesis,
            "neutrality_basis": "none",
            "features": list(features),
            "universe": {
                "basis_groups": list(symbols),
                "max_symbols": len(symbols),
                "lookback_days": 365,
                "interval": "1h",
            },
            "risk": {"max_leverage": 1.5 if len(symbols) > 2 else 1.0},
            "regime_gates": dict(regime_gates or {}),
            "params": dict(params or {}),
        }
    )


class PluralityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.spec_a = _spec(
            family="perp_multi_asset_carry",
            features=["funding_72h_mean"],
            symbols=["BTC", "ETH"],
            hypothesis="carry anchor",
            params={"long_count": 2, "short_count": 2, "gross_target": 1.0},
        )
        cls.spec_b = _spec(
            family="perp_multi_asset_carry",
            features=["funding_carry_to_vol"],
            symbols=["BTC", "ETH"],
            hypothesis="carry momentum",
            params={"long_count": 3, "short_count": 1, "gross_target": 1.4},
        )
        cls.spec_c = _spec(
            family="perp_multi_asset_carry",
            features=["funding_skew"],
            symbols=["BTC", "ETH"],
            hypothesis="carry skew",
            params={"long_count": 4, "short_count": 0, "gross_target": 1.8},
        )
        cls.spec_a._aggregate_score = 0.91  # type: ignore[attr-defined]
        cls.spec_b._aggregate_score = 0.84  # type: ignore[attr-defined]
        cls.spec_c._aggregate_score = 0.77  # type: ignore[attr-defined]

    def test_plurality_select_returns_composite(self) -> None:
        composite = plurality_select(
            [self.spec_a, self.spec_b, self.spec_c], k=3
        )

        self.assertIsInstance(composite, SignalSpec)
        # Metadata inherited from the highest-scored spec.
        self.assertEqual(composite.family, "perp_multi_asset_carry")
        self.assertEqual(composite.hypothesis, "carry anchor")
        self.assertEqual(composite.track, "trend_signals")
        # Features concatenated (deduplicated, order-preserving).
        self.assertEqual(
            composite.features,
            ["funding_72h_mean", "funding_carry_to_vol", "funding_skew"],
        )
        # Numeric params averaged across the three specs.
        self.assertAlmostEqual(composite.params["long_count"], (2 + 3 + 4) / 3)
        self.assertAlmostEqual(composite.params["short_count"], (2 + 1 + 0) / 3)
        self.assertAlmostEqual(composite.params["gross_target"], (1.0 + 1.4 + 1.8) / 3)


if __name__ == "__main__":
    unittest.main()
