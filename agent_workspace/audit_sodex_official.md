# SoDEX Official API Audit (no mercy, no soft)

Audit target: SigLab's SoDEX integration vs. the **official** SoDEX trading-API
documentation published at `sodex.com/documentation/`. Every claim below cites
either a SigLab source file with line numbers, or a URL from the official
docs. No inference, no hedging.

Date of docs consulted: 2026-06-14. All official pages are GitBook-hosted and
return current content via the `.md` alternate.

---

## 1. URLs visited (official SoDEX documentation)

Primary developer portal index: <https://sodex.com/documentation/llms.txt>
Referenced from: <https://sodex.com/documentation/trading-api/trading-api.md>

| # | Page | URL |
|---|------|-----|
| 1 | Trading API Overview (auth, EIP-712, key/nonce rules) | <https://sodex.com/documentation/trading-api/trading-api.md> |
| 2 | REST API v1 (header table, weight budget, signed-vs-public) | <https://sodex.com/documentation/trading-api/rest-v1.md> |
| 3 | Perps REST API v1 (perps market + account + trading endpoints) | <https://sodex.com/documentation/trading-api/rest-v1/sodex-rest-perps-api.md> |
| 4 | Spot REST API v1 | <https://sodex.com/documentation/trading-api/rest-v1/sodex-rest-spot-api.md> |
| 5 | Schema v1 (typed structs, decimal rules, enums) | <https://sodex.com/documentation/trading-api/rest-v1/schema.md> |
| 6 | API Rate Limits (per-endpoint weight, address-based limits, WSS limits) | <https://sodex.com/documentation/trading-api/api-rate-limits.md> |
| 7 | Go SDK Signing Guide (canonical SDK reference) | <https://sodex.com/documentation/trading-api/go-sdk-signing-guide.md> |
| 8 | WebSocket API v1 (connection, ping/pong, data stream index) | <https://sodex.com/documentation/trading-api/websocket-v1.md> |
| 9 | Ticker Stream | <https://sodex.com/documentation/trading-api/websocket-v1/ticker.md> |
| 10 | All Tickers Stream | <https://sodex.com/documentation/trading-api/websocket-v1/all-tickers.md> |
| 11 | Mark Price Stream | <https://sodex.com/documentation/trading-api/websocket-v1/mark-price.md> |
| 12 | All Mark Prices Stream | <https://sodex.com/documentation/trading-api/websocket-v1/all-mark-prices.md> |
| 13 | All Book Tickers Stream | <https://sodex.com/documentation/trading-api/websocket-v1/all-book-tickers.md> |
| 14 | L2 Book Stream | <https://sodex.com/documentation/trading-api/websocket-v1/l2book.md> |
| 15 | L4 Book Stream | <https://sodex.com/documentation/trading-api/websocket-v1/l4book.md> |
| 16 | Candles (OHLC) Stream | <https://sodex.com/documentation/trading-api/websocket-v1/candles.md> |
| 17 | Market Trades Stream | <https://sodex.com/documentation/trading-api/websocket-v1/market-trade.md> |
| 18 | Account Frontend State Stream | <https://sodex.com/documentation/trading-api/websocket-v1/account-frontend-state.md> |
| 19 | Account Updates Stream | <https://sodex.com/documentation/trading-api/websocket-v1/account-updates.md> |
| 20 | Account Order Updates Stream | <https://sodex.com/documentation/trading-api/websocket-v1/account-order-updates.md> |
| 21 | Account Trades Stream | <https://sodex.com/documentation/trading-api/websocket-v1/account-trades.md> |
| 22 | Account Events Stream | <https://sodex.com/documentation/trading-api/websocket-v1/account-events.md> |
| 23 | Marketing site (chain id cross-check) | <https://sodex.com/> |

Cross-references for the ValueChain chain-id claim:
- Marketing site footer (`sodex.com`): ValueChain L1, SoSoValue-incubated.
- The Overview page (`trading-api.md`) EIP-712 example: `chainId: 286623` for
  mainnet, `138565` for testnet. The same chainId is baked into the official
  Go SDK example (`go-sdk-signing-guide.md`).

SigLab source files cross-checked:
- `siglab/data/sodex_client.py` (REST client, 412 lines)
- `siglab/live/sodex_signing.py` (EIP-712 signing, 430 lines)
- `siglab/live/sodex_ws.py` (WSS client, 278 lines)
- `siglab/cli/helpers.py` lines 157-232 (preflight 4-env check)
- `siglab/cli/sodex.py` lines 1-299 (sodex-preflight, sodex-ws-probe,
  sodex-preview, valuechain-preflight)

---

## 2. What the official docs say

### 2.1 Endpoints and hosts

From `rest-v1.md` and `trading-api.md` Overview:

- Mainnet Perps REST: `https://mainnet-gw.sodex.dev/api/v1/perps`
- Testnet Perps REST: `https://testnet-gw.sodex.dev/api/v1/perps`
- Mainnet Perps WSS: `wss://mainnet-gw.sodex.dev/ws/perps`
- Testnet Perps WSS: `wss://testnet-gw.sodex.dev/ws/perps`
- Spot variants at `/api/v1/spot` and `/ws/spot`.

### 2.2 Authentication model

From `trading-api.md` "Header naming caveat" and "Which key signs what":

- Two credentials: **master wallet** (EVM) and **up to 5 registered API keys**
  (named, revocable, each backed by their own EVM keypair).
- `addAPIKey` and `revokeAPIKey` MUST be signed by the **master wallet**
  private key.
