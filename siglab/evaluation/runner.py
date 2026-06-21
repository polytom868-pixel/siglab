from __future__ import annotations

import math
from typing import Any

import pandas as pd

from siglab.data.feeds import MarketDataProvider
from siglab.evaluator.compile import (  # via shim for mock compat
    PAIR_STATEFUL_POLICY_SCHEMA,
    _build_pair_trade_positions,
)
from siglab.evaluator.gates import evaluate_gates  # via shim for mock compat
from siglab.evaluator.score import serialize_stats, summarize_window_results
from siglab.schemas import SignalSpec
from siglab.config import SiglabConfig
from siglab.evaluator.backtesting import BacktestConfig  # via shim for mock compat

from siglab.utils import safe_float as _safe_float

# Import from extracted sub-modules
from siglab.evaluation.runner_regime import (
    _pair_regime_diagnostics,
    _pair_regime_state,
    _pair_trade_episodes_with_regime,
)
from siglab.evaluation.runner_serialize import (
    _policy_summary_spec,
    _serialize_canonical_run,
    _serialize_window_ranges,
    _unique_float_values,
    _unique_int_values,
)
from siglab.evaluation.runner_analysis import (
    _pre_audit_context_pack,
    _pre_audit_drawdown_pack,
)
from siglab.evaluation.runner_utils import (
    _pre_audit_end_idx,
    _series_has_finite_values,
    _series_last_value,
    _series_min_value,
    _series_total_return,
)

# Lazy wrappers so unittest.mock.patch("siglab.evaluator.core.compile_spec") works.
async def _lazy_compile_spec(settings: Any, provider: Any, spec: Any) -> Any:
    from siglab.evaluator.core import compile_spec as _fn
    return await _fn(settings, provider, spec)


def _lazy_run_backtest(prices: Any, target_weights: Any, config: Any) -> Any:
    from siglab.evaluator.core import run_backtest as _fn
    return _fn(prices, target_weights, config)


