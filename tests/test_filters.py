"""Tests for job filters."""
import pytest

from opencareer.filters import classify_category, is_entry_level
from opencareer.models import JobPosting


def make_job(title):
    return JobPosting(source="test", source_id="1", title=title, company="Acme", url="u")


@pytest.mark.parametrize("title", [
    "Junior Software Engineer",
    "Software Engineer, New Grad",
    "Entry Level Network Technician",
    "Associate Security Analyst",
    "IT Support Specialist",          # no seniority word at all -> keep it
])
def test_entry_level_titles_are_kept(title):
    assert is_entry_level(make_job(title)) is True


@pytest.mark.parametrize("title", [
    "Senior Software Engineer",
    "Sr. Network Engineer",
    "Staff Security Engineer",
    "Lead Developer",
    "Principal Engineer",
    "Engineering Manager",
    "Director of IT",
])
def test_senior_titles_are_rejected(title):
    assert is_entry_level(make_job(title)) is False


@pytest.mark.parametrize("title, expected", [
    ("Junior Software Engineer", "software"),
    ("Backend Developer", "software"),
    ("SOC Analyst I", "cybersecurity"),
    ("Associate Security Engineer", "cybersecurity"),
    ("Network Technician", "networking"),
    ("Help Desk Technician", "it"),
    ("IT Support Specialist", "it"),
    ("Marketing Coordinator", None),
])
def test_classify_category(title, expected):
    assert classify_category(make_job(title)) == expected


def test_it_does_not_match_inside_words():
    # "it" appears inside "Security" and "Writer" -- must not count
    assert classify_category(make_job("Technical Writer")) is None


@pytest.mark.parametrize("title", [
    "Head of Startups, Developer GTM",
    "VP of Engineering",
    "Chief Information Security Officer",
])
def test_leadership_titles_are_rejected(title):
    assert is_entry_level(make_job(title)) is False


@pytest.mark.parametrize("title", [
    "Executive Assistant - Developer Platform",
    "Account Executive, Security",
    "Technical Recruiter - Software",
])
def test_non_technical_roles_have_no_category(title):
    assert classify_category(make_job(title)) is None
