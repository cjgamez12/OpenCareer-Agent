"""Tests for job ranking. TODAY is fixed so results never change."""
from datetime import date, datetime

import pytest

from opencareer.models import JobPosting
from opencareer.ranking import is_expired, rank_jobs, score_job

TODAY = date(2026, 11, 22)


def make_job(title="Software Engineer", **overrides):
    fields = {"source": "greenhouse", "source_id": title, "title": title,
              "company": "Acme", "url": "u"}
    fields.update(overrides)
    return JobPosting(**fields)


def test_plain_title_scores_zero():
    assert score_job(make_job("Software Engineer"), TODAY) == 0


@pytest.mark.parametrize("title, points", [
    ("Software Engineer Intern", 30),
    ("Software Engineer, New Grad", 30),
    ("Junior Developer", 25),
    ("Associate Security Analyst", 15),
    ("SOC Analyst I", 10),
])
def test_entry_level_signals_add_points(title, points):
    assert score_job(make_job(title), TODAY) == points


def test_verified_deadline_soon_adds_points():
    job = make_job(deadline=date(2026, 11, 25),
                   deadline_source="greenhouse.application_deadline")
    assert score_job(job, TODAY) == 40


def test_unverified_deadline_is_ignored():
    job = make_job(deadline=date(2026, 11, 25))
    assert score_job(job, TODAY) == 0


def test_recent_posting_adds_points():
    job = make_job(posted_at=datetime(2026, 11, 20, 9, 0))
    assert score_job(job, TODAY) == 15


def test_is_expired():
    assert is_expired(make_job(deadline=date(2026, 11, 21)), TODAY) is True
    assert is_expired(make_job(deadline=date(2026, 11, 22)), TODAY) is False
    assert is_expired(make_job(), TODAY) is False


def test_rank_orders_best_first_and_drops_expired():
    plain = make_job("Software Engineer")
    intern = make_job("Software Engineer Intern")
    expired = make_job("Junior Developer", deadline=date(2026, 11, 1),
                       deadline_source="greenhouse.application_deadline")
    ranked = rank_jobs([plain, intern, expired], TODAY)
    assert [job.title for score, job in ranked] == [
        "Software Engineer Intern",
        "Software Engineer",
    ]
