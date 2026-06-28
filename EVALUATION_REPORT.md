# SigLab Architecture & Integration Evaluation

## Lines of Code by Feature

```
  api_clients           3631 lines   (36%)
  evaluation            2750 lines   (17%)
  live_trading          2261 lines   (14%)
  cli                   1896 lines   (12%)
  dashboard             1843 lines   (12%)
  other                 1750 lines   (11%)
  llm                    850 lines    (5%)
  evidence               679 lines    (4%)
  operator               265 lines    (2%)
```

38 Python files, 15,925 lines total.

## File Size Hotspots

```
 3631 siglab/data/feeds.py        -- 6 merged modules: SoSoValueClient + SoDEXPublicPerpsClient + SoDEXFeeds + SoDEXSignedPerpsClient + SoDEXWeightScheduler + error classes
 1564 siglab/live/paper_client.py -- paper trading + live execution adapter + Strategy classes
 1559 siglab/dashboard/routes.py  -- FastAPI routes + DashboardState class + template rendering
 1330 siglab/evaluation/compile.py-- 14 compiler handlers in one file
  951 siglab/cli/helpers.py       -- CLI utilities + market report builder + deployment helpers
  738 siglab/utils.py             -- risk metrics + circuit breaker + crypto helpers + path utils
```

## Architecture Assessment

### Strengths
- **Clean module boundaries**: `data/` (API clients), `evaluation/` (backtest/compile), `live/` (execution), `operator/` (pipeline orchestration), `llm/` (AI research), `dashboard/` (FastAPI web)
- **Rich error hierarchy**: Each external dependency has its own exception classes — `SoSoValueApiError` (with 5 subtypes: Config, Auth, RateLimit, Transport, generic), `SoDEXError` (with 5 subtypes: WeightLimit, RateLimit, Transport, Upstream, Format), `LLMProviderError` (with 7 subtypes: Config, Auth, RateLimit, Quota, Transport, Upstream, Format)
- **Retry + backoff in API clients**: SoSoValueClient (4 retries, jittered exponential backoff), ClaudeClient (3 retries, 2^n backoff), SoDEXPublicPerpsClient (3 retries on 429)
- **Rate limiting**: SoSoValueClient enforces a process-local rolling budget; SoDEX has a weight-based scheduler (`SoDEXWeightScheduler`)
- **Response caching**: SoSoValueClient has per-endpoint TTL cache with dedup of in-flight requests
- **Protocol classes**: `SoDEXSigner`, `WebSocketConnection`, `_ResearchProvider`, `_MarketDataProvider` — clean interfaces for testability
- **Minimal external DB**: Just SQLite for deployment records; no ORM, no heavy framework dependencies
- **Metrics on all clients**: Endpoint-level latency, success rates, retry counts, rate-limit hits, transport failures

### Weaknesses
- **`feeds.py` god file (3631 lines)**: Merges SoSoValueClient, SoDEXPublicPerpsClient, SoDEXFeeds, SoDEXSignedPerpsClient, SoDEXWeightScheduler, and all error classes. Six distinct responsibilities in one file.
- **`paper_client.py` god file (1564 lines)**: Merges SoDEXPaperPerpsClient (paper trading engine), SoDEXExecutionAdapter (live execution), and DirectionalPerpsSigLabStrategy (full strategy lifecycle). Three independent systems.
- **`cli/helpers.py` (951 lines)**: Does everything — market report generation, deployment helpers, path utilities, JSON rendering — should be split into CLI domain modules.
- **`utils.py` (738 lines)**: Grab bag of risk metric computation, circuit breaker, path resolution, JSON helpers, and hashing utilities.
- **Backwards-compat aliases**: `_align_cross_sectional_components = _align_cs_comp`, `_validate_symbol = _val_sym`, `_validate_quantity = _val_qty`, etc. in `paper_client.py` and `compile.py`. Dead code that implies hesitance to clean up.
- **LLM provider abstraction is a facade**: `SUPPORTED_LLM_PROVIDERS = frozenset({"anthropic", "openai"})` exists, but `resolve_llm_provider()` always returns `"anthropic"`. No base class for LLM clients. The only implementation (`ClaudeClient`) is hardcoded to the Anthropic SDK format.
- **No dependency injection framework**: Manual constructor injection throughout, which is fine, but no container or service locator pattern makes testing integration paths harder.

