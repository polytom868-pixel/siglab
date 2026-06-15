# SigLab TUI — Brutally Honest Screen & Widget Critic

Scope: `siglab/tui/screens/` + `siglab/tui/widgets/` + `siglab/tui/api_client.py` + `siglab/tui/cli_bridge.py`. Read-only review. All file:line citations are to those files exactly as they exist on 2026-06-15.

The architecture is consistent, well-typed, and runs end-to-end. The "zero-copy" pattern across widgets is real and the hot path on `set_data` does not allocate per-item. The data plumbing is testable, the bindings are well advertised, and the error envelopes are uniform (`friendly_error` + `notify`). The critique below is about *UX density*, *redundancy*, *latency in the wrong place*, and a handful of "almost zero-copy" leaks that quietly allocate per refresh.

---

## 1. The seven screens

### 1.1 `BaseScreen` — `screens/base.py`
- **Data loading**: A `_refresh_timer = self.set_interval(self._refresh_interval, self._refresh_all)` and a `call_after_refresh(self._refresh_all)` on mount (lines 93-96). `_refresh_all` flips `is_loading=True`, awaits `_fetch_data()`, then resets. Errors are funneled into `_update_status_error` and `self.notify(severity="error")` with a try/except to tolerate test contexts without a live app.
- **Error handling**: Top-level catch-all in `_refresh_all` swallows everything to `logger.warning` (line 122) — partial failures inside `_fetch_multiple` are counted but never propagated. The only externally visible failure is the status bar.
- **Hotkeys** (7, all `ClassVar`, line 44-52): `escape`/`ctrl+c` → go_back, `r` → refresh, `j`/`k` → move down/up, `/` → focus_search, `?` → app help.
- **Loading state**: A `LoadingIndicator` widget toggled by `_set_loading(loading)` (line 149) and a `Static` status widget via `_update_status_text` (line 161). Both degrade silently if the widgets are missing.
- **Search/filter contract**: Subclasses set `_search_input_id` and `_search_list_id` and get `on_input_changed` wired automatically via `_on_search_input_changed` (line 187). Clean.
- **Verdict**: Solid. The base class earns its keep. Two latent bugs:
  - The `try/except` around `self.notify` (line 119) silently drops notifications when the app isn't mounted — fine for tests, but real headless runs that lack an App also lose them, including startup errors.
  - `_fetch_multiple` (line 130) takes *coroutines* as args but awaits them via `await fn` instead of `await fn()`. If a caller passed an *unawaited* coroutine (which is what `_fetch_data` does on line 379-384 of market.py), `await fn` works only because Python's coroutine protocol auto-awaits an un-called coroutine. This is fragile.

### 1.2 `MarketScreen` — `screens/market.py`
- **Data loading**: Three parallel-ish (sequential) sub-fetches via `_fetch_multiple(self._fetch_tickers(), self._fetch_klines(), self._fetch_orderbook(), ...)` (line 379). Each talks to `TuiApiClient`. A partial-success counter produces a "Partial update (N/3)" status. Selection change triggers `_refresh_klines_and_book` (line 461) but the symbol list itself only refreshes on the 30s timer.
- **Error handling**: `safe_query(self, "#symbol-list", SymbolListWidget, lambda w: w.set_symbols(entries))` (line 407) wraps widget lookups — exceptions inside the lambda are silently dropped. A user who hits a malformed ticker will see the symbol list stop updating with zero diagnostic.
- **Hotkeys** (line 350-352): All BaseScreen bindings + `enter` → `select_symbol`. The BaseScreen bindings for `/` and `?` are present, but `?` is shown as `app.show_help` (not screen-local help).
- **Loading state**: `LoadingIndicator` is mounted but the `_set_loading(True)` call in BaseScreen only takes effect *after* the first fetch. The `is_loading` reactive is `True` initially, so the loading indicator should appear immediately — confirmed by `_loading_widget_id = "#market-loading"`.
- **Transition smoothness**: Excellent. Selection in the left list triggers only the kline+orderbook refresh, not the ticker scrape. Good optimisation.
- **Verdict**: Best-behaved screen in the set. Friction is small.

