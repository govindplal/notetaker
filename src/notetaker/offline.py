"""Offline pipeline: local video file -> transcript + all frames + structured notes + HTML.

Usage:
    uv run python -m notetaker.offline <video> --out <dir> --course "AI and ML Bootcamp" \\
        --lecture 3 --title "Transformers Part 1" --ledger <dir>/ledger.json

The ledger remembers what each lecture taught. Pass the same --ledger for every lecture of a
course, in order, so later lectures skip what earlier ones already covered.
This is the milestone 2 path. The worker (milestone 4) calls the same functions per job stage.
"""

import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path

from notetaker import gemini, media
from notetaker.notes.model import Concept
from notetaker.notes.writer import ledger_entries, write_lecture_notes
from notetaker.render.html import FrameRef, render_lecture


def load_ledger(path: Path | None) -> list[Concept]:
    if path is None or not path.exists():
        return []
    return [Concept.model_validate(x) for x in json.loads(path.read_text(encoding="utf-8"))]


def save_ledger(path: Path, ledger: list[Concept]) -> None:
    path.write_text(
        json.dumps([c.model_dump() for c in ledger], ensure_ascii=False, indent=2), encoding="utf-8"
    )


def run(
    video: Path,
    out_dir: Path,
    *,
    course: str = "Course",
    lecture_idx: int = 1,
    title: str | None = None,
    ledger_path: Path | None = None,
    api_key: str | None = None,
) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    c = gemini.client(api_key or os.environ.get("GEMINI_API_KEY"))
    with tempfile.TemporaryDirectory(prefix="notetaker-") as tmp:
        tmp_dir = Path(tmp)
        audio = media.extract_audio(video, tmp_dir / "audio.mp3")
        segments = gemini.transcribe(c, audio)
        (out_dir / "transcript.json").write_text(
            json.dumps(segments, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        raw = media.sample_frames(video, tmp_dir / "frames")
        kept = media.dedupe_frames(raw)
        # Every distinct frame is stored for good; only near-duplicates are dropped.
        frames_out = out_dir / "frames"
        frames_out.mkdir(exist_ok=True)
        stored: list[tuple[float, Path]] = []
        for f in kept:
            dest = frames_out / f"{int(f.ts):06d}.jpg"
            shutil.copyfile(f.path, dest)
            stored.append((f.ts, dest))

    # Notes see only the lectures before this one, so re-running a lecture is idempotent.
    ledger = [x for x in load_ledger(ledger_path) if x.lecture_idx < lecture_idx]
    notes = write_lecture_notes(
        c,
        course=course,
        lecture_idx=lecture_idx,
        title=title or f"Lecture {lecture_idx}",
        segments=segments,
        frames=stored,
        ledger=ledger,
    )
    (out_dir / "notes.json").write_text(notes.model_dump_json(indent=2), encoding="utf-8")

    page = render_lecture(
        notes,
        course_title=course,
        lecture_idx=lecture_idx,
        frames=[FrameRef(ts=ts, href=f"frames/{p.name}") for ts, p in stored],
        transcript=segments,
        lecture_titles={},
    )
    (out_dir / "notes.html").write_text(page, encoding="utf-8")

    if ledger_path is not None:
        others = [x for x in load_ledger(ledger_path) if x.lecture_idx != lecture_idx]
        save_ledger(
            ledger_path, sorted(others + ledger_entries(notes, lecture_idx), key=lambda x: x.id)
        )

    return {
        "segments": len(segments),
        "frames_raw": len(raw),
        "frames_stored": len(stored),
        "sections": len(notes.sections),
        "left_out": len(notes.left_out),
        "html": str(out_dir / "notes.html"),
    }


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("video", type=Path)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--course", default="Course")
    p.add_argument("--lecture", type=int, default=1)
    p.add_argument("--title")
    p.add_argument("--ledger", type=Path)
    args = p.parse_args()
    summary = run(
        args.video,
        args.out,
        course=args.course,
        lecture_idx=args.lecture,
        title=args.title,
        ledger_path=args.ledger,
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
