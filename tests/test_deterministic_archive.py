from __future__ import annotations

import unittest

import siglab.search.select as select_mod
from siglab.models import SignalSpec
from siglab.search.select import pick_deterministic_parent, rank_deterministic_specs


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


def _row(spec: SignalSpec, *, quality: float, passed: bool = False) -> dict:
    return {
        "spec_hash": spec.strategy_hash(),
        "family": spec.family,
        "spec": spec.canonical_dict(),
        "aggregate_score": quality,
        "passed": passed,
        "deployd": passed,
        "summary": {
            "median_total_return": quality * 0.01,
            "validation_total_return": quality * 0.008,
            "pre_audit_canonical_total_return": quality * 0.02,
            "pre_audit_canonical_max_drawdown": -0.08,
            "active_bar_fraction": 0.45,
        },
        "research_summary": {
            "run_context": {
                "deterministic": True,
                "phase_label": "burn_in",
            }
        },
    }


class _FakeLineage:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = list(rows)

    def recent(self, _track: str, *, limit: int = 500, run_session_id: str | None = None):
        return list(self._rows)[:limit]


class DeterministicArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        select_mod._RNG.seed(7)

    def test_pick_deterministic_parent_prefers_strong_anchor_with_randomness(self) -> None:
        carry = _spec(
            family="perp_multi_asset_carry",
            features=["funding_72h_mean", "funding_carry_to_vol"],
            symbols=["BTC", "ETH", "SOL", "HYPE"],
            hypothesis="carry anchor",
            params={"long_count": 2, "short_count": 2, "gross_target": 1.0},
        )
        basket = _spec(
            family="perp_basket_neutral_levered",
            features=["price_return_24h", "trend_strength_72h"],
            symbols=["BTC", "ETH", "SOL", "HYPE"],
            hypothesis="basket alt",
            params={"long_count": 2, "short_count": 2, "gross_target": 1.2},
        )
        pair = _spec(
            family="perp_pair_trade_unlevered",
            features=["pair_corr_72h", "pair_carry_to_vol_spread"],
            symbols=["ETH", "BTC"],
            hypothesis="pair alt",
            params={"gross_target": 1.0, "trade_style": "reversion"},
        )
        rows = [
            _row(carry, quality=6.0, passed=True),
            _row(basket, quality=-2.0),
            _row(
                _spec(
                    family="perp_basket_neutral_levered",
                    features=["price_return_72h", "market_volatility_168h"],
                    symbols=["BTC", "ETH", "SOL", "HYPE"],
                    hypothesis="basket dud 2",
                    params={"long_count": 2, "short_count": 2, "gross_target": 1.2},
                ),
                quality=-3.0,
            ),
        ]

        picked = pick_deterministic_parent(
            "trend_signals",
            _FakeLineage(rows),
            [carry, basket, pair],
            iteration_number=3,
        )

        self.assertEqual(picked.family, "perp_multi_asset_carry")

    def test_rank_deterministic_specs_keeps_anchor_and_adds_diversity(self) -> None:
        carry = _spec(
            family="perp_multi_asset_carry",
            features=["funding_72h_mean", "funding_carry_to_vol"],
            symbols=["BTC", "ETH", "SOL", "HYPE"],
            hypothesis="carry anchor",
            params={"long_count": 2, "short_count": 2, "gross_target": 1.0},
        )
        carry_alt = _spec(
            family="perp_multi_asset_carry",
            features=["funding_72h_mean", "co_movement_72h"],
            symbols=["BTC", "ETH", "SOL", "HYPE"],
            hypothesis="carry regime alt",
            params={"long_count": 2, "short_count": 2, "gross_target": 1.0},
            regime_gates={"entry": [{"expression": "co_movement_72h", "min": 0.2}]},
        )
        basket = _spec(
            family="perp_basket_neutral_levered",
            features=["price_return_24h", "relative_carry_z_72h"],
            symbols=["BTC", "ETH", "BNB", "XRP"],
            hypothesis="basket novel",
            params={"long_count": 2, "short_count": 2, "gross_target": 1.2},
        )
        pair = _spec(
            family="perp_pair_trade_unlevered",
            features=["pair_kalman_residual_z_72h", "pair_beta_drift_z_168h"],
            symbols=["ETH", "BTC"],
            hypothesis="pair novel",
            params={"gross_target": 1.0, "trade_style": "reversion"},
        )

        recent_rows = [
            _row(carry, quality=6.0, passed=True),
            _row(
                _spec(
                    family="perp_multi_asset_carry",
                    features=["funding_72h_mean", "trend_strength_72h"],
                    symbols=["BTC", "ETH", "SOL", "HYPE"],
                    hypothesis="carry overused 1",
                    params={"long_count": 2, "short_count": 2, "gross_target": 1.0},
                ),
                quality=1.0,
            ),
            _row(
                _spec(
                    family="perp_multi_asset_carry",
                    features=["funding_168h_mean", "market_volatility_168h"],
                    symbols=["BTC", "ETH", "SOL", "HYPE"],
                    hypothesis="carry overused 2",
                    params={"long_count": 2, "short_count": 2, "gross_target": 1.0},
                ),
                quality=0.5,
            ),
        ]

        ranked = rank_deterministic_specs(
            specs=[carry, carry_alt, basket, pair],
            parent=carry,
            recent_rows=recent_rows,
            seed_specs=[carry, basket, pair],
            population_size=2,
        )

        self.assertEqual(len(ranked), 2)
        families = {spec.family for spec in ranked}
        self.assertIn("perp_multi_asset_carry", families)
        self.assertGreaterEqual(len(families), 2)


if __name__ == "__main__":
    unittest.main()

