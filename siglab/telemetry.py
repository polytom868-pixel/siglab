from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from collections.abc import Iterable

from siglab.utils import percentile as _percentile
from siglab.utils import safe_float


@dataclass(frozen=True)
class EmpiricalEstimate:
    sample_count: int
    p50_latency_ms: float | None
    p95_latency_ms: float | None
    mean_prompt_tokens: float | None
    mean_completion_tokens: float | None
    mean_total_tokens: float | None
    retry_rate: float | None
    failure_rate: float | None
    confidence: str
    calibration_error_known: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_count": self.sample_count,
            "p50_latency_ms": self.p50_latency_ms,
            "p95_latency_ms": self.p95_latency_ms,
            "mean_prompt_tokens": self.mean_prompt_tokens,
            "mean_completion_tokens": self.mean_completion_tokens,
            "mean_total_tokens": self.mean_total_tokens,
            "retry_rate": self.retry_rate,
            "failure_rate": self.failure_rate,
            "confidence": self.confidence,
            "calibration_error_known": self.calibration_error_known,
        }


def estimate_from_provider_snapshots(
    snapshots: Iterable[dict[str, Any]],
) -> EmpiricalEstimate:
    rows = [dict(item) for item in snapshots]
    latencies = [
        float(value)
        for row in rows
        for value in (row.get("p50_ms"), row.get("p95_ms"))
        if isinstance(value, (int, float))
    ]
    usage_rows = [dict(row.get("usage") or {}) for row in rows]
    prompt_tokens = [_float_or_none(row.get("prompt_tokens")) for row in usage_rows]
    completion_tokens = [
        _float_or_none(row.get("completion_tokens")) for row in usage_rows
    ]
    total_tokens = [_float_or_none(row.get("total_tokens")) for row in usage_rows]
    retry_counts = [_float_or_none(row.get("retry_count")) for row in rows]
    success_rates = [_float_or_none(row.get("success_rate")) for row in rows]
    sample_count = len(rows)
    mean_success = _mean(success_rates)
    return EmpiricalEstimate(
        sample_count=sample_count,
        p50_latency_ms=_percentile(latencies, 50),
        p95_latency_ms=_percentile(latencies, 95),
        mean_prompt_tokens=_mean(prompt_tokens),
        mean_completion_tokens=_mean(completion_tokens),
        mean_total_tokens=_mean(total_tokens),
        retry_rate=_mean(retry_counts),
        failure_rate=1.0 - mean_success if mean_success is not None else None,
        confidence=_confidence(sample_count),
    )


