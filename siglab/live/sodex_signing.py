from __future__ import annotations

import json
import os
import tempfile
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol, cast

from eth_utils import keccak  # type: ignore[attr-defined]
from eth_account import Account
from eth_account.messages import encode_typed_data
__all__ = ["keccak"]



SUPPORTED_SODEX_SIGNED_ACTIONS = frozenset(
    {
        "newOrder",
        "cancelOrder",
        "scheduleCancel",
        "updateLeverage",
        "updateMargin",
    }
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


class SoDEXSigner(Protocol):
    signer_type: str

    def sign_typed_payload(self, *, domain: str, account_id: int, payload_hash: str, nonce: int) -> str:
        ...


@dataclass(frozen=True)
class SoDEXSignedRequest:
    method: str
    path: str
    body: OrderedDict[str, Any]
    domain: str = "futures"
    weight: int = 20


class SoDEXDryRunSigner:
    signer_type = "dry-run"

    def sign_typed_payload(self, *, domain: str, account_id: int, payload_hash: str, nonce: int) -> str:
        raise SoDEXNotReadyError("Dry-run signer cannot produce live SoDEX signatures")


class SoDEXPrivateKeySigner:
    signer_type = "evm-private-key"

    def __init__(self, *, private_key: str | None, environment: str = "mainnet") -> None:
        if not private_key:
            raise SoDEXConfigError("SoDEX private key is required for live signing")
        self.private_key = private_key
        self.environment = environment

    def sign_typed_payload(self, *, domain: str, account_id: int, payload_hash: str, nonce: int) -> str:
        typed_data = build_exchange_action_typed_data(
            domain=domain,
            environment=self.environment,
            payload_hash_value=payload_hash,
            nonce=nonce,
        )
        signable = encode_typed_data(full_message=typed_data)
        signed = Account.sign_message(signable, private_key=self.private_key)
        return prefixed_eip712_signature("0x" + signed.signature.hex())


class SoDEXNonceManager:
    def __init__(
        self,
        *,
        store_path: Path | None = None,
        now_ms: Callable[[], int] | None = None,
        window_past_ms: int = 2 * 24 * 60 * 60 * 1000,
        window_future_ms: int = 24 * 60 * 60 * 1000,
        high_water_size: int = 64,
    ) -> None:
        self.store_path = store_path
        self.now_ms = now_ms or (lambda: int(time.time() * 1000))
        self.window_past_ms = int(window_past_ms)
        self.window_future_ms = int(window_future_ms)
        self.high_water_size = int(high_water_size)
        self._seen: dict[str, deque[int]] = {}
        if store_path is not None and store_path.exists():
            raw = json.loads(store_path.read_text())
            for key, values in dict(raw or {}).items():
                self._seen[str(key)] = deque([int(v) for v in values], maxlen=self.high_water_size)

    def next_nonce(self, api_key_name: str) -> int:
        now = int(self.now_ms())
        seen = self._seen_for(api_key_name)
        nonce = max(now, (max(seen) + 1) if seen else now)
        self.validate(api_key_name, nonce)
        seen.append(nonce)
        self._persist()
        return nonce

    def validate(self, api_key_name: str, nonce: int) -> None:
        if not api_key_name:
            raise SoDEXNonceError("api_key_name is required for nonce validation")
        value = int(nonce)
        now = int(self.now_ms())
        if value <= now - self.window_past_ms or value >= now + self.window_future_ms:
            raise SoDEXNonceError("nonce is outside SoDEX time window")
        seen = self._seen_for(api_key_name)
        if value in seen:
            raise SoDEXNonceError("nonce was already used for this API key")
        if seen and value <= min(seen):
            raise SoDEXNonceError("nonce is not larger than the stored high-water minimum")

    def _seen_for(self, api_key_name: str) -> deque[int]:
        key = str(api_key_name)
        if key not in self._seen:
            self._seen[key] = deque(maxlen=self.high_water_size)
        return self._seen[key]

    def _persist(self) -> None:
        if self.store_path is None:
            return
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {key: list(values) for key, values in self._seen.items()}
        data = json.dumps(payload, indent=2, ensure_ascii=True)
        # Atomic write: stage in a temp file in the same directory, then os.replace
        # so a crash mid-write cannot corrupt the nonce store.
        fd, tmp_path = tempfile.mkstemp(
            prefix=".nonce-", suffix=".tmp", dir=str(self.store_path.parent)
        )
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(data)
            os.replace(tmp_path, self.store_path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise


def validate_account_id(account_id: Any) -> int:
    try:
        value = int(account_id)
    except (TypeError, ValueError) as exc:
        raise SoDEXConfigError("accountID must be an unsigned integer") from exc
    if value < 0:
        raise SoDEXConfigError("accountID must be an unsigned integer")
    return value


def canonical_json(payload: OrderedDict[str, Any]) -> str:
    if not isinstance(payload, OrderedDict):
        raise SoDEXConfigError("SoDEX signing payload must be an OrderedDict to preserve Go struct field order")
    return json.dumps(_canonical_value(payload), separators=(",", ":"), ensure_ascii=True)


def payload_hash(payload: OrderedDict[str, Any]) -> str:
    return "0x" + keccak(text=canonical_json(payload)).hex()


def build_signed_headers(*, api_key_name: str, signature: str, nonce: int) -> dict[str, str]:
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


def build_signature_input(*, domain: str, account_id: int, body: OrderedDict[str, Any], nonce: int) -> dict[str, Any]:
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


def http_body_from_action_payload(payload: OrderedDict[str, Any]) -> OrderedDict[str, Any]:
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
        "message": {
            "payloadHash": str(payload_hash_value),
            "nonce": int(nonce),
        },
    }


def prefixed_eip712_signature(signature_hex: str) -> str:
    raw = str(signature_hex)
    if not raw.startswith("0x"):
        raise SoDEXConfigError("signature must be 0x-prefixed hex")
    if raw.startswith("0x01"):
        return raw
    return "0x01" + raw[2:]


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
        ]
    )


