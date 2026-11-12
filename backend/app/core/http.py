"""Shared async HTTP helpers: a pooled client plus bounded retries."""

import asyncio
import logging
import random
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import httpx

logger = logging.getLogger("voicekyc.http")

# One client per process. Reusing it avoids a TLS handshake on every provider
# call. Tests that never run the lifespan fall back to a short-lived client.
_shared_client: httpx.AsyncClient | None = None


def set_shared_client(client: httpx.AsyncClient | None) -> None:
    global _shared_client
    _shared_client = client


@asynccontextmanager
async def http_client(timeout: float) -> AsyncIterator[httpx.AsyncClient]:
    if _shared_client is not None:
        yield _shared_client
        return
    async with httpx.AsyncClient(timeout=timeout) as client:
        yield client

# Transient upstream failures worth retrying. A 4xx (other than 408/429) is a
# caller error and must not be retried.
RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}
RETRYABLE_EXCEPTIONS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.RemoteProtocolError,
)


async def request_with_retries(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    attempts: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 4.0,
    provider: str = "upstream",
    **kwargs: Any,
) -> httpx.Response:
    """Send a request, retrying transient network errors and 5xx/429 responses.

    Returns the final response (including a non-2xx one) so callers can raise a
    provider-specific error with the original body. Raises the last transport
    exception only when every attempt fails.
    """
    attempts = max(1, attempts)
    last_error: Exception | None = None
    last_status: int | None = None

    for attempt in range(1, attempts + 1):
        try:
            response = await client.request(method, url, **kwargs)
        except RETRYABLE_EXCEPTIONS as exc:
            last_error = exc
            last_status = None
        else:
            if response.status_code not in RETRYABLE_STATUS_CODES:
                return response
            last_error = None
            last_status = response.status_code

        if attempt == attempts:
            if last_error is not None:
                raise last_error
            return response

        delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
        delay += random.uniform(0, 0.25)
        logger.warning(
            "provider.request.retry",
            extra={
                "event": "provider.request.retry",
                "provider": provider,
                "attempt": attempt,
                "status_code": last_status,
                "retryable": True,
            },
        )
        await asyncio.sleep(delay)

    # Unreachable, but keeps type checkers happy.
    raise RuntimeError("retry loop exhausted without a response")
