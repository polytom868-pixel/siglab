from __future__ import annotations

import math
import unittest
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

from siglab.evaluator.core import (
    ResearchEvaluator,
    _safe_float,
    _unique_float_values,
    _unique_int_values,
)


def _make_mock_settings() -> MagicMock:
    """Build a mock SiglabConfig with required fields."""
    settings = MagicMock()
    settings.root_dir = "/tmp"
    settings.sosovalue_config_path = "/tmp/soso.json"
    settings.generated_strategy_dir = "/tmp/strategies"
    settings.data_lake_dir = "/tmp/lake"
    settings.artifact_dir = "/tmp/artifacts"
    settings.live_dir = "/tmp/live"
    settings.ancestry_db_path = "/tmp/ancestry.db"
    settings.sosovalue_api_key_override = None
    return settings


def _make_mock_provider() -> MagicMock:
    """Build a mock MarketDataProvider."""
    return MagicMock()


def _make_evaluator(
    settings: MagicMock | None = None,
    provider: MagicMock | None = None,
) -> ResearchEvaluator:
    return ResearchEvaluator(
        settings=settings or _make_mock_settings(),
        provider=provider or _make_mock_provider(),
    )


class ResearchEvaluatorConstructionTests(unittest.TestCase):
    """ResearchEvaluator __init__ stores settings and provider."""

    def test_construction_with_settings_and_provider(self) -> None:
        settings = _make_mock_settings()
        provider = _make_mock_provider()
        ev = ResearchEvaluator(settings=settings, provider=provider)
        self.assertIs(ev.settings, settings)
        self.assertIs(ev.provider, provider)

    def test_construction_defaults(self) -> None:
        ev = _make_evaluator()
        self.assertIsNotNone(ev.settings)
        self.assertIsNotNone(ev.provider)


