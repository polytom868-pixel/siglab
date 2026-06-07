from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from siglab.evaluation.feature_dsl import load_feature_spec
from siglab.families import (
    family_execution_profile,
    load_family_spec,
)
from siglab.orchestration.contracts import feature_roles_for_formula
from siglab.search.mutate import (
    BASKET_NEUTRAL_FAMILIES,
    CROSS_SECTIONAL_UNIVERSES,
    MULTI_ASSET_CARRY_FAMILY,
    PAIR_CARRY_REGIME_FEATURES,
    PAIR_COMPRESSION_REVERSION_FEATURES,
    PAIR_DYNAMIC_RESIDUAL_FEATURES,
    PAIR_MEAN_REVERSION_FEATURES,
    PAIR_MEAN_REVERSION_SPEED_FEATURES,
    PAIR_QUALITY_MOMENTUM_FEATURES,
    PAIR_TRADE_FAMILIES,
    PAIR_UNIVERSES,
)


def _feature_kind(name: str, formula: str) -> tuple[str, str]:
    roles = feature_roles_for_formula(name) | feature_roles_for_formula(formula)
    if "orthogonal_regime" in roles:
        if "funding" in roles:
            return "regime", "funding_regime"
        if "pair_state" in roles:
            return "regime", "pair_regime"
        return "regime", "market_regime"
    if "core_carry" in roles or "carry_term_structure" in roles:
        return "signal", "carry"
    if "spread_or_residual" in roles:
        return "signal", "residual"
    if "trend_or_momentum" in roles:
        return "signal", "trend"
    return "signal", "general"


def _feature_description(name: str, formula: str) -> str:
    lname = name.lower()
    if "relative_carry_z" in lname:
        return "Carry relative to the basket funding level, scaled by funding dispersion."
    if "carry_term_structure" in lname:
        return "Short-horizon carry minus longer carry baseline; positive means carry is strengthening."
    if "carry_decay_ratio" in lname:
        return "Carry term structure normalized by longer carry level; useful for decay detection."
    if "funding_dispersion" in lname:
        return "Cross-sectional or pair funding spread dispersion; high values imply crowding disagreement."
    if "funding_level" in lname or lname.endswith("funding_72h_mean") or lname.endswith("funding_168h_mean"):
        return "Funding level feature; high values indicate richer carry."
    if "co_movement" in lname:
        return "Cross-sectional co-movement state; high values mean assets are moving together more coherently."
    if "breadth" in lname:
        return "Market breadth signal describing how broad participation is across the basket."
    if "trend_strength" in lname:
        return "Return scaled by realized volatility; higher values indicate stronger directional trend."
    if "ema_gap" in lname:
        return "Fast-minus-slow EMA gap; positive means short-term trend is stronger than medium-term trend."
    if "macd" in lname:
        return "EMA-differential trend oscillator related to recent acceleration."
    if "rsi" in lname:
        return "Momentum oscillator centered on overbought/oversold balance."
    if "bollinger" in lname:
        return "Deviation or width relative to a rolling mean and dispersion envelope."
    if "pair_corr" in lname or "correlation" in lname:
        return "Pair co-movement feature measuring rolling return correlation."
    if "pair_beta" in lname or "kalman_beta" in lname:
        return "Hedge-ratio stability feature; useful for residual quality and pair state."
    if "residual" in lname or "log_spread" in lname:
        return "Residual or spread feature measuring relative mispricing between pair legs."
    if "vol" in lname or "volatility" in lname:
        return "Volatility state feature capturing price or spread dispersion."
    if "return" in lname:
        return "Return-based feature describing recent directional movement."
    return f"Feature derived from `{formula}`."


def _feature_interpretation(name: str) -> dict[str, str]:
    lname = name.lower()
    if "carry" in lname or "funding" in lname:
        return {
            "high": "richer carry or stronger funding pressure",
            "low": "cheaper carry or weaker funding pressure",
        }
    if "vol" in lname:
        return {"high": "higher realized volatility", "low": "lower realized volatility"}
    if "co_movement" in lname or "corr" in lname:
        return {"high": "stronger co-movement", "low": "weaker co-movement"}
    if "trend" in lname or "momentum" in lname or "return" in lname:
        return {"high": "stronger recent trend", "low": "weaker recent trend"}
    if "residual" in lname or "spread" in lname:
        return {"high": "wider positive dislocation", "low": "wider negative dislocation"}
    return {"high": "higher feature value", "low": "lower feature value"}


