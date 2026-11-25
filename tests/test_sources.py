"""Tests for the Lever, Ashby, and USAJOBS adapters and the source registry. No network."""
from datetime import date

import pytest

from opencareer.sources import ashby, lever, registry, usajobs

LEVER_DATA = [{
    "id": "abc-123", "text": "Software Engineer, New Grad",
    "categories": {"location": "San Francisco, CA", "commitment": "Full-time", "team": "Platform"},
    "hostedUrl": "https://jobs.lever.co/acme/abc-123", "applyUrl": "https://jobs.lever.co/acme/abc-123/apply",
    "workplaceType": "hybrid", "descriptionPlain": "Build APIs in Python.", "createdAt": 1763683200000,
}, {"id": "no-title"}]

ASHBY_DATA = {"jobs": [{
    "id": "8fb1", "title": "Security Engineer", "location": "San Francisco",
    "secondaryLocations": [{"location": "Remote - US"}], "isListed": True, "isRemote": True,
    "workplaceType": "Hybrid", "publishedAt": "2026-11-05T02:20:02.702+00:00",
    "jobUrl": "https://jobs.ashbyhq.com/openai/8fb1", "descriptionPlain": "Protect systems.",
}, {"id": "hidden", "title": "Unlisted", "isListed": False}]}

USAJOBS_DATA = {"SearchResult": {"SearchResultItems": [{
    "MatchedObjectId": "812345",
    "MatchedObjectDescriptor": {
        "PositionID": "DHS-26-001", "PositionTitle": "IT Specialist (INFOSEC)",
        "OrganizationName": "Cybersecurity and Infrastructure Security Agency",
        "PositionLocationDisplay": "Sacramento, California",
        "PositionURI": "https://www.usajobs.gov/job/812345",
        "PublicationStartDate": "2026-11-20T00:00:00.0000",
        "ApplicationCloseDate": "2026-12-05T23:59:59.9970",
        "UserArea": {"Details": {"JobSummary": "Protect federal networks."}},
    },
}]}}


def test_lever_parses_fields():
    jobs = lever.parse_jobs(LEVER_DATA, "Acme")
    assert len(jobs) == 1                                  # the posting with no title is skipped
    job = jobs[0]
    assert (job.source, job.source_id, job.company) == ("lever", "abc-123", "Acme")
    assert job.location == "San Francisco, CA"
    assert job.description == "Build APIs in Python."
    assert job.posted_at.year == 2025                      # milliseconds converted correctly


def test_ashby_parses_fields_and_skips_unlisted():
    jobs = ashby.parse_jobs(ASHBY_DATA, "OpenAI")
    assert [j.title for j in jobs] == ["Security Engineer"]
    job = jobs[0]
    assert job.location == "San Francisco; Remote - US"
    assert job.remote is True
    assert job.posted_at.date() == date(2026, 11, 5)


def test_usajobs_has_verified_deadline():
    job = usajobs.parse_jobs(USAJOBS_DATA)[0]
    assert job.company == "Cybersecurity and Infrastructure Security Agency"
    assert job.deadline == date(2026, 12, 5)
    assert job.deadline_source == "usajobs.ApplicationCloseDate"
    assert job.description == "Protect federal networks."


def test_usajobs_without_key_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("USAJOBS_API_KEY", raising=False)
    with pytest.raises(usajobs.MissingCredentials):
        usajobs.fetch_jobs("cybersecurity")


@pytest.mark.parametrize("text, expected", [
    ("https://boards.greenhouse.io/cloudflare/jobs/123", ("greenhouse", "cloudflare")),
    ("https://job-boards.greenhouse.io/twilio/jobs/8396809", ("greenhouse", "twilio")),
    ("https://jobs.lever.co/plaid/abc-123/apply", ("lever", "plaid")),
    ("https://jobs.ashbyhq.com/openai/8fb1615c", ("ashby", "openai")),
    ("stripe", ("greenhouse", "stripe")),
])
def test_parse_board_input(text, expected):
    assert registry.parse_board_input(text) == expected


def test_keys_round_trip():
    assert registry.split_key("cloudflare") == ("greenhouse", "cloudflare")
    assert registry.split_key("lever:plaid") == ("lever", "plaid")
    assert registry.make_key("ashby", "openai") == "ashby:openai"
    assert registry.make_key("greenhouse", "okta") == "okta"
