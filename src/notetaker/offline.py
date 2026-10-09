"""Offline pipeline: local video file -> transcript + slide frames + Markdown notes.

Usage:
    uv run python -m notetaker.offline <video> --out <dir>

This is the milestone 2 path. The worker (milestone 4) calls the same functions per job stage.
"""

import argparse
import json
import os
import tempfile
from pathlib import Path

from notetaker import gemini, media


def run(video: Path, out_dir: Path, api_key: str | None = None) -> dict:
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
        # Keep the kept frames outside the temp dir so the notes can refer to them.
        frames_out = out_dir / "frames"
        frames_out.mkdir(exist_ok=True)
        persisted = []
        for f in kept:
            dest = frames_out / f"{int(f.ts):06d}.jpg"
            dest.write_bytes(f.path.read_bytes())
            persisted.append((f.ts, dest))

        notes = gemini.write_notes(c, segments, persisted)
        (out_dir / "notes.md").write_text(notes, encoding="utf-8")

    return {
        "segments": len(segments),
        "frames_raw": len(raw),
        "frames_kept": len(persisted),
        "notes_chars": len(notes),
    }


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("video", type=Path)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    summary = run(args.video, args.out)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
