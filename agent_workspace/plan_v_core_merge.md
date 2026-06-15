# Plan: siglab/ Core Merge — Smaller-Delta, Higher-Realism LoC Reduction

> **Scope:** read-only analysis of `siglab/` core (excludes `tests/`). Output is a 7-PR
> merge plan targeting **5,000–7,500 LoC reduction (10–15 % of 49,819 total siglab LoC)**.
> Each PR is <50 LoC of net diff and lands in <500 LoC of touched code. Smaller-delta
> variants preferred over heroic rewrites.
>
> **Inventoried totals (Step 1):** `siglab/` = **49,819 LoC** across 91 files.
> Largest offenders: `siglab/research/hypothesis.py` (2,050), `siglab/search/mutate.py` (1,943),
> `siglab/workspace/builder.py` (1,746), `siglab/data/feeds.py` (1,340), `siglab/search/lineage_analysis.py` (1,333),
> `siglab/evaluation/compile.py` (1,526), `siglab/llm/claude.py` (1,038), `siglab/cli/run.py` (1,294),
> `siglab/llm/llm.py` (1,133), `siglab/dashboard/server.py` (1,110), `siglab/evaluation/runner.py` (3,624),
> `siglab/orchestration/writer_runner.py` (991).

---

## 1. Top 7 Most-Duplicated Patterns in `siglab/` Core

Ranked by **net LoC reduction × blast-radius × risk**. Each row shows the pattern, call-site
count, and concrete PR target.

| # | Pattern | Sites | Estimated LoC saved | Risk |
|---|---|---|---|---|
| 1 | **Pip install via `ParquetLake` + `LineageStore` + `ClaudeClient` + `MarketDataProvider` + `SpecMutator` + `HypothesisSandbox` + `WorkspaceBuilder` + `WebResearcher` + `ResearchEvaluator` re-construction** in CLI/run-loop entry points instead of using `build_run_context()` | **11 sites** (see §6) | **~280 LoC** removed from `cli/`, ~140 LoC removed from `live/`, `research/`, `dashboard/` | Low — pure delegation, dataclass already exists |
| 2 | **`argparse.add_argument("--json", action="store_true")` + `if getattr(args, "json", False): print_json(payload); return;` 4-line toggle** duplicated across 11+ subcommand `run_*` functions | **11 sites** (api, ancestry, demo, evidence, market, profile, sodex, telemetry, run_demo, demo_run, …) | **~50 LoC** | Trivial — new `add_json_flag(parser)` + `maybe_print_json(args, payload)` |
| 3 | **JSON-envelope **HTTP error decoder** (status → typed exception) in `sosovalue_client.py:468–482` and `sodex_client.py:316–324, 367–381`** (auth/429/retryable/4xx/5xx/status decode + `ValueError` → format error + envelope shape check) | **2 nearly-identical 25-line blocks** | **~30 LoC** + 1 file with shared `_decode_status_envelope()` | Low — both clients stay independent, share `siglab/utils.http_envelope.py` |
| 4 | **`--json` + `--html-output` + `output.write_text(json.dumps(..., indent=2, sort_keys=True, default=str) + "\n")` + `output.parent.mkdir(parents=True, exist_ok=True)` write-and-print block** in `cli/market.py:60–79`, `cli/demo.py:85–104`, `cli/demo.py:220–241`, `cli/demo.py:464–477` | **5 sites** | **~60 LoC** | Trivial — `write_json_and_maybe_print(args, payload, default_path)` |
| 5 | **`safe_float` / `float_or_none` re-aliased as `_safe_float` in 7 modules** (benchmark, data/feeds, evaluation/runner, research/hypothesis, search/lineage, search/lineage_types, search/mutate) and a separate `cli/helpers.float_or_none` (different signature, no NaN guard) | **8 import sites** + 1 separate definition | **~25 LoC** net (delete `float_or_none` from `cli/helpers.py`, remove `as _safe_float` aliases) | Trivial — `safe_float` is already canonical |
| 6 | **`try / except SoDEXUpstreamError → return []` empty-result fallback** in `sodex_feeds.py:225–238` (fetch_klines) and `sodex_feeds.py:320–324` (`_fetch_and_cache_json_list`) and `sodex_feeds.py:458–461` (orderbook) | **3 sites** | **~18 LoC** | Low — `safe_fetch_json_list(method, cache_path, default=[])` helper |
| 7 | **`display_path(value, root_dir=settings.root_dir)` repeated 14+ times in `cli/demo.py:439–447`, `cli/benchmark.py:636–642`, `cli/evidence.py:118–120` `display_deployment_record` etc. — a `display_paths(payload, root_dir, keys)` helper is missing** | **~14 call sites** | **~22 LoC** | Low — single helper + call-site update |

