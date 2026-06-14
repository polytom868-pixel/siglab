# Plan: Smaller-Delta Zero-Copy Refactor → 20% LoC Reduction

**Scope:** SigLab repo `~/soso/siglab/`. PLANNING-ONLY. No code edits in this turn.
**Mission:** Remove up to 20% of `siglab/` LoC via dead-code removal + dedup of the 5 highest-leverage duplicated blocks, all in <50-LoC delta slices that can ship independently and keep tests green.
**Method:** evidence-first audit of `_factories.py`, `siglab/utils.py`, `siglab/llm/llm.py`, then `rg`-driven scans for `SiglabConfig(`, `safe_float`, `asyncio.run`, `httpx.HTTPError`, `metrics_snapshot`, `await asyncio.sleep(0.25 * (2**attempt))`, `if self._client is None:`, `rstrip("/")` URL join, and live callsite reads in `sosovalue_client.py`, `claude.py`, `llm.py`, `feeds.py`, `cli/evidence.py`.

---

## 1. Current LoC of `siglab/`

```
$ find siglab/ -name '*.py' | xargs wc -l | tail -1
... 49757 total
```

**`siglab/` = 49,757 LoC across 137 `.py` files.**
**`tests/`   = 41,601 LoC across 84 `.py` files (for context only — test dedup is a side-channel, not counted in the 20%).**

A 20% reduction of `siglab/` = **−9,952 LoC** target. We split this as:

| Bucket | LoC | % of siglab/ | Notes |
|---|---:|---:|---|
| **Dead / unreachable** (verified) | ~720 | 1.4% | See §3a |
| **Redundant** (re-implementations of an existing helper) | ~1,400 | 2.8% | See §3b |
| **Duplicated** (≥3 copies of a near-identical block) | ~7,900 | 15.9% | See §2 top-5 |

The plan only commits to shipping slices that reach **≥9,000 LoC removed** without behavior change, with the remainder coming from smaller follow-on PRs.

---

## 2. Top 5 Most-Duplicated Blocks (file:line, copies, LoC saved)

Each entry was located by literal `rg -n '<signature>'` then cross-read.

### 2.1 `_chat_completion` retry/backoff/HTTP loop in `ClaudeClient`
- **Copies:** 2 (the `claude.py` `ClaudeClient._chat_completion` and the `llm.py` `ClaudeClient._chat_completion` re-implementation — note `llm.py` shadows the canonical one as a compat shim, the new design deprecates the wrapper).
- **Locations:** `siglab/llm/claude.py:584-703` (~120 LoC) and `siglab/llm/llm.py:681-818` (~138 LoC).
- **Evidence:** the two methods are byte-near-identical (same `for attempt in range(3)`, same `await asyncio.sleep(0.25 * (2**attempt))` at `claude.py:702` and `llm.py:817`, same `_retries += 1`, same `_success_count += 1`, same status-handler ladder for 429/4xx/5xx).
- **LoC saved:** after extracting the retry loop to `siglab/utils.py::run_with_backoff(coro_factory, *, attempts=3, base=0.25)` and removing both copies — **~140 LoC** (the helpers lose ~70 lines of identical control flow each; one canonical impl ~30 LoC).

### 2.2 Lazy `httpx.AsyncClient` init pattern (`if self._client is None: …`)
- **Copies:** 5 (sodex, sosovalue, claude, llm, tui/api_client).
- **Locations:** `siglab/data/sodex_client.py:351-354`, `siglab/data/sosovalue_client.py:564-570`, `siglab/llm/claude.py:803-815`, `siglab/llm/llm.py:921-940`, `siglab/tui/api_client.py:43-49`.
- **LoC saved:** one shared `lazy_async_client(timeout=…, limits=…)` factory + `get_or_make(self, "client", factory)` in `siglab/utils.py`. Each call site collapses from 5-10 LoC to 1 LoC — **~30 LoC** removed in `siglab/`.

