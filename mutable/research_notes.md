# Current Hypotheses

- The directional track should stay concentrated in liquid majors because the
  fallback public data path is strongest there.
- The carry track should keep basis spread and stable PT ladders separate until
  both families have stable individual statistics.
- Positive funding is only interesting if the sign does not flip often enough to
  erase carry after fees.
- Stable PT carry is best modeled as a roll-down instrument; the system should
  exit before expiry instead of trying to simulate redemption mechanics inside
  the Wayfinder core backtester.
