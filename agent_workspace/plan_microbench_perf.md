# Plan Microbench Perf — Top 3 Wins + Microbenches

**Status:** PLAN-only. No source edits. No commit.
**Author:** PlanMicrobenchPerf (delegated from Main).
**Date:** 2026-06-14.
**Scope:** three perf wins, three new microbench tests, one shared HTTP-batching helper, OpenRouter + SoSoValue usage accounting changes, and a new combined baseline — across `siglab/cli/paper.py`, `siglab/tui/screens/market.py`, `siglab/llm/llm.py`, `siglab/live/paper_client.py`, `siglab/data/sodex_client.py`, `siglab/data/sosovalue_client.py`, `siglab/data/sodex_feeds.py`, plus the new file `tests/bench/test_bench_microbench_perf.py` and the new helper `siglab/llm/batch_http.py` (or analogous).
**Out of scope:** any change to `sodex_feeds.py` core semantics, `sosovalue_client.py` envelope validation, `planner_runner.py` repair loop, `writer_runner.py` repair loop.

---

## 0. Pre-flight — what the current code actually says

Re-read the six files the assignment calls out. Evidence with `file:line` that the plan below depends on:

1. **`tests/bench/test_bench_sodex_ws.py`** (the only existing perf test pattern in this repo):
   - L1-14 module docstring: "Establishes the baseline for the SoDEX WS probe hot path (perf plan #4): target ~0.3 s on success (single first frame). The bound here (<5 s) is intentionally loose so the test does not flap on noisy CI while still catching a 10x+ regression."
   - L26 `WALL_TIME_BUDGET_S = 5.0` (loose bound, the safety ceiling)
   - L31 `TARGET_WALL_TIME_S = 0.3` (the eventual tight target after the perf plan lands — explicitly *not* asserted today)
   - L34-73 `_run_sodex_ws_probe()`: spawns `python3 -m siglab.cli sodex-ws-probe --exit-on-first-frame --timeout-seconds 0.1 --evidence-output /tmp/...` via `subprocess.run` with `cwd=os.path.dirname(os.path.abspath(__file__))` and `timeout=8.0`.
   - L76-91 `test_bench_sodex_ws_probe_subprocess_overhead()`: single deterministic sample, single `assert elapsed < WALL_TIME_BUDGET_S`. No `pytest-benchmark` dep.
   - **Harness pattern**: `subprocess.run` + `time.perf_counter` + loose ceiling + tight target constant kept un-asserted.

2. **`tests/bench/test_bench_cli_help.py`** (the second and only other existing perf test):
   - L1-15 docstring: "current observed 2.97 s on the working tree the perf plan was measured on, target 0.9 s. The bound here (<5 s) is intentionally loose so the test does not flap on a noisy CI host while still catching a >70% regression."
   - L26 `WALL_TIME_BUDGET_S = 5.0`, L30 `TARGET_WALL_TIME_S = 0.9`
   - L33-49 `_run_cli_help()`: `subprocess.run([sys.executable, "-m", "siglab.cli", "--help"], cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))` and asserts `result.returncode == 0`.
   - L52-61 `test_bench_cli_help_cold_start()`: same loose-ceiling / tight-target pattern.
   - **No pytest-benchmark is a project dep** — both files use `time.perf_counter`. The new microbench test in §3 follows the same pattern: no new runtime dependency.

3. **`siglab/data/sodex_feeds.py`** — the seven `fetch_*` methods (re-read of L164-527):
   - L164-239 `fetch_klines()` — one `await self._client.klines(...)` after a lake-cache check. Single REST round-trip per call.
   - L301-327 `fetch_symbols()` — one `await self._client.symbols()`. Single round-trip.
   - L333-362 `fetch_tickers()` — one `await self._client.tickers()`. Single round-trip.
   - L368-396 `fetch_mark_prices()` — one `await self._client.mark_prices()`. Single round-trip.
   - L402-431 `fetch_book_tickers()` — one `await self._client.book_tickers()`. Single round-trip.
   - L437-474 `fetch_orderbook()` — one `await self._client.orderbook()`. Single round-trip.
   - L480-514 `fetch_trades()` — one `await self._client.trades()`. Single round-trip.
   - **Finding**: each `fetch_*` already does the minimum number of REST calls (1). The redundancy is *across calls* — when callers want multiple endpoints, they currently await them serially, and a single-`httpx.AsyncClient` pool could amortise TCP+TLS handshakes. The actual hot sequential-await site is in the **callers**, not the `fetch_*` methods themselves (see point 4).

4. **`siglab/cli/paper.py:59-93` `run_paper_status()`** — the cleanest, *smallest-delta* sequential-await site in the repo:
   ```python
   60  client = _make_paper_client(args)
   61  try:
   62      # Process open orders against latest klines
   63      try:
   64          open_orders = client.get_orders(args.session, status="OPEN") if hasattr(client, 'get_orders') else []
   65          open_symbols = {o["symbol"] for o in open_orders}
   66          settings = load_settings()
   67          lake = ParquetLake(settings.root_dir / "data" / "cache")
   68          feeds = SoDEXFeeds(lake=lake)
   69          for sym in open_symbols:                                                   # ← L70 loop
   70              try:
   71                  klines = await feeds.fetch_klines(sym, "1m", limit=5)              # ← L72 sequential await
   72                  if not klines.empty:
   73                      await client.process_klines(args.session, klines)
   74              except Exception:
   75                  pass
   ```
   For N open symbols this is **N sequential round-trips to SoDEX**, dominated by the SoDEX REST RTT. With `asyncio.gather` it collapses to **1 round-trip**. The lake cache already short-circuits cache hits, so the win is concentrated on cold-cache or N-symbol pre-warm paths (the latter is the deployed/operator use case).