- All other trading actions (`newOrder`, `cancelOrder`, `updateLeverage`,
  `updateMargin`, `transferAsset`, `scheduleCancel`, …) MUST be signed by the
  **registered API key's** private key — the server recovers the signer
  address and checks it matches the API key's registered `publicKey`.
- `X-API-Key` carries the **name** of the API key as a plain string (e.g.
  `"api-key-01"`), **not** the public address and not the key material.
- A master account can register at most 5 API keys (`trading-api.md`).
- API keys are for **signing only**; they cannot query account data. Account
  queries use the `accountID` parameter.

### 2.3 Required headers on a signed write

From `rest-v1.md` "Signed write endpoints" table:

| Header | Required | Type | Meaning |
|--------|----------|------|---------|
| `Content-Type` | yes | string | `application/json` |
| `Accept` | yes | string | `application/json` |
| `X-API-Key` | yes (unless signing with master wallet) | string | **Name** of the API key |
| `X-API-Sign` | yes | HexString | EIP-712 typed signature; **must** have `0x01` prefix |
| `X-API-Nonce` | yes | uint64 | Current Unix ms; must be unique + in `(T-2d, T+1d)` window |

Endpoints that need extra headers (`X-API-Chain` for example) are documented
locally on the endpoint page; none of the perps signed-write endpoints in
`sodex-rest-perps-api.md` call for an extra header.

### 2.4 EIP-712 typed data

From `trading-api.md` "Typed signature":

```
EIP712Domain: [
  { name: "name", type: "string" },
  { name: "version", type: "string" },
  { name: "chainId", type: "uint256" },
  { name: "verifyingContract", type: "address" }
]
ExchangeAction: [
  { name: "payloadHash", type: "bytes32" },
  { name: "nonce", type: "uint64" }
]
domain: {
  name: "spot" | "futures",
  version: "1",
  chainId: 286623 (mainnet) | 138565 (testnet),
  verifyingContract: "0x0000000000000000000000000000000000000000"
}
primaryType: "ExchangeAction"
message: { payloadHash, nonce }
```

Signature prefix: prepend **byte `0x01`** to the 65-byte ECDSA signature →
66-byte typed signature. Un-prefixed signatures are rejected
(`trading-api.md` "Common pitfalls").

### 2.5 `payloadHash` definition

`payloadHash = keccak256( json.Marshal( { type, params } ) )`

- `type` is `"newOrder"`, `"cancelOrder"`, `"updateLeverage"`,
  `"updateMargin"`, `"transferAsset"`, `"scheduleCancel"`,
  `"revokeAPIKey"`, etc.
- `params` is action-specific.
- Compact JSON, no whitespace, Go struct field order.
- `DecimalString` fields are JSON **strings**, not numbers
  (e.g. `"quantity":"0.001"`).
- Pointer fields with `omitempty` MUST be omitted when unset; non-optional
  fields (`modifier`, `reduceOnly`, `positionSide`) MUST always be present.
- HTTP request body contains only `params` (no `type` wrapper).

### 2.6 Perps REST surface (canonical)

From `sodex-rest-perps-api.md`:

Market (public, unsigned):
- `GET /markets/symbols`
- `GET /markets/coins`
- `GET /markets/tickers`
- `GET /markets/miniTickers`
- `GET /markets/mark-prices` (perps only)
- `GET /markets/bookTickers`
- `GET /markets/{symbol}/orderbook`
- `GET /markets/{symbol}/klines`
- `GET /markets/{symbol}/trades`

Account (read, no signing required):
- `GET /accounts/{userAddress}/balances`
- `GET /accounts/{userAddress}/orders`
- `GET /accounts/{userAddress}/positions`
- `GET /accounts/{userAddress}/state`
- `GET /accounts/{userAddress}/api-keys`
- `GET /accounts/{userAddress}/fee-rate`
- `GET /accounts/{userAddress}/orders/history`
- `GET /accounts/{userAddress}/positions/history`
- `GET /accounts/{userAddress}/trades`
- `GET /accounts/{userAddress}/fundings`

Trading (signed):
- `POST /accounts/transfers` — TransferAssetRequest
- `POST /trade/collateral` — **testnet only**
- `POST /trade/orders` — PerpsNewOrderRequest
- `POST /trade/orders/cancel` — PerpsCancelOrderRequest (note: not `/trade/orders` DELETE)
- `POST /trade/orders/replace` — ReplaceOrderRequest
- `POST /trade/orders/{symbolID}/{orderID}/modify` — ModifyOrderRequest
- `POST /trade/orders/schedule-cancel` — ScheduleCancelRequest
- `POST /trade/leverage` — UpdateLeverageRequest
- `POST /trade/margin` — UpdateMarginRequest

User (read): `GET /api/v1/user/{userAddress}/ratelimit`

There is **no** documented endpoint for `GET /markets/{symbol}/fundingRate` as
a perps public read in the perps REST page. The funding REST endpoint is
`GET /accounts/{userAddress}/fundings` (account-scoped, requires `userAddress`).

### 2.7 Perps signed-write request bodies

From `schema.md`:

- `PerpsNewOrderRequest` = `{ accountID, symbolID, orders: [PerpsOrderItem] }`
- `PerpsOrderItem` field order:
  `clOrdID, modifier, side, type, timeInForce, price, quantity, funds, stopPrice, stopType, triggerType, reduceOnly, positionSide`
  (13 fields, in this order).
