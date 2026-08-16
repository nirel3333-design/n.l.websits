"""Turn a folder of video files into queue entries.

Each video may sit next to an optional JSON sidecar with the same stem:

    videos/promo.mp4
    videos/promo.json   ->  {"title": "...", "privacy_level": "...",
                             "publish_at": "2026-08-20T18:00:00+03:00"}

Videos are identified by content hash, so re-scanning, renaming or moving a
file never queues the same clip twice.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import pathlib
from typing import Any

VIDEO_SUFFIXES = {".mp4", ".mov", ".webm", ".avi", ".mkv"}
HASH_CHUNK = 1024 * 1024


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(HASH_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def title_from_filename(path: pathlib.Path) -> str:
    """Fallback caption: filename without extension, underscores as spaces."""
    return path.stem.replace("_", " ").replace("-", " ").strip()


def parse_publish_at(value: Any) -> float | None:
    if not value or not isinstance(value, str):
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()  # interpret naive times as local
    return parsed.timestamp()


def read_sidecar(video: pathlib.Path) -> dict[str, Any]:
    sidecar = video.with_suffix(".json")
    if not sidecar.exists():
        return {}
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def is_stable(path: pathlib.Path, settle_seconds: float = 2.0) -> bool:
    """Guard against picking up a file that is still being copied in."""
    import time

    try:
        first = path.stat().st_size
        time.sleep(settle_seconds)
        return first == path.stat().st_size and first > 0
    except OSError:
        return False


def scan(videos_dir: pathlib.Path, store, *, settle: bool = True) -> dict[str, int]:
    """Add any new videos in the folder to the queue. Returns a small summary."""
    added = skipped = 0
    for path in sorted(videos_dir.iterdir()):
        if not path.is_file() or path.suffix.lower() not in VIDEO_SUFFIXES:
            continue
        if settle and not is_stable(path):
            continue

        meta = read_sidecar(path)
        options = {
            k: meta[k]
            for k in ("disable_comment", "disable_duet", "disable_stitch",
                      "cover_timestamp_ms")
            if k in meta
        }
        created = store.add_video(
            path=str(path),
            sha256=sha256_file(path),
            size=path.stat().st_size,
            title=str(meta.get("title") or title_from_filename(path)),
            privacy_level=meta.get("privacy_level"),
            options=options,
            scheduled_for=parse_publish_at(meta.get("publish_at")),
        )
        if created:
            added += 1
            store.log("info", f"נוסף לתור: {path.name}")
        else:
            skipped += 1
    return {"added": added, "skipped": skipped}
