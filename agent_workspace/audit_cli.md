# SigLab CLI — Surgical Audit Report

Scope: `siglab/cli/*.py`, `siglab/__init__.py`, `siglab/__main__.py`. All subcommands are registered via `argparse` (NOT `@click` / `@app.command` / typer). The CLI is a top-level `argparse.ArgumentParser` with `add_subparsers(required=True)` in `siglab/cli/__init__.py`.

---

## 1. COMMAND REGISTRY

All subparsers are registered in `siglab/cli/__init__.py:96-103` (root parser) and individually defined in each `add_subparser` module. Dispatch table is in `siglab/cli/__init__.py:146-235`.

### 1.1 `ancestry` — `siglab/cli/ancestry_cmd.py:13-30`
- `ancestry`: `--track` (choices=`TRACK_CLI_CHOICES`, default=`None`), `--limit` (int, default=10), `--json` (store_true). Dispatches to `run_ancestry` at `__init__.py:176-178`.
- `clear-passed`: `--track` (choices=`["all", *TRACK_CLI_CHOICES]`, default=`"all"`). No `--json`; always JSON. Dispatches to `run_clear_passed` at `__init__.py:179-181`.
- No mutual-exclusion flags. Cross-file invocations: `LineageStore.list_rows` (line 36), `LineageStore.clear_spec` (line 78), `LineageStore.dashboard_rows` (line 75).

### 1.2 `api-surface` — `siglab/cli/api.py:12-17`
- `api-surface`: `--json`. Dispatches to `run_command` at `__init__.py:173-175`. Reads `docs/*.yaml|md` only; no config or credentials.

### 1.3 `benchmark-init` / `benchmark-eval` / `benchmark-status` — `siglab/cli/benchmark.py:23-49`
- `benchmark-init`: `--deck` (choices=`supported_deck_names()`, default=`DEFAULT_BENCHMARK_DECK`), `--agent-label` (default=`"external_agent"`), `--run-label` (default=`None`), `--force` (store_true). Dispatch `__init__.py:212-214` → `run_benchmark_init` (line 52).
- `benchmark-eval`: `--deck` only. Dispatch `__init__.py:215-217` → `run_benchmark_eval` (line 74, async).
- `benchmark-status`: `--deck` only. Dispatch `__init__.py:218-220` → `run_benchmark_status` (line 98).
- All three call `load_settings()`. Only `run_benchmark_eval` calls `require_sosovalue_config(settings)` (line 76).
- Cross-file: `LineageStore`, `ClaudeClient`, `SpecMutator`, `ResearchEvaluator`, `MarketDataProvider`, `ParquetLake`.

### 1.4 `config` — `siglab/cli/config_cmd.py:11-25`
- `config validate` (subcommand via `add_subparsers(dest="config_command", required=True)`): no flags. Dispatch `__init__.py:182-184` → `config_validate_command` (line 28). Calls `require_sosovalue_config` indirectly through `load_settings()` then reads `settings.sosovalue_config_path`.

### 1.5 `dashboard` / `dashboard-start` / `dashboard-stop` — `siglab/cli/dashboard.py:13-33`
- `dashboard`: `--host` (default=`"127.0.0.1"`), `--port` (int, default=8765). Dispatch `__init__.py:185-187` → `run_dashboard` (line 36).
- `dashboard-start`: `--host` (default=`"0.0.0.0"`), `--port` (int, default=3100), `--reload` (store_true). Dispatch `__init__.py:188-190` → `run_dashboard_start` (line 41, calls `uvicorn.run`).
- `dashboard-stop`: `--port` (int, default=3100). Dispatch `__init__.py:191-193` → `run_dashboard_stop` (line 59, executes `lsof -ti :PORT`).

### 1.6 `demo-report` / `demo-manifest` / `demo-refresh` / `wave-status` — `siglab/cli/demo.py:26-69`
- `demo-report`: `--output`, `--html-output`, `--json`. Dispatch `__init__.py:161-163` → `run_demo_report` (line 77).
- `demo-manifest`: `--output`, `--html-output`, `--json`. Dispatch `__init__.py:164-166` → `run_demo_manifest` (line 221).
- `demo-refresh`: `--wave-number` (int, default=1), `--goal` (default=`"refresh buildathon demo artifacts"`), `--json`. Dispatch `__init__.py:167-169` → `run_demo_refresh` (line 370).
- `wave-status`: `--wave-number` (int, required), `--phase` (default=`"execution"`), `--status` (choices=`["running","passed","blocked","failed"]`, default=`"running"`), `--goal` (required), `--agents`, `--outputs`, `--blockers`, `--validation-status` (default=`"not_run"`), `--next-decision`, `--output`, `--json`. Dispatch `__init__.py:221-223` → `run_wave_status` (line 464).

### 1.7 `deploy` / `deployments` — `siglab/cli/deploy.py:21-37`
- `deploy`: `--spec` (required), `--agent-id`, `--wallet-label`, `--config` (dest=`config_path`), `--job-name`, `--interval` (dest=`interval_seconds`, int), `--schedule` (store_true), `--llm-finalize` (store_true), `--live` (store_true). Dispatch `__init__.py:194-196` → `run_deploy` (line 39, async). Calls `require_sosovalue_config` (line 41) and constructs `LiveDeploymentManager` (lines 45-49). No `--json` flag.
- `deployments`: `--spec` (optional, default=`None`). Dispatch `__init__.py:197-199` → `run_deployments` (line 104). No `--json`; always JSON.

