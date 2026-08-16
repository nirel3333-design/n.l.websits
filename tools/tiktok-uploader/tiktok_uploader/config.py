"""Configuration loading.

Values come from environment variables, optionally seeded from a .env file
sitting next to the project root. Kept dependency-free on purpose so the tool
only needs flask + requests.
"""

from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass, field

ROOT = pathlib.Path(__file__).resolve().parent.parent


def load_dotenv(path: pathlib.Path | None = None) -> None:
    """Seed os.environ from a .env file. Existing env vars always win."""
    path = path or ROOT / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip())
    except (TypeError, ValueError):
        return default


@dataclass
class Config:
    client_key: str = ""
    client_secret: str = ""
    redirect_uri: str = "http://127.0.0.1:8765/auth/callback"

    host: str = "127.0.0.1"
    port: int = 8765

    videos_dir: pathlib.Path = ROOT / "videos"
    state_dir: pathlib.Path = ROOT / "state"

    # Posting behaviour
    privacy_level: str = "PUBLIC_TO_EVERYONE"
    disable_comment: bool = False
    disable_duet: bool = False
    disable_stitch: bool = False
    cover_timestamp_ms: int = 1000

    # Scheduling
    schedule_times: list[str] = field(default_factory=lambda: ["10:00", "18:00"])
    min_gap_minutes: int = 30
    max_attempts: int = 3
    autopost: bool = True

    @property
    def token_path(self) -> pathlib.Path:
        return self.state_dir / "tokens.json"

    @property
    def db_path(self) -> pathlib.Path:
        return self.state_dir / "queue.db"

    @property
    def configured(self) -> bool:
        return bool(self.client_key and self.client_secret)


def load_config() -> Config:
    load_dotenv()
    times = [
        t.strip()
        for t in os.environ.get("SCHEDULE_TIMES", "10:00,18:00").split(",")
        if t.strip()
    ]
    cfg = Config(
        client_key=os.environ.get("TIKTOK_CLIENT_KEY", "").strip(),
        client_secret=os.environ.get("TIKTOK_CLIENT_SECRET", "").strip(),
        redirect_uri=os.environ.get(
            "TIKTOK_REDIRECT_URI", "http://127.0.0.1:8765/auth/callback"
        ).strip(),
        host=os.environ.get("HOST", "127.0.0.1").strip(),
        port=_int("PORT", 8765),
        privacy_level=os.environ.get("PRIVACY_LEVEL", "PUBLIC_TO_EVERYONE").strip(),
        disable_comment=_bool("DISABLE_COMMENT", False),
        disable_duet=_bool("DISABLE_DUET", False),
        disable_stitch=_bool("DISABLE_STITCH", False),
        cover_timestamp_ms=_int("COVER_TIMESTAMP_MS", 1000),
        schedule_times=times,
        min_gap_minutes=_int("MIN_GAP_MINUTES", 30),
        max_attempts=_int("MAX_ATTEMPTS", 3),
        autopost=_bool("AUTOPOST", True),
    )
    if os.environ.get("VIDEOS_DIR"):
        cfg.videos_dir = pathlib.Path(os.environ["VIDEOS_DIR"]).expanduser().resolve()
    if os.environ.get("STATE_DIR"):
        cfg.state_dir = pathlib.Path(os.environ["STATE_DIR"]).expanduser().resolve()

    cfg.videos_dir.mkdir(parents=True, exist_ok=True)
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    return cfg
