"""Your GitHub projects -> resume project entries you approve.

1. Read your PUBLIC repos from GitHub's API (no login; GITHUB_TOKEN in .env is optional and only
   raises GitHub's rate limit).
2. Claude drafts project entries using ONLY what each repo shows: README, description, languages.
3. You edit the draft, then click Add. Only then is anything written to profile.yaml, and only
   new projects are added: the rest of the file, comments included, is left exactly as it was.

Tailoring reads your profile's projects, so added projects show up in tailored resumes.
"""
import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import requests
import yaml

API = "https://api.github.com"
README_LIMIT = 6000                 # characters of each README sent to Claude
MAX_SELECTED = 10
REPOS_FILE = "github_repos.json"    # kept in your profile folder (never committed)
DRAFT_FILE = "github_draft.json"


class GitHubError(Exception):
    """A problem to show on the page as a friendly message."""


@dataclass
class Repo:
    name: str
    url: str
    description: str = ""
    language: str = ""
    topics: list = field(default_factory=list)
    pushed_at: str = ""
    stars: int = 0
    in_profile: bool = False


def username_from(profile: dict) -> str | None:
    """'https://github.com/alexrivera' -> 'alexrivera'."""
    link = str(((profile or {}).get("links") or {}).get("github") or "").strip()
    match = re.search(r"github\.com/([A-Za-z0-9-]+)", link) or re.fullmatch(r"@?([A-Za-z0-9-]+)", link)
    return match.group(1) if match else None


