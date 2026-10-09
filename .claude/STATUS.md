# Status (rewrite at every checkpoint; snapshot, not a log; under ~40 lines)

Updated: 2026-10-09

## Now
- Milestone 1 (skeleton) done: uv project, SQLite schema, resumable job queue, 11 tests green, committed and pushed to private repo github.com/govindplal/notetaker (main).
- Milestone 2 offline pipeline works end to end on the 4m33s sample (24 segments, 11 frames kept, notes written). Output checked by reading it; only the first half of the milestone (English-heavy clip) is covered.
- Plan approved: `docs/PLAN.md` (stack, pipeline, milestones). Stack decided: Python + FastAPI/HTMX + SQLite, Chrome container over CDP, Gemini Flash for transcription and notes, free Oracle ARM VM.
- Site probe done on 100xdevs.com AI/ML course (course 23, 33 videos): plain HLS from `transcoded-video.b-cdn.net`, no DRM. Direct HLS fetch with a plain request (no cookies/referer) returns 403 from the CDN, so the site restricts it; we did not work around that. Capture is on hold pending the owner's decision.
- `model-router` mod is installed user-level at `~/.claude/skills/model-router` (edit that copy; tune rules in `hooks/classify.ts`; `/route [tier]` pins a tier).

## Next
- Milestone 2 follow-ups: test on a clip with real Hindi speech; add `.env` loading to the CLI; diagram crops (bbox) not built yet.
- Milestone 3 feasibility (2x in-browser recording) verified on lecture 1: 20 wall-s gave 37.8 media-s and 3.6 MB of audio+video via MediaRecorder on captureStream(). Player allows one playing device per account; the worker must pause on 'Video already playing'.
- Milestone 3 (capture): direct download is blocked (403). Do not spoof referer/cookies. Options for the owner: real-time recording of normal playback, or manual video/slide upload into the pipeline.

## Open questions / blocked on the user
- Do the 100xdevs course terms allow downloading lecture streams? Blocks the capture milestone (3).
- (Done) Gemini key is in `.env`; ffmpeg installed.
- Sample video for milestone 2: C:\Users\govin\Videos\2026-10-09\Sample.mp4 (4m33s, 1080p, 43 MB).

## Learned this project (not yet in CLAUDE.md)
- Mods are only auto-loaded from `.claude/skills/<name>` (project) or `~/.claude/skills/<name>`.
- In Git Bash, `claude -p "/cmd"` needs `MSYS_NO_PATHCONV=1`.
- The lecture list on 100xdevs shows ~128 videos across all the owner's courses; the AI/ML one has 33.