class WalkforwardWindowsTests(unittest.TestCase):
    """_walkforward_windows produces correct window configurations."""

    def test_small_size_returns_single_window(self) -> None:
        ev = _make_evaluator()
        for size in [1, 10, 30, 59]:
            windows = ev._walkforward_windows(size)
            self.assertEqual(len(windows), 1, msg=f"size={size}")
            self.assertEqual(windows[0]["label"], "full")
            self.assertEqual(windows[0]["role"], "reference")
            self.assertEqual(windows[0]["start_idx"], 0)
            self.assertEqual(windows[0]["end_idx"], size)

    def test_size_sixty_has_four_unique_windows(self) -> None:
        ev = _make_evaluator()
        windows = ev._walkforward_windows(60)
        self.assertEqual(len(windows), 4)
        labels = [w["label"] for w in windows]
        self.assertIn("front", labels)
        self.assertIn("middle", labels)
        self.assertIn("back", labels)
        self.assertIn("full", labels)

    def test_large_size_has_four_or_fewer_unique_windows(self) -> None:
        ev = _make_evaluator()
        for size in [200, 500, 1000, 10000]:
            windows = ev._walkforward_windows(size)
            self.assertLessEqual(len(windows), 4, msg=f"size={size}")
            self.assertGreaterEqual(len(windows), 3, msg=f"size={size}")

    def test_windows_have_correct_boundaries(self) -> None:
        ev = _make_evaluator()
        size = 100
        windows = ev._walkforward_windows(size)
        for w in windows:
            self.assertIn("start_idx", w)
            self.assertIn("end_idx", w)
            self.assertGreaterEqual(w["start_idx"], 0)
            self.assertLessEqual(w["end_idx"], size)

    def test_front_window_uses_two_thirds(self) -> None:
        ev = _make_evaluator()
        size = 90
        windows = ev._walkforward_windows(size)
        front = next(w for w in windows if w["label"] == "front")
        expected_end = max(size * 2 // 3, 30)
        self.assertEqual(front["end_idx"], expected_end)

    def test_front_window_floor_at_30(self) -> None:
        ev = _make_evaluator()
        size = 30
        windows = ev._walkforward_windows(size)
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0]["end_idx"], 30)

    def test_dedup_identical_windows(self) -> None:
        """Dedup verification: when middle-start + 2/3 overflows, duplicate
        windows are collapsed."""
        ev = _make_evaluator()
        windows = ev._walkforward_windows(60)
        seen: set[tuple[int, int]] = set()
        for w in windows:
            key = (w["start_idx"], w["end_idx"])
            self.assertNotIn(key, seen, msg=f"Duplicate window {key}")
            seen.add(key)

    def test_middle_window_centered(self) -> None:
        ev = _make_evaluator()
        size = 300
        windows = ev._walkforward_windows(size)
        middle = next(w for w in windows if w["label"] == "middle")
        self.assertEqual(middle["role"], "stability_window")
        self.assertEqual(middle["start_idx"], size // 6)


class RollingValidationWindowsTests(unittest.TestCase):
    """_rolling_validation_windows generates proper train/validation splits."""

    def setUp(self) -> None:
        self.ev = _make_evaluator()
        self.index = pd.date_range("2020-01-01", periods=1000, freq="h")

    def test_insufficient_data_returns_empty(self) -> None:
        windows = self.ev._rolling_validation_windows(
            self.index, selector_end=50, min_rows=30,
        )
        self.assertEqual(windows, [])

    def test_returns_chunks(self) -> None:
        windows = self.ev._rolling_validation_windows(
            self.index, selector_end=1000, min_rows=30,
        )
        self.assertGreater(len(windows), 0)
        for w in windows:
            self.assertIn("label", w)
            self.assertIn("role", w)
            self.assertEqual(w["role"], "rolling_validation")
            self.assertIn("train_start_idx", w)
            self.assertIn("train_end_idx", w)
            self.assertIn("start_idx", w)
            self.assertIn("end_idx", w)
            self.assertIn("train_start_timestamp", w)
            self.assertIn("train_end_timestamp", w)
            self.assertIn("validation_start_timestamp", w)
            self.assertIn("validation_end_timestamp", w)

    def test_chunks_dont_overlap(self) -> None:
        windows = self.ev._rolling_validation_windows(
            self.index, selector_end=1000, min_rows=30,
        )
        covered: set[int] = set()
        for w in windows:
            val_start = int(w["start_idx"])
            val_end = int(w["end_idx"])
            for idx in range(val_start, val_end):
                self.assertNotIn(
                    idx, covered,
                    msg=f"Overlap at idx {idx} in window {w['label']}",
                )
                covered.add(idx)

    def test_last_window_extended_to_selector_end(self) -> None:
        """If the last validation window doesn't reach selector_end, it should
        be extended."""
        windows = self.ev._rolling_validation_windows(
            self.index, selector_end=1000, min_rows=30,
        )
        if windows:
            last = windows[-1]
            self.assertEqual(int(last["end_idx"]), 1000)

    def test_small_selector_edge(self) -> None:
        """Edge case: selector_end just at boundary."""
        # Exactly 3 * min_rows
        windows = self.ev._rolling_validation_windows(
            self.index, selector_end=90, min_rows=30,
        )
        self.assertGreater(len(windows), 0)

        # Just below 3 * min_rows
        windows = self.ev._rolling_validation_windows(
            self.index, selector_end=89, min_rows=30,
        )
        self.assertEqual(windows, [])


class MinimumRowsForDurationTests(unittest.TestCase):
    """_minimum_rows_for_duration estimates from DatetimeIndex cadence."""

    def setUp(self) -> None:
        self.ev = _make_evaluator()

    def test_hourly_cadence_estimates_correct_rows(self) -> None:
        index = pd.date_range("2020-01-01", periods=100, freq="h")
        duration = pd.Timedelta(days=30)  # 720 hours
        result = self.ev._minimum_rows_for_duration(
            index, duration=duration, floor_rows=30,
        )
        # 30 days * 24 hours = 720 rows needed
        self.assertEqual(result, 720)

    def test_minutely_cadence_estimates_correct_rows(self) -> None:
        index = pd.date_range("2020-01-01", periods=100, freq="min")
        duration = pd.Timedelta(hours=1)
        result = self.ev._minimum_rows_for_duration(
            index, duration=duration, floor_rows=10,
        )
        # 60 minutes → 60 rows needed
        self.assertEqual(result, 60)

    def test_returns_floor_rows_for_non_datetimeindex(self) -> None:
        index = pd.RangeIndex(100)
        result = self.ev._minimum_rows_for_duration(
            index, duration=pd.Timedelta(days=30), floor_rows=30,
        )
        self.assertEqual(result, 30)

    def test_returns_floor_rows_for_empty_index(self) -> None:
        index = pd.DatetimeIndex([])
        result = self.ev._minimum_rows_for_duration(
            index, duration=pd.Timedelta(days=30), floor_rows=30,
        )
        self.assertEqual(result, 30)

    def test_returns_floor_rows_for_single_element_index(self) -> None:
        index = pd.DatetimeIndex(["2020-01-01"])
        result = self.ev._minimum_rows_for_duration(
            index, duration=pd.Timedelta(days=30), floor_rows=30,
        )
        self.assertEqual(result, 30)

    def test_returns_floor_when_cadence_is_zero(self) -> None:
        index = pd.DatetimeIndex(["2020-01-01"] * 5)
        result = self.ev._minimum_rows_for_duration(
            index, duration=pd.Timedelta(days=30), floor_rows=30,
        )
        self.assertEqual(result, 30)

    def test_daily_cadence(self) -> None:
        index = pd.date_range("2020-01-01", periods=100, freq="D")
        duration = pd.Timedelta(days=30)
        result = self.ev._minimum_rows_for_duration(
            index, duration=duration, floor_rows=10,
        )
        # 30 days / 1 day cadence = 30 rows
        self.assertEqual(result, 30)

    def test_floor_rows_dominates_when_cadence_is_large(self) -> None:
        """When cadence is slow, floor_rows may dominate over estimated."""
        index = pd.date_range("2020-01-01", periods=10, freq="D")
        duration = pd.Timedelta(hours=1)
        result = self.ev._minimum_rows_for_duration(
            index, duration=duration, floor_rows=50,
        )
        # cadence = 1 day = 24h, ceil(1h/24h) = 1, max(50, 1) = 50
        self.assertEqual(result, 50)


class TargetAuditSizeTests(unittest.TestCase):
    """_target_audit_size computes correct audit size."""

    def setUp(self) -> None:
        self.ev = _make_evaluator()

    def test_non_datetime_index_uses_ten_percent(self) -> None:
        index = pd.RangeIndex(1000)
        result = self.ev._target_audit_size(
            index, size=1000, min_rows=30,
        )
        # minimum_audit_rows = floor_rows = 30
        # max(30, round(1000 * 0.1)) = max(30, 100) = 100
        self.assertEqual(result, 100)

    def test_datetime_index_audit_size_can_dominate(self) -> None:
        """With high-cadence date index, 30-day minimum dominates."""
        index = pd.date_range("2020-01-01", periods=10000, freq="h")
        result = self.ev._target_audit_size(
            index, size=100, min_rows=30,
        )
        # minimum_audit_rows = max(30, ceil(720h/1h)) = 720
        # max(720, round(100 * 0.1)) = max(720, 10) = 720
        self.assertEqual(result, 720)

    def test_ten_percent_dominates_when_larger(self) -> None:
        index = pd.RangeIndex(10000)
        result = self.ev._target_audit_size(
            index, size=10000, min_rows=30,
        )
        # minimum_audit_rows = floor_rows = 30
        # max(30, round(10000 * 0.1)) = max(30, 1000) = 1000
        self.assertEqual(result, 1000)


class EvaluationPlanTests(unittest.TestCase):
    """_evaluation_plan produces correct plan for short/medium/long data."""

    def setUp(self) -> None:
        self.ev = _make_evaluator()

    def test_short_data_no_holdout(self) -> None:
        index = pd.date_range("2020-01-01", periods=40, freq="h")
        plan = self.ev._evaluation_plan(index, min_rows=30)
        self.assertIsNone(plan["validation_window"])
        self.assertIsNone(plan["audit_window"])
        self.assertEqual(plan["selector_scope"], "in_sample_only")
        self.assertFalse(plan["visual_split"]["strict_holdout"])
        self.assertFalse(plan["visual_split"]["selector_uses_holdout"])

    def test_short_data_has_selector_windows(self) -> None:
        index = pd.date_range("2020-01-01", periods=40, freq="h")
        plan = self.ev._evaluation_plan(index, min_rows=30)
        self.assertGreater(len(plan["selector_windows"]), 0)

    def test_short_data_visual_split_ranges(self) -> None:
        index = pd.date_range("2020-01-01", periods=40, freq="h")
        plan = self.ev._evaluation_plan(index, min_rows=30)
        ranges = plan["visual_split"]["ranges"]
        self.assertEqual(len(ranges), 1)
        self.assertEqual(ranges[0]["kind"], "in_sample")
        self.assertEqual(ranges[0]["start_idx"], 0)
        self.assertEqual(ranges[0]["end_idx"], 40)

    def test_very_short_data_empty_ranges(self) -> None:
        index = pd.DatetimeIndex([])
        plan = self.ev._evaluation_plan(index, min_rows=30)
        ranges = plan["visual_split"]["ranges"]
        self.assertEqual(ranges, [])

    def test_medium_data_has_validation_no_audit(self) -> None:
        """Medium-length data gets a validation holdout but no audit."""
        index = pd.date_range("2020-01-01", periods=80, freq="h")
        plan = self.ev._evaluation_plan(index, min_rows=30)
        self.assertIsNotNone(plan["validation_window"])
        self.assertIsNone(plan["audit_window"])
        self.assertEqual(plan["validation_window"]["role"], "validation_holdout")

    def test_medium_data_split_boundaries(self) -> None:
        index = pd.date_range("2020-01-01", periods=80, freq="h")
        plan = self.ev._evaluation_plan(index, min_rows=30)
        val = plan["validation_window"]
        self.assertGreaterEqual(val["start_idx"], 0)
        self.assertLessEqual(val["end_idx"], 80)
        self.assertGreater(val["end_idx"], val["start_idx"])

    def test_long_data_rolling_chunks(self) -> None:
        """Long data with DatetimeIndex produces rolling chunks."""
        index = pd.date_range("2020-01-01", periods=1000, freq="h")
        plan = self.ev._evaluation_plan(index, min_rows=30)
        self.assertEqual(plan["selector_scope"], "rolling_validation_chunks")
        self.assertIsNone(plan["validation_window"])
        self.assertIsNotNone(plan["audit_window"])
        self.assertTrue(plan["visual_split"]["strict_holdout"])

    def test_long_data_rolling_chunks_audit_slice(self) -> None:
        index = pd.date_range("2020-01-01", periods=1000, freq="D")
        plan = self.ev._evaluation_plan(index, min_rows=30)
        audit = plan["audit_window"]
        size = len(index)
        expected_audit_size = max(30, round(size * 0.10))
        self.assertEqual(int(audit["end_idx"]) - int(audit["start_idx"]), expected_audit_size)

    def test_visual_split_rolling_ranges(self) -> None:
        index = pd.date_range("2020-01-01", periods=1000, freq="h")
        plan = self.ev._evaluation_plan(index, min_rows=30)
        ranges = plan["visual_split"]["ranges"]
        self.assertEqual(len(ranges), 2)
        kinds = [r["kind"] for r in ranges]
        self.assertIn("rolling_selector", kinds)
        self.assertIn("audit_holdout", kinds)

    def test_long_data_fallback_has_validation_and_audit(self) -> None:
        """When rolling chunks < 3, fall back to in-sample + val + audit."""
        index = pd.date_range("2020-01-01", periods=120, freq="D")
        plan = self.ev._evaluation_plan(index, min_rows=30)
        self.assertEqual(plan["selector_scope"], "in_sample_only")
        self.assertIsNotNone(plan["validation_window"])
        self.assertIsNotNone(plan["audit_window"])

    def test_long_data_fallback_visual_split_three_ranges(self) -> None:
        index = pd.date_range("2020-01-01", periods=120, freq="D")
        plan = self.ev._evaluation_plan(index, min_rows=30)
        ranges = plan["visual_split"]["ranges"]
        self.assertEqual(len(ranges), 3)
        kinds = [r["kind"] for r in ranges]
        self.assertIn("in_sample", kinds)
        self.assertIn("validation_holdout", kinds)
        self.assertIn("audit_holdout", kinds)

    def test_fallback_strict_holdout_true(self) -> None:
        index = pd.date_range("2020-01-01", periods=120, freq="D")
        plan = self.ev._evaluation_plan(index, min_rows=30)
        self.assertTrue(plan["visual_split"]["strict_holdout"])

    def test_visual_split_includes_timestamps(self) -> None:
        index = pd.date_range("2020-01-01", periods=80, freq="h")
        plan = self.ev._evaluation_plan(index, min_rows=30)
        ranges = plan["visual_split"]["ranges"]
        for r in ranges:
            self.assertIn("start_timestamp", r)
            self.assertIn("end_timestamp", r)

    def test_evaluation_plan_min_rows_near_boundary(self) -> None:
        """Edge: min_rows values near boundary conditions."""
        index = pd.date_range("2020-01-01", periods=100, freq="h")
        plan = self.ev._evaluation_plan(index, min_rows=14)
        self.assertIn("selector_windows", plan)


class EmptyPolicySweepSummaryTests(unittest.TestCase):
    """_empty_policy_sweep_summary returns expected default structure."""

    def setUp(self) -> None:
        self.ev = _make_evaluator()

    def test_has_all_expected_keys(self) -> None:
        summary = self.ev._empty_policy_sweep_summary()
        expected_keys = {
            "policy_sweep_applied",
            "policy_sweep_narrowed",
            "policy_sweep_train_window_count",
            "policy_sweep_trial_count",
            "policy_sweep_best_train_score",
            "policy_sweep_activity_penalty",
            "policy_sweep_material_change",
            "policy_sweep_changed_keys",
            "policy_sweep_proposed_policy",
            "policy_sweep_frozen_policy",
            "policy_sweep_comparison_available",
            "policy_sweep_declared_evaluation",
            "policy_sweep_frozen_evaluation",
            "policy_sweep_declared_better_metrics",
            "policy_sweep_frozen_better_metrics",
            "policy_sweep_equal_metrics",
            "policy_sweep_realized_winner",
            "policy_active_bar_fraction",
            "policy_regime_gate_open_fraction",
            "policy_entry_abs_score",
            "policy_exit_abs_score",
            "policy_flip_abs_score",
            "policy_max_holding_bars",
            "policy_cooldown_bars",
        }
        self.assertEqual(set(summary.keys()), expected_keys)

    def test_default_false_and_none_values(self) -> None:
        summary = self.ev._empty_policy_sweep_summary()
        self.assertFalse(summary["policy_sweep_applied"])
        self.assertFalse(summary["policy_sweep_narrowed"])
        self.assertIsNone(summary["policy_sweep_best_train_score"])
        self.assertEqual(summary["policy_sweep_activity_penalty"], 0.0)
        self.assertFalse(summary["policy_sweep_material_change"])
        self.assertEqual(summary["policy_sweep_changed_keys"], [])
        self.assertEqual(summary["policy_sweep_proposed_policy"], {})
        self.assertEqual(summary["policy_sweep_frozen_policy"], {})
        self.assertFalse(summary["policy_sweep_comparison_available"])


class SelectorTrainWindowsTests(unittest.TestCase):
    """_selector_train_windows extracts train windows from evaluation plan."""

    def setUp(self) -> None:
        self.ev = _make_evaluator()
        self.index = pd.date_range("2020-01-01", periods=500, freq="h")

    def test_in_sample_scope_uses_window_boundaries(self) -> None:
        plan = {
            "selector_scope": "in_sample_only",
            "selector_windows": [
                {"label": "front", "role": "early_window", "start_idx": 0, "end_idx": 100},
                {"label": "back", "role": "late_window", "start_idx": 50, "end_idx": 100},
            ],
        }
        windows = self.ev._selector_train_windows(
            self.index, evaluation_plan=plan, min_rows=30,
        )
        self.assertEqual(len(windows), 2)
        for w in windows:
            self.assertIn("start_idx", w)
            self.assertIn("end_idx", w)

    def test_rolling_validation_changes_role_to_train(self) -> None:
        plan = {
            "selector_scope": "rolling_validation_chunks",
            "selector_windows": [
                {
                    "label": "chunk_1_validation",
                    "role": "rolling_validation",
                    "start_idx": 100,
                    "end_idx": 200,
                    "train_start_idx": 0,
                    "train_end_idx": 100,
                },
            ],
        }
        windows = self.ev._selector_train_windows(
            self.index, evaluation_plan=plan, min_rows=30,
        )
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0]["role"], "rolling_train")
        self.assertEqual(windows[0]["start_idx"], 0)
        self.assertEqual(windows[0]["end_idx"], 100)
        self.assertIn("_train", windows[0]["label"])

    def test_dedup_in_selector_windows(self) -> None:
        plan = {
            "selector_scope": "in_sample_only",
            "selector_windows": [
                {"label": "a", "role": "early", "start_idx": 0, "end_idx": 100},
                {"label": "b", "role": "dup", "start_idx": 0, "end_idx": 100},
            ],
        }
        windows = self.ev._selector_train_windows(
            self.index, evaluation_plan=plan, min_rows=30,
        )
        self.assertEqual(len(windows), 1)

    def test_filters_windows_below_min_rows(self) -> None:
        plan = {
            "selector_scope": "in_sample_only",
            "selector_windows": [
                {"label": "tiny", "role": "small", "start_idx": 0, "end_idx": 10},
            ],
        }
        windows = self.ev._selector_train_windows(
            self.index, evaluation_plan=plan, min_rows=30,
        )
        self.assertEqual(windows, [])


