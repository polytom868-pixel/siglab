from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from typing import Any

import numpy as np

from siglab.evaluator.gates import evaluate_gates
from siglab.evaluator.score import (
    _bounded,
    _safe_nanmedian,
    _safe_nanmin,
    serialize_stats,
    summarize_window_results,
)

_BASE_TREND_SIGNALS: dict[str, Any] = {
    "median_total_return": 0.05,
    "median_sharpe": 1.0,
    "worst_max_drawdown": -0.10,
    "asset_breadth": 3,
    "canonical_series_valid": True,
}

_BASE_YIELD_FLOWS: dict[str, Any] = _BASE_TREND_SIGNALS.copy()


# ---------------------------------------------------------------------------
#  evaluate_gates  (siglab.evaluator.gates)
# ---------------------------------------------------------------------------

class EvaluateGatesAllPassTests(unittest.TestCase):
    """evaluate_gates returns (True, []) when all conditions pass."""

    def test_trend_signals_passes_all_gates(self) -> None:
        summary = {**_BASE_TREND_SIGNALS, "median_sharpe": 1.2}
        ok, reasons = evaluate_gates("trend_signals", summary)
        self.assertTrue(ok)
        self.assertEqual(reasons, [])

    def test_yield_flows_passes_all_gates(self) -> None:
        summary = {**_BASE_YIELD_FLOWS, "median_sharpe": 0.5}
        ok, reasons = evaluate_gates("yield_flows", summary)
        self.assertTrue(ok)
        self.assertEqual(reasons, [])

    def test_empty_summary_fails_with_default_values(self) -> None:
        ok, reasons = evaluate_gates("trend_signals", {})
        self.assertFalse(ok)
        self.assertIn("non_positive_median_return", reasons)
        self.assertIn("non_positive_median_sharpe", reasons)
        self.assertIn("insufficient_breadth", reasons)


class EvaluateGatesLiquidationTests(unittest.TestCase):
    """evaluate_gates rejects on positive liquidation_count."""

    def test_liquidation_count_positive_fails(self) -> None:
        summary: dict[str, Any] = {"liquidation_count": 1}
        ok, reasons = evaluate_gates("trend_signals", summary)
        self.assertFalse(ok)
        self.assertIn("liquidation", reasons)

    def test_liquidation_count_large_fails(self) -> None:
        summary: dict[str, Any] = {"liquidation_count": 5}
        ok, reasons = evaluate_gates("trend_signals", summary)
        self.assertFalse(ok)
        self.assertIn("liquidation", reasons)


class EvaluateGatesReturnTests(unittest.TestCase):
    """evaluate_gates rejects on non-positive median total return."""

    def test_zero_median_total_return_fails(self) -> None:
        summary: dict[str, Any] = {"median_total_return": 0.0}
        ok, reasons = evaluate_gates("trend_signals", summary)
        self.assertFalse(ok)
        self.assertIn("non_positive_median_return", reasons)

    def test_negative_median_total_return_fails(self) -> None:
        summary: dict[str, Any] = {"median_total_return": -0.1}
        ok, reasons = evaluate_gates("trend_signals", summary)
        self.assertFalse(ok)
        self.assertIn("non_positive_median_return", reasons)

    def test_positive_median_total_return_passes(self) -> None:
        summary: dict[str, Any] = {**_BASE_TREND_SIGNALS, "median_total_return": 0.01}
        ok, reasons = evaluate_gates("trend_signals", summary)
        self.assertTrue(ok)
        self.assertNotIn("non_positive_median_return", reasons)


class EvaluateGatesSharpeTests(unittest.TestCase):
    """evaluate_gates rejects on non-positive median sharpe."""

    def test_zero_median_sharpe_fails(self) -> None:
        summary: dict[str, Any] = {"median_sharpe": 0.0}
        ok, reasons = evaluate_gates("trend_signals", summary)
        self.assertFalse(ok)
        self.assertIn("non_positive_median_sharpe", reasons)

    def test_negative_median_sharpe_fails(self) -> None:
        summary: dict[str, Any] = {"median_sharpe": -0.5}
        ok, reasons = evaluate_gates("trend_signals", summary)
        self.assertFalse(ok)
        self.assertIn("non_positive_median_sharpe", reasons)


