"""Assisted apply: open a job's real application form, fill what we're sure of, and STOP.

You review every field and click Submit yourself. This code never clicks a submit button.

Run by the dashboard's "Prepare application" button, or by hand:
    PYTHONPATH=src python -m opencareer.apply --url "JOB_URL"
"""
import argparse
import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# 1. Your answers, from profile.yaml
# ---------------------------------------------------------------------------

DEMOGRAPHIC_KEYS = ["gender", "hispanic_latino", "race_ethnicity", "veteran_status", "disability_status"]


def answers_from_profile(profile: dict, company: str = "") -> dict:
    """Everything the filler is allowed to type, in one flat dict. Missing = leave for you."""
    name = (profile.get("name") or "").strip()
    first, _, last = name.partition(" ")
    links = profile.get("links") or {}
    prefs = profile.get("preferences") or {}
    edu = profile.get("education") or {}
    app = profile.get("application_answers") or {}
    demo = app.get("demographics") or {}
    address = app.get("address") or {}
    city, state = (address.get("city") or "").strip(), (address.get("state") or "").strip()
    city_state = ", ".join(part for part in (city, STATE_ABBREVIATIONS.get(state.lower(), state)) if part)

    def yes_no(value):
        return None if value is None else ("Yes" if value else "No")

    job = next((e for e in (profile.get("experience") or []) if e.get("tech", True) is not False), {})
    (start_m, start_y), (end_m, end_y) = _dates_to_month_year(str(job.get("dates") or ""))
    degree = str(edu.get("degree") or "")
    discipline = edu.get("discipline") or (degree.split(" in ", 1)[1].strip() if " in " in degree else None)
    state_full = STATE_NAMES.get(state.lower(), state) if state else ""

    return {
        "first_name": first or None,
        "last_name": last or None,
        "full_name": name or None,
        "email": profile.get("email") or None,
        "phone": str(profile.get("phone") or "") or None,
        "linkedin": links.get("linkedin") or None,
        "github": links.get("github") or None,
        "website": links.get("website") or links.get("github") or None,
        "location": app.get("current_location") or city_state or None,
        "address_line1": address.get("street") or None,
        "city": city or None,
        "state": state or None,
        "zip": str(address.get("zip") or "") or None,
        "country": address.get("country") or None,
        "city_full": ", ".join(p for p in (city, state_full) if p) or None,
        "over_18": yes_no(app.get("over_18")),
        "previously_employed": _worked_at(profile, company),
        **{key: yes_no((app.get("standard_answers") or {}).get(key)) for key in STANDARD_KEYS},
        "degree_level": _degree_level(degree),
        "discipline": discipline,
        "employer": job.get("org") or None,
        "job_title": job.get("title") or None,
        "job_start_month": start_m, "job_start_year": start_y,
        "job_end_month": end_m, "job_end_year": end_y,
        "school": edu.get("school") or None,
        "degree": edu.get("degree") or None,
        "graduation": str(edu.get("graduation") or "") or None,
        "work_authorized": yes_no(app.get("work_authorized_us")),
        "sponsorship": yes_no(prefs.get("needs_sponsorship")),
        "relocate": yes_no(prefs.get("willing_to_relocate")),
        "start_date": app.get("start_date") or None,
        "how_heard": app.get("how_did_you_hear") or None,
        "salary": None,  # always left for you to decide per job
        **{key: (str(demo.get(key)).strip() or None) if demo.get(key) is not None else None
           for key in DEMOGRAPHIC_KEYS},
    }


# Yes/no facts about you, set once in profile.yaml under application_answers.standard_answers
STANDARD_KEYS = ["government_official", "relative_of_government_official", "conflict_of_interest",
                 "referred_by_business_partner"]


def _worked_at(profile: dict, company: str) -> str | None:
    """'Have you worked at <company> before?' from your job history. None if no company given."""
    if not company:
        return None
    wanted = _norm(company)
    for job in profile.get("experience") or []:
        org = _norm(str(job.get("org") or ""))
        if org and (org == wanted or wanted in org.split() or org.startswith(wanted)):
            return "Yes"
    return "No"


MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]


def _dates_to_month_year(text: str):
    """'2025-09 to 2025-12' -> (('September', '2025'), ('December', '2025'))."""
    found = re.findall(r"(\d{4})-(\d{1,2})", text)
    def one(i):
        if len(found) > i:
            year, month = found[i]
            return MONTHS[int(month) - 1], year
        return None, None
    return one(0), one(1)


def _degree_level(degree: str) -> str | None:
    d = degree.lower().replace(".", "")
    if d.startswith(("ba ", "bs ", "bachelor")):
        return "Bachelor's Degree"
    if d.startswith(("ma ", "ms ", "master")):
        return "Master's Degree"
    return None


# ---------------------------------------------------------------------------
# 2. Matching form fields to answers (pure Python, fully tested)
# ---------------------------------------------------------------------------

@dataclass
class Field:
    id: str                       # our data-oc-id on the element
    kind: str                     # text, email, tel, url, textarea, select, radio, checkbox, file, combobox
    label: str
    required: bool = False
    options: list = field(default_factory=list)   # select/radio: [{"id":..., "text":...}]
    hint: str = ""                                 # the element's id/name, e.g. "resume"


@dataclass
class Action:
    field_id: str
    how: str                      # fill, select, check, upload, combobox
    value: str
    what: str                     # which answer was used (for the summary)
    alternates: list = field(default_factory=list)  # other wordings to try in dropdowns