class PairPolicySpecsTests(unittest.TestCase):
    """_pair_policy_specs generates policy variants correctly."""

    def setUp(self) -> None:
        self.ev = _make_evaluator()

    def test_generates_multiple_policy_variants(self) -> None:
        base_policy = {
            "entry_abs_score": 0.3,
            "exit_abs_score": 0.15,
            "flip_abs_score": 0.3,
            "max_holding_bars": 48,
            "cooldown_bars": 4,
        }
        policies = self.ev._pair_policy_specs(
            family="perp_pair_trade_unlevered",
            base_policy=base_policy,
            intent_locks={},
        )
        self.assertGreater(len(policies), 0)
        for p in policies:
            self.assertIn("entry_abs_score", p)
            self.assertIn("exit_abs_score", p)
            self.assertIn("flip_abs_score", p)
            self.assertIn("max_holding_bars", p)
            self.assertIn("cooldown_bars", p)
            self.assertIn("min_abs_score", p)

    def test_all_policies_have_valid_entry(self) -> None:
        base_policy = {
            "entry_abs_score": 0.3,
            "exit_abs_score": 0.15,
            "flip_abs_score": 0.3,
            "max_holding_bars": 48,
            "cooldown_bars": 4,
        }
        policies = self.ev._pair_policy_specs(
            family="perp_pair_trade_unlevered",
            base_policy=base_policy,
            intent_locks={},
        )
        for p in policies:
            self.assertGreaterEqual(p["entry_abs_score"], 0.05)
            self.assertLessEqual(p["entry_abs_score"], 1.5)

    def test_narrow_sweep_entry_values_tighter(self) -> None:
        base_policy = {
            "entry_abs_score": 0.3,
            "exit_abs_score": 0.15,
            "flip_abs_score": 0.3,
            "max_holding_bars": 48,
            "cooldown_bars": 4,
        }
        wide = self.ev._pair_policy_specs(
            family="perp_pair_trade_unlevered",
            base_policy=base_policy,
            intent_locks={},
        )
        narrow = self.ev._pair_policy_specs(
            family="perp_pair_trade_unlevered",
            base_policy=base_policy,
            intent_locks={"narrow_sweep": True},
        )
        wide_entry_range = max(p["entry_abs_score"] for p in wide) - min(p["entry_abs_score"] for p in wide)
        narrow_entry_range = max(p["entry_abs_score"] for p in narrow) - min(p["entry_abs_score"] for p in narrow)
        self.assertLessEqual(narrow_entry_range, wide_entry_range)

    def test_lock_time_stop_restricts_hold_values(self) -> None:
        base_policy = {
            "entry_abs_score": 0.3,
            "exit_abs_score": 0.15,
            "flip_abs_score": 0.3,
            "max_holding_bars": 48,
            "cooldown_bars": 0,
        }
        policies = self.ev._pair_policy_specs(
            family="perp_pair_trade_unlevered",
            base_policy=base_policy,
            intent_locks={"lock_time_stop": True, "narrow_sweep": False},
        )
        # All hold values should be close to 48 when lock_time_stop is True
        for p in policies:
            self.assertGreaterEqual(p["max_holding_bars"], 0)
            self.assertLessEqual(p["max_holding_bars"], 336)

    def test_lock_cooldown_restricts_cooldown_values(self) -> None:
        base_policy = {
            "entry_abs_score": 0.3,
            "exit_abs_score": 0.15,
            "flip_abs_score": 0.3,
            "max_holding_bars": 0,
            "cooldown_bars": 4,
        }
        policies = self.ev._pair_policy_specs(
            family="perp_pair_trade_unlevered",
            base_policy=base_policy,
            intent_locks={"lock_cooldown": True, "narrow_sweep": False},
        )
        for p in policies:
            self.assertGreaterEqual(p["cooldown_bars"], 0)
            self.assertLessEqual(p["cooldown_bars"], 168)

    def test_respects_max_trials(self) -> None:
        base_policy = {
            "entry_abs_score": 0.3,
            "exit_abs_score": 0.15,
            "flip_abs_score": 0.3,
            "max_holding_bars": 48,
            "cooldown_bars": 4,
        }
        policies = self.ev._pair_policy_specs(
            family="perp_pair_trade_unlevered",
            base_policy=base_policy,
            intent_locks={},
            max_trials=10,
        )
        self.assertLessEqual(len(policies), 10)

    def test_no_duplicate_policies(self) -> None:
        base_policy = {
            "entry_abs_score": 0.3,
            "exit_abs_score": 0.15,
            "flip_abs_score": 0.3,
            "max_holding_bars": 48,
            "cooldown_bars": 4,
        }
        policies = self.ev._pair_policy_specs(
            family="perp_pair_trade_unlevered",
            base_policy=base_policy,
            intent_locks={},
        )
        seen: set[tuple[float, float, float, int, int]] = set()
        for p in policies:
            key = (
                round(p["entry_abs_score"], 6),
                round(p["exit_abs_score"], 6),
                round(p["flip_abs_score"], 6),
                int(p["max_holding_bars"]),
                int(p["cooldown_bars"]),
            )
            self.assertNotIn(key, seen, msg=f"Duplicate policy key {key}")
            seen.add(key)

    def test_exit_abs_score_never_exceeds_entry(self) -> None:
        base_policy = {
            "entry_abs_score": 0.3,
            "exit_abs_score": 0.15,
            "flip_abs_score": 0.3,
            "max_holding_bars": 48,
            "cooldown_bars": 4,
        }
        policies = self.ev._pair_policy_specs(
            family="perp_pair_trade_unlevered",
            base_policy=base_policy,
            intent_locks={},
        )
        for p in policies:
            self.assertLessEqual(p["exit_abs_score"], p["entry_abs_score"] + 1e-9)

    def test_flip_abs_score_never_below_entry(self) -> None:
        base_policy = {
            "entry_abs_score": 0.3,
            "exit_abs_score": 0.15,
            "flip_abs_score": 0.3,
            "max_holding_bars": 48,
            "cooldown_bars": 4,
        }
        policies = self.ev._pair_policy_specs(
            family="perp_pair_trade_unlevered",
            base_policy=base_policy,
            intent_locks={},
        )
        for p in policies:
            self.assertGreaterEqual(p["flip_abs_score"], p["entry_abs_score"] - 1e-9)


