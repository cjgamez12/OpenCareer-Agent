"""Tests for the application tracker."""
from dataclasses import replace
from datetime import date

import pytest

from opencareer import pipeline, storage, tracker
from opencareer.models import JobPosting

URL = "https://example.com/1"


@pytest.fixture
def conn():
    c = storage.connect(":memory:")
    yield c
    c.close()


def test_mark_and_read_back(conn):
    tracker.mark_applied(conn, URL, date(2026, 11, 23))
    assert tracker.applied_urls(conn) == {URL}


def test_marking_twice_keeps_one_entry(conn):
    tracker.mark_applied(conn, URL)
    tracker.mark_applied(conn, URL)
    assert len(tracker.list_applications(conn)) == 1
    assert len(tracker.history(conn, URL)) == 1


def test_shortlist_hides_jobs_you_applied_to(conn):
    job = JobPosting(source="greenhouse", source_id="1",
                     title="Software Engineer Intern", company="Acme", url=URL)
    storage.save_jobs(conn, [replace(job, tags=("software",))])
    assert len(pipeline.shortlist(conn, date(2026, 11, 23))) == 1

    tracker.mark_applied(conn, URL)
    assert pipeline.shortlist(conn, date(2026, 11, 23)) == []


def test_apply_schedules_a_follow_up_in_7_days(conn):
    tracker.mark_applied(conn, URL, date(2026, 11, 23))
    assert tracker.due_follow_ups(conn, date(2026, 11, 29)) == []
    assert tracker.due_follow_ups(conn, date(2026, 11, 30)) == [(URL, "applied", "2026-11-30")]


def test_update_changes_status_and_records_round(conn):
    tracker.mark_applied(conn, URL, date(2026, 11, 23))
    tracker.update(conn, URL, "interview", on=date(2026, 12, 1),
                   round_number=2, note="System design")
    assert tracker.list_applications(conn) == [("2026-11-23", "interview", URL)]
    assert tracker.history(conn, URL) == [
        ("2026-11-23", "applied", None, ""),
        ("2026-12-01", "interview", 2, "System design"),
    ]


def test_rejection_cancels_follow_ups(conn):
    tracker.mark_applied(conn, URL, date(2026, 11, 23))
    tracker.update(conn, URL, "rejected", on=date(2026, 11, 25))
    assert tracker.due_follow_ups(conn, date(2026, 12, 31)) == []


def test_custom_follow_up_date(conn):
    tracker.mark_applied(conn, URL, date(2026, 11, 23))
    tracker.update(conn, URL, "recruiter_screen", on=date(2026, 11, 24),
                   follow_up_on=date(2026, 11, 26))
    assert tracker.due_follow_ups(conn, date(2026, 11, 26)) == [
        (URL, "recruiter_screen", "2026-11-26")
    ]


def test_unknown_stage_is_rejected(conn):
    tracker.mark_applied(conn, URL)
    with pytest.raises(ValueError):
        tracker.update(conn, URL, "vibes")


def test_update_before_applying_is_rejected(conn):
    with pytest.raises(ValueError):
        tracker.update(conn, URL, "interview")
