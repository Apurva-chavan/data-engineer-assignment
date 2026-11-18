"""Lever JSON API scraper for data/ML engineering roles at US tech companies."""
from __future__ import annotations
import hashlib
import json
import logging
import re

from pipeline.http_client import fetch
from pipeline.scrapers.greenhouse import strip_html, _is_us, TARGET_TITLE_RE

logger = logging.getLogger(__name__)

# Lever board slugs for target companies
# Note: 'plaid' returns 404 (migrated ATS); replaced with 'neon' (Neon Serverless Postgres)
SITES = ["palantir", "neon"]
BASE_URL = "https://api.lever.co/v0/postings/{site}?mode=json"


def scrape() -> list[dict]:
    results = []
    for site in SITES:
        url = BASE_URL.format(site=site)
        content, ctype = fetch(url)
        if not content:
            logger.warning("Lever: empty response for %s", site)
            continue

        # Lever occasionally returns HTML error pages instead of JSON
        if "text/html" in ctype or content.strip().startswith("<"):
            logger.warning("Lever: got HTML for %s, skipping", site)
            continue

        try:
            jobs = json.loads(content)
        except Exception as e:
            logger.error("Lever: JSON parse error for %s: %s", site, e)
            continue

        if isinstance(jobs, dict):
            jobs = jobs.get("postings", [])
        if not isinstance(jobs, list):
            jobs = []

        logger.info("Lever %s: %d total jobs", site, len(jobs))
        for job in jobs:
            title = job.get("text", "")
            if not TARGET_TITLE_RE.search(title):
                continue

            categories = job.get("categories", {})
            location = categories.get("location", "") or job.get("workplaceType", "")
            if not _is_us(location):
                continue

            # Lever description is split across lists + descriptionPlain
            parts = [lst.get("content", "") for lst in job.get("lists", [])]
            parts.append(job.get("descriptionPlain", "") or strip_html(job.get("description", "")))
            raw_text = re.sub(r"\s+", " ", strip_html(" ".join(parts)))[:4000]

            external_id = job.get("id", "")
            posting_id = "lv_" + hashlib.sha256(f"{site}_{external_id}".encode()).hexdigest()[:16]

            results.append({
                "posting_id": posting_id,
                "source": "lever",
                "company": site,
                "title": title,
                "location_raw": location,
                "url": job.get("hostedUrl", ""),
                "raw_text": raw_text,
                "external_id": external_id,
                "updated_at": str(job.get("createdAt", "")),
            })

    logger.info("Lever: %d relevant postings", len(results))
    return results