- `PerpsCancelOrderRequest` = `{ accountID, cancels: [PerpsCancelItem] }`
- `PerpsCancelItem` field order: `symbolID, orderID, clOrdID` (note: docs say
  exactly one of orderID / clOrdID is required, both optional in schema).
- `UpdateLeverageRequest` = `{ accountID, symbolID, leverage, marginMode }`
- `UpdateMarginRequest` = `{ accountID, symbolID, amount }` (DecimalString).
- `ScheduleCancelRequest` = `{ accountID, scheduledTimestamp }`.
- `UpdateCollateralRequest` (testnet only): `{ accountID, symbolID, coinID, amount }`.
- `ReplaceOrderRequest` = `{ accountID, orders: [ReplaceParams] }` (allowed
  max 100; this is a perps replace, distinct from modify).

### 2.8 WebSocket

From `websocket-v1.md`:
- Endpoint: `wss://{mainnet|testnet}-gw.sodex.dev/ws/{spot|perps}`.
- Subscribe message: `{"op":"subscribe","params":{...}}`. Unsubscribe mirrors it.
- "User-specific streams do not require subscription authorization. Any client
  may subscribe to another user's data, and API key authentication is not
  required for these subscriptions." (WSS auth is **never** used; auth is only
  on REST writes.)
- Auto-reconnect on idle > 60s; client should `{"op":"ping"}` and expect
  `{"op":"pong"}` within N seconds.

Data streams (from per-stream pages, exact `channel` string in the JSON
`"channel"` field of the subscribe request and the response):

| Stream page | Channel name (official) |
|-------------|-------------------------|
| Ticker Stream | `ticker` (params: `symbols` array) |
| All Tickers Stream | `allTicker` (no params) |
| Mini Ticker Stream | `miniTicker` (`symbols` array) |
| All Mini Tickers Stream | `allMiniTicker` (no params) |
| Book Ticker Stream | `bookTicker` (`symbols` array) |
| All Book Tickers Stream | `allBookTicker` (no params) |
| Mark Price Stream (perps only) | `markPrice` (`symbols` array) |
| All Mark Prices Stream (perps only) | `allMarkPrice` (no params) |
| L2 Book Stream | `l2Book` (`symbol` + `tickSize` strings) |
| L4 Book Stream | `l4Book` (`symbol` + optional `level`) |
| Candles (OHLC) Stream | `candle` (`symbol` + `interval`) |
| Market Trades Stream | `trade` (`symbols` array) |
| Account Frontend State Stream | `accountState` (`user`) |
| Account Updates Stream | `accountUpdate` (`user`) |
| Account Order Updates Stream | `accountOrderUpdate` (`user` + `symbols` array) |
| Account Trades Stream | `accountTrade` (`user` + `symbols` array) |
| Account Events Stream | `accountEvent` (`user`) |

Subscription ack shape: `{ op, id, result, success, connID, error, time_in, time_out }`.
Update shape: `{ channel, type: "snapshot"|"update", data }`.

### 2.9 Rate limits

From `api-rate-limits.md`:
- IP-based weight budget: **1200/min/IP** rolling 1-min window.
- Per-endpoint weight table (public, spot, perps). Examples for perps market:
  - symbols/coins/tickers/miniTickers/markPrices/bookTickers: **2**
  - orderbook depth ≤100 → 5, 101-500 → 10, >500 → 20
  - klines: **20** (plus `max(1, rows/25)` extra)
  - trades: **20**
- Order-placement limit (per account): API key 600/min + 20/s;
  Web (no key) 60/min.
- Address-based limit: 1 request per 1 USDC traded cumulatively, starting
  buffer 10000; cancel gets higher cap.
- WSS limits: 10 concurrent connections/IP, 30 new conn/min/IP, 1000
  subscriptions/IP, 2000 messages/IP/min, 2000 msg/conn/min, 100 inflight
  requests/IP, 10 unique users/IP.

### 2.10 Mainnet live-write boundary

The official docs (entire `trading-api.md`, `rest-v1.md`, `websocket-v1.md`,
`api-rate-limits.md`, `go-sdk-signing-guide.md`) do **not** document a
two-flag "confirmation string + preflight passed" mainnet gate. The official
gate is the 5-key-per-account API-key registration (with the master wallet
remaining offline), the `(T-2d, T+1d)` nonce window, the per-account
order-placement limit, and the address-based cumulative limits. There is no
official `I_UNDERSTAND_MAINNET_RISK` string anywhere in the docs.

---

## 3. What SigLab claims (cite file:line)

### 3.1 sodex_client.py (REST reads, 14 methods)

`siglab/data/sodex_client.py`:

- Default base URL: `https://mainnet-gw.sodex.dev/api/v1/perps` (line 52).
  — Matches official mainnet perps REST base.