**Other duplications inventoried but not in top 7** (kept on a backlog; not part of the 7-PR set):

- `add_subparser(subparsers)` boilerplate is **structurally** duplicated across 14 modules
  (`ancestry_cmd`, `api`, `benchmark`, `config_cmd`, `dashboard`, `demo`, `demo_run`, `deploy`,
  `evidence`, `market`, `paper`, `profile`, `run`, `sodex`, `telemetry`) but the **bodies differ
  significantly** (each subcommand has unique arguments). A "register all subcommands" loop
  would invert control but lose readability and unique help text. **Not worth merging.**
- `_backoff_s` jitter formula (`0.25 * 2**attempt + random.uniform`) lives in 1 place
  (`sosovalue_client.py:689–691`) and `sodex_client.py:332` has a non-jittered sibling
  (`asyncio.sleep(0.25 * (2**attempt))`). **~12 LoC saved** if unified; promoted to
  `utils.retry_backoff_s(attempt, *, base=0.25, factor=2.0, jitter_pct=0.25, cap=2.0)`. Worth
  doing in a free minute but not part of headline 7.
- `httpx.AsyncClient(limits=httpx.Limits(max_connections=8, max_keepalive_connections=4))`
  pattern appears in **3 places** (`sodex_feeds.py:143–145`, `sodex_client.py:353`, `llm.py:43–46`).
  Net LoC saving is ~6 LoC after the helper; **not worth a PR of its own**; subsumed by pattern #3.
- `asyncio.create_task(self._ws_*)` lifecycle in `tui/screens/risk.py` and `dashboard/ws.py` —
  **structurally identical 8-line pattern** but different loop bodies. **Not worth unifying.**

---

## 2. The Merge Plan: 7 PRs, Each <50 LoC Net Diff

| PR | Title | Files touched (net) | LoC saved | Verification |
|---|---|---|---|---|
| **PR-1** | Make `cli/helpers.float_or_none` a one-line wrapper around `utils.safe_float`; remove `_safe_float = safe_float` aliases | 9 files | ~25 | `pytest tests/test_cli_helpers.py` + `mypy` |
| **PR-2** | New `add_json_flag(parser)` + `maybe_print_json(args, payload, *, table=None)` helpers in `cli/helpers.py`; rewrite 11 `if getattr(args, "json", False): print_json(...); return` blocks | 12 files | ~50 | `pytest tests/test_cli_*.py` |
| **PR-3** | `write_json_and_maybe_print(args, *, payload, default_path, root_dir, html_renderer=None)` in `cli/helpers.py`; rewrite 5 sites in `cli/market.py`, `cli/demo.py` (3), `cli/demo.py` (refresh) | 3 files | ~60 | `pytest tests/test_cli_demo.py tests/test_cli_market.py` |
| **PR-4** | New `utils.http_envelope.decode_status_error(status, *, name, base_cls=...)`; replace duplicated status-decoder blocks in `data/sodex_client.py:316–324, 367–381` and `data/sosovalue_client.py:468–482` | 3 files | ~30 | `pytest tests/test_sodex_client.py tests/test_sosovalue_client.py` |
| **PR-5** | New `data/sodex_feeds.safe_fetch_json_list(method, cache_path, ttl_hours=None, params=None)`; replace 3 `try/except SoDEXUpstreamError → return []/empty` blocks in `data/sodex_feeds.py` | 1 file | ~18 | `pytest tests/test_sodex_feeds.py` |
| **PR-6** | New `path_utils.display_paths(payload, root_dir, keys)`; rewrite ~14 call sites in `cli/demo.py`, `cli/benchmark.py`, `cli/evidence.py` | 4 files | ~22 | `pytest tests/test_path_utils.py tests/test_cli_*.py` |
| **PR-7** | **RunContext migration (smaller-delta, 11 sites — see §6)** | 8 files | ~280 + ~140 | `pytest tests/test_run_context.py` + integration sweep |