### 1.8 `evidence-build` / `evidence-map` — `siglab/cli/evidence.py:21-44`
- `evidence-build`: `--etf-type` (default=`"us-btc-spot"`), `--currency` (default=`"BTC"`), `--news-page-size` (int, default=10), `--news-pages` (int, default=1), `--output`, `--summary-output`, `--summary-top-links` (int, default=10), `--json`. Dispatch `__init__.py:155-157` → `run_evidence_build` (line 47, async).
- `evidence-map`: `--summary`, `--evidence`, `--output`, `--json`. Dispatch `__init__.py:158-160` → `run_evidence_map` (line 137).

### 1.9 `market-report` — `siglab/cli/market.py:24-34`
- `market-report`: `--entity` (default=`"BTC"`), `--sosovalue-evidence`, `--sodex-evidence`, `--output`, `--html-output`, `--json`. Dispatch `__init__.py:170-172` → `run_command` (line 37).

### 1.10 `paper-start` / `paper-status` / `paper-promote` — `siglab/cli/paper.py:23-49`
- `paper-start`: `--session`, `--sessions-dir`. Dispatch `__init__.py:227-229` → `run_paper_start` (line 52, async).
- `paper-status`: `--session` (required), `--sessions-dir`. Dispatch `__init__.py:230-232` → `run_paper_status` (line 59, async).
- `paper-promote`: `--session` (required), `--sessions-dir`, `--threshold` (float), `--consecutive-days` (int), `--min-trading-days` (int). Dispatch `__init__.py:233-235` → `run_paper_promote` (line 95, async). No `--json`; always JSON via `print_json`.

### 1.11 `profile` — `siglab/cli/profile.py:12-18`
- `profile`: `--json`, `--strict`. Dispatch `__init__.py:152-154` → `run_command` (line 21).

### 1.12 `run` / `inspect` — `siglab/cli/run.py:62-147`
- `run`: `--track` (default=`"all"`), `--population-size`, `--family`, `--families`, `--resume-run`, `--burn-in-iterations` (int, default=0), `--iterations` (int, default=1, 0=infinite), `--max-runtime-seconds` (float), `--max-total-cost` (float, REJECTED — see §2), `--max-total-credits` (float, NOT USD), `--max-call-estimated-credits` (float, NOT USD), `--max-provider-errors` (int), `--max-consecutive-no-improvement` (int), `--max-consecutive-crashes` (int), `--cooldown-seconds-on-429` (float, default=0.0), `--provider-fallback-on-quota` (store_true), `--stop-on-live-surface-unavailable` (store_true), `--resume-safe-check` (store_true), `--memory-scope` (choices=`["session_local","track_shared"]`), `--symbols`, `--use-historical-seeds` (store_true, default=`None`), `--skip-llm` (store_true), `--agent-label` (default=`"siglab_harness"`), `--run-label`. Dispatch `__init__.py:146-148` → `run_command` (line 150, async).
- `inspect`: `--track` (default=`"all"`). Dispatch `__init__.py:149-151` → `inspect_command` (line 842, async).
- Mutual-exclusion: `--family` vs `--families` is enforced inside `parse_family_scope` (`siglab/cli/helpers.py:544-555`) which `SystemExit(1)`s if both set. No top-level argparse `add_mutually_exclusive_group`.

### 1.13 `sodex-preflight` / `valuechain-preflight` / `sodex-ws-probe` / `sodex-preview` — `siglab/cli/sodex.py:47-96`
- `sodex-preflight`: `--json`. Dispatch `__init__.py:200-202` → `run_sodex_preflight` (line 99). No `--live` or `--no-preflight` override; this is a pure env-readiness probe.
- `valuechain-preflight`: `--rpc-url` (default=`"https://mainnet.valuechain.xyz"`), `--expected-chain-id` (int, default=286623), `--json`. Dispatch `__init__.py:203-205` → `run_valuechain_preflight` (line 117, async).
- `sodex-ws-probe`: `--environment` (choices=`["mainnet","testnet"]`, default=`"mainnet"`), `--market` (choices=`["spot","perps"]`, default=`"perps"`), `--channel` (default=`"allBookTicker"`), `--symbol`, `--user-address`, `--account-id` (int), `--timeout-seconds` (float, default=8.0), `--evidence-output`, `--json`. Dispatch `__init__.py:206-208` → `run_sodex_ws_probe` (line 154, async). Hardcodes `live_write=False`, `signed=False` (lines 166-168) — read-only.
- `sodex-preview`: `--kind` (required, choices=`["new-order","cancel-order","schedule-cancel","update-leverage","update-margin"]`), `--account-id` (int, required), `--symbol-id` (int, required), `--nonce` (int, required), `--cl-ord-id` (default=`"siglab-preview"`), `--modifier` (default=`"NORMAL"`), `--side` (default=`"BUY"`), `--order-type` (default=`"LIMIT"`), `--time-in-force` (default=`"GTC"`), `--price`, `--quantity`, `--funds`, `--order-id` (int), `--orig-cl-ord-id`, `--scheduled-timestamp` (int), `--amount`, `--reduce-only` (store_true), `--position-side` (default=`"BOTH"`), `--leverage` (int, default=1), `--margin-mode` (default=`"ISOLATED"`), `--json` (cosmetic — output always JSON). Dispatch `__init__.py:209-211` → `run_sodex_preview` (line 218). Returns `"submitted": False` and `"signature": None` (lines 290-291) — sign-only preview, no network call.

### 1.14 `telemetry-report` — `siglab/cli/telemetry.py:14-21`
- `telemetry-report`: `--track` (default=`"all"`), `--run-session-id`, `--json`. Dispatch `__init__.py:224-226` → `run_command` (line 24).

