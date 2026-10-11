import time
import random
import logging
import functools
import asyncio
from typing import Callable, Any, Tuple, Type, Optional

logger = logging.getLogger("cloudlens.retry")


def retry_with_backoff(
    max_retries: int = 3,
    base_delay: float = 0.2,
    max_delay: float = 2.0,
    jitter: bool = True,
    retryable_exceptions: Tuple[Type[Exception], ...] = (Exception,),
    fallback_value: Any = None,
    raise_on_exhaustion: bool = False
):
    """
    Decorator for retrying functions with exponential backoff and randomized jitter.
    Supports both synchronous and asynchronous callables.
    """
    def decorator(func: Callable):
        if asyncio.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                retries = 0
                while True:
                    try:
                        return await func(*args, **kwargs)
                    except retryable_exceptions as e:
                        retries += 1
                        if retries > max_retries:
                            logger.warning(
                                f"Async call {func.__name__} failed after {max_retries} retries: {e}"
                            )
                            if raise_on_exhaustion:
                                raise
                            return fallback_value

                        delay = min(max_delay, base_delay * (2 ** (retries - 1)))
                        if jitter:
                            delay += random.uniform(0, base_delay)
                        logger.info(
                            f"Retrying async {func.__name__} in {delay:.3f}s (attempt {retries}/{max_retries}) after: {e}"
                        )
                        await asyncio.sleep(delay)
            return async_wrapper
        else:
            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                retries = 0
                while True:
                    try:
                        return func(*args, **kwargs)
                    except retryable_exceptions as e:
                        retries += 1
                        if retries > max_retries:
                            logger.warning(
                                f"Sync call {func.__name__} failed after {max_retries} retries: {e}"
                            )
                            if raise_on_exhaustion:
                                raise
                            return fallback_value

                        delay = min(max_delay, base_delay * (2 ** (retries - 1)))
                        if jitter:
                            delay += random.uniform(0, base_delay)
                        logger.info(
                            f"Retrying sync {func.__name__} in {delay:.3f}s (attempt {retries}/{max_retries}) after: {e}"
                        )
                        time.sleep(delay)
            return sync_wrapper
    return decorator


def execute_with_timeout_and_fallback(
    func: Callable,
    fallback: Any = None,
    error_msg: Optional[str] = None,
    *args,
    **kwargs
) -> Any:
    """Safely executes a function; returns fallback on failure without crashing."""
    try:
        return func(*args, **kwargs)
    except Exception as e:
        msg = error_msg or f"Error in {getattr(func, '__name__', 'callable')}: {e}"
        logger.error(msg)
        return fallback
