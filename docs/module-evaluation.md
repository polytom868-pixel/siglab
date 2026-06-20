# Evaluation Engine

## Purpose

The evaluation engine is SigLab's core pipeline for assessing whether a strategy specification (spec) is viable for live deployment. It answers one question: **given a strategy spec and historical market data, does this strategy produce risk-adjusted returns that meet predefined quality gates?**

The engine performs:

- **Strategy compilation** — translating a YAML spec into computable feature frames and target positions
- **Backtesting** — simulating the strategy over historical price/funding data with realistic mechanics (leverage, liquidation, rebalancing)
- **Scoring** — computing a composite aggregate score from bounded performance components
- **Gating** — applying pass/fail criteria (drawdown limits, return thresholds, breadth checks)
- **Lineage tracking** — recording every experiment in SQLite for ancestry, memory packets, and Pareto analysis

## Architecture

The evaluation pipeline is a linear flow through focused submodules under `siglab/evaluation/`:

```
Spec YAML
    │
    ▼
┌──────────┐     ┌───────────┐     ┌─────────┐     ┌───────────┐     ┌──────────────┐
│ compile  │ ──▶ │ backtest  │ ──▶ │  score  │ ──▶ │   gates   │ ──▶ │   lineage    │
│          │     │           │     │         │     │           │     │  (LineageStore)│
└──────────┘     └───────────┘     └─────────┘     └───────────┘     └──────────────┘
```

### Module Map

| File | Responsibility |
|------|---------------|
| `compile.py` (1540 lines) | Feature resolution, position construction, regime gate masking, policy sweeps |
| `backtest.py` | Core backtest loop: equity curve, returns, PnL, liquidation detection |
| `score.py` | Aggregate score computation with bounded components |
| `gates.py` | Pass/fail gate conditions |
| `feature_dsl.py` (677 lines) | Recursive expression evaluator for feature formulas |
| `strategy_semantics.py` | Feature role classification, motif signatures, trade style inference |
| `events.py` | Perpetual futures market event analysis (roll detection, universe classification) |
| `runner.py` (3654 lines) | `ResearchEvaluator` — orchestrates the full pipeline |
| `__init__.py` | Package marker; no eager imports to avoid circular dependencies |

## Backtesting

Backtesting is handled by `siglab/evaluation/backtest.py`.

### Core Function

```python
run_backtest(prices, target_weights, config) -> BacktestResult
```

**Inputs:**
- `prices` — DataFrame of asset prices indexed by timestamp
- `target_weights` — DataFrame of target portfolio weights (shifted by 1 bar to prevent look-ahead bias)
- `config` — `BacktestConfig` dataclass with leverage, funding rates, rebalance threshold, liquidation toggle

**Mechanics:**
1. Prices and weights are aligned and forward-filled
2. Hourly returns are computed via `pct_change()`
3. PnL is calculated as `returns * lagged_weights * leverage`
4. If perpetual futures, funding rate PnL is added: `funding * lagged_weights`
5. Equity curve = `(1 + pnl).cumprod()`
6. Liquidation is detected when equity ≤ 0

**Output — `BacktestResult`:**
- `equity_curve` — cumulative equity series
- `returns` — per-bar PnL series
- `positions` — weight DataFrame
- `trades` — list of executed trade dicts (timestamp, symbol, size)
- `stats` — summary statistics (total_return, sharpe, cagr, max_drawdown, calmar)
- `liquidated` / `liquidation_timestamp`

### Summary Statistics

Computed in `_stats()`:
- **Total return**: `equity[-1] / equity[0] - 1`
- **Sharpe**: `mean(returns) / std(returns) * sqrt(annual_factor)` where `annual_factor = 365.25 * 24` (hourly bars)
- **CAGR**: compound annual growth rate from total return and period count
- **Max drawdown**: worst peak-to-trough decline
- **Calmar**: `CAGR / |max_drawdown|`

### Evaluation Windows

The `ResearchEvaluator` in `runner.py` splits data into:
- **Selector windows** — in-sample windows used for strategy selection
- **Validation window** — out-of-sample holdout for validation metrics
- **Audit window** — final pre-deployment check

Multiple leverage tiers are tested (1.0, min(2.0, max_leverage), max_leverage) across all windows.

## Scoring

Scoring is handled by `siglab/evaluation/score.py`.

### `summarize_window_results()`

Aggregates per-window backtest results into a single summary dict. All score components are individually **bounded** via `_bounded()` to prevent any single extreme value from dominating.

**Component weights:**

```
aggregate_score = median_sharpe
                + 4.0 * median_total_return
                + 0.5 * median_calmar
                + 0.1 * asset_breadth
                + 0.25 * profitable_window_pct
                + 1.5 * median_max_drawdown
```

