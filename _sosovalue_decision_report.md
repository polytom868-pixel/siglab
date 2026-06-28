# SoSoValue API Decision Engine Research

## 1. Endpoint Map with Decision Relevance

### Currently Working Endpoints

| Endpoint | Schema | Decision Value | Status in Client |
|---|---|---|---|
| `POST /openapi/v2/etf/historicalInflowChart` | `{date, totalNetInflow, totalValueTraded, totalNetAssets, cumNetInflow}[]` (300 days) | **HIGH** — net inflow/outflow is the primary market direction signal | `etf_historical_inflow()` ✅ |
| `GET /openapi/v1/etfs/{ticker}/market-snapshot` | `{date, ticker, sponsor_fee, net_inflow, cum_inflow, net_assets, mkt_price, prem_dsc, value_traded, volume}` | **HIGH** — per-ETF breakdown, price oracle via `mkt_price`, volume for liquidity assessment | NOT wrapped (was `etf_market_snapshot()` — deleted) ❌ |
| `GET /openapi/v1/etfs?symbol=BTC&country_code=US` | `{ticker, name, exchange}[]` — 13 BTC ETFs, 10 ETH ETFs | **MEDIUM** — sector mapping, universe reference | NOT wrapped (was `etf_list()` — deleted) ❌ |
| `GET /api/v1/news/search?keyword=BTC&pageSize=2` | `{page, page_size, total, list[{id, title, content, release_time, matched_currencies, tags}]}` | **HIGH** — keyword-directed news with sentiment signal, currency tagging | NOT wrapped (`news_search()` — deleted, but tool STILL CALLS IT) ❌🔴 |
| `GET /openapi/v1/news/featured/currency?pageNum=1&pageSize=2&currencyId=1` | `{pageNum, pageSize, totalPage, total, list[{title, multilanguageContent, ...}], lastSortValues}` | **MEDIUM** — curated news feed per currency | `featured_news_by_currency()` ✅ |
| `GET /openapi/v1/currencies` | `{currency_id, symbol, name}[]` (1277 currencies) | **LOW** — ID lookup, only needed for `currency_market_snapshot()` | NOT wrapped (was `listed_currencies()` — deleted) |

### Dead/Broken Endpoints

| Endpoint | Failure | Notes |
|---|---|---|
| `GET /openapi/v1/currencies/{id}/market-snapshot` | 500 | `currency_market_snapshot()` calls this — broken, no alternative found |
| `GET /openapi/v1/etfs/summary-history` | 400/404 | Parameter format unknown |
| `GET /openapi/v1/etfs/historical-inflow` | 404 | Only v2 POST variant works |

### Current Degradation

The SoSoValue integration has **3 known bugs** introduced by cleanup:
1. `search_crypto_news` tool calls `client.news_search()` — method was deleted, will raise `AttributeError`
2. `currency_market_snapshot()` hits 500 on `/currencies/{id}/market-snapshot` — completely dead
3. No client method wraps the working `market-snapshot` and `etfs?symbol=BTC` endpoints

## 2. Decision Pipeline Mapping

### Current Flow

```
LLM tools (text summaries)
        ↓
    [no structured evidence conversion]
        ↓
evidence_records (built manually in spec JSON)
        ↓
evidence_to_decision() → TradeSignal {direction, symbol, confidence, size}
        ↓
risk_check() → RiskReport {passed, reasons, composite_score}
        ↓
position_to_paper() → paper order
```

### How SoSoValue Data Maps to Decisions

**ETF Inflows → Market Direction Signal** (highest weight)
- `totalNetInflow` sign → BUY (positive) or SELL (negative) direction
- `cumNetInflow` cumulative → trend strength
- `|inflow| / net_assets` → signal conviction as confidence 0.5–0.95
- Aggregate across BTC + ETH types → broad market tilt

**Per-ETF Snapshots → Sector Rotation / Concentration Signal**
- Individual `mkt_price` per ETF → price oracle (fallback if SoDEX fails)
- `value_traded / net_assets` → turnover rate (active management signal)
- `volume` → liquidity check for sizing
- Compare BTC ETF vs ETH ETF inflows → rotation signal

**News → Sentiment / Volatility Adjustment**
- `matched_currencies` → which asset is affected
- Content → sentiment classification (supports BUY/SELL/NEUTRAL)
- Confidence from `_news_relevance_score()` (0.75 base, higher with currency match)
- Volume of news → volatility adjustment factor

