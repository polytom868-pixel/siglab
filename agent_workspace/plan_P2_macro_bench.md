# Plan P2 — Macro Regime Benchmark (macro_bench_*)

> **Scope:** PLAN-ONLY. This file is the design contract for a future
> implementation; no source file edits, no commits. The plan is explicit about
> what is *not* being changed (smaller-delta principle) and what new file
> is being added.

---

## 0. Naming clarification (read this first)

The bench family is internally named **`macro_*`** (the file pattern) to
indicate that it operates at the *market macro* level — pulling two of
SoSoValue's existing callable surfaces and turning them into regime
markers. It does **NOT** call the SoSoValue `/macro/events` endpoint.

Why the distinction matters:

| Term in this plan | What it actually is |
|-------------------|---------------------|
| `macro_bench_*` (file name family) | A new benchmark family that pulls **`featured_news` + `currency_klines`** and produces **5 regime markers**. |
| SoSoValue `/macro/events` (off-limits) | A BLOCKED endpoint per `siglab/data/sosovalue_capabilities.py:321-344`. Not implemented, not callable. The README explicitly says `Index/Macro/...` are out of buildathon scope. |
| "Regime markers" | Numeric per-day values that get thresholded into a label, paralleling `_pair_regime_state()` in `siglab/evaluation/runner.py:2043-2128`. |
| "Macro regime" in this plan | Cross-asset / market-wide context derived from featured news cadence + a representative currency kline. Not a callback into the SoSoValue Macro API. |

If a future reader assumes `macro_*` is a wrapper for `/macro/events`, they
will read the wrong code. The plan deliberately uses `macro_bench_*` to keep
the naming clearly internal to SigLab.

---

## 1. The 5 macro regime markers

All five are computed from data the **`SoSoValueClient` wrapper can
already return today** (`featured_news_pages`, `etf_historical_inflow`,
`listed_currencies`). No new wrapper is added in this delta.

For each marker: **(name)** — **(concrete formula)** — **(source
endpoint)** — **(label rule)**.

### 1.1 `news_volume_24h`
- **Source:** `GET /api/v1/news/featured?page=1&page_size=100` (per page)
  via `SoSoValueClient.featured_news_pages(max_pages=1, page_size=100)`
  in `siglab/data/sosovalue_client.py:160-188`.
- **Concrete formula:**
  ```python
  cutoff_24h = now_utc - pd.Timedelta(hours=24)
  ts = pd.to_datetime(df_news["publishTime"], unit="ms", utc=True, errors="coerce")
  news_volume_24h = int(ts.ge(cutoff_24h).sum())
  ```
- **Label rule (per rolling baseline of last 30 days of 24h counts):**
  - `z_score >= 1.0` → `"news_surge"`
  - `z_score <= -1.0` → `"news_quiet"`
  - else → `"news_baseline"`
  - If the rolling baseline has < 7 days of history → label `None`
    (insufficient evidence; do not synthesise).
- **Why it matters:** A 24h news surge is a known macro regime trigger
  for the existing `regime_gates.entry` machinery
  (`siglab/research/hypothesis.py:120-156` shows how a regime gate can be
  wired in). A spike in `featured_news` cadence is the cheapest "is
  something happening in crypto right now?" signal available from
  SoSoValue.

### 1.2 `news_volume_7d_zscore`
- **Source:** same as 1.1, but counts 7-day windows.
- **Concrete formula:**
  ```python
  df["publishTime"] = pd.to_datetime(df["publishTime"], unit="ms", utc=True, errors="coerce")
  daily_counts = df.dropna(subset=["publishTime"]) \
                   .set_index("publishTime") \
                   .resample("1D").size() \
                   .rename("count")
  today = daily_counts.index.max().normalize()
  window_7d = daily_counts.loc[today - pd.Timedelta(days=6) : today].sum()
  # 30-day rolling baseline ending yesterday
  baseline = daily_counts.loc[today - pd.Timedelta(days=30) : today - pd.Timedelta(days=1)]
  baseline_mean = baseline.mean()
  baseline_std  = baseline.std(ddof=1)
  z = (window_7d - baseline_mean) / baseline_std if baseline_std > 0 else float("nan")
  ```
- **Label rule:** `z >= 1.0` → `"news_surge_7d"`, `z <= -1.0` →
  `"news_drought_7d"`, else `"news_baseline_7d"`. Less than 14 days of
  baseline → label `None`.
- **Why it matters:** 24h is noisy. 7d z-score smooths single-day spikes
  so a real "macro news event" shows up as a sustained regime, not a
  one-off blip. Useful as the slow regime gate to pair with 1.1's fast
  one.

### 1.3 `etf_flow_zscore_7d`
- **Source:** `GET /etfs/summary-history?symbol=BTC&country_code=US` via
  `SoSoValueClient.etf_historical_inflow(etf_type="us-btc-spot")` in
  `siglab/data/sosovalue_client.py:133-145`.
- **Concrete formula:**
  ```python
  rows = await client.etf_historical_inflow(etf_type="us-btc-spot")
  df = pd.DataFrame(rows)
  df["date"] = pd.to_datetime(df["date"], utc=True, errors="coerce")
  df = df.dropna(subset=["date"]).sort_values("date")
  df["net_inflow"] = pd.to_numeric(df["totalNetInflow"], errors="coerce")
  df["net_inflow_7d_sum"] = df["net_inflow"].rolling(7).sum()
  latest = df["net_inflow_7d_sum"].iloc[-1]
  baseline = df["net_inflow_7d_sum"].dropna().iloc[-30:-1]   # prior 30 windows
  mu, sd = baseline.mean(), baseline.std(ddof=1)
  z = (latest - mu) / sd if sd > 0 else float("nan")
  ```
