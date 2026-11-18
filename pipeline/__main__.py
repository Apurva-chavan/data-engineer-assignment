"""Entry point: python -m pipeline run | evaluate"""
from __future__ import annotations
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def run_pipeline() -> None:
    from pipeline.scrapers import greenhouse, lever, hn
    from pipeline.extractor import extract, build_posting, get_cost_stats
    from pipeline.dedup import find_duplicate, merge_into
    from pipeline import storage

    conn = storage.get_conn()
    storage.init_db(conn)

    now = datetime.now(timezone.utc)
    stats = {"inserted": 0, "updated": 0, "unchanged": 0, "deduped": 0, "errors": 0}

    # Collect raw postings from all 3 sources
    all_raw: list[dict] = []
    for scraper in (greenhouse, lever, hn):
        try:
            all_raw.extend(scraper.scrape())
        except Exception as e:
            logger.error("Scraper %s failed: %s", scraper.__name__, e)

    logger.info("Total raw postings collected: %d", len(all_raw))

    # Track posting_ids seen this run to detect closed postings
    seen_ids: set[str] = set()

    for raw in all_raw:
        try:
            extraction = extract(raw)
            posting = build_posting(raw, extraction, now)
        except Exception as e:
            logger.error("Build posting failed for %s: %s", raw.get("posting_id"), e)
            stats["errors"] += 1
            continue

        # Reload existing records each iteration so newly inserted records
        # are visible for dedup within the same run
        existing_records = storage.get_by_company_title(conn)
        dup = find_duplicate(dict(posting), existing_records)

        if dup and dup["posting_id"] != posting.posting_id:
            # Duplicate found: merge source URLs into canonical record
            merged = merge_into(dup, dict(posting))
            conn.execute(
                "UPDATE jobs SET source_urls = ?, last_seen = ? WHERE posting_id = ?",
                (json.dumps(merged["source_urls"]), now.isoformat(), dup["posting_id"]),
            )
            conn.commit()
            stats["deduped"] += 1
            seen_ids.add(dup["posting_id"])
            continue

        result = storage.upsert(conn, posting)
        stats[result] += 1
        seen_ids.add(posting.posting_id)

    # Mark any previously-open posting not seen this run as closed
    for job in storage.get_all(conn):
        if job["status"] == "open" and job["posting_id"] not in seen_ids:
            storage.mark_closed(conn, job["posting_id"])
            stats["updated"] += 1
            logger.info("Marked closed: %s", job["posting_id"])

    cost = get_cost_stats()
    logger.info("Run complete: %s", stats)
    logger.info("LLM cost this run: %s", cost)

    storage.export_csv(conn, str(Path(__file__).parent.parent / "data" / "jobs.csv"))
    conn.close()


def run_evaluate() -> None:
    """Compute field-level extraction accuracy against labeled_set.csv and report LLM cost."""
    import csv
    from pipeline import storage
    from pipeline.extractor import get_cost_stats

    labeled_path = Path(__file__).parent.parent / "data" / "labeled_set.csv"
    if not labeled_path.exists():
        logger.error("labeled_set.csv not found at %s", labeled_path)
        return

    conn = storage.get_conn()
    all_jobs = {j["posting_id"]: j for j in storage.get_all(conn)}
    conn.close()

    fields = ["seniority", "city", "country", "remote_policy", "salary_min",
              "salary_max", "currency", "employment_type", "visa_sponsorship"]
    correct = {f: 0 for f in fields}
    total = {f: 0 for f in fields}

    with open(labeled_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            pid = row.get("posting_id", "")
            job = all_jobs.get(pid)
            if not job:
                continue
            for field in fields:
                label = row.get(f"label_{field}", "").strip()
                if not label:
                    continue
                total[field] += 1
                predicted = str(job.get(field) if job.get(field) is not None else "").strip()
                # Normalise bool representations: DB stores 0/1 integers
                predicted = predicted.replace("1", "True").replace("0", "False")
                label = label.replace("1", "True").replace("0", "False")
                if predicted.lower() == label.lower():
                    correct[field] += 1

    field_accuracy = {}
    for f in fields:
        acc = correct[f] / total[f] if total[f] else None
        field_accuracy[f] = {"accuracy": round(acc, 4) if acc is not None else None,
                              "correct": correct[f], "total": total[f]}

    report = {
        "field_accuracy": field_accuracy,
        "llm_cost": get_cost_stats(),
    }

    report_path = Path(__file__).parent.parent / "data" / "quality_report.json"
    report_path.write_text(json.dumps(report, indent=2))
    logger.info("Quality report written to %s", report_path)
    for f, v in field_accuracy.items():
        if v["total"]:
            logger.info("  %-20s %.1f%%  (%d/%d)",
                        f, (v["accuracy"] or 0) * 100, v["correct"], v["total"])


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        run_pipeline()
    elif cmd == "evaluate":
        run_evaluate()
    else:
        print(f"Unknown command: {cmd}. Use 'run' or 'evaluate'.")
        sys.exit(1)
