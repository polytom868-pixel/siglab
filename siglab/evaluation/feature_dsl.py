from __future__ import annotations
import re
from pathlib import Path
from typing import Any, Callable, cast
import numpy as np
import pandas as pd
import yaml
from siglab.track_registry import storage_track_name
_OPS = ('pct_change', 'diff', 'ema', 'rolling_mean', 'rolling_sum', 'rolling_std', 'rolling_zscore', 'rolling_min', 'rolling_max', 'rolling_skew', 'rolling_kurt', 'rolling_corr', 'rolling_autocorr', 'rolling_beta', 'rolling_hurst', 'mean_reversion_halflife', 'kalman_beta', 'kalman_residual', 'rsi', 'sub', 'mul', 'div', 'abs', 'log', 'clip', 'sign_flip_prob')
_NUM_RE = re.compile(r'^-?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?$')
_ID_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')

def load_feature_spec(root_dir: Path, *, track: str, family: str | None=None) -> dict[str, Any]:
    pld = yaml.safe_load((root_dir / 'mutable' / 'feature_lab.yaml').read_text()); ts = pld.get('tracks', {}).get(storage_track_name(track) or track, {})
    if family is None:
        fams = ts.get('families', {}); aliases: dict[str, str] = {}; rsf: dict[str, list[str]] = {}
        for fn, fs in fams.items(): aliases.update(dict(fs.get('aliases') or {})); rsf[fn] = list(fs.get('raw_series') or [])
        return {'aliases': aliases, 'raw_series_by_family': rsf, 'operators': list(_OPS)}
    fs_ = ts.get('families', {}).get(family, {})
    return {'aliases': dict(fs_.get('aliases') or {}), 'raw_series': list(fs_.get('raw_series') or []), 'operators': list(_OPS)}

def valid_expr(expression: str, *, aliases: dict[str, str], raw_series: set[str]) -> bool:
    try: _eval(expression, raw_frames={n: pd.DataFrame() for n in raw_series}, aliases=aliases, cache={}, validate_only=True); return True
    except (ValueError, TypeError, KeyError, ZeroDivisionError, RecursionError): return False

def resolve_feature_frames(features: list[str], *, aliases: dict[str, str], raw_frames: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    cache: dict[str, pd.DataFrame] = {}; resolved: dict[str, pd.DataFrame] = {}
    for ft in features: resolved[ft] = _eval(ft, raw_frames=raw_frames, aliases=aliases, cache=cache, validate_only=False)
    return resolved

def _eval(expr: str, *, raw_frames: dict[str, pd.DataFrame], aliases: dict[str, str], cache: dict[str, pd.DataFrame], validate_only: bool) -> pd.DataFrame:
    expr = expr.strip()
    if expr in cache: return cache[expr]
    if expr in aliases:
        result = _eval(aliases[expr], raw_frames=raw_frames, aliases=aliases, cache=cache, validate_only=validate_only); cache[expr] = result; return result
    if expr in raw_frames: result = raw_frames[expr]; cache[expr] = result; return result
    if _ID_RE.match(expr): raise ValueError(f'Unknown feature token: {expr}')
    fn, args = _parse(expr)
    args_val = [_earg(t, raw_frames=raw_frames, aliases=aliases, cache=cache, validate_only=validate_only) for t in args]
    result = _apply(fn, args_val, validate_only=validate_only); cache[expr] = result; return result

def _earg(token: str, *, raw_frames: dict[str, pd.DataFrame], aliases: dict[str, str], cache: dict[str, pd.DataFrame], validate_only: bool) -> pd.DataFrame | float:
    s = token.strip()
    return float(s) if _NUM_RE.match(s) else _eval(s, raw_frames=raw_frames, aliases=aliases, cache=cache, validate_only=validate_only)

def _parse(expr: str) -> tuple[str, list[str]]:
    if '(' not in expr or not expr.endswith(')'): raise ValueError(f'Invalid feature expression: {expr}')
    fn, rest = expr.split('(', 1); fn = fn.strip()
    if fn not in _OPS: raise ValueError(f'Unsupported feature operator: {fn}')
    return (fn, _split(rest[:-1].strip()))

def _split(text: str) -> list[str]:
    if not text: return []
    d = 0; cur: list[str] = []; args: list[str] = []
    for c in text:
        if c == ',' and d == 0: args.append(''.join(cur).strip()); cur = []; continue
        if c == '(' or c == ')': d += 1 if c == '(' else -1
        if d < 0: raise ValueError('Unbalanced parentheses in feature expression')
        cur.append(c)
    if d != 0: raise ValueError('Unbalanced parentheses in feature expression')
    args.append(''.join(cur).strip()); return args

def _make_rolling_op(attr: str):
    def op(args, *, validate_only):
        f, w = _efi(args)
        return pd.DataFrame() if validate_only else getattr(f.rolling(w), attr)()
    return op

def _make_frame_periods_op(func: str):
    def op(args, *, validate_only):
        f, p = _efi(args)
        return pd.DataFrame() if validate_only else getattr(f, func)(p)
    return op

def _apply(fn: str, args: list[pd.DataFrame | float], *, validate_only: bool) -> pd.DataFrame:
    h = _OP_REG.get(fn)
    if h is None: raise ValueError(f'Unsupported feature operator: {fn}')
    return h(args, validate_only=validate_only)

def _ef(args: list[pd.DataFrame | float], *, expected: int) -> pd.DataFrame:
    if len(args) != expected or not isinstance(args[0], pd.DataFrame): raise ValueError('Expected dataframe argument')
    return args[0]

def _efi(args: list[pd.DataFrame | float]) -> tuple[pd.DataFrame, int]:
    if len(args) != 2 or not isinstance(args[0], pd.DataFrame) or not isinstance(args[1], float): raise ValueError('Expected frame and numeric window')
    return (args[0], int(args[1]))

def _e2fi(args: list[pd.DataFrame | float]) -> tuple[pd.DataFrame, pd.DataFrame, int]:
    if len(args) != 3 or not isinstance(args[0], pd.DataFrame) or not isinstance(args[1], pd.DataFrame) or not isinstance(args[2], float): raise ValueError('Expected frame, frame, and numeric window')
    return (args[0], args[1], int(args[2]))

def _ef2i(args: list[pd.DataFrame | float]) -> tuple[pd.DataFrame, int, int]:
    if len(args) != 3 or not isinstance(args[0], pd.DataFrame) or not isinstance(args[1], float) or not isinstance(args[2], float): raise ValueError('Expected frame and two numeric arguments')
    return (args[0], int(args[1]), int(args[2]))

def _eka(args: list[pd.DataFrame | float]) -> tuple[pd.DataFrame, pd.DataFrame, float, float]:
    if len(args) == 2 and isinstance(args[0], pd.DataFrame) and isinstance(args[1], pd.DataFrame): return (args[0], args[1], 1e-05, 0.001)
    if len(args) == 4 and isinstance(args[0], pd.DataFrame) and isinstance(args[1], pd.DataFrame) and isinstance(args[2], float) and isinstance(args[3], float): return (args[0], args[1], float(args[2]), float(args[3]))
    raise ValueError('kalman_beta and kalman_residual expect frame, frame, and optional process/observation noise')

def _op_rolling_zscore(args, *, validate_only):
    f, w = _efi(args)
    if validate_only: return pd.DataFrame()
    m = f.rolling(w).mean(); s = f.rolling(w).std().replace(0.0, np.nan)
    return f.sub(m).div(s).replace([np.inf, -np.inf], np.nan)

def _op_rolling_corr(args, *, validate_only):
    l, r, w = _e2fi(args)
    return pd.DataFrame() if validate_only else l.rolling(w).corr(r)

def _op_rolling_autocorr(args, *, validate_only):
    f, lag, w = _ef2i(args)
    if validate_only: return pd.DataFrame()
    return f.rolling(w).corr(f.shift(lag))

def _op_rolling_beta(args, *, validate_only):
    l, r, w = _e2fi(args)
    if validate_only: return pd.DataFrame()
    return l.rolling(w).cov(r).div(r.rolling(w).var().replace(0.0, np.nan)).replace([np.inf, -np.inf], np.nan)

def _op_rolling_hurst(args, *, validate_only):
    f, w = _efi(args)
    if validate_only: return pd.DataFrame()
    out = pd.DataFrame(index=f.index, columns=f.columns, dtype=float)
    for c in f.columns: out[c] = f[c].astype(float).rolling(w, min_periods=w).apply(_hurst, raw=True)
    return out

def _op_mean_reversion_halflife(args, *, validate_only):
    f, w = _efi(args)
    if validate_only: return pd.DataFrame()
    lagged = f.shift(1); delta = f.diff()
    beta = delta.rolling(w).cov(lagged).div(lagged.rolling(w).var().replace(0.0, np.nan)); phi = 1.0 + beta
    vphi = phi.where((phi > 0.0) & (phi < 1.0))
    return cast(pd.DataFrame, (-np.log(2.0) / np.log(vphi)).replace([np.inf, -np.inf], np.nan))

def _op_kalman_beta(args, *, validate_only):
    l, r, pn, on = _eka(args)
    return pd.DataFrame() if validate_only else _kbf(l, r, process_noise=pn, observation_noise=on)

def _op_kalman_residual(args, *, validate_only):
    l, r, pn, on = _eka(args)
    if validate_only: return pd.DataFrame()
    beta = _kbf(l, r, process_noise=pn, observation_noise=on)
    al, ar = l.align(r, join='outer')
    return al.sub(beta.mul(ar))

def _op_rsi(args, *, validate_only):
    f, w = _efi(args)
    if validate_only: return pd.DataFrame()
    d = f.diff(); gains = d.clip(lower=0.0); losses = (-d).clip(lower=0.0); alpha = 1.0 / max(w, 1)
    ag = gains.ewm(alpha=alpha, adjust=False, min_periods=w).mean(); al = losses.ewm(alpha=alpha, adjust=False, min_periods=w).mean()
    rs = ag.div(al.replace(0.0, np.nan)); rsi = 100.0 - 100.0 / (1.0 + rs)
    rsi = rsi.where(al > 0.0, 100.0); return rsi.where(~((ag <= 0.0) & (al <= 0.0)), 50.0)

def _op_sign_flip_prob(args, *, validate_only):
    f, w = _efi(args)
    return pd.DataFrame() if validate_only else f.apply(np.sign).diff().ne(0).astype(float).rolling(w).mean()

def _op_abs(args, *, validate_only):
    f = _ef(args, expected=1)
    return pd.DataFrame() if validate_only else f.abs()

def _op_log(args, *, validate_only):
    f = _ef(args, expected=1)
    return pd.DataFrame() if validate_only else cast(pd.DataFrame, np.log(f.where(f > 0.0)))

def _op_clip(args, *, validate_only):
    f = _ef(args[:1], expected=1)
    if len(args) != 3 or not isinstance(args[1], float) or not isinstance(args[2], float): raise ValueError('clip expects frame, low, high')
    return pd.DataFrame() if validate_only else f.clip(lower=args[1], upper=args[2])

def _op_sub(args, *, validate_only):
    if len(args) != 2: raise ValueError('sub expects 2 arguments')
    return pd.DataFrame() if validate_only else _bin(args[0], args[1], lambda a, b: a.sub(b, fill_value=0.0))

def _op_mul(args, *, validate_only):
    if len(args) != 2: raise ValueError('mul expects 2 arguments')
    return pd.DataFrame() if validate_only else _bin(args[0], args[1], lambda a, b: a * b)

def _op_div(args, *, validate_only):
    if len(args) != 2: raise ValueError('div expects 2 arguments')
    return pd.DataFrame() if validate_only else _bin(args[0], args[1], _sdiv)

_OP_REG = dict[str, Any](
    [('pct_change', _make_frame_periods_op('pct_change')), ('diff', _make_frame_periods_op('diff')),
     ('rolling_mean', _make_rolling_op('mean')), ('rolling_sum', _make_rolling_op('sum')),
     ('rolling_std', _make_rolling_op('std')), ('rolling_min', _make_rolling_op('min')),
     ('rolling_max', _make_rolling_op('max')), ('rolling_skew', _make_rolling_op('skew')),
     ('rolling_kurt', _make_rolling_op('kurt')), ('rolling_zscore', _op_rolling_zscore),
     ('rolling_corr', _op_rolling_corr), ('rolling_autocorr', _op_rolling_autocorr),
     ('rolling_beta', _op_rolling_beta), ('rolling_hurst', _op_rolling_hurst),
     ('mean_reversion_halflife', _op_mean_reversion_halflife), ('kalman_beta', _op_kalman_beta),
     ('kalman_residual', _op_kalman_residual), ('rsi', _op_rsi), ('sign_flip_prob', _op_sign_flip_prob),
     ('abs', _op_abs), ('log', _op_log), ('clip', _op_clip), ('sub', _op_sub), ('mul', _op_mul), ('div', _op_div)])

def _op_ema(args, *, validate_only):
    f, s = _efi(args)
    return pd.DataFrame() if validate_only else f.ewm(span=s, adjust=False).mean()

_OP_REG['ema'] = _op_ema

def _bin(left: pd.DataFrame | float, right: pd.DataFrame | float, op: Callable[[Any, Any], pd.DataFrame]) -> pd.DataFrame:
    if isinstance(left, pd.DataFrame) and isinstance(right, pd.DataFrame):
        lf, rf = _afp(left, right); return cast(pd.DataFrame, op(lf, rf))
    if isinstance(left, pd.DataFrame): return cast(pd.DataFrame, op(left, right))
    if isinstance(right, pd.DataFrame):
        if op is _sdiv: return cast(pd.DataFrame, _sdiv(pd.DataFrame(float(left), index=right.index, columns=right.columns), right))
        return cast(pd.DataFrame, op(pd.DataFrame(float(left), index=right.index, columns=right.columns), right))
    raise ValueError('At least one argument must be a dataframe')

def _ap(left: pd.DataFrame | float, right: pd.DataFrame | float) -> tuple[pd.DataFrame, pd.DataFrame]:
    if isinstance(left, pd.DataFrame) and isinstance(right, pd.DataFrame): return _afp(left, right)
    if isinstance(left, pd.DataFrame): return (left, pd.DataFrame(cast(float, right), index=left.index, columns=left.columns))
    if isinstance(right, pd.DataFrame): return (pd.DataFrame(left, index=right.index, columns=right.columns), right)
    raise ValueError('At least one argument must be a dataframe')

def _afp(left: pd.DataFrame, right: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    li, ri = left.align(right, join='outer', axis=0); lc = list(left.columns); rc = list(right.columns)
    if lc == rc: return (li.reindex(columns=lc), ri.reindex(columns=rc))
    if len(lc) == 1 and lc[0] not in rc: return (_bcast(li, target_columns=rc), ri.reindex(columns=rc))
    if len(rc) == 1 and rc[0] not in lc: return (li.reindex(columns=lc), _bcast(ri, target_columns=lc))
    cc = [c for c in lc if c in rc]
    if cc: return (li.reindex(columns=cc), ri.reindex(columns=cc))
    return li.align(ri, join='outer', axis=1, fill_value=np.nan)

def _bcast(frame: pd.DataFrame, *, target_columns: list[str]) -> pd.DataFrame:
    if len(frame.columns) != 1: raise ValueError('broadcast_single_column_frame requires exactly one column')
    return pd.DataFrame({c: frame.iloc[:, 0] for c in target_columns}, index=frame.index).reindex(columns=target_columns)

def _sdiv(left: pd.DataFrame | float, right: pd.DataFrame | float) -> pd.DataFrame:
    if isinstance(left, pd.DataFrame) and isinstance(right, pd.DataFrame): out = left.div(right.replace(0.0, np.nan))
    elif isinstance(left, pd.DataFrame): out = left / cast(float, right)
    elif isinstance(right, pd.DataFrame): out = pd.DataFrame(float(left), index=right.index, columns=right.columns).div(right.replace(0.0, np.nan))
    else: raise ValueError('safe_div requires a dataframe operand')
    return out.replace([np.inf, -np.inf], np.nan)

def _hurst(values: np.ndarray) -> float:
    arr = np.asarray(values, dtype=float)[np.isfinite(np.asarray(values, dtype=float))]
    if arr.size < 32: return float('nan')
    ml = min(20, arr.size // 2); lags = [l for l in (2, 4, 8, 16, 20) if l < ml]
    if len(lags) < 2: lags = list(range(2, ml))
    tau: list[float] = []; lv: list[int] = []
    for lag in lags:
        d = arr[lag:] - arr[:-lag]
        if d.size < 2: continue
        sc = float(np.nanstd(d))
        if not np.isfinite(sc) or sc <= 0.0: continue
        lv.append(lag); tau.append(sc)
    return float('nan') if len(tau) < 2 else float(np.polyfit(np.log(lv), np.log(tau), 1)[0])

def _kbf(left: pd.DataFrame, right: pd.DataFrame, *, process_noise: float, observation_noise: float) -> pd.DataFrame:
    al, ar = left.align(right, join='outer'); out = pd.DataFrame(index=al.index, columns=al.columns, dtype=float); q = max(float(process_noise), 1e-10); r = max(float(observation_noise), 1e-10)
    for c in al.columns:
        y = al[c].astype(float).to_numpy(); x = ar[c].astype(float).to_numpy(); bs = np.full(len(y), np.nan, dtype=float); st = 0.0; cov = 1.0
        for i, (yy, xx) in enumerate(zip(y, x)):
            if not np.isfinite(yy) or not np.isfinite(xx): continue
            if i == 0 and abs(xx) > 1e-12: st = float(yy) / float(xx)
            pc = cov + q; iv = xx * xx * pc + r
            if not np.isfinite(iv) or iv <= 0.0: continue
            kg = pc * xx / iv; st = float(st + kg * (yy - xx * st)); cov = max(float((1.0 - kg * xx) * pc), 1e-10); bs[i] = st
        out[c] = bs
    return out

# backward compat aliases for tests
_aligned_frame_pair = _afp
_aligned_pair = _ap
_broadcast_single_column_frame = _bcast
_expect_frame = _ef
_expect_frame_and_int = _efi
_expect_frame_and_two_ints = _ef2i
_expect_kalman_args = _eka
_expect_two_frames_and_int = _e2fi
_hurst_exponent = _hurst
_op_pct_change = _OP_REG['pct_change']
_op_diff = _OP_REG['diff']
_op_rolling_mean = _OP_REG['rolling_mean']
_op_rolling_sum = _OP_REG['rolling_sum']
_op_rolling_std = _OP_REG['rolling_std']
_op_rolling_min = _OP_REG['rolling_min']
_op_rolling_max = _OP_REG['rolling_max']
_op_rolling_skew = _OP_REG['rolling_skew']
_op_rolling_kurt = _OP_REG['rolling_kurt']
_parse_call = _parse
_safe_div = _sdiv
_split_args = _split
