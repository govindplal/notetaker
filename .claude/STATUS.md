# Status (rewrite at every checkpoint; snapshot, not a log; under ~40 lines)

Updated: 2026-10-10. Long version of everything (requirements, decisions, mistakes): `docs/HANDOFF.md`. Read it first in a new session.

## Now
- Pipeline works live on the owner's real 100xdevs account: recorder (2x, continuous, saves on any stop, resumes at the exact end of what was saved) -> audio slowed to 1x -> chunked Gemini transcription -> frames -> structured notes in the owner's style with ledger dedupe -> HTML (lecture pages + index) -> working files deleted. Latest commit `293f8de`, 69 tests pass, lint clean.
- Worker: one lecture at a time in order; device busy waits 10 min; expired sign-in pauses the course; Gemini daily quota waits for reset; failed lecture blocks later ones until `retry`.
- Notes never store or show transcripts/frames; HTML copies the reference repo's palette (one fixed theme); figures drawn from data.
- Recorder Chrome: `--remote-debugging-port=9222 --user-data-dir=data/chrome-profile` (owner signed in once). Do not open the course on another device while recording.
- Data (git-ignored): `data/notetaker.db` (course 1, 33 lectures all pending), `data/lectures-ai-ml.tsv`, `data/chrome-profile/`. The Gemini key is in the repo-root `.env`.

## Waiting on the owner
- Gemini free tier allows about 20 requests/day/model and today's quota is used up (resets about 20 h after 2026-10-09 evening). Owner must choose: start now on free (slow, processing waits for quota), wait for reset, or paid Gemini (about $7-10). Do not start the 30 h full-course run until they say so.

## Next
- Run the full AI/ML course at 2x (lecture 1 first; it only has a 3-minute test), then show the owner lecture 1's notes after checking the page is current.
- Test a full-length lecture and Hindi-heavy speech; live kill/restart test of the continuous capture.
- Hosting: Docker with Chrome + Xvfb + noVNC for sign-in, FastAPI web UI, deploy to a free VM (`docs/PLAN.md`).

## Learned this project
- Mods are only auto-loaded from `.claude/skills/<name>` (project) or `~/.claude/skills/<name>`.
- In Git Bash, `claude -p "/cmd"` needs `MSYS_NO_PATHCONV=1`.
- The 100xdevs account shows ~128 videos across courses; AI/ML has 33.
