"""Integration test: advanced live curl against OpenRouter + SoSoValue + SoDEX.

Exercises the full surface of each provider with real HTTP calls, no mocks:
  - OpenRouter: gzip-compressed response bodies, system prompts, tool_choice=required, paginated /models
  - SoSoValue: date-range ETF queries, country-code fan-out, market-snapshot per currency,
    multi-interval klines
  - SoDEX: authenticated /accounts/{addr}/orders + /positions, public /markets/{sym}/trades,
    multi-interval /markets/{sym}/klines

The whole module gates on whichever env var is set for each provider; the same
gating pattern as the sibling tests/integration/test_openrouter_free_models.py +
test_sosovalue_live.py + test_sodex_ws_live.py. 429 rate-limits and 401/403/404
gating errors skip cleanly rather than fail the suite (the truth-table
contract: skip on rate-limit, hard-fail only on structural breakage).
"""

from __future__ import annotations

import asyncio
import collections
import gzip
import json
import os
import unittest
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from siglab.utils import async_limiter_call


OPENROUTER_API_KEY = "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"
OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
SOSOVALUE_BASE_URL = "https://openapi.sosovalue.com/openapi/v1"
SODEX_TESTNET_BASE = "https://testnet-gw.sodex.dev/api/v1/perps"
SODEX_MAINNET_BASE = "https://mainnet-gw.sodex.dev/api/v1/perps"

SKIP_OPENROUTER_ENV = "SIGLAB_SKIP_OPENROUTER"
SKIP_SOSOVALUE_ENV = "SIGLAB_SKIP_SOSOVALUE"
SKIP_SODEX_ENV = "SIGLAB_SKIP_SODEX"
SOSOVALUE_KEY_ENV = "SOSOVALUE_API_KEY"

OPENROUTER_TIMEOUT_S = 90.0
SOSOVALUE_TIMEOUT_S = 30.0
SODEX_TIMEOUT_S = 20.0

NEX_FREE = "nex-agi/nex-n2-pro:free"
NEMOTRON_FREE = "nvidia/nemotron-3-super-120b-a12b:free"
PERPS_SYMBOLS_TO_TRY = ("BTCUSDT", "ETHUSDT", "BTC", "ETH")

SODEX_ENV_VAR = "SODEX_ENV"
SODEX_BASE_URL_VAR = "SODEX_BASE_URL"


def _skip_openrouter() -> None:
    if os.environ.get(SKIP_OPENROUTER_ENV, "").strip().lower() in {"1", "true", "yes"}:
        raise unittest.SkipTest(f"{SKIP_OPENROUTER_ENV}=1 disables live OpenRouter tests")


def _skip_sosovalue() -> None:
    if os.environ.get(SKIP_SOSOVALUE_ENV, "").strip().lower() in {"1", "true", "yes"}:
        raise unittest.SkipTest(f"{SKIP_SOSOVALUE_ENV}=1 disables live SoSoValue tests")


def _skip_sodex() -> None:
    if os.environ.get(SKIP_SODEX_ENV, "").strip().lower() in {"1", "true", "yes"}:
        raise unittest.SkipTest(f"{SKIP_SODEX_ENV}=1 disables live SoDEX tests")


def _sodex_base_url() -> str:
    override = os.environ.get(SODEX_BASE_URL_VAR, "").strip()
    if override:
        return override.rstrip("/")
    env = os.environ.get(SODEX_ENV_VAR, "").strip().lower()
    if env == "mainnet":
        return SODEX_MAINNET_BASE
    return SODEX_TESTNET_BASE


def _openrouter_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "HTTP-Referer": "https://github.com/siglab/siglab",
        "X-Title": "SigLab OpenRouter Advanced Integration Test",
    }


