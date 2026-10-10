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
from notetaker.capture.record import record_lecture
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


def capture(ctx: Ctx) -> None:
    """Play the lecture in the signed-in browser at speed x and record it."""
    rec = record_lecture(
        ctx.cfg.cdp_url,
        ctx.lecture["url"],
        ctx.work / "recording.webm",
        speed=ctx.cfg.speed,
        max_media_seconds=ctx.cfg.max_media_seconds,
    )
    (ctx.work / "capture.json").write_text(
        json.dumps({"speed": rec.speed, "lecture_seconds": rec.lecture_seconds}), encoding="utf-8"
    )


def transcribe(ctx: Ctx) -> None:
    """Slow the recording's audio back to natural pace, then transcribe it in chunks."""
    cap = json.loads((ctx.work / "capture.json").read_text(encoding="utf-8"))
    audio = media.extract_audio(ctx.work / "recording.webm", ctx.work / "audio.mp3", cap["speed"])
    segments: list[dict] = []
    for offset, chunk in media.split_audio(audio, ctx.work / "audio_chunks"):
        for seg in gemini.transcribe(ctx.client, chunk):
            segments.append({**seg, "start": seg["start"] + offset, "end": seg["end"] + offset})
    shutil.rmtree(ctx.work / "audio_chunks", ignore_errors=True)
    audio.unlink(missing_ok=True)
    (ctx.work / "transcript.json").write_text(
        json.dumps(segments, ensure_ascii=False), encoding="utf-8"
    )


FRAME_EVERY_LECTURE_S = 2.0  # one candidate frame per two seconds of lecture


def frames(ctx: Ctx) -> None:
    """Sample frames from the recording, keep one per distinct slide or board state.

    The recording is sped up, so frames are sampled every 2/speed recorded seconds and their
    timestamps are scaled back to lecture time.
    """
    cap = json.loads((ctx.work / "capture.json").read_text(encoding="utf-8"))
    speed = cap["speed"]
    raw = media.sample_frames(
        ctx.work / "recording.webm", ctx.work / "frames", every_s=FRAME_EVERY_LECTURE_S / speed
    )
    raw = [(ts * speed, path) for ts, path in raw]
    kept = media.dedupe_frames(raw)
    keep_paths = {f.path for f in kept}
    for _ts, path in raw:
        if path not in keep_paths:
            path.unlink(missing_ok=True)
    (ctx.work / "frames.json").write_text(
        json.dumps([[f.ts, f.path.name] for f in kept]), encoding="utf-8"
    )
    (ctx.work / "recording.webm").unlink(missing_ok=True)  # the big file is no longer needed


def notes(ctx: Ctx) -> None:
    """Write the notes with Gemini, store them with the ledger, and refresh the course's HTML."""
    segments = json.loads((ctx.work / "transcript.json").read_text(encoding="utf-8"))
    listing = json.loads((ctx.work / "frames.json").read_text(encoding="utf-8"))
    frame_files = [(ts, ctx.work / "frames" / name) for ts, name in listing]
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
