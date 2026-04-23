"""Retry utilities for transient failures."""

from __future__ import annotations

import logging
import time
from typing import Callable, TypeVar, Any

logger = logging.getLogger(__name__)

T = TypeVar("T")


def retry_on_exception(
    *,
    exceptions: tuple[type[Exception], ...] = (Exception,),
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator to retry a function on specific exceptions.

    Args:
        exceptions: Tuple of exception types to retry on
        max_attempts: Maximum number of attempts (including first try)
        delay: Initial delay between retries in seconds
        backoff: Multiplier for delay after each retry

    Example:
        @retry_on_exception(exceptions=(httpx.HTTPError,), max_attempts=3)
        def fetch_data():
            ...
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        def wrapper(*args: Any, **kwargs: Any) -> T:
            current_delay = delay
            last_exception: Exception | None = None

            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt == max_attempts:
                        logger.warning(
                            f"{func.__name__} failed after {max_attempts} attempts: "
                            f"{type(e).__name__}: {e}"
                        )
                        raise

                    logger.info(
                        f"{func.__name__} failed (attempt {attempt}/{max_attempts}), "
                        f"retrying in {current_delay:.1f}s: {type(e).__name__}: {e}"
                    )
                    time.sleep(current_delay)
                    current_delay *= backoff

            # Should never reach here, but for type safety
            if last_exception:
                raise last_exception
            raise RuntimeError("Retry logic error")

        return wrapper
    return decorator
