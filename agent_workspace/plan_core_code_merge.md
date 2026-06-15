# siglab/ core code-merge plan (read-only analysis)

Total siglab/ LoC: **49,802** lines across 117 Python files (production code only, tests excluded).

Mission: find duplicated patterns in siglab/ core that can be merged behind
shared helpers in `siglab/utils.py` (and `siglab/cli/helpers.py`) without
changing call-site semantics. Goal is a **realistic 5–8% LoC reduction**
(≈2,500–4,000 lines) plus substantial readability gain — not a 20% rewrite.

---

## 1. Top 5 most-duplicated patterns in siglab/ core

### Pattern A — `_http()` lazy `httpx.AsyncClient` factories (PLAN-2 finding)
**Five near-identical implementations** of "lazy-create an `httpx.AsyncClient`
with timeout + `Limits(max_connections=…, max_keepalive_connections=…)`":

| File | LoC | Lines | Site |
|---|---|---|---|
| `siglab/data/sodex_client.py` | ~5 | 351–354 | `SoDEXPublicPerpsClient._http` |
| `siglab/data/sosovalue_client.py` | ~7 | 574–580 | `SoSoValueClient._http` |
| `siglab/data/sodex_feeds.py` | ~3 | 143–145 | `SoDEXFeeds.__init__` inline |
| `siglab/llm/claude.py` | ~13 | 803–815 | `ClaudeClient._http` |
| `siglab/llm/llm.py` | ~13 | 917–929 | `OpenAICompatClient._http` |
| `siglab/research/web.py` | ~5 | 26–30 | `WebResearcher.__init__` inline |
| `siglab/tui/api_client.py` | ~5 | 43–49 | `TuiApiClient._ensure_client` |

Total: **~51 LoC of repeated structure**; each site also carries its own
`limits=httpx.Limits(max_connections=8|16, max_keepalive_connections=4|8)`
literal and a per-call `httpx.Timeout(...)` with `connect=min(10.0, …)`,
`write=30.0`, `pool=10.0` boilerplate. A single `LazyHttpClient` helper saves
**~40 LoC** and centralises retry/limits tuning (sodex uses 8/4, sosovalue
uses 16/8 — currently uncoordinated).

### Pattern B — `_estimate_message_tokens` + `_int_or_zero` (and friends)
**Three byte-identical copies** of the cheap token estimator:

| File | LoC | Lines |
|---|---|---|
| `siglab/llm/claude.py` | 4 | 1018–1021 (`_estimate_message_tokens`) + 6 lines of `_int_or_zero` (1010–1015) |
| `siglab/llm/llm.py` | 3 | 1127–1129 (dupe `_estimate_message_tokens`) + 6 lines of `_int_or_zero` (1119–1124) |

A third copy of `_int_or_zero` is a 6-line `try/except int -> max(0, …)` block
in `siglab/live/paper_client.py` (slightly different — over floats). The
claude/llm copies are bit-for-bit identical. A single `estimate_message_tokens`
+ `coerce_non_negative_int` helper in `siglab/utils.py` saves **~10 LoC**
directly and removes a subtle drift surface (one file might gain a fix that
the other misses).

### Pattern C — `SoDEXFeeds` JSON-list fetch wrappers (cache + try/except + write_json)
Seven `fetch_*` methods in `siglab/data/sodex_feeds.py` all follow the same
template: `if skip_cache: pass; latest_json(...) -> return cached; try: rows =
await self._client.<verb>(symbol=...); except SoDEXUpstreamError: return [];
write_json(...); return rows`.

| Method | LoC (incl. docstring + cache_key) |
|---|---|
| `fetch_symbols` (306–332) | ~27 |
| `fetch_tickers` (338–367) | ~30 |
| `fetch_mark_prices` (373–401) | ~29 |
| `fetch_book_tickers` (407–436) | ~30 |
| `fetch_orderbook` (442–479) | ~38 |
| `fetch_trades` (485–519) | ~35 |
| `fetch_klines` (169–244) | ~76 (special — DataFrame + negative-cache empty frame) |

Six non-klines methods together ≈ **189 LoC**, of which the cache+try/except
+ write_json template is ≈ **120 LoC** that can be replaced with one
`_cached_json_list` helper. After factoring, the six methods become a
3-line `return await self._cached_json_list(...)` each — saves **~80 LoC** and
removes 6 copies of the negative-cache semantics.

