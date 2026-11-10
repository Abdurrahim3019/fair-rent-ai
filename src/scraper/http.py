"""Polite HTTP layer: shared session, rate limit, retry with backoff, DB cache."""
import logging
import threading
import time
import urllib.robotparser
from urllib.parse import urlparse

import requests

from src import db
from src.config import MAX_RETRIES, REQUEST_DELAY_SEC, USER_AGENT

log = logging.getLogger(__name__)

_session: requests.Session | None = None
_lock = threading.Lock()
_last_request_at = 0.0
_robots: dict[str, urllib.robotparser.RobotFileParser] = {}


class FetchError(RuntimeError):
    pass


def get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "az"})
    return _session


def _wait_rate_limit() -> None:
    global _last_request_at
    with _lock:
        elapsed = time.monotonic() - _last_request_at
        if elapsed < REQUEST_DELAY_SEC:
            time.sleep(REQUEST_DELAY_SEC - elapsed)
        _last_request_at = time.monotonic()


def allowed_by_robots(url: str) -> bool:
    host = urlparse(url)
    base = f"{host.scheme}://{host.netloc}"
    rp = _robots.get(base)
    if rp is None:
        # Fetch with our own session (urllib's default UA gets 403 -> disallow-all).
        rp = urllib.robotparser.RobotFileParser(base + "/robots.txt")
        try:
            resp = get_session().get(base + "/robots.txt", timeout=10)
            rp.parse(resp.text.splitlines() if resp.ok else [])
        except Exception:  # unreachable robots.txt -> be permissive but log
            log.warning("robots.txt unreachable for %s", base)
            rp.parse([])
        _robots[base] = rp
    return rp.can_fetch(USER_AGENT, url)


def fetch_html(url: str, use_cache: bool = True, engine=None) -> str:
    """GET url with cache (24h), robots check, rate limit and exponential backoff."""
    if use_cache:
        cached = db.get_cached_page(url, engine=engine)
        if cached is not None:
            return cached
    if not allowed_by_robots(url):
        raise FetchError(f"robots.txt disallows {url}")

    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        _wait_rate_limit()
        try:
            resp = get_session().get(url, timeout=20)
            if resp.status_code == 404:
                raise FetchError(f"Not found: {url}")
            if resp.status_code >= 500 or resp.status_code == 429:
                raise requests.HTTPError(f"HTTP {resp.status_code}")
            resp.raise_for_status()
            html = resp.text
            db.put_cached_page(url, html, engine=engine)
            return html
        except FetchError:
            raise
        except Exception as exc:  # network / 5xx / 429
            last_exc = exc
            if attempt < MAX_RETRIES:
                backoff = 2 ** attempt
                log.warning("Fetch failed (%s), retry in %ss: %s", exc, backoff, url)
                time.sleep(backoff)
    raise FetchError(f"Failed after {MAX_RETRIES} retries: {url} ({last_exc})")
