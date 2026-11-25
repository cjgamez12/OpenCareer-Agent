"""Privacy check: make sure your personal files can't end up on GitHub or be read by others.

Run it any time:
    PYTHONPATH=src python -m opencareer.privacy
Turn on the commit guard (blocks any commit that contains your personal info):
    PYTHONPATH=src python -m opencareer.privacy --install-hook
"""
import argparse
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
PRIVATE = ["profile/", "applications/", "*.db", ".env", "autopilot.log", "profile.yaml"]
PRIVATE_PATHS = ("profile/", "applications/", ".env", "jobs.db", "autopilot.log")


def _git(*args: str, cwd: Path) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    return result.stdout if result.returncode == 0 else ""


def _is_private(path: str) -> bool:
    return (path.startswith(PRIVATE_PATHS[:2]) or path in PRIVATE_PATHS[2:] or path.endswith(".db")
            or path.split("/")[-1] == "profile.yaml")              # a profile copy anywhere in the project


# ---------------------------------------------------------------------------
# Commit guard: runs before every commit and refuses one that contains your personal info
# ---------------------------------------------------------------------------

RESUME_FILES = (".pdf", ".doc", ".docx")
HOOK_MARK = "# OpenCareer privacy guard"
HOOK = f"""#!/bin/sh
{HOOK_MARK}: blocks commits that contain your personal info.
cd "$(git rev-parse --show-toplevel)" || exit 1
PYTHON=".venv/bin/python"
[ -x "$PYTHON" ] || PYTHON="python3"
PYTHONPATH=src "$PYTHON" -m opencareer.privacy --staged
"""


def profile_dir_for(project: Path) -> Path:
    """Where your profile lives: OPENCAREER_PROFILE_DIR from .env, or the project's profile/ folder."""
    try:
        from dotenv import dotenv_values
        configured = dotenv_values(project / ".env").get("OPENCAREER_PROFILE_DIR")
    except ImportError:
        configured = None
    folder = Path(os.environ.get("OPENCAREER_PROFILE_DIR") or configured or "profile").expanduser()
    return folder if folder.is_absolute() else project / folder


def personal_values(profile: dict) -> dict[str, re.Pattern]:
    """Patterns for the things that identify you: email, phone number, street address."""
    found = {}
    email = str(profile.get("email") or "").strip()
    if "@" in email:
        found["your email"] = re.compile(re.escape(email), re.I)
    digits = re.sub(r"\D", "", str(profile.get("phone") or ""))[-10:]
    if len(digits) >= 7:
        found["your phone number"] = re.compile(r"[\s().-]*".join(digits))
    street = str((((profile.get("application_answers") or {}).get("address") or {}).get("street")) or "").strip()
    if len(street) >= 6:
        found["your street address"] = re.compile(r"\s+".join(map(re.escape, street.split())), re.I)
    return found


def _staged_files(project: Path) -> list[str]:
    out = _git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z", cwd=project)
    return [path for path in out.split("\0") if path]


def staged_problems(project: Path = PROJECT, profile: dict | None = None) -> list[str]:
    """What's wrong with the commit you're about to make. Never prints your info itself."""
    if profile is None:
        import yaml
        path = profile_dir_for(project) / "profile.yaml"
        try:
            profile = (yaml.safe_load(path.read_text()) or {}) if path.exists() else {}
        except yaml.YAMLError:
            profile = {}
    patterns = personal_values(profile if isinstance(profile, dict) else {})
    problems = []
    for path in _staged_files(project):
        if _is_private(path):
            problems.append(f"{path}: this is a private file (profile, applications, database or keys)")
            continue
        if path.lower().endswith(RESUME_FILES):
            problems.append(f"{path}: looks like a resume or document; keep those in your profile folder")
            continue
        shown = subprocess.run(["git", "show", f":{path}"], cwd=project, capture_output=True)
        text = shown.stdout[:20_000_000].decode("utf-8", errors="ignore")
        for what, pattern in patterns.items():
            if pattern.search(text):
                problems.append(f"{path}: contains {what}")
    return problems


def _hook_path(project: Path) -> Path | None:
    where = _git("rev-parse", "--git-path", "hooks/pre-commit", cwd=project).strip()
    return (project / where) if where else None