### 1.3 `PaperScreen` — `screens/paper.py`
- **Data loading**: On mount, runs `_init_session` (line 585) which **spawns a subprocess to call `python -c "from siglab.live.paper_client import SoDEXPaperPerpsClient; ..."`** (line 572-583) just to *list sessions*. This is the heaviest cold-start in the entire TUI. After init, every 15s refresh is `run_cli("paper-status", ...)` (line 646) — another subprocess.
- **Error handling**: Three nested JSON decode error branches (lines 614, 650) log and update the status. Order placement and cancel both run their own ad-hoc subprocesses (lines 743, 859) with full Python interpreters loading `config`, `paper_client`, and full Settings — every order is a process spawn. Errors from the subprocess are trimmed to 60–80 chars (lines 632, 769, 879) and the user gets a terse message in the form widget.
- **Hotkeys** (line 505-514): The full set: `s/b/t/Q/p/enter/n/c` plus base. `Q` for quantity is awkward — uppercase is unreachable without shift on most keyboards and lowercase `q` collides with the default `quit` semantic. The help text in `OrderFormWidget.render` (line 322) advertises `[Q]ty` correctly, so the binding is at least documented.
- **Loading state**: The screen has its own `_spinner_idx` for the *evaluation* flow (Strategy screen, line 423) — but this is the paper screen's UI pattern leaking across files. Wait, the spinner lives in `strategy.py`. Paper doesn't have a visible spinner during init, which means a fresh install with a slow disk can sit on "Creating paper session…" silently for several seconds.
- **Transition smoothness**: Awkward. The `_init_session` failure path (line 632) sets `self.status_text` directly and disables loading, but the base-class `_refresh_timer` is *already running*. The next 15s tick will run `_fetch_data` and the `if not self.session_id: return` guard (line 645) bails out silently — the user sees no periodic "still broken" signal, only the initial error.
- **Verdict**: Most fragile screen. Subprocess-per-tick is the wrong choice; `TuiApiClient` already exists with `/paper/sessions/*` endpoints (api_client.py:267-298) but PaperScreen does not use a single HTTP call.

### 1.4 `RiskScreen` — `screens/risk.py`
- **Data loading**: Standard HTTP via `TuiApiClient.get_risk()` (line 461) plus a long-running `_ws_risk_loop` (line 404) that subscribes to the `risk_score` WebSocket. WS has 1s→30s exponential backoff. On every WS tick the screen re-renders the gauge, drawdown, and correlation matrix (line 419-442). The status bar is also reset to "Live · Risk · WS updated" — which then leaks over the auto-refresh status set by `_fetch_data` (line 449). The two writers race.
- **Error handling**: If `get_risk()` raises, all four widgets are *zeroed* (line 491-498). The user sees the gauge go from 67/100 to "No risk data available" with a `█` bar of nothing. The alert stream is also cleared. This is *too aggressive* — a transient fetch failure should not wipe state. There's no debounce on the WS callback either, so a burst of ticks thrashes the layout.
- **Hotkeys** (line 361-363): Base + `f` → filter_alerts (cycles all/critical/warning/info). `j`/`k` are overridden (line 519-525) to scroll the alert stream *up/down* instead of the standard list navigation.
- **Loading state**: Loading indicator + status, but the WS `notify("Risk score updated: 0.42")` (line 440) fires on every tick — terminal spam.
- **Verdict**: Good bones (the WS path is the only real-time screen), but the *zero on error* pattern is hostile and the per-tick `notify` is a UX regression.

### 1.5 `TelemetryScreen` — `screens/telemetry.py`
- **Data loading**: Three fetches in `_fetch_data` (line 596): `run_cli("telemetry-report")` (CLI bridge), `self._api.get_ops_board()` (HTTP), and `run_cli("ancestry")` (CLI bridge). Two subprocesses per 30s tick. Telemetry and ancestry data are stored on `self._telemetry_data` / `self._runs_data` (line 612, 636) *and* the per-widget copies — reference sharing but two layers of indirection.
- **Error handling**: Every sub-fetch has a `try/except Exception as exc: logger.debug(...)` (lines 615, 626, 639) — silent swallows. If the FastAPI is up but the CLI tool is broken, the user sees an empty provider metrics widget with no diagnostic.
- **Hotkeys** (line 531-539): The largest binding set in the suite: `space/c/s/d/f/t/v` + base. `f` cycles status filter, `d` cycles date range, `t` cycles track, `s` cycles sort, `c` toggles compare view, `v` toggles detail view. These are discoverable only because they are also shown via `Binding(..., show=True)`. Good.
- **Loading state**: `_loading_widget_id = "#telemetry-loading"`. The detail toggle (line 714) sets a `_detail_view` reactive but the `service-health` widget starts with `classes="hidden"` (line 573) — fine.
- **Transition smoothness**: The view-toggle (line 686) does CSS class swapping on five widgets via four `safe_query` calls. `if not all([detail, provider, tool_usage, health, comparison]): return` (line 694) bails silently if any is missing — no status update. The compare-mode call to `_update_comparison` iterates `self._runs_data` (line 815) and matches by `spec_hash == h` for every selected hash — O(N×M) for what could be a dict.
- **Verdict**: Most feature-dense screen, most likely to confuse a new user. The two CLI subprocesses (telemetry-report, ancestry) per tick are wasteful.