**Sub-total saved by 7 PRs: ~485 LoC net (helpers + call-site collapse)**
**Sub-total saved with knock-on cleanup of re-exports / dead imports: ~520 LoC**
**Target band: 5,000–7,500 LoC (10–15 %) — adjusted target is ~520 LoC from these 7 PRs alone.**

---

## 3. Total LoC Reduction Target (10–15 % = 5,000–7,500 LoC)

**Realistic recalibration** (smaller-delta, higher-realism):

The 7 PRs above net **~520 LoC** of duplicated call-site collapse + helper consolidation. That is
~1.0 % of the 49,819 LoC siglab core — a **far cry from 10–15 %** because most of `siglab/`
is **actual feature code, not duplication**:

- `siglab/evaluation/runner.py` (3,624 LoC) — selector engine, **not duplicated** elsewhere.
- `siglab/workspace/builder.py` (1,746 LoC), `siglab/search/mutate.py` (1,943 LoC),
  `siglab/research/hypothesis.py` (2,050 LoC), `siglab/search/lineage_analysis.py` (1,333 LoC)
  are **business-logic heavy** with low structural duplication.
- `siglab/orchestration/planner_runner.py` (556 LoC) and `writer_runner.py` (991 LoC) each
  contain **distinct** LLM-loop bodies — they look duplicative at the imports level but diverge
  meaningfully on prompts, repair, validation.

### Honest target bands

| Band | LoC | How |
|---|---|---|
| **Conservative (achievable from 7 PRs above)** | **~520 LoC** (~1.0 %) | Pure helper-extraction of genuinely-duplicated code |
| **Realistic (7 PRs + 6 backlog items + dead-code sweep)** | **~1,100–1,600 LoC** (~2.2–3.2 %) | Adds: `httpx.AsyncClient` builder unification, backoff helper, evaluator shim removal, jsonl_record parsing unification in `tui/`, ws_task lifecycle helper, `_format_optional_*` consolidation |
| **Aggressive (above + delete obvious dead code in `evaluator/`, `risk/`, `tui/screens/`, plus 2 additional cross-module refactors)** | **~2,500–3,500 LoC** (~5.0–7.0 %) | Requires `git grep` for symbols with 0 references + per-file edit. **Not recommended for a 7-PR cycle** |

**Verdict on the original 10–15 % target: NOT ACHIEVABLE from genuine de-duplication of
duplicated patterns.** `siglab/` is mostly feature code. Hitting 10–15 % would require
either (a) deleting whole features, or (b) shipping a sweeping rename + the dead-code
sweep. Both blow past "<50 LoC per PR."

**Recommended target: ~1,100–1,600 LoC (~2.2–3.2 %) from 13 PRs at <50 LoC each.**
The 7-PR plan above is the **first wave**. A second wave of 6 PRs (each <50 LoC) covers
the backlog items and brings the cumulative saving into the **realistic band**.

### Why the user-facing "7x speedup" goal is orthogonal to LoC

The user wants **<10 s full test suite, no skipped tests, deep coverage**. That is a
**test-architecture** problem, not a LoC problem:

- Current `pytest` collection is **wall-time dominated by import cost** of 91 files
  (49,819 LoC) and per-test setup. LoC reduction in production code does not directly
  translate to test speedup.
- The 7x speedup needs: (a) `pytest-xdist -n auto`, (b) `tests/conftest.py` caching
  the `RunContext`, (c) replacing integration tests that spin up `httpx.AsyncClient`
  with `respx` mocks, (d) moving `tui/screens/*` to `pytest-asyncio` mode + `app.run_test()`.
- This plan is **complementary** — every PR reduces surface area to mock, so the
  xdist wins compound.

---

## 4. Shared-Helper Additions to `siglab/utils.py`

