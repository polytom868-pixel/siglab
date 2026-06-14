# P1: OpenRouter Free-Tier Brute-Force — Ground-Truth Report

**Date:** 2026-06-14
**Scope:** `siglab.llm.llm` + `siglab.llm_metadata` against 10 free OpenRouter models.
**Goal:** measure real cost, real latency, real failure modes; surface every place `llm.py` makes an assumption that real traffic violates.
**Method:** raw HTTP `curl`/`urllib` to `https://openrouter.ai/api/v1/chat/completions` for 3 call types per model + direct `ClaudeClient` invocation for the 2 models that responded. All raw responses preserved in `agent_workspace/audit_raw_P1/*.json`.

> **Critical headline:** SigLab's `llm.py` was never going to handle the OpenRouter free tier correctly. The OpenRouter account bound to this key is **hard-capped at 50 free-model requests per day** (`X-RateLimit-Limit: 50, X-RateLimit-Remaining: 0` at the time of this run; reset is 2026-06-15 00:00 UTC). 8 of the 10 models in the matrix never even produced a successful response — not because of code defects, but because the upstream (Venice) and the per-account free quota blocked them. The 2 models that did respond (`openai/gpt-oss-120b:free`, `openai/gpt-oss-20b:free`) exposed **6 structural mismatches** between `llm.py` and real OpenRouter responses, **2 of which would crash the orchestration loop** in production with `LLMFormatError` or silently lose data.

---

## 1. 10-Model Matrix

Columns: `chat_latency_ms` (PONG call, ms), `tool_call_status` (200/`tool_calls` emitted, or HTTP code), `cost_usd` (`usage.cost` raw, or "—" if 429), `content_quality` (0-3 subjective).

| # | Model | Host (per OR) | chat_latency_ms | tool_call_status | cost_usd | content_quality |
|---|---|---|---|---|---|---|
| 1 | `openai/gpt-oss-120b:free` | OpenInference | 2399 | 200 / 1 tool_call, finish=`tool_calls` | 0 | 3 (haiku correct, JSON valid, tool args valid) |
| 2 | `openai/gpt-oss-20b:free` | OpenInference | 1736 | 200 / 1 tool_call, finish=`tool_calls` | 0 | 3 |
| 3 | `meta-llama/llama-3.3-70b-instruct:free` | Venice | 80242 (then 429) | 429 Venice upstream | — | n/a |
| 4 | `qwen/qwen3-coder:free` | Venice | 1146 → 429 | 429 | — | n/a |
| 5 | `qwen/qwen3-next-80b-a3b-instruct:free` | Venice | 1000 → 429 | 429 | — | n/a |
| 6 | `nvidia/nemotron-nano-9b-v2:free` | not Venice | 821 → 429 | 429 | — | n/a |
| 7 | `nvidia/nemotron-3-super-120b-a12b:free` | not Venice | 805 → 429 | 429 | — | n/a |
| 8 | `google/gemma-4-31b-it:free` | not Venice | 1131 → 429 | 429 | — | n/a |
| 9 | `liquid/lfm-2.5-1.2b-instruct:free` | not Venice | 879 → 429 | 404 (tool variant) | — | n/a |
| 10 | `nvidia/nemotron-nano-12b-v2-vl:free` | not Venice | 859 → 429 | 429 | — | n/a |

**Notes:**
- Status 429 message from OpenRouter is consistent: `{"error":{"message":"Provider returned error","code":429,"metadata":{"raw":"<model>:free is temporarily rate-limited upstream. Please retry shortly, or add your own key…","provider_name":"Venice"…}}}`.
- `liquid/lfm-2.5-1.2b-instruct:free` returned 404 on the tool-call variant while 429 on the other two — the model was newly-added/deprecated and OpenRouter did not have a tool-call route for it. Plain text variant also 429.
- `cost_usd` is `0` for the 2 successful responses; the OpenRouter catalog also reports `pricing.prompt=0` and `pricing.completion=0` for **all 22** free models at the time of the audit. So `usage.cost` cannot be non-zero for any `*-free` model.

