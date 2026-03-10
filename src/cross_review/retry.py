"""Retry logic with exponential backoff (design doc §17)."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Awaitable, Callable, TypeVar

from pydantic import ValidationError

T = TypeVar("T")

logger = logging.getLogger(__name__)

RETRYABLE_EXCEPTIONS: tuple[type[BaseException], ...] = (
    TimeoutError,
    json.JSONDecodeError,
    ConnectionError,
    ValidationError,
)


async def with_retry(
    fn: Callable[..., Awaitable[T]],
    *,
    max_attempts: int = 2,
    base_delay: float = 1.0,
    retryable: tuple[type[BaseException], ...] = RETRYABLE_EXCEPTIONS,
) -> T:
    """Call *fn* up to *max_attempts* times, sleeping with exponential backoff
    between retries when a retryable exception is raised.

    Returns the result of *fn* on success.
    Raises the last exception after all attempts are exhausted.
    """
    last_exc: BaseException | None = None

    for attempt in range(max_attempts):
        try:
            return await fn()
        except retryable as exc:
            last_exc = exc
            if attempt < max_attempts - 1:
                delay = base_delay * (2**attempt)
                logger.warning(
                    "Attempt %d/%d failed (%s: %s), retrying in %.1fs …",
                    attempt + 1,
                    max_attempts,
                    type(exc).__name__,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)
            else:
                logger.error(
                    "All %d attempts exhausted. Last error: %s: %s",
                    max_attempts,
                    type(exc).__name__,
                    exc,
                )

    # This line is reached only when every attempt raised a retryable exception.
    raise last_exc  # type: ignore[misc]
