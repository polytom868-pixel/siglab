"""
Backward-compat shim — delegates to ``siglab.evaluation.strategy_semantics``.
"""

from siglab.evaluation.strategy_semantics import (  # noqa: F401
    PAIR_TRADE_FAMILIES,
    REGIME_KEYWORDS,
    NON_REGIME_ROLES,
    MOMENTUM_KEYWORDS,
    RESIDUAL_KEYWORDS,
    dict_or_empty,
    supports_explicit_trade_style,
    feature_roles_for_formula,
    spec_feature_roles,
    gate_dimensions,
    normalized_gate_entries,
    trade_style_bucket,
    motif_signature,
    inferred_trade_style,
)
