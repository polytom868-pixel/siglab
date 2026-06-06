# Dashboard & API Server

## Purpose

The SigLab Dashboard provides a consolidated interface for monitoring experiments, risk metrics, market data, and operational status. It exposes three surfaces:

- **FastAPI REST API** (`routes.py`) — health, config, ops-board, evidence graph, skill report, risk metrics, and SoDEX market data endpoints. Runs on port **3100** by default.
- **FastAPI WebSocket** (`ws.py`) — real-time streaming of klines, tickers, positions, and risk scores.
- **Legacy HTTP server** (`server.py`) — a `ThreadingHTTPServer`-based dashboard with static file serving and experiment/run/ops API endpoints. Runs on port **8765** by default. Includes a richer `DashboardApp` class that powers experiment detail views, run summaries, ops payloads, skill value reports, and live deployment.

---

## Architecture

### FastAPI App (`app.py`)

`create_app()` builds a `FastAPI` instance with:

- **Lifespan manager** (`lifespan`): initializes `DashboardState` on startup — loads `SiglabConfig` (falls back to a minimal config on failure) and opens a `LineageStore` connection.
- **CORS middleware**: `allow_origins=["*"]` — fully permissive.
- **Two routers**: the REST API router (`routes.router`) and the WebSocket router (`ws.router`).

The module-level `app = create_app()` is the ASGI entrypoint referenced by `uvicorn`.

### Route Organization

| File | Prefix | Purpose |
|------|--------|---------|
| `routes.py` | `/health`, `/config`, `/ops-board`, `/evidence-graph`, `/skill-report`, `/risk`, `/market/*` | REST endpoints |
| `ws.py` | `/ws` | WebSocket streaming |
| `server.py` | `/api/*`, `/` (static) | Legacy HTTP server with experiment/run/ops APIs and static HTML. Runs on port **8765** by default with its own experiment detail, run summary, ops payload, skill report, and live deployment endpoints. |

### State Management

**`DashboardState`** (defined in `app.py`) holds all runtime state:

```python
class DashboardState:
    config: SiglabConfig | None          # Loaded configuration
    lineage: LineageStore | None         # SQLite-backed experiment lineage
    start_time: float                    # Epoch time for uptime calculation
    ws_manager: WebSocketManager         # Active WebSocket connection manager
    _sodex_feeds: Any | None      # Lazy-initialized market data client
```

- Attached to `app.state.dashboard` during the lifespan startup.
- **Lazy initialization**: `get_sodex_feeds()` creates a `SoDEXFeeds(ParquetLake(...))` on first call, caching the result. Returns `None` if imports fail (numpy/SoDEXFeeds unavailable).

---

## REST Endpoints

### `/health` — `GET`

Returns service health status.

| Field | Type | Description |
|-------|------|-------------|
| `status` | `string` | Always `"ok"` |
| `version` | `string` | `"0.1.0"` |
| `uptime_seconds` | `float` | Seconds since server startup |

### `/config` — `GET`

Returns the full `SiglabConfig` as JSON, grouped into `system`, `sosovalue`, and `claude` sections. Sensitive fields (API keys) are replaced with `api_key_configured: bool`.

**Errors**: `503` if config not loaded.

### `/ops-board` — `GET`

Returns consolidated ops-board data including artifact status, summary, and service health.

| Section | Description |
|---------|-------------|
| `artifact_status` | Status of `demo_manifest`, `telemetry`, `market_report`, `sodex_preflight`, `wave_status` — each with `status`, `path`, `mtime`, `age_seconds`, `freshness` |
| `summary` | High-level buildathon status: `demo_manifest`, `telemetry_report`, `market_report`, `sodex_preflight`, `wave_status` |
| `service_health` | Dashboard, database, SoDEX API, SoSoValue API status |

Artifacts are loaded from `runs/` directory relative to the configured `artifact_dir`. Freshness is classified as `fresh` (≤15 min), `stale` (≤24h), or `expired`.

**Errors**: `503` if config not loaded.

### `/evidence-graph` — `GET`

Returns a graph of evidence nodes and edges derived from the latest `*.summary.json` in `artifact_dir/evidence/`.

| Field | Type | Description |
|-------|------|-------------|
| `nodes` | `list[dict]` | Each node has `id`, `label`, `kind` (`source` or `entity`), `count` |
| `edges` | `list[dict]` | Each edge has `source`, `target`, `label` (relation), `confidence`, `warning`, `day_gap` |

Returns empty lists with a note if no evidence data is available.

### `/skill-report` — `GET`

Returns per-skill metrics aggregated from experiment tool traces (planner, writer, reflector stages).

