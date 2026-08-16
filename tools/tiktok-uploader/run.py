#!/usr/bin/env python3
"""Entry point: starts the scheduler and serves the local dashboard."""

import sys
import webbrowser

from tiktok_uploader.config import load_config
from tiktok_uploader.dashboard import create_app


def main() -> int:
    cfg = load_config()
    app, scheduler = create_app(cfg)
    scheduler.start()

    url = f"http://{cfg.host}:{cfg.port}/"
    print(f"\n  לוח הבקרה:  {url}")
    print(f"  תיקיית סרטונים:  {cfg.videos_dir}")
    if not cfg.configured:
        print("\n  ⚠  חסרים TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET בקובץ .env\n")
    else:
        print(f"  שעות פרסום:  {', '.join(cfg.schedule_times)}\n")

    if "--no-browser" not in sys.argv:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    try:
        # threaded=True so a long upload never blocks the dashboard.
        app.run(host=cfg.host, port=cfg.port, threaded=True, use_reloader=False)
    except KeyboardInterrupt:
        pass
    finally:
        scheduler.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
