# Notetaker: project plan and stack

## Context
The owner takes online courses on custom sites that have no captions or transcripts. The audio is English plus Hindi, often mixed in one sentence. Notetaker is a hosted, single-user service. The owner adds a course URL and signs in once from a web page. The server then watches the lectures unattended and writes detailed notes. The owner reads the notes from any browser (e.g. on a break at work). Nothing runs on the laptop.

Constraints from the owner:
- **Free if possible.**
- 10-30 lecture hours a week.
- Python.
- Free Gemini tier is acceptable (Google may use the content).
- Sign-in through a remote browser shown in the web page.
- Notes must be detailed and structured. Slide and board content goes into the notes as text, tables, formulas and redrawn diagrams, not as screenshots. A cropped image is used only when a diagram can't be redrawn.

## Site probe: 100xdevs.com (signed-in, read-only)
- Course: AI and ML Bootcamp (course 23). The "128 videos" figure covers the owner's whole account; the AI/ML course itself has **33 videos**, about 55 hours in total, so the backlog clears in 2-5 weeks at 10-30 h/week. The owner also has separate courses (web development, DSA, ...). At setup the app asks which course to follow; each course is added separately.
- Video player loads plain HLS VOD from `transcoded-video.b-cdn.net/<id>/480p/playlist.m3u8`, with 6 s `.ts` segments. No `EXT-X-KEY` and no `EXT-X-MAP`, so **no DRM observed**. Lecture 1 is 656 segments, 3935 s, which matches the page's 1:05:35.
- Consequence: capture can be a direct HLS download with ffmpeg/yt-dlp, not real-time recording. That means the x86 VPS isn't needed for DRM, and the Oracle ARM VM is enough.
- Open: whether the course terms allow downloading or automated capture. Not checked yet. Blocks the capture milestone.
- Lecture slides are Google Drive links and Colab notebooks on each lecture. Gemini can read the Drive slides as an extra source.

## Decisions (stack)
| Area | Choice | Why |
|-|-|-|
| Language | Python 3.12, `uv`, `ruff`, `pytest` | Best media tooling (yt-dlp, Playwright, ffmpeg, imagehash) |
| Web UI | FastAPI + Jinja2 + HTMX; Markdown rendered server-side, KaTeX + Mermaid in the browser | Tiny and server-rendered; nothing to build or bundle |
| DB / queue | SQLite (WAL) with a `jobs` table polled by the worker | Single user; no Redis or Postgres to host |
| Browser | One long-lived **Google Chrome** in its own container (Xvfb + PulseAudio + x11vnc + noVNC). The worker drives it over CDP (`playwright.connect_over_cdp`) | The browser you sign into is the same one that watches the lectures, so the session needs no export step |
| Media | `yt-dlp` / `ffmpeg` for stream download. Fallback: ffmpeg `x11grab` + Pulse recording of real-time playback (for DRM) | Download is fast. Recording works for anything that plays |
| Transcription | **Gemini Flash audio** (decided by the owner), behind the `Transcriber` interface so Groq Whisper can be added later if free limits bite | Handles Hinglish well, and one provider and one key keep it simple |
| Notes + slides | **Gemini Flash (free tier)**, multimodal: transcript segment + de-duplicated keyframes → Markdown notes | Free, reads slides and boards, can redraw diagrams as Mermaid |
| Hosting | Docker Compose on one VM. **Oracle Always Free ARM (2 OCPU / 12 GB)** if the site has no DRM. **~€4/mo x86 VPS (Hetzner CX22)** if DRM, because Chrome with Widevine has no Linux ARM build | Only the DRM case costs money |
| Public access | Caddy (auto-HTTPS) + free DuckDNS subdomain; app login protects everything, including noVNC (Caddy `forward_auth`) | Free, no domain purchase |

Provider calls go through small interfaces (`Transcriber`, `NoteWriter`), so Claude or a paid tier can be swapped in later without touching the pipeline.

## Architecture
```
 phone/work browser ──HTTPS──> Caddy ──> web (FastAPI) ──┐
                                   └──> noVNC (sign-in)   │  SQLite (courses, lectures, jobs, notes)
                                                          │
 worker (Python) ── polls jobs ───────────────────────────┘
   └─ CDP ──> chrome container (Xvfb, Pulse, persistent profile) ──> course site
   └─ Groq / Gemini APIs
```
Three containers: `web`, `worker`, `chrome` (+ `caddy`). Data volume: `/data/notetaker.db`, `/data/chrome-profile`, `/data/media` (temporary), `/data/assets` (diagram crops).

