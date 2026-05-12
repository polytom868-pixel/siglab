# SigLab Tuning Guide

This file collects the main user-facing levers in `siglab`.

Use it as a map of:

- what you can change quickly at runtime
- what you can change in the signal surface
- where the editable source-of-truth files live

## Fast Runtime Levers

These are the easiest knobs to change without editing Python code.

### CLI

`siglab challenge` exposes the main submission controls:

```bash
siglab challenge init --challenge trend_signals_open
siglab challenge eval --challenge trend_signals_open
siglab challenge status
siglab deploy
siglab deployments
```

High-impact flags:

- `--challenge`: choose the challenge deck
- `--family` / `--families`: constrain the family set
- `--iterations`: total run length
- `--warmup-iterations`: deterministic warm-up before main search
- `--population-size`: spec count per iteration in the non-workspace path
- `--memory-scope`: `session_local` or `track_shared`
- `--skip-llm`: deterministic/no-LLM mode
- `--runner-label`: label the run in ancestry and the dashboard

### `.env`

The most important environment knobs are:

- `SOSOVALUE_CONFIG_PATH`
- `SOSOVALUE_API_KEY`
- `CLAUDE_API_KEY`
- `CLAUDE_MODEL`
- `CLAUDE_THINKING`
- `CLAUDE_MAX_TOOL_ROUNDS`
- `SIGLAB_POPULATION_SIZE`
- `SIGLAB_OPTUNA_TRIALS`
- `SIGLAB_MEMORY_SCOPE`
- `SIGLAB_USE_HISTORICAL_SEEDS`
- `SIGLAB_AGENT_DEPLOY_DIR`

`SIGLAB_MEMORY_SCOPE=session_local` is the safest default if you want each run
to learn only from its own history.

## Signal Surface Levers

These files shape what the search loop can explore.

### Tracks and families

Current seeded tracks and families include:

- `trend_signals`
  - `multi_asset_momentum`
  - `pair_momentum_base`
  - `pair_momentum_levered`
  - `multi_asset_yield`
- `yield_flows`
  - `funding_spread`
  - `yield_ladder`
  - `yield_rotation`
  - `borrow_carry_rotation`

If you want to narrow experiments to one family, use `--family`.

### Assets and universe breadth

The main asset and universe levers are:

- `basis_groups`
- `max_symbols`
- `lookback_days`
- `interval`
- `chains`

### Positioning and risk posture

The highest-impact policy knobs usually are:

- `long_count`
- `short_count`
- `gross_target`
- `min_abs_score`
- `max_asset_weight`
- `rebalance_threshold`
- `max_leverage`

### Search behavior

The biggest search-loop levers are:

- `SIGLAB_OPTUNA_TRIALS`
- `SIGLAB_MEMORY_SCOPE`
- `CLAUDE_MAX_TOOL_ROUNDS`
- `--iterations`
- `--warmup-iterations`

If audit quality is weak, the first levers to inspect are usually:

- memory scope
- Optuna trial count
- family defaults
- seed universes and seed params

## Recommended Editing Order

If you are new to the repo, change things in this order:

1. `.env` runtime knobs
2. `mutable/` seed universes and params
3. family defaults and weights
4. feature aliases and formulas

That keeps simple operational tuning separate from deeper signal-surface edits.

## Source Of Truth

The editable source-of-truth files for users are:

- [`config.example.json`](../config.example.json)
- [`.env.example`](../.env.example)
- [`mutable/signal_registry.yaml`](../mutable/signal_registry.yaml)
- [`mutable/universe_map.yaml`](../mutable/universe_map.yaml)
- [`mutable/indicator_lib.dsl`](../mutable/indicator_lib.dsl)
- [`mutable/indicator_lib.yaml`](../mutable/indicator_lib.yaml)
- [`mutable/research_log.md`](../mutable/research_log.md)

