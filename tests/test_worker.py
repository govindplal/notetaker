import json
from pathlib import Path

import numpy as np
import pytest

from notetaker import jobs, pipeline, store, worker
from notetaker.capture.record import DeviceBusy, NeedsSignIn, Recording
from notetaker.config import Config
from notetaker.db import connect
from notetaker.export import export_course
from notetaker.notes.claude_writer import UsageLimited
from notetaker.notes.model import Block, GlossaryItem, LectureNotes, Section
from notetaker.notes.writer import ledger_entries


@pytest.fixture
def env(tmp_path):
    cfg = Config(data_dir=tmp_path)
    conn = connect(cfg.db_path)
    cid = store.add_course(conn, "https://x/course", "AI and ML")
    store.add_lectures(conn, cid, [("Intro", "https://x/v/1"), ("Attention", "https://x/v/2")])
    return cfg, conn, cid


def _recording_stages(calls, boom=None):
    """Stub stages. The notes stage also marks the lecture finished, as the real pipeline would."""

    def make(stage):
        def run(ctx):
            calls.append((ctx.lecture["idx"], stage))
            if boom and boom[0] == (ctx.lecture["idx"], stage) and boom[1]:
                raise boom[1].pop(0)
            if stage == "notes":
                ctx.work.mkdir(parents=True, exist_ok=True)
                (ctx.work / "capture.json").write_text(json.dumps({"complete": True, "parts": []}))
                (ctx.work / "transcript.json").write_text(json.dumps({"complete": True}))

        return run

    return {s: make(s) for s in jobs.STAGES}


def test_lectures_run_in_order_and_one_at_a_time(env):
    cfg, conn, cid = env
    calls = []
    reason = worker.work(conn, cfg, log=lambda _m: None, stages=_recording_stages(calls))
    assert reason == "finished"
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
    store.save_notes(conn, cid, lec1["id"], 1, n1, ledger_entries(n1, 1), "m", covered_until=0.0)
    n1b = _notes(heading="Embeddings")
    # re-run replaces
    store.save_notes(conn, cid, lec1["id"], 1, n1b, ledger_entries(n1b, 1), "m", covered_until=0.0)
    assert [c.title for c in store.ledger(conn, cid, 2)] == ["Embeddings"]
    assert store.ledger(conn, cid, 1) == []  # nothing before lecture 1
    assert store.ledger(conn, cid, 2)[0].key_points == ["k1"]
    assert lec2["idx"] == 2


def test_export_writes_pages_index_and_no_media(env, tmp_path):
    _cfg, conn, cid = env
    lec1 = conn.execute("SELECT * FROM lectures WHERE idx = 1").fetchone()
    n1 = _notes()
    store.save_notes(conn, cid, lec1["id"], 1, n1, ledger_entries(n1, 1), "m", covered_until=0.0)
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


def _write_parts(ctx, parts, speed=2.0, duration=1500.0, complete=True):
    state = {"duration": duration, "complete": complete, "parts": []}
    for i, (start, end) in enumerate(parts, 1):
        name = f"part-{i:04d}.webm"
        (ctx.work / name).write_bytes(b"x")
        state["parts"].append({"file": name, "start": start, "end": end, "speed": speed})
    (ctx.work / "capture.json").write_text(json.dumps(state))


def _fake_recorder(calls, die_at=None, duration=1500.0, advance=None, error=None):
    """Stands in for the browser. Records (start_at, max_media_seconds) per call.

    With die_at=T the session reports progress up to lecture time T and then fails with `error`.
    """

    def record(cdp, url, out, speed, max_media_seconds, start_at, on_progress, should_stop=None):
        calls.append((start_at, max_media_seconds))
        out.write_bytes(b"x")
        stop = start_at + (advance if advance is not None else (max_media_seconds or duration))
        t = start_at
        while t < min(stop, duration):
            t = min(t + 50.0, stop, duration)
            on_progress(t, duration)
            if should_stop is not None and should_stop():
                end = t
                return Recording(
                    path=out, speed=speed, lecture_seconds=end - start_at, wall_seconds=1.0,
                    start_t=start_at, end_t=end, duration=duration,
                )  # fmt: skip
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
    assert calls[1][0] == 600.0 - margin  # seeks to where we stopped, not to 0
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


def test_a_stop_request_saves_the_part_and_leaves_the_recording_incomplete(env, monkeypatch):
    ctx = _ctx(env)
    ctx.should_stop = lambda: True
    calls = []
    monkeypatch.setattr(pipeline, "record_lecture", _fake_recorder(calls, duration=1500.0))
    pipeline.capture(ctx)
    assert _state(ctx)["complete"] is False
    assert not (ctx.work / "part-0001.partial").exists()  # nothing half-saved
    assert pipeline.lecture_finished(ctx.work) is False


