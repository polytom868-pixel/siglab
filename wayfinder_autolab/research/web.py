from __future__ import annotations

import asyncio
import json
import re
from html import unescape
from typing import Any
from urllib.parse import urlparse

import httpx

from wayfinder_autolab.data.lake import ParquetLake
from wayfinder_autolab.llm import KimiTool
from wayfinder_autolab.models import CandidateGraph
from wayfinder_autolab.settings import AutolabSettings

_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_WS_RE = re.compile(r"\s+")


class WebResearcher:
    def __init__(self, settings: AutolabSettings, lake: ParquetLake) -> None:
        self.settings = settings
        self.lake = lake
        self.http = httpx.AsyncClient(
            timeout=httpx.Timeout(20.0),
            headers={"User-Agent": "wayfinder-autolab-web-research/0.1"},
            follow_redirects=True,
        )

    @property
    def is_configured(self) -> bool:
        return bool(self.settings.tavily_api_key)

    async def close(self) -> None:
        await self.http.aclose()

    def kimi_tools(self) -> list[KimiTool]:
        if not self.is_configured:
            return []
        return [
            KimiTool(
                name="tavily_search",
                description=(
                    "Search the public web for up-to-date strategy, protocol, or market "
                    "research and return concise results with sources."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query."},
                        "max_results": {
                            "type": "integer",
                            "description": "How many result rows to return, between 1 and 5.",
                            "minimum": 1,
                            "maximum": 5,
                        },
                    },
                    "required": ["query"],
                },
                handler=self._tool_tavily_search,
            ),
            KimiTool(
                name="web_fetch",
                description=(
                    "Fetch and summarize a specific public URL. Use this after search when "
                    "a source page needs closer inspection."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "Public http or https URL to inspect.",
                        }
                    },
                    "required": ["url"],
                },
                handler=self._tool_web_fetch,
            ),
        ]

    async def build_context(
        self,
        *,
        track: str,
        parent: CandidateGraph,
        research_summary: dict[str, Any],
        recent_results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not self.is_configured:
            return {
                "enabled": False,
                "provider": "disabled",
                "queries": [],
                "reports": [],
            }

        queries = self._build_queries(
            track=track,
            parent=parent,
            research_summary=research_summary,
            recent_results=recent_results,
        )
        reports = await asyncio.gather(
            *[self._research_query(query) for query in queries],
            return_exceptions=True,
        )

        cleaned_reports: list[dict[str, Any]] = []
        for report in reports:
            if isinstance(report, Exception):
                continue
            cleaned_reports.append(report)

        return {
            "enabled": True,
            "provider": "tavily+web",
            "queries": queries,
            "reports": cleaned_reports,
        }

    def _build_queries(
        self,
        *,
        track: str,
        parent: CandidateGraph,
        research_summary: dict[str, Any],
        recent_results: list[dict[str, Any]],
    ) -> list[str]:
        symbols = ", ".join(parent.universe.basis_groups[:4] or ["BTC", "ETH", "SOL"])
        feature_hint = ", ".join(parent.features[:4]) or "funding, momentum, carry"
        failure_hints = ", ".join(
            sorted(
                {
                    reason
                    for row in recent_results
                    for reason in row.get("summary", {}).get("gate_reasons", [])
                }
            )
        )

        if track == "directional_perps":
            queries = [
                (
                    "systematic perpetual futures trading strategy price momentum funding overlay "
                    f"long short indicators risk management {symbols}"
                ),
                (
                    "perp trading regime filters funding momentum trend reversal "
                    f"systematic strategy research {feature_hint}"
                ),
            ]
        else:
            carry_markets = research_summary.get("pt_rotation_markets") or research_summary.get("lending_markets") or []
            market_hint = ", ".join(
                str(row.get("market") or row.get("basis_symbol") or "")
                for row in carry_markets[:3]
                if row
            )
            queries = [
                (
                    "systematic yield rotation strategy pendle PT lending carry "
                    f"hedged basis risk management {symbols} {market_hint}"
                ),
                (
                    "systematic carry strategy lend borrow supply APY principal token "
                    f"ranking hedge rebalancing research {feature_hint}"
                ),
            ]

        if failure_hints:
            queries.append(
                f"{parent.family} systematic strategy improve {failure_hints} avoid look ahead bias"
            )

        deduped: list[str] = []
        seen: set[str] = set()
        for query in queries:
            clean = _collapse_ws(query)
            if clean not in seen:
                deduped.append(clean)
                seen.add(clean)
            if len(deduped) >= 3:
                break
        return deduped

    async def _research_query(self, query: str) -> dict[str, Any]:
        cache_key = f"tavily_search__{_safe_key(query)}"
        cached = self.lake.latest_json("web_research", cache_key, max_age_hours=12)
        if cached:
            return dict(cached)

        response = await self._tavily_search(query)
        top_results = list(response.get("results") or [])[: self.settings.web_explore_results_per_query]
        explored = await asyncio.gather(
            *[
                self._explore_result(result)
                for result in top_results
            ],
            return_exceptions=True,
        )

        report = {
            "query": query,
            "answer": _compact_text(response.get("answer") or "", 320),
            "insights": self._build_insights(
                answer=response.get("answer"),
                results=list(response.get("results") or []),
                explored=[row for row in explored if isinstance(row, dict)],
            ),
            "sources": [
                {
                    "title": row.get("title"),
                    "url": row.get("url"),
                    "domain": _domain(row.get("url")),
                }
                for row in list(response.get("results") or [])[: self.settings.tavily_max_results]
            ],
            "request_id": response.get("request_id"),
            "usage": response.get("usage"),
        }
        self.lake.write_json("web_research", cache_key, report)
        return report

    async def _tool_tavily_search(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = _collapse_ws(str(arguments.get("query") or ""))
        if not query:
            return {"ok": False, "error": "query is required"}

        report = await self._research_query(query)
        max_results = max(1, min(5, int(arguments.get("max_results", 3))))
        return {
            "ok": True,
            "query": report.get("query"),
            "answer": report.get("answer"),
            "insights": list(report.get("insights") or [])[:5],
            "sources": list(report.get("sources") or [])[:max_results],
        }

    async def _tool_web_fetch(self, arguments: dict[str, Any]) -> dict[str, Any]:
        url = str(arguments.get("url") or "").strip()
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return {"ok": False, "error": "url must be a public http or https URL"}
        result = await self._explore_result({"url": url})
        if not result:
            return {"ok": False, "error": "unable to fetch url", "url": url}
        return {
            "ok": True,
            "url": result.get("url"),
            "domain": result.get("domain"),
            "title": result.get("title"),
            "source": result.get("source"),
            "excerpt": result.get("excerpt"),
        }

    async def _tavily_search(self, query: str) -> dict[str, Any]:
        payload = {
            "query": query,
            "topic": "general",
            "search_depth": "advanced",
            "max_results": self.settings.tavily_max_results,
            "chunks_per_source": 3,
            "include_answer": "advanced",
            "include_raw_content": "markdown",
            "include_favicon": True,
        }
        response = await self.http.post(
            f"{self.settings.tavily_base_url.rstrip('/')}/search",
            headers={
                "Authorization": f"Bearer {self.settings.tavily_api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
        return response.json()

    async def _explore_result(self, result: dict[str, Any]) -> dict[str, Any]:
        url = str(result.get("url") or "")
        raw_content = _compact_text(result.get("raw_content") or "", 1400)
        if raw_content:
            return {
                "url": url,
                "domain": _domain(url),
                "title": result.get("title"),
                "source": "tavily_raw_content",
                "excerpt": raw_content,
            }

        if not url:
            return {}

        try:
            response = await self.http.get(url)
            response.raise_for_status()
        except Exception:
            return {
                "url": url,
                "domain": _domain(url),
                "title": result.get("title"),
                "source": "unavailable",
                "excerpt": _compact_text(result.get("content") or "", 280),
            }

        content_type = response.headers.get("Content-Type", "")
        text = response.text
        if "html" in content_type.lower():
            title_match = _TITLE_RE.search(text)
            title = _collapse_ws(unescape(title_match.group(1))) if title_match else str(result.get("title") or "")
            clean_text = _html_to_text(text)
        else:
            title = str(result.get("title") or "")
            clean_text = _collapse_ws(text)

        return {
            "url": url,
            "domain": _domain(url),
            "title": title,
            "source": "direct_fetch",
            "excerpt": _compact_text(clean_text, 1000),
        }

    def _build_insights(
        self,
        *,
        answer: Any,
        results: list[dict[str, Any]],
        explored: list[dict[str, Any]],
    ) -> list[str]:
        insights: list[str] = []
        if answer:
            insights.append(_compact_text(str(answer), 260))

        for row in results[:2]:
            title = str(row.get("title") or _domain(row.get("url")))
            snippet = _compact_text(row.get("content") or row.get("raw_content") or "", 220)
            if snippet:
                insights.append(f"{title}: {snippet}")

        for row in explored[:2]:
            title = str(row.get("title") or row.get("domain") or "source")
            excerpt = _compact_text(row.get("excerpt") or "", 220)
            if excerpt:
                insights.append(f"{title}: {excerpt}")

        deduped: list[str] = []
        seen: set[str] = set()
        for insight in insights:
            if insight not in seen:
                deduped.append(insight)
                seen.add(insight)
            if len(deduped) >= 5:
                break
        return deduped


def _safe_key(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "query"


def _domain(url: str | None) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    return parsed.netloc.lower()


def _collapse_ws(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()


def _compact_text(text: str, limit: int) -> str:
    collapsed = _collapse_ws(unescape(str(text)))
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"


def _html_to_text(html: str) -> str:
    cleaned = _SCRIPT_STYLE_RE.sub(" ", html)
    cleaned = _TAG_RE.sub(" ", cleaned)
    return _collapse_ws(unescape(cleaned))
