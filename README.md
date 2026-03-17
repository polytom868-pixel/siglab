# Wayfinder Autolab

`wayfinder-autolab` is a bounded research loop on top of the
[`wayfinder-paths-sdk`](https://github.com/WayfinderFoundation/wayfinder-paths-sdk).
It searches structured strategy graphs, backtests them with Wayfinder's
backtesting engine, keeps lineage and artifacts, exposes a local dashboard,
and can promote selected winners into runnable generated strategy packages.

## What It Does

- Searches structured candidates against a fixed evaluator.
- Current LLM run loop support is focused on `directional_perps`.
- Uses Wayfinder SDK backtesting as the accounting core.
- Enforces next-bar timing and a three-way split: in-sample, validation, and final audit.
- Stores every experiment, score, artifact, and research trace in local state.
- Exposes a lightweight dashboard with separate run, run-detail, and experiment-detail views.
- Can export selected experiments into generated Wayfinder strategy packages.

## Under Construction

This repository is usable, but several parts are still moving and should be
treated as experimental:

- `perp_multi_asset_carry`:
  - the family is “carry in spirit,” but its ranked execution model, default
    feature mix, and planner guidance are still evolving
  - expect behavior, defaults, and memory surfaces around carry experiments to
    keep changing
- Live export and deployment:
  - live export is currently implemented only for selected `directional_perps`
    families
  - generated strategies still depend on `wayfinder_autolab` runtime helpers
  - scheduling a runner job is an operator workflow, not a production-ready
    deployment system
  - `--live` only disables `dry_run`; it should not be read as “production safe”
- LLM search coverage:
  - the main workspace/planner/writer/Optuna loop is currently wired for
    `directional_perps`
  - carry/PT/lending families outside that loop can still compile and backtest,
    but they are not yet on the same mature orchestration path
- Artifact and prompt surfaces:
  - recent-trial ledgers, reflection packets, benchmark observation files, and
    dashboard views are still being refined
  - older artifacts may not expose the same retained-series or decomposition
    fields as newer runs

## Current Live Support

Backtest/search support currently includes:

- `directional_perps`
  - `perp_multi_asset_decision`
  - `perp_pair_trade_unlevered`
  - `perp_pair_trade_levered`
- `systematic_carry`
  - `basis_spread`
  - `stable_pt_ladder`
  - `pt_yield_rotation`
  - `lending_carry_rotation`

Live export support currently includes:

- `directional_perps` families only

Carry/PT/lending families are still searchable and backtestable, but are marked
`unsupported` for live promotion until their execution bodies are implemented.

The `perp_multi_asset_carry` family should be treated as research-active rather
than stable. Its semantics are documented in the manifests and prompts, but the
family is still under active iteration.

## Quickstart

### 1. Install

Use Python 3.12 and Poetry:

```bash
poetry env use python3.12
poetry install
```

`poetry install` pulls `wayfinder-paths` from GitHub. You do not need a sibling
SDK checkout for normal use.

Fast path for a new machine:

```bash
./scripts/quickstart.sh
```

That script can prompt for API keys, create `config.json` / `.env` from the
shipped examples, run `poetry install`, and start the dashboard.

### 2. Configure

Copy the example files first:

```bash
cp config.example.json config.json
cp .env.example .env
```

Then edit `.env` and set the values you need. Important variables:

```bash
WAYFINDER_CONFIG_PATH=./config.json
WAYFINDER_API_KEY=...

KIMI_API_KEY=...
KIMI_MODEL=kimi-k2.5
KIMI_BASE_URL=https://api.moonshot.ai/v1
KIMI_THINKING=enabled

AUTOLAB_STRATEGY_EXPORT_DIR=wayfinder_autolab/live/generated_strategies

TAVILY_API_KEY=...
```

`WAYFINDER_CONFIG_PATH` is required for commands that fetch market data, run
searches, evaluate benchmark candidates, or promote a strategy. Those commands
fail fast if the config file is missing.

`config.example.json` is a valid minimal starting point. If you set
`WAYFINDER_API_KEY` in `.env`, it is fine for `config.json` to keep
`system.api_key` empty. It also includes the current Wayfinder API base URL:
`https://strategies.wayfinder.ai/api/v1`.

Useful optional settings:

```bash
AUTOLAB_POPULATION_SIZE=4
KIMI_MAX_TOOL_ROUNDS=6
TAVILY_MAX_RESULTS=5
WEB_EXPLORE_RESULTS_PER_QUERY=2
```

### 3. Inspect The Current Research Surface

```bash
poetry run autolab inspect --track directional_perps
```

### 4. Run A Search

One cycle:

```bash
poetry run autolab run --track directional_perps --population-size 4
```

Twenty generations:

```bash
poetry run autolab run --track directional_perps --population-size 4 --iterations 20
```

Run forever:

```bash
poetry run autolab run --track directional_perps --population-size 4 --iterations 0
```

Single track / single family:

```bash
poetry run autolab run --track directional_perps --family perp_multi_asset_decision --population-size 1 --iterations 10
```

Label a harness run so it is grouped clearly in the dashboard:

```bash
poetry run autolab run --track directional_perps --iterations 30 --agent-label autolab_harness
```

### 5. Open The Dashboard

```bash
poetry run autolab dashboard --host 127.0.0.1 --port 8765
```

Then open:

```text
http://127.0.0.1:8765
```

## Compare Agents

There are now two clean ways to compare search behavior in the same evaluator:

- the internal Kimi-powered Autolab harness
- an external-agent benchmark deck, for example Claude Code

Both flows write into the same lineage DB and artifacts, and both can be
tagged with `agent_label` / `run_label` so the dashboard can compare them at
the run-session level.

### Internal Kimi Harness

Use this when you want to run the full Autolab planner/writer/reflection loop.

Example:

```bash
poetry run autolab run \
  --track directional_perps \
  --burn-in-iterations 3 \
  --iterations 30 \
  --agent-label kimi_harness
```

Notes:

- this uses the built-in Kimi planner/writer flow
- the run will appear in the dashboard as a harness run
- use a different `--agent-label` if you want to compare multiple harness setups

### External Claude Code Benchmark

Use this when you want Claude Code to iterate on a single mutable candidate file
against the fixed Autolab evaluator, in the same style as `autoresearch`.

Initialize a fresh labeled benchmark session:

```bash
poetry run autolab benchmark-init \
  --deck directional_perps_external \
  --force \
  --agent-label claude_code \
  --run-label claude-benchmark-1
```

Then start Claude Code from the repo root and point it at the benchmark deck.

Give it instructions like:

```text
Read benchmarks/directional_perps_external/program.md
and benchmarks/directional_perps_external/observation.md.

Only edit benchmarks/directional_perps_external/candidate.yaml.

After each edit, run:
poetry run autolab benchmark-eval --deck directional_perps_external

Do not edit runtime code.
Stop after 10 attempts or after the first keep.
```

Notes:

- benchmark runs will appear in the dashboard as benchmark runs
- `candidate.yaml` is automatically restored to the incumbent on `discard`, `invalid`, or `crash`
- use a new `--run-label` for each Claude benchmark session you want to compare

## Research Model

Autolab keeps the evaluator fixed and only mutates structured candidates.

Each generation:

1. Picks a parent from lineage.
2. Builds live market context from Delta Lab, Pendle, lending surfaces, and public fallbacks.
3. Runs optional Tavily-backed web research when `TAVILY_API_KEY` is configured, plus optional Kimi tool use.
4. Asks Kimi for bounded candidate mutations.
5. Compiles each candidate into prices, target positions, and cashflow inputs.
6. Runs Wayfinder backtests.
7. Scores and gates candidates.
8. Records every result in SQLite and JSON artifacts.

The evaluator uses:

- next-bar execution timing
- dropped newest bar to avoid incomplete-bar leakage
- first 75% of the sample for selector windows
- final 25% as a strict holdout

`aggregate_score` is in-sample only. Holdout metrics are recorded separately and
shown in the dashboard.

## CLI Reference

Top-level commands:

```bash
poetry run autolab run --help
poetry run autolab benchmark-init --help
poetry run autolab benchmark-eval --help
poetry run autolab benchmark-status --help
poetry run autolab inspect --help
poetry run autolab lineage --help
poetry run autolab dashboard --help
poetry run autolab promote --help
poetry run autolab promotions --help
```

Most-used commands:

```bash
poetry run autolab lineage --track directional_perps --limit 20
poetry run autolab promotions
poetry run autolab promotions --candidate 0fdd9dc4718692e3
```

Track aliases:

- `systematic_carry` is the canonical carry track name
- `market_neutral_carry` remains accepted as a compatibility alias

## External Benchmark Deck

Autolab also supports an `autoresearch`-style external-agent benchmark loop for
`directional_perps`.

Initialize the deck:

```bash
poetry run autolab benchmark-init --deck directional_perps_external
```

Or initialize a labeled external-agent session for comparison in the dashboard:

```bash
poetry run autolab benchmark-init --deck directional_perps_external --force --agent-label claude_code --run-label claude-benchmark-1
```

This creates:

- `benchmarks/directional_perps_external/program.md`
- `benchmarks/directional_perps_external/observation.md`
- `benchmarks/directional_perps_external/candidate.yaml`
- `benchmarks/directional_perps_external/best_candidate.yaml`
- `benchmarks/directional_perps_external/results.tsv`
- `benchmarks/directional_perps_external/state.json`

Then use the deck like this:

1. Read `program.md` and `observation.md`
2. Edit only `candidate.yaml`
3. Run:

```bash
poetry run autolab benchmark-eval --deck directional_perps_external
```

4. Inspect the returned status plus `results.tsv`

If the result is `keep`, the benchmark command advances the incumbent. If the
result is `discard`, `invalid`, or `crash`, the benchmark command restores
`candidate.yaml` back to the incumbent automatically.

Benchmark evaluations are also written into the normal lineage DB and artifacts
with a distinct `run_session_id`, `run_label`, and `agent_label`, so the
dashboard can compare external-agent benchmark sessions against internal harness
runs at the run-session level.

## Dashboard

The dashboard provides:

- a run-first home page with per-run improvement curves
- run detail pages with experiment tables scoped to a single run
- experiment detail pages with retained full-run charts
- metric switching
- family and track filtering
- retained full-run charts
- holdout and in-sample split visualization
- promotion readiness and live promotion controls

Experiment detail pages show:

- portfolio equity curve
- run metrics over time
- long/short timeline
- trade tape
- holdout split ranges
- live-promotion state

## Files And State

Key runtime outputs:

- lineage DB: `wayfinder_autolab.db`
- experiment artifacts: `artifacts/`
- cached data lake: `data/lake/`
- runner state: `.wayfinder/runner/`
- benchmark deck state: `benchmarks/*/`

Generated live strategy packages are written under:

- `wayfinder_autolab/live/generated_strategies/`

These runtime outputs are local working state. They are intentionally ignored in
source control and should not be committed.

## Repo Layout

- `wayfinder_autolab/`: application code
- `mutable/`: mutable family, feature, and graph source definitions
- `tests/`: Python test suite
- `benchmarks/`: local external-agent benchmark decks
- `artifacts/`, `data/lake/`, `wayfinder_autolab.db`: local runtime state
- `wayfinder_autolab/live/generated_strategies/`: generated live strategy packages

## Promoting A Winner To Live

Warning:
Live promotion is still under construction. It is best understood as a thin
export-and-runner bridge for operator-supervised testing, not a hardened
deployment system.

### Export Only

```bash
poetry run autolab promote --candidate 0fdd9dc4718692e3
```

This generates:

- a generated strategy package under `wayfinder_autolab/live/generated_strategies/`
- `strategy.py`
- `manifest.yaml`
- `README.md`
- `live_spec.json`
- a promotion record in lineage metadata

### Export And Schedule A Dry-Run Job

```bash
poetry run autolab promote \
  --candidate 0fdd9dc4718692e3 \
  --wallet-label basis_trading_strategy \
  --interval 600 \
  --schedule
```

Optional Kimi finalize step:

```bash
poetry run autolab promote \
  --candidate 0fdd9dc4718692e3 \
  --wallet-label basis_trading_strategy \
  --interval 600 \
  --schedule \
  --llm-finalize
```

Switch to live execution explicitly:

```bash
poetry run autolab promote \
  --candidate 0fdd9dc4718692e3 \
  --wallet-label basis_trading_strategy \
  --interval 600 \
  --schedule \
  --live
```

Defaults:

- promotion is manual
- exported strategies default to `dry_run`
- scheduling creates an `update` runner job
- deposit/funding is still a separate explicit action
- exported strategies are coupled to the current `wayfinder_autolab` runtime
- only selected `directional_perps` families are supported

## Runner Operations

In this environment, prefer the module form:

```bash
poetry run python -m wayfinder_paths.runnerd status
poetry run python -m wayfinder_paths.runnerd runs autolab-test-perp --limit 10
poetry run python -m wayfinder_paths.runnerd run-once autolab-test-perp
poetry run python -m wayfinder_paths.runnerd run-report 1 --tail-bytes 4000
```

The generated strategies currently import `wayfinder_autolab`, so they should be
run from the autolab Poetry environment.

## Testing

Run the test suite:

```bash
poetry run pytest --maxfail=1 -q
```

Useful checks:

```bash
node --check wayfinder_autolab/dashboard/static/common.js
node --check wayfinder_autolab/dashboard/static/home.js
node --check wayfinder_autolab/dashboard/static/app.js
node --check wayfinder_autolab/dashboard/static/experiment.js
poetry run python -m py_compile wayfinder_autolab/cli.py
```

## Notes And Caveats

- The core backtester is Wayfinder SDK code, not a custom autolab engine.
- Search-time `promoted` means “selected inside the research loop”; live
  promotion metadata is stored separately.
- If Delta Lab is rate-limited, autolab falls back to public data sources where
  supported.
- Live directional-perp export is thin and delegates its signal logic back to
  autolab runtime helpers to keep research and live logic aligned.
- Carry-family live execution is intentionally not wired yet.
