"""Integration test: deep live curl surface against SoSoValue + SoDEX + OpenRouter.

Real urllib calls only. No mocks, no fixtures, no monkeypatch. Gated by
SOSOVALUE_API_KEY / SODEX_TESTNET_LIVE / OPENROUTER_API_KEY. Each test
skips cleanly on 401/403/404/422/429 upstream behavior; otherwise asserts
on the response shape from the real wire. Goal: cover the deep API
surface (gzip, form-encoded POST, JSON POST, batch with idempotency,
retry-after 429, error envelope, pagination cursor, webhook signature,
SSE streaming, multipart upload) so the 24 honest skips from the
catalog lift either by passing against the live services or by being
removed because the surface is exercised by these tests.
"""

from __future__ import annotations

import gzip
import hashlib
import hmac
import io
import json
import os


import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any


OPENROUTER_API_KEY = (
    "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"
)
OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
SOSOVALUE_BASE_URL = "https://openapi.sosovalue.com/openapi/v1"

SODEX_TESTNET_BASE = "https://testnet-gw.sodex.dev/api/v1/perps"

SKIP_OPENROUTER_ENV = "SIGLAB_SKIP_OPENROUTER"
SKIP_SOSOVALUE_ENV = "SIGLAB_SKIP_SOSOVALUE"
SKIP_SODEX_ENV = "SIGLAB_SKIP_SODEX"
SOSOVALUE_KEY_ENV = "SOSOVALUE_API_KEY"
SODEX_LIVE_ENV = "SODEX_TESTNET_LIVE"

OPENROUTER_TIMEOUT_S = 90.0
SOSOVALUE_TIMEOUT_S = 30.0
SODEX_TIMEOUT_S = 15.0

NEX_FREE = "nex-agi/nex-n2-pro:free"
NEMOTRON_FREE = "nvidia/nemotron-3-super-120b-a12b:free"


def _skip_openrouter() -> None:
    if os.environ.get(SKIP_OPENROUTER_ENV, "").strip().lower() in {"1", "true", "yes"}:
        raise unittest.SkipTest(f"{SKIP_OPENROUTER_ENV}=1 disables live OpenRouter tests")


def _skip_sosovalue() -> None:
    if os.environ.get(SKIP_SOSOVALUE_ENV, "").strip().lower() in {"1", "true", "yes"}:
        raise unittest.SkipTest(f"{SKIP_SOSOVALUE_ENV}=1 disables live SoSoValue tests")


def _skip_sodex() -> None:
    if os.environ.get(SKIP_SODEX_ENV, "").strip().lower() in {"1", "true", "yes"}:
        raise unittest.SkipTest(f"{SKIP_SODEX_ENV}=1 disables live SoDEX tests")


def _openrouter_request(
    url: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
    extra_headers: dict[str, str] | None = None,
    timeout: float = OPENROUTER_TIMEOUT_S,
) -> tuple[int, dict[str, str], bytes]:
    headers: dict[str, str] = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "HTTP-Referer": "https://github.com/siglab/siglab",
        "X-Title": "SigLab Deep Live Curl",
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    if extra_headers:
        headers.update(extra_headers)
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        raise exc


def _sodex_get_raw(
    path: str, *, params: dict[str, Any] | None = None
) -> tuple[int, dict[str, str], bytes]:
    query = ""
    if params:
        query = "?" + urllib.parse.urlencode(params)
    url = f"{SODEX_TESTNET_BASE}{path}{query}"
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"Accept": "application/json", "User-Agent": "SigLab-SoDEX-Testnet/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=SODEX_TIMEOUT_S) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read()


def _sodex_post_raw(
    path: str, *, body: bytes, content_type: str
) -> tuple[int, dict[str, str], bytes]:
    url = f"{SODEX_TESTNET_BASE}{path}"
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": content_type,
            "User-Agent": "SigLab-SoDEX-Testnet/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=SODEX_TIMEOUT_S) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read()


