"""Runs queued lecture jobs one at a time, in lecture order, until stopped or finished.

Start runs until one of these happens: the owner asks it to stop (the current part is saved), the
Claude usage is used up (the work waits for the next Start), or every lecture is finished.
Device busy and sign-in expiry are short waits, retried while the worker runs.
"""

import os
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from notetaker import jobs, store
from notetaker.capture.record import DeviceBusy, NeedsSignIn
from notetaker.config import Config
from notetaker.notes.claude_writer import UsageLimited
from notetaker.pipeline import STAGE_FUNCS, Ctx, lecture_finished

BUSY_RETRY_S = 600  # someone is watching on another device: try again in ten minutes
SIGNIN_RETRY_S = 3600  # sign-in expired: wait for the owner to sign in again


class AlreadyRunning(RuntimeError):
    pass


@contextmanager
def single_instance(path: Path) -> Iterator[None]:
    """Hold an OS file lock for the life of the worker. A crashed worker releases it on its own."""
    path.parent.mkdir(parents=True, exist_ok=True)
    f = path.open("a+")
    try:
        f.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as e:
        f.close()
        raise AlreadyRunning(f"another worker is running (lock {path})") from e
    try:
        yield
    finally:
        f.close()


def stop_requested(cfg: Config) -> bool:
    return cfg.stop_path.exists()


def advance(conn: sqlite3.Connection) -> int:
    """Queue the next lecture of every active course whose previous lecture is finished."""
    started = 0
    for c in conn.execute("SELECT id FROM courses WHERE status = 'active' ORDER BY id").fetchall():
        nxt = store.next_lecture_to_start(conn, c["id"])
        if nxt is not None:
            jobs.enqueue_lecture(conn, nxt["id"])
            started += 1
    return started


def _work_left(conn: sqlite3.Connection) -> bool:
    """Is there anything the worker could still do without a new Start?"""
    if conn.execute("SELECT 1 FROM jobs WHERE state IN ('queued', 'running') LIMIT 1").fetchone():
        return True
    return any(
        store.next_lecture_to_start(conn, r["id"]) is not None
        for r in conn.execute("SELECT id FROM courses WHERE status = 'active'")
    )


def _blocked_by_usage(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM jobs WHERE state = 'queued' AND error LIKE 'Claude usage limit%' LIMIT 1"
        ).fetchone()
        is not None
    )


def run_job(
    conn: sqlite3.Connection,
    cfg: Config,
    job: sqlite3.Row,
    stages: dict[str, Callable[[Ctx], None]] = STAGE_FUNCS,
    now: float | None = None,
    should_stop: Callable[[], bool] = lambda: False,
) -> str:
    """Run one claimed job.

    Returns done, partial (the lecture is not finished and waits for the next run), busy,
    needs_signin, usage_limit or failed.
    """
    lec = store.lecture(conn, job["lecture_id"])
    course = store.course(conn, lec["course_id"])
    ctx = Ctx(conn=conn, cfg=cfg, lecture=lec, course=course, should_stop=should_stop)
    try:
        stages[job["stage"]](ctx)
    except NeedsSignIn as e:
        conn.execute("UPDATE courses SET status = 'needs_signin' WHERE id = ?", (course["id"],))
        jobs.defer(conn, job["id"], SIGNIN_RETRY_S, f"needs sign-in ({e})", now)
        return "needs_signin"
    except DeviceBusy as e:
        jobs.defer(conn, job["id"], BUSY_RETRY_S, f"another device is playing ({e})", now)
        return "busy"
    except UsageLimited as e:
        # The Claude usage is used up: keep the work and stop; the next Start carries on.
        jobs.defer(conn, job["id"], e.retry_after_s, f"Claude usage limit: {e}", now)
        return "usage_limit"
    except Exception as e:  # noqa: BLE001 - any stage error becomes a retry or a recorded failure
        jobs.fail(conn, job["id"], f"{type(e).__name__}: {e}", now)
        return "failed"
    if job["stage"] == "notes" and not lecture_finished(cfg.work_dir / f"lecture-{lec['id']}"):
        jobs.finish_partial(conn, job["id"])
        return "partial"
    jobs.complete(conn, job["id"])
    return "done"


def work(
    conn: sqlite3.Connection,
    cfg: Config,
    *,
    log: Callable[[str], None] = print,
    stages: dict[str, Callable[[Ctx], None]] = STAGE_FUNCS,
    idle_s: float = 30,
) -> str:
    """Process jobs until stopped, out of Claude usage, or finished. Returns why it stopped."""
    with single_instance(cfg.lock_path):
        cfg.stop_path.unlink(missing_ok=True)  # a stop left over from before this start is void
        # Anything still marked running was left by a crashed worker, so it runs again.
        conn.execute("UPDATE jobs SET state = 'queued' WHERE state = 'running'")
        told = False
        while True:
            stopping = stop_requested(cfg)
            if not stopping:
                advance(conn)
            job = jobs.claim_next(conn)
            if job is None:
                if stopping:
                    return "stopped"
                if _blocked_by_usage(conn):  # no point waiting an hour in the loop: say so and stop
                    return "usage_limit"
                if not _work_left(conn):
                    return "finished"
                if not told:
                    log("waiting: device busy, sign-in needed or a retry is scheduled")
                    told = True
                time.sleep(idle_s)
                continue
            told = False
            lec = store.lecture(conn, job["lecture_id"])
            log(f"lecture {lec['idx']} ({lec['title']}): {job['stage']} ...")
            outcome = run_job(conn, cfg, job, stages, should_stop=lambda: stop_requested(cfg))
            log(f"lecture {lec['idx']}: {job['stage']} -> {outcome}")
            if outcome == "usage_limit":
                return "usage_limit"
