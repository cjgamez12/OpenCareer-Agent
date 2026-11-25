"""Rank jobs by entry-level relevance, verified deadline, and freshness."""
from datetime import date

from opencareer.filters import _has_word
from opencareer.models import JobPosting

ENTRY_SIGNALS = {
    "intern": 30,
    "new grad": 30,
    "junior": 25,
    "entry level": 25,
    "associate": 15,
    "i": 10,
}


def is_expired(job: JobPosting, today: date) -> bool:
    """True only if the job HAS a deadline and it is before today."""
    return job.deadline is not None and job.deadline < today


def _relevance_points(job: JobPosting) -> int:
    """Add up points for every entry-level signal in the title."""
    total = 0
    for word, points in ENTRY_SIGNALS.items():
        if _has_word(job.title, word):
            total += points
    return total


def _deadline_points(job: JobPosting, today: date) -> int:
    """Only verified deadlines count. Sooner deadline = more points."""
    if job.deadline is None or job.deadline_source is None:
        return 0
    days_left = (job.deadline - today).days
    if days_left <= 7:
        return 40
    if days_left <= 30:
        return 20
    return 5


def _freshness_points(job: JobPosting, today: date) -> int:
    """Newer postings get more points."""
    if job.posted_at is None:
        return 0
    age_days = (today - job.posted_at.date()).days
    if age_days <= 7:
        return 15
    if age_days <= 30:
        return 5
    return 0


def score_job(job: JobPosting, today: date) -> int:
    return (_relevance_points(job)
            + _deadline_points(job, today)
            + _freshness_points(job, today))


def rank_jobs(jobs: list[JobPosting], today: date) -> list[tuple[int, JobPosting]]:
    """Drop expired jobs, score the rest, best first."""
    scored = [(score_job(job, today), job) for job in jobs if not is_expired(job, today)]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored
