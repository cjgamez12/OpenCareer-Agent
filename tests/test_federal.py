"""Tests for federal (USAJOBS) grade handling and the cybersecurity / supervisor fixes."""
from datetime import date

import pytest

from opencareer import pipeline
from opencareer.models import JobPosting
from opencareer.sources import usajobs


def usajobs_item(title, low, high, plan="GS", n="1"):
    return {"MatchedObjectId": n, "MatchedObjectDescriptor": {
        "PositionID": f"P{n}", "PositionTitle": title, "OrganizationName": "Navy",
        "PositionLocationDisplay": "Point Mugu, California", "PositionURI": f"https://www.usajobs.gov/job/{n}",
        "JobGrade": [{"Code": plan}],
        "UserArea": {"Details": {"LowGrade": low, "HighGrade": high, "JobSummary": "Run networks."}},
    }}


def test_grade_is_saved_in_the_description():
    job = usajobs.parse_job(usajobs_item("IT Specialist", "07", "09"))
    assert job.description.startswith("Grade: GS-07 to GS-09")
    assert job.description.endswith("Run networks.")
    assert pipeline.grade_label(job) == "GS-7 to 9"
    assert pipeline.start_grade(job) == 7


def test_recent_graduates_jobs_are_marked():
    job = usajobs.parse_job(usajobs_item("IT Specialist", "05", "07"), recent_graduates=True)
    assert pipeline.RECENT_GRADS_LINE in job.description


def test_jobs_starting_above_gs9_are_filtered_with_a_reason():
    high = usajobs.parse_job(usajobs_item("IT Specialist (INFOSEC)", "12", "12"))
    assert pipeline.select_jobs([high]) == []
    assert "starts at GS-12" in pipeline.why_not_suggested(high)


def test_gs7_ladder_is_kept():
    ladder = usajobs.parse_job(usajobs_item("IT Specialist", "07", "11"))  # starts at 7: kept
    assert len(pipeline.select_jobs([ladder])) == 1


def test_new_grad_mode_prefers_gs5_7_and_recent_graduates():
    gs7 = usajobs.parse_job(usajobs_item("IT Specialist", "07", "09", n="1"))
    gs9 = usajobs.parse_job(usajobs_item("IT Specialist", "09", "09", n="2"))
    grads = usajobs.parse_job(usajobs_item("IT Specialist", "09", "09", n="3"), recent_graduates=True)
    assert pipeline.new_grad_adjustment(gs7) == 20
    assert pipeline.new_grad_adjustment(gs9) == 0
    assert pipeline.new_grad_adjustment(grads) == 25


def job(title):
    return JobPosting(source="greenhouse", source_id="1", title=title, company="Acme", url="u")


@pytest.mark.parametrize("title", [
    "IT CYBERSECURITY SPECIALIST", "IT Specialist (INFOSEC)", "Cybersecurity Analyst",
])
def test_cyber_titles_are_cybersecurity(title):
    assert pipeline.select_jobs([job(title)])[0].tags == ("cybersecurity",)


def test_excluded_roles_stay_excluded():
    assert pipeline.select_jobs([job("Cybersecurity Account Executive")]) == []


@pytest.mark.parametrize("title", ["SUPV IT SPECIALIST (CUSTSPT/SYSADMIN)", "IT Supervisor"])
def test_supervisor_titles_are_filtered(title):
    assert pipeline.select_jobs([job(title)]) == []


def test_gs9_only_jobs_are_filtered():
    gs9 = usajobs.parse_job(usajobs_item("IT SPECIALIST (NETWORK/CUSTSPT)", "09", "09"))
    assert pipeline.select_jobs([gs9]) == []
    assert "master's" in pipeline.why_not_suggested(gs9)


@pytest.mark.parametrize("plan, low", [("NT", "05"), ("DS", "04"), ("NH", "03"), ("AD", "00")])
def test_pay_bands_are_not_treated_as_gs(plan, low):
    band = usajobs.parse_job(usajobs_item("IT SPECIALIST (INFOSEC)", low, low, plan=plan))
    assert pipeline.start_grade(band) is None
    assert pipeline.new_grad_adjustment(band) == 0              # no GS-5/7 bonus
    assert len(pipeline.select_jobs([band])) == 1                # kept, but not boosted
    assert "pay band" in pipeline.grade_label(band)


def test_gg_counts_like_gs():
    gg = usajobs.parse_job(usajobs_item("IT Specialist (APPSW)", "07", "11", plan="GG"))
    assert pipeline.new_grad_adjustment(gg) == 20
