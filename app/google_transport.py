from __future__ import annotations

import http.client
import logging
import socket
import ssl
import time
from collections.abc import Callable
from typing import TypeVar


logger = logging.getLogger(__name__)

T = TypeVar("T")

RETRY_ATTEMPTS = 3
RETRY_INITIAL_DELAY_SECONDS = 0.4
RETRY_BACKOFF = 2.0


def is_retryable_google_transport_error(exc: BaseException) -> bool:
    """Return True for short-lived network failures that are safe to retry."""
    retryable_types: tuple[type[BaseException], ...] = (
        ssl.SSLError,
        socket.timeout,
        TimeoutError,
        ConnectionError,
        http.client.HTTPException,
    )
    optional_retryable: list[type[BaseException]] = []
    try:
        import requests

        optional_retryable.extend(
            [
                requests.exceptions.SSLError,
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
            ]
        )
    except Exception:
        pass
    try:
        import urllib3

        optional_retryable.extend(
            [
                urllib3.exceptions.SSLError,
                urllib3.exceptions.ProtocolError,
                urllib3.exceptions.ReadTimeoutError,
                urllib3.exceptions.ConnectTimeoutError,
                urllib3.exceptions.MaxRetryError,
            ]
        )
    except Exception:
        pass

    current: BaseException | None = exc
    while current is not None:
        if isinstance(current, retryable_types) or any(isinstance(current, item) for item in optional_retryable):
            return True
        text = str(current).lower()
        if any(
            marker in text
            for marker in [
                "record layer failure",
                "eof occurred in violation of protocol",
                "connection reset",
                "connection aborted",
                "remote end closed connection",
                "temporarily unavailable",
                "timed out",
            ]
        ):
            return True
        current = current.__cause__ or current.__context__
    return False


def with_google_transport_retry(stage: str, operation: Callable[[], T], *, attempts: int = RETRY_ATTEMPTS) -> T:
    """Run a Google network operation with bounded retries for transient transport failures."""
    last_error: BaseException | None = None
    delay = RETRY_INITIAL_DELAY_SECONDS
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except Exception as exc:
            if not is_retryable_google_transport_error(exc) or attempt >= attempts:
                raise
            last_error = exc
            logger.warning(
                "Google transport retry stage=%s attempt=%s/%s error_type=%s",
                stage,
                attempt,
                attempts,
                exc.__class__.__name__,
            )
            time.sleep(delay)
            delay *= RETRY_BACKOFF
    if last_error is not None:
        raise last_error
    raise RuntimeError(f"Google transport operation did not run: {stage}")


def close_google_service(service: object) -> None:
    """Close a googleapiclient Resource transport when the installed version supports it."""
    close = getattr(service, "close", None)
    if not callable(close):
        return
    try:
        close()
    except Exception:
        logger.debug("Google service close failed", exc_info=True)
