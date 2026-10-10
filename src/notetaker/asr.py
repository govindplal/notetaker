"""Speech to text with a local Whisper model, behind a small `Transcriber` interface.

The whole lecture is transcribed in one pass, so there are no artificial chunk boundaries. Segments
are yielded as they are produced, so the caller can save progress as it goes.

Audio is decoded with ffmpeg rather than by faster-whisper itself: the installed PyAV rejects the
metadata option faster-whisper passes, and ffmpeg is already a requirement of the pipeline.
"""

import os
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

import numpy as np

SAMPLE_RATE = 16000


class Transcriber(Protocol):
    def transcribe(self, samples: np.ndarray, *, offset_s: float = 0.0) -> Iterator[dict]:
        """Yield {"start", "end", "text"} for 16 kHz mono float32 `samples`.

        Times are lecture seconds: `offset_s` is added to every segment, so a resumed run lines up
        with the rest of the transcript.
        """
        ...


def decode(audio: Path, start_s: float = 0.0) -> np.ndarray:
    """Decode an audio file to 16 kHz mono float32, starting at `start_s` seconds."""
    raw = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            f"{start_s:.3f}",
            "-i",
            str(audio),
            "-f",
            "f32le",
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-",
        ],
        check=True,
        capture_output=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.float32)


def _add_nvidia_dlls() -> None:
    """Make the pip-installed CUDA libraries findable on Windows (cuBLAS, cuDNN)."""
    try:
        import nvidia
    except ImportError:
        return
    for base in nvidia.__path__:
        for bin_dir in Path(base).glob("*/bin"):
            os.add_dll_directory(str(bin_dir))
            os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"


class WhisperTranscriber:
    """faster-whisper on the GPU when it works, otherwise on the CPU (int8)."""

    def __init__(self, model: str = "large-v3-turbo", device: str | None = None):
        from faster_whisper import WhisperModel

        self.model_name = model
        _add_nvidia_dlls()
        errors = []
        candidates = [("cuda", "float16"), ("cpu", "int8")]
        if device:
            candidates = [c for c in candidates if c[0] == device]
        self._model = None
        for dev, ctype in candidates:
            try:
                self._model = WhisperModel(model, device=dev, compute_type=ctype)
                self.device = dev
                break
            except Exception as e:  # missing CUDA libraries show up here, so fall back to CPU
                errors.append(f"{dev}: {e}")
        if self._model is None:
            raise RuntimeError("no usable Whisper device: " + "; ".join(errors))

    def transcribe(self, samples: np.ndarray, *, offset_s: float = 0.0) -> Iterator[dict]:
        # language=None lets Whisper detect it from the first 30 s. Hindi/English mixing is NOT yet
        # verified: a whole-lecture language guess may suit one language and mangle the other.
        segments, _info = self._model.transcribe(samples, vad_filter=True, beam_size=5)
        for s in segments:
            text = s.text.strip()
            if text:
                yield {"start": s.start + offset_s, "end": s.end + offset_s, "text": text}


def decode_recording(path: Path, speed: float, natural_s: float) -> np.ndarray:
    """A recorded part (played at `speed`x) as 16 kHz mono float32 at natural lecture pace.

    The audio is slowed back down with ffmpeg's atempo filter, and cut to `natural_s` seconds, which
    is the part's length in lecture time.
    """
    from notetaker.media import atempo_chain

    filters = ["-af", atempo_chain(speed)] if speed > 1 else []
    raw = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            *filters,
            "-t",
            f"{natural_s:.3f}",
            "-f",
            "f32le",
            "-",
        ],
        check=True,
        capture_output=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.float32)