Total subcommand entries: 26 (`run`, `inspect`, `profile`, `evidence-build`, `evidence-map`, `demo-report`, `demo-manifest`, `demo-refresh`, `market-report`, `api-surface`, `ancestry`, `clear-passed`, `config`, `dashboard`, `dashboard-start`, `dashboard-stop`, `deploy`, `deployments`, `sodex-preflight`, `valuechain-preflight`, `sodex-ws-probe`, `sodex-preview`, `benchmark-init`, `benchmark-eval`, `benchmark-status`, `wave-status`, `telemetry-report`, `paper-start`, `paper-status`, `paper-promote`) = 30 distinct `dest` values registered through 14 `add_subparser` callers. The dispatch table at `__init__.py:146-235` has 30 explicit `if args.command == ...` branches.

---

## 2. ERROR HANDLING

The CLI does NOT wrap the dispatch in a global `try/except`. `argparse` calls `sys.exit(2)` on parse errors. The main dispatch (`__init__.py:94-235`) raises whatever the handler raises. Behavior is summarized per subcommand:

| Subcommand | Error → exit code | Cite |
|---|---|---|
| `ancestry` | Exceptions propagate. `LineageStore.list_rows` may raise sqlite errors → traceback + exit 1. | `ancestry_cmd.py:33-62` |
| `clear-passed` | Always exits 0 unless `LineageStore.clear_spec` raises. | `ancestry_cmd.py:65-85` |
| `api-surface` | `path.read_text` not wrapped — FileNotFoundError → traceback + 1. | `api.py:31` |
| `benchmark-init` | No try/except; `init_benchmark_deck` exceptions propagate. | `benchmark.py:52-71` |
| `benchmark-eval` | `try/finally` closes `provider`, but exceptions propagate → traceback. | `benchmark.py:84-95` |
| `benchmark-status` | No try/except. | `benchmark.py:98-104` |
| `config validate` | On any error: `_report_config_validation(errors)` → `raise SystemExit(1)`. On success: `raise SystemExit(0)`. JSON dict NOT printed — output is stderr only. | `config_cmd.py:36-81` |
| `dashboard` | `run_dashboard_server` exceptions propagate. | `dashboard.py:36-38` |
| `dashboard-start` | `uvicorn.run` may exit 0 on SIGINT or raise. | `dashboard.py:50-56` |
| `dashboard-stop` | No PID → `print_error` + `raise SystemExit(1)` (line 76). `TimeoutExpired` → `print_error` + `raise SystemExit(1)` (lines 80-82). `ProcessLookupError` swallowed silently (line 83-84, just `pass`). | `dashboard.py:75-84` |
| `demo-report` | No try/except. `json.dumps(..., default=str)` swallows un-serializable. | `demo.py:77-104` |
| `demo-manifest` | No try/except. | `demo.py:221-241` |
| `demo-refresh` | No try/except; any failure in `build_market_report` or write raises. | `demo.py:370-456` |
| `wave-status` | No try/except. | `demo.py:464-476` |
| `deploy` | `print(..., file=sys.stderr)` + `raise SystemExit(1)` for missing spec (line 54), ineligible spec (line 67). `manager.deploy` exceptions propagate. | `deploy.py:54, 67` |
| `deployments` | `print_error` + early return (no SystemExit) when single spec not found. | `deploy.py:113` |
| `evidence-build` | `try/finally` closes client; `asyncio.gather` errors propagate. | `evidence.py:68-86` |
| `evidence-map` | No summary found → `print(stderr)` + `raise SystemExit(1)`. | `evidence.py:152-153` |
| `inspect` | `try/finally` closes `web_researcher` and `provider`; exceptions propagate. | `run.py:858-879` |
| `market-report` | No try/except. | `market.py:37-79` |
| `paper-start` | `SoDEXPaperPerpsClient.create_session` exceptions propagate. | `paper.py:52-56` |
| `paper-status` | `PaperClientError` → `print_error` + `raise SystemExit(1)` (line 91-92). Inner kline processing exceptions swallowed (lines 75-78, 86-87). | `paper.py:75-92` |
| `paper-promote` | `PaperClientError` → prints result JSON with `promoted=False` + `raise SystemExit(1)` (lines 147-160). Non-eligible → `raise SystemExit(1)` after printing `promoted:True/False` JSON (lines 144-145). | `paper.py:144-160` |
| `profile` | `strict` mode with `failures>0` → `raise SystemExit(min(failures, 125))` (line 31). | `profile.py:28-31` |
| `run` | `--max-total-cost` → `print(stderr)` + `raise SystemExit(1)` (lines 159-164). Otherwise: all exceptions inside `_run_iterations` propagate → uncaught traceback. `--resume-safe-check` failure → `print(stderr)` + `raise SystemExit(1)` (`run.py:987-992`). | `run.py:159-164, 984-992` |
| `sodex-preflight` | No try/except; pure env read. | `sodex.py:99-114` |
| `valuechain-preflight` | `except (httpx.HTTPError, TypeError, ValueError)` → records into report dict, never raises. Exit 0 always. | `sodex.py:142-144` |
| `sodex-ws-probe` | `except (SoDEXWebSocketError, OSError, TypeError, ValueError)` → records into report dict, exits 0. Inner `recv_update` errors caught and recorded. | `sodex.py:181-208` |
| `sodex-preview` | `parse_sodex_enum` on bad value → `print(stderr)` + `raise SystemExit(1)` (`helpers.py:299-300`). `update-margin` missing `--amount` → `print(stderr)` + `raise SystemExit(1)`. | `sodex.py:256-258` |
| `telemetry-report` | No try/except. | `telemetry.py:24-62` |
| `clear-passed` | Always JSON, exit 0 unless internal sqlite error. | `ancestry_cmd.py:65-85` |