def test_capture_fails_instead_of_looping_when_playback_makes_no_progress(env, monkeypatch):
    ctx = _ctx(env)
    monkeypatch.setattr(pipeline, "record_lecture", _fake_recorder([], advance=0.2))
    with pytest.raises(pipeline.RecordingFailed):
        pipeline.capture(ctx)


class _FakeTranscriber:
    """Yields one segment per second of audio, labelled with the lecture time it reached."""

    def __init__(self):
        self.calls = []

    def transcribe(self, samples, *, offset_s=0.0):
        self.calls.append((offset_s, len(samples) / 16000))
        t = 0.0
        while t + 1 <= len(samples) / 16000:
            yield {"start": offset_s + t, "end": offset_s + t + 1, "text": f"w{int(offset_s + t)}"}
            t += 1


def _fake_decode(seconds_by_name=None):
    def decode(path, speed, natural_s):
        return np.full(int(round(natural_s * 16000)), 0.1, dtype=np.float32)

    return decode


def test_transcription_is_one_pass_over_the_whole_lecture_and_keeps_the_timeline(env, monkeypatch):
    ctx = _ctx(env)
    _write_parts(ctx, [(0.0, 600.0), (600.0, 1200.0)], speed=2.0)
    monkeypatch.setattr(pipeline.asr, "decode_recording", _fake_decode())
    fake = _FakeTranscriber()
    ctx._transcriber = fake
    pipeline.transcribe(ctx)
    assert fake.calls == [(0.0, 1200.0)]  # one call for the whole lecture, no 30-minute chunks
    meta = json.loads((ctx.work / "transcript.json").read_text(encoding="utf-8"))
    assert meta["complete"] is True
    assert meta["segments"][0]["start"] == 0.0 and meta["segments"][-1]["start"] == 1199.0
    assert (ctx.work / "audio.pcm").stat().st_size == 1200 * 16000 * 2  # int16 mono, lecture pace


def test_transcription_of_a_lecture_still_recording_keeps_only_final_segments(env, monkeypatch):
    ctx = _ctx(env)
    _write_parts(ctx, [(0.0, 600.0)], speed=2.0, complete=False)
    monkeypatch.setattr(pipeline.asr, "decode_recording", _fake_decode())
    ctx._transcriber = _FakeTranscriber()
    pipeline.transcribe(ctx)
    meta = json.loads((ctx.work / "transcript.json").read_text(encoding="utf-8"))
    assert meta["complete"] is False
    assert max(s["end"] for s in meta["segments"]) <= 600.0 - pipeline.SAFE_TAIL_S


def test_transcription_resumes_from_the_last_final_segment(env, monkeypatch):
    ctx = _ctx(env)
    monkeypatch.setattr(pipeline.asr, "decode_recording", _fake_decode())
    fake = _FakeTranscriber()
    ctx._transcriber = fake
    _write_parts(ctx, [(0.0, 600.0)], speed=2.0, complete=False)
    pipeline.transcribe(ctx)
    first_end = json.loads((ctx.work / "transcript.json").read_text())["segments"][-1]["end"]
    # the next run records more, and transcription carries on from the last final segment
    _write_parts(ctx, [(0.0, 600.0), (600.0, 900.0)], speed=2.0, complete=True)
    pipeline.transcribe(ctx)
    assert fake.calls[1][0] == first_end
    meta = json.loads((ctx.work / "transcript.json").read_text())
    assert meta["complete"] is True and meta["segments"][-1]["end"] == 900.0


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
    assert kept["parts_done"] == 2
    assert [k[0] for k in kept["frames"]] == [0.0, 600.0]  # lecture seconds, offset by part start
    assert all((ctx.work / rel).exists() for _t, rel in kept["frames"])
    assert not (ctx.work / "part-0001.webm").exists() and not (ctx.work / "part-0002.webm").exists()


def test_cleanup_removes_everything(env):
    ctx = _ctx(env)
    (ctx.work / "transcript.json").write_text("[]")
    pipeline.cleanup(ctx)
    assert not Path(ctx.cfg.work_dir / f"lecture-{ctx.lecture['id']}").exists()


def test_a_stop_during_a_lecture_finishes_its_chain_and_starts_no_new_lecture(env):
    cfg, conn, _cid = env
    calls = []
    stages = _recording_stages(calls)
    real_capture = stages["capture"]

    def capture_then_stop(ctx):
        real_capture(ctx)
        cfg.stop_path.write_text("stop")  # the owner presses Stop while this lecture records

    stages["capture"] = capture_then_stop
    # Notes for a part only: nothing marks the lecture finished, as after a real stop.
    stages["notes"] = lambda ctx: calls.append((ctx.lecture["idx"], "notes"))
    reason = worker.work(conn, cfg, log=lambda _m: None, stages=stages)
    assert reason == "stopped"
    assert calls == [(1, s) for s in jobs.STAGES[:4]]  # lecture 1 notes, no cleanup, no lecture 2
    assert conn.execute("SELECT status FROM lectures WHERE idx = 2").fetchone()[0] == "pending"


