import json
from pathlib import Path

import pytest

from notetaker import jobs, pipeline, store, worker
from notetaker.capture.record import DeviceBusy, NeedsSignIn
from notetaker.config import Config
from notetaker.db import connect
from notetaker.export import export_course
from notetaker.notes.model import Block, GlossaryItem, LectureNotes, Section
from notetaker.notes.writer import ledger_entries


@pytest.fixture
def env(tmp_path):
    cfg = Config(data_dir=tmp_path, gemini_api_key="k")
    conn = connect(cfg.db_path)
    cid = store.add_course(conn, "https://x/course", "AI and ML")
    store.add_lectures(conn, cid, [("Intro", "https://x/v/1"), ("Attention", "https://x/v/2")])
    return cfg, conn, cid


def _recording_stages(calls, boom=None):
    def make(stage):
        def run(ctx):
            calls.append((ctx.lecture["idx"], stage))
            if boom and boom[0] == (ctx.lecture["idx"], stage) and boom[1]:
                raise boom[1].pop(0)

        return run

    return {s: make(s) for s in jobs.STAGES}


def test_lectures_run_in_order_and_one_at_a_time(env):
    cfg, conn, cid = env
    calls = []
    worker.work(conn, cfg, log=lambda _m: None, stages=_recording_stages(calls))
    assert calls == [(i, s) for i in (1, 2) for s in jobs.STAGES]
    assert [r["status"] for r in conn.execute("SELECT status FROM lectures ORDER BY idx")] == [
        "done",
        "done",
    ]


def test_only_the_first_lecture_is_queued_at_first(env):
    _cfg, conn, _cid = env
    assert worker.advance(conn) == 1
    queued = conn.execute("SELECT lecture_id FROM jobs").fetchall()
    assert len(queued) == 1
    assert worker.advance(conn) == 0  # lecture 1 is in flight, so lecture 2 waits


def test_device_busy_waits_without_using_an_attempt(env):
    cfg, conn, _cid = env
    worker.advance(conn)
    job = jobs.claim_next(conn, now=1000)
    stages = _recording_stages([], boom=((1, "capture"), [DeviceBusy("tv")]))
    assert worker.run_job(conn, cfg, job, stages, now=1000) == "busy"
    row = conn.execute("SELECT state, attempts, next_run_at FROM jobs").fetchone()
    assert (row["state"], row["attempts"]) == ("queued", 0)
    assert row["next_run_at"] == 1000 + worker.BUSY_RETRY_S
    assert jobs.claim_next(conn, now=1000 + 5) is None
    assert jobs.claim_next(conn, now=1000 + worker.BUSY_RETRY_S) is not None


def test_sign_in_needed_pauses_the_course(env):
    cfg, conn, cid = env
    worker.advance(conn)
    job = jobs.claim_next(conn)
    stages = _recording_stages([], boom=((1, "capture"), [NeedsSignIn("/login")]))
    assert worker.run_job(conn, cfg, job, stages) == "needs_signin"
    assert store.course(conn, cid)["status"] == "needs_signin"
    assert worker.advance(conn) == 0  # paused courses are skipped


def test_repeated_failures_block_later_lectures(env):
    cfg, conn, _cid = env
    errors = [RuntimeError("boom")] * (jobs.MAX_ATTEMPTS + 2)
    stages = _recording_stages([], boom=((1, "transcribe"), errors))
    worker.advance(conn)
    now = 1000.0
    for _ in range(40):
        job = jobs.claim_next(conn, now=now)
        if job is None:
            now += 10**6
            if conn.execute("SELECT status FROM lectures WHERE idx = 1").fetchone()[0] == "failed":
                break
            continue
        worker.run_job(conn, cfg, job, stages, now=now)
    assert conn.execute("SELECT status FROM lectures WHERE idx = 1").fetchone()[0] == "failed"
    assert worker.advance(conn) == 0  # lecture 2 must wait for lecture 1


def _notes(title="T", heading="Tokens"):
    return LectureNotes(
        title=title,
        lead="Text becomes numbers. Then the model reads them.",
        sections=[
            Section(
                heading=heading,
                status="new",
                concept=heading,
                summary="s",
                key_points=["k1"],
                blocks=[Block(type="text", text="body")],
            )
        ],
        glossary=[GlossaryItem(term="Token", definition="A piece of text.")],
        left_out=["greeting"],
    )


def test_ledger_only_contains_earlier_lectures_and_saving_replaces(env):
    _cfg, conn, cid = env
    lec1, lec2 = [dict(r) for r in conn.execute("SELECT * FROM lectures ORDER BY idx")]
    n1 = _notes()
    store.save_notes(conn, cid, lec1["id"], 1, n1, ledger_entries(n1, 1), "m")
    n1b = _notes(heading="Embeddings")
    store.save_notes(conn, cid, lec1["id"], 1, n1b, ledger_entries(n1b, 1), "m")  # re-run replaces
    assert [c.title for c in store.ledger(conn, cid, 2)] == ["Embeddings"]
    assert store.ledger(conn, cid, 1) == []  # nothing before lecture 1
    assert store.ledger(conn, cid, 2)[0].key_points == ["k1"]
    assert lec2["idx"] == 2


