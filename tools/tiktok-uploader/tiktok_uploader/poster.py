"""Posting one video, and the background scheduler that decides when to."""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import threading
import time
from typing import Any

from .api import TikTokClient, TikTokError, choose_privacy
from .auth import AuthError
from .config import Config
from .store import Store


def post_video(video: dict[str, Any], *, cfg: Config, client: TikTokClient,
               store: Store) -> dict[str, Any]:
    """Run the full Direct Post flow for one queued video."""
    vid = video["id"]
    path = pathlib.Path(video["path"])
    if not path.exists():
        raise FileNotFoundError(f"file has gone missing: {path}")

    size = path.stat().st_size
    options = json.loads(video.get("options_json") or "{}")

    # 1. What is this creator allowed to do right now?
    info = client.creator_info()
    allowed = info.get("privacy_level_options", [])
    requested = video.get("privacy_level") or cfg.privacy_level
    privacy = choose_privacy(requested, allowed)
    if privacy != requested:
        store.log(
            "warn",
            f"רמת הפרטיות {requested} אינה זמינה לחשבון — נעשה שימוש ב-{privacy}",
        )

    max_seconds = info.get("max_video_post_duration_sec")
    if max_seconds:
        store.log("info", f"מגבלת אורך סרטון לחשבון: {max_seconds} שניות")

    # TikTok also reports per-interaction locks; respect them.
    disable_comment = bool(options.get("disable_comment", cfg.disable_comment)) or \
        bool(info.get("comment_disabled"))
    disable_duet = bool(options.get("disable_duet", cfg.disable_duet)) or \
        bool(info.get("duet_disabled"))
    disable_stitch = bool(options.get("disable_stitch", cfg.disable_stitch)) or \
        bool(info.get("stitch_disabled"))

    # 2. Reserve the upload
    store.update_video(vid, status="uploading", error=None)
    init = client.init_direct_post(
        size=size,
        title=video.get("title") or "",
        privacy_level=privacy,
        disable_comment=disable_comment,
        disable_duet=disable_duet,
        disable_stitch=disable_stitch,
        cover_timestamp_ms=int(
            options.get("cover_timestamp_ms", cfg.cover_timestamp_ms)
        ),
    )
    publish_id = init.get("publish_id", "")
    upload_url = init.get("upload_url", "")
    if not publish_id or not upload_url:
        raise TikTokError("init did not return publish_id/upload_url")
    store.update_video(vid, publish_id=publish_id)

    # 3. Send the bytes
    def progress(done: int, total: int) -> None:
        if total > 1:
            store.log("info", f"{path.name}: הועלה נתח {done}/{total}")

    client.upload_file(
        upload_url, path, size,
        init["_chunk_size"], init["_total_chunks"],
        on_progress=progress,
    )

    # 4. Wait for TikTok to finish processing
    store.update_video(vid, status="processing")
    result = client.wait_for_publish(publish_id)

    post_id = ""
    ids = result.get("publicaly_available_post_id") or result.get(
        "publicly_available_post_id"
    )
    if isinstance(ids, list) and ids:
        post_id = str(ids[0])

    store.update_video(
        vid, status="posted", post_id=post_id, posted_at=time.time(), error=None
    )
    store.log("info", f"פורסם בהצלחה: {path.name}"
                      + (f" (post id {post_id})" if post_id else ""))
    return result


class Scheduler(threading.Thread):
    """Posts one pending video whenever a scheduled slot comes due."""

    def __init__(self, cfg: Config, store: Store, client: TikTokClient):
        super().__init__(daemon=True, name="tiktok-scheduler")
        self.cfg = cfg
        self.store = store
        self.client = client
        self._stop = threading.Event()
        self._wake = threading.Event()
        self.last_error: str | None = None

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def poke(self) -> None:
        """Ask the loop to look at the queue right now."""
        self._wake.set()

    # ---------- slot logic ----------

    def _slot_times(self, day: dt.date) -> list[dt.datetime]:
        out = []
        for raw in self.cfg.schedule_times:
            try:
                hh, mm = (int(x) for x in raw.split(":", 1))
                out.append(dt.datetime.combine(day, dt.time(hh, mm)).astimezone())
            except (ValueError, TypeError):
                continue
        return sorted(out)

    def due_now(self, now: float) -> bool:
        """True if we are inside a scheduling slot and the gap has elapsed."""
        last = self.store.last_posted_at()
        if last and (now - last) < self.cfg.min_gap_minutes * 60:
            return False

        moment = dt.datetime.fromtimestamp(now).astimezone()
        window = self.cfg.min_gap_minutes * 60
        for slot in self._slot_times(moment.date()):
            delta = now - slot.timestamp()
            # Inside the slot's window, and we have not already posted in it.
            if 0 <= delta < window:
                if last and last >= slot.timestamp():
                    return False
                return True
        return False

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as exc:  # keep the thread alive no matter what
                self.last_error = str(exc)
                self.store.log("error", f"שגיאה בלולאת התזמון: {exc}")
            self._wake.wait(timeout=30)
            self._wake.clear()

    def _tick(self) -> None:
        if not self.cfg.autopost:
            return
        now = time.time()
        item = self.store.next_pending(now)
        if not item:
            return

        # An explicit publish_at overrides the daily slots.
        explicit = item.get("scheduled_for")
        if not explicit and not self.due_now(now):
            return

        self.post_one(item)

    def post_one(self, item: dict[str, Any]) -> bool:
        vid = item["id"]
        name = pathlib.Path(item["path"]).name
        attempts = int(item.get("attempts", 0)) + 1
        self.store.update_video(vid, attempts=attempts)
        try:
            post_video(item, cfg=self.cfg, client=self.client, store=self.store)
            self.last_error = None
            return True
        except (TikTokError, AuthError, FileNotFoundError, OSError, ValueError) as exc:
            message = str(exc)
            self.last_error = message
            final = attempts >= self.cfg.max_attempts
            self.store.update_video(
                vid, status="failed" if final else "pending", error=message
            )
            self.store.log(
                "error",
                f"{name}: {message}"
                + ("" if final else f" — ניסיון {attempts}/{self.cfg.max_attempts}"),
            )
            return False
