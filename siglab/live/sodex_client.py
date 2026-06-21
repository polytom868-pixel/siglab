"""SoDEX clients."""
from __future__ import annotations

import logging
import time
import uuid
from collections import OrderedDict
from typing import Any

import httpx

from siglab.data.sodex_client import (
    SoDEXError,
    SoDEXFormatError,
    SoDEXPublicPerpsClient,
    SoDEXRateLimitError,
    SoDEXTransportError,
    SoDEXUpstreamError,
    _batch_order_weight,
)
from siglab.live.sodex_signing import (
    SoDEXNonceManager,
    SoDEXNotReadyError,
    SoDEXSignedRequest,
    SoDEXSigner,
    build_signature_input,
    build_signed_headers,
    canonical_json,
    http_body_from_action_payload,
    perps_cancel_order_body,
    perps_new_order_body,
    perps_order_item,
    perps_schedule_cancel_body,
    perps_update_leverage_body,
    perps_update_margin_body,
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
            base_url=(
                kwargs.pop("base_url", "https://mainnet-gw.sodex.dev/api/v1/perps")
                if environment == "mainnet"
                else kwargs.pop("base_url", "https://testnet-gw.sodex.dev/api/v1/perps")
            ),
            **kwargs,
        )
        self.api_key_name = api_key_name
        self.account_id = account_id
        self.signer = signer
        self.nonce_manager = nonce_manager
        self.environment = environment
        self.dry_run = bool(dry_run)

    def new_order_request(self, *, symbol_id: int, orders: list[OrderedDict[str, Any]]) -> SoDEXSignedRequest:
        if self.account_id is None:
            raise SoDEXNotReadyError("SoDEX account_id is required to build new-order request")
        body = perps_new_order_body(account_id=self.account_id, symbol_id=symbol_id, orders=orders)
        return SoDEXSignedRequest(
            method="POST",
            path="/trade/orders",
            body=body,
            domain="futures",
            weight=_batch_order_weight(len(orders)),
        )

    def update_leverage_request(self, *, symbol_id: int, leverage: int, margin_mode: int) -> SoDEXSignedRequest:
        if self.account_id is None:
            raise SoDEXNotReadyError("SoDEX account_id is required to build update-leverage request")
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

    def cancel_order_request(self, *, cancels: list[OrderedDict[str, Any]]) -> SoDEXSignedRequest:
        if self.account_id is None:
            raise SoDEXNotReadyError("SoDEX account_id is required to build cancel-order request")
        return SoDEXSignedRequest(
            method="DELETE",
            path="/trade/orders",
            body=perps_cancel_order_body(account_id=self.account_id, cancels=cancels),
            domain="futures",
            weight=_batch_order_weight(len(cancels)),
        )

    def schedule_cancel_request(self, *, scheduled_timestamp: int | None = None) -> SoDEXSignedRequest:
        if self.account_id is None:
            raise SoDEXNotReadyError("SoDEX account_id is required to build schedule-cancel request")
        return SoDEXSignedRequest(
            method="POST",
            path="/trade/orders/schedule-cancel",
            body=perps_schedule_cancel_body(account_id=self.account_id, scheduled_timestamp=scheduled_timestamp),
            domain="futures",
            weight=1,
        )

    def update_margin_request(self, *, symbol_id: int, amount: str) -> SoDEXSignedRequest:
        if self.account_id is None:
            raise SoDEXNotReadyError("SoDEX account_id is required to build update-margin request")
        return SoDEXSignedRequest(
            method="POST",
            path="/trade/margin",
            body=perps_update_margin_body(account_id=self.account_id, symbol_id=symbol_id, amount=amount),
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
            raise SoDEXNotReadyError("SoDEX nonce manager is required for signed writes")
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
                "SoDEX signed client is in dry-run mode; skipping live write to %s %s "
                "(set dry_run=False to enable real writes)",
                prepared["method"],
                prepared["url"],
            )
            return {"dry_run": True, "method": prepared["method"], "url": prepared["url"]}
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
            modifier=1,          # NORMAL
            side=1 if is_buy else 2,  # BUY=1, SELL=2
            order_type=2,        # MARKET
            time_in_force=1,     # GTC
            quantity=str(size),
            reduce_only=reduce_only,
        )
        request = self.new_order_request(symbol_id=symbol_id, orders=[order])
        return await self.send_signed_request(request)



