# SigLab Ultimate Deep Audit Report

**4 agents: API schema, data flow, LLM autonomy, dashboard minimalism**
**Date: 2026-06-27 | Final state: 36 files, ~14,390 lines, 555 tests pass**

---

## 1. SoSoValue API Schema Built

**File: `sosovalue_api_schema.json` (31KB, 15 endpoints tested)**

### Live Working Endpoints (7)

| Endpoint | Used By | Response Fields |
|----------|---------|----------------|
| `POST /openapi/v1/data/default/coin/list` | `listed_currencies()` | `currencyName, fullName, currencyId` |
| `POST /openapi/v2/etf/historicalInflowChart` | ✅ evidence pipeline | `date, totalNetInflow, totalValueTraded, totalNetAssets, cumNetInflow` |
| `POST /openapi/v2/etf/currentEtfDataMetrics` | ❌ REMOVED (DEPRECATED) | Per-fund ETF granularity (IBIT vs FBTC) |
| `GET /api/v1/news/featured` | ❌ no public method | Paginated news list |
| `GET /api/v1/news/featured/currency` | ✅ evidence pipeline | Full article metadata |
| `GET /openapi/v1/currencies/{id}/market-snapshot` | ❌ **NOT IMPLEMENTED** | **Free live price! Market cap, ATH, cycle low** |
| `GET /openapi/v1/currencies/{id}/klines` | ❌ **NOT IMPLEMENTED** | Spot market OHLCV |

### Key Gap: `currency_market_snapshot()`
SigLab has **zero price fallback when SoDEX is down**. SoSoValue offers a free `GET /openapi/v1/currencies/{id}/market-snapshot` that gives price, market cap, ATH, cycle low — but our client doesn't use it. **Adding this would give SigLab an independent price source.**

### Dead Endpoints (6)
All 404: coin ohlcv, coin detail, coin rank, ETF latestInflow, ETF latestInflowData, BTC treasuries.

---

## 2. Data Flow: 10-Stage Trace

```
SoSoValue API → SoSoValueClient.request() → MarketDataProvider (ParquetLake cache)
→ EvidenceStore (dedup by SHA-256, append to JSONL)
→ build_market_report() (read JSONL, filter, compute signal)
→ runs/market_report.json
→ DashboardState._soa() → GET /api/ops → ops.js renders
```

**No data lost at any stage.** All field names match. Null handling is correct. Evidence deduplication works via SHA-256 evidence_id.

---

## 3. LLM Autonomy: CRITICAL FAILURES

| Feature | Status | Detail |
|---------|--------|--------|
| `complete_text()` | ✅ **WORKS** | Returns valid responses from OpenModel AI |
| `complete_json()` | ❌ **BROKEN by default** | `json_mode=False` → returns empty string. **Fix: default to `json_mode=True`** |
| `complete_json_with_tools()` | ❌ **BROKEN** | Anthropic SDK sends wrong format for OpenModel AI API. Can't do tool calling. |
| `ClaudeTool` instances | ❌ **ZERO exist** | Tool framework is 100% dead code — 0 tool instances across 37 files |

**The LLM has ZERO tools to call.** It can only do text completion. No API access, no research capability, no autonomy.

---

## 4. Production Readiness: 6.5/10

| Criterion | Status | Score |
|-----------|--------|:-----:|
| Headless operation | ✅ PASS | CLI runs without GUI |
| Graceful degradation | ⚠️ PARTIAL | No circuit breakers, no fallback data |
| Error logging | ⚠️ PARTIAL | No root logging config. No structured logging |
| Env var config | ✅ PASS | .env + .siglab-provider.env |
| Test coverage | ❌ **46%** | cli/ 0-26%, compile.py 14%, feeds.py 37% |
| Startup time | ✅ PASS | 449ms cold import |
| Memory | ✅ ADEQUATE | 73 MB RSS |

---

## 5. Dashboard Minimalism: 6/10

| Dimension | Score | Issues |
|-----------|:-----:|--------|
| Typography | 8/10 | Inter + JetBrains Mono, good pairing |
| Color scheme | 7/10 | 28 CSS colors, dark theme with #4ade80 accent |
| Information density | 4/10 | Too much data per card, no progressive disclosure |
| Brand clarity | 5/10 | Logo exists but value prop not obvious in 3 seconds |
| Efficiency | 6/10 | Ops board has 7 panels — could be 3 |

---

## 6. Hackathon Demo Script (3 minutes)

```
1. "SigLab is a crypto research-to-action platform."
2. python3 -m siglab demo run (5s)
   → "Gathers real ETF flows from SoSoValue, market data from SoDEX"
   → Shows: 1,102 evidence rows in 5 seconds
3. python3 -m siglab market-report
   → "BTC ETF outflow -$444M, bid $60,078 / ask $60,079"
   → Status: READY_FOR_OPERATOR_REVIEW
4. Open dashboard → shows experiments + ops board
5. python3 -m siglab operator
   → "Evidence → decision pipeline"
   → Operator evaluates risk, position size, generates signal
```

---

## 7. Critical Fixes Needed

| Priority | Fix | Impact |
|:--------:|-----|--------|
| 🔴 | Fix `complete_json()` default `json_mode=True` | LLM integration works |
| 🔴 | Add `currency_market_snapshot()` wrapper | Free price fallback |
| 🟡 | Add root logging config | Errors visible by default |
| 🟡 | Wire tool calling (switch to OpenAI SDK) | LLM autonomy possible |
| 🟢 | Add 6 missing SoSoValue endpoint wrappers | Full API coverage |
| 🟢 | Remove dead tool framework or implement it | Clean up dead code |
