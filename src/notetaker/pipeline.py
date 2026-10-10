"""The per-lecture stages: capture, transcribe, frames, notes, cleanup.

A lecture is worked on in parts. Each stage only handles what is new since its last run, and it
saves progress as it goes, so a stop, a crash or a usage limit loses nothing and the next run picks
up where it left off:

- capture: records the lecture from where the last part ended, as one or more parts.
- transcribe: decodes the new parts into one audio file at lecture pace, and transcribes the audio
  from the end of the last final segment onwards. Speech that may still be cut off is not final.
- frames: samples frames from the new parts, keeps one per distinct slide or board state.
- notes: writes notes for the transcript and frames since the last notes, and merges them into the
  notes already stored for the lecture.

A lecture is finished once its recording and transcript are complete. Only then are its working
files deleted. Otherwise the notes stage leaves the lecture pending, to be continued next run.
"""

import json
import shutil
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from notetaker import asr, media, store
from notetaker.capture.record import RecordingFailed, record_lecture
from notetaker.config import Config
from notetaker.export import export_course
from notetaker.notes.claude_writer import ClaudeWriter
from notetaker.notes.model import LectureNotes
from notetaker.notes.writer import ledger_entries, merge_notes, write_lecture_notes

SAMPLE_RATE = asr.SAMPLE_RATE
SAFETY_REAL_S = 3.0  # a part that was cut off is trusted up to this much before its checkpoint
MIN_USEFUL_S = 5.0  # a cut-off recording shorter than this (lecture seconds) is thrown away
SAFE_TAIL_S = 6.0  # while recording continues, the last seconds of speech may still be cut off
FRAME_EVERY_LECTURE_S = 2.0  # one candidate frame per two seconds of lecture


@dataclass
class Ctx:
    conn: sqlite3.Connection
    cfg: Config
    lecture: sqlite3.Row
    course: sqlite3.Row
    should_stop: Callable[[], bool] = field(default=lambda: False, repr=False)
    _transcriber: object = field(default=None, repr=False)
    _writer: object = field(default=None, repr=False)

    @property
    def work(self) -> Path:
        d = self.cfg.work_dir / f"lecture-{self.lecture['id']}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def transcriber(self):
        if self._transcriber is None:
            self._transcriber = asr.WhisperTranscriber(self.cfg.whisper_model)
        return self._transcriber

    @property
    def writer(self):
        if self._writer is None:
            self._writer = ClaudeWriter(self.cfg.claude_model)
        return self._writer


