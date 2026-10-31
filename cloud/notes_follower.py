"""Write notes, in lecture order, for lectures the Modal run has captured and transcribed.

Runs on the laptop, where `claude -p` is signed in. For the next lecture without notes it waits
until Modal has written status/post-<id>.json, downloads that lecture's working files (transcript
and frames), runs the project's own notes stage (which also refreshes the course HTML), marks the
lecture done and deletes its working files here and in the Volume.

    uv run --with modal python cloud/notes_follower.py

Exits 0 when every lecture has notes. Exits non-zero, so whoever started it is told, when the cloud
run pauses (sign-in, repeated capture failures), when capture goes quiet for a long time, or when
notes keep failing for a reason other than the Claude usage limit (which is simply waited out).
"""

import shutil
import sys
import time
from pathlib import Path

import modal

from notetaker import store
from notetaker.config import Config, load_dotenv
from notetaker.db import connect
from notetaker.notes.claude_writer import UsageLimited
from notetaker.pipeline import Ctx, lecture_finished
from notetaker.pipeline import notes as notes_stage

COURSE = 1
POLL_S = 300
LIMIT_WAIT_S = 3600
NOTE_FAIL_LIMIT = 3
QUIET_ALERT_S = 90 * 60
LIMIT_WORDS = ("usage limit", "rate limit", "limit reached", "quota", "resets", "exceeded")

vol = modal.Volume.from_name("notetaker-data")
state = modal.Dict.from_name("notetaker-state")
LOG = Path("data/notes-follower.log")


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def remote_exists(path: str) -> bool:
    folder, _, name = path.rpartition("/")
    try:
        return any(e.path.rsplit("/", 1)[-1] == name for e in vol.listdir(folder))
    except Exception:  # noqa: BLE001 - missing folder
        return False


def download(remote: str, local: Path) -> int:
    n = 0
    for e in vol.listdir(remote, recursive=True):
        if e.type.name != "FILE":
            continue
        rel = e.path.split(remote, 1)[1].lstrip("/")
        dest = local / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("wb") as f:
            for chunk in vol.read_file(e.path):
                f.write(chunk)
        n += 1
    return n


def cloud_alarm() -> str | None:
    """A reason to wake the owner/agent, or None while the cloud run looks healthy."""
    paused = state.get("paused")
    if paused and paused != "stopped by request":
        return f"cloud run paused: {paused}"
    if state.get("running") and not paused and not state.get("capture_finished"):
        hb = (state.get("heartbeat") or {}).get("t", 0)
        last = max(hb, state.get("launched_at", 0))
        if time.time() - last > QUIET_ALERT_S:
            return f"capture quiet for {(time.time() - last) / 60:.0f} min"
    return None


def main() -> int:
    load_dotenv()
    cfg = Config.from_env()
    conn = connect(cfg.db_path)
    course = store.course(conn, COURSE)
    fails = 0
    waiting_for = None
    log("notes follower started")
    while True:
        lec = conn.execute(
            "SELECT * FROM lectures WHERE course_id = ? AND status != 'done' ORDER BY idx LIMIT 1",
            (COURSE,),
        ).fetchone()
        if lec is None:
            log("every lecture has notes")
            return 0
        lid, idx = lec["id"], lec["idx"]
        work = cfg.work_dir / f"lecture-{lid}"
        if not remote_exists(f"status/post-{lid}.json"):
            alarm = cloud_alarm()
            if alarm:
                log(f"ALERT {alarm}")
                return 3
            if waiting_for != idx:
                log(f"waiting for Modal to finish lecture {idx}")
                waiting_for = idx
            time.sleep(POLL_S)
            continue
        if not lecture_finished(work):
            shutil.rmtree(work, ignore_errors=True)
            got = download(f"work/lecture-{lid}", work)
            log(f"lecture {idx}: downloaded {got} working files")
            if not lecture_finished(work):
                log(f"ALERT lecture {idx}: downloaded files are not a finished lecture")
                return 4
        conn.execute("UPDATE lectures SET status = 'running' WHERE id = ?", (lid,))
        t0 = time.time()
        try:
            notes_stage(Ctx(conn=conn, cfg=cfg, lecture=lec, course=course))
        except UsageLimited as e:
            log(f"lecture {idx}: Claude usage limit, waiting {e.retry_after_s / 60:.0f} min ({e})")
            time.sleep(e.retry_after_s)
            continue
        except Exception as e:  # noqa: BLE001
            text = f"{type(e).__name__}: {e}"
            if any(w in text.lower() for w in LIMIT_WORDS):
                log(f"lecture {idx}: looks like a usage limit, waiting 60 min ({text[:300]})")
                time.sleep(LIMIT_WAIT_S)
                continue
            fails += 1
            log(f"lecture {idx}: notes failed {fails}/{NOTE_FAIL_LIMIT}: {text[:500]}")
            if fails >= NOTE_FAIL_LIMIT:
                log("ALERT notes keep failing")
                return 2
            time.sleep(600)
            continue
        fails = 0
        conn.execute("UPDATE lectures SET status = 'done' WHERE id = ?", (lid,))
        shutil.rmtree(work, ignore_errors=True)
        try:
            vol.remove_file(f"work/lecture-{lid}", recursive=True)
        except Exception as e:  # noqa: BLE001 - leftover files are harmless
            log(f"lecture {idx}: could not delete remote working files: {e}")
        notes = store.stored_notes(conn, lid)
        n = len(notes.sections) if notes else 0
        log(f"lecture {idx}: notes written ({n} sections) in {(time.time() - t0) / 60:.1f} min")


if __name__ == "__main__":
    sys.exit(main())