class EvaluateGatesValidationTests(unittest.TestCase):
    """evaluate_gates validation holdout checks."""

    def test_validation_available_with_negative_return_fails(self) -> None:
        summary: dict[str, Any] = {
            "validation_available": True,
            "validation_total_return": -0.05,
            "validation_sharpe": 1.0,
        }
        ok, reasons = evaluate_gates("trend_signals", summary)
        self.assertFalse(ok)
        self.assertIn("non_positive_validation_return", reasons)

    def test_validation_available_with_negative_sharpe_fails(self) -> None:
        summary: dict[str, Any] = {
            "validation_available": True,
            "validation_total_return": 0.05,
            "validation_sharpe": -0.1,
        }
        ok, reasons = evaluate_gates("trend_signals", summary)
        self.assertFalse(ok)
        self.assertIn("non_positive_validation_sharpe", reasons)

    def test_validation_available_both_negative_fails(self) -> None:
        summary: dict[str, Any] = {
            "validation_available": True,
            "validation_total_return": -0.02,
            "validation_sharpe": -0.3,
        }
        ok, reasons = evaluate_gates("trend_signals", summary)
        self.assertIn("non_positive_validation_return", reasons)
        self.assertIn("non_positive_validation_sharpe", reasons)

    def test_validation_not_available_skipped(self) -> None:
        summary: dict[str, Any] = {**_BASE_TREND_SIGNALS, "validation_available": False}
        ok, reasons = evaluate_gates("trend_signals", summary)
        self.assertTrue(ok)
        self.assertNotIn("non_positive_validation_return", reasons)
        self.assertNotIn("non_positive_validation_sharpe", reasons)


class EvaluateGatesPreAuditCanonicalReturnTests(unittest.TestCase):
    """evaluate_gates checks pre_audit_canonical_total_return."""

    def test_negative_pre_audit_return_fails(self) -> None:
        summary: dict[str, Any] = {"pre_audit_canonical_total_return": -0.1}
        ok, reasons = evaluate_gates("trend_signals", summary)
        self.assertFalse(ok)
        self.assertIn("non_positive_pre_audit_canonical_return", reasons)

    def test_zero_pre_audit_return_fails(self) -> None:
        summary: dict[str, Any] = {"pre_audit_canonical_total_return": 0.0}
        ok, reasons = evaluate_gates("trend_signals", summary)
        self.assertFalse(ok)
        self.assertIn("non_positive_pre_audit_canonical_return", reasons)

    def test_positive_pre_audit_return_passes(self) -> None:
        summary: dict[str, Any] = {**_BASE_TREND_SIGNALS, "pre_audit_canonical_total_return": 0.1}
        ok, reasons = evaluate_gates("trend_signals", summary)
        self.assertTrue(ok)

    def test_missing_pre_audit_return_passes(self) -> None:
        ok, reasons = evaluate_gates("trend_signals", _BASE_TREND_SIGNALS)
        self.assertTrue(ok)
        self.assertNotIn("non_positive_pre_audit_canonical_return", reasons)


class EvaluateGatesCanonicalSeriesTests(unittest.TestCase):
    """evaluate_gates checks canonical_series_valid."""

    def test_invalid_canonical_series_fails(self) -> None:
        summary: dict[str, Any] = {"canonical_series_valid": False}
        ok, reasons = evaluate_gates("trend_signals", summary)
        self.assertFalse(ok)
        self.assertIn("invalid_canonical_series", reasons)

    def test_canonical_series_valid_passes(self) -> None:
        summary: dict[str, Any] = {**_BASE_TREND_SIGNALS, "canonical_series_valid": True}
        ok, reasons = evaluate_gates("trend_signals", summary)
        self.assertTrue(ok)

    def test_missing_canonical_series_defaults_to_valid(self) -> None:
        ok, reasons = evaluate_gates("trend_signals", _BASE_TREND_SIGNALS)
        self.assertTrue(ok)
        self.assertNotIn("invalid_canonical_series", reasons)


