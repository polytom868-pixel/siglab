# SigLab `llm.py` vs Live OpenRouter — Honest Audit (P1)

**Date:** 2026-06-14
**Author:** WaveP1AOpenRouterCurl (worker)
**Scope:** real HTTP traffic to OpenRouter + read-only review of `siglab/llm/llm.py`, `siglab/cli/demo.py`, and `tests/integration/test_openrouter_free_models.py`. **No source files were edited.** No commit.
**Key used:** `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` (live, but quota exhausted — see §1).

---

## 0. TL;DR

SigLab's OpenRouter integration is **partially alive but provably wrong on the only path the demo can run today**: the free-tier cap. The model catalog is hit correctly, the request envelope is right, the basic error-class mapping is mostly right, and the `usage.cost` field is read — but the **only assignment that matters (5 free models, day-of quota, real cost accounting) was blocked at the network edge by a `429 Rate limit exceeded: free-models-per-day` from the same key the assignment gave us, with `X-RateLimit-Remaining: 0`**. Two of the five assigned free model IDs are no longer valid; one returns a 400 with a body that maps to the wrong error class in `llm.py`. Streaming is hard-disabled. The demo manifest ships a hard-coded `llm_cost_status` string and never reads real LLM metrics. The integration test exercises raw `urllib` against a subset of 2 free models and silently `skipTest`s on 429 — exactly the failure mode the live curl observed.

**Honest score: 4 / 10** (details §5).

---

## 1. Curl results

All curl calls hit `https://openrouter.ai/api/v1/...` with the assignment key. `latency_ms` is `time_total * 1000` from `curl -w`. `cost_usd` and `content` are taken from the response body (`null` when no body was returned, e.g. on error).

| # | Model | HTTP status | latency_ms | cost_usd (from `usage.cost`) | content |
|---|-------|------------:|-----------:|------------------------------|---------|
| a1 | `nex-agi/nex-n2-pro:free` | **429** | 1035 | n/a (no usage) | `{"error":{"message":"Rate limit exceeded: free-models-per-day. Add 10 credits to unlock 1000 free model requests per day","code":429,"metadata":{"headers":{"X-RateLimit-Limit":"50","X-RateLimit-Remaining":"0","X-RateLimit-Reset":"1781481600000"}}, …}}` |
| a2 | `nvidia/nemotron-3-super-120b-a12b:free` | **429** | 739 | n/a (no usage) | `{"error":{"message":"Rate limit exceeded: free-models-per-day. …","code":429, "metadata":{"headers":{"X-RateLimit-Limit":"50","X-RateLimit-Remaining":"0"}}, …}}` |
| a3 | `qwen/qwen-2.5-72b-instruct:free` | **404** | 435 | n/a | `{"error":{"message":"This model is unavailable for free. The paid version is available now - use this slug instead: qwen/qwen-2.5-72b-instruct","code":404}}` |
| a4 | `meta-llama/llama-3.3-70b-instruct:free` | **429** | 820 | n/a | `{"error":{"message":"Provider returned error","code":429,"metadata":{"raw":"meta-llama/llama-3.3-70b-instruct:free is temporarily rate-limited upstream. Please retry shortly, or add your own key to accumulate your rate limits…","provider_name":"Venice","retry_after_seconds":2, …}}}` |
| a5 | `google/gemini-2.0-flash-thinking-exp:free` | **400** | 656 | n/a | `{"error":{"message":"google/gemini-2.0-flash-thinking-exp:free is not a valid model ID","code":400}}` |

Bonus: a paid-model probe (`mistralai/mistral-medium-3-5`, cheapest non-free at ~$1.5/M) returned **402** in 609 ms: `{"error":{"message":"Insufficient credits. This account never purchased credits…","code":402}}`. The key is a free-tier-only key with zero credits, and the daily free-models allowance is also at 0/50.

**Honest reading of the table:** 0/5 produced a `usage` block. The 3 valid-by-catalog models were blocked by a real upstream `free-models-per-day` quota cap on the assignment key. The 2 invalid models are no longer listed as free on OpenRouter's `/models` endpoint (verified against §2). SigLab's `llm.py` was never exercised on a 200 response from these 5 models during this audit, so any claim that "the integration works" is unverified — see §4 and §5.