# Order matters: more specific patterns first.
RULES = [
    ("resume_file", r"\bresume\b|\bcv\b"),
    ("cover_letter_file", r"cover letter"),
    ("first_name", r"first name|given name|preferred first"),
    ("last_name", r"last name|family name|surname"),
    ("email", r"e-?mail"),
    ("phone", r"phone|mobile"),
    ("linkedin", r"linkedin"),
    ("github", r"github"),
    ("website", r"website|portfolio|personal site"),
    ("salary", r"salary|compensation|pay expectation|desired pay"),
    ("sponsorship", r"sponsor"),
    ("work_authorized", r"authori[sz]ed to work|legally (?:able|eligible|authori)|work authori[sz]ation|eligible to work"),
    ("relocate", r"relocat"),
    ("job_start_month", r"start (?:date )?month"),
    ("job_start_year", r"start (?:date )?year"),
    ("job_end_month", r"end (?:date )?month"),
    ("job_end_year", r"end (?:date )?year"),
    ("employer", r"company name|^employer|^company\b"),
    ("job_title", r"^title\b|^job title|^position title"),
    ("over_18", r"at least 18|18 years of age|over (?:the age of )?18|18 or older"),
    ("previously_employed", r"previously (?:been )?(?:employed|worked)|ever (?:been )?employed by|former employee|"
                            r"have you (?:ever )?worked (?:for|at)"),
    ("relative_of_government_official", r"relative of a government official|relative.{0,40}government official"),
    ("government_official", r"government official"),
    ("conflict_of_interest", r"conflict of interest"),
    ("referred_by_business_partner", r"referred .{0,40}(?:senior leader|decision.?maker)"),
    ("start_date", r"earliest.*start|when can you start|available to start|desired start|^start date\s*\*?$"),
    ("how_heard", r"how did you hear|how did you find|source of (?:application|referral)|where did you (?:hear|find)"),
    ("hispanic_latino", r"hispanic|latin"),
    ("race_ethnicity", r"\brace\b|ethnicity"),
    ("gender", r"gender|\bsex\b"),
    ("veteran_status", r"veteran|protected veteran"),
    ("disability_status", r"disabilit"),
    ("address_line2", r"address line 2|apt|suite|unit\b"),
    ("address_line1", r"address line 1|street|^address\b|mailing address|home address"),
    ("zip", r"\bzip\b|postal code|zip code|postcode"),
    ("state", r"^state\b|\bstate\b|province"),
    ("city", r"^city\b|\bcity\b|town"),
    ("country", r"\bcountry\b"),
    ("school", r"school|university|college"),
    ("degree", r"\bdegree\b"),
    ("discipline", r"discipline|field of study|\bmajor\b"),
    ("graduation", r"graduation"),
    ("location", r"current location|where are you (?:located|based)|^location"),
    ("full_name", r"full name|^name\b|legal name"),
]

STATE_ABBREVIATIONS = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO",
    "connecticut": "CT", "delaware": "DE", "district of columbia": "DC", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD", "massachusetts": "MA",
    "michigan": "MI", "minnesota": "MN", "mississippi": "MS", "missouri": "MO", "montana": "MT",
    "nebraska": "NE", "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM",
    "new york": "NY", "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
    "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}
STATE_NAMES = {abbr.lower(): name.title() for name, abbr in STATE_ABBREVIATIONS.items()}

DECLINE = re.compile(r"decline|don.?t wish|do not wish|prefer not|not to (?:answer|disclose|self)", re.I)


# Short-label-only keys: "Please state why you..." is a question, not the State field.
SHORT_LABEL_ONLY = {"state", "city", "country", "zip", "address_line1", "address_line2", "employer", "job_title"}


def classify(label: str) -> str | None:
    text = " ".join((label or "").lower().split())
    for key, pattern in RULES:
        if re.search(pattern, text):
            if key in SHORT_LABEL_ONLY and len(text.replace("*", "").strip()) > 40:
                continue
            return key
    return None


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", (text or "").lower().replace("'", "")).split())


def choose_option(options: list[dict], wanted: str) -> dict | None:
    """Pick the option that matches an answer like "Yes", "No", or "Decline to answer"."""
    wanted_n = _norm(wanted)
    real = [o for o in options if _norm(o.get("text")) not in ("", "select", "select one", "please select")]
    if not real or not wanted_n:
        return None
    if DECLINE.search(wanted):
        return next((o for o in real if DECLINE.search(o["text"])), None)
    alternates = {wanted_n}
    if wanted_n in STATE_ABBREVIATIONS:               # "California" also matches "CA"
        alternates.add(STATE_ABBREVIATIONS[wanted_n].lower())
    if wanted_n in STATE_NAMES:                       # "CA" also matches "California"
        alternates.add(STATE_NAMES[wanted_n].lower())
    for o in real:                                    # exact (or state name/abbreviation)
        if _norm(o["text"]) in alternates:
            return o
    if wanted_n in ("yes", "no"):                     # "Yes, I am authorized..." style options
        starts = [o for o in real if _norm(o["text"]).split(" ")[:1] == [wanted_n]]
        return starts[0] if len(starts) == 1 else None
    matches = [o for o in real if wanted_n in _norm(o["text"]) or _norm(o["text"]) in wanted_n]
    if len(matches) == 1:
        return matches[0]
    # Same words, different punctuation: "University of California, Berkeley" vs "... - Berkeley"
    wanted_words = set(wanted_n.split())
    scored = sorted(((len(wanted_words & set(_norm(o["text"]).split())) / len(wanted_words), o) for o in real),
                    key=lambda pair: pair[0], reverse=True)
    if scored and scored[0][0] >= 0.99 and (len(scored) == 1 or scored[1][0] < scored[0][0]):
        return scored[0][1]
    # "United States" vs "United States of America" vs "United States Minor Outlying Islands":
    starting = sorted((o for o in matches if _norm(o["text"]).startswith(wanted_n)), key=lambda o: len(o["text"]))
    if starting and _norm(starting[0]["text"]) in (wanted_n, wanted_n + " of america"):
        return starting[0]
    return None


def plan(fields: list[Field], answers: dict, files: dict) -> tuple[list[Action], list[str]]:
    """Decide what to fill. Returns (actions, labels that need YOU)."""
    actions, needs_you, used = [], [], set()
    for f in fields:
        key = classify(f.label)
        if f.kind == "file" and key not in ("resume_file", "cover_letter_file"):
            key = classify(f.hint.replace("_", " ").replace("-", " ")) or key
        if f.kind == "file":
            path = files.get("resume") if key == "resume_file" else files.get("cover_letter") if key == "cover_letter_file" else None
            if path and key not in used:
                actions.append(Action(f.id, "upload", str(path), key))
                used.add(key)
            elif f.required:
                needs_you.append(f.label or "File upload")
            continue
        value = answers.get(key) if key else None
        alternates = []
        if f.kind in ("select", "radio", "combobox"):
            if key == "degree":
                value = answers.get("degree_level") or value
                alternates = ["Bachelor's", "Bachelors", "Bachelor of Arts", "Bachelor of Science"] \
                    if value == "Bachelor's Degree" else []
            elif key in ("city", "location"):
                value, alternates = answers.get("city_full") or value, [answers.get("city"), answers.get("location")]
            elif key == "how_heard":
                alternates = ["Company Website", "Company Careers Page", "Careers Page", "Website"]
            elif key in ("job_start_year", "job_end_year"):
                pass
        remembered = False
        if not value and rememberable(f.label, key) and f.kind != "file":
            value = (answers.get("_memory") or {}).get(question_key(f.label, answers.get("_company", "")))
            remembered = bool(value)
        if NEVER_REMEMBER.search(f.label or ""):
            value = None                                   # agreements: always you
        if key in ("resume_file", "cover_letter_file") or not value:
            if f.required or key in ("salary", "sponsorship", "work_authorized") or key in DEMOGRAPHIC_KEYS:
                needs_you.append(f.label or "Unlabeled field")
            continue
        if f.kind in ("select", "radio"):
            option = next((o for o in (choose_option(f.options, w) for w in [value, *alternates] if w) if o), None)
            if option:
                actions.append(Action(option["id"] if f.kind == "radio" else f.id,
                                      "check" if f.kind == "radio" else "select",
                                      option["text"], "remembered" if remembered else key))
            else:
                needs_you.append(f.label)
        elif f.kind == "checkbox":
            if f.required:
                needs_you.append(f.label)
        elif f.kind == "combobox":
            actions.append(Action(f.id, "combobox", value, "remembered" if remembered else key,
                                  [a for a in alternates if a]))
        else:
            actions.append(Action(f.id, "fill", value, "remembered" if remembered else key))
    return actions, list(dict.fromkeys(needs_you))


