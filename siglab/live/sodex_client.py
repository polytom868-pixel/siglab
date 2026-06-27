"""SoDEX clients — merged from live/sodex_client.py and live/sodex_signing.py."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
import uuid
from collections import OrderedDict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

import httpx
from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_utils.crypto import keccak
import contextlib

from siglab.data.sodex_client import (
    SoDEXError,
    SoDEXFormatError,
    SoDEXPublicPerpsClient,
    SoDEXRateLimitError,
    SoDEXTransportError,
    SoDEXUpstreamError,
    _batch_order_weight,
)

__all__ = [
    "SoDEXError",
    "SoDEXFormatError",
    "SoDEXPublicPerpsClient",
    "SoDEXRateLimitError",
    "SoDEXSignedPerpsClient",
    "SoDEXTransportError",
    "SoDEXUpstreamError",
]
logger = logging.getLogger(__name__)

SUPPORTED_SODEX_SIGNED_ACTIONS = frozenset(
    {"newOrder", "cancelOrder", "scheduleCancel", "updateLeverage", "updateMargin"},
)
UNSUPPORTED_SODEX_SIGNED_ACTIONS = {
    "replaceOrder": "blocked until official SDK/source pins the perps wrapper type and struct order",
    "modifyOrder": "blocked until official SDK/source pins the perps wrapper type and struct order",
    "transferAsset": "blocked until full transfer schema and live operator policy are pinned",
}


class SoDEXSigningError(RuntimeError):
    pass


class SoDEXConfigError(SoDEXSigningError):
    pass


class SoDEXNonceError(SoDEXSigningError):
    pass


class SoDEXNotReadyError(SoDEXSigningError):
    pass


class SoDEXDryRunSigner:
    signer_type = "dry-run"

    def sign_typed_payload(
        self,
        *,
        domain: str,
        account_id: int,
        payload_hash: str,
        nonce: int,
    ) -> str:
        raise SoDEXNotReadyError(
            "SoDEX dry-run signer refuses to sign — configure a real signer with SODEX_PRIVATE_KEY or SODEX_AWS_KMS_KEY_ARN environment variables.",
        )


class SoDEXSigner(Protocol):
    signer_type: str

    def sign_typed_payload(
        self,
        *,
        domain: str,
        account_id: int,
        payload_hash: str,
        nonce: int,
    ) -> str: ...


@dataclass(frozen=True)
class SoDEXSignedRequest:
    method: str
    path: str
    body: OrderedDict[str, Any]
    domain: str = "futures"
    weight: int = 20


def _validate_domain(domain: str, environment: str) -> None:
    if domain not in {"spot", "futures"}:
        raise SoDEXConfigError("SoDEX signing domain must be 'spot' or 'futures'")
    if environment not in {"mainnet", "testnet"}:
        raise SoDEXConfigError("SoDEX environment must be 'mainnet' or 'testnet'")


class SoDEXPrivateKeySigner:
    signer_type = "evm-private-key"

    def __init__(
        self,
        *,
        private_key: str | None,
        environment: str = "mainnet",
    ) -> None:
        if not private_key:
            raise SoDEXConfigError("SoDEX private key is required for live signing")
        self.private_key = private_key
        self.environment = environment

    def sign_typed_payload(
        self,
        *,
        domain: str,
        account_id: int,
        payload_hash: str,
        nonce: int,
    ) -> str:
        _validate_domain(domain=domain, environment=self.environment)
        typed_data = build_exchange_action_typed_data(
            domain=domain,
            environment=self.environment,
            payload_hash_value=payload_hash,
            nonce=nonce,
        )
        return prefixed_eip712_signature(
            "0x"
            + Account.sign_message(
                encode_typed_data(full_message=typed_data),
                private_key=self.private_key,
            ).signature.hex(),
        )


class SoDEXNonceManager:
    def __init__(
        self,
        *,
        store_path: Path | None = None,
        environment: str = "testnet",
        high_water_size: int = 64,
    ) -> None:
        self.store_path = store_path
        self.environment = str(environment).strip().lower()
        self.high_water_size = int(high_water_size)
        self._seen: dict[str, deque[int]] = {}
        if store_path is not None and store_path.exists():
            for key, values in dict(json.loads(store_path.read_text()) or {}).items():
                self._seen[str(key)] = deque(
                    [int(v) for v in values],
                    maxlen=self.high_water_size,
                )

    def next_nonce(self, api_key_name: str) -> int:
        now = int(time.time() * 1000)
        seen = self._sf(api_key_name)
        nonce = max(now, max(seen) + 1 if seen else now)
        self.validate(api_key_name, nonce)
        seen.append(nonce)
        self._persist()
        return nonce

    def validate(self, api_key_name: str, nonce: int) -> None:
        if not api_key_name:
            raise SoDEXNonceError("api_key_name is required for nonce validation")
        value = int(nonce)
        if self.environment == "mainnet":
            now = int(time.time() * 1000)
            if value <= now - 172800000 or value >= now + 86400000:
                raise SoDEXNonceError("nonce is outside SoDEX time window (mainnet)")
        seen = self._sf(api_key_name)
        if value in seen:
            raise SoDEXNonceError("nonce was already used for this API key")
        if seen and value <= min(seen):
            raise SoDEXNonceError(
                "nonce is not larger than the stored high-water minimum",
            )

    def _sf(self, api_key_name: str) -> deque[int]:
        key = str(api_key_name)
        if key not in self._seen:
            self._seen[key] = deque(maxlen=self.high_water_size)
        return self._seen[key]

    def _persist(self) -> None:
        if self.store_path is None:
            return
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(
            {key: list(values) for key, values in self._seen.items()},
            indent=2,
            ensure_ascii=True,
        )
        fd, tmp_path = tempfile.mkstemp(
            prefix=".nonce-",
            suffix=".tmp",
            dir=str(self.store_path.parent),
        )
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(data)
            os.replace(tmp_path, self.store_path)
        except OSError:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
            raise


def validate_account_id(account_id: str | int) -> int:
    try:
        value = int(account_id)
    except (TypeError, ValueError) as exc:
        raise SoDEXConfigError("accountID must be an unsigned integer") from exc
    if value < 0:
        raise SoDEXConfigError("accountID must be an unsigned integer")
    return value


def canonical_json(payload: OrderedDict[str, Any]) -> str:
    if not isinstance(payload, OrderedDict):
        raise SoDEXConfigError(
            "SoDEX signing payload must be an OrderedDict to preserve Go struct field order",
        )
    return json.dumps(_cv(payload), separators=(",", ":"), ensure_ascii=True)


def payload_hash(payload: OrderedDict[str, Any]) -> str:
    return "0x" + keccak(text=canonical_json(payload)).hex()


def build_signed_headers(
    *,
    api_key_name: str,
    signature: str,
    nonce: int,
) -> dict[str, str]:
    if not api_key_name:
        raise SoDEXConfigError("X-API-Key name is required")
    if not signature:
        raise SoDEXConfigError("X-API-Sign is required")
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-API-Key": str(api_key_name),
        "X-API-Sign": str(signature),
        "X-API-Nonce": str(int(nonce)),
    }


def build_signature_input(
    *,
    domain: str,
    account_id: int,
    body: OrderedDict[str, Any],
    nonce: int,
) -> dict[str, Any]:
    validate_action_payload(body)
    return {
        "domain": str(domain),
        "accountID": validate_account_id(account_id),
        "payloadHash": payload_hash(body),
        "nonce": int(nonce),
    }


def validate_action_payload(payload: OrderedDict[str, Any]) -> None:
    if not isinstance(payload, OrderedDict):
        raise SoDEXConfigError("SoDEX action payload must be an OrderedDict")
    if list(payload.keys()) != ["type", "params"]:
        raise SoDEXConfigError("SoDEX signing payload must be ordered as type, params")
    if not isinstance(payload.get("type"), str) or not payload.get("type"):
        raise SoDEXConfigError("SoDEX signing payload type is required")
    if not isinstance(payload.get("params"), OrderedDict):
        raise SoDEXConfigError("SoDEX signing payload params must be an OrderedDict")


def http_body_from_action_payload(
    payload: OrderedDict[str, Any],
) -> OrderedDict[str, Any]:
    validate_action_payload(payload)
    return cast("OrderedDict[str, Any]", payload["params"])


def build_eip712_domain(*, domain: str, environment: str = "mainnet") -> dict[str, Any]:
    if domain not in {"spot", "futures"}:
        raise SoDEXConfigError("SoDEX signing domain must be 'spot' or 'futures'")
    env = str(environment or "mainnet").strip().lower()
    chain_id = {"mainnet": 286623, "testnet": 138565}.get(env)
    if chain_id is None:
        raise SoDEXConfigError("SoDEX environment must be 'mainnet' or 'testnet'")
    return {
        "name": domain,
        "version": "1",
        "chainId": chain_id,
        "verifyingContract": "0x0000000000000000000000000000000000000000",
    }


def build_exchange_action_typed_data(
    *,
    domain: str,
    environment: str,
    payload_hash_value: str,
    nonce: int,
) -> dict[str, Any]:
    if not str(payload_hash_value).startswith("0x"):
        raise SoDEXConfigError("payloadHash must be a 0x-prefixed bytes32 hex string")
    return {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "ExchangeAction": [
                {"name": "payloadHash", "type": "bytes32"},
                {"name": "nonce", "type": "uint64"},
            ],
        },
        "domain": build_eip712_domain(domain=domain, environment=environment),
        "primaryType": "ExchangeAction",
        "message": {"payloadHash": str(payload_hash_value), "nonce": int(nonce)},
    }


def prefixed_eip712_signature(signature_hex: str) -> str:
    raw = str(signature_hex)
    if not raw.startswith("0x"):
        raise SoDEXConfigError("signature must be 0x-prefixed hex")
    return raw if raw.startswith("0x01") else "0x01" + raw[2:]


def perps_order_item(
    *,
    cl_ord_id: str,
    modifier: int,
    side: int,
    order_type: int,
    time_in_force: int,
    price: str | None = None,
    quantity: str | None = None,
    funds: str | None = None,
    stop_price: str | None = None,
    stop_type: int | None = None,
    trigger_type: int | None = None,
    reduce_only: bool = False,
    position_side: int = 1,
) -> OrderedDict[str, Any]:
    return OrderedDict(
        [
            ("clOrdID", cl_ord_id),
            ("modifier", int(modifier)),
            ("side", int(side)),
            ("type", int(order_type)),
            ("timeInForce", int(time_in_force)),
            ("price", price),
            ("quantity", quantity),
            ("funds", funds),
            ("stopPrice", stop_price),
            ("stopType", stop_type),
            ("triggerType", trigger_type),
            ("reduceOnly", bool(reduce_only)),
            ("positionSide", int(position_side)),
        ],
    )


def _pab(action: str, params: list[tuple[str, Any]]) -> OrderedDict[str, Any]:
    return OrderedDict([("type", action), ("params", OrderedDict(params))])


def perps_new_order_body(
    *,
    account_id: int,
    symbol_id: int,
    orders: list[OrderedDict[str, Any]],
) -> OrderedDict[str, Any]:
    if not orders:
        raise SoDEXConfigError("Perps order batch cannot be empty")
    if len(orders) > 100:
        raise SoDEXConfigError("Perps order batch cannot exceed 100 orders")
    if any(not isinstance(order, OrderedDict) for order in orders):
        raise SoDEXConfigError(
            "Perps orders must be OrderedDict instances to preserve signing field order",
        )
    return _pab(
        "newOrder",
        [
            ("accountID", validate_account_id(account_id)),
            ("symbolID", int(symbol_id)),
            ("orders", orders),
        ],
    )


def perps_update_leverage_body(
    *,
    account_id: int,
    symbol_id: int,
    leverage: int,
    margin_mode: int,
) -> OrderedDict[str, Any]:
    return _pab(
        "updateLeverage",
        [
            ("accountID", validate_account_id(account_id)),
            ("symbolID", int(symbol_id)),
            ("leverage", int(leverage)),
            ("marginMode", int(margin_mode)),
        ],
    )


def perps_cancel_item(
    *,
    symbol_id: int,
    order_id: int | None = None,
    cl_ord_id: str | None = None,
) -> OrderedDict[str, Any]:
    if (order_id is None and (not cl_ord_id)) or (order_id is not None and cl_ord_id):
        raise SoDEXConfigError(
            "Perps cancel item must provide exactly one of orderID or clOrdID",
        )
    return OrderedDict(
        [
            ("symbolID", int(symbol_id)),
            ("orderID", int(order_id) if order_id is not None else None),
            ("clOrdID", cl_ord_id),
        ],
    )


def perps_cancel_order_body(
    *,
    account_id: int,
    cancels: list[OrderedDict[str, Any]],
) -> OrderedDict[str, Any]:
    if not cancels:
        raise SoDEXConfigError("Perps cancel batch cannot be empty")
    if len(cancels) > 100:
        raise SoDEXConfigError("Perps cancel batch cannot exceed 100 cancels")
    if any(not isinstance(cancel, OrderedDict) for cancel in cancels):
        raise SoDEXConfigError(
            "Perps cancels must be OrderedDict instances to preserve signing field order",
        )
    return _pab(
        "cancelOrder",
        [("accountID", validate_account_id(account_id)), ("cancels", cancels)],
    )


def perps_schedule_cancel_body(
    *,
    account_id: int,
    scheduled_timestamp: int | None = None,
) -> OrderedDict[str, Any]:
    return _pab(
        "scheduleCancel",
        [
            ("accountID", validate_account_id(account_id)),
            (
                "scheduledTimestamp",
                int(scheduled_timestamp) if scheduled_timestamp is not None else None,
            ),
        ],
    )


def perps_update_margin_body(
    *,
    account_id: int,
    symbol_id: int,
    amount: str,
) -> OrderedDict[str, Any]:
    if not isinstance(amount, str) or not amount.strip():
        raise SoDEXConfigError("UpdateMargin amount must be a non-empty DecimalString")
    return _pab(
        "updateMargin",
        [
            ("accountID", validate_account_id(account_id)),
            ("symbolID", int(symbol_id)),
            ("amount", amount),
        ],
    )


def _cv(value: object) -> object:
    if isinstance(value, OrderedDict):
        return OrderedDict(
            ((key, _cv(item)) for key, item in value.items() if item is not None),
        )
    if isinstance(value, dict):
        raise SoDEXConfigError(
            "Nested SoDEX signing payload objects must be OrderedDict",
        )
    if isinstance(value, list):
        return [_cv(item) for item in value]
    if isinstance(value, float):
        raise SoDEXConfigError(
            "DecimalString fields must remain quoted strings; floats are forbidden in signing payloads",
        )
    return value


class SoDEXSignedPerpsClient(SoDEXPublicPerpsClient):
    def __init__(
        self,
        *,
        api_key_name: str | None,
        account_id: int | None,
        signer: SoDEXSigner | None,
        nonce_manager: SoDEXNonceManager | None,
        environment: str = "mainnet",
        dry_run: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            base_url=kwargs.pop("base_url", "https://mainnet-gw.sodex.dev/api/v1/perps")
            if environment == "mainnet"
            else kwargs.pop("base_url", "https://testnet-gw.sodex.dev/api/v1/perps"),
            **kwargs,
        )
        self.api_key_name = api_key_name
        self.account_id = account_id
        self.signer = signer
        self.nonce_manager = nonce_manager
        self.environment = environment
        self.dry_run = bool(dry_run)

    def new_order_request(
        self,
        *,
        symbol_id: int,
        orders: list[OrderedDict[str, Any]],
    ) -> SoDEXSignedRequest:
        if self.account_id is None:
            raise SoDEXNotReadyError(
                "SoDEX account_id is required to build new-order request",
            )
        body = perps_new_order_body(
            account_id=self.account_id,
            symbol_id=symbol_id,
            orders=orders,
        )
        return SoDEXSignedRequest(
            method="POST",
            path="/trade/orders",
            body=body,
            domain="futures",
            weight=_batch_order_weight(len(orders)),
        )

    def update_leverage_request(
        self,
        *,
        symbol_id: int,
        leverage: int,
        margin_mode: int,
    ) -> SoDEXSignedRequest:
        if self.account_id is None:
            raise SoDEXNotReadyError(
                "SoDEX account_id is required to build update-leverage request",
            )
        return SoDEXSignedRequest(
            method="POST",
            path="/trade/leverage",
            body=perps_update_leverage_body(
                account_id=self.account_id,
                symbol_id=symbol_id,
                leverage=leverage,
                margin_mode=margin_mode,
            ),
            domain="futures",
            weight=1,
        )

    def cancel_order_request(
        self,
        *,
        cancels: list[OrderedDict[str, Any]],
    ) -> SoDEXSignedRequest:
        if self.account_id is None:
            raise SoDEXNotReadyError(
                "SoDEX account_id is required to build cancel-order request",
            )
        return SoDEXSignedRequest(
            method="DELETE",
            path="/trade/orders",
            body=perps_cancel_order_body(account_id=self.account_id, cancels=cancels),
            domain="futures",
            weight=_batch_order_weight(len(cancels)),
        )

    def schedule_cancel_request(
        self,
        *,
        scheduled_timestamp: int | None = None,
    ) -> SoDEXSignedRequest:
        if self.account_id is None:
            raise SoDEXNotReadyError(
                "SoDEX account_id is required to build schedule-cancel request",
            )
        return SoDEXSignedRequest(
            method="POST",
            path="/trade/orders/schedule-cancel",
            body=perps_schedule_cancel_body(
                account_id=self.account_id,
                scheduled_timestamp=scheduled_timestamp,
            ),
            domain="futures",
            weight=1,
        )

    def update_margin_request(
        self,
        *,
        symbol_id: int,
        amount: str,
    ) -> SoDEXSignedRequest:
        if self.account_id is None:
            raise SoDEXNotReadyError(
                "SoDEX account_id is required to build update-margin request",
            )
        return SoDEXSignedRequest(
            method="POST",
            path="/trade/margin",
            body=perps_update_margin_body(
                account_id=self.account_id,
                symbol_id=symbol_id,
                amount=amount,
            ),
            domain="futures",
            weight=1,
        )

    def prepare_signed_request(self, request: SoDEXSignedRequest) -> dict[str, Any]:
        if not self.api_key_name:
            raise SoDEXNotReadyError("SoDEX api_key_name is required for signed writes")
        if self.account_id is None:
            raise SoDEXNotReadyError("SoDEX account_id is required for signed writes")
        if self.signer is None:
            raise SoDEXNotReadyError("SoDEX signer is required for signed writes")
        if self.nonce_manager is None:
            raise SoDEXNotReadyError(
                "SoDEX nonce manager is required for signed writes",
            )
        nonce = self.nonce_manager.next_nonce(self.api_key_name)
        signature_input = build_signature_input(
            domain=request.domain,
            account_id=self.account_id,
            body=request.body,
            nonce=nonce,
        )
        signature = self.signer.sign_typed_payload(
            domain=request.domain,
            account_id=self.account_id,
            payload_hash=signature_input["payloadHash"],
            nonce=nonce,
        )
        http_body = http_body_from_action_payload(request.body)
        return {
            "method": request.method.upper(),
            "url": f"{self.base_url}/{request.path.lstrip('/')}",
            "body": canonical_json(http_body),
            "signing_payload": canonical_json(request.body),
            "headers": build_signed_headers(
                api_key_name=self.api_key_name,
                signature=signature,
                nonce=nonce,
            ),
            "signature_input": signature_input,
            "weight": request.weight,
        }

    async def send_signed_request(self, request: SoDEXSignedRequest) -> dict[str, Any]:
        prepared = self.prepare_signed_request(request)
        if self.dry_run:
            logger.warning(
                "SoDEX signed client is in dry-run mode; skipping live write to %s %s (set dry_run=False to enable real writes)",
                prepared["method"],
                prepared["url"],
            )
            return {
                "dry_run": True,
                "method": prepared["method"],
                "url": prepared["url"],
            }
        metrics = self._metrics_for("signed.write")
        await self.weight_scheduler.acquire(int(prepared["weight"]))
        metrics.attempts += 1
        started = time.perf_counter()
        try:
            response = await self._http().request(
                prepared["method"],
                prepared["url"],
                headers=prepared["headers"],
                content=prepared["body"],
                timeout=self.timeout_s,
            )
        except (httpx.HTTPError, OSError, TimeoutError) as exc:
            metrics.transport_failures += 1
            raise SoDEXTransportError(f"signed.write transport failure: {exc}") from exc
        metrics.latencies_ms.append((time.perf_counter() - started) * 1000.0)
        if int(response.status_code) == 429:
            metrics.rate_limits += 1
        payload = self._checked_payload(response, "signed.write")
        metrics.successes += 1
        return payload

    async def place_market_order(
        self,
        *,
        symbol_id: int,
        is_buy: bool,
        size: str,
        reduce_only: bool = False,
        cl_ord_id: str | None = None,
    ) -> dict[str, Any]:
        """Bridge method: compose a market order and submit via the signed path."""
        order = perps_order_item(
            cl_ord_id=cl_ord_id or f"w9_{uuid.uuid4().hex[:12]}",
            modifier=1,
            side=1 if is_buy else 2,
            order_type=2,
            time_in_force=1,
            quantity=str(size),
            reduce_only=reduce_only,
        )
        request = self.new_order_request(symbol_id=symbol_id, orders=[order])
        return await self.send_signed_request(request)
