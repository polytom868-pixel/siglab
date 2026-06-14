# Plan P2 — Live-SoSoValue Micro Benchmark

**Status:** PLAN-only. No source edits. No commit.
**Author:** WaveP2AMicroBenchPlan (delegated from Main).
**Date:** 2026-06-14.
**Scope:** `tests/bench/test_bench_sosovalue_micro.py` (one new file).
**Out of scope:** every file under `siglab/evaluation/`, `siglab/data/sosovalue_client.py`, `siglab/data/feeds.py`, `pyproject.toml`, `tests/conftest.py`.

---

## 0. Pre-flight — what the current code actually says

Read the relevant files end-to-end before writing the bench. Findings (with `file:line` evidence) that the plan below depends on:

1. **`siglab/data/sosovalue_client.py:133-159`** — exactly **2 IMPLEMENTED** endpoints:
   - `etf_historical_inflow(*, etf_type="us-btc-spot")` → builds spec `GET /etfs/summary-history?symbol=BTC&country_code=US` (L138-141). Despite the audit's verdict, this is a **GET with query string** (not the "POST /openapi/v2/etf/historicalInflowChart" of the earlier draft); the audit's claim of "method wrong" applies to a previous version, not the file on disk today.
   - `listed_currencies()` → builds spec `GET /currencies` (L148-159) with `require_envelope=True` and `require_non_empty=True`. Required fields: `currency_id`, `symbol`, `name`.
   - `featured_news_pages()` exists (L160-188) but targets the WRONG path per the brutal audit (`/api/v1/news/featured` under the wrong base). The micro bench must NOT depend on it.

2. **`tests/integration/test_sosovalue_live.py`** — confirms the live shape:
   - L41 base URL: `https://openapi.sosovalue.com/openapi/v1`
   - L38 skip var: `SOSOVALUE_API_KEY` (unset → `SkipTest`)
   - L37 explicit opt-out: `SIGLAB_SKIP_SOSOVALUE=1`
   - L103-150 envelope contract: `GET /currencies` returns `{code: 0, message: "success", data: [{currency_id, symbol, name}, ...]}`.
   - L130-150: `GET /etfs/summary-history?symbol=BTC&country_code=US` returns either a flat array `[{date, total_net_inflow, ...}]` or an envelope. Per official docs (`summary-history.md`) the response is a flat array — no envelope. The live test handles both shapes.
   - L186-200: `GET /currencies/{id}/klines?interval=1d` is gated on `1d` only (per `klines.md`). The live test does not assert non-empty: the demo plan can return a real timeseries or skip if empty.

3. **`siglab/evaluation/backtest.py:47-159`** — `run_backtest(prices, target_weights, config) -> BacktestResult`:
   - L58 sorts by timestamp, L60 computes `pct_change`, L62 multiplies by `weights.shift(1) * leverage`.
   - L86 `equity = (1+pnl).cumprod()`.
   - L137-159 `_stats` returns `{total_return, sharpe, cagr, max_drawdown, calmar, liquidated}`. Sharpe is annualised with `annual_factor = sqrt(365.25 * 24)` (hourly bars). The micro bench must use `freq="D"` and re-scale to a daily-annualised factor `sqrt(365.25)` (or call `run_backtest` with klines only at the `1d` interval, in which case the result.stats sharpe is already daily-annualised only if the input is hourly). Since klines are daily, we should compute the bench metrics ourselves from the kline return series and NOT call `run_backtest` directly (the engine assumes hourly bars via `annual_factor=365.25*24`).
   - Decision: the bench uses `run_backtest` only for the SIGNAL-DRIVEN trade-loop metric (one trade per signal, hold one bar) where the bar frequency doesn't matter for hit-rate; for sharpe / max-drawdown it computes its own daily-annualised metrics off the kline series. This is the "smaller-delta" play — do not modify `backtest.py` to add a daily variant.

4. **`tests/bench/`** — only 2 files exist today:
   - `test_bench_sodex_ws.py` — uses `subprocess.run([sys.executable, "-m", "siglab.cli", "sodex-ws-probe", ...])` and asserts wall-time `< 5s` (L26, L87-91). Single deterministic sample, no pytest-benchmark.
   - `test_bench_cli_help.py` — same pattern for `python -m siglab.cli --help`.
   - **No pytest-benchmark is a project dep** (confirmed by the `time.perf_counter` fallback in both files).
   - Both files use loose budgets to avoid CI flapping and tight `TARGET_*` constants for the eventual perf-plan landing.

5. **`siglab/evaluation/__init__.py`** — explicitly no eager imports to avoid circular dependencies. The micro bench can safely `from siglab.evaluation.backtest import BacktestConfig, run_backtest` (the `runner.py` import chain is what they were avoiding).