# ---------------------------------------------------------------------------
# 2b. Remembering your answers to repeated questions
# ---------------------------------------------------------------------------

# Agreements and acknowledgments are never remembered or auto-filled: you read them each time.
NEVER_REMEMBER = re.compile(r"confirm|acknowledg|i understand|i agree|agree to|certify|consent|attest|"
                            r"signature|terms|privacy notice|arbitration", re.I)
MEMORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS answer_memory (
    question   TEXT PRIMARY KEY,
    answer     TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def question_key(label: str, company: str = "") -> str:
    """'Have you worked at Coinbase?*' -> 'have you worked at {company}' (works for every company)."""
    text = (label or "").replace("*", " ")
    if company:
        text = re.sub(re.escape(company), "{company}", text, flags=re.I)
    return " ".join(re.sub(r"[^a-z0-9{} ]", " ", text.lower()).split())


def rememberable(label: str, key: str | None) -> bool:
    if NEVER_REMEMBER.search(label or ""):
        return False
    return key in (None, "how_heard", "discipline")      # profile answers already cover the rest


def load_memory(conn) -> dict[str, str]:
    conn.executescript(MEMORY_SCHEMA)
    return {q: a for q, a in conn.execute("SELECT question, answer FROM answer_memory")}


def save_memory(conn, answered: list[dict], company: str) -> int:
    """Store your answers to questions the profile doesn't cover. Returns how many were saved."""
    conn.executescript(MEMORY_SCHEMA)
    saved = 0
    with conn:
        for item in answered:
            label, value = item.get("label") or "", (item.get("value") or "").strip()
            if not value or len(value) > 200 or value.lower().startswith("select"):
                continue
            if not rememberable(label, classify(label)):
                continue
            conn.execute("INSERT OR REPLACE INTO answer_memory (question, answer, updated_at) "
                         "VALUES (?, ?, CURRENT_TIMESTAMP)", (question_key(label, company), value))
            saved += 1
    return saved


# ---------------------------------------------------------------------------
# 2c. Asking Claude about dropdowns the rules couldn't answer
# ---------------------------------------------------------------------------
# After the rules and your remembered answers, some dropdowns are still empty: "How did you hear
# about us?" with company-specific choices, a "Discipline" list without your exact major, and so on.
# OpenCareer reads the choices and asks Claude which one is true for you. Claude only sees the
# question, its choices, and your education, work and skills. Its picks get a BLUE outline: check them.

# These questions are yours alone; they're never sent to Claude.
NOT_FOR_AI = re.compile(
    r"criminal|convict|felony|misdemeanor|arrest|terminat|fired|discharg|lawsuit|non.?compete|"
    r"background check|drug|government official|relative|related to|conflict of interest|clearance|"
    r"citizen|visa|sponsor|authori[sz]ed|immigration|salary|compensation|\bpay\b|"
    r"gender|race|ethnic|hispanic|latin|veteran|disab|sexual orientation|pronoun|transgender", re.I)
NOT_FOR_AI_PROFILE = ("name", "email", "phone", "links", "address", "demographics")
AI_MAX_QUESTIONS = 25
AI_MAX_OPTIONS = 60
PLACEHOLDER = re.compile(r"^\s*(?:select\b|choose\b|please select|-+\s*$|\u2014)|^\s*$", re.I)

AI_SYSTEM = """You help fill in a job application for the applicant described in FACTS. GOALS lists the places they want to work, most wanted first. ALREADY_ANSWERED lists your earlier answers on this form: stay consistent with them.
For each question, choose ONE option, copying its text exactly, or null.

There are two kinds of answers:
- "fact": the question asks something about the applicant (school, major, experience, how they heard about the job, how they use tools). Answer only from FACTS, and from SAVED (the applicant's own answer to that question) when it is given; then choose the option that means the same as SAVED. Never guess or invent. For a field of study or job category, choose an option only if it accurately describes the applicant; if none does and an option like "Other" exists, choose that.
- "suggestion": the question asks what the applicant prefers (team, focus area, opportunity type, office). Choose the option that best fits the skills and experience in FACTS and the places in GOALS, and give the reason.

Always answer null for: availability or start dates, graduation year or eligibility, anything legal, agreements and acknowledgments, salary.
Reply with JSON only: {"answers": [{"id": "...", "choice": "<exact option text, or null>", "kind": "fact" or "suggestion", "why": "<under 15 words>"}]}"""


def facts_for_ai(profile: dict) -> dict:
    """Only what Claude needs for application questions: education, work, projects, skills.

    No name, email, phone, links, address, demographics, or saved application answers."""
    from opencareer import applicant

    return {key: value for key, value in applicant.profile_for_ai(profile).items()
            if key not in NOT_FOR_AI_PROFILE}


def saved_answer(label: str, answers: dict) -> str | None:
    """Your own answer to this question, from the profile or memory, if there is one."""
    key = classify(label)
    if key in DEMOGRAPHIC_KEYS or key in STANDARD_KEYS or key in ("salary", "sponsorship", "work_authorized"):
        return None
    value = (answers.get("degree_level") if key == "degree" else
             answers.get("city_full") if key in ("city", "location") else
             answers.get(key) if key else None)
    value = value or (answers.get("_memory") or {}).get(question_key(label, answers.get("_company", "")))
    return value if isinstance(value, str) and value.strip() else None


def ai_candidates(fields: list[Field], done_ids: list[str]) -> list[Field]:
    """Choice questions that are still empty and that Claude is allowed to answer."""
    done = set(done_ids)
    out = []
    for f in fields:
        if f.kind not in ("select", "radio", "combobox") or f.id in done:
            continue
        if f.kind == "radio" and any(o["id"] in done for o in f.options):
            continue
        label = (f.label or "").strip()
        key = classify(label)
        if not label or NEVER_REMEMBER.search(label) or NOT_FOR_AI.search(label):
            continue
        if key in DEMOGRAPHIC_KEYS or key in STANDARD_KEYS or key in ("salary", "sponsorship", "work_authorized"):
            continue
        out.append(f)
    return out[:AI_MAX_QUESTIONS]


def probe_words(saved: str | None) -> list[str]:
    """What to type into a searchable list to see its choices: your answer, then its main words."""
    if not saved:
        return []
    common = {"university", "college", "bachelor", "bachelors", "degree", "with", "from", "the", "and"}
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z']+", saved) if len(w) >= 4 and w.lower() not in common]
    return list(dict.fromkeys([*_search_phrases([saved]), *words]))[:4]


def parse_ai_choices(text: str, questions: list[dict]) -> list[dict]:
    """Keep only answers that are exactly one of the listed choices. Anything else is ignored."""
    try:
        data = json.loads(text[text.index("{"): text.rindex("}") + 1])
    except ValueError:
        return []
    by_id = {q["id"]: q for q in questions}
    picks = []
    for item in (data.get("answers") or []) if isinstance(data, dict) else []:
        if not isinstance(item, dict):
            continue
        question, choice = by_id.get(str(item.get("id"))), item.get("choice")
        if not question or not isinstance(choice, str):
            continue
        match = next((o for o in question["options"] if _norm(o) == _norm(choice)), None)
        if match:
            kind = "suggestion" if item.get("kind") == "suggestion" else "fact"
            picks.append({"id": question["id"], "label": question["question"], "choice": match,
                          "kind": kind, "why": str(item.get("why") or "")[:120]})
    return picks


def ask_ai_about(questions: list[dict], facts: dict, job_title: str, company: str, *, client=None,
                 goals: list[str] | None = None, earlier: list[dict] | None = None) -> list[dict]:
    from opencareer import ai

    payload = {"job": {"title": job_title, "company": company}, "facts": facts, "goals": goals or [],
               "already_answered": earlier or [],
               "questions": [{"id": q["id"], "question": q["question"], "options": q["options"],
                              **({"saved": q["saved"]} if q.get("saved") else {})} for q in questions]}
    text = ai.ask_claude(AI_SYSTEM, json.dumps(payload, indent=1, default=str), client=client, max_tokens=1500)
    return parse_ai_choices(text, questions)


def ai_chooser(profile: dict, job, client=None, goals: list[str] | None = None):
    """A function fill_page calls with the leftover questions, or None if there's no API key
    or you turned this off (OPENCAREER_AI_PICKS=0 in .env). goals: your target location names."""
    if os.environ.get("OPENCAREER_AI_PICKS", "1").strip() == "0":
        return None
    if client is None and not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    from opencareer import ai

    facts = facts_for_ai(profile)

    def choose(questions: list[dict], earlier: list[dict] | None = None) -> list[dict]:
        try:
            return ask_ai_about(questions, facts, getattr(job, "title", ""), getattr(job, "company", ""),
                                client=client, goals=goals, earlier=earlier)
        except ai.AIError as error:
            print("[ai] skipped:", error, flush=True)
            return []

    return choose


# ---------------------------------------------------------------------------
# 3. Which page to open
# ---------------------------------------------------------------------------

def candidate_urls(job, board_key: str | None) -> list[str]:
    """Pages to try, best first. Greenhouse's embed form skips company careers pages."""
    from opencareer.sources import registry

    url, _ = apply_url_for(job, board_key)
    urls = []
    if job.source == "greenhouse" and board_key and registry.split_key(board_key)[0] == "greenhouse":
        board = registry.split_key(board_key)[1]
        urls.append(f"https://boards.greenhouse.io/embed/job_app?for={board}&token={job.source_id}")
    urls.append(url)
    if job.url not in urls:
        urls.append(job.url)
    return urls


def apply_url_for(job, board_key: str | None) -> tuple[str, bool]:
    """(url to open, whether we can fill it). Government jobs apply inside USAJOBS."""
    from opencareer.sources import registry

    if job.source == "usajobs":
        return job.url, False
    if job.source == "lever":
        return (job.url if job.url.rstrip("/").endswith("/apply") else job.url.rstrip("/") + "/apply"), True
    if job.source == "ashby":
        return (job.url if job.url.rstrip("/").endswith("/application") else job.url.rstrip("/") + "/application"), True
    if board_key and registry.split_key(board_key)[0] == "greenhouse":
        board = registry.split_key(board_key)[1]
        return f"https://job-boards.greenhouse.io/{board}/jobs/{job.source_id}", True
    return job.url, True


# ---------------------------------------------------------------------------
# 4. The browser part
# ---------------------------------------------------------------------------

EXTRACT_JS = r"""
() => {
  const out = []; let n = 0;
  const text = el => (el ? (el.innerText || el.textContent || "").replace(/\s+/g, " ").trim() : "");
  const labelFor = el => {
    if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) return text(l); }
    const wrap = el.closest("label"); if (wrap) return text(wrap);
    const by = el.getAttribute("aria-labelledby");
    if (by) return by.split(/\s+/).map(id => text(document.getElementById(id))).join(" ");
    if (el.getAttribute("aria-label")) return el.getAttribute("aria-label");
    const box = el.closest(".field, .application-question, .form-group, [class*='question'], [class*='field'], fieldset");
    if (box) { const l = box.querySelector("label, legend, .text, [class*='label']"); if (l) return text(l); }
    return el.placeholder || el.name || "";
  };
  const isRequired = (el, label) => el.required || el.getAttribute("aria-required") === "true" || /\*/.test(label);
  const seenRadio = {};
  document.querySelectorAll("input, textarea, select, [role='combobox']").forEach(el => {
    const type = (el.getAttribute("type") || el.tagName).toLowerCase();
    if (["hidden", "submit", "button", "reset", "image", "search"].includes(type)) return;
    if (el.disabled) return;
    if (el.getAttribute("aria-hidden") === "true") return;          // hidden helper inputs (React dropdowns)
    if (type !== "file" && el.tabIndex === -1 && getComputedStyle(el).opacity === "0") return;
    if (type !== "file" && el.offsetParent === null && el.getAttribute("role") !== "combobox") return;
    if (type === "radio") {
      const key = el.name || ("r" + n);
      const id = "oc" + (n++); el.setAttribute("data-oc-id", id);
      const optLabel = labelFor(el);
      if (!seenRadio[key]) {
        const box = el.closest("fieldset, .field, .application-question, [role='radiogroup'], [class*='question']");
        const q = box ? text(box.querySelector("legend, label, .text, [class*='label']")) : key;
        seenRadio[key] = {id: "grp-" + key, kind: "radio", label: q, required: el.required || /\*/.test(q),
                          options: [], hint: key};
        out.push(seenRadio[key]);
      }
      seenRadio[key].options.push({id, text: optLabel});
      return;
    }
    const id = "oc" + (n++); el.setAttribute("data-oc-id", id);
    const label = labelFor(el);
    let kind = el.getAttribute("role") === "combobox" && el.tagName !== "SELECT" ? "combobox"
             : el.tagName === "SELECT" ? "select" : el.tagName === "TEXTAREA" ? "textarea" : type;
    const options = el.tagName === "SELECT" ? [...el.options].map(o => ({id: o.value, text: o.text.trim()})) : [];
    out.push({id, kind, label, required: isRequired(el, label), options,
              hint: [el.id, el.name, el.getAttribute("autocomplete")].filter(Boolean).join(" ")});
  });
  return out;
}
"""

HIGHLIGHT_JS = r"""
([filledIds, aiPicks, needs, total, remembered]) => {
  const clean = t => String(t).replace(/[<>&]/g, "");
  const mark = (id, style) => {
    const el = document.querySelector(`[data-oc-id="${id}"]`);
    if (el) el.style.outline = style;
  };
  const aiIds = aiPicks.map(p => p.id);
  filledIds.forEach(id => mark(id, "2px solid #1a7f37"));
  aiPicks.forEach(p => mark(p.id, p.kind === "suggestion" ? "3px dashed #0969da" : "3px solid #0969da"));
  document.querySelectorAll("[data-oc-id]").forEach(el => {
    const id = el.getAttribute("data-oc-id");
    if (filledIds.includes(id) || aiIds.includes(id)) return;
    const required = el.required || el.getAttribute("aria-required") === "true";
    if (required && !el.value) { el.style.outline = "3px solid #d4a72c"; el.style.background = "#fff8c5"; }
  });
  const box = document.createElement("div");
  box.id = "opencareer-panel";
  box.style.cssText = "position:fixed;top:12px;right:12px;z-index:2147483647;max-width:380px;max-height:80vh;" +
    "overflow:auto;background:#fff;border:2px solid #0969da;border-radius:10px;padding:12px 14px;" +
    "font:13px/1.4 -apple-system,sans-serif;box-shadow:0 6px 24px rgba(0,0,0,.2);color:#1f2328";
  const ul = items => "<ul style='margin:6px 0 0;padding-left:18px'>" + items.map(t => "<li>" + t + "</li>").join("") + "</ul>";
  const line = p => clean(p.label) + " &rarr; <b>" + clean(p.choice) + "</b>" +
    "<br><span style='color:#59636e'>" + (p.seen ? "Read " + p.seen + " choices. " : "") + clean(p.why || "") + "</span>";
  const facts = aiPicks.filter(p => p.kind !== "suggestion"), ideas = aiPicks.filter(p => p.kind === "suggestion");
  box.innerHTML = "<b>OpenCareer filled " + (filledIds.length + aiPicks.length) + " of " + total + " fields</b>" +
    " (green outline" + (remembered ? ", " + remembered + " from your past answers" : "") + ")." +
    (facts.length ? "<div style='margin-top:6px'><b>Claude answered from your profile</b> (solid blue): check each one" +
      ul(facts.map(line)) + "</div>" : "") +
    (ideas.length ? "<div style='margin-top:6px'><b>Claude suggested</b> (dashed blue): your call, change any you like" +
      ul(ideas.map(line)) + "</div>" : "") +
    (needs.length ? "<div style='margin-top:6px'><b>Needs you</b> (yellow):" + ul(needs.map(clean)) + "</div>" : "") +
    "<div style='margin-top:8px'>Check every field, then <b>click Submit yourself</b>. " +
    "OpenCareer never submits for you.</div><button style='margin-top:8px' onclick='this.parentNode.remove()'>Hide</button>";
  document.body.appendChild(box);
}
"""

READ_ANSWERS_JS = r"""
() => [...document.querySelectorAll("[data-oc-id]")].map(el => {
  const id = el.getAttribute("data-oc-id");
  let value = "";
  if (el.tagName === "SELECT") value = el.options[el.selectedIndex]?.text || "";
  else if (el.type === "radio") { if (!el.checked) return null; value = (el.closest("label")?.innerText || el.value); }
  else if (el.type === "checkbox" || el.type === "file") return null;
  else if (el.getAttribute("role") === "combobox") {
    const box = el.closest('[class*="control"], [class*="container"], .field') || el.parentElement;
    value = box?.querySelector('[class*="single-value"], [class*="singleValue"]')?.innerText || el.value || "";
  } else value = el.value || "";
  const field = el.closest(".field, fieldset, [class*='question'], [class*='field']");
  const label = el.type === "radio"
    ? (field?.querySelector("legend, label")?.innerText || "")
    : (el.labels?.[0]?.innerText || (el.getAttribute("aria-labelledby") ?
        document.getElementById(el.getAttribute("aria-labelledby").split(" ")[0])?.innerText : "") ||
       field?.querySelector("label")?.innerText || "");
  return {label: (label || "").replace(/\s+/g, " ").trim(), value: (value || "").trim()};
}).filter(Boolean)
"""


# While OpenCareer fills a form, the form cannot be submitted, by anything. Removed when done,
# so YOUR click on Submit works normally afterwards.
LOCK_JS = r"""
() => {
  window.__ocBlock = e => { e.preventDefault(); e.stopImmediatePropagation(); };
  document.addEventListener("submit", window.__ocBlock, true);
}
"""
UNLOCK_JS = r"""
() => { if (window.__ocBlock) document.removeEventListener("submit", window.__ocBlock, true); }
"""

CONFIRMATION = re.compile(r"thank you for (?:applying|your application)|application (?:has been )?(?:submitted|received)"
                          r"|we.?ve received your application|successfully submitted", re.I)


@dataclass
class Status:
    state: str                    # preparing, filled, submitted, closed, error
    message: str = ""
    apply_url: str = ""
    filled: list = field(default_factory=list)
    needs_you: list = field(default_factory=list)
    ai_picked: list = field(default_factory=list)   # [{"label", "choice", "why"}]: check these


def write_status(folder: Path, status: Status) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "apply_status.json").write_text(json.dumps(asdict(status), indent=2))


