# B.AI (Byaigc100 / baichuan / b.ai) — External Audit

**Author:** ResearchBAI (external research auditor)
**Date:** 2026-06-14
**Subject:** SigLab's claim that "B.AI Credits are not USD and must not be presented as USD spend" (`siglab/cli/demo.py:297`, echoed at `siglab/llm/claude.py:546,732,733` and `AGENTS.md:14`).

---

## 1. URLs visited (primary sources only)

| # | URL | Purpose |
|---|---|---|
| 1 | https://docs.b.ai/llmservice/pricing-and-usage | Official B.AI pricing/credits page (platform conversion + per-model table) |
| 2 | https://docs.b.ai/llmservice/api | Official B.AI API reference (auth, endpoints, request/response shape) |
| 3 | https://docs.bankofai.io/llmservice/pricing-and-usage | "Bank of AI" mirror of the B.AI pricing page (same platform, same `1 USD = 1,000,000 Credits` line) |
| 4 | https://github.com/baichuan-inc/Baichuan2/issues/14 | Baichuan historical issue confirming `api.baichuan-ai.com/v1/chat/completions` Bearer pattern (legacy vendor) |
| 5 | https://platform.baichuan-ai.com/docs/api | Baichuan's own portal (legacy Chinese-language docs; unrelated to B.AI; included for disambiguation only) |
| 6 | `file:/home/eya/soso/siglab/siglab/llm/claude.py` | SigLab's B.AI wrapper |
| 7 | `file:/home/eya/soso/siglab/siglab/cli/demo.py` | Demo manifest with the "red flag" claim |
| 8 | `file:/home/eya/soso/siglab/AGENTS.md` | Repository-level forbidden claim |

Search-only URLs (used for triangulation, not cited as evidence): web searches for "B.AI API documentation", "b.ai credits pricing", "Byaigc100 API developer portal", "baichuan API B.AI chat completions auth", "api.b.ai chat completions endpoint auth base URL", "b.ai 1 USD 1000000 credits platform wide conversion rate".

---

## 2. What the official B.AI docs actually say

### 2.1 Auth, endpoints, request/response schema (https://docs.b.ai/llmservice/api)

- **Base URL:** `https://api.b.ai`
- **Version:** 1.0, OpenAPI 3.1.0
- **Authentication (two equivalent forms):**
  - `Authorization: Bearer <token>` — HTTP Bearer, format `sk-xxx` (same value as the API key)
  - `x-api-key: <your-api-key>` — API Key header
  - "The `Chat Completions` and `Messages` endpoints both accept either `x-api-key` or `Authorization: Bearer <token>`. In practice, both use the same platform-issued secret."
- **Endpoints documented:**
  1. `GET /v1/models` — list models, returns `{object, success, data: [{id, object, created, owned_by, supported_endpoint_types}]}` (OpenAI-compatible `list` shape).
  2. `POST /v1/chat/completions` — OpenAI-compatible, `Bearer Token`, body fields: `model` (required), `messages` (required), `stream`, `max_tokens`, `temperature`, `top_p`, `stop`, `n`, `frequency_penalty`, `presence_penalty`, `seed`, `response_format`, `tools`, `tool_choice`, `user`, `web_search_options`. Response includes standard `usage` object (`prompt_tokens`, `completion_tokens`, `total_tokens`, `prompt_tokens_details.cached_tokens`, `completion_tokens_details.reasoning_tokens`, etc.).
  3. `POST /v1/messages` — Anthropic Messages-compatible, `x-api-key` or Bearer.
- **Status codes documented:** 200, 400, 401 (invalid/missing auth), 403 (insufficient quota / banned), 429 (rate limit), 500, 502, 503.

### 2.2 Pricing, credits, USD (https://docs.b.ai/llmservice/pricing-and-usage)

> **"Platform-wide Credits conversion: `1 USD = 1,000,000 Credits` (`1M` / `1000K` Credits)."**

Verbatim, on the platform's own pricing page. Equivalently `1 credit ≈ $0.000001`.

