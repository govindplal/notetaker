import pytest

from notetaker import jobs
from notetaker.db import connect


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    c.execute("INSERT INTO courses (id, url) VALUES (1, 'https://example.com/c')")
    c.execute("INSERT INTO lectures (id, course_id, idx, title, url) VALUES (1, 1, 0, 'L1', 'u1')")
    c.execute("INSERT INTO lectures (id, course_id, idx, title, url) VALUES (2, 1, 1, 'L2', 'u2')")
    return c


def test_enqueue_is_idempotent(conn):
    jobs.enqueue_lecture(conn, 1)
    jobs.enqueue_lecture(conn, 1)
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


def test_claim_returns_none_when_empty(conn):
    assert jobs.claim_next(conn) is None


def test_claim_marks_running_and_counts_attempt(conn):
    jobs.enqueue_lecture(conn, 1)
    job = jobs.claim_next(conn)
    assert job["stage"] == "capture"
    row = conn.execute("SELECT state, attempts FROM jobs WHERE id = ?", (job["id"],)).fetchone()
    assert (row["state"], row["attempts"]) == ("running", 1)
    assert jobs.claim_next(conn) is None  # already taken


def test_complete_walks_every_stage_then_marks_lecture_done(conn):
    jobs.enqueue_lecture(conn, 1)
    seen = []
    while (job := jobs.claim_next(conn)) is not None:
        seen.append(job["stage"])
        jobs.complete(conn, job["id"])
    assert tuple(seen) == jobs.STAGES
    assert conn.execute("SELECT status FROM lectures WHERE id = 1").fetchone()[0] == "done"


def test_fail_backs_off_exponentially(conn):
    jobs.enqueue_lecture(conn, 1)
    job = jobs.claim_next(conn, now=1000)
    jobs.fail(conn, job["id"], "boom", now=1000)
    assert jobs.claim_next(conn, now=1000 + jobs.BACKOFF_BASE_S - 1) is None
    job = jobs.claim_next(conn, now=1000 + jobs.BACKOFF_BASE_S)
    assert job is not None
    jobs.fail(conn, job["id"], "boom", now=2000)
    row = conn.execute("SELECT next_run_at, error FROM jobs").fetchone()
    assert row["next_run_at"] == 2000 + jobs.BACKOFF_BASE_S * 2
    assert row["error"] == "boom"


def test_fail_gives_up_after_max_attempts(conn):
    jobs.enqueue_lecture(conn, 1)
    now = 1000.0
    for _ in range(jobs.MAX_ATTEMPTS):
        now += 10**6  # past any backoff
        job = jobs.claim_next(conn, now=now)
        jobs.fail(conn, job["id"], "boom", now=now)
    assert conn.execute("SELECT state FROM jobs").fetchone()[0] == "failed"
    assert conn.execute("SELECT status FROM lectures WHERE id = 1").fetchone()[0] == "failed"
    assert jobs.claim_next(conn, now=now + 10**9) is None


def test_requeue_stale_recovers_crashed_running_jobs(conn):
    jobs.enqueue_lecture(conn, 1)
    jobs.claim_next(conn, now=1000)
    assert jobs.requeue_stale(conn, now=1000 + jobs.STALE_RUNNING_S - 1) == 0
    assert jobs.requeue_stale(conn, now=1000 + jobs.STALE_RUNNING_S) == 1
    assert jobs.claim_next(conn, now=1000 + jobs.STALE_RUNNING_S) is not None


def test_completed_stage_is_not_redone_after_restart(conn):
    jobs.enqueue_lecture(conn, 1)
    first = jobs.claim_next(conn)
    jobs.complete(conn, first["id"])
    jobs.enqueue_lecture(conn, 1)  # e.g. the course is re-scanned after a restart
    nxt = jobs.claim_next(conn)
    assert nxt["stage"] == "transcribe"
