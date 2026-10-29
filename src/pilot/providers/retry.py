"""Retry transient failures without exposing provider bodies or credential-bearing URLs."""

import math
import random
import time
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from pilot.config import RetrySettings
from pilot.providers.base import ProviderError


def _retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        delay = float(value)
    except ValueError:
        try:
            delay = (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return None
    return max(0.0, delay) if math.isfinite(delay) else None


def post_json(
    client: httpx.Client,
    url: str,
    payload: dict[str, Any],
    retry: RetrySettings,
    *,
    headers: dict[str, str] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = random.random,
) -> dict[str, Any]:
    """Retry-After is a minimum: abort rather than retry early if it exceeds our bound."""
    reason = "request failed"
    for attempt in range(retry.max_attempts):
        retry_after = None
        try:
            response = client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException:
            reason = "request timed out"
        except httpx.RequestError:
            raise ProviderError("Provider connection failed; check host/network") from None
        else:
            status = response.status_code
            if 200 <= status < 300:
                try:
                    data = response.json()
                except ValueError:
                    raise ProviderError("Provider returned invalid JSON") from None
                if not isinstance(data, dict):
                    raise ProviderError("Provider returned a non-object JSON response")
                return data
            reason = f"HTTP {status}"
            if status != 429 and not 500 <= status < 600:
                raise ProviderError(f"Provider rejected request ({reason})")
            retry_after = _retry_after(response.headers.get("Retry-After"))
        if attempt + 1 == retry.max_attempts:
            break
        if retry_after is not None and retry_after > retry.max_delay:
            raise ProviderError("Provider Retry-After exceeds retry.max_delay; try again later")
        delay = min(retry.max_delay, retry.base_delay * 2**attempt * (1 + jitter()))
        sleep(max(delay, retry_after or 0))
    raise ProviderError(f"Provider failed after {retry.max_attempts} attempts ({reason})")