## Pipeline (one resumable job per stage, per lecture)
1. **discover** (per course): open the course URL in the shared Chrome. Collect links and an accessibility snapshot. Gemini returns an ordered JSON list of lectures `{title, url, section}`. You confirm or edit the list in the UI before processing starts. The course is re-scanned daily for new lectures.
2. **capture**: open the lecture page and listen to network traffic for `.m3u8` / `.mpd` / `.mp4` and known players (Vimeo, YouTube, Wistia, Bunny). With cookies and referer from the browser, download using yt-dlp/ffmpeg. If the stream is DRM/encrypted or not found, fall back to **record**: play in Chrome and capture screen + audio with ffmpeg in real time, watching for the end of the video.
3. **transcribe**: extract 16 kHz mono audio and split it into roughly 10-minute chunks at silences. Send them to the chosen Transcriber and get back timestamped segments. Prompt rules: keep Hindi/English as spoken, Hindi in Roman script, do not translate. Store the full transcript.
4. **frames**: ffmpeg scene-change detection, then perceptual-hash de-duplication, gives the distinct slide/board states with timestamps (typically 20-80 per hour).
5. **notes**: for each ~10-15 min segment, send the transcript plus that segment's frames to Gemini. Output is Markdown: topic headings, explanations, examples and `[mm:ss]` timestamps. Formulas go in as LaTeX, tables as Markdown and diagrams as Mermaid. When a diagram can't be redrawn, Gemini returns a bounding box and we crop that region from the frame. A final pass merges the segments into one lecture document with consistent headings.
6. **cleanup**: delete the media, keep the transcript, notes and crops.

Job rules:
- One lecture at a time, with a polite delay between page loads.
- Retries use exponential backoff and respect free-tier rate limits (Groq 20 RPM / 28.8K audio-sec/day; Gemini RPM/RPD read from the project at startup).
- A login redirect or 401 marks the course **needs sign-in** and pauses it.
- Every stage is idempotent: it skips work whose output is already stored.

## Web UI pages
- **Login**: single password (argon2 hash in `.env`), signed session cookie, rate-limited.
- **Courses**: list with progress (x/y lectures done) and status (running, paused, needs sign-in).
- **Add course**: enter the URL, a noVNC panel opens on the shared Chrome, sign in, click "Done". Discovery runs and you confirm the lecture list.
- **Course**: lectures in order, each with a stage badge.
- **Lecture notes**: rendered notes (KaTeX, Mermaid, crops) with a collapsible transcript beside them. Mobile-friendly.

## Data model (SQLite)
- `courses(id, url, title, status, last_scan_at)`
- `lectures(id, course_id, idx, section, title, url, status, duration_s)`
- `jobs(id, lecture_id|course_id, stage, state, attempts, next_run_at, error)`
- `transcripts(lecture_id, segments_json, provider)`
- `frames(lecture_id, ts, path, phash)`
- `notes(lecture_id, markdown, model, created_at)`

## Repo layout
```
src/notetaker/  config.py db.py jobs.py worker.py browser.py discover.py
                capture/{stream.py,record.py}  transcribe/{base.py,groq.py,gemini.py}
                frames.py  notes/{writer.py,prompts/}  web/{app.py,auth.py,templates/,static/}
docker/         compose.yml Dockerfile.app Dockerfile.chrome Caddyfile
tests/          unit + fixtures (short sample clip, recorded API responses)
```
New dependencies (approval needed): playwright, yt-dlp, fastapi, uvicorn, jinja2, python-multipart, itsdangerous, argon2-cffi, google-genai, groq, pillow, imagehash, markdown-it-py; dev: ruff, pytest. System: ffmpeg, Chrome, Xvfb, PulseAudio, noVNC (inside Docker images only).