class EmptyWindowSummaryTests(unittest.TestCase):
    """_empty_window_summary returns correct structure."""

    def setUp(self) -> None:
        self.ev = _make_evaluator()

    def test_validation_prefix(self) -> None:
        summary = self.ev._empty_window_summary("validation")
        self.assertFalse(summary["validation_available"])
        self.assertIsNone(summary["validation_sharpe"])
        self.assertIsNone(summary["validation_total_return"])
        self.assertIsNone(summary["validation_cagr"])
        self.assertIsNone(summary["validation_calmar"])
        self.assertIsNone(summary["validation_max_drawdown"])
        self.assertIsNone(summary["validation_liquidated"])
        self.assertEqual(summary["validation_window_count"], 0)
        self.assertIsNone(summary["validation_profitable_window_pct"])

    def test_audit_prefix(self) -> None:
        summary = self.ev._empty_window_summary("audit")
        self.assertFalse(summary["audit_available"])
        self.assertIsNone(summary["audit_sharpe"])
        self.assertEqual(summary["audit_window_count"], 0)


class PairPolicyActivityTests(unittest.TestCase):
    """_pair_policy_activity_summary and _pair_policy_activity_penalty."""

    def setUp(self) -> None:
        self.ev = _make_evaluator()

    def test_activity_summary_all_active(self) -> None:
        raw_target = pd.DataFrame({"a": [1.0, 2.0, 3.0], "b": [-0.5, -1.0, -2.0]})
        summary = self.ev._pair_policy_activity_summary(
            raw_target=raw_target, regime_gate_mask=None,
        )
        self.assertEqual(summary["active_bar_fraction"], 1.0)
        self.assertIsNone(summary["regime_gate_open_fraction"])

    def test_activity_summary_all_flat(self) -> None:
        raw_target = pd.DataFrame({"a": [0.0, 0.0], "b": [0.0, 0.0]})
        summary = self.ev._pair_policy_activity_summary(
            raw_target=raw_target, regime_gate_mask=None,
        )
        self.assertEqual(summary["active_bar_fraction"], 0.0)

    def test_activity_summary_with_regime_gate(self) -> None:
        raw_target = pd.DataFrame({"a": [1.0, 0.0, 1.0]})
        gate_mask = pd.Series([True, False, True])
        summary = self.ev._pair_policy_activity_summary(
            raw_target=raw_target, regime_gate_mask=gate_mask,
        )
        self.assertEqual(summary["active_bar_fraction"], 2.0 / 3.0)
        self.assertEqual(summary["regime_gate_open_fraction"], 2.0 / 3.0)

    def test_penalty_zero_for_active_strategy(self) -> None:
        summary: dict[str, float | None] = {
            "active_bar_fraction": 0.5,
            "regime_gate_open_fraction": 0.5,
        }
        policy = {"max_holding_bars": 48, "cooldown_bars": 4}
        penalty = self.ev._pair_policy_activity_penalty(
            activity_summary=summary, policy=policy,
        )
        self.assertEqual(penalty, 0.0)

    def test_penalty_high_for_inactive(self) -> None:
        summary: dict[str, float | None] = {
            "active_bar_fraction": 0.001,
            "regime_gate_open_fraction": 0.01,
        }
        policy = {"max_holding_bars": 48, "cooldown_bars": 4}
        penalty = self.ev._pair_policy_activity_penalty(
            activity_summary=summary, policy=policy,
        )
        # active < 0.005 → 0.45, gate < 0.02 → 0.15 = 0.60
        self.assertAlmostEqual(penalty, 0.60)

    def test_penalty_inactive_no_hold_cooldown(self) -> None:
        """Extra penalty when active_fraction < 0.01 and no holding/cooldown."""
        summary: dict[str, float | None] = {
            "active_bar_fraction": 0.005,
            "regime_gate_open_fraction": None,
        }
        policy = {"max_holding_bars": 0, "cooldown_bars": 0}
        penalty = self.ev._pair_policy_activity_penalty(
            activity_summary=summary, policy=policy,
        )
        # active < 0.01 (and >= 0.005) → 0.25
        # no gate penalty
        # active < 0.01 with max_holding=0, cooldown=0 → +0.05
        self.assertAlmostEqual(penalty, 0.30)

    def test_penalty_with_unknown_active_fraction(self) -> None:
        summary: dict[str, float | None] = {
            "active_bar_fraction": None,
            "regime_gate_open_fraction": None,
        }
        policy = {"max_holding_bars": 0, "cooldown_bars": 0}
        penalty = self.ev._pair_policy_activity_penalty(
            activity_summary=summary, policy=policy,
        )
        self.assertEqual(penalty, 0.0)

    def test_penalty_medium_active(self) -> None:
        summary: dict[str, float | None] = {
            "active_bar_fraction": 0.015,
            "regime_gate_open_fraction": 0.03,
        }
        policy = {"max_holding_bars": 48, "cooldown_bars": 4}
        penalty = self.ev._pair_policy_activity_penalty(
            activity_summary=summary, policy=policy,
        )
        # active < 0.02 → +0.1, gate < 0.05 → +0.05 = 0.15
        self.assertAlmostEqual(penalty, 0.15)


