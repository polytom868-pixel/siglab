# Lesson 4 — Smaller Delta

**Lesson**: When an apply step's proof command fails, the reflex is to keep widening the change until it "works." That is the wrong direction. Each widening step compounds the diff and obscures the original fault. The right move is to narrow the change.

**Rule**: A failed proof command triggers a **shrink**, not a **search-and-replace**. Halve the parameter change, drop the optional branch, defer the second behavior, and re-run. You may broaden only after three consecutive narrower attempts each produce a passing proof.

## Smaller-delta gating rule

```
def should_keep_going_after_proof_failure(failed_attempt: int, last_passing_delta: str | None) -> str:
    """Return the next action after a PROOF_COMMAND failure.

    failed_attempt = number of consecutive failed proof runs in this apply step.
    last_passing_delta = the (smaller) delta that DID pass, or None if none yet.
    """
    if failed_attempt == 0:
        return "stop_and_investigate"  # never reached; first failure is attempt 1
    if failed_attempt == 1:
        return "halve_the_delta"        # change magnitude → 0.5x
    if failed_attempt == 2:
        return "drop_optional_branch"   # keep main change, remove any "and also…" lines
    if failed_attempt == 3 and last_passing_delta is not None:
        return "ship_smaller_delta"     # the half-step is acceptable; ship it, log the rest
    if failed_attempt >= 4:
        return "escalate"               # three shrinks failed → out of scope for this wave
```

Enforcement contract:

1. **Attempt 1 fails → halve the delta.** `4.0 → 1.5` becomes `4.0 → 2.75`. Re-run the proof. Do not debug the original failure; debug the halved version.
2. **Attempt 2 fails → drop the optional branch.** The original change had a primary edit and a "while we're at it" edit. Keep the primary, drop the secondary. Re-run.
3. **Attempt 3 fails, but a smaller delta passed previously → ship the smaller delta.** The full target is out of reach for this wave; the smaller delta is the deliverable. Log the remaining gap in the apply report.
4. **Attempt 4+ fails → escalate.** The change is too entangled for this microchange wave. Stop, write a handoff to the next wave, and do not force a fix.

Hard prohibitions:

- No "let me just patch around it" — that's a search-and-replace, not a shrink.
- No "let me skip the failing test" — tests exist to gate this exact shape of change.
- No "let me just update the expected value" — that's the proof lying to you.

## Failure mode this lesson prevents

Lesson 4 was learned when an apply step tried to change `settle` from 4.0 to 1.5 in one shot, hit a regression, and the apply agent responded by editing four sibling files to make the proof pass. The diff ballooned from one parameter to a cross-module refactor. The verifier caught it, but the audit cost of reviewing the expanded diff exceeded the cost of doing the change correctly in two waves. The smaller-delta rule would have forced the apply to ship `4.0 → 2.75` cleanly, log `1.5` as the next-wave target, and leave the audit lane with a one-line diff to review.

## How to apply

- First failure is data, not defeat. Halve. Re-run.
- Track `failed_attempt` in the apply report — the gating rule is observable, not implicit.
- If you find yourself editing files that are not on the original proof path, you are widening, not shrinking. Stop and re-apply this rule.
- A shipped smaller delta with a logged next-wave target is a success, not a partial. The next wave picks it up; the audit lane stays narrow.