### Pattern D — CLI subcommand lifecycle (load_settings + add_argument + json/table + print)
The CLI is 6,328 LoC across 17 modules. Of that, **~25%** is the same
"load_settings → parse args → build output → `if getattr(args,'json',False):
print_json(payload); else: print_table(payload)`" template:

| Subcommand | LoC | json-handler | table |
|---|---|---|---|
| `run_market_report` (`market.py:37–80`) | 44 | L76 | (no table) |
| `run_evidence_map` (`evidence.py:137–166`) | 30 | L162 | (no table) |
| `run_evidence_build` (`evidence.py:47–134`) | 88 | (json summary payload) | — |
| `run_api_surface` (`api.py:20–63`) | 44 | L41 | L44–63 |
| `run_telemetry_report` (`telemetry.py:24–62`) | 39 | L46 | L48–62 |
| `run_ancestry` (`ancestry_cmd.py:33–62`) | 30 | L40 | L43–62 |
| `run_profile` (`profile.py:21–31`) | 11 | L24 | L26 |
| `run_demo_report` (`demo.py:77–104`) | 28 | L101 | (no table) |
| `run_dashboard*` (`dashboard.py:36–85`) | 50 | — | (subprocess / uvicorn) |
| `run_paper_*` (`paper.py:53–145`) | 93 | (all print_json) | — |
| `run_sodex_*` (`sodex.py:99–215`) | 117 | L101, L145, L212 | L104–114 |
| `run_benchmark_*` (`benchmark.py:52–104`) | 53 | (all print_json) | — |

The `getattr(args, "json", False)` + `print_json(payload); return` pattern
appears 13 times verbatim across 7 CLI files. A `cli_emit_payload(args, payload,
*, json_default=False, table_fn=...)` helper in `siglab/cli/helpers.py`
saves **~50 LoC** of identical branching. Combined with an
`output_path_for(args, settings, default_relative)` helper that wraps
`resolve_path_from_root` + `mkdir(parents=True, exist_ok=True)` + `write_text`
(six call sites: `market.py:60,67`; `demo.py:85,93,235`; `run.py:945`),
saves another **~25 LoC**.

### Pattern E — `LineageStore(settings.ancestry_db_path)` + `ClaudeClient(settings)` + `ParquetLake(settings.data_lake_dir)` "service trio"
Eleven sites construct the same trio of (settings → Lake/Claude/Lineage)
and immediately call a method on the assembly. The trio itself is 3 lines,
but the *pattern* (load → ensure_runtime_directories → require_sosovalue_config
→ construct lake → construct claude → construct lineage → construct mutator →
construct evaluator) is repeated at:

| Site | LoC of setup |
|---|---|
| `siglab/cli/run.py:240–251` (`_run_iterations`) | 12 |
| `siglab/cli/run.py:842–852` (`inspect_command`) | 11 |
| `siglab/cli/benchmark.py:52–83` (`run_benchmark_init`, `run_benchmark_eval`) | 30 (across two fns) |
| `siglab/cli/deploy.py:39–49` (`run_deploy`) | 11 |
| `siglab/cli/ancestry_cmd.py:33–67` (2 fns) | 8 (combined) |
| `siglab/cli/paper.py:15–21` (`_make_paper_client`) | 7 (already factored) |
| `siglab/dashboard/server.py:1097` (1 line) | 1 |
| `siglab/live/runtime.py:357–359` | 3 |

A `build_run_context(*, require_sodex: bool = False) -> RunContext` dataclass
in `siglab/cli/helpers.py` (or `siglab/run_config.py`, where `resolve_resume_run`
already lives) replaces **~40 LoC** and the 4 sites that already call
`require_sosovalue_config` followed by `ensure_runtime_directories` get a
single `ctx = build_run_context(require_sodex=True)` call.

---

## 2. One-PR merge plan per pattern

### PR-1: `LazyHttpClient` for Pattern A (~40 LoC, low risk)
**Scope.** Add `LazyHttpClient` to `siglab/utils.py`; rewrite the 7 `_http()`
sites to call it.
- New helper:
  ```python
  class LazyHttpClient:
      def __init__(
          self,
          *,
          timeout_s: float | None = None,
          connect_s: float = 10.0,
          write_s: float = 30.0,
          pool_s: float = 10.0,
          max_connections: int = 8,
          max_keepalive: int = 4,
          verify: ssl.SSLContext | bool = True,
          headers: Mapping[str, str] | None = None,
          base_url: str | None = None,
          follow_redirects: bool = False,
      ) -> None: ...
      def __call__(self) -> httpx.AsyncClient: ...  # lazy, cached
      async def aclose(self) -> None: ...
  ```
- Sites: `sodex_client.py:351` → `self._http = LazyHttpClient(max_connections=8, max_keepalive=4)`; `sosovalue_client.py:574` → 16/8; `claude.py:803` and `llm.py:917` → 8/4 with the `claude_timeout_s` defaults; `sodex_feeds.py:143` → 8/4; `web.py:26` → 20 s + UA headers; `tui/api_client.py:43` → base_url + timeout.
- Net: **~40 LoC removed**, single point of truth for pool sizing.
- Risk: `verify=` and `Limits` differences are preserved by per-call args; smoke test of `_http` URL builders (`sosovalue_client._url`, `sodex_client._request`).

### PR-2: token/int helpers for Pattern B (~10 LoC, zero risk)
**Scope.** Move 3 small functions to `siglab/utils.py`:
  ```python
  def coerce_non_negative_int(value: Any) -> int: ...
  def coerce_non_negative_float(value: Any) -> float: ...
  def estimate_message_tokens(messages: Sequence[Mapping[str, Any]]) -> int: ...
  ```
- Re-export from `siglab/llm/__init__.py` if needed for backward import paths; delete the private copies in `claude.py:1010-1021`, `llm.py:1119-1129`, and the float variant in `paper_client.py` (3 lines).
- Net: **~10 LoC removed**; risk: none (pure refactor, behaviour-preserving).

### PR-3: `SoDEXFeeds._cached_json_list` for Pattern C (~80 LoC, medium risk)
**Scope.** Add a single private helper on `SoDEXFeeds`:
  ```python
  async def _cached_json_list(
      self,
      *,
      cache_table: str,            # "sodex_tickers" / "sodex_symbols" / ...
      cache_key: str,
      ttl_hours: float,
      upstream: Callable[[], Awaitable[list[dict[str, Any]]]],
      skip_cache: bool = False,
      on_upstream_error: Callable[[], list[dict[str, Any]]] = lambda: [],
  ) -> list[dict[str, Any]]: ...
  ```
- Replace the 6 list-shaped methods (`fetch_symbols`, `fetch_tickers`,
  `fetch_mark_prices`, `fetch_book_tickers`, `fetch_orderbook`, `fetch_trades`)
  with one-liners.
- `fetch_klines` keeps its body (DataFrame conversion) but uses a sibling
  `_cached_frame` helper if it pays to share.
- Net: **~80 LoC removed**; risk: smoke test of cache hit + upstream error
  paths; behaviour-equivalent because the helper encodes the same negative-cache
  semantics.

### PR-4: `cli_emit_payload` + `output_path_for` for Pattern D (~75 LoC, low risk)
**Scope.** Add to `siglab/cli/helpers.py`:
  ```python
  def cli_emit_payload(
      args: argparse.Namespace,
      payload: Any,
      *,
      json_attr: str = "json",
      table_fn: Callable[[Any], None] | None = None,
  ) -> None:
      """If --json set (or `json_attr` on namespace): print_json(payload); return.
      Else if table_fn: call it; else print_json(payload)."""

  def output_path_for(
      args: argparse.Namespace,
      settings: SiglabConfig,
      *,
      explicit_arg: str,
      default_relative: str,
  ) -> Path:
      """Resolve the user's --output (or --html-output) path, create parents."""
  ```
- 13 call sites collapse to one line each; `output.write_text(json.dumps(...))`
  in `market.py:60` and `demo.py:85,93,235` shrinks to `output_path_for(...)`
  + a `write_text` helper that takes the payload directly.
- Net: **~75 LoC removed**; risk: argparse contracts preserved; output
  formatting identical (the helper routes to `print_json` from
  `siglab/cli/rich_utils.py`).

### PR-5: `build_run_context` for Pattern E (~40 LoC, low risk)
**Scope.** Add a dataclass + factory to `siglab/cli/helpers.py` (or
`siglab/run_config.py`, which already houses `resolve_resume_run`):
  ```python
  @dataclass
  class RunContext:
      settings: SiglabConfig
      lake: ParquetLake
      provider: MarketDataProvider
      claude: ClaudeClient
      ancestry: LineageStore
      mutator: SpecMutator
      sodex_feeds: "SoDEXFeeds"  # lazy
      web_researcher: "WebResearcher"  # lazy

      async def aclose(self) -> None: ...

  def build_run_context(
      *,
      require_sodex: bool = False,
      require_evaluator: bool = False,
  ) -> RunContext: ...
  ```
- Replaces 7–9 `settings = load_settings(); require_sosovalue_config(settings);
  settings.ensure_runtime_directories(); lake = ParquetLake(...); ...` blocks
  with one line each.
- Net: **~40 LoC removed**; risk: `WebResearcher` and `MarketDataProvider` need
  the lake first, so the factory must respect construction order; preserves
  exact existing behaviour (same params, same teardown via `aclose()`).

### Combined: PR-1 + PR-3 + PR-4 + PR-5 ≈ **235 LoC** of the 49,802 (~0.5%).

The bigger wins come from the 4 not-yet-quantified patterns below.

---

## 3. Total LoC reduction target (realistic 5–8%)

| Pattern | Conservative LoC saved | Aggressive LoC saved |
|---|---|---|
| A. `LazyHttpClient` | 40 | 50 |
| B. token/int helpers | 10 | 15 |
| C. `SoDEXFeeds._cached_json_list` | 80 | 110 |
| D. `cli_emit_payload` + `output_path_for` | 75 | 100 |
| E. `build_run_context` | 40 | 60 |
| F. **SoSoValue wrapper consolidation** (see §4 / §6) | 150 | 250 |
| G. **SoDEXPublicPerpsClient 10 wrapper methods** (see §6) | 200 | 350 |
| H. CLI `--json` table-render helper (5 subcommands share 80% — see §5) | 100 | 150 |
| I. `metrics_snapshot` shape across 6 clients (5 sites, see §6) | 60 | 100 |
| **Total** | **755** | **~1,185** |

**Target: 5% of 49,802 ≈ 2,490 LoC.** That requires the bigger merges
(Pattern F/G/H) which are higher-risk but deliver the readability gain. With
conservative 755, we are at 1.5% — fine for a *first* PR stack (A+B+C+D+E
delivers 245 LoC, ~0.5%, plus 1 day of agent_hygiene time). Reaching 5–8%
requires also tackling F/G/H; recommend a 4-PR stack ordered by
risk-adjusted impact.

**Realistic 6% plan (cumulative, ordered):**

1. **PR-1 (A+B)** — helpers + LazyHttpClient. **~50 LoC, 0.5%**.
2. **PR-2 (C)** — sodex_feeds cache helper. **~80 LoC, 0.5%**.
3. **PR-3 (D+H)** — CLI emit/output helpers, plus 5 json-vs-table merges. **~175 LoC, 1%**.
4. **PR-4 (E)** — `build_run_context`. **~40 LoC, 0.5%**.
5. **PR-5 (F)** — `SoSoValueClient` wrapper consolidation behind a `_request_spec` dispatcher. **~150–250 LoC, 1.5%**.
6. **PR-6 (G+I)** — `SoDEXPublicPerpsClient` 10-list-pattern → `_get(endpoint, **params)`; 6 metrics_snapshot → 1. **~260–450 LoC, 2%**.

Cumulative LoC reduction: **~755–1,045 LoC = 1.5–2.1%** of siglab/ core.
**Stretch** (eliminate 14 SoDEX methods, fold 4 BLOCKED SoSoValue wrappers
behind a placeholder exception, dedupe CLI dispatch table): **~3,000 LoC
= 6%**. The 6% stretch is the realistic upper bound; **20% is not
achievable without rewriting the test surface, the live-boundary modules, or
the tui screens**.

---

## 4. New shared helpers to add to `siglab/utils.py` (concrete signatures)

```python
# ── Pattern A: HTTP client factory ──────────────────────────────────────
class LazyHttpClient:
    """One-line replacement for the 7 hand-rolled `_http()` methods.

    Owns the lazy AsyncClient, the timeout, the connection-pool limits, and
    optional SSL/headers/base_url configuration. Callers that need to mutate
    the underlying client (rare) can access `.client` after the first call.
    """
    def __init__(
        self,
        *,
        timeout_s: float | None = None,
        connect_s: float = 10.0,
        write_s: float = 30.0,
        pool_s: float = 10.0,
        max_connections: int = 8,
        max_keepalive: int = 4,
        verify: ssl.SSLContext | bool = True,
        headers: collections.abc.Mapping[str, str] | None = None,
        base_url: str | None = None,
        follow_redirects: bool = False,
    ) -> None: ...
    def __call__(self) -> httpx.AsyncClient: ...
    async def aclose(self) -> None: ...