No subcommand emits a structured JSON error envelope. `config validate` is the only command that goes to stderr with `SystemExit` and never prints JSON. `paper-promote` is the only subcommand that emits a JSON `result` AND raises `SystemExit(1)` simultaneously (lines 142, 145).

---

## 3. CONFIG / CREDENTIAL LOADING

| Command | Source | Cite |
|---|---|---|
| `run` | `load_settings()` (reads `SIGLAB_CONFIG_PATH` and `pyproject.toml`/env). `require_sosovalue_config(settings)` reads `settings.sosovalue_config_path` (the `config.json` from `SOSOVALUE_CONFIG_PATH`). `--max-call-estimated-credits` mutates `settings.bai_max_call_credits`. | `run.py:151, 154, 152-153` |
| `inspect` | `load_settings` + `require_sosovalue_config`. | `run.py:843-844` |
| `benchmark-init` | `load_settings` only — NO sosovalue config gate. | `benchmark.py:53` |
| `benchmark-eval` | `load_settings` + `require_sosovalue_config` (gates the path). | `benchmark.py:75-76` |
| `benchmark-status` | `load_settings` only. | `benchmark.py:99` |
| `config validate` | `load_settings` + reads `settings.sosovalue_config_path` (config.json) and validates `system.api_key` and `system.api_base_url` are present. | `config_cmd.py:30, 57-60` |
| `evidence-build` | `load_settings` + `require_sosovalue_config`. Re-reads `settings.sosovalue_config_path.read_text` as JSON and extracts `api_key` (line 56-57). Passes `api_key` to `SoSoValueClient`. | `evidence.py:49, 56-67` |
| `evidence-map` | `load_settings` only. | `evidence.py:138` |
| `deploy` | `load_settings` + `require_sosovalue_config`. `--config` overrides `settings.sosovalue_config_path` (line 69). | `deploy.py:40-41, 69` |
| `dashboard*` | `load_settings` only for legacy `dashboard` (line 37). `dashboard-start` and `dashboard-stop` do not call `load_settings`. | `dashboard.py:37, 41, 59` |
| `paper-*` | `load_settings` only. | `paper.py:16, 67-69` |
| `profile` | `load_settings` only. | `profile.py:22` |
| `api-surface` | `load_settings` only. | `api.py:21` |
| `ancestry`, `clear-passed` | `load_settings` only. | `ancestry_cmd.py:34, 66` |
| `demo-*`, `wave-status` | `load_settings` only. | `demo.py:78, 222, 371, 465` |
| `market-report` | `load_settings` only. | `market.py:38` |
| `telemetry-report` | `load_settings` only. | `telemetry.py:25` |
| `sodex-preflight` | `sodex_preflight_report()` reads `os.environ` for: `SODEX_API_KEY_NAME`, `SODEX_ACCOUNT_ID`, `SODEX_NONCE_STORE_PATH`, `SODEX_ENVIRONMENT`, `SODEX_PRIVATE_KEY`, `SODEX_MAINNET_LIVE_WRITE_CONFIRMATION`, `SODEX_TESTNET_PREFLIGHT_PASSED`. | `helpers.py:165-237` |
| `valuechain-preflight` | Reads `--rpc-url` CLI arg only. | `sodex.py:118` |
| `sodex-ws-probe` | `load_settings` only (for evidence output path). | `sodex.py:190` |
| `sodex-preview` | `load_settings` NOT called. Constructs `SoDEXSignedRequest` from CLI args only — no signer, no nonce store read. | `sodex.py:222-291` |

**Secret path interpolation risk (per AGENTS.md §4 §3):**
- `sodex-preflight` echoes the **resolved absolute path** of `SODEX_NONCE_STORE_PATH` into the preflight report dict at `helpers.py:193-195` (`nonce_path = Path(nonce_store).expanduser(); ... = (Path.cwd() / nonce_path).resolve()`), and includes the full `Path` object via `nonce_path.parent` in `nonce_store_status`. The preflight report is printed by `run_sodex_preflight` at `sodex.py:104-114` (table) and `sodex.py:101-103` (JSON). This means `--help` does not leak, but running `siglab sodex-preflight --json` will print the resolved path of the nonce store to stdout. Not a secret value itself, but a fingerprint of the filesystem. The preflight dict is also emitted unredacted into `demo-manifest` (`demo.py:293`), `demo-report` (`demo.py:127`), `wave-status` chain, and `demo-refresh` (`demo.py:439`).
- `evidence-build` reads `api_key` from `config.json` and passes it into `SoSoValueClient` (`evidence.py:57-67`). The raw key is never printed by the CLI; the `--json` payload only contains paths, record counts, and module names. Safe.
- `config validate` validates presence of `system.api_key` (`config_cmd.py:57`) but never prints the value. Safe.
- `deploy` echoes the deployment record JSON via `print_json(detail)` at `deploy.py:58`. If the record contains `config_path` (which is the resolved `config.json` path), this is path leakage but not secret leakage. `display_deployment_record` (`helpers.py:320-324`) reformats the path to be root-relative, mitigating full-path disclosure.

---

## 4. LIVE BOUNDARY (per AGENTS.md)

### 4.1 SoDEX signed writes (per AGENTS.md: account_id, api_key_name, nonce_store, signer)

`sodex-preview` is the only subcommand that constructs `SoDEXSignedRequest` bodies (`sodex.py:222-291`). It does NOT call `build_signature` and does NOT send the request. The output is `{"signature": None, "submitted": False}` (`sodex.py:289-290`). Therefore it does NOT issue a signed write — it produces a pre-sign canonical payload only. The four prerequisites are not checked inside this command. The preflight command (`sodex-preflight`) is the only place that checks all four, and it is separate.

