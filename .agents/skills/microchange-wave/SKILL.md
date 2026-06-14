---
name: microchange-wave
description: Apply one targeted SigLab edit and verify it landed safely. Use after a planner_runner / writer_runner plan produces a single concrete change. Always run the post-edit safety check (Lesson 3) and the smaller-delta gate (Lesson 4) before reporting success. Emits a microchange_card with the three CHECK_N results and the DELTA_SHIPPED flag.
---

# SigLab Microchange-Wave Playbook

Goal: every microchange (one targeted edit, one verifiable effect) lands in three states — smoke-tested by the apply agent, gated by a smaller-delta rule, and traceable to a prior plan lesson.

## Workflow

1. **Read the plan.** Locate the section's `**File:line of change:**` and the `**Test impact:**` line. If the section asserts any tolerance ("tests tolerate X", "won't break", "behaves identically"), confirm a `PROOF_COMMAND:` line is present immediately under `**File:line of change:**`. Research and audit sections may carry `**Proof required:** no -- research/audit section` instead.
2. **Compute deltas BEFORE editing (Lesson 4).** Measure `BIG_DELTA = wall_time(claimed_change) - wall_time(baseline)` and `SMARTER_DELTA = wall_time(safer_change) - wall_time(baseline)`. If `SMARTER_DELTA > 0` and the edit lands in a high-risk class (CONST, fixture scope, test body, public interface), default to `SMARTER_DELTA`. Record `DELTA_SHIPPED: BIG|SMARTER` for the card.
3. **Apply.** Run the section's `PROOF_COMMAND:` verbatim from repo root. Capture exit code, last ≥10 lines of output, and wall-clock duration. Paste under `## PROOF EVIDENCE` in the final report. If exit code ≠ 0, abort the entire plan with `ABORTED: proof failed at <verbatim PROOF_COMMAND: line>` and edit nothing. On proof pass, apply the edit.
4. **Safety check (Lesson 3).** Run the three one-liner checks against the edited file: `CHECK_1: grep -c '</input>\|</output>'` must be `0`, `CHECK_2: wc -l` must equal the pre-edit expected line count, `CHECK_3: python3 -c 'import ast; ast.parse(...)'` must exit `0`. Each check emits exactly one `CHECK_N: <result>` line. On any failure, revert (`git checkout -- <FILE>`) and emit `SAFETY_FAIL: CHECK_<N>`.
5. **Emit the microchange_card.** Fill in the template at `templates/microchange_card.template.md` with the three `CHECK_N:` results, the `DELTA_SHIPPED:` flag, the proof evidence, and the lesson numbers applied. Hand off to the run-reviewer lane (`.agents/skills/siglab-run-reviewer`).

## Rules

### Lesson 1 — Proof protocol

Any plan section that includes an assertion-tolerance claim ("tests tolerate X", "assertions survive Y", "this only changes the wait time, not the contract", or any "should be fine" / "won't break" / "behaves identically" assertion) MUST carry a `PROOF_COMMAND:` line in the form:

```
PROOF_COMMAND: <command that, when run from repo root, demonstrates the claim>
```

Constraints: runnable from `~/soso/siglab` without setup beyond `python3 -m pytest` and the dev dependencies declared in `pyproject.toml:45-51`; completes in <5 minutes; exits 0 on success and non-zero on failure; scopes to a single `TestClass::test_name`; paste-runnable as-is. Sections with no empirical claim may be labelled `**Proof required:** no` and skip verification.

The apply agent MUST execute the proof BEFORE editing any file. Order: locate section → run command verbatim → capture stdout + stderr → paste under `## PROOF EVIDENCE` with the exact command, exit code, last ≥10 lines, and wall-clock duration → proceed only if exit code == 0. On failure, abort the entire plan and bounce back to the plan agent. The apply agent MUST NOT edit any file, create any branch, or open any PR while an abort is in effect.

Failure-mode checklist:

- Proof command broken (typo, renamed test, missing pytest-timeout cap, shell-quote error): run `python3 -m pytest {test_path}::{TestClass}::{test_name} --collect-only -q` to confirm; abort with `ABORTED: proof invalid at <PROOF_COMMAND: line> -- <pytest error>`. Do NOT auto-fix the proof.
- Proof passes but sibling test regresses after the edit: revert with `git checkout -- <file>`, bounce with `BLOCKED: sibling regression at <test_path>::<failing_test>`.
- Test is order-dependent or flaky: run the `PROOF_COMMAND` three times; any non-zero exit aborts with `ABORTED: flaky proof at <PROOF_COMMAND: line> (run N of 3 failed)`.
- Proof requires credentials, network, or live boundary state: run `python3 -m siglab.cli profile --strict --json` and `python3 -m siglab.cli sodex-preflight --json`; if they pass, retry the proof; if they fail, abort with `BLOCKED: live boundary not ready at <PROOF_COMMAND: line> -- <preflight output>`.
- Proof is correct but the edit introduces a different breakage: run `python3 -m pytest {test_path} --collect-only -q` AFTER the edit; if collection fails, revert and abort with `ABORTED: edit broke collection at <file:line> -- <pytest error>`.
- Plan section has no `PROOF_COMMAND:` but introduces a behavioral change: bounce with `ABORTED: missing PROOF_COMMAND at <section heading>`. Pure-documentation or pure-rename sections are exempt when labelled `**Proof required:** no`.

Naming: `PROOF_COMMAND` and `PROOF EVIDENCE` are the only two new identifiers. SCREAMING_SNAKE matches the `WARNING` / `ERROR` / `OK` convention in `siglab/cli/helpers.py`. Not added to `pyproject.toml` markers; documentation-only tokens in the plan-and-apply contract.

### Lesson 2 — Class-scope fixtures with per-test reset helpers

When promoting a function-scoped test fixture to a wider scope, the apply agent MUST pair the scope change with per-test reset helpers that keep test isolation intact. The reference shape is the fixture at `tests/test_tui_tmux_hardening.py:208-214` and the reset helpers at `tests/test_tui_tmux_hardening.py:145-178` (`pop_to_base()`, `resize()` — each ≤5 lines).

If the scope promotion would let one test's mutations leak into another (mutation order across `TestResizeBehavior` at `tests/test_tui_tmux_hardening.py:598-682`), the apply agent MUST add an autouse reset helper and keep function-scope. The `TestDeterminism` pattern at `tests/test_tui_tmux_hardening.py:728-797` (TmuxTUI context manager, no shared fixture) is the safer alternative when isolation cannot be guaranteed.

The apply agent MUST NOT introduce shared mutable state without a reset hook. Lesson 1's proof protocol does not replace this rule: a passing proof only proves the named test tolerates the change, not that sibling tests stay isolated.

### Lesson 3 — Post-edit safety check

Three single-line checks the apply agent runs as the LAST step before reporting success. Each must produce exactly one stdout line of the form `CHECK_N: <result>`. If any check fails, the apply agent reverts and reports `SAFETY_FAIL: CHECK_<N>`.

Exact one-liner (substitute `<FILE>` for the edited path, `<EXPECTED_LINES>` for the pre-edit line count):

```bash
{ echo "CHECK_1: $(grep -c '</input>\|</output>' <FILE>)"; \
  echo "CHECK_2: $(wc -l < <FILE>) / <EXPECTED_LINES>"; \
  echo "CHECK_3: $(python3 -c 'import ast,sys; ast.parse(open("<FILE>").read())' 2>&1 >/dev/null; echo $?)"; } \
  | awk -F: 'BEGIN{want="CHECK_1: 0\nCHECK_2: <EXPECTED_LINES> / <EXPECTED_LINES>\nCHECK_3: 0"} {print $1": "$2}'
```

Notes on the contract:

- `CHECK_1` — `grep -c '</input>\|</output>'` must return `0`. Catches literal tool-emitted tags that the edit tool sometimes leaves behind. Any non-zero count is a hard fail.
- `CHECK_2` — `wc -l` must equal the expected line count, printed as `<actual> / <expected>`. The literal `/ <expected>` makes drift obvious at a glance. The expected value is whatever the apply agent recorded before the edit (plan agent supplies it; apply agent re-verifies after a stale snapshot).
- `CHECK_3` — `python3 -c "import ast; ast.parse(open('<FILE>').read())"` must exit `0`. Catches syntax errors that `grep -c` cannot see (e.g. an unclosed bracket inside a string literal, or a function body accidentally split). Echo the exit code via `$?`.

The `awk` pass is optional; its job is to format-check that every line starts with `CHECK_N:`. If the harness is plain bash, the apply agent can drop `awk` and rely on the apply-time diff between the three expected `CHECK_N:` lines and the actual output.