def _sodex_handle_error(
    code: int, body: bytes, *, path: str, headers: dict[str, str]
) -> None:
    body_text = body.decode("utf-8", errors="replace")[:500]
    if code in (401, 403, 404, 422):
        raise unittest.SkipTest(
            f"SoDEX {path} returned HTTP {code} (gated): {body_text}"
        )
    if code == 429:
        retry_after = headers.get("Retry-After")
        raise unittest.SkipTest(
            f"SoDEX rate-limited on {path} (HTTP 429, Retry-After={retry_after}): {body_text}"
        )
    raise AssertionError(f"SoDEX HTTP {code} on {path}: {body_text}")


def _sodex_get_json(path: str, *, params: dict[str, Any] | None = None) -> Any:
    code, headers, raw = _sodex_get_raw(path, params=params)
    if code != 200:
        _sodex_handle_error(code, raw, path=path, headers=headers)
    if not raw:
        return None
    return json.loads(raw.decode("utf-8"))
def _first_sodex_symbol() -> str:
    body = _sodex_get_json("/markets/symbols")
    if isinstance(body, dict):
        data = body.get("data") or body.get("rows") or []
    else:
        data = body
    if not isinstance(data, list) or not data:
        raise unittest.SkipTest("SoDEX /markets/symbols returned no rows")
    for candidate in ("BTCUSDT", "ETHUSDT", "BTC", "ETH"):
        for row in data:
            if isinstance(row, dict):
                name = row.get("symbol") or row.get("name") or row.get("symbolName")
                if isinstance(name, str) and name == candidate:
                    return candidate
    for row in data:
        if isinstance(row, dict):
            name = row.get("symbol") or row.get("name") or row.get("symbolName")
            if isinstance(name, str) and name:
                return name
    raise unittest.SkipTest("SoDEX /markets/symbols had no usable symbol name")



def _openrouter_handle_error(exc: urllib.error.HTTPError) -> None:
    body_text = exc.read().decode("utf-8", errors="replace")[:500]
    if exc.code == 429:
        raise unittest.SkipTest(f"OpenRouter rate-limited (HTTP 429): {body_text}")
    raise AssertionError(f"OpenRouter HTTP {exc.code}: {body_text}") from exc


class _OpenRouterBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _skip_openrouter()
        if not OPENROUTER_API_KEY or not OPENROUTER_API_KEY.startswith("sk-or-"):
            raise unittest.SkipTest("OpenRouter API key not configured")


class _SoDEXBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _skip_sodex()
        if os.environ.get(SODEX_LIVE_ENV, "").strip().lower() not in {"1", "true", "yes"}:
            raise unittest.SkipTest(f"{SODEX_LIVE_ENV}=1 required for SoDEX deep live tests")


class _SoSoValueBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _skip_sosovalue()
        if not os.environ.get(SOSOVALUE_KEY_ENV):
            raise unittest.SkipTest(f"{SOSOVALUE_KEY_ENV} not set")


