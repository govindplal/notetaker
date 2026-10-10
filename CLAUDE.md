# notetaker

Notetaker is a hosted service that watches course lectures on the owner's behalf and writes detailed notes. The owner points it at a course URL and signs in once. It then works through the videos unattended, and the owner reads the notes later on a web page (e.g. on a break at work). Single user. Main constraint: the courses are on custom sites with no captions or transcripts, and the audio is bilingual (English + Hindi, often mixed mid-sentence), so it must get the audio itself and transcribe it well.

## Architecture (non-negotiable principles)
- Runs on a host, never on the owner's laptop. A long-running worker does the work; the web UI is read-only plus "add course" and "re-sign-in".
- Pipeline per lecture: discover -> capture media -> transcribe -> (extract slide frames) -> write notes. Each stage is a resumable job with its state in the DB, so a crash or restart never redoes finished stages.
- Capture prefers intercepting the real media stream (HLS/MP4 from network requests) and downloading with ffmpeg. Real-time playback recording is the fallback only.
- Sign-in is one-time: the saved browser session (Playwright storageState) is stored encrypted on the host. When it expires, the UI shows "re-sign-in needed" and the worker pauses that course. Never store the course password.
- Transcription sits behind a `Transcriber` interface (cloud ASR first, local Whisper possible later). Hindi/English code-switching must be kept as spoken, not force-translated.
- Notes are written in English, keep Hindi terms in Devanagari or Roman as spoken, carry timestamps that link back to the video position, and include slide/board content from extracted frames.
- Be polite to the course site: sequential downloads, low rate, no parallel hammering.

## Built vs still to build
Read `docs/HANDOFF.md` first: it holds the owner's requirements, decisions and past mistakes.
Built: job queue and DB, discovery, in-browser recorder (real playback at 2x, since downloading is not allowed), chunked Gemini transcription, structured notes with ledger dedupe, static HTML export, worker with crash/quota handling, CLI.
Still to build: web UI (courses, status, notes viewer, password), hosting and deploy. Capture in this repo is recording, not stream download: the architecture bullet above that prefers stream interception is superseded.

## Stack (decided; full plan in `docs/PLAN.md`)
- Python 3.12, `uv`, `ruff`, `pytest`.
- Web: FastAPI + Jinja2 + HTMX, Markdown rendered server-side, KaTeX + Mermaid in the browser.
- DB and queue: SQLite (WAL) with a `jobs` table polled by the worker.
- Browser: one long-lived Google Chrome in its own container (Xvfb + noVNC for the one-time sign-in), driven by the worker over CDP.
- Capture: `yt-dlp`/`ffmpeg` HLS download (100xdevs serves plain HLS from Bunny CDN, no DRM seen). Real-time recording is a fallback only.
- Transcription and notes: Gemini Flash (free tier), behind `Transcriber` / `NoteWriter` interfaces.
- Hosting: Docker Compose on a free Oracle ARM VM, Caddy + DuckDNS (or Cloudflare Tunnel).
- First target course: 100xdevs.com "AI and ML Bootcamp" (course 23, 33 videos, ~55 h).

## Commands
- Test: `uv run pytest`
- Lint: `uv run ruff check . && uv run ruff format --check .`

## Gotchas
- ffmpeg is installed via winget (open a new shell, or add the WinGet Packages Gyan.FFmpeg bin dir to PATH). Docker only exists inside WSL (`wsl -e docker ...`). Keep unit tests independent of both.
- Owner confirmed recording playback is within the course terms; downloading is not allowed (CDN returns 403, do not bypass).
- Gemini free tier is about 20 requests/day/model; the worker waits for the reset.
- Never start the full-course run (about 30 h) unless the owner asks.

## Workflow
- Explore and plan before non-trivial edits; use `/start-feature` and `/fix-bug`, and `/wrap-up` to end a session.
- Run tests/lint and report real results before calling work done.
- **Ship every checkpoint:** after each completed feature (tests/lint green), commit and push to `origin` (`https://github.com/govindplal/notetaker`, private, branch `main`). If it deploys, confirm the deploy succeeded and give the live URL.

## Current status
@.claude/STATUS.md
