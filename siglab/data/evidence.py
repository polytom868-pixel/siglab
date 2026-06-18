from __future__ import annotations

import json
import hashlib
from collections import Counter
from datetime import UTC, date, datetime
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class EvidenceRecord:
    source: str
    observed_at: str
    entity: str
    module: str
    relation: str
    confidence: float
    evidence_path: str
    timestamp: str | None = None
    value: Any = None
    attributes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["confidence"] = max(0.0, min(1.0, float(self.confidence)))
        return payload


class EvidenceStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.last_append_stats: dict[str, int] = {"records_seen": 0, "records_appended": 0, "duplicates_skipped": 0}

    def append_many(self, records: Iterable[EvidenceRecord]) -> int:
        rows = [record.to_dict() for record in records]
        self.last_append_stats = {"records_seen": len(rows), "records_appended": 0, "duplicates_skipped": 0}
        if not rows:
            return 0
        existing_ids = {_evidence_id(row) for row in self.load()}
        unique_rows: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for row in rows:
            evidence_id = _evidence_id(row)
            if evidence_id in existing_ids or evidence_id in seen_ids:
                continue
            row["evidence_id"] = evidence_id
            unique_rows.append(row)
            seen_ids.add(evidence_id)
        if not unique_rows:
            self.last_append_stats = {
                "records_seen": len(rows),
                "records_appended": 0,
                "duplicates_skipped": len(rows),
            }
            return 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            for row in unique_rows:
                handle.write(json.dumps(row, ensure_ascii=True, sort_keys=True, default=str) + "\n")
        self.last_append_stats = {
            "records_seen": len(rows),
            "records_appended": len(unique_rows),
            "duplicates_skipped": len(rows) - len(unique_rows),
        }
        return len(unique_rows)

    def load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows

    def query(
        self,
        *,
        entity: str | None = None,
        module: str | None = None,
        relation: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        rows = self.load()
        if entity is not None:
            needle = entity.lower()
            rows = [row for row in rows if needle in str(row.get("entity", "")).lower()]
        if module is not None:
            rows = [row for row in rows if str(row.get("module")) == module]
        if relation is not None:
            rows = [row for row in rows if str(row.get("relation")) == relation]
        return rows[: max(0, int(limit))]

    def linked_relations(self, *, max_day_gap: int = 1) -> list[dict[str, Any]]:
        return link_feed_events_to_etf_flows(self.load(), max_day_gap=max_day_gap)

    def summary(self, *, max_day_gap: int = 1, top_links: int = 10) -> dict[str, Any]:
        rows = self.load()
        return summarize_evidence(rows, self.linked_relations(max_day_gap=max_day_gap), top_links=top_links)

    def write_summary(self, path: Path, *, max_day_gap: int = 1, top_links: int = 10) -> dict[str, Any]:
        summary = self.summary(max_day_gap=max_day_gap, top_links=top_links)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(summary, ensure_ascii=True, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
        return summary


def etf_inflow_evidence(
    rows: Iterable[dict[str, Any]],
    *,
    etf_type: str,
    observed_at: str,
    evidence_path: str,
) -> list[EvidenceRecord]:
    records: list[EvidenceRecord] = []
    for row in rows:
        date = str(row.get("date") or "")
        if not date:
            continue
        records.extend(
            [
                EvidenceRecord(
                    source="sosovalue.etf_historical_inflow",
                    observed_at=observed_at,
                    timestamp=date,
                    entity=etf_type,
                    module="ETF",
                    relation="total_net_inflow",
                    value=row.get("total_net_inflow"),
                    confidence=0.95,
                    evidence_path=evidence_path,
                    attributes={"total_value_traded": row.get("total_value_traded")},
                ),
                EvidenceRecord(
                    source="sosovalue.etf_historical_inflow",
                    observed_at=observed_at,
                    timestamp=date,
                    entity=etf_type,
                    module="ETF",
                    relation="total_net_assets",
                    value=row.get("total_net_assets"),
                    confidence=0.95,
                    evidence_path=evidence_path,
                    attributes={"cum_net_inflow": row.get("cum_net_inflow")},
                ),
            ]
        )
    return records


def news_evidence(
    rows: Iterable[dict[str, Any]],
    *,
    observed_at: str,
    evidence_path: str,
    module: str = "Feeds",
    default_entity: str = "market",
    source: str = "sosovalue.featured_news",
) -> list[EvidenceRecord]:
    records: list[EvidenceRecord] = []
    for row in rows:
        content = _preferred_multilingual_content(row.get("multilanguageContent"))
        title = str(row.get("title") or row.get("headline") or content.get("title") or content.get("content") or "").strip()
        if not title:
            continue
        matched = row.get("matchedCurrencies")
        entity = str(
            row.get("currencyName")
            or row.get("symbol")
            or row.get("currencySymbol")
            or _matched_currency_symbol(matched, preferred=default_entity)
            or default_entity
        )
        timestamp = (
            row.get("publishTime")
            or row.get("publishedAt")
            or row.get("createdAt")
            or row.get("createTime")
            or row.get("releaseTime")
        )
        records.append(
            EvidenceRecord(
                source=source,
                observed_at=observed_at,
                timestamp=str(timestamp) if timestamp is not None else None,
                entity=entity,
                module=module,
                relation="news_mention",
                value=title,
                confidence=0.75,
                evidence_path=evidence_path,
                attributes={
                    "id": row.get("id"),
                    "url": row.get("url") or row.get("link") or row.get("sourceLink"),
                    "summary": row.get("summary") or row.get("description") or content.get("content"),
                    "author": row.get("author"),
                    "category": row.get("category"),
                    "matchedCurrencies": matched if isinstance(matched, list) else [],
                },
            )
        )
    return records


def sodex_ws_evidence(
    update: dict[str, Any],
    *,
    observed_at: str,
    evidence_path: str,
) -> list[EvidenceRecord]:
    channel = str(update.get("channel") or "")
    update_type = str(update.get("type") or "")
    data = update.get("data")
    rows = data if isinstance(data, list) else [data] if isinstance(data, dict) else []
    records: list[EvidenceRecord] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or row.get("s") or row.get("pair") or channel or "sodex")
        timestamp = row.get("T") or row.get("time") or row.get("closeTime") or row.get("blockTime")
        bid = row.get("bidPx") or row.get("bid") or row.get("b")
        ask = row.get("askPx") or row.get("ask") or row.get("a")
        trade_value = (
            row.get("lastPx")
            or row.get("markPrice")
            or row.get("bidPx")
            or row.get("bid")
            or row.get("b")
            or row.get("p")
            or update_type
        )
        records.append(
            EvidenceRecord(
                source="sodex.websocket",
                observed_at=observed_at,
                timestamp=str(timestamp) if timestamp is not None else observed_at,
                entity=symbol,
                module="SoDEX",
                relation=f"websocket_{channel or 'update'}",
                value=trade_value,
                confidence=0.8,
                evidence_path=evidence_path,
                attributes={
                    "channel": channel,
                    "type": update_type,
                    "bid": bid,
                    "ask": ask,
                    "raw_keys": sorted(row.keys()),
                },
            )
        )
    return records


def link_feed_events_to_etf_flows(rows: Iterable[dict[str, Any]], *, max_day_gap: int = 1) -> list[dict[str, Any]]:
    materialized = list(rows)
    flows = [row for row in materialized if row.get("module") == "ETF" and row.get("relation") == "total_net_inflow"]
    news_rows = [row for row in materialized if row.get("module") == "Feeds" and row.get("relation") == "news_mention"]
    links: list[dict[str, Any]] = []
    for news in news_rows:
        news_day = _record_day(news.get("timestamp"))
        if news_day is None:
            continue
        entity = str(news.get("entity") or "").lower()
        for flow in flows:
            flow_day = _record_day(flow.get("timestamp"))
            if flow_day is None:
                continue
            gap = abs((news_day - flow_day).days)
            if gap > int(max_day_gap):
                continue
            flow_entity = str(flow.get("entity") or "").lower()
            if entity not in {"", "market"} and entity not in flow_entity:
                continue
            links.append(
                {
                    "source": "siglab.evidence.link_feed_events_to_etf_flows",
                    "relation": "feed_event_near_etf_flow",
                    "left_evidence_path": news.get("evidence_path"),
                    "right_evidence_path": flow.get("evidence_path"),
                    "entity": flow.get("entity"),
                    "feed_entity": news.get("entity"),
                    "day_gap": gap,
                    "confidence": 0.45 if entity == "market" else 0.65,
                    "warning": "temporal/categorical link only; not causal",
                    "feed_title": news.get("value"),
                    "flow_value": flow.get("value"),
                    "flow_date": flow.get("timestamp"),
                    "feed_timestamp": news.get("timestamp"),
                }
            )
    return links


def summarize_evidence(rows: Iterable[dict[str, Any]], links: Iterable[dict[str, Any]], *, top_links: int = 10) -> dict[str, Any]:
    materialized_rows = list(rows)
    materialized_links = list(links)
    sorted_links = sorted(
        materialized_links,
        key=lambda link: (float(link.get("confidence") or 0.0), str(link.get("feed_timestamp") or "")),
        reverse=True,
    )
    return {
        "record_count": len(materialized_rows),
        "link_count": len(materialized_links),
        "module_counts": dict(sorted(Counter(str(row.get("module") or "") for row in materialized_rows).items())),
        "relation_counts": dict(sorted(Counter(str(row.get("relation") or "") for row in materialized_rows).items())),
        "source_counts": dict(sorted(Counter(str(row.get("source") or "") for row in materialized_rows).items())),
        "entity_counts": dict(sorted(Counter(str(row.get("entity") or "") for row in materialized_rows).items())),
        "link_relation_counts": dict(sorted(Counter(str(link.get("relation") or "") for link in materialized_links).items())),
        "top_links": [
            {
                "relation": link.get("relation"),
                "entity": link.get("entity"),
                "feed_entity": link.get("feed_entity"),
                "confidence": link.get("confidence"),
                "warning": link.get("warning"),
                "feed_title": link.get("feed_title"),
                "feed_timestamp": link.get("feed_timestamp"),
                "flow_date": link.get("flow_date"),
                "flow_value": link.get("flow_value"),
                "day_gap": link.get("day_gap"),
            }
            for link in sorted_links[: max(0, int(top_links))]
        ],
    }


def _evidence_id(row: dict[str, Any]) -> str:
    existing = row.get("evidence_id")
    if existing:
        return str(existing)
    stable_payload = {
        "source": row.get("source"),
        "entity": row.get("entity"),
        "module": row.get("module"),
        "relation": row.get("relation"),
        "timestamp": row.get("timestamp"),
        "value": row.get("value"),
        "evidence_path": row.get("evidence_path"),
    }
    encoded = json.dumps(stable_payload, ensure_ascii=True, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
    return "ev_" + hashlib.sha256(encoded).hexdigest()[:24]


def _preferred_multilingual_content(value: Any) -> dict[str, Any]:
    if not isinstance(value, list):
        return {}
    fallback: dict[str, Any] = {}
    for item in value:
        if not isinstance(item, dict):
            continue
        if not fallback:
            fallback = item
        if str(item.get("language") or "").lower() == "en":
            return item
    return fallback


def _matched_currency_symbol(value: Any, *, preferred: str | None = None) -> str | None:
    if not isinstance(value, list):
        return None
    preferred_value = str(preferred or "").strip().lower()
    fallback: str | None = None
    for item in value:
        if not isinstance(item, dict):
            continue
        symbol = item.get("symbol") or item.get("currencySymbol") or item.get("name")
        if symbol:
            normalized = str(symbol)
            if preferred_value and normalized.lower() == preferred_value:
                return normalized
            if fallback is None:
                fallback = normalized
    return fallback


def _record_day(value: Any) -> date | None:
    if value is None:
        return None
    raw = str(value)
    if raw.isdigit():
        numeric = int(raw)
        if numeric > 10_000_000_000:
            numeric = numeric // 1000
        return datetime.fromtimestamp(numeric, tz=UTC).date()
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return datetime.strptime(raw[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
