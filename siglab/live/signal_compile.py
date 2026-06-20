"""Signal compilation adapter — provides a stable import target for runtime.

This module re-exports ``compile_spec`` from the real implementation in
``siglab.evaluation.compile`` (the evaluation pipeline, preserved per C1),
replacing the deleted ``siglab.evaluator.compile`` shim.
"""

from siglab.evaluation.compile import compile_spec  # noqa: F401