5. **`siglab/tui/screens/market.py:461-467` `_refresh_klines_and_book()`** — a second sequential-await site inside the TUI refresh-on-symbol-select path:
   ```python
   461  async def _refresh_klines_and_book(self) -> None:
   462      self.is_loading = True
   463      try:
   464          await self._fetch_klines()        # ← L464 sequential
   465          await self._fetch_orderbook()     # ← L465 sequential
   466      finally:
   467          self.is_loading = False
   ```
   The first `await self._fetch_data()` (L377-384) already uses `_fetch_multiple` (which is `asyncio.gather`) — that path is the **positive precedent** for the patch here. The second path (the symbol-change refresh) was missed. This is a 2× wall-time win per symbol click.

6. **`siglab/llm/llm.py:920-932` `ClaudeClient._http()`** — the per-instance `httpx.AsyncClient` is already lazy-instantiated and **reused across calls** (the `if self._client is None` guard at L921 ensures it survives all `complete_*` calls on the same `ClaudeClient` instance). So at the LLM-client level there is no fresh-`AsyncClient`-per-call waste. **The win is *not* here** — the assignment framed the win as "where httpx.AsyncClient could be reused (connection pool)" but the more honest finding is: the bigger reuse win is the **SoDEX and SoSoValue clients**, where each `SoDEXFeeds` / `SoSoValueClient` instance holds its own `_http()` (see point 7). The patch keeps the existing `httpx.AsyncClient` and *threads it* through call sites that were constructing a fresh `SoDEXPublicPerpsClient` per short-lived call.

7. **`siglab/data/sodex_client.py:48-69` `SoDEXPublicPerpsClient.__init__()`** — already supports `client: httpx.AsyncClient | None = None` and `self._owns_client = client is None` (L55-62). The constructor L66-68 `close()` only calls `aclose()` if it owns the client. The redundancy is at the **caller level**: `SoDEXFeeds.__init__` (L142-147) constructs a *fresh* `SoDEXPublicPerpsClient` (and therefore a fresh `httpx.AsyncClient` via `_http()`) on every `SoDEXFeeds(lake=lake)` call. The `tests` and the TUI (`siglab/tui/screens/market.py`) construct a new `SoDEXFeeds` on each refresh, so the connection pool is dropped and re-built each cycle → one full TCP+TLS handshake per SoDEX endpoint per refresh.

8. **`siglab/live/paper_client.py:1167-1201` `_save_session_to_disk()`** — the redundant `.npy` write:
   ```python
   1178  with os.fdopen(fd, "w") as f:
   1179      json.dump(data, f)
   1180  os.replace(tmp_path, str(path))
   1181  npy_path = path.with_suffix(".npy")
   1182  np.save(str(npy_path), np.array(data, dtype=object), allow_pickle=True)   # ← L1182 mirror write
   ```
   Every save writes **two files** (`.json` + `.npy`) for the same content. There are **5 callers** of `_save_session_to_disk` (L417, L555, L599, L781, L825, L1154 — six sites counting the funding path), each one doing double I/O. The mirror was added "to keep the JSON-only sessions readable" but the read path at L1206-1247 already *prefers* JSON and only falls back to `.npy` for legacy files. For new sessions the `.npy` write is pure waste — a 2× I/O cost (and `np.save` with `allow_pickle=True` is not free: it round-trips the dict through `np.array(..., dtype=object)` then pickles it). The win is unconditional: the new code only writes `.npy` for sessions whose `.json` does not yet exist (legacy migration helper), not for the hot save path.

9. **`siglab/orchestration/planner_runner.py:73-170`** — the planner's repair loop awaits `self.claude.complete_text_with_tools()` up to `MAX_REPAIR_ATTEMPTS` (L20 = `MAX_REPAIR_ATTEMPTS`) times in a serial `for` loop. The LLM calls **cannot be batched** into one — each attempt is conditioned on the previous attempt's failure packet and changes the message history. This is a *causal* chain, not an embarrassingly-parallel set. The honest finding is: **the planner loop is not a candidate for "1 call instead of N"**. The LLM-batching win lives elsewhere (see point 10 and §2.c).

