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


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "").strip())
    except (TypeError, ValueError):
        return default


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
    projects_dir: pathlib.Path = ROOT / "projects"
    music_dir: pathlib.Path = ROOT / "assets" / "music"

    # Branding burned into every video
    business_name: str = "N.L Studio"
    phone: str = ""
    logo_path: str = ""
    font_path: str = ""

    # Video shape
    seconds_per_slide: float = 2.8
    max_video_seconds: float = 45.0
    outro_seconds: float = 2.4

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
        business_name=os.environ.get("BUSINESS_NAME", "N.L Studio").strip(),
        phone=os.environ.get("PHONE", "").strip(),
        logo_path=os.environ.get("LOGO_PATH", "").strip(),
        font_path=os.environ.get("FONT_PATH", "").strip(),
        seconds_per_slide=_float("SECONDS_PER_SLIDE", 2.8),
        max_video_seconds=_float("MAX_VIDEO_SECONDS", 45.0),
        outro_seconds=_float("OUTRO_SECONDS", 2.4),
    )
    for var, attr in (("VIDEOS_DIR", "videos_dir"), ("STATE_DIR", "state_dir"),
                      ("PROJECTS_DIR", "projects_dir"), ("MUSIC_DIR", "music_dir")):
        if os.environ.get(var):
            setattr(cfg, attr,
                    pathlib.Path(os.environ[var]).expanduser().resolve())

    for path in (cfg.videos_dir, cfg.state_dir, cfg.projects_dir, cfg.music_dir):
        path.mkdir(parents=True, exist_ok=True)
    return cfg
