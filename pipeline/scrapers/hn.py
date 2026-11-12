"""Hacker News 'Who is Hiring?' HTML scraper — no Firebase API."""
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
    r"\b(data engineer|ml engineer|machine learning engineer|analytics engineer|platform engineer|data platform|mlops|data scientist|data infrastructure|ml platform|ml infra|data infra|applied scientist|applied ml|llm engineer|ai engineer|data reliability|analytics platform)\b",
    re.IGNORECASE,
)
US_RE = re.compile(
    r"\b(remote|united states|usa?|u\.s\.|new york|san francisco|seattle|austin|chicago|boston|denver|atlanta|los angeles|nyc|sf|bay area|washington dc|philadelphia|miami|portland|dallas|mountain view|palo alto|menlo park|sunnyvale|san jose|bellevue)\b",
    re.IGNORECASE,
)
NON_US_RE = re.compile(
    r"\b(london|uk|berlin|germany|paris|france|toronto|canada|amsterdam|netherlands|singapore|india|bangalore|barcelona|spain|dublin|ireland|sydney|australia|cologne|munich|zurich|switzerland|poland|warsaw)\b",
    re.IGNORECASE,
)


def _find_current_thread(html: str) -> str | None:
    """Return item ID of the most recent 'Ask HN: Who is hiring?' thread."""
    soup = BeautifulSoup(html, "lxml")
    for a in soup.select("a.storylink, td.title a"):
        text = a.get_text()
        if "who is hiring" in text.lower():
            href = a.get("href", "")
            m = re.search(r"id=(\d+)", href)
            if m:
                return m.group(1)
    # fallback: any link with item?id=
    for a in soup.find_all("a", href=re.compile(r"item\?id=\d+")):
        text = a.get_text()
        if "who is hiring" in text.lower():
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
            # Only top-level comments (indent == 0)
            indent_tag = row.select_one("td.ind img")
            if indent_tag and int(indent_tag.get("width", 0)) > 0:
                continue

            comment_id = row.get("id", "")
            div = row.select_one("div.commtext")
            if not div:
                continue
            text = div.get_text(separator="\n").strip()
            comments.append({"id": comment_id, "text": text})

        # Pagination: find "More" link
        more = soup.find("a", string=re.compile(r"More$"))
        if more and more.get("href"):
            url = urljoin("https://news.ycombinator.com/", more["href"])
        else:
            url = None

    return comments


def _split_roles(comment_text: str) -> list[str]:
    """Split a multi-role comment into individual role chunks."""
    # Split on common separators: blank line + title-like line, or "Role:" headers
    chunks = re.split(r"\n{2,}(?=[A-Z])", comment_text)
    return [c.strip() for c in chunks if c.strip()]


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
    logger.info("HN: %d top-level comments", len(comments))

    results = []
    for comment in comments:
        text = comment["text"]
        if not TARGET_RE.search(text):
            continue
        if NON_US_RE.search(text) and not US_RE.search(text):
            continue
        if not US_RE.search(text):
            continue

        chunks = _split_roles(text)
        for chunk in chunks:
            if not TARGET_RE.search(chunk):
                continue
            if NON_US_RE.search(chunk) and not US_RE.search(chunk):
                continue
            if not US_RE.search(chunk):
                continue

            chunk_id = hashlib.sha256(chunk[:500].encode()).hexdigest()[:16]
            posting_id = f"hn_{chunk_id}"

            # Best-effort title extraction: first line
            first_line = chunk.split("\n")[0].strip()[:200]

            results.append({
                "posting_id": posting_id,
                "source": "hn",
                "company": "",  # extracted by LLM
                "title": first_line,
                "location_raw": "",
                "url": f"{HN_ITEM_BASE}{comment['id']}",
                "raw_text": chunk[:4000],
                "external_id": chunk_id,
                "updated_at": "",
            })

    logger.info("HN: %d relevant role chunks", len(results))
    return results
