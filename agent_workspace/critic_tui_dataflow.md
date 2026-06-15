# SigLab TUI Data-Flow Critic Report

Scope: `siglab/tui/data_views.py`, `siglab/tui/loading.py`, `siglab/tui/__init__.py`, `siglab/tui/__main__.py`.
Smoke test: `python3 -c "from siglab.tui.data_views import *; print('ok')"` → `ok` (0.51s).
Brief: brutally honest critic of data flow, loading states, and entrypoint. Must handle 4 failure modes (API down, slow, partial, wrong-shape).

---

## 1. Data view layer — contract between `api_client` and widgets

**Headline finding: there is *no* contract.** The `data_views.py` module defines ten frozen dataclass wrappers (`TickerView`, `SymbolEntry`, `KlineView`, `OrderBookView`, `PositionView`, `OrderView`, `PnlSnapshot`, `RiskSnapshot`, `GraphNode`, `GraphEdge`, `StrategyEntry`) and one helper (`closes_from_klines`), all wrapping raw dicts with property-based zero-copy access. The design is fine on paper; in practice the contract is one-sided.

- **Inbound contract is implicit.** `TickerView.from_dict` (data_views.py:34) accepts *any* dict, then `.get()`-chains each field with silent defaults (e.g. `data_views.py:39` returns `"?"` for a missing `symbol`; `:43` returns `0` for a missing `last_price`; `:155` returns `"?"` for a missing `position.symbol`). The cost of schema drift is therefore "rendered wrong silently" rather than "rendered broken loudly". A wrong-shape response from the API becomes a wall of `?` and `0.0` — the user has no idea the data is broken.
- **Property access is unbounded.** None of the `_raw.get(...)` calls validate types. `float(self._raw.get("volume", 0))` (data_views.py:53) raises `ValueError` if the API ever returns `"volume": "1.2K"` (a string with a K-suffix is a common exchange shape). One bad field kills the entire view because dataclass `frozen=True` doesn't catch attribute errors; the surrounding `try/except` is the caller's problem and there is *no* caller-side try/except that we can see in the four files in scope.
- **Consistency is uneven.** `TickerView` checks both camelCase (`lastPrice`) and snake_case (`last_price`) keys (data_views.py:43); `KlineView` does not (data_views.py:99-115 only looks for `open/high/low/close/volume` in one case). `PnlSnapshot` and `OrderView` use snake_case; `TickerView` is bilingual. The contract changes shape per view.
- **OrderBookView copies unnecessarily.** `:133` does `tuple(data.get("bids", []))` and `:134` does the same for asks. For a depth snapshot of 100+ levels, this allocates two new tuples on every refresh — the module's own docstring (data_views.py:1-12) brags about zero-copy. `closes_from_klines` (:426) is similarly non-zero-copy (it materialises a new tuple of floats).
- **No provenance.** None of the views carry a timestamp, a fetch_id, a stale flag, or an error sentinel. Widgets cannot tell "this is fresh data" from "this is the cached value from 30 s ago that I'm still showing because the refresh failed". This is the single biggest gap for the 4-failure-mode brief.
- **No `__post_init__` validation.** `RiskSnapshot.from_dict` (data_views.py:265) accepts `composite_score=None` and `correlation_matrix=None` without checking the alternative "missing entirely" path. `PnlSnapshot.from_dict` (data_views.py:236) coerces `int(d.get("open_position_count", 0))` but does not check whether `realized_pnl` is a number-string like `"1.2K"`.
- **The `raw` property undermines the wrapper.** Every view exposes `self._raw` via a `.raw` property (data_views.py:56, 118, 169, 222, 311, 348, 414). This means consumers can always bypass the wrapper and reach for the underlying dict, so a refactor that tightens validation in the view layer will leave silent escape hatches everywhere.

**Verdict.** The view layer is a *cosmetic* layer — it gives you attribute access instead of `dict.get`, and silently hides schema drift. It is not a contract. A `pydantic.BaseModel` or a TypedDict-with-runtime-validator would actually *be* a contract; the current code is typing sugar.

---

## 2. Loading state — does the TUI show "Loading…" when API > 2 s?

**The widget exists, the wiring does not — within the four files in scope, the answer is: not visible to the user.**

