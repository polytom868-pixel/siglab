from __future__ import annotations

import math
from typing import Any, cast

import numpy as np
import pandas as pd

from siglab.utils import dget, safe_float as _sf


def mean_pairwise_rolling_corr(returns: pd.DataFrame, *, window: int) -> pd.Series:
    cols = list(returns.columns)
    if not cols:
        return pd.Series(dtype=float)
    rows: list[pd.Series] = []
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            rows.append(returns.iloc[:, i].rolling(window).corr(returns.iloc[:, j]))
    return pd.concat(rows, axis=1).mean(axis=1) if rows else pd.Series(dtype=float)


def _mpsigs(frame: pd.DataFrame, *, eps_: float = 1e-09) -> pd.Series:
    arr = frame.to_numpy(dtype=float, na_value=np.nan)
    result: list[tuple[tuple[str, int], ...]] = []
    for row in arr:
        masked = np.where(np.isfinite(row), row, 0.0)
        pos = np.abs(masked) > eps_
        if not pos.any():
            result.append(())
            continue
        col_idx = np.where(pos)[0]
        signs = np.sign(masked[pos]).astype(int)
        cols = tuple(
            (frame.columns[int(c)], int(s))
            for c, s in zip(col_idx, signs)
        )
        result.append(cols)
    return pd.Series(result, index=frame.index, dtype=object)






def _rdl_np(values: np.ndarray, cols: list[str], eps_: float = 1e-09) -> str:
    vals = np.where(np.isfinite(values), values, 0.0)
    pos = np.abs(vals) > eps_
    if not pos.any():
        return "flat"
    active_cols = [cols[i] for i in range(len(cols)) if pos[i]]
    active_signs = np.sign(vals[pos]).astype(int)
    longs = sum(1 for s in active_signs if s > 0)
    shorts = sum(1 for s in active_signs if s < 0)
    if longs > 0 and shorts == 0:
        return "long"
    if shorts > 0 and longs == 0:
        return "short"
    if longs > 0 and shorts > 0:
        return "mixed"
    return "flat" if (longs == 0 and shorts == 0) else "mixed"


def _ppeps(*, tw: pd.DataFrame, r: pd.Series) -> list[dict[str, Any]]:
    if tw.empty:
        return []
    sigs = _mpsigs(tw)
    eps: list[dict[str, Any]] = []
    csig: tuple[tuple[str, int], ...] = ()
    sts: pd.Timestamp | None = None
    pts: pd.Timestamp | None = None
    def _addep(es: pd.Timestamp, ee: pd.Timestamp, sig: tuple[tuple[str, int], ...]) -> None:
        if not sig:
            return
        et = tw.loc[es:ee]
        if et.empty:
            return
        er_ = pd.to_numeric(r.loc[es:ee], errors="coerce").dropna()
        sr = et.iloc[0]
        c = pd.to_numeric(sr, errors="coerce").fillna(0.0)
        aa = list(c.index[c.abs() > 1e-09])
        la = list(c.index[c > 1e-09])
        sa = list(c.index[c < -1e-09])
        ge = pd.to_numeric(et.abs().sum(axis=1), errors="coerce").fillna(0.0)
        ne = pd.to_numeric(et.sum(axis=1), errors="coerce").fillna(0.0)
        aac = et.abs().gt(1e-09).sum(axis=1).astype(float) if not et.empty else pd.Series(dtype=float)
        eps.append({
            "direction": _rdl_np(sr.to_numpy(dtype=float, na_value=np.nan), list(sr.index), eps_=1e-09),
            "start_timestamp": es.isoformat(),
            "end_timestamp": ee.isoformat(),
            "bars": int(er_.shape[0]),
            "total_return": _sf(cast(Any, (1.0 + er_).prod()) - 1.0 if not er_.empty else 0.0),
            "active_assets": aa, "long_assets": la, "short_assets": sa,
            "active_asset_count": _sf(aac.median()),
            "gross_exposure": _sf(ge.median()),
            "net_exposure": _sf(ne.median()),
        })
    for tr, sig in sigs.items():
        ts: pd.Timestamp | None = cast(pd.Timestamp | None, tr)
        if not csig and sig:
            csig = sig
            sts = ts
        elif csig and sig != csig:
            if sts is not None and pts is not None:
                _addep(sts, pts, csig)
            csig = sig
            sts = ts if sig else None
        pts = ts
    if csig and sts is not None and pts is not None:
        _addep(sts, pts, csig)
    return eps


def _episode_stats(matched: list[dict[str, Any]], returns: list[float]) -> dict[str, Any]:
    cnt: dict[str, int] = {}
    for e in matched:
        d = e.get("direction", "")
        if d:
            cnt[d] = cnt.get(d, 0) + 1
    return {
        "trade_count": len(matched),
        "win_rate": _sf(
            sum(1 for v in returns if v > 0.0) / len(returns) if returns else None,
        ),
        "median_return": _sf(float(np.median(returns))) if returns else None,
        "direction_counts": cnt,
    }


def _hpb(tw: pd.DataFrame, r: pd.Series) -> list[dict[str, Any]]:
    eps = _ppeps(tw=tw, r=r)
    specs = [
        ("bars_1_6", 1, 6),
        ("bars_7_24", 7, 24),
        ("bars_25_72", 25, 72),
        ("bars_73_plus", 73, None),
    ]
    rows: list[dict[str, Any]] = []
    for label, lo, hi in specs:
        matched = [
            e
            for e in eps
            if int(e["bars"]) >= lo and (hi is None or int(e["bars"]) <= hi)
        ]
        rb = [
            float(e["total_return"])
            for e in matched
            if e.get("total_return") is not None
        ]
        rows.append({
            "label": label,
            **(_episode_stats(matched, rb)),
            "median_bars": _sf(float(np.median([int(e["bars"]) for e in matched]))) if matched else None,
        })
    return rows

def _sps(
    *,
    r: pd.Series,
    gross_exposure: pd.Series,
    mask: pd.Series,
    label: str,
) -> dict[str, Any]:
    am = mask.reindex(r.index).fillna(value=False).astype(dtype=bool)
    subset = r[am].dropna()
    esub = gross_exposure.reindex(r.index).fillna(0.0)[am]
    if subset.empty:
        return {"label": label, "available": False, "sample_bars": 0, "active_bars": 0}
    tr = float(cast(Any, (1.0 + subset).prod()) - 1.0)
    ab = int((esub > 1e-09).sum())
    sh: float | None = None
    mdd: float | None = None
    v = float(subset.std())
    if math.isfinite(v) and v > 0.0:
        sh = _sf(float(subset.mean()) / v * math.sqrt(365.25 * 24.0))
    eq = (1.0 + subset).cumprod()
    dd_ = eq.div(eq.cummax()).sub(1.0)
    mdd = _sf(float(dd_.min()))
    return {
        "label": label,
        "available": True,
        "sample_bars": int(subset.shape[0]),
        "active_bars": ab,
        "active_bar_fraction": _sf(ab / max(1, int(subset.shape[0]))),
        "avg_gross_exposure": _sf(esub.mean()),
        "mean_return": _sf(subset.mean()),
        "total_return": _sf(tr),
        "sharpe": sh,
        "max_drawdown": mdd,
        "positive_bar_fraction": _sf(float((subset > 0.0).mean())),
    }


