from __future__ import annotations

from typing import Any

PAIR_TRADE_FAMILIES = frozenset({'perp_pair_trade_unlevered', 'perp_pair_trade_levered'})
REGIME_KEYWORDS = ('trend_strength', 'trend_efficiency', 'market_volatility', 'volatility', 'co_movement', 'breadth', 'corr', 'correlation', 'dispersion', 'funding_dispersion', 'funding_level')
NON_REGIME_ROLES = ('carry_term_structure', 'cross_sectional_core', 'trend_or_momentum', 'spread_or_residual')
MOMENTUM_KEYWORDS = ('momentum', 'return', 'ema', 'macd', 'rsi', 'breakout')
RESIDUAL_KEYWORDS = ('residual', 'kalman', 'pair_ratio', 'log_spread', 'bollinger', 'z_', 'zscore', 'autocorr', 'half_life', 'hurst')

def dict_or_empty(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}

def supports_explicit_trade_style(family: str | None) -> bool:
    return str(family or '').strip() in PAIR_TRADE_FAMILIES

def feature_roles_for_formula(feature: str) -> set[str]:
    text = str(feature or '').lower()
    roles: set[str] = set()
    if any((keyword in text for keyword in ('funding', 'carry'))):
        roles.add('core_carry')
        roles.add('funding')
    if any((keyword in text for keyword in ('term_structure', 'decay'))):
        roles.add('carry_term_structure')
    if any((keyword in text for keyword in REGIME_KEYWORDS)):
        roles.add('orthogonal_regime')
    if any((keyword in text for keyword in MOMENTUM_KEYWORDS)):
        roles.add('trend_or_momentum')
    if any((keyword in text for keyword in RESIDUAL_KEYWORDS)):
        roles.add('spread_or_residual')
    if text.startswith('pair_') or 'asset_1_' in text or 'asset_2_' in text:
        roles.add('pair_state')
    if 'relative_' in text or 'breadth_adjusted_' in text:
        roles.add('cross_sectional_core')
    return roles

def spec_feature_roles(features: list[str]) -> set[str]:
    roles: set[str] = set()
    for feature in features:
        roles.update(feature_roles_for_formula(feature))
    return roles

def gate_dimensions(regime_gates: dict[str, Any] | None) -> list[str]:
    dimensions: list[str] = []
    for gate in list(dict_or_empty(regime_gates).get('entry') or []):
        expression = ''
        if isinstance(gate, dict):
            expression = str(gate.get('expression') or '')
        elif isinstance(gate, str):
            expression = gate
        if not expression:
            continue
        dimension = expression.split('(', 1)[0] if '(' not in expression else expression
        if '(' in expression:
            inner = expression.split('(', 1)[1].split(',', 1)[0].split(')', 1)[0]
            dimension = inner.strip() or expression
        dimensions.append(dimension.strip())
    return [dimension for dimension in dimensions if dimension]

def normalized_gate_entries(regime_gates: dict[str, Any] | None) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for gate in list(dict_or_empty(regime_gates).get('entry') or []):
        if isinstance(gate, str):
            expression = gate.strip()
            if expression:
                entries.append({'expression': expression, 'kind': 'string'})
            continue
        if not isinstance(gate, dict):
            continue
        expression = str(gate.get('expression') or '').strip()
        if not expression:
            continue
        normalized: dict[str, Any] = {'expression': expression, 'kind': 'dict'}
        if gate.get('min') is not None:
            normalized['min'] = gate.get('min')
        if gate.get('max') is not None:
            normalized['max'] = gate.get('max')
        entries.append(normalized)
    return entries

def trade_style_bucket(payload: dict[str, Any]) -> str:
    family = str(payload.get('family') or '')
    if not supports_explicit_trade_style(family):
        return 'cross_sectional'
    params = dict(payload.get('params') or {})
    return str(params.get('trade_style') or 'unspecified')

def motif_signature(payload: dict[str, Any]) -> str:
    family = str(payload.get('family') or '')
    features = [str(feature) for feature in list(payload.get('features') or [])]
    role_head = '+'.join(sorted(spec_feature_roles(features))[:4]) or 'uncategorized'
    gate_head = '+'.join(sorted(gate_dimensions(dict(payload.get('regime_gates') or {})))[:3]) or 'no_gates'
    return f'{family}|{trade_style_bucket(payload)}|{role_head}|{gate_head}'

def inferred_trade_style(spec: dict[str, Any]) -> str:
    params = dict(spec.get('params') or {})
    explicit = str(params.get('trade_style') or '').strip().lower()
    family = str(spec.get('family') or '').lower()
    allowed_pair_styles = {'reversion', 'pullback', 'continuation', 'breakout', 'hybrid'}
    if explicit in allowed_pair_styles and supports_explicit_trade_style(family):
        return explicit
    joined = ' '.join([str(spec.get('hypothesis') or ''), ' '.join((str(feature) for feature in spec.get('features') or []))]).lower()
    if 'carry' in family or any((token in joined for token in {'carry', 'funding'})):
        return 'carry'
    if 'basket' in family:
        return 'basket_neutral'
    if 'decision' in family:
        return 'directional'
    if any((token in joined for token in {'breakout', 'donchian'})):
        return 'breakout'
    if any((token in joined for token in {'pullback', 'rsi'})):
        return 'pullback'
    if any((token in joined for token in {'reversion', 'mean reversion', 'residual', 'bollinger', 'z_'})):
        return 'reversion'
    if any((token in joined for token in {'momentum', 'trend', 'continuation', 'macd'})):
        return 'continuation'
    return 'hybrid'
