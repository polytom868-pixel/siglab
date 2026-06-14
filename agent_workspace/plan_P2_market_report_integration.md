# Plan P2 — Fold real SoSoValue into `market_report` as real evidence (no schema break)

> **Scope:** READ + WRITE plan only. No source edits. No commit.
> **Goal:** `runs/market_report_latest.json` reads from real SoSoValue traffic (the same traffic already feeding `runs/evidence/sosovalue_evidence.jsonl`) instead of an offline JSONL. The schema stays identical; only the values become live.
> **Smaller-delta principle:** No new file. No new CLI flag. The data source switch is a single constructor argument on `build_market_report` — driven by `SOSOVALUE_API_KEY` presence.

---

## 0. Repo truth surface (what is actually there)

I verified the following before writing this plan. All file references are anchored.

- **`siglab/cli/market.py`** (not `market_report.py`): contains `build_market_report(*, entity, sosovalue_evidence, sodex_evidence)` (lines 82-182). It is **purely an offline file reader** — it calls `read_jsonl_with_stats(sosovalue_evidence)` at line 88 and `read_jsonl_with_stats(sodex_evidence)` at line 89, then filters by `entity`/`relation`/`module`. There is **no current call to `SoSoValueClient`** in this function. The shape emitted is fixed (see §1).
- **`siglab/data/sosovalue_client.py`** (`SoSoValueClient`): exactly three implemented methods, all `async`:
  - `etf_historical_inflow(*, etf_type="us-btc-spot")` — calls `GET /etfs/summary-history` (line 133). Returns rows with `date`, `total_net_inflow`, `total_value_traded`, `total_net_assets`, `cum_net_inflow`.
  - `listed_currencies()` — calls `GET /currencies` (line 148). Currency list, 24h TTL.
  - `featured_news_pages(*, max_pages, page_size, category_list)` — calls `GET /api/v1/news/featured` (line 160). 60s TTL, paginated.
  - All three raise `SoSoValueConfigError("SOSOVALUE_API_KEY is required for SoSoValue API calls")` when `api_key` is empty (line 192).
- **`siglab/data/sosovalue_capabilities.py`**: truth table of 30 endpoints. **Only three are `IMPLEMENTED` with `wrapper=SoSoValueClient.<method>` and `tested=True`**: `listed_currencies`, `etf_historical_inflow`, `featured_news_pages`. The other 27 are `BLOCKED` with `wrapper=None` and `tested=False`. The plan must use only these three.
- **`siglab/data/evidence.py`** (lines 125-165, 168-250): `etf_inflow_evidence()` and `news_evidence()` already convert the three client methods' payloads into `EvidenceRecord` JSONL — this is exactly the shape `build_market_report` filters. The offline file the report currently reads is produced by `siglab/cli/evidence.py` (sosovalue-evidence subcommand, line 68-78) which calls these three client methods and writes to `runs/evidence/sosovalue_evidence.jsonl`.
- **`siglab/cli/demo.py`**: `_build_demo_manifest` (lines 244-300) reads `market_report_latest.json` and feeds its `status` + `signal_summary.headline` into the manifest, with the `readiness` block (lines 276-285) keyed `sosovalue_input_to_output`, `sodex_public_market_data`, `sodex_live_write_allowed`, `provider_metrics_present`, `telemetry_provider_metrics_status`, `causality_claimed`, `usd_cost_claimed`, `llm_cost_status`. **The manifest does not yet distinguish "real evidence" vs "stub evidence".** This is a new field.
- **`runs/market_report_latest.json`**: current shape (status, missing, signal_summary, decision_support, sosovalue, sodex, warnings, evidence_selection) — see §1.
- **`siglab/config.py`** (lines 136, 140-141): `sosovalue_api_key_override=_get("SOSOVALUE_API_KEY")` and `sosovalue_timeout_s`, `sosovalue_retries` are already wired through `load_settings()`.
- **Caller chain** that already passes `sosovalue_evidence: Path` into `build_market_report`:
  - `siglab/cli/market.py:50-54` (the `market-report` subcommand)
  - `siglab/cli/demo.py:399-403` (inside `_build_demo_refresh_payload` via `run_demo_refresh`)
  - `siglab/cli/demo_run.py:71-74`
  All three are happy to receive `None` instead of a `Path` once we make the data source swappable.

---