def install_hook(project: Path = PROJECT) -> str:
    path = _hook_path(project)
    if path is None:
        return "This folder isn't a git repository."
    if path.exists() and HOOK_MARK not in path.read_text(errors="ignore"):
        return f"There's already a different pre-commit hook at {path}; not replacing it. Tell Claude."
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(HOOK)
    path.chmod(0o755)
    return "Commit guard is on: every commit is checked for your personal info first."


def hook_installed(project: Path = PROJECT) -> bool:
    path = _hook_path(project)
    return bool(path and path.exists() and HOOK_MARK in path.read_text(errors="ignore")
                and os.access(path, os.X_OK))


def check(project: Path = PROJECT) -> list[tuple[bool, str, str]]:
    """Returns (ok, what was checked, how to fix it if not ok)."""
    results = []
    ignore = (project / ".gitignore").read_text().split() if (project / ".gitignore").exists() else []
    missing = [p for p in PRIVATE if p not in ignore]
    results.append((not missing, ".gitignore covers your private files",
                    "Add these lines to .gitignore: " + ", ".join(missing)))

    tracked = [p for p in _git("ls-files", cwd=project).splitlines() if _is_private(p)]
    results.append((not tracked, "No private files are tracked by git",
                    "Stop tracking them (keeps your copy): git rm -r --cached " + " ".join(tracked)))

    history = {p for p in _git("log", "--all", "--name-only", "--pretty=format:", cwd=project).splitlines()
               if p and _is_private(p)}
    results.append((not history, "No private files were ever committed (full history checked)",
                    "These are in your git history: " + ", ".join(sorted(history)) +
                    ". Tell Claude, and keep the repo private until it's cleaned."))

    if _git("rev-parse", "--git-dir", cwd=project):
        results.append((hook_installed(project), "Commit guard is on (blocks commits with your personal info)",
                        "Run: PYTHONPATH=src python -m opencareer.privacy --install-hook"))

    data = profile_dir_for(project)
    inside = data.resolve().is_relative_to(project.resolve())
    results.append((True, "Your profile folder: " + ("inside the project (ignored by git)" if inside
                                                       else f"outside the project ({data})"), ""))

    env = project / ".env"
    if env.exists():
        mode = stat.S_IMODE(env.stat().st_mode)
        results.append((mode & 0o077 == 0, ".env (your API keys) is readable only by you",
                        "Run: chmod 600 .env"))
    return results


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Check that your personal info can't end up on GitHub.")
    parser.add_argument("--staged", action="store_true", help="check the commit you're about to make")
    parser.add_argument("--install-hook", action="store_true", help="check every commit automatically")
    args = parser.parse_args(argv)
    if args.install_hook:
        print(install_hook())
        return
    if args.staged:
        top = _git("rev-parse", "--show-toplevel", cwd=Path.cwd()).strip()   # the repo being committed
        problems = staged_problems(Path(top) if top else PROJECT)
        if problems:
            print("🛑 Commit blocked by OpenCareer's privacy guard:")
            for problem in problems:
                print("   • " + problem)
            print("Remove it (git restore --staged <file>), fix the file, then commit again.")
            sys.exit(1)
        return
    results = check()
    for ok, what, fix in results:
        print(("✅ " if ok else "❌ ") + what)
        if not ok:
            print("   → " + fix)
    print()
    print("Also good to know:")
    print("  • The dashboard only listens on 127.0.0.1: other devices, even on your Wi-Fi, can't open it.")
    print("  • Tailoring sends Claude your resume facts, never your address, demographics, or preferences.")
    print("  • Fetching jobs sends nothing about you; assisted apply sends your info only to the")
    print("    company whose form you submit.")
    print("  • For dropdowns the rules can't answer, Claude sees the question, its choices, and your")
    print("    education, work and skills, and your target location names: never your name, contact")
    print("    info, address, or demographics.")
    print("    Turn it off with OPENCAREER_AI_PICKS=0 in .env.")
    print("  • The GitHub page reads only your public repos; Claude sees their READMEs and the")
    print("    projects already in your profile.")
    if all(ok for ok, _, _ in results):
        print("\nAll checks passed.")


if __name__ == "__main__":
    main()
