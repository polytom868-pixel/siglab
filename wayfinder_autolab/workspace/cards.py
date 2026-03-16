from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import yaml

from wayfinder_autolab.orchestration.trials import summarize_generalization


class _PlainFloatDumper(yaml.SafeDumper):
    pass


def _plain_float_representer(dumper: yaml.SafeDumper, value: float) -> yaml.nodes.ScalarNode:
    if not math.isfinite(value):
        text = repr(value)
    else:
        text = format(value, ".15f").rstrip("0").rstrip(".")
        if "." not in text and "e" not in text.lower():
            text = f"{text}.0"
        if text in {"-0", "-0.0", ""}:
            text = "0.0"
    return dumper.represent_scalar("tag:yaml.org,2002:float", text)


_PlainFloatDumper.add_representer(float, _plain_float_representer)


def strip_audit_fields(payload: Any) -> Any:
    if isinstance(payload, dict):
        cleaned: dict[str, Any] = {}
        for key, value in payload.items():
            key_str = str(key)
            if key_str.startswith("audit_"):
                continue
            cleaned[key_str] = strip_audit_fields(value)
        return cleaned
    if isinstance(payload, list):
        return [strip_audit_fields(item) for item in payload]
    return payload


def dump_frontmatter(frontmatter: dict[str, Any], body: str) -> str:
    serialized = yaml.dump(
        frontmatter,
        sort_keys=False,
        allow_unicode=False,
        Dumper=_PlainFloatDumper,
    ).rstrip()
    body_text = body.strip() + "\n" if body.strip() else ""
    return f"---\n{serialized}\n---\n\n{body_text}"


def dump_yaml_block(payload: Any) -> str:
    return yaml.dump(
        payload,
        sort_keys=False,
        allow_unicode=False,
        Dumper=_PlainFloatDumper,
    ).rstrip()


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    stripped = text.lstrip()
    if not stripped.startswith("---\n"):
        return {}, text
    _, remainder = stripped.split("---\n", 1)
    frontmatter_blob, separator, body = remainder.partition("\n---\n")
    if not separator:
        return {}, text
    parsed = yaml.safe_load(frontmatter_blob) or {}
    return dict(parsed), body.lstrip()


def read_frontmatter(path: Path) -> tuple[dict[str, Any], str]:
    return parse_frontmatter(path.read_text())


def write_markdown(path: Path, *, frontmatter: dict[str, Any], body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_frontmatter(frontmatter, body))


def relative_path(path: Path, root: Path) -> str:
    return str(path.resolve().relative_to(root.resolve()))


