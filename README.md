# Data Engineer Take-Home: Job Posting Pipeline

## Quick Start

```bash
pip install -r requirements.txt
cp .env.example .env        # fill in OPENAI_API_KEY (Groq key works)
python -m pipeline run      # scrape → extract → dedup → store
python -m pipeline evaluate # field-level accuracy + cost report
python -m pytest tests/ -v  # 20 unit tests
```

## Sources

| Source | Method | Companies |
|--------|--------|-----------|
| Greenhouse | JSON API (`boards-api.greenhouse.io`) | stripe, airbnb, databricks, figma |
| Lever | JSON API (`api.lever.co`) | palantir, neon* |
| Hacker News | HTML scraper | "Ask HN: Who is Hiring?" thread |

\* `plaid` returns 404 on the Lever API (migrated ATS); replaced with `neon`.

Filters applied at scrape time: title must match data/ML/analytics/platform engineer patterns; location must be US or US-remote.

## Project Structure

```
pipeline/
  scrapers/
    greenhouse.py   # Greenhouse JSON API
    lever.py        # Lever JSON API
    hn.py           # HN HTML scraper (top-level comments, paginated)
  models.py         # Pydantic schema: JobPosting, RawExtraction
  extractor.py      # LLM extraction + Pydantic validation + cost tracking
  dedup.py          # Fuzzy dedup across sources
  storage.py        # SQLite: canonical records + change history
  http_client.py    # Rate-limited, cached, retry-aware HTTP client
  __main__.py       # Entry point
tests/
  test_dedup.py           # 10 dedup unit tests
  test_change_history.py  # 10 upsert/history unit tests
cache/              # Raw HTTP responses keyed by SHA-256(url), TTL=6h
data/
  jobs.db           # SQLite canonical store
  jobs.csv          # Flat export
  labeled_set.csv   # 65 hand-labeled postings (24 from HN, 38 Greenhouse, 3 Lever)
  quality_report.json
  llm_cost.json     # Persisted token usage (written after every LLM call)
```

## Schema (JobPosting)

| Field | Type | Source |
|-------|------|--------|
| posting_id | str | Stable hash of (source, external_id) |
| company | str | API field (Greenhouse/Lever) or LLM (HN) |
| title | str | API field |
| seniority | junior\|mid\|senior\|staff_plus\|null | LLM |
| city | str\|null | LLM |
| country | str\|null | LLM + heuristic |
| remote_policy | onsite\|hybrid\|remote\|null | LLM + heuristic |
| salary_min / salary_max | float\|null | LLM, annualized |
| currency | str\|null | LLM, ISO 3-letter |
| employment_type | full_time\|contract\|intern\|null | LLM |
| skills | list[str] | LLM |
| visa_sponsorship | bool\|null | LLM (explicit evidence required) |
| posted_date | date\|null | LLM |
| status | open\|closed | Auto-managed |
| source_urls | list[str] | All source links merged across dedup |
| evidence | dict[str, str] | Exact quote per extracted field |
| first_seen / last_seen | datetime | Auto-managed |

A field not stated in the posting is always null — never guessed. The `evidence` dict stores the exact quote from the posting text that justifies each non-null field.

## Design Decisions

### 1. Scraping Politely
- `robots.txt` checked via `urllib.robotparser` before every domain
- 1 req/s per domain enforced in `http_client.py`
- Exponential backoff retry (tenacity): 3 attempts, 2–30s wait
- All failures logged; pipeline continues on error — never crashes

### 2. Caching
Every HTTP response is written to `cache/<sha256(url)>.json` before parsing. Re-runs skip the network entirely if cache is fresh (default TTL = 6h, configurable via `CACHE_TTL_HOURS`). This also lets you replay LLM extraction without re-scraping.

### 3. LLM Extraction
- Provider: Groq API (OpenAI-compatible), model `qwen/qwen3.8-27b` (free tier)
- HTML stripped to plain text, truncated to 12,000 chars before sending
- Greenhouse/Lever structured fields (title, company, location) filled from API directly; LLM handles free-text fields
- For HN posts, company name is also extracted by the LLM (it appears at the start of each comment)
- Output validated with Pydantic `RawExtraction`; validation errors fall back to null fields
- Token usage written to `data/llm_cost.json` after every call so cost is available to `evaluate`

### 4. Deduplication
Same role can appear on company careers page, ATS feed, and HN under different titles:
1. Normalize company name (strip Inc/LLC/Corp noise, lowercase)
2. Normalize title (strip seniority words, roman numerals, punctuation)
3. Exact normalized match → merge
4. Fuzzy match (RapidFuzz `token_sort_ratio` ≥ 88) within same company → merge
5. Merged records keep **all** source URLs

### 5. Incremental / Idempotent Re-runs
- `posting_id` is a stable hash of `(source, external_id)` — same posting always gets same ID
- `upsert()` inserts on first seen; updates only when a tracked field actually changes
- Before any UPDATE, old values written to `job_history` with `changed_at` + `diff` JSON
- Postings absent from the current run are automatically marked `status=closed` with a history entry
- Running twice produces zero duplicates and zero spurious updates

### 6. Quality Measurement

Hand-labeled 65 postings (24 HN, 38 Greenhouse, 3 Lever). Run `python -m pipeline evaluate`.

Results from `data/quality_report.json`:

| Field | Accuracy | Labeled |
|-------|----------|---------|
| seniority | 100% | 47 |
| city | 100% | 41 |
| country | 100% | 63 |
| remote_policy | 100% | 46 |
| salary_min | 100% | 12 |
| salary_max | 100% | 11 |
| currency | 100% | 13 |
| employment_type | 100% | 61 |
| visa_sponsorship | 22% | 9 |

`visa_sponsorship` is low because most postings don't explicitly state it. The prompt requires explicit evidence; re-running after a fresh scrape with the updated prompt should improve this significantly.

### 7. LLM Cost

Groq free tier — **$0 cost**. Equivalent OpenAI `gpt-4o-mini` cost: ~$0.002/posting × ~150 postings ≈ **$0.30 total**. Cost per posting and total token counts are in `data/quality_report.json` under `llm_cost`.

## Known Limitations

1. HN pagination: "More" link structure changes occasionally; scraper logs a warning and stops rather than crashing.
2. Greenhouse HTML-in-JSON: exotic JS widgets in descriptions produce garbled text after stripping.
3. Lever content-type: endpoint occasionally returns `text/html`; client detects and skips.

## Scaling to 5,000 Sources / Weekly Refresh

| Concern | Approach |
|---------|----------|
| Throughput | Async HTTP (httpx + asyncio), 10 concurrent domains, still 1 req/s per domain |
| LLM cost | Pre-filter with keyword regex before LLM; batch 20 postings per API call. ~$500/week at scale |
| Storage | Postgres + pgvector for semantic dedup; partition `job_history` by month |
| Orchestration | Prefect or Dagster for retries, alerting, per-source scheduling |
| Dedup at scale | Block on `(company_slug, date_bucket)` before fuzzy match → O(n) not O(n²) |

## Tools Used

- **Amazon Q Developer** — AI coding assistant used throughout (scaffolding, debugging, prompt tuning)
- **Groq API** (`qwen/qwen3.8-27b`) — LLM extraction, OpenAI-compatible, free tier
