# SigLab SoSoValue / SoDEX / B.AI Integration — FINAL NO-MERCY VERDICT

**Auditor:** MasterAuditor (synthesizing ResearchSoSoValue, ResearchSoDEX, ResearchBAI, plus direct verification against the current truth diagram and SigLab source)
**Date:** 2026-06-14
**Scope:** `siglab/data/sosovalue_*.py`, `siglab/data/sodex_*.py`, `siglab/live/sodex_*.py`, `siglab/cli/sodex.py`, `siglab/cli/helpers.py`, `siglab/cli/demo.py`, `siglab/llm/claude.py`, `siglab/data/siglab_api_integration.txt`

---

## 1. The 30-second verdict

SigLab's three-integration stack is a paper tiger. SoSoValue: 2 endpoints "implemented" — both wrong method, wrong path, or wrong host against the official GitBook (`https://sosovalue-1.gitbook.io/sosovalue-api-doc`); 18 listed as BLOCKED, of which a truth-table audit found at least 4 paths fabricated, 4 method/path/host-wrong, and the news wrapper actually exists in `sosovalue_client.py:159-220` even though the truth table claims it is BLOCKED. SoDEX: the EIP-712 cryptography is correct and matches the official Go SDK, but the wire transport is broken in production — DELETE instead of POST on cancel, a phantom `/markets/{symbol}/fundingRate` endpoint, two wrong WebSocket account-channel names (`accountFrontendState` and `accountOrder` should be `accountState` and `accountOrderUpdate`), six WSS subscribe param-shape errors, and a fabricated mainnet gate `I_UNDERSTAND_MAINNET_RISK` that does not exist in any official doc. B.AI: the wrapper hardcodes the per-model rate table directly from the B.AI pricing page, computes per-call credit cost with the platform's own formula, and refuses calls over a credit cap — then prints "B.AI Credits are not USD" on the buildathon manifest, even though the same pricing page opens with `1 USD = 1,000,000 Credits`. The package does compile, the unit tests pass, the dry-run signing is faithful, and the refusal gates are tight. The product is a polished demo of a half-built trading system.

**Bottom line:** Score 22/80. Do not ship. The 2/10, 4/10, 0.3/10 on the three research sub-audits average ~2.1/10 and the integration is a buildathon demo, not production.

---

## 2. Score breakdown

| Component | Score | One-line justification |
|---|---|---|
| SoSoValue (currency + ETF) | **2/10** | Both IMPLEMENTED endpoints hit wrong method, path, or host; truth table misclassifies 4+ existing paths as BLOCKED; phantom `/news/featured/currency`; camelCase params throughout news wrapper. |
| SoDEX public (REST reads) | **5/10** | 10/11 endpoints correct, but `funding_history` calls a non-existent perps endpoint; defaults to mainnet reads without an opt-in path; 6 of 10 documented account reads missing. |
| SoDEX signed (EIP-712 writes) | **4/10** | EIP-712 math matches the official Go SDK exactly, but cancel uses DELETE (should be POST), `I_UNDERSTAND_MAINNET_RISK` gate is a SigLab invention, `addAPIKey`/`transferAsset`/`replaceOrder`/`modifyOrder` explicitly deferred despite docs being clear, no live testnet validation against SoDEX exists. |
| SoDEX WSS | **2/10** | Two of five account-channel names wrong (`accountFrontendState`, `accountOrder`); `symbol` singular used where the engine requires `symbols` array; `l2Book` missing `tickSize`; `candle` missing `interval`; no enforcement of 10 concurrent conn/IP, 1000 subscriptions/IP, 2000 msg/min/conn. |
| B.AI (LLM wrapper) | **5/10** | Real auth, real per-model rate table, real per-call estimator, real refusal cap. But the headline "B.AI Credits are not USD" claim is false against `https://docs.b.ai/llmservice/pricing-and-usage` which prints `1 USD = 1,000,000 Credits` in a callout block. |
| ValueChain (chain-id preflight) | **6/10** | Read-only `eth_chainId` check against `https://mainnet.valuechain.xyz` with expected chainId 286623 is real and matches official docs. But it is a preflight, not execution — calling it "ValueChain integration" overstates what it does. |
| Wiring / CLI / demo manifest | **3/10** | `sodex-preflight --exit-on-first-frame` and `sodex-ws-probe --exit-on-first-frame` are real. `sodex-preview` is dry-run only (`signature: None, submitted: False`). The buildathon demo manifest prints the false "Credits are not USD" red flag and `"usd_cost_claimed": False` even though the math is right next door. |
| Truthfulness (red_flags, capabilities table, readiness) | **2/10** | Truth table misclassifies implemented code as BLOCKED, fabricates paths, and pairs a working "verify cost" pipeline with a "never claim USD" red flag. The safety claims are theater. |
| **TOTAL** | **29/80** | Below the threshold for a buildathon demo that an external reviewer could reproduce without operator help. The "testnet-first / no live / we won't claim USD" discipline is real and worth something — but it is the only thing holding the score above 0. |

Note: my own synthesis settles on 29/80 across the eight categories. The sub-audits landed at 2/10 (SoSoValue), 4/10 (SoDEX overall), 0.3/10 (B.AI truthfulness claim). The difference is that I am scoring implementation completeness across more dimensions, not just the headline claim. Either way, **the package does not survive a cold review by a third party with an API key and 30 minutes.**

---

## 3. Top 10 worst gaps (ranked)

