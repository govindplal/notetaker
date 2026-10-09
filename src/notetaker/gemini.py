"""Thin wrapper around the Gemini API: file upload, JSON transcription and note generation."""

import json
import time
from functools import partial
from pathlib import Path

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

MODEL = "gemini-3.8-flash"


def client(api_key: str | None = None) -> genai.Client:
    return genai.Client(api_key=api_key) if api_key else genai.Client()


RETRYABLE_CODES = {429, 500, 503, 504}


def with_retries(call, attempts: int = 6, base_s: float = 5.0):
    """Run `call()`, retrying rate limits and overload errors with exponential backoff."""
    for i in range(attempts):
        try:
            return call()
        except genai_errors.APIError as e:
            if e.code not in RETRYABLE_CODES or i == attempts - 1:
                raise
            time.sleep(base_s * 2**i)


def upload_and_wait(c: genai.Client, path: Path, mime: str, timeout_s: float = 600) -> types.File:
    f = c.files.upload(file=path, config=types.UploadFileConfig(mime_type=mime))
    deadline = time.time() + timeout_s
    while f.state and f.state.name == "PROCESSING":
        if time.time() > deadline:
            raise TimeoutError(f"Gemini file processing timed out for {path.name}")
        time.sleep(3)
        f = c.files.get(name=f.name)
    if f.state and f.state.name != "ACTIVE":
        raise RuntimeError(f"Gemini file {path.name} ended in state {f.state.name}")
    return f


TRANSCRIBE_PROMPT = """Transcribe this lecture audio.

Rules:
- The speaker mixes English and Hindi in the same sentence. Keep every word in the
  language it was spoken in. Do not translate.
- Write Hindi words in Roman script (romanised Hindi, e.g. "matlab", "samajh"), as spoken.
- Include technical terms exactly as said.
- Split into segments of one to two sentences each.
- Give each segment start and end times in seconds from the start of the audio.

Return JSON only: a list of objects {"start": number, "end": number, "text": string}."""


def parse_segments(raw: str) -> list[dict]:
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError("transcript JSON is not a list")
    segs = []
    for item in data:
        segs.append(
            {
                "start": float(item["start"]),
                "end": float(item["end"]),
                "text": str(item["text"]).strip(),
            }
        )
    return [s for s in segs if s["text"]]


def transcribe(c: genai.Client, audio: Path, model: str = MODEL) -> list[dict]:
    f = upload_and_wait(c, audio, "audio/mpeg")
    resp = with_retries(
        partial(
            c.models.generate_content,
            model=model,
            contents=[f, TRANSCRIBE_PROMPT],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.0,
            ),
        )
    )
    return parse_segments(resp.text)


NOTES_PROMPT = """You are writing study notes for a lecture. The transcript is in English
and Hindi (romanised); keep Hindi terms as spoken and explain them in English where useful.

Write detailed Markdown notes for the transcript section below:
- Use `##` headings for topics and `###` for subtopics. Keep the heading names stable.
- Explain each concept clearly, with examples where the lecture gives them.
- Put formulas in LaTeX ($...$ inline, $$...$$ for blocks).
- Put comparisons and lists in Markdown tables or bullet lists.
- For a process, architecture, or flow that is shown on a slide or board, redraw it as a
  Mermaid diagram in a ```mermaid block. Use the attached frames for slide and board content.
- Slide and board text should appear as text in the notes, not as image references.
- Add a `[mm:ss]` timestamp at the start of each subtopic so the reader can find it in the video.
- Do not invent content the lecture did not cover. If something is unclear, say so.

Transcript section ({start}-{end}):
{transcript}
"""


def _mmss(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60:02d}:{s % 60:02d}"


def write_notes(
    c: genai.Client,
    segments: list[dict],
    frames: list[tuple[float, Path]],
    window_s: float = 900.0,
    model: str = MODEL,
) -> str:
    """Write notes window by window (default 15 min). Frames go to the window they fall in."""
    if not segments:
        return ""
    end_t = segments[-1]["end"]
    parts = []
    t0 = 0.0
    while t0 < end_t:
        t1 = t0 + window_s
        window = [s for s in segments if t0 <= s["start"] < t1]
        if window:
            text = "\n".join(f"[{_mmss(s['start'])}] {s['text']}" for s in window)
            win_frames = [
                types.Part.from_bytes(data=p.read_bytes(), mime_type="image/jpeg")
                for ts, p in frames
                if t0 <= ts < t1
            ]
            prompt = NOTES_PROMPT.format(
                start=_mmss(t0), end=_mmss(min(t1, end_t)), transcript=text
            )
            resp = with_retries(
                partial(
                    c.models.generate_content,
                    model=model,
                    contents=[*win_frames, prompt],
                    config=types.GenerateContentConfig(temperature=0.2),
                )
            )
            parts.append(resp.text.strip())
        t0 = t1
    return "\n\n".join(parts)
