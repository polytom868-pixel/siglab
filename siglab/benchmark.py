from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from siglab.schemas import SignalSpec
from siglab.path_utils import display_path
from siglab.track_registry import canonical_track_name, storage_track_name


DEFAULT_BENCHMARK_DECK = "trend_signals_external"
DEFAULT_BENCHMARK_TRACK = "trend_signals"
DEFAULT_BENCHMARK_SEED_FAMILY = "perp_multi_asset_carry"
DEFAULT_BENCHMARK_AGENT_LABEL = "external_agent"


@dataclass
class BenchmarkDeckPaths:
    root: Path
    program_path: Path
    spec_path: Path
    best_spec_path: Path
    results_path: Path
    observation_path: Path
    state_path: Path


def benchmark_paths(*, settings: Any, deck_name: str) -> BenchmarkDeckPaths:
    root = settings.root_dir / "benchmarks" / deck_name
    return BenchmarkDeckPaths(
        root=root,
        program_path=root / "program.md",
        spec_path=root / "spec.yaml",
        best_spec_path=root / "best_spec.yaml",
        results_path=root / "results.tsv",
        observation_path=root / "observation.md",
        state_path=root / "state.json",
    )


def supported_deck_names() -> list[str]:
    return [DEFAULT_BENCHMARK_DECK]


def validate_deck_name(deck_name: str) -> str:
    deck = str(deck_name or "").strip()
    if deck not in supported_deck_names():
        allowed = ", ".join(supported_deck_names())
        raise SystemExit(f"Unsupported benchmark deck `{deck}`. Supported: {allowed}")
    return deck