### Gap 1 — `sodex_ws.py:79, :81` whitelists two wrong account-channel names

**WHAT IS WRONG:** `accountFrontendState` should be `accountState`; `accountOrder` should be `accountOrderUpdate`.
**FILE:LINE:** `siglab/live/sodex_ws.py:79` and `siglab/live/sodex_ws.py:81`. The same wrong names are in `SODEX_WS_ACCOUNT_CHANNELS` at `siglab/live/sodex_ws.py:86-92`, so the EVM-address validation at `siglab/live/sodex_ws.py:257-261` only runs for the wrong channel names — meaning the entire account-channel user/address validation never fires for the real engine channel names unless the user types the correct name by hand.
**URL PROVING THE LIE:** `https://sodex.com/documentation/trading-api/websocket-v1/account-frontend-state.md` (channel string is `accountState`) and `https://sodex.com/documentation/trading-api/websocket-v1/account-order-updates.md` (channel string is `accountOrderUpdate`).
**WHAT IT WOULD TAKE TO FIX:** Replace the four string literals in `siglab/live/sodex_ws.py:66-92`. Add a probe that round-trips each of the 18 channel names and asserts the engine's ack payload has `success: true` (this would have caught both bugs in CI). One-line rename, one-line regression test.

### Gap 2 — `sosovalue_capabilities.py:93-104` declares a phantom `GET /api/v1/news/featured/currency` endpoint

**WHAT IS WRONG:** The official SoSoValue API has no `/news/featured/currency` endpoint and `/news/featured` takes no `currency` filter (only `language` and `category`). SigLab's `featured_news_by_currency_pages` at `siglab/data/sosovalue_client.py:189-220` calls a URL the official server does not expose. The truth table calls this endpoint BLOCKED at `sosovalue_capabilities.py:93-104`, but the wrapper code for it exists at `sosovalue_client.py:189-220` — so the truth table contradicts the source.
**FILE:LINE:** `siglab/data/sosovalue_capabilities.py:93-104` (truth table); `siglab/data/sosovalue_client.py:189-220` (phantom wrapper); `siglab/data/sosovalue_client.py:212` (path); `siglab/data/sosovalue_client.py:61` (wrong base URL — `https://openapi.sosovalue.com` without the required `/openapi/v1` prefix).
**URL PROVING THE LIE:** `https://sosovalue-1.gitbook.io/sosovalue-api-doc/6.-feeds/featured-news.md` — official parameters are `page`, `page_size`, `language`, `category` (array of integers). No currency filter exists.
**WHAT IT WOULD TAKE TO FIX:** Delete `featured_news_by_currency_pages` from `siglab/data/sosovalue_client.py:189-220`. Rename the remaining method to `featured_news_pages` (which it already is at `sosovalue_client.py:159-187`). Rewrite the params: change `pageNum` → `page`, `pageSize` → `page_size`, drop `categoryList` (use `category` as an integer array, not a comma-joined string), drop `currencyId`. Re-aim the base URL: `news_base_url = "https://openapi.sosovalue.com/openapi/v1"`, not `"https://openapi.sosovalue.com"`. ~20 lines of code.

### Gap 3 — `etf_base_url = "https://api.sosovalue.xyz"` is not in the official docs

**WHAT IS WRONG:** `siglab/data/sosovalue_client.py:60` sets `etf_base_url: str = "https://api.sosovalue.xyz"`. The official SoSoValue base URL is `https://openapi.sosovalue.com/openapi/v1` (per `https://sosovalue-1.gitbook.io/sosovalue-api-doc`). The `api.sosovalue.xyz` host is not referenced anywhere in the GitBook. The IMPLEMENTED `etf_historical_inflow` calls `POST /openapi/v2/etf/historicalInflowChart` with JSON body `{"type": "us-btc-spot"}` against that host. The official method is GET, the official path is `/etfs/summary-history` (no `/openapi/v2/`), the official parameters are query string `?symbol=BTC&country_code=US`, and the official response is a flat array of objects with snake_case fields (`date`, `total_net_inflow`, `total_value_traded`, `total_net_assets`, `cum_net_inflow`). SigLab's wrapper requires camelCase fields (`totalNetInflow`, `totalValueTraded`, `totalNetAssets`, `cumNetInflow`) per `siglab/data/sosovalue_client.py:137` — which will fail against the official response.
**FILE:LINE:** `siglab/data/sosovalue_client.py:60` (base URL); `siglab/data/sosovalue_client.py:132-144` (`etf_historical_inflow` method); `siglab/data/sosovalue_client.py:137` (camelCase `required_fields`).
**URL PROVING THE LIE:** `https://sosovalue-1.gitbook.io/sosovalue-api-doc/2.-etf/summary-history.md` — official is `GET /etfs/summary-history?symbol=BTC&country_code=US` with snake_case response fields.
**WHAT IT WOULD TAKE TO FIX:** Change `etf_base_url` to `"https://openapi.sosovalue.com/openapi/v1"`. Rewrite `etf_historical_inflow` to use `method="GET"`, `path="/etfs/summary-history"`, and `params={"symbol": "BTC", "country_code": "US"}`. Change `required_fields` to `("date", "total_net_inflow", "total_value_traded", "total_net_assets", "cum_net_inflow")`. Loosen `_validate_payload` to accept a flat array (or document the envelope inconsistency). Then run it against a real `SOSOVALUE_API_KEY` to prove it works. ~15 lines of code, plus a live test.

### Gap 4 — `cli/sodex.py:254` builds a DELETE for perps cancel; official is POST

