# Handoff: everything a new session needs

Read this once at the start of a session. `.claude/STATUS.md` is the short snapshot; this is the long version (requirements, decisions, how the code works, what went wrong). Update it when something durable changes.

## What the owner wants
Notetaker watches course lectures for the owner and writes notes they read later on a web page. Single user, hosted on a server (never on the laptop), free if possible, Python, free Gemini tier acceptable. One-time sign-in. The course site allows only one active player per account.

First target: 100xdevs.com "AI and ML Bootcamp", course 23, 33 lectures (about 55 h, 1-2.5 h each), English + Hindi/Hinglish, no captions. Lecture list: `data/lectures-ai-ml.tsv` (git-ignored; ids were read from the site by clicking sidebar rows). The owner said recording at normal or higher player speed is within the terms ("you are good to go with the terms"). Downloading is not allowed and the CDN returns 403 to direct fetches; do not try to bypass it.

## Notes requirements (the owner stated these explicitly)
- Written in the owner's handwriting style (from their 55-page handwritten PDF `C:\Users\govin\Downloads\355.pdf`; style summary in `src/notetaker/notes/style.md`).
- Only useful knowledge. No chit-chat, no off-topic Q&A.
- Do not repeat what an earlier lecture covered; include genuinely new additions (per-course concept ledger, sections are `new` or `extends`).
- HTML like https://github.com/ThariqS/html-effectiveness, mainly `14-research-feature-explainer.html`: one fixed palette, identical in light and dark (ivory #FAF9F5, slate #141413, clay #D97757, oat #E3DACC, olive #788C5D, sky #6A8CAF).
- SVG figures where relevant, like the reference. Figures are data (`Figure` kind flow|layers) drawn by `render/figures.py`; the model never writes markup.
- NEVER store or show transcripts or frames. Useful frame content goes into the notes as text/tables/figures. Working files are deleted once a lecture's notes exist. Tests assert there are no transcript/frame tables.
- The "Left out" list is not shown on the page (kept in JSON only).

## Recording policy and decisions
- Recorder: the real course player in a signed-in Chrome (`--remote-debugging-port=9222 --user-data-dir=data/chrome-profile`), driven over CDP. Click Play (real user gesture), seek, set playback rate, `captureStream()` + MediaRecorder, chunks streamed to Python through `expose_function`.
- Default speed 2x (speed test: 2x recommended, 3x usable, 4x+ loses speech; `docs/speed-test.json`). Audio is slowed back to 1x with ffmpeg `atempo` before transcription. No video re-encode (dropped by the owner's decision).
- Continuous recording, not fixed parts. The owner asked: save whatever was watched on any failure, and on the next run resume from where the recording stopped, regardless of where the site resumes. Implemented in `pipeline.capture`: checkpoint file every poll, `_save_cutoff` on any exception, salvage of leftover `.partial` files after a hard kill, explicit seek to the last part's end, about 3 s real-time loss at most. State is `capture.json` (`{duration, parts[], complete}`).
- Transcription in 30-minute chunks per part; frames sampled from the recording and deduped, timestamps scaled by the speed.
- The memory-pressure kill that ended the first full run was a laptop/Claude Code problem. On the server it will not exist, but save-and-resume stays (reboots, network drops, device takeover).
- Do NOT start the full-course run on your own. Start it only when the owner says so (it is about 30 h of wall time at 2x). If started from Claude Code, use `CLAUDE_CODE_DISABLE_BG_SHELL_PRESSURE_REAP=1` or the owner's own terminal.

## Pipeline and worker
Stages per lecture: capture, transcribe, frames, notes, cleanup (SQLite `jobs`). The worker runs one lecture at a time, in order. Deferrals that do not use an attempt: DeviceBusy (600 s), NeedsSignIn (course paused), QuotaExhausted (wait for reset, max 24 h). A failed lecture blocks later ones until `retry`; `redo` resets a lecture and all later ones; running jobs are re-queued at worker start.

CLI: `python -m notetaker add-course ...`, `run [--forever --speed --max-minutes]`, `status`, `retry`, `redo`, `export`.

## Gemini free-tier quota (the binding constraint)
Free tier is about 20 requests/day/model. One 2-hour lecture needs about 5-6 requests (4 transcription chunks + notes). On 2026-10-09 all four fallback models were exhausted by tests; reset about 20 h later. Rough estimate: 10-12 lectures/day if every model has the same limit, so 3-4 days free for 33 lectures (unverified for lite models). Options put to the owner (their choice is pending as of 2026-10-10):
1. Start now on free; recording needs no Gemini, processing waits for quota.
2. Wait for the reset, then start.
3. Paid Gemini (about $7-10 for the whole course, unverified estimate).
Other ideas not yet put to them: Groq Whisper for transcription (free but weaker on Hinglish), capture ahead while waiting.

## Known gaps
- No full-length (1-2 h) lecture or Hindi-heavy lecture has been run yet. Lecture 1 has only a 3-minute test (stale test notes in `data/notes/course-1/`); all 33 lectures are reset to pending.
- The continuous-session capture was verified with a fake browser in tests, and a live kill/restart on the earlier fixed-slice version. Not yet killed live in its final form.
- Hosting is not built (Docker with Chrome + Xvfb + noVNC for sign-in, FastAPI web UI, free VM). `docs/PLAN.md` has the plan.
- The 33 lectures' real notes do not exist yet; when the owner asks "show me the notes", verify the page is current before opening it.

## Lessons from mistakes (do not repeat)
- I once stored transcripts/frames after misreading the owner; they never want them.
- I once deleted a partial recording after a kill; never delete captured media that is the only copy.
- I opened stale pages for the owner (a failed lecture, an empty figure). Verify content before showing it.
- Do not give time estimates without checking the arithmetic (I said 2-3 h for the course; it is about 30 h at 2x).
- Model-written SVG/Mermaid in JSON was unreliable; keep figures as data.
- An edit that silently did not apply (ledger `key_points`) went unnoticed; re-grep after format-triggering edits.

## Environment notes
- Windows, Git Bash. ffmpeg via winget (new shell for PATH). Docker only inside WSL.
- `.env` holds `GEMINI_API_KEY` (git-ignored; the key was pasted in chat, treat it as secret; do not write it into any file that is committed).
- Repo: github.com/govindplal/notetaker (private, `main`). Commit and push after every green checkpoint.
- Tests: `uv run pytest` (69 passing at `293f8de`). Lint: `uv run ruff check . && uv run ruff format --check .`
