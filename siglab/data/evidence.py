from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter
from datetime import UTC, date, datetime
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable


logger = logging.getLogger(__name__)


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
        if not rows:
            self.last_append_stats = {"records_seen": 0, "records_appended": 0, "duplicates_skipped": 0}
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
        if unique_rows:
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
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]

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
        for field, value in (("module", module), ("relation", relation)):
            if value is not None:
                rows = [row for row in rows if str(row.get(field)) == value]
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
        for relation, attr_key in [
            ("total_net_inflow", "total_value_traded"),
            ("total_net_assets", "cum_net_inflow"),
        ]:
            records.append(
                EvidenceRecord(
                    source="sosovalue.etf_historical_inflow",
                    observed_at=observed_at,
                    timestamp=date,
                    entity=etf_type,
                    module="ETF",
                    relation=relation,
                    value=row.get(relation),
                    confidence=0.95,
                    evidence_path=evidence_path,
                    attributes={attr_key: row.get(attr_key)},
                )
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
        title = str(_first_of(row, ("title", "headline")) or content.get("title") or content.get("content") or "").strip()
        if not title:
            continue
        matched = row.get("matchedCurrencies")
        entity = str(
            _first_of(row, ("currencyName", "symbol", "currencySymbol"))
            or _matched_currency_symbol(matched, preferred=default_entity)
            or default_entity
        )
        timestamp = _first_of(row, ("publishTime", "publishedAt", "createdAt", "createTime", "releaseTime"))
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
                    "url": _first_of(row, ("url", "link", "sourceLink")),
                    "summary": _first_of(row, ("summary", "description")) or content.get("content"),
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
        symbol = str(_first_of(row, ("symbol", "s", "pair")) or channel or "sodex")
        timestamp = _first_of(row, ("T", "time", "closeTime", "blockTime"))
        bid = _first_of(row, ("bidPx", "bid", "b"))
        ask = _first_of(row, ("askPx", "ask", "a"))
        trade_value = _first_of(row, ("lastPx", "markPrice", "bidPx", "bid", "b", "p")) or update_type
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
    flows = [r for r in materialized if r.get("module") == "ETF" and r.get("relation") == "total_net_inflow"]
    news_rows = [r for r in materialized if r.get("module") == "Feeds" and r.get("relation") == "news_mention"]
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
            if entity not in {"", "market"} and entity not in str(flow.get("entity") or "").lower():
                continue
            links.append({
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
            })
    return links


def summarize_evidence(rows: Iterable[dict[str, Any]], links: Iterable[dict[str, Any]], *, top_links: int = 10) -> dict[str, Any]:
    materialized_rows = list(rows)
    materialized_links = list(links)
    sorted_links = sorted(
        materialized_links,
        key=lambda link: (float(link.get("confidence") or 0.0), str(link.get("feed_timestamp") or "")),
        reverse=True,
    )
    count_fields = ("module", "relation", "source", "entity")
    counts = {f"{f}_counts": dict(sorted(Counter(str(r.get(f) or "") for r in materialized_rows).items())) for f in count_fields}
    top_link_keys = ("relation", "entity", "feed_entity", "confidence", "warning", "feed_title", "feed_timestamp", "flow_date", "flow_value", "day_gap")
    return {
        "record_count": len(materialized_rows),
        "link_count": len(materialized_links),
        **counts,
        "link_relation_counts": dict(sorted(Counter(str(l.get("relation") or "") for l in materialized_links).items())),
        "top_links": [{k: link.get(k) for k in top_link_keys} for link in sorted_links[: max(0, int(top_links))]],
    }


def _evidence_id(row: dict[str, Any]) -> str:
    existing = row.get("evidence_id")
    if existing:
        return str(existing)
    stable_payload: dict[str, Any] = {
        "source": row.get("source"),
        "entity": row.get("entity"),
        "module": row.get("module"),
        "relation": row.get("relation"),
        "timestamp": row.get("timestamp"),
        "value": _normalize_value(row.get("value")),
    }
    # evidence_path is location metadata, not identity; exclude None/missing.
    if row.get("evidence_path") is not None:
        stable_payload["evidence_path"] = row["evidence_path"]
    encoded = json.dumps(stable_payload, ensure_ascii=True, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
    return "ev_" + hashlib.sha256(encoded).hexdigest()[:24]


def _coerce_float(value: Any) -> float | None:
    """Coerce int/float/numeric-string to float; None for bool or non-numeric."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _normalize_value(value: Any) -> Any:
    """Normalize a value for stable hashing across equivalent representations.

    Numeric values (int, float, or numeric string) are coerced to float and
    rendered via repr() so "100", 100, 100.0 all hash identically. bool is
    preserved distinctly from 0/1; other values pass through unchanged.
    """
    if isinstance(value, bool):
        return value
    candidate = _coerce_float(value)
    return repr(candidate) if candidate is not None else value


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


def _first_of(row: dict[str, Any], keys: Iterable[str]) -> Any:
    """Return the first non-None value among ``keys`` in ``row``."""
    for key in keys:
        value = row.get(key)
        if value is not None:
            return value
    return None


def _record_day(value: Any) -> date | None:
    if value is None:
        return None
    # Numeric epoch (int, float, or numeric string) — ms if absurdly large.
    candidate = _coerce_float(value)
    if candidate is not None:
        if candidate > 10_000_000_000:
            candidate = candidate / 1000.0
        return datetime.fromtimestamp(candidate, tz=UTC).date()
    raw = str(value).strip()
    for parser in (
        lambda: datetime.fromisoformat(raw.replace("Z", "+00:00")),
        lambda: datetime.strptime(raw[:10], "%Y-%m-%d"),
        lambda: datetime.strptime(raw[:10], "%m/%d/%Y"),
    ):
        try:
            return parser().date()
        except ValueError:
            continue
    logger.warning("Unparseable evidence timestamp %r; returning None", raw)
    return None
