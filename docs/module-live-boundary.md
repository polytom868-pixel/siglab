# Live Trading Boundary

> The wall between paper simulation and real SoDEX execution.

## Purpose

The live boundary is the architectural layer that separates **paper trading** (simulated fills on real market data) from **real signed execution** (submitting orders to the SoDEX exchange). Its purpose is to:

1. Let operators validate strategies against real market conditions without risking capital.
2. Gate promotion to live execution behind composite performance scores and safety checks.
3. Ensure every live write path is explicitly configured, signed, and preflight-validated before any order reaches the exchange.

This module is the concrete implementation of the AGENTS.md rule: *SoDEX signed writes must refuse unless account ID, API key name, nonce store, and signer material are configured.*

---

## Architecture

The `siglab/live/` module contains the following components:

```
siglab/live/
├── __init__.py            # Public re-exports for the live boundary
├── paper_client.py        # Paper trading engine (SoDEXPaperPerpsClient)
├── runtime.py             # Live strategy runtime + execution adapter
├── sodex_signing.py       # EIP-712 signing, nonce management, request builders
├── sodex_client.py        # SoDEX public + signed perps HTTP clients
├── sodex_ws.py            # SoDEX WebSocket client (public book ticker stream)
├── sodex_rate_limit.py    # Per-IP weight scheduler for SoDEX REST
├── promotion.py           # Paper-to-live promotion scoring engine
├── reconciliation.py      # Backtest vs paper PnL comparison
├── exporter.py            # Live deployment export (strategy packages)
└── deployed_agents/       # Generated strategy packages land here
```

**Data flow:**

```
SoSoValue evidence → Spec → Backtest → Paper session (real klines)
                                            ↓
                                    Promotion scoring
                                            ↓
                                    Export (dry-run package)
                                            ↓
                                    Live runtime (requires real client)
                                            ↓
                                    SoDEX signed REST (testnet → mainnet)
```

---

## SoDEX Integration

### Public REST (data) — Implemented

Public endpoints require no authentication and are fully live-validated:

| Endpoint | Client | Status |
|---|---|---|
| Klines (candlesticks) | `SoDEXPublicPerpsClient.klines()` | Live-probed |
| Symbols | `SoDEXPublicPerpsClient.symbols()` | Live-probed |
| Tickers (24h stats) | `SoDEXPublicPerpsClient.tickers()` | Live-probed |
| Mark prices / funding rates | `SoDEXPublicPerpsClient.mark_prices()` | Live-probed |
| Book tickers (best bid/ask) | `SoDEXPublicPerpsClient.book_tickers()` | Live-probed |
| Order book depth | `SoDEXPublicPerpsClient.orderbook()` | Live-probed |
| Recent trades | `SoDEXPublicPerpsClient.trades()` | Live-probed |

The `SoDEXFeeds` class (in `siglab/data/sodex_feeds.py`) wraps `SoDEXPublicPerpsClient` with `ParquetLake` caching and DataFrame conversion.

### Public WebSocket — Implemented

`SoDEXWebSocketClient` connects to the public `allBookTicker` stream. No authentication required. No daemon/supervisor yet.

### Private/Signed (execution) — Dry-run only

Signed write actions are structurally implemented but blocked at runtime:

| Action | Signed body builder | Runtime status |
|---|---|---|
| `newOrder` | `perps_new_order_body()` | Dry-run only |
| `cancelOrder` | `perps_cancel_order_body()` | Dry-run only |
| `scheduleCancel` | `perps_schedule_cancel_body()` | Dry-run only |
| `updateLeverage` | `perps_update_leverage_body()` | Dry-run only |
| `updateMargin` | `perps_update_margin_body()` | Dry-run only |

**Explicitly blocked** (in `UNSUPPORTED_SODEX_SIGNED_ACTIONS`):
- `replaceOrder` — blocked until official SDK pins the perps wrapper type
- `modifyOrder` — blocked until official SDK pins the perps wrapper type
- `transferAsset` — blocked until full transfer schema is pinned

### Private WebSocket (account stream) — Preflight only

Params are preflight-validated, but no live account stream connection exists.

### ValueChain — Read-only preflight

Chain-id preflight is read-only readiness, not execution.

---

## Paper Client

**Class:** `SoDEXPaperPerpsClient`

