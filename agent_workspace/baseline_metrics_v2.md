# Iter 0 Baseline Metrics (HEAD = f20d8bb)

## System
- CPU: 14 cores
- Total RAM: 11960 MB
- Available RAM: 7940 MB
- Load: 2.04 / 3.56 / 3.36 (1m / 5m / 15m)
- Uptime: 11:32

## siglab/ LoC (target -30%)
- Total: 49830 lines
- Target: ~34900 lines
- Reduction needed: ~14930 lines

## tests/ LoC (target -50%)
- Total: 43053 lines
- Target: ~21500 lines
- Reduction needed: ~21500 lines

## Pytest baseline
- Tests collected: 2772
- Pytest scan time: 1.80s (collect), 3.13s (real)
- Pass: 2713
- Skip: 59
- Fail: 0
- Total runtime: 55.07s (with 7 xdist workers)
- Warnings: 4 (numpy RuntimeWarnings in evaluator engine tests)

## Ruff
- 0 errors ✅
- mypy: ~341 errors (not yet measured post-ruff-fixes)

## TUI state
- 7 screens: market, paper, risk, telemetry, strategy, evidence, base
- Real data: 5/6 screens (need audit)
- Subprocess spawn: 3 in PaperScreen (replaced via prior agents)
- Bindings: TBD audit

## Performance deltas to measure (after I4, I5, I6)
- RAM used during full pytest
- CPU during pytest
- Load during pytest
- Pytest collect time
- Per-test runtime
- TUI render time