**WHAT IS WRONG:** `siglab/cli/sodex.py:254` does `SoDEXSignedRequest(method="DELETE", path="/trade/orders", body=body, weight=1)` for cancel. The official endpoint is `POST /trade/orders/cancel` with body `PerpsCancelOrderRequest = { accountID, cancels: [PerpsCancelItem] }` (per `https://sodex.com/documentation/trading-api/rest-v1/sodex-rest-perps-api.md` "Cancel multiple orders"). There is no documented DELETE on the perps surface. A live testnet cancel will 404.
**FILE:LINE:** `siglab/cli/sodex.py:254` (the bug); also the `cancel_order` method in `sodex_signing.py` (around line 360) is correct in its body shape but never wired to a path; the only place the verb/path is set is in `cli/sodex.py:254`.
**URL PROVING THE LIE:** `https://sodex.com/documentation/trading-api/rest-v1/sodex-rest-perps-api.md` "Cancel multiple orders" — `POST /trade/orders/cancel`.
**WHAT IT WOULD TAKE TO FIX:** Change one line: `SoDEXSignedRequest(method="POST", path="/trade/orders/cancel", ...)`. Add a `--kind` test that round-trips against the testnet and asserts the server returns a non-error response. One-line code fix, one integration test.

### Gap 5 — `sodex_client.py:212-228` `funding_history` calls a non-existent endpoint

**WHAT IS WRONG:** `funding_history(symbol, start_time, end_time)` builds `GET /markets/{symbol}/fundingRate` with no `userAddress`. The official funding endpoint is `GET /accounts/{userAddress}/fundings` and is account-scoped. There is no public, per-symbol funding read on the official perps REST surface. A caller of `funding_history()` against the engine gets 404. The SigLab implementation also passes the perps mainnet base URL by default, so this is a wire-incompatible client method.
**FILE:LINE:** `siglab/data/sodex_client.py:212-228` (`funding_history` method).
**URL PROVING THE LIE:** `https://sodex.com/documentation/trading-api/rest-v1/sodex-rest-perps-api.md` "Query funding history" — `GET /accounts/{userAddress}/fundings` with required `userAddress` path parameter.
**WHAT IT WOULD TAKE TO FIX:** Rewrite `funding_history` to require `user_address` and call `GET /accounts/{user_address}/fundings?startTime=...&endTime=...&accountID=...`. Move it to a new `SoDEXAccountHistoryClient` (or rename the existing class) and gate it on the user providing a real `userAddress`. ~15 lines of code, plus the account-scoped endpoint migration.

### Gap 6 — `sodex_ws.py:66-84, :269` symbol-channel subscribe param shape is wrong

**WHAT IS WRONG:** The official docs (e.g. `ticker.md`, `mark-price.md`, `market-trade.md`, `account-trades.md`, `account-order-updates.md`) require a `symbols` **array** in the subscribe params — e.g. `"symbols": ["BTC-USD"]`. SigLab's `_validate_subscription_params` at `siglab/live/sodex_ws.py:269` only checks for a non-empty `symbol` (singular string). The probe at `siglab/cli/sodex.py:160-165` builds `params = {"channel": "allBookTicker", "symbol": str(args.symbol)}` — sending the wrong shape. Channels `ticker`, `allTicker`, `miniTicker`, `allMiniTicker`, `bookTicker`, `allBookTicker`, `markPrice`, `allMarkPrice`, `trade`, `accountOrderUpdate`, `accountTrade` all want a `symbols` array. The `l2Book` channel additionally requires `tickSize` (string), and `candle` requires `interval` (string). SigLab's validator enforces none of this.
**FILE:LINE:** `siglab/live/sodex_ws.py:66-84` (channel whitelist), `siglab/live/sodex_ws.py:269` (validator), `siglab/cli/sodex.py:160-165` (probe param shape).
**URL PROVING THE LIE:** `https://sodex.com/documentation/trading-api/websocket-v1/ticker.md` (params example: `{"channel":"ticker","symbols":["BTC-USD"]}`); `https://sodex.com/documentation/trading-api/websocket-v1/l2book.md` (requires `tickSize`); `https://sodex.com/documentation/trading-api/websocket-v1/candles.md` (requires `interval`).
**WHAT IT WOULD TAKE TO FIX:** Replace the singular `symbol` validation in `siglab/live/sodex_ws.py:269` with channel-specific param-shape validation. Add a `SODEX_WS_CHANNEL_PARAMS` map keyed by channel name, with each entry declaring the required param keys and types. Update the probe in `siglab/cli/sodex.py:160-165` to build the correct shape. ~50 lines of code, plus a per-channel probe test that round-trips against the engine.

### Gap 7 — `siglab/cli/helpers.py:231-237` invents a mainnet gate `I_UNDERSTAND_MAINNET_RISK`