**Component caps:**

| Component | Lower bound | Upper bound |
|-----------|-------------|-------------|
| median_sharpe | -20.0 | 20.0 |
| median_total_return | -1.0 | 5.0 |
| median_calmar | -50.0 | 50.0 |
| median_max_drawdown | -1.0 | 0.0 |

Non-finite values (NaN, inf) are replaced with 0.0 before clamping.

**Key metrics in the summary dict:**
- `aggregate_score` — the composite score
- `median_sharpe`, `median_total_return`, `median_cagr`, `median_calmar` — medians across windows
- `worst_max_drawdown` — worst drawdown across all windows
- `liquidation_count` — number of windows that resulted in liquidation
- `profitable_window_pct` — fraction of windows with positive returns
- `asset_breadth` — number of distinct assets traded

## Gates

Gates are pass/fail criteria evaluated in `siglab/evaluation/gates.py`.

### `evaluate_gates(track, summary) -> (passed, reasons)`

Returns `(True, [])` when all gates pass, or `(False, [tag1, tag2, ...])` listing failing gate tags.

| Gate | Condition | Tag |
|------|-----------|-----|
| Liquidation | Any window liquidated | `liquidation` |
| Median return | median_total_return ≤ 0 | `non_positive_median_return` |
| Median Sharpe | median_sharpe ≤ 0 | `non_positive_median_sharpe` |
| Validation return | validation_total_return ≤ 0 (if validation available) | `non_positive_validation_return` |
| Validation Sharpe | validation_sharpe ≤ 0 (if validation available) | `non_positive_validation_sharpe` |
| Pre-audit canonical return | pre_audit_canonical_total_return ≤ 0 | `non_positive_pre_audit_canonical_return` |
| Canonical series validity | canonical_series_valid is False | `invalid_canonical_series` |
| Drawdown limit | worst_max_drawdown < -0.35 (trend_signals) or < -0.25 (other) | `drawdown_limit` |
| Breadth (trend_signals) | asset_breadth < 2 | `insufficient_breadth` |
| Breadth (yield_flows) | asset_breadth < 1 | `insufficient_breadth` |

A strategy passes evaluation only when **all** gates pass simultaneously.

## Feature DSL

The Feature DSL (`siglab/evaluation/feature_dsl.py`) provides a recursive expression evaluator for computing strategy features from raw market data.

### How Features Are Defined

Features are declared in the spec YAML as a list of expression strings:

```yaml
features:
  - funding_168h_mean
  - funding_72h_mean
  - funding_carry_to_vol
  - realized_vol_168h
  - relative_momentum_24h
  - trend_strength_72h
```

### Expression Types

1. **Raw series** — direct references to data columns (e.g., `funding_168h_mean`)
2. **Aliases** — named shortcuts defined in `mutable/feature_lab.yaml`
3. **Function calls** — composed expressions using operators

### Available Operators

The DSL supports 35+ operators organized by category:

**Transforms:** `pct_change`, `diff`, `ema`, `log`, `abs`, `neg`, `sign_flip_prob`

**Rolling statistics:** `rolling_mean`, `rolling_sum`, `rolling_std`, `rolling_zscore`, `rolling_min`, `rolling_max`, `rolling_skew`, `rolling_kurt`, `rolling_corr`, `rolling_autocorr`, `rolling_beta`, `rolling_hurst`

**Signal generators:** `rsi`, `mean_reversion_halflife`, `kalman_beta`, `kalman_residual`

**Arithmetic:** `add`, `sub`, `mul`, `div`

**Comparison/logic:** `gt`, `ge`, `lt`, `le`, `and`, `or`, `not`, `where`, `clip`

### Feature Resolution

```python
resolve_feature_frames(features, aliases=aliases, raw_frames=raw_frames)
```

Each feature expression is recursively evaluated. Results are cached to avoid recomputation. The DSL parser validates syntax without side effects when `validate_only=True`.

### Normalization

Features are normalized before scoring:
- **Cross-sectional z-score** (default): z-score across assets at each timestamp
- **Time-series z-score**: rolling z-score over a configurable window (default 72 bars)

## Strategy Semantics

`siglab/evaluation/strategy_semantics.py` classifies strategies by their feature composition.

### Feature Roles

Features are assigned roles based on keyword matching:

| Role | Keywords |
|------|----------|
| `core_carry` | funding, carry |
| `carry_term_structure` | term_structure, decay |
| `orthogonal_regime` | trend_strength, volatility, co_movement, breadth, corr, dispersion |
| `trend_or_momentum` | momentum, return, ema, macd, rsi, breakout |
| `spread_or_residual` | residual, kalman, pair_ratio, bollinger, z_, zscore, half_life, hurst |
| `cross_sectional_core` | relative_, breadth_adjusted_ |
| `pair_state` | pair_, asset_1_, asset_2_ |

