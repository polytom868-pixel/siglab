"""Tests for TuiApiClient retry logic and _get/_post helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from siglab.tui.api_client import TuiApiClient


def _mock_response(status_code: int = 200, json_data: dict | None = None) -> MagicMock:
    """Build a mock httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {"ok": True}
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"{status_code}",
            request=httpx.Request("GET", "http://test"),
            response=httpx.Response(status_code),
        )
    else:
        resp.raise_for_status = MagicMock()
    return resp


class TestRequestWithRetry:
    """Tests for TuiApiClient._request_with_retry."""

    @pytest.mark.asyncio
    async def test_success_first_try_no_retry(self) -> None:
        """Successful request on first try does not retry."""
        client = TuiApiClient()
        mock_resp = _mock_response(200, {"data": "ok"})

        call_count = 0
        _original_get = httpx.AsyncClient.get
        async def counting_get(self_client, path, **kwargs):
            nonlocal call_count
            call_count += 1
            return mock_resp

        with patch.object(httpx.AsyncClient, "get", counting_get):
            result = await client._request_with_retry("get", "/test")

        assert result == {"data": "ok"}
        assert call_count == 1
        await client.close()

    @pytest.mark.asyncio
    async def test_connect_error_retries_once_then_raises(self) -> None:
        """ConnectError triggers one retry; second failure raises."""
        client = TuiApiClient()

        call_count = 0

        async def failing_get(self_client, path, **kwargs):
            nonlocal call_count
            call_count += 1
            raise httpx.ConnectError("Connection refused")

        with patch.object(httpx.AsyncClient, "get", failing_get):
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                with pytest.raises(httpx.ConnectError):
                    await client._request_with_retry("get", "/test")
                mock_sleep.assert_awaited_once_with(0.5)

        assert call_count == 2
        await client.close()

    @pytest.mark.asyncio
    async def test_5xx_retries_once(self) -> None:
        """5xx error retries once; second success returns data."""
        client = TuiApiClient()

        call_count = 0

        async def flaky_get(self_client, path, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                resp = _mock_response(503)
                return resp
            return _mock_response(200, {"recovered": True})

        with patch.object(httpx.AsyncClient, "get", flaky_get):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await client._request_with_retry("get", "/test")

        assert result == {"recovered": True}
        assert call_count == 2
        await client.close()

    @pytest.mark.asyncio
    async def test_5xx_retries_once_then_raises(self) -> None:
        """Repeated 5xx raises after retry."""
        client = TuiApiClient()

        call_count = 0

        async def always_500(self_client, path, **kwargs):
            nonlocal call_count
            call_count += 1
            return _mock_response(500)

        with patch.object(httpx.AsyncClient, "get", always_500):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                with pytest.raises(httpx.HTTPStatusError):
                    await client._request_with_retry("get", "/test")

        assert call_count == 2
        await client.close()

    @pytest.mark.asyncio
    async def test_4xx_does_not_retry(self) -> None:
        """4xx error raises immediately without retry."""
        client = TuiApiClient()

        call_count = 0

        async def bad_request(self_client, path, **kwargs):
            nonlocal call_count
            call_count += 1
            return _mock_response(400)

        with patch.object(httpx.AsyncClient, "get", bad_request):
            with pytest.raises(httpx.HTTPStatusError):
                await client._request_with_retry("get", "/test")

        assert call_count == 1
        await client.close()

    @pytest.mark.asyncio
    async def test_timeout_retries_once(self) -> None:
        """Timeout triggers one retry; second failure raises."""
        client = TuiApiClient()

        call_count = 0

        async def timeout_get(self_client, path, **kwargs):
            nonlocal call_count
            call_count += 1
            raise httpx.TimeoutException("Timed out")

        with patch.object(httpx.AsyncClient, "get", timeout_get):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                with pytest.raises(httpx.TimeoutException):
                    await client._request_with_retry("get", "/test")

        assert call_count == 2
        await client.close()


class TestGetPostHelpers:
    """Tests for _get and _post convenience methods."""

    @pytest.mark.asyncio
    async def test_get_delegates_to_request_with_retry(self) -> None:
        """_get calls _request_with_retry with 'get' method."""
        client = TuiApiClient()
        mock_resp = _mock_response(200, {"items": [1]})

        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock, return_value=mock_resp):
            result = await client._get("/items", params={"q": "test"})

        assert result == {"items": [1]}
        await client.close()

    @pytest.mark.asyncio
    async def test_post_delegates_to_request_with_retry(self) -> None:
        """_post calls _request_with_retry with 'post' method."""
        client = TuiApiClient()
        mock_resp = _mock_response(200, {"created": True})

        async def mock_post(self_client, path, **kwargs):
            assert kwargs.get("json") == {"name": "test"}
            return mock_resp

        with patch.object(httpx.AsyncClient, "post", mock_post):
            result = await client._post("/items", json={"name": "test"})

        assert result == {"created": True}
        await client.close()

    @pytest.mark.asyncio
    async def test_post_retries_on_connect_error(self) -> None:
        """_post inherits retry behavior from _request_with_retry."""
        client = TuiApiClient()

        call_count = 0

        async def failing_post(self_client, path, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.ConnectError("refused")
            return _mock_response(200, {"done": True})

        with patch.object(httpx.AsyncClient, "post", failing_post):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await client._post("/items", json={})

        assert result == {"done": True}
        assert call_count == 2
        await client.close()
