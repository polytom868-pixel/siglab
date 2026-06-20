# CLI System

## Purpose

The SigLab CLI (`python -m siglab.cli`) provides operator-facing commands for all SigLab functions: research execution, evidence collection, market reporting, paper trading, benchmarking, deployment, SoDEX live-boundary probing, telemetry aggregation, demo artifact generation, and system profiling. It is the primary human and agent interface to the SigLab research-to-action pipeline.

---

## Architecture

### Entry Point

The CLI is a Python package at `siglab/cli/`. The entry point is `siglab.cli.main()`, invoked via:

```bash
python -m siglab.cli <command> [options]
```

`__main__.py` simply calls `main()`, so `python -m siglab.cli` and `from siglab.cli import main; main()` are equivalent.

### Parser Structure

`main()` in `__init__.py` builds a top-level `argparse.ArgumentParser` with `prog="siglab"`, then creates a `subparsers` group with `required=True`. Each command module exposes an `add_subparser(subparsers)` function that registers one or more subcommands. After parsing, `main()` dispatches to the appropriate handler by matching `args.command`.

A global `--no-color` flag disables ANSI output (also respects the `NO_COLOR` environment variable).

### Command Modules

Commands are organized into domain-specific modules under `siglab/cli/`:

| Module | Commands | Sync/Async |
|---|---|---|
| `profile.py` | `profile` | Sync |
| `config_cmd.py` | `config` (sub: `validate`) | Sync |
| `api.py` | `api-surface` | Sync |
| `evidence.py` | `evidence-build`, `evidence-map` | Async / Sync |
| `demo.py` | `demo-report`, `demo-manifest`, `demo-refresh`, `wave-status` | Sync |
| `market.py` | `market-report` | Sync |
| `telemetry.py` | `telemetry-report` | Sync |
| `dashboard.py` | `dashboard`, `dashboard-start`, `dashboard-stop` | Sync |
| `deploy.py` | `deploy`, `deployments` | Async / Sync |
| `sodex.py` | `sodex-preflight`, `valuechain-preflight`, `sodex-ws-probe`, `sodex-preview` | Sync / Async |
| `benchmark.py` | `benchmark-init`, `benchmark-eval`, `benchmark-status` | Sync / Async |
| `paper.py` | `paper-start`, `paper-status`, `paper-promote` | Async |
| `ancestry_cmd.py` | `ancestry`, `clear-passed` | Sync |
| `run.py` | `run`, `inspect` | Async |

### Shared Utilities

| Module | Purpose |
|---|---|
| `helpers.py` | Shared helper functions (JSONL reading, path resolution, preflight reports, evidence sorting, deployment eligibility, enum parsing, etc.) |
| `rich_utils.py` | Rich console, tables, panels, progress bars, semantic print helpers, JSON output |
| `run.py` | The main research run loop and inspect command (~1,300 lines) |

### Backward Compatibility

`__init__.py` re-exports symbols from submodules so that existing `from siglab.cli import ...` statements continue to work. New code should import from the specific submodule (e.g. `from siglab.cli.helpers import ...`).

---

## Command Reference

### `profile`

Run the SigLab hardening profile.

```
python -m siglab.cli profile [--json] [--strict]
```

| Flag | Description |
|---|---|
| `--json` | Output profile as JSON instead of a Rich panel |
| `--strict` | Exit with non-zero code if any strict failures are found (exit code = min(failures, 125)) |

### `config validate`

Validate `config.json` and environment settings.

```
python -m siglab.cli config validate
```

No additional flags. Exits 0 on success, 1 on validation errors.

### `api-surface`

Summarize source-of-truth SoSoValue/SoDEX API surface maps.

```
python -m siglab.cli api-surface [--json]
```

Reads YAML surface docs from `docs/` and reports line counts, endpoint path mentions, and status keywords (supported/missing/blocked).

### `evidence-build`

Build a source-backed SoSoValue evidence JSONL from verified API surfaces.

```
python -m siglab.cli evidence-build [options]
```

| Flag | Default | Description |
|---|---|---|
| `--etf-type` | `us-btc-spot` | ETF type identifier |
| `--currency` | `BTC` | Currency symbol for news filtering |
| `--news-page-size` | `10` | News page size |
| `--news-pages` | `1` | Number of news pages to fetch |
| `--output` | auto | Output JSONL path |
| `--summary-output` | auto | Summary JSON output path |
| `--summary-top-links` | `10` | Top links in summary |
| `--json` | flag | Print result as JSON |

