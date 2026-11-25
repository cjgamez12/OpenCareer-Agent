"""Daily autopilot: find new jobs, tailor the best few, and tell you they're ready.

It NEVER applies. It only gets drafts ready for your review.

Run it once by hand:
    PYTHONPATH=src python -m opencareer.autopilot
Turn the daily schedule on/off (macOS):
    PYTHONPATH=src python -m opencareer.autopilot --install --hour 12
    PYTHONPATH=src python -m opencareer.autopilot --uninstall
(or use the buttons on the dashboard)
"""
import argparse
import json
import os
import plistlib
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

from opencareer import applicant, locations, pipeline, settings, storage, tailor, tracker
from opencareer import boards as board_registry
from opencareer.ai import AIError
from opencareer.sources import greenhouse, registry

DEFAULT_COUNT = 5
LABEL = "com.opencareer.autopilot"
PROJECT = Path(__file__).resolve().parents[2]

SCHEMA = """
CREATE TABLE IF NOT EXISTS autopilot_runs (
    id       INTEGER PRIMARY KEY,
    ran_at   TEXT NOT NULL,
    fetched  INTEGER NOT NULL,
    new      INTEGER NOT NULL,
    tailored TEXT NOT NULL,      -- JSON list of job URLs
    errors   TEXT NOT NULL       -- JSON list of messages
);
"""


def _ensure_table(conn) -> None:
    conn.executescript(SCHEMA)


def _new_grad(conn, profile_dir: Path) -> bool:
    chosen = settings.get_flag(conn, "new_grad_mode")
    if chosen is not None:
        return chosen
    text = applicant.read_profile_text(profile_dir)
    try:
        grad = str(((applicant.parse_profile(text) if text else {}).get("education") or {}).get("graduation", ""))
    except ValueError:
        return False
    return bool(grad) and grad[:7] <= date.today().isoformat()[:7]


def run(conn, *, profile_dir: Path, output_dir: Path, default_boards: dict,
        count: int = DEFAULT_COUNT, today: date | None = None,
        describe=None, tailor_fn=None, notify=None) -> dict:
    """One autopilot run. Returns a summary dict (also saved in the database)."""
    _ensure_table(conn)
    today = today or date.today()
    describe = describe or greenhouse.fetch_job_description
    tailor_fn = tailor_fn or tailor.tailor_job
    notify = notify or notify_mac

    stats = pipeline.run(conn, board_registry.all_boards(conn, default_boards))
    targets = locations.list_targets(conn)
    ranked = pipeline.shortlist(conn, today, limit=1000, targets=targets,
                                new_grad=_new_grad(conn, profile_dir))

    tailored, errors = [], [f"Couldn't reach: {name}" for name in stats["errors"]]
    for _, job in ranked:
        if len(tailored) >= count:
            break
        if tailor.load_draft(job, output_dir):
            continue                                   # already has a draft
        description = job.description
        board = board_registry.board_for(conn, job, default_boards)
        if not description and board and registry.split_key(board)[0] == "greenhouse":
            try:
                description = describe(registry.split_key(board)[1], job.source_id)
            except Exception:
                description = ""
        try:
            tailor_fn(job, description, profile_dir=profile_dir, output_dir=output_dir)
            tailored.append(job.url)
        except AIError as error:
            errors.append(f"{job.title} at {job.company}: {error}")
            if "API key" in str(error) or "profile" in str(error).lower():
                break                                  # every other job would fail the same way

    summary = {"ran_at": datetime.now().isoformat(timespec="seconds"), "fetched": stats["fetched"],
               "new": stats["new"], "tailored": tailored, "errors": errors}
    with conn:
        conn.execute("INSERT INTO autopilot_runs (ran_at, fetched, new, tailored, errors) VALUES (?, ?, ?, ?, ?)",
                     (summary["ran_at"], summary["fetched"], summary["new"],
                      json.dumps(tailored), json.dumps(errors)))
    if tailored:
        notify(f"{len(tailored)} application{'s' if len(tailored) != 1 else ''} ready for review",
               f"{stats['new']} new jobs found. Open the dashboard to review.")
    elif errors:
        notify("Autopilot needs attention", errors[0][:120])
    return summary


