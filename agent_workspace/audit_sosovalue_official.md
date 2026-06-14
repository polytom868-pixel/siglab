# SigLab ↔ Official SoSoValue API — Brutal External Audit

**Auditor**: ResearchSoSoValue (external research agent)
**Date**: 2026-06-14
**Scope**: `siglab/data/sosovalue_client.py`, `siglab/data/sosovalue_capabilities.py`
**Sources of truth**:
- SoSoValue developer portal: https://sosovalue.com/developer (also reachable at https://m.sosovalue.com/developer)
- Official API docs (GitBook): https://sosovalue-1.gitbook.io/sosovalue-api-doc
- Doc index: https://sosovalue-1.gitbook.io/sosovalue-api-doc/llms.txt
- Base URL declared by SoSoValue: `https://openapi.sosovalue.com/openapi/v1`
- Doc roadmap: https://sosovalue.com/developer (Crypto ETF Data = "Ongoing"; Coins Data, Daily AI Token Report, Token Introduction, Token Social Sentiment, Real-time Coin Price, Crypto Fundraising Data = all "Coming Soon")
- Official launch announcement: https://x.com/SoSoValueCrypto/status/1907734828214358201 (Apr 2, 2025)

---

## 1. What SigLab claims

SigLab's truth table at `siglab/data/sosovalue_capabilities.py:20-261` declares 20 SoSoValue rows with **2 IMPLEMENTED / 18 BLOCKED**:

| # | Module | Endpoint (claimed) | Method | Status | Source line |
|---|---|---|---|---|---|
| 1 | Currency & Pairs | `POST /openapi/v1/data/default/coin/list` | POST | IMPLEMENTED | L21-32 |
| 2 | Currency & Pairs | `GET /currencies/{id}/market-snapshot` | GET | BLOCKED | L33-44 |
| 3 | Currency & Pairs | `GET /currencies/{id}/klines` | GET | BLOCKED | L45-56 |
| 4 | Currency & Pairs | `GET /currencies/{id}` | GET | BLOCKED | L57-68 |
| 5 | Currency & Pairs | `GET /currencies/{id}/{token-econ,supply,pairs,sector-spotlight,fundraising}` | GET | BLOCKED | L69-80 |
| 6 | Feeds | `GET /api/v1/news/featured` | GET | BLOCKED | L81-92 |
| 7 | Feeds | `GET /api/v1/news/featured/currency` | GET | BLOCKED | L93-104 |
| 8 | ETF | `POST /openapi/v2/etf/historicalInflowChart` | POST | IMPLEMENTED | L105-116 |
| 9 | ETF | "etf current metrics / daily ETF data" | ? | BLOCKED | L117-128 |
| 10 | ETF | `GET /etfs/list` | GET | BLOCKED | L129-140 |
| 11 | ETF | `GET /etfs/summary-history` | GET | BLOCKED | L141-152 |
| 12 | ETF | `GET /etfs/{ticker}/market-snapshot` | GET | BLOCKED | L153-164 |
| 13 | ETF | `GET /etfs/{ticker}/history` | GET | BLOCKED | L165-176 |
| 14 | SoSoValue Index | "index data / constituents / market snapshot / klines" | ? | BLOCKED | L177-188 |
| 15 | Crypto Stocks | "stock list / market snapshot / market cap / klines / sectors" | ? | BLOCKED | L189-200 |
| 16 | BTC Treasuries | "BTC treasury company list / purchase history" | ? | BLOCKED | L201-212 |
| 17 | Fundraising | "project list / project detail" | ? | BLOCKED | L213-224 |
| 18 | Macro | "macroeconomic events / event history" | ? | BLOCKED | L225-236 |
| 19 | Analysis Charts | "chart list / chart data" | ? | BLOCKED | L237-248 |
| 20 | Feeds | `GET /news` / `GET /news/hot` | GET | BLOCKED | L249-261 |

SigLab's `SoSoValueEndpoints` dataclass at `siglab/data/sosovalue_client.py:57-61` declares three base URLs:

```python
openapi_base_url: str = "https://openapi.sosovalue.com/openapi/v1"
etf_base_url:    str = "https://api.sosovalue.xyz"
news_base_url:   str = "https://openapi.sosovalue.com"
```

Auth header: `x-soso-api-key: <key>` (set in `_single_http_attempt`, `siglab/data/sosovalue_client.py:275`).

---

## 2. URLs visited (raw list)

