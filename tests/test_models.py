"""Tests for the JobPosting model."""
from dataclasses import FrozenInstanceError

import pytest

from opencareer.models import JobPosting


def make_job(**overrides):
    """Build a JobPosting with sensible defaults; override any field you need."""
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


def test_fingerprint_ignores_case_and_outer_spaces():
    a = make_job(title=" Software Engineer I ", company="ACME")
    b = make_job(title="software engineer i", company="acme")
    assert a.fingerprint() == b.fingerprint()


def test_same_job_from_different_sources_matches():
    # Same job posted on two job boards should be treated as a duplicate
    a = make_job(source="greenhouse", source_id="1")
    b = make_job(source="lever", source_id="abc")
    assert a.fingerprint() == b.fingerprint()


def test_different_companies_do_not_match():
    a = make_job(company="Acme")
    b = make_job(company="Globex")
    assert a.fingerprint() != b.fingerprint()


def test_missing_location_does_not_crash():
    job = make_job(location=None)
    assert job.fingerprint() == "acme|software engineer i|"


def test_job_is_frozen():
    job = make_job()
    with pytest.raises(FrozenInstanceError):
        job.title = "Changed"


def test_fingerprint_ignores_extra_inner_spaces():
    # Real API data is messy: "Software  Engineer" (two spaces) is the same job
    a = make_job(title="Software  Engineer   I")
    b = make_job(title="Software Engineer I")
    assert a.fingerprint() == b.fingerprint()
