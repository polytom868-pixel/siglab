"""LLM tools for autonomous crypto research."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class ResearchTool:
    """A tool the LLM can call for research."""
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema format
    handler: Callable[..., Awaitable[str]]  # Returns text summary


# Tool: Get ETF inflow data
async def get_etf_flow(etf_type: str = "us-btc-spot") -> str:
    """Get latest ETF inflow/outflow data. etf_type: us-btc-spot or us-eth-spot."""
    from siglab.data.sosovalue_client import SoSoValueClient
    from siglab.config import load_settings
    settings = load_settings()
    key = settings.sosovalue_api_key_override
    if not key:
        return "Error: No SoSoValue API key configured"
    client = SoSoValueClient(api_key=key)
    rows = await client.etf_historical_inflow(etf_type=etf_type)
    if not rows:
        return "No ETF data available"
    latest = rows[0]
    return (
        f"Latest {etf_type} data:\n"
        f"  Date: {latest.get('date')}\n"
        f"  Net Flow: ${latest.get('totalNetInflow', 0):,.0f}\n"
        f"  Net Assets: ${latest.get('totalNetAssets', 0):,.0f}"
    )


ETF_FLOW_TOOL = ResearchTool(
    name="get_etf_flow",
    description="Get latest BTC or ETH ETF inflow/outflow data from SoSoValue",
    parameters={
        "type": "object",
        "properties": {
            "etf_type": {
                "type": "string",
                "enum": ["us-btc-spot", "us-eth-spot"],
                "description": "ETF type to query",
            }
        },
        "required": [],
    },
    handler=get_etf_flow,
)


# Tool: Get SoDEX market data
async def get_market_data(symbol: str = "BTC-USD") -> str:
    """Get current market data for a perpetual symbol from SoDEX."""
    from siglab.data.sodex_client import SoDEXPublicPerpsClient

    client = SoDEXPublicPerpsClient()
    tickers = await client.tickers()
    for t in tickers:
        if t.get("s") == symbol or t.get("symbol") == symbol:
            return (
                f"Market data for {symbol}:\n"
                f"  Last Price: {t.get('lastPx') or t.get('lastPrice', 'N/A')}\n"
                f"  Bid: {t.get('bidPx') or t.get('bidPrice', 'N/A')}\n"
                f"  Ask: {t.get('askPx') or t.get('askPrice', 'N/A')}\n"
                f"  24h Volume: {t.get('volume') or t.get('baseVolume', 'N/A')}"
            )
    return f"No data found for symbol {symbol}"


MARKET_DATA_TOOL = ResearchTool(
    name="get_market_data",
    description="Get current market data (price, bid, ask, volume) for a perp symbol from SoDEX",
    parameters={
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": "Trading pair symbol (e.g. BTC-USD, ETH-USD)",
            }
        },
        "required": [],
    },
    handler=get_market_data,
)


# Tool: Get SoSoValue news
async def get_crypto_news(currency: str = "BTC", limit: int = 5) -> str:
    """Get latest crypto news for a specific currency."""
    from siglab.data.sosovalue_client import SoSoValueClient
    from siglab.config import load_settings

    settings = load_settings()
    key = settings.sosovalue_api_key_override
    if not key:
        return "Error: No SoSoValue API key configured"
    client = SoSoValueClient(api_key=key)
    rows = await client.featured_news_by_currency(page_size=limit)
    if not rows:
        return "No news available"
    result = [f"Latest {limit} crypto news items:"]
    for row in rows[:limit]:
        # Try multilanguageContent title first, then direct title, then content field, then first 80 chars
        title = None
        mlc = row.get("multilanguageContent") or []
        if mlc and isinstance(mlc[0], dict):
            title = mlc[0].get("title")
        if not title:
            title = row.get("title")
        if not title:
            title = row.get("content")
        if not title:
            content_text = str(row.get("content") or "")
            title = content_text[:80] + "..." if len(content_text) > 80 else content_text
        if not title:
            title = "Untitled"
        result.append(f"  - {title}")
    return "\n".join(result)


NEWS_TOOL = ResearchTool(
    name="get_crypto_news",
    description="Get latest crypto news headlines from SoSoValue",
    parameters={
        "type": "object",
        "properties": {
            "currency": {"type": "string", "description": "Currency to get news for"},
            "limit": {"type": "integer", "description": "Number of news items (max 10)"},
        },
        "required": [],
    },
    handler=get_crypto_news,
)


# Tool: Get funding rate history
async def get_funding_rate(symbol: str = "BTC-USD", days: int = 7) -> str:
    """Get historical funding rate data for a perpetual symbol from SoDEX."""
    from siglab.data.sodex_client import SoDEXPublicPerpsClient

    client = SoDEXPublicPerpsClient()
    import time
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - (int(days) * 86400 * 1000)
    history = await client.funding_history(symbol, start_time=start_ms, end_time=now_ms)
    if not history:
        return f"No funding rate data available for {symbol}"
    recent = history[-1]
    avg_rate = sum(float(h.get("fundingRate", 0)) for h in history) / len(history)
    return (
        f"Funding rate data for {symbol} (last {days} days):\n"
        f"  Latest rate: {float(recent.get('fundingRate', 0)):.8f}\n"
        f"  Average rate: {avg_rate:.8f}\n"
        f"  Samples: {len(history)}"
    )


FUNDING_RATE_TOOL = ResearchTool(
    name="get_funding_rate",
    description="Get historical funding rate data for a perpetual symbol from SoDEX",
    parameters={
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": "Trading pair symbol (e.g. BTC-USD, ETH-USD)",
            },
            "days": {
                "type": "integer",
                "description": "Number of days of history to fetch",
            },
        },
        "required": [],
    },
    handler=get_funding_rate,
)


# Tool: Search crypto news
async def search_crypto_news(keyword: str, page_size: int = 5) -> str:
    """Search crypto news by keyword from SoSoValue."""
    from siglab.data.sosovalue_client import SoSoValueClient
    from siglab.config import load_settings

    settings = load_settings()
    key = settings.sosovalue_api_key_override
    if not key:
        return "Error: No SoSoValue API key configured"
    client = SoSoValueClient(api_key=key)
    rows = await client.news_search(keyword=keyword, page_size=page_size)
    if not rows:
        return f"No news found for keyword '{keyword}'"
    result = [f"News search results for '{keyword}':"]
    for row in rows[:page_size]:
        title = None
        mlc = row.get("multilanguageContent") or []
        if mlc and isinstance(mlc[0], dict):
            title = mlc[0].get("title")
        if not title:
            title = row.get("title")
        if not title:
            content_text = str(row.get("content") or "")
            title = content_text[:80] + "..." if len(content_text) > 80 else content_text
        if not title:
            title = "Untitled"
        result.append(f"  - {title}")
    return "\n".join(result)


SEARCH_NEWS_TOOL = ResearchTool(
    name="search_crypto_news",
    description="Search crypto news by keyword from SoSoValue",
    parameters={
        "type": "object",
        "properties": {
            "keyword": {
                "type": "string",
                "description": "Search keyword for news lookup",
            },
            "page_size": {
                "type": "integer",
                "description": "Number of results (max 10)",
            },
        },
        "required": ["keyword"],
    },
    handler=search_crypto_news,
)


# All tools registry
RESEARCH_TOOLS: list[ResearchTool] = [ETF_FLOW_TOOL, MARKET_DATA_TOOL, NEWS_TOOL, FUNDING_RATE_TOOL, SEARCH_NEWS_TOOL]


def tools_to_anthropic_format(tools: list[ResearchTool]) -> list[dict]:
    """Convert ResearchTool definitions to Anthropic tool calling format."""
    return [
        {
            "name": t.name,
            "description": t.description,
            "input_schema": t.parameters,
        }
        for t in tools
    ]
