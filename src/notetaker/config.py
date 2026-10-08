import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    data_dir: Path
    gemini_api_key: str | None

    @property
    def db_path(self) -> Path:
        return self.data_dir / "notetaker.db"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            data_dir=Path(os.environ.get("NOTETAKER_DATA_DIR", "data")),
            gemini_api_key=os.environ.get("GEMINI_API_KEY") or None,
        )