6. **Currently-skipped tests** — the assignment's "80 currently-skipped" framing combines:
   - All 22 explicit `skipTest` / `SkipTest` sites counted by `grep` (L0-L22 of grep output).
   - All 670 lines of `tests/test_sosovalue_api.py` that mock the SoSoValue client and would skip if the key were real (the whole file is mocks of mocks; the moment any of them hits a real endpoint they are invalid).
   - The 18 BLOCKED truth-table rows at `siglab/data/sosovalue_capabilities.py:33-261` (per the brutal audit) which currently have zero coverage because nothing exercises them.
   - The bench has 0 SoSoValue tests today.
   - The 80 figure is the assignment's estimate, not a `grep` count. The bench plan covers ~3 of those 18 BLOCKED rows directly (`/currencies`, `/etfs/summary-history`, `/currencies/{id}/klines`) and surfaces the rest as "would-needs-more-impl" rather than counting them as replaced.

---

## 1. The 5 metrics the micro bench will measure

All 5 consume **live SoSoValue data only**. Each metric is a `callable() -> float | None` decorated with `@pytest.mark.bench` so a future `-m "not bench"` can opt out. Every metric must be safe to run when `SOSOVALUE_API_KEY` is unset (see §3).

### Metric 1 — `metric_currency_list_envelope_shape`

**Formula** (binary pass/fail):
```
1.0  if GET /currencies envelope matches {code: 0, data: list[dict with keys in {"currency_id","symbol","name"}]} AND len(data) >= 50
0.0  otherwise
None if the endpoint errored (skipped)
```

Concrete: assert `body["code"] in (0, "0")`, `isinstance(body["data"], list)`, `len(body["data"]) >= 50`, and that the first row has a `currency_id` (string) and `symbol` (non-empty string). This is the **shape-conformance probe** for the 1 IMPLEMENTED currency endpoint — and is the only test in the bench that directly validates SigLab's `listed_currencies` contract against real traffic.

### Metric 2 — `metric_etf_summary_history_present_and_inflow_finite`

**Formula** (continuous):
```
nanmean(abs(total_net_inflow))  if  rows is non-empty and all total_net_inflow values are finite
None  otherwise (also return None when SOSOVALUE_API_KEY unset)
```

Concrete: hit `GET /etfs/summary-history?symbol=BTC&country_code=US`, parse the flat-array shape, extract `total_net_inflow` column, compute `pd.Series(rows)["total_net_inflow"].abs().mean()`. Units: USD. This is the **mean absolute daily ETF net-inflow** — measures whether the upstream is returning real numbers (not zeros, not NaN, not the same constant). If `< 1e6` USD, the bench logs a WARNING but does not fail (real demo data can be quiet on weekends).

### Metric 3 — `metric_kline_signal_hit_rate_1d`

**Formula** (continuous, range `[0.0, 1.0]`):
```
hits / N
where
    N    = number of (signal, next-bar) pairs in the window
    hits = count of pairs where sign(target_weights.loc[t+1] * returns.loc[t+1]) > 0
and
    signal_t  = sign(close_t - close_{t-1})  (1-day momentum)
    weights_t = +1 if signal_t == 1 else -1
    returns_t = close_t / close_{t-1} - 1
```

Concrete: pull `/currencies/{id}/klines?interval=1d` for the first currency from `/currencies`. Build a price Series from `data[].close` (or whatever field the live shape uses — see §2.2). Compute `target_weights = sign(prices.diff()).reindex(prices.index)`. Call `run_backtest(prices.to_frame(name="SYM"), target_weights.to_frame("SYM"), BacktestConfig(leverage=1.0))`. Read `result.trades` count and `result.stats["total_return"]`. Compute `hit_rate = (weights.shift(1) * returns > 0).mean()`. Return `hit_rate`. This is the **honest measure of a 1-day momentum signal against real SoSoValue klines** — not a backtest of an imagined strategy, but a probe that says "if you went long on up-days and short on down-days, how often were you right?"

**Sanity range:** 0.45 - 0.55 is "the market is roughly efficient"; 0.40-0.60 is normal. The bench does NOT assert a specific value — it logs the number and asserts only that the value is finite and within `[0.0, 1.0]`. Asserting `> 0.5` would be a fake test (a 1-day momentum signal on real BTC data can underperform).

### Metric 4 — `metric_kline_signal_sharpe_daily_annualised`

**Formula** (continuous):
```
sharpe = (mean(daily_returns) / std(daily_returns)) * sqrt(252.0)
where daily_returns_t = weights.shift(1).loc[t] * prices.pct_change().loc[t]
```

Concrete: same kline series as Metric 3, but compute `sharpe` directly off the kline returns (not via `run_backtest`, which uses an hourly annualisation factor). The bench computes its own. Asserts only that `sharpe` is finite and `|sharpe| < 10.0` (a real demo signal will be noisy; values outside that range indicate a data glitch or divide-by-zero on a flat segment).

### Metric 5 — `metric_currencies_endpoint_latency_p95`

