# Contributing

Thanks for taking a look. Notetaker is a small single-user tool; bug reports, fixes and support for more
course sites are all welcome.

## Set up

```bash
git clone https://github.com/govindplal/notetaker.git
cd notetaker
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

Python 3.12 and [uv](https://docs.astral.sh/uv/) are required. The tests need neither Chrome,
ffmpeg, a GPU nor a Claude login: speech and notes are faked.

## Before you open a pull request

- Keep tests and lint green, and add a test for new behaviour or a fixed bug.
- Keep changes small and explain the problem they solve.
- Read `docs/architecture.md` first. The main rule: every stage must stay **resumable**, and finished
  work must never be lost by a stop, crash or retry.
- Never commit anything from `data/` (it holds a logged-in browser profile), `.env`, or any course
  content, transcripts or screenshots.

## Good first contributions

- Page-state detection for another course platform (`src/notetaker/capture/state.py`).
- A different `Transcriber` or `NoteWriter` implementation.
- Another note style (`NOTETAKER_STYLE_FILE`) worth sharing as an example.
- Testing on macOS or Linux, which the author has not done.

## Responsible use

This project records video you already have legitimate access to. Please don't add features that
download or decrypt streams, bypass logins or player restrictions, or run lectures in parallel.