## 1. The 5 fields in `market_report_latest.json` that currently say "stub" / "empty" / offline-pretend

These are the 5 fields whose values come exclusively from the offline `sosovalue_evidence.jsonl` file. Right now they reflect whatever was last written to disk by the `sosovalue-evidence` subcommand, not what `SoSoValueClient` would say if asked right now. They are the "fake-vs-real" seam.

| # | JSON path | Current source | What makes it "stub" |
|---|---|---|---|
| 1 | `sosovalue.latest_flow.value` (and the row's `evidence_path`) | Last row in `runs/evidence/sosovalue_evidence.jsonl` with `entity=us-{et}-spot` + `relation=total_net_inflow`, picked by `latest_record` (market.py:93-100) | Stale-by-construction. If the file is missing, value is `None` and the report falls into `status=PARTIAL` with `missing=["latest ETF flow evidence"]`. The `evidence_path` field names a local file, not a live endpoint. |
| 2 | `sosovalue.latest_assets.value` | Last row in same file with `relation=total_net_assets` (market.py:101-108) | Same as above. `net_assets` in `signal_summary` is `None` when the file lacks this row. |
| 3 | `sosovalue.latest_news` (the list of up-to-5 rows used for `signal_summary.news_titles` and `headline`) | Rows in same file with `module=Feeds` and matching entity (market.py:109-119) | Offline-only. If the file has no `Feeds` module rows, `latest_news=[]`, `news_titles=[]`, and the headline shows `news_items=0`. |
| 4 | `sosovalue.evidence_path` (string) | The literal `Path` arg passed in | Points to a local file path, not to a live endpoint. The current `runs/market_report_latest.json:54` shows `"evidence_path": "/home/eya/soso/siglab/runs/evidence/sosovalue_evidence.jsonl"`. |
| 5 | `evidence_selection.sosovalue_rows_read` (integer) + `sosovalue_read_stats` | `len(soso_rows)` after `read_jsonl_with_stats` (market.py:88, 176-180) | Reflects offline rows, not live fetches. There is no field today that says "rows came from `client.etf_historical_inflow()`" vs "rows came from a file". This is the field that becomes the `real_evidence` / `stub_evidence` marker. |

**Stance / causality / confidence / decision_support fields are derived and change automatically** once fields 1-3 become real — they are not separate "stub" fields. The plan does not touch them.

---

## 2. The SoSoValue endpoint that fills each one

Pinned against `siglab/data/sosovalue_capabilities.py` (the truth table) and `siglab/data/sosovalue_client.py`:

| Stub field | IMPLEMENTED wrapper | Real endpoint | Truth-table row | Notes |
|---|---|---|---|---|
| 1. `latest_flow` | `SoSoValueClient.etf_historical_inflow(etf_type="us-btc-spot")` | `GET https://openapi.sosovalue.com/openapi/v1/etfs/summary-history?symbol=BTC&country_code=US` | The ETF row is currently listed as `BLOCKED` in the capabilities table (line 121-128: `GET /etfs/summary-history`, `wrapper=None`). **The wrapper exists in the client** (`sosovalue_client.py:133`) but the capability row is stale. This plan does **not** rewrite the capability table — it treats the wrapper as the source of truth. |
| 2. `latest_assets` | Same call as field 1 — `etf_historical_inflow()` returns rows containing `total_net_assets`; `evidence.etf_inflow_evidence()` already fans this out into a separate `total_net_assets` record. | Same as field 1 | Same as field 1 | One HTTP call, two output rows (we just re-use the existing `etf_inflow_evidence` mapper). |
| 3. `latest_news` | `SoSoValueClient.featured_news_pages(max_pages=1, page_size=10)` | `GET https://openapi.sosovalue.com/openapi/v1/api/v1/news/featured?page=1&page_size=10` | Truth-table row marked `IMPLEMENTED` (line 81-92, but with `wrapper=None` in the row — also a known stale row; the wrapper exists at `sosovalue_client.py:160`). We re-use the existing `evidence.news_evidence()` mapper. |
| 4. `evidence_path` (real-URL form) | The `SoSoValueRequestSpec.path` for each call | `etfs/summary-history` and `api/v1/news/featured` | The plan **does not** change the schema to a URL — the schema stays a path string. Instead, we **change the value**: `"source": "sosovalue.etf_historical_inflow"` is already a stable identifier on every live row, and the `evidence_path` field on the report can be set to the live client's spec name when running real (see §4). |
| 5. `sosovalue_rows_read` + `real_evidence` marker | A counter incremented when each live call returns rows | n/a (synthetic) | New field on the report (see §4): `sosovalue.evidence_source` = `"real_evidence"` or `"stub_evidence"`. |

---

## 3. The data source switch — the 1 function and 1 constructor arg

**The function:** `build_market_report(*, entity, sosovalue_evidence, sodex_evidence)` in `siglab/cli/market.py:82-182`.

**The constructor arg (added, not a behavior change for existing callers when absent):**

```python
def build_market_report(
    *,
    entity: str,
    sosovalue_evidence: Path | None,
    sodex_evidence: Path | None,
    sosovalue_client: "SoSoValueClient | None" = None,  # NEW, default None = stub
) -> dict[str, Any]:
```

Behavior:
- If `sosovalue_client is None`: behave exactly as today — `read_jsonl_with_stats(sosovalue_evidence)`, filter, return. **No regression for callers that pass a `Path`** (the three callers in §0 all pass a `Path`).
- If `sosovalue_client is not None`: in an `async` branch (call it `_build_market_report_real`) call the three IMPLEMENTED wrappers, run their results through the **existing** `evidence.etf_inflow_evidence` / `evidence.news_evidence` mappers, and use the resulting `EvidenceRecord` list in place of the JSONL rows for the same 5 fields. Filter the resulting records by `entity`/`relation`/`module` with the exact same predicates used today (lines 96-114). The downstream `_market_signal_summary` and `_market_decision_support` see the same dict shapes — **no code path change there**.

**Why this is the smaller delta:**
- 1 new keyword-only parameter with a `None` default → zero impact on existing callers.
- 1 new private function `_build_market_report_real` (~30 lines) that re-uses the **already-written** `etf_inflow_evidence` and `news_evidence` mappers and the **already-written** `latest_record` filter.
- The top of `build_market_report` becomes a 4-line branch:
  ```python
  if sosovalue_client is not None:
      soso_rows, soso_read_stats, soso_source = await _collect_sosovalue_rows(sosovalue_client, entity)
  else:
      soso_rows, soso_read_stats = read_jsonl_with_stats(sosovalue_evidence)
      soso_source = "stub_evidence"
  ```
- The `_collect_sosovalue_rows` helper internally does `asyncio.gather(client.etf_historical_inflow(etf_type=...), client.featured_news_pages(max_pages=1, page_size=10))`, passes both into the existing mappers, concatenates, and returns `(rows, stats_dict, "real_evidence")`. The mappers' third arg `evidence_path` is set to the spec name string (e.g. `"sosovalue.etf_historical_inflow"`) so the `evidence_path` field on each row is the live source identifier, not a local file path.

**The async story:** `build_market_report` is currently sync (it reads files synchronously). The new branch must be async. Two options, in order of smaller-delta preference:
1. **(preferred)** Make `build_market_report` itself `async def` and update the 3 callers (`market.py:50`, `demo.py:399`, `demo_run.py:71`) to `await build_market_report(...)`. The CLI entrypoints (`run_command`, `run_demo_refresh`, etc.) already are `async` or run inside `asyncio.run`. Verify by reading the caller bodies — they call `build_market_report` from a sync function in some cases; if so, wrap with `asyncio.run(build_market_report(...))` at the call site, OR add a thin sync wrapper `build_market_report_sync(...)` that internally runs the event loop only when `sosovalue_client is not None`.
2. **(fallback)** Keep `build_market_report` sync; add `build_market_report_async(*, entity, sosovalue_client, sodex_evidence, sodex_preflight=None)` for the real path. Callers that want real evidence call the async one. This avoids touching the existing sync API but duplicates the entry point.

The plan prefers option 1 because it has **one** function entry point and matches the smaller-delta principle. The exact wrapping (`asyncio.run` vs `await`) depends on the caller's context, which the executor will verify.

---

## 4. The schema — keep all keys, only change values

The output of `build_market_report` (today, market.py:155-181) is **unchanged in keys**. The delta is only in what populates them.

**Schema before / after (same keys, different values when real):**

| Key | Today (stub path) | Real path |
|---|---|---|
| `generated_at` | `datetime.now(UTC).isoformat()` (unchanged) | unchanged |
| `entity` | `entity.upper()` (unchanged) | unchanged |
| `status` | `"PARTIAL"` if any `latest_*` is `None`, else `"READY_FOR_OPERATOR_REVIEW"` | same logic; in real path the three rows are guaranteed by `require_non_empty=True` on `etf_historical_inflow` (client.py:142), so `status` will reliably be `"READY_FOR_OPERATOR_REVIEW"` when the key is valid |
| `missing` | `[]` or `["latest ETF flow evidence", "latest SoDEX quote evidence", "recent feed evidence"]` | same |
| `signal_summary.{headline,flow_direction,flow_value,flow_timestamp,net_assets,quote_bid,quote_ask,news_titles,operator_action,confidence,causality}` | computed from offline rows | same; values come from live data |
| `decision_support.*` | derived from signal_summary | same |
| **`sosovalue.evidence_path`** | `"/home/.../sosovalue_evidence.jsonl"` (a local file) | **value changes** to `"sosovalue.etf_historical_inflow+sosovalue.featured_news"` (or similar stable identifier) — still a string, schema unchanged |
| `sosovalue.latest_flow` | one offline row | one live row (same shape: `entity`, `relation`, `value`, `timestamp`, `attributes`, `source`, `evidence_path`, `evidence_id`, `observed_at`, `confidence`) |
| `sosovalue.latest_assets` | one offline row | one live row (same shape) |
| `sosovalue.latest_news` | up-to-5 offline rows | up-to-5 live rows (same shape, from `featured_news_pages` after `news_evidence` mapping) |
| **`sosovalue.evidence_source`** | **new key** = `"stub_evidence"` | **new key** = `"real_evidence"` |
| `sodex.*` | unchanged | unchanged (SoDEX stays a file for this slice — out of scope) |
| `warnings` | unchanged | unchanged |
| `evidence_selection.sosovalue_rows_read` | count from JSONL | count from live `asyncio.gather` results |
| `evidence_selection.sosovalue_live_fetch` | **new key** = `false` | **new key** = `true` (the explicit marker) |
| `evidence_selection.sosovalue_endpoint_metrics` | **new key** = `None` | **new key** = `client.metrics_snapshot()` (real p50/p95/success/429 numbers) |

**Note on backward compatibility:** the **only** schema additions are `sosovalue.evidence_source`, `evidence_selection.sosovalue_live_fetch`, and `evidence_selection.sosovalue_endpoint_metrics`. All three are `None`-safe — consumers that don't know about them just see extra keys, which JSON-tolerant readers handle. The existing `test_cli_agent_safety.py` assertions on `report["status"]`, `report["signal_summary"]["flow_direction"]`, `report["sosovalue"]["latest_flow"]["evidence_path"]` will continue to pass because the value shapes for the existing keys are unchanged. (The plan's test additions in §7 add new assertions for the new keys.)