def read_status(folder: Path) -> dict | None:
    path = Path(folder) / "apply_status.json"
    try:
        return json.loads(path.read_text()) if path.exists() else None
    except json.JSONDecodeError:
        return None


def _launch(playwright, headless: bool):
    try:
        return playwright.chromium.launch(channel="chrome", headless=headless)   # your installed Chrome
    except Exception:
        return playwright.chromium.launch(headless=headless)                     # Playwright's Chromium


def _apply_action(frame, action: Action) -> bool:
    el = frame.locator(f'[data-oc-id="{action.field_id}"]').first
    if el.count() == 0:
        return False
    if (el.get_attribute("type") or "").lower() in ("submit", "button"):
        return False                                                  # safety: never touch buttons
    try:
        if action.how == "fill":
            el.fill(action.value)
        elif action.how == "select":
            el.select_option(label=action.value)
        elif action.how == "check":
            el.check()
        elif action.how == "upload":
            el.set_input_files(action.value)
        elif action.how == "combobox":
            return _pick_from_dropdown(frame, el, [action.value, *action.alternates])
        return True
    except Exception:
        return False


MIN_FIELDS = 3          # fewer than this = we haven't found the application form yet

CLICK_APPLY_JS = r"""
() => {
  // Only a button/link whose whole text is "Apply", "Apply now", or "Apply for this job",
  // never a submit button and never anything inside a form. Opening a form sends nothing.
  const ok = /^\s*apply(?: now| for this (?:job|position|role))?\s*$/i;
  const el = [...document.querySelectorAll("a, button")].find(e =>
    ok.test(e.innerText || "") && e.type !== "submit" && !e.closest("form") && e.offsetParent !== null);
  if (!el) return false;
  el.click();
  return true;
}
"""