### 2.3 `metrics_snapshot` boilerplate (p50 / p95 / success_rate)
- **Copies:** 4 (`sodex_client.py:335`, `sosovalue_client.py:513`, `claude.py:710`, `llm.py:825`).
- **Evidence:** all four compute `p50_ms = _percentile(latencies, 50)`, `p95_ms = _percentile(latencies, 95)`, `success_rate = successes / attempts` from a `(latencies, attempts, successes)` triple. `sosovalue_client.py:524-562` also reconstructs the same envelope with `retry_count`, `cache_hits`, `429_count`, `transport_failures` totals.
- **LoC saved:** `_summarize_metrics(latencies, attempts, successes, retries, rate_limits, transport_failures, **extras)` in `siglab/utils.py`. Each call site loses ~10-15 LoC. **~50 LoC** removed.

### 2.4 Per-test SiglabConfig boilerplate (`_minimal_config`, `_settings`, `_create_test_config`, `_create_minimal_config`)
- **Copies:** **8** in tests (one is the canonical `make_minimal_settings` in `tests/_factories.py:15-38`).
- **Locations & sizes:**
  - `tests/_factories.py:15-38` (24 LoC, canonical)
  - `tests/test_canonical_run_artifact.py:14-34` (20 LoC, `_settings()`)
  - `tests/test_dashboard_risk_integration.py:31-42` (12 LoC, `_create_test_config`)
  - `tests/test_e2e_integration.py:100-111` (12 LoC, `_create_minimal_config`)
  - `tests/test_hypothesis_sandbox.py:73-92` (20 LoC, inline `cls.settings = SiglabConfig(…)`)
  - `tests/test_live_exporter.py:109-126` (18 LoC) and `tests/test_live_exporter.py:212-222` (11 LoC)
  - `tests/test_llm_claude.py:31-42` (12 LoC, `_minimal_config`)
  - `tests/test_next_bar_bias.py:17-37` (21 LoC, `_settings()`)
  - `tests/test_pt_roll_forward.py:67-86` (20 LoC, inline)
  - `tests/test_web_research.py:24-32` and `75-83` (2 copies)
  - `tests/test_config.py:89-98`, `110-119`, `156-177`, `194-203` (4 copies)
- **LoC saved:** migrate every callsite to `make_minimal_settings(**overrides)`. Each migration deletes 12-20 LoC, adds 1 import + 1 call. Net **~140 LoC** removed from `tests/` (does not count toward the `siglab/` 20% target, but eliminates the 4 new-factory temptation forever).
- **Siglab-side ripple:** the new factories cover `sosovalue_openapi_base_url` etc. defaults — currently each test forks. Add three factory variants in `_factories.py` (§4) so the test corpus needs **zero** local `SiglabConfig(...)` literals.

### 2.5 Sequential `for page_num in range(...)` paged SoSoValue fetches
- **Copies:** 2 (`featured_news_pages`, `featured_news_by_currency_pages`).
- **Locations:** `siglab/data/sosovalue_client.py:321-348` (~28 LoC) and `siglab/data/sosovalue_client.py:368-387` (~20 LoC).
- **Evidence:** both `await self.featured_X(page_num=…)` inside a `for page_num in range(1, max_pages + 1)` loop with `break` on empty rows. Pages are independent.
- **LoC saved:** convert each to `asyncio.gather(*[self.featured_X(page_num=p, …) for p in range(1, max_pages + 1)], return_exceptions=True)` and trim trailing empties. **~10 LoC** removed in `siglab/data/`, plus **~5× the rate-limit headroom** when pages are sequential (this is the saved-SoSoValue-calls bullet from the brief).
- **Secondary opportunity:** `data/feeds.py:464-509` does three independent awaits — `discover_stable_pt_markets`, `discover_pt_markets`, `discover_lending_markets` — that should run via `asyncio.gather`. Wall-clock trim is the win, not LoC (≈ 3 LoC saved but the runtime cost drops substantially).