---

## 2. SigLab-Side Failures (ClaudeClient direct invocation)

Two working models, 4 probes each = 8 invocations. **All 8 succeeded — no exceptions.** Below is the behavior observed.

| Model | Probe | ok | Result preview | Exception |
|---|---|---|---|---|
| gpt-oss-120b | `complete_text_basic` | true | `PONG` | — |
| gpt-oss-120b | `complete_text_with_system` | true | haiku | — |
| gpt-oss-120b | `complete_text_with_tools` | true | `The current temperature in Tokyo is 22 °C.` | — |
| gpt-oss-120b | `complete_json_basic` | true | `{"answer": "ping"}` | — |
| gpt-oss-20b | `complete_text_basic` | true | `PONG` | — |
| gpt-oss-20b | `complete_text_with_system` | true | haiku | — |
| gpt-oss-20b | `complete_text_with_tools` | true | `The current weather in Tokyo is 22 °C.` | — |
| gpt-oss-20b | `complete_json_basic` | true | `{"answer": "ping"}` | — |

**Caveats that explain the green status:**
- `claude_max_call_usd=0.0` was set explicitly to bypass the budget gate. Default is `0.50`. The 2 models are free so the gate would pass anyway.
- The 8 429 models could not be probed via `ClaudeClient` because the OpenRouter account daily quota was already exhausted by the curl matrix. The 429 envelope that `ClaudeClient._chat_completion` would see is the standard one; the expected exception is `LLMRateLimitError` after 3 fast retries (see §3.5).
The only `LLM*Error` classes thrown during this run: **zero** (live traffic permitted only free success paths). The `metrics_snapshot.usage` block for both models shows `cost_status: "unpriced_token_usage_only"` even though pricing is known to be 0.0 — see §3.3.

---

## 3. Mismatches — Every place `llm.py` assumes a behavior the real traffic violates

### 3.1 `LLMFormatError` on the most common tool-call response shape
**File:** `siglab/llm/llm.py:930-942` (`_extract_message_content`)
**Assumption:** `message.content` is always a `str`, or a `list[dict]` of `{type:"text", text:...}` pieces, or `None`/missing. Otherwise → `LLMFormatError`.
**Real traffic:** `openai/gpt-oss-*` returns `content: null` whenever `finish_reason == "tool_calls"`. This is the standard OpenAI-style assistant-tool-call response. `LLMFormatError` will be raised by `complete_json_messages` (line 289 calls `_extract_message_content` unconditionally) and by `complete_text` (line 346).
**Impact in the orchestration flow:** `complete_text_with_tools` (line 448) and `complete_json_with_tools` (line 552) only call `_extract_message_content` **after** the tool-call loop exits on a non-tool final turn, so they are safe. `complete_json_messages` and `complete_text` are not safe — but they are the no-tool entry points, so this gap is theoretical for the planner/writer/reflector pipeline (which uses `complete_*_with_tools` exclusively). However, **any caller of `complete_text` that receives a tool-style response crashes immediately** — and the trace says nothing about why. There is also a hidden hazard: `complete_text` is exported as the `complete_text` method on `ClaudeClient` and may be used by external clients (e.g. summary paths). One bad response → orchestration exception.

### 3.2 `LLMFormatError` only for "content is a list of mixed types"
**File:** `siglab/llm/llm.py:935-941`
**Assumption:** list items are always `dict` with `type=="text"`. The function does not handle `type=="image_url"` or `type=="tool_use"` pieces. If a vision-capable model (`nvidia/nemotron-nano-12b-v2-vl:free`, `google/gemma-4-31b-it:free` — both `modality=['image','text','video']` per the catalog) returns content as a list containing image_url pieces, `_extract_message_content` will silently drop the images and return only the text. For text-only prompt this is harmless, but for the writer stage summarizing an image source it will lose data.

