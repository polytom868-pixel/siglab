# Deep System Audit — All Hidden Issues Found

**4 agents: SDK deep dive, data flow trace, import chain, LLM integration**
**Date: 2026-06-27 | Current: 36 files, ~15,500 lines, 556 tests**

---

## CRITICAL: C-02 camelCase/snake_case Bug in build_research_summary

**File:** `siglab/data/feeds.py:468-470`
**Found by:** DEEP-SDK agent

`build_research_summary()` reads ETF data with `r.get('total_net_inflow')` and `r.get('total_net_assets')` but the API returns `totalNetInflow` and `totalNetAssets` (camelCase). **The LLM research prompt ALWAYS gets null ETF values.** The evidence pipeline (evidence.py) correctly handles this mapping, so evidence files are fine — but the research summary sent to the demo output is broken.

This is the SAME bug class as C-02 from the first audit, but in a DIFFERENT code path. The evidence.py fix didn't propagate to feeds.py.

---

## Pre-Existing Test Failure (1)

**File:** `tests/unit/test_config.py:45`
**Root cause (found by DEEP-DEAD):** `SIGLAB_MEMORY_SCOPE` env var exists in `.env` but `load_settings()` NEVER reads it. The `memory_scope` field on `SiglabConfig` defaults to `'session_local'` but is never populated from env. The test expects it to be populated. **Fix:** either delete the test or wire the env var.

**3 more env vars in `.env` are NEVER read by code:**
- `SIGLAB_OPTUNA_TRIALS`
- `SIGLAB_MEMORY_SCOPE`
- `SIGLAB_USE_HISTORICAL_SEEDS`

---

## Data Flow Issues (DEEP-TRACE)

| Issue | Severity | Detail |
|-------|----------|--------|
| ETF data cached TWICE | MEDIUM | ParquetLake AND EvidenceStore independently cache same ETF data |
| EvidenceStore dead methods | LOW | `write_summary`, `summary`, `linked_relations`, `query` — never called |
| Dashboard never refreshes | MEDIUM | `_ops_cache` never invalidated after initial load. Dashboard is STATIC after page load |
| evidence_graph bug | LOW | `write_evidence_graph_html` expects JSON list, upstream writes JSON dict |
| Field identity lost | MEDIUM | Original API field names (`totalNetInflow`) become generic `value` at EvidenceRecord boundary |
| No provenance tracking | LOW | `evidence_path` stores JSONL file path, not API source URL |

---

## Integration Quality Issues (DEEP-SDK)

| Issue | Severity | Detail |
|-------|----------|--------|
| No SoSoValue SDK exists | INFO | Our wrapper is necessary. No `pip install sosovalue` available |
| `limit` param silently ignored | LOW | Both ETF and coin list endpoints ignore `limit` — always return all rows |
| Double row validation | LOW | `_validate_data_shape` + `_rows_from_data` iterate same rows twice |
| Missing `featured_news()` method | MEDIUM | Only `featured_news_by_currency()` exists. The curated 43K-article endpoint is inaccessible from SoSoValueClient |
| Rate limit burst handling | MEDIUM | Only 2 retries with 0.5s backoff. 429 needs 15-20s cool-down |
| SoSoValue rate limit | INFO | Demo plan ~20/min. Our 10/min is appropriate |

---

## LLM Integration (DEEP-LLM)

| Finding | Detail |
|---------|--------|
| **LLM WORKS** | Both `complete_text` and `complete_json` return valid OpenModel AI responses |
| **SoSoValue WORKS** | 300 ETF rows returned after rate-limit cool-down |
| **LLM only in dead path** | `ClaudeClient` is only called from `live/exporter.py` — strategy documentation notes. NEVER in demo/research/operator |
| **Tool loop bug confirmed** | `_tool_loop` line 582-583 assigns `result = tool` instead of calling the tool handler |
| **Only 1 production caller** | `complete_json` is called from `routes.py:experiment_series_payload()` for evidence narrative (generates text from evidence) |

---

## Summary of ALL Hidden Issues

| ID | Severity | Location | Description | Found By |
|----|----------|----------|-------------|----------|
| **C-02b** | 🔴 CRITICAL | feeds.py:468-470 | camelCase read in build_research_summary (same bug as C-02, different file) | DEEP-SDK |
| P-01 | 🔴 CRITICAL | test_config.py:45 | SIGLAB_MEMORY_SCOPE env var never wired — test fails | DEEP-DEAD |
| P-02 | 🟡 MEDIUM | .env | 3 env vars never read by code | DEEP-DEAD |
| P-03 | 🟡 MEDIUM | evidence.py | 4 EvidenceStore methods dead | DEEP-TRACE |
| P-04 | 🟡 MEDIUM | routes.py | _ops_cache never invalidated — dashboard is static | DEEP-TRACE |
| P-05 | 🟡 MEDIUM | feeds.py | Double row iteration in validation | DEEP-SDK |
| P-06 | 🟡 MEDIUM | feeds.py | Missing featured_news() public method | DEEP-SDK |
| P-07 | 🟡 MEDIUM | feeds.py | Rate limit burst handling too weak | DEEP-SDK |
| P-08 | 🟢 LOW | llm/llm.py | Tool loop bug (result=tool instead of calling handler) | DEEP-LLM |
| P-09 | 🟢 LOW | evidence.py | evidence_graph format mismatch | DEEP-TRACE |
| P-10 | 🟢 LOW | evidence.py | Field identity lost at EvidenceRecord boundary | DEEP-TRACE |