WAITING_PANEL_JS = r"""
() => {
  if (document.getElementById("opencareer-panel")) return;
  const box = document.createElement("div");
  box.id = "opencareer-panel";
  box.style.cssText = "position:fixed;top:12px;right:12px;z-index:2147483647;max-width:340px;background:#fff;" +
    "border:2px solid #d4a72c;border-radius:10px;padding:12px 14px;font:13px/1.4 -apple-system,sans-serif;" +
    "box-shadow:0 6px 24px rgba(0,0,0,.2);color:#1f2328";
  box.innerHTML = "<b>OpenCareer is waiting for the form.</b><div style='margin-top:6px'>Click this site's " +
    "<b>Apply</b> button (accept or reject cookies if asked). As soon as the application form appears, " +
    "OpenCareer fills it in.</div>";
  document.body.appendChild(box);
}
"""


def find_form(page):
    """The frame (page or embedded iframe) with the most form fields, and those fields."""
    best_frame, best_fields = page.main_frame, []
    for frame in page.frames:
        try:
            fields = frame.evaluate(EXTRACT_JS)
        except Exception:
            continue
        if len(fields) > len(best_fields):
            best_frame, best_fields = frame, fields
    return best_frame, [Field(**f) for f in best_fields]


def click_apply_button(page) -> bool:
    try:
        clicked = page.main_frame.evaluate(CLICK_APPLY_JS)
    except Exception:
        return False
    if clicked:
        page.wait_for_timeout(2500)
    return bool(clicked)


