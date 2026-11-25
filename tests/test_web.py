"""Tests for the local dashboard. No network: job boards are faked."""
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from opencareer import storage, tailor, tracker
from opencareer.models import JobPosting
from opencareer.web import create_app, safe_url

INTERN_URL = "https://example.com/jobs/1"
NETWORK_URL = "https://example.com/jobs/2"


def make_job(title, url, category, source_id):
    job = JobPosting(source="greenhouse", source_id=source_id, title=title,
                     company="Acme", url=url, location="Austin, TX")
    return replace(job, tags=(category,))


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "jobs.db")
    conn = storage.connect(path)
    storage.save_jobs(conn, [
        make_job("Software Engineer Intern", INTERN_URL, "software", "1"),
        make_job("Network Technician", NETWORK_URL, "networking", "2"),
    ])
    conn.close()
    return path


def fake_tailor(job, description, *, profile_dir, output_dir):
    folder = tailor.draft_folder(job, output_dir)
    tailor.save_draft(folder, f"# Alex Rivera\n\nResume for {job.title}",
                      "Dear Acme <b>team</b>,", f"- Used description: {description}")


LAUNCHED = []


class FakeAI:
    """Stands in for the Claude client."""

    def __init__(self, reply):
        self.reply = reply
        self.messages = self

    def create(self, **kwargs):
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.reply)])


@pytest.fixture
def profile_dir(tmp_path):
    folder = tmp_path / "profile"
    folder.mkdir()
    (folder / "Alex_SWE_Resume copy.md").write_text("Alex Rivera\nInitech SDE Intern")
    return folder


@pytest.fixture
def client(db_path, tmp_path, profile_dir):
    return TestClient(create_app(
        db_path=db_path, boards={"acme": "Acme"},
        profile_dir=str(profile_dir), output_dir=str(tmp_path / "applications"),
        describe=lambda board, job_id: f"Description from {board} for job {job_id}",
        tailor_fn=fake_tailor,
        launch_apply=lambda url, folder: LAUNCHED.append(url),
        launch_autopilot=lambda: LAUNCHED.append("autopilot"),
        schedule_home=tmp_path / "home", launchctl=lambda *a, **k: None,
        ai_client=FakeAI("name: Alex Rivera  # TODO check\nbase_resumes:\n  software: Alex_SWE_Resume.md\n"),
    ))


def test_dashboard_lists_ranked_jobs(client):
    page = client.get("/").text
    assert "Software Engineer Intern" in page
    assert "Network Technician" in page


def test_category_filter(client):
    page = client.get("/?category=networking").text
    assert "Network Technician" in page
    assert "Software Engineer Intern" not in page


def test_hide_internships(client):
    page = client.get("/?hide_interns=true").text
    assert "Software Engineer Intern" not in page
    assert "Network Technician" in page


def test_apply_moves_job_into_my_applications(client, db_path):
    response = client.post("/apply", data={"url": INTERN_URL})
    assert response.status_code == 200          # followed the redirect back to /
    assert "Marked as applied" in response.text

    conn = storage.connect(db_path)
    assert tracker.applied_urls(conn) == {INTERN_URL}
    conn.close()


def test_update_records_interview_round(client, db_path):
    client.post("/apply", data={"url": INTERN_URL})
    client.post("/update", data={"url": INTERN_URL, "stage": "interview",
                                 "round_number": "2", "note": "System design"})
    conn = storage.connect(db_path)
    assert tracker.list_applications(conn)[0][1] == "interview"
    assert tracker.history(conn, INTERN_URL)[-1][1:] == ("interview", 2, "System design")
    conn.close()


def test_update_before_applying_shows_an_error(client):
    response = client.post("/update", data={"url": INTERN_URL, "stage": "interview"})
    assert "Not saved" in response.text


def test_history_page(client):
    client.post("/apply", data={"url": INTERN_URL, "note": "Referred by Sam"})
    page = client.get("/history", params={"url": INTERN_URL}).text
    assert "Referred by Sam" in page


