"""SQLite storage: canonical records + change history."""
from __future__ import annotations
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from pipeline.models import JobPosting

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "jobs.db"
DB_PATH.parent.mkdir(exist_ok=True)

TRACKED_FIELDS = {"title", "salary_min", "salary_max", "currency", "city", "country",
                  "remote_policy", "status", "employment_type", "seniority"}


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS jobs (
        posting_id TEXT PRIMARY KEY,
        company TEXT,
        title TEXT,
        seniority TEXT,
        city TEXT,
        country TEXT,
        remote_policy TEXT,
        salary_min REAL,
        salary_max REAL,
        currency TEXT,
        employment_type TEXT,
        skills TEXT,
        visa_sponsorship INTEGER,
        posted_date TEXT,
        status TEXT DEFAULT 'open',
        source_urls TEXT,
        evidence TEXT,
        first_seen TEXT,
        last_seen TEXT
    );

    CREATE TABLE IF NOT EXISTS job_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        posting_id TEXT,
        changed_at TEXT,
        diff TEXT
    );
    """)
    conn.commit()


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    for f in ("skills", "source_urls", "evidence"):
        if d.get(f):
            try:
                d[f] = json.loads(d[f])
            except Exception:
                d[f] = []
    return d


def upsert(conn: sqlite3.Connection, posting: JobPosting) -> str:
    """Insert or update a posting. Returns 'inserted', 'updated', or 'unchanged'."""
    existing_row = conn.execute(
        "SELECT * FROM jobs WHERE posting_id = ?", (posting.posting_id,)
    ).fetchone()

    now = datetime.now(timezone.utc).isoformat()

    if existing_row is None:
        conn.execute(
            """INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                posting.posting_id,
                posting.company,
                posting.title,
                posting.seniority,
                posting.city,
                posting.country,
                posting.remote_policy,
                posting.salary_min,
                posting.salary_max,
                posting.currency,
                posting.employment_type,
                json.dumps(posting.skills),
                None if posting.visa_sponsorship is None else int(posting.visa_sponsorship),
                posting.posted_date.isoformat() if posting.posted_date else None,
                posting.status,
                json.dumps(posting.source_urls),
                json.dumps(posting.evidence),
                posting.first_seen.isoformat(),
                posting.last_seen.isoformat(),
            ),
        )
        conn.commit()
        return "inserted"

    existing = _row_to_dict(existing_row)
    diff = {}
    for field in TRACKED_FIELDS:
        old_val = existing.get(field)
        new_val = getattr(posting, field, None)
        if new_val is not None and str(old_val) != str(new_val):
            diff[field] = {"old": old_val, "new": new_val}

    # Always merge source_urls
    existing_urls = set(existing.get("source_urls") or [])
    new_urls = set(posting.source_urls)
    merged_urls = list(existing_urls | new_urls)

    if diff:
        # Save history before update
        conn.execute(
            "INSERT INTO job_history (posting_id, changed_at, diff) VALUES (?,?,?)",
            (posting.posting_id, now, json.dumps(diff)),
        )
        set_clauses = ", ".join(f"{k} = ?" for k in diff)
        values = [diff[k]["new"] for k in diff]
        values += [now, json.dumps(merged_urls), posting.posting_id]
        conn.execute(
            f"UPDATE jobs SET {set_clauses}, last_seen = ?, source_urls = ? WHERE posting_id = ?",
            values,
        )
        conn.commit()
        return "updated"

    # No field changes but update last_seen + merge URLs
    conn.execute(
        "UPDATE jobs SET last_seen = ?, source_urls = ? WHERE posting_id = ?",
        (now, json.dumps(merged_urls), posting.posting_id),
    )
    conn.commit()
    return "unchanged"


def get_all(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM jobs").fetchall()
    return [_row_to_dict(r) for r in rows]


def get_by_company_title(conn: sqlite3.Connection) -> list[dict]:
    """Return all jobs for dedup lookup."""
    return get_all(conn)


def mark_closed(conn: sqlite3.Connection, posting_id: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    existing = conn.execute(
        "SELECT status FROM jobs WHERE posting_id = ?", (posting_id,)
    ).fetchone()
    if existing and existing["status"] != "closed":
        conn.execute(
            "INSERT INTO job_history (posting_id, changed_at, diff) VALUES (?,?,?)",
            (posting_id, now, json.dumps({"status": {"old": "open", "new": "closed"}})),
        )
        conn.execute(
            "UPDATE jobs SET status = 'closed', last_seen = ? WHERE posting_id = ?",
            (now, posting_id),
        )
        conn.commit()


def export_csv(conn: sqlite3.Connection, path: str) -> None:
    import csv
    rows = get_all(conn)
    if not rows:
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        for row in rows:
            # Flatten lists for CSV
            for k in ("skills", "source_urls", "evidence"):
                if isinstance(row.get(k), (list, dict)):
                    row[k] = json.dumps(row[k])
            writer.writerow(row)
    logger.info("Exported %d rows to %s", len(rows), path)
