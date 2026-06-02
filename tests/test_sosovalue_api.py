from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from siglab.data.sosovalue_client import (
    SoSoValueApiError,
    SoSoValueAuthError,
    SoSoValueClient,
    SoSoValueConfigError,
    SoSoValueRateLimitError,
    SoSoValueRequestSpec,
    SoSoValueTransportError,
    SoSoValueUpstreamFormatError,
)
from siglab.data.feeds import MarketDataProvider
from siglab.data.sosovalue_capabilities import capability_matrix


class _FakeResponse:
    def __init__(self, payload: object, *, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> object:
        return self._payload


class SoSoValueClientTests(unittest.IsolatedAsyncioTestCase):
    def _current_metrics_payload(self) -> dict[str, object]:
        metric = {"value": 1.0, "lastUpdateDate": "2026-01-01", "status": 1}
        return {
            "totalNetAssets": dict(metric),
            "totalNetAssetsPercentage": dict(metric),
            "dailyNetInflow": dict(metric),
            "cumNetInflow": dict(metric),
            "dailyTotalValueTraded": dict(metric),
            "totalTokenHoldings": dict(metric),
            "list": [
                {
                    "id": "gbtc",
                    "ticker": "GBTC",
                    "institute": "Grayscale",
                    "netAssets": dict(metric),
                    "netAssetsPercentage": dict(metric),
                    "dailyNetInflow": dict(metric),
                    "cumNetInflow": dict(metric),
                    "dailyValueTraded": dict(metric),
                    "fee": dict(metric),
                    "discountPremiumRate": dict(metric),
                }
            ],
        }

    async def test_client_rejects_missing_api_key(self) -> None:
        client = SoSoValueClient(api_key=None)
        with self.assertRaisesRegex(SoSoValueApiError, "SOSOVALUE_API_KEY is required"):
            await client.etf_historical_inflow()

    async def test_client_parses_etf_inflow_rows(self) -> None:
        http_client = SimpleNamespace(
            request=AsyncMock(
                return_value=_FakeResponse(
                    {
                        "code": 0,
                        "data": {
                            "list": [
                                {
                                    "date": "2026-01-01",
                                    "totalNetInflow": 123.4,
                                    "totalValueTraded": 456.7,
                                    "totalNetAssets": 890.1,
                                    "cumNetInflow": 234.5,
                                }
                            ]
                        },
                    }
                )
            )
        )
        client = SoSoValueClient(api_key="test-key", client=http_client)

        rows = await client.etf_historical_inflow(etf_type="us-btc-spot")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["totalNetInflow"], 123.4)
        call = http_client.request.await_args
        self.assertEqual(call.args[0], "POST")
        self.assertIn("/openapi/v2/etf/historicalInflowChart", call.args[1])

    async def test_client_parses_featured_news_rows(self) -> None:
        http_client = SimpleNamespace(
            request=AsyncMock(
                return_value=_FakeResponse(
                    {
                        "code": 0,
                        "data": {
                            "list": [
                                {
                                    "id": 1,
                                    "title": "fallback title",
                                    "multilanguageContent": [
                                        {"title": "localized title", "content": "localized summary"}
                                    ],
                                    "matchedCurrencies": ["BTC"],
                                }
                            ]
                        },
                    }
                )
            )
        )
        client = SoSoValueClient(api_key="test-key", client=http_client)

        rows = await client.featured_news_by_currency(page_num=2, page_size=5, currency_id=7)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], 1)
        call = http_client.request.await_args
        self.assertEqual(call.args[0], "GET")
        self.assertIn("/api/v1/news/featured/currency", call.args[1])
        self.assertEqual(call.kwargs["params"]["pageNum"], 2)
        self.assertEqual(call.kwargs["params"]["pageSize"], 5)
        self.assertEqual(call.kwargs["params"]["currencyId"], 7)

    async def test_client_fetches_multiple_verified_news_pages(self) -> None:
        http_client = SimpleNamespace(
            request=AsyncMock(
                side_effect=[
                    _FakeResponse({"code": 0, "data": {"list": [{"id": "p1"}]}}),
                    _FakeResponse({"code": 0, "data": {"list": [{"id": "p2"}]}}),
                ]
            )
        )
        client = SoSoValueClient(api_key="test-key", client=http_client, retries=0)

        rows = await client.featured_news_pages(max_pages=2, page_size=100)

        self.assertEqual([row["id"] for row in rows], ["p1", "p2"])
        self.assertEqual(http_client.request.await_count, 2)
        self.assertEqual(http_client.request.await_args_list[0].kwargs["params"]["pageNum"], 1)
        self.assertEqual(http_client.request.await_args_list[1].kwargs["params"]["pageNum"], 2)

    async def test_client_rejects_unofficial_news_page_size(self) -> None:
        client = SoSoValueClient(api_key="test-key", client=SimpleNamespace(request=AsyncMock()), retries=0)

        with self.assertRaises(SoSoValueConfigError):
            await client.featured_news(page_size=101)

    async def test_client_parses_listed_currencies(self) -> None:
        http_client = SimpleNamespace(
            request=AsyncMock(
                return_value=_FakeResponse({"code": 0, "data": [{"id": "1", "fullName": "Bitcoin", "name": "btc"}]})
            )
        )
        client = SoSoValueClient(api_key="test-key", client=http_client)

        rows = await client.listed_currencies()

        self.assertEqual(rows[0]["name"], "btc")
        call = http_client.request.await_args
        self.assertEqual(call.args[0], "POST")
        self.assertIn("/openapi/v1/data/default/coin/list", call.args[1])

    async def test_client_parses_current_etf_metrics_object(self) -> None:
        http_client = SimpleNamespace(
            request=AsyncMock(return_value=_FakeResponse({"code": 0, "data": self._current_metrics_payload()}))
        )
        client = SoSoValueClient(api_key="test-key", client=http_client)

        data = await client.etf_current_metrics()

        self.assertEqual(data["totalNetAssets"]["value"], 1.0)
        self.assertEqual(len(data["list"]), 1)
        call = http_client.request.await_args
        self.assertEqual(call.args[0], "POST")
        self.assertIn("/openapi/v2/etf/currentEtfDataMetrics", call.args[1])

    async def test_client_rejects_current_etf_metrics_missing_aggregate(self) -> None:
        data = self._current_metrics_payload()
        data.pop("totalTokenHoldings")
        http_client = SimpleNamespace(
            request=AsyncMock(return_value=_FakeResponse({"code": 0, "data": data}))
        )
        client = SoSoValueClient(api_key="test-key", client=http_client, retries=0)

        with self.assertRaisesRegex(SoSoValueUpstreamFormatError, "missing aggregate fields"):
            await client.etf_current_metrics()

    async def test_client_rejects_current_etf_metrics_missing_list_field(self) -> None:
        data = self._current_metrics_payload()
        rows = data["list"]
        assert isinstance(rows, list)
        rows[0].pop("ticker")
        http_client = SimpleNamespace(
            request=AsyncMock(return_value=_FakeResponse({"code": 0, "data": data}))
        )
        client = SoSoValueClient(api_key="test-key", client=http_client, retries=0)

        with self.assertRaisesRegex(SoSoValueUpstreamFormatError, "row 0 missing required fields"):
            await client.etf_current_metrics()

    async def test_client_retries_and_surfaces_api_code_errors(self) -> None:
        http_client = SimpleNamespace(
            request=AsyncMock(
                return_value=_FakeResponse({"code": 40001, "msg": "bad request"})
            )
        )
        client = SoSoValueClient(api_key="test-key", client=http_client, retries=0)

        with self.assertRaisesRegex(SoSoValueApiError, "bad request"):
            await client.etf_historical_inflow()

    async def test_client_classifies_rate_limit_without_auth_retry_leak(self) -> None:
        http_client = SimpleNamespace(request=AsyncMock(return_value=_FakeResponse({"code": 0}, status_code=429)))
        client = SoSoValueClient(api_key="test-key", client=http_client, retries=0)

        with self.assertRaises(SoSoValueRateLimitError):
            await client.etf_historical_inflow()

    async def test_client_rejects_missing_required_fields(self) -> None:
        http_client = SimpleNamespace(
            request=AsyncMock(return_value=_FakeResponse({"code": 0, "data": {"list": [{"date": "2026-01-01"}]}}))
        )
        client = SoSoValueClient(api_key="test-key", client=http_client, retries=0)

        with self.assertRaisesRegex(SoSoValueUpstreamFormatError, "missing required fields"):
            await client.etf_historical_inflow()

    async def test_client_caches_and_reports_cache_hits(self) -> None:
        http_client = SimpleNamespace(
            request=AsyncMock(
                return_value=_FakeResponse(
                    {
                        "code": 0,
                        "data": {
                            "list": [
                                {
                                    "date": "2026-01-01",
                                    "totalNetInflow": 123.4,
                                    "totalValueTraded": 456.7,
                                    "totalNetAssets": 890.1,
                                    "cumNetInflow": 234.5,
                                }
                            ]
                        },
                    }
                )
            )
        )
        client = SoSoValueClient(api_key="test-key", client=http_client, retries=0)

        await client.etf_historical_inflow()
        await client.etf_historical_inflow()

        self.assertEqual(http_client.request.await_count, 1)
        metrics = client.metrics_snapshot()
        self.assertGreater(metrics["cache_hit_ratio"], 0)
        self.assertEqual(metrics["rate_limit_policy"]["conservative_calls_per_minute"], 20)
        self.assertTrue(metrics["rate_limit_policy"]["enforced"])

    async def test_client_enforces_process_local_conservative_rate_limit(self) -> None:
        sleeps: list[float] = []
        original_sleep = asyncio.sleep

        async def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)
            client._rate_limit_events.clear()

        http_client = SimpleNamespace(
            request=AsyncMock(
                return_value=_FakeResponse(
                    {
                        "code": 0,
                        "data": {
                            "list": [
                                {
                                    "date": "2026-01-01",
                                    "totalNetInflow": 1,
                                    "totalValueTraded": 1,
                                    "totalNetAssets": 1,
                                    "cumNetInflow": 1,
                                }
                            ]
                        },
                    }
                )
            )
        )
        client = SoSoValueClient(
            api_key="test-key",
            client=http_client,
            retries=0,
            conservative_rate_limit_per_minute=1,
        )
        try:
            asyncio.sleep = fake_sleep  # type: ignore[assignment]
            await client.request(
                SoSoValueRequestSpec("first", "POST", "https://example.test", "/first", json_body={})
            )
            await client.request(
                SoSoValueRequestSpec("second", "POST", "https://example.test", "/second", json_body={})
            )
        finally:
            asyncio.sleep = original_sleep  # type: ignore[assignment]

        self.assertEqual(http_client.request.await_count, 2)
        self.assertTrue(sleeps)

    async def test_client_preserves_transport_failure_classification(self) -> None:
        http_client = SimpleNamespace(request=AsyncMock(side_effect=TimeoutError("boom")))
        client = SoSoValueClient(api_key="test-key", client=http_client, retries=0)

        with self.assertRaises(SoSoValueTransportError):
            await client.etf_historical_inflow()

    async def test_client_raises_auth_error_on_401(self) -> None:
        """401 response raises SoSoValueAuthError (VAL-DATA-012)."""
        http_client = SimpleNamespace(
            request=AsyncMock(return_value=_FakeResponse({"code": 0}, status_code=401))
        )
        client = SoSoValueClient(api_key="test-key", client=http_client, retries=0)

        with self.assertRaises(SoSoValueAuthError):
            await client.etf_historical_inflow()

    async def test_client_retries_on_5xx(self) -> None:
        """5xx response triggers retry up to configured limit (VAL-DATA-019)."""
        http_client = SimpleNamespace(
            request=AsyncMock(return_value=_FakeResponse({"code": 0}, status_code=502))
        )
        client = SoSoValueClient(api_key="test-key", client=http_client, retries=2)

        with self.assertRaises(SoSoValueApiError):
            await client.etf_historical_inflow()

        self.assertEqual(http_client.request.await_count, 3)  # initial + 2 retries

    async def test_client_raises_upstream_format_on_invalid_envelope(self) -> None:
        """Response with non-zero code field raises SoSoValueUpstreamFormatError (VAL-DATA-020)."""
        http_client = SimpleNamespace(
            request=AsyncMock(
                return_value=_FakeResponse({"code": 40001, "msg": "bad request", "data": []})
            )
        )
        client = SoSoValueClient(api_key="test-key", client=http_client, retries=0)

        with self.assertRaises(SoSoValueUpstreamFormatError):
            await client.etf_historical_inflow()

    async def test_client_raises_upstream_format_on_missing_code_field(self) -> None:
        """Response without code field raises SoSoValueUpstreamFormatError when data is unexpected type."""
        http_client = SimpleNamespace(
            request=AsyncMock(
                return_value=_FakeResponse({"data": "not_an_object_or_list"})
            )
        )
        client = SoSoValueClient(api_key="test-key", client=http_client, retries=0)

        with self.assertRaises(SoSoValueUpstreamFormatError):
            await client.etf_historical_inflow()

    async def test_endpoint_metrics_updated_after_successful_call(self) -> None:
        """Successful request updates endpoint metrics (VAL-DATA-018)."""
        http_client = SimpleNamespace(
            request=AsyncMock(
                return_value=_FakeResponse(
                    {
                        "code": 0,
                        "data": {
                            "list": [
                                {
                                    "date": "2026-01-01",
                                    "totalNetInflow": 123.4,
                                    "totalValueTraded": 456.7,
                                    "totalNetAssets": 890.1,
                                    "cumNetInflow": 234.5,
                                }
                            ]
                        },
                    }
                )
            )
        )
        client = SoSoValueClient(api_key="test-key", client=http_client, retries=0)

        await client.etf_historical_inflow()

        metrics = client.metrics_snapshot()
        self.assertIn("endpoints", metrics)
        self.assertIn("etf.historical_inflow", metrics["endpoints"])
        ep = metrics["endpoints"]["etf.historical_inflow"]
        self.assertGreater(ep["attempts"], 0)
        self.assertGreater(ep["successes"], 0)
        self.assertIsNotNone(ep["p50_ms"])

    async def test_currency_market_snapshot_parses_object(self) -> None:
        http_client = SimpleNamespace(
            request=AsyncMock(
                return_value=_FakeResponse(
                    {
                        "code": 0,
                        "data": {
                            "price": 458.0,
                            "change_pct_24h": -0.12,
                            "marketcap": 98187284636.4,
                            "marketcap_rank": 4,
                        },
                    }
                )
            )
        )
        client = SoSoValueClient(api_key="test-key", client=http_client, retries=0)

        snapshot = await client.currency_market_snapshot("bitcoin")

        self.assertEqual(snapshot["price"], 458.0)
        self.assertEqual(snapshot["marketcap_rank"], 4)
        call = http_client.request.await_args
        self.assertEqual(call.args[0], "GET")
        self.assertIn("/currencies/bitcoin/market-snapshot", call.args[1])

    async def test_currency_market_snapshot_rejects_non_object_data(self) -> None:
        http_client = SimpleNamespace(
            request=AsyncMock(return_value=_FakeResponse({"code": 0, "data": []}))
        )
        client = SoSoValueClient(api_key="test-key", client=http_client, retries=0)

        with self.assertRaises(SoSoValueUpstreamFormatError):
            await client.currency_market_snapshot("bitcoin")

    async def test_currency_klines_returns_rows(self) -> None:
        http_client = SimpleNamespace(
            request=AsyncMock(
                return_value=_FakeResponse(
                    {
                        "code": 0,
                        "data": [
                            {
                                "timestamp": 1710000000000,
                                "open": 123,
                                "high": 130,
                                "low": 120,
                                "close": 125,
                                "volume": 100000,
                            }
                        ],
                    }
                )
            )
        )
        client = SoSoValueClient(api_key="test-key", client=http_client, retries=0)

        rows = await client.currency_klines("bitcoin", limit=10)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["close"], 125)
        call = http_client.request.await_args
        self.assertEqual(call.args[0], "GET")
        self.assertIn("/currencies/bitcoin/klines", call.args[1])
        self.assertEqual(call.kwargs["params"]["interval"], "1d")
        self.assertEqual(call.kwargs["params"]["limit"], 10)

    async def test_currency_klines_rejects_invalid_interval(self) -> None:
        client = SoSoValueClient(api_key="test-key", retries=0)

        with self.assertRaises(SoSoValueConfigError):
            await client.currency_klines("bitcoin", interval="1h")

    async def test_etf_list_returns_rows(self) -> None:
        http_client = SimpleNamespace(
            request=AsyncMock(
                return_value=_FakeResponse(
                    {
                        "code": 0,
                        "data": [
                            {"ticker": "IBIT", "name": "iShares Bitcoin Trust", "exchange": "NYSE"}
                        ],
                    }
                )
            )
        )
        client = SoSoValueClient(api_key="test-key", client=http_client, retries=0)

        rows = await client.etf_list(symbol="BTC", country_code="US")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ticker"], "IBIT")
        call = http_client.request.await_args
        self.assertEqual(call.args[0], "GET")
        self.assertIn("/etfs", call.args[1])
        self.assertEqual(call.kwargs["params"]["symbol"], "BTC")
        self.assertEqual(call.kwargs["params"]["country_code"], "US")

    async def test_etf_summary_history_returns_rows(self) -> None:
        http_client = SimpleNamespace(
            request=AsyncMock(
                return_value=_FakeResponse(
                    {
                        "code": 0,
                        "data": [
                            {
                                "date": "2024-04-12",
                                "total_net_inflow": -55066297.0,
                                "total_value_traded": 4706120449.0,
                                "total_net_assets": 56216535367.0,
                                "cum_net_inflow": 13534833596.095,
                            }
                        ],
                    }
                )
            )
        )
        client = SoSoValueClient(api_key="test-key", client=http_client, retries=0)

        rows = await client.etf_summary_history(symbol="BTC", country_code="US")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["total_net_inflow"], -55066297.0)
        call = http_client.request.await_args
        self.assertEqual(call.args[0], "GET")
        self.assertIn("/etfs/summary-history", call.args[1])

    async def test_etf_summary_history_validates_required_fields(self) -> None:
        http_client = SimpleNamespace(
            request=AsyncMock(
                return_value=_FakeResponse({"code": 0, "data": [{"date": "2024-04-12"}]})
            )
        )
        client = SoSoValueClient(api_key="test-key", client=http_client, retries=0)

        with self.assertRaises(SoSoValueUpstreamFormatError):
            await client.etf_summary_history(symbol="BTC", country_code="US")

    async def test_etf_market_snapshot_parses_object(self) -> None:
        http_client = SimpleNamespace(
            request=AsyncMock(
                return_value=_FakeResponse(
                    {
                        "code": 0,
                        "data": {
                            "date": "2024-04-12",
                            "ticker": "IBIT",
                            "net_inflow": 3000000,
                            "net_assets": 5000000,
                        },
                    }
                )
            )
        )
        client = SoSoValueClient(api_key="test-key", client=http_client, retries=0)

        snapshot = await client.etf_market_snapshot("IBIT")

        self.assertEqual(snapshot["ticker"], "IBIT")
        self.assertEqual(snapshot["net_inflow"], 3000000)
        call = http_client.request.await_args
        self.assertEqual(call.args[0], "GET")
        self.assertIn("/etfs/IBIT/market-snapshot", call.args[1])