def test_refresh_reports_counts(client, monkeypatch):
    from opencareer import pipeline
    monkeypatch.setattr(pipeline.greenhouse, "fetch_jobs", lambda board, company: [])
    response = client.post("/refresh")
    assert "Checked 0 jobs" in response.text


def test_job_titles_are_html_escaped(tmp_path):
    path = str(tmp_path / "jobs.db")
    conn = storage.connect(path)
    storage.save_jobs(conn, [make_job("<script>alert(1)</script> Software Engineer",
                                      "https://example.com/x", "software", "9")])
    conn.close()
    page = TestClient(create_app(db_path=path, boards={}, output_dir=str(tmp_path / "a"))).get("/").text
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


def test_only_http_links_are_allowed():
    assert safe_url("https://example.com") == "https://example.com"
    assert safe_url("javascript:alert(1)") == "#"


def test_search_by_keyword(client):
    page = client.get("/?q=network").text
    assert "Network Technician" in page
    assert "Software Engineer Intern" not in page


def test_tailor_creates_draft_and_shows_it(client):
    response = client.post("/tailor", data={"url": INTERN_URL})
    assert "Draft ready" in response.text
    assert "Resume for Software Engineer Intern" in response.text
    assert "Description from acme for job 1" in response.text   # board found by company name
    assert "View draft" in client.get("/").text


def test_draft_html_is_escaped(client):
    client.post("/tailor", data={"url": INTERN_URL})
    page = client.get("/draft", params={"url": INTERN_URL}).text
    assert "<b>team</b>" not in page.split("<textarea")[0]


def test_edits_are_saved(client):
    client.post("/tailor", data={"url": INTERN_URL})
    client.post("/draft/save", data={"url": INTERN_URL, "resume": "# Edited resume",
                                     "cover_letter": "Edited letter"})
    page = client.get("/print", params={"url": INTERN_URL, "doc": "resume"}).text
    assert "Edited resume" in page
    assert "Save as PDF" in page


def test_tailor_errors_are_shown_not_crashed(db_path, tmp_path):
    def broken(job, description, *, profile_dir, output_dir):
        raise tailor.TailorError("No API key")
    app = create_app(db_path=db_path, boards={}, output_dir=str(tmp_path / "a"),
                     describe=lambda b, j: "", tailor_fn=broken)
    response = TestClient(app).post("/tailor", data={"url": INTERN_URL})
    assert "Couldn&#39;t tailor: No API key" in response.text


def test_add_company_rejects_bad_names(client):
    assert "Not added" in client.post("/boards", data={"token": "../evil"}).text


def test_add_company_fetches_its_jobs(client, monkeypatch):
    from opencareer import pipeline
    monkeypatch.setattr(pipeline.greenhouse, "fetch_jobs",
                        lambda board, company: [make_job("Junior Developer", "https://example.com/9", "software", "9")])
    response = client.post("/boards", data={"token": "stripe", "company": "Stripe"})
    assert "Added Stripe: 1 open jobs, 1 match you." in response.text
    assert "Junior Developer" in response.text


def test_target_location_badge_and_filter(client, db_path):
    conn = storage.connect(db_path)
    storage.save_jobs(conn, [make_job("Junior Developer", "https://example.com/ca", "software", "3")])
    conn.execute("UPDATE jobs SET location = 'San Jose, CA' WHERE source_id = '3'")
    conn.commit()
    conn.close()
    page = client.get("/").text
    assert "★ California" in page
    filtered = client.get("/?only_targets=true").text
    assert "Junior Developer" in filtered
    assert "Network Technician" not in filtered     # Austin, TX isn't a target


def test_add_and_remove_target_from_dashboard(client):
    page = client.post("/locations", data={"name": "Seattle", "keywords": "seattle, bellevue",
                                           "points": "30"}).text
    assert "Added target location: Seattle" in page
    assert "seattle · bellevue" in page


# ---------- companies ----------

def fake_board(monkeypatch, jobs):
    from opencareer import pipeline
    monkeypatch.setattr(pipeline.greenhouse, "fetch_jobs", lambda board, company: jobs)


