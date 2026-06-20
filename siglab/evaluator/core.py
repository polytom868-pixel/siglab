"""
Backward-compat shim — delegates to ``siglab.evaluation.runner``.
Uses lazy __getattr__ to avoid circular import chains.

All evaluation logic has moved to the ``siglab/evaluation/`` package.
This file exists only so that existing imports from
``siglab.evaluator.core`` continue to work unchanged.
"""

from typing import Any


def __getattr__(name: str) -> Any:
    import importlib
    mod = importlib.import_module("siglab.evaluation.runner")
    if hasattr(mod, name):
        return getattr(mod, name)
    # Also try evaluation.compile for compile_spec (which lives in runner via lazy wrapper)
    compile_mod = importlib.import_module("siglab.evaluation.compile")
    if hasattr(compile_mod, name):
        return getattr(compile_mod, name)
    backtest_mod = importlib.import_module("siglab.evaluation.backtest")
    if hasattr(backtest_mod, name):
        return getattr(backtest_mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [  # noqa: F822 -- names resolved via __getattr__
    "ResearchEvaluator",
    "_safe_float",
    "_unique_float_values",
    "_unique_int_values",
    "_serialize_series",
    "_serialize_metrics_frame",
    "_serialize_weight_changes",
    "_serialize_trades",
    "_annualized_sharpe",
    "_max_drawdown",
    "_slice_performance_stats",
    "_row_position_signature",
    "_episode_asset_lists",
    "_row_direction_label",
    "_mean_pairwise_rolling_corr",
    "_pair_position_episodes",
    "_holding_period_buckets",
    "_pair_regime_state",
    "_lookup_timestamp",
    "_pair_regime_snapshot",
    "_pair_trade_episodes_with_regime",
    "_pair_regime_diagnostics",
    "_serialize_canonical_run",
    "_pre_audit_drawdown_pack",
    "_series_from_payload",
    "_pre_audit_trade_episodes_from_canonical",
    "_series_has_finite_values",
    "_series_total_return",
    "_series_last_value",
    "_series_min_value",
    "_series_values",
    "_pre_audit_end_idx",
    "_serialize_window_ranges",
]
