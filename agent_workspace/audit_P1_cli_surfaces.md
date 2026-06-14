# P1 Audit: SigLab CLI Surfaces — Profile / Market-Report / Sodex-Preflight / Demo-Run

**Date:** 2026-06-14
**Auditor:** WaveP1BCLIEndToEnd
**Working dir:** `/home/eya/soso/siglab`
**Goal:** Drive the 4 named CLI surfaces end-to-end with real OpenRouter key, real SoSoValue key, real SoDEX testnet env, and report what actually happens vs. what is claimed.

## TL;DR (read this first)

- **All 4 CLI surfaces exit 0 and print JSON.** They look healthy at a glance.
- **None of them make a real network call.** Verified by `strace -e network,connect` — 0 `connect()` syscalls, 0 sockets opened in any of the 4 invocations.
- The surfaces are **offline report aggregators** that read pre-existing local artifacts (`runs/evidence/*.jsonl`, `runs/provider_metrics/*.jsonl`, `runs/llm_traces/`, env vars, and AST scans of the repo).
- The OpenRouter key and SoSoValue key in the environment are **never consulted** by any of the 4 commands.
- Two of the four surfaces hardcode status strings that imply a real OpenRouter interaction occurred when it never did (`llm_cost_status: "verified_openrouter_usd_priced_pending_wave_1a"` and the "LLM cost is reported in USD against the live OpenRouter response usage.cost field" red_flag in `demo.py:298`).
- SoDEX preflight is honest — it correctly reports that nothing is configured. It does not pretend live data was fetched.

**Verdict:** the 4 CLI surfaces are a **status snapshot, not a live demo**. The "End-to-end" framing in the operator docs is misleading. The CLI can show that *prior* runs did things, but the operator cannot trust that *this invocation* exercised the live stack.

---

## 1. CLI surface results table

| Surface | Exit | Wall time | Real network call? | Real data? | Verdict |
|---|---|---|---|---|---|
| `profile --strict --json` | 0 | ~4.0 s | No (`strace` 0 connects) | Walks AST, 5 stub_marker findings on TUI placeholder screen. Reads nothing live. | **Honest static analyzer.** Does not pretend to call APIs. |
| `market-report --json` | 0 | ~2.2 s | No (`strace` 0 connects) | Reads 620 rows of `runs/evidence/sosovalue_evidence.jsonl` (from 2026-06-04) and 152 rows of `runs/evidence/sodex_ws_evidence.jsonl` (from 2026-05-14). Status: `READY_FOR_OPERATOR_REVIEW`. | **Canned-data aggregator.** Pretends nothing live. Status string is honest. |
| `sodex-preflight --json` | 0 | ~2.1 s | No (`strace` 0 connects) | Inspects `os.environ` for `SODEX_*` vars. All signed-path prereqs missing. `live_write_allowed: false`. | **Honest env-var gate.** Reports exactly what env says. |
| `demo-run --json` | 0 | ~4.3 s | No (`strace` 0 connects) | Delegates to the 3 above + reads `runs/latest_telemetry_report.json` + `runs/provider_metrics/*.jsonl` (all stale, from 2026-05-14). | **Aggregator of stale local artifacts.** `llm_cost_status` field is a hardcoded string, not a measured value. |

### Truncated JSON shapes (real output)

**profile** (165 KB total, 90 KB visible — truncated for reading):

```json
{
  "findings": [
    {"kind": "stub_marker", "path": "siglab/tui/app.py", "line": 50, "severity": "medium", ...},
    ...
  ],
  "modules": [...134 modules...],
  "public_objects": [...579 entries...],
  "summary": {"by_kind": {"stub_marker": 5}, "by_severity": {"medium": 5},
              "finding_count": 5, "module_count": 134, "public_object_count": 579}
}
```

**market-report** (374 bytes stdout, real report body in `runs/market_report.json` 18 KB):

```json
{
  "entity": "BTC",
  "html_output": "/home/eya/soso/siglab/runs/market_report.html",
  "output": "/home/eya/soso/siglab/runs/market_report.json",
  "status": "READY_FOR_OPERATOR_REVIEW",
  "warnings": [
    "Evidence links are temporal/contextual and are not causal claims.",
    "Signed SoDEX execution is refused unless preflight reports live_write_allowed=true."
  ]
}
```

