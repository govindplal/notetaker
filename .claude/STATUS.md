# Status (rewrite at every checkpoint; snapshot, not a log; under ~40 lines)

Updated: 2026-10-11. Long version: `docs/HANDOFF.md` (partly stale: still describes Gemini). `CLAUDE.md` is current.

## Now
- Runs on the owner's laptop (GTX 1650 Ti 4 GB, 16 GB RAM). Local faster-whisper (large-v3-turbo; GPU 0.32x real time, CPU 0.71x) + `claude -p` notes. Gemini and the Hugging Face deploy spike are deleted. 95 tests pass, lint clean.
- A lecture is worked in parts: Stop saves the recorded part, notes are written for it, lecture returns to `pending`; next Start continues and merges new notes (`notes.covered_until`). Claude usage limit stops the worker. Control page: `uv run python -m notetaker ui` -> http://127.0.0.1:8765 (Start/Stop/Retry/Resume/sign-in button, notes links). `/course` command prints where things stand.
- Verified live (separate DB `data/e2e-run`, cap 150 s): stop mid-lecture, resume, lecture finished and cleaned up, HTML exported; a real two-part Claude notes write merged correctly (sections from both parts, no repeats, a frame passed without error). Two bugs found live and fixed with tests (partial lecture not re-queued; Whisper end overrun left transcript incomplete).
- Real course data (git-ignored): `data/notetaker.db` (course 1, 33 lectures, all pending, untouched), `data/lectures-ai-ml.tsv`, `data/chrome-profile/`.

## Not verified yet
- Hindi/English quality of Whisper (`language=None`; whole-lecture language guess may mangle one language). Test on a Hindi-heavy lecture first.
- That Claude actually uses the slide frames (Read tool); real usage-limit message text (`_looks_like_limit` is a guess); a full-length lecture end to end; the control page opened in a browser (only TestClient-tested); the recorder's seek-to-0 failed once ("seek landed at 185s").

## Next
1. Hindi-heavy test: pick a lecture, run a few minutes, read the transcript and notes. Fix language handling if needed.
2. Open the control page, press Start on a short capped run, check it looks right.
3. Only when the owner says: start the real course. Expect several Starts because of Claude plan limits (a trivial call cost about $0.17 of cached context).

## Parked
- Public/student version: private repo github.com/govindplal/notetaker-public (`docs/ARCHITECTURE.md`). After the personal one is complete.

## Learned this project
- Mods only auto-load from `.claude/skills/<name>`. In Git Bash, `claude -p "/cmd"` needs `MSYS_NO_PATHCONV=1`. AI/ML course has 33 lectures.
- Compound `rm` commands get denied; single simple `rm` calls work. Run long jobs with the Bash tool's `run_in_background`, not `nohup &`.
- ffmpeg is not on the Bash PATH (see CLAUDE.md gotchas).
