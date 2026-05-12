# Spec Checklist

- Family matches the extracted planner contract.
- Trade style matches the extracted planner contract when one is specified.
- Features are valid for the chosen family.
- Required feature roles from the extracted planner contract are satisfied.
- Forbidden motifs from the extracted planner contract are not violated.
- Universe shape matches the family.
- No unsupported top-level keys.
- Thesis is concise.
- Novel formulas are allowed, but only if they use manifest-listed aliases, raw series, and operators.
- Regime gates are explicit only when the extracted planner contract actually calls for them.
- If using `regime_gates.entry`, every item is either:
  - a string expression like `ge(pair_corr_72h,0.9)`
  - or a dict like `{"expression":"market_volatility_168h","max":0.0085}`
- Never use gate keys like `op`, `condition`, `threshold`, or `active`.
- If the planner provides an explicit gate spec, the expression and numeric values match exactly.
- Example: `{"expression":"funding_dispersion_72h","min":0.000001}` must stay `0.000001`, not `1.0`.
- Do not rewrite small thresholds into scientific notation.
- The spec would still make sense before any evaluator sweep.
