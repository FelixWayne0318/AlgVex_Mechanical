"""
Shared HTTP retry decorator for REST API clients.

Provides a standard retry strategy with exponential backoff for all
data-fetching REST clients. Replaces inconsistent hand-written retry logic.

Reference: docs/upgrade_plan_v2/02_DATA_QUALITY.md §0

Usage:
    from utils.http_retry import api_retry

    class MyClient:
        @api_retry
        def fetch_data(self, ...):
            response = requests.get(url, timeout=self.timeout)
            response.raise_for_status()
            return response.json()

    # For urllib-based clients (e.g., binance_account.py):
    from utils.http_retry import urllib_api_retry

    class MyUrllibClient:
        @urllib_api_retry
        def fetch_data(self, ...):
            req = urllib.request.Request(url)
            return urllib.request.urlopen(req, timeout=10)
"""

import logging
import socket
import urllib.error

import requests
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception,
    retry_if_exception_type,
    before_sleep_log,
)

logger = logging.getLogger(__name__)

# Retry on transient network errors only (not 4xx client errors)
api_retry = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type((
        requests.exceptions.Timeout,
        requests.exceptions.ConnectionError,
    )),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)

# Retry for urllib-based clients (binance_account.py etc.)
# Catches URLError (connection refused, DNS failure) and socket.timeout.
# Does NOT catch HTTPError (which is a subclass of URLError but represents
# server responses like 4xx/5xx — those are handled by caller logic).
def _is_urllib_transient_error(exc: BaseException) -> bool:
    """Return True for transient network errors that should be retried."""
    # HTTPError is a server response (4xx/5xx) — do NOT retry
    if isinstance(exc, urllib.error.HTTPError):
        return False
    # URLError wraps socket errors (connection refused, DNS, timeout)
    if isinstance(exc, urllib.error.URLError):
        return True
    # Direct socket timeout
    if isinstance(exc, socket.timeout):
        return True
    # Generic connection errors
    if isinstance(exc, ConnectionError):
        return True
    return False


urllib_api_retry = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception(_is_urllib_transient_error),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)

DEFAULT_TIMEOUT = 10  # seconds
