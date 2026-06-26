"""Read-only signal narrative layer."""

from __future__ import annotations

from typing import Any

from siglab.evaluation.backtest import _cagr_safe


def _fmt(value: float, precision: int = 4) -> str:
    if value is None:
        return "N/A"
    if not isinstance(value, (int, float)):
        return str(value)
    import math

    if not math.isfinite(value):
        return "N/A"
    if abs(value) < 1e-12:
        return "0.0"
    magnitude = math.floor(math.log10(abs(value))) if abs(value) > 0 else 0
    if magnitude <= precision + 4:
        return f"{value:.{precision}f}" if abs(value) < 1 else f"{value:.{precision}f}"
    return f"{value:.{precision}e}"


def _narrative_header(run_meta: dict) -> str:
    liquidated = bool(run_meta.get("liquidated", False))
    liq_tag = " [LIQUIDATED]" if liquidated else ""
    leverage = run_meta.get("leverage", 1.0)
    trade_count = run_meta.get("trade_count", 0)
    parts = [
        "=== Signal Evaluation Narrative ===",
        f"Leverage: {_fmt(leverage)}x | Trades: {trade_count}{liq_tag}",
    ]
    return "\n".join(parts)


def _narrative_evidence_sources(evidence: dict) -> str:
    windows = list(evidence.get("evaluation_windows") or [])
    if not windows:
        return "Evidence sources: none recorded."
    roles: dict[str, int] = {}
    for w in windows:
        role = str(w.get("role", "unknown"))
        roles[role] = roles.get(role, 0) + 1
    visual_split = dict(evidence.get("visual_split") or {})
    note = str(visual_split.get("note", "")) or "No split note available."
    src = "Evidence sources:\n"
    for role, count in sorted(roles.items()):
        src += f"  - {role}: {count} window(s)\n"
    src += f"  Split strategy: {note}"
    return src


def _narrative_performance_summary(stats: dict) -> str:
    equity_payload = stats.get("equity_curve") or {}
    equity_values = list(equity_payload.get("values") or [])
    drawdown_payload = stats.get("drawdown_curve") or {}
    drawdown_values = list(drawdown_payload.get("values") or [])
    if len(equity_values) >= 2:
        begin = float(equity_values[0])
        end = float(equity_values[-1])
        total_ret = end / begin - 1.0 if begin > 0 else None
        periods = len(equity_values)
        if total_ret is not None and total_ret > -1.0 and (begin > 0):
            cagr = _cagr_safe(begin, end, periods)
        else:
            cagr = None
    else:
        total_ret = None
        cagr = None
    max_dd = min(drawdown_values) if drawdown_values else None
    calmar = (
        cagr / abs(max_dd)
        if cagr is not None and max_dd is not None and (max_dd < 0)
        else None
    )
    liquidated = bool(stats.get("liquidated", False))
    lines = [
        "=== Performance Summary ===",
        f"Total Return: {_fmt(total_ret)}"
        if total_ret is not None
        else "Total Return: N/A",
        f"CAGR: {_fmt(cagr)}" if cagr is not None else "CAGR: N/A",
        f"Max Drawdown: {_fmt(max_dd)}" if max_dd is not None else "Max Drawdown: N/A",
        f"Calmar Ratio: {_fmt(calmar)}" if calmar is not None else "Calmar Ratio: N/A",
    ]
    if liquidated:
        lines.append("Status: LIQUIDATED")
    return "\n".join(lines)


def _narrative_feature_decomposition(features: dict) -> str:
    drawdown_pack = features.get("pre_audit_drawdown_pack") or {}
    contributors = list(drawdown_pack.get("top_feature_contributors") or [])
    signal_story = dict(drawdown_pack.get("signal_story") or {})
    lines = ["=== Feature Decomposition ==="]
    if not contributors:
        lines.append("No feature contributor data available.")
        return "\n".join(lines)
    window_median_score = signal_story.get("window_median_score")
    trough_score = signal_story.get("trough_score")
    aligned_frac = signal_story.get("aligned_with_position_fraction")
    if window_median_score is not None:
        lines.append(f"Median score in window: {_fmt(window_median_score)}")
    if trough_score is not None:
        lines.append(f"Score at trough: {_fmt(trough_score)}")
    if aligned_frac is not None:
        lines.append(f"Score aligned with position: {_fmt(aligned_frac * 100.0)}%")
    lines.append("Top contributing features (window median component):")
    for i, contrib in enumerate(contributors[:5], 1):
        feat = str(contrib.get("feature", "?"))
        median_val = contrib.get("window_median_component")
        trough_val = contrib.get("trough_component")
        aligned = contrib.get("aligned_with_position_fraction")
        feat_line = f"  {i}. {feat}"
        if median_val is not None:
            feat_line += f" | window median: {_fmt(median_val)}"
        if trough_val is not None:
            feat_line += f" | at trough: {_fmt(trough_val)}"
        if aligned is not None:
            feat_line += f" | aligned: {_fmt(aligned * 100.0)}%"
        lines.append(feat_line)
    return "\n".join(lines)