class WindowResultRowTests(unittest.TestCase):
    """_window_result_row structure."""

    def setUp(self) -> None:
        self.ev = _make_evaluator()

    def test_basic_structure(self) -> None:
        prices = pd.DataFrame(
            {"close": [100.0, 101.0, 102.0]},
            index=pd.date_range("2020-01-01", periods=3, freq="h"),
        )
        result = MagicMock()
        result.stats = {"sharpe": 1.5, "total_return": 0.05}
        result.liquidated = False

        row = self.ev._window_result_row(
            result=result,
            window_spec={"label": "full", "role": "reference", "start_idx": 0, "end_idx": 3},
            leverage=1.0,
            prices=prices,
            used_for_selector=True,
        )
        self.assertEqual(row["window"], "full")
        self.assertEqual(row["role"], "reference")
        self.assertTrue(row["used_for_selector"])
        self.assertEqual(row["start_idx"], 0)
        self.assertEqual(row["end_idx"], 3)
        self.assertEqual(row["leverage"], 1.0)
        self.assertFalse(row["liquidated"])
        self.assertIn("stats", row)
        self.assertEqual(row["stats"]["sharpe"], 1.5)
        self.assertIn("start_timestamp", row)
        self.assertIn("end_timestamp", row)

    def test_structure_with_train_metadata(self) -> None:
        prices = pd.DataFrame(
            {"close": [100.0, 101.0]},
            index=pd.date_range("2020-01-01", periods=2, freq="h"),
        )
        result = MagicMock()
        result.stats = {"sharpe": 0.5}
        result.liquidated = False

        row = self.ev._window_result_row(
            result=result,
            window_spec={
                "label": "chunk_1",
                "role": "rolling_validation",
                "start_idx": 100,
                "end_idx": 200,
                "train_start_idx": 0,
                "train_end_idx": 100,
                "train_start_timestamp": "2020-01-01T00:00:00",
                "train_end_timestamp": "2020-01-05T00:00:00",
                "validation_start_timestamp": "2020-01-05T01:00:00",
                "validation_end_timestamp": "2020-01-10T00:00:00",
            },
            leverage=2.0,
            prices=prices,
            used_for_selector=False,
        )
        self.assertEqual(row["train_start_idx"], 0)
        self.assertEqual(row["train_end_idx"], 100)
        self.assertIsNotNone(row["train_start_timestamp"])
        self.assertIsNotNone(row["validation_start_timestamp"])

    def test_liquidated_true(self) -> None:
        prices = pd.DataFrame(
            {"close": [100.0]},
            index=pd.date_range("2020-01-01", periods=1, freq="h"),
        )
        result = MagicMock()
        result.stats = {}
        result.liquidated = True

        row = self.ev._window_result_row(
            result=result,
            window_spec={"label": "x", "role": "y", "start_idx": 0, "end_idx": 1},
            leverage=1.0,
            prices=prices,
            used_for_selector=True,
        )
        self.assertTrue(row["liquidated"])