class EvaluateGatesDrawdownTests(unittest.TestCase):
    """evaluate_gates checks worst_max_drawdown limits by track."""

    def test_trend_signals_drawdown_below_limit_fails(self) -> None:
        summary: dict[str, Any] = {"worst_max_drawdown": -0.40}
        ok, reasons = evaluate_gates("trend_signals", summary)
        self.assertFalse(ok)
        self.assertIn("drawdown_limit", reasons)

    def test_trend_signals_drawdown_at_limit_passes(self) -> None:
        summary: dict[str, Any] = {**_BASE_TREND_SIGNALS, "worst_max_drawdown": -0.35}
        ok, reasons = evaluate_gates("trend_signals", summary)
        self.assertTrue(ok)

    def test_trend_signals_drawdown_above_limit_passes(self) -> None:
        summary: dict[str, Any] = {**_BASE_TREND_SIGNALS, "worst_max_drawdown": -0.30}
        ok, reasons = evaluate_gates("trend_signals", summary)
        self.assertTrue(ok)

    def test_yield_flows_drawdown_below_limit_fails(self) -> None:
        summary: dict[str, Any] = {"worst_max_drawdown": -0.30}
        ok, reasons = evaluate_gates("yield_flows", summary)
        self.assertFalse(ok)
        self.assertIn("drawdown_limit", reasons)

    def test_yield_flows_drawdown_at_limit_passes(self) -> None:
        summary: dict[str, Any] = {**_BASE_YIELD_FLOWS, "worst_max_drawdown": -0.25}
        ok, reasons = evaluate_gates("yield_flows", summary)
        self.assertTrue(ok)

    def test_yield_flows_drawdown_above_limit_passes(self) -> None:
        summary: dict[str, Any] = {**_BASE_YIELD_FLOWS, "worst_max_drawdown": -0.20}
        ok, reasons = evaluate_gates("yield_flows", summary)
        self.assertTrue(ok)


class EvaluateGatesBreadthTests(unittest.TestCase):
    """evaluate_gates checks asset_breadth by track."""

    def test_trend_signals_breadth_below_two_fails(self) -> None:
        summary: dict[str, Any] = {"asset_breadth": 1}
        ok, reasons = evaluate_gates("trend_signals", summary)
        self.assertFalse(ok)
        self.assertIn("insufficient_breadth", reasons)

    def test_trend_signals_breadth_at_two_passes(self) -> None:
        summary: dict[str, Any] = {**_BASE_TREND_SIGNALS, "asset_breadth": 2}
        ok, reasons = evaluate_gates("trend_signals", summary)
        self.assertTrue(ok)

    def test_yield_flows_breadth_below_one_fails(self) -> None:
        summary: dict[str, Any] = {"asset_breadth": 0}
        ok, reasons = evaluate_gates("yield_flows", summary)
        self.assertFalse(ok)
        self.assertIn("insufficient_breadth", reasons)

    def test_yield_flows_breadth_at_one_passes(self) -> None:
        summary: dict[str, Any] = {**_BASE_YIELD_FLOWS, "asset_breadth": 1}
        ok, reasons = evaluate_gates("yield_flows", summary)
        self.assertTrue(ok)


class EvaluateGatesUnknownTrackTests(unittest.TestCase):
    """evaluate_gates handles tracks not in TRACK_ALIASES."""

    def test_unknown_track_uses_default_drawdown(self) -> None:
        summary: dict[str, Any] = {
            "worst_max_drawdown": -0.30,
            "asset_breadth": 1,
        }
        ok, reasons = evaluate_gates("unknown_track", summary)
        self.assertFalse(ok)
        self.assertIn("drawdown_limit", reasons)
        self.assertNotIn("insufficient_breadth", reasons)

    def test_unknown_track_low_drawdown_passes(self) -> None:
        summary: dict[str, Any] = {**_BASE_TREND_SIGNALS, "asset_breadth": 1}
        ok, reasons = evaluate_gates("unknown_track", summary)
        self.assertTrue(ok)


