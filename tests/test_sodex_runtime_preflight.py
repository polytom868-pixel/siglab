from __future__ import annotations

import unittest

from siglab.live.runtime import SoDEXExecutionAdapter


class _Client:
    def get_user_state(self) -> dict:
        return {}

    def update_leverage(self) -> None:
        return None

    def place_market_order(self) -> tuple[bool, str]:
        return True, "ok"

    def all_mids(self) -> dict:
        return {}


class _Signer:
    signer_type = "test"


class SoDEXRuntimePreflightTests(unittest.TestCase):
    def test_dependency_report_lists_signed_path_missing_prerequisites(self) -> None:
        report = SoDEXExecutionAdapter(config={}).dependency_report()

        self.assertFalse(report["client_configured"])
        self.assertFalse(report["signed_path"]["ready"])
        self.assertIn("signer", report["signed_path"]["missing_prerequisites"])
        self.assertIn("accountID", report["signed_path"]["missing_prerequisites"])
        self.assertIn("api_key_name", report["signed_path"]["missing_prerequisites"])
        self.assertEqual(report["rate_limit_scope"]["scope"], "per_ip")
        self.assertTrue(report["rate_limit_scope"]["local_scheduler_only"])

    def test_dependency_report_marks_signed_path_ready_when_all_prereqs_exist(self) -> None:
        adapter = SoDEXExecutionAdapter(
            config={
                "sodex_client": _Client(),
                "sodex_signing": {
                    "signer": _Signer(),
                    "api_key_name": "siglab-key",
                    "accountID": 1001,
                    "nonce_store_path": "/tmp/sodex-nonce.json",
                    "environment": "testnet",
                },
            }
        )
        report = adapter.dependency_report()

        self.assertTrue(report["signed_path"]["ready"])
        self.assertEqual(report["signed_path"]["environment"], "testnet")
        self.assertEqual(report["signed_path"]["signer_type"], "test")
        self.assertEqual(report["signed_path"]["missing_prerequisites"], [])
        self.assertIn("external shared limiter", report["rate_limit_scope"]["operator_warning"])


if __name__ == "__main__":
    unittest.main()