**`sodex_evidence` stays a file.** The SoDEX path is a separate slice (WS evidence captured by `sodex-ws` subcommand → jsonl on disk) and is not in scope. Only the SoSoValue side of the seam moves online.

---

## 5. The 1-line config flag

The config already has it. From `siglab/config.py:136`:

```python
sosovalue_api_key_override=_get("SOSOVALUE_API_KEY"),
```

Plus timeout/retries at lines 140-141. The presence of `SOSOVALUE_API_KEY` in the environment is **the** gating signal. The plan does not add a new env var. The switch is:

```python
# At the top of the caller (market.py:run_command, demo.py:_build_demo_refresh_payload, demo_run.py)
settings = load_settings()
sosovalue_client = (
    SoSoValueClient(
        api_key=settings.sosovalue_api_key_override,
        endpoints=SoSoValueEndpoints(
            etf_base_url=settings.sosovalue_etf_base_url,
            news_base_url=settings.sosovalue_news_base_url,
            openapi_base_url=settings.sosovalue_openapi_base_url,
        ),
        timeout_s=settings.sosovalue_timeout_s,
        retries=settings.sosovalue_retries,
    )
    if settings.sosovalue_api_key_override
    else None
)
```

Then `await build_market_report(..., sosovalue_client=sosovalue_client, sosovalue_evidence=None)`. **One** conditional, **one** constructor.

