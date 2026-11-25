"""Tests that private data stays private."""
import subprocess

import yaml

from opencareer import applicant, privacy, tailor
from opencareer.models import JobPosting

PROFILE = {
    "name": "Alex Rivera", "email": "c@example.com",
    "preferences": {"needs_sponsorship": False},
    "application_answers": {"address": {"street": "123 Secret St", "zip": "95113"},
                            "demographics": {"gender": "Decline to answer"}},
    "experience": [{"title": "SDE Intern", "org": "Initech"}],
}


def test_tailoring_prompt_never_contains_address_or_demographics():
    job = JobPosting(source="x", source_id="1", title="SWE", company="Acme", url="u")
    prompt = tailor.build_prompt(job, "desc", PROFILE, "")
    assert "123 Secret St" not in prompt and "95113" not in prompt
    assert "demographics" not in prompt and "needs_sponsorship" not in prompt
    assert "Initech" in prompt and "c@example.com" in prompt      # resume facts still included


def test_resume_review_never_sends_address(tmp_path):
    (tmp_path / "profile.yaml").write_text(yaml.safe_dump(PROFILE))
    (tmp_path / "r.md").write_text("Alex resume")
    sent = []

    class Client:
        messages = None

        def __init__(self):
            self.messages = self

        def create(self, **kwargs):
            sent.append(kwargs["messages"][0]["content"])
            from types import SimpleNamespace
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")])
    applicant.review_resume_with_ai(tmp_path, tmp_path / "r.md", client=Client())
    assert "123 Secret St" not in sent[0] and "Initech" in sent[0]


def git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def test_privacy_check_passes_on_a_clean_repo(tmp_path):
    git(tmp_path, "init", "-q")
    (tmp_path / ".gitignore").write_text("\n".join(privacy.PRIVATE) + "\n")
    (tmp_path / "profile").mkdir()
    (tmp_path / "profile" / "profile.yaml").write_text("name: x")
    (tmp_path / ".env").write_text("KEY=1")
    (tmp_path / ".env").chmod(0o600)
    (tmp_path / "app.py").write_text("print(1)")
    git(tmp_path, "add", ".")
    git(tmp_path, "-c", "user.email=a@b.c", "-c", "user.name=t", "commit", "-qm", "init")
    assert not all(ok for ok, _, _ in privacy.check(tmp_path))       # guard not on yet
    privacy.install_hook(tmp_path)
    assert all(ok for ok, _, _ in privacy.check(tmp_path))


def test_privacy_check_catches_a_committed_profile(tmp_path):
    git(tmp_path, "init", "-q")
    (tmp_path / "profile").mkdir()
    (tmp_path / "profile" / "profile.yaml").write_text("name: x")
    git(tmp_path, "add", ".")
    git(tmp_path, "-c", "user.email=a@b.c", "-c", "user.name=t", "commit", "-qm", "oops")
    failed = [what for ok, what, _ in privacy.check(tmp_path) if not ok]
    assert ".gitignore covers your private files" in failed
    assert "No private files are tracked by git" in failed
    assert "No private files were ever committed (full history checked)" in failed


def test_privacy_check_catches_a_profile_copy_outside_the_profile_folder(tmp_path):
    git(tmp_path, "init", "-q")
    (tmp_path / ".gitignore").write_text("profile/\napplications/\n*.db\n.env\nautopilot.log\n")
    (tmp_path / "profile.yaml").write_text("name: x")
    git(tmp_path, "add", ".")
    git(tmp_path, "-c", "user.email=a@b.c", "-c", "user.name=t", "commit", "-qm", "oops")
    failed = {what: fix for ok, what, fix in privacy.check(tmp_path) if not ok}
    assert "profile.yaml" in failed[".gitignore covers your private files"]
    assert "profile.yaml" in failed["No private files are tracked by git"]


# ---------- commit guard ----------

ME = {"email": "alex.private@example.org", "phone": "408-555-0142",
      "application_answers": {"address": {"street": "742 Evergreen Terrace"}}}


def test_guard_finds_your_info_in_any_file_and_any_format(tmp_path):
    git(tmp_path, "init", "-q")
    files = {
        "notes.txt": "call me at (408) 555 0142",
        "src/app.py": "EMAIL = 'Alex.Private@Example.org'",
        "docs/where.md": "I live at 742  evergreen   terrace",
        "backup/profile.yaml": "name: x",
        "Alex_Resume.pdf": "%PDF",
        "clean.py": "print('hello')",
        "tests/fake.py": "EMAIL = 'alex@example.com'; PHONE = '408-000-0000'",
    }
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text)
    git(tmp_path, "add", "-f", ".")
    problems = privacy.staged_problems(tmp_path, ME)
    joined = "\n".join(problems)
    assert "notes.txt: contains your phone number" in joined
    assert "src/app.py: contains your email" in joined
    assert "docs/where.md: contains your street address" in joined
    assert "backup/profile.yaml: this is a private file" in joined
    assert "Alex_Resume.pdf: looks like a resume" in joined
    assert "clean.py" not in joined and "tests/fake.py" not in joined
    assert "alex.private@example.org" not in joined.lower() and "742" not in joined   # never echoes your info


def test_install_hook_never_replaces_someone_elses_hook(tmp_path):
    git(tmp_path, "init", "-q")
    hook = tmp_path / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho mine\n")
    assert "not replacing" in privacy.install_hook(tmp_path)
    assert hook.read_text() == "#!/bin/sh\necho mine\n"
    hook.unlink()
    privacy.install_hook(tmp_path)
    assert privacy.hook_installed(tmp_path)


def test_guard_blocks_a_real_commit_and_allows_a_clean_one(tmp_path):
    import os
    import sys
    import yaml
    from pathlib import Path

    git(tmp_path, "init", "-q")
    (tmp_path / "src").symlink_to(Path(privacy.__file__).resolve().parents[1])     # the opencareer code
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    shim = tmp_path / ".venv" / "bin" / "python"                  # runs YOUR python, venv and all
    shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    shim.chmod(0o755)
    (tmp_path / "profile").mkdir()
    (tmp_path / "profile" / "profile.yaml").write_text(yaml.safe_dump(ME))
    privacy.install_hook(tmp_path)
    env = {k: v for k, v in os.environ.items() if k != "OPENCAREER_PROFILE_DIR"}

    def commit(name, text):
        (tmp_path / name).write_text(text)
        subprocess.run(["git", "add", name], cwd=tmp_path, check=True)
        return subprocess.run(["git", "-c", "user.email=a@b.c", "-c", "user.name=t", "commit", "-m", name],
                              cwd=tmp_path, capture_output=True, text=True, env=env)

    leak = commit("leak.txt", "reach me: 408.555.0142")
    assert leak.returncode != 0 and "Commit blocked" in leak.stdout + leak.stderr
    subprocess.run(["git", "rm", "-q", "--cached", "leak.txt"], cwd=tmp_path, check=True)
    ok = commit("ok.txt", "nothing personal here")
    assert ok.returncode == 0, ok.stdout + ok.stderr


def test_profile_folder_can_live_outside_the_project(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENCAREER_PROFILE_DIR", raising=False)
    (tmp_path / ".env").write_text("OPENCAREER_PROFILE_DIR=/Users/someone/OpenCareerData/profile\n")
    assert str(privacy.profile_dir_for(tmp_path)) == "/Users/someone/OpenCareerData/profile"
    (tmp_path / ".env").write_text("")
    assert privacy.profile_dir_for(tmp_path) == tmp_path / "profile"