- **Label rule:** `z >= 1.0` → `"etf_strong_inflow"`, `z <= -1.0` →
  `"etf_strong_outflow"`, else `"etf_neutral"`. Fewer than 14 valid
  7d windows → label `None`.
- **Why it matters:** This is the actual macro proxy for "is risk-on
  flow coming into BTC?". Already wired into `market.py:97-108`
  (`latest_flow` / `latest_assets`) — we are not re-inventing that
  field, we are deriving a *z-scored* variant for the regime marker.

### 1.4 `kline_volatility_7d`
- **Source:** the existing `SoDEXFeeds.fetch_klines(symbol="BTC", interval="1d", limit=30)`
  in `siglab/data/sodex_feeds.py:164-225`. We deliberately reuse the
  SoDEX perp kline (the same one the existing backtest consumes via
  `MarketDataProvider._fetch_perp_bundle_sodex` at
  `siglab/data/feeds.py:1002-1090`) — this is the
  `currency_klines` analogue in our pipeline. The SoSoValue
  `/currencies/{id}/klines` endpoint is currently BLOCKED in
  `siglab/data/sosovalue_capabilities.py:46-56`; we do not pretend to
  call it.
- **Concrete formula:**
  ```python
  klines = await feeds.fetch_klines(symbol="BTC", interval="1d", limit=30)
  closes = klines["close"].astype(float)
  log_rets = np.log(closes).diff().dropna()
  vol_7d = log_rets.iloc[-7:].std(ddof=1)
  baseline_30d = log_rets.std(ddof=1)               # full 30-day window std
  ratio = vol_7d / baseline_30d if baseline_30d > 0 else float("nan")
  ```
- **Label rule:** `ratio >= 1.5` → `"vol_expansion"`, `ratio <= 0.6` →
  `"vol_compression"`, else `"vol_baseline"`. Fewer than 14 daily
  closes → label `None`.
- **Why it matters:** This is the "is BTC choppy right now?" marker,
  paralleling `market_volatility_label` (`high_volatility` /
  `low_volatility`) in `siglab/evaluation/runner.py:2199-2207`. We
  intentionally compute it from a 30-day rolling window — not the
  168-bar (7-day hourly) rolling std used in the existing regime
  classifier — so the bench can be run in isolation with a single
  `fetch_klines` call instead of needing the full evaluation
  pipeline.

### 1.5 `news_to_vol_ratio`
- **Source:** derived from 1.2 (7d news z-score) and 1.4 (7d vol ratio).
- **Concrete formula:**
  ```python
  ratio = (news_z_7d or 0.0) / (vol_ratio_7d or float("nan"))
  ```
  Guard: if either input is `None`/NaN, the marker is `None`.
- **Label rule:**
  - both surge (`news_z >= 1.0` and `vol_ratio >= 1.5`) → `"news_driven_vol"`
  - news surge but vol quiet (`news_z >= 1.0` and `vol_ratio <= 0.6`) → `"unrealised_news_pressure"`
  - news quiet but vol surge (`news_z <= -1.0` and `vol_ratio >= 1.5`) → `"price_action_unanchored"`
  - else → `"news_vol_aligned"`
- **Why it matters:** This is the *honest* regime marker that tells an
  operator whether the news cadence and the price action agree. The
  existing `market_trend_label` / `market_volatility_label` split
  (`siglab/evaluation/runner.py:2191-2208`) tells you the price side;
  1.5 is the only marker that fuses news + price. It is also the
  marker the user can argue with most cleanly: either news is surging
  and the bars are moving, or news is surging and the bars are not
  moving, etc.

### 1.6 Summary table (all 5 markers)

| ID | Marker | Source endpoint | Type | Threshold rule |
|----|--------|-----------------|------|----------------|
| 1.1 | `news_volume_24h` | `/api/v1/news/featured` (wrapper present) | count | 30d z-score |
| 1.2 | `news_volume_7d_zscore` | `/api/v1/news/featured` (wrapper present) | z-score | ±1.0 |
| 1.3 | `etf_flow_zscore_7d` | `/etfs/summary-history` (wrapper present) | z-score | ±1.0 |
| 1.4 | `kline_volatility_7d` | SoDEX `/markets/BTC/klines?interval=1d` (wrapper present) | ratio | 0.6 / 1.5 |
| 1.5 | `news_to_vol_ratio` | derived from 1.2 + 1.4 | categorical | 4-way rule |

All five respect the **"insufficient evidence → label is `None`"**
contract already used by `_pair_regime_snapshot()`
(`siglab/evaluation/runner.py:2191-2208`) — never synthesise a label
from < 14 days of data.

---

## 2. Data flow: curl → DataFrame → regime_marker → market_report_field

### 2.1 End-to-end (high level)

