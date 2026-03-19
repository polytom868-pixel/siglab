from __future__ import annotations

import unittest

import pandas as pd

from wayfinder_autolab.data.providers import (
    _align_perp_bundle_frames,
    _dedupe_time_index,
    _frame_column_or_default,
    _sanitize_perp_symbols,
)


class ProviderUtilsTests(unittest.TestCase):
    def test_frame_column_or_default_handles_missing_columns(self) -> None:
        frame = pd.DataFrame({"present": [1.0, None]}, index=[0, 1])
        present = _frame_column_or_default(frame, "present", default=0.0)
        missing = _frame_column_or_default(frame, "missing", default=0.5)
        self.assertEqual(list(present), [1.0, 0.0])
        self.assertEqual(list(missing), [0.5, 0.5])

    def test_sanitize_perp_symbols_drops_usd_and_dedupes(self) -> None:
        self.assertEqual(
            _sanitize_perp_symbols(["ETH", "usd", "BTC", "ETH", "", "  sol  "]),
            ["ETH", "BTC", "SOL"],
        )

    def test_align_perp_bundle_frames_uses_common_price_window(self) -> None:
        prices = pd.DataFrame(
            {
                "HYPE": [10.0, 11.0, 12.0],
                "ASTER": [None, 1.0, 1.1],
            },
            index=pd.to_datetime(
                ["2026-01-01T00:00:00", "2026-01-01T01:00:00", "2026-01-01T02:00:00"]
            ),
        )
        funding = pd.DataFrame(
            {
                "HYPE": [0.01, 0.02, 0.03],
                "ASTER": [0.04, 0.05, 0.06],
            },
            index=prices.index,
        )
        aligned_prices, aligned_funding = _align_perp_bundle_frames(prices, funding)
        self.assertEqual(len(aligned_prices), 2)
        self.assertEqual(aligned_prices.index[0], prices.index[1])
        self.assertEqual(list(aligned_prices.columns), ["HYPE", "ASTER"])
        self.assertEqual(list(aligned_funding.index), list(aligned_prices.index))

    def test_dedupe_time_index_keeps_last_duplicate_timestamp(self) -> None:
        frame = pd.DataFrame(
            {"funding_rate": [0.01, 0.02, 0.03]},
            index=pd.to_datetime(
                [
                    "2026-01-01T00:00:00",
                    "2026-01-01T00:00:00",
                    "2026-01-01T01:00:00",
                ]
            ),
        )
        deduped = _dedupe_time_index(frame)
        self.assertEqual(len(deduped), 2)
        self.assertEqual(float(deduped.iloc[0]["funding_rate"]), 0.02)


if __name__ == "__main__":
    unittest.main()
