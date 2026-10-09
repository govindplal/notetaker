"""Write a course's notes as static HTML: one page per finished lecture plus an index."""

import re
import sqlite3
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from notetaker import store
from notetaker.render.html import inline, render_lecture

_env = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "render" / "templates"),
    autoescape=select_autoescape(["html"]),
)
_STATUS = {
    "pending": "queued",
    "running": "in progress",
    "failed": "needs attention",
    "done": "done",
}


def _blurb(lead: str, limit: int = 220) -> str:
    plain = re.sub(r"[*_`]", "", lead).strip()
    first = re.split(r"(?<=[.!?])\s", plain, maxsplit=1)[0]
    return first if len(first) <= limit else first[: limit - 1].rstrip() + "…"


def lecture_filename(idx: int) -> str:
    return f"lecture-{idx:02d}.html"


def export_course(conn: sqlite3.Connection, course_id: int, out_dir: Path) -> list[Path]:
    """Render every finished lecture and the index page into `out_dir`. Safe to run repeatedly."""
    out_dir.mkdir(parents=True, exist_ok=True)
    course = store.course(conn, course_id)
    title = course["title"] or "Course"
    items = store.notes_for_course(conn, course_id)
    finished = [(lec, n) for lec, n in items if n is not None]
    titles = {lec["idx"]: lec["title"] for lec, _n in items}
    written = []
    for pos, (lec, notes) in enumerate(finished):
        prev_href = lecture_filename(finished[pos - 1][0]["idx"]) if pos > 0 else None
        next_href = (
            lecture_filename(finished[pos + 1][0]["idx"]) if pos + 1 < len(finished) else None
        )
        page = render_lecture(
            notes,
            course_title=title,
            lecture_idx=lec["idx"],
            prev_href=prev_href,
            next_href=next_href,
            lecture_titles=titles,
        )
        path = out_dir / lecture_filename(lec["idx"])
        path.write_text(page, encoding="utf-8")
        written.append(path)

    rows = []
    for lec, notes in items:
        row = {
            "idx": lec["idx"],
            "title": lec["title"],
            "status": _STATUS.get(lec["status"], lec["status"]),
        }
        if notes is not None:
            row.update(
                href=lecture_filename(lec["idx"]),
                blurb=inline(_blurb(notes.lead)),
                extends=sum(1 for s in notes.sections if s.status == "extends"),
            )
        rows.append(row)
    waiting = [r for r in rows if "href" not in r]
    index = _env.get_template("index.html").render(
        course_title=title,
        rows=rows,
        done=len(finished),
        total=len(items),
        next_title=waiting[0]["title"] if waiting else None,
    )
    (out_dir / "index.html").write_text(index, encoding="utf-8")
    written.append(out_dir / "index.html")
    return written
