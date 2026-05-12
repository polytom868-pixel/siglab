# yield_flows

## Mission

Find the strongest Pendle/PT carry rotation strategy, rotating between yield
opportunities and optionally hedging underlying beta with perps, while using
only the families currently supported by the compiler.

## Allowed Families

- `basis_spread`
- `stable_pt_ladder`
- `pt_yield_rotation`
- `lending_carry_rotation`

## Allowed Features

- `funding_72h_mean`
- `funding_168h_mean`
- `funding_flip_prob_14d`
- `realized_vol_168h`
- `pt_discount_to_par`
- `implied_minus_underlying_apy`
- `implied_apy_level`
- `underlying_apy_level`
- `expiry_roll_down`
- `tvl_momentum_30d`
- `lending_carry_level`
- `lending_supply_apr`
- `lending_reward_apr`
- `lending_base_yield_apy`
- `lending_utilization`
- `lending_price_return_24h`
- `lending_supply_tvl_momentum_168h`
- `lending_carry_to_util`

## Constraints

- `basis_spread` must remain long spot and short perp.
- `stable_pt_ladder` must only use stable or USD-like PT markets and must exit
  before expiry.
- `pt_yield_rotation` must rotate between PT markets and may set
  `hedge_mode=none` or `hedge_mode=perp`.
- `lending_carry_rotation` must rank lending markets systematically from
  observed carry and liquidity inputs, and may optionally hedge beta with perps.
- The proposal may mutate indicator formulas using the feature DSL operators:
  `pct_change`, `diff`, `rolling_mean`, `rolling_std`, `add`, `sub`, `mul`,
  `div`, `neg`, `clip`, and `sign_flip_prob`.
- The proposal may change universe filters, feature subsets, selection counts,
  leverage caps, rebalance thresholds, hedge mode, hedge ratio, and long/short
  enable flags where relevant.

## Gates

- No liquidations on any leverage tier.
- Positive median total return across walk-forward windows.
- Worst max drawdown better than `-0.25`.

