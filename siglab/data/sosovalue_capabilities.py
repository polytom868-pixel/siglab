from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SoSoValueCapability:
    module: str
    endpoint: str
    wrapper: str | None
    tested: bool
    cached: bool
    retried: bool
    rate_limited: bool
    used_by_strategy: bool
    status: str
    reason: str


CAPABILITIES: tuple[SoSoValueCapability, ...] = (
    SoSoValueCapability(
        "Currency & Pairs",
        "POST /openapi/v1/data/default/coin/list",
        "SoSoValueClient.listed_currencies",
        True,
        True,
        True,
        True,
        False,
        "IMPLEMENTED",
        "Official current docs expose this as the prerequisite listed-currency surface.",
    ),
    SoSoValueCapability(
        "Feeds",
        "GET /api/v1/news/featured",
        "SoSoValueClient.featured_news / featured_news_pages",
        True,
        True,
        True,
        True,
        False,
        "IMPLEMENTED",
        "Official current docs expose paginated featured news; SigLab enforces pageSize 1..100 and supports bounded page fetches.",
    ),
    SoSoValueCapability(
        "Feeds",
        "GET /api/v1/news/featured/currency",
        "SoSoValueClient.featured_news_by_currency / featured_news_by_currency_pages",
        True,
        True,
        True,
        True,
        True,
        "IMPLEMENTED",
        "SigLab normalizes this into research/news context for strategy scoring and supports bounded page fetches.",
    ),
    SoSoValueCapability(
        "ETF",
        "POST /openapi/v2/etf/historicalInflowChart",
        "SoSoValueClient.etf_historical_inflow",
        True,
        True,
        True,
        True,
        True,
        "IMPLEMENTED",
        "SigLab uses this as the ETF proxy backbone for market features.",
    ),
    SoSoValueCapability(
        "ETF",
        "POST /openapi/v2/etf/currentEtfDataMetrics",
        "SoSoValueClient.etf_current_metrics",
        True,
        True,
        True,
        True,
        False,
        "IMPLEMENTED",
        "Official current docs expose current daily ETF metrics.",
    ),
    SoSoValueCapability(
        "Market/reference layer",
        "currency info / market snapshot / trading pairs / historical klines / supply / sector spotlight",
        None,
        False,
        False,
        False,
        False,
        False,
        "BLOCKED",
        "Not present in the official current GitBook API nav verified during this sweep; guessing paths is forbidden.",
    ),
    SoSoValueCapability(
        "SoSoValue Index",
        "index data",
        None,
        False,
        False,
        False,
        False,
        False,
        "BLOCKED",
        "Public index product docs were found, but no callable OpenAPI endpoint page was verified.",
    ),
    SoSoValueCapability(
        "Crypto Stocks",
        "crypto stocks data",
        None,
        False,
        False,
        False,
        False,
        False,
        "BLOCKED",
        "No official callable OpenAPI endpoint page was verified.",
    ),
    SoSoValueCapability(
        "BTC Treasuries",
        "BTC treasury data",
        None,
        False,
        False,
        False,
        False,
        False,
        "BLOCKED",
        "No official callable OpenAPI endpoint page was verified.",
    ),
    SoSoValueCapability(
        "Fundraising",
        "fundraising data",
        None,
        False,
        False,
        False,
        False,
        False,
        "BLOCKED",
        "No official callable OpenAPI endpoint page was verified.",
    ),
    SoSoValueCapability(
        "Macro",
        "macro events / event history",
        None,
        False,
        False,
        False,
        False,
        False,
        "BLOCKED",
        "No official callable OpenAPI endpoint page was verified.",
    ),
    SoSoValueCapability(
        "Analysis Charts",
        "analysis chart data",
        None,
        False,
        False,
        False,
        False,
        False,
        "BLOCKED",
        "No official callable OpenAPI endpoint page was verified.",
    ),
)


def capability_matrix() -> list[dict[str, object]]:
    return [
        {
            "doc_module": item.module,
            "endpoint": item.endpoint,
            "siglab_wrapper": item.wrapper,
            "tested": item.tested,
            "cached": item.cached,
            "retried": item.retried,
            "rate_limited": item.rate_limited,
            "used_by_strategy": item.used_by_strategy,
            "status": item.status,
            "reason": item.reason,
        }
        for item in CAPABILITIES
    ]