def test_export_writes_pages_index_and_no_media(env, tmp_path):
    _cfg, conn, cid = env
    lec1 = conn.execute("SELECT * FROM lectures WHERE idx = 1").fetchone()
    n1 = _notes()
    store.save_notes(conn, cid, lec1["id"], 1, n1, ledger_entries(n1, 1), "m")
    conn.execute("UPDATE lectures SET status = 'done' WHERE id = ?", (lec1["id"],))
    out = tmp_path / "site"
    files = export_course(conn, cid, out)
    assert {f.name for f in files} == {"lecture-01.html", "index.html"}
    index = (out / "index.html").read_text(encoding="utf-8")
    assert "1 of 2 lectures have notes" in index and 'href="lecture-01.html"' in index
    assert "Attention" in index and "queued" in index  # pending lecture shown without a link
    page = (out / "lecture-01.html").read_text(encoding="utf-8")
    assert "Tokens" in page and "<img" not in page and "greeting" not in page


def _ctx(env):
    cfg, conn, cid = env
    lec = store.lecture(conn, 1)
    return pipeline.Ctx(conn=conn, cfg=cfg, lecture=lec, course=store.course(conn, cid))


def test_chunked_transcription_slows_audio_and_keeps_one_timeline(env, monkeypatch):
    ctx = _ctx(env)
    (ctx.work / "recording.webm").write_bytes(b"x")
    (ctx.work / "capture.json").write_text(json.dumps({"speed": 2.0, "lecture_seconds": 1200}))
    chunks = []
    for i in range(2):
        p = ctx.work / f"c{i}.mp3"
        p.write_bytes(b"x")
        chunks.append((i * 600.0, p))
    seen = {}

    def fake_extract(video, out, speed=None):
        seen["video"], seen["speed"] = video.name, speed
        out.write_bytes(b"a")
        return out

    monkeypatch.setattr(pipeline.media, "extract_audio", fake_extract)
    monkeypatch.setattr(pipeline.media, "split_audio", lambda a, d: chunks)
    monkeypatch.setattr(
        pipeline.gemini,
        "transcribe",
        lambda c, chunk: [{"start": 5.0, "end": 9.0, "text": chunk.name}],
    )
    ctx._client = object()
    pipeline.transcribe(ctx)
    assert seen == {
        "video": "recording.webm",
        "speed": 2.0,
    }  # audio only, slowed back; no video re-encode
    segs = json.loads((ctx.work / "transcript.json").read_text(encoding="utf-8"))
    assert [s["start"] for s in segs] == [5.0, 605.0]  # second chunk is offset by 600 s
    assert not (ctx.work / "audio.mp3").exists()
    assert (ctx.work / "recording.webm").exists()  # still needed for the frames stage


def test_frames_are_sampled_from_the_recording_with_lecture_time_stamps(env, monkeypatch):
    ctx = _ctx(env)
    rec = ctx.work / "recording.webm"
    rec.write_bytes(b"x")
    (ctx.work / "capture.json").write_text(json.dumps({"speed": 4.0, "lecture_seconds": 100}))
    called = {}

    def fake_sample(video, out_dir, every_s):
        called["video"], called["every_s"] = video.name, every_s
        out_dir.mkdir(parents=True, exist_ok=True)
        files = []
        for i in range(3):
            f = out_dir / f"raw_{i}.jpg"
            f.write_bytes(b"j")
            files.append((i * every_s, f))  # timestamps in recorded time, as ffmpeg gives them
        return files

    monkeypatch.setattr(pipeline.media, "sample_frames", fake_sample)
    monkeypatch.setattr(
        pipeline.media,
        "dedupe_frames",
        lambda raw: [pipeline.media.Frame(ts=ts, path=p) for ts, p in raw[::2]],
    )
    pipeline.frames(ctx)
    assert called == {"video": "recording.webm", "every_s": 0.5}  # 2 s of lecture = 0.5 s at 4x
    kept = json.loads((ctx.work / "frames.json").read_text(encoding="utf-8"))
    assert kept == [[0.0, "raw_0.jpg"], [4.0, "raw_2.jpg"]]  # lecture seconds: 0 and 2 * 2.0
    assert not (ctx.work / "frames" / "raw_1.jpg").exists()  # near-duplicates are deleted
    assert not rec.exists()


def test_cleanup_removes_everything(env):
    ctx = _ctx(env)
    (ctx.work / "transcript.json").write_text("[]")
    pipeline.cleanup(ctx)
    assert not Path(ctx.cfg.work_dir / f"lecture-{ctx.lecture['id']}").exists()


def test_jobs_left_running_by_a_crash_are_resumed(env):
    cfg, conn, _cid = env
    worker.advance(conn)
    jobs.claim_next(conn)  # worker "dies" while the job is running
    calls = []
    worker.work(conn, cfg, log=lambda _m: None, stages=_recording_stages(calls))
    assert (1, "capture") in calls
    assert conn.execute("SELECT status FROM lectures WHERE idx = 2").fetchone()[0] == "done"


def test_redo_resets_the_lecture_and_everything_after_it(env):
    _cfg, conn, cid = env
    for lec in conn.execute("SELECT * FROM lectures ORDER BY idx").fetchall():
        n = _notes(heading=f"T{lec['idx']}")
        store.save_notes(conn, cid, lec["id"], lec["idx"], n, ledger_entries(n, lec["idx"]), "m")
        conn.execute("UPDATE lectures SET status = 'done' WHERE id = ?", (lec["id"],))
        jobs.enqueue_lecture(conn, lec["id"])
    assert store.reset_from(conn, cid, 2) == 1  # only lecture 2 is at or after #2
    assert conn.execute("SELECT status FROM lectures WHERE idx = 1").fetchone()[0] == "done"
    assert conn.execute("SELECT status FROM lectures WHERE idx = 2").fetchone()[0] == "pending"
    assert [c.title for c in store.ledger(conn, cid, 3)] == ["T1"]  # lecture 2's concepts are gone
    assert conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 1
    assert store.reset_from(conn, cid, 1) == 2
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
