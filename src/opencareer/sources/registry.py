"""All job sources in one place.

A board key is "NAME" for Greenhouse (the original source) or "SOURCE:NAME" for the others,
e.g. "lever:plaid", "ashby:openai", "usajobs:cybersecurity".
"""
import re

from opencareer.sources import ashby, greenhouse, lever, usajobs

MODULES = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby, "usajobs": usajobs}
LABELS = {"greenhouse": "Greenhouse", "lever": "Lever", "ashby": "Ashby", "usajobs": "USAJOBS"}

LINK_PATTERNS = [
    ("greenhouse", re.compile(r"(?:job-)?boards(?:-api)?\.greenhouse\.io/(?:v1/boards/)?([A-Za-z0-9_-]+)", re.I)),
    ("lever", re.compile(r"jobs\.(?:eu\.)?lever\.co/([A-Za-z0-9_.-]+)", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)", re.I)),
]


def split_key(key: str) -> tuple[str, str]:
    source, _, name = key.partition(":")
    if name and source in MODULES:
        return source, name
    return "greenhouse", key


def make_key(source: str, name: str) -> str:
    return name if source == "greenhouse" else f"{source}:{name}"


def parse_board_input(text: str, source: str = "greenhouse") -> tuple[str, str]:
    """Accept a pasted job link OR a plain board name. Returns (source, name)."""
    text = text.strip()
    for link_source, pattern in LINK_PATTERNS:
        match = pattern.search(text)
        if match:
            return link_source, match.group(1).replace("%20", " ")
    return (source if source in MODULES else "greenhouse"), text


def fetch(key: str, company: str):
    source, name = split_key(key)
    return MODULES[source].fetch_jobs(name, company)   # looked up at call time (tests can swap it)