def _feature_common_uses(name: str, kind: str, subkind: str) -> list[str]:
    uses: list[str] = []
    lname = name.lower()
    if kind == "regime":
        uses.append("regime_filtering")
    if subkind == "carry":
        uses.extend(["carry_ranking", "carry_confirmation"])
    if subkind == "trend":
        uses.extend(["trend_following", "breakout_confirmation"])
    if subkind == "residual":
        uses.extend(["mean_reversion", "spread_quality"])
    if "vol" in lname:
        uses.append("risk_suppression")
    if "co_movement" in lname or "corr" in lname:
        uses.append("coherence_filter")
    return list(dict.fromkeys(uses)) or ["general_signal"]


def _feature_anti_patterns(name: str, formula: str) -> list[str]:
    roles = feature_roles_for_formula(name) | feature_roles_for_formula(formula)
    anti: list[str] = []
    if "trend_or_momentum" in roles:
        anti.append("do not stack multiple near-duplicate trend overlays without evidence")
    if "core_carry" in roles:
        anti.append("do not treat carry magnitude alone as sufficient regime protection")
    if "orthogonal_regime" in roles:
        anti.append("do not use as a hard gate without checking expected open fraction")
    if "spread_or_residual" in roles:
        anti.append("do not assume residual quality without checking hedge-ratio stability")
    return anti


def _similar_features(name: str, alias_map: dict[str, str]) -> list[str]:
    lname = name.lower()
    kinds = []
    if "carry" in lname or "funding" in lname:
        kinds = ["carry", "funding"]
    elif "ema" in lname or "macd" in lname:
        kinds = ["ema", "macd", "trend"]
    elif "residual" in lname or "spread" in lname or "kalman" in lname:
        kinds = ["residual", "spread", "kalman", "corr", "beta"]
    elif "co_movement" in lname or "corr" in lname:
        kinds = ["co_movement", "corr", "correlation"]
    elif "vol" in lname:
        kinds = ["vol", "volatility"]
    similar = [
        other
        for other in alias_map
        if other != name and any(token in other.lower() for token in kinds)
    ]
    return sorted(similar)[:5]


def build_feature_catalog(
    *,
    track: str,
    families: list[str],
    root_dir: Path,
) -> list[dict[str, Any]]:
    all_specs = load_feature_spec(root_dir, track=track)
    alias_to_families: dict[str, set[str]] = {}
    alias_to_formula: dict[str, str] = {}
    for family in families:
        family_spec = load_feature_spec(root_dir, track=track, family=family)
        for name, formula in dict(family_spec.get("aliases") or {}).items():
            alias_to_families.setdefault(str(name), set()).add(family)
            alias_to_formula[str(name)] = str(formula)
    catalog: list[dict[str, Any]] = []
    for name in sorted(alias_to_formula):
        formula = alias_to_formula[name]
        kind, subkind = _feature_kind(name, formula)
        catalog.append(
            {
                "name": name,
                "family": sorted(alias_to_families.get(name) or []),
                "kind": kind,
                "subkind": subkind,
                "formula": formula,
                "raw_series": [
                    raw
                    for raw in all_specs.get("raw_series_by_family", {}).get(
                        next(iter(sorted(alias_to_families.get(name) or [])), ""),
                        [],
                    )
                    if raw in formula
                ],
                "operators": [operator for operator in all_specs.get("operators") or [] if f"{operator}(" in formula],
                "description": _feature_description(name, formula),
                "lookback_hint": [token for token in name.replace("-", "_").split("_") if token.isdigit()],
                "interpretation": _feature_interpretation(name),
                "common_uses": _feature_common_uses(name, kind, subkind),
                "similar_features": _similar_features(name, alias_to_formula),
                "anti_patterns": _feature_anti_patterns(name, formula),
                "formula_legal": True,
            }
        )
    return catalog


