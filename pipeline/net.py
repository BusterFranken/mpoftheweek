"""Polite HTTP helpers: UA, timeouts, retries with backoff, throttling, raw cache.

Every response body is cached verbatim under ``pipeline/data/raw/`` so that
re-runs are debuggable and offline-friendly. Set ``NO_CACHE = True`` (the
``--no-cache`` flag of ``pipeline.run``) to force re-fetching.
"""
from __future__ import annotations

import json
import logging
import random
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

import requests

from . import config

log = logging.getLogger(__name__)

# Toggled by pipeline.run --no-cache.
NO_CACHE = False

_session = requests.Session()
_session.headers["User-Agent"] = config.USER_AGENT

_last_request_at: dict[str, float] = {}


class FetchError(RuntimeError):
    """A request failed permanently (all retries exhausted)."""


class TransientApiError(RuntimeError):
    """A retryable failure (5xx/429, or the EP API's 200-with-error-body)."""


def _throttle(host: str) -> None:
    min_interval = (
        config.API_MIN_INTERVAL if host == urlsplit(config.EP_API_BASE).netloc else config.WWW_MIN_INTERVAL
    )
    last = _last_request_at.get(host)
    if last is not None:
        wait = min_interval - (time.monotonic() - last)
        if wait > 0:
            time.sleep(wait)
    _last_request_at[host] = time.monotonic()


def _cache_fresh(path: Path, ttl: timedelta | None) -> bool:
    if NO_CACHE or not path.is_file() or path.stat().st_size == 0:
        return False
    if ttl is None:
        return True
    age = datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)
    return age <= ttl


def _api_error_message(body: str) -> str | None:
    """The EP Open Data API signals transient overload as HTTP 200 + error JSON.

    A real payload always has a top-level "data" key; the error body does not.
    """
    head = body.lstrip()
    if not head.startswith("{"):
        return None
    try:
        doc = json.loads(body)
    except ValueError:
        return None
    if isinstance(doc, dict) and "error" in doc and "data" not in doc:
        return str(doc["error"])[:200]
    return None


def get(
    url: str,
    *,
    params: dict | None = None,
    cache_path: Path | None = None,
    ttl: timedelta | None = None,
    validate: Callable[[str], bool] | None = None,
) -> str:
    """GET ``url`` and return the response body as text.

    The raw body is cached at ``cache_path`` (atomic write). A cached file
    younger than ``ttl`` (any age when ttl is None) short-circuits the request.
    ``validate`` may reject a body (return falsy) to trigger a retry.
    """
    if cache_path is not None and _cache_fresh(cache_path, ttl):
        return cache_path.read_text(encoding="utf-8")

    host = urlsplit(url).netloc
    last_err: Exception | None = None
    for attempt in range(config.RETRIES):
        if attempt:
            delay = config.BACKOFF_BASE * (2 ** (attempt - 1)) + random.uniform(0, 2)
            log.info("retry %d in %.1fs (%s): %s", attempt, delay, last_err, url)
            time.sleep(delay)
        _throttle(host)
        try:
            resp = _session.get(url, params=params, timeout=config.TIMEOUT)
            if resp.status_code in (429, 500, 502, 503, 504):
                raise TransientApiError(f"HTTP {resp.status_code}")
            resp.raise_for_status()
            if not resp.encoding:
                resp.encoding = "utf-8"
            body = resp.text
            api_err = _api_error_message(body)
            if api_err:
                raise TransientApiError(f"API error body: {api_err}")
            if validate is not None and not validate(body):
                raise TransientApiError("response failed validation")
            if cache_path is not None:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                tmp = cache_path.with_suffix(cache_path.suffix + ".tmp")
                tmp.write_text(body, encoding="utf-8")
                tmp.replace(cache_path)
            return body
        except (requests.RequestException, TransientApiError) as exc:
            last_err = exc
    raise FetchError(f"giving up on {url} after {config.RETRIES} attempts: {last_err}")


def get_api_json(
    path: str,
    *,
    params: dict | None = None,
    cache_path: Path | None = None,
    ttl: timedelta | None = None,
) -> dict:
    """GET an EP Open Data API path and parse the JSON-LD envelope."""
    query = {"format": config.EP_API_FORMAT}
    if params:
        query.update(params)
    body = get(
        f"{config.EP_API_BASE}{path}", params=query, cache_path=cache_path, ttl=ttl
    )
    return json.loads(body)
