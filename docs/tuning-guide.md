# Tuning Guide

This file collects the main user-facing levers in `wayfinder-autolab`.

Use it as a map of:

- what you can change quickly at runtime
- what you can change in the research surface
- where the editable source-of-truth files live

## Fast Runtime Levers

These are the easiest knobs to change without editing Python code.

### CLI

`autolab run` exposes the main search controls:

```bash
poetry run autolab run \
  --track directional_perps \
  --family perp_multi_asset_carry \
  --iterations 10 \
  --burn-in-iterations 0 \
  --population-size 4 \
  --memory-scope run_local
```

High-impact flags:

- `--track`: choose the research track
- `--family` / `--families`: constrain the family set
- `--iterations`: total run length
- `--burn-in-iterations`: deterministic warm-up before main LLM search
- `--population-size`: candidate count per iteration in the non-workspace path
- `--memory-scope`: `run_local` or `track_global`
- `--skip-llm`: deterministic/no-LLM mode
- `--agent-label`: label the run in lineage and the dashboard

### `.env`

The most important environment knobs are:

- `WAYFINDER_CONFIG_PATH`
- `WAYFINDER_API_KEY`
- `KIMI_API_KEY`
- `KIMI_MODEL`
- `KIMI_THINKING`
- `KIMI_MAX_TOOL_ROUNDS`
- `AUTOLAB_POPULATION_SIZE`
- `AUTOLAB_OPTUNA_TRIALS`
- `AUTOLAB_MEMORY_SCOPE`
- `TAVILY_API_KEY`
- `TAVILY_MAX_RESULTS`
- `WEB_EXPLORE_RESULTS_PER_QUERY`

`AUTOLAB_MEMORY_SCOPE=run_local` is the safest default if you want each run to
learn only from its own history.

## Research Surface Levers

These files shape what the search loop can explore.

### Families and defaults

File:

- [`mutable/family_lab.yaml`](../mutable/family_lab.yaml)

Use this file to change:

- which families exist under each track
- family capabilities and execution profiles
- default policy values such as:
  - `long_count`
  - `short_count`
  - `gross_target`
  - `min_abs_score`
- default feature-weight priors per family

This is the right place to change the overall behavior of a family.

### Seed candidates and asset universes

File:

- [`mutable/graph_lab.yaml`](../mutable/graph_lab.yaml)

Use this file to change:

- the initial seed candidates
- asset lists in `universe.basis_groups`
- `max_symbols`
- `lookback_days`
- `interval`
- per-seed `risk` defaults
- per-seed `params`

This is the easiest place to change what assets or universes the system starts
from.

### Feature definitions and aliases

Files:

- [`mutable/feature_lab.dsl`](../mutable/feature_lab.dsl)
- [`mutable/feature_lab.yaml`](../mutable/feature_lab.yaml)

Use these files to change:

- available feature aliases
- market-wide overlays
- feature formulas and derived signals

If you want to add a new ranking signal, this is usually the place.

### Planner notes and prompt-side research hints

File:

- [`mutable/research_notes.md`](../mutable/research_notes.md)

Use this for lightweight operator guidance that should influence the planner
without changing code.

## Big Levers By Category

### Track and family choice

Current seeded tracks and families include:

- `directional_perps`
  - `perp_multi_asset_decision`
  - `perp_multi_asset_carry`
  - `perp_pair_trade_unlevered`
  - `perp_pair_trade_levered`
  - `perp_basket_neutral_unlevered`
  - `perp_basket_neutral_levered`
- `market_neutral_carry`
  - `basis_spread`
  - `stable_pt_ladder`
  - `pt_yield_rotation`
  - `lending_carry_rotation`

If you want to narrow experiments to one family, use `--family`.

If you want to change how a family behaves by default, edit
[`mutable/family_lab.yaml`](../mutable/family_lab.yaml).

### Assets and universe breadth

The main asset and universe levers are:

- `basis_groups`
- `max_symbols`
- `lookback_days`
- `interval`
- `chains`

These mostly live in seed candidates in
[`mutable/graph_lab.yaml`](../mutable/graph_lab.yaml).

For example, the default `directional_perps` seeds currently start from
`BTC`, `ETH`, `SOL`, and `HYPE` on `hyperevm`, while the carry/PT seeds use
their own chain and asset sets.

### Positioning and risk posture

The highest-impact policy knobs usually are:

- `long_count`
- `short_count`
- `gross_target`
- `min_abs_score`
- `max_asset_weight`
- `rebalance_threshold`
- `max_leverage`

These appear in both:

- [`mutable/family_lab.yaml`](../mutable/family_lab.yaml) for family defaults
- [`mutable/graph_lab.yaml`](../mutable/graph_lab.yaml) for specific seed
  candidates

### Search behavior

The biggest search-loop levers are:

- `AUTOLAB_OPTUNA_TRIALS`
- `AUTOLAB_MEMORY_SCOPE`
- `KIMI_MAX_TOOL_ROUNDS`
- `--iterations`
- `--burn-in-iterations`

If audit quality is weak, the first levers to inspect are usually:

- memory scope
- Optuna trial count
- family defaults in `family_lab.yaml`
- seed universes and seed params in `graph_lab.yaml`

## Recommended Editing Order

If you are new to the repo, change things in this order:

1. `.env` runtime knobs
2. [`mutable/graph_lab.yaml`](../mutable/graph_lab.yaml) seed universes and
   params
3. [`mutable/family_lab.yaml`](../mutable/family_lab.yaml) family defaults and
   weights
4. [`mutable/feature_lab.dsl`](../mutable/feature_lab.dsl) and
   [`mutable/feature_lab.yaml`](../mutable/feature_lab.yaml) feature surface

That keeps simple operational tuning separate from deeper strategy-surface
edits.

## Source Of Truth

The generated workspace manifests and dashboard views are helpful, but the
editable source-of-truth files for users are:

- [`config.example.json`](../config.example.json)
- [`.env.example`](../.env.example)
- [`mutable/family_lab.yaml`](../mutable/family_lab.yaml)
- [`mutable/graph_lab.yaml`](../mutable/graph_lab.yaml)
- [`mutable/feature_lab.dsl`](../mutable/feature_lab.dsl)
- [`mutable/feature_lab.yaml`](../mutable/feature_lab.yaml)
- [`mutable/research_notes.md`](../mutable/research_notes.md)
