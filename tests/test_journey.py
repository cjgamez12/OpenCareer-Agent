"""Tests for the job-search flow chart."""
import sqlite3
from datetime import date

from opencareer import journey, tracker


def ev(*stages):
    """Events as tracker.history returns them: (happened_on, stage, round, note)."""
    out = []
    for s in stages:
        stage, _, rnd = s.partition(":")
        out.append(("2026-11-01", stage, int(rnd) if rnd else None, ""))
    return out


def test_paths_follow_the_steps_each_application_reached():
    assert journey.path_for(ev("applied")) == ["Applications", "No reply yet"]
    assert journey.path_for(ev("applied", "ghosted")) == ["Applications", "Ghosted"]
    assert journey.path_for(ev("applied", "recruiter_screen", "interview:1", "interview:2", "rejected")) == \
        ["Applications", "Recruiter screen", "Round 1", "Round 2", "Rejected"]
    assert journey.path_for(ev("applied", "interview", "interview", "final_round", "offer", "accepted")) == \
        ["Applications", "Round 1", "Round 2", "Final round", "Offer", "Accepted"]
    assert journey.path_for(ev("applied", "interview:1", "offer", "withdrawn"))[-1] == "Declined"
    assert journey.path_for(ev("applied", "withdrawn"))[-1] == "Withdrew"
    assert journey.path_for(ev("applied", "assessment"))[-1] == "In progress"


def test_steps_never_go_backwards():
    assert journey.path_for(ev("applied", "interview:2", "recruiter_screen", "interview:1", "rejected")) == \
        ["Applications", "Round 2", "Rejected"]


def make_db():
    conn = sqlite3.connect(":memory:")
    day = date(2026, 11, 1)
    plans = [["ghosted"]] * 5 + [["rejected"]] * 2 + [[]] * 2 + \
            [["recruiter_screen", ("interview", 1), "rejected"],
             ["recruiter_screen", ("interview", 1), ("interview", 2), "offer", "accepted"]]
    for i, plan in enumerate(plans):
        url = f"https://example.com/{i}"
        tracker.mark_applied(conn, url, on=day)
        for step in plan:
            stage, rnd = step if isinstance(step, tuple) else (step, None)
            tracker.update(conn, url, stage, on=day, round_number=rnd)
    return conn


def test_flows_count_every_application():
    links = journey.flows(make_db())
    assert links[("Applications", "Ghosted")] == 5
    assert links[("Applications", "Recruiter screen")] == 2
    assert links[("Recruiter screen", "Round 1")] == 2
    assert links[("Round 1", "Rejected")] == 1
    assert links[("Offer", "Accepted")] == 1
    assert sum(v for (a, _), v in links.items() if a == "Applications") == 11
    assert journey.headline(links) == "11 applications · 2 reached an interview (18%) · 1 offer"


def test_chart_draws_every_step_and_handles_empty():
    svg = journey.sankey_svg(journey.flows(make_db()))
    for label in ("Applications", "Ghosted", "Rejected", "Recruiter screen", "Round 1", "Round 2",
                  "Offer", "Accepted", "No reply yet"):
        assert f">{label}<" in svg
    assert ">11<" in svg and 'role="img"' in svg
    assert journey.sankey_svg({}) is None
    assert journey.headline({}) == ""