def last_run(conn) -> dict | None:
    _ensure_table(conn)
    row = conn.execute("SELECT ran_at, fetched, new, tailored, errors FROM autopilot_runs "
                       "ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        return None
    return {"ran_at": row[0], "fetched": row[1], "new": row[2],
            "tailored": json.loads(row[3]), "errors": json.loads(row[4])}


def ready_for_review(conn, days: int = 14) -> list[str]:
    """Job URLs the autopilot tailored recently that you haven't applied to yet."""
    _ensure_table(conn)
    applied = tracker.applied_urls(conn)
    since = (datetime.now().date().toordinal() - days)
    urls = []
    for ran_at, tailored in conn.execute("SELECT ran_at, tailored FROM autopilot_runs ORDER BY id DESC"):
        if datetime.fromisoformat(ran_at).date().toordinal() < since:
            break
        for url in json.loads(tailored):
            if url not in applied and url not in urls:
                urls.append(url)
    return urls


# ---------------- notifications & schedule (macOS) ----------------

def notify_mac(title: str, message: str) -> None:
    """A small macOS notification. Does nothing on other systems."""
    if sys.platform != "darwin":
        return
    script = f'display notification {json.dumps(message)} with title "OpenCareer Agent" subtitle {json.dumps(title)}'
    subprocess.run(["osascript", "-e", script], check=False, capture_output=True)


def plist_path(home: Path | None = None) -> Path:
    return (home or Path.home()) / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def build_plist(hour: int, minute: int = 0, project: Path = PROJECT, python: str | None = None) -> dict:
    python = python or str(project / ".venv" / "bin" / "python")
    return {
        "Label": LABEL,
        "ProgramArguments": [python, "-m", "opencareer.autopilot"],
        "WorkingDirectory": str(project),
        "EnvironmentVariables": {"PYTHONPATH": str(project / "src")},
        "StartCalendarInterval": {"Hour": hour, "Minute": minute},
        "StandardOutPath": str(project / "autopilot.log"),
        "StandardErrorPath": str(project / "autopilot.log"),
    }


def install_schedule(hour: int, minute: int = 0, *, home: Path | None = None, runner=subprocess.run) -> Path:
    """Run every day at hour:minute. If the Mac is asleep then, it runs when it wakes up."""
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("Pick a time between 00:00 and 23:59.")
    path = plist_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    runner(["launchctl", "unload", str(path)], check=False, capture_output=True)
    path.write_bytes(plistlib.dumps(build_plist(hour, minute)))
    runner(["launchctl", "load", str(path)], check=False, capture_output=True)
    return path


def uninstall_schedule(*, home: Path | None = None, runner=subprocess.run) -> None:
    path = plist_path(home)
    if path.exists():
        runner(["launchctl", "unload", str(path)], check=False, capture_output=True)
        path.unlink()


def scheduled_time(home: Path | None = None) -> str | None:
    """'12:00' if the daily run is on, else None."""
    path = plist_path(home)
    if not path.exists():
        return None
    try:
        when = plistlib.loads(path.read_bytes())["StartCalendarInterval"]
        return f"{when['Hour']:02d}:{when.get('Minute', 0):02d}"
    except Exception:
        return None


def main(argv=None) -> None:
    from dotenv import load_dotenv

    parser = argparse.ArgumentParser(description="Find new jobs and tailor the best ones for your review.")
    parser.add_argument("--count", type=int, default=None, help=f"how many to tailor (default {DEFAULT_COUNT})")
    parser.add_argument("--install", action="store_true", help="run every day (macOS)")
    parser.add_argument("--uninstall", action="store_true", help="stop the daily run")
    parser.add_argument("--hour", type=int, default=12)
    parser.add_argument("--minute", type=int, default=0)
    args = parser.parse_args(argv)

    if args.install:
        print(f"Daily autopilot on at {args.hour:02d}:{args.minute:02d}: {install_schedule(args.hour, args.minute)}")
        return
    if args.uninstall:
        uninstall_schedule()
        print("Daily autopilot off.")
        return

    os.chdir(PROJECT)
    load_dotenv(PROJECT / ".env")
    conn = storage.connect(os.environ.get("OPENCAREER_DB", "jobs.db"))
    count = args.count or get_count(conn)
    summary = run(conn, profile_dir=Path(os.environ.get("OPENCAREER_PROFILE_DIR", "profile")),
                  output_dir=Path(os.environ.get("OPENCAREER_APPLICATIONS_DIR", "applications")),
                  default_boards=pipeline.BOARDS, count=count)
    conn.close()
    print(f"[{summary['ran_at']}] fetched {summary['fetched']}, new {summary['new']}, "
          f"tailored {len(summary['tailored'])}, errors {len(summary['errors'])}")
    for error in summary["errors"]:
        print("  -", error)


def get_count(conn) -> int:
    conn.executescript(settings.SCHEMA)
    row = conn.execute("SELECT value FROM settings WHERE key = 'autopilot_count'").fetchone()
    return int(row[0]) if row and row[0].isdigit() else DEFAULT_COUNT


def set_count(conn, count: int) -> None:
    if not 1 <= count <= 20:
        raise ValueError("Pick between 1 and 20 per day.")
    conn.executescript(settings.SCHEMA)
    with conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('autopilot_count', ?)", (str(count),))



if __name__ == "__main__":
    main()
