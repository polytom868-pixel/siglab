from __future__ import annotations

import asyncio
import logging
import json
import random
import ssl
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import httpx
from siglab.utils import percentile as _percentile

logger = logging.getLogger(__name__)


class SoSoValueApiError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, payload: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


class SoSoValueConfigError(SoSoValueApiError):
    """Missing or invalid local SoSoValue client configuration."""


class SoSoValueAuthError(SoSoValueApiError):
    """Authentication or authorization failed upstream."""


class SoSoValueRateLimitError(SoSoValueApiError):
    """SoSoValue rate limited the request."""

    def __init__(self, message: str, *, status_code: int | None = None, payload: Any = None, retry_after: float | None = None) -> None:
        super().__init__(message, status_code=status_code, payload=payload)
        self.retry_after = retry_after


class SoSoValueTransportError(SoSoValueApiError):
    """Network, DNS, TLS, timeout, or socket transport failure."""


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


@dataclass
class _EndpointMetrics:
    latencies_ms: list[float] = field(default_factory=list)
    attempts: int = 0
    successes: int = 0
    retries: int = 0
    rate_limits: int = 0
    transport_failures: int = 0


class SoSoValueClient:

    def __init__(
        self,
        *,
        api_key: str | None,
        endpoints: SoSoValueEndpoints | None = None,
        timeout_s: float = 10.0,
        connect_timeout_s: float = 3.0,
        write_timeout_s: float = 5.0,
        pool_timeout_s: float = 3.0,
        retries: int = 2,
        max_concurrency: int = 8,
        conservative_rate_limit_per_minute: int = 20,
        verify: ssl.SSLContext | str | bool | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key
        self.endpoints = endpoints or SoSoValueEndpoints()
        self.timeout_s = float(timeout_s)
        self.connect_timeout_s = float(connect_timeout_s)
        self.write_timeout_s = float(write_timeout_s)
        self.pool_timeout_s = float(pool_timeout_s)
        self.retries = max(0, int(retries))
        self.verify = verify
        self._client = client
        self._owns_client = client is None
        self._semaphore = asyncio.Semaphore(max(1, int(max_concurrency)))
        self.conservative_rate_limit_per_minute = int(conservative_rate_limit_per_minute)
        self._rate_limit_events: deque[float] = deque()
        self._rate_limit_lock = asyncio.Lock()
        self._inflight: dict[str, asyncio.Task[dict[str, Any]]] = {}
        self._metrics: dict[str, _EndpointMetrics] = {}

    @property
    def is_configured(self) -> bool:
        return bool(str(self.api_key or "").strip())

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    async def etf_historical_inflow(self, *, etf_type: str = "us-btc-spot") -> list[dict[str, Any]]:
        spec = SoSoValueRequestSpec(
            name="etf.historical_inflow",
            method="POST",
            base_url=self.endpoints.etf_base_url,
            path="/openapi/v2/etf/historicalInflowChart",
            json_body={"type": etf_type},
            ttl_s=300.0,
            required_fields=("date", "total_net_inflow", "total_value_traded", "total_net_assets", "cum_net_inflow"),
            identity_fields=("date",),
            require_non_empty=True,
        )
        payload = await self.request(spec)
        return self._rows_from_data(payload.get("data"), spec)

    async def etf_current_metrics(self, *, etf_type: str = "us-btc-spot") -> dict[str, Any]:
        spec = SoSoValueRequestSpec(
            name="etf.current_metrics",
            method="POST",
            base_url=self.endpoints.etf_base_url,
            path="/openapi/v2/etf/currentEtfDataMetrics",
            json_body={"type": etf_type},
            ttl_s=300.0,
        )
        payload = await self.request(spec)
        data = payload.get("data")
        if not isinstance(data, dict):
            raise SoSoValueApiError("etf.current_metrics data was not an object", payload=data)
        self._validate_etf_current_metrics(data)
        return dict(data)

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

    async def _fetch_featured_news_page(self, *, page_num: int = 1, page_size: int = 10, category_list: list[int] | None = None) -> list[dict[str, Any]]:
        self._validate_news_page_size(page_size)
        params: dict[str, Any] = {
            "pageNum": int(page_num),
            "pageSize": int(page_size),
        }
        if category_list:
            params["categoryList"] = ",".join(str(int(value)) for value in category_list)
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

    async def featured_news_pages(
        self,
        *,
        max_pages: int = 1,
        page_size: int = 10,
        category_list: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        return await self._paginate(
            self._fetch_featured_news_page,
            max_pages,
            page_size=page_size,
            category_list=category_list,
        )

    async def featured_news_by_currency(
        self,
        *,
        page_num: int = 1,
        page_size: int = 10,
        currency_id: int | None = None,
        category_list: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        self._validate_news_page_size(page_size)
        params: dict[str, Any] = {
            "pageNum": int(page_num),
            "pageSize": int(page_size),
        }
        if currency_id is not None:
            params["currencyId"] = int(currency_id)
        if category_list:
            params["categoryList"] = ",".join(str(int(value)) for value in category_list)
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

    async def _paginate(
        self,
        fetch_page: Any,
        max_pages: int,
        **page_kwargs: Any,
    ) -> list[dict[str, Any]]:
        pages = await asyncio.gather(
            *(
                fetch_page(page_num=page_num, **page_kwargs)
                for page_num in range(1, max(1, int(max_pages)) + 1)
            )
        )
        rows: list[dict[str, Any]] = []
        for page_rows in pages:
            if not page_rows:
                break
            rows.extend(page_rows)
        return rows

    async def request(self, spec: SoSoValueRequestSpec) -> dict[str, Any]:
        if not self.is_configured:
            raise SoSoValueConfigError("SOSOVALUE_API_KEY is required for SoSoValue API calls")
        key = self._cache_key(spec)
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.create_task(self._request_uncached(spec, key))
            self._inflight[key] = task
        try:
            return dict(await task)
        finally:
            if self._inflight.get(key) is task:
                self._inflight.pop(key, None)

    async def _request_uncached(self, spec: SoSoValueRequestSpec, cache_key: str) -> dict[str, Any]:
        metrics = self._endpoint_metrics(spec.name)
        last_error: SoSoValueApiError | None = None
        for attempt in range(self.retries + 1):
            metrics.attempts += 1
            started = time.perf_counter()
            try:
                async with self._semaphore:
                    await self._acquire_rate_slot()
                    payload = await self._single_http_attempt(spec)
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                metrics.latencies_ms.append(elapsed_ms)
                metrics.successes += 1
                return payload
            except SoSoValueRateLimitError as exc:
                metrics.rate_limits += 1
                last_error = exc
            except SoSoValueTransportError as exc:
                metrics.transport_failures += 1
                last_error = exc
            except SoSoValueApiError as exc:
                last_error = exc
                if not self._is_retryable(spec.method, exc.status_code):
                    raise
            if attempt >= self.retries:
                break
            metrics.retries += 1
            if isinstance(last_error, SoSoValueRateLimitError) and last_error.retry_after is not None and last_error.retry_after > 0:
                await asyncio.sleep(float(last_error.retry_after))
            else:
                await asyncio.sleep(self._backoff_s(attempt))
        raise last_error or SoSoValueTransportError(f"{spec.name} request failed without a captured error")

    async def _single_http_attempt(self, spec: SoSoValueRequestSpec) -> dict[str, Any]:
        headers = {"x-soso-api-key": str(self.api_key).strip(), "Content-Type": "application/json"}
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
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout, httpx.TimeoutException, OSError, TimeoutError) as exc:
            raise SoSoValueTransportError(f"{spec.name} transport failure: {exc}") from exc
        except httpx.HTTPError as exc:
            raise SoSoValueTransportError(f"{spec.name} HTTP transport failure: {exc}") from exc

        status = int(response.status_code)
        if status in (401, 403):
            raise SoSoValueAuthError(f"{spec.name} auth failed with HTTP {status}", status_code=status)
        if status == 429:
            retry_after: float | None = None
            try:
                retry_after = float(response.headers.get("Retry-After", ""))
            except (ValueError, TypeError):
                pass
            raise SoSoValueRateLimitError(f"{spec.name} rate limited with HTTP 429", status_code=status, retry_after=retry_after)
        if status >= 400:
            raise SoSoValueApiError(f"{spec.name} upstream HTTP {status}", status_code=status)

        try:
            payload = response.json()
        except ValueError as exc:
            raise SoSoValueApiError(f"{spec.name} returned malformed JSON", status_code=status) from exc
        return self._validate_payload(spec, payload, status)

    def _is_retryable(self, method: str, status: int | None) -> bool:
        """Idempotent-only retry: GET/HEAD retry on 429/502/503/504; POST/PUT/DELETE only on 429."""
        if status is None:
            return False
        if status == 429:
            return True
        if method.upper() in ("GET", "HEAD"):
            return status in (502, 503, 504)
        return False

    def _validate_payload(self, spec: SoSoValueRequestSpec, payload: Any, status_code: int) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise SoSoValueApiError(
                f"{spec.name} response was not a JSON object",
                status_code=status_code,
                payload=payload,
            )
        code = payload.get("code")
        if code not in (None, 0, "0"):
            message = str(payload.get("msg") or payload.get("message") or "SoSoValue API returned a non-zero code")
            raise SoSoValueApiError(message, status_code=status_code, payload=payload)
        if "data" in payload:
            self._rows_from_data(payload.get("data"), spec)
        return payload

    def _rows_from_data(self, data: Any, spec: SoSoValueRequestSpec) -> list[dict[str, Any]]:
        if isinstance(data, list):
            rows = [dict(r) for r in data if isinstance(r, dict)]
        elif isinstance(data, dict) and isinstance(data.get("list"), list):
            rows = [dict(r) for r in data["list"] if isinstance(r, dict)]
        elif isinstance(data, dict):
            rows = [dict(data)]
        elif data in (None, ""):
            rows = []
        else:
            raise SoSoValueApiError(f"{spec.name} data had unsupported shape", payload=data)
        if spec.require_non_empty and not rows:
            raise SoSoValueApiError(f"{spec.name} returned empty data", payload=data)
        if spec.identity_fields or spec.required_fields:
            optional = tuple(f for f in spec.required_fields if f not in spec.identity_fields)
            for idx, row in enumerate(rows):
                missing_id = [f for f in spec.identity_fields if f not in row]
                if missing_id:
                    raise SoSoValueApiError(
                        f"{spec.name} row {idx} missing identity fields: {', '.join(missing_id)}", payload=row)
                self._fill_optional_fields(row, optional, f"{spec.name} row {idx}")
        return rows

    def metrics_snapshot(self) -> dict[str, Any]:
        endpoints: dict[str, Any] = {}
        all_latencies: list[float] = []
        totals = {
            "retry_count": 0,
            "attempts": 0,
            "successes": 0,
            "429_count": 0,
            "transport_failures": 0,
        }
        for name, metrics in self._metrics.items():
            latencies = sorted(metrics.latencies_ms)
            all_latencies.extend(latencies)
            totals["retry_count"] += metrics.retries
            totals["attempts"] += metrics.attempts
            totals["successes"] += metrics.successes
            totals["429_count"] += metrics.rate_limits
            totals["transport_failures"] += metrics.transport_failures
            endpoints[name] = {
                "p50_ms": _percentile(latencies, 50),
                "p95_ms": _percentile(latencies, 95),
                "attempts": metrics.attempts,
                "successes": metrics.successes,
                "retry_count": metrics.retries,
                "429_count": metrics.rate_limits,
                "transport_failures": metrics.transport_failures,
            }
        all_latencies.sort()
        attempts = max(1, totals["attempts"])
        return {
            "p50_ms": _percentile(all_latencies, 50),
            "p95_ms": _percentile(all_latencies, 95),
            "retry_count": totals["retry_count"],
            "429_count": totals["429_count"],
            "transport_failures": totals["transport_failures"],
            "success_rate": totals["successes"] / attempts,
            "rate_limit_policy": {
                "scope": "api_key_or_plan",
                "conservative_calls_per_minute": self.conservative_rate_limit_per_minute,
                "source": "https://m.sosovalue.com/developer",
                "enforced": self.conservative_rate_limit_per_minute > 0,
                "used_in_current_window": len(self._rate_limit_events),
                "warning": "SigLab enforces this process-local rolling budget; use a shared limiter when multiple processes share one API key.",
            },
            "endpoints": endpoints,
        }

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                verify=self._verify_config(),
                http2=True,
                limits=httpx.Limits(max_connections=50, max_keepalive_connections=10, keepalive_expiry=30.0),
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
            raise SoSoValueConfigError("SoSoValue news pageSize must be between 1 and 100")

    def _fill_optional_fields(self, row: dict[str, Any], fields: tuple[str, ...], label: str) -> None:
        missing = [f for f in fields if f not in row]
        if missing:
            logger.warning("%s missing optional fields: %s; filling with None", label, ", ".join(missing))
            for f in missing:
                row.setdefault(f, None)

    def _validate_etf_current_metrics(self, data: dict[str, Any]) -> None:
        # Aggregates and most row fields are optional; only `ticker` and a
        # non-empty `list` remain strict. Missing fields degrade to None.
        for agg in ("totalNetAssets", "totalNetAssetsPercentage", "dailyNetInflow",
                    "cumNetInflow", "dailyTotalValueTraded", "totalTokenHoldings"):
            value = data.get(agg)
            if not isinstance(value, dict):
                if value is not None:
                    logger.warning("etf.current_metrics aggregate `%s` was not an object; defaulting to None", agg)
                data[agg] = None
            else:
                self._fill_optional_fields(value, ("value", "lastUpdateDate", "status"),
                                           f"etf.current_metrics aggregate `{agg}`")
        rows = data.get("list")
        if not isinstance(rows, list) or not rows:
            raise SoSoValueApiError("etf.current_metrics returned no ETF rows", payload=data)
        row_optional = ("id", "institute", "netAssets", "netAssetsPercentage", "dailyNetInflow",
                        "cumNetInflow", "dailyValueTraded", "fee", "discountPremiumRate")
        for idx, row in enumerate(rows):
            if not isinstance(row, dict):
                raise SoSoValueApiError(f"etf.current_metrics row {idx} was not an object", payload=row)
            if "ticker" not in row:
                raise SoSoValueApiError(
                    f"etf.current_metrics row {idx} missing identity field: ticker", payload=row)
            self._fill_optional_fields(row, row_optional, f"etf.current_metrics row {idx}")

    def _endpoint_metrics(self, name: str) -> _EndpointMetrics:
        metrics = self._metrics.get(name)
        if metrics is None:
            metrics = _EndpointMetrics()
            self._metrics[name] = metrics
        return metrics

    async def _acquire_rate_slot(self) -> None:
        """Throttle to the conservative per-minute request cap.

        Process-local: coordinates calls within a single client/process only;
        the upstream API-key quota (20 req/min) can still be exceeded when
        multiple processes share one key. The cap is conservative on purpose.
        """
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

    def _backoff_s(self, attempt: int) -> float:
        base = min(2.0, 0.25 * (2**attempt))
        return cast(float, base + random.uniform(0.0, base * 0.25))

    def _verify_config(self) -> ssl.SSLContext | bool:
        if self.verify is not None:
            if isinstance(self.verify, str):
                return ssl.create_default_context(cafile=self.verify)
            return self.verify
        try:
            return ssl.create_default_context()
        except Exception:
            pass
        try:
            import certifi

            path = Path(certifi.where())
            if path.exists():
                return ssl.create_default_context(cafile=str(path))
        except Exception:
            pass
        return True