class EvaluateGatesCombinedTests(unittest.TestCase):
    """evaluate_gates returns all failing reasons together."""

    def test_multiple_failures_collected(self) -> None:
        summary: dict[str, Any] = {
            "liquidation_count": 2,
            "median_total_return": -0.05,
            "median_sharpe": -1.0,
            "worst_max_drawdown": -0.40,
            "asset_breadth": 0,
            "canonical_series_valid": False,
            "validation_available": True,
            "validation_total_return": -0.01,
            "validation_sharpe": -0.1,
            "pre_audit_canonical_total_return": -0.2,
        }
        ok, reasons = evaluate_gates("trend_signals", summary)
        self.assertFalse(ok)
        expected_reasons = [
            "liquidation",
            "non_positive_median_return",
            "non_positive_median_sharpe",
            "non_positive_validation_return",
            "non_positive_validation_sharpe",
            "non_positive_pre_audit_canonical_return",
            "invalid_canonical_series",
            "drawdown_limit",
            "insufficient_breadth",
        ]
        for r in expected_reasons:
            self.assertIn(r, reasons, msg=f"Missing reason: {r}")
        self.assertEqual(len(reasons), len(expected_reasons))


# ---------------------------------------------------------------------------
#  serialize_stats  (siglab.evaluator.score)
# ---------------------------------------------------------------------------

class SerializeStatsTests(unittest.TestCase):
    """serialize_stats converts special objects to JSON-safe types."""

    def test_passes_through_normal_numbers(self) -> None:
        stats = {"a": 1, "b": 2.5, "c": "hello", "d": True}
        result = serialize_stats(stats)
        self.assertEqual(result, stats)

    def test_converts_numpy_floats(self) -> None:
        stats = {"a": np.float64(3.14), "b": np.float32(1.5)}
        result = serialize_stats(stats)
        self.assertIsInstance(result["a"], float)
        self.assertIsInstance(result["b"], float)
        self.assertAlmostEqual(result["a"], 3.14)

    def test_converts_numpy_integers(self) -> None:
        stats = {"a": np.int64(42), "b": np.int32(7)}
        result = serialize_stats(stats)
        self.assertIsInstance(result["a"], float)
        self.assertIsInstance(result["b"], float)
        self.assertEqual(result["a"], 42.0)

    def test_converts_datetime_to_isoformat(self) -> None:
        dt = datetime(2026, 5, 30, 12, 0, 0)
        stats = {"timestamp": dt}
        result = serialize_stats(stats)
        self.assertEqual(result["timestamp"], "2026-05-30T12:00:00")

    def test_converts_timedelta_to_seconds(self) -> None:
        td = timedelta(hours=2, minutes=30)
        stats = {"duration": td}
        result = serialize_stats(stats)
        self.assertEqual(result["duration"], 9000.0)

    def test_mixed_types_handled(self) -> None:
        dt = datetime(2026, 1, 1)
        td = timedelta(days=1)
        stats: dict[str, Any] = {
            "int_val": 10,
            "float_val": 3.14,
            "np_float": np.float64(2.71),
            "np_int": np.int32(99),
            "timestamp": dt,
            "duration": td,
            "str_val": "keep",
        }
        result = serialize_stats(stats)
        self.assertEqual(result["int_val"], 10)
        self.assertAlmostEqual(result["float_val"], 3.14)
        self.assertIsInstance(result["np_float"], float)
        self.assertIsInstance(result["np_int"], float)
        self.assertEqual(result["timestamp"], "2026-01-01T00:00:00")
        self.assertEqual(result["duration"], 86400.0)
        self.assertEqual(result["str_val"], "keep")

    def test_empty_stats_returns_empty(self) -> None:
        result = serialize_stats({})
        self.assertEqual(result, {})


# ---------------------------------------------------------------------------
#  _safe_nanmedian  (siglab.evaluator.score)
# ---------------------------------------------------------------------------

