"""Tests for the daily autopilot. Nothing here calls Claude, the network, or launchctl."""
import plistlib
from dataclasses import replace
from datetime import date

import pytest

from opencareer import autopilot, storage, tailor, tracker
from opencareer.ai import AIError
from opencareer.models import JobPosting


def job(title, n, location="San Jose, CA"):
    return replace(JobPosting(source="lever", source_id=n, title=title, company="Acme",
                              url=f"https://jobs.lever.co/acme/{n}", location=location,
                              description="Build things."), tags=("software",))


@pytest.fixture
def setup(tmp_path, monkeypatch):
    conn = storage.connect(":memory:")
    from opencareer.sources import lever
    jobs = [job("Junior Developer", "1"), job("Software Engineer, New Grad", "2"),
            job("Backend Developer", "3"), job("Software Engineer I", "4")]
    monkeypatch.setattr(lever, "fetch_jobs", lambda site, company: jobs)
    notes, tailored = [], []

    def fake_tailor(j, description, *, profile_dir, output_dir):
        tailored.append(j.url)
        tailor.save_draft(tailor.draft_folder(j, output_dir), "# R", "L", "- n")

    run = lambda count=2, tailor_fn=fake_tailor: autopilot.run(
        conn, profile_dir=tmp_path / "p", output_dir=tmp_path / "out", default_boards={"lever:acme": "Acme"},
        count=count, today=date(2026, 11, 24), tailor_fn=tailor_fn,
        notify=lambda title, msg: notes.append(title))
    yield conn, run, notes, tailored
    conn.close()


def test_tailors_the_top_n_and_notifies(setup):
    conn, run, notes, tailored = setup
    summary = run(count=2)
    assert summary["new"] == 4 and len(summary["tailored"]) == 2
    assert notes == ["2 applications ready for review"]
    assert autopilot.ready_for_review(conn) == summary["tailored"]


def test_skips_jobs_that_already_have_drafts(setup):
    conn, run, notes, tailored = setup
    first = run(count=2)["tailored"]
    second = run(count=2)["tailored"]
    assert not set(first) & set(second)
    assert len(tailored) == 4


def test_applied_jobs_leave_the_ready_list(setup):
    conn, run, notes, tailored = setup
    urls = run(count=2)["tailored"]
    tracker.mark_applied(conn, urls[0])
    assert autopilot.ready_for_review(conn) == urls[1:]


def test_stops_early_without_an_api_key(setup):
    conn, run, notes, tailored = setup

    def no_key(j, d, **kwargs):
        raise AIError("No API key: add ANTHROPIC_API_KEY=... to .env")
    summary = run(count=3, tailor_fn=no_key)
    assert summary["tailored"] == [] and len(summary["errors"]) == 1
    assert notes == ["Autopilot needs attention"]
    assert autopilot.last_run(conn)["errors"] == summary["errors"]


def test_schedule_install_and_uninstall(tmp_path):
    calls = []
    runner = lambda cmd, **kw: calls.append(cmd[:2])
    path = autopilot.install_schedule(12, home=tmp_path, runner=runner)
    plist = plistlib.loads(path.read_bytes())
    assert plist["StartCalendarInterval"] == {"Hour": 12, "Minute": 0}
    assert plist["ProgramArguments"][1:] == ["-m", "opencareer.autopilot"]
    assert autopilot.scheduled_time(tmp_path) == "12:00"
    assert ["launchctl", "load"] in calls
    autopilot.uninstall_schedule(home=tmp_path, runner=runner)
    assert autopilot.scheduled_time(tmp_path) is None


def test_bad_times_and_counts_are_rejected(tmp_path):
    with pytest.raises(ValueError):
        autopilot.install_schedule(25, home=tmp_path, runner=lambda *a, **k: None)
    with pytest.raises(ValueError):
        autopilot.set_count(storage.connect(":memory:"), 50)
