"""The control page: see the worker and each lecture, start or stop it, retry, and read the notes.

Runs on this machine only (bound to 127.0.0.1). It never records or transcribes itself: Start
launches the same `python -m notetaker run` that the command line uses, and Stop asks that worker
to finish its current part. Buttons that would fight a running worker are refused.
"""

import subprocess
import sys
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from notetaker import store
from notetaker.config import Config
from notetaker.db import connect
from notetaker.export import lecture_filename
from notetaker.worker import AlreadyRunning, single_instance

TEMPLATES = Path(__file__).parent / "render" / "templates"
RECORDER_CHROME = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]
STAGE_LABEL = {
    "capture": "recording",
    "transcribe": "transcribing",
    "frames": "sampling slides",
    "notes": "writing notes",
    "cleanup": "cleaning up",
}


def is_running(cfg: Config) -> bool:
    try:
        with single_instance(cfg.lock_path):
            return False
    except AlreadyRunning:
        return True


def worker_state(conn, cfg: Config) -> dict:
    running = is_running(cfg)
    stopping = cfg.stop_path.exists()
    now = time.time()
    current = conn.execute(
        "SELECT l.title, j.stage FROM jobs j JOIN lectures l ON l.id = j.lecture_id "
        "WHERE j.state = 'running' LIMIT 1"
    ).fetchone()
    waiting = conn.execute(
        "SELECT error, next_run_at FROM jobs WHERE state = 'queued' AND error IS NOT NULL "
        "ORDER BY next_run_at LIMIT 1"
    ).fetchone()
    if running and stopping:
        label = "stopping (finishing the current part)"
    elif running:
        label = "running"
    else:
        label = "stopped"
    usage = waiting is not None and (waiting["error"] or "").startswith("Claude usage limit")
    return {
        "running": running,
        "stopping": stopping,
        "label": label,
        "current": (
            f"{current['title']}: {STAGE_LABEL.get(current['stage'], current['stage'])}"
            if current
            else None
        ),
        "waiting": waiting["error"] if waiting and not usage else None,
        "waiting_until": (
            time.strftime("%H:%M", time.localtime(waiting["next_run_at"]))
            if waiting and waiting["next_run_at"] > now
            else None
        ),
        "usage_limit": waiting["error"] if usage else None,
    }


def _course_rows(conn, cfg: Config) -> list[dict]:
    rows = []
    for c in conn.execute("SELECT * FROM courses ORDER BY id"):
        lectures = []
        for lec in conn.execute(
            "SELECT * FROM lectures WHERE course_id = ? ORDER BY idx", (c["id"],)
        ):
            job = conn.execute(
                "SELECT stage, state, error FROM jobs WHERE lecture_id = ? AND state != 'done' "
                "ORDER BY id DESC LIMIT 1",
                (lec["id"],),
            ).fetchone()
            has_notes = store.stored_notes(conn, lec["id"]) is not None
            page = f"/notes/course-{c['id']}/{lecture_filename(lec['idx'])}"
            lectures.append(
                {
                    "id": lec["id"],
                    "idx": lec["idx"],
                    "title": lec["title"],
                    "status": lec["status"],
                    "detail": (
                        f"{STAGE_LABEL.get(job['stage'], job['stage'])} {job['state']}"
                        + (f": {job['error']}" if job["error"] else "")
                        if job
                        else ""
                    ),
                    "page": page if has_notes else None,
                }
            )
        done = sum(1 for lec in lectures if lec["status"] == "done")
        rows.append(
            {
                "id": c["id"],
                "title": c["title"] or c["url"],
                "status": c["status"],
                "done": done,
                "total": len(lectures),
                "index": f"/notes/course-{c['id']}/index.html",
                "lectures": lectures,
            }
        )
    return rows


