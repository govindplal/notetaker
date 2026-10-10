import json
from pathlib import Path

import pytest

from notetaker import jobs, pipeline, store, worker
from notetaker.capture.record import DeviceBusy, NeedsSignIn, Recording
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


def _write_parts(ctx, parts, speed=2.0, duration=1500.0):
    state = {"duration": duration, "complete": True, "parts": []}
    for i, (start, end) in enumerate(parts, 1):
        name = f"part-{i:04d}.webm"
        (ctx.work / name).write_bytes(b"x")
        state["parts"].append({"file": name, "start": start, "end": end, "speed": speed})
    (ctx.work / "capture.json").write_text(json.dumps(state))


def _fake_recorder(calls, die_at=None, duration=1500.0, advance=None, error=None):
    """Stands in for the browser. Records (start_at, max_media_seconds) per call.

    With die_at=T the session reports progress up to lecture time T and then fails with `error`.
    """

    def record(cdp, url, out, speed, max_media_seconds, start_at, on_progress):
        calls.append((start_at, max_media_seconds))
        out.write_bytes(b"x")
        stop = start_at + (advance if advance is not None else (max_media_seconds or duration))
        t = start_at
        while t < min(stop, duration):
            t = min(t + 50.0, stop, duration)
            on_progress(t, duration)
            if die_at is not None and t >= die_at:
                raise error or RuntimeError("killed")
        end = min(stop, duration)
        return Recording(
            path=out, speed=speed, lecture_seconds=end - start_at, wall_seconds=1.0,
            start_t=start_at, end_t=end, duration=duration,
        )  # fmt: skip

    return record


def _state(ctx):
    return json.loads((ctx.work / "capture.json").read_text())


def test_an_error_mid_recording_keeps_what_was_recorded_and_the_restart_continues(env, monkeypatch):
    ctx = _ctx(env)
    calls = []
    monkeypatch.setattr(pipeline, "record_lecture", _fake_recorder(calls, die_at=600.0))
    with pytest.raises(RuntimeError):
        pipeline.capture(ctx)  # e.g. the player stalls or another device takes over
    part = _state(ctx)["parts"][0]
    margin = pipeline.SAFETY_REAL_S * 2.0  # speed is 2x
    assert (part["start"], part["end"]) == (0.0, 600.0 - margin)  # saved up to the last checkpoint
    assert part["cut_off"] and (ctx.work / part["file"]).exists()
    assert not list(ctx.work.glob("*.partial")) and not list(ctx.work.glob("*.progress"))

    monkeypatch.setattr(pipeline, "record_lecture", _fake_recorder(calls))
    pipeline.capture(ctx)  # the restart
    assert (
        calls[1][0] == 600.0 - margin
    )  # seeks to where we stopped, not to 0 or the site's position
    state = _state(ctx)
    assert state["complete"] and state["parts"][-1]["end"] == 1500.0
    assert [p["start"] for p in state["parts"]] == [0.0, 600.0 - margin]


def test_a_hard_kill_leaves_files_that_the_next_run_turns_into_a_part(env, monkeypatch):
    ctx = _ctx(env)
    (ctx.work / "part-0001.partial").write_bytes(b"x" * 100)  # streamed to disk before the kill
    (ctx.work / "part-0001.progress").write_text(
        json.dumps({"start": 0.0, "t": 420.0, "speed": 2.0, "duration": 1500.0})
    )
    calls = []
    monkeypatch.setattr(pipeline, "record_lecture", _fake_recorder(calls))
    pipeline.capture(ctx)
    state = _state(ctx)
    assert (state["parts"][0]["start"], state["parts"][0]["end"]) == (0.0, 414.0)
    assert calls[0][0] == 414.0 and state["complete"]
    assert (ctx.work / "part-0001.webm").exists() and not list(ctx.work.glob("*.partial"))


def test_a_cut_off_that_got_nowhere_is_discarded(env, monkeypatch):
    ctx = _ctx(env)
    (ctx.work / "part-0001.partial").write_bytes(b"x")
    (ctx.work / "part-0001.progress").write_text(
        json.dumps({"start": 0.0, "t": 8.0, "speed": 2.0, "duration": 1500.0})  # 8 - 6 = 2 s
    )
    calls = []
    monkeypatch.setattr(pipeline, "record_lecture", _fake_recorder(calls))
    pipeline.capture(ctx)
    assert calls[0][0] == 0.0 and _state(ctx)["parts"][0]["start"] == 0.0


def test_the_device_busy_error_also_saves_the_recording_so_far(env, monkeypatch):
    ctx = _ctx(env)
    monkeypatch.setattr(
        pipeline, "record_lecture", _fake_recorder([], die_at=300.0, error=DeviceBusy("tv"))
    )
    with pytest.raises(DeviceBusy):
        pipeline.capture(ctx)
    assert _state(ctx)["parts"][0]["end"] == 300.0 - pipeline.SAFETY_REAL_S * 2.0


