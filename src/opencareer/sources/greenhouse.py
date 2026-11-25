import html
import re
from datetime import date, datetime

import requests

from opencareer.models import JobPosting

API_URL = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs"
SOURCE = "greenhouse"


def _parse_datetime(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _parse_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def parse_job(item, company):
    location = (item.get("location") or {}).get("name")
    deadline = _parse_date(item.get("application_deadline"))
    return JobPosting(
        source=SOURCE, source_id=str(item["id"]), title=item["title"],
        company=item.get("company_name") or company, url=item["absolute_url"],
        location=location, remote="remote" in (location or "").lower(),
        posted_at=_parse_datetime(item.get("first_published")),
        deadline=deadline,
        deadline_source="greenhouse.application_deadline" if deadline else None,
    )


def parse_jobs(data, company):
    jobs = []
    for item in data.get("jobs", []):
        jobs.append(parse_job(item, company))
    return jobs


def fetch_jobs(board, company):
    response = requests.get(API_URL.format(board=board), timeout=15)
    response.raise_for_status()
    return parse_jobs(response.json(), company)


JOB_URL = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}"


def html_to_text(raw: str) -> str:
    """Greenhouse sends the description as escaped HTML. Turn it into plain text."""
    text = html.unescape(raw or "")
    text = re.sub(r"<(br|/p|/li|/h\d|/div)\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<li[^>]*>", "- ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def fetch_job_description(board: str, job_id: str) -> str:
    """Download one job's full description as plain text."""
    response = requests.get(JOB_URL.format(board=board, job_id=job_id), timeout=15)
    response.raise_for_status()
    return html_to_text(response.json().get("content", ""))
