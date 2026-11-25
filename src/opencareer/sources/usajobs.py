"""Search US government jobs on USAJOBS. Needs a free API key in .env:

    USAJOBS_API_KEY=...
    USAJOBS_EMAIL=the email you requested the key with

Request a key at https://developer.usajobs.gov/apirequest/
"""
import os

import requests

from opencareer.models import JobPosting
from opencareer.sources._dates import parse_date, parse_datetime

API_URL = "https://data.usajobs.gov/api/Search"
SOURCE = "usajobs"


class MissingCredentials(requests.RequestException):
    """No USAJOBS key yet. Counted like a network error, so other sources keep working."""


def has_credentials() -> bool:
    return bool(os.environ.get("USAJOBS_API_KEY") and os.environ.get("USAJOBS_EMAIL"))


def _grade_line(d: dict, details: dict) -> str:
    """e.g. "Grade: GS-07 to GS-09". Saved as the first line of the description."""
    plan = ((d.get("JobGrade") or [{}])[0] or {}).get("Code") or "GS"
    low, high = details.get("LowGrade"), details.get("HighGrade")
    if not low:
        return ""
    if high and high != low:
        return f"Grade: {plan}-{low} to {plan}-{high}"
    return f"Grade: {plan}-{low}"


def parse_job(item: dict, recent_graduates: bool = False) -> JobPosting:
    d = item["MatchedObjectDescriptor"]
    details = ((d.get("UserArea") or {}).get("Details") or {})
    deadline = parse_date(d.get("ApplicationCloseDate"))
    header = [line for line in (_grade_line(d, details),
                                "Hiring path: Recent Graduates" if recent_graduates else "") if line]
    summary = details.get("JobSummary") or d.get("QualificationSummary") or ""
    return JobPosting(
        source=SOURCE,
        source_id=str(item.get("MatchedObjectId") or d["PositionID"]),
        title=d["PositionTitle"],
        company=d.get("OrganizationName") or d.get("DepartmentName") or "US Government",
        url=d.get("PositionURI") or "",
        location=d.get("PositionLocationDisplay"),
        remote="remote" in (d.get("PositionLocationDisplay") or "").lower(),
        description="\n".join(header + ([""] if header else []) + [summary]).strip(),
        posted_at=parse_datetime(d.get("PublicationStartDate")),
        deadline=deadline,
        deadline_source="usajobs.ApplicationCloseDate" if deadline else None,
    )


def parse_jobs(data: dict, recent_graduates: bool = False) -> list[JobPosting]:
    items = ((data or {}).get("SearchResult") or {}).get("SearchResultItems") or []
    return [parse_job(item, recent_graduates) for item in items if item.get("MatchedObjectDescriptor")]


def _search(params: dict) -> dict:
    response = requests.get(
        API_URL,
        params=params,
        headers={
            "Host": "data.usajobs.gov",
            "User-Agent": os.environ["USAJOBS_EMAIL"],
            "Authorization-Key": os.environ["USAJOBS_API_KEY"],
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def fetch_jobs(keyword: str, company: str = "USAJOBS") -> list[JobPosting]:
    """keyword comes from the board name, e.g. "it-specialist" searches "it specialist".

    Runs two searches: all jobs, and jobs open to Recent Graduates (marked in the description).
    """
    if not has_credentials():
        raise MissingCredentials("USAJOBS needs USAJOBS_API_KEY and USAJOBS_EMAIL in .env")
    words = keyword.replace("-", " ")
    graduates = parse_jobs(_search({"Keyword": words, "ResultsPerPage": 100, "HiringPath": "graduates"}),
                           recent_graduates=True)
    everyone = parse_jobs(_search({"Keyword": words, "ResultsPerPage": 100}))
    seen = {job.source_id for job in graduates}
    return graduates + [job for job in everyone if job.source_id not in seen]