def _prs(
    *,
    prices: pd.DataFrame,
    tw: pd.DataFrame,
    funding_rates: pd.DataFrame | None,
) -> dict[str, Any]:
    if prices.empty:
        return {"available": False}
    prices = prices.sort_index()
    r1h = prices.pct_change()
    r24h = prices.pct_change(24)
    funding = (
        funding_rates.reindex(prices.index).ffill().fillna(0.0)
        if funding_rates is not None
        else pd.DataFrame(np.nan, index=prices.index, columns=prices.columns)
    )
    ta = tw.reindex(prices.index).ffill().fillna(0.0)
    mt = r24h.mean(axis=1)
    mv = r1h.rolling(168).std().mean(axis=1)
    fl = funding.mean(axis=1)
    fd = funding.std(axis=1)
    br = r24h.gt(0.0).mean(axis=1)
    cm = mean_pairwise_rolling_corr(r1h, window=72)
    ge = ta.abs().sum(axis=1)
    ne = ta.sum(axis=1)
    aac = ta.abs().gt(1e-09).sum(axis=1).astype(float)
    aw = ta.abs()
    conc = (
        aw.div(aw.sum(axis=1).replace(0.0, np.nan), axis=0)
        .pow(2)
        .sum(axis=1)
        .fillna(0.0)
    )
    ta_arr = ta.to_numpy(dtype=float, na_value=0.0)
    tc = list(ta.columns)
    pd_ = pd.Series(
        [_rdl_np(ta_arr[i], tc) for i in range(len(ta_arr))],
        index=ta.index,
        dtype=object,
    )
    th = {
        "market_volatility_median": _sf(mv.dropna().median(), default=None),
        "funding_level_median": _sf(fl.dropna().median(), default=None),
        "funding_dispersion_median": _sf(fd.dropna().median(), default=None),
        "breadth_median": _sf(br.dropna().median(), default=None),
        "co_movement_median": _sf(cm.dropna().median(), default=None),
        "concentration_median": _sf(conc.dropna().median(), default=None),
    }
    state: dict[str, Any] = {
        "available": True,
        "index": prices.index,
        "market_trend": mt,
        "market_volatility": mv,
        "funding_level": fl,
        "funding_dispersion": fd,
        "breadth": br,
        "co_movement": cm,
        "gross_exposure": ge,
        "net_exposure": ne,
        "active_asset_count": aac,
        "concentration": conc,
        "position_direction": pd_,
        "thresholds": th,
    }
    cols = list(prices.columns)
    if len(cols) >= 2:
        a1s, a2s = str(cols[0]), str(cols[1])
        a1r = prices[a1s].pct_change()
        a2r = prices[a2s].pct_change()
        pr_ = prices[a1s].div(prices[a2s]).replace([np.inf, -np.inf], np.nan)
        state.update(
            {
                "asset_1_symbol": a1s,
                "asset_2_symbol": a2s,
                "pair_volatility": pr_.pct_change().rolling(72).std(),
                "pair_correlation": a1r.rolling(72).corr(a2r),
                "pair_direction": a1r.rolling(24).mean().sub(a2r.rolling(24).mean()),
            },
        )
        state["thresholds"].update(
            {
                "pair_volatility_median": _sf(
                    state["pair_volatility"].dropna().median(),
                    default=None,
                ),
                "pair_correlation_median": _sf(
                    state["pair_correlation"].dropna().median(),
                    default=None,
                ),
            },
        )
    return state


