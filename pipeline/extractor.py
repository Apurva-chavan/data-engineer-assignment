"""LLM extraction using OpenAI structured outputs + Pydantic validation."""
from __future__ import annotations
import json
import logging
import os
import re
from datetime import datetime

from openai import OpenAI
from pydantic import ValidationError

from pipeline.models import RawExtraction, JobPosting

logger = logging.getLogger(__name__)

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=os.environ["OPENAI_API_KEY"],
            base_url=BASE_URL,
        )
    return _client


MODEL = os.getenv("OPENAI_MODEL", "llama-3.3-70b-versatile")
BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.groq.com/openai/v1")

SYSTEM_PROMPT = """You are a structured data extractor for job postings.
Extract ONLY information explicitly stated in the text. Use null for anything not stated — never guess.
Return valid JSON matching this schema:
{
  "seniority": "junior|mid|senior|staff_plus|null",
  "city": "string|null",
  "country": "string|null",
  "remote_policy": "onsite|hybrid|remote|null",
  "salary_min": "number|null",
  "salary_max": "number|null",
  "currency": "3-letter ISO code|null",
  "employment_type": "full_time|contract|intern|null",
  "skills": ["list of tech skills"],
  "visa_sponsorship": "true|false|null",
  "posted_date": "YYYY-MM-DD|null",
  "evidence": {
    "field_name": "exact quote from text that supports this field"
  }
}
Salary must be annualized numbers only (no strings). Skills are tech tools/languages only."""


def _truncate(text: str, max_tokens: int = 3000) -> str:
    # Rough token estimate: 4 chars per token
    return text[: max_tokens * 4]


def extract(raw: dict) -> RawExtraction:
    """Call LLM to extract structured fields from raw_text. Returns RawExtraction."""
    text = _truncate(raw.get("raw_text", ""))
    if not text.strip():
        return RawExtraction()

    # For Greenhouse/Lever, prepend structured context to help LLM
    context = f"Title: {raw.get('title', '')}\nLocation: {raw.get('location_raw', '')}\n\n{text}"

    try:
        resp = _get_client().chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": context},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
        raw_json = resp.choices[0].message.content
        data = json.loads(raw_json)
        return RawExtraction(**data)
    except ValidationError as e:
        logger.warning("Validation error for %s: %s", raw.get("posting_id"), e)
        return RawExtraction()
    except Exception as e:
        logger.error("LLM extraction failed for %s: %s", raw.get("posting_id"), e)
        return RawExtraction()


def _normalize_company(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())


def _infer_location(location_raw: str, extraction: RawExtraction) -> tuple[str | None, str | None, str | None]:
    city = extraction.city
    country = extraction.country or ("US" if location_raw else None)
    remote_policy = extraction.remote_policy
    loc = location_raw.lower()
    if not remote_policy:
        if "remote" in loc:
            remote_policy = "remote"
        elif "hybrid" in loc:
            remote_policy = "hybrid"
    if not country and any(k in loc for k in ["us", "usa", "united states", "remote"]):
        country = "US"
    return city, country, remote_policy


def build_posting(raw: dict, extraction: RawExtraction, now: datetime) -> JobPosting:
    city, country, remote_policy = _infer_location(raw.get("location_raw", ""), extraction)

    posted_date = None
    if extraction.posted_date:
        try:
            from datetime import date
            posted_date = date.fromisoformat(extraction.posted_date)
        except ValueError:
            pass

    company = raw.get("company") or extraction.city or ""
    # For HN, company may be in evidence
    if not company and "company" in extraction.evidence:
        company = extraction.evidence["company"]

    return JobPosting(
        posting_id=raw["posting_id"],
        company=_normalize_company(company),
        title=raw.get("title", ""),
        seniority=extraction.seniority,
        city=city or extraction.city,
        country=country,
        remote_policy=remote_policy,
        salary_min=extraction.salary_min,
        salary_max=extraction.salary_max,
        currency=extraction.currency,
        employment_type=extraction.employment_type,
        skills=extraction.skills,
        visa_sponsorship=extraction.visa_sponsorship,
        posted_date=posted_date,
        status="open",
        source_urls=[raw.get("url", "")] if raw.get("url") else [],
        evidence=extraction.evidence,
        first_seen=now,
        last_seen=now,
    )
