# Notetaker

[![ci](https://github.com/govindplal/notetaker/actions/workflows/ci.yml/badge.svg)](https://github.com/govindplal/notetaker/actions/workflows/ci.yml) [![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Notetaker works through an online video course for you and writes detailed study notes, so you can read instead of typing while you watch.

You point it at a course, sign in once, and press Start. For each lecture it plays the video in your own signed-in Chrome, records the audio and the slides in real time, transcribes the speech, and has Claude write structured notes: sections, diagrams, code, tables and a glossary. You read the result later as a static web page. Transcripts and screenshots are never kept in the notes.

It was built for long technical courses on sites with no captions or transcripts, with English and Hindi mixed in the same sentence.

## What you get

- **Notes as a static site.** One page per lecture, with a table of contents, redrawn figures, code blocks, tables and hover glossary terms. No transcripts, no frames.
- **No repeats across lectures.** A ledger of what earlier lectures already taught is given to the writer, so later notes only add what is new.
- **Resumable.** Every stage saves its progress. A stop, crash, sign-in expiry or Claude usage limit loses nothing; the next run continues from where it stopped.
- **A small local control page** to start and stop the worker and see status.

## How it works

```
capture -> transcribe -> frames -> notes -> cleanup        (one lecture at a time)
```

| Stage | What happens | Tool |
|---|---|---|
| capture | Plays the lecture at 2x in Chrome and records the player's own stream | Playwright over the Chrome DevTools Protocol |
| transcribe | Slows the audio back to 1x, transcribes it | faster-whisper (GPU if available, CPU otherwise) |
| frames | Keeps one frame per distinct slide or board state | ffmpeg + perceptual hashing |
| notes | Writes structured notes from transcript, frames and the ledger | `claude -p` (Claude Code, headless) |
| cleanup | Deletes the recording, audio, transcript and frames | |

State lives in a SQLite database (a `jobs` table polled by the worker) and in a work folder per lecture.

## Requirements

- Python 3.12 and [uv](https://docs.astral.sh/uv/)
- Google Chrome
- [ffmpeg](https://ffmpeg.org/) on your PATH
- [Claude Code](https://claude.com/claude-code) installed and signed in (`claude` on your PATH). Notes are written with your own Claude plan, so a full course can hit your usage limit and need several runs.
- Optional: an NVIDIA GPU with CUDA for faster transcription (CPU works, slower)

Developed and tested on Windows 11 with Git Bash. Other systems are untested.

## Install

```bash
git clone https://github.com/govindplal/notetaker.git
cd notetaker
uv sync
cp .env.example .env     # optional: settings are listed below
```

## Use

1. **List the lectures** in a TSV file, one per line, in teaching order: `title<TAB>lecture page URL`.
2. **Register the course:**
   ```bash
   uv run python -m notetaker add-course --url COURSE_URL --title "My Course" --lectures lectures.tsv
   ```
3. **Start the control page and sign in once.** Its *Sign in* button opens a Chrome window, at your course's page, with its own profile (`data/chrome-profile`); sign in there and leave it.
   ```bash
   uv run python -m notetaker ui        # http://127.0.0.1:8765
   ```
4. **Run the worker** (from the page, or from the command line):
   ```bash
   uv run python -m notetaker run
   ```
5. **Read the notes** in `data/notes/course-1/index.html`.

Other commands: `stop` (finish the current part and exit), `status`, `retry --lecture ID`, `redo --lecture ID` (redo a lecture and every later one, since their notes depend on it), `export --course ID` (rebuild the HTML).

Before a real run, try a short one: set `NOTETAKER_MAX_MEDIA_S=150` and a separate `NOTETAKER_DATA_DIR`, so a test never touches your real data.

## Settings

All optional, set in `.env` or the environment.

| Variable | Default | Meaning |
|---|---|---|
| `NOTETAKER_DATA_DIR` | `data` | Where the database, work files, notes and Chrome profile live |
| `NOTETAKER_CDP_URL` | `http://127.0.0.1:9222` | The recorder Chrome's debugging address |
| `NOTETAKER_SPEED` | `2.0` | Playback speed while recording |
| `NOTETAKER_MAX_MEDIA_S` | unset | Cap per lecture, for short test runs only |
| `NOTETAKER_WHISPER_MODEL` | `large-v3-turbo` | faster-whisper model |
| `NOTETAKER_WHISPER_LANGUAGE` | auto-detect | e.g. `en`. Forcing the wrong language produces gibberish; test on a clip first |
| `NOTETAKER_WHISPER_PROMPT` | unset | Optional vocabulary hint for Whisper |
| `NOTETAKER_CLAUDE_MODEL` | account default | Model for the notes |
| `NOTETAKER_STYLE_FILE` | bundled style | A Markdown file with your own note-writing style |
| `NOTETAKER_CHROME` | auto-detected | Path to Chrome/Chromium for the sign-in button |
| `NOTETAKER_SIGNIN_URL_HINTS`, `NOTETAKER_SIGNIN_TEXT_HINTS`, `NOTETAKER_BUSY_TEXT` | common defaults | Comma-separated phrases that tell Notetaker a page is a sign-in page or "another device is playing", for sites worded differently |

`.env.example` lists every setting with comments.

## Responsible use

Notetaker records the video **you are already allowed to watch**, in your own browser session, at playback speed. It does not download or decrypt anything, and it does not bypass a login or a player. Please keep it that way:

- Use it only on courses you have paid for or have legitimate access to, and check the site's terms about automated or recorded playback.
- It plays one lecture at a time, on purpose. Don't run several in parallel or hammer a site.
- Don't play the same account on another device while it records; most sites allow one player at a time.
- The notes are derived from someone else's work. They are for your own study; don't publish or redistribute them without the author's permission.
- The sign-in profile stays on your machine. Never share `data/` and never commit it.

## Limitations

- Developed and tested on Windows 11 with Chrome. macOS and Linux are untested (the Chrome finder and the cloud runner are written to work there).
- Page detection (sign-in page, "another device is playing") uses generic hints. Sites worded differently need the `NOTETAKER_*` hint variables, or a change in `src/notetaker/capture/state.py`.
- The player is driven with a real click on a "Play video" button, falling back to clicking the `<video>` element. A player that works differently may need a change in `capture/record.py`.
- Whisper's quality on mixed-language speech depends on the language setting; test before a long run.
- Claude usage limits apply, and the exact limit message is only partly detected, so a run may need restarting.
- It records in real time (half the lecture length at 2x), one lecture at a time. A 50-hour course takes more than a day.

## Optional: run in the cloud

`cloud/` contains an experimental deployment on [Modal](https://modal.com) that records, transcribes and writes the notes with your laptop off, one lecture at a time. It needs a Modal account, a one-time remote sign-in and a Claude token stored as a Modal secret. It is not covered by the tests and costs real money. See [`cloud/README.md`](cloud/README.md).

## Project layout

```
src/notetaker/   the pipeline: capture/, asr.py, media.py, notes/, render/, worker.py, web.py, cli
cloud/           optional Modal deployment (see cloud/README.md)
docs/            architecture.md: how it works and why
tests/           unit tests (speech and notes are faked; no Chrome, GPU or Claude needed)
```

## Development

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

Notes are written by a pluggable `NoteWriter` (`src/notetaker/notes/writer.py`) and speech is transcribed by a `Transcriber` (`src/notetaker/asr.py`), so another provider can be added without touching the pipeline.

See [`docs/architecture.md`](docs/architecture.md) for the design and [`CONTRIBUTING.md`](CONTRIBUTING.md) to contribute.

## License

[MIT](LICENSE) © 2026 Govind P Lal. The licence covers this software only; it gives you no rights to any course content.
