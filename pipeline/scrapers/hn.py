"""Hacker News 'Who is Hiring?' HTML scraper."""
from __future__ import annotations
import hashlib
import logging
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from pipeline.http_client import fetch

logger = logging.getLogger(__name__)

HN_SUBMITTED = "https://news.ycombinator.com/submitted?id=whoishiring"
HN_ITEM_BASE = "https://news.ycombinator.com/item?id="

TARGET_RE = re.compile(
    r"\b(data engineer|ml engineer|machine learning engineer|analytics engineer|"
    r"platform engineer|data platform|mlops|data scientist|data infrastructure|"
    r"ml platform|ml infra|data infra|applied scientist|applied ml|llm engineer|"
    r"ai engineer|data reliability|analytics platform)\b",
    re.IGNORECASE,
)
US_RE = re.compile(
    r"\b(remote|united states|usa?|u\.s\.|new york|san francisco|seattle|austin|"
    r"chicago|boston|denver|atlanta|los angeles|nyc|sf|bay area|washington dc|"
    r"philadelphia|miami|portland|dallas|mountain view|palo alto|menlo park|"
    r"sunnyvale|san jose|bellevue)\b",
    re.IGNORECASE,
)
NON_US_RE = re.compile(
    r"\b(london| uk|berlin|germany|paris|france|toronto|canada|amsterdam|"
    r"netherlands|singapore|india|bangalore|barcelona|spain|dublin|ireland|"
    r"sydney|australia|cologne|munich|zurich|switzerland|poland|warsaw)\b",
    re.IGNORECASE,
)


def _find_current_thread(html: str) -> str | None:
    """Return item ID of the most recent 'Ask HN: Who is hiring?' thread."""
    soup = BeautifulSoup(html, "lxml")
    # Try all anchor tags — HN has changed its CSS classes over time
    for a in soup.find_all("a", href=re.compile(r"item\?id=\d+")):
        if "who is hiring" in a.get_text().lower():
            m = re.search(r"id=(\d+)", a["href"])
            if m:
                return m.group(1)
    return None


def _parse_comments(item_id: str) -> list[dict]:
    """Scrape all top-level comments from an HN thread, following pagination."""
    comments = []
    url = f"{HN_ITEM_BASE}{item_id}"

    while url:
        content, _ = fetch(url)
        if not content:
            break
        soup = BeautifulSoup(content, "lxml")

        for row in soup.select("tr.athing.comtr"):
            # Only top-level comments (indent width == 0)
            indent_tag = row.select_one("td.ind img")
            if indent_tag and int(indent_tag.get("width", 0)) > 0:
                continue
            comment_id = row.get("id", "")
            div = row.select_one("div.commtext")
            if not div:
                continue
            text = div.get_text(separator="\n").strip()
            comments.append({"id": comment_id, "text": text})

        # Follow pagination
        more = soup.find("a", string=re.compile(r"More$"))
        url = urljoin("https://news.ycombinator.com/", more["href"]) if more and more.get("href") else None

    return comments


def _is_us_relevant(text: str) -> bool:
    """True if text mentions US/remote and doesn't exclusively mention non-US."""
    has_us = bool(US_RE.search(text))
    has_non_us = bool(NON_US_RE.search(text))
    return has_us and not (has_non_us and not has_us)


def scrape() -> list[dict]:
    content, _ = fetch(HN_SUBMITTED)
    if not content:
        logger.error("HN: could not fetch submitted page")
        return []

    thread_id = _find_current_thread(content)
    if not thread_id:
        logger.error("HN: could not find current hiring thread")
        return []

    logger.info("HN: found thread id=%s", thread_id)
    comments = _parse_comments(thread_id)
    logger.info("HN: %d top-level comments scraped", len(comments))

    results = []
    for comment in comments:
        text = comment["text"]
        if not TARGET_RE.search(text):
            continue
        if not US_RE.search(text):
            continue
        if NON_US_RE.search(text) and not US_RE.search(text):
            continue

        # Split multi-role comments into individual role chunks
        chunks = re.split(r"\n{2,}(?=[A-Z])", text)
        for chunk in [c.strip() for c in chunks if c.strip()]:
            if not TARGET_RE.search(chunk):
                continue
            if not US_RE.search(chunk) and not US_RE.search(text):
                continue
            if NON_US_RE.search(chunk) and not US_RE.search(chunk):
                continue

            chunk_id = hashlib.sha256(chunk[:500].encode()).hexdigest()[:16]
            first_line = chunk.split("\n")[0].strip()[:200]

            results.append({
                "posting_id": f"hn_{chunk_id}",
                "source": "hn",
                "company": "",  # LLM extracts company from text
                "title": first_line,
                "location_raw": "",
                "url": f"{HN_ITEM_BASE}{comment['id']}",
                "raw_text": chunk[:4000],
                "external_id": chunk_id,
                "updated_at": "",
            })

    logger.info("HN: %d relevant role chunks", len(results))
    return results
