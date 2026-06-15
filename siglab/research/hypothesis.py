from __future__ import annotations

import copy
import json
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd

from siglab.data.store import ParquetLake
from siglab.data.feeds import MarketDataProvider
from siglab.evaluator import ResearchEvaluator
from siglab.evaluator.compile import (
    PAIR_TRADE_FAMILIES,
    _pair_raw_frames,
    _perp_raw_frames,
    compile_spec,
)
from siglab.evaluation.feature_dsl import (
    is_valid_feature_expression,
    load_feature_spec,
    resolve_feature_frames,
)
from siglab.utils import short_hash

from siglab.llm import ClaudeTool
from siglab.schemas import SignalSpec
from siglab.search import LineageStore
from siglab.config import SiglabConfig
from siglab.evaluator.backtesting import BacktestConfig, run_backtest

DEFAULT_HORIZONS = (6, 24, 72, 168)
MAX_COMPARE_FEATURES = 6
MIN_WINDOW_OBSERVATIONS = 24
MAX_INSPECT_EPISODES = 12
MAX_GATE_COUNT = 12


class HypothesisSandbox:
    def __init__(
        self,
        settings: SiglabConfig,
        lake: ParquetLake,
        provider: MarketDataProvider,
    ) -> None:
        self.settings = settings
        self.lake = lake
        self.provider = provider
        self._evaluator = ResearchEvaluator(settings, provider)
        self._ancestry = LineageStore(settings.ancestry_db_path)

    def claude_tools(
        self,
        *,
        track: str,
        parent: SignalSpec,
        memory_scope: str = "track_shared",
        run_session_id: str | None = None,
    ) -> list[ClaudeTool]:
        if track != "trend_signals":
            return []
        return [
            ClaudeTool(
                name="probe_feature_forward_stats",
                description=(
                    "Compile a proposed directional-perps feature on train-only history. "
                    "Returns forward-return predictiveness across several horizons plus "
                    "correlations against parent predictors. Validation and audit slices "
                    "are excluded."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "feature": {
                            "type": "string",
                            "description": "Feature alias or DSL formula to analyze.",
                        },
                        "family": {
                            "type": "string",
                            "description": (
                                "Optional family override. Defaults to the current parent family."
                            ),
                        },
                        "basis_groups": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Optional asset symbols to analyze. For pair families, pass two "
                                "symbols. Defaults to the current parent basis groups."
                            ),
                        },
                        "compare_features": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Optional features to measure redundancy against. Defaults to the "
                                "current parent features."
                            ),
                        },
                        "horizons": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "description": (
                                "Optional forward-return horizons in bars. Defaults to "
                                "[6, 24, 72, 168]."
                            ),
                        },
                    },
                    "required": ["feature"],
                },
                handler=lambda arguments: self._tool_probe_feature_forward_stats(
                    track=track,
                    parent=parent,
                    arguments=arguments,
                ),
            ),
            ClaudeTool(
                name="probe_spec_gate_impact",
                description=(
                    "Train-only A/B check for directional-perps regime gates. "
                    "Compares the same spec with and without its regime gates, "
                    "reports gate coverage, selector-train performance deltas, and "
                    "whether the gates actually filter anything. Validation and audit "
                    "slices are excluded."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "spec_hash": {
                            "type": "string",
                            "description": (
                                "Optional stored spec hash to use as the base spec. "
                                "If omitted, the current parent is used."
                            ),
                        },
                        "family": {
                            "type": "string",
                            "description": "Optional family override.",
                        },
                        "basis_groups": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional symbol override for the spec universe.",
                        },
                        "features": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional feature override. Defaults to the base spec features.",
                        },
                        "regime_gates": {
                            "type": "object",
                            "description": (
                                "Optional regime gate override. Defaults to the base spec "
                                "regime_gates. Use this to test whether proposed gates really bind."
                            ),
                        },
                        "params": {
                            "type": "object",
                            "description": "Optional params override, usually only when testing threshold ideas.",
                        },
                        "neutrality_basis": {
                            "type": "string",
                            "description": "Optional neutrality_basis override.",
                        },
                        "horizons": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "description": (
                                "Optional forward-return horizons in bars used for kept-vs-blocked "
                                "bar comparisons. Defaults to [6, 24, 72, 168]."
                            ),
                        },
                    },
                },
                handler=lambda arguments: self._tool_probe_spec_gate_impact(
                    track=track,
                    parent=parent,
                    arguments=arguments,
                ),
            ),
            ClaudeTool(
                name="inspect_pre_audit_spec",
                description=(
                    "Inspect a stored directional-perps spec using pre-audit diagnostics only. "
                    "Returns filtered pre-audit trade episodes plus parent/child ancestry context. "
                    "Audit slices are excluded."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "spec_hash": {
                            "type": "string",
                            "description": "Spec hash to inspect.",
                        },
                        "direction": {
                            "type": "string",
                            "enum": [
                                "long_asset_1_short_asset_2",
                                "short_asset_1_long_asset_2",
                            ],
                            "description": "Optional episode direction filter.",
                        },
                        "pnl_sign": {
                            "type": "string",
                            "enum": ["positive", "negative"],
                            "description": "Optional episode return sign filter.",
                        },
                        "holding_bucket": {
                            "type": "string",
                            "enum": ["bars_1_6", "bars_7_24", "bars_25_72", "bars_73_plus"],
                            "description": "Optional holding-duration bucket filter.",
                        },
                        "regime_dimension": {
                            "type": "string",
                            "enum": [
                                "market_trend",
                                "pair_volatility",
                                "funding_dispersion",
                                "pair_correlation",
                                "pair_direction",
                            ],
                            "description": "Optional regime dimension to filter on.",
                        },
                        "regime_label": {
                            "type": "string",
                            "description": "Optional entry-regime label filter, for example low_correlation.",
                        },
                        "limit": {
                            "type": "integer",
                            "description": f"Maximum number of episodes to return. Defaults to {MAX_INSPECT_EPISODES}.",
                        },
                    },
                    "required": ["spec_hash"],
                },
                handler=lambda arguments: self._tool_inspect_pre_audit_spec(
                    track=track,
                    arguments=arguments,
                    memory_scope=memory_scope,
                    run_session_id=run_session_id,
                ),
            ),
            ClaudeTool(
                name="summarize_experiment_frontier",
                description=(
                    "Summarize the current directional-perps experiment frontier using pre-audit-safe "
                    "metrics only. Reports which families and feature neighborhoods are producing the "
                    "best pre-audit results, recent run patterns, and repeated weak motifs. Audit is excluded."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "family": {
                            "type": "string",
                            "description": "Optional family filter.",
                        },
                        "top_n": {
                            "type": "integer",
                            "description": "Number of strongest and weakest anchors to summarize. Defaults to 5.",
                        },
                        "recent_limit": {
                            "type": "integer",
                            "description": "Number of recent runs to include. Defaults to 8.",
                        },
                        "include_deterministic": {
                            "type": "boolean",
                            "description": (
                                "Whether to include deterministic burn-in runs. Defaults to true so "
                                "the frontier includes strong non-audit seed anchors when they exist."
                            ),
                        },
                    },
                },
                handler=lambda arguments: self._tool_summarize_experiment_frontier(
                    track=track,
                    arguments=arguments,
                    memory_scope=memory_scope,
                    run_session_id=run_session_id,
                ),
            ),
            ClaudeTool(
                name="compare_intended_vs_frozen_spec",
                description=(
                    "Compare a stored LLM-proposed directional-perps spec against the "
                    "evaluated frozen spec. Reports policy drift, dropped or added regime "
                    "gates, and whether the compiled gates actually bound. Audit is excluded."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "spec_hash": {
                            "type": "string",
                            "description": "Stored spec hash to inspect.",
                        },
                    },
                    "required": ["spec_hash"],
                },
                handler=lambda arguments: self._tool_compare_intended_vs_frozen_spec(
                    track=track,
                    arguments=arguments,
                ),
            ),
        ]

    async def _tool_probe_feature_forward_stats(
        self,
        *,
        track: str,
        parent: SignalSpec,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        feature = str(arguments.get("feature") or "").strip()
        if not feature:
            return {"ok": False, "error": "feature is required"}

        family = str(arguments.get("family") or parent.family).strip() or parent.family
        basis_groups = [
            str(symbol).upper()
            for symbol in list(arguments.get("basis_groups") or parent.universe.basis_groups)
        ]
        if not basis_groups:
            basis_groups = list(parent.universe.basis_groups)

        compare_features = [
            str(item).strip()
            for item in list(arguments.get("compare_features") or parent.features)
            if str(item).strip()
        ][:MAX_COMPARE_FEATURES]
        compare_features = [item for item in compare_features if item != feature]
        horizons = _sanitize_horizons(arguments.get("horizons"))

        cache_key = self._cache_key(
            track=track,
            family=family,
            basis_groups=basis_groups,
            feature=feature,
            compare_features=compare_features,
            horizons=horizons,
        )
        cached = self.lake.latest_json("feature_probe", cache_key, max_age_hours=6)
        if cached is not None:
            return dict(cached)

        try:
            probe_spec = self._probe_spec(
                parent=parent,
                family=family,
                basis_groups=basis_groups,
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

        feature_spec = load_feature_spec(
            self.settings.root_dir,
            track=track,
            family=probe_spec.family,
        )
        aliases = feature_spec.get("aliases") or {}
        raw_series = set(feature_spec.get("raw_series") or [])
        if not self._is_valid_feature(feature, aliases=aliases, raw_series=raw_series):
            return {
                "ok": False,
                "error": "invalid_feature_expression",
                "feature": feature,
                "family": probe_spec.family,
            }

        valid_compare_features: list[str] = []
        invalid_compare_features: list[str] = []
        for item in compare_features:
            if self._is_valid_feature(item, aliases=aliases, raw_series=raw_series):
                valid_compare_features.append(item)
            else:
                invalid_compare_features.append(item)

        try:
            raw_context = await self._raw_context(probe_spec)
            resolved = resolve_feature_frames(
                [feature, *valid_compare_features],
                aliases=aliases,
                raw_frames=raw_context["raw_frames"],
            )
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "feature": feature,
                "family": probe_spec.family,
            }

        feature_frame = resolved[feature]
        train_windows, plan = self._train_windows(raw_context["target_source"].index)
        if not train_windows:
            return {"ok": False, "error": "no_train_windows_available"}

        predictive_summary = self._predictive_summary(
            feature_frame=feature_frame,
            target_source=raw_context["target_source"],
            train_windows=train_windows,
            horizons=horizons,
        )
        predictor_correlations = self._predictor_correlations(
            feature_frame=feature_frame,
            resolved_frames=resolved,
            compare_features=valid_compare_features,
            train_windows=train_windows,
        )
        response = {
            "ok": True,
            "feature": feature,
            "family": probe_spec.family,
            "basis_groups": list(raw_context["symbols"]),
            "interval": probe_spec.universe.interval,
            "lookback_days": probe_spec.universe.lookback_days,
            "data_source": raw_context["source"],
            "analysis_scope": {
                "mode": "train_only",
                "selector_scope": str(plan.get("selector_scope", "in_sample_only")),
                "validation_excluded": True,
                "audit_excluded": True,
                "train_window_count": len(train_windows),
                "train_windows": [
                    {
                        "label": str(window["label"]),
                        "start_timestamp": str(window["start_timestamp"]),
                        "end_timestamp": str(window["end_timestamp"]),
                    }
                    for window in train_windows[:6]
                ],
                "visual_split_note": str(plan["visual_split"].get("note") or ""),
            },
            "feature_profile": self._feature_profile(
                feature_frame=feature_frame,
                train_windows=train_windows,
            ),
            "forward_return_predictiveness": predictive_summary["horizons"],
            "best_directional_horizon": predictive_summary["best_horizon"],
            "predictor_correlations": predictor_correlations,
            "invalid_compare_features": invalid_compare_features,
        }
        self.lake.write_json("feature_probe", cache_key, response)
        return response

    async def _tool_inspect_pre_audit_spec(
        self,
        *,
        track: str,
        arguments: dict[str, Any],
        memory_scope: str,
        run_session_id: str | None,
    ) -> dict[str, Any]:
        spec_hash = str(arguments.get("spec_hash") or "").strip()
        if not spec_hash:
            return {"ok": False, "error": "spec_hash is required"}

        detail = self._ancestry.experiment_detail(spec_hash)
        if detail is None:
            return {"ok": False, "error": "spec_not_found", "spec_hash": spec_hash}
        if str(detail.get("track") or "") != track:
            return {
                "ok": False,
                "error": "spec_track_mismatch",
                "spec_hash": spec_hash,
                "track": detail.get("track"),
            }

        artifact = dict(detail.get("artifact") or {})
        canonical_run = dict(artifact.get("canonical_run") or {})
        if not canonical_run:
            return {"ok": False, "error": "spec_artifact_missing", "spec_hash": spec_hash}

        pre_audit_episodes = _pre_audit_trade_episodes(canonical_run)
        filters = {
            "direction": str(arguments.get("direction") or "").strip() or None,
            "pnl_sign": str(arguments.get("pnl_sign") or "").strip() or None,
            "holding_bucket": str(arguments.get("holding_bucket") or "").strip() or None,
            "regime_dimension": str(arguments.get("regime_dimension") or "").strip() or None,
            "regime_label": str(arguments.get("regime_label") or "").strip() or None,
        }
        limit = _sanitize_limit(arguments.get("limit"), default=MAX_INSPECT_EPISODES)
        filtered_episodes = _filter_trade_episodes(pre_audit_episodes, filters=filters)

        return {
            "ok": True,
            "spec_hash": spec_hash,
            "track": str(detail.get("track") or ""),
            "family": str(detail.get("family") or ""),
            "parent_hash": detail.get("parent_hash"),
            "spec": {
                "hypothesis": str((detail.get("spec") or {}).get("hypothesis") or ""),
                "features": list((detail.get("spec") or {}).get("features") or [])[:10],
                "basis_groups": list((((detail.get("spec") or {}).get("universe") or {}).get("basis_groups")) or []),
                "params": _compact_spec_params((detail.get("spec") or {}).get("params") or {}),
            },
            "summary": _compact_summary(detail.get("summary") or {}),
            "pre_audit_context": {
                "drawdown_pack": _strip_audit_fields(
                    dict(canonical_run.get("pre_audit_drawdown_pack") or {})
                ),
                "gate_diagnostics": _strip_audit_fields(
                    dict((canonical_run.get("pre_audit_context_pack") or {}).get("gate_diagnostics") or {})
                ),
                "equity_shift_pack": _strip_audit_fields(
                    dict((canonical_run.get("pre_audit_context_pack") or {}).get("equity_shift_pack") or {})
                ),
                "time_bin_pack": _strip_audit_fields(
                    dict((canonical_run.get("pre_audit_context_pack") or {}).get("time_bin_pack") or {})
                ),
                "exemplar_trades": _strip_audit_fields(
                    dict((canonical_run.get("pre_audit_context_pack") or {}).get("exemplar_trades") or {})
                ),
            },
            "ancestry": _spec_ancestry_summary(
                ancestry=self._ancestry,
                track=track,
                spec_hash=spec_hash,
                run_session_id=run_session_id if memory_scope == "session_local" else None,
            ),
            "episode_filter": {
                "applied": {key: value for key, value in filters.items() if value},
                "all_pre_audit_episode_count": len(pre_audit_episodes),
                "matching_episode_count": len(filtered_episodes),
                "returned_episode_count": min(len(filtered_episodes), limit),
            },
            "filtered_episode_summary": _episode_summary(filtered_episodes),
            "trade_episodes": [
                _strip_audit_fields(_compact_trade_episode(episode))
                for episode in filtered_episodes[:limit]
            ],
        }

    async def _tool_summarize_experiment_frontier(
        self,
        *,
        track: str,
        arguments: dict[str, Any],
        memory_scope: str,
        run_session_id: str | None,
    ) -> dict[str, Any]:
        family_filter = str(arguments.get("family") or "").strip() or None
        top_n = _sanitize_limit(arguments.get("top_n"), default=5)
        recent_limit = _sanitize_limit(arguments.get("recent_limit"), default=8)
        include_deterministic = bool(arguments.get("include_deterministic", True))

        rows = self._ancestry.dashboard_rows(
            track=track,
            family=family_filter,
            run_session_id=run_session_id if memory_scope == "session_local" else None,
        )
        if not include_deterministic:
            rows = [row for row in rows if not self._ancestry._is_deterministic_experiment(row)]
        if not rows:
            return {
                "ok": False,
                "error": "no_matching_experiments",
                "track": track,
                "family": family_filter,
            }

        frontier_rows: list[dict[str, Any]] = []
        for row in rows:
            summary = dict(row.get("summary") or {})
            spec = dict(row.get("spec") or {})
            artifact = _artifact_payload(row.get("artifact_path"))
            gate_diagnostics = dict(
                ((artifact.get("canonical_run") or {}).get("pre_audit_context_pack") or {}).get(
                    "gate_diagnostics"
                )
                or {}
            )
            frontier_rows.append(
                {
                    "created_at": str(row.get("created_at") or ""),
                    "spec_hash": str(row.get("spec_hash") or ""),
                    "family": str(row.get("family") or "unknown"),
                    "parent_hash": row.get("parent_hash"),
                    "aggregate_score": _coerce_float(row.get("aggregate_score")),
                    "passed": bool(row.get("passed")),
                    "deployd": bool(row.get("deployd")),
                    "deterministic": self._ancestry._is_deterministic_experiment(row),
                    "hypothesis": str(spec.get("hypothesis") or ""),
                    "features": [str(feature) for feature in list(spec.get("features") or [])[:10]],
                    "basis_groups": list((spec.get("universe") or {}).get("basis_groups") or []),
                    "summary": {
                        "median_total_return": _coerce_float(summary.get("median_total_return")),
                        "validation_total_return": _coerce_float(summary.get("validation_total_return")),
                        "pre_audit_canonical_total_return": _coerce_float(
                            summary.get("pre_audit_canonical_total_return")
                        ),
                        "pre_audit_canonical_max_drawdown": _coerce_float(
                            summary.get("pre_audit_canonical_max_drawdown")
                        ),
                    },
                    "active_bar_fraction": _coerce_float(gate_diagnostics.get("active_bar_fraction")),
                    "gate_bottlenecks": list(gate_diagnostics.get("bottleneck_tags") or [])[:4],
                }
            )

        positive_rows = [
            row
            for row in frontier_rows
            if (row["summary"].get("pre_audit_canonical_total_return") or 0.0) > 0.0
        ]
        family_summary = _frontier_family_summary(frontier_rows)
        top_positive = sorted(
            positive_rows or frontier_rows,
            key=lambda row: (
                row["summary"].get("pre_audit_canonical_total_return")
                if row["summary"].get("pre_audit_canonical_total_return") is not None
                else float("-inf"),
                row["summary"].get("validation_total_return")
                if row["summary"].get("validation_total_return") is not None
                else float("-inf"),
                row.get("aggregate_score") if row.get("aggregate_score") is not None else float("-inf"),
            ),
            reverse=True,
        )[:top_n]
        weakest = sorted(
            frontier_rows,
            key=lambda row: (
                row["summary"].get("pre_audit_canonical_total_return")
                if row["summary"].get("pre_audit_canonical_total_return") is not None
                else float("inf"),
                row["summary"].get("validation_total_return")
                if row["summary"].get("validation_total_return") is not None
                else float("inf"),
            ),
        )[:top_n]
        recent_rows = sorted(
            frontier_rows,
            key=lambda row: row.get("created_at") or "",
            reverse=True,
        )[:recent_limit]

        response = {
            "ok": True,
            "track": track,
            "family_filter": family_filter,
            "analysis_scope": {
                "audit_excluded": True,
                "include_deterministic": include_deterministic,
                "experiments_considered": len(frontier_rows),
                "positive_pre_audit_runs": len(positive_rows),
                "non_deterministic_runs": sum(1 for row in frontier_rows if not row["deterministic"]),
            },
            "family_summary": family_summary,
            "top_positive_anchors": [_frontier_row_payload(row) for row in top_positive],
            "weakest_runs": [_frontier_row_payload(row) for row in weakest],
            "recent_runs": [_frontier_row_payload(row) for row in recent_rows],
            "positive_feature_frequencies": _feature_frequency_summary(
                top_positive,
                limit=10,
            ),
            "negative_feature_frequencies": _feature_frequency_summary(
                weakest,
                limit=10,
            ),
            "motif_warnings": _frontier_warnings(
                frontier_rows=frontier_rows,
                positive_rows=positive_rows,
                recent_rows=recent_rows,
            ),
        }
        return response

    async def _tool_probe_spec_gate_impact(
        self,
        *,
        track: str,
        parent: SignalSpec,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        horizons = _sanitize_horizons(arguments.get("horizons"))
        try:
            spec = self._spec_from_tool_arguments(
                track=track,
                parent=parent,
                arguments=arguments,
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

        cache_key = self._gate_probe_cache_key(
            track=track,
            spec=spec,
            horizons=horizons,
        )
        cached = self.lake.latest_json("gate_probe", cache_key, max_age_hours=6)
        if cached is not None:
            return dict(cached)

        if not dict(spec.regime_gates or {}).get("entry"):
            return {
                "ok": False,
                "error": "spec_has_no_regime_gates",
                "spec_hash": str(arguments.get("spec_hash") or "").strip() or None,
            }

        try:
            gated = await compile_spec(self.settings, self.provider, spec)
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "error": f"compile_failed: {type(exc).__name__}: {exc}",
            }

        ungated_spec = SignalSpec.from_dict(spec.canonical_dict())
        ungated_spec.regime_gates = {}
        try:
            ungated = await compile_spec(self.settings, self.provider, ungated_spec)
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "error": f"ungated_compile_failed: {type(exc).__name__}: {exc}",
            }

        prices = gated.prices.sort_index()
        if len(prices.index) > 1:
            prices = prices.iloc[:-1]
        if prices.empty:
            return {"ok": False, "error": "insufficient_price_history"}

        raw_context = await self._raw_context(spec)
        train_windows, plan = self._train_windows(prices.index)
        if not train_windows:
            return {"ok": False, "error": "no_train_windows_available"}

        gated_summary = self._train_only_policy_summary(
            spec=spec,
            compiled=gated,
            prices=prices,
            train_windows=train_windows,
        )
        ungated_summary = self._train_only_policy_summary(
            spec=ungated_spec,
            compiled=ungated,
            prices=prices,
            train_windows=train_windows,
        )
        gated_mask = gated.regime_gate_mask.reindex(prices.index).fillna(False) if gated.regime_gate_mask is not None else pd.Series(True, index=prices.index, dtype=bool)
        forward_profile = self._gate_forward_return_profile(
            target_source=raw_context["target_source"].reindex(prices.index),
            gate_mask=gated_mask,
            train_windows=train_windows,
            horizons=horizons,
        )
        gate_metadata = dict(gated.metadata.get("regime_gates") or {})
        active_fraction = _clean_float(float(gated_mask.mean())) if len(gated_mask.index) else 0.0
        warnings = self._gate_probe_warnings(
            gate_metadata=gate_metadata,
            active_fraction=active_fraction,
            gated_summary=gated_summary,
            ungated_summary=ungated_summary,
        )

        response = {
            "ok": True,
            "spec_blueprint": {
                "spec_hash": str(arguments.get("spec_hash") or "").strip() or None,
                "family": spec.family,
                "basis_groups": list(spec.universe.basis_groups),
                "features": list(spec.features)[:10],
                "params": _compact_spec_params(spec.params),
                "regime_gates": _strip_audit_fields(dict(spec.regime_gates or {})),
            },
            "analysis_scope": {
                "mode": "train_only",
                "selector_scope": str(plan.get("selector_scope", "in_sample_only")),
                "validation_excluded": True,
                "audit_excluded": True,
                "train_window_count": len(train_windows),
            },
            "gate_coverage": {
                "configured": bool(gate_metadata.get("configured")),
                "gate_count": len(list(gate_metadata.get("entry") or [])),
                "combined_active_fraction": active_fraction,
                "entry": list(gate_metadata.get("entry") or []),
            },
            "selector_train_comparison": {
                "gated": gated_summary,
                "ungated": ungated_summary,
                "delta": _gate_probe_delta(gated_summary, ungated_summary),
            },
            "kept_vs_blocked_forward_returns": forward_profile,
            "warnings": warnings,
        }
        self.lake.write_json("gate_probe", cache_key, response)
        return response

    async def _tool_compare_intended_vs_frozen_spec(
        self,
        *,
        track: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        spec_hash = str(arguments.get("spec_hash") or "").strip()
        if not spec_hash:
            return {"ok": False, "error": "spec_hash is required"}

        detail = self._ancestry.experiment_detail(spec_hash)
        if detail is None:
            return {"ok": False, "error": "spec_not_found", "spec_hash": spec_hash}
        if str(detail.get("track") or "") != track:
            return {
                "ok": False,
                "error": "spec_track_mismatch",
                "spec_hash": spec_hash,
                "track": detail.get("track"),
            }

        research_summary = dict(detail.get("research_summary") or {})
        llm_trace = dict((research_summary.get("llm_tool_trace") or {}).get("trace") or {})
        log_path = str((research_summary.get("llm_tool_trace") or {}).get("log_path") or "").strip()
        proposed_spec = _llm_spec_from_log(log_path) if log_path else None
        if proposed_spec is None:
            return {
                "ok": False,
                "error": "no_llm_intent_available",
                "spec_hash": spec_hash,
            }

        evaluated_spec = SignalSpec.from_dict(dict(detail.get("spec") or {}))
        summary = dict(detail.get("summary") or {})
        artifact = dict(detail.get("artifact") or {})
        compiled_metadata = dict(artifact.get("compiled_metadata") or {})
        compiled_regime_gates = dict(compiled_metadata.get("regime_gates") or {})

        proposed_gates = list(dict(proposed_spec.get("regime_gates") or {}).get("entry") or [])
        evaluated_gates = list(dict(evaluated_spec.regime_gates or {}).get("entry") or [])
        proposed_gate_exprs = {_gate_expression(spec) for spec in proposed_gates if _gate_expression(spec)}
        evaluated_gate_exprs = {_gate_expression(spec) for spec in evaluated_gates if _gate_expression(spec)}
        feature_overlap = len(set(proposed_spec.get("features") or []) & set(evaluated_spec.features))
        feature_union = len(set(proposed_spec.get("features") or []) | set(evaluated_spec.features))

        response = {
            "ok": True,
            "spec_hash": spec_hash,
            "family": evaluated_spec.family,
            "hypothesis": str(evaluated_spec.hypothesis or ""),
            "proposed_spec": {
                "family": proposed_spec.get("family"),
                "hypothesis": proposed_spec.get("hypothesis"),
                "features": list(proposed_spec.get("features") or [])[:10],
                "regime_gates": _strip_audit_fields(dict(proposed_spec.get("regime_gates") or {})),
                "params": _compact_spec_params(dict(proposed_spec.get("params") or {})),
            },
            "evaluated_spec": {
                "family": evaluated_spec.family,
                "hypothesis": evaluated_spec.hypothesis,
                "features": list(evaluated_spec.features)[:10],
                "regime_gates": _strip_audit_fields(dict(evaluated_spec.regime_gates or {})),
                "params": _compact_spec_params(dict(evaluated_spec.params or {})),
            },
            "intent_alignment": {
                "family_match": str(proposed_spec.get("family") or "") == evaluated_spec.family,
                "feature_overlap_fraction": _clean_float(
                    feature_overlap / feature_union if feature_union else 1.0
                ),
                "proposed_gate_count": len(proposed_gates),
                "evaluated_gate_count": len(evaluated_gates),
                "dropped_gate_expressions": sorted(proposed_gate_exprs - evaluated_gate_exprs),
                "added_gate_expressions": sorted(evaluated_gate_exprs - proposed_gate_exprs),
                "gate_active_fraction": compiled_regime_gates.get("combined_active_fraction"),
                "sweep_drift": {
                    "material_change": bool(summary.get("policy_sweep_material_change")),
                    "changed_keys": list(summary.get("policy_sweep_changed_keys") or []),
                    "activity_penalty": summary.get("policy_sweep_activity_penalty"),
                    "proposed_policy": dict(summary.get("policy_sweep_proposed_policy") or {}),
                    "frozen_policy": dict(summary.get("policy_sweep_frozen_policy") or {}),
                },
            },
            "warnings": _intent_vs_frozen_warnings(
                summary=summary,
                compiled_regime_gates=compiled_regime_gates,
                proposed_gate_exprs=proposed_gate_exprs,
                evaluated_gate_exprs=evaluated_gate_exprs,
            ),
            "trace_metadata": {
                "log_path": log_path or None,
                "tool_names": list(llm_trace.get("tool_names") or []),
                "error": llm_trace.get("error"),
            },
        }
        return response

    def _probe_spec(
        self,
        *,
        parent: SignalSpec,
        family: str,
        basis_groups: list[str],
    ) -> SignalSpec:
        payload = copy.deepcopy(parent.canonical_dict())
        payload["family"] = family
        payload["universe"]["basis_groups"] = list(basis_groups or parent.universe.basis_groups)
        if family in PAIR_TRADE_FAMILIES:
            payload["universe"]["basis_groups"] = list(payload["universe"]["basis_groups"][:2])
            payload["universe"]["max_symbols"] = 2
        return SignalSpec.from_dict(payload)

    def _spec_from_tool_arguments(
        self,
        *,
        track: str,
        parent: SignalSpec,
        arguments: dict[str, Any],
    ) -> SignalSpec:
        spec_hash = str(arguments.get("spec_hash") or "").strip()
        if spec_hash:
            detail = self._ancestry.experiment_detail(spec_hash)
            if detail is None:
                raise ValueError("spec_not_found")
            base_payload = dict(detail.get("spec") or {})
            if str(detail.get("track") or "") != track:
                raise ValueError("spec_track_mismatch")
        else:
            base_payload = parent.canonical_dict()

        payload = copy.deepcopy(base_payload)
        payload["track"] = track
        if arguments.get("family") is not None:
            payload["family"] = str(arguments.get("family") or payload.get("family") or "").strip()
        if arguments.get("neutrality_basis") is not None:
            payload["neutrality_basis"] = str(arguments.get("neutrality_basis") or "").strip() or None
        if arguments.get("basis_groups") is not None:
            payload.setdefault("universe", {})
            payload["universe"]["basis_groups"] = [
                str(symbol).upper()
                for symbol in list(arguments.get("basis_groups") or [])
                if str(symbol).strip()
            ]
        if arguments.get("features") is not None:
            payload["features"] = [
                str(feature).strip()
                for feature in list(arguments.get("features") or [])
                if str(feature).strip()
            ]
        if arguments.get("regime_gates") is not None:
            payload["regime_gates"] = dict(arguments.get("regime_gates") or {})
        if arguments.get("params") is not None:
            merged_params = dict(payload.get("params") or {})
            merged_params.update(dict(arguments.get("params") or {}))
            payload["params"] = merged_params
        spec = SignalSpec.from_dict(payload)
        if spec.family in PAIR_TRADE_FAMILIES and len(spec.universe.basis_groups) != 2:
            raise ValueError("pair_spec_requires_exactly_two_symbols")
        if not spec.features:
            raise ValueError("spec_features_required")
        return spec

    async def _raw_context(self, spec: SignalSpec) -> dict[str, Any]:
        if spec.track != "trend_signals":
            raise ValueError("probe_feature_forward_stats currently supports trend_signals only")

        if spec.family in PAIR_TRADE_FAMILIES:
            requested_symbols = [str(symbol).upper() for symbol in spec.universe.basis_groups[:2]]
            symbols = await self.provider.discover_perp_symbols(requested_symbols, limit=2)
            ordered_symbols = [symbol for symbol in requested_symbols if symbol in symbols]
            for symbol in symbols:
                if symbol not in ordered_symbols:
                    ordered_symbols.append(symbol)
            if len(ordered_symbols) != 2:
                raise ValueError("Pair feature probe requires exactly two supported symbols")
            bundle = await self.provider.fetch_perp_bundle(
                symbols=ordered_symbols,
                lookback_days=spec.universe.lookback_days,
                interval=spec.universe.interval,
            )
            prices = bundle["prices"][ordered_symbols].sort_index()
            if len(prices.index) > 1:
                prices = prices.iloc[:-1]
            funding = (
                bundle["funding"][ordered_symbols]
                .reindex(prices.index)
                .ffill()
                .fillna(0.0)
            )
            raw_frames = _pair_raw_frames(
                prices=prices,
                funding=funding,
                asset_1_symbol=ordered_symbols[0],
                asset_2_symbol=ordered_symbols[1],
            )
            return {
                "raw_frames": raw_frames,
                "target_source": raw_frames["price_ratio"],
                "symbols": ordered_symbols,
                "source": bundle["source"],
            }

        symbols = await self.provider.discover_perp_symbols(
            spec.universe.basis_groups,
            limit=spec.universe.max_symbols,
        )
        bundle = await self.provider.fetch_perp_bundle(
            symbols=symbols,
            lookback_days=spec.universe.lookback_days,
            interval=spec.universe.interval,
        )
        prices = bundle["prices"][symbols].sort_index()
        if len(prices.index) > 1:
            prices = prices.iloc[:-1]
        funding = bundle["funding"][symbols].reindex(prices.index).ffill().fillna(0.0)
        return {
            "raw_frames": _perp_raw_frames(prices, funding),
            "target_source": prices,
            "symbols": symbols,
            "source": bundle["source"],
        }

    def _train_windows(self, index: pd.Index) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        min_rows = max(14, min(30, len(index) // 2))
        plan = self._evaluator._evaluation_plan(index, min_rows=min_rows)
        windows: list[dict[str, Any]] = []
        seen: set[tuple[int, int]] = set()
        selector_scope = str(plan.get("selector_scope", "in_sample_only"))
        for window in list(plan.get("selector_windows") or []):
            if selector_scope == "rolling_validation_chunks":
                start_idx = int(window.get("train_start_idx", 0))
                end_idx = int(window.get("train_end_idx", 0))
                label = str(window.get("label") or "rolling_validation").replace(
                    "_validation",
                    "_train",
                )
            else:
                start_idx = int(window.get("start_idx", 0))
                end_idx = int(window.get("end_idx", 0))
                label = str(window.get("label") or "selector_window")
            if end_idx - start_idx < min_rows:
                continue
            key = (start_idx, end_idx)
            if key in seen:
                continue
            seen.add(key)
            windows.append(
                {
                    "label": label,
                    "start_idx": start_idx,
                    "end_idx": end_idx,
                    "start_timestamp": index[start_idx].isoformat(),
                    "end_timestamp": index[end_idx - 1].isoformat(),
                }
            )
        return windows, plan

    def _predictive_summary(
        self,
        *,
        feature_frame: pd.DataFrame,
        target_source: pd.DataFrame,
        train_windows: list[dict[str, Any]],
        horizons: list[int],
    ) -> dict[str, Any]:
        horizon_rows: list[dict[str, Any]] = []
        best_horizon: dict[str, Any] | None = None
        best_strength = -1.0
        for horizon in horizons:
            rows: list[dict[str, Any]] = []
            for window in train_windows:
                start_idx = int(window["start_idx"])
                end_idx = int(window["end_idx"])
                feature_window = feature_frame.iloc[start_idx:end_idx]
                target_window = target_source.iloc[start_idx:end_idx]
                directional_target = target_window.pct_change(horizon).shift(-horizon)
                directional_stats = _frame_pair_stats(feature_window, directional_target)
                magnitude_stats = _frame_pair_stats(feature_window.abs(), directional_target.abs())
                if int(directional_stats["rows"]) < MIN_WINDOW_OBSERVATIONS:
                    continue
                rows.append(
                    {
                        "rows": int(directional_stats["rows"]),
                        "spearman": directional_stats["spearman"],
                        "pearson": directional_stats["pearson"],
                        "top_bottom_spread": directional_stats["top_bottom_spread"],
                        "bucket_monotonicity": directional_stats["bucket_monotonicity"],
                        "abs_feature_abs_return_spearman": magnitude_stats["spearman"],
                    }
                )
            aggregate = _aggregate_predictive_rows(rows)
            aggregate["horizon_bars"] = horizon
            aggregate["interval"] = "1h"
            horizon_rows.append(aggregate)
            strength = abs(float(aggregate.get("median_spearman") or 0.0))
            if aggregate.get("available") and strength > best_strength:
                best_strength = strength
                best_horizon = {
                    "horizon_bars": horizon,
                    "median_spearman": aggregate.get("median_spearman"),
                    "median_top_bottom_spread": aggregate.get("median_top_bottom_spread"),
                }
        return {
            "horizons": horizon_rows,
            "best_horizon": best_horizon,
        }

    def _predictor_correlations(
        self,
        *,
        feature_frame: pd.DataFrame,
        resolved_frames: dict[str, pd.DataFrame],
        compare_features: list[str],
        train_windows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for compare_feature in compare_features:
            compare_frame = resolved_frames.get(compare_feature)
            if compare_frame is None:
                continue
            correlations: list[dict[str, Any]] = []
            for window in train_windows:
                start_idx = int(window["start_idx"])
                end_idx = int(window["end_idx"])
                stats = _frame_pair_stats(
                    feature_frame.iloc[start_idx:end_idx],
                    compare_frame.iloc[start_idx:end_idx],
                )
                if int(stats["rows"]) < MIN_WINDOW_OBSERVATIONS:
                    continue
                correlations.append(
                    {
                        "rows": int(stats["rows"]),
                        "spearman": stats["spearman"],
                        "pearson": stats["pearson"],
                    }
                )
            aggregate = _aggregate_correlation_rows(correlations)
            aggregate["feature"] = compare_feature
            aggregate["redundancy"] = _redundancy_band(aggregate.get("median_spearman"))
            rows.append(aggregate)
        rows.sort(
            key=lambda row: abs(float(row.get("median_spearman") or 0.0)),
            reverse=True,
        )
        return rows[:MAX_COMPARE_FEATURES]

    def _feature_profile(
        self,
        *,
        feature_frame: pd.DataFrame,
        train_windows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        slices = [
            feature_frame.iloc[int(window["start_idx"]): int(window["end_idx"])]
            for window in train_windows
        ]
        if not slices:
            return {"available": False}
        combined = pd.concat(slices).sort_index()
        stacked = combined.stack(future_stack=True).dropna()
        if stacked.empty:
            return {"available": False}
        return {
            "available": True,
            "sample_count": int(stacked.shape[0]),
            "mean": _clean_float(stacked.mean()),
            "std": _clean_float(stacked.std()),
            "median_abs_value": _clean_float(stacked.abs().median()),
            "positive_fraction": _clean_float((stacked > 0.0).mean()),
            "negative_fraction": _clean_float((stacked < 0.0).mean()),
            "zero_fraction": _clean_float((stacked == 0.0).mean()),
        }

    def _cache_key(
        self,
        *,
        track: str,
        family: str,
        basis_groups: list[str],
        feature: str,
        compare_features: list[str],
        horizons: list[int],
    ) -> str:
        bundle_context = self.provider.current_bundle_context() or {}
        payload = json.dumps(
            {
                "track": track,
                "family": family,
                "basis_groups": list(basis_groups),
                "feature": feature,
                "compare_features": list(compare_features),
                "horizons": list(horizons),
                "bundle_hour": str(bundle_context.get("as_of") or "")[:13],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return short_hash(payload)

    def _gate_probe_cache_key(
        self,
        *,
        track: str,
        spec: SignalSpec,
        horizons: list[int],
    ) -> str:
        bundle_context = self.provider.current_bundle_context() or {}
        payload = json.dumps(
            {
                "track": track,
                "spec": spec.canonical_dict(),
                "horizons": list(horizons),
                "bundle_hour": str(bundle_context.get("as_of") or "")[:13],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return short_hash(payload)

    def _train_only_policy_summary(
        self,
        *,
        spec: SignalSpec,
        compiled: Any,
        prices: pd.DataFrame,
        train_windows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        prices_all = prices.sort_index()
        funding_all = None
        if compiled.funding_rates is not None:
            funding_all = compiled.funding_rates.reindex(prices_all.index).ffill().fillna(0.0)
        target_unshifted = compiled.target_positions.reindex(prices_all.index).ffill().fillna(0.0)
        target_all = target_unshifted.shift(1).fillna(0.0)
        rows: list[dict[str, Any]] = []
        for window in train_windows:
            start_idx = int(window["start_idx"])
            end_idx = int(window["end_idx"])
            prices_window = prices_all.iloc[start_idx:end_idx]
            if len(prices_window.index) < MIN_WINDOW_OBSERVATIONS:
                continue
            target_window = target_all.reindex(prices_window.index).ffill().fillna(0.0)
            funding_window = (
                funding_all.reindex(prices_window.index).ffill().fillna(0.0)
                if funding_all is not None
                else None
            )
            config = BacktestConfig(
                leverage=1.0,
                funding_rates=funding_window,
                rebalance_threshold=spec.risk.rebalance_threshold,
                enable_liquidation=True,
            )
            result = run_backtest(prices_window, target_window, config)
            window_spec = {
                "label": str(window["label"]),
                "role": "train_probe",
                "start_idx": start_idx,
                "end_idx": end_idx,
            }
            rows.append(
                self._evaluator._window_result_row(
                    result=result,
                    window_spec=window_spec,
                    leverage=1.0,
                    prices=prices_window,
                    used_for_selector=False,
                )
            )

        summary = self._evaluator._aggregate_window_summary("train", rows)
        active_fraction = _active_fraction_from_target(target_unshifted)
        position_flip_rate = _position_flip_rate_from_target(target_unshifted)
        return {
            "window_count": int(summary.get("train_window_count") or 0),
            "median_total_return": summary.get("train_total_return"),
            "median_sharpe": summary.get("train_sharpe"),
            "median_calmar": summary.get("train_calmar"),
            "worst_max_drawdown": summary.get("train_max_drawdown"),
            "profitable_window_pct": summary.get("train_profitable_window_pct"),
            "active_bar_fraction": active_fraction,
            "position_flip_rate": position_flip_rate,
        }

    def _gate_forward_return_profile(
        self,
        *,
        target_source: pd.DataFrame,
        gate_mask: pd.Series,
        train_windows: list[dict[str, Any]],
        horizons: list[int],
    ) -> list[dict[str, Any]]:
        if target_source.empty:
            return []
        rows: list[dict[str, Any]] = []
        for horizon in horizons:
            kept_means: list[float] = []
            blocked_means: list[float] = []
            kept_medians: list[float] = []
            blocked_medians: list[float] = []
            for window in train_windows:
                start_idx = int(window["start_idx"])
                end_idx = int(window["end_idx"])
                window_source = target_source.iloc[start_idx:end_idx]
                future = window_source.pct_change(horizon).shift(-horizon)
                window_mask = gate_mask.reindex(window_source.index).fillna(False)
                kept = _stack_frame(future.loc[window_mask])
                blocked = _stack_frame(future.loc[~window_mask])
                if not kept.empty:
                    kept_means.append(float(kept.mean()))
                    kept_medians.append(float(kept.median()))
                if not blocked.empty:
                    blocked_means.append(float(blocked.mean()))
                    blocked_medians.append(float(blocked.median()))
            rows.append(
                {
                    "horizon_bars": int(horizon),
                    "kept_mean_return": _median_value(kept_means),
                    "blocked_mean_return": _median_value(blocked_means),
                    "kept_median_return": _median_value(kept_medians),
                    "blocked_median_return": _median_value(blocked_medians),
                }
            )
        return rows

    def _is_valid_feature(
        self,
        feature: str,
        *,
        aliases: dict[str, str],
        raw_series: set[str],
    ) -> bool:
        if feature in aliases:
            return True
        return is_valid_feature_expression(
            feature,
            aliases=aliases,
            raw_series=raw_series,
        )

    def _gate_probe_warnings(
        self,
        *,
        gate_metadata: dict[str, Any],
        active_fraction: float | None,
        gated_summary: dict[str, Any],
        ungated_summary: dict[str, Any],
    ) -> list[str]:
        warnings: list[str] = []
        combined = _coerce_float(gate_metadata.get("combined_active_fraction"))
        if combined is not None and combined >= 0.98:
            warnings.append("regime_gates_are_effectively_always_open")
        elif combined is not None and combined <= 0.02:
            warnings.append("regime_gates_are_extremely_restrictive")
        if active_fraction is not None and active_fraction <= 0.02:
            warnings.append("gated_spec_is_near_flat")
        gated_return = _coerce_float(gated_summary.get("median_total_return"))
        ungated_return = _coerce_float(ungated_summary.get("median_total_return"))
        if (
            gated_return is not None
            and ungated_return is not None
            and abs(gated_return - ungated_return) <= 0.005
            and combined is not None
            and combined >= 0.95
        ):
            warnings.append("gates_do_not_change_train_outcomes")
        return warnings


def _sanitize_horizons(raw: Any) -> list[int]:
    values = list(raw or DEFAULT_HORIZONS)
    cleaned: list[int] = []
    seen: set[int] = set()
    for value in values:
        try:
            horizon = int(value)
        except (TypeError, ValueError):
            continue
        if horizon < 1 or horizon > 24 * 14 or horizon in seen:
            continue
        cleaned.append(horizon)
        seen.add(horizon)
    return cleaned or list(DEFAULT_HORIZONS)


def _gate_probe_delta(
    gated_summary: dict[str, Any],
    ungated_summary: dict[str, Any],
) -> dict[str, Any]:
    keys = [
        "median_total_return",
        "median_sharpe",
        "median_calmar",
        "worst_max_drawdown",
        "profitable_window_pct",
        "active_bar_fraction",
        "position_flip_rate",
    ]
    delta: dict[str, Any] = {}
    for key in keys:
        gated_value = _coerce_float(gated_summary.get(key))
        ungated_value = _coerce_float(ungated_summary.get(key))
        if gated_value is None or ungated_value is None:
            continue
        delta[key] = _clean_float(gated_value - ungated_value)
    return delta


def _llm_spec_from_log(log_path: str) -> dict[str, Any] | None:
    if not log_path:
        return None
    path = Path(log_path)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
    except Exception:
        return None
    parsed = payload.get("parsed_response") or {}
    if isinstance(parsed, dict):
        if isinstance(parsed.get("spec"), dict):
            return dict(parsed["spec"])
        if isinstance(parsed.get("specs"), list) and parsed["specs"]:
            first = parsed["specs"][0]
            if isinstance(first, dict):
                return dict(first)
        if "family" in parsed:
            return dict(parsed)
    return None


def _gate_expression(spec: Any) -> str:
    if isinstance(spec, str):
        return spec.strip()
    if isinstance(spec, dict):
        return str(spec.get("expression") or spec.get("feature") or "").strip()
    return ""


def _intent_vs_frozen_warnings(
    *,
    summary: dict[str, Any],
    compiled_regime_gates: dict[str, Any],
    proposed_gate_exprs: set[str],
    evaluated_gate_exprs: set[str],
) -> list[str]:
    warnings: list[str] = []
    if proposed_gate_exprs - evaluated_gate_exprs:
        warnings.append("some_proposed_regime_gates_were_dropped_before_evaluation")
    if bool(summary.get("policy_sweep_material_change")):
        warnings.append("policy_sweep_materially_changed_spec")
    active_fraction = _coerce_float(compiled_regime_gates.get("combined_active_fraction"))
    if active_fraction is not None and active_fraction >= 0.98:
        warnings.append("compiled_regime_gates_were_effectively_always_open")
    elif active_fraction is not None and active_fraction <= 0.02:
        warnings.append("compiled_regime_gates_were_extremely_restrictive")
    return warnings


def _artifact_payload(artifact_path: Any) -> dict[str, Any]:
    path_str = str(artifact_path or "").strip()
    if not path_str:
        return {}
    path = Path(path_str)
    if not path.exists():
        return {}
    try:
        return dict(json.loads(path.read_text()))
    except Exception:
        return {}


def _frontier_row_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "spec_hash": row.get("spec_hash"),
        "family": row.get("family"),
        "parent_hash": row.get("parent_hash"),
        "deterministic": bool(row.get("deterministic")),
        "passed": bool(row.get("passed")),
        "deployd": bool(row.get("deployd")),
        "hypothesis": row.get("hypothesis"),
        "features": list(row.get("features") or [])[:6],
        "basis_groups": list(row.get("basis_groups") or []),
        "aggregate_score": row.get("aggregate_score"),
        "active_bar_fraction": row.get("active_bar_fraction"),
        "gate_bottlenecks": list(row.get("gate_bottlenecks") or [])[:4],
        "summary": dict(row.get("summary") or {}),
    }


def _feature_frequency_summary(rows: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    counter: Counter[str] = Counter()
    for row in rows:
        counter.update(str(feature) for feature in row.get("features") or [])
    return [
        {"feature": feature, "count": count}
        for feature, count in counter.most_common(limit)
    ]


def _frontier_family_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("family") or "unknown"), []).append(row)

    payloads: list[dict[str, Any]] = []
    for family, family_rows in grouped.items():
        pre_values = [
            value
            for value in (
                row["summary"].get("pre_audit_canonical_total_return")
                for row in family_rows
            )
            if value is not None
        ]
        validation_values = [
            value
            for value in (
                row["summary"].get("validation_total_return")
                for row in family_rows
            )
            if value is not None
        ]
        active_values = [
            value
            for value in (row.get("active_bar_fraction") for row in family_rows)
            if value is not None
        ]
        best_row = max(
            family_rows,
            key=lambda row: (
                row["summary"].get("pre_audit_canonical_total_return")
                if row["summary"].get("pre_audit_canonical_total_return") is not None
                else float("-inf"),
                row["summary"].get("validation_total_return")
                if row["summary"].get("validation_total_return") is not None
                else float("-inf"),
            ),
        )
        positive_rows = [
            row
            for row in family_rows
            if (row["summary"].get("pre_audit_canonical_total_return") or 0.0) > 0.0
        ]
        payloads.append(
            {
                "family": family,
                "experiments_total": len(family_rows),
                "passed_total": sum(1 for row in family_rows if row.get("passed")),
                "positive_pre_audit_total": len(positive_rows),
                "deterministic_total": sum(1 for row in family_rows if row.get("deterministic")),
                "mean_pre_audit_canonical_total_return": _clean_float(pd.Series(pre_values, dtype=float).mean())
                if pre_values
                else None,
                "mean_validation_total_return": _clean_float(pd.Series(validation_values, dtype=float).mean())
                if validation_values
                else None,
                "mean_active_bar_fraction": _clean_float(pd.Series(active_values, dtype=float).mean())
                if active_values
                else None,
                "best_spec_hash": best_row.get("spec_hash"),
                "best_pre_audit_canonical_total_return": best_row["summary"].get(
                    "pre_audit_canonical_total_return"
                ),
                "top_feature_frequencies": _feature_frequency_summary(
                    positive_rows or sorted(
                        family_rows,
                        key=lambda row: (
                            row["summary"].get("pre_audit_canonical_total_return")
                            if row["summary"].get("pre_audit_canonical_total_return") is not None
                            else float("-inf")
                        ),
                        reverse=True,
                    )[:3],
                    limit=6,
                ),
            }
        )

    payloads.sort(
        key=lambda row: (
            row.get("best_pre_audit_canonical_total_return")
            if row.get("best_pre_audit_canonical_total_return") is not None
            else float("-inf"),
            row.get("mean_validation_total_return")
            if row.get("mean_validation_total_return") is not None
            else float("-inf"),
        ),
        reverse=True,
    )
    return payloads


def _frontier_warnings(
    *,
    frontier_rows: list[dict[str, Any]],
    positive_rows: list[dict[str, Any]],
    recent_rows: list[dict[str, Any]],
) -> list[str]:
    warnings: list[str] = []
    if not frontier_rows:
        return warnings

    if positive_rows:
        family_counts = Counter(str(row.get("family") or "unknown") for row in positive_rows)
        dominant_family, dominant_count = family_counts.most_common(1)[0]
        if dominant_count / len(positive_rows) >= 0.65 and len(positive_rows) >= 3:
            warnings.append(
                f"positive_frontier_is_concentrated_in_{dominant_family}"
            )
        if all(bool(row.get("deterministic")) for row in positive_rows[: min(3, len(positive_rows))]):
            warnings.append("positive_frontier_is_still_led_by_deterministic_anchors")

    recent_pre = [
        row["summary"].get("pre_audit_canonical_total_return")
        for row in recent_rows
        if row["summary"].get("pre_audit_canonical_total_return") is not None
    ]
    if len(recent_pre) >= 4 and sum(1 for value in recent_pre[:5] if value <= 0.0) >= 4:
        warnings.append("recent_runs_are_mostly_non_positive_pre_audit")

    recent_low_activity = [
        row
        for row in recent_rows
        if row.get("active_bar_fraction") is not None and float(row["active_bar_fraction"]) <= 0.02
    ]
    if len(recent_low_activity) >= max(2, min(4, len(recent_rows))):
        warnings.append("recent_runs_are_collapsing_into_near_flat_activity")

    return warnings


def _active_fraction_from_target(target: pd.DataFrame) -> float | None:
    if target.empty:
        return None
    active = target.abs().sum(axis=1).gt(0.0)
    return _clean_float(active.mean())


def _position_flip_rate_from_target(target: pd.DataFrame) -> float | None:
    if target.empty or len(target.index) < 2:
        return None
    signature = target.round(8).astype(str).agg("|".join, axis=1)
    flips = signature.iloc[1:].ne(signature.shift(1).iloc[1:])
    return _clean_float(flips.mean())


def _stack_frame(frame: pd.DataFrame | pd.Series) -> pd.Series:
    if isinstance(frame, pd.Series):
        return pd.to_numeric(frame, errors="coerce").dropna()
    if isinstance(frame, pd.DataFrame):
        return frame.apply(pd.to_numeric, errors="coerce").stack(future_stack=True).dropna()
    return pd.Series(dtype=float)


def _coerce_float(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not pd.notna(numeric):
        return None
    return numeric


def _frame_pair_stats(feature_frame: pd.DataFrame, target_frame: pd.DataFrame) -> dict[str, Any]:
    aligned = pd.concat(
        [
            feature_frame.stack(future_stack=True).rename("feature"),
            target_frame.stack(future_stack=True).rename("target"),
        ],
        axis=1,
    ).dropna()
    if aligned.empty:
        return {
            "rows": 0,
            "spearman": None,
            "pearson": None,
            "top_bottom_spread": None,
            "bucket_monotonicity": None,
        }

    bucket_count = min(5, int(aligned["feature"].nunique()))
    top_bottom_spread = None
    bucket_monotonicity = None
    if bucket_count >= 2:
        quantiles = pd.qcut(
            aligned["feature"],
            q=bucket_count,
            labels=False,
            duplicates="drop",
        )
        if quantiles.nunique() >= 2:
            bucket_means = aligned.groupby(quantiles)["target"].mean().sort_index()
            if len(bucket_means) >= 2:
                top_bottom_spread = _clean_float(bucket_means.iloc[-1] - bucket_means.iloc[0])
                bucket_monotonicity = _clean_float(
                    _spearman_corr(
                        pd.Series(bucket_means.values, dtype=float),
                        pd.Series(range(len(bucket_means)), dtype=float),
                    )
                )

    return {
        "rows": int(aligned.shape[0]),
        "spearman": _clean_float(_spearman_corr(aligned["feature"], aligned["target"])),
        "pearson": _clean_float(_pearson_corr(aligned["feature"], aligned["target"])),
        "top_bottom_spread": top_bottom_spread,
        "bucket_monotonicity": bucket_monotonicity,
    }


def _aggregate_predictive_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "available": False,
            "window_count": 0,
            "total_rows": 0,
            "median_spearman": None,
            "median_pearson": None,
            "positive_spearman_window_fraction": None,
            "median_top_bottom_spread": None,
            "median_bucket_monotonicity": None,
            "median_abs_feature_abs_return_spearman": None,
        }
    spearman_values = [float(row["spearman"]) for row in rows if row.get("spearman") is not None]
    return {
        "available": True,
        "window_count": len(rows),
        "total_rows": int(sum(int(row.get("rows") or 0) for row in rows)),
        "median_spearman": _median_value(row.get("spearman") for row in rows),
        "median_pearson": _median_value(row.get("pearson") for row in rows),
        "positive_spearman_window_fraction": _clean_float(
            sum(1 for value in spearman_values if value > 0.0) / len(spearman_values)
        )
        if spearman_values
        else None,
        "median_top_bottom_spread": _median_value(
            row.get("top_bottom_spread") for row in rows
        ),
        "median_bucket_monotonicity": _median_value(
            row.get("bucket_monotonicity") for row in rows
        ),
        "median_abs_feature_abs_return_spearman": _median_value(
            row.get("abs_feature_abs_return_spearman") for row in rows
        ),
    }


def _aggregate_correlation_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "available": False,
            "window_count": 0,
            "total_rows": 0,
            "median_spearman": None,
            "median_pearson": None,
        }
    return {
        "available": True,
        "window_count": len(rows),
        "total_rows": int(sum(int(row.get("rows") or 0) for row in rows)),
        "median_spearman": _median_value(row.get("spearman") for row in rows),
        "median_pearson": _median_value(row.get("pearson") for row in rows),
    }


def _median_value(values: Any) -> float | None:
    series = pd.Series([value for value in values if value is not None], dtype=float)
    if series.empty:
        return None
    return _clean_float(series.median())


def _redundancy_band(value: float | None) -> str:
    if value is None:
        return "unknown"
    magnitude = abs(float(value))
    if magnitude >= 0.85:
        return "high"
    if magnitude >= 0.55:
        return "moderate"
    return "low"


def _spearman_corr(left: pd.Series, right: pd.Series) -> float | None:
    if left.empty or right.empty:
        return None
    left_rank = left.rank(method="average")
    right_rank = right.rank(method="average")
    return _pearson_corr(left_rank, right_rank)


def _pearson_corr(left: pd.Series, right: pd.Series) -> float | None:
    aligned = pd.concat(
        [
            pd.to_numeric(left, errors="coerce").rename("left"),
            pd.to_numeric(right, errors="coerce").rename("right"),
        ],
        axis=1,
    ).dropna()
    if len(aligned.index) < 2:
        return None
    if aligned["left"].nunique() < 2 or aligned["right"].nunique() < 2:
        return None
    value = aligned["left"].corr(aligned["right"], method="pearson")
    return None if value is None or pd.isna(value) else float(value)


def _clean_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result:
        return None
    return result



def _sanitize_limit(raw: Any, *, default: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(1, min(50, value))


from siglab.evaluation.analysis_utils import pre_audit_trade_episodes as _pre_audit_trade_episodes


def _episode_bucket(bars: Any) -> str | None:
    try:
        count = int(bars)
    except (TypeError, ValueError):
        return None
    if count <= 0:
        return None
    if count <= 6:
        return "bars_1_6"
    if count <= 24:
        return "bars_7_24"
    if count <= 72:
        return "bars_25_72"
    return "bars_73_plus"


def _filter_trade_episodes(
    trade_episodes: list[dict[str, Any]],
    *,
    filters: dict[str, str | None],
) -> list[dict[str, Any]]:
    direction = filters.get("direction")
    pnl_sign = filters.get("pnl_sign")
    holding_bucket = filters.get("holding_bucket")
    regime_dimension = filters.get("regime_dimension")
    regime_label = filters.get("regime_label")
    regime_key = f"{regime_dimension}_label" if regime_dimension else None

    filtered: list[dict[str, Any]] = []
    for episode in trade_episodes:
        if direction and str(episode.get("direction") or "") != direction:
            continue
        total_return = episode.get("total_return")
        if pnl_sign == "positive" and not (total_return is not None and float(total_return) > 0.0):
            continue
        if pnl_sign == "negative" and not (total_return is not None and float(total_return) < 0.0):
            continue
        if holding_bucket and _episode_bucket(episode.get("bars")) != holding_bucket:
            continue
        if regime_key and regime_label:
            entry_regime = dict(episode.get("entry_regime") or {})
            if str(entry_regime.get(regime_key) or "") != regime_label:
                continue
        filtered.append(episode)
    filtered.sort(
        key=lambda episode: str(episode.get("start_timestamp") or ""),
        reverse=True,
    )
    return filtered


def _episode_summary(trade_episodes: list[dict[str, Any]]) -> dict[str, Any]:
    if not trade_episodes:
        return {
            "trade_count": 0,
            "win_rate": None,
            "avg_return": None,
            "median_return": None,
            "median_hold_bars": None,
            "direction_counts": {},
            "entry_regime_counts": {},
        }
    returns = pd.Series(
        [float(episode["total_return"]) for episode in trade_episodes if episode.get("total_return") is not None],
        dtype=float,
    )
    bars = pd.Series(
        [float(episode["bars"]) for episode in trade_episodes if episode.get("bars") is not None],
        dtype=float,
    )
    direction_counts = Counter(str(episode.get("direction") or "") for episode in trade_episodes)
    regime_counts: dict[str, Counter[str]] = {
        "market_trend": Counter(),
        "pair_volatility": Counter(),
        "funding_dispersion": Counter(),
        "pair_correlation": Counter(),
        "pair_direction": Counter(),
    }
    for episode in trade_episodes:
        entry_regime = dict(episode.get("entry_regime") or {})
        for dimension in list(regime_counts):
            label = str(entry_regime.get(f"{dimension}_label") or "").strip()
            if label:
                regime_counts[dimension][label] += 1
    return {
        "trade_count": len(trade_episodes),
        "win_rate": _clean_float((returns > 0.0).mean()) if not returns.empty else None,
        "avg_return": _clean_float(returns.mean()) if not returns.empty else None,
        "median_return": _clean_float(returns.median()) if not returns.empty else None,
        "median_hold_bars": _clean_float(bars.median()) if not bars.empty else None,
        "direction_counts": dict(direction_counts),
        "entry_regime_counts": {
            dimension: [
                {"label": label, "count": count}
                for label, count in counter.most_common(3)
            ]
            for dimension, counter in regime_counts.items()
            if counter
        },
    }


def _compact_trade_episode(episode: dict[str, Any]) -> dict[str, Any]:
    return {
        "start_timestamp": episode.get("start_timestamp"),
        "end_timestamp": episode.get("end_timestamp"),
        "direction": episode.get("direction"),
        "bars": _clean_float(episode.get("bars")),
        "holding_bucket": _episode_bucket(episode.get("bars")),
        "total_return": _clean_float(episode.get("total_return")),
        "entry_regime": dict(episode.get("entry_regime") or {}),
        "exit_regime": dict(episode.get("exit_regime") or {}),
    }


def _compact_summary(summary: dict[str, Any]) -> dict[str, Any]:
    cleaned = _strip_audit_fields(dict(summary or {}))
    keys = [
        "aggregate_score",
        "median_total_return",
        "median_sharpe",
        "validation_total_return",
        "validation_sharpe",
        "pre_audit_canonical_total_return",
        "pre_audit_canonical_max_drawdown",
        "policy_sweep_applied",
        "policy_sweep_best_train_score",
        "policy_entry_abs_score",
        "policy_exit_abs_score",
        "policy_flip_abs_score",
        "policy_max_holding_bars",
        "policy_cooldown_bars",
        "passed",
        "gate_reasons",
    ]
    return {
        key: cleaned.get(key)
        for key in keys
        if key in cleaned
    }


def _compact_spec_params(params: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "trade_style",
        "gross_target",
        "max_gross_target",
        "signal_leverage_scale",
        "entry_abs_score",
        "exit_abs_score",
        "flip_abs_score",
        "max_holding_bars",
        "cooldown_bars",
        "min_abs_score",
    ]
    return {
        key: params.get(key)
        for key in keys
        if key in params
    }


def _spec_ancestry_summary(
    *,
    ancestry: LineageStore,
    track: str,
    spec_hash: str,
    run_session_id: str | None = None,
) -> dict[str, Any]:
    rows = ancestry.dashboard_rows(track=track, run_session_id=run_session_id)
    by_hash = {
        str(row.get("spec_hash") or ""): row
        for row in rows
    }
    current = by_hash.get(spec_hash)
    if current is None:
        return {}
    parent_hash = str(current.get("parent_hash") or "").strip()
    parent_row = by_hash.get(parent_hash) if parent_hash else None
    children = [
        row
        for row in rows
        if str(row.get("parent_hash") or "") == spec_hash
    ]
    siblings = [
        row
        for row in rows
        if parent_hash
        and str(row.get("parent_hash") or "") == parent_hash
        and str(row.get("spec_hash") or "") != spec_hash
    ]

    def _row_payload(row: dict[str, Any]) -> dict[str, Any]:
        spec = dict(row.get("spec") or {})
        summary = dict(row.get("summary") or {})
        return {
            "spec_hash": row.get("spec_hash"),
            "family": row.get("family"),
            "aggregate_score": row.get("aggregate_score"),
            "hypothesis": spec.get("hypothesis"),
            "features": list(spec.get("features") or [])[:6],
            "basis_groups": list((spec.get("universe") or {}).get("basis_groups") or []),
            "summary": _compact_summary(summary),
        }

    children.sort(key=lambda row: float(row.get("aggregate_score") or -1e9), reverse=True)
    siblings.sort(key=lambda row: float(row.get("aggregate_score") or -1e9), reverse=True)
    return {
        "parent": _row_payload(parent_row) if parent_row is not None else None,
        "children": [_row_payload(row) for row in children[:6]],
        "siblings": [_row_payload(row) for row in siblings[:6]],
    }


def _strip_audit_fields(payload: Any) -> Any:
    if isinstance(payload, dict):
        cleaned: dict[str, Any] = {}
        for key, value in payload.items():
            key_str = str(key)
            if key_str.startswith("audit_"):
                continue
            cleaned[key_str] = _strip_audit_fields(value)
        return cleaned
    if isinstance(payload, list):
        return [_strip_audit_fields(item) for item in payload]
    return payload