def _headers(raw: bool = False) -> dict:
    headers = {"Accept": "application/vnd.github.raw" if raw else "application/vnd.github+json",
               "User-Agent": "OpenCareer-Agent"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    return headers


def _get(session, url: str, raw: bool = False):
    try:
        response = session.get(url, headers=_headers(raw), timeout=20)
    except requests.RequestException as error:
        raise GitHubError(f"Couldn't reach GitHub: {error}") from error
    if response.status_code == 403 and "rate limit" in response.text.lower():
        raise GitHubError("GitHub's hourly limit for anonymous requests was reached. Try again in an hour, "
                          "or add GITHUB_TOKEN to .env.")
    return response


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _norm_url(url: str) -> str:
    return (url or "").lower().rstrip("/").removesuffix(".git").replace("http://", "https://")


def matches_project(repo: Repo, projects: list) -> bool:
    """Is this repo already one of your profile's projects (same link or same name)?"""
    for project in projects or []:
        if not isinstance(project, dict):
            continue
        link = _norm_url(str(project.get("link") or ""))
        if link and link == _norm_url(repo.url):
            return True
        if _key(str(project.get("name") or "")) == _key(repo.name):
            return True
    return False


def fetch_repos(username: str, profile: dict | None = None, session=None) -> list[Repo]:
    """Your public repos, newest activity first. Forks are skipped (they're someone else's code)."""
    session = session or requests.Session()
    response = _get(session, f"{API}/users/{username}/repos?per_page=100&sort=pushed")
    if response.status_code == 404:
        raise GitHubError(f"No GitHub user named {username}. Check links.github in your profile.")
    if response.status_code != 200:
        raise GitHubError(f"GitHub answered {response.status_code}.")
    projects = (profile or {}).get("projects") or []
    repos = []
    for item in response.json():
        if item.get("fork"):
            continue
        repo = Repo(name=item.get("name", ""), url=item.get("html_url", ""),
                    description=item.get("description") or "", language=item.get("language") or "",
                    topics=item.get("topics") or [], pushed_at=(item.get("pushed_at") or "")[:10],
                    stars=item.get("stargazers_count") or 0)
        repo.in_profile = matches_project(repo, projects)
        repos.append(repo)
    return repos


def fetch_readme(username: str, repo: str, session=None) -> str:
    session = session or requests.Session()
    response = _get(session, f"{API}/repos/{username}/{repo}/readme", raw=True)
    return response.text[:README_LIMIT] if response.status_code == 200 else ""


def fetch_languages(username: str, repo: str, session=None) -> list[str]:
    session = session or requests.Session()
    response = _get(session, f"{API}/repos/{username}/{repo}/languages")
    return list(response.json())[:8] if response.status_code == 200 else []


# ---------------------------------------------------------------------------
# Saving what we fetched (in your profile folder, which git ignores)
# ---------------------------------------------------------------------------

def save_repos(profile_dir: Path, username: str, repos: list[Repo]) -> None:
    data = {"username": username, "repos": [asdict(r) for r in repos]}
    (Path(profile_dir) / REPOS_FILE).write_text(json.dumps(data, indent=1))


def load_repos(profile_dir: Path) -> tuple[str | None, list[Repo]]:
    path = Path(profile_dir) / REPOS_FILE
    if not path.exists():
        return None, []
    try:
        data = json.loads(path.read_text())
        return data.get("username"), [Repo(**r) for r in data.get("repos", [])]
    except (json.JSONDecodeError, TypeError):
        return None, []


def save_draft(profile_dir: Path, draft: dict) -> None:
    (Path(profile_dir) / DRAFT_FILE).write_text(json.dumps(draft, indent=1))


def load_draft(profile_dir: Path) -> dict | None:
    path = Path(profile_dir) / DRAFT_FILE
    try:
        return json.loads(path.read_text()) if path.exists() else None
    except json.JSONDecodeError:
        return None


def clear_draft(profile_dir: Path) -> None:
    (Path(profile_dir) / DRAFT_FILE).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Claude drafts the entries
# ---------------------------------------------------------------------------

DRAFT_SYSTEM = """You write resume project entries from a developer's own GitHub repositories.
Rules:
- Use ONLY what each repository shows (README, description, languages, topics). Never invent features, users, numbers, awards or results.
- Skip repos with nothing to show (no README and no description, a class template with no real work, or a placeholder). Put them under "skipped" with a short reason.
- status: "done", unless the README says the work is in progress or planned ("in_progress" or "planned").
- stack: the languages, frameworks and tools the repo actually uses.
- bullets: 2 or 3 resume bullets. Start each with a strong verb; say concretely what was built and with what. Numbers only if the README states them.
- If a repo is already one of EXISTING_PROJECTS (same name or link), don't add it again. If the repo shows something the existing entry is missing, put one sentence under "updates".
Reply with YAML only, in exactly this shape:
new_projects:
  - name: ...
    status: done
    stack: [...]
    link: ...
    bullets: [...]
updates:
  - project: ...
    suggestion: ...
skipped:
  - repo: ...
    reason: ..."""


def build_prompt(repos: list[dict], existing_projects: list) -> str:
    payload = {"existing_projects": existing_projects or [], "repositories": repos}
    return json.dumps(payload, indent=1, default=str)


def parse_draft(text: str) -> dict:
    """Claude's YAML -> {"new_projects": [...], "updates": [...], "skipped": [...]}."""
    body = re.sub(r"^```(?:yaml)?\s*|\s*```\s*$", "", text.strip())
    try:
        data = yaml.safe_load(body) or {}
    except yaml.YAMLError as error:
        raise GitHubError(f"Claude's draft wasn't valid YAML, so it wasn't used. Try again. ({error})") from error
    if not isinstance(data, dict):
        raise GitHubError("Claude's draft wasn't in the expected shape. Try again.")
    projects = [p for p in (data.get("new_projects") or []) if isinstance(p, dict) and p.get("name")]
    updates = [u for u in (data.get("updates") or []) if isinstance(u, dict)]
    skipped = [s for s in (data.get("skipped") or []) if isinstance(s, dict)]
    return {"new_projects": projects, "updates": updates, "skipped": skipped}


def draft_projects(username: str, selected: list[Repo], profile: dict, *, session=None, client=None) -> dict:
    """Read the chosen repos and have Claude draft entries. Returns a draft for review; saves nothing."""
    from opencareer import ai

    if not selected:
        raise GitHubError("Pick at least one repo.")
    repos = []
    for repo in selected[:MAX_SELECTED]:
        repos.append({"name": repo.name, "link": repo.url, "description": repo.description,
                      "languages": fetch_languages(username, repo.name, session) or [repo.language],
                      "topics": repo.topics, "last_push": repo.pushed_at,
                      "readme": fetch_readme(username, repo.name, session)})
    text = ai.ask_claude(DRAFT_SYSTEM, build_prompt(repos, profile.get("projects") or []),
                         client=client, max_tokens=4000)
    draft = parse_draft(text)
    draft["yaml"] = entries_to_yaml(draft["new_projects"])
    return draft


def entries_to_yaml(entries: list[dict]) -> str:
    return yaml.safe_dump(entries, sort_keys=False, allow_unicode=True, width=1000) if entries else ""


# ---------------------------------------------------------------------------
# Adding approved entries to profile.yaml without disturbing anything else
# ---------------------------------------------------------------------------

def parse_entries(text: str) -> list[dict]:
    """Your edited draft (a YAML list) -> entries. Raises GitHubError if it isn't valid."""
    try:
        data = yaml.safe_load(text or "") or []
    except yaml.YAMLError as error:
        raise GitHubError(f"The draft isn't valid YAML: {error}") from error
    if isinstance(data, dict) and "new_projects" in data:
        data = data["new_projects"] or []
    if not isinstance(data, list) or not all(isinstance(e, dict) and e.get("name") for e in data):
        raise GitHubError("The draft should be a list of projects, each with a name.")
    return data


def insert_projects(profile_text: str, entries: list[dict]) -> str:
    """Add entries to the projects: section of profile.yaml as text, so your comments stay.

    Raises GitHubError when the projects section is written in a form we can't safely add to."""
    if not entries:
        return profile_text
    block = "".join("  " + line + "\n" if line else "\n"
                    for line in entries_to_yaml(entries).rstrip("\n").split("\n"))
    lines = profile_text.split("\n")
    header = next((i for i, line in enumerate(lines) if re.match(r"^projects:", line)), None)
    if header is None:
        return profile_text.rstrip("\n") + "\n\nprojects:\n" + block
    rest = lines[header].split(":", 1)[1].split("#", 1)[0].strip()
    if rest == "[]":
        lines[header] = "projects:"
    elif rest:
        raise GitHubError("Your projects list is written on one line. Add these in the Profile editor instead.")
    last = header
    for i in range(header + 1, len(lines)):
        line = lines[i]
        if line and not line[0].isspace() and not line.startswith("#"):
            break                                     # the next top-level section
        if line.strip() and line[0].isspace():
            last = i                                  # still inside projects:
    lines.insert(last + 1, block.rstrip("\n"))
    return "\n".join(lines)


def add_to_profile(profile_dir: Path, entries: list[dict]) -> int:
    """Add approved projects that aren't in your profile yet. Returns how many were added."""
    from opencareer import applicant

    text = applicant.read_profile_text(profile_dir)
    if text is None:
        raise GitHubError("Set up your profile first.")
    profile = applicant.parse_profile(text)
    existing = profile.get("projects") or []
    new = [e for e in entries
           if not matches_project(Repo(name=str(e.get("name")), url=str(e.get("link") or "")), existing)]
    if not new:
        return 0
    updated = insert_projects(text, new)
    check = applicant.parse_profile(updated)                    # never save a broken profile
    if len(check.get("projects") or []) != len(existing) + len(new):
        raise GitHubError("Couldn't add them cleanly. Add these in the Profile editor instead.")
    applicant.save_profile_text(profile_dir, updated)
    return len(new)
