"""Deduplication: merge postings that refer to the same role."""
from __future__ import annotations
import re

from rapidfuzz import fuzz

FUZZY_THRESHOLD = 88  # token_sort_ratio


def _normalize(s: str) -> str:
    s = s.lower()
    s = re.sub(r"\b(sr|jr|senior|junior|staff|principal|lead|i{1,3}|iv|v)\b", "", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


_COMPANY_NOISE = re.compile(r"\b(inc|llc|ltd|corp|co|the|technologies|technology|labs|ai|data|systems|group)\b")

def _company_key(company: str) -> str:
    s = company.lower()
    s = _COMPANY_NOISE.sub("", s)
    return re.sub(r"[^a-z0-9]", "", s)


def find_duplicate(
    posting: dict,
    existing: list[dict],
) -> dict | None:
    """Return the existing record that matches posting, or None."""
    comp_key = _company_key(posting["company"])
    title_norm = _normalize(posting["title"])

    for ex in existing:
        if _company_key(ex["company"]) != comp_key:
            continue
        # Exact normalized match
        if _normalize(ex["title"]) == title_norm:
            return ex
        # Fuzzy match
        score = fuzz.token_sort_ratio(_normalize(ex["title"]), title_norm)
        if score >= FUZZY_THRESHOLD:
            return ex
    return None


def merge_into(canonical: dict, duplicate: dict) -> dict:
    """Merge duplicate's source_urls into canonical. Return updated canonical."""
    existing_urls = set(canonical.get("source_urls") or [])
    for url in duplicate.get("source_urls") or []:
        existing_urls.add(url)
    canonical["source_urls"] = list(existing_urls)
    return canonical