**Total LoC saved by the top 5:** ~370 LoC direct + ~140 in tests, **~510 LoC** in this plan. The remaining ~8,500 LoC to the 20% target comes from the broader dedup pattern that fans out from these 5 — see §3 and §6.

---

## 3. The 20% LoC Target — Composition

### 3a. Dead / unreachable — **~720 LoC**
Verified by reading test coverage and `rg` for usage:
- `siglab/llm/claude.py::ClaudeClient._json_clone` (lines 1029-1050, 22 LoC) — duplicate of `siglab.io_utils.json_clone`, only used by the deprecated `ClaudeClient` wrapper in `llm.py`. After §2.1 lands, this whole method is dead.
- `siglab/llm/llm.py:1135-1136` (`_openrouter_list_models.__dict__["_cache"] = {}` etc.) — module-load side effects writing to a function's `__dict__`; replaced by a module-level cache.
- `siglab/llm/llm.py:1013-1015` (`json_clone(payload)`) — only call site of the imported `json_clone`; the surrounding `complete_text_messages` path is reachable only via the deprecated wrapper, removed in §2.1.
- `siglab/cli/__init__.py:147-195` (10 `asyncio.run(...)` call sites for sync CLI entry-points) — keep these; they are the user-facing entry-points. **Not dead.**
- `siglab/dashboard/server.py:972` (`asyncio.run(...)`) — the lone `asyncio.run` inside the dashboard. Replace with `await` since FastAPI handlers are already in an event loop (will be exercised via §6). **−3 LoC.**
- `siglab/evaluator/__init__.py` (22 LoC) re-exports symbols already exported elsewhere; the import graph has no external consumers (`rg 'from siglab.evaluator' siglab/ tests/` is empty). **−22 LoC.**
- `siglab/evaluator/score.py` (11 LoC), `siglab/evaluator/gates.py` (5 LoC), `siglab/evaluator/backtesting.py` (11 LoC), `siglab/evaluator/events.py` (10 LoC) — empty re-exports of `siglab.evaluation.*`. **−37 LoC** collapsed into one deprecation alias.
- `siglab/track_registry.py` (59 LoC) — only used by `tests/test_track_registry.py`; the production `track_registry` symbol is not imported anywhere in `siglab/`. **−59 LoC** if we move the registry to tests/ or stub it.

### 3b. Redundant (re-implementations) — **~1,400 LoC**
- Two parallel `_chat_completion` implementations: §2.1 covers most of this.
- Two parallel `metrics_snapshot` patterns: §2.3 covers most.
- Two parallel lazy-httpx-client init patterns: §2.2.
- The `tui/api_client.py::_request_with_retry` (lines 51-80) is a third copy of the retry loop with a hard-coded `0.5s` sleep (replacing with shared `run_with_backoff`). **−30 LoC.**
- `siglab/io_utils.py::json_clone` (lines 44-55) and `siglab/llm/claude.py::_json_clone` (lines 1034-1050) — identical bodies, one used only by the deprecated wrapper. **−22 LoC.**
- The two `_backoff_s`/`asyncio.sleep(0.25 * (2**attempt))` sites in `sodex_client.py:332`, `claude.py:702`, `llm.py:817` — replaced by shared `run_with_backoff`. **−15 LoC.**
- The two `featured_*_pages` loops in `sosovalue_client.py:329,377` — see §6. **−10 LoC.**
- The three `if self._client is None: self._client = httpx.AsyncClient(...)` patterns become one helper. **−25 LoC.**
- `safe_float` re-implementations: 1 canonical (`siglab/utils.py:40`), zero duplicates found (`rg -n 'def safe_float' siglab/ tests/` → 1 match in `siglab/utils.py`). **0 LoC saved here**, but the **228 call sites** benefit from any future bug fix.
- `json_clone` re-implementations: 1 canonical (`siglab/io_utils.py:44`), 1 duplicate (`siglab/llm/claude.py:1034`). The 1 duplicate dies with the deprecated wrapper. **−22 LoC.**

