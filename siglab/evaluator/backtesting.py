"""
Backward-compat shim — delegates to ``siglab.evaluation.backtest``.
"""

from siglab.evaluation.backtest import (  # noqa: F401
    BacktestConfig,
    BacktestResult,
    convert_to_spot,
    run_backtest,
    _stats,
)
