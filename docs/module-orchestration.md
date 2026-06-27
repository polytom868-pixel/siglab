# Orchestration Layer & Configuration

## Purpose

The orchestration layer coordinates SigLab's automated research-to-action pipeline. It drives the loop from hypothesis planning through spec writing, numerical optimization, evaluation, and post-run reflection. Each iteration produces a concrete signal spec (a structured JSON strategy definition), evaluates it against historical data, and feeds lessons back into the next cycle.

The orchestration layer does **not** claim causal market prediction or live signed SoDEX execution. It is a research iteration engine that produces ranked, backtested signal candidates.

## Architecture

The pipeline runs four sequential stages per iteration:

```
Planner → Writer → Optimizer → Reflector
```

| Stage | Runner Class | Output Contract | Description |
|-------|-------------|-----------------|-------------|
| **Planner** | `ResearchPlannerRunner` | `PlannerOutput` | Produces a markdown research note and a typed contract dict that constrains the writer. Uses workspace tools, probes, and web search to ground the next test in evidence. |
| **Writer** | `SpecWriterRunner` | `WriterOutput` | Takes the research note + planner contract and emits a single validated spec JSON. Runs preflight checks (schema, conformance, gate lint, activity lint) and can repair on failure. |
| **Optimizer** | `OptunaOptimizerRunner` | `OptimizerOutput` | Uses Optuna (TPE sampler) to sweep continuous numeric parameters (e.g., entry/exit scores, cooldown bars). Infers the search space from the spec payload. |
| **Reflector** | `ReflectionRunner` | `ReflectorOutput` | Produces a lesson card (markdown with YAML frontmatter) summarizing the verdict, failure mode, and next test. Persisted to the workspace card store. |

After each stage, `WorkspaceHooks` can record experiment cards and refresh frontier files.

## Typed Contracts

All pipeline data flows are governed by `TypedDict` contracts defined in `siglab/orchestration/contracts.py`. These replace untyped `dict[str, Any]` passing.

### `PlannerOutput` (total=False)

The planner contract is built incrementally. Downstream consumers treat missing keys as absent intent, not errors.

| Field | Type | Description |
|-------|------|-------------|
| `decision` | `str` | e.g. `"refine_current_family"` or `"branch_family"` |
| `search_mode` | `str` | Current search mode (default `"refine"`) |
| `target_family` | `str` | Strategy family to target (e.g. `"perp_carry_directional"`) |
| `target_trade_style` | `str \| None` | Trade style for families that support explicit styles |
| `target_universe` | `list[str]` | Basis group symbols for the tradeable universe |
| `core_hypothesis` | `str` | The main hypothesis being tested |
| `informative_test` | `str` | Concrete description of the next experiment |
| `expected_success` / `expected_failure` | `list[str]` | Expected outcomes |
| `evidence_paths` | `list[str]` | Workspace-relative paths to evidence cards |
| `tools_used` | `list[str]` | Tools called during planning |
| `tracking_tags` | `list[str]` | Tags for traceability |
| `must_answer` | `str` | The question this iteration must answer |
| `required_feature_roles` | `list[str]` | Semantic feature roles the spec must include |
| `required_features` / `forbidden_features` | `list[str]` | Named feature constraints |
| `forbidden_motifs` | `list[str]` | Motif patterns to avoid |
| `gate_intent` | `dict[str, Any]` | Desired regime gate behavior |
| `required_gate_dimensions` | `list[str]` | Gate dimensions that must be present |
| `required_variation_axis` | `str \| None` | `"non_regime"` or `"policy"` axis requirement |
| `banned_motif_signatures` | `list[str]` | Previously failed motif signatures |
| `writer_inputs` | `list[str]` | Inputs the writer should consult |
| `planner_regime_gates` | `dict[str, Any]` | Exact gate specs the writer must preserve |

### `WriterOutput` (total=False)

