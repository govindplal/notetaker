"""Runs queued lecture jobs one at a time, in lecture order."""

import sqlite3
import time
from collections.abc import Callable

from notetaker import jobs, store
from notetaker.capture.record import DeviceBusy, NeedsSignIn
from notetaker.config import Config
from notetaker.pipeline import STAGE_FUNCS, Ctx

BUSY_RETRY_S = 600  # someone is watching on another device: try again in ten minutes
SIGNIN_RETRY_S = 3600  # sign-in expired: wait for the owner to sign in again


def advance(conn: sqlite3.Connection) -> int:
    """Queue the next lecture of every active course whose previous lecture is finished."""
    started = 0
    for c in conn.execute("SELECT id FROM courses WHERE status = 'active' ORDER BY id").fetchall():
        nxt = store.next_lecture_to_start(conn, c["id"])
        if nxt is not None:
            jobs.enqueue_lecture(conn, nxt["id"])
            started += 1
    return started


def run_job(
    conn: sqlite3.Connection,
    cfg: Config,
    job: sqlite3.Row,
    stages: dict[str, Callable[[Ctx], None]] = STAGE_FUNCS,
    now: float | None = None,
) -> str:
    """Run one claimed job. Returns done, busy, needs_signin or failed."""
    lec = store.lecture(conn, job["lecture_id"])
    course = store.course(conn, lec["course_id"])
    ctx = Ctx(conn=conn, cfg=cfg, lecture=lec, course=course)
    try:
        stages[job["stage"]](ctx)
    except NeedsSignIn as e:
        conn.execute("UPDATE courses SET status = 'needs_signin' WHERE id = ?", (course["id"],))
        jobs.defer(conn, job["id"], SIGNIN_RETRY_S, f"needs sign-in ({e})", now)
        return "needs_signin"
    except DeviceBusy as e:
        jobs.defer(conn, job["id"], BUSY_RETRY_S, f"another device is playing ({e})", now)
        return "busy"
    except Exception as e:  # noqa: BLE001 - any stage error becomes a retry or a recorded failure
        jobs.fail(conn, job["id"], f"{type(e).__name__}: {e}", now)
        return "failed"
    jobs.complete(conn, job["id"])
    return "done"


def work(
    conn: sqlite3.Connection,
    cfg: Config,
    *,
    stop_when_idle: bool = True,
    idle_s: float = 30,
    log: Callable[[str], None] = print,
    stages: dict[str, Callable[[Ctx], None]] = STAGE_FUNCS,
) -> None:
    """Process jobs until none are ready (or forever, polling, when stop_when_idle is False)."""
    # One worker runs at a time, so anything still marked running is left over from a crash.
    conn.execute("UPDATE jobs SET state = 'queued' WHERE state = 'running'")
    told = False
    while True:
        advance(conn)
        job = jobs.claim_next(conn)
        if job is None:
            waiting = conn.execute("SELECT COUNT(*) FROM jobs WHERE state = 'queued'").fetchone()[0]
            if waiting and not told:
                log(
                    f"{waiting} job(s) waiting to retry (another device playing, or sign-in needed)."
                )
                told = True
            if not waiting and stop_when_idle:
                return
            time.sleep(idle_s)
            continue
        told = False
        lec = store.lecture(conn, job["lecture_id"])
        log(f"lecture {lec['idx']} ({lec['title']}): {job['stage']} ...")
        outcome = run_job(conn, cfg, job, stages)
        log(f"lecture {lec['idx']}: {job['stage']} -> {outcome}")
