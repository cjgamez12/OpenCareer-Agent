"""Job discovery pipeline: fetch -> filter -> save -> rank."""
from dataclasses import replace
from datetime import date

import re

import requests

from opencareer import boards as board_registry
from opencareer import locations, ranking, storage, tracker
from opencareer.filters import EXCLUDE_WORDS, _has_word, classify_category, is_entry_level
from opencareer.models import JobPosting
from opencareer.sources import greenhouse, registry

BOARDS = {
    "cloudflare": "Cloudflare",
    "datadog": "Datadog",
    "robinhood": "Robinhood",
    "discord": "Discord",
    "twilio": "Twilio",
    "okta": "Okta",
    "coinbase": "Coinbase",
}


# Titles that mean "not entry level" beyond the basic senior words:
# government "Supervisory ..." roles and company levels L4+/P3+ (e.g. Twilio "(L4)").
TOO_SENIOR = re.compile(r"\b(?:supervisory|supv|supervisor)\b|\b(?:L[4-9]|P[3-9])\b", re.IGNORECASE)
CYBER = re.compile(r"cyber|infosec|information security", re.IGNORECASE)

# Federal jobs: a bachelor's degree generally qualifies for GS-5, and GS-7 with Superior
# Academic Achievement (e.g. a 3.0+ GPA). GS-9 needs a master's or a year of GS-7 experience.
# Jobs that START above GS-7 are filtered out. Only GS-style pay plans use these numbers:
# pay bands like NT, DS, NH ("band 4") mean something else, so they're never treated as GS.
MAX_START_GRADE = 7
GS_LIKE_PLANS = {"GS", "GG"}
GRADE_LINE = re.compile(r"^Grade: ([A-Z]{2})-(\d{1,2})(?: to [A-Z]{2}-(\d{1,2}))?", re.MULTILINE)
RECENT_GRADS_LINE = "Hiring path: Recent Graduates"


def start_grade(job: JobPosting) -> int | None:
    """Lowest GS-style grade of a federal job, or None (non-federal jobs and pay-band plans)."""
    match = GRADE_LINE.search(job.description or "")
    if not match or match.group(1) not in GS_LIKE_PLANS:
        return None
    return int(match.group(2))


def is_pay_band(job: JobPosting) -> bool:
    match = GRADE_LINE.search(job.description or "")
    return bool(match) and match.group(1) not in GS_LIKE_PLANS


def grade_label(job: JobPosting) -> str:
    match = GRADE_LINE.search(job.description or "")
    if not match:
        return ""
    plan, low, high = match.groups()
    label = f"{plan}-{int(low)}" + (f" to {int(high)}" if high and high != low else "")
    return label + (" (pay band: check qualifications)" if plan not in GS_LIKE_PLANS else "")


def category_of(job: JobPosting) -> str | None:
    """classify_category, plus: anything cybersecurity / infosec counts as cybersecurity."""
    if any(_has_word(job.title, word) for word in EXCLUDE_WORDS):
        return None
    category = classify_category(job)
    if CYBER.search(job.title):
        return "cybersecurity"
    return category

NEW_GRAD_SIGNALS = re.compile(
    r"new grad|new graduate|recent graduate|university grad|university graduate|"
    r"early career|entry[- ]level|\b(?:L[12]|P[12])\b", re.IGNORECASE)
INTERNSHIP = re.compile(r"\bintern(?:ship)?s?\b|\bco-?op\b", re.IGNORECASE)
NEW_GRAD_BONUS = 25
INTERNSHIP_PENALTY = 40


def _grade_too_high(job: JobPosting) -> bool:
    grade = start_grade(job)
    return grade is not None and grade > MAX_START_GRADE


def _entry_level(job: JobPosting) -> bool:
    return is_entry_level(job) and not TOO_SENIOR.search(job.title) and not _grade_too_high(job)


def select_jobs(jobs: list[JobPosting]) -> list[JobPosting]:
    """Keep entry-level jobs in our categories, tagged with their category."""
    selected = []
    for job in jobs:
        if not _entry_level(job):
            continue
        category = category_of(job)
        if category is None:
            continue
        selected.append(replace(job, tags=(category,)))
    return selected


def why_not_suggested(job: JobPosting) -> str:
    """Short reason a job was filtered out, for the company page."""
    if _grade_too_high(job):
        return f"starts at {grade_label(job)}: needs a master's or federal experience (you qualify for GS-5/7)"
    if not _entry_level(job):
        return "senior / leadership title"
    if category_of(job) is None:
        return "not software, security, networking, or IT"
    return ""


def fetch_board(conn, board: str, company: str) -> tuple[list[JobPosting], list[JobPosting], int]:
    """Fetch one company's open jobs and save the ones that match you.

    Returns (every open job, the matching ones, how many were new).
    Raises requests.RequestException on network errors.
    """
    jobs = registry.fetch(board, company)
    chosen = select_jobs(jobs)
    board_registry.remember(conn, chosen, board)
    new = storage.save_jobs(conn, chosen)
    return jobs, chosen, new


def run(conn, boards: dict[str, str]) -> dict:
    """Fetch every board, keep the jobs we want, save them. Return counts."""
    stats = {"fetched": 0, "selected": 0, "new": 0, "errors": []}
    for board, company in boards.items():
        try:
            jobs, chosen, new = fetch_board(conn, board, company)
        except requests.RequestException:
            stats["errors"].append(board)
            continue
        stats["fetched"] += len(jobs)
        stats["selected"] += len(chosen)
        stats["new"] += new
    return stats


def new_grad_adjustment(job: JobPosting) -> int:
    """New-grad mode: favor new-grad / early-career roles, push internships down."""
    points = 0
    if NEW_GRAD_SIGNALS.search(job.title) or RECENT_GRADS_LINE in (job.description or ""):
        points += NEW_GRAD_BONUS
    grade = start_grade(job)
    if grade is not None and grade <= 7:
        points += 20  # GS-5 / GS-7: what a bachelor's degree qualifies for
    if INTERNSHIP.search(job.title):
        points -= INTERNSHIP_PENALTY
    return points


def rank_with_targets(jobs: list[JobPosting], today: date, targets=None,
                      new_grad: bool = False) -> list[tuple[int, JobPosting]]:
    """Rank jobs, adding location bonuses and (optionally) new-grad adjustments."""
    ranked = ranking.rank_jobs(jobs, today)
    if targets or new_grad:
        adjusted = []
        for score, job in ranked:
            target = locations.matching_target(job, targets) if targets else None
            score += target.points if target else 0
            if new_grad:
                score += new_grad_adjustment(job)
            adjusted.append((score, job))
        adjusted.sort(key=lambda pair: pair[0], reverse=True)
        ranked = adjusted
    return ranked


def shortlist(conn, today: date, limit: int = 25, targets=None,
              new_grad: bool = False) -> list[tuple[int, JobPosting]]:
    """Current filters, minus jobs already applied to, ranked best first."""
    applied = tracker.applied_urls(conn)
    current = [job for job in select_jobs(storage.load_jobs(conn)) if job.url not in applied]
    return rank_with_targets(current, today, targets, new_grad)[:limit]


if __name__ == "__main__":
    conn = storage.connect("jobs.db")
    print(run(conn, board_registry.all_boards(conn, BOARDS)))
    print()
    for score, job in shortlist(conn, date.today(), targets=locations.list_targets(conn)):
        print(f"{score:>3}  [{job.tags[0]}] {job.title} | {job.company} | {job.location}")
        print(f"     {job.url}")
    conn.close()
