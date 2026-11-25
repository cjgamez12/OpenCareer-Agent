"""Tests for SQLite storage and deduplication."""
from datetime import date, datetime

import pytest

from opencareer.models import JobPosting
from opencareer.storage import connect, load_jobs, save_jobs


def make_job(**overrides):
    fields = {
        "source": "greenhouse",
        "source_id": "123",
        "title": "Software Engineer I",
        "company": "Acme",
        "url": "https://example.com/jobs/123",
        "location": "Austin, TX",
    }
    fields.update(overrides)
    return JobPosting(**fields)


@pytest.fixture
def conn():
    """A fresh in-memory database for each test (nothing written to disk)."""
    c = connect(":memory:")
    yield c
    c.close()


def test_empty_database_has_no_jobs(conn):
    assert load_jobs(conn) == []


def test_save_and_load_round_trip(conn):
    job = make_job(
        remote=True,
        posted_at=datetime(2026, 11, 1, 9, 30),
        deadline=date(2026, 12, 15),
        deadline_source="usajobs.ApplicationCloseDate",
        tags=("python", "entry"),
    )
    save_jobs(conn, [job])
    assert load_jobs(conn) == [job]


def test_save_returns_number_of_new_jobs(conn):
    jobs = [
        make_job(source_id="1", title="Software Engineer I"),
        make_job(source_id="2", title="IT Support Specialist"),
    ]
    assert save_jobs(conn, jobs) == 2


def test_same_job_saved_twice_is_stored_once(conn):
    job = make_job()
    save_jobs(conn, [job])
    assert save_jobs(conn, [job]) == 0
    assert len(load_jobs(conn)) == 1


def test_same_job_from_two_sources_is_stored_once(conn):
    a = make_job(source="greenhouse", source_id="1")
    b = make_job(source="lever", source_id="abc", title="  software engineer i ")
    assert save_jobs(conn, [a, b]) == 1
    assert load_jobs(conn)[0].source == "greenhouse"   # first one wins


def test_jobs_survive_reconnect(tmp_path):
    path = str(tmp_path / "jobs.db")
    c1 = connect(path)
    save_jobs(c1, [make_job()])
    c1.close()

    c2 = connect(path)
    assert len(load_jobs(c2)) == 1
    c2.close()
