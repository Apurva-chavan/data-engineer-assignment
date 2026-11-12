"""Tests for change history and upsert logic."""
import json
import sqlite3
import pytest
from datetime import datetime, date

from pipeline.models import JobPosting
from pipeline.storage import init_db, upsert, get_all


def make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def make_posting(**kwargs) -> JobPosting:
    defaults = dict(
        posting_id="test_001",
        company="stripe",
        title="Data Engineer",
        seniority="senior",
        city="San Francisco",
        country="US",
        remote_policy="hybrid",
        salary_min=150000,
        salary_max=200000,
        currency="USD",
        employment_type="full_time",
        skills=["Python", "Spark"],
        visa_sponsorship=True,
        posted_date=date(2024, 1, 15),
        status="open",
        source_urls=["https://stripe.com/jobs/1"],
        evidence={"salary_min": "$150k"},
        first_seen=datetime(2024, 1, 1),
        last_seen=datetime(2024, 1, 1),
    )
    defaults.update(kwargs)
    return JobPosting(**defaults)


class TestUpsert:
    def test_insert_new(self):
        conn = make_conn()
        result = upsert(conn, make_posting())
        assert result == "inserted"
        rows = get_all(conn)
        assert len(rows) == 1
        assert rows[0]["posting_id"] == "test_001"

    def test_idempotent_rerun(self):
        conn = make_conn()
        upsert(conn, make_posting())
        result = upsert(conn, make_posting())
        assert result == "unchanged"
        rows = get_all(conn)
        assert len(rows) == 1

    def test_update_on_salary_change(self):
        conn = make_conn()
        upsert(conn, make_posting())
        updated = make_posting(salary_min=160000)
        result = upsert(conn, updated)
        assert result == "updated"
        rows = get_all(conn)
        assert rows[0]["salary_min"] == 160000

    def test_change_history_recorded(self):
        conn = make_conn()
        upsert(conn, make_posting())
        upsert(conn, make_posting(salary_min=160000))
        history = conn.execute("SELECT * FROM job_history WHERE posting_id = 'test_001'").fetchall()
        assert len(history) == 1
        diff = json.loads(history[0]["diff"])
        assert "salary_min" in diff
        assert diff["salary_min"]["old"] == 150000
        assert diff["salary_min"]["new"] == 160000

    def test_multiple_changes_accumulate_history(self):
        conn = make_conn()
        upsert(conn, make_posting())
        upsert(conn, make_posting(salary_min=160000))
        upsert(conn, make_posting(salary_min=160000, city="New York"))
        history = conn.execute("SELECT * FROM job_history WHERE posting_id = 'test_001'").fetchall()
        assert len(history) == 2

    def test_source_urls_merged_on_unchanged(self):
        conn = make_conn()
        upsert(conn, make_posting(source_urls=["https://a.com"]))
        upsert(conn, make_posting(source_urls=["https://b.com"]))
        rows = get_all(conn)
        urls = rows[0]["source_urls"]
        assert "https://a.com" in urls
        assert "https://b.com" in urls

    def test_status_change_to_closed(self):
        conn = make_conn()
        upsert(conn, make_posting())
        upsert(conn, make_posting(status="closed"))
        rows = get_all(conn)
        assert rows[0]["status"] == "closed"
        history = conn.execute("SELECT * FROM job_history").fetchall()
        assert len(history) == 1