| Field | Type | Description |
|-------|------|-------------|
| `spec_payload` | `dict \| None` | The final validated spec JSON |
| `spec_path` | `str \| None` | Path to the saved spec file |
| `trace_path` | `str` | Path to the writer trace JSON |
| `accepted` | `bool` | Whether the spec passed preflight |
| `base_spec_payload` | `dict \| None` | The base (parent) spec used as starting point |
| `base_spec_path` | `str \| None` | Path to the base spec file |
| `structure_spec` | `dict \| None` | Structural constraints passed to the LLM |
| `patch_payload` | `dict \| None` | Diff between base and final spec |
| `patch_summary` | `list[str] \| None` | Human-readable patch description |
| `spec_after_patch_path` | `str \| None` | Path to the spec file after patch application |
| `failure_reason` | `str \| None` | Why the writer failed (if applicable) |
| `failure_packet` | `dict \| None` | Detailed failure info for repair |

### `OptimizerOutput` (total=False)

| Field | Type | Description |
|-------|------|-------------|
| `spec_payload` | `dict` | Best spec found by Optuna |
| `best_summary` | `dict` | Evaluation summary of the best trial |
| `best_params` | `dict` | Optimal parameter values |
| `optuna_space` | `dict` | Inferred Optuna search space |
| `score_diagnosis` | `dict` | Component-level score breakdown |
| `trial_count` | `int` | Number of Optuna trials run |
| `objective_value` | `float` | Best objective value |
| `fragility_penalty` | `float` | Penalization for fragility signals |
| `deployment_score` | `float \| None` | `aggregate_score - fragility_penalty` |
| `fragility_pack` | `dict` | Detailed fragility analysis |
| `stability_pack` | `dict` | Stability analysis results |

### `ReflectorOutput` (total=False)

| Field | Type | Description |
|-------|------|-------------|
| `lesson_card_path` | `str` | Path to the saved lesson card markdown |
| `trace_path` | `str` | Path to the reflector trace JSON |
| `frontmatter` | `dict` | Parsed YAML frontmatter from the lesson card |

### `PreflightResult` (dataclass)

Used by the writer's preflight validation:

| Field | Type | Description |
|-------|------|-------------|
| `parse_error` | `str \| None` | JSON parse error, if any |
| `hard_issues` | `list[str]` | Schema or structural violations |
| `conformance_issues` | `list[str]` | Planner contract violations |
| `gate_lint` | `dict \| None` | Regime gate quality analysis |
| `changed_fields` | `list[str]` | Fields that changed under validation |
| `harmless_changed_fields` | `list[str]` | Normalization-only changes |
| `material_changed_fields` | `list[str]` | Meaningful drift changes |
| `validated_payload` | `dict \| None` | The normalized spec payload |

Computed properties: `material_drift` (bool), `acceptable` (bool — true when no errors, no conformance issues, no material drift, and payload is present).

## Runner Pattern

Each runner follows a consistent pattern:

1. **Load skill prompt** — reads a `SKILL.md` file from `.agents/skills/<skill-name>/` or falls back to an embedded prompt.
2. **Build user prompt** — assembles context from workspace files (RUNBOOK, TASK, manifests, parent card, evidence, etc.).
3. **Call LLM** — uses `ClaudeClient` for text or JSON completion, with tool-calling support.
4. **Extract contract** — parses the LLM output into a typed contract dict.
5. **Validate** — runs semantic checks, conformance checks, or preflight validation.
6. **Repair loop** — if validation fails, feeds the failure packet back to the LLM for correction (up to `MAX_REPAIR_ATTEMPTS` or `MAX_ATTEMPTS`).
7. **Write trace** — persists the full inputs, outputs, tool calls, and Claude trace to a JSON file.
8. **Return result** — returns a typed result dataclass.

### ResearchPlannerRunner

- **Max repair attempts**: 5
- **Tool budget**: up to 24 total tool calls, 8 probe calls total, 6 per probe tool
- **Tools**: `search_workspace`, `search_workspace_text`, `open_file`, `search_features`, `inspect_feature`, `suggest_feature_set`, `probe_feature_forward_stats`, `probe_spec_gate_impact`, `compare_intended_vs_frozen_spec`, `web_search`, `web_fetch`, `think`
- **Semantic checks**: note length, action keywords, target family, must_answer, informative test, probe claims, tool usage
- **Fallback**: generates a minimal fallback note and contract if all repair attempts fail

### SpecWriterRunner