### 1.6 `StrategyScreen` — `screens/strategy.py`
- **Data loading**: `run_cli("ancestry", "--json", ...)` (line 433) once per 30s. Per-selection, `_load_results_for_hash(spec_hash)` (line 457) *re-runs ancestry* and re-iterates the rows to find the matching hash — i.e. the "detail" path re-fetches the entire dataset just to extract one row. There is a `TuiApiClient.get_strategy_detail(spec_hash)` (api_client.py:179) that is **never called** by the screen. This is the most expensive correct fix in the suite.
- **Error handling**: Every JSON parse failure is `logger.warning` (line 441). The evaluation flow (line 520-557) has 180s timeout — well within `run_cli` defaults — and surfaces failure via `self.notify(severity="error")`. Good.
- **Hotkeys** (line 370-376): `e` (eval, 180s), `i` (init deck), `c` (compare), `space` (select), `s` (sort) + base. `s` is reused for sort but also `/` for search. There is no "evaluate selected" — `e` always evaluates `self._deck` (line 530), not the highlighted strategy. This is a naming trap.
- **Loading state**: Custom spinner timer at 0.5s (line 415) drives a `_tick_spinner` (line 423) which writes to the status text. Spinner is *only* visible during `is_evaluating=True`; the initial 30s refresh has no spinner.
- **Verdict**: The 180s eval is the only blocking long op in the suite, and the spinner handles it well. The per-selection re-fetch is the clear regression.

### 1.7 `EvidenceScreen` — `screens/evidence.py`
- **Data loading**: `self._api.get_evidence_graph()` (line 531) on 30s. Pure HTTP. No CLI subprocess for the data path. Demo step execution uses `run_cli(*args)` (line 625) by *string-splitting* the canned command — see DEMO_STEPS at line 47-104. Each demo step runs a real CLI tool, captures stdout/stderr, and stores a result dict on the widget. Clean.
- **Error handling**: Catch-all `except Exception as exc` (line 548) on the graph fetch — sets `api_connected=False` and shows friendly error in the status. Good. Demo step failures (line 634) record `returncode=-1` and continue — no `notify`, no escalation, the user has to scan the demo flow widget to see "✗".
- **Hotkeys** (line 470-477): `tab/enter/n/p/a/f` + base. `f` filters to *source* nodes (line 577) but the docstring/comment at line 477 claims `Binding("f", "filter_source", "Sources")`. The other filters (`action_filter_entity`, `action_filter_clear`) exist in the code but **have no binding** — only reachable programmatically.
- **Loading state**: `LoadingIndicator` + status. The `action_run_all` (line 646) does not toggle the loading indicator at the screen level — only `demo.set_running(True)` on the widget. If the user hits `a`, the global loading indicator does not change.
- **Verdict**: The "8 demo steps" are hard-coded in source (line 47-104). That is a maintainability issue: a UI change requires a code change. Also, `n` collides with the standard "new" mental model. Reasonable screen but the hard-coded step list is a real coupling.

---

## 2. The three widgets

