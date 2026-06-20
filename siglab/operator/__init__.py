"""
SigLab Operator Module.

Provides the ``OperatorPipeline`` — a research-to-decision production pipeline
that transforms evidence records into trade signals, applies risk checks,
and positions via the paper client under dry-run enforcement.
"""

from siglab.operator.pipeline import OperatorPipeline, TradeSignal, Position, RiskReport

__all__ = [
    "OperatorPipeline",
    "TradeSignal",
    "Position",
    "RiskReport",
]
