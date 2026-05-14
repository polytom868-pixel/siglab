# Provider Routing Policy

## B.AI Defaults

- `planner_model`: `deepseek-v4-flash`
- `writer_model`: `deepseek-v4-flash`
- `reflector_model`: `deepseek-v4-flash`
- `fallback_fast_model`: `kimi-k2.5`
- `fallback_reasoning_model`: `deepseek-v4-pro`
- `context_tokens`: `70000`

## Runtime Behavior

- 403 marks the model unavailable for the current client session.
- quota/balance failure marks the model quota-blocked for the current client session.
- routed calls skip unavailable and quota-blocked models.
- writer and reflector routes skip models demoted for high latency.
- model used is written into LLM traces after fallback selection.
- provider metrics include latency, retry count, rate-limit count, transport failures, success rate, token usage when returned by the upstream provider, and routing policy snapshot.
- B.AI Credits pricing is verified from the official B.AI Pricing and Usage docs for the configured model IDs. Metrics report `credits_estimate`, `priced_tokens`, and `pricing_source` when the model is in the verified table.
- USD cost is still not priced. Metrics keep `cost_usd: null`; `--max-total-cost` refuses until a verified Credits-to-USD/account-balance policy is wired.
- `--max-total-credits` is enforced cooperatively between iterations from provider `credits_estimate`. This is a real B.AI Credits budget stop, not a dollar budget.
- `--max-call-estimated-credits` / `BAI_MAX_CALL_CREDITS` refuses a single B.AI call before HTTP when the rough token estimate and official Credits table already exceed the configured budget. This is an estimate, but it prevents obvious one-call budget blowouts.
- B.AI context pressure is estimated before calls using a cheap character/token proxy. Metrics report `context_pressure.event_count` and the latest warning/critical event. If the configured default output budget would exceed the configured B.AI context window and the caller did not explicitly set `max_tokens`, SigLab clamps output tokens before making the live call.
- Run-level provider metrics are persisted under `runs/provider_metrics/<run_session_id>.jsonl` plus `<run_session_id>.latest.json`. `telemetry-report` consumes the cumulative JSONL path and avoids double-counting the latest JSON convenience copy.
- `telemetry-report` reports `provider_metrics_status=missing` when traces exist but no provider metrics artifact exists. That is a telemetry gap, not success.

## Verified B.AI Credit Rates

These are Credits/Token, not USD. The source is the official B.AI Pricing and Usage docs.

| model | input | cache write | cache read | output |
| --- | ---: | ---: | ---: | ---: |
| `deepseek-v4-flash` | 0.14 | 0.14 | 0.003 | 0.28 |
| `deepseek-v4-pro` | 0.435 | 0.435 | 0.004 | 0.87 |
| `kimi-k2.5` | 0.59 | 0.59 | 0.177 | 3.00 |
| `gpt-5.2` | 1.75 | 1.75 | 0.175 | 14.00 |
| `gpt-5.4` | 2.50 | 2.50 | 0.25 | 15.00 |
| `gpt-5.5` | 5.00 | 5.00 | 0.50 | 30.00 |
| `claude-sonnet-4-6` | 3.00 | 3.75 | 0.30 | 15.00 |
| `claude-opus-4-7` | 5.00 | 6.25 | 0.50 | 25.00 |
| `claude-haiku-4-5` | 1.00 | 1.25 | 0.10 | 5.00 |

## Latency Demotion

For B.AI, successful writer/reflector calls above 10 seconds mark that model `latency_demoted` for the current client session. Planner calls are not latency-demoted because planner quality is allowed to spend more latency than writer/reflector stages. This is session-local and does not persist across process restarts.

## Probe Artifact

Latest probe artifact from this pass: `runs/provider_capabilities/bai_20260513T0456.json`.

Observed live probe status:

| model | reachable | entitled | quota_available | retry_count | transport_failures |
| --- | --- | --- | --- | --- | --- |
| `deepseek-v4-flash` | yes | yes | yes | 0 | 0 |
| `kimi-k2.5` | yes | yes | yes | 0 | 0 |
| `gpt-5.2` | yes | yes | yes | 0 | 0 |

Operational default remains `deepseek-v4-flash` for planner, writer, and reflector. `gpt-5.2` is reachable but must not become a default loop writer unless budget policy explicitly permits it.
