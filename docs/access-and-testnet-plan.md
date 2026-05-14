# Access And Testnet Plan

Generated: 2026-05-14

This file exists because access blockers must be handled early, not hidden at the end of the build.

## Current Access Truth

| Surface | Current state | Validation status | Blocker |
| --- | --- | --- | --- |
| SoSoValue OpenAPI | Repo-local config supported | live evidence path works when key is configured | none when `config.json` or `SOSOVALUE_API_KEY` is present |
| B.AI | Repo-local `.siglab-provider.env` supported | live provider path and no-HTTP credit guard tested | USD/balance API not verified |
| SoDEX public REST | no secret required | public reads implemented/live-probed | none for public reads |
| SoDEX public WebSocket | no secret required | public `allBookTicker` implemented/live-probed | no daemon/supervisor yet |
| SoDEX private/account WebSocket | user address/accountID required | params now preflight-validated | operator account details required |
| SoDEX signed writes | signer/account/API key/nonce store required | dry-run only | credentials, signer, accountID, funded/test account |
| ValueChain | public RPC | chain-id preflight | no contract/index integration |
| SSI/Index | official product docs only | not integrated | official contract addresses or callable data source required |

## Required Operator Inputs

For first real signed validation, use testnet before mainnet:

```bash
SODEX_ENVIRONMENT=testnet
SODEX_API_KEY_NAME=...
SODEX_ACCOUNT_ID=...
SODEX_NONCE_STORE_PATH=./runs/sodex_nonce_store.json
SODEX_PRIVATE_KEY=...
```

Mainnet requires both an explicit recorded testnet-pass flag and an explicit operator risk confirmation:

```bash
SODEX_ENVIRONMENT=mainnet
SODEX_TESTNET_PREFLIGHT_PASSED=true
SODEX_MAINNET_LIVE_WRITE_CONFIRMATION=I_UNDERSTAND_MAINNET_RISK
```

Private/account WebSocket validation additionally needs:

```bash
SODEX_USER_ADDRESS=0x...
```

Do not use a funded main wallet for initial validation. Use an isolated signer.

## Required Checks Before Any Live Write Claim

1. `python3 -m siglab.cli sodex-preflight --json` returns `live_write_allowed: true`.
2. `SODEX_ENVIRONMENT=testnet` passes first.
3. Nonce store path has an existing writable parent, the file is writable when present, and any existing file is parseable JSON.
4. Signed preview payload matches canonical serializer tests.
5. Operator confirms account and chain are supported.
6. Public rate-limit budget is not exceeded.
7. No command writes live artifacts when preflight fails.

## Mainnet Restrictions

Mainnet validation may require deposit/account setup and supported-chain checks. Treat mainnet as blocked until operator explicitly provides account details and confirms funding/chain requirements.

## Buildathon Access Timing

If SoSoValue or SoDEX buildathon-specific access is available through a request form, request it before further live-validation work. Missing access blocks only the live proof, not dry-run signing, preflight, docs, tests, or public-read validation.