OPTION_SELECTORS = ['[role="option"]', '[id*="-option-"]', '[class*="select__option"]',
                    '[class*="option"]:not([class*="options"]):not([class*="optional"])']


def _visible_options(frame):
    """The dropdown's options, whichever way the site builds them."""
    for selector in OPTION_SELECTORS:
        found = frame.locator(selector)
        visible = [found.nth(i) for i in range(min(found.count(), 80)) if found.nth(i).is_visible()]
        if visible:
            return visible
    return []


DROPDOWN_WAIT_MS = 4000          # searchable lists (schools, disciplines) can take a few seconds
LOADING = re.compile(r"loading|searching|no options|type to search", re.I)


def _search_phrases(candidates: list[str]) -> list[str]:
    """What to type: each answer, then its distinctive part ("University of California, Berkeley"
    -> also "Berkeley"), because some site searches only match part of a name."""
    phrases = list(candidates)
    for c in candidates:
        for part in re.split(r"\s*[,\-\u2013]\s*", c)[1:]:
            if len(part) >= 4 and part not in phrases:
                phrases.append(part)
    return phrases


READ_WAIT_MS = 2500


def _wait_for_options(frame, wait_ms: int):
    """The dropdown's options once they've loaded (ignores "Loading..."). Returns (elements, texts)."""
    waited = 0
    while True:
        options = _visible_options(frame)
        texts = [o.inner_text().strip() for o in options]
        if texts and not all(LOADING.search(t) for t in texts):
            return options, texts
        if waited >= wait_ms:
            return [], []
        frame.wait_for_timeout(250)
        waited += 250


def read_options(frame, el, probes: list[str], keep_open: bool = False) -> list[str]:
    """Open a dropdown and collect its choices, the way you'd look before picking.

    A short list shows everything when opened. For a long or searchable list (schools, majors),
    also type a few words from your answer, plus "Other", and list those results first.
    keep_open leaves a short list showing (so you can watch Claude decide); search text is always cleared."""
    opened: list[str] = []
    found: list[str] = []
    try:
        el.click()
        _, opened = _wait_for_options(frame, 1500)
    except Exception:
        pass
    opened = [t for t in opened if t and not LOADING.search(t)]
    if not (0 < len(opened) < 50):
        for typed in [*probes, "Other"] if _editable(el) else []:
            try:
                el.click()
                el.fill("")
                el.press_sequentially(typed[:40], delay=15)
            except Exception:
                break
            _, texts = _wait_for_options(frame, READ_WAIT_MS)
            found += [t for t in texts if t and t not in found and not LOADING.search(t)]
    if found or not keep_open:
        _close_menu(el)
    return list(dict.fromkeys([*found, *opened]))[:AI_MAX_OPTIONS]