class MarketDataGapAndCapabilityTests(unittest.IsolatedAsyncioTestCase):
    def _provider(self) -> MarketDataProvider:
        provider = object.__new__(MarketDataProvider)
        provider.sosovalue = SimpleNamespace(
            etf_historical_inflow=AsyncMock(),
            featured_news_by_currency=AsyncMock(),
        )
        provider.lake = SimpleNamespace(
            latest_json=lambda *args, **kwargs: None,
            write_json=lambda *args, **kwargs: None,
            write_frame=lambda *args, **kwargs: None,
        )
        provider._active_bundle_id = None
        provider._active_as_of = None
        provider._bundle_cache = {}
        provider._warm_cache = {}
        provider._bundle_components = []
        provider._bundle_manifest = {}
        provider._persist_bundle_frames = lambda *args, **kwargs: None
        return provider

    async def test_fetch_etf_historical_inflow_respects_gap_and_cache(self) -> None:
        provider = self._provider()
        provider.sosovalue.etf_historical_inflow.return_value = []

        rows = await provider.fetch_etf_historical_inflow(etf_type="us-btc-spot")

        self.assertEqual(rows, [])
        self.assertEqual(provider.sosovalue.etf_historical_inflow.await_count, 1)

    async def test_fetch_featured_news_normalizes_content(self) -> None:
        provider = self._provider()
        provider.sosovalue.featured_news_by_currency.return_value = [
            {
                "id": 9,
                "title": "raw",
                "multilanguageContent": [{"title": "localized", "content": "summary"}],
                "sourceLink": "https://example.com",
                "releaseTime": "2026-01-01T00:00:00Z",
                "category": "news",
                "tags": ["macro"],
                "matchedCurrencies": ["ETH"],
            }
        ]

        rows = await provider.fetch_featured_news(page_num=1, page_size=1, currency_id=3)

        self.assertEqual(rows[0]["title"], "localized")
        self.assertEqual(rows[0]["summary"], "summary")
        self.assertEqual(rows[0]["matched_currencies"], ["ETH"])
        self.assertEqual(provider.sosovalue.featured_news_by_currency.await_count, 1)

    def test_capability_matrix_has_no_unclassified_surface(self) -> None:
        rows = capability_matrix()
        modules = {row["doc_module"] for row in rows}

        for expected in {
            "Currency & Pairs",
            "ETF",
            "Feeds",
            "SoSoValue Index",
            "Crypto Stocks",
            "BTC Treasuries",
            "Fundraising",
            "Macro",
            "Analysis Charts",
        }:
            self.assertIn(expected, modules)
        for row in rows:
            self.assertIn(row["status"], {"IMPLEMENTED", "PARTIAL", "BLOCKED", "REJECTED"})
            self.assertTrue(row["reason"])


if __name__ == "__main__":
    unittest.main()
