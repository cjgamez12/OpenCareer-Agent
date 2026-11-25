"""Your profile (profile.yaml) and resume files, plus AI help with both.

Everything lives in the profile/ folder, which git ignores.
"""
import re
from pathlib import Path

import yaml

from opencareer.ai import ask_claude

PROFILE_FILE = "profile.yaml"
RESUME_SUFFIXES = {".pdf", ".md", ".txt"}

BLANK_TEMPLATE = """# Your applicant profile. Tailoring uses ONLY what is written here and in your resumes.
name: ""
email: ""
phone: ""
links:
  linkedin: ""
  github: ""

preferences:
  willing_to_relocate: null   # true / false
  needs_sponsorship: null     # true / false

education:
  school: ""
  degree: ""
  graduation: ""      # e.g. 2026-06
  gpa: ""

skills:
  languages: []

experience: []
#  - title: ""
#    org: ""
#    dates: ""
#    bullets: []

projects: []
#  - name: ""
#    status: done        # done / in_progress / planned
#    stack: []

# Which resume file to start from for each kind of job (see the Resumes page)
base_resumes:
  software: ""
  cybersecurity: ""
  networking: ""
  it: ""
"""

BUILD_PROFILE_PROMPT = """You turn an applicant's resumes into one profile.yaml file.

Rules:
- Use ONLY facts written in the resumes. Never invent anything.
- When resumes disagree (dates, honors, years of experience, numbers), do not pick one:
  write the field and add a comment on that line: "# TODO check: resumes disagree (A vs B)".
- Projects marked Pending, Planned, or still in progress get status: planned or in_progress.
  Note any placeholder text like [X] in a comment.
- Keep non-technical jobs, but add "tech: false" to them.
- Fill base_resumes with the resume FILE NAME that best fits each category
  (software, cybersecurity, networking, it, non_tech).
- Leave preferences (relocation, sponsorship, locations) as null with "# TODO" comments.
- Keep it compact: list each skill ONCE (no years in parentheses), at most about 40 skills,
  grouped into a few categories. Skip generic soft skills ("Attention to Detail").
- When several resumes word the same bullet differently, keep only the best version.

Follow the shape of this template, adding fields where the resumes have more detail
(bullets for each job, facts for each project, coursework, honors, leadership):

{template}

Reply with ONLY the YAML. No code fences, no explanation."""

REVIEW_PROMPT = """You are an experienced tech recruiter reviewing one resume for entry-level
software, cybersecurity, networking, and IT roles.

Be specific and honest. Never suggest adding facts the applicant didn't provide; where a
bullet lacks a result, say what kind of result would help instead of inventing one.
Compare against the profile, if given, to catch facts that disagree.

Reply in Markdown with exactly these sections:
## Top 3 fixes
## Bullet rewrites
(before -> after, only using facts already present)
## Problems to fix before sending
(placeholders, inconsistent facts, typos, unfinished projects presented as done)
## What's missing for these roles
"""


# ---------- profile.yaml ----------

def profile_path(profile_dir: Path) -> Path:
    return Path(profile_dir) / PROFILE_FILE


def read_profile_text(profile_dir: Path) -> str | None:
    path = profile_path(profile_dir)
    return path.read_text() if path.exists() else None


def parse_profile(text: str) -> dict:
    """Check the YAML is valid and is a mapping (name: ..., skills: ...)."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        where = f" (line {mark.line + 1})" if mark else ""
        raise ValueError(f"That isn't valid YAML{where}. Check indentation and colons.") from error
    if not isinstance(data, dict):
        raise ValueError("The profile should start with fields like  name: Alex Rivera")
    return data


def save_profile_text(profile_dir: Path, text: str) -> dict:
    data = parse_profile(text)
    Path(profile_dir).mkdir(parents=True, exist_ok=True)
    profile_path(profile_dir).write_text(text.rstrip() + "\n")
    return data


# Never sent to the AI: your address, demographic answers, and preferences
# (sponsorship, relocation). A resume doesn't need them, so they stay on your Mac.
PRIVATE_SECTIONS = ("application_answers", "preferences")


def profile_for_ai(profile: dict) -> dict:
    """A copy of your profile with private sections removed, for sending to Claude."""
    return {key: value for key, value in (profile or {}).items() if key not in PRIVATE_SECTIONS}


# ---------- resume files ----------

def list_resumes(profile_dir: Path) -> list[Path]:
    found = []
    for folder in (Path(profile_dir), Path(profile_dir) / "resumes"):
        if folder.is_dir():
            found += [p for p in folder.iterdir()
                      if p.is_file() and p.suffix.lower() in RESUME_SUFFIXES]
    return sorted(found, key=lambda p: p.name.lower())


def _normalize(name: str) -> str:
    """'Alex_Rivera_SWE_Resume copy (1).pdf' -> 'alexriverasweresume'."""
    stem = Path(name).stem.lower()
    stem = re.sub(r"\s*\(\d+\)", "", stem)
    stem = re.sub(r"[\s_-]*copy\b", "", stem)
    return re.sub(r"[^a-z0-9]", "", stem)


def find_resume(profile_dir: Path, name: str) -> Path | None:
    """Find a resume by name, ignoring ' copy', '(1)', spaces, and case."""
    if not name:
        return None
    wanted = _normalize(name)
    files = list_resumes(profile_dir)
    for path in files:
        if path.name == name:
            return path
    for path in files:
        if _normalize(path.name) == wanted:
            return path
    return None


def resume_by_filename(profile_dir: Path, filename: str) -> Path | None:
    """Only ever return files that are really in the profile folder (no ../ tricks)."""
    for path in list_resumes(profile_dir):
        if path.name == filename:
            return path
    return None


def resume_text(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages).strip()
    return path.read_text().strip()


# ---------- AI help ----------

def build_profile_with_ai(profile_dir: Path, *, client=None) -> str:
    """Draft a profile.yaml from all your resumes. Returned for review, NOT saved."""
    resumes = list_resumes(profile_dir)
    if not resumes:
        raise ValueError("Put your resume PDFs in the profile folder first.")
    parts = [f'<resume file="{p.name}">\n{resume_text(p)}\n</resume>' for p in resumes]
    reply = ask_claude(BUILD_PROFILE_PROMPT.format(template=BLANK_TEMPLATE),
                       "\n\n".join(parts), client=client, max_tokens=16000)
    reply = re.sub(r"^```(?:yaml)?\s*|\s*```$", "", reply.strip())
    parse_profile(reply)  # make sure it's usable before showing it
    return reply


def feedback_path(profile_dir: Path, resume: Path) -> Path:
    return Path(profile_dir) / "feedback" / f"{resume.stem}.md"


def review_resume_with_ai(profile_dir: Path, resume: Path, *, client=None) -> str:
    text = read_profile_text(profile_dir)
    try:
        profile = yaml.safe_dump(profile_for_ai(parse_profile(text)), sort_keys=False) if text else "(no profile yet)"
    except ValueError:
        profile = "(profile has a formatting problem)"
    prompt = (f'<resume file="{resume.name}">\n{resume_text(resume)}\n</resume>\n\n'
              f"<profile>\n{profile}\n</profile>")
    feedback = ask_claude(REVIEW_PROMPT, prompt, client=client, max_tokens=3000).strip()
    path = feedback_path(profile_dir, resume)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(feedback + "\n")
    return feedback


def load_feedback(profile_dir: Path, resume: Path) -> str | None:
    path = feedback_path(profile_dir, resume)
    return path.read_text().strip() if path.exists() else None