## Milestones (each ends with tests/lint green, commit, STATUS.md update)
0. **Site probe (no code).** With your course URL, I sign in through Claude-in-Chrome on your machine once and inspect network requests on one lecture to find the player and check for DRM. This decides Oracle ARM (free) or x86 VPS. *Blocked on: your course URL(s).*
1. **Skeleton:** repo, `pyproject`, ruff/pytest, config, SQLite schema + migrations, job runner state machine with tests.
2. **Offline pipeline:** given a local video file, produce transcript, frames, then notes. Transcriber bake-off (Groq vs Gemini) on a real Hinglish lecture; you judge the output and the default gets picked. Notes prompt tuned until slides, formulas and diagrams come out right.
3. **Capture:** chrome container, CDP connection, stream download, plus real-time record fallback if milestone 0 found DRM.
4. **Discovery + end-to-end worker** on one real course, locally in Docker.
5. **Web UI:** login, courses, add course with noVNC sign-in, lecture list confirmation, notes viewer.
6. **Deploy:** VM, Docker Compose, Caddy + DuckDNS, nightly SQLite backup to Oracle Object Storage (free 20 GB), private GitHub remote (asked before first push).

## Verification
- Unit: job state transitions and retries, login-redirect detection, frame de-duplication, chunking at silences, parsing of Gemini output (Mermaid/LaTeX/bbox) using recorded fixtures.
- Integration: a 3-5 min Hinglish sample clip through the full offline pipeline with live APIs. Check the transcript keeps mixed speech as spoken and that slide text and a diagram end up in the notes.
- End-to-end on the server: add the real course from a phone, sign in through noVNC, confirm the lecture list, and get notes for the first lecture. Restart the worker mid-lecture and confirm it resumes without redoing finished stages. Expire the session and confirm "needs sign-in" shows.

## Risks
- **DRM** makes capture real-time (1x speed; about 30 h/week is still fine) and needs x86, which costs about €4/mo.
- Oracle free capacity can be unavailable, and idle VMs can be reclaimed; the hourly worker activity keeps the VM from looking idle. The fallback is the same compose file on a cheap VPS.
- Free-tier limits change. Limits are configurable and the worker backs off rather than failing.
- The course site's terms may forbid downloading. Use is personal; downloads are deleted after notes are written.


## Update: notes design (2026-10-09, from the owner's handwritten notes and the HTML reference)
- **Style:** notes follow the owner's handwriting (`src/notetaker/notes/style.md`): short plain sentences, `Term - explanation`, arrows for naming, Problem/Solution, "Reasons to fail", concrete examples, bracketed asides, minimal maths.
- **Only useful knowledge:** greetings, logistics, off-topic Q&A and in-lecture repeats are dropped. Each dropped chunk gets a one-line reason in a "Left out" list so nothing disappears silently.
- **No repeats across lectures:** a per-course **ledger** (`L<lecture>-c<n> | title | one-line summary`) is sent with each new lecture. Topics already taught are not rewritten; if a lecture adds something new to an old topic, only the new part is written, tagged "Adds to earlier" with a "Builds on: Lecture N" pointer. Lectures must be processed in order.
- **Store nothing but notes (corrected by the owner):** transcripts, frames, audio and video are never stored long term and never shown in the notes. They are working files in a temporary folder while a lecture is processed (so a crashed job can resume) and are deleted when its notes are written. Anything useful in a frame is written into the notes as text, a table or a diagram. Only the structured notes, the ledger and the generated HTML remain.
- **Output:** one structured JSON per lecture (`LectureNotes`) rendered to a self-contained HTML page (Jinja2) that copies the "feature explainer" layout from github.com/ThariqS/html-effectiveness (`14-research-feature-explainer.html`): sticky left "On this page" rail, mono eyebrow, serif title, TL;DR box with a clay edge, serif h2 sections, collapsible Example blocks, tabbed code, oat callouts, tables, KaTeX formulas, Mermaid diagrams, and a FAQ-style glossary at the bottom with hover definitions in the text. The audit list of skipped chunks (`left_out`) stays in the JSON and is not shown. One fixed light palette taken from the reference (ivory, slate, clay, olive, sky, warm grays); no dark mode, no timestamps, no images.
- **Models:** Gemini calls fall back through 3.8, 3.7, 3.6, 3.5 Flash on 503/429, because 3.8 is often overloaded.

- **Figures (2026-10-09):** the model does not write SVG. It describes a figure as data (`flow`: nodes and edges; `layers`: neural-network style columns) and `render/figures.py` lays it out and draws inline SVG in the reference palette (spread edge ports, label collision avoidance, loops as arcs, wide flows flip to top-to-bottom). Writing SVG inside the JSON reply proved unreliable (empty or runaway output). Generation is validated: length caps on captions and labels, a figure block must carry a figure, repetitive text is detected, and bad output is retried up to 3 times.