Fetches ETF historical inflow and featured news from SoSoValue, normalizes into evidence records, writes JSONL and summary.

### `evidence-map`

Render an HTML evidence graph from an evidence summary artifact.

```
python -m siglab.cli evidence-map [options]
```

| Flag | Default | Description |
|---|---|---|
| `--summary` | auto | Path to summary JSON |
| `--evidence` | auto | Path to evidence JSONL (rebuilds summary) |
| `--output` | auto | HTML output path |
| `--json` | flag | Print result as JSON |

### `market-report`

Build a deterministic SoSoValue + SoDEX evidence-linked market report.

```
python -m siglab.cli market-report [options]
```

| Flag | Default | Description |
|---|---|---|
| `--entity` | `BTC` | Entity to report on |
| `--sosovalue-evidence` | auto | SoSoValue evidence JSONL path |
| `--sodex-evidence` | auto | SoDEX evidence JSONL path |
| `--output` | auto | JSON output path |
| `--html-output` | auto | HTML output path |
| `--json` | flag | Print result as JSON |

Produces JSON and HTML reports with signal summary, decision support stance, evidence quality metrics, and explicit warnings that evidence links are not causal claims.

### `demo-report`

Emit a buildathon/operator demo report from latest evidence and readiness artifacts.

```
python -m siglab.cli demo-report [options]
```

| Flag | Default | Description |
|---|---|---|
| `--output` | `runs/demo_report.json` | JSON output path |
| `--html-output` | auto | HTML output path |
| `--json` | flag | Print result as JSON |

### `demo-manifest`

Index latest demo artifacts, telemetry, evidence, and live-boundary readiness.

```
python -m siglab.cli demo-manifest [options]
```

| Flag | Default | Description |
|---|---|---|
| `--output` | `runs/demo_manifest_latest.json` | JSON output path |
| `--html-output` | auto | HTML output path |
| `--json` | flag | Print result as JSON |

### `demo-refresh`

Refresh safe demo artifacts for the ops board without submitting live trades. Regenerates preflight, telemetry, market report, demo report, wave status, and demo manifest in a single run.

```
python -m siglab.cli demo-refresh [options]
```

| Flag | Default | Description |
|---|---|---|
| `--wave-number` | `1` | Wave number for wave status |
| `--goal` | `refresh buildathon demo artifacts` | Goal label |
| `--json` | flag | Print result as JSON |

### `wave-status`

Write the latest operator/agent wave status artifact consumed by the ops board.

```
python -m siglab.cli wave-status [options]
```

| Flag | Default | Description |
|---|---|---|
| `--wave-number` | *required* | Wave number |
| `--phase` | `execution` | Phase label |
| `--status` | `running` | One of: `running`, `passed`, `blocked`, `failed` |
| `--goal` | *required* | Goal description |
| `--agents` | `""` | Comma-separated agent role labels |
| `--outputs` | `""` | Comma-separated wave output labels |
| `--blockers` | `""` | Comma-separated blockers |
| `--validation-status` | `not_run` | Validation status string |
| `--next-decision` | `""` | Next decision text |
| `--output` | auto | Output path |
| `--json` | flag | Print result as JSON |

### `telemetry-report`

Aggregate empirical LLM/tool telemetry from run trace artifacts.

```
python -m siglab.cli telemetry-report [options]
```

| Flag | Default | Description |
|---|---|---|
| `--track` | `all` | Track name or `all` |
| `--run-session-id` | None | Filter by run session ID |
| `--json` | flag | Output as JSON (default is Rich table) |

### `sodex-preflight`

Check SoDEX signed-path prerequisites and report readiness.

```
python -m siglab.cli sodex-preflight [--json]
```

Reports: public read readiness, schema pinning, signed path readiness (checks `SODEX_API_KEY_NAME`, `SODEX_ACCOUNT_ID`, `SODEX_NONCE_STORE_PATH`, `SODEX_PRIVATE_KEY`, `SODEX_ENVIRONMENT`), live write permission, missing prerequisites, rate limit budget, and supported/unsupported signed actions.

### `valuechain-preflight`

Verify ValueChain RPC chain-id against expected value (read-only readiness check).

```
python -m siglab.cli valuechain-preflight [options]
```

| Flag | Default | Description |
|---|---|---|
| `--rpc-url` | `https://mainnet.valuechain.xyz` | RPC endpoint URL |
| `--expected-chain-id` | `286623` | Expected chain ID |
| `--json` | flag | Output as JSON |

### `sodex-ws-probe`

