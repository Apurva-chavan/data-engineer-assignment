"""Tests for deduplication logic."""
import pytest
from pipeline.dedup import find_duplicate, merge_into, _normalize, _company_key


def make_job(company: str, title: str, urls: list[str] | None = None) -> dict:
    return {"company": company, "title": title, "source_urls": urls or [], "posting_id": f"{company}_{title}"}


class TestNormalize:
    def test_strips_seniority_words(self):
        assert _normalize("Senior Data Engineer") == _normalize("Data Engineer")

    def test_strips_roman_numerals(self):
        assert _normalize("Data Engineer II") == _normalize("Data Engineer")

    def test_case_insensitive(self):
        assert _normalize("DATA ENGINEER") == _normalize("data engineer")


class TestFindDuplicate:
    def test_exact_match(self):
        existing = [make_job("stripe", "Data Engineer")]
        dup = find_duplicate(make_job("stripe", "Data Engineer"), existing)
        assert dup is not None

    def test_seniority_variant(self):
        existing = [make_job("stripe", "Data Engineer")]
        dup = find_duplicate(make_job("stripe", "Senior Data Engineer"), existing)
        assert dup is not None

    def test_different_company_no_match(self):
        existing = [make_job("stripe", "Data Engineer")]
        dup = find_duplicate(make_job("airbnb", "Data Engineer"), existing)
        assert dup is None

    def test_fuzzy_match(self):
        existing = [make_job("databricks", "Data Engineer, Platform")]
        dup = find_duplicate(make_job("databricks", "Sr. Data Engineer Platform"), existing)
        assert dup is not None

    def test_completely_different_title_no_match(self):
        existing = [make_job("stripe", "Software Engineer, Payments")]
        dup = find_duplicate(make_job("stripe", "Data Engineer"), existing)
        assert dup is None

    def test_empty_existing(self):
        assert find_duplicate(make_job("stripe", "Data Engineer"), []) is None

    def test_company_key_normalization(self):
        existing = [make_job("Stripe, Inc.", "Data Engineer")]
        dup = find_duplicate(make_job("stripe", "Data Engineer"), existing)
        assert dup is not None


class TestMergeInto:
    def test_merges_urls(self):
        canonical = make_job("stripe", "Data Engineer", ["https://a.com"])
        dup = make_job("stripe", "Senior Data Engineer", ["https://b.com"])
        result = merge_into(canonical, dup)
        assert "https://a.com" in result["source_urls"]
        assert "https://b.com" in result["source_urls"]

    def test_no_duplicate_urls(self):
        canonical = make_job("stripe", "Data Engineer", ["https://a.com"])
        dup = make_job("stripe", "Data Engineer", ["https://a.com"])
        result = merge_into(canonical, dup)
        assert result["source_urls"].count("https://a.com") == 1

    def test_empty_dup_urls(self):
        canonical = make_job("stripe", "Data Engineer", ["https://a.com"])
        dup = make_job("stripe", "Data Engineer", [])
        result = merge_into(canonical, dup)
        assert result["source_urls"] == ["https://a.com"]