- **Max attempts**: 2 (3 for B.AI provider)
- **Preflight validation**: schema parse, family match, conformance violations, gate lint (via `probe_spec_gate_impact`), activity lint (compile + check active bar fraction)
- **Repair**: builds a repair packet with errors, conformance issues, gate lint results, and normalization diff
- **Planner gate preservation**: rewrites LLM-emitted gate values to match the planner's exact numeric literals

### OptunaOptimizerRunner

- **Sampler**: TPE with `multivariate=True`, seed=7
- **Warm start**: enqueues seed parameters from ancestry and warm-start heuristics
- **Objective**: `aggregate_score - fragility_penalty` with selector stability, activity shortfall, turnover, and extreme parameter penalties
- **Space inference**: automatically infers Optuna search space from the spec payload's numeric params

### ReflectionRunner

- **Required frontmatter fields**: `family`, `verdict`, `failure_mode`, `why_parent_change_failed`, `failed_motif_signature`, `one_reusable_lesson`, `one_next_test`, `next_move`, `do_not_repeat`, `evidence_paths`, `tracking_tags`, `status`
- **Fallback frontmatter**: generated from the evaluation packet if the LLM output is malformed

## Hooks

Defined in `siglab/orchestration/hooks.py`. The `WorkspaceHooks` dataclass holds a `WorkspaceBuilder` and `WorkspaceSession`.

| Method | Called After | Behavior |
|--------|-------------|----------|
| `after_experiment(spec_hash, iteration_number)` | Each experiment iteration | Records the experiment card via `builder.record_experiment()`, refreshes frontier files. Returns the card reference path. |
| `after_reflection()` | The reflector stage | Refreshes frontier files so the next planner iteration sees updated lesson cards. |

## Trials

The `siglab/orchestration/trials.py` module contains scoring, patching, and generalization analysis utilities:

### Scoring (`score_diagnosis`)

Computes a component-level breakdown of how a spec compares to an incumbent:

| Component | Weight |
|-----------|--------|
| `median_sharpe` | 1.0 |
| `median_total_return` | 4.0 |
| `median_calmar` | 0.5 |
| `asset_breadth` | 0.1 |
| `profitable_window_pct` | 0.25 |
| `worst_max_drawdown` | 1.5 |

### Generalization (`summarize_generalization`)

Penalizes fragility signals to compute a `deployment_score`:

- Negative validation return
- Negative audit return
- Generalization gap (pre-audit vs validation)
- Audit gap (validation vs audit)
- Activity shortfall (active bar fraction < 15%)
- Excess turnover (> 10% mean)
- Transaction cost share (> 35%)
- Selector window return std (> 0.03 threshold, weight 8.0)
- Selector window Sharpe std (> 0.75 threshold, weight 0.75)
- Selector unprofitable window share (< 50% profitable, weight 2.0)
- Extreme parameter edge proximity
- Low bar count (< 72 bars)
- Stability penalty

### Patch Tracking (`build_spec_patch`)

Diffs a base spec against a target spec, producing a list of `{path, old, new}` change entries with spec hashes.

### Return Attribution (`summarize_return_attribution`)

Decomposes returns into price contribution, carry contribution, and transaction cost contribution. Labels the return driver as `price_dominant`, `carry_dominant`, or `mixed`.

## Configuration

### `SiglabConfig` (dataclass)

Defined in `siglab/config.py`. Constructed by `load_settings()`.

#### Directory Layout

| Field | Default | Description |
|-------|---------|-------------|
| `root_dir` | `Path(__file__).parents[1]` | Project root |
| `sosovalue_config_path` | `root / config.json` | SoSoValue API config |
| `generated_strategy_dir` | `siglab/live/deployed_agents` | Exported strategy specs |
| `data_lake_dir` | `root / data / cache` | Cached data |
| `artifact_dir` | `root / runs` | Run artifacts |
| `live_dir` | `root / live` | Live deployment dir |
| `ancestry_db_path` | `root / siglab.db` | SQLite ancestry DB |

#### LLM Provider Settings