**sodex-preflight** (3136 bytes stdout):

```json
{
  "public_read_ready": true,
  "schema_pinned": true,
  "signed_path": {
    "ready": false, "environment": "testnet",
    "missing_prerequisites": ["SODEX_API_KEY_NAME", "SODEX_ACCOUNT_ID",
                              "SODEX_NONCE_STORE_PATH", "SODEX_PRIVATE_KEY"],
    "testnet_preflight_passed": false, "mainnet_confirmation_present": false
  },
  "live_write_allowed": false,
  "live_write_refusal_reason": "missing signed-path prerequisites",
  ...
}
```

**demo-run** (1655 bytes stdout):

```json
{
  "sodex_preflight": {"environment": "testnet", "live_write_allowed": false,
                      "public_read_ready": true, "schema_pinned": true,
                      "signed_path_ready": false},
  "market_report": {"as_of": null, "entity": "BTC",
                    "status": "READY_FOR_OPERATOR_REVIEW", "warnings": [...]},
  "demo_manifest": {
    "artifact_count": 13,
    "readiness": {
      "causality_claimed": false,
      "llm_cost_status": "verified_openrouter_usd_priced_pending_wave_1a",
      "provider_metrics_present": true,
      "sodex_live_write_allowed": false,
      "sodex_public_market_data": true,
      "sosovalue_input_to_output": true,
      "telemetry_provider_metrics_status": "present",
      "usd_cost_claimed": false
    }
  },
  "telemetry_report": {"confidence": "good", "provider_metrics_status": "present",
                       "tool_invocation_count": 257, "trace_count": 73},
  "summary": "preflight: public_read=True signed=False live_write=False | manifest: readiness={...} artifacts=13 | market: entity=BTC status=READY_FOR_OPERATOR_REVIEW warnings=2 | telemetry: traces=73 tools=257 providers=present"
}
```

---

## 2. OpenRouter call traces

**No OpenRouter call was made by any surface.**

Evidence:

- `OPENROUTER_API_KEY=sk-or-v1-f97dbf67...` set in env at run time.
- `strace -f -e trace=network,connect` for each of the 4 commands: **0 `connect()` calls, 0 `socket()` calls.** Confirmed for `profile`, `market-report`, `sodex-preflight`, `demo-run`.
- The only "openrouter" string in profile stdout is the dataclass field names of `SiglabConfig` (`openrouter_api_key`, `openrouter_base_url`, `openrouter_model`, `openrouter_planner_model`, ...) — these are static config field names, not runtime traces.
- `latency`: 0 ms (no call). `cost`: $0.00 (no call).
- `cost_usd` / `llm_cost_status`: the `llm_cost_status` field in `demo_manifest` is **hardcoded** to `"verified_openrouter_usd_priced_pending_wave_1a"` (`siglab/cli/demo.py:284`). The "verified" prefix is a lie. No model call, no `usage.cost` field, no verification.

The CLI never invokes the LLM client at all. The closest any surface gets to "real" is the **stale `runs/llm_traces/` and `runs/provider_metrics/` files from 2026-05-14** — over a month old, not from this run.

The actual OpenRouter code path lives in `siglab/llm/llm.py` (`OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"`, line 29) and `siglab/llm/claude.py`. Neither is reachable from the 4 CLI commands in this audit.

---

## 3. SoSoValue data

**Cached. Not refreshed.**

- `market-report` reads `runs/evidence/sosovalue_evidence.jsonl` via `latest_path(evidence_dir, "*sosovalue*.jsonl")` in `siglab/cli/market.py:43`.
- That file has **620 rows, last modified 2026-06-04T05:30:00** (10 days before this audit).
- The latest flow value (`-396596501.8 USD`, timestamp 2026-06-03) is real data, but it is data that was fetched by a previous run, not by `market-report` itself.
- The `sosovalue_evidence.jsonl` file has no live `request` field, no latency, no cost. It is a flat JSONL evidence store.
- SoSoValue API key is in env (`SOSOVALUE_API_KEY=SOSO-e5b2...`), but **never read** by any of the 4 CLI surfaces. The key is dead weight for these commands.

Stub/real verdict: **real data, stale**. The "real" verdict is a generous read — it's real evidence captured by a prior run, but this invocation of `market-report` cannot be trusted to be live.

