"""Rate-limited HTTP client with retry, shared by the collectors.

Errors are re-raised with secrets masked, because request URLs carry the DART key.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

from src.common.config import mask_secret

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _error_detail(resp: Any) -> str:
    """Short, masked excerpt of an error body (e.g. Naver's errorCode/errorMessage) for diagnosis."""
    try:
        text = resp.content[:300].decode("utf-8", "replace")
    except Exception:
        return ""
    return mask_secret(" ".join(text.split()))


class CollectorError(RuntimeError):
    """Request failed permanently. The message never contains secret values."""


class RateLimitedClient:
    def __init__(
        self,
        delay_sec: float,
        max_retries: int = 3,
        timeout: float = 30.0,
        session: requests.Session | Any | None = None,
        sleep=time.sleep,
    ) -> None:
        self.delay_sec = delay_sec
        self.max_retries = max_retries
        self.timeout = timeout
        self.session = session or requests.Session()
        self._sleep = sleep
        self._last_request = 0.0

    def _wait_turn(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.delay_sec:
            self._sleep(self.delay_sec - elapsed)
        self._last_request = time.monotonic()

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        last_error = ""
        for attempt in range(1, self.max_retries + 1):
            self._wait_turn()
            try:
                resp = self.session.get(url, timeout=self.timeout, **kwargs)
            except requests.RequestException as e:
                last_error = mask_secret(f"{type(e).__name__}: {e}")
            else:
                if resp.status_code not in RETRYABLE_STATUS:
                    if resp.status_code >= 400:
                        raise CollectorError(
                            f"HTTP {resp.status_code} for {mask_secret(url)}: {_error_detail(resp)}"
                        )
                    return resp
                last_error = f"HTTP {resp.status_code}"
            if attempt < self.max_retries:
                backoff = 2 ** attempt
                logger.warning("request failed (%s), retry %d/%d in %ds",
                               last_error, attempt, self.max_retries, backoff)
                self._sleep(backoff)
        raise CollectorError(f"gave up after {self.max_retries} attempts: {last_error}")
