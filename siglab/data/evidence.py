from __future__ import annotations

import hashlib
import asyncio
import json
import logging
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from collections.abc import Callable, Iterable

logger = logging.getLogger(__name__)

_CURRENCY_SYMBOL_ALIASES: dict[str, str] = {
    "BITCOIN": "BTC",
    "ETHEREUM": "ETH",
    "SOLANA": "SOL",
    "XRP": "XRP",
    "DOGECOIN": "DOGE",
    "BNB": "BNB",
    "HYPERLIQUID": "HYPE",
    "SUI": "SUI",
}


def _news_relevance_score(
    item: dict[str, Any],
    symbols: set[str],
) -> float:
    """Score a news item's relevance to given currency symbols (0.0–1.0).

    Checks matchedCurrencies field first, then falls back to title/summary text
    matching. Returns 1.0 for explicit currency match, 0.3 per text mention
    (capped at 1.0).
    """
    matched_list = item.get("matched_currencies") or item.get("matchedCurrencies")
    if matched_list:
        matched_ids: set[str] = set()
        for c in matched_list:
            if isinstance(c, dict):
                name = str(c.get("currencyName") or c.get("symbol") or "").upper().strip()
                if name:
                    matched_ids.add(name)
                    alias = _CURRENCY_SYMBOL_ALIASES.get(name)
                    if alias:
                        matched_ids.add(alias)
            elif isinstance(c, (str, int, float)):
                name = str(c).upper().strip()
                if name:
                    matched_ids.add(name)
        if matched_ids & symbols:
            return 1.0
    title = str(_first_of(item, ("title", "headline")) or "").upper()
    summary = str(_first_of(item, ("summary", "description", "content")) or "").upper()
    text = title + " " + summary
    matches = sum(1 for sym in symbols if sym in text)
    if matches:
        return min(1.0, 0.3 * matches)
    return 0.0


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

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            object.__setattr__(
                self,
                "confidence",
                max(0.0, min(1.0, float(self.confidence))),
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EvidenceStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._seen_ids: set[str] | None = None
        self.last_append_stats: dict[str, int] = {
            "records_seen": 0,
            "records_appended": 0,
            "duplicates_skipped": 0,
        }

    def _load_seen_ids(self) -> set[str]:
        if self._seen_ids is None:
            self._seen_ids = {_evidence_id(row) for row in self.load()}
        return self._seen_ids

    def _is_duplicate(self, row: dict[str, Any], seen_ids: set[str]) -> bool:
        evidence_id = _evidence_id(row)
        if evidence_id in seen_ids:
            return True
        created_at = row.get("created_at")
        if created_at and isinstance(created_at, str):
            try:
                ts = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                if (datetime.now(UTC) - ts).total_seconds() < 30:
                    seen_ids.add(evidence_id)
                    return False
            except (ValueError, TypeError):
                pass
        seen_ids.add(evidence_id)
        return False

    def append_many(self, records: Iterable[EvidenceRecord]) -> int:
        rows = [record.to_dict() for record in records]
        if not rows:
            self.last_append_stats = {
                "records_seen": 0,
                "records_appended": 0,
                "duplicates_skipped": 0,
            }
            return 0
        seen_ids = self._load_seen_ids()
        unique_rows: list[dict[str, Any]] = []
        for row in rows:
            evidence_id = _evidence_id(row)
            if self._is_duplicate(row, seen_ids):
                continue
            row["evidence_id"] = evidence_id
            unique_rows.append(row)
        if unique_rows:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                for row in unique_rows:
                    handle.write(
                        json.dumps(row, ensure_ascii=True, sort_keys=True, default=str)
                        + "\n",
                    )
        self.last_append_stats = {
            "records_seen": len(rows),
            "records_appended": len(unique_rows),
            "duplicates_skipped": len(rows) - len(unique_rows),
        }
        return len(unique_rows)

    def load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [
            json.loads(line)
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

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
        for field_name, value in (("module", module), ("relation", relation)):
            if value is not None:
                rows = [row for row in rows if str(row.get(field_name)) == value]
        return rows[: max(0, int(limit))]

    def linked_relations(self, *, max_day_gap: int = 1) -> list[dict[str, Any]]:
        return link_feed_events_to_etf_flows(self.load(), max_day_gap=max_day_gap)

    def summary(self, *, max_day_gap: int = 1, top_links: int = 10) -> dict[str, Any]:
        rows = self.load()
        return summarize_evidence(
            rows,
            self.linked_relations(max_day_gap=max_day_gap),
            top_links=top_links,
        )

    def write_summary(
        self,
        path: Path,
        *,
        max_day_gap: int = 1,
        top_links: int = 10,
    ) -> dict[str, Any]:
        summary = self.summary(max_day_gap=max_day_gap, top_links=top_links)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                summary,
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
                default=str,
            )
            + "\n",
            encoding="utf-8",
        )
        return summary