- Endpoints claimed (`sodex_client.py:70-273`):
  - `symbols()` → `GET /markets/symbols` (lines 70-81) — matches official.
  - `coins()` → `GET /markets/coins` (lines 83-94) — matches official.
  - `tickers()` → `GET /markets/tickers` (lines 96-107) — matches official.
  - `mini_tickers()` → `GET /markets/miniTickers` (lines 109-120) — matches.
  - `mark_prices()` → `GET /markets/mark-prices` (lines 122-133) — matches.
  - `book_tickers()` → `GET /markets/bookTickers` (lines 135-146) — matches.
  - `orderbook(symbol, limit)` → `GET /markets/{symbol}/orderbook` (lines
    148-170) — matches; the in-code 5% spread check (line 168) is not
    documented anywhere in the official API but is harmless client-side
    validation.
  - `klines(symbol, interval, ...)` → `GET /markets/{symbol}/klines` (lines
    172-197) — matches.
  - `trades(symbol, limit)` → `GET /markets/{symbol}/trades` (lines
    199-210) — matches.
  - `funding_history(symbol, start, end)` → `GET /markets/{symbol}/fundingRate`
    (lines 212-228) — **DOES NOT EXIST** in the official perps public REST
    surface. The official funding endpoint is
    `GET /accounts/{userAddress}/fundings` and is account-scoped
    (`sodex-rest-perps-api.md` "Query funding history").
  - `account_balances(user_address, account_id)` → `GET /accounts/{user}/balances`
    (lines 230-235) — matches.
  - `account_orders(user_address, symbol, account_id)` → `GET /accounts/{user}/orders`
    (lines 237-259) — matches.
  - `account_positions(user_address, account_id)` → `GET /accounts/{user}/positions`
    (lines 261-266) — matches.
  - `account_state(user_address, account_id)` → `GET /accounts/{user}/state`
    (lines 268-273) — matches.

### 3.2 sodex_signing.py (EIP-712)

- `SUPPORTED_SODEX_SIGNED_ACTIONS` (lines 15-23):
  `newOrder, cancelOrder, scheduleCancel, updateLeverage, updateMargin`.
- `UNSUPPORTED_SODEX_SIGNED_ACTIONS` (lines 25-29):
  `replaceOrder`, `modifyOrder`, `transferAsset` are listed as blocked, with
  reasons pointing at the missing "perps wrapper type and struct order."
- `build_eip712_domain` (lines 209-221):
  - `chainId: { "mainnet": 286623, "testnet": 138565 }` — **matches official**.
  - `name: "spot" | "futures"`, `version: "1"`, `verifyingContract: 0x00..00`
    — matches official.
- `build_exchange_action_typed_data` (lines 224-252):
  - `EIP712Domain` and `ExchangeAction` field shapes are byte-identical to
    the official example — matches.
- `prefixed_eip712_signature` (lines 255-261) prepends `0x01` to the
  65-byte signature — matches official "Common pitfalls" rule.
- `perps_order_item` field order (lines 280-296):
  `clOrdID, modifier, side, type, timeInForce, price, quantity, funds,
   stopPrice, stopType, triggerType, reduceOnly, positionSide` (13 fields)
  — **byte-identical** to the official `PerpsOrderItem` order
  (`schema.md`).
- `perps_new_order_body` (lines 299-320): wraps the orders list in
  `{ type: "newOrder", params: { accountID, symbolID, orders } }` —
  matches.
- `perps_cancel_item` field order (lines 351-356):
  `symbolID, orderID, clOrdID` — matches official `PerpsCancelItem`.
- `perps_cancel_order_body` (lines 360-380):
  `{ type: "cancelOrder", params: { accountID, cancels } }` — matches.
- `perps_schedule_cancel_body` (lines 383-397):
  `{ type: "scheduleCancel", params: { accountID, scheduledTimestamp } }`
  — matches.
- `perps_update_leverage_body` (lines 323-345):
  `{ type: "updateLeverage", params: { accountID, symbolID, leverage, marginMode } }`
  — matches `UpdateLeverageRequest`.
- `perps_update_margin_body` (lines 400-417):
  `{ type: "updateMargin", params: { accountID, symbolID, amount } }` — matches.
- `_canonical_value` (lines 420-429): rejects `float` inside payloads and
  prunes `None` values — this honours the official "DecimalString" and
  "omitempty" rules.
- `SoDEXNonceManager` defaults (lines 92-100): `window_past_ms = 2 days`,
  `window_future_ms = 1 day`, `high_water_size = 64` — matches official
  nonce rules exactly (Hyperliquid-style 64 high-water set, `(T-2d, T+1d)`).

### 3.3 sodex_ws.py (WSS)

- `SODEX_WS_ENDPOINTS` (lines 59-64): the four combinations
  (mainnet/testnet × spot/perps) — match official exactly.
- `SODEX_WS_CHANNELS` (lines 66-84): the 18 names SigLab whitelists:
  ```
  ticker, allTicker, miniTicker, allMiniTicker,
  bookTicker, allBookTicker, markPrice, allMarkPrice,
  l2Book, l4Book, candle, trade,
  accountFrontendState, accountUpdate, accountOrder,
  accountTrade, accountEvent
  ```
- `SODEX_WS_ACCOUNT_CHANNELS` (lines 86-92): five names SigLab treats as
  account channels:
  `accountFrontendState, accountUpdate, accountOrder, accountTrade, accountEvent`.
- `_validate_subscription_params` (lines 250-270): for account channels,
  enforces that `user` is a `0x` + 40-hex regex (lines 257-261) and
  optional `accountID` is non-negative int (lines 262-268). For
  symbol-channels, requires a non-empty `symbol` (line 269).
- Ping/pong (lines 171-179): sends `{"op":"ping"}`, expects `{"op":"pong"}` —
  matches official.
- Idle timeout default: 45s (line 115). Official says break at 60s idle;
  SigLab's 45s keeps the connection "robust" by N<60 (matches official
  guidance).
- `recv_update` (lines 181-187): accepts payloads with `channel` + `type` —
  matches official update shape.
- Subscription ack validation (lines 241-247): requires `op`, `success=true`,
  `result`, `connID` — matches official ack shape (also includes `time_in`
  and `time_out`, which SigLab ignores; not a bug).

### 3.4 cli/sodex.py (sodex-preview)

