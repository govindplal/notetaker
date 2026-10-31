"""Control the deployed Modal run (cloud/modal_app.py) from the laptop.

uv run --with modal python cloud/ctl.py status
uv run --with modal python cloud/ctl.py upload-lectures      # from data/notetaker.db, course 1
uv run --with modal python cloud/ctl.py start                # begin / continue the chain
uv run --with modal python cloud/ctl.py stop                 # save the current part and pause
uv run --with modal python cloud/ctl.py resume               # clear a pause and continue
uv run --with modal python cloud/ctl.py whisper --model large-v3-turbo --language en
uv run --with modal python cloud/ctl.py asr-test --lecture 2 --start 600 --seconds 360
"""

import argparse
import json
import sqlite3
import sys
import time

import modal

APP = "notetaker"


def _state() -> modal.Dict:
    return modal.Dict.from_name("notetaker-state", create_if_missing=True)


def _vol() -> modal.Volume:
    return modal.Volume.from_name("notetaker-data", create_if_missing=True)


def _fn(name: str):
    return modal.Function.from_name(APP, name)


def _lectures_from_db(db: str, course: int) -> list[dict]:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, idx, title, url FROM lectures WHERE course_id = ? ORDER BY idx", (course,)
    ).fetchall()
    return [dict(r) for r in rows]


def _status_files() -> set[str]:
    try:
        return {e.path.split("/")[-1] for e in _vol().listdir("status")}
    except Exception:  # noqa: BLE001 - no status folder yet
        return set()


def status() -> None:
    st = _state()
    keys = ("running", "paused", "stop", "current", "heartbeat", "whisper", "capture_finished")
    for k in keys:
        v = st.get(k)
        if k == "heartbeat" and v:
            v = {**v, "age_s": round(time.time() - v["t"])}
        print(f"{k}: {v}")
    files = _status_files()
    try:
        lectures = json.loads(b"".join(_vol().read_file("lectures.json")))
    except Exception:  # noqa: BLE001
        lectures = []
    cap = [x["idx"] for x in lectures if f"capture-{x['id']}.json" in files]
    post = [x["idx"] for x in lectures if f"post-{x['id']}.json" in files]
    print(f"captured: {len(cap)}/{len(lectures)} {cap}")
    print(f"transcribed+frames: {len(post)}/{len(lectures)} {post}")
    print("recent events:")
    for line in (st.get("events") or [])[-12:]:
        print("  " + line)


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    u = sub.add_parser("upload-lectures")
    u.add_argument("--db", default="data/notetaker.db")
    u.add_argument("--course", type=int, default=1)
    sub.add_parser("start")
    sub.add_parser("stop")
    sub.add_parser("resume")
    w = sub.add_parser("whisper")
    w.add_argument("--model", default="large-v3-turbo")
    w.add_argument("--language", default=None)
    w.add_argument("--prompt", default=None)
    t = sub.add_parser("asr-test")
    t.add_argument("--lecture", type=int, required=True, help="lecture idx")
    t.add_argument("--start", type=float, default=600)
    t.add_argument("--seconds", type=float, default=360)
    t.add_argument("--skip-record", action="store_true")
    t.add_argument("--db", default="data/notetaker.db")
    args = p.parse_args()
    st = _state()

    if args.cmd == "status":
        status()
    elif args.cmd == "upload-lectures":
        lectures = _lectures_from_db(args.db, args.course)
        with _vol().batch_upload(force=True) as b:
            import io

            b.put_file(io.BytesIO(json.dumps(lectures).encode()), "lectures.json")
        print(f"uploaded {len(lectures)} lectures: #{lectures[0]['idx']}..#{lectures[-1]['idx']}")
    elif args.cmd == "start":
        st["running"] = True
        st["stop"] = False
        st["launched_at"] = time.time()
        call = _fn("capture_next").spawn()
        print(f"capture chain started ({call.object_id})")
    elif args.cmd == "stop":
        st["stop"] = True
        print("stop requested: the current part is saved, then the run pauses")
    elif args.cmd == "resume":
        st["stop"] = False
        st["paused"] = None
        st["running"] = True
        st["launched_at"] = time.time()
        call = _fn("capture_next").spawn()
        print(f"resumed ({call.object_id})")
    elif args.cmd == "whisper":
        st["whisper"] = {"model": args.model, "language": args.language, "prompt": args.prompt}
        print(f"whisper settings: {st['whisper']}")
    elif args.cmd == "asr-test":
        lec = next(x for x in _lectures_from_db(args.db, 1) if x["idx"] == args.lecture)
        name = f"clip-{lec['idx']}-{int(args.start)}"
        if not args.skip_record:
            print(f"recording lecture {lec['idx']} {args.start:.0f}s +{args.seconds:.0f}s ...")
            print(_fn("record_clip").remote(lec["url"], args.start, args.seconds, name))
        prompt = (
            "Hinglish lecture on AI and machine learning. Hindi words are written in Roman "
            "script: matlab, toh, kya, hai, accha, samjhe, dekho."
        )
        configs = [
            {"model": "large-v3-turbo", "language": None},
            {"model": "large-v3-turbo", "language": "en"},
            {"model": "large-v3-turbo", "language": "hi"},
            {"model": "large-v3-turbo", "language": "en", "prompt": prompt},
            {"model": "large-v3", "language": None},
            {"model": "large-v3", "language": "en", "prompt": prompt},
        ]
        res = _fn("asr_compare").remote(name, args.seconds, configs)
        out = f"data/eval/{name}.json"
        with open(out, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
        for r in res:
            print(
                f"== {r['model']} lang={r.get('language')} prompt={bool(r.get('prompt'))} "
                f"({r['seconds']}s)"
            )
        print(f"saved {out}")
    else:
        sys.exit(f"unknown command {args.cmd}")


if __name__ == "__main__":
    main()