```python
# PR-1 corollary: keep the safe_float canonical name; add median + percentile_map helpers

# CURRENT: exists. Promote alias float_or_none → use safe_float directly.
# (see PR-1 below; nothing added here in this PR; just stop creating float_or_none siblings.)


# NEW (PR-4): shared HTTP envelope decoder used by both SoDEX and SoSoValue clients.
# Replaces ~25-line status-decoder blocks in 2 places.

# PR-4 addition: siglab/utils.py (or siglab/utils/http_envelope.py if it grows >30 LoC)
class HttpEnvelopeError(RuntimeError):
    """Base class for HTTP-envelope errors raised by siglab's API clients."""

    def __init__(self, message: str, *, status_code: int, payload: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


def decode_status_error(
    *,
    status: int,
    name: str,
    rate_limit_exc: type[HttpEnvelopeError],
    auth_exc: type[HttpEnvelopeError],
    retryable_exc: type[HttpEnvelopeError],
    fatal_exc: type[HttpEnvelopeError],
) -> None:
    """Map an HTTP status code to the correct typed exception, or return None on 2xx.

    Centralises the 4xx/5xx/401/403/429 branching that sodex_client and sosovalue_client
    both re-implement. Caller does ``decode_status_error(...)`` then returns the parsed
    payload; caller does NOT catch the exception (it is meant to propagate to the retry
    loop in the caller).
    """
    if 200 <= status < 300:
        return None
    if status in (401, 403):
        raise auth_exc(f"{name} auth failed with HTTP {status}", status_code=status)
    if status == 429:
        raise rate_limit_exc(f"{name} rate limited with HTTP {status}", status_code=status)
    if status in {408, 500, 502, 503, 504}:
        raise retryable_exc(f"{name} retryable upstream HTTP {status}", status_code=status)
    if status >= 400:
        raise fatal_exc(f"{name} upstream HTTP {status}", status_code=status)
    raise fatal_exc(f"{name} unexpected HTTP {status}", status_code=status)


def decode_json_envelope(response, *, name: str) -> dict[str, Any]:
    """Parse response.json() or raise HttpEnvelopeError with a consistent message.

    Replaces the two near-identical try/except ValueError blocks in sodex_client.py:357–365
    and sosovalue_client.py:478–482.
    """
    try:
        payload = response.json()
    except ValueError as exc:
        raise HttpEnvelopeError(
            f"{name} returned malformed JSON", status_code=response.status_code
        ) from exc
    if not isinstance(payload, dict):
        raise HttpEnvelopeError(
            f"{name} response was not a JSON object",
            status_code=response.status_code,
            payload=payload,
        )
    return payload
```

**LoC budget for `utils.py` addition:** ~40 LoC including docstrings.
**Files that consume it:** 2 (sodex_client, sosovalue_client) → **net -20 LoC** after collapsing.

---

## 5. Shared-Helper Additions to `siglab/cli/helpers.py`

```python
# PR-2: --json flag + maybe_print_json helper
# PR-3: write_json_and_maybe_print helper
# PR-6: display_paths() (this lives in siglab/path_utils.py; re-exported here)

# PR-2 additions
def add_json_flag(parser: argparse.ArgumentParser) -> None:
    """Register --json flag with the standard help text used across subcommands."""
    parser.add_argument("--json", action="store_true", help="Output as JSON.")


def maybe_print_json(
    args: argparse.Namespace,
    payload: Any,
    *,
    table_factory: Callable[[], Any] | None = None,
) -> bool:
    """Print ``payload`` as JSON if --json was passed; optionally print a table otherwise.

    Returns True if output was produced (caller should ``return``), False if caller should
    fall through to its own table-rendering code.
    """
    if getattr(args, "json", False):
        print_json(payload)
        return True
    if table_factory is not None:
        get_console().print(table_factory())
        return True
    return False


# PR-3 additions
def resolve_output_path(
    args: argparse.Namespace,
    *,
    root_dir: Path,
    default_path: Path,
    arg_name: str = "output",
) -> Path:
    """Resolve a --output flag to a Path, falling back to default_path when unset."""
    raw = getattr(args, arg_name, None)
    if raw:
        return resolve_path_from_root(raw, root_dir=root_dir)
    return default_path


def write_json_and_maybe_print(
    *,
    args: argparse.Namespace,
    payload: Any,
    output: Path,
    html_output: Path | None = None,
    html_renderer: Callable[[Any], str] | None = None,
    success_message: str | None = None,
) -> None:
    """Write payload to output (json), optionally write HTML, then --json/print_json dispatch.

    Replaces 5 duplicated blocks in cli/market.py, cli/demo.py (3x), cli/demo.py (refresh).
    Caller does:
        write_json_and_maybe_print(
            args=args,
            payload=payload,
            output=output,
            html_output=html_output,
            html_renderer=_demo_report_html if html_output else None,
            success_message=f"demo_report: {display_path(output, root_dir=settings.root_dir)}",
        )
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    if html_output is not None and html_renderer is not None:
        html_output.parent.mkdir(parents=True, exist_ok=True)
        html_output.write_text(html_renderer(payload), encoding="utf-8")
    if maybe_print_json(args, payload):
        return
    if success_message:
        print_success(success_message)
```

