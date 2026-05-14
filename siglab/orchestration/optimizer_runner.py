from __future__ import annotations

import json
import re
import statistics
import warnings
from dataclasses import dataclass
from typing import Any

try:
    import optuna
except ImportError:  # pragma: no cover - exercised in runtime environments without optuna installed
    optuna = None

from siglab.schemas import SignalSpec
from siglab.orchestration.trials import (
    apply_path_value,
    clone_payload,
    get_path_value,
    score_diagnosis,
    summarize_generalization,
    summarize_patch,
)


@dataclass
class OptimizationResult:
    spec_payload: dict[str, Any]
    best_summary: dict[str, Any]
    best_params: dict[str, Any]
    optuna_space: dict[str, Any]
    score_diagnosis: dict[str, Any]
    trial_count: int
    objective_value: float
    fragility_penalty: float
    deployment_score: float | None
    fragility_pack: dict[str, Any]
    stability_pack: dict[str, Any]


class OptunaOptimizerRunner:
    def __init__(
        self,
        *,
        settings: Any,
        evaluator: Any,
        mutator: Any,
        ancestry: Any,
    ) -> None:
        self.settings = settings
        self.evaluator = evaluator
        self.mutator = mutator
        self.ancestry = ancestry

    async def run(
        self,
        *,
        session: Any,
        base_payload: dict[str, Any],
        spec_payload: dict[str, Any],
        iteration_paths: dict[str, Any],
        incumbent_summary: dict[str, Any] | None,
    ) -> OptimizationResult:
        optuna_space = infer_optuna_space(spec_payload)
        iteration_paths["optuna_space_path"].write_text(
            json.dumps(optuna_space, indent=2, ensure_ascii=True, default=str)
        )
        if optuna is None:
            raise RuntimeError("Optuna is not installed. Add the dependency before running the optimizer stage.")
        if not list(optuna_space.get("parameters") or []):
            summary = {"aggregate_score": None}
            score = score_diagnosis(summary, incumbent_summary or {})
            generalization = summarize_generalization(
                summary,
                optuna_space=optuna_space,
                tuned_params={},
                stability_pack={"status": "skipped_no_params"},
            )
            iteration_paths["optuna_best_path"].write_text(
                json.dumps(
                    {
                        "spec": spec_payload,
                        "best_params": {},
                        "summary": summary,
                        "score_diagnosis": score,
                        "fragility_penalty": generalization.get("fragility_penalty"),
                        "deployment_score": generalization.get("deployment_score"),
                        "fragility_pack": generalization.get("fragility_pack"),
                        "stability_pack": generalization.get("stability_pack"),
                        "objective_value": self._objective(
                            summary,
                            optuna_space=optuna_space,
                            tuned_params={},
                            stability_pack={"status": "skipped_no_params"},
                        ),
                        "search_skipped": True,
                    },
                    indent=2,
                    ensure_ascii=True,
                    default=str,
                )
            )
            iteration_paths["optuna_trials_path"].write_text("")
            return OptimizationResult(
                spec_payload=spec_payload,
                best_summary=summary,
                best_params={},
                optuna_space=optuna_space,
                score_diagnosis=score,
                trial_count=0,
                objective_value=self._objective(
                    summary,
                    optuna_space=optuna_space,
                    tuned_params={},
                    stability_pack={"status": "skipped_no_params"},
                ),
                fragility_penalty=float(generalization.get("fragility_penalty") or 0.0),
                deployment_score=generalization.get("deployment_score"),
                fragility_pack=dict(generalization.get("fragility_pack") or {}),
                stability_pack=dict(generalization.get("stability_pack") or {}),
            )

        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message=r"Argument ``multivariate`` is an experimental feature\.",
                category=getattr(optuna.exceptions, "ExperimentalWarning", Warning),
            )
            sampler = optuna.samplers.TPESampler(seed=7, multivariate=True)
        study = optuna.create_study(direction="maximize", sampler=sampler)
        for seed_params in self._warm_start_params(
            session=session,
            family=str(spec_payload.get("family") or ""),
            spec_payload=spec_payload,
            optuna_space=optuna_space,
        ):
            try:
                study.enqueue_trial(seed_params)
            except Exception:
                continue

        best_summary: dict[str, Any] | None = None
        best_payload = dict(spec_payload)
        best_params: dict[str, Any] = {}
        best_objective = -1e18
        trial_rows: list[dict[str, Any]] = []
        for _ in range(int(getattr(self.settings, "optuna_trials", 20) or 20)):
            trial = study.ask()
            params = _suggest_params(trial=trial, optuna_space=optuna_space)
            payload = clone_payload(spec_payload)
            for path, value in params.items():
                apply_path_value(payload, path, value)
            validated_payload = self._validated_payload(
                session=session,
                payload=payload,
            )
            spec = SignalSpec.from_dict(validated_payload)
            try:
                evaluation = await self.evaluator.evaluate(spec, fast_mode=True)
                summary = dict(evaluation.get("summary") or {})
                objective_details = self._objective_details(
                    summary,
                    evaluation=evaluation,
                    optuna_space=optuna_space,
                    tuned_params=params,
                )
                objective_value = float(objective_details.get("objective") or -1e18)
                trial_status = "ok"
            except Exception as exc:  # noqa: BLE001
                summary = {}
                objective_details = self._objective_details(
                    summary,
                    optuna_space=optuna_space,
                    tuned_params=params,
                )
                objective_value = -1e9
                trial_status = f"error:{type(exc).__name__}"
            study.tell(trial, objective_value)
            row = {
                "trial_number": int(trial.number),
                "params": params,
                "objective": objective_value,
                "status": trial_status,
                "summary": {
                    "aggregate_score": summary.get("aggregate_score"),
                    "validation_total_return": summary.get("validation_total_return"),
                    "pre_audit_canonical_total_return": summary.get("pre_audit_canonical_total_return"),
                    "passed": summary.get("passed"),
                    "gate_reasons": list(summary.get("gate_reasons") or []),
                },
                "fragility_penalty": objective_details.get("fragility_penalty"),
                "deployment_score": objective_details.get("deployment_score"),
                "audit_alignment": objective_details.get("audit_alignment"),
                "fragility_label": objective_details.get("fragility_label"),
                "param_diff_summary": summarize_patch(
                    _payload_patch_for_paths(
                        base_payload=spec_payload,
                        target_payload=validated_payload,
                        paths=list(params),
                    )
                ),
            }
            trial_rows.append(row)
            if best_summary is None or objective_value > best_objective:
                best_summary = summary
                best_payload = validated_payload
                best_params = params
                best_objective = objective_value

        stability_pack = await self._stability_sweep(
            session=session,
            spec_payload=best_payload,
            optuna_space=optuna_space,
            best_summary=best_summary or {},
        )

        iteration_paths["optuna_trials_path"].write_text(
            "\n".join(json.dumps(row, ensure_ascii=True, default=str) for row in trial_rows) + ("\n" if trial_rows else "")
        )
        objective_details = self._objective_details(
            best_summary or {},
            optuna_space=optuna_space,
            tuned_params=best_params,
            stability_pack=stability_pack,
        )
        diagnosis = score_diagnosis(best_summary or {}, incumbent_summary or {})
        iteration_paths["optuna_best_path"].write_text(
            json.dumps(
                {
                    "spec": best_payload,
                    "best_params": best_params,
                    "summary": best_summary or {},
                    "score_diagnosis": diagnosis,
                    "fragility_penalty": objective_details.get("fragility_penalty"),
                    "deployment_score": objective_details.get("deployment_score"),
                    "audit_alignment": objective_details.get("audit_alignment"),
                    "fragility_label": objective_details.get("fragility_label"),
                    "fragility_pack": objective_details.get("fragility_pack"),
                    "stability_pack": stability_pack,
                    "objective_value": objective_details.get("objective"),
                },
                indent=2,
                ensure_ascii=True,
                default=str,
            )
        )
        return OptimizationResult(
            spec_payload=best_payload,
            best_summary=best_summary or {},
            best_params=best_params,
            optuna_space=optuna_space,
            score_diagnosis=diagnosis,
            trial_count=len(trial_rows),
            objective_value=float(objective_details.get("objective") or -1e18),
            fragility_penalty=float(objective_details.get("fragility_penalty") or 0.0),
            deployment_score=objective_details.get("deployment_score"),
            fragility_pack=dict(objective_details.get("fragility_pack") or {}),
            stability_pack=stability_pack,
        )

    def _objective(
        self,
        summary: dict[str, Any],
        *,
        evaluation: dict[str, Any] | None = None,
        optuna_space: dict[str, Any] | None = None,
        tuned_params: dict[str, Any] | None = None,
        stability_pack: dict[str, Any] | None = None,
    ) -> float:
        details = self._objective_details(
            summary,
            evaluation=evaluation,
            optuna_space=optuna_space,
            tuned_params=tuned_params,
            stability_pack=stability_pack,
        )
        return float(details.get("objective") or -1e18)

    def _objective_details(
        self,
        summary: dict[str, Any],
        *,
        evaluation: dict[str, Any] | None = None,
        optuna_space: dict[str, Any] | None = None,
        tuned_params: dict[str, Any] | None = None,
        stability_pack: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        aggregate = float(summary.get("aggregate_score") or -1e9)
        gate_penalty = 20.0 if not bool(summary.get("passed")) else 0.0
        generalization = summarize_generalization(
            summary,
            evaluation=evaluation,
            optuna_space=optuna_space,
            tuned_params=tuned_params,
            stability_pack=stability_pack,
        )
        objective = aggregate - gate_penalty - float(generalization.get("fragility_penalty") or 0.0)
        return {
            "objective": objective,
            "gate_penalty": gate_penalty,
            **generalization,
        }

    def _validated_payload(
        self,
        *,
        session: Any,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        spec = SignalSpec.from_dict(payload)
        validated = self.mutator._validate_spec(
            spec=spec,
            track=session.track,
            allowed_families=list(session.families),
            allowed_features_by_family=self.mutator._allowed_features_by_family(
                session.track,
                family=list(session.families),
            ),
            family_defaults=self.mutator._family_defaults(
                session.track,
                family=list(session.families),
            ),
        )
        return validated.canonical_dict()

    def _warm_start_params(
        self,
        *,
        session: Any,
        family: str,
        spec_payload: dict[str, Any],
        optuna_space: dict[str, Any],
    ) -> list[dict[str, Any]]:
        seeded: list[dict[str, Any]] = []
        rows = [
            row
            for row in self.ancestry.dashboard_rows(
                track=session.track,
                family=family,
                run_session_id=(
                    session.run_session_id
                    if getattr(session, "memory_scope", "track_shared") != "track_shared"
                    else None
                ),
            )
            if bool(row.get("passed"))
        ]
        rows.sort(
            key=lambda row: (
                self._warm_start_priority(
                    row=row,
                    spec_payload=spec_payload,
                ),
                str(row.get("created_at") or ""),
            ),
            reverse=True,
        )
        seen: set[str] = set()
        for row in rows[:6]:
            payload = dict(row.get("spec") or {})
            params: dict[str, Any] = {}
            for item in list(optuna_space.get("parameters") or []):
                path = str(item.get("path") or "")
                if not self._seed_path_compatible(
                    path=path,
                    source_payload=payload,
                    target_payload=spec_payload,
                ):
                    continue
                value = get_path_value(payload, path)
                if value is None:
                    continue
                if not self._value_in_space(value=value, space_item=item):
                    continue
                params[path] = value
            if params:
                signature = json.dumps(params, sort_keys=True, ensure_ascii=True, default=str)
                if signature in seen:
                    continue
                seen.add(signature)
                seeded.append(params)
        return seeded

    async def _stability_sweep(
        self,
        *,
        session: Any,
        spec_payload: dict[str, Any],
        optuna_space: dict[str, Any],
        best_summary: dict[str, Any],
    ) -> dict[str, Any]:
        if not bool(best_summary.get("passed")):
            return {"status": "skipped_failed_spec", "neighbor_count": 0}
        parameters = list(optuna_space.get("parameters") or [])
        if not parameters:
            return {"status": "skipped_no_params", "neighbor_count": 0}

        low_payload = clone_payload(spec_payload)
        high_payload = clone_payload(spec_payload)
        low_changed = False
        high_changed = False
        for item in parameters:
            path = str(item.get("path") or "")
            current = get_path_value(spec_payload, path)
            if current is None:
                continue
            if str(item.get("kind") or "float") == "int":
                low_value = max(int(item.get("low") or 0), int(current) - 1)
                high_value = min(int(item.get("high") or 0), int(current) + 1)
            else:
                current_value = float(current)
                low_bound = float(item.get("low") or current_value)
                high_bound = float(item.get("high") or current_value)
                low_value = current_value + (low_bound - current_value) * 0.1
                high_value = current_value + (high_bound - current_value) * 0.1
            if low_value != current:
                apply_path_value(low_payload, path, low_value)
                low_changed = True
            if high_value != current:
                apply_path_value(high_payload, path, high_value)
                high_changed = True

        neighbor_payloads = []
        if low_changed:
            neighbor_payloads.append(("low_neighbor", low_payload))
        if high_changed:
            neighbor_payloads.append(("high_neighbor", high_payload))
        if not neighbor_payloads:
            return {"status": "skipped_not_movable", "neighbor_count": 0}

        central_objective = self._objective(
            best_summary,
            optuna_space=optuna_space,
            tuned_params={
                str(item.get("path") or ""): get_path_value(spec_payload, str(item.get("path") or ""))
                for item in parameters
                if str(item.get("path") or "")
            },
        )
        results: list[dict[str, Any]] = []
        for label, payload in neighbor_payloads:
            validated_payload = self._validated_payload(session=session, payload=payload)
            spec = SignalSpec.from_dict(validated_payload)
            try:
                evaluation = await self.evaluator.evaluate(spec, fast_mode=True)
                summary = dict(evaluation.get("summary") or {})
                objective = self._objective(
                    summary,
                    evaluation=evaluation,
                    optuna_space=optuna_space,
                    tuned_params={
                        str(item.get("path") or ""): get_path_value(validated_payload, str(item.get("path") or ""))
                        for item in parameters
                        if str(item.get("path") or "")
                    },
                )
                passed = bool(summary.get("passed"))
                status = "ok"
            except Exception as exc:  # noqa: BLE001
                summary = {}
                objective = -1e9
                passed = False
                status = f"error:{type(exc).__name__}"
            results.append(
                {
                    "label": label,
                    "spec_hash": SignalSpec.from_dict(validated_payload).strategy_hash(),
                    "objective": objective,
                    "passed": passed,
                    "status": status,
                    "summary": {
                        "aggregate_score": summary.get("aggregate_score"),
                        "validation_total_return": summary.get("validation_total_return"),
                        "pre_audit_canonical_total_return": summary.get("pre_audit_canonical_total_return"),
                        "passed": summary.get("passed"),
                    },
                    "param_diff_summary": summarize_patch(
                        _payload_patch_for_paths(
                            base_payload=spec_payload,
                            target_payload=validated_payload,
                            paths=[str(item.get("path") or "") for item in parameters],
                        )
                    ),
                }
            )

        objectives = [float(row.get("objective") or -1e18) for row in results]
        passed_fraction = sum(1 for row in results if bool(row.get("passed"))) / len(results)
        mean_objective = sum(objectives) / len(objectives)
        std_objective = statistics.pstdev(objectives) if len(objectives) > 1 else 0.0
        stability_penalty = 0.0
        if any(not bool(row.get("passed")) for row in results):
            stability_penalty += 1.0
        stability_penalty += max(0.0, central_objective - mean_objective) * 0.5
        stability_penalty += std_objective * 0.25
        return {
            "status": "ok" if passed_fraction == 1.0 else "fragile",
            "neighbor_count": len(results),
            "passed_fraction": passed_fraction,
            "mean_objective": mean_objective,
            "min_objective": min(objectives),
            "std_objective": std_objective,
            "stability_penalty": stability_penalty,
            "neighbors": results,
        }

    def _warm_start_priority(
        self,
        *,
        row: dict[str, Any],
        spec_payload: dict[str, Any],
    ) -> tuple[int, int, int, float]:
        payload = dict(row.get("spec") or {})
        target_features = self._feature_signature(spec_payload)
        payload_features = self._feature_signature(payload)
        same_feature_hash = int(payload_features == target_features)
        same_book = int(
            get_path_value(payload, "params.long_count") == get_path_value(spec_payload, "params.long_count")
            and get_path_value(payload, "params.short_count") == get_path_value(spec_payload, "params.short_count")
        )
        gate_match_count = self._matching_gate_expression_count(
            source_payload=payload,
            target_payload=spec_payload,
        )
        aggregate_score = float(dict(row.get("summary") or {}).get("aggregate_score") or -1e18)
        return (same_feature_hash, same_book, gate_match_count, aggregate_score)

    def _seed_path_compatible(
        self,
        *,
        path: str,
        source_payload: dict[str, Any],
        target_payload: dict[str, Any],
    ) -> bool:
        match = re.match(r"^regime_gates\.entry\[(\d+)\]\.(min|max)$", path)
        if not match:
            return True
        gate_index = int(match.group(1))
        source_gate = self._entry_gate(source_payload, gate_index)
        target_gate = self._entry_gate(target_payload, gate_index)
        if not source_gate or not target_gate:
            return False
        return str(source_gate.get("expression") or "").strip() == str(target_gate.get("expression") or "").strip()

    def _value_in_space(self, *, value: Any, space_item: dict[str, Any]) -> bool:
        kind = str(space_item.get("kind") or "float")
        if kind == "int":
            try:
                numeric = int(value)
            except (TypeError, ValueError):
                return False
            return int(space_item.get("low") or 0) <= numeric <= int(space_item.get("high") or 0)
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return False
        return float(space_item.get("low") or 0.0) <= numeric <= float(space_item.get("high") or 0.0)

    def _feature_signature(self, payload: dict[str, Any]) -> tuple[str, ...]:
        return tuple(sorted(str(feature) for feature in list(payload.get("features") or [])))

    def _matching_gate_expression_count(
        self,
        *,
        source_payload: dict[str, Any],
        target_payload: dict[str, Any],
    ) -> int:
        source_gates = list(dict(source_payload.get("regime_gates") or {}).get("entry") or [])
        target_gates = list(dict(target_payload.get("regime_gates") or {}).get("entry") or [])
        matches = 0
        for index, target_gate in enumerate(target_gates):
            if index >= len(source_gates):
                continue
            if str(dict(source_gates[index] or {}).get("expression") or "").strip() == str(dict(target_gate or {}).get("expression") or "").strip():
                matches += 1
        return matches

    def _entry_gate(self, payload: dict[str, Any], index: int) -> dict[str, Any]:
        entry = list(dict(payload.get("regime_gates") or {}).get("entry") or [])
        if index >= len(entry):
            return {}
        return dict(entry[index] or {})


def infer_optuna_space(spec_payload: dict[str, Any]) -> dict[str, Any]:
    family = str(spec_payload.get("family") or "")
    params: list[dict[str, Any]] = []
    risk = dict(spec_payload.get("risk") or {})
    policy = dict(spec_payload.get("params") or {})
    entry_gates = list(dict(spec_payload.get("regime_gates") or {}).get("entry") or [])

    def add_float(path: str, value: Any, low: float, high: float, *, log: bool = False) -> None:
        current = _float_or_none(value)
        if current is None:
            return
        params.append(
            {
                "path": path,
                "kind": "float",
                "low": low,
                "high": high,
                "log": log,
                "default": current,
            }
        )

    def add_int(path: str, value: Any, low: int, high: int) -> None:
        try:
            current = int(value)
        except (TypeError, ValueError):
            return
        params.append(
            {
                "path": path,
                "kind": "int",
                "low": low,
                "high": high,
                "default": current,
            }
        )

    add_float(
        "risk.max_asset_weight",
        risk.get("max_asset_weight"),
        low=max(0.1, float(risk.get("max_asset_weight", 0.35)) * 0.7),
        high=min(1.0, float(risk.get("max_asset_weight", 0.35)) * 1.3 + 0.02),
    )
    add_float(
        "risk.rebalance_threshold",
        risk.get("rebalance_threshold"),
        low=max(0.005, float(risk.get("rebalance_threshold", 0.03)) * 0.5),
        high=min(0.1, float(risk.get("rebalance_threshold", 0.03)) * 1.5 + 0.005),
    )
    add_float(
        "risk.max_leverage",
        risk.get("max_leverage"),
        low=max(0.5, float(risk.get("max_leverage", 1.0)) * 0.75),
        high=min(4.0, float(risk.get("max_leverage", 1.0)) * 1.5),
    )
    add_float(
        "params.gross_target",
        policy.get("gross_target"),
        low=max(0.1, float(policy.get("gross_target", 1.0)) * 0.7),
        high=min(3.0, float(policy.get("gross_target", 1.0)) * 1.3 + 0.1),
    )
    add_float(
        "params.min_abs_score",
        policy.get("min_abs_score"),
        low=max(0.0, float(policy.get("min_abs_score", 0.0)) * 0.5),
        high=min(1.5, max(0.2, float(policy.get("min_abs_score", 0.0)) * 1.5 + 0.05)),
    )
    if family in {"perp_pair_trade_unlevered", "perp_pair_trade_levered"}:
        add_float(
            "params.max_gross_target",
            policy.get("max_gross_target"),
            low=max(
                float(policy.get("gross_target", 1.0)),
                float(policy.get("max_gross_target", policy.get("gross_target", 1.0))) * 0.75,
            ),
            high=min(4.0, float(policy.get("max_gross_target", policy.get("gross_target", 1.0))) * 1.4),
        )
        add_float(
            "params.signal_leverage_scale",
            policy.get("signal_leverage_scale"),
            low=0.2,
            high=3.0,
        )
        for path in ["params.entry_abs_score", "params.exit_abs_score", "params.flip_abs_score"]:
            add_float(path, get_path_value(spec_payload, path), low=0.0, high=1.5)
        add_int("params.max_holding_bars", policy.get("max_holding_bars"), low=0, high=24 * 14)
        add_int("params.cooldown_bars", policy.get("cooldown_bars"), low=0, high=24 * 7)
    for index, gate in enumerate(entry_gates):
        if not isinstance(gate, dict):
            continue
        if gate.get("min") is not None:
            low, high, log = _threshold_bounds(gate.get("min"))
            add_float(f"regime_gates.entry[{index}].min", gate.get("min"), low=low, high=high, log=log)
        if gate.get("max") is not None:
            low, high, log = _threshold_bounds(gate.get("max"))
            add_float(f"regime_gates.entry[{index}].max", gate.get("max"), low=low, high=high, log=log)
    return {
        "family": family,
        "parameters": params,
    }


def _payload_patch_for_paths(
    *,
    base_payload: dict[str, Any],
    target_payload: dict[str, Any],
    paths: list[str],
) -> dict[str, Any]:
    changes: list[dict[str, Any]] = []
    for path in paths:
        changes.append(
            {
                "path": path,
                "old": get_path_value(base_payload, path),
                "new": get_path_value(target_payload, path),
            }
        )
    return {
        "changes": changes,
    }


def _suggest_params(*, trial: optuna.trial.Trial, optuna_space: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for item in list(optuna_space.get("parameters") or []):
        path = str(item.get("path") or "")
        kind = str(item.get("kind") or "float")
        if kind == "int":
            params[path] = trial.suggest_int(
                path,
                int(item.get("low") or 0),
                int(item.get("high") or 1),
            )
            continue
        params[path] = trial.suggest_float(
            path,
            float(item.get("low") or 0.0),
            float(item.get("high") or 1.0),
            log=bool(item.get("log")),
        )
    return params


def _threshold_bounds(value: Any) -> tuple[float, float, bool]:
    current = _float_or_none(value)
    if current is None:
        return 0.0, 1.0, False
    magnitude = abs(current)
    if magnitude > 0.0 and magnitude < 0.05:
        return max(1e-6, magnitude / 4.0), max(magnitude * 4.0, 1e-5), True
    if current >= 0.0:
        return max(0.0, current * 0.5), max(current * 1.5 + 0.01, 0.02), False
    return current * 1.5 - 0.01, min(current * 0.5, -1e-6), False


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


