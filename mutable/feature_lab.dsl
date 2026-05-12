# Supported feature formulas.
# These are descriptive formulas for the bounded feature registry used by the
# compiler. The LLM may reference names from the left-hand side only.

price_return_24h = pct_change(price_usd, 24)
price_return_72h = pct_change(price_usd, 72)
realized_vol_168h = rolling_std(log_return(price_usd), 168)
funding_72h_mean = rolling_mean(funding_rate, 72)
funding_168h_mean = rolling_mean(funding_rate, 168)
funding_flip_prob_14d = rolling_mean(sign_flip(funding_rate), 336)
pt_discount_to_par = 1.0 - pt_price
implied_minus_underlying_apy = implied_apy - underlying_apy
expiry_roll_down = pt_discount_to_par / max(days_to_expiry, 1)
tvl_momentum_30d = pct_change(total_tvl, 30)