class ResearchEvaluator:
    def __init__(
        self,
        settings: SiglabConfig,
        provider: MarketDataProvider,
    ) -> None:
        self.settings = settings
        self.provider = provider

    async def evaluate(
        self,
        spec: SignalSpec,
        *,
        fast_mode: bool = False,
    ) -> dict[str, Any]:
        spec = SignalSpec.from_dict(spec.canonical_dict())
        compiled = await _lazy_compile_spec(self.settings, self.provider, spec)
        prices_all = compiled.prices.sort_index()
        if len(prices_all.index) > 1:
            prices_all = prices_all.iloc[:-1]
        funding_all = None
        if compiled.funding_rates is not None:
            funding_all = compiled.funding_rates.reindex(prices_all.index).ffill().fillna(0.0)

        compiled.metadata["signal_timing"] = "next_bar"
        compiled.metadata["bias_controls"] = {
            "signal_timing": "next_bar",
            "dropped_last_bar": len(prices_all.index) < len(compiled.prices.index),
            "position_shift_bars": 1,
            "leak_checks_passed": False,
        }

        min_rows = max(14, min(30, len(prices_all.index) // 2))
        evaluation_plan = self._evaluation_plan(prices_all.index, min_rows=min_rows)
        selector_windows = evaluation_plan["selector_windows"]
        validation_window = evaluation_plan.get("validation_window")
        audit_window = evaluation_plan.get("audit_window")
        leverage_tiers = sorted(
            {1.0, min(2.0, spec.risk.max_leverage), spec.risk.max_leverage}
        )
        compiled.metadata["evaluation_split"] = evaluation_plan["visual_split"]
        compiled.metadata["selector_scope"] = str(
            evaluation_plan.get("selector_scope", "in_sample_only")
        )
        compiled.metadata["selector_uses_holdout"] = bool(
            evaluation_plan["visual_split"].get("selector_uses_holdout")
        )
        compiled.metadata["selector_window_count"] = len(selector_windows)

        target_unshifted = compiled.target_positions.reindex(prices_all.index).ffill().fillna(0.0)
        declared_policy_target_unshifted: pd.DataFrame | None = None
        policy_sweep_summary = self._empty_policy_sweep_summary()
        pair_policy_compatible = (
            compiled.metadata.get("policy_schema") == PAIR_STATEFUL_POLICY_SCHEMA
            or (
                spec.family in {"perp_pair_trade_unlevered", "perp_pair_trade_levered"}
                and compiled.metadata.get("asset_1_symbol")
                and compiled.metadata.get("asset_2_symbol")
            )
        )
        if pair_policy_compatible and compiled.signal_score is not None:
            target_unshifted, policy_sweep_summary, declared_policy_target_unshifted = self._pair_target_with_policy_sweep(
                spec=spec,
                compiled=compiled,
                prices_all=prices_all,
                funding_all=funding_all,
                evaluation_plan=evaluation_plan,
                min_rows=min_rows,
                max_trials=18 if fast_mode else None,
                max_train_windows=2 if fast_mode else None,
            )
        target_all = target_unshifted.shift(1).fillna(0.0)

        # Compute actual leak checks from data properties rather than hardcoding True
        has_valid_positions = not target_unshifted.isna().all().all()
        no_extreme_positions = not bool((target_unshifted.abs() > 1e10).any().any())
        shift_applied = bool(compiled.metadata["bias_controls"].get("position_shift_bars", 0) >= 1)
        compiled.metadata["bias_controls"]["leak_checks_passed"] = (
            shift_applied and has_valid_positions and no_extreme_positions
        )

        selector_results: list[dict[str, Any]] = []
        window_results: list[dict[str, Any]] = []
        for leverage in leverage_tiers:
            for window_spec in selector_windows:
                start_idx = int(window_spec["start_idx"])
                end_idx = int(window_spec["end_idx"])
                prices = prices_all.iloc[start_idx:end_idx]
                if len(prices) < min_rows:
                    continue
                target = target_all.reindex(prices.index).ffill().fillna(0.0)
                funding = funding_all.reindex(prices.index).ffill().fillna(0.0) if funding_all is not None else None
                config = BacktestConfig(
                    leverage=leverage,
                    funding_rates=funding,
                    rebalance_threshold=spec.risk.rebalance_threshold,
                    enable_liquidation=True,
                )
                result = _lazy_run_backtest(prices, target, config)
                row = self._window_result_row(
                    result=result,
                    window_spec=window_spec,
                    leverage=leverage,
                    prices=prices,
                    used_for_selector=True,
                )
                selector_results.append(row)
                window_results.append(row)

        selector_uses_validation_chunks = (
            str(evaluation_plan.get("selector_scope")) == "rolling_validation_chunks"
        )
        validation_summary = self._empty_window_summary("validation")
        if selector_uses_validation_chunks:
            leverage_one_selector_rows = [
                row for row in selector_results if math.isclose(float(row["leverage"]), 1.0)
            ]
            validation_summary = self._aggregate_window_summary(
                "validation",
                leverage_one_selector_rows or selector_results,
            )
        if validation_window is not None:
            validation_summary = self._run_summary_window(
                window_spec=validation_window,
                prices_all=prices_all,
                target_all=target_all,
                funding_all=funding_all,
                spec=spec,
                min_rows=min_rows,
                window_results=window_results,
                summary_prefix="validation",
            )
        audit_summary = self._empty_window_summary("audit")
        if audit_window is not None:
            audit_summary = self._run_summary_window(
                window_spec=audit_window,
                prices_all=prices_all,
                target_all=target_all,
                funding_all=funding_all,
                spec=spec,
                min_rows=min_rows,
                window_results=window_results,
                summary_prefix="audit",
            )

        if not selector_results:
            raise ValueError("Evaluator could not build any valid walk-forward windows")

        pair_regime_state = (
            _pair_regime_state(
                prices=prices_all,
                target_weights=target_all,
                funding_rates=funding_all,
            )
            if str(compiled.metadata.get("diagnostic_adapter") or "") in {"perp_cross_sectional", "cross_sectional"}
            else {"available": False}
        )
        canonical_run = self._canonical_full_run(
            prices_all=prices_all,
            target_all=target_all,
            funding_all=funding_all,
            spec=spec,
            visual_split=evaluation_plan["visual_split"],
            regime_state=pair_regime_state,
            evaluation_windows=selector_windows
            + ([validation_window] if validation_window is not None else [])
            + ([audit_window] if audit_window is not None else [])
            + [
                {
                    "label": "reference_full",
                    "role": "reference_full",
                    "start_idx": 0,
                    "end_idx": len(prices_all.index),
                }
            ],
        )
        canonical_series_valid = _series_has_finite_values(canonical_run.get("equity_curve"))
        pre_audit_end_idx = _pre_audit_end_idx(
            evaluation_plan["visual_split"],
            canonical_run.get("equity_curve"),
        )
        summary_pre_audit_canonical_total_return = _series_total_return(
            canonical_run.get("equity_curve"),
            end_idx=pre_audit_end_idx,
        )
        summary_pre_audit_canonical_end_equity = _series_last_value(
            canonical_run.get("equity_curve"),
            end_idx=pre_audit_end_idx,
        )
        summary_pre_audit_canonical_max_drawdown = _series_min_value(
            canonical_run.get("drawdown_curve"),
            end_idx=pre_audit_end_idx,
        )
        canonical_run["pre_audit_drawdown_pack"] = _pre_audit_drawdown_pack(
            canonical_run=canonical_run,
            target_weights=target_all,
            signal_score=compiled.signal_score,
            signal_components=compiled.signal_components,
            end_idx=pre_audit_end_idx,
        )
        canonical_run["pre_audit_context_pack"] = _pre_audit_context_pack(
            canonical_run=canonical_run,
            target_weights=target_all,
            signal_score=compiled.signal_score,
            signal_components=compiled.signal_components,
            compiled_metadata=compiled.metadata,
            regime_gate_mask=compiled.regime_gate_mask,
            regime_state=pair_regime_state,
            end_idx=pre_audit_end_idx,
        )

        summary = summarize_window_results(
            window_results=selector_results,
            asset_breadth=int(compiled.metadata.get("asset_breadth", 0)),
        )
        summary.update(validation_summary)
        summary.update(audit_summary)
        if validation_summary.get("validation_available"):
            summary["holdout_available"] = validation_summary["validation_available"]
            summary["holdout_sharpe"] = validation_summary["validation_sharpe"]
            summary["holdout_total_return"] = validation_summary["validation_total_return"]
            summary["holdout_cagr"] = validation_summary["validation_cagr"]
            summary["holdout_calmar"] = validation_summary["validation_calmar"]
            summary["holdout_max_drawdown"] = validation_summary["validation_max_drawdown"]
            summary["holdout_liquidated"] = validation_summary["validation_liquidated"]
        else:
            summary["holdout_available"] = False
            summary["holdout_sharpe"] = None
            summary["holdout_total_return"] = None
            summary["holdout_cagr"] = None
            summary["holdout_calmar"] = None
            summary["holdout_max_drawdown"] = None
            summary["holdout_liquidated"] = None
        summary["canonical_series_valid"] = bool(canonical_series_valid)
        summary["pre_audit_canonical_total_return"] = summary_pre_audit_canonical_total_return
        summary["pre_audit_canonical_end_equity"] = summary_pre_audit_canonical_end_equity
        summary["pre_audit_canonical_max_drawdown"] = summary_pre_audit_canonical_max_drawdown
        summary["strict_holdout"] = bool(evaluation_plan["visual_split"]["strict_holdout"])
        summary["selector_scope"] = str(evaluation_plan.get("selector_scope", "in_sample_only"))
        summary["selector_uses_holdout"] = bool(
            evaluation_plan["visual_split"].get("selector_uses_holdout")
        )
        gate_diagnostics_summary = (
            (canonical_run.get("pre_audit_context_pack") or {}).get("gate_diagnostics") or {}
        )
        summary["active_bar_fraction"] = gate_diagnostics_summary.get("active_bar_fraction")
        summary["entry_signal_bar_fraction"] = gate_diagnostics_summary.get(
            "entry_signal_bar_fraction"
        )
        summary["score_alignment_when_active"] = gate_diagnostics_summary.get(
            "score_alignment_when_active"
        )
        summary["gate_bottleneck_tags"] = (
            gate_diagnostics_summary.get("bottleneck_tags") or []
        )[:6]
        summary.update(policy_sweep_summary)
        if (
            declared_policy_target_unshifted is not None
            and bool(summary.get("policy_sweep_applied"))
            and bool(summary.get("policy_sweep_material_change"))
        ):
            declared_target_all = declared_policy_target_unshifted.shift(1).fillna(0.0)
            declared_snapshot = self._pair_policy_evaluation_snapshot(
                spec=spec,
                target_all=declared_target_all,
                raw_target=declared_policy_target_unshifted,
                prices_all=prices_all,
                funding_all=funding_all,
                selector_windows=selector_windows,
                validation_window=validation_window,
                audit_window=audit_window,
                evaluation_plan=evaluation_plan,
                leverage_tiers=leverage_tiers,
                min_rows=min_rows,
                asset_breadth=int(compiled.metadata.get("asset_breadth", 0)),
                regime_gate_mask=compiled.regime_gate_mask,
            )
            frozen_snapshot = self._pair_policy_snapshot_from_evaluation(
                summary=summary,
                canonical_run=canonical_run,
            )
            comparison = self._pair_policy_compare_snapshots(
                declared_snapshot=declared_snapshot,
                frozen_snapshot=frozen_snapshot,
            )
            compiled.metadata["pair_policy_sweep"] = {
                **(compiled.metadata.get("pair_policy_sweep") or {}),
                "declared_evaluation": declared_snapshot,
                "frozen_evaluation": frozen_snapshot,
                "comparison": comparison,
            }
            summary.update(
                {
                    "policy_sweep_comparison_available": True,
                    "policy_sweep_declared_evaluation": declared_snapshot,
                    "policy_sweep_frozen_evaluation": frozen_snapshot,
                    "policy_sweep_declared_better_metrics": comparison["declared_better_metrics"],
                    "policy_sweep_frozen_better_metrics": comparison["frozen_better_metrics"],
                    "policy_sweep_equal_metrics": comparison["equal_metrics"],
                    "policy_sweep_realized_winner": comparison["realized_winner"],
                }
            )
        passed, gate_reasons = evaluate_gates(spec.track, summary)
        summary["passed"] = passed
        summary["gate_reasons"] = gate_reasons

        return {
            "spec": spec.canonical_dict(),
            "spec_hash": spec.strategy_hash(),
            "summary": summary,
            "windows": window_results,
            "compiled_metadata": compiled.metadata,
            "canonical_run": canonical_run,
        }

    def _run_summary_window(
        self,
        *,
        window_spec: dict[str, Any],
        prices_all: pd.DataFrame,
        target_all: pd.DataFrame,
        funding_all: pd.Series | None,
        spec: SignalSpec,
        min_rows: int,
        window_results: list[dict[str, Any]],
        summary_prefix: str,
    ) -> dict[str, Any]:
        prices = prices_all.iloc[
            int(window_spec["start_idx"]): int(window_spec["end_idx"])
        ]
        if len(prices) < min_rows:
            return self._empty_window_summary(summary_prefix)
        target = target_all.reindex(prices.index).ffill().fillna(0.0)
        funding = (
            funding_all.reindex(prices.index).ffill().fillna(0.0)
            if funding_all is not None
            else None
        )
        config = BacktestConfig(
            leverage=1.0,
            funding_rates=funding,
            rebalance_threshold=spec.risk.rebalance_threshold,
            enable_liquidation=True,
        )
        result = _lazy_run_backtest(prices, target, config)
        row = self._window_result_row(
            result=result,
            window_spec=window_spec,
            leverage=1.0,
            prices=prices,
            used_for_selector=False,
        )
        window_results.append(row)
        return self._window_summary(summary_prefix, row)

    def _walkforward_windows(self, size: int) -> list[dict[str, Any]]:
        if size < 60:
            return [
                {
                    "label": "full",
                    "role": "reference",
                    "start_idx": 0,
                    "end_idx": size,
                }
            ]

        windows = [
            {
                "label": "front",
                "role": "early_window",
                "start_idx": 0,
                "end_idx": max(size * 2 // 3, 30),
            },
            {
                "label": "middle",
                "role": "stability_window",
                "start_idx": size // 6,
                "end_idx": min(size, size // 6 + size * 2 // 3),
            },
            {
                "label": "back",
                "role": "late_window",
                "start_idx": size // 3,
                "end_idx": size,
            },
            {
                "label": "full",
                "role": "reference",
                "start_idx": 0,
                "end_idx": size,
            },
        ]
        deduped: list[dict[str, Any]] = []
        seen: set[tuple[int, int]] = set()
        for window_spec in windows:
            start_idx_val = window_spec["start_idx"]
            end_idx_val = window_spec["end_idx"]
            assert isinstance(start_idx_val, int) and isinstance(end_idx_val, int)
            start_idx: int = start_idx_val
            end_idx: int = end_idx_val
            key = (start_idx, end_idx)
            if key in seen:
                continue
            deduped.append(window_spec)
            seen.add(key)
        return deduped

    def _rolling_validation_windows(
        self,
        index: pd.Index,
        *,
        selector_end: int,
        min_rows: int,
    ) -> list[dict[str, Any]]:
        selector_size = selector_end
        if selector_size < (3 * min_rows):
            return []

        validation_size = max(min_rows, selector_size // 6)
        train_size = max(2 * validation_size, selector_size // 3)
        if train_size + validation_size > selector_size:
            train_size = selector_size - validation_size
        if train_size < min_rows or validation_size < min_rows:
            return []

        windows: list[dict[str, Any]] = []
        chunk_index = 1
        train_start = 0
        while train_start + train_size + validation_size <= selector_end:
            train_end = train_start + train_size
            validation_start = train_end
            validation_end = validation_start + validation_size
            windows.append(
                {
                    "label": f"chunk_{chunk_index}_validation",
                    "role": "rolling_validation",
                    "start_idx": validation_start,
                    "end_idx": validation_end,
                    "train_start_idx": train_start,
                    "train_end_idx": train_end,
                    "train_start_timestamp": index[train_start].isoformat(),
                    "train_end_timestamp": index[train_end - 1].isoformat(),
                    "validation_start_timestamp": index[validation_start].isoformat(),
                    "validation_end_timestamp": index[validation_end - 1].isoformat(),
                }
            )
            train_start += validation_size
            chunk_index += 1
        if windows and int(windows[-1]["end_idx"]) < selector_end:
            windows[-1]["end_idx"] = selector_end
            windows[-1]["validation_end_timestamp"] = index[selector_end - 1].isoformat()
        return windows

    def _minimum_rows_for_duration(
        self,
        index: pd.Index,
        *,
        duration: pd.Timedelta,
        floor_rows: int,
    ) -> int:
        if not isinstance(index, pd.DatetimeIndex) or len(index) < 2:
            return floor_rows
        deltas = index.to_series().diff().dropna()
        positive_deltas = deltas[deltas > pd.Timedelta(0)]
        if positive_deltas.empty:
            return floor_rows
        cadence = positive_deltas.median()
        if pd.isna(cadence) or cadence <= pd.Timedelta(0):
            return floor_rows
        return max(floor_rows, int(math.ceil(duration / cadence)))

    def _target_audit_size(
        self,
        index: pd.Index,
        *,
        size: int,
        min_rows: int,
    ) -> int:
        minimum_audit_rows = self._minimum_rows_for_duration(
            index,
            duration=pd.Timedelta(days=30),
            floor_rows=min_rows,
        )
        return max(minimum_audit_rows, int(round(size * 0.10)))

    def _evaluation_plan(
        self,
        index: pd.Index,
        *,
        min_rows: int,
    ) -> dict[str, Any]:
        size = len(index)
        if size < max(2 * min_rows, 30):
            return {
                "selector_windows": self._walkforward_windows(size),
                "validation_window": None,
                "audit_window": None,
                "selector_scope": "in_sample_only",
                "visual_split": {
                    "strict_holdout": False,
                    "selector_uses_holdout": False,
                    "note": (
                        "The sample is too short to reserve a strict holdout. "
                        "SigLab is using all available data for in-sample scoring."
                    ),
                    "ranges": (
                        [
                            {
                                "label": "In-Sample Only",
                                "kind": "in_sample",
                                "start_idx": 0,
                                "end_idx": size,
                                "start_timestamp": index[0].isoformat(),
                                "end_timestamp": index[-1].isoformat(),
                            }
                        ]
                        if size > 0
                        else []
                    ),
                },
            }

        target_audit_size = self._target_audit_size(index, size=size, min_rows=min_rows)
        if size < max(3 * min_rows, 45) or target_audit_size > size - (2 * min_rows):
            holdout_size = max(min_rows, size // 4)
            holdout_size = min(holdout_size, size - min_rows)
            split_idx = max(min_rows, size - holdout_size)
            return {
                "selector_windows": self._walkforward_windows(split_idx),
                "validation_window": {
                    "label": "validation_holdout",
                    "role": "validation_holdout",
                    "start_idx": split_idx,
                    "end_idx": size,
                },
                "audit_window": None,
                "selector_scope": "in_sample_only",
                "visual_split": {
                    "strict_holdout": False,
                    "selector_uses_holdout": False,
                    "note": (
                        "The sample is too short for a separate validation holdout plus the "
                        "minimum 30-day audit slice. SigLab is reserving a single validation "
                        "holdout only."
                    ),
                    "ranges": [
                        {
                            "label": "In-Sample",
                            "kind": "in_sample",
                            "start_idx": 0,
                            "end_idx": split_idx,
                            "start_timestamp": index[0].isoformat(),
                            "end_timestamp": index[split_idx - 1].isoformat(),
                        },
                        {
                            "label": "Validation",
                            "kind": "validation_holdout",
                            "start_idx": split_idx,
                            "end_idx": size,
                            "start_timestamp": index[split_idx].isoformat(),
                            "end_timestamp": index[-1].isoformat(),
                        },
                    ],
                },
            }

        audit_size = target_audit_size
        audit_start = size - audit_size
        selector_windows = self._rolling_validation_windows(
            index,
            selector_end=audit_start,
            min_rows=min_rows,
        )
        if len(selector_windows) >= 3:
            return {
                "selector_windows": selector_windows,
                "validation_window": None,
                "audit_window": {
                    "label": "audit_holdout",
                    "role": "audit_holdout",
                    "start_idx": audit_start,
                    "end_idx": size,
                },
                "selector_scope": "rolling_validation_chunks",
                "visual_split": {
                    "strict_holdout": True,
                    "selector_uses_holdout": True,
                    "note": (
                        "SigLab now scores specs on rolling train-validation chunks across "
                        "the pre-audit history and preserves the final audit slice as the untouched "
                        "out-of-sample check."
                    ),
                    "ranges": [
                        {
                            "label": "Rolling Selection Zone",
                            "kind": "rolling_selector",
                            "start_idx": 0,
                            "end_idx": audit_start,
                            "start_timestamp": index[0].isoformat(),
                            "end_timestamp": index[audit_start - 1].isoformat(),
                        },
                        {
                            "label": "Audit",
                            "kind": "audit_holdout",
                            "start_idx": audit_start,
                            "end_idx": size,
                            "start_timestamp": index[audit_start].isoformat(),
                            "end_timestamp": index[-1].isoformat(),
                        },
                    ],
                },
            }

        remaining = size - audit_size
        validation_size = max(min_rows, remaining // 4)
        validation_size = min(validation_size, remaining - min_rows)
        validation_start = max(min_rows, size - audit_size - validation_size)
        audit_start = size - audit_size
        return {
            "selector_windows": self._walkforward_windows(validation_start),
            "validation_window": {
                "label": "validation_holdout",
                "role": "validation_holdout",
                "start_idx": validation_start,
                "end_idx": audit_start,
            },
            "audit_window": {
                "label": "audit_holdout",
                "role": "audit_holdout",
                "start_idx": audit_start,
                "end_idx": size,
            },
            "selector_scope": "in_sample_only",
            "visual_split": {
                "strict_holdout": True,
                "selector_uses_holdout": False,
                "note": (
                    "SigLab now scores on the in-sample slice only, monitors a validation holdout "
                    "during search, and preserves the final audit slice as the untouched out-of-sample check."
                ),
                "ranges": [
                    {
                        "label": "In-Sample",
                        "kind": "in_sample",
                        "start_idx": 0,
                        "end_idx": validation_start,
                        "start_timestamp": index[0].isoformat(),
                        "end_timestamp": index[validation_start - 1].isoformat(),
                    },
                    {
                        "label": "Validation",
                        "kind": "validation_holdout",
                        "start_idx": validation_start,
                        "end_idx": audit_start,
                        "start_timestamp": index[validation_start].isoformat(),
                        "end_timestamp": index[audit_start - 1].isoformat(),
                    },
                    {
                        "label": "Audit",
                        "kind": "audit_holdout",
                        "start_idx": audit_start,
                        "end_idx": size,
                        "start_timestamp": index[audit_start].isoformat(),
                        "end_timestamp": index[-1].isoformat(),
                    },
                ],
            },
        }

    def _empty_policy_sweep_summary(self) -> dict[str, Any]:
        return {
            "policy_sweep_applied": False,
            "policy_sweep_narrowed": False,
            "policy_sweep_train_window_count": 0,
            "policy_sweep_trial_count": 0,
            "policy_sweep_best_train_score": None,
            "policy_sweep_activity_penalty": 0.0,
            "policy_sweep_material_change": False,
            "policy_sweep_changed_keys": [],
            "policy_sweep_proposed_policy": {},
            "policy_sweep_frozen_policy": {},
            "policy_sweep_comparison_available": False,
            "policy_sweep_declared_evaluation": {},
            "policy_sweep_frozen_evaluation": {},
            "policy_sweep_declared_better_metrics": [],
            "policy_sweep_frozen_better_metrics": [],
            "policy_sweep_equal_metrics": [],
            "policy_sweep_realized_winner": None,
            "policy_active_bar_fraction": None,
            "policy_regime_gate_open_fraction": None,
            "policy_entry_abs_score": None,
            "policy_exit_abs_score": None,
            "policy_flip_abs_score": None,
            "policy_max_holding_bars": None,
            "policy_cooldown_bars": None,
        }

    def _selector_train_windows(
        self,
        index: pd.Index,
        *,
        evaluation_plan: dict[str, Any],
        min_rows: int,
    ) -> list[dict[str, Any]]:
        windows: list[dict[str, Any]] = []
        seen: set[tuple[int, int]] = set()
        selector_scope = str(evaluation_plan.get("selector_scope", "in_sample_only"))
        for window in evaluation_plan.get("selector_windows") or []:
            if selector_scope == "rolling_validation_chunks":
                start_idx = int(window.get("train_start_idx", 0))
                end_idx = int(window.get("train_end_idx", 0))
                role = "rolling_train"
                label = str(window.get("label") or "rolling_validation").replace(
                    "_validation",
                    "_train",
                )
            else:
                start_idx = int(window.get("start_idx", 0))
                end_idx = int(window.get("end_idx", 0))
                role = str(window.get("role") or "selector_train")
                label = str(window.get("label") or "selector_train")
            if end_idx - start_idx < min_rows:
                continue
            key = (start_idx, end_idx)
            if key in seen:
                continue
            seen.add(key)
            windows.append(
                {
                    "label": label,
                    "role": role,
                    "start_idx": start_idx,
                    "end_idx": end_idx,
                }
            )
        return windows

    def _pair_policy_specs(
        self,
        *,
        family: str,
        base_policy: dict[str, Any],
        intent_locks: dict[str, Any],
        max_trials: int | None = None,
    ) -> list[dict[str, Any]]:
        base_entry = max(0.05, float(base_policy["entry_abs_score"]))
        narrow_sweep = bool(intent_locks.get("narrow_sweep"))
        base_exit_ratio = (
            float(base_policy["exit_abs_score"]) / base_entry
            if base_entry > 0.0
            else 0.5
        )
        base_flip_ratio = (
            float(base_policy["flip_abs_score"]) / base_entry
            if base_entry > 0.0
            else 1.0
        )
        if narrow_sweep:
            entry_values = _unique_float_values(
                [
                    max(0.05, base_entry - 0.03),
                    base_entry,
                    min(1.5, base_entry + 0.03),
                    base_entry * 0.9,
                    base_entry * 1.1,
                ],
                low=0.05,
                high=1.5,
            )
            exit_ratios = _unique_float_values(
                [
                    max(0.1, base_exit_ratio * 0.9),
                    base_exit_ratio,
                    min(1.0, base_exit_ratio * 1.1),
                ],
                low=0.05,
                high=1.0,
            )
            flip_ratios = _unique_float_values(
                [
                    max(1.0, base_flip_ratio * 0.95),
                    base_flip_ratio,
                    min(1.5, base_flip_ratio * 1.05),
                ],
                low=1.0,
                high=2.5,
            )
        else:
            entry_values = _unique_float_values(
                [base_entry * 0.75, base_entry, max(base_entry + 0.05, base_entry * 1.25)],
                low=0.05,
                high=1.5,
            )
            exit_ratios = [0.4, 0.75]
            flip_ratios = [1.0, 1.35]

        base_hold = int(base_policy["max_holding_bars"])
        if bool(intent_locks.get("lock_time_stop")) and base_hold > 0:
            hold_values = _unique_int_values(
                [max(0, base_hold - 12), base_hold, min(24 * 14, base_hold + 12)],
                low=0,
                high=24 * 14,
            )
        elif narrow_sweep:
            hold_anchor = 24 if family == "perp_pair_trade_levered" else 48
            hold_values = _unique_int_values(
                [base_hold, max(0, base_hold - hold_anchor // 2), base_hold + hold_anchor // 2],
                low=0,
                high=24 * 14,
            )
        else:
            hold_values = _unique_int_values(
                [
                    base_hold,
                    0,
                    24 if family == "perp_pair_trade_levered" else 48,
                    72,
                ],
                low=0,
                high=24 * 14,
            )

        base_cooldown = int(base_policy["cooldown_bars"])
        if bool(intent_locks.get("lock_cooldown")) and base_cooldown > 0:
            cooldown_values = _unique_int_values(
                [max(0, base_cooldown - 2), base_cooldown, min(24 * 7, base_cooldown + 2)],
                low=0,
                high=24 * 7,
            )
        elif narrow_sweep:
            cooldown_values = _unique_int_values(
                [base_cooldown, max(0, base_cooldown - 2), base_cooldown + 2, 4],
                low=0,
                high=24 * 7,
            )
        else:
            cooldown_values = _unique_int_values(
                [base_cooldown, 0, 4, 8],
                low=0,
                high=24 * 7,
            )

        policies: list[dict[str, Any]] = []
        seen: set[tuple[Any, ...]] = set()
        for entry_abs_score in entry_values:
            for exit_ratio in exit_ratios:
                exit_abs_score = max(0.0, min(entry_abs_score, entry_abs_score * exit_ratio))
                for flip_ratio in flip_ratios:
                    flip_abs_score = max(entry_abs_score, min(2.5, entry_abs_score * flip_ratio))
                    for max_holding_bars in hold_values:
                        for cooldown_bars in cooldown_values:
                            policy = {
                                **base_policy,
                                "entry_abs_score": entry_abs_score,
                                "exit_abs_score": exit_abs_score,
                                "flip_abs_score": flip_abs_score,
                                "max_holding_bars": max_holding_bars,
                                "cooldown_bars": cooldown_bars,
                                "min_abs_score": entry_abs_score,
                            }
                            key = (
                                round(policy["entry_abs_score"], 6),
                                round(policy["exit_abs_score"], 6),
                                round(policy["flip_abs_score"], 6),
                                int(policy["max_holding_bars"]),
                                int(policy["cooldown_bars"]),
                            )
                            if key in seen:
                                continue
                            seen.add(key)
                            policies.append(policy)
        if max_trials is not None and len(policies) > max_trials:
            base_entry_score = float(base_policy["entry_abs_score"]) or 0.05
            base_exit = float(base_policy["exit_abs_score"]) or 0.05
            base_flip = float(base_policy["flip_abs_score"]) or max(base_entry_score, 0.05)
            base_hold = int(base_policy["max_holding_bars"])
            base_cooldown = int(base_policy["cooldown_bars"])

            def _distance(policy: dict[str, Any]) -> tuple[float, float]:
                score = (
                    abs(float(policy["entry_abs_score"]) - base_entry_score) / max(abs(base_entry_score), 0.05)
                    + abs(float(policy["exit_abs_score"]) - base_exit) / max(abs(base_exit), 0.05)
                    + abs(float(policy["flip_abs_score"]) - base_flip) / max(abs(base_flip), 0.05)
                    + abs(int(policy["max_holding_bars"]) - base_hold) / 24.0
                    + abs(int(policy["cooldown_bars"]) - base_cooldown) / 4.0
                )
                hold_penalty = abs(int(policy["max_holding_bars"]) - base_hold)
                return (score, hold_penalty)

            policies = sorted(policies, key=_distance)[:max_trials]
        return policies

    def _pair_target_with_policy_sweep(
        self,
        *,
        spec: SignalSpec,
        compiled: Any,
        prices_all: pd.DataFrame,
        funding_all: pd.DataFrame | None,
        evaluation_plan: dict[str, Any],
        min_rows: int,
        max_trials: int | None = None,
        max_train_windows: int | None = None,
    ) -> tuple[pd.DataFrame, dict[str, Any], pd.DataFrame]:
        signal_score = compiled.signal_score.reindex(prices_all.index).ffill().fillna(0.0)
        regime_gate_mask = (
            compiled.regime_gate_mask.reindex(prices_all.index).ffill().fillna(False).astype(bool)
            if compiled.regime_gate_mask is not None
            else None
        )
        exit_on_regime_break = bool(
            (compiled.metadata.get("regime_gates") or {}).get("exit_on_break", True)
        )
        base_policy = {
            "gross_target": float(compiled.metadata.get("gross_target", 1.0)),
            "max_gross_target": float(
                compiled.metadata.get("max_gross_target", compiled.metadata.get("gross_target", 1.0))
            ),
            "entry_abs_score": float(
                compiled.metadata.get("entry_abs_score", compiled.metadata.get("min_abs_score", 0.0))
            ),
            "exit_abs_score": float(
                compiled.metadata.get(
                    "exit_abs_score",
                    max(0.0, float(compiled.metadata.get("entry_abs_score", 0.0)) * 0.5),
                )
            ),
            "flip_abs_score": float(
                compiled.metadata.get(
                    "flip_abs_score",
                    compiled.metadata.get("entry_abs_score", compiled.metadata.get("min_abs_score", 0.0)),
                )
            ),
            "max_holding_bars": int(compiled.metadata.get("max_holding_bars", 0)),
            "cooldown_bars": int(compiled.metadata.get("cooldown_bars", 0)),
            "signal_leverage_scale": float(compiled.metadata.get("signal_leverage_scale", 0.75)),
            "min_abs_score": float(
                compiled.metadata.get("entry_abs_score", compiled.metadata.get("min_abs_score", 0.0))
            ),
        }
        declared_raw_target = _build_pair_trade_positions(
            signal_score,
            asset_1_symbol=str(compiled.metadata["asset_1_symbol"]),
            asset_2_symbol=str(compiled.metadata["asset_2_symbol"]),
            gross_target=float(base_policy["gross_target"]),
            max_gross_target=float(base_policy["max_gross_target"]),
            max_asset_weight=spec.risk.max_asset_weight,
            entry_abs_score=float(base_policy["entry_abs_score"]),
            exit_abs_score=float(base_policy["exit_abs_score"]),
            flip_abs_score=float(base_policy["flip_abs_score"]),
            max_holding_bars=int(base_policy["max_holding_bars"]),
            cooldown_bars=int(base_policy["cooldown_bars"]),
            signal_leverage_scale=float(base_policy["signal_leverage_scale"]),
            regime_gate_mask=regime_gate_mask,
            exit_on_regime_break=exit_on_regime_break,
        )
        regime_gates = compiled.metadata.get("regime_gates") or {}
        intent_locks = {
            "narrow_sweep": bool(regime_gates.get("entry"))
            or int(base_policy["max_holding_bars"]) > 0
            or int(base_policy["cooldown_bars"]) > 0,
            "lock_time_stop": int(base_policy["max_holding_bars"]) > 0,
            "lock_cooldown": int(base_policy["cooldown_bars"]) > 0,
            "regime_gate_count": len(regime_gates.get("entry") or []),
        }

        train_windows = self._selector_train_windows(
            prices_all.index,
            evaluation_plan=evaluation_plan,
            min_rows=min_rows,
        )
        train_windows = self._sample_policy_train_windows(
            train_windows,
            max_count=max_train_windows,
        )
        best_policy = dict(base_policy)
        best_summary: dict[str, Any] | None = None
        trial_count = 0

        if train_windows:
            for policy in self._pair_policy_specs(
                family=spec.family,
                base_policy=base_policy,
                intent_locks=intent_locks,
                max_trials=max_trials,
            ):
                trial_count += 1
                raw_target = _build_pair_trade_positions(
                    signal_score,
                    asset_1_symbol=str(compiled.metadata["asset_1_symbol"]),
                    asset_2_symbol=str(compiled.metadata["asset_2_symbol"]),
                    gross_target=float(policy["gross_target"]),
                    max_gross_target=float(policy["max_gross_target"]),
                    max_asset_weight=spec.risk.max_asset_weight,
                    entry_abs_score=float(policy["entry_abs_score"]),
                    exit_abs_score=float(policy["exit_abs_score"]),
                    flip_abs_score=float(policy["flip_abs_score"]),
                    max_holding_bars=int(policy["max_holding_bars"]),
                    cooldown_bars=int(policy["cooldown_bars"]),
                    signal_leverage_scale=float(policy["signal_leverage_scale"]),
                    regime_gate_mask=regime_gate_mask,
                    exit_on_regime_break=exit_on_regime_break,
                )
                shifted_target = raw_target.shift(1).fillna(0.0)
                rows: list[dict[str, Any]] = []
                for window_spec in train_windows:
                    start_idx = int(window_spec["start_idx"])
                    end_idx = int(window_spec["end_idx"])
                    prices = prices_all.iloc[start_idx:end_idx]
                    if len(prices) < min_rows:
                        continue
                    target = shifted_target.reindex(prices.index).ffill().fillna(0.0)
                    funding = (
                        funding_all.reindex(prices.index).ffill().fillna(0.0)
                        if funding_all is not None
                        else None
                    )
                    result = _lazy_run_backtest(
                        prices,
                        target,
                        BacktestConfig(
                            leverage=1.0,
                            funding_rates=funding,
                            rebalance_threshold=spec.risk.rebalance_threshold,
                            enable_liquidation=True,
                        ),
                    )
                    rows.append(
                        self._window_result_row(
                            result=result,
                            window_spec=window_spec,
                            leverage=1.0,
                            prices=prices,
                            used_for_selector=True,
                        )
                    )
                if not rows:
                    continue
                aggregate = summarize_window_results(
                    window_results=rows,
                    asset_breadth=int(compiled.metadata.get("asset_breadth", 0)),
                )
                activity_summary = self._pair_policy_activity_summary(
                    raw_target=raw_target,
                    regime_gate_mask=regime_gate_mask,
                )
                activity_penalty = self._pair_policy_activity_penalty(
                    activity_summary=activity_summary,
                    policy=policy,
                )
                changed_param_count = sum(
                    1 for key in ("entry_abs_score", "exit_abs_score", "flip_abs_score",
                                   "max_holding_bars", "cooldown_bars")
                    if key in policy and key in base_policy
                    and not math.isclose(float(policy.get(key, 0)), float(base_policy.get(key, 0)),
                                         rel_tol=0.05, abs_tol=0.01)
                )
                complexity_penalty = 0.02 * changed_param_count
                rank = (
                    float(aggregate.get("aggregate_score") or -1e9) - activity_penalty - complexity_penalty,
                    float(aggregate.get("median_total_return") or -1e9),
                    float(aggregate.get("profitable_window_pct") or -1e9),
                    -activity_penalty,
                    -abs(float(policy["entry_abs_score"]) - float(base_policy["entry_abs_score"])),
                )
                if best_summary is not None:
                    best_changed_count = sum(
                        1 for key in ("entry_abs_score", "exit_abs_score", "flip_abs_score",
                                       "max_holding_bars", "cooldown_bars")
                        if key in best_policy and key in base_policy
                        and not math.isclose(float(best_policy.get(key, 0)), float(base_policy.get(key, 0)),
                                             rel_tol=0.05, abs_tol=0.01)
                    )
                    best_complexity_penalty = 0.02 * best_changed_count
                else:
                    best_complexity_penalty = 0.0
                best_rank = (
                    float(best_summary.get("aggregate_score") or -1e9)
                    - float(best_summary.get("activity_penalty") or 0.0) - best_complexity_penalty,
                    float(best_summary.get("median_total_return") or -1e9),
                    float(best_summary.get("profitable_window_pct") or -1e9),
                    -float(best_summary.get("activity_penalty") or 0.0),
                    -abs(float(best_policy["entry_abs_score"]) - float(base_policy["entry_abs_score"])),
                ) if best_summary is not None else None
                if best_rank is None or rank > best_rank:
                    best_policy = dict(policy)
                    best_summary = {
                        **aggregate,
                        "activity_penalty": activity_penalty,
                        "complexity_penalty": complexity_penalty,
                        "activity_summary": activity_summary,
                    }

        raw_target = _build_pair_trade_positions(
            signal_score,
            asset_1_symbol=str(compiled.metadata["asset_1_symbol"]),
            asset_2_symbol=str(compiled.metadata["asset_2_symbol"]),
            gross_target=float(best_policy["gross_target"]),
            max_gross_target=float(best_policy["max_gross_target"]),
            max_asset_weight=spec.risk.max_asset_weight,
            entry_abs_score=float(best_policy["entry_abs_score"]),
            exit_abs_score=float(best_policy["exit_abs_score"]),
            flip_abs_score=float(best_policy["flip_abs_score"]),
            max_holding_bars=int(best_policy["max_holding_bars"]),
            cooldown_bars=int(best_policy["cooldown_bars"]),
            signal_leverage_scale=float(best_policy["signal_leverage_scale"]),
            regime_gate_mask=regime_gate_mask,
            exit_on_regime_break=exit_on_regime_break,
        )

        spec.params["gross_target"] = float(best_policy["gross_target"])
        spec.params["max_gross_target"] = float(best_policy["max_gross_target"])
        spec.params["entry_abs_score"] = float(best_policy["entry_abs_score"])
        spec.params["exit_abs_score"] = float(best_policy["exit_abs_score"])
        spec.params["flip_abs_score"] = float(best_policy["flip_abs_score"])
        spec.params["max_holding_bars"] = int(best_policy["max_holding_bars"])
        spec.params["cooldown_bars"] = int(best_policy["cooldown_bars"])
        spec.params["signal_leverage_scale"] = float(best_policy["signal_leverage_scale"])
        spec.params["min_abs_score"] = float(best_policy["entry_abs_score"])

        compiled.metadata["gross_target"] = float(best_policy["gross_target"])
        compiled.metadata["max_gross_target"] = float(best_policy["max_gross_target"])
        compiled.metadata["entry_abs_score"] = float(best_policy["entry_abs_score"])
        compiled.metadata["exit_abs_score"] = float(best_policy["exit_abs_score"])
        compiled.metadata["flip_abs_score"] = float(best_policy["flip_abs_score"])
        compiled.metadata["max_holding_bars"] = int(best_policy["max_holding_bars"])
        compiled.metadata["cooldown_bars"] = int(best_policy["cooldown_bars"])
        compiled.metadata["min_abs_score"] = float(best_policy["entry_abs_score"])
        compiled.metadata["signal_leverage_scale"] = float(best_policy["signal_leverage_scale"])
        compiled.metadata["pair_policy_sweep"] = {
            "applied": bool(train_windows),
            "narrowed": bool(intent_locks["narrow_sweep"]),
            "train_window_count": len(train_windows),
            "trial_count": int(trial_count),
            "best_train_summary": best_summary or {},
            "intent_locks": intent_locks,
            "proposed_policy": _policy_summary_spec(base_policy),
            "frozen_policy": _policy_summary_spec(best_policy),
        }
        changed_keys: list[str] = []
        for key in (
            "entry_abs_score",
            "exit_abs_score",
            "flip_abs_score",
            "max_holding_bars",
            "cooldown_bars",
        ):
            if key in {"entry_abs_score", "exit_abs_score", "flip_abs_score"}:
                changed = not math.isclose(
                    float(best_policy[key]),
                    float(base_policy[key]),
                    rel_tol=1e-9,
                    abs_tol=1e-9,
                )
            else:
                changed = int(best_policy[key]) != int(base_policy[key])
            if changed:
                changed_keys.append(key)
        activity_summary = (best_summary or {}).get("activity_summary") or {}
        policy_sweep_best_train_score = (
            float(best_summary["aggregate_score"])
            if best_summary is not None and best_summary.get("aggregate_score") is not None
            else None
        )

        return raw_target, {
            "policy_sweep_applied": bool(train_windows),
            "policy_sweep_narrowed": bool(intent_locks["narrow_sweep"]),
            "policy_sweep_train_window_count": len(train_windows),
            "policy_sweep_trial_count": int(trial_count),
            "policy_sweep_best_train_score": policy_sweep_best_train_score,
            "policy_sweep_activity_penalty": float((best_summary or {}).get("activity_penalty") or 0.0),
            "policy_sweep_material_change": bool(changed_keys),
            "policy_sweep_changed_keys": changed_keys,
            "policy_sweep_proposed_policy": _policy_summary_spec(base_policy),
            "policy_sweep_frozen_policy": _policy_summary_spec(best_policy),
            "policy_active_bar_fraction": activity_summary.get("active_bar_fraction"),
            "policy_regime_gate_open_fraction": activity_summary.get("regime_gate_open_fraction"),
            "policy_entry_abs_score": float(best_policy["entry_abs_score"]),
            "policy_exit_abs_score": float(best_policy["exit_abs_score"]),
            "policy_flip_abs_score": float(best_policy["flip_abs_score"]),
            "policy_max_holding_bars": int(best_policy["max_holding_bars"]),
            "policy_cooldown_bars": int(best_policy["cooldown_bars"]),
        }, declared_raw_target

    def _pair_policy_evaluation_snapshot(
        self,
        *,
        spec: SignalSpec,
        target_all: pd.DataFrame,
        raw_target: pd.DataFrame,
        prices_all: pd.DataFrame,
        funding_all: pd.DataFrame | None,
        selector_windows: list[dict[str, Any]],
        validation_window: dict[str, Any] | None,
        audit_window: dict[str, Any] | None,
        evaluation_plan: dict[str, Any],
        leverage_tiers: list[float],
        min_rows: int,
        asset_breadth: int,
        regime_gate_mask: pd.Series | None,
    ) -> dict[str, Any]:
        selector_results: list[dict[str, Any]] = []
        for leverage in leverage_tiers:
            for window_spec in selector_windows:
                start_idx = int(window_spec["start_idx"])
                end_idx = int(window_spec["end_idx"])
                prices = prices_all.iloc[start_idx:end_idx]
                if len(prices) < min_rows:
                    continue
                target = target_all.reindex(prices.index).ffill().fillna(0.0)
                funding = (
                    funding_all.reindex(prices.index).ffill().fillna(0.0)
                    if funding_all is not None
                    else None
                )
                result = _lazy_run_backtest(
                    prices,
                    target,
                    BacktestConfig(
                        leverage=leverage,
                        funding_rates=funding,
                        rebalance_threshold=spec.risk.rebalance_threshold,
                        enable_liquidation=True,
                    ),
                )
                selector_results.append(
                    self._window_result_row(
                        result=result,
                        window_spec=window_spec,
                        leverage=leverage,
                        prices=prices,
                        used_for_selector=True,
                    )
                )
        aggregate = summarize_window_results(
            window_results=selector_results,
            asset_breadth=asset_breadth,
        ) if selector_results else {}

        selector_uses_validation_chunks = (
            str(evaluation_plan.get("selector_scope")) == "rolling_validation_chunks"
        )
        validation_summary = self._empty_window_summary("validation")
        if selector_uses_validation_chunks:
            leverage_one_selector_rows = [
                row for row in selector_results if math.isclose(float(row["leverage"]), 1.0)
            ]
            validation_summary = self._aggregate_window_summary(
                "validation",
                leverage_one_selector_rows or selector_results,
            )
        elif validation_window is not None:
            validation_prices = prices_all.iloc[
                int(validation_window["start_idx"]): int(validation_window["end_idx"])
            ]
            if len(validation_prices) >= min_rows:
                validation_target = target_all.reindex(validation_prices.index).ffill().fillna(0.0)
                validation_funding = (
                    funding_all.reindex(validation_prices.index).ffill().fillna(0.0)
                    if funding_all is not None
                    else None
                )
                validation_result = _lazy_run_backtest(
                    validation_prices,
                    validation_target,
                    BacktestConfig(
                        leverage=1.0,
                        funding_rates=validation_funding,
                        rebalance_threshold=spec.risk.rebalance_threshold,
                        enable_liquidation=True,
                    ),
                )
                validation_summary = self._window_summary(
                    "validation",
                    self._window_result_row(
                        result=validation_result,
                        window_spec=validation_window,
                        leverage=1.0,
                        prices=validation_prices,
                        used_for_selector=False,
                    ),
                )

        audit_summary = self._empty_window_summary("audit")
        if audit_window is not None:
            audit_prices = prices_all.iloc[int(audit_window["start_idx"]): int(audit_window["end_idx"])]
            if len(audit_prices) >= min_rows:
                audit_target = target_all.reindex(audit_prices.index).ffill().fillna(0.0)
                audit_funding = (
                    funding_all.reindex(audit_prices.index).ffill().fillna(0.0)
                    if funding_all is not None
                    else None
                )
                audit_result = _lazy_run_backtest(
                    audit_prices,
                    audit_target,
                    BacktestConfig(
                        leverage=1.0,
                        funding_rates=audit_funding,
                        rebalance_threshold=spec.risk.rebalance_threshold,
                        enable_liquidation=True,
                    ),
                )
                audit_summary = self._window_summary(
                    "audit",
                    self._window_result_row(
                        result=audit_result,
                        window_spec=audit_window,
                        leverage=1.0,
                        prices=audit_prices,
                        used_for_selector=False,
                    ),
                )

        canonical_run = self._canonical_full_run(
            prices_all=prices_all,
            target_all=target_all,
            funding_all=funding_all,
            spec=spec,
            visual_split=evaluation_plan["visual_split"],
            regime_state={"available": False},
            evaluation_windows=selector_windows
            + ([validation_window] if validation_window is not None else [])
            + ([audit_window] if audit_window is not None else [])
            + [
                {
                    "label": "reference_full",
                    "role": "reference_full",
                    "start_idx": 0,
                    "end_idx": len(prices_all.index),
                }
            ],
        )
        pre_audit_end_idx = _pre_audit_end_idx(
            canonical_run.get("visual_split") or {},
            canonical_run.get("equity_curve"),
        )
        return {
            "selector_aggregate_score": _safe_float(aggregate.get("aggregate_score"), default=None),
            "selector_median_total_return": _safe_float(aggregate.get("median_total_return"), default=None),
            "selector_profitable_window_pct": _safe_float(
                aggregate.get("profitable_window_pct"),
                default=None,
            ),
            "validation_total_return": _safe_float(
                validation_summary.get("validation_total_return"),
                default=None,
            ),
            "pre_audit_canonical_total_return": _series_total_return(
                canonical_run.get("equity_curve"),
                end_idx=pre_audit_end_idx,
            ),
            "pre_audit_canonical_max_drawdown": _series_min_value(
                canonical_run.get("drawdown_curve"),
                end_idx=pre_audit_end_idx,
            ),
            "audit_total_return": _safe_float(
                audit_summary.get("audit_total_return"),
                default=None,
            ),
            "active_bar_fraction": _safe_float(
                self._pair_policy_activity_summary(
                    raw_target=raw_target,
                    regime_gate_mask=regime_gate_mask,
                ).get("active_bar_fraction"),
                default=None,
            ),
            "regime_gate_open_fraction": _safe_float(
                self._pair_policy_activity_summary(
                    raw_target=raw_target,
                    regime_gate_mask=regime_gate_mask,
                ).get("regime_gate_open_fraction"),
                default=None,
            ),
        }

    def _pair_policy_snapshot_from_evaluation(
        self,
        *,
        summary: dict[str, Any],
        canonical_run: dict[str, Any],
    ) -> dict[str, Any]:
        pre_audit_end_idx = _pre_audit_end_idx(
            canonical_run.get("visual_split") or {},
            canonical_run.get("equity_curve"),
        )
        return {
            "selector_aggregate_score": _safe_float(summary.get("aggregate_score"), default=None),
            "selector_median_total_return": _safe_float(summary.get("median_total_return"), default=None),
            "selector_profitable_window_pct": _safe_float(
                summary.get("profitable_window_pct"),
                default=None,
            ),
            "validation_total_return": _safe_float(summary.get("validation_total_return"), default=None),
            "pre_audit_canonical_total_return": _safe_float(
                summary.get("pre_audit_canonical_total_return"),
                default=None,
            ),
            "pre_audit_canonical_max_drawdown": _series_min_value(
                canonical_run.get("drawdown_curve"),
                end_idx=pre_audit_end_idx,
            ),
            "audit_total_return": _safe_float(summary.get("audit_total_return"), default=None),
            "active_bar_fraction": _safe_float(
                summary.get("policy_active_bar_fraction", summary.get("active_bar_fraction")),
                default=None,
            ),
            "regime_gate_open_fraction": _safe_float(
                summary.get("policy_regime_gate_open_fraction"),
                default=None,
            ),
        }

    def _pair_policy_compare_snapshots(
        self,
        *,
        declared_snapshot: dict[str, Any],
        frozen_snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        metric_directions = {
            "selector_aggregate_score": 1.0,
            "selector_median_total_return": 1.0,
            "selector_profitable_window_pct": 1.0,
            "validation_total_return": 1.0,
            "pre_audit_canonical_total_return": 1.0,
            "pre_audit_canonical_max_drawdown": 1.0,
            "audit_total_return": 1.0,
        }
        declared_better: list[str] = []
        frozen_better: list[str] = []
        equal: list[str] = []
        for metric, direction in metric_directions.items():
            declared_value = _safe_float(declared_snapshot.get(metric), default=None)
            frozen_value = _safe_float(frozen_snapshot.get(metric), default=None)
            if declared_value is None or frozen_value is None:
                continue
            adjusted_declared = declared_value * direction
            adjusted_frozen = frozen_value * direction
            if math.isclose(adjusted_declared, adjusted_frozen, rel_tol=1e-9, abs_tol=1e-9):
                equal.append(metric)
            elif adjusted_declared > adjusted_frozen:
                declared_better.append(metric)
            else:
                frozen_better.append(metric)
        realized_winner = "mixed"
        if declared_better and not frozen_better:
            realized_winner = "declared"
        elif frozen_better and not declared_better:
            realized_winner = "frozen"
        elif not declared_better and not frozen_better and equal:
            realized_winner = "equal"
        return {
            "declared_better_metrics": declared_better,
            "frozen_better_metrics": frozen_better,
            "equal_metrics": equal,
            "realized_winner": realized_winner,
        }

    def _sample_policy_train_windows(
        self,
        windows: list[dict[str, Any]],
        *,
        max_count: int | None,
    ) -> list[dict[str, Any]]:
        if max_count is None or max_count <= 0 or len(windows) <= max_count:
            return windows
        if max_count == 1:
            return [windows[0]]
        last_index = len(windows) - 1
        selected_indexes: list[int] = []
        for position in range(max_count):
            ratio = position / max(max_count - 1, 1)
            index = int(round(ratio * last_index))
            if index not in selected_indexes:
                selected_indexes.append(index)
        return [windows[index] for index in selected_indexes]

    def _pair_policy_activity_summary(
        self,
        *,
        raw_target: pd.DataFrame,
        regime_gate_mask: pd.Series | None,
    ) -> dict[str, float | None]:
        active_mask = raw_target.abs().sum(axis=1) > 1e-9
        active_fraction = float(active_mask.mean()) if len(active_mask.index) else 0.0
        regime_gate_open_fraction = (
            float(regime_gate_mask.astype(float).mean())
            if regime_gate_mask is not None and len(regime_gate_mask.index)
            else None
        )
        return {
            "active_bar_fraction": active_fraction,
            "regime_gate_open_fraction": regime_gate_open_fraction,
        }

    def _pair_policy_activity_penalty(
        self,
        *,
        activity_summary: dict[str, float | None],
        policy: dict[str, Any],
    ) -> float:
        active_fraction = _safe_float(
            activity_summary.get("active_bar_fraction"),
            default=None,
        )
        regime_gate_open_fraction = _safe_float(
            activity_summary.get("regime_gate_open_fraction"),
            default=None,
        )
        penalty = 0.0
        if active_fraction is not None:
            if active_fraction < 0.005:
                penalty += 0.45
            elif active_fraction < 0.01:
                penalty += 0.25
            elif active_fraction < 0.02:
                penalty += 0.1
        if regime_gate_open_fraction is not None:
            if regime_gate_open_fraction < 0.02:
                penalty += 0.15
            elif regime_gate_open_fraction < 0.05:
                penalty += 0.05
        if (
            active_fraction is not None
            and active_fraction < 0.01
            and int(policy.get("max_holding_bars", 0)) == 0
            and int(policy.get("cooldown_bars", 0)) == 0
        ):
            penalty += 0.05
        return penalty

    def _window_result_row(
        self,
        *,
        result: Any,
        window_spec: dict[str, Any],
        leverage: float,
        prices: pd.DataFrame,
        used_for_selector: bool,
    ) -> dict[str, Any]:
        return {
            "window": str(window_spec["label"]),
            "role": str(window_spec["role"]),
            "used_for_selector": bool(used_for_selector),
            "start_idx": int(window_spec["start_idx"]),
            "end_idx": int(window_spec["end_idx"]),
            "start_timestamp": prices.index[0].isoformat(),
            "end_timestamp": prices.index[-1].isoformat(),
            "leverage": leverage,
            "stats": serialize_stats(result.stats),
            "liquidated": result.liquidated,
            "train_start_idx": window_spec.get("train_start_idx"),
            "train_end_idx": window_spec.get("train_end_idx"),
            "train_start_timestamp": window_spec.get("train_start_timestamp"),
            "train_end_timestamp": window_spec.get("train_end_timestamp"),
            "validation_start_timestamp": window_spec.get("validation_start_timestamp"),
            "validation_end_timestamp": window_spec.get("validation_end_timestamp"),
        }

    def _canonical_full_run(
        self,
        *,
        prices_all: pd.DataFrame,
        target_all: pd.DataFrame,
        funding_all: pd.DataFrame | None,
        spec: SignalSpec,
        visual_split: dict[str, Any],
        regime_state: dict[str, Any],
        evaluation_windows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        full_config = BacktestConfig(
            leverage=1.0,
            funding_rates=funding_all,
            rebalance_threshold=spec.risk.rebalance_threshold,
            enable_liquidation=True,
        )
        result = _lazy_run_backtest(prices_all, target_all, full_config)
        regime_diagnostics = (
            _pair_regime_diagnostics(
                prices=prices_all,
                target_weights=target_all,
                funding_rates=funding_all,
                returns=result.returns,
            )
            if regime_state.get("available")
            else {}
        )
        trade_episodes = (
            _pair_trade_episodes_with_regime(
                target_weights=target_all,
                returns=result.returns,
                regime_state=regime_state,
            )
            if regime_state.get("available")
            else []
        )
        return _serialize_canonical_run(
            result=result,
            target_weights=target_all,
            visual_split=visual_split,
            evaluation_windows=_serialize_window_ranges(prices_all.index, evaluation_windows),
            regime_diagnostics=regime_diagnostics,
            regime_state=regime_state,
            trade_episodes=trade_episodes,
        )

    def _empty_window_summary(self, prefix: str) -> dict[str, Any]:
        return {
            f"{prefix}_available": False,
            f"{prefix}_sharpe": None,
            f"{prefix}_total_return": None,
            f"{prefix}_cagr": None,
            f"{prefix}_calmar": None,
            f"{prefix}_max_drawdown": None,
            f"{prefix}_liquidated": None,
            f"{prefix}_window_count": 0,
            f"{prefix}_profitable_window_pct": None,
        }

    def _window_summary(self, prefix: str, row: dict[str, Any]) -> dict[str, Any]:
        stats = row.get("stats") or {}
        return {
            f"{prefix}_available": True,
            f"{prefix}_sharpe": stats.get("sharpe"),
            f"{prefix}_total_return": stats.get("total_return"),
            f"{prefix}_cagr": stats.get("cagr"),
            f"{prefix}_calmar": stats.get("calmar"),
            f"{prefix}_max_drawdown": stats.get("max_drawdown"),
            f"{prefix}_liquidated": bool(row.get("liquidated")),
            f"{prefix}_window_count": 1,
            f"{prefix}_profitable_window_pct": (
                1.0
                if (stats.get("total_return") is not None and _safe_float(stats.get("total_return")) is not None and float(stats["total_return"]) > 0.0)
                else 0.0
            ),
        }

    def _aggregate_window_summary(
        self,
        prefix: str,
        rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not rows:
            return self._empty_window_summary(prefix)
        aggregate = summarize_window_results(
            window_results=rows,
            asset_breadth=0,
        )
        return {
            f"{prefix}_available": True,
            f"{prefix}_sharpe": aggregate.get("median_sharpe"),
            f"{prefix}_total_return": aggregate.get("median_total_return"),
            f"{prefix}_cagr": aggregate.get("median_cagr"),
            f"{prefix}_calmar": aggregate.get("median_calmar"),
            f"{prefix}_max_drawdown": aggregate.get("worst_max_drawdown"),
            f"{prefix}_liquidated": bool(aggregate.get("liquidation_count", 0)),
            f"{prefix}_window_count": int(aggregate.get("window_count", 0)),
            f"{prefix}_profitable_window_pct": aggregate.get("profitable_window_pct"),
        }