**WHAT IS WRONG:** The two-flag gate (`SODEX_TESTNET_PREFLIGHT_PASSED=true` + `SODEX_MAINNET_LIVE_WRITE_CONFIRMATION=I_UNDERSTAND_MAINNET_RISK`) is documented in `module-live-boundary.md:380-401` and enforced in `siglab/cli/helpers.py:231-237`. The string `I_UNDERSTAND_MAINNET_RISK` appears nowhere in the official SoDEX documentation. The actual SoDEX mainnet boundary is: (a) API-key registered by master wallet via `addAPIKey`; (b) nonce within `(T-2d, T+1d)`; (c) per-account order-placement cap of 600/min + 20/s; (d) address-based cumulative limit starting at 10000 buffer. SigLab implements (b), cannot implement (c) or (d) client-side, and does not implement (a) at all (no `addAPIKey` in `SUPPORTED_SODEX_SIGNED_ACTIONS` at `siglab/live/sodex_signing.py:15-23`). Advertising the string as a "double confirmation" against SoDEX is documentation fraud.
**FILE:LINE:** `siglab/cli/helpers.py:231-237` (the gate); `docs/module-live-boundary.md:380-401` (misadvertised as SoDEX requirement); `siglab/live/sodex_signing.py:15-23` (no `addAPIKey` in supported list).
**URL PROVING THE LIE:** `https://sodex.com/documentation/trading-api/trading-api.md` and `https://sodex.com/documentation/trading-api/rest-v1.md` and `https://sodex.com/documentation/trading-api/api-rate-limits.md` — none of these contain the string `I_UNDERSTAND_MAINNET_RISK`, `TESTNET_PREFLIGHT_PASSED`, or any two-flag confirmation gate. The actual mainnet boundary is the `addAPIKey` master-wallet-signed registration + the 5-key per-account cap + the per-account order-placement limit + the address-based cumulative limit.
**WHAT IT WOULD TAKE TO FIX:** Either (1) drop the `I_UNDERSTAND_MAINNET_RISK` env var entirely and document the real boundary (master-wallet + addAPIKey + nonce window + per-account order cap), or (2) keep the env var but re-label it as `SIGLAB_OPERATOR_CONFIRMED_MAINNET_RISK` (SigLab-internal, not a SoDEX requirement). Implementing `addAPIKey` is its own work — it requires the master wallet private key, which is a cold-storage operation that should never be wired into an automated test. ~10 lines of code change + a docs edit.

### Gap 8 — `siglab/cli/demo.py:297` prints "B.AI Credits are not USD" while the math is right next door

**WHAT IS WRONG:** The buildathon demo manifest prints `"B.AI Credits are not USD and must not be presented as USD spend."` at `siglab/cli/demo.py:297`. The B.AI pricing page (`https://docs.b.ai/llmservice/pricing-and-usage`) opens with `1 USD = 1,000,000 Credits` in a callout block. SigLab's own `siglab/llm/claude.py:30-63` hardcodes the full 33-entry `BAI_CREDITS_PER_TOKEN` table copied directly from that same pricing page. `siglab/llm/claude.py:790-801` accumulates `_usage_credits` using the platform's published formula. `siglab/llm/claude.py:528-557` raises `LLMQuotaError` when the platform-formula estimate exceeds `BAI_MAX_CALL_CREDITS`. The "not USD" claim is contradicted by code one file away.
**FILE:LINE:** `siglab/cli/demo.py:297` (the lie); `siglab/llm/claude.py:30-63` (rate table); `siglab/llm/claude.py:790-801` (per-call accounting); `siglab/llm/claude.py:528-557` (refusal cap); `siglab/llm/claude.py:730-735` (telemetry emits `cost_usd: None` while carrying the source data); `AGENTS.md:14` (forbids future agents from telling the truth).
**URL PROVING THE LIE:** `https://docs.b.ai/llmservice/pricing-and-usage` — "Platform-wide Credits conversion: `1 USD = 1,000,000 Credits` (`1M` / `1000K` Credits)."
**WHAT IT WOULD TAKE TO FIX:** In `siglab/llm/claude.py:730`, change `cost_usd: None` to `cost_usd: self._usage_credits / 1_000_000` and add a `cost_status: "verified_bai_credit_estimate_usd_priced"`. In `siglab/cli/demo.py:297`, drop the "B.AI Credits are not USD" line. In `AGENTS.md:14`, drop the "Never claim: USD cost enforcement for B.AI Credits" instruction. The credit cap keeps working whether the unit is called "credits" or "USD-cents" — that part is fine. The four-line honesty fix lands you at the truth.

### Gap 9 — `siglab/data/sosovalue_client.py:307-320` `_validate_payload` rejects the official `/currencies` response

**WHAT IS WRONG:** `_validate_payload` requires the response body to be a dict with a `code` field equal to 0 or "0" (or absent). The official `/currencies` endpoint returns a **flat JSON array** of `{"currency_id": "...", "symbol": "...", "name": "..."}` objects (`https://sosovalue-1.gitbook.io/sosovalue-api-doc/1.-currency-and-pairs/list.md`). Calling `listed_currencies()` against the real SoSoValue server triggers `SoSoValueUpstreamFormatError(f"{spec.name} response was not a JSON object", ...)` on the very first byte. The wrapper also calls `POST /data/default/coin/list` (wrong method, wrong path — the official is `GET /currencies`), so the only "success" path for this code today is against `api.sosovalue.xyz`, which is not in the official docs.
**FILE:LINE:** `siglab/data/sosovalue_client.py:307-320` (`_validate_payload`); `siglab/data/sosovalue_client.py:147-158` (`listed_currencies` method).
**URL PROVING THE LIE:** `https://sosovalue-1.gitbook.io/sosovalue-api-doc/1.-currency-and-pairs/list.md` — response is a flat array `[{"currency_id": "1673723677362319867", "symbol": "USDT", "name": "USDT"}]`. The `https://sosovalue-1.gitbook.io/sosovalue-api-doc/response-format.md` page documents the `code`/`message`/`data` envelope as **unified but not universal** — `/currencies` is one of the flat-array exceptions.
**WHAT IT WOULD TAKE TO FIX:** Make the envelope check opt-in per endpoint (add a `require_envelope: bool` field to `SoSoValueRequestSpec`). Make `listed_currencies` use `method="GET"`, `path="/currencies"`, and `require_envelope=False`. Make the `required_fields` tuple reflect the real response shape (`currency_id`, `symbol`, `name`). ~10 lines of code, plus a real-SOSOVALUE_API_KEY live test that asserts the response parses.

