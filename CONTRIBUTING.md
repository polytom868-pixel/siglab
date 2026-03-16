# Contributing

## Scope

This repository is a research harness around the Wayfinder backtesting stack.
Changes should preserve two things:

- the evaluator remains the fixed accounting and scoring authority
- local runtime state stays out of source control

## Environment

Use Python 3.12 and Poetry.

```bash
poetry env use python3.12
poetry install
```

If you are running commands that fetch market data, evaluate candidates, or
promote strategies, set `WAYFINDER_CONFIG_PATH` to a real config file. For most
pure unit-test work, the test suite uses stubs and temp paths instead.

## Repo Conventions

- Runtime outputs such as `artifacts/`, `data/lake/`, benchmark state, logs, and
  local databases are local-only. Do not commit them.
- Use repo-relative paths in user-facing docs and UI output.
- Keep the public repo free of local-machine path references.
- Generated live strategies belong under
  `wayfinder_autolab/live/generated_strategies/`, not inside the Paths SDK tree.

## Making Changes

Prefer small, testable changes.

Areas to be careful with:

- `wayfinder_autolab/evaluator/`: changes here can alter score semantics
- `wayfinder_autolab/orchestration/`: changes here can alter search behavior
- `wayfinder_autolab/dashboard/`: keep browser code free of absolute local paths
- `mutable/`: source definitions here affect generated family and feature surfaces

When changing shared behavior, update the nearest targeted tests rather than
relying only on one large integration run.

## Before Opening A Pull Request

Run the checks that match the touched surface.

Minimum:

```bash
poetry run pytest --maxfail=1 -q
```

If you changed dashboard JavaScript, also run:

```bash
node --check wayfinder_autolab/dashboard/static/common.js
node --check wayfinder_autolab/dashboard/static/home.js
node --check wayfinder_autolab/dashboard/static/app.js
node --check wayfinder_autolab/dashboard/static/experiment.js
```

If you changed packaging, settings, or CLI wiring, make sure the README and
`.env.example` still describe the current behavior.

## Pull Request Expectations

- explain the user-visible or operator-visible effect
- call out evaluator, search-loop, or scoring changes explicitly
- mention any runtime migration or compatibility impact
- include the commands you used to verify the change

## Issue Triage

Bug reports are most useful when they include:

- exact command run
- relevant environment variables or config assumptions
- expected versus actual behavior
- artifact, run, or candidate references when relevant

Feature requests are most useful when they describe:

- the workflow that is blocked today
- the target user or operator
- the expected success criteria