```
https://sosovalue.com/developer
https://m.sosovalue.com/developer
https://sosovalue-1.gitbook.io/sosovalue-api-doc                  (Introduction)
https://sosovalue-1.gitbook.io/sosovalue-api-doc/authentication.md
https://sosovalue-1.gitbook.io/sosovalue-api-doc/setting-up-your-api-key.md
https://sosovalue-1.gitbook.io/sosovalue-api-doc/query-modes.md
https://sosovalue-1.gitbook.io/sosovalue-api-doc/response-format.md
https://sosovalue-1.gitbook.io/sosovalue-api-doc/rate-limit.md
https://sosovalue-1.gitbook.io/sosovalue-api-doc/error-responses.md
https://sosovalue-1.gitbook.io/sosovalue-api-doc/endpoint-overview.md
https://sosovalue-1.gitbook.io/sosovalue-api-doc/llms.txt
https://sosovalue-1.gitbook.io/sosovalue-api-doc/1.-currency-and-pairs/currency.md
https://sosovalue-1.gitbook.io/sosovalue-api-doc/1.-currency-and-pairs/list.md
https://sosovalue-1.gitbook.io/sosovalue-api-doc/1.-currency-and-pairs/info.md
https://sosovalue-1.gitbook.io/sosovalue-api-doc/1.-currency-and-pairs/market-snapshot.md
https://sosovalue-1.gitbook.io/sosovalue-api-doc/1.-currency-and-pairs/klines.md
https://sosovalue-1.gitbook.io/sosovalue-api-doc/2.-etf/etf.md
https://sosovalue-1.gitbook.io/sosovalue-api-doc/2.-etf/summary-history.md
https://sosovalue-1.gitbook.io/sosovalue-api-doc/2.-etf/list.md
https://sosovalue-1.gitbook.io/sosovalue-api-doc/2.-etf/market-snapshot.md
https://sosovalue-1.gitbook.io/sosovalue-api-doc/2.-etf/history.md
https://sosovalue-1.gitbook.io/sosovalue-api-doc/6.-feeds/feeds.md
https://sosovalue-1.gitbook.io/sosovalue-api-doc/6.-feeds/news.md
https://sosovalue-1.gitbook.io/sosovalue-api-doc/6.-feeds/hot-news.md
https://sosovalue-1.gitbook.io/sosovalue-api-doc/6.-feeds/featured-news.md
https://sosovalue-1.gitbook.io/sosovalue-api-doc/6.-feeds/search.md
https://openapi.sosovalue.com/  (HTTP 404 — does not exist)
https://openapi.sosovalue.com/v1  (HTTP 404)
https://openapi.sosovalue.com/v2  (HTTP 404)
https://api.sosovalue.xyz/  (HTTP 401 — host responds, but undocumented)
https://github.com/SoSoValueLabs/so-so-value-python-sdk  (HTTP 404 — repo does not exist)
```

---

## 3. What the official docs actually say (verbatim, with URLs)

### 3.1 Base URL

> **Base URL**: `https://openapi.sosovalue.com/openapi/v1`
> Source: https://sosovalue-1.gitbook.io/sosovalue-api-doc

The host `openapi.sosovalue.com` exists, but the doc-suggested Swagger UIs at `/v1` and `/v2` are both 404. The GitBook is the only machine-readable documentation.

### 3.2 Authentication

> # Authentication
> All requests must include an API Key in the request Header:
>
> | Header         | Description  |
> | -------------- | ------------ |
> | x-soso-api-key | Your API Key |
>
> Source: https://sosovalue-1.gitbook.io/sosovalue-api-doc/authentication.md

### 3.3 Rate limit

> | Dimension         | Rule                       |
> | ----------------- | -------------------------- |
> | Scope             | Per API Key                |
> | Monthly quota     | 100,000 requests per month |
> | Request frequency | 20 requests per minute     |
>
> Source: https://sosovalue-1.gitbook.io/sosovalue-api-doc/rate-limit.md

