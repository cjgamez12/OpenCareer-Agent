"""Tests for target locations."""
from dataclasses import replace
from datetime import date

import pytest

from opencareer import locations, pipeline, storage
from opencareer.models import JobPosting


@pytest.fixture
def conn():
    c = storage.connect(":memory:")
    yield c
    c.close()


def job_at(location, title="Software Engineer", source_id="1"):
    return replace(JobPosting(source="greenhouse", source_id=source_id, title=title,
                              company="Acme", url=f"https://example.com/{source_id}",
                              location=location), tags=("software",))


def test_starts_with_california_and_remote_us(conn):
    names = [t.name for t in locations.list_targets(conn)]
    assert names == ["California", "Remote (US)"]


@pytest.mark.parametrize("place", [
    "Hybrid - San Francisco, CA", "Menlo Park, CA; New York, NY", "Sacramento", "Oakland, California",
])
def test_california_matches(conn, place):
    assert locations.matching_target(job_at(place), locations.list_targets(conn)).name == "California"


@pytest.mark.parametrize("place", ["Toronto, Canada", "Paris, France", "Hybrid", None])
def test_non_matches(conn, place):
    assert locations.matching_target(job_at(place), locations.list_targets(conn)) is None


def test_remote_us_matches(conn):
    target = locations.matching_target(job_at("Remote - USA"), locations.list_targets(conn))
    assert target.name == "Remote (US)"


def test_add_and_remove_a_target(conn):
    locations.add_target(conn, "Seattle", "seattle, bellevue, redmond", 30)
    seattle = [t for t in locations.list_targets(conn) if t.name == "Seattle"][0]
    assert seattle.keywords == ("seattle", "bellevue", "redmond")
    assert locations.matching_target(job_at("Bellevue, WA"), locations.list_targets(conn)).name == "Seattle"
    locations.delete_target(conn, seattle.id)
    assert "Seattle" not in [t.name for t in locations.list_targets(conn)]


def test_name_is_used_when_keywords_are_empty(conn):
    locations.add_target(conn, "London", "", 10)
    assert locations.matching_target(job_at("London, UK"), locations.list_targets(conn)).name == "London"


def test_removed_defaults_stay_removed(conn):
    for t in locations.list_targets(conn):
        locations.delete_target(conn, t.id)
    assert locations.list_targets(conn) == []


def test_bad_points_rejected(conn):
    with pytest.raises(ValueError):
        locations.add_target(conn, "Mars", "", 500)


def test_shortlist_boosts_target_locations(conn):
    storage.save_jobs(conn, [job_at("London, UK", source_id="1"),
                             job_at("San Jose, CA", source_id="2")])
    ranked = pipeline.shortlist(conn, date(2026, 11, 23), targets=locations.list_targets(conn))
    assert [(s, j.location) for s, j in ranked] == [(25, "San Jose, CA"), (0, "London, UK")]
