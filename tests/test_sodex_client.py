from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from siglab.live.sodex_client import SoDEXFormatError, SoDEXPublicPerpsClient, SoDEXRateLimitError
from siglab.data.sodex_rate_limit import SoDEXWeightScheduler


class _Response:
    def __init__(self, payload: object, *, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> object:
        return self._payload


class SoDEXPublicPerpsClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_symbols_validates_envelope_and_rows(self) -> None:
        http = SimpleNamespace(
            request=AsyncMock(return_value=_Response({"code": 0, "timestamp": 1, "data": [{"symbol": "BTC-USD"}]}))
        )
        client = SoDEXPublicPerpsClient(client=http)

        rows = await client.symbols(symbol="BTC-USD")

        self.assertEqual(rows[0]["symbol"], "BTC-USD")
        call = http.request.await_args
        self.assertEqual(call.args[0], "GET")
        self.assertIn("/markets/symbols", call.args[1])
        self.assertEqual(call.kwargs["headers"]["Accept"], "application/json")

    async def test_missing_envelope_is_format_error(self) -> None:
        http = SimpleNamespace(request=AsyncMock(return_value=_Response({"data": []})))
        client = SoDEXPublicPerpsClient(client=http)

        with self.assertRaises(SoDEXFormatError):
            await client.symbols()

    async def test_rate_limit_is_classified(self) -> None:
        http = SimpleNamespace(request=AsyncMock(return_value=_Response({"code": 1, "timestamp": 1}, status_code=429)))
        client = SoDEXPublicPerpsClient(client=http, retries=0)

        with self.assertRaises(SoDEXRateLimitError):
            await client.symbols()

    async def test_public_market_endpoints_use_documented_weights(self) -> None:
        http = SimpleNamespace(
            request=AsyncMock(return_value=_Response({"code": 0, "timestamp": 1, "data": []}))
        )
        scheduler = SoDEXWeightScheduler(budget=1200)
        client = SoDEXPublicPerpsClient(client=http, weight_scheduler=scheduler)

        await client.symbols()
        await client.coins()
        await client.klines(symbol="BTC-USD", interval="1m", limit=10)

        self.assertEqual(scheduler.snapshot()["used_weight"], 24)

    async def test_expanded_public_market_endpoints_build_documented_paths(self) -> None:
        http = SimpleNamespace(
            request=AsyncMock(return_value=_Response({"code": 0, "timestamp": 1, "data": []}))
        )
        client = SoDEXPublicPerpsClient(client=http)

        await client.tickers(symbol="BTC-USD")
        await client.mini_tickers()
        await client.mark_prices()
        await client.book_tickers(symbol="BTC-USD")
        await client.trades(symbol="BTC-USD", limit=5)

        paths = [call.args[1] for call in http.request.await_args_list]
        self.assertTrue(any(path.endswith("/markets/tickers") for path in paths))
        self.assertTrue(any(path.endswith("/markets/miniTickers") for path in paths))
        self.assertTrue(any(path.endswith("/markets/mark-prices") for path in paths))
        self.assertTrue(any(path.endswith("/markets/bookTickers") for path in paths))
        self.assertTrue(any(path.endswith("/markets/BTC-USD/trades") for path in paths))

    async def test_orderbook_requires_object_data(self) -> None:
        http = SimpleNamespace(
            request=AsyncMock(return_value=_Response({"code": 0, "timestamp": 1, "data": {"bids": [], "asks": []}}))
        )
        client = SoDEXPublicPerpsClient(client=http)

        data = await client.orderbook(symbol="BTC-USD", limit=10)

        self.assertEqual(data, {"bids": [], "asks": []})
        call = http.request.await_args
        self.assertTrue(call.args[1].endswith("/markets/BTC-USD/orderbook"))

    async def test_account_read_endpoints_validate_address_and_shape(self) -> None:
        http = SimpleNamespace(
            request=AsyncMock(return_value=_Response({"code": 0, "timestamp": 1, "data": {"balances": []}}))
        )
        client = SoDEXPublicPerpsClient(client=http)
        address = "0x" + "1" * 40

        data = await client.account_balances(user_address=address, account_id=7)

        self.assertEqual(data, {"balances": []})
        call = http.request.await_args
        self.assertTrue(call.args[1].endswith(f"/accounts/{address}/balances"))
        self.assertEqual(call.kwargs["params"], {"accountID": 7})
        with self.assertRaises(SoDEXFormatError):
            await client.account_state(user_address="not-an-address")

    async def test_account_orders_allows_list_or_object_data(self) -> None:
        http = SimpleNamespace(
            request=AsyncMock(return_value=_Response({"code": 0, "timestamp": 1, "data": []}))
        )
        client = SoDEXPublicPerpsClient(client=http)

        payload = await client.account_orders(user_address="0x" + "2" * 40, symbol="BTC-USD")

        self.assertEqual(payload, {"data": []})
        self.assertEqual(http.request.await_args.kwargs["params"], {"symbol": "BTC-USD"})


if __name__ == "__main__":
    unittest.main()