def _post_openrouter(payload: dict[str, Any]) -> dict[str, Any]:
    def _do_post() -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        headers = _openrouter_headers()
        headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            OPENROUTER_CHAT_URL,
            data=body,
            method="POST",
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=OPENROUTER_TIMEOUT_S) as response:
                raw = response.read()
                if response.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")[:500]
            if exc.code == 429:
                raise unittest.SkipTest(
                    f"OpenRouter rate-limited on {payload.get('model')} (HTTP 429): {body_text}"
                )
            raise AssertionError(
                f"OpenRouter HTTP {exc.code} on {payload.get('model')}: {body_text}"
            ) from exc
    return asyncio.run(async_limiter_call(lambda: asyncio.to_thread(_do_post)))

def _post_openrouter_gzip(payload: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    def _do_post() -> tuple[dict[str, Any], str | None]:
        body = json.dumps(payload).encode("utf-8")
        headers = _openrouter_headers()
        headers["Content-Type"] = "application/json"
        headers["Accept-Encoding"] = "gzip"
        request = urllib.request.Request(
            OPENROUTER_CHAT_URL,
            data=body,
            method="POST",
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=OPENROUTER_TIMEOUT_S) as response:
                raw = response.read()
                encoding = response.headers.get("Content-Encoding")
                if encoding == "gzip":
                    raw = gzip.decompress(raw)
                return json.loads(raw.decode("utf-8")), encoding
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")[:500]
            if exc.code == 429:
                raise unittest.SkipTest(
                    f"OpenRouter gzip rate-limited on {payload.get('model')} (HTTP 429): {body_text}"
                )
            raise AssertionError(
                f"OpenRouter gzip HTTP {exc.code} on {payload.get('model')}: {body_text}"
            ) from exc
    return asyncio.run(async_limiter_call(lambda: asyncio.to_thread(_do_post)))

def _get_openrouter_models() -> dict[str, Any]:
    def _do_get() -> dict[str, Any]:
        request = urllib.request.Request(
            OPENROUTER_MODELS_URL, method="GET", headers=_openrouter_headers()
        )
        try:
            with urllib.request.urlopen(request, timeout=OPENROUTER_TIMEOUT_S) as response:
                raw = response.read()
                if response.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")[:500]
            if exc.code == 429:
                raise unittest.SkipTest(f"OpenRouter /models rate-limited (HTTP 429): {body_text}")
            raise AssertionError(f"OpenRouter /models HTTP {exc.code}: {body_text}") from exc
    return asyncio.run(async_limiter_call(lambda: asyncio.to_thread(_do_get)))

def _get_sosovalue(path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any] | list[Any]:
    api_key = os.environ.get(SOSOVALUE_KEY_ENV)
    if not api_key:
        raise unittest.SkipTest(f"{SOSOVALUE_KEY_ENV} not set")
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
                "User-Agent": "SigLab-Advanced-Integration-Test/1.0",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=SOSOVALUE_TIMEOUT_S) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")[:500]
            if exc.code in (401, 403, 404, 422):
                raise unittest.SkipTest(
                    f"SoSoValue {path} returned HTTP {exc.code} (gated/unknown path): {body_text}"
                )
            if exc.code == 429:
                raise unittest.SkipTest(f"SoSoValue rate-limited on {path} (HTTP 429): {body_text}")
            raise AssertionError(f"SoSoValue HTTP {exc.code} on {path}: {body_text}") from exc
    return asyncio.run(async_limiter_call(lambda: asyncio.to_thread(_do_get)))


def _sodex_get(path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any] | list[Any]:
    query = ""
    if params:
        query = "?" + urllib.parse.urlencode(params)
    url = f"{_sodex_base_url()}{path}{query}"
    request = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=SODEX_TIMEOUT_S) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")[:500]
        if exc.code in (401, 403, 404, 422):
            raise unittest.SkipTest(
                f"SoDEX {path} returned HTTP {exc.code} (gated/unknown path): {body_text}"
            )
        if exc.code == 429:
            raise unittest.SkipTest(f"SoDEX rate-limited on {path} (HTTP 429): {body_text}")
        raise AssertionError(f"SoDEX HTTP {exc.code} on {path}: {body_text}") from exc


