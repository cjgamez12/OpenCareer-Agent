"""Tests for LinkedIn alerts -> company job boards. No network: job boards and Claude are faked."""
import json
from types import SimpleNamespace

import pytest
import requests
from fastapi.testclient import TestClient

from opencareer import finder
from opencareer.models import JobPosting
from opencareer.sources import registry
from opencareer.web import create_app

# Which (source, board) exist in our fake world, and their open jobs
BOARDS = {
    ("greenhouse", "datadog"): ["Software Engineer I"],
    ("lever", "plaid"): ["Associate Software Engineer"],
    ("ashby", "palo-alto-networks"): ["Security Engineer, New Grad"],
    ("greenhouse", "emptyco"): [],                       # exists but no open jobs: must not count
}

ALERT = """Hi Alex, here are new jobs for "software engineer" in San Jose
Software Engineer I
Datadog · San Jose, CA (Hybrid)
Actively recruiting
Associate Software Engineer
Plaid · Remote
Security Engineer, New Grad
Palo Alto Networks · Santa Clara, CA
This email was intended for Alex Rivera (alex@example.com)
"""


def fake_module(source):
    def fetch_jobs(name, company):
        if (source, name) not in BOARDS:
            raise requests.HTTPError("404")
        return [JobPosting(source=source, source_id=f"{name}-{i}", title=title, company=company,
                           url=f"https://example.com/{source}/{name}/{i}", location="San Jose, CA")
                for i, title in enumerate(BOARDS[(source, name)])]
    return SimpleNamespace(fetch_jobs=fetch_jobs)


@pytest.fixture(autouse=True)
def fake_boards(monkeypatch):
    for source in ("greenhouse", "lever", "ashby"):
        monkeypatch.setitem(registry.MODULES, source, fake_module(source))


class FakeClaude:
    def __init__(self, companies):
        self.reply, self.sent = json.dumps({"companies": companies}), []
        self.messages = self

    def create(self, **kwargs):
        self.sent.append(kwargs["messages"][0]["content"])
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=self.reply)])


def test_slug_guesses():
    assert finder.slug_candidates("Palo Alto Networks, Inc.") == ["paloaltonetworks", "palo-alto-networks",
                                                                  "paloaltonetworksinc"]
    assert finder.slug_candidates("Datadog") == ["datadog"]
    assert finder.slug_candidates("AT&T") == ["att"]


def test_find_board_tries_each_source_and_needs_open_jobs():
    assert finder.find_board("Datadog") == ("greenhouse", "datadog", 1)
    assert finder.find_board("Plaid") == ("lever", "plaid", 1)
    assert finder.find_board("Palo Alto Networks") == ("ashby", "palo-alto-networks", 1)
    assert finder.find_board("EmptyCo") is None                  # board exists, but nothing open
    assert finder.find_board("Workday-Only Corp") is None


def test_a_plain_list_is_used_without_claude():
    assert finder.companies_from_text("Datadog\nPlaid\n\nplaid\nLinkedIn") == ["Datadog", "Plaid"]


def test_an_alert_email_goes_to_claude_without_email_addresses():
    client = FakeClaude(["Datadog", "Plaid", "Palo Alto Networks", "LinkedIn"])
    names = finder.companies_from_text(ALERT, client=client)
    assert names == ["Datadog", "Plaid", "Palo Alto Networks"]
    assert "alex@example.com" not in client.sent[0] and "[email]" in client.sent[0]


def test_pasted_board_links_are_recognized():
    text = "look: https://jobs.lever.co/plaid/abc-123 and https://www.linkedin.com/jobs/view/999"
    assert finder.board_links(text) == [("lever", "plaid")]


def test_from_linkedin_box_tracks_found_companies(tmp_path):
    client = TestClient(create_app(db_path=str(tmp_path / "jobs.db"), boards={"coinbase": "Coinbase"},
                                   profile_dir=str(tmp_path / "p"), output_dir=str(tmp_path / "a"),
                                   ai_client=FakeClaude(["Datadog", "Plaid", "Palo Alto Networks", "Coinbase",
                                                         "Workday Only Corp"])))
    page = client.post("/boards/find", data={"text": ALERT}, follow_redirects=True).text
    assert "Now tracking Datadog (Greenhouse), Plaid (Lever), Palo Alto Networks (Ashby)" in page
    assert "Already tracking: Coinbase" in page
    assert "No public job board found for Workday Only Corp" in page
    companies = client.get("/companies").text
    assert "Datadog" in companies and "Plaid" in companies and "Palo Alto Networks" in companies


def test_a_bad_pasted_link_is_not_kept(tmp_path):
    client = TestClient(create_app(db_path=str(tmp_path / "jobs.db"), boards={},
                                   profile_dir=str(tmp_path / "p"), output_dir=str(tmp_path / "a")))
    page = client.post("/boards/find", data={"text": "https://jobs.lever.co/nosuchco/1"}, follow_redirects=True).text
    assert "No public job board found for" in page and "Now tracking" not in page
