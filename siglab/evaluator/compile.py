"""
Backward-compat shim — delegates to ``siglab.evaluation.compile``.
Uses lazy __getattr__ to avoid circular import chains.
"""

from typing import Any


def __getattr__(name: str) -> Any:
    import importlib
    mod = importlib.import_module("siglab.evaluation.compile")
    if hasattr(mod, name):
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [  # noqa: F822 -- names resolved via __getattr__
    "PAIR_TRADE_FAMILIES",
    "PERP_EXECUTION_PROFILES",
    "PAIR_STATEFUL_POLICY_SCHEMA",
    "compile_spec",
]
