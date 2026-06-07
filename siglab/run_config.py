from __future__ import annotations

from pathlib import Path
from typing import Any

from siglab.data import MarketDataProvider
from siglab.io_utils import read_json_if_exists
from siglab.schemas import SignalSpec
from siglab.search.mutate import SpecMutator
from siglab.evaluation.strategy_semantics import PAIR_TRADE_FAMILIES
from siglab.track_registry import canonical_track_name, resolve_track


def resolve_memory_scope(*, explicit: str | None, default: str | None) -> str:
    scope = str(explicit or default or "session_local").strip().lower()
    if scope not in {"session_local", "track_shared"}:
        return "session_local"
    return scope


def ancestry_scope_kwargs(*, memory_scope: str, run_session_id: str) -> dict[str, str]:
    if memory_scope == "track_shared":
        return {}
    return {"run_session_id": run_session_id}


def next_iteration_from_workspace(workspace_root: Path) -> int:
    state = read_json_if_exists(workspace_root / "current" / "SESSION_STATE.json")
    try:
        current_iteration = int(state.get("iteration_number") or 0)
    except (TypeError, ValueError):
        current_iteration = 0
    if current_iteration > 0:
        return current_iteration + 1
    max_iteration = 0
    iterations_dir = workspace_root / "iterations"
    if iterations_dir.exists():
        for child in iterations_dir.iterdir():
            if not child.is_dir():
                continue
            prefix = child.name.split("_", 1)[0]
            try:
                max_iteration = max(max_iteration, int(prefix))
            except ValueError:
                continue
    return max_iteration + 1 if max_iteration > 0 else 1


def resolve_resume_run(
    *,
    settings: Any,
    run_session_id: str,
) -> dict[str, Any]:
    matches = [
        path
        for path in settings.artifact_dir.glob(f"*/workspaces/{run_session_id}")
        if path.is_dir()
    ]
    if not matches:
        raise SystemExit(f"Run session `{run_session_id}` was not found under `{settings.artifact_dir}`")
    if len(matches) > 1:
        raise SystemExit(
            f"Run session `{run_session_id}` matched multiple workspaces; resume requires a unique run_session_id"
        )
    workspace_root = matches[0]
    track = resolve_track(workspace_root.parents[1].name)
    meta = read_json_if_exists(workspace_root / "meta" / "session.json")
    state = read_json_if_exists(workspace_root / "current" / "SESSION_STATE.json")
    families = [
        str(family)
        for family in list(meta.get("families") or [])
        if str(family).strip()
    ]
    memory_scope = resolve_memory_scope(
        explicit=str(state.get("memory_scope") or meta.get("memory_scope") or "session_local"),
        default="session_local",
    )
    custom_symbols = [
        str(symbol).strip().upper()
        for symbol in list(state.get("custom_symbols") or meta.get("custom_symbols") or [])
        if str(symbol).strip()
    ]
    use_historical_seeds = bool(
        state.get("use_historical_seeds")
        if "use_historical_seeds" in state
        else meta.get("use_historical_seeds")
    )
    return {
        "workspace_root": workspace_root,
        "track": track,
        "families": families,
        "memory_scope": memory_scope,
        "custom_symbols": custom_symbols,
        "use_historical_seeds": use_historical_seeds,
        "next_iteration": next_iteration_from_workspace(workspace_root),
    }


def parse_symbol_override(symbols: str | None) -> list[str] | None:
    if symbols is None:
        return None
    parsed: list[str] = []
    seen: set[str] = set()
    for raw in str(symbols).split(","):
        symbol = raw.strip().upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        parsed.append(symbol)
    if not parsed:
        raise SystemExit("--symbols must contain at least one symbol")
    if len(parsed) < 2:
        raise SystemExit("--symbols must contain at least two symbols")
    return parsed


def override_seed_spec_symbols(
    spec: SignalSpec,
    custom_symbols: list[str] | None,
) -> SignalSpec:
    if not custom_symbols:
        return spec
    payload = spec.canonical_dict()
    family = str(payload.get("family") or "")
    universe = dict(payload.get("universe") or {})
    if family in PAIR_TRADE_FAMILIES:
        universe["basis_groups"] = list(custom_symbols[:2])
        universe["max_symbols"] = 2
    else:
        universe["basis_groups"] = list(custom_symbols)
        universe["max_symbols"] = len(custom_symbols)
    payload["universe"] = universe
    return SignalSpec.from_dict(payload)


def load_seed_specs_for_run(
    *,
    mutator: SpecMutator,
    track: str,
    family_scope: str | list[str] | None,
    custom_symbols: list[str] | None,
    use_historical_seeds: bool,
) -> list[SignalSpec]:
    return [
        override_seed_spec_symbols(spec, custom_symbols)
        for spec in mutator.load_seed_specs(
            track,
            family=family_scope,
            include_historical=use_historical_seeds,
        )
    ]


async def validate_symbol_override(
    *,
    provider: MarketDataProvider,
    custom_symbols: list[str] | None,
) -> list[str] | None:
    if not custom_symbols:
        return None
    discovered = await provider.discover_perp_symbols(custom_symbols, limit=len(custom_symbols))
    discovered_set = set(discovered)
    resolved = [symbol for symbol in custom_symbols if symbol in discovered_set]
    missing = [symbol for symbol in custom_symbols if symbol not in discovered_set]
    if missing:
        raise SystemExit(
            "Unsupported --symbols for trend_signals: " + ", ".join(missing)
        )
    return resolved