A paper trading simulator that uses **real SoDEX market data** (klines, funding rates) to simulate order execution without submitting live trades.

### Session model

- Sessions are identified by a 12-character hex ID.
- Session state (orders, positions, PnL) is persisted as `.npy` files in a configurable `sessions_dir`.
- State survives process restarts via disk serialization.
- Multiple sessions can run concurrently.

### Order types

| Type | Description |
|---|---|
| `LIMIT` | Fills when kline crosses limit price |
| `MARKET` | Fills at kline close price |

### Order lifecycle

```
OPEN → FILLED (when kline crosses limit) / CANCELLED / EXPIRED
```

### Time-in-force

| TIF | Expiry |
|---|---|
| `GTC` | 72 hours default |
| `IOC` | 1 minute |
| `FOK` | 10 seconds |
| `GTX` | 72 hours (post-only) |

### Key methods

| Method | Purpose |
|---|---|
| `create_session(name)` | Create a new paper trading session |
| `place_order(session_id, ...)` | Place a paper order (validated, no exchange call) |
| `cancel_order(session_id, order_id)` | Cancel an open order |
| `process_klines(session_id, klines)` | Match open orders against new kline data |
| `process_funding(session_id)` | Apply funding costs using real SoDEX funding rates (8h intervals) |
| `get_positions(session_id)` | Get current positions |
| `get_pnl(session_id)` | Get PnL summary (realized, unrealized, funding) |
| `get_session_status(session_id)` | Full status payload (VAL-CLI-016 canonical format) |
| `get_orders(session_id, ...)` | Get orders with optional symbol/status filters |

### Fill mechanics

- **BUY LIMIT:** fills when kline `low <= limit_price`, at `min(limit_price, max(open, low))`
- **SELL LIMIT:** fills when kline `high >= limit_price`, at `max(limit_price, min(open, high))`
- **MARKET:** always fills at kline close price
- Position entry price is averaged when increasing, PnL is realized when reducing

### Funding simulation

Funding is applied every 8 hours using real SoDEX mark prices and funding rates. Long positions pay when funding rate is positive; shorts receive.

---

## Runtime

**Class:** `DirectionalPerpsSigLabStrategy`

The live strategy runtime that connects paper-validated specs to real execution.

### Strategy lifecycle

1. **`setup()`** — Loads `live_spec.json`, compiles the spec, initializes the `SoDEXExecutionAdapter`. If `dry_run` is `False`, validates all live dependencies.
2. **`update()`** — Computes target weights from the latest compiled spec, builds a trade plan (delta between target and current positions), and either dry-run logs or executes via the adapter.
3. **`withdraw()`** — Closes all open perp positions.
4. **`exit()`** — Alias for `withdraw()`.

### SoDEXExecutionAdapter

A thin adapter over a real SoDEX client that provides:
- `place_market_order()` — submit a signed market order
- `update_leverage()` — set leverage for an asset
- `get_user_state()` — fetch account state (positions, margin)
- `all_mids()` — fetch all mid prices
- `get_valid_order_size()` — validate order size against exchange rules
- `dependency_report()` — preflight readiness check

### Trade plan

The runtime computes a trade plan by:
1. Fetching latest target weights from the compiled spec
2. Fetching current positions from SoDEX user state
3. Computing delta quantities per symbol
4. Filtering out trades below `min_trade_usd` (default: $25)

### Execution guard

The runtime **refuses to execute** unless:
- A real SoDEX client is provided in config
- All required methods are present on the client
- All signing prerequisites are configured

---

## SoDEX Signing

**Module:** `sodex_signing.py`

### API key management

Signed requests require:
- **API key name** (`X-API-Key` header)
- **Account ID** (unsigned integer, validated)
- **Signer** (produces EIP-712 signatures)
- **Nonce store** (prevents replay attacks)

### Request signing flow

1. Build an `OrderedDict` action payload (type + params, Go struct field order preserved)
2. Canonical JSON serialization (no whitespace, no nulls, no floats — DecimalStrings only)
3. Keccak-256 hash of canonical JSON → `payloadHash`
4. Build EIP-712 typed data (`ExchangeAction` struct with `payloadHash` + `nonce`)
5. Sign with EIP-712 (`encode_typed_data` → `Account.sign_message`)
6. Prefix signature with `0x01` (EIP-712 indicator)
7. Attach headers: `X-API-Key`, `X-API-Sign`, `X-API-Nonce`