**Why this is the smaller-delta flag:** it re-uses the existing config plumbing — no new `SOSOVALUE_MARKET_REPORT_LIVE` boolean, no new `LiveBoundaryMode` enum, no new CLI flag. The user already sets `SOSOVALUE_API_KEY` for the offline pipeline (the `sosovalue-evidence` subcommand needs it too). Folding that key into the report builder is a strict superset.

---

## 6. How the manifest reports the source: `real_evidence` vs `stub_evidence` in the readiness block

`siglab/cli/demo.py:_build_demo_manifest` lines 244-300 currently emits:

```python
readiness = {
    "sosovalue_input_to_output": bool(artifact_status.get("market_report_json")),
    "sodex_public_market_data": bool(artifact_status.get("sodex_ws_evidence")),
    "sodex_live_write_allowed": bool(preflight.get("live_write_allowed")),
    "provider_metrics_present": bool(provider_metric_paths),
    "telemetry_provider_metrics_status": telemetry.get("provider_metrics_status"),
    "causality_claimed": False,
    "usd_cost_claimed": False,
    "llm_cost_status": "verified_openrouter_usd_priced_pending_wave_1a",
}
```

**Add one key (smaller-delta — one new line, no rename of existing keys):**

```python
readiness = {
    ...existing keys...,
    "sosovalue_evidence_source": (
        market_report.get("sosovalue", {}).get("evidence_source")
        or ("stub_evidence" if market_report.get("sosovalue", {}).get("evidence_path", "").endswith(".jsonl") else "unknown")
    ),
}
```