def render_experiment_card(
    *,
    row: dict[str, Any],
    artifact: dict[str, Any] | None,
) -> tuple[str, dict[str, Any]]:
    summary = strip_audit_fields(dict(row.get("summary") or {}))
    candidate = dict(row.get("candidate") or {})
    trial = dict(dict(row.get("research_summary") or {}).get("trial") or {})
    generalization = summarize_generalization(
        dict(row.get("summary") or {}),
        stability_pack=dict(trial.get("stability_pack") or {}),
    )
    canonical_run = strip_audit_fields(dict((artifact or {}).get("canonical_run") or {}))
    context_pack = dict(canonical_run.get("pre_audit_context_pack") or {})
    drawdown_pack = dict(canonical_run.get("pre_audit_drawdown_pack") or {})
    hypothesis = str(candidate.get("hypothesis") or "").strip()
    family = str(row.get("family") or "")
    features = [str(feature) for feature in candidate.get("features") or []]
    trade_style = str(dict(candidate.get("params") or {}).get("trade_style") or "").strip()
    gate_tags = list(summary.get("gate_bottleneck_tags") or [])
    gate_reasons = list(summary.get("gate_reasons") or [])
    frontmatter = {
        "kind": "experiment",
        "candidate_hash": row.get("candidate_hash"),
        "parent_hash": row.get("parent_hash"),
        "family": family,
        "passed": bool(row.get("passed")),
        "promoted": bool(row.get("promoted")),
        "outcome": "passed" if bool(row.get("passed")) else "failed",
        "trade_style": trade_style or "unspecified",
        "pre_audit_canonical_total_return": summary.get("pre_audit_canonical_total_return"),
        "validation_total_return": summary.get("validation_total_return"),
        "median_total_return": summary.get("median_total_return"),
        "active_bar_fraction": summary.get("active_bar_fraction"),
        "return_driver": trial.get("return_driver"),
        "exposure_profile": trial.get("exposure_profile"),
        "price_contribution": trial.get("price_contribution"),
        "carry_contribution": trial.get("carry_contribution"),
        "tx_cost_contribution": trial.get("tx_cost_contribution"),
        "fragility_penalty": trial.get("fragility_penalty", generalization.get("fragility_penalty")),
        "promotion_score": trial.get("promotion_score", generalization.get("promotion_score")),
        "audit_alignment": trial.get("audit_alignment", generalization.get("audit_alignment")),
        "fragility_label": trial.get("fragility_label", generalization.get("fragility_label")),
        "stability_status": trial.get(
            "stability_status",
            dict(generalization.get("stability_pack") or {}).get("status"),
        ),
        "stability_pass_fraction": trial.get(
            "stability_pass_fraction",
            dict(generalization.get("stability_pack") or {}).get("passed_fraction"),
        ),
        "motif_audit_streak": trial.get("motif_audit_streak"),
        "tracking_tags": sorted(
            {
                family,
                *(tag for tag in gate_tags if isinstance(tag, str)),
                *(reason for reason in gate_reasons if isinstance(reason, str)),
                *(feature for feature in features[:4]),
            }
        ),
        "created_at": row.get("created_at"),
    }

    regime_excerpt = json.dumps(
        dict(context_pack.get("trade_regime_pack") or {}),
        indent=2,
        ensure_ascii=True,
        default=str,
    )[:2200]
    gate_excerpt = json.dumps(
        dict(context_pack.get("gate_diagnostics") or {}),
        indent=2,
        ensure_ascii=True,
        default=str,
    )[:1800]
    drawdown_excerpt = json.dumps(
        drawdown_pack,
        indent=2,
        ensure_ascii=True,
        default=str,
    )[:1800]
    policy_comparison = strip_audit_fields(
        {
            "declared_evaluation": summary.get("policy_sweep_declared_evaluation"),
            "frozen_evaluation": summary.get("policy_sweep_frozen_evaluation"),
            "declared_better_metrics": summary.get("policy_sweep_declared_better_metrics"),
            "frozen_better_metrics": summary.get("policy_sweep_frozen_better_metrics"),
            "equal_metrics": summary.get("policy_sweep_equal_metrics"),
            "realized_winner": summary.get("policy_sweep_realized_winner"),
        }
    )
    policy_comparison_excerpt = json.dumps(
        policy_comparison,
        indent=2,
        ensure_ascii=True,
        default=str,
    )[:1800]
    body = "\n".join(
        [
            f"# Experiment {row.get('candidate_hash')}",
            "",
            f"Family: `{family}`",
            f"Hypothesis: {hypothesis or 'n/a'}",
            f"Trade style: `{trade_style or 'unspecified'}`",
            f"Universe: `{json.dumps(candidate.get('universe') or {}, ensure_ascii=True, sort_keys=True)}`",
            "",
            "## Summary",
            f"- `median_total_return`: {summary.get('median_total_return')}",
            f"- `validation_total_return`: {summary.get('validation_total_return')}",
            f"- `pre_audit_canonical_total_return`: {summary.get('pre_audit_canonical_total_return')}",
            f"- `pre_audit_canonical_max_drawdown`: {summary.get('pre_audit_canonical_max_drawdown')}",
            f"- `active_bar_fraction`: {summary.get('active_bar_fraction')}",
            f"- `return_driver`: {trial.get('return_driver') or 'n/a'}",
            f"- `exposure_profile`: {trial.get('exposure_profile') or 'n/a'}",
            f"- `price_contribution`: {trial.get('price_contribution')}",
            f"- `carry_contribution`: {trial.get('carry_contribution')}",
            f"- `tx_cost_contribution`: {trial.get('tx_cost_contribution')}",
            f"- `fragility_penalty`: {trial.get('fragility_penalty', generalization.get('fragility_penalty'))}",
            f"- `promotion_score`: {trial.get('promotion_score', generalization.get('promotion_score'))}",
            f"- `audit_alignment`: {trial.get('audit_alignment') or generalization.get('audit_alignment') or 'n/a'}",
            f"- `fragility_label`: {trial.get('fragility_label') or generalization.get('fragility_label') or 'n/a'}",
            f"- `stability_status`: {trial.get('stability_status') or dict(generalization.get('stability_pack') or {}).get('status') or 'n/a'}",
            f"- `stability_pass_fraction`: {trial.get('stability_pass_fraction', dict(generalization.get('stability_pack') or {}).get('passed_fraction'))}",
            f"- `motif_audit_streak`: {trial.get('motif_audit_streak')}",
            f"- `best_regime_context`: {trial.get('best_regime_context') or 'n/a'}",
            f"- `worst_regime_context`: {trial.get('worst_regime_context') or 'n/a'}",
            f"- `gate_bottleneck_tags`: {gate_tags}",
            f"- `gate_reasons`: {gate_reasons}",
            "",
            "## Features",
            *[f"- `{feature}`" for feature in features],
            "",
            "## Regime Excerpt",
            "```json",
            regime_excerpt,
            "```",
            "",
            "## Gate Excerpt",
            "```json",
            gate_excerpt,
            "```",
            "",
            "## Policy Sweep Comparison",
            "```json",
            policy_comparison_excerpt,
            "```",
            "",
            "## Drawdown Excerpt",
            "```json",
            drawdown_excerpt,
            "```",
        ]
    ).strip()
    return body, frontmatter


