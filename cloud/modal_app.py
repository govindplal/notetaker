"""Notetaker on Modal: process a whole course, one lecture at a time, with your laptop off.

- capture_next (CPU): Chromium on a virtual display, signed in through the profile in Volume
  `notetaker-profile` (made once with cloud/login.py). Records the next lecture that is not
  captured yet with the project's own `pipeline.capture` (2x, resumable parts), hands it to `post`,
  then calls itself for the next lecture. At most one capture container ever runs.
- post (CPU) -> transcribe_gpu (GPU): `pipeline.transcribe` with faster-whisper, then
  `pipeline.frames`. Runs while the next lecture is being recorded.
- notes_loop (CPU): `pipeline.notes` with `claude -p`, in lecture order, as soon as a lecture's
  transcript and frames are ready. Needs the Modal secret `claude-token`. Waits out usage limits.
- watchdog (cron): restarts a loop that died and re-runs a lost post.

Working files live in Volume `notetaker-data` under work/lecture-<id>/ exactly as on a laptop;
status/capture-<id>.json and status/post-<id>.json mark finished stages. Live control and progress
are in Dict `notetaker-state` (see cloud/ctl.py). A lecture's working files are deleted once its
notes are written.

Deploy:  modal deploy cloud/modal_app.py      (see cloud/README.md for the full setup)
"""

import json
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import modal

APP_NAME = "notetaker"
SRC = Path(__file__).resolve().parent.parent / "src" / "notetaker"
PROFILE = "/profile"
DATA = "/data"
CDP = "http://127.0.0.1:9222"
DEFAULT_WHISPER = {"model": "large-v3-turbo", "language": None, "prompt": None}
CAPTURE_FAIL_LIMIT = 6  # consecutive failures of one lecture before the course pauses
BUSY_WAIT_S = 600
BUSY_LIMIT = 36  # six hours of "another device is playing" before pausing
STALE_S = 15 * 60
GPUS = ["L4", "A10G", "L40S", "T4"]  # first one with capacity wins

app = modal.App(APP_NAME)
profile_vol = modal.Volume.from_name("notetaker-profile", create_if_missing=True)
data_vol = modal.Volume.from_name("notetaker-data", create_if_missing=True)
state = modal.Dict.from_name("notetaker-state", create_if_missing=True)

PY_DEPS = [
    "numpy",
    "pillow",
    "imagehash",
    "pydantic>=2",
    "jinja2",
    "markdown-it-py",
    "playwright==1.*",
]
IGNORE = ["**/__pycache__", "**/*.pyc"]

chrome_image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("chromium", "xvfb", "fluxbox", "ffmpeg", "fonts-liberation", "fonts-noto-core")
    .pip_install(*PY_DEPS)
    .add_local_dir(SRC, "/root/notetaker", ignore=IGNORE)
)

gpu_image = (
    modal.Image.from_registry("nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04", add_python="3.12")
    .env({"DEBIAN_FRONTEND": "noninteractive"})
    .apt_install("ffmpeg")
    .pip_install("faster-whisper>=1.2.1", *PY_DEPS)
    .run_commands(
        'python -c "from faster_whisper import download_model as d; '
        "d('large-v3-turbo'); d('large-v3')\""
    )
    .add_local_dir(SRC, "/root/notetaker", ignore=IGNORE)
)

light_image = modal.Image.debian_slim(python_version="3.12")


# ---------------------------------------------------------------- helpers (run in containers)


def _path() -> None:
    if "/root" not in sys.path:
        sys.path.insert(0, "/root")


def _log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())}Z {msg}"
    print(line, flush=True)
    try:
        events = state.get("events", [])
        state["events"] = (events + [line])[-200:]
    except Exception as e:  # noqa: BLE001 - logging must never break the work
        print(f"(event log failed: {e})", flush=True)


def _lectures() -> list[dict]:
    return json.loads(Path(DATA, "lectures.json").read_text(encoding="utf-8"))


def _status(kind: str, lid: int) -> Path:
    return Path(DATA, "status", f"{kind}-{lid}.json")


def _write_status(kind: str, lid: int, obj: dict) -> None:
    p = _status(kind, lid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj), encoding="utf-8")


