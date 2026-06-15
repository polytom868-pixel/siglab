from __future__ import annotations

import unittest
from collections import OrderedDict
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx

from siglab.live.sodex_client import SoDEXRateLimitError, SoDEXSignedPerpsClient, SoDEXTransportError, SoDEXUpstreamError
from siglab.live.sodex_signing import (
    SoDEXNonceManager,
    SoDEXNotReadyError,
    SoDEXSignedRequest,
    perps_cancel_item,
    perps_new_order_body,
    perps_order_item,
    perps_update_leverage_body,
)


class _Signer:
    signer_type = "test"

    def sign_typed_payload(self, *, domain: str, account_id: int, payload_hash: str, nonce: int) -> str:
        return "0x01" + "ab" * 65


class _Response:
    def __init__(self, payload: object, *, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> object:
        return self._payload


class SoDEXSignedClientTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _make_client(*, dry_run: bool = True, client: object | None = None) -> SoDEXSignedPerpsClient:
        return SoDEXSignedPerpsClient(
            api_key_name="siglab-key",
            account_id=1001,
            signer=_Signer(),
            nonce_manager=SoDEXNonceManager(now_ms=lambda: 1760373925000),
            environment="testnet",
            dry_run=dry_run,
            client=client,
        )

    @staticmethod
    def _leverage_request() -> SoDEXSignedRequest:
        return SoDEXSignedRequest(
            method="POST",
            path="/trade/leverage",
            body=perps_update_leverage_body(account_id=1001, symbol_id=1, leverage=5, margin_mode=1),
        )

    async def test_prepare_signed_request_builds_headers_and_signature_input(self) -> None:
        client = self._make_client()
        request = SoDEXSignedRequest(
            method="POST",
            path="/trade/orders",
            body=perps_new_order_body(
                account_id=1001,
                symbol_id=1,
                orders=[
                    OrderedDict(
                        [
                            ("clOrdID", "abc"),
                            ("modifier", 1),
                            ("side", 1),
                            ("type", 2),
                            ("timeInForce", 3),
                            ("quantity", "0.01"),
                            ("reduceOnly", False),
                            ("positionSide", 1),
                        ]
                    )
                ],
            ),
        )

        prepared = client.prepare_signed_request(request)

        self.assertEqual(prepared["method"], "POST")
        self.assertIn("testnet-gw.sodex.dev", prepared["url"])
        self.assertEqual(prepared["headers"]["X-API-Key"], "siglab-key")
        self.assertEqual(prepared["headers"]["X-API-Sign"], "0x01" + "ab" * 65)
        self.assertEqual(prepared["signature_input"]["domain"], "futures")
        self.assertIn('"quantity":"0.01"', prepared["body"])
        self.assertNotIn('"type":"newOrder"', prepared["body"])
        self.assertIn('"type":"newOrder"', prepared["signing_payload"])

    async def test_send_signed_request_refuses_in_dry_run(self) -> None:
        client = self._make_client()

        with self.assertRaises(SoDEXNotReadyError):
            await client.send_signed_request(
                SoDEXSignedRequest(
                    method="POST",
                    path="/trade/orders",
                    body=perps_update_leverage_body(account_id=1001, symbol_id=1, leverage=5, margin_mode=1),
                )
            )

    async def test_missing_prerequisites_fail_before_signature(self) -> None:
        client = SoDEXSignedPerpsClient(
            api_key_name=None,
            account_id=None,
            signer=None,
            nonce_manager=None,
            dry_run=True,
        )

        with self.assertRaises(SoDEXNotReadyError):
            client.prepare_signed_request(
                SoDEXSignedRequest(method="POST", path="/trade/orders", body=OrderedDict([("accountID", 1001)]))
            )

    async def test_client_builds_new_order_request_with_documented_path_and_weight(self) -> None:
        client = self._make_client()
        orders = [
            perps_order_item(
                cl_ord_id=f"siglab-{idx}",
                modifier=1,
                side=1,
                order_type=1,
                time_in_force=2,
                quantity="0.01",
            )
            for idx in range(40)
        ]

        request = client.new_order_request(symbol_id=1, orders=orders)
        prepared = client.prepare_signed_request(request)

        self.assertEqual(request.path, "/trade/orders")
        self.assertEqual(request.weight, 2)
        self.assertNotIn('"type":"newOrder"', prepared["body"])
        self.assertIn('"type":"newOrder"', prepared["signing_payload"])
        self.assertIn('"orders":[', prepared["body"])

    async def test_client_builds_update_leverage_request_with_documented_path_and_weight(self) -> None:
        client = self._make_client()

        request = client.update_leverage_request(symbol_id=1, leverage=5, margin_mode=1)
        prepared = client.prepare_signed_request(request)

        self.assertEqual(request.path, "/trade/leverage")
        self.assertEqual(request.weight, 1)
        self.assertNotIn('"type":"updateLeverage"', prepared["body"])
        self.assertIn('"type":"updateLeverage"', prepared["signing_payload"])

    async def test_client_builds_cancel_and_schedule_cancel_requests(self) -> None:
        client = self._make_client()

        cancel_request = client.cancel_order_request(cancels=[perps_cancel_item(symbol_id=1, cl_ord_id="siglab-1")])
        schedule_request = client.schedule_cancel_request(scheduled_timestamp=1760373930000)
        cancel_prepared = client.prepare_signed_request(cancel_request)
        schedule_prepared = client.prepare_signed_request(schedule_request)

        self.assertEqual(cancel_request.method, "DELETE")
        self.assertEqual(cancel_request.path, "/trade/orders")
        self.assertEqual(cancel_request.weight, 1)
        self.assertNotIn('"type":"cancelOrder"', cancel_prepared["body"])
        self.assertIn('"type":"cancelOrder"', cancel_prepared["signing_payload"])
        self.assertEqual(schedule_request.method, "POST")
        self.assertEqual(schedule_request.path, "/trade/orders/schedule-cancel")
        self.assertEqual(schedule_request.weight, 1)
        self.assertNotIn('"type":"scheduleCancel"', schedule_prepared["body"])
        self.assertIn('"type":"scheduleCancel"', schedule_prepared["signing_payload"])

    async def test_client_builds_update_margin_request(self) -> None:
        client = self._make_client()

        request = client.update_margin_request(symbol_id=1, amount="-0.25")
        prepared = client.prepare_signed_request(request)

        self.assertEqual(request.path, "/trade/margin")
        self.assertEqual(request.weight, 1)
        self.assertNotIn('"type":"updateMargin"', prepared["body"])
        self.assertIn('"type":"updateMargin"', prepared["signing_payload"])
        self.assertIn('"amount":"-0.25"', prepared["body"])

    async def test_signed_write_rejects_business_error_envelope(self) -> None:
        http = SimpleNamespace(request=AsyncMock(return_value=_Response({"code": 1001, "timestamp": 1, "error": "bad"})))
        client = self._make_client(dry_run=False, client=http)

        with self.assertRaises(SoDEXUpstreamError):
            await client.send_signed_request(self._leverage_request())

    async def test_signed_write_classifies_rate_limit(self) -> None:
        http = SimpleNamespace(request=AsyncMock(return_value=_Response({"code": 1, "timestamp": 1}, status_code=429)))
        client = self._make_client(dry_run=False, client=http)

        with self.assertRaises(SoDEXRateLimitError):
            await client.send_signed_request(self._leverage_request())
        self.assertEqual(client.metrics_snapshot()["endpoints"]["signed.write"]["429_count"], 1)

    async def test_signed_write_records_success_metrics(self) -> None:
        http = SimpleNamespace(request=AsyncMock(return_value=_Response({"code": 0, "timestamp": 1, "data": {}})))
        client = self._make_client(dry_run=False, client=http)

        payload = await client.send_signed_request(self._leverage_request())

        snapshot = client.metrics_snapshot()["endpoints"]["signed.write"]
        self.assertEqual(payload["code"], 0)
        self.assertEqual(snapshot["attempts"], 1)
        self.assertEqual(snapshot["success_rate"], 1.0)

    async def test_signed_write_classifies_transport_error(self) -> None:
        http = SimpleNamespace(request=AsyncMock(side_effect=httpx.ConnectError("boom")))
        client = self._make_client(dry_run=False, client=http)

        with self.assertRaises(SoDEXTransportError):
            await client.send_signed_request(self._leverage_request())
        self.assertEqual(client.metrics_snapshot()["endpoints"]["signed.write"]["transport_failures"], 1)


if __name__ == "__main__":
    unittest.main()