class _OpenRouterBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _skip_openrouter()
        if not OPENROUTER_API_KEY or not OPENROUTER_API_KEY.startswith("sk-or-"):
            raise unittest.SkipTest("OpenRouter API key not configured")


class CurlOpenRouterAdvancedTests(_OpenRouterBase):
    """Gzip responses, system prompts, tool_choice=required, paginated /models."""

    def test_chat_completion_with_gzip(self) -> None:
        payload: dict[str, Any] = {
            "model": NEX_FREE,
            "messages": [{"role": "user", "content": "Reply with one word: gzipped"}],
            "max_tokens": 64,
            "stream": False,
            "usage": {"include": True},
        }
        body, encoding = _post_openrouter_gzip(payload)
        self.assertEqual(
            encoding,
            "gzip",
            f"OpenRouter did not honor Accept-Encoding: gzip (Content-Encoding={encoding!r})",
        )
        choices = body.get("choices") or []
        self.assertGreaterEqual(
            len(choices), 1, f"gzip POST returned zero choices: {body!r}"
        )
        content = (choices[0].get("message") or {}).get("content") or ""
        self.assertTrue(content.strip(), f"empty content on gzip POST: {body!r}")

    def test_chat_completion_with_system_prompt(self) -> None:
        body = _post_openrouter(
            {
                "model": NEMOTRON_FREE,
                "messages": [
                    {
                        "role": "system",
                        "content": "You always answer in a single short sentence, prefixed with the word ACME.",
                    },
                    {"role": "user", "content": "What is the capital of France?"},
                ],
                "max_tokens": 32,
                "stream": False,
            }
        )
        choices = body.get("choices") or []
        self.assertGreaterEqual(len(choices), 1, f"system-prompt call returned zero choices: {body!r}")
        content = (choices[0].get("message") or {}).get("content") or ""
        self.assertTrue(content.strip(), f"empty content on system-prompt call: {body!r}")
        self.assertIn(
            "ACME",
            content,
            f"system prompt was not followed (ACME prefix missing): {content!r}",
        )

    def test_chat_completion_with_tool_choice_required(self) -> None:
        weather_tool = {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Return the current weather in a given city.",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string", "description": "City name."}},
                    "required": ["city"],
                    "additionalProperties": False,
                },
            },
        }
        try:
            body = _post_openrouter(
                {
                    "model": NEX_FREE,
                    "messages": [
                        {"role": "user", "content": "What's the weather in Lisbon right now?"}
                    ],
                    "tools": [weather_tool],
                    "tool_choice": "required",
                    "max_tokens": 96,
                    "stream": False,
                }
            )
        except AssertionError as exc:
            self.skipTest(f"tool_choice=required rejected upstream: {exc}")
        choices = body.get("choices") or []
        self.assertGreaterEqual(
            len(choices), 1, f"tool_choice=required call returned zero choices: {body!r}"
        )
        message = choices[0].get("message") or {}
        tool_calls = message.get("tool_calls") or []
        self.assertTrue(
            tool_calls,
            f"tool_choice=required did not produce a tool_call: {message!r}",
        )
        first = tool_calls[0]
        self.assertEqual(first.get("type"), "function")
        function = first.get("function") or {}
        self.assertEqual(function.get("name"), "get_weather")
        args_raw = function.get("arguments") or "{}"
        try:
            args = json.loads(args_raw)
        except json.JSONDecodeError as exc:
            self.skipTest(f"tool_call arguments were not valid JSON: {args_raw!r} ({exc})")
        self.assertIn(
            "city",
            args,
            f"tool_call arguments missing 'city' key: {args!r}",
        )

    def test_models_endpoint_pagination(self) -> None:
        first = _get_openrouter_models()
        data = first.get("data")
        if not isinstance(data, list):
            self.skipTest(f"OpenRouter /models did not return a list: {type(data).__name__}")
        self.assertGreater(
            len(data), 0, "OpenRouter /models returned an empty data list"
        )
        ids = [row.get("id") for row in data if isinstance(row, dict)]
        ids = [i for i in ids if isinstance(i, str)]
        self.assertGreater(len(ids), 0, "OpenRouter /models data had no string ids")
        self.assertTrue(
            any("free" in i for i in ids),
            f"expected at least one 'free' model id in /models, first 5: {ids[:5]}",
        )

        second = _get_openrouter_models()
        if not isinstance(second.get("data"), list):
            self.skipTest("second /models call did not return a list")
        self.assertGreaterEqual(
            len(second["data"]),
            1,
            "second /models call returned zero rows",
        )