def init_benchmark_deck(
    *,
    settings: Any,
    ancestry: Any,
    mutator: Any,
    deck_name: str,
    runner_label: str = DEFAULT_BENCHMARK_AGENT_LABEL,
    run_label: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    deck = validate_deck_name(deck_name)
    paths = benchmark_paths(settings=settings, deck_name=deck)
    if paths.root.exists() and not force:
        required = [
            paths.program_path,
            paths.spec_path,
            paths.best_spec_path,
            paths.results_path,
            paths.observation_path,
            paths.state_path,
        ]
        if all(path.exists() for path in required):
            raise SystemExit(
                f"Benchmark deck `{deck}` already exists. Use --force to reinitialize it."
            )
    paths.root.mkdir(parents=True, exist_ok=True)

    seed = _select_benchmark_seed(
        settings=settings,
        ancestry=ancestry,
        mutator=mutator,
        track=DEFAULT_BENCHMARK_TRACK,
        family=DEFAULT_BENCHMARK_SEED_FAMILY,
    )
    spec_payload = dict(seed["spec"])
    summary = dict(seed.get("summary") or {})
    normalized_runner_label = _normalize_runner_label(runner_label)
    benchmark_run_id = _benchmark_run_id(deck=deck, runner_label=normalized_runner_label)
    benchmark_run_label = str(run_label or benchmark_run_id)

    state = {
        "deck_name": deck,
        "track": DEFAULT_BENCHMARK_TRACK,
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
        "runner_label": normalized_runner_label,
        "benchmark_run_id": benchmark_run_id,
        "run_label": benchmark_run_label,
        "benchmark_iteration": 0,
        "incumbent_spec_hash": SignalSpec.from_dict(spec_payload).strategy_hash(),
        "incumbent_source": seed["source"],
        "incumbent_artifact_path": display_path(
            seed.get("artifact_path"),
            root_dir=settings.root_dir,
        ),
        "best_aggregate_score": summary.get("aggregate_score"),
        "best_validation_total_return": summary.get("validation_total_return"),
        "best_pre_audit_canonical_total_return": summary.get("pre_audit_canonical_total_return"),
        "last_result_status": None,
    }

    _write_yaml(paths.spec_path, spec_payload)
    _write_yaml(paths.best_spec_path, spec_payload)
    paths.program_path.write_text(_program_template(deck_name=deck))
    paths.results_path.write_text(
        "timestamp\tspec_hash\tfamily\taggregate_score\tvalidation_return\tpre_audit_return\tstatus\tdescription\n"
    )
    paths.state_path.write_text(json.dumps(state, indent=2, ensure_ascii=True))
    paths.observation_path.write_text(
        render_observation(
            ancestry=ancestry,
            mutator=mutator,
            deck_name=deck,
            state=state,
            incumbent_payload=spec_payload,
            incumbent_summary=summary,
        )
    )
    return {
        "deck_name": deck,
        "paths": _paths_payload(paths),
        "state": state,
        "seed_source": seed["source"],
        "seed_spec_hash": state["incumbent_spec_hash"],
    }


async def evaluate_benchmark_deck(
    *,
    settings: Any,
    ancestry: Any,
    mutator: Any,
    evaluator: Any,
    provider: Any,
    deck_name: str,
) -> dict[str, Any]:
    deck = validate_deck_name(deck_name)
    paths = benchmark_paths(settings=settings, deck_name=deck)
    if not paths.state_path.exists():
        raise SystemExit(f"Benchmark deck `{deck}` is not initialized. Run benchmark-init first.")

    state = json.loads(paths.state_path.read_text())
    payload = _read_yaml(paths.spec_path)
    spec = SignalSpec.from_dict(payload)
    if canonical_track_name(spec.track) != DEFAULT_BENCHMARK_TRACK:
        raise SystemExit(
            f"Benchmark deck `{deck}` only supports track `{DEFAULT_BENCHMARK_TRACK}`."
        )

    allowed_families = mutator._allowed_families(spec.track, family=None)
    allowed_features_by_family = mutator._allowed_features_by_family(spec.track, family=None)
    family_defaults = mutator._family_defaults(spec.track, family=None)

    incumbent_payload = _read_yaml(paths.best_spec_path)
    incumbent_spec = SignalSpec.from_dict(incumbent_payload)
    incumbent_hash = str(state.get("incumbent_spec_hash") or incumbent_spec.strategy_hash())
    incumbent_detail = ancestry.experiment_detail(incumbent_hash)
    incumbent_summary = dict(incumbent_detail.get("summary") or {}) if incumbent_detail else {
        "aggregate_score": state.get("best_aggregate_score"),
        "validation_total_return": state.get("best_validation_total_return"),
        "pre_audit_canonical_total_return": state.get("best_pre_audit_canonical_total_return"),
    }

    try:
        validated = mutator._validate_spec(
            spec=spec,
            track=spec.track,
            allowed_families=allowed_families,
            allowed_features_by_family=allowed_features_by_family,
            family_defaults=family_defaults,
        )
    except Exception as exc:  # noqa: BLE001
        result = _benchmark_failure_result(
            paths=paths,
            state=state,
            status="invalid",
            description=f"{type(exc).__name__}: {exc}",
            spec_hash=spec.strategy_hash(),
            family=spec.family,
            incumbent_payload=incumbent_payload,
            ancestry=ancestry,
            mutator=mutator,
            incumbent_summary=incumbent_summary,
            deck_name=deck,
        )
        return result

    provider.begin_iteration_bundle(track=validated.track, parent=validated)
    try:
        evaluation = await evaluator.evaluate(validated, fast_mode=False)
    except Exception as exc:  # noqa: BLE001
        provider.clear_iteration_bundle()
        result = _benchmark_failure_result(
            paths=paths,
            state=state,
            status="crash",
            description=f"{type(exc).__name__}: {exc}",
            spec_hash=validated.strategy_hash(),
            family=validated.family,
            incumbent_payload=incumbent_payload,
            ancestry=ancestry,
            mutator=mutator,
            incumbent_summary=incumbent_summary,
            deck_name=deck,
        )
        return result

    market_bundle = dict(provider.current_bundle_context() or {})
    research_summary = {
        "track": validated.track,
        "parent_family": incumbent_spec.family,
        "parent_hash": incumbent_hash if ancestry.has_spec(incumbent_hash) else None,
        "market_bundle": market_bundle,
        "external_research": {
            "enabled": False,
            "provider": "benchmark_deck",
            "queries": [],
            "reports": [],
        },
        "run_context": {
            "run_session_id": str(state.get("benchmark_run_id") or _benchmark_run_id(deck=deck, runner_label=str(state.get("runner_label") or DEFAULT_BENCHMARK_AGENT_LABEL))),
            "phase_label": "benchmark",
            "iteration_number": int(state.get("benchmark_iteration") or 0) + 1,
            "deterministic": False,
            "llm_phase": False,
            "runner_label": str(state.get("runner_label") or DEFAULT_BENCHMARK_AGENT_LABEL),
            "run_label": str(state.get("run_label") or state.get("benchmark_run_id") or ""),
            "benchmark_mode": True,
            "benchmark_deck": deck,
            "benchmark_source": "external_agent_deck",
        },
    }
    artifact_path = _write_benchmark_artifact(settings=settings, track=validated.track, evaluation=evaluation)
    ancestry.record(
        evaluation=evaluation,
        parent_hash=research_summary["parent_hash"],
        research_summary=research_summary,
        artifact_path=str(artifact_path),
    )
    provider.clear_iteration_bundle()

    summary = dict(evaluation.get("summary") or {})
    status = _benchmark_status(summary=summary, incumbent_summary=incumbent_summary)
    description = str((evaluation.get("spec") or {}).get("hypothesis") or "").strip() or validated.hypothesis
    _append_result_row(
        paths=paths,
        timestamp=datetime.now(UTC).isoformat(),
        spec_hash=str(evaluation.get("spec_hash") or validated.strategy_hash()),
        family=validated.family,
        aggregate_score=summary.get("aggregate_score"),
        validation_return=summary.get("validation_total_return"),
        pre_audit_return=summary.get("pre_audit_canonical_total_return"),
        status=status,
        description=description,
    )

    state["benchmark_iteration"] = int(state.get("benchmark_iteration") or 0) + 1
    state["updated_at"] = datetime.now(UTC).isoformat()
    state["last_result_status"] = status

    if status == "keep":
        incumbent_payload = dict(evaluation.get("spec") or validated.canonical_dict())
        incumbent_summary = summary
        incumbent_hash = str(evaluation.get("spec_hash") or validated.strategy_hash())
        _write_yaml(paths.best_spec_path, incumbent_payload)
        _write_yaml(paths.spec_path, incumbent_payload)
        state["incumbent_spec_hash"] = incumbent_hash
        state["incumbent_source"] = "benchmark_keep"
        state["incumbent_artifact_path"] = display_path(artifact_path, root_dir=settings.root_dir)
        state["best_aggregate_score"] = summary.get("aggregate_score")
        state["best_validation_total_return"] = summary.get("validation_total_return")
        state["best_pre_audit_canonical_total_return"] = summary.get("pre_audit_canonical_total_return")
    else:
        _write_yaml(paths.spec_path, incumbent_payload)

    paths.state_path.write_text(json.dumps(state, indent=2, ensure_ascii=True))
    paths.observation_path.write_text(
        render_observation(
            ancestry=ancestry,
            mutator=mutator,
            deck_name=deck,
            state=state,
            incumbent_payload=incumbent_payload,
            incumbent_summary=incumbent_summary,
        )
    )
    return {
        "deck_name": deck,
        "status": status,
        "spec_hash": str(evaluation.get("spec_hash") or validated.strategy_hash()),
        "artifact_path": display_path(artifact_path, root_dir=settings.root_dir),
        "summary": summary,
        "state": state,
    }


def benchmark_status(
    *,
    settings: Any,
    deck_name: str,
) -> dict[str, Any]:
    deck = validate_deck_name(deck_name)
    paths = benchmark_paths(settings=settings, deck_name=deck)
    if not paths.state_path.exists():
        raise SystemExit(f"Benchmark deck `{deck}` is not initialized. Run benchmark-init first.")
    return {
        "deck_name": deck,
        "paths": _paths_payload(paths),
        "state": json.loads(paths.state_path.read_text()),
        "recent_results": _read_results_rows(paths.results_path, limit=10),
    }


def render_observation(
    *,
    ancestry: Any,
    mutator: Any,
    deck_name: str,
    state: dict[str, Any],
    incumbent_payload: dict[str, Any],
    incumbent_summary: dict[str, Any],
) -> str:
    track = DEFAULT_BENCHMARK_TRACK
    allowed_families = mutator._allowed_families(track, family=None)
    current_best = ancestry.best(track)
    rows = ancestry.dashboard_rows(track=track)
    recent_failures = [
        row for row in rows if not bool(dict(row.get("summary") or {}).get("passed"))
    ][-5:]
    failure_lines = []
    for row in recent_failures[-3:]:
        summary = dict(row.get("summary") or {})
        gate_reasons = ", ".join(list(summary.get("gate_reasons") or [])[:3]) or "n/a"
        failure_lines.append(
            f"- {row['spec_hash']} {row['family']}: "
            f"score={_fmt_num(summary.get('aggregate_score'))}, "
            f"validation={_fmt_pct(summary.get('validation_total_return'))}, "
            f"pre_audit={_fmt_pct(summary.get('pre_audit_canonical_total_return'))}, "
            f"gate_reasons={gate_reasons}"
        )
    if not failure_lines:
        failure_lines.append("- no recent failures recorded yet")

    lines = [
        f"# Benchmark Observation: {deck_name}",
        "",
        "This is an external-agent benchmark deck modeled after `autoresearch`.",
        "Edit only `spec.yaml`. The evaluator and validator are fixed.",
        "",
        "## Objective",
        "- Beat the current incumbent on `aggregate_score`.",
        "- A spec is `keep` only if it passes normal gating and improves the incumbent.",
        "- Tie-break with `validation_total_return`, then `pre_audit_canonical_total_return`.",
        "",
        "## Session",
        f"- runner_label: `{state.get('runner_label')}`",
        f"- benchmark_run_id: `{state.get('benchmark_run_id')}`",
        f"- run_label: `{state.get('run_label')}`",
        "",
        "## Current Incumbent",
        f"- hash: `{state.get('incumbent_spec_hash')}`",
        f"- source: `{state.get('incumbent_source')}`",
        f"- family: `{incumbent_payload.get('family')}`",
        f"- aggregate_score: `{_fmt_num(incumbent_summary.get('aggregate_score'))}`",
        f"- validation_total_return: `{_fmt_pct(incumbent_summary.get('validation_total_return'))}`",
        f"- pre_audit_canonical_total_return: `{_fmt_pct(incumbent_summary.get('pre_audit_canonical_total_return'))}`",
        "",
        "## Allowed Families",
        "- " + ", ".join(allowed_families),
        "",
        "## Current Strongest Anchor",
        f"- default seed family: `{DEFAULT_BENCHMARK_SEED_FAMILY}`",
        f"- incumbent hypothesis: {str(incumbent_payload.get('hypothesis') or '').strip()}",
        "",
        "## Best Existing Passed Spec In DB",
        (
            f"- `{current_best['spec_hash']}` {current_best['spec']['family']} "
            f"aggregate_score={_fmt_num(current_best.get('aggregate_score'))}"
            if current_best is not None
            else "- none in current DB"
        ),
        "",
        "## Recent Failure Motifs",
        *failure_lines,
    ]
    return "\n".join(lines).strip() + "\n"


def _select_benchmark_seed(
    *,
    settings: Any,
    ancestry: Any,
    mutator: Any,
    track: str,
    family: str,
) -> dict[str, Any]:
    carry_rows = [
        row
        for row in ancestry.dashboard_rows(track=track, family=family)
        if bool(row.get("deployd"))
    ]
    if carry_rows:
        carry_rows.sort(
            key=lambda row: (
                float(dict(row.get("summary") or {}).get("aggregate_score") or 0.0),
                str(row.get("created_at") or ""),
            ),
            reverse=True,
        )
        chosen = carry_rows[0]
        return {
            "spec": dict(chosen.get("spec") or {}),
            "summary": dict(chosen.get("summary") or {}),
            "source": "deployd_db",
            "artifact_path": chosen.get("artifact_path"),
        }

    historical = _best_historical_seed_artifact(
        settings=settings,
        mutator=mutator,
        track=track,
        family=family,
    )
    if historical is not None:
        return historical

    static_seed = mutator.load_seed_specs(track, family=family)[0]
    return {
        "spec": static_seed.canonical_dict(),
        "summary": {},
        "source": "static_seed",
        "artifact_path": None,
    }


def _best_historical_seed_artifact(
    *,
    settings: Any,
    mutator: Any,
    track: str,
    family: str,
) -> dict[str, Any] | None:
    storage_track = storage_track_name(track)
    search_dirs = [settings.artifact_dir / storage_track]
    search_dirs.extend(
        sorted(
            (
                backup_dir / "runs" / storage_track
                for backup_dir in (settings.root_dir / "backups").glob("relaunch_*")
            ),
            reverse=True,
        )
    )
    best_payload: dict[str, Any] | None = None
    best_key: tuple[float, float] | None = None
    for directory in search_dirs:
        if not directory.exists():
            continue
        for path in directory.glob("*.json"):
            try:
                payload = json.loads(path.read_text())
            except Exception:  # noqa: BLE001
                continue
            spec_payload = dict(payload.get("spec") or {})
            summary = dict(payload.get("summary") or {})
            if canonical_track_name(spec_payload.get("track")) != track:
                continue
            if str(spec_payload.get("family") or "") != family:
                continue
            if not mutator._historical_seedworthy(summary):
                continue
            quality = mutator._historical_seed_quality(summary)
            freshness = path.stat().st_mtime
            key = (quality, freshness)
            if best_key is None or key > best_key:
                best_key = key
                best_payload = {
                    "spec": spec_payload,
                    "summary": summary,
                    "source": "historical_artifact",
                    "artifact_path": str(path),
                }
    return best_payload


def _benchmark_status(*, summary: dict[str, Any], incumbent_summary: dict[str, Any]) -> str:
    if not bool(summary.get("passed")):
        return "discard"
    spec_vector = (
        _safe_float(summary.get("aggregate_score")),
        _safe_float(summary.get("validation_total_return")),
        _safe_float(summary.get("pre_audit_canonical_total_return")),
    )
    incumbent_vector = (
        _safe_float(incumbent_summary.get("aggregate_score")),
        _safe_float(incumbent_summary.get("validation_total_return")),
        _safe_float(incumbent_summary.get("pre_audit_canonical_total_return")),
    )
    if _compare_vectors(spec_vector, incumbent_vector) > 0:
        return "keep"
    return "discard"


def _compare_vectors(left: tuple[float | None, ...], right: tuple[float | None, ...]) -> int:
    norm_left = tuple(-1e18 if value is None else float(value) for value in left)
    norm_right = tuple(-1e18 if value is None else float(value) for value in right)
    if norm_left > norm_right:
        return 1
    if norm_left < norm_right:
        return -1
    return 0


def _benchmark_failure_result(
    *,
    paths: BenchmarkDeckPaths,
    state: dict[str, Any],
    status: str,
    description: str,
    spec_hash: str,
    family: str,
    incumbent_payload: dict[str, Any],
    ancestry: Any,
    mutator: Any,
    incumbent_summary: dict[str, Any],
    deck_name: str,
) -> dict[str, Any]:
    _append_result_row(
        paths=paths,
        timestamp=datetime.now(UTC).isoformat(),
        spec_hash=spec_hash,
        family=family,
        aggregate_score=None,
        validation_return=None,
        pre_audit_return=None,
        status=status,
        description=description,
    )
    state["benchmark_iteration"] = int(state.get("benchmark_iteration") or 0) + 1
    state["updated_at"] = datetime.now(UTC).isoformat()
    state["last_result_status"] = status
    _write_yaml(paths.spec_path, incumbent_payload)
    paths.state_path.write_text(json.dumps(state, indent=2, ensure_ascii=True))
    paths.observation_path.write_text(
        render_observation(
            ancestry=ancestry,
            mutator=mutator,
            deck_name=deck_name,
            state=state,
            incumbent_payload=incumbent_payload,
            incumbent_summary=incumbent_summary,
        )
    )
    return {
        "deck_name": deck_name,
        "status": status,
        "spec_hash": spec_hash,
        "error": description,
        "state": state,
    }


def _write_benchmark_artifact(*, settings: Any, track: str, evaluation: dict[str, Any]) -> Path:
    target_dir = settings.artifact_dir / track
    target_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = target_dir / f"{timestamp}_{evaluation['spec_hash']}.json"
    target.write_text(json.dumps(evaluation, indent=2, ensure_ascii=True))
    return target


def _append_result_row(
    *,
    paths: BenchmarkDeckPaths,
    timestamp: str,
    spec_hash: str,
    family: str,
    aggregate_score: Any,
    validation_return: Any,
    pre_audit_return: Any,
    status: str,
    description: str,
) -> None:
    line = "\t".join(
        [
            timestamp,
            spec_hash,
            family,
            _fmt_num(aggregate_score),
            _fmt_pct(validation_return),
            _fmt_pct(pre_audit_return),
            status,
            description.replace("\t", " ").strip(),
        ]
    )
    with paths.results_path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _read_results_rows(path: Path, *, limit: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    lines = path.read_text().splitlines()
    if not lines:
        return rows
    header = lines[0].split("\t")
    for line in lines[1:]:
        if not line.strip():
            continue
        values = line.split("\t")
        rows.append(dict(zip(header, values, strict=False)))
    return rows[-limit:]


def _paths_payload(paths: BenchmarkDeckPaths) -> dict[str, str]:
    return {
        "root": display_path(paths.root, root_dir=paths.root.parents[1]) or str(paths.root),
        "program_path": display_path(paths.program_path, root_dir=paths.root.parents[1]) or str(paths.program_path),
        "spec_path": display_path(paths.spec_path, root_dir=paths.root.parents[1]) or str(paths.spec_path),
        "best_spec_path": display_path(paths.best_spec_path, root_dir=paths.root.parents[1]) or str(paths.best_spec_path),
        "results_path": display_path(paths.results_path, root_dir=paths.root.parents[1]) or str(paths.results_path),
        "observation_path": display_path(paths.observation_path, root_dir=paths.root.parents[1]) or str(paths.observation_path),
        "state_path": display_path(paths.state_path, root_dir=paths.root.parents[1]) or str(paths.state_path),
    }


def _read_yaml(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text()) or {}
    if not isinstance(payload, dict):
        raise SystemExit(f"Expected mapping in {path}")
    return dict(payload)


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(yaml.safe_dump(payload, sort_keys=False))


def _fmt_num(value: Any) -> str:
    numeric = _safe_float(value)
    if numeric is None:
        return ""
    return f"{numeric:.6f}"


def _fmt_pct(value: Any) -> str:
    numeric = _safe_float(value)
    if numeric is None:
        return ""
    return f"{numeric:.4%}"


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_runner_label(runner_label: str | None) -> str:
    normalized = str(runner_label or "").strip().lower().replace(" ", "_")
    return normalized or DEFAULT_BENCHMARK_AGENT_LABEL


def _benchmark_run_id(*, deck: str, runner_label: str) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"benchmark::{deck}::{runner_label}::{timestamp}"


def _program_template(*, deck_name: str) -> str:
    return f"""# {deck_name}

This benchmark deck is modeled after `autoresearch`.

## Setup

Read these files first:
- `README.md`
- `benchmarks/{deck_name}/observation.md`
- `benchmarks/{deck_name}/spec.yaml`
- `benchmarks/{deck_name}/best_spec.yaml`

You are benchmarking an external-agent loop against the fixed `siglab` evaluator.

## What you can edit

- Edit only `benchmarks/{deck_name}/spec.yaml`

## What you cannot edit

- Do not edit runtime code, evaluator code, mutator code, or the benchmark keep/discard logic.
- Do not change the evaluation harness.

## Benchmark loop

1. Read `observation.md`
2. Edit `spec.yaml`
3. Run:

```bash
poetry run siglab benchmark-eval --deck {deck_name}
```

4. Check the returned status and `results.tsv`
5. If the result is `keep`, the benchmark command has advanced the incumbent.
6. If the result is `discard`, `invalid`, or `crash`, the benchmark command has restored `spec.yaml` back to the incumbent.
7. Repeat

## Goal

Beat the incumbent on `aggregate_score` while still passing normal gating.

Tie-breaks:
1. `validation_total_return`
2. `pre_audit_canonical_total_return`
"""