def _prd(
    *,
    prices: pd.DataFrame,
    tw: pd.DataFrame,
    funding_rates: pd.DataFrame | None,
    r: pd.Series,
) -> dict[str, Any]:
    rs = _prs(prices=prices, tw=tw, funding_rates=funding_rates)
    if not rs.get("available"):
        return {"available": False}
    th = rs.get("thresholds", {}) or {}

    def _bs(label, mask, hi_label, lo_label):
        median_val = th.get(f"{label}_median")
        has_median = median_val is not None
        median_float = float(median_val) if has_median else 0.0
        return [
            _sps(
                r=r,
                gross_exposure=rs["gross_exposure"],
                mask=mask >= 0.0
                if label == "market_trend"
                else mask < median_float
                if has_median
                else pd.Series(data=False, index=prices.index),
                label=hi_label,
            ),
            _sps(
                r=r,
                gross_exposure=rs["gross_exposure"],
                mask=mask < 0.0
                if label == "market_trend"
                else mask >= median_float
                if has_median
                else pd.Series(data=False, index=prices.index),
                label=lo_label,
            ),
        ]

    bs_ = {
        "market_trend": _bs(
            "market_trend",
            rs["market_trend"],
            "market_uptrend",
            "market_downtrend",
        ),
        "market_volatility": _bs(
            "market_volatility",
            rs["market_volatility"],
            "high_volatility",
            "low_volatility",
        ),
        "funding_level": _bs(
            "funding_level",
            rs["funding_level"],
            "high_funding",
            "low_funding",
        ),
        "funding_dispersion": _bs(
            "funding_dispersion",
            rs["funding_dispersion"],
            "funding_dispersed",
            "funding_compressed",
        ),
    }
    if "pair_volatility" in rs:
        bs_["pair_volatility"] = _bs(
            "pair_volatility",
            rs["pair_volatility"],
            "high_volatility",
            "low_volatility",
        )
        bs_["pair_correlation"] = _bs(
            "pair_correlation",
            rs["pair_correlation"],
            "high_correlation",
            "low_correlation",
        )
        bs_["pair_direction"] = [
            _sps(
                r=r,
                gross_exposure=rs["gross_exposure"],
                mask=rs["pair_direction"] >= 0.0,
                label="asset_1_leading",
            ),
            _sps(
                r=r,
                gross_exposure=rs["gross_exposure"],
                mask=rs["pair_direction"] < 0.0,
                label="asset_2_leading",
            ),
        ]
    return {
        "available": True,
        "asset_1_symbol": rs.get("asset_1_symbol"),
        "asset_2_symbol": rs.get("asset_2_symbol"),
        "thresholds": th,
        "bar_slices": bs_,
        "holding_period_buckets": _hpb(tw, r),
    }


def _fraction_while_flat(
    sem2d: np.ndarray[tuple[int, int], np.dtype[np.bool_]],
    fm1: object,
) -> float | None:
    """Mean of any-axis-1 per row, restricted to rows where fm1 is True."""
    if hasattr(fm1, "to_numpy"):
        fm1_arr: np.ndarray = fm1.to_numpy()  # type: ignore[attr-defined]
    else:
        fm1_arr = np.asarray(fm1)
    if not bool(fm1_arr.any()):
        return None
    mask = fm1_arr.astype(bool)
    any_per_row = cast(np.ndarray, sem2d.any(axis=1))
    return float(any_per_row[mask].mean())


