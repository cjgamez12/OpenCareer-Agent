"""Tests for the fetch -> filter -> save pipeline. No network."""
import pytest
import requests

from opencareer import pipeline, storage
from opencareer.models import JobPosting


def make_job(title, source_id="1", company="Acme"):
    return JobPosting(source="greenhouse", source_id=source_id,
                      title=title, company=company, url="u")


def test_keeps_entry_level_job_in_our_categories():
    result = pipeline.select_jobs([make_job("Junior Software Engineer")])
    assert len(result) == 1


def test_selected_job_is_tagged_with_its_category():
    job = pipeline.select_jobs([make_job("SOC Analyst I")])[0]
    assert job.tags == ("cybersecurity",)


def test_drops_senior_jobs():
    assert pipeline.select_jobs([make_job("Senior Software Engineer")]) == []


def test_drops_jobs_outside_our_categories():
    assert pipeline.select_jobs([make_job("Account Executive")]) == []


FAKE_BOARDS = {
    "acme": [
        make_job("Junior Software Engineer", "1"),
        make_job("Senior Network Engineer", "2"),
        make_job("Account Executive", "3"),
    ],
    "globex": [make_job("IT Support Specialist", "9", company="Globex")],
}


@pytest.fixture
def fake_fetch(monkeypatch):
    """Replace the real Greenhouse fetch with fake boards."""
    def fake(board, company):
        if board not in FAKE_BOARDS:
            raise requests.HTTPError("404 Not Found")
        return FAKE_BOARDS[board]
    monkeypatch.setattr(pipeline.greenhouse, "fetch_jobs", fake)


@pytest.fixture
def conn():
    c = storage.connect(":memory:")
    yield c
    c.close()


def test_run_fetches_filters_and_saves(fake_fetch, conn):
    stats = pipeline.run(conn, {"acme": "Acme", "globex": "Globex"})
    assert stats["fetched"] == 4
    assert stats["selected"] == 2
    assert stats["new"] == 2
    assert len(storage.load_jobs(conn)) == 2


def test_second_run_finds_nothing_new(fake_fetch, conn):
    boards = {"acme": "Acme"}
    pipeline.run(conn, boards)
    assert pipeline.run(conn, boards)["new"] == 0


def test_one_broken_board_does_not_stop_the_others(fake_fetch, conn):
    stats = pipeline.run(conn, {"doesnotexist": "Nope", "globex": "Globex"})
    assert stats["new"] == 1
    assert stats["errors"] == ["doesnotexist"]