- **How Credits are calculated:** "The number of tokens consumed in each interaction is converted into Credits based on the pricing of the selected model and deducted from your account balance."
- **Full per-model table** (Input / Cache-Write / Cache-Read / Output credits per token + Web-Search credits per use). Examples actually shipped in the docs:
  - GPT-5.2: input 1.75, cache_write 1.75, cache_read 0.175, output 14.00, web_search 10,000
  - Claude Sonnet 4.6: input 3.00, cache_write 3.75, cache_read 0.30, output 15.00, web_search 10,000
  - Claude Opus 4.6: input 5.00, cache_write 6.25, cache_read 0.50, output 25.00, web_search 10,000
  - DeepSeek V3.2: input 0.29, output 0.44
  - Gemini 3 Flash: input 0.50, output 3.00
  - GLM-5.1: input 1.40, output 4.40
  - Kimi K2.5: input 0.59, output 3.00
  - **MiniMax M3** (the model that runs this auditor): input 0.30, cache_write 0.30, cache_read 0.06, output 1.20
- **Worked example in the docs themselves:** "If you use GPT-5.2 to ask a question (10 input tokens) and the AI responds with an answer (50 output tokens), the entire dialogue consumes 717.5 credits (calculated as: `10 × 1.75 + 50 × 14`)."
- **Cache worked example:** "Claude Sonnet 4.6 with 1000 tokens cached — first request (cache write): 1000 × 3.75 = 3,750 credits; subsequent (cache read): 1000 × 0.30 = 300 credits — a 90% savings."
- **Subscriptions, all denominated in USD:**
  - Plan Pro: **$200/month**, ~50–500 messages per 12h, requires invite code.
  - Plan Max: **$2,000/month**, ~500–5,000 messages per 12h.
  - "Subscription usage is measured within a rolling 12-hour window, and capacity is gradually released over time." When allowance runs out, system "automatically begins consuming top-up Credits from your account balance."
- **Pricing note in the docs themselves:** "Prices shown in the documentation are B.AI standard reference prices for base billing purposes. B.AI may provide lower actual usage costs through top-up bonuses and account benefits. Specific prices, bonus Credits, and account benefits are subject to the platform display and final billing records."
- **Cross-confirmed at https://docs.bankofai.io/llmservice/pricing-and-usage** (same page, different host, same `1 USD = 1,000,000 Credits` and same table). This is B.AI's own mirror — listed as an official trusted access URL on the docs themselves: `https://chat.bankofai.io/chat`.

### 2.3 So what the docs do — and do not — say

- **Yes**, the docs publish a platform-wide, fixed, fiat-denominated credit conversion. `1 USD = 1,000,000 Credits` is a number the platform itself prints, in a callout box, on the canonical pricing page.
- **No**, the docs do not say "Credits are not USD." They say "Credits" is the unit; the platform tells you, line by line, how many USD those Credits correspond to.
- **No**, the docs do not say "do not convert to USD." In fact every subscription is quoted in USD ($200, $2,000) and the only caveat (top-up bonuses) is itself expressed in credits against a USD reference.

---

## 3. What SigLab actually claims (file:line)

| Site | Text | File:line |
|---|---|---|
| Demo red flags (HTML + JSON manifest) | `"B.AI Credits are not USD and must not be presented as USD spend."` | `siglab/cli/demo.py:297` |
| Readiness flags | `"usd_cost_claimed": False` | `siglab/cli/demo.py:283` |
| Readiness flags | `"causality_claimed": False` | `siglab/cli/demo.py:282` |
| B.AI wrapper, credit pressure event | `"usd_priced": False` | `siglab/llm/claude.py:546` |
| B.AI wrapper, telemetry summary | `"cost_usd": None` | `siglab/llm/claude.py:730` |
| B.AI wrapper, telemetry summary | `"cost_status": "verified_bai_credit_estimate_usd_unpriced"` | `siglab/llm/claude.py:731-735` |
| Repo guidance | `Never claim: ... USD cost enforcement for B.AI Credits` | `AGENTS.md:14` |

**SigLab's own internal state, however, contradicts the claim:**