`deploy` invokes `LiveDeploymentManager.deploy` (`deploy.py:70-79`) which sits OUTSIDE this audit lane; the audit lane cannot verify that `deploy` gates via preflight. The CLI does call `require_sosovalue_config` (line 41) but does NOT call `sodex_preflight_report` to assert `live_write_allowed=True` before `manager.deploy`. Flag this: **`deploy` does not call `sodex_preflight_report` (no import, no gate) — cite `deploy.py:39-85`**.

### 4.2 SoSoValue calls without `x-soso-api-key`

There is no direct `httpx`/`requests` call to SoSoValue from any CLI subcommand. The SoSoValue client is constructed only in `evidence-build` (`evidence.py:58-67`) and the `api_key` IS passed to `SoSoValueClient`. The CLI does not assemble the header itself; the header construction is inside `siglab.data.sosovalue_client` (outside audit lane). No CLI subcommand issues a SoSoValue call with a missing or empty `x-soso-api-key`. The header field is named `x-soso-api-key` per the SoSoValue docs but is constructed downstream — no direct leakage in CLI.

### 4.3 Preflight bypass

- `deploy` (line 39) calls `require_sosovalue_config` but does NOT consult `sodex_preflight_report()`. Bypass: `deploy.py:39-85` — missing preflight gate for `LiveDeploymentManager.deploy`.
- `dashboard-start` and `dashboard-stop` (`dashboard.py:41-56, 59-85`) do not touch SoDEX; preflight is irrelevant.
- `paper-start`, `paper-status`, `paper-promote` (`paper.py:52-160`) are paper trading (offline) and do not issue SoDEX writes; preflight is not required.
- `evidence-build` (`evidence.py:47-134`) does not need SoDEX preflight (SoSoValue read-only).

### 4.4 B.AI Credits conflated with USD

- `run.py:100-111` explicitly documents: `--max-total-credits` help text says "Stop cooperatively when verified provider Credits telemetry reaches this budget. This is not USD." and `--max-call-estimated-credits` says "Refuse a single B.AI call when pre-call estimated Credits exceeds this budget." No conflation in help text.
- `run.py:158-164` explicitly REJECTS `--max-total-cost` with a stderr message stating it is not enforced and not implemented. This is the correct non-conflation.
- `demo.py:297` (`_build_demo_manifest` red_flags) explicitly says "B.AI Credits are not USD and must not be presented as USD spend."
- `market.py:275` (decision_support `unsafe_claims`) explicitly says "USD cost is not claimed for provider usage."
- `demo.py:283` sets `usd_cost_claimed=False` in the readiness dict — explicit guard.
- No CLI command or helper computes a `* USD_RATE` conversion or displays "B.AI Credits" with a `$` prefix. No conflation.

---

## 5. SUBPROCESS / EXTERNAL EXEC

| Site | Cite | Risk |
|---|---|---|
| `subprocess.run(f"lsof -ti :{port}", shell=True, capture_output=True, text=True, timeout=5)` | `dashboard.py:66-72` | **shell=True with f-string interpolation of `--port` (int from argparse)**. `--port` is `int(args.port)` (line 64), so the value is constrained to digits; however, the f-string still invokes a shell. Argparse `type=int` rejects non-integer input before reaching this call. **Risk: low but real** — a future `--port` default change to `str` would expose shell injection. Prefer `["lsof", "-ti", f":{port}"]` with `shell=False`. |
| `os.kill(pid, signal.SIGTERM)` | `dashboard.py:78` | `pid` is parsed from `lsof` stdout with `int(p) for p in ...` (line 73). Constrained to integers. Safe. |
| `uvicorn.run("siglab.dashboard.app:app", host=..., port=..., reload=..., log_level="info")` | `dashboard.py:50-56` | In-process ASGI server. `reload=True` spawns a subprocess via uvicorn's reloader — operator-controlled. The string `"siglab.dashboard.app:app"` is hardcoded. Safe. |
| `httpx.AsyncClient(...).post(rpc_url, json=..., headers=...)` | `sodex.py:127-132` | `rpc_url` from `--rpc-url` (default `"https://mainnet.valuechain.xyz"`). In-process HTTP, not subprocess. |
| `SoDEXWebSocketClient(...).subscribe(...)` | `sodex.py:170-178` | Network socket, in-process async. |
| `MarketDataProvider`, `ClaudeClient`, `LineageStore`, etc. | various | In-process library calls. Not subprocess. |

No `os.system`, no `subprocess.Popen`, no `subprocess.call` anywhere in the CLI.

---

## 6. SMELLS / RISKS (deterministic)

### 6.1 Swallowed exceptions
- `paper.py:75-76`: `except Exception: pass` swallows all kline-fetch errors per symbol.
- `paper.py:77-78`: outer `except Exception: pass` swallows the entire order-processing loop.
- `paper.py:86-87`: `except Exception: status["mark_prices"] = {}` swallows mark-price fetch errors.
- `dashboard.py:83-84`: `except ProcessLookupError: pass` silently absorbs a race where the PID is gone between `lsof` and `os.kill`. No log.
- `run.py:650-651`: `except Exception: deployment_manager = None` (inside `_reflect_on_iteration`); continues without deployment manager.
- `run.py:308-310`: catches `Exception`, logs via `print_error`, and `continue`s the iteration loop. This is a real error path, not silent — borderline acceptable.
- `run.py:535-537`: same pattern in burn-in phase.
- `sodex.py:181-184`: `recv_update` errors recorded into report dict — not silent, surfaced as `update_error_class`/`update_error` in JSON.
- `sodex.py:206-208`: connection errors recorded into report dict — not silent.

