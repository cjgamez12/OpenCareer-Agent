"""Tests for GitHub projects -> profile. GitHub and Claude are faked: no network."""
import json
from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient

from opencareer import applicant, github
from opencareer.github import Repo
from opencareer.web import create_app

REPOS = [
    {"name": "OpenCareer_AGENT", "html_url": "https://github.com/alex/OpenCareer_AGENT", "fork": False,
     "description": "Personal AI job-application agent", "language": "Python", "topics": ["fastapi"],
     "pushed_at": "2026-11-24T23:00:00Z", "stargazers_count": 2},
    {"name": "socket-chat", "html_url": "https://github.com/alex/socket-chat", "fork": False,
     "description": "Multithreaded chat server in C", "language": "C", "topics": [],
     "pushed_at": "2025-06-01T00:00:00Z", "stargazers_count": 0},
    {"name": "someone-elses-lib", "html_url": "https://github.com/alex/someone-elses-lib", "fork": True,
     "description": "", "language": "Go", "topics": [], "pushed_at": "2024-01-01T00:00:00Z"},
]

PROFILE_TEXT = """# My profile
name: "Alex Rivera"
email: "alex@example.com"
links:
  github: "https://github.com/alex"

application_answers:
  address:
    street: "123 Secret St"

projects:
  - name: "Socket Chat"   # from my resume
    status: done
    stack: [C]

# Which resume file to start from
base_resumes:
  software: "swe.pdf"
"""


class FakeGitHub:
    """Answers like api.github.com for user 'alex'."""

    def __init__(self, status=200):
        self.status, self.urls = status, []

    def get(self, url, headers=None, timeout=None):
        self.urls.append(url)
        if url.endswith("/users/alex/repos?per_page=100&sort=pushed"):
            return SimpleNamespace(status_code=self.status, text="rate limit exceeded" if self.status == 403 else "",
                                   json=lambda: REPOS)
        if url.endswith("/readme"):
            return SimpleNamespace(status_code=200, text="# OpenCareer\nFinds jobs and pre-fills applications.",
                                   json=lambda: {})
        if url.endswith("/languages"):
            return SimpleNamespace(status_code=200, text="", json=lambda: {"Python": 9000, "HTML": 300})
        return SimpleNamespace(status_code=404, text="", json=lambda: {})


DRAFT_REPLY = """```yaml
new_projects:
  - name: OpenCareer Agent
    status: done
    stack: [Python, FastAPI, SQLite, Playwright]
    link: https://github.com/alex/OpenCareer_AGENT
    bullets:
      - Built a job-search agent that pulls postings from public job-board APIs and ranks them.
      - Automated application form filling with Playwright while leaving submission to the user.
updates:
  - project: Socket Chat
    suggestion: Mention the multithreaded server design from the README.
skipped: []
```"""


class FakeClaude:
    def __init__(self, reply=DRAFT_REPLY):
        self.reply, self.sent = reply, []
        self.messages = self

    def create(self, **kwargs):
        self.sent.append(kwargs["messages"][0]["content"])
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=self.reply)])


def test_username_from_profile_link():
    assert github.username_from({"links": {"github": "https://github.com/alexrivera"}}) == "alexrivera"
    assert github.username_from({"links": {"github": "github.com/alexrivera/"}}) == "alexrivera"
    assert github.username_from({"links": {"github": ""}}) is None
    assert github.username_from({}) is None


def test_fetch_repos_skips_forks_and_marks_what_is_in_your_profile():
    profile = applicant.parse_profile(PROFILE_TEXT)
    repos = github.fetch_repos("alex", profile, session=FakeGitHub())
    assert [r.name for r in repos] == ["OpenCareer_AGENT", "socket-chat"]
    assert [r.in_profile for r in repos] == [False, True]          # "Socket Chat" == "socket-chat"
    assert repos[0].pushed_at == "2026-11-24"


def test_rate_limit_gives_a_friendly_message():
    with pytest.raises(github.GitHubError, match="hourly limit"):
        github.fetch_repos("alex", {}, session=FakeGitHub(status=403))


def test_draft_uses_only_repo_facts_and_your_projects():
    profile = applicant.parse_profile(PROFILE_TEXT)
    client = FakeClaude()
    repo = Repo("OpenCareer_AGENT", "https://github.com/alex/OpenCareer_AGENT", "agent", "Python")
    draft = github.draft_projects("alex", [repo], profile, session=FakeGitHub(), client=client)
    assert draft["new_projects"][0]["name"] == "OpenCareer Agent"
    assert draft["updates"][0]["project"] == "Socket Chat"
    assert "name: OpenCareer Agent" in draft["yaml"]
    sent = client.sent[0]
    assert "pre-fills applications" in sent and "Socket Chat" in sent   # README + existing projects
    for private in ("123 Secret St", "alex@example.com", "Alex Rivera"):
        assert private not in sent


