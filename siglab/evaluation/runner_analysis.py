from __future__ import annotations

import math
from typing import Any, cast

import numpy as np
import pandas as pd

from siglab.utils import safe_float as _sf


def mean_pairwise_rolling_corr(returns: pd.DataFrame, *, window: int) -> pd.Series:
    cols = list(returns.columns)
    if not cols:
        return pd.Series(dtype=float)
    if len(cols) == 1:
        return pd.Series(1.0, index=returns.index, dtype=float)
    rows: list[pd.Series] = []
    for li in range(len(cols)):
        for ri in range(li + 1, len(cols)):
            rows.append(returns.iloc[:, li].rolling(window).corr(returns.iloc[:, ri]))
    return pd.concat(rows, axis=1).mean(axis=1) if rows else pd.Series(dtype=float)


def pre_audit_trade_episodes(cr: dict[str, Any]) -> list[dict[str, Any]]:
    eps = cr.get("trade_episodes") or []
    if not eps:
        return []
    vs = {**cr.get("visual_split", {})}
    ast = None
    for w in vs.get("ranges") or []:
        if str(w.get("kind") or "") == "audit_holdout":
            ast = pd.Timestamp(w.get("start_timestamp"))
            break
    if ast is None:
        return [e for e in eps if isinstance(e, dict)]
    filt: list[dict[str, Any]] = []
    for e in eps:
        if not isinstance(e, dict):
            continue
        ets = e.get("end_timestamp") or e.get("start_timestamp")
        if not ets:
            continue
        if pd.Timestamp(ets) >= ast:
            continue
        filt.append(e)
    return filt


def _svals(pld: dict[str, Any] | None, *, ei: int | None = None) -> list[float]:
    vr = (pld or {}).get("values") or []
    if ei is not None:
        vr = vr[: max(0, min(len(vr), int(ei)))]
    return [float(v) for v in vr if v is not None]


def _sfp(pld: dict[str, Any] | None, *, ei: int | None = None) -> pd.Series:
    iv = (pld or {}).get("index") or []
    rv = (pld or {}).get("values") or []
    lim = len(rv) if ei is None else max(0, min(len(rv), int(ei)))
    if lim <= 0:
        return pd.Series(dtype=float)
    s = pd.Series(
        pd.to_numeric(pd.Series(rv[:lim], dtype="float64"), errors="coerce").to_numpy(),
        index=pd.to_datetime(iv[:lim], errors="coerce"),
    )
    s = s[~pd.isna(s.index)]
    return s.sort_index()


def _mpsigs(frame: pd.DataFrame, *, eps_: float = 1e-09) -> pd.Series:
    arr = frame.to_numpy(dtype=float, na_value=np.nan)
    cols = list(frame.columns)
    result: list[tuple[tuple[str, int], ...]] = []
    for i in range(len(arr)):
        row = arr[i]
        active = [
            (cols[j], int(np.sign(row[j])))
            for j in range(len(cols))
            if not np.isnan(row[j]) and abs(row[j]) > eps_
        ]
        if active:
            active.sort(key=lambda x: x[0])
        result.append(tuple(active))
    return pd.Series(result, index=frame.index, dtype=object)


def _eassets(
    row: pd.Series,
    *,
    eps_: float = 1e-09,
) -> tuple[list[str], list[str], list[str]]:
    c = pd.to_numeric(row, errors="coerce").fillna(0.0)
    active = [str(k) for k, v in c.items() if abs(float(v)) > eps_]
    longs = [str(k) for k, v in c.items() if float(v) > eps_]
    shorts = [str(k) for k, v in c.items() if float(v) < -eps_]
    return (active, longs, shorts)


def _rdl(row: pd.Series, *, eps_: float = 1e-09) -> str:
    c = pd.to_numeric(row, errors="coerce").fillna(0.0)
    active, longs, shorts = _eassets(c, eps_=eps_)
    if not active:
        return "flat"
    if (
        len(c.index) >= 2
        and len(active) == 2
        and (set(active) == set(map(str, c.index[:2])))
    ):
        fst, snd = float(c.iloc[0]), float(c.iloc[1])
        if fst > eps_ and snd < -eps_:
            return "long_asset_1_short_asset_2"
        if fst < -eps_ and snd > eps_:
            return "short_asset_1_long_asset_2"
    gross = float(c.abs().sum())
    net = float(c.sum())
    if longs and shorts and gross > 0.0 and abs(net) <= gross * 0.2:
        return "market_neutral"
    if net > eps_ or (longs and not shorts):
        return "net_long"
    if net < -eps_ or (shorts and not longs):
        return "net_short"
    return "mixed"


