"""LLM extraction using OpenAI structured outputs + Pydantic validation."""
from __future__ import annotations
import json
import logging
import os
import re
from datetime import datetime, date
from pathlib import Path

from openai import OpenAI
from pydantic import ValidationError

from pipeline.models import RawExtraction, JobPosting

logger = logging.getLogger(__name__)

MODEL = os.getenv("OPENAI_MODEL", "llama-3.3-70b-versatile")
BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.groq.com/openai/v1")
# Cost per 1M tokens in USD — set to 0 for free-tier providers like Groq
COST_PER_1M_INPUT = float(os.getenv("COST_PER_1M_INPUT", "0.0"))
COST_PER_1M_OUTPUT = float(os.getenv("COST_PER_1M_OUTPUT", "0.0"))

_COST_FILE = Path(__file__).parent.parent / "data" / "llm_cost.json"

_client: OpenAI | None = None
_usage: dict = {"input_tokens": 0, "output_tokens": 0, "calls": 0}


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], base_url=BASE_URL)
    return _client


def _save_cost() -> None:
    _COST_FILE.parent.mkdir(exist_ok=True)
    _COST_FILE.write_text(json.dumps(_usage), encoding="utf-8")


def get_cost_stats() -> dict:
    """Return cumulative token usage and estimated USD cost.
    Reads from disk so it works even when called from a separate process (evaluate)."""
    usage = _usage.copy()
    if _COST_FILE.exists():
        try:
            saved = json.loads(_COST_FILE.read_text(encoding="utf-8"))
            # Use whichever has more calls (disk = persisted from run_pipeline)
            if saved.get("calls", 0) >= usage["calls"]:
                usage = saved
        except Exception:
            pass
    cost = (
        usage["input_tokens"] / 1_000_000 * COST_PER_1M_INPUT
        + usage["output_tokens"] / 1_000_000 * COST_PER_1M_OUTPUT
    )
    return {
        "calls": usage["calls"],
        "input_tokens": usage["input_tokens"],
        "output_tokens": usage["output_tokens"],
        "total_cost_usd": round(cost, 6),
        "cost_per_posting_usd": round(cost / usage["calls"], 6) if usage["calls"] else 0.0,
        "note": "Groq free tier used; equivalent OpenAI gpt-4o-mini cost ~$0.002/posting",
    }


SYSTEM_PROMPT = """You are a structured data extractor for job postings.
Extract ONLY information explicitly stated in the text. Use null for anything not stated — never guess.
Return valid JSON matching this schema:
{
  "company": "string|null",
  "seniority": "junior|mid|senior|staff_plus|null",
  "city": "string|null",
  "country": "string|null",
  "remote_policy": "onsite|hybrid|remote|null",
  "salary_min": number|null,
  "salary_max": number|null,
  "currency": "3-letter ISO code|null",
  "employment_type": "full_time|contract|intern|null",
  "skills": ["list of tech skills"],
  "visa_sponsorship": true|false|null,
  "posted_date": "YYYY-MM-DD|null",
  "evidence": {
    "field_name": "exact quote from text that supports this field"
  }
}
Rules:
- salary_min and salary_max must be annualized numbers only (no strings). Convert hourly/monthly to annual.
- skills: tech tools, languages, frameworks only (e.g. Python, Spark, dbt, Kubernetes).
- visa_sponsorship: true ONLY if posting explicitly says it sponsors visas/work authorization.
  false ONLY if it explicitly says "no visa sponsorship", "must be authorized to work", "no sponsorship", or similar.
  null if not mentioned.
- seniority: junior=intern/entry/associate, mid=mid-level/II/no seniority qualifier, senior=senior/III, staff_plus=staff/principal/lead/director/VP/fellow.
- company: extract the hiring company name from the text. For HN posts it appears at the start of the comment.
- evidence: for each non-null field, include the exact quote from the text that justifies it."""


def _truncate(text: str, max_chars: int = 12000) -> str:
    return text[:max_chars]


def extract(raw: dict) -> RawExtraction:
    """Call LLM to extract structured fields from raw_text. Returns RawExtraction."""
    text = _truncate(raw.get("raw_text", ""))
    if not text.strip():
        return RawExtraction()

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
        if resp.usage:
            _usage["input_tokens"] += resp.usage.prompt_tokens
            _usage["output_tokens"] += resp.usage.completion_tokens
        _usage["calls"] += 1
        _save_cost()
        data = json.loads(resp.choices[0].message.content)
        return RawExtraction(**data)
    except ValidationError as e:
        logger.warning("Validation error for %s: %s", raw.get("posting_id"), e)
        _usage["calls"] += 1
        _save_cost()
        return RawExtraction()
    except Exception as e:
        logger.error("LLM extraction failed for %s: %s", raw.get("posting_id"), e)
        return RawExtraction()


# US location keywords for heuristic fallback
_US_KEYWORDS = [
    "united states", "usa", "u.s.", " us ", "remote",
    "new york", "san francisco", "seattle", "austin", "chicago",
    "boston", "denver", "atlanta", "nyc", "sf", "bay area",
    "los angeles", "washington dc", "philadelphia", "miami",
    "portland", "dallas", "mountain view", "palo alto",
    "menlo park", "sunnyvale", "san jose", "bellevue",
]
_NON_US_KEYWORDS = [
    "london", " uk", "berlin", "germany", "paris", "france",
    "toronto", "canada", "amsterdam", "netherlands", "singapore",
    "india", "bangalore", "barcelona", "spain", "dublin", "ireland",
    "sydney", "australia", "cologne", "munich", "zurich",
    "switzerland", "poland", "warsaw",
]


def _is_us_location(loc: str) -> bool:
    loc = loc.lower()
    if any(k in loc for k in _NON_US_KEYWORDS):
        return False
    return any(k in loc for k in _US_KEYWORDS)


def _infer_location(
    location_raw: str, extraction: RawExtraction
) -> tuple[str | None, str | None, str | None]:
    city = extraction.city
    country = extraction.country
    remote_policy = extraction.remote_policy
    loc = location_raw.lower()

    # Heuristic remote_policy from location string if LLM didn't extract it
    if not remote_policy:
        if "remote" in loc:
            remote_policy = "remote"
        elif "hybrid" in loc:
            remote_policy = "hybrid"

    # Only default country to US if location actually looks US
    if not country and _is_us_location(location_raw):
        country = "US"

    return city, country, remote_policy


def _normalize_company(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())


def build_posting(raw: dict, extraction: RawExtraction, now: datetime) -> JobPosting:
    city, country, remote_policy = _infer_location(raw.get("location_raw", ""), extraction)

    posted_date = None
    if extraction.posted_date:
        try:
            posted_date = date.fromisoformat(extraction.posted_date)
        except ValueError:
            pass

    # Greenhouse/Lever have company in raw; HN posts get it from LLM extraction
    company = raw.get("company") or extraction.company or ""

    return JobPosting(
        posting_id=raw["posting_id"],
        company=_normalize_company(company),
        title=raw.get("title", ""),
        seniority=extraction.seniority,
        city=city,
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
        source_urls=[raw["url"]] if raw.get("url") else [],
        evidence=extraction.evidence,
        first_seen=now,
        last_seen=now,
    )
