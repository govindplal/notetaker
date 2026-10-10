"""The per-lecture stages: capture, transcribe, frames, notes, cleanup.

Each stage reads and writes only its lecture's working folder (data/work/lecture-<id>) and the
database, so a crashed job can simply run again. The working folder holds the recording, audio,
transcript and frames; it is deleted by the cleanup stage once the notes are stored. Only the notes,
the concept ledger and the generated HTML are kept.
"""

import json
import shutil
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from notetaker import gemini, media, store
from notetaker.capture.record import RecordingFailed, record_lecture
from notetaker.config import Config
from notetaker.export import export_course
from notetaker.notes.writer import ledger_entries, write_lecture_notes


@dataclass
class Ctx:
    conn: sqlite3.Connection
    cfg: Config
    lecture: sqlite3.Row
    course: sqlite3.Row
    _client: object = field(default=None, repr=False)

    @property
    def work(self) -> Path:
        d = self.cfg.work_dir / f"lecture-{self.lecture['id']}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def client(self):
        if self._client is None:
            self._client = gemini.client(self.cfg.gemini_api_key)
        return self._client


def _read_state(ctx: Ctx) -> dict:
    path = ctx.work / "capture.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"duration": None, "parts": [], "complete": False}


def _write_state(ctx: Ctx, state: dict) -> None:
    """Atomically save progress, so a crash never leaves a half-written file."""
    tmp = ctx.work / "capture.json.tmp"
    tmp.write_text(json.dumps(state), encoding="utf-8")
    tmp.replace(ctx.work / "capture.json")


# A recording that was cut off is only trusted up to a little before the last checkpoint, because
# the file can lag the checkpoint by a couple of seconds.
TRANSCRIBE_CHUNK_S = 1800  # natural-pace audio per transcription request
SAFETY_REAL_S = 3.0
MIN_USEFUL_S = 5.0  # a cut-off recording shorter than this (lecture seconds) is thrown away


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
    """Record the lecture in one continuous session, and never lose what was already recorded.

    Progress is checkpointed as it plays. If anything stops the session (an error, another device
    taking over the player, or a kill), whatever was recorded is kept as a part ending where we know
    it got to. A restart seeks explicitly to the end of the last part and carries on, whatever
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
            )
        except BaseException:
            _save_cutoff(ctx, state, partial)  # keep what we have, then let the error through
            raise
        if rec.end_t - rec.start_t < 1.0:
            partial.unlink(missing_ok=True)
            _progress_file(partial).unlink(missing_ok=True)
            raise RecordingFailed(f"no progress recording from {cursor:.0f}s")
        partial.replace(final)
        _progress_file(partial).unlink(missing_ok=True)
        parts.append(
            {"file": final.name, "start": rec.start_t, "end": rec.end_t, "speed": rec.speed}
        )
        state["duration"] = rec.duration
        _write_state(ctx, state)
    state["complete"] = True
    _write_state(ctx, state)


def transcribe(ctx: Ctx) -> None:
    """Slow each part's audio back to natural pace, transcribe it, and join one timeline."""
    state = _read_state(ctx)
    segments: list[dict] = []
    for part in state["parts"]:
        recorded_s = (part["end"] - part["start"]) / part[
            "speed"
        ]  # a cut-off part stops at its end
        audio = media.extract_audio(
            ctx.work / part["file"], ctx.work / "audio.mp3", part["speed"], max_seconds=recorded_s
        )
        # Fewer, longer requests: the free tier allows only ~20 per model per day.
        for offset, chunk in media.split_audio(
            audio, ctx.work / "audio_chunks", seconds=TRANSCRIBE_CHUNK_S
        ):
            for seg in gemini.transcribe(ctx.client, chunk):
                start = part["start"] + offset
                segments.append({**seg, "start": seg["start"] + start, "end": seg["end"] + start})
        shutil.rmtree(ctx.work / "audio_chunks", ignore_errors=True)
        audio.unlink(missing_ok=True)
    (ctx.work / "transcript.json").write_text(
        json.dumps(segments, ensure_ascii=False), encoding="utf-8"
    )


FRAME_EVERY_LECTURE_S = 2.0  # one candidate frame per two seconds of lecture


def frames(ctx: Ctx) -> None:
    """Sample frames from every part, keep one per distinct slide or board state.

    The recordings are sped up, so frames are sampled every 2/speed recorded seconds and their
    timestamps are scaled back to lecture time (plus the part's start).
    """
    state = _read_state(ctx)
    raw: list[tuple[float, Path]] = []
    for n, part in enumerate(state["parts"], 1):
        sampled = media.sample_frames(
            ctx.work / part["file"],
            ctx.work / "frames" / f"p{n:04d}",
            every_s=FRAME_EVERY_LECTURE_S / part["speed"],
        )
        raw += [
            (part["start"] + ts * part["speed"], path)
            for ts, path in sampled
            if part["start"] + ts * part["speed"] <= part["end"]
        ]
    kept = media.dedupe_frames(raw)  # one pass over all parts, so repeats across a seam are dropped
    keep_paths = {f.path for f in kept}
    for _ts, path in raw:
        if path not in keep_paths:
            path.unlink(missing_ok=True)
    (ctx.work / "frames.json").write_text(
        json.dumps([[f.ts, str(f.path.relative_to(ctx.work))] for f in kept]), encoding="utf-8"
    )
    for part in state["parts"]:  # the recordings are no longer needed
        (ctx.work / part["file"]).unlink(missing_ok=True)


def notes(ctx: Ctx) -> None:
    """Write the notes with Gemini, store them with the ledger, and refresh the course's HTML."""
    segments = json.loads((ctx.work / "transcript.json").read_text(encoding="utf-8"))
    listing = json.loads((ctx.work / "frames.json").read_text(encoding="utf-8"))
    frame_files = [(ts, ctx.work / rel) for ts, rel in listing]
    idx = ctx.lecture["idx"]
    result = write_lecture_notes(
        ctx.client,
        course=ctx.course["title"] or "Course",
        lecture_idx=idx,
        title=ctx.lecture["title"],
        segments=segments,
        frames=frame_files,
        ledger=store.ledger(ctx.conn, ctx.course["id"], idx),
    )
    store.save_notes(
        ctx.conn,
        ctx.course["id"],
        ctx.lecture["id"],
        idx,
        result,
        ledger_entries(result, idx),
        model=gemini.MODEL,
    )
    export_course(ctx.conn, ctx.course["id"], ctx.cfg.notes_dir / f"course-{ctx.course['id']}")


def cleanup(ctx: Ctx) -> None:
    """Delete every working file: recording, audio, transcript and frames."""
    shutil.rmtree(ctx.cfg.work_dir / f"lecture-{ctx.lecture['id']}", ignore_errors=True)


STAGE_FUNCS: dict[str, Callable[[Ctx], None]] = {
    "capture": capture,
    "transcribe": transcribe,
    "frames": frames,
    "notes": notes,
    "cleanup": cleanup,
}