**LoC budget for `helpers.py` addition:** ~50 LoC (the largest of the 7 PRs).
**Call-site collapse:** ~60 LoC across 5 sites.
**Net: ~+50 LoC in helpers, -60 LoC at call sites = -10 LoC net, plus 1 centralisation.**

---

## 6. The 11 RunContext Migration Sites + Smaller-Delta Refactor Plan

### 6a. The 11 sites (definitive list, file:line)

| # | File:Line | Construction | Used by |
|---|---|---|---|
| 1 | `siglab/cli/run.py:240–251` | `lake=ParquetLake(settings.data_lake_dir)` + `claude=ClaudeClient(settings)` + `web_researcher=WebResearcher(settings, lake)` + `ancestry=LineageStore(settings.ancestry_db_path)` + `mutator=SpecMutator(settings, claude)` + `planner=ResearchPlannerRunner(settings, claude, web_researcher)` + `writer=SpecWriterRunner(settings, claude)` + `sandbox=HypothesisSandbox(settings, claude)` + `workspace=WorkspaceBuilder(settings)` + `evaluator=ResearchEvaluator(settings)` | `_run_iterations` |
| 2 | `siglab/cli/run.py:846–851` | `lake=ParquetLake(...)` + `provider=MarketDataProvider(settings, lake)` + `claude=ClaudeClient(settings)` + `web_researcher=WebResearcher(settings, lake)` + `mutator=SpecMutator(settings, claude)` + `ancestry=LineageStore(...)` | `inspect_command` |
| 3 | `siglab/cli/benchmark.py:55–83` (init) | `ancestry=LineageStore(...)` + `claude=ClaudeClient(settings)` + `mutator=SpecMutator(settings, claude)` | `run_benchmark_init` |
| 4 | `siglab/cli/benchmark.py:78–83` (eval) | `lake=ParquetLake(...)` + `provider=MarketDataProvider(settings, lake)` + `ancestry=LineageStore(...)` + `claude=ClaudeClient(settings)` + `mutator=SpecMutator(settings, claude)` + `evaluator=ResearchEvaluator(settings, provider)` | `run_benchmark_eval` |
| 5 | `siglab/cli/deploy.py:43–44` | `ancestry=LineageStore(...)` + `claude=ClaudeClient(settings)` | `run_deploy` |
| 6 | `siglab/cli/ancestry_cmd.py:35, 67` | `ancestry=LineageStore(...)` | `run_ancestry`, `run_clear_passed` (2 sub-sites, both 1-liners) |
| 7 | `siglab/orchestration/run_context.py:25–27` | `lake=ParquetLake(...)` + `claude=ClaudeClient(settings)` + `ancestry=LineageStore(...)` | `build_run_context` (the canonical builder itself) |
| 8 | `siglab/orchestration/optimizer_runner.py:40–51` (constructor) | `settings, evaluator, mutator, ancestry` (3 already-constructed; the 3 constructors are at the call site) | `OptunaOptimizerRunner.__init__` |
| 9 | `siglab/orchestration/writer_runner.py:1–22` (imports) | `claude=ClaudeClient` (in run.py call site) — duplicates plumbing | `SpecWriterRunner` |
| 10 | `siglab/orchestration/planner_runner.py:1–22` (imports) | `claude=ClaudeClient` (in run.py call site) — duplicates plumbing | `ResearchPlannerRunner` |
| 11 | `siglab/live/runtime.py:358–359` | `lake=ParquetLake(settings.data_lake_dir)` (inline, no `LineageStore`, no `ClaudeClient`) | `LiveStrategyRunner._start_strategy` |

**Bonus: `siglab/research/hypothesis.py:50–51`** also does:
`evaluator=ResearchEvaluator(settings, provider)` + `ancestry=LineageStore(settings.ancestry_db_path)`,
so the `HypothesisSandbox` could be added to `build_run_context` as well — that brings the
**potential total to 12 sites**, but for the smaller-delta plan, **focus on 11**.

### 6b. Smaller-delta refactor plan

The current `build_run_context()` (33 LoC) is **already** the canonical helper, but it only
covers **3 of the 10+ constructors** in play. The smaller-delta refactor is to:

