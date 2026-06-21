"""SigLab evaluation package."""

# NOTE: We do NOT eagerly import ResearchEvaluator here because that would
# trigger a circular import chain (evaluation.runner → data.feeds → ...
# → search.lineage → strategy_semantics (shim) → evaluation.strategy_semantics).
# Use ``from siglab.evaluation.runner import ResearchEvaluator`` directly
# in consumer code, or rely on the ``siglab.evaluator`` backward-compat shim.