### 6.2 Mutable default arguments
None. `search` for `def \w+\([^)]*=\{\}` and `=\s*\[\]` returned no matches in function signatures. All list/dict initializations are inside function bodies (e.g. `errors: list[str] = []` at `config_cmd.py:32`, `trial_context: dict[str, Any] = {}` at `run.py:290`).

### 6.3 Async/sync mixing
- `paper.py:64-74`: `run_paper_status` calls `client.get_orders` (sync) and then `await feeds.fetch_klines` / `await client.process_klines` inside a sync-style try/except — this is INSIDE an `async def`, so it's correct.
- `evidence.py:71-83`: `asyncio.gather` with conditional `asyncio.sleep(0, result=[])` for the empty path — pattern is OK but the `asyncio.sleep(0, result=[])` yields control with no work. Idiomatic alternative: build the list of coroutines conditionally. Not a bug.
- `run.py:84-94` in benchmark-eval: `try/finally` around `await evaluate_benchmark_deck` is correct.
- No `asyncio.run` invoked from inside an event loop. All `asyncio.run(...)` calls happen at the dispatch boundary in `__init__.py:147, 150, 156, 195, 204, 207, 216, 228, 231, 234` — top-level only. Safe.
- `sodex.py:218-219` `run_sodex_preview` is sync — calls `_sodex_preview_payload` which is sync. `sodex.py:222-291` is sync. Correct.

### 6.4 Unbounded loops
- `run.py:292` `while True: ... if iterations > 0 and iteration_number > iterations: break` — bounded by `--iterations`. **But `--iterations 0` is documented as "Use 0 for infinite"** (`run.py:91-93`) — the loop is only stopped by `--max-runtime-seconds`, `--max-total-credits`, `--max-provider-errors`, `--max-consecutive-no-improvement`, `--max-consecutive-crashes`, or `--stop-on-live-surface-unavailable`. If none of those are set, **the loop runs forever**. The cooperative `max_runtime_timestamp` check at `run.py:296-298` is the only wall-clock bound. Cite: `run.py:291-298`.
- `helpers.py:457-479` `motif_audit_streak` loops over `ancestry.recent(limit=40)` — bounded.
- `helpers.py:500-501` `ancestry.recent(track, limit=500)` — bounded by `limit=500`.
- `run.py:744-746` `ancestry.recent(..., limit=12)` — bounded.
- `ancestry_cmd.py:74-79` `for track in tracks: for row in rows: ...` — `tracks` is a list of canonical track names (small), `rows` from `LineageStore.dashboard_rows` is bounded by dashboard's default. Bounded.
- No `while True` without a break check found outside `run.py:292`.

### 6.5 Functions > 200 lines
- `_run_iterations` (`run.py:222-495`) — ~273 lines. Internal, not directly user-callable but is the core loop. Exceeds 200 lines.
- `_write_run_reflection_internal` (`run.py:995-1255`) — ~260 lines. Internal helper.
- `run_demo_refresh` (`demo.py:370-456`) — ~87 lines. Under 200.
- `_demo_manifest_html` (`demo.py:302-362`) — ~61 lines. Under 200.
- `_demo_report_html` (`demo.py:142-213`) — ~72 lines. Under 200.
- `_build_demo_manifest` (`demo.py:244-300`) — ~57 lines. Under 200.
- `build_market_report` (`market.py:82-183`) — ~102 lines. Under 200.
- `sodex_preflight_report` (`helpers.py:157-287`) — ~131 lines. Under 200.
- Only `_run_iterations` and `_write_run_reflection_internal` cross the 200-line threshold.

### 6.6 Dead code
- `siglab/cli/__init__.py:85` re-exports `resolve_resume_run as _resolve_resume_run` from `siglab.run_config` and binds it to a name with a leading underscore. The name is never referenced again in `__init__.py`. `resolve_resume_run` is used directly in `run.py:252-256` via the original import. The `__init__.py` re-export is dead.
- `siglab/cli/__init__.py:89` re-exports `run_command` from `siglab.cli.profile` as `profile_command` and again as the plain name on line 90 from `siglab.cli.run`. The alias `profile_command` is never used inside `__init__.py`; it is bound for external importers only. Not strictly dead (test/external may import it), but no internal reference.
- `siglab/cli/__init__.py:91` re-exports `inspect_command`. Used at `__init__.py:150` via `_run_mod.inspect_command(args)`. The module attribute is reachable but the alias is redundant.
- `deploy.py:87-101` defines `_deployment_ineligible_reasons_fn` and `deployment_ineligible_reasons_fn` as thin wrappers calling `helpers.deployment_ineligible_reasons`. The wrapper is called from `deploy.py:64-66` (only one call site). The indirection adds no value — `helpers.deployment_ineligible_reasons` could be called directly. Mild dead abstraction.
- `siglab.cli.helpers` symbols re-exported in `__init__.py:21-48` (29 aliases with leading underscore) — none are referenced in `__init__.py` itself. They are present for "backward compatibility" but no caller in the audit lane uses them.