```
+------------------------------------------------------------------+
|  tests/bench/test_bench_sosovalue_macro.py  (NEW)                |
|  - pytest test, NOT a CLI command                                |
|  - Skips cleanly when SOSOVALUE_API_KEY is unset                 |
|  - Imports siglab.data.sosovalue_client.SoSoValueClient (live)   |
|  - Imports siglab.data.sodex_feeds.SoDEXFeeds        (live)      |
+------------------------------------------------------------------+
                |                            |
                v                            v
+----------------------+          +-----------------------------+
| SoSoValueClient      |          | SoDEXFeeds                  |
| .featured_news_pages |          | .fetch_klines(symbol="BTC", |
| .etf_historical_inflow|         |   interval="1d", limit=30)  |
+--------+-------------+          +---------------+-------------+
         |                                         |
         v                                         v
+----------------------+          +-----------------------------+
| df_news (DataFrame)  |          | df_klines (DataFrame)       |
| - publishTime (ms)   |          | - timestamp, open, high,    |
| - title, summary,    |          |   low, close, volume        |
|   matchedCurrencies, |          |                             |
|   category, url      |          |                             |
+--------+-------------+          +---------------+-------------+
         |                                         |
         +-----------------+-----------------------+
                           v
            +-----------------------------+
            | compute_macro_regime_markers|
            | (pure function, see 2.2)    |
            | markers = {                 |
            |   "news_volume_24h": {...}, |
            |   "news_volume_7d_zscore":  |
            |       {...},                |
            |   "etf_flow_zscore_7d":     |
            |       {...},                |
            |   "kline_volatility_7d":    |
            |       {...},                |
            |   "news_to_vol_ratio":      |
            |       {...},                |
            | }                           |
            +--------------+--------------+
                           v
            +-----------------------------+
            | assertions in the bench:    |
            | - shape, types              |
            | - label ∈ {None, <expected>}|
            | - cross-marker consistency   |
            +-----------------------------+
```

The "→ market_report_field" path is the **optional downstream wire**
described in section 6. The bench itself does not depend on that wire —
it runs end-to-end without touching `market.py`.

### 2.2 Line-level pseudocode (what the bench actually executes)