def test_a_notes_stage_for_an_unfinished_lecture_leaves_it_pending_for_the_next_run(env):
    cfg, conn, _cid = env
    worker.advance(conn)
    for stage in jobs.STAGES[:3]:
        job = jobs.claim_next(conn)
        assert job["stage"] == stage
        jobs.complete(conn, job["id"])
    job = jobs.claim_next(conn)  # notes, with nothing finished on disk yet
    stages = {**_recording_stages([]), "notes": lambda ctx: None}
    assert worker.run_job(conn, cfg, job, stages) == "partial"
    assert conn.execute("SELECT status FROM lectures WHERE idx = 1").fetchone()[0] == "pending"
    assert conn.execute("SELECT COUNT(*) FROM jobs WHERE stage = 'cleanup'").fetchone()[0] == 0


def test_the_claude_usage_limit_stops_the_worker_without_waiting(env):
    cfg, conn, _cid = env

    def out_of_usage(ctx):
        raise UsageLimited("usage limit reached", retry_after_s=3600)

    stages = {**_recording_stages([]), "notes": out_of_usage}
    reason = worker.work(conn, cfg, log=lambda _m: None, stages=stages)
    assert reason == "usage_limit"
    row = conn.execute("SELECT state, attempts, error FROM jobs WHERE stage = 'notes'").fetchone()
    assert (row["state"], row["attempts"]) == ("queued", 0)  # no attempt used up
    assert row["error"].startswith("Claude usage limit")
    # the next Start reports the limit straight away instead of waiting for the reset
    again = worker.work(conn, cfg, log=lambda _m: None, stages=stages)
    assert again == "usage_limit"


def test_a_second_worker_cannot_start_while_one_is_running(env):
    cfg, conn, _cid = env
    with worker.single_instance(cfg.lock_path):
        with pytest.raises(worker.AlreadyRunning):
            worker.work(conn, cfg, log=lambda _m: None, stages=_recording_stages([]))


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
        store.save_notes(
            conn,
            cid,
            lec["id"],
            lec["idx"],
            n,
            ledger_entries(n, lec["idx"]),
            "m",
            covered_until=0.0,
        )
        conn.execute("UPDATE lectures SET status = 'done' WHERE id = ?", (lec["id"],))
        jobs.enqueue_lecture(conn, lec["id"])
    assert store.reset_from(conn, cid, 2) == 1  # only lecture 2 is at or after #2
    assert conn.execute("SELECT status FROM lectures WHERE idx = 1").fetchone()[0] == "done"
    assert conn.execute("SELECT status FROM lectures WHERE idx = 2").fetchone()[0] == "pending"
    assert [c.title for c in store.ledger(conn, cid, 3)] == ["T1"]  # lecture 2's concepts are gone
    assert conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 1
    assert store.reset_from(conn, cid, 1) == 2
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


def test_a_partial_lecture_is_queued_again_from_capture_on_the_next_run(env):
    cfg, conn, _cid = env
    stages = _recording_stages([])
    stages["notes"] = lambda ctx: None  # notes for a part only: the lecture stays unfinished
    worker.advance(conn)
    for _ in jobs.STAGES[:4]:  # first run: capture, transcribe, frames, notes
        job = jobs.claim_next(conn)
        assert worker.run_job(conn, cfg, job, stages) in ("done", "partial")
    assert conn.execute("SELECT status FROM lectures WHERE idx = 1").fetchone()[0] == "pending"
    assert worker.advance(conn) == 1  # the next run picks the same lecture up again
    again = jobs.claim_next(conn)
    assert again is not None and again["stage"] == "capture"
    # and it walks the whole chain again, not just the first stage
    jobs.complete(conn, again["id"])
    assert jobs.claim_next(conn)["stage"] == "transcribe"


class _OverrunTranscriber:
    """Like Whisper, whose last segment can end a fraction of a second past the audio."""

    def transcribe(self, samples, *, offset_s=0.0):
        end = offset_s + len(samples) / 16000
        yield {"start": offset_s, "end": end - 5, "text": "body"}
        yield {"start": end - 5, "end": end + 0.4, "text": "last words"}


def test_a_finished_recording_gives_a_complete_transcript_even_if_whisper_overruns(
    env, monkeypatch
):
    ctx = _ctx(env)
    _write_parts(ctx, [(0.0, 300.0)], speed=2.0, complete=True)
    monkeypatch.setattr(pipeline.asr, "decode_recording", _fake_decode())
    ctx._transcriber = _OverrunTranscriber()
    pipeline.transcribe(ctx)
    meta = json.loads((ctx.work / "transcript.json").read_text(encoding="utf-8"))
    assert meta["complete"] is True  # not "cut off": nothing more will be recorded
    assert [s["text"] for s in meta["segments"]] == ["body", "last words"]
    assert meta["segments"][-1]["end"] == 300.0  # clamped to the audio that exists
    assert pipeline.lecture_finished(ctx.work) is True
