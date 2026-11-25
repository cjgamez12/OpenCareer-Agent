"""Fetch jobs from a company's public Lever board (jobs.lever.co/NAME). No API key needed."""
import requests

from opencareer.models import JobPosting
from opencareer.sources._dates import parse_datetime

API_URL = "https://api.lever.co/v0/postings/{site}"
SOURCE = "lever"


def parse_job(item: dict, company: str) -> JobPosting:
    categories = item.get("categories") or {}
    location = categories.get("location") or "; ".join(categories.get("allLocations") or []) or None
    workplace = (item.get("workplaceType") or "").lower()
    return JobPosting(
        source=SOURCE,
        source_id=str(item["id"]),
        title=item["text"],
        company=company,
        url=item.get("hostedUrl") or item.get("applyUrl") or "",
        location=location,
        remote=workplace == "remote" or "remote" in (location or "").lower(),
        description=item.get("descriptionPlain") or "",
        posted_at=parse_datetime(item.get("createdAt")),
    )


def parse_jobs(data, company: str) -> list[JobPosting]:
    return [parse_job(item, company) for item in (data or []) if item.get("id") and item.get("text")]


def fetch_jobs(site: str, company: str) -> list[JobPosting]:
    response = requests.get(API_URL.format(site=site), params={"mode": "json"}, timeout=20)
    response.raise_for_status()
    return parse_jobs(response.json(), company)