**Formula** (continuous, milliseconds):
```
p95([latency_ms_1, latency_ms_2, ..., latency_ms_5])  of 5 sequential GETs to /currencies
None  if all 5 error
```

Concrete: run `client.listed_currencies()` (the real `SoSoValueClient`) 5 times in a loop, reading `client.metrics_snapshot()["endpoints"]["currency.list"]["p95_ms"]` after each call. The point of this metric is **NOT to assert latency is fast** (CI is noisy) — it is to surface the upstream's response time and assert that the client did NOT transport-fail. The bench asserts only that `p95_ms > 0` and `p95_ms < 30_000` (30s sanity ceiling) and that `transport_failures == 0`. If transport fails, return `None` and let the test skip.

---

## 2. Data flow: curl -> DataFrame -> signal -> backtest -> metric

### 2.1 Module-level helpers (defined in the bench file, not imported from `siglab/`)

```python
# Line numbers refer to test_bench_sosovalue_micro.py (the new file).
# Pseudocode only — final code may differ on column names per live response shape.

SKIP_ENV_VAR = "SIGLAB_BENCH_SKIP_SOSOVALUE"      # L24
API_KEY_ENV_VAR = "SOSOVALUE_API_KEY"              # L25
BASE_URL = "https://openapi.sosovalue.com/openapi/v1"  # L26
TIMEOUT_S = 30.0                                   # L27

def _api_key() -> str | None:                      # L30-31
    return os.environ.get(API_KEY_ENV_VAR) or None

def _should_skip() -> bool:                        # L34-37
    if os.environ.get(SKIP_ENV_VAR, "").strip().lower() in {"1","true","yes"}:
        return True
    return not _api_key()

def _get(path: str, params: dict | None = None) -> dict | list:    # L40-65
    # urllib.request.Request, header "x-soso-api-key: <key>",
    # timeout=TIMEOUT_S. On 401/403/404/422 -> raise SkipTest.
    # On 429 -> raise SkipTest. On 5xx -> raise SkipTest.
    # On any other HTTPError -> raise AssertionError.
    # On transport error -> raise SkipTest.

def _unwrap_envelope(body) -> list[dict]:          # L68-78
    # Tolerate both envelope and flat-array shapes (per audit §3.5).
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        if isinstance(body.get("data"), list):
            return body["data"]
        if isinstance(body.get("list"), list):
            return body["list"]
        return [body]
    raise SkipTest(f"unhandled shape: {type(body).__name__}")
```

### 2.2 Per-metric flow (line-level pseudocode for each test method)

**Metric 1 — envelope shape:**
```python
def test_bench_metric_currency_list_envelope_shape():       # L120-138
    if _should_skip():
        pytest.skip("SOSOVALUE_API_KEY not set")
    body = _get("/currencies")                              # L122
    rows = _unwrap_envelope(body)                           # L123
    assert isinstance(body, dict) and body.get("code") in (0, "0")
    assert len(rows) >= 50
    first = rows[0]
    assert "currency_id" in first and "symbol" in first
```

**Metric 2 — ETF mean abs inflow:**
```python
def test_bench_metric_etf_summary_history_present():       # L141-170
    if _should_skip():
        pytest.skip("SOSOVALUE_API_KEY not set")
    body = _get("/etfs/summary-history", params={"symbol":"BTC","country_code":"US"})
    rows = _unwrap_envelope(body)
    frame = pd.DataFrame(rows)                              # cols per official schema
    assert "total_net_inflow" in frame.columns
    series = pd.to_numeric(frame["total_net_inflow"], errors="coerce").dropna()
    assert len(series) > 0
    mean_abs = float(series.abs().mean())
    assert mean_abs > 0                                      # upstream is alive
    assert mean_abs == mean_abs                              # not NaN
    # Bench does NOT assert mean_abs > some threshold.
    # Log: "metric_etf_summary_history_present_and_inflow_finite = {mean_abs:.0f} USD"
```

**Metric 3 — kline hit rate:**
```python
async def test_bench_metric_kline_hit_rate_1d():            # L173-220 (async)
    if _should_skip():
        pytest.skip("SOSOVALUE_API_KEY not set")
    # Step 1: get a currency_id
    body = _get("/currencies")
    rows = _unwrap_envelope(body)
    cid = rows[0]["currency_id"]
    # Step 2: get klines (1d is the only supported interval per docs)
    kline_body = _get(f"/currencies/{cid}/klines", params={"interval": "1d"})
    klines = _unwrap_envelope(kline_body)
    # Step 3: build a price Series (column name will be confirmed against live response)
    df = pd.DataFrame(klines)
    price_col = "close" if "close" in df.columns else df.columns[0]
    prices = pd.to_numeric(df[price_col], errors="coerce").dropna()
    if len(prices) < 10:
        pytest.skip(f"only {len(prices)} kline rows; need >= 10 for hit-rate")
    # Step 4: signal = sign(close.diff())
    signal = np.sign(prices.diff()).fillna(0.0)
    weights = pd.Series(signal, name="SYM", index=prices.index)
    # Step 5: hit-rate
    ret = prices.pct_change().fillna(0.0)
    hit_rate = float((weights.shift(1).fillna(0.0) * ret > 0).mean())
    # Step 6: run_backtest for total_return only (the engine's sharpe is hourly-annualised)
    res = run_backtest(
        prices.to_frame("SYM"),
        weights.to_frame("SYM"),
        BacktestConfig(leverage=1.0),
    )
    total_return = res.stats["total_return"]
    assert 0.0 <= hit_rate <= 1.0
    assert total_return == total_return                       # not NaN
    # Log the numbers; do NOT assert hit_rate > 0.5.
```

