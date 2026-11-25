"""Publish a clean public copy of this project.

Your working repo stays private. This makes (or updates) a separate folder with:
- only the files you've COMMITTED (your profile, applications, database and .env can't come along),
- a scan of every file for your email, phone number and street address (it stops if it finds any),
- fresh history: one new commit per publish, under your GitHub "noreply" email, so your personal
  email isn't in the public commit history either.

Run:
    PYTHONPATH=src python -m opencareer.publish --to ~/OpenCareer_Public --email ID+username@users.noreply.github.com
Then push that folder to an empty public GitHub repo (it prints the exact commands).
"""
import argparse
import io
import shutil
import subprocess
import tarfile
from datetime import date
from pathlib import Path

from opencareer import privacy

NOREPLY = "@users.noreply.github.com"
PUBLIC_REPO = "OpenCareer-Agent"


class PublishError(Exception):
    """Something to fix before publishing."""


def _run(args, cwd, check=True):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=check)


def scan_folder(folder: Path, profile: dict) -> list[str]:
    """Every problem in the copy: private files, resumes, or your personal info in any file."""
    patterns = privacy.personal_values(profile or {})
    problems = []
    for path in sorted(p for p in folder.rglob("*") if p.is_file() and ".git" not in p.relative_to(folder).parts):
        rel = path.relative_to(folder).as_posix()
        if privacy._is_private(rel):
            problems.append(f"{rel}: this is a private file (profile, applications, database or keys)")
            continue
        if rel.lower().endswith(privacy.RESUME_FILES):
            problems.append(f"{rel}: looks like a resume or document")
            continue
        text = path.read_bytes()[:20_000_000].decode("utf-8", errors="ignore")
        problems += [f"{rel}: contains {what}" for what, pattern in patterns.items() if pattern.search(text)]
    return problems


def _export_committed_files(project: Path, out: Path) -> None:
    """Replace the copy's files with exactly what's committed in your repo (its .git is kept)."""
    for child in out.iterdir():
        if child.name == ".git":
            continue
        shutil.rmtree(child) if child.is_dir() and not child.is_symlink() else child.unlink()
    archive = subprocess.run(["git", "archive", "--format=tar", "HEAD"], cwd=project,
                             capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(out, filter="data")


def publish(project: Path, out: Path, email: str, name: str, profile: dict) -> str:
    """Make or update the public copy and commit it. Returns what happened. Never pushes."""
    if not email.endswith(NOREPLY):
        raise PublishError("Use your GitHub noreply email so your personal email isn't public. "
                           "Find it at github.com/settings/emails (turn on 'Keep my email addresses private'); "
                           f"it looks like 12345678+username{NOREPLY}")
    project, out = Path(project).resolve(), Path(out).expanduser().resolve()
    if out == project or project in out.parents:
        raise PublishError("Pick a folder outside your project, like ~/OpenCareer_Public.")
    if _run(["git", "rev-parse", "--verify", "HEAD"], project, check=False).returncode != 0:
        raise PublishError("Your project has no commits yet.")
    out.mkdir(parents=True, exist_ok=True)
    if not (out / ".git").exists():
        _run(["git", "init", "-q", "-b", "main"], out)
    _export_committed_files(project, out)

    problems = scan_folder(out, profile)
    if problems:
        raise PublishError("Not published. Fix these in your project, commit, and run publish again:\n"
                           + "\n".join("   • " + p for p in problems))

    _run(["git", "add", "-A"], out)
    if not _run(["git", "status", "--porcelain"], out).stdout.strip():
        return "Nothing changed since the last publish."
    first = _run(["git", "rev-parse", "--verify", "HEAD"], out, check=False).returncode != 0
    message = "OpenCareer Agent" if first else f"Update ({date.today().isoformat()})"
    _run(["git", "-c", f"user.name={name}", "-c", f"user.email={email}", "-c", "commit.gpgsign=false",
          "commit", "-q", "--no-verify", "-m", message], out)
    return "Published a clean copy (first commit)." if first else "Updated the clean copy (new commit)."


def uncommitted_changes(project: Path) -> bool:
    return bool(_run(["git", "status", "--porcelain", "--untracked-files=no"], project, check=False).stdout.strip())


def main(argv=None) -> None:
    import yaml

    parser = argparse.ArgumentParser(description="Make a clean public copy of this project.")
    parser.add_argument("--to", default="~/OpenCareer_Public", help="folder for the public copy")
    parser.add_argument("--email", required=True, help="your GitHub noreply email")
    parser.add_argument("--name", default="", help="author name for the public commit")
    args = parser.parse_args(argv)

    project = privacy.PROJECT
    profile_file = privacy.profile_dir_for(project) / "profile.yaml"
    profile = (yaml.safe_load(profile_file.read_text()) or {}) if profile_file.exists() else {}
    if not profile:
        raise SystemExit("Couldn't read your profile, so the scan can't check for your info. Not published.")
    name = args.name or str(profile.get("name") or "").strip() or "OpenCareer"
    if uncommitted_changes(project):
        print("Note: you have uncommitted changes. Only committed files are published.\n")
    try:
        print(publish(project, Path(args.to), args.email, name, profile))
    except PublishError as error:
        raise SystemExit(str(error))
    out = Path(args.to).expanduser()
    has_remote = bool(_run(["git", "remote"], out).stdout.strip())
    print("\nScanned every file: none of your email, phone or address, and no private files.")
    print(f"Look through it first if you like: open {out}\n")
    if has_remote:
        print(f"To publish:\n  cd {out}\n  git push")
    else:
        print(f"To publish: create an EMPTY public repo at github.com/new named {PUBLIC_REPO}, then run:")
        print(f"  cd {out}")
        from opencareer import github
        user = github.username_from(profile) or "<your-username>"
        print(f"  git remote add origin https://github.com/{user}/{PUBLIC_REPO}.git")
        print("  git push -u origin main")


if __name__ == "__main__":
    main()
