"""Shared rate-limited HTTP client with disk caching, retry, and robots.txt respect."""
from __future__ import annotations
import hashlib
import json
import logging
import os
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).parent.parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)
CACHE_TTL = int(os.getenv("CACHE_TTL_HOURS", "6")) * 3600
REQUEST_DELAY = 1.0  # max 1 request per second per domain

_domain_last_request: dict[str, float] = {}
_robots_cache: dict[str, RobotFileParser] = {}


def _cache_path(url: str) -> Path:
    return CACHE_DIR / f"{hashlib.sha256(url.encode()).hexdigest()}.json"


def _is_cache_fresh(path: Path) -> bool:
    return path.exists() and (time.time() - path.stat().st_mtime) < CACHE_TTL


def _rate_limit(domain: str) -> None:
    elapsed = time.time() - _domain_last_request.get(domain, 0)
    if elapsed < REQUEST_DELAY:
        time.sleep(REQUEST_DELAY - elapsed)
    _domain_last_request[domain] = time.time()


def _get_robots(domain: str) -> RobotFileParser:
    if domain not in _robots_cache:
        rp = RobotFileParser()
        try:
            rp.set_url(f"https://{domain}/robots.txt")
            rp.read()
        except Exception:
            pass  # if robots.txt is unreachable, allow by default
        _robots_cache[domain] = rp
    return _robots_cache[domain]


def _can_fetch(url: str) -> bool:
    domain = urlparse(url).netloc
    return _get_robots(domain).can_fetch("*", url)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type(requests.RequestException),
    reraise=True,
)
def _do_request(url: str, headers: dict) -> requests.Response:
    return requests.get(url, headers=headers, timeout=20)


def fetch(url: str, force_refresh: bool = False) -> tuple[str, str]:
    """Fetch URL, returning (content, content_type). Caches to disk.

    Respects robots.txt, enforces 1 req/s per domain, retries with backoff.
    Returns ("", "text/plain") on any failure — never raises.
    """
    cache_path = _cache_path(url)
    if not force_refresh and _is_cache_fresh(cache_path):
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        return cached["content"], cached["content_type"]

    if not _can_fetch(url):
        logger.warning("robots.txt disallows %s", url)
        return "", "text/plain"

    _rate_limit(urlparse(url).netloc)
    try:
        resp = _do_request(url, headers={"User-Agent": "JobPipelineBot/1.0 (research)"})
        resp.raise_for_status()
        content = resp.text
        content_type = resp.headers.get("content-type", "text/html")
    except Exception as e:
        logger.error("Failed to fetch %s: %s", url, e)
        return "", "text/plain"

    cache_path.write_text(
        json.dumps({"content": content, "content_type": content_type}),
        encoding="utf-8",
    )
    return content, content_type