### 6.7 Un-validated JSON / dict access
- `evidence.py:56-57`: `raw = json.loads(settings.sosovalue_config_path.read_text(encoding="utf-8"))` then `api_key = ... str((raw.get("system") or {}).get("api_key") or "").strip()`. If `system` is a non-dict, the `or {}` fallback handles it. Safe.
- `config_cmd.py:39-44`: `json.loads(...)` wrapped in `try/except json.JSONDecodeError` — reports error and exits. Safe.
- `config_cmd.py:46-48`: `isinstance(raw, dict)` guard. Safe.
- `config_cmd.py:57-60`: `system.get("api_key")` and `system.get("api_base_url")` — `.get` on dict. Safe.
- `ancestry_cmd.py:53-60`: `row["created_at"]`, `row["track"]`, etc. — direct subscripting on sqlite row dict. If the DB schema is missing a column, `KeyError` propagates. No guard. **Cite: `ancestry_cmd.py:53-60`.**
- `ancestry_cmd.py:78`: `str(row.get("spec_hash") or "")` — uses `.get` with `or ""` fallback. Safe.
- `helpers.py:199` `os.access(nonce_path, os.W_OK)` — wraps a missing file. `os.access` returns False silently. Safe.
- `helpers.py:204` `parsed = json.loads(nonce_path.read_text())` — wrapped in `try/except (OSError, json.JSONDecodeError, TypeError, ValueError)`. Safe.
- `sodex.py:128-138`: `response.json()` then `payload.get("result")` — guarded with `isinstance(payload, dict)`. Safe.
- `paper.py:65-67`: `o["symbol"] for o in open_orders` — subscript without `.get`. If `open_orders` returns non-dict items, `KeyError`. Defensive but realistic.
- `run.py:50-58` (in deploy `display_deployment_record` body): uses `.get` throughout. Safe.
- `demo.py:124-126`: `load_json_if_exists(sosovalue_summaries[-1])` — guarded by `if sosovalue_summaries else None`. Safe.

### 6.8 shell=True on user input
- `dashboard.py:67-68`: `subprocess.run(f"lsof -ti :{port}", shell=True, ...)`. `--port` is constrained by `type=int` to integer values (`dashboard.py:33`). **No injection vector under current argparse type**, but the f-string is a code smell — list-form `["lsof", "-ti", f":{port}"]` would be safer. **Cite: `dashboard.py:66-68`.**

---

## 7. JSON OUTPUT CONTRACT

For each `--json` flag, the dict is constructed at the cited line. Schemas below.

### 7.1 `ancestry --json` — `ancestry_cmd.py:40-42`
Schema: array of row dicts from `LineageStore.list_rows(...)`. Each row contains keys: `created_at`, `track`, `family`, `spec_hash`, `aggregate_score`, `passed`, `deployd`. No wrapper object.

### 7.2 `clear-passed` — `ancestry_cmd.py:80-85`
Schema: `{"track": str, "tracks_cleared": int, "passed_specs_removed": int}`. Always JSON (no `--json` flag).

### 7.3 `api-surface --json` — `api.py:29-40, 41-43`
Schema:
```json
{
  "sosovalue":  {"path": str, "exists": bool, "line_count": int, "endpoint_path_mentions": int, "supported_mentions": int, "missing_mentions": int, "blocked_mentions": int},
  "sodex":      {"...": "..."},
  "ecosystem":  {"...": "..."},
  "buildathon": {"...": "..."}
}
```

### 7.4 `benchmark-init` — `benchmark.py:58-71`
Schema: whatever `init_benchmark_deck(...)` returns. No `--json` flag; always `print_json(payload)`.

### 7.5 `benchmark-eval` — `benchmark.py:84-95`
Schema: whatever `evaluate_benchmark_deck(...)` returns. No `--json` flag; always JSON.

### 7.6 `benchmark-status` — `benchmark.py:98-104`
Schema: whatever `benchmark_status_payload(...)` returns. No `--json` flag; always JSON.

### 7.7 `config validate` — `config_cmd.py:28-74`
Schema: **NO JSON OUTPUT.** On error: stderr via `print_error` + `SystemExit(1)`. On success: `print_success` + `print_key_value_pairs` with `api_base_url`, then `raise SystemExit(0)`. No `--json` flag exists.

### 7.8 `dashboard*` — `dashboard.py`
No `--json` flag, no JSON output. Plain text messages via `print_info`/`print_success`/`print_error`.

### 7.9 `demo-report --json` — `demo.py:95-104`
Schema:
```json
{"output": str, "html_output": str|null, "readiness": dict, "red_flags": list[str]}
```
Where `readiness` keys: `sosovalue_api`, `sodex_public_api`, `sodex_signed_execution`, `demo_materials` (`demo.py:128-133`).

### 7.10 `demo-manifest --json` — `demo.py:237-239`
Schema: the full manifest dict, with keys `generated_at`, `purpose`, `artifacts` (dict of 14 path strings + `provider_metrics` list), `artifact_status` (dict of booleans), `readiness` (7 booleans/strings), `market_report_status`, `market_report_headline`, `sodex_preflight` (full preflight dict), `red_flags` (list[str]).

### 7.11 `demo-refresh --json` — `demo.py:436-456`
Schema:
```json
{
  "generated_at": str,
  "artifacts": {"sodex_preflight": str, "telemetry": str, "market_report": str, "market_report_html": str, "demo_report": str, "demo_manifest": str, "demo_manifest_html": str, "wave_status": str},
  "readiness": dict,
  "market_report_status": str,
  "live_write_allowed": bool,
  "unsafe_claims": list[str]
}
```

### 7.12 `wave-status --json` — `demo.py:479-497`
Schema:
```json
{
  "generated_at": str, "wave_number": int, "phase": str, "status": str, "goal": str,
  "agents": list[str], "outputs": list[str], "blockers": list[str],
  "validation_status": str, "next_decision": str,
  "stop_allowed": false, "unsafe_claims": list[str]
}
```

### 7.13 `deploy` (no `--json` flag) — `deploy.py:58, 83`
`deploy` does not have a `--json` flag. When a fresh spec is being deployed, prints `print_json(detail)` where `detail` is the output of `display_deployment_record(...)` (keys: `strategy_dir`, `spec_path`, `manifest_path`, `readme_path`, `config_path`, and lineage DB columns). When an existing deployment is found, prints `print_json(existing)` (the raw lineage row) followed by a warning string.