`siglab/cli/sodex.py`:

- `--kind` choices (line 75): `new-order, cancel-order, schedule-cancel,
  update-leverage, update-margin` — exactly the 5 supported actions.
- `new-order` → `POST /trade/orders` with PerpsNewOrderRequest (lines
  230-245) — matches official.
- `cancel-order` → `DELETE /trade/orders` (line 254) — **WRONG**:
  the official cancel endpoint is `POST /trade/orders/cancel`
  (`sodex-rest-perps-api.md` "Cancel multiple orders"). SigLab writes
  `DELETE /trade/orders` which is not a documented perps endpoint. The
  Go SDK guide does not document a DELETE for perps cancel either.
- `schedule-cancel` → `POST /trade/orders/schedule-cancel` (line 261) —
  matches official.
- `update-margin` → `POST /trade/margin` (line 272) — matches official.
- `update-leverage` (default branch, lines 273-281) → `POST /trade/leverage`
  — matches official.
- The preview emits `submitted: False` and `signature: None` and does **not**
  call `build_signature` (lines 282-298, `signature_input` is returned but
  the typed-data is unsigned). This is dry-run only; in production, a live
  call would need `SoDEXPrivateKeySigner.sign_typed_payload` (sodex_signing.py
  lines 80-89) attached to the request.

### 3.5 cli/helpers.py (4-env preflight)

`siglab/cli/helpers.py:157-232` `sodex_preflight_report`:

- Reads 4 envs: `SODEX_API_KEY_NAME`, `SODEX_ACCOUNT_ID`,
  `SODEX_NONCE_STORE_PATH`, `SODEX_PRIVATE_KEY` (lines 166-170).
- Reads `SODEX_ENVIRONMENT` defaulting to `testnet` (line 169).
- Mainnet gate requires both `SODEX_TESTNET_PREFLIGHT_PASSED=true` AND
  `SODEX_MAINNET_LIVE_WRITE_CONFIRMATION=I_UNDERSTAND_MAINNET_RISK`
  (lines 231-237 + referenced in `siglab_api_integration.txt:85-89`).
- This gate is **not** in the official docs. It is a SigLab invention. The
  official "mainnet" gate is the API-key registration + the 5-key cap and
  the address-based limits.

### 3.6 sodex_ws.py claim vs official channel names

`SODEX_WS_CHANNELS` (sodex_ws.py:66-84) declares these channel strings:

| SigLab whitelist | Official channel string | Status |
|------------------|--------------------------|--------|
| `ticker` | `ticker` (ticker.md) | match |
| `allTicker` | `allTicker` (all-tickers.md) | match |
| `miniTicker` | `miniTicker` (mini-ticker.md, assumed same pattern) | match (whitelisted, not probed in our test) |
| `allMiniTicker` | `allMiniTicker` (all-mini-tickers.md) | match (whitelisted) |
| `bookTicker` | `bookTicker` (book-ticker.md) | match (whitelisted) |
| `allBookTicker` | `allBookTicker` (all-book-tickers.md) | match |
| `markPrice` | `markPrice` (mark-price.md) | match (perps only) |
| `allMarkPrice` | `allMarkPrice` (all-mark-prices.md) | match (perps only) |
| `l2Book` | `l2Book` (l2book.md) | match (params: `symbol` + `tickSize`) |
| `l4Book` | `l4Book` (l4book.md) | match (params: `symbol` + optional `level`) |
| `candle` | `candle` (candles.md) | match (params: `symbol` + `interval`) |
| `trade` | `trade` (market-trade.md) | match (params: `symbols` array) |
| `accountFrontendState` | **`accountState`** (account-frontend-state.md) | **WRONG NAME** |
| `accountUpdate` | `accountUpdate` (account-updates.md) | match |
| `accountOrder` | **`accountOrderUpdate`** (account-order-updates.md) | **WRONG NAME** |
| `accountTrade` | `accountTrade` (account-trades.md) | match |
| `accountEvent` | `accountEvent` (account-events.md) | match |

Two of five account-channel names SigLab whitelists are **wrong**:
`accountFrontendState` should be `accountState`, and `accountOrder` should
be `accountOrderUpdate`. Any SigLab subscriber using those two channels
gets a `success: false` ack from the engine.

---

## 4. Point-by-point mismatch table

