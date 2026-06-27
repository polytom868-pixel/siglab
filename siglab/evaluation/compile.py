from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import numpy as np
import pandas as pd

from siglab.config import SiglabConfig
from siglab.data.feeds import MarketDataProvider
from siglab.evaluation.backtest import convert_to_spot
from siglab.evaluation.events import (
    classify_pt_market_state,
    detect_pt_roll_events,
    summarize_pt_universe,
)
from siglab.evaluation.feature_dsl import load_feature_spec, resolve_feature_frames
from siglab.evaluation.runner_analysis import mean_pairwise_rolling_corr
from siglab.evaluation.strategy_semantics import PAIR_TRADE_FAMILIES
from siglab.families import (
    family_capabilities,
    family_diagnostic_adapter,
    family_execution_profile,
    family_policy_schema,
    load_family_spec,
)
from siglab.schemas import CompiledChild, SignalSpec
from siglab.utils import feature_hash as _fh

_PERF_PROFILES = {"ranked_directional", "basket_neutral_spread", "ranked_carry"}


def _ssp(
    raw_frames: dict[str, pd.DataFrame],
    spec: SignalSpec,
    aliases: dict[str, str],
    fw: dict[str, float],
) -> tuple[
    dict[str, pd.DataFrame],
    pd.DataFrame,
    dict[str, pd.DataFrame],
    pd.Series | None,
    dict[str, Any],
]:
    shifted = {k: v.shift(1) for k, v in raw_frames.items()}
    ff = resolve_feature_frames(spec.features, aliases=aliases, raw_frames=shifted)
    score = _ws(ff, spec.features, fw, return_components=False)
    sc = _ws(ff, spec.features, fw, return_components=True)
    rgm, rgm_meta = _rrg(spec.regime_gates, aliases=aliases, raw_frames=shifted)
    return (
        ff,
        cast(pd.DataFrame, score),
        cast(dict[str, pd.DataFrame], sc),
        rgm,
        rgm_meta,
    )


def _bsm(
    spec: SignalSpec,
    *,
    capabilities: list[str] | dict[str, Any],
    execution_profile: str | None,
    diagnostic_adapter: str | None,
    policy_schema: str | None,
    features: list[str],
    feature_hash: str,
    source: str,
    bundle_as_of: object,
    asset_breadth: int,
    rg_meta: dict[str, Any],
    prices: pd.DataFrame,
    **extra: object,
) -> dict[str, Any]:
    return {
        "track": spec.track,
        "family": spec.family,
        "capabilities": capabilities,
        "execution_profile": execution_profile,
        "diagnostic_adapter": diagnostic_adapter,
        "policy_schema": policy_schema,
        "features": features,
        "feature_hash": feature_hash,
        "regime_gates": rg_meta,
        "source": source,
        "bundle_as_of": bundle_as_of,
        "asset_breadth": asset_breadth,
        "signal_timing": "next_bar",
        "compiled_at": datetime.now(UTC).isoformat(),
        **extra,
        **_hb(prices),
    }


def _cs_z(frame: pd.DataFrame) -> pd.DataFrame:
    c = frame.replace([np.inf, -np.inf], np.nan)
    m = c.mean(axis=1)
    return c.sub(m, axis=0).div(c.std(axis=1).replace(0.0, np.nan), axis=0).fillna(0.0)


