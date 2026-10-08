"""Resumable per-lecture job queue. One row per (lecture, stage); stages run in order."""

import sqlite3
import time

STAGES = ("capture", "transcribe", "frames", "notes", "cleanup")
MAX_ATTEMPTS = 5
BACKOFF_BASE_S = 30
STALE_RUNNING_S = 3600


def enqueue_lecture(conn: sqlite3.Connection, lecture_id: int) -> None:
    """Queue the first stage of a lecture. Safe to call twice."""
    conn.execute(
        "INSERT OR IGNORE INTO jobs (lecture_id, stage) VALUES (?, ?)",
        (lecture_id, STAGES[0]),
    )


def claim_next(conn: sqlite3.Connection, now: float | None = None) -> sqlite3.Row | None:
    """Atomically take the oldest ready job, or None. Lecture order is preserved by id."""
    now = time.time() if now is None else now
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT * FROM jobs WHERE state = 'queued' AND next_run_at <= ? ORDER BY id LIMIT 1",
            (now,),
        ).fetchone()
        if row is not None:
            conn.execute(
                "UPDATE jobs SET state = 'running', attempts = attempts + 1, next_run_at = ? "
                "WHERE id = ?",
                (now, row["id"]),
            )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return row


def complete(conn: sqlite3.Connection, job_id: int) -> None:
    """Mark a job done and queue the next stage (or mark the lecture done after the last)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        job = conn.execute("SELECT lecture_id, stage FROM jobs WHERE id = ?", (job_id,)).fetchone()
        conn.execute("UPDATE jobs SET state = 'done', error = NULL WHERE id = ?", (job_id,))
        i = STAGES.index(job["stage"])
        if i + 1 < len(STAGES):
            conn.execute(
                "INSERT OR IGNORE INTO jobs (lecture_id, stage) VALUES (?, ?)",
                (job["lecture_id"], STAGES[i + 1]),
            )
            conn.execute(
                "UPDATE lectures SET status = 'running' WHERE id = ?", (job["lecture_id"],)
            )
        else:
            conn.execute("UPDATE lectures SET status = 'done' WHERE id = ?", (job["lecture_id"],))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def fail(conn: sqlite3.Connection, job_id: int, error: str, now: float | None = None) -> None:
    """Retry with exponential backoff, or give up after MAX_ATTEMPTS."""
    now = time.time() if now is None else now
    attempts = conn.execute("SELECT attempts FROM jobs WHERE id = ?", (job_id,)).fetchone()[0]
    if attempts >= MAX_ATTEMPTS:
        conn.execute("UPDATE jobs SET state = 'failed', error = ? WHERE id = ?", (error, job_id))
        conn.execute(
            "UPDATE lectures SET status = 'failed' "
            "WHERE id = (SELECT lecture_id FROM jobs WHERE id = ?)",
            (job_id,),
        )
        return
    delay = BACKOFF_BASE_S * 2 ** (attempts - 1)
    conn.execute(
        "UPDATE jobs SET state = 'queued', error = ?, next_run_at = ? WHERE id = ?",
        (error, now + delay, job_id),
    )


def requeue_stale(conn: sqlite3.Connection, now: float | None = None) -> int:
    """After a crash, running jobs left behind go back to the queue. Returns how many."""
    now = time.time() if now is None else now
    cur = conn.execute(
        "UPDATE jobs SET state = 'queued' WHERE state = 'running' AND next_run_at <= ?",
        (now - STALE_RUNNING_S,),
    )
    return cur.rowcount
