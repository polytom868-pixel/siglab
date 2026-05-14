# SoSoValue Coverage Gap Report

Generated: 2026-05-14

This report is strict. A product page is not a callable API endpoint. A documented module is not counted as implemented unless SigLab has a wrapper, parser/normalizer, tests, and a usable operator path.

## Current Truth

| Module | Official callable endpoint verified | SigLab wrapper | Live validation | Status | Exact gap |
| --- | --- | --- | --- | --- | --- |
| Currency & Pairs | Listed-currency path verified from official GitBook page | `SoSoValueClient.listed_currencies` | yes via evidence-build currency resolution | PARTIAL | Currency info, market snapshot, trading pairs, klines, supply, sector/spotlight endpoints are not verified callable. |
| ETF | Historical inflow chart and current metrics | `etf_historical_inflow`, `etf_current_metrics` | historical inflow yes; current metrics yes | PARTIAL | ETF list/market snapshot/historical data beyond verified v2 paths not implemented unless official pages are pinned. Live v2 current metrics use `totalTokenHoldings`; V1 docs describe `totalBtcHoldings`. |
| Feeds | Featured news and featured news by currency verified from official GitBook pages | `featured_news`, `featured_news_pages`, `featured_news_by_currency`, `featured_news_by_currency_pages` | yes | PARTIAL | Hot news, search, and any other feed endpoints remain unverified/missing. |
| SoSoValue Index | Product/index docs verified; callable OpenAPI endpoint not verified | none | no | BLOCKED | Need official callable endpoint or official contract/data source before integration. |
| Crypto Stocks | No callable endpoint verified | none | no | BLOCKED | Need official endpoint docs; do not scrape or invent paths. |
| BTC Treasuries | No callable endpoint verified | none | no | BLOCKED | Need official endpoint docs; high research value but not source-backed yet. |
| Fundraising | Developer roadmap says Crypto Fundraising Data is Coming Soon | none | no | EXTERNALLY_BLOCKED | Not callable until SoSoValue ships official endpoint docs. |
| Macro | Product/category docs and feed categories suggest macro context; callable event endpoint not verified | none | no | BLOCKED | Need official event/history endpoint docs. |
| Analysis Charts | Product/dashboard docs verified; callable endpoint not verified | none | no | BLOCKED | Need official chart endpoint docs or official export API. |

## What Is Implemented Correctly

- All SoSoValue requests use `x-soso-api-key`.
- Missing API key fails as `SoSoValueConfigError`.
- 401/403/429/5xx/transport/malformed/empty-data paths are classified.
- ETF historical inflow validates required fields and business envelope.
- ETF current metrics now validate required aggregate/list shape and the live v2 `totalTokenHoldings` field.
- News pagination enforces page size `1..100`.
- Evidence build deduplicates records and writes summary artifacts.

## What Is Still Weak

- The official GitBook API Document navigation currently exposes five callable endpoint pages: listed currencies, featured news, featured news by currency, ETF historical inflow chart, and current ETF data metrics.
- The listed-currencies page says it is a prerequisite before WebSocket feeds, but no official SoSoValue WebSocket API schema/page was verified; WebSocket support must stay unimplemented/unverified.
- Deeper product modules remain inaccessible as callable endpoint pages in the public API docs.
- SoSoValue developer page currently confirms Crypto ETF Data and Crypto News Feeds as active/ongoing surfaces while listing Coins Data, Daily AI Token Report, Token Introduction, Token Social Sentiment, Real-time Coin Price, and Crypto Fundraising Data as Coming Soon.
- The developer page states Beta API plan rate limit is `20 calls/min`; SigLab should treat 20/min as the conservative SoSoValue budget until a higher plan is configured.
- The capability map still has high-value blocked modules. That is a product capability gap, not just documentation debt.
- Live SoSoValue smoke in `unittest` skips if `SOSOVALUE_API_KEY` env is absent even though local config may contain a key. That keeps CI safe, but it means full live proof is operator-triggered, not automatic.
- Cross-module intelligence is still ETF/news/SoDEX-stream heavy. Macro/Index/Treasury/Fundraising are not real inputs yet.

## Highest-Value Next Verified Additions

1. Macro events/history if official callable docs are found.
2. SoSoValue Index constituents/weights if official callable docs or official chain/data source are found.
3. BTC Treasury and Crypto Stocks if official callable docs are found.
4. Feed search/hot-news endpoints if official callable docs are found.

## Forbidden Claims

- Do not claim “full SoSoValue integration.”
- Do not claim Index/Macro/Treasury/Fundraising wrappers exist.
- Do not count SSI product methodology as API integration.
- Do not infer endpoints from product UI URLs.