def _ts_z(frame: pd.DataFrame, *, window: int) -> pd.DataFrame:
    mp = max(8, window // 4)
    rm = frame.rolling(window, min_periods=mp).mean()
    rs = frame.rolling(window, min_periods=mp).std().replace(0.0, np.nan)
    return frame.sub(rm).div(rs).replace([np.inf, -np.inf], np.nan).fillna(0.0)


def _ws(
    ff,
    selected,
    fw,
    *,
    normalization="cross_sectional",
    z_window=72,
    return_components=False,
):
    chosen = [f for f in selected if f in ff]
    if return_components and not chosen:
        return {}
    if not chosen:
        raise ValueError("Spec did not reference any compiled features")
    wt = sum(abs(float(fw.get(n, 1.0))) for n in chosen) or float(len(chosen))
    if return_components:
        comps = {}
        for n in chosen:
            c = (
                _ts_z(ff[n], window=z_window)
                if normalization == "time_series" or ff[n].shape[1] <= 1
                else _cs_z(ff[n])
            )
            comps[n] = (c * (float(fw.get(n, 1.0)) / wt)).fillna(0.0)
        return comps
    score = None
    for n in chosen:
        c = (
            _ts_z(ff[n], window=z_window)
            if normalization == "time_series" or ff[n].shape[1] <= 1
            else _cs_z(ff[n])
        )
        w = float(fw.get(n, 1.0)) / wt
        score = c * w if score is None else score.add(c * w, fill_value=0.0)
    assert score is not None
    return score.fillna(0.0)


def _align_cs(frame: pd.DataFrame, *, symbols: list[str]) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(0.0, index=frame.index, columns=symbols)
    clean = frame.apply(pd.to_numeric, errors="coerce")
    sym_cols = [c for c in symbols if c in clean.columns]
    extra = [c for c in clean.columns if c not in symbols]
    broadcast = (
        pd.DataFrame({s: clean[extra].mean(axis=1) for s in symbols}, index=clean.index)
        if extra and not clean[extra].empty
        else pd.DataFrame(0.0, index=clean.index, columns=symbols)
    )
    return cast(
        pd.DataFrame,
        clean.reindex(columns=sym_cols)
        .reindex(columns=symbols)
        .fillna(0.0)
        .add(broadcast, fill_value=0.0)
        .reindex(columns=symbols)
        .fillna(0.0),
    )


def _align_cs_comp(
    components: dict[str, pd.DataFrame],
    *,
    symbols: list[str],
) -> dict[str, pd.DataFrame]:
    return {n: _align_cs(f, symbols=symbols) for n, f in components.items()}


def _mask_ff(
    ff: dict[str, pd.DataFrame],
    eligible: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    return {n: f.where(eligible.reindex_like(f).fillna(False)) for n, f in ff.items()}


def _ensure_elig(score: pd.DataFrame, eligible: pd.DataFrame) -> pd.DataFrame:
    adj = score.copy()
    ea = eligible.reindex_like(adj).fillna(False)
    for ts in ea.sum(axis=1)[ea.sum(axis=1) == 1].index:
        labels = list(ea.columns[ea.loc[ts]])
        if labels:
            adj.loc[ts, labels[0]] = max(float(adj.loc[ts, labels[0]]), 1.0)
    return adj


def _rgm(idx: pd.Index, rgm: pd.Series | None) -> pd.Series:
    return (
        rgm.reindex(idx).ffill().fillna(False).astype(bool)
        if rgm is not None
        else pd.Series(True, index=idx, dtype=bool)
    )


def _brp(
    score: pd.DataFrame,
    *,
    long_count: int,
    short_count: int,
    gross_target: float,
    max_asset_weight: float,
    require_positive_longs: bool = False,
    min_abs_score: float = 0.0,
    require_both_sides: bool = False,
    regime_gate_mask: pd.Series | None = None,
) -> pd.DataFrame:
    n, m = score.shape
    arr = score.to_numpy(dtype=float, na_value=np.nan)
    result = np.zeros((n, m), dtype=float)
    cols = list(score.columns)
    gm = _rgm(score.index, regime_gate_mask)
    for i in range(n):
        if not bool(gm.iloc[i]):
            continue
        row = arr[i]
        nmask = np.isnan(row)
        if nmask.all():
            continue
        ci = np.where(~nmask)[0]
        li: list[int] = []
        si: list[int] = []
        if long_count > 0:
            cands = ci[np.argsort(-row[ci])][: min(long_count, len(ci))]
            if require_positive_longs:
                cands = [j for j in cands if row[j] > 0.0]
            if min_abs_score > 0.0:
                cands = [j for j in cands if row[j] >= min_abs_score]
            li = cands
        if short_count > 0:
            cands = ci[np.argsort(row[ci])][: min(short_count, len(ci))]
            if min_abs_score > 0.0:
                cands = [j for j in cands if row[j] <= -min_abs_score]
            si = cands
        for j in set(li) & set(si):
            if row[j] >= 0.0:
                si.remove(j)
            else:
                li.remove(j)
        hl = len(li) > 0
        hs = len(si) > 0
        if require_both_sides and not (hl and hs):
            continue
        if not hl and not hs:
            continue
        lb, sb = (
            (gross_target / 2.0, gross_target / 2.0)
            if hl and hs
            else (gross_target, 0.0)
            if hl
            else (0.0, gross_target)
        )
        if hl:
            result[i, li] = min(max_asset_weight, lb / len(li))
        if hs:
            result[i, si] = -min(max_asset_weight, sb / len(si))
    return pd.DataFrame(result, index=score.index, columns=cols).ffill().fillna(0.0)


def _bpp(
    score: pd.DataFrame,
    *,
    selection_count: int,
    gross_target: float,
    max_asset_weight: float,
    regime_gate_mask: pd.Series | None = None,
) -> pd.DataFrame:
    cols = [f"{s}_SPOT" for s in score.columns] + [f"{s}_PERP" for s in score.columns]
    target = pd.DataFrame(0.0, index=score.index, columns=cols)
    gm = _rgm(score.index, regime_gate_mask)
    for ts, row in score.iterrows():
        tk = cast(pd.Timestamp, ts)
        if not bool(gm.loc[tk]):
            continue
        clean = row.dropna()
        if clean.empty:
            continue
        sel = clean[clean > 0.0].nlargest(min(selection_count, len(clean)))
        if sel.empty:
            continue
        pw = min(max_asset_weight, gross_target / (2.0 * len(sel)))
        for s in sel.index:
            target.loc[ts, f"{s}_SPOT"] = pw
            target.loc[ts, f"{s}_PERP"] = -pw
    return target.ffill().fillna(0.0)


def _bptp(
    score: pd.DataFrame,
    *,
    a1: str,
    a2: str,
    gross_target: float,
    max_gross_target: float,
    max_asset_weight: float,
    entry_abs_score: float,
    exit_abs_score: float,
    flip_abs_score: float,
    max_holding_bars: int,
    cooldown_bars: int,
    signal_leverage_scale: float,
    regime_gate_mask: pd.Series | None = None,
    exit_on_regime_break: bool = True,
) -> pd.DataFrame:
    target = pd.DataFrame(0.0, index=score.index, columns=[a1, a2])
    pc = score.columns[0]
    ps = 0
    hb = 0
    cd = 0
    gm = _rgm(score.index, regime_gate_mask)
    for ts, v in score[pc].items():
        tk = cast(pd.Timestamp, ts)
        sv = 0.0 if pd.isna(v) else float(v)
        ex = False
        rok = bool(gm.loc[tk])
        if ps == 0:
            hb = 0
            if cd > 0:
                cd -= 1
            if cd <= 0 and rok:
                if sv >= entry_abs_score:
                    ps = 1
                    hb = 0
                elif sv <= -entry_abs_score:
                    ps = -1
                    hb = 0
        else:
            hb += 1
            to = max_holding_bars > 0 and hb >= max_holding_bars
            if exit_on_regime_break and not rok:
                ex = True
            elif ps > 0:
                if sv <= -flip_abs_score:
                    ps = -1
                    hb = 0
                elif to or abs(sv) < exit_abs_score:
                    ex = True
            elif sv >= flip_abs_score:
                ps = 1
                hb = 0
            elif to or abs(sv) < exit_abs_score:
                ex = True
            if ex:
                ps = 0
                hb = 0
                cd = cooldown_bars
        if ps == 0:
            continue
        ss = max(0.0, abs(sv) - entry_abs_score)
        lf = min(1.0, ss / max(signal_leverage_scale, 1e-06))
        gt = gross_target + (max_gross_target - gross_target) * lf
        lw = min(max_asset_weight, gt / 2.0)
        target.loc[ts, a1] = lw if ps > 0 else -lw
        target.loc[ts, a2] = -lw if ps > 0 else lw
    return target.fillna(0.0)


def _ppp(
    *,
    family: str,
    params: dict[str, Any],
    defaults: dict[str, Any],
) -> dict[str, Any]:
    gt = float(params.get("gross_target", defaults.get("gross_target", 1.0)))
    mgc = 1.0 if family == "perp_pair_trade_unlevered" else 3.0
    mgt = max(
        gt,
        min(
            mgc,
            float(params.get("max_gross_target", defaults.get("max_gross_target", gt))),
        ),
    )
    ead = defaults.get("entry_abs_score", defaults.get("min_abs_score", 0.0))
    eas = max(
        0.0,
        min(
            1.5,
            float(params.get("entry_abs_score", params.get("min_abs_score", ead))),
        ),
    )
    exd = defaults.get("exit_abs_score", max(0.0, eas * 0.5))
    exas = max(0.0, min(eas, float(params.get("exit_abs_score", exd))))
    fad = defaults.get("flip_abs_score", eas)
    fas = max(eas, min(2.5, float(params.get("flip_abs_score", fad))))
    mhb = max(
        0,
        min(
            24 * 14,
            int(params.get("max_holding_bars", defaults.get("max_holding_bars", 0))),
        ),
    )
    cb = max(
        0,
        min(24 * 7, int(params.get("cooldown_bars", defaults.get("cooldown_bars", 0)))),
    )
    sls = max(
        0.25,
        min(
            3.0,
            float(
                params.get(
                    "signal_leverage_scale",
                    defaults.get("signal_leverage_scale", 0.75),
                ),
            ),
        ),
    )
    return {
        "gross_target": gt,
        "max_gross_target": mgt,
        "entry_abs_score": eas,
        "exit_abs_score": exas,
        "flip_abs_score": fas,
        "max_holding_bars": mhb,
        "cooldown_bars": cb,
        "signal_leverage_scale": sls,
        "min_abs_score": eas,
    }


def _rpp(
    *,
    params: dict[str, Any],
    defaults: dict[str, Any],
    long_enabled_default: bool,
    short_enabled_default: bool,
) -> dict[str, Any]:
    gt = float(params.get("gross_target", defaults.get("gross_target", 1.0)))
    mas = float(params.get("min_abs_score", defaults.get("min_abs_score", 0.0)))
    lc = int(params.get("long_count", defaults.get("long_count", 0)))
    sc = int(params.get("short_count", defaults.get("short_count", 0)))
    return {
        "gross_target": max(0.1, min(3.0, gt)),
        "min_abs_score": max(0.0, min(1.5, mas)),
        "long_count": max(0, min(8, lc)),
        "short_count": max(0, min(8, sc)),
        "long_enabled": bool(params.get("long_enabled", long_enabled_default)),
        "short_enabled": bool(params.get("short_enabled", short_enabled_default)),
    }


def _gmff(
    frame: pd.DataFrame,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> pd.Series:
    num = frame.apply(pd.to_numeric, errors="coerce")
    if minimum is None and maximum is None:
        return cast(pd.Series, num.fillna(0.0).gt(0.0).all(axis=1))
    mask = pd.Series(True, index=num.index, dtype=bool)
    if minimum is not None:
        mask &= num.ge(float(minimum)).all(axis=1)
    if maximum is not None:
        mask &= num.le(float(maximum)).all(axis=1)
    return mask.fillna(False)


def _rrg(
    regime_gates: dict[str, Any],
    *,
    aliases: dict[str, str],
    raw_frames: dict[str, pd.DataFrame],
) -> tuple[pd.Series | None, dict[str, Any]]:
    pld = regime_gates or {}
    entry = list(pld.get("entry") or [])
    eob = bool(pld.get("exit_on_break", True))
    if not entry:
        return (None, {"configured": False, "entry": [], "exit_on_break": eob})
    specs: list[dict[str, Any]] = []
    exprs: list[str] = []
    for spec in entry:
        if isinstance(spec, str):
            expr = spec.strip()
            mn = None
            mx = None
        elif isinstance(spec, dict):
            expr = str(spec.get("expression") or spec.get("feature") or "").strip()
            mn = spec.get("min")
            mx = spec.get("max")
        else:
            continue
        if not expr:
            continue
        n: dict[str, str | float] = {"expression": expr}
        if mn is not None:
            n["min"] = float(mn)
        if mx is not None:
            n["max"] = float(mx)
        specs.append(n)
        exprs.append(expr)
    if not exprs:
        return (None, {"configured": False, "entry": [], "exit_on_break": eob})
    resolved = resolve_feature_frames(exprs, aliases=aliases, raw_frames=raw_frames)
    cmask: pd.Series | None = None
    details: list[dict[str, Any]] = []
    for spec in specs:
        f = resolved.get(spec["expression"])
        if f is None or f.empty:
            continue
        gm = _gmff(f, minimum=spec.get("min"), maximum=spec.get("max"))
        cmask = gm if cmask is None else cmask & gm
        details.append(
            {**spec, "active_fraction": float(gm.mean()) if len(gm.index) else 0.0},
        )
    if cmask is None:
        return (None, {"configured": False, "entry": [], "exit_on_break": eob})
    return (
        cmask.fillna(False),
        {
            "configured": True,
            "entry": details,
            "exit_on_break": eob,
            "combined_active_fraction": float(cmask.mean())
            if len(cmask.index)
            else 0.0,
        },
    )


def _gf(s: pd.Series) -> pd.DataFrame:
    return pd.to_numeric(s, errors="coerce").rename("GLOBAL").to_frame()


def _pgrf(prices: pd.DataFrame, funding: pd.DataFrame) -> dict[str, pd.DataFrame]:
    prices = prices.sort_index()
    funding = funding.reindex(prices.index).ffill().fillna(0.0)
    r1h = prices.pct_change()
    return {
        "market_price_mean": _gf(prices.mean(axis=1)),
        "market_funding_mean": _gf(funding.mean(axis=1)),
        "market_funding_dispersion": _gf(funding.std(axis=1)),
        "market_breadth_24h": _gf(prices.pct_change(24).gt(0.0).mean(axis=1)),
        "market_co_movement_72h": _gf(mean_pairwise_rolling_corr(r1h, window=72)),
        "market_realized_vol_168h": _gf(r1h.rolling(168).std().mean(axis=1)),
    }


def _prf(prices: pd.DataFrame, funding: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return {"price": prices, "funding": funding, **_pgrf(prices, funding)}


def _pair_rf(
    *,
    prices: pd.DataFrame,
    funding: pd.DataFrame,
    a1: str,
    a2: str,
) -> dict[str, pd.DataFrame]:
    p1 = prices[a1].replace([np.inf, -np.inf], np.nan)
    p2 = prices[a2].replace([np.inf, -np.inf], np.nan)
    f1 = funding[a1].replace([np.inf, -np.inf], np.nan)
    f2 = funding[a2].replace([np.inf, -np.inf], np.nan)

    def _pf(s):
        return s.rename("PAIR").to_frame().reindex(prices.index)

    return {
        "asset_1_price": _pf(p1),
        "asset_2_price": _pf(p2),
        "asset_1_funding": _pf(f1),
        "asset_2_funding": _pf(f2),
        "price_ratio": _pf(p1.div(p2).replace([np.inf, -np.inf], np.nan)),
        "funding_spread": _pf(f1.sub(f2, fill_value=0.0)),
        **_pgrf(prices[[a1, a2]], funding[[a1, a2]]),
    }


def _pt_rf(
    prices: pd.DataFrame,
    implied_apy: pd.DataFrame,
    underlying_apy: pd.DataFrame,
    total_tvl: pd.DataFrame,
    dte: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    return {
        "pt_price": prices,
        "implied_apy": implied_apy,
        "underlying_apy": underlying_apy,
        "total_tvl": total_tvl,
        "days_to_expiry": dte.clip(lower=1.0),
    }


def _ppt_mf(provider, markets, histories):
    labels = list(histories)

    def _mf(column):
        return _ffill_wow(
            pd.concat(
                [
                    pd.to_numeric(histories[label][column], errors="coerce").rename(
                        label
                    )
                    for label in labels
                ],
                axis=1,
            ).sort_index(),
        )

    prices = _mf("ptPrice")
    iapy = _mf("impliedApy")
    uapy = _mf("underlyingApy")
    ttl = _mf("totalTvl")
    expiry_by_label = {
        provider.market_label(r): pd.Timestamp(str(r["expiry"])).tz_localize(None)
        for r in markets
        if provider.market_label(r) in labels
    }
    dte = pd.DataFrame(index=prices.index, columns=prices.columns, dtype=float)
    for lbl in prices.columns:
        exp = expiry_by_label[lbl]
        dte[lbl] = (
            (exp - prices.index.to_series()).dt.total_seconds() / 86400.0
        ).values
    valid = prices.notna().any(axis=1)
    prices = prices.loc[valid]
    iapy = iapy.reindex(prices.index)
    uapy = uapy.reindex(prices.index)
    ttl = ttl.reindex(prices.index)
    dte = dte.reindex(prices.index)
    return (prices, iapy, uapy, ttl, dte)


def _ffill_wow(frame: pd.DataFrame) -> pd.DataFrame:
    filled = frame.sort_index().ffill()
    for col in filled.columns:
        obs = frame[col].dropna()
        if obs.empty:
            filled[col] = np.nan
            continue
        filled.loc[filled.index < obs.index.min(), col] = np.nan
        filled.loc[filled.index > obs.index.max(), col] = np.nan
    return filled


def _bpt_hp(
    pt_positions: pd.DataFrame,
    *,
    m2hs: dict[str, str],
    hedge_symbols: list[str],
    hr: float,
) -> pd.DataFrame:
    cols = [f"{s}_PERP" for s in hedge_symbols]
    hp = pd.DataFrame(0.0, index=pt_positions.index, columns=cols)
    for ml, hs in m2hs.items():
        if ml not in pt_positions.columns or not hs:
            continue
        hc = f"{hs}_PERP"
        if hc not in hp.columns:
            continue
        hp[hc] = hp[hc].add(-pt_positions[ml] * hr, fill_value=0.0)
    return hp.fillna(0.0)


def _blpf(root_prices: pd.DataFrame, carry_apy: pd.DataFrame) -> pd.DataFrame:
    if root_prices.empty:
        return root_prices
    dty = root_prices.index.to_series().diff().dt.total_seconds().fillna(0.0) / (
        365.25 * 24.0 * 60.0 * 60.0
    )
    pdt = dty[dty > 0]
    fd = float(pdt.median()) if not pdt.empty else 0.0
    dt_filled = dty.replace(0.0, np.nan).fillna(fd)
    cf = (
        1.0 + carry_apy.shift(1).fillna(0.0).mul(dt_filled, axis=0).clip(lower=-0.99)
    ).cumprod()
    return root_prices.reindex(cf.index).ffill().mul(cf)


def _hb(frame: pd.DataFrame) -> dict[str, str | None]:
    if frame.empty:
        return {"history_start": None, "history_end": None}
    return {
        "history_start": frame.index.min().isoformat(),
        "history_end": frame.index.max().isoformat(),
    }


def _pt_lm(
    *,
    spec: SignalSpec,
    prices: pd.DataFrame,
    days_to_expiry: pd.DataFrame,
    eligible: pd.DataFrame,
    inside_roll_window: pd.DataFrame,
    expired_or_untradable: pd.DataFrame,
    roll_events: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "lifecycle_policy": {
            "open_ended_policy": "continuous_rotation",
            "pt_universe_policy": "dynamic_eligible_set",
            "roll_target_policy": "strategy_ranked_replacement",
            "roll_cost_model": "simple_trading_cost",
            "roll_days_before_expiry": spec.risk.roll_days_before_expiry,
            "min_days_to_expiry": spec.universe.min_days_to_expiry,
            "max_days_to_expiry": spec.universe.max_days_to_expiry,
        },
        "pt_strategy_badges": ["open-ended", "dynamic universe", "roll-forward"],
        "roll_event_count": len(roll_events),
        "roll_events": roll_events,
        **summarize_pt_universe(
            prices=prices,
            eligible=eligible,
            inside_roll_window=inside_roll_window,
            expired_or_untradable=expired_or_untradable,
        ),
        "days_to_expiry_latest": {
            c: float(v) for c, v in days_to_expiry.iloc[-1].dropna().to_dict().items()
        }
        if not days_to_expiry.empty
        else {},
    }


def _lrf(
    *,
    lending_prices: pd.DataFrame,
    combined_supply_apy: pd.DataFrame,
    supply_apr: pd.DataFrame,
    supply_reward_apr: pd.DataFrame,
    base_yield_apy: pd.DataFrame,
    utilization: pd.DataFrame,
    supply_tvl_usd: pd.DataFrame,
    borrow_apr: pd.DataFrame,
    borrow_tvl_usd: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    return {
        "lending_price": lending_prices,
        "combined_supply_apy": combined_supply_apy,
        "supply_apr": supply_apr,
        "supply_reward_apr": supply_reward_apr,
        "base_yield_apy": base_yield_apy,
        "utilization": utilization,
        "supply_tvl_usd": supply_tvl_usd,
        "borrow_apr": borrow_apr,
        "borrow_tvl_usd": borrow_tvl_usd,
    }


async def compile_spec(
    settings: SiglabConfig,
    provider: MarketDataProvider,
    spec: SignalSpec,
) -> CompiledChild:
    fs = load_family_spec(settings.root_dir, spec.track, spec.family)
    d = fs.get("defaults") or {}
    fw = fs.get("feature_weights") or {}
    caps = family_capabilities(fs)
    ep = family_execution_profile(fs)
    da = family_diagnostic_adapter(fs)
    ps = family_policy_schema(fs)
    fspec = load_feature_spec(settings.root_dir, track=spec.track, family=spec.family)
    a = fspec.get("aliases") or {}
    if spec.track == "trend_signals" and spec.family in PAIR_TRADE_FAMILIES:
        rs = [str(s).upper() for s in spec.universe.basis_groups[:2]]
        symbols = await provider.discover_perp_symbols(rs, limit=2)
        od = [s for s in rs if s in symbols]
        for s in symbols:
            if s not in od:
                od.append(s)
        if len(od) != 2:
            raise ValueError("Pair trade family requires exactly two supported symbols")
        a1, a2 = od[0], od[1]
        bundle = await provider.fetch_perp_bundle(
            symbols=od,
            lookback_days=spec.universe.lookback_days,
            interval=spec.universe.interval,
        )
        raw_frames = _pair_rf(
            prices=bundle["prices"],
            funding=bundle["funding"],
            a1=a1,
            a2=a2,
        )
        _, score, sc, rgm, rgm_meta = _ssp(raw_frames, spec, a, fw)
        pp = _ppp(family=spec.family, params=spec.params, defaults=d)
        positions = _bptp(
            score,
            a1=a1,
            a2=a2,
            gross_target=pp["gross_target"],
            max_gross_target=pp["max_gross_target"],
            max_asset_weight=spec.risk.max_asset_weight,
            entry_abs_score=pp["entry_abs_score"],
            exit_abs_score=pp["exit_abs_score"],
            flip_abs_score=pp["flip_abs_score"],
            max_holding_bars=pp["max_holding_bars"],
            cooldown_bars=pp["cooldown_bars"],
            signal_leverage_scale=pp["signal_leverage_scale"],
            regime_gate_mask=rgm,
            exit_on_regime_break=bool(rgm_meta.get("exit_on_break", True)),
        )
        return CompiledChild(
            prices=bundle["prices"][od],
            target_positions=positions,
            funding_rates=bundle["funding"][od],
            signal_score=score,
            signal_components=sc,
            regime_gate_mask=rgm,
            metadata=_bsm(
                spec,
                capabilities=caps,
                execution_profile=ep,
                diagnostic_adapter=da,
                policy_schema=ps,
                features=spec.features,
                feature_hash=_fh(spec.features),
                source=bundle["source"],
                bundle_as_of=bundle.get("bundle_as_of"),
                asset_breadth=len(od),
                prices=bundle["prices"],
                rg_meta=rgm_meta,
                symbols=od,
                asset_1_symbol=a1,
                asset_2_symbol=a2,
                gross_target=pp["gross_target"],
                max_gross_target=pp["max_gross_target"],
                entry_abs_score=pp["entry_abs_score"],
                exit_abs_score=pp["exit_abs_score"],
                flip_abs_score=pp["flip_abs_score"],
                max_holding_bars=pp["max_holding_bars"],
                cooldown_bars=pp["cooldown_bars"],
                min_abs_score=pp["min_abs_score"],
                signal_leverage_scale=pp["signal_leverage_scale"],
                leverage_profile="unlevered"
                if spec.family == "perp_pair_trade_unlevered"
                else "levered",
            ),
        )
    if spec.track == "trend_signals" and ep in _PERF_PROFILES:
        symbols = await provider.discover_perp_symbols(
            spec.universe.basis_groups,
            limit=spec.universe.max_symbols,
        )
        bundle = await provider.fetch_perp_bundle(
            symbols=symbols,
            lookback_days=spec.universe.lookback_days,
            interval=spec.universe.interval,
        )
        raw_frames = _prf(bundle["prices"], bundle["funding"])
        _, score, sc, rgm, rgm_meta = _ssp(raw_frames, spec, a, fw)
        score = _align_cs(score, symbols=symbols)
        sc = _align_cs_comp(sc, symbols=symbols)
        rgm, rgm_meta = _rrg(spec.regime_gates, aliases=a, raw_frames=raw_frames)
        led = True
        sed = ep != "ranked_directional"
        policy = _rpp(
            params=spec.params,
            defaults=d,
            long_enabled_default=led,
            short_enabled_default=sed,
        )
        positions = _brp(
            score,
            long_count=policy["long_count"] if policy["long_enabled"] else 0,
            short_count=policy["short_count"] if policy["short_enabled"] else 0,
            gross_target=policy["gross_target"],
            max_asset_weight=spec.risk.max_asset_weight,
            require_positive_longs=ep == "ranked_directional",
            min_abs_score=policy["min_abs_score"],
            require_both_sides=ep in {"basket_neutral_spread", "ranked_carry"},
            regime_gate_mask=rgm,
        )
        return CompiledChild(
            prices=bundle["prices"],
            target_positions=positions,
            funding_rates=bundle["funding"],
            signal_score=score,
            signal_components=sc,
            regime_gate_mask=rgm,
            metadata=_bsm(
                spec,
                capabilities=caps,
                execution_profile=ep,
                diagnostic_adapter=da,
                policy_schema=ps,
                features=spec.features,
                feature_hash=_fh(spec.features),
                source=bundle["source"],
                bundle_as_of=bundle.get("bundle_as_of"),
                asset_breadth=len(symbols),
                prices=bundle["prices"],
                rg_meta=rgm_meta,
                symbols=symbols,
                long_enabled=policy["long_enabled"],
                short_enabled=policy["short_enabled"],
                long_count=policy["long_count"],
                short_count=policy["short_count"],
                min_abs_score=policy["min_abs_score"],
                gross_target=policy["gross_target"],
            ),
        )
    if spec.family == "basis_spread":
        symbols = await provider.discover_perp_symbols(
            spec.universe.basis_groups,
            limit=spec.universe.max_symbols,
        )
        bundle = await provider.fetch_perp_bundle(
            symbols=symbols,
            lookback_days=spec.universe.lookback_days,
            interval=spec.universe.interval,
        )
        raw_frames = _prf(bundle["prices"], bundle["funding"])
        _, score, sc, rgm, rgm_meta = _ssp(raw_frames, spec, a, fw)
        pair_positions = _bpp(
            score,
            selection_count=int(
                spec.params.get("selection_count", d.get("selection_count", 2)),
            ),
            gross_target=float(
                spec.params.get("gross_target", d.get("gross_target", 1.0)),
            ),
            max_asset_weight=spec.risk.max_asset_weight,
            regime_gate_mask=rgm,
        )
        sp, sf = convert_to_spot(bundle["prices"])
        prices = pd.concat(
            [sp.add_suffix("_SPOT"), bundle["prices"].add_suffix("_PERP")],
            axis=1,
        ).sort_index()
        funding = (
            pd.concat(
                [sf.add_suffix("_SPOT"), bundle["funding"].add_suffix("_PERP")],
                axis=1,
            )
            .sort_index()
            .reindex(prices.index)
            .ffill()
            .fillna(0.0)
        )
        return CompiledChild(
            prices=prices,
            target_positions=pair_positions.reindex(prices.index).ffill().fillna(0.0),
            funding_rates=funding,
            signal_score=score,
            signal_components=sc,
            regime_gate_mask=rgm,
            metadata=_bsm(
                spec,
                capabilities=caps,
                execution_profile=ep,
                diagnostic_adapter=da,
                policy_schema=ps,
                features=spec.features,
                feature_hash=_fh(spec.features),
                source=bundle["source"],
                bundle_as_of=bundle.get("bundle_as_of"),
                asset_breadth=len(symbols),
                prices=prices,
                rg_meta=rgm_meta,
                symbols=symbols,
                selection_count=int(
                    spec.params.get("selection_count", d.get("selection_count", 2)),
                ),
                gross_target=float(
                    spec.params.get("gross_target", d.get("gross_target", 1.0)),
                ),
            ),
        )
    if spec.family == "stable_pt_ladder":
        markets = await provider.discover_stable_pt_markets(
            spec.universe,
            limit=spec.universe.max_symbols,
        )
        histories = await provider.fetch_pt_histories(
            markets,
            lookback_days=spec.universe.lookback_days,
        )
        if not histories:
            raise ValueError("No stable PT histories available for this spec")
        prices, iapy, uapy, ttl, dte = _ppt_mf(provider, markets, histories)
        raw_frames = _pt_rf(
            prices=prices,
            implied_apy=iapy,
            underlying_apy=uapy,
            total_tvl=ttl,
            dte=dte,
        )
        ff, score, _, _, _ = _ssp(raw_frames, spec, a, fw)
        pt_state = classify_pt_market_state(
            prices=prices,
            days_to_expiry=dte,
            required_frames=[iapy, uapy, ttl],
            roll_days_before_expiry=spec.risk.roll_days_before_expiry,
            min_days_to_expiry=spec.universe.min_days_to_expiry,
            max_days_to_expiry=spec.universe.max_days_to_expiry,
        )
        ff = _mask_ff(ff, pt_state["eligible"])
        score = _ensure_elig(
            cast(pd.DataFrame, _ws(ff, spec.features, fw, return_components=False)),
            pt_state["eligible"],
        )
        positions = _brp(
            score,
            long_count=int(
                spec.params.get("selection_count", d.get("selection_count", 3)),
            ),
            short_count=0,
            gross_target=float(
                spec.params.get("gross_target", d.get("gross_target", 0.9)),
            ),
            max_asset_weight=spec.risk.max_asset_weight,
            require_positive_longs=True,
        )
        roll_events = detect_pt_roll_events(
            positions,
            eligible=pt_state["eligible"],
            inside_roll_window=pt_state["inside_roll_window"],
            expired_or_untradable=pt_state["expired_or_untradable"],
            days_to_expiry=dte,
        )
        funding = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
        return CompiledChild(
            prices=prices,
            target_positions=positions.reindex(prices.index).ffill().fillna(0.0),
            funding_rates=funding,
            metadata=_bsm(
                spec,
                capabilities=caps,
                execution_profile=ep,
                diagnostic_adapter=da,
                policy_schema=ps,
                features=spec.features,
                feature_hash=_fh(spec.features),
                source="pendle_public",
                bundle_as_of=prices.index.max().isoformat()
                if not prices.empty
                else None,
                asset_breadth=len(prices.columns),
                prices=prices,
                rg_meta={},
                markets=list(prices.columns),
                selection_count=int(
                    spec.params.get("selection_count", d.get("selection_count", 3)),
                ),
                gross_target=float(
                    spec.params.get("gross_target", d.get("gross_target", 0.9)),
                ),
                **_pt_lm(
                    spec=spec,
                    prices=prices,
                    days_to_expiry=dte,
                    eligible=pt_state["eligible"],
                    inside_roll_window=pt_state["inside_roll_window"],
                    expired_or_untradable=pt_state["expired_or_untradable"],
                    roll_events=roll_events,
                ),
            ),
        )
    if spec.family == "pt_yield_rotation":
        markets = await provider.discover_pt_markets(
            spec.universe,
            limit=spec.universe.max_symbols,
        )
        histories = await provider.fetch_pt_histories(
            markets,
            lookback_days=spec.universe.lookback_days,
        )
        if not histories:
            raise ValueError("No PT histories available for this spec")
        prices, iapy, uapy, ttl, dte = _ppt_mf(provider, markets, histories)
        raw_frames = _pt_rf(
            prices=prices,
            implied_apy=iapy,
            underlying_apy=uapy,
            total_tvl=ttl,
            dte=dte,
        )
        ff, score, _, _, _ = _ssp(raw_frames, spec, a, fw)
        pt_state = classify_pt_market_state(
            prices=prices,
            days_to_expiry=dte,
            required_frames=[iapy, uapy, ttl],
            roll_days_before_expiry=spec.risk.roll_days_before_expiry,
            min_days_to_expiry=spec.universe.min_days_to_expiry,
            max_days_to_expiry=spec.universe.max_days_to_expiry,
        )
        ff = _mask_ff(ff, pt_state["eligible"])
        score = _ensure_elig(
            cast(pd.DataFrame, _ws(ff, spec.features, fw, return_components=False)),
            pt_state["eligible"],
        )
        pt_positions = _brp(
            score,
            long_count=int(
                spec.params.get("selection_count", d.get("selection_count", 2)),
            ),
            short_count=0,
            gross_target=float(
                spec.params.get("gross_target", d.get("gross_target", 0.8)),
            ),
            max_asset_weight=spec.risk.max_asset_weight,
            require_positive_longs=True,
        )
        roll_events = detect_pt_roll_events(
            pt_positions,
            eligible=pt_state["eligible"],
            inside_roll_window=pt_state["inside_roll_window"],
            expired_or_untradable=pt_state["expired_or_untradable"],
            days_to_expiry=dte,
        )
        funding = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
        cp = prices.copy()
        cpos = pt_positions.reindex(prices.index).ffill().fillna(0.0)
        hm = str(spec.params.get("hedge_mode", d.get("hedge_mode", "none"))).lower()
        hr = float(spec.params.get("hedge_ratio", d.get("hedge_ratio", 1.0)))
        hsym: list[str] = []
        src = "pendle_public"
        if hm == "perp":
            m2hs = {
                provider.market_label(r): str(r.get("hedgeSymbol"))
                for r in markets
                if provider.market_label(r) in prices.columns
                and r.get("hedgeSymbol")
                and str(r.get("hedgeSymbol")) != "USD"
            }
            hsym = sorted(set(m2hs.values()))
            if hsym:
                hb = await provider.fetch_perp_bundle(
                    symbols=hsym,
                    lookback_days=spec.universe.lookback_days,
                    interval="1h",
                )
                hp = hb["prices"].reindex(prices.index).ffill().add_suffix("_PERP")
                hf = (
                    hb["funding"]
                    .reindex(prices.index)
                    .ffill()
                    .fillna(0.0)
                    .add_suffix("_PERP")
                )
                hpos = _bpt_hp(cpos, m2hs=m2hs, hedge_symbols=hsym, hr=hr)
                cp = pd.concat([prices, hp], axis=1).sort_index()
                cpos = (
                    pd.concat([cpos, hpos], axis=1)
                    .reindex(cp.index)
                    .ffill()
                    .fillna(0.0)
                )
                funding = (
                    pd.concat([funding, hf], axis=1)
                    .reindex(cp.index)
                    .ffill()
                    .fillna(0.0)
                )
                src = f"pendle_public+{hb['source']}"
        return CompiledChild(
            prices=cp,
            target_positions=cpos,
            funding_rates=funding,
            metadata=_bsm(
                spec,
                capabilities=caps,
                execution_profile=ep,
                diagnostic_adapter=da,
                policy_schema=ps,
                features=spec.features,
                feature_hash=_fh(spec.features),
                source=src,
                bundle_as_of=prices.index.max().isoformat()
                if not prices.empty
                else None,
                asset_breadth=len(prices.columns),
                prices=cp,
                rg_meta={},
                markets=list(prices.columns),
                hedge_mode=hm,
                hedge_ratio=hr,
                hedge_symbols=hsym,
                selection_count=int(
                    spec.params.get("selection_count", d.get("selection_count", 2)),
                ),
                gross_target=float(
                    spec.params.get("gross_target", d.get("gross_target", 0.8)),
                ),
                **_pt_lm(
                    spec=spec,
                    prices=prices,
                    days_to_expiry=dte,
                    eligible=pt_state["eligible"],
                    inside_roll_window=pt_state["inside_roll_window"],
                    expired_or_untradable=pt_state["expired_or_untradable"],
                    roll_events=roll_events,
                ),
            ),
        )
    if spec.family == "lending_carry_rotation":
        markets = await provider.discover_lending_markets(
            spec.universe,
            limit=spec.universe.max_symbols,
        )
        lb = await provider.fetch_lending_bundle(
            markets,
            lookback_days=spec.universe.lookback_days,
        )
        if lb["prices"].empty:
            raise ValueError("No lending histories available for this spec")
        lp = _blpf(lb["prices"], lb["combined_supply_apy"])
        raw_frames = _lrf(
            lending_prices=lp,
            combined_supply_apy=lb["combined_supply_apy"],
            supply_apr=lb["supply_apr"],
            supply_reward_apr=lb["supply_reward_apr"],
            base_yield_apy=lb["base_yield_apy"],
            utilization=lb["utilization"],
            supply_tvl_usd=lb["supply_tvl_usd"],
            borrow_apr=lb["borrow_apr"],
            borrow_tvl_usd=lb["borrow_tvl_usd"],
        )
        _, score, sc, rgm, rgm_meta = _ssp(raw_frames, spec, a, fw)
        lpos = _brp(
            score,
            long_count=int(
                spec.params.get("selection_count", d.get("selection_count", 2)),
            ),
            short_count=0,
            gross_target=float(
                spec.params.get("gross_target", d.get("gross_target", 0.8)),
            ),
            max_asset_weight=spec.risk.max_asset_weight,
            require_positive_longs=True,
            regime_gate_mask=rgm,
        )
        funding = pd.DataFrame(0.0, index=lp.index, columns=lp.columns)
        cp = lp.copy()
        cpos = lpos.reindex(lp.index).ffill().fillna(0.0)
        hm = str(spec.params.get("hedge_mode", d.get("hedge_mode", "none"))).lower()
        hr = float(spec.params.get("hedge_ratio", d.get("hedge_ratio", 1.0)))
        lh: list[str] = []
        src = lb["source"]
        if hm == "perp":
            m2hs = {
                label: s for label, s in lb["hedge_symbols"].items() if s and s != "USD"
            }
            lh = sorted(set(m2hs.values()))
            if lh:
                hb = await provider.fetch_perp_bundle(
                    symbols=lh,
                    lookback_days=spec.universe.lookback_days,
                    interval="1h",
                )
                hp = hb["prices"].reindex(lp.index).ffill().add_suffix("_PERP")
                hf = (
                    hb["funding"]
                    .reindex(lp.index)
                    .ffill()
                    .fillna(0.0)
                    .add_suffix("_PERP")
                )
                hpos = _bpt_hp(cpos, m2hs=m2hs, hedge_symbols=lh, hr=hr)
                cp = pd.concat([lp, hp], axis=1).sort_index()
                cpos = (
                    pd.concat([cpos, hpos], axis=1)
                    .reindex(cp.index)
                    .ffill()
                    .fillna(0.0)
                )
                funding = (
                    pd.concat([funding, hf], axis=1)
                    .reindex(cp.index)
                    .ffill()
                    .fillna(0.0)
                )
                src = f"{src}+{hb['source']}"
        return CompiledChild(
            prices=cp,
            target_positions=cpos,
            funding_rates=funding,
            signal_score=score,
            signal_components=sc,
            regime_gate_mask=rgm,
            metadata=_bsm(
                spec,
                capabilities=caps,
                execution_profile=ep,
                diagnostic_adapter=da,
                policy_schema=ps,
                features=spec.features,
                feature_hash=_fh(spec.features),
                source=src,
                bundle_as_of=lb.get("bundle_as_of"),
                asset_breadth=len(lp.columns),
                prices=cp,
                rg_meta=rgm_meta,
                markets=list(lp.columns),
                hedge_mode=hm,
                hedge_ratio=hr,
                hedge_symbols=lh,
                selection_count=int(
                    spec.params.get("selection_count", d.get("selection_count", 2)),
                ),
                gross_target=float(
                    spec.params.get("gross_target", d.get("gross_target", 0.8)),
                ),
            ),
        )
    raise ValueError(f"Unsupported spec family: {spec.family}")


# backward compat aliases for tests
_align_cross_sectional_components = _align_cs_comp
_align_cross_sectional_frame = _align_cs
_build_lending_price_frames = _blpf
_build_pair_positions = _bpp
_build_pair_trade_positions = _bptp
_build_pt_hedge_positions = _bpt_hp
_build_ranked_positions = _brp
_build_shared_metadata = _bsm
_cross_sectional_zscore = _cs_z
_ensure_single_eligible_scores = _ensure_elig
_ffill_within_observed_window = _ffill_wow
_gate_mask_from_frame = _gmff
_history_bounds = _hb
_lending_raw_frames = _lrf
_mask_feature_frames = _mask_ff
_pair_policy_parameters = _ppp
_pair_raw_frames = _pair_rf
_perp_global_raw_frames = _pgrf
_perp_raw_frames = _prf
_prepare_pt_market_frames = _ppt_mf
_pt_lifecycle_metadata = _pt_lm
_pt_raw_frames = _pt_rf
_ranked_policy_parameters = _rpp
_resolve_gate_mask = _rgm
_resolve_regime_gates = _rrg
_shared_scoring_pipeline = _ssp
_time_series_zscore = _ts_z
_weighted_scoring = _ws
