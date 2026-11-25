"""From LinkedIn to company job boards, without ever visiting LinkedIn.

Paste a LinkedIn job alert email (or just company names). OpenCareer picks out the companies,
finds each one's own job board on Greenhouse, Lever or Ashby, and tracks it from then on.
Most jobs posted on LinkedIn are copies of these company boards, which have the real deadlines.
"""
import json
import re

from opencareer.sources import registry

SEARCH_ORDER = ("greenhouse", "lever", "ashby")
SUFFIXES = {"inc", "llc", "corp", "corporation", "co", "company", "ltd", "limited", "technologies",
            "technology", "labs", "hq", "group", "the", "plc", "gmbh"}
MAX_COMPANIES = 15
JOB_WORDS = re.compile(r"\b(engineer|developer|analyst|intern|internship|manager|specialist|technician|"
                       r"administrator|remote|hybrid|on-site|applicants?|ago|promoted|easy apply|actively)\b", re.I)
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

EXTRACT_SYSTEM = """From this text (usually a LinkedIn job alert email or copied job posts), list the names of the hiring companies.
Leave out LinkedIn itself, job titles, locations, and anything that isn't a company name. Keep each name exactly as written.
Reply with JSON only: {"companies": ["...", "..."]}"""


class FinderError(Exception):
    """Something to show on the page."""


def slug_candidates(company: str) -> list[str]:
    """Likely board names: 'Palo Alto Networks, Inc.' -> paloaltonetworks, palo-alto-networks."""
    words = re.findall(r"[a-z0-9]+", company.lower().replace("&", "").replace("'", ""))
    core = [w for w in words if w not in SUFFIXES] or words
    options = ["".join(core), "-".join(core), "".join(words)]
    return [o for o in dict.fromkeys(options) if o and len(o) <= 40][:3]


def find_board(company: str) -> tuple[str, str, int] | None:
    """(source, board name, open jobs) for the company's own job board, or None.

    A board only counts if it has open jobs, so a lucky guess at someone else's empty board
    can't add the wrong company."""
    for source in SEARCH_ORDER:
        for slug in slug_candidates(company):
            try:
                jobs = registry.MODULES[source].fetch_jobs(slug, company)
            except Exception:                     # not found, network trouble: try the next guess
                continue
            if jobs:
                return source, slug, len(jobs)
    return None


def board_links(text: str) -> list[tuple[str, str]]:
    """Greenhouse / Lever / Ashby job links pasted in, as (source, board name)."""
    found = []
    for url in re.findall(r"https?://\S+", text):
        source, name = registry.parse_board_input(url)
        if any(pattern.search(url) for _, pattern in registry.LINK_PATTERNS):
            found.append((source, name))
    return list(dict.fromkeys(found))


def _dedupe(names: list[str]) -> list[str]:
    seen, out = set(), []
    for name in names:
        name = name.strip(" •-\t*·")
        if name and name.lower() not in seen and name.lower() != "linkedin":
            seen.add(name.lower())
            out.append(name)
    return out[:MAX_COMPANIES]


def companies_from_text(text: str, *, client=None) -> list[str]:
    """Company names from what you pasted. A plain list is used as-is; a whole alert email is read
    by Claude (email addresses are removed first)."""
    text = re.sub(r"https?://\S+", " ", text or "").strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    if len(lines) <= MAX_COMPANIES and all(len(line) <= 50 for line in lines) \
            and not any(JOB_WORDS.search(line) for line in lines):
        return _dedupe(lines)
    from opencareer import ai

    try:
        reply = ai.ask_claude(EXTRACT_SYSTEM, EMAIL.sub("[email]", text)[:12000], client=client, max_tokens=800)
    except ai.AIError as error:
        raise FinderError(f"{error} Or paste just the company names, one per line.") from error
    try:
        data = json.loads(reply[reply.index("{"): reply.rindex("}") + 1])
    except ValueError:
        raise FinderError("Couldn't read the company names. Paste just the names, one per line.")
    names = [n for n in (data.get("companies") or []) if isinstance(n, str) and len(n) <= 80]
    return _dedupe(names)
