"""Command line: add a course, run the worker, stop it, check progress, retry, export, open the page.

    uv run python -m notetaker add-course --url URL --title "AI and ML Bootcamp" --lectures lectures.tsv
    uv run python -m notetaker run              # process lectures in order until stopped or finished
    uv run python -m notetaker stop             # finish the current part and exit
    uv run python -m notetaker status
    uv run python -m notetaker retry --lecture 3
    uv run python -m notetaker export --course 1
    uv run python -m notetaker ui               # the control page, at http://127.0.0.1:8765

lectures.tsv has one lecture per line, in teaching order: title <TAB> lecture page URL.
The recorder browser must be running and signed in (see docs/PLAN.md).
"""

import argparse
import dataclasses
import shutil
import sys
from pathlib import Path

from notetaker import store
from notetaker.config import Config, load_dotenv
from notetaker.db import connect
from notetaker.export import export_course
from notetaker.worker import AlreadyRunning, work


def _read_lectures(path: Path) -> list[tuple[str, str]]:
    out = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        title, sep, url = line.rpartition("\t")
        if not sep or not url.startswith("http"):
            sys.exit(f"{path}:{n}: expected 'title<TAB>https://lecture-url'")
        out.append((title.strip(), url.strip()))
    return out


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    p = argparse.ArgumentParser(
        prog="notetaker", description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add-course", help="register a course and its lectures")
    a.add_argument("--url", required=True, help="the course page URL")
    a.add_argument("--title", required=True)
    a.add_argument("--lectures", type=Path, required=True, help="TSV file: title<TAB>url per line")

    r = sub.add_parser("run", help="process lectures in order until stopped or finished")
    r.add_argument("--speed", type=float, help="playback speed while recording (default 2)")
    r.add_argument(
        "--max-minutes", type=float, help="record at most this much of each lecture (testing)"
    )

    sub.add_parser("stop", help="finish the current part of recording and exit")
    sub.add_parser("status", help="show the worker, courses, lectures and jobs")

    t = sub.add_parser("retry", help="retry a failed lecture")
    t.add_argument("--lecture", type=int, required=True, help="lecture id (see status)")

    d = sub.add_parser(
        "redo", help="redo a lecture (and every later one, whose notes depend on it)"
    )
    d.add_argument("--lecture", type=int, required=True, help="lecture id (see status)")

    e = sub.add_parser("export", help="rewrite the HTML for a course")
    e.add_argument("--course", type=int, required=True)

    u = sub.add_parser("ui", help="open the local control page")
    u.add_argument("--port", type=int, default=8765)

    args = p.parse_args(argv)
    cfg = Config.from_env()
    cfg.data_dir.mkdir(parents=True, exist_ok=True)

    if args.cmd == "ui":
        from notetaker.web import serve

        serve(cfg, port=args.port)
        return

    conn = connect(cfg.db_path)
    if args.cmd == "add-course":
        cid = store.add_course(conn, args.url, args.title)
        added = store.add_lectures(conn, cid, _read_lectures(args.lectures))
        print(f"course {cid}: {added} lecture(s) added")
    elif args.cmd == "run":
        overrides = {}
        if args.speed:
            overrides["speed"] = args.speed
        if args.max_minutes:
            overrides["max_media_seconds"] = args.max_minutes * 60
        try:
            reason = work(conn, dataclasses.replace(cfg, **overrides))
        except AlreadyRunning as e:
            sys.exit(str(e))
        print(f"worker stopped: {reason}")
    elif args.cmd == "stop":
        cfg.stop_path.write_text("stop requested\n", encoding="utf-8")
        print("stop requested; the worker finishes the current part and exits")
    elif args.cmd == "status":
        from notetaker.web import worker_state

        state = worker_state(conn, cfg)
        print(f"worker: {state['label']}")
        for c in conn.execute("SELECT * FROM courses ORDER BY id"):
            print(f"course {c['id']}: {c['title']} [{c['status']}]")
            for lec in conn.execute(
                "SELECT * FROM lectures WHERE course_id = ? ORDER BY idx", (c["id"],)
            ):
                job = conn.execute(
                    "SELECT stage, state, error FROM jobs WHERE lecture_id = ? AND state != 'done' "
                    "ORDER BY id DESC LIMIT 1",
                    (lec["id"],),
                ).fetchone()
                extra = (
                    f"  {job['stage']} {job['state']}"
                    + (f": {job['error']}" if job["error"] else "")
                    if job
                    else ""
                )
                print(
                    f"  id {lec['id']:>3}  #{lec['idx']:<3} {lec['status']:<8} {lec['title']}{extra}"
                )
    elif args.cmd == "retry":
        conn.execute(
            "UPDATE jobs SET state = 'queued', attempts = 0, next_run_at = 0, error = NULL "
            "WHERE lecture_id = ? AND state = 'failed'",
            (args.lecture,),
        )
        conn.execute(
            "UPDATE lectures SET status = 'running' WHERE id = ? AND status = 'failed'",
            (args.lecture,),
        )
        conn.execute("UPDATE courses SET status = 'active' WHERE status = 'needs_signin'")
        print("queued for retry; run `python -m notetaker run`")
    elif args.cmd == "redo":
        lec = store.lecture(conn, args.lecture)
        if lec is None:
            sys.exit(f"no lecture with id {args.lecture}")
        later = conn.execute(
            "SELECT id FROM lectures WHERE course_id = ? AND idx >= ?",
            (lec["course_id"], lec["idx"]),
        ).fetchall()
        n = store.reset_from(conn, lec["course_id"], lec["idx"])
        for row in later:  # partial working files would otherwise be carried on from
            shutil.rmtree(cfg.work_dir / f"lecture-{row['id']}", ignore_errors=True)
        print(f"reset {n} lecture(s) from #{lec['idx']} onwards; run `python -m notetaker run`")
    elif args.cmd == "export":
        for path in export_course(conn, args.course, cfg.notes_dir / f"course-{args.course}"):
            print(path)


if __name__ == "__main__":
    main()
