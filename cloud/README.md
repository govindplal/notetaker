# Run Notetaker on Modal

This is how the author runs it, with the laptop closed. It is separate from the core package: nothing in `src/notetaker` imports it, and it is not part of the installed package or the tests. It deploys the same pipeline on [Modal](https://modal.com) so a whole course is recorded,
transcribed and noted with your laptop off, still one lecture at a time. It uses real cloud
resources and costs money, and it is not covered by the tests. Read `modal_app.py` before using it.

## What runs where

- **capture** (CPU container): Chromium on a virtual display plays and records each lecture.
- **transcribe** (GPU container): faster-whisper on the recorded audio, then slide frames.
- **notes** (CPU container): `claude -p` writes the notes, in lecture order. It waits out usage limits.
- **supervise** (no schedule): restarts a loop that died and ends the whole run when every lecture
  has notes, when nothing has progressed for 6 hours, or after 120 hours. Then `ctl.py status` shows
  a receipt and nothing is running. An earlier version used a 20-minute cron that kept firing after
  the course was done; that is why there is no cron now.

State lives in two Modal Volumes (`notetaker-profile` for the browser sign-in, `notetaker-data` for
work files and the database) and a Dict (`notetaker-state`) for live control.

## Setup

1. Install the CLI and sign in: `uv tool install modal`, then `modal setup`.
2. Add your lectures to a local database first (`python -m notetaker add-course ...`).
3. Sign in to your course site once, in a remote browser:
   ```bash
   modal run cloud/login.py --url https://your-course-site.example.com
   ```
   It prints a link and a one-time VNC password. Open the link, sign in, then stop the run. The
   cookies stay in the `notetaker-profile` Volume. The link is a public tunnel protected only by that
   password, so use it quickly and stop the run when done.
4. Store a Claude token for the notes step (`claude setup-token`), then:
   ```bash
   modal secret create claude-token CLAUDE_CODE_OAUTH_TOKEN=<token>
   ```
   Never paste the token anywhere public. Rotate it if it leaks.
5. Deploy and control:
   ```bash
   modal deploy cloud/modal_app.py
   uv run --with modal python cloud/ctl.py upload-lectures      # from your local database
   uv run --with modal python cloud/ctl.py start
   uv run --with modal python cloud/ctl.py start-notes
   uv run --with modal python cloud/ctl.py status
   uv run --with modal python cloud/ctl.py pull-notes           # download the finished HTML
   ```

`ctl.py stop` pauses after the current part; `resume` continues; `whisper --language en` changes the
speech settings. If the run pauses with "needs sign-in", repeat step 3 and run `resume`.

## Cost and etiquette

A 50-hour course took roughly 30 hours of wall-clock recording and a few GPU-hours. Check Modal's
current prices. Keep it to one lecture at a time and do not play the same account elsewhere while it
runs. When finished, stop the app (`modal app stop notetaker`) and delete the Volumes you no longer
need.