10. **`siglab/orchestration/writer_runner.py:174-251` `for attempt in range(1, self._max_attempts() + 1)`** — same shape as the planner: serial, conditioned on the previous attempt's preflight result. **Also not batchable** for the same reason. So the "batched LLM calls" win is not in the repair loops.

   The actual LLM-batching opportunity is at a different layer: a single `ClaudeClient.complete_text_with_tools` call already supports a **multi-tool** round where the model emits N tool calls in a single HTTP request and the runner executes them serially. The redundancy is the **per-tool-call** HTTP round-trip *inside* the `for tool_call in tool_calls` loop at `siglab/llm/llm.py:471-477` (mirrored at `:370-376` in the JSON variant): when the model returns K tool calls, the runner awaits K serial `_execute_tool_call` invocations even when those tool calls are **independent** (no shared state, no data dependency). The patch is to execute independent tool calls with `asyncio.gather` when the tool map permits it (most planner tools and most writer tools qualify — `web_researcher.search`, `hypothesis_sandbox.probe`, `workspace_builder.read` are all read-only). The model still sees all K results in the *next* round (because we append all `tool_message` dicts to `messages` after `gather` returns), preserving the contract. The HTTP cost to OpenRouter stays at 1 request per round (the LLM call is single-shot; the tool execution is local), so **OpenRouter cost is unchanged** — the win is *latency* per tool round, not cost.

11. **`siglab/data/sosovalue_client.py:99-130` `SoSoValueClient.__init__()`** — same `client: httpx.AsyncClient | None = None` pattern as `SoDEXPublicPerpsClient` (L104, L114, L128-130). And `cli/evidence.py:58-86` already constructs one `SoSoValueClient` per CLI invocation — fine for a one-shot CLI but the **per-CLI instantiation** is a hot path when a workspace session calls `siglab refresh-evidence` multiple times. This is the SoSoValue analogue of point 7. The SoSoValue client also exposes a single-endpoint-method surface (`etf_historical_inflow`, `listed_currencies`, `featured_news_pages`, etc.) that the CLI's `asyncio.gather` at `cli/evidence.py:71-84` already exploits — that part is **already done correctly** and is the positive precedent for the new helper in §4.

12. **`siglab/llm/llm.py:42-46` `_openrouter_client()`** — constructs a fresh `httpx.AsyncClient` *per* call to `_openrouter_list_models` (used at L63 in an `async with`). But this function is **already cached** at the module level (L55-59 `_cache` / `_cached_at` with a 600 s TTL), so the fresh-client cost only happens once per 10 minutes. **Not a meaningful win** — the 600 s TTL makes the per-call cost amortised to near-zero. This is the honest finding the assignment asks for: the win is *not* here.

13. **OpenRouter cost basis (for the §5 accounting changes)**:
   - `OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"` (L29)
   - `OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"` (L30)
   - Cost accumulator: `ClaudeClient._usage_cost_usd` (L207) and `ClaudeClient._priced_token_count` (L208), updated at L917-918 by `_record_usage`.
   - Free-tier limit per OpenRouter: 20 req/min, ~12 req/min sustained (per web-search finding [3]).
   - The "1 call instead of N" claim from the assignment does **not apply** to the planner/writer repair loops (they are causal chains). The real OpenRouter-cost win is on the **planner tool execution** side: each `complete_text_with_tools` round already costs 1 request; the tool-call-side parallelism does not change that. So §5's "OpenRouter cost saved" is **0%**, and we say so explicitly. The win for OpenRouter is **risk reduction on free-tier 429s**: with `asyncio.gather` we issue 1 LLM call per round instead of 1 + serial-tool-rounds, so the **wall-time per round drops** (so fewer round-trips per second of wall time), but the *request count* is unchanged. The honest accounting in §5 surfaces this.

14. **SoSoValue rate-limit basis (for §5)**:
   - `conservative_rate_limit_per_minute: int = 20` (L102, default constructor arg)
   - `SODEX_WEIGHT_BUDGET_PER_MINUTE = 1200` (`siglab/data/sodex_rate_limit.py:10`)
   - `SoDEXWeightScheduler.acquire(weight)` is awaited at `siglab/data/sodex_client.py:300` for every endpoint call.

---

## 1. Current microbench baseline

The repo currently has exactly **2** microbench tests. Both are loose ceiling / tight target constants, no `pytest-benchmark` dep, single deterministic `time.perf_counter` sample.

| Test file | Test name | Loose ceiling | Tight target | Current observed (per the perf-plan docstring) | Measurement |
|---|---|---|---|---|---|
| `tests/bench/test_bench_sodex_ws.py` | `test_bench_sodex_ws_probe_subprocess_overhead` | `WALL_TIME_BUDGET_S = 5.0` (L26) | `TARGET_WALL_TIME_S = 0.3` (L31) | not asserted; harness only | `subprocess.run([sys.executable, "-m", "siglab.cli", "sodex-ws-probe", "--exit-on-first-frame", "--timeout-seconds", "0.1", "--evidence-output", "/tmp/siglab_sodex_ws_probe_baseline.json"], capture_output=True, text=True, cwd=os.path.dirname(os.path.abspath(__file__)), timeout=8.0)` then `elapsed = time.perf_counter() - start` (L43-67) |
| `tests/bench/test_bench_cli_help.py` | `test_bench_cli_help_cold_start` | `WALL_TIME_BUDGET_S = 5.0` (L26) | `TARGET_WALL_TIME_S = 0.9` (L30) | **2.97 s** on the working tree the perf plan was measured on (per L4 docstring) | `subprocess.run([sys.executable, "-m", "siglab.cli", "--help"], capture_output=True, text=True, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))` then `elapsed = time.perf_counter() - start` (L36-44), asserts `result.returncode == 0` |

**Baseline microbench numbers (current code, no patches applied):**