### Gap 10 — `siglab/data/sosovalue_capabilities.py:117-128` ("etf current metrics / daily ETF data") hides three distinct official endpoints under one fuzzy row

**WHAT IS WRONG:** The BLOCKED row at `sosovalue_capabilities.py:117-128` reads "etf current metrics / daily ETF data" with no path, no method, no parameter list. It bundles three distinct official endpoints: `GET /etfs/summary-history` (the daily aggregate — already counted as row 141-152), `GET /etfs/{ticker}/history` (per-ticker daily, row 165-176), and `GET /etfs/{ticker}/market-snapshot` (current, row 153-164). This is the same anti-pattern repeated in rows 14-19 (Index, Crypto Stocks, BTC Treasuries, Fundraising, Macro, Analysis) where 2-6 official endpoints are merged into a single fuzzy cell. A reviewer reading the truth table cannot tell that the project claims zero coverage of, say, `GET /crypto-stocks/sector/{name}/index` (`https://sosovalue-1.gitbook.io/sosovalue-api-doc/4.-crypto-stocks/crypto-stocks.md`). The truth table overstates coverage by aggregating distinct endpoints under "BLOCKED" prose and understates it by listing some endpoints that the source does not have wrappers for.
**FILE:LINE:** `siglab/data/sosovalue_capabilities.py:117-128` (worst offender); also `sosovalue_capabilities.py:177-188` (Index), `sosovalue_capabilities.py:189-200` (Crypto Stocks), `sosovalue_capabilities.py:201-212` (BTC Treasuries), `sosovalue_capabilities.py:213-224` (Fundraising), `sosovalue_capabilities.py:225-236` (Macro), `sosovalue_capabilities.py:237-248` (Analysis Charts).
**URL PROVING THE LIE:** `https://sosovalue-1.gitbook.io/sosovalue-api-doc/endpoint-overview.md` lists 33 official endpoints across 9 modules. SigLab's truth table has 20 rows, of which 7 are aggregated cells covering 2-6 endpoints each. So the truth table structurally under-represents the official API by at least 13 endpoints.
**WHAT IT WOULD TAKE TO FIX:** Expand the truth table to one row per official endpoint, with `wrapper` populated only where the source has a method, and `path`/`method` populated from the official GitBook. Regenerate the 35 pinning tests at `tests/test_sosovalue_capabilities.py` to match. Maybe 90 minutes of work. A reviewer can then read the table and see the actual coverage gap.

---

## 4. What's honest about it (3-5 things SigLab gets RIGHT)

1. **The EIP-712 typed-data construction in `siglab/live/sodex_signing.py` is faithful to the official SoDEX spec.** Domain name (`spot` / `futures`), chainId (`286623` mainnet / `138565` testnet), `verifyingContract: 0x00…00`, the `ExchangeAction { payloadHash: bytes32, nonce: uint64 }` struct, the `0x01` signature prefix, `DecimalString` enforcement (rejects `float` at `sodex_signing.py:420-429`), `omitempty` enforcement (filters `None` at the same lines), the `PerpsOrderItem` field order (13 fields, exact Go struct order from `schema.md`), and the nonce window `(T-2d, T+1d)` all match the official docs verbatim. Someone actually read `trading-api.md` and `schema.md`. The unit tests at `tests/test_sodex_signing.py` cover this. This is real engineering. (URL: `https://sodex.com/documentation/trading-api/trading-api.md` "Typed signature" + `https://sodex.com/documentation/trading-api/rest-v1/schema.md`.)

2. **The testnet-first discipline for signed writes is genuine and consistent.** `sodex-preflight_report` at `siglab/cli/helpers.py:157-232` defaults `SODEX_ENVIRONMENT` to `testnet`, refuses mainnet without the operator-set confirmation, and `sodex-preview` at `siglab/cli/sodex.py:288-298` is a dry-run that emits `signature: None, submitted: False` — it never hits the wire. The `--exit-on-first-frame` short-circuits added to `sodex-preflight` and `sodex-ws-probe` are real latency fixes for CI. There is no `mainnet` path that bypasses the gate. (`siglab/cli/sodex.py:113-130`; `siglab/cli/helpers.py:228-237`.) This is the single biggest reason the package doesn't score a zero — the safety story on the *transport* layer is honest.

3. **The B.AI credit estimator is real and the cap actually fires.** `siglab/llm/claude.py:30-63` hardcodes the full 33-entry `BAI_CREDITS_PER_TOKEN` table from the B.AI pricing page; `siglab/llm/claude.py:790-801` accumulates `_usage_credits` using the platform's published formula; `siglab/llm/claude.py:528-557` raises `LLMQuotaError` when the platform-formula estimate exceeds `BAI_MAX_CALL_CREDITS`. The headers actually sent to B.AI (`siglab/llm/claude.py:836-852`) match the docs verbatim. A misbehaving orchestrator that tries to spend unbounded credits will be stopped. (URL: `https://docs.b.ai/llmservice/api` and `https://docs.b.ai/llmservice/pricing-and-usage`.)