### EIP-712 domain

| Environment | Chain ID |
|---|---|
| `mainnet` | 286623 |
| `testnet` | 138565 |

### Nonce manager (`SoDEXNonceManager`)

- Generates monotonically increasing nonces per API key
- Validates nonces are within a 2-day past / 1-day future window
- Rejects duplicate nonces
- Persists to a JSON file for cross-restart safety
- High-water mark tracking (max 64 nonces per key)

### Signer implementations

| Signer | Type | Behavior |
|---|---|---|
| `SoDEXDryRunSigner` | `dry-run` | Raises `SoDEXNotReadyError` on any sign attempt |
| `SoDEXPrivateKeySigner` | `evm-private-key` | Signs with an EVM private key (requires `eth_account`) |

### Supported vs blocked actions

**Supported:** `newOrder`, `cancelOrder`, `scheduleCancel`, `updateLeverage`, `updateMargin`

**Blocked:** `replaceOrder`, `modifyOrder`, `transferAsset` (pending official SDK pinning)

---

## Promotion

**Module:** `promotion.py`

Promotion determines whether a paper trading session has performed well enough to graduate to live trading.

### Composite score

A weighted average of four normalised sub-scores:

| Sub-score | Weight | Normalisation |
|---|---|---|
| PnL (total return) | 0.25 | 0% → 0, 30% annual → 1.0 |
| Sharpe ratio | 0.25 | 0 → 0, ≥ 3.0 → 1.0 |
| Win rate | 0.25 | Natural [0, 1] |
| Max drawdown | 0.25 | 0% → 1.0, ≤ -30% → 0.0 |

### Promotion gates

A session is promotion-eligible when **all** of the following are true:

1. **Minimum trading days:** ≥ 10 days (default)
2. **Consecutive days above threshold:** ≥ 5 consecutive days with composite score ≥ 0.65 (default)

These gates correspond to validation ID `VAL-PAPER-012`.

### Key functions

| Function | Purpose |
|---|---|
| `compute_sub_scores(metrics)` | Normalise raw metrics to [0, 1] sub-scores |
| `compute_composite_score(metrics, weights)` | Weighted composite from raw metrics |
| `promotion_eligible(daily_metrics, ...)` | Check promotion eligibility with gate logic |
| `extract_session_metrics(client, session_id)` | Extract aggregate metrics from a paper session |
| `extract_daily_metrics(client, session_id)` | Extract per-day metrics from a paper session |

---

## Reconciliation

**Module:** `reconciliation.py`

The `ReconciliationEngine` compares backtest PnL with paper PnL over overlapping time windows.

### Metrics produced

| Metric | Description |
|---|---|
| `correlation` | Pearson correlation of overlapping returns |
| `tracking_error` | Std dev of (backtest returns − paper returns) |
| `bias` | Mean of (backtest returns − paper returns) |
| `divergence_warning` | `True` when tracking error > threshold (default: 5%) |

### Usage

```python
engine = ReconciliationEngine(divergence_threshold=0.05)
result = engine.compare(backtest_pnl_series, paper_pnl_series)
if result["divergence_warning"]:
    # Paper execution diverging from backtest
```

The engine aligns on common time index points and requires at least 2 overlapping periods.

---

## Export

**Module:** `exporter.py`

### LiveDeploymentManager

Handles the export of validated experiments as deployable strategy packages.

#### Deployment readiness checks (`deployment_readiness`)

An experiment must pass **all** of these to be exportable:

- Track is `trend_signals` AND family is one of: `perp_multi_asset_decision`, `perp_pair_trade_unlevered`, `perp_pair_trade_levered`
- Has a strict holdout split
- Has retained holdout metrics
- Did not liquidate in backtest
- Has canonical retained series runs

#### What gets generated

For each qualifying experiment, the exporter creates:

```
live/deployed_agents/siglab_{family}_{spec_hash}/
├── __init__.py
├── strategy.py          # Subclass of DirectionalPerpsSigLabStrategy
├── live_spec.json       # Full spec + runtime config
├── manifest.yaml        # Permissions + adapter declarations
└── README.md            # Operator-facing notes
```