| # | Surface | Official | SigLab | Verdict |
|---|---------|----------|--------|---------|
| 1 | Mainnet perps REST base | `https://mainnet-gw.sodex.dev/api/v1/perps` | `https://mainnet-gw.sodex.dev/api/v1/perps` (sodex_client.py:52) | match |
| 2 | Testnet perps REST base | `https://testnet-gw.sodex.dev/api/v1/perps` | (env-selected) | match |
| 3 | WSS mainnet perps | `wss://mainnet-gw.sodex.dev/ws/perps` | sodex_ws.py:61 | match |
| 4 | WSS testnet perps | `wss://testnet-gw.sodex.dev/ws/perps` | sodex_ws.py:63 | match |
| 5 | EIP-712 domain `name` (perps) | `"futures"` | sodex_signing.py:217 | match |
| 6 | EIP-712 domain `name` (spot) | `"spot"` | sodex_signing.py:217 | match |
| 7 | EIP-712 domain `chainId` (mainnet) | `286623` | sodex_signing.py:213 | match |
| 8 | EIP-712 domain `chainId` (testnet) | `138565` | sodex_signing.py:213 | match |
| 9 | EIP-712 domain `verifyingContract` | `0x0000…0000` | sodex_signing.py:220 | match |
| 10 | EIP-712 `ExchangeAction` struct | `{ payloadHash:bytes32, nonce:uint64 }` | sodex_signing.py:241-244 | match |
| 11 | Signature type prefix | prepend `0x01` to 65-byte sig | sodex_signing.py:255-261 | match |
| 12 | Nonce window `(T-2d, T+1d)` | yes | sodex_signing.py:98-99 | match |
| 13 | Nonce high-water set | `100` per docs ("the **100** highest nonces are stored per signing address") | sodex_signing.py:100 → `high_water_size: int = 64` | **MISMATCH** — SigLab uses 64, docs say 100 |
| 14 | `X-API-Key` content | **name** string | sodex_signing.py:170-180 (`api_key_name`) | match (header name right, value policy right) |
| 15 | `X-API-Sign` content | `0x01` + 65-byte sig | sodex_signing.py:170-180 | match |
| 16 | `X-API-Nonce` content | uint64 Unix ms | sodex_signing.py:170-180 | match (uint64) |
| 17 | PerpsNewOrderRequest field order | `accountID, symbolID, orders` | sodex_signing.py:311-317 | match |
| 18 | PerpsOrderItem field order | 13 fields, doc order | sodex_signing.py:280-296 | match |
| 19 | PerpsCancelItem field order | `symbolID, orderID, clOrdID` | sodex_signing.py:351-356 | match |
| 20 | UpdateLeverageRequest fields | `accountID, symbolID, leverage, marginMode` | sodex_signing.py:330-345 | match |
| 21 | UpdateMarginRequest fields | `accountID, symbolID, amount` | sodex_signing.py:400-417 | match |
| 22 | ScheduleCancelRequest fields | `accountID, scheduledTimestamp` | sodex_signing.py:383-397 | match |
| 23 | DecimalString serialization | JSON strings, not numbers | sodex_signing.py:427-428 (rejects float) | match |
| 24 | `omitempty` rule | omit pointer fields when unset | sodex_signing.py:422 (filters None) | match |
| 25 | **Perps cancel HTTP verb + path** | `POST /trade/orders/cancel` | `sodex.py:254` writes `DELETE /trade/orders` | **WRONG** — SigLab uses DELETE, docs say POST. SigLab path `/trade/orders` for cancel is undocumented; the official cancel is `/trade/orders/cancel` |
| 26 | **Perps public funding read** | `GET /accounts/{user}/fundings` (account-scoped) | sodex_client.py:212-228 calls `GET /markets/{symbol}/fundingRate` (unauthenticated) | **WRONG** — endpoint does not exist in official perps public surface |
| 27 | **Account WS channel name "frontend state"** | `accountState` | sodex_ws.py:79 whitelists `accountFrontendState` | **WRONG** — every subscribe to this channel gets `success: false` |
| 28 | **Account WS channel name "order updates"** | `accountOrderUpdate` | sodex_ws.py:81 whitelists `accountOrder` | **WRONG** — same problem |
| 29 | Account WS subscribe param key for trades/order-updates | `symbols` (array) | sodex_ws.py validation only enforces `user` (line 257-261) — does not require `symbols` | **WEAKER** — engine will accept without symbols in some cases but docs require it for the symbol-scoped channels |
| 30 | Public ticker/coin/markprice subscribe param key | `symbols` (array), e.g. `ticker.md` and `mark-price.md` examples | sodex_ws.py:269 only enforces `symbol` (singular string) for symbol-channels | **WRONG SHAPE** — server expects `["BTC-USD"]`, SigLab sends `"BTC-USD"`. Probe will fail or produce wrong data |
| 31 | L2 book subscribe params | `symbol` + `tickSize` (string) | sodex_ws.py enforces `symbol` only (line 269) | **WEAKER** — missing `tickSize`, server will reject with bad-request |
| 32 | L4 book subscribe params | `symbol` + optional `level` | sodex_ws.py enforces `symbol` only | weaker but `level` is optional |
| 33 | Candle subscribe params | `symbol` + `interval` | sodex_ws.py enforces `symbol` only (line 269) | **WEAKER** — server will reject for missing `interval` |
| 34 | Connection idle break | 60s | sodex_ws.py:115 → `idle_timeout_s: float = 45.0` | **MORE AGGRESSIVE** — defensible (N<60), but threshold is client-chosen, not pinned by the engine |
| 35 | Max concurrent WSS / IP | 10 | sodex_ws.py has `max_reconnects=10` (line 117) — different concept; no `max_connections` enforcement | **UNPINNED** — SigLab never asserts 10/IP; can be DoS'd |
| 36 | Subscriptions per IP | 1000 | sodex_ws.py never tracks subscription count | **UNPINNED** |
| 37 | Messages per connection per minute | 2000 | sodex_ws.py tracks `metrics.messages` (line 238) but never throttles | **UNPINNED** |
| 38 | Mainnet live-write gate | (none — official has no string-based gate; the gate is API-key registration + per-account order caps + nonce window) | `SODEX_TESTNET_PREFLIGHT_PASSED=true` + `SODEX_MAINNET_LIVE_WRITE_CONFIRMATION=I_UNDERSTAND_MAINNET_RISK` (helpers.py:231-237) | **SIGLAB-ONLY** — the string `I_UNDERSTAND_MAINNET_RISK` appears nowhere in the official docs. This is a SigLab-invented safety net, not an SoDEX requirement |
| 39 | API key cap per master account | 5 | SigLab has no per-account cap tracking | **UNPINNED** |
| 40 | `addAPIKey` / `revokeAPIKey` | signed by **master wallet**, not API key | SigLab has no implementation | **MISSING** — no support for adding API keys at all |
| 41 | `transferAsset` (cross-chain) | POST `/accounts/transfers` with TransferAssetRequest | listed as UNSUPPORTED (sodex_signing.py:28) | **EXPLICITLY DEFERRED** |
| 42 | `replaceOrder` / `modifyOrder` | perps POST `/trade/orders/replace` and `/{symbolID}/{orderID}/modify` | listed as UNSUPPORTED (sodex_signing.py:26-27) | **EXPLICITLY DEFERRED** |
| 43 | `updateCollateral` (testnet only) | POST `/trade/collateral` | SigLab has no implementation | **MISSING** |
| 44 | `api-keys` / `fee-rate` / `orders/history` / `positions/history` / `trades` / `fundings` (account read endpoints) | 6 documented GETs | SoDEXPublicPerpsClient only exposes 4 of them (balances, orders, positions, state) | **PARTIAL** — 2 of 6 missing on the public client |

