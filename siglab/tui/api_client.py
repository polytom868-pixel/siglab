"""FastAPI HTTP client for the SigLab TUI.

Connects to the FastAPI dashboard running on port 3100.
"""

from __future__ import annotations

from typing import Any

import httpx


class TuiApiClient:
    """Async HTTP client for the SigLab FastAPI dashboard.

    Args:
        base_url: Base URL of the FastAPI dashboard (default http://localhost:3100).
        timeout: Request timeout in seconds (default 10).
    """

    def __init__(
        self, base_url: str = "http://localhost:3100", timeout: float = 10.0
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None
        self._timeout = timeout

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
            )
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP client session."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def get_health(self) -> dict[str, Any]:
        """Fetch the /health endpoint.

        Returns:
            Dict with status, version, uptime_seconds fields.

        Raises:
            httpx.HTTPError: If the request fails.
        """
        client = await self._ensure_client()
        response = await client.get("/health")
        response.raise_for_status()
        return response.json()

    async def get_config(self) -> dict[str, Any]:
        """Fetch the /config endpoint.

        Returns:
            Dict with system, sosovalue, claude configuration.

        Raises:
            httpx.HTTPError: If the request fails.
        """
        client = await self._ensure_client()
        response = await client.get("/config")
        response.raise_for_status()
        return response.json()

    async def get_ops_board(self) -> dict[str, Any]:
        """Fetch the /ops-board endpoint.

        Returns:
            Dict with artifact_status, summary, and service_health.

        Raises:
            httpx.HTTPError: If the request fails.
        """
        client = await self._ensure_client()
        response = await client.get("/ops-board")
        response.raise_for_status()
        return response.json()

    async def get_evidence_graph(self) -> dict[str, Any]:
        """Fetch the /evidence-graph endpoint.

        Returns:
            Dict with nodes and edges arrays.

        Raises:
            httpx.HTTPError: If the request fails.
        """
        client = await self._ensure_client()
        response = await client.get("/evidence-graph")
        response.raise_for_status()
        return response.json()

    async def get_skill_report(self) -> dict[str, Any]:
        """Fetch the /skill-report endpoint.

        Returns:
            Dict with per-skill metrics.

        Raises:
            httpx.HTTPError: If the request fails.
        """
        client = await self._ensure_client()
        response = await client.get("/skill-report")
        response.raise_for_status()
        return response.json()

    async def get_risk(self) -> dict[str, Any]:
        """Fetch the /risk endpoint.

        Returns:
            Dict with composite_score, max_drawdown, correlation_matrix.

        Raises:
            httpx.HTTPError: If the request fails.
        """
        client = await self._ensure_client()
        response = await client.get("/risk")
        response.raise_for_status()
        return response.json()