- `siglab/llm/claude.py:30-63` — full 33-entry `BAI_CREDITS_PER_TOKEN` table, all four rates per model, **hardcoded directly from the B.AI pricing page**. Source URL is even cited inside the file at `siglab/llm/claude.py:545,737` as `https://docs.b.ai/llmservice/pricing-and-usage/`.
- `siglab/llm/claude.py:790-801` — actual credit accounting: `self._usage_credits += (standard_prompt * input_rate) + (cache_write * cache_write_rate) + (cache_read * cache_read_rate) + (completion * output_rate)`. This is the platform's own published formula, applied at runtime.
- `siglab/llm/claude.py:528-557` — runtime guard `BAI_MAX_CALL_CREDITS` that **does refuse calls** when the platform-formula estimate exceeds the configured cap. The refuse-message text is `"B.AI estimated call credits {x:.6f} exceed BAI_MAX_CALL_CREDITS={y:.6f}"`.
- `siglab/llm/claude.py:1024-1031` — `_estimate_bai_credits(...)` helper applying the platform's input/output rates.
- Headers actually sent to B.AI (`siglab/llm/claude.py:836-852`): both `Authorization: Bearer <key>` AND `x-api-key: <key>`, matching the docs verbatim.

So SigLab:
1. Has the B.AI credit table copy-pasted into source.
2. Computes per-call credit cost using the platform's own formula.
3. Refuses calls when the credit estimate exceeds a cap.
4. Cites the B.AI pricing URL in its own telemetry.
5. Then asserts, in user-facing output, that "Credits are not USD" and "usd_priced: False" and "cost_usd: None" — i.e. it has the number in hand and refuses to print it.

---

## 4. Point-by-point comparison: claim vs reality

| # | SigLab claim | Official reality | Verdict |
|---|---|---|---|
| 1 | "B.AI Credits are not USD" (`demo.py:297`, `AGENTS.md:14`) | B.AI docs publish `1 USD = 1,000,000 Credits` in a callout block at the top of `pricing-and-usage`. USD is the unit in which Plans are denominated ($200/mo, $2,000/mo). | **False.** Credits are a fixed-rate prepaid token against a USD balance. The platform itself provides the conversion. |
| 2 | "usd_cost_claimed = False" (`demo.py:283`) | The internal estimator at `claude.py:790-801,1024-1031` is the B.AI-published formula, and a USD conversion is a one-line divide-by-1,000,000. SigLab already does the math; it just won't say it. | **Self-contradictory theater.** |
| 3 | "usd_priced: False" (`claude.py:546`) | Same data path; the field name is the only thing keeping the value out of output. | **Theatrical flag, not a substantive safety claim.** |
| 4 | "cost_usd: None" / "cost_status: ..._usd_unpriced" (`claude.py:730-735`) | Field is intentionally null while the source values needed to populate it are sitting in the same dict (`usage_credits`, `priced_tokens`, `pricing_source`). | **Disabling a number that is already known.** |
| 5 | "Never claim: USD cost enforcement for B.AI Credits" (`AGENTS.md:14`) | The repo's own `BAI_MAX_CALL_CREDITS` is exactly a credit-budget cap. Since credits have a published USD rate, this is enforced USD cost, just with the unit name changed. | **The cap IS a USD cost cap with cosmetic renaming.** |
| 6 | (implicit) "We don't know the credit-per-request cost" | SigLab hardcodes the per-token rates for 33 models in `BAI_CREDITS_PER_TOKEN` and uses them to refuse calls. | **They have the number. They hide it.** |
| 7 | (implicit) "There is no honest USD-equivalent we could report" | Divide `usage_credits` by 1,000,000. Cite the platform's own rate. Round to whatever precision is sensible. | **Trivial.** A four-line patch. |
| 8 | (implicit) "This is a safety claim, not a hedge" | A safety claim protects users from a lie. The B.AI pricing page is the lie's source of truth. Pretending 1 USD = 1,000,000 Credits doesn't exist while the math runs anyway does not protect users — it just makes the tool less auditable. | **Bad safety, good marketing.** |

---

## 5. Brutal verdict: B.AI / LLM half

### 0.3 / 10

The B.AI wrapper itself is real and competent: real auth headers, real URL, real request/response shape, real per-model rate table, real credit estimator, real per-call guard, real telemetry. That part is **not** what's broken.

What's broken is the **truthfulness claim** the project hangs its credibility on. SigLab publicly asserts "B.AI Credits are not USD" on the same codebase that, on the previous code path, copy-pastes the B.AI credit table, computes cost with it, and refuses calls when cost exceeds a cap — and that table comes with a printed USD conversion the platform publishes in the same callout as the table.

