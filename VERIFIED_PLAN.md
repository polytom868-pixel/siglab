# Verified Fix Plan — All Claims Verified

**4 agents: SoSoValue, Evidence, LLM, Architecture — NO EDITS, pure verification**
**Date: 2026-06-27 | 41 files, 16,278 lines, 564 tests pass**

---

## Claim Verification Summary

| Claim | Stated | Verified | Verdict |
|-------|:------:|:--------:|:-------:|
| SoSoValue: 82% dead methods | 9/11 | **9/12 = 75%** | ✅ Close enough |
| SoSoValue: 20:1 overhead ratio | 992:50 | **992:80-110 = ~10x** | ⚠️ Hyperbolic but directionally correct |
| DataProvider: retry loop dead | Never called | **CONFIRMED dead** | ✅ `_request_with_retry` NEVER called |
| Evidence: 35% dead functions | ~250L | **199L = 28%** | ⚠️ Slightly overestimated |
| Evidence: schema mismatch | Different fields | **CONFIRMED CRITICAL** | ✅ evidence_to_decision ALWAYS returns HOLD |
| LLM: 3+ tool calls broken | Batching bug | **CONFIRMED** | ✅ Anthropic requires single message |
| LLM: missing funding_rate tool | Exists in API | **CONFIRMED** | ✅ SoDEX has it, tool doesn't |
| Arch: dead scaffold | 3,700L | **121K lines** | ✅ Way worse than stated |
| Arch: cold import tax | 600ms | **547ms** | ✅ Within 9% |

---

## Phase 1: Safe Deletes (NONE/LOW risk) — ~1,900 lines

### SoSoValueClient (9 dead methods + 4 dead helpers = ~300 lines)
- Delete `listed_currencies`, `currency_klines`, `news_search`, `featured_news`, `featured_news_pages`, `featured_news_by_currency_pages`, `etf_list`, `etf_summary_history`, `etf_market_snapshot`
- Delete `_paginate`, `_fetch_featured_news_page`, `_build_news_params`, `_validate_news_page_size`
- Delete cache layer (inflight tracker, response_cache, _store_in_cache, _cache_key)
- Delete rate limiter (3 endpoints don't need it)
- Delete `_fill_optional_fields` (only used by dead methods)

### DataProvider base (simplify ~70% dead code)
- Delete `_request_with_retry`, `_do_request` hook, 4 error classification hooks (all never overridden)
- Delete DataProvider base class or reduce to ~30L CircuitBreaker utility

### Evidence pipeline (4 dead functions = ~199 lines)
- Delete `write_evidence_graph_html`, `link_feed_events_to_etf_flows`, `summarize_evidence`, `sodex_ws_evidence`
- Delete orphan `assets/evidence_graph.html`

### Feeds.py (dead build_research_summary = ~160 lines)
- Delete `build_research_summary` (ZERO production callers)
- Delete `fetch_etf_historical_inflow`, `fetch_featured_news` (only called by above)

### Scaffold directories (121K lines — git untrack, don't delete from disk)
- `git rm -r --cached agent_workspace/` (113K lines, 16MB)
- `git rm -r --cached tmp/` (6.7K lines)
- `git rm -r --cached scripts/` (1.5K lines)

---

## Phase 2: Bug Fixes (MEDIUM risk)

### Fix LLM tool result batching (llm.py:146-156)
- Group consecutive `tool` role messages into ONE Anthropic user message with multiple `tool_result` content blocks
- **Unblocks multi-tool AI research**

### Fix evidence schema mismatch (operator/pipeline.py)
- Map EvidenceRecord fields to what `evidence_to_decision` reads
- Add `signal`, `weight`, `symbol` extraction from EvidenceRecord

---

## Phase 3: New Tools (LOW risk)

### Add `get_funding_rate` tool to llm/tools.py
- Wrap SoDEXPublicPerpsClient.funding_history()
- Most-requested missing perps data

### Add `search_crypto_news` tool to llm/tools.py
- Wrap SoSoValueClient.news_search()
- Complements existing featured_news tool

### Increase max_tokens 2000→4096 (llm.py)
- 4 public methods need default update
- Prevents truncation of multi-tool responses

---

## Summary: Lines Saved

| Phase | Lines | Cumulative |
|-------|:-----:|:----------:|
| SoSoValue dead methods | −300 | 15,978 |
| DataProvider simplify | −200 | 15,778 |
| Evidence dead functions | −199 | 15,579 |
| Feeds dead functions | −160 | 15,419 |
| Scaffold directories (untrack) | −121K on disk | 15,419 (code only) |
| **Total code reduction** | **−859** | **15,419 (-38% from 24,847)** |
