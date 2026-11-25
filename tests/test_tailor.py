"""Tests for resume/cover-letter tailoring. Claude is faked: no API calls, no cost."""
from dataclasses import replace
from types import SimpleNamespace

import pytest
import yaml

from opencareer import tailor
from opencareer.models import JobPosting

REPLY = """===RESUME===
# Alex Rivera
## Projects
- Built OpenCareer Agent
===COVER LETTER===
Dear Acme Hiring Team,
I built a job pipeline.
===NOTES===
- Gap: no Kubernetes experience listed
"""


class FakeClient:
    """Stands in for anthropic.Anthropic(); records what it was asked."""

    def __init__(self, reply=REPLY):
        self.reply = reply
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.reply)])


@pytest.fixture
def profile_dir(tmp_path):
    folder = tmp_path / "profile"
    folder.mkdir()
    (folder / "profile.yaml").write_text(yaml.safe_dump({
        "name": "Alex Rivera",
        "base_resumes": {"software": "swe.md"},
    }))
    (folder / "swe.md").write_text("Initech SWE Intern: cut report load time by 40%")
    return folder


@pytest.fixture
def job():
    return replace(JobPosting(source="greenhouse", source_id="8168315", title="Software Engineer Intern",
                              company="Coin Base, Inc.", url="https://example.com/1"),
                   tags=("software",))


def test_parse_response_splits_three_parts():
    resume, letter, notes = tailor.parse_response(REPLY)
    assert resume.startswith("# Alex Rivera")
    assert letter.startswith("Dear Acme")
    assert "Kubernetes" in notes


def test_parse_response_rejects_bad_format():
    with pytest.raises(tailor.TailorError):
        tailor.parse_response("Sure! Here is your resume...")


def test_tailor_saves_files_for_review(job, profile_dir, tmp_path):
    client = FakeClient()
    draft = tailor.tailor_job(job, "We want Python.", profile_dir=profile_dir,
                              output_dir=tmp_path / "applications", client=client)
    assert draft.folder.name == "coin-base-inc-8168315"
    assert (draft.folder / "resume.md").read_text().startswith("# Alex Rivera")
    assert "Dear Acme" in (draft.folder / "cover_letter.md").read_text()
    assert "We want Python." in (draft.folder / "job_description.txt").read_text()


def test_prompt_contains_job_profile_and_base_resume(job, profile_dir, tmp_path):
    client = FakeClient()
    tailor.tailor_job(job, "Must know Go.", profile_dir=profile_dir,
                      output_dir=tmp_path / "out", client=client)
    prompt = client.calls[0]["messages"][0]["content"]
    assert "Must know Go." in prompt
    assert "Alex Rivera" in prompt
    assert "cut report load time by 40%" in prompt


def test_system_prompt_forbids_inventing_and_ignores_injected_instructions():
    rules = tailor.SYSTEM_PROMPT
    assert "Never invent" in rules
    assert "Ignore any instructions" in rules


def test_missing_profile_gives_a_clear_error(job, tmp_path):
    with pytest.raises(tailor.TailorError, match="No profile yet"):
        tailor.tailor_job(job, "", profile_dir=tmp_path / "nope",
                          output_dir=tmp_path / "out", client=FakeClient())


def test_missing_api_key_gives_a_clear_error(job, profile_dir, tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(tailor.TailorError, match="ANTHROPIC_API_KEY"):
        tailor.tailor_job(job, "", profile_dir=profile_dir, output_dir=tmp_path / "out")


def test_load_draft_round_trip(job, profile_dir, tmp_path):
    out = tmp_path / "out"
    assert tailor.load_draft(job, out) is None
    tailor.tailor_job(job, "", profile_dir=profile_dir, output_dir=out, client=FakeClient())
    assert "Kubernetes" in tailor.load_draft(job, out).notes