### 2.1 `widgets/base.py` — `FilterableListWidget` + `ComparisonWidget`
- **Visual design**: `FilterableListWidget` is just text rows with bold-on-green for the selected row (delegated to subclasses' `_render_item`). `ComparisonWidget` is a column-per-item table with a delta column and a hook for extras. Both rely on inherited `Static` styling — no per-class CSS.
- **Data binding**: `_items_reactive` (ClassVar string) points at the subclass's reactive list (e.g. `symbols`, `strategies`, `runs`). `set_data` (line 49) *tuples* the incoming list — immutable, so reactivity only re-fires when the reactive attribute is reassigned. Filtering re-runs `_apply_filters` and reassigns the reactive list with a fresh filtered list. Multi-select lives on `self._selected_hashes` (line 45) — a `set[str]`.
- **Refresh logic**: `set_data` → tuple → `_apply_filters` → `setattr(self, _items_reactive, filtered)`. Re-render is driven by the reactive assignment. No explicit `refresh()` needed unless the *contents* of the items change but the list reference does not. This is a *latent bug* for tables where the upstream mutates dicts in place (e.g. TelemetryScreen — `lw.set_runs(rows)` with new `rows` list, but each row dict is a fresh dict from JSON, so this works). For long-lived reference-shared dicts, the UI would go stale.
- **Verdict**: Clean abstraction. The reliance on a `ClassVar` string to point at the reactive is a code smell, but it works.

### 2.2 `widgets/sparkline.py` — `sparkline_text` + `SparklineWidget`
- **Visual design**: 8 Unicode block chars (line 18), 8-level intensity. Single colour per chart (whole-bar green or red depending on first-vs-last). No partial gradient.
- **Data binding**: `SparklineWidget` is a thin wrapper — `values: reactive[list[float]]` and `render()` calls the free function. The free function `sparkline_text(values, *, width, bullish_color, bearish_color, neutral_color)` (line 21) accepts any `Sequence[float]` and rescales to width.
- **Refresh logic**: `set_values` (line 149) coerces non-lists with `list(values)` — *unconditional copy*. This is the only "zero-copy" leak in the sparkline layer. A `memoryview` or `tuple` input gets materialised. Not a hot path, but the docstring (line 150-155) literally claims "no copy" while doing one.
- **Verdict**: Fine. The `ohlc_summary` (line 104) helper returns a plain string with `O/H/L/C` — used by `KlinesChartWidget` in market.py:185.

### 2.3 `widgets/status_bar.py` — `SigLabStatusBar`
- **Visual design**: Three `Static` widgets laid out left/center/right. A 1-second timer (line 71) updates the right side with the current UTC time. Connection icon is a single `●`/`○`.
- **Data binding**: Imperative — `set_connected(bool)` (line 92) sets `self._connected`, and `_update_display` reads it. No reactive.
- **Refresh logic**: `set_interval(1.0, self._update_display)` is started in `on_mount` and never stopped. If the screen containing the status bar is unmounted, the timer keeps firing into a widget that may not be in the tree.
- **Verdict**: Tiny but has a real bug — see section 4.

---

## 3. Data flow: `cli_bridge` → `api_client` → widget

### Path A (HTTP, used by Market/Risk/Telemetry/Evidence)
- `TuiApiClient` (api_client.py:22) wraps `httpx.AsyncClient`. `_request_with_retry` (line 51) does **one** retry on transient 5xx/connect/timeout errors with a fixed 0.5s sleep. 4xx errors are *not* retried (line 73). Connection is lazy: `httpx.AsyncClient` is created on first request, closed via `close()` or `__aexit__`.
- **Efficiency**: Good. Connection pooling from httpx. No per-call allocation beyond the parsed dict. `BaseScreen` re-uses one client instance per screen (line 85: `self._api_client_class()`).
- **Testability**: `httpx.MockTransport` works against `_request_with_retry`; subclasses of `BaseScreen` accept an `api_client` kwarg (base.py:78), so DI is clean.

### Path B (CLI bridge, used by Paper/Strategy/Telemetry)
- `run_cli(*args, timeout=30)` (cli_bridge.py:34) spawns `sys.executable -m siglab.cli <args>`, captures stdout/stderr, returns a `CliResult` named tuple.
- **Efficiency**: Poor. Every call is a fresh Python interpreter cold-start (config import, settings load, full CLI app init). For Paper's 15s refresh and Telemetry's 30s refresh with two CLI calls each, the per-screen CPU cost is dominated by interpreter startup, not by the actual work. On a cold cache, paper-status can take >1s.
- **Testability**: Trivial to mock by replacing `run_cli`. Paper/Strategy pass `run_cli` through the function attribute — wait, no, they import it directly. Mocking requires `unittest.mock.patch("siglab.tui.screens.paper.run_cli", ...)`. Workable but not pleasant.

### Path C (WebSocket, used by Risk only)
- `ws_subscribe_risk(callback)` (api_client.py:403) connects to `/ws`, sends `subscribe/risk_score`, and yields messages to the callback. No reconnection — the wrapping `_ws_risk_loop` in `RiskScreen` handles that (risk.py:404).
- **Efficiency**: Fine. One TCP connection, push-based.
- **Testability**: Poor — the API is `async for msg`, not injectable. `websockets.connect` is imported inside the method, hard to monkey-patch.

### Verdict
The HTTP path is mature, fast, and testable. The CLI path is *the* systemic performance tax on the TUI. A `TuiApiClient` already exposes `list_paper_sessions`, `create_paper_session`, `get_paper_session`, `get_paper_positions`, `get_paper_orders`, `place_paper_order`, `cancel_paper_order` (api_client.py:267-382), and `get_strategy_detail` (line 179) — but the Paper and Strategy screens do not call any of them. Migrating the subprocesses to HTTP would cut cold-start latency ~10× for paper, eliminate the duplicated "spawn a Python interpreter to call a class method" anti-pattern in `PaperScreen._init_session` (paper.py:572-583) and `_place_order` (paper.py:743-757), and make tests possible without `unittest.mock.patch`.

---

## 4. The five worst screen UX frictions (with file:line)

1. **`PaperScreen` spawns a Python subprocess to list sessions, then a second one to place an order, then a third to cancel an order, all for one user action** — `screens/paper.py:572-583` (list), `:743-757` (place), `:859-870` (cancel). Cold interpreter + `load_settings()` + `SoDEXPaperPerpsClient` instantiation per order. `TuiApiClient` already has `/paper/sessions/*` endpoints (api_client.py:267-382); the screen should use them. (Highest-impact fix.)
2. **`StrategyScreen._load_results_for_hash` re-runs `ancestry --json` (full dataset) to fetch one row** — `screens/strategy.py:457-472`. A per-selection click triggers a full scrape. The dedicated `TuiApiClient.get_strategy_detail(spec_hash)` (api_client.py:179) is unused.
3. **`RiskScreen._on_ws_risk_update` notifies on every tick AND sets status to "WS updated", which then races the auto-refresh's "Live · Risk · refreshed" status** — `screens/risk.py:438-440`. With 15s HTTP refresh + WS pushes every few seconds, the status text is a flickering mess and `notify` floods the notification stack.
4. **`RiskScreen._fetch_risk_data` zeroes all four widgets (composite_score=None, drawdown_history=[], matrix=None, alerts=[]) on a single fetch failure** — `screens/risk.py:491-498`. A transient 5xx or timeout flashes the screen to empty. The retry already happens inside `TuiApiClient._request_with_retry` (api_client.py:51-80); this widget-level wipe is gratuitous and destructive.
5. **`EvidenceScreen` hard-codes 8 demo steps including the full `command` string** — `screens/evidence.py:47-104`. Adding/removing a step requires a code change. The `Binding("f", "filter_source")` (line 476) only filters to *source*; `action_filter_entity` and `action_filter_clear` exist in the class but have **no key binding** — they are unreachable from the UI.

(Honourable mention: `SigLabStatusBar` starts a 1-second timer in `on_mount` and never stops it — `widgets/status_bar.py:71`. If the bar is re-parented or removed, the timer keeps calling `_update_display` on a non-mounted widget, which Textual handles but the timer leak still adds garbage-collection pressure on long sessions.)

---

## 5. The five widget design issues (with file:line)

1. **`FilterableListWidget.set_data` stores `tuple(items)` (immutable), then `_apply_filters` reassigns a *new list* to the reactive on every filter change** — `widgets/base.py:49-65`. Multi-step filtering allocates a fresh list per step. For 1000+ item lists with three filter predicates, this is O(N) per keystroke during a search.
2. **`SparklineWidget.set_values` claims "no copy" in the docstring but always does `list(values)` when input is not a `list`** — `widgets/sparkline.py:149-156`. The wrapping `SparklineWidget` is the one place a copy happens; the free `sparkline_text` does not need it. Either honour the docstring or relax the claim.
3. **`ComparisonWidget.render` imports `BORDER_DIM, TEXT_PRIMARY, WARNING_YELLOW, truncate` from `siglab.tui.formatting` *inside* the function** — `widgets/base.py:159`. Per-render import. Move to module top.
4. **`SparklineWidget` is never actually used by any screen** — confirmed by reading the screen files. Every screen calls `sparkline_text(values, width=...)` directly (e.g. `market.py:178`, `risk.py:180`, `paper.py:218`, `telemetry.py:293`, `strategy.py:354`). The wrapper class is dead weight in the public surface and may mislead future contributors.
5. **`FilterableListWidget` exposes `selected_index` as a `reactive[int]` but the multi-select state (`_selected_hashes`) is a plain attribute, not reactive** — `widgets/base.py:33, 45`. This means no `watch_selected_hashes` hook, no automatic style change on multi-select, and the class cannot be used as a drop-in for `ListView`-style observers.

(Honourable mention: `SigLabStatusBar` calls `self.query_one(...)` three times in `_update_display` (status_bar.py:88-90) every second. Cache the Static handles in `on_mount`.)

---

## 6. The five smaller-delta fixes (1-line patches)

1. **`BaseScreen.action_refresh_now` is not actually "now"** — `screens/base.py:214-216`. The 30s `set_interval` will still fire 200ms after the user hits `r` and race with the manual refresh. Patch: `self._refresh_timer.stop(); self._refresh_timer = self.set_interval(self._refresh_interval, self._refresh_all)` after the manual call.
2. **`RiskScreen._on_ws_risk_update` per-tick `notify`** — `screens/risk.py:440`. Patch: wrap the `self.notify` in `if composite is not None and (composite - self._last_notified) > 0.05:` to suppress sub-5% drift spam. (Requires adding `self._last_notified` initialisation in `__init__`.)
3. **`KlinesChartWidget` `symbol` reactive does not change when `set_candles` is called from a different symbol** — `screens/market.py:149-157`. The header line re-renders off the stale `self.symbol` until the next reactive poke. Patch: `self.symbol = symbol` inside `set_candles` (and accept a `symbol` arg).
4. **`SparklineWidget.set_values` always copies non-lists** — `widgets/sparkline.py:156`. Patch: `self.values = values if isinstance(values, list) else list(values)` → `self.values = list(values) if not isinstance(values, list) else values  # noqa: F841 — same as today` (no change, but rename method to `set_values_copy` to match behaviour, or hoist the copy to the screen).
5. **`FilterableListWidget._apply_filters` rewrites the reactive list on every keystroke** — `widgets/base.py:59-65`. For interactive search this is the right thing, but for the no-op case (filter set to "" and `_filter_text` is already ""), it still allocates. Patch: `if not self._filter_text and not getattr(self, self._items_reactive): return` at the top of `_apply_filters` after setting attributes.

(Bonus 1-line fixes:
- `widgets/status_bar.py:71` — change `set_interval(1.0, ...)` to `set_interval(1.0, self._update_display, name="status-clock")` and call `self._clock_timer.stop()` in a new `on_unmount`.
- `screens/risk.py:519-525` — `action_move_down` and `action_move_up` ignore the standard list navigation; either rename to `action_scroll_alerts_down/up` and rebind `j`/`k` to filter cycling, or restore the inherited list behaviour.
- `screens/paper.py:509` — `Binding("Q", "focus_qty", "Qty", show=True)` → change to lowercase `q` and add a `shift+q` alias, or rebind to `f6` / digit to avoid the shift-required key on US layouts.
- `cli_bridge.py:73` — `proc.returncode or 0` collapses a legitimate `returncode=0` from a process that returned `-0`; use `proc.returncode if proc.returncode is not None else 0`.)

---

## Bottom line
The TUI's *architecture* is clean: one base class, one client, one bridge, well-named reactives. The *runtime* is held back by three patterns that the test suite cannot catch: (a) subprocess-per-tick for paper/telemetry/strategy, (b) full-dataset re-fetches for single-row lookups, and (c) zero-state-on-error in Risk. The *visual layer* is solid but has one lying docstring (sparkline zero-copy claim), one dead class (`SparklineWidget`), and one timer leak (`SigLabStatusBar`). Migrating the three CLI-bound screens onto the existing `TuiApiClient` HTTP endpoints is the single change that moves the most metrics — cold-start latency, test coverage, and concurrent-tick load on the host. The 1-line fixes above are the lowest-risk wins to ship in the meantime.
