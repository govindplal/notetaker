# Status (rewrite at every checkpoint; snapshot, not a log; under ~40 lines)

Updated: 2026-11-01. Long version: `docs/HANDOFF.md` (stale: Gemini, laptop-only). `CLAUDE.md` is current except it still says "runs on the laptop".

## Now: full course run on Modal (owner asked 2026-10-31: all 33 lectures from scratch, laptop off)
- Started 2026-10-31 18:37 UTC. Capture + transcription + frames run in Modal (`cloud/modal_app.py`, deployed app `notetaker`, workspace govind-p-lal). Notes also run in Modal (since 2026-11-01 ~18:53 UTC): `notes_loop` uses `claude -p` with Modal secret `claude-token` (CLAUDE_CODE_OAUTH_TOKEN), DB copy in Volume `notetaker-data` (`notetaker.db`, uploaded once; the cloud DB is now the truth, the local one is stale), HTML in the Volume under `notes/course-1/`; read it with `ctl.py pull-notes` (-> `data/notes-cloud`). Waits an hour on anything that looks like a usage limit; watchdog restarts it. `cloud/notes_follower.py` (laptop) is the old path, not in use. Laptop can be off. The token was pasted in a chat once: rotate it (`claude setup-token`, then `modal secret create claude-token ... --force`).
- Progress / control: `uv run --with modal python cloud/ctl.py status|stop|resume|start|whisper|asr-test` (PYTHONUTF8=1).
- Modal pieces: `capture_next` (CPU, Chromium + Xvfb, max 1 container, chains itself), `post` -> `transcribe_gpu` (GPU list L4/A10G/L40S/T4; L4 had no capacity once), `watchdog` cron every 20 min (restarts a dead chain, re-runs a lost post). Volumes: `notetaker-profile` (Chrome sign-in), `notetaker-data` (lectures.json, work/, status/). Dict `notetaker-state` (running/paused/stop/heartbeat/events/whisper).
- Whisper in Modal: large-v3-turbo, language `en`, Hinglish initial prompt (~60x real time on L4). Tested on 2 clips: this instructor speaks mostly English; forcing `hi` turns English into Devanagari gibberish; auto-detect risks that if a lecture opens in Hindi. Local default is still auto-detect (`NOTETAKER_WHISPER_LANGUAGE` to change).
- Local DB reset from scratch before the run (backup `data/notetaker.db.bak-2026-10-31`). Don't press Start on the local control page while the cloud run is going (two players on one account).
- Sign-in: `scripts/modal_login.py` (noVNC tunnel, random VNC password, 20 min). Rerun it if the run pauses with "needs sign-in", then `ctl.py resume`.

## Not verified yet
- Notes from a full-length lecture (one `claude -p` call per lecture, 30 min timeout); real usage-limit message text (follower waits 1 h on anything that looks like a limit); Claude using frames; session lifetime of the Modal sign-in.
- Notes still need the laptop on. To go fully laptop-off: `claude setup-token` -> Modal secret, and run the notes stage in Modal (DB on a Volume).

## Parked
- Public/student version: private repo github.com/govindplal/notetaker-public (`docs/ARCHITECTURE.md`). After the personal one is complete.

## Learned this project
- Mods only auto-load from `.claude/skills/<name>`. In Git Bash, `claude -p "/cmd"` needs `MSYS_NO_PATHCONV=1`. AI/ML course has 33 lectures.
- Compound `rm` commands get denied; single simple `rm` calls work. Run long jobs with the Bash tool's `run_in_background`, not `nohup &`.
- ffmpeg is not on the Bash PATH (see CLAUDE.md gotchas). Modal CLI on Windows needs `PYTHONUTF8=1` (charmap errors).
- Modal: Chromium needs `--no-sandbox`; delete stale Singleton* files before launch; `modal.forward` tunnels need the owner's OK.
