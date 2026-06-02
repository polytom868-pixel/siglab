from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from siglab.data.feeds import MarketDataProvider
from siglab.evaluator.compile import (
    PAIR_STATEFUL_POLICY_SCHEMA,
    _build_pair_trade_positions,
    compile_spec,
)
from siglab.evaluator.gates import evaluate_gates
from siglab.evaluator.score import serialize_stats, summarize_window_results
from siglab.schemas import SignalSpec
from siglab.config import SiglabConfig
from siglab.evaluator.backtesting import BacktestConfig, run_backtest


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
        compiled = await compile_spec(self.settings, self.provider, spec)
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
        selector_windows = list(evaluation_plan["selector_windows"])
        validation_window = evaluation_plan.get("validation_window")
        audit_window = evaluation_plan.get("audit_window")
        leverage_tiers = sorted(
            {1.0, min(2.0, spec.risk.max_leverage), spec.risk.max_leverage}
        )
        compiled.metadata["evaluation_split"] = dict(evaluation_plan["visual_split"])
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
                result = run_backtest(prices, target, config)
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
                validation_config = BacktestConfig(
                    leverage=1.0,
                    funding_rates=validation_funding,
                    rebalance_threshold=spec.risk.rebalance_threshold,
                    enable_liquidation=True,
                )
                validation_result = run_backtest(
                    validation_prices,
                    validation_target,
                    validation_config,
                )
                validation_row = self._window_result_row(
                    result=validation_result,
                    window_spec=validation_window,
                    leverage=1.0,
                    prices=validation_prices,
                    used_for_selector=False,
                )
                window_results.append(validation_row)
                validation_summary = self._window_summary("validation", validation_row)

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
                audit_config = BacktestConfig(
                    leverage=1.0,
                    funding_rates=audit_funding,
                    rebalance_threshold=spec.risk.rebalance_threshold,
                    enable_liquidation=True,
                )
                audit_result = run_backtest(audit_prices, audit_target, audit_config)
                audit_row = self._window_result_row(
                    result=audit_result,
                    window_spec=audit_window,
                    leverage=1.0,
                    prices=audit_prices,
                    used_for_selector=False,
                )
                window_results.append(audit_row)
                audit_summary = self._window_summary("audit", audit_row)

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
        gate_diagnostics_summary = dict(
            (canonical_run.get("pre_audit_context_pack") or {}).get("gate_diagnostics") or {}
        )
        summary["active_bar_fraction"] = gate_diagnostics_summary.get("active_bar_fraction")
        summary["entry_signal_bar_fraction"] = gate_diagnostics_summary.get(
            "entry_signal_bar_fraction"
        )
        summary["score_alignment_when_active"] = gate_diagnostics_summary.get(
            "score_alignment_when_active"
        )
        summary["gate_bottleneck_tags"] = list(
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
                **dict(compiled.metadata.get("pair_policy_sweep") or {}),
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
        for window in list(evaluation_plan.get("selector_windows") or []):
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
            base_entry = float(base_policy["entry_abs_score"]) or 0.05
            base_exit = float(base_policy["exit_abs_score"]) or 0.05
            base_flip = float(base_policy["flip_abs_score"]) or max(base_entry, 0.05)
            base_hold = int(base_policy["max_holding_bars"])
            base_cooldown = int(base_policy["cooldown_bars"])

            def _distance(policy: dict[str, Any]) -> tuple[float, float]:
                score = (
                    abs(float(policy["entry_abs_score"]) - base_entry) / max(abs(base_entry), 0.05)
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
            dict(compiled.metadata.get("regime_gates") or {}).get("exit_on_break", True)
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
        intent_locks = {
            "narrow_sweep": bool(dict(compiled.metadata.get("regime_gates") or {}).get("entry"))
            or int(base_policy["max_holding_bars"]) > 0
            or int(base_policy["cooldown_bars"]) > 0,
            "lock_time_stop": int(base_policy["max_holding_bars"]) > 0,
            "lock_cooldown": int(base_policy["cooldown_bars"]) > 0,
            "regime_gate_count": len(list(dict(compiled.metadata.get("regime_gates") or {}).get("entry") or [])),
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
                    result = run_backtest(
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
                rank = (
                    float(aggregate.get("aggregate_score") or -1e9) - activity_penalty,
                    float(aggregate.get("median_total_return") or -1e9),
                    float(aggregate.get("profitable_window_pct") or -1e9),
                    -activity_penalty,
                    -abs(float(policy["entry_abs_score"]) - float(base_policy["entry_abs_score"])),
                )
                best_rank = (
                    float(best_summary.get("aggregate_score") or -1e9)
                    - float(best_summary.get("activity_penalty") or 0.0),
                    float(best_summary.get("median_total_return") or -1e9),
                    float(best_summary.get("profitable_window_pct") or -1e9),
                    -float(best_summary.get("activity_penalty") or 0.0),
                    -abs(float(best_policy["entry_abs_score"]) - float(base_policy["entry_abs_score"])),
                ) if best_summary is not None else None
                if best_rank is None or rank > best_rank:
                    best_policy = dict(policy)
                    best_summary = {
                        **dict(aggregate),
                        "activity_penalty": activity_penalty,
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
            "best_train_summary": dict(best_summary or {}),
            "intent_locks": dict(intent_locks),
            "proposed_policy": {
                "entry_abs_score": float(base_policy["entry_abs_score"]),
                "exit_abs_score": float(base_policy["exit_abs_score"]),
                "flip_abs_score": float(base_policy["flip_abs_score"]),
                "max_holding_bars": int(base_policy["max_holding_bars"]),
                "cooldown_bars": int(base_policy["cooldown_bars"]),
            },
            "frozen_policy": {
                "entry_abs_score": float(best_policy["entry_abs_score"]),
                "exit_abs_score": float(best_policy["exit_abs_score"]),
                "flip_abs_score": float(best_policy["flip_abs_score"]),
                "max_holding_bars": int(best_policy["max_holding_bars"]),
                "cooldown_bars": int(best_policy["cooldown_bars"]),
            },
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
        activity_summary = dict((best_summary or {}).get("activity_summary") or {})
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
            "policy_sweep_proposed_policy": {
                "entry_abs_score": float(base_policy["entry_abs_score"]),
                "exit_abs_score": float(base_policy["exit_abs_score"]),
                "flip_abs_score": float(base_policy["flip_abs_score"]),
                "max_holding_bars": int(base_policy["max_holding_bars"]),
                "cooldown_bars": int(base_policy["cooldown_bars"]),
            },
            "policy_sweep_frozen_policy": {
                "entry_abs_score": float(best_policy["entry_abs_score"]),
                "exit_abs_score": float(best_policy["exit_abs_score"]),
                "flip_abs_score": float(best_policy["flip_abs_score"]),
                "max_holding_bars": int(best_policy["max_holding_bars"]),
                "cooldown_bars": int(best_policy["cooldown_bars"]),
            },
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
                result = run_backtest(
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
                validation_result = run_backtest(
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
                audit_result = run_backtest(
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
            return list(windows)
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
            "stats": serialize_stats(dict(result.stats)),
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
        result = run_backtest(prices_all, target_all, full_config)
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
        stats = dict(row.get("stats") or {})
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
                if (_safe_float(stats.get("total_return")) is not None and float(stats["total_return"]) > 0.0)
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


def _safe_float(
    value: Any,
    digits: int = 8,
    *,
    default: float | None = None,
) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(numeric):
        return default
    return round(numeric, digits)


def _unique_float_values(values: list[float], *, low: float, high: float) -> list[float]:
    cleaned: list[float] = []
    seen: set[float] = set()
    for value in values:
        numeric = round(max(low, min(high, float(value))), 6)
        if numeric in seen:
            continue
        seen.add(numeric)
        cleaned.append(numeric)
    return cleaned


def _unique_int_values(values: list[int], *, low: int, high: int) -> list[int]:
    cleaned: list[int] = []
    seen: set[int] = set()
    for value in values:
        numeric = max(low, min(high, int(value)))
        if numeric in seen:
            continue
        seen.add(numeric)
        cleaned.append(numeric)
    return cleaned


def _serialize_series(series: pd.Series, digits: int = 8) -> dict[str, Any]:
    clean = pd.to_numeric(series, errors="coerce")
    return {
        "index": [timestamp.isoformat() for timestamp in clean.index],
        "values": [_safe_float(value, digits=digits) for value in clean.tolist()],
    }


def _serialize_metrics_frame(frame: pd.DataFrame, digits: int = 8) -> dict[str, Any]:
    normalized = frame.copy()
    if "fee_amount" not in normalized.columns:
        if "cost" in normalized.columns:
            normalized["fee_amount"] = pd.to_numeric(
                normalized["cost"],
                errors="coerce",
            ).fillna(0.0)
        else:
            normalized["fee_amount"] = 0.0
    if "funding_amount" not in normalized.columns:
        normalized["funding_amount"] = 0.0
    if "cash_balance" not in normalized.columns:
        normalized["cash_balance"] = np.nan
    if "margin_headroom" not in normalized.columns:
        normalized["margin_headroom"] = np.nan
    return {
        "index": [timestamp.isoformat() for timestamp in normalized.index],
        "columns": list(normalized.columns),
        "rows": [
            [_safe_float(value, digits=digits) for value in row]
            for row in normalized.itertuples(index=False, name=None)
        ],
    }


def _serialize_weight_changes(
    frame: pd.DataFrame,
    *,
    digits: int = 6,
    epsilon: float = 1e-9,
) -> dict[str, Any]:
    changes: list[dict[str, Any]] = []
    previous: dict[str, float] | None = None
    for timestamp, row in frame.iterrows():
        current = {
            column: round(float(value), digits)
            for column, value in row.items()
            if pd.notna(value) and abs(float(value)) > epsilon
        }
        if current == previous:
            continue
        changes.append(
            {
                "timestamp": timestamp.isoformat(),
                "weights": current,
            }
        )
        previous = current
    return {
        "index": [timestamp.isoformat() for timestamp in frame.index],
        "columns": list(frame.columns),
        "changes": changes,
    }


def _serialize_trades(
    trades: list[dict[str, Any]],
    *,
    regime_state: dict[str, Any] | None = None,
    target_weights: pd.DataFrame | None = None,
) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for trade in trades:
        item: dict[str, Any] = {}
        for key, value in trade.items():
            if hasattr(value, "isoformat"):
                item[key] = value.isoformat()
            elif isinstance(value, (int, float)):
                item[key] = _safe_float(value)
            else:
                item[key] = value
        if regime_state and regime_state.get("available"):
            item["regime_snapshot"] = _pair_regime_snapshot(
                regime_state=regime_state,
                timestamp=trade.get("timestamp"),
                target_weights=target_weights,
            )
        serialized.append(item)
    return serialized


def _annualized_sharpe(returns: pd.Series, *, periods_per_year: float = 365.25 * 24.0) -> float | None:
    clean = pd.to_numeric(returns, errors="coerce").dropna()
    if clean.empty:
        return None
    volatility = float(clean.std())
    if not math.isfinite(volatility) or volatility <= 0.0:
        return None
    return _safe_float((float(clean.mean()) / volatility) * math.sqrt(periods_per_year))


def _max_drawdown(returns: pd.Series) -> float | None:
    clean = pd.to_numeric(returns, errors="coerce").dropna()
    if clean.empty:
        return None
    equity = (1.0 + clean).cumprod()
    drawdown = equity.div(equity.cummax()).sub(1.0)
    return _safe_float(drawdown.min())


def _slice_performance_stats(
    *,
    returns: pd.Series,
    gross_exposure: pd.Series,
    mask: pd.Series,
    label: str,
) -> dict[str, Any]:
    aligned_mask = mask.reindex(returns.index).fillna(False).astype(bool)
    subset = pd.to_numeric(returns[aligned_mask], errors="coerce").dropna()
    exposure_subset = gross_exposure.reindex(returns.index).fillna(0.0)[aligned_mask]
    if subset.empty:
        return {
            "label": label,
            "available": False,
            "sample_bars": 0,
            "active_bars": 0,
        }
    total_return = float((1.0 + subset).prod() - 1.0)
    active_bars = int((exposure_subset > 1e-9).sum())
    return {
        "label": label,
        "available": True,
        "sample_bars": int(subset.shape[0]),
        "active_bars": active_bars,
        "active_bar_fraction": _safe_float(active_bars / max(1, int(subset.shape[0]))),
        "avg_gross_exposure": _safe_float(exposure_subset.mean()),
        "mean_return": _safe_float(subset.mean()),
        "total_return": _safe_float(total_return),
        "sharpe": _annualized_sharpe(subset),
        "max_drawdown": _max_drawdown(subset),
        "positive_bar_fraction": _safe_float((subset > 0.0).mean()),
    }


def _row_position_signature(row: pd.Series, *, epsilon: float = 1e-9) -> tuple[tuple[str, int], ...]:
    clean = pd.to_numeric(row, errors="coerce").fillna(0.0)
    active = [
        (str(column), int(np.sign(value)))
        for column, value in clean.items()
        if abs(float(value)) > epsilon
    ]
    active.sort(key=lambda item: item[0])
    return tuple(active)


def _episode_asset_lists(row: pd.Series, *, epsilon: float = 1e-9) -> tuple[list[str], list[str], list[str]]:
    clean = pd.to_numeric(row, errors="coerce").fillna(0.0)
    active = [str(column) for column, value in clean.items() if abs(float(value)) > epsilon]
    longs = [str(column) for column, value in clean.items() if float(value) > epsilon]
    shorts = [str(column) for column, value in clean.items() if float(value) < -epsilon]
    return active, longs, shorts


def _row_direction_label(row: pd.Series, *, epsilon: float = 1e-9) -> str:
    clean = pd.to_numeric(row, errors="coerce").fillna(0.0)
    active, longs, shorts = _episode_asset_lists(clean, epsilon=epsilon)
    if not active:
        return "flat"
    if len(clean.index) >= 2 and len(active) == 2 and set(active) == set(map(str, clean.index[:2])):
        first = float(clean.iloc[0])
        second = float(clean.iloc[1])
        if first > epsilon and second < -epsilon:
            return "long_asset_1_short_asset_2"
        if first < -epsilon and second > epsilon:
            return "short_asset_1_long_asset_2"
    gross = float(clean.abs().sum())
    net = float(clean.sum())
    if longs and shorts and gross > 0.0 and abs(net) <= gross * 0.2:
        return "market_neutral"
    if net > epsilon or (longs and not shorts):
        return "net_long"
    if net < -epsilon or (shorts and not longs):
        return "net_short"
    return "mixed"


def _mean_pairwise_rolling_corr(
    returns: pd.DataFrame,
    *,
    window: int,
) -> pd.Series:
    columns = list(returns.columns)
    if not columns:
        return pd.Series(dtype=float)
    if len(columns) == 1:
        return pd.Series(1.0, index=returns.index, dtype=float)
    rows: list[pd.Series] = []
    for left_idx in range(len(columns)):
        for right_idx in range(left_idx + 1, len(columns)):
            rows.append(returns.iloc[:, left_idx].rolling(window).corr(returns.iloc[:, right_idx]))
    return pd.concat(rows, axis=1).mean(axis=1) if rows else pd.Series(dtype=float)


def _pair_position_episodes(
    *,
    target_weights: pd.DataFrame,
    returns: pd.Series,
) -> list[dict[str, Any]]:
    if target_weights.empty:
        return []
    signatures = pd.Series(
        [_row_position_signature(row) for _, row in target_weights.iterrows()],
        index=target_weights.index,
        dtype=object,
    )
    episodes: list[dict[str, Any]] = []
    current_signature: tuple[tuple[str, int], ...] = ()
    start_timestamp: pd.Timestamp | None = None
    previous_timestamp: pd.Timestamp | None = None

    def _append_episode(
        episode_start: pd.Timestamp,
        episode_end: pd.Timestamp,
        signature: tuple[tuple[str, int], ...],
    ) -> None:
        if not signature:
            return
        episode_target = target_weights.loc[episode_start:episode_end]
        if episode_target.empty:
            return
        episode_returns = pd.to_numeric(
            returns.loc[episode_start:episode_end],
            errors="coerce",
        ).dropna()
        start_row = episode_target.iloc[0]
        active_assets, long_assets, short_assets = _episode_asset_lists(start_row)
        gross_exposure = pd.to_numeric(episode_target.abs().sum(axis=1), errors="coerce").fillna(0.0)
        net_exposure = pd.to_numeric(episode_target.sum(axis=1), errors="coerce").fillna(0.0)
        active_asset_count = (
            episode_target.abs().gt(1e-9).sum(axis=1).astype(float)
            if not episode_target.empty
            else pd.Series(dtype=float)
        )
        episodes.append(
            {
                "direction": _row_direction_label(start_row),
                "start_timestamp": episode_start.isoformat(),
                "end_timestamp": episode_end.isoformat(),
                "bars": int(episode_returns.shape[0]),
                "total_return": _safe_float(
                    (1.0 + episode_returns).prod() - 1.0
                    if not episode_returns.empty
                    else 0.0
                ),
                "active_assets": active_assets,
                "long_assets": long_assets,
                "short_assets": short_assets,
                "active_asset_count": _safe_float(active_asset_count.median()),
                "gross_exposure": _safe_float(gross_exposure.median()),
                "net_exposure": _safe_float(net_exposure.median()),
            }
        )

    for timestamp, signature in signatures.items():
        if not current_signature and signature:
            current_signature = signature
            start_timestamp = timestamp
        elif current_signature and signature != current_signature:
            if start_timestamp is not None and previous_timestamp is not None:
                _append_episode(start_timestamp, previous_timestamp, current_signature)
            current_signature = signature
            start_timestamp = timestamp if signature else None
        previous_timestamp = timestamp

    if current_signature and start_timestamp is not None and previous_timestamp is not None:
        _append_episode(start_timestamp, previous_timestamp, current_signature)
    return episodes


def _holding_period_buckets(target_weights: pd.DataFrame, returns: pd.Series) -> list[dict[str, Any]]:
    episodes = _pair_position_episodes(target_weights=target_weights, returns=returns)
    bucket_specs = [
        ("bars_1_6", 1, 6),
        ("bars_7_24", 7, 24),
        ("bars_25_72", 25, 72),
        ("bars_73_plus", 73, None),
    ]
    rows: list[dict[str, Any]] = []
    for label, low, high in bucket_specs:
        matched = [
            episode
            for episode in episodes
            if int(episode["bars"]) >= low and (high is None or int(episode["bars"]) <= high)
        ]
        returns_bucket = [
            float(episode["total_return"])
            for episode in matched
            if episode.get("total_return") is not None
        ]
        rows.append(
            {
                "label": label,
                "trade_count": len(matched),
                "median_bars": _safe_float(np.median([int(episode["bars"]) for episode in matched]))
                if matched
                else None,
                "median_return": _safe_float(np.median(returns_bucket)) if returns_bucket else None,
                "win_rate": _safe_float(
                    sum(1 for value in returns_bucket if value > 0.0) / len(returns_bucket)
                )
                if returns_bucket
                else None,
                "direction_counts": _episode_direction_counts(matched),
            }
        )
    return rows


def _pair_regime_state(
    *,
    prices: pd.DataFrame,
    target_weights: pd.DataFrame,
    funding_rates: pd.DataFrame | None,
) -> dict[str, Any]:
    if prices.empty:
        return {"available": False}
    prices = prices.sort_index()
    returns_1h = prices.pct_change()
    returns_24h = prices.pct_change(24)
    funding = (
        funding_rates.reindex(prices.index).ffill().fillna(0.0)
        if funding_rates is not None
        else pd.DataFrame(np.nan, index=prices.index, columns=prices.columns)
    )
    target_aligned = target_weights.reindex(prices.index).ffill().fillna(0.0)

    market_trend = returns_24h.mean(axis=1)
    market_volatility = returns_1h.rolling(168).std().mean(axis=1)
    funding_level = funding.mean(axis=1)
    funding_dispersion = funding.std(axis=1)
    breadth = returns_24h.gt(0.0).mean(axis=1)
    co_movement = _mean_pairwise_rolling_corr(returns_1h, window=72)
    gross_exposure = target_aligned.abs().sum(axis=1)
    net_exposure = target_aligned.sum(axis=1)
    active_asset_count = target_aligned.abs().gt(1e-9).sum(axis=1).astype(float)
    abs_weights = target_aligned.abs()
    concentration = (
        abs_weights.div(abs_weights.sum(axis=1).replace(0.0, np.nan), axis=0).pow(2).sum(axis=1)
    ).fillna(0.0)
    position_direction = pd.Series(
        [_row_direction_label(row) for _, row in target_aligned.iterrows()],
        index=target_aligned.index,
        dtype=object,
    )

    thresholds = {
        "market_volatility_median": _safe_float(market_volatility.dropna().median(), default=None),
        "funding_level_median": _safe_float(funding_level.dropna().median(), default=None),
        "funding_dispersion_median": _safe_float(funding_dispersion.dropna().median(), default=None),
        "breadth_median": _safe_float(breadth.dropna().median(), default=None),
        "co_movement_median": _safe_float(co_movement.dropna().median(), default=None),
        "concentration_median": _safe_float(concentration.dropna().median(), default=None),
    }
    state: dict[str, Any] = {
        "available": True,
        "index": prices.index,
        "market_trend": market_trend,
        "market_volatility": market_volatility,
        "funding_level": funding_level,
        "funding_dispersion": funding_dispersion,
        "breadth": breadth,
        "co_movement": co_movement,
        "gross_exposure": gross_exposure,
        "net_exposure": net_exposure,
        "active_asset_count": active_asset_count,
        "concentration": concentration,
        "position_direction": position_direction,
        "thresholds": thresholds,
    }

    if prices.shape[1] >= 2:
        asset_1_symbol, asset_2_symbol = list(prices.columns[:2])
        asset_1_returns_1h = prices[asset_1_symbol].pct_change()
        asset_2_returns_1h = prices[asset_2_symbol].pct_change()
        pair_ratio = prices[asset_1_symbol].div(prices[asset_2_symbol]).replace([np.inf, -np.inf], np.nan)
        pair_volatility = pair_ratio.pct_change().rolling(72).std()
        pair_correlation = asset_1_returns_1h.rolling(72).corr(asset_2_returns_1h)
        pair_direction = prices[asset_1_symbol].pct_change(24).sub(prices[asset_2_symbol].pct_change(24))
        state.update(
            {
                "asset_1_symbol": asset_1_symbol,
                "asset_2_symbol": asset_2_symbol,
                "pair_volatility": pair_volatility,
                "pair_correlation": pair_correlation,
                "pair_direction": pair_direction,
            }
        )
        state["thresholds"].update(
            {
                "pair_volatility_median": _safe_float(pair_volatility.dropna().median(), default=None),
                "pair_correlation_median": _safe_float(pair_correlation.dropna().median(), default=None),
            }
        )
    return state


def _lookup_timestamp(index: pd.Index, timestamp: Any) -> pd.Timestamp | None:
    if len(index) == 0 or timestamp is None:
        return None
    ts = pd.Timestamp(timestamp)
    if isinstance(index, pd.DatetimeIndex):
        if index.tz is None:
            if ts.tzinfo is not None:
                ts = ts.tz_convert(None)
        else:
            if ts.tzinfo is None:
                ts = ts.tz_localize(index.tz)
            else:
                ts = ts.tz_convert(index.tz)
    if ts in index:
        return pd.Timestamp(ts)
    position = int(index.searchsorted(ts, side="right")) - 1
    if position < 0:
        return None
    if position >= len(index):
        position = len(index) - 1
    return pd.Timestamp(index[position])


def _pair_regime_snapshot(
    *,
    regime_state: dict[str, Any],
    timestamp: Any,
    target_weights: pd.DataFrame | None,
) -> dict[str, Any]:
    if not regime_state.get("available"):
        return {}
    aligned_timestamp = _lookup_timestamp(regime_state["index"], timestamp)
    if aligned_timestamp is None:
        return {}
    thresholds = dict(regime_state.get("thresholds") or {})
    market_trend_value = _safe_float(regime_state["market_trend"].get(aligned_timestamp))
    market_volatility_value = _safe_float(regime_state["market_volatility"].get(aligned_timestamp))
    funding_level_value = _safe_float(regime_state["funding_level"].get(aligned_timestamp))
    funding_dispersion_value = _safe_float(regime_state["funding_dispersion"].get(aligned_timestamp))
    breadth_value = _safe_float(regime_state["breadth"].get(aligned_timestamp))
    co_movement_value = _safe_float(regime_state["co_movement"].get(aligned_timestamp))
    gross_exposure_value = _safe_float(regime_state["gross_exposure"].get(aligned_timestamp))
    net_exposure_value = _safe_float(regime_state["net_exposure"].get(aligned_timestamp))
    active_asset_count_value = _safe_float(regime_state["active_asset_count"].get(aligned_timestamp))
    concentration_value = _safe_float(regime_state["concentration"].get(aligned_timestamp))
    position_direction = str(regime_state["position_direction"].get(aligned_timestamp) or "flat")

    market_vol_threshold = thresholds.get("market_volatility_median")
    funding_level_threshold = thresholds.get("funding_level_median")
    funding_threshold = thresholds.get("funding_dispersion_median")
    breadth_threshold = thresholds.get("breadth_median")
    co_movement_threshold = thresholds.get("co_movement_median")
    concentration_threshold = thresholds.get("concentration_median")
    if target_weights is not None and not target_weights.empty:
        exposure_row = target_weights.reindex(regime_state["index"]).ffill().fillna(0.0)
        gross_exposure_value = _safe_float(exposure_row.abs().sum(axis=1).get(aligned_timestamp))
        net_exposure_value = _safe_float(exposure_row.sum(axis=1).get(aligned_timestamp))

    snapshot = {
        "timestamp": aligned_timestamp.isoformat(),
        "market_trend_label": (
            "market_uptrend"
            if market_trend_value is not None and market_trend_value >= 0.0
            else "market_downtrend"
            if market_trend_value is not None
            else None
        ),
        "market_trend_24h": market_trend_value,
        "market_volatility_label": (
            "high_volatility"
            if market_volatility_value is not None
            and market_vol_threshold is not None
            and market_volatility_value >= market_vol_threshold
            else "low_volatility"
            if market_volatility_value is not None and market_vol_threshold is not None
            else None
        ),
        "market_volatility_168h": market_volatility_value,
        "funding_level_label": (
            "high_funding"
            if funding_level_value is not None
            and funding_level_threshold is not None
            and funding_level_value >= funding_level_threshold
            else "low_funding"
            if funding_level_value is not None and funding_level_threshold is not None
            else None
        ),
        "funding_level_72h": funding_level_value,
        "funding_dispersion_label": (
            "funding_dispersed"
            if funding_dispersion_value is not None
            and funding_threshold is not None
            and funding_dispersion_value >= funding_threshold
            else "funding_compressed"
            if funding_dispersion_value is not None and funding_threshold is not None
            else None
        ),
        "funding_dispersion_72h": funding_dispersion_value,
        "breadth_label": (
            "broad_participation"
            if breadth_value is not None
            and breadth_threshold is not None
            and breadth_value >= breadth_threshold
            else "weak_participation"
            if breadth_value is not None and breadth_threshold is not None
            else None
        ),
        "breadth_24h": breadth_value,
        "co_movement_label": (
            "high_co_movement"
            if co_movement_value is not None
            and co_movement_threshold is not None
            and co_movement_value >= co_movement_threshold
            else "low_co_movement"
            if co_movement_value is not None and co_movement_threshold is not None
            else None
        ),
        "co_movement_72h": co_movement_value,
        "concentration_label": (
            "concentrated"
            if concentration_value is not None
            and concentration_threshold is not None
            and concentration_value >= concentration_threshold
            else "diversified"
            if concentration_value is not None and concentration_threshold is not None
            else None
        ),
        "concentration": concentration_value,
        "position_direction": position_direction,
        "position_structure_label": position_direction,
        "gross_exposure": gross_exposure_value,
        "net_exposure": net_exposure_value,
        "active_asset_count": active_asset_count_value,
    }
    if "pair_volatility" in regime_state:
        pair_volatility_value = _safe_float(regime_state["pair_volatility"].get(aligned_timestamp))
        pair_correlation_value = _safe_float(regime_state["pair_correlation"].get(aligned_timestamp))
        pair_direction_value = _safe_float(regime_state["pair_direction"].get(aligned_timestamp))
        pair_vol_threshold = thresholds.get("pair_volatility_median")
        correlation_threshold = thresholds.get("pair_correlation_median")
        snapshot.update(
            {
                "pair_volatility_label": (
                    "high_volatility"
                    if pair_volatility_value is not None
                    and pair_vol_threshold is not None
                    and pair_volatility_value >= pair_vol_threshold
                    else "low_volatility"
                    if pair_volatility_value is not None and pair_vol_threshold is not None
                    else None
                ),
                "pair_volatility_72h": pair_volatility_value,
                "pair_correlation_label": (
                    "high_correlation"
                    if pair_correlation_value is not None
                    and correlation_threshold is not None
                    and pair_correlation_value >= correlation_threshold
                    else "low_correlation"
                    if pair_correlation_value is not None and correlation_threshold is not None
                    else None
                ),
                "pair_correlation_72h": pair_correlation_value,
                "pair_direction_label": (
                    "asset_1_leading"
                    if pair_direction_value is not None and pair_direction_value >= 0.0
                    else "asset_2_leading"
                    if pair_direction_value is not None
                    else None
                ),
                "pair_direction_24h": pair_direction_value,
            }
        )
    return snapshot


def _pair_trade_episodes_with_regime(
    *,
    target_weights: pd.DataFrame,
    returns: pd.Series,
    regime_state: dict[str, Any],
) -> list[dict[str, Any]]:
    episodes = _pair_position_episodes(target_weights=target_weights, returns=returns)
    annotated: list[dict[str, Any]] = []
    for episode in episodes:
        start_timestamp = episode.get("start_timestamp")
        end_timestamp = episode.get("end_timestamp")
        annotated.append(
            {
                **episode,
                "entry_regime": _pair_regime_snapshot(
                    regime_state=regime_state,
                    timestamp=start_timestamp,
                    target_weights=target_weights,
                ),
                "exit_regime": _pair_regime_snapshot(
                    regime_state=regime_state,
                    timestamp=end_timestamp,
                    target_weights=target_weights,
                ),
            }
        )
    return annotated


def _pair_regime_diagnostics(
    *,
    prices: pd.DataFrame,
    target_weights: pd.DataFrame,
    funding_rates: pd.DataFrame | None,
    returns: pd.Series,
) -> dict[str, Any]:
    regime_state = _pair_regime_state(
        prices=prices,
        target_weights=target_weights,
        funding_rates=funding_rates,
    )
    if not regime_state.get("available"):
        return {"available": False}
    thresholds = dict(regime_state.get("thresholds") or {})
    bar_slices = {
        "market_trend": [
            _slice_performance_stats(
                returns=returns,
                gross_exposure=regime_state["gross_exposure"],
                mask=regime_state["market_trend"] >= 0.0,
                label="market_uptrend",
            ),
            _slice_performance_stats(
                returns=returns,
                gross_exposure=regime_state["gross_exposure"],
                mask=regime_state["market_trend"] < 0.0,
                label="market_downtrend",
            ),
        ],
        "market_volatility": [
            _slice_performance_stats(
                returns=returns,
                gross_exposure=regime_state["gross_exposure"],
                mask=regime_state["market_volatility"] >= float(thresholds["market_volatility_median"])
                if thresholds.get("market_volatility_median") is not None
                else pd.Series(False, index=prices.index),
                label="high_volatility",
            ),
            _slice_performance_stats(
                returns=returns,
                gross_exposure=regime_state["gross_exposure"],
                mask=regime_state["market_volatility"] < float(thresholds["market_volatility_median"])
                if thresholds.get("market_volatility_median") is not None
                else pd.Series(False, index=prices.index),
                label="low_volatility",
            ),
        ],
        "funding_level": [
            _slice_performance_stats(
                returns=returns,
                gross_exposure=regime_state["gross_exposure"],
                mask=regime_state["funding_level"] >= float(thresholds["funding_level_median"])
                if thresholds.get("funding_level_median") is not None
                else pd.Series(False, index=prices.index),
                label="high_funding",
            ),
            _slice_performance_stats(
                returns=returns,
                gross_exposure=regime_state["gross_exposure"],
                mask=regime_state["funding_level"] < float(thresholds["funding_level_median"])
                if thresholds.get("funding_level_median") is not None
                else pd.Series(False, index=prices.index),
                label="low_funding",
            ),
        ],
        "funding_dispersion": [
            _slice_performance_stats(
                returns=returns,
                gross_exposure=regime_state["gross_exposure"],
                mask=regime_state["funding_dispersion"] >= float(thresholds["funding_dispersion_median"])
                if thresholds.get("funding_dispersion_median") is not None
                else pd.Series(False, index=prices.index),
                label="funding_dispersed",
            ),
            _slice_performance_stats(
                returns=returns,
                gross_exposure=regime_state["gross_exposure"],
                mask=regime_state["funding_dispersion"] < float(thresholds["funding_dispersion_median"])
                if thresholds.get("funding_dispersion_median") is not None
                else pd.Series(False, index=prices.index),
                label="funding_compressed",
            ),
        ],
        "breadth": [
            _slice_performance_stats(
                returns=returns,
                gross_exposure=regime_state["gross_exposure"],
                mask=regime_state["breadth"] >= float(thresholds["breadth_median"])
                if thresholds.get("breadth_median") is not None
                else pd.Series(False, index=prices.index),
                label="broad_participation",
            ),
            _slice_performance_stats(
                returns=returns,
                gross_exposure=regime_state["gross_exposure"],
                mask=regime_state["breadth"] < float(thresholds["breadth_median"])
                if thresholds.get("breadth_median") is not None
                else pd.Series(False, index=prices.index),
                label="weak_participation",
            ),
        ],
        "co_movement": [
            _slice_performance_stats(
                returns=returns,
                gross_exposure=regime_state["gross_exposure"],
                mask=regime_state["co_movement"] >= float(thresholds["co_movement_median"])
                if thresholds.get("co_movement_median") is not None
                else pd.Series(False, index=prices.index),
                label="high_co_movement",
            ),
            _slice_performance_stats(
                returns=returns,
                gross_exposure=regime_state["gross_exposure"],
                mask=regime_state["co_movement"] < float(thresholds["co_movement_median"])
                if thresholds.get("co_movement_median") is not None
                else pd.Series(False, index=prices.index),
                label="low_co_movement",
            ),
        ],
        "concentration": [
            _slice_performance_stats(
                returns=returns,
                gross_exposure=regime_state["gross_exposure"],
                mask=regime_state["concentration"] >= float(thresholds["concentration_median"])
                if thresholds.get("concentration_median") is not None
                else pd.Series(False, index=prices.index),
                label="concentrated",
            ),
            _slice_performance_stats(
                returns=returns,
                gross_exposure=regime_state["gross_exposure"],
                mask=regime_state["concentration"] < float(thresholds["concentration_median"])
                if thresholds.get("concentration_median") is not None
                else pd.Series(False, index=prices.index),
                label="diversified",
            ),
        ],
    }
    if "pair_volatility" in regime_state:
        bar_slices["pair_volatility"] = [
            _slice_performance_stats(
                returns=returns,
                gross_exposure=regime_state["gross_exposure"],
                mask=regime_state["pair_volatility"] >= float(thresholds["pair_volatility_median"])
                if thresholds.get("pair_volatility_median") is not None
                else pd.Series(False, index=prices.index),
                label="high_volatility",
            ),
            _slice_performance_stats(
                returns=returns,
                gross_exposure=regime_state["gross_exposure"],
                mask=regime_state["pair_volatility"] < float(thresholds["pair_volatility_median"])
                if thresholds.get("pair_volatility_median") is not None
                else pd.Series(False, index=prices.index),
                label="low_volatility",
            ),
        ]
        bar_slices["pair_correlation"] = [
            _slice_performance_stats(
                returns=returns,
                gross_exposure=regime_state["gross_exposure"],
                mask=regime_state["pair_correlation"] >= float(thresholds["pair_correlation_median"])
                if thresholds.get("pair_correlation_median") is not None
                else pd.Series(False, index=prices.index),
                label="high_correlation",
            ),
            _slice_performance_stats(
                returns=returns,
                gross_exposure=regime_state["gross_exposure"],
                mask=regime_state["pair_correlation"] < float(thresholds["pair_correlation_median"])
                if thresholds.get("pair_correlation_median") is not None
                else pd.Series(False, index=prices.index),
                label="low_correlation",
            ),
        ]
        bar_slices["pair_direction"] = [
            _slice_performance_stats(
                returns=returns,
                gross_exposure=regime_state["gross_exposure"],
                mask=regime_state["pair_direction"] >= 0.0,
                label="asset_1_leading",
            ),
            _slice_performance_stats(
                returns=returns,
                gross_exposure=regime_state["gross_exposure"],
                mask=regime_state["pair_direction"] < 0.0,
                label="asset_2_leading",
            ),
        ]
    return {
        "available": True,
        "asset_1_symbol": regime_state.get("asset_1_symbol"),
        "asset_2_symbol": regime_state.get("asset_2_symbol"),
        "thresholds": thresholds,
        "bar_slices": bar_slices,
        "holding_period_buckets": _holding_period_buckets(target_weights, returns),
    }


def _serialize_canonical_run(
    *,
    result: Any,
    target_weights: pd.DataFrame,
    visual_split: dict[str, Any],
    evaluation_windows: list[dict[str, Any]],
    regime_diagnostics: dict[str, Any],
    regime_state: dict[str, Any],
    trade_episodes: list[dict[str, Any]],
) -> dict[str, Any]:
    equity_curve = result.equity_curve.astype(float)
    drawdown_curve = equity_curve.div(equity_curve.cummax()).sub(1.0).fillna(0.0)
    return {
        "window": "full",
        "leverage": 1.0,
        "visual_split": visual_split,
        "evaluation_windows": evaluation_windows,
        "equity_curve": _serialize_series(equity_curve),
        "returns": _serialize_series(result.returns.astype(float)),
        "drawdown_curve": _serialize_series(drawdown_curve),
        "metrics_by_period": _serialize_metrics_frame(result.metrics_by_period),
        "target_weights": _serialize_weight_changes(target_weights),
        "trades": _serialize_trades(
            result.trades,
            regime_state=regime_state,
            target_weights=target_weights,
        ),
        "trade_count": len(result.trades),
        "trade_episodes": trade_episodes,
        "regime_diagnostics": regime_diagnostics,
        "liquidated": bool(result.liquidated),
        "liquidation_timestamp": (
            result.liquidation_timestamp.isoformat()
            if result.liquidation_timestamp is not None
            else None
        ),
    }


def _pre_audit_drawdown_pack(
    *,
    canonical_run: dict[str, Any],
    target_weights: pd.DataFrame,
    signal_score: pd.DataFrame | None,
    signal_components: dict[str, pd.DataFrame] | None,
    end_idx: int | None,
) -> dict[str, Any]:
    def _empty_pack(
        *,
        bars: int,
        start_timestamp: Any = None,
        trough_timestamp: Any = None,
        equity_peak: float | None = None,
        equity_trough: float | None = None,
        dominant_direction: str | None = "flat",
    ) -> dict[str, Any]:
        return {
            "start_timestamp": (str(start_timestamp) if start_timestamp is not None else None),
            "trough_timestamp": (str(trough_timestamp) if trough_timestamp is not None else None),
            "bars": int(max(bars, 0)),
            "drawdown": 0.0,
            "equity_peak": _safe_float(equity_peak),
            "equity_trough": _safe_float(equity_trough),
            "dominant_position_direction": dominant_direction,
            "long_bar_fraction": 0.0,
            "short_bar_fraction": 0.0,
            "flat_bar_fraction": 1.0 if bars > 0 else None,
            "signal_story": {},
            "top_feature_contributors": [],
        }

    equity_payload = canonical_run.get("equity_curve")
    drawdown_payload = canonical_run.get("drawdown_curve")
    index_values = list((equity_payload or {}).get("index") or [])
    equity_values = list((equity_payload or {}).get("values") or [])
    drawdown_values = list((drawdown_payload or {}).get("values") or [])
    limit = len(equity_values) if end_idx is None else max(0, min(len(equity_values), int(end_idx)))
    if limit < 2:
        start_timestamp = index_values[0] if index_values else None
        last_timestamp = index_values[limit - 1] if limit and len(index_values) >= limit else start_timestamp
        last_equity = equity_values[limit - 1] if limit and len(equity_values) >= limit else None
        return _empty_pack(
            bars=limit,
            start_timestamp=start_timestamp,
            trough_timestamp=last_timestamp,
            equity_peak=last_equity,
            equity_trough=last_equity,
        )

    index_values = index_values[:limit]
    equity_values = equity_values[:limit]
    drawdown_values = drawdown_values[:limit]
    if not drawdown_values:
        return _empty_pack(
            bars=limit,
            start_timestamp=index_values[0] if index_values else None,
            trough_timestamp=index_values[-1] if index_values else None,
            equity_peak=equity_values[-1] if equity_values else None,
            equity_trough=equity_values[-1] if equity_values else None,
        )

    trough_idx = int(np.argmin(drawdown_values))
    trough_drawdown = _safe_float(drawdown_values[trough_idx])
    if trough_drawdown is None or trough_drawdown >= 0.0:
        flat_equity = equity_values[trough_idx] if equity_values else None
        return _empty_pack(
            bars=limit,
            start_timestamp=index_values[0] if index_values else None,
            trough_timestamp=index_values[trough_idx] if index_values else None,
            equity_peak=flat_equity,
            equity_trough=flat_equity,
        )
    peak_idx = int(np.argmax(equity_values[: trough_idx + 1]))
    if peak_idx >= trough_idx:
        flat_equity = equity_values[trough_idx] if equity_values else None
        return _empty_pack(
            bars=limit,
            start_timestamp=index_values[0] if index_values else None,
            trough_timestamp=index_values[trough_idx] if index_values else None,
            equity_peak=flat_equity,
            equity_trough=flat_equity,
        )

    window_bars = max(1, trough_idx - peak_idx + 1)
    positions = target_weights.iloc[:limit] if not target_weights.empty else pd.DataFrame()
    window_positions = positions.iloc[peak_idx : trough_idx + 1] if not positions.empty else positions
    direction_counts: dict[str, int] = {}
    if not window_positions.empty:
        direction_series = pd.Series(
            [_row_direction_label(row) for _, row in window_positions.iterrows()],
            index=window_positions.index,
            dtype=object,
        )
        for direction, count in direction_series.value_counts().to_dict().items():
            if str(direction) == "flat":
                continue
            direction_counts[str(direction)] = int(count)
    dominant_direction = (
        max(direction_counts.items(), key=lambda item: item[1])[0]
        if direction_counts
        else None
    )
    active_mask = (
        window_positions.abs().sum(axis=1).gt(1e-9)
        if not window_positions.empty
        else pd.Series(dtype=bool)
    )
    long_bars = int(
        (window_positions.sum(axis=1) > 0.0).sum()
        if not window_positions.empty
        else 0
    )
    short_bars = int(
        (window_positions.sum(axis=1) < 0.0).sum()
        if not window_positions.empty
        else 0
    )
    flat_bars = int((~active_mask).sum()) if not active_mask.empty else 0

    signal_story: dict[str, Any] = {}
    if signal_score is not None and not signal_score.empty and not window_positions.empty:
        score_frame = signal_score.iloc[:limit].apply(pd.to_numeric, errors="coerce").fillna(0.0)
        score_window = score_frame.iloc[peak_idx : trough_idx + 1]
        aligned_values: list[float] = []
        support_scores: list[float] = []
        trough_support = None
        if score_frame.shape[1] == 1 and window_positions.shape[1] >= 2:
            primary_sign = np.sign(
                pd.to_numeric(window_positions.iloc[:, 0], errors="coerce").fillna(0.0)
            )
            support_series = score_window.iloc[:, 0].mul(primary_sign.loc[score_window.index], fill_value=0.0)
            active_series = primary_sign.loc[score_window.index].abs() > 0
            if bool(active_series.any()):
                active_support = support_series[active_series]
                support_scores.extend(float(value) for value in active_support.tolist())
                aligned_values.extend(float(value > 0.0) for value in active_support.tolist())
            trough_timestamp = score_frame.index[trough_idx]
            trough_support = _safe_float(support_series.get(trough_timestamp), default=None)
        else:
            position_sign = np.sign(window_positions.reindex(columns=score_window.columns).fillna(0.0))
            for timestamp in score_window.index:
                active_cols = list(score_window.columns[position_sign.loc[timestamp].abs() > 0])
                if not active_cols:
                    continue
                signed_support = (
                    score_window.loc[timestamp, active_cols] * position_sign.loc[timestamp, active_cols]
                )
                support_scores.append(float(signed_support.mean()))
                aligned_values.append(float((signed_support > 0.0).mean()))
            trough_timestamp = score_frame.index[trough_idx]
            active_cols = list(score_frame.columns[position_sign.loc[trough_timestamp].abs() > 0])
            if active_cols:
                trough_support = _safe_float(
                    float(
                        (
                            score_frame.loc[trough_timestamp, active_cols]
                            * position_sign.loc[trough_timestamp, active_cols]
                        ).mean()
                    )
                )
        signal_story = {
            "window_median_score": _safe_float(float(np.median(support_scores)) if support_scores else None),
            "trough_score": trough_support,
            "aligned_with_position_fraction": _safe_float(
                float(np.mean(aligned_values)) if aligned_values else None
            ),
        }

    feature_story: list[dict[str, Any]] = []
    for feature, frame in dict(signal_components or {}).items():
        if frame is None or frame.empty or window_positions.empty:
            continue
        component = frame.iloc[:limit].apply(pd.to_numeric, errors="coerce").fillna(0.0)
        component_window = component.iloc[peak_idx : trough_idx + 1]
        comp_aligned_values: list[float] = []
        comp_support_scores: list[float] = []
        trough_component = None
        if component.shape[1] == 1 and window_positions.shape[1] >= 2:
            primary_sign = np.sign(
                pd.to_numeric(window_positions.iloc[:, 0], errors="coerce").fillna(0.0)
            )
            support_series = component_window.iloc[:, 0].mul(primary_sign.loc[component_window.index], fill_value=0.0)
            active_series = primary_sign.loc[component_window.index].abs() > 0
            if bool(active_series.any()):
                active_support = support_series[active_series]
                comp_support_scores.extend(float(value) for value in active_support.tolist())
                comp_aligned_values.extend(float(value > 0.0) for value in active_support.tolist())
            trough_timestamp = component.index[trough_idx]
            trough_component = _safe_float(support_series.get(trough_timestamp), default=None)
        else:
            position_sign = np.sign(window_positions.reindex(columns=component_window.columns).fillna(0.0))
            for timestamp in component_window.index:
                active_cols = list(component_window.columns[position_sign.loc[timestamp].abs() > 0])
                if not active_cols:
                    continue
                signed_support = (
                    component_window.loc[timestamp, active_cols]
                    * position_sign.loc[timestamp, active_cols]
                )
                comp_support_scores.append(float(signed_support.mean()))
                comp_aligned_values.append(float((signed_support > 0.0).mean()))
            trough_timestamp = component.index[trough_idx]
            active_cols = list(component.columns[position_sign.loc[trough_timestamp].abs() > 0])
            if active_cols:
                trough_component = _safe_float(
                    float(
                        (
                            component.loc[trough_timestamp, active_cols]
                            * position_sign.loc[trough_timestamp, active_cols]
                        ).mean()
                    )
                )
        feature_story.append(
            {
                "feature": str(feature),
                "window_median_component": _safe_float(
                    float(np.median(comp_support_scores)) if comp_support_scores else None
                ),
                "trough_component": trough_component,
                "aligned_with_position_fraction": _safe_float(
                    float(np.mean(comp_aligned_values)) if comp_aligned_values else None
                ),
            }
        )
    feature_story.sort(
        key=lambda row: abs(_safe_float(row.get("window_median_component")) or 0.0),
        reverse=True,
    )

    return {
        "start_timestamp": str(index_values[peak_idx]),
        "trough_timestamp": str(index_values[trough_idx]),
        "bars": window_bars,
        "drawdown": trough_drawdown,
        "equity_peak": _safe_float(equity_values[peak_idx]),
        "equity_trough": _safe_float(equity_values[trough_idx]),
        "dominant_position_direction": dominant_direction,
        "long_bar_fraction": _safe_float(long_bars / window_bars),
        "short_bar_fraction": _safe_float(short_bars / window_bars),
        "flat_bar_fraction": _safe_float(flat_bars / window_bars),
        "signal_story": signal_story,
        "top_feature_contributors": feature_story[:4],
    }


def _series_from_payload(
    payload: dict[str, Any] | None,
    *,
    end_idx: int | None = None,
) -> pd.Series:
    index_values = list((payload or {}).get("index") or [])
    raw_values = list((payload or {}).get("values") or [])
    limit = len(raw_values) if end_idx is None else max(0, min(len(raw_values), int(end_idx)))
    if limit <= 0:
        return pd.Series(dtype=float)
    index = pd.to_datetime(index_values[:limit], errors="coerce")
    values = pd.to_numeric(pd.Series(raw_values[:limit], dtype="float64"), errors="coerce")
    series = pd.Series(values.to_numpy(), index=index)
    series = series[~series.index.isna()]
    return series.sort_index()


def _pre_audit_trade_episodes_from_canonical(canonical_run: dict[str, Any]) -> list[dict[str, Any]]:
    episodes = list(canonical_run.get("trade_episodes") or [])
    if not episodes:
        return []
    visual_split = dict(canonical_run.get("visual_split") or {})
    audit_start = None
    for window in list(visual_split.get("ranges") or []):
        if str(window.get("kind") or "") == "audit_holdout":
            audit_start = pd.Timestamp(window.get("start_timestamp"))
            break
    if audit_start is None:
        return [episode for episode in episodes if isinstance(episode, dict)]

    filtered: list[dict[str, Any]] = []
    for episode in episodes:
        if not isinstance(episode, dict):
            continue
        end_timestamp = episode.get("end_timestamp") or episode.get("start_timestamp")
        if not end_timestamp:
            continue
        if pd.Timestamp(end_timestamp) >= audit_start:
            continue
        filtered.append(episode)
    return filtered


def _episode_direction_counts(trade_episodes: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for episode in trade_episodes:
        direction = str(episode.get("direction") or "").strip()
        if not direction:
            continue
        counts[direction] = counts.get(direction, 0) + 1
    return counts


def _trade_regime_pack(trade_episodes: list[dict[str, Any]]) -> dict[str, Any]:
    if not trade_episodes:
        return {}
    label_keys: list[str] = []
    for episode in trade_episodes:
        entry_regime = dict(episode.get("entry_regime") or {})
        label_keys.extend(
            key for key, value in entry_regime.items() if key.endswith("_label") and value
        )
    dimensions = {
        key.removesuffix("_label"): key
        for key in sorted(set(label_keys))
    }
    regime_pack: dict[str, Any] = {}
    for dimension, label_key in dimensions.items():
        rows: list[dict[str, Any]] = []
        by_label: dict[str, list[dict[str, Any]]] = {}
        for episode in trade_episodes:
            label = str((episode.get("entry_regime") or {}).get(label_key) or "").strip()
            if not label:
                continue
            by_label.setdefault(label, []).append(episode)
        for label, matched in by_label.items():
            returns = [
                float(episode["total_return"])
                for episode in matched
                if episode.get("total_return") is not None
            ]
            bars = [
                float(episode["bars"])
                for episode in matched
                if _safe_float(episode.get("bars"), default=None) is not None
            ]
            rows.append(
                {
                    "label": label,
                    "trade_count": len(matched),
                    "win_rate": _safe_float(
                        sum(1 for value in returns if value > 0.0) / len(returns)
                        if returns
                        else None
                    ),
                    "avg_return": _safe_float(sum(returns) / len(returns) if returns else None),
                    "median_return": _safe_float(float(np.median(returns)) if returns else None),
                    "median_hold_bars": _safe_float(float(np.median(bars)) if bars else None),
                    "direction_counts": _episode_direction_counts(matched),
                }
            )
        rows.sort(
            key=lambda row: (
                float(row.get("avg_return") or -1e9),
                int(row.get("trade_count") or 0),
            ),
            reverse=True,
        )
        if rows:
            regime_pack[dimension] = {
                "rows": rows,
                "best_label": rows[0]["label"],
                "worst_label": min(
                    rows,
                    key=lambda row: float(row.get("avg_return") or 1e9),
                )["label"],
            }
    return regime_pack


def _window_regime_summary(
    *,
    regime_state: dict[str, Any],
    start_timestamp: pd.Timestamp,
    end_timestamp: pd.Timestamp,
) -> dict[str, Any]:
    if not regime_state.get("available"):
        return {}
    index_value = regime_state.get("index")
    index = pd.DatetimeIndex(index_value if index_value is not None else [])
    if index.empty:
        return {}
    mask = (index >= start_timestamp) & (index <= end_timestamp)
    if not bool(mask.any()):
        return {}

    def _mean_value(series: pd.Series) -> float | None:
        values = pd.to_numeric(series.loc[mask], errors="coerce").dropna()
        if values.empty:
            return None
        return _safe_float(values.mean())

    thresholds = dict(regime_state.get("thresholds") or {})
    market_trend = _mean_value(regime_state["market_trend"])
    market_volatility = _mean_value(regime_state["market_volatility"])
    funding_level = _mean_value(regime_state["funding_level"])
    funding_dispersion = _mean_value(regime_state["funding_dispersion"])
    breadth = _mean_value(regime_state["breadth"])
    co_movement = _mean_value(regime_state["co_movement"])
    concentration = _mean_value(regime_state["concentration"])
    direction_series = pd.Series(regime_state["position_direction"], index=index).loc[mask]
    direction_counts = direction_series.value_counts().to_dict()
    dominant_position_direction = max(
        (
            (str(label), int(count))
            for label, count in direction_counts.items()
            if str(label) != "flat"
        ),
        key=lambda item: item[1],
        default=(None, 0),
    )[0]

    payload = {
        "market_trend_label": (
            "market_uptrend"
            if market_trend is not None and market_trend >= 0.0
            else "market_downtrend"
            if market_trend is not None
            else None
        ),
        "avg_market_trend_24h": market_trend,
        "market_volatility_label": (
            "high_volatility"
            if market_volatility is not None
            and thresholds.get("market_volatility_median") is not None
            and market_volatility >= float(thresholds["market_volatility_median"])
            else "low_volatility"
            if market_volatility is not None and thresholds.get("market_volatility_median") is not None
            else None
        ),
        "avg_market_volatility_168h": market_volatility,
        "funding_level_label": (
            "high_funding"
            if funding_level is not None
            and thresholds.get("funding_level_median") is not None
            and funding_level >= float(thresholds["funding_level_median"])
            else "low_funding"
            if funding_level is not None and thresholds.get("funding_level_median") is not None
            else None
        ),
        "avg_funding_level_72h": funding_level,
        "funding_dispersion_label": (
            "funding_dispersed"
            if funding_dispersion is not None
            and thresholds.get("funding_dispersion_median") is not None
            and funding_dispersion >= float(thresholds["funding_dispersion_median"])
            else "funding_compressed"
            if funding_dispersion is not None and thresholds.get("funding_dispersion_median") is not None
            else None
        ),
        "avg_funding_dispersion_72h": funding_dispersion,
        "breadth_label": (
            "broad_participation"
            if breadth is not None
            and thresholds.get("breadth_median") is not None
            and breadth >= float(thresholds["breadth_median"])
            else "weak_participation"
            if breadth is not None and thresholds.get("breadth_median") is not None
            else None
        ),
        "avg_breadth_24h": breadth,
        "co_movement_label": (
            "high_co_movement"
            if co_movement is not None
            and thresholds.get("co_movement_median") is not None
            and co_movement >= float(thresholds["co_movement_median"])
            else "low_co_movement"
            if co_movement is not None and thresholds.get("co_movement_median") is not None
            else None
        ),
        "avg_co_movement_72h": co_movement,
        "concentration_label": (
            "concentrated"
            if concentration is not None
            and thresholds.get("concentration_median") is not None
            and concentration >= float(thresholds["concentration_median"])
            else "diversified"
            if concentration is not None and thresholds.get("concentration_median") is not None
            else None
        ),
        "avg_concentration": concentration,
        "dominant_position_direction": dominant_position_direction,
    }
    if "pair_correlation" in regime_state:
        pair_volatility = _mean_value(regime_state["pair_volatility"])
        pair_correlation = _mean_value(regime_state["pair_correlation"])
        pair_direction = _mean_value(regime_state["pair_direction"])
        payload.update(
            {
                "pair_volatility_label": (
                    "high_volatility"
                    if pair_volatility is not None
                    and thresholds.get("pair_volatility_median") is not None
                    and pair_volatility >= float(thresholds["pair_volatility_median"])
                    else "low_volatility"
                    if pair_volatility is not None and thresholds.get("pair_volatility_median") is not None
                    else None
                ),
                "avg_pair_volatility_72h": pair_volatility,
                "pair_correlation_label": (
                    "high_correlation"
                    if pair_correlation is not None
                    and thresholds.get("pair_correlation_median") is not None
                    and pair_correlation >= float(thresholds["pair_correlation_median"])
                    else "low_correlation"
                    if pair_correlation is not None and thresholds.get("pair_correlation_median") is not None
                    else None
                ),
                "avg_pair_correlation_72h": pair_correlation,
                "pair_direction_label": (
                    "asset_1_leading"
                    if pair_direction is not None and pair_direction >= 0.0
                    else "asset_2_leading"
                    if pair_direction is not None
                    else None
                ),
                "avg_pair_direction_24h": pair_direction,
            }
        )
    return payload


def _equity_window_trade_stats(
    *,
    trade_episodes: list[dict[str, Any]],
    start_timestamp: pd.Timestamp,
    end_timestamp: pd.Timestamp,
) -> dict[str, Any]:
    matched: list[dict[str, Any]] = []
    for episode in trade_episodes:
        start = episode.get("start_timestamp")
        if not start:
            continue
        timestamp = pd.Timestamp(start)
        if start_timestamp <= timestamp <= end_timestamp:
            matched.append(episode)
    returns = [
        float(episode["total_return"])
        for episode in matched
        if episode.get("total_return") is not None
    ]
    bars = [
        float(episode["bars"])
        for episode in matched
        if _safe_float(episode.get("bars"), default=None) is not None
    ]
    days = max(1.0, (end_timestamp - start_timestamp).total_seconds() / 86400.0)
    direction_counts = _episode_direction_counts(matched)
    dominant_direction = (
        max(direction_counts.items(), key=lambda item: item[1])[0]
        if direction_counts
        else None
    )
    return {
        "trade_count": len(matched),
        "entries_per_day": _safe_float(len(matched) / days),
        "win_rate": _safe_float(
            sum(1 for value in returns if value > 0.0) / len(returns)
            if returns
            else None
        ),
        "avg_return": _safe_float(sum(returns) / len(returns) if returns else None),
        "median_return": _safe_float(float(np.median(returns)) if returns else None),
        "median_hold_bars": _safe_float(float(np.median(bars)) if bars else None),
        "dominant_direction": dominant_direction,
        "direction_counts": direction_counts,
    }


def _pre_audit_equity_shift_pack(
    *,
    equity_curve: pd.Series,
    trade_episodes: list[dict[str, Any]],
    regime_state: dict[str, Any],
) -> dict[str, Any]:
    clean = pd.to_numeric(equity_curve, errors="coerce").dropna()
    if clean.shape[0] < 2:
        return {}
    drawdown = clean.div(clean.cummax()).sub(1.0)
    peak_timestamp = clean.idxmax()
    trough_timestamp = drawdown.idxmin()
    drawdown_start = clean.loc[:trough_timestamp].idxmax()
    pre_peak = _equity_window_trade_stats(
        trade_episodes=trade_episodes,
        start_timestamp=clean.index.min(),
        end_timestamp=peak_timestamp,
    )
    post_peak = _equity_window_trade_stats(
        trade_episodes=trade_episodes,
        start_timestamp=peak_timestamp,
        end_timestamp=clean.index.max(),
    )
    drawdown_window = _equity_window_trade_stats(
        trade_episodes=trade_episodes,
        start_timestamp=drawdown_start,
        end_timestamp=trough_timestamp,
    )
    drawdown_window["regime"] = _window_regime_summary(
        regime_state=regime_state,
        start_timestamp=drawdown_start,
        end_timestamp=trough_timestamp,
    )
    return {
        "peak_timestamp": peak_timestamp.isoformat(),
        "peak_equity": _safe_float(clean.loc[peak_timestamp]),
        "max_drawdown_start": drawdown_start.isoformat(),
        "max_drawdown_end": trough_timestamp.isoformat(),
        "max_drawdown": _safe_float(drawdown.loc[trough_timestamp]),
        "pre_peak": pre_peak,
        "post_peak": post_peak,
        "drawdown_window": drawdown_window,
    }


def _pre_audit_time_bin_pack(
    *,
    returns: pd.Series,
    trade_episodes: list[dict[str, Any]],
    regime_state: dict[str, Any],
) -> dict[str, Any]:
    clean = pd.to_numeric(returns, errors="coerce").dropna()
    if clean.empty:
        return {}
    daily_returns = clean.resample("1D").apply(lambda values: float((1.0 + values).prod() - 1.0))
    if daily_returns.shape[0] < 14:
        return {}

    def _window_payload(window_days: int) -> dict[str, Any] | None:
        if daily_returns.shape[0] < window_days:
            return None
        rolling = (1.0 + daily_returns.fillna(0.0)).rolling(window_days).apply(np.prod, raw=True) - 1.0
        rolling = rolling.dropna()
        if rolling.empty:
            return None
        best_end = rolling.idxmax()
        worst_end = rolling.idxmin()

        def _summary(end_timestamp: pd.Timestamp, label: str) -> dict[str, Any]:
            end_loc = int(daily_returns.index.get_loc(end_timestamp))
            start_loc = max(0, end_loc - window_days + 1)
            start_timestamp = pd.Timestamp(daily_returns.index[start_loc])
            trade_stats = _equity_window_trade_stats(
                trade_episodes=trade_episodes,
                start_timestamp=start_timestamp,
                end_timestamp=end_timestamp,
            )
            trade_stats["regime"] = _window_regime_summary(
                regime_state=regime_state,
                start_timestamp=start_timestamp,
                end_timestamp=end_timestamp,
            )
            return {
                "label": label,
                "start_timestamp": start_timestamp.isoformat(),
                "end_timestamp": end_timestamp.isoformat(),
                "window_days": window_days,
                "total_return": _safe_float(float(rolling.loc[end_timestamp])),
                **trade_stats,
            }

        return {
            "window_days": window_days,
            "best_window": _summary(best_end, "best"),
            "worst_window": _summary(worst_end, "worst"),
        }

    windows = [payload for payload in (_window_payload(14), _window_payload(30)) if payload]
    return {"windows": windows} if windows else {}


def _entry_feature_contributors(
    *,
    signal_components: dict[str, pd.DataFrame] | None,
    timestamp: Any,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for feature, frame in dict(signal_components or {}).items():
        if frame is None or frame.empty:
            continue
        aligned_timestamp = _lookup_timestamp(frame.index, timestamp)
        if aligned_timestamp is None:
            continue
        value = _safe_float(frame.iloc[:, 0].get(aligned_timestamp), default=None)
        if value is None:
            continue
        rows.append(
            {
                "feature": str(feature),
                "value": value,
                "abs_value": _safe_float(abs(value)),
            }
        )
    rows.sort(key=lambda row: abs(float(row.get("value") or 0.0)), reverse=True)
    return rows[:3]


def _pre_audit_exemplar_trades(
    *,
    trade_episodes: list[dict[str, Any]],
    signal_score: pd.DataFrame | None,
    signal_components: dict[str, pd.DataFrame] | None,
) -> dict[str, Any]:
    if not trade_episodes:
        return {}
    scored = [episode for episode in trade_episodes if episode.get("total_return") is not None]
    if not scored:
        return {}
    winners = sorted(scored, key=lambda episode: float(episode["total_return"]), reverse=True)[:2]
    losers = sorted(scored, key=lambda episode: float(episode["total_return"]))[:2]

    def _payload(episode: dict[str, Any]) -> dict[str, Any]:
        entry_timestamp = episode.get("start_timestamp")
        entry_score = None
        if signal_score is not None and not signal_score.empty and entry_timestamp:
            aligned_timestamp = _lookup_timestamp(signal_score.index, entry_timestamp)
            if aligned_timestamp is not None:
                entry_score = _safe_float(signal_score.iloc[:, 0].get(aligned_timestamp), default=None)
        return {
            "start_timestamp": entry_timestamp,
            "end_timestamp": episode.get("end_timestamp"),
            "direction": episode.get("direction"),
            "bars": _safe_float(episode.get("bars"), default=None),
            "total_return": _safe_float(episode.get("total_return"), default=None),
            "entry_score": entry_score,
            "entry_regime": dict(episode.get("entry_regime") or {}),
            "entry_feature_contributors": _entry_feature_contributors(
                signal_components=signal_components,
                timestamp=entry_timestamp,
            ),
        }

    return {
        "winners": [_payload(episode) for episode in winners],
        "losers": [_payload(episode) for episode in losers],
    }


def _pair_gate_diagnostics(
    *,
    signal_score: pd.DataFrame | None,
    target_weights: pd.DataFrame,
    compiled_metadata: dict[str, Any],
    end_idx: int | None,
    regime_gate_mask: pd.Series | None = None,
) -> dict[str, Any]:
    if signal_score is None or signal_score.empty or target_weights.empty:
        return {}
    limit = len(signal_score.index) if end_idx is None else max(0, min(len(signal_score.index), int(end_idx)))
    if limit <= 1:
        return {}
    score_frame = signal_score.iloc[:limit].apply(pd.to_numeric, errors="coerce").fillna(0.0)
    target_frame = target_weights.iloc[:limit].apply(pd.to_numeric, errors="coerce").fillna(0.0)
    score_sign = np.sign(score_frame)
    position_sign = np.sign(target_frame)
    active_mask = target_frame.abs().sum(axis=1) > 1e-9
    flat_mask = ~active_mask

    entry_abs_score = float(compiled_metadata.get("entry_abs_score", compiled_metadata.get("min_abs_score", 0.0)))
    exit_abs_score = float(compiled_metadata.get("exit_abs_score", max(0.0, entry_abs_score * 0.5)))
    flip_abs_score = float(compiled_metadata.get("flip_abs_score", entry_abs_score))
    score_entry_mask = score_frame.abs().ge(entry_abs_score)
    score_flip_mask = score_frame.abs().ge(flip_abs_score)
    score_exit_band = score_frame.abs().lt(exit_abs_score)

    pair_mode = score_frame.shape[1] == 1 and target_frame.shape[1] >= 2
    position_signature = pd.Series(
        [_row_position_signature(row) for _, row in target_frame.iterrows()],
        index=target_frame.index,
        dtype=object,
    )
    if pair_mode:
        active_score_signature = pd.Series(
            [
                ("long_asset_1_short_asset_2",)
                if float(row.iloc[0]) >= entry_abs_score
                else ("short_asset_1_long_asset_2",)
                if float(row.iloc[0]) <= -entry_abs_score
                else tuple()
                for _, row in score_frame.iterrows()
            ],
            index=score_frame.index,
            dtype=object,
        )
    else:
        active_score_signature = pd.Series(
            [
                tuple(
                    sorted(
                        (
                            str(column),
                            int(np.sign(value)),
                        )
                        for column, value in row.items()
                        if abs(float(value)) >= entry_abs_score
                    )
                )
                for _, row in score_frame.iterrows()
            ],
            index=score_frame.index,
            dtype=object,
        )
    score_flips = (
        active_score_signature != active_score_signature.shift(1)
    ) & active_score_signature.astype(bool) & active_score_signature.shift(1).astype(bool)
    position_flips = (
        position_signature != position_signature.shift(1)
    ) & position_signature.astype(bool) & position_signature.shift(1).astype(bool)

    aligned_active_fraction = None
    active_alignment: list[float] = []
    if pair_mode:
        primary_sign = np.sign(
            pd.to_numeric(target_frame.iloc[:, 0], errors="coerce").fillna(0.0)
        )
        aligned = score_sign.iloc[:, 0].mul(primary_sign, fill_value=0.0)
        active_alignment = [float(value > 0.0) for value in aligned[active_mask].tolist()]
    else:
        for timestamp in score_frame.index[active_mask]:
            active_cols = target_frame.columns[target_frame.loc[timestamp].abs() > 1e-9]
            if len(active_cols) == 0:
                continue
            aligned = (
                score_sign.loc[timestamp, active_cols]
                == position_sign.loc[timestamp, active_cols]
            )
            active_alignment.append(float(aligned.mean()))
    if active_alignment:
        aligned_active_fraction = _safe_float(float(np.mean(active_alignment)))

    bottleneck_tags: list[str] = []
    entry_fraction = float(score_entry_mask.any(axis=1).mean())
    active_fraction = float(active_mask.mean())
    position_flip_rate = float(position_flips.mean()) if len(position_flips.index) > 1 else 0.0
    if entry_fraction < 0.05:
        bottleneck_tags.append("sparse_entry_signal")
    if active_fraction < 0.10:
        bottleneck_tags.append("low_active_fraction")
    if position_flip_rate > 0.20:
        bottleneck_tags.append("high_position_flip_rate")
    if aligned_active_fraction is not None and aligned_active_fraction < 0.55:
        bottleneck_tags.append("weak_score_alignment")

    regime_gate_summary = None
    if regime_gate_mask is not None:
        gate_mask = regime_gate_mask.reindex(score_frame.index).ffill().fillna(False).astype(bool)
        regime_gate_summary = {
            "configured": True,
            "active_fraction": _safe_float(float(gate_mask.mean())),
            "blocked_while_flat_fraction": _safe_float(
                float((~gate_mask)[flat_mask].mean()) if bool(flat_mask.any()) else None
            ),
            "broken_while_active_fraction": _safe_float(
                float((~gate_mask)[active_mask].mean()) if bool(active_mask.any()) else None
            ),
            "exit_on_break": bool(
                dict(compiled_metadata.get("regime_gates") or {}).get("exit_on_break", True)
            ),
            "entry": list(dict(compiled_metadata.get("regime_gates") or {}).get("entry") or []),
        }
        active_frac_gate = _safe_float(regime_gate_summary.get("active_fraction"))
        if active_frac_gate is not None and active_frac_gate < 0.30:
            bottleneck_tags.append("restrictive_regime_gate")

    return {
        "policy": {
            "entry_abs_score": _safe_float(entry_abs_score),
            "exit_abs_score": _safe_float(exit_abs_score),
            "flip_abs_score": _safe_float(flip_abs_score),
            "max_holding_bars": int(compiled_metadata.get("max_holding_bars", 0) or 0),
            "cooldown_bars": int(compiled_metadata.get("cooldown_bars", 0) or 0),
            "signal_leverage_scale": _safe_float(compiled_metadata.get("signal_leverage_scale")),
        },
        "active_bar_fraction": _safe_float(active_fraction),
        "flat_bar_fraction": _safe_float(float(flat_mask.mean())),
        "entry_signal_bar_fraction": _safe_float(entry_fraction),
        "flip_signal_bar_fraction": _safe_float(float(score_flip_mask.any(axis=1).mean())),
        "inside_exit_band_fraction": _safe_float(float(score_exit_band.all(axis=1).mean())),
        "score_sign_flip_rate": _safe_float(float(score_flips.mean()) if len(score_flips.index) > 1 else 0.0),
        "position_flip_rate": _safe_float(position_flip_rate),
        "entry_signal_while_flat_fraction": _safe_float(
            float(score_entry_mask.any(axis=1)[flat_mask].mean()) if bool(flat_mask.any()) else None
        ),
        "score_alignment_when_active": aligned_active_fraction,
        "median_active_asset_count": _safe_float(
            float(
                (
                    pd.Series(2.0, index=target_frame.index)
                    if pair_mode
                    else target_frame.abs().gt(1e-9).sum(axis=1).astype(float)
                )[active_mask].median()
            )
            if bool(active_mask.any())
            else None
        ),
        "regime_gates": regime_gate_summary,
        "bottleneck_tags": bottleneck_tags,
    }


def _policy_context_from_metadata(compiled_metadata: dict[str, Any]) -> dict[str, Any]:
    policy = {
        "execution_profile": compiled_metadata.get("execution_profile"),
        "long_count": int(compiled_metadata.get("long_count", 0) or 0),
        "short_count": int(compiled_metadata.get("short_count", 0) or 0),
        "selection_count": int(compiled_metadata.get("selection_count", 0) or 0),
        "entry_abs_score": _safe_float(compiled_metadata.get("entry_abs_score"), default=None),
        "exit_abs_score": _safe_float(compiled_metadata.get("exit_abs_score"), default=None),
        "flip_abs_score": _safe_float(compiled_metadata.get("flip_abs_score"), default=None),
        "max_holding_bars": int(compiled_metadata.get("max_holding_bars", 0) or 0),
        "cooldown_bars": int(compiled_metadata.get("cooldown_bars", 0) or 0),
        "signal_leverage_scale": _safe_float(compiled_metadata.get("signal_leverage_scale"), default=None),
        "gross_target": _safe_float(compiled_metadata.get("gross_target"), default=None),
        "max_gross_target": _safe_float(compiled_metadata.get("max_gross_target"), default=None),
    }
    sweep = dict(compiled_metadata.get("pair_policy_sweep") or {})
    if sweep:
        policy["policy_sweep"] = {
            "applied": bool(sweep.get("applied")),
            "train_window_count": int(sweep.get("train_window_count", 0) or 0),
            "trial_count": int(sweep.get("trial_count", 0) or 0),
            "best_train_score": _safe_float(
                dict(sweep.get("best_train_summary") or {}).get("aggregate_score"),
                default=None,
            ),
            "best_train_return": _safe_float(
                dict(sweep.get("best_train_summary") or {}).get("median_total_return"),
                default=None,
            ),
        }
    return policy


def _pre_audit_context_pack(
    *,
    canonical_run: dict[str, Any],
    target_weights: pd.DataFrame,
    signal_score: pd.DataFrame | None,
    signal_components: dict[str, pd.DataFrame] | None,
    compiled_metadata: dict[str, Any],
    regime_gate_mask: pd.Series | None,
    regime_state: dict[str, Any],
    end_idx: int | None,
) -> dict[str, Any]:
    if not regime_state.get("available"):
        return {}
    trade_episodes = _pre_audit_trade_episodes_from_canonical(canonical_run)
    equity_curve = _series_from_payload(canonical_run.get("equity_curve"), end_idx=end_idx)
    returns = _series_from_payload(canonical_run.get("returns"), end_idx=end_idx)
    return {
        "trade_regime_pack": _trade_regime_pack(trade_episodes),
        "equity_shift_pack": _pre_audit_equity_shift_pack(
            equity_curve=equity_curve,
            trade_episodes=trade_episodes,
            regime_state=regime_state,
        ),
        "time_bin_pack": _pre_audit_time_bin_pack(
            returns=returns,
            trade_episodes=trade_episodes,
            regime_state=regime_state,
        ),
        "exemplar_trades": _pre_audit_exemplar_trades(
            trade_episodes=trade_episodes,
            signal_score=signal_score,
            signal_components=signal_components,
        ),
        "gate_diagnostics": _pair_gate_diagnostics(
            signal_score=signal_score,
            target_weights=target_weights,
            compiled_metadata=compiled_metadata,
            end_idx=end_idx,
            regime_gate_mask=regime_gate_mask,
        ),
        "policy_context": _policy_context_from_metadata(compiled_metadata),
    }


def _series_has_finite_values(payload: dict[str, Any] | None) -> bool:
    values = list((payload or {}).get("values") or [])
    return any(value is not None for value in values)


def _series_total_return(
    payload: dict[str, Any] | None,
    *,
    end_idx: int | None = None,
) -> float | None:
    values = _series_values(payload, end_idx=end_idx)
    if len(values) < 2 or values[0] == 0.0:
        return None
    return float(values[-1] / values[0] - 1.0)


def _series_last_value(
    payload: dict[str, Any] | None,
    *,
    end_idx: int | None = None,
) -> float | None:
    values = _series_values(payload, end_idx=end_idx)
    if not values:
        return None
    return float(values[-1])


def _series_min_value(
    payload: dict[str, Any] | None,
    *,
    end_idx: int | None = None,
) -> float | None:
    values = _series_values(payload, end_idx=end_idx)
    if not values:
        return None
    return float(min(values))


def _series_values(
    payload: dict[str, Any] | None,
    *,
    end_idx: int | None = None,
) -> list[float]:
    values_raw = list((payload or {}).get("values") or [])
    if end_idx is not None:
        values_raw = values_raw[: max(0, min(len(values_raw), int(end_idx)))]
    return [float(value) for value in values_raw if value is not None]


def _pre_audit_end_idx(
    visual_split: dict[str, Any],
    series_payload: dict[str, Any] | None,
) -> int | None:
    for row in list((visual_split or {}).get("ranges") or []):
        if str(row.get("kind") or "") == "audit_holdout":
            return int(row.get("start_idx") or 0)
    values = list((series_payload or {}).get("values") or [])
    return len(values) if values else None


def _serialize_window_ranges(
    full_index: pd.Index,
    windows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    if len(full_index) == 0:
        return serialized
    for window_spec in windows:
        start_idx = int(window_spec["start_idx"])
        end_idx = int(window_spec["end_idx"])
        if start_idx >= end_idx or start_idx >= len(full_index):
            continue
        end_pos = min(len(full_index) - 1, max(start_idx, end_idx - 1))
        serialized.append(
            {
                "label": str(window_spec["label"]),
                "role": str(window_spec["role"]),
                "start_idx": start_idx,
                "end_idx": end_idx,
                "start_timestamp": full_index[start_idx].isoformat(),
                "end_timestamp": full_index[end_pos].isoformat(),
            }
        )
        if "train_start_idx" in window_spec:
            serialized[-1]["train_start_idx"] = int(window_spec["train_start_idx"])
        if "train_end_idx" in window_spec:
            serialized[-1]["train_end_idx"] = int(window_spec["train_end_idx"])
        if "train_start_timestamp" in window_spec:
            serialized[-1]["train_start_timestamp"] = str(window_spec["train_start_timestamp"])
        if "train_end_timestamp" in window_spec:
            serialized[-1]["train_end_timestamp"] = str(window_spec["train_end_timestamp"])
        if "validation_start_timestamp" in window_spec:
            serialized[-1]["validation_start_timestamp"] = str(
                window_spec["validation_start_timestamp"]
            )
        if "validation_end_timestamp" in window_spec:
            serialized[-1]["validation_end_timestamp"] = str(
                window_spec["validation_end_timestamp"]
            )
    return serialized