def _narrative_drawdown_analysis(drawdown: dict) -> str:
    drawdown_pack = drawdown.get("pre_audit_drawdown_pack") or {}
    context_pack = dict(drawdown.get("pre_audit_context_pack") or {})
    equity_shift = dict(context_pack.get("equity_shift_pack") or {})
    max_dd = drawdown_pack.get("drawdown")
    dominant_dir = drawdown_pack.get("dominant_position_direction")
    bars = drawdown_pack.get("bars", 0)
    long_frac = drawdown_pack.get("long_bar_fraction")
    short_frac = drawdown_pack.get("short_bar_fraction")
    flat_frac = drawdown_pack.get("flat_bar_fraction")
    lines = [
        "=== Drawdown Analysis ===",
        f"Max drawdown: {_fmt(max_dd)} over {int(bars)} bar(s)"
        if max_dd is not None
        else "Max drawdown: N/A",
        f"Dominant direction: {dominant_dir}" if dominant_dir else "",
    ]
    if long_frac is not None and short_frac is not None:
        lines.append(
            f"Position composition: {_fmt(long_frac * 100.0)}% long / {_fmt(short_frac * 100.0)}% short / {_fmt((flat_frac or 0.0) * 100.0)}% flat",
        )
    peak_equity = equity_shift.get("peak_equity")
    if peak_equity is not None:
        lines.append(f"Peak equity before drawdown: {_fmt(peak_equity)}")
    pre_peak = dict(equity_shift.get("pre_peak") or {})
    if pre_peak.get("trade_count", 0) > 0:
        lines.append(
            f"Pre-drawdown: {pre_peak['trade_count']} trades, win rate {_fmt(pre_peak.get('win_rate', 0.0) * 100.0)}%, avg return {_fmt(pre_peak.get('avg_return', 0.0))}",
        )
    return "\n".join(line for line in lines if line)


def _narrative_exemplar_trades(trades: dict) -> str:
    if not trades:
        return "=== Exemplar Trades ===\nNo exemplar trade data available."
    winners = list(trades.get("winners") or [])
    losers = list(trades.get("losers") or [])
    lines = ["=== Exemplar Trades ==="]
    if winners:
        lines.append("Best trades:")
        for i, trade in enumerate(winners[:3], 1):
            ret = trade.get("total_return")
            direction = trade.get("direction", "?")
            bars = trade.get("bars")
            entry_score = trade.get("entry_score")
            features = list(trade.get("entry_feature_contributors") or [])
            line = (
                f"  {i}. {direction} | return: {_fmt(ret)} | bars: {_fmt(bars)}"
                if bars is not None
                else f"  {i}. {direction} | return: {_fmt(ret)}"
            )
            if entry_score is not None:
                line += f" | entry score: {_fmt(entry_score)}"
            if features:
                top_feat = features[0].get("feature", "?")
                line += f" | top feature: {top_feat}"
            lines.append(line)
    else:
        lines.append("No winning trades identified.")
    if losers:
        lines.append("Worst trades:")
        for i, trade in enumerate(losers[:3], 1):
            ret = trade.get("total_return")
            direction = trade.get("direction", "?")
            bars = trade.get("bars")
            entry_score = trade.get("entry_score")
            features = list(trade.get("entry_feature_contributors") or [])
            line = (
                f"  {i}. {direction} | return: {_fmt(ret)} | bars: {_fmt(bars)}"
                if bars is not None
                else f"  {i}. {direction} | return: {_fmt(ret)}"
            )
            if entry_score is not None:
                line += f" | entry score: {_fmt(entry_score)}"
            if features:
                top_feat = features[0].get("feature", "?")
                line += f" | top feature: {top_feat}"
            lines.append(line)
    else:
        lines.append("No losing trades identified.")
    trade_count = len(winners) + len(losers)
    lines.append(f"Total exemplar trades shown: {trade_count}")
    return "\n".join(lines)


