# Loop Supervision

## Added Controls

- `--iterations 0`
- `--max-runtime-seconds`
- `--max-total-cost`
- `--max-provider-errors`
- `--max-consecutive-no-improvement`
- `--max-consecutive-crashes`
- `--cooldown-seconds-on-429`
- `--provider-fallback-on-quota`
- `--stop-on-live-surface-unavailable`
- `--resume-safe-check`

## Stop Artifacts

Policy stops are written to `runs/loop_stops/<run_session_id>.json` with:

- `run_session_id`
- `reason`
- `created_at`
- active policy payload
- final counters relevant to the stop
- `runtime_guard_semantics` and `elapsed_runtime_seconds` for max-runtime stops

## Current Hard Boundary

Cost accounting is not implemented. `--max-total-cost` fails fast instead of pretending to enforce spend. Do not pass it until provider token/cost telemetry exists. Use runtime limits, provider-error limits, no-improvement limits, and provider-side account controls as the current practical budget boundaries.

`--max-runtime-seconds` is a cooperative between-iteration guard. It is checked before the next iteration starts; it does not interrupt a planner, writer, optimizer, or evaluator stage mid-call. Operators who need a hard wall-clock interrupt must wrap the process with an external supervisor until mid-stage cancellation is implemented.

## Live Planner Failure Semantics

- Live-provider planner fallback notes are refused after repair exhaustion.
- Planner failures are written to the trace before the CLI exits or advances policy counters.
- Provider errors increment `max_provider_errors`; non-provider planner crashes increment `max_consecutive_crashes`.
- Tool-loop exhaustion gets one forced no-tool finalization turn. If the final answer still fails semantic validation, the planner fails loudly.

## Deployment Eligibility

A spec can pass evaluation gates and still fail deployment eligibility. The CLI reports exact reason counters, for example:

- `fragility_label_fragile`
- `audit_total_return_below_minus_2pct`
- `active_bar_count_below_72`