def render_feature_surface(*, catalog: list[dict[str, Any]]) -> str:
    by_bucket: dict[tuple[str, str], list[str]] = {}
    for row in catalog:
        key = (str(row.get("kind") or "signal"), str(row.get("subkind") or "general"))
        by_bucket.setdefault(key, []).append(str(row.get("name") or ""))
    lines = [
        "# Feature Surface",
        "",
        "This is the high-level semantic view of the feature space. Use feature tools for exact lookup before using or replacing a feature you do not fully understand.",
        "",
    ]
    for (kind, subkind), names in sorted(by_bucket.items()):
        lines.append(f"## {kind} / {subkind}")
        for name in sorted(names)[:8]:
            lines.append(f"- `{name}`")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def render_feature_catalog_md(*, catalog: list[dict[str, Any]]) -> str:
    lines = ["# Feature Catalog", ""]
    for row in catalog:
        lines.extend(
            [
                f"## {row['name']}",
                f"- families: `{row['family']}`",
                f"- kind: `{row['kind']}` / `{row['subkind']}`",
                f"- formula: `{row['formula']}`",
                f"- description: {row['description']}",
                f"- common_uses: `{row['common_uses']}`",
                f"- similar_features: `{row['similar_features']}`",
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def render_family_feature_manifest(
    *,
    family: str,
    catalog: list[dict[str, Any]],
) -> str:
    rows = [row for row in catalog if family in list(row.get("family") or [])]
    lines = [
        f"# Feature Contract: {family}",
        "",
        "Use existing aliases when they already express the intended signal. Only compose a new formula when the exact idea is not already available here.",
        "",
    ]
    for row in rows:
        lines.extend(
            [
                f"## {row['name']}",
                f"- kind: `{row['kind']}` / `{row['subkind']}`",
                f"- formula: `{row['formula']}`",
                f"- description: {row['description']}",
                f"- similar_features: `{row['similar_features']}`",
                f"- anti_patterns: `{row['anti_patterns']}`",
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def compute_spec_fingerprint(root_dir: Path) -> dict[str, Any]:
    tracked_files = [
        root_dir / "mutable" / "family_lab.yaml",
        root_dir / "mutable" / "feature_lab.yaml",
        root_dir / "siglab" / "search" / "mutate.py",
        root_dir / "siglab" / "families.py",
    ]
    hasher = hashlib.sha256()
    for path in tracked_files:
        hasher.update(str(path.relative_to(root_dir)).encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(path.read_bytes())
        hasher.update(b"\0")
    return {
        "fingerprint": hasher.hexdigest(),
        "files": [str(path.relative_to(root_dir)) for path in tracked_files],
    }


def render_runbook() -> str:
    return "\n".join(
        [
            "# Runbook",
            "",
            "This workspace is the planner and writer contract for `trend_signals`.",
            "",
            "Always-loaded files:",
            "- `RUNBOOK.md`",
            "- `TASK.md`",
            "- `WORKSPACE_INDEX.md`",
            "- `current/SESSION_STATE.json`",
            "- `current/frontier_brief.md`",
            "- `current/market_brief.md`",
            "- `current/parent_card.md`",
            "- `current/families_index.md`",
            "- `current/incumbent_spec.yaml`",
            "- `current/family_incumbents.json`",
            "- `current/recent_trials.md`",
            "",
            "Browse deeper only when needed through workspace search/open tools.",
            "",
            "Rules:",
            "- Treat `cards/experiments/*.md` as canonical pre-audit spec cards.",
            "- Treat `cards/reflections/*.md` as compact lesson cards.",
            "- Treat `cards/probes/*.md` as cached evidence, not as default context.",
            "- Use manifests and cookbooks instead of reading `mutate.py` directly.",
            "- Recent trials may include coarse audit outcomes and compact price/carry attribution, but not full audit series.",
            "",
            "Iteration outputs:",
            "- `iterations/<iteration>_<parent_hash>/research_note.md`",
            "- `iterations/<iteration>_<parent_hash>/spec.json`",
            "- stage trace files in the same iteration folder",
        ]
    )


def render_constraints(
    *,
    track: str,
    families: list[str],
    root_dir: Path,
) -> str:
    lines = [
        "# Constraints",
        "",
        "The validator clamps specs after writing. Stay inside these ranges to avoid drift.",
        "",
        "Global directional-perps rules:",
        "- `lookback_days` is clamped to 365 and then fixed to 365 for evaluated specs.",
        "- `rebalance_threshold` is clamped to `[0.0, 0.25]`.",
        "- `max_leverage` is clamped to `[1.0, 3.0]`.",
        "- `min_abs_score` is clamped to `[0.0, 1.5]`.",
        "",
    ]
    for family in families:
        spec = load_family_spec(root_dir, track, family)
        execution_profile = family_execution_profile(spec) or "unknown"
        lines.append(f"## {family}")
        lines.append(f"- Execution profile: `{execution_profile}`")
        if family in PAIR_TRADE_FAMILIES:
            lines.extend(
                [
                    "- Pair universes must contain exactly 2 symbols.",
                    "- `gross_target` is clamped to `[0.5, 1.5]`.",
                    "- `max_gross_target` is clamped to `[gross_target, 1.0]` for unlevered and `[gross_target, 3.0]` for levered.",
                    "- `signal_leverage_scale` is clamped to `[0.25, 3.0]`.",
                    "- `entry_abs_score` is clamped to `[0.0, 1.5]` and becomes `min_abs_score`.",
                    "- `exit_abs_score` is clamped to `[0.0, entry_abs_score]`.",
                    "- `flip_abs_score` is clamped to `[entry_abs_score, 2.5]`.",
                    "- `max_holding_bars` is clamped to `[0, 336]`.",
                    "- `cooldown_bars` is clamped to `[0, 168]`.",
                    "- `trade_style` must be one of `reversion`, `pullback`, `continuation`, `breakout`, or `hybrid`.",
                ]
            )
        elif family in BASKET_NEUTRAL_FAMILIES or family == MULTI_ASSET_CARRY_FAMILY:
            lines.extend(
                [
                    "- `long_count` and `short_count` are clamped to `[1, max_symbols]`.",
                    "- `gross_target` is clamped to `[0.4, 2.5]`.",
                    "- Basket universes should use 4 symbols unless the family manifest says otherwise.",
                ]
            )
        else:
            lines.extend(
                [
                    "- Directional families should keep `long_count` and `short_count` inside `[0, max_symbols]`.",
                    "- Avoid unsupported keys; the writer must emit only schema-approved fields.",
                ]
            )
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def render_regime_catalog() -> str:
    return "\n".join(
        [
            "# Regime Catalog",
            "",
            "These are the regime labels used downstream in diagnostics and reflections. Prefer these exact labels over fuzzy prose.",
            "",
            "## Market",
            "- `market_uptrend` / `market_downtrend`",
            "- `high_volatility` / `low_volatility`",
            "- `high_funding` / `low_funding`",
            "- `funding_dispersed` / `funding_compressed`",
            "- `broad_participation` / `weak_participation`",
            "- `high_co_movement` / `low_co_movement`",
            "- `concentrated` / `diversified`",
            "",
            "## Pair and Basket Enrichments",
            "- `high_correlation` / `low_correlation`",
            "- `high_volatility` / `low_volatility` for pair spread volatility",
            "- `asset_1_leading` / `asset_2_leading`",
            "- `market_neutral`, `long_asset_1_short_asset_2`, `short_asset_1_long_asset_2` in direction summaries",
            "",
            "Use these labels when naming failure modes, proposing suppressions, and reading lesson cards.",
        ]
    ).strip() + "\n"


def render_policy_surface(*, families: list[str]) -> str:
    lines = [
        "# Policy Surface",
        "",
        "This file describes which knobs are meaningful, which are swept locally, and which are mostly fixed by validation/evaluation behavior.",
        "",
        "## Shared guidance",
        "- `track`, `family`, `features`, `universe`, `risk`, and `regime_gates` are intentful and should not rely on the evaluator to rescue them.",
        "- The writer should choose coherent starting values, not knife-edge thresholds.",
        "- If a gate is central to the thesis, encode it explicitly; additive features do not create a hard gate.",
        "",
    ]
    for family in families:
        lines.extend([f"## {family}"])
        if family in PAIR_TRADE_FAMILIES:
            lines.extend(
                [
                    "- Locally swept by evaluator: `entry_abs_score`, `exit_abs_score`, `flip_abs_score`, `max_holding_bars`, `cooldown_bars`.",
                    "- These are starting policy values, not necessarily the final frozen policy.",
                    "- `regime_gates` and `features` are not locally invented by the evaluator; they must already express the thesis correctly.",
                    "- Material intent changes usually mean: different feature set, different gate expression/min/max, different universe, or different trade style.",
                ]
            )
        elif family == MULTI_ASSET_CARRY_FAMILY:
            lines.extend(
                [
                    "- Cross-sectional carry is a ranked long/short execution family; carry features often rank the basket but realized returns may be price-led.",
                    "- Primary structural levers are feature mix, long/short counts, gating, universe, and concentration.",
                    "- Cross-sectional `trade_style` is guidance only here; do not treat it like pair-trade `trade_style`.",
                    "- Material intent changes usually mean: different ranking neighborhood, different book structure, or different regime suppression.",
                ]
            )
        elif family in BASKET_NEUTRAL_FAMILIES:
            lines.extend(
                [
                    "- No local policy sweep is applied in the same way as pair families.",
                    "- `long_count`, `short_count`, `gross_target`, and `min_abs_score` are written intent, subject only to validator clamping.",
                    "- Regime gates must be valid on write; the evaluator will measure them, not infer them.",
                    "- Material intent changes usually mean: different feature neighborhood, different universe, or different regime suppression.",
                ]
            )
        else:
            lines.extend(
                [
                    "- Directional count and exposure knobs are validator-clamped but not pair-swept.",
                    "- Relative-vs-absolute signal choice is a thesis change, not a threshold tweak.",
                ]
            )
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def render_family_manifest(
    *,
    track: str,
    family: str,
    root_dir: Path,
) -> str:
    spec = load_family_spec(root_dir, track, family)
    feature_spec = load_feature_spec(root_dir, track=track, family=family)
    execution_profile = family_execution_profile(spec) or "unknown"
    defaults = dict(spec.get("defaults") or {})
    aliases = sorted((feature_spec.get("aliases") or {}).keys())
    alias_definitions = dict(feature_spec.get("aliases") or {})
    raw_series = sorted(feature_spec.get("raw_series") or [])
    operators = sorted(feature_spec.get("operators") or [])
    prompt_module = str(dict(spec.get("capabilities") or {}).get("prompt_module") or "unknown")
    preferred_universes = PAIR_UNIVERSES if family in PAIR_TRADE_FAMILIES else CROSS_SECTIONAL_UNIVERSES
    common_failures = [
        "over-gating that kills activity",
        "reusing the same feature neighborhood without adding orthogonal state",
        "depending on the sweep to fix a weak thesis",
    ]
    if family == MULTI_ASSET_CARRY_FAMILY:
        common_failures.extend(
            [
                "carry decay or low-co-movement regimes are not handled",
                "assuming a carry-labelled winner is carry-dominant when the realized PnL is actually price-led",
            ]
        )
    lines = [
        f"# {family}",
        "",
        f"- Execution profile: `{execution_profile}`",
        f"- Prompt module: `{prompt_module}`",
        f"- Defaults: `{json.dumps(defaults, ensure_ascii=True, sort_keys=True)}`",
        f"- Preferred universes: `{preferred_universes}`",
        "",
        "## Family semantics",
    ]
    if family == MULTI_ASSET_CARRY_FAMILY:
        lines.extend(
            [
                "- `perp_multi_asset_carry` is carry in spirit, but operationally it is a cross-sectional ranked long/short book.",
                "- The family requires both long and short sides when active because it uses the `ranked_carry` execution profile.",
                "- Carry features often rank the basket, but realized PnL may come primarily from directional price movement in the selected book.",
                "- Price, relative-momentum, and volatility features are valid ranking inputs here; this family is not limited to funding-only features.",
                "",
            ]
        )
    elif family == "perp_multi_asset_decision":
        lines.extend(
            [
                "- `perp_multi_asset_decision` is the directional ranked family.",
                "- It uses the same cross-sectional feature surface as carry families, but the `ranked_directional` execution profile defaults toward positive long selection instead of requiring both sides.",
                "- The distinction versus `perp_multi_asset_carry` is execution behavior and book construction, not ownership of price features.",
                "",
            ]
        )
    else:
        lines.append("- Read the execution profile and defaults together; they determine how the ranked score becomes a book.")
        lines.append("")
    lines.extend(
        [
        "## Allowed aliases",
        *[f"- `{alias}`" for alias in aliases],
        "",
        "## Alias definitions",
        *[f"- `{alias}` = `{alias_definitions[alias]}`" for alias in aliases],
        "",
        "## Raw series",
        *[f"- `{series}`" for series in raw_series],
        "",
        "## Formula operators",
        *[f"- `{operator}`" for operator in operators],
        "",
        "Novel feature formulas are allowed when they are composed only from these operators plus the listed aliases and raw series.",
        "Boolean operators such as `gt`, `ge`, `lt`, `le`, `and`, `or`, `not`, and `where` are especially useful inside `regime_gates.entry` expressions.",
        "",
        "## Tunable knobs",
        *[f"- `{key}` default `{value}`" for key, value in sorted(defaults.items())],
        "",
        "## Valid trade styles",
        "- `reversion`, `pullback`, `continuation`, `breakout`, `hybrid` for pair families",
        "- cross-sectional families may omit `trade_style`; execution profile, feature mix, and book structure are the primary levers there",
        "",
        "## Common failure modes",
        *[f"- {failure}" for failure in common_failures],
        "",
        "## Spec schema slice",
        "Required top-level keys: `track`, `family`, `hypothesis`, `neutrality_basis`, `features`, `universe`, `risk`, `regime_gates`, `params`.",
        "Do not add unsupported keys.",
        "",
        "## Regime gate contract",
        "- `regime_gates.entry` is `[]` or a list of strings / dicts.",
        "- Valid string gate: `ge(pair_corr_72h,0.9)`",
        "- Valid bounded gate dict: `{\"expression\":\"market_volatility_168h\",\"max\":0.0085}`",
        "- Valid bounded gate dict: `{\"expression\":\"funding_dispersion_72h\",\"min\":0.00001}`",
        "- Invalid gate shapes include `op`, `condition`, `threshold`, `active`, or dicts without `expression`.",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def render_family_contract(
    *,
    track: str,
    family: str,
    root_dir: Path,
) -> dict[str, Any]:
    spec = load_family_spec(root_dir, track, family)
    feature_spec = load_feature_spec(root_dir, track=track, family=family)
    execution_profile = family_execution_profile(spec) or "unknown"
    defaults = dict(spec.get("defaults") or {})
    return {
        "track": track,
        "family": family,
        "execution_profile": execution_profile,
        "prompt_module": str(dict(spec.get("capabilities") or {}).get("prompt_module") or "unknown"),
        "top_level_keys": [
            "track",
            "family",
            "hypothesis",
            "neutrality_basis",
            "features",
            "universe",
            "risk",
            "regime_gates",
            "params",
        ],
        "allowed_aliases": sorted((feature_spec.get("aliases") or {}).keys()),
        "alias_definitions": dict(feature_spec.get("aliases") or {}),
        "raw_series": sorted(feature_spec.get("raw_series") or []),
        "operators": sorted(feature_spec.get("operators") or []),
        "defaults": defaults,
        "preferred_universes": PAIR_UNIVERSES if family in PAIR_TRADE_FAMILIES else CROSS_SECTIONAL_UNIVERSES,
        "regime_gate_contract": {
            "entry_list": "[] or list[str|dict]",
            "valid_string_example": "ge(pair_corr_72h,0.9)",
            "valid_dict_examples": [
                {"expression": "market_volatility_168h", "max": 0.0085},
                {"expression": "funding_dispersion_72h", "min": 0.00001},
            ],
            "invalid_keys": ["op", "condition", "threshold", "active"],
            "exit_on_break_default": True,
        },
        "policy_surface": {
            "pair_local_sweep_fields": (
                [
                    "entry_abs_score",
                    "exit_abs_score",
                    "flip_abs_score",
                    "max_holding_bars",
                    "cooldown_bars",
                ]
                if family in PAIR_TRADE_FAMILIES
                else []
            ),
            "notes": (
                "pair thresholds and time controls are locally swept; exact written values are starting policy only"
                if family in PAIR_TRADE_FAMILIES
                else "written params are mostly evaluated as written, subject to validator clamping"
            ),
        },
    }


def render_families_index(
    *,
    track: str,
    families: list[str],
    root_dir: Path,
    rows: list[dict[str, Any]],
) -> str:
    by_family: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_family.setdefault(str(row.get("family") or ""), []).append(row)
    lines = [
        "# Families Index",
        "",
        "Current allowed families, execution profiles, and hot/cold status.",
        "",
    ]
    for family in families:
        spec = load_family_spec(root_dir, track, family)
        execution_profile = family_execution_profile(spec) or "unknown"
        family_rows = by_family.get(family, [])
        passed = sum(1 for row in family_rows if bool(row.get("passed")))
        positive = sum(
            1
            for row in family_rows
            if float((row.get("summary") or {}).get("pre_audit_canonical_total_return") or 0.0) > 0.0
        )
        status = "hot" if positive > 0 else "cold"
        lines.extend(
            [
                f"## {family}",
                f"- Execution profile: `{execution_profile}`",
                f"- Attempts: `{len(family_rows)}`",
                f"- Passed: `{passed}`",
                f"- Positive pre-audit rows: `{positive}`",
                f"- Status: `{status}`",
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def render_cookbook_pages(track: str) -> dict[str, str]:
    if track != "trend_signals":
        return {}
    return {
        "pair_trade_patterns.md": _render_pair_cookbook(),
        "carry_patterns.md": _render_carry_cookbook(),
        "directional_patterns.md": _render_directional_cookbook(),
        "basket_neutral_patterns.md": _render_basket_cookbook(),
    }


def _render_pair_cookbook() -> str:
    sections = [
        ("MeanReversionCore", PAIR_MEAN_REVERSION_FEATURES, "Use when spread dislocation is the main thesis."),
        ("QualityMomentum", PAIR_QUALITY_MOMENTUM_FEATURES, "Use when relative price leadership plus carry alignment matters."),
        ("CompressionReversion", PAIR_COMPRESSION_REVERSION_FEATURES, "Use when the spread should mean-revert after compressed or tight states."),
        ("DynamicResidual", PAIR_DYNAMIC_RESIDUAL_FEATURES, "Use when hedge-ratio drift or Kalman residual behavior is the missing state variable."),
        ("ReversionSpeed", PAIR_MEAN_REVERSION_SPEED_FEATURES, "Use when half-life, autocorrelation, or Hurst is needed to decide whether reversion is timely."),
        ("CarryRegime", PAIR_CARRY_REGIME_FEATURES, "Use when carry and correlation are filtering the spread edge rather than defining it."),
    ]
    lines = ["# Pair Trade Patterns", ""]
    for name, formulas, guidance in sections:
        lines.extend(
            [
                f"## {name}",
                guidance,
                *[f"- `{formula}`" for formula in formulas],
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def _render_carry_cookbook() -> str:
    return "\n".join(
        [
            "# Carry Patterns",
            "",
            "Use these when `perp_multi_asset_carry` should stay carry in spirit but the ranking engine may need price, momentum, or volatility context to realize the edge.",
            "",
            "## CoreCarryRanking",
            "- `funding_72h_mean`",
            "- `funding_168h_mean`",
            "- `funding_carry_to_vol`",
            "",
            "## Carry Plus Relative Momentum",
            "- `relative_momentum_24h`",
            "- `relative_momentum_72h`",
            "- `breadth_adjusted_relative_momentum_24h`",
            "",
            "## Carry Plus Trend Quality",
            "- `trend_strength_72h`",
            "- `ema_gap_12_26`",
            "- `price_return_vol_adj_24h`",
            "",
            "## RelativeCarry",
            "- `relative_carry_z_72h`",
            "- `relative_carry_168h`",
            "",
            "## CarryTermStructure",
            "- `carry_term_structure_24_168`",
            "- `carry_decay_ratio_24_168`",
            "",
            "## Carry Plus Light Price Context",
            "- `price_return_24h`",
            "- `price_return_72h`",
        ]
    ).strip() + "\n"


def _render_directional_cookbook() -> str:
    return "\n".join(
        [
            "# Directional Patterns",
            "",
            "Directional families should favor relative strength and market-state overlays instead of broad beta.",
            "",
            "## RelativeStrength",
            "- `relative_momentum_24h`",
            "- `breadth_adjusted_relative_momentum_24h`",
            "",
            "## TrendQuality",
            "- `ema_gap_12_26`",
            "- `macd_hist_12_26_9`",
            "- `trend_strength_72h`",
            "",
            "## CarryOverlay",
            "- `relative_carry_z_72h`",
            "- `funding_carry_to_vol`",
            "- `carry_term_structure_24_168`",
        ]
    ).strip() + "\n"


def _render_basket_cookbook() -> str:
    return "\n".join(
        [
            "# Basket Neutral Patterns",
            "",
            "Use these when the edge should come from long-basket versus short-basket ranking rather than outright market direction.",
            "",
            "## RelativeCarryImbalance",
            "- `relative_carry_z_72h`",
            "- `funding_carry_to_vol`",
            "- `carry_term_structure_24_168`",
            "",
            "## RelativeMomentum",
            "- `relative_momentum_24h`",
            "- `breadth_adjusted_relative_momentum_24h`",
            "",
            "## CrossSectionalHybrid",
            "- mix one carry signal with one relative-momentum signal and one market-state suppressor",
        ]
    ).strip() + "\n"