def _narrative_regime_context(regime: dict) -> str:
    context_pack = dict(regime.get("pre_audit_context_pack") or {})
    regime_pack = dict(context_pack.get("trade_regime_pack") or {})
    if not regime_pack:
        return "=== Regime Context ===\nNo regime data available."
    lines = ["=== Regime Context ==="]
    regime_order = [
        ("market_trend", "Market Trend"),
        ("market_volatility", "Volatility"),
        ("funding_level", "Funding Level"),
        ("funding_dispersion", "Funding Dispersion"),
        ("breadth", "Breadth"),
        ("co_movement", "Co-movement"),
        ("concentration", "Concentration"),
        ("position_structure", "Position Structure"),
        ("pair_volatility", "Pair Volatility"),
        ("pair_correlation", "Pair Correlation"),
    ]
    for key, label in regime_order:
        entry = dict(regime_pack.get(key) or {})
        rows = list(entry.get("rows") or [])
        if not rows:
            continue
        best = str(entry.get("best_label", "?"))
        worst = str(entry.get("worst_label", "?"))
        best_row: dict[str, Any] = next((r for r in rows if r.get("label") == best), {})
        worst_row: dict[str, Any] = next(
            (r for r in rows if r.get("label") == worst), {},
        )
        total_trades_best = best_row.get("trade_count", 0)
        win_rate_best = best_row.get("win_rate")
        total_trades_worst = worst_row.get("trade_count", 0)
        win_rate_worst = worst_row.get("win_rate")
        line = f"  {label}: best={best}"
        if win_rate_best is not None:
            line += f" (wr={_fmt(win_rate_best * 100.0)}%, n={total_trades_best})"
        line += f", worst={worst}"
        if win_rate_worst is not None:
            line += f" (wr={_fmt(win_rate_worst * 100.0)}%, n={total_trades_worst})"
        lines.append(line)
    return "\n".join(lines)


def _narrative_gate_diagnostics(gates: dict) -> str:
    if isinstance(gates, list):
        return "=== Gate Diagnostics ===\nNo gate diagnostics available."
    gate_data = dict(gates) if isinstance(gates, dict) else {}
    if not gate_data:
        return "=== Gate Diagnostics ===\nNo gate diagnostics available."
    lines = ["=== Gate Diagnostics ==="]
    active_frac = gate_data.get("active_bar_fraction")
    if active_frac is not None:
        lines.append(f"Active bar fraction: {_fmt(active_frac * 100.0)}%")
    entry_frac = gate_data.get("entry_signal_bar_fraction")
    if entry_frac is not None:
        lines.append(f"Entry signal bar fraction: {_fmt(entry_frac * 100.0)}%")
    alignment = gate_data.get("score_alignment_when_active")
    if alignment is not None:
        lines.append(f"Score alignment when active: {_fmt(alignment * 100.0)}%")
    flip_rate = gate_data.get("score_sign_flip_rate")
    if flip_rate is not None:
        lines.append(f"Signal flip rate: {_fmt(flip_rate * 100.0)}%")
    position_flip_rate = gate_data.get("position_flip_rate")
    if position_flip_rate is not None:
        lines.append(f"Position flip rate: {_fmt(position_flip_rate * 100.0)}%")
    median_assets = gate_data.get("median_active_asset_count")
    if median_assets is not None:
        lines.append(f"Median active assets: {_fmt(median_assets)}")
    bottleneck_tags = list(gate_data.get("bottleneck_tags") or [])
    if bottleneck_tags:
        lines.append(f"Bottlenecks: {', '.join(bottleneck_tags)}")
    else:
        lines.append("No bottlenecks detected.")
    regime_gates = dict(gate_data.get("regime_gates") or {})
    if regime_gates.get("configured"):
        gate_active = regime_gates.get("active_fraction")
        gate_entries = list(regime_gates.get("entry") or [])
        lines.append(
            f"Regime gates active: {_fmt(gate_active * 100.0)}%"
            if gate_active is not None
            else "Regime gates: configured",
        )
        for entry in gate_entries[:3]:
            expr = str(entry.get("expression", str(entry.get("feature", "?"))))
            frac = entry.get("active_fraction")
            if frac is not None:
                lines.append(f"  Gate '{expr}': active {_fmt(frac * 100.0)}%")
    else:
        lines.append("No regime gates configured.")
    policy = dict(gate_data.get("policy") or {})
    if policy:
        entry_score = policy.get("entry_abs_score")
        exit_score = policy.get("exit_abs_score")
        if entry_score is not None and exit_score is not None:
            lines.append(f"Policy: entry={_fmt(entry_score)}, exit={_fmt(exit_score)}")
    return "\n".join(lines)


def build_signal_narrative(canonical_run: dict) -> str:
    """Build a complete human-readable signal evaluation narrative."""
    sections: list[str] = []
    sections.append(_narrative_header(canonical_run))
    sections.append("")
    sections.append(_narrative_evidence_sources(canonical_run))
    sections.append("")
    sections.append(_narrative_performance_summary(canonical_run))
    sections.append("")
    sections.append(_narrative_drawdown_analysis(canonical_run))
    sections.append("")
    sections.append(_narrative_feature_decomposition(canonical_run))
    sections.append("")
    context_pack = dict(canonical_run.get("pre_audit_context_pack") or {})
    exemplar_trades = context_pack.get("exemplar_trades") or {}
    sections.append(_narrative_exemplar_trades(exemplar_trades))
    sections.append("")
    sections.append(_narrative_regime_context(canonical_run))
    sections.append("")
    gate_diagnostics = context_pack.get("gate_diagnostics") or {}
    sections.append(_narrative_gate_diagnostics(gate_diagnostics))
    sections.append("")
    narrative = "\n".join(sections)
    return narrative