def create_app(cfg: Config) -> FastAPI:
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    app = FastAPI(title="notetaker")
    templates = Jinja2Templates(directory=str(TEMPLATES))
    cfg.notes_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/notes", StaticFiles(directory=str(cfg.notes_dir), html=True), name="notes")

    def conn():
        return connect(cfg.db_path)

    def panel(request: Request, message: str | None = None):
        c = conn()
        try:
            ctx = {
                "state": worker_state(c, cfg),
                "courses": _course_rows(c, cfg),
                "message": message,
            }
        finally:
            c.close()
        return templates.TemplateResponse(request, "control_panel.html", ctx)

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        c = conn()
        try:
            ctx = {
                "state": worker_state(c, cfg),
                "courses": _course_rows(c, cfg),
                "message": None,
            }
        finally:
            c.close()
        return templates.TemplateResponse(request, "control.html", ctx)

    @app.get("/panel", response_class=HTMLResponse)
    def fragment(request: Request):
        return panel(request)

    @app.post("/worker/start", response_class=HTMLResponse)
    def start(request: Request):
        if is_running(cfg):
            return panel(request, "The worker is already running.")
        cmd = [sys.executable, "-m", "notetaker", "run"]
        kwargs: dict = {"cwd": str(Path.cwd()), "stdout": subprocess.DEVNULL}
        if sys.platform == "win32":
            kwargs["creationflags"] = (
                subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            )
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen(cmd, **kwargs)
        time.sleep(1.5)  # give it a moment to take the lock before the panel is redrawn
        return panel(request, "Started. It runs until you press Stop or the Claude usage runs out.")

    @app.post("/worker/stop", response_class=HTMLResponse)
    def stop(request: Request):
        if is_running(cfg):
            cfg.stop_path.write_text("stop requested\n", encoding="utf-8")
            return panel(
                request, "Stop requested: the current part is saved, then the worker exits."
            )
        return panel(request, "The worker is not running.")

    @app.post("/lecture/{lecture_id}/retry", response_class=HTMLResponse)
    def retry(request: Request, lecture_id: int):
        c = conn()
        try:
            if store.lecture(c, lecture_id) is None:
                raise HTTPException(404, "no such lecture")
            n = c.execute(
                "UPDATE jobs SET state = 'queued', attempts = 0, next_run_at = 0, error = NULL "
                "WHERE lecture_id = ? AND state = 'failed'",
                (lecture_id,),
            ).rowcount
            c.execute(
                "UPDATE lectures SET status = 'running' WHERE id = ? AND status = 'failed'",
                (lecture_id,),
            )
            c.execute("UPDATE courses SET status = 'active' WHERE status = 'needs_signin'")
        finally:
            c.close()
        msg = "Queued for retry. Press Start." if n else "Nothing failed for this lecture."
        return panel(request, msg)

    @app.post("/course/{course_id}/resume", response_class=HTMLResponse)
    def resume(request: Request, course_id: int):
        c = conn()
        try:
            c.execute(
                "UPDATE courses SET status = 'active' WHERE id = ? AND status != 'active'",
                (course_id,),
            )
        finally:
            c.close()
        return panel(request, "Course resumed. Press Start to run it.")

    @app.post("/signin", response_class=HTMLResponse)
    def signin(request: Request):
        """Open the recorder Chrome with its own profile, so the owner can sign in once."""
        exe = next((p for p in RECORDER_CHROME if Path(p).exists()), None)
        if exe is None:
            return panel(request, "Chrome was not found in its usual place.")
        if is_running(cfg):
            return panel(request, "Stop the worker before signing in.")
        subprocess.Popen(
            [
                exe,
                "--remote-debugging-port=9222",
                f"--user-data-dir={(cfg.data_dir / 'chrome-profile').resolve()}",
                "https://100xdevs.com",
            ]
        )
        return panel(request, "Sign in in the Chrome window that opened, then return here.")

    return app


def serve(cfg: Config, port: int = 8765) -> None:
    import uvicorn

    print(f"control page: http://127.0.0.1:{port}")
    uvicorn.run(create_app(cfg), host="127.0.0.1", port=port, log_level="warning")