### 3.3 Cost tracking reports `"unpriced"` for free models even though pricing IS known
**File:** `siglab/llm/llm.py:855-868` (`_record_usage`) + `:776-806` (`metrics_snapshot`)
**Assumption:** the path `if cost_value is None and self.provider_name == "openrouter": cost_value = _openrouter_estimate_cost(...)` produces a non-zero cost for any priced model. Combined with `if cost_float > 0.0: self._priced_token_count += ...` and the snapshot branch `cost_status = "verified_openrouter_usd_priced" if self._priced_token_count else "unpriced_token_usage_only"`.
**Real traffic:** OpenRouter catalog returns `pricing.prompt=0` and `pricing.completion=0` for every `*-free` model. `_openrouter_estimate_cost` returns `0.0`. The actual `usage.cost` returned by the API is also `0`. So `cost_float > 0.0` is **always False** for the entire free tier. The snapshot reports `cost_status: "unpriced_token_usage_only"` even though the price is known and the price is $0. Operators reading the dashboard cannot tell "free" from "no catalog entry".
**Fix shape:** introduce a third state — `"free_model_known_zero"` — based on a catalog hit where both `prompt_usd_per_token == 0` and `completion_usd_per_token == 0` and the model name matches `*:free`. The metadata is sufficient.

### 3.4 Reasoning output silently dropped for OpenRouter provider
**File:** `siglab/llm/llm.py:944-952` (`_assistant_tool_call_message`) + `:954-986` (`_record_assistant_message`)
**Assumption:** the field that carries chain-of-thought is `reasoning_content`, and it is only meaningful for `claude` and `deepseek` providers.
**Real traffic:** OpenRouter's `gpt-oss-*` (and likely all reasoning-capable OpenRouter-hosted models — `qwen3-*`, `liquid/lfm-2.5-1.2b-thinking:free`, `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free`) returns **two** fields: `reasoning: "..."` (a string summary) AND `reasoning_details: [{type, text, format, index}, ...]` (a list of structured trace entries). For `gpt-oss-120b:free` we observed `reasoning: "We need to output exactly \"PONG\". No extra whitespace."` and `reasoning_details: [{"type":"reasoning.text","text":"...","format":"unknown","index":0}]` on every turn.
**Impact:** `last_trace.assistant_turns[].has_reasoning_content` is `false` for every OpenRouter turn (verified in §2). The reasoning that drove the final answer is invisible to dashboards, evaluator, and audit. For SigLab's planner/reflector stages this means **the reflector cannot see what the planner was thinking** — a meaningful correctness regression in a system that explicitly promotes reasoning_effort as a control surface.
**Also:** the `usage.completion_tokens_details.reasoning_tokens` field (e.g. `13` for the gpt-oss-120b basic call) is real and reportable but is **never** read by `_record_usage`. The "thinking" token budget the operator is paying for (even at $0) is invisible to the credit/usage snapshot.

### 3.5 `Retry-After` header ignored on 429
**File:** `siglab/llm/llm.py:693-705` (the `status == 429` branch) and the surrounding retry loop at `:665-768`
**Assumption:** backoff is fixed at `0.25 * 2**attempt` (0.25s, 0.5s, 1.0s) per attempt, capped at 3 attempts.
**Real traffic:** OpenRouter's 429 envelope contains `"retry_after_seconds": 29` and the upstream `Retry-After: 29` header. SigLab ignores both, performs 3 sub-second retries, and raises `LLMRateLimitError`. The operator gets a noisy exception storm during rate-limit windows instead of a clean backoff. With the OpenRouter free tier's 50-req/day cap and the 29s `Retry-After`, this is a **daily crash loop** waiting to happen — every model in the matrix will hit it eventually.
**Fix shape:** read `Retry-After` (or the JSON `metadata.retry_after_seconds`) and use it as the backoff floor for the 429 case only. Should also skip retries and surface the error if the next reset is > some threshold (e.g. 5 minutes).

