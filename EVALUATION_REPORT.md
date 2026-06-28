# SigLab 5-Agent Evaluation Report

**Reference research, LLM tools, evidence quality, architecture, benchmarks**
**Date: 2026-06-27 | Final: 36 files, ~14,250 lines, 564 tests pass**

---

## 1. Reference Architecture Research (EVAL-Ref)

**Projects studied:** TradingAgents, FinRobot, Priced-In

### Key Takeaways
| Pattern | TradingAgents | SigLab | Gap |
|---------|--------------|--------|-----|
| Multi-agent | ✅ Specialized analyst roles | ❌ Single agent, 3 tools | SigLab needs agent specialization |
| Tool framework | ✅ Function calling | ❌ **CRITICALLY BROKEN** | Wrong SDK (OpenAI ↔ Anthropic) |
| Data pipeline | ✅ Streaming | ✅ Batch (JSONL) | Adequate for research |
| SoSoValue API use | N/A | 3 of 31+ endpoints | **Massive underutilization** |

---

## 2. LLM Tool Framework: CRITICALLY BROKEN (EVAL-LLM)

| Issue | Severity | Impact |
|-------|----------|--------|
| **Wrong SDK** | 🔴 CRITICAL | OpenAI SDK against Anthropic backend — every tool call returns 404 |
| **99.7% data waste** (ETF tool) | 🔴 HIGH | Fetches 300 rows (48KB) but uses only 1st row (166 bytes) |
| **News tool returns 'Untitled'** | 🟡 MEDIUM | No fallback when title is null |
| **No 429 retry** | 🟡 MEDIUM | LLM client doesn't handle rate limits |
| **Tool loop dead code** | 🟡 MEDIUM | `_tool_loop` never calls tools |

### LLM Benchmark (4 tasks)
- Avg time: **7.5s** per task
- Avg tokens: **581** per task
- Hallucination rate: **0%** (correctly declines unknown queries)
- Tool calls avg: **1.5** per task

---

## 3. Evidence Pipeline: 8.9% Waste (EVAL-Evidence)

| Issue | Waste | Impact |
|-------|:-----:|--------|
| Quote relation 100% redundant | 79 records (7.2%) | Duplicates bid_price/ask_price |
| All 82 quote records have `timestamp=None` | **Data quality bug** | Random ordering on sort |
| Dedup never exercised | 0 benefit | Files deleted before write |
| 3 dead trading pairs (TON, NATGAS, BASED) | 18 records (1.6%) | Zero liquidity noise |
| `price_change_24h_pct` never produced | Silent failure | No log, no warning |
| News evidence silently skipped | No news in output | Exception absorbed by `return_exceptions=True` |

---

## 4. Architecture: Risk Score 7/10 (EVAL-Arch)

| Score | Value |
|:-----:|-------|
| Architecture | **6/10** |
| SoSoValue integration | **4/10** (3 of 31+ endpoints, 450 lines of infra overhead) |
| Risk | **7/10** (HIGH — no fallback providers, no vendor abstraction) |
| Lines of code | **15,868 total** (feeds.py 3,594 = 23% of codebase) |

### No Fallback Architecture
| API | Single Point of Failure | Fallback? |
|-----|:----------------------:|:---------:|
| SoSoValue | ETF + news + currencies | ❌ None |
| SoDEX | Market data | ❌ None (currency_market_snapshot exists but unused) |
| OpenModel AI | LLM | ❌ None |

---

## 5. Microbenchmarks: 95% Import-Bound (EVAL-Bench)

| Component | Cold | Warm | Issue |
|-----------|:----:|:----:|-------|
| Config load | 669ms | 335ms | **pandas import 371ms** |
| Dashboard routes import | 1,157ms | — | **openai import 290ms** |
| Evidence read (sosovalue) | 86ms | 2.8ms | File I/O |
| Market report | 447ms | 255ms | Cold import |
| Experiments payload | 6ms | 2.4ms | Fast — cached |

**FIX: Lazy-import pandas (−371ms) and openai (−290ms) for 50% startup reduction.**

---

## 6. Critical Blockers Summary

| Priority | Issue | Fix |
|:--------:|-------|-----|
| P0 | LLM tool framework uses wrong SDK | Switch Anthropic SDK |
| P0 | LLM has no tools (framework broken) | Rewrite tool format for Anthropic |
| P1 | 99.7% ETF data waste | Add `limit=1` parameter |
| P1 | News tool always returns 'Untitled' | Fix title extraction |
| P1 | Evidence quote records have `timestamp=None` | Add timestamp field |
| P1 | No 429 retry in LLM client | Add rate limit handling |
| P2 | 95% import-bound startup | Lazy-import pandas + openai |
| P2 | No fallback for any provider | Add provider abstraction |
