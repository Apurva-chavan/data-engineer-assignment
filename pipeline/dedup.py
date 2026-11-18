"""Deduplication: merge postings that refer to the same role across sources."""
from __future__ import annotations
import re

from rapidfuzz import fuzz

FUZZY_THRESHOLD = 88  # token_sort_ratio minimum to consider a title match


_SENIORITY_WORDS = re.compile(
    r"\b(sr|jr|senior|junior|staff|principal|lead|i{1,3}|iv|v)\b"
)
_COMPANY_NOISE = re.compile(
    r"\b(inc|llc|ltd|corp|co|the|technologies|technology|labs|ai|data|systems|group)\b"
)


def _normalize(s: str) -> str:
    """Lowercase, strip seniority/level words and punctuation for title comparison."""
    s = s.lower()
    s = _SENIORITY_WORDS.sub("", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _company_key(company: str) -> str:
    """Normalize company name to a bare alphanumeric key."""
    s = company.lower()
    s = _COMPANY_NOISE.sub("", s)
    return re.sub(r"[^a-z0-9]", "", s)


def find_duplicate(posting: dict, existing: list[dict]) -> dict | None:
    """Return the existing record that is the same role as posting, or None.

    Matching strategy (within same company):
    1. Exact normalized title match.
    2. Fuzzy token_sort_ratio >= FUZZY_THRESHOLD (handles 'Sr. Data Engineer'
       vs 'Senior Data Engineer, Platform').
    """
    comp_key = _company_key(posting.get("company", ""))
    title_norm = _normalize(posting.get("title", ""))

    for ex in existing:
        if _company_key(ex.get("company", "")) != comp_key:
            continue
        ex_title_norm = _normalize(ex.get("title", ""))
        if ex_title_norm == title_norm:
            return ex
        if fuzz.token_sort_ratio(ex_title_norm, title_norm) >= FUZZY_THRESHOLD:
            return ex
    return None


def merge_into(canonical: dict, duplicate: dict) -> dict:
    """Merge duplicate's source_urls into canonical. Returns updated canonical."""
    urls = set(canonical.get("source_urls") or [])
    urls.update(duplicate.get("source_urls") or [])
    canonical["source_urls"] = list(urls)
    return canonical
