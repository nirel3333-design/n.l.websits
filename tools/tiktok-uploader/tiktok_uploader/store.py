"""SQLite-backed queue state and on-disk token storage."""

from __future__ import annotations

import json
import os
import pathlib
import sqlite3
import threading
import time
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    path          TEXT    NOT NULL,
    sha256        TEXT    NOT NULL UNIQUE,
    size          INTEGER NOT NULL,
    title         TEXT    NOT NULL DEFAULT '',
    privacy_level TEXT,
    options_json  TEXT    NOT NULL DEFAULT '{}',
    status        TEXT    NOT NULL DEFAULT 'pending',
    publish_id    TEXT,
    post_id       TEXT,
    error         TEXT,
    attempts      INTEGER NOT NULL DEFAULT 0,
    scheduled_for REAL,
    created_at    REAL    NOT NULL,
    posted_at     REAL
);
CREATE INDEX IF NOT EXISTS idx_videos_status ON videos(status);

CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL NOT NULL,
    level   TEXT NOT NULL,
    message TEXT NOT NULL
);
"""

# Terminal + in-flight states. 'pending' is the only state the scheduler picks up.
STATUSES = ("pending", "uploading", "processing", "posted", "failed", "skipped")


class Store:
    def __init__(self, db_path: pathlib.Path):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        # Anything left mid-flight by a crash should be retried, not stuck.
        self._conn.execute(
            "UPDATE videos SET status='pending' WHERE status IN ('uploading','processing')"
        )
        self._conn.commit()

    # ---------- videos ----------

    def add_video(self, *, path: str, sha256: str, size: int, title: str,
                  privacy_level: str | None, options: dict[str, Any],
                  scheduled_for: float | None) -> bool:
        """Insert a video. Returns False if this content hash is already known."""
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO videos (path, sha256, size, title, privacy_level,"
                    " options_json, scheduled_for, created_at)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (path, sha256, size, title, privacy_level,
                     json.dumps(options, ensure_ascii=False), scheduled_for, time.time()),
                )
                self._conn.commit()
                return True
            except sqlite3.IntegrityError:
                # Same bytes already queued or posted — refresh the path in case
                # the file was renamed or moved, but do not re-queue it.
                self._conn.execute(
                    "UPDATE videos SET path=? WHERE sha256=?", (path, sha256)
                )
                self._conn.commit()
                return False

    def list_videos(self, status: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM videos"
        args: tuple = ()
        if status:
            sql += " WHERE status=?"
            args = (status,)
        sql += " ORDER BY COALESCE(scheduled_for, created_at) ASC, id ASC"
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, args).fetchall()]

    def get_video(self, video_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM videos WHERE id=?", (video_id,)
            ).fetchone()
            return dict(row) if row else None

    def next_pending(self, now: float) -> dict[str, Any] | None:
        """Oldest pending video whose scheduled time (if any) has arrived."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM videos WHERE status='pending'"
                " AND (scheduled_for IS NULL OR scheduled_for <= ?)"
                " ORDER BY COALESCE(scheduled_for, created_at) ASC, id ASC LIMIT 1",
                (now,),
            ).fetchone()
            return dict(row) if row else None

    def update_video(self, video_id: int, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._lock:
            self._conn.execute(
                f"UPDATE videos SET {cols} WHERE id=?", (*fields.values(), video_id)
            )
            self._conn.commit()

    def delete_video(self, video_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM videos WHERE id=?", (video_id,))
            self._conn.commit()

    def counts(self) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT status, COUNT(*) c FROM videos GROUP BY status"
            ).fetchall()
        out = {s: 0 for s in STATUSES}
        for r in rows:
            out[r["status"]] = r["c"]
        return out

    def last_posted_at(self) -> float | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT MAX(posted_at) m FROM videos WHERE posted_at IS NOT NULL"
            ).fetchone()
        return row["m"] if row and row["m"] else None

    # ---------- events ----------

    def log(self, level: str, message: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO events (ts, level, message) VALUES (?,?,?)",
                (time.time(), level, message),
            )
            self._conn.execute(
                "DELETE FROM events WHERE id NOT IN"
                " (SELECT id FROM events ORDER BY id DESC LIMIT 500)"
            )
            self._conn.commit()

    def recent_events(self, limit: int = 60) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]


class TokenStore:
    """OAuth tokens on disk, written with owner-only permissions."""

    def __init__(self, path: pathlib.Path):
        self.path = path
        self._lock = threading.Lock()

    def load(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def save(self, data: dict[str, Any]) -> None:
        with self._lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            os.chmod(tmp, 0o600)
            tmp.replace(self.path)

    def clear(self) -> None:
        with self._lock:
            self.path.unlink(missing_ok=True)
