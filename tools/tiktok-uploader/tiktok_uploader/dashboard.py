"""Local Flask dashboard.

Binds to 127.0.0.1 by default. It holds a TikTok access token, so do not expose
it to a public interface without putting real authentication in front of it.
"""

from __future__ import annotations

import datetime as dt
import pathlib
import threading
from typing import Any

from flask import Flask, jsonify, redirect, render_template, request

from . import projects as projects_mod
from .api import TikTokClient
from .auth import Auth, AuthError
from .config import Config
from .poster import Scheduler
from .scanner import scan
from .store import Store, TokenStore


def humanize(ts: float | None) -> str:
    if not ts:
        return ""
    return dt.datetime.fromtimestamp(ts).astimezone().strftime("%d/%m %H:%M")


def create_app(cfg: Config) -> tuple[Flask, Scheduler]:
    app = Flask(__name__)
    store = Store(cfg.db_path)
    tokens = TokenStore(cfg.token_path)
    auth = Auth(cfg, tokens)
    client = TikTokClient(auth)
    scheduler = Scheduler(cfg, store, client)
    scan_lock = threading.Lock()
    build_lock = threading.Lock()

    def state_payload() -> dict[str, Any]:
        videos = []
        for v in store.list_videos():
            videos.append({
                "id": v["id"],
                "name": pathlib.Path(v["path"]).name,
                "title": v["title"],
                "status": v["status"],
                "error": v["error"],
                "attempts": v["attempts"],
                "size_mb": round(v["size"] / 1048576, 1),
                "post_id": v["post_id"],
                "scheduled_for": humanize(v["scheduled_for"]),
                "posted_at": humanize(v["posted_at"]),
            })
        try:
            auth_state = auth.status()
        except Exception as exc:  # never let the dashboard 500 on auth trouble
            auth_state = {"connected": False, "error": str(exc)}
        try:
            project_list = projects_mod.list_projects(cfg)
        except Exception:
            project_list = []
        return {
            "auth": auth_state,
            "configured": cfg.configured,
            "counts": store.counts(),
            "videos": videos,
            "projects": project_list,
            "events": [
                {"ts": humanize(e["ts"]), "level": e["level"], "message": e["message"]}
                for e in store.recent_events(40)
            ],
            "settings": {
                "videos_dir": str(cfg.videos_dir),
                "schedule_times": cfg.schedule_times,
                "min_gap_minutes": cfg.min_gap_minutes,
                "privacy_level": cfg.privacy_level,
                "autopost": cfg.autopost,
                "max_attempts": cfg.max_attempts,
            },
            "last_error": scheduler.last_error,
        }

    # ---------- pages ----------

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/state")
    def api_state():
        return jsonify(state_payload())

    # ---------- auth ----------

    @app.get("/auth/login")
    def auth_login():
        try:
            return redirect(auth.authorize_url())
        except AuthError as exc:
            return render_template("index.html", flash=str(exc)), 400

    @app.get("/auth/callback")
    def auth_callback():
        error = request.args.get("error")
        if error:
            store.log("error", f"ההתחברות נדחתה: {error}")
            return redirect("/")
        code = request.args.get("code", "")
        state = request.args.get("state", "")
        try:
            auth.exchange_code(code, state)
            info = auth.status()
            store.log("info", "החשבון חובר בהצלחה")
            if not info.get("can_direct_post"):
                store.log(
                    "warn",
                    "ההרשאה video.publish לא אושרה — פרסום ישיר לא יעבוד עדיין",
                )
        except AuthError as exc:
            store.log("error", f"ההתחברות נכשלה: {exc}")
        return redirect("/")

    @app.post("/api/logout")
    def api_logout():
        auth.disconnect()
        store.log("info", "החשבון נותק")
        return jsonify({"ok": True})

    # ---------- queue actions ----------

    @app.post("/api/scan")
    def api_scan():
        if not scan_lock.acquire(blocking=False):
            return jsonify({"ok": False, "error": "סריקה כבר רצה"}), 409
        try:
            result = scan(cfg.videos_dir, store)
        finally:
            scan_lock.release()
        return jsonify({"ok": True, **result})

    @app.post("/api/build")
    def api_build():
        """Render every unbuilt project folder into a captioned video."""
        if not build_lock.acquire(blocking=False):
            return jsonify({"ok": False, "error": "בנייה כבר רצה"}), 409

        def worker():
            try:
                result = projects_mod.build_all(
                    cfg, log=lambda m: store.log("info", m)
                )
                store.log(
                    "info",
                    f"בנייה הסתיימה: {result['built']} נבנו, "
                    f"{result['skipped']} דולגו, {result['failed']} נכשלו",
                )
                if result["built"]:
                    scan(cfg.videos_dir, store, settle=False)
            except Exception as exc:
                store.log("error", f"הבנייה נכשלה: {exc}")
            finally:
                build_lock.release()

        threading.Thread(target=worker, daemon=True).start()
        return jsonify({"ok": True, "started": True})

    @app.post("/api/post/<int:video_id>")
    def api_post(video_id: int):
        item = store.get_video(video_id)
        if not item:
            return jsonify({"ok": False, "error": "not found"}), 404
        if item["status"] in ("uploading", "processing"):
            return jsonify({"ok": False, "error": "כבר בתהליך"}), 409

        def worker():
            scheduler.post_one(item)

        threading.Thread(target=worker, daemon=True).start()
        return jsonify({"ok": True})

    @app.post("/api/retry/<int:video_id>")
    def api_retry(video_id: int):
        store.update_video(video_id, status="pending", error=None, attempts=0)
        scheduler.poke()
        return jsonify({"ok": True})

    @app.post("/api/skip/<int:video_id>")
    def api_skip(video_id: int):
        store.update_video(video_id, status="skipped")
        return jsonify({"ok": True})

    @app.post("/api/delete/<int:video_id>")
    def api_delete(video_id: int):
        store.delete_video(video_id)
        return jsonify({"ok": True})

    @app.post("/api/autopost")
    def api_autopost():
        cfg.autopost = bool((request.get_json(silent=True) or {}).get("enabled"))
        store.log("info", f"פרסום אוטומטי {'הופעל' if cfg.autopost else 'כובה'}")
        scheduler.poke()
        return jsonify({"ok": True, "autopost": cfg.autopost})

    app.config["STORE"] = store
    return app, scheduler
