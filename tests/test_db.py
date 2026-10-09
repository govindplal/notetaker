from notetaker.config import Config
from notetaker.db import connect


def test_schema_creates_all_tables_and_enables_wal(tmp_path):
    conn = connect(tmp_path / "t.db")
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"courses", "lectures", "jobs", "concepts", "notes"} <= tables
    assert not {"transcripts", "frames"} & tables  # never stored
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_connect_twice_is_safe(tmp_path):
    connect(tmp_path / "t.db").close()
    connect(tmp_path / "t.db").close()


def test_config_reads_env(monkeypatch, tmp_path):
    monkeypatch.setenv("NOTETAKER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GEMINI_API_KEY", "")
    cfg = Config.from_env()
    assert cfg.db_path == tmp_path / "notetaker.db"
    assert cfg.gemini_api_key is None
