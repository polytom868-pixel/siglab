from __future__ import annotations

import tempfile
import unittest
from collections import OrderedDict
from pathlib import Path

from siglab.live.sodex_signing import (
    SoDEXConfigError,
    SoDEXDryRunSigner,
    SoDEXPrivateKeySigner,
    SoDEXNonceError,
    SoDEXNotReadyError,
    SoDEXNonceManager,
    SUPPORTED_SODEX_SIGNED_ACTIONS,
    UNSUPPORTED_SODEX_SIGNED_ACTIONS,
    build_eip712_domain,
    build_exchange_action_typed_data,
    build_signature_input,
    build_signed_headers,
    canonical_json,
    http_body_from_action_payload,
    payload_hash,
    perps_cancel_item,
    perps_cancel_order_body,
    perps_new_order_body,
    perps_order_item,
    perps_schedule_cancel_body,
    perps_update_leverage_body,
    perps_update_margin_body,
    prefixed_eip712_signature,
    validate_account_id,
)


class SoDEXSigningTests(unittest.TestCase):
    def test_supported_and_unsupported_signed_actions_are_explicit(self) -> None:
        self.assertIn("cancelOrder", SUPPORTED_SODEX_SIGNED_ACTIONS)
        self.assertIn("updateMargin", SUPPORTED_SODEX_SIGNED_ACTIONS)
        self.assertIn("replaceOrder", UNSUPPORTED_SODEX_SIGNED_ACTIONS)
        self.assertIn("official SDK/source", UNSUPPORTED_SODEX_SIGNED_ACTIONS["replaceOrder"])

    def test_canonical_json_preserves_order_omits_none_and_keeps_decimal_strings(self) -> None:
        payload = OrderedDict(
            [
                ("accountID", 1001),
                ("symbolID", 1),
                ("price", "100000"),
                ("quantity", "0.01"),
                ("clientOrderID", None),
            ]
        )

        self.assertEqual(
            canonical_json(payload),
            '{"accountID":1001,"symbolID":1,"price":"100000","quantity":"0.01"}',
        )

    def test_payload_hash_changes_when_field_order_changes(self) -> None:
        left = OrderedDict([("accountID", 1001), ("symbolID", 1), ("quantity", "0.01")])
        right = OrderedDict([("symbolID", 1), ("accountID", 1001), ("quantity", "0.01")])

        self.assertNotEqual(payload_hash(left), payload_hash(right))

    def test_float_decimal_is_rejected(self) -> None:
        with self.assertRaises(SoDEXConfigError):
            canonical_json(OrderedDict([("quantity", 0.01)]))

    def test_headers_are_exact_signed_header_set(self) -> None:
        headers = build_signed_headers(api_key_name="siglab-key", signature="0xabc", nonce=123)

        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(headers["Accept"], "application/json")
        self.assertEqual(headers["X-API-Key"], "siglab-key")
        self.assertEqual(headers["X-API-Sign"], "0xabc")
        self.assertEqual(headers["X-API-Nonce"], "123")

    def test_nonce_rejects_duplicates_and_time_window(self) -> None:
        now = 1_760_000_000_000
        manager = SoDEXNonceManager(now_ms=lambda: now)
        nonce = manager.next_nonce("key")

        with self.assertRaises(SoDEXNonceError):
            manager.validate("key", nonce)
        with self.assertRaises(SoDEXNonceError):
            manager.validate("key", now - 3 * 24 * 60 * 60 * 1000)
        with self.assertRaises(SoDEXNonceError):
            manager.validate("key", now + 2 * 24 * 60 * 60 * 1000)

    def test_nonce_persists_high_water(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nonce.json"
            manager = SoDEXNonceManager(store_path=path, now_ms=lambda: 1_760_000_000_000)
            nonce = manager.next_nonce("key")
            manager2 = SoDEXNonceManager(store_path=path, now_ms=lambda: 1_760_000_000_001)

            with self.assertRaises(SoDEXNonceError):
                manager2.validate("key", nonce)

    def test_signature_input_and_domain_are_deterministic(self) -> None:
        body = OrderedDict(
            [
                ("type", "updateMargin"),
                (
                    "params",
                    OrderedDict([("accountID", 1001), ("symbolID", 1), ("amount", "0.01")]),
                ),
            ]
        )
        signature_input = build_signature_input(domain="futures", account_id=1001, body=body, nonce=123)

        self.assertEqual(signature_input["domain"], "futures")
        self.assertEqual(signature_input["accountID"], 1001)
        self.assertEqual(signature_input["nonce"], 123)
        self.assertTrue(signature_input["payloadHash"].startswith("0x"))
        self.assertEqual(
            build_eip712_domain(domain="futures", environment="mainnet"),
            {
                "name": "futures",
                "version": "1",
                "chainId": 286623,
                "verifyingContract": "0x0000000000000000000000000000000000000000",
            },
        )
        typed = build_exchange_action_typed_data(
            domain="futures",
            environment="testnet",
            payload_hash_value=signature_input["payloadHash"],
            nonce=123,
        )
        self.assertEqual(typed["primaryType"], "ExchangeAction")
        self.assertEqual(typed["domain"]["chainId"], 138565)
        self.assertEqual(typed["types"]["ExchangeAction"][0], {"name": "payloadHash", "type": "bytes32"})
        self.assertEqual(typed["message"]["nonce"], 123)

    def test_action_payload_wrapper_is_required_and_http_body_is_params_only(self) -> None:
        body = perps_update_margin_body(account_id=1001, symbol_id=1, amount="-0.25")

        self.assertEqual(canonical_json(http_body_from_action_payload(body)), '{"accountID":1001,"symbolID":1,"amount":"-0.25"}')
        with self.assertRaises(SoDEXConfigError):
            build_signature_input(
                domain="futures",
                account_id=1001,
                body=OrderedDict([("accountID", 1001)]),
                nonce=123,
            )

    def test_signature_prefix_byte_is_added_once(self) -> None:
        raw = "0x" + "ab" * 65

        prefixed = prefixed_eip712_signature(raw)

        self.assertTrue(prefixed.startswith("0x01"))
        self.assertEqual(prefixed_eip712_signature(prefixed), prefixed)

    def test_account_id_and_dry_run_signer_fail_loudly(self) -> None:
        with self.assertRaises(SoDEXConfigError):
            validate_account_id(-1)
        with self.assertRaises(SoDEXNotReadyError):
            SoDEXDryRunSigner().sign_typed_payload(domain="futures", account_id=1, payload_hash="0x00", nonce=1)
        with self.assertRaises(SoDEXConfigError):
            SoDEXPrivateKeySigner(private_key=None)

    def test_private_key_signer_returns_prefixed_signature(self) -> None:
        signer = SoDEXPrivateKeySigner(
            private_key="0x" + "11" * 32,
            environment="testnet",
        )
        signature = signer.sign_typed_payload(
            domain="futures",
            account_id=1,
            payload_hash="0x" + "22" * 32,
            nonce=1760373925000,
        )

        self.assertTrue(signature.startswith("0x01"))
        self.assertEqual(len(signature), 134)

    def test_perps_new_order_body_preserves_schema_order_and_decimal_strings(self) -> None:
        order = perps_order_item(
            cl_ord_id="siglab-1",
            modifier=1,
            side=1,
            order_type=1,
            time_in_force=2,
            price="100000",
            quantity="0.01",
            reduce_only=True,
            position_side=1,
        )
        body = perps_new_order_body(account_id=1001, symbol_id=1, orders=[order])

        self.assertEqual(list(body.keys()), ["type", "params"])
        self.assertEqual(list(body["params"].keys()), ["accountID", "symbolID", "orders"])
        self.assertEqual(
            list(order.keys()),
            [
                "clOrdID",
                "modifier",
                "side",
                "type",
                "timeInForce",
                "price",
                "quantity",
                "funds",
                "stopPrice",
                "stopType",
                "triggerType",
                "reduceOnly",
                "positionSide",
            ],
        )
        self.assertEqual(
            canonical_json(body),
            (
                '{"type":"newOrder","params":{"accountID":1001,"symbolID":1,'
                '"orders":[{"clOrdID":"siglab-1","modifier":1,"side":1,"type":1,'
                '"timeInForce":2,"price":"100000","quantity":"0.01",'
                '"reduceOnly":true,"positionSide":1}]}}'
            ),
        )

    def test_perps_new_order_body_rejects_invalid_batches(self) -> None:
        order = perps_order_item(
            cl_ord_id="siglab-1",
            modifier=1,
            side=1,
            order_type=1,
            time_in_force=2,
            quantity="0.01",
        )

        with self.assertRaises(SoDEXConfigError):
            perps_new_order_body(account_id=1001, symbol_id=1, orders=[])
        with self.assertRaises(SoDEXConfigError):
            perps_new_order_body(account_id=1001, symbol_id=1, orders=[order] * 101)
        with self.assertRaises(SoDEXConfigError):
            perps_new_order_body(account_id=1001, symbol_id=1, orders=[{"clOrdID": "bad"}])  # type: ignore[list-item]

    def test_perps_update_leverage_body_preserves_schema_order(self) -> None:
        body = perps_update_leverage_body(account_id=1001, symbol_id=1, leverage=5, margin_mode=1)

        self.assertEqual(list(body.keys()), ["type", "params"])
        self.assertEqual(list(body["params"].keys()), ["accountID", "symbolID", "leverage", "marginMode"])
        self.assertEqual(
            canonical_json(body),
            '{"type":"updateLeverage","params":{"accountID":1001,"symbolID":1,"leverage":5,"marginMode":1}}',
        )

    def test_perps_cancel_body_preserves_schema_order_and_exactly_one_identifier(self) -> None:
        cancel = perps_cancel_item(symbol_id=1, cl_ord_id="siglab-1")
        body = perps_cancel_order_body(account_id=1001, cancels=[cancel])

        self.assertEqual(list(cancel.keys()), ["symbolID", "orderID", "clOrdID"])
        self.assertEqual(list(body["params"].keys()), ["accountID", "cancels"])
        self.assertEqual(
            canonical_json(body),
            '{"type":"cancelOrder","params":{"accountID":1001,"cancels":[{"symbolID":1,"clOrdID":"siglab-1"}]}}',
        )
        with self.assertRaises(SoDEXConfigError):
            perps_cancel_item(symbol_id=1)
        with self.assertRaises(SoDEXConfigError):
            perps_cancel_item(symbol_id=1, order_id=10, cl_ord_id="siglab-1")
        with self.assertRaises(SoDEXConfigError):
            perps_cancel_order_body(account_id=1001, cancels=[])
        with self.assertRaises(SoDEXConfigError):
            perps_cancel_order_body(account_id=1001, cancels=[cancel] * 101)
        with self.assertRaises(SoDEXConfigError):
            perps_cancel_order_body(account_id=1001, cancels=[{"symbolID": 1}])  # type: ignore[list-item]

    def test_perps_schedule_cancel_body_omits_unset_timestamp(self) -> None:
        body = perps_schedule_cancel_body(account_id=1001)
        scheduled = perps_schedule_cancel_body(account_id=1001, scheduled_timestamp=1760373930000)

        self.assertEqual(
            canonical_json(body),
            '{"type":"scheduleCancel","params":{"accountID":1001}}',
        )
        self.assertEqual(
            canonical_json(scheduled),
            '{"type":"scheduleCancel","params":{"accountID":1001,"scheduledTimestamp":1760373930000}}',
        )

    def test_perps_update_margin_body_preserves_decimal_string(self) -> None:
        body = perps_update_margin_body(account_id=1001, symbol_id=1, amount="-0.25")

        self.assertEqual(list(body["params"].keys()), ["accountID", "symbolID", "amount"])
        self.assertEqual(
            canonical_json(body),
            '{"type":"updateMargin","params":{"accountID":1001,"symbolID":1,"amount":"-0.25"}}',
        )
        with self.assertRaises(SoDEXConfigError):
            perps_update_margin_body(account_id=1001, symbol_id=1, amount="")


if __name__ == "__main__":
    unittest.main()
