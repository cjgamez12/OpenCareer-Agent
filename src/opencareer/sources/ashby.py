"""Fetch jobs from a company's public Ashby board (jobs.ashbyhq.com/NAME). No API key needed."""
import requests

from opencareer.models import JobPosting
from opencareer.sources._dates import parse_datetime

API_URL = "https://api.ashbyhq.com/posting-api/job-board/{board}"
SOURCE = "ashby"


def _locations(item: dict) -> str | None:
    places = [item.get("location")]
    for extra in item.get("secondaryLocations") or []:
        places.append(extra.get("location") if isinstance(extra, dict) else extra)
    places = [p for p in places if p]
    return "; ".join(dict.fromkeys(places)) or None


def parse_job(item: dict, company: str) -> JobPosting:
    location = _locations(item)
    workplace = (item.get("workplaceType") or "").lower()
    return JobPosting(
        source=SOURCE,
        source_id=str(item["id"]),
        title=item["title"],
        company=company,
        url=item.get("jobUrl") or item.get("applyUrl") or "",
        location=location,
        remote=bool(item.get("isRemote")) or workplace == "remote",
        description=item.get("descriptionPlain") or "",
        posted_at=parse_datetime(item.get("publishedAt")),
    )


def parse_jobs(data: dict, company: str) -> list[JobPosting]:
    return [parse_job(item, company) for item in (data or {}).get("jobs", [])
            if item.get("isListed", True) and item.get("id") and item.get("title")]


def fetch_jobs(board: str, company: str) -> list[JobPosting]:
    response = requests.get(API_URL.format(board=board), timeout=30)
    response.raise_for_status()
    return parse_jobs(response.json(), company)
