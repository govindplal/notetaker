# Status (rewrite at every checkpoint; snapshot, not a log; under ~40 lines)

Updated: 2026-10-09

## Now
- Milestone 1 (skeleton) done: uv project, SQLite schema, resumable job queue, 11 tests green, committed and pushed to private repo github.com/govindplal/notetaker (main).
- Plan approved: `docs/PLAN.md` (stack, pipeline, milestones). Stack decided: Python + FastAPI/HTMX + SQLite, Chrome container over CDP, Gemini Flash for transcription and notes, free Oracle ARM VM.
- Site probe done on 100xdevs.com AI/ML course (course 23, 33 videos): plain HLS from `transcoded-video.b-cdn.net`, no DRM. Capture still blocked on the terms question.
- `model-router` mod is installed user-level at `~/.claude/skills/model-router` (edit that copy; tune rules in `hooks/classify.ts`; `/route [tier]` pins a tier).

## Next
- Milestone 2: offline pipeline on a local video file (needs a Gemini API key in `.env` and ffmpeg installed).

## Open questions / blocked on the user
- Do the 100xdevs course terms allow downloading lecture streams? Blocks the capture milestone (3).
- (Done) Gemini key is in `.env`; ffmpeg installed.
- Sample video for milestone 2: C:\Users\govin\Videos\2026-10-09\Sample.mp4 (4m33s, 1080p, 43 MB).

## Learned this project (not yet in CLAUDE.md)
- Mods are only auto-loaded from `.claude/skills/<name>` (project) or `~/.claude/skills/<name>`.
- In Git Bash, `claude -p "/cmd"` needs `MSYS_NO_PATHCONV=1`.
- The lecture list on 100xdevs shows ~128 videos across all the owner's courses; the AI/ML one has 33.