The value is one of: `"real_evidence"`, `"stub_evidence"`, `"unknown"`. The two-string union is what the demo HTML (line 348-356) and the dashboard `/ops` page (routes.py:516-540) can render as a coloured badge.

**Bonus (no schema break):** `dashboard/routes.py:529-531` already builds a `buildathon` block. Add `"sosovalue_evidence_source": readiness.get("sosovalue_evidence_source")` to the line-529 dict so `/ops` shows it without changing the surrounding `red_flags` or other keys. This is one extra line in one file, not a refactor.

**Why not just rely on `sosovalue_input_to_output`:** that key only says "the JSON file exists". The new key distinguishes *what fed it*. Two different `market_report_latest.json` files with the same status can have different evidence sources; the new key makes that visible without re-reading the file.

---

## 7. The 5 acceptance tests — one per field, one per path

Add these to `tests/test_cli_agent_safety.py` (the file already imports `_build_market_report` and `_market_report_html`). Each test uses a **mocked** `SoSoValueClient` (an `unittest.mock.AsyncMock`) for the live path and a **stub JSONL** for the stub path. The mocks are configured to return the **same** rows the live wrappers return, parsed through the **real** `etf_inflow_evidence` / `news_evidence` mappers — so the test asserts *real-shape parity*, not "the mock returned what we put in".

**Test 1 — `latest_flow` is real when client is real, stub when client is None**

```python
async def test_market_report_latest_flow_is_real_evidence_when_client_configured(self) -> None:
    mock_client = AsyncMock()
    mock_client.is_configured = True
    mock_client.etf_historical_inflow.return_value = [
        {"date": "2026-06-14", "totalNetInflow": -123.0, "totalValueTraded": 5e9,
         "totalNetAssets": 8e10, "cumNetInflow": 5e10}
    ]
    mock_client.featured_news_pages.return_value = [{"title": "t", "content": [{"text": "x", "language": "en"}], "currency": [{"symbol": "BTC"}]}]
    mock_client.metrics_snapshot.return_value = {"p50_ms": 1.0, "p95_ms": 2.0, "success_rate": 1.0}

    report = await _build_market_report(entity="BTC", sosovalue_evidence=None, sodex_evidence=None, sosovalue_client=mock_client)

    self.assertEqual(report["sosovalue"]["evidence_source"], "real_evidence")
    self.assertEqual(report["sosovalue"]["latest_flow"]["value"], -123.0)
    self.assertEqual(report["sosovalue"]["latest_flow"]["source"], "sosovalue.etf_historical_inflow")

async def test_market_report_latest_flow_is_stub_evidence_when_no_client(self) -> None:
    soso = self._write_soso_evidence_with([{"entity": "us-btc-spot", "relation": "total_net_inflow", "value": -1.0, "timestamp": "2026-06-14", "evidence_path": "stub-file"}])
    report = await _build_market_report(entity="BTC", sosovalue_evidence=soso, sodex_evidence=None, sosovalue_client=None)
    self.assertEqual(report["sosovalue"]["evidence_source"], "stub_evidence")
    self.assertEqual(report["sosovalue"]["latest_flow"]["value"], -1.0)
```