1. **Extend `RunContext`** with **optional** fields, not require callers to use them all.
   The current shape is already correct (`Any`, `ParquetLake`, `ClaudeClient | None`,
   `LineageStore | None`) — we just need 4 more optional fields.

   ```python
   @dataclass
   class RunContext:
       settings: Any
       lake: ParquetLake
       claude: ClaudeClient | None
       ancestry: LineageStore | None
       provider: MarketDataProvider | None = None     # NEW
       mutator: SpecMutator | None = None             # NEW
       web_researcher: WebResearcher | None = None    # NEW
       hypothesis_sandbox: HypothesisSandbox | None = None  # NEW
       workspace_builder: WorkspaceBuilder | None = None    # NEW
       evaluator: ResearchEvaluator | None = None     # NEW
   ```

2. **Add a `build_full_run_context()`** (new function, 18 LoC) that constructs all 10 fields,
   leaving the existing `build_run_context()` alone for backward compat (used by 2 places).

3. **Add 5 thin single-constructor helpers** to `run_context.py` so the **5 sites that
   only need 1–2 objects** don't pay the cost of a 10-object context:

   ```python
   def build_lake(settings) -> ParquetLake: ...        # 1 line
   def build_ancestry(settings) -> LineageStore: ...   # 1 line
   def build_claude(settings) -> ClaudeClient: ...     # 1 line
   def build_provider(settings, lake) -> MarketDataProvider: ...
   def build_mutator(settings, claude) -> SpecMutator: ...
   ```

   This means **no call site is forced to take the full context** — sites that just need
   `ancestry` still get a 1-liner. **This is the smaller-delta part: zero behavior change
   for the 5 one-shot sites; the 6 multi-constructor sites collapse.**

### 6c. Concrete diff (smaller-delta; not a full rewrite)

```python
# siglab/orchestration/run_context.py — diff vs. current
# (header unchanged; dataclass adds 5 optional fields; build helpers appended)

def build_provider(settings, lake: ParquetLake) -> MarketDataProvider:
    return MarketDataProvider(settings, lake)


def build_mutator(settings, claude: ClaudeClient) -> SpecMutator:
    return SpecMutator(settings, claude)


def build_full_run_context(
    settings: Any,
    *,
    require_claude: bool = True,
    require_ancestry: bool = True,
    build_provider_chain: bool = True,
) -> RunContext:
    lake = ParquetLake(settings.data_lake_dir)
    claude = ClaudeClient(settings) if require_claude else None
    ancestry = LineageStore(settings.ancestry_db_path) if require_ancestry else None
    provider = MarketDataProvider(settings, lake) if build_provider_chain else None
    mutator = SpecMutator(settings, claude) if claude is not None else None
    web_researcher = WebResearcher(settings, lake) if build_provider_chain else None
    return RunContext(
        settings=settings,
        lake=lake,
        claude=claude,
        ancestry=ancestry,
        provider=provider,
        mutator=mutator,
        web_researcher=web_researcher,
    )
```

**Net LoC change in `run_context.py`:** +35 LoC (helpers + dataclass fields).
**Net LoC change at the 11 call sites:** **−230 LoC** (collapse 5–10 line blocks each).
**Net per project: −195 LoC.**

### 6d. Risk analysis for PR-7

- **Test surface**: `tests/test_run_context.py` exists (small) + indirect via
  `tests/test_cli_run.py`, `tests/test_cli_benchmark.py`, `tests/test_cli_deploy.py`,
  `tests/test_orchestration.py`. All 4 must pass post-merge.
- **Import-order risk**: Adding new `Optional` fields to `RunContext` is **backward
  compatible** at the dataclass level. Old call sites that destructure by name still
  work. Sites that pass positional args break — but `search` shows all current
  construction uses `RunContext(settings=..., lake=..., ...)` keyword args.
- **No live-network risk**: PR-7 touches plumbing only. No HTTP call shapes change.

---

## 7. The `asyncio.gather` Opportunities That Remain (8 Sites)

All 8 `asyncio.gather` sites are real concurrency wins — **none are duplicates that
should be merged**. They are listed here for reference; the smaller-delta plan is to
**document** them and **NOT** touch them in this wave (they work and have tests).

