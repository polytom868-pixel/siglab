# Lesson 1 — Proof Protocol

**Lesson**: Every code change in a microchange wave must be backed by an explicit proof command, not a "should be fine" prediction. "Looks correct" is a claim; a command that exercises the changed branch is evidence.

**Rule**: Apply agents MUST run a `PROOF_COMMAND` that targets the behavior they touched and capture its output before claiming the change is done. If the proof command does not exist in the affected module's test file, the change is incomplete.

## PROOF_COMMAND template

```
PROOF_COMMAND = "pytest <path-to-test-file>::<test-that-exercises-the-branch> -x -q"
```

Required elements:

1. **Targeted test path** — the test must import / exercise the symbol or branch being changed, not a sibling test that happens to pass.
2. **Narrow selection** — use `::test_xxx` to run a single test, never a bare `pytest <dir>` when a targeted proof is feasible.
3. **Stop on first failure** — `-x` is required. A wave of microchanges must not silently accumulate skipped or xfailed tests.
4. **Quiet output** — `-q` so the transcript stays scannable; promote to `-v` only when the proof fails and you need to inspect the failure shape.

If the test file does not exist yet, write the test first, then run the proof. The test is the contract; the production change is what makes the test pass.

## Failure mode this lesson prevents

Lesson 1 was learned when Apply2 (settle 4.0 → 1.5) caused a regression that the audit's "should be fine" prediction missed. The change shipped with a hand-wave — "the parameter is monotonic, this can't break anything" — and the regression surfaced only in a downstream wave's verifier pass. The audit had predicted the change was safe; the proof command that would have caught the regression (a targeted test of the settle path at the new value) was never run. A one-line proof would have surfaced the failure at the apply step, not two waves later.

## How to apply

- Before editing: locate or write the test that covers the branch you intend to change. Note its path in your apply report.
- After editing: run the PROOF_COMMAND. Paste the trailing 10–20 lines of output in your apply report — not just "passed".
- If the test does not exist and you cannot write one for the change, the change is too large or too coupled. Stop, decompose, or escalate. Do not proceed on "should be fine."