#### Runtime config defaults

| Setting | Default |
|---|---|
| `dry_run` | `True` |
| `slippage` | 0.0035 (0.35%) |
| `min_trade_usd` | $25 |
| `live_leverage` | 1.0 |

#### Deployment boundary enforcement

The `_preflight_deploy_boundary` method **hard-refuses**:
- If the SoDEX runtime config file does not exist
- If `dry_run` is `False` (this build only supports dry-run package export)
- If `schedule` is `True` (requires a configured runner client that does not exist yet)
- If `interval_seconds` is `None` or non-positive when `schedule` is `True`

---

## Security

### What prevents accidental live execution

1. **Dry-run default:** Every `live_spec.json` ships with `dry_run: true`. The runtime checks this before executing.
2. **Missing client guard:** `SoDEXExecutionAdapter._require_client()` raises `RuntimeError` if no real client is configured.
3. **Dry-run signer:** `SoDEXDryRunSigner.sign_typed_payload()` raises `SoDEXNotReadyError`, making signed requests impossible without a real signer.
4. **Preflight gate:** `DirectionalPerpsSigLabStrategy.setup()` validates all live dependencies when `dry_run` is `False`.
5. **Export boundary:** `LiveDeploymentManager._preflight_deploy_boundary()` refuses non-dry-run exports entirely.
6. **Mainnet double-confirmation:** Requires both `SODEX_TESTNET_PREFLIGHT_PASSED=true` and `SODEX_MAINNET_LIVE_WRITE_CONFIRMATION=I_UNDERSTAND_MAINNET_RISK` env vars.
7. **`sodex-preflight` CLI:** Returns `live_write_allowed: true` only when all signing prerequisites are met.
8. **OrderedDict enforcement:** Signing payloads must use `OrderedDict` to preserve Go struct field order — plain `dict` raises `SoDEXConfigError`.
9. **Float rejection in signing:** DecimalString fields must remain quoted strings; raw floats in signing payloads raise `SoDEXConfigError`.
10. **Action whitelist:** Only `SUPPORTED_SODEX_SIGNED_ACTIONS` have builder functions (`perps_new_order_body`, etc.); attempting to build an unsupported action raises `SoDEXConfigError`. `validate_action_payload()` validates **structure only** (OrderedDict, key order, non-empty type string).

### Credential hygiene

Per AGENTS.md, the following must never be committed:
- `config.json` (contains API keys)
- `.env` / `.siglab-provider.env`
- Wallet keys
- `runs/` directory
- Nonce store files

### Testnet-first protocol

See `docs/access-and-testnet-plan.md` for the full operator workflow:
1. Start with `SODEX_ENVIRONMENT=testnet`
2. Pass `sodex-preflight --json` with `live_write_allowed: true`
3. Validate signed preview payloads against canonical serializer tests
4. Only then consider mainnet with explicit risk confirmation

---

## Cross-Module Relationships

| Module A | Module B | Relationship |
|----------|----------|-------------|
| `promotion` | `risk.guardian` | Both compute composite performance metrics; promotion gates parallel risk alert thresholds |
| `runtime` | `paper_client` | Share order/position abstractions; paper sessions validate fill mechanics used in live execution |

## Testing

### Run live boundary tests

```bash
# All tests
python3 -m pytest -q

# Live module tests specifically
python3 -m pytest tests/ -q -k "paper or live or signing or promotion or reconciliation"

# CLI agent safety tests (includes live boundary safety)
python3 -m pytest tests/test_cli_agent_safety.py -q
```

### Profile check

```bash
python3 -m siglab.cli profile --strict --json
```

### SoDEX preflight

```bash
python3 -m siglab.cli sodex-preflight --json
```

### Key test areas

| Area | What to test |
|---|---|
| Paper client | Order lifecycle, fill mechanics, funding costs, session persistence |
| Signing | Canonical JSON, payload hashing, EIP-712 typed data, nonce uniqueness |
| Promotion | Sub-score normalisation, composite scoring, gate logic (min days, consecutive days) |
| Reconciliation | Correlation, tracking error, bias, divergence warnings |
| Runtime | Dependency report, trade plan generation, dry-run guard |
| Export | Deployment readiness checks, preflight boundary enforcement |