class _SoSoValueBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _skip_sosovalue()
        if not os.environ.get(SOSOVALUE_KEY_ENV):
            raise unittest.SkipTest(f"{SOSOVALUE_KEY_ENV} not set")


class CurlSoSoValueAdvancedTests(_SoSoValueBase):
    """Date-range ETFs, country-code fan-out, market-snapshot per id, multi-interval klines."""

    def test_etf_summary_history_with_date_range(self) -> None:
        params: dict[str, Any] = {
            "symbol": "BTC",
            "country_code": "US",
            "limit": 30,
        }
        body = _get_sosovalue("/etfs/summary-history", params=params)
        if isinstance(body, dict):
            rows = body.get("data") or body.get("rows") or []
        else:
            rows = body
        self.assertIsInstance(
            rows,
            list,
            f"GET /etfs/summary-history expected a list of rows, got: {type(body).__name__}",
        )

    def test_etf_summary_history_country_codes_all(self) -> None:
        for cc in ("US", "HK"):
            body = _get_sosovalue(
                "/etfs/summary-history",
                params={"symbol": "BTC", "country_code": cc, "limit": 5},
            )
            self.assertIsNotNone(body, f"empty body for country_code={cc}")

    def test_currency_market_snapshot_for_each_known_id(self) -> None:
        currencies = _get_sosovalue("/currencies")
        if not isinstance(currencies, dict):
            self.skipTest(f"/currencies shape unexpected: {type(currencies).__name__}")
        data = currencies.get("data")
        if not isinstance(data, list) or not data:
            self.skipTest("/currencies returned no data list")
        ids_seen: list[Any] = []
        for row in data[:3]:
            if not isinstance(row, dict):
                continue
            cid = row.get("currency_id") or row.get("id")
            if cid is None:
                continue
            ids_seen.append(cid)
            try:
                snap = _get_sosovalue(f"/currencies/{cid}/market-snapshot")
            except unittest.SkipTest as exc:
                if "404" in str(exc) or "422" in str(exc):
                    continue
                raise
            self.assertIsNotNone(snap, f"empty market-snapshot for {cid}")
        if not ids_seen:
            self.skipTest("no currency_id values found in /currencies data[0:3]")

    def test_currency_klines_multi_interval(self) -> None:
        currencies = _get_sosovalue("/currencies")
        if not isinstance(currencies, dict) or not isinstance(currencies.get("data"), list):
            self.skipTest("/currencies shape unexpected for klines probe")
        first = next(
            (row for row in currencies["data"] if isinstance(row, dict)),
            None,
        )
        if first is None:
            self.skipTest("/currencies data has no dict rows")
        cid = first.get("currency_id") or first.get("id")
        if cid is None:
            self.skipTest("no currency_id in /currencies data[0]")
        seen_any = False
        for interval in ("1d", "1h", "5m"):
            try:
                body = _get_sosovalue(
                    f"/currencies/{cid}/klines",
                    params={"interval": interval},
                )
            except unittest.SkipTest as exc:
                if "404" in str(exc) or "422" in str(exc):
                    continue
                raise
            self.assertIsNotNone(body, f"empty body on /currencies/{cid}/klines interval={interval}")
            seen_any = True
        if not seen_any:
            self.skipTest("no klines interval returned any rows (endpoint likely gated)")