| Field | Type | Description |
|-------|------|-------------|
| `skills` | `list[dict]` | Per-skill: `skill_name`, `usage_count`, `average_latency_ms`, `total_input_tokens`, `total_output_tokens`, `error_count`, `stages`, `classification` |
| `total_skills` | `int` | Number of unique skills |
| `total_invocations` | `int` | Sum of all usage counts |

Classifications: `HIGH_VALUE` (probe/suggest tools), `MEDIUM_VALUE` (search/workspace tools), `LOW_VALUE` (think tool), `NOISY` (>8 invocations with low/medium value).

### `/risk` — `GET`

Returns portfolio risk metrics computed from `.npy` paper trading sessions in `live/paper_sessions/`.

| Field | Type | Description |
|-------|------|-------------|
| `composite_score` | `float \| null` | Weighted composite risk score |
| `max_drawdown` | `float \| null` | Maximum drawdown from first equity curve |
| `current_drawdown` | `float \| null` | Current drawdown level |
| `recovery_periods` | `int \| null` | Recovery time in periods |
| `sharpe_ratio` | `float` | Annualized Sharpe ratio (sqrt(365)) |
| `correlation_matrix` | `list[list[float]] \| null` | Cross-strategy correlation matrix |
| `strategy_count` | `int` | Number of strategies with equity data |
| `strategy_names` | `list[string]` | Names derived from `.npy` filenames |
| `sub_scores` | `dict` | Normalized sub-scores: `sharpe`, `drawdown`, `concentration`, `correlation_risk` |
| `drawdown_history` | `list[float]` | Downsampled (≤60 points) drawdown series for sparkline |
| `alerts` | `list[dict]` | Last 20 drawdown events with `severity`, `value`, `message` |

Returns null/empty values when no data is available.

### `/market/symbols` — `GET`

Returns all tradable SoDEX perpetual symbols.

| Field | Type | Description |
|-------|------|-------------|
| `symbols` | `list` | Symbol list from SoDEXFeeds |
| `count` | `int` | Number of symbols |

Returns empty list if `SoDEXFeeds` is not available or fetch fails.

### `/market/tickers` — `GET`

Returns 24-hour ticker data for all SoDEX perp symbols.

| Field | Type | Description |
|-------|------|-------------|
| `tickers` | `list` | Ticker objects |
| `count` | `int` | Number of tickers |

### `/market/klines/{symbol}` — `GET`

Returns kline/candlestick data for a perp symbol.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `symbol` | `path` | *required* | Trading pair symbol |
| `interval` | `query` | `"1h"` | Candle interval |
| `limit` | `query` | `60` | Number of candles |

| Field | Type | Description |
|-------|------|-------------|
| `klines` | `list[dict]` | Candle records with `timestamp` (ISO string), OHLCV fields |
| `symbol` | `string` | Echoed symbol |
| `interval` | `string` | Echoed interval |
| `count` | `int` | Number of klines returned |

### `/market/orderbook/{symbol}` — `GET`

Returns order book depth for a perp symbol.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `symbol` | `path` | *required* | Trading pair symbol |
| `limit` | `query` | `20` | Depth levels per side |

| Field | Type | Description |
|-------|------|-------------|
| `bids` | `list` | Bid levels |
| `asks` | `list` | Ask levels |
| `symbol` | `string` | Echoed symbol |

---

## WebSocket

### Connection Lifecycle

The WebSocket endpoint is at `/ws`. On connect:

1. Client is registered with `WebSocketManager`.
2. A `{"type": "connected", ...}` welcome message is sent.
3. The server waits for JSON messages with a 30-second receive timeout.
4. On timeout, a keepalive ping is sent automatically.
5. On disconnect or error, the client is unregistered and all subscriptions/tasks are cleaned up.

### Message Protocol

All messages are JSON objects. The `action` (or `type`) field determines behavior.

#### Client → Server

| Action | Required Fields | Description |
|--------|----------------|-------------|
| `ping` | — | Server responds with `pong` |
| `subscribe` | `symbol` (optional for `risk_score`), `subscription_type` | Subscribe to data stream. Types: `klines`, `ticks`/`ticker`, `positions`, `risk_score` |
| `unsubscribe` | `symbol` (omit to unsubscribe all) | Unsubscribe from symbol(s) |
| `get_positions` | — | Request current paper trading positions snapshot |
| `get_risk` | — | Request current risk metrics snapshot |

#### Server → Client

| Type | Fields | Description |
|------|--------|-------------|
| `subscribed` | `symbol`, `subscription_type`, `message` | Confirmation of subscription |
| `unsubscribed` | `symbol` | Confirmation of unsubscription |
| `klines` | `symbol`, `data` (OHLCV array), `interval` | Kline snapshot (initial and updates) |
| `ticker` | `symbol`, `bid`, `ask`, `last_price`, `timestamp` | Ticker snapshot |
| `positions` | `positions` (array of position objects) | Paper trading positions |
| `risk_score` | `composite_score`, `max_drawdown`, `correlation_matrix`, `strategy_count`, `sharpe_ratio`, `timestamp` | Risk metrics |
| `ping` | `timestamp` | Server keepalive (on 30s timeout) |
| `pong` | `timestamp` | Response to client ping |
| `error` | `message` | Error description |