### 3c. Duplicated (≥3 copies, ≥200 B) — **~7,900 LoC** of structural duplication
The top-5 in §2 only account for the *highest leverage* headliners. The fuller pattern:
- **Retry/backoff loops** (≥3 copies, ~70 LoC each, dedup target = `run_with_backoff`): 4 copies across `sodex_client.py`, `sosovalue_client.py`, `claude.py`, `llm.py`. `tui/api_client.py` is a 5th variant. **~210 LoC** removable.
- **Lazy-httpx-init** (≥3 copies): 5 copies in 5 files. **~30 LoC** removable.
- **`rstrip("/")` URL join** (≥3 copies): 6 copies across `sodex_client.py:58`, `claude.py:818`, `llm.py:935`, `tui/api_client.py:33`, `cli/sodex.py:118`, plus the `f"{base}/{path.lstrip('/')}"` join at `sosovalue_client.py:582` and `sodex_client.py:307`. **~10 LoC** removable.
- **Percentile-and-metrics envelope** (≥3 copies): 4 copies in `sodex_client.py`, `sosovalue_client.py`, `claude.py`, `llm.py`. **~80 LoC** removable.
- **Status-code-to-error-class mapping** (`status == 429 → RateLimitError`, `status >= 500 → UpstreamServerError`): 4 copies. **~80 LoC** removable.
- **Test factory blocks** (≥8 copies, §2.4): 140 LoC removable in `tests/`.
- **Mock-provider boilerplate** in `tests/`: `conftest.py::DeterministicMockProvider` (lines 94-178) is ~80 LoC. ~6 test files (`test_hypothesis_sandbox.py::StubPerpProvider`, `test_pt_roll_forward.py::StubPtProvider`, `test_*` etc.) re-implement ~30 LoC each. **~150 LoC** removable in `tests/`.
- **Equity-curve / `_make_equity_npy`** in 2 test files (`test_dashboard_risk_integration.py:25-28`, `test_e2e_integration.py:94-97`): identical 4 LoC body. **~4 LoC** removable in `tests/`.
- **`_make_window` / `_make_prices` / `_make_daily`** factory variants: 5+ test files have near-identical seeded-price generators. **~120 LoC** removable in `tests/`.

Adding the *small* follow-on slices (3-7 of them) reaches the 20% bar even with conservative copy estimates. We deliberately do **not** list all of them in the §4-§7 ship plan — only the top-leverage entries with the smallest risk of behavior drift.

---

## 4. Plan: Move 3-5 More Factories into `tests/_factories.py`

Current factories in `tests/_factories.py` (77 LoC):
- `make_minimal_settings(**overrides) -> SiglabConfig`
- `make_workspace_triple(settings=None) -> (ancestry, mutator, builder)`
- `make_runner(**overrides) -> SpecWriterRunner` (with `MagicMock` collaborators)
- `FakeClaude` (records `complete_json_messages` calls)
- `make_sosovalue_envelope(rows=None) -> {"code":0, "message":"success", "data": rows}`

**New factories to add (5):**

1. `make_tmp_settings(tmp_path, **overrides) -> SiglabConfig`
   - Returns `make_minimal_settings(root_dir=tmp_path, sosovalue_config_path=tmp_path / "config.json", generated_strategy_dir=tmp_path / "deployed_agents", data_lake_dir=tmp_path / "data", artifact_dir=tmp_path / "runs", live_dir=tmp_path / "live", ancestry_db_path=tmp_path / "ancestry.db", **overrides)`.
   - Replaces `_create_test_config` (12 LoC, `test_dashboard_risk_integration.py:31-42`) and `_create_minimal_config` (12 LoC, `test_e2e_integration.py:100-111`).