### Trade Style Inference

`inferred_trade_style(spec)` classifies a strategy into one of:
- `carry` — funding/carry-based
- `basket_neutral` — basket family
- `directional` — decision family
- `breakout` — breakout/donchian patterns
- `pullback` — RSI/pullback patterns
- `reversion` — mean reversion/residual patterns
- `continuation` — momentum/trend patterns
- `hybrid` — default fallback

For pair trade families, explicit `trade_style` in params is honored: `reversion`, `pullback`, `continuation`, `breakout`, `hybrid`.

### Motif Signatures

A motif signature is a compact fingerprint: `{family}|{trade_style}|{top_roles}|{top_gate_dimensions}`. Used by the planner to ban repeated failed strategy patterns.

## LineageStore

`siglab/search/lineage.py` implements experiment tracking in SQLite.

### Database Schema

Four tables:

**`experiments`** — latest state per spec_hash (upsert semantics):
- `spec_hash` (PK), `created_at`, `track`, `family`, `parent_hash`
- `spec_json`, `research_summary`, `summary_json`
- `aggregate_score`, `passed`, `deployd`
- `artifact_path`

**`experiment_events`** — append-only event log of every evaluation:
- Same columns as experiments plus `event_id` (auto-increment)
- Indexed on `(track, created_at)` and `spec_hash`

**`deployments`** — deployment metadata per spec_hash:
- Strategy name, directory, paths (spec, manifest, readme, config)
- Job configuration (name, interval, wallet label)
- Status flags (scheduled, dry_run, llm_finalized, support_status)

**`query_cards`** — cached external research query results:
- `query_hash` (PK), `track`, `family`, `canonical_query`, `report_json`

### Key Methods

| Method | Purpose |
|--------|---------|
| `record()` | Insert/update experiment and append to event log |
| `deploy()` | Mark a spec_hash as deployed |
| `record_deployment()` | Store full deployment metadata |
| `recent()` | Get N most recent experiments for a track |
| `best()` | Get highest-scoring passed experiment for a track |
| `memory_packet()` | Build a rich context packet for the planner (Pareto frontier, nearest winners/failures, coverage summaries, novelty pressure) |
| `dashboard_rows()` | Full event history for dashboard display |
| `experiment_detail()` | Single experiment with full artifact payload |
| `run_summaries()` | Group experiments by run session with aggregate stats |
| `list_rows()` | Paginated experiment listing |
| `has_spec()` | Check if a spec_hash exists |
| `clear_passed()` | Remove passed (non-deployed) experiments and their artifacts |
| `deployment()` | Retrieve deployment metadata for a spec_hash |
| `record_query_cards()` | Insert/replace cached external research query cards |

### Memory Packet

`memory_packet()` assembles context for the LLM planner including:
- Pareto frontier of non-dominated experiments
- Nearest winners and failures (ranked by spec similarity)
- Coverage summary (families, assets, features, failure modes)
- Archetype coverage by trade style
- Novelty pressure (flags when recent runs are too homogeneous)
- Failure/behavior/regime/drawdown/gate/equity pattern summaries
- Relevant query cards from external research

### Spec Similarity

Experiments are ranked by similarity to a parent spec using a weighted score:
- Same family: +5.0
- Same neutrality_basis: +1.0
- Shared assets: +1.5 per asset
- Shared features: +0.35 per feature
- Same maturity bucket: +1.0
- Same hedge_mode: +0.75
- Deployed: +0.25

## Typed Contracts

Defined in `siglab/orchestration/contracts.py`, these replace untyped `dict[str, Any]` in the orchestration pipeline.

### `PlannerOutput`

Output from the planner stage. Key fields:
- `decision` — the planner's decision (e.g., "explore", "refine")
- `search_mode`, `target_family`, `target_trade_style`
- `core_hypothesis`, `informative_test`
- `required_feature_roles`, `required_features`, `forbidden_features`
- `gate_intent`, `required_gate_dimensions`
- `banned_motif_signatures`, `required_variation_axis`
- `planner_regime_gates` — gate specs the writer must preserve

### `WriterOutput`

Output from the spec writer stage:
- `spec_payload`, `spec_path` — the generated spec
- `accepted` — whether the spec passed preflight
- `structure_spec`, `patch_payload`, `patch_summary` — if patching was used
- `failure_reason`, `failure_packet` — if generation failed

### `OptimizerOutput`