def _rdl_np(values: np.ndarray, cols: list[str], eps_: float = 1e-09) -> str:
    vals = np.where(np.isfinite(values), values, 0.0)
    am = np.abs(vals) > eps_
    if not am.any():
        return "flat"
    ai = np.where(am)[0]
    li = ai[vals[ai] > eps_]
    si = ai[vals[ai] < -eps_]
    active = [cols[j] for j in ai]
    longs = [cols[j] for j in li]
    shorts = [cols[j] for j in si]
    if len(cols) >= 2 and len(active) == 2 and set(active) == {cols[0], cols[1]}:
        f, s = vals[0], vals[1]
        if f > eps_ and s < -eps_:
            return "long_asset_1_short_asset_2"
        if f < -eps_ and s > eps_:
            return "short_asset_1_long_asset_2"
    gross = float(np.abs(vals).sum())
    net = float(vals.sum())
    if longs and shorts and gross > 0.0 and abs(net) <= gross * 0.2:
        return "market_neutral"
    if net > eps_ or (longs and not shorts):
        return "net_long"
    if net < -eps_ or (shorts and not longs):
        return "net_short"
    return "mixed"


def _ppeps(*, tw: pd.DataFrame, r: pd.Series) -> list[dict[str, Any]]:
    if tw.empty:
        return []
    sigs = _mpsigs(tw)
    eps: list[dict[str, Any]] = []
    csig: tuple[tuple[str, int], ...] = ()
    sts: pd.Timestamp | None = None
    pts: pd.Timestamp | None = None

    def _addep(
        es: pd.Timestamp,
        ee: pd.Timestamp,
        sig: tuple[tuple[str, int], ...],
    ) -> None:
        if not sig:
            return
        et = tw.loc[es:ee]
        if et.empty:
            return
        er_ = pd.to_numeric(r.loc[es:ee], errors="coerce").dropna()
        sr = et.iloc[0]
        aa, la, sa = _eassets(sr)
        ge = pd.to_numeric(et.abs().sum(axis=1), errors="coerce").fillna(0.0)
        ne = pd.to_numeric(et.sum(axis=1), errors="coerce").fillna(0.0)
        aac = (
            et.abs().gt(1e-09).sum(axis=1).astype(float)
            if not et.empty
            else pd.Series(dtype=float)
        )
        eps.append(
            {
                "direction": _rdl(sr),
                "start_timestamp": es.isoformat(),
                "end_timestamp": ee.isoformat(),
                "bars": int(er_.shape[0]),
                "total_return": _sf(
                    cast(Any, (1.0 + er_).prod()) - 1.0 if not er_.empty else 0.0,
                ),
                "active_assets": aa,
                "long_assets": la,
                "short_assets": sa,
                "active_asset_count": _sf(aac.median()),
                "gross_exposure": _sf(ge.median()),
                "net_exposure": _sf(ne.median()),
            },
        )

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
        rows.append(
            {
                "label": label,
                "trade_count": len(matched),
                "median_bars": _sf(float(np.median([int(e["bars"]) for e in matched])))
                if matched
                else None,
                "median_return": _sf(float(np.median(rb))) if rb else None,
                "win_rate": _sf(sum(1 for v in rb if v > 0.0) / len(rb))
                if rb
                else None,
                "direction_counts": _edc(matched),
            },
        )
    return rows


def _edc(te: list[dict[str, Any]]) -> dict[str, int]:
    cnt: dict[str, int] = {}
    for e in te:
        d = str(e.get("direction") or "").strip()
        if d:
            cnt[d] = cnt.get(d, 0) + 1
    return cnt


def _sps(
    *,
    r: pd.Series,
    gross_exposure: pd.Series,
    mask: pd.Series,
    label: str,
) -> dict[str, Any]:
    am = mask.reindex(r.index).fillna(False).astype(bool)
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


def _lts(
    idx: pd.Index,
    ts: str | float | pd.Timestamp | None,
) -> pd.Timestamp | None:
    if len(idx) == 0 or ts is None:
        return None
    t = pd.Timestamp(ts)
    if isinstance(idx, pd.DatetimeIndex):
        if idx.tz is None:
            if t.tzinfo is not None:
                t = t.tz_convert(None)
        elif t.tzinfo is None:
            t = t.tz_localize(idx.tz)
        else:
            t = t.tz_convert(idx.tz)
    if t in idx:
        return pd.Timestamp(t)
    p = int(idx.searchsorted(t, side="right")) - 1
    if p < 0:
        return None
    if p >= len(idx):
        p = len(idx) - 1
    return pd.Timestamp(idx[p])


def _rbl(v: float | None, threshold: float | None, hi: str, lo: str) -> str | None:
    return None if v is None or threshold is None else hi if v >= threshold else lo


def _pair_regime_snapshot(
    *,
    regime_state: dict[str, Any],
    timestamp: str | float | pd.Timestamp | None,
    target_weights: pd.DataFrame | None,
) -> dict[str, Any]:
    if not regime_state.get("available"):
        return {}
    ats = _lts(regime_state["index"], timestamp)
    if ats is None:
        return {}
    th = {**regime_state.get("thresholds", {})}
    mtv = _sf(regime_state["market_trend"].get(ats))
    mvv = _sf(regime_state["market_volatility"].get(ats))
    flv = _sf(regime_state["funding_level"].get(ats))
    fdv = _sf(regime_state["funding_dispersion"].get(ats))
    bv = _sf(regime_state["breadth"].get(ats))
    cmv = _sf(regime_state["co_movement"].get(ats))
    gev = _sf(regime_state["gross_exposure"].get(ats))
    nev = _sf(regime_state["net_exposure"].get(ats))
    aacv = _sf(regime_state["active_asset_count"].get(ats))
    conc = _sf(regime_state["concentration"].get(ats))
    pd_ = str(regime_state["position_direction"].get(ats) or "flat")
    mvt = th.get("market_volatility_median")
    flt = th.get("funding_level_median")
    ft = th.get("funding_dispersion_median")
    bt = th.get("breadth_median")
    cmt = th.get("co_movement_median")
    ct = th.get("concentration_median")
    if target_weights is not None and not target_weights.empty:
        er = target_weights.reindex(regime_state["index"]).ffill().fillna(0.0)
        gev = _sf(er.abs().sum(axis=1).get(ats))
        nev = _sf(er.sum(axis=1).get(ats))
    snap: dict[str, Any] = {
        "market_trend_label": _rbl(mtv, 0.0, "market_uptrend", "market_downtrend"),
        "market_trend_24h": mtv,
        "market_volatility_label": _rbl(mvv, mvt, "high_volatility", "low_volatility"),
        "market_volatility_168h": mvv,
        "funding_level_label": _rbl(flv, flt, "high_funding", "low_funding"),
        "funding_level_72h": flv,
        "funding_dispersion_label": _rbl(
            fdv,
            ft,
            "funding_dispersed",
            "funding_compressed",
        ),
        "funding_dispersion_72h": fdv,
        "breadth_label": _rbl(bv, bt, "broad_participation", "weak_participation"),
        "breadth_24h": bv,
        "co_movement_label": _rbl(cmv, cmt, "high_co_movement", "low_co_movement"),
        "co_movement_72h": cmv,
        "concentration_label": _rbl(conc, ct, "concentrated", "diversified"),
        "concentration": conc,
        "position_direction": pd_,
        "position_structure_label": pd_,
        "gross_exposure": gev,
        "net_exposure": nev,
        "active_asset_count": aacv,
    }
    if "pair_volatility" in regime_state:
        pvv = _sf(regime_state["pair_volatility"].get(ats))
        pcv = _sf(regime_state["pair_correlation"].get(ats))
        pdv = _sf(regime_state["pair_direction"].get(ats))
        snap.update(
            {
                "pair_volatility_label": _rbl(
                    pvv,
                    th.get("pair_volatility_median"),
                    "high_volatility",
                    "low_volatility",
                ),
                "pair_volatility_72h": pvv,
                "pair_correlation_label": _rbl(
                    pcv,
                    th.get("pair_correlation_median"),
                    "high_correlation",
                    "low_correlation",
                ),
                "pair_correlation_72h": pcv,
                "pair_direction_label": _rbl(
                    pdv,
                    0.0,
                    "asset_1_leading",
                    "asset_2_leading",
                ),
                "pair_direction_24h": pdv,
            },
        )
    return snap