def aggregate_trace_telemetry(trace_paths: Iterable[Path]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    tool_rows: list[dict[str, Any]] = []
    for path in trace_paths:
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            continue
        stage = str(payload.get("stage") or Path(path).stem.replace("_trace", ""))
        trace = dict(payload.get("claude_trace") or {})
        provider = trace.get("provider")
        model = trace.get("model")
        rows.append(
            {
                "stage": stage,
                "provider": provider,
                "model": model,
                "tool_rounds_used": int(trace.get("tool_rounds_used") or 0),
                "tool_count_available": int(trace.get("tool_count_available") or 0),
                "had_error": bool(trace.get("error") or payload.get("error")),
            },
        )
        for call in list(trace.get("tool_calls") or []):
            if not isinstance(call, dict):
                continue
            tool_rows.append(
                {
                    "stage": stage,
                    "name": str(call.get("name") or ""),
                    "latency_ms": _float_or_none(call.get("latency_ms")) or 0.0,
                    "had_error": bool((call.get("result") or {}).get("error"))
                    if isinstance(call.get("result"), dict)
                    else False,
                },
            )
    return {
        "trace_count": len(rows),
        "stage_counts": dict(sorted(_count(row["stage"] for row in rows).items())),
        "provider_counts": dict(
            sorted(
                _count(row["provider"] for row in rows if row.get("provider")).items(),
            ),
        ),
        "model_counts": dict(
            sorted(_count(row["model"] for row in rows if row.get("model")).items()),
        ),
        "tool_invocation_count": len(tool_rows),
        "tool_counts": dict(
            sorted(
                _count(row["name"] for row in tool_rows if row.get("name")).items(),
            ),
        ),
        "tool_latency_ms": {
            "p50": _percentile([float(row["latency_ms"]) for row in tool_rows], 50),
            "p95": _percentile([float(row["latency_ms"]) for row in tool_rows], 95),
        },
        "error_count": sum(1 for row in rows if row["had_error"]),
        "tool_error_count": sum(1 for row in tool_rows if row["had_error"]),
        "confidence": _confidence(len(rows)),
        "calibration_error_known": False,
    }


def aggregate_provider_metrics_artifacts(
    metric_paths: Iterable[Path],
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    malformed_count = 0
    for path in metric_paths:
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError:
            malformed_count += 1
            continue
        if Path(path).suffix == ".jsonl":
            for line in text.splitlines():
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    malformed_count += 1
                    continue
                if isinstance(payload, dict):
                    rows.append(payload)
                else:
                    malformed_count += 1
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            malformed_count += 1
            continue
        if isinstance(payload, dict):
            rows.append(payload)
        else:
            malformed_count += 1
    snapshots = [
        dict(row.get("provider_metrics") or row.get("metrics") or {}) for row in rows
    ]
    usage_rows = [dict(snapshot.get("usage") or {}) for snapshot in snapshots]
    context_rows = [
        dict(snapshot.get("context_pressure") or {}) for snapshot in snapshots
    ]
    credit_rows = [
        dict(snapshot.get("credit_pressure") or {}) for snapshot in snapshots
    ]
    return {
        "artifact_count": len(rows),
        "malformed_count": malformed_count,
        "providers": dict(
            sorted(
                _count(
                    snapshot.get("provider")
                    for snapshot in snapshots
                    if snapshot.get("provider")
                ).items(),
            ),
        ),
        "models": dict(
            sorted(
                _count(
                    snapshot.get("model")
                    for snapshot in snapshots
                    if snapshot.get("model")
                ).items(),
            ),
        ),
        "latest": rows[-1] if rows else None,
        "usage": {
            "prompt_tokens": _last_number(usage_rows, "prompt_tokens"),
            "completion_tokens": _last_number(usage_rows, "completion_tokens"),
            "total_tokens": _last_number(usage_rows, "total_tokens"),
            "priced_tokens": _last_number(usage_rows, "priced_tokens"),
            "credits_estimate": _last_number(usage_rows, "credits_estimate"),
            "cost_usd": None,
            "cost_status": _last_value(usage_rows, "cost_status"),
        },
        "context_pressure": {
            "event_count": _last_number(context_rows, "event_count"),
            "latest": _last_value(context_rows, "latest"),
        },
        "credit_pressure": {
            "event_count": _last_number(credit_rows, "event_count"),
            "latest": _last_value(credit_rows, "latest"),
        },
        "confidence": _confidence(len(rows)),
    }


def _confidence(sample_count: int) -> str:
    if sample_count >= 30:
        return "good"
    if sample_count >= 10:
        return "medium"
    return "poor"


_float_or_none = safe_float


def _mean(values: Iterable[float | None]) -> float | None:
    clean = [float(value) for value in values if value is not None]
    if not clean:
        return None
    return sum(clean) / len(clean)


def _last_number(rows: list[dict[str, Any]], key: str) -> float | None:
    for row in reversed(rows):
        value = _float_or_none(row.get(key))
        if value is not None:
            return value
    return None


def _last_value(rows: list[dict[str, Any]], key: str) -> Any:
    for row in reversed(rows):
        value = row.get(key)
        if value is not None:
            return value
    return None


def _count(values: Iterable[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value)
        counts[key] = counts.get(key, 0) + 1
    return counts
