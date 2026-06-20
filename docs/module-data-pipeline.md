# Data Pipeline and API Integrations

## Purpose

The data pipeline fetches market data from external APIs (SoDEX and SoSoValue), caches it locally in Parquet/JSON files via `ParquetLake`, normalizes it into pandas DataFrames, and exposes it through a FastAPI dashboard and WebSocket server for the TUI/CLI consumers.

```
External APIs → Client Layer → Feeds Layer → ParquetLake (cache) → Dashboard REST/WS → TUI/CLI
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          External APIs                                  │
│  ┌──────────────────────────┐  ┌─────────────────────────────────────┐  │
│  │  SoDEX Perp REST API     │  │  SoSoValue OpenAPI / ETF / News    │  │
│  │  mainnet-gw.sodex.dev    │  │  openapi.sosovalue.com             │  │
│  └────────────┬─────────────┘  └──────────────┬──────────────────────┘  │
└───────────────┼───────────────────────────────┼─────────────────────────┘
                │                               │
                ▼                               ▼
┌───────────────────────────┐  ┌────────────────────────────────────────┐
│  SoDEXPublicPerpsClient   │  │  SoSoValueClient                      │
│  (live/sodex_client.py)   │  │  (data/sosovalue_client.py)           │
│  - HTTP, retries, metrics │  │  - HTTP, retries, rate-limit, cache   │
└────────────┬──────────────┘  └──────────────┬─────────────────────────┘
             │                                │
             ▼                                │
┌───────────────────────────┐                 │
│  SoDEXFeeds               │                 │
│  (data/sodex_feeds.py)    │                 │
│  - ParquetLake caching    │                 │
│  - DataFrame conversion   │                 │
└────────────┬──────────────┘                 │
             │                                │
             ▼                                ▼
┌────────────────────────────────────────────────────────────────────────┐
│  MarketDataProvider  (data/feeds.py)                                    │
│  - Unified feeds layer combining SoDEX + SoSoValue                    │
│  - Perp bundle builder (klines + funding rates)                       │
│  - Symbol discovery, PT/lending market discovery                      │
│  - Iteration bundle lifecycle                                         │
└────────────────────────┬──────────────────────────────────────────────┘
                         │
                         ▼
┌────────────────────────────────────────────────────────────────────────┐
│  ParquetLake  (data/store.py)                                          │
│  - namespace/key directory structure                                    │
│  - Timestamped .parquet and .json files                               │
│  - TTL-based freshness checks                                         │
│  - prune / prune_all cleanup                                          │
└────────────────────────┬──────────────────────────────────────────────┘
                         │
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
┌──────────────┐ ┌──────────────┐ ┌───────────────────┐
│ Dashboard    │ │ EvidenceStore│ │ TUI / CLI         │
│ FastAPI      │ │ (JSONL)      │ │ (live/runtime.py) │
│ REST + WS    │ │              │ │                   │
└──────────────┘ └──────────────┘ └───────────────────┘
```

---

## SoDEX Integration

### Client: `SoDEXPublicPerpsClient`

Located in `siglab/live/sodex_client.py`. Handles HTTP transport to the SoDEX perp REST API.

| Parameter | Default | Description |
|---|---|---|
| `base_url` | `https://mainnet-gw.sodex.dev/api/v1/perps` | Perp REST API base |
| `timeout_s` | `10.0` | Per-request timeout |
| `retries` | `1` | Retries on transport/5xx |

### Public REST Endpoints

| Method | Path | Description |
|---|---|---|
| `symbols()` | `GET /markets/symbols` | All tradable perp symbols with metadata (name, precision, margin tiers, status) |
| `coins(coin?)` | `GET /markets/coins` | All tradeable coin/asset metadata |
| `klines(symbol, interval, limit)` | `GET /markets/{symbol}/klines` | Candlestick data |
| `tickers(symbol?)` | `GET /markets/tickers` | 24h ticker statistics |
| `mini_tickers(symbol?)` | `GET /markets/miniTickers` | Lightweight 24h ticker (minimal fields) |
| `mark_prices(symbol?)` | `GET /markets/mark-prices` | Mark prices, index prices, funding rates |
| `book_tickers(symbol?)` | `GET /markets/bookTickers` | Best bid/ask |
| `orderbook(symbol, limit)` | `GET /markets/{symbol}/orderbook` | Order book depth |
| `trades(symbol, limit)` | `GET /markets/{symbol}/trades` | Recent trades |