class SafeNanmedianTests(unittest.TestCase):
    """_safe_nanmedian returns median ignoring NaN."""

    def test_normal_values(self) -> None:
        arr = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        result = _safe_nanmedian(arr)
        self.assertEqual(result, 3.0)

    def test_with_some_nan(self) -> None:
        arr = np.array([1.0, np.nan, 3.0, np.nan, 5.0])
        result = _safe_nanmedian(arr)
        self.assertEqual(result, 3.0)

    def test_all_nan_returns_default(self) -> None:
        arr = np.array([np.nan, np.nan, np.nan])
        result = _safe_nanmedian(arr, default=0.0)
        self.assertEqual(result, 0.0)

    def test_all_nan_custom_default(self) -> None:
        arr = np.array([np.nan, np.nan])
        result = _safe_nanmedian(arr, default=-1.0)
        self.assertEqual(result, -1.0)

    def test_empty_array_returns_default(self) -> None:
        arr = np.array([])
        result = _safe_nanmedian(arr, default=0.0)
        self.assertEqual(result, 0.0)

    def test_single_value(self) -> None:
        arr = np.array([42.0])
        result = _safe_nanmedian(arr)
        self.assertEqual(result, 42.0)

    def test_single_nan_returns_default(self) -> None:
        arr = np.array([np.nan])
        result = _safe_nanmedian(arr, default=5.0)
        self.assertEqual(result, 5.0)

    def test_even_count_median(self) -> None:
        arr = np.array([1.0, 2.0, 3.0, 4.0])
        result = _safe_nanmedian(arr)
        self.assertEqual(result, 2.5)


# ---------------------------------------------------------------------------
#  _safe_nanmin  (siglab.evaluator.score)
# ---------------------------------------------------------------------------

class SafeNanminTests(unittest.TestCase):
    """_safe_nanmin returns min ignoring NaN."""

    def test_normal_values(self) -> None:
        arr = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        result = _safe_nanmin(arr)
        self.assertEqual(result, 1.0)

    def test_with_some_nan(self) -> None:
        arr = np.array([np.nan, 2.0, np.nan, 4.0])
        result = _safe_nanmin(arr)
        self.assertEqual(result, 2.0)

    def test_all_nan_returns_default(self) -> None:
        arr = np.array([np.nan, np.nan])
        result = _safe_nanmin(arr, default=0.0)
        self.assertEqual(result, 0.0)

    def test_all_nan_custom_default(self) -> None:
        arr = np.array([np.nan, np.nan])
        result = _safe_nanmin(arr, default=-5.0)
        self.assertEqual(result, -5.0)

    def test_empty_array_returns_default(self) -> None:
        arr = np.array([])
        result = _safe_nanmin(arr, default=0.0)
        self.assertEqual(result, 0.0)

    def test_single_value(self) -> None:
        arr = np.array([-3.0])
        result = _safe_nanmin(arr)
        self.assertEqual(result, -3.0)

    def test_negative_values(self) -> None:
        arr = np.array([-5.0, -2.0, -10.0])
        result = _safe_nanmin(arr)
        self.assertEqual(result, -10.0)

    def test_single_nan_returns_default(self) -> None:
        arr = np.array([np.nan])
        result = _safe_nanmin(arr, default=1.0)
        self.assertEqual(result, 1.0)


# ---------------------------------------------------------------------------
#  _bounded  (siglab.evaluator.score)
# ---------------------------------------------------------------------------

class BoundedTests(unittest.TestCase):
    """_bounded clamps value within [lower, upper]."""

    def test_value_within_bounds(self) -> None:
        result = _bounded(5.0, lower=0.0, upper=10.0)
        self.assertEqual(result, 5.0)

    def test_value_at_lower_bound(self) -> None:
        result = _bounded(0.0, lower=0.0, upper=10.0)
        self.assertEqual(result, 0.0)

    def test_value_at_upper_bound(self) -> None:
        result = _bounded(10.0, lower=0.0, upper=10.0)
        self.assertEqual(result, 10.0)

    def test_value_below_lower_bound(self) -> None:
        result = _bounded(-5.0, lower=0.0, upper=10.0)
        self.assertEqual(result, 0.0)

    def test_value_above_upper_bound(self) -> None:
        result = _bounded(15.0, lower=0.0, upper=10.0)
        self.assertEqual(result, 10.0)

    def test_nan_returns_zero(self) -> None:
        result = _bounded(np.nan, lower=0.0, upper=10.0)
        self.assertEqual(result, 0.0)

    def test_inf_returns_zero(self) -> None:
        result = _bounded(np.inf, lower=0.0, upper=10.0)
        self.assertEqual(result, 0.0)

    def test_neg_inf_returns_zero(self) -> None:
        result = _bounded(-np.inf, lower=0.0, upper=10.0)
        self.assertEqual(result, 0.0)

    def test_negative_bounds(self) -> None:
        result = _bounded(-5.0, lower=-10.0, upper=-1.0)
        self.assertEqual(result, -5.0)

    def test_below_negative_bounds(self) -> None:
        result = _bounded(-15.0, lower=-10.0, upper=-1.0)
        self.assertEqual(result, -10.0)

    def test_above_negative_bounds(self) -> None:
        result = _bounded(0.0, lower=-10.0, upper=-1.0)
        self.assertEqual(result, -1.0)


