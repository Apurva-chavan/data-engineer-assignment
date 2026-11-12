"""Greenhouse JSON API scraper."""
from __future__ import annotations
import hashlib
import logging
import re
from html.parser import HTMLParser

from pipeline.http_client import fetch

logger = logging.getLogger(__name__)

TOKENS = ["stripe", "airbnb", "databricks", "figma"]
BASE_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"

TARGET_TITLE_RE = re.compile(
    r"\b(data engineer|ml engineer|machine learning engineer|analytics engineer|platform engineer|data platform|mlops|data scientist|data infrastructure|ml platform|ml infra|data infra|applied scientist|applied ml|llm engineer|ai engineer|data reliability|analytics platform)\b",
    re.IGNORECASE,
)


class _HTMLStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data):
        self._parts.append(data)

    def get_text(self) -> str:
        return " ".join(self._parts)


def strip_html(html: str) -> str:
    s = _HTMLStripper()
    s.feed(html)
    return re.sub(r"\s+", " ", s.get_text()).strip()


def _is_us(location: str) -> bool:
    loc = location.lower()
    # Explicit non-US exclusion
    non_us = ["london", "uk", "berlin", "germany", "paris", "france", "toronto",
              "canada", "amsterdam", "netherlands", "singapore", "india", "bangalore",
              "barcelona", "spain", "dublin", "ireland", "sydney", "australia",
              "cologne", "munich", "zurich", "switzerland", "poland", "warsaw"]
    if any(k in loc for k in non_us):
        return False
    return any(k in loc for k in ["united states", " us ", "usa", "u.s.", "remote",
                                   "new york", "san francisco", "seattle", "austin",
                                   "chicago", "boston", "denver", "atlanta", "nyc",
                                   "sf", "bay area", "los angeles", "la", "washington dc",
                                   "philadelphia", "miami", "portland", "dallas",
                                   "new york city", "mountain view", "palo alto",
                                   "menlo park", "sunnyvale", "san jose", "bellevue"])


def scrape() -> list[dict]:
    results = []
    for token in TOKENS:
        url = BASE_URL.format(token=token)
        content, ctype = fetch(url)
        if not content:
            logger.warning("Greenhouse: empty response for token %s", token)
            continue
        try:
            import json
            data = json.loads(content)
        except Exception as e:
            logger.error("Greenhouse: JSON parse error for %s: %s", token, e)
            continue

        jobs = data.get("jobs", [])
        logger.info("Greenhouse %s: %d total jobs", token, len(jobs))
        for job in jobs:
            title = job.get("title", "")
            if not TARGET_TITLE_RE.search(title):
                continue
            location = job.get("location", {}).get("name", "")
            if not _is_us(location):
                continue

            description_html = ""
            if isinstance(job.get("content"), str):
                description_html = job["content"]

            raw_text = strip_html(description_html)[:4000]
            external_id = str(job.get("id", ""))
            posting_id = "gh_" + hashlib.sha256(f"{token}_{external_id}".encode()).hexdigest()[:16]

            results.append({
                "posting_id": posting_id,
                "source": "greenhouse",
                "company": token,
                "title": title,
                "location_raw": location,
                "url": job.get("absolute_url", ""),
                "raw_text": raw_text,
                "external_id": external_id,
                "updated_at": job.get("updated_at", ""),
            })

    logger.info("Greenhouse: %d relevant postings", len(results))
    return results