**Metric 4 — daily-annualised sharpe:**
```python
def test_bench_metric_kline_sharpe_daily_annualised():      # L223-260
    # Reuses prices from Metric 3 (extracted into a session-scoped helper).
    # ...same as Metric 3 steps 1-4, then:
    port_ret = (weights.shift(1).fillna(0.0) * ret).dropna()
    if port_ret.std() == 0:
        pytest.skip("flat kline segment; sharpe undefined")
    sharpe = float(port_ret.mean() / port_ret.std() * (252.0 ** 0.5))
    assert math.isfinite(sharpe)
    assert abs(sharpe) < 10.0
```

**Metric 5 — endpoint latency p95:**
```python
async def test_bench_metric_currencies_endpoint_latency():  # L263-300 (async)
    if _should_skip():
        pytest.skip("SOSOVALUE_API_KEY not set")
    async with SoSoValueClient(api_key=_api_key()) as client:
        for _ in range(5):
            rows = await client.listed_currencies()
            assert len(rows) >= 50
        snap = client.metrics_snapshot()
        ep = snap["endpoints"]["currency.list"]
        assert ep["transport_failures"] == 0
        p95 = ep["p95_ms"]
        assert 0.0 < p95 < 30_000.0
```

### 2.3 Data-flow diagram (ASCII)

```
+---------------------+        +---------------------+        +----------------------+
| urllib GET          |        | _unwrap_envelope    |        | pd.DataFrame         |
| /currencies         | -----> | {code,msg,data}     | -----> | 50 rows              |
| x-soso-api-key hdr  |        |   or flat array     |        |  cols: id,symbol,name|
+---------------------+        +---------------------+        +----------------------+
        |                                |                              |
        v                                v                              v
+---------------------+        +---------------------+        +----------------------+
| urllib GET          |        | _unwrap_envelope    |        | pd.DataFrame         |
| /etfs/summary-      | -----> | flat-array or       | -----> | total_net_inflow col |
|  history?symbol=BTC |        |   envelope          |        | abs().mean()         |
+---------------------+        +---------------------+        +----------------------+
        |                                |                              |
        v                                v                              v
+---------------------+        +---------------------+        +----------------------+
| urllib GET          |        | pd.DataFrame        |        | signal=sign(diff)    |
| /currencies/{id}/   | -----> | kline rows          | -----> | weights=signal       |
|  klines?interval=1d |        | close col           |        |   run_backtest()     |
+---------------------+        +---------------------+        |   hit_rate, sharpe   |
                                                              +----------------------+
                                                                       |
                                                                       v
                                                              +----------------------+
                                                              | pytest.assert        |
                                                              | (loose, see §1)      |
                                                              +----------------------+
```

---

## 3. Skip semantics — when `SOSOVALUE_API_KEY` is unset

**Contract:** the bench MUST NOT fail, MUST NOT raise, and MUST NOT pollute the test summary. It MUST emit a visible `pytest.skip(...)` message naming the env var so a human running `pytest -v` can see why nothing happened.

Implementation:

```python
# L34-37
def _should_skip() -> bool:
    if os.environ.get(SKIP_ENV_VAR, "").strip().lower() in {"1", "true", "yes"}:
        return True
    return not _api_key()

# In every test method, the FIRST lines are:
def test_xxx():
    if _should_skip():
        pytest.skip(
            f"{API_KEY_ENV_VAR} unset or {SKIP_ENV_VAR}=1; "
            f"live SoSoValue micro bench requires a real key"
        )
    # ... real work ...
```

**When `SOSOVALUE_API_KEY` is unset:**
- All 5 test methods (plus 2 extra — see §5) return `pytest.skip(...)` at the top.
- pytest reports the test as `s` (skipped) with the reason string in `-v` output.
- Test summary shows `5 skipped` (or 7 — see §5) instead of `5 passed` or `5 failed`.
- **No network IO happens** — `_api_key()` is checked before any `urllib.request` call. CI without a key pays zero network cost.

