"""Track applications: stages, interview rounds, notes, and follow-ups."""
import argparse
import sqlite3
from datetime import date, timedelta

STAGES = [
    "applied", "assessment", "recruiter_screen", "interview",
    "final_round", "offer", "accepted", "rejected", "withdrawn", "ghosted",
]
CLOSED_STAGES = {"accepted", "rejected", "withdrawn", "ghosted"}
DEFAULT_FOLLOW_UP_DAYS = 7

SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    url        TEXT PRIMARY KEY,
    applied_on TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'applied'
);
CREATE TABLE IF NOT EXISTS events (
    id           INTEGER PRIMARY KEY,
    url          TEXT NOT NULL REFERENCES applications(url),
    happened_on  TEXT NOT NULL,
    stage        TEXT NOT NULL,
    round        INTEGER,
    note         TEXT NOT NULL DEFAULT '',
    follow_up_on TEXT
);
"""


def _ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def mark_applied(conn: sqlite3.Connection, url: str,
                 on: date | None = None, note: str = "") -> None:
    """Record a submitted application. Marking the same URL twice is harmless."""
    _ensure_tables(conn)
    on = on or date.today()
    follow_up = on + timedelta(days=DEFAULT_FOLLOW_UP_DAYS)
    with conn:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO applications (url, applied_on) VALUES (?, ?)",
            (url, on.isoformat()),
        )
        if cursor.rowcount == 1:
            conn.execute(
                "INSERT INTO events (url, happened_on, stage, note, follow_up_on) "
                "VALUES (?, ?, 'applied', ?, ?)",
                (url, on.isoformat(), note, follow_up.isoformat()),
            )


def update(conn: sqlite3.Connection, url: str, stage: str,
           on: date | None = None, round_number: int | None = None,
           note: str = "", follow_up_on: date | None = None) -> None:
    """Record a new stage (interview, rejection, offer, ...)."""
    _ensure_tables(conn)
    if stage not in STAGES:
        raise ValueError(f"Unknown stage {stage!r}. Choose from: {', '.join(STAGES)}")
    exists = conn.execute("SELECT 1 FROM applications WHERE url = ?", (url,)).fetchone()
    if exists is None:
        raise ValueError(f"No application recorded for {url}. Run 'apply' first.")

    on = on or date.today()
    if stage in CLOSED_STAGES:
        follow_up = None
    else:
        follow_up = follow_up_on or on + timedelta(days=DEFAULT_FOLLOW_UP_DAYS)

    with conn:
        conn.execute(
            "INSERT INTO events (url, happened_on, stage, round, note, follow_up_on) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (url, on.isoformat(), stage, round_number, note,
             follow_up.isoformat() if follow_up else None),
        )
        conn.execute("UPDATE applications SET status = ? WHERE url = ?", (stage, url))


def applied_urls(conn: sqlite3.Connection) -> set[str]:
    _ensure_tables(conn)
    return {row[0] for row in conn.execute("SELECT url FROM applications")}


def list_applications(conn: sqlite3.Connection) -> list[tuple[str, str, str]]:
    _ensure_tables(conn)
    rows = conn.execute(
        "SELECT applied_on, status, url FROM applications ORDER BY applied_on"
    )
    return [tuple(row) for row in rows]


def history(conn: sqlite3.Connection, url: str) -> list[tuple]:
    _ensure_tables(conn)
    rows = conn.execute(
        "SELECT happened_on, stage, round, note FROM events WHERE url = ? ORDER BY id",
        (url,),
    )
    return [tuple(row) for row in rows]


def due_follow_ups(conn: sqlite3.Connection, today: date) -> list[tuple[str, str, str]]:
    """Applications whose LATEST step has a follow-up date on or before today."""
    _ensure_tables(conn)
    rows = conn.execute(
        """
        SELECT e.url, e.stage, e.follow_up_on
        FROM events e
        JOIN (SELECT url, MAX(id) AS last_id FROM events GROUP BY url) latest
          ON e.id = latest.last_id
        WHERE e.follow_up_on IS NOT NULL AND e.follow_up_on <= ?
        ORDER BY e.follow_up_on
        """,
        (today.isoformat(),),
    )
    return [tuple(row) for row in rows]


def main(argv: list[str] | None = None) -> None:
    from opencareer import storage

    parser = argparse.ArgumentParser(prog="tracker", description="Track your job applications.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("apply", help="record that you submitted an application")
    p.add_argument("url")
    p.add_argument("--note", default="")

    p = sub.add_parser("update", help="record a new stage for an application")
    p.add_argument("url")
    p.add_argument("stage", choices=STAGES)
    p.add_argument("--round", type=int, dest="round_number")
    p.add_argument("--note", default="")
    p.add_argument("--follow-up", type=date.fromisoformat, dest="follow_up_on",
                   help="custom reminder date, YYYY-MM-DD")

    sub.add_parser("list", help="every application and its current status")

    p = sub.add_parser("history", help="full timeline for one application")
    p.add_argument("url")

    sub.add_parser("due", help="follow-ups due today or earlier")

    args = parser.parse_args(argv)
    conn = storage.connect("jobs.db")
    try:
        if args.command == "apply":
            mark_applied(conn, args.url, note=args.note)
            print(f"Recorded application: {args.url}")
        elif args.command == "update":
            update(conn, args.url, args.stage, round_number=args.round_number,
                   note=args.note, follow_up_on=args.follow_up_on)
            print(f"Updated: {args.url} -> {args.stage}")
        elif args.command == "list":
            for applied_on, status, url in list_applications(conn):
                print(f"{applied_on}  {status:<17} {url}")
        elif args.command == "history":
            for happened_on, stage, rnd, note in history(conn, args.url):
                label = f"{stage} (round {rnd})" if rnd else stage
                print(f"{happened_on}  {label:<26} {note}")
        elif args.command == "due":
            due = due_follow_ups(conn, date.today())
            if not due:
                print("No follow-ups due.")
            for url, stage, follow_up_on in due:
                print(f"{follow_up_on}  after {stage:<17} {url}")
    except ValueError as error:
        print(f"Error: {error}")
        raise SystemExit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