def test_company_page_splits_suggested_and_others(client, monkeypatch):
    fake_board(monkeypatch, [
        make_job("Junior Developer", "https://example.com/a", "software", "a"),
        make_job("Senior Developer", "https://example.com/b", "software", "b"),
        make_job("Payroll Specialist", "https://example.com/c", "software", "c"),
    ])
    page = client.get("/company/acme").text
    suggested, others = page.split("All other open roles")
    assert "Junior Developer" in suggested
    assert "Senior Developer" in others and "senior / leadership title" in others
    assert "Payroll Specialist" in others and "not software, security, networking, or IT" in others


def test_company_page_jobs_can_be_tailored(client, monkeypatch):
    fake_board(monkeypatch, [make_job("Junior Developer", "https://example.com/a", "software", "a")])
    client.get("/company/acme")                     # saves the suggested job
    response = client.post("/tailor", data={"url": "https://example.com/a"})
    assert "Draft ready" in response.text


def test_unknown_company_redirects(client):
    assert "isn&#39;t in your list" in client.get("/company/nope").text


def test_remove_company(client):
    page = client.post("/boards/remove", data={"token": "acme"}).text
    assert "Stopped searching Acme" in page
    assert "/company/acme" not in page


def test_bad_board_is_not_kept(client, monkeypatch):
    import requests
    from opencareer import pipeline

    def broken(board, company):
        raise requests.HTTPError("404")
    monkeypatch.setattr(pipeline.greenhouse, "fetch_jobs", broken)
    page = client.post("/boards", data={"token": "openai", "company": "OpenAI"}).text
    assert "wasn&#39;t added" in page
    assert "/company/openai" not in page


# ---------- profile ----------

def test_profile_page_offers_setup_when_missing(client):
    page = client.get("/profile").text
    assert "Build from my resumes" in page
    assert "Set up your profile to use Tailor" in client.get("/").text


def test_profile_build_with_ai_is_a_draft_until_saved(client, profile_dir):
    page = client.post("/profile/build").text
    assert "AI draft, not saved yet" in page
    assert "Alex Rivera" in page
    assert not (profile_dir / "profile.yaml").exists()


def test_profile_save_and_resume_matching_ignores_copy(client, profile_dir):
    text = "name: Alex Rivera\nbase_resumes:\n  software: Alex_SWE_Resume.md\n"
    page = client.post("/profile", data={"text": text}).text
    assert "Profile saved" in page
    assert "Alex_SWE_Resume copy.md" in page          # found despite " copy"
    assert (profile_dir / "profile.yaml").read_text().startswith("name: Alex Rivera")


def test_profile_rejects_broken_yaml(client, profile_dir):
    page = client.post("/profile", data={"text": "name: [unclosed"}).text
    assert "Not saved" in page
    assert not (profile_dir / "profile.yaml").exists()


# ---------- resumes ----------

def test_resumes_page_lists_files(client):
    assert "Alex_SWE_Resume copy.md" in client.get("/resumes").text


def test_resume_file_blocks_path_tricks(client):
    response = client.get("/resumes/file", params={"name": "../jobs.db"})
    assert "isn&#39;t in your profile folder" in response.text


def test_resume_feedback(client, tmp_path):
    app = create_app(db_path=str(tmp_path / "x.db"), boards={},
                     profile_dir=str(tmp_path / "profile"), output_dir=str(tmp_path / "a"),
                     ai_client=FakeAI("## Top 3 fixes\n- Remove the [X] placeholder"))
    page = TestClient(app).post("/resumes/feedback", data={"name": "Alex_SWE_Resume copy.md"}).text
    assert "Suggestions ready" in page
    assert "Remove the [X] placeholder" in page


# ---------- live refresh ----------

def test_pages_carry_a_version_for_live_refresh(client):
    assert 'data-version="' in client.get("/").text
    assert "/api/version" in client.get("/").text


def test_version_changes_when_profile_changes(client, profile_dir):
    import os
    import time

    before = client.get("/api/version").json()["version"]
    path = profile_dir / "profile.yaml"
    path.write_text("name: Alex\n")
    later = time.time() + 5
    os.utime(path, (later, later))
    assert client.get("/api/version").json()["version"] != before