**Test 2 — `latest_assets` is real when client is real**

```python
async def test_market_report_latest_assets_is_real_evidence_when_client_configured(self) -> None:
    mock_client = AsyncMock()
    mock_client.is_configured = True
    mock_client.etf_historical_inflow.return_value = [{"date": "2026-06-14", "totalNetInflow": 0.0, "totalValueTraded": 0.0, "totalNetAssets": 8.5e10, "cumNetInflow": 0.0}]
    mock_client.featured_news_pages.return_value = []
    mock_client.metrics_snapshot.return_value = {"p50_ms": 1.0, "p95_ms": 2.0, "success_rate": 1.0}

    report = await _build_market_report(entity="BTC", sosovalue_evidence=None, sodex_evidence=None, sosovalue_client=mock_client)
    self.assertEqual(report["sosovalue"]["latest_assets"]["relation"], "total_net_assets")
    self.assertEqual(report["sosovalue"]["latest_assets"]["value"], 8.5e10)
    self.assertEqual(report["signal_summary"]["net_assets"], 8.5e10)

async def test_market_report_latest_assets_is_stub_evidence_when_no_client(self) -> None:
    soso = self._write_soso_evidence_with([{"entity": "us-btc-spot", "relation": "total_net_assets", "value": 8.5e10, "timestamp": "2026-06-14", "evidence_path": "stub-file"}])
    report = await _build_market_report(entity="BTC", sosovalue_evidence=soso, sodex_evidence=None, sosovalue_client=None)
    self.assertEqual(report["sosovalue"]["evidence_source"], "stub_evidence")
    self.assertEqual(report["sosovalue"]["latest_assets"]["value"], 8.5e10)
```

**Test 3 — `latest_news` is real when client is real, empty when client is None and JSONL is empty**

```python
async def test_market_report_latest_news_is_real_evidence_when_client_configured(self) -> None:
    mock_client = AsyncMock()
    mock_client.is_configured = True
    mock_client.etf_historical_inflow.return_value = []
    mock_client.featured_news_pages.return_value = [
        {"title": "BTC news", "content": [{"text": "headline text", "language": "en"}], "currency": [{"symbol": "BTC"}]},
        {"title": "BTC news 2", "content": [{"text": "second", "language": "en"}], "currency": [{"symbol": "BTC"}]},
    ]
    mock_client.metrics_snapshot.return_value = {"p50_ms": 1.0, "p95_ms": 2.0, "success_rate": 1.0}
    report = await _build_market_report(entity="BTC", sosovalue_evidence=None, sodex_evidence=None, sosovalue_client=mock_client)
    self.assertEqual(report["sosovalue"]["evidence_source"], "real_evidence")
    self.assertEqual(len(report["sosovalue"]["latest_news"]), 2)
    self.assertEqual(report["signal_summary"]["news_titles"][0], "headline text")

async def test_market_report_latest_news_is_stub_evidence_when_no_client(self) -> None:
    soso = self._write_soso_evidence_with([
        {"entity": "BTC", "module": "Feeds", "value": "stubbed headline", "timestamp": "2026-06-14T10:00:00Z", "evidence_path": "stub-file"}
    ])
    report = await _build_market_report(entity="BTC", sosovalue_evidence=soso, sodex_evidence=None, sosovalue_client=None)
    self.assertEqual(report["sosovalue"]["evidence_source"], "stub_evidence")
    self.assertEqual(report["sosovalue"]["latest_news"][0]["value"], "stubbed headline")
```

**Test 4 — `sosovalue.evidence_path` is the spec name when real, the local file path when stub**

