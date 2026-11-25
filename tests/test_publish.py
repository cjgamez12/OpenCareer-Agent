"""Tests for publishing a clean public copy. Uses throwaway git repos; never pushes."""
import subprocess

import pytest

from opencareer import publish

ME = {"name": "Alex Rivera", "email": "alex.private@example.org", "phone": "408-555-0142",
      "application_answers": {"address": {"street": "742 Evergreen Terrace"}}}
NOREPLY = "12345+alex@users.noreply.github.com"


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout


def commit_all(repo, message="work"):
    git(repo, "add", "-A")
    git(repo, "-c", "user.email=alex.private@example.org", "-c", "user.name=Alex", "commit", "-qm", message)


@pytest.fixture
def project(tmp_path):
    repo = tmp_path / "OpenCareer_Agent"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "app.py").write_text("print('hello')\n")
    (repo / "README.md").write_text("# OpenCareer\n")
    (repo / ".gitignore").write_text("profile/\njobs.db\n.env\n")
    git(repo, "init", "-q", "-b", "main")
    commit_all(repo, "first, with my personal email as author")
    (repo / "profile").mkdir()
    (repo / "profile" / "profile.yaml").write_text("email: alex.private@example.org\n")   # ignored, never copied
    (repo / ".env").write_text("ANTHROPIC_API_KEY=secret\n")
    (repo / "jobs.db").write_bytes(b"db")
    (repo / "draft_not_committed.py").write_text("x = 1\n")
    return repo


def test_publishes_only_committed_files_with_fresh_history(project, tmp_path):
    out = tmp_path / "public"
    assert "first commit" in publish.publish(project, out, NOREPLY, "Alex Rivera", ME)
    files = sorted(p.relative_to(out).as_posix() for p in out.rglob("*")
                   if p.is_file() and ".git" not in p.relative_to(out).parts)
    assert files == [".gitignore", "README.md", "src/app.py"]
    log = git(out, "log", "--format=%an <%ae>|%s")
    assert log.strip() == f"Alex Rivera <{NOREPLY}>|OpenCareer Agent"     # one commit, noreply only
    assert "alex.private@example.org" not in git(out, "log", "--format=%ae %ce")


def test_updates_add_a_commit_and_removed_files_disappear(project, tmp_path):
    out = tmp_path / "public"
    publish.publish(project, out, NOREPLY, "Alex Rivera", ME)
    assert publish.publish(project, out, NOREPLY, "Alex Rivera", ME) == "Nothing changed since the last publish."
    (project / "src" / "app.py").unlink()
    (project / "src" / "new.py").write_text("print('new')\n")
    commit_all(project, "rename")
    assert "Updated" in publish.publish(project, out, NOREPLY, "Alex Rivera", ME)
    assert not (out / "src" / "app.py").exists() and (out / "src" / "new.py").exists()
    assert len(git(out, "log", "--format=%H").split()) == 2


def test_stops_when_your_info_is_in_a_committed_file(project, tmp_path):
    (project / "notes.md").write_text("Text me at 408.555.0142\n")
    (project / "Alex_Resume.pdf").write_bytes(b"%PDF")
    commit_all(project, "oops")
    out = tmp_path / "public"
    with pytest.raises(publish.PublishError) as error:
        publish.publish(project, out, NOREPLY, "Alex Rivera", ME)
    message = str(error.value)
    assert "notes.md: contains your phone number" in message
    assert "Alex_Resume.pdf: looks like a resume" in message
    assert "408" not in message                                           # never repeats your info
    assert subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=out,
                          capture_output=True).returncode != 0            # nothing was committed


def test_requires_a_noreply_email_and_an_outside_folder(project, tmp_path):
    with pytest.raises(publish.PublishError, match="noreply"):
        publish.publish(project, tmp_path / "public", "alex.private@example.org", "Alex", ME)
    with pytest.raises(publish.PublishError, match="outside your project"):
        publish.publish(project, project / "public", NOREPLY, "Alex", ME)
