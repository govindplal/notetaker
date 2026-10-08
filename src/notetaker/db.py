import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS courses (
    id INTEGER PRIMARY KEY,
    url TEXT NOT NULL UNIQUE,
    title TEXT,
    status TEXT NOT NULL DEFAULT 'active',  -- active | paused | needs_signin
    last_scan_at TEXT
);

CREATE TABLE IF NOT EXISTS lectures (
    id INTEGER PRIMARY KEY,
    course_id INTEGER NOT NULL REFERENCES courses(id),
    idx INTEGER NOT NULL,
    section TEXT,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | running | done | failed
    duration_s INTEGER,
    UNIQUE (course_id, url)
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    lecture_id INTEGER NOT NULL REFERENCES lectures(id),
    stage TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'queued',  -- queued | running | done | failed
    attempts INTEGER NOT NULL DEFAULT 0,
    next_run_at REAL NOT NULL DEFAULT 0,
    error TEXT,
    UNIQUE (lecture_id, stage)
);
CREATE INDEX IF NOT EXISTS jobs_ready ON jobs (state, next_run_at);

CREATE TABLE IF NOT EXISTS transcripts (
    lecture_id INTEGER PRIMARY KEY REFERENCES lectures(id),
    segments_json TEXT NOT NULL,
    provider TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS frames (
    id INTEGER PRIMARY KEY,
    lecture_id INTEGER NOT NULL REFERENCES lectures(id),
    ts REAL NOT NULL,
    path TEXT NOT NULL,
    phash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notes (
    lecture_id INTEGER PRIMARY KEY REFERENCES lectures(id),
    markdown TEXT NOT NULL,
    model TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def connect(path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, isolation_level=None)  # autocommit; we use explicit transactions
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    return conn
