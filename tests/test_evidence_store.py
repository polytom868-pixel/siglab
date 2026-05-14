from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from siglab.data.evidence import (
    EvidenceRecord,
    EvidenceStore,
    etf_inflow_evidence,
    link_feed_events_to_etf_flows,
    news_evidence,
    sodex_ws_evidence,
    summarize_evidence,
)


class EvidenceStoreTests(unittest.TestCase):
    def test_store_appends_and_queries_source_backed_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EvidenceStore(Path(tmp) / "evidence.jsonl")
            count = store.append_many(
                [
                    EvidenceRecord(
                        source="sosovalue.etf_historical_inflow",
                        observed_at="2026-05-13T20:00:00+01:00",
                        timestamp="2026-05-12",
                        entity="us-btc-spot",
                        module="ETF",
                        relation="total_net_inflow",
                        value="100",
                        confidence=1.2,
                        evidence_path="sosovalue/etf/us-btc-spot.json",
                    )
                ]
            )

            rows = store.query(entity="btc", module="ETF", relation="total_net_inflow")

            self.assertEqual(count, 1)
            self.assertEqual(rows[0]["entity"], "us-btc-spot")
            self.assertEqual(rows[0]["confidence"], 1.0)
            self.assertEqual(rows[0]["evidence_path"], "sosovalue/etf/us-btc-spot.json")
            self.assertTrue(str(rows[0]["evidence_id"]).startswith("ev_"))

    def test_store_dedupes_repeated_records_across_loop_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EvidenceStore(Path(tmp) / "evidence.jsonl")
            record = EvidenceRecord(
                source="sosovalue.etf_historical_inflow",
                observed_at="2026-05-13T20:00:00+01:00",
                timestamp="2026-05-12",
                entity="us-btc-spot",
                module="ETF",
                relation="total_net_inflow",
                value="100",
                confidence=0.95,
                evidence_path="sosovalue/etf/us-btc-spot.json",
            )

            first_count = store.append_many([record, record])
            second_count = store.append_many([record])

            self.assertEqual(first_count, 1)
            self.assertEqual(second_count, 0)
            self.assertEqual(len(store.load()), 1)
            self.assertEqual(store.last_append_stats["records_seen"], 1)
            self.assertEqual(store.last_append_stats["duplicates_skipped"], 1)

    def test_etf_inflow_rows_expand_into_typed_evidence(self) -> None:
        records = etf_inflow_evidence(
            [
                {
                    "date": "2026-05-12",
                    "totalNetInflow": "10",
                    "totalValueTraded": "200",
                    "totalNetAssets": "300",
                    "cumNetInflow": "400",
                }
            ],
            etf_type="us-btc-spot",
            observed_at="2026-05-13T20:00:00+01:00",
            evidence_path="cache/etf.json",
        )

        self.assertEqual([record.relation for record in records], ["total_net_inflow", "total_net_assets"])
        self.assertEqual(records[0].module, "ETF")
        self.assertEqual(records[0].attributes["totalValueTraded"], "200")

    def test_news_rows_expand_into_event_evidence(self) -> None:
        records = news_evidence(
            [
                {
                    "title": "Bitcoin ETF flows rebound",
                    "currencySymbol": "BTC",
                    "publishTime": "2026-05-12T00:00:00Z",
                    "url": "https://example.test/story",
                }
            ],
            observed_at="2026-05-13T20:00:00+01:00",
            evidence_path="cache/news.json",
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].entity, "BTC")
        self.assertEqual(records[0].module, "Feeds")
        self.assertEqual(records[0].relation, "news_mention")
        self.assertEqual(records[0].attributes["url"], "https://example.test/story")

    def test_news_rows_use_sosovalue_multilingual_content_shape(self) -> None:
        records = news_evidence(
            [
                {
                    "id": "story-1",
                    "author": "Odaily",
                    "category": 1,
                    "releaseTime": 1778698863000,
                    "sourceLink": "https://example.test/source",
                    "matchedCurrencies": [{"symbol": "BTC"}],
                    "multilanguageContent": [
                        {"language": "zh-cn", "title": "中文", "content": "zh"},
                        {"language": "en", "title": "Fed chair vote affects macro risk", "content": "macro text"},
                    ],
                }
            ],
            observed_at="2026-05-13T20:00:00+01:00",
            evidence_path="cache/news.json",
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].entity, "BTC")
        self.assertEqual(records[0].timestamp, "1778698863000")
        self.assertEqual(records[0].value, "Fed chair vote affects macro risk")
        self.assertEqual(records[0].attributes["summary"], "macro text")
        self.assertEqual(records[0].attributes["author"], "Odaily")
        self.assertEqual(records[0].attributes["url"], "https://example.test/source")

    def test_news_rows_can_preserve_currency_filter_context_when_matches_are_empty(self) -> None:
        records = news_evidence(
            [
                {
                    "matchedCurrencies": [],
                    "multilanguageContent": [{"language": "en", "title": "BTC news", "content": "body"}],
                }
            ],
            observed_at="2026-05-13T20:00:00+01:00",
            evidence_path="cache/news.json",
            default_entity="BTC",
            source="sosovalue.featured_news_by_currency",
        )

        self.assertEqual(records[0].entity, "BTC")
        self.assertEqual(records[0].source, "sosovalue.featured_news_by_currency")

    def test_news_rows_use_content_when_title_is_null(self) -> None:
        records = news_evidence(
            [
                {
                    "matchedCurrencies": [{"name": "BTC"}],
                    "multilanguageContent": [{"language": "en", "title": None, "content": "5 BTC recovered"}],
                }
            ],
            observed_at="2026-05-13T20:00:00+01:00",
            evidence_path="cache/news.json",
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].value, "5 BTC recovered")

    def test_news_rows_prefer_requested_entity_in_matched_currencies(self) -> None:
        records = news_evidence(
            [
                {
                    "matchedCurrencies": [{"name": "MAG7.SSI"}, {"name": "BTC"}],
                    "multilanguageContent": [{"language": "en", "content": "BTC related event"}],
                }
            ],
            observed_at="2026-05-13T20:00:00+01:00",
            evidence_path="cache/news.json",
            default_entity="BTC",
        )

        self.assertEqual(records[0].entity, "BTC")

    def test_links_feed_events_to_nearby_etf_flow_without_claiming_causality(self) -> None:
        rows = [
            {
                "module": "ETF",
                "relation": "total_net_inflow",
                "entity": "us-btc-spot",
                "timestamp": "2026-05-12",
                "value": "100",
                "evidence_path": "cache/etf.json",
            },
            {
                "module": "Feeds",
                "relation": "news_mention",
                "entity": "BTC",
                "timestamp": "1778544000000",
                "value": "ETF flows rebound",
                "evidence_path": "cache/news.json",
            },
        ]

        links = link_feed_events_to_etf_flows(rows, max_day_gap=1)

        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["relation"], "feed_event_near_etf_flow")
        self.assertEqual(links[0]["confidence"], 0.65)
        self.assertIn("not causal", links[0]["warning"])

    def test_summary_compacts_records_and_links_for_loop_context(self) -> None:
        rows = [
            {
                "source": "sosovalue.etf_historical_inflow",
                "module": "ETF",
                "relation": "total_net_inflow",
                "entity": "us-btc-spot",
                "timestamp": "2026-05-12",
                "value": "100",
                "evidence_path": "cache/etf.json",
            },
            {
                "source": "sosovalue.featured_news_by_currency",
                "module": "Feeds",
                "relation": "news_mention",
                "entity": "BTC",
                "timestamp": "1778544000000",
                "value": "ETF flows rebound",
                "evidence_path": "cache/news.json",
            },
        ]
        links = link_feed_events_to_etf_flows(rows, max_day_gap=1)

        summary = summarize_evidence(rows, links, top_links=1)

        self.assertEqual(summary["record_count"], 2)
        self.assertEqual(summary["link_count"], 1)
        self.assertEqual(summary["module_counts"], {"ETF": 1, "Feeds": 1})
        self.assertEqual(summary["source_counts"]["sosovalue.featured_news_by_currency"], 1)
        self.assertEqual(summary["top_links"][0]["relation"], "feed_event_near_etf_flow")
        self.assertIn("not causal", summary["top_links"][0]["warning"])

    def test_store_writes_summary_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            store = EvidenceStore(base / "evidence.jsonl")
            store.append_many(
                [
                    EvidenceRecord(
                        source="sosovalue.featured_news",
                        observed_at="2026-05-13T20:00:00+01:00",
                        timestamp="2026-05-12T00:00:00Z",
                        entity="BTC",
                        module="Feeds",
                        relation="news_mention",
                        value="BTC news",
                        confidence=0.75,
                        evidence_path="cache/news.json",
                    )
                ]
            )

            summary = store.write_summary(base / "summary.json")

            self.assertEqual(summary["record_count"], 1)
            self.assertTrue((base / "summary.json").exists())

    def test_sodex_websocket_update_expands_into_evidence(self) -> None:
        records = sodex_ws_evidence(
            {
                "channel": "allBookTicker",
                "type": "snapshot",
                "data": [{"symbol": "BTC-USD", "bidPx": "100", "askPx": "101"}],
            },
            observed_at="2026-05-14T00:00:00Z",
            evidence_path="runs/sodex_probes/ws.json",
        )

        self.assertEqual(len(records), 1)
        row = records[0].to_dict()
        self.assertEqual(row["source"], "sodex.websocket")
        self.assertEqual(row["module"], "SoDEX")
        self.assertEqual(row["entity"], "BTC-USD")
        self.assertEqual(row["relation"], "websocket_allBookTicker")
        self.assertEqual(row["value"], "100")
        self.assertEqual(row["attributes"]["bid"], "100")
        self.assertEqual(row["attributes"]["ask"], "101")

    def test_sodex_websocket_update_uses_compact_bid_ask_aliases(self) -> None:
        records = sodex_ws_evidence(
            {
                "channel": "allBookTicker",
                "type": "snapshot",
                "data": [{"s": "BTC-USD", "b": "100", "a": "101"}],
            },
            observed_at="2026-05-14T00:00:00Z",
            evidence_path="runs/sodex_probes/ws.json",
        )

        row = records[0].to_dict()
        self.assertEqual(row["value"], "100")
        self.assertEqual(row["attributes"]["bid"], "100")
        self.assertEqual(row["attributes"]["ask"], "101")


if __name__ == "__main__":
    unittest.main()