### Supported Kline Intervals

`1m`, `5m`, `15m`, `30m`, `1h`, `4h`, `1d`, `1w`, `1M`

### Kline DataFrame Columns

`timestamp` (DatetimeIndex), `open`, `high`, `low`, `close`, `volume`, `quote_volume`

### SoDEXFeeds Caching Layer

`SoDEXFeeds` (in `siglab/data/sodex_feeds.py`) wraps the raw client with `ParquetLake` caching:

| Data Type | Cache TTL | Cache Namespace |
|---|---|---|
| Klines | 1 hour | `sodex_klines` |
| Symbols | 24 hours | `sodex_symbols` |
| Tickers | 15 minutes | `sodex_tickers` |
| Mark Prices | 15 minutes | `sodex_mark_prices` |
| Book Tickers | ~5 minutes | `sodex_book_tickers` |
| Orderbook | ~2 minutes | `sodex_orderbook` |
| Recent Trades | ~5 minutes | `sodex_trades` |

All endpoints accept `skip_cache=True` to bypass the lake. Nonexistent symbols return empty data instead of raising.

### Error Hierarchy

All SoDEX errors extend `SoDEXError`:

- `SoDEXTransportError` — network, DNS, TLS, timeout
- `SoDEXRateLimitError` — HTTP 429
- `SoDEXUpstreamError` — business logic / HTTP error
- `SoDEXFormatError` — malformed response envelope

---

## SoSoValue Integration

### Client: `SoSoValueClient`

Located in `siglab/data/sosovalue_client.py`. An async HTTP client with in-memory TTL cache, exponential backoff, and per-endpoint metrics.

### Configuration

| Parameter | Default | Description |
|---|---|---|
| `api_key` | (required) | Passed as `x-soso-api-key` header |
| `timeout_s` | `10.0` | Overall request timeout |
| `connect_timeout_s` | `3.0` | Connection establishment timeout |
| `write_timeout_s` | `5.0` | Write body timeout |
| `pool_timeout_s` | `3.0` | Connection pool wait timeout |
| `retries` | `2` | Retry count for retryable errors |
| `max_concurrency` | `8` | Max concurrent requests |
| `conservative_rate_limit_per_minute` | `20` | Client-side rolling window limit |

### API Endpoints

| Method | Base URL | Path | TTL (s) |
|---|---|---|---|
| `etf_historical_inflow()` | etf_base_url | `POST /openapi/v2/etf/historicalInflowChart` | 300 |
| `etf_current_metrics()` | etf_base_url | `POST /openapi/v2/etf/currentEtfDataMetrics` | 300 |
| `listed_currencies()` | openapi_base_url | `POST /data/default/coin/list` | 86400 |
| `currency_market_snapshot(id)` | openapi_base_url | `GET /currencies/{id}/market-snapshot` | 30 |
| `currency_klines(id)` | openapi_base_url | `GET /currencies/{id}/klines` | 60 |
| `etf_list(symbol, country_code)` | openapi_base_url | `GET /etfs` | 60 |
| `etf_summary_history()` | openapi_base_url | `GET /etfs/summary-history` | 60 |
| `etf_market_snapshot(ticker)` | openapi_base_url | `GET /etfs/{ticker}/market-snapshot` | 60 |
| `featured_news()` | news_base_url | `GET /api/v1/news/featured` | 60 |
| `featured_news_pages()` | news_base_url | (paginates `featured_news`) | 60 |
| `featured_news_by_currency()` | news_base_url | `GET /api/v1/news/featured/currency` | 60 |
| `featured_news_by_currency_pages()` | news_base_url | (paginates `featured_news_by_currency`) | 60 |

### Endpoint URLs

```
openapi_base_url = https://openapi.sosovalue.com/openapi/v1
etf_base_url     = https://api.sosovalue.xyz
news_base_url    = https://openapi.sosovalue.com
```

### Error Hierarchy

All errors extend `SoSoValueApiError`:

- `SoSoValueConfigError` — missing API key or invalid config
- `SoSoValueAuthError` — HTTP 401/403
- `SoSoValueRateLimitError` — HTTP 429
- `SoSoValueUpstreamFormatError` — malformed JSON or invalid business shape
- `SoSoValueRetryableError` — 5xx (auto-retried)
- `SoSoValueUpstreamServerError` — extends retryable
- `SoSoValueTransportError` — network/DNS/TLS/socket failure
- `SoSoValueEmptyDataError` — endpoint returned no rows

### Rate Limiting

The client enforces a process-local rolling window of `conservative_rate_limit_per_minute` (default 20) calls. When the budget is exhausted, it sleeps until the oldest call in the window expires. The documented upstream limit per the SoSoValue developer portal applies per API key/plan.

---

## MarketDataProvider

`MarketDataProvider` (in `siglab/data/feeds.py`) is the unified feeds layer that combines SoDEX and SoSoValue data into structured bundles for the research pipeline.

### Constructor

```python
MarketDataProvider(settings: SiglabConfig, lake: ParquetLake, config_path?, sodex_feeds?)
```

### Perp Bundle Builder

`fetch_perp_bundle(symbols, lookback_days, interval)` builds a perp bundle by:

1. Fetching klines from SoDEX for each symbol (`_fetch_perp_bundle_sodex`)
2. Aligning all price series to a common timestamp index
3. Fetching funding rates from SoDEX mark-prices snapshot
4. Returning `{prices: DataFrame, funding: DataFrame, source, bundle_as_of, bundle_id}`

The `prices` DataFrame has one column per symbol (e.g., `BTC`, `ETH`), indexed by timestamp. The `funding` DataFrame mirrors the same shape with current funding rates.

### Symbol Discovery

`discover_perp_symbols(preferred_symbols, limit)` resolves symbol lists:
- Uses `MAJOR_PERP_SYMBOLS` as fallback: `BTC, ETH, SOL, HYPE, DOGE, BNB, XRP, SUI`
- Synthetic stable labels (e.g., `USD`) are filtered out
- Results cached in `_warm_cache`

### Research Summary

`build_research_summary(track, parent)` assembles a full market context for a signal spec, including:
- Perp bundle (prices + funding)
- Pair calibration statistics (volatility, correlation, residual z-scores)
- Stable PT markets (for yield_flows track)
- Lending markets (for yield_flows track)

### Iteration Bundles

Bundle lifecycle managed via `begin_iteration_bundle()`, `current_bundle_context()`, and `clear_iteration_bundle()`. Each bundle gets a unique `bundle_id` (SHA-256 hash) and is persisted to `ParquetLake` as a manifest.

---

## ParquetLake

`ParquetLake` (in `siglab/data/store.py`) is the local cache backend.

### File Structure

```
<root>/
  <namespace>/
    <key>/
      20250101T120000Z.parquet    # DataFrame cache
      20250101T120000Z.json       # JSON payload cache
```

- Namespace and key are sanitized (non-alphanumeric characters replaced with `_`)
- Each write creates a new timestamped file (UTC ISO-style naming)
- `latest_frame()` / `latest_json()` reads the most recent file by sorted filename

### TTL-Based Freshness

All read methods accept `max_age_hours`. If the latest file's modification time is older than `now - max_age_hours`, the cache is treated as expired and `None` is returned.

### Pruning

- `prune(namespace, key, max_age_hours)` — removes stale `.parquet` and `.json` files in a single namespace/key directory
- `prune_all(default_max_age_hours)` — iterates all namespace/key directories and prunes each

### Default Cache TTLs Used Across the System

| Data | TTL |
|---|---|
| SoDEX klines | 1 hour |
| SoDEX symbols | 24 hours |
| SoDEX tickers | 15 min |
| SoDEX mark prices | 15 min |
| SoDEX book tickers | ~5 min |
| SoDEX orderbook | ~2 min |
| SoSoValue ETF inflow | 6 hours |
| SoSoValue currencies | 24 hours |
| SoSoValue klines | 60 sec |
| Pendle PT markets | 12 hours |
| Delta Lab lending | 6 hours |

---

## EvidenceStore

`EvidenceStore` (in `siglab/data/evidence.py`) manages JSONL-based evidence records.

### Storage Format

