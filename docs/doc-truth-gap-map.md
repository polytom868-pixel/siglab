# Doc Truth And Gap Map

## SoSoValue Truth

- Auth uses `x-soso-api-key` on API requests.
- Successful business payloads use an envelope with `code: 0` where documented.
- Non-zero business codes are not success even when HTTP transport succeeds.
- Current SigLab verified wrappers remain limited to listed currencies, featured news, featured news by currency, ETF historical inflow, and current ETF metrics.
- Unverified modules remain blocked rather than guessed.

## SoDEX Truth

Official SoDEX REST v1 docs verify:

- Mainnet Perps REST base: `https://mainnet-gw.sodex.dev/api/v1/perps`.
- Testnet Perps REST base: `https://testnet-gw.sodex.dev/api/v1/perps`.
- Public market-data endpoints are unsigned and usually need only `Accept: application/json`.
- Authenticated REST writes use EIP-712 signatures.
- Signed write headers are `Content-Type: application/json`, `Accept: application/json`, `X-API-Key`, `X-API-Sign`, and `X-API-Nonce`.
- Nonces are tracked per API key, must be unique, and must remain inside the documented time window.
- Perps writes use the `futures` signing domain.
- The official SDK signs `ActionPayload{type, params}` but sends the endpoint request struct as the HTTP body. Dry-run previews must therefore distinguish `canonical_signing_payload` from `canonical_body`.
- REST response envelopes contain `code`, `timestamp`, optional `error`, and endpoint-specific `data`.
- REST weight budget is `1200` per minute per IP; unmatched endpoints default to weight `20`.
- Perps public market metadata endpoints `symbols` and `coins` are documented at weight `2`; klines remain weight `20`.
- Perps order placement uses `POST /trade/orders`; batch order weight is `1 + floor(N/40)`.
- Perps order cancellation uses `DELETE /trade/orders`; batch cancel weight is `1 + floor(N/40)`.
- Perps schedule-cancel uses `POST /trade/orders/schedule-cancel`; request weight is `1`.
- Perps leverage update uses `POST /trade/leverage`; request weight is `1`; the schema field is `marginMode`, not `marginType`.
- Perps isolated margin update uses `POST /trade/margin`; request weight is `1`; `amount` is a quoted `DecimalString`.
- Perps market endpoints include symbols, coins, tickers, mini tickers, mark prices, book tickers, order book, klines, and recent trades.
- Perps trading endpoints include place/cancel/replace orders, TP/SL modification, schedule cancel, leverage update, and isolated margin update.

## SigLab Gap Map

### Safe Now

- SoSoValue key handling is centralized and typed.
- SoSoValue unverified endpoints are blocked instead of invented.
- Dry export writes an honest SoDEX-named package.
- Live deploy refuses before writing artifacts.
- Scheduled deploy refuses before writing artifacts.
- Unsigned SoDEX public perps symbols/coins/klines client exists with envelope validation.
- Live public `perps.symbols` validation passed on 2026-05-13 with 79 rows, retry `0`, 429 `0`, transport failures `0`; the client now charges the documented metadata weight `2` instead of the unmatched default `20`.
- SoDEX signing scaffolding exists without fake signatures.
- SoDEX private-key signer adapter exists but is not configured with live secrets in repo.
- SoDEX signed perps client exists for deterministic dry-run preparation and guarded live submission.
- SoDEX perps new-order, cancel-order, schedule-cancel, update-leverage, and update-margin request builders preserve documented schema order.
- SoDEX nonce manager enforces duplicate and time-window rejection.
- SoDEX canonical payload JSON rejects float DecimalString mistakes and preserves ordered fields.
- SoDEX signed request preparation refuses non-wrapper signing payloads and sends the HTTP body without the signing-only `type` wrapper.
- SoDEX request-weight scheduler enforces atomic rolling budget admission under concurrent bursts.
- Signed write responses classify HTTP errors, rate limits, transport failures, and non-zero business envelopes.
- Update-leverage signing payloads use the official `marginMode` field.
- Runtime dependency report includes signed-path readiness and exact missing prerequisites.
- Runtime dependency report and CLI preflight explicitly warn that the SoDEX weight scheduler is process-local while the official gateway budget is per IP.
- CLI `sodex-preflight --json` refuses signed writes early with exact missing prerequisites.
- CLI `sodex-preview` builds dry-run canonical signed request inputs for new-order, cancel-order, schedule-cancel, update-leverage, and update-margin without signing or submitting.

### Unsafe Now

- Runtime SoDEX adapter is an injected-method shim, not an official REST signer.
- No real API-key/signing/account material is configured for final live signed validation.
- Signed SoDEX write submission remains unproven against a real gateway/account.
- New-order, cancel-order, schedule-cancel, update-leverage, and update-margin signed perps builders are implemented; replace/TP-SL/transfer builders remain intentionally missing.
- SoDEX request-weight scheduler is local-process only; distributed multi-process rate sharing is not implemented.

### Wrong Now

- Older generated packages may still contain Hyperliquid manifest naming.

### Missing Now

- SoDEX signed builders for replace orders, TP/SL modify, and transfers.
- Long-run live gateway rate-limit calibration.
- Operator dependency report inside exported runtime packages.

### Blocked By Infra

- Real signed SoDEX write validation is blocked without API key/signing material/account ID.

### Blocked By Docs

- No undocumented SoSoValue wrappers will be added without official callable endpoint pages.

### Blocked By Live Client Gaps

- Scheduled runner execution remains blocked until a real runner is supplied or implemented.

## Fix Queue

1. Add remaining signed perps request builders only where official schema is clear.
2. Add token/cost accounting for provider loops.
3. Continue verified SoSoValue endpoint expansion only from official docs.
4. Add operator dependency report inside exported runtime packages.
5. Add distributed SoDEX request-weight coordination if multiple SigLab processes share one egress IP.