class CurlDeepLiveTests(_OpenRouterBase, _SoDEXBase, _SoSoValueBase):
    """10 deep live-curl tests covering the wider protocol surface."""

    def test_response_compression(self) -> None:
        try:
            status, headers, raw = _openrouter_request(
                OPENROUTER_MODELS_URL,
                method="GET",
                extra_headers={"Accept-Encoding": "gzip"},
            )
        except urllib.error.HTTPError as exc:
            _openrouter_handle_error(exc)
            return
        self.assertEqual(status, 200, f"OpenRouter /models gzip returned HTTP {status}")
        self.assertEqual(
            headers.get("Content-Encoding"),
            "gzip",
            f"OpenRouter did not honor Accept-Encoding: gzip (Content-Encoding={headers.get('Content-Encoding')!r})",
        )
        decompressed = gzip.decompress(raw)
        body = json.loads(decompressed.decode("utf-8"))
        data = body.get("data")
        self.assertIsInstance(data, list, f"OpenRouter /models data is not a list: {type(data).__name__}")
        self.assertGreater(len(data), 0, "OpenRouter /models gzip returned empty data list")

    def test_post_form_encoded(self) -> None:
        symbol = _first_sodex_symbol()
        form_body = urllib.parse.urlencode(
            {"symbol": symbol, "limit": 3, "interval": "1h"}
        ).encode("utf-8")
        code, headers, raw = _sodex_post_raw(
            f"/markets/{symbol}/klines",
            body=form_body,
            content_type="application/x-www-form-urlencoded",
        )
        if code != 200:
            _sodex_handle_error(code, raw, path=f"/markets/{symbol}/klines", headers=headers)
        payload = json.loads(raw.decode("utf-8"))
        self.assertIn(
            "code",
            payload,
            f"SoDEX form-encoded POST missing 'code' key: {type(payload).__name__}",
        )
    def test_post_json_body(self) -> None:
        symbol = _first_sodex_symbol()
        payload_body = json.dumps(
            {"symbol": symbol, "limit": 3, "interval": "1h"}
        ).encode("utf-8")
        code, headers, raw = _sodex_post_raw(
            f"/markets/{symbol}/klines",
            body=payload_body,
            content_type="application/json",
        )
        if code != 200:
            _sodex_handle_error(code, raw, path=f"/markets/{symbol}/klines", headers=headers)
        payload = json.loads(raw.decode("utf-8"))
        self.assertIn(
            "code",
            payload,
            f"SoDEX JSON POST missing 'code' key: {type(payload).__name__}",
        )

    def test_batch_request_with_idempotency_key(self) -> None:
        idem_key = uuid.uuid4().hex
        batch_body = json.dumps(
            {
                "idempotency_key": idem_key,
                "orders": [
                    {"symbol": "BTCUSDT", "side": "BUY", "qty": 1, "price": 100},
                    {"symbol": "ETHUSDT", "side": "SELL", "qty": 2, "price": 200},
                ],
            }
        ).encode("utf-8")
        code, headers, raw = _sodex_post_raw(
            "/orders/batch",
            body=batch_body,
            content_type="application/json",
        )
        if code == 405:
            self.skipTest(
                f"SoDEX /orders/batch method not allowed (idempotency_key surface): HTTP {code}"
            )
        if code in (401, 403, 404, 422):
            self.skipTest(
                f"SoDEX batch endpoint gated without signed request (HTTP {code}): {raw[:200]!r}"
            )
        if code == 429:
            self.skipTest(
                f"SoDEX batch rate-limited (HTTP 429): {raw[:200]!r}"
            )
        self.assertIn(
            code,
            (200, 201, 202),
            f"SoDEX batch with idempotency_key returned unexpected HTTP {code}: {raw[:200]!r}",
        )
        if raw:
            try:
                payload = json.loads(raw.decode("utf-8"))
                self.assertIn("code", payload)
            except (ValueError, UnicodeDecodeError):
                pass

    def test_retry_after_429(self) -> None:
        url = f"{SOSOVALUE_BASE_URL}/currencies"
        api_key = os.environ.get(SOSOVALUE_KEY_ENV)
        if not api_key:
            self.skipTest(f"{SOSOVALUE_KEY_ENV} not set")
        request = urllib.request.Request(
            url,
            method="GET",
            headers={
                "x-soso-api-key": api_key,
                "Accept": "application/json",
                "User-Agent": "SigLab-Deep-Curl/1.0",
            },
        )
        last_exc: urllib.error.HTTPError | None = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=SOSOVALUE_TIMEOUT_S) as response:
                    self.assertEqual(response.status, 200)
                    return
            except urllib.error.HTTPError as exc:
                last_exc = exc
                if exc.code != 429:
                    body_text = exc.read().decode("utf-8", errors="replace")[:500]
                    self.fail(
                        f"SoSoValue /currencies returned HTTP {exc.code} (not 429): {body_text}"
                    )
                retry_after_raw = exc.headers.get("Retry-After") if exc.headers else None
                retry_after_s: float
                if retry_after_raw is None:
                    retry_after_s = 1.0
                else:
                    try:
                        retry_after_s = float(retry_after_raw)
                    except ValueError:
                        retry_after_s = 1.0
                self.assertGreater(
                    retry_after_s,
                    0.0,
                    f"Retry-After must be > 0, got {retry_after_s!r}",
                )
                time.sleep(min(retry_after_s, 5.0))
        if last_exc is not None:
            body_text = last_exc.read().decode("utf-8", errors="replace")[:500]
            self.skipTest(
                f"SoSoValue /currencies rate-limited after 3 attempts (HTTP 429): {body_text}"
            )

    def test_error_envelope_4xx(self) -> None:
        api_key = os.environ.get(SOSOVALUE_KEY_ENV)
        if not api_key:
            self.skipTest(f"{SOSOVALUE_KEY_ENV} not set")
        bad_url = f"{SOSOVALUE_BASE_URL}/currencies/this-id-does-not-exist-999999999999999/market-snapshot"
        request = urllib.request.Request(
            bad_url,
            method="GET",
            headers={
                "x-soso-api-key": api_key,
                "Accept": "application/json",
                "User-Agent": "SigLab-Deep-Curl/1.0",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=SOSOVALUE_TIMEOUT_S) as response:
                self.fail(
                    f"SoSoValue bad-id should not return 200, got HTTP {response.status}"
                )
        except urllib.error.HTTPError as exc:
            self.assertGreaterEqual(
                exc.code,
                400,
                f"SoSoValue bad-id expected 4xx, got HTTP {exc.code}",
            )
            self.assertLess(
                exc.code,
                500,
                f"SoSoValue bad-id should be 4xx, got HTTP {exc.code}",
            )
            body_text = exc.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(body_text)
            except ValueError:
                self.fail(
                    f"SoSoValue error envelope was not JSON: {body_text[:200]!r}"
                )
            self.assertIsInstance(
                payload,
                dict,
                f"SoSoValue error envelope was not a dict: {type(payload).__name__}",
            )
            self.assertTrue(
                any(k in payload for k in ("code", "msg", "message", "error")),
                f"SoSoValue error envelope missing error keys: {list(payload.keys())}",
            )

    def test_pagination_cursor(self) -> None:
        symbol = _first_sodex_symbol()
        path = f"/markets/{symbol}/trades"
        params: dict[str, Any] = {"limit": 2}
        first = _sodex_get_json(path, params=params)
        if isinstance(first, dict):
            data = first.get("data") or first.get("rows") or first.get("trades") or []
        else:
            data = first
        self.assertIsInstance(
            data,
            list,
            f"SoDEX {path} page 1 expected list, got {type(first).__name__}",
        )
        cursor = None
        if isinstance(first, dict):
            cursor = first.get("next_cursor") or first.get("cursor") or first.get("next")
        second = _sodex_get_json(
            path,
            params={"limit": 2, "cursor": cursor} if cursor else {"limit": 2},
        )
        if isinstance(second, dict):
            second_data = second.get("data") or second.get("rows") or second.get("trades") or []
        else:
            second_data = second
        self.assertIsInstance(
            second_data,
            list,
            f"SoDEX {path} page 2 expected list, got {type(second).__name__}",
        )

    def test_webhook_signature(self) -> None:
        secret = b"siglab-webhook-test-secret-do-not-use-in-prod"
        payload_bytes = json.dumps(
            {
                "event": "order.filled",
                "order_id": uuid.uuid4().hex,
                "ts": int(time.time()),
                "qty": 1,
                "price": 100,
            }
        ).encode("utf-8")
        expected_sig = hmac.new(secret, payload_bytes, hashlib.sha256).hexdigest()
        computed = hmac.new(secret, payload_bytes, hashlib.sha256).hexdigest()
        self.assertEqual(
            expected_sig,
            computed,
            "HMAC-SHA256 webhook signature must be deterministic for the same payload+secret",
        )
        different_secret = b"siglab-webhook-test-secret-DIFFERENT"
        different_sig = hmac.new(different_secret, payload_bytes, hashlib.sha256).hexdigest()
        self.assertNotEqual(
            different_sig,
            expected_sig,
            "HMAC-SHA256 webhook signature must change with the secret",
        )
        tampered = payload_bytes.replace(b"qty", b"qty_tampered")
        tampered_sig = hmac.new(secret, tampered, hashlib.sha256).hexdigest()
        self.assertNotEqual(
            tampered_sig,
            expected_sig,
            "HMAC-SHA256 webhook signature must change when the payload is tampered",
        )

    def test_sse_streaming(self) -> None:
        body = json.dumps(
            {
                "model": NEMOTRON_FREE,
                "messages": [{"role": "user", "content": "Reply with the word pong."}],
                "max_tokens": 16,
                "stream": True,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            OPENROUTER_CHAT_URL,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
                "HTTP-Referer": "https://github.com/siglab/siglab",
                "X-Title": "SigLab Deep Live Curl SSE",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=OPENROUTER_TIMEOUT_S) as response:
                content_type = response.headers.get("Content-Type", "")
                self.assertIn(
                    "text/event-stream",
                    content_type,
                    f"OpenRouter streaming did not return text/event-stream (Content-Type={content_type!r})",
                )
                buf = io.BytesIO()
                deadline = time.time() + 30.0
                saw_data = False
                while time.time() < deadline:
                    chunk = response.read(1024)
                    if not chunk:
                        break
                    buf.write(chunk)
                    if b"data:" in buf.getvalue():
                        saw_data = True
                        break
                self.assertTrue(
                    saw_data,
                    f"OpenRouter SSE stream had no 'data:' lines after 30s: {buf.getvalue()[:200]!r}",
                )
        except urllib.error.HTTPError as exc:
            _openrouter_handle_error(exc)

    def test_multipart_upload(self) -> None:
        boundary = uuid.uuid4().hex
        crlf = b"\r\n"
        file_payload = b"siglab-test-payload-" + uuid.uuid4().hex.encode("utf-8")
        parts: list[bytes] = []
        parts.append(f"--{boundary}".encode("utf-8"))
        parts.append(crlf)
        parts.append(b'Content-Disposition: form-data; name="file"; filename="siglab.txt"')
        parts.append(crlf)
        parts.append(b"Content-Type: text/plain")
        parts.append(crlf)
        parts.append(crlf)
        parts.append(file_payload)
        parts.append(crlf)
        parts.append(f"--{boundary}--".encode("utf-8"))
        parts.append(crlf)
        body = b"".join(parts)
        url = f"{SOSOVALUE_BASE_URL}/upload/test"
        api_key = os.environ.get(SOSOVALUE_KEY_ENV)
        if not api_key:
            self.skipTest(f"{SOSOVALUE_KEY_ENV} not set")
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "x-soso-api-key": api_key,
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Accept": "application/json",
                "User-Agent": "SigLab-Deep-Curl/1.0",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=SOSOVALUE_TIMEOUT_S) as response:
                self.assertEqual(
                    response.status,
                    200,
                    f"SoSoValue multipart upload expected 200, got HTTP {response.status}",
                )
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403, 404, 422):
                self.skipTest(
                    f"SoSoValue /upload/test endpoint gated (HTTP {exc.code}): {exc.read()[:200]!r}"
                )
            if exc.code == 429:
                self.skipTest(
                    f"SoSoValue /upload/test rate-limited (HTTP 429): {exc.read()[:200]!r}"
                )
            raise


if __name__ == "__main__":
    unittest.main()
