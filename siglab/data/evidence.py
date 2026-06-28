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
    api_source: str | None = None

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
        self.last_append_stats: dict[str, int] = {
            "records_seen": 0,
            "records_appended": 0,
        }

    def append_many(self, records: Iterable[EvidenceRecord]) -> int:
        rows: list[dict[str, Any]] = []
        seen = 0
        for record in records:
            seen += 1
            row = record.to_dict()
            # Skip stale evidence (>180 days old) unless it's intentional historical backfill
            # (detected by both timestamp and observed_at being far in the past)
            ts_raw = row.get("timestamp") or row.get("observed_at")
            if ts_raw:
                try:
                    ts_dt = datetime.fromisoformat(ts_raw)
                    ts_age = datetime.now(UTC) - ts_dt
                    if ts_age.days > 180:
                        # Allow through if observed_at is ALSO old — signals intentional backfill
                        obs_raw = row.get("observed_at")
                        if obs_raw:
                            obs_dt = datetime.fromisoformat(obs_raw)
                            obs_age = datetime.now(UTC) - obs_dt
                            if obs_age.days <= 180:
                                continue  # stale live data, skip
                        else:
                            continue
                except (ValueError, TypeError):
                    pass
            rows.append(row)
        if not rows:
            self.last_append_stats = {
                "records_seen": seen,
                "records_appended": 0,
            }
            return 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            for row in rows:
                row["evidence_id"] = _evidence_id(row)
                handle.write(
                    json.dumps(row, ensure_ascii=True, sort_keys=True, default=str)
                    + "\n",
                )
        self.last_append_stats = {
            "records_seen": seen,
            "records_appended": len(rows),
        }
        return len(rows)

    def load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [
            json.loads(line)
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]






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
                confidence=_evidence_confidence(observed_at, base=0.95),
                evidence_path=evidence_path,
                attributes={attr_key: row.get(attr_key)},
                api_source=api_key,
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
            timestamp=str(timestamp) if timestamp is not None else observed_at,
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
        # Skip zero-liquidity pairs (no price data at all)
        if not any(
            [
                row.get("bidPx") or row.get("bidPrice"),
                row.get("askPx") or row.get("askPrice"),
                row.get("markPx") or row.get("markPrice"),
            ]
        ):
            return []
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
            timestamp=observed_at,
            entity=entity,
            module="SoDEX",
            confidence=_evidence_confidence(observed_at, base=0.9),
            evidence_path=evidence_path,
            attributes={"symbol": symbol},
            api_source="sodex.rest.perps_market_tickers",
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


def _evidence_confidence(observed_at: str, base: float = 0.95) -> float:
    """Calculate dynamic confidence based on data freshness.

    Confidence decays linearly from *base* at ~0.01 per hour,
    floored at 0.5 so evidence is never completely discounted.
    """
    try:
        age_hours = (
            (datetime.now(UTC) - datetime.fromisoformat(observed_at)).total_seconds() / 3600
        )
    except (ValueError, TypeError):
        return base
    return max(0.5, base - (age_hours * 0.01))


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


