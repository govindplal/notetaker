"""ffmpeg helpers: audio extraction, frame sampling and perceptual de-duplication."""

import subprocess
from dataclasses import dataclass
from pathlib import Path

import imagehash
from PIL import Image


def _run(args: list[str]) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def duration_seconds(video: Path) -> float:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            str(video),
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return float(out.strip())


def extract_audio(video: Path, out: Path) -> Path:
    """16 kHz mono MP3: small enough to upload quickly, good enough for speech."""
    _run(["-i", str(video), "-vn", "-ac", "1", "-ar", "16000", "-b:a", "64k", str(out)])
    return out


def sample_frames(video: Path, out_dir: Path, every_s: float = 2.0) -> list[tuple[float, Path]]:
    """Grab one frame every `every_s` seconds at 960px wide. Returns (timestamp, path)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    pattern = out_dir / "raw_%06d.jpg"
    _run(
        [
            "-i",
            str(video),
            "-vf",
            f"fps=1/{every_s},scale=960:-2",
            "-q:v",
            "4",
            str(pattern),
        ]
    )
    return [(i * every_s, p) for i, p in enumerate(sorted(out_dir.glob("raw_*.jpg")))]


@dataclass(frozen=True)
class Frame:
    ts: float
    path: Path


def dedupe_frames(frames: list[tuple[float, Path]], threshold: int = 6) -> list[Frame]:
    """Keep a frame only when it differs enough from the last kept one.

    Lecture video is mostly a static slide or talking head, so this drops
    the long unchanged stretches and keeps each new slide or board state.
    """
    kept: list[Frame] = []
    last_hash = None
    for ts, path in frames:
        with Image.open(path) as img:
            h = imagehash.phash(img)
        if last_hash is None or (h - last_hash) > threshold:
            kept.append(Frame(ts=ts, path=path))
            last_hash = h
    return kept
