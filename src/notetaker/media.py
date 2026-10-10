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


def extract_audio(
    video: Path, out: Path, speed: float | None = None, max_seconds: float | None = None
) -> Path:
    """16 kHz mono MP3: small enough to upload quickly, good enough for speech.

    For a recording made at `speed`x, pass `speed` to slow the audio back to natural pace, which
    transcribes far better than the sped-up audio (see docs/speed-test.json).
    """
    filters = ["-af", atempo_chain(speed)] if speed and speed > 1 else []
    # max_seconds is recorded time: it cuts a recording that was cut off, at the point we trust.
    limit = ["-t", f"{max_seconds:.3f}"] if max_seconds else []
    _run(
        [
            "-i",
            str(video),
            *limit,
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            *filters,
            "-b:a",
            "64k",
            str(out),
        ]
    )
    return out


def split_audio(audio: Path, out_dir: Path, seconds: int = 600) -> list[tuple[float, Path]]:
    """Cut audio into ~10 minute chunks (a long lecture is too big for one transcription call).

    Returns (start offset in seconds, path) per chunk.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "-i",
            str(audio),
            "-f",
            "segment",
            "-segment_time",
            str(seconds),
            "-c",
            "copy",
            str(out_dir / "chunk_%04d.mp3"),
        ]
    )
    return [(float(i * seconds), p) for i, p in enumerate(sorted(out_dir.glob("chunk_*.mp3")))]


def atempo_chain(speed: float) -> str:
    """ffmpeg filter that slows audio by `speed`x. Each atempo stage only goes down to 0.5."""
    if speed < 1:
        raise ValueError("speed must be >= 1")
    factor = 1 / speed
    stages = []
    while factor < 0.5:
        stages.append("atempo=0.5")
        factor /= 0.5
    stages.append(f"atempo={factor:.6f}")
    return ",".join(stages)


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
