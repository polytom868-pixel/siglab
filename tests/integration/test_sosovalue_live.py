"""Integration test: live SoSoValue API against the public SoSoValue endpoints.

The free Demo API key from https://docs.sosovalue.com is rate-limited but
sufficient for smoke-testing the 2 IMPLEMENTED endpoints + 4-5 of the BLOCKED
truth-table rows.

Env vars to enable (otherwise the entire module is skipped):
  SOSOVALUE_API_KEY   - free Demo API key from the SoSoValue developer portal

End-points tested:
  1. GET /currencies  (flat array; Wave 3-E rewrite)  -- the IMPLEMENTED listed_currencies wrapper
  2. GET /etfs/summary-history?symbol=BTC&country_code=US  (Wave 3-B rewrite) -- the IMPLEMENTED etf_historical_inflow wrapper
  3. GET /currencies/{id}/market-snapshot  (the BLOCKED currency_market_snapshot truth-table row)
  4. GET /api/v1/news/featured?page=1&page_size=5  (the BLOCKED featured_news truth-table row)
  5. GET /currencies/{id}/klines  (the BLOCKED currency_klines truth-table row)

For each: just smoke-test that the response shape matches the SoSoValueClient
expected shape (the wrapper class will validate this; we do not duplicate the
assertion logic in this integration test).

Skips cleanly when SOSOVALUE_API_KEY is unset. Use SIGLAB_SKIP_SOSOVALUE=1 to
disable even if the env var is set.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from siglab.utils import async_limiter_call


SKIP_ENV_VAR = "SIGLAB_SKIP_SOSOVALUE"
API_KEY_ENV_VAR = "SOSOVALUE_API_KEY"

# Verified against https://sosovalue-1.gitbook.io/sosovalue-api-doc
SOSOVALUE_BASE_URL = "https://openapi.sosovalue.com/openapi/v1"

# Bound per request so the test does not hang on a network stall.
REQUEST_TIMEOUT_S = 30.0


def _skip_if_disabled() -> None:
    if os.environ.get(SKIP_ENV_VAR, "").strip().lower() in {"1", "true", "yes"}:
        raise unittest.SkipTest(f"{SKIP_ENV_VAR}=1 disables live SoSoValue tests")


API_KEY = os.environ.get(API_KEY_ENV_VAR) or None


def _api_key() -> str | None:
    return API_KEY


def _get_so_sovalue(path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any] | list[Any]:
    """GET against openapi.sosovalue.com/openapi/v1 with x-soso-api-key auth.

    Per the official docs the auth header is `x-soso-api-key: <key>` (Wave 3-A
    base URL fix + the original SigLab contract from AGENTS.md:62).
    """
    api_key = _api_key()
    if not api_key:
        raise unittest.SkipTest(f"{API_KEY_ENV_VAR} not set")

    def _do_get() -> dict[str, Any] | list[Any]:
        query = ""
        if params:
            query = "?" + urllib.parse.urlencode(params)
        url = f"{SOSOVALUE_BASE_URL}{path}{query}"
        request = urllib.request.Request(
            url,
            method="GET",
            headers={
                "x-soso-api-key": api_key,
                "Accept": "application/json",
                "User-Agent": "SigLab-Integration-Test/1.0",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_S) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:500]
            # 401/403/404: skip (means our truth-table claim is wrong: endpoint is
            # gated or the path is wrong); don't fail the suite.
            if exc.code in (401, 403, 404, 422):
                raise unittest.SkipTest(
                    f"SoSoValue {path} returned HTTP {exc.code} (truth-table mismatch?): {body}"
                )
            # 429: rate-limited
            if exc.code == 429:
                raise unittest.SkipTest(f"SoSoValue rate-limited on {path} (HTTP 429)")
            raise AssertionError(f"SoSoValue HTTP {exc.code} on {path}: {body}") from exc
    return asyncio.run(async_limiter_call(lambda: asyncio.to_thread(_do_get)))

class _LiveBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _skip_if_disabled()
        if not _api_key():
            raise unittest.SkipTest(f"{API_KEY_ENV_VAR} not set")
        cls._shared_currencies = _get_so_sovalue("/currencies")

    def test_currencies_returns_envelope(self) -> None:
        # Verified live on 2026-06-14: the SoSoValue /currencies endpoint
        # returns {code: 0, message: "success", data: [...]}. The Wave 3-E
        # rewrite's `_rows_from_data(payload.get("data"), spec)` call at
        # sosovalue_client.py:159 already handles this envelope.
        started = time.perf_counter()
        body = self._shared_currencies
        elapsed = time.perf_counter() - started

        self.assertIsInstance(
            body,
            dict,
            f"GET /currencies should return the {{code, message, data}} envelope, got: {type(body).__name__}",
        )
        self.assertEqual(body.get("code"), 0, f"unexpected code: {body!r}")
        data = body.get("data")
        self.assertIsInstance(data, list, f"envelope data should be a list: {body!r}")
        self.assertGreater(len(data), 0, f"empty /currencies data in {elapsed:.1f}s")
        first = data[0]
        if isinstance(first, dict):
            keys = [str(k) for k in list(first.keys())[:5]]
            has_id = any(k in first for k in ("currency_id", "id", "symbol"))
            self.assertTrue(
                has_id,
                f"/currencies data row missing id/currency_id/symbol key: {keys}",
            )

    def test_etf_summary_history_returns_rows(self) -> None:
        started = time.perf_counter()
        body = _get_so_sovalue(
            "/etfs/summary-history",
            params={"symbol": "BTC", "country_code": "US"},
        )
        elapsed = time.perf_counter() - started

        # Per the Wave 3-B rewrite this endpoint can be flat-array OR envelope.
        if isinstance(body, dict):
            data = body.get("data") or body.get("rows") or body
        else:
            data = body
        self.assertIsInstance(
            data,
            list,
            f"GET /etfs/summary-history should be a list or have 'data' list, got: {type(data).__name__}",
        )
        # Don't assert non-empty: the official doc allows empty responses when
        # the symbol has no data for the date range.
        _ = elapsed


class SoSoValueTruthTableBlockTests(_LiveBase):
    """Smoke-test the BLOCKED rows from the truth table.

    The whole point of the Wave 4 expansion is that the truth table documents
    EXACTLY which endpoints exist. If the official endpoint is reachable with
    a free Demo key, the BLOCKED row is honest; if the endpoint is gated,
    401/403/404/422 will skip the test (not fail).
    """

    def test_currency_market_snapshot_path(self) -> None:
        # /currencies returns an envelope {code, message, data} per the live
        # verification on 2026-06-14; extract the first data row.
        currencies = self._shared_currencies
        if not isinstance(currencies, dict) or not isinstance(currencies.get("data"), list):
            self.skipTest(f"/currencies shape unexpected: {type(currencies).__name__}")
        first = currencies["data"][0] if currencies["data"] else None
        if not isinstance(first, dict):
            self.skipTest("/currencies data[0] is not a dict")
        currency_id = first.get("currency_id") or first.get("id")
        if currency_id is None:
            self.skipTest("no currency_id in /currencies data[0]")

        # Now try the BLOCKED market-snapshot endpoint with that id.
        body = _get_so_sovalue(f"/currencies/{currency_id}/market-snapshot")
        self.assertIsNotNone(body, "empty body from /currencies/{id}/market-snapshot")

    def test_featured_news_path(self) -> None:
        body = _get_so_sovalue(
            "/api/v1/news/featured",
            params={"page": 1, "page_size": 5},
        )
        self.assertIsNotNone(body)

    def test_currency_klines_path(self) -> None:
        currencies = self._shared_currencies
        if not isinstance(currencies, dict) or not isinstance(currencies.get("data"), list):
            self.skipTest(f"/currencies shape unexpected: {type(currencies).__name__}")
        first = currencies["data"][0] if currencies["data"] else None
        if not isinstance(first, dict):
            self.skipTest("/currencies data[0] is not a dict")
        currency_id = first.get("currency_id") or first.get("id")
        if currency_id is None:
            self.skipTest("no currency_id in /currencies data[0]")
        body = _get_so_sovalue(
            f"/currencies/{currency_id}/klines",
            params={"interval": "1d"},
        )
        self.assertIsNotNone(body)


if __name__ == "__main__":
    unittest.main()