### What Should Be Wired

**Direct feature extraction** (no LLM intermediary needed):
```python
etf_inflow_evidence(api_rows) → EvidenceRecord[]
  # relation="total_net_inflow", entity="us-btc-spot", confidence=decayed_freshness

per_etf_snapshot_evidence(snapshots) → EvidenceRecord[]
  # relation="etf_price", entity="IBIT", value=33.85
  # relation="etf_inflow", entity="IBIT", value=-444505600

news_evidence(news_rows) → EvidenceRecord[]
  # relation="news_mention", entity="BTC", confidence=0.75
  # matched_currencies → entity mapping
```

**Operator pipeline integration**:
```
SoSoValue API
  → etf_inflow_evidence() → evidence_records[0..N]
  → evidence_to_decision() → consensus TradeSignal
  → risk_check() using volatility from per-ETF data
  → position sizing adjusted by news sentiment volume
```

## 3. Minimum Viable Integration

### What MUST Be Fixed (blockers)

1. **Add `news_search()` back** — `search_crypto_news` tool is completely broken without it
2. **Fix currency_market_snapshot() or remove it** — currently misleading (returns 500)

### What Should Be Re-Added (not deleted)

| Method | Endpoint | Priority | Why |
|---|---|---|---|
| `etf_market_snapshot(ticker)` | `GET /openapi/v1/etfs/{ticker}/market-snapshot` | HIGH | Per-ETF price oracle + inflow breakdown. IBIT alone shows $445M net outflow on 2026-06-26 — that's a critical signal you can't get from aggregate |
| `etf_list(symbol, country_code)` | `GET /openapi/v1/etfs?symbol=X&country_code=XX` | MEDIUM | ETF universe for sector rotation. Required for multi-asset signals |
| `news_search(keyword, page_size)` | `GET /api/v1/news/search?keyword=X&pageSize=N` | HIGH | Keyword-directed news with `matched_currencies` entity mapping — directly feeds `news_evidence()` |
| ✓ `etf_historical_inflow()` survives | `POST /openapi/v2/etf/historicalInflowChart` | — | Already wired. Aggregate inflow + cum inflow 300 days. Best signal there is |

### What Should Stay Deleted

| Method | Reason |
|---|---|
| `currency_klines` | No working endpoint found |
| `currency_market_snapshot` | `/currencies/{id}/market-snapshot` returns 500 |
| `etf_summary_history` | No working URL pattern found |
| `featured_news_pages` | Pagination wrapper, `featured_news_by_currency` covers it |
| `listed_currencies` | Only needed for broken currency_market_snapshot |

### How to Wire Into Operator Pipeline

The missing link is a **SoSoValue evidence adapter** in `operator/pipeline.py`:

```
SoSoValueClient
    ↓ structured extraction (new SoSoValueEvidenceAdapter)
etf_inflow_evidence() + news_evidence() + per_etf_snapshot_evidence()
    ↓
evidence_records[]
    ↓
evidence_to_decision()    ← already works, uses relation/value/confidence fields
    ↓
TradeSignal
    ↓
risk_check()              ← needs SoSoValue volatility from per-ETF data
    ↓
Position
```

**Specific wiring changes needed:**
1. Re-add 3 methods to `SoSoValueClient` (news_search, etf_market_snapshot, etf_list)
2. Add `async def sovalue_evidence_bundle()` that calls all 3 in parallel and converts to `EvidenceRecord[]`
3. Call it from `pipeline.run_once()` before `evidence_to_decision()`
4. Feed per-ETF `mkt_price` as `market_data["price"]` fallback
5. Compute vol from `value_traded / net_assets` for position sizing

## 4. Signal Quality Validation

The strongest signal is **aggregate ETF net inflow**:
- 2026-06-26: BTC ETFs net outflow -$444M (IBIT alone -$444.5M)
- ETH ETFs net outflow -$12.8M
- This is a clear SELL signal across a single day

Per-ETF data adds nuance:
- IBIT has $60.7B cum inflow vs $44.4B net assets (76% still in the fund)
- GBTC still cumulatively negative (-$27.1B) despite positive days
- FBTC has 0% sponsor fee (competitive edge)

News search provides:
- Direct currency tagging through `matched_currencies[]` 
- Multi-currency news (e.g. "BTC" news also tags BNB if Binance mentioned)
- Cross-reference near ETF flow dates for causal linking
