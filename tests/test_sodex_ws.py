from __future__ import annotations

import asyncio
import json
import unittest

from siglab.live.sodex_ws import (
    SoDEXWebSocketClient,
    SoDEXWebSocketConfigError,
    SoDEXWebSocketDisconnected,
    SoDEXWebSocketFormatError,
    SoDEXWebSocketTimeoutError,
)


class FakeWebSocket:
    def __init__(self, replies: list[object]) -> None:
        self.replies = list(replies)
        self.sent: list[str] = []
        self.closed = False

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self) -> str:
        if not self.replies:
            await asyncio.sleep(10)
        item = self.replies.pop(0)
        if item == "__sleep__":
            await asyncio.sleep(10)
        if isinstance(item, Exception):
            raise item
        return str(item)

    async def close(self) -> None:
        self.closed = True


class SoDEXWebSocketClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_subscribe_validates_ack_and_records_metrics(self) -> None:
        ws = FakeWebSocket(
            [
                json.dumps(
                    {
                        "op": "subscribe",
                        "result": {"channel": "accountEvent", "user": "0x" + "1" * 40, "accountID": 1},
                        "success": True,
                        "connID": "0xabc",
                        "error": None,
                    }
                )
            ]
        )
        client = SoDEXWebSocketClient(connect=lambda _url: _return(ws))

        ack = await client.subscribe({"channel": "accountEvent", "user": "0x" + "1" * 40}, request_id=7)

        self.assertTrue(ack["success"])
        self.assertEqual(json.loads(ws.sent[0])["id"], 7)
        self.assertEqual(client.snapshot()["metrics"]["subscriptions"], 1)

    async def test_rejects_unknown_channel_before_network(self) -> None:
        client = SoDEXWebSocketClient(connect=lambda _url: _return(FakeWebSocket([])))

        with self.assertRaises(SoDEXWebSocketConfigError):
            await client.subscribe({"channel": "fake"})

    async def test_account_channels_require_user_address_before_network(self) -> None:
        client = SoDEXWebSocketClient(connect=lambda _url: _return(FakeWebSocket([])))

        with self.assertRaisesRegex(SoDEXWebSocketConfigError, "requires user"):
            await client.subscribe({"channel": "accountEvent"})
        with self.assertRaisesRegex(SoDEXWebSocketConfigError, "EVM address"):
            await client.subscribe({"channel": "accountEvent", "user": "not-an-address"})
        with self.assertRaisesRegex(SoDEXWebSocketConfigError, "accountID"):
            await client.subscribe({"channel": "accountEvent", "user": "0x" + "1" * 40, "accountID": -1})

    async def test_symbol_channels_require_symbol_before_network(self) -> None:
        client = SoDEXWebSocketClient(connect=lambda _url: _return(FakeWebSocket([])))

        with self.assertRaisesRegex(SoDEXWebSocketConfigError, "requires symbol"):
            await client.subscribe({"channel": "bookTicker"})

    async def test_bad_ack_shape_is_format_error(self) -> None:
        ws = FakeWebSocket([json.dumps({"op": "subscribe", "success": False, "error": "bad"})])
        client = SoDEXWebSocketClient(connect=lambda _url: _return(ws))

        with self.assertRaises(SoDEXWebSocketFormatError):
            await client.subscribe({"channel": "allBookTicker"})

    async def test_ping_pong_and_idle_timeout_keepalive(self) -> None:
        ws = FakeWebSocket(["__sleep__", json.dumps({"op": "pong"})])
        client = SoDEXWebSocketClient(connect=lambda _url: _return(ws), idle_timeout_s=0.001, pong_timeout_s=1.0)

        payload = await client.keepalive_once()

        self.assertEqual(payload, {"op": "pong"})
        self.assertEqual(json.loads(ws.sent[0]), {"op": "ping"})
        self.assertEqual(client.snapshot()["metrics"]["pongs"], 1)

    async def test_malformed_json_and_disconnect_are_classified(self) -> None:
        bad = SoDEXWebSocketClient(connect=lambda _url: _return(FakeWebSocket(["not-json"])))
        with self.assertRaises(SoDEXWebSocketFormatError):
            await bad.recv_update(timeout_s=1.0)

        disconnected = SoDEXWebSocketClient(connect=lambda _url: _return(FakeWebSocket([RuntimeError("closed")])))
        with self.assertRaises(SoDEXWebSocketDisconnected):
            await disconnected.recv_update(timeout_s=1.0)

    async def test_timeout_is_classified(self) -> None:
        client = SoDEXWebSocketClient(connect=lambda _url: _return(FakeWebSocket([])))

        with self.assertRaises(SoDEXWebSocketTimeoutError):
            await client.recv_update(timeout_s=0.001)

    async def test_reconnect_budget_is_enforced(self) -> None:
        calls = 0

        async def connect(_url: str) -> FakeWebSocket:
            nonlocal calls
            calls += 1
            return FakeWebSocket([])

        client = SoDEXWebSocketClient(connect=connect, max_reconnects=1)
        await client.connect()
        await client.reconnect()
        with self.assertRaises(SoDEXWebSocketDisconnected):
            await client.reconnect()
        self.assertEqual(calls, 2)


async def _return(value: FakeWebSocket) -> FakeWebSocket:
    return value


if __name__ == "__main__":
    unittest.main()