def _ptrwr(
    *,
    tw: pd.DataFrame,
    r: pd.Series,
    regime_state: dict[str, Any],
) -> list[dict[str, Any]]:
    eps = _ppeps(tw=tw, r=r)
    ann: list[dict[str, Any]] = []
    for e in eps:
        ann.append(
            {
                **e,
                "entry_regime": _pair_regime_snapshot(
                    regime_state=regime_state,
                    timestamp=e.get("start_timestamp"),
                    target_weights=tw,
                ),
                "exit_regime": _pair_regime_snapshot(
                    regime_state=regime_state,
                    timestamp=e.get("end_timestamp"),
                    target_weights=tw,
                ),
            },
        )
    return ann


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
    th = {**rs.get("thresholds", {})}

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
                else pd.Series(False, index=prices.index),
                label=hi_label,
            ),
            _sps(
                r=r,
                gross_exposure=rs["gross_exposure"],
                mask=mask < 0.0
                if label == "market_trend"
                else mask >= median_float
                if has_median
                else pd.Series(False, index=prices.index),
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


def _trp(te: list[dict[str, Any]]) -> dict[str, Any]:
    if not te:
        return {}
    lks: list[str] = []
    for e in te:
        er = {**e.get("entry_regime", {})}
        lks.extend(k for k, v in er.items() if k.endswith("_label") and v)
    dims = {k.removesuffix("_label"): k for k in sorted(set(lks))}
    rp: dict[str, Any] = {}
    for dim, lk in dims.items():
        rows: list[dict[str, Any]] = []
        by_label: dict[str, list[dict[str, Any]]] = {}
        for e in te:
            lbl = str((e.get("entry_regime") or {}).get(lk) or "").strip()
            if lbl:
                by_label.setdefault(lbl, []).append(e)
        for lbl, matched in by_label.items():
            returns = [
                float(e["total_return"])
                for e in matched
                if e.get("total_return") is not None
            ]
            bars = [
                float(e["bars"])
                for e in matched
                if _sf(e.get("bars"), default=None) is not None
            ]
            rows.append(
                {
                    "label": lbl,
                    "trade_count": len(matched),
                    "win_rate": _sf(
                        sum(1 for v in returns if v > 0.0) / len(returns)
                        if returns
                        else None,
                    ),
                    "avg_return": _sf(sum(returns) / len(returns) if returns else None),
                    "median_return": _sf(
                        float(np.median(returns)) if returns else None,
                    ),
                    "median_hold_bars": _sf(float(np.median(bars)) if bars else None),
                    "direction_counts": _edc(matched),
                },
            )
        rows.sort(
            key=lambda r: (
                float(r.get("avg_return") or -1e9),
                int(r.get("trade_count") or 0),
            ),
            reverse=True,
        )
        if rows:
            rp[dim] = {
                "rows": rows,
                "best_label": rows[0]["label"],
                "worst_label": min(
                    rows,
                    key=lambda r: float(r.get("avg_return") or 1e9),
                )["label"],
            }
    return rp


def _wrs(
    *,
    regime_state: dict[str, Any],
    start_timestamp: pd.Timestamp,
    end_timestamp: pd.Timestamp,
) -> dict[str, Any]:
    if not regime_state.get("available"):
        return {}
    iv = regime_state.get("index")
    idx = pd.DatetimeIndex(iv if iv is not None else [])
    if idx.empty:
        return {}
    mask = (idx >= start_timestamp) & (idx <= end_timestamp)
    if not bool(mask.any()):
        return {}

    def _mv(s):
        v = pd.to_numeric(s.loc[mask], errors="coerce").dropna()
        return _sf(v.mean()) if not v.empty else None

    th = {**regime_state.get("thresholds", {})}
    mt = _mv(regime_state["market_trend"])
    mv_ = _mv(regime_state["market_volatility"])
    fl = _mv(regime_state["funding_level"])
    fdv = _mv(regime_state["funding_dispersion"])
    br = _mv(regime_state["breadth"])
    cm = _mv(regime_state["co_movement"])
    conc = _mv(regime_state["concentration"])
    ds = pd.Series(regime_state["position_direction"], index=idx).loc[mask]
    dc = ds.value_counts().to_dict()
    dpd = max(
        (
            (str(dir_label), int(c))
            for dir_label, c in dc.items()
            if str(dir_label) != "flat"
        ),
        key=lambda x: x[1],
        default=(None, 0),
    )[0]
    pl: dict[str, Any] = {
        "market_trend_label": "market_uptrend"
        if mt is not None and mt >= 0.0
        else "market_downtrend"
        if mt is not None
        else None,
        "avg_market_trend_24h": mt,
        "market_volatility_label": "high_volatility"
        if mv_ is not None
        and th.get("market_volatility_median") is not None
        and mv_ >= float(th["market_volatility_median"])
        else "low_volatility"
        if mv_ is not None and th.get("market_volatility_median") is not None
        else None,
        "avg_market_volatility_168h": mv_,
        "funding_level_label": "high_funding"
        if fl is not None
        and th.get("funding_level_median") is not None
        and fl >= float(th["funding_level_median"])
        else "low_funding"
        if fl is not None and th.get("funding_level_median") is not None
        else None,
        "avg_funding_level_72h": fl,
        "funding_dispersion_label": "funding_dispersed"
        if fdv is not None
        and th.get("funding_dispersion_median") is not None
        and fdv >= float(th["funding_dispersion_median"])
        else "funding_compressed"
        if fdv is not None and th.get("funding_dispersion_median") is not None
        else None,
        "avg_funding_dispersion_72h": fdv,
        "breadth_label": "broad_participation"
        if br is not None
        and th.get("breadth_median") is not None
        and br >= float(th["breadth_median"])
        else "weak_participation"
        if br is not None and th.get("breadth_median") is not None
        else None,
        "avg_breadth_24h": br,
        "co_movement_label": "high_co_movement"
        if cm is not None
        and th.get("co_movement_median") is not None
        and cm >= float(th["co_movement_median"])
        else "low_co_movement"
        if cm is not None and th.get("co_movement_median") is not None
        else None,
        "avg_co_movement_72h": cm,
        "concentration_label": "concentrated"
        if conc is not None
        and th.get("concentration_median") is not None
        and conc >= float(th["concentration_median"])
        else "diversified"
        if conc is not None and th.get("concentration_median") is not None
        else None,
        "avg_concentration": conc,
        "dominant_position_direction": dpd,
        "position_direction_counts": dc,
    }
    if "pair_correlation" in regime_state:
        pv = _mv(regime_state["pair_volatility"])
        pc = _mv(regime_state["pair_correlation"])
        pd_ = _mv(regime_state["pair_direction"])
        pl.update(
            {
                "pair_volatility_label": "high_volatility"
                if pv is not None
                and th.get("pair_volatility_median") is not None
                and pv >= float(th["pair_volatility_median"])
                else "low_volatility"
                if pv is not None and th.get("pair_volatility_median") is not None
                else None,
                "avg_pair_volatility_72h": pv,
                "pair_correlation_label": "high_correlation"
                if pc is not None
                and th.get("pair_correlation_median") is not None
                and pc >= float(th["pair_correlation_median"])
                else "low_correlation"
                if pc is not None and th.get("pair_correlation_median") is not None
                else None,
                "avg_pair_correlation_72h": pc,
                "pair_direction_label": "asset_1_leading"
                if pd_ is not None and pd_ >= 0.0
                else "asset_2_leading"
                if pd_ is not None
                else None,
                "avg_pair_direction_24h": pd_,
            },
        )
    return pl


def _ewts(
    *,
    te: list[dict[str, Any]],
    sts: pd.Timestamp,
    ets: pd.Timestamp,
) -> dict[str, Any]:
    matched: list[dict[str, Any]] = []
    for e in te:
        s = e.get("start_timestamp")
        if s and sts <= pd.Timestamp(s) <= ets:
            matched.append(e)
    returns = [
        float(e["total_return"]) for e in matched if e.get("total_return") is not None
    ]
    bars = [
        float(e["bars"])
        for e in matched
        if _sf(e.get("bars"), default=None) is not None
    ]
    days = max(1.0, (ets - sts).total_seconds() / 86400.0)
    dc = _edc(matched)
    dd = max(dc.items(), key=lambda x: x[1])[0] if dc else None
    return {
        "trade_count": len(matched),
        "entries_per_day": _sf(len(matched) / days),
        "win_rate": _sf(
            sum(1 for v in returns if v > 0.0) / len(returns) if returns else None,
        ),
        "avg_return": _sf(sum(returns) / len(returns) if returns else None),
        "median_return": _sf(float(np.median(returns)) if returns else None),
        "median_hold_bars": _sf(float(np.median(bars)) if bars else None),
        "dominant_direction": dd,
        "direction_counts": dc,
    }


def _paesp(
    *,
    equity_curve: pd.Series,
    te: list[dict[str, Any]],
    regime_state: dict[str, Any],
) -> dict[str, Any]:
    c = pd.to_numeric(equity_curve, errors="coerce").dropna()
    if c.shape[0] < 2:
        return {}
    dd = c.div(c.cummax()).sub(1.0)
    pt = pd.Timestamp(c.idxmax())
    tt = pd.Timestamp(dd.idxmin())
    ds = pd.Timestamp(c.loc[:tt].idxmax())
    pre = _ewts(te=te, sts=pd.Timestamp(c.index.min()), ets=pt)
    post = _ewts(te=te, sts=pt, ets=pd.Timestamp(c.index.max()))
    dw = _ewts(te=te, sts=ds, ets=tt)
    dw["regime"] = _wrs(regime_state=regime_state, start_timestamp=ds, end_timestamp=tt)
    return {
        "peak_timestamp": pt.isoformat(),
        "peak_equity": _sf(c.loc[pt]),
        "max_drawdown_start": ds.isoformat(),
        "max_drawdown_end": tt.isoformat(),
        "max_drawdown": _sf(dd.loc[tt]),
        "pre_peak": pre,
        "post_peak": post,
        "drawdown_window": dw,
    }


def _patbp(
    *,
    r: pd.Series,
    te: list[dict[str, Any]],
    regime_state: dict[str, Any],
) -> dict[str, Any]:
    c = pd.to_numeric(r, errors="coerce").dropna()
    if c.empty:
        return {}
    dr = c.resample("1D").apply(lambda v: float((1.0 + v).prod() - 1.0))
    if dr.shape[0] < 14:
        return {}

    def _wp(wd: int) -> dict[str, Any] | None:
        if dr.shape[0] < wd:
            return None
        roll = (1.0 + dr.fillna(0.0)).rolling(wd).apply(np.prod, raw=True) - 1.0
        roll = roll.dropna()
        if roll.empty:
            return None
        be = roll.idxmax()
        we = roll.idxmin()

        def _smr(et: pd.Timestamp, label: str) -> dict[str, Any]:
            el = int(cast(int, dr.index.get_loc(et)))
            sl = max(0, el - wd + 1)
            st = pd.Timestamp(dr.index[sl])
            ts = _ewts(te=te, sts=st, ets=et)
            ts["regime"] = _wrs(
                regime_state=regime_state,
                start_timestamp=st,
                end_timestamp=et,
            )
            return {
                "label": label,
                "start_timestamp": st.isoformat(),
                "end_timestamp": et.isoformat(),
                "window_days": wd,
                "total_return": _sf(float(cast(Any, roll.loc[et]))),
                **ts,
            }

        return {
            "window_days": wd,
            "best_window": _smr(pd.Timestamp(cast(Any, be)), "best"),
            "worst_window": _smr(pd.Timestamp(cast(Any, we)), "worst"),
        }

    ws = [p for p in (_wp(14), _wp(30)) if p]
    return {"windows": ws} if ws else {}


def _efeats(
    *,
    signal_components: dict[str, pd.DataFrame] | None,
    timestamp: str | float | pd.Timestamp | None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for feat, frame in (signal_components or {}).items():
        if frame is None or frame.empty:
            continue
        ats = _lts(frame.index, timestamp)
        if ats is None:
            continue
        v = _sf(frame.iloc[:, 0].get(ats), default=None)
        if v is None:
            continue
        rows.append({"feature": str(feat), "value": v, "abs_value": _sf(abs(v))})
    rows.sort(key=lambda r: abs(float(r.get("value") or 0.0)), reverse=True)
    return rows[:3]


def _paet(
    *,
    te: list[dict[str, Any]],
    signal_score: pd.DataFrame | None,
    signal_components: dict[str, pd.DataFrame] | None,
) -> dict[str, Any]:
    if not te:
        return {}
    scored = [e for e in te if e.get("total_return") is not None]
    if not scored:
        return {}
    winners = sorted(scored, key=lambda e: float(e["total_return"]), reverse=True)[:2]
    losers = sorted(scored, key=lambda e: float(e["total_return"]))[:2]

    def _pl(e: dict[str, Any]) -> dict[str, Any]:
        ets = e.get("start_timestamp")
        es = None
        if signal_score is not None and not signal_score.empty and ets:
            ats = _lts(signal_score.index, ets)
            if ats is not None:
                es = _sf(signal_score.iloc[:, 0].get(ats), default=None)
        return {
            "start_timestamp": ets,
            "end_timestamp": e.get("end_timestamp"),
            "direction": e.get("direction"),
            "bars": _sf(e.get("bars"), default=None),
            "total_return": _sf(e.get("total_return"), default=None),
            "entry_score": es,
            "entry_regime": {**e.get("entry_regime", {})},
            "entry_feature_contributors": _efeats(
                signal_components=signal_components,
                timestamp=ets,
            ),
        }

    return {"winners": [_pl(e) for e in winners], "losers": [_pl(e) for e in losers]}


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
        gm = regime_gate_mask.reindex(sf.index).ffill().fillna(False).astype(bool)
        rgs = {
            "configured": True,
            "active_fraction": _sf(float(gm.mean())),
            "blocked_while_flat_fraction": _sf(
                float((~gm)[fm1].mean()) if bool(fm1.any()) else None,
            ),
            "broken_while_active_fraction": _sf(
                float((~gm)[am1].mean()) if bool(am1.any()) else None,
            ),
            "exit_on_break": bool(
                compiled_metadata.get("regime_gates", {}).get("exit_on_break", True),
            ),
            "entry": compiled_metadata.get("regime_gates", {}).get("entry") or [],
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
            float(sem2d.any(axis=1)[fm1].mean()) if bool(fm1.any()) else None,
        ),
        "score_alignment_when_active": aaf,
        "median_active_asset_count": _sf(mav),
        "regime_gates": rgs,
        "bottleneck_tags": bt,
    }


def _pcfm(cm: dict[str, Any]) -> dict[str, Any]:
    p: dict[str, Any] = {
        "execution_profile": cm.get("execution_profile"),
        "long_count": int(cm.get("long_count", 0) or 0),
        "short_count": int(cm.get("short_count", 0) or 0),
        "selection_count": int(cm.get("selection_count", 0) or 0),
        "entry_abs_score": _sf(cm.get("entry_abs_score"), default=None),
        "exit_abs_score": _sf(cm.get("exit_abs_score"), default=None),
        "flip_abs_score": _sf(cm.get("flip_abs_score"), default=None),
        "max_holding_bars": int(cm.get("max_holding_bars", 0) or 0),
        "cooldown_bars": int(cm.get("cooldown_bars", 0) or 0),
        "signal_leverage_scale": _sf(cm.get("signal_leverage_scale"), default=None),
        "gross_target": _sf(cm.get("gross_target"), default=None),
        "max_gross_target": _sf(cm.get("max_gross_target"), default=None),
    }
    sw = {**cm.get("pair_policy_sweep", {})}
    if sw:
        p["policy_sweep"] = {
            "applied": bool(sw.get("applied")),
            "train_window_count": int(sw.get("train_window_count", 0) or 0),
            "trial_count": int(sw.get("trial_count", 0) or 0),
            "best_train_score": _sf(
                sw.get("best_train_summary", {}).get("aggregate_score"),
                default=None,
            ),
            "best_train_return": _sf(
                sw.get("best_train_summary", {}).get("median_total_return"),
                default=None,
            ),
        }
    return p
