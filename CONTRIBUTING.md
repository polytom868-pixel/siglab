# Contributing

## Scope

SigLab is a signal discovery loop for the SoSoValue ecosystem.
Changes should preserve two things:

- evaluator math and search behavior stay stable
- local runtime state stays out of source control

## Environment

Use Python 3.12 and Poetry.

```bash
poetry env use python3.12
poetry install
```

For commands that fetch data, run searches, or update live outputs, set
`SOSOVALUE_CONFIG_PATH` to a real config file. For most unit-test work, the
test suite uses stubs and temp paths instead.

## Repo Conventions

- Runtime outputs such as `runs/`, `data/cache/`, benchmark state, logs, and
  local databases are local-only. Do not commit them.
- Use repo-relative paths in user-facing docs and UI output.
- Keep the public repo free of local-machine path references.
- Generated live agents belong under `siglab/live/deployed_agents/`.

## Making Changes

Prefer small, testable changes.

Areas to be careful with:

- `siglab/evaluator/`: changes here can alter score semantics
- `siglab/orchestration/`: changes here can alter search behavior
- `siglab/dashboard/`: keep browser code free of absolute local paths
- `mutable/`: source definitions here affect generated family and feature surfaces

When changing shared behavior, update the nearest targeted tests rather than
relying only on one large integration run.

## Before Opening A Pull Request

Run the checks that match the touched surface.

Minimum:

```bash
python -m pytest --maxfail=1 -q
```

If you changed dashboard JavaScript, also run:

```bash
node --check siglab/dashboard/static/common.js
node --check siglab/dashboard/static/home.js
node --check siglab/dashboard/static/app.js
node --check siglab/dashboard/static/experiment.js
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
- run, deck, or signal references when relevant

Feature requests are most useful when they describe:

- the workflow that is blocked today
- the target user or operator
- the expected success criteria