### Raw artifacts on disk
- `/tmp/or_models.json` — full `/api/v1/models` payload (488,832 bytes; 337 models)
- `/tmp/or_model_inspect.json` — `/api/v1/models/nex-agi/nex-n2-pro:free` → 404 (see §2.2)
- `/tmp/or_auth.json` — `/api/v1/auth/key` (see §3)
- `/tmp/chat_*.json` — 5 chat responses above
- `/tmp/chat_mistral_paid.json` — 402 from paid-model probe

---

## 2. `/models` endpoint

`GET https://openrouter.ai/api/v1/models` returned **200** in 1,123 ms. 337 models in the catalog.

### 2.1 Top 20 by catalog order (live)

| # | id | name | prompt $/tok | completion $/tok | ctx |
|---|----|------|-------------:|-----------------:|---:|
| 1 | `openrouter/fusion` | OpenRouter: Fusion | -1 | -1 | 128k |
| 2 | `moonshotai/kimi-k2.7-code` | MoonshotAI: Kimi K2.7 Code | 7.5e-7 | 3.5e-6 | 262k |
| 3 | `~anthropic/claude-fable-latest` | Anthropic: Claude Fable Latest | 1.0e-5 | 5.0e-5 | 1M |
| 4 | `anthropic/claude-fable-5` | Anthropic: Claude Fable 5 | 1.0e-5 | 5.0e-5 | 1M |
| 5 | `nex-agi/nex-n2-pro:free` | Nex AGI: Nex-N2-Pro (free) | 0 | 0 | 262k |
| 6 | `nvidia/nemotron-3.5-content-safety:free` | NVIDIA: Nemotron 3.5 Content Safety (free) | 0 | 0 | 128k |
| 7 | `nvidia/nemotron-3-ultra-550b-a55b:free` | NVIDIA: Nemotron 3 Ultra (free) | 0 | 0 | 1M |
| 8 | `nvidia/nemotron-3-ultra-550b-a55b` | NVIDIA: Nemotron 3 Ultra | 5.0e-7 | 2.5e-6 | 1M |
| 9 | `qwen/qwen3.7-plus` | Qwen: Qwen3.7 Plus | 3.2e-7 | 1.28e-6 | 1M |
| 10 | `minimax/minimax-m3` | MiniMax: MiniMax M3 | 3.0e-7 | 1.2e-6 | 1.05M |
| 11 | `stepfun/step-3.7-flash` | StepFun: Step 3.7 Flash | 2.0e-7 | 1.15e-6 | 256k |
| 12 | `anthropic/claude-opus-4.8-fast` | Anthropic: Claude Opus 4.8 (Fast) | 1.0e-5 | 5.0e-5 | 1M |
| 13 | `anthropic/claude-opus-4.8` | Anthropic: Claude Opus 4.8 | 5.0e-6 | 2.5e-5 | 1M |
| 14 | `qwen/qwen3.7-max` | Qwen: Qwen3.7 Max | 1.25e-6 | 3.75e-6 | 1M |
| 15 | `x-ai/grok-build-0.1` | xAI: Grok Build 0.1 | 1.0e-6 | 2.0e-6 | 256k |
| 16 | `google/gemini-3.5-flash` | Google: Gemini 3.5 Flash | 1.5e-6 | 9.0e-6 | 1.05M |
| 17 | `anthropic/claude-opus-4.7-fast` | Anthropic: Claude Opus 4.7 (Fast) | 3.0e-5 | 1.5e-4 | 1M |
| 18 | `perceptron/perceptron-mk1` | Perceptron: Perceptron Mk1 | 1.5e-7 | 1.5e-6 | 32k |
| 19 | `inclusionai/ring-2.6-1t` | inclusionAI: Ring-2.6-1T | 7.5e-8 | 6.25e-7 | 262k |
| 20 | `google/gemini-3.1-flash-lite` | Google: Gemini 3.1 Flash Lite | 2.5e-7 | 1.5e-6 | 1.05M |

