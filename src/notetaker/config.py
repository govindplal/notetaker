import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    data_dir: Path
    gemini_api_key: str | None
    cdp_url: str = "http://127.0.0.1:9222"  # the recorder Chrome, signed in once
    speed: float = 2.0  # playback speed while recording
    max_media_seconds: float | None = None  # cap per lecture; for short test recordings only

    @property
    def db_path(self) -> Path:
        return self.data_dir / "notetaker.db"

    @property
    def work_dir(self) -> Path:
        """Working files (recording, audio, transcript, frames). Deleted once a lecture's notes exist."""
        return self.data_dir / "work"

    @property
    def notes_dir(self) -> Path:
        return self.data_dir / "notes"

    @classmethod
    def from_env(cls) -> "Config":
        cap = os.environ.get("NOTETAKER_MAX_MEDIA_S")
        return cls(
            data_dir=Path(os.environ.get("NOTETAKER_DATA_DIR", "data")),
            gemini_api_key=os.environ.get("GEMINI_API_KEY") or None,
            cdp_url=os.environ.get("NOTETAKER_CDP_URL", "http://127.0.0.1:9222"),
            speed=float(os.environ.get("NOTETAKER_SPEED", "2.0")),
            max_media_seconds=float(cap) if cap else None,
        )


def load_dotenv(path: Path = Path(".env")) -> None:
    """Load KEY=VALUE lines into the environment without overriding values already set."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
