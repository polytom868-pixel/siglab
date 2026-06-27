"""Shared utility functions for CLI subcommand modules."""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast
from collections.abc import Callable

from siglab.config import SiglabConfig, load_settings
from siglab.utils import resolve_path_from_root, safe_float
from siglab.telemetry import (
    build_telemetry_payload,
    provider_metric_paths_for_telemetry,
    trace_paths_for_telemetry,
)


class _ResearchProvider(Protocol):
    is_configured: bool


class _MarketDataProvider(Protocol):
    def current_bundle_context(self) -> dict[str, Any]: ...


class _AncestryStore(Protocol):
    def best(
        self,
        track: str,
        run_session_id: str | None = ...,
    ) -> dict[str, Any] | None: ...

    def experiment_detail(self, spec_hash: str) -> dict[str, Any] | None: ...

    def dashboard_rows(
        self,
        track: str = ...,
        family: str = ...,
        run_session_id: str | None = ...,
    ) -> list[dict[str, Any]]: ...

    def recent(self, track: str, limit: int = ...) -> list[dict[str, Any]]: ...


class _Mutator(Protocol):
    def canonical_dict(self) -> dict[str, Any]: ...


def latest_path(directory: Path, pattern: str) -> Path | None:
    """Find the most recently modified file matching a glob pattern."""
    matches = sorted(directory.glob(pattern), key=lambda item: item.stat().st_mtime)
    return matches[-1] if matches else None


