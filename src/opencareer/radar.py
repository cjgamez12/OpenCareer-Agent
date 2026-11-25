"""Job radar: checks every company board every 30 minutes, so you hear about new jobs fast
and closed ones disappear.

- New: a job that matches you and wasn't on its company's board at the last check.
- Closed: a job that's gone from its company's board two checks in a row (one miss can be a
  hiccup). If it comes back, it's open again.
- A Mac notification tells you about strong new matches.

Run it once by hand:
    PYTHONPATH=src python -m opencareer.radar
Turn the 30-minute schedule on/off (macOS), or use the dashboard:
    PYTHONPATH=src python -m opencareer.radar --install
    PYTHONPATH=src python -m opencareer.radar --uninstall
"""
import argparse
import json
import os
import plistlib
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path

LABEL = "com.opencareer.radar"
INTERVAL_MINUTES = 30
CLOSE_AFTER_MISSES = 2
NEW_FOR_HOURS = 24                  # how long a job wears its "new" badge
PROJECT = Path(__file__).resolve().parents[2]

SCHEMA = """
CREATE TABLE IF NOT EXISTS job_seen (
    url        TEXT PRIMARY KEY,
    board      TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen  TEXT NOT NULL,
    misses     INTEGER NOT NULL DEFAULT 0,
    closed_on  TEXT,
    baseline   INTEGER NOT NULL DEFAULT 0   -- 1 = already there the first time we looked
);
CREATE TABLE IF NOT EXISTS radar_runs (
    id       INTEGER PRIMARY KEY,
    ran_at   TEXT NOT NULL,
    checked  INTEGER NOT NULL,
    new      TEXT NOT NULL,          -- JSON list of job URLs
    closed   TEXT NOT NULL,          -- JSON list of job URLs
    errors   TEXT NOT NULL           -- JSON list of board keys
);
"""


def _ensure(conn) -> None:
    conn.executescript(SCHEMA)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def observe(conn, board: str, fetched_urls: set[str] | None, matching_urls: list[str], now: str | None = None) -> dict:
    """Record one successful look at a board. Returns {"new": [...], "closed": [...], "reopened": [...]}.

    fetched_urls: every job on the board right now (None when the source isn't a full list, so
    nothing can be called closed). matching_urls: the ones that match you.
    The first time a board is seen, its jobs are the baseline, not "new" (no notification flood)."""
    _ensure(conn)
    now = now or _now()
    known = {url: (misses, closed_on) for url, misses, closed_on in
             conn.execute("SELECT url, misses, closed_on FROM job_seen WHERE board = ?", (board,))}
    baseline = not known
    result = {"new": [], "closed": [], "reopened": []}
    with conn:
        for url in matching_urls:
            if url not in known:
                conn.execute("INSERT OR IGNORE INTO job_seen (url, board, first_seen, last_seen, baseline) "
                             "VALUES (?, ?, ?, ?, ?)", (url, board, now, now, int(baseline)))
                if not baseline:
                    result["new"].append(url)
        for url, (misses, closed_on) in known.items():
            if fetched_urls is None:
                continue
            if url in fetched_urls:
                if closed_on:
                    result["reopened"].append(url)
                conn.execute("UPDATE job_seen SET last_seen = ?, misses = 0, closed_on = NULL WHERE url = ?",
                             (now, url))
            elif not closed_on:
                misses += 1
                closed = now if misses >= CLOSE_AFTER_MISSES else None
                conn.execute("UPDATE job_seen SET misses = ?, closed_on = ? WHERE url = ?", (misses, closed, url))
                if closed:
                    result["closed"].append(url)
    return result


def closed_urls(conn) -> set[str]:
    _ensure(conn)
    return {row[0] for row in conn.execute("SELECT url FROM job_seen WHERE closed_on IS NOT NULL")}


def new_urls(conn, hours: int = NEW_FOR_HOURS, now: datetime | None = None) -> set[str]:
    """Jobs first seen in the last day (but not part of a board's first baseline look)."""
    _ensure(conn)
    since = ((now or datetime.now()) - timedelta(hours=hours)).isoformat(timespec="seconds")
    rows = conn.execute("SELECT url FROM job_seen WHERE first_seen >= ? AND baseline = 0 AND closed_on IS NULL",
                        (since,))
    return {row[0] for row in rows}


# ---------------------------------------------------------------------------
# One check of every board
# ---------------------------------------------------------------------------

