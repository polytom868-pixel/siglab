# Benchmark Observation: trend_signals_external

This is an external-agent benchmark deck modeled after `autoresearch`.
Edit only `spec.yaml`. The evaluator and validator are fixed.

## Objective
- Beat the current incumbent on `aggregate_score`.
- A spec is `keep` only if it passes normal gating and improves the incumbent.
- Tie-break with `validation_total_return`, then `pre_audit_canonical_total_return`.

## Session
- runner_label: `external_agent`
- benchmark_run_id: `benchmark::trend_signals_external::external_agent::20260602T151655Z`
- run_label: `benchmark::trend_signals_external::external_agent::20260602T151655Z`

## Current Incumbent
- hash: `3f9921c692b9288d`
- source: `historical_artifact`
- family: `perp_multi_asset_carry`
- aggregate_score: `24.870038`
- validation_total_return: `-0.0152%`
- pre_audit_canonical_total_return: `0.5384%`

## Allowed Families
- perp_multi_asset_decision, perp_pair_trade_unlevered, perp_pair_trade_levered, perp_basket_neutral_unlevered, perp_basket_neutral_levered, perp_multi_asset_carry

## Current Strongest Anchor
- default seed family: `perp_multi_asset_carry`
- incumbent hypothesis: Rank perps with a carry-led but price-aware cross-sectional score, short the crowded expensive carry names, and buy the strongest cheap-carry names.

## Best Existing Passed Spec In DB
- `bad81f03a7487a5a` perp_multi_asset_decision aggregate_score=38.128087

## Recent Failure Motifs
- 3f9921c692b9288d perp_multi_asset_carry: score=24.870038, validation=-0.0152%, pre_audit=0.5384%, gate_reasons=non_positive_median_return, non_positive_median_sharpe, non_positive_validation_return
- 3f9921c692b9288d perp_multi_asset_carry: score=24.870038, validation=-0.0152%, pre_audit=0.5384%, gate_reasons=non_positive_median_return, non_positive_median_sharpe, non_positive_validation_return
- 3f9921c692b9288d perp_multi_asset_carry: score=24.870038, validation=-0.0152%, pre_audit=0.5384%, gate_reasons=non_positive_median_return, non_positive_median_sharpe, non_positive_validation_return