def _collect_evidence(
    rows: Iterable[Any],
    extractor: Callable[[Any], EvidenceRecord | list[EvidenceRecord] | None],
) -> list[EvidenceRecord]:
    records: list[EvidenceRecord] = []
    for item in rows:
        result = extractor(item)
        if result is None:
            continue
        if isinstance(result, list):
            records.extend(result)
        else:
            records.append(result)
    return records


def etf_inflow_evidence(
    api_rows: Iterable[dict[str, Any]],
    *,
    etf_type: str,
    observed_at: str,
    evidence_path: str,
) -> list[EvidenceRecord]:
    def _extract(row: dict[str, Any]) -> list[EvidenceRecord]:
        date = str(row.get("date") or "")
        if not date:
            return []
        return [
            EvidenceRecord(
                source="sosovalue.etf_historical_inflow",
                observed_at=observed_at,
                timestamp=date,
                entity=etf_type,
                module="ETF",
                relation=relation_name,
                value=row.get(api_key),
                confidence=0.95,
                evidence_path=evidence_path,
                attributes={attr_key: row.get(attr_key)},
            )
            for relation_name, api_key, attr_key in [
                ("total_net_inflow", "totalNetInflow", "totalValueTraded"),
                ("total_net_assets", "totalNetAssets", "cumNetInflow"),
            ]
        ]
    return _collect_evidence(api_rows, _extract)


