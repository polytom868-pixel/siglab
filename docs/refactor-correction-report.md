# Refactor Correction Report

Generated: 2026-05-14

This report names what the backend-to-SigLab refactor fixed, what remains legacy-shaped, and what still needs removal or stronger tests.

## Refactored Correctly

| Area | Evidence |
| --- | --- |
| Central configuration | `siglab/config.py` replaces scattered settings and supports repo-local provider/SoSoValue paths. |
| SoSoValue client ownership | `siglab/data/sosovalue_client.py` centralizes auth, retries, cache, dedupe, metrics, and typed errors. |
| SoDEX live boundary | `siglab/live/sodex_client.py`, `siglab/live/sodex_signing.py`, and CLI preflight refuse fake signed writes. |
| LLM provider routing | `siglab/llm/claude.py` and `siglab/llm/policy.py` route B.AI/OpenRouter/Claude-style calls with fallback state. |
| Runtime loop posture | CLI supports run labels, resume, policy stops, telemetry reports, and strict profile. |
| Evidence normalization | `siglab/data/evidence.py` maps SoSoValue ETF/news and SoDEX WebSocket snapshots into source-backed records. |

## Refactored Incorrectly Or Still Legacy-Shaped

| Area | Problem | Current mitigation | Needed next fix |
| --- | --- | --- | --- |
| Deleted/renamed data modules | `siglab/data/lake.py` and `siglab/data/providers.py` are deleted in git status while imports moved to `store.py` and `feeds.py`. | Strict profile imports pass. | Add a no-legacy-import test that fails if old module names reappear or are referenced. |
| Kimi naming | Tests and classes still use `test_kimi_tools.py` / Kimi-era names while provider layer now covers B.AI/OpenRouter/Claude. | Behavior tests pass. | Rename tests/classes later; do not do it mid-pass unless necessary because it is churn-heavy. |
| Provider class name | `ClaudeClient` is now a generic chat-completions client. | Docs explain provider routing. | Rename to `LLMClient` only with broad mechanical refactor and compatibility tests. |
| WebSocket dependency | `websockets` existed transitively in lock file but not in `pyproject.toml`. | Added explicit dependency. | Keep dependency hygiene test. |
| SoSoValue module coverage | Capability matrix blocks unverified modules instead of implementing them. | Honest maps and gap report. | Continue official-doc discovery and implement only verified endpoints. |
| Runtime/export boundary | Live export refuses unsupported/signed-missing paths, but real SoDEX signed execution still unproven. | Preflight and dry-run signing tests. | Live credential validation when credentials exist. |

## Hidden Broken Parts Found And Fixed This Pass

- WebSocket layer was absent despite official SoDEX WebSocket docs. Fixed with `SoDEXWebSocketClient`, tests, CLI probe, and live public stream artifact.
- Account-read wrappers initially returned an un-awaited coroutine. Fixed and tested.
- B.AI credit table drifted again versus current official docs. Fixed to current Credits/Input/Cache Write/Cache Read/Output rates and regression-tested with cached-token and Kimi cases.
- WebSocket keepalive test initially did not simulate idle correctly. Fixed fixture to force timeout then ping.

## Still Unsafe

- No distributed SoDEX REST rate limiter.
- No persistent WebSocket stream daemon/supervisor.
- No live signed order validation.
- No real Index/SSI/Macro/Treasury/Fundraising data ingestion.

## Required Next Refactor Tests

1. No legacy import references for deleted modules.
2. Dependency hygiene: directly imported third-party packages must be declared in `pyproject.toml`.
3. Runtime/export manifest compatibility test for SoDEX + evidence graph artifacts.