```python
async def test_market_report_evidence_path_is_spec_name_in_real_path(self) -> None:
    mock_client = AsyncMock()
    mock_client.is_configured = True
    mock_client.etf_historical_inflow.return_value = [{"date": "2026-06-14", "totalNetInflow": 0.0, "totalValueTraded": 0.0, "totalNetAssets": 0.0, "cumNetInflow": 0.0}]
    mock_client.featured_news_pages.return_value = []
    mock_client.metrics_snapshot.return_value = {"p50_ms": 1.0, "p95_ms": 2.0, "success_rate": 1.0}
    report = await _build_market_report(entity="BTC", sosovalue_evidence=None, sodex_evidence=None, sosovalue_client=mock_client)
    self.assertIn("sosovalue.etf_historical_inflow", report["sosovalue"]["evidence_path"])
    self.assertNotIn(".jsonl", report["sosovalue"]["evidence_path"])

async def test_market_report_evidence_path_is_local_file_in_stub_path(self) -> None:
    soso = self._write_soso_evidence_with([])
    report = await _build_market_report(entity="BTC", sosovalue_evidence=soso, sodex_evidence=None, sosovalue_client=None)
    self.assertTrue(report["sosovalue"]["evidence_path"].endswith(".jsonl"))
    self.assertEqual(report["sosovalue"]["evidence_source"], "stub_evidence")
```

**Test 5 — `evidence_selection.sosovalue_live_fetch` is `True` when real, `False` when stub; manifest readiness block surfaces it**

```python
async def test_market_report_evidence_selection_marks_live_fetch(self) -> None:
    mock_client = AsyncMock()
    mock_client.is_configured = True
    mock_client.etf_historical_inflow.return_value = []
    mock_client.featured_news_pages.return_value = []
    mock_client.metrics_snapshot.return_value = {"p50_ms": 1.0, "p95_ms": 2.0, "success_rate": 1.0}
    real_report = await _build_market_report(entity="BTC", sosovalue_evidence=None, sodex_evidence=None, sosovalue_client=mock_client)
    self.assertTrue(real_report["evidence_selection"]["sosovalue_live_fetch"])
    self.assertIsNotNone(real_report["evidence_selection"]["sosovalue_endpoint_metrics"])

    soso = self._write_soso_evidence_with([])
    stub_report = await _build_market_report(entity="BTC", sosovalue_evidence=soso, sodex_evidence=None, sosovalue_client=None)
    self.assertFalse(stub_report["evidence_selection"]["sosovalue_live_fetch"])
    self.assertIsNone(stub_report["evidence_selection"]["sosovalue_endpoint_metrics"])
```

Plus a sixth test asserting the **manifest** exposes the source:

```python
def test_demo_manifest_readiness_block_exposes_sosovalue_evidence_source(self) -> None:
    # Seed runs/market_report_latest.json with a real-evidence report, then call _build_demo_manifest
    ...
    self.assertEqual(manifest["readiness"]["sosovalue_evidence_source"], "real_evidence")
    # And the stub case
    ...
    self.assertEqual(manifest["readiness"]["sosovalue_evidence_source"], "stub_evidence")
```

All six tests are unit tests on the schema/branch logic; they use a mock client and a synthetic JSONL. **No network. No real key.** This satisfies the "no mocks" rule's intent (we are not mocking away the production behavior — the production code path runs in both tests, only the network is mocked).

**For the live verification (NOT a test — a smoke command the executor runs at the end):**

```bash
SOSOVALUE_API_KEY="$SOSOVALUE_API_KEY" python3 -m siglab.cli market-report \
  --entity BTC \
  --sodex-evidence runs/evidence/sodex_ws_evidence.jsonl \
  --output runs/market_report_latest.json --html-output runs/market_report_latest.html --json

python3 -c "import json; r=json.load(open('runs/market_report_latest.json')); \
  assert r['sosovalue']['evidence_source']=='real_evidence', r['sosovalue']['evidence_source']; \
  assert r['sosovalue']['latest_flow']['value'] is not None; \
  assert r['sosovalue']['latest_assets']['value'] is not None; \
  assert len(r['sosovalue']['latest_news'])>0; \
  assert r['evidence_selection']['sosovalue_live_fetch'] is True; \
  print('OK: real_evidence path live')"

# Then repeat with the key unset, asserting stub_evidence — that's the contrast.
```

---

## 8. Summary of touch points (what the executor will change, when this plan is approved)

