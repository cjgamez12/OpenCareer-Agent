"""Tests for new-grad mode and the extra seniority rules."""
from datetime import date

import pytest

from opencareer import pipeline
from opencareer.models import JobPosting

TODAY = date(2026, 11, 24)


def job(title, n="1"):
    return JobPosting(source="greenhouse", source_id=n, title=title, company="Acme", url=f"u{n}")


@pytest.mark.parametrize("title", [
    "Supervisory IT Specialist", "Software Engineer (L4)", "Software Engineer P3",
])
def test_extra_senior_titles_are_filtered(title):
    assert pipeline.select_jobs([job(title)]) == []


@pytest.mark.parametrize("title", ["Software Engineer (L2)", "IT Specialist", "Software Engineer, New Grad"])
def test_early_titles_are_kept(title):
    assert len(pipeline.select_jobs([job(title)])) == 1


def test_new_grad_mode_puts_new_grad_roles_above_internships():
    jobs = pipeline.select_jobs([job("Software Engineer Intern", "1"),
                                 job("Software Engineer, New Grad", "2")])
    off = [j.title for _, j in pipeline.rank_with_targets(jobs, TODAY)]
    on = [j.title for _, j in pipeline.rank_with_targets(jobs, TODAY, new_grad=True)]
    assert off[0] == "Software Engineer Intern"            # normal ranking: tie, intern listed first
    assert on[0] == "Software Engineer, New Grad"


def test_new_grad_adjustment_values():
    assert pipeline.new_grad_adjustment(job("Early Career Security Analyst")) == 25
    assert pipeline.new_grad_adjustment(job("Network Engineering Co-op")) == -40
    assert pipeline.new_grad_adjustment(job("Backend Developer")) == 0