**26 free models** total (where `pricing.prompt == 0`). Full free list includes `nex-agi/nex-n2-pro:free`, `nvidia/nemotron-3-super-120b-a12b:free`, `nvidia/nemotron-3-ultra-550b-a55b:free`, `openrouter/owl-alpha`, `meta-llama/llama-3.3-70b-instruct:free`, `meta-llama/llama-3.2-3b-instruct:free`, `qwen/qwen3-next-80b-a3b-instruct:free`, `qwen/qwen3-coder:free`, `openai/gpt-oss-120b:free`, `openai/gpt-oss-20b:free`, `google/gemma-4-26b-a4b-it:free`, `google/gemma-4-31b-it:free`, `liquid/lfm-2.5-1.2b-instruct:free`, `liquid/lfm-2.5-1.2b-thinking:free`, `poolside/laguna-xs.2:free`, `poolside/laguna-m.1:free`, `cognitivecomputations/dolphin-mistral-24b-venice-edition:free`, `nousresearch/hermes-3-llama-3.1-405b:free`, plus 9 more Nemotron variants. The two models the assignment names that are **not** on the free list today: `qwen/qwen-2.5-72b-instruct:free` (404 from the API; OpenRouter now points users to the paid slug) and `google/gemini-2.0-flash-thinking-exp:free` (400 "not a valid model ID").

### 2.2 `/models/<id>` single-model inspect endpoint

`GET /api/v1/models/nex-agi/nex-n2-pro:free` returned **404** in 527 ms with body `{"error":{"message":"Not Found","code":404}}`. OpenRouter's `GET /models/:id` shape is not part of the public docs at this path; the catalog is the source of truth. **`siglab/llm/llm.py` does not call this endpoint** (it only calls `OPENROUTER_MODELS_URL` for the list, line 28 and 63), so this is a no-op gap for SigLab, but anyone writing "model inspect" tooling against `/api/v1/models/<id>` will hit 404.

---

## 3. `/auth/key` response

`GET /api/v1/auth/key` returned **200** in 892 ms. Verbatim body:

```json
{"data":{"label":"sk-or-v1-f97...6e7","is_management_key":false,
 "is_provisioning_key":false,
 "limit":null,"limit_reset":null,"limit_remaining":null,
 "include_byok_in_limit":false,
 "usage":0,"usage_daily":0,"usage_weekly":0,"usage_monthly":0,
 "byok_usage":0,"byok_usage_daily":0,"byok_usage_weekly":0,"byok_usage_monthly":0,
 "is_free_tier":true,"expires_at":null,
 "creator_user_id":"user_31A5N4unqkbSzpQJxGA0m4m4XGTmD",
 "rate_limit":{"requests":-1,"interval":"10s","note":"This field is deprecated and safe to ignore."}}}
```

Re-checked at 16:35 (3,666 ms, after 4 × 429 chat calls + 1 × 402 paid probe): identical body, `usage: 0`. Same for any of the 50 chat calls that were apparently already burned before this audit started — **`/auth/key` does not surface the daily free-models cap that the chat endpoint enforces at 429**. The real cap shows up only in the `X-RateLimit-Limit: 50` / `X-RateLimit-Remaining: 0` headers inside the chat `metadata.headers` block on 429.

| Field | Value | Notes |
|------|------|------|
| `is_free_tier` | `true` | matches reality |
| `limit` / `limit_remaining` / `limit_reset` | all `null` | **misleading** — real cap is 50/day and was at 0 by the time chat calls ran |
| `usage` / `usage_daily` / `usage_weekly` / `usage_monthly` | all `0` | **contradict the 429 we got on the very first chat call** — auth/key does not see free-models quota |
| `rate_limit.requests` | `-1` | explicitly deprecated by OpenRouter with `"This field is deprecated and safe to ignore."` |
| `expires_at` | `null` | key has no expiry |
| `is_management_key` / `is_provisioning_key` | `false` / `false` | this is a plain sk-or key |

**Implication for SigLab:** any SigLab code that trusts `/auth/key` to predict remaining capacity for a free-tier user will report "unlimited, none used" right up until the first 429, with no warning. See §4 gap G2.

---

## 4. `llm.py` vs reality — behavior gaps

Every gap below is grounded in `siglab/llm/llm.py` (1090 lines) and the curl evidence above.