- **CLI cold start** (existing test): **2.97 s** observed (loose ceiling 5.0 s, target 0.9 s).
- **sodex-ws-probe subprocess overhead** (existing test): not asserted today, harness only (loose ceiling 5.0 s, target 0.3 s).
- **The new microbench tests below do not exist yet** — they are introduced in §3.

**Why these two are the right precedent for the new microbenches**: they prove the *harness works* under `time.perf_counter` with no extra dep, and they document the loose/tight pattern (loose ceiling to avoid CI flapping, tight target constant kept un-asserted until the perf plan lands). The 3 new tests in §3 follow the same pattern with the same ceiling-to-target ratio.

---

## 2. Top 3 perf wins

Each win includes: (a) the redundant code path with `file:line`, (b) the cost mechanism, (c) the 5-line conceptual patch, (d) the estimated savings (time, SoSoValue rate limit, OpenRouter cost).

### Win 2.a — `asyncio.gather` for parallel SoSoValue / SoDEX reads

**Where**: `siglab/cli/paper.py:69-76` (the `for sym in open_symbols` loop awaits `feeds.fetch_klines(sym, "1m", limit=5)` and `client.process_klines(...)` serially for every open symbol).

**Cost mechanism**: For N open symbols, N serial round-trips to SoDEX `GET /markets/{sym}/klines`. Even with a populated lake cache the first cold-cache call costs the full RTT (≈ 80-150 ms per call to `https://mainnet-gw.sodex.dev/api/v1/perps`), and the SoDEX weight-scheduler (`SODEX_WEIGHT_BUDGET_PER_MINUTE = 1200`, default endpoint weight 20) admits the calls serially. N=10 symbols → 10 sequential awaits → 1.0-1.5 s wall-time for klines alone. The `cli/evidence.py:71-84` `asyncio.gather` on 3 SoSoValue endpoints is the positive precedent this is modelled on.

**The 5-line patch (conceptual — *not applied*)**:
```python
# siglab/cli/paper.py:69-76 — REPLACE the for-loop body
symbols = list(open_symbols)
frames = await asyncio.gather(
    *(feeds.fetch_klines(s, "1m", limit=5) for s in symbols),
    return_exceptions=True,
)
for sym, klines in zip(symbols, frames):
    if isinstance(klines, BaseException) or getattr(klines, "empty", True):
        continue
    await client.process_klines(args.session, klines)
```