- **Path**: Single `.jsonl` file (one JSON object per line)
- **Append-only**: New records are appended; never mutated in place
- **Dedup**: Each record gets a deterministic `evidence_id` (SHA-256 of source, entity, module, relation, timestamp, value, evidence_path). Duplicates are skipped on write.

### EvidenceRecord Schema

| Field | Type | Description |
|---|---|---|
| `source` | string | Data origin (e.g., `sosovalue.etf_historical_inflow`) |
| `observed_at` | string | ISO timestamp of observation |
| `entity` | string | Subject entity (e.g., `us-btc-spot`, `BTC-USD`) |
| `module` | string | Module category (`ETF`, `Feeds`, `SoDEX`) |
| `relation` | string | Relationship type (e.g., `total_net_inflow`, `news_mention`) |
| `confidence` | float | 0.0–1.0 |
| `evidence_path` | string | Path to raw evidence artifact |
| `timestamp` | string? | Optional event timestamp |
| `value` | any | Primary value |
| `attributes` | dict | Additional metadata |

### Evidence Factories

- `etf_inflow_evidence(rows, etf_type, ...)` — converts ETF inflow rows into evidence records
- `news_evidence(rows, ...)` — converts news items into evidence records
- `sodex_ws_evidence(update, ...)` — converts SoDEX WebSocket updates into evidence records

### Querying and Linking

- `query(entity?, module?, relation?, limit)` — filter records by field values
- `linked_relations(max_day_gap)` — links news mentions to ETF inflows within a time window (temporal/categorical only, not causal)
- `summary(max_day_gap, top_links)` — generates aggregate statistics: record counts by module/relation/source/entity, plus top linked events

---

## Dashboard API

FastAPI application in `siglab/dashboard/app.py` with routes in `siglab/dashboard/routes.py`.

### REST Endpoints

| Method | Path | Description |
|---|---|---|
| `GET /health` | Returns `{status, version, uptime_seconds}` |
| `GET /config` | Full `SiglabConfig` as JSON (grouped by section: system, sosovalue, claude) |
| `GET /ops-board` | Consolidated ops-board: artifact status, buildathon summary, service health |
| `GET /evidence-graph` | Evidence graph with nodes (sources + entities) and edges (linked relations) |
| `GET /skill-report` | Per-skill metrics aggregated from experiment tool traces (usage count, latency, tokens, classification) |
| `GET /risk` | Portfolio risk metrics: composite score, max drawdown, Sharpe ratio, correlation matrix, drawdown history, alerts |
| `GET /market/symbols` | All tradable SoDEX perp symbols |
| `GET /market/tickers` | 24-hour ticker data for all SoDEX perps |
| `GET /market/klines/{symbol}` | Kline/candlestick data (`?interval=1h&limit=60`) |
| `GET /market/orderbook/{symbol}` | Order book depth (`?limit=20`) |

### Dashboard State

`DashboardState` holds runtime context:

- `config: SiglabConfig` — loaded settings
- `lineage: LineageStore` — experiment lineage database
- `ws_manager: WebSocketManager` — active WebSocket connections
- `_sodex_feeds: SoDEXFeeds` — lazily initialized

### CORS

All origins allowed (`*`), all methods, all headers, with credentials.

---

## WebSocket

Endpoint: `ws://<host>:<port>/ws` (in `siglab/dashboard/ws.py`)

### Protocol

Messages are JSON with an `action` (or `type`) field.

### Client → Server Messages

| Action | Fields | Description |
|---|---|---|
| `subscribe` | `symbol`, `subscription_type` | Subscribe to data stream |
| `unsubscribe` | `symbol` | Unsubscribe (empty = all) |
| `get_positions` | — | Request current paper trading positions |
| `get_risk` | — | Request current risk metrics snapshot |
| `ping` | — | Keepalive (replies with `pong`) |

### Subscription Types

| `subscription_type` | Description |
|---|---|
| `klines` | Kline/candlestick data (initial snapshot of 5 bars) |
| `ticks` / `ticker` | Bid/ask/last price snapshot |
| `positions` | Paper trading positions |
| `risk_score` | Periodic risk score push (every 15 seconds) |

### Server → Client Messages

