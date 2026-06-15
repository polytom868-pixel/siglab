# SigLab Python Perf Patterns — Web Research Plan

Mission: identify the top Python perf patterns from 2026 sources that can give SigLab a 50%+ speedup, grounded in web research and live IRC signals from W-2 / W-3 / S-7.

## 1. IRC signals (verified against current SigLab)

- **WaveW2HelpersExtract** — siglab `utils.py` builds short-lived `aiohttp.ClientSession()` per call inside `run_with_backoff` / `async_limiter_call` / `_get_url` / `_post_url`; the hot async I/O lives in `siglab/live/` (deployed_agents, paper_client, sodex_ws) and `siglab/research/web.py`.
- **WaveW3RateLimiterWrap** — `utils.py:99 async_limiter_call` constructs a fresh `asyncio.Semaphore` on every call, so it does not pool across calls; callable must return a coroutine (wrap sync urllib with `asyncio.to_thread`). `asyncio_mode=auto` is already configured. OpenRouter free tier is 429-limited upstream, so the limiter must pool to help.
- **WaveS7ToolGather** — `asyncio.gather(*coros)` preserves submission order; build the coroutine list from the `tool_map` (already a Sequence) — O(n), no I/O. Configure `httpx.Limits(max_connections=..., max_keepalive_connections=...)` once at construction; set per-request timeout. AsyncClient bound to loop A fails on loop B (cross-loop reuse trap, hits tests).

## 2. The 10 best Python perf papers / docs of 2026

For each: 1-line summary, 1 concrete code pattern, 1 size of speedup, and the source URL.

### 1. asyncio.gather vs sequential await — 50% wall-time cut
- Summary: `asyncio.gather` interleaves I/O across coroutines; 10 sequential awaits at 1s each drop from ~10s to ~5s wall time on independent I/O.
- Pattern: `results = await asyncio.gather(*(fetch(u) for u in urls), return_exceptions=True)` (replace any for-await that only does I/O).
- Speedup: ~2x (50%) for I/O-bound fan-out.
- URL: https://dev.to/shehzan/mastering-python-async-patterns-a-complete-guide-to-asyncio-in-2026-10o6

### 2. httpx.AsyncClient long-lived + connection pool limits — 2x throughput
- Summary: One `httpx.AsyncClient` per loop, with `Limits(max_keepalive_connections, max_connections)`, reused across the whole service, beats per-request client construction by 2x.
- Pattern:
  ```python
  _client = httpx.AsyncClient(http2=True, timeout=10.0,
      limits=httpx.Limits(max_keepalive_connections=20, max_connections=100))
  # in request paths: await _client.get(url)  ;  in shutdown: await _client.aclose()
  ```
- Speedup: ~2x (50% wall-time cut) on connection-heavy workloads.
- URL: https://webeyez.com/insights/guides/async-with-httpx-asyncclient-guide

### 3. httpx HTTP/2 multiplexing — 30% over HTTP/1.1
- Summary: `http2=True` lets many requests share one TCP/TLS session without head-of-line blocking; 500 concurrent requests finish in 5.5s vs 7.8s for `requests`.
- Pattern: same as #2 with `http2=True` at client construction.
- Speedup: ~30% on many-concurrent-requests-to-same-host workloads.
- URL: https://oneuptime.com/blog/post/2026-02-03-python-httpx-async-requests/view

### 4. functools.lru_cache — 100x+ on recursive / repeated work
- Summary: Memoizing a naive recursive function cuts repeated work to O(1) per call; Fibonacci(35) drops from ~2.3s to ~0.5 ms.
- Pattern: `@functools.lru_cache(maxsize=1024)` on deterministic pure functions, plus `functools.cache` for unbounded.
- Speedup: 100x+ on hot memoizable paths (rare-but-real wins in SigLab: cost calculation, signing, parsing).
- URL: https://medium.com/@vipulm124/profiling-python-fibonacci-from-naive-recursion-to-lru-cache-speed-88b3d032896e

