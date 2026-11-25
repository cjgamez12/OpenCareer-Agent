"""Tests for the job radar: new jobs, closed jobs, notifications, and the 30-minute schedule."""
import plistlib
import sqlite3
from datetime import date, datetime
from types import SimpleNamespace

import pytest
import requests
from fastapi.testclient import TestClient

from opencareer import pipeline, radar, storage, tracker
from opencareer.models import JobPosting
from opencareer.sources import registry
from opencareer.web import create_app


def test_first_look_is_a_baseline_then_new_jobs_are_new():
    conn = sqlite3.connect(":memory:")
    first = radar.observe(conn, "acme", {"a", "b"}, ["a", "b"], now="2026-11-25T08:00:00")
    assert first["new"] == []                                    # no flood the first time
    second = radar.observe(conn, "acme", {"a", "b", "c"}, ["a", "b", "c"], now="2026-11-25T08:30:00")
    assert second["new"] == ["c"]
    assert radar.new_urls(conn, now=datetime(2026, 11, 25, 9, 0)) == {"c"}


def test_a_job_closes_after_two_missed_checks_and_can_reopen():
    conn = sqlite3.connect(":memory:")
    radar.observe(conn, "acme", {"a", "b"}, ["a", "b"])
    assert radar.observe(conn, "acme", {"a"}, ["a"])["closed"] == []      # one miss: maybe a hiccup
    assert radar.observe(conn, "acme", {"a"}, ["a"])["closed"] == ["b"]   # two misses: closed
    assert radar.closed_urls(conn) == {"b"}
    assert radar.observe(conn, "acme", {"a", "b"}, ["a", "b"])["reopened"] == ["b"]
    assert radar.closed_urls(conn) == set()


def test_keyword_searches_never_close_jobs():
    conn = sqlite3.connect(":memory:")
    radar.observe(conn, "usajobs:cyber", None, ["x"])
    for _ in range(3):
        radar.observe(conn, "usajobs:cyber", None, [])
    assert radar.closed_urls(conn) == set()


def job(title, n, board="acme"):
    return JobPosting(source="greenhouse", source_id=str(n), title=title, company="Acme",
                      url=f"https://example.com/{board}/{n}", location="San Jose, CA")


@pytest.fixture
def board(monkeypatch):
    """A fake Greenhouse board whose open jobs we can change between checks."""
    state = {"jobs": [job("Junior Software Engineer", 1), job("Associate Network Engineer", 2)]}

    def fetch_jobs(name, company):
        if name != "acme":
            raise requests.HTTPError("404")
        return list(state["jobs"])

    monkeypatch.setitem(registry.MODULES, "greenhouse", SimpleNamespace(fetch_jobs=fetch_jobs))
    return state


def test_pipeline_reports_new_and_closed_and_hides_closed(board, tmp_path):
    conn = storage.connect(str(tmp_path / "jobs.db"))
    first = pipeline.run(conn, {"acme": "Acme"})
    assert first["selected"] == 2 and first["new_urls"] == []
    board["jobs"] = [job("Associate Network Engineer", 2), job("Junior Security Analyst", 3)]
    second = pipeline.run(conn, {"acme": "Acme"})
    assert second["new_urls"] == ["https://example.com/acme/3"] and second["closed_urls"] == []
    third = pipeline.run(conn, {"acme": "Acme"})
    assert third["closed_urls"] == ["https://example.com/acme/1"]
    titles = [j.title for _, j in pipeline.shortlist(conn, date.today())]
    assert "Junior Software Engineer" not in titles and "Junior Security Analyst" in titles


def test_check_notifies_about_new_matches(board, tmp_path):
    conn = storage.connect(str(tmp_path / "jobs.db"))
    sent = []
    radar.check(conn, default_boards={"acme": "Acme"}, profile_dir=tmp_path, notify=lambda *a: sent.append(a))
    assert sent == []                                            # baseline: nothing to announce
    board["jobs"].append(job("Junior Security Analyst", 3))
    summary = radar.check(conn, default_boards={"acme": "Acme"}, profile_dir=tmp_path,
                          notify=lambda *a: sent.append(a))
    assert summary["new"] == ["https://example.com/acme/3"]
    assert sent and sent[0][0] == "1 new job for you" and "Junior Security Analyst · Acme" in sent[0][1]
    assert radar.last_run(conn)["new"] == ["https://example.com/acme/3"]


def test_schedule_runs_every_30_minutes(tmp_path):
    calls = []
    path = radar.install(home=tmp_path, runner=lambda *a, **k: calls.append(a[0]))
    plist = plistlib.loads(path.read_bytes())
    assert plist["StartInterval"] == 1800 and plist["RunAtLoad"] is True
    assert plist["ProgramArguments"][-2:] == ["-m", "opencareer.radar"]
    assert radar.is_on(tmp_path) and calls[-1][:2] == ["launchctl", "load"]
    radar.uninstall(home=tmp_path, runner=lambda *a, **k: None)
    assert not radar.is_on(tmp_path)


def test_dashboard_shows_radar_new_badge_and_closed_postings(board, tmp_path):
    db = str(tmp_path / "jobs.db")
    app = create_app(db_path=db, boards={"acme": "Acme"}, profile_dir=str(tmp_path / "p"),
                     output_dir=str(tmp_path / "a"), schedule_home=tmp_path, launchctl=lambda *a, **k: None)
    client = TestClient(app)
    assert "Turn on job radar" in client.get("/").text
    assert "Job radar is on" in client.post("/radar/schedule", data={"on": "1"}, follow_redirects=True).text

    client.post("/refresh")                                      # baseline
    tracker.mark_applied(storage.connect(db), "https://example.com/acme/1")
    board["jobs"] = [job("Associate Network Engineer", 2), job("Junior Security Analyst", 3)]
    client.post("/refresh")
    page = client.post("/refresh", follow_redirects=True).text   # second miss: job 1 is closed
    assert "1 closed" in page
    assert "posting closed" in page                              # on your application
    assert 'Junior Security Analyst</a> <span class="tag"' in page and ">new</span>" in page
