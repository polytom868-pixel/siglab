from __future__ import annotations

import copy
import json
import random
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from siglab.feature_dsl import is_valid_feature_expression, load_feature_spec
from siglab.families import (
    family_execution_profile,
    family_prompt_module,
    load_family_spec,
    load_track_family_specs,
)
from siglab.llm import ClaudeClient, ClaudeTool
from siglab.schemas import SignalSpec
from siglab.search.select import rank_deterministic_specs
from siglab.config import SiglabConfig
from siglab.track_registry import canonical_track_name, storage_track_name

PAIR_UNIVERSES: list[list[str]] = [
    ["ETH", "BTC"],
    ["SOL", "ETH"],
    ["BTC", "SOL"],
    ["ETH", "HYPE"],
]
CROSS_SECTIONAL_UNIVERSES: list[list[str]] = [
    ["BTC", "ETH", "SOL", "HYPE"],
    ["BTC", "ETH", "BNB", "XRP"],
    ["ETH", "SOL", "DOGE", "SUI"],
]
TREND_SIGNALS_LOOKBACK_DAYS = 365
UNLEVERED_PAIR_FAMILY = "perp_pair_trade_unlevered"
LEVERED_PAIR_FAMILY = "perp_pair_trade_levered"
BASKET_NEUTRAL_UNLEVERED_FAMILY = "perp_basket_neutral_unlevered"
BASKET_NEUTRAL_LEVERED_FAMILY = "perp_basket_neutral_levered"
MULTI_ASSET_CARRY_FAMILY = "perp_multi_asset_carry"
PAIR_TRADE_FAMILIES = {
    UNLEVERED_PAIR_FAMILY,
    LEVERED_PAIR_FAMILY,
}
BASKET_NEUTRAL_FAMILIES = {
    UNLEVERED_PAIR_FAMILY,
    LEVERED_PAIR_FAMILY,
    BASKET_NEUTRAL_UNLEVERED_FAMILY,
    BASKET_NEUTRAL_LEVERED_FAMILY,
}
PAIR_MEAN_REVERSION_FEATURES = [
    "neg(div(sub(price_ratio, rolling_mean(price_ratio,60)), clip(rolling_std(price_ratio,60),0.0001,10.0)))",
    "neg(sub(pct_change(asset_1_price,24), pct_change(asset_2_price,24)))",
    "neg(div(sub(funding_spread, rolling_mean(funding_spread,72)), clip(rolling_std(funding_spread,72),0.000001,1.0)))",
    "neg(div(rolling_mean(funding_spread,72), clip(pair_realized_vol_168h,0.01,10.0)))",
]
PAIR_QUALITY_MOMENTUM_FEATURES = [
    "sub(pct_change(asset_1_price,24), pct_change(asset_2_price,24))",
    "div(sub(pct_change(asset_1_price,24), pct_change(asset_2_price,24)), clip(pair_realized_vol_168h,0.01,10.0))",
    "sub(rolling_mean(asset_2_funding,72), rolling_mean(asset_1_funding,72))",
    "mul(sub(pct_change(asset_1_price,24), pct_change(asset_2_price,24)), sub(rolling_mean(asset_2_funding,72), rolling_mean(asset_1_funding,72)))",
]
PAIR_COMPRESSION_REVERSION_FEATURES = [
    "neg(div(sub(price_ratio, rolling_mean(price_ratio,20)), clip(rolling_std(price_ratio,20),0.0001,10.0)))",
    "div(sub(rolling_mean(asset_2_funding,72), rolling_mean(asset_1_funding,72)), clip(pair_realized_vol_168h,0.01,10.0))",
    "neg(pair_bollinger_width_20)",
    "neg(pair_realized_vol_168h)",
]
PAIR_DYNAMIC_RESIDUAL_FEATURES = [
    "neg(pair_kalman_residual_z_72h)",
    "neg(pair_kalman_residual_momentum_24h)",
    "pair_beta_drift_z_168h",
    "pair_kalman_beta_stability_72h",
]
PAIR_MEAN_REVERSION_SPEED_FEATURES = [
    "neg(pair_residual_autocorr_24h)",
    "neg(pair_residual_half_life_168h)",
    "neg(pair_residual_hurst_168h)",
    "pair_spread_vol_z_168h",
]
PAIR_CARRY_REGIME_FEATURES = [
    "pair_carry_to_vol_spread",
    "pair_carry_to_vol_z_168h",
    "funding_spread_dispersion_z_168h",
    "pair_corr_z_168h",
]


def crossover_specs(a_dict: dict, b_dict: dict) -> dict:
    """Uniform crossover on discrete fields, average on numeric params."""
    child = {}
    for key in set(a_dict) | set(b_dict):
        va, vb = a_dict.get(key), b_dict.get(key)
        if key == "features":
            pool = list(set(va or []) | set(vb or []))
            child[key] = random.sample(pool, min(len(pool), max(len(va or []), 1)))
        elif key == "params" and isinstance(va, dict) and isinstance(vb, dict):
            merged = {}
            for pk in set(va) | set(vb):
                pa, pb = va.get(pk), vb.get(pk)
                if isinstance(pa, (int, float)) and isinstance(pb, (int, float)):
                    merged[pk] = (pa + pb) / 2.0
                else:
                    merged[pk] = random.choice([pa, pb])
            child[key] = merged
        elif key == "family":
            child[key] = random.choice([va, vb])
        else:
            child[key] = random.choice([va, vb])
    return child


def _ordered_pair_universes(
    current_pair: list[str] | None = None,
    *,
    prefer_alternates: bool,
) -> list[list[str]]:
    current = tuple(current_pair or [])
    pairs = [list(pair) for pair in PAIR_UNIVERSES]
    if len(current) != 2:
        return pairs
    alternate_pairs = [pair for pair in pairs if tuple(pair) != current]
    if prefer_alternates:
        return alternate_pairs + [list(current)]
    return [list(current)] + alternate_pairs


def _trim_symbols(symbols: list[str] | None, *, max_symbols: int) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for symbol in list(symbols or []):
        normalized = str(symbol or "").strip().upper()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        cleaned.append(normalized)
        if len(cleaned) >= max_symbols:
            break
    return cleaned


def _ordered_cross_sectional_universes(
    current_symbols: list[str] | None = None,
    *,
    prefer_alternates: bool,
    max_symbols: int,
) -> list[list[str]]:
    current = _trim_symbols(current_symbols, max_symbols=max_symbols)
    universes: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()

    def _append(symbols: list[str] | None) -> None:
        trimmed = _trim_symbols(symbols, max_symbols=max_symbols)
        key = tuple(trimmed)
        if len(trimmed) < 2 or key in seen:
            return
        seen.add(key)
        universes.append(trimmed)

    if prefer_alternates:
        for basket in CROSS_SECTIONAL_UNIVERSES:
            if tuple(_trim_symbols(basket, max_symbols=max_symbols)) != tuple(current):
                _append(basket)
        _append(current)
        return universes

    _append(current)
    for basket in CROSS_SECTIONAL_UNIVERSES:
        _append(basket)
    return universes


class SpecMutator:
    def __init__(self, settings: SiglabConfig, claude: ClaudeClient) -> None:
        self.settings = settings
        self.claude = claude
        self.last_llm_trace: dict[str, Any] | None = None
        self.last_llm_log_path: str | None = None

    def _family_spec(self, track: str, family: str) -> dict[str, Any]:
        return load_family_spec(self.settings.root_dir, track, family)

    def _family_execution_profile(self, track: str, family: str) -> str | None:
        return family_execution_profile(self._family_spec(track, family))

    def _family_prompt_module(self, track: str, family: str) -> str | None:
        return family_prompt_module(self._family_spec(track, family))

    def load_seed_specs(
        self,
        track: str,
        family: str | list[str] | None = None,
        *,
        include_historical: bool | None = None,
    ) -> list[SignalSpec]:
        payload = yaml.safe_load(
            (self.settings.root_dir / "mutable" / "graph_lab.yaml").read_text()
        )
        canonical_track = canonical_track_name(track) or track
        family_scope = _family_scope(family)
        rows = [
            SignalSpec.from_dict(row)
            for row in payload.get("specs") or []
            if canonical_track_name(str(row.get("track"))) == canonical_track
            and (family_scope is None or str(row.get("family")) in family_scope)
        ]
        if include_historical is None:
            include_historical = bool(getattr(self.settings, "use_historical_seeds", False))
        if include_historical:
            rows = self._merge_historical_seed_specs(
                rows=rows,
                track=canonical_track,
                family_scope=family_scope,
            )
        if not rows:
            family_suffix = f" family={family}" if family else ""
            raise ValueError(f"No seed specs defined for {track}{family_suffix}")
        return rows

    def _merge_historical_seed_specs(
        self,
        *,
        rows: list[SignalSpec],
        track: str,
        family_scope: set[str] | None,
    ) -> list[SignalSpec]:
        merged: dict[str, SignalSpec] = {spec.family: spec for spec in rows}
        ordered_families: list[str] = []
        for spec in self._historical_seed_specs(track=track, family_scope=family_scope):
            merged[spec.family] = spec
            if spec.family not in ordered_families:
                ordered_families.append(spec.family)
        for spec in rows:
            if spec.family not in ordered_families:
                ordered_families.append(spec.family)
        return [merged[family_name] for family_name in ordered_families if family_name in merged]

    def _historical_seed_specs(
        self,
        *,
        track: str,
        family_scope: set[str] | None,
    ) -> list[SignalSpec]:
        storage_track = storage_track_name(track)
        if storage_track is None:
            return []
        artifact_root = Path(getattr(self.settings, "artifact_dir", self.settings.root_dir / "runs"))
        search_dirs = [artifact_root / storage_track]
        search_dirs.extend(
            sorted(
                (
                    backup_dir / "runs" / storage_track
                    for backup_dir in (self.settings.root_dir / "backups").glob("relaunch_*")
                ),
                reverse=True,
            )
        )
        best_by_family: dict[str, tuple[float, float, SignalSpec]] = {}
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
                family_name = str(spec_payload.get("family") or "")
                if not family_name:
                    continue
                if canonical_track_name(str(spec_payload.get("track"))) != track:
                    continue
                if family_scope is not None and family_name not in family_scope:
                    continue
                if not self._historical_seedworthy(summary):
                    continue
                try:
                    spec = SignalSpec.from_dict(spec_payload)
                except Exception:  # noqa: BLE001
                    continue
                quality = self._historical_seed_quality(summary)
                freshness = path.stat().st_mtime
                current = best_by_family.get(family_name)
                if current is None or (quality, freshness) > (current[0], current[1]):
                    best_by_family[family_name] = (quality, freshness, spec)
        ranked = sorted(
            best_by_family.values(),
            key=lambda item: (item[0], item[1]),
            reverse=True,
        )
        return [spec for _quality, _freshness, spec in ranked]

    def _historical_seedworthy(self, summary: dict[str, Any]) -> bool:
        pre_audit_total_return = _safe_float(summary.get("pre_audit_canonical_total_return"))
        median_total_return = _safe_float(summary.get("median_total_return"))
        validation_total_return = _safe_float(summary.get("validation_total_return"))
        pre_audit_max_drawdown = _safe_float(summary.get("pre_audit_canonical_max_drawdown"))
        if pre_audit_total_return is not None and pre_audit_total_return > 0.0:
            return True
        if (
            median_total_return is not None
            and median_total_return > 0.0
            and (pre_audit_total_return is None or pre_audit_total_return > -0.05)
            and (pre_audit_max_drawdown is None or pre_audit_max_drawdown > -0.15)
        ):
            return True
        if (
            validation_total_return is not None
            and validation_total_return > 0.0
            and (pre_audit_total_return is None or pre_audit_total_return > -0.03)
        ):
            return True
        return False

    def _historical_seed_quality(self, summary: dict[str, Any]) -> float:
        quality = 0.0
        pre_audit_total_return = _safe_float(summary.get("pre_audit_canonical_total_return"))
        if pre_audit_total_return is not None:
            quality += pre_audit_total_return * 12.0
        validation_total_return = _safe_float(summary.get("validation_total_return"))
        if validation_total_return is not None:
            quality += validation_total_return * 5.0
        median_total_return = _safe_float(summary.get("median_total_return"))
        if median_total_return is not None:
            quality += median_total_return * 3.0
        median_sharpe = _safe_float(summary.get("median_sharpe"))
        if median_sharpe is not None:
            quality += median_sharpe * 0.05
        pre_audit_max_drawdown = _safe_float(summary.get("pre_audit_canonical_max_drawdown"))
        if pre_audit_max_drawdown is not None:
            quality += pre_audit_max_drawdown * 1.5
        active_bar_fraction = _safe_float(summary.get("active_bar_fraction"))
        if active_bar_fraction is not None and active_bar_fraction < 0.05:
            quality -= 0.5
        return quality

    async def propose(
        self,
        *,
        track: str,
        parent: SignalSpec,
        research_summary: dict[str, Any],
        recent_results: list[dict[str, Any]],
        memory_packet: dict[str, Any] | None,
        population_size: int,
        skip_llm: bool,
        family: str | list[str] | None = None,
        exclude_hashes: set[str] | None = None,
        llm_tools: list[ClaudeTool] | None = None,
        deterministic_recent_rows: list[dict[str, Any]] | None = None,
        deterministic_seed_specs: list[SignalSpec] | None = None,
    ) -> list[SignalSpec]:
        self.last_llm_trace = None
        self.last_llm_log_path = None
        allowed_families = self._allowed_families(track, family=family)
        allowed_features_by_family = self._allowed_features_by_family(track, family=family)
        allowed_features = sorted(
            {
                feature
                for features in allowed_features_by_family.values()
                for feature in features
            }
        )
        feature_guide = load_feature_spec(self.settings.root_dir, track=track)
        family_defaults = self._family_defaults(track, family=family)
        exclude_hashes = exclude_hashes or set()

        proposals: list[SignalSpec] = []
        if not skip_llm and self.claude.is_configured:
            proposals = await self._llm_proposals(
                track=track,
                parent=parent,
                research_summary=research_summary,
                recent_results=recent_results,
                memory_packet=memory_packet or {},
                allowed_families=allowed_families,
                allowed_features=allowed_features,
                allowed_features_by_family=allowed_features_by_family,
                family_defaults=family_defaults,
                raw_series_by_family=feature_guide.get("raw_series_by_family") or {},
                formula_operators=feature_guide.get("operators") or [],
                llm_tools=llm_tools or [],
            )
        else:
            proposals = self._deterministic_variants(track, parent, family=family)

        validated: list[SignalSpec] = []
        seen: set[str] = set()
        for spec in proposals:
            fixed = self._validate_spec(
                spec=spec,
                track=track,
                allowed_families=allowed_families,
                allowed_features_by_family=allowed_features_by_family,
                family_defaults=family_defaults,
            )
            spec_hash = fixed.strategy_hash()
            if spec_hash in seen or spec_hash in exclude_hashes:
                continue
            validated.append(fixed)
            seen.add(spec_hash)
            if not skip_llm and len(validated) >= population_size:
                break
        if skip_llm:
            ranked = rank_deterministic_specs(
                specs=validated,
                parent=parent,
                recent_rows=list(deterministic_recent_rows or []),
                seed_specs=list(
                    deterministic_seed_specs
                    or self.load_seed_specs(track, family=family)
                ),
                population_size=population_size,
            )
            return ranked[:population_size]
        return validated[:population_size]

    async def _llm_proposals(
        self,
        *,
        track: str,
        parent: SignalSpec,
        research_summary: dict[str, Any],
        recent_results: list[dict[str, Any]],
        memory_packet: dict[str, Any],
        allowed_families: list[str],
        allowed_features: list[str],
        allowed_features_by_family: dict[str, list[str]],
        family_defaults: dict[str, Any],
        raw_series_by_family: dict[str, list[str]],
        formula_operators: list[str],
        llm_tools: list[ClaudeTool],
    ) -> list[SignalSpec]:
        system_prompt = (
            "Design bounded strategy graphs for a backtest engine. "
            "Before forming a new hypothesis, thoroughly understand what has gone well, what has gone badly, "
            "which regimes or gates caused that outcome, and whether recent mutations actually improved on their parents. "
            "Use the supplied market context, recent outcomes, last-five run history, broader outstanding runs, and external web research when present. "
            "Decide explicitly whether you should refine the current thesis, branch to a materially different archetype inside the same family, switch families, "
            "or do more tool-based investigation because the evidence is contradictory. "
            "Do not switch families just to appear novel; if one family is the only area showing positive pre-audit behavior, lean into it and test orthogonal variants there first. "
            "If the provided tools would help verify a protocol mechanic, strategy idea, or current market convention, call them before finalizing the JSON. "
            "Return a compact JSON object only."
        )
        compact_research = self._compact_research_summary(research_summary)
        compact_memory = self._compact_memory_packet(memory_packet)
        formula_examples = self._formula_examples(track, parent.family)
        feature_idea_examples = self._feature_idea_examples(track, parent.family)
        family_modules = {
            family_name: {
                "execution_profile": self._family_execution_profile(track, family_name),
                "prompt_module": self._family_prompt_module(track, family_name),
            }
            for family_name in allowed_families
        }
        user_payload = {
            "task": "Return exactly 1 spec",
            "track": track,
            "allowed_families": allowed_families,
            "family_modules": family_modules,
            "allowed_features": allowed_features,
            "allowed_features_by_family": allowed_features_by_family,
            "family_defaults": family_defaults,
            "raw_series_by_family": raw_series_by_family,
            "formula_operators": formula_operators,
            "formula_examples": formula_examples,
            "feature_idea_examples": feature_idea_examples,
            "preferred_pair_universes": PAIR_UNIVERSES if track == "trend_signals" else [],
            "preferred_cross_sectional_universes": (
                CROSS_SECTIONAL_UNIVERSES if track == "trend_signals" else []
            ),
            "pair_trade_archetypes": (
                ["reversion", "pullback", "continuation", "breakout", "hybrid"]
                if track == "trend_signals"
                else []
            ),
            "evaluation_policy": {
                "selector": (
                    "SigLab scores specs on rolling validation chunks across the pre-audit "
                    "history when enough realized data exists, and falls back to simpler "
                    "validation or in-sample scoring when the realized history is shorter."
                ),
                "history": (
                    "Do not assume the full requested lookback is always available. Favor "
                    "specs that remain stable if the realized history is materially shorter, "
                    "for example around 200 days instead of the requested maximum."
                ),
                "audit": (
                    "The final audit block is reserved as untouched out-of-sample evaluation and "
                    "is never used by the selector. Do not optimize specifically for audit behavior."
                ),
            },
            "parent": {
                "family": parent.family,
                "features": parent.features,
                "universe": parent.canonical_dict()["universe"],
                "risk": parent.canonical_dict()["risk"],
                "regime_gates": parent.regime_gates,
                "params": parent.params,
            },
            "research_summary": compact_research,
            "memory_packet": compact_memory,
            "recent_results": [
                {
                    "spec_hash": row["spec_hash"],
                    "family": row["family"],
                    "score": round(float(row["aggregate_score"]), 4),
                    "passed": bool(row["passed"]),
                    "summary": {
                        "median_total_return": row["summary"].get("median_total_return"),
                        "validation_total_return": row["summary"].get("validation_total_return"),
                        "pre_audit_canonical_total_return": row["summary"].get(
                            "pre_audit_canonical_total_return"
                        ),
                    },
                    "gate_reasons": row["summary"].get("gate_reasons", []),
                }
                for row in recent_results[:5]
            ],
            "response_shape": {
                "specs": [
                    {
                        "track": track,
                        "family": "allowed family",
                        "hypothesis": "short string",
                        "neutrality_basis": "none|underlying|usd",
                        "features": ["allowed_feature"],
                        "universe": {},
                        "risk": {},
                        "regime_gates": {
                            "entry": [
                                {
                                    "expression": "allowed_feature_or_boolean_formula",
                                    "min": "optional number",
                                    "max": "optional number",
                                }
                            ],
                            "exit_on_break": True,
                        },
                        "params": {
                            "trade_style": "reversion|pullback|continuation|breakout|hybrid"
                        },
                    }
                ]
            },
            "rules": [
                "Keep fields minimal and valid",
                "Use external_research reports to vary the family, features, long short flags, and hedge policy when the evidence supports it",
                "Use the memory packet to avoid repeating explored failures and to reuse strong families, assets, and feature neighborhoods",
                "Study last_five_runs, outstanding_runs, recent_results, nearest_failures, nearest_winners, and validation_leaders before proposing anything; understand the current search frontier and recent failures before hypothesizing a fix",
                "If last_five_runs shows degrading returns, repeated restrictive gating, or a narrow repeated motif, do not keep polishing the same parent thesis unless you can explain the specific missing edge",
                "Use the available search and fetch tools when you need fresher or more specific evidence than the embedded summary provides",
                "Use only allowed families and either feature aliases or formulas built from the listed operators and raw series",
                "Use only alias features that are valid for the selected family",
                "You may invent novel indicators by composing the listed operators over the raw series, following the formula examples and feature idea examples",
                "Use probe_feature_forward_stats when testing a new directional-perps feature would help; it evaluates train-only forward-return predictiveness across several horizons and reports redundancy versus parent predictors",
                "Use inspect_pre_audit_spec when you need to inspect a stored spec's filtered pre-audit trade episodes, pre-audit diagnostics, or parent-child ancestry before proposing a mutation",
                "Use summarize_experiment_frontier when you need to know which families, feature neighborhoods, and recent runs are actually producing positive pre-audit results across the track; use it before abandoning a family with positive anchors or before claiming the frontier has shifted",
                "Use probe_spec_gate_impact when adding, tightening, or questioning regime_gates; confirm on train-only data that the gates materially filter bars and improve train behavior instead of staying open almost all the time",
                "Use compare_intended_vs_frozen_spec on recent misses when you need to know whether the idea was wrong or whether normalization and the sweep materially changed the thing that actually ran",
                "Use the supplied pair_calibration percentiles to calibrate thresholds and gates; avoid arbitrary extreme cutoffs that would almost never fire",
                "Use the failure_pattern_summary, behavior_pattern_summary, regime_pattern_summary, drawdown_pattern_summary, gate_pattern_summary, and equity_pattern_summary to diagnose whether recent failures came from no-trade behavior, overtrading, bad regime fit, weak exits, weak gates, or the model holding the wrong thesis through a drawdown",
                "Read nearest_failures[].drawdown_pack when present to see what the score and top feature contributors were saying during the worst pre-audit drawdown window",
                "Read nearest_failures[].gate_diagnostics, equity_shift_pack, time_bin_pack, and exemplar_trade_pack when present to understand which gates were too loose or too tight, how behavior changed after the peak, which windows were best or worst, and what concrete winners or losers looked like",
                "Read nearest_failures[].parent_delta when present to understand whether the last mutation actually improved trade count, drawdown, flip rate, or pre-audit return relative to its parent",
                "Read last_five_runs[].parent_delta, bottlenecks, and sweep_drift to see whether the run is actually improving or just surviving by trading less or by letting the sweep rewrite the spec",
                "If the run seems stuck, mixed, or family selection is unclear, use summarize_experiment_frontier plus inspect_pre_audit_spec to separate family-level edge from single-spec noise before mutating again",
                "If sweep_drift shows repeated material changes, assume the current proposal intent is too fragile; simplify the thesis, make critical gates and time controls explicit, and avoid depending on a knife-edge threshold mix",
                "When the evidence is contradictory, use tools before deciding which regime or feature family to trust",
                "When proposing more than two regime gates, or when the thesis relies on gates being critical, verify the gates with probe_spec_gate_impact before trusting the idea",
                "If recent misses show material sweep drift or dropped gates, use compare_intended_vs_frozen_spec before mutating the parent again",
                "Do not jump straight to a new idea. First reconcile the strongest positive anchors, the outstanding runs, and the recurring failure modes into a coherent diagnosis, then propose a spec that explicitly addresses that diagnosis",
                "For pair-trade formulas, treat the two legs symmetrically as asset_1 and asset_2 rather than assuming a privileged leg",
                "For pair-trade specs, choose an explicit trade_style from reversion, pullback, continuation, breakout, or hybrid",
                "For pair-trade specs, prefer interpretable signals such as relative momentum, spread dislocation, funding divergence, carry normalized by volatility, regime filters, dynamic residuals, hedge-ratio drift, mean-reversion speed, and compression or expansion state",
                "For pair-trade specs, build the idea in layers: thesis, core signal, regime gates, exit or holding logic, and cooldown when needed",
                "For pair-trade specs, use regime_gates.entry to express true regime gating; additive features alone do not create a hard gate",
                "Each regime_gates.entry item may be either a boolean formula expression such as ge(pair_corr_72h,0.9) or an expression plus min/max bounds",
                "Use gt, ge, lt, le, and, or, not, and where when you need custom regime logic inside regime_gates.entry expressions",
                "For pair-trade specs, you may choose from the preferred pair universes and tune gross_target, max_gross_target, signal_leverage_scale, entry_abs_score, exit_abs_score, flip_abs_score, max_holding_bars, and cooldown_bars",
                "For directional specs, think in terms of trend, breakout, pullback, reversal, breadth, and funding overlays across many assets rather than pair-ratio logic",
                "For basket-neutral specs, think in terms of long basket versus short basket, relative dislocation, dispersion compression, hedge-ratio drift, and cross-sectional carry imbalance",
                "For carry specs, think in terms of carry level, carry-to-vol, carry dispersion, funding stability, and avoiding carry flip risk",
                "Prefer mutations that can explain why they should improve on the parent based on parent_delta, gate diagnostics, and the worst time windows",
                "Use validation_leaders as the positive anchors when passed winners are sparse, and improve on their gate_reasons or weak validation metrics",
                "When recent passes cluster in one narrow feature neighborhood, deliberately test an orthogonal state variable such as dynamic beta, Kalman residuals, half-life, Hurst, autocorrelation, or carry regime z-scores rather than only tightening the same thresholds",
                "When the strongest positive anchors are concentrated in one family, prefer staying in that family unless recent same-family attempts are clearly degrading or the evidence says the edge is exhausted",
                "When novelty_pressure.required is true or run_context.force_novelty is true, branch materially by changing trade_style, universe, gating logic, or core signal class; switch families only when the current family neighborhood looks exhausted or contradictory",
                "Do not branch into a different family solely to satisfy novelty pressure if dominant_family_positive_anchor is true in the memory packet",
                "Remember that the evaluator only does a local policy sweep. A good spec should still make sense before the sweep; do not rely on the sweep to rescue a weak thesis",
                "Prefer realistic trade frequency and holding behavior; do not produce zero-trade, near-flat, or constant-flip specs unless you can justify the sparse activity economically",
                "When losses cluster in one regime, suppress or gate that regime rather than stacking more entry signals",
                "Prefer specs that remain robust if the realized history is shorter than the requested lookback",
                "Optimize for selector-window stability and validation robustness, not for the untouched final audit block",
                "Only emit these top-level keys per spec: track, family, hypothesis, neutrality_basis, features, universe, risk, regime_gates, params",
                "Do not emit unsupported keys such as formula_signals, rationale, score_breakdown, notes, or comments",
                "Prefer compact hypotheses",
                "Do not include prose outside JSON",
            ],
        }
        user_prompt = json.dumps(
            user_payload,
            separators=(",", ":"),
        )

        try:
            payload = await self.claude.complete_json_with_tools(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                tools=llm_tools,
                max_tokens=min(self.settings.claude_max_tokens, 4000),
                timeout_s=max(self.settings.claude_timeout_s, 120.0),
                max_tool_rounds=max(25, self.settings.claude_max_tool_rounds),
                stage="writer",
            )
            log_path = self._write_llm_exchange_log(
                track=track,
                parent=parent,
                system_prompt=system_prompt,
                user_payload=user_payload,
                parsed_response=payload,
                error=None,
                tool_names=[tool.name for tool in llm_tools],
            )
            self.last_llm_log_path = str(log_path)
            self.last_llm_trace = {
                "track": track,
                "parent_family": parent.family,
                "parent_hash": parent.strategy_hash(),
                "trace": dict(self.claude.last_trace or {}),
                "log_path": self.last_llm_log_path,
            }
        except Exception:
            log_path = self._write_llm_exchange_log(
                track=track,
                parent=parent,
                system_prompt=system_prompt,
                user_payload=user_payload,
                parsed_response=None,
                error="llm_proposal_failed",
                tool_names=[tool.name for tool in llm_tools],
            )
            self.last_llm_log_path = str(log_path)
            self.last_llm_trace = {
                "track": track,
                "parent_family": parent.family,
                "parent_hash": parent.strategy_hash(),
                "trace": dict(self.claude.last_trace or {}),
                "error": "llm_proposal_failed",
                "log_path": self.last_llm_log_path,
            }
            return []

        rows = self._extract_llm_spec_rows(payload)[:1]
        if self.last_llm_trace is not None:
            self.last_llm_trace["spec_count"] = len(rows)
        return [SignalSpec.from_dict(row) for row in rows if isinstance(row, dict)]

    def _extract_llm_spec_rows(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            return []
        if isinstance(payload.get("specs"), list):
            return [row for row in payload.get("specs") or [] if isinstance(row, dict)]
        spec = payload.get("spec")
        if isinstance(spec, dict):
            return [spec]

        # Claude sometimes returns the single spec object directly instead of wrapping it.
        top_level_keys = {
            "track",
            "family",
            "hypothesis",
            "neutrality_basis",
            "features",
            "universe",
            "risk",
            "regime_gates",
            "params",
        }
        if "track" in payload and "family" in payload and any(key in payload for key in top_level_keys):
            return [payload]
        return []

    def _write_llm_exchange_log(
        self,
        *,
        track: str,
        parent: SignalSpec,
        system_prompt: str,
        user_payload: dict[str, Any],
        parsed_response: dict[str, Any] | None,
        error: str | None,
        tool_names: list[str],
    ) -> Path:
        target_dir = self.settings.artifact_dir / "llm_traces" / track
        target_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        target = target_dir / f"{timestamp}_{parent.strategy_hash()}.json"
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "track": track,
            "parent_family": parent.family,
            "parent_hash": parent.strategy_hash(),
            "system_prompt": system_prompt,
            "user_payload": user_payload,
            "tool_names": tool_names,
            "claude_trace": dict(self.claude.last_trace or {}),
            "claude_exchange": dict(self.claude.last_exchange or {}),
            "parsed_response": parsed_response,
            "error": error,
        }
        target.write_text(json.dumps(payload, indent=2, ensure_ascii=True, default=str))
        return target

    def _compact_research_summary(self, research_summary: dict[str, Any]) -> dict[str, Any]:
        compact = {
            "track": research_summary.get("track"),
            "parent_family": research_summary.get("parent_family"),
            "parent_hash": research_summary.get("parent_hash"),
            "run_context": dict(research_summary.get("run_context") or {}),
            "market_bundle": dict(research_summary.get("market_bundle") or {}),
            "perp_symbols": list(research_summary.get("perp_symbols") or [])[:5],
            "perp_snapshot": list(research_summary.get("perp_snapshot") or [])[:4],
            "pair_calibration": dict(research_summary.get("pair_calibration") or {}),
            "stable_pt_markets": list(research_summary.get("stable_pt_markets") or [])[:4],
            "pt_rotation_markets": list(research_summary.get("pt_rotation_markets") or [])[:4],
            "lending_markets": list(research_summary.get("lending_markets") or [])[:4],
        }
        external = dict(research_summary.get("external_research") or {})
        compact_reports = []
        for report in list(external.get("reports") or [])[:2]:
            compact_reports.append(
                {
                    "query": report.get("query"),
                    "answer": report.get("answer"),
                    "insights": list(report.get("insights") or [])[:4],
                    "sources": list(report.get("sources") or [])[:3],
                }
            )
        compact["external_research"] = {
            "enabled": bool(external.get("enabled")),
            "provider": external.get("provider"),
            "queries": list(external.get("queries") or [])[:3],
            "reports": compact_reports,
        }
        return compact

    def _compact_memory_packet(self, memory_packet: dict[str, Any]) -> dict[str, Any]:
        coverage = dict(memory_packet.get("coverage_summary") or {})
        return {
            "market_bundle": dict(memory_packet.get("market_bundle") or {}),
            "pareto_frontier": list(memory_packet.get("pareto_frontier") or [])[:3],
            "validation_leaders": list(memory_packet.get("validation_leaders") or [])[:3],
            "nearest_winners": list(memory_packet.get("nearest_winners") or [])[:3],
            "nearest_failures": list(memory_packet.get("nearest_failures") or [])[:3],
            "outstanding_runs": list(memory_packet.get("outstanding_runs") or [])[:8],
            "last_five_runs": list(memory_packet.get("last_five_runs") or [])[:5],
            "archetype_coverage": list(memory_packet.get("archetype_coverage") or [])[:5],
            "novelty_pressure": dict(memory_packet.get("novelty_pressure") or {}),
            "coverage_summary": {
                "experiments_total": coverage.get("experiments_total"),
                "passed_total": coverage.get("passed_total"),
                "families": list(coverage.get("families") or [])[:6],
                "assets": list(coverage.get("assets") or [])[:6],
                "features": list(coverage.get("features") or [])[:8],
                "failure_modes": list(coverage.get("failure_modes") or [])[:6],
            },
            "failure_pattern_summary": dict(memory_packet.get("failure_pattern_summary") or {}),
            "behavior_pattern_summary": dict(memory_packet.get("behavior_pattern_summary") or {}),
            "regime_pattern_summary": dict(memory_packet.get("regime_pattern_summary") or {}),
            "drawdown_pattern_summary": dict(memory_packet.get("drawdown_pattern_summary") or {}),
            "gate_pattern_summary": dict(memory_packet.get("gate_pattern_summary") or {}),
            "equity_pattern_summary": dict(memory_packet.get("equity_pattern_summary") or {}),
            "query_cards": list(memory_packet.get("query_cards") or [])[:3],
        }

    def _formula_examples(self, track: str, family: str) -> list[str]:
        if track == "trend_signals" and family in PAIR_TRADE_FAMILIES:
            return [
                "sub(pct_change(asset_1_price,24), pct_change(asset_2_price,24))",
                "sub(rolling_mean(asset_1_funding,72), rolling_mean(asset_2_funding,72))",
                "sub(asset_2_funding_carry_to_vol, asset_1_funding_carry_to_vol)",
                "div(sub(price_ratio, rolling_mean(price_ratio,20)), rolling_std(price_ratio,20))",
                "div(sub(funding_spread, rolling_mean(funding_spread,72)), clip(rolling_std(funding_spread,72),0.000001,1.0))",
                "div(sub(pct_change(asset_1_price,24), pct_change(asset_2_price,24)), clip(pair_realized_vol_168h,0.01,10.0))",
                "mul(pair_rsi_centered_14, pair_ratio_return_24h)",
                "div(pair_ema_gap_12_26, clip(pair_realized_vol_168h,0.01,10.0))",
            ]
        if track == "trend_signals" and family in {
            BASKET_NEUTRAL_UNLEVERED_FAMILY,
            BASKET_NEUTRAL_LEVERED_FAMILY,
        }:
            return [
                "relative_carry_z_72h",
                "relative_momentum_24h",
                "breadth_adjusted_relative_momentum_24h",
                "sub(price_return_24h, funding_72h_mean)",
                "div(price_return_72h, clip(realized_vol_168h,0.01,10.0))",
                "neg(div(funding_72h_mean, clip(realized_vol_168h,0.01,10.0)))",
                "sub(neg(funding_168h_mean), funding_flip_prob_14d)",
                "sub(neg(funding_carry_to_vol), price_return_24h)",
                "sub(ema_gap_12_26, funding_z_168h)",
                "mul(rsi_centered_14, neg(funding_accel_24h))",
            ]
        if track == "trend_signals" and family == MULTI_ASSET_CARRY_FAMILY:
            return [
                "relative_carry_z_72h",
                "carry_term_structure_24_168",
                "carry_decay_ratio_24_168",
                "breadth_adjusted_relative_momentum_24h",
                "neg(funding_72h_mean)",
                "neg(div(funding_72h_mean, clip(realized_vol_168h,0.01,10.0)))",
                "sub(neg(funding_168h_mean), funding_flip_prob_14d)",
                "sub(neg(funding_carry_to_vol), funding_accel_24h)",
            ]
        if track == "trend_signals":
            return [
                "relative_momentum_24h",
                "relative_carry_z_72h",
                "div(sub(price, rolling_mean(price,20)), rolling_std(price,20))",
                "sub(rolling_mean(funding,72), rolling_mean(funding,24))",
                "neg(funding_carry_to_vol)",
                "sub(div(price_return_72h, clip(realized_vol_168h,0.01,10.0)), funding_flip_prob_14d)",
                "mul(rsi_centered_14, price_return_24h)",
                "div(ema_gap_12_26, clip(realized_vol_168h,0.01,10.0))",
            ]
        return [
            "sub(implied_apy, underlying_apy)",
            "div(sub(1.0, pt_price), days_to_expiry)",
            "div(combined_supply_apy, clip(utilization,0.15,1.0))",
        ]

    def _feature_idea_examples(self, track: str, family: str) -> list[dict[str, str]]:
        if track == "trend_signals" and family in PAIR_TRADE_FAMILIES:
            return [
                {
                    "idea": "Relative momentum spread",
                    "formula": "sub(pct_change(asset_1_price,24), pct_change(asset_2_price,24))",
                    "why": "Captures short-horizon performance divergence between the two legs.",
                },
                {
                    "idea": "Vol-normalized relative momentum",
                    "formula": "div(sub(pct_change(asset_1_price,24), pct_change(asset_2_price,24)), clip(pair_realized_vol_168h,0.01,10.0))",
                    "why": "Penalizes signals that only look large because the pair is noisy.",
                },
                {
                    "idea": "Spread z-score dislocation",
                    "formula": "div(sub(price_ratio, rolling_mean(price_ratio,60)), clip(rolling_std(price_ratio,60),0.0001,10.0))",
                    "why": "Measures how far the pair ratio has moved away from its recent center.",
                },
                {
                    "idea": "Funding divergence",
                    "formula": "sub(rolling_mean(asset_1_funding,72), rolling_mean(asset_2_funding,72))",
                    "why": "Captures persistent carry pressure differences across the two legs.",
                },
                {
                    "idea": "Funding spread z-score",
                    "formula": "div(sub(funding_spread, rolling_mean(funding_spread,72)), clip(rolling_std(funding_spread,72),0.000001,1.0))",
                    "why": "Flags unusually extreme funding dislocations rather than raw funding level alone.",
                },
                {
                    "idea": "Carry to volatility",
                    "formula": "div(rolling_mean(funding_spread,72), clip(pair_realized_vol_168h,0.01,10.0))",
                    "why": "Keeps carry signals from dominating when pair volatility is high.",
                },
                {
                    "idea": "Residual spread reversion",
                    "formula": "neg(pair_residual_z_60)",
                    "why": "Explicitly fades large log-spread dislocations instead of relying on raw ratio moves.",
                },
                {
                    "idea": "Carry spread normalized by each leg",
                    "formula": "pair_carry_to_vol_spread",
                    "why": "Compares one-sided funding pressure after normalizing each leg by its own volatility.",
                },
                {
                    "idea": "Trend quality spread",
                    "formula": "pair_trend_efficiency_spread_72h",
                    "why": "Lets the pair prefer the cleaner trend rather than just the faster move.",
                },
                {
                    "idea": "Correlation-aware continuation",
                    "formula": "mul(pair_return_spread_24h, clip(pair_corr_72h,0.0,1.0))",
                    "why": "Rewards relative momentum more when the pair is still trading as a coherent relationship.",
                },
                {
                    "idea": "One-sided funding carry",
                    "formula": "sub(asset_2_funding_carry_to_vol, asset_1_funding_carry_to_vol)",
                    "why": "Lets the pair lean away from the leg paying richer funding relative to its own volatility.",
                },
                {
                    "idea": "Momentum plus carry confirmation",
                    "formula": "mul(sub(pct_change(asset_1_price,24), pct_change(asset_2_price,24)), sub(rolling_mean(asset_1_funding,72), rolling_mean(asset_2_funding,72)))",
                    "why": "Deploys setups where price leadership and carry pressure point the same way.",
                },
                {
                    "idea": "Mean reversion under compression",
                    "formula": "div(neg(div(sub(price_ratio, rolling_mean(price_ratio,20)), clip(rolling_std(price_ratio,20),0.0001,10.0))), clip(add(pair_bollinger_width_20,0.05),0.05,10.0))",
                    "why": "Targets reversion when the spread is displaced while bandwidth remains relatively tight.",
                },
            ]
        if track == "trend_signals" and family in {
            BASKET_NEUTRAL_UNLEVERED_FAMILY,
            BASKET_NEUTRAL_LEVERED_FAMILY,
        }:
            return [
                {
                    "idea": "Cross-sectional relative momentum",
                    "formula": "div(price_return_72h, clip(realized_vol_168h,0.01,10.0))",
                    "why": "Ranks assets by cleaner trend quality before constructing long and short baskets.",
                },
                {
                    "idea": "Carry-aware reversion",
                    "formula": "sub(neg(funding_72h_mean), price_return_24h)",
                    "why": "Prefers cheap carry names after short-term weakness and avoids the most expensive crowded legs.",
                },
                {
                    "idea": "Funding acceleration fade",
                    "formula": "neg(funding_accel_24h)",
                    "why": "Targets assets where carry pressure is rolling over instead of accelerating further.",
                },
                {
                    "idea": "Carry stability overlay",
                    "formula": "sub(neg(funding_168h_mean), funding_flip_prob_14d)",
                    "why": "Keeps basket signals aligned with stable carry instead of crowded carry that reverses too often.",
                },
                {
                    "idea": "Relative carry richness",
                    "formula": "relative_carry_z_72h",
                    "why": "Measures whether an asset is rich or cheap on carry relative to the current basket rather than on absolute funding alone.",
                },
                {
                    "idea": "Breadth-adjusted relative momentum",
                    "formula": "breadth_adjusted_relative_momentum_24h",
                    "why": "Lets basket signals prefer names outperforming the market when participation is broad enough to trust the move.",
                },
            ]
        if track == "trend_signals" and family == MULTI_ASSET_CARRY_FAMILY:
            return [
                {
                    "idea": "Carry to volatility",
                    "formula": "neg(div(funding_72h_mean, clip(realized_vol_168h,0.01,10.0)))",
                    "why": "Finds the richest short side and the cheapest long side after penalizing noisy assets.",
                },
                {
                    "idea": "Stable carry spread",
                    "formula": "sub(neg(funding_168h_mean), funding_flip_prob_14d)",
                    "why": "Avoids carry edges that reverse too often to survive execution.",
                },
                {
                    "idea": "Carry deceleration",
                    "formula": "sub(neg(funding_carry_to_vol), funding_accel_24h)",
                    "why": "Prefers names where attractive carry is not simultaneously accelerating against the position.",
                },
                {
                    "idea": "Relative carry z-score",
                    "formula": "relative_carry_z_72h",
                    "why": "Makes the carry book basket-relative so it generalizes across changing symbol mixes and funding regimes.",
                },
                {
                    "idea": "Carry term structure",
                    "formula": "carry_term_structure_24_168",
                    "why": "Distinguishes stable carry from carry that is already decaying versus its longer-horizon baseline.",
                },
            ]
        if track == "trend_signals":
            return [
                {
                    "idea": "Vol-normalized momentum",
                    "formula": "div(price_return_24h, clip(realized_vol_168h,0.01,10.0))",
                    "why": "Separates true trend from noisy price moves.",
                },
                {
                    "idea": "Funding-adjusted momentum",
                    "formula": "sub(price_return_72h, funding_72h_mean)",
                    "why": "Avoids paying too much carry for a weak directional edge.",
                },
                {
                    "idea": "Carry-stable momentum overlay",
                    "formula": "sub(div(price_return_72h, clip(realized_vol_168h,0.01,10.0)), funding_flip_prob_14d)",
                    "why": "Lets directional signals keep trend quality while down-weighting unstable carry regimes.",
                },
                {
                    "idea": "Relative momentum versus market",
                    "formula": "relative_momentum_24h",
                    "why": "Ranks assets by outperformance versus the basket instead of rewarding broad market beta alone.",
                },
                {
                    "idea": "Relative carry overlay",
                    "formula": "relative_carry_z_72h",
                    "why": "Lets directional signals distinguish between genuine carry outliers and a market-wide funding regime shift.",
                },
            ]
        return [
            {
                "idea": "Discount roll-down",
                "formula": "div(sub(1.0, pt_price), days_to_expiry)",
                "why": "Measures PT convergence speed to par.",
            },
        ]

    def _deterministic_variants(
        self,
        track: str,
        parent: SignalSpec,
        family: str | list[str] | None = None,
    ) -> list[SignalSpec]:
        variants = [parent]
        base = parent.canonical_dict()
        seed_by_family = {
            spec.family: spec
            for spec in self.load_seed_specs(track, family=family)
        }
        family_order = list(seed_by_family)
        pair_family_order = [
            family_name
            for family_name in family_order
            if family_name in PAIR_TRADE_FAMILIES
        ]

        def next_family(current: str) -> str:
            index = family_order.index(current) if current in family_order else 0
            return family_order[(index + 1) % len(family_order)]

        if track == "trend_signals" and parent.family == "perp_multi_asset_decision":
            def _append(payload: dict[str, Any]) -> None:
                normalized = copy.deepcopy(payload)
                normalized["params"]["long_enabled"] = True
                normalized["params"]["short_enabled"] = True
                normalized["params"]["long_count"] = min(
                    int(normalized["universe"].get("max_symbols", 4)),
                    max(1, int(normalized["params"].get("long_count", 2))),
                )
                normalized["params"]["short_count"] = min(
                    int(normalized["universe"].get("max_symbols", 4)),
                    max(0, int(normalized["params"].get("short_count", 2))),
                )
                variants.append(SignalSpec.from_dict(normalized))

            def _append_cross_sectional(payload: dict[str, Any], symbols: list[str]) -> None:
                normalized = copy.deepcopy(payload)
                max_symbols = int(normalized["universe"].get("max_symbols", len(symbols) or 4))
                normalized["universe"]["basis_groups"] = list(
                    _trim_symbols(symbols, max_symbols=max_symbols)
                )
                normalized["universe"]["max_symbols"] = min(
                    max_symbols,
                    len(normalized["universe"]["basis_groups"]),
                )
                normalized["universe"]["lookback_days"] = TREND_SIGNALS_LOOKBACK_DAYS
                _append(normalized)

            def _append_pair_seed(payload: dict[str, Any], family_name: str) -> None:
                normalized = copy.deepcopy(payload)
                normalized["track"] = track
                normalized["family"] = family_name
                normalized["universe"]["basis_groups"] = list(
                    normalized["universe"].get("basis_groups")[:2] or PAIR_UNIVERSES[0]
                )
                normalized["universe"]["max_symbols"] = 2
                normalized["universe"]["lookback_days"] = TREND_SIGNALS_LOOKBACK_DAYS
                variants.append(SignalSpec.from_dict(normalized))

            basket_universes = _ordered_cross_sectional_universes(
                base["universe"].get("basis_groups"),
                prefer_alternates=True,
                max_symbols=int(base["universe"].get("max_symbols", 4)),
            )
            for basket_symbols in basket_universes:
                alternate_basket = copy.deepcopy(base)
                _append_cross_sectional(alternate_basket, basket_symbols)

                carry_overlay = copy.deepcopy(base)
                carry_overlay["features"] = [
                    "ema_gap_12_26",
                    "macd_hist_12_26_9",
                    "relative_momentum_24h",
                    "trend_strength_72h",
                    "relative_carry_z_72h",
                    "carry_term_structure_24_168",
                ]
                carry_overlay["params"]["min_abs_score"] = 0.18
                carry_overlay["risk"]["rebalance_threshold"] = 0.02
                _append_cross_sectional(carry_overlay, basket_symbols)

            for pair_family in pair_family_order:
                pair_seed_spec = seed_by_family.get(pair_family)
                if pair_seed_spec is None:
                    continue
                pair_seed = pair_seed_spec.canonical_dict()
                _append_pair_seed(pair_seed, pair_family)

                pair_payloads: list[dict[str, Any]] = []
                for pair_symbols in _ordered_pair_universes(
                    pair_seed["universe"].get("basis_groups"),
                    prefer_alternates=False,
                ):
                    base_pair = copy.deepcopy(pair_seed)
                    base_pair["universe"]["basis_groups"] = list(pair_symbols)
                    pair_payloads.append(base_pair)

                    mean_reversion_pair = copy.deepcopy(base_pair)
                    mean_reversion_pair["features"] = list(PAIR_MEAN_REVERSION_FEATURES)
                    mean_reversion_pair["risk"]["rebalance_threshold"] = 0.02
                    mean_reversion_pair["params"]["min_abs_score"] = 0.22
                    mean_reversion_pair["params"]["signal_leverage_scale"] = 1.0
                    pair_payloads.append(mean_reversion_pair)

                    quality_momentum_pair = copy.deepcopy(base_pair)
                    quality_momentum_pair["features"] = list(PAIR_QUALITY_MOMENTUM_FEATURES)
                    quality_momentum_pair["risk"]["rebalance_threshold"] = 0.01
                    quality_momentum_pair["params"]["min_abs_score"] = 0.16
                    quality_momentum_pair["params"]["signal_leverage_scale"] = 0.6
                    quality_momentum_pair["params"]["max_gross_target"] = (
                        1.0 if pair_family == UNLEVERED_PAIR_FAMILY else 3.0
                    )
                    pair_payloads.append(quality_momentum_pair)

                    compression_reversion_pair = copy.deepcopy(base_pair)
                    compression_reversion_pair["features"] = list(PAIR_COMPRESSION_REVERSION_FEATURES)
                    compression_reversion_pair["risk"]["rebalance_threshold"] = 0.015
                    compression_reversion_pair["params"]["gross_target"] = 1.0
                    compression_reversion_pair["params"]["max_gross_target"] = (
                        1.0 if pair_family == UNLEVERED_PAIR_FAMILY else 2.25
                    )
                    compression_reversion_pair["params"]["min_abs_score"] = 0.24
                    compression_reversion_pair["params"]["signal_leverage_scale"] = 0.9
                    compression_reversion_pair["risk"]["max_leverage"] = (
                        1.0 if pair_family == UNLEVERED_PAIR_FAMILY else 2.25
                    )
                    pair_payloads.append(compression_reversion_pair)

                for pair_payload in pair_payloads:
                    _append_pair_seed(pair_payload, pair_family)

            for family_name in family_order:
                if family_name in {parent.family, *pair_family_order}:
                    continue
                extra_seed = seed_by_family.get(family_name)
                if extra_seed is None:
                    continue
                variants.append(SignalSpec.from_dict(extra_seed.canonical_dict()))

            short_lookback = copy.deepcopy(base)
            short_lookback["universe"]["lookback_days"] = 30
            short_lookback["risk"]["rebalance_threshold"] = 0.01
            _append(short_lookback)

            medium_lookback = copy.deepcopy(base)
            medium_lookback["universe"]["lookback_days"] = 60
            medium_lookback["risk"]["rebalance_threshold"] = 0.02
            medium_lookback["params"]["min_abs_score"] = 0.15
            _append(medium_lookback)

            conservative_gross = copy.deepcopy(base)
            conservative_gross["params"]["gross_target"] = 0.8
            conservative_gross["risk"]["max_leverage"] = 1.0
            conservative_gross["params"]["min_abs_score"] = 0.25
            _append(conservative_gross)

            base_leverage = copy.deepcopy(base)
            base_leverage["params"]["gross_target"] = 1.0
            base_leverage["risk"]["max_leverage"] = 1.0
            _append(base_leverage)

            moderate_leverage = copy.deepcopy(base)
            moderate_leverage["params"]["gross_target"] = 1.0
            moderate_leverage["risk"]["max_leverage"] = 1.5
            _append(moderate_leverage)

            aggressive_gross = copy.deepcopy(base)
            aggressive_gross["params"]["gross_target"] = 1.2
            aggressive_gross["risk"]["max_leverage"] = 1.5
            _append(aggressive_gross)

            breakout = copy.deepcopy(base)
            breakout["features"] = [
                "donchian_position_20",
                "donchian_width_20",
                "ema_gap_12_26",
                "price_return_24h",
                "price_return_72h",
                "realized_vol_168h",
            ]
            breakout["risk"]["rebalance_threshold"] = 0.01
            _append(breakout)

            trend_confirmation = copy.deepcopy(base)
            trend_confirmation["features"] = [
                "ema_gap_12_26",
                "macd_hist_12_26_9",
                "price_return_72h",
                "price_return_168h",
                "rsi_centered_14",
                "trend_strength_72h",
            ]
            trend_confirmation["universe"]["lookback_days"] = 60
            trend_confirmation["params"]["min_abs_score"] = 0.20
            _append(trend_confirmation)

            fast_momentum = copy.deepcopy(base)
            fast_momentum["features"] = [
                "ema_gap_12_26",
                "macd_hist_12_26_9",
                "price_return_24h",
                "price_return_72h",
                "rsi_centered_14",
                "sub(rolling_mean(funding,72), rolling_mean(funding,24))",
            ]
            fast_momentum["risk"]["rebalance_threshold"] = 0.01
            _append(fast_momentum)

            slower_momentum = copy.deepcopy(base)
            slower_momentum["features"] = [
                "donchian_position_20",
                "ema_gap_12_26",
                "macd_hist_12_26_9",
                "price_return_168h",
                "realized_vol_168h",
                "rsi_centered_14",
            ]
            slower_momentum["universe"]["lookback_days"] = 120
            slower_momentum["risk"]["rebalance_threshold"] = 0.05
            slower_momentum["params"]["min_abs_score"] = 0.30
            _append(slower_momentum)
        elif track == "trend_signals" and parent.family in PAIR_TRADE_FAMILIES:
            def _append_pair(payload: dict[str, Any]) -> None:
                normalized = copy.deepcopy(payload)
                normalized["universe"]["basis_groups"] = list(
                    normalized["universe"].get("basis_groups")[:2] or PAIR_UNIVERSES[0]
                )
                normalized["universe"]["max_symbols"] = 2
                variants.append(SignalSpec.from_dict(normalized))

            multi_seed_spec = seed_by_family.get("perp_multi_asset_decision")
            if multi_seed_spec is not None:
                variants.append(SignalSpec.from_dict(multi_seed_spec.canonical_dict()))

            for pair_symbols in _ordered_pair_universes(
                base["universe"].get("basis_groups"),
                prefer_alternates=True,
            ):
                mean_reversion_pair = copy.deepcopy(base)
                mean_reversion_pair["universe"]["basis_groups"] = list(pair_symbols)
                mean_reversion_pair["features"] = list(PAIR_MEAN_REVERSION_FEATURES)
                mean_reversion_pair["risk"]["rebalance_threshold"] = 0.02
                mean_reversion_pair["params"]["min_abs_score"] = 0.22
                mean_reversion_pair["params"]["signal_leverage_scale"] = 1.0
                mean_reversion_pair["params"]["max_gross_target"] = (
                    1.0 if parent.family == UNLEVERED_PAIR_FAMILY else 3.0
                )
                _append_pair(mean_reversion_pair)

                quality_momentum_pair = copy.deepcopy(base)
                quality_momentum_pair["universe"]["basis_groups"] = list(pair_symbols)
                quality_momentum_pair["features"] = list(PAIR_QUALITY_MOMENTUM_FEATURES)
                quality_momentum_pair["risk"]["rebalance_threshold"] = 0.01
                quality_momentum_pair["params"]["min_abs_score"] = 0.16
                quality_momentum_pair["params"]["max_gross_target"] = (
                    1.0 if parent.family == UNLEVERED_PAIR_FAMILY else 3.0
                )
                quality_momentum_pair["params"]["signal_leverage_scale"] = 0.6
                _append_pair(quality_momentum_pair)

                dynamic_residual_pair = copy.deepcopy(base)
                dynamic_residual_pair["universe"]["basis_groups"] = list(pair_symbols)
                dynamic_residual_pair["features"] = list(PAIR_DYNAMIC_RESIDUAL_FEATURES)
                dynamic_residual_pair["risk"]["rebalance_threshold"] = 0.015
                dynamic_residual_pair["params"]["min_abs_score"] = 0.18
                dynamic_residual_pair["params"]["max_holding_bars"] = 72
                dynamic_residual_pair["params"]["cooldown_bars"] = 8
                _append_pair(dynamic_residual_pair)

                reversion_speed_pair = copy.deepcopy(base)
                reversion_speed_pair["universe"]["basis_groups"] = list(pair_symbols)
                reversion_speed_pair["features"] = list(PAIR_MEAN_REVERSION_SPEED_FEATURES)
                reversion_speed_pair["risk"]["rebalance_threshold"] = 0.02
                reversion_speed_pair["params"]["min_abs_score"] = 0.2
                reversion_speed_pair["params"]["max_holding_bars"] = 48
                reversion_speed_pair["params"]["cooldown_bars"] = 6
                _append_pair(reversion_speed_pair)

                carry_regime_pair = copy.deepcopy(base)
                carry_regime_pair["universe"]["basis_groups"] = list(pair_symbols)
                carry_regime_pair["features"] = list(PAIR_CARRY_REGIME_FEATURES)
                carry_regime_pair["risk"]["rebalance_threshold"] = 0.02
                carry_regime_pair["params"]["min_abs_score"] = 0.18
                carry_regime_pair["params"]["max_holding_bars"] = 72
                carry_regime_pair["params"]["cooldown_bars"] = 4
                _append_pair(carry_regime_pair)

                compression_reversion_pair = copy.deepcopy(base)
                compression_reversion_pair["universe"]["basis_groups"] = list(pair_symbols)
                compression_reversion_pair["features"] = list(PAIR_COMPRESSION_REVERSION_FEATURES)
                compression_reversion_pair["risk"]["rebalance_threshold"] = 0.015
                compression_reversion_pair["params"]["min_abs_score"] = 0.24
                compression_reversion_pair["params"]["max_gross_target"] = (
                    1.0 if parent.family == UNLEVERED_PAIR_FAMILY else 2.25
                )
                compression_reversion_pair["params"]["signal_leverage_scale"] = 0.9
                _append_pair(compression_reversion_pair)

                aggressive_pair = copy.deepcopy(base)
                aggressive_pair["universe"]["basis_groups"] = list(pair_symbols)
                aggressive_pair["features"] = list(PAIR_QUALITY_MOMENTUM_FEATURES)
                aggressive_pair["params"]["gross_target"] = 1.0
                aggressive_pair["params"]["max_gross_target"] = (
                    1.0 if parent.family == UNLEVERED_PAIR_FAMILY else 3.0
                )
                aggressive_pair["params"]["signal_leverage_scale"] = 0.5
                aggressive_pair["risk"]["max_leverage"] = (
                    1.0 if parent.family == UNLEVERED_PAIR_FAMILY else 3.0
                )
                aggressive_pair["risk"]["max_asset_weight"] = (
                    0.5 if parent.family == UNLEVERED_PAIR_FAMILY else 1.5
                )
                _append_pair(aggressive_pair)

                tighter_gate = copy.deepcopy(base)
                tighter_gate["universe"]["basis_groups"] = list(pair_symbols)
                tighter_gate["params"]["min_abs_score"] = min(
                    0.75,
                    float(tighter_gate["params"].get("min_abs_score", 0.15)) + 0.1,
                )
                tighter_gate["params"]["signal_leverage_scale"] = max(
                    0.35,
                    float(tighter_gate["params"].get("signal_leverage_scale", 0.75)) - 0.1,
                )
                tighter_gate["params"]["cooldown_bars"] = max(
                    4,
                    int(tighter_gate["params"].get("cooldown_bars", 0)),
                )
                _append_pair(tighter_gate)
            for family_name in family_order:
                if family_name in {parent.family, "perp_multi_asset_decision"}:
                    continue
                extra_seed = seed_by_family.get(family_name)
                if extra_seed is None:
                    continue
                variants.append(SignalSpec.from_dict(extra_seed.canonical_dict()))
        elif track == "trend_signals":
            basket_universes = _ordered_cross_sectional_universes(
                base["universe"].get("basis_groups"),
                prefer_alternates=True,
                max_symbols=int(base["universe"].get("max_symbols", 4)),
            )

            def _with_cross_sectional_universe(payload: dict[str, Any], symbols: list[str]) -> SignalSpec:
                normalized = copy.deepcopy(payload)
                max_symbols = int(normalized["universe"].get("max_symbols", len(symbols) or 4))
                normalized["universe"]["basis_groups"] = list(
                    _trim_symbols(symbols, max_symbols=max_symbols)
                )
                normalized["universe"]["max_symbols"] = min(
                    max_symbols,
                    len(normalized["universe"]["basis_groups"]),
                )
                normalized["universe"]["lookback_days"] = TREND_SIGNALS_LOOKBACK_DAYS
                return SignalSpec.from_dict(normalized)

            for basket_symbols in basket_universes:
                variants.append(_with_cross_sectional_universe(base, basket_symbols))

                carry_overlay_seed = copy.deepcopy(base)
                if parent.family in {
                    BASKET_NEUTRAL_UNLEVERED_FAMILY,
                    BASKET_NEUTRAL_LEVERED_FAMILY,
                }:
                    carry_overlay_seed["features"] = [
                        "relative_momentum_24h",
                        "price_return_72h",
                        "realized_vol_168h",
                        "relative_carry_z_72h",
                        "carry_term_structure_24_168",
                        "funding_carry_to_vol",
                    ]
                    carry_overlay_seed["params"]["min_abs_score"] = 0.16
                elif parent.family == MULTI_ASSET_CARRY_FAMILY:
                    carry_overlay_seed["features"] = [
                        "relative_carry_z_72h",
                        "relative_carry_168h",
                        "carry_term_structure_24_168",
                        "carry_decay_ratio_24_168",
                        "funding_flip_prob_14d",
                        "funding_carry_to_vol",
                        "price_return_24h",
                    ]
                    carry_overlay_seed["params"]["min_abs_score"] = 0.12
                variants.append(_with_cross_sectional_universe(carry_overlay_seed, basket_symbols))

            first = copy.deepcopy(base)
            if parent.family == "perp_multi_asset_decision":
                first["params"]["long_count"] = min(
                    int(first["params"].get("long_count", 2)) + 1,
                    int(first["universe"].get("max_symbols", 6)),
                )
                first["params"]["long_enabled"] = True
            else:
                first["params"]["min_abs_score"] = min(
                    0.75,
                    float(first["params"].get("min_abs_score", 0.15)) + 0.1,
                )
            first["risk"]["rebalance_threshold"] = max(
                0.01, float(first["risk"].get("rebalance_threshold", 0.03)) - 0.01
            )
            variants.append(SignalSpec.from_dict(first))

            alternate_family = next_family(parent.family)
            second = seed_by_family[alternate_family].canonical_dict()
            variants.append(SignalSpec.from_dict(second))

            third = copy.deepcopy(base)
            third["params"]["gross_target"] = max(
                0.6, min(1.2, float(third["params"].get("gross_target", 1.0)) + 0.1)
            )
            third["universe"]["lookback_days"] = max(
                30, min(180, int(third["universe"].get("lookback_days", 90)) - 14)
            )
            third["params"]["min_abs_score"] = min(
                0.75,
                max(0.0, float(third["params"].get("min_abs_score", 0.0)) + 0.2),
            )
            variants.append(SignalSpec.from_dict(third))

            fourth = copy.deepcopy(base)
            fourth["risk"]["max_leverage"] = max(
                1.0, min(3.0, float(fourth["risk"].get("max_leverage", 2.0)) + 0.5)
            )
            if parent.family == "perp_multi_asset_decision":
                fourth["params"]["short_count"] = max(
                    0, int(fourth["params"].get("short_count", 1)) + 1
                )
                fourth["params"]["short_enabled"] = not bool(
                    fourth["params"].get("short_enabled", True)
                )
                fourth["features"] = [
                    "ema_gap_12_26",
                    "macd_hist_12_26_9",
                    "rsi_centered_14",
                    "donchian_position_20",
                    "sub(rolling_mean(funding,72), rolling_mean(funding,24))",
                    "realized_vol_168h",
                ]
                fourth["params"]["min_abs_score"] = 0.2
            elif parent.family in {
                BASKET_NEUTRAL_UNLEVERED_FAMILY,
                BASKET_NEUTRAL_LEVERED_FAMILY,
            }:
                fourth["features"] = [
                    "price_return_24h",
                    "price_return_72h",
                    "ema_gap_12_26",
                    "realized_vol_168h",
                    "funding_72h_mean",
                    "funding_carry_to_vol",
                ]
                fourth["params"]["min_abs_score"] = 0.16
                fourth["params"]["long_count"] = max(1, int(fourth["params"].get("long_count", 2)))
                fourth["params"]["short_count"] = max(1, int(fourth["params"].get("short_count", 2)))
            elif parent.family == MULTI_ASSET_CARRY_FAMILY:
                fourth["features"] = [
                    "relative_carry_z_72h",
                    "relative_carry_168h",
                    "carry_term_structure_24_168",
                    "carry_decay_ratio_24_168",
                    "funding_flip_prob_14d",
                    "funding_carry_to_vol",
                    "price_return_24h",
                ]
                fourth["params"]["min_abs_score"] = 0.12
            else:
                fourth["features"] = [
                    "pair_ema_gap_12_26",
                    "pair_macd_hist_12_26_9",
                    "pair_rsi_centered_14",
                    "funding_spread_72h_mean",
                    "funding_spread_flip_prob_14d",
                ]
                fourth["params"]["min_abs_score"] = 0.2
            variants.append(SignalSpec.from_dict(fourth))
        else:
            first = copy.deepcopy(base)
            first["params"]["selection_count"] = min(
                int(first["params"].get("selection_count", 2)) + 1,
                int(first["universe"].get("max_symbols", 5)),
            )
            first["risk"]["roll_days_before_expiry"] = int(
                first["risk"].get("roll_days_before_expiry", 5)
            ) + 2
            variants.append(SignalSpec.from_dict(first))

            alternate_family = next_family(parent.family)
            second = seed_by_family[alternate_family].canonical_dict()
            variants.append(SignalSpec.from_dict(second))

            third = copy.deepcopy(base)
            if third["family"] == "pt_yield_rotation":
                current_mode = str(third["params"].get("hedge_mode", "perp")).lower()
                third["params"]["hedge_mode"] = "none" if current_mode == "perp" else "perp"
                third["params"]["hedge_ratio"] = max(
                    0.0,
                    min(1.25, float(third["params"].get("hedge_ratio", 0.75)) + 0.25),
                )
                third["universe"]["basis_groups"] = ["BTC", "ETH", "SOL", "HYPE"]
            elif third["family"] == "lending_carry_rotation":
                current_mode = str(third["params"].get("hedge_mode", "perp")).lower()
                third["params"]["hedge_mode"] = "none" if current_mode == "perp" else "perp"
                third["params"]["hedge_ratio"] = max(
                    0.0,
                    min(1.25, float(third["params"].get("hedge_ratio", 0.75)) + 0.25),
                )
                third["features"] = [
                    "combined_supply_apy",
                    "supply_reward_apr",
                    "pct_change(supply_tvl_usd,168)",
                    "div(combined_supply_apy, clip(utilization,0.15,1.0))",
                ]
            else:
                third["params"]["gross_target"] = max(
                    0.5, min(1.1, float(third["params"].get("gross_target", 0.9)) - 0.1)
                )
                third["universe"]["lookback_days"] = max(
                    30, min(180, int(third["universe"].get("lookback_days", 120)) - 21)
                )
            variants.append(SignalSpec.from_dict(third))

            fourth = copy.deepcopy(base)
            fourth["risk"]["max_asset_weight"] = max(
                0.1,
                min(0.5, float(fourth["risk"].get("max_asset_weight", 0.4)) - 0.05),
            )
            fourth["risk"]["rebalance_threshold"] = max(
                0.0,
                min(0.25, float(fourth["risk"].get("rebalance_threshold", 0.05)) - 0.02),
            )
            if fourth["family"] == "stable_pt_ladder":
                fourth["params"]["selection_count"] = max(
                    1,
                    int(fourth["params"].get("selection_count", 3)) - 1,
                )
            if fourth["family"] == "pt_yield_rotation":
                fourth["features"] = [
                    "sub(implied_apy, underlying_apy)",
                    "div(sub(1.0, pt_price), days_to_expiry)",
                    "pct_change(total_tvl,30)",
                ]
            if fourth["family"] == "lending_carry_rotation":
                fourth["features"] = [
                    "combined_supply_apy",
                    "base_yield_apy",
                    "pct_change(supply_tvl_usd,168)",
                    "div(combined_supply_apy, clip(utilization,0.15,1.0))",
                ]
            variants.append(SignalSpec.from_dict(fourth))

        return variants

    def _coerce_bool(self, value: Any, default: bool = True) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "1", "yes", "on"}:
                return True
            if lowered in {"false", "0", "no", "off"}:
                return False
        if value is None:
            return default
        return bool(value)

    def _normalize_regime_gates(
        self,
        *,
        spec: SignalSpec,
        family_allowed_features: set[str],
        feature_aliases: dict[str, str],
        raw_series: set[str],
    ) -> dict[str, Any]:
        execution_profile = self._family_execution_profile(spec.track, spec.family)
        if execution_profile not in {"ranked_directional", "basket_neutral_spread", "ranked_carry", "basis_hedged"}:
            return {}

        payload = dict(spec.regime_gates or {})
        raw_entry = payload.get("entry") or []
        if isinstance(raw_entry, (str, dict)):
            raw_entry = [raw_entry]

        normalized_entry: list[dict[str, Any]] = []
        for spec in list(raw_entry):
            expression = ""
            minimum = None
            maximum = None
            if isinstance(spec, str):
                expression = spec.strip()
            elif isinstance(spec, dict):
                expression = str(spec.get("expression") or spec.get("feature") or "").strip()
                minimum = spec.get("min")
                maximum = spec.get("max")
            if not expression:
                continue
            if expression not in family_allowed_features and not is_valid_feature_expression(
                expression,
                aliases=feature_aliases,
                raw_series=raw_series,
            ):
                continue
            gate_spec: dict[str, str | float] = {"expression": expression}
            if minimum is not None:
                gate_spec["min"] = float(minimum)
            if maximum is not None:
                gate_spec["max"] = float(maximum)
            normalized_entry.append(gate_spec)

        if not normalized_entry:
            return {}
        normalized_entry = sorted(
            normalized_entry,
            key=lambda row: json.dumps(row, sort_keys=True, separators=(",", ":")),
        )

        return {
            "entry": normalized_entry,
            "exit_on_break": self._coerce_bool(payload.get("exit_on_break"), default=True),
        }

    def _validate_spec(
        self,
        *,
        spec: SignalSpec,
        track: str,
        allowed_families: list[str],
        allowed_features_by_family: dict[str, list[str]],
        family_defaults: dict[str, Any],
    ) -> SignalSpec:
        if spec.track != track:
            spec.track = track
        if spec.family not in allowed_families:
            raise ValueError(f"Unsupported family for track {track}: {spec.family}")

        feature_spec = load_feature_spec(
            self.settings.root_dir,
            track=track,
            family=spec.family,
        )
        feature_aliases = feature_spec.get("aliases") or {}
        raw_series = set(feature_spec.get("raw_series") or [])
        family_allowed_features = set(allowed_features_by_family.get(spec.family) or [])
        cleaned_features = []
        for feature in spec.features:
            if feature in family_allowed_features:
                cleaned_features.append(feature)
                continue
            if is_valid_feature_expression(
                feature,
                aliases=feature_aliases,
                raw_series=raw_series,
            ):
                cleaned_features.append(feature)
        if not cleaned_features:
            cleaned_features = list(family_defaults[spec.family]["features"])
        spec.features = cleaned_features
        spec.regime_gates = self._normalize_regime_gates(
            spec=spec,
            family_allowed_features=family_allowed_features,
            feature_aliases=feature_aliases,
            raw_series=raw_series,
        )

        spec.universe.max_symbols = max(1, min(spec.universe.max_symbols, 8))
        spec.universe.lookback_days = max(
            21, min(spec.universe.lookback_days, TREND_SIGNALS_LOOKBACK_DAYS)
        )
        spec.risk.max_leverage = max(1.0, min(spec.risk.max_leverage, 3.0))
        spec.risk.rebalance_threshold = max(
            0.0, min(spec.risk.rebalance_threshold, 0.25)
        )

        if track == "trend_signals":
            # Keep directional-perp experiments on a fixed horizon so
            # comparisons and charts are apples-to-apples across generations.
            spec.universe.lookback_days = TREND_SIGNALS_LOOKBACK_DAYS
            execution_profile = self._family_execution_profile(track, spec.family)
            spec.params["min_abs_score"] = max(
                0.0,
                min(1.5, float(spec.params.get("min_abs_score", 0.0))),
            )
            if spec.family in PAIR_TRADE_FAMILIES:
                trade_style = str(spec.params.get("trade_style") or "").strip().lower()
                if trade_style not in {
                    "reversion",
                    "pullback",
                    "continuation",
                    "breakout",
                    "hybrid",
                }:
                    trade_style = "hybrid"
                spec.params["trade_style"] = trade_style
                spec.risk.max_asset_weight = max(
                    0.10,
                    min(
                        spec.risk.max_asset_weight,
                        0.5 if spec.family == UNLEVERED_PAIR_FAMILY else 2.0,
                    ),
                )
                spec.params["gross_target"] = max(
                    0.5,
                    min(1.5, float(spec.params.get("gross_target", 1.0))),
                )
                max_gross_cap = 1.0 if spec.family == UNLEVERED_PAIR_FAMILY else 3.0
                spec.params["max_gross_target"] = max(
                    spec.params["gross_target"],
                    min(
                        max_gross_cap,
                        float(
                            spec.params.get(
                                "max_gross_target",
                                spec.params["gross_target"],
                            )
                        ),
                    ),
                )
                spec.params["signal_leverage_scale"] = max(
                    0.25,
                    min(3.0, float(spec.params.get("signal_leverage_scale", 0.75))),
                )
                entry_abs_score = max(
                    0.0,
                    min(
                        1.5,
                        float(
                            spec.params.get(
                                "entry_abs_score",
                                spec.params.get("min_abs_score", 0.0),
                            )
                        ),
                    ),
                )
                spec.params["entry_abs_score"] = entry_abs_score
                spec.params["min_abs_score"] = entry_abs_score
                spec.params["exit_abs_score"] = max(
                    0.0,
                    min(
                        entry_abs_score,
                        float(spec.params.get("exit_abs_score", entry_abs_score * 0.5)),
                    ),
                )
                spec.params["flip_abs_score"] = max(
                    entry_abs_score,
                    min(
                        2.5,
                        float(spec.params.get("flip_abs_score", entry_abs_score)),
                    ),
                )
                spec.params["max_holding_bars"] = max(
                    0,
                    min(24 * 14, int(spec.params.get("max_holding_bars", 0))),
                )
                spec.params["cooldown_bars"] = max(
                    0,
                    min(24 * 7, int(spec.params.get("cooldown_bars", 0))),
                )
                spec.universe.max_symbols = 2
                pair_symbols = list(spec.universe.basis_groups[:2] or PAIR_UNIVERSES[0])
                if len(pair_symbols) < 2:
                    pair_symbols = PAIR_UNIVERSES[0]
                spec.universe.basis_groups = pair_symbols
                if spec.family == UNLEVERED_PAIR_FAMILY:
                    spec.params["gross_target"] = min(
                        1.0,
                        float(spec.params["gross_target"]),
                    )
                    spec.params["max_gross_target"] = 1.0
                    spec.risk.max_leverage = 1.0
                else:
                    spec.risk.max_leverage = max(
                        spec.params["max_gross_target"],
                        spec.risk.max_leverage,
                    )
                if len(spec.universe.basis_groups) < 2:
                    spec.universe.basis_groups = PAIR_UNIVERSES[0]
                return spec

            if execution_profile in {"basket_neutral_spread", "ranked_carry"}:
                spec.risk.max_asset_weight = max(
                    0.05,
                    min(
                        spec.risk.max_asset_weight,
                        0.5 if spec.family == BASKET_NEUTRAL_UNLEVERED_FAMILY else 1.0,
                    ),
                )
                spec.params["gross_target"] = max(
                    0.4,
                    min(2.5, float(spec.params.get("gross_target", 1.0))),
                )
                spec.params["long_count"] = max(
                    1,
                    min(
                        int(spec.params.get("long_count", 2)),
                        spec.universe.max_symbols,
                    ),
                )
                spec.params["short_count"] = max(
                    1,
                    min(
                        int(spec.params.get("short_count", 2)),
                        spec.universe.max_symbols,
                    ),
                )
                spec.params["long_enabled"] = self._coerce_bool(
                    spec.params.get("long_enabled", True),
                    default=True,
                )
                spec.params["short_enabled"] = self._coerce_bool(
                    spec.params.get("short_enabled", True),
                    default=True,
                )
                if spec.family == BASKET_NEUTRAL_UNLEVERED_FAMILY:
                    spec.risk.max_leverage = 1.0
                return spec

            spec.risk.max_asset_weight = max(
                0.05, min(spec.risk.max_asset_weight, 0.5)
            )
            spec.params["gross_target"] = max(
                0.5,
                min(1.5, float(spec.params.get("gross_target", 1.0))),
            )

            spec.params["long_count"] = max(
                1,
                min(
                    int(spec.params.get("long_count", 1)),
                    spec.universe.max_symbols,
                ),
            )
            spec.params["short_count"] = max(
                0,
                min(
                    int(spec.params.get("short_count", 0)),
                    spec.universe.max_symbols,
                ),
            )
            spec.params["long_enabled"] = self._coerce_bool(
                spec.params.get("long_enabled", True),
                default=True,
            )
            spec.params["short_enabled"] = self._coerce_bool(
                spec.params.get("short_enabled", True),
                default=True,
            )
        else:
            spec.risk.max_asset_weight = max(
                0.05, min(spec.risk.max_asset_weight, 0.5)
            )
            spec.params["selection_count"] = max(
                1,
                min(
                    int(spec.params.get("selection_count", 2)),
                    spec.universe.max_symbols,
                ),
            )
            spec.params["gross_target"] = max(
                0.4,
                min(1.2, float(spec.params.get("gross_target", 0.8))),
            )
            hedge_mode = str(spec.params.get("hedge_mode", "none")).lower()
            if hedge_mode not in {"none", "perp"}:
                hedge_mode = "none"
            spec.params["hedge_mode"] = hedge_mode
            spec.params["hedge_ratio"] = max(
                0.0,
                min(1.5, float(spec.params.get("hedge_ratio", 0.75))),
            )
        return spec

    def _allowed_families(
        self,
        track: str,
        family: str | list[str] | None = None,
    ) -> list[str]:
        families = list(load_track_family_specs(self.settings.root_dir, track).keys())
        family_scope = _family_scope(family)
        if family_scope is None:
            return families
        missing = [name for name in family_scope if name not in families]
        if missing:
            raise ValueError(f"Unsupported family for track {track}: {', '.join(missing)}")
        return [name for name in families if name in family_scope]

    def _family_defaults(
        self,
        track: str,
        family: str | list[str] | None = None,
    ) -> dict[str, Any]:
        families = load_track_family_specs(self.settings.root_dir, track)
        out: dict[str, Any] = {}
        family_scope = _family_scope(family)
        for family_name, payload in families.items():
            if family_scope is not None and family_name not in family_scope:
                continue
            out[family_name] = {
                "params": dict(payload.get("defaults") or {}),
                "features": list((payload.get("feature_weights") or {}).keys()),
            }
        return out

    def _allowed_features(self, track: str) -> list[str]:
        feature_guide = load_feature_spec(self.settings.root_dir, track=track)
        return sorted((feature_guide.get("aliases") or {}).keys())

    def _allowed_features_by_family(
        self,
        track: str,
        family: str | list[str] | None = None,
    ) -> dict[str, list[str]]:
        allowed_families = self._allowed_families(track, family=family)
        return {
            family_name: sorted(
                (
                    load_feature_spec(
                        self.settings.root_dir,
                        track=track,
                        family=family_name,
                    ).get("aliases")
                    or {}
                ).keys()
            )
            for family_name in allowed_families
        }


def _family_scope(family: str | list[str] | None) -> set[str] | None:
    if family is None:
        return None
    if isinstance(family, str):
        return {family}
    return {str(item) for item in family}


def _safe_float(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric




