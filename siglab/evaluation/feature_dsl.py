from __future__ import annotations

import re
from pathlib import Path
from typing import Any, cast

import numpy as np
import pandas as pd
import yaml

from siglab.track_registry import storage_track_name

FUNCTION_OPERATORS = (
    "pct_change",
    "diff",
    "ema",
    "rolling_mean",
    "rolling_sum",
    "rolling_std",
    "rolling_zscore",
    "rolling_min",
    "rolling_max",
    "rolling_skew",
    "rolling_kurt",
    "rolling_corr",
    "rolling_autocorr",
    "rolling_beta",
    "rolling_hurst",
    "mean_reversion_halflife",
    "kalman_beta",
    "kalman_residual",
    "rsi",
    "add",
    "sub",
    "mul",
    "div",
    "neg",
    "abs",
    "log",
    "clip",
    "sign_flip_prob",
    "gt",
    "lt",
    "and",
    "not",
    "where",
)

_NUMBER_RE = re.compile(r"^-?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?$")
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def load_feature_spec(
    root_dir: Path,
    *,
    track: str,
    family: str | None = None,
) -> dict[str, Any]:
    payload = yaml.safe_load((root_dir / "mutable" / "feature_lab.yaml").read_text())
    track_spec = payload.get("tracks", {}).get(storage_track_name(track) or track, {})
    if family is None:
        families = track_spec.get("families", {})
        aliases: dict[str, str] = {}
        raw_series_by_family: dict[str, list[str]] = {}
        for family_name, family_spec in families.items():
            aliases.update(dict(family_spec.get("aliases") or {}))
            raw_series_by_family[family_name] = list(family_spec.get("raw_series") or [])
        return {
            "aliases": aliases,
            "raw_series_by_family": raw_series_by_family,
            "operators": list(FUNCTION_OPERATORS),
        }

    family_spec = track_spec.get("families", {}).get(family, {})
    return {
        "aliases": dict(family_spec.get("aliases") or {}),
        "raw_series": list(family_spec.get("raw_series") or []),
        "operators": list(FUNCTION_OPERATORS),
    }


def is_valid_feature_expression(
    expression: str,
    *,
    aliases: dict[str, str],
    raw_series: set[str],
) -> bool:
    try:
        _evaluate_feature(
            expression,
            raw_frames={name: pd.DataFrame() for name in raw_series},
            aliases=aliases,
            cache={},
            validate_only=True,
        )
    except Exception:
        return False
    return True


def resolve_feature_frames(
    features: list[str],
    *,
    aliases: dict[str, str],
    raw_frames: dict[str, pd.DataFrame],
) -> dict[str, pd.DataFrame]:
    cache: dict[str, pd.DataFrame] = {}
    resolved: dict[str, pd.DataFrame] = {}
    for feature in features:
        resolved[feature] = _evaluate_feature(
            feature,
            raw_frames=raw_frames,
            aliases=aliases,
            cache=cache,
            validate_only=False,
        )
    return resolved


def _evaluate_feature(
    expression: str,
    *,
    raw_frames: dict[str, pd.DataFrame],
    aliases: dict[str, str],
    cache: dict[str, pd.DataFrame],
    validate_only: bool,
) -> pd.DataFrame:
    expression = expression.strip()
    if expression in cache:
        return cache[expression]

    if expression in aliases:
        result = _evaluate_feature(
            aliases[expression],
            raw_frames=raw_frames,
            aliases=aliases,
            cache=cache,
            validate_only=validate_only,
        )
        cache[expression] = result
        return result

    if expression in raw_frames:
        result = raw_frames[expression]
        cache[expression] = result
        return result

    if _IDENT_RE.match(expression):
        raise ValueError(f"Unknown feature token: {expression}")

    function_name, arg_tokens = _parse_call(expression)
    args = [
        _evaluate_arg(
            token,
            raw_frames=raw_frames,
            aliases=aliases,
            cache=cache,
            validate_only=validate_only,
        )
        for token in arg_tokens
    ]
    result = _apply_operator(function_name, args, validate_only=validate_only)
    cache[expression] = result
    return result


def _evaluate_arg(
    token: str,
    *,
    raw_frames: dict[str, pd.DataFrame],
    aliases: dict[str, str],
    cache: dict[str, pd.DataFrame],
    validate_only: bool,
) -> pd.DataFrame | float:
    stripped = token.strip()
    if _NUMBER_RE.match(stripped):
        return float(stripped)
    return _evaluate_feature(
        stripped,
        raw_frames=raw_frames,
        aliases=aliases,
        cache=cache,
        validate_only=validate_only,
    )


def _parse_call(expression: str) -> tuple[str, list[str]]:
    if "(" not in expression or not expression.endswith(")"):
        raise ValueError(f"Invalid feature expression: {expression}")
    function_name, remainder = expression.split("(", 1)
    function_name = function_name.strip()
    if function_name not in FUNCTION_OPERATORS:
        raise ValueError(f"Unsupported feature operator: {function_name}")
    inner = remainder[:-1].strip()
    return function_name, _split_args(inner)


def _split_args(text: str) -> list[str]:
    if not text:
        return []
    depth = 0
    current: list[str] = []
    args: list[str] = []
    for char in text:
        if char == "," and depth == 0:
            args.append("".join(current).strip())
            current = []
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                raise ValueError("Unbalanced parentheses in feature expression")
        current.append(char)
    if depth != 0:
        raise ValueError("Unbalanced parentheses in feature expression")
    args.append("".join(current).strip())
    return args


# --- Operator registry (dict dispatch) ---

def _op_pct_change(args: list[pd.DataFrame | float], *, validate_only: bool) -> pd.DataFrame:
    frame, periods = _expect_frame_and_int(args)
    if validate_only:
        return pd.DataFrame()
    return frame.pct_change(periods)

def _op_diff(args: list[pd.DataFrame | float], *, validate_only: bool) -> pd.DataFrame:
    frame, periods = _expect_frame_and_int(args)
    if validate_only:
        return pd.DataFrame()
    return frame.diff(periods)

def _op_rolling_mean(args: list[pd.DataFrame | float], *, validate_only: bool) -> pd.DataFrame:
    frame, window = _expect_frame_and_int(args)
    if validate_only:
        return pd.DataFrame()
    return frame.rolling(window).mean()

def _op_rolling_sum(args: list[pd.DataFrame | float], *, validate_only: bool) -> pd.DataFrame:
    frame, window = _expect_frame_and_int(args)
    if validate_only:
        return pd.DataFrame()
    return frame.rolling(window).sum()

def _op_ema(args: list[pd.DataFrame | float], *, validate_only: bool) -> pd.DataFrame:
    frame, span = _expect_frame_and_int(args)
    if validate_only:
        return pd.DataFrame()
    return frame.ewm(span=span, adjust=False).mean()

def _op_rolling_std(args: list[pd.DataFrame | float], *, validate_only: bool) -> pd.DataFrame:
    frame, window = _expect_frame_and_int(args)
    if validate_only:
        return pd.DataFrame()
    return frame.rolling(window).std()

def _op_rolling_zscore(args: list[pd.DataFrame | float], *, validate_only: bool) -> pd.DataFrame:
    frame, window = _expect_frame_and_int(args)
    if validate_only:
        return pd.DataFrame()
    mean = frame.rolling(window).mean()
    std = frame.rolling(window).std().replace(0.0, np.nan)
    return frame.sub(mean).div(std).replace([np.inf, -np.inf], np.nan)

def _op_rolling_min(args: list[pd.DataFrame | float], *, validate_only: bool) -> pd.DataFrame:
    frame, window = _expect_frame_and_int(args)
    if validate_only:
        return pd.DataFrame()
    return frame.rolling(window).min()

def _op_rolling_max(args: list[pd.DataFrame | float], *, validate_only: bool) -> pd.DataFrame:
    frame, window = _expect_frame_and_int(args)
    if validate_only:
        return pd.DataFrame()
    return frame.rolling(window).max()

def _op_rolling_skew(args: list[pd.DataFrame | float], *, validate_only: bool) -> pd.DataFrame:
    frame, window = _expect_frame_and_int(args)
    if validate_only:
        return pd.DataFrame()
    return frame.rolling(window).skew()

def _op_rolling_kurt(args: list[pd.DataFrame | float], *, validate_only: bool) -> pd.DataFrame:
    frame, window = _expect_frame_and_int(args)
    if validate_only:
        return pd.DataFrame()
    return frame.rolling(window).kurt()

def _op_rolling_corr(args: list[pd.DataFrame | float], *, validate_only: bool) -> pd.DataFrame:
    left, right, window = _expect_two_frames_and_int(args)
    if validate_only:
        return pd.DataFrame()
    return left.rolling(window).corr(right)

def _op_rolling_autocorr(args: list[pd.DataFrame | float], *, validate_only: bool) -> pd.DataFrame:
    frame, lag, window = _expect_frame_and_two_ints(args)
    if validate_only:
        return pd.DataFrame()
    shifted = frame.shift(lag)
    return frame.rolling(window).corr(shifted)

def _op_rolling_beta(args: list[pd.DataFrame | float], *, validate_only: bool) -> pd.DataFrame:
    left, right, window = _expect_two_frames_and_int(args)
    if validate_only:
        return pd.DataFrame()
    covariance = left.rolling(window).cov(right)
    variance = right.rolling(window).var().replace(0.0, np.nan)
    return covariance.div(variance).replace([np.inf, -np.inf], np.nan)

def _op_rolling_hurst(args: list[pd.DataFrame | float], *, validate_only: bool) -> pd.DataFrame:
    frame, window = _expect_frame_and_int(args)
    if validate_only:
        return pd.DataFrame()
    out = pd.DataFrame(index=frame.index, columns=frame.columns, dtype=float)
    for column in frame.columns:
        series = frame[column].astype(float)
        out[column] = series.rolling(window, min_periods=window).apply(_hurst_exponent, raw=True)
    return out

def _op_mean_reversion_halflife(args: list[pd.DataFrame | float], *, validate_only: bool) -> pd.DataFrame:
    frame, window = _expect_frame_and_int(args)
    if validate_only:
        return pd.DataFrame()
    lagged = frame.shift(1)
    delta = frame.diff()
    beta = delta.rolling(window).cov(lagged).div(lagged.rolling(window).var().replace(0.0, np.nan))
    phi = 1.0 + beta
    valid_phi = phi.where((phi > 0.0) & (phi < 1.0))
    halflife = (-np.log(2.0) / np.log(valid_phi)).replace([np.inf, -np.inf], np.nan)
    return cast(pd.DataFrame, halflife)

def _op_kalman_beta(args: list[pd.DataFrame | float], *, validate_only: bool) -> pd.DataFrame:
    left, right, process_noise, observation_noise = _expect_kalman_args(args)
    if validate_only:
        return pd.DataFrame()
    return _kalman_beta_frame(left, right, process_noise=process_noise, observation_noise=observation_noise)

def _op_kalman_residual(args: list[pd.DataFrame | float], *, validate_only: bool) -> pd.DataFrame:
    left, right, process_noise, observation_noise = _expect_kalman_args(args)
    if validate_only:
        return pd.DataFrame()
    beta = _kalman_beta_frame(left, right, process_noise=process_noise, observation_noise=observation_noise)
    aligned_left, aligned_right = left.align(right, join="outer")
    return aligned_left.sub(beta.mul(aligned_right))

def _op_rsi(args: list[pd.DataFrame | float], *, validate_only: bool) -> pd.DataFrame:
    frame, window = _expect_frame_and_int(args)
    if validate_only:
        return pd.DataFrame()
    delta = frame.diff()
    gains = delta.clip(lower=0.0)
    losses = (-delta).clip(lower=0.0)
    alpha = 1.0 / max(window, 1)
    avg_gain = gains.ewm(alpha=alpha, adjust=False, min_periods=window).mean()
    avg_loss = losses.ewm(alpha=alpha, adjust=False, min_periods=window).mean()
    rs = avg_gain.div(avg_loss.replace(0.0, np.nan))
    rsi = 100.0 - (100.0 / (1.0 + rs))
    rsi = rsi.where(avg_loss > 0.0, 100.0)
    both_zero = (avg_gain <= 0.0) & (avg_loss <= 0.0)
    return rsi.where(~both_zero, 50.0)

def _op_sign_flip_prob(args: list[pd.DataFrame | float], *, validate_only: bool) -> pd.DataFrame:
    frame, window = _expect_frame_and_int(args)
    if validate_only:
        return pd.DataFrame()
    sign_change = frame.apply(np.sign).diff()
    return sign_change.ne(0).astype(float).rolling(window).mean()

def _op_neg(args: list[pd.DataFrame | float], *, validate_only: bool) -> pd.DataFrame:
    frame = _expect_frame(args, expected=1)
    if validate_only:
        return pd.DataFrame()
    return -frame

def _op_abs(args: list[pd.DataFrame | float], *, validate_only: bool) -> pd.DataFrame:
    frame = _expect_frame(args, expected=1)
    if validate_only:
        return pd.DataFrame()
    return frame.abs()

def _op_log(args: list[pd.DataFrame | float], *, validate_only: bool) -> pd.DataFrame:
    frame = _expect_frame(args, expected=1)
    if validate_only:
        return pd.DataFrame()
    return cast(pd.DataFrame, np.log(frame.where(frame > 0.0)))

def _op_clip(args: list[pd.DataFrame | float], *, validate_only: bool) -> pd.DataFrame:
    frame = _expect_frame(args[:1], expected=1)
    if len(args) != 3 or not isinstance(args[1], float) or not isinstance(args[2], float):
        raise ValueError("clip expects frame, low, high")
    if validate_only:
        return pd.DataFrame()
    return frame.clip(lower=args[1], upper=args[2])

def _op_gt(args: list[pd.DataFrame | float], *, validate_only: bool) -> pd.DataFrame:
    if len(args) != 2:
        raise ValueError("gt expects 2 arguments")
    if validate_only:
        return pd.DataFrame()
    return _comparison(args[0], args[1], lambda a, b: a > b)

def _op_lt(args: list[pd.DataFrame | float], *, validate_only: bool) -> pd.DataFrame:
    if len(args) != 2:
        raise ValueError("lt expects 2 arguments")
    if validate_only:
        return pd.DataFrame()
    return _comparison(args[0], args[1], lambda a, b: a < b)

def _op_and(args: list[pd.DataFrame | float], *, validate_only: bool) -> pd.DataFrame:
    if len(args) != 2:
        raise ValueError("and expects 2 arguments")
    if validate_only:
        return pd.DataFrame()
    return _logical(args[0], args[1], lambda a, b: a & b)

def _op_not(args: list[pd.DataFrame | float], *, validate_only: bool) -> pd.DataFrame:
    if len(args) != 1:
        raise ValueError("not expects 1 argument")
    if validate_only:
        return pd.DataFrame()
    return _truthy_frame(args[0]).eq(0.0).astype(float)

def _op_where(args: list[pd.DataFrame | float], *, validate_only: bool) -> pd.DataFrame:
    if len(args) != 3:
        raise ValueError("where expects condition, then_value, else_value")
    if validate_only:
        return pd.DataFrame()
    condition, on_true, on_false = args
    condition_frame = _truthy_frame(condition)
    if isinstance(on_true, float) and isinstance(on_false, float):
        true_frame = pd.DataFrame(on_true, index=condition_frame.index, columns=condition_frame.columns)
        false_frame = pd.DataFrame(on_false, index=condition_frame.index, columns=condition_frame.columns)
    else:
        true_frame, false_frame = _aligned_pair(on_true, on_false)
    return true_frame.where(condition_frame > 0.0, false_frame)

def _op_add(args: list[pd.DataFrame | float], *, validate_only: bool) -> pd.DataFrame:
    if len(args) != 2:
        raise ValueError("add expects 2 arguments")
    if validate_only:
        return pd.DataFrame()
    return _binary(args[0], args[1], lambda a, b: a.add(b, fill_value=0.0))

def _op_sub(args: list[pd.DataFrame | float], *, validate_only: bool) -> pd.DataFrame:
    if len(args) != 2:
        raise ValueError("sub expects 2 arguments")
    if validate_only:
        return pd.DataFrame()
    return _binary(args[0], args[1], lambda a, b: a.sub(b, fill_value=0.0))

def _op_mul(args: list[pd.DataFrame | float], *, validate_only: bool) -> pd.DataFrame:
    if len(args) != 2:
        raise ValueError("mul expects 2 arguments")
    if validate_only:
        return pd.DataFrame()
    return _binary(args[0], args[1], lambda a, b: a * b)

def _op_div(args: list[pd.DataFrame | float], *, validate_only: bool) -> pd.DataFrame:
    if len(args) != 2:
        raise ValueError("div expects 2 arguments")
    if validate_only:
        return pd.DataFrame()
    return _binary(args[0], args[1], _safe_div)

_OPERATOR_REGISTRY: dict[str, Any] = {
    "pct_change": _op_pct_change,
    "diff": _op_diff,
    "ema": _op_ema,
    "rolling_mean": _op_rolling_mean,
    "rolling_sum": _op_rolling_sum,
    "rolling_std": _op_rolling_std,
    "rolling_zscore": _op_rolling_zscore,
    "rolling_min": _op_rolling_min,
    "rolling_max": _op_rolling_max,
    "rolling_skew": _op_rolling_skew,
    "rolling_kurt": _op_rolling_kurt,
    "rolling_corr": _op_rolling_corr,
    "rolling_autocorr": _op_rolling_autocorr,
    "rolling_beta": _op_rolling_beta,
    "rolling_hurst": _op_rolling_hurst,
    "mean_reversion_halflife": _op_mean_reversion_halflife,
    "kalman_beta": _op_kalman_beta,
    "kalman_residual": _op_kalman_residual,
    "rsi": _op_rsi,
    "sign_flip_prob": _op_sign_flip_prob,
    "neg": _op_neg,
    "abs": _op_abs,
    "log": _op_log,
    "clip": _op_clip,
    "gt": _op_gt,
    "lt": _op_lt,
    "and": _op_and,
    "not": _op_not,
    "where": _op_where,
    "add": _op_add,
    "sub": _op_sub,
    "mul": _op_mul,
    "div": _op_div,
}


def _apply_operator(
    function_name: str,
    args: list[pd.DataFrame | float],
    *,
    validate_only: bool,
) -> pd.DataFrame:
    handler = _OPERATOR_REGISTRY.get(function_name)
    if handler is None:
        raise ValueError(f"Unsupported feature operator: {function_name}")
    return handler(args, validate_only=validate_only)


def _expect_frame(args: list[pd.DataFrame | float], *, expected: int) -> pd.DataFrame:
    if len(args) != expected or not isinstance(args[0], pd.DataFrame):
        raise ValueError("Expected dataframe argument")
    return args[0]


def _expect_frame_and_int(args: list[pd.DataFrame | float]) -> tuple[pd.DataFrame, int]:
    if len(args) != 2 or not isinstance(args[0], pd.DataFrame) or not isinstance(args[1], float):
        raise ValueError("Expected frame and numeric window")
    return args[0], int(args[1])


def _expect_two_frames_and_int(
    args: list[pd.DataFrame | float],
) -> tuple[pd.DataFrame, pd.DataFrame, int]:
    if (
        len(args) != 3
        or not isinstance(args[0], pd.DataFrame)
        or not isinstance(args[1], pd.DataFrame)
        or not isinstance(args[2], float)
    ):
        raise ValueError("Expected frame, frame, and numeric window")
    return args[0], args[1], int(args[2])


def _expect_frame_and_two_ints(
    args: list[pd.DataFrame | float],
) -> tuple[pd.DataFrame, int, int]:
    if (
        len(args) != 3
        or not isinstance(args[0], pd.DataFrame)
        or not isinstance(args[1], float)
        or not isinstance(args[2], float)
    ):
        raise ValueError("Expected frame and two numeric arguments")
    return args[0], int(args[1]), int(args[2])


def _expect_kalman_args(
    args: list[pd.DataFrame | float],
) -> tuple[pd.DataFrame, pd.DataFrame, float, float]:
    if len(args) == 2 and isinstance(args[0], pd.DataFrame) and isinstance(args[1], pd.DataFrame):
        return args[0], args[1], 1e-5, 1e-3
    if (
        len(args) == 4
        and isinstance(args[0], pd.DataFrame)
        and isinstance(args[1], pd.DataFrame)
        and isinstance(args[2], float)
        and isinstance(args[3], float)
    ):
        return args[0], args[1], float(args[2]), float(args[3])
    raise ValueError("kalman_beta and kalman_residual expect frame, frame, and optional process/observation noise")


def _binary(
    left: pd.DataFrame | float,
    right: pd.DataFrame | float,
    operation: Any,
) -> pd.DataFrame:
    if isinstance(left, pd.DataFrame) and isinstance(right, pd.DataFrame):
        left_frame, right_frame = _aligned_pair(left, right)
        return cast(pd.DataFrame, operation(left_frame, right_frame))
    if isinstance(left, pd.DataFrame):
        return cast(pd.DataFrame, operation(left, right))
    if isinstance(right, pd.DataFrame):
        if operation is _safe_div:
            return cast(pd.DataFrame, _safe_div(pd.DataFrame(float(left), index=right.index, columns=right.columns), right))
        return cast(pd.DataFrame, operation(pd.DataFrame(float(left), index=right.index, columns=right.columns), right))
    raise ValueError("At least one argument must be a dataframe")