def test_capture_stops_at_the_testing_cap(env, monkeypatch):
    import dataclasses

    ctx = _ctx(env)
    ctx.cfg = dataclasses.replace(ctx.cfg, max_media_seconds=700.0)
    calls = []
    monkeypatch.setattr(pipeline, "record_lecture", _fake_recorder(calls))
    pipeline.capture(ctx)
    assert calls == [(0.0, 700.0)] and _state(ctx)["parts"][0]["end"] == 700.0


def test_capture_fails_instead_of_looping_when_playback_makes_no_progress(env, monkeypatch):
    ctx = _ctx(env)
    monkeypatch.setattr(pipeline, "record_lecture", _fake_recorder([], advance=0.2))
    with pytest.raises(pipeline.RecordingFailed):
        pipeline.capture(ctx)


def test_transcription_slows_each_part_trims_cut_offs_and_keeps_one_timeline(env, monkeypatch):
    ctx = _ctx(env)
    _write_parts(ctx, [(0.0, 594.0), (594.0, 1200.0)], speed=2.0)
    seen = []

    def fake_extract(video, out, speed=None, max_seconds=None):
        seen.append((video.name, speed, max_seconds))
        out.write_bytes(b"a")
        return out

    def fake_split(audio, out_dir, seconds):
        out_dir.mkdir(parents=True, exist_ok=True)
        chunk = out_dir / "chunk.mp3"
        chunk.write_bytes(b"c")
        return [(0.0, chunk)]  # one chunk per part keeps this test about the timeline

    monkeypatch.setattr(pipeline.media, "extract_audio", fake_extract)
    monkeypatch.setattr(pipeline.media, "split_audio", fake_split)
    names = iter(["first", "second"])
    monkeypatch.setattr(
        pipeline.gemini,
        "transcribe",
        lambda c, a: [{"start": 5.0, "end": 9.0, "text": next(names)}],
    )
    ctx._client = object()
    pipeline.transcribe(ctx)
    assert seen == [
        ("part-0001.webm", 2.0, 297.0),
        ("part-0002.webm", 2.0, 303.0),
    ]  # recorded seconds
    segs = json.loads((ctx.work / "transcript.json").read_text(encoding="utf-8"))
    assert [(x["text"], x["start"]) for x in segs] == [("first", 5.0), ("second", 599.0)]
    assert not (ctx.work / "audio.mp3").exists()
    assert (ctx.work / "part-0001.webm").exists()  # still needed for the frames stage


def test_frames_use_lecture_time_across_parts_and_recordings_are_deleted(env, monkeypatch):
    ctx = _ctx(env)
    _write_parts(ctx, [(0.0, 600.0), (600.0, 1200.0)], speed=4.0)
    called = []

    def fake_sample(video, out_dir, every_s):
        called.append((video.name, every_s))
        out_dir.mkdir(parents=True, exist_ok=True)
        files = []
        for i in range(2):
            f = out_dir / f"raw_{i}.jpg"
            f.write_bytes(b"j")
            files.append((i * every_s, f))  # recorded-time stamps, as ffmpeg gives them
        return files

    monkeypatch.setattr(pipeline.media, "sample_frames", fake_sample)
    monkeypatch.setattr(
        pipeline.media, "dedupe_frames", lambda raw: [pipeline.media.Frame(ts=t, path=p) for t, p in raw[::2]]
    )  # fmt: skip
    pipeline.frames(ctx)
    assert called == [
        ("part-0001.webm", 0.5),
        ("part-0002.webm", 0.5),
    ]  # 2 s of lecture = 0.5 s at 4x
    kept = json.loads((ctx.work / "frames.json").read_text(encoding="utf-8"))
    assert [k[0] for k in kept] == [0.0, 600.0]  # lecture seconds, offset by each part's start
    assert all((ctx.work / rel).exists() for _t, rel in kept)
    assert not (ctx.work / "part-0001.webm").exists() and not (ctx.work / "part-0002.webm").exists()


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


def test_a_daily_gemini_quota_is_a_wait_not_a_failure(env):
    from notetaker.gemini import QuotaExhausted

    cfg, conn, _cid = env
    worker.advance(conn)
    job = jobs.claim_next(conn, now=1000)

    def out_of_quota(ctx):
        raise QuotaExhausted(72000)  # about 20 hours

    stages = {**_recording_stages([]), "capture": out_of_quota}
    assert worker.run_job(conn, cfg, job, stages, now=1000) == "quota"
    row = conn.execute("SELECT state, attempts, next_run_at FROM jobs").fetchone()
    assert (row["state"], row["attempts"]) == ("queued", 0)  # no attempt used up
    assert row["next_run_at"] == 1000 + 72000 + 120
    assert conn.execute("SELECT status FROM lectures WHERE idx = 1").fetchone()[0] != "failed"
