import pytest
from fastapi.testclient import TestClient

from notetaker import jobs, store
from notetaker.config import Config
from notetaker.db import connect
from notetaker.web import create_app
from notetaker.worker import single_instance


@pytest.fixture
def app_env(tmp_path):
    cfg = Config(data_dir=tmp_path)
    conn = connect(cfg.db_path)
    cid = store.add_course(conn, "https://x/course", "AI and ML")
    store.add_lectures(conn, cid, [("Intro", "https://x/v/1"), ("Attention", "https://x/v/2")])
    return cfg, conn, TestClient(create_app(cfg))


def test_the_page_shows_the_worker_the_course_and_every_lecture(app_env):
    _cfg, _conn, client = app_env
    page = client.get("/")
    assert page.status_code == 200
    for text in ("AI and ML", "Intro", "Attention", "stopped", "0 of 2 lectures done"):
        assert text in page.text


def test_stop_when_nothing_is_running_does_nothing(app_env):
    cfg, _conn, client = app_env
    r = client.post("/worker/stop")
    assert "not running" in r.text
    assert not cfg.stop_path.exists()


def test_stop_asks_a_running_worker_to_finish_its_part(app_env):
    cfg, _conn, client = app_env
    with single_instance(cfg.lock_path):  # stands in for a running worker
        r = client.post("/worker/stop")
        assert "Stop requested" in r.text and "stopping" in r.text
    assert cfg.stop_path.exists()


def test_start_is_refused_while_a_worker_is_running(app_env):
    cfg, _conn, client = app_env
    with single_instance(cfg.lock_path):
        r = client.post("/worker/start")
    assert "already running" in r.text


def test_a_failed_lecture_can_be_retried_from_the_page(app_env):
    _cfg, conn, client = app_env
    jobs.enqueue_lecture(conn, 1)
    conn.execute("UPDATE jobs SET state = 'failed', attempts = 5, error = 'boom'")
    conn.execute("UPDATE lectures SET status = 'failed' WHERE id = 1")
    assert "boom" in client.get("/panel").text
    r = client.post("/lecture/1/retry")
    assert "Queued for retry" in r.text
    row = conn.execute("SELECT state, attempts, error FROM jobs").fetchone()
    assert (row["state"], row["attempts"], row["error"]) == ("queued", 0, None)
    assert conn.execute("SELECT status FROM lectures WHERE id = 1").fetchone()[0] == "running"


def test_retry_of_an_unknown_lecture_is_a_404(app_env):
    _cfg, _conn, client = app_env
    assert client.post("/lecture/99/retry").status_code == 404


def test_a_claude_usage_limit_is_shown_with_what_to_do(app_env):
    _cfg, conn, client = app_env
    jobs.enqueue_lecture(conn, 1)
    conn.execute(
        "UPDATE jobs SET state = 'queued', error = 'Claude usage limit: resets at 9pm', "
        "next_run_at = 9999999999"
    )
    text = client.get("/panel").text
    assert "Claude usage is used up" in text and "resets at 9pm" in text


def test_a_paused_course_can_be_resumed(app_env):
    _cfg, conn, client = app_env
    conn.execute("UPDATE courses SET status = 'needs_signin'")
    assert "Resume course" in client.get("/panel").text
    client.post("/course/1/resume")
    assert conn.execute("SELECT status FROM courses").fetchone()[0] == "active"


def test_finished_notes_are_linked_and_served(app_env):
    cfg, conn, client = app_env
    from notetaker.notes.model import Block, LectureNotes, Section
    from notetaker.notes.writer import ledger_entries

    notes = LectureNotes(
        title="Intro",
        lead="What a model is.",
        sections=[
            Section(
                heading="Models",
                status="new",
                concept="Models",
                summary="s",
                key_points=["k"],
                blocks=[Block(type="text", text="body")],
            )
        ],
    )
    store.save_notes(conn, 1, 1, 1, notes, ledger_entries(notes, 1), "m", covered_until=10.0)
    conn.execute("UPDATE lectures SET status = 'done' WHERE id = 1")
    from notetaker.export import export_course

    export_course(conn, 1, cfg.notes_dir / "course-1")
    assert "/notes/course-1/lecture-01.html" in client.get("/panel").text
    page = client.get("/notes/course-1/lecture-01.html")
    assert page.status_code == 200 and "Models" in page.text