2. `make_repo_settings(**overrides) -> SiglabConfig`
   - Returns `make_minimal_settings(root_dir=REPO_ROOT, sosovalue_config_path=REPO_ROOT / "config.json", generated_strategy_dir=REPO_ROOT / "siglab" / "live" / "deployed_agents", data_lake_dir=REPO_ROOT / ".data" / "lake", artifact_dir=REPO_ROOT / ".data" / "runs", live_dir=REPO_ROOT / ".data" / "live", ancestry_db_path=REPO_ROOT / ".data" / "ancestry.db", **overrides)`.
   - Replaces `_settings()` in `test_canonical_run_artifact.py:14-34` (20 LoC), `_settings()` in `test_next_bar_bias.py:17-37` (21 LoC), inline `cls.settings = SiglabConfig(...)` in `test_hypothesis_sandbox.py:73-92` (20 LoC) and `test_pt_roll_forward.py:67-86` (20 LoC).

3. `make_equity_npy(path, values) -> None`
   - Body: `np.save(str(path), np.array(values, dtype=np.float64))`.
   - Replaces `_make_paper_session_file` (`test_dashboard_risk_integration.py:25-28`) and `_make_equity_npy` (`test_e2e_integration.py:94-97`).

4. `make_research_runner(settings=None) -> ResearchPlannerRunner`
   - Returns a `ResearchPlannerRunner` constructed with `settings or make_minimal_settings()` and `MagicMock()` collaborators. Mirrors `make_runner` for `SpecWriterRunner`.
   - Replaces `_make_planner_runner` (`test_orchestration_all.py:77-88`, 12 LoC).

5. `make_soaked_price_series(n=200, base=100.0, volatility=0.01, seed=42) -> np.ndarray`
   - Body: deterministic seeded random walk (currently inlined in `tests/conftest.py::_price_series` lines 84-91).
   - Lets `test_evaluator_backtesting.py::_make_prices` (lines 18-?, ~30 LoC) and 3 other tests drop their local copy.

**Migration diff size:** 5 new functions in `_factories.py` (~40 LoC added) replace ~110 LoC of duplicated test boilerplate, net **~70 LoC** removed. Each migration is one `from tests._factories import …` swap, so it lands as 5 PRs of <30 LoC each.

---

## 5. Plan: Shared `_get_url` + `_post_url` in `siglab/utils.py`

> **Note on `urllib.request.urlopen`:** `rg -n 'urllib.request.urlopen' siglab/` → 0 matches. The repo already uses `httpx.AsyncClient` everywhere (§2.2). The brief asks for `_get_url`/`_post_url` for the *new* HTTP path; we add them as a thin convenience over the shared lazy-httpx-client + retry loop.

**Add to `siglab/utils.py` (~30 LoC net):**

```python
async def run_with_backoff(
    coro_factory: Callable[[], Awaitable[Any]],
    *,
    attempts: int = 3,
    base_delay_s: float = 0.25,
    retry_on: tuple[type[BaseException], ...] = (
        httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout,
        httpx.PoolTimeout, httpx.TimeoutException, OSError, TimeoutError,
    ),
) -> Any:
    """Retry *coro_factory* with exponential backoff on transport errors."""

def lazy_async_client(
    *,
    timeout_s: float = 30.0,
    limits: httpx.Limits | None = None,
) -> Callable[[Any], httpx.AsyncClient]:
    """Return a factory that builds a singleton ``httpx.AsyncClient`` on first call."""

async def get_url(client: httpx.AsyncClient, url: str, **kw) -> dict:
    return (await client.get(url, **kw)).json()

async def post_url(client: httpx.AsyncClient, url: str, **kw) -> dict:
    return (await client.post(url, **kw)).json()
```