def _perps_action_body(action: str, params: list[tuple[str, Any]]) -> OrderedDict[str, Any]:
    """Build a SoDEX perps signing body with ordered type + params fields."""
    return OrderedDict([("type", action), ("params", OrderedDict(params))])


def perps_new_order_body(*, account_id: int, symbol_id: int, orders: list[OrderedDict[str, Any]]) -> OrderedDict[str, Any]:
    if not orders:
        raise SoDEXConfigError("Perps order batch cannot be empty")
    if len(orders) > 100:
        raise SoDEXConfigError("Perps order batch cannot exceed 100 orders")
    if any(not isinstance(order, OrderedDict) for order in orders):
        raise SoDEXConfigError("Perps orders must be OrderedDict instances to preserve signing field order")
    return _perps_action_body("newOrder", [
        ("accountID", validate_account_id(account_id)),
        ("symbolID", int(symbol_id)),
        ("orders", orders),
    ])


def perps_update_leverage_body(
    *,
    account_id: int,
    symbol_id: int,
    leverage: int,
    margin_mode: int,
) -> OrderedDict[str, Any]:
    return _perps_action_body("updateLeverage", [
        ("accountID", validate_account_id(account_id)),
        ("symbolID", int(symbol_id)),
        ("leverage", int(leverage)),
        ("marginMode", int(margin_mode)),
    ])


def perps_cancel_item(*, symbol_id: int, order_id: int | None = None, cl_ord_id: str | None = None) -> OrderedDict[str, Any]:
    if (order_id is None and not cl_ord_id) or (order_id is not None and cl_ord_id):
        raise SoDEXConfigError("Perps cancel item must provide exactly one of orderID or clOrdID")
    return OrderedDict(
        [
            ("symbolID", int(symbol_id)),
            ("orderID", int(order_id) if order_id is not None else None),
            ("clOrdID", cl_ord_id),
        ]
    )


def perps_cancel_order_body(*, account_id: int, cancels: list[OrderedDict[str, Any]]) -> OrderedDict[str, Any]:
    if not cancels:
        raise SoDEXConfigError("Perps cancel batch cannot be empty")
    if len(cancels) > 100:
        raise SoDEXConfigError("Perps cancel batch cannot exceed 100 cancels")
    if any(not isinstance(cancel, OrderedDict) for cancel in cancels):
        raise SoDEXConfigError("Perps cancels must be OrderedDict instances to preserve signing field order")
    return _perps_action_body("cancelOrder", [
        ("accountID", validate_account_id(account_id)),
        ("cancels", cancels),
    ])


def perps_schedule_cancel_body(*, account_id: int, scheduled_timestamp: int | None = None) -> OrderedDict[str, Any]:
    return _perps_action_body("scheduleCancel", [
        ("accountID", validate_account_id(account_id)),
        ("scheduledTimestamp", int(scheduled_timestamp) if scheduled_timestamp is not None else None),
    ])


def perps_update_margin_body(*, account_id: int, symbol_id: int, amount: str) -> OrderedDict[str, Any]:
    if not isinstance(amount, str) or not amount.strip():
        raise SoDEXConfigError("UpdateMargin amount must be a non-empty DecimalString")
    return _perps_action_body("updateMargin", [
        ("accountID", validate_account_id(account_id)),
        ("symbolID", int(symbol_id)),
        ("amount", amount),
    ])


def _canonical_value(value: Any) -> Any:
    if isinstance(value, OrderedDict):
        return OrderedDict((key, _canonical_value(item)) for key, item in value.items() if item is not None)
    if isinstance(value, dict):
        raise SoDEXConfigError("Nested SoDEX signing payload objects must be OrderedDict")
    if isinstance(value, list):
        return [_canonical_value(item) for item in value]
    if isinstance(value, float):
        raise SoDEXConfigError("DecimalString fields must remain quoted strings; floats are forbidden in signing payloads")
    return value
