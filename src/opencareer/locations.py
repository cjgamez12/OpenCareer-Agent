"""Your target locations: jobs in places you care about get bonus points.

Each target has a name, a list of keywords to look for in a job's location,
and how many points a match is worth. Edit them from the dashboard.
"""
import re
import sqlite3
from dataclasses import dataclass

from opencareer.models import JobPosting

SCHEMA = """
CREATE TABLE IF NOT EXISTS location_targets (
    id       INTEGER PRIMARY KEY,
    name     TEXT NOT NULL,
    keywords TEXT NOT NULL,
    points   INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

DEFAULT_TARGETS = [
    ("California", [
        "california", ", ca", "bay area", "san francisco", "los angeles", "san jose",
        "san diego", "sacramento", "oakland", "palo alto",
        "menlo park", "mountain view", "sunnyvale", "santa clara", "irvine", "berkeley",
    ], 25),
    ("Remote (US)", [
        "remote - us", "remote - usa", "remote, us", "remote (us", "remote us",
        "remote - united states", "united states",
    ], 15),
]


@dataclass(frozen=True)
class Target:
    id: int
    name: str
    keywords: tuple[str, ...]
    points: int


def _ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    seeded = conn.execute("SELECT 1 FROM settings WHERE key = 'locations_seeded'").fetchone()
    if not seeded:
        with conn:
            for name, keywords, points in DEFAULT_TARGETS:
                conn.execute(
                    "INSERT INTO location_targets (name, keywords, points) VALUES (?, ?, ?)",
                    (name, ",".join(keywords), points),
                )
            conn.execute("INSERT INTO settings (key, value) VALUES ('locations_seeded', '1')")


def _split_keywords(text: str) -> list[str]:
    return [k.strip().lower() for k in text.split(",") if k.strip()]


def list_targets(conn: sqlite3.Connection) -> list[Target]:
    _ensure_tables(conn)
    rows = conn.execute("SELECT id, name, keywords, points FROM location_targets ORDER BY points DESC, id")
    targets = []
    for row in rows:
        stored = [k for k in row[2].split(",") if k]
        targets.append(Target(row[0], row[1], tuple(stored), row[3]))
    return targets


def add_target(conn: sqlite3.Connection, name: str, keywords: str, points: int) -> None:
    """keywords is comma-separated, e.g. "seattle, bellevue, redmond"."""
    _ensure_tables(conn)
    name = name.strip()
    words = _split_keywords(keywords) or ([name.lower()] if name else [])
    if not name or not words:
        raise ValueError("Give the target a name (and optionally keywords).")
    if not 0 <= points <= 100:
        raise ValueError("Points must be between 0 and 100.")
    with conn:
        conn.execute(
            "INSERT INTO location_targets (name, keywords, points) VALUES (?, ?, ?)",
            (name, ",".join(words), points),
        )


def delete_target(conn: sqlite3.Connection, target_id: int) -> None:
    _ensure_tables(conn)
    with conn:
        conn.execute("DELETE FROM location_targets WHERE id = ?", (target_id,))


def _keyword_in(keyword: str, text: str) -> bool:
    """Whole-phrase match, so ", ca" matches "San Jose, CA" but not "Toronto, Canada"."""
    return re.search(rf"(?<!\w){re.escape(keyword)}(?!\w)", text) is not None


def matching_target(job: JobPosting, targets: list[Target]) -> Target | None:
    """The highest-points target this job's location matches, if any."""
    text = (job.location or "").lower()
    if not text:
        return None
    for target in sorted(targets, key=lambda t: t.points, reverse=True):
        if any(_keyword_in(keyword, text) for keyword in target.keywords):
            return target
    return None