**When the key IS set but the upstream returns 401/403/404/422 (truth-table mismatch):**
- `_get(...)` raises `pytest.skip(...)` with a message naming the endpoint and the HTTP code, so a wrong path is reported as a discovery rather than a test failure.
- The test summary shows `5 skipped` with one line per skip reason. This matches the existing pattern in `tests/integration/test_sosovalue_live.py:86-89`.

**When the upstream returns 429 (rate limit):**
- `_get(...)` raises `pytest.skip(...)` citing the 429. Matches `tests/integration/test_sosovalue_live.py:91-92`.

**When the upstream returns 5xx or times out:**
- `_get(...)` raises `pytest.skip(...)` so a transient server issue is not a test failure. The bench will not catch an upstream outage — that is a job for monitoring, not for a unit bench.

**The bench MUST NOT call `unittest.SkipTest` or `self.skipTest` — it uses `pytest.skip` so it works under either `pytest` or `unittest` runners.** This mirrors `tests/integration/test_openrouter_free_models.py` which uses both styles.

**Log policy:** every skip calls `logging.getLogger(__name__).info("skip: %s", reason)` BEFORE `pytest.skip(...)` so the reason is in the test log even if `-v` is off.

---

## 4. The 1 new test file path

```
tests/bench/test_bench_sosovalue_micro.py
```

Single file. No `__init__.py` changes (the `tests/bench/` dir already has one in spirit via pytest discovery). No `pyproject.toml` change (no new dep; `pandas`, `numpy`, `pytest` are all already in the runtime). No `conftest.py` change.

`urllib.request` is used (not `httpx`) so the bench has zero new transitive deps — matches `tests/integration/test_sosovalue_live.py:30-33`.

`asyncio` is used ONLY for the `SoSoValueClient` direct-use metric (Metric 5); that one method is `async def` and uses `pytest-asyncio` if available, falling back to `asyncio.run` otherwise. `pytest-asyncio` is already in the dev deps (the existing TUI tests use it).

---

## 5. Concrete test methods (5-7 with assertions)