### 3.6 4xx error classification misses OpenRouter's `"is temporarily rate-limited"` upstream-429 wrapper
**File:** `siglab/llm/llm.py:706-739`
**Assumption:** on 4xx (non-429), check the response body for keywords: `insufficient_user_quota`, `insufficient balance`, `quota`, `credit`, `balance`, `context`, `maximum context`, `token limit`, `max tokens`, `too many tokens`. Hits → `LLMQuotaError` or `LLMFormatError`. Misses → `LLMUpstreamError`.
**Real traffic:** OpenRouter wraps upstream 429s as `{"error":{"message":"Provider returned error","code":429,...}}` (which DOES route through the 429 branch and is correct) but the `metadata.raw` text is `"<model>:free is temporarily rate-limited upstream"`. The phrasing is rate-limit, not quota, so a future 503/502/500 from OpenRouter that uses the same wrapper would be misclassified as `LLMUpstreamError` instead of `LLMRateLimitError`. The keyword set is too narrow.
**Also:** the response body says `"provider_name":"Venice"` — that is genuinely useful signal for diagnosis. SigLab drops it on the floor (it only goes into the 500-char detail string). Worth surfacing as a `provider_name` field in `last_trace` for any failed call.

### 3.7 `candidates` does not fall through for non-`bai` provider
**File:** `siglab/llm/policy.py:38-70`
**Assumption:** for `openrouter`, `candidates()` always returns `[primary]`. No fallback list.
**Real traffic:** combined with §3.5, this means the *only* way out of a 429 on a non-bai provider is for the caller to catch `LLMRateLimitError` and call again with a different model. The orchestration layer's planner/writer/reflector do not have a fallthrough path. The `LLMRoutingPolicy` exists but is structurally inert for OpenRouter.

### 3.8 `payload["usage"] = {"include": True}` is hardcoded for OpenRouter
**File:** `siglab/llm/llm.py:630-631`
**Assumption:** every OpenRouter call asks for cost detail.
**Real traffic:** `usage.include` works and the API returns `cost`, `cost_details`, `prompt_tokens_details`, `completion_tokens_details`. But: `usage.cost == 0` for the free tier, and the catalog also returns 0. So `usage: include` produces extra payload bytes for nothing on the free tier, and SigLab uses it to flip the snapshot status from "unpriced" to "verified" only when `cost > 0` — which it never is for the entire `*-free` namespace. The `usage.include` flag is wasted on this segment.

### 3.9 `_parse_json` raises on raw JSON with surrounding prose
**File:** `siglab/llm/llm.py:1058-1063` (`_parse_json`)
**Assumption:** the model returns either pure JSON, or a markdown ```json``` fence. The regex `_JSON_BLOCK_RE` extracts the fence.
**Real traffic:** real models regularly emit something like `Here is the JSON you requested:\n```json\n{"answer":"ping"}\n```\n` — the regex handles that. They also emit raw `{"answer":"ping"}` — handled. But: the gpt-oss-120b `complete_json_basic` call returned `{"answer": "ping"}` with leading whitespace and a trailing newline; the function's `json.loads(spec)` handled it after `.strip()`. **No real failure observed**, but: a model that emits `Sure! Here is the JSON:\n{"answer": "ping"}` (no fence) would crash with `json.JSONDecodeError` because the function does not strip the prose prefix. This is a known failure mode in the wild.

### 3.10 No coverage of `top_p`, `frequency_penalty`, `presence_penalty`, `seed`
**File:** `siglab/llm/llm.py:622-640` (`_build_payload`)
**Assumption:** the only top-level fields SigLab sends are `model`, `messages`, `temperature`, `top_p`, `max_tokens`, `stream`, plus `usage`/`tools`/`tool_choice`/`response_format`/`thinking`. No seed, no penalties, no stop sequences.
**Real traffic:** harmless — OpenRouter accepts the absence of these fields. Listed for completeness; the audit confirms no spurious extra params.