### Risk Score Periodic Push

When a client subscribes with `subscription_type: "risk_score"`:

1. An immediate risk score snapshot is sent.
2. A background `asyncio.Task` is created that pushes risk scores **every 15 seconds**.
3. The task is cancelled when the client disconnects.

---

## Risk Computation

### `_compute_risk_metrics(state)`

Loads `.npy` session files from `config.live_dir / "paper_sessions"` and computes:

1. **Equity curve extraction**: Each `.npy` file is loaded with `allow_pickle=True`. If the array has a structured `equity` field, it's used directly; otherwise float arrays are treated as equity curves.
2. **Max drawdown**: Computed via `siglab.risk.guardian.max_drawdown()` on the first equity curve.
3. **Current drawdown**: Via `current_drawdown()`.
4. **Recovery time**: Via `recovery_time()`.
5. **Drawdown history**: A sparkline series downsampled to ≤60 points using peak-accumulated drawdown formula.
6. **Sharpe ratio**: `mean(returns) / std(returns) * sqrt(365)` from `np.diff(eq) / eq[:-1]`.
7. **Correlation matrix**: Via `correlation_matrix(returns_list)` when ≥2 strategies exist.
8. **Sub-scores**: Normalized scores for `sharpe`, `drawdown`, `concentration`, `correlation_risk` using dedicated normalize functions from `siglab.risk.guardian`.
9. **Composite score**: Via `compute_composite_score(sharpe, drawdown, concentration, correlation_risk)`.
10. **Alerts**: `track_drawdown_events()` on the first equity curve, severity classified as `warning` (<15%) or `critical` (≥15%).

**Dependencies**: `numpy`, `siglab.risk.guardian` (guardian module provides the computation functions).

---

## Error Handling

| Scenario | Response |
|----------|----------|
| Config not loaded (startup failure) | `503 HTTPException` for `/config` and `/ops-board`; graceful fallback for `/risk` and market endpoints |
| SoDEXFeeds unavailable (import failure) | Empty data lists with `"note": "SoDEXFeeds not available"` |
| Market data fetch failure | Caught per-endpoint; returns `"error": str(exc)` with empty data |
| No paper session `.npy` files | `null`/empty values for all risk fields |
| numpy not installed | `{"note": "numpy not available"}` appended to empty risk response |
| Evidence summary missing/empty | Empty `nodes`/`edges` with `"note": "No evidence data available"` |
| LineageStore unavailable | Empty skill report |
| WebSocket invalid JSON | `{"type": "error", "message": "Invalid JSON payload"}` |
| WebSocket unknown action | `{"type": "error", "message": "Unknown action: ..."}` |
| Path traversal in artifact loading | Returns `{"status": "blocked", "error": "artifact path escapes repo root"}` |

---

## Configuration

| Setting | Value | Source |
|---------|-------|--------|
| Port | **3100** (FastAPI), **8765** (legacy) | CLI default, `--port` flag |
| Bind address | `0.0.0.0` (FastAPI), `127.0.0.1` (legacy) | CLI default, `--host` flag |
| CORS | `allow_origins=["*"]`, all methods, all headers | Hardcoded in `app.py` |
| Startup | Loads `SiglabConfig` via `load_settings()`, opens `LineageStore` | `lifespan()` context manager |
| Shutdown | Implicit — `yield` exits, resources cleaned up by GC | `lifespan()` |

---

## How to Start

### FastAPI server (recommended)

```bash
# Via CLI
python3 -m siglab.cli dashboard-start

# With options
python3 -m siglab.cli dashboard-start --host 0.0.0.0 --port 3100

# Direct uvicorn
uvicorn siglab.dashboard.app:app --host 0.0.0.0 --port 3100

# Development mode with auto-reload
python3 -m siglab.cli dashboard-start --reload
```

### Legacy HTTP server

```bash
python3 -m siglab.cli dashboard
python3 -m siglab.cli dashboard --port 8765
```

### Stopping

```bash
python3 -m siglab.cli dashboard-stop --port 3100
```

---

## Testing

```bash
# Dashboard risk integration tests
python3 -m pytest -q tests/test_dashboard_risk_integration.py

# Dashboard runs/experiments tests
python3 -m pytest -q tests/test_dashboard_runs.py

# E2E integration (includes dashboard)
python3 -m pytest -q tests/test_e2e_integration.py
```
