"""Integration tests: live SoDEX testnet endpoint coverage.

Real urllib calls against the public SoDEX testnet gateway documented at
https://sodex.com/documentation/trading-api/. Five endpoints, all unauthenticated
public read paths (per the testnet docs the public market data is open; faucet
dispenses free testnet USDC). Each test skips cleanly on 401/403/404/422/429
and on transport errors so a Cloudflare block or rate-limit does not fail
the suite. Gated by SODEX_TESTNET_LIVE=1; set SIGLAB_SKIP_SODEX=1 to disable.
"""

from __future__ import annotations

import json
import os
import socket
import ssl
import time
import unittest
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse


ENABLE_ENV_VAR = "SODEX_TESTNET_LIVE"
SKIP_ENV_VAR = "SIGLAB_SKIP_SODEX"

SODEX_TESTNET_REST = "https://testnet-gw.sodex.dev/api/v1"
SODEX_TESTNET_WSS = "wss://testnet-gw.sodex.dev/ws"
SODEX_TESTNET_FAUCET = "https://testnet-gw.sodex.dev/api/v1/faucet"

REST_TIMEOUT_S = 15.0
WSS_TIMEOUT_S = 10.0


def _enabled() -> bool:
    if os.environ.get(SKIP_ENV_VAR, "").strip().lower() in {"1", "true", "yes"}:
        return False
    return os.environ.get(ENABLE_ENV_VAR, "").strip().lower() in {"1", "true", "yes"}


def _http_get(url: str) -> Any:
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"Accept": "application/json", "User-Agent": "SigLab-SoDEX-Testnet/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=REST_TIMEOUT_S) as response:
            raw = response.read()
            if not raw:
                return None
            try:
                return json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return raw[:512].decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")[:500]
        if exc.code in (401, 403, 404, 422):
            raise unittest.SkipTest(
                f"SoDEX {urlparse(url).path} returned HTTP {exc.code} (gated): {body_text}"
            )
        if exc.code == 429:
            raise unittest.SkipTest(
                f"SoDEX rate-limited on {urlparse(url).path} (HTTP 429): {body_text}"
            )
        raise AssertionError(f"SoDEX HTTP {exc.code} on {urlparse(url).path}: {body_text}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise unittest.SkipTest(f"SoDEX transport error on {urlparse(url).path}: {exc}")


def _wss_handshake(url: str) -> dict[str, object]:
    parsed = urlparse(url)
    if parsed.scheme not in ("ws", "wss"):
        raise AssertionError(f"unexpected WSS scheme: {parsed.scheme}")
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "wss" else 80)
    path = parsed.path or "/"

    started = time.perf_counter()
    ctx = ssl.create_default_context()
    with socket.create_connection((host, port), timeout=WSS_TIMEOUT_S) as raw:
        if parsed.scheme == "wss":
            sock = ctx.wrap_socket(raw, server_hostname=host)
        else:
            sock = raw
        try:
            key = "dGhlIHNhbXBsZSBub25jZQ=="
            handshake = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {host}:{port}\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Version: 13\r\n"
                "\r\n"
            )
            sock.sendall(handshake.encode("ascii"))
            sock.settimeout(WSS_TIMEOUT_S)
            response = b""
            while b"\r\n\r\n" not in response:
                chunk = sock.recv(1024)
                if not chunk:
                    break
                response += chunk
            elapsed = time.perf_counter() - started
            status_line = response.split(b"\r\n", 1)[0].decode("latin-1", errors="replace")
            return {
                "status": status_line,
                "elapsed_s": elapsed,
                "raw_head": response[:200].decode("latin-1", errors="replace"),
            }
        finally:
            try:
                sock.close()
            except OSError:
                pass


class SoDEXTestnetLiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not _enabled():
            raise unittest.SkipTest(
                f"set {ENABLE_ENV_VAR}=1 to run live SoDEX testnet tests"
            )

    def test_spot_markets_symbols(self) -> None:
        url = f"{SODEX_TESTNET_REST}/spot/markets/symbols"
        body = _http_get(url)
        self.assertIsNotNone(body, f"empty body on {url}")
        if isinstance(body, dict):
            data = body.get("data")
            self.assertIsNotNone(data, f"spot /markets/symbols missing data: {body!r}")
        else:
            self.assertIsInstance(body, list, f"spot /markets/symbols not a list: {body!r}")

    def test_perps_markets_tickers(self) -> None:
        url = f"{SODEX_TESTNET_REST}/perps/markets/tickers"
        body = _http_get(url)
        self.assertIsNotNone(body, f"empty body on {url}")
        if isinstance(body, dict):
            data = body.get("data")
            self.assertIsNotNone(data, f"perps /markets/tickers missing data: {body!r}")
        else:
            self.assertIsInstance(body, list, f"perps /markets/tickers not a list: {body!r}")

    def test_faucet_reachable(self) -> None:
        request = urllib.request.Request(
            SODEX_TESTNET_FAUCET,
            method="GET",
            headers={"Accept": "application/json", "User-Agent": "SigLab-SoDEX-Testnet/1.0"},
        )
        try:
            with urllib.request.urlopen(request, timeout=REST_TIMEOUT_S) as response:
                status = response.status
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")[:500]
            if exc.code in (401, 403, 404, 422, 405, 400):
                raise unittest.SkipTest(
                    f"faucet returned HTTP {exc.code} (gated/method): {body_text}"
                )
            if exc.code == 429:
                raise unittest.SkipTest(f"faucet rate-limited (HTTP 429): {body_text}")
            raise AssertionError(f"faucet HTTP {exc.code}: {body_text}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise unittest.SkipTest(f"faucet transport error: {exc}")
        self.assertGreaterEqual(
            status, 200, f"faucet returned non-2xx status: {status}"
        )
        self.assertLess(status, 500, f"faucet returned server error: {status}")

    def test_wss_spot_handshake(self) -> None:
        url = f"{SODEX_TESTNET_WSS}/spot"
        try:
            result = _wss_handshake(url)
        except (socket.error, OSError, ssl.SSLError) as exc:
            raise unittest.SkipTest(f"wss /spot transport error: {exc}")
        status = str(result.get("status", ""))
        self.assertIn("101", status, f"wss /spot did not upgrade: {status!r}")

    def test_wss_perps_handshake(self) -> None:
        url = f"{SODEX_TESTNET_WSS}/perps"
        try:
            result = _wss_handshake(url)
        except (socket.error, OSError, ssl.SSLError) as exc:
            raise unittest.SkipTest(f"wss /perps transport error: {exc}")
        status = str(result.get("status", ""))
        self.assertIn("101", status, f"wss /perps did not upgrade: {status!r}")


if __name__ == "__main__":
    unittest.main()