The claim is:
- factually false (the platform publishes the conversion);
- internally self-contradictory (the same package computes the cost it says it can't compute);
- not load-bearing (the cap works whether the unit is called "credits" or "USD-cents");
- and actively harmful in the only sense that matters here: it makes the demo panel lie about the platform it's billing against. A reviewer who trusts the red flag will be wrong about the platform; a reviewer who reads `claude.py` will see the lie.

It's not even hedging. It's a costume.

### What would change the score (concrete, not vibes)

- Drop the "not USD" claim from `demo.py:297` and `AGENTS.md:14`.
- Populate `cost_usd` in the telemetry summary (`claude.py:730`) with `usage_credits / 1_000_000` and a `pricing_source` already in the same struct.
- Add a `cost_usd_estimate` to the demo readiness dict next to `usd_cost_claimed`, defaulting to None only when no priced tokens were seen.
- Cite the platform URL in the field next to the number, not as a hidden source string.

If SigLab did those four things the score jumps to 7/10, because the only thing missing would be live-validated `top_up`/subscription telemetry and end-to-end reconciliation against B.AI's `/usage` page (out of scope for this audit, but possible).

---

## 6. Top 3 worst gaps (file:line)

1. **`siglab/cli/demo.py:297`** — The headline lie. Prints `"B.AI Credits are not USD and must not be presented as USD spend."` to the buildathon demo manifest. B.AI's own pricing page opens with `1 USD = 1,000,000 Credits` (https://docs.b.ai/llmservice/pricing-and-usage). The manifest is what reviewers and the buildathon panel see first. This is the single most damaging line in the file.

2. **`AGENTS.md:14`** — `Never claim: USD cost enforcement for B.AI Credits`. The instruction tells future agents to keep the lie alive. Worse, the `BAI_MAX_CALL_CREDITS` cap at `claude.py:530-557` is, in functional terms, USD cost enforcement with a different unit name. Future agents are told to hide a feature that already exists.

3. **`siglab/llm/claude.py:730-735`** — `cost_usd: None` and `cost_status: "verified_bai_credit_estimate_usd_unpriced"` in the telemetry summary. The same summary already carries `credits_estimate`, `priced_tokens`, and `pricing_source: "https://docs.b.ai/llmservice/pricing-and-usage/"`. This is the moment in the data path where honesty is one arithmetic op away and the code refuses to do it. Fixing this single dict is the highest-leverage honesty fix in the package.

Honorable mentions (not in the top 3 but worth recording):
- `siglab/llm/claude.py:546` — `"usd_priced": False` in the credit-pressure event; same pattern, harder to find.
- `siglab/cli/demo.py:283` — `"usd_cost_claimed": False` in readiness; same lie in a different field name.

---

## 7. Verdict on the four framing questions

**(a) Is the claim "Credits are not USD" factually true given the docs?**
No. https://docs.b.ai/llmservice/pricing-and-usage states `1 USD = 1,000,000 Credits` directly. Credits are a fixed-rate prepaid credit against a USD balance. They are not USD in the same way a prepaid phone minute is not a dollar — but the platform publishes the conversion, denominates its own subscriptions in USD, and exposes the rate in the same UI as the per-model table.

**(b) Does SigLab even know the credit-per-request cost?**
Yes. `siglab/llm/claude.py:30-63` hardcodes 33 models' input/cache-write/cache-read/output rates directly from the B.AI pricing page. The estimator at `claude.py:790-801,1024-1031` uses those rates to accumulate `_usage_credits` at runtime. A pre-call guard at `claude.py:528-557` raises `LLMQuotaError` when the platform's own formula projects a call over the configured cap. SigLab has the number; it just calls it "credits" and refuses to print it as USD.

**(c) Is there a USD-equivalent that SigLab could honestly report?**
Yes. `usage_credits / 1_000_000` is the platform's own published rate. Every value needed to compute it (`usage_credits`, `priced_tokens`, `pricing_source`) is already in the telemetry summary at `claude.py:727-740`. No new data, no new dependency. A four-line change.

**(d) Is the "red_flag" claim helpful or just theater?**
Theater. A useful red flag protects users from a real risk (e.g. "this endpoint is rate-limited; do not use it for hot-path trading"). This one protects SigLab's narrative from being contradicted by its own telemetry. It is a "do not look here" sign on a door that opens onto the same room the visitor is already standing in.
