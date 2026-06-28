# Critical Architecture Critique & 80/20 Delta Plan

**5 agents: Architecture critic, LLM evaluator, Evidence critic, SoSoValue critic, Benchmarks**
**Date: 2026-06-27 | 41 files, 16,278 lines, 564 tests pass**

---

## Grade Summary

| Area | Score | Trend |
|------|:-----:|:-----:|
| Architecture | **4/10** | ↓ (was 7/10 last evaluation — now with honest critique) |
| LLM Tools | **4/10** | ↓ (was 5.5 — 3+ parallel tool calling broken) |
| Evidence Pipeline | **5/10** | ↔ (35% dead code, confidence wasted) |
| SoSoValue Integration | **2/10** | ↓↓ (82% of methods dead, 20:1 overhead ratio) |
| Performance | **6/10** | ↑ (lazy imports helped, but 600ms cold tax remains) |

---

## The Biggest Problems

### 1. SoSoValue Integration: 992 Lines for 3 Endpoint Calls (C5-SoSo)

| Metric | Value |
|--------|-------|
| Total lines (SoSoValueClient + DataProvider) | 992 |
| Lines actually needed | ~50 |
| Overhead ratio | **20:1** |
| Dead methods (0 production callers) | **9 of 11 (82%)** |

**The abstraction stack is absurd:** error hierarchy (7 classes), rate limiter, in-memory cache (never used — files deleted before write), metrics framework, circuit breaker (untested), in-flight request dedup (never triggers), SSL certifi fallback chain, pagination abstraction, dataclass request specs — all for 3 endpoint calls.

**Minimum viable:** 50 lines. 3 endpoint methods, 1 shared HTTP helper, no rate limiting, no circuit breaker, no metrics.

### 2. 22% Dead Scaffold Code (C5-Critic)

| Type | Lines | % |
|------|:-----:|:-:|
| `agent_workspace/` | 448 | 2.7% |
| `scripts/` | 1,335 | 8.2% |
| `tmp/` | 1,800 | 11.1% |
| **Total dead scaffold** | **~3,700** | **22%** |

Plus evidence.py: 35% dead functions (250L). SoSoValueClient: 82% dead methods.

### 3. LLM Tools: 3+ Parallel Calls Broken (C5-LLM)

Tool result batching sends each `tool_result` as separate user message. Anthropic API requires ALL results from one turn in a SINGLE content array. Crashes on 3+ parallel calls.

Missing tools: `get_funding_rate`, `search_news`, `list_currencies`, `get_klines`.

### 4. Evidence Schema Mismatch with Operator (C5-Evidence)

EvidenceRecord produces `[source, observed_at, entity, module, relation, confidence, ...]` but operator/pipeline.py's `evidence_to_decision` expects `[signal, confidence, weight, symbol]`. **Different schema entirely.** Evidence is irrelevant to trading decisions.

### 5. 600ms Cold Import Tax (C5-Bench)

`data/__init__.py` eagerly imports everything. Fix: defer imports = **500ms faster per command** (1 file, 5 lines).

---

## Pareto Analysis: Smallest Deltas, Biggest Impact

| # | Delta | Effort | Benefit | Lines |
|:-:|-------|:------:|:-------:|:----:|
| 1 | **Defer data/__init__.py imports** | 1 file, 5 lines | **500ms faster cold start** | −1 |
| 2 | **Delete 9 dead SoSoValueClient methods** | 1 file, ~200L | **−200 lines, cleaner API** | −200 |
| 3 | **Delete `agent_workspace/`, `scripts/`, `tmp/`** | 3 directories | **−3,583 lines, −22%** | −3,583 |
| 4 | **Delete evidence.py dead functions** | 1 file, ~250L | **−250 lines, −35% of file** | −250 |
| 5 | **Fix tool result batching (3+ parallel calls)** | 1 function | **Unblocks multi-tool AI research** | +10 |
| 6 | **Add `get_funding_rate` tool** | 1 file, ~20L | **Most-requested perps data** | +20 |
| 7 | **Increase max_tokens 2000→4096** | 1 constant | **Better multi-tool answers** | −0 |
| 8 | **Collapse CLI into fewer files** | ~500L | **Simpler CLI** | −200 |
| 9 | **Merge DashboardState into `state.py`** | 1 split | **Cleaner separation** | −0 |
| **Total** | | | **−4,223 lines (-26%)** | |

---

## The Right-Sized SigLab: 14 Files

Current: **41 files, 16,278 lines**. Right-sized: **14 files, ~10,000 lines**.

| Keep | Toss | Merge |
|------|------|-------|
| data/sosovalue_client.py (trimmed to 50L) | agent_workspace/ | cli/* → 2 files |
| data/sodex_client.py | scripts/ | dashboard/ → 2 files |
| evaluation/compile.py | tmp/ | config + schemas |
| evaluation/runner_analysis.py | evidence.py dead functions | |
| live/exporter.py | provider_base.py (overkill) | |
| live/paper_client.py | llm/tools.py (merge into llm) | |
| config.py | 9 dead SoSoValue methods | |
| dashboard/routes.py (split state) | | |
