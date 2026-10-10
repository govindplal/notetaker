"""Database helpers: courses, lectures, the concept ledger and finished notes."""

import json
import sqlite3

from notetaker.notes.model import Concept, LectureNotes


def add_course(conn: sqlite3.Connection, url: str, title: str | None = None) -> int:
    conn.execute("INSERT OR IGNORE INTO courses (url, title) VALUES (?, ?)", (url, title))
    if title:
        conn.execute("UPDATE courses SET title = ? WHERE url = ?", (title, url))
    return conn.execute("SELECT id FROM courses WHERE url = ?", (url,)).fetchone()[0]


def add_lectures(conn: sqlite3.Connection, course_id: int, lectures: list[tuple[str, str]]) -> int:
    """Add (title, url) pairs in teaching order. Existing lectures keep their position."""
    have = {
        r["url"] for r in conn.execute("SELECT url FROM lectures WHERE course_id = ?", (course_id,))
    }
    next_idx = conn.execute(
        "SELECT COALESCE(MAX(idx), 0) FROM lectures WHERE course_id = ?", (course_id,)
    ).fetchone()[0]
    added = 0
    for title, url in lectures:
        if url in have:
            continue
        next_idx += 1
        conn.execute(
            "INSERT INTO lectures (course_id, idx, title, url) VALUES (?, ?, ?, ?)",
            (course_id, next_idx, title, url),
        )
        added += 1
    return added


def lecture(conn: sqlite3.Connection, lecture_id: int) -> sqlite3.Row:
    return conn.execute("SELECT * FROM lectures WHERE id = ?", (lecture_id,)).fetchone()


def course(conn: sqlite3.Connection, course_id: int) -> sqlite3.Row:
    return conn.execute("SELECT * FROM courses WHERE id = ?", (course_id,)).fetchone()


def next_lecture_to_start(conn: sqlite3.Connection, course_id: int) -> sqlite3.Row | None:
    """The next lecture to process, or None when one is in flight or all are done.

    Lectures run strictly in order and one at a time, because each lecture's notes depend on the
    ledger of the ones before it.
    """
    busy = conn.execute(
        "SELECT 1 FROM jobs j JOIN lectures l ON l.id = j.lecture_id "
        "WHERE l.course_id = ? AND j.state IN ('queued', 'running') LIMIT 1",
        (course_id,),
    ).fetchone()
    if busy:
        return None
    first_open = conn.execute(
        "SELECT * FROM lectures WHERE course_id = ? AND status != 'done' ORDER BY idx LIMIT 1",
        (course_id,),
    ).fetchone()
    # A failed lecture blocks the ones after it until it is retried: their notes depend on it.
    return first_open if first_open is not None and first_open["status"] == "pending" else None


def ledger(conn: sqlite3.Connection, course_id: int, before_idx: int) -> list[Concept]:
    rows = conn.execute(
        "SELECT * FROM concepts WHERE course_id = ? AND lecture_idx < ? ORDER BY lecture_idx, id",
        (course_id, before_idx),
    ).fetchall()
    return [
        Concept(
            id=r["id"],
            lecture_idx=r["lecture_idx"],
            title=r["title"],
            summary=r["summary"],
            key_points=json.loads(r["key_points_json"]),
        )
        for r in rows
    ]


def save_notes(
    conn: sqlite3.Connection,
    course_id: int,
    lecture_id: int,
    lecture_idx: int,
    notes: LectureNotes,
    concepts: list[Concept],
    model: str,
) -> None:
    """Store the notes and replace this lecture's ledger rows, in one transaction."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "INSERT INTO notes (lecture_id, json, model) VALUES (?, ?, ?) "
            "ON CONFLICT(lecture_id) DO UPDATE SET json = excluded.json, model = excluded.model, "
            "created_at = CURRENT_TIMESTAMP",
            (lecture_id, notes.model_dump_json(), model),
        )
        conn.execute(
            "DELETE FROM concepts WHERE course_id = ? AND lecture_idx = ?", (course_id, lecture_idx)
        )
        for c in concepts:
            conn.execute(
                "INSERT INTO concepts (id, course_id, lecture_idx, title, summary, key_points_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (c.id, course_id, c.lecture_idx, c.title, c.summary, json.dumps(c.key_points)),
            )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def notes_for_course(
    conn: sqlite3.Connection, course_id: int
) -> list[tuple[sqlite3.Row, LectureNotes | None]]:
    """Every lecture of the course in order, with its notes when they exist."""
    out = []
    for lec in conn.execute(
        "SELECT * FROM lectures WHERE course_id = ? ORDER BY idx", (course_id,)
    ):
        row = conn.execute("SELECT json FROM notes WHERE lecture_id = ?", (lec["id"],)).fetchone()
        out.append((lec, LectureNotes.model_validate_json(row["json"]) if row else None))
    return out


def reset_from(conn: sqlite3.Connection, course_id: int, from_idx: int) -> int:
    """Forget lecture `from_idx` and every later one so they are processed again, in order."""
    ids = [
        r["id"]
        for r in conn.execute(
            "SELECT id FROM lectures WHERE course_id = ? AND idx >= ?", (course_id, from_idx)
        )
    ]
    conn.execute("BEGIN IMMEDIATE")
    try:
        for lid in ids:
            conn.execute("DELETE FROM jobs WHERE lecture_id = ?", (lid,))
            conn.execute("DELETE FROM notes WHERE lecture_id = ?", (lid,))
            conn.execute("UPDATE lectures SET status = 'pending' WHERE id = ?", (lid,))
        conn.execute(
            "DELETE FROM concepts WHERE course_id = ? AND lecture_idx >= ?", (course_id, from_idx)
        )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return len(ids)