def check(conn, *, default_boards: dict, profile_dir: Path, notify=None, today: date | None = None) -> dict:
    """Fetch every board, update new/closed, notify about strong new matches. Returns a summary."""
    from opencareer import autopilot, locations, pipeline
    from opencareer import boards as board_registry

    _ensure(conn)
    notify = notify or autopilot.notify_mac
    stats = pipeline.run(conn, board_registry.all_boards(conn, default_boards))
    targets = locations.list_targets(conn)
    ranked = pipeline.shortlist(conn, today or date.today(), limit=1000, targets=targets,
                                new_grad=autopilot._new_grad(conn, profile_dir))
    fresh = set(stats.get("new_urls", []))
    best = [job for _, job in ranked if job.url in fresh]
    summary = {"ran_at": _now(), "checked": stats["fetched"], "new": [j.url for j in best],
               "closed": stats.get("closed_urls", []), "errors": stats["errors"]}
    with conn:
        conn.execute("INSERT INTO radar_runs (ran_at, checked, new, closed, errors) VALUES (?, ?, ?, ?, ?)",
                     (summary["ran_at"], summary["checked"], json.dumps(summary["new"]),
                      json.dumps(summary["closed"]), json.dumps(summary["errors"])))
    if best:
        top = "; ".join(f"{job.title} · {job.company}" for job in best[:2])
        more = f" (+{len(best) - 2} more)" if len(best) > 2 else ""
        notify(f"{len(best)} new job{'s' if len(best) != 1 else ''} for you", top[:180] + more)
    return summary


def last_run(conn) -> dict | None:
    _ensure(conn)
    row = conn.execute("SELECT ran_at, checked, new, closed, errors FROM radar_runs ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        return None
    return {"ran_at": row[0], "checked": row[1], "new": json.loads(row[2]),
            "closed": json.loads(row[3]), "errors": json.loads(row[4])}


# ---------------------------------------------------------------------------
# The 30-minute schedule (macOS launchd)
# ---------------------------------------------------------------------------

def plist_path(home: Path | None = None) -> Path:
    return (home or Path.home()) / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def build_plist(minutes: int = INTERVAL_MINUTES, project: Path = PROJECT, python: str | None = None) -> dict:
    python = python or str(project / ".venv" / "bin" / "python")
    return {
        "Label": LABEL,
        "ProgramArguments": [python, "-m", "opencareer.radar"],
        "WorkingDirectory": str(project),
        "EnvironmentVariables": {"PYTHONPATH": str(project / "src")},
        "StartInterval": minutes * 60,
        "RunAtLoad": True,
        "StandardOutPath": str(project / "radar.log"),
        "StandardErrorPath": str(project / "radar.log"),
    }


def install(*, home: Path | None = None, runner=subprocess.run, minutes: int = INTERVAL_MINUTES) -> Path:
    if not 15 <= minutes <= 24 * 60:
        raise ValueError("Check between every 15 minutes and once a day.")
    path = plist_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    runner(["launchctl", "unload", str(path)], check=False, capture_output=True)
    path.write_bytes(plistlib.dumps(build_plist(minutes)))
    runner(["launchctl", "load", str(path)], check=False, capture_output=True)
    return path


def uninstall(*, home: Path | None = None, runner=subprocess.run) -> None:
    path = plist_path(home)
    if path.exists():
        runner(["launchctl", "unload", str(path)], check=False, capture_output=True)
        path.unlink()


def is_on(home: Path | None = None) -> bool:
    return plist_path(home).exists()


def main(argv=None) -> None:
    from dotenv import load_dotenv

    from opencareer import pipeline, storage

    parser = argparse.ArgumentParser(description="Check every company board for new and closed jobs.")
    parser.add_argument("--install", action="store_true", help=f"check every {INTERVAL_MINUTES} minutes (macOS)")
    parser.add_argument("--uninstall", action="store_true", help="stop the regular checks")
    args = parser.parse_args(argv)
    if args.install:
        print(f"Job radar on: checking every {INTERVAL_MINUTES} minutes ({install()}).")
        return
    if args.uninstall:
        uninstall()
        print("Job radar off.")
        return

    os.chdir(PROJECT)
    load_dotenv(PROJECT / ".env")
    conn = storage.connect(os.environ.get("OPENCAREER_DB", "jobs.db"))
    summary = check(conn, default_boards=pipeline.BOARDS,
                    profile_dir=Path(os.environ.get("OPENCAREER_PROFILE_DIR", "profile")))
    conn.close()
    print(f"[{summary['ran_at']}] checked {summary['checked']} jobs: {len(summary['new'])} new for you, "
          f"{len(summary['closed'])} closed, {len(summary['errors'])} boards unreachable")


if __name__ == "__main__":
    main()
