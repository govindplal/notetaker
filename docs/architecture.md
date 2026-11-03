# Architecture

Notetaker turns a video course into a static website of notes. It is a small, single-user pipeline
built around one idea: **every stage is resumable and saves its progress**, so a stop, a crash, a
sign-in expiry or an AI usage limit never costs finished work.

## The pipeline

One lecture at a time, in course order (later notes depend on earlier ones):

```
capture -> transcribe -> frames -> notes -> cleanup
```

| Stage | Module | What it does |
|---|---|---|
| capture | `capture/record.py`, `pipeline.capture` | Opens the lecture in a signed-in Chrome, presses the player's own Play, sets the speed (2x), and records `video.captureStream()` with `MediaRecorder` inside the page. Chunks are streamed to a `.webm` file. Progress is checkpointed every couple of seconds. |
| transcribe | `asr.py`, `pipeline.transcribe` | Decodes the recording with ffmpeg, slows the audio back to natural pace (`atempo`), and transcribes it with faster-whisper in one pass. Segments are saved as they arrive. |
| frames | `media.py`, `pipeline.frames` | Samples a frame every 2 s of lecture time and keeps one per distinct slide or board state (perceptual hash). The recording is deleted afterwards. |
| notes | `notes/`, `pipeline.notes` | Builds a prompt from the transcript, the frames and the course **ledger**, asks the note writer for structured JSON, validates it, merges it into the lecture's stored notes and refreshes the HTML. |
| cleanup | `pipeline.cleanup` | Deletes the lecture's recording, audio, transcript and frames. Nothing but the notes is kept. |

## Why it is built this way

**Resumable parts.** A lecture is recorded in parts. When recording stops for any reason, the part
captured so far is kept, the next run continues from where it ended, and notes are written per part
and merged (`notes.covered_until` records how far the stored notes reach). Speech near the end of a
part that may still be cut off is not treated as final until more has been recorded.

**A job queue in SQLite.** `jobs` has one row per (lecture, stage). `jobs.claim_next` takes the oldest
ready job inside a transaction. Failures retry with exponential backoff; waits that are not failures
(another device is playing, sign-in expired, usage limit) are deferred without using up an attempt.
A file lock (`worker.lock`) guarantees one worker, and a `STOP` file asks it to finish the current
part and exit.

**The ledger.** After each lecture, every section becomes a row (`L3-c2`: title, summary, key points).
The next lecture's prompt includes the ledger of everything taught so far and tells the writer to
drop anything already covered and to say what is new when it extends an earlier topic. This keeps a
long course from repeating itself.

**Real-time recording, not downloading.** The recorder plays the lecture in your own browser session
and records what the player shows. It never fetches or decrypts the stream. This is also why it is
slow (about half the lecture length at 2x) and why it plays one lecture at a time.

**Notes are structured data.** The writer returns `LectureNotes` JSON (`notes/model.py`): sections of
typed blocks (text, bullets, table, formula, code, example, callout, figure), a glossary, and a
"left out" list. Figures are described as data and drawn by `render/figures.py`, so the notes do not
depend on screenshots, and invalid or degenerate model output is rejected and retried.

**Interfaces, not providers.** `NoteWriter` (`notes/writer.py`) turns a prompt and frame files into
JSON; the shipped implementation is `ClaudeWriter` (`claude -p`, headless). `Transcriber` (`asr.py`)
yields timed segments; the shipped one is `WhisperTranscriber` (GPU with CPU fallback). Either can be
replaced without touching the pipeline.

## Other parts

- `worker.py`: the loop that claims jobs, handles sign-in/busy/usage-limit outcomes and stops cleanly.
- `store.py`, `db.py`: courses, lectures, notes, ledger (SQLite, WAL).
- `export.py`, `render/`: static HTML for each course (Jinja2 templates, one fixed theme).
- `web.py`: the local control page (FastAPI + HTMX, bound to 127.0.0.1). It only starts and stops the
  same worker the command line uses.
- `cloud/`: an optional Modal deployment of the same pipeline (see `cloud/README.md`).

## Data and privacy

Everything lives under `NOTETAKER_DATA_DIR` (default `data/`, git-ignored): the database, the work
folders, the finished notes and the recorder's Chrome profile (your logged-in session). Transcripts
and frames are working files only and are deleted when a lecture is finished. Never share or commit
that folder.