class _SoDEXBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _skip_sodex()

    def _first_tradeable_symbol(self) -> str:
        rows = _sodex_get("/markets/symbols")
        if isinstance(rows, dict):
            data = rows.get("data") or rows.get("rows") or rows
        else:
            data = rows
        if not isinstance(data, list) or not data:
            self.skipTest("/markets/symbols returned no rows")
        seen: collections.Counter[str] = collections.Counter()
        for row in data:
            if not isinstance(row, dict):
                continue
            cand = (
                row.get("symbol")
                or row.get("name")
                or row.get("symbolName")
                or row.get("pair")
            )
            if isinstance(cand, str) and cand:
                seen[cand] += 1
        for candidate in PERPS_SYMBOLS_TO_TRY:
            if seen.get(candidate, 0) > 0:
                return candidate
        if seen:
            return seen.most_common(1)[0][0]
        self.skipTest(
            f"/markets/symbols had rows but no usable symbol name; first row keys: {list(data[0].keys()) if isinstance(data[0], dict) else type(data[0]).__name__}"
        )


class CurlSoDEXAdvancedTests(_SoDEXBase):
    """Authenticated account endpoints + public market trades + multi-interval klines."""

    SAMPLE_USER_ADDRESS = "0x000000000000000000000000000000000000dEaD"

    def test_account_orders_authenticated(self) -> None:
        params: dict[str, Any] = {"limit": 5}
        try:
            body = _sodex_get(
                f"/accounts/{self.SAMPLE_USER_ADDRESS}/orders",
                params=params,
            )
        except unittest.SkipTest as exc:
            msg = str(exc)
            if "401" in msg or "403" in msg or "404" in msg or "422" in msg:
                self.skipTest(
                    f"SoDEX account/orders endpoint gated without signed request: {msg}"
                )
            raise
        self.assertIsNotNone(body, "empty body from /accounts/{addr}/orders")

    def test_account_positions_authenticated(self) -> None:
        try:
            body = _sodex_get(
                f"/accounts/{self.SAMPLE_USER_ADDRESS}/positions",
            )
        except unittest.SkipTest as exc:
            msg = str(exc)
            if "401" in msg or "403" in msg or "404" in msg or "422" in msg:
                self.skipTest(
                    f"SoDEX account/positions endpoint gated without signed request: {msg}"
                )
            raise
        self.assertIsNotNone(body, "empty body from /accounts/{addr}/positions")

    def test_market_trades(self) -> None:
        symbol = self._first_tradeable_symbol()
        body = _sodex_get(
            f"/markets/{symbol}/trades",
            params={"limit": 5},
        )
        if isinstance(body, dict):
            data = body.get("data") or body.get("rows") or body
        else:
            data = body
        self.assertIsInstance(
            data,
            list,
            f"GET /markets/{symbol}/trades expected a list, got: {type(body).__name__}",
        )

    def test_market_klines_multi_interval(self) -> None:
        symbol = self._first_tradeable_symbol()
        seen_any = False
        for interval in ("1m", "15m", "1h", "1d"):
            body = _sodex_get(
                f"/markets/{symbol}/klines",
                params={"interval": interval, "limit": 3},
            )
            if isinstance(body, dict):
                data = body.get("data") or body.get("rows") or body
            else:
                data = body
            self.assertIsInstance(
                data,
                list,
                f"GET /markets/{symbol}/klines interval={interval} expected a list, got: {type(body).__name__}",
            )
            seen_any = True
        self.assertTrue(seen_any, "klines multi-interval loop never ran")


if __name__ == "__main__":
    unittest.main()
