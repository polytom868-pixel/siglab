"""
Backward-compat re-exports for ``siglab.evaluator``.

All logic has moved to ``siglab.evaluation``. This module and the
remaining ``evaluator/`` submodules exist only as import shims so
that existing code continues to work unchanged.

Uses lazy __getattr__ to avoid circular import chains.
"""

from typing import Any


def __getattr__(name: str) -> Any:
    import importlib
    mod = importlib.import_module("siglab.evaluation.runner")
    if hasattr(mod, name):
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["ResearchEvaluator"]