---

## 4. SoDEX preflight

**Honest, deterministic, env-var driven.** This is the one surface that does what it claims.

- Reads `os.environ` via `sodex_preflight_report()` in `siglab/cli/helpers.py:157`.
- No `httpx`, no socket, no handshake.
- Correctly reports that `SODEX_API_KEY_NAME`, `SODEX_ACCOUNT_ID`, `SODEX_NONCE_STORE_PATH`, `SODEX_PRIVATE_KEY` are all unset.
- `live_write_allowed: false`, `signed_path.ready: false`.
- `SODEX_ENVIRONMENT` defaults to `"testnet"` if not set (`siglab/cli/helpers.py:176`).
- `SODEX_WS_TESTNET=1` (the env var the assignment mentions) is **not consulted** by the preflight. It is used by `sodex_ws.py` and `sodex-ws-probe` (not in scope here).

**Verdict:** `sodex-preflight` is a deterministic, honest status check. It does not pretend to call SoDEX.

---

## 5. Artifacts in `runs/` actually produced

`find runs/ -newer <profile_stdout>` (i.e. touched by the 4 surface invocations in this audit):

| File | Size | mtime | Surface that wrote it | Source data |
|---|---|---|---|---|
| `runs/market_report.json` | 18 KB | 2026-06-14 16:50:18 | `market-report` | 620 SOSO + 152 SODEX rows from disk |
| `runs/market_report.html` | 2 KB | 2026-06-14 16:50:18 | `market-report` | same |
| `runs/demo_manifest_latest.json` | 5 KB | 2026-06-14 16:47:47 | `demo-run` (via `demo.py:_build_demo_manifest`) | artifact_status over pre-existing files |
| `runs/demo_manifest_latest.html` | 5 KB | 2026-06-14 16:47:47 | `demo-run` | same |

`sodex-preflight` and `profile` **wrote nothing to `runs/`** during the audit.

Files that the demo manifest *claims* are present but were not touched in this run:

- `runs/llm_traces/*` — last modified 2026-06-14 12:33:38 (over 4 hours stale; the jsonl files inside are from 2026-05-14)
- `runs/provider_metrics/*.jsonl` — all from 2026-05-14 (over a month stale)
- `runs/sodex_probes/` — from 2026-05-14
- `runs/evidence/sosovalue_evidence.jsonl` — from 2026-06-04
- `runs/evidence/sodex_ws_evidence.jsonl` — from 2026-05-14

The CLI does not stamp a "data_freshness" or "data_age" field on the demo manifest, so a reader cannot tell from the output that the "73 traces" and "257 tool invocations" come from a month-old run, not from this invocation.

---

## 6. Top 10 broken CLI behaviors

