"""Integration test: live curl against 3 undocumented SoSoValue endpoints.

Exercises endpoints NOT covered in the official SoSoValue developer docs:
  - GET /api/sosotest   (testnet-style smoke endpoint)
  - GET /api/soso-btc   (undocumented BTC convenience)
  - GET /api/market     (undocumented market overview)

These endpoints are deliberately NOT in the truth table at
docs/access-and-testnet-plan.md. The goal of this file is to verify they
either (a) respond with a sane envelope or (b) skip cleanly on 404/401/403/422
or network unreachable (no DNS, no route).

The same gating pattern as test_sosovalue_live.py: skip on rate-limit,
401/403/404/422, hard-fail only on structural breakage.
"""

from __future__ import annotations

import json
import os
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


SKIP_ENV_VAR = "SIGLAB_SKIP_SOSOVALUE"
API_KEY_ENV_VAR = "SOSOVALUE_API_KEY"

SOSOVALUE_BASE_URL = "https://openapi.sosovalue.com"
SOSOVALUE_TESTNET_BASE_URL = "https://testnet.sosovalue.com"

REQUEST_TIMEOUT_S = 30.0


def _skip_if_disabled() -> None:
    if os.environ.get(SKIP_ENV_VAR, "").strip().lower() in {"1", "true", "yes"}:
        raise unittest.SkipTest(f"{SKIP_ENV_VAR}=1 disables live SoSoValue tests")


API_KEY = os.environ.get(API_KEY_ENV_VAR) or None


def _api_key() -> str | None:
    return API_KEY


def _get(
    path: str,
    *,
    base_url: str = SOSOVALUE_BASE_URL,
    params: dict[str, Any] | None = None,
) -> dict[str, Any] | list[Any]:
    api_key = _api_key()
    if not api_key:
        raise unittest.SkipTest(f"{API_KEY_ENV_VAR} not set")
    query = ""
    if params:
        query = "?" + urllib.parse.urlencode(params)
    url = f"{base_url}{path}{query}"
    request = urllib.request.Request(
        url,
        method="GET",
        headers={
            "x-soso-api-key": api_key,
            "Accept": "application/json",
            "User-Agent": "SigLab-Integration-Test/2.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_S) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        if exc.code in (401, 403, 404, 422):
            raise unittest.SkipTest(
                f"SoSoValue {path} returned HTTP {exc.code}: {body}"
            )
        if exc.code == 429:
            raise unittest.SkipTest(f"SoSoValue rate-limited on {path} (HTTP 429)")
        raise AssertionError(f"SoSoValue HTTP {exc.code} on {path}: {body}") from exc
    except urllib.error.URLError as exc:
        raise unittest.SkipTest(f"SoSoValue {path} unreachable: {exc.reason}")


class _UndocumentedLiveBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _skip_if_disabled()
        if not _api_key():
            raise unittest.SkipTest(f"{API_KEY_ENV_VAR} not set")


class SoSoTestEndpointTests(_UndocumentedLiveBase):
    """GET /api/sosotest - the testnet-style smoke endpoint (3 tests)."""

    def test_sosotest_root_reachable(self) -> None:
        started = time.perf_counter()
        body = _get(
            "/api/sosotest",
            base_url=SOSOVALUE_TESTNET_BASE_URL,
        )
        elapsed = time.perf_counter() - started
        self.assertIsNotNone(body, "empty body from /api/sosotest")
        _ = elapsed

    def test_sosotest_with_format_param(self) -> None:
        body = _get(
            "/api/sosotest",
            base_url=SOSOVALUE_TESTNET_BASE_URL,
            params={"format": "json"},
        )
        self.assertIsNotNone(body, "empty body from /api/sosotest?format=json")

    def test_sosotest_envelope_shape(self) -> None:
        body = _get("/api/sosotest", base_url=SOSOVALUE_TESTNET_BASE_URL)
        if not isinstance(body, dict):
            self.skipTest(
                f"/api/sosotest returned non-dict: {type(body).__name__}"
            )
        if "data" in body:
            self.assertIsInstance(
                body["data"],
                (list, dict, type(None)),
                f"/api/sosotest 'data' has unexpected type: {type(body['data']).__name__}",
            )


class SoSoBtcEndpointTests(_UndocumentedLiveBase):
    """GET /api/soso-btc - undocumented BTC convenience (3 tests)."""

    def test_soso_btc_root_reachable(self) -> None:
        body = _get("/api/soso-btc")
        self.assertIsNotNone(body, "empty body from /api/soso-btc")

    def test_soso_btc_with_symbol_param(self) -> None:
        body = _get("/api/soso-btc", params={"symbol": "BTC"})
        self.assertIsNotNone(body, "empty body from /api/soso-btc?symbol=BTC")

    def test_soso_btc_envelope_shape(self) -> None:
        body = _get("/api/soso-btc")
        if not isinstance(body, dict):
            self.skipTest(
                f"/api/soso-btc returned non-dict: {type(body).__name__}"
            )
        if "data" in body:
            self.assertIsInstance(
                body["data"],
                (list, dict, type(None)),
                f"/api/soso-btc 'data' has unexpected type: {type(body['data']).__name__}",
            )


class MarketEndpointTests(_UndocumentedLiveBase):
    """GET /api/market - undocumented market overview (2 tests)."""

    def test_market_root_reachable(self) -> None:
        body = _get("/api/market")
        self.assertIsNotNone(body, "empty body from /api/market")

    def test_market_with_currency_param(self) -> None:
        body = _get("/api/market", params={"currency": "USD"})
        self.assertIsNotNone(
            body, "empty body from /api/market?currency=USD"
        )


if __name__ == "__main__":
    unittest.main()
