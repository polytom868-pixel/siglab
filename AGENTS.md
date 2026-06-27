# AGENTS.md

Guidance for coding agents working in SigLab.

## Mission

SigLab is a SoSoValue ecosystem research-to-action prototype. Optimize for truthful operator value, not clean-looking logs.

Never claim:

- full SoSoValue integration
- live signed SoDEX execution
- causal market prediction
- USD cost enforcement for B.AI Credits

unless the relevant code, live validation, and docs prove it.

## Core Commands

```bash
python3 -m pytest -q
python3 -m siglab.cli demo run --json
python3 -m siglab.cli demo manifest --json
python3 -m siglab.cli market-report --entity BTC --json
python3 -m siglab.cli telemetry-report --track trend_signals --json
```

## Demo Flow

Use `docs/demo-script.md` as the buildathon demo script.

The proof chain is:

1. `demo run` auto-collects SoSoValue ETF + news evidence and SoDEX REST perps quote evidence.
2. `market-report` builds operator-facing decision support from evidence files.
3. `telemetry-report` for provider/tool/evidence telemetry.
4. `sodex-preflight` for live boundary truth.
5. `demo manifest` to index the artifact set.

## Repo-Local Skills

Reuse these before creating parallel prompt systems:

- `.agents/skills/siglab-signal-scout`
- `.agents/skills/siglab-spec-writer`
- `.agents/skills/siglab-run-reviewer`

Runners:

- `siglab/orchestration/planner_runner.py`
- `siglab/orchestration/writer_runner.py`
- `siglab/orchestration/reflector_runner.py`

The workspace builder mirrors `.agents/skills` into `.claude/skills`.

## Live Boundary Rules

- SoSoValue calls must use `x-soso-api-key`.
- SoDEX signed writes must refuse unless account ID, API key name, nonce store, and signer material are configured.
- Prefer SoDEX testnet for first signed validation. See `docs/access-and-testnet-plan.md`.
- SoDEX public WebSocket support does not imply private/account stream readiness.
- ValueChain chain-id preflight is read-only readiness, not execution.
- B.AI Credits are not USD.

## Validation Standard

Add hard tests for failure paths:

- malformed evidence
- stale/duplicate evidence
- missing provider metrics
- malformed provider metrics
- missing credentials
- bad WebSocket params
- quota/credit pressure
- context pressure
- docs/code overclaim drift

Do not add mock-only tests as proof of live integration.

## Hygiene

Do not commit:

- `.env`
- `.siglab-provider.env`
- `config.json`
- wallet keys
- `runs/`
- `data/cache/`
- local DB/log files
