# directional_perps

## Mission

Find the strongest perps-only trading system using two complementary family
types:
- a multi-asset decision family where each asset can be long, short, or flat
- a pair-trade family where a two-asset spread can be traded either way or left
  flat

## Allowed Families

- `perp_multi_asset_decision`
- `perp_pair_trade_unlevered`
- `perp_pair_trade_levered`

## Allowed Features

- `price_return_24h`
- `price_return_72h`
- `price_return_168h`
- `trend_strength_72h`
- `realized_vol_168h`
- `bollinger_z_20`
- `bollinger_width_20`
- `funding_72h_mean`
- `funding_168h_mean`
- `funding_flip_prob_14d`
- `funding_carry_to_vol`
- `pair_ratio_return_24h`
- `pair_ratio_return_72h`
- `pair_ratio_return_168h`
- `pair_trend_strength_72h`
- `pair_realized_vol_168h`
- `pair_ema_gap_12_26`
- `pair_macd_hist_12_26_9`
- `pair_bollinger_z_20`
- `pair_bollinger_width_20`
- `pair_rsi_centered_14`
- `asset_1_funding_carry_to_vol`
- `asset_2_funding_carry_to_vol`
- `funding_spread_72h_mean`
- `funding_spread_168h_mean`
- `funding_spread_flip_prob_14d`

## Constraints

- Execution must remain perps only.
- `perp_multi_asset_decision` should score each asset independently, so assets
  may all point the same way or stay flat.
- `perp_pair_trade_unlevered` should score the spread between two assets and
  may be long asset 1 / short asset 2, the reverse, or flat, while
  staying capped at 1x gross.
- `perp_pair_trade_levered` should score the spread between two assets and may
  be long asset 1 / short asset 2, the reverse, or flat, with
  signal-scaled gross exposure up to 3x.
- Pair-trade proposals should stay within the preferred pair universe:
  `ETH/BTC`.
- Pair-trade formulas may use symmetric raw leg inputs:
  `asset_1_price`, `asset_2_price`, `asset_1_funding`, `asset_2_funding`,
  plus derived pair inputs `price_ratio` and `funding_spread`.
- Directional perps runs use a fixed requested lookback of `365` days, but the
  strategy must remain sensible if the realized history is materially shorter.
- Evaluation should prefer rolling train/validation chunks when enough history
  exists, but the strategy must remain sensible if the realized history is
  materially shorter than the requested maximum lookback.
- The final audit block is untouched out-of-sample evaluation and should never
  be used as a selection target or tuned against.
- The strategy should treat price as the primary signal source and may use
  funding as carry, filtering, or tie-breaking context.
- The proposal may choose long-only or long-short positioning.
- The proposal may mutate indicator formulas using the feature DSL operators:
  `pct_change`, `diff`, `ema`, `rolling_mean`, `rolling_std`, `rolling_min`,
  `rolling_max`, `rsi`, `add`, `sub`, `mul`, `div`, `neg`, `clip`, and
  `sign_flip_prob`.
- The candidate may change selection counts, feature subsets, leverage caps,
  rebalance thresholds, gross exposure targets, and long/short enable flags.
- The proposal may not invent new families or features outside the mutable
  surfaces.

## Gates

- No liquidations on any leverage tier.
- Positive median total return across walk-forward windows.
- Positive median Sharpe across walk-forward windows.