def _cfg(**kw):
    _path()
    from notetaker.config import Config

    return Config(data_dir=Path(DATA), cdp_url=CDP, speed=2.0, **kw)


def _whisper() -> dict:
    return {**DEFAULT_WHISPER, **(state.get("whisper") or {})}


def _start_chrome() -> list[subprocess.Popen]:
    prof = Path(PROFILE) / "chrome-profile"
    # A browser killed mid-run leaves lock files that make Chromium refuse the profile.
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        (prof / name).unlink(missing_ok=True)
    env = {"DISPLAY": ":99", "PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/root"}
    procs = [subprocess.Popen(["Xvfb", ":99", "-screen", "0", "1280x800x24"])]
    time.sleep(2)
    procs.append(subprocess.Popen(["fluxbox"], env=env, stderr=subprocess.DEVNULL))
    procs.append(
        subprocess.Popen(
            [
                "chromium",
                f"--user-data-dir={prof}",
                "--no-sandbox",
                "--test-type",
                "--disable-dev-shm-usage",
                "--remote-debugging-port=9222",
                "--autoplay-policy=no-user-gesture-required",
                "--disable-background-media-suspend",
                "--disable-renderer-backgrounding",
                "--disable-backgrounding-occluded-windows",
                "about:blank",
            ],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    )
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"{CDP}/json/version", timeout=2).read()
            return procs
        except OSError:
            time.sleep(1)
    raise RuntimeError("Chromium did not open its debugging port")


def _stop_procs(procs: list[subprocess.Popen]) -> None:
    for p in reversed(procs):
        p.terminate()
    for p in procs:
        try:
            p.wait(timeout=15)
        except subprocess.TimeoutExpired:
            p.kill()


class _Flag:
    """state['stop'], read at most every 15 s (the recorder polls every 2 s)."""

    def __init__(self):
        self._t, self._v = 0.0, False

    def __call__(self) -> bool:
        if time.time() - self._t > 15:
            try:
                self._v = bool(state.get("stop"))
            except Exception:  # noqa: BLE001 - a Dict hiccup must not stop a recording
                pass
            self._t = time.time()
        return self._v


def _position(work: Path) -> dict | None:
    for p in sorted(work.glob("part-*.progress"), reverse=True):
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
    return None


# ---------------------------------------------------------------- the course run


@app.function(
    image=chrome_image,
    volumes={PROFILE: profile_vol, DATA: data_vol},
    cpu=2.0,
    memory=4096,
    timeout=8 * 3600,
    max_containers=1,
)
def capture_next() -> str:
    """Record the next lecture that is not captured yet, then hand off and chain."""
    _path()
    data_vol.reload()
    if state.get("paused"):
        return f"paused: {state.get('paused')}"
    todo = [lec for lec in _lectures() if not _status("capture", lec["id"]).exists()]
    if not todo:
        state["capture_finished"] = time.time()
        _log("every lecture is captured")
        return "all captured"
    lec = todo[0]
    lid, idx = lec["id"], lec["idx"]
    state["current"] = {"id": lid, "idx": idx, "title": lec["title"], "since": time.time()}
    _log(f"capture lecture {idx} (id {lid}) starts")

    from notetaker.capture.record import DeviceBusy, NeedsSignIn
    from notetaker.pipeline import Ctx, _read_state, capture

    cfg = _cfg(max_media_seconds=state.get("max_media_s"))
    ctx = Ctx(conn=None, cfg=cfg, lecture=lec, course=None, should_stop=_Flag())
    done = threading.Event()

    def heartbeat() -> None:
        n = 0
        while not done.wait(30):
            n += 1
            try:
                state["heartbeat"] = {"t": time.time(), "idx": idx, "pos": _position(ctx.work)}
                if n % 4 == 0:
                    data_vol.commit()  # keep partial recordings safe if the container dies
            except Exception as e:  # noqa: BLE001
                print(f"heartbeat: {e}", flush=True)

    procs = _start_chrome()
    hb = threading.Thread(target=heartbeat, daemon=True)
    hb.start()
    outcome = "captured"
    try:
        capture(ctx)
    except NeedsSignIn as e:
        state["paused"] = f"needs sign-in (lecture {idx}): {e}"
        outcome = "needs_signin"
    except DeviceBusy as e:
        busy = int(state.get("busy_count", 0)) + 1
        state["busy_count"] = busy
        outcome = f"busy ({busy}): {e}"
        if busy >= BUSY_LIMIT:
            state["paused"] = f"another device kept playing for hours (lecture {idx})"
        else:
            time.sleep(BUSY_WAIT_S)
    except Exception as e:  # noqa: BLE001 - kept parts are resumed by the next call
        fails = int(state.get(f"fails:{lid}", 0)) + 1
        state[f"fails:{lid}"] = fails
        outcome = f"error {fails}/{CAPTURE_FAIL_LIMIT}: {type(e).__name__}: {e}"
        if fails >= CAPTURE_FAIL_LIMIT:
            state["paused"] = f"capture of lecture {idx} failed {fails} times: {e}"
        else:
            time.sleep(30 * fails)
    finally:
        done.set()
        _stop_procs(procs)

    st = _read_state(ctx)
    captured = bool(st["complete"])
    if captured:
        _write_status(
            "capture",
            lid,
            {"parts": len(st["parts"]), "duration": st["duration"], "at": time.time()},
        )
        state["busy_count"] = 0
    data_vol.commit()
    profile_vol.commit()  # refreshed cookies
    covered = st["parts"][-1]["end"] if st["parts"] else 0
    _log(f"capture lecture {idx}: {outcome}; {covered:.0f}/{st['duration'] or 0:.0f}s recorded")
    if captured:
        state[f"post_started:{lid}"] = time.time()
        post.spawn(lid)
    if state.get("stop"):
        state["paused"] = "stopped by request"
        return "stopped"
    if not state.get("paused"):
        state["launched_at"] = time.time()
        capture_next.spawn()
    return outcome