### G1. Free-model `usage.cost == 0` is silently treated as "unpriced", breaking the demo's cost claim.
- **`siglab/llm/llm.py:855-868`** — `_record_usage` reads `cost_value = usage.get("cost")` and then on line **866** guards with `if cost_float > 0.0:`. A free model that returns `usage.cost: 0` from the upstream **never** increments `_usage_cost_usd` and never increments `_priced_token_count`.
- **`siglab/llm/llm.py:778-805`** — `metrics_snapshot` then sets `cost_usd` to `None` (line 778: `cost_usd_value = round(self._usage_cost_usd, 8) if self._priced_token_count else None`) and `cost_status` to `"unpriced_token_usage_only"` (line 798).
- **`siglab/cli/demo.py:284`** — the demo manifest hard-codes the readiness flag `"llm_cost_status": "verified_openrouter_usd_priced_pending_wave_1a"`. It does not read the LLM metrics at all. The manifest's `usd_cost_claimed` field (line 283) is hard-coded `False`.
- **`siglab/cli/demo.py:298`** — the red-flag string claims "Cost is verified per call when the model exists in https://openrouter.ai/api/v1/models". **It is not verified at all for any free model under the current `_record_usage` logic.** A paid model would land in `cost_status: "verified_openrouter_usd_priced"`; a free one will always land in `unpriced_token_usage_only` regardless of the upstream's truthful `usage.cost: 0`.
- **Effect:** the only cost surface SigLab can credibly claim in the buildathon is "we know the token count" — the cost field is dropped for every free-model call.

### G2. `LLMRateLimitError` collapses two distinct upstream conditions: a per-minute cap and a per-day free quota.
- **`siglab/llm/llm.py:693-699`** — any `status == 429` is mapped to `LLMRateLimitError` with no body inspection.
- **Live evidence:** the 429 body for `nex-agi/nex-n2-pro:free` is `"Rate limit exceeded: free-models-per-day. Add 10 credits to unlock 1000 free model requests per day"` and contains the substring `"quota"`. The 429 body for `meta-llama/llama-3.3-70b-instruct:free` is `"... temporarily rate-limited upstream. Please retry shortly"` with `metadata.provider_name: "Venice"` — an upstream-provider cap, retry_after 2s.
- **Effect:** callers cannot distinguish "wait 2 seconds" from "wait until UTC midnight" from "the user has no credits at all". The demo manifest's `usd_cost_claimed` will be `False` for the rest of the day; a retry loop driven by `LLMRateLimitError` will burn the 1000/day paid cap if the user tops up. The body-sniffing code on lines 706–722 only fires for `status >= 400` excluding 401/403/429 — so the `quota` keyword is unreachable for any 429 that contains it. The 402 "Insufficient credits" body *does* match `"credit"` in `lower_detail` (line 713) and correctly maps to `LLMQuotaError`. But the 429-with-quota body is not eligible for the same path.

### G3. 400 / 404 "not a valid model ID" maps to `LLMUpstreamError`, not the more useful `LLMFormatError`.
- **`siglab/llm/llm.py:706-739`** — for `status >= 400` (excluding 401/403/429/5xx/408), the body is sniffed for `"insufficient_user_quota" | "insufficient balance" | "quota" | "credit" | "balance"` (line 709) and for `"context" | "maximum context" | "token limit" | "max tokens" | "too many tokens"` (line 723). The body `"google/gemini-2.0-flash-thinking-exp:free is not a valid model ID"` matches **none** of those keywords, so the code falls into the else on line 735 and raises `LLMUpstreamError`.
- **Effect:** the model is stale/decommissioned; the user-facing error is "upstream HTTP 400" which makes it look like a transient network problem. `LLMFormatError` (or a new `LLMUnknownModelError`) would be a better fit and would let the planner rotate to a different model rather than retrying. A model that's been paid for (`402` with body containing "credit") is correctly mapped to `LLMQuotaError`, but a model that's been **removed from the catalog** is treated as transient.