| # | File:line | Broken behavior | The lie |
|---|---|---|---|
| 1 | `siglab/cli/demo.py:284` | `llm_cost_status: "verified_openrouter_usd_priced_pending_wave_1a"` is hardcoded. | The string starts with "verified" — but no OpenRouter call is made by this command. The "pending_wave_1a" tail is a tell that it is a placeholder, but downstream automation may treat the "verified" prefix as truth. |
| 2 | `siglab/cli/demo.py:298` | Red-flag warning claims "LLM cost is reported in USD against the live OpenRouter response usage.cost field." | The CLI never reads `usage.cost`. There is no live OpenRouter response. The warning describes a path that the CLI does not follow. |
| 3 | `siglab/cli/demo.py:283` + `siglab/cli/market.py:294` | `usd_cost_claimed: false` (demo) and `"USD cost is not claimed for provider usage"` (market risk_controls) both deny cost. But `llm_cost_status: "verified_openrouter_usd_priced_pending_wave_1a"` (demo) is set in the same payload. | The same payload says "we don't claim cost" and "USD cost verified" in the same dict. Self-contradictory. |
| 4 | `siglab/cli/telemetry.py:39-42` (and demo manifest consumer) | `provider_metrics_status = "present"` whenever `runs/provider_metrics/*.jsonl` exists, regardless of age. | The CLI happily reports `tool_invocation_count: 257, trace_count: 73, providers=present` from May-14 jsonl files. There is no `latest_data_age_s` or `last_run_iso` field. A reader cannot tell that this is over a month stale. |
| 5 | `siglab/cli/profile.py:21-31` | `profile --strict` returns exit 0 (despite `--strict` flag) because `strict_failure_count(profile)` only counts `high` severity findings, and the current 5 stub_marker findings are all `medium`. | `--strict` is silently degraded to "exit 0 unless high severity". The docstring/help string does not tell the operator that `--strict` ignores `medium`. |
| 6 | `siglab/cli/market.py:42-44` | `latest_path(evidence_dir, "*sosovalue*.jsonl")` returns the most recently *modified* file, not the most recently *fetched* file. | If the operator re-runs `market-report` after touching a `*.jsonl` file with `touch`, the CLI will report that file as "latest" — and a 10-day-old file counts as fresh. There is no `evidence_observed_at` filter. |
| 7 | `siglab/cli/sodex.py:50-51` | `--exit-on-first-frame` is parsed but its handling is identical to `--json`: just prints the report. The option is dead code. | The flag name implies a one-frame WS handshake probe, but the implementation just prints the deterministic env-var report. There is no first frame. |
| 8 | `siglab/cli/demo_run.py:36-96` | `run_demo_run` calls `sodex_preflight_report()`, `build_market_report()`, and `aggregate_trace_telemetry()` but does not stamp the result with the actual age of the underlying evidence. | A "live" demo would include `data_age_s`, `last_evidence_observed_at`, or similar. The current demo `summary` line is a string of True/False booleans that look healthy but mean nothing about freshness. |
| 9 | `siglab/cli/sodex.py:102-120` | `run_sodex_preflight` does not check `SODEX_WS_TESTNET` at all. It defaults `SODEX_ENVIRONMENT` to `"testnet"` but does not attempt a WS handshake. | The CLI claims to report "SoDEX testnet readiness" but it does not open a socket. The `public_read_ready: true` field is a hardcoded constant in `siglab/cli/helpers.py:246` — not a measured result. |
| 10 | `siglab/cli/__init__.py:174-176` | `demo-run` is invoked via `settings = load_settings()` then `_demo_run_mod.run_demo_run(settings, json=...)`. `load_settings()` may pull `OPENROUTER_API_KEY` from env (`siglab/config.py:155`), but the key is then unused by `run_demo_run`. | The CLI accepts and loads the OpenRouter key but never uses it. No error, no warning, no `provider_used: "none"` field. Operators cannot tell that the key was ignored. |

---

## 7. Smaller-delta fix plan (5-line patches)

These are minimal, surgical patches. None of them change architecture. Each one is a `replace` of < 5 lines.

### Patch 1 — honest `llm_cost_status` in demo manifest
File: `siglab/cli/demo.py:281-285`

```python
readiness = {
    "sosovalue_input_to_output": bool(artifact_status.get("market_report_json")),
    "sodex_public_market_data": bool(artifact_status.get("sodex_ws_evidence")),
    "sodex_live_write_allowed": bool(preflight.get("live_write_allowed")),
    "provider_metrics_present": bool(provider_metric_paths),
    "telemetry_provider_metrics_status": telemetry.get("provider_metrics_status"),
    "causality_claimed": False,
    "usd_cost_claimed": False,
    "llm_cost_status": "verified_openrouter_usd_priced_pending_wave_1a",  # <-- LIE
}
```

Patch:
```python
    "usd_cost_claimed": False,
    "llm_cost_status": "not_measured_this_invocation",
    "llm_cost_evidence_age_s": _evidence_age_seconds(provider_metric_paths),
```
Add a tiny helper at module top: `def _evidence_age_seconds(paths): return (datetime.now(UTC).timestamp() - max(os.path.getmtime(p) for p in paths)) if paths else None`.

### Patch 2 — stop claiming "live OpenRouter response" in red flags
File: `siglab/cli/demo.py:295-299`

Replace the third red_flag:
```python
"LLM cost is reported in USD against the live OpenRouter response usage.cost field. Cost is verified per call when the model exists in https://openrouter.ai/api/v1/models; absent cost is reported as cost_status='unpriced' and the run is flagged for human review.",
```
with:
```python
"LLM cost is not measured by this command. demo-run is an aggregator of local artifacts; cost measurement requires a planner/writer/reflector run (see `siglab run` / `siglab inspect`).",
```

### Patch 3 — remove the `verified_` prefix from the literal
File: `siglab/cli/demo.py:284`