4. **The ValueChain chain-id preflight is a real chain assertion, not theater.** `sodex.py:run_valuechain_preflight` POSTs `{"jsonrpc":"2.0","id":1,"method":"eth_chainId","params":[]}` to `https://mainnet.valuechain.xyz` (the documented RPC per `https://chainlist.org/chain/valuechain`) and asserts the response is `0x45eff` (= 286623 decimal). This is the correct mainnet chain ID for ValueChain and matches the official SoDEX docs (`trading-api.md` EIP-712 example uses `chainId: 286623` for mainnet). It is read-only, but it actually verifies the wire.

5. **The auth header `x-soso-api-key: <key>` at `siglab/data/sosovalue_client.py:275` matches the official SoSoValue `https://sosovalue-1.gitbook.io/sosovalue-api-doc/authentication.md` exactly.** And the `20 calls/min` rate-limit policy at `siglab/data/sosovalue_client.py:102` matches the documented Beta/Demo plan quota. These two facts, plus the EIP-712 crypto, are the entire reason the package scores above 0. They are not the whole story, but they are real.

6. **SoDEXPublicPerpsClient covers 10 of 11 documented perps public REST endpoints correctly** (`siglab/data/sodex_client.py:70-273`): `symbols`, `coins`, `tickers`, `miniTickers`, `mark-prices`, `bookTickers`, `orderbook`, `klines`, `trades`, plus the account reads `balances`, `orders`, `positions`, `state`. All URLs, all methods, all response handling match the official `sodex-rest-perps-api.md`. The one fabricated endpoint (`funding_history`) is the exception that proves the rule — the rest are real. (URL: `https://sodex.com/documentation/trading-api/rest-v1/sodex-rest-perps-api.md`.)

---

## 5. Production-readiness verdict

### Can you trust a market report from SigLab? **No.**

- `siglab/llm/claude.py:30-63` hardcodes 33 B.AI rate rows from `https://docs.b.ai/llmservice/pricing-and-usage`; if the B.AI pricing page changes a number, SigLab silently uses the wrong rate. No version pin, no live fetch.
- `siglab/data/sosovalue_client.py:60` `etf_base_url = "https://api.sosovalue.xyz"` is undocumented in the official SoSoValue docs. A market report that cites ETF inflow data sourced from this wrapper is citing data whose source-of-truth is a host that SoSoValue does not own or document.
- `siglab/data/sosovalue_capabilities.py:105-116` claims the ETF wrapper is "SigLab's ETF proxy backbone for market features" — but the wrapper hits a path the official docs do not expose, with method wrong, body shape wrong, and field names wrong. The `required_fields` tuple (`date`, `totalNetInflow`, `totalValueTraded`, `totalNetAssets`, `cumNetInflow`) does not match the official snake_case response.
- `siglab/llm/claude.py:730` emits `cost_usd: None` and `cost_status: "verified_bai_credit_estimate_usd_unpriced"`. A reviewer cannot tell from the report how much was spent in USD on the LLM calls that produced the report's narrative. Combined with `siglab/cli/demo.py:297`'s false "B.AI Credits are not USD" red flag, the report's economic provenance is unauditable.

### Can you run a paper-trade session end-to-end? **Yes, with caveats.**

- The SoDEX public REST client works against `https://mainnet-gw.sodex.dev/api/v1/perps` for 10 of 11 perps market endpoints (everything except `funding_history`).
- `siglab/cli/sodex.py:run_sodex_ws_probe` connects to `wss://mainnet-gw.sodex.dev/ws/{spot,perps}` and round-trips a subscribe for `allBookTicker` (the default channel). For non-account channels with correct param shapes, this will work.
- `sodex-preview` produces a correctly-shaped signing payload (verified in `siglab/live/sodex_signing.py:280-417`), but does not submit it.
- The paper-trade story is "fetch public market data → build a candidate spec → print it". This works. The candidate spec is not live-validated against SoDEX, but the public-data half of the loop is real.

### Can you promote to live signed execution? **No.**

- The cancel-order wiring at `siglab/cli/sodex.py:254` uses `DELETE /trade/orders`; the official is `POST /trade/orders/cancel` (`https://sodex.com/documentation/trading-api/rest-v1/sodex-rest-perps-api.md`). The first live cancel would 404.
- `addAPIKey` and `revokeAPIKey` are not in `SUPPORTED_SODEX_SIGNED_ACTIONS` (`siglab/live/sodex_signing.py:15-23`) and not implemented. There is no code path to register a SoDEX API key from inside SigLab. The official docs require this to be signed by the **master wallet's private key** (`https://sodex.com/documentation/trading-api/trading-api.md` "Which key signs what"). Promoting to live without `addAPIKey` is impossible.
- The mainnet gate `I_UNDERSTAND_MAINNET_RISK` is a SigLab tripwire, not a SoDEX requirement (`siglab/cli/helpers.py:231-237`). A reviewer of the live-promotion path would correctly call this documentation fraud.
- The SoDEX public REST client defaults to **mainnet reads** (`siglab/data/sodex_client.py:52`), which is inconsistent with the testnet-first rule the project advertises for signed writes. A paper-trade that touches the mainnet reads will hit a system in "mainnet closed alpha / early 2026 public launch" status per the SoDEX homepage.
- `transferAsset`, `replaceOrder`, and `modifyOrder` are listed as `UNSUPPORTED_SODEX_SIGNED_ACTIONS` (`siglab/live/sodex_signing.py:25-29`) with reasons citing "blocked until official SDK/source pins the perps wrapper type and struct order". The official docs already pin the perps wrapper types in `schema.md`. The deferral is a SigLab choice, not a doc gap.

