"""
Backward-compat shim — delegates to ``siglab.evaluation.feature_dsl``.
"""

from siglab.evaluation.feature_dsl import (  # noqa: F401
    FUNCTION_OPERATORS,
    load_feature_spec,
    is_valid_feature_expression,
    resolve_feature_frames,
)