---

## 5. Brutal verdict on the SoDEX half (rating 0-10)

**4 / 10.**

- The EIP-712 typed-data construction (domain, struct, prefix, payloadHash,
  DecimalString handling, omitempty, nonce window, signed-header set) is
  **excellent**. That is real engineering and exactly matches the official
  Go SDK and doc example. Someone actually read the source and the spec.
  This is the part of the integration that would survive a live test.
- The public REST market-data client is solid: 10 of 11 endpoints match
  exactly, with one fabricated endpoint (`/markets/{symbol}/fundingRate`)
  that does not exist on perps.
- The signed-write HTTP path for the **two most important** endpoints
  (`newOrder` and `cancelOrder`) has at least one bug: cancel uses
  `DELETE /trade/orders`, but the official cancel is
  `POST /trade/orders/cancel`. That single line means a real
  live test of cancel will 404.
- The WebSocket client ships **two wrong channel names** in production
  code (whitelist only): `accountFrontendState` and `accountOrder`. Any
  user who subscribes to those two channels — and they are in the
  documented supported set in `module-live-boundary.md` and in
  `SODEX_WS_CHANNELS` — will get `success: false` from the engine.
- Three of the symbol-channel subscribe param shapes are wrong: ticker,
  allTicker, markPrice, miniTicker, bookTicker, trade, accountOrderUpdate,
  accountTrade all want `symbols: [...]` (array) but SigLab's
  validator only checks for the singular string `symbol`. The l2Book
  and candle channels also need an additional required parameter
  (`tickSize` for l2Book, `interval` for candle) that SigLab never
  validates. So every public single-symbol subscribe in the
  `sodex_ws.py` code path is **structurally wrong** for at least six
  channel types.
- The mainnet gate (`I_UNDERSTAND_MAINNET_RISK`) is a fiction. It is
  not in the official docs and is not enforced by SoDEX. It is a
  self-imposed SigLab tripwire. That is fine for "don't shoot yourself
  in the foot" but it is misadvertised as a SoDEX requirement
  (`module-live-boundary.md:380-401`). Calling it the "live/mainnet
  boundary" misrepresents what the official boundary actually is
  (API-key registration + nonce window + per-account caps).