### 7.14 `deployments` (no `--json` flag) — `deploy.py:104-117`
Always JSON. If `--spec` provided and found: `display_deployment_record` dict. If `--spec` not found: `print_error(...)` only (no JSON, exits 0 anyway). If no `--spec`: list of `display_deployment_record` dicts.

### 7.15 `evidence-build --json` — `evidence.py:117-134`
Schema:
```json
{
  "output": str, "summary_output": str, "records_appended": int, "cross_module_links": int,
  "currency": str, "currency_id": int|null, "link_relations": list[str], "modules": list[str],
  "relations": list[str], "source_counts": dict[str, int], "summary_record_count": int,
  "summary_top_links": int, "append_stats": dict, "observed_at": str
}
```

### 7.16 `evidence-map --json` — `evidence.py:161-164`
Schema: `{"summary": str, "output": str}`.

### 7.17 `inspect` (no `--json` flag) — `run.py:842-879`
Iterates tracks, calls `print_json(summary)` once per track. `summary` is the result of `provider.build_research_summary(track, parent)` augmented with `external_research` and `memory_packet`. Schema is the ResearchSummary dict (not specified in CLI).

### 7.18 `market-report --json` — `market.py:69-79`
Schema:
```json
{"output": str, "html_output": str, "entity": str, "status": str, "warnings": list[str]}
```

### 7.19 `paper-start` (no `--json` flag) — `paper.py:52-56`
Always JSON: `{"session_id": str, "name": str}`.

### 7.20 `paper-status` (no `--json` flag) — `paper.py:59-92`
Always JSON: result of `client.get_session_status(args.session)`, augmented with `"mark_prices": dict`. On `PaperClientError`: `print_error` + `SystemExit(1)`, no JSON.

### 7.21 `paper-promote` (no `--json` flag) — `paper.py:95-160`
Always JSON:
```json
{
  "promoted": bool, "reason": str, "composite_score": float, "sub_scores": dict[str, float],
  "trade_count": int, "trading_days": int, "threshold": float, "consecutive_days_required": int,
  "min_trading_days_required": int
}
```
On `PaperClientError`: result is `{"promoted": false, "reason": <str(exc)>, "composite_score": 0.0, "sub_scores": {}, "trade_count": 0, "trading_days": 0, "threshold": ..., "consecutive_days_required": ..., "min_trading_days_required": ...}` then `SystemExit(1)`. On non-eligible: result with `promoted:false` then `SystemExit(1)`.

### 7.22 `profile --json` — `profile.py:21-31`
Schema: the result of `build_profile(settings.root_dir)` (Hardening Profile dict).

### 7.23 `run` (no `--json` flag) — `run.py:150-220`
No `--json` flag. Output is side-effect-only (writes artifacts, logs via `print_*`). No JSON printed to stdout.

### 7.24 `sodex-preflight --json` — `sodex.py:99-103`
Schema: the full `sodex_preflight_report()` dict, with keys:
```json
{
  "public_read_ready": true, "schema_pinned": true,
  "signed_path": {"ready": bool, "environment": str, "signer_ready": bool, "signer_type": str|null,
                  "accountID_present": bool, "api_key_name_present": bool, "nonce_store_ready": bool,
                  "nonce_store": {...}, "testnet_preflight_passed": bool,
                  "mainnet_confirmation_present": bool, "missing_prerequisites": list[str]},
  "live_write_allowed": bool, "live_write_refusal_reason": str|null,
  "access_plan": {...}, "next_actions": list[str],
  "request_weight_budget_per_minute": int, "documented_endpoint_weights": dict,
  "rate_limit_scope": {...}, "supported_signed_actions": list[str],
  "unsupported_signed_actions": dict
}
```

### 7.25 `valuechain-preflight --json` — `sodex.py:117-147`
Schema:
```json
{
  "rpc_url": str, "expected_chain_id": int, "source": str, "ready": bool,
  "http_status": int|null, "response_shape": list[str]|str|null,
  "chain_id_hex": str|null, "chain_id": int|null,
  "missing_or_wrong": str (only if not ready),
  "error_class": str (only on exception), "error": str (only on exception)
}
```

### 7.26 `sodex-ws-probe --json` — `sodex.py:154-215`
Schema:
```json
{
  "environment": str, "market": str, "params": dict, "live_write": false, "signed": false,
  "ready": bool,
  "subscribe_ack": dict (on success),
  "first_update_keys": list[str], "first_update_channel": str, "first_update_type": str (on update received),
  "update_error_class": str, "update_error": str (on update error),
  "evidence_output": str, "evidence_summary_output": str, "evidence_records_appended": int,
  "evidence_summary_record_count": int (if --evidence-output),
  "error_class": str, "error": str (on connection error),
  "snapshot": dict (always)
}
```

### 7.27 `sodex-preview` — `sodex.py:218-219, 281-291`
Always JSON (no `--json` flag effect — `--json` is a no-op cosmetic). Schema:
```json
{
  "method": str, "path": str, "domain": str, "weight": int,
  "canonical_body": str, "canonical_signing_payload": str,
  "signature_input": str, "signature": null, "submitted": false
}
```

### 7.28 `telemetry-report --json` — `telemetry.py:35-45`
Schema:
```json
{
  "trace_count": int, "stage_counts": dict, "provider_counts": dict, "model_counts": dict,
  "tool_invocation_count": int, "tool_counts": dict, "tool_latency_ms": dict,
  "confidence": str,
  "trace_paths_scanned": int,
  "provider_metrics": {...}, "provider_metrics_paths_scanned": int,
  "provider_metrics_status": "missing"|"present"|"not_applicable"
}
```

---

**End of audit.**
