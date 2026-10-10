# Status (rewrite at every checkpoint; snapshot, not a log; under ~40 lines)

Updated: 2026-10-09

## Now
- Full pipeline works live on the owner's real 100xdevs account: `python -m notetaker add-course ...` then `run`. Recorder (2x, seeks to start) -> normalize to 1x -> chunked Gemini transcription -> slides -> structured notes in the owner's style -> HTML (lecture pages + index) -> all working files deleted. Verified on lecture 1 of the AI/ML course (first 3 min only via `--max-minutes 3`): all 5 stages done on the first attempt, nothing but notes/ledger/HTML left on disk. 54 tests pass.
- Worker rules: strictly one lecture at a time in order (ledger depends on earlier lectures); "another device playing" waits 10 min without counting as a failure; expired sign-in pauses the course; a failed lecture blocks later ones until `retry`.
- Notes: HTML copies the reference repo's feature-explainer layout (one fixed palette), figures are drawn from data (flow/layers), no frames/transcripts stored or shown. See `docs/PLAN.md`.
- Recorder Chrome runs with `--remote-debugging-port=9222 --user-data-dir=data/chrome-profile` (owner signed in once). Closing all its tabs exits Chrome. Do not open the course on another device while recording (one player per account).
- Repo: github.com/govindplal/notetaker (main). `model-router` mod is user-level at `~/.claude/skills/model-router`.

## Next
- Full run of the real AI/ML course (33 lectures): needs the lecture list (title + URL per lecture) in a TSV; automatic discovery from the course page is not built.
- Test Hindi-heavy speech and a full-length (1-2 h) lecture; check Gemini free-tier limits over a long run.
- Hosting: Docker image with Chrome + Xvfb + noVNC for the one-time sign-in, web UI to browse notes, deploy to the VM.

## Open questions / blocked on the user
- Do the 100xdevs course terms allow downloading lecture streams? Blocks the capture milestone (3).
- (Done) Gemini key is in `.env`; ffmpeg installed.
- Sample video for milestone 2: C:\Users\govin\Videos\2026-10-09\Sample.mp4 (4m33s, 1080p, 43 MB).

## Learned this project (not yet in CLAUDE.md)
- Mods are only auto-loaded from `.claude/skills/<name>` (project) or `~/.claude/skills/<name>`.
- In Git Bash, `claude -p "/cmd"` needs `MSYS_NO_PATHCONV=1`.
- The lecture list on 100xdevs shows ~128 videos across all the owner's courses; the AI/ML one has 33.