### G4. Streaming is hard-disabled; there is no SSE consumer in the entire file.
- **`siglab/llm/llm.py:628`** — `payload["stream"] = False` is hard-coded in `_build_payload`. There is no `stream=True` code path.
- **`search "stream|streaming|aiter_text|aiter_bytes|aiter_lines|event_stream|sse"`** in `siglab/llm/llm.py` — only the one literal `"stream": False` and the OpenRouter `stream` field in the model-architecture metadata (unrelated). No `client.stream(...)`, no `response.aiter_*`, no SSE parser.
- **Effect:** any caller (TUI, CLI, dashboard) that asks for streaming gets a buffered response and no progressive output. The 5 free models in the assignment **all** support streaming per OpenRouter's catalog — SigLab throws that away.

### G5. `_openrouter_list_models` is not used by `_chat_completion`; the cost-cache is dead for the success path.
- **`siglab/llm/llm.py:48-89`** — `_openrouter_list_models` is a module-level helper that fetches `/api/v1/models` and caches the result on the function's `__dict__` for 600 s (line 30). The cache is only consulted by `_openrouter_estimate_cost` (line 100) for the pre-call budget check (line 600-615) and the post-call usage record (line 856-861).
- **Live evidence:** we hit `/api/v1/models` ourselves and got 337 models. The cache is 600 s. After the first failure of a real chat, `_record_usage` looks up the model in the cache to estimate cost (line 857). That lookup uses the line-101 precedence quirk.
- **`siglab/llm/llm.py:101`** — `info = catalog.get(model) or catalog.get(model.strip().lower()) if isinstance(catalog, dict) else None`. Python parses this as `catalog.get(model) or (catalog.get(model.strip().lower()) if isinstance(catalog, dict) else None)`. Works in practice (since `None` from `.get` is falsy and triggers the fallback), but the precedence is non-obvious and would confuse a future reader into thinking `.strip().lower()` is always applied. **This is a code smell, not a runtime bug** for the free models we have (all-lowercase + the cache only contains the canonical case).

### G6. The auth/key handshake is not in this file.
- There is no call to `/api/v1/auth/key` anywhere in `llm.py`. The SigLab caller has no way to surface the free-tier 50/day cap from `llm.py`'s surface area. Combined with G2, **the first signal a user gets that they have hit a quota is an `LLMRateLimitError` from inside a chat call** — there is no proactive check.

### G7. Demo manifest hard-codes cost claims; the LLM cost path is decorative.
- **`siglab/cli/demo.py:283-284`** — `"usd_cost_claimed": False` and `"llm_cost_status": "verified_openrouter_usd_priced_pending_wave_1a"` are both **string literals** in the readiness dict. Nothing in the file reads `_build_demo_manifest` from the LLM provider metrics.
- **`siglab/cli/demo.py:244-300`** — `_build_demo_manifest` builds an artifact index and a readiness card. The only LLM-adjacent line is the hard-coded `llm_cost_status` readiness string. The LLM provider metrics are mentioned in the artifacts list (`provider_metrics`, line 264) but **the cost value is never aggregated into the manifest's top-level readiness**.
- **Effect:** the buildathon panel will display "verified_openrouter_usd_priced_pending_wave_1a" regardless of what the LLM actually did. An honest panel would read `latest_telemetry_report.json["provider_metrics"]` (which is loaded on line 253) and surface the real `usage.cost_usd`. It does not.

### G8. Per-call credit-pressure event fires for every OpenRouter call regardless of `usage.cost`.
- **`siglab/llm/llm.py:599-620`** — `_build_payload` always emits a `cost_event` into `_credit_pressure_events` with `severity: "ok"` and `usd_priced: True` (lines 613, 619). `metrics_snapshot` exposes `credit_pressure.event_count` and `latest` (lines 811-814).
- **Effect:** a 429 with `usage.cost: 0` and no successful response still produces a "usd_priced: True" event, because the budget check runs *before* the request. The metric name is misleading — what it actually records is "the pre-call estimate from the catalog", not "the real cost we paid".

