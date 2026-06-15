# SigLab Module Contracts & Boundaries — Research Report (iter 17)

## 1. Top 5 Python module-contract patterns

1. **`typing.Protocol`** — structural interface, no inheritance required. `@runtime_checkable` (3.12+) adds `isinstance`. Best for cross-module boundaries where the implementer is owned elsewhere.
2. **`abc.ABC` + `@abstractmethod`** — runtime-enforced abstract base. Best when instantiation of an incomplete class must raise, or when concrete helpers should be inherited.
3. **`typing.TypedDict`** (`total=False` partial, `total=True` strict) — zero-runtime-cost schema for dicts crossing a boundary. Best for orchestration pipeline contracts that are JSON-serializable shapes.
4. **`@dataclass(frozen=True, slots=True)`** — immutable value object, cheap construction. Best for read-only DTOs shared between layers (hashable, safe by reference).
5. **`Protocol` + `ABC` combo** — `Protocol` for static checking, thin `ABC(Protocol)` for runtime guard. 2025 best practice when both compile-time and runtime enforcement are needed.

## 2. Top 3 anti-patterns to avoid

- **Leaky abstraction / god module** — exposing concrete `httpx`, `eth_account`, or filesystem paths. SigLab risk: `sodex_signing.py` re-exports 25+ internals via `siglab.live.__init__`; only `SoDEXSigner` / `SoDEXSignedRequest` are true contract.
- **`dict[str, Any]` as a contract** — untyped payloads bypass mypy. SigLab already has the right answer in `orchestration/contracts.py` (TypedDicts); apply the same to paper order responses and TUI payloads.
- **Subprocess-as-API** (`tui/cli_bridge.run_cli`) — couples TUI to CLI argv + stdout format. Treat as a deliberate boundary, not a contract substitute; every `run_cli` call should be paired with a TypedDict / dataclass.

## 3. Code examples for refactor agents

```python
# Protocol for signer / adapter — siglab/live/sodex_signing.py
class SoDEXSigner(Protocol):
    def sign_typed_payload(self, *, domain: str, account_id: int, payload_hash: str, nonce: int) -> str: ...
```

```python
# TypedDict pipeline contract — siglab/orchestration/contracts.py
class PlannerOutput(TypedDict, total=False):
    decision: str; target_family: str; evidence_paths: list[str]
```

```python
# Frozen dataclass DTO — siglab/tui/data_views.py
@dataclass(frozen=True, slots=True)
class TickerView:
    symbol: str; last_price: float; price_change_pct: float; volume: float
```

```python
# import-linter boundary — .importlinter
[importlinter:contract:domain-cannot-import-adapters]
type = forbidden
source_modules = siglab.orchestration
forbidden_modules = siglab.live.sodex_signing, siglab.cli
```

## 4. SigLab-specific findings

- **Clean boundaries**: `siglab.live` (single-responsibility modules; explicit `__init__` re-exports), `siglab.tui` (frozen `data_views` + `BaseScreen` + `TuiApiClient` is textbook Protocol-by-Protocol), `siglab.risk` (pure functions, no I/O leak).
- **TypedDict contracts exist**: `orchestration/contracts.py` (5 TypedDicts), `search/lineage_types.py` (~12 row TypedDicts) — right pattern, replicate.
- **Protocol in use**: `SoDEXSigner` (sodex_signing.py:50), `WebSocketConnection` (sodex_ws.py:32).
- **Needs work**: `evaluation/compile.py` (58 KB), `search/mutate.py` (95 KB), `research/hypothesis.py` (80 KB), `evaluation/runner.py` (152 KB) — god modules, no Protocol seams. `siglab.live.__init__` re-exports 60+ symbols including private `perps_*_body` / `canonical_json`. No `abc.ABC` use anywhere; no `import-linter` config; `mypy.ini` (601 B) is global-only.

## 5. Recommendations with impact estimates

1. **Promote TypedDict contracts to all pipeline boundaries** — new `siglab/contracts/` with `PaperOrderResponse`, `RiskScoreSnapshot`, `EvidenceGraphResponse`, etc. **Impact**: ~200-400 LoC removed (manual `dict[str, Any]` annotations + `cast(...)`), mypy catches drift. Effort: 2-3 days.
2. **Add `.importlinter` with 4 boundary rules** — `orchestration→live`, `cli→tui`, `live→tui`, `risk→live` all forbidden. **Impact**: <50 LoC config; expect 5-10 latent cross-layer imports caught in first run. Effort: 0.5 day.
3. **Split `siglab.live.__init__` into public + `_internals`** — keep only `SoDEXPaperPerpsClient`, `DirectionalPerpsSigLabStrategy`, `LiveDeploymentManager`, `SoDEXSigner` Protocol, error types public. **Impact**: clarifies 10-rule security surface in `module-live-boundary.md`; eliminates accidental dependency on signing internals. Effort: 1 day.
4. **Add `Protocol` seams to the 4 god modules** — `FeatureExtractor`, `Mutator`, `HypothesisGenerator` Protocols; current classes become canonical impls. **Impact**: enables parallel impls (mock `Mutator` for tests); ~150-300 LoC removable from fixtures. Effort: 3-5 days.
5. **Add `ABC` for plugin registration** in `data.MarketDataProvider` and `live.SoDEXSigner` — pure ABCs with one abstract `name()` method; `__init_subclass__` registration. **Impact**: catches missing impls at import time; unblocks future market-data backends. Effort: 1 day.