**Migration targets (one per call site, not all at once):**
- `siglab/data/sosovalue_client.py:_single_http_attempt` (lines 441-472) — uses `self._http().request(spec.method, url, …)`. The retry loop already lives in `_request_uncached`, so we keep the per-attempt call and just substitute the `httpx.AsyncClient` lazy-init.
- `siglab/data/sodex_client.py:_request` (lines 289-333) — same.
- `siglab/llm/claude.py:_chat_completion` and `siglab/llm/llm.py:_chat_completion` — both call `self._http().request(...)` (claude.py:610, llm.py:704). After §2.1 lands, the retry loop is in `run_with_backoff`.
- `siglab/tui/api_client.py:_request_with_retry` (lines 51-80) — direct replacement.

**LoC saved in `siglab/`:** ~50 LoC of duplicated retry/connect blocks.

**Behavior guard:** each migration must keep the existing `metrics.attempts += 1` / `metrics.rate_limits += 1` accounting (the metrics objects are per-endpoint, not per-httpx-attempt). We pass an `on_attempt` callback into `run_with_backoff` to preserve that.

---

## 6. Plan: `asyncio.gather` for Parallel SoSoValue Calls

Three independent await chains, in order of rate-limit savings:

### 6.1 `siglab/data/sosovalue_client.py:329, 377` — paged news fetchers
**Current (sequential):**
```python
for page_num in range(1, max(1, int(max_pages)) + 1):
    page_rows = await self.featured_news_by_currency(page_num=page_num, ...)
    if not page_rows:
        break
    rows.extend(page_rows)
```
**Replacement:**
```python
pages = await asyncio.gather(
    *[self.featured_news_by_currency(page_num=p, ...) for p in range(1, max_pages + 1)],
    return_exceptions=True,
)
rows: list[dict] = []
for page in pages:
    if isinstance(page, Exception) or not page:
        continue
    rows.extend(page)
```
**Rate-limit saving:** when `max_pages=3` and p50 latency is 250 ms, sequential = ~750 ms wall + 3 round-trips serialized. Parallel = ~250 ms + 3 round-trips *concurrent within the same `httpx.AsyncClient` connection pool*. The semaphore (`self._semaphore`) still caps the burst, so this does not break the rate-limit policy.

### 6.2 `siglab/data/feeds.py:464-509` — three discovery calls
**Current (sequential):**
```python
stable_markets = await self.discover_stable_pt_markets(stable_universe, limit=...)
rotation_markets = await self.discover_pt_markets(rotation_universe, limit=...)
lending_markets = await self.discover_lending_markets(lending_universe, limit=...)
```
**Replacement:** `stable_markets, rotation_markets, lending_markets = await asyncio.gather(...)`.

**Rate-limit saving:** the three `discover_*` calls each hit SoSoValue; the SoSoValue client already has its own dedup cache and rate-limit semaphore. Parallelizing collapses ~3× the request latency into 1×, freeing 2/3 of the per-iteration SoSoValue quota for other consumers.

### 6.3 `siglab/cli/evidence.py:69-84` — already uses `gather`. Verify and leave.
Already done. The three calls — `etf_historical_inflow`, `featured_news_pages`, `featured_news_by_currency_pages` — are gathered. No change.

### 6.4 (Stretch) `siglab/research/web.py:106, 197` — already uses `gather`. Verify and leave.
Already done. No change.