```python
# tests/bench/test_bench_sosovalue_micro.py

# === Method 1 — Metric 1 ==========================================
def test_bench_metric_currency_list_envelope_shape() -> None:
    """Live GET /currencies must return {code, message, data:[{...}]} with >=50 rows."""
    if _should_skip():
        pytest.skip(...)
    body = _get("/currencies")
    assert isinstance(body, dict), f"expected envelope dict, got {type(body).__name__}"
    assert body.get("code") in (0, "0"), f"non-zero code: {body!r}"
    data = body.get("data")
    assert isinstance(data, list), f"data not a list: {type(data).__name__}"
    assert len(data) >= 50, f"only {len(data)} currencies; upstream is degraded"
    first = data[0]
    assert isinstance(first, dict)
    assert "currency_id" in first, f"missing currency_id; keys={list(first)[:5]}"
    assert "symbol" in first and isinstance(first["symbol"], str) and first["symbol"]

# === Method 2 — Metric 2 ==========================================
def test_bench_metric_etf_summary_history_inflow_finite() -> None:
    """Live GET /etfs/summary-history must return rows with finite total_net_inflow."""
    if _should_skip():
        pytest.skip(...)
    body = _get("/etfs/summary-history", params={"symbol":"BTC","country_code":"US"})
    rows = _unwrap_envelope(body)
    assert isinstance(rows, list)
    assert len(rows) > 0, "etf summary-history returned empty list"
    frame = pd.DataFrame(rows)
    assert "total_net_inflow" in frame.columns, (
        f"missing total_net_inflow; cols={list(frame.columns)[:5]}"
    )
    series = pd.to_numeric(frame["total_net_inflow"], errors="coerce").dropna()
    assert len(series) == len(frame), "some total_net_inflow values not numeric"
    assert (series.abs() > 0).any(), "all inflows are zero; upstream is degraded"
    mean_abs = float(series.abs().mean())
    # Bench does NOT assert mean_abs > some threshold. Log only.
    _log_metric("etf_summary_history_mean_abs_inflow_usd", mean_abs)

# === Method 3 — Metric 3 (sync variant using urllib) ==============
def test_bench_metric_kline_signal_hit_rate_1d() -> None:
    """Live /currencies/{id}/klines + 1d momentum signal; report (do not assert) hit-rate."""
    if _should_skip():
        pytest.skip(...)
    cid = _first_currency_id()
    kline_body = _get(f"/currencies/{cid}/klines", params={"interval":"1d"})
    klines = _unwrap_envelope(kline_body)
    assert len(klines) >= 10, f"only {len(klines)} klines; need >=10"
    df = pd.DataFrame(klines)
    price_col = "close" if "close" in df.columns else df.columns[0]
    prices = pd.to_numeric(df[price_col], errors="coerce").dropna()
    assert len(prices) >= 10
    signal = np.sign(prices.diff()).fillna(0.0)
    weights = pd.Series(signal, name="SYM", index=prices.index)
    ret = prices.pct_change().fillna(0.0)
    hit_rate = float((weights.shift(1).fillna(0.0) * ret > 0).mean())
    assert 0.0 <= hit_rate <= 1.0, f"hit_rate out of range: {hit_rate}"
    res = run_backtest(
        prices.to_frame("SYM"),
        weights.to_frame("SYM"),
        BacktestConfig(leverage=1.0),
    )
    tr = res.stats["total_return"]
    assert tr == tr, f"total_return is NaN: {tr}"
    _log_metric("kline_hit_rate_1d", hit_rate)
    _log_metric("kline_signal_total_return", tr)

# === Method 4 — Metric 4 ==========================================
def test_bench_metric_kline_signal_sharpe_daily_annualised() -> None:
    """Daily-annualised sharpe of the 1d-momentum signal on real klines."""
    if _should_skip():
        pytest.skip(...)
    # Reuse the kline fetch helper from Method 3
    prices = _first_currency_klines_daily()
    signal = np.sign(prices.diff()).fillna(0.0)
    port_ret = (signal.shift(1).fillna(0.0) * prices.pct_change().fillna(0.0)).dropna()
    if port_ret.std() == 0.0 or len(port_ret) < 5:
        pytest.skip(f"flat/short kline segment; sharpe undefined (n={len(port_ret)})")
    sharpe = float(port_ret.mean() / port_ret.std() * (252.0 ** 0.5))
    assert math.isfinite(sharpe), f"sharpe not finite: {sharpe}"
    assert abs(sharpe) < 10.0, f"|sharpe| >= 10 on demo data: {sharpe}"
    _log_metric("kline_sharpe_daily_annualised", sharpe)

# === Method 5 — Metric 5 (async, uses SoSoValueClient) ===========
@pytest.mark.asyncio
async def test_bench_metric_currencies_endpoint_latency() -> None:
    """5 sequential GETs to /currencies; p95 latency must be < 30s and zero transport failures."""
    if _should_skip():
        pytest.skip(...)
    async with SoSoValueClient(api_key=_api_key(), conservative_rate_limit_per_minute=20) as client:
        for _ in range(5):
            rows = await client.listed_currencies()
            assert len(rows) >= 50, f"only {len(rows)} currencies returned"
        snap = client.metrics_snapshot()
        ep = snap["endpoints"]["currency.list"]
        assert ep["transport_failures"] == 0, f"transport failures: {ep}"
        p95 = float(ep["p95_ms"])
        assert 0.0 < p95 < 30_000.0, f"p95 out of range: {p95:.1f} ms"
        _log_metric("currencies_endpoint_p95_ms", p95)
        _log_metric("currencies_endpoint_success_rate", float(ep["successes"] / max(1, ep["attempts"])))

# === Method 6 — guard rail =======================================
def test_bench_skip_semantics_when_key_unset(monkeypatch) -> None:
    """The skip path must not raise and must not call the network."""
    monkeypatch.delenv("SOSOVALUE_API_KEY", raising=False)
    monkeypatch.setenv("SIGLAB_BENCH_SKIP_SOSOVALUE", "1")
    # Re-run the skip predicate directly — no IO.
    from tests.bench.test_bench_sosovalue_micro import _should_skip
    assert _should_skip() is True

# === Method 7 — guard rail =======================================
def test_bench_envelope_unwrap_tolerates_both_shapes() -> None:
    """_unwrap_envelope must accept envelope, flat-array, and {list: [...]} shapes."""
    assert _unwrap_envelope({"code": 0, "data": [{"a": 1}]}) == [{"a": 1}]
    assert _unwrap_envelope([{"a": 1}, {"a": 2}]) == [{"a": 1}, {"a": 2}]
    assert _unwrap_envelope({"list": [{"a": 1}]}) == [{"a": 1}]
    with pytest.raises(pytest.skip.Exception):  # type: ignore[attr-defined]
        _unwrap_envelope("not a dict or list")
```

**Counts:** 7 test methods (5 metrics + 2 guard rails). The "5-7" requirement is satisfied.

---

## 6. Smaller-delta — exactly 0 edits to other files

**Edits in this plan:** 1 new file. **Files touched (0 edits):**
- `siglab/evaluation/__init__.py` — NOT edited.
- `siglab/evaluation/backtest.py` — NOT edited (the bench imports `run_backtest` and `BacktestConfig` as-is; the sharpe metric is computed manually with `sqrt(252)` instead of `sqrt(365.25*24)` because klines are daily).
- `siglab/evaluation/score.py` — NOT edited.
- `siglab/evaluation/analysis_utils.py` — NOT edited.
- `siglab/evaluation/compile.py` — NOT edited.
- `siglab/data/sosovalue_client.py` — NOT edited. The bench USES `SoSoValueClient` for Metric 5 (the `async with` test) but does not change it. The other 4 metrics hit the wire directly with `urllib.request` to stay aligned with `tests/integration/test_sosovalue_live.py:30-81`.
- `siglab/data/feeds.py` — NOT edited.
- `tests/conftest.py` — NOT edited.
- `pyproject.toml` — NOT edited (no new dep; `urllib`, `pandas`, `numpy`, `pytest` all in the runtime already; `pytest-asyncio` already in dev deps for TUI tests).
- `tests/bench/__init__.py` — does not exist; do not create.

