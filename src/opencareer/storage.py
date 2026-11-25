"""Save and load JobPostings in SQLite."""
import sqlite3
from datetime import date, datetime

from opencareer.models import JobPosting

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id              INTEGER PRIMARY KEY,
    source          TEXT NOT NULL,
    source_id       TEXT NOT NULL,
    title           TEXT NOT NULL,
    company         TEXT NOT NULL,
    url             TEXT NOT NULL,
    location        TEXT,
    remote          INTEGER NOT NULL DEFAULT 0,
    description     TEXT NOT NULL DEFAULT '',
    posted_at       TEXT,
    deadline        TEXT,
    deadline_source TEXT,
    tags            TEXT NOT NULL DEFAULT '',
    fingerprint     TEXT NOT NULL UNIQUE,
    first_seen      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (source, source_id)
);
"""

INSERT_SQL = """
INSERT OR IGNORE INTO jobs (
    source, source_id, title, company, url, location, remote,
    description, posted_at, deadline, deadline_source, tags, fingerprint
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def connect(path: str = "jobs.db") -> sqlite3.Connection:
    """Open (or create) the database and make sure the table exists."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row      # lets us read columns by name: row["title"]
    conn.executescript(SCHEMA)
    return conn


def _to_row(job: JobPosting) -> tuple:
    """JobPosting -> tuple of values in the same order as INSERT_SQL."""
    return (
        job.source,
        job.source_id,
        job.title,
        job.company,
        job.url,
        job.location,
        int(job.remote),                                   # SQLite has no bool: 1/0
        job.description,
        job.posted_at.isoformat() if job.posted_at else None,
        job.deadline.isoformat() if job.deadline else None,
        job.deadline_source,
        ",".join(job.tags),                                # ("a","b") -> "a,b"
        job.fingerprint(),
    )


def _from_row(row: sqlite3.Row) -> JobPosting:
    """Database row -> JobPosting (the reverse of _to_row)."""
    return JobPosting(
        source=row["source"],
        source_id=row["source_id"],
        title=row["title"],
        company=row["company"],
        url=row["url"],
        location=row["location"],
        remote=bool(row["remote"]),
        description=row["description"],
        posted_at=datetime.fromisoformat(row["posted_at"]) if row["posted_at"] else None,
        deadline=date.fromisoformat(row["deadline"]) if row["deadline"] else None,
        deadline_source=row["deadline_source"],
        tags=tuple(t for t in row["tags"].split(",") if t),
    )

def save_jobs(conn: sqlite3.Connection, jobs: list[JobPosting]) -> int:
    """Insert jobs, skipping duplicates. Return how many were actually new."""
    new_count = 0
    with conn:
        for job in jobs:
            cursor = conn.execute(INSERT_SQL, _to_row(job))
            new_count += cursor.rowcount
    return new_count 


def load_jobs(conn: sqlite3.Connection) -> list[JobPosting]:
    """Return every saved job, oldest first."""
    rows = conn.execute("SELECT * FROM jobs ORDER BY id").fetchall()
    return [_from_row(row) for row in rows]