| Field | Env Var | Default |
|-------|---------|---------|
| `llm_provider` | `LLM_PROVIDER` | auto-detected from API keys |
| `claude_api_key` | `CLAUDE_API_KEY` | `None` |
| `claude_model` | `CLAUDE_MODEL` | `"claude-k2.5"` |
| `claude_base_url` | `CLAUDE_BASE_URL` | `"https://api.moonshot.ai/v1"` |
| `claude_max_tokens` | `CLAUDE_MAX_TOKENS` | `32768` |
| `claude_temperature` | `CLAUDE_TEMPERATURE` | `1.0` |
| `claude_top_p` | `CLAUDE_TOP_P` | `0.95` |
| `claude_timeout_s` | `CLAUDE_TIMEOUT_S` | `300.0` |
| `claude_thinking` | `CLAUDE_THINKING` | `None` |
| `claude_max_tool_rounds` | `CLAUDE_MAX_TOOL_ROUNDS` | `25` |
| `deepseek_api_key` | `DEEPSEEK_API_KEY` | `None` |
| `deepseek_base_url` | `DEEPSEEK_BASE_URL` | `"https://api.deepseek.com"` |
| `deepseek_model` | `DEEPSEEK_MODEL` | `"deepseek-reasoner"` |
| `openrouter_api_key` | `OPENROUTER_API_KEY` | `None` |
| `openrouter_base_url` | `OPENROUTER_BASE_URL` | `"https://openrouter.ai/api/v1"` |
| `openrouter_model` | `OPENROUTER_MODEL` | `"openai/gpt-4.1-mini"` |
| `openrouter_reasoning_model` | `OPENROUTER_REASONING_MODEL` | `None` |
| `openrouter_fast_model` | `OPENROUTER_FAST_MODEL` | `None` |
| `openrouter_http_referer` | `OPENROUTER_HTTP_REFERER` | `None` |
| `openrouter_title` | `OPENROUTER_TITLE` | `None` |
| `bai_api_key` | `BAI_API_KEY` / `ANTHROPIC_AUTH_TOKEN` | `None` |
| `bai_base_url` | `BAI_BASE_URL` / `ANTHROPIC_BASE_URL` | `"https://api.b.ai"` |
| `bai_model` | `BAI_MODEL` / `ANTHROPIC_MODEL` | `"deepseek-v4-flash"` |
| `bai_planner_model` | `BAI_PLANNER_MODEL` | `"deepseek-v4-flash"` |
| `bai_writer_model` | `BAI_WRITER_MODEL` | `"deepseek-v4-flash"` |
| `bai_reflector_model` | `BAI_REFLECTOR_MODEL` | `"deepseek-v4-flash"` |
| `bai_fallback_fast_model` | `BAI_FALLBACK_FAST_MODEL` | `"kimi-k2.5"` |
| `bai_fallback_reasoning_model` | `BAI_FALLBACK_REASONING_MODEL` | `"deepseek-v4-pro"` |
| `bai_context_tokens` | `BAI_CONTEXT_TOKENS` | `70000` |
| `bai_max_call_credits` | `BAI_MAX_CALL_CREDITS` | `None` |

#### SoSoValue API Settings

| Field | Env Var | Default |
|-------|---------|---------|
| `sosovalue_api_key_override` | `SOSOVALUE_API_KEY` | `None` |
| `sosovalue_openapi_base_url` | `SOSOVALUE_OPENAPI_BASE_URL` | `"https://openapi.sosovalue.com/openapi/v1"` |
| `sosovalue_etf_base_url` | `SOSOVALUE_ETF_BASE_URL` | `"https://openapi.sosovalue.com"` |
| `sosovalue_news_base_url` | `SOSOVALUE_NEWS_BASE_URL` | `"https://openapi.sosovalue.com"` |
| `sosovalue_timeout_s` | `SOSOVALUE_TIMEOUT_S` | `30.0` |
| `sosovalue_retries` | `SOSOVALUE_RETRIES` | `2` |

#### Pipeline Settings

| Field | Env Var | Default |
|-------|---------|---------|
| `population_size` | `SIGLAB_POPULATION_SIZE` | `4` |
| `optuna_trials` | `SIGLAB_OPTUNA_TRIALS` | `20` |
| `memory_scope` | `SIGLAB_MEMORY_SCOPE` | `"session_local"` |
| `use_historical_seeds` | `SIGLAB_USE_HISTORICAL_SEEDS` | `false` |
| `tracks` | — | `CANONICAL_TRACKS` tuple |