def render_experiment_view_card(
    *,
    row: dict[str, Any],
    canonical_card_ref: str,
    kind: str,
) -> tuple[str, dict[str, Any]]:
    family = str(row.get("family") or "")
    summary = strip_audit_fields(dict(row.get("summary") or {}))
    frontmatter = {
        "kind": kind,
        "candidate_hash": row.get("candidate_hash"),
        "family": family,
        "outcome": kind,
        "tracking_tags": [family, kind],
        "created_at": row.get("created_at"),
    }
    body = "\n".join(
        [
            f"# {kind.title()} {row.get('candidate_hash')}",
            "",
            f"Canonical card: `{canonical_card_ref}`",
            f"Family: `{family}`",
            f"Pre-audit canonical return: {summary.get('pre_audit_canonical_total_return')}",
            f"Validation return: {summary.get('validation_total_return')}",
            f"Median return: {summary.get('median_total_return')}",
        ]
    )
    return body, frontmatter


def render_probe_card(
    *,
    probe_key: str,
    probe_type: str,
    family: str,
    universe: list[str],
    bundle_id: str | None,
    arguments: dict[str, Any],
    result: dict[str, Any],
    tracking_tags: list[str] | None = None,
) -> tuple[str, dict[str, Any]]:
    frontmatter = {
        "kind": "probe",
        "probe_key": probe_key,
        "probe_type": probe_type,
        "family": family,
        "universe": list(universe),
        "bundle_id": bundle_id,
        "tracking_tags": sorted(set(tracking_tags or [family, probe_type])),
    }
    body = "\n".join(
        [
            f"# Probe {probe_key}",
            "",
            f"Type: `{probe_type}`",
            f"Family: `{family}`",
            f"Universe: `{universe}`",
            f"Bundle: `{bundle_id or 'unknown'}`",
            "",
            "## Arguments",
            "```json",
            json.dumps(arguments, indent=2, ensure_ascii=True, default=str),
            "```",
            "",
            "## Result",
            "```json",
            json.dumps(strip_audit_fields(result), indent=2, ensure_ascii=True, default=str)[:6000],
            "```",
        ]
    )
    return body, frontmatter


def extract_markdown_section(text: str, heading: str, *, max_chars: int | None = None) -> str:
    target = heading.strip().lower().lstrip("#").strip()
    if not target:
        return text[: max_chars or len(text)]
    lines = text.splitlines()
    start_index: int | None = None
    start_level = 1
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("#"):
            continue
        level = len(stripped) - len(stripped.lstrip("#"))
        title = stripped[level:].strip().lower()
        if title == target:
            start_index = index
            start_level = level
            break
    if start_index is None:
        return text[: max_chars or len(text)]
    end_index = len(lines)
    for index in range(start_index + 1, len(lines)):
        stripped = lines[index].strip()
        if not stripped.startswith("#"):
            continue
        level = len(stripped) - len(stripped.lstrip("#"))
        if level <= start_level:
            end_index = index
            break
    section = "\n".join(lines[start_index:end_index]).strip()
    if max_chars is not None:
        return section[:max_chars]
    return section