Connect to the SoDEX public WebSocket and receive a single update as evidence.

```
python -m siglab.cli sodex-ws-probe [options]
```

| Flag | Default | Description |
|---|---|---|
| `--environment` | `mainnet` | `mainnet` or `testnet` |
| `--market` | `perps` | `spot` or `perps` |
| `--channel` | `allBookTicker` | WebSocket channel |
| `--symbol` | None | Optional symbol filter |
| `--user-address` | None | Optional user address |
| `--account-id` | None | Optional account ID |
| `--timeout-seconds` | `8.0` | Connection timeout |
| `--evidence-output` | None | Path to write evidence JSONL |
| `--json` | flag | Output as JSON |

### `sodex-preview`

Preview a signed SoDEX request without signing or submitting. Shows canonical body, signing payload, and signature input.

```
python -m siglab.cli sodex-preview [options]
```

| Flag | Default | Description |
|---|---|---|
| `--kind` | *required* | One of: `new-order`, `cancel-order`, `schedule-cancel`, `update-leverage`, `update-margin` |
| `--account-id` | *required* | Account ID |
| `--symbol-id` | *required* | Symbol ID |
| `--nonce` | *required* | Nonce |
| `--cl-ord-id` | `siglab-preview` | Client order ID |
| `--modifier` | `NORMAL` | Order modifier (NORMAL, STOP, BRACKET, ATTACHED_STOP) |
| `--side` | `BUY` | Order side (BUY, SELL) |
| `--order-type` | `LIMIT` | Order type (LIMIT, MARKET) |
| `--time-in-force` | `GTC` | Time in force (GTC, FOK, IOC, GTX) |
| `--price` | None | Order price |
| `--quantity` | None | Order quantity |
| `--funds` | None | Order funds |
| `--order-id` | None | Order ID (for cancel) |
| `--orig-cl-ord-id` | None | Original client order ID (for cancel) |
| `--scheduled-timestamp` | None | Scheduled cancel timestamp |
| `--amount` | None | Margin amount (for update-margin) |
| `--reduce-only` | flag | Reduce-only flag |
| `--position-side` | `BOTH` | Position side (BOTH, LONG, SHORT) |
| `--leverage` | `1` | Leverage value |
| `--margin-mode` | `ISOLATED` | Margin mode (ISOLATED, CROSS) |
| `--json` | flag | Accepted for consistency; output is always JSON |

### `benchmark-init`

Initialize a benchmark deck.

```
python -m siglab.cli benchmark-init [options]
```

| Flag | Default | Description |
|---|---|---|
| `--deck` | default deck | Benchmark deck name |
| `--agent-label` | `external_agent` | Agent label |
| `--run-label` | None | Run label |
| `--force` | flag | Force re-initialization |

### `benchmark-eval`

Evaluate a benchmark deck.

```
python -m siglab.cli benchmark-eval [--deck <name>]
```

| Flag | Default | Description |
|---|---|---|
| `--deck` | default deck | Benchmark deck name |

Requires SoSoValue config (fetches market data for evaluation).

### `benchmark-status`

Show benchmark deck status.

```
python -m siglab.cli benchmark-status [--deck <name>]
```

### `paper-start`

Create a new paper trading session.

```
python -m siglab.cli paper-start [options]
```

| Flag | Default | Description |
|---|---|---|
| `--session` | None | Optional session label |
| `--sessions-dir` | `sessions/` | Directory for session `.npy` files |

### `paper-status`

Show paper trading session status, processing open orders against latest klines and fetching mark prices.

```
python -m siglab.cli paper-status --session <id> [--sessions-dir <dir>]
```

| Flag | Default | Description |
|---|---|---|
| `--session` | *required* | Session ID |
| `--sessions-dir` | `sessions/` | Directory for session files |

### `paper-promote`

Check paper session promotion eligibility and promote if eligible. Evaluates composite score, sub-scores, consecutive days, and minimum trading days.

```
python -m siglab.cli paper-promote --session <id> [options]
```

| Flag | Default | Description |
|---|---|---|
| `--session` | *required* | Session ID |
| `--sessions-dir` | `sessions/` | Directory for session files |
| `--threshold` | default | Promotion score threshold |
| `--consecutive-days` | default | Required consecutive days above threshold |
| `--min-trading-days` | default | Minimum trading days required |

Exits 0 if promoted, 1 if not eligible.

### `ancestry`

Display the ancestry lineage table.

```
python -m siglab.cli ancestry [options]
```

| Flag | Default | Description |
|---|---|---|
| `--track` | None | Track name filter |
| `--limit` | `10` | Max rows |
| `--json` | flag | Output as JSON (default is Rich table) |

### `clear-passed`

Clear passed but not deployed specs from the ancestry store.

```
python -m siglab.cli clear-passed [--track <name>|all]
```

| Flag | Default | Description |
|---|---|---|
| `--track` | `all` | Track name or `all` |

### `deploy`

Deploy a spec from the ancestry store.

```
python -m siglab.cli deploy --spec <hash> [options]
```

| Flag | Default | Description |
|---|---|---|
| `--spec` | *required* | Spec hash to deploy |
| `--agent-id` | None | Agent identifier |
| `--wallet-label` | None | Wallet label |
| `--config` | None | Config file path |
| `--job-name` | None | Job name |
| `--interval` | None | Interval in seconds |
| `--schedule` | flag | Enable scheduling |
| `--llm-finalize` | flag | Use LLM to finalize deployment |
| `--live` | flag | Enable live execution (default is dry-run) |

Checks deployment eligibility (passed status, fragility label, audit return, active bar count).

### `deployments`

List deployments or inspect a specific deployment.

```
python -m siglab.cli deployments [--spec <hash>]
```

### `dashboard`

Start the legacy embedded dashboard server.

```
python -m siglab.cli dashboard [--host 127.0.0.1] [--port 8765]
```

### `dashboard-start`

Start the FastAPI dashboard server.

```
python -m siglab.cli dashboard-start [options]
```

| Flag | Default | Description |
|---|---|---|
| `--host` | `0.0.0.0` | Bind address |
| `--port` | `3100` | Port |
| `--reload` | flag | Enable auto-reload for development |

### `dashboard-stop`

Stop the running FastAPI dashboard.

```
python -m siglab.cli dashboard-stop [--port 3100]
```

### `run`

Execute the main research loop. Runs LLM-powered strategy generation, evaluation, and lineage tracking.

```
python -m siglab.cli run [options]
```

| Flag | Default | Description |
|---|---|---|
| `--track` | `all` | Track name or `all` |
| `--population-size` | None | Population size |
| `--family` | None | Single family to run |
| `--families` | None | Comma-separated family list |
| `--resume-run` | None | Resume existing run session by ID |
| `--burn-in-iterations` | `0` | Deterministic iterations before main phase |
| `--iterations` | `1` | Generations per track (0 = infinite) |
| `--max-runtime-seconds` | None | Wall-clock budget |
| `--max-total-cost` | None | *Not enforced* (errors if set) |
| `--max-total-credits` | None | B.AI Credits budget (not USD) |
| `--max-call-estimated-credits` | None | Per-call B.AI Credits limit |
| `--max-provider-errors` | None | Max provider errors before stopping |
| `--max-consecutive-no-improvement` | None | Max stale iterations |
| `--max-consecutive-crashes` | None | Max consecutive crashes |
| `--cooldown-seconds-on-429` | `0.0` | Cooldown on rate-limit errors |
| `--provider-fallback-on-quota` | flag | Fall back to alternate provider on quota |
| `--stop-on-live-surface-unavailable` | flag | Stop if live surface is unavailable |
| `--resume-safe-check` | flag | Enable resume safety check |
| `--memory-scope` | `session_local` | `session_local` or `track_shared` |
| `--symbols` | None | Comma-separated basis symbols |
| `--use-historical-seeds` | flag | Use best historical family seeds |
| `--skip-llm` | flag | Skip LLM calls (deterministic only) |
| `--agent-label` | `siglab_harness` | Agent label |
| `--run-label` | None | Run label |

### `inspect`

Inspect track state and recent results.

```
python -m siglab.cli inspect [--track <name>|all]
```

---

## Rich Output

The CLI uses the [Rich](https://github.com/Textualize/rich) library for formatted terminal output via `siglab/cli/rich_utils.py`.

### Shared Console

A module-level `Console` instance is initialized once at CLI startup via `init_console(force_no_color=...)`. All Rich output goes through `get_console()`.

### Semantic Theme

A custom `SIGLAB_THEME` defines semantic styles:

| Style | Usage |
|---|---|
| `success` | Bold green — passed checks, completions |
| `error` | Bold red — failures, blocked states |
| `warning` | Bold yellow — partial states, cautions |
| `info` | Bold blue — informational messages |
| `muted` | Dim — secondary/decorative text |
| `accent` | Bold cyan — spec hashes, highlights |
| `label` | Bold — table column headers |

### Output Components

| Function | Purpose |
|---|---|
| `print_json(data)` | JSON with syntax highlighting (plain JSON when piped/no-color) |
| `make_table(title)` | Consistently styled Rich table |
| `print_panel(content, title)` | Content in a bordered panel |
| `print_key_value_pairs(title, pairs)` | Key-value table display |
| `make_progress()` | Progress bar with spinner, bar, count, and elapsed time |
| `print_success(msg)` | Green checkmark message |
| `print_error(msg)` | Red cross message |
| `print_warning(msg)` | Yellow warning message |
| `print_info(msg)` | Blue info message |
| `print_header(title)` | Section header with horizontal rule |
| `print_muted(msg)` | Dimmed message |
| `print_status_line(msg, style)` | Single styled status line |
| `status_style(value)` | Maps boolean/status strings to Rich style names |

---

## JSON Output

Nearly every command supports a `--json` flag for machine-readable output. The pattern is consistent:

1. Build a payload dict
2. If `--json` is set, call `print_json(payload)` and return
3. Otherwise, render via Rich tables/panels, then print a summary JSON

`print_json()` uses plain `json.dumps` (no ANSI) when:
- `--no-color` flag is set
- `NO_COLOR` environment variable is set
- stdout is not a TTY (piped output)

This makes all commands safe for piping to `jq`, files, or agent consumers.

---

## Configuration

### `config.json`

SigLab reads its primary configuration from `config.json` (path set via `SOSOVALUE_CONFIG_PATH` or defaults to `config.json` in the root directory). The config is validated by `config validate` and must contain:

```json
{
  "system": {
    "api_key": "...",
    "api_base_url": "..."
  }
}
```

> **Note:** The `system.api_key` in `config.json` is consumed directly by the SoSoValue API client, not by `load_settings()`. The `load_settings()` function resolves `SiglabConfig` from environment variables and env files only. `SOSOVALUE_API_KEY` env var serves as an override.

### Environment Variables

SigLab reads environment variables from the process, `.env` at the repo root, and `.siglab-provider.env`. Resolved by `load_settings()` in `siglab/config.py` unless noted.

#### SoSoValue

| Variable | Default | Purpose |
|---|---|---|
| `SOSOVALUE_CONFIG_PATH` | `config.json` | Path to SoSoValue config file |
| `SOSOVALUE_API_KEY` | None | SoSoValue API key override |
| `SOSOVALUE_OPENAPI_BASE_URL` | `https://openapi.sosovalue.com/openapi/v1` | OpenAPI base URL |
| `SOSOVALUE_ETF_BASE_URL` | `https://api.sosovalue.xyz` | ETF base URL |
| `SOSOVALUE_NEWS_BASE_URL` | `https://openapi.sosovalue.com` | News base URL |
| `SOSOVALUE_TIMEOUT_S` | `30` | Request timeout (seconds) |
| `SOSOVALUE_RETRIES` | `2` | Retry count |

#### LLM Providers

| Variable | Default | Purpose |
|---|---|---|
| `LLM_PROVIDER` | auto-detected | Explicit provider (`claude`, `deepseek`, `bai`, `openrouter`) |
| `CLAUDE_API_KEY` | None | Claude/Moonshot API key |
| `CLAUDE_MODEL` | `claude-k2.5` | Model name |
| `CLAUDE_BASE_URL` | `https://api.moonshot.ai/v1` | Base URL |
| `CLAUDE_MAX_TOKENS` | `32768` | Max tokens per request |
| `CLAUDE_TEMPERATURE` | `1.0` | Sampling temperature |
| `CLAUDE_TIMEOUT_S` | `300` | Request timeout |
| `DEEPSEEK_API_KEY` | None | DeepSeek API key |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | DeepSeek base URL |
| `DEEPSEEK_MODEL` | `deepseek-reasoner` | Model name |
| `OPENROUTER_API_KEY` | None | OpenRouter key (alias: `OPENROUTER_KEY`) |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | Base URL |
| `OPENROUTER_MODEL` | `openai/gpt-4.1-mini` | Model name |
| `OPENROUTER_REASONING_MODEL` | None | Reasoning model override |
| `OPENROUTER_FAST_MODEL` | None | Fast model override |
| `ANTHROPIC_AUTH_TOKEN` / `BAI_API_KEY` | None | B.AI key (first non-null wins) |
| `ANTHROPIC_BASE_URL` | `https://api.b.ai` | B.AI base URL |
| `ANTHROPIC_MODEL` | `deepseek-v4-flash` | B.AI default model |
| `BAI_PLANNER_MODEL` | same as ANTHROPIC_MODEL | Planner agent model |
| `BAI_WRITER_MODEL` | `deepseek-v4-flash` | Writer agent model |
| `BAI_REFLECTOR_MODEL` | `deepseek-v4-flash` | Reflector agent model |
| `BAI_FALLBACK_FAST_MODEL` | `kimi-k2.5` | Fast fallback |
| `BAI_FALLBACK_REASONING_MODEL` | `deepseek-v4-pro` | Reasoning fallback |
| `BAI_CONTEXT_TOKENS` | `70000` | Context token budget |

#### Web Research

| Variable | Default | Purpose |
|---|---|---|
| `TAVILY_API_KEY` | None | Tavily API key |
| `TAVILY_BASE_URL` | `https://api.tavily.com` | Tavily base URL |
| `TAVILY_MAX_RESULTS` | `5` | Max results per query |

#### SigLab Runtime

| Variable | Default | Purpose |
|---|---|---|
| `SIGLAB_PROVIDER_CONFIG_PATH` | `.siglab-provider.env` | Provider env file path |
| `SIGLAB_STRATEGY_EXPORT_DIR` | `siglab/live/deployed_agents` | Strategy export directory |
| `SIGLAB_POPULATION_SIZE` | `4` | Population size for research runs |
| `SIGLAB_OPTUNA_TRIALS` | `20` | Optuna trial count |
| `SIGLAB_MEMORY_SCOPE` | `session_local` | Memory scope |
| `SIGLAB_USE_HISTORICAL_SEEDS` | `false` | Use best historical seeds |

#### SoDEX (used directly by CLI, not by `load_settings()`)

| Variable | Purpose |
|---|---|
| `SODEX_API_KEY_NAME` | SoDEX API key name |
| `SODEX_ACCOUNT_ID` | SoDEX account ID |
| `SODEX_NONCE_STORE_PATH` | Path to nonce store JSON |
| `SODEX_PRIVATE_KEY` | EVM private key for signing |
| `SODEX_ENVIRONMENT` | `testnet` (default) or `mainnet` |
| `SODEX_TESTNET_PREFLIGHT_PASSED` | Must be `true` before mainnet |
| `SODEX_MAINNET_LIVE_WRITE_CONFIRMATION` | Must be `I_UNDERSTAND_MAINNET_RISK` |

#### Terminal

| Variable | Purpose |
|---|---|
| `NO_COLOR` | Disable ANSI color output |

### Settings Resolution

All commands call `load_settings()` from `siglab.config` to resolve the `SiglabConfig` dataclass, which provides `root_dir`, `artifact_dir`, `ancestry_db_path`, `data_lake_dir`, track list, and SoSoValue endpoint configuration.

---

## Testing

### Test File

CLI tests are in `tests/test_cli_agent_safety.py`. The test suite covers:

- Profile command strict JSON output
- CLI help text verification (`--run-label`, `--max-total-credits`, etc.)
- Telemetry trace path filtering by track and run session
- Provider metrics artifact persistence and discovery
- Deployment eligibility reasons
- Max total cost fast-fail behavior
- Credit budget stop behavior (B.AI Credits, not USD)
- Demo report HTML honesty (no live execution overclaim)
- Demo manifest artifact indexing without overclaim
- Market report evidence linking without causality claims
- Market report handling of stale/malformed evidence rows
- Resume run workspace state resolution
- SoSoValue config validation
- Currency ID resolution
- SoDEX preflight (missing prerequisites, malformed account ID, nonce store validation, mainnet blocking, secret non-exposure)
- Wave status structured payloads
- SoDEX preview (canonical body, signing payload, enum aliases, cancel/schedule-cancel/margin paths)
- Audit field stripping
- Agent-safe memory packet sanitization
- External research extraction (Tavily tool calls only)
- Run reflection (deterministic row exclusion, audit field exclusion)

### Running Tests

```bash
python3 -m pytest -q tests/test_cli_agent_safety.py
```

Or run the full test suite:

```bash
python3 -m pytest -q
```

### CLI Smoke Tests

Several tests invoke the CLI as a subprocess to verify end-to-end behavior:

```bash
python3 -m siglab.cli profile --strict --json
python3 -m siglab.cli run --help
python3 -m siglab.cli sodex-preview --kind update-margin --account-id 1001 --symbol-id 1 --nonce 1760373925000 --amount -0.25 --json
```
