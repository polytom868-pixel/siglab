from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from siglab.evaluator.events import (
    classify_pt_market_state,
    detect_pt_roll_events,
    summarize_pt_universe,
)


class ClassifyPtMarketStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.index = pd.date_range("2026-01-01", periods=10, freq="D")
        self.prices = pd.DataFrame(
            {
                "PT-A": [1.0, 0.99, 0.98, 0.97, 0.96, 0.95, 0.94, 0.93, 0.92, 0.91],
                "PT-B": [1.0, 1.01, 1.02, 1.03, 1.04, 1.05, 1.06, 1.07, 1.08, 1.09],
            },
            index=self.index,
        )
        self.days_to_expiry = pd.DataFrame(
            {
                "PT-A": [90.0, 80.0, 70.0, 60.0, 50.0, 40.0, 30.0, 20.0, 10.0, 0.0],
                "PT-B": [90.0, 80.0, 70.0, 60.0, 50.0, 40.0, 30.0, 20.0, 10.0, 0.0],
            },
            index=self.index,
        )
        self.required = [
            pd.DataFrame(
                {
                    "PT-A": [0.05] * 10,
                    "PT-B": [0.03] * 10,
                },
                index=self.index,
            )
        ]

    def test_returns_expected_keys(self) -> None:
        result = classify_pt_market_state(
            prices=self.prices,
            days_to_expiry=self.days_to_expiry,
            required_frames=self.required,
            roll_days_before_expiry=7,
            min_days_to_expiry=5,
            max_days_to_expiry=100,
        )
        self.assertIn("availability", result)
        self.assertIn("eligible", result)
        self.assertIn("inside_roll_window", result)
        self.assertIn("expired_or_untradable", result)
        self.assertEqual(len(result), 4)

    def test_availability_true_when_all_data_present(self) -> None:
        result = classify_pt_market_state(
            prices=self.prices,
            days_to_expiry=self.days_to_expiry,
            required_frames=self.required,
            roll_days_before_expiry=7,
            min_days_to_expiry=5,
            max_days_to_expiry=100,
        )
        self.assertTrue(result["availability"].all().all())

    def test_availability_false_where_price_is_nan(self) -> None:
        prices = self.prices.copy()
        prices.iloc[3, 0] = np.nan
        result = classify_pt_market_state(
            prices=prices,
            days_to_expiry=self.days_to_expiry,
            required_frames=self.required,
            roll_days_before_expiry=7,
            min_days_to_expiry=5,
            max_days_to_expiry=100,
        )
        self.assertFalse(result["availability"].iloc[3, 0])

    def test_availability_false_where_required_frame_is_nan(self) -> None:
        req = self.required[0].copy()
        req.iloc[2, 1] = np.nan
        result = classify_pt_market_state(
            prices=self.prices,
            days_to_expiry=self.days_to_expiry,
            required_frames=[req],
            roll_days_before_expiry=7,
            min_days_to_expiry=5,
            max_days_to_expiry=100,
        )
        self.assertFalse(result["availability"].iloc[2, 1])

    def test_inside_roll_window_requires_zero_to_roll_threshold(self) -> None:
        result = classify_pt_market_state(
            prices=self.prices,
            days_to_expiry=self.days_to_expiry,
            required_frames=self.required,
            roll_days_before_expiry=7,
            min_days_to_expiry=5,
            max_days_to_expiry=100,
        )
        self.assertFalse(result["inside_roll_window"].any().any())

    def test_inside_roll_window_detects_near_expiry(self) -> None:
        days = pd.DataFrame(
            {
                "PT-A": [90.0, 80.0, 70.0, 60.0, 50.0, 40.0, 30.0, 5.0, 3.0, 0.0],
            },
            index=self.index,
        )
        prices = pd.DataFrame({"PT-A": [1.0] * 10}, index=self.index)
        result = classify_pt_market_state(
            prices=prices,
            days_to_expiry=days,
            required_frames=[prices],
            roll_days_before_expiry=7,
            min_days_to_expiry=5,
            max_days_to_expiry=100,
        )
        self.assertTrue(result["inside_roll_window"].iloc[7, 0])
        self.assertTrue(result["inside_roll_window"].iloc[8, 0])
        self.assertFalse(result["inside_roll_window"].iloc[9, 0])

    def test_expired_or_untradable_at_zero_days(self) -> None:
        result = classify_pt_market_state(
            prices=self.prices,
            days_to_expiry=self.days_to_expiry,
            required_frames=self.required,
            roll_days_before_expiry=7,
            min_days_to_expiry=5,
            max_days_to_expiry=100,
        )
        self.assertTrue(result["expired_or_untradable"].iloc[9].all())
        self.assertFalse(result["expired_or_untradable"].iloc[0].any())

    def test_eligible_requires_data_and_maturity_range(self) -> None:
        result = classify_pt_market_state(
            prices=self.prices,
            days_to_expiry=self.days_to_expiry,
            required_frames=self.required,
            roll_days_before_expiry=7,
            min_days_to_expiry=5,
            max_days_to_expiry=100,
        )
        self.assertTrue(result["eligible"].iloc[0].all())
        self.assertFalse(result["eligible"].iloc[9].any())

    def test_eligible_excludes_inside_roll_window(self) -> None:
        days = pd.DataFrame(
            {"PT-A": [5.0]},
            index=pd.date_range("2026-01-01", periods=1, freq="D"),
        )
        prices = pd.DataFrame({"PT-A": [1.0]}, index=days.index)
        result = classify_pt_market_state(
            prices=prices,
            days_to_expiry=days,
            required_frames=[prices],
            roll_days_before_expiry=7,
            min_days_to_expiry=1,
            max_days_to_expiry=100,
        )
        self.assertFalse(result["eligible"].iloc[0, 0])

    def test_negative_days_to_expiry_not_eligible(self) -> None:
        days = pd.DataFrame(
            {"PT-A": [-5.0]},
            index=pd.date_range("2026-01-01", periods=1, freq="D"),
        )
        prices = pd.DataFrame({"PT-A": [1.0]}, index=days.index)
        result = classify_pt_market_state(
            prices=prices,
            days_to_expiry=days,
            required_frames=[prices],
            roll_days_before_expiry=7,
            min_days_to_expiry=1,
            max_days_to_expiry=100,
        )
        self.assertFalse(result["eligible"].iloc[0, 0])

    def test_empty_prices_returns_empty_frames(self) -> None:
        empty = pd.DataFrame()
        result = classify_pt_market_state(
            prices=empty,
            days_to_expiry=empty,
            required_frames=[],
            roll_days_before_expiry=7,
            min_days_to_expiry=5,
            max_days_to_expiry=100,
        )
        self.assertTrue(result["availability"].empty)
        self.assertTrue(result["eligible"].empty)

    def test_single_column_classification(self) -> None:
        prices = pd.DataFrame({"PT-A": [1.0, 0.0]}, index=pd.date_range("2026-01-01", periods=2, freq="D"))
        days = pd.DataFrame({"PT-A": [50.0, -1.0]}, index=prices.index)
        result = classify_pt_market_state(
            prices=prices,
            days_to_expiry=days,
            required_frames=[prices],
            roll_days_before_expiry=7,
            min_days_to_expiry=5,
            max_days_to_expiry=100,
        )
        self.assertTrue(result["eligible"].iloc[0, 0])
        self.assertFalse(result["eligible"].iloc[1, 0])
        self.assertTrue(result["expired_or_untradable"].iloc[1, 0])

    def test_min_days_to_expiry_filters(self) -> None:
        days = pd.DataFrame(
            {"PT-A": [3.0, 10.0]},
            index=pd.date_range("2026-01-01", periods=2, freq="D"),
        )
        prices = pd.DataFrame({"PT-A": [1.0, 1.0]}, index=days.index)
        result = classify_pt_market_state(
            prices=prices,
            days_to_expiry=days,
            required_frames=[prices],
            roll_days_before_expiry=2,
            min_days_to_expiry=5,
            max_days_to_expiry=100,
        )
        self.assertFalse(result["eligible"].iloc[0, 0])
        self.assertTrue(result["eligible"].iloc[1, 0])

    def test_max_days_to_expiry_filters(self) -> None:
        days = pd.DataFrame(
            {"PT-A": [150.0, 50.0]},
            index=pd.date_range("2026-01-01", periods=2, freq="D"),
        )
        prices = pd.DataFrame({"PT-A": [1.0, 1.0]}, index=days.index)
        result = classify_pt_market_state(
            prices=prices,
            days_to_expiry=days,
            required_frames=[prices],
            roll_days_before_expiry=7,
            min_days_to_expiry=5,
            max_days_to_expiry=100,
        )
        self.assertFalse(result["eligible"].iloc[0, 0])
        self.assertTrue(result["eligible"].iloc[1, 0])

    def test_nan_in_required_frame_blocks_availability(self) -> None:
        prices = pd.DataFrame(
            {"PT-A": [1.0, 1.0]},
            index=pd.date_range("2026-01-01", periods=2, freq="D"),
        )
        req1 = pd.DataFrame(
            {"PT-A": [0.05, np.nan]},
            index=prices.index,
        )
        result = classify_pt_market_state(
            prices=prices,
            days_to_expiry=pd.DataFrame({"PT-A": [50.0, 50.0]}, index=prices.index),
            required_frames=[req1],
            roll_days_before_expiry=7,
            min_days_to_expiry=5,
            max_days_to_expiry=100,
        )
        self.assertTrue(result["availability"].iloc[0, 0])
        self.assertFalse(result["availability"].iloc[1, 0])

    def test_multiple_required_frames_all_must_be_available(self) -> None:
        prices = pd.DataFrame(
            {"PT-A": [1.0, 1.0, 1.0]},
            index=pd.date_range("2026-01-01", periods=3, freq="D"),
        )
        req1 = pd.DataFrame(
            {"PT-A": [0.05, 0.05, 0.05]},
            index=prices.index,
        )
        req2 = pd.DataFrame(
            {"PT-A": [100.0, np.nan, 100.0]},
            index=prices.index,
        )
        result = classify_pt_market_state(
            prices=prices,
            days_to_expiry=pd.DataFrame({"PT-A": [50.0, 50.0, 50.0]}, index=prices.index),
            required_frames=[req1, req2],
            roll_days_before_expiry=7,
            min_days_to_expiry=5,
            max_days_to_expiry=100,
        )
        self.assertTrue(result["availability"].iloc[0, 0])
        self.assertFalse(result["availability"].iloc[1, 0])
        self.assertTrue(result["availability"].iloc[2, 0])


class SummarizePtUniverseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.index = pd.date_range("2026-01-01", periods=5, freq="D")
        self.prices = pd.DataFrame(
            {
                "PT-A": [1.0] * 5,
                "PT-B": [1.0] * 5,
                "PT-C": [1.0] * 5,
            },
            index=self.index,
        )

    def test_returns_expected_keys(self) -> None:
        eligible = pd.DataFrame(True, index=self.index, columns=self.prices.columns)
        result = summarize_pt_universe(
            prices=self.prices,
            eligible=eligible,
            inside_roll_window=pd.DataFrame(
                False, index=self.index, columns=self.prices.columns
            ),
            expired_or_untradable=pd.DataFrame(
                False, index=self.index, columns=self.prices.columns
            ),
        )
        expected_keys = {
            "eligible_market_count_min",
            "eligible_market_count_max",
            "eligible_market_count_median",
            "eligible_market_count_latest",
            "inside_roll_market_count_latest",
            "expired_market_count_latest",
            "markets_entered_during_backtest",
        }
        self.assertSetEqual(set(result.keys()), expected_keys)

    def test_all_eligible(self) -> None:
        eligible = pd.DataFrame(True, index=self.index, columns=self.prices.columns)
        result = summarize_pt_universe(
            prices=self.prices,
            eligible=eligible,
            inside_roll_window=pd.DataFrame(
                False, index=self.index, columns=self.prices.columns
            ),
            expired_or_untradable=pd.DataFrame(
                False, index=self.index, columns=self.prices.columns
            ),
        )
        self.assertEqual(result["eligible_market_count_min"], 3)
        self.assertEqual(result["eligible_market_count_max"], 3)
        self.assertEqual(result["eligible_market_count_median"], 3.0)
        self.assertEqual(result["eligible_market_count_latest"], 3)
        self.assertEqual(result["inside_roll_market_count_latest"], 0)
        self.assertEqual(result["expired_market_count_latest"], 0)
        self.assertEqual(result["markets_entered_during_backtest"], [])

    def test_partial_eligibility(self) -> None:
        eligible = pd.DataFrame(
            {
                "PT-A": [True, True, True, True, True],
                "PT-B": [True, True, False, False, False],
                "PT-C": [False, False, True, True, False],
            },
            index=self.index,
        )
        result = summarize_pt_universe(
            prices=self.prices,
            eligible=eligible,
            inside_roll_window=pd.DataFrame(
                False, index=self.index, columns=self.prices.columns
            ),
            expired_or_untradable=pd.DataFrame(
                False, index=self.index, columns=self.prices.columns
            ),
        )
        self.assertEqual(result["eligible_market_count_min"], 1)
        self.assertEqual(result["eligible_market_count_max"], 2)
        self.assertEqual(result["eligible_market_count_latest"], 1)

    def test_inside_roll_window_count(self) -> None:
        inside = pd.DataFrame(
            {
                "PT-A": [False, False, False, False, True],
                "PT-B": [False, False, False, False, False],
                "PT-C": [False, False, False, False, True],
            },
            index=self.index,
        )
        result = summarize_pt_universe(
            prices=self.prices,
            eligible=pd.DataFrame(True, index=self.index, columns=self.prices.columns),
            inside_roll_window=inside,
            expired_or_untradable=pd.DataFrame(
                False, index=self.index, columns=self.prices.columns
            ),
        )
        self.assertEqual(result["inside_roll_market_count_latest"], 2)

    def test_expired_count(self) -> None:
        expired = pd.DataFrame(
            {
                "PT-A": [False, False, False, False, True],
                "PT-B": [False, False, False, False, False],
            },
            index=self.index,
        )
        prices = pd.DataFrame(
            {"PT-A": [1.0] * 5, "PT-B": [1.0] * 5},
            index=self.index,
        )
        result = summarize_pt_universe(
            prices=prices,
            eligible=pd.DataFrame(True, index=self.index, columns=prices.columns),
            inside_roll_window=pd.DataFrame(
                False, index=self.index, columns=prices.columns
            ),
            expired_or_untradable=expired,
        )
        self.assertEqual(result["expired_market_count_latest"], 1)

    def test_dynamic_entries_detected(self) -> None:
        prices = pd.DataFrame(
            {
                "PT-A": [1.0, 1.0, 1.0, 1.0, 1.0],
                "PT-B": [np.nan, np.nan, 1.0, 1.0, 1.0],
            },
            index=self.index,
        )
        result = summarize_pt_universe(
            prices=prices,
            eligible=pd.DataFrame(True, index=self.index, columns=prices.columns),
            inside_roll_window=pd.DataFrame(
                False, index=self.index, columns=prices.columns
            ),
            expired_or_untradable=pd.DataFrame(
                False, index=self.index, columns=prices.columns
            ),
        )
        self.assertEqual(result["markets_entered_during_backtest"], ["PT-B"])

    def test_empty_data_returns_defaults(self) -> None:
        empty = pd.DataFrame()
        result = summarize_pt_universe(
            prices=empty,
            eligible=empty,
            inside_roll_window=empty,
            expired_or_untradable=empty,
        )
        self.assertEqual(result["eligible_market_count_min"], 0)
        self.assertEqual(result["eligible_market_count_max"], 0)
        self.assertEqual(result["eligible_market_count_median"], 0.0)
        self.assertEqual(result["eligible_market_count_latest"], 0)
        self.assertEqual(result["inside_roll_market_count_latest"], 0)
        self.assertEqual(result["expired_market_count_latest"], 0)

    def test_no_dynamic_entries_when_all_prices_start_together(self) -> None:
        result = summarize_pt_universe(
            prices=self.prices,
            eligible=pd.DataFrame(True, index=self.index, columns=self.prices.columns),
            inside_roll_window=pd.DataFrame(
                False, index=self.index, columns=self.prices.columns
            ),
            expired_or_untradable=pd.DataFrame(
                False, index=self.index, columns=self.prices.columns
            ),
        )
        self.assertEqual(result["markets_entered_during_backtest"], [])

    def test_all_nan_column_not_dynamic(self) -> None:
        prices = pd.DataFrame(
            {
                "PT-A": [1.0, 1.0, 1.0, 1.0, 1.0],
                "PT-B": [np.nan, np.nan, np.nan, np.nan, np.nan],
            },
            index=self.index,
        )
        result = summarize_pt_universe(
            prices=prices,
            eligible=pd.DataFrame(True, index=self.index, columns=prices.columns),
            inside_roll_window=pd.DataFrame(
                False, index=self.index, columns=prices.columns
            ),
            expired_or_untradable=pd.DataFrame(
                False, index=self.index, columns=prices.columns
            ),
        )
        self.assertEqual(result["markets_entered_during_backtest"], [])


class DetectPtRollEventsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.index = pd.date_range("2026-01-01", periods=10, freq="D")
        self.prices = pd.DataFrame(
            {"PT-A": [1.0] * 10, "PT-B": [1.0] * 10, "PT-C": [1.0] * 10},
            index=self.index,
        )
        self.days_to_expiry = pd.DataFrame(
            {"PT-A": [90.0, 80.0, 70.0, 60.0, 50.0, 40.0, 30.0, 20.0, 10.0, 0.0],
             "PT-B": [90.0, 80.0, 70.0, 60.0, 50.0, 40.0, 30.0, 20.0, 10.0, 0.0],
             "PT-C": [90.0, 80.0, 70.0, 60.0, 50.0, 40.0, 30.0, 20.0, 10.0, 0.0]},
            index=self.index,
        )

    def test_no_changes_no_events(self) -> None:
        positions = pd.DataFrame(
            {
                "PT-A": [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
                "PT-B": [0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5],
            },
            index=self.index,
        )
        events = detect_pt_roll_events(
            pt_positions=positions,
            eligible=pd.DataFrame(True, index=self.index, columns=self.prices.columns),
            inside_roll_window=pd.DataFrame(
                False, index=self.index, columns=self.prices.columns
            ),
            expired_or_untradable=pd.DataFrame(
                False, index=self.index, columns=self.prices.columns
            ),
            days_to_expiry=self.days_to_expiry,
        )
        self.assertEqual(events, [])

    def test_exit_due_to_expiry_detected(self) -> None:
        positions = pd.DataFrame(
            {
                "PT-A": [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 0.0],
            },
            index=self.index,
        )
        expired = pd.DataFrame(
            {
                "PT-A": [False] * 9 + [True],
            },
            index=self.index,
        )
        events = detect_pt_roll_events(
            pt_positions=positions,
            eligible=pd.DataFrame(True, index=self.index, columns=["PT-A"]),
            inside_roll_window=pd.DataFrame(
                False, index=self.index, columns=["PT-A"]
            ),
            expired_or_untradable=expired,
            days_to_expiry=self.days_to_expiry[["PT-A"]],
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["reason"], "expired_or_untradable")
        self.assertEqual(events[0]["from_markets"], ["PT-A"])
        self.assertEqual(events[0]["to_markets"], [])

    def test_exit_due_to_roll_window_detected(self) -> None:
        positions = pd.DataFrame(
            {
                "PT-A": [1.0, 1.0, 1.0, 1.0, 0.0, 1.0],
            },
            index=pd.date_range("2026-01-01", periods=6, freq="D"),
        )
        inside_roll = pd.DataFrame(
            {
                "PT-A": [False, False, False, False, True, False],
            },
            index=positions.index,
        )
        events = detect_pt_roll_events(
            pt_positions=positions,
            eligible=pd.DataFrame(True, index=positions.index, columns=["PT-A"]),
            inside_roll_window=inside_roll,
            expired_or_untradable=pd.DataFrame(
                False, index=positions.index, columns=["PT-A"]
            ),
            days_to_expiry=pd.DataFrame(
                {"PT-A": [10.0] * 6}, index=positions.index
            ),
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["reason"], "inside_roll_window")
        self.assertEqual(events[0]["from_markets"], ["PT-A"])

    def test_exit_without_expiry_or_roll_not_recorded(self) -> None:
        positions = pd.DataFrame(
            {
                "PT-A": [1.0, 0.0, 1.0],
            },
            index=pd.date_range("2026-01-01", periods=3, freq="D"),
        )
        events = detect_pt_roll_events(
            pt_positions=positions,
            eligible=pd.DataFrame(True, index=positions.index, columns=["PT-A"]),
            inside_roll_window=pd.DataFrame(
                False, index=positions.index, columns=["PT-A"]
            ),
            expired_or_untradable=pd.DataFrame(
                False, index=positions.index, columns=["PT-A"]
            ),
            days_to_expiry=pd.DataFrame(
                {"PT-A": [10.0] * 3}, index=positions.index
            ),
        )
        self.assertEqual(events, [])

    def test_roll_event_includes_market_counts(self) -> None:
        positions = pd.DataFrame(
            {
                "PT-A": [1.0, 0.0],
                "PT-B": [0.5, 0.5],
            },
            index=pd.date_range("2026-01-01", periods=2, freq="D"),
        )
        expired = pd.DataFrame(
            {
                "PT-A": [False, True],
                "PT-B": [False, False],
            },
            index=positions.index,
        )
        events = detect_pt_roll_events(
            pt_positions=positions,
            eligible=pd.DataFrame(
                True, index=positions.index, columns=["PT-A", "PT-B"]
            ),
            inside_roll_window=pd.DataFrame(
                False, index=positions.index, columns=["PT-A", "PT-B"]
            ),
            expired_or_untradable=expired,
            days_to_expiry=pd.DataFrame(
                {"PT-A": [10.0, 0.0], "PT-B": [10.0, 9.0]},
                index=positions.index,
            ),
        )
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["eligible_market_count"], 2)
        self.assertEqual(event["selected_market_count"], 1)

    def test_enter_after_exit_captured_in_roll_event(self) -> None:
        positions = pd.DataFrame(
            {
                "PT-A": [1.0, 0.0],
                "PT-B": [0.0, 0.5],
            },
            index=pd.date_range("2026-01-01", periods=2, freq="D"),
        )
        expired = pd.DataFrame(
            {
                "PT-A": [False, True],
                "PT-B": [False, False],
            },
            index=positions.index,
        )
        events = detect_pt_roll_events(
            pt_positions=positions,
            eligible=pd.DataFrame(
                True, index=positions.index, columns=["PT-A", "PT-B"]
            ),
            inside_roll_window=pd.DataFrame(
                False, index=positions.index, columns=["PT-A", "PT-B"]
            ),
            expired_or_untradable=expired,
            days_to_expiry=pd.DataFrame(
                {"PT-A": [10.0, 0.0], "PT-B": [10.0, 9.0]},
                index=positions.index,
            ),
        )
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["from_markets"], ["PT-A"])
        self.assertEqual(event["to_markets"], ["PT-B"])

    def test_roll_event_timestamp_format(self) -> None:
        positions = pd.DataFrame(
            {"PT-A": [1.0, 0.0]},
            index=pd.date_range("2026-01-01", periods=2, freq="D"),
        )
        expired = pd.DataFrame(
            {"PT-A": [False, True]},
            index=positions.index,
        )
        events = detect_pt_roll_events(
            pt_positions=positions,
            eligible=pd.DataFrame(True, index=positions.index, columns=["PT-A"]),
            inside_roll_window=pd.DataFrame(
                False, index=positions.index, columns=["PT-A"]
            ),
            expired_or_untradable=expired,
            days_to_expiry=pd.DataFrame(
                {"PT-A": [10.0, 0.0]}, index=positions.index
            ),
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["timestamp"], "2026-01-02T00:00:00")

    def test_roll_event_includes_days_to_expiry(self) -> None:
        positions = pd.DataFrame(
            {"PT-A": [1.0, 0.0], "PT-B": [0.0, 0.5]},
            index=pd.date_range("2026-01-01", periods=2, freq="D"),
        )
        expired = pd.DataFrame(
            {"PT-A": [False, True], "PT-B": [False, False]},
            index=positions.index,
        )
        events = detect_pt_roll_events(
            pt_positions=positions,
            eligible=pd.DataFrame(
                True, index=positions.index, columns=["PT-A", "PT-B"]
            ),
            inside_roll_window=pd.DataFrame(
                False, index=positions.index, columns=["PT-A", "PT-B"]
            ),
            expired_or_untradable=expired,
            days_to_expiry=pd.DataFrame(
                {"PT-A": [10.0, 0.0], "PT-B": [10.0, 9.0]},
                index=positions.index,
            ),
        )
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertIn("from_days_to_expiry", event)
        self.assertIn("to_days_to_expiry", event)
        self.assertEqual(event["from_days_to_expiry"]["PT-A"], 0.0)
        self.assertEqual(event["to_days_to_expiry"]["PT-B"], 9.0)

    def test_multiple_roll_events(self) -> None:
        positions = pd.DataFrame(
            {
                "PT-A": [1.0, 0.0, 1.0],
                "PT-B": [0.0, 1.0, 0.0],
            },
            index=pd.date_range("2026-01-01", periods=3, freq="D"),
        )
        expired = pd.DataFrame(
            {
                "PT-A": [False, True, False],
                "PT-B": [False, False, False],
            },
            index=positions.index,
        )
        inside_roll = pd.DataFrame(
            {
                "PT-A": [False, False, False],
                "PT-B": [False, False, True],
            },
            index=positions.index,
        )
        events = detect_pt_roll_events(
            pt_positions=positions,
            eligible=pd.DataFrame(
                True, index=positions.index, columns=["PT-A", "PT-B"]
            ),
            inside_roll_window=inside_roll,
            expired_or_untradable=expired,
            days_to_expiry=pd.DataFrame(
                {"PT-A": [10.0, 0.0, 5.0], "PT-B": [10.0, 9.0, 3.0]},
                index=positions.index,
            ),
        )
        self.assertEqual(len(events), 2)

    def test_zero_positions_ignored(self) -> None:
        positions = pd.DataFrame(
            {"PT-A": [0.0, 0.0]},
            index=pd.date_range("2026-01-01", periods=2, freq="D"),
        )
        events = detect_pt_roll_events(
            pt_positions=positions,
            eligible=pd.DataFrame(True, index=positions.index, columns=["PT-A"]),
            inside_roll_window=pd.DataFrame(
                True, index=positions.index, columns=["PT-A"]
            ),
            expired_or_untradable=pd.DataFrame(
                False, index=positions.index, columns=["PT-A"]
            ),
            days_to_expiry=pd.DataFrame(
                {"PT-A": [5.0, 3.0]}, index=positions.index
            ),
        )
        self.assertEqual(events, [])

    def test_nan_positions_treated_as_zero(self) -> None:
        positions = pd.DataFrame(
            {"PT-A": [1.0, np.nan]},
            index=pd.date_range("2026-01-01", periods=2, freq="D"),
        )
        expired = pd.DataFrame(
            {"PT-A": [False, True]},
            index=positions.index,
        )
        events = detect_pt_roll_events(
            pt_positions=positions,
            eligible=pd.DataFrame(True, index=positions.index, columns=["PT-A"]),
            inside_roll_window=pd.DataFrame(
                False, index=positions.index, columns=["PT-A"]
            ),
            expired_or_untradable=expired,
            days_to_expiry=pd.DataFrame(
                {"PT-A": [10.0, 0.0]}, index=positions.index
            ),
        )
        self.assertEqual(len(events), 1)

    def test_roll_window_expiry_days_to_expiry_in_event(self) -> None:
        positions = pd.DataFrame(
            {"PT-A": [1.0, 0.0]},
            index=pd.date_range("2026-01-01", periods=2, freq="D"),
        )
        inside_roll = pd.DataFrame(
            {"PT-A": [False, True]},
            index=positions.index,
        )
        events = detect_pt_roll_events(
            pt_positions=positions,
            eligible=pd.DataFrame(True, index=positions.index, columns=["PT-A"]),
            inside_roll_window=inside_roll,
            expired_or_untradable=pd.DataFrame(
                False, index=positions.index, columns=["PT-A"]
            ),
            days_to_expiry=pd.DataFrame(
                {"PT-A": [10.0, 3.0]}, index=positions.index
            ),
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["reason"], "inside_roll_window")
        self.assertEqual(events[0]["from_days_to_expiry"]["PT-A"], 3.0)

    def test_single_row_no_events(self) -> None:
        positions = pd.DataFrame(
            {"PT-A": [1.0]},
            index=pd.date_range("2026-01-01", periods=1, freq="D"),
        )
        events = detect_pt_roll_events(
            pt_positions=positions,
            eligible=pd.DataFrame(True, index=positions.index, columns=["PT-A"]),
            inside_roll_window=pd.DataFrame(
                False, index=positions.index, columns=["PT-A"]
            ),
            expired_or_untradable=pd.DataFrame(
                False, index=positions.index, columns=["PT-A"]
            ),
            days_to_expiry=pd.DataFrame(
                {"PT-A": [10.0]}, index=positions.index
            ),
        )
        self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
