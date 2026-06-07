"""Small HTTP helper shared by provider adapters.

Wraps httpx with sane timeouts and a bounded retry/backoff on 429 and 5xx. The
transport is injectable so adapters can be unit-tested offline (httpx.MockTransport).
Deeper resilience (jitter, circuit-breaking) is Phase 6.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from .base import ProviderUnavailableError, RateLimitError

logger = logging.getLogger("sportsup.http")


@dataclass
class HttpResponse:
    status_code: int
    data: Any


class HttpClient:
    def __init__(
        self,
        base_url: str,
        headers: dict[str, str] | None = None,
        *,
        timeout: float = 15.0,
        max_retries: int = 3,
        backoff_base: float = 1.5,
        transport: httpx.BaseTransport | None = None,
        sleep=time.sleep,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url,
            headers=headers or {},
            timeout=timeout,
            transport=transport,
        )
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._sleep = sleep

    def request(
        self, method: str, path: str, *, params: dict[str, Any] | None = None, json: Any = None
    ) -> HttpResponse:
        """Issue a request, retrying on network errors, 429, and 5xx. 4xx and 2xx are
        returned to the caller (so POST callers can inspect API error bodies)."""
        last_exc: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                resp = self._client.request(method, path, params=params, json=json)
            except httpx.HTTPError as exc:  # network/DNS/timeout
                last_exc = ProviderUnavailableError(f"request to {path} failed: {exc}")
                self._backoff(attempt)
                continue

            if resp.status_code == 429:
                retry_after = _retry_after_seconds(resp)
                last_exc = RateLimitError(f"rate limited on {path}", retry_after=retry_after)
                self._backoff(attempt, retry_after)
                continue
            if resp.status_code >= 500:
                last_exc = ProviderUnavailableError(f"server error {resp.status_code} on {path}")
                self._backoff(attempt)
                continue

            try:
                data = resp.json()
            except ValueError:
                data = None
            return HttpResponse(resp.status_code, data)

        assert last_exc is not None
        raise last_exc

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        r = self.request("GET", path, params=params)
        if r.status_code in (401, 403):
            raise ProviderUnavailableError(f"auth failed ({r.status_code}) on {path} — check API key")
        if r.status_code >= 400:
            raise ProviderUnavailableError(f"HTTP {r.status_code} on {path}")
        return r.data

    def post_json(self, path: str, json: Any) -> HttpResponse:
        """POST returning status + parsed body without raising on 4xx."""
        return self.request("POST", path, json=json)

    def _backoff(self, attempt: int, retry_after: float | None = None) -> None:
        if attempt >= self._max_retries - 1:
            return
        delay = retry_after if retry_after is not None else self._backoff_base ** attempt
        logger.debug("backing off %.1fs before retry %d", delay, attempt + 1)
        self._sleep(delay)

    def close(self) -> None:
        self._client.close()


def _retry_after_seconds(resp: httpx.Response) -> float | None:
    value = resp.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None