def _ask_ai(frame, fields: list[Field], done_ids: list[str], answers: dict, choose) -> list[dict]:
    """One question at a time: open it, read every choice, let Claude decide, answer, then the next.

    Claude also sees its earlier answers on this form, so they stay consistent. Runs while locked."""
    applied: list[dict] = []
    for f in ai_candidates(fields, done_ids):
        saved = saved_answer(f.label, answers)
        el = frame.locator(f'[data-oc-id="{f.id}"]').first if f.kind == "combobox" else None
        if f.kind == "combobox":
            options = read_options(frame, el, probe_words(saved), keep_open=True) if el.count() else []
        else:
            options = [o["text"] for o in f.options if o.get("text") and not PLACEHOLDER.search(o["text"])]
        if not options:
            continue
        question = {"id": f.id, "question": f.label, "options": options[:AI_MAX_OPTIONS],
                    **({"saved": saved} if saved else {})}
        print(f"[ai] reading '{f.label}': {len(options)} choices: {options[:20]}", flush=True)
        earlier = [{"question": p["label"], "answer": p["choice"]} for p in applied]
        pick = next(iter(choose([question], earlier) or []), None)
        if not pick or pick.get("id") != f.id:
            if el is not None:
                _close_menu(el)
            print(f"[ai] '{f.label}': left for you", flush=True)
            continue
        if f.kind == "radio":
            option = next((o for o in f.options if o["text"] == pick["choice"]), None)
            action = Action(option["id"], "check", pick["choice"], "claude") if option else None
        else:
            action = Action(f.id, "select" if f.kind == "select" else "combobox", pick["choice"], "claude")
        done = False
        if action and f.kind == "combobox":
            try:                                            # the list is still open: click the answer
                shown, texts = _wait_for_options(frame, 0)
                done = bool(texts) and _click_matching(shown, texts, [pick["choice"]])
            except Exception:
                done = False
            if not done:
                _close_menu(el)
        if action and (done or _apply_action(frame, action)):
            applied.append({**pick, "field_id": action.field_id, "seen": len(options)})
            print(f"[ai] {f.label} -> {pick['choice']} ({pick.get('why', '')})", flush=True)
    return applied


LOOK_FIRST_MS = 1000


def _click_matching(options, texts, wanted: list[str]) -> bool:
    listed = [{"id": str(i), "text": t} for i, t in enumerate(texts)]
    for answer in wanted:
        pick = choose_option(listed, answer)
        if pick:
            options[int(pick["id"])].click()
            return True
    return False


def _pick_from_dropdown(frame, el, candidates: list[str]) -> bool:
    """Pick your answer in a dropdown the way you would.

    1. Open it and look: short lists (Yes/No, Veteran Status...) show every choice right away.
    2. If your answer isn't showing, type to search (schools, majors), waiting a few seconds for
       lists that search a database as you type.
    3. If the site's options can't be clicked, check the option the dropdown has highlighted
       (aria-activedescendant); only if it's the right answer, confirm it with Enter. This only
       happens inside fill_page, while the form is locked against submission.
    """
    wanted = [c for c in candidates if c]
    texts: list[str] = []
    try:
        el.click()
        options, texts = _wait_for_options(frame, LOOK_FIRST_MS)
        if texts and _click_matching(options, texts, wanted):
            return True
    except Exception:
        pass
    if not _editable(el):                                  # a dropdown you can't type in: nothing more to try
        _close_menu(el)
        print(f"[dropdown] couldn't pick {wanted[:1]}; options seen:", texts[:15], flush=True)
        return False
    for typed in _search_phrases(wanted):
        el.click()
        el.fill("")
        el.press_sequentially(typed[:40], delay=15)
        waited = 0
        while waited <= DROPDOWN_WAIT_MS:
            options, texts = _wait_for_options(frame, 0)
            if texts and _click_matching(options, texts, wanted):
                return True
            frame.wait_for_timeout(250)
            waited += 250
        active = el.get_attribute("aria-activedescendant")
        if active:
            highlighted = frame.locator(f'[id="{active}"]')
            if highlighted.count() and any(
                    choose_option([{"id": "0", "text": highlighted.first.inner_text().strip()}], w) for w in wanted):
                el.press("Enter")                          # form is locked: Enter can't submit
                return True
    print(f"[dropdown] couldn't pick {wanted[:1]}; options seen:",
          [o.inner_text().strip() for o in _visible_options(frame)][:15], flush=True)
    _close_menu(el)
    return False


def _editable(el) -> bool:
    try:
        return el.is_editable()
    except Exception:
        return False


def _close_menu(el) -> None:
    try:
        if _editable(el):
            el.fill("")
        el.press("Escape")                                 # close the menu; Escape never submits
    except Exception:
        pass


def fill_page(page, answers: dict, files: dict, choose=None) -> Status:
    frame, fields = find_form(page)
    if len(fields) < MIN_FIELDS:
        try:
            page.main_frame.evaluate(WAITING_PANEL_JS)
        except Exception:
            pass
        return Status("waiting", "Couldn't find the application form on this page yet. Click the site's "
                      "Apply button: OpenCareer fills the form as soon as it appears.", page.url, [],
                      ["Click the site's Apply button to open the form"])
    actions, needs_you = plan(fields, answers, files)
    filled, done_ids, ai_picks = [], [], []
    frame.evaluate(LOCK_JS)
    try:
        results = [(action, _apply_action(frame, action)) for action in actions]
        done_ids = [action.field_id for action, ok in results if ok]
        if choose:
            try:
                ai_picks = _ask_ai(frame, fields, done_ids, answers, choose)
            except Exception as error:                       # never let this break the rest of the fill
                print("[ai] skipped:", error, flush=True)
    finally:
        frame.evaluate(UNLOCK_JS)
    done_ids = []
    for action, ok in results:
        if ok:
            done_ids.append(action.field_id)
            filled.append(action.what)
        else:
            needs_you.append(next((f.label for f in fields if f.id == action.field_id), action.what))
    picked_labels = {p["label"] for p in ai_picks}
    needs_you = list(dict.fromkeys(label for label in needs_you
                                   if label and label != "Unlabeled field" and label not in picked_labels))
    filled += ["claude"] * len(ai_picks)
    try:
        page.main_frame.evaluate("() => document.getElementById('opencareer-panel')?.remove()")
    except Exception:
        pass
    shown = [{"id": p["field_id"], "label": p["label"], "choice": p["choice"], "seen": p.get("seen", 0),
              "kind": p.get("kind", "fact"), "why": p.get("why", "")} for p in ai_picks]
    frame.evaluate(HIGHLIGHT_JS, [done_ids, shown, needs_you, len(fields), filled.count("remembered")])
    total = len(done_ids) + len(ai_picks)
    return Status("filled", f"Filled {total} of {len(fields)} fields"
                  + (f" ({len(ai_picks)} chosen by Claude: check those)." if ai_picks else "."),
                  page.url, filled, needs_you,
                  [{"label": p["label"], "choice": p["choice"], "kind": p.get("kind", "fact"),
                    "why": p.get("why", "")} for p in ai_picks])


