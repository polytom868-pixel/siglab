from __future__ import annotations

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd

from wayfinder_autolab.data.providers import MarketDataProvider
from wayfinder_autolab.data.providers import _pair_calibration_snapshot


class MarketDataProviderNoFallbackTests(unittest.IsolatedAsyncioTestCase):
    def _provider(self) -> MarketDataProvider:
        provider = object.__new__(MarketDataProvider)
        provider.delta_lab = SimpleNamespace()
        provider.pendle = SimpleNamespace()
        provider.lake = SimpleNamespace()
        provider._active_bundle_id = None
        provider._active_as_of = None
        provider._bundle_cache = {}
        provider._warm_cache = {}
        provider._bundle_components = []
        provider._bundle_manifest = {}
        provider._persist_bundle_frames = lambda *args, **kwargs: None
        return provider

    async def test_discover_perp_symbols_raises_when_delta_lab_fails(self) -> None:
        provider = self._provider()
        provider.delta_lab.get_basis_symbols = AsyncMock(side_effect=RuntimeError("delta lab down"))

        with self.assertRaisesRegex(RuntimeError, "delta lab down"):
            await provider.discover_perp_symbols(["ETH", "BTC"], limit=2)

    async def test_fetch_perp_bundle_raises_when_delta_lab_fails(self) -> None:
        provider = self._provider()
        provider._fetch_perp_bundle_delta_lab = AsyncMock(side_effect=RuntimeError("delta lab down"))

        with self.assertRaisesRegex(RuntimeError, "delta lab down"):
            await provider.fetch_perp_bundle(
                symbols=["ETH", "BTC"],
                lookback_days=21,
                interval="1h",
            )

        self.assertEqual(provider._bundle_cache, {})

    async def test_discover_perp_symbols_reuses_warm_cache_across_iteration_bundles(self) -> None:
        provider = self._provider()
        provider.delta_lab.get_basis_symbols = AsyncMock(
            return_value={"symbols": [{"symbol": "ETH"}, {"symbol": "BTC"}, {"symbol": "SOL"}]}
        )

        provider._active_bundle_id = "bundle-1"
        first = await provider.discover_perp_symbols(["ETH", "BTC"], limit=2)
        provider._bundle_cache = {}
        provider._active_bundle_id = "bundle-2"
        second = await provider.discover_perp_symbols(["ETH", "BTC"], limit=2)

        self.assertEqual(first, ["ETH", "BTC"])
        self.assertEqual(second, ["ETH", "BTC"])
        self.assertEqual(provider.delta_lab.get_basis_symbols.await_count, 1)

    async def test_fetch_perp_bundle_reuses_warm_cache_across_iteration_bundles(self) -> None:
        provider = self._provider()
        index = pd.date_range("2026-01-01", periods=24, freq="h")
        prices = pd.DataFrame(
            {"ETH": [2000.0 + float(i) for i in range(24)], "BTC": [30000.0 + float(i) * 2.0 for i in range(24)]},
            index=index,
        )
        funding = pd.DataFrame(
            {"ETH": [0.0001] * 24, "BTC": [-0.00002] * 24},
            index=index,
        )
        provider._fetch_perp_bundle_delta_lab = AsyncMock(
            return_value={
                "prices": prices,
                "funding": funding,
                "source": "delta_lab",
                "bundle_as_of": "2026-01-01T00:00:00+00:00",
                "bundle_id": "bundle-1",
            }
        )

        provider._active_bundle_id = "bundle-1"
        provider._active_as_of = datetime(2026, 1, 1, tzinfo=UTC)
        first = await provider.fetch_perp_bundle(symbols=["ETH", "BTC"], lookback_days=21, interval="1h")

        provider._bundle_cache = {}
        provider._active_bundle_id = "bundle-2"
        provider._active_as_of = datetime(2026, 1, 2, tzinfo=UTC)
        second = await provider.fetch_perp_bundle(symbols=["ETH", "BTC"], lookback_days=21, interval="1h")

        self.assertEqual(provider._fetch_perp_bundle_delta_lab.await_count, 1)
        self.assertEqual(first["bundle_id"], "bundle-1")
        self.assertEqual(second["bundle_id"], "bundle-2")
        self.assertEqual(second["bundle_as_of"], "2026-01-02T00:00:00+00:00")
        pd.testing.assert_frame_equal(second["prices"], prices)
        pd.testing.assert_frame_equal(second["funding"], funding)

    def test_pair_calibration_snapshot_reports_percentiles(self) -> None:
        index = pd.date_range("2026-01-01", periods=120, freq="h")
        prices = pd.DataFrame(
            {
                "ETH": [2000.0 + float(i) * 2.0 for i in range(120)],
                "BTC": [30000.0 + float(i) * 10.0 for i in range(120)],
            },
            index=index,
        )
        funding = pd.DataFrame(
            {
                "ETH": [0.0001 if i % 2 == 0 else -0.00005 for i in range(120)],
                "BTC": [-0.00002 if i % 3 == 0 else 0.00004 for i in range(120)],
            },
            index=index,
        )

        snapshot = _pair_calibration_snapshot(
            prices=prices,
            funding=funding,
            symbols=["ETH", "BTC"],
        )

        self.assertEqual(snapshot["pair"], ["ETH", "BTC"])
        self.assertEqual(snapshot["sample_bars"], 120)
        self.assertIn("funding_spread_percentiles", snapshot)
        self.assertIn("pair_volatility_72h_percentiles", snapshot)
        self.assertIn("pair_correlation_72h_percentiles", snapshot)
        self.assertIn("observed_fractions", snapshot)


if __name__ == "__main__":
    unittest.main()
