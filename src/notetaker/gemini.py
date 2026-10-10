"""Thin wrapper around the Gemini API: file upload, JSON transcription and note generation."""

import json
import re
import time
from pathlib import Path

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

MODEL = "gemini-3.8-flash"
# Tried in order when the preferred model is overloaded (503) or out of quota (429). Each model has
# its own free-tier quota, so more models means more requests per day.
FALLBACK_MODELS = ("gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash")
# Lighter models: good enough for transcription, not used for the notes.
LITE_MODELS = ("gemini-3.5-flash-lite", "gemini-3.1-flash-lite")
DAILY_QUOTA_S = 600  # a 429 that says "retry in more than this" is a daily quota, not a rate limit


class QuotaExhausted(Exception):
    """Every model is out of free-tier quota. Wait `retry_after_s` instead of failing."""

    def __init__(self, retry_after_s: float):
        super().__init__(
            f"Gemini daily quota used up; resets in about {retry_after_s / 3600:.1f} h"
        )
        self.retry_after_s = retry_after_s


_exhausted_until: dict[str, float] = {}  # model -> time its quota comes back


def client(api_key: str | None = None) -> genai.Client:
    return genai.Client(api_key=api_key) if api_key else genai.Client()


RETRYABLE_CODES = {429, 500, 503, 504}


def retry_after_seconds(e: Exception) -> float | None:
    """Seconds the API says to wait, from its 'retryDelay' ('74681s') or 'retry in 20h44m' text."""
    text = str(e)
    m = re.search(r"retryDelay['\"]?:\s*['\"]?([\d.]+)s", text)
    if m:
        return float(m.group(1))
    m = re.search(r"retry in (?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?", text)
    if m and any(m.groups()):
        h, mi, sec = (float(x) if x else 0.0 for x in m.groups())
        return h * 3600 + mi * 60 + sec
    return None


def generate(c: genai.Client, contents, config=None, models=FALLBACK_MODELS, base_s: float = 4.0):
    """generate_content that moves to the next model when one is overloaded or out of quota.

    Models whose daily quota is gone are skipped until it resets. When every model is out, raises
    QuotaExhausted so the caller can wait for the reset instead of failing.
    """
    last = None
    for rnd in range(3):
        now = time.time()
        live = [m for m in models if _exhausted_until.get(m, 0) <= now]
        if not live:
            raise QuotaExhausted(max(min(_exhausted_until[m] for m in models) - now, 60.0))
        for model in live:
            try:
                return c.models.generate_content(model=model, contents=contents, config=config)
            except genai_errors.APIError as e:
                wait = retry_after_seconds(e) if e.code == 429 else None
                if wait is not None and wait > DAILY_QUOTA_S:
                    _exhausted_until[model] = time.time() + wait
                    continue
                if e.code not in RETRYABLE_CODES:
                    raise
                last = e
        time.sleep(base_s * 2**rnd)
    if last is not None:
        raise last
    raise QuotaExhausted(60.0)


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
    resp = generate(
        c,
        [f, TRANSCRIBE_PROMPT],
        types.GenerateContentConfig(response_mime_type="application/json", temperature=0.0),
        models=(model, *[m for m in (*FALLBACK_MODELS, *LITE_MODELS) if m != model]),
    )
    return parse_segments(resp.text)