def run(job, board_key, profile: dict, draft, folder: Path, db_path: str | None,
        headless: bool = False, wait: bool = True) -> Status:
    """Make the PDFs, open the form, fill it, then wait while you review and submit."""
    from playwright.sync_api import sync_playwright

    from opencareer import documents

    url, fillable = apply_url_for(job, board_key)
    write_status(folder, Status("preparing", "Making PDFs and opening the application…", url))
    with sync_playwright() as p:
        browser = _launch(p, headless)
        try:
            printer = browser.new_page()
            stem = _file_stem(profile)                      # e.g. "Alex_Rivera"
            files = {
                "resume": documents.write_pdf(
                    printer, documents.document_html(draft.resume, "resume", job.company),
                    folder / f"{stem}_Resume.pdf"),
                "cover_letter": documents.write_pdf(
                    printer, documents.document_html(draft.cover_letter, "cover_letter", job.company),
                    folder / f"{stem}_Cover_Letter.pdf"),
            }
            printer.close()

            page = browser.new_page(viewport={"width": 1280, "height": 900})
            answers = answers_from_profile(profile, job.company)
            answers["_company"] = job.company
            goals = []
            if db_path:
                from opencareer import locations, storage
                memory_conn = storage.connect(db_path)
                answers["_memory"] = load_memory(memory_conn)
                goals = [target.name for target in locations.list_targets(memory_conn)]
                memory_conn.close()
            choose = ai_chooser(profile, job, goals=goals)
            if fillable:
                status = None
                for candidate in candidate_urls(job, board_key):
                    try:
                        page.goto(candidate, wait_until="domcontentloaded", timeout=45000)
                    except Exception:
                        continue
                    page.wait_for_timeout(2500)             # let the form finish loading
                    if len(find_form(page)[1]) < MIN_FIELDS and click_apply_button(page):
                        pass                                 # an "Apply" button revealed the form
                    if len(find_form(page)[1]) >= MIN_FIELDS:
                        status = fill_page(page, answers, files, choose)
                        break
                if status is None:                           # no form found anywhere: wait for you
                    if page.url not in (job.url,):
                        page.goto(job.url, wait_until="domcontentloaded", timeout=45000)
                    status = fill_page(page, answers, files, choose)
            else:
                status = Status("filled", "Government job: apply inside USAJOBS. Your PDFs are ready to upload.",
                                url, [], ["Sign in to USAJOBS and upload the PDFs from this folder"])
            page.screenshot(path=str(folder / "filled_form.png"), full_page=True)
            write_status(folder, status)

            if wait:
                status = _wait_for_you(page, job, folder, status, db_path, answers, files, choose)
            return status
        except Exception as error:
            status = Status("error", f"Couldn't prepare the application: {error}", url)
            write_status(folder, status)
            return status
        finally:
            try:
                browser.close()
            except Exception:
                pass


def _file_stem(profile: dict) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", profile.get("name") or "Resume").strip("_") or "Resume"


def _wait_for_you(page, job, folder: Path, status: Status, db_path: str | None,
                  answers: dict | None = None, files: dict | None = None, choose=None) -> Status:
    """Wait until you submit (we detect the confirmation page) or close the window.

    If the form wasn't found yet, keep looking: when you click the site's Apply button and
    the form appears, fill it then.
    """
    start_url = page.url
    snapshot = []
    while True:
        try:
            if page.is_closed():
                break
            try:
                frame, _ = find_form(page)
                snapshot = frame.evaluate(READ_ANSWERS_JS) or snapshot
            except Exception:
                pass
            if status.state == "waiting" and answers is not None and len(find_form(page)[1]) >= MIN_FIELDS:
                page.wait_for_timeout(1500)                  # let the whole form render
                status = fill_page(page, answers, files or {}, choose)
                page.screenshot(path=str(folder / "filled_form.png"), full_page=True)
                write_status(folder, status)
                start_url = page.url
                continue
            body = page.inner_text("body", timeout=2000)
            if (page.url != start_url and re.search(r"confirm|thank|success|submitted", page.url, re.I)) \
                    or CONFIRMATION.search(body):
                status = Status("submitted", "You submitted it. Marked as applied in your tracker.",
                                status.apply_url, status.filled, [], status.ai_picked)
                write_status(folder, status)
                if db_path:
                    from opencareer import storage, tracker

                    conn = storage.connect(db_path)
                    tracker.mark_applied(conn, job.url, note="Submitted via assisted apply")
                    learned = save_memory(conn, snapshot, job.company)
                    conn.close()
                    status.message += f" Remembered {learned} of your answers for next time."
                    write_status(folder, status)
                return status
            time.sleep(2)
        except Exception:
            break
    status = Status("closed", "Window closed. If you did submit, click \"I applied\".",
                    status.apply_url, status.filled, status.needs_you, status.ai_picked)
    write_status(folder, status)
    return status


# ---------------------------------------------------------------------------
# 5. Command line (the dashboard starts this in the background)
# ---------------------------------------------------------------------------

def main(argv=None) -> None:
    import yaml

    from opencareer import applicant, pipeline, storage, tailor
    from opencareer import boards as board_registry

    parser = argparse.ArgumentParser(description="Open and pre-fill a job application for your review.")
    parser.add_argument("--url", required=True, help="the job's URL as shown in the dashboard")
    parser.add_argument("--db", default="jobs.db")
    parser.add_argument("--profile", default="profile")
    parser.add_argument("--out", default="applications")
    args = parser.parse_args(argv)
    try:
        from dotenv import load_dotenv
        load_dotenv()                                         # ANTHROPIC_API_KEY, when run by hand
    except ImportError:
        pass

    conn = storage.connect(args.db)
    job = next((j for j in pipeline.select_jobs(storage.load_jobs(conn)) if j.url == args.url), None)
    if job is None:
        raise SystemExit("That job isn't in your saved list.")
    board = board_registry.board_for(conn, job, pipeline.BOARDS)
    conn.close()
    draft = tailor.load_draft(job, Path(args.out))
    if draft is None:
        raise SystemExit("Tailor this job first.")
    profile = yaml.safe_load(applicant.read_profile_text(Path(args.profile)) or "") or {}
    status = run(job, board, profile, draft, draft.folder, args.db)
    print(status.message)


if __name__ == "__main__":
    main()
