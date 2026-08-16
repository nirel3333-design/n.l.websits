"""TikTok Content Posting API client.

Docs: https://developers.tiktok.com/doc/content-posting-api-get-started

Direct Post flow:
    1. creator_info/query      -> what this creator is allowed to do right now
    2. video/init              -> publish_id + upload_url
    3. PUT chunks to upload_url
    4. status/fetch            -> poll until PUBLISH_COMPLETE

IMPORTANT: until your app passes TikTok's audit, Direct Post is restricted —
unaudited clients can only publish as SELF_ONLY (private). creator_info tells
you which privacy levels are actually available, and we validate against it
instead of guessing.
"""

from __future__ import annotations

import pathlib
import time
from typing import Any, Callable, Iterable

import requests

BASE = "https://open.tiktokapis.com/v2"
CREATOR_INFO_URL = f"{BASE}/post/publish/creator_info/query/"
DIRECT_POST_INIT_URL = f"{BASE}/post/publish/video/init/"
INBOX_INIT_URL = f"{BASE}/post/publish/inbox/video/init/"
STATUS_URL = f"{BASE}/post/publish/status/fetch/"

# TikTok's chunking rules for FILE_UPLOAD.
MIN_CHUNK = 5 * 1024 * 1024        # 5 MB
MAX_CHUNK = 64 * 1024 * 1024       # 64 MB
TARGET_CHUNK = 32 * 1024 * 1024    # comfortable middle ground for big files
MAX_TITLE = 2200

TERMINAL_OK = "PUBLISH_COMPLETE"
TERMINAL_FAIL = "FAILED"


class TikTokError(RuntimeError):
    """An error reported by TikTok, carrying their error code when present."""

    def __init__(self, message: str, code: str = "", http_status: int = 0):
        super().__init__(message)
        self.code = code
        self.http_status = http_status


def plan_chunks(size: int) -> tuple[int, int]:
    """Return (chunk_size, total_chunk_count) satisfying TikTok's constraints.

    A whole file of 64 MB or less goes up as a single chunk. Larger files are
    split into equal chunks with the *final* chunk absorbing the remainder, so
    no chunk is ever below the 5 MB floor.
    """
    if size <= 0:
        raise ValueError("video file is empty")
    if size <= MAX_CHUNK:
        return size, 1
    chunk = TARGET_CHUNK
    count = size // chunk
    if count > 1000:  # API ceiling
        count = 1000
        chunk = size // count
        if chunk > MAX_CHUNK:
            raise ValueError("video is too large to upload within API limits")
    return chunk, int(count)