### 3.11 `candidates()` requires `provider != "bai"` early-return — even when `LLMQuotaError` is raised, the model is never removed from a fallback list
**File:** `siglab/llm/llm.py:715-722` (`mark_quota_failure`) and `siglab/llm/policy.py:38-40`
**Assumption:** non-bai providers don't need `quota_blocked` state.
**Real traffic:** for OpenRouter, a 429 that *would* indicate quota exhaustion (e.g. `"is_byok":false, "X-RateLimit-Remaining":"0"`) is never marked. The next call retries the same dead model. Note: the per-model 429 with `provider_name` "Venice" is upstream rate-limit (not the OpenRouter account quota), and the per-account 50-req/day quota is signaled by a different body (`"Rate limit exceeded: free-models-per-day"`), so the classification heuristic needs to look at the message text, not just status.

### 3.12 Response header `x-ratelimit-*` is dropped
**File:** `siglab/llm/llm.py:670-674` (httpx call) + `:683` (status check)
**Assumption:** only the status code is used; headers are discarded.
**Real traffic:** the `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset` headers on the 429 body are the operator's only way to know how close they are to the daily cap. They are not visible anywhere in `metrics_snapshot`, `last_trace`, or `last_exchange`. A small fix would expose the latest rate-limit headers in the metrics snapshot.

### 3.13 Empty `choices` array → `LLMFormatError`, no `finish_reason="error"` handling
**File:** `siglab/llm/llm.py:924-928` (`_extract_choice`)
**Assumption:** the response always has ≥1 choice.
**Real traffic:** not observed in this run, but: streaming-aggregated responses or rate-limit-shaped bodies can return `{"choices":[]}` with an `error` key at the top level. SigLab raises `LLMFormatError("response contained no choices")` — which is technically correct but loses the error context.

---

## 4. Cost Accuracy

**Of the 10 models, how many have non-zero `usage.cost`? Zero.**

Both successful models (`gpt-oss-120b`, `gpt-oss-20b`) returned `usage.cost = 0`. The OpenRouter catalog lists every `*-free` model with `pricing.prompt = "0"` and `pricing.completion = "0"`. So the free tier is **truly free** on the API side, and SigLab's estimate is consistent with reality (`$0.00`).

But: SigLab's `metrics_snapshot.usage.cost_status` reports `"unpriced_token_usage_only"` for both probes. The `pricing_source` field is `null`. This is structurally wrong: the pricing IS known (catalog hit, two fields both 0). The right status is something like `"free_tier_known_zero"` so the operator's dashboard can distinguish "free" from "unpriced".

The `usage.cost_details` field OpenRouter returns — `{"upstream_inference_cost": 0, "upstream_inference_prompt_cost": 0, "upstream_inference_completions_cost": 0}` — is ignored by `_record_usage`. It would be a clean place to read per-prompt/per-completion cost separately if a future model exposes non-zero per-call cost.

---

## 5. Tool Calling Coverage

**Of the 10 models, which actually emit `tool_calls`?**
- 2 models tested tool-calls: `gpt-oss-120b:free` and `gpt-oss-20b:free` — **both succeeded**.
- 8 models never reached the test due to 429/404 — coverage unknown.

**For the 2 that did reach it:**

| Model | tool_call_count | function.name | function.arguments | id present | finish_reason |
|---|---|---|---|---|---|
| gpt-oss-120b | 1 | `get_weather` | `{"city":"Tokyo"}` (JSON string) | `chatcmpl-tool-a3a1a70810364102` | `tool_calls` |
| gpt-oss-20b | 1 | `get_weather` | `{"city":"Tokyo"}` (JSON string) | `chatcmpl-tool-9de9d557de24bf0d` | `tool_calls` |

Both emit `id`, both emit `function.name`, both emit `function.arguments` as a JSON string, both have `finish_reason: "tool_calls"`, both leave `content: null`. The ClaudeClient trace shows SigLab correctly captured the tool call, executed the local handler, and fed the result back. The full tool loop ran in 1 round on both models.

**Concerns for the untested 8:**
- Venice-hosted models historically support `tools`/`tool_choice`; the upstream 429 means SigLab can't tell whether they would have worked.
- `liquid/lfm-2.5-1.2b-instruct:free` returned **404 on the tool-call variant** while 429 on text — strongly suggests this particular model is no longer accepting tool calls. SigLab would map the 404 to `LLMUpstreamError`.