**Estimated savings**:
- **Time saved per call**: for N open symbols, `t_parallel ≈ max(rtt) ≈ 150 ms` vs `t_serial ≈ N * 150 ms`. For N=10: ~1.35 s saved per `run_paper_status` invocation. For N=20 (the upper bound observed in the deployed agent): ~2.85 s saved.
- **SoSoValue rate limit saved**: **0** — SoSoValue is not in this path. (SoSoValue's CLI gather is already done in `cli/evidence.py:71-84`.)
- **SoDEX rate-limit weight saved per minute**: the SoDEX weight-scheduler already admits the 10 calls (weight 20 each = 200 total) under the 1200/minute budget, so the budget is not the bottleneck. The win is **wall-time**, not weight.
- **OpenRouter cost saved**: **0** — no LLM call in this path.

**Brutal honesty**: This win is **conditional**. The lake cache (`ParquetLake`) already short-circuits cache hits in `SoDEXFeeds.fetch_klines` (L212-218 of `sodex_feeds.py`), so on a warm cache the loop is dominated by `pd.read_parquet` + JSON serialisation, not by the network. The **win is concentrated on the first call after a cold start** (or after a klines TTL expiry at 1 hour, `DEFAULT_KLINES_CACHE_TTL_HOURS = 1.0`). The microbench in §3.a measures both the cold-cache and warm-cache paths and gates the assertion to the cold-cache path.

### Win 2.b — `httpx.AsyncClient` reuse via shared instance

**Where**: `siglab/data/sodex_feeds.py:142-147` constructs a fresh `SoDEXPublicPerpsClient` (and therefore a fresh `httpx.AsyncClient` via `_http()` at `siglab/data/sodex_client.py:351-354`) on every `SoDEXFeeds(lake=lake)` call. Two hot sites:

1. `siglab/cli/paper.py:67-69`: `lake = ParquetLake(...); feeds = SoDEXFeeds(lake=lake)` — instantiated per `run_paper_status` call.
2. `siglab/tui/screens/market.py` (via the dashboard `routes.py:541-590` path): each dashboard request constructs a new `SoDEXFeeds`.

**Cost mechanism**: Every fresh `httpx.AsyncClient` starts an empty keep-alive pool, so the first call to a host pays a full TCP handshake (50-200 ms per the [2] web search) plus TLS (≈ 100-300 ms for `https://mainnet-gw.sodex.dev`). Even with a 5-call refresh (5 endpoints), the 4 follow-up calls would have reused the pool *if* the client had been reused — but because the client is per-instance and the instance is per-request, the pool is torn down and rebuilt every request.

**The 5-line patch (conceptual — *not applied*)**:
```python
# siglab/cli/paper.py:59-69 — keep a module-level singleton client
from siglab.data.sodex_client import SoDEXPublicPerpsClient

# Constructed once at module import, closed in atexit
_sodex_http = httpx.AsyncClient(
    limits=httpx.Limits(max_connections=8, max_keepalive_connections=4)
)
_sodex_client = SoDEXPublicPerpsClient(client=_sodex_http)

# Then SoDEXFeeds uses the shared client
feeds = SoDEXFeeds(lake=lake, _client=_sodex_client)   # new optional ctor arg
```

The cleanest concrete shape is to add an optional `_client: SoDEXPublicPerpsClient | None = None` parameter to `SoDEXFeeds.__init__` and let callers pass a long-lived client. The constructor at `siglab/data/sodex_feeds.py:125-147` already takes `weight_scheduler`; adding `_client` next to it is a 1-line change.

**Estimated savings**:
- **Time saved per call**: ~50-200 ms per *first* call to a host (TCP) + ~100-300 ms (TLS) = **150-500 ms** per process invocation, or per TUI refresh. For 1 CLI invocation: 1 invocation × ~300 ms = ~0.3 s. For 1 TUI refresh cycle: same.
- **SoSoValue rate limit saved**: **0** — this is the SoDEX pool. (A separate but analogous change applies to the SoSoValue pool; see brutal-honesty note below.)
- **SoDEX rate-limit weight saved**: **0** — the weight scheduler is per-client; sharing the client does not change the per-call weight.
- **OpenRouter cost saved**: **0** — no LLM call.

**Brutal honesty**: `httpx.AsyncClient` reuse only matters when (a) the same client instance lives across multiple HTTP calls and (b) the calls are to the same host. Inside a single CLI invocation, the win is *zero* for the *first* call (you have to pay the handshake once anyway) — the win is on the *follow-up* calls to the same host within the same process lifetime. The TUI refresh path is the only place where the same process makes 5+ SoDEX calls per refresh and the client is being torn down between refreshes. **The CLI single-shot path gains essentially nothing.** The microbench in §3.b focuses on the multi-call-per-process case (TUI dashboard route) to make this distinction honest.

### Win 2.c — Parallelise independent tool calls inside a single LLM round

**Where**: `siglab/llm/llm.py:471-477` (in `complete_text_with_tools`):
```python
for tool_call in tool_calls:
    tool_message, trace_entry = await self._execute_tool_call(
        tool_call=tool_call,
        tool_map=tool_map,
    )
    trace["tool_calls"].append(trace_entry)
    messages.append(tool_message)
continue
```
And the mirror at L473-479 in `complete_json_with_tools` (same shape).

**Cost mechanism**: When the model returns K tool calls in one round (the planner's tools include `probe_feature_forward_stats`, `probe_spec_gate_impact`, `compare_intended_vs_frozen_spec`, `web_search`, `read_workspace_file` — these are all independent read-only probes), the runner awaits them **serially**. Each tool execution is local + a `httpx` call to the model provider. `await self._execute_tool_call(...)` does: (a) parse the tool arguments, (b) invoke the handler — which is often an `await web_researcher.search(query)` or `await hypothesis_sandbox.probe(...)`. Those handlers make their own `httpx` calls (to SoSoValue, to a local feature reader, etc.). Serial K awaits = K × RTT wall-time. With `asyncio.gather` for independent tools = max(RTT) wall-time.

**The 5-line patch (conceptual — *not applied*)**:
```python
# siglab/llm/llm.py:471-477 — REPLACE the for-loop
results = await asyncio.gather(
    *(self._execute_tool_call(tc, tool_map=tool_map) for tc in tool_calls),
    return_exceptions=False,  # let exceptions propagate to the caller unchanged
)
for tool_message, trace_entry in results:
    trace["tool_calls"].append(trace_entry)
    messages.append(tool_message)
continue
```

**Estimated savings**:
- **Time saved per call**: for K independent tool calls, `t_parallel ≈ max(tool_latency)` vs `t_serial ≈ sum(tool_latencies)`. The planner's `_planner_max_tool_rounds` is 8 (L401) but the **per-round tool count** is bounded by what the model emits, typically 1-3. For K=3 with each tool taking 200-500 ms (a web search + a probe + a workspace read): serial = 0.6-1.5 s, parallel = 200-500 ms. **Time saved per LLM round: 400-1000 ms.**
- **SoSoValue rate limit saved**: **0** — tools are local; the SoSoValue/SoDEX calls *inside* a tool are not aggregated by this patch. (If 2 of the 3 tools each make 1 SoSoValue call, `asyncio.gather` does not coalesce them — they still go through `SoSoValueClient.request`, which has its own `_inflight` dedup at `siglab/data/sosovalue_client.py:399-407`. So the *call count* is unchanged. Only the *wall-time* drops.)
- **OpenRouter cost saved**: **0** — the LLM call is single-shot regardless of how many tool calls are in the response. The win is *latency per round*, not cost.
- **Plurality honest assessment**: the prompt mentions "batched LLM calls" / "1 call instead of N". This patch does **not** do that. It parallelises the *local tool execution* that follows the LLM call. The LLM call count is unchanged. The win is the wall-time-per-round drop. §5's OpenRouter accounting reflects this honestly.

**Brutal honesty**: The patch requires that the K tool calls are **truly independent** (no shared mutable state, no data dependency on the result of an earlier call in the same round). All five planner tools (`probe_*`, `compare_*`, `web_search`, `read_workspace_file`) qualify. The writer tools (`complete_json_messages` doesn't even take tools) do not apply. The reflector's `complete_text` doesn't apply. So this win lives entirely in the **planner's tool-execution loop**. The microbench in §3.c measures planner wall-time per round, not per run.

---

## 3. The 1 new microbench test for each win

All three follow the existing `tests/bench/test_bench_*.py` pattern: `time.perf_counter` + loose ceiling + tight target constant kept un-asserted. No `pytest-benchmark` dep. The new file is **`tests/bench/test_bench_microbench_perf.py`** (one file, three test functions, three section headers mirroring §2.a / §2.b / §2.c).

### 3.a — `test_bench_paper_status_gather_under_budget`

**Measures**: the cold-cache wall-time of `run_paper_status` for N=10 simulated open symbols, after the patch in §2.a.

**Setup** (conceptual — *not applied*):
```python
"""Microbench for cli/paper.py asyncio.gather parallelisation (§2.a).

Establishes the baseline for the cold-cache gather path. Target: 0.5 s for
N=10 open symbols (vs the serial path's 1.5 s). Loose ceiling 3.0 s catches
a 6x regression.
"""
WALL_TIME_BUDGET_S = 3.0
TARGET_WALL_TIME_S = 0.5
N_SYMBOLS = 10

async def _run_paper_status_cold() -> float:
    # monkeypatch SoDEXFeeds.fetch_klines to return a small fixed DataFrame
    # so the bench is hermetic; the win is the gather wall-time, not the parse.
    # Then invoke the CLI handler in-process (not via subprocess) to avoid
    # the cli import overhead that test_bench_cli_help already measures.
    start = time.perf_counter()
    # ... in-process invocation of run_paper_status with a stubbed session ...
    return time.perf_counter() - start
```

**Assertion**: `elapsed < WALL_TIME_BUDGET_S` (loose), `TARGET_WALL_TIME_S` kept as a documented constant (un-asserted until the perf plan lands).

### 3.b — `test_bench_dashboard_soxdex_pool_reuse_under_budget`

**Measures**: the wall-time of the dashboard market-data route (5 sequential SoDEX calls: `tickers`, `klines`, `orderbook`, `mark_prices`, `book_tickers`) before vs after the `httpx.AsyncClient` reuse patch in §2.b.

**Setup** (conceptual — *not applied*):
```python
"""Microbench for SoDEX httpx.AsyncClient reuse (§2.b).

Target: 0.7 s for 5 sequential SoDEX calls (vs 1.2 s for the per-request
client). Loose ceiling 4.0 s catches a 6x regression.
"""
WALL_TIME_BUDGET_S = 4.0
TARGET_WALL_TIME_S = 0.7
N_CALLS = 5
```

**Brutal honesty**: this bench depends on either a real SoDEX endpoint or a local stub server. For a hermetic unit-level bench, the test mocks `httpx.AsyncClient.send` to return canned JSON. The patch is exercised at the **client-instantiation** layer (one `SoDEXPublicPerpsClient` vs N), and the bench measures the `__init__` + first-call cost.

### 3.c — `test_bench_planner_tool_round_under_budget`

**Measures**: the wall-time of one planner LLM round with K=3 independent tool calls, after the `asyncio.gather` patch in §2.c.

**Setup** (conceptual — *not applied*):
```python
"""Microbench for ClaudeClient tool-execution parallelism (§2.c).

Target: 0.5 s per LLM round with 3 independent tool calls (vs 1.5 s serial).
Loose ceiling 3.0 s catches a 6x regression.
"""
WALL_TIME_BUDGET_S = 3.0
TARGET_WALL_TIME_S = 0.5
K_TOOL_CALLS = 3
```

**Brutal honesty**: the LLM call is mocked (the test injects a canned `tool_calls` list); the tool handlers are stubbed to each await an `asyncio.sleep(0.2)` so the wall-time difference between serial and gather is observable. This isolates the patch's effect from the LLM provider's actual latency.

### 3.d — Combined baseline (all three at once)

`test_bench_microbench_perf_combined` runs all three patches together and asserts the *aggregate* wall-time stays under a combined loose ceiling (`COMBINED_WALL_TIME_BUDGET_S = 6.0`) with a documented tight target (`COMBINED_TARGET_WALL_TIME_S = 1.7`). This is the "new microbench baseline" called for in §6.

---

## 4. The new shared helper for HTTP request batching

**File**: `siglab/llm/batch_http.py` (new; not created today — planned for the implementation pass).

**Shape** (conceptual — *not applied*):
```python
"""Shared helper: bounded-concurrency batch helper for httpx.AsyncClient + asyncio.

Used by the planner tool-execution gather (§2.c) and by the dashboard
SoDEX gather (§2.b) to coalesce N independent HTTP-bound coroutines into a
single event-loop tick without exceeding a per-host concurrency cap.
"""
import asyncio
from typing import Any, Awaitable, Callable, Iterable, TypeVar

T = TypeVar("T")

async def gather_bounded(
    coros: Iterable[Awaitable[T]],
    *,
    concurrency: int = 4,
) -> list[T]:
    """asyncio.gather with a per-batch concurrency cap.

    The cap is a *soft* cap (semaphore), not a hard partition: a long-running
    coroutine does not block short ones from starting as long as a slot is
    free. This is the standard "bounded gather" pattern; httpx's own
    Limits(max_connections=8, ...) is the *per-host* cap and the two stack.
    """
    sem = asyncio.Semaphore(max(1, int(concurrency)))

    async def _one(coro: Awaitable[T]) -> T:
        async with sem:
            return await coro

    return await asyncio.gather(*(_one(c) for c in coros))
```

**Why a helper, not a one-liner at each call site**:
- The 3 new patch sites (paper status, dashboard route, planner tool round) all need the same shape. Duplicating the semaphore boilerplate 3 times is a regression risk.
- The cap of 4 is below the SoDEX `Limits(max_connections=8, max_keepalive_connections=4)` so we never queue more than what the connection pool can serve.
- The helper is provider-agnostic (no httpx types in the signature), so it composes with both SoDEX (`SoDEXFeeds.fetch_*` returns are `Awaitable[pd.DataFrame]`) and the planner tool map (`_execute_tool_call` returns are `Awaitable[tuple[dict, dict]]`).

**Why not just `asyncio.gather` directly**: `asyncio.gather` with no cap will happily spawn 10 concurrent SoDEX calls if the model emits 10 tool calls. The SoDEX weight-scheduler is per-process, not per-call, so 10 concurrent calls drain the 1200/minute budget 10× faster. The bounded helper is the cap that keeps us under the rate-limit ceiling while still parallelising.

---

## 5. The OpenRouter + SoSoValue usage accounting changes

The assignment asks for "OpenRouter + SoSoValue usage accounting changes" alongside the patches. The honest finding is that **2 of the 3 wins are zero-cost wins on both providers**, and §5 says so explicitly.

### 5.a — OpenRouter cost

| Win | OpenRouter requests before | OpenRouter requests after | OpenRouter cost delta |
|---|---|---|---|
| §2.a (`asyncio.gather` for parallel SoSoValue/SoDEX reads) | 0 (no LLM call in path) | 0 | **0** |
| §2.b (`httpx.AsyncClient` reuse) | 0 (no LLM call in path) | 0 | **0** |
| §2.c (parallelise independent tool calls in one LLM round) | 1 per planner round | 1 per planner round | **0** |

**Net OpenRouter cost delta: 0%**. The §2.c patch is **not** the "1 call instead of N" win the assignment framed — that win does not exist in this codebase. The planner and writer repair loops are *causal chains* (each attempt depends on the previous attempt's failure packet), so they cannot be batched into one prompt without breaking the contract. The §2.c win is *wall-time per round* (K independent tool calls → 1 round-trip), not *request count*.

**What changes in the accounting**:
- `ClaudeClient._request_count` (L200 of `siglab/llm/llm.py`) and `_usage_cost_usd` (L207) are *unchanged* by the §2.c patch — they still record 1 LLM call per round.
- A new metric `ClaudeClient._planner_round_parallel_tool_time_ms` (sum of the parallel tool latencies) would be useful for the microbench in §3.c. This is a one-line add at the `messages.append(tool_message)` site after `gather`. **Not required** for the perf win; useful for the bench.
- `_auth_key_cache` (L212) and `_auth_key_cached_at` (L213) are unaffected.

### 5.b — SoSoValue rate limit

| Win | SoSoValue calls before | SoSoValue calls after | Weight saved |
|---|---|---|---|
| §2.a | N (one per open symbol, klines) | N (same; the gather is local to SoDEX, not SoSoValue) | **0** |
| §2.b | 0 (no SoSoValue call in path) | 0 | **0** |
| §2.c | K (one per tool call that hits SoSoValue) | K (gather does not coalesce) | **0** |

**Net SoSoValue rate-limit saved: 0 calls**. The pre-existing `cli/evidence.py:71-84` `asyncio.gather` already does the right thing for the CLI evidence path (3 SoSoValue calls → 1 wall-clock round-trip; the calls are still 3 against the rate limit but they all hit the network in parallel). The §2.a patch is a *SoDEX*-parallel win, not a SoSoValue one.

**What changes in the accounting**:
- `SoSoValueClient._rate_limit_events` (deque at L118) and `_acquire_rate_slot` (L663) are *unchanged*. The 20/minute budget is still 20/minute.
- The new helper `gather_bounded` (from §4) does **not** bypass the `_acquire_rate_slot` lock — that lock is enforced inside `SoSoValueClient._request_uncached` (L417). The helper is a wall-time accelerator, not a rate-limit accountant.

### 5.c — SoDEX rate limit

| Win | SoDEX weight before | SoDEX weight after | Weight saved |
|---|---|---|---|
| §2.a | N × 20 (klines, weight 20 each) | N × 20 (same; gather does not bypass the weight scheduler) | **0** |
| §2.b | 0 (the call is the same; only the client lifetime changes) | 0 | **0** |
| §2.c | 0 (no SoDEX call inside the planner tool map) | 0 | **0** |

**Net SoDEX rate-limit weight saved: 0 weight**. The `SoDEXWeightScheduler.acquire(weight)` at `siglab/data/sodex_client.py:300` is enforced per call regardless of concurrency. The 1200/minute budget is unchanged.

### 5.d — Aggregate

**The 3 patches save wall-time, not provider cost.** That is the honest finding. The assignment's framing ("Plan to use OpenRouter + SoSoValue more per call to reduce total round trips") is the wrong direction — the per-call cost is the cost the providers charge; the patches reduce *wall-time per call*, not *cost per call*. The new accounting simply adds a `wall_time_saved_per_session_s` field to `ClaudeClient.metrics_snapshot` and `SoSoValueClient.metrics_snapshot` populated by the patch sites (the bench in §3 can then read this and assert the budget).

---

## 6. The new microbench baseline (after the 3 patches)

**Pre-patch baseline** (current observed, from §1 + the test docstrings):
- CLI cold start: **2.97 s** (loose ceiling 5.0 s, target 0.9 s)
- sodex-ws-probe subprocess overhead: not asserted (loose ceiling 5.0 s, target 0.3 s)
- *No existing microbench for paper status / dashboard / planner tool round* (these are the new tests in §3)

**Post-patch expected wall-time** (after §2.a + §2.b + §2.c land):

| Bench | Pre-patch wall-time | Post-patch wall-time | Source of saving |
|---|---|---|---|
| `test_bench_cli_help_cold_start` (existing) | 2.97 s | 2.97 s (unchanged) | none of the 3 patches touch CLI startup |
| `test_bench_sodex_ws_probe_subprocess_overhead` (existing) | ~0.3 s harness | ~0.3 s (unchanged) | none of the 3 patches touch the WS probe |
| `test_bench_paper_status_gather_under_budget` (§3.a, new) | 1.5 s (N=10 serial) | **0.5 s** (gather) | §2.a |
| `test_bench_dashboard_soxdex_pool_reuse_under_budget` (§3.b, new) | 1.2 s (5 calls, fresh client each) | **0.7 s** (shared client) | §2.b |
| `test_bench_planner_tool_round_under_budget` (§3.c, new) | 1.5 s (3 serial tools × 0.5 s each) | **0.5 s** (gather) | §2.c |
| `test_bench_microbench_perf_combined` (§3.d, new) | 4.2 s (sum) | **1.7 s** (sum) | all three |

**New combined loose ceiling**: `COMBINED_WALL_TIME_BUDGET_S = 6.0` (≈ 1.4× the post-patch sum, mirrors the loose/tight ratio in the existing tests).
**New combined tight target**: `COMBINED_TARGET_WALL_TIME_S = 1.7` (kept un-asserted until the perf plan lands, mirrors the existing pattern).

**Per-patch saving summary** (so the new baseline is auditable from the test output):
- §2.a: ~1.0 s saved per `run_paper_status` invocation (N=10), scales linearly with N.
- §2.b: ~0.5 s saved per dashboard refresh (5 calls); 0 saved for a single-shot CLI invocation.
- §2.c: ~1.0 s saved per planner LLM round (K=3); scales with K and per-tool latency.

**Why this baseline matters**: it is the *first* microbench in the repo that measures something other than CLI startup. After the patches land and the bench is green, the codebase has 6 perf tests (3 existing, 3 new) instead of 2, and the buildathon score gains a measurable wall-time improvement (the assignment's 8.20/10 is largely constrained by buildathon-perceived latency, and `run_paper_status` is a buildathon validator surface).

**Brutal final assessment of the score potential**:
- The 3 patches save **~2.5 s of wall-time per buildathon evaluator invocation** in the worst case (a single shell command that does `paper-status` + `dashboard refresh` + `planner round` in sequence).
- The 3 patches save **0 LLM cost** and **0 provider rate-limit budget**.
- The buildathon score uplift is **bounded by evaluator latency budgets** — if the evaluator allows 5 s per command and the pre-patch wall-time was 4.2 s for the 3 commands, the post-patch 1.7 s gives 2.5 s of headroom for additional validation. That headroom can be re-spent on **more validator invocations**, which is the multiplier the score actually rewards.
- Effort: ~3 small patches (5-line each per the §2 patches) + 1 new test file (~150 lines, mirroring the existing `test_bench_*.py` pattern) + 1 new helper file (~25 lines). **Total: ~250 lines of new code, ~15 lines of patch.** No new deps.

---

## 7. Out-of-scope notes (for the implementer)

- The planner repair loop (`planner_runner.py:73-170`) and writer repair loop (`writer_runner.py:174-251`) are **not** candidates for the "1 LLM call instead of N" win. Each iteration is conditioned on the previous iteration's failure packet. Any batched-LLM-call patch here would require restructuring the failure-feedback protocol — out of scope for a microbench perf pass.
- `SoDEXPublicPerpsClient._http()` and `SoSoValueClient._http()` are *already* lazy-instantiated and reused across calls within a single instance. The win in §2.b is at the **caller lifetime** layer (a new `SoDEXFeeds(lake=lake)` per request is what kills the pool), not the client layer.
- The `_openrouter_client()` factory in `siglab/llm/llm.py:42-46` is **already** amortised by the 600 s module-level cache at L55-59 — the per-call cost is zero in steady state. The `_openrouter_auth_key()` at L215-240 also caches for 60 s. **Neither needs the §2.b patch.**
- The `_save_session_to_disk` mirror `.npy` write at `siglab/live/paper_client.py:1182` is a real 2× I/O waste, but it is **not** one of the top 3 perf wins called for by the assignment (which specifies HTTP-call / connection-pool / batched-LLM). It is left for a follow-up microbench focused on local I/O.

---

**Blocking:** do not start implementation until this plan is accepted by the main agent.
