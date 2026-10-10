# notetaker

Notetaker watches course lectures on the owner's behalf and writes detailed notes. The owner points it at a course URL and signs in once. Press Start and it works through the videos until stopped, and the owner reads the notes later on a web page. Single user. Main constraint: the courses are on custom sites with no captions or transcripts, and the audio is bilingual (English + Hindi, often mixed mid-sentence), so it must get the audio itself and transcribe it well.

## Architecture (non-negotiable principles)
- Runs on the owner's laptop (decided 2026-10-10: no free host fits Chrome + ASR, and the owner has no credit card). A worker does the work; a local control page (`python -m notetaker ui`) starts/stops it and shows status and notes.
- Pipeline per lecture: capture -> transcribe -> frames -> notes -> cleanup. Each stage is a resumable job with its state in the DB and its progress in the lecture's work folder, so a stop, crash or restart never redoes finished work.
- A lecture is worked in parts. Stop saves the recorded part, then notes are written for it and merged into the lecture's stored notes; the next Start continues from that point (`notes.covered_until`).
- Capture is real-time recording of the course player (downloading is not allowed), at 2x. The audio is slowed back to 1x before transcription (tested: better accuracy).
- Sign-in is one-time: the recorder Chrome keeps its own profile (`data/chrome-profile`). When it expires the course pauses and the page says so. Never store the course password.
- Transcription is local faster-whisper (`Transcriber` in `asr.py`, GPU with CPU fallback). Notes come from `claude -p` headless (`NoteWriter` in `notes/writer.py`, `ClaudeWriter`). Gemini is gone and must not come back; do not rotate API keys/projects to get quota.
- Notes are written in English, keep Hindi terms as spoken, and include slide/board content from extracted frames. Notes never store or show transcripts or frames.
- Be polite to the course site: one lecture at a time, no parallel hammering.

## Built vs still to build
Read `docs/HANDOFF.md` and `.claude/STATUS.md` first (HANDOFF is partly stale: it still describes Gemini).
Built: job queue and DB, discovery, in-browser recorder, local Whisper transcription, Claude notes with ledger dedupe and part-by-part merge, static HTML export, worker with lock/stop/usage-limit handling, CLI, local control page, `/course` command.
Not verified: Hindi/English quality of Whisper, Claude viewing slide frames, a full-length lecture, the real usage-limit message text.

## Stack
- Python 3.12, `uv`, `ruff`, `pytest`.
- Web: FastAPI + Jinja2 + HTMX (control page, local only, port 8765). Notes are static HTML under `data/notes`.
- DB and queue: SQLite (WAL) with a `jobs` table polled by the worker.
- Browser: the owner's Google Chrome with `--remote-debugging-port=9222 --user-data-dir=data/chrome-profile`, driven over CDP.
- First target course: 100xdevs.com "AI and ML Bootcamp" (course 23, 33 videos, ~55 h).

## Commands
- Test: `uv run pytest`
- Lint: `uv run ruff check . && uv run ruff format --check .`
- Run: `uv run python -m notetaker run` / `stop` / `status` / `ui`

## Gotchas
- ffmpeg is installed via winget and is NOT on the Bash PATH: add the WinGet `Gyan.FFmpeg_*/ffmpeg-*-full_build/bin` dir. Keep unit tests independent of it.
- PyAV 19 breaks faster-whisper's own audio decoding; we decode with ffmpeg and pass arrays. GPU needs the pip `nvidia-cublas-cu12` and `nvidia-cudnn-cu12` DLLs (registered in `asr.py`).
- Start long runs with the Bash tool's `run_in_background`, not `nohup ... &` (the process can be killed when the call returns).
- Owner confirmed recording playback is within the course terms; downloading is not allowed (CDN returns 403, do not bypass). Do not play the course on another device while recording.
- Claude headless uses the owner's plan: a trivial call cost about $0.17 of cached context, so a full course will hit usage limits and need several Starts.
- Never start the full-course run (about 30 h) unless the owner asks. Test with a separate data dir (`NOTETAKER_DATA_DIR=data/e2e-run`) and `NOTETAKER_MAX_MEDIA_S`.

## Workflow
- Explore and plan before non-trivial edits; use `/start-feature` and `/fix-bug`, `/course` for where things stand, and `/wrap-up` to end a session.
- Run tests/lint and report real results before calling work done.
- **Ship every checkpoint:** after each completed feature (tests/lint green), commit and push to `origin` (`https://github.com/govindplal/notetaker`, private, branch `main`).

## Current status
@.claude/STATUS.md