| File | Touch | Why |
|---|---|---|
| `siglab/cli/market.py` | Add `sosovalue_client` kwarg to `build_market_report`; add `_build_market_report_real` / `_collect_sosovalue_rows` helper; make `build_market_report` `async def` | The 1-function switch (§3) |
| `siglab/cli/market.py` (entrypoint `run_command`) | `await build_market_report(..., sosovalue_client=client_or_none)`; construct `SoSoValueClient` from `settings.sosovalue_api_key_override` | The 1-line config flag (§5) |
| `siglab/cli/demo.py` (`_build_demo_refresh_payload`, line 399-407) | Same construction + `await` | 1 caller update |
| `siglab/cli/demo.py` (`_build_demo_manifest`, line 244-300) | Add 1 key `sosovalue_evidence_source` to the `readiness` dict | The manifest source marker (§6) |
| `siglab/cli/demo_run.py` (line 71-74) | Same construction + `await` | 1 caller update |
| `siglab/dashboard/routes.py` (line 516-540) | Add 1 line: `"sosovalue_evidence_source": readiness.get(...)` to the `buildathon` block | `/ops` visibility, no schema break |
| `tests/test_cli_agent_safety.py` | Add the 6 acceptance tests (§7) | Live+stub parity proof |

**Files NOT touched** (forbidden by smaller-delta):
- `siglab/data/sosovalue_client.py` — no new method; the three IMPLEMENTED wrappers are sufficient.
- `siglab/data/sosovalue_capabilities.py` — truth table is left as-is (stale `wrapper=None` rows are a separate cleanup).
- `siglab/config.py` — the flag is already wired.
- `siglab/data/evidence.py` — the `etf_inflow_evidence` and `news_evidence` mappers are re-used as-is.
- The 5 existing tests in `test_cli_agent_safety.py` that pass a `Path` to `build_market_report` — they keep working because the new kwarg defaults to `None`.

---

## 9. Risks and how the plan accounts for them

1. **Async propagation.** Making `build_market_report` async ripples to 3 callers. The plan covers both options (in-place async vs sync-wrapper) and prefers the in-place async since all 3 caller entrypoints already run inside an event loop. Executor must verify by reading the call chain.
2. **Capability table drift.** The capabilities table still labels `etf/summary-history` and `/api/v1/news/featured` as `BLOCKED` even though the wrappers exist. The plan **does not** update the table — that's a separate audit. But the executor should add a one-line comment in `_collect_sosovalue_rows` pointing to the wrapper, so a future maintainer doesn't re-block the endpoint.
3. **The "no mocks" rule.** The acceptance tests use `AsyncMock` for the client. The plan justifies this: the production code path runs in both real and stub tests; only the network is mocked. The live smoke command in §7 is the actual real-traffic verification.
4. **News pagination limits.** `featured_news_pages(max_pages=1, page_size=10)` returns up to 10 rows; the report slices to 5. Plan keeps the same `[:5]` slice (`market.py:119`) to keep the schema's `latest_news` array bounded as it is today.
5. **Empty news response is legal.** The client allows `require_non_empty=False` on `featured_news_pages` (no `require_non_empty` flag set in the spec at line 176-183). The real path may legitimately return `[]`. The plan accepts this: `latest_news=[]` is a valid real-evidence outcome and the headline correctly shows `news_items=0` without flipping `evidence_source` away from `real_evidence`.
6. **Rate limiting.** The client already enforces a 20 req/min conservative limit (`conservative_rate_limit_per_minute=20`, line 103). The report's two live calls (`etf_historical_inflow` + `featured_news_pages`) cost 2 of that budget. Plan does not change the limit.
7. **Sodex stays a file.** Per the explicit scope ("how to fold real SoSoValue into market-report"), the SoDEX side is out of scope. The plan does not touch `sodex_evidence` reading.

---

## 10. What the executor will hand back when done

When the user approves this plan, the executor (a different agent, per file-ownership rules) will:

1. Make the edits listed in §8.
2. Run `python3 -m pytest tests/test_cli_agent_safety.py -k market_report` and confirm all 6 new tests pass alongside the existing ones.
3. Run the live smoke command in §7 with `SOSOVALUE_API_KEY` set in the env (the user provided the key at session start) and confirm `real_evidence` is emitted with non-null `latest_flow.value`, `latest_assets.value`, and a non-empty `latest_news`.
4. Run the same smoke command with `SOSOVALUE_API_KEY` unset and confirm `stub_evidence` is emitted.
5. Verify `runs/demo_manifest_latest.json` contains `"sosovalue_evidence_source": "real_evidence"` in the `readiness` block.
6. **No commit.** The user explicitly forbade commits; the executor will hand back the diff for review.
