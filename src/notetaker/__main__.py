"""Command line: add a course, run the worker, check progress, retry, export.

    uv run python -m notetaker add-course --url URL --title "AI and ML Bootcamp" --lectures lectures.tsv
    uv run python -m notetaker run            # process lectures in order, then exit
    uv run python -m notetaker run --forever  # keep polling (for a server)
    uv run python -m notetaker status
    uv run python -m notetaker retry --lecture 3
    uv run python -m notetaker export --course 1

lectures.tsv has one lecture per line, in teaching order: title <TAB> lecture page URL.
The recorder browser must be running and signed in (see docs/PLAN.md).
"""

import argparse
import dataclasses
import sys
from pathlib import Path

from notetaker import jobs, store
from notetaker.config import Config, load_dotenv
from notetaker.db import connect
from notetaker.export import export_course
from notetaker.worker import work


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

    r = sub.add_parser("run", help="process queued lectures in order")
    r.add_argument(
        "--forever", action="store_true", help="keep polling instead of exiting when idle"
    )
    r.add_argument("--speed", type=float, help="playback speed while recording (default 2)")
    r.add_argument(
        "--max-minutes", type=float, help="record at most this much of each lecture (testing)"
    )

    sub.add_parser("status", help="show courses, lectures and jobs")

    t = sub.add_parser("retry", help="retry a failed lecture")
    t.add_argument("--lecture", type=int, required=True, help="lecture id (see status)")

    e = sub.add_parser("export", help="rewrite the HTML for a course")
    e.add_argument("--course", type=int, required=True)

    args = p.parse_args(argv)
    cfg = Config.from_env()
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
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
        work(conn, dataclasses.replace(cfg, **overrides), stop_when_idle=not args.forever)
    elif args.cmd == "status":
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
    elif args.cmd == "export":
        for path in export_course(conn, args.course, cfg.notes_dir / f"course-{args.course}"):
            print(path)
    _ = jobs  # re-exported for scripts


if __name__ == "__main__":
    main()