- `LoadingIndicator` (loading.py:19) is a `textual.widgets.Static` with a `loading: reactive[bool]` (loading.py:33). Setting `loading = True` cycles a braille spinner (loading.py:54 sets a 100 ms tick; :59 advances `_spinner_idx`; :66 renders `" {frame} Loading…"` in `ACCENT_GREEN`).
- **No threshold.** `:33` flips on a boolean, not a duration. There is no "show spinner only after 2 s" gate, no "auto-show on fetch start" hook, no `set_loading(True)` from the app layer visible in the four files. The threshold is a discipline the call sites must enforce; nothing in the four files does.
- **Tick runs unconditionally.** `on_mount` (loading.py:52) sets a 100 ms interval even when `loading=False`. The interval keeps firing for the entire app lifetime, calling `self.refresh()` only when `self.loading` is true (loading.py:58 short-circuits the body). The cost is one timer per `LoadingIndicator` instance; the bigger cost is the `refresh()` call when active re-paints the widget ten times a second whether or not anything is changing.
- **No "stale" or "last successful fetch" state.** `:67-69` renders `status_text` (a free-form string set by the caller) when idle. There is no built-in concept of "last refreshed at HH:MM:SS", no automatic transition to a stale indicator after N seconds, and no built-in error state. A `LoadingIndicator` with `loading=False, status_text=""` renders `Text("")` — i.e. a blank widget.
- **Partial data case (1 endpoint down) is invisible.** If 4 of 5 endpoints succeed and 1 fails, the 4 successful widgets will show their last successful data (or `?`/`0.0` if it's their first cycle); the 1 failed widget will silently show `?`/`0.0`. There is no per-widget error badge, no "1 endpoint failed" banner, and no way for the user to see the 5th endpoint is broken.

**Verdict.** The widget is a thin, well-styled spinner. The "loading state" of the TUI is whatever the app-level call sites do with it, and the four files in scope give us no evidence of app-level wiring.

---

## 3. Entry flow — bootstrap & API URL configurability

- `__main__.py` is 11 lines. `main()` instantiates `SigLabTUI().run()` with no arguments (line 7). No env-var read, no CLI parser, no config-file read, no prompt. There is no way to override the API URL at startup from the entrypoint in scope.
- `__init__.py` exports `SigLabTUI` via `__getattr__` (lines 6-11) — lazy import from `siglab.tui.app`. This is fine; the actual config layer (if any) lives in `siglab.tui.app`, which is out of scope for this report.
- **No `argparse`, no `click`, no `typer` call.** The four files in scope import only `from siglab.tui.app import SigLabTUI`. The entrypoint is hard-coded to the default API URL that `SigLabTUI` itself constructs.
- **Smoke test passes**, but only because `data_views` has no `api_client` import. The moment a real launch goes through `__main__.py`, the API URL is whatever the app baked in.
- **No way to point at a staging server, a tunnel, or a test fixture** without editing source. For a system whose whole job is monitoring a remote service, this is a sharp edge: a user behind a VPN, on a different network, or testing against `http://localhost:9999` cannot re-target without code changes.

**Verdict.** Entry flow is "open the app, pray the default URL is reachable." Configurability belongs in `__main__.py` (or the app), and in scope it is absent.

---

## 4. Failure handling — what does the user see?

The four files in scope are *not* the whole TUI; failure behaviour is mostly determined by the call sites in `app.py`, the api_client, and the widgets. With the four files in scope, here is what we can definitively say:

### a. API server down
- `from_dict` classes do not catch any exception. A network exception (`httpx.ConnectError`, `requests.ConnectionError`, `asyncio.TimeoutError`) raised in the api_client bubbles through `from_dict` (which is pure dict-wrapping, not the call site) all the way to whatever worker is calling it. Within the four files in scope, there is *no* error handling, no fallback, and no user-visible message. **Outside scope:** whatever the app does (crash, modal, silent empty panel), we cannot verify here.
- The data views will still accept the *previous* cycle's data only if the caller retains the last successful dict and re-wraps it on failure. None of the view classes store such history.

### b. API server slow (> 10 s)
- `LoadingIndicator.loading` is a boolean. There is no time-based transition. If the app layer sets `loading=True` before each fetch and forgets to set it `False` on success, the spinner will spin forever. If the app layer doesn't touch `loading` at all, the user sees a frozen panel with whatever was last rendered.
- The 100 ms timer (loading.py:54) means a *visible* spinner costs 10 paints/second, so any "show a loading hint after 2 s" UX has to be implemented at the call site.

### c. API returns partial data (1 endpoint down)
- Each view independently wraps its slice. The ticker widget will show 30 tickers; the risk widget will show `composite_score=None, sub_scores={}, drawdown_history=()` (data_views.py:265-277 defaults). The user sees a healthy ticker panel next to a blank risk panel. **There is no banner or "X of Y endpoints failed" message anywhere in the four files in scope.**

### d. API returns wrong-shape data (schema change)
- **Silent corruption.** `TickerView` (data_views.py:39-58) treats every missing or wrong-type key as `?` / `0.0`. A schema change that drops `lastPrice` in favour of `last_price` (or vice versa) — both of which the view already accepts — works. A schema change that drops both, or wraps numbers in strings, produces `?` and `0.0` in the UI. There is no `__post_init__` validation, no `validate=True` constructor flag, no error class. The user sees plausible-looking zeros. **This is the worst-case behaviour for a financial dashboard.**

### e. User has no network
- Same as (a) — the api_client raises a connection error, no view catches it, no fallback. The four files in scope give us no "offline mode" or "stale data" indicator.

**Summary of (4)**: The four files in scope handle none of the four failure modes explicitly. They provide the *plumbing* (silent zero-copy wrappers, a generic spinner widget) but no *policy* (no timeouts, no stale flags, no validation, no error surfacing).

---

## 5. The 5 worst data-flow frictions (with file:line)

1. **`OrderBookView.from_dict` does a non-zero-copy wrap on a hot path.** data_views.py:133-134 — `tuple(data.get("bids", []))` and `tuple(data.get("asks", []))` materialise new tuples of lists on every refresh. For a 100-level book, that's ~200 list references copied per cycle, twice a second. The module's own docstring (data_views.py:1-12) calls out zero-copy as the design principle; this is the most flagrant violation because it lives on the order book — the single highest-cardinality data feed in any trading UI.
2. **Silent schema-drift masking in `TickerView` and friends.** data_views.py:39 (`return str(self._raw.get("symbol", "?"))`), :43 (last_price defaults to 0), :155 (position symbol defaults to "?"), :213 (order status defaults to "?"). A wrong-shape API response renders as a panel of `?` and `0.0` with no error indication — the user is told nothing.
3. **No `__post_init__` type coercion guard.** data_views.py:265-277 (`RiskSnapshot.from_dict`) and :236-243 (`PnlSnapshot.from_dict`) coerce user input without validation. `int(d.get("open_position_count", 0))` and `float(d.get("total_pnl", 0))` will raise `ValueError` on bad strings, killing the entire view construction with no in-app recovery path.
4. **Spinner timer fires for the entire app lifetime regardless of state.** loading.py:52-54 — `on_mount` calls `self.set_interval(0.1, self._tick_spinner)`. There is no `on_unmount` cleanup, and the interval keeps ticking even when `loading=False` and `status_text=""` (i.e. the widget is invisible). For a TUI that mounts multiple `LoadingIndicator` instances (header, footer, per-panel), this is one redundant timer per widget per session.
5. **No per-view freshness, timestamp, or error metadata.** data_views.py:23-414 — every view is a frozen snapshot of *whatever dict it was handed*. There is no `fetched_at: float`, no `stale: bool`, no `error: str | None`. Widgets cannot distinguish "this is the live value" from "this is the cached value from 30 s ago that I'm still showing because the refresh failed". This is the structural reason the 4-failure-mode brief fails: the data layer has no way to *represent* failure.

(Honourable mention: `closes_from_klines` at data_views.py:420-426 is called "zero-copy" in its docstring but materialises a fresh tuple of floats — minor compared to the five above.)

---

## 6. The 5 smaller-delta fixes (1-line patches)

1. **data_views.py:133** — change `bids=tuple(data.get("bids", []))` to `bids=tuple(data.get("bids") or ())` (and same for `asks` one line down) to drop a defensive copy when the key is missing or `None`. Marginal but free.
2. **data_views.py:420** — change the docstring's "zero-copy for tuple of floats" claim to "single tuple materialisation" so the comment matches the code.
3. **loading.py:54** — change `self.set_interval(0.1, self._tick_spinner)` to `self.set_interval(0.1, self._tick_spinner, pause=not self.loading)` (or guard `_tick_spinner` with an early return) so the timer doesn't run when not needed. Single-line.
4. **loading.py:67-69** — change `if self.status_text:` to `if self.status_text or self.last_error:` and add a `last_error: reactive[str] = reactive("")` so the widget can show "stale" or "fetch failed" without forcing the caller to compose the string. Two small lines, but the second is one line of code.
5. **__main__.py:6-7** — change `def main() -> None: SigLabTUI().run()` to read `os.environ.get("SIGLAB_API_URL")` and pass it through, so a user can `SIGLAB_API_URL=http://localhost:9999 python -m siglab.tui` to re-target without code edits. Two lines including the import.

(Honourable mention: adding `fetched_at: float = 0.0` to `TickerView.from_dict` as `return cls(_raw=d, fetched_at=time.time())` would be the single most valuable 1-line change in the data layer, but it requires touching the dataclass definition and every `from_dict` call — borderline 1-liner.)

---

## Final verdict

The TUI's data-flow layer is a *thin* layer that solves a styling problem (attribute access vs. `dict.get`) but not a *contract* problem. The loading widget is a thin, well-styled spinner with no threshold, no freshness, and no error state. The entrypoint is a 7-line hard-coded launch with no configurability. **The TUI does not handle any of the 4 failure modes explicitly.** The fixes are 1-line patches; the underlying gap is that the data layer has no concept of "this fetch failed" or "this data is N seconds old", so the rest of the stack cannot show it.