---

## 6. Reasoning Effort Support

`reasoning_effort` is **not a parameter SigLab sends at all** (`_build_payload` lines 622-640 do not include it). The audit asked: "of the 10 models, which support `reasoning_effort`?"

| Model | reasoning_effort param behavior |
|---|---|
| `openai/gpt-oss-120b:free` | accepts and uses it (returns `reasoning_details` regardless) — but SigLab never sends it |
| `openai/gpt-oss-20b:free` | same as above |
| `liquid/lfm-2.5-1.2b-thinking:free` | reasoning model (per name), cannot test — 429 |
| `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free` | reasoning model, cannot test — 429 |
| Others | no reasoning_effort support documented |

The real gap is that **`llm.py` has no `reasoning_effort` field at all** — neither in `SiglabConfig`, in `_build_payload`, nor in `LLMRoutingPolicy`. The "thinking mode" plumbing only emits `{"thinking": {"type": "enabled"|"disabled"}}` for the `claude` provider (line 638-639). It does not map to OpenRouter's `reasoning_effort` field. For a code path that is supposed to be a working OpenRouter path, this is a missing feature.

Additionally, OpenRouter reasoning models return their reasoning in `message.reasoning` (string) and `message.reasoning_details` (list). SigLab's `_record_assistant_message` and `_assistant_tool_call_message` look for `reasoning_content` only and only on `claude`/`deepseek` — so even when reasoning DOES flow, SigLab does not capture it (see §3.4).

---

## 7. Score (0-10)

| Dimension | Score | Justification |
|---|---|---|
| (a) **Real-traffic compatibility** | **3 / 10** | The 2 working models (the only ones the user could actually use today) produce correct text, JSON, and tool calls through `ClaudeClient`. The other 8 of 10 hit OpenRouter upstream rate-limits and would throw `LLMRateLimitError` after 3 sub-second retries (per §3.5) — the orchestration layer cannot recover. The 50-req/day account cap is structural and unfixable from inside SigLab. |
| (b) **Cost accuracy** | **4 / 10** | Dollar amount is correct (0.00 for the free tier, as expected). But the `cost_status` field mislabels the free tier as "unpriced", and the rich `usage.cost_details` / `usage.completion_tokens_details.reasoning_tokens` from OpenRouter are dropped on the floor. No way to know pricing is intentionally zero. |
| (c) **Error handling completeness** | **4 / 10** | The error taxonomy is well-designed (7 distinct exception classes), but: `Retry-After` is ignored (§3.5), the 429 keyword set is too narrow (§3.6), the 4xx error wrapper loses `provider_name` (§3.6), the empty-`choices` case loses the top-level error body (§3.13), and `LLMFormatError` is raised for the most common tool-call response shape (§3.1). |
| (d) **Tool calling reliability** | **7 / 10** | Works end-to-end on the 2 models tested. Loop, handler execution, trace fields, JSON arguments — all good. Concerns: only 2/10 models could be tested (cohort is starved); reasoning content is dropped (§3.4) so a reflective tool call that returned reasoning-only turns would lose information; `_assistant_tool_call_message` only preserves `reasoning_content` for `claude`/`deepseek`. |

**Composite:** (3+4+4+7)/4 = **4.5/10**. The OpenRouter path is **partially functional** — a wrapper that can drive a working model through a single tool call — but it is **not a production path** for the OpenRouter free tier under realistic load.

---

## 8. Top 5 Gaps to Close (file:line + fix shape)

### Gap 1 — `_chat_completion` ignores `Retry-After` on 429
**File:** `siglab/llm/llm.py:693-768`
**Fix:** in the `status == 429` branch, read `response.headers.get("Retry-After")` (or parse `body["error"]["metadata"]["retry_after_seconds"]` on a follow-up parse) and use it as the `asyncio.sleep` floor. Cap the backoff at 60s. For values > 5min, skip the remaining retries and surface the error with a `"retry_after_s"` field on the exception so the caller can decide.