```python
"llm_cost_status": "verified_openrouter_usd_priced_pending_wave_1a",
```
→
```python
"llm_cost_status": "not_measured_by_demo_run",
```

### Patch 4 — make `public_read_ready` actually measured
File: `siglab/cli/helpers.py:157` and `:246`

Replace the hardcoded:
```python
"public_read_ready": True,
```
with a function call:
```python
"public_read_ready": _probe_soDEX_public_read(sodex_base_url=source.get("SODEX_BASE_URL", "https://api.sodex.app")),
```
where the helper does a `httpx.head(...)` with a 3 s timeout. On failure, return `False` and set `public_read_error`. This converts a hardcoded constant into a real readiness check.

### Patch 5 — make `--strict` actually exit non-zero on `medium`
File: `siglab/cli/profile.py:28-31`

```python
if getattr(args, "strict", False):
    failures = strict_failure_count(profile)
    if failures:
        raise SystemExit(min(failures, 125))
```
Add `medium` to the failure count:
```python
if getattr(args, "strict", False):
    failures = strict_failure_count(profile) + sum(1 for f in profile.get("findings", []) if f.get("severity") == "medium")
    if failures:
        raise SystemExit(min(failures, 125))
```

### Patch 6 (bonus) — stamp demo manifest with data age
File: `siglab/cli/demo.py:286-301`

Add two new keys to the returned dict:
```python
"data_freshness": {
    "sosovalue_evidence_age_s": _file_age_seconds(artifacts["sosovalue_evidence"]) if Path(artifacts["sosovalue_evidence"]).exists() else None,
    "sodex_ws_evidence_age_s": _file_age_seconds(artifacts["sodex_ws_evidence"]) if Path(artifacts["sodex_ws_evidence"]).exists() else None,
    "provider_metrics_age_s": max((_file_age_seconds(p) for p in provider_metric_paths), default=None) if provider_metric_paths else None,
},
```
Helper:
```python
def _file_age_seconds(p: str) -> float:
    return max(0.0, datetime.now(UTC).timestamp() - os.path.getmtime(p))
```

### Patch 7 (bonus) — drop the dead `--exit-on-first-frame` flag
File: `siglab/cli/sodex.py:50-51`

Either implement it (a real `httpx.ws_connect` with a 1-frame timeout) or delete the line:
```python
preflight_parser.add_argument("--exit-on-first-frame", action="store_true")
```

---

## Appendix A — full evidence chain

All raw artifacts captured in this audit are in `agent_workspace/reports/`:

```
agent_workspace/reports/
  profile.stdout          165,050 bytes — full profile JSON
  profile.stderr          0 bytes
  market_report.stdout    374 bytes — market-report CLI summary
  market_report.stderr    0 bytes
  sodex_preflight.stdout  3,136 bytes — full preflight report
  sodex_preflight.stderr  0 bytes
  demo_run.stdout         1,655 bytes — full demo summary
  demo_run.stderr         0 bytes
```

Artifacts written into the project tree:
```
runs/market_report.json         18 KB   (regenerated by market-report)
runs/market_report.html         2 KB    (regenerated by market-report)
runs/demo_manifest_latest.json  5 KB    (regenerated by demo-run)
runs/demo_manifest_latest.html  5 KB    (regenerated by demo-run)
```

## Appendix B — environment

```
OPENROUTER_API_KEY=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
SOSOVALUE_API_KEY=SOSO-e5b2e51815fd4acfbdbd412d8fec9524
LLM_PROVIDER=claude         (from .env, set by operator)
ANTHROPIC_BASE_URL=https://api.b.ai  (from .siglab-provider.env, set by operator)
SODEX_WS_TESTNET not set    (sodex-preflight defaults SODEX_ENVIRONMENT to testnet regardless)
```

## Appendix C — verification method

Each surface was run twice: once with `strace -f -e trace=network,connect` attached, once for clean stdout/stderr capture. All 4 runs:

- Exit code 0.
- No `connect()` syscalls.
- No `socket()` calls that reach out of process.
- No `httpx`, `requests`, `urllib.request`, `httpx.post` references in the captured stderr.

The 4 commands are, functionally, **deterministic JSON-emitting static analyzers over local files**. They are not demos in the sense of "drives the live stack end-to-end."
