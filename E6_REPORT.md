# E6 Evaluation Report — After Fixes

**5 agents: Architecture, LLM, Evidence, SoSoValue, Benchmarks**
**Date: 2026-06-27 | 41 files, 15,419 lines, 556 tests pass**

---

## Key Improvements Since Last Evaluation

| Metric | Before (E5) | After (E6) | Change |
|--------|:-----------:|:----------:|:------:|
| Architecture elegance | 4/10 | **3.5/10** (more honest) | −0.5 |
| LLM tool score | 4/10 | **7/10** (batching fixed) | **+3.0** |
| 3+ parallel tools | BROKEN | **WORKING** | ✅ Fixed |
| Evidence dead code | 28% | **~0%** | ✅ Clean |
| SoSoValue dead methods | 75% | **~60%** | Progress |
| Test suite time | 14.5s | **11.87s** | −18% |
| Codebase | 16,278L | **15,419L** | −859 |

---

## Critical Issues Found

### 1. search_crypto_news tool BROKEN (E6-LLM)
Calls `client.news_search()` which was deleted in the cleanup. **Raises AttributeError at runtime.**

### 2. currency_market_snapshot returns 500 (E6-SoSo)
The `/currencies/{id}/market-snapshot` endpoint returns HTTP 500. Completely dead.

### 3. cache_enabled param removed, test broken (E6-Bench)
`test_sosovalue_live.py` passes `cache_enabled=False` to SoSoValueClient constructor. The param was removed in cleanup. Test crashes.

### 4. SoSoValue evidence never reaches operator pipeline (E6-SoSo)
`build_research_summary` was deleted (had zero callers), but nothing replaced it. Evidence flows: API → JSONL file. But `operator/pipeline.py` never reads those files. Data goes through LLM text summary → manual spec JSON — **skipping structured decision features entirely.**

### 5. 95% import-bound (E6-Bench)
Three lazy imports would cut 80-90% of cold latency:
- `import pandas as pd` in feeds.py (−1.5s)
- `from fastapi import ...` in routes.py (−0.7s)
- `import numpy as np` in utils.py (−0.1s)

---

## Next 80/20 Deltas

| # | Delta | Effort | Benefit |
|:-:|-------|:------:|:-------:|
| 1 | **Fix search_crypto_news** (method deleted) | 1 line | Tool works |
| 2 | **Lazy-import pandas in feeds.py** | 20 edits, mechanical | **−1.5s cold start** |
| 3 | **Lazy-import fastapi in routes.py** | 5 edits | **−0.7s cold start** |
| 4 | **Fix currency_market_snapshot 500** | Investigate | Price fallback works |
| 5 | **Wire SoSoValue evidence → operator** | Medium | **Decisions use real data** |
| 6 | **Re-add 3 SoSoValue methods** | Small | Full API coverage |
| 7 | **Fix test cache_enabled param** | 1 line | Test passes |
| 8 | **Reduce LLM client retries** (2+3=9 per call) | 2 lines | Fewer rate limits |