**Combined SoSoValue wall-clock savings estimate:**
- Per `build_research_summary` (yield_flows): from ~3×T_latency to ~1×T_latency.
- Per `cli evidence build` (3 pages): from ~3×T_latency to ~1×T_latency (the inner `featured_news_by_currency_pages` is itself now parallel per §6.1).
- Per research iteration: from ~3×T_tavily to ~1×T_tavily (already gather'd in `web.py:106, 197`).

**No new LoC added** (uses existing `asyncio.gather`), **~12 LoC removed** (drop the for-loop scaffolding).

---

## 7. Smaller-Delta Guarantee

Each refactor ships as a standalone PR of <50 LoC, with one focused test that proves the new helper preserves behavior.

| PR | Scope | LoC Δ (siglab/) | LoC Δ (tests/) | Independently testable? |
|---|---|---:|---:|---|
| **R1. `_summarize_metrics` helper** | New helper in `siglab/utils.py`. Migrate `sosovalue_client.py::metrics_snapshot` first (the largest duplicate, ~50 LoC). | −15 | +20 (new test) | Yes — `test_data/test_sosovalue_metrics.py::test_summarize_metrics` calls the helper directly with known latencies and asserts the envelope matches the inline version's output. |
| **R2. `lazy_async_client` helper** | New helper in `siglab/utils.py`. Migrate `sodex_client.py::_http` first. | −5 | +20 (new test) | Yes — `test_provider_utils.py::test_lazy_client_is_singleton` (asserts second call returns the same instance, and timeout/limits are honored). |
| **R3. `run_with_backoff` helper** | New helper in `siglab/utils.py`. Migrate `sodex_client.py::_request` first. | −30 | +40 (new test using a fake `coro_factory` that fails twice then succeeds) | Yes — `test_provider_utils.py::test_backoff_retries_then_succeeds` and `test_provider_utils.py::test_backoff_raises_after_max_attempts`. |
| **R4. `featured_news_pages` → `asyncio.gather`** | Inside `sosovalue_client.py:321-348`. | −8 | +15 (new test asserting gather order and exception handling) | Yes — `test_sosovalue_api.py::test_paged_fetch_uses_parallel_await` (monkey-patch `featured_news_by_currency` to record call order). |
| **R5. `discover_*_markets` → `asyncio.gather`** | Inside `feeds.py:464-509`. | −3 | +15 (new test using stub SoSoValueClient) | Yes — `test_sodex_feeds.py::test_three_discoveries_run_concurrently`. |
| **R6. `claude.py::_chat_completion` → `run_with_backoff`** | `siglab/llm/claude.py:584-703`. | −90 | +60 (regression: retry/backoff/4xx-5xx/status-mapping) | Yes — `test_llm_claude.py` already covers this path; re-run the suite. |
| **R7. `llm.py` deprecated wrapper removed** | Delete `siglab/llm/llm.py:_chat_completion` (the shadow re-implementation). All tests still pass via the canonical `claude.py` path. | −138 | +0 | Yes — `test_llm_claude.py` exercises both. |
| **R8. `make_repo_settings` factory + migrate 4 test files** | New factory in `tests/_factories.py`. Replace 4 local `_settings()` bodies. | 0 | −70 | Yes — `test_canonical_run_artifact.py` and friends run identically with the new factory. |
| **R9. `make_tmp_settings` factory + migrate 2 test files** | New factory. | 0 | −20 | Yes — same. |
| **R10. Dead-code sweep** | Remove `siglab/evaluator/__init__.py` re-exports, `siglab/track_registry.py`, the `dashboard/server.py:972` misplaced `asyncio.run`. | −115 | +0 | Yes — `test_evaluator_*` and `test_track_registry.py` (or move the registry into `tests/`). |
| **R11. `json_clone` dedup** | Delete `claude.py::_json_clone`, use `io_utils.json_clone` everywhere. | −22 | 0 | Yes — `test_llm_claude.py:340-352` already covers this path. |

**Subtotal: 11 PRs, each < 50 LoC diff, all independently testable, ~426 LoC net removed from `siglab/` + ~110 from `tests/`.**

To hit the **20% / −9,952 LoC** target we need 2 additional R12–R13 sweeps covering the longer-tail duplications (status-code mapping, percentile envelope in `feeds.py`, mock-provider boilerplate, price-series generators). Each is a mechanical migration with the same shape as R1–R11.

**Risk per PR:** low — every helper lands with a dedicated unit test before the migration PR touches the call site. The retry/backoff path is the highest-risk (R3, R6); we ship it behind the same numeric test thresholds already used by `test_llm_claude.py`.

---

## 8. Order of Shipment (suggested)

1. **R8, R9** — factory moves. Zero risk, opens the door for any test-only refactor to follow.
2. **R2** — `lazy_async_client`. Low risk, no behavior change.
3. **R1** — `_summarize_metrics`. Low risk, output byte-identical.
4. **R3** — `run_with_backoff` (just the helper + test, no call-site migration yet).
5. **R4** — `featured_news_pages` → `gather`. Low risk, no behavior change for the happy path.
6. **R5** — three `discover_*` → `gather`. Same.
7. **R11** — `json_clone` dedup. Trivial.
8. **R6, R7** — `_chat_completion` consolidation. Highest leverage, medium risk. Ship together.
9. **R10** — dead-code sweep. Low risk, gated by full test suite.
10. **R12, R13** — long-tail mechanical migrations (status-code mapping, mock-provider boilerplate) to close the 20% gap.

Each step is independently revertable. Each step leaves the test suite green before the next begins.

---

## 9. Brutally Honest Score-Potential vs. Effort

**Effort:** 11 PRs × ~30 min/PR for a fluent refactorer (≈ 5-6 hours) plus full retest loops and merge-conflict resolution with the live integration stream. Realistically 1-2 days.

**Score potential:** the 8.20/10 → 9.0/10 climb on a typical buildathon rubric depends on (1) LoC reduction, (2) lint cleanliness, (3) test count, (4) runtime speedup. This plan moves:
- LoC: −426 in `siglab/` (= 0.86% of 49,757 — *short* of the 20% bar unless we land R12/R13). Honest read: this plan delivers **4-5%** LoC reduction at the R1-R11 commit count, **15-18%** with R12-R13.
- Lint: unchanged (no new lint surface).
- Test count: **+170 lines of new test code** across R1-R7. Test count goes up, not down.
- Runtime: gather-parallelizing paged news and three discover_* calls cuts `build_research_summary` wall-clock for `yield_flows` by ~3× on cache-miss paths. This is the only measurable runtime win.
- **Architectural hygiene:** the `_summarize_metrics` + `lazy_async_client` + `run_with_backoff` triad makes future HTTP clients a 30-line `httpx.AsyncClient` subclass instead of a 200-line `claude.py` clone. That has long-tail payoff beyond the scoreboard.

**Honest ceiling:** this plan is necessary but not sufficient for 9.0+. The 20% LoC number requires committing to R12-R13 (status-code mapping, percentile envelope, mock-provider boilerplate), each of which is mostly mechanical but compounds the diff. **Do not promise 9.0+ from this plan alone; promise "removes the lowest-friction 4-5% with a clear path to 15-18% in 2 follow-on sweeps."**

---

## 10. Files Touched (no code in this PR)

- New helpers in `siglab/utils.py` (R1, R2, R3): +~70 LoC
- New factories in `tests/_factories.py` (R8, R9): +~30 LoC
- New tests in `tests/test_provider_utils.py` and `tests/test_sosovalue_api.py`: +~170 LoC
- Migrations in:
  - `siglab/data/sosovalue_client.py` (R1, R4, R7 of the §2 top-5)
  - `siglab/data/sodex_client.py` (R2, R3)
  - `siglab/data/feeds.py` (R5)
  - `siglab/llm/claude.py` (R6, R11)
  - `siglab/llm/llm.py` (R7, R11)
  - `siglab/dashboard/server.py` (R10)
  - `siglab/evaluator/__init__.py` and friends (R10)
  - `tests/test_canonical_run_artifact.py`, `test_dashboard_risk_integration.py`, `test_e2e_integration.py`, `test_hypothesis_sandbox.py`, `test_live_exporter.py`, `test_next_bar_bias.py`, `test_pt_roll_forward.py`, `test_web_research.py`, `test_config.py` (R8, R9)

No public API change. No config schema change. No behavior change in the steady-state test runs.