@app.function(image=gpu_image, gpu=GPUS, volumes={DATA: data_vol}, timeout=3 * 3600)
def transcribe_gpu(lid: int) -> dict:
    """The project's transcribe stage for one captured lecture, on a GPU."""
    _path()
    data_vol.reload()
    from notetaker.asr import WhisperTranscriber
    from notetaker.pipeline import Ctx, _read_json, transcribe

    lec = next(x for x in _lectures() if x["id"] == lid)
    ws = _whisper()
    cfg = _cfg(
        whisper_model=ws["model"], whisper_language=ws["language"], whisper_prompt=ws["prompt"]
    )
    ctx = Ctx(conn=None, cfg=cfg, lecture=lec, course=None)
    t0 = time.time()
    ctx._transcriber = WhisperTranscriber(
        ws["model"], device="cuda", language=ws["language"], initial_prompt=ws["prompt"]
    )
    transcribe(ctx)
    data_vol.commit()
    meta = _read_json(ctx.work / "transcript.json", {})
    return {
        "segments": len(meta.get("segments", [])),
        "complete": meta.get("complete"),
        "audio_s": meta.get("audio_s"),
        "seconds": round(time.time() - t0, 1),
        "whisper": ws,
    }


@app.function(image=chrome_image, volumes={DATA: data_vol}, cpu=2.0, memory=4096, timeout=4 * 3600)
def post(lid: int) -> dict:
    """Transcribe (GPU) then keep the distinct frames, for one captured lecture."""
    _path()
    if _status("post", lid).exists():
        return {"skipped": "already done"}
    from notetaker.pipeline import AUDIO, Ctx, _read_json, frames

    lec = next(x for x in _lectures() if x["id"] == lid)
    state[f"post_started:{lid}"] = time.time()
    tr = transcribe_gpu.remote(lid)
    data_vol.reload()
    ctx = Ctx(conn=None, cfg=_cfg(), lecture=lec, course=None)
    if not tr.get("complete"):
        _log(f"post lecture {lec['idx']}: transcript incomplete {tr}")
        raise RuntimeError(f"transcript incomplete for lecture {lec['idx']}: {tr}")
    frames(ctx)  # also deletes the recordings, whose audio is in the transcript by now
    (ctx.work / AUDIO).unlink(missing_ok=True)
    kept = len(_read_json(ctx.work / "frames.json", {"frames": []})["frames"])
    info = {**tr, "frames": kept, "at": time.time()}
    _write_status("post", lid, info)
    data_vol.commit()
    _log(
        f"post lecture {lec['idx']}: {tr['segments']} segments, {kept} frames, "
        f"{tr['audio_s']:.0f}s audio transcribed in {tr['seconds']:.0f}s"
    )
    return info


@app.function(
    image=light_image,
    volumes={DATA: data_vol},
    schedule=modal.Period(minutes=20),
    timeout=300,
)
def watchdog() -> str:
    """Restart the capture chain if it died; re-run a post that never finished."""
    if not state.get("running"):
        return "not running"
    data_vol.reload()
    lectures = _lectures()
    now = time.time()
    actions = []
    if not state.get("paused"):
        hb = (state.get("heartbeat") or {}).get("t", 0)
        last = max(hb, state.get("launched_at", 0))
        left = [x for x in lectures if not _status("capture", x["id"]).exists()]
        if left and now - last > STALE_S:
            state["launched_at"] = now
            capture_next.spawn()
            actions.append(f"restarted capture (quiet for {(now - last) / 60:.0f} min)")
    if state.get("notes_enabled") and not state.get("notes_finished"):
        if now - state.get("notes_hb", 0) > 20 * 60 and now - state.get("notes_launched", 0) > 600:
            state["notes_launched"] = now
            notes_loop.spawn()
            actions.append("restarted notes loop")
    for x in lectures:
        lid = x["id"]
        if _status("capture", lid).exists() and not _status("post", lid).exists():
            started = state.get(f"post_started:{lid}", 0)
            if now - started > 3 * 3600:
                state[f"post_started:{lid}"] = now
                post.spawn(lid)
                actions.append(f"re-ran post for lecture {x['idx']}")
    for a in actions:
        _log(f"watchdog: {a}")
    return "; ".join(actions) or "ok"


# ---------------------------------------------------------------- notes in the cloud

notes_image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("nodejs", "npm", "ffmpeg")
    .run_commands("npm install -g @anthropic-ai/claude-code")
    .pip_install(*PY_DEPS)
    .add_local_dir(SRC, "/root/notetaker", ignore=IGNORE)
)
claude_secret = modal.Secret.from_name("claude-token")  # CLAUDE_CODE_OAUTH_TOKEN
LIMIT_WORDS = ("usage limit", "rate limit", "limit reached", "quota", "resets", "exceeded")
DB_LOCAL = "/tmp/notetaker.db"


def _db_back_to_volume(conn) -> None:
    import sqlite3

    dest = sqlite3.connect(f"{DATA}/notetaker.db.new")
    conn.backup(dest)
    dest.close()
    Path(f"{DATA}/notetaker.db.new").replace(f"{DATA}/notetaker.db")
    data_vol.commit()


