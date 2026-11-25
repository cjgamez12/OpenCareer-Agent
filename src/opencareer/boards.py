"""Which company job boards to search, and which board each saved job came from."""
import re
import sqlite3

from opencareer.models import JobPosting

from opencareer.sources import registry

TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS boards (
    token   TEXT PRIMARY KEY,
    company TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS job_boards (
    url   TEXT PRIMARY KEY,
    board TEXT NOT NULL
);
"""


def _ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(boards)")}
    if "removed" not in columns:  # databases made before "Remove" existed
        with conn:
            conn.execute("ALTER TABLE boards ADD COLUMN removed INTEGER NOT NULL DEFAULT 0")


def add_board(conn: sqlite3.Connection, token: str, company: str, source: str = "greenhouse") -> str:
    """Save a company's job board. token is the NAME in its job link; returns the board key."""
    _ensure_tables(conn)
    source, token = registry.parse_board_input(token, source)
    token = token.strip()
    if source == "greenhouse":
        token = token.lower()
    if not TOKEN_PATTERN.match(token):
        raise ValueError("Board names use only letters, numbers, dots, dashes, and underscores.")
    key = registry.make_key(source, token)
    if source == "usajobs":
        company = company.strip() or f"USAJOBS: {token.replace('-', ' ')}"
    company = company.strip() or token.replace("-", " ").title()
    with conn:
        conn.execute("INSERT OR REPLACE INTO boards (token, company, removed) VALUES (?, ?, 0)",
                     (key, company))
    return key


def remove_board(conn: sqlite3.Connection, token: str, company: str = "") -> None:
    """Stop searching a company (built-in or added). Adding it again brings it back."""
    _ensure_tables(conn)
    with conn:
        conn.execute("INSERT OR REPLACE INTO boards (token, company, removed) VALUES (?, ?, 1)",
                     (token, company or token))


def all_boards(conn: sqlite3.Connection, defaults: dict[str, str]) -> dict[str, str]:
    """Built-in boards plus any you added from the dashboard."""
    _ensure_tables(conn)
    merged = dict(defaults)
    for token, company, removed in conn.execute("SELECT token, company, removed FROM boards ORDER BY token"):
        if removed:
            merged.pop(token, None)
        else:
            merged[token] = company
    return merged


def remember(conn: sqlite3.Connection, jobs: list[JobPosting], board: str) -> None:
    """Record which board each job came from (needed to fetch its full description)."""
    _ensure_tables(conn)
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO job_boards (url, board) VALUES (?, ?)",
            [(job.url, board) for job in jobs],
        )


def board_for(conn: sqlite3.Connection, job: JobPosting, defaults: dict[str, str]) -> str | None:
    """Find the board for a job: by its URL first, then by matching the company name."""
    _ensure_tables(conn)
    row = conn.execute("SELECT board FROM job_boards WHERE url = ?", (job.url,)).fetchone()
    if row:
        return row[0]
    for token, company in all_boards(conn, defaults).items():
        if company.lower() == job.company.lower():
            return token
    return None