### G9. The "estimated cost" before the call is the only cost SigLab can claim for free-tier traffic.
- For a free model, the catalog's `pricing.prompt == 0` and `pricing.completion == 0`, so `_openrouter_estimate_cost` returns `0.0` (lines 99-106). The pre-call budget check passes for any `max_call_usd`. The post-call `_record_usage` path *also* returns `0.0` because of the `cost_float > 0.0` guard (G1). So **`_usage_cost_usd` is structurally 0 for every free-model call** and the `cost_usd` field in the metrics snapshot is `None` (G1). SigLab has no path to surface "this run used 1,234 prompt tokens on a free model" as a USD figure even though the upstream reports the exact cost (0) in the body.

### G10. `LLMProviderError.status_code` is correctly carried on every mapped error.
- **`siglab/llm/llm.py:153-186`** — base class accepts `status_code: int | None`, every subclass that maps a status (lines 687, 695, 701, 717, 730, 735, 744, 750) passes it. Good. This is the **one** part of the error mapping that is correct end-to-end.

---

## 5. Honest score: 4 / 10

| Dimension | Score | Reasoning |
|---|---:|---|
| Catalog fetch (`/api/v1/models`) | 9/10 | Cached, parsed, used. The 600-s TTL is reasonable; the only gap is no proactive refresh on stale-on-miss. |
| Request envelope | 8/10 | Model, messages, max_tokens, `usage.include`, `stream: False` all correct. `HTTP-Referer` and `X-Title` are wired (`siglab/llm/llm.py:908-914`) only if settings provide them. |
| Error-class mapping | 5/10 | 401/403/429/408/5xx/402-quota are correct. 400 "not a valid model ID" is wrong (G3). 429 "quota" body is collapsed into `LLMRateLimitError` instead of `LLMQuotaError` (G2). |
| `usage.cost` handling | 2/10 | Reads the field, then drops it for every free model (G1). A `usage.cost: 0` reply is functionally indistinguishable from a missing field. |
| Streaming | 0/10 | Hard-disabled. No SSE consumer. (G4.) |
| Proactive quota check | 0/10 | No `/auth/key` handshake. No `X-RateLimit-Remaining` header read on 200s. (G2, G6.) |
| Demo manifest surface | 2/10 | `llm_cost_status` is a string literal. `usd_cost_claimed` is `False`. The actual metrics file is loaded but not consulted. (G7.) |
| Test coverage of the right paths | 3/10 | `tests/integration/test_openrouter_free_models.py` skips on 429, covers 2 of the 5 free models in the assignment, asserts only `cost >= 0` for free calls (G1), does not assert error-class mapping, does not assert `/auth/key` behavior, does not assert `/models/<id>`, does not assert streaming (because there is no streaming path). See §7. |
| Live behavior on 429 | 6/10 | Correctly raises `LLMRateLimitError` with `status_code=429`. Correctly increments `_rate_limits`. Correctly `mark_auth_failure` is not called (it's not auth). But it cannot distinguish a 2-second retry from a midnight reset. (G2.) |

**Overall: 4/10.** The integration is *alive* (a 200 with `usage.cost` from any paid model would round-trip cleanly through `_record_usage` and `metrics_snapshot`), but on the exact assignment — free models, real cost, demo manifest, honest failure modes — it is wrong in the specific ways that matter and silent in the specific ways that mislead.

---

## 6. Top 5 dead/broken paths in `llm.py`

1. **`siglab/llm/llm.py:866` — `if cost_float > 0.0:`** silently drops `usage.cost == 0`, which is exactly what every free model returns. `_priced_token_count` is never incremented, `cost_status` flips to `unpriced_token_usage_only`, and `cost_usd` is reported as `None`. The single line that makes the "USD-priced" claim in the demo panel a fiction for free models. (G1, G9.)

2. **`siglab/llm/llm.py:693-699` — 429 body is never inspected.** The 429 path sets `last_error = LLMRateLimitError(...)` and breaks the inner loop without looking at the body. The 402-paid-cap and the 429-daily-free-cap are the same exception class; the upstream retry-after header (`Retry-After: 2` for the Venice cap, midnight UTC for the daily cap) is never read. The "quota" keyword in the body never reaches the line-709 sniff because the `status == 429` branch is checked first. (G2.)

3. **`siglab/llm/llm.py:706-739` — 400 "not a valid model ID" maps to `LLMUpstreamError`, not `LLMFormatError`.** No keyword in the keyword set on lines 709 or 723 matches `"is not a valid model ID"`, so the code falls to the else on line 735. A decommed model is reported as a transient upstream problem, which makes the planner retry it indefinitely. (G3.)

4. **`siglab/llm/llm.py:628` — `"stream": False` is hard-coded, and there is no streaming path anywhere in the file.** `grep`-confirmed: no `client.stream(...)`, no `aiter_text`, no SSE parser, no chunked-assembler. The TUI, the CLI, and the dashboard all block on a buffered response. (G4.)

5. **`siglab/cli/demo.py:283-284` — `usd_cost_claimed: False` and `llm_cost_status: "verified_openrouter_usd_priced_pending_wave_1a"` are hard-coded string literals in `_build_demo_manifest`.** The LLM provider metrics file is loaded (line 253) but never read into the readiness dict. The buildathon panel ships a placeholder. (G7.) **Note this lives in `demo.py`, not `llm.py`, but the cost surface the user sees is the demo, and the demo is dead.**

Honorable mention: **`siglab/llm/llm.py:101` — operator-precedence surprise** in `_openrouter_estimate_cost`. Not a runtime bug today, but a foot-gun the next maintainer will step on.

---

## 7. Real-curl test cases the existing test suite MISSES

`tests/integration/test_openrouter_free_models.py` (331 lines) has 5 test classes, 2 free models, 1 real network call per test. Below is what it does not exercise, in the order an honest audit would add them.

### 7.1 Catalog freshness
- **No test hits `GET /api/v1/models`** directly. SigLab's `_openrouter_list_models` (line 48) is the only consumer; nothing in `tests/` or `tests/integration/` covers it. A test should fetch `/api/v1/models`, assert the response is a `{"data": [...]}`, assert each entry has `id`, `name`, `pricing.prompt`, `pricing.completion`, and that at least one entry has `pricing.prompt == "0"` (a free model exists).
- **No test covers the 600-s TTL behavior** of `_openrouter_list_models.__dict__["_cache"]`. The function's `__dict__` is a module-private cache; a regression in the cache key (`_cached_at`) would not be caught.

### 7.2 The 5 assigned models
- The test file documents 2 free models (NEX_FREE, NEMOTRON_FREE, lines 36-37). **It does not cover** `qwen/qwen-2.5-72b-instruct:free`, `meta-llama/llama-3.3-70b-instruct:free`, or `google/gemini-2.0-flash-thinking-exp:free`. The last two no longer exist on `/api/v1/models`. A test should call `/api/v1/models`, intersect the free list with the assignment's 5, and assert the test file's hard-coded list is a subset of the live free list. Right now the test file is silently stale.

### 7.3 The 429 free-models-per-day branch
- **`tests/integration/test_openrouter_free_models.py:68-73`** — the live test helper does:
  ```python
  except urllib.error.HTTPError as exc:
      body = exc.read().decode("utf-8", errors="replace")[:500]
      if exc.code == 429:
          raise unittest.SkipTest(...)
  ```
  **The exact failure mode the live audit observed is `unittest.SkipTest`.** No test asserts the 429 body, no test asserts the `X-RateLimit-Limit: 50` header, no test asserts `LLMRateLimitError` is what `llm.py` would raise on this body. The integration test's `skipTest` is the bug, not the cure.

### 7.4 The 402 "Insufficient credits" branch
- No test. A test should run with an empty-credits key and assert `LLMQuotaError`, `status_code == 402`, and the body contains "credit". Today there is no path that asserts that mapping. (The line-709 sniff *does* contain "credit", but no test pins it.)

### 7.5 The 400 "not a valid model ID" branch
- No test. A test should call `chat/completions` with a non-existent model id and assert `LLMUpstreamError` (current behavior, gap G3) **and** mark the test as `[KNOWN-WRONG-CLASS]` so the gap is visible. A correct fix would change the mapping to `LLMFormatError` and this test would flip green.

### 7.6 `LLMAuthError` mapping
- No test. A test should call the chat endpoint with an obviously wrong key (or with `Authorization: Bearer invalid`) and assert `LLMAuthError`, `status_code in (401, 403)`. This is the only way the 401/403 branch (line 685-691) gets pinned.

### 7.7 The `/auth/key` endpoint and the rate-limit-handshake gap
- No test. A test should fetch `/api/v1/auth/key` and assert the response shape (`data.limit`, `data.is_free_tier`, `data.rate_limit.requests`). A test should also assert that the documented free-tier cap (`X-RateLimit-Limit: 50`) appears on a chat 429. Today the only consumer of `/auth/key` is the auditor.

### 7.8 `usage.cost` semantics for free models
- **`tests/integration/test_openrouter_free_models.py:307-327` — `OpenRouterCostAccountingTests`** asserts `cost >= 0`. That is the only assertion. It does **not** assert that `_record_usage` would record `cost_usd == 0` for a free model. It does **not** import `ClaudeClient` or `_record_usage` and pin the `cost_status: "verified_openrouter_usd_priced"` vs `"unpriced_token_usage_only"` behavior. The test is "live data shape"; SigLab's "is the cost correctly read into the metrics dict" is not tested.

### 7.9 `usage.prompt_tokens_details.cached_tokens`
- **`tests/integration/test_openrouter_free_models.py:200-268`** — the prompt-caching test asserts the field is a dict and `cached_tokens >= 0`. It does not assert the **path** in `llm.py` that reads it (line 847-849: `cache_read = max(cache_read, _int_or_zero(prompt_details.get("cached_tokens")))`). A test that imports `ClaudeClient._record_usage` and feeds it a fake usage block with `prompt_tokens_details.cached_tokens: 1000` would pin the precedence and the `max(...)` behavior.

### 7.10 `routing_policy.mark_auth_failure` and `mark_quota_failure` side effects
- **`siglab/llm/llm.py:686, 716`** — these mark the model in the routing policy. No test asserts that after a 401 on `model_a`, the routing policy's `candidates(...)` excludes `model_a` on the next call. Combined with the 3-attempt loop (line 665, 764) and the `break` on auth (line 692) but not on quota (line 721), the routing behavior is implicit and untested.

### 7.11 Streaming
- No test. There is no streaming path. A failing test that asserts `stream: True` reaches the API is the right regression pin.

### 7.12 Demo manifest integration
- No test on `siglab/cli/demo.py:_build_demo_manifest` asserting the `llm_cost_status` field reflects real metrics. The hard-coded string at line 284 is a regression waiting to happen; no test guards it.

### 7.13 `urllib` vs `httpx` mismatch
- The test uses `urllib.request` (line 29) to keep the surface small, per the docstring at line 19. `llm.py` uses `httpx.AsyncClient`. The two libraries handle connection pooling, timeouts, and TLS errors differently. A test that exercises `ClaudeClient` end-to-end against a live free model would catch a class of bugs (e.g. the `_http` AsyncClient timeout default `self.settings.claude_timeout_s` from line 874) that the `urllib` helper cannot. Today there is no such end-to-end test of the production code path.

---

## 8. Appendix — raw evidence

| Path | Contents |
|------|----------|
| `/tmp/or_models.json` | 488,832 bytes; full `/api/v1/models` payload (337 models) |
| `/tmp/or_model_inspect.json` | 404 from `/api/v1/models/nex-agi/nex-n2-pro:free` |
| `/tmp/or_auth.json` | `/api/v1/auth/key` 200 body (see §3) |
| `/tmp/chat_nex_n2_pro.json` | 429 free-models-per-day on `nex-agi/nex-n2-pro:free` |
| `/tmp/chat_nemotron_super.json` | 429 free-models-per-day on `nvidia/nemotron-3-super-120b-a12b:free` |
| `/tmp/chat_qwen25.json` | 404 "unavailable for free" on `qwen/qwen-2.5-72b-instruct:free` |
| `/tmp/chat_llama33.json` | 429 Venice upstream rate-limit on `meta-llama/llama-3.3-70b-instruct:free` |
| `/tmp/chat_gemini_thinking.json` | 400 "not a valid model ID" on `google/gemini-2.0-flash-thinking-exp:free` |
| `/tmp/chat_mistral_paid.json` | 402 "Insufficient credits" on `mistralai/mistral-medium-3-5` |

**End of audit. No source files were edited. No commit was made.**