def read_jsonl_with_stats(
    path: Path | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if path is None or not path.exists():
        return (
            [],
            {
                "path": str(path) if path else None,
                "line_count": 0,
                "record_count": 0,
                "malformed_count": 0,
                "non_object_count": 0,
            },
        )
    rows: list[dict[str, Any]] = []
    malformed_count = 0
    non_object_count = 0
    lines = path.read_text(encoding="utf-8").splitlines()
    for line in lines:
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
            non_object_count += 1
    return (
        rows,
        {
            "path": str(path),
            "line_count": len(lines),
            "record_count": len(rows),
            "malformed_count": malformed_count,
            "non_object_count": non_object_count,
        },
    )


def latest_record(
    rows: list[dict[str, Any]],
    *,
    required_value: str | None = None,
) -> dict[str, Any] | None:
    rows = [row for row in rows if _record_has_required_value(row, required_value)]
    if not rows:
        return None
    return sorted(rows, key=_record_sort_key, reverse=True)[0]


def _record_has_required_value(row: dict[str, Any], required_value: str | None) -> bool:
    if required_value is None:
        return True
    if _record_timestamp(row) is None:
        return False
    if required_value == "numeric":
        return safe_float(row.get("value")) is not None
    if required_value == "quote":
        attrs = dict(row.get("attributes") or {})
        return bool(attrs.get("bid") or row.get("value")) and bool(attrs.get("ask"))
    return True

def _record_sort_key(row: dict[str, Any]) -> tuple[int, float, str]:
    timestamp = _record_timestamp(row)
    if timestamp is None:
        return (0, 0.0, str(row.get("evidence_path") or ""))
    return (1, timestamp.timestamp(), str(row.get("evidence_path") or ""))


def _record_timestamp(row: dict[str, Any]) -> datetime | None:
    value = row.get("timestamp") or row.get("observed_at")
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def load_json_if_exists(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def sosovalue_currency_id(rows: list[dict[str, Any]], symbol: str) -> int | None:
    needle = str(symbol or "").strip().lower()
    for row in rows:
        if str(row.get("currencyName") or "").strip().lower() == needle:
            return int(row["currencyId"])
        if str(row.get("fullName") or "").strip().lower() == needle:
            return int(row["currencyId"])
    return None




def sodex_preflight_report(env: dict[str, str] | None = None) -> dict[str, Any]:
    """Check SoDEX signed-path prerequisites and return a readiness report."""
    from siglab.data.feeds import (
        SODEX_ENDPOINT_WEIGHTS,
        SODEX_WEIGHT_BUDGET_PER_MINUTE,
        SUPPORTED_SODEX_SIGNED_ACTIONS,
        UNSUPPORTED_SODEX_SIGNED_ACTIONS,
    )

    source = env if env is not None else os.environ
    api_key_name = str(source.get("SODEX_API_KEY_NAME") or "").strip()
    account_id = str(source.get("SODEX_ACCOUNT_ID") or "").strip()
    nonce_store = str(source.get("SODEX_NONCE_STORE_PATH") or "").strip()
    environment = str(source.get("SODEX_ENVIRONMENT") or "testnet").strip().lower()
    private_key_present = bool(str(source.get("SODEX_PRIVATE_KEY") or "").strip())
    missing: list[str] = []
    if not api_key_name:
        missing.append("SODEX_API_KEY_NAME")
    if not account_id:
        missing.append("SODEX_ACCOUNT_ID")
    else:
        try:
            if int(account_id) < 0:
                missing.append("SODEX_ACCOUNT_ID must be an unsigned integer")
        except ValueError:
            missing.append("SODEX_ACCOUNT_ID must be an unsigned integer")
    if not nonce_store:
        missing.append("SODEX_NONCE_STORE_PATH")
        nonce_store_status = {
            "ready": False,
            "path_present": False,
            "parent_writable": False,
            "file_writable": False,
            "parseable": False,
            "error": "SODEX_NONCE_STORE_PATH is required",
        }
    else:
        nonce_path = Path(nonce_store).expanduser()
        if not nonce_path.is_absolute():
            nonce_path = (Path.cwd() / nonce_path).resolve()
        parent = nonce_path.parent
        parent_exists = parent.exists()
        parent_writable = bool(parent_exists and os.access(parent, os.W_OK))
        file_writable = bool(
            (not nonce_path.exists() and parent_writable)
            or os.access(nonce_path, os.W_OK),
        )
        parseable = True
        nonce_error = None
        if nonce_path.exists():
            try:
                parsed = json.loads(nonce_path.read_text())
                if not isinstance(parsed, dict):
                    parseable = False
                    nonce_error = "nonce store must be a JSON object"
            except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
                parseable = False
                nonce_error = f"nonce store is not parseable JSON: {exc}"
        if not parent_exists:
            nonce_error = "nonce store parent directory does not exist"
        elif not parent_writable:
            nonce_error = "nonce store parent directory is not writable"
        elif not file_writable:
            nonce_error = "nonce store file is not writable"
        nonce_store_status = {
            "ready": bool(
                parent_exists and parent_writable and file_writable and parseable,
            ),
            "path_present": True,
            "parent_writable": parent_writable,
            "file_writable": file_writable,
            "parseable": parseable,
            "error": nonce_error,
        }
        if not nonce_store_status["ready"]:
            missing.append(
                f"SODEX_NONCE_STORE_PATH not ready: {nonce_store_status['error']}",
            )
    if not private_key_present:
        missing.append("SODEX_PRIVATE_KEY")
    if environment not in {"mainnet", "testnet"}:
        missing.append("SODEX_ENVIRONMENT must be mainnet or testnet")
    mainnet_confirmation = str(
        source.get("SODEX_MAINNET_LIVE_WRITE_CONFIRMATION") or "",
    ).strip()
    testnet_passed = (
        str(source.get("SODEX_TESTNET_PREFLIGHT_PASSED") or "").strip().lower()
    )
    if environment == "mainnet":
        if testnet_passed not in {"1", "true", "yes"}:
            missing.append("SODEX_TESTNET_PREFLIGHT_PASSED must be true before mainnet")
        if mainnet_confirmation != "I_UNDERSTAND_MAINNET_RISK":
            missing.append(
                "SODEX_MAINNET_LIVE_WRITE_CONFIRMATION must equal I_UNDERSTAND_MAINNET_RISK",
            )
    return {
        "public_read_ready": True,
        "schema_pinned": True,
        "signed_path": {
            "ready": not missing,
            "environment": environment,
            "signer_ready": private_key_present,
            "signer_type": "evm-private-key" if private_key_present else None,
            "accountID_present": bool(account_id),
            "api_key_name_present": bool(api_key_name),
            "nonce_store_ready": bool(nonce_store_status["ready"]),
            "nonce_store": nonce_store_status,
            "testnet_preflight_passed": testnet_passed in {"1", "true", "yes"},
            "mainnet_confirmation_present": mainnet_confirmation
            == "I_UNDERSTAND_MAINNET_RISK",
            "missing_prerequisites": missing,
        },
        "live_write_allowed": not missing,
        "live_write_refusal_reason": None
        if not missing
        else "missing signed-path prerequisites",
        "access_plan": {
            "preferred_validation_environment": "testnet",
            "mainnet_warning": "Do not attempt signed mainnet writes until testnet/account preflight passes and operator confirms supported chain/deposit requirements.",
            "buildathon_access_request": "Request SoSoValue/SoDEX buildathon access early when missing API/account prerequisites block live validation.",
            "required_operator_inputs": [
                "SoDEX environment: testnet first, mainnet only after explicit operator confirmation",
                "SoDEX API key name",
                "SoDEX accountID",
                "nonce store path",
                "isolated EVM signer private key",
                "user address for private/account WebSocket probes",
            ],
        },
        "next_actions": [
            "prefer SODEX_ENVIRONMENT=testnet for first signed validation",
            *[f"set {name}" for name in missing if name.startswith("SODEX_")],
            "run siglab sodex-preflight --json again before any deploy/export/live attempt",
        ],
        "request_weight_budget_per_minute": SODEX_WEIGHT_BUDGET_PER_MINUTE,
        "documented_endpoint_weights": dict(sorted(SODEX_ENDPOINT_WEIGHTS.items())),
        "rate_limit_scope": {
            "scope": "per_ip",
            "local_scheduler_only": True,
            "operator_warning": "SigLab's built-in SoDEX weight scheduler is process-local. Use an external shared limiter when multiple processes share one egress IP.",
        },
        "supported_signed_actions": sorted(SUPPORTED_SODEX_SIGNED_ACTIONS),
        "unsupported_signed_actions": dict(UNSUPPORTED_SODEX_SIGNED_ACTIONS),
    }


def parse_sodex_enum(value: str, aliases: dict[str, int], field_name: str) -> int:
    raw = str(value).strip()
    if raw.isdigit():
        parsed = int(raw)
        if parsed in set(aliases.values()):
            return parsed
    normalized = raw.upper().replace("-", "_")
    if normalized in aliases:
        return aliases[normalized]
    accepted = ", ".join(
        [*aliases.keys(), *[str(v) for v in sorted(set(aliases.values()))]],
    )
    print(
        f"--{field_name.replace('_', '-')} must be one of: {accepted}",
        file=sys.stderr,
    )
    raise SystemExit(1)


def require_sosovalue_config(settings: SiglabConfig) -> Path:
    config_path = resolve_path_from_root(
        settings.sosovalue_config_path,
        root_dir=settings.root_dir,
    )
    if not config_path.exists():
        print(
            f"SOSOVALUE_CONFIG_PATH is required for this command and must point to an existing file. Tried: {config_path}. Create it with `cp config.example.json config.json` or point SOSOVALUE_CONFIG_PATH at an existing SoSoValue config.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    settings.sosovalue_config_path = config_path
    return config_path


def display_deployment_record(
    *,
    settings: SiglabConfig,
    record: dict[str, Any],
) -> dict[str, Any]:
    normalized = dict(record)
    for key in [
        "strategy_dir",
        "spec_path",
        "manifest_path",
        "readme_path",
        "config_path",
    ]:
        normalized[key] = display_path_static(
            normalized.get(key),
            root_dir=settings.root_dir,
        )
    return normalized


def display_path_static(value: str | Path | None, root_dir: Path) -> str:
    """Resolve a path for display, relative to root_dir if possible."""
    from siglab.utils import display_path as _dp

    return cast(str, _dp(value, root_dir=root_dir))


def strip_audit_fields(
    payload: dict[str, Any] | list[Any],
) -> dict[str, Any] | list[Any]:
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


def agent_safe_recent_results(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned_rows: list[dict[str, Any]] = []
    for row in rows:
        cleaned = dict(row)
        cleaned["summary"] = strip_audit_fields(dict(row.get("summary") or {}))
        cleaned_rows.append(cleaned)
    return cleaned_rows












def write_artifact(
    settings: SiglabConfig,
    track: str,
    evaluation: dict[str, Any],
) -> Path:
    from siglab.utils import write_json

    target_dir = settings.artifact_dir / track
    target_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = target_dir / f"{timestamp}_{evaluation['spec_hash']}.json"
    write_json(target, evaluation)
    return cast(Path, target)


def parse_family_scope(
    family: str | None,
    families: str | None,
) -> str | list[str] | None:
    if family and families:
        print("Use either --family or --families, not both", file=sys.stderr)
        raise SystemExit(1)
    if family:
        return family
    if not families:
        return None
    parsed = [item.strip() for item in str(families).split(",") if item.strip()]
    if not parsed:
        print("--families must contain at least one family", file=sys.stderr)
        raise SystemExit(1)
    return parsed


def _format_optional_pct(value: float | str | None) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.2%}"
    except (TypeError, ValueError):
        return "n/a"


def _format_optional_number(value: float | str | None) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "n/a"


def external_research_from_llm_trace(
    *,
    llm_trace: dict[str, Any] | None,
    web_researcher: _ResearchProvider,
) -> dict[str, Any]:
    payload = tool_only_external_research(web_researcher=web_researcher)
    trace = dict((llm_trace or {}).get("trace") or {})
    tool_calls = list(trace.get("tool_calls") or [])
    reports: list[dict[str, Any]] = []
    queries: list[str] = []
    for tool_call in tool_calls:
        if str(tool_call.get("name") or "") != "tavily_search":
            continue
        result = dict(tool_call.get("result") or {})
        if not bool(result.get("ok")):
            continue
        query = str(result.get("query") or "").strip()
        if not query:
            continue
        queries.append(query)
        reports.append(
            {
                "query": query,
                "answer": result.get("answer"),
                "insights": list(result.get("insights") or []),
                "sources": list(result.get("sources") or []),
            },
        )
    if reports:
        payload["provider"] = "tavily_tool_calls"
        payload["queries"] = queries
        payload["reports"] = reports
    return payload


_HTML_CACHE: dict[str, str] = {}


def _render_html_template(name: str, **kwargs: Any) -> str:
    if name not in _HTML_CACHE:
        template_path = Path(__file__).parent.parent / "assets" / f"{name}.html"
        _HTML_CACHE[name] = template_path.read_text(encoding="utf-8")
    return _HTML_CACHE[name].format(**kwargs)


def add_json_flag(
    parser: argparse.ArgumentParser,
    *,
    dest: str = "as_json",
    default: bool = False,
    help_text: str | None = None,
) -> None:
    parser.add_argument(
        "--json",
        action="store_true",
        dest=dest,
        default=default,
        help=help_text or "Emit machine-readable JSON to stdout.",
    )


def maybe_print_json(payload: object, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, default=str))


def write_json_and_maybe_print(
    path: Path,
    payload: object,
    *,
    as_json: bool,
    writer: Callable[..., Any] | None = None,
) -> Path:
    if writer is None:
        from siglab.utils import write_json as _write_json

        writer = _write_json
    written = writer(path, payload)
    maybe_print_json(payload, as_json=as_json)
    return written if isinstance(written, Path) else path


def display_paths(
    values: str | Path | list[str | Path] | None,
    *,
    root_dir: Path | None,
) -> list[str | None]:
    from siglab.utils import display_path as _dp

    if values is None:
        return []
    if not isinstance(values, list):
        return [_dp(values, root_dir=root_dir)]
    return [_dp(item, root_dir=root_dir) for item in values]
from siglab.cli.rich_utils import get_console, make_table, print_json


def add_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser(
        "market-report",
        help="Build a deterministic SoSoValue + SoDEX evidence-linked market report.",
    )
    parser.add_argument("--entity", default="BTC")
    parser.add_argument("--sosovalue-evidence", default=None)
    parser.add_argument("--sodex-evidence", default=None)
    parser.add_argument("--output", default=None)
    parser.add_argument("--html-output", default=None)
    add_json_flag(parser)


def run_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    evidence_dir = settings.root_dir / "runs" / "evidence"
    sosovalue_path = (
        resolve_path_from_root(args.sosovalue_evidence, root_dir=settings.root_dir)
        if args.sosovalue_evidence
        else evidence_dir / "sosovalue.jsonl"
    )
    sodex_path = (
        resolve_path_from_root(args.sodex_evidence, root_dir=settings.root_dir)
        if args.sodex_evidence
        else evidence_dir / "sodex_rest.jsonl"
    )
    report = build_market_report(
        entity=str(args.entity or "BTC").upper(),
        sosovalue_evidence=sosovalue_path,
        sodex_evidence=sodex_path,
    )
    output = (
        resolve_path_from_root(args.output, root_dir=settings.root_dir)
        if args.output
        else settings.root_dir / "runs" / "market_report.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    html_output = (
        resolve_path_from_root(args.html_output, root_dir=settings.root_dir)
        if args.html_output
        else settings.root_dir / "runs" / "market_report.html"
    )
    html_output.parent.mkdir(parents=True, exist_ok=True)
    html_output.write_text(market_report_html(report), encoding="utf-8")
    payload = {
        "output": str(output),
        "html_output": str(html_output),
        "entity": report["entity"],
        "status": report["status"],
        "warnings": report["warnings"],
    }
    if getattr(args, "as_json", False):
        print_json(payload)
        return
    print_json(payload)


def build_market_report(
    *,
    entity: str,
    sosovalue_evidence: Path | None,
    sodex_evidence: Path | None,
) -> dict[str, Any]:
    soso_rows, soso_read_stats = read_jsonl_with_stats(sosovalue_evidence)
    sodex_rows, sodex_read_stats = read_jsonl_with_stats(sodex_evidence)
    entity_upper = entity.upper()
    etf_entity = f"us-{entity_upper.lower()}-spot"
    quote_entity = f"{entity_upper}-USD"
    latest_flow = latest_record(
        [
            row
            for row in soso_rows
            if row.get("entity") == etf_entity
            and row.get("relation") == "total_net_inflow"
        ],
        required_value="numeric",
    )
    latest_assets = latest_record(
        [
            row
            for row in soso_rows
            if row.get("entity") == etf_entity
            and row.get("relation") == "total_net_assets"
        ],
        required_value="numeric",
    )
    news_rows = [
        row
        for row in soso_rows
        if row.get("module") == "Feeds"
        and str(row.get("entity") or "").upper() in {entity_upper, "MARKET"}
    ]
    latest_news = sorted(
        [row for row in news_rows if str(row.get("value") or "").strip()],
        key=_record_sort_key_internal,
        reverse=True,
    )[:5]
    quote = latest_record(
        [
            row
            for row in sodex_rows
            if str(row.get("entity") or "").upper() == quote_entity
        ],
        required_value="quote",
    )
    preflight = sodex_preflight_report()
    warnings = [
        "Evidence links are temporal/contextual and are not causal claims.",
        "Signed SoDEX execution is refused unless preflight reports live_write_allowed=true.",
    ]
    missing: list[str] = []
    if latest_flow is None:
        missing.append("latest ETF flow evidence")
    if quote is None:
        missing.append("latest SoDEX quote evidence")
    if not latest_news:
        missing.append("recent feed evidence")
    status = "PARTIAL" if missing else "READY_FOR_OPERATOR_REVIEW"
    signal = _market_signal_summary(
        entity=entity_upper,
        latest_flow=latest_flow,
        latest_assets=latest_assets,
        quote=quote,
        latest_news=latest_news,
        preflight=preflight,
    )
    decision_support = _market_decision_support(
        entity=entity_upper,
        signal=signal,
        missing=missing,
        preflight=preflight,
    )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "entity": entity_upper,
        "status": status,
        "missing": missing,
        "signal_summary": signal,
        "decision_support": decision_support,
        "sosovalue": {
            "evidence_path": str(sosovalue_evidence) if sosovalue_evidence else None,
            "latest_flow": latest_flow,
            "latest_assets": latest_assets,
            "latest_news": latest_news,
        },
        "sodex": {
            "evidence_path": str(sodex_evidence) if sodex_evidence else None,
            "latest_quote": quote,
            "preflight": preflight,
        },
        "warnings": warnings,
        "evidence_selection": {
            "latest_valid_semantics": "parsed_timestamp_then_observed_at_skip_invalid_required_values",
            "sosovalue_rows_read": len(soso_rows),
            "sodex_rows_read": len(sodex_rows),
            "news_rows_considered": len(news_rows),
            "sosovalue_read_stats": soso_read_stats,
            "sodex_read_stats": sodex_read_stats,
        },
    }


def _market_signal_summary(
    *,
    entity: str,
    latest_flow: dict[str, Any] | None,
    latest_assets: dict[str, Any] | None,
    quote: dict[str, Any] | None,
    latest_news: list[dict[str, Any]],
    preflight: dict[str, Any],
) -> dict[str, Any]:
    flow_value = safe_float((latest_flow or {}).get("value"))
    if flow_value is None:
        flow_bias = "unknown"
    elif flow_value > 0:
        flow_bias = "ETF inflow"
    elif flow_value < 0:
        flow_bias = "ETF outflow"
    else:
        flow_bias = "flat ETF flow"
    quote_attrs = dict((quote or {}).get("attributes") or {})
    bid = quote_attrs.get("bid") or (quote or {}).get("value")
    ask = quote_attrs.get("ask")
    return {
        "headline": f"{entity}: {flow_bias}; SoDEX quote bid={(bid if bid is not None else 'missing')} ask={(ask if ask is not None else 'missing')}; news_items={len(latest_news)}; live_write_allowed={bool(preflight.get('live_write_allowed'))}",
        "flow_direction": flow_bias,
        "flow_value": flow_value,
        "flow_timestamp": (latest_flow or {}).get("timestamp"),
        "net_assets": safe_float((latest_assets or {}).get("value")),
        "quote_bid": bid,
        "quote_ask": ask,
        "news_titles": [str(row.get("value") or "")[:180] for row in latest_news],
        "operator_action": "review_only_signed_execution_blocked"
        if not preflight.get("live_write_allowed")
        else "review_before_any_live_order",
        "confidence": "medium" if latest_flow and quote else "low",
        "causality": "not_claimed",
    }


def _market_decision_support(
    *,
    entity: str,
    signal: dict[str, Any],
    missing: list[str],
    preflight: dict[str, Any],
) -> dict[str, Any]:
    quote_bid = signal.get("quote_bid")
    quote_ask = signal.get("quote_ask")
    flow_direction = str(signal.get("flow_direction") or "unknown")
    evidence_complete = not missing
    if not evidence_complete:
        stance = "NO_ACTION_INCOMPLETE_EVIDENCE"
    elif (
        flow_direction in {"ETF inflow", "ETF outflow"}
        and quote_bid is not None
        and (quote_ask is not None)
    ):
        stance = "REVIEW_CONTEXT_NOT_TRADE_SIGNAL"
    else:
        stance = "WATCH_ONLY"
    confirmations = [
        "refresh SoSoValue ETF flow/news evidence before acting",
        "refresh SoDEX quote/orderbook context before acting",
        "run strategy evaluation; do not trade from this report alone",
    ]
    invalidations = [
        "missing or malformed evidence rows increase uncertainty",
        "latest SoDEX quote is unavailable or stale",
        "SoDEX preflight refuses live write prerequisites",
    ]
    next_actions = [
        f"run bounded SigLab evaluation for {entity}-related families",
        "inspect evidence graph for non-causal narrative links",
        "keep signed execution disabled unless sodex-preflight reports live_write_allowed=true",
    ]
    if preflight.get("live_write_allowed"):
        next_actions.append(
            "if operator still proceeds, require manual confirmation and dry-run preview before live write",
        )
    return {
        "stance": stance,
        "use_case": "operator_decision_support",
        "not_a_trade_signal": True,
        "evidence_complete": evidence_complete,
        "confirmations_required": confirmations,
        "invalidation_checks": invalidations,
        "next_actions": next_actions,
        "risk_controls": [
            "no causal claim",
            "no automatic order submission",
            "signed SoDEX execution requires explicit preflight success",
            "USD cost is not claimed for provider usage",
        ],
    }


def market_report_html(report: dict[str, Any]) -> str:

    def esc(value: object) -> str:
        return html.escape(str(value))

    signal = dict(report.get("signal_summary") or {})
    decision = dict(report.get("decision_support") or {})
    selection = dict(report.get("evidence_selection") or {})
    soso_stats = dict(selection.get("sosovalue_read_stats") or {})
    sodex_stats = dict(selection.get("sodex_read_stats") or {})

    return _render_html_template(
        "market_report",
        entity=esc(report.get("entity")),
        status=esc(report.get("status")),
        generated_at=esc(report.get("generated_at")),
        headline=esc(signal.get("headline")),
        flow_direction=esc(signal.get("flow_direction")),
        flow_value=esc(signal.get("flow_value")),
        net_assets=esc(signal.get("net_assets")),
        operator_action=esc(signal.get("operator_action")),
        causality=esc(signal.get("causality")),
        stance=esc(decision.get("stance")),
        next_actions="".join(
            f"<li>{esc(item)}</li>" for item in list(decision.get("next_actions") or [])
        ),
        not_a_trade_signal=esc(decision.get("not_a_trade_signal")),
        news_items="".join(
            f"<li>{esc(item)}</li>" for item in list(signal.get("news_titles") or [])
        )
        or "<li>missing</li>",
        selection_semantics=esc(selection.get("latest_valid_semantics")),
        soso_record_count=esc(soso_stats.get("record_count")),
        soso_malformed=esc(soso_stats.get("malformed_count")),
        soso_non_object=esc(soso_stats.get("non_object_count")),
        sodex_record_count=esc(sodex_stats.get("record_count")),
        sodex_malformed=esc(sodex_stats.get("malformed_count")),
        sodex_non_object=esc(sodex_stats.get("non_object_count")),
        missing_items="".join(
            f"<li>{esc(item)}</li>" for item in list(report.get("missing") or [])
        )
        or "<li>none</li>",
        warnings_items="".join(
            f"<li>{esc(item)}</li>" for item in list(report.get("warnings") or [])
        ),
    )


def _record_sort_key_internal(row: dict[str, Any]) -> tuple[int, float, str]:
    """Internal sort key for market report news rows."""
    timestamp = _record_timestamp(row)
    if timestamp is None:
        return (0, 0.0, str(row.get("evidence_path") or ""))
    return (1, timestamp.timestamp(), str(row.get("evidence_path") or ""))


def telemetry_add_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser(
        "telemetry-report",
        help="Aggregate empirical LLM/tool telemetry from run trace artifacts.",
    )
    parser.add_argument("--track", default="all")
    parser.add_argument("--run-session-id", default=None)
    parser.add_argument("--json", action="store_true")


def telemetry_run_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    trace_paths = trace_paths_for_telemetry(
        settings=settings,
        track=str(args.track or "all"),
        run_session_id=args.run_session_id,
    )
    provider_metric_paths = provider_metric_paths_for_telemetry(
        settings=settings,
        run_session_id=args.run_session_id,
    )
    payload = build_telemetry_payload(
        trace_paths=trace_paths,
        provider_metric_paths=provider_metric_paths,
    )
    if getattr(args, "json", False):
        print_json(payload)
    else:
        import json as _json

        table = make_table(title="Telemetry Report")
        table.add_column("Metric", style="label", no_wrap=True)
        table.add_column("Value")
        table.add_row("trace_count", str(payload["trace_count"]))
        table.add_row(
            "stage_counts",
            _json.dumps(payload["stage_counts"], sort_keys=True),
        )
        table.add_row(
            "provider_counts",
            _json.dumps(payload["provider_counts"], sort_keys=True),
        )
        table.add_row(
            "model_counts",
            _json.dumps(payload["model_counts"], sort_keys=True),
        )
        table.add_row("tool_invocation_count", str(payload["tool_invocation_count"]))
        table.add_row(
            "tool_counts",
            _json.dumps(payload["tool_counts"], sort_keys=True),
        )
        table.add_row(
            "tool_latency_ms",
            _json.dumps(payload["tool_latency_ms"], sort_keys=True),
        )
        table.add_row(
            "provider_metrics_status",
            str(payload["provider_metrics_status"]),
        )
        table.add_row("confidence", str(payload["confidence"]))
        get_console().print(table)