def news_evidence(
    rows: Iterable[dict[str, Any]],
    *,
    observed_at: str,
    evidence_path: str,
    module: str = "Feeds",
    default_entity: str = "market",
    source: str = "sosovalue.featured_news",
    currency_filter: set[str] | None = None,
) -> list[EvidenceRecord]:
    if currency_filter:
        symbols = {str(s).upper().strip() for s in currency_filter}
        rows = [r for r in rows if _news_relevance_score(r, symbols) > 0.0]
    def _extract(row: dict[str, Any]) -> EvidenceRecord | None:
        content = _preferred_multilingual_content(row.get("multilanguageContent"))
        title = str(
            _first_of(row, ("title", "headline"))
            or content.get("title")
            or content.get("content")
            or "",
        ).strip()
        if not title:
            return None
        matched = row.get("matchedCurrencies")
        entity = str(
            _first_of(row, ("currencyName", "symbol", "currencySymbol"))
            or _matched_currency_symbol(matched, preferred=default_entity)
            or default_entity,
        )
        timestamp = _first_of(
            row,
            ("publishTime", "publishedAt", "createdAt", "createTime", "releaseTime"),
        )
        return EvidenceRecord(
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
                "summary": _first_of(row, ("summary", "description"))
                or content.get("content"),
                "author": row.get("author"),
                "category": row.get("category"),
                "matchedCurrencies": matched if isinstance(matched, list) else [],
            },
        )
    return _collect_evidence(rows, _extract)


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

    def _extract(row: dict[str, Any]) -> EvidenceRecord | None:
        if not isinstance(row, dict):
            return None
        symbol = str(_first_of(row, ("symbol", "s", "pair")) or channel or "sodex")
        timestamp = _first_of(row, ("T", "time", "closeTime", "blockTime"))
        bid = _first_of(row, ("bidPx", "bid", "b"))
        ask = _first_of(row, ("askPx", "ask", "a"))
        trade_value = (
            _first_of(row, ("lastPx", "markPrice", "bidPx", "bid", "b", "p"))
            or update_type
        )
        return EvidenceRecord(
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
    return _collect_evidence(rows, _extract)


def sodex_rest_evidence(
    tickers: Iterable[dict[str, Any]],
    *,
    observed_at: str,
    evidence_path: str,
) -> list[EvidenceRecord]:
    """Convert SoDEX REST /markets/tickers + /markets/bookTickers into evidence records.

    Each ticker dict may contain the following fields (SoDEX native or canonical names):
        symbol, lastPx/lastPrice, priceChangePercent,
        bidPx/bidPrice, askPx/askPrice,
        volume/baseVolume, quoteVolume.
    """
    def _extract(row: dict[str, Any]) -> list[EvidenceRecord]:
        symbol = str(_first_of(row, ("symbol",)) or "unknown")
        entity = symbol.upper()
        last_price = _coerce_float(_first_of(row, ("lastPx", "lastPrice")))
        bid_price = _coerce_float(_first_of(row, ("bidPx", "bidPrice")))
        ask_price = _coerce_float(_first_of(row, ("askPx", "askPrice")))
        price_change = _coerce_float(_first_of(row, ("priceChangePercent",)))
        base_volume = _coerce_float(_first_of(row, ("volume", "baseVolume")))
        quote_volume = _coerce_float(_first_of(row, ("quoteVolume",)))

        records: list[EvidenceRecord] = []
        common = dict(
            source="sodex.rest.perps_market_tickers",
            observed_at=observed_at,
            entity=entity,
            module="SoDEX",
            confidence=0.9,
            evidence_path=evidence_path,
            attributes={"symbol": symbol},
        )
        if last_price is not None:
            records.append(EvidenceRecord(**common, relation="mark_price", value=last_price))
        if bid_price is not None:
            records.append(EvidenceRecord(**common, relation="bid_price", value=bid_price))
        if ask_price is not None:
            records.append(EvidenceRecord(**common, relation="ask_price", value=ask_price))
        if price_change is not None:
            records.append(
                EvidenceRecord(**common, relation="price_change_24h_pct", value=price_change)
            )
        if base_volume is not None:
            records.append(
                EvidenceRecord(**common, relation="base_volume_24h", value=base_volume)
            )
        if quote_volume is not None:
            records.append(
                EvidenceRecord(**common, relation="quote_volume_24h", value=quote_volume)
            )
        # Consolidated quote record for market report consumption
        if bid_price is not None or ask_price is not None:
            quote_value = ask_price if ask_price is not None else bid_price
            records.append(
                EvidenceRecord(
                    source="sodex.rest.perps_market_tickers",
                    observed_at=observed_at,
                    entity=entity,
                    module="SoDEX",
                    relation="quote",
                    value=quote_value,
                    confidence=0.9,
                    evidence_path=evidence_path,
                    attributes={"symbol": symbol, "bid": bid_price, "ask": ask_price},
                )
            )
        return records
    return _collect_evidence(tickers, _extract)



def _merge_ticker_book(
    tickers: list[dict[str, Any]],
    book_tickers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge ticker data with book ticker data keyed by symbol."""
    book_by_symbol: dict[str, dict[str, Any]] = {}
    for bt in book_tickers:
        sym = str(bt.get("symbol") or "")
        if sym:
            book_by_symbol[sym] = bt
    merged: list[dict[str, Any]] = []
    for tk in tickers:
        sym = str(tk.get("symbol") or "")
        row = dict(tk)
        if sym in book_by_symbol:
            bt = book_by_symbol[sym]
            row["bidPx"] = _first_of(bt, ("bidPx", "bidPrice"))
            row["askPx"] = _first_of(bt, ("askPx", "askPrice"))
        merged.append(row)
    return merged


async def sodex_quote_evidence(
    client: Any,  # SoDEXPublicPerpsClient — avoid circular import of the class
    *,
    observed_at: str,
    evidence_path: str,
) -> list[EvidenceRecord]:
    """Fetch SoDEX tickers + book tickers and convert to evidence records.

    Args:
        client: SoDEXPublicPerpsClient instance (or duck-typed with
            ``tickers()`` and ``book_tickers()`` async methods).
    """
    tickers_data, book_tickers_data = await asyncio.gather(
        client.tickers(),
        client.book_tickers(),
        return_exceptions=True,
    )
    if isinstance(tickers_data, Exception):
        logger.warning("sodex_quote_evidence: tickers() failed: %s", tickers_data)
        tickers_data = []
    if isinstance(book_tickers_data, Exception):
        logger.warning("sodex_quote_evidence: book_tickers() failed: %s", book_tickers_data)
        book_tickers_data = []
    merged = _merge_ticker_book(
        list(tickers_data) if isinstance(tickers_data, list) else [],
        list(book_tickers_data) if isinstance(book_tickers_data, list) else [],
    )
    return sodex_rest_evidence(
        merged,
        observed_at=observed_at,
        evidence_path=evidence_path,
    )

def link_feed_events_to_etf_flows(
    rows: Iterable[dict[str, Any]],
    *,
    max_day_gap: int = 1,
) -> list[dict[str, Any]]:
    materialized = list(rows)
    flows = [
        r
        for r in materialized
        if r.get("module") == "ETF" and r.get("relation") == "total_net_inflow"
    ]
    news_rows = [
        r
        for r in materialized
        if r.get("module") == "Feeds" and r.get("relation") == "news_mention"
    ]
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
            if (
                entity not in {"", "market"}
                and entity not in str(flow.get("entity") or "").lower()
            ):
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
                },
            )
    return links


def summarize_evidence(
    rows: Iterable[dict[str, Any]],
    links: Iterable[dict[str, Any]],
    *,
    top_links: int = 10,
) -> dict[str, Any]:
    materialized_rows = list(rows)
    materialized_links = list(links)
    sorted_links = sorted(
        materialized_links,
        key=lambda link: (
            float(link.get("confidence") or 0.0),
            str(link.get("feed_timestamp") or ""),
        ),
        reverse=True,
    )
    count_fields = ("module", "relation", "source", "entity")
    counts = {
        f"{f}_counts": dict(
            sorted(Counter(str(r.get(f) or "") for r in materialized_rows).items()),
        )
        for f in count_fields
    }
    top_link_keys = (
        "relation",
        "entity",
        "feed_entity",
        "confidence",
        "warning",
        "feed_title",
        "feed_timestamp",
        "flow_date",
        "flow_value",
        "day_gap",
    )
    return {
        "record_count": len(materialized_rows),
        "link_count": len(materialized_links),
        **counts,
        "link_relation_counts": dict(
            sorted(
                Counter(
                    str(link.get("relation") or "") for link in materialized_links
                ).items(),
            ),
        ),
        "top_links": [
            {k: link.get(k) for k in top_link_keys}
            for link in sorted_links[: max(0, int(top_links))]
        ],
    }


def _evidence_id(row: dict[str, Any]) -> str:
    existing = row.get("evidence_id")
    if existing:
        return str(existing)
    value = row.get("value")
    if isinstance(value, bool):
        normalized_value: Any = value
    elif isinstance(value, (int, float)):
        normalized_value = repr(float(value))
    elif isinstance(value, str):
        try:
            normalized_value = repr(float(value.strip()))
        except ValueError:
            normalized_value = value
    else:
        normalized_value = value
    stable_payload: dict[str, Any] = {
        "source": row.get("source"),
        "entity": row.get("entity"),
        "module": row.get("module"),
        "relation": row.get("relation"),
        "timestamp": row.get("timestamp"),
        "value": normalized_value,
    }
    if row.get("evidence_path") is not None:
        stable_payload["evidence_path"] = row["evidence_path"]
    encoded = json.dumps(
        stable_payload,
        ensure_ascii=True,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    ).encode("utf-8")
    return "ev_" + hashlib.sha256(encoded).hexdigest()[:24]


def _coerce_float(value: object) -> float | None:
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


def _preferred_multilingual_content(value: object) -> dict[str, Any]:
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


def _matched_currency_symbol(
    value: object,
    *,
    preferred: str | None = None,
) -> str | None:
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
    for key in keys:
        value = row.get(key)
        if value is not None:
            return value
    return None


def _record_day(value: object) -> date | None:
    if value is None:
        return None
    candidate = _coerce_float(value)
    if candidate is not None:
        if candidate > 10000000000:
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


def write_evidence_graph_html(summary_path: Path, output_path: Path) -> Path:
    """Render an evidence summary JSON file as a standalone HTML graph."""
    try:
        with open(summary_path) as f:
            records: list[dict[str, Any]] = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        records = []
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for rec in records:
        symbol = rec.get("symbol", "unknown")
        direction = rec.get("signal", "NEUTRAL")
        source = rec.get("source", "unknown")
        confidence = rec.get("confidence", 0.0)
        for label in (source, symbol):
            if label and label not in seen_ids:
                seen_ids.add(label)
                kind = "source" if label == source else "symbol"
                nodes.append({"id": label, "label": label, "kind": kind})
        if source and symbol:
            edges.append(
                {
                    "source": source,
                    "target": symbol,
                    "direction": direction,
                    "confidence": confidence,
                },
            )
    html_parts: list[str] = [
        "<!DOCTYPE html>",
        "<html><head><meta charset='utf-8'><title>SigLab Evidence Graph</title>",
        "<style>body{font-family:sans-serif;margin:2em}.node{display:inline-block;padding:4px 10px;margin:4px;border-radius:4px}.source{background:#ddf}.symbol{background:#dfd}</style></head><body>",
        f"<h1>Evidence Graph</h1><p>{len(nodes)} nodes, {len(edges)} edges</p>",
        "<h2>Nodes</h2><div>",
    ]
    for n in nodes:
        html_parts.append(f'<span class="node {n["kind"]}">{n["label"]}</span>')
    html_parts.append("</div><h2>Edges</h2><ul>")
    for e in edges:
        html_parts.append(
            f"<li>{e['source']} → {e['target']} ({e['direction']}, confidence={e['confidence']})</li>",
        )
    html_parts.append("</ul></body></html>")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(html_parts), encoding="utf-8")
    return output_path
