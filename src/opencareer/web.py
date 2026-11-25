"""Local dashboard for OpenCareer Agent. Runs only on your computer."""
import html
import logging
import os
import subprocess
import sys
from datetime import date
from pathlib import Path
from urllib.parse import quote

import markdown as md
import requests
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from opencareer import applicant, autopilot, finder, github, journey, locations, pipeline, settings, storage, tailor, tracker
from opencareer import apply as assisted_apply
from opencareer import boards as board_registry
from opencareer.ai import AIError
from opencareer.filters import _has_word
from opencareer.sources import greenhouse, registry, usajobs

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
CATEGORIES = ["software", "cybersecurity", "networking", "it"]


def safe_url(url: str) -> str:
    """Only allow http(s) links, so a bad URL from an API can't run JavaScript."""
    return url if url.startswith(("https://", "http://")) else "#"


def render_markdown(text: str) -> Markup:
    """Markdown -> HTML, with any raw HTML in the text escaped first (never executed)."""
    return Markup(md.markdown(html.escape(text or "", quote=False)))


TEMPLATES.env.filters["safe_url"] = safe_url
TEMPLATES.env.globals["grade_of"] = lambda job: pipeline.grade_label(job)
TEMPLATES.env.globals["recent_grads"] = lambda job: pipeline.RECENT_GRADS_LINE in (job.description or "")
TEMPLATES.env.globals["source_label"] = lambda key: registry.LABELS[registry.split_key(key)[0]]


class _HideRefreshChecks(logging.Filter):
    """Keep the terminal readable: don't log the page's every-3-seconds refresh check."""

    def filter(self, record: logging.LogRecord) -> bool:
        return "/api/version" not in record.getMessage()


logging.getLogger("uvicorn.access").addFilter(_HideRefreshChecks())
TEMPLATES.env.filters["markdown"] = render_markdown


def _back(message: str, to: str = "/") -> RedirectResponse:
    """Go back to a page and show a one-line message."""
    joiner = "&" if "?" in to else "?"
    return RedirectResponse(url=f"{to}{joiner}message={quote(message)}", status_code=303)


def create_app(db_path: str | None = None, boards: dict | None = None,
               profile_dir: str | None = None, output_dir: str | None = None,
               describe=None, tailor_fn=None, ai_client=None, launch_apply=None,
               launch_autopilot=None, schedule_home=None, launchctl=None, github_session=None) -> FastAPI:
    load_dotenv()  # reads ANTHROPIC_API_KEY from .env
    db_path = db_path or os.environ.get("OPENCAREER_DB", "jobs.db")
    default_boards = boards if boards is not None else pipeline.BOARDS
    profile_dir = Path(profile_dir or os.environ.get("OPENCAREER_PROFILE_DIR", "profile"))
    output_dir = Path(output_dir or os.environ.get("OPENCAREER_APPLICATIONS_DIR", "applications"))
    describe = describe or greenhouse.fetch_job_description
    if tailor_fn is None:
        def tailor_fn(job, description, **kwargs):
            return tailor.tailor_job(job, description, client=ai_client, **kwargs)

    if launch_apply is None:
        def launch_apply(url: str, log_folder: Path) -> None:
            """Start the form filler in the background, so the dashboard stays usable."""
            src = str(Path(__file__).resolve().parents[1])
            env = {**os.environ, "PYTHONPATH": src + os.pathsep + os.environ.get("PYTHONPATH", "")}
            log_folder.mkdir(parents=True, exist_ok=True)
            log = open(log_folder / "apply_log.txt", "w")
            subprocess.Popen(
                [sys.executable, "-m", "opencareer.apply", "--url", url, "--db", str(db_path),
                 "--profile", str(profile_dir), "--out", str(output_dir)],
                env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
            )

    if launch_autopilot is None:
        def launch_autopilot() -> None:
            """Run the autopilot now, in the background."""
            src = str(Path(__file__).resolve().parents[1])
            env = {**os.environ, "PYTHONPATH": src + os.pathsep + os.environ.get("PYTHONPATH", "")}
            log = open(Path(db_path).resolve().parent / "autopilot.log", "a")
            subprocess.Popen([sys.executable, "-m", "opencareer.autopilot"], env=env,
                             stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    launchctl = launchctl or subprocess.run

    app = FastAPI(title="OpenCareer Agent")

    def get_conn():
        """One database connection per request, always closed afterwards."""
        conn = storage.connect(db_path)
        try:
            yield conn
        finally:
            conn.close()

    def find_job(conn, url: str):
        for job in pipeline.select_jobs(storage.load_jobs(conn)):
            if job.url == url:
                return job
        return None

    def new_grad_mode(conn) -> bool:
        """Your choice if you've set it; otherwise ON once your graduation date has passed."""
        chosen = settings.get_flag(conn, "new_grad_mode")
        if chosen is not None:
            return chosen
        text = applicant.read_profile_text(profile_dir)
        try:
            grad = str(((applicant.parse_profile(text) if text else {}).get("education") or {}).get("graduation", ""))
        except ValueError:
            return False
        return bool(grad) and grad[:7] <= date.today().isoformat()[:7]

    def state_version() -> str:
        """Changes whenever the database, your profile/resumes, or a draft changes."""
        newest, count = 0, 0
        paths = [Path(db_path)]
        for folder in (profile_dir, output_dir):
            if folder.is_dir():
                paths += [p for p in folder.rglob("*") if p.is_file()]
        for path in paths:
            try:
                newest = max(newest, path.stat().st_mtime_ns)
                count += 1
            except FileNotFoundError:
                continue
        return f"{newest}-{count}"

    def page(request: Request, name: str, context: dict):
        context.setdefault("message", request.query_params.get("message", ""))
        context["nav"] = request.url.path
        context["version"] = state_version()
        return TEMPLATES.TemplateResponse(request, name, context)

    @app.get("/api/version")
    def version():
        """The browser asks this every few seconds and reloads when it changes."""
        return JSONResponse({"version": state_version()})

    # ---------------- dashboard ----------------

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request, category: str = "", hide_interns: bool = False,
                  only_targets: bool = False, q: str = "", conn=Depends(get_conn)):
        today = date.today()
        jobs_by_url = {job.url: job for job in storage.load_jobs(conn)}
        targets = locations.list_targets(conn)
        new_grad = new_grad_mode(conn)

        ranked = pipeline.shortlist(conn, today, limit=1000, targets=targets, new_grad=new_grad)
        if only_targets:
            ranked = [(s, j) for s, j in ranked if locations.matching_target(j, targets)]
        if category:
            ranked = [(s, j) for s, j in ranked if j.tags and j.tags[0] == category]
        if hide_interns:
            ranked = [(s, j) for s, j in ranked if not _has_word(j.title, "intern")]
        if q.strip():
            needle = q.strip().lower()
            ranked = [(s, j) for s, j in ranked
                      if needle in f"{j.title} {j.company} {j.location or ''}".lower()]

        applications = [
            {"applied_on": applied_on, "status": status, "url": url, "job": jobs_by_url.get(url)}
            for applied_on, status, url in tracker.list_applications(conn)
        ]
        due = [
            {"url": url, "stage": stage, "follow_up_on": follow_up_on, "job": jobs_by_url.get(url)}
            for url, stage, follow_up_on in tracker.due_follow_ups(conn, today)
        ]
        links = journey.flows(conn)
        journey_svg = Markup(journey.sankey_svg(links) or "")
        status_counts = {}
        for row in applications:
            status_counts[row["status"]] = status_counts.get(row["status"], 0) + 1

        shown = ranked[:50]
        drafted = {job.url for _, job in shown if tailor.load_draft(job, output_dir)}
        target_of = {}
        for _, job in shown:
            target = locations.matching_target(job, targets)
            if target:
                target_of[job.url] = target.name

        return page(request, "dashboard.html", {
            "ranked": shown, "total_matches": len(ranked), "drafted": drafted,
            "applications": applications, "due": due, "status_counts": status_counts,
            "categories": CATEGORIES, "category": category, "hide_interns": hide_interns,
            "only_targets": only_targets, "targets": targets, "target_of": target_of, "q": q,
            "stages": tracker.STAGES, "journey_svg": journey_svg, "journey_headline": journey.headline(links),
            "boards": board_registry.all_boards(conn, default_boards),
            "has_profile": applicant.read_profile_text(profile_dir) is not None,
            "new_grad": new_grad,
            "ready": [jobs_by_url[u] for u in autopilot.ready_for_review(conn) if u in jobs_by_url],
            "autopilot_last": autopilot.last_run(conn),
            "autopilot_time": autopilot.scheduled_time(schedule_home),
            "autopilot_count": autopilot.get_count(conn),
        })

    @app.post("/autopilot/schedule")
    def autopilot_schedule(on: str = Form(...), hour: int = Form(12), count: int = Form(5),
                           conn=Depends(get_conn)):
        if on != "1":
            autopilot.uninstall_schedule(home=schedule_home, runner=launchctl)
            return _back("Daily autopilot is off.")
        try:
            autopilot.set_count(conn, count)
            autopilot.install_schedule(hour, home=schedule_home, runner=launchctl)
        except ValueError as error:
            return _back(f"Not changed: {error}")
        return _back(f"Daily autopilot is on: every day at {hour:02d}:00 it finds new jobs and tailors "
                     f"the top {count}. It never applies for you.")

    @app.post("/autopilot/run")
    def autopilot_run():
        launch_autopilot()
        return _back("Autopilot is running in the background (a few minutes). "
                     "This page updates by itself when drafts are ready.")

    @app.post("/settings/new-grad")
    def toggle_new_grad(on: str = Form(...), conn=Depends(get_conn)):
        settings.set_flag(conn, "new_grad_mode", on == "1")
        return _back("New-grad mode is on: internships move down, new-grad and early-career roles move up."
                     if on == "1" else "New-grad mode is off: internships rank normally.")

    @app.post("/apply")
    def apply(url: str = Form(...), note: str = Form(""), back: str = Form("/"),
              conn=Depends(get_conn)):
        tracker.mark_applied(conn, url, note=note)
        return _back("Marked as applied. A follow-up reminder is set for 7 days from today.",
                     to=back if back.startswith("/") else "/")

    @app.post("/update")
    def update(url: str = Form(...), stage: str = Form(...), round_number: str = Form(""),
               note: str = Form(""), follow_up: str = Form(""), conn=Depends(get_conn)):
        try:
            tracker.update(
                conn, url, stage,
                round_number=int(round_number) if round_number.strip() else None,
                note=note,
                follow_up_on=date.fromisoformat(follow_up) if follow_up else None,
            )
        except ValueError as error:
            return _back(f"Not saved: {error}")
        return _back(f"Updated to {stage}.")

    @app.post("/refresh")
    def refresh(conn=Depends(get_conn)):
        stats = pipeline.run(conn, board_registry.all_boards(conn, default_boards))
        text = f"Checked {stats['fetched']} jobs, {stats['selected']} matched, {stats['new']} new."
        if stats["errors"]:
            text += (f" Could not reach: {', '.join(stats['errors'])}. "
                     "Remove it on the Companies page if it isn't a Greenhouse board.")
        return _back(text)

    @app.post("/locations")
    def add_location(name: str = Form(...), keywords: str = Form(""), points: str = Form("20"),
                     conn=Depends(get_conn)):
        try:
            locations.add_target(conn, name, keywords, int(points or 0))
        except ValueError as error:
            return _back(f"Not added: {error}")
        return _back(f"Added target location: {name.strip()}.")

    @app.post("/locations/delete")
    def delete_location(target_id: int = Form(...), conn=Depends(get_conn)):
        locations.delete_target(conn, target_id)
        return _back("Removed that target location.")

    # ---------------- companies ----------------

    @app.get("/companies", response_class=HTMLResponse)
    def companies(request: Request, conn=Depends(get_conn)):
        saved = pipeline.select_jobs(storage.load_jobs(conn))
        counts = {}
        for job in saved:
            counts[job.company.lower()] = counts.get(job.company.lower(), 0) + 1
        return page(request, "companies.html", {
            "boards": board_registry.all_boards(conn, default_boards), "counts": counts,
            "usajobs_ready": usajobs.has_credentials(),
        })

    @app.post("/boards")
    def add_board(token: str = Form(...), company: str = Form(""), source: str = Form("greenhouse"),
                  conn=Depends(get_conn)):
        detected, _ = registry.parse_board_input(token, source)
        if detected == "usajobs" and not usajobs.has_credentials():
            return _back("USAJOBS needs a free API key first: see the steps on this page.", to="/companies")
        try:
            key = board_registry.add_board(conn, token, company, source)
        except ValueError as error:
            return _back(f"Not added: {error}", to="/companies")
        name = board_registry.all_boards(conn, default_boards)[key]
        stats = pipeline.run(conn, {key: name})
        if stats["errors"]:
            board_registry.remove_board(conn, key, name)
            label = registry.LABELS[registry.split_key(key)[0]]
            return _back(f"{name} wasn't added: no {label} board called \"{registry.split_key(key)[1]}\". "
                         "Try pasting a link to one of its job postings instead.", to="/companies")
        return _back(f"Added {name}: {stats['fetched']} open jobs, {stats['selected']} match you.",
                     to=f"/company/{quote(key)}")

    @app.post("/boards/find")
    def find_boards(text: str = Form(""), conn=Depends(get_conn)):
        """Companies from a LinkedIn alert (or a list of names) -> their own job boards, tracked from now on."""
        try:
            companies = finder.companies_from_text(text, client=ai_client)
        except finder.FinderError as error:
            return _back(str(error), to="/companies")
        tracked = {name.lower() for name in board_registry.all_boards(conn, default_boards).values()}
        added, already, missing = {}, [], []
        for source, name in finder.board_links(text):
            try:
                key = board_registry.add_board(conn, name, name.replace("-", " ").title(), source)
            except ValueError:
                missing.append(name)
                continue
            added[key] = board_registry.all_boards(conn, default_boards)[key]
        for company in companies:
            if company.lower() in tracked:
                already.append(company)
                continue
            found = finder.find_board(company)
            if not found:
                missing.append(company)
                continue
            source, slug, _ = found
            key = board_registry.add_board(conn, slug, company, source)
            added[key] = company
        if not (added or already or missing):
            return _back("No company names found in what you pasted.", to="/companies")
        parts = []
        if added:
            stats = pipeline.run(conn, added)
            for key in stats["errors"]:                     # a pasted link to a board that doesn't exist
                board_registry.remove_board(conn, key, added[key])
                missing.append(added.pop(key))
        if added:
            labels = [f"{name} ({registry.LABELS[registry.split_key(key)[0]]})" for key, name in added.items()]
            parts.append(f"Now tracking {', '.join(labels)}: {stats['selected']} jobs match you.")
        if already:
            parts.append(f"Already tracking: {', '.join(already)}.")
        if missing:
            parts.append(f"No public job board found for {', '.join(missing)}. They may use Workday or LinkedIn Easy Apply. "
                         "On LinkedIn, click Apply: if it opens a Greenhouse, Lever or Ashby page, paste that link here.")
        return _back(" ".join(parts), to="/companies")

    @app.post("/boards/remove")
    def remove_board(token: str = Form(...), conn=Depends(get_conn)):
        name = board_registry.all_boards(conn, default_boards).get(token, token)
        board_registry.remove_board(conn, token, name)
        return _back(f"Stopped searching {name}.", to="/companies")

    @app.get("/company/{token}", response_class=HTMLResponse)
    def company_page(request: Request, token: str, q: str = "", conn=Depends(get_conn)):
        all_boards = board_registry.all_boards(conn, default_boards)
        if token not in all_boards:
            return _back("That company isn't in your list. Add it on the Companies page.",
                         to="/companies")
        company = all_boards[token]
        try:
            jobs, chosen, new = pipeline.fetch_board(conn, token, company)
        except requests.RequestException:
            return _back(f"Couldn't reach {company}'s job board right now.", to="/companies")

        targets = locations.list_targets(conn)
        applied = tracker.applied_urls(conn)
        suggested = pipeline.rank_with_targets(chosen, date.today(), targets, new_grad_mode(conn))
        chosen_urls = {job.url for job in chosen}
        others = [(job, pipeline.why_not_suggested(job)) for job in jobs if job.url not in chosen_urls]
        if q.strip():
            needle = q.strip().lower()
            suggested = [(s, j) for s, j in suggested if needle in f"{j.title} {j.location or ''}".lower()]
            others = [(j, r) for j, r in others if needle in f"{j.title} {j.location or ''}".lower()]
        others.sort(key=lambda pair: pair[0].title.lower())
        target_of = {j.url: t.name for _, j in suggested
                     if (t := locations.matching_target(j, targets))}
        return page(request, "company.html", {
            "token": token, "company": company, "total": len(jobs), "new": new,
            "suggested": suggested, "others": others, "applied": applied, "q": q,
            "drafted": {j.url for _, j in suggested if tailor.load_draft(j, output_dir)},
            "target_of": target_of,
        })

    # ---------------- tailoring & drafts ----------------

    @app.post("/tailor")
    def make_draft(url: str = Form(...), conn=Depends(get_conn)):
        job = find_job(conn, url)
        if job is None:
            return _back("That job isn't in your saved list anymore.")
        description = job.description  # Lever, Ashby, and USAJOBS include it already
        board = board_registry.board_for(conn, job, default_boards)
        if not description and board and registry.split_key(board)[0] == "greenhouse":
            try:
                description = describe(registry.split_key(board)[1], job.source_id)
            except Exception:
                description = ""
        try:
            tailor_fn(job, description, profile_dir=profile_dir, output_dir=output_dir)
        except AIError as error:
            return _back(f"Couldn't tailor: {error}")
        note = "" if description else " (the full job description couldn't be loaded, so it used the title only)"
        return _back(f"Draft ready{note}. Review it before you apply.", to=f"/draft?url={quote(url)}")

    @app.get("/draft", response_class=HTMLResponse)
    def view_draft(request: Request, url: str, conn=Depends(get_conn)):
        job = find_job(conn, url)
        draft = tailor.load_draft(job, output_dir) if job else None
        if draft is None:
            return _back("No draft for that job yet. Click Tailor first.")
        source = registry.split_key(board_registry.board_for(conn, job, default_boards) or "")[0]
        return page(request, "draft.html", {
            "job": job, "draft": draft, "apply_status": assisted_apply.read_status(draft.folder),
            "fillable": job.source != "usajobs",
            "has_answers": "application_answers" in (applicant.read_profile_text(profile_dir) or ""),
            "source": source,
        })

    @app.post("/prepare")
    def prepare(url: str = Form(...), conn=Depends(get_conn)):
        job = find_job(conn, url)
        draft = tailor.load_draft(job, output_dir) if job else None
        if draft is None:
            return _back("Tailor this job first, then prepare the application.")
        assisted_apply.write_status(draft.folder, assisted_apply.Status("preparing", "Starting Chrome…"))
        launch_apply(url, draft.folder)
        return _back("Opening the application in a new Chrome window (about 10–20 seconds). "
                     "Review every field there, then click Submit yourself.", to=f"/draft?url={quote(url)}")

    @app.post("/draft/save")
    def save_draft(url: str = Form(...), resume: str = Form(...), cover_letter: str = Form(...),
                   conn=Depends(get_conn)):
        job = find_job(conn, url)
        if job is None:
            return _back("That job isn't in your saved list anymore.")
        tailor.save_draft(tailor.draft_folder(job, output_dir), resume.strip(), cover_letter.strip())
        return _back("Saved your edits.", to=f"/draft?url={quote(url)}")

    @app.get("/print", response_class=HTMLResponse)
    def print_view(request: Request, url: str, doc: str = "resume", conn=Depends(get_conn)):
        job = find_job(conn, url)
        draft = tailor.load_draft(job, output_dir) if job else None
        if draft is None:
            return _back("No draft for that job yet.")
        text = draft.cover_letter if doc == "cover_letter" else draft.resume
        return TEMPLATES.TemplateResponse(request, "print.html", {"job": job, "text": text, "doc": doc})

    @app.get("/history", response_class=HTMLResponse)
    def history(request: Request, url: str, conn=Depends(get_conn)):
        jobs_by_url = {job.url: job for job in storage.load_jobs(conn)}
        return page(request, "history.html", {
            "url": url, "job": jobs_by_url.get(url), "events": tracker.history(conn, url),
        })

    # ---------------- profile ----------------

    @app.get("/profile", response_class=HTMLResponse)
    def profile_page(request: Request, start: str = ""):
        saved = applicant.read_profile_text(profile_dir)
        draft_file = profile_dir / "profile.draft.yaml"
        text, source = saved, "saved"
        if start == "template":
            text, source = applicant.BLANK_TEMPLATE, "template"
        elif start == "ai" and draft_file.exists():
            text, source = draft_file.read_text(), "ai"

        summary, error = None, ""
        if text:
            try:
                summary = applicant.parse_profile(text)
            except ValueError as problem:
                error = str(problem)
        resume_status = {}
        if summary:
            for category, name in (summary.get("base_resumes") or {}).items():
                found = applicant.find_resume(profile_dir, name) if name else None
                resume_status[category] = {"name": name, "found": found.name if found else None}
        return page(request, "profile.html", {
            "text": text, "source": source, "summary": summary, "error": error,
            "resume_status": resume_status, "resume_count": len(applicant.list_resumes(profile_dir)),
            "folder": profile_dir.resolve(),
        })

    @app.post("/profile")
    def save_profile(text: str = Form(...)):
        try:
            applicant.save_profile_text(profile_dir, text)
        except ValueError as error:
            return _back(f"Not saved: {error}", to="/profile")
        (profile_dir / "profile.draft.yaml").unlink(missing_ok=True)
        return _back("Profile saved. Tailoring will use it from now on.", to="/profile")

    @app.post("/profile/build")
    def build_profile():
        try:
            draft = applicant.build_profile_with_ai(profile_dir, client=ai_client)
        except (AIError, ValueError) as error:
            return _back(f"Couldn't build a profile: {error}", to="/profile")
        (profile_dir / "profile.draft.yaml").write_text(draft + "\n")
        return _back("Draft built from your resumes. Fix every TODO, then click Save.",
                     to="/profile?start=ai")

    # ---------------- GitHub projects ----------------

    def _profile_data() -> dict:
        text = applicant.read_profile_text(profile_dir)
        try:
            return applicant.parse_profile(text) if text else {}
        except ValueError:
            return {}

    @app.get("/github", response_class=HTMLResponse)
    def github_page(request: Request):
        profile = _profile_data()
        username = github.username_from(profile)
        fetched_for, repos = github.load_repos(profile_dir)
        if fetched_for != username:
            repos = []                                   # fetched for a different account: refresh
        for repo in repos:
            repo.in_profile = github.matches_project(repo, profile.get("projects") or [])
        return page(request, "github.html", {
            "username": username, "repos": repos, "draft": github.load_draft(profile_dir),
            "has_profile": bool(profile), "max_selected": github.MAX_SELECTED,
        })

    @app.post("/github/refresh")
    def github_refresh():
        profile = _profile_data()
        username = github.username_from(profile)
        if not username:
            return _back("Add your GitHub link to your profile (links: github:) first.", to="/github")
        try:
            repos = github.fetch_repos(username, profile, session=github_session)
        except github.GitHubError as error:
            return _back(str(error), to="/github")
        github.save_repos(profile_dir, username, repos)
        new = sum(not r.in_profile for r in repos)
        return _back(f"Found {len(repos)} public repos; {new} aren't in your profile yet.", to="/github")

    @app.post("/github/draft")
    def github_draft(repo: list[str] = Form(default=[])):
        profile = _profile_data()
        username, repos = github.load_repos(profile_dir)
        selected = [r for r in repos if r.name in set(repo)]
        if len(selected) > github.MAX_SELECTED:
            return _back(f"Pick up to {github.MAX_SELECTED} repos at a time.", to="/github")
        try:
            draft = github.draft_projects(username, selected, profile, session=github_session, client=ai_client)
        except (github.GitHubError, AIError) as error:
            return _back(str(error), to="/github")
        github.save_draft(profile_dir, draft)
        return _back("Draft ready. Read every line, fix anything that's off, then click Add.", to="/github")

    @app.post("/github/add")
    def github_add(text: str = Form("")):
        try:
            added = github.add_to_profile(profile_dir, github.parse_entries(text))
        except (github.GitHubError, ValueError) as error:
            return _back(f"Not added: {error}", to="/github")
        github.clear_draft(profile_dir)
        if not added:
            return _back("Those projects are already in your profile.", to="/github")
        return _back(f"Added {added} project(s) to your profile. Tailored resumes can use them now.", to="/github")

    @app.post("/github/discard")
    def github_discard():
        github.clear_draft(profile_dir)
        return _back("Draft discarded.", to="/github")

    # ---------------- resumes ----------------

    def _base_for(name: str) -> list[str]:
        text = applicant.read_profile_text(profile_dir)
        try:
            data = applicant.parse_profile(text) if text else {}
        except ValueError:
            data = {}
        uses = []
        for category, wanted in (data.get("base_resumes") or {}).items():
            found = applicant.find_resume(profile_dir, wanted) if wanted else None
            if found and found.name == name:
                uses.append(category)
        return uses

    @app.get("/resumes", response_class=HTMLResponse)
    def resumes(request: Request):
        files = applicant.list_resumes(profile_dir)
        rows = [{"name": p.name, "kb": max(1, p.stat().st_size // 1024), "uses": _base_for(p.name),
                 "has_feedback": applicant.load_feedback(profile_dir, p) is not None} for p in files]
        return page(request, "resumes.html", {"rows": rows, "folder": profile_dir.resolve()})

    @app.get("/resumes/view", response_class=HTMLResponse)
    def resume_view(request: Request, name: str):
        path = applicant.resume_by_filename(profile_dir, name)
        if path is None:
            return _back("That resume isn't in your profile folder.", to="/resumes")
        return page(request, "resume_view.html", {
            "name": path.name, "is_pdf": path.suffix.lower() == ".pdf", "uses": _base_for(path.name),
            "feedback": applicant.load_feedback(profile_dir, path),
            "text": applicant.resume_text(path),
        })

    @app.get("/resumes/file")
    def resume_file(name: str):
        path = applicant.resume_by_filename(profile_dir, name)
        if path is None:
            return _back("That resume isn't in your profile folder.", to="/resumes")
        media = "application/pdf" if path.suffix.lower() == ".pdf" else "text/plain"
        return FileResponse(path, media_type=media, content_disposition_type="inline")

    @app.post("/resumes/feedback")
    def resume_feedback(name: str = Form(...)):
        path = applicant.resume_by_filename(profile_dir, name)
        if path is None:
            return _back("That resume isn't in your profile folder.", to="/resumes")
        try:
            applicant.review_resume_with_ai(profile_dir, path, client=ai_client)
        except AIError as error:
            return _back(f"Couldn't review it: {error}", to=f"/resumes/view?name={quote(name)}")
        return _back("Suggestions ready.", to=f"/resumes/view?name={quote(name)}")

    return app


app = create_app()