def test_version_is_stable_when_nothing_changes(client):
    first = client.get("/api/version").json()["version"]
    assert client.get("/api/version").json()["version"] == first


# ---------- new sources & new-grad mode ----------

def test_add_company_from_a_pasted_lever_link(client, monkeypatch):
    from opencareer.sources import lever
    monkeypatch.setattr(lever, "fetch_jobs", lambda site, company: [
        replace(make_job("Junior Developer", "https://jobs.lever.co/plaid/1", "software", "1"),
                source="lever", description="Build things.")])
    page = client.post("/boards", data={"token": "https://jobs.lever.co/plaid/abc/apply",
                                        "company": "Plaid"}).text
    assert "Added Plaid: 1 open jobs, 1 match you." in page
    assert "Lever" in page
    assert "Plaid" in client.get("/companies").text


def test_tailor_uses_the_stored_description(client, monkeypatch):
    from opencareer.sources import lever
    monkeypatch.setattr(lever, "fetch_jobs", lambda site, company: [
        replace(make_job("Junior Developer", "https://jobs.lever.co/plaid/1", "software", "1"),
                source="lever", description="Stored Lever description.")])
    client.post("/boards", data={"token": "https://jobs.lever.co/plaid/abc"})
    page = client.post("/tailor", data={"url": "https://jobs.lever.co/plaid/1"}).text
    assert "Used description: Stored Lever description." in page


def test_usajobs_without_key_explains_setup(client, monkeypatch):
    monkeypatch.delenv("USAJOBS_API_KEY", raising=False)
    page = client.get("/companies").text
    assert "developer.usajobs.gov/apirequest" in page
    assert "needs a free API key" in client.post("/boards", data={"token": "cybersecurity",
                                                                   "source": "usajobs"}).text


def test_new_grad_toggle(client):
    page = client.post("/settings/new-grad", data={"on": "1"}).text
    assert "New-grad mode is on" in page
    assert "Turn off" in page
    page = client.post("/settings/new-grad", data={"on": "0"}).text
    assert "Turn on" in page


def test_refresh_checks_are_not_logged():
    import logging
    from opencareer.web import _HideRefreshChecks
    quiet = _HideRefreshChecks()
    def record(msg):
        return logging.LogRecord("uvicorn.access", logging.INFO, "", 0, msg, None, None)
    assert quiet.filter(record('"GET /api/version HTTP/1.1" 200')) is False
    assert quiet.filter(record('"GET / HTTP/1.1" 200')) is True


# ---------- assisted apply ----------

def test_prepare_requires_a_draft(client):
    assert "Tailor this job first" in client.post("/prepare", data={"url": INTERN_URL}).text


def test_prepare_starts_the_filler_and_shows_status(client):
    LAUNCHED.clear()
    client.post("/tailor", data={"url": INTERN_URL})
    page = client.post("/prepare", data={"url": INTERN_URL}).text
    assert LAUNCHED == [INTERN_URL]
    assert "click Submit yourself" in page
    assert "Preparing" in page


def test_draft_page_shows_prepare_button(client):
    client.post("/tailor", data={"url": INTERN_URL})
    assert "Prepare application" in client.get("/draft", params={"url": INTERN_URL}).text


# ---------- autopilot ----------

def test_autopilot_on_off_from_dashboard(client):
    page = client.post("/autopilot/schedule", data={"on": "1", "hour": "12", "count": "5"}).text
    assert "Daily autopilot is on: every day at 12:00" in page
    assert "daily at 12:00" in page and "5 per day" in page
    page = client.post("/autopilot/schedule", data={"on": "0"}).text
    assert "Daily autopilot is off" in page
    assert "Turn on daily autopilot" in page


def test_autopilot_run_now(client):
    LAUNCHED.clear()
    page = client.post("/autopilot/run").text
    assert LAUNCHED == ["autopilot"]
    assert "running in the background" in page