@app.function(
    image=notes_image,
    volumes={DATA: data_vol},
    secrets=[claude_secret],
    cpu=1.0,
    memory=2048,
    timeout=23 * 3600,
    max_containers=1,
)
def notes_loop() -> str:
    """Write notes, in lecture order, for every lecture whose transcript and frames are ready."""
    import shutil
    import sqlite3

    _path()
    data_vol.reload()
    from notetaker import store
    from notetaker.db import connect
    from notetaker.notes.claude_writer import UsageLimited
    from notetaker.pipeline import Ctx
    from notetaker.pipeline import notes as notes_stage

    shutil.copy(f"{DATA}/notetaker.db", DB_LOCAL)
    conn = connect(DB_LOCAL)
    conn.row_factory = sqlite3.Row
    cfg = _cfg()
    course = store.course(conn, 1)
    fails, started = 0, time.time()
    _log("notes loop started")
    while True:
        state["notes_hb"] = time.time()
        if state.get("notes_stop"):
            return "stopped"
        if time.time() - started > 22 * 3600:
            notes_loop.spawn()  # a fresh container takes over before the timeout
            return "handed over"
        lec = conn.execute(
            "SELECT * FROM lectures WHERE course_id = 1 AND status != 'done' ORDER BY idx LIMIT 1"
        ).fetchone()
        if lec is None:
            state["notes_finished"] = time.time()
            _log("notes: every lecture has notes")
            return "finished"
        lid, idx = lec["id"], lec["idx"]
        data_vol.reload()
        if not _status("post", lid).exists():
            time.sleep(120)
            continue
        conn.execute("UPDATE lectures SET status = 'running' WHERE id = ?", (lid,))
        t0 = time.time()
        try:
            notes_stage(Ctx(conn=conn, cfg=cfg, lecture=lec, course=course))
        except UsageLimited as e:
            _log(f"notes lecture {idx}: Claude usage limit, waiting {e.retry_after_s / 60:.0f} min")
            _sleep_beating(e.retry_after_s)
            continue
        except Exception as e:  # noqa: BLE001
            text = f"{type(e).__name__}: {e}"
            if any(w in text.lower() for w in LIMIT_WORDS):
                _log(f"notes lecture {idx}: looks like a usage limit, waiting 60 min: {text[:200]}")
                _sleep_beating(3600)
                continue
            fails += 1
            _log(f"notes lecture {idx}: failed {fails}: {text[:400]}")
            state["notes_alarm"] = f"lecture {idx}: {text[:300]}"
            _sleep_beating(600 if fails < 3 else 3600)
            continue
        fails = 0
        state["notes_alarm"] = None
        conn.execute("UPDATE lectures SET status = 'done' WHERE id = ?", (lid,))
        shutil.rmtree(Path(DATA, "work", f"lecture-{lid}"), ignore_errors=True)
        _db_back_to_volume(conn)
        n = len(store.stored_notes(conn, lid).sections)
        _log(f"notes lecture {idx}: {n} sections in {(time.time() - t0) / 60:.1f} min")


def _sleep_beating(seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        state["notes_hb"] = time.time()
        time.sleep(min(300, max(end - time.time(), 1)))


@app.function(image=notes_image, secrets=[claude_secret], timeout=300)
def claude_check() -> str:
    """Does `claude -p` work in the cloud with the stored token?"""
    r = subprocess.run(
        ["claude", "-p", "Reply with exactly: ok", "--output-format", "json", "--tools", ""],
        capture_output=True,
        text=True,
        timeout=240,
    )
    return f"exit {r.returncode}\n{r.stdout[:600]}\n{r.stderr[:600]}"


# ---------------------------------------------------------------- one-off tests


@app.function(
    image=chrome_image,
    volumes={PROFILE: profile_vol, DATA: data_vol},
    cpu=2.0,
    memory=4096,
    timeout=1800,
    max_containers=1,
)
def record_clip(url: str, start_s: float, seconds: float, name: str) -> dict:
    """Record `seconds` of lecture from `start_s` into test/<name>.webm (for ASR tests)."""
    _path()
    from notetaker.capture.record import record_lecture

    out = Path(DATA, "test", f"{name}.webm")
    procs = _start_chrome()
    try:
        rec = record_lecture(CDP, url, out, speed=2.0, max_media_seconds=seconds, start_at=start_s)
    finally:
        _stop_procs(procs)
    data_vol.commit()
    profile_vol.commit()
    return {"file": str(out), "start": rec.start_t, "end": rec.end_t, "bytes": out.stat().st_size}


@app.function(image=gpu_image, gpu=GPUS, volumes={DATA: data_vol}, timeout=1800)
def asr_compare(name: str, natural_s: float, configs: list[dict]) -> list[dict]:
    """Transcribe test/<name>.webm with several Whisper settings, for reading side by side."""
    _path()
    data_vol.reload()
    from notetaker.asr import WhisperTranscriber, decode_recording

    samples = decode_recording(Path(DATA, "test", f"{name}.webm"), 2.0, natural_s)
    out = []
    models: dict[str, WhisperTranscriber] = {}
    for c in configs:
        t0 = time.time()
        m = models.get(c["model"])
        if m is None:
            m = models[c["model"]] = WhisperTranscriber(c["model"], device="cuda")
        m.language, m.initial_prompt = c.get("language"), c.get("prompt")
        segs = list(m.transcribe(samples))
        out.append(
            {
                **c,
                "seconds": round(time.time() - t0, 1),
                "text": "\n".join(f"[{s['start']:.0f}] {s['text']}" for s in segs),
            }
        )
    return out