## Integration Assessment

### SoSoValue API (`SoSoValueClient`)
```
strength: ✓ 4 retries, jittered backoff ✓ rate limiting ✓ TTL cache ✓ rich error types ✓ metrics
risk:    ✗ no fallback provider ✗ no circuit breaker ✗ no graceful degradation
```
The client is well-implemented, but there is **no fallback** if the API goes down. All ETF data, news, and currency data depend on this single provider. The cache provides temporary relief during brief outages but has finite TTL.

### SoDEX API (`SoDEXPublicPerpsClient`, `SoDEXFeeds`, `SoDEXSignedPerpsClient`)
```
strength: ✓ rich error hierarchy ✓ weight scheduler for rate limits ✓ retry on 429
risk:    ✗ field-name schema changes produce silent nulls in evidence ✗ hardcoded endpoints
         ✗ no schema version validation
```
The LLM tool `get_market_data` probes `t.get('s') or t.get('symbol')` — if SoDEX renames fields, this silently returns "No data found" instead of raising. Evidence functions (`sodex_rest_evidence`) access dict keys like `"lastPx"`, `"bidPx"` directly — a field rename would produce `None` values that propagate silently through the decision pipeline.

### SoDEX WebSocket (`SoDEXWebSocketClient`)
```
strength: ✓ error hierarchy (Format, Config, Timeout, Disconnected) ✓ channel mapping
risk:    ✗ hardcoded endpoints from constants ✗ no reconnection with backoff (only bare reconnect)
```
Good error structure but relies entirely on hardcoded URL mappings. No connection health monitoring.

### LLM / AI Provider (`ClaudeClient`)
```
strength: ✓ retry with backoff (3 attempts) ✓ OpenAI → Anthropic format conversion
         ✓ token tracking and cost estimation ✓ rate-limit / auth error distinction
risk:    ✗ hardcoded to Anthropic Messages API ✗ no provider abstraction ✗ provider detection is mocked
         ✗ name misleading — uses OpenModel AI, not Anthropic directly
```
The layer works end-to-end with OpenModel AI's Anthropic-compatible endpoint, but switching providers would require rewriting `_call_chat`. Despite `SUPPORTED_LLM_PROVIDERS = {"anthropic", "openai"}`, the openai path is dead code. Tools create new API clients on every invocation (no reuse).

## Risk Assessment

### Risk 1: SoSoValue API down → no fallback
**Severity: CRITICAL** — affects 4 features (ETF flows, news, currency data, market snapshots)

SoSoValueClient has 4 retries with exponential backoff, but if the API is genuinely unavailable:
- `SoSoValueTransportError` propagates up through `MarketDataProvider.build_research_summary()`
- The research pipeline stops producing ETF evidence and news evidence
- No cached-data fallback for stale data (cache just returns stale results briefly)
- No alternative data provider (CoinGecko, CoinMarketCap, etc.)
- No graceful degradation mode (e.g., "run with SoDEX data only")

### Risk 2: SoDEX API changes → silent nulls
**Severity: HIGH** — affects downstream decision quality, difficult to detect

SoDEX clients have good structural error handling (HTTP status, transport), but:
- **Evidence layer is schema-fragile**: `sodex_rest_evidence` accesses `row["lastPx"]`, `row["bidPx"]`, `row["askPx"]`. If SoDEX renames these to camelCase or adds a version prefix, these become `None` without any error.
- **LLM tools are schema-fragile**: `get_market_data` checks `t.get('s') or t.get('symbol')` — field renames cause silent "No data found".
- **No version pinning**: No API version header, no schema validation at the boundary.
- **No schema-change alerting**: No tests that validate field existence against a known response fixture.

