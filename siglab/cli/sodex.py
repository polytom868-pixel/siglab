"""SoDEX subcommands: sodex-preflight, sodex-ws-probe, sodex-preview, valuechain-preflight."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from typing import Any

import httpx

from siglab.config import load_settings
from siglab.data import EvidenceStore, sodex_ws_evidence
from siglab.live.sodex_signing import (
    build_signature_input,
    canonical_json,
    http_body_from_action_payload,
    perps_cancel_item,
    perps_cancel_order_body,
    perps_new_order_body,
    perps_order_item,
    perps_schedule_cancel_body,
    perps_update_leverage_body,
    perps_update_margin_body,
)
from siglab.live.sodex_ws import SoDEXWebSocketClient, SoDEXWebSocketError
from siglab.path_utils import resolve_path_from_root
from siglab.cli.helpers import parse_sodex_enum, sodex_preflight_report


SODEX_SIDE_ALIASES = {"BUY": 1, "SELL": 2}
SODEX_ORDER_TYPE_ALIASES = {"LIMIT": 1, "MARKET": 2}
SODEX_TIME_IN_FORCE_ALIASES = {"GTC": 1, "FOK": 2, "IOC": 3, "GTX": 4}
SODEX_POSITION_SIDE_ALIASES = {"BOTH": 1, "LONG": 2, "SHORT": 3}
SODEX_MODIFIER_ALIASES = {"NORMAL": 1, "STOP": 2, "BRACKET": 3, "ATTACHED_STOP": 4}
SODEX_MARGIN_MODE_ALIASES = {"ISOLATED": 1, "CROSS": 2}


def add_subparser(subparsers) -> None:
    # sodex-preflight
    preflight_parser = subparsers.add_parser("sodex-preflight")
    preflight_parser.add_argument("--json", action="store_true")

    # valuechain-preflight
    vc_parser = subparsers.add_parser("valuechain-preflight")
    vc_parser.add_argument("--rpc-url", default="https://mainnet.valuechain.xyz")
    vc_parser.add_argument("--expected-chain-id", type=int, default=286623)
    vc_parser.add_argument("--json", action="store_true")

    # sodex-ws-probe
    ws_parser = subparsers.add_parser("sodex-ws-probe")
    ws_parser.add_argument("--environment", choices=["mainnet", "testnet"], default="mainnet")
    ws_parser.add_argument("--market", choices=["spot", "perps"], default="perps")
    ws_parser.add_argument("--channel", default="allBookTicker")
    ws_parser.add_argument("--symbol", default=None)
    ws_parser.add_argument("--user-address", default=None)
    ws_parser.add_argument("--account-id", type=int, default=None)
    ws_parser.add_argument("--timeout-seconds", type=float, default=8.0)
    ws_parser.add_argument("--evidence-output", default=None)
    ws_parser.add_argument("--json", action="store_true")

    # sodex-preview
    preview_parser = subparsers.add_parser("sodex-preview")
    preview_parser.add_argument(
        "--kind",
        choices=["new-order", "cancel-order", "schedule-cancel", "update-leverage", "update-margin"],
        required=True,
    )
    preview_parser.add_argument("--account-id", type=int, required=True)
    preview_parser.add_argument("--symbol-id", type=int, required=True)
    preview_parser.add_argument("--nonce", type=int, required=True)
    preview_parser.add_argument("--cl-ord-id", default="siglab-preview")
    preview_parser.add_argument("--modifier", default="NORMAL")
    preview_parser.add_argument("--side", default="BUY")
    preview_parser.add_argument("--order-type", default="LIMIT")
    preview_parser.add_argument("--time-in-force", default="GTC")
    preview_parser.add_argument("--price", default=None)
    preview_parser.add_argument("--quantity", default=None)
    preview_parser.add_argument("--funds", default=None)
    preview_parser.add_argument("--order-id", type=int, default=None)
    preview_parser.add_argument("--orig-cl-ord-id", default=None)
    preview_parser.add_argument("--scheduled-timestamp", type=int, default=None)
    preview_parser.add_argument("--amount", default=None)
    preview_parser.add_argument("--reduce-only", action="store_true")
    preview_parser.add_argument("--position-side", default="BOTH")
    preview_parser.add_argument("--leverage", type=int, default=1)
    preview_parser.add_argument("--margin-mode", default="ISOLATED")
    preview_parser.add_argument("--json", action="store_true", help="Accepted for CLI consistency; output is always JSON.")


def run_sodex_preflight(args: argparse.Namespace) -> None:
    report = sodex_preflight_report()
    if getattr(args, "json", False):
        print(json.dumps(report, indent=2, sort_keys=True))
        return
    print(f"public_read_ready={report['public_read_ready']}")
    print(f"schema_pinned={report['schema_pinned']}")
    print(f"signed_path_ready={report['signed_path']['ready']}")
    print(f"environment={report['signed_path']['environment']}")
    if report["signed_path"]["missing_prerequisites"]:
        print("missing_prerequisites=" + ",".join(report["signed_path"]["missing_prerequisites"]))
    print(f"live_write_allowed={report['live_write_allowed']}")


async def run_valuechain_preflight(args: argparse.Namespace) -> None:
    rpc_url = str(args.rpc_url).rstrip("/")
    expected = int(args.expected_chain_id)
    report: dict[str, Any] = {
        "rpc_url": rpc_url,
        "expected_chain_id": expected,
        "source": "https://sodex.com/documentation/user-guide/faq/how-do-i-add-the-valuechain-network",
        "ready": False,
    }
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.post(
                rpc_url,
                json={"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []},
                headers={"Content-Type": "application/json"},
            )
        report["http_status"] = int(response.status_code)
        payload = response.json()
        report["response_shape"] = sorted(payload.keys()) if isinstance(payload, dict) else type(payload).__name__
        chain_hex = payload.get("result") if isinstance(payload, dict) else None
        report["chain_id_hex"] = chain_hex
        report["chain_id"] = int(str(chain_hex), 16) if isinstance(chain_hex, str) and chain_hex.startswith("0x") else None
        report["ready"] = report["chain_id"] == expected
        if not report["ready"]:
            report["missing_or_wrong"] = "ValueChain RPC did not return the documented chain ID"
    except (httpx.HTTPError, TypeError, ValueError) as exc:
        report["error_class"] = type(exc).__name__
        report["error"] = str(exc)
    if getattr(args, "json", False):
        print(json.dumps(report, indent=2, sort_keys=True))
        return
    status = "READY" if report.get("ready") else "NOT READY"
    print(f"ValueChain RPC {status}: chain_id={report.get('chain_id')} expected={expected} rpc={rpc_url}")


async def run_sodex_ws_probe(args: argparse.Namespace) -> None:
    params: dict[str, Any] = {"channel": str(args.channel)}
    if args.symbol:
        params["symbol"] = str(args.symbol)
    if args.user_address:
        params["user"] = str(args.user_address)
    if args.account_id is not None:
        params["accountID"] = int(args.account_id)
    report: dict[str, Any] = {
        "environment": args.environment,
        "market": args.market,
        "params": params,
        "live_write": False,
        "signed": False,
        "ready": False,
    }
    client = SoDEXWebSocketClient(
        environment=args.environment,
        market=args.market,
        idle_timeout_s=float(args.timeout_seconds),
        pong_timeout_s=min(5.0, float(args.timeout_seconds)),
        max_reconnects=0,
    )
    try:
        ack = await client.subscribe(params, request_id=1)
        report["subscribe_ack"] = ack
        try:
            update = await client.recv_update(timeout_s=float(args.timeout_seconds))
        except SoDEXWebSocketError as exc:
            report["update_error_class"] = type(exc).__name__
            report["update_error"] = str(exc)
        else:
            report["first_update_keys"] = sorted(update.keys())
            report["first_update_channel"] = update.get("channel")
            report["first_update_type"] = update.get("type")
            if args.evidence_output:
                settings = load_settings()
                evidence_output = resolve_path_from_root(args.evidence_output, root_dir=settings.root_dir)
                records = sodex_ws_evidence(
                    update,
                    observed_at=datetime.now(UTC).isoformat(),
                    evidence_path=f"sodex/ws/{args.market}/{args.channel}",
                )
                store = EvidenceStore(evidence_output)
                appended = store.append_many(records)
                summary_output = evidence_output.with_suffix(".summary.json")
                summary = store.write_summary(summary_output)
                report["evidence_output"] = str(evidence_output)
                report["evidence_summary_output"] = str(summary_output)
                report["evidence_records_appended"] = appended
                report["evidence_summary_record_count"] = summary["record_count"]
        report["ready"] = True
    except (SoDEXWebSocketError, OSError, TypeError, ValueError) as exc:
        report["error_class"] = type(exc).__name__
        report["error"] = str(exc)
    finally:
        report["snapshot"] = client.snapshot()
        await client.close()
    if getattr(args, "json", False):
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
        return
    print(json.dumps(report, indent=2, sort_keys=True, default=str))


def run_sodex_preview(args: argparse.Namespace) -> None:
    print(json.dumps(_sodex_preview_payload(args), indent=2, sort_keys=True))


def _sodex_preview_payload(args: argparse.Namespace) -> dict[str, Any]:
    if args.kind == "new-order":
        order = perps_order_item(
            cl_ord_id=str(args.cl_ord_id),
            modifier=parse_sodex_enum(args.modifier, SODEX_MODIFIER_ALIASES, "modifier"),
            side=parse_sodex_enum(args.side, SODEX_SIDE_ALIASES, "side"),
            order_type=parse_sodex_enum(args.order_type, SODEX_ORDER_TYPE_ALIASES, "order_type"),
            time_in_force=parse_sodex_enum(args.time_in_force, SODEX_TIME_IN_FORCE_ALIASES, "time_in_force"),
            price=args.price,
            quantity=args.quantity,
            funds=args.funds,
            reduce_only=bool(args.reduce_only),
            position_side=parse_sodex_enum(args.position_side, SODEX_POSITION_SIDE_ALIASES, "position_side"),
        )
        body = perps_new_order_body(account_id=int(args.account_id), symbol_id=int(args.symbol_id), orders=[order])
        from siglab.live.sodex_signing import SoDEXSignedRequest
        request = SoDEXSignedRequest(method="POST", path="/trade/orders", body=body, weight=1)
    elif args.kind == "cancel-order":
        cancel = perps_cancel_item(
            symbol_id=int(args.symbol_id),
            order_id=args.order_id,
            cl_ord_id=args.orig_cl_ord_id,
        )
        body = perps_cancel_order_body(account_id=int(args.account_id), cancels=[cancel])
        from siglab.live.sodex_signing import SoDEXSignedRequest
        request = SoDEXSignedRequest(method="DELETE", path="/trade/orders", body=body, weight=1)
    elif args.kind == "schedule-cancel":
        body = perps_schedule_cancel_body(
            account_id=int(args.account_id),
            scheduled_timestamp=args.scheduled_timestamp,
        )
        from siglab.live.sodex_signing import SoDEXSignedRequest
        request = SoDEXSignedRequest(method="POST", path="/trade/orders/schedule-cancel", body=body, weight=1)
    elif args.kind == "update-margin":
        if args.amount is None:
            raise SystemExit("--amount is required for --kind update-margin")
        body = perps_update_margin_body(
            account_id=int(args.account_id),
            symbol_id=int(args.symbol_id),
            amount=str(args.amount),
        )
        from siglab.live.sodex_signing import SoDEXSignedRequest
        request = SoDEXSignedRequest(method="POST", path="/trade/margin", body=body, weight=1)
    else:
        body = perps_update_leverage_body(
            account_id=int(args.account_id),
            symbol_id=int(args.symbol_id),
            leverage=int(args.leverage),
            margin_mode=parse_sodex_enum(args.margin_mode, SODEX_MARGIN_MODE_ALIASES, "margin_mode"),
        )
        from siglab.live.sodex_signing import SoDEXSignedRequest
        request = SoDEXSignedRequest(method="POST", path="/trade/leverage", body=body, weight=1)
    signature_input = build_signature_input(
        domain=request.domain,
        account_id=int(args.account_id),
        body=request.body,
        nonce=int(args.nonce),
    )
    return {
        "method": request.method,
        "path": request.path,
        "domain": request.domain,
        "weight": request.weight,
        "canonical_body": canonical_json(http_body_from_action_payload(request.body)),
        "canonical_signing_payload": canonical_json(request.body),
        "signature_input": signature_input,
        "signature": None,
        "submitted": False,
    }