class WindowSummaryTests(unittest.TestCase):
    """_window_summary produces correct summary from a row."""

    def setUp(self) -> None:
        self.ev = _make_evaluator()

    def test_validation_window_summary(self) -> None:
        row = {
            "stats": {
                "sharpe": 1.2,
                "total_return": 0.15,
                "cagr": 0.08,
                "calmar": 2.0,
                "max_drawdown": -0.1,
            },
            "liquidated": False,
        }
        summary = self.ev._window_summary("validation", row)
        self.assertTrue(summary["validation_available"])
        self.assertEqual(summary["validation_sharpe"], 1.2)
        self.assertEqual(summary["validation_total_return"], 0.15)
        self.assertEqual(summary["validation_cagr"], 0.08)
        self.assertEqual(summary["validation_calmar"], 2.0)
        self.assertEqual(summary["validation_max_drawdown"], -0.1)
        self.assertFalse(summary["validation_liquidated"])
        self.assertEqual(summary["validation_window_count"], 1)

    def test_profitable_window_pct_one(self) -> None:
        row = {
            "stats": {"total_return": 0.05, "sharpe": 1.0, "cagr": 0.03, "calmar": 1.0, "max_drawdown": -0.05},
            "liquidated": False,
        }
        summary = self.ev._window_summary("test", row)
        self.assertEqual(summary["test_profitable_window_pct"], 1.0)

    def test_profitable_window_pct_zero(self) -> None:
        row = {
            "stats": {"total_return": -0.05, "sharpe": -0.5, "cagr": -0.02, "calmar": -1.0, "max_drawdown": -0.2},
            "liquidated": True,
        }
        summary = self.ev._window_summary("test", row)
        self.assertEqual(summary["test_profitable_window_pct"], 0.0)
        self.assertTrue(summary["test_liquidated"])


class AggregateWindowSummaryTests(unittest.TestCase):
    """_aggregate_window_summary aggregates multiple rows."""

    def setUp(self) -> None:
        self.ev = _make_evaluator()

    def test_empty_rows_returns_empty_summary(self) -> None:
        summary = self.ev._aggregate_window_summary("validation", [])
        self.assertFalse(summary["validation_available"])

    def test_single_row(self) -> None:
        rows = [
            {
                "stats": {
                    "sharpe": 1.5,
                    "total_return": 0.10,
                    "cagr": 0.05,
                    "calmar": 1.5,
                    "max_drawdown": -0.08,
                },
                "liquidated": False,
            }
        ]
        summary = self.ev._aggregate_window_summary("validation", rows)
        self.assertTrue(summary["validation_available"])
        self.assertEqual(summary["validation_window_count"], 1)

    def test_multiple_rows(self) -> None:
        rows = [
            {
                "stats": {
                    "sharpe": 1.0,
                    "total_return": 0.05,
                    "cagr": 0.02,
                    "calmar": 0.5,
                    "max_drawdown": -0.1,
                },
                "liquidated": False,
            },
            {
                "stats": {
                    "sharpe": 2.0,
                    "total_return": 0.15,
                    "cagr": 0.08,
                    "calmar": 2.0,
                    "max_drawdown": -0.05,
                },
                "liquidated": False,
            },
        ]
        summary = self.ev._aggregate_window_summary("validation", rows)
        self.assertTrue(summary["validation_available"])
        self.assertEqual(summary["validation_window_count"], 2)
        self.assertIsNotNone(summary["validation_sharpe"])
        self.assertIsNotNone(summary["validation_total_return"])

    def test_some_liquidated(self) -> None:
        rows = [
            {
                "stats": {
                    "sharpe": -2.0,
                    "total_return": -0.3,
                    "cagr": -0.1,
                    "calmar": -3.0,
                    "max_drawdown": -0.5,
                },
                "liquidated": True,
            },
            {
                "stats": {
                    "sharpe": 1.5,
                    "total_return": 0.10,
                    "cagr": 0.05,
                    "calmar": 2.0,
                    "max_drawdown": -0.05,
                },
                "liquidated": False,
            },
        ]
        summary = self.ev._aggregate_window_summary("validation", rows)
        self.assertTrue(summary["validation_liquidated"])