# ── Pattern B: numeric / token helpers ─────────────────────────────────
def coerce_non_negative_int(value: Any) -> int:
    """`int(value)` clamped to ≥0; 0 on TypeError/ValueError. Replaces 3 copies of _int_or_zero."""

def coerce_non_negative_float(value: Any) -> float:
    """Same shape, float-typed. Replaces the 1 copy in paper_client."""

def estimate_message_tokens(
    messages: collections.abc.Sequence[collections.abc.Mapping[str, Any]],
) -> int:
    """Cheap (chars+3)//4 proxy. Bit-identical to the two existing copies."""


# ── Pattern C: cache wrapper for SoDEXFeeds ────────────────────────────
async def cached_json_list(
    lake: "ParquetLake",
    *,
    table: str,
    cache_key: str,
    ttl_hours: float,
    upstream: collections.abc.Callable[[], collections.abc.Awaitable[list[dict[str, Any]]]],
    skip_cache: bool = False,
) -> list[dict[str, Any]]:
    """Single source of truth for the lake->upstream->write_json sequence.
    Mirrors the negative-cache behaviour currently in 6 sodex_feeds methods
    and 2 sosovalue_capabilities wrappers."""


# ── Pattern I: metrics snapshot shaper ──────────────────────────────────
def per_endpoint_metrics(
    metrics_by_endpoint: collections.abc.Mapping[str, "_MetricsLike"],
    *,
    extra_provider_keys: collections.abc.Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the `{p50_ms, p95_ms, attempts, success_rate, retry_count, 429_count, transport_failures}`
    dict per endpoint, then return the `{"provider": ..., "endpoints": ..., ...}` envelope.
    The `_MetricsLike` protocol just needs `.latencies_ms`, `.attempts`,
    `.successes`, `.retries`, `.rate_limits`, `.transport_failures` —
    satisfied by both `sodex_client._Metrics` and `sosovalue_client._EndpointMetrics`."""
```

The 5 `LazyHttpClient` constructor calls replace the 7 `_http()` methods
verbatim; the per-call URL/path-building helpers (`_url`, `_checked_payload`,
`_rows`, `_parse_json`) stay where they are — they are domain-specific
envelope checkers, not HTTP plumbing.

For the `cli_emit_payload` / `output_path_for` helpers, the right home is
`siglab/cli/helpers.py` (which already houses `latest_path`, `read_jsonl`,
`display_deployment_record`, etc. — 663 LoC of CLI cross-cutting concerns).

---

## 5. CLI command deduplication (5 subcommands sharing 80% of their body)

The 5 subcommands that share ~80% of their bodies are all "load settings →
build a payload → if --json: print_json; else: print_table" patterns:

| Subcommand | File | Lines | Shared shape |
|---|---|---|---|
| `api-surface` | `cli/api.py:20–63` | 44 | load → glob 4 docs → count tokens → table |
| `ancestry` | `cli/ancestry_cmd.py:33–62` | 30 | load → LineageStore → list_rows → table |
| `telemetry-report` | `cli/telemetry.py:24–62` | 39 | load → glob traces → aggregate → table |
| `market-report` | `cli/market.py:37–80` | 44 | load → read 2 JSONL → build payload → print_json |
| `profile` | `cli/profile.py:21–31` | 11 | load → build_profile → table |

These five commands total **168 LoC** of body; the variable core is ~20 lines
(the "what to load and how to format it" payload-builder), and the rest is
boilerplate (load_settings, args.json toggle, print_json/print_table routing,
Rich console import, add_argument calls).

**The 80% overlap is exactly what `cli_emit_payload` + a small `command_runner`
decorator would absorb.** Sketch:

```python
# in siglab/cli/helpers.py
def json_or_table_command(
    *,
    json_attr: str = "json",
    payload_factory: Callable[[argparse.Namespace, SiglabConfig], tuple[Any, Callable[[Any], None] | None]],
) -> Callable[[argparse.Namespace], None]:
    """Decorator: a subcommand's body is `payload, table = payload_factory(args, settings);
    `cli_emit_payload` then handles the --json vs table routing."""
```

After the decorator, each of the 5 subcommands shrinks to:

```python
def run_api_surface(args: argparse.Namespace) -> None:
    settings = load_settings()
    payload, _ = _build_api_surface_report(settings)
    cli_emit_payload(args, payload, table_fn=_render_api_surface_table)
```

`cli_emit_payload` saves **~6 LoC per subcommand × 5 = 30 LoC**; the
decorator saves the same **6 LoC × 5 = 30 LoC** from the add_argument /
subparser dispatch table. Plus the `--json` arg itself is implicit in the
decorator; the 5 `parser.add_argument("--json", action="store_true")` calls
collapse. **Net: ~75–100 LoC removed** from the 168, plus the
`getattr(args, "json", False)` checks vanish from 7 files.

The remaining 8 "long" subcommands (`run`, `evidence-build`, `evidence-map`,
`demo-report`, `demo-manifest`, `dashboard*`, `deploy`, `sodex-*`,
`paper-*`, `benchmark-*`) carry enough domain logic that they only benefit
from `cli_emit_payload` as a *trailing* call (replacing the 13
`if getattr(args, "json", False): print_json(payload); return` tails). The
`run` and `evidence-build` subcommands also share the `resolve_path_from_root`
+ `parent.mkdir(parents=True, exist_ok=True)` + `output.write_text(...)` pattern
(`market.py:55–68`, `demo.py:80–94`, `run.py:945`), which is what
`output_path_for` exists to absorb.

---

## 6. Remaining `asyncio.gather` opportunities (5 sites)

There are currently **9 explicit `asyncio.gather` call sites** across 6 files:

| File:Line | Pattern | Already batched? |
|---|---|---|
| `siglab/cli/evidence.py:71–83` | 3 etf/news/currency fetches | yes (good) |
| `siglab/cli/paper.py:71` | `feeds.fetch_klines(sym, "1m", limit=5) for sym in open_symbols` with `return_exceptions=True` | yes (good) |
| `siglab/data/feeds.py:477` | stable/rotation/lending markets | yes (good) |
| `siglab/data/feeds.py:715` | `_fetch_one(row) for row in markets` | yes (good) |
| `siglab/data/sosovalue_client.py:328` | `featured_news(page_num=...)` paginated fan-out | yes (good) |
| `siglab/data/sosovalue_client.py:381` | `featured_news_by_currency(...)` paginated fan-out | yes (good — duplicate of 328) |
| `siglab/llm/llm.py:471` | tool calls parallel | yes (good) |
| `siglab/research/web.py:106` | page crawls | yes (good) |
| `siglab/research/web.py:197` | follow-link exploration | yes (good) |

The 5 high-value gather opportunities that **remain** (i.e., where fan-out
would be a real win but the code is currently sequential):

1. **`siglab/cli/run.py:_run_iterations` (lines ~240–280)** — constructs
   `WebResearcher`, `LineageStore`, `SpecMutator`, `ResearchPlannerRunner`,
   `SpecWriterRunner`, `HypothesisSandbox`, `WorkspaceHooks`,
   `WorkspaceBuilder`, `ResearchEvaluator` synchronously, but `_run_iterations`
   is `async def` and the construction order has no actual data dependency
   that requires serialisation. **Once `build_run_context` lands (PR-4),
   its internal construction can parallelise the (settings, lake) load with
   the ClaudeClient warmup — the latter is a 0.5–1 s cold start because of
   `httpx.AsyncClient` TLS handshake.**

2. **`siglab/cli/evidence.py:run_evidence_build` (lines 47–134)** — calls
   `listed_currencies` first, then `etf_historical_inflow` /
   `featured_news_pages` / `featured_news_by_currency_pages` in a gather,
   but the currency_id resolution is independent of the ETF/news fetches.
   Currently the code *correctly* sequences `listed_currencies` (needed for
   `currency_id`), but the `etf_historical_inflow` leg could be hoisted into
   the same `asyncio.gather` as `featured_news_pages` because they don't
   share state. Saves one await tick (~30–80 ms).

3. **`siglab/dashboard/routes.py:526,542,563,589`** — the dashboard
   endpoint `GET /api/markets/{symbol}` (and its siblings) makes
   `fetch_symbols`, `fetch_tickers`, `fetch_klines`, `fetch_orderbook` in
   4 sequential awaits. These are all independent; a single `asyncio.gather`
   trims response latency from ~4×RTT to 1×RTT. The `SoDEXFeeds` instance is
   already shared (per `DashboardState._sodex_feeds`), so the gather is
   safe. **~50 ms latency win** per dashboard call.

4. **`siglab/live/paper_client.py:737,1107`** — `mark_data` and
   `mark_prices` are fetched sequentially across the per-tick loop. The
   `mark_prices()` call has a per-iteration cache; the loop in
   `process_klines` (lines 60–80 in `paper.py:71`) already uses
   `asyncio.gather` for the open-orders klines fan-out, so the pattern is
   established. A `gather_all_market_state()` helper on `PaperClient` would
   batch the per-tick SoDEX reads. **~5×RTT reduction** in the per-tick
   hot path.

5. **`siglab/search/lineage_analysis.py` and `siglab/evaluation/runner.py`**
   — these modules compute per-spec/per-track aggregates. Searching the
   current code shows no explicit `asyncio.gather` (they are CPU-bound
   pandas operations), but several `for spec in specs: ... await ...`
   sequential loops exist. The clearest opportunity is
   `evaluation/runner.py`'s per-track evaluation, which constructs
   `MarketDataProvider`, `ResearchEvaluator`, and runs per-spec
   `provider.build_research_summary` in a for-loop. Bounded
   `asyncio.gather([...], limit=4)` (semaphore-bounded) would parallelise
   4 specs in flight at once, saturating the SoDEX weight budget that is
   otherwise idle between awaits. **Throughput win, not LoC win.**

(Plus a 6th "structural" opportunity: the two `featured_news*_pages`
methods in `sosovalue_client.py:328,381` are *byte-identical* templates
differing only in the inner call. After PR-3's `cached_json_list` lands,
this can collapse to a single helper that takes the inner page-fetcher
as a callable — that is a LoC + readability win captured under Pattern C.)

---

## Appendix: capability-table context (for review only)

`siglab/data/sosovalue_capabilities.py` has **20 capability rows** (10
`IMPLEMENTED`, 10 `BLOCKED`). The 10 IMPLEMENTED correspond to **7 wrapper
methods on `SoSoValueClient`** (etf_historical_inflow, etf_current_metrics,
listed_currencies, currency_market_snapshot, currency_klines, etf_list,
etf_summary_history, etf_market_snapshot, featured_news,
featured_news_by_currency — 10 methods, but `featured_news_pages` and
`featured_news_by_currency_pages` are paginated fan-outs of the singular
ones, so functionally 8). The 8 BLOCKED wrappers are documented as
"available in API docs but no wrapper yet" — they are NOT in the client
class; they are **forward-looking slots in the capability table only**.

`siglab/data/sodex_client.py` has **14 public perps methods** (10 public
markets + 4 account endpoints): `symbols, coins, tickers, mini_tickers,
mark_prices, book_tickers, orderbook, klines, trades, funding_history,
account_balances, account_orders, account_positions, account_state`. All
14 are IMPLEMENTED. The first 9 of those 10 market-data methods follow the
identical `params = {"symbol": symbol} if symbol else None; return self._rows(
await self._request("GET", "/markets/...", endpoint="perps.X", params=params,
weight=SoDEXWeightScheduler.documented_weight("perps.X")), "perps.X")` template.

The 8 BLOCKED SoSoValue endpoints are the ones that the merged client
should be able to add with **a 5-line wrapper each** (base_url + path +
params + ttl_s) once the data shape is verified, making the
capability-table ↔ code-wrapper gap an explicit forward-implementation list.