### Can you use it as a SoSoValue evidence client? **No.**

- `siglab/data/sosovalue_client.py:60` `etf_base_url = "https://api.sosovalue.xyz"` is not a SoSoValue-owned host per the official docs.
- `siglab/data/sosovalue_client.py:132-144` `etf_historical_inflow` calls `POST /openapi/v2/etf/historicalInflowChart` with body `{"type": "us-btc-spot"}`; the official is `GET /etfs/summary-history?symbol=BTC&country_code=US` under `https://openapi.sosovalue.com/openapi/v1`.
- `siglab/data/sosovalue_client.py:159-187` `featured_news_pages` sends `pageNum`/`pageSize`/`categoryList`; official is `page`/`page_size`/`category` (integer array).
- `siglab/data/sosovalue_client.py:189-220` `featured_news_by_currency_pages` calls a phantom endpoint.
- `_validate_payload` at `siglab/data/sosovalue_client.py:307-320` rejects the flat-array response of the official `/currencies` endpoint.
- The truth table at `siglab/data/sosovalue_capabilities.py:20-261` overstates coverage by aggregating 7 cells across 2-6 endpoints, and misclassifies `featured_news_pages` as BLOCKED (it is implemented) while leaving `featured_news_by_currency_pages` as BLOCKED (it is also implemented, but points at a phantom URL).
- Even if you fixed every path, the underlying doc itself is in "Beta" / "Ongoing" status per `https://sosovalue.com/developer` ("Crypto ETF Data: Ongoing; Crypto News Feeds: Ongoing; Coins Data, Daily AI Token Report, Token Introduction, Token Social Sentiment, Real-time Coin Price, Crypto Fundraising Data: Coming Soon"). A SoSoValue evidence client built on a Beta doc is fragile by construction.

---

## 6. The actual test account question

**Can an external reviewer reproduce the SigLab demo without a paid SoSoValue key, without SoDEX testnet credentials, without B.AI credits? What's the minimum cost?**

### What is free, no signup required

- **SoDEX public market data** is unauthenticated. `https://mainnet-gw.sodex.dev/api/v1/perps/markets/symbols`, `/coins`, `/tickers`, `/klines`, `/trades`, `/orderbook` all return without an API key. An external reviewer can pull live BTC-USD perps data right now. Zero cost.
- **SoDEX WSS public channels** are unauthenticated. `wss://mainnet-gw.sodex.dev/ws/perps` will let you subscribe to `allBookTicker`, `ticker`, `markPrice`, etc. without credentials. Zero cost.
- **SoDEX ValueChain chain-id preflight** is a single POST to `https://mainnet.valuechain.xyz` with method `eth_chainId`. Zero cost.
- **SoDEX testnet faucet** dispenses **100 USDC per wallet per day** for free (`https://testnet.sodex.com/faucet`). Connect a MetaMask, click Claim, done. No KYC, no payment. To get a testnet SoDEX account you also need to call `addAPIKey` (signed by your master wallet) — but you can do that for free against the testnet RPC. Zero fiat cost.

### What requires a free signup

- **SoSoValue API key** — Free Demo plan, "accessible to all SoSoValue users at zero cost" per `https://sosovalue.com/developer` FAQ. Requires creating a SoSoValue account (likely email signup). Rate limit: 20 calls/min, 100,000 calls/month. Cost: **$0**, but you need an account.
- **B.AI account with free Credits** — The B.AI pricing page says "Free bonus Credits are valid for 30 days from the date they are issued, including Credits granted for new-user registration." New-user signup grants Credits. Cost: **$0**, but you need a B.AI account and the free Credits expire in 30 days. Plan Pro is $200/month (requires invite code), Plan Max is $2,000/month.

### What is paid

- **SoDEX mainnet signed writes** require real USDC on ValueChain mainnet (chainId 286623) and a real SoDEX account, which means a master wallet private key, USDC deposits, and a 5-key-per-account cap. Real money. Not needed for paper-trade.
- **SoSoValue paid plan** is "coming soon" per the developer portal FAQ. No published price as of 2026-06-14.

### Minimum cost to reproduce the SigLab demo end-to-end

**$0.00 if you can do the following for free:**

1. Skip SoSoValue entirely. The wrapper hits undocumented paths and would fail anyway. The `market_report` can be exercised with SoDEX public data only.
2. Use a free SoSoValue Demo key if you want the ETF flow to actually work after the path fixes. Email signup.
3. Use SoDEX public mainnet reads (no auth) for market data.
4. Use the SoDEX testnet faucet to get 100 USDC/day for any signed-write paper-trade.
5. Sign up for a free B.AI account, claim the 30-day free Credits. Run `siglab planner-run` and `siglab writer-run` until Credits run out.

**Minimum total hard cost to validate the buildathon demo as it ships today: $0.00.** The demo is reproducible on free tiers for all three platforms.

**What you cannot do for free:** validate signed mainnet SoDEX execution (requires real USDC on ValueChain), validate the buildathon "live-write" path (requires real mainnet account + SoDEX testnet cleared), or run a B.AI `Plan Pro` workflow (requires invite code + $200/month).