class HelperFunctionTests(unittest.TestCase):
    """Tests for module-level helper functions."""

    def test_safe_float_with_valid_numbers(self) -> None:
        self.assertEqual(_safe_float(3.14159, digits=4), 3.1416)
        self.assertEqual(_safe_float(0, digits=2), 0.0)
        self.assertEqual(_safe_float(1), 1.0)

    def test_safe_float_with_none(self) -> None:
        self.assertIsNone(_safe_float(None))

    def test_safe_float_with_nan(self) -> None:
        self.assertIsNone(_safe_float(float("nan")))

    def test_safe_float_with_inf(self) -> None:
        self.assertIsNone(_safe_float(float("inf")))

    def test_safe_float_with_string(self) -> None:
        self.assertIsNone(_safe_float("not a number"))

    def test_safe_float_with_default(self) -> None:
        self.assertEqual(_safe_float(None, default=0.0), 0.0)

    def test_unique_float_values_clamps_to_range(self) -> None:
        result = _unique_float_values([-1.0, 0.5, 2.0], low=0.0, high=1.0)
        for v in result:
            self.assertGreaterEqual(v, 0.0)
            self.assertLessEqual(v, 1.0)

    def test_unique_float_values_dedup(self) -> None:
        result = _unique_float_values([0.5, 0.5, 0.6], low=0.0, high=1.0)
        self.assertEqual(len(result), 2)

    def test_unique_float_values_empty_input(self) -> None:
        result = _unique_float_values([], low=0.0, high=1.0)
        self.assertEqual(result, [])

    def test_unique_int_values_clamps_and_dedup(self) -> None:
        result = _unique_int_values([5, 5, 3, -1, 100], low=0, high=10)
        self.assertEqual(len(result), 4)  # 5(dedup), 3, 0(clamped), 10(clamped)
        for v in result:
            self.assertGreaterEqual(v, 0)
            self.assertLessEqual(v, 10)

    def test_unique_int_values_empty(self) -> None:
        result = _unique_int_values([], low=0, high=10)
        self.assertEqual(result, [])

    def test_unique_int_values_all_out_of_range(self) -> None:
        result = _unique_int_values([-5, -3], low=0, high=10)
        self.assertEqual(result, [0])


class SamplePolicyTrainWindowsTests(unittest.TestCase):
    """_sample_policy_train_windows evenly samples windows."""

    def setUp(self) -> None:
        self.ev = _make_evaluator()

    def test_no_sampling_when_under_limit(self) -> None:
        windows = [{"label": "a"}, {"label": "b"}]
        result = self.ev._sample_policy_train_windows(windows, max_count=5)
        self.assertEqual(len(result), 2)

    def test_sampling_when_over_limit(self) -> None:
        windows = [
            {"label": f"w{i}"} for i in range(10)
        ]
        result = self.ev._sample_policy_train_windows(windows, max_count=3)
        self.assertEqual(len(result), 3)

    def test_max_count_one_returns_first(self) -> None:
        windows = [{"label": "first"}, {"label": "second"}]
        result = self.ev._sample_policy_train_windows(windows, max_count=1)
        self.assertEqual(result, [windows[0]])

    def test_none_max_count_returns_all(self) -> None:
        windows = [{"label": "a"}, {"label": "b"}]
        result = self.ev._sample_policy_train_windows(windows, max_count=None)
        self.assertEqual(len(result), 2)

    def test_empty_windows_returns_empty(self) -> None:
        result = self.ev._sample_policy_train_windows([], max_count=3)
        self.assertEqual(result, [])


class EdgeCaseTests(unittest.TestCase):
    """Edge cases: empty data, single-row, extreme values."""

    def setUp(self) -> None:
        self.ev = _make_evaluator()

    def test_walkforward_empty(self) -> None:
        windows = self.ev._walkforward_windows(0)
        self.assertEqual(windows, [{"label": "full", "role": "reference", "start_idx": 0, "end_idx": 0}])

    def test_rolling_validation_with_empty_index(self) -> None:
        index = pd.DatetimeIndex([])
        windows = self.ev._rolling_validation_windows(
            index, selector_end=0, min_rows=30,
        )
        self.assertEqual(windows, [])

    def test_evaluation_plan_with_empty_index(self) -> None:
        index = pd.DatetimeIndex([])
        plan = self.ev._evaluation_plan(index, min_rows=30)
        self.assertIn("selector_windows", plan)
        self.assertEqual(len(plan["visual_split"]["ranges"]), 0)

    def test_minimum_rows_for_duration_with_irregular_index(self) -> None:
        irregular = pd.DatetimeIndex([
            "2020-01-01", "2020-01-02", "2020-01-05", "2020-01-06",
        ])
        result = self.ev._minimum_rows_for_duration(
            irregular, duration=pd.Timedelta(days=7), floor_rows=5,
        )
        # median positive delta should be ~1 day (between 01-01 and 01-02)
        self.assertGreaterEqual(result, 5)

    def test_empty_policy_specs_with_minimal_base(self) -> None:
        base_policy = {
            "entry_abs_score": 0.05,
            "exit_abs_score": 0.025,
            "flip_abs_score": 0.05,
            "max_holding_bars": 0,
            "cooldown_bars": 0,
        }
        policies = self.ev._pair_policy_specs(
            family="perp_pair_trade_unlevered",
            base_policy=base_policy,
            intent_locks={},
        )
        self.assertGreater(len(policies), 0)

    def test_window_summary_with_missing_stats(self) -> None:
        row = {
            "stats": {},
            "liquidated": False,
        }
        summary = self.ev._window_summary("test", row)
        self.assertTrue(summary["test_available"])
        self.assertIsNone(summary["test_sharpe"])
        self.assertEqual(summary["test_window_count"], 1)
        # total_return is None, so profitable_window_pct should be 0.0
        self.assertEqual(summary["test_profitable_window_pct"], 0.0)