class TikTokClient:
    def __init__(self, auth, session: requests.Session | None = None):
        self.auth = auth
        self.session = session or requests.Session()

    # ---------- low level ----------

    def _post(self, url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        token = self.auth.access_token()
        resp = self.session.post(
            url,
            json=payload if payload is not None else {},
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=UTF-8",
            },
            timeout=60,
        )
        try:
            body = resp.json()
        except ValueError:
            raise TikTokError(
                f"Non-JSON response from {url} ({resp.status_code})",
                http_status=resp.status_code,
            )
        err = (body or {}).get("error") or {}
        code = err.get("code", "")
        # TikTok returns error.code == "ok" on success.
        if code and code != "ok":
            raise TikTokError(
                err.get("message") or code, code=code, http_status=resp.status_code
            )
        if resp.status_code >= 400:
            raise TikTokError(
                f"HTTP {resp.status_code} from {url}", http_status=resp.status_code
            )
        return body.get("data", {}) or {}

    # ---------- steps ----------

    def creator_info(self) -> dict[str, Any]:
        """Must be called before every Direct Post — TikTok requires the app to
        show the creator their current options, and it also gives us the real
        list of allowed privacy levels and the max duration."""
        return self._post(CREATOR_INFO_URL)

    def init_direct_post(self, *, size: int, title: str, privacy_level: str,
                         disable_comment: bool, disable_duet: bool,
                         disable_stitch: bool,
                         cover_timestamp_ms: int) -> dict[str, Any]:
        chunk_size, total = plan_chunks(size)
        payload = {
            "post_info": {
                "title": title[:MAX_TITLE],
                "privacy_level": privacy_level,
                "disable_comment": disable_comment,
                "disable_duet": disable_duet,
                "disable_stitch": disable_stitch,
                "video_cover_timestamp_ms": cover_timestamp_ms,
            },
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": size,
                "chunk_size": chunk_size,
                "total_chunk_count": total,
            },
        }
        data = self._post(DIRECT_POST_INIT_URL, payload)
        data["_chunk_size"] = chunk_size
        data["_total_chunks"] = total
        return data

    def init_inbox_upload(self, *, size: int) -> dict[str, Any]:
        """Send to the creator's TikTok inbox/drafts instead of publishing."""
        chunk_size, total = plan_chunks(size)
        payload = {
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": size,
                "chunk_size": chunk_size,
                "total_chunk_count": total,
            }
        }
        data = self._post(INBOX_INIT_URL, payload)
        data["_chunk_size"] = chunk_size
        data["_total_chunks"] = total
        return data

    def upload_file(self, upload_url: str, path: pathlib.Path, size: int,
                    chunk_size: int, total_chunks: int,
                    on_progress: Callable[[int, int], None] | None = None) -> None:
        """PUT the file to TikTok's storage, one chunk at a time."""
        with path.open("rb") as fh:
            for index in range(total_chunks):
                start = index * chunk_size
                # The last chunk swallows any remainder.
                end = size - 1 if index == total_chunks - 1 else start + chunk_size - 1
                length = end - start + 1
                fh.seek(start)
                blob = fh.read(length)
                if not blob:
                    raise TikTokError("Unexpected end of file while chunking")

                resp = self.session.put(
                    upload_url,
                    data=blob,
                    headers={
                        "Content-Type": "video/mp4",
                        "Content-Length": str(len(blob)),
                        "Content-Range": f"bytes {start}-{end}/{size}",
                    },
                    timeout=600,
                )
                # 201 = final chunk accepted, 206 = partial accepted.
                if resp.status_code not in (200, 201, 206):
                    raise TikTokError(
                        f"Chunk {index + 1}/{total_chunks} rejected "
                        f"(HTTP {resp.status_code}): {resp.text[:200]}",
                        http_status=resp.status_code,
                    )
                if on_progress:
                    on_progress(index + 1, total_chunks)

    def status(self, publish_id: str) -> dict[str, Any]:
        return self._post(STATUS_URL, {"publish_id": publish_id})

    def wait_for_publish(self, publish_id: str, *, timeout: int = 600,
                         interval: int = 5,
                         on_poll: Callable[[str], None] | None = None) -> dict[str, Any]:
        deadline = time.time() + timeout
        last: dict[str, Any] = {}
        while time.time() < deadline:
            last = self.status(publish_id)
            state = last.get("status", "")
            if on_poll:
                on_poll(state)
            if state == TERMINAL_OK:
                return last
            if state == TERMINAL_FAIL:
                reason = last.get("fail_reason") or last.get("error_code") or "unknown"
                raise TikTokError(f"TikTok rejected the post: {reason}")
            time.sleep(interval)
        raise TikTokError(
            f"Timed out after {timeout}s waiting for publish (last status: "
            f"{last.get('status', 'unknown')})"
        )


def choose_privacy(requested: str, allowed: Iterable[str]) -> str:
    """Pick a privacy level TikTok will actually accept.

    Unaudited apps typically only get SELF_ONLY back from creator_info; silently
    posting publicly is impossible there, so we fall back rather than fail.
    """
    allowed = list(allowed or [])
    if not allowed:
        return requested
    if requested in allowed:
        return requested
    for fallback in ("SELF_ONLY", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR",
                     "PUBLIC_TO_EVERYONE"):
        if fallback in allowed:
            return fallback
    return allowed[0]