def _pgd(
    *,
    signal_score: pd.DataFrame | None,
    tw: pd.DataFrame,
    compiled_metadata: dict[str, Any],
    ei: int | None,
    regime_gate_mask: pd.Series | None = None,
) -> dict[str, Any]:
    if signal_score is None or signal_score.empty or tw.empty:
        return {}
    lim = (
        len(signal_score.index)
        if ei is None
        else max(0, min(len(signal_score.index), int(ei)))
    )
    if lim <= 1:
        return {}
    sf = signal_score.iloc[:lim].fillna(0.0)
    tf = tw.iloc[:lim].fillna(0.0)
    sa = sf.to_numpy(dtype=float, na_value=0.0)
    ta = tf.to_numpy(dtype=float, na_value=0.0)
    ssig = np.sign(sa)
    psig = np.sign(ta)
    ta_abs = np.abs(ta)
    am1 = ta_abs.sum(axis=1) > 1e-09
    fm1 = ~am1
    eas = float(
        compiled_metadata.get(
            "entry_abs_score",
            compiled_metadata.get("min_abs_score", 0.0),
        ),
    )
    exas = float(compiled_metadata.get("exit_abs_score", max(0.0, eas * 0.5)))
    fas = float(compiled_metadata.get("flip_abs_score", eas))
    sa_abs = np.abs(sa)
    sem2d = sa_abs >= eas
    sf2d = sa_abs >= fas
    seb2d = sa_abs < exas
    pm = sf.shape[1] == 1 and tf.shape[1] >= 2
    sc = list(sf.columns)
    psig_ = _mpsigs(tf)
    if pm:
        sv = sa[:, 0]
        asig: list[tuple[tuple[str, int], ...]] = [
            (
                (("long_asset_1_short_asset_2", 1),)
                if v >= eas
                else (("short_asset_1_long_asset_2", 1),)
                if v <= -eas
                else ()
            )
            for v in sv
        ]
        ass = pd.Series(asig, index=sf.index, dtype=object)
    else:
        asig = []
        for i in range(len(sa)):
            row = sa[i]
            active = [
                (sc[j], int(np.sign(row[j])))
                for j in range(len(sc))
                if abs(row[j]) >= eas
            ]
            if active:
                active.sort(key=lambda x: x[0])
            asig.append(tuple(active))
        ass = pd.Series(asig, index=sf.index, dtype=object)
    ss_ = ass.shift(1)
    pss_ = psig_.shift(1)
    sflips = (ass != ss_) & ass.astype(bool) & ss_.astype(bool)
    pflips = (psig_ != pss_) & psig_.astype(bool) & pss_.astype(bool)
    aaf = None
    if am1.any():
        if pm:
            aal = (sa[:, 0] * np.sign(ta[:, 0]) > 0.0)[am1].tolist()
        else:
            seq = (ssig == psig).astype(float)
            acm = ta_abs > 1e-09
            seq_m = seq * acm
            rc = np.maximum(acm.sum(axis=1, where=~np.isnan(acm)), 1)
            ra = seq_m.sum(axis=1) / rc
            aal = ra[am1].tolist()
        if aal:
            aaf = _sf(float(np.mean(aal)))
    bt: list[str] = []
    ef = float(sem2d.any(axis=1).mean())
    af = float(am1.mean())
    pfr = float(pflips.mean()) if len(pflips.index) > 1 else 0.0
    if ef < 0.05:
        bt.append("sparse_entry_signal")
    if af < 0.1:
        bt.append("low_active_fraction")
    if pfr > 0.2:
        bt.append("high_position_flip_rate")
    if aaf is not None and aaf < 0.55:
        bt.append("weak_score_alignment")
    rgs: dict[str, Any] | None = None
    if regime_gate_mask is not None:
        gm = regime_gate_mask.reindex(sf.index).ffill().fillna(value=False).astype(dtype=bool)
        rgs = {
            "configured": True,
            "active_fraction": _sf(float(gm.mean())),
            "blocked_while_flat_fraction": _sf(
                float((~gm)[fm1].mean()) if bool(fm1.any()) else None,
            ),
            "broken_while_active_fraction": _sf(
                float((~gm)[am1].mean()) if bool(am1.any()) else None,
            ),
            "exit_on_break": dget(compiled_metadata, "regime_gates", "exit_on_break", default=True),
            "entry": dget(compiled_metadata, "regime_gates", "entry") or [],
        }
        afg = _sf(rgs.get("active_fraction"))
        if afg is not None and afg < 0.3:
            bt.append("restrictive_regime_gate")
    mav = (
        2.0
        if pm
        else (
            float(np.median((ta_abs > 1e-09).sum(axis=1).astype(float)[am1]))
            if bool(am1.any())
            else None
        )
    )
    return {
        "policy": {
            "entry_abs_score": _sf(eas),
            "exit_abs_score": _sf(exas),
            "flip_abs_score": _sf(fas),
            "max_holding_bars": int(compiled_metadata.get("max_holding_bars", 0) or 0),
            "cooldown_bars": int(compiled_metadata.get("cooldown_bars", 0) or 0),
            "signal_leverage_scale": _sf(
                compiled_metadata.get("signal_leverage_scale"),
            ),
        },
        "active_bar_fraction": _sf(af),
        "flat_bar_fraction": _sf(float(fm1.mean())),
        "entry_signal_bar_fraction": _sf(ef),
        "flip_signal_bar_fraction": _sf(float(sf2d.any(axis=1).mean())),
        "inside_exit_band_fraction": _sf(float(seb2d.all(axis=1).mean())),
        "score_sign_flip_rate": _sf(
            float(sflips.mean()) if len(sflips.index) > 1 else 0.0,
        ),
        "position_flip_rate": _sf(pfr),
        "entry_signal_while_flat_fraction": _sf(
            _fraction_while_flat(sem2d, fm1),
        ),
        "score_alignment_when_active": aaf,
        "median_active_asset_count": _sf(mav),
        "regime_gates": rgs,
        "bottleneck_tags": bt,
    }