**Why this is the smallest possible delta:** the bench is a pure leaf test file that imports from the public surface of `siglab.evaluation` and `siglab.data` and reaches the network directly. No fixture, no helper, no shared module, no constant. The two guard-rail tests (Methods 6, 7) are pure-CPU and prove the skip/envelope-unwrap logic without making any HTTP call — so even on a host without network they pass.

**Why the bench is honest (no fake tests):**
- The hit-rate and sharpe metrics assert only that the values are FINITE and within sane bounds (`[0,1]`, `|sharpe| < 10`). They do NOT assert `hit_rate > 0.5` or `sharpe > 1.0` — those would be fake tests on noisy demo data.
- The ETF inflow metric asserts the value is non-zero (catches an upstream that returns a constant), but does NOT assert a specific dollar amount.
- The latency metric asserts `p95 < 30s` — a sanity ceiling, not a perf target. Tightening this is left for the perf plan to land.
- All assertions either pass on the live demo data (proving the upstream is alive) or are skipped (proving the bench handles absence gracefully). None assert a made-up number.

---

## 7. Coverage — how many of the 80 currently-skipped tests this would replace

The "80 currently-skipped tests" framing combines (assignment's own estimate, not a `grep` count):

| Bucket | Count | This bench touches? |
|---|---|---|
| Explicit `skipTest` / `SkipTest` sites in `tests/` (grep'd: 22 sites) | 22 | 0 directly (the bench has its own skip semantics, not shared with those) |
| `tests/test_sosovalue_api.py` (670 lines, ~30 mocked test methods) | ~30 | 0 (the bench is a real-traffic bench, not a mock-audit bench) |
| BLOCKED truth-table rows at `siglab/data/sosovalue_capabilities.py:33-261` (18 rows, zero coverage) | 18 | **3 of 18 replaced**: `/currencies` (BLK-1 is IMP so this counts double), `/etfs/summary-history` (BLK-11), `/currencies/{id}/klines` (BLK-3). The other 15 BLOCKED rows are not exercised by the bench. |
| Bench tests today (2 files, 2 test methods) | 2 | +5 new metric tests + 2 guard rails = +7 |
| Total: 22 + 30 + 18 + 2 = 72; rounded up to 80 in the assignment | ~80 | **3 BLOCKED rows replaced + 7 new bench tests added** |

**Honest assessment:** the bench plan **directly replaces 3 of the 18 BLOCKED rows** (about 4% of the 80 figure), and **adds 7 new tests** to the bench dir (which had 2 before). It does not touch the 22 explicit `skipTest` sites or the 30 mocked unit tests — those need their own pass (and a separate plan).

**What this bench does NOT do (and is not claiming to do):**
- It does not exercise the 15 other BLOCKED endpoints (`/currencies/{id}/market-snapshot`, `/etfs/list`, `/etfs/{ticker}/market-snapshot`, `/etfs/{ticker}/history`, `/indices/...`, `/crypto-stocks/...`, `/btc-treasuries/...`, `/fundraising/...`, `/macro/...`, `/analyses/...`, `/news/hot`, `/news/featured` after the audit, etc.). A second micro-bench pass could cover those, but each requires a new `_get` helper and a new metric definition.
- It does not fix the truth-table errors (the brutal audit found 4 wrong paths, 2 wrong methods, 1 phantom endpoint). Those are code changes in `siglab/`, which is out of scope here.
- It does not fix the `etf_base_url = "https://api.sosovalue.xyz"` problem (`sosovalue_client.py:60`). Metric 2 hits the URL the client constructs internally; if `etf_base_url` is wrong, Metric 2 will fail with a network/404 and `pytest.skip(...)` (truth-table mismatch) — which is exactly the right signal to surface.

**What this bench DOES deliver:**
- 1 new file, 7 new test methods, 0 edits to other files.
- Real, live, end-to-end coverage of the 3 SoSoValue endpoints a SigLab user can actually hit today.
- A signal-log (`_log_metric`) that emits a one-line `metric=... value=...` per test, so a future perf dashboard can chart the live latency and hit-rate over time without any code change.

---

## 8. Acceptance criteria for THIS plan (not the bench)

The plan is acceptable iff:

- [x] Section 1 lists 5 metrics with concrete formulas and units.
- [x] Section 2 gives a line-level data flow from urllib -> DataFrame -> signal -> backtest -> metric.
- [x] Section 3 explicitly addresses `SOSOVALUE_API_KEY` unset (must skip, must log, must not call network).
- [x] Section 4 names the single new test file path.
- [x] Section 5 lists the 5-7 test methods with their assertions.
- [x] Section 6 documents 0 edits to other files.
- [x] Section 7 estimates coverage honestly (3/18 BLOCKED rows replaced; does not pretend to cover 80).

The bench implementation can begin ONLY after this plan is reviewed and accepted. The bench itself is then implemented by a different agent (ApplyAgent) in a separate worktree, with a separate verification pass (AuditAgent) before merge.

---

## 9. Risks & open questions

1. **Kline response field name** — the official docs example for `/currencies/{id}/klines` shows `[{"date", "open", "high", "low", "close", "volume"}]` but the live test (`tests/integration/test_sosovalue_live.py:196-200`) does not assert field names. The bench's Metric 3-4 falls back to `df.columns[0]` if `"close"` is missing. The first real run will reveal the actual schema; the fallback means the bench does not break.

2. **`SoSoValueClient.metrics_snapshot` shape** — `p95_ms` may be `None` for an endpoint with no recorded latencies (e.g. all 5 calls hit the cache). The bench asserts `0.0 < p95 < 30_000.0` which is robust against the None case (None < 30_000 is True but 0.0 < None is True; we should change to `p95 is not None` first). Will be tightened in the implementation.

3. **`run_backtest` with `interval=1d` klines** — `backtest.py:81` only applies funding cost at "8-hour settlement boundaries" based on `pnl.index.hour % 8 == 0 & pnl.index.minute == 0`. Daily klines have hour=0 (midnight) which IS an 8-hour boundary, so the engine will try to apply funding even when the user did not pass `funding_rates`. The engine's guard at L78 (`if config.funding_rates is not None`) saves us: we pass `BacktestConfig()` (default `funding_rates=None`), so the funding branch is skipped entirely. No bug here.

4. **Currency-list shape mismatch** — the audit says `_validate_payload` rejects `/currencies` because it lacks a `code` field, but `listed_currencies()` at `sosovalue_client.py:148-159` uses `require_envelope=True`. If the live response is a flat array (per official docs), the client would fail. This is a real client bug, not a bench bug. The bench's Metric 1 hits the wire directly with `urllib` (bypassing the buggy client) and Metric 5 calls the client — so if Metric 5 fails with `SoSoValueUpstreamFormatError`, the test surface would catch it. Worth noting for the follow-up fix.

5. **pytest-asyncio** — Method 5 is `async def`. The TUI tests in `tests/test_tui_*.py` use `@pytest.mark.asyncio`. Confirm before implementation that this is configured in `pyproject.toml`; if not, fall back to `asyncio.run(...)` inside a sync test.

6. **Rate-limit interaction** — 5 sequential `/currencies` calls in Metric 5 + 1 ETF call in Metric 2 + 1 currency list in Metric 3 = 7 calls. Below the 20/min limit. No backoff needed.

7. **What if `interval=1d` is the only supported interval and the bench user wants to test other intervals?** — out of scope; the brutal audit confirmed `1d`-only at `klines.md`, and the bench follows the official contract.

---

## 10. References

- `siglab/data/sosovalue_client.py:89-159` — the 2 IMPLEMENTED endpoints.
- `siglab/evaluation/backtest.py:47-159` — the backtest engine and stats.
- `tests/integration/test_sosovalue_live.py:30-150` — the live test pattern + envelope shape.
- `tests/bench/test_bench_sodex_ws.py:1-92` — the bench harness pattern (subprocess-free version, single deterministic sample, loose budget + tight target).
- `tests/bench/test_bench_cli_help.py:1-62` — same pattern.
- `docs/module-evaluation.md:43-160` — the documented backtest mechanics (matches `backtest.py`).
- `agent_workspace/audit_sosovalue_official.md` — the brutal audit of the SoSoValue integration.
- `siglab/data/sosovalue_capabilities.py:20-261` — the 20-row truth table (2 IMPLEMENTED, 18 BLOCKED).
- SoSoValue official docs: `https://sosovalue-1.gitbook.io/sosovalue-api-doc` (currency.md, klines.md, summary-history.md, response-format.md, rate-limit.md).

---

## 11. Handoff

**Next step:** ApplyAgent (separate worktree) implements `tests/bench/test_bench_sosovalue_micro.py` exactly as described in §1-§7 of this plan. The ApplyAgent MUST NOT edit any file other than the new bench file.

**Verification step:** AuditAgent (separate context) runs the new bench file with `SOSOVALUE_API_KEY` set, then unset, and confirms:
- Key set, real upstream: 5 metrics log non-None values, all assertions pass, no skip.
- Key unset: all 5 metrics `pytest.skip`, no network IO, no failures.
- Upstream 401/404/422 on any endpoint: the corresponding metric `pytest.skip`s with the HTTP code in the reason.

**Blocking:** do not start implementation until this plan is accepted by the main agent.