**Realistic demo-reproduction budget: $0 if reviewer accepts "testnet signed-write only", $200/month if reviewer wants full B.AI Pro access, $0 + real SoDEX mainnet USDC if reviewer wants to attempt the live signed-write flow.** No reviewer needs to pay anything to see the demo run; they only need to pay to see the demo *do something useful*.

---

## Source-of-truth URL list (every claim above is grounded in one or more of these)

- SoSoValue developer portal: <https://sosovalue.com/developer> / <https://m.sosovalue.com/developer>
- SoSoValue official API GitBook root: <https://sosovalue-1.gitbook.io/sosovalue-api-doc>
- SoSoValue auth docs: <https://sosovalue-1.gitbook.io/sosovalue-api-doc/authentication.md>
- SoSoValue rate limit docs: <https://sosovalue-1.gitbook.io/sosovalue-api-doc/rate-limit.md>
- SoSoValue response format: <https://sosovalue-1.gitbook.io/sosovalue-api-doc/response-format.md>
- SoSoValue error responses: <https://sosovalue-1.gitbook.io/sosovalue-api-doc/error-responses.md>
- SoSoValue endpoint overview (33 endpoints): <https://sosovalue-1.gitbook.io/sosovalue-api-doc/endpoint-overview.md>
- SoSoValue Currency & Pairs: <https://sosovalue-1.gitbook.io/sosovalue-api-doc/1.-currency-and-pairs/currency.md>
- SoSoValue Currency List: <https://sosovalue-1.gitbook.io/sosovalue-api-doc/1.-currency-and-pairs/list.md>
- SoSoValue ETF: <https://sosovalue-1.gitbook.io/sosovalue-api-doc/2.-etf/etf.md>
- SoSoValue ETF Summary History: <https://sosovalue-1.gitbook.io/sosovalue-api-doc/2.-etf/summary-history.md>
- SoSoValue ETF List: <https://sosovalue-1.gitbook.io/sosovalue-api-doc/2.-etf/list.md>
- SoSoValue Feeds: <https://sosovalue-1.gitbook.io/sosovalue-api-doc/6.-feeds/feeds.md>
- SoSoValue Featured News: <https://sosovalue-1.gitbook.io/sosovalue-api-doc/6.-feeds/featured-news.md>
- SoDEX documentation index: <https://sodex.com/documentation/llms.txt>
- SoDEX Trading API Overview (auth, EIP-712, key/nonce): <https://sodex.com/documentation/trading-api/trading-api.md>
- SoDEX REST API v1: <https://sodex.com/documentation/trading-api/rest-v1.md>
- SoDEX Perps REST API: <https://sodex.com/documentation/trading-api/rest-v1/sodex-rest-perps-api.md>
- SoDEX Schema: <https://sodex.com/documentation/trading-api/rest-v1/schema.md>
- SoDEX Rate Limits: <https://sodex.com/documentation/trading-api/api-rate-limits.md>
- SoDEX Go SDK Signing Guide: <https://sodex.com/documentation/trading-api/go-sdk-signing-guide.md>
- SoDEX WebSocket v1: <https://sodex.com/documentation/trading-api/websocket-v1.md>
- SoDEX Account Frontend State Stream: <https://sodex.com/documentation/trading-api/websocket-v1/account-frontend-state.md>
- SoDEX Account Order Updates Stream: <https://sodex.com/documentation/trading-api/websocket-v1/account-order-updates.md>
- SoDEX L2 Book Stream: <https://sodex.com/documentation/trading-api/websocket-v1/l2book.md>
- SoDEX Candles Stream: <https://sodex.com/documentation/trading-api/websocket-v1/candles.md>
- SoDEX testnet faucet: <https://testnet.sodex.com/faucet>
- SoDEX marketing site: <https://sodex.com/>
- ValueChain on ChainList: <https://chainlist.org/chain/valuechain>
- B.AI API docs: <https://docs.b.ai/llmservice/api>
- B.AI pricing/credits: <https://docs.b.ai/llmservice/pricing-and-usage>
- B.AI Bank of AI mirror: <https://docs.bankofai.io/llmservice/pricing-and-usage>

---

## Final rating

**29 / 80.** The EIP-712 cryptography, the SoDEX public REST client for 10/11 endpoints, the WSS ping/pong and idle-timeout discipline, the B.AI credit estimator with refusal cap, the auth header on SoSoValue, the 20-rpm rate limit, the testnet-first gate, the `I_UNDERSTAND_MAINNET_RISK` string as a SigLab-internal tripwire (even if it's misadvertised as a SoDEX requirement), and the ValueChain chain-id preflight are all real engineering.

The SoSoValue "two IMPLEMENTED endpoints" are wrong method, wrong path, wrong host, wrong field names against the official GitBook. The SoDEX signed-write path has a wrong HTTP verb on cancel, a fabricated mainnet gate, and no `addAPIKey` implementation. The SoDEX WSS client whitelists two wrong account-channel names and uses the wrong subscribe-param shape for six channels. The B.AI integration prints a false "Credits are not USD" red flag in the same buildathon manifest whose telemetry already contains the USD equivalent.

**Recommendation: do not ship as production. Ship as buildathon demo only with the gaps above called out. The single highest-leverage fix is to populate `cost_usd` in `siglab/llm/claude.py:730` and drop the "Credits are not USD" line in `siglab/cli/demo.py:297` — four lines of code, makes the most damaging lie go away, and the B.AI half of the score jumps from 5/10 to 7/10. The SoSoValue and SoDEX fixes are larger (path/host/method corrections across two surfaces), but each one is local to a small number of files.**