This is the body of the proposed `compute_macro_regime_markers()`,
which the test file imports (or duplicates, for purity) from
`siglab/evaluation/macro_bench.py`. **To honour the "no edits to
siglab/evaluation/*" smaller-delta constraint**, the test file lives at
`tests/bench/test_bench_sosovalue_macro.py` and the pure function it
calls is defined **inside the test file itself** for this delta. The
plan documents both options explicitly.

```python
# tests/bench/test_bench_sosovalue_macro.py  (NEW)
import os
import time
import asyncio
import unittest
import numpy as np
import pandas as pd
from typing import Any

# Skip semantics: no API key -> skip the whole module.
SKIP_ENV_VAR = "SIGLAB_SKIP_SOSOVALUE"
API_KEY_ENV_VAR = "SOSOVALUE_API_KEY"


def _skip_if_disabled() -> None:
    if os.environ.get(SKIP_ENV_VAR, "").strip().lower() in {"1", "true", "yes"}:
        raise unittest.SkipTest(f"{SKIP_ENV_VAR}=1 disables macro bench")


def _api_key() -> str | None:
    return os.environ.get(API_KEY_ENV_VAR) or None


# --- 1. NEWS PULL -----------------------------------------------------
async def _pull_featured_news(client, *, page_size: int = 100) -> pd.DataFrame:
    """Live: GET /api/v1/news/featured?page=1&page_size=100."""
    rows = await client.featured_news_pages(max_pages=1, page_size=page_size)
    df = pd.DataFrame(rows)
    # The wrapper already returns plain dicts; columns of interest:
    #   publishTime (ms epoch), title, summary, matchedCurrencies, category
    return df


# --- 2. ETF FLOW PULL -------------------------------------------------
async def _pull_etf_inflow(client) -> pd.DataFrame:
    """Live: GET /etfs/summary-history?symbol=BTC&country_code=US."""
    rows = await client.etf_historical_inflow(etf_type="us-btc-spot")
    df = pd.DataFrame(rows)
    return df


# --- 3. KLINE PULL (SoDEX) -------------------------------------------
async def _pull_btc_klines(feeds) -> pd.DataFrame:
    """Live: SoDEX /markets/BTC/klines?interval=1d&limit=30.
    Reuses SoDEXFeeds.fetch_klines wrapper (siglab/data/sodex_feeds.py)."""
    klines = await feeds.fetch_klines(symbol="BTC", interval="1d", limit=30)
    if klines is None or klines.empty:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
    return klines.reset_index()  # ensure columns


# --- 4. MARKER 1.1: news_volume_24h ---------------------------------
def _marker_news_volume_24h(df_news: pd.DataFrame) -> dict[str, Any]:
    if df_news.empty or "publishTime" not in df_news.columns:
        return {"value": None, "label": None, "reason": "no_news_rows"}
    ts = pd.to_datetime(df_news["publishTime"], unit="ms", utc=True, errors="coerce")
    cutoff_24h = pd.Timestamp.utcnow().tz_localize("UTC") - pd.Timedelta(hours=24)
    count_24h = int(ts.ge(cutoff_24h).sum())
    return {
        "value": count_24h,
        "label": _zscore_label_for_24h(df_news, cutoff_24h),
        "raw_count_24h": count_24h,
    }


def _zscore_label_for_24h(df_news, cutoff_24h) -> str | None:
    """Compute 30-day rolling baseline of daily news counts; z-score today's 24h count."""
    if df_news.empty:
        return None
    ts = pd.to_datetime(df_news["publishTime"], unit="ms", utc=True, errors="coerce")
    ts = ts.dropna()
    if len(ts) < 7:                                       # minimum signal
        return None
    daily = ts.dt.floor("1D").value_counts().sort_index()
    if len(daily) < 7:
        return None
    today = daily.index.max().normalize()
    baseline = daily.loc[daily.index < today].tail(30)
    if len(baseline) < 7:
        return None
    mu, sd = baseline.mean(), baseline.std(ddof=1)
    if sd == 0 or np.isnan(sd):
        return None
    z = (daily.loc[today] - mu) / sd
    if z >= 1.0:  return "news_surge"
    if z <= -1.0: return "news_quiet"
    return "news_baseline"


# --- 5. MARKER 1.2: news_volume_7d_zscore ----------------------------
def _marker_news_volume_7d_zscore(df_news: pd.DataFrame) -> dict[str, Any]:
    if df_news.empty or "publishTime" not in df_news.columns:
        return {"value": None, "label": None, "reason": "no_news_rows"}
    ts = pd.to_datetime(df_news["publishTime"], unit="ms", utc=True, errors="coerce")
    ts = ts.dropna()
    if len(ts) < 7:
        return {"value": None, "label": None, "reason": "fewer_than_7_news_rows"}
    daily = ts.dt.floor("1D").value_counts().sort_index()
    if len(daily) < 14:
        return {"value": None, "label": None, "reason": "fewer_than_14_days_of_history"}
    today = daily.index.max().normalize()
    window_7d = daily.loc[today - pd.Timedelta(days=6) : today].sum()
    baseline = daily.loc[today - pd.Timedelta(days=30) : today - pd.Timedelta(days=1)]
    mu, sd = baseline.mean(), baseline.std(ddof=1)
    if sd == 0 or np.isnan(sd):
        return {"value": None, "label": None, "reason": "zero_variance_baseline"}
    z = float((window_7d - mu) / sd)
    label = "news_surge_7d" if z >= 1.0 else "news_drought_7d" if z <= -1.0 else "news_baseline_7d"
    return {"value": z, "label": label, "raw_window_7d": int(window_7d),
            "baseline_mean": float(mu), "baseline_std": float(sd)}


# --- 6. MARKER 1.3: etf_flow_zscore_7d -------------------------------
def _marker_etf_flow_zscore_7d(df_etf: pd.DataFrame) -> dict[str, Any]:
    if df_etf.empty or "date" not in df_etf.columns:
        return {"value": None, "label": None, "reason": "no_etf_rows"}
    df = df_etf.copy()
    df["date"] = pd.to_datetime(df["date"], utc=True, errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date")
    if "totalNetInflow" in df.columns:
        df["net_inflow"] = pd.to_numeric(df["totalNetInflow"], errors="coerce")
    elif "total_net_inflow" in df.columns:
        df["net_inflow"] = pd.to_numeric(df["total_net_inflow"], errors="coerce")
    else:
        return {"value": None, "label": None, "reason": "no_net_inflow_column"}
    df = df.dropna(subset=["net_inflow"])
    if len(df) < 14:
        return {"value": None, "label": None, "reason": "fewer_than_14_etf_rows"}
    df["net_inflow_7d_sum"] = df["net_inflow"].rolling(7).sum()
    s = df["net_inflow_7d_sum"].dropna()
    if len(s) < 14:
        return {"value": None, "label": None, "reason": "fewer_than_14_valid_windows"}
    latest = float(s.iloc[-1])
    baseline = s.iloc[-30:-1] if len(s) >= 31 else s.iloc[:-1]
    mu, sd = baseline.mean(), baseline.std(ddof=1)
    if sd == 0 or np.isnan(sd):
        return {"value": None, "label": None, "reason": "zero_variance_etf_baseline"}
    z = float((latest - mu) / sd)
    label = ("etf_strong_inflow" if z >= 1.0
             else "etf_strong_outflow" if z <= -1.0
             else "etf_neutral")
    return {"value": z, "label": label, "raw_latest_7d_sum": latest,
            "baseline_mean": float(mu), "baseline_std": float(sd)}


# --- 7. MARKER 1.4: kline_volatility_7d ------------------------------
def _marker_kline_volatility_7d(df_klines: pd.DataFrame) -> dict[str, Any]:
    if df_klines.empty or "close" not in df_klines.columns:
        return {"value": None, "label": None, "reason": "no_klines_rows"}
    closes = pd.to_numeric(df_klines["close"], errors="coerce").dropna()
    if len(closes) < 14:
        return {"value": None, "label": None, "reason": "fewer_than_14_closes"}
    log_rets = np.log(closes.astype(float)).diff().dropna()
    if len(log_rets) < 14:
        return {"value": None, "label": None, "reason": "fewer_than_14_returns"}
    vol_7d = float(log_rets.iloc[-7:].std(ddof=1))
    baseline_30d = float(log_rets.std(ddof=1))
    if baseline_30d == 0 or np.isnan(baseline_30d):
        return {"value": None, "label": None, "reason": "zero_variance_returns"}
    ratio = vol_7d / baseline_30d
    label = ("vol_expansion" if ratio >= 1.5
             else "vol_compression" if ratio <= 0.6
             else "vol_baseline")
    return {"value": ratio, "label": label, "raw_vol_7d": vol_7d,
            "raw_baseline_30d": baseline_30d}


# --- 8. MARKER 1.5: news_to_vol_ratio --------------------------------
def _marker_news_to_vol_ratio(m_news_7d: dict, m_vol_7d: dict) -> dict[str, Any]:
    z = m_news_7d.get("value")
    r = m_vol_7d.get("value")
    if z is None or r is None or np.isnan(z) or np.isnan(r):
        return {"value": None, "label": None, "reason": "missing_input"}
    if z >= 1.0 and r >= 1.5:   label = "news_driven_vol"
    elif z >= 1.0 and r <= 0.6: label = "unrealised_news_pressure"
    elif z <= -1.0 and r >= 1.5:label = "price_action_unanchored"
    else:                       label = "news_vol_aligned"
    return {"value": float(z / r if r else float("nan")), "label": label,
            "news_z_7d": float(z), "vol_ratio_7d": float(r)}


# --- 9. AGGREGATOR ---------------------------------------------------
async def compute_macro_regime_markers(
    *, soso_client, sodex_feeds, skip_so_dex_on_failure: bool = True,
) -> dict[str, Any]:
    started = time.perf_counter()
    markers: dict[str, Any] = {}
    diagnostics: dict[str, Any] = {}

    # News
    try:
        df_news = await _pull_featured_news(soso_client)
        diagnostics["news_rows"] = int(len(df_news))
    except Exception as exc:                                       # noqa: BLE001
        df_news = pd.DataFrame()
        diagnostics["news_error"] = f"{type(exc).__name__}: {exc}"
    markers["news_volume_24h"]      = _marker_news_volume_24h(df_news)
    markers["news_volume_7d_zscore"]= _marker_news_volume_7d_zscore(df_news)

    # ETF
    try:
        df_etf = await _pull_etf_inflow(soso_client)
        diagnostics["etf_rows"] = int(len(df_etf))
    except Exception as exc:                                       # noqa: BLE001
        df_etf = pd.DataFrame()
        diagnostics["etf_error"] = f"{type(exc).__name__}: {exc}"
    markers["etf_flow_zscore_7d"]   = _marker_etf_flow_zscore_7d(df_etf)

    # Kline (SoDEX)
    try:
        df_klines = await _pull_btc_klines(sodex_feeds)
        diagnostics["kline_rows"] = int(len(df_klines))
    except Exception as exc:                                       # noqa: BLE001
        df_klines = pd.DataFrame()
        diagnostics["kline_error"] = f"{type(exc).__name__}: {exc}"
        if not skip_so_dex_on_failure:
            raise
    markers["kline_volatility_7d"]  = _marker_kline_volatility_7d(df_klines)

    # Derived
    markers["news_to_vol_ratio"]    = _marker_news_to_vol_ratio(
        markers["news_volume_7d_zscore"], markers["kline_volatility_7d"]
    )

    elapsed = time.perf_counter() - started
    return {
        "markers": markers,
        "diagnostics": diagnostics,
        "elapsed_seconds": round(elapsed, 3),
        "data_provenance": {
            "news":   "sosovalue.featured_news",
            "etf":    "sosovalue.etf_historical_inflow",
            "klines": "sodex.fetch_klines",
        },
        "schema_version": "macro_bench_v1",
    }
```

### 2.3 What the test file *actually asserts*

The test file is the *executable proof* that this end-to-end shape is
real. Concretely, for each of the 5 markers, the test asserts:

1. The marker key is present.
2. The value is one of `None`, `int`, or `float`.
3. The label is one of `None` *or* the documented label set for that
   marker.
4. The `reason` field is set when `value is None` (so an operator can
   tell *why* a label is missing rather than seeing a silent `None`).
5. For the SoSoValue pulls, at least 1 row was actually returned
   (i.e. the call did not silently get a fake zero-row response). If
   the upstream returns zero rows, the test fails honestly — that is
   the entire point of the "no fake tests" mandate.

---

## 3. Skip semantics (when `SOSOVALUE_API_KEY` is unset)

### 3.1 Behaviour contract

The bench is a **live integration test**, not a mocked unit test. The
contract is identical to the existing
`tests/integration/test_sosovalue_live.py:47-49, 96-101`:

| Env state | Behaviour |
|-----------|-----------|
| `SOSOVALUE_API_KEY` **unset** (or empty) | **SKIP the whole module** with `unittest.SkipTest("SOSOVALUE_API_KEY not set")`. No HTTP call. No marker computation. No synthetic fixtures. |
| `SIGLAB_SKIP_SOSOVALUE=1` (or `true`/`yes`) | **SKIP the whole module** even if the key is set. Honours the existing kill switch. |
| Key set, SoSoValue returns 401/403/404/422 on the news/klines/etf endpoints | Treat as **truth-table-mismatch**: skip the *specific* marker with `reason="http_<code>"`, do not fail the test. The test then continues with whatever markers it has and reports the partial result. |
| Key set, SoSoValue returns 429 (rate-limited) | Skip with `unittest.SkipTest("SoSoValue rate-limited")`, identical to the live test at line 91-92. |
| Key set, network transport failure (DNS / TLS / timeout) | Treat each marker independently: skip the affected marker with `reason=<ExceptionClass>: <msg>`, do not fail the test. The test still asserts on whatever markers succeeded. |
| Key set, SoSoValue returns 200 but `data` is `[]` | **FAIL honestly** with a clear message: `"SoSoValue /api/v1/news/featured returned 200 with empty data; refusing to assert against an empty regime"`. This is the explicit "no fake tests" rule. |
| All five markers compute successfully | **PASS** with the full marker payload logged via `print()` for human inspection. |

### 3.2 What the bench logs (when it runs)

Standard `unittest.TestCase` output is fine. The bench additionally
prints the marker payload to stdout in a stable, single-line-per-marker
format so a human can see it without opening an artifact:

```
[macro_bench] SOSOVALUE_API_KEY set: True
[macro_bench] elapsed_seconds=2.314
[macro_bench] news_volume_24h       value=37    label=news_surge      reason=None
[macro_bench] news_volume_7d_zscore value= 1.84 label=news_surge_7d  reason=None
[macro_bench] etf_flow_zscore_7d    value= 0.42 label=etf_neutral    reason=None
[macro_bench] kline_volatility_7d   value= 1.27 label=vol_baseline   reason=None
[macro_bench] news_to_vol_ratio     value= 1.45 label=news_vol_aligned reason=None
```

When a marker is skipped:

```
[macro_bench] news_volume_7d_zscore value=None  label=None reason=fewer_than_14_days_of_history
```

This log format is chosen so a human can grep `[macro_bench]` out of a
CI log. It is *not* a contract that any other code depends on.

### 3.3 Why this contract (not a default-zero fallback)

The existing `evaluate_gates` returns `(False, ["insufficient_breadth"])`
when the asset breadth is 0
(`siglab/evaluation/gates.py:62-66`). The regime classifier returns
`None` for the label when the threshold is `None`
(`siglab/evaluation/runner.py:2191-2207`). The macro bench follows that
same convention: **absence of evidence → absence of a claim**, not a
defaulted "neutral" value that would silently wash out real regime
changes. A market_report that includes `"kline_volatility_7d":
{"value": null, "label": null, "reason": "fewer_than_14_closes"}` is
honest in a way that `"kline_volatility_7d": 0.0` is not.

---

## 4. The 1 new test file path

`tests/bench/test_bench_sosovalue_macro.py`

**Not** `tests/integration/test_sosovalue_macro.py` (despite the live
HTTP calls) because the existing `tests/bench/` directory already
follows this pattern: a bench is *also* a live probe, but it is
colocated with `tests/bench/test_bench_sodex_ws.py` and
`tests/bench/test_bench_cli_help.py`, not with the broader
`tests/integration/` collection. This keeps the `pytest -m integration`
gate (declared in `pyproject.toml:36-41`) from accidentally including
the macro bench in the integration suite — the bench runs with
`pytest tests/bench/` and is part of the standard suite, not the
optional integration run.

There is no `__init__.py` in `tests/bench/` today (see the
`tests/bench/` listing: it has only the two test files and a
`__pycache__/`); the new file is consistent with the existing layout.

---

## 5. Concrete test methods (5–7)

Five marker tests + one orchestrator + one honesty guard. Each is
intentionally a single `async def test_…` so the bench reads top-down
by marker and so partial failures (one marker 429s, others succeed) do
not cascade.

| # | Test method | What it actually does | Honest about |
|---|-------------|----------------------|--------------|
| 1 | `test_bench_macro_marker_news_volume_24h` | Pull `/api/v1/news/featured?page=1&page_size=100` live; assert `value ∈ {int, None}`, `label ∈ {None, "news_surge", "news_quiet", "news_baseline"}`; print the raw 24h count. | Whether the featured-news endpoint actually returns rows for the day. |
| 2 | `test_bench_macro_marker_news_volume_7d_zscore` | Same news pull; compute the 30d z-score; assert `value ∈ {float, None}`, `label ∈ {None, "news_surge_7d", "news_drought_7d", "news_baseline_7d"}`; require `len(daily) >= 14` to return a non-None label. | Whether 30 days of news history are available on a free Demo key (the operator's truth-table claim about history depth). |
| 3 | `test_bench_macro_marker_etf_flow_zscore_7d` | Pull `/etfs/summary-history?symbol=BTC&country_code=US` live; assert `value ∈ {float, None}`, `label ∈ {None, "etf_strong_inflow", "etf_strong_outflow", "etf_neutral"}`; require at least 14 valid 7-day rolling sums. | Whether the BTC ETF endpoint actually returns > 14 daily rows (it should; the live smoke at `test_sosovalue_live.py:130-150` already passes for a single call). |
| 4 | `test_bench_macro_marker_kline_volatility_7d` | Pull SoDEX `/markets/BTC/klines?interval=1d&limit=30` via `SoDEXFeeds.fetch_klines`; assert `value ∈ {float, None}`, `label ∈ {None, "vol_expansion", "vol_compression", "vol_baseline"}`; require `len(log_rets) >= 14`. | Whether the SoDEX kline endpoint gives 30 daily bars (i.e. whether the bench is honest about "30d baseline"). |
| 5 | `test_bench_macro_marker_news_to_vol_ratio` | Combine the 1.2 and 1.4 results in-process; assert `label ∈ {None, "news_driven_vol", "unrealised_news_pressure", "price_action_unanchored", "news_vol_aligned"}`; assert label is `None` whenever either input is `None`. | The bench is honest about not synthesising the derived label when its inputs are missing. |
| 6 | `test_bench_macro_orchestrator_end_to_end` | Run `compute_macro_regime_markers(...)` once; assert the returned dict has all 5 marker keys, the diagnostics block has `news_rows`/`etf_rows`/`kline_rows` integer counts, and `elapsed_seconds < 15.0` (loose budget — the existing `tests/bench/test_bench_cli_help.py:26` and `tests/bench/test_bench_sodex_ws.py:26` use loose budgets in the same 5–8s range). | The full pipeline is real, not a unit test pretending. |
| 7 | `test_bench_macro_honest_skip_when_no_key` | When `SOSOVALUE_API_KEY` is unset, the bench must SKIP (not fail, not fake-zeros). Uses a `monkeypatch`-style env override (or `unittest.SkipTest` directly when the key is already empty). | The bench is not silently producing false data when the operator forgets to configure the key. |

Total: 7 tests, all live. No mocks, no fixtures, no synthesised DataFrames.

### 5.1 Shared test base (mirrors `_LiveBase`)

```python
class _MacroBenchBase(unittest.IsolatedAsyncioTestCase):
    """Skip-cleanly when SOSOVALUE_API_KEY is unset, like the live SoSoValue test."""

    async def asyncSetUp(self) -> None:
        _skip_if_disabled()
        if not _api_key():
            raise unittest.SkipTest(f"{API_KEY_ENV_VAR} not set")
        # Import lazily so a missing key (or no network) never costs
        # anything at collection time.
        from siglab.config import load_settings
        from siglab.data.sosovalue_client import SoSoValueClient
        from siglab.data.sodex_feeds import SoDEXFeeds
        from siglab.data.store import ParquetLake

        self.settings = load_settings()
        self.lake = ParquetLake(self.settings.data_lake_dir)
        self.soso = SoSoValueClient(api_key=_api_key(), timeout_s=20.0, retries=1)
        self.feeds = SoDEXFeeds(lake=self.lake)

    async def asyncTearDown(self) -> None:
        await self.soso.close()
```

---

## 6. How to wire into `market_report` (so operators see the macro markers)

### 6.1 What we are NOT changing (smaller-delta commitment)

- `siglab/research/hypothesis.py` — **0 edits** (per the assignment).
- `siglab/evaluation/runner.py` (and the rest of `siglab/evaluation/`)
  — **0 edits** in this delta.
- `siglab/data/sosovalue_client.py` — **0 edits** in this delta.
- `siglab/data/sodex_feeds.py` — **0 edits** in this delta.
- `siglab/evaluation/macro_bench.py` — does **not** exist yet; we are
  not creating it in this delta either (see 6.2).

The only code-level change outside the test file is the **one-line
config flag** described in 6.3.

### 6.2 What we ARE changing in this delta

Exactly one file: `tests/bench/test_bench_sosovalue_macro.py`.

The "wire into market_report" step is the **next delta** (P3). This
plan stops at the point where the bench proves the 5 markers are
real; the next delta is the one that teaches `market.py:82-182` to
read a precomputed `runs/macro_bench_latest.json` and surface it in
the operator view.

### 6.3 The 1-line config flag

The bench is opt-in via an env var so a CI host without the key can
still collect the rest of the suite. The default is **off** for safety:

```python
# In tests/bench/test_bench_sosovalue_macro.py (NEW file, top-level)
RUN_MACRO_BENCH_ENV_VAR = "SIGLAB_RUN_MACRO_BENCH"
# honour: "1"/"true"/"yes" -> run; anything else (including unset) -> skip
```

This is the *only* config change. It lives inside the test file
itself (not in `siglab/config.py`), so the production CLI surface
is untouched. To enable: `SIGLAB_RUN_MACRO_BENCH=1
SOSOVALUE_API_KEY=… pytest tests/bench/`.

### 6.4 The downstream wire (P3, not in this delta)

For documentation continuity, the P3 wire will look like this — this
is **not** implemented now, only described so the reviewer can
sanity-check the contract.

```python
# Pseudocode for P3 — DO NOT IMPLEMENT IN THIS DELTA
# Lives in siglab/cli/market.py around line 141-148 (signal_summary)

macro = _load_macro_bench_latest()                # reads runs/macro_bench_latest.json
signal["macro_regime"] = {
    "news_volume_24h":       macro["markers"]["news_volume_24h"],
    "news_volume_7d_zscore": macro["markers"]["news_volume_7d_zscore"],
    "etf_flow_zscore_7d":    macro["markers"]["etf_flow_zscore_7d"],
    "kline_volatility_7d":   macro["markers"]["kline_volatility_7d"],
    "news_to_vol_ratio":     macro["markers"]["news_to_vol_ratio"],
    "provenance":            macro["data_provenance"],
    "generated_at":          macro.get("generated_at"),
}
```

And the dashboard surface (`siglab/dashboard/static/ops.js:44-46`) gets
a 6th line:

```js
["Macro Regime", valueLabel(market.macro_regime?.news_to_vol_ratio?.label || "unknown")],
```

Those edits are P3. This delta (P2) is the *honest test* that proves
the 5 markers can be computed from real traffic. No operator wire
until that proof exists.

---

## 7. Smaller-delta summary

| File | Edits in this delta |
|------|---------------------|
| `tests/bench/test_bench_sosovalue_macro.py` | **NEW** (the only new file) |
| `siglab/research/*` | 0 |
| `siglab/evaluation/*` | 0 |
| `siglab/data/sosovalue_client.py` | 0 |
| `siglab/data/sodex_feeds.py` | 0 |
| `siglab/cli/market.py` | 0 (P3 wire only) |
| `siglab/dashboard/static/ops.js` | 0 (P3 wire only) |
| `siglab/config.py` | 0 (the 1-line flag is internal to the test file) |
| `config.example.json` | 0 |

The only thing the bench depends on that is *not* its own file is:
- `siglab.data.sosovalue_client.SoSoValueClient.featured_news_pages`
  (already implemented, line 160)
- `siglab.data.sosovalue_client.SoSoValueClient.etf_historical_inflow`
  (already implemented, line 133)
- `siglab.data.sodex_feeds.SoDEXFeeds.fetch_klines` (already
  implemented, line 164)
- `siglab.data.store.ParquetLake` (already implemented, used by
  `SoDEXFeeds` for its kline cache)

All four are part of the live "What Is Real Now" surface in
`README.md:11-30`. The bench adds *zero* new wiring to the production
tree; it consumes the existing wire and asserts against real upstream
data.

---

## 8. Honest report on what this bench will reveal (preview)

Because the assignment asks for "honest report on how UNFUNCTIONAL
SigLab is against real traffic", the bench is designed to *expose*
gaps, not to dress them up. Concrete expected outcomes from a
`pytest tests/bench/test_bench_sosovalue_macro.py` run today
(2026-06-14, with the supplied `SOSOVALUE_API_KEY`):

- **Test 1 (news 24h)**: almost certainly passes. The
  `featured_news_pages` wrapper is implemented
  (`sosovalue_client.py:160-188`) and the live integration test at
  `tests/integration/test_sosovalue_live.py:179-184` already confirms
  the endpoint returns rows.
- **Test 2 (news 7d zscore)**: depends on whether 30 days of news
  history is accessible via the Demo key. The free Demo key from
  `tests/integration/test_sosovalue_live.py:1-23` is rate-limited and
  only fetches `page=1` of the current listing. We may get
  `label=None, reason="fewer_than_14_days_of_history"` — that is
  the bench telling the truth: the SoSoValue featured-news endpoint,
  as currently exposed, does not give us 30 days of history in a
  single call. This is a *finding*, not a failure.
- **Test 3 (ETF 7d zscore)**: likely passes. The
  `etf_historical_inflow` wrapper is implemented
  (`sosovalue_client.py:133-145`) and the live smoke at
  `test_sosovalue_live.py:130-150` already pulls rows.
- **Test 4 (kline vol 7d)**: passes if the SoDEX WS kline cache
  (`sodex_feeds.py:209-217`) has 30 daily bars. With a 30-day TTL
  and a fresh run, this is realistic.
- **Test 5 (news-to-vol ratio)**: passes iff tests 2 and 4 both have
  non-None values. If test 2 is None for the documented reason, test
  5 is also None — and the test asserts that explicitly.
- **Test 6 (orchestrator)**: passes if at least 3 of 5 markers
  produce non-None values within 15s. The budget is loose on purpose.
- **Test 7 (honest skip)**: passes unconditionally (it asserts the
  skip path, not the data path).

The bench will produce a JSON-shaped honest report (one
`compute_macro_regime_markers()` payload per run) that the operator
can read and decide: is the SoSoValue news depth enough? Is the SoDEX
kline depth enough? Those are the questions the existing
`BLOCKED` truth table
(`siglab/data/sosovalue_capabilities.py:46-56, 82-92`) cannot answer
by itself; this bench answers them with measured numbers.

---

## 9. Open questions for the implementer (one list, not a TODO tree)

1. Should the orchestrator return a frozen dataclass or a plain dict?
   Plan says plain dict for `json.dumps` round-trippability.
2. Should the bench also assert on `data_provenance`? Plan says no —
   the provenance block is *self-reported by the bench*; asserting on
   it would be circular. The diagnostic counts (e.g. `news_rows`) are
   asserted instead.
3. Is `monkeypatch.setenv("SOSOVALUE_API_KEY", "")` enough for test 7,
   or do we need to wrap the key check in a function so it can be
   overridden? The existing `tests/integration/test_sosovalue_live.py:52-53`
   uses `os.environ.get(API_KEY_ENV_VAR)`, so `monkeypatch.delenv` is
   the cleanest way.
4. Should we also assert that the 5 marker `label` sets are disjoint?
   The plan claims they are, but a test that locks it down is cheap.
   Add to test 6 if time permits; not required for the first run.

---

## 10. Summary

- **5 markers** are defined, each with a concrete formula, a source
  endpoint (all already implemented), and a label rule that
  respects the existing "insufficient evidence → label is `None`"
  contract.
- **1 new file**: `tests/bench/test_bench_sosovalue_macro.py`,
  containing the bench and the 7 tests.
- **1 config flag**: `SIGLAB_RUN_MACRO_BENCH` (off by default),
  declared *inside* the test file, not in `siglab/config.py`.
- **0 edits** to `siglab/research/*`, `siglab/evaluation/*`,
  `siglab/data/sosovalue_client.py`, or any other production file.
- **0 mocks, 0 fixtures, 0 synthesised DataFrames** in the new test
  file. Every value is the result of a real HTTP call to either
  SoSoValue or SoDEX.
- **Operator wire is P3**, not P2. The bench proves the data is
  real; the dashboard / `market_report` integration is a separate
  delta.