def _aligned_pair(
    left: pd.DataFrame | float,
    right: pd.DataFrame | float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if isinstance(left, pd.DataFrame) and isinstance(right, pd.DataFrame):
        return _aligned_frame_pair(left, right)
    if isinstance(left, pd.DataFrame):
        return left, pd.DataFrame(cast(float, right), index=left.index, columns=left.columns)
    if isinstance(right, pd.DataFrame):
        return pd.DataFrame(left, index=right.index, columns=right.columns), right
    raise ValueError("At least one argument must be a dataframe")


def _aligned_frame_pair(left: pd.DataFrame, right: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    left_indexed, right_indexed = left.align(right, join="outer", axis=0)
    left_columns = list(left.columns)
    right_columns = list(right.columns)
    if left_columns == right_columns:
        return (
            left_indexed.reindex(columns=left_columns),
            right_indexed.reindex(columns=right_columns),
        )
    if len(left_columns) == 1 and left_columns[0] not in right_columns:
        target_columns = right_columns
        return (
            _broadcast_single_column_frame(left_indexed, target_columns=target_columns),
            right_indexed.reindex(columns=target_columns),
        )
    if len(right_columns) == 1 and right_columns[0] not in left_columns:
        target_columns = left_columns
        return (
            left_indexed.reindex(columns=target_columns),
            _broadcast_single_column_frame(right_indexed, target_columns=target_columns),
        )
    common_columns = [column for column in left_columns if column in right_columns]
    if common_columns:
        return (
            left_indexed.reindex(columns=common_columns),
            right_indexed.reindex(columns=common_columns),
        )
    return left_indexed.align(right_indexed, join="outer", axis=1, fill_value=np.nan)


def _broadcast_single_column_frame(
    frame: pd.DataFrame,
    *,
    target_columns: list[str],
) -> pd.DataFrame:
    if len(frame.columns) != 1:
        raise ValueError("broadcast_single_column_frame requires exactly one column")
    series = frame.iloc[:, 0]
    return pd.DataFrame(
        {column: series for column in target_columns},
        index=frame.index,
    ).reindex(columns=target_columns)


def _truthy_frame(value: pd.DataFrame | float) -> pd.DataFrame:
    if isinstance(value, pd.DataFrame):
        return value.fillna(0.0).astype(float)
    raise ValueError("Expected dataframe truthy operand")


def _comparison(
    left: pd.DataFrame | float,
    right: pd.DataFrame | float,
    comparator: Any,
) -> pd.DataFrame:
    left_frame, right_frame = _aligned_pair(left, right)
    return cast(pd.DataFrame, comparator(left_frame, right_frame).fillna(False).astype(float))


def _logical(
    left: pd.DataFrame | float,
    right: pd.DataFrame | float,
    operator: Any,
) -> pd.DataFrame:
    left_frame, right_frame = _aligned_pair(left, right)
    return cast(pd.DataFrame, operator(left_frame.fillna(0.0) != 0.0, right_frame.fillna(0.0) != 0.0).astype(float))


def _safe_div(left: pd.DataFrame | float, right: pd.DataFrame | float) -> pd.DataFrame:
    if isinstance(left, pd.DataFrame) and isinstance(right, pd.DataFrame):
        out = left.div(right.replace(0.0, np.nan))
    elif isinstance(left, pd.DataFrame):
        out = left / cast(float, right)
    elif isinstance(right, pd.DataFrame):
        out = pd.DataFrame(float(left), index=right.index, columns=right.columns).div(
            right.replace(0.0, np.nan)
        )
    else:
        raise ValueError("safe_div requires a dataframe operand")
    return out.replace([np.inf, -np.inf], np.nan)


def _hurst_exponent(values: np.ndarray) -> float:
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size < 32:
        return float("nan")
    max_lag = min(20, arr.size // 2)
    lags = [lag for lag in (2, 4, 8, 16, 20) if lag < max_lag]
    if len(lags) < 2:
        lags = list(range(2, max_lag))
    tau: list[float] = []
    lag_values: list[int] = []
    for lag in lags:
        diff = arr[lag:] - arr[:-lag]
        if diff.size < 2:
            continue
        scale = float(np.nanstd(diff))
        if not np.isfinite(scale) or scale <= 0.0:
            continue
        lag_values.append(lag)
        tau.append(scale)
    if len(tau) < 2:
        return float("nan")
    slope = np.polyfit(np.log(lag_values), np.log(tau), 1)[0]
    return float(slope)


def _kalman_beta_frame(
    left: pd.DataFrame,
    right: pd.DataFrame,
    *,
    process_noise: float,
    observation_noise: float,
) -> pd.DataFrame:
    aligned_left, aligned_right = left.align(right, join="outer")
    out = pd.DataFrame(index=aligned_left.index, columns=aligned_left.columns, dtype=float)
    q = max(float(process_noise), 1e-10)
    r = max(float(observation_noise), 1e-10)

    for column in aligned_left.columns:
        y = aligned_left[column].astype(float).to_numpy()
        x = aligned_right[column].astype(float).to_numpy()
        beta_series = np.full(len(y), np.nan, dtype=float)
        state = 0.0
        covariance = 1.0

        for idx, (yy, xx) in enumerate(zip(y, x)):
            if not np.isfinite(yy) or not np.isfinite(xx):
                continue
            if idx == 0 and abs(xx) > 1e-12:
                state = float(yy) / float(xx)

            predicted_covariance = covariance + q
            innovation_variance = (xx * xx * predicted_covariance) + r
            if not np.isfinite(innovation_variance) or innovation_variance <= 0.0:
                continue

            kalman_gain = predicted_covariance * xx / innovation_variance
            innovation = yy - (xx * state)
            state = float(state + kalman_gain * innovation)
            covariance = max(float((1.0 - (kalman_gain * xx)) * predicted_covariance), 1e-10)
            beta_series[idx] = state

        out[column] = beta_series
    return out