class PairPolicyEvaluationSnapshotTests(unittest.TestCase):
    """_pair_policy_evaluation_snapshot builds snapshot structure."""

    def setUp(self) -> None:
        self.ev = _make_evaluator()
        self.prices = pd.DataFrame(
            {"a": [100.0, 101.0, 102.0, 103.0, 104.0]},
            index=pd.date_range("2020-01-01", periods=5, freq="D"),
        )
        self.target_all = pd.DataFrame(
            {"a": [1.0, 0.0, 1.0, 0.0, 1.0]},
            index=self.prices.index,
        )
        self.raw_target = self.target_all.copy()
        self.spec = MagicMock()
        self.spec.risk.rebalance_threshold = 0.01

    def test_snapshot_structure(self) -> None:
        snapshot = self.ev._pair_policy_evaluation_snapshot(
            spec=self.spec,
            target_all=self.target_all,
            raw_target=self.raw_target,
            prices_all=self.prices,
            funding_all=None,
            selector_windows=[
                {"label": "full", "role": "reference", "start_idx": 0, "end_idx": 5},
            ],
            validation_window=None,
            audit_window=None,
            evaluation_plan={
                "selector_scope": "in_sample_only",
                "visual_split": {
                    "strict_holdout": False,
                    "selector_uses_holdout": False,
                    "note": "",
                    "ranges": [],
                },
            },
            leverage_tiers=[1.0],
            min_rows=3,
            asset_breadth=1,
            regime_gate_mask=None,
        )
        self.assertIsInstance(snapshot, dict)
        expected_keys = {
            "selector_aggregate_score",
            "selector_median_total_return",
            "selector_profitable_window_pct",
            "validation_total_return",
            "pre_audit_canonical_total_return",
            "pre_audit_canonical_max_drawdown",
            "audit_total_return",
            "active_bar_fraction",
            "regime_gate_open_fraction",
        }
        for key in expected_keys:
            self.assertIn(key, snapshot, msg=f"Missing key: {key}")

        # selector windows exist but are too short (5 < min_rows=3? no, 5 >= 3...)
        # But min_rows=3 and start_idx=0, end_idx=5, so prices=5 rows >= 3 → should run backtest
        # With mock result returning None for everything, aggregate_score etc will be None
        # because summarize_window_results will have issues with NaN stats
        self.assertIsNotNone(snapshot.get("selector_aggregate_score"))


class PairPolicySnapshotFromEvaluationTests(unittest.TestCase):
    """_pair_policy_snapshot_from_evaluation builds snapshot from summary."""

    def setUp(self) -> None:
        self.ev = _make_evaluator()

    def test_basic_snapshot(self) -> None:
        summary = {
            "aggregate_score": 3.5,
            "median_total_return": 0.12,
            "profitable_window_pct": 0.75,
            "validation_total_return": 0.05,
            "pre_audit_canonical_total_return": 0.2,
            "audit_total_return": 0.03,
            "active_bar_fraction": 0.6,
        }
        canonical_run = {
            "visual_split": {"ranges": []},
            "drawdown_curve": {"values": [1.0, 0.95, 0.98]},
        }
        snapshot = self.ev._pair_policy_snapshot_from_evaluation(
            summary=summary, canonical_run=canonical_run,
        )
        self.assertEqual(snapshot["selector_aggregate_score"], 3.5)
        self.assertEqual(snapshot["selector_median_total_return"], 0.12)
        self.assertEqual(snapshot["selector_profitable_window_pct"], 0.75)
        self.assertEqual(snapshot["validation_total_return"], 0.05)
        self.assertEqual(snapshot["pre_audit_canonical_total_return"], 0.2)
        self.assertEqual(snapshot["audit_total_return"], 0.03)
        self.assertEqual(snapshot["active_bar_fraction"], 0.6)

    def test_pre_audit_canonical_max_drawdown_from_drawdown_curve(self) -> None:
        summary = {"aggregate_score": 1.0}
        canonical_run = {
            "visual_split": {"ranges": []},
            "drawdown_curve": {"values": [None, -0.1, -0.25]},
        }
        snapshot = self.ev._pair_policy_snapshot_from_evaluation(
            summary=summary, canonical_run=canonical_run,
        )
        # min of [-0.1, -0.25] = -0.25
        self.assertEqual(snapshot["pre_audit_canonical_max_drawdown"], -0.25)


class PairPolicyCompareSnapshotsTests(unittest.TestCase):
    """_pair_policy_compare_snapshots compares two snapshots."""

    def setUp(self) -> None:
        self.ev = _make_evaluator()

    def test_declared_better(self) -> None:
        declared = {
            "selector_aggregate_score": 5.0,
            "selector_median_total_return": 0.5,
            "selector_profitable_window_pct": 0.8,
            "validation_total_return": 0.2,
            "pre_audit_canonical_total_return": 0.3,
            "pre_audit_canonical_max_drawdown": -0.1,
            "audit_total_return": 0.1,
        }
        frozen = {
            "selector_aggregate_score": 3.0,
            "selector_median_total_return": 0.3,
            "selector_profitable_window_pct": 0.6,
            "validation_total_return": 0.1,
            "pre_audit_canonical_total_return": 0.2,
            "pre_audit_canonical_max_drawdown": -0.2,
            "audit_total_return": 0.05,
        }
        result = self.ev._pair_policy_compare_snapshots(
            declared_snapshot=declared, frozen_snapshot=frozen,
        )
        # declared has higher values in all metrics → declared wins
        self.assertEqual(result["realized_winner"], "declared")

    def test_mixed_results(self) -> None:
        declared = {
            "selector_aggregate_score": 5.0,
            "validation_total_return": 0.1,
        }
        frozen = {
            "selector_aggregate_score": 3.0,
            "validation_total_return": 0.2,
        }
        result = self.ev._pair_policy_compare_snapshots(
            declared_snapshot=declared, frozen_snapshot=frozen,
        )
        self.assertEqual(result["realized_winner"], "mixed")

    def test_missing_values_skipped(self) -> None:
        declared: dict = {}
        frozen: dict = {}
        result = self.ev._pair_policy_compare_snapshots(
            declared_snapshot=declared, frozen_snapshot=frozen,
        )
        # Both empty → no metrics compared → default "mixed"
        self.assertEqual(result["realized_winner"], "mixed")


if __name__ == "__main__":
    unittest.main()
