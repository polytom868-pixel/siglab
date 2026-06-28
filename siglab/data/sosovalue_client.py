"""SoSoValue API client for ETF, news, and crypto data."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import ssl
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from siglab.data.provider_base import DataProvider, CircuitBreakerOpenError

logger = logging.getLogger(__name__)


def _fast_json_loads(data: bytes) -> Any:
    try:
        import orjson

        return orjson.loads(data)
    except ImportError:
        return json.loads(data)


class SoSoValueApiError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        payload: object = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


class SoSoValueConfigError(SoSoValueApiError):
    pass


class SoSoValueAuthError(SoSoValueApiError):
    pass


class SoSoValueRateLimitError(SoSoValueApiError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        payload: object = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message, status_code=status_code, payload=payload)
        self.retry_after = retry_after


class SoSoValueTransportError(SoSoValueApiError):
    pass


@dataclass(frozen=True)
class SoSoValueEndpoints:
    openapi_base_url: str = "https://openapi.sosovalue.com/openapi/v1"
    etf_base_url: str = "https://openapi.sosovalue.com"
    news_base_url: str = "https://openapi.sosovalue.com"


@dataclass(frozen=True)
class SoSoValueRequestSpec:
    name: str
    method: str
    base_url: str
    path: str
    params: dict[str, Any] | None = None
    json_body: dict[str, Any] | None = None
    ttl_s: float = 0.0
    required_fields: tuple[str, ...] = ()
    identity_fields: tuple[str, ...] = ()
    require_non_empty: bool = False


class SoSoValueClient(DataProvider):
    """Client for SoSoValue's REST API (crypto prices, ETF data, news)."""

    def __init__(
        self,
        *,
        api_key: str | None,
        endpoints: SoSoValueEndpoints | None = None,
        timeout_s: float = 10.0,
        connect_timeout_s: float = 3.0,
        write_timeout_s: float = 5.0,
        pool_timeout_s: float = 3.0,
        retries: int = 4,
        max_concurrency: int = 8,
        conservative_rate_limit_per_minute: int = 10,
        verify: ssl.SSLContext | str | bool | None = None,
        client: httpx.AsyncClient | None = None,
        cache_enabled: bool = True,
    ) -> None:
        super().__init__(retries=retries)
        self.api_key = api_key
        self.endpoints = endpoints or SoSoValueEndpoints()
        self.timeout_s = float(timeout_s)
        self.connect_timeout_s = float(connect_timeout_s)
        self.write_timeout_s = float(write_timeout_s)
        self.pool_timeout_s = float(pool_timeout_s)
        self.verify = verify
        self._client = client
        self._owns_client = client is None
        self._semaphore = asyncio.Semaphore(max(1, int(max_concurrency)))
        self.conservative_rate_limit_per_minute = int(conservative_rate_limit_per_minute)
        self._rate_limit_events: deque[float] = deque()
        self._rate_limit_lock = asyncio.Lock()
        self._inflight: dict[str, asyncio.Task[dict[str, Any]]] = {}
        self._response_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._max_cache_size: int = 500
        self._cache_enabled: bool = bool(cache_enabled)
        self._cache_hits: int = 0
        self._cache_misses: int = 0

    @property
    def is_configured(self) -> bool:
        return bool(str(self.api_key or "").strip())

    async def close(self) -> None:
        if self._cache_hits + self._cache_misses > 0:
            hit_rate = self._cache_hits / max(1, self._cache_hits + self._cache_misses)
            logger.debug(
                "sosovalue_cache cache_enabled=%s hits=%d misses=%d hit_rate=%.1f%% size=%d",
                self._cache_enabled,
                self._cache_hits,
                self._cache_misses,
                hit_rate * 100.0,
                len(self._response_cache),
            )
        if self._owns_client and self._client is not None:
            await self._client.aclose()
        await super().close()

    # --- Public API methods ---

    async def request(self, spec: SoSoValueRequestSpec) -> dict[str, Any]:
        if not self.is_configured:
            raise SoSoValueConfigError(
                "SOSOVALUE_API_KEY is required for SoSoValue API calls",
            )
        key = self._cache_key(spec)
        if self._cache_enabled and spec.ttl_s > 0:
            cached = self._response_cache.get(key)
            if cached is not None:
                expiry, payload = cached
                if time.monotonic() < expiry:
                    self._cache_hits += 1
                    return payload
                self._response_cache.pop(key, None)
        self._cache_misses += 1
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.create_task(self._request_uncached(spec, key))
            self._inflight[key] = task
        try:
            return await task
        finally:
            if self._inflight.get(key) is task:
                self._inflight.pop(key, None)

    async def _request_uncached(
        self,
        spec: SoSoValueRequestSpec,
        cache_key: str,
    ) -> dict[str, Any]:
        last_error: SoSoValueApiError | None = None
        for attempt in range(self.retries + 1):
            metrics = self._metrics_store.get(spec.name)
            if metrics:
                metrics.attempts += 1
            else:
                m = self._metrics_for(spec.name)
                m.attempts += 1

            started = time.perf_counter()
            try:
                async with self._semaphore:
                    await self._acquire_rate_slot()
                    # Check circuit breaker before the HTTP call
                    self._circuit_breaker.acquire(spec.name)
                    payload = await self._single_http_attempt(spec)
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                # Record success
                m2 = self._metrics_for(spec.name)
                m2.latencies_ms.append(elapsed_ms)
                m2.successes += 1
                self._circuit_breaker.on_success(spec.name)
                if self._cache_enabled and spec.ttl_s > 0 and cache_key:
                    self._store_in_cache(cache_key, payload, spec.ttl_s)
                return payload
            except CircuitBreakerOpenError:
                m3 = self._metrics_for(spec.name)
                m3.circuit_breaks += 1
                raise
            except SoSoValueRateLimitError as exc:
                m4 = self._metrics_for(spec.name)
                m4.rate_limits += 1
                last_error = exc
            except SoSoValueTransportError as exc:
                m5 = self._metrics_for(spec.name)
                m5.transport_failures += 1
                last_error = exc
            except SoSoValueApiError as exc:
                last_error = exc
                if not self._is_retryable(spec.method, exc.status_code):
                    raise
            if attempt >= self.retries:
                break
            m6 = self._metrics_for(spec.name)
            m6.retries += 1
            self._circuit_breaker.on_failure(spec.name)
            if (
                isinstance(last_error, SoSoValueRateLimitError)
                and last_error.retry_after is not None
                and (last_error.retry_after > 0)
            ):
                await asyncio.sleep(float(last_error.retry_after))
            else:
                await asyncio.sleep(self._backoff_s(attempt))
        raise last_error or SoSoValueTransportError(
            f"{spec.name} request failed without a captured error",
        )

    async def _single_http_attempt(self, spec: SoSoValueRequestSpec) -> dict[str, Any]:
        headers = {
            "x-soso-api-key": str(self.api_key).strip(),
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        }
        url = self._url(spec.base_url, spec.path)
        try:
            response = await self._http().request(
                spec.method.upper(),
                url,
                params=spec.params,
                json=spec.json_body,
                headers=headers,
                timeout=self._timeout(),
            )
        except (
            httpx.ConnectError,
            httpx.ReadTimeout,
            httpx.WriteTimeout,
            httpx.PoolTimeout,
            httpx.TimeoutException,
            OSError,
            TimeoutError,
        ) as exc:
            raise SoSoValueTransportError(
                f"{spec.name} transport failure: {exc}",
            ) from exc
        except httpx.HTTPError as exc:
            raise SoSoValueTransportError(
                f"{spec.name} HTTP transport failure: {exc}",
            ) from exc
        status = int(response.status_code)
        if status in (401, 403):
            raise SoSoValueAuthError(
                f"{spec.name} auth failed with HTTP {status}",
                status_code=status,
            )
        if status == 429:
            retry_after: float | None = None
            with contextlib.suppress(ValueError, TypeError):
                retry_after = float(response.headers.get("Retry-After", ""))
            raise SoSoValueRateLimitError(
                f"{spec.name} rate limited with HTTP 429",
                status_code=status,
                retry_after=retry_after,
            )
        if status >= 400:
            raise SoSoValueApiError(
                f"{spec.name} upstream HTTP {status}",
                status_code=status,
            )
        try:
            payload = _fast_json_loads(response.content)
        except ValueError as exc:
            raise SoSoValueApiError(
                f"{spec.name} returned malformed JSON",
                status_code=status,
            ) from exc
        return self._validate_payload(spec, payload, status)

    def _is_retryable(self, method: str, status: int | None) -> bool:
        if status is None:
            return False
        if status == 429:
            return True
        if method.upper() in ("GET", "HEAD"):
            return status in (502, 503, 504)
        return False

    def _validate_payload(
        self,
        spec: SoSoValueRequestSpec,
        payload: object,
        status_code: int,
    ) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise SoSoValueApiError(
                f"{spec.name} response was not a JSON object",
                status_code=status_code,
                payload=payload,
            )
        code = payload.get("code")
        if code not in (None, 0, "0"):
            message = str(
                payload.get("msg")
                or payload.get("message")
                or "SoSoValue API returned a non-zero code",
            )
            raise SoSoValueApiError(message, status_code=status_code, payload=payload)
        return payload

    def _rows_from_data(
        self,
        data: object,
        spec: SoSoValueRequestSpec,
    ) -> list[dict[str, Any]]:
        needs_validation = bool(spec.identity_fields or spec.required_fields)
        if isinstance(data, list):
            if not needs_validation and all(isinstance(r, dict) for r in data):
                if spec.require_non_empty and (not data):
                    raise SoSoValueApiError(
                        f"{spec.name} returned empty data",
                        payload=data,
                    )
                return data
            rows = [dict(r) for r in data if isinstance(r, dict)]
        elif isinstance(data, dict) and isinstance(data.get("list"), list):
            lst = data["list"]
            if not needs_validation and all(isinstance(r, dict) for r in lst):
                if spec.require_non_empty and (not lst):
                    raise SoSoValueApiError(
                        f"{spec.name} returned empty data",
                        payload=data,
                    )
                return lst
            rows = [dict(r) for r in lst if isinstance(r, dict)]
        elif isinstance(data, dict):
            if not needs_validation:
                if spec.require_non_empty:
                    raise SoSoValueApiError(
                        f"{spec.name} returned empty data",
                        payload=data,
                    )
                return [data]
            rows = [dict(data)]
        elif data in (None, ""):
            rows = []
        else:
            raise SoSoValueApiError(
                f"{spec.name} data had unsupported shape",
                payload=data,
            )
        if spec.require_non_empty and (not rows):
            raise SoSoValueApiError(f"{spec.name} returned empty data", payload=data)
        if needs_validation:
            optional = tuple(
                f for f in spec.required_fields if f not in spec.identity_fields
            )
            for idx, row in enumerate(rows):
                missing_id = [f for f in spec.identity_fields if f not in row]
                if missing_id:
                    raise SoSoValueApiError(
                        f"{spec.name} row {idx} missing identity fields: {', '.join(missing_id)}",
                        payload=row,
                    )
                self._fill_optional_fields(row, optional, f"{spec.name} row {idx}")
        return rows

    def _paginate(
        self,
        fetch_page: Any,
        max_pages: int,
        **page_kwargs: Any,
    ) -> asyncio.Task[list[dict[str, Any]]]:
        async def _run() -> list[dict[str, Any]]:
            pages = await asyncio.gather(
                *(
                    fetch_page(page_num=page_num, **page_kwargs)
                    for page_num in range(1, max(1, int(max_pages)) + 1)
                ),
            )
            rows: list[dict[str, Any]] = []
            for page_rows in pages:
                if not page_rows:
                    break
                rows.extend(page_rows)
            return rows
        return asyncio.ensure_future(_run())

    # --- ETF methods ---

    async def etf_historical_inflow(
        self,
        *,
        etf_type: str = "us-btc-spot",
    ) -> list[dict[str, Any]]:
        spec = SoSoValueRequestSpec(
            name="etf.historical_inflow",
            method="POST",
            base_url=self.endpoints.etf_base_url,
            path="/openapi/v2/etf/historicalInflowChart",
            json_body={"type": etf_type},
            ttl_s=300.0,
            required_fields=(
                "date",
                "totalNetInflow",
                "totalValueTraded",
                "totalNetAssets",
                "cumNetInflow",
            ),
            identity_fields=("date",),
            require_non_empty=True,
        )
        payload = await self.request(spec)
        return self._rows_from_data(payload.get("data"), spec)

    # --- Currency methods ---

    async def listed_currencies(self) -> list[dict[str, Any]]:
        spec = SoSoValueRequestSpec(
            name="currency.list",
            method="POST",
            base_url=self.endpoints.openapi_base_url,
            path="/data/default/coin/list",
            json_body={},
            ttl_s=86400.0,
            require_non_empty=True,
        )
        payload = await self.request(spec)
        return self._rows_from_data(payload.get("data"), spec)

    async def currency_market_snapshot(self, currency_id: int) -> dict[str, Any]:
        spec = SoSoValueRequestSpec(
            name="currency.market_snapshot",
            method="GET",
            base_url=self.endpoints.openapi_base_url,
            path=f"/currencies/{currency_id}/market-snapshot",
            ttl_s=60.0,
        )
        return await self.request(spec)

    async def currency_klines(
        self,
        currency_id: int,
        interval: str = "1d",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        spec = SoSoValueRequestSpec(
            name="currency.klines",
            method="GET",
            base_url=self.endpoints.openapi_base_url,
            path=f"/currencies/{currency_id}/klines",
            params={"interval": interval, "limit": limit},
            ttl_s=60.0,
        )
        payload = await self.request(spec)
        return (payload or {}).get("data") or []

    # --- ETF list/methods ---

    async def etf_list(self, symbol: str = "", country_code: str = "") -> list[dict[str, Any]]:
        spec = SoSoValueRequestSpec(
            name="etf.list",
            method="GET",
            base_url=self.endpoints.openapi_base_url,
            path="/etfs",
            params={"symbol": symbol, "country_code": country_code},
            ttl_s=300.0,
        )
        payload = await self.request(spec)
        return (payload or {}).get("data") or []

    async def etf_summary_history(self, symbol: str, country_code: str = "") -> list[dict[str, Any]]:
        spec = SoSoValueRequestSpec(
            name="etf.summary_history",
            method="GET",
            base_url=self.endpoints.openapi_base_url,
            path="/etfs/summary-history",
            params={"symbol": symbol, "country_code": country_code},
            ttl_s=300.0,
        )
        payload = await self.request(spec)
        return (payload or {}).get("data") or []

    async def etf_market_snapshot(self, ticker: str) -> dict[str, Any]:
        spec = SoSoValueRequestSpec(
            name="etf.market_snapshot",
            method="GET",
            base_url=self.endpoints.openapi_base_url,
            path=f"/etfs/{ticker}/market-snapshot",
            ttl_s=300.0,
        )
        return await self.request(spec)

    # --- News methods ---

    def _build_news_params(
        self,
        *,
        page_num: int,
        page_size: int,
        currency_id: int | None = None,
        category_list: list[int] | None = None,
    ) -> dict[str, Any]:
        self._validate_news_page_size(page_size)
        params: dict[str, Any] = {"pageNum": int(page_num), "pageSize": int(page_size)}
        if currency_id is not None:
            params["currencyId"] = int(currency_id)
        if category_list:
            params["categoryList"] = ",".join(
                str(int(value)) for value in category_list
            )
        return params

    async def featured_news(
        self,
        page_num: int = 1,
        page_size: int = 10,
    ) -> list[dict[str, Any]]:
        return await self._fetch_featured_news_page(
            page_num=page_num,
            page_size=page_size,
        )

    async def _fetch_featured_news_page(
        self,
        *,
        page_num: int = 1,
        page_size: int = 10,
        category_list: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        params = self._build_news_params(
            page_num=page_num,
            page_size=page_size,
            category_list=category_list,
        )
        spec = SoSoValueRequestSpec(
            name="news.featured",
            method="GET",
            base_url=self.endpoints.news_base_url,
            path="/api/v1/news/featured",
            params=params,
            ttl_s=60.0,
        )
        payload = await self.request(spec)
        return self._rows_from_data(payload.get("data"), spec)

    async def news_search(self, keyword: str, page_num: int = 1, page_size: int = 10) -> list[dict[str, Any]]:
        spec = SoSoValueRequestSpec(
            name="news.search",
            method="GET",
            base_url=self.endpoints.news_base_url,
            path="/api/v1/news/search",
            params={"keyword": keyword, "pageNum": page_num, "pageSize": page_size},
            ttl_s=60.0,
        )
        payload = await self.request(spec)
        return (payload or {}).get("data", {}).get("list") or []

    async def featured_news_pages(
        self,
        *,
        max_pages: int = 1,
        page_size: int = 10,
        category_list: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        fetch = await self._paginate(
            self._fetch_featured_news_page,
            max_pages,
            page_size=page_size,
            category_list=category_list,
        )
        return fetch

    async def featured_news_by_currency(
        self,
        *,
        page_num: int = 1,
        page_size: int = 10,
        currency_id: int | None = None,
        category_list: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        params = self._build_news_params(
            page_num=page_num,
            page_size=page_size,
            currency_id=currency_id,
            category_list=category_list,
        )
        spec = SoSoValueRequestSpec(
            name="news.featured_by_currency",
            method="GET",
            base_url=self.endpoints.news_base_url,
            path="/api/v1/news/featured/currency",
            params=params,
            ttl_s=60.0,
        )
        payload = await self.request(spec)
        return self._rows_from_data(payload.get("data"), spec)

    async def featured_news_by_currency_pages(
        self,
        *,
        max_pages: int = 1,
        page_size: int = 10,
        currency_id: int | None = None,
        category_list: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        return await self._paginate(
            self.featured_news_by_currency,
            max_pages,
            page_size=page_size,
            currency_id=currency_id,
            category_list=category_list,
        )

    # --- Internal helpers ---

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                verify=self._verify_config(),
                http2=True,
                limits=httpx.Limits(
                    max_connections=100,
                    max_keepalive_connections=20,
                    keepalive_expiry=60.0,
                ),
            )
        return self._client

    def _timeout(self) -> httpx.Timeout:
        return httpx.Timeout(
            timeout=self.timeout_s,
            connect=self.connect_timeout_s,
            read=self.timeout_s,
            write=self.write_timeout_s,
            pool=self.pool_timeout_s,
        )

    def _url(self, base_url: str, path: str) -> str:
        return f"{base_url.rstrip('/')}/{path.lstrip('/')}"

    def _cache_key(self, spec: SoSoValueRequestSpec) -> str:
        return json.dumps(
            {
                "method": spec.method.upper(),
                "url": self._url(spec.base_url, spec.path),
                "params": spec.params or {},
                "json": spec.json_body or {},
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    def _validate_news_page_size(self, page_size: int) -> None:
        if int(page_size) < 1 or int(page_size) > 100:
            raise SoSoValueConfigError(
                "SoSoValue news pageSize must be between 1 and 100",
            )

    def _fill_optional_fields(
        self,
        row: dict[str, Any],
        fields: tuple[str, ...],
        label: str,
    ) -> None:
        missing = [f for f in fields if f not in row]
        if missing:
            logger.warning(
                "%s missing optional fields: %s; filling with None",
                label,
                ", ".join(missing),
            )
            for f in missing:
                row.setdefault(f, None)

    async def _acquire_rate_slot(self) -> None:
        limit = int(self.conservative_rate_limit_per_minute)
        if limit <= 0:
            return
        async with self._rate_limit_lock:
            while True:
                now = time.monotonic()
                cutoff = now - 60.0
                while self._rate_limit_events and self._rate_limit_events[0] <= cutoff:
                    self._rate_limit_events.popleft()
                if len(self._rate_limit_events) < limit:
                    self._rate_limit_events.append(now)
                    return
                sleep_for = max(0.0, 60.0 - (now - self._rate_limit_events[0]))
                await asyncio.sleep(sleep_for)

    def _store_in_cache(self, key: str, payload: dict[str, Any], ttl_s: float) -> None:
        expiry = time.monotonic() + ttl_s
        if len(self._response_cache) >= self._max_cache_size:
            with contextlib.suppress(StopIteration):
                self._response_cache.pop(next(iter(self._response_cache)), None)
        self._response_cache[key] = (expiry, payload)

    def _verify_config(self) -> ssl.SSLContext | bool:
        if self.verify is not None:
            if isinstance(self.verify, str):
                return ssl.create_default_context(cafile=self.verify)
            return self.verify
        try:
            return ssl.create_default_context()
        except (ssl.SSLError, OSError):
            logger.debug("ssl.create_default_context() failed, trying certifi fallback")
        try:
            import certifi

            path = Path(certifi.where())
            if path.exists():
                return ssl.create_default_context(cafile=str(path))
        except (ssl.SSLError, OSError, ImportError, FileNotFoundError):
            logger.debug(
                "certifi SSL context creation also failed, disabling verification",
            )
        return True
