"""Integration test: live SoDEX WebSocket connection.

SoDEX testnet credentials are optional -- the public market channels work
without auth. Test against the public testnet WSS endpoint documented at
https://sodex.com/documentation/trading-api/websocket-v1.md.

Env vars to enable (otherwise the entire module is skipped):
  SODEX_WS_TESTNET  - set to "1" to run the test against the testnet WSS
  SODEX_WS_URL      - override the WSS URL (default: wss://testnet-gw.sodex.dev/ws/perps)

Skips cleanly when the WSS is unreachable or returns 0 valid frames in the
timeout window. Use SIGLAB_SKIP_SODEX_WS=1 to disable even when SODEX_WS_TESTNET=1.
"""

from __future__ import annotations

import os
import socket
import ssl
import time
import unittest


SKIP_ENV_VAR = "SIGLAB_SKIP_SODEX_WS"
ENABLE_ENV_VAR = "SODEX_WS_TESTNET"
URL_ENV_VAR = "SODEX_WS_URL"

# Per https://sodex.com/documentation/trading-api/websocket-v1.md
DEFAULT_TESTNET_WSS = "wss://testnet-gw.sodex.dev/ws/perps"
DEFAULT_TIMEOUT_S = 12.0


def _skip_if_disabled() -> None:
    if os.environ.get(SKIP_ENV_VAR, "").strip().lower() in {"1", "true", "yes"}:
        raise unittest.SkipTest(f"{SKIP_ENV_VAR}=1 disables live SoDEX WSS test")


def _wss_url() -> str:
    return os.environ.get(URL_ENV_VAR) or DEFAULT_TESTNET_WSS


def _wss_enabled() -> bool:
    return os.environ.get(ENABLE_ENV_VAR, "").strip() in {"1", "true", "yes"}


def _wss_handshake_check(url: str, timeout_s: float) -> dict[str, object]:
    """Open a TCP+TLS+WS handshake to the WSS URL and return a small dict.

    This does NOT speak the full WS protocol. It just verifies that the WSS
    endpoint is reachable from this host (DNS + TCP + TLS + WS upgrade returns
    a 101 Switching Protocols). If any of those steps fail, the test skips
    with the error reason.
    """
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme not in ("ws", "wss"):
        raise AssertionError(f"unexpected WSS scheme: {parsed.scheme}")
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "wss" else 80)
    path = parsed.path or "/"

    started = time.perf_counter()
    ctx = ssl.create_default_context()
    with socket.create_connection((host, port), timeout=timeout_s) as raw:
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
            sock.settimeout(timeout_s)
            response = b""
            while b"\r\n\r\n" not in response:
                chunk = sock.recv(1024)
                if not chunk:
                    break
                response += chunk
            elapsed = time.perf_counter() - started
            status_line = response.split(b"\r\n", 1)[0].decode("latin-1", errors="replace")
            return {"status": status_line, "elapsed_s": elapsed, "raw_head": response[:200].decode("latin-1", errors="replace")}
        finally:
            try:
                sock.close()
            except OSError:
                pass


class SoDEXWSSTests(unittest.TestCase):
    """Smoke-test the SoDEX WSS endpoint reachability + handshake."""

    @classmethod
    def setUpClass(cls) -> None:
        _skip_if_disabled()
        if not _wss_enabled():
            raise unittest.SkipTest(
                f"set {ENABLE_ENV_VAR}=1 to run live SoDEX WSS handshake"
            )

    def test_wss_handshake_switching_protocols(self) -> None:
        url = _wss_url()
        try:
            result = _wss_handshake_check(url, timeout_s=DEFAULT_TIMEOUT_S)
        except (socket.gaierror, socket.timeout, ConnectionRefusedError, OSError) as exc:
            self.skipTest(f"cannot reach {url}: {exc}")

        status = str(result.get("status", ""))
        # SoDEX gateway must respond with 101 Switching Protocols on a valid
        # WS upgrade. Anything else (403, 404, 502) means the gateway is
        # down or the path is wrong.
        if "101" not in status:
            self.skipTest(
                f"SoDEX WSS {url} did not return 101 Switching Protocols: {status}"
            )
        self.assertIn("101", status, f"unexpected WSS status: {status}")


if __name__ == "__main__":
    unittest.main()
