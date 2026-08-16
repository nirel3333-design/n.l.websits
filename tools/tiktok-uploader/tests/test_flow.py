"""End-to-end test against a fake TikTok server.

The real API is unreachable from CI, so this stands up a local HTTP server that
speaks the same protocol and drives the real client through the whole Direct
Post flow: creator_info -> init -> chunked PUT -> status polling.

Run:  .venv/bin/python -m tests.test_flow
"""

from __future__ import annotations

import hashlib
import http.server
import json
import pathlib
import sys
import tempfile
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tiktok_uploader import api  # noqa: E402
from tiktok_uploader.api import TikTokClient, plan_chunks, choose_privacy  # noqa: E402
from tiktok_uploader.config import Config  # noqa: E402
from tiktok_uploader.poster import post_video  # noqa: E402
from tiktok_uploader.scanner import parse_publish_at, title_from_filename  # noqa: E402
from tiktok_uploader.store import Store  # noqa: E402

RECEIVED: dict[str, bytearray] = {}
POLLS = {"n": 0}
FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok   {label}")
    else:
        FAILURES.append(f"{label} {detail}".strip())
        print(f"  FAIL {label} {detail}")


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence
        pass

    def _json(self, payload, code=200):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        body = json.loads(raw or b"{}")
        ok = {"code": "ok"}

        if self.path.endswith("/creator_info/query/"):
            return self._json({"data": {
                "creator_username": "nlstudio",
                "privacy_level_options": ["PUBLIC_TO_EVERYONE", "SELF_ONLY"],
                "comment_disabled": False,
                "duet_disabled": False,
                "stitch_disabled": True,
                "max_video_post_duration_sec": 600,
            }, "error": ok})

        if self.path.endswith("/video/init/"):
            src = body["source_info"]
            RECEIVED["buf"] = bytearray(src["video_size"])
            RECEIVED["seen"] = bytearray(src["video_size"])
            RECEIVED["post_info"] = body["post_info"]
            return self._json({"data": {
                "publish_id": "pub_123",
                "upload_url": f"http://127.0.0.1:{self.server.server_address[1]}/upload",
            }, "error": ok})

        if self.path.endswith("/status/fetch/"):
            POLLS["n"] += 1
            # first poll still processing, then complete
            state = "PROCESSING_UPLOAD" if POLLS["n"] < 2 else "PUBLISH_COMPLETE"
            return self._json({"data": {
                "status": state,
                "publicaly_available_post_id": ["7300000000000000000"],
            }, "error": ok})

        return self._json({"error": {"code": "not_found", "message": self.path}}, 404)

    def do_PUT(self):
        length = int(self.headers.get("Content-Length", 0))
        blob = self.rfile.read(length)
        rng = self.headers.get("Content-Range", "")
        # bytes start-end/total
        span, _, total = rng.replace("bytes ", "").partition("/")
        start, _, end = span.partition("-")
        start, end, total = int(start), int(end), int(total)

        if end - start + 1 != len(blob):
            return self._json({"error": "content-range mismatch"}, 400)
        RECEIVED["buf"][start:end + 1] = blob
        for i in range(start, end + 1):
            RECEIVED["seen"][i] = 1

        done = all(RECEIVED["seen"])
        self.send_response(201 if done else 206)
        self.send_header("Content-Length", "0")
        self.end_headers()


class FakeAuth:
    def access_token(self):
        return "fake-token"


def main() -> int:
    print("\n[1] chunk planning")
    check("small file -> single chunk", plan_chunks(3_000_000) == (3_000_000, 1))
    check("64MB -> single chunk", plan_chunks(64 * 1024 * 1024) == (64 * 1024 * 1024, 1))
    cs, n = plan_chunks(100 * 1024 * 1024)
    check("100MB -> multi chunk", n > 1 and api.MIN_CHUNK <= cs <= api.MAX_CHUNK,
          f"got chunk={cs} count={n}")
    check("empty file rejected", _raises(lambda: plan_chunks(0)))

    print("\n[2] privacy fallback")
    check("keeps allowed level",
          choose_privacy("PUBLIC_TO_EVERYONE", ["PUBLIC_TO_EVERYONE", "SELF_ONLY"])
          == "PUBLIC_TO_EVERYONE")
    check("falls back when not allowed",
          choose_privacy("PUBLIC_TO_EVERYONE", ["SELF_ONLY"]) == "SELF_ONLY")

    print("\n[3] sidecar helpers")
    check("title from filename",
          title_from_filename(pathlib.Path("/x/my_great-clip.mp4")) == "my great clip")
    check("publish_at parsed", parse_publish_at("2030-01-01T10:00:00+02:00") is not None)
    check("bad publish_at ignored", parse_publish_at("not a date") is None)

    print("\n[4] full Direct Post flow against fake server")
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    api.CREATOR_INFO_URL = f"{base}/v2/post/publish/creator_info/query/"
    api.DIRECT_POST_INIT_URL = f"{base}/v2/post/publish/video/init/"
    api.STATUS_URL = f"{base}/v2/post/publish/status/fetch/"

    tmp = pathlib.Path(tempfile.mkdtemp())
    # 9 MB of non-repeating bytes so a chunking bug corrupts the hash
    payload = hashlib.sha256(b"seed").digest() * (9 * 1024 * 1024 // 32)
    video = tmp / "clip.mp4"
    video.write_bytes(payload)

    cfg = Config(videos_dir=tmp, state_dir=tmp)
    store = Store(tmp / "q.db")
    store.add_video(path=str(video), sha256="abc", size=len(payload),
                    title="שלום עולם #test", privacy_level=None,
                    options={}, scheduled_for=None)
    item = store.list_videos()[0]

    client = TikTokClient(FakeAuth())
    result = post_video(item, cfg=cfg, client=client, store=store)

    check("reported publish complete", result.get("status") == "PUBLISH_COMPLETE")
    check("polled until done", POLLS["n"] >= 2, f"polls={POLLS['n']}")
    check("bytes arrived intact", bytes(RECEIVED["buf"]) == payload)
    check("caption forwarded",
          RECEIVED["post_info"]["title"] == "שלום עולם #test")
    check("stitch lock from creator_info respected",
          RECEIVED["post_info"]["disable_stitch"] is True)

    row = store.get_video(item["id"])
    check("row marked posted", row["status"] == "posted", f"got {row['status']}")
    check("post id stored", row["post_id"] == "7300000000000000000")
    check("posted_at set", bool(row["posted_at"]))

    print("\n[5] dedupe")
    added = store.add_video(path=str(video), sha256="abc", size=1, title="x",
                            privacy_level=None, options={}, scheduled_for=None)
    check("same hash not re-queued", added is False)

    print("\n[6] dashboard boots")
    from tiktok_uploader.dashboard import create_app
    app, sched = create_app(Config(videos_dir=tmp, state_dir=tmp))
    c = app.test_client()
    check("GET / renders", c.get("/").status_code == 200)
    state = c.get("/api/state").get_json()
    check("state json shape", "counts" in state and "videos" in state)
    check("reports not connected", state["auth"]["connected"] is False)
    sched.stop()

    server.shutdown()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED:")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("all checks passed")
    return 0


def _raises(fn) -> bool:
    try:
        fn()
        return False
    except Exception:
        return True


if __name__ == "__main__":
    raise SystemExit(main())