#### Web Research Settings

| Field | Env Var | Default |
|-------|---------|---------|
| `tavily_api_key` | `TAVILY_API_KEY` | `None` |
| `tavily_base_url` | `TAVILY_BASE_URL` | `"https://api.tavily.com"` |
| `tavily_max_results` | `TAVILY_MAX_RESULTS` | `5` |
| `web_explore_results_per_query` | `WEB_EXPLORE_RESULTS_PER_QUERY` | `2` |

### `config.json`

Located at the project root. Contains SoSoValue API credentials:

```json
{
  "system": {
    "api_base_url": "https://openapi.sosovalue.com/openapi/v1",
    "api_key": "SOSO-e5b2e5..."
  }
}
```

The `config.example.json` ships with an empty `api_key` for reference.

### Environment Files

| File | Purpose |
|------|---------|
| `.env` | Primary environment overrides (gitignored) |
| `.siglab-provider.env` | Provider-specific overrides (gitignored). Path overridable via `SIGLAB_PROVIDER_CONFIG_PATH` |

## Settings Cascade

Configuration is loaded by `load_settings()` with the following precedence (highest wins):

1. **Environment variables** (`os.getenv`) — checked first
2. **`.env` file** at project root
3. **`.siglab-provider.env`** (or path from `SIGLAB_PROVIDER_CONFIG_PATH`)
4. **Hardcoded defaults** in the `SiglabConfig` dataclass

LLM provider auto-detection order (when `LLM_PROVIDER` is not set):
1. `CLAUDE_API_KEY` → `"claude"`
2. `DEEPSEEK_API_KEY` → `"deepseek"`
3. `ANTHROPIC_AUTH_TOKEN` or `BAI_API_KEY` → `"bai"`
4. `OPENROUTER_API_KEY` → `"openrouter"`
5. Fallback → `"claude"`

**Environment variable aliases:**
- `OPENROUTER_KEY` aliases `OPENROUTER_API_KEY`
- `ANTHROPIC_AUTH_TOKEN` aliases `BAI_API_KEY`
- `ANTHROPIC_BASE_URL` aliases `BAI_BASE_URL`
- `ANTHROPIC_MODEL` aliases `BAI_MODEL`

## Dependencies

Key packages from `pyproject.toml`:

| Package | Version | Used For |
|---------|---------|----------|
| `python` | `^3.12` | Runtime |
| `certifi` | `^2025.11.12` | CA certificate bundle (TLS verification) |
| `httpx` | `^0.28.1` | HTTP client (API calls) |
| `websockets` | `^15.0.1` | SoDEX WebSocket probes |
| `numpy` | `^1.26.0` | Numerical computation |
| `pandas` | `^2.2.0` | Time series / backtest data |
| `pyarrow` | `^20.0.0` | Parquet / columnar data |
| `pyyaml` | `^6.0.2` | YAML parsing (specs, manifests) |
| `optuna` | `^4.5.0` | Hyperparameter optimization |
| `pydantic` | `^2.13.4` | Data validation |
| `fastapi` | `^0.136.3` | Dashboard API server |
| `uvicorn` | `^0.48.0` | ASGI server |
| `textual` | `^8.2.7` | Terminal UI |
| `rich` | `^15.0.0` | Console formatting |

Dev dependencies: `ruff`, `pytest`, `pytest-timeout`, `pytest-asyncio`, `textual-dev`.

## Testing

Run orchestration tests:

```bash
# All tests (quiet mode)
python3 -m pytest -q

# Only orchestration-related tests
python3 -m pytest tests/ -q -k orchestration

# Integration tests (real API calls)
python3 -m pytest -m integration

# With async support (configured via asyncio_mode = "auto")
python3 -m pytest tests/test_cli_agent_safety.py -q
```

Profile the full pipeline:

```bash
python3 -m siglab.cli profile --strict --json
```

Generate a demo manifest to verify artifact indexing:

```bash
python3 -m siglab.cli demo-manifest --json
```
