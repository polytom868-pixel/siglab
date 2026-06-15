"""
Backward-compat shim — delegates to ``siglab.evaluation.gates``.
"""

__all__ = ["evaluate_gates"]
from siglab.evaluation.gates import evaluate_gates