Output from Optuna parameter optimization:
- `spec_payload` — best spec found
- `best_summary`, `best_params`, `optuna_space`
- `trial_count`, `objective_value`
- `fragility_penalty`, `deployment_score`
- `fragility_pack`, `stability_pack`

### `ReflectorOutput`

Output from the reflection/lesson-learning stage:
- `lesson_card_path`, `trace_path`
- `frontmatter` — parsed YAML frontmatter

### `PreflightResult`

Validated result from writer preflight. Dataclass with computed properties:
- `material_drift` — True if any material fields changed
- `acceptable` — True when no parse errors, hard issues, conformance issues, or material drift

### Conformance Checking

`conformance_violations()` validates that a writer's spec conforms to the planner's contract:
- Family and trade_style match
- Required feature roles are present
- Required/forbidden features are respected
- Gate dimensions are implemented
- Planner-provided regime gates are preserved
- Variation axes are satisfied
- Banned motif signatures are avoided

## CLI Commands

### `benchmark-init`

Initializes a benchmark deck with seed specs.

```bash
python3 -m siglab benchmark-init --deck trend_signals_external [--agent-label LABEL] [--run-label LABEL] [--force]
```

Creates baseline experiment entries in the lineage store for the specified deck.

### `benchmark-eval`

Evaluates all specs in a benchmark deck.

```bash
python3 -m siglab benchmark-eval --deck trend_signals_external
```

Runs the full evaluation pipeline (compile → backtest → score → gates → lineage) for each spec in the deck and records results.

### `benchmark-status`

Shows the current state of a benchmark deck.

```bash
python3 -m siglab benchmark-status --deck trend_signals_external
```

### `ancestry`

Lists experiment history from the lineage store.

```bash
python3 -m siglab ancestry [--track TRACK] [--limit N] [--json]
```

Displays a table (or JSON) of experiments with created_at, track, family, spec_hash, score, passed, deployed status.

### `clear-passed`

Removes passed but non-deployed experiments.

```bash
python3 -m siglab clear-passed [--track TRACK|all]
```

## Golden Files

Golden-file regression tests (`tests/test_golden_evaluator.py`) verify numerical reproducibility of the evaluation pipeline.

### How It Works

1. A `DeterministicMockProvider` produces fixed synthetic market data
2. A known `SignalSpec` is evaluated through the full pipeline
3. The entire evaluation result is hashed via `compute_evaluation_hash()`
4. The hash is compared against a stored golden value in `tests/golden/evaluator_golden.txt`

### Test Cases

| Test | What it verifies |
|------|-----------------|
| `TestSpecHashDeterminism` | Same spec always produces same `strategy_hash` (16-char hex) |
| `TestEvaluationReproducibility` | Same spec + same data → byte-identical evaluation hash across runs |
| `TestGoldenFile` | Current evaluation hash matches the stored golden hash |
| `TestSpecCanonicalDict` | `canonical_dict()` → `from_dict()` round-trip is stable; feature order doesn't affect hash |

### Updating Golden Files

If a change is intentional, delete `tests/golden/evaluator_golden.txt` and re-run. The test will record the new hash and skip on first run.

## Testing

### Running Evaluation Tests

```bash
# All evaluation tests
python3 -m pytest tests/test_golden_evaluator.py -v

# Full test suite
python3 -m pytest -q

# Profile validation
python3 -m siglab profile --strict --json
```

### Key Test Files

- `tests/test_golden_evaluator.py` — golden-file regression and determinism tests
- `tests/test_cli_agent_safety.py` — CLI safety constraints

### Test Infrastructure

Tests use `DeterministicMockProvider` from `tests/conftest.py` which provides fixed synthetic data, ensuring evaluation results are fully reproducible without network access or real market data.

## Appendix: Spec Example

A typical strategy spec (from `benchmarks/trend_signals_external/spec.yaml`):

```yaml
track: trend_signals
family: perp_multi_asset_carry
hypothesis: Rank perps with a carry-led but price-aware cross-sectional score
neutrality_basis: none
features:
  - funding_168h_mean
  - funding_72h_mean
  - funding_carry_to_vol
  - realized_vol_168h
  - relative_momentum_24h
  - trend_strength_72h
universe:
  basis_groups: [BTC, ETH, SOL, HYPE]
  chains: [hyperevm]
  max_symbols: 4
  lookback_days: 365
  interval: 1h
risk:
  max_asset_weight: 0.35
  max_leverage: 1.5
params:
  gross_target: 1.0
  long_count: 2
  short_count: 2
  min_abs_score: 0.12
```

This spec declares a multi-asset carry strategy with 6 features, evaluated against perpetual futures data on HyperEVM chains.
