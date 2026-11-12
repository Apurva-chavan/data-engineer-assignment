from __future__ import annotations
from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel, field_validator
import re


SENIORITY = {"junior", "mid", "senior", "staff_plus"}
REMOTE_POLICY = {"onsite", "hybrid", "remote"}
EMPLOYMENT_TYPE = {"full_time", "contract", "intern"}
STATUS = {"open", "closed"}


class JobPosting(BaseModel):
    posting_id: str
    company: str
    title: str
    seniority: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    remote_policy: Optional[str] = None
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    currency: Optional[str] = None
    employment_type: Optional[str] = None
    skills: list[str] = []
    visa_sponsorship: Optional[bool] = None
    posted_date: Optional[date] = None
    status: str = "open"
    source_urls: list[str] = []
    evidence: dict[str, Optional[str]] = {}
    first_seen: datetime
    last_seen: datetime

    @field_validator("seniority")
    @classmethod
    def validate_seniority(cls, v):
        if v is not None and v not in SENIORITY:
            return None
        return v

    @field_validator("remote_policy")
    @classmethod
    def validate_remote_policy(cls, v):
        if v is not None and v not in REMOTE_POLICY:
            return None
        return v

    @field_validator("employment_type")
    @classmethod
    def validate_employment_type(cls, v):
        if v is not None and v not in EMPLOYMENT_TYPE:
            return None
        return v

    @field_validator("status")
    @classmethod
    def validate_status(cls, v):
        if v not in STATUS:
            return "open"
        return v

    @field_validator("salary_min", "salary_max")
    @classmethod
    def validate_salary(cls, v):
        if v is not None and (v < 0 or v > 10_000_000):
            return None
        return v

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v):
        if v is not None and not re.match(r"^[A-Z]{3}$", v):
            return None
        return v


class RawExtraction(BaseModel):
    """LLM output before merging with structured API fields."""
    seniority: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    remote_policy: Optional[str] = None
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    currency: Optional[str] = None
    employment_type: Optional[str] = None
    skills: list[str] = []
    visa_sponsorship: Optional[bool] = None
    posted_date: Optional[str] = None
    evidence: dict[str, Optional[str]] = {}