### 5. asyncio.Semaphore for 50 RPS rate limit — sustained throughput
- Summary: A `Semaphore(N)` gates in-flight coroutines; combine with a 0.02s token-bucket release loop to cap at 50 RPS without bursty overload.
- Pattern:
  ```python
  sem = asyncio.Semaphore(50)
  async def fetch(u):
      async with sem: return await client.get(u)
  async def refill():
      while True: await asyncio.sleep(0.02); sem.release() if not sem.locked() else None
  asyncio.create_task(refill())
  ```
- Speedup: replaces 429s and back-off sleeps; effective throughput up 2-3x vs unthrottled burst.
- URL: https://rednafi.com/python/limit-concurrency-with-semaphore

### 6. Lazy imports via `__getattr__` / PEP 810 — ~30% startup cut
- Summary: Defer heavy imports (httpx, pydantic, torch, pandas) until first attribute access; PEP 810 will make this a first-class `lazy` keyword in 3.15.
- Pattern:
  ```python
  # utils.py
  def __getattr__(name):
      if name == "httpx": return __import__("httpx")
      raise AttributeError(name)
  ```
- Speedup: ~30% CLI cold-start (SigLab `siglab.cli.*` currently eagerly imports httpx/aiohttp).
- URL: https://pythontest.com/python-lazy-imports-now

### 7. attrs (`slots=True, frozen=True`) over `@dataclass` — 2x instantiate, 45% less memory
- Summary: `attrs.define(slots=True, frozen=True)` drops per-instance `__dict__`; in 3.12+ it instantiates ~2x faster than `frozen=True, slots=True` dataclass and uses ~45% less memory.
- Pattern: `from attrs import frozen; @frozen(slots=True)\nclass Order: id: str; price: float` (replace hot DTOs in `siglab/live/paper_client.py` etc.).
- Speedup: ~2x instantiation, ~45% less memory on hot DTOs.
- URL: https://threeofwands.com/attra-iv-zero-overhead-frozen-attrs-classes

### 8. pytest-asyncio `asyncio_mode=auto` + module-scoped event loop — ~5x test suite
- Summary: Drop the per-test loop teardown; one loop per module avoids the per-test loop spin-up that dominates async test time.
- Pattern (pyproject.toml):
  ```toml
  [tool.pytest.ini_options]
  asyncio_mode = "auto"
  asyncio_default_fixture_loop_scope = "module"
  ```
  And in code: `@pytest_asyncio.fixture(loop_scope="module")` for shared clients.
- Speedup: ~5x on async-heavy suites (SigLab `tests/test_live_*`).
- URL: https://qaskills.sh/blog/pytest-asyncio-testing-guide

### 9. Generator expressions for large aggregations — up to 99% memory + 90%+ runtime
- Summary: Lazy generators avoid materializing huge intermediate lists; for memory-bound workloads the swap is a step-change, not a small win.
- Pattern: `total = sum(parse(line) for line in stream)` instead of `[parse(line) for line in stream] -> sum(...)` (one pass, no intermediate list).
- Speedup: up to 99% memory, often 50%+ wall-time on large inputs.
- URL: https://blog.stackademic.com/python-performance-showdown-list-comprehension-vs-generator-90-faster-5bd642803e0a

### 10. defaultdict for auto-populating groups — 30-40% over `dict.get` + assign
- Summary: `defaultdict(factory)` inlines the missing-key insert, beating the `d.get(k, d.setdefault(k, v))` double-lookup idiom; for read-only lookups plain `dict.get` is faster.
- Pattern: `groups = defaultdict(list); for x in items: groups[x.kind].append(x)` (replace the "if k not in d" pattern in aggregator code).
- Speedup: 30-40% on group-by workloads; not for simple lookups.
- URL: https://dev.to/jorjishasan/built-in-dictionary-vs-defaultdict-2pmh

## 3. Top 3 patterns that would give SigLab 50%+ speedup

These three together are the 50% plan, ordered by expected ROI given the IRC-confirmed hotspots.