Marketing page reiterates: "The SoSoValue Beta API plan has a rate limit of 20 calls/min." (https://sosovalue.com/developer FAQ)

### 3.4 Response format

> All endpoints return a unified wrapper format:
>
> ```json
> { "code": 0, "message": "success", "data": { ... } }
> ```
>
> Source: https://sosovalue-1.gitbook.io/sosovalue-api-doc/response-format.md

### 3.5 Complete official endpoint list (33 endpoints, all GET)

From `endpoint-overview.md` (https://sosovalue-1.gitbook.io/sosovalue-api-doc/endpoint-overview.md):

| Module | Official Endpoint | Method | Update Freq |
|---|---|---|---|
| Currency | `/currencies` | GET | 1 min |
| Currency | `/currencies/{id}` | GET | 5 min |
| Currency | `/currencies/{id}/market-snapshot` | GET | 30 s |
| Currency | `/currencies/{id}/token-economics` | GET | 5 min |
| Currency | `/currencies/{id}/klines` | GET | Real-time |
| Currency | `/currencies/{id}/supply` | GET | 1 min |
| Currency | `/currencies/{id}/pairs` | GET | 30 s |
| Currency | `/currencies/sector-spotlight` | GET | 1 min |
| Currency | `/currencies/{id}/fundraising` | GET | 1 min |
| ETF | `/etfs/summary-history` | GET | 1 min |
| ETF | `/etfs` | GET | 1 min |
| ETF | `/etfs/{ticker}/market-snapshot` | GET | 1 min |
| ETF | `/etfs/{ticker}/history` | GET | 1 min |
| Index | `/indices` | GET | 1 min |
| Index | `/indices/{ticker}/constituents` | GET | 1 min |
| Index | `/indices/{ticker}/market-snapshot` | GET | 30 s |
| Index | `/indices/{ticker}/klines` | GET | 1 min |
| Crypto Stocks | `/crypto-stocks` | GET | 1 min |
| Crypto Stocks | `/crypto-stocks/{ticker}/market-snapshot` | GET | 30 s |
| Crypto Stocks | `/crypto-stocks/{ticker}/market-cap` | GET | 1 min |
| Crypto Stocks | `/crypto-stocks/{ticker}/klines` | GET | Real-time |
| Crypto Stocks | `/crypto-stocks/sector` | GET | 1 min |
| Crypto Stocks | `/crypto-stocks/sector/{name}/index` | GET | 1 min |
| BTC Treasuries | `/btc-treasuries` | GET | 1 min |
| BTC Treasuries | `/btc-treasuries/{ticker}/purchase-history` | GET | 1 min |
| Feeds | `/news` | GET | Real-time |
| Feeds | `/news/hot` | GET | Real-time |
| Feeds | `/news/featured` | GET | Real-time |
| Feeds | `/news/search` | GET | (n/a) |
| Fundraising | `/fundraising/projects` | GET | 1 min |
| Fundraising | `/fundraising/projects/{id}` | GET | 1 min |
| Macro | `/macro/events` | GET | 1 min |
| Macro | `/macro/events/{event}/history` | GET | 1 min |
| Analysis | `/analyses` | GET | 1 min |
| Analysis | `/analyses/{chart_name}` | GET | 1 min |

**33 GET endpoints total. Zero POST endpoints. Zero endpoints under `/api/v1/...`. Zero endpoints under `/openapi/v2/...`. Zero endpoints under `/data/default/coin/list`. Zero endpoint with `historicalInflowChart` in its path.**

### 3.6 ETF Summary History — full schema

> # 2.1 ETF Summary History
> ```
> GET /etfs/summary-history
> ```
> **Request Parameters**
> | symbol | string | Yes | Currency symbol, e.g. BTC, ETH |
> | country_code | string | Yes | Country code, e.g. US |
> | start_date | string | No | Start date. Only the most recent 1 month is supported |
> | end_date | string | No | End date. Only the most recent 1 month is supported |
> | limit | integer | No | Number of records, default 50, max 300 |
>
> **Response Example**
> ```json
> [
>   { "date": "2024-04-12", "total_net_inflow": -55066297.0, "total_value_traded": 4706120449.0, "total_net_assets": 56216535367.0, "cum_net_inflow": 13534833596.095 }
> ]
> ```
>
> Source: https://sosovalue-1.gitbook.io/sosovalue-api-doc/2.-etf/summary-history.md

### 3.7 Currency List — full schema

> # 1.1 Currency List
> ```
> GET /currencies
> ```
> No parameters. Returns all listed currencies.
> ```json
> [
>   { "currency_id": "1673723677362319867", "symbol": "USDT", "name": "USDT" }
> ]
> ```
>
> Source: https://sosovalue-1.gitbook.io/sosovalue-api-doc/1.-currency-and-pairs/list.md

### 3.8 Featured News — full schema

> # 6.3 Featured News
> ```
> GET /news/featured
> ```
> | page | integer | Yes | Page number, >= 1 |
> | page_size | integer | Yes | Items per page, range 20-100 |
> | language | string | No | Response language; defaults to English |
> | category | array[integer] | No | Category filter |
>
> **Response Example** (note: no `code`/`message`/`data` wrapper, returns flat page object):
> ```json
> { "page": 1, "page_size": 20, "total": 115, "list": [ ... ] }
> ```
>
> Source: https://sosovalue-1.gitbook.io/sosovalue-api-doc/6.-feeds/featured-news.md

**No `currency` filter on `/news/featured`. No `/news/featured/currency` endpoint anywhere in the docs.**

### 3.9 Klines — limits

> Only daily (`1d`) klines are available. The query range is limited to the most recent 3 months.
> Source: https://sosovalue-1.gitbook.io/sosovalue-api-doc/1.-currency-and-pairs/klines.md

### 3.10 Python SDK — does not exist

Web search returned a fabricated claim: `https://github.com/SoSoValueLabs/so-so-value-python-sdk`. The repo returns HTTP 404 on GitHub. The official developer portal makes no mention of any SDK; the only path is "RESTful JSON endpoints using HTTP requests" (https://sosovalue.com/developer FAQ).

### 3.11 Doc roadmap status (from `https://sosovalue.com/developer`)

| Feature | Status on SoSoValue portal |
|---|---|
| Crypto ETF Data | **Ongoing** |
| Crypto News Feeds | **Ongoing** |
| Coins Data | Coming Soon |
| Daily AI Token Report | Coming Soon |
| Token Introduction | Coming Soon |
| Token Social Sentiment | Coming Soon |
| Real-time Coin Price | Coming Soon |
| Crypto Fundraising Data | Coming Soon |

### 3.12 Paid plans

> "SoSoValue API offers free and paid plans. The Demo API plan is accessible to all SoSoValue users at zero cost. The paid plan will be launched soon."
> Source: https://sosovalue.com/developer FAQ

There is **no public Pro/Enterprise tier with documented rate limits** at the time of writing. The "20 calls/min" applies to the Beta/Demo plan.

---

## 4. Point-by-point mismatch table

Legend: ✅ = matches, ❌ = contradicts official docs, ⚠ = partially wrong, 🔍 = unverifiable from official docs.

### 4.1 IMPLEMENTED endpoints (the ones SigLab claims actually work)

| # | SigLab claim | Official reality | Verdict |
|---|---|---|---|
| IMP-1 | `POST /openapi/v1/data/default/coin/list` via `https://openapi.sosovalue.com/openapi/v1` (`sosovalue_client.py:148-158`, `sosovalue_capabilities.py:21-32`) | Official = `GET /currencies`, no params, returns a flat array `[{"currency_id": "...", "symbol": "...", "name": "..."}]` (`list.md`). | ❌ **Method wrong (POST vs GET). Path does not exist in official docs. Response shape wrong (SigLab expects `{code, message, data: {list: [...]}}`; official returns a flat array).** |
| IMP-2 | `POST /openapi/v2/etf/historicalInflowChart` via `https://api.sosovalue.xyz` with JSON body `{"type": "us-btc-spot"}` (`sosovalue_client.py:132-144`, `sosovalue_capabilities.py:105-116`) | Official = `GET /etfs/summary-history?symbol=BTC&country_code=US` under `https://openapi.sosovalue.com/openapi/v1/...` (`summary-history.md`). Required fields are snake_case and as **query string**, not JSON body. | ❌ **Method wrong (POST vs GET). Base URL wrong (`api.sosovalue.xyz` vs `openapi.sosovalue.com`). Path `/openapi/v2/etf/historicalInflowChart` does not exist. Body schema wrong. Returns a flat array, not `{code, message, data: {list: [...]}}` envelope.** |

### 4.2 BLOCKED endpoints (SigLab admits no wrapper)

| # | SigLab claim | Official reality | Verdict |
|---|---|---|---|
| BLK-2 | `GET /currencies/{id}/market-snapshot` (`sosovalue_capabilities.py:33-44`) | Official = `GET /currencies/{currency_id}/market-snapshot` under `/openapi/v1/...` (`market-snapshot.md`). Response is a flat object, NOT wrapped. | ✅ Path matches, ⚠ case param is `currency_id` (snake_case) not `id`. No wrapper in response. |
| BLK-3 | `GET /currencies/{id}/klines` (`sosovalue_capabilities.py:45-56`) | Official = `GET /currencies/{currency_id}/klines?interval=1d` with `start_time`/`end_time`/`limit`. **Only `1d` interval is supported; query range capped at most recent 3 months.** (`klines.md`) | ⚠ Path correct, but **no `interval` enum supports anything except `1d`**. SigLab truth-table row says nothing about the 1d-only restriction. |
| BLK-4 | `GET /currencies/{id}` (`sosovalue_capabilities.py:57-68`) | Official = `GET /currencies/{currency_id}` (`info.md`) | ✅ Path matches. |
| BLK-5 | `GET /currencies/{id}/{token-economics,supply,pairs,sector-spotlight,fundraising}` (`sosovalue_capabilities.py:69-80`) | Official = `/currencies/{id}/token-economics`, `/currencies/{id}/supply`, `/currencies/{id}/pairs`, **`/currencies/sector-spotlight` (no `{id}` segment!)**, `/currencies/{id}/fundraising` (`currency.md`) | ❌ `/currencies/sector-spotlight` is **not** an `/{id}/...` subpath. SigLab grouping is wrong. |
| BLK-6 | `GET /api/v1/news/featured` (`sosovalue_capabilities.py:81-92`) | Official = `GET /news/featured` under `/openapi/v1/...` (`featured-news.md`). | ❌ **Path prefix `/api/v1/news/featured` does not exist in the official API.** SigLab's `news_base_url = "https://openapi.sosovalue.com"` (stripped of `/openapi/v1`) plus `/api/v1/news/featured` builds a URL that the official server has no record of. |
| BLK-7 | `GET /api/v1/news/featured/currency` (`sosovalue_capabilities.py:93-104`) | **Does not exist anywhere in official docs.** Featured news takes no `currency` filter; only `category` (array) + `language`. Currency filter exists on `/news` only, not `/news/featured`. (`featured-news.md`, `news.md`) | ❌ **Phantom endpoint. SigLab's `featured_news_by_currency_pages` calls a URL the official server does not expose.** |
| BLK-9 | "etf current metrics / daily ETF data" (`sosovalue_capabilities.py:117-128`) | Official daily ETF data = `GET /etfs/summary-history` (already counted as BLK-11). Per-ticker daily = `GET /etfs/{ticker}/history` (BLK-13). "current metrics" = `GET /etfs/{ticker}/market-snapshot` (BLK-12). | ⚠ Truth-table row is fuzzy: it bundles three distinct official endpoints into one cell. |
| BLK-10 | `GET /etfs/list` (`sosovalue_capabilities.py:129-140`) | Official = `GET /etfs` (not `/etfs/list`). (`list.md`) | ❌ **Path is wrong. Official is `/etfs` with no `/list` suffix.** |
| BLK-11 | `GET /etfs/summary-history` (`sosovalue_capabilities.py:141-152`) | Official = `GET /etfs/summary-history?symbol=BTC&country_code=US` (required: symbol, country_code). (`summary-history.md`) | ✅ Path correct. ⚠ SigLab truth table does not record that `symbol` and `country_code` are **required**. |
| BLK-12 | `GET /etfs/{ticker}/market-snapshot` (`sosovalue_capabilities.py:153-164`) | Official = `GET /etfs/{ticker}/market-snapshot` (`market-snapshot.md`) | ✅ Path matches. |
| BLK-13 | `GET /etfs/{ticker}/history` (`sosovalue_capabilities.py:165-176`) | Official = `GET /etfs/{ticker}/history?start_date=...&end_date=...&limit=...`. Most recent 1 month only. (`history.md`) | ✅ Path matches. ⚠ 1-month range cap unrecorded. |
| BLK-14 | "index data / constituents / market snapshot / klines" (`sosovalue_capabilities.py:177-188`) | Official = `/indices`, `/indices/{ticker}/constituents`, `/indices/{ticker}/market-snapshot`, `/indices/{ticker}/klines` (`3.-sosovalue-index/index.md`) | ⚠ Truth table bundles four endpoints into one cell. Singular "Index" entry. |
| BLK-15 | "stock list / market snapshot / market cap / klines / sectors" (`sosovalue_capabilities.py:189-200`) | Official = `/crypto-stocks`, `/crypto-stocks/{ticker}/market-snapshot`, `/crypto-stocks/{ticker}/market-cap`, `/crypto-stocks/{ticker}/klines`, `/crypto-stocks/sector`, `/crypto-stocks/sector/{name}/index` | ⚠ Bundles 5–6 endpoints into one cell. |
| BLK-16 | "BTC treasury company list / purchase history" (`sosovalue_capabilities.py:201-212`) | Official = `/btc-treasuries`, `/btc-treasuries/{ticker}/purchase-history` | ✅ Two endpoints, two cells merged. |
| BLK-17 | "project list / project detail" (`sosovalue_capabilities.py:213-224`) | Official = `/fundraising/projects`, `/fundraising/projects/{id}` (`7.-fundraising/fundraising.md`) | ✅ Two endpoints, one cell. |
| BLK-18 | "macroeconomic events / event history" (`sosovalue_capabilities.py:225-236`) | Official = `/macro/events`, `/macro/events/{event}/history` (`8.-macro/macro.md`) | ✅ Two endpoints, one cell. |
| BLK-19 | "chart list / chart data" (`sosovalue_capabilities.py:237-248`) | Official = `/analyses`, `/analyses/{chart_name}` (`9.-analysis-charts/analysis.md`) | ✅ Two endpoints, one cell. |
| BLK-20 | `GET /news / GET /news/hot` (`sosovalue_capabilities.py:249-261`) | Official = `GET /news` and `GET /news/hot` under `/openapi/v1/...` (`news.md`, `hot-news.md`) | ✅ Paths match. |

### 4.3 Auth header

| SigLab claim | Official reality | Verdict |
|---|---|---|
| `x-soso-api-key: <key>` (`sosovalue_client.py:275`) | `x-soso-api-key: Your API Key` (`authentication.md`) | ✅ Matches. |

### 4.4 Rate limit

| SigLab claim | Official reality | Verdict |
|---|---|---|
| `conservative_rate_limit_per_minute = 20` default (`sosovalue_client.py:102`); "20 calls/min, 100k/month" cited at `sosovalue_client.py:389` | "20 requests per minute" + "100,000 requests per month" (`rate-limit.md`); marketing page confirms "20 calls/min" | ✅ Matches the Beta/Demo plan. ⚠ No mention of paid tier — `metrics_snapshot` cites `https://m.sosovalue.com/developer` which is correct, but the doc explicitly says the paid plan is "coming soon". |

### 4.5 Base URL

| SigLab claim | Official reality | Verdict |
|---|---|---|
| `https://openapi.sosovalue.com/openapi/v1` (`sosovalue_client.py:59`) | `https://openapi.sosovalue.com/openapi/v1` (Introduction) | ✅ Matches for "currency list". |
| `https://api.sosovalue.xyz` for ETF (`sosovalue_client.py:60`) | No mention of `api.sosovalue.xyz` in the GitBook. The doc's only base URL is `openapi.sosovalue.com/openapi/v1`. | ❌ **Wrong host. SigLab's IMPLEMENTED ETF endpoint targets a domain the official documentation never references.** |
| `https://openapi.sosovalue.com` (no `/openapi/v1`) for news (`sosovalue_client.py:61`) | Base URL is `https://openapi.sosovalue.com/openapi/v1` (Introduction) | ❌ **Wrong prefix; SigLab strips `/openapi/v1` from the news base URL, then prepends `/api/v1/news/...` from the path, so the final URL doesn't exist.** |

### 4.6 SDK availability

| SigLab claim | Official reality | Verdict |
|---|---|---|
| (None — SigLab ships its own `httpx`-based wrapper at `siglab/data/sosovalue_client.py`) | GitBook docs make no mention of any official SDK. The dev portal FAQ answers "How do I connect" with "RESTful JSON endpoints using HTTP requests." The web-search-fabricated repo `github.com/SoSoValueLabs/so-so-value-python-sdk` returns HTTP 404. | ✅ SigLab was right to write its own client; no official SDK exists. (No penalty.) |

### 4.7 Response envelope expectations

| SigLab expectation (`sosovalue_client.py:307-320`) | Official reality | Verdict |
|---|---|---|
| Every response has `code` (0 or error) and `data` field | Unified wrapper exists, **except** `/news/featured` and `/news/hot` return flat `{page, page_size, total, list}` and `/currencies` returns a flat array (`response-format.md`, `featured-news.md`, `hot-news.md`, `list.md`). | ❌ `_validate_payload` will **reject** the response from `/currencies` (no `code` field) — but `_rows_from_data` tolerates a list directly. **The wrapper is half-applied and will silently fail or crash depending on which endpoint the code touches.** |

### 4.8 Param casing (SigLab client code)

| SigLab `featured_news_pages` params (`sosovalue_client.py:170-174`) | Official spec | Verdict |
|---|---|---|
| `pageNum`, `pageSize`, `categoryList` (camelCase) | `page`, `page_size`, `category` (snake_case). Note `category` is an **integer array**, not a comma-separated string. (`featured-news.md`) | ❌ **Casing wrong. Format wrong.** A call like `?pageNum=1&pageSize=10&categoryList=2` would either be ignored or rejected by the server. |

---

## 5. Brutal verdict

**Score: 2 / 10.**

Reasoning:
- The auth header name and the 20-rpm rate limit are correct, which is worth something.
- The "BASE URL = https://openapi.sosovalue.com/openapi/v1" claim exists in the code but is **only used for one of the two IMPLEMENTED endpoints** (and even that endpoint is calling a POST URL that the official docs do not expose).
- The other "IMPLEMENTED" endpoint (`/openapi/v2/etf/historicalInflowChart`) targets `api.sosovalue.xyz` — a host the official documentation does not list. Either SigLab is hitting a third-party scraper/reverse-proxy, or it is fabricating endpoint paths. Either way, it is not "the official SoSoValue API."
- Two endpoints SigLab calls (`/api/v1/news/featured` and `/api/v1/news/featured/currency`) **do not exist** in the official documentation. The first is just a path mismatch — the official version is `/news/featured`. The second is a phantom — there is no `currency`-filtered variant of featured news in the official API at all.
- Two IMPLEMENTED endpoints use POST when the official equivalents are GET. Both expect JSON bodies when the official expects query strings.
- The truth table at `sosovalue_capabilities.py:20-261` is internally inconsistent: it claims `GET /etfs/list` is BLOCKED, but the official endpoint is `/etfs` (no `/list`); the row for "current ETF metrics" is hand-wavy prose.
- The `featured_news_pages` method sends camelCase parameter names that the official API never documents; no `currency_id` filter exists on `/news/featured`.
- The `_validate_payload` wrapper-envelope check is incompatible with the official `/currencies` response shape (flat array, no `code`).
- SigLab's truth table says "available in API docs but no wrapper yet" for 18 of 20 rows. That phrase is technically true for the 8 rows where the official path matches a SigLab path with cosmetic differences, false for 4 rows where the path is wrong or doesn't exist, and unverified for the rest. Either way, 18/20 = 90% unimplemented, with multiple path-and-method errors in the "implemented" 10%.
- The whole thing is a toy that wraps two or three reverse-engineered scrapes against an API whose own documentation was published April 2025 and is still in "Beta" / "Ongoing" status per the developer's own roadmap.

This is **not** an integration. It is a wrapper around two calls (and even those are mostly mis-named) glued to a truth table that overstates coverage by an order of magnitude.

---

## 6. Top 5 worst gaps (with file:line references)

1. **`etf_base_url = "https://api.sosovalue.xyz"`** — `siglab/data/sosovalue_client.py:60`. The official documentation never references this host. Either the docs are incomplete (possible — paid plan is "coming soon") or SigLab is hitting a third-party mirror. The GitBook base URL is `https://openapi.sosovalue.com/openapi/v1` (Introduction). This means the IMPLEMENTED ETF wrapper at `siglab/data/sosovalue_client.py:132-144` and the BLOCKED ETF rows 9-13 (`sosovalue_capabilities.py:117-176`) are all pointed at the wrong server. The IMPLEMENTED ETF call to `POST /openapi/v2/etf/historicalInflowChart` is calling a path that the official docs do not document.

2. **Phantom `GET /api/v1/news/featured/currency` endpoint** — `siglab/data/sosovalue_capabilities.py:93-104` truth-table row, called from `siglab/data/sosovalue_client.py:189-220` (`featured_news_by_currency_pages`). Official `/news/featured` does not accept a `currency` filter. There is no `/news/featured/currency` path anywhere in the GitBook. This is a fabricated endpoint.

3. **Wrong method + wrong path on the "IMPLEMENTED" currency list** — `siglab/data/sosovalue_client.py:147-158` issues `POST /data/default/coin/list` with `json_body={}`. Official is `GET /currencies` with no body, returning a flat array (`list.md`). SigLab's `_validate_payload` (`sosovalue_client.py:307-320`) would also reject the official response because it lacks a `code` field.

4. **Path mismatches in the BLOCKED truth table** — `siglab/data/sosovalue_capabilities.py:129-140` lists `GET /etfs/list` as BLOCKED, but the official endpoint is `GET /etfs` (`list.md`). SigLab's `news_base_url = "https://openapi.sosovalue.com"` (`sosovalue_client.py:61`) drops the required `/openapi/v1` prefix, then re-adds `/api/v1/news/...` to the path — building URLs the official server does not expose. The wrapper at `siglab/data/sosovalue_client.py:159-187` (`featured_news_pages`) is therefore pointed at a non-existent host+path combination.

5. **Camelcase parameter names everywhere in news code** — `siglab/data/sosovalue_client.py:170-174, 200-207` send `pageNum`, `pageSize`, `categoryList`, `currencyId`. Official spec for `/news/featured` (`featured-news.md`) is `page`, `page_size`, `category` (integer array), `language` — no `currencyId`, no `pageNum`. The endpoint also requires `page` and `page_size`; SigLab validates `page_size` between 1-100 (`sosovalue_client.py:430-431`) but never sends the correct key. Any call to this code path against the real server would 400.

---

## 7. What's missing for production (no fluff)

1. **No working base URL on ETF.** SigLab must either switch `etf_base_url` to `https://openapi.sosovalue.com/openapi/v1` and re-implement `etf_historical_inflow` as `GET /etfs/summary-history?symbol=BTC&country_code=US`, or document that `api.sosovalue.xyz` is a known third-party mirror and the `openapi.sosovalue.com` route is unverified. Currently neither is true.

2. **No working news endpoints.** Both `featured_news_pages` and `featured_news_by_currency_pages` need to be rewritten to hit `https://openapi.sosovalue.com/openapi/v1/news/featured?page=1&page_size=20&language=en&category=...`. The currency-filtered variant must be removed entirely (it does not exist).

3. **No working currency list.** `listed_currencies` must become `GET /currencies` (no body) and the client must accept a flat array response (the envelope check must be opt-in per endpoint, not global).

4. **No compliance with official error codes.** SigLab's error handling reads `code`/`message`/`data` (`_validate_payload` L307-320), but the official error code reference (`error-responses.md`) uses 400001/400002/400003/400101/400102/400301/400401/400402/402901/500001/500301, with HTTP status 400/401/403/404/429/500/503). SigLab maps everything to its own exception classes (`SoSoValueAuthError`, `SoSoValueRateLimitError`, etc.) without ever reading the official numeric code.

5. **No support for the official 100,000-requests/month quota.** SigLab enforces 20/min but ignores the monthly cap entirely. A long-running backtest could rack up 100k calls and start getting 429s that look like generic rate limits.

6. **No support for the 1-month ETF history window and 3-month klines window.** These are hard limits in the official docs (`summary-history.md`, `klines.md`); SigLab's truth table doesn't even mention them.

7. **No SDK existence check.** SigLab ships its own client when no official SDK exists — that's fine, but the `metrics_snapshot` claims a `source` of `https://m.sosovalue.com/developer` (L389) without citing any version of the doc, and no version pin exists anywhere in the code. The official docs are version-less GitBook pages; the moment SoSoValue renames `/etfs/summary-history` (which is the kind of thing that happens between "Beta" and "Paid"), every wrapper breaks silently.

8. **No concurrency / multi-process rate coordination.** SigLab's `_acquire_rate_slot` is a process-local asyncio lock (L440-454). The official rate limit is "Per API Key" (scope: API key, not per process). Two SigLab processes sharing one key will each burn 20/min and collectively 2× the limit.

---

## 8. Bottom line

SigLab's SoSoValue "integration" is a thin, mostly-broken wrapper around two POST calls to a third-party subdomain, plus four BLOCKED entries that the truth table advertises as "available in API docs." The truth table itself mixes fuzzy prose with concrete paths, occasionally getting the paths wrong. The two IMPLEMENTED endpoints use methods, paths, and body shapes that do not match the official documentation. The BLOCKED ones are not implemented for non-trivial reasons: the path is wrong, the param casing is wrong, the host is wrong, or the endpoint does not exist. The auth header and the 20-rpm rate limit are the only two things that line up with reality.

**Score: 2/10. Recommendation: do not ship. Re-implement from the official GitBook (`https://sosovalue-1.gitbook.io/sosovalue-api-doc`) before any external claim about SoSoValue coverage.**