| Type | Description |
|---|---|
| `connected` | Welcome message on connection |
| `subscribed` | Confirmation of subscription |
| `unsubscribed` | Confirmation of unsubscription |
| `klines` | Kline data array |
| `ticker` | Bid/ask/last price |
| `positions` | Paper trading positions |
| `risk_score` | Risk metrics (composite score, max drawdown, Sharpe, correlation matrix) |
| `ping` / `pong` | Keepalive |
| `error` | Error message |

### Keepalive

If no message is received within 30 seconds, the server sends a `ping`. The client is expected to respond with a `pong`.

---

## Error Handling

### SoDEX Retry Logic

- 1 retry on transport errors and HTTP 5xx
- Upstream business errors (`SoDEXUpstreamError`) on nonexistent symbols are caught and return empty data
- Per-endpoint latency percentiles (p50, p95) tracked in `metrics_snapshot()`

### SoSoValue Retry Logic

- 2 retries (configurable) on transport errors, 429, and 5xx
- Exponential backoff: `base = min(2.0, 0.25 * 2^attempt) + jitter(0–25%)`
- In-flight deduplication: concurrent requests to the same cache key share a single `asyncio.Task`
- Client-side rate limiter (rolling window per minute)

### Error Classification Summary

| Category | SoDEX | SoSoValue |
|---|---|---|
| Auth failure | — | `SoSoValueAuthError` (401/403) |
| Rate limit | `SoDEXRateLimitError` (429) | `SoSoValueRateLimitError` (429) |
| Transport | `SoDEXTransportError` | `SoSoValueTransportError` |
| Upstream error | `SoDEXUpstreamError` | `SoSoValueUpstreamServerError` |
| Malformed response | `SoDEXFormatError` | `SoSoValueUpstreamFormatError` |
| Empty data | (returns empty) | `SoSoValueEmptyDataError` |
| Missing config | — | `SoSoValueConfigError` |

### Timeout Configuration

| Component | Parameter | Default |
|---|---|---|
| SoDEX per-request | `timeout_s` | 10.0s |
| SoSoValue overall | `timeout_s` | 10.0s |
| SoSoValue connect | `connect_timeout_s` | 3.0s |
| SoSoValue write | `write_timeout_s` | 5.0s |
| SoSoValue pool | `pool_timeout_s` | 3.0s |
| WebSocket receive | asyncio.wait_for | 30.0s |
| Risk push interval | periodic task | 15.0s |

---

## Configuration

### config.json

The main configuration file (`config.json`) maps to `SiglabConfig` fields:

| Key | Description |
|---|---|
| `sosovalue_api_key` / `sosovalue_api_key_override` | SoSoValue API key |
| `sosovalue_openapi_base_url` | OpenAPI base URL |
| `sosovalue_etf_base_url` | ETF API base URL |
| `sosovalue_news_base_url` | News API base URL |
| `sosovalue_timeout_s` | SoSoValue request timeout |
| `sosovalue_retries` | SoSoValue retry count |
| `root_dir` | Project root |
| `data_lake_dir` | ParquetLake root (default: `data/cache`) |
| `artifact_dir` | Runs/artifacts directory |
| `live_dir` | Live paper trading data |
| `ancestry_db_path` | SQLite lineage database |

### Environment Variables

| Variable | Description |
|---|---|
| `SOSOVALUE_API_KEY` | SoSoValue API key (fallback for config.json) |
| `SOLODEX_BASE_URL` | Override SoDEX base URL |

### API Key Authentication

- **SoSoValue**: Every request includes `x-soso-api-key` header
- **SoDEX public endpoints**: No authentication required
- **SoDEX signed writes**: Requires account ID, API key name, nonce store, and signer material (see `siglab/live/sodex_signing.py`)

---

## Testing

### Run All Tests

```bash
python3 -m pytest -q
```

### Run Data Pipeline Tests Only

```bash
python3 -m pytest tests/ -q -k "sodex or sosovalue or feeds or store or evidence or dashboard"
```

### Key Test Files

- `tests/test_cli_agent_safety.py` — CLI agent safety tests
- Additional data pipeline tests are in `tests/` (search for modules matching `sodex`, `sosovalue`, `feeds`, `store`, `evidence`)

### Profile Command

```bash
python3 -m siglab.cli profile --strict --json
```

This validates the full system configuration including API connectivity and data pipeline readiness.