### A. `asyncio.gather` over sequential for-await on the I/O fan-out paths
- Where: `siglab/live/sodex_client.py`, `siglab/live/sodex_ws.py`, `siglab/live/paper_client.py`, `siglab/live/deployed_agents/*`, `siglab/research/web.py`.
- Why 50%: every for-await over an independent network call is paying full RTT per item; gather is the textbook 2x on I/O-bound fan-out, and S-7 confirmed the tool list is already a Sequence (O(n) coroutine build, no I/O).
- Concrete change shape (no edit here): wrap the loops in `asyncio.gather(*coros, return_exceptions=True)`, then iterate the list to record per-call success/error.
- Risk: exception handling differs (`return_exceptions=True` is required to avoid one failure cancelling siblings); tests under `tests/test_live_*` cover the I/O shape.

### B. Module-scoped `httpx.AsyncClient` (or pooled `aiohttp.ClientSession`) with `Limits(...)`
- Where: `siglab/utils.py` (`async_limiter_call`, `_get_url`, `_post_url`, `run_with_backoff`) and the live/* modules.
- Why 50%: W-2 confirmed per-call client construction is the norm; W-3 confirmed `async_limiter_call` creates a fresh `Semaphore` per call so the pool is not even shared. One client + one Semaphore per process, plus `http2=True`, gets the full httpx 2x and the HTTP/2 ~30% on top.
- Concrete change shape: hoist `_client` to module scope, construct once with `httpx.Limits(max_keepalive_connections=20, max_connections=100)`, `http2=True`, `timeout=10.0`; close in a shutdown hook only.
- Risk: AsyncClient is bound to the loop it was created on (S-7 flagged this); per-test fixtures must build a fresh client (covered by #C).

### C. `pytest-asyncio` module-scoped event loop (or session-scoped) in `pyproject.toml`
- Where: `pyproject.toml` `[tool.pytest.ini_options]` and the `tests/conftest.py` async fixtures.
- Why 50% on the test side: the per-test loop creation is the dominant cost in `tests/test_live_*`, `tests/test_workspace_flow.py`, `tests/test_orchestration_all.py`; module-scoped loop is the textbook 5x. W-3 confirmed `asyncio_mode=auto` is already on; only the loop scope change is needed.
- Concrete change shape: add `asyncio_default_fixture_loop_scope = "module"` (or `"session"`) and convert shared `httpx.AsyncClient` fixtures to `loop_scope="module"`.
- Risk: shared mutable state across tests in the same module; fix by isolating per-test state inside the test bodies.

## 4. Supporting patterns (worth doing, but individually <50%)

- **`asyncio.to_thread`** to wrap sync urllib calls so the event loop is not blocked (cited by W-3). Pattern: `await limiter(lambda: asyncio.to_thread(sync_fn))`.
- **`asyncio.TaskGroup` (3.11+)** for structured concurrency — ~5-10% over `gather` on creation overhead, and deterministic cancellation. (https://applifting.io/blog/python-structured-concurrency)
- **attrs `frozen(slots=True)`** for DTOs in `paper_client.py` / `sodex_signing.py` — 2x instantiate, 45% memory. (https://threeofwands.com/attra-iv-zero-overhead-frozen-attrs-classes)
- **Generator expressions** for the line-by-line aggregations in `research/hypothesis.py` and `search/mutate.py`.
- **defaultdict** for the group-by aggregation loops in `orchestration/optimizer_runner.py` and `evaluator/score.py`.

## 5. Out of scope / explicitly not recommended

- **Pydantic v2** for internal DTOs: it wins on JSON validation (~2-3x) but loses on init speed and memory vs dataclass/attrs. Use only at the API edge where validation is the point. (https://python.plainenglish.io/i-benchmarked-pythons-3-data-libraries-the-results-surprised-me-821dbc7c440e)
- **aiohttp over httpx for greenfield**: aiohttp is ~20-30% faster in pure async benchmarks, but httpx's unified API + HTTP/2 multiplexing wins in mixed-mode and where SigLab already has httpx. Do not migrate just for throughput without a separate test pass. (https://proxywing.com/blog/httpx-vs-requests-vs-aiohttp-feature-performance-comparison-guide)
- **PEP 810 lazy keyword**: not yet shipping; stick with `__getattr__` / `importlib.import_module` patterns for now.