# ---------------------------------------------------------------------------
#  summarize_window_results  (siglab.evaluator.score)
# ---------------------------------------------------------------------------

def _make_window(
    sharpe: float,
    total_return: float,
    cagr: float = 0.0,
    calmar: float = 0.0,
    max_drawdown: float = 0.0,
    liquidated: bool = False,
) -> dict[str, Any]:
    return {
        "stats": {
            "sharpe": sharpe,
            "total_return": total_return,
            "cagr": cagr,
            "calmar": calmar,
            "max_drawdown": max_drawdown,
        },
        "liquidated": liquidated,
    }


class SummarizeWindowResultsTests(unittest.TestCase):
    """summarize_window_results aggregates window results."""

    def test_single_positive_window(self) -> None:
        windows = [_make_window(sharpe=1.5, total_return=0.10, cagr=0.05, calmar=2.0, max_drawdown=-0.05)]
        result = summarize_window_results(window_results=windows, asset_breadth=3)
        self.assertAlmostEqual(result["aggregate_score"], 1.5 + 4.0 * 0.10 + 0.5 * 2.0 + 0.1 * 3 + 0.25 * 1.0 + 1.5 * (-0.05))
        self.assertEqual(result["median_sharpe"], 1.5)
        self.assertEqual(result["median_total_return"], 0.10)
        self.assertEqual(result["median_cagr"], 0.05)
        self.assertEqual(result["median_calmar"], 2.0)
        self.assertEqual(result["worst_max_drawdown"], -0.05)
        self.assertEqual(result["liquidation_count"], 0)
        self.assertEqual(result["window_count"], 1)
        self.assertEqual(result["profitable_window_pct"], 1.0)
        self.assertEqual(result["asset_breadth"], 3)

    def test_multiple_windows_averaged(self) -> None:
        windows = [
            _make_window(sharpe=1.0, total_return=0.05),
            _make_window(sharpe=2.0, total_return=0.15),
        ]
        result = summarize_window_results(window_results=windows, asset_breadth=2)
        self.assertAlmostEqual(result["median_sharpe"], 1.5)
        self.assertAlmostEqual(result["median_total_return"], 0.10)

    def test_multiple_windows_odd_count(self) -> None:
        windows = [
            _make_window(sharpe=1.0, total_return=0.05),
            _make_window(sharpe=2.0, total_return=0.15),
            _make_window(sharpe=3.0, total_return=0.25),
        ]
        result = summarize_window_results(window_results=windows, asset_breadth=1)
        self.assertAlmostEqual(result["median_sharpe"], 2.0)
        self.assertAlmostEqual(result["median_total_return"], 0.15)

    def test_some_liquidated(self) -> None:
        windows = [
            _make_window(sharpe=1.0, total_return=0.05, liquidated=False),
            _make_window(sharpe=-2.0, total_return=-0.30, liquidated=True),
            _make_window(sharpe=1.5, total_return=0.10, liquidated=False),
        ]
        result = summarize_window_results(window_results=windows, asset_breadth=2)
        # median sharpe: [-2.0, 1.0, 1.5] -> 1.0
        self.assertAlmostEqual(result["median_sharpe"], 1.0)
        self.assertEqual(result["liquidation_count"], 1)
        self.assertEqual(result["window_count"], 3)

    def test_all_liquidated(self) -> None:
        windows = [
            _make_window(sharpe=-5.0, total_return=-0.50, liquidated=True),
            _make_window(sharpe=-3.0, total_return=-0.40, liquidated=True),
        ]
        result = summarize_window_results(window_results=windows, asset_breadth=1)
        self.assertEqual(result["liquidation_count"], 2)
        self.assertEqual(result["window_count"], 2)
        self.assertAlmostEqual(result["profitable_window_pct"], 0.0)
        self.assertIsNotNone(result["aggregate_score"])

    def test_empty_windows_still_returns_structure(self) -> None:
        result = summarize_window_results(window_results=[], asset_breadth=0)
        self.assertEqual(result["window_count"], 0)
        self.assertEqual(result["liquidation_count"], 0)
        self.assertEqual(result["median_sharpe"], 0.0)
        self.assertEqual(result["median_total_return"], 0.0)
        self.assertEqual(result["worst_max_drawdown"], 0.0)
        self.assertIsInstance(result["profitable_window_pct"], float)
        expected_keys = {
            "aggregate_score", "median_sharpe", "median_total_return",
            "median_cagr", "median_calmar", "worst_max_drawdown",
            "liquidation_count", "window_count", "profitable_window_pct",
            "asset_breadth", "score_component_caps",
        }
        self.assertSetEqual(set(result.keys()), expected_keys)

    def test_nan_in_stats_is_handled(self) -> None:
        windows = [
            _make_window(sharpe=np.nan, total_return=np.nan),
            _make_window(sharpe=2.0, total_return=0.10),
        ]
        result = summarize_window_results(window_results=windows, asset_breadth=2)
        self.assertEqual(result["median_sharpe"], 2.0)
        self.assertEqual(result["median_total_return"], 0.10)

    def test_all_nan_stats_returns_defaults(self) -> None:
        windows = [
            _make_window(sharpe=np.nan, total_return=np.nan),
            _make_window(sharpe=np.nan, total_return=np.nan),
        ]
        result = summarize_window_results(window_results=windows, asset_breadth=0)
        self.assertEqual(result["median_sharpe"], 0.0)
        self.assertEqual(result["median_total_return"], 0.0)
        self.assertEqual(result["worst_max_drawdown"], 0.0)
        self.assertAlmostEqual(result["profitable_window_pct"], 0.0)

    def test_score_component_caps_present(self) -> None:
        windows = [_make_window(sharpe=2.0, total_return=0.20, calmar=3.0, max_drawdown=-0.10)]
        result = summarize_window_results(window_results=windows, asset_breadth=1)
        caps = result["score_component_caps"]
        self.assertIn("median_sharpe", caps)
        self.assertIn("median_total_return", caps)
        self.assertIn("median_calmar", caps)
        self.assertIn("median_drawdown", caps)
        self.assertAlmostEqual(caps["median_sharpe"], 2.0)
        self.assertAlmostEqual(caps["median_total_return"], 0.20)

    def test_score_component_caps_bounded(self) -> None:
        windows = [_make_window(sharpe=100.0, total_return=10.0, calmar=200.0, max_drawdown=-0.99)]
        result = summarize_window_results(window_results=windows, asset_breadth=1)
        caps = result["score_component_caps"]
        self.assertAlmostEqual(caps["median_sharpe"], 20.0)
        self.assertAlmostEqual(caps["median_total_return"], 5.0)
        self.assertAlmostEqual(caps["median_calmar"], 50.0)
        self.assertAlmostEqual(caps["median_drawdown"], -0.99)

    def test_aggregate_score_formula(self) -> None:
        windows = [
            _make_window(sharpe=1.0, total_return=0.10, calmar=1.0, max_drawdown=-0.20),
        ]
        result = summarize_window_results(window_results=windows, asset_breadth=2)
        # score_sharpe = _bounded(1.0, lower=-20, upper=20) = 1.0
        # score_return = _bounded(0.10, lower=-1, upper=5) = 0.10
        # score_calmar = _bounded(1.0, lower=-50, upper=50) = 1.0
        # score_drawdown = _bounded(-0.20, lower=-1, upper=0) = -0.20
        # aggregate = 1.0 + 4*0.10 + 0.5*1.0 + 0.1*2 + 0.25*1.0 + 1.5*(-0.20)
        #           = 1.0 + 0.40 + 0.50 + 0.20 + 0.25 - 0.30
        #           = 2.05
        expected = 1.0 + 4.0 * 0.10 + 0.5 * 1.0 + 0.1 * 2 + 0.25 * 1.0 + 1.5 * (-0.20)
        self.assertAlmostEqual(result["aggregate_score"], expected)


if __name__ == "__main__":
    unittest.main()
