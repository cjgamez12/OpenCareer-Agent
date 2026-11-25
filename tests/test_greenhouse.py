"""Tests for the Greenhouse adapter. These never touch the network."""
import json
from datetime import date
from pathlib import Path

import pytest

from opencareer.sources import greenhouse

FIXTURE = Path(__file__).parent / "fixtures" / "greenhouse_jobs.json"


@pytest.fixture
def data():
    return json.loads(FIXTURE.read_text())


def test_parses_every_job(data):
    assert len(greenhouse.parse_jobs(data, "Example Corp")) == 3


def test_basic_fields(data):
    job = greenhouse.parse_jobs(data, "Example Corp")[0]
    assert job.source == "greenhouse"
    assert job.source_id == "1001"
    assert job.title == "Software Engineer I"
    assert job.company == "Example Corp"
    assert job.url == "https://boards.greenhouse.io/examplecorp/jobs/1001"
    assert job.location == "Remote - US"
    assert job.posted_at.date() == date(2026, 11, 1)


def test_remote_is_detected_from_location(data):
    jobs = greenhouse.parse_jobs(data, "Example Corp")
    assert jobs[0].remote is True
    assert jobs[1].remote is False


def test_verified_deadline_records_its_source(data):
    job = greenhouse.parse_jobs(data, "Example Corp")[0]
    assert job.deadline == date(2026, 12, 15)
    assert job.deadline_source == "greenhouse.application_deadline"


def test_missing_deadline_is_never_guessed(data):
    job = greenhouse.parse_jobs(data, "Example Corp")[1]
    assert job.deadline is None
    assert job.deadline_source is None


def test_missing_fields_use_safe_defaults(data):
    job = greenhouse.parse_jobs(data, "Example Corp")[2]
    assert job.location is None
    assert job.remote is False
    assert job.company == "Example Corp"
    assert job.posted_at is None


def test_empty_board_returns_empty_list():
    assert greenhouse.parse_jobs({"jobs": []}, "X") == []
    assert greenhouse.parse_jobs({}, "X") == []


def test_fetch_jobs_calls_the_right_url(monkeypatch, data):
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return data

    def fake_get(url, **kwargs):
        calls.append(url)
        return FakeResponse()

    monkeypatch.setattr(greenhouse.requests, "get", fake_get)
    jobs = greenhouse.fetch_jobs("examplecorp", "Example Corp")

    assert calls == ["https://boards-api.greenhouse.io/v1/boards/examplecorp/jobs"]
    assert len(jobs) == 3