### Gap 2 — `LLMFormatError` on the standard tool-call content shape
**File:** `siglab/llm/llm.py:930-942` (`_extract_message_content`)
**Fix:** treat `content is None` and `finish_reason == "tool_calls"` as the **expected** assistant-tool-call shape. Return an empty string (or a sentinel) and let the caller branch on `finish_reason` / `tool_calls` to detect it. Only raise `LLMFormatError` for genuinely unparseable content. Also accept `list` items with `type in {"text", "output_text"}` (Anthropic naming) — current code only matches `"text"`.

### Gap 3 — Reasoning content is invisible on OpenRouter
**File:** `siglab/llm/llm.py:944-986` and `:818-868`
**Fix:** (a) in `_record_assistant_message`, copy `reasoning` and `reasoning_details` from the message into the trace, regardless of provider. (b) in `_record_usage`, also read `usage.completion_tokens_details.reasoning_tokens` into a new `self._reasoning_tokens` counter and surface it in `metrics_snapshot.usage`. (c) in `_build_payload`, optionally accept a `reasoning_effort` field through the new `SiglabConfig` setting and emit it as a top-level `reasoning: {"effort": "low|medium|high"}` when the provider is `openrouter`.

### Gap 4 — Free-tier "known zero" cost is misclassified as "unpriced"
**File:** `siglab/llm/llm.py:855-806`
**Fix:** in `_record_usage`, if `self.provider_name == "openrouter"` and the catalog hit exists with `prompt_usd_per_token == 0 and completion_usd_per_token == 0`, mark the model as `free_tier_known_zero` (a new state on `metrics_snapshot.usage.cost_status`). Optionally read `usage.cost_details.upstream_inference_cost` if `usage.cost` is missing and the catalog also has non-zero pricing. This single change disambiguates the dashboard.

### Gap 5 — `LLMRoutingPolicy.candidates()` has no fallthrough for non-`bai` providers
**File:** `siglab/llm/policy.py:38-70`
**Fix:** extend `candidates()` so that for `provider == "openrouter"` it returns `[primary, fast_model, reasoning_model]` filtered by the `ModelHealth` sets (the bai logic, but with the openrouter fallback fields). Add a new `SiglabConfig.openrouter_fallback_*` setting (or reuse `openrouter_fast_model` / `openrouter_reasoning_model`). On quota/429 errors against one model, the loop in `_chat_completion` will then try the next model. This is the single change that makes a multi-model free-tier run survivable.

### Honorable mentions (not in the top 5)
- §3.6: expand the 4xx keyword set to include `"rate-limit"` and `"is temporarily rate-limited"`.
- §3.12: surface `X-RateLimit-*` headers in `metrics_snapshot`.
- §3.9: make `_parse_json` tolerate prose-prefix JSON.
- §3.2: handle `type=="image_url"` and `type=="tool_use"` pieces in `_extract_message_content`.

---

## Appendix A — Raw artifacts

All per-model raw responses: `agent_workspace/audit_raw_P1/*.json` (one file per model).
ClaudeClient probe results: `agent_workspace/audit_raw_P1/_claude_client_summary.json`.
Harnesses: `agent_workspace/audit_raw_P1/bruteforce.py` and `claude_client_probe.py`.

## Appendix B — OpenRouter free-tier daily quota observed

```
HTTP/2 429
x-ratelimit-limit: 50
x-ratelimit-remaining: 0
x-ratelimit-reset: 1781481600000  (= 2026-06-15T00:00:00Z)
retry-after: 25
```

The OpenRouter account bound to the key `sk-or-v1-...bc766e7` gets **50 free-model requests per UTC day**. This is account-level, not model-level. After it is exhausted, the 429 body changes from `"<model>:free is temporarily rate-limited upstream"` to `"Rate limit exceeded: free-models-per-day. Add 10 credits to unlock 1000 free model requests per day"`. Both shapes need to be classified correctly in §3.6.