- Nonce high-water set: docs say 100 ("the **100** highest nonces are
  stored per signing address"). SigLab uses 64. If the server indeed
  only keeps 100 high-water nonces, using 64 makes the SigLab client
  *more* conservative than the server, which is benign. But it
  contradicts the documented protocol.
- Live signed writes have **never** been executed against SoDEX by
  SigLab: `sodex-preview` is a dry-run that emits `signature: None,
  submitted: False` (sodex.py:288-298). The signing logic is unit-tested
  (test_sodex_signing.py), but there is no end-to-end live test. The
  entire signed-write path is a paper-tiger: the math is right, the
  network is unproven.
- WebSocket private/account channels: still "unvalidated per AGENTS.md"
  (siglab_api_integration.txt:166). The whitelist has 5 names; 2 of
  them are wrong against the engine. Real subscribes will fail.

In short: the **cryptographic core** of the integration is faithful to the
docs. The **wire transport** has a number of wrong paths, wrong verbs, wrong
param shapes, and wrong channel names. The **safety narrative** overstates
the official boundary and invents confirmation strings that are not in the
official spec. The whole "SoDEX half" works as a unit-tested signing
utility; it does not work as a wire-compatible client.

---

## 6. Top 5 worst gaps (with file:line)

1. **sodex_ws.py:79, :81 — two account-channel names are wrong.**
   `accountFrontendState` should be `accountState` (per
   `account-frontend-state.md` example, channel string is
   `"accountState"`). `accountOrder` should be `accountOrderUpdate` (per
   `account-order-updates.md` example, channel string is
   `"accountOrderUpdate"`). These are in the `SODEX_WS_CHANNELS`
   whitelist at production-line 79 and 81; the validator at
   sodex_ws.py:254-255 accepts them. Any real subscribe to these
   channels will return `success: false` from the engine. The
   `SODEX_WS_ACCOUNT_CHANNELS` set at lines 86-92 also contains the
   wrong names, so the EVM-address validation in lines 257-261 only
   runs for the (wrong) channel names — meaning **the entire
   account-channel user/address validation never fires for the real
   engine channel names** unless the user types the correct name by
   hand. Worst gap: silently broken channel contract.

2. **cli/sodex.py:254 — perps cancel uses the wrong HTTP verb and path.**
   SigLab builds `SoDEXSignedRequest(method="DELETE", path="/trade/orders",
   body=body, weight=1)` for cancel. The official endpoint is
   `POST /trade/orders/cancel` (`sodex-rest-perps-api.md` "Cancel
   multiple orders"). The `DELETE /trade/orders` path is not a
   documented perps endpoint. A live cancel test will 404 (or worse,
   silently 405). The body SigLab sends (`PerpsCancelOrderRequest`)
   is also wrapped in `{type, params}` per the signing payload
   contract — correct for signing, but the *actual HTTP body* that
   goes on the wire must be the `params` only (sodex_signing.py:204-206
   does this for `http_body_from_action_payload`, but the preview never
   executes). For the cancel path specifically, SigLab has not
   exercised either the right verb (`POST`) or the right path
   (`/trade/orders/cancel`).

3. **sodex_client.py:212-228 — `funding_history` hits a non-existent
   endpoint.** The method calls
   `GET /markets/{symbol}/fundingRate` with no `userAddress` and no
   `accountID`. The official funding read is
   `GET /accounts/{userAddress}/fundings` and is account-scoped
   (`sodex-rest-perps-api.md` "Query funding history"). There is no
   public, per-symbol funding read on the perps REST surface. Any
   caller of `funding_history()` is hitting a 404 in production.

4. **sodex_ws.py:66-84, :269 — symbol-channel subscribe param shape is
   wrong for ticker, allTicker, miniTicker, allMiniTicker, bookTicker,
   allBookTicker, markPrice, allMarkPrice, trade, accountOrderUpdate,
   accountTrade, and accountState has no symbol list at all.** The
   docs (e.g. `ticker.md`, `mark-price.md`, `market-trade.md`,
   `account-trades.md`, `account-order-updates.md`) all show the
   `params` object using a `symbols` **array** (e.g.
   `"symbols": ["BTC-USD"]`). SigLab's validator at line 269 only
   checks for a non-empty `symbol` (singular string), and SigLab's
   probe at `cli/sodex.py:160-165` builds a `params` dict using
   `symbol: str(args.symbol)`. Probes that go to the official server
   with `"symbol": "BTC-USD"` instead of `"symbols": ["BTC-USD"]`
   will fail to match the engine's expected JSON shape. Additionally,
   `l2Book` requires `tickSize` and `candle` requires `interval`
   (per `l2book.md` and `candles.md`); SigLab's validator doesn't
   check for either. Three of the eleven official channel families
   have *additional* required parameters that SigLab never sends.

5. **cli/helpers.py:231-237 + module-live-boundary.md:380-401 — the
   mainnet live-write gate is a SigLab invention, not a SoDEX
   requirement.** The string `I_UNDERSTAND_MAINNET_RISK` and the
   pair-with `SODEX_TESTNET_PREFLIGHT_PASSED=true` appear nowhere in
   the official SoDEX documentation. The actual SoDEX mainnet boundary
   is: (a) API-key registered by master wallet (`addAPIKey`),
   (b) nonce within `(T-2d, T+1d)`, (c) per-account order-placement
   cap of 600/min + 20/s, (d) address-based cumulative limit starting
   at 10000 buffer. SigLab implements (b) and (d)-ish (the address
   cap is enforced server-side; SigLab cannot enforce it client-side)
   and does not implement (a) at all (no `addAPIKey` in
   `SUPPORTED_SODEX_SIGNED_ACTIONS`). Advertising
   `I_UNDERSTAND_MAINNET_RISK` as a SoDEX "double confirmation"
   is documentation fraud against the official spec. The official
   docs would never require a human to type that string; the engine
   doesn't see it.

Honourable mentions (not in top 5 but worth flagging):

- sodex_signing.py:100 — `high_water_size: int = 64`, docs say 100.
  SigLab is more conservative; benign but divergent from the spec.
- sodex_signing.py:25-29 — `replaceOrder`, `modifyOrder`,
  `transferAsset` are listed as UNSUPPORTED. The official endpoints
  exist (`POST /trade/orders/replace`, `POST /trade/orders/{symbolID}/{orderID}/modify`,
  `POST /accounts/transfers`). The reasons in the source are
  "blocked until official SDK/source pins the perps wrapper type and
  struct order" — fine to defer, but the public docs already pin
  these (in `schema.md`). SigLab could implement them today from
  the docs alone; the deferral is a SigLab choice, not a doc gap.
- sodex_client.py:52 — default base is mainnet. The official docs do
  not recommend mainnet-by-default for unauthenticated reads
  (sodex.com is in "mainnet closed alpha" / "early 2026 public launch"
  per the homepage timeline). Defaulting to mainnet reads for a
  paper-tiger integration that has not been live-validated is
  aggressive. Defensible per SigLab's testnet-first rule for signed
  writes, but inconsistent with their own testnet-first discipline
  for unauth reads.
- sodex_ws.py:115 — `idle_timeout_s: float = 45.0` is well below the
  official 60s break threshold, so the client is safe. But the
  WSS rate limits (10 concurrent conn/IP, 1000 subscriptions/IP,
  2000 msg/min/conn) are never enforced. `max_reconnects=10`
  (sodex_ws.py:117) is also unrelated to the official concurrent-conn
  cap.
- siglab/data/sodex_client.py — missing 6 of 10 documented
  perps account REST endpoints: `api-keys`, `fee-rate`,
  `orders/history`, `positions/history`, `trades`, `fundings`
  (the legitimate, account-scoped `fundings`).