def test_bad_draft_is_rejected():
    with pytest.raises(github.GitHubError):
        github.parse_draft("new_projects: [unclosed")
    with pytest.raises(github.GitHubError):
        github.parse_entries("just some text")


def test_insert_keeps_comments_and_other_sections():
    entries = [{"name": "OpenCareer Agent", "status": "done", "stack": ["Python"], "bullets": ["Built it."]}]
    updated = github.insert_projects(PROFILE_TEXT, entries)
    assert "# My profile" in updated and '"Socket Chat"   # from my resume' in updated
    assert "# Which resume file to start from" in updated
    data = yaml.safe_load(updated)
    assert [p["name"] for p in data["projects"]] == ["Socket Chat", "OpenCareer Agent"]
    assert data["base_resumes"] == {"software": "swe.pdf"}
    assert data["application_answers"]["address"]["street"] == "123 Secret St"


def test_insert_into_an_empty_projects_list_and_a_missing_one():
    entries = [{"name": "A"}]
    empty = 'name: "x"\nprojects: []\n#  - name: ""\n\nskills:\n  languages: []\n'
    assert yaml.safe_load(github.insert_projects(empty, entries))["projects"] == [{"name": "A"}]
    missing = 'name: "x"\n'
    assert yaml.safe_load(github.insert_projects(missing, entries))["projects"] == [{"name": "A"}]


def test_one_line_projects_list_is_left_for_you():
    with pytest.raises(github.GitHubError, match="Profile editor"):
        github.insert_projects('projects: [{name: X}]\n', [{"name": "A"}])


def test_add_to_profile_skips_projects_you_already_have(tmp_path):
    (tmp_path / "profile.yaml").write_text(PROFILE_TEXT)
    added = github.add_to_profile(tmp_path, [{"name": "socket chat"}, {"name": "OpenCareer Agent"}])
    assert added == 1
    data = yaml.safe_load((tmp_path / "profile.yaml").read_text())
    assert [p["name"] for p in data["projects"]] == ["Socket Chat", "OpenCareer Agent"]
    assert github.add_to_profile(tmp_path, [{"name": "OpenCareer Agent"}]) == 0


# ---------- the dashboard page ----------

@pytest.fixture
def app_client(tmp_path):
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "profile.yaml").write_text(PROFILE_TEXT)
    client = TestClient(create_app(db_path=str(tmp_path / "jobs.db"), boards={}, profile_dir=str(profile),
                                   output_dir=str(tmp_path / "apps"), ai_client=FakeClaude(),
                                   github_session=FakeGitHub()))
    return client, profile


def test_github_page_flow_check_draft_add(app_client):
    client, profile = app_client
    assert "Check my GitHub" in client.get("/github").text

    page = client.post("/github/refresh", follow_redirects=True).text
    assert "Found 2 public repos; 1 aren" in page
    assert "OpenCareer_AGENT" in page and "someone-elses-lib" not in page

    page = client.post("/github/draft", data={"repo": ["OpenCareer_AGENT"]}, follow_redirects=True).text
    assert "Draft project entries" in page and "Add to my profile" in page
    assert "Mention the multithreaded server design" in page
    before = (profile / "profile.yaml").read_text()
    assert "OpenCareer Agent" not in before                         # nothing saved before you click Add

    draft = json.loads((profile / "github_draft.json").read_text())
    page = client.post("/github/add", data={"text": draft["yaml"]}, follow_redirects=True).text
    assert "Added 1 project" in page
    saved = yaml.safe_load((profile / "profile.yaml").read_text())
    assert [p["name"] for p in saved["projects"]] == ["Socket Chat", "OpenCareer Agent"]
    assert not (profile / "github_draft.json").exists()
    assert "✓ yes" in client.get("/github").text


def test_github_page_asks_for_a_link_when_missing(tmp_path):
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "profile.yaml").write_text('name: "x"\n')
    client = TestClient(create_app(db_path=str(tmp_path / "jobs.db"), boards={}, profile_dir=str(profile),
                                   output_dir=str(tmp_path / "apps"), github_session=FakeGitHub()))
    assert "Add your GitHub link" in client.get("/github").text
    assert "Add your GitHub link" in client.post("/github/refresh", follow_redirects=True).text


def test_broken_edit_is_not_saved(app_client):
    client, profile = app_client
    before = (profile / "profile.yaml").read_text()
    page = client.post("/github/add", data={"text": "- name: [oops"}, follow_redirects=True).text
    assert "Not added" in page
    assert (profile / "profile.yaml").read_text() == before