def _read_json(path: Path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def _write_json(path: Path, obj) -> None:
    """Atomically save, so a crash never leaves a half-written file."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _read_state(ctx: Ctx) -> dict:
    return _read_json(ctx.work / "capture.json", {"duration": None, "parts": [], "complete": False})


def _write_state(ctx: Ctx, state: dict) -> None:
    _write_json(ctx.work / "capture.json", state)


def lecture_finished(work: Path) -> bool:
    """True once the recording and the transcript are both complete."""
    capture = _read_json(work / "capture.json", {"complete": False})
    transcript = _read_json(work / "transcript.json", {"complete": False})
    return bool(capture["complete"] and transcript["complete"])


def _progress_file(partial: Path) -> Path:
    return partial.with_suffix(".progress")


def _save_cutoff(ctx: Ctx, state: dict, partial: Path) -> None:
    """Keep whatever a recording that was cut off managed to capture.

    The recording streams to disk as it plays and its checkpoint says how far it got, so the file is
    usable up to a few seconds before that. The next run carries on from where this part ends.
    """
    progress = _progress_file(partial)
    try:
        info = json.loads(progress.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        info = None
    final = partial.with_suffix(".webm")
    if info and partial.exists():
        end = info["t"] - SAFETY_REAL_S * info["speed"]
        if end - info["start"] >= MIN_USEFUL_S:
            partial.replace(final)
            state["parts"].append(
                {
                    "file": final.name,
                    "start": info["start"],
                    "end": end,
                    "speed": info["speed"],
                    "cut_off": True,
                }
            )
            state["duration"] = state["duration"] or info.get("duration")
            _write_state(ctx, state)
    partial.unlink(missing_ok=True)
    progress.unlink(missing_ok=True)


def capture(ctx: Ctx) -> None:
    """Record the lecture from where the last part ended. Stops early when asked to.

    Progress is checkpointed as it plays. If anything stops the session (an error, another device
    taking over the player, a kill, or a stop request), whatever was recorded is kept as a part
    ending where we know it got to. A run carries on from the end of the last part, whatever
    position the site itself wants to resume from.
    """
    cfg = ctx.cfg
    state = _read_state(ctx)
    for leftover in sorted(ctx.work.glob("part-*.partial")):  # from a hard kill
        _save_cutoff(ctx, state, leftover)
    parts = state["parts"]
    stop_at = cfg.max_media_seconds  # testing cap on the total, normally None
    while not state["complete"]:
        cursor = parts[-1]["end"] if parts else 0.0
        duration = state["duration"]
        if (duration is not None and cursor >= duration - 1.5) or (
            stop_at is not None and cursor >= stop_at - 1.0
        ):
            state["complete"] = True
            break
        if ctx.should_stop():
            break
        n = len(parts) + 1
        partial, final = ctx.work / f"part-{n:04d}.partial", ctx.work / f"part-{n:04d}.webm"

        def checkpoint(t: float, total: float, start: float = cursor, path: Path = partial) -> None:
            _progress_file(path).write_text(
                json.dumps({"start": start, "t": t, "speed": cfg.speed, "duration": total}),
                encoding="utf-8",
            )

        try:
            rec = record_lecture(
                cfg.cdp_url,
                ctx.lecture["url"],
                partial,
                speed=cfg.speed,
                max_media_seconds=None if stop_at is None else stop_at - cursor,
                start_at=cursor,
                on_progress=checkpoint,
                should_stop=ctx.should_stop,
            )
        except BaseException:
            _save_cutoff(ctx, state, partial)  # keep what we have, then let the error through
            raise
        if rec.end_t - rec.start_t < 1.0:
            partial.unlink(missing_ok=True)
            _progress_file(partial).unlink(missing_ok=True)
            if ctx.should_stop():  # asked to stop before anything new was captured
                break
            raise RecordingFailed(f"no progress recording from {cursor:.0f}s")
        partial.replace(final)
        _progress_file(partial).unlink(missing_ok=True)
        parts.append(
            {"file": final.name, "start": rec.start_t, "end": rec.end_t, "speed": rec.speed}
        )
        state["duration"] = rec.duration
        _write_state(ctx, state)
    _write_state(ctx, state)


AUDIO = "audio.pcm"  # 16 kHz mono int16 at lecture pace, grown part by part


def _append_audio(ctx: Ctx, parts: list[dict]) -> None:
    """Decode the parts not yet in the audio file and append them, in order, at their lecture time."""
    log = _read_json(ctx.work / "audio.json", {"parts": 0, "samples": 0})
    pcm = ctx.work / AUDIO
    if pcm.exists():  # a part half-written before a crash is dropped and decoded again
        with pcm.open("r+b") as f:
            f.truncate(log["samples"] * 2)
    for i in range(log["parts"], len(parts)):
        part = parts[i]
        have = pcm.stat().st_size // 2 if pcm.exists() else 0
        target = round(part["start"] * SAMPLE_RATE)
        samples = asr.decode_recording(
            ctx.work / part["file"], part["speed"], part["end"] - part["start"]
        )
        pad = np.zeros(max(target - have, 0), dtype=np.int16)  # silence for any gap
        pcm_chunk = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
        with pcm.open("ab") as f:
            f.write(pad.tobytes())
            f.write(pcm_chunk.tobytes())
        log = {"parts": i + 1, "samples": pcm.stat().st_size // 2}
        _write_json(ctx.work / "audio.json", log)


def transcribe(ctx: Ctx) -> None:
    """Transcribe the lecture's audio from the last final segment on, in one pass."""
    state = _read_state(ctx)
    _append_audio(ctx, state["parts"])
    pcm = ctx.work / AUDIO
    audio_s = pcm.stat().st_size / 2 / SAMPLE_RATE if pcm.exists() else 0.0
    meta = _read_json(
        ctx.work / "transcript.json",
        {"segments": [], "complete": False, "audio_s": 0.0},
    )
    recording_done = bool(state["complete"])
    limit = audio_s if recording_done else audio_s - SAFE_TAIL_S
    kept = [s for s in meta["segments"] if s["end"] <= limit]
    resume = kept[-1]["end"] if kept else 0.0

    new: list[dict] = []
    stopped = cut = False
    if pcm.exists() and audio_s > resume:
        raw = np.memmap(pcm, dtype=np.int16, mode="r")
        samples = raw[int(resume * SAMPLE_RATE) :].astype(np.float32) / 32768.0
        del raw
        for seg in ctx.transcriber.transcribe(samples, offset_s=resume):
            if seg["end"] > limit:
                if not recording_done:
                    cut = True  # the rest is not final until more has been recorded
                    break
                seg = {
                    **seg,
                    "end": limit,
                }  # Whisper can overrun the audio by a fraction of a second
            new.append(seg)
            if ctx.should_stop():
                stopped = True
                break
    _write_json(
        ctx.work / "transcript.json",
        {
            "segments": kept + new,
            "complete": recording_done and not stopped and not cut,
            "audio_s": audio_s,
        },
    )


def frames(ctx: Ctx) -> None:
    """Sample frames from the parts not yet done, keep one per distinct slide or board state.

    The recordings are sped up, so frames are sampled every 2/speed recorded seconds and their
    timestamps are scaled back to lecture time (plus the part's start). Each part's recording is
    deleted once its frames are kept: the audio has already been taken from it by then.
    """
    state = _read_state(ctx)
    meta = _read_json(ctx.work / "frames.json", {"parts_done": 0, "frames": []})
    existing = [(ts, ctx.work / rel) for ts, rel in meta["frames"]]
    raw: list[tuple[float, Path]] = []
    for n in range(meta["parts_done"], len(state["parts"])):
        part = state["parts"][n]
        sampled = media.sample_frames(
            ctx.work / part["file"],
            ctx.work / "frames" / f"p{n + 1:04d}",
            every_s=FRAME_EVERY_LECTURE_S / part["speed"],
        )
        raw += [
            (part["start"] + ts * part["speed"], path)
            for ts, path in sampled
            if part["start"] + ts * part["speed"] <= part["end"]
        ]
    # The last kept frame seeds the comparison, so a slide that continues across a stop is not repeated.
    seed = existing[-1:]
    kept = media.dedupe_frames(seed + raw)
    kept_paths = {f.path for f in kept}
    for _ts, path in raw:
        if path not in kept_paths:
            path.unlink(missing_ok=True)
    new_kept = [(f.ts, f.path) for f in kept if f.path not in {p for _, p in seed}]
    everything = existing + new_kept
    _write_json(
        ctx.work / "frames.json",
        {
            "parts_done": len(state["parts"]),
            "frames": [[ts, str(p.relative_to(ctx.work))] for ts, p in everything],
        },
    )
    for part in state["parts"]:
        (ctx.work / part["file"]).unlink(missing_ok=True)


def notes(ctx: Ctx) -> None:
    """Write notes for what is new since the last run, merge them, and refresh the course HTML."""
    lec, idx = ctx.lecture, ctx.lecture["idx"]
    transcript = _read_json(ctx.work / "transcript.json", {"segments": [], "complete": False})
    listing = _read_json(ctx.work / "frames.json", {"frames": []})["frames"]
    covered = store.covered_until(ctx.conn, lec["id"])
    last_end = max((s["end"] for s in transcript["segments"]), default=covered)
    new_covered = max(
        covered, transcript.get("audio_s", 0.0) if transcript["complete"] else last_end
    )
    new_segments = [s for s in transcript["segments"] if s["end"] > covered]
    new_frames = [(ts, ctx.work / rel) for ts, rel in listing if covered < ts <= new_covered]
    existing = store.stored_notes(ctx.conn, lec["id"])
    if not new_segments and not new_frames:
        if existing is None and transcript["complete"]:
            # A lecture with no speech and no slides still gets a (empty) entry.
            existing = LectureNotes(
                title=lec["title"], lead="No speech was transcribed.", sections=[]
            )
            store.save_notes(
                ctx.conn,
                ctx.course["id"],
                lec["id"],
                idx,
                existing,
                [],
                model=_model_label(ctx),
                covered_until=new_covered,
            )
    else:
        result = write_lecture_notes(
            ctx.writer,
            course=ctx.course["title"] or "Course",
            lecture_idx=idx,
            title=lec["title"],
            segments=new_segments,
            frames=new_frames,
            ledger=store.ledger(ctx.conn, ctx.course["id"], idx),
            earlier=existing,
        )
        merged = merge_notes(existing, result) if existing else result
        store.save_notes(
            ctx.conn,
            ctx.course["id"],
            lec["id"],
            idx,
            merged,
            ledger_entries(merged, idx),
            model=_model_label(ctx),
            covered_until=new_covered,
        )
    export_course(ctx.conn, ctx.course["id"], ctx.cfg.notes_dir / f"course-{ctx.course['id']}")


def _model_label(ctx: Ctx) -> str:
    return f"claude:{ctx.cfg.claude_model or 'default'}"


def cleanup(ctx: Ctx) -> None:
    """Delete every working file of a finished lecture: recording, audio, transcript and frames."""
    shutil.rmtree(ctx.cfg.work_dir / f"lecture-{ctx.lecture['id']}", ignore_errors=True)


STAGE_FUNCS: dict[str, Callable[[Ctx], None]] = {
    "capture": capture,
    "transcribe": transcribe,
    "frames": frames,
    "notes": notes,
    "cleanup": cleanup,
}