Failure handling: the apply agent does NOT attempt a partial revert. It runs `git checkout -- <FILE>` (or the equivalent workspace reset), emits `SAFETY_FAIL: CHECK_<N> <detail>`, and re-enters the planning lane with the lesson attached.

### Lesson 4 — Smaller-delta gating

Rule form:

> `if plan.savings > 0 and plan.risk > medium: ship the smaller-delta first, measure, then optionally escalate.`

Where `plan.risk > medium` means ANY of:

- the edit touches a `CONST` (top-level constant in a module imported by other modules),
- the edit changes a fixture scope (`scope="class"`, `scope="module"`, autouse `conftest.py`),
- the edit modifies a test body (asserts, fixtures, parametrize, or mock wiring),
- the edit changes a public interface (exported function signature, dataclass field, CLI flag).

The apply agent must compute, BEFORE editing, the `SMARTER_DELTA`:

```
SMARTER_DELTA = wall_time(safer_change) - wall_time(baseline)
BIG_DELTA    = wall_time(claimed_change) - wall_time(baseline)
```

If `SMARTER_DELTA > 0` AND `BIG_DELTA` lands in a risk class above, the apply agent ships `SMARTER_DELTA` by default. Escalation to the full `BIG_DELTA` is allowed only when:

1. the plan lane re-runs with the smaller-delta measured, and
2. a fresh risk review downgrades at least one risk axis, and
3. the new diff stays inside the safer change's footprint.

Examples from prior waves (kept as evidence):

- Settle 4.0→1.5 was over-aggressive; smaller-delta 4.0→3.0 saved 1s with materially lower regression risk. The bigger-delta was abandoned.
- Class-scope fixture change broke test isolation; smaller-delta shipped an autouse reset helper and kept function-scope. 0s saved, but the suite passed and stayed parallel-safe.

Anti-pattern (banned): shipping the bigger-delta "to get the full savings" when the smaller-delta is the demonstrably safer path. The skill reviewer (`.agents/skills/siglab-run-reviewer`) flags any lesson-1/2 retro whose `BIG_DELTA` was applied despite a positive `SMARTER_DELTA` and a high-risk axis.

## Output

Use the template at `templates/microchange_card.template.md`. The card must record:

- the three `CHECK_N:` results from Lesson 3,
- the `DELTA_SHIPPED: BIG|SMARTER` flag from Lesson 4,
- the `## PROOF EVIDENCE` block from Lesson 1 (command, exit code, last ≥10 lines, wall-clock duration),
- the lesson numbers applied (1, 2, 3, 4, or a subset).

The literal safety-check one-liner lives at `templates/safety_oneshot.template.sh`. Both templates are owned by the templates lane; this skill only points to them.

## References

Lesson files (read-only, owned by the references lane):

- `references/lesson_1_settle_threshold.md` — proof protocol; evidence from `agent://PlaybookAgent1`.
- `references/lesson_2_class_scope_fixture.md` — reset-helper pattern; evidence from `agent://PlaybookAgent2` and `tests/test_tui_tmux_hardening.py:208-214, 145-178, 598-682, 728-797`.
- `references/lesson_3_post_edit_safety.md` — `CHECK_1/2/3` one-liner; evidence from `agent://PlaybookAgent3` Section 1.
- `references/lesson_4_smaller_delta_gating.md` — `SMARTER_DELTA` rule; evidence from `agent://PlaybookAgent3` Section 2.

Related skills:

- `.agents/skills/siglab-signal-scout/SKILL.md` — input lane (plan origin).
- `.agents/skills/siglab-spec-writer/SKILL.md` — input lane (plan origin).
- `.agents/skills/siglab-run-reviewer/SKILL.md` — output lane (consumer of the `microchange_card`).

Prior wave evidence (read-only):

- `agent_workspace/audit_cli.md` — Lessons 1 & 2 audit notes.
- `agent_workspace/audit_tui_test_surface.md` — TUI fixture surface.
- `agent_workspace/audit_risk_guarded.md` — risk-guardian audit.

Contracts:

- `AGENTS.md` — repo contract; the safety check and smaller-delta rule do not contradict the live-boundary rules, the validation standard, or the hygiene list.
- `pyproject.toml:45-51` — dev dependencies (`pytest`, `pytest-timeout`, `pytest-asyncio`, `textual-dev`) used by Lesson 1 proofs.
- `siglab/orchestration/planner_runner.py`, `writer_runner.py`, `reflector_runner.py` — plan sources; not modified by this skill.