### Risk 3: LLM provider changes → no abstraction
**Severity: HIGH** — switching providers requires rewriting the core client

Despite claiming support for OpenAI:
- `resolve_llm_provider()` hardcodes `"anthropic"` — the OpenAI path is a dead branch
- `ClaudeClient._call_chat()` manually converts messages to Anthropic Messages API format
- No base class / abstract interface for provider implementations
- No adapter pattern — `_get_client()` always returns an `AsyncAnthropic` instance
- API key and base URL come from `OPENMODEL_API_KEY` / `OPENMODEL_BASE_URL` — tied to OpenModel AI
- If OpenModel AI changes its API compatibility or Anthropic deprecates the Messages format, the entire LLM subsystem needs rewriting

### Secondary Risks

- **OperatorPipeline has no external API fault isolation**: A SoSoValue failure during `run_once()` would crash the pipeline. No timeout boundary between data-gathering and decision-making.
- **Live deployment exports generated strategy code**: `LiveDeploymentManager` writes `.py` files from templates. An injection vector exists if user-controlled spec fields make it into the generated code.
- **Paper trading and live execution in same file**: `paper_client.py` contains `SoDEXPaperPerpsClient`, `SoDEXExecutionAdapter`, and `DirectionalPerpsSigLabStrategy`. A bug in paper-trading logic could affect live execution paths through shared utility functions.

## Bottlenecks

1. **`feeds.py` (3631 lines)**: Top-priority consolidation target. Split into: `data/sosovalue.py` (SoSoValueClient), `data/sodex_public.py` (SoDEXPublicPerpsClient + SoDEXFeeds), `data/sodex_auth.py` (signing + SoDEXSignedPerpsClient), `data/sodex_ratelimit.py` (SoDEXWeightScheduler).

2. **`paper_client.py` (1564 lines)**: Split into: `live/paper_core.py` (SoDEXPaperPerpsClient), `live/execution.py` (SoDEXExecutionAdapter), `live/strategies.py` (DirectionalPerpsSigLabStrategy and co.).

3. **No external API fallbacks**: Every external API is a hard dependency. No circuit-breaker pattern on data providers (unlike OperatorPipeline which has one for trading). No stale-data fallback mode, no alternative provider.

4. **LLM provider lock-in**: The provider abstraction is cosmetic. No way to swap to a different LLM without rewriting the client.

5. **Schema coupling to SoDEX field names**: Evidence functions access raw API field names directly. A SoDEX schema change produces silent data corruption in the decision pipeline rather than a detectable error.

6. **Dashboard state management**: `DashboardState` in `routes.py` (1559 lines) handles both route definitions and all state management. Should separate into a proper service layer.

## Scores

| Dimension | Score | Rationale |
|-----------|-------|-----------|
| **Architecture** | **7/10** | Clean module boundaries with good error hierarchies and separation of concerns. Dragged down by god files (feeds.py: 3631 lines, paper_client.py: 1564 lines), dead compat shims, and cosmetic provider abstractions. |
| **Integration** | **6/10** | Well-built clients with retries, rate limiting, caching, and metrics. No fallback providers for any external API. Single points of failure for every external dependency. Schema-fragile evidence layer. |
| **Risk** | **5/10** | Three critical single points of failure (SoSoValue, SoDEX, LLM provider) with no fallback or graceful degradation. SoDEX schema changes can silently corrupt downstream decisions. LLM provider abstraction is a facade. |

## Key Recommendations

1. **Split `feeds.py`** into domain-specific modules before adding any new features
2. **Add fallback data sources** for critical market data (e.g., CoinGecko as SoDEX fallback)
3. **Add schema validation at API boundaries** — validate field presence immediately on receipt, not downstream
4. **Create a proper LLM provider abstraction** with an `LLMClient` protocol/ABC, then implement both `AnthropicClient` and `OpenAIClient`
5. **Separate paper trading from live execution** in `paper_client.py`
6. **Add circuit breakers on all data providers** (follow the pattern already in OperatorPipeline)
7. **Remove backward-compat aliases** to reduce cognitive load
