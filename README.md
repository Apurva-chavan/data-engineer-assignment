# Data Engineer Assignment Pipeline

## Quick Start

```bash
pip install -r requirements.txt
cp .env.example .env   # add your OPENAI_API_KEY
python -m pipeline run
```

## Architecture

```
pipeline/
  scrapers/
    greenhouse.py   # Greenhouse JSON API → raw postings
    lever.py        # Lever JSON API → raw postings
    hn.py           # HN HTML scraper → raw comments
  extractor.py      # LLM extraction + Pydantic validation
  dedup.py          # Fuzzy dedup across sources
  storage.py        # SQLite: canonical records + change history
  http_client.py    # Shared rate-limited, retry-aware HTTP client
  models.py         # Pydantic schema
  __main__.py       # Entry point: python -m pipeline run
tests/
  test_dedup.py
  test_change_history.py
cache/              # Raw HTTP responses (JSON/HTML), keyed by URL hash
data/               # jobs.db (SQLite), jobs.csv, labeled_set.csv
```

- **Plaid → Neon**: `plaid` returns 404 on the Lever API (company migrated ATS); swapped to `neon` (Neon Serverless Postgres) which returns valid JSON. Noted per assignment instructions.
- **HN comment selector**: HN uses `div.commtext` not `span.commtext`; fixed in scraper.

### Design Trade-offs

- **SQLite over Postgres**: zero-ops for a take-home; swap the connection string for Postgres in production.
- **Raw cache on disk**: every HTTP response is written to `cache/` before parsing. Re-runs skip the network entirely if the cache is fresh (TTL=6h). This also lets you replay extraction without re-scraping.
- **LLM cost control**: raw HTML is stripped to plain text and truncated to 3,000 tokens before sending to the LLM. We use `gpt-4o-mini` (~$0.002/posting). Greenhouse/Lever structured fields (title, company, location) are filled from the API response directly; the LLM only handles free-text fields (salary, skills, visa, seniority).
- **Dedup strategy**: exact `(company_normalized, title_normalized)` match first; then fuzzy title match (RapidFuzz token_sort_ratio ≥ 88) within the same company. Merged records keep all source URLs.
- **Change history**: before any UPDATE, the old row is copied to `job_history` with a `changed_at` timestamp and a `diff` JSON column.
- **Idempotency**: `posting_id` is a stable hash of `(source, external_id)`. INSERT OR IGNORE on first seen; UPDATE only when a tracked field changes.

### What Breaks First

1. **HN pagination**: the "More" link structure changes occasionally; the scraper will log a warning and stop paginating rather than crash.
2. **Greenhouse HTML-in-JSON**: some tokens return descriptions as raw HTML with inline `<script>` tags; the stripper handles common cases but exotic JS widgets will produce garbled text.
3. **LLM hallucination on salary**: the prompt explicitly instructs null-over-guess, but very long postings sometimes confuse the model. The Pydantic validator rejects non-null salaries that don't look like numbers.
4. **Lever content-type**: the endpoint occasionally returns `text/html` instead of `application/json`; the client detects this and falls back to HTML parsing.

### Scaling to 5,000 Sources / Weekly Refresh

| Concern | Approach |
|---|---|
| Throughput | Async HTTP (httpx + asyncio), 10 concurrent domains, still 1 req/s per domain |
| LLM cost | Pre-filter with a cheap classifier (fastText or keyword regex) before sending to GPT; batch 20 postings per API call using structured outputs. At $0.002/posting × 5,000 sources × ~50 new jobs/week = ~$500/week — acceptable |
| Storage | Migrate to Postgres + pgvector for semantic dedup; partition `job_history` by month |
| Orchestration | Prefect or Dagster for retries, alerting, and per-source scheduling; no Kafka/Spark needed at this scale |
| Dedup at scale | Blocking on `(company_slug, date_bucket)` before fuzzy match keeps O(n) instead of O(n²) |

### Tools Used

- Amazon Q Developer (AI coding assistant) — used for scaffolding modules and suggesting Pydantic schema
- Groq API (`qwen/qwen3.8-27b`) — LLM extraction (free tier, OpenAI-compatible)
- All scraping, dedup, and storage logic written manually

### LLM Cost Results (from labeled run)

See `data/quality_report.json` after running `python -m pipeline evaluate`.

Groq free tier used — $0 cost. Equivalent OpenAI cost: ~$0.002 per posting × 40 postings ≈ **$0.08 total**.

### Labeled Set Notes

`data/labeled_set.csv` contains 35 postings (12 from HN, 21 from Greenhouse, 2 from Lever).
Labels were verified against the raw text for each posting. Field-level accuracy from `quality_report.json`:
- seniority: 100% (25/25), city: 100% (22/22), country: 100% (33/33)
- remote_policy: 100% (18/18), employment_type: 100% (29/29)
- salary_min/max: 100% (4/4) — most postings don't state salary
- visa_sponsorship: 50% (1/2) — only 2 postings stated it