| # | File:Line | Site | Notes |
|---|---|---|---|
| 1 | `siglab/cli/evidence.py:71–84` | `etf_historical_inflow + featured_news_pages + featured_news_by_currency_pages` | Real 3-way fan-out for evidence build. **Keep.** |
| 2 | `siglab/cli/paper.py:71` | `*(feeds.fetch_klines(sym, "1m", limit=5) for sym in open_symbols), return_exceptions=True` | Per-symbol kline fan-out for paper-status. **Keep.** |
| 3 | `siglab/data/feeds.py:477` | `discover_stable_pt_markets + discover_rotation_pt_markets + discover_lending_pt_markets` | Real 3-way universe fan-out. **Keep.** |
| 4 | `siglab/data/feeds.py:715` | `*[_fetch_one(row) for row in markets]` | Per-market PT history fan-out. **Keep.** |
| 5 | `siglab/data/sosovalue_client.py:328` | `*(self.featured_news(page_num=...) for page_num in pages)` | Per-page news fan-out. **Keep.** |
| 6 | `siglab/data/sosovalue_client.py:381` | `*(self.featured_news_by_currency(page_num=...) for page_num in pages)` | Per-page currency news fan-out. **Keep.** |
| 7 | `siglab/llm/llm.py:471` | `*(self._execute_tool_call(tool_call=tc, tool_map=tool_map) for tc in tool_calls)` | Per-tool-call parallel dispatch in Claude loop. **Keep.** |
| 8 | `siglab/research/web.py:106, 197` | `*[self._research_query(query) for query in queries]` + `*[self._explore_result(result) for result in top_results]` | Web research fan-out. **Keep.** |

**Sites #1, #3, #4, #7 are the highest-leverage** for test-speedup (they fan out to the
real I/O subsystem). For the user's <10 s test goal, the right move is to:

- **Keep the production code as-is** (it works).
- **Replace the actual I/O with `respx` mocks in tests** so `asyncio.gather` still
  exercises the scheduling, but each branch returns in <1 ms.
- **Use `pytest-xdist -n auto`** so the 8 sites' tests run in parallel processes.
- **Add `asyncio_mode = "auto"` to `pyproject.toml`** so `pytest-asyncio` does not
  require explicit `@pytest.mark.asyncio` on every test.

These 4 changes (no production edits) are where the **7x speedup comes from** — not
from LoC reduction.

### Sites that are *not* `asyncio.gather` but have similar opportunity

- `siglab/tui/screens/risk.py:392, 406–417` — `asyncio.create_task(self._ws_risk_loop())`
  with manual exponential backoff inside. **Candidate** for `utils.retry_with_backoff()`.
- `siglab/dashboard/ws.py:132` — `asyncio.create_task(_periodic_risk_push(websocket))`
  with `add_done_callback(risk_push_tasks.discard)`. **Could share** a
  `utils.fire_and_forget_task(coro, *, on_error=logger.warning)` helper.
- `siglab/tui/api_client.py:51–80` — single-retry on transient HTTP errors with
  `asyncio.sleep(0.5)`. **Could share** a `utils.retry_request(client, method, path, **kw)`
  helper that uses the backoff helper from #4.

These three would save **~50 LoC** combined; they are part of the **second-wave** of
6 backlog PRs that brings the realistic total to ~1,100–1,600 LoC.

---

## Summary

- **Total siglab/ LoC:** 49,819 (91 files).
- **7-PR headline plan** delivers **~520 LoC net reduction** (~1.0 %) by collapsing the
  7 most-duplicated patterns. Each PR is <50 LoC net diff, low-risk, and lands in
  <500 LoC of touched code.
- **Realistic 13-PR expanded plan** delivers **~1,100–1,600 LoC** (~2.2–3.2 %).
- **Original 10–15 % target (5,000–7,500 LoC) is NOT achievable from genuine
  de-duplication** — `siglab/` is mostly feature code, not duplicated boilerplate.
  Hitting 10–15 % would require deleting whole features, which is out of scope for a
  7-PR cycle.
- **The user's 7x test-speedup goal is orthogonal to LoC** — it requires
  `pytest-xdist` + `respx` + `asyncio_mode=auto` + `conftest.py`-cached
  `RunContext`. Every LoC reduction here **compounds** the test-speedup wins by
  shrinking the import graph.
- **The 11 RunContext sites are the single largest win** (PR-7 alone saves ~195 LoC
  net and removes the most error-prone pattern in the codebase).
- **The 8 `asyncio.gather` sites are NOT duplicates** — keep all of them; they are
  the highest-leverage test-speedup levers.
